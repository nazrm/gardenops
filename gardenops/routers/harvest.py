from __future__ import annotations

import json
from datetime import date
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
    dump_metadata as _dump_metadata,
)
from gardenops.router_helpers import (
    generate_public_id as _generate_public_id,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    parse_metadata as _parse_metadata,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.routers.media import collect_media_cleanup_for_target
from gardenops.security import AuthContext
from gardenops.services.automation import on_harvest_logged
from gardenops.services.media_store import unlink_storage_keys
from gardenops.sql_dates import month_number_sql

router = APIRouter()

HarvestUnit = Literal["kg", "g", "lbs", "oz", "pieces", "bunches", "liters", "heads", "other"]
HarvestQuality = Literal["excellent", "good", "fair", "poor"]


class CreateHarvestBody(StrictBaseModel):
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: float = Field(ge=0)
    unit: HarvestUnit = "kg"
    quality: HarvestQuality = "good"
    notes: str = Field(default="", max_length=2000)
    plant_ids: list[str] = Field(default_factory=list)
    plot_ids: list[str] = Field(default_factory=list)


class UpdateHarvestBody(StrictBaseModel):
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    quantity: float | None = Field(default=None, ge=0)
    unit: HarvestUnit | None = None
    quality: HarvestQuality | None = None
    notes: str | None = Field(default=None, max_length=2000)
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
            f"SELECT plt_id FROM plants WHERE plt_id IN ({placeholders})",
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
    missing = [pid for pid in normalized if pid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plants not found in active garden: {', '.join(missing[:5])}",
        )
    return normalized


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
            f"SELECT plot_id FROM plots WHERE plot_id IN ({placeholders})",
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
    missing = [pid for pid in normalized if pid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plots not found in active garden: {', '.join(missing[:5])}",
        )
    return normalized


# ── Link management ──


def _load_harvest_links(
    db: DbConn, entry_ids: list[int]
) -> tuple[dict[int, list[str]], dict[int, list[str]], dict[int, list[dict[str, str]]]]:
    if not entry_ids:
        return {}, {}, {}
    placeholders = ",".join(["%s"] * len(entry_ids))
    plant_map: dict[int, list[str]] = {eid: [] for eid in entry_ids}
    for r in db.execute(
        f"SELECT entry_id, plt_id FROM harvest_entry_plants WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall():
        plant_map[int(r["entry_id"])].append(str(r["plt_id"]))
    plot_map: dict[int, list[str]] = {eid: [] for eid in entry_ids}
    plot_detail_map: dict[int, list[dict[str, str]]] = {eid: [] for eid in entry_ids}
    for r in db.execute(
        f"SELECT hep.entry_id, hep.plot_id, p.zone_name "
        f"FROM harvest_entry_plots hep "
        f"JOIN plots p ON p.plot_id = hep.plot_id "
        f"WHERE hep.entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall():
        plot_map[int(r["entry_id"])].append(str(r["plot_id"]))
        plot_detail_map[int(r["entry_id"])].append(
            {"plot_id": str(r["plot_id"]), "zone_name": str(r["zone_name"])}
        )
    return plant_map, plot_map, plot_detail_map


def _set_harvest_links(
    db: DbConn,
    context: AuthContext,
    entry_id: int,
    plant_ids: list[str],
    plot_ids: list[str],
) -> None:
    valid_plant_ids = _validate_plant_ids(db, context, plant_ids)
    valid_plot_ids = _validate_plot_ids(db, context, plot_ids)
    db.execute(
        "DELETE FROM harvest_entry_plants WHERE entry_id = %s",
        (entry_id,),
    )
    db.execute(
        "DELETE FROM harvest_entry_plots WHERE entry_id = %s",
        (entry_id,),
    )
    executemany(
        db,
        "INSERT INTO harvest_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(entry_id, pid) for pid in valid_plant_ids],
    )
    executemany(
        db,
        "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(entry_id, plot_id) for plot_id in valid_plot_ids],
    )


# ── Serialization ──


def _serialize_harvest(
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
        "occurred_on": str(row["occurred_on"]),
        "quantity": float(row["quantity"]),
        "unit": str(row["unit"]),
        "quality": str(row["quality"]),
        "notes": str(row["notes"] or ""),
        "metadata": metadata,
        "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] else None,
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "plant_ids": plant_ids,
        "plot_ids": plot_ids,
        "plots": plots or [{"plot_id": pid, "zone_name": ""} for pid in plot_ids],
    }


def _fetch_entry(db: DbConn, entry_id: str, garden_id: int) -> dict:
    row = db.execute(
        "SELECT * FROM harvest_entries WHERE public_id = %s AND garden_id = %s",
        (entry_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Harvest entry not found")
    return dict(row)


def _build_linked_journal_title(quantity: float, unit: str, plant_ids: list[str]) -> str:
    if plant_ids:
        return f"Harvested {quantity:g} {unit} from {plant_ids[0]}"
    return f"Harvested {quantity:g} {unit}"


def _upsert_linked_journal_entry(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    harvest_entry_id: str,
    journal_entry_id: str | None,
    occurred_on: str,
    quantity: float,
    unit: str,
    notes: str,
    plant_ids: list[str],
    plot_ids: list[str],
) -> tuple[int, str]:
    metadata_json = _dump_metadata(
        {
            "linked_harvest_entry_id": harvest_entry_id,
            "source": "auto:harvest",
            "quantity": quantity,
            "unit": unit,
        }
    )
    title = _build_linked_journal_title(quantity, unit, plant_ids)
    now_ms = current_timestamp_ms()
    journal_public_id = journal_entry_id
    if journal_entry_id is None:
        jrow = db.execute(
            """
            INSERT INTO garden_journal_entries
                (public_id, garden_id, event_type, occurred_on, title, notes,
                 metadata_json, actor_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, %s, 'harvested', %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, public_id
            """,
            (
                _generate_public_id("jrn"),
                garden_id,
                occurred_on,
                title,
                notes,
                metadata_json,
                context.user_id,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        assert jrow is not None
        internal_journal_id = int(jrow["id"])
        journal_public_id = str(jrow["public_id"])
    else:
        db.execute(
            """
            UPDATE garden_journal_entries
            SET occurred_on = %s,
                title = %s,
                notes = %s,
                metadata_json = %s,
                updated_at_ms = %s
            WHERE public_id = %s AND garden_id = %s
            """,
            (
                occurred_on,
                title,
                notes,
                metadata_json,
                now_ms,
                journal_entry_id,
                garden_id,
            ),
        )
        journal_row = db.execute(
            """
            SELECT id, public_id
            FROM garden_journal_entries
            WHERE public_id = %s AND garden_id = %s
            """,
            (journal_entry_id, garden_id),
        ).fetchone()
        if not journal_row:
            raise HTTPException(status_code=404, detail="Linked journal entry not found")
        internal_journal_id = int(journal_row["id"])
        journal_public_id = str(journal_row["public_id"])

    db.execute(
        "DELETE FROM garden_journal_entry_plants WHERE entry_id = %s",
        (internal_journal_id,),
    )
    db.execute(
        "DELETE FROM garden_journal_entry_plots WHERE entry_id = %s",
        (internal_journal_id,),
    )
    for plant_id in _validate_plant_ids(db, context, plant_ids):
        db.execute(
            "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (internal_journal_id, plant_id),
        )
    for plot_id in _validate_plot_ids(db, context, plot_ids):
        db.execute(
            "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            (internal_journal_id, plot_id),
        )
    return internal_journal_id, journal_public_id


def _delete_linked_journal_entry(
    db: DbConn,
    *,
    garden_id: int,
    journal_entry_id: str,
) -> None:
    media_storage_pairs = collect_media_cleanup_for_target(
        db,
        garden_id=garden_id,
        target_type="journal_entry",
        target_id=journal_entry_id,
    )
    db.execute(
        "DELETE FROM garden_journal_entries WHERE public_id = %s AND garden_id = %s",
        (journal_entry_id, garden_id),
    )
    if media_storage_pairs:
        for storage_key, preview_storage_key in media_storage_pairs:
            unlink_storage_keys(storage_key, preview_storage_key)


# ── Endpoints ──


@router.get("/harvest/summary")
def harvest_summary(
    request: Request,
    db: DB,
    year: int | None = Query(default=None),
    quality: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    target_year = year if year is not None else date.today().year
    month_expr = month_number_sql("occurred_on")

    conditions = ["garden_id = %s"]
    params: list[object] = [garden_id]
    year_start = f"{target_year}-01-01"
    year_end = f"{target_year}-12-31"
    conditions.append("occurred_on >= %s")
    conditions.append("occurred_on <= %s")
    params.extend([year_start, year_end])
    if quality:
        conditions.append("quality = %s")
        params.append(quality)
    if date_from:
        _validate_date(date_from)
        conditions.append("occurred_on >= %s")
        params.append(date_from)
    if date_to:
        _validate_date(date_to)
        conditions.append("occurred_on <= %s")
        params.append(date_to)
    where = " AND ".join(conditions)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM harvest_entries
        WHERE {where}
        """,
        params,
    ).fetchone()
    total_entries = int(total_row["c"]) if total_row else 0

    # By plant: group by plt_id and unit
    by_plant_rows = db.execute(
        f"""
        SELECT hep.plt_id, p.name, he.unit,
               SUM(he.quantity) AS total_qty,
               COUNT(*) AS entries
        FROM harvest_entries he
        JOIN harvest_entry_plants hep ON hep.entry_id = he.id
        LEFT JOIN plants p ON p.plt_id = hep.plt_id
        WHERE {where.replace("garden_id", "he.garden_id", 1)}
        GROUP BY hep.plt_id, p.name, he.unit
        ORDER BY total_qty DESC
        """,
        params,
    ).fetchall()
    by_plant = [
        {
            "plt_id": str(r["plt_id"]),
            "name": str(r["name"] or r["plt_id"]),
            "total_qty": float(r["total_qty"]),
            "unit": str(r["unit"]),
            "entries": int(r["entries"]),
        }
        for r in by_plant_rows
    ]

    # By month
    by_month_rows = db.execute(
        f"""
        SELECT {month_expr} AS month,
               SUM(quantity) AS total_qty,
               COUNT(*) AS entries
        FROM harvest_entries
        WHERE {where}
        GROUP BY month
        ORDER BY month
        """,
        params,
    ).fetchall()
    by_month = [
        {
            "month": int(r["month"]),
            "total_qty": float(r["total_qty"]),
            "entries": int(r["entries"]),
        }
        for r in by_month_rows
    ]

    # By quality
    quality_rows = db.execute(
        f"""
        SELECT quality, COUNT(*) AS c
        FROM harvest_entries
        WHERE {where}
        GROUP BY quality
        """,
        params,
    ).fetchall()
    by_quality = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    for r in quality_rows:
        by_quality[str(r["quality"])] = int(r["c"])

    return {
        "year": target_year,
        "total_entries": total_entries,
        "by_plant": by_plant,
        "by_month": by_month,
        "by_quality": by_quality,
    }


def _resolve_linked_journal_public_id(
    db: DbConn,
    *,
    garden_id: int,
    metadata: dict,
) -> str | None:
    raw_value = metadata.get("journal_entry_id")
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    if normalized.isdigit():
        row = db.execute(
            """
            SELECT public_id
            FROM garden_journal_entries
            WHERE id = %s AND garden_id = %s
            """,
            (int(normalized), garden_id),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT public_id
            FROM garden_journal_entries
            WHERE public_id = %s AND garden_id = %s
            """,
            (normalized, garden_id),
        ).fetchone()
    if not row:
        return None
    return str(row["public_id"])


@router.get("/harvest/{entry_id}")
def get_harvest_entry(request: Request, db: DB, entry_id: str) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_entry(db, entry_id, garden_id)
    internal_id = int(row["id"])
    plant_map, plot_map, plot_detail_map = _load_harvest_links(db, [internal_id])
    return _serialize_harvest(
        row,
        plant_map.get(internal_id, []),
        plot_map.get(internal_id, []),
        plot_detail_map.get(internal_id, []),
    )


@router.get("/harvest")
def list_harvest_entries(
    request: Request,
    db: DB,
    plant_id: str | None = Query(default=None),
    plot_id: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    quality: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["e.garden_id = %s"]
    params: list = [garden_id]

    if plant_id:
        conditions.append("e.id IN (SELECT entry_id FROM harvest_entry_plants WHERE plt_id = %s)")
        params.append(plant_id)

    if plot_id:
        conditions.append("e.id IN (SELECT entry_id FROM harvest_entry_plots WHERE plot_id = %s)")
        params.append(plot_id)

    if quality:
        conditions.append("e.quality = %s")
        params.append(quality)

    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            """
            (
                e.notes ILIKE %s
                OR e.id IN (
                    SELECT hep.entry_id
                    FROM harvest_entry_plants hep
                    JOIN plants p ON p.plt_id = hep.plt_id
                    WHERE hep.plt_id ILIKE %s
                       OR p.name ILIKE %s
                )
                OR e.id IN (
                    SELECT entry_id
                    FROM harvest_entry_plots
                    WHERE plot_id ILIKE %s
                )
            )
            """
        )
        params.extend([like, like, like, like])

    if date_from:
        _validate_date(date_from)
        conditions.append("e.occurred_on >= %s")
        params.append(date_from)

    if date_to:
        _validate_date(date_to)
        conditions.append("e.occurred_on <= %s")
        params.append(date_to)

    where = " AND ".join(conditions)

    total_row = db.execute(
        f"SELECT COUNT(*) AS c FROM harvest_entries e WHERE {where}",
        params,
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = db.execute(
        f"""
        SELECT e.*
        FROM harvest_entries e
        WHERE {where}
        ORDER BY e.occurred_on DESC, e.created_at_ms DESC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()

    entry_ids = [int(r["id"]) for r in rows]
    plant_map, plot_map, plot_detail_map = _load_harvest_links(db, entry_ids)

    entries = [
        _serialize_harvest(
            dict(r),
            plant_map.get(int(r["id"]), []),
            plot_map.get(int(r["id"]), []),
            plot_detail_map.get(int(r["id"]), []),
        )
        for r in rows
    ]
    return {"entries": entries, "total": total}


@router.post("/harvest", status_code=201)
def create_harvest_entry(
    request: Request,
    db: DB,
    body: CreateHarvestBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    _validate_date(body.occurred_on)

    now_ms = current_timestamp_ms()

    row = db.execute(
        """
        INSERT INTO harvest_entries
            (public_id, garden_id, occurred_on, quantity, unit, quality, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            _generate_public_id("hrv"),
            garden_id,
            body.occurred_on,
            body.quantity,
            body.unit,
            body.quality,
            body.notes,
            "{}",
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    entry_id = int(row["id"])
    entry_public_id = str(row["public_id"])
    _set_harvest_links(db, context, entry_id, body.plant_ids, body.plot_ids)
    _, journal_entry_public_id = _upsert_linked_journal_entry(
        db,
        context=context,
        garden_id=garden_id,
        harvest_entry_id=entry_public_id,
        journal_entry_id=None,
        occurred_on=body.occurred_on,
        quantity=body.quantity,
        unit=body.unit,
        notes=body.notes,
        plant_ids=body.plant_ids,
        plot_ids=body.plot_ids,
    )
    db.execute(
        "UPDATE harvest_entries SET metadata_json = %s, updated_at_ms = %s WHERE id = %s",
        (
            _dump_metadata({"journal_entry_id": journal_entry_public_id}),
            current_timestamp_ms(),
            entry_id,
        ),
    )
    on_harvest_logged(db, garden_id, entry_id)
    db.commit()
    return {"status": "ok", "id": entry_public_id, "journal_entry_id": journal_entry_public_id}


@router.patch("/harvest/{entry_id}")
def update_harvest_entry(
    request: Request,
    db: DB,
    entry_id: str,
    body: UpdateHarvestBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    existing_row = _fetch_entry(db, entry_id, garden_id)
    internal_id = int(existing_row["id"])
    public_id = str(existing_row["public_id"])

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    if "occurred_on" in updates and updates["occurred_on"] is not None:
        _validate_date(updates["occurred_on"])

    set_clauses = []
    params: list = []
    for field in ("occurred_on", "quantity", "unit", "quality", "notes"):
        if field in updates:
            set_clauses.append(f"{field} = %s")
            params.append(updates[field])

    set_clauses.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.append(internal_id)

    db.execute(
        f"UPDATE harvest_entries SET {', '.join(set_clauses)} WHERE id = %s",
        params,
    )

    if "plant_ids" in updates:
        plant_ids = updates["plant_ids"] if updates["plant_ids"] is not None else []
        plot_ids_val: list[str] = []
        if "plot_ids" in updates:
            plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        else:
            existing_plots = db.execute(
                "SELECT plot_id FROM harvest_entry_plots WHERE entry_id = %s",
                (internal_id,),
            ).fetchall()
            plot_ids_val = [str(r["plot_id"]) for r in existing_plots]
        _set_harvest_links(db, context, internal_id, plant_ids, plot_ids_val)
    elif "plot_ids" in updates:
        plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        existing_plants = db.execute(
            "SELECT plt_id FROM harvest_entry_plants WHERE entry_id = %s",
            (internal_id,),
        ).fetchall()
        plant_ids_current = [str(r["plt_id"]) for r in existing_plants]
        _set_harvest_links(db, context, internal_id, plant_ids_current, plot_ids_val)

    refreshed = _fetch_entry(db, entry_id, garden_id)
    plant_map, plot_map, plot_detail_map = _load_harvest_links(db, [internal_id])
    metadata = _parse_metadata(existing_row.get("metadata_json"))
    journal_entry_id = _resolve_linked_journal_public_id(
        db,
        garden_id=garden_id,
        metadata=metadata,
    )
    _, journal_entry_public_id = _upsert_linked_journal_entry(
        db,
        context=context,
        garden_id=garden_id,
        harvest_entry_id=public_id,
        journal_entry_id=journal_entry_id,
        occurred_on=str(refreshed["occurred_on"]),
        quantity=float(refreshed["quantity"]),
        unit=str(refreshed["unit"]),
        notes=str(refreshed["notes"] or ""),
        plant_ids=plant_map.get(internal_id, []),
        plot_ids=plot_map.get(internal_id, []),
    )
    db.execute(
        "UPDATE harvest_entries SET metadata_json = %s, updated_at_ms = %s WHERE id = %s",
        (
            _dump_metadata({"journal_entry_id": journal_entry_public_id}),
            current_timestamp_ms(),
            internal_id,
        ),
    )

    db.commit()
    return {"status": "ok"}


@router.delete("/harvest/{entry_id}")
def delete_harvest_entry(request: Request, db: DB, entry_id: str) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_entry(db, entry_id, garden_id)
    internal_id = int(row["id"])
    public_id = str(row["public_id"])
    metadata = _parse_metadata(row.get("metadata_json"))
    journal_entry_id = _resolve_linked_journal_public_id(
        db,
        garden_id=garden_id,
        metadata=metadata,
    )
    media_storage_pairs = collect_media_cleanup_for_target(
        db,
        garden_id=garden_id,
        target_type="harvest_entry",
        target_id=public_id,
    )
    if journal_entry_id is not None:
        _delete_linked_journal_entry(
            db,
            garden_id=garden_id,
            journal_entry_id=journal_entry_id,
        )
    db.execute("DELETE FROM harvest_entries WHERE id = %s", (internal_id,))
    db.commit()
    if media_storage_pairs:
        for storage_key, preview_storage_key in media_storage_pairs:
            unlink_storage_keys(storage_key, preview_storage_key)
    return {"status": "ok"}
