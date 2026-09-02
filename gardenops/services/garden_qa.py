from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gardenops.db import DbConn
from gardenops.rate_limit import (
    acquire_concurrency_slot,
    provider_limit_profile,
    reserve_daily_provider_budget,
)
from gardenops.router_helpers import effective_role, is_local_admin_fallback
from gardenops.security import AuthContext
from gardenops.services.ai_provider import chat_with_ai, require_ai_provider_configured


def build_garden_context(db: DbConn, context: AuthContext) -> str:
    garden_id = context.garden_id
    role = effective_role(context)
    elevated = role in {"admin", "editor"} or is_local_admin_fallback(context)
    if garden_id is None:
        plots: list[dict[str, Any]] = []
        plant_rows: list[dict[str, Any]] = []
    elif elevated:
        plots = db.execute(
            """
            SELECT p.plot_id, p.zone_code, p.zone_name, p.grid_row, p.grid_col, p.sub_zone
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s
            ORDER BY p.zone_code, p.plot_number
            """,
            (garden_id,),
        ).fetchall()
        plant_rows = db.execute(
            """
            SELECT p.plt_id, p.name, p.latin, p.category, p.bloom_month, p.color,
                   p.hardiness, p.height_cm, p.light, p.year_planted,
                   pp.plot_id AS asgn_plot_id, pp.quantity AS asgn_quantity
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            LEFT JOIN (
                SELECT pp.plt_id, pp.plot_id, pp.quantity, ppo.garden_id
                FROM plot_plants pp
                JOIN plot_ownership ppo ON ppo.plot_id = pp.plot_id
            ) pp ON pp.plt_id = p.plt_id AND pp.garden_id = po.garden_id
            WHERE po.garden_id = %s
            ORDER BY p.name, pp.plot_id
            """,
            (garden_id,),
        ).fetchall()
    elif context.user_id is not None:
        plots = db.execute(
            """
            SELECT p.plot_id, p.zone_code, p.zone_name, p.grid_row, p.grid_col, p.sub_zone
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s AND po.owner_user_id = %s
            ORDER BY p.zone_code, p.plot_number
            """,
            (garden_id, context.user_id),
        ).fetchall()
        plant_rows = db.execute(
            """
            SELECT p.plt_id, p.name, p.latin, p.category, p.bloom_month, p.color,
                   p.hardiness, p.height_cm, p.light, p.year_planted,
                   pp.plot_id AS asgn_plot_id, pp.quantity AS asgn_quantity
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            LEFT JOIN (
                SELECT pp.plt_id, pp.plot_id, pp.quantity,
                       ppo.garden_id, ppo.owner_user_id
                FROM plot_plants pp
                JOIN plot_ownership ppo ON ppo.plot_id = pp.plot_id
            ) pp ON pp.plt_id = p.plt_id
                AND pp.garden_id = po.garden_id
                AND pp.owner_user_id = po.owner_user_id
            WHERE po.garden_id = %s AND po.owner_user_id = %s
            ORDER BY p.name, pp.plot_id
            """,
            (garden_id, context.user_id),
        ).fetchall()
    else:
        plots = []
        plant_rows = []

    plants_by_id: dict[str, dict[str, Any]] = {}
    assignments: list[dict[str, Any]] = []
    for row in plant_rows:
        plant_id = str(row["plt_id"])
        plants_by_id.setdefault(plant_id, row)
        if row.get("asgn_plot_id") is not None:
            assignments.append(
                {
                    "plot_id": str(row["asgn_plot_id"]),
                    "name": str(row["name"] or ""),
                    "quantity": int(row["asgn_quantity"] or 1),
                }
            )

    zones: dict[str, dict[str, Any]] = {}
    for row in plots:
        code = str(row["zone_code"])
        zone = zones.setdefault(code, {"name": str(row["zone_name"]), "plots": []})
        zone["plots"].append(str(row["plot_id"]))

    lines = [
        f"Garden summary: {len(plots)} plots, {len(plants_by_id)} plants, "
        f"{len(assignments)} plantings.",
        "",
        "Zones:",
    ]
    for code, zone in sorted(zones.items()):
        lines.append(f"  {code} ({zone['name']}): {len(zone['plots'])} plots")
    lines.extend(["", "Plants:"])
    for plant in plants_by_id.values():
        parts = [str(plant["name"])]
        if plant.get("latin"):
            parts.append(f"({plant['latin']})")
        details: list[str] = []
        for field_name, label in (
            ("category", ""),
            ("bloom_month", "bloom: "),
            ("color", "color: "),
            ("hardiness", "hardiness: "),
            ("light", "light: "),
            ("year_planted", "planted: "),
        ):
            if plant.get(field_name):
                details.append(f"{label}{plant[field_name]}")
        if plant.get("height_cm"):
            details.append(f"{plant['height_cm']}cm")
        if details:
            parts.append("- " + ", ".join(details))
        lines.append(f"  {' '.join(parts)}")
    assignments_by_plot: dict[str, list[str]] = {}
    for assignment in assignments:
        label = str(assignment["name"])
        if int(assignment["quantity"]) > 1:
            label += f" (x{assignment['quantity']})"
        assignments_by_plot.setdefault(str(assignment["plot_id"]), []).append(label)
    lines.extend(["", "Plot assignments:"])
    for plot_id, labels in sorted(assignments_by_plot.items()):
        lines.append(f"  {plot_id}: {', '.join(labels)}")
    return "\n".join(lines)


CHAT_SYSTEM_TEMPLATE = (
    "You are a plant expert with extensive hands-on gardening experience in Norway. "
    "Use the supplied GardenOps data and Norwegian climate. Be concise and state uncertainty. "
    "Do not claim a plant or location exists unless it appears in the data.\n\n"
    "GARDEN DATA:\n{context}"
)


def answer_garden_question(
    db: DbConn,
    context: AuthContext,
    *,
    message: str,
    history: list[dict[str, str]] | None = None,
    garden_context: str | None = None,
    max_tokens: int = 1024,
    timeout_seconds: float | None = 60,
    chat_callable: Callable[..., str] = chat_with_ai,
) -> str:
    provider = require_ai_provider_configured()
    context_text = (
        garden_context if garden_context is not None else build_garden_context(db, context)
    )
    limits = provider_limit_profile("ai-garden-chat")
    messages = list(history or [])
    messages.append({"role": "user", "content": message[:2000]})
    with acquire_concurrency_slot(
        bucket="ai-garden-chat",
        limit=int(limits["concurrency_limit"]),
    ):
        reserve_daily_provider_budget(
            db,
            feature="ai-garden-chat",
            user_id=context.user_id,
            garden_id=context.garden_id,
            user_limit=int(limits["user_limit"]),
            garden_limit=int(limits["garden_limit"]),
        )
        return chat_callable(
            CHAT_SYSTEM_TEMPLATE.format(context=context_text),
            messages,
            use_fast_model=provider == "openai",
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
