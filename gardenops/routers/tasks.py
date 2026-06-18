from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Literal, cast

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
    require_write as _require_write,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.security import AuthContext
from gardenops.services.notification_service import clear_task_notifications
from gardenops.services.observation_updates import mark_seen_growing_from_observation
from gardenops.services.task_windows import derive_recommended_window_strings

router = APIRouter()

TaskType = Literal[
    "water",
    "protect",
    "prune",
    "deadhead",
    "divide",
    "fertilize",
    "sow",
    "plant_out",
    "observe_bloom",
    "harvest",
    "inspect_issue",
]

TaskStatus = Literal["pending", "completed", "skipped", "snoozed"]
TaskSeverity = Literal["low", "normal", "high"]

_ALLOWED_TASK_ACTIONS_BY_STATUS: dict[str, set[str]] = {
    "pending": {"complete", "skip", "snooze", "reschedule"},
    "snoozed": {"complete", "skip", "snooze", "reschedule"},
    "completed": {"complete", "skip", "snooze", "reschedule"},
    "skipped": {"snooze", "reschedule"},
}


class CreateTaskBody(StrictBaseModel):
    task_type: TaskType
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    severity: TaskSeverity = "normal"
    due_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_start_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_end_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    plant_ids: list[str] = Field(default_factory=list)
    plot_ids: list[str] = Field(default_factory=list)


class UpdateTaskBody(StrictBaseModel):
    task_type: TaskType | None = None
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    severity: TaskSeverity | None = None
    due_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_start_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_end_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    plant_ids: list[str] | None = None
    plot_ids: list[str] | None = None


class ActionTaskBody(StrictBaseModel):
    action: Literal["complete", "skip", "snooze", "reschedule"]
    snooze_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    reschedule_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str | None = Field(default=None, max_length=2000)


class BatchActionTaskBody(ActionTaskBody):
    task_ids: list[str] = Field(min_length=1, max_length=200)


class RefreshTaskDescriptionsBody(StrictBaseModel):
    force_all: bool = False


def _task_list_date_expressions() -> dict[str, str]:
    return {
        "due_on": "t.due_on::date",
        "snoozed_until": "t.snoozed_until::date",
        "today": "CURRENT_DATE",
        "plus_7_days": "(CURRENT_DATE + INTERVAL '7 days')::date",
        "plus_30_days": "(CURRENT_DATE + INTERVAL '30 days')::date",
    }


def _task_window_values(task_row: dict) -> tuple[str | None, str | None, str | None]:
    return (
        str(task_row["window_start_on"]) if task_row.get("window_start_on") else None,
        str(task_row["window_end_on"]) if task_row.get("window_end_on") else None,
        str(task_row["window_kind"]) if task_row.get("window_kind") else None,
    )


def _normalize_task_window(
    *,
    task_type: str,
    due_on: str,
    window_start_on: str | None,
    window_end_on: str | None,
) -> tuple[str | None, str | None, str | None]:
    if window_start_on is None and window_end_on is None:
        derived = derive_recommended_window_strings(task_type, due_on)
        if derived is None:
            return (None, None, None)
        return (derived[0], derived[1], "recommended")
    if window_start_on is None or window_end_on is None:
        raise HTTPException(
            status_code=422,
            detail="window_start_on and window_end_on must be provided together",
        )
    _validate_date(window_start_on)
    _validate_date(window_end_on)
    if date.fromisoformat(window_start_on) > date.fromisoformat(window_end_on):
        raise HTTPException(
            status_code=422,
            detail="window_start_on must be on or before window_end_on",
        )
    return (window_start_on, window_end_on, "manual")


def _resolve_updated_task_window(
    *,
    existing_task: dict,
    updates: dict,
) -> tuple[str | None, str | None, str | None]:
    existing_start, existing_end, existing_kind = _task_window_values(existing_task)
    next_task_type = str(updates.get("task_type") or existing_task["task_type"])
    next_due_on = str(updates.get("due_on") or existing_task["due_on"])
    window_fields_touched = "window_start_on" in updates or "window_end_on" in updates
    if window_fields_touched:
        next_window_start = updates.get("window_start_on", existing_start)
        next_window_end = updates.get("window_end_on", existing_end)
        if next_window_start is None and next_window_end is None:
            return (None, None, None)
        return _normalize_task_window(
            task_type=next_task_type,
            due_on=next_due_on,
            window_start_on=next_window_start,
            window_end_on=next_window_end,
        )
    if ("task_type" in updates or "due_on" in updates) and existing_kind == "recommended":
        return _normalize_task_window(
            task_type=next_task_type,
            due_on=next_due_on,
            window_start_on=None,
            window_end_on=None,
        )
    return (existing_start, existing_end, existing_kind)


def _resolve_quick_reschedule_window(
    task_row: dict,
    due_on: str,
) -> tuple[str | None, str | None, str | None]:
    return _normalize_task_window(
        task_type=str(task_row["task_type"]),
        due_on=due_on,
        window_start_on=None,
        window_end_on=None,
    )


# ── ID validation helpers ───────────────────────────────────────


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
    missing = [pid for pid in normalized if pid not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Plots not found in active garden: {', '.join(missing[:5])}",
        )
    return normalized


# ── Link management ────────────────────────────────────────────


def _load_links(
    db: DbConn, task_ids: list[int]
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    if not task_ids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(task_ids))
    plant_map: dict[int, list[str]] = {tid: [] for tid in task_ids}
    for r in db.execute(
        f"SELECT task_id, plt_id FROM garden_task_plants WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall():
        plant_map[int(r["task_id"])].append(str(r["plt_id"]))
    plot_map: dict[int, list[str]] = {tid: [] for tid in task_ids}
    for r in db.execute(
        f"SELECT task_id, plot_id FROM garden_task_plots WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall():
        plot_map[int(r["task_id"])].append(str(r["plot_id"]))
    return plant_map, plot_map


def _set_links(
    db: DbConn,
    context: AuthContext,
    task_id: int,
    plant_ids: list[str],
    plot_ids: list[str],
) -> None:
    valid_plant_ids = _validate_plant_ids(db, context, plant_ids)
    valid_plot_ids = _validate_plot_ids(db, context, plot_ids)
    db.execute(
        "DELETE FROM garden_task_plants WHERE task_id = %s",
        (task_id,),
    )
    db.execute(
        "DELETE FROM garden_task_plots WHERE task_id = %s",
        (task_id,),
    )
    executemany(
        db,
        "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
        [(task_id, pid) for pid in valid_plant_ids],
    )
    executemany(
        db,
        "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
        [(task_id, plot_id) for plot_id in valid_plot_ids],
    )


# ── Serialization ──────────────────────────────────────────────


def _serialize_task(
    row: dict,
    plant_ids: list[str],
    plot_ids: list[str],
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
        "task_type": str(row["task_type"]),
        "title": str(row["title"] or ""),
        "description": str(row["description"] or ""),
        "status": str(row["status"]),
        "severity": str(row["severity"]),
        "due_on": str(row["due_on"]),
        "snoozed_until": str(row["snoozed_until"]) if row["snoozed_until"] else None,
        "window_start_on": (str(row["window_start_on"]) if row.get("window_start_on") else None),
        "window_end_on": (str(row["window_end_on"]) if row.get("window_end_on") else None),
        "window_kind": str(row["window_kind"]) if row.get("window_kind") else None,
        "rule_source": str(row["rule_source"] or ""),
        "metadata": metadata,
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] else None
        ),
        "completed_by_user_id": (
            int(row["completed_by_user_id"]) if row["completed_by_user_id"] else None
        ),
        "completed_at_ms": (int(row["completed_at_ms"]) if row["completed_at_ms"] else None),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "plant_ids": plant_ids,
        "plot_ids": plot_ids,
    }


def _fetch_task(
    db: DbConn,
    task_id: str,
    garden_id: int,
    *,
    for_update: bool = False,
) -> dict:
    sql = "SELECT * FROM garden_tasks WHERE public_id = %s AND garden_id = %s"
    if for_update:
        sql += " FOR UPDATE"
    row = db.execute(sql, (task_id, garden_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return dict(row)


def _parse_task_metadata(task_row: dict) -> dict:
    metadata_raw = task_row.get("metadata_json") or "{}"
    try:
        metadata = json.loads(metadata_raw)
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        metadata = {}
    return metadata if isinstance(metadata, dict) else {}


def _update_task_metadata(
    db: DbConn,
    task_id: int,
    metadata: dict,
    updated_at_ms: int | None = None,
) -> None:
    if updated_at_ms is None:
        db.execute(
            "UPDATE garden_tasks SET metadata_json = %s WHERE id = %s",
            (json.dumps(metadata), task_id),
        )
        return
    db.execute(
        "UPDATE garden_tasks SET metadata_json = %s, updated_at_ms = %s WHERE id = %s",
        (json.dumps(metadata), updated_at_ms, task_id),
    )


def _load_task_rows_by_internal_ids(
    db: DbConn,
    task_ids: list[int],
    *,
    for_update: bool = False,
) -> dict[int, dict]:
    if not task_ids:
        return {}
    placeholders = ",".join(["%s"] * len(task_ids))
    sql = f"SELECT * FROM garden_tasks WHERE id IN ({placeholders})"
    if for_update:
        sql += " FOR UPDATE"
    rows = db.execute(sql, task_ids).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def _task_linked_plant_ids(
    db: DbConn,
    task_id: int,
) -> list[str]:
    rows = db.execute(
        "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id",
        (task_id,),
    ).fetchall()
    return [str(row["plt_id"]) for row in rows]


def _task_linked_plot_ids(
    db: DbConn,
    task_id: int,
) -> list[str]:
    rows = db.execute(
        "SELECT plot_id FROM garden_task_plots WHERE task_id = %s ORDER BY plot_id",
        (task_id,),
    ).fetchall()
    return [str(row["plot_id"]) for row in rows]


def _mark_seen_growing_for_completed_bloom_task(
    db: DbConn,
    task_row: dict,
) -> None:
    if str(task_row.get("task_type") or "") != "observe_bloom":
        return

    task_id = int(task_row["id"])
    garden_id = int(task_row["garden_id"])
    plant_ids = _task_linked_plant_ids(db, task_id)
    if not plant_ids:
        return

    task_plot_ids = _task_linked_plot_ids(db, task_id)
    mark_seen_growing_from_observation(
        db,
        garden_id=garden_id,
        plant_ids=plant_ids,
        seen_date=date.today().isoformat(),
        plot_ids=task_plot_ids,
    )


def _record_completed_bloom_observation(
    db: DbConn,
    context: AuthContext,
    task_row: dict,
    now_ms: int,
) -> None:
    if str(task_row.get("task_type") or "") != "observe_bloom":
        return
    task_metadata = _parse_task_metadata(task_row)
    if task_metadata.get("completion_journal_entry_id"):
        return

    task_id = int(task_row["id"])
    plant_ids = _task_linked_plant_ids(db, task_id)
    if not plant_ids:
        return

    metadata = {
        "source": "task_completion",
        "source_task_id": str(task_row["public_id"]),
        "source_task_type": "observe_bloom",
    }
    entry_row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, 'bloomed', %s, '', '', %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            _generate_public_id("jrn"),
            int(task_row["garden_id"]),
            date.today().isoformat(),
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert entry_row is not None
    entry_id = int(entry_row["id"])
    entry_public_id = str(entry_row["public_id"])
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(entry_id, plant_id) for plant_id in plant_ids],
    )
    task_metadata["completion_journal_entry_id"] = entry_public_id
    _update_task_metadata(db, task_id, task_metadata, updated_at_ms=now_ms)


def _dedupe_task_ids(task_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in task_ids:
        task_id = str(raw).strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="Task IDs must be non-empty strings")
        if task_id in seen:
            continue
        seen.add(task_id)
        normalized.append(task_id)
    return normalized


def _validate_task_ids(
    db: DbConn,
    garden_id: int,
    task_ids: list[str],
) -> list[int]:
    normalized = _dedupe_task_ids(task_ids)
    placeholders = ",".join(["%s"] * len(normalized))
    rows = db.execute(
        "SELECT id, public_id FROM garden_tasks "
        f"WHERE garden_id = %s AND public_id IN ({placeholders})",
        [garden_id, *normalized],
    ).fetchall()
    found = {str(row["public_id"]): int(row["id"]) for row in rows}
    missing = [task_id for task_id in normalized if task_id not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=(
                "Tasks not found in active garden: "
                + ", ".join(str(task_id) for task_id in missing[:10])
            ),
        )
    return [found[task_id] for task_id in normalized]


def _preferred_task_language(
    db: DbConn,
    context: AuthContext,
) -> str:
    if context.user_id is None:
        return "en"
    row = db.execute(
        "SELECT language FROM auth_users WHERE id = %s",
        (context.user_id,),
    ).fetchone()
    if row and row["language"] and str(row["language"]).strip().lower() == "no":
        return "no"
    return "en"


def _apply_task_action(
    db: DbConn,
    context: AuthContext,
    task_id: int,
    body: ActionTaskBody,
    now_ms: int,
    task_row: dict | None = None,
) -> None:
    if task_row is None:
        task_row = _load_task_rows_by_internal_ids(db, [task_id]).get(task_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail="Task not found")
    current_status = str(task_row.get("status") or "")
    allowed_actions = _ALLOWED_TASK_ACTIONS_BY_STATUS.get(current_status, set())
    if body.action not in allowed_actions:
        raise HTTPException(
            status_code=409,
            detail=f"Action {body.action} is not valid for {current_status} tasks",
        )
    if current_status == "completed" and body.action == "complete":
        return
    if body.action == "complete":
        db.execute(
            """
            UPDATE garden_tasks
            SET status = 'completed',
                completed_by_user_id = %s,
                completed_at_ms = %s,
                snoozed_until = NULL,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (context.user_id, now_ms, now_ms, task_id),
        )
        _record_completed_bloom_observation(db, context, task_row, now_ms)
        _mark_seen_growing_for_completed_bloom_task(db, task_row)
    elif body.action == "skip":
        db.execute(
            """
            UPDATE garden_tasks
            SET status = 'skipped',
                snoozed_until = NULL,
                completed_by_user_id = NULL,
                completed_at_ms = NULL,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (now_ms, task_id),
        )
    elif body.action == "snooze":
        if not body.snooze_until:
            raise HTTPException(
                status_code=422, detail="snooze_until is required for snooze action"
            )
        _validate_date(body.snooze_until)
        db.execute(
            """
            UPDATE garden_tasks
            SET status = 'snoozed',
                snoozed_until = %s,
                completed_by_user_id = NULL,
                completed_at_ms = NULL,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (body.snooze_until, now_ms, task_id),
        )
    elif body.action == "reschedule":
        if not body.reschedule_to:
            raise HTTPException(
                status_code=422,
                detail="reschedule_to is required for reschedule action",
            )
        _validate_date(body.reschedule_to)
        window_start_on, window_end_on, window_kind = _resolve_quick_reschedule_window(
            task_row,
            body.reschedule_to,
        )
        db.execute(
            """
            UPDATE garden_tasks
            SET due_on = %s,
                status = 'pending',
                snoozed_until = NULL,
                completed_by_user_id = NULL,
                completed_at_ms = NULL,
                window_start_on = %s,
                window_end_on = %s,
                window_kind = %s,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (body.reschedule_to, window_start_on, window_end_on, window_kind, now_ms, task_id),
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unknown action: {body.action}")

    clear_reason_by_action = {
        "complete": "completed",
        "skip": "skipped",
        "snooze": "snoozed",
        "reschedule": "rescheduled",
    }
    clear_task_notifications(
        db,
        garden_id=int(task_row["garden_id"]),
        task_public_id=str(task_row["public_id"]),
        reason=clear_reason_by_action[body.action],
        now_ms=now_ms,
    )

    if body.notes and body.notes.strip():
        row = db.execute(
            "SELECT metadata_json FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        metadata = _parse_task_metadata(dict(row) if row else {})
        notes_list = metadata.get("action_notes", [])
        if not isinstance(notes_list, list):
            notes_list = []
        notes_list.append(
            {
                "text": body.notes.strip(),
                "actor_user_id": context.user_id,
                "action": body.action,
                "at_ms": now_ms,
            }
        )
        metadata["action_notes"] = notes_list
        _update_task_metadata(db, task_id, metadata)


# ── Endpoints ──────────────────────────────────────────────────


@router.get("/tasks")
def list_tasks(
    request: Request,
    db: DB,
    view: str | None = Query(default=None),
    status: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    plant_id: str | None = Query(default=None),
    plot_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    date_expr = _task_list_date_expressions()
    actionable_status_clause = (
        f"(t.status = 'pending' OR "
        f"(t.status = 'snoozed' AND {date_expr['snoozed_until']} <= {date_expr['today']}))"
    )
    actionable_date = f"COALESCE({date_expr['snoozed_until']}, {date_expr['due_on']})"

    conditions = ["t.garden_id = %s"]
    params: list = [garden_id]

    if view == "today":
        conditions.append(
            f"({actionable_status_clause} AND {actionable_date} <= {date_expr['today']})"
        )
    elif view == "week":
        conditions.append(
            f"{actionable_status_clause} AND {actionable_date} <= {date_expr['plus_7_days']}"
        )
    elif view == "month":
        conditions.append(
            f"{actionable_status_clause} AND {actionable_date} <= {date_expr['plus_30_days']}"
        )
    elif view == "overdue":
        conditions.append(
            f"{actionable_status_clause} AND {actionable_date} < {date_expr['today']}"
        )

    if status:
        conditions.append("t.status = %s")
        params.append(status)

    if task_type:
        types = [tt.strip() for tt in task_type.split(",") if tt.strip()]
        if types:
            ph = ",".join(["%s"] * len(types))
            conditions.append(f"t.task_type IN ({ph})")
            params.extend(types)

    if plant_id:
        conditions.append("t.id IN (SELECT task_id FROM garden_task_plants WHERE plt_id = %s)")
        params.append(plant_id)

    if plot_id:
        conditions.append("t.id IN (SELECT task_id FROM garden_task_plots WHERE plot_id = %s)")
        params.append(plot_id)

    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            """
            (
                t.title ILIKE %s
                OR t.description ILIKE %s
                OR t.id IN (
                    SELECT gtp.task_id
                    FROM garden_task_plants gtp
                    JOIN plants p ON p.plt_id = gtp.plt_id
                    WHERE gtp.plt_id ILIKE %s
                       OR p.name ILIKE %s
                       OR p.latin ILIKE %s
                )
            )
            """
        )
        params.extend([like, like, like, like, like])

    where = " AND ".join(conditions)

    total_row = db.execute(
        f"SELECT COUNT(*) AS c FROM garden_tasks t WHERE {where}",
        params,
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = db.execute(
        f"""
        SELECT t.*
        FROM garden_tasks t
        WHERE {where}
        ORDER BY {actionable_date} ASC, t.created_at_ms DESC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()

    task_ids = [int(r["id"]) for r in rows]
    plant_map, plot_map = _load_links(db, task_ids)

    tasks = [
        _serialize_task(
            dict(r),
            plant_map.get(int(r["id"]), []),
            plot_map.get(int(r["id"]), []),
        )
        for r in rows
    ]
    return {"tasks": tasks, "total": total}


@router.get("/tasks/{task_id}")
def get_task(request: Request, db: DB, task_id: str) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_task(db, task_id, garden_id)
    internal_task_id = int(row["id"])
    plant_map, plot_map = _load_links(db, [internal_task_id])
    return _serialize_task(
        row,
        plant_map.get(internal_task_id, []),
        plot_map.get(internal_task_id, []),
    )


@router.post("/tasks", status_code=201)
def create_task(
    request: Request,
    db: DB,
    body: CreateTaskBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    _validate_date(body.due_on)
    window_start_on, window_end_on, window_kind = _normalize_task_window(
        task_type=body.task_type,
        due_on=body.due_on,
        window_start_on=body.window_start_on,
        window_end_on=body.window_end_on,
    )

    now_ms = current_timestamp_ms()
    row = db.execute(
        """
        INSERT INTO garden_tasks
            (garden_id, task_type, title, description, severity, due_on,
             window_start_on, window_end_on, window_kind,
             created_by_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, public_id
        """,
        (
            garden_id,
            body.task_type,
            body.title,
            body.description,
            body.severity,
            body.due_on,
            window_start_on,
            window_end_on,
            window_kind,
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    internal_task_id = int(row["id"])
    task_public_id = str(row["public_id"])
    _set_links(db, context, internal_task_id, body.plant_ids, body.plot_ids)
    db.commit()
    return {"status": "ok", "id": task_public_id}


@router.patch("/tasks/{task_id}")
def update_task(
    request: Request,
    db: DB,
    task_id: str,
    body: UpdateTaskBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    existing_task = _fetch_task(db, task_id, garden_id)
    internal_task_id = int(existing_task["id"])

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    if "due_on" in updates and updates["due_on"] is not None:
        _validate_date(updates["due_on"])

    next_window_start, next_window_end, next_window_kind = _resolve_updated_task_window(
        existing_task=existing_task,
        updates=updates,
    )

    set_clauses: list[str] = []
    params: list = []
    for field in ("task_type", "title", "description", "severity", "due_on"):
        if field in updates:
            set_clauses.append(f"{field} = %s")
            params.append(updates[field])
    existing_window_start, existing_window_end, existing_window_kind = _task_window_values(
        existing_task
    )
    if (
        next_window_start != existing_window_start
        or next_window_end != existing_window_end
        or next_window_kind != existing_window_kind
    ):
        set_clauses.extend(
            [
                "window_start_on = %s",
                "window_end_on = %s",
                "window_kind = %s",
            ]
        )
        params.extend([next_window_start, next_window_end, next_window_kind])

    set_clauses.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.append(internal_task_id)

    db.execute(
        f"UPDATE garden_tasks SET {', '.join(set_clauses)} WHERE id = %s",
        params,
    )

    if "description" in updates and existing_task.get("rule_source"):
        metadata = _parse_task_metadata(existing_task)
        metadata["description_customized"] = True
        metadata["description_generated"] = False
        metadata["description_source"] = "manual"
        _update_task_metadata(db, internal_task_id, metadata)

    if "plant_ids" in updates:
        plant_ids = updates["plant_ids"] if updates["plant_ids"] is not None else []
        plot_ids_val: list[str] = []
        if "plot_ids" in updates:
            plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        else:
            existing_plots = db.execute(
                "SELECT plot_id FROM garden_task_plots WHERE task_id = %s",
                (internal_task_id,),
            ).fetchall()
            plot_ids_val = [str(r["plot_id"]) for r in existing_plots]
        _set_links(db, context, internal_task_id, plant_ids, plot_ids_val)
    elif "plot_ids" in updates:
        plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        existing_plants = db.execute(
            "SELECT plt_id FROM garden_task_plants WHERE task_id = %s",
            (internal_task_id,),
        ).fetchall()
        plant_ids_current = [str(r["plt_id"]) for r in existing_plants]
        _set_links(db, context, internal_task_id, plant_ids_current, plot_ids_val)

    db.commit()
    return {"status": "ok"}


@router.post("/tasks/{task_id}/action")
def task_action(
    request: Request,
    db: DB,
    task_id: str,
    body: ActionTaskBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_task(db, task_id, garden_id, for_update=True)
    internal_task_id = int(row["id"])

    now_ms = current_timestamp_ms()
    _apply_task_action(db, context, internal_task_id, body, now_ms, task_row=row)
    db.commit()
    return {"status": "ok"}


@router.post("/tasks/batch-action")
def batch_task_action(
    request: Request,
    db: DB,
    body: BatchActionTaskBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    task_ids = _validate_task_ids(db, garden_id, body.task_ids)
    task_rows = _load_task_rows_by_internal_ids(db, task_ids, for_update=True)

    now_ms = current_timestamp_ms()
    action_body = ActionTaskBody(
        action=body.action,
        snooze_until=body.snooze_until,
        reschedule_to=body.reschedule_to,
        notes=body.notes,
    )
    for task_id in task_ids:
        task_row = task_rows.get(task_id)
        if task_row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        _apply_task_action(db, context, task_id, action_body, now_ms, task_row=task_row)

    db.commit()
    return {"status": "ok", "updated": len(task_ids)}


@router.delete("/tasks/{task_id}")
def delete_task(request: Request, db: DB, task_id: str) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_task(db, task_id, garden_id)
    clear_task_notifications(
        db,
        garden_id=garden_id,
        task_public_id=str(row["public_id"]),
        reason="deleted",
    )
    db.execute("DELETE FROM garden_tasks WHERE id = %s", (int(row["id"]),))
    db.commit()
    return {"status": "ok"}


@router.post("/tasks/refresh-descriptions")
def refresh_descriptions(
    request: Request,
    db: DB,
    body: RefreshTaskDescriptionsBody | None = None,
) -> dict:
    """Regenerate inferred descriptions for rule-backed tasks."""
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    preferred_locale = _preferred_task_language(db, context)
    force_all = bool(body.force_all) if body is not None else False

    from gardenops.services.task_generator import (
        _lookup_plant_context,
        generate_task_description_overrides,
        infer_task_description,
    )

    rows = db.execute(
        "SELECT * FROM garden_tasks"
        " WHERE garden_id = %s AND rule_source IS NOT NULL AND rule_source <> ''",
        (garden_id,),
    ).fetchall()

    now_ms = current_timestamp_ms()
    updated = 0
    task_specs: list[dict[str, object]] = []
    row_map: dict[int, dict] = {}
    for row in rows:
        task = dict(row)
        row_map[int(task["id"])] = task
        metadata = _parse_task_metadata(task)
        if (
            not force_all
            and metadata.get("description_customized")
            and str(task.get("description") or "").strip()
        ):
            continue
        desc_en, desc_no = infer_task_description(db, task)
        if not desc_en:
            continue
        plant_ids = db.execute(
            "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id LIMIT 1",
            (task["id"],),
        ).fetchall()
        plant_id = str(plant_ids[0]["plt_id"]) if plant_ids else ""
        task_specs.append(
            {
                "task_key": str(task["id"]),
                "task_id": int(task["id"]),
                "task_type": str(task["task_type"]),
                "work_order": bool(metadata.get("work_order")),
                "due_on": str(task["due_on"]),
                "plant": (
                    _lookup_plant_context(db, plant_id)
                    if plant_id
                    else {
                        "name": task.get("title") or str(task["id"]),
                        "category": "",
                        "light": "",
                        "hardiness": "",
                        "care_watering": "",
                        "care_soil": "",
                        "care_planting": "",
                        "care_maintenance": "",
                        "care_notes": "",
                    }
                ),
                "fallback_en": desc_en,
                "fallback_no": desc_no,
            }
        )

    overrides = generate_task_description_overrides(
        task_specs,
        preferred_locale=preferred_locale,
    )
    for spec in task_specs:
        desc_en, desc_no = overrides.get(
            str(spec["task_key"]),
            (str(spec["fallback_en"]), str(spec["fallback_no"])),
        )
        task_id = int(cast(int | float | str, spec["task_id"]))
        task = row_map.get(task_id)
        if task is None:
            continue
        metadata = _parse_task_metadata(task)
        metadata["description_no"] = desc_no
        metadata["description_generated"] = True
        metadata["description_source"] = "care_instructions"
        metadata.pop("description_customized", None)
        db.execute(
            "UPDATE garden_tasks"
            " SET description = %s, metadata_json = %s, updated_at_ms = %s"
            " WHERE id = %s",
            (desc_en, json.dumps(metadata), now_ms, task_id),
        )
        updated += 1

    db.commit()
    return {"updated": updated}


@router.post("/tasks/generate")
def generate_tasks_endpoint(request: Request, db: DB) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    preferred_locale = _preferred_task_language(db, context)

    now = datetime.now(UTC)
    from gardenops.services.task_generator import generate_tasks

    result = generate_tasks(
        db,
        garden_id,
        target_month=now.month,
        target_year=now.year,
        actor_user_id=context.user_id,
        preferred_locale=preferred_locale,
    )
    return result
