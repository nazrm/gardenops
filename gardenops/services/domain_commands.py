from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException

from gardenops.db import DbConn, current_timestamp_ms, executemany
from gardenops.router_helpers import generate_public_id
from gardenops.security import AuthContext
from gardenops.services.automation import on_harvest_logged, on_issue_created
from gardenops.services.media_store import collect_orphaned_media_storage_keys
from gardenops.services.notification_service import (
    clear_task_notifications,
    create_issue_created_notifications,
    refresh_task_notifications_for_task,
)
from gardenops.services.observation_updates import mark_seen_growing_from_observation
from gardenops.services.task_completion import (
    CompletionOutcome,
    capture_completion_original_task_state,
    completion_capture_already_recorded,
    grouped_completion_history_started,
    is_completion_capture_task,
    plant_names_for_ids,
    record_completion_journal_entry,
    refreshed_generated_group_description,
    refreshed_group_title,
    remaining_plant_ids_after_completion,
    restore_completion_capture_original_presentation,
    task_plot_ids_for_plant_ids,
    update_task_plant_links,
    update_task_plot_links,
    validate_completed_plant_ids,
    validate_completion_capture_plant_links,
    validate_completion_outcome,
)

JournalEventType = Literal[
    "planted",
    "moved",
    "divided",
    "pruned",
    "watered",
    "fertilized",
    "bloomed",
    "harvested",
    "died",
    "observed",
]
HarvestUnit = Literal["kg", "g", "lbs", "oz", "pieces", "bunches", "liters", "heads", "other"]
HarvestQuality = Literal["excellent", "good", "fair", "poor"]
IssueType = Literal["pest", "disease", "fungal", "nutrient", "environmental", "damage", "other"]
IssueSeverity = Literal["low", "normal", "high", "critical"]


@dataclass(frozen=True)
class CommandResult:
    primary_type: Literal["journal_entry", "harvest_entry", "issue", "task", "plant"]
    primary_id: str
    records: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    journal_entry_ids: tuple[str, ...] = field(default_factory=tuple)


def _garden_id(context: AuthContext) -> int:
    if context.garden_id is None:
        raise HTTPException(status_code=500, detail="Missing garden context")
    return int(context.garden_id)


def _effective_role(context: AuthContext) -> str:
    return context.garden_role or context.role


def _is_local_admin(context: AuthContext) -> bool:
    return context.auth_type == "none" and context.user_id is None and context.role == "admin"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for raw in values if (value := str(raw).strip())))


def _validate_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date: {value}") from None


def _validate_plant_ids(
    db: DbConn,
    context: AuthContext,
    plant_ids: list[str],
    *,
    observation: bool = False,
    mutation: bool = False,
) -> list[str]:
    normalized = _dedupe(plant_ids)
    if not normalized:
        return []
    placeholders = ",".join(["%s"] * len(normalized))
    garden_id = _garden_id(context)
    if _is_local_admin(context):
        rows = db.execute(
            f"SELECT plt_id, NULL::bigint AS owner_user_id FROM plants "
            f"WHERE plt_id IN ({placeholders})",  # noqa: S608
            normalized,
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT plt_id, owner_user_id FROM plant_ownership "
            f"WHERE garden_id = %s AND plt_id IN ({placeholders})",  # noqa: S608
            [garden_id, *normalized],
        ).fetchall()
    by_id = {str(row["plt_id"]): row for row in rows}
    missing = [plant_id for plant_id in normalized if plant_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plants not found in active garden: {', '.join(missing[:5])}",
        )
    if (
        (observation or mutation)
        and not _is_local_admin(context)
        and _effective_role(context) != "admin"
    ):
        denied = [
            plant_id
            for plant_id in normalized
            if context.user_id is None
            or by_id[plant_id]["owner_user_id"] is None
            or int(by_id[plant_id]["owner_user_id"]) != int(context.user_id)
        ]
        if denied:
            raise HTTPException(
                status_code=404,
                detail=f"Plants not found in active garden: {', '.join(denied[:5])}",
            )
    return normalized


def _validate_plot_ids(
    db: DbConn,
    context: AuthContext,
    plot_ids: list[str],
    *,
    mutation: bool = False,
) -> list[str]:
    normalized = _dedupe(plot_ids)
    if not normalized:
        return []
    placeholders = ",".join(["%s"] * len(normalized))
    garden_id = _garden_id(context)
    if _is_local_admin(context):
        rows = db.execute(
            f"SELECT plot_id, NULL::bigint AS owner_user_id, plot_kind, archived_at_ms "
            f"FROM plots WHERE plot_id IN ({placeholders})",  # noqa: S608
            normalized,
        ).fetchall()
    elif mutation:
        rows = db.execute(
            f"SELECT p.plot_id, po.owner_user_id, p.plot_kind, p.archived_at_ms "
            f"FROM plots p JOIN plot_ownership po ON po.plot_id = p.plot_id "
            f"WHERE po.garden_id = %s AND p.plot_id IN ({placeholders})",  # noqa: S608
            [garden_id, *normalized],
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT plot_id, NULL::bigint AS owner_user_id, ''::text AS plot_kind, "
            f"NULL::bigint AS archived_at_ms FROM plot_ownership "
            f"WHERE garden_id = %s AND plot_id IN ({placeholders})",  # noqa: S608
            [garden_id, *normalized],
        ).fetchall()
    by_id = {str(row["plot_id"]): row for row in rows}
    found = set(by_id)
    missing = [plot_id for plot_id in normalized if plot_id not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plots not found in active garden: {', '.join(missing[:5])}",
        )
    if mutation:
        archived = [
            plot_id for plot_id in normalized if by_id[plot_id]["archived_at_ms"] is not None
        ]
        if archived:
            raise HTTPException(status_code=410, detail="Plot is archived")
        if not _is_local_admin(context) and _effective_role(context) != "admin":
            denied = [
                plot_id
                for plot_id in normalized
                if not (
                    str(by_id[plot_id]["plot_kind"] or "") == "container"
                    and _effective_role(context) == "editor"
                )
                and (
                    context.user_id is None
                    or by_id[plot_id]["owner_user_id"] is None
                    or int(by_id[plot_id]["owner_user_id"]) != int(context.user_id)
                )
            ]
            if denied:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plots not found in active garden: {', '.join(denied[:5])}",
                )
    return normalized


def create_plant_command(
    db: DbConn,
    context: AuthContext,
    *,
    name: str,
    latin: str,
    category: str,
    bloom_month: str,
    color: str,
    hardiness: str,
    height_cm: int | None,
    light: str,
    link: str,
    deer_resistant: bool,
    care_watering: str,
    care_soil: str,
    care_planting: str,
    care_maintenance: str,
    care_notes: str,
    plot_id: str,
    quantity: int = 1,
    year_planted: str | None = None,
    plant_id: str | None = None,
) -> CommandResult:
    garden_id = _garden_id(context)
    valid_plot = _validate_plot_ids(db, context, [plot_id], mutation=True)[0]
    if quantity < 1:
        raise HTTPException(status_code=422, detail="Plant quantity must be at least 1")
    public_id = plant_id or generate_public_id("plt")
    if db.execute("SELECT 1 FROM plants WHERE plt_id = %s", (public_id,)).fetchone():
        raise HTTPException(status_code=409, detail="Plant ID already exists")
    owner_id = context.user_id
    if owner_id is None:
        owner = db.execute(
            """
            SELECT user_id FROM garden_memberships
            WHERE garden_id = %s AND role = 'admin'
            ORDER BY user_id LIMIT 1
            """,
            (garden_id,),
        ).fetchone()
        owner_id = int(owner["user_id"]) if owner else None
    if owner_id is None:
        raise HTTPException(status_code=409, detail="Garden has no plant owner")
    db.execute(
        """
        INSERT INTO plants (
            plt_id, name, latin, category, bloom_month, color, hardiness,
            height_cm, light, link, year_planted, deer_resistant,
            care_watering, care_soil, care_planting, care_maintenance, care_notes
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            public_id,
            name.strip(),
            latin.strip(),
            category.strip(),
            bloom_month.strip(),
            color.strip(),
            hardiness.strip(),
            None if height_cm is None or height_cm < 0 else min(height_cm, 4000),
            light.strip(),
            link.strip(),
            year_planted,
            int(deer_resistant),
            care_watering.strip(),
            care_soil.strip(),
            care_planting.strip(),
            care_maintenance.strip(),
            care_notes.strip(),
        ),
    )
    db.execute(
        "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
        (public_id, owner_id, garden_id),
    )
    db.execute(
        "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
        (valid_plot, public_id, quantity),
    )
    return CommandResult(
        primary_type="plant",
        primary_id=public_id,
        records=(("plant", public_id),),
    )


def assign_plant_command(
    db: DbConn,
    context: AuthContext,
    *,
    plant_id: str,
    plot_id: str,
    quantity: int = 1,
) -> CommandResult:
    valid_plant = _validate_plant_ids(db, context, [plant_id], mutation=True)[0]
    valid_plot = _validate_plot_ids(db, context, [plot_id], mutation=True)[0]
    if quantity < 1:
        raise HTTPException(status_code=422, detail="Plant quantity must be at least 1")
    db.execute(
        """
        INSERT INTO plot_plants (plot_id, plt_id, quantity)
        VALUES (%s, %s, %s)
        ON CONFLICT (plot_id, plt_id) DO UPDATE
            SET quantity = excluded.quantity
        """,
        (valid_plot, valid_plant, quantity),
    )
    return CommandResult(
        primary_type="plant",
        primary_id=valid_plant,
        records=(("plant", valid_plant),),
    )


def move_plant_command(
    db: DbConn,
    context: AuthContext,
    *,
    plant_id: str,
    from_plot_id: str,
    to_plot_id: str,
    quantity: int | None = None,
) -> CommandResult:
    valid_plant = _validate_plant_ids(db, context, [plant_id], mutation=True)[0]
    valid_plots = _validate_plot_ids(
        db,
        context,
        [from_plot_id, to_plot_id],
        mutation=True,
    )
    source_plot, destination_plot = valid_plots
    if source_plot == destination_plot:
        raise HTTPException(status_code=422, detail="Source and destination plots must differ")
    plot_rows = db.execute(
        "SELECT plot_id, plot_kind FROM plots WHERE plot_id = ANY(%s) ORDER BY plot_id FOR UPDATE",
        (sorted(valid_plots),),
    ).fetchall()
    plot_kinds = {str(row["plot_id"]): str(row["plot_kind"] or "") for row in plot_rows}
    source = db.execute(
        """
        SELECT quantity, seen_growing, seen_growing_date, room_label
        FROM plot_plants WHERE plot_id = %s AND plt_id = %s FOR UPDATE
        """,
        (source_plot, valid_plant),
    ).fetchone()
    if not source:
        raise HTTPException(status_code=404, detail="Plant not found in the source plot")
    source_quantity = int(source["quantity"])
    moved_quantity = source_quantity if quantity is None else quantity
    if moved_quantity < 1 or moved_quantity > source_quantity:
        raise HTTPException(
            status_code=422,
            detail=f"Move quantity must be between 1 and {source_quantity}",
        )
    destination = db.execute(
        "SELECT quantity FROM plot_plants WHERE plot_id = %s AND plt_id = %s FOR UPDATE",
        (destination_plot, valid_plant),
    ).fetchone()
    destination_room_label = (
        source["room_label"] if plot_kinds.get(destination_plot) == "indoor" else None
    )
    if moved_quantity == source_quantity:
        db.execute(
            "DELETE FROM plot_plants WHERE plot_id = %s AND plt_id = %s",
            (source_plot, valid_plant),
        )
    else:
        db.execute(
            "UPDATE plot_plants SET quantity = quantity - %s WHERE plot_id = %s AND plt_id = %s",
            (moved_quantity, source_plot, valid_plant),
        )
    if destination:
        db.execute(
            "UPDATE plot_plants SET quantity = quantity + %s WHERE plot_id = %s AND plt_id = %s",
            (moved_quantity, destination_plot, valid_plant),
        )
    else:
        db.execute(
            """
            INSERT INTO plot_plants (
                plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                destination_plot,
                valid_plant,
                moved_quantity,
                source["seen_growing"],
                source["seen_growing_date"],
                destination_room_label,
            ),
        )
    return CommandResult(
        primary_type="plant",
        primary_id=valid_plant,
        records=(("plant", valid_plant),),
    )


def delete_plant_command(
    db: DbConn,
    context: AuthContext,
    *,
    plant_id: str,
) -> CommandResult:
    garden_id = _garden_id(context)
    valid_plant = _validate_plant_ids(db, context, [plant_id], mutation=True)[0]
    locked = db.execute(
        "SELECT 1 FROM plants WHERE plt_id = %s FOR UPDATE",
        (valid_plant,),
    ).fetchone()
    if not locked:
        raise HTTPException(status_code=404, detail="Plant not found in active garden")
    collect_orphaned_media_storage_keys(
        db,
        garden_id=garden_id,
        target_type="plant",
        target_id=valid_plant,
    )
    db.execute(
        """
        DELETE FROM plot_plants
        WHERE plt_id = %s AND plot_id IN (
            SELECT plot_id FROM plot_ownership WHERE garden_id = %s
        )
        """,
        (valid_plant, garden_id),
    )
    db.execute(
        "DELETE FROM plant_ownership WHERE plt_id = %s AND garden_id = %s",
        (valid_plant, garden_id),
    )
    remaining = db.execute(
        "SELECT 1 FROM plant_ownership WHERE plt_id = %s", (valid_plant,)
    ).fetchone()
    if not remaining:
        db.execute("DELETE FROM plot_plants WHERE plt_id = %s", (valid_plant,))
        db.execute("DELETE FROM plants WHERE plt_id = %s", (valid_plant,))
    return CommandResult(
        primary_type="plant",
        primary_id=valid_plant,
        records=(("plant", valid_plant),),
    )


def create_journal_entry_command(
    db: DbConn,
    context: AuthContext,
    *,
    event_type: JournalEventType,
    occurred_on: str,
    title: str = "",
    notes: str = "",
    metadata: dict[str, Any] | None = None,
    plant_ids: list[str] | None = None,
    plot_ids: list[str] | None = None,
    public_id: str | None = None,
    now_ms: int | None = None,
    mark_seen_growing: Callable[..., None] = mark_seen_growing_from_observation,
) -> CommandResult:
    garden_id = _garden_id(context)
    occurred_on = _validate_date(occurred_on)
    valid_plants = _validate_plant_ids(
        db,
        context,
        plant_ids or [],
        observation=event_type == "bloomed",
    )
    valid_plots = _validate_plot_ids(db, context, plot_ids or [])
    timestamp = current_timestamp_ms() if now_ms is None else now_ms
    entry_public_id = public_id or generate_public_id("jrn")
    row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            entry_public_id,
            garden_id,
            event_type,
            occurred_on,
            title,
            notes,
            json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
            context.user_id,
            timestamp,
            timestamp,
        ),
    ).fetchone()
    assert row is not None
    entry_id = int(row["id"])
    entry_public_id = str(row["public_id"])
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(entry_id, plant_id) for plant_id in valid_plants],
    )
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(entry_id, plot_id) for plot_id in valid_plots],
    )
    if event_type == "bloomed" and valid_plants:
        mark_seen_growing(
            db,
            garden_id=garden_id,
            plant_ids=valid_plants,
            seen_date=occurred_on,
            plot_ids=valid_plots,
        )
    return CommandResult(
        primary_type="journal_entry",
        primary_id=entry_public_id,
        records=(("journal_entry", entry_public_id),),
        journal_entry_ids=(entry_public_id,),
    )


def _linked_harvest_journal(
    db: DbConn,
    context: AuthContext,
    *,
    garden_id: int,
    harvest_id: str,
    occurred_on: str,
    quantity: float,
    unit: str,
    notes: str,
    plant_ids: list[str],
    plot_ids: list[str],
    now_ms: int,
) -> str:
    title = (
        f"Harvested {quantity:g} {unit} from {plant_ids[0]}"
        if plant_ids
        else f"Harvested {quantity:g} {unit}"
    )
    result = create_journal_entry_command(
        db,
        context,
        event_type="harvested",
        occurred_on=occurred_on,
        title=title,
        notes=notes,
        metadata={
            "linked_harvest_entry_id": harvest_id,
            "source": "auto:harvest",
            "quantity": quantity,
            "unit": unit,
        },
        plant_ids=plant_ids,
        plot_ids=plot_ids,
        now_ms=now_ms,
    )
    return result.primary_id


def create_harvest_entry_command(
    db: DbConn,
    context: AuthContext,
    *,
    occurred_on: str,
    quantity: float,
    unit: HarvestUnit = "kg",
    quality: HarvestQuality = "good",
    notes: str = "",
    plant_ids: list[str] | None = None,
    plot_ids: list[str] | None = None,
    public_id: str | None = None,
    now_ms: int | None = None,
) -> CommandResult:
    garden_id = _garden_id(context)
    occurred_on = _validate_date(occurred_on)
    valid_plants = _validate_plant_ids(db, context, plant_ids or [])
    valid_plots = _validate_plot_ids(db, context, plot_ids or [])
    timestamp = current_timestamp_ms() if now_ms is None else now_ms
    harvest_id = public_id or generate_public_id("hrv")
    row = db.execute(
        """
        INSERT INTO harvest_entries
            (public_id, garden_id, occurred_on, quantity, unit, quality, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, '{}', %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            harvest_id,
            garden_id,
            occurred_on,
            quantity,
            unit,
            quality,
            notes,
            context.user_id,
            timestamp,
            timestamp,
        ),
    ).fetchone()
    assert row is not None
    internal_id = int(row["id"])
    harvest_id = str(row["public_id"])
    executemany(
        db,
        "INSERT INTO harvest_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(internal_id, plant_id) for plant_id in valid_plants],
    )
    executemany(
        db,
        "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(internal_id, plot_id) for plot_id in valid_plots],
    )
    journal_id = _linked_harvest_journal(
        db,
        context,
        garden_id=garden_id,
        harvest_id=harvest_id,
        occurred_on=occurred_on,
        quantity=quantity,
        unit=unit,
        notes=notes,
        plant_ids=valid_plants,
        plot_ids=valid_plots,
        now_ms=timestamp,
    )
    db.execute(
        "UPDATE harvest_entries SET metadata_json = %s, updated_at_ms = %s WHERE id = %s",
        (
            json.dumps({"journal_entry_id": journal_id}, sort_keys=True, separators=(",", ":")),
            timestamp,
            internal_id,
        ),
    )
    on_harvest_logged(db, garden_id, internal_id)
    return CommandResult(
        primary_type="harvest_entry",
        primary_id=harvest_id,
        records=(("harvest_entry", harvest_id), ("journal_entry", journal_id)),
        journal_entry_ids=(journal_id,),
    )


def create_issue_command(
    db: DbConn,
    context: AuthContext,
    *,
    issue_type: IssueType,
    title: str = "",
    description: str = "",
    severity: IssueSeverity = "normal",
    suspected_cause: str = "",
    treatment_plan: str = "",
    follow_up_on: str | None = None,
    plant_ids: list[str] | None = None,
    plot_ids: list[str] | None = None,
    public_id: str | None = None,
    now_ms: int | None = None,
) -> CommandResult:
    garden_id = _garden_id(context)
    if follow_up_on:
        follow_up_on = _validate_date(follow_up_on)
    valid_plants = _validate_plant_ids(db, context, plant_ids or [])
    valid_plots = _validate_plot_ids(db, context, plot_ids or [])
    timestamp = current_timestamp_ms() if now_ms is None else now_ms
    issue_id = public_id or generate_public_id("iss")
    history = [
        {
            "kind": "created",
            "at_ms": timestamp,
            "actor_user_id": context.user_id,
            "actor_username": context.username,
            "title": title,
            "status": "open",
            "severity": severity,
            "summary": "Issue reported",
        }
    ]
    row = db.execute(
        """
        INSERT INTO garden_issues
            (public_id, garden_id, issue_type, title, description, severity, status,
             suspected_cause, treatment_plan, follow_up_on, metadata_json,
             created_by_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            issue_id,
            garden_id,
            issue_type,
            title,
            description,
            severity,
            suspected_cause,
            treatment_plan,
            follow_up_on,
            json.dumps({"history": history}, sort_keys=True, separators=(",", ":")),
            context.user_id,
            timestamp,
            timestamp,
        ),
    ).fetchone()
    assert row is not None
    internal_id = int(row["id"])
    issue_id = str(row["public_id"])
    executemany(
        db,
        "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
        [(internal_id, plant_id) for plant_id in valid_plants],
    )
    executemany(
        db,
        "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
        [(internal_id, plot_id) for plot_id in valid_plots],
    )
    journal_notes = "\n".join(
        [
            f"Type: {issue_type}",
            f"Severity: {severity}",
            "Status: open",
            "Issue reported",
            *([description] if description.strip() else []),
        ]
    )
    journal = create_journal_entry_command(
        db,
        context,
        event_type="observed",
        occurred_on=date.today().isoformat(),
        title=f"Issue reported: {title.strip() or issue_type}",
        notes=journal_notes,
        metadata={
            "issue_id": issue_id,
            "issue_event": "created",
            "issue_status": "open",
            "issue_severity": severity,
        },
        plant_ids=valid_plants,
        plot_ids=valid_plots,
        now_ms=timestamp,
    )
    on_issue_created(db, garden_id, internal_id, context.user_id)
    create_issue_created_notifications(
        db,
        garden_id=garden_id,
        issue_public_id=issue_id,
        title=title or "Issue reported",
        body=description,
        severity=severity,
        actor_user_id=context.user_id,
    )
    return CommandResult(
        primary_type="issue",
        primary_id=issue_id,
        records=(("issue", issue_id), ("journal_entry", journal.primary_id)),
        journal_entry_ids=(journal.primary_id,),
    )


def _task_linked_plant_ids(db: DbConn, task_id: int) -> list[str]:
    rows = db.execute(
        "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id", (task_id,)
    ).fetchall()
    return [str(row["plt_id"]) for row in rows]


def _task_linked_plot_ids(db: DbConn, task_id: int, garden_id: int) -> list[str]:
    rows = db.execute(
        """
        SELECT gtp.plot_id
        FROM garden_task_plots gtp
        JOIN plots p ON p.plot_id = gtp.plot_id
        JOIN plot_ownership po ON po.plot_id = gtp.plot_id
        WHERE gtp.task_id = %s AND p.garden_id = %s AND po.garden_id = %s
        ORDER BY gtp.plot_id
        """,
        (task_id, garden_id, garden_id),
    ).fetchall()
    return [str(row["plot_id"]) for row in rows]


def _restore_original_task_links(db: DbConn, *, task_id: int, task_row: dict[str, Any]) -> None:
    raw_metadata = task_row.get("metadata_json") or "{}"
    try:
        metadata = json.loads(str(raw_metadata))
    except json.JSONDecodeError:
        return
    raw_plants = metadata.get("completion_capture_original_plant_ids")
    raw_plots = metadata.get("completion_capture_original_plot_ids")
    if isinstance(raw_plants, list):
        requested_ids = _dedupe([str(value).strip() for value in raw_plants if str(value).strip()])
        rows = (
            db.execute(
                """
            SELECT DISTINCT p.plt_id
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.garden_id = %s AND p.plt_id = ANY(%s)
            """,
                (int(task_row["garden_id"]), requested_ids),
            ).fetchall()
            if requested_ids
            else []
        )
        existing_ids = {str(row["plt_id"]) for row in rows}
        plant_ids = [plant_id for plant_id in requested_ids if plant_id in existing_ids]
        if plant_ids:
            update_task_plant_links(db, task_id=task_id, remaining_plant_ids=plant_ids)
    if isinstance(raw_plots, list):
        requested_plot_ids = _dedupe(
            [str(value).strip() for value in raw_plots if str(value).strip()]
        )
        rows = (
            db.execute(
                """
            SELECT p.plot_id
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.garden_id = %s AND po.garden_id = %s AND p.plot_id = ANY(%s)
            """,
                (
                    int(task_row["garden_id"]),
                    int(task_row["garden_id"]),
                    requested_plot_ids,
                ),
            ).fetchall()
            if requested_plot_ids
            else []
        )
        existing_plot_ids = {str(row["plot_id"]) for row in rows}
        update_task_plot_links(
            db,
            task_id=task_id,
            remaining_plot_ids=[
                plot_id for plot_id in requested_plot_ids if plot_id in existing_plot_ids
            ],
        )


def complete_task_command(
    db: DbConn,
    context: AuthContext,
    *,
    task_public_id: str,
    expected_updated_at_ms: int | None,
    completed_plant_ids: list[str] | None,
    completion_outcome: CompletionOutcome | None,
    notes: str | None,
    occurred_on: str,
    selected_plot_ids: list[str] | None = None,
    now_ms: int | None = None,
    locked_task_row: dict[str, Any] | None = None,
) -> CommandResult:
    garden_id = _garden_id(context)
    action_on = _validate_date(occurred_on)
    task_row = locked_task_row
    if task_row is None:
        row = db.execute(
            "SELECT * FROM garden_tasks WHERE public_id = %s AND garden_id = %s FOR UPDATE",
            (task_public_id, garden_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        task_row = dict(row)
    if str(task_row["public_id"]) != task_public_id or int(task_row["garden_id"]) != garden_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if expected_updated_at_ms is not None and int(task_row.get("updated_at_ms") or 0) != int(
        expected_updated_at_ms
    ):
        raise HTTPException(
            status_code=409,
            detail="Task changed since this action was created; refresh it and try again",
        )
    current_status = str(task_row.get("status") or "")
    if current_status not in {"pending", "snoozed", "completed"}:
        raise HTTPException(
            status_code=409,
            detail=f"Action complete is not valid for {current_status} tasks",
        )
    task_type = str(task_row.get("task_type") or "")
    linked_plants = _task_linked_plant_ids(db, int(task_row["id"]))
    if selected_plot_ids is not None:
        _validate_plot_ids(db, context, selected_plot_ids)
    validate_completion_capture_plant_links(
        task_type=task_type,
        linked_plant_ids=linked_plants,
    )
    outcome = validate_completion_outcome(task_type=task_type, outcome=completion_outcome)
    requested = _dedupe(completed_plant_ids or [])
    if (
        is_completion_capture_task(task_type)
        and requested
        and any(plant_id not in linked_plants for plant_id in requested)
        and completion_capture_already_recorded(
            task_row=task_row,
            task_type=task_type,
            selected_plant_ids=requested,
            outcome=outcome,
        )
    ):
        return CommandResult(primary_type="task", primary_id=task_public_id)
    if (
        current_status == "completed"
        and is_completion_capture_task(task_type)
        and completed_plant_ids is None
    ):
        return CommandResult(primary_type="task", primary_id=task_public_id)
    selected_plants = validate_completed_plant_ids(
        task_type=task_type,
        linked_plant_ids=linked_plants,
        requested_plant_ids=completed_plant_ids,
    )
    if task_type == "observe_bloom":
        _validate_plant_ids(db, context, selected_plants, observation=True)
    if current_status == "completed":
        return CommandResult(primary_type="task", primary_id=task_public_id)
    internal_id = int(task_row["id"])
    linked_plots = _task_linked_plot_ids(db, internal_id, garden_id)
    remaining_plants = remaining_plant_ids_after_completion(
        linked_plant_ids=linked_plants,
        completed_plant_ids=selected_plants,
    )
    partial = bool(is_completion_capture_task(task_type) and selected_plants and remaining_plants)
    selected_plots = linked_plots
    remaining_plots: list[str] = []
    if partial:
        selected_plots = task_plot_ids_for_plant_ids(
            db,
            task_id=internal_id,
            garden_id=garden_id,
            plant_ids=selected_plants,
        )
        remaining_plots = task_plot_ids_for_plant_ids(
            db,
            task_id=internal_id,
            garden_id=garden_id,
            plant_ids=remaining_plants,
        )
    timestamp = current_timestamp_ms() if now_ms is None else now_ms
    journal_id, metadata = record_completion_journal_entry(
        db,
        context=context,
        task_row=task_row,
        selected_plant_ids=selected_plants,
        selected_plot_ids=selected_plots,
        outcome=outcome,
        notes=notes,
        now_ms=timestamp,
        occurred_on=action_on,
    )
    metadata = capture_completion_original_task_state(
        task_row=task_row,
        metadata=metadata,
        linked_plant_ids=linked_plants,
        linked_plot_ids=linked_plots,
    )
    if partial:
        update_task_plant_links(db, task_id=internal_id, remaining_plant_ids=remaining_plants)
        update_task_plot_links(db, task_id=internal_id, remaining_plot_ids=remaining_plots)
        title = str(task_row.get("title") or "")
        description = str(task_row.get("description") or "")
        names = plant_names_for_ids(db, remaining_plants)
        if task_type in {"prune", "fertilize"} and names:
            title = refreshed_group_title(task_type, names)
            refreshed = refreshed_generated_group_description(
                db,
                task_row=task_row,
                task_type=task_type,
                remaining_plant_ids=remaining_plants,
                metadata=metadata,
            )
            if refreshed is not None:
                description, metadata = refreshed
        db.execute(
            """
            UPDATE garden_tasks
            SET title = %s, description = %s, status = 'pending',
                completed_by_user_id = NULL, completed_at_ms = NULL,
                snoozed_until = NULL, metadata_json = %s, updated_at_ms = %s
            WHERE id = %s
            """,
            (
                title,
                description,
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                timestamp,
                internal_id,
            ),
        )
        refresh_task_notifications_for_task(
            db,
            garden_id=garden_id,
            task_public_id=task_public_id,
            now_ms=timestamp,
        )
    else:
        title = str(task_row.get("title") or "")
        description = str(task_row.get("description") or "")
        if grouped_completion_history_started(task_row):
            _restore_original_task_links(db, task_id=internal_id, task_row=task_row)
            title, description, metadata = restore_completion_capture_original_presentation(
                task_row=task_row,
                metadata=metadata,
            )
        db.execute(
            """
            UPDATE garden_tasks
            SET title = %s, description = %s, status = 'completed',
                completed_by_user_id = %s, completed_at_ms = %s,
                snoozed_until = NULL, metadata_json = %s, updated_at_ms = %s
            WHERE id = %s
            """,
            (
                title,
                description,
                context.user_id,
                timestamp,
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                timestamp,
                internal_id,
            ),
        )
        clear_task_notifications(
            db,
            garden_id=garden_id,
            task_public_id=task_public_id,
            reason="completed",
            now_ms=timestamp,
        )
    if notes and notes.strip():
        row = db.execute(
            "SELECT metadata_json FROM garden_tasks WHERE id = %s", (internal_id,)
        ).fetchone()
        try:
            next_metadata = json.loads(str(row["metadata_json"] or "{}")) if row else {}
        except json.JSONDecodeError:
            next_metadata = {}
        action_notes = next_metadata.get("action_notes")
        if not isinstance(action_notes, list):
            action_notes = []
        action_notes.append(
            {
                "text": notes.strip(),
                "actor_user_id": context.user_id,
                "action": "complete",
                "at_ms": timestamp,
            }
        )
        next_metadata["action_notes"] = action_notes
        db.execute(
            "UPDATE garden_tasks SET metadata_json = %s WHERE id = %s",
            (json.dumps(next_metadata), internal_id),
        )
    records: list[tuple[str, str]] = [("task", task_public_id)]
    journal_ids: tuple[str, ...] = ()
    if journal_id:
        records.append(("journal_entry", journal_id))
        journal_ids = (journal_id,)
    return CommandResult(
        primary_type="task",
        primary_id=task_public_id,
        records=tuple(records),
        journal_entry_ids=journal_ids,
    )


def link_existing_media_command(
    db: DbConn,
    context: AuthContext,
    *,
    asset_id: str,
    targets: list[tuple[Literal["journal_entry", "plant", "issue", "harvest_entry"], str]],
) -> None:
    garden_id = _garden_id(context)
    asset = db.execute(
        "SELECT asset_id FROM media_assets WHERE asset_id = %s AND garden_id = %s",
        (asset_id, garden_id),
    ).fetchone()
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    for target_type, target_id in targets:
        target_id = str(target_id).strip()
        if target_type == "plant":
            _validate_plant_ids(db, context, [target_id])
        else:
            table, column = {
                "journal_entry": ("garden_journal_entries", "public_id"),
                "issue": ("garden_issues", "public_id"),
                "harvest_entry": ("harvest_entries", "public_id"),
            }[target_type]
            row = db.execute(
                f"SELECT 1 FROM {table} WHERE {column} = %s AND garden_id = %s",  # noqa: S608
                (target_id, garden_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"{target_type} not found")
        db.execute(
            """
            INSERT INTO media_links (asset_id, target_type, target_id, sort_order)
            VALUES (%s, %s, %s, 0)
            ON CONFLICT(asset_id, target_type, target_id) DO NOTHING
            """,
            (asset_id, target_type, target_id),
        )
        if target_type == "plant":
            existing_cover = db.execute(
                "SELECT 1 FROM plant_media_covers WHERE garden_id = %s AND plt_id = %s",
                (garden_id, target_id),
            ).fetchone()
            if not existing_cover:
                db.execute(
                    """
                    INSERT INTO plant_media_covers
                        (garden_id, plt_id, asset_id, set_at_ms, set_by_user_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(garden_id, plt_id) DO NOTHING
                    """,
                    (garden_id, target_id, asset_id, current_timestamp_ms(), context.user_id),
                )
                db.execute(
                    "DELETE FROM plant_cover_import_status WHERE garden_id = %s AND plt_id = %s",
                    (garden_id, target_id),
                )
