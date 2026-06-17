import csv
import io
import json
import os
import re
from collections import defaultdict
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.params import Query
from pydantic import Field, field_validator

from gardenops.db import DB, DbConn, current_timestamp_ms, executemany
from gardenops.events import notify_garden_modified
from gardenops.models import StrictBaseModel
from gardenops.parsing import parse_bool, parse_optional_bool
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    effective_role,
    is_owner_or_admin,
)
from gardenops.router_helpers import (
    generate_public_id as _generate_public_id,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.routers.media import collect_media_cleanup_for_target
from gardenops.security import (
    AuthContext,
    has_write_access,
)
from gardenops.services.media_store import unlink_storage_keys
from gardenops.services.observation_cycles import (
    is_current_observation_year,
    observation_year,
)
from gardenops.services.observation_updates import mark_seen_growing_from_observation

router = APIRouter()

_SEEN_GROWING_DATE_RE = re.compile(
    r"^\d{4}$"
    r"|^\d{4}-(0[1-9]|1[0-2])$"
    r"|^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"
)


def _validate_seen_growing_date(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if not _SEEN_GROWING_DATE_RE.match(v):
        msg = "seen_growing_date must be YYYY, YYYY-MM, or YYYY-MM-DD"
        raise ValueError(msg)
    return v


class CreatePlantBody(StrictBaseModel):
    plt_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    latin: str = Field(default="", max_length=200)
    category: str = Field(default="løk", max_length=40)
    bloom_month: str = Field(default="", max_length=120)
    color: str = Field(default="", max_length=120)
    hardiness: str = Field(default="", max_length=40)
    height_cm: int | None = None
    light: str = Field(default="", max_length=120)
    link: str = Field(default="", max_length=2000)
    year_planted: str | None = Field(default=None, max_length=80)
    deer_resistant: bool = False


class UpdatePlantBody(StrictBaseModel):
    name: str | None = None
    latin: str | None = None
    category: str | None = None
    bloom_month: str | None = None
    color: str | None = None
    hardiness: str | None = None
    height_cm: int | None = None
    light: str | None = None
    link: str | None = None
    year_planted: str | None = None
    deer_resistant: bool | None = None
    seen_growing: bool | None = None
    seen_growing_date: str | None = Field(default=None, max_length=10)
    care_watering: str | None = None
    care_soil: str | None = None
    care_planting: str | None = None
    care_maintenance: str | None = None
    care_notes: str | None = None

    @field_validator("seen_growing_date")
    @classmethod
    def validate_seen_growing_date(cls, v: str | None) -> str | None:
        return _validate_seen_growing_date(v)


class ImportPlantsCsvBody(StrictBaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)


PLANT_CSV_REQUIRED_COLUMNS = [
    "plt_id",
    "name",
    "latin",
    "category",
    "bloom_month",
    "color",
    "hardiness",
    "height_cm",
    "light",
    "link",
    "year_planted",
    "deer_resistant",
]
PLANT_CARE_COLUMNS = [
    "care_watering",
    "care_soil",
    "care_planting",
    "care_maintenance",
    "care_notes",
]
PLANT_ASSIGNMENTS_COLUMN = "plot_assignments"
PLANT_CSV_EXPORT_COLUMNS = [
    *PLANT_CSV_REQUIRED_COLUMNS,
    *PLANT_CARE_COLUMNS,
    PLANT_ASSIGNMENTS_COLUMN,
]

MAX_SANE_HEIGHT_CM = 4000


def _parse_optional_int(raw: str) -> int | None:
    value = raw.strip()
    if value == "":
        return None
    return int(value)


def _clamp_height_cm(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        return None
    return min(value, MAX_SANE_HEIGHT_CM)


def _parse_positive_int(raw: str) -> int:
    value = raw.strip()
    if value == "":
        raise ValueError("Missing quantity value")
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"Invalid quantity value: {raw}")
    return parsed


def _serialize_plot_assignments(rows: list[dict[str, Any]]) -> str:
    payload = [
        {
            "plot_id": str(row["plot_id"]),
            "quantity": int(row["quantity"]),
            "seen_growing": (None if row["seen_growing"] is None else bool(row["seen_growing"])),
            "seen_growing_date": row["seen_growing_date"],
        }
        for row in rows
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_plot_assignments(
    raw: str,
) -> list[dict[str, int | bool | str | None]]:
    text = raw.strip()
    if not text:
        return []

    assignments: dict[str, dict[str, int | bool | str | None]] = {}
    if text.startswith("["):
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError("plot_assignments must be a JSON array")
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("plot_assignments array items must be objects")
            plot_id = str(item.get("plot_id", "")).strip()
            if not plot_id:
                raise ValueError("plot_assignments items must include plot_id")
            quantity_raw = item.get("quantity", 1)
            try:
                quantity = int(quantity_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid quantity for plot assignment {plot_id}") from exc
            if quantity < 1:
                raise ValueError(f"Invalid quantity for plot assignment {plot_id}")
            seen_growing_raw = item.get("seen_growing", None)
            seen_growing = None
            if seen_growing_raw is not None:
                seen_growing = parse_optional_bool(str(seen_growing_raw))
            seen_growing_date_raw = item.get("seen_growing_date", None)
            seen_growing_date = _validate_seen_growing_date(
                None
                if seen_growing_date_raw is None
                else str(seen_growing_date_raw).strip() or None
            )
            existing = assignments.get(plot_id)
            if existing is None:
                assignments[plot_id] = {
                    "plot_id": plot_id,
                    "quantity": quantity,
                    "seen_growing": seen_growing,
                    "seen_growing_date": seen_growing_date,
                }
            else:
                existing["quantity"] = int(cast(int | str, existing["quantity"])) + quantity
                if seen_growing is not None:
                    existing["seen_growing"] = seen_growing
                if seen_growing_date is not None:
                    existing["seen_growing_date"] = seen_growing_date
        return [assignments[plot_id] for plot_id in sorted(assignments)]

    for token in text.split("|"):
        part = token.strip()
        if not part:
            continue
        plot_id = part
        quantity = 1
        if "=" in part:
            plot_id, _, quantity_raw = part.rpartition("=")
            plot_id = plot_id.strip()
            quantity = _parse_positive_int(quantity_raw)
        elif ":" in part:
            maybe_plot_id, _, maybe_quantity = part.rpartition(":")
            if maybe_quantity.strip().isdigit():
                plot_id = maybe_plot_id.strip()
                quantity = _parse_positive_int(maybe_quantity)
        plot_id = plot_id.strip()
        if not plot_id:
            raise ValueError("plot_assignments contains an empty plot id")
        existing = assignments.get(plot_id)
        if existing is None:
            assignments[plot_id] = {
                "plot_id": plot_id,
                "quantity": quantity,
                "seen_growing": None,
                "seen_growing_date": None,
            }
        else:
            existing["quantity"] = int(cast(int | str, existing["quantity"])) + quantity
    return [assignments[plot_id] for plot_id in sorted(assignments)]


def _build_safe_update(
    allowlist: set[str],
    updates: dict[str, object],
) -> tuple[str, list[object]]:
    """Build a parameterised SET clause from validated column names.

    Args:
        allowlist: Column names that are safe to include in the SET clause.
        updates: Mapping of column name to new value.

    Returns:
        A (set_clause, params) tuple where set_clause is e.g.
        ``"name = ?, color = ?"`` and params is the corresponding values.

    Raises:
        HTTPException: If any key in *updates* is not in *allowlist*.
    """
    for col in updates:
        if col not in allowlist:
            raise HTTPException(400, f"Invalid field: {col}")
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    return set_clause, list(updates.values())


def _effective_role(context: AuthContext) -> str:
    return effective_role(context)


def _is_owner_or_admin(context: AuthContext, owner_user_id: int | None) -> bool:
    return is_owner_or_admin(context, owner_user_id)


def _require_plant_access(
    db: DbConn,
    plt_id: str,
    context: AuthContext,
    *,
    read_only: bool = False,
) -> None:
    garden_id = _active_garden_id(context)
    plant_exists = db.execute(
        "SELECT 1 FROM plants WHERE plt_id = %s",
        (plt_id,),
    ).fetchone()
    if not plant_exists:
        raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    row = db.execute(
        """
        SELECT po.owner_user_id, po.garden_id
        FROM plant_ownership po
        WHERE po.plt_id = %s AND po.garden_id = %s
        """,
        (plt_id, garden_id),
    ).fetchone()
    if not row:
        if _is_local_admin_fallback(context):
            return
        raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    if read_only and _effective_role(context) in {"admin", "editor"}:
        return
    if not _is_owner_or_admin(context, row["owner_user_id"]):
        raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")


def _plant_owned_outside_garden(db: DbConn, *, plt_id: str, garden_id: int) -> bool:
    row = db.execute(
        """
        SELECT 1
        FROM plant_ownership
        WHERE plt_id = %s AND garden_id <> %s
        LIMIT 1
        """,
        (plt_id, garden_id),
    ).fetchone()
    return row is not None


def _require_plant_can_be_adopted_or_modified(
    db: DbConn,
    *,
    plt_id: str,
    garden_id: int,
) -> None:
    active_row = db.execute(
        """
        SELECT 1
        FROM plant_ownership
        WHERE plt_id = %s AND garden_id = %s
        LIMIT 1
        """,
        (plt_id, garden_id),
    ).fetchone()
    if active_row is not None:
        if _plant_owned_outside_garden(db, plt_id=plt_id, garden_id=garden_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Plant {plt_id} is shared with another garden and cannot be modified "
                    "until it is split into a garden-scoped plant"
                ),
            )
        return
    if _plant_owned_outside_garden(db, plt_id=plt_id, garden_id=garden_id):
        raise HTTPException(
            status_code=409,
            detail=f"Plant {plt_id} already belongs to another garden",
        )


def _plant_id_exists(db: DbConn, plt_id: str) -> bool:
    row = db.execute("SELECT 1 FROM plants WHERE plt_id = %s", (plt_id,)).fetchone()
    return row is not None


def _plant_scope_sql(context: AuthContext) -> tuple[str, list[object]]:
    garden_id = _active_garden_id(context)
    if _is_local_admin_fallback(context):
        return " AND (po.garden_id = %s OR po.garden_id IS NULL)", [garden_id]
    if _effective_role(context) in {"admin", "editor"}:
        return " AND po.garden_id = %s", [garden_id]
    return " AND po.garden_id = %s AND po.owner_user_id = %s", [garden_id, context.user_id]


def _assignment_rows_for_plant(
    db: DbConn,
    *,
    plt_id: str,
    garden_id: int,
) -> list[dict[str, Any]]:
    return db.execute(
        """
        SELECT pp.plot_id, pp.quantity, pp.seen_growing, pp.seen_growing_date
        FROM plot_plants pp
        LEFT JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE pp.plt_id = %s AND (pwo.garden_id = %s OR pwo.garden_id IS NULL)
        ORDER BY pp.plot_id
        """,
        (plt_id, garden_id),
    ).fetchall()


def _assignment_rows_for_plants(
    db: DbConn,
    *,
    plt_ids: list[str],
    garden_id: int,
) -> dict[str, list[dict[str, Any]]]:
    if not plt_ids:
        return {}
    placeholders = ",".join(["%s"] * len(plt_ids))
    rows = db.execute(
        f"""
        SELECT
            pp.plt_id,
            pp.plot_id,
            pp.quantity,
            pp.seen_growing,
            pp.seen_growing_date,
            CASE WHEN plot_ref.plot_id IS NULL THEN 1 ELSE 0 END AS missing_plot
        FROM plot_plants pp
        LEFT JOIN plots plot_ref ON plot_ref.plot_id = pp.plot_id
        LEFT JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE pp.plt_id IN ({placeholders})
          AND (pwo.garden_id = %s OR pwo.garden_id IS NULL)
        ORDER BY pp.plt_id, pp.plot_id
        """,
        [*plt_ids, garden_id],
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["plt_id"])].append(row)
    return grouped


def _last_bloomed_on_by_plant(
    db: DbConn,
    *,
    plt_ids: list[str],
    garden_id: int,
) -> dict[str, str]:
    if not plt_ids:
        return {}
    placeholders = ",".join(["%s"] * len(plt_ids))
    rows = db.execute(
        f"""
        SELECT jep.plt_id, MAX(je.occurred_on) AS last_bloomed_on
        FROM garden_journal_entry_plants jep
        JOIN garden_journal_entries je ON je.id = jep.entry_id
        WHERE je.garden_id = %s
          AND je.event_type = 'bloomed'
          AND jep.plt_id IN ({placeholders})
        GROUP BY jep.plt_id
        """,
        [garden_id, *plt_ids],
    ).fetchall()
    return {
        str(row["plt_id"]): str(row["last_bloomed_on"]) for row in rows if row["last_bloomed_on"]
    }


def _fetch_plant_rows(
    db: DbConn,
    *,
    context: AuthContext,
    q: str = "",
    category: str = "",
    plt_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT p.* FROM plants p LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id WHERE 1=1"
    params: list[object] = []
    scope_sql, scope_params = _plant_scope_sql(context)
    sql += scope_sql
    params.extend(scope_params)

    if plt_ids is not None:
        if not plt_ids:
            return []
        placeholders = ",".join(["%s"] * len(plt_ids))
        sql += f" AND p.plt_id IN ({placeholders})"  # noqa: S608
        params.extend(plt_ids)

    query = q.strip()
    if query:
        sql += " AND (p.name ILIKE %s OR p.latin ILIKE %s)"
        params.extend([f"%{query}%", f"%{query}%"])
    if category:
        sql += " AND p.category = %s"
        params.append(category)
    sql += " ORDER BY p.name, p.plt_id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return db.execute(sql, params).fetchall()


def _fetch_plant_search_rows(
    db: DbConn,
    *,
    context: AuthContext,
    q: str,
    limit: int,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT p.plt_id, p.name, COALESCE(p.latin, '') AS latin, "
        "COALESCE(p.category, '') AS category "
        "FROM plants p "
        "LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id "
        "WHERE 1=1"
    )
    params: list[object] = []
    scope_sql, scope_params = _plant_scope_sql(context)
    sql += scope_sql
    params.extend(scope_params)

    query = q.strip()
    if query:
        sql += " AND (p.name ILIKE %s OR p.latin ILIKE %s)"
        params.extend([f"%{query}%", f"%{query}%"])
    sql += " ORDER BY p.name, p.plt_id LIMIT %s"
    params.append(limit)
    return db.execute(sql, params).fetchall()


def _serialize_plant_rows(
    db: DbConn,
    *,
    rows: list[dict[str, Any]],
    garden_id: int,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    plant_ids = [str(row["plt_id"]) for row in rows]
    assignment_rows = _assignment_rows_for_plants(
        db,
        plt_ids=plant_ids,
        garden_id=garden_id,
    )
    last_bloomed_on_by_plant = _last_bloomed_on_by_plant(
        db,
        plt_ids=plant_ids,
        garden_id=garden_id,
    )

    result = []
    for r in rows:
        d = dict(r)
        plant_seen_growing = None if d.get("seen_growing") is None else bool(d["seen_growing"])
        plant_seen_growing_date = (
            str(d["seen_growing_date"]) if d.get("seen_growing_date") else None
        )
        plant_seen_growing_is_current_year = is_current_observation_year(plant_seen_growing_date)
        d["seen_growing"] = plant_seen_growing
        d["seen_growing_date"] = plant_seen_growing_date
        d["seen_growing_year"] = observation_year(plant_seen_growing_date)
        d["seen_growing_is_current_year"] = plant_seen_growing_is_current_year
        plant_assignments = assignment_rows.get(str(d["plt_id"]), [])
        d["plot_ids"] = [str(row["plot_id"]) for row in plant_assignments]
        d["missing_plot_ids"] = [
            str(row["plot_id"]) for row in plant_assignments if int(row["missing_plot"] or 0) == 1
        ]
        assignment_observed_this_year = any(
            is_current_observation_year(
                str(row["seen_growing_date"]) if row["seen_growing_date"] else None
            )
            for row in plant_assignments
        )
        not_seen_dates = [
            str(row["seen_growing_date"])
            for row in plant_assignments
            if row["seen_growing"] == 0
            and row["seen_growing_date"]
            and is_current_observation_year(str(row["seen_growing_date"]))
        ]
        not_seen_count = sum(
            1
            for row in plant_assignments
            if row["seen_growing"] == 0
            and is_current_observation_year(
                str(row["seen_growing_date"]) if row["seen_growing_date"] else None
            )
        )
        seen_count = sum(
            1
            for row in plant_assignments
            if row["seen_growing"] == 1
            and is_current_observation_year(
                str(row["seen_growing_date"]) if row["seen_growing_date"] else None
            )
        )
        unknown_seen_count = sum(
            1
            for row in plant_assignments
            if row["seen_growing"] is None
            or not is_current_observation_year(
                str(row["seen_growing_date"]) if row["seen_growing_date"] else None
            )
        )
        if (
            plant_seen_growing is False
            and plant_seen_growing_date
            and plant_seen_growing_is_current_year
        ):
            not_seen_dates.append(plant_seen_growing_date)
        if plant_seen_growing is True and plant_seen_growing_is_current_year:
            seen_count += 1
        elif plant_seen_growing is False and plant_seen_growing_is_current_year:
            not_seen_count += 1
        last_not_seen_year = max(not_seen_dates) if not_seen_dates else None
        if not_seen_count > 0 and seen_count == 0 and unknown_seen_count == 0:
            d["presence_status"] = "gone"
        elif not_seen_count > 0:
            d["presence_status"] = "mixed"
        else:
            d["presence_status"] = "present"
        d["last_not_seen_year"] = str(last_not_seen_year) if last_not_seen_year else None
        last_bloomed_on = last_bloomed_on_by_plant.get(str(d["plt_id"]))
        bloomed_this_year = is_current_observation_year(last_bloomed_on)
        d["last_bloomed_on"] = last_bloomed_on
        d["last_bloomed_year"] = observation_year(last_bloomed_on)
        d["bloomed_this_year"] = bloomed_this_year
        d["observed_this_year"] = (
            plant_seen_growing_is_current_year or assignment_observed_this_year or bloomed_this_year
        )
        d["deer_resistant"] = bool(d.get("deer_resistant"))
        d["quantity"] = sum(int(row["quantity"] or 0) for row in plant_assignments)
        result.append(d)
    return result


def _serialize_plant_search_rows(
    db: DbConn,
    *,
    rows: list[dict[str, Any]],
    garden_id: int,
    include_assignments: bool,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    assignment_rows: dict[str, list[dict[str, Any]]] = {}
    if include_assignments:
        plant_ids = [str(row["plt_id"]) for row in rows]
        assignment_rows = _assignment_rows_for_plants(
            db,
            plt_ids=plant_ids,
            garden_id=garden_id,
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        plt_id = str(row["plt_id"])
        item: dict[str, object] = {
            "plt_id": plt_id,
            "name": str(row["name"] or ""),
            "latin": str(row["latin"] or ""),
            "category": str(row["category"] or ""),
        }
        if include_assignments:
            plant_assignments = assignment_rows.get(plt_id, [])
            item["plot_ids"] = [str(entry["plot_id"]) for entry in plant_assignments]
            item["quantity"] = sum(int(entry["quantity"] or 0) for entry in plant_assignments)
        result.append(item)
    return result


def _serialize_plot_assignment(row: dict[str, Any]) -> dict[str, Any]:
    seen_growing_date = str(row["seen_growing_date"]) if row.get("seen_growing_date") else None
    return {
        "plot_id": str(row["plot_id"]),
        "quantity": int(row["quantity"]),
        "seen_growing": (None if row["seen_growing"] is None else bool(row["seen_growing"])),
        "seen_growing_date": seen_growing_date,
        "seen_growing_year": observation_year(seen_growing_date),
        "seen_growing_is_current_year": is_current_observation_year(seen_growing_date),
    }


@router.get("/plants/next-id")
def next_plant_id(db: DB, request: Request) -> dict:
    """Return a garden-local plant id that is safe to insert into the global plant table."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    rows = db.execute(
        """
        SELECT p.plt_id
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s AND p.plt_id LIKE 'PLT-%%'
        """,
        (garden_id,),
    ).fetchall()
    max_n = 0
    garden_prefix = f"PLT-G{garden_id}-"
    max_garden_n = 0
    for r in rows:
        plt_id = str(r["plt_id"])
        m = re.match(r"^PLT-(\d+)$", plt_id)
        if m:
            max_n = max(max_n, int(m.group(1)))
        scoped = re.match(rf"^{re.escape(garden_prefix)}(\d+)$", plt_id)
        if scoped:
            max_garden_n = max(max_garden_n, int(scoped.group(1)))

    candidate = f"PLT-{max_n + 1:03d}"
    if not _plant_id_exists(db, candidate):
        return {"next_id": candidate}

    # The plant primary key is global. Fall back to a garden-scoped prefix so
    # an empty garden does not get an id that already belongs to another garden.
    next_n = max_garden_n + 1
    while True:
        candidate = f"{garden_prefix}{next_n:03d}"
        if not _plant_id_exists(db, candidate):
            return {"next_id": candidate}
        next_n += 1


@router.get("/plants")
def list_plants(
    db: DB,
    request: Request,
    q: Annotated[str, Query()] = "",
    category: Annotated[str, Query()] = "",
) -> list[dict]:
    """List plants with optional search/filter, including plot assignments."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    rows = _fetch_plant_rows(
        db,
        context=context,
        q=q,
        category=category,
    )
    return _serialize_plant_rows(db, rows=rows, garden_id=garden_id)


@router.get("/plants/search")
def search_plants(
    db: DB,
    request: Request,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    include_assignments: Annotated[bool, Query()] = False,
) -> list[dict]:
    """Return a limited plant result set for search/autocomplete UI."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    rows = _fetch_plant_search_rows(
        db,
        context=context,
        q=q,
        limit=limit,
    )
    return _serialize_plant_search_rows(
        db,
        rows=rows,
        garden_id=garden_id,
        include_assignments=include_assignments,
    )


@router.get("/plants/{plt_id}/details")
def get_plant_details(plt_id: str, db: DB, request: Request) -> dict[str, Any]:
    """Return one plant with the same detail shape as GET /plants."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plant_access(db, plt_id, context, read_only=True)
    rows = _fetch_plant_rows(
        db,
        context=context,
        plt_ids=[plt_id],
        limit=1,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    return _serialize_plant_rows(db, rows=rows, garden_id=garden_id)[0]


def _resolve_plant_owner_id(
    db: DbConn,
    *,
    garden_id: int,
    requested_user_id: int | None,
) -> int | None:
    if requested_user_id is not None:
        return requested_user_id
    admin_row = db.execute(
        """SELECT user_id FROM garden_memberships
           WHERE garden_id = %s AND role = 'admin'
           ORDER BY user_id LIMIT 1""",
        (garden_id,),
    ).fetchone()
    if not admin_row:
        return None
    return int(admin_row["user_id"])


@router.post("/plants", status_code=201)
def create_plant(body: CreatePlantBody, db: DB, request: Request) -> dict:
    """Create a new plant entry in the database.

    If the plant already exists globally (e.g. created by another garden),
    it is adopted into this garden rather than rejected.
    """
    context = _auth_context(request)
    if not has_write_access(context):
        raise HTTPException(403, "Write access required")
    garden_id = _active_garden_id(context)

    existing = db.execute(
        "SELECT 1 FROM plants WHERE plt_id = %s",
        (body.plt_id,),
    ).fetchone()
    _require_plant_can_be_adopted_or_modified(db, plt_id=body.plt_id, garden_id=garden_id)
    if not existing:
        db.execute(
            """INSERT INTO plants
               (plt_id, name, latin, category, bloom_month, color,
                hardiness, height_cm, light, link, year_planted,
                deer_resistant)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                body.plt_id,
                body.name,
                body.latin,
                body.category,
                body.bloom_month,
                body.color,
                body.hardiness,
                _clamp_height_cm(body.height_cm),
                body.light,
                body.link,
                body.year_planted,
                int(body.deer_resistant),
            ),
        )
    owner_id = _resolve_plant_owner_id(
        db,
        garden_id=garden_id,
        requested_user_id=context.user_id,
    )
    if owner_id is not None:
        db.execute(
            """
            INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
            VALUES (%s, %s, %s)
            ON CONFLICT(plt_id, garden_id) DO NOTHING
            """,
            (body.plt_id, owner_id, garden_id),
        )
    db.commit()
    notify_garden_modified()
    return {"status": "ok", "plt_id": body.plt_id}


@router.post("/plants/import-csv")
def import_plants_csv(body: ImportPlantsCsvBody, db: DB, request: Request) -> dict:
    """Import plant metadata from exported CSV format, upserting by plant id."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    reader = csv.DictReader(io.StringIO(body.csv_text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV is empty")
    missing = [col for col in PLANT_CSV_REQUIRED_COLUMNS if col not in reader.fieldnames]
    if missing:
        raise HTTPException(
            400,
            f"CSV is missing required columns: {', '.join(missing)}",
        )
    has_assignments_column = PLANT_ASSIGNMENTS_COLUMN in reader.fieldnames

    max_rows = int(os.environ.get("CSV_IMPORT_MAX_ROWS", "5000"))
    created = 0
    updated = 0
    row_count = 0
    imported_assignments: dict[str, list[dict[str, int | bool | str | None]]] = {}
    owner_id = _resolve_plant_owner_id(
        db,
        garden_id=garden_id,
        requested_user_id=context.user_id,
    )
    try:
        for idx, row in enumerate(reader, start=2):
            if row_count >= max_rows:
                raise HTTPException(
                    400,
                    f"CSV exceeds maximum of {max_rows} rows",
                )
            plt_id = (row.get("plt_id") or "").strip()
            name = (row.get("name") or "").strip()
            category = (row.get("category") or "").strip()
            if not plt_id or not name or not category:
                raise HTTPException(
                    400,
                    f"CSV row {idx} is missing required plt_id, name, or category",
                )
            ownership_row = db.execute(
                """
                SELECT owner_user_id
                FROM plant_ownership
                WHERE plt_id = %s AND garden_id = %s
                """,
                (plt_id, garden_id),
            ).fetchone()
            if ownership_row and not _is_owner_or_admin(context, ownership_row["owner_user_id"]):
                raise HTTPException(
                    403,
                    f"Plant {plt_id} is owned by another user in this garden",
                )
            _require_plant_can_be_adopted_or_modified(db, plt_id=plt_id, garden_id=garden_id)
            if has_assignments_column:
                imported_assignments[plt_id] = _parse_plot_assignments(
                    row.get(PLANT_ASSIGNMENTS_COLUMN) or "",
                )

            payload = (
                plt_id,
                name,
                (row.get("latin") or "").strip(),
                category,
                (row.get("bloom_month") or "").strip(),
                (row.get("color") or "").strip(),
                (row.get("hardiness") or "").strip(),
                _clamp_height_cm(_parse_optional_int(row.get("height_cm") or "")),
                (row.get("light") or "").strip(),
                (row.get("link") or "").strip(),
                ((row.get("year_planted") or "").strip() or None),
                int(parse_bool(row.get("deer_resistant") or "")),
                (row.get("care_watering") or "").strip(),
                (row.get("care_soil") or "").strip(),
                (row.get("care_planting") or "").strip(),
                (row.get("care_maintenance") or "").strip(),
                (row.get("care_notes") or "").strip(),
            )
            exists = db.execute("SELECT 1 FROM plants WHERE plt_id = %s", (plt_id,)).fetchone()
            db.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color,
                    hardiness, height_cm, light, link, year_planted,
                    deer_resistant,
                    care_watering, care_soil,
                    care_planting, care_maintenance, care_notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(plt_id) DO UPDATE SET
                    name = excluded.name,
                    latin = excluded.latin,
                    category = excluded.category,
                    bloom_month = excluded.bloom_month,
                    color = excluded.color,
                    hardiness = excluded.hardiness,
                    height_cm = excluded.height_cm,
                    light = excluded.light,
                    link = excluded.link,
                    year_planted = excluded.year_planted,
                    deer_resistant = excluded.deer_resistant,
                    care_watering = excluded.care_watering,
                    care_soil = excluded.care_soil,
                    care_planting = excluded.care_planting,
                    care_maintenance = excluded.care_maintenance,
                    care_notes = excluded.care_notes
                """,
                payload,
            )
            if owner_id is not None:
                db.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id
                    """,
                    (plt_id, owner_id, garden_id),
                )
            created += 0 if exists else 1
            updated += 1 if exists else 0
            row_count += 1
        if has_assignments_column and imported_assignments:
            _reject_foreign_plot_targets(
                db,
                [
                    str(assignment["plot_id"])
                    for assignments in imported_assignments.values()
                    for assignment in assignments
                ],
                context,
            )
            db.execute("SET CONSTRAINTS ALL DEFERRED")
            for plt_id, assignments in imported_assignments.items():
                db.execute(
                    """
                    DELETE FROM plot_plants
                    WHERE plt_id = %s
                      AND (
                        plot_id IN (
                            SELECT plot_id FROM plot_ownership WHERE garden_id = %s
                        )
                        OR plot_id NOT IN (SELECT plot_id FROM plot_ownership)
                      )
                    """,
                    (plt_id, garden_id),
                )
                if assignments:
                    executemany(
                        db,
                        """
                        INSERT INTO plot_plants (
                            plot_id, plt_id, quantity, seen_growing, seen_growing_date
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (plot_id, plt_id) DO UPDATE SET
                            quantity = EXCLUDED.quantity,
                            seen_growing = EXCLUDED.seen_growing,
                            seen_growing_date = EXCLUDED.seen_growing_date
                        """,
                        [
                            (
                                str(assignment["plot_id"]),
                                plt_id,
                                int(cast(int | str, assignment["quantity"])),
                                (
                                    None
                                    if assignment["seen_growing"] is None
                                    else int(bool(assignment["seen_growing"]))
                                ),
                                assignment["seen_growing_date"],
                            )
                            for assignment in assignments
                        ],
                    )
            db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    notify_garden_modified()
    return {"status": "ok", "rows": row_count, "created": created, "updated": updated}


@router.patch("/plants/{plt_id}")
def update_plant(plt_id: str, body: UpdatePlantBody, db: DB, request: Request) -> dict:
    """Update fields of an existing plant."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plant_access(db, plt_id, context)
    _require_plant_can_be_adopted_or_modified(db, plt_id=plt_id, garden_id=garden_id)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    _ALLOWED_PLANT_COLS = {
        "name",
        "latin",
        "category",
        "bloom_month",
        "color",
        "hardiness",
        "height_cm",
        "light",
        "link",
        "year_planted",
        "deer_resistant",
        "seen_growing",
        "seen_growing_date",
        "care_watering",
        "care_soil",
        "care_planting",
        "care_maintenance",
        "care_notes",
    }
    if "deer_resistant" in updates:
        updates["deer_resistant"] = int(bool(updates["deer_resistant"]))
    if "seen_growing" in updates and updates["seen_growing"] is not None:
        updates["seen_growing"] = int(bool(updates["seen_growing"]))
    set_clause, params = _build_safe_update(_ALLOWED_PLANT_COLS, updates)
    db.execute(
        f"UPDATE plants SET {set_clause} WHERE plt_id = %s",
        [*params, plt_id],
    )
    db.commit()
    notify_garden_modified()
    return {"status": "ok"}


@router.delete("/plants/{plt_id}")
def delete_plant(plt_id: str, db: DB, request: Request) -> dict:
    """Remove a plant from this garden.

    Deletes the plant's ownership for this garden and removes it from
    plots in this garden.  The global plant record is only deleted when
    no other garden still references it.
    """
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plant_access(db, plt_id, context)
    media_storage_pairs = collect_media_cleanup_for_target(
        db,
        garden_id=garden_id,
        target_type="plant",
        target_id=plt_id,
    )

    # Remove from plots owned by this garden
    db.execute(
        """
        DELETE FROM plot_plants
        WHERE plt_id = %s AND plot_id IN (
            SELECT plot_id FROM plot_ownership WHERE garden_id = %s
        )
        """,
        (plt_id, garden_id),
    )
    # Remove ownership for this garden
    db.execute(
        "DELETE FROM plant_ownership WHERE plt_id = %s AND garden_id = %s",
        (plt_id, garden_id),
    )
    # If no other garden owns this plant, delete the global record
    remaining = db.execute(
        "SELECT 1 FROM plant_ownership WHERE plt_id = %s",
        (plt_id,),
    ).fetchone()
    if not remaining:
        db.execute("DELETE FROM plot_plants WHERE plt_id = %s", (plt_id,))
        db.execute("DELETE FROM plants WHERE plt_id = %s", (plt_id,))
    db.commit()
    for storage_key, preview_storage_key in media_storage_pairs:
        unlink_storage_keys(storage_key, preview_storage_key)
    notify_garden_modified()
    return {"status": "ok"}


@router.get("/plants/{plt_id}/plots")
def plant_plots(plt_id: str, db: DB, request: Request) -> list[str]:
    """Return plot IDs where a given plant is assigned."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    plant_exists = db.execute(
        "SELECT 1 FROM plants WHERE plt_id = %s",
        (plt_id,),
    ).fetchone()
    if not plant_exists:
        return []
    owner_row = db.execute(
        """
        SELECT po.owner_user_id, po.garden_id
        FROM plant_ownership po
        WHERE po.plt_id = %s AND po.garden_id = %s
        """,
        (plt_id, garden_id),
    ).fetchone()
    if not owner_row:
        if not _is_local_admin_fallback(context):
            raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    elif not _is_owner_or_admin(context, owner_row["owner_user_id"]):
        raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    if _is_local_admin_fallback(context):
        rows = db.execute(
            "SELECT plot_id FROM plot_plants WHERE plt_id = %s ORDER BY plot_id",
            (plt_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT pp.plot_id
            FROM plot_plants pp
            JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id = %s AND po.garden_id = %s
            ORDER BY pp.plot_id
            """,
            (plt_id, garden_id),
        ).fetchall()
    return [r["plot_id"] for r in rows]


@router.get("/plants/{plt_id}/assignments")
def plant_assignments(plt_id: str, db: DB, request: Request) -> list[dict]:
    """Return plot assignments (with seen_growing) for a given plant."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    plant_exists = db.execute(
        "SELECT 1 FROM plants WHERE plt_id = %s",
        (plt_id,),
    ).fetchone()
    if not plant_exists:
        return []
    owner_row = db.execute(
        """
        SELECT po.owner_user_id, po.garden_id
        FROM plant_ownership po
        WHERE po.plt_id = %s AND po.garden_id = %s
        """,
        (plt_id, garden_id),
    ).fetchone()
    if not owner_row:
        if not _is_local_admin_fallback(context):
            raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    elif not _is_owner_or_admin(context, owner_row["owner_user_id"]):
        raise HTTPException(status_code=404, detail=f"Plant {plt_id} not found")
    rows = _assignment_rows_for_plant(db, plt_id=plt_id, garden_id=garden_id)
    return [_serialize_plot_assignment(r) for r in rows]


# ── Batch actions ─────────────────────────────────────────


_BATCH_UPDATE_FIELDS = {
    "year_planted",
    "deer_resistant",
    "category",
}


class BatchUpdateBody(StrictBaseModel):
    plt_ids: list[str] = Field(min_length=1, max_length=500)
    updates: dict[str, object] = Field(default_factory=dict)
    plot_ids: list[str] = Field(default_factory=list, max_length=100)
    plot_action: str | None = Field(default=None, pattern=r"^(assign|remove)$")
    care_note_append: str = Field(default="", max_length=4000)


class BatchJournalEntryBody(StrictBaseModel):
    plt_ids: list[str] = Field(min_length=1, max_length=500)
    event_type: str = Field(
        pattern=r"^(planted|moved|divided|pruned|watered|"
        r"fertilized|bloomed|harvested|died|observed)$"
    )
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=4000)
    plot_ids: list[str] = Field(default_factory=list, max_length=100)


def _validate_batch_plant_ids(
    db: DbConn,
    plt_ids: list[str],
    context: AuthContext,
) -> None:
    """Ensure all plant IDs belong to this garden."""
    garden_id = _active_garden_id(context)
    ph = ",".join(["%s"] * len(plt_ids))
    if _is_local_admin_fallback(context):
        rows = db.execute(
            f"SELECT plt_id FROM plants WHERE plt_id IN ({ph})",
            plt_ids,
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT po.plt_id FROM plant_ownership po
            WHERE po.plt_id IN ({ph}) AND po.garden_id = %s
            """,
            [*plt_ids, garden_id],
        ).fetchall()
    found = {r["plt_id"] for r in rows}
    missing = [pid for pid in plt_ids if pid not in found]
    if missing:
        raise HTTPException(404, f"Plants not found: {', '.join(missing[:5])}")


def _normalize_batch_plot_ids(plot_ids: list[str]) -> list[str]:
    normalized = []
    seen: set[str] = set()
    for raw in plot_ids:
        plot_id = str(raw).strip()
        if not plot_id or plot_id in seen:
            continue
        seen.add(plot_id)
        normalized.append(plot_id)
    return normalized


def _validate_batch_plot_ids(
    db: DbConn,
    plot_ids: list[str],
    context: AuthContext,
) -> list[str]:
    normalized = _normalize_batch_plot_ids(plot_ids)
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
        garden_id = _active_garden_id(context)
        rows = db.execute(
            f"""
            SELECT plot_id
            FROM plot_ownership
            WHERE garden_id = %s AND plot_id IN ({placeholders})
            """,
            [garden_id, *normalized],
        ).fetchall()
    found = {str(row["plot_id"]) for row in rows}
    missing = [plot_id for plot_id in normalized if plot_id not in found]
    if missing:
        raise HTTPException(
            404,
            f"Plots not found in active garden: {', '.join(missing[:5])}",
        )
    return normalized


def _validate_batch_plot_targets(
    db: DbConn,
    plot_ids: list[str],
    context: AuthContext,
) -> list[str]:
    normalized = _normalize_batch_plot_ids(plot_ids)
    if not normalized:
        return []
    garden_id = _active_garden_id(context)
    placeholders = ",".join(["%s"] * len(normalized))
    rows = db.execute(
        f"""
        SELECT p.plot_id, po.garden_id
        FROM plots p
        LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE p.plot_id IN ({placeholders})
        """,
        normalized,
    ).fetchall()
    rows_by_plot = {str(row["plot_id"]): row for row in rows}
    missing_or_foreign = [
        plot_id
        for plot_id in normalized
        if plot_id not in rows_by_plot
        or rows_by_plot[plot_id]["garden_id"] is None
        or int(rows_by_plot[plot_id]["garden_id"]) != garden_id
    ]
    if missing_or_foreign:
        raise HTTPException(
            404,
            f"Plots not found in active garden: {', '.join(missing_or_foreign[:5])}",
        )
    return normalized


def _reject_foreign_plot_targets(
    db: DbConn,
    plot_ids: list[str],
    context: AuthContext,
) -> list[str]:
    normalized = _normalize_batch_plot_ids(plot_ids)
    if not normalized:
        return []
    garden_id = _active_garden_id(context)
    placeholders = ",".join(["%s"] * len(normalized))
    rows = db.execute(
        f"""
        SELECT plot_id, garden_id
        FROM plot_ownership
        WHERE plot_id IN ({placeholders})
          AND garden_id IS NOT NULL
          AND garden_id != %s
        """,
        [*normalized, garden_id],
    ).fetchall()
    foreign = [str(row["plot_id"]) for row in rows]
    if foreign:
        raise HTTPException(
            404,
            f"Plots not found in active garden: {', '.join(foreign[:5])}",
        )
    return normalized


@router.post("/plants/batch-update")
def batch_update_plants(body: BatchUpdateBody, db: DB, request: Request) -> dict:
    context = _auth_context(request)
    if not has_write_access(context):
        raise HTTPException(403, "Write access required")
    garden_id = _active_garden_id(context)  # validates garden context
    _validate_batch_plant_ids(db, body.plt_ids, context)
    for plt_id in body.plt_ids:
        _require_plant_access(db, plt_id, context)
        _require_plant_can_be_adopted_or_modified(db, plt_id=plt_id, garden_id=garden_id)

    count = 0

    # Field updates
    if body.updates:
        set_clause, params = _build_safe_update(
            _BATCH_UPDATE_FIELDS,
            body.updates,
        )
        ph = ",".join(["%s"] * len(body.plt_ids))
        db.execute(
            f"UPDATE plants SET {set_clause} WHERE plt_id IN ({ph})",
            [*params, *body.plt_ids],
        )
        count = len(body.plt_ids)

    if body.care_note_append.strip():
        note = body.care_note_append.strip()
        ph = ",".join(["%s"] * len(body.plt_ids))
        db.execute(
            f"""
            UPDATE plants
            SET care_notes = CASE
                WHEN COALESCE(TRIM(care_notes), '') = '' THEN %s
                ELSE care_notes || char(10) || %s
            END
            WHERE plt_id IN ({ph})
            """,
            [note, note, *body.plt_ids],
        )
        count = max(count, len(body.plt_ids))

    # Plot assignment changes
    if body.plot_ids and body.plot_action:
        normalized_plot_ids = _validate_batch_plot_targets(
            db,
            body.plot_ids,
            context,
        )
        if body.plot_action == "assign":
            executemany(
                db,
                "INSERT INTO plot_plants "
                "(plot_id, plt_id, quantity) VALUES (%s, %s, 1) "
                "ON CONFLICT DO NOTHING",
                ((plot_id, plt_id) for plt_id in body.plt_ids for plot_id in normalized_plot_ids),
            )
            count = max(count, len(body.plt_ids))
        elif body.plot_action == "remove":
            p_plots = ",".join(["%s"] * len(normalized_plot_ids))
            p_plants = ",".join(["%s"] * len(body.plt_ids))
            db.execute(
                f"DELETE FROM plot_plants WHERE plot_id IN ({p_plots}) AND plt_id IN ({p_plants})",
                [*normalized_plot_ids, *body.plt_ids],
            )
            count = max(count, len(body.plt_ids))

    db.commit()
    notify_garden_modified()
    return {"status": "ok", "updated": count}


@router.post("/plants/batch-journal-entry", status_code=201)
def batch_journal_entry(body: BatchJournalEntryBody, db: DB, request: Request) -> dict:
    context = _auth_context(request)
    if not has_write_access(context):
        raise HTTPException(403, "Write access required")
    gid = _active_garden_id(context)
    _validate_batch_plant_ids(db, body.plt_ids, context)
    valid_plot_ids = _validate_batch_plot_ids(db, body.plot_ids, context)
    _validate_date(body.occurred_on)

    now_ms = current_timestamp_ms()
    row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms,
             updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, '{}', %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            _generate_public_id("jrn"),
            gid,
            body.event_type,
            body.occurred_on,
            body.title,
            body.notes,
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    entry_id = int(row["id"])
    entry_public_id = str(row["public_id"])
    for plt_id in body.plt_ids:
        db.execute(
            "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (entry_id, plt_id),
        )
    for plot_id in valid_plot_ids:
        db.execute(
            "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            (entry_id, plot_id),
        )
    if body.event_type == "bloomed":
        mark_seen_growing_from_observation(
            db,
            garden_id=gid,
            plant_ids=body.plt_ids,
            seen_date=body.occurred_on,
            plot_ids=valid_plot_ids,
        )
    db.commit()
    return {"status": "ok", "id": entry_public_id}
