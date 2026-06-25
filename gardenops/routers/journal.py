from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.db import DB, DbConn, current_timestamp_ms, executemany
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    dedupe_ids as _dedupe_ids,
)
from gardenops.router_helpers import (
    generate_public_id as _generate_public_id,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    is_owner_or_admin as _is_owner_or_admin,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.routers.media import collect_media_cleanup_for_target
from gardenops.security import AuthContext
from gardenops.services.media_store import unlink_storage_keys
from gardenops.services.observation_updates import mark_seen_growing_from_observation

router = APIRouter()

EventType = Literal[
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


class CreateJournalEntryBody(StrictBaseModel):
    event_type: EventType
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=4000)
    metadata: dict = Field(default_factory=dict)
    plant_ids: list[str] = Field(default_factory=list)
    plot_ids: list[str] = Field(default_factory=list)


class UpdateJournalEntryBody(StrictBaseModel):
    event_type: EventType | None = None
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    metadata: dict | None = None
    plant_ids: list[str] | None = None
    plot_ids: list[str] | None = None


def _validate_plant_ids(
    db: DbConn,
    context: AuthContext,
    plant_ids: list[str],
) -> list[str]:
    normalized = _dedupe_ids(plant_ids)
    if not normalized:
        return []
    placeholders = ",".join(["%s"] * len(normalized))
    if _is_local_admin_fallback(context):
        rows = db.execute(
            f"""
            SELECT plt_id
            FROM plants
            WHERE plt_id IN ({placeholders})
            """,
            normalized,
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT plt_id
            FROM plant_ownership
            WHERE garden_id = %s AND plt_id IN ({placeholders})
            """,
            [_active_garden_id(context), *normalized],
        ).fetchall()
    found = {str(row["plt_id"]) for row in rows}
    missing = [plant_id for plant_id in normalized if plant_id not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plants not found in active garden: {', '.join(missing[:5])}",
        )
    return normalized


def _require_observation_plant_access(
    db: DbConn,
    context: AuthContext,
    plant_ids: list[str],
) -> None:
    normalized = _validate_plant_ids(db, context, plant_ids)
    if not normalized or _is_local_admin_fallback(context):
        return
    placeholders = ",".join(["%s"] * len(normalized))
    rows = db.execute(
        f"""
        SELECT plt_id, owner_user_id
        FROM plant_ownership
        WHERE garden_id = %s AND plt_id IN ({placeholders})
        """,
        [_active_garden_id(context), *normalized],
    ).fetchall()
    rows_by_plant = {str(row["plt_id"]): row for row in rows}
    denied = [
        plant_id
        for plant_id in normalized
        if plant_id not in rows_by_plant
        or not _is_owner_or_admin(context, rows_by_plant[plant_id]["owner_user_id"])
    ]
    if denied:
        raise HTTPException(
            status_code=404,
            detail=f"Plants not found in active garden: {', '.join(denied[:5])}",
        )


def _validate_plot_ids(
    db: DbConn,
    context: AuthContext,
    plot_ids: list[str],
) -> list[str]:
    normalized = _dedupe_ids(plot_ids)
    if not normalized:
        return []
    placeholders = ",".join(["%s"] * len(normalized))
    if _is_local_admin_fallback(context):
        rows = db.execute(
            f"""
            SELECT plot_id
            FROM plots
            WHERE plot_id IN ({placeholders})
            """,
            normalized,
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT plot_id
            FROM plot_ownership
            WHERE garden_id = %s AND plot_id IN ({placeholders})
            """,
            [_active_garden_id(context), *normalized],
        ).fetchall()
    found = {str(row["plot_id"]) for row in rows}
    missing = [plot_id for plot_id in normalized if plot_id not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plots not found in active garden: {', '.join(missing[:5])}",
        )
    return normalized


def _fetch_entry(db: DbConn, entry_id: str, garden_id: int) -> dict:
    row = db.execute(
        """
        SELECT e.*, u.username AS actor_username
        FROM garden_journal_entries e
        LEFT JOIN auth_users u ON u.id = e.actor_user_id
        WHERE e.public_id = %s AND e.garden_id = %s
        """,
        (entry_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return dict(row)


def _load_links(
    db: DbConn, entry_ids: list[int]
) -> tuple[dict[int, list[str]], dict[int, list[str]], dict[int, list[dict[str, str]]]]:
    if not entry_ids:
        return {}, {}, {}
    placeholders = ",".join(["%s"] * len(entry_ids))
    plant_map: dict[int, list[str]] = {eid: [] for eid in entry_ids}
    for r in db.execute(
        f"SELECT entry_id, plt_id FROM garden_journal_entry_plants "
        f"WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall():
        plant_map[int(r["entry_id"])].append(str(r["plt_id"]))
    plot_map: dict[int, list[str]] = {eid: [] for eid in entry_ids}
    plot_detail_map: dict[int, list[dict[str, str]]] = {eid: [] for eid in entry_ids}
    for r in db.execute(
        f"SELECT jep.entry_id, jep.plot_id, p.zone_name "
        f"FROM garden_journal_entry_plots jep "
        f"JOIN plots p ON p.plot_id = jep.plot_id "
        f"WHERE jep.entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall():
        plot_map[int(r["entry_id"])].append(str(r["plot_id"]))
        plot_detail_map[int(r["entry_id"])].append(
            {"plot_id": str(r["plot_id"]), "zone_name": str(r["zone_name"])}
        )
    return plant_map, plot_map, plot_detail_map


def _serialize_entry(
    row: dict,
    plant_ids: list[str],
    plot_ids: list[str],
    plots: list[dict[str, str]] | None = None,
) -> dict:
    metadata_raw = row.get("metadata_json") or "{}"
    try:
        metadata = json.loads(metadata_raw)
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        metadata = {}
    return {
        "id": str(row["public_id"]),
        "garden_id": int(row["garden_id"]),
        "event_type": str(row["event_type"]),
        "occurred_on": str(row["occurred_on"]),
        "title": str(row["title"] or ""),
        "notes": str(row["notes"] or ""),
        "metadata": metadata,
        "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] else None,
        "actor_username": str(row["actor_username"]) if row.get("actor_username") else None,
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "plant_ids": plant_ids,
        "plot_ids": plot_ids,
        "plots": plots or [{"plot_id": pid, "zone_name": ""} for pid in plot_ids],
    }


def _set_links(
    db: DbConn,
    context: AuthContext,
    entry_id: int,
    plant_ids: list[str],
    plot_ids: list[str],
) -> None:
    valid_plant_ids = _validate_plant_ids(db, context, plant_ids)
    valid_plot_ids = _validate_plot_ids(db, context, plot_ids)
    db.execute(
        "DELETE FROM garden_journal_entry_plants WHERE entry_id = %s",
        (entry_id,),
    )
    db.execute(
        "DELETE FROM garden_journal_entry_plots WHERE entry_id = %s",
        (entry_id,),
    )
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(entry_id, pid) for pid in valid_plant_ids],
    )
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(entry_id, plot_id) for plot_id in valid_plot_ids],
    )


def _apply_bloom_side_effects(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    event_type: str,
    occurred_on: str,
    plant_ids: list[str],
    plot_ids: list[str],
) -> None:
    if event_type != "bloomed" or not plant_ids:
        return
    _require_observation_plant_access(db, context, plant_ids)
    mark_seen_growing_from_observation(
        db,
        garden_id=garden_id,
        plant_ids=plant_ids,
        seen_date=occurred_on,
        plot_ids=plot_ids,
    )


@router.get("/journal")
def list_journal_entries(
    request: Request,
    db: DB,
    event_type: str | None = Query(default=None),
    plant_id: str | None = Query(default=None),
    plot_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    actor_user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["e.garden_id = %s"]
    params: list = [garden_id]

    if event_type:
        types = [t.strip() for t in event_type.split(",") if t.strip()]
        if types:
            ph = ",".join(["%s"] * len(types))
            conditions.append(f"e.event_type IN ({ph})")
            params.extend(types)

    if plant_id:
        conditions.append(
            "e.id IN (SELECT entry_id FROM garden_journal_entry_plants WHERE plt_id = %s)"
        )
        params.append(plant_id)

    if plot_id:
        conditions.append(
            "e.id IN (SELECT entry_id FROM garden_journal_entry_plots WHERE plot_id = %s)"
        )
        params.append(plot_id)

    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            """
            (
                e.title ILIKE %s
                OR e.notes ILIKE %s
                OR e.id IN (
                    SELECT jep.entry_id
                    FROM garden_journal_entry_plants jep
                    JOIN plants p ON p.plt_id = jep.plt_id
                    WHERE jep.plt_id ILIKE %s
                       OR p.name ILIKE %s
                       OR p.latin ILIKE %s
                )
                OR e.id IN (
                    SELECT entry_id
                    FROM garden_journal_entry_plots
                    WHERE plot_id ILIKE %s
                )
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    if date_from:
        _validate_date(date_from)
        conditions.append("e.occurred_on >= %s")
        params.append(date_from)

    if date_to:
        _validate_date(date_to)
        conditions.append("e.occurred_on <= %s")
        params.append(date_to)

    if actor_user_id is not None:
        conditions.append("e.actor_user_id = %s")
        params.append(actor_user_id)

    if actor:
        conditions.append("u.username ILIKE %s")
        params.append(f"%{actor.strip()}%")

    where = " AND ".join(conditions)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM garden_journal_entries e
        LEFT JOIN auth_users u ON u.id = e.actor_user_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = db.execute(
        f"""
        SELECT e.*, u.username AS actor_username
        FROM garden_journal_entries e
        LEFT JOIN auth_users u ON u.id = e.actor_user_id
        WHERE {where}
        ORDER BY e.occurred_on DESC, e.created_at_ms DESC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()

    entry_ids = [int(r["id"]) for r in rows]
    plant_map, plot_map, plot_detail_map = _load_links(db, entry_ids)

    entries = [
        _serialize_entry(
            dict(r),
            plant_map.get(int(r["id"]), []),
            plot_map.get(int(r["id"]), []),
            plot_detail_map.get(int(r["id"]), []),
        )
        for r in rows
    ]
    return {"entries": entries, "total": total}


@router.get("/journal/{entry_id}")
def get_journal_entry(request: Request, db: DB, entry_id: str) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_entry(db, entry_id, garden_id)
    internal_id = int(row["id"])
    plant_map, plot_map, plot_detail_map = _load_links(db, [internal_id])
    return _serialize_entry(
        row,
        plant_map.get(internal_id, []),
        plot_map.get(internal_id, []),
        plot_detail_map.get(internal_id, []),
    )


@router.post("/journal", status_code=201)
def create_journal_entry(
    request: Request,
    db: DB,
    body: CreateJournalEntryBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    _validate_date(body.occurred_on)
    valid_plant_ids = _validate_plant_ids(db, context, body.plant_ids)
    valid_plot_ids = _validate_plot_ids(db, context, body.plot_ids)
    if body.event_type == "bloomed":
        _require_observation_plant_access(db, context, valid_plant_ids)

    now_ms = current_timestamp_ms()
    metadata_str = json.dumps(body.metadata, sort_keys=True, separators=(",", ":"))

    row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, public_id
        """,
        (
            _generate_public_id("jrn"),
            garden_id,
            body.event_type,
            body.occurred_on,
            body.title,
            body.notes,
            metadata_str,
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    entry_id = int(row["id"])
    entry_public_id = str(row["public_id"])
    _set_links(db, context, entry_id, valid_plant_ids, valid_plot_ids)
    _apply_bloom_side_effects(
        db,
        context=context,
        garden_id=garden_id,
        event_type=body.event_type,
        occurred_on=body.occurred_on,
        plant_ids=valid_plant_ids,
        plot_ids=valid_plot_ids,
    )
    db.commit()
    return {"status": "ok", "id": entry_public_id}


@router.patch("/journal/{entry_id}")
def update_journal_entry(
    request: Request,
    db: DB,
    entry_id: str,
    body: UpdateJournalEntryBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    existing_row = _fetch_entry(db, entry_id, garden_id)
    internal_id = int(existing_row["id"])

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    if "occurred_on" in updates and updates["occurred_on"] is not None:
        _validate_date(updates["occurred_on"])

    set_clauses = []
    params: list = []
    for field in ("event_type", "occurred_on", "title", "notes"):
        if field in updates:
            set_clauses.append(f"{field} = %s")
            params.append(updates[field])
    if "metadata" in updates:
        set_clauses.append("metadata_json = %s")
        params.append(json.dumps(updates["metadata"], sort_keys=True, separators=(",", ":")))

    set_clauses.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.append(internal_id)

    db.execute(
        f"UPDATE garden_journal_entries SET {', '.join(set_clauses)} WHERE id = %s",
        params,
    )

    if "plant_ids" in updates:
        plant_ids = updates["plant_ids"] if updates["plant_ids"] is not None else []
        plot_ids_val: list[str] = []
        if "plot_ids" in updates:
            plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        else:
            existing_plots = db.execute(
                "SELECT plot_id FROM garden_journal_entry_plots WHERE entry_id = %s",
                (internal_id,),
            ).fetchall()
            plot_ids_val = [str(r["plot_id"]) for r in existing_plots]
        _set_links(db, context, internal_id, plant_ids, plot_ids_val)
    elif "plot_ids" in updates:
        plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        existing_plants = db.execute(
            "SELECT plt_id FROM garden_journal_entry_plants WHERE entry_id = %s",
            (internal_id,),
        ).fetchall()
        plant_ids_current = [str(r["plt_id"]) for r in existing_plants]
        _set_links(db, context, internal_id, plant_ids_current, plot_ids_val)

    db.commit()
    return {"status": "ok"}


@router.delete("/journal/{entry_id}")
def delete_journal_entry(request: Request, db: DB, entry_id: str) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    existing_row = _fetch_entry(db, entry_id, garden_id)
    internal_id = int(existing_row["id"])
    public_id = str(existing_row["public_id"])
    media_storage_pairs = collect_media_cleanup_for_target(
        db,
        garden_id=garden_id,
        target_type="journal_entry",
        target_id=public_id,
    )
    db.execute("DELETE FROM garden_journal_entries WHERE id = %s", (internal_id,))
    db.commit()
    for storage_key, preview_storage_key in media_storage_pairs:
        unlink_storage_keys(storage_key, preview_storage_key)
    return {"status": "ok"}
