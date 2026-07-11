from __future__ import annotations

import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.db import DB, DbConn, current_timestamp_ms, executemany
from gardenops.models import StrictBaseModel
from gardenops.offline_idempotency import (
    ISSUE_TARGET,
    ISSUES_ENDPOINT,
    prepare_operation,
    raise_operation_target_gone,
    reserve_operation,
)
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
from gardenops.services.automation import on_issue_created, sync_issue_followup_task
from gardenops.services.media_store import unlink_storage_keys
from gardenops.services.notification_service import (
    clear_issue_notifications,
    clear_task_notifications,
    create_issue_created_notifications,
)

router = APIRouter()


def _clear_followup_task_notifications(
    db: DbConn,
    *,
    garden_id: int,
    followup_clears: list[tuple[str, str]],
    now_ms: int,
) -> None:
    for task_public_id, reason in followup_clears:
        clear_task_notifications(
            db,
            garden_id=garden_id,
            task_public_id=task_public_id,
            reason=f"issue_{reason}",
            now_ms=now_ms,
        )


IssueType = Literal[
    "pest",
    "disease",
    "fungal",
    "nutrient",
    "environmental",
    "damage",
    "other",
]

IssueStatus = Literal[
    "open",
    "monitoring",
    "treating",
    "resolved",
    "dismissed",
]

IssueSeverity = Literal[
    "low",
    "normal",
    "high",
    "critical",
]


class CreateIssueBody(StrictBaseModel):
    issue_type: IssueType
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    severity: IssueSeverity = "normal"
    suspected_cause: str = Field(default="", max_length=1000)
    treatment_plan: str = Field(default="", max_length=2000)
    follow_up_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    plant_ids: list[str] = Field(default_factory=list)
    plot_ids: list[str] = Field(default_factory=list)


class UpdateIssueBody(StrictBaseModel):
    issue_type: IssueType | None = None
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    severity: IssueSeverity | None = None
    status: IssueStatus | None = None
    suspected_cause: str | None = Field(default=None, max_length=1000)
    treatment_plan: str | None = Field(default=None, max_length=2000)
    follow_up_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
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


# ── Serialization ─────────────────────────────────────────────────


def _serialize_issue(row: dict, plant_ids: list[str], plot_ids: list[str]) -> dict:
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
        "issue_type": str(row["issue_type"]),
        "title": str(row["title"] or ""),
        "description": str(row["description"] or ""),
        "severity": str(row["severity"]),
        "status": str(row["status"]),
        "suspected_cause": str(row["suspected_cause"] or ""),
        "treatment_plan": str(row["treatment_plan"] or ""),
        "follow_up_on": str(row["follow_up_on"]) if row["follow_up_on"] else None,
        "metadata": metadata,
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] else None
        ),
        "resolved_by_user_id": (
            int(row["resolved_by_user_id"]) if row["resolved_by_user_id"] else None
        ),
        "resolved_at_ms": (int(row["resolved_at_ms"]) if row["resolved_at_ms"] else None),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "plant_ids": plant_ids,
        "plot_ids": plot_ids,
    }


def _normalize_issue_history(row: dict) -> list[dict]:
    metadata = _parse_metadata(row.get("metadata_json"))
    events: list[dict] = []
    raw_history = metadata.get("history")
    if isinstance(raw_history, list):
        for raw_event in raw_history:
            if not isinstance(raw_event, dict):
                continue
            kind = str(raw_event.get("kind") or "").strip()
            if kind not in {"created", "updated", "resolved", "dismissed", "reopened"}:
                continue
            at_ms = raw_event.get("at_ms")
            if not isinstance(at_ms, int):
                continue
            events.append(
                {
                    "kind": kind,
                    "at_ms": at_ms,
                    "actor_user_id": (
                        int(raw_event["actor_user_id"])
                        if raw_event.get("actor_user_id") is not None
                        else None
                    ),
                    "actor_username": (
                        str(raw_event["actor_username"])
                        if raw_event.get("actor_username")
                        else None
                    ),
                    "title": str(raw_event.get("title") or row.get("title") or ""),
                    "status": str(raw_event.get("status") or row.get("status") or "open"),
                    "severity": str(raw_event.get("severity") or row.get("severity") or "normal"),
                    "summary": str(raw_event.get("summary") or ""),
                }
            )

    if not any(event["kind"] == "created" for event in events):
        events.append(
            {
                "kind": "created",
                "at_ms": int(row["created_at_ms"]),
                "actor_user_id": (
                    int(row["created_by_user_id"]) if row.get("created_by_user_id") else None
                ),
                "actor_username": None,
                "title": str(row.get("title") or ""),
                "status": "open",
                "severity": str(row.get("severity") or "normal"),
                "summary": "",
            }
        )

    if row.get("resolved_at_ms") and not any(event["kind"] == "resolved" for event in events):
        events.append(
            {
                "kind": "resolved",
                "at_ms": int(row["resolved_at_ms"]),
                "actor_user_id": (
                    int(row["resolved_by_user_id"])
                    if row.get("resolved_by_user_id") is not None
                    else None
                ),
                "actor_username": None,
                "title": str(row.get("title") or ""),
                "status": "resolved",
                "severity": str(row.get("severity") or "normal"),
                "summary": "",
            }
        )

    events.sort(key=lambda item: int(item["at_ms"]), reverse=True)
    return events


def _append_issue_history_event(
    db: DbConn,
    issue_row: dict,
    *,
    kind: Literal["created", "updated", "resolved", "dismissed", "reopened"],
    actor_user_id: int | None,
    actor_username: str | None,
    at_ms: int,
    summary: str = "",
) -> None:
    metadata = _parse_metadata(issue_row.get("metadata_json"))
    raw_history = metadata.get("history")
    history = raw_history[:] if isinstance(raw_history, list) else []
    history.insert(
        0,
        {
            "kind": kind,
            "at_ms": at_ms,
            "actor_user_id": actor_user_id,
            "actor_username": actor_username,
            "title": str(issue_row.get("title") or ""),
            "status": str(issue_row.get("status") or "open"),
            "severity": str(issue_row.get("severity") or "normal"),
            "summary": summary,
        },
    )
    metadata["history"] = history
    db.execute(
        "UPDATE garden_issues SET metadata_json = %s WHERE id = %s",
        (_dump_metadata(metadata), int(issue_row["id"])),
    )


def _issue_journal_title(
    kind: Literal["created", "updated", "resolved", "dismissed", "reopened"],
    issue_row: dict,
) -> str:
    base = str(issue_row.get("title") or "").strip() or str(issue_row.get("issue_type") or "issue")
    if kind == "created":
        return f"Issue reported: {base}"
    if kind == "resolved":
        return f"Issue resolved: {base}"
    if kind == "dismissed":
        return f"Issue dismissed: {base}"
    if kind == "reopened":
        return f"Issue reopened: {base}"
    return f"Issue updated: {base}"


def _issue_journal_notes(
    kind: Literal["created", "updated", "resolved", "dismissed", "reopened"],
    issue_row: dict,
    summary: str,
) -> str:
    parts = [
        f"Type: {issue_row.get('issue_type') or 'other'}",
        f"Severity: {issue_row.get('severity') or 'normal'}",
        f"Status: {issue_row.get('status') or 'open'}",
    ]
    if summary:
        parts.append(summary)
    description = str(issue_row.get("description") or "").strip()
    if description and kind == "created":
        parts.append(description)
    return "\n".join(parts)


def _create_issue_journal_entry(
    db: DbConn,
    *,
    garden_id: int,
    issue_row: dict,
    actor_user_id: int | None,
    plant_ids: list[str],
    plot_ids: list[str],
    kind: Literal["created", "updated", "resolved", "dismissed", "reopened"],
    summary: str,
    at_ms: int,
) -> None:
    jrow = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, 'observed', %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            _generate_public_id("jrn"),
            garden_id,
            date.today().isoformat(),
            _issue_journal_title(kind, issue_row),
            _issue_journal_notes(kind, issue_row, summary),
            _dump_metadata(
                {
                    "issue_id": str(issue_row["public_id"]),
                    "issue_event": kind,
                    "issue_status": str(issue_row.get("status") or "open"),
                    "issue_severity": str(issue_row.get("severity") or "normal"),
                }
            ),
            actor_user_id,
            at_ms,
            at_ms,
        ),
    ).fetchone()
    assert jrow is not None
    entry_id = int(jrow["id"])
    for plant_id in plant_ids:
        db.execute(
            "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (entry_id, plant_id),
        )
    for plot_id in plot_ids:
        db.execute(
            "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            (entry_id, plot_id),
        )


def _load_related_journal_entries(
    db: DbConn,
    *,
    garden_id: int,
    plant_ids: list[str],
    plot_ids: list[str],
    limit: int,
) -> list[dict]:
    link_conditions: list[str] = []
    params: list[object] = [garden_id]

    if plant_ids:
        placeholders = ",".join(["%s"] * len(plant_ids))
        link_conditions.append(
            f"""
            e.id IN (
                SELECT entry_id
                FROM garden_journal_entry_plants
                WHERE plt_id IN ({placeholders})
            )
            """
        )
        params.extend(plant_ids)

    if plot_ids:
        placeholders = ",".join(["%s"] * len(plot_ids))
        link_conditions.append(
            f"""
            e.id IN (
                SELECT entry_id
                FROM garden_journal_entry_plots
                WHERE plot_id IN ({placeholders})
            )
            """
        )
        params.extend(plot_ids)

    if not link_conditions:
        return []

    rows = db.execute(
        f"""
        SELECT e.*, u.username AS actor_username
        FROM garden_journal_entries e
        LEFT JOIN auth_users u ON u.id = e.actor_user_id
        WHERE e.garden_id = %s
          AND ({" OR ".join(link_conditions)})
        ORDER BY e.occurred_on DESC, e.created_at_ms DESC
        LIMIT %s
        """,
        [*params, limit],
    ).fetchall()
    entry_ids = [int(row["id"]) for row in rows]
    if not entry_ids:
        return []

    placeholders = ",".join(["%s"] * len(entry_ids))
    plant_map: dict[int, list[str]] = {entry_id: [] for entry_id in entry_ids}
    for row in db.execute(
        "SELECT entry_id, plt_id"
        " FROM garden_journal_entry_plants"
        f" WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall():
        plant_map[int(row["entry_id"])].append(str(row["plt_id"]))

    plot_map: dict[int, list[str]] = {entry_id: [] for entry_id in entry_ids}
    for row in db.execute(
        "SELECT entry_id, plot_id"
        " FROM garden_journal_entry_plots"
        f" WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall():
        plot_map[int(row["entry_id"])].append(str(row["plot_id"]))

    entries: list[dict] = []
    for row in rows:
        entries.append(
            {
                "id": str(row["public_id"]),
                "garden_id": int(row["garden_id"]),
                "event_type": str(row["event_type"]),
                "occurred_on": str(row["occurred_on"]),
                "title": str(row["title"] or ""),
                "notes": str(row["notes"] or ""),
                "metadata": _parse_metadata(row["metadata_json"]),
                "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] else None,
                "actor_username": (str(row["actor_username"]) if row["actor_username"] else None),
                "created_at_ms": int(row["created_at_ms"]),
                "updated_at_ms": int(row["updated_at_ms"]),
                "plant_ids": plant_map.get(int(row["id"]), []),
                "plot_ids": plot_map.get(int(row["id"]), []),
            }
        )
    return entries


def _build_update_summary(
    previous_row: dict,
    updates: dict,
    previous_plant_ids: list[str],
    previous_plot_ids: list[str],
    current_plant_ids: list[str],
    current_plot_ids: list[str],
) -> str:
    changes: list[str] = []
    if "issue_type" in updates and updates["issue_type"] != previous_row.get("issue_type"):
        changes.append(
            f"Type {previous_row.get('issue_type') or 'other'} -> {updates['issue_type']}"
        )
    if "title" in updates and (updates["title"] or "") != (previous_row.get("title") or ""):
        changes.append("Updated title")
    if "description" in updates and (updates["description"] or "") != (
        previous_row.get("description") or ""
    ):
        changes.append("Updated description")
    if "severity" in updates and updates["severity"] != previous_row.get("severity"):
        changes.append(
            f"Severity {previous_row.get('severity') or 'normal'} -> {updates['severity']}"
        )
    if "status" in updates and updates["status"] != previous_row.get("status"):
        changes.append(f"Status {previous_row.get('status') or 'open'} -> {updates['status']}")
    if "suspected_cause" in updates and (updates["suspected_cause"] or "") != (
        previous_row.get("suspected_cause") or ""
    ):
        changes.append("Updated suspected cause")
    if "treatment_plan" in updates and (updates["treatment_plan"] or "") != (
        previous_row.get("treatment_plan") or ""
    ):
        changes.append("Updated treatment plan")
    if "follow_up_on" in updates and updates["follow_up_on"] != previous_row.get("follow_up_on"):
        changes.append("Updated follow-up date")
    if sorted(previous_plant_ids) != sorted(current_plant_ids):
        changes.append("Updated linked plants")
    if sorted(previous_plot_ids) != sorted(current_plot_ids):
        changes.append("Updated linked plots")
    return "; ".join(changes)


# ── Link management ──────────────────────────────────────────────


def _load_issue_links(
    db: DbConn, issue_ids: list[int]
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    if not issue_ids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(issue_ids))
    plant_map: dict[int, list[str]] = {iid: [] for iid in issue_ids}
    for r in db.execute(
        f"SELECT issue_id, plt_id FROM garden_issue_plants WHERE issue_id IN ({placeholders})",
        issue_ids,
    ).fetchall():
        plant_map[int(r["issue_id"])].append(str(r["plt_id"]))
    plot_map: dict[int, list[str]] = {iid: [] for iid in issue_ids}
    for r in db.execute(
        f"SELECT issue_id, plot_id FROM garden_issue_plots WHERE issue_id IN ({placeholders})",
        issue_ids,
    ).fetchall():
        plot_map[int(r["issue_id"])].append(str(r["plot_id"]))
    return plant_map, plot_map


def _set_issue_links(
    db: DbConn,
    context: AuthContext,
    issue_id: int,
    plant_ids: list[str],
    plot_ids: list[str],
) -> None:
    valid_plant_ids = _validate_plant_ids(db, context, plant_ids)
    valid_plot_ids = _validate_plot_ids(db, context, plot_ids)
    db.execute(
        "DELETE FROM garden_issue_plants WHERE issue_id = %s",
        (issue_id,),
    )
    db.execute(
        "DELETE FROM garden_issue_plots WHERE issue_id = %s",
        (issue_id,),
    )
    executemany(
        db,
        "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
        [(issue_id, pid) for pid in valid_plant_ids],
    )
    executemany(
        db,
        "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
        [(issue_id, plot_id) for plot_id in valid_plot_ids],
    )


def _fetch_issue(db: DbConn, issue_id: str, garden_id: int) -> dict:
    row = db.execute(
        """
        SELECT *
        FROM garden_issues
        WHERE public_id = %s AND garden_id = %s
        """,
        (issue_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Issue not found")
    return dict(row)


def _issue_create_response(
    db: DbConn,
    *,
    garden_id: int,
    target_id: str,
) -> dict:
    row = db.execute(
        """
        SELECT public_id
        FROM garden_issues
        WHERE public_id = %s AND garden_id = %s
        """,
        (target_id, garden_id),
    ).fetchone()
    if not row:
        raise_operation_target_gone()
    return {"status": "ok", "id": str(row["public_id"])}


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/issues/summary")
def issue_summary(request: Request, db: DB) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    rows = db.execute(
        """
        SELECT status, COUNT(*) AS c
        FROM garden_issues
        WHERE garden_id = %s
        GROUP BY status
        """,
        (garden_id,),
    ).fetchall()
    counts: dict[str, int] = {
        "open": 0,
        "monitoring": 0,
        "treating": 0,
        "resolved": 0,
        "dismissed": 0,
    }
    total = 0
    for r in rows:
        s = str(r["status"])
        c = int(r["c"])
        if s in counts:
            counts[s] = c
        total += c
    counts["total"] = total
    return counts


@router.get("/issues")
def list_issues(
    request: Request,
    db: DB,
    status: str | None = Query(default=None),
    issue_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    plant_id: str | None = Query(default=None),
    plot_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["i.garden_id = %s"]
    params: list = [garden_id]

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            ph = ",".join(["%s"] * len(statuses))
            conditions.append(f"i.status IN ({ph})")
            params.extend(statuses)

    if issue_type:
        types = [t.strip() for t in issue_type.split(",") if t.strip()]
        if types:
            ph = ",".join(["%s"] * len(types))
            conditions.append(f"i.issue_type IN ({ph})")
            params.extend(types)

    if severity:
        severities = [s.strip() for s in severity.split(",") if s.strip()]
        if severities:
            ph = ",".join(["%s"] * len(severities))
            conditions.append(f"i.severity IN ({ph})")
            params.extend(severities)

    if plant_id:
        conditions.append("i.id IN (SELECT issue_id FROM garden_issue_plants WHERE plt_id = %s)")
        params.append(plant_id)

    if plot_id:
        conditions.append("i.id IN (SELECT issue_id FROM garden_issue_plots WHERE plot_id = %s)")
        params.append(plot_id)

    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            """
            (
                i.title ILIKE %s
                OR i.description ILIKE %s
                OR i.suspected_cause ILIKE %s
                OR i.treatment_plan ILIKE %s
            )
            """
        )
        params.extend([like, like, like, like])

    where = " AND ".join(conditions)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM garden_issues i
        WHERE {where}
        """,
        params,
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = db.execute(
        f"""
        SELECT i.*
        FROM garden_issues i
        WHERE {where}
        ORDER BY i.created_at_ms DESC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()

    issue_ids = [int(r["id"]) for r in rows]
    plant_map, plot_map = _load_issue_links(db, issue_ids)

    issues = [
        _serialize_issue(
            dict(r),
            plant_map.get(int(r["id"]), []),
            plot_map.get(int(r["id"]), []),
        )
        for r in rows
    ]
    return {"issues": issues, "total": total}


@router.get("/issues/{issue_id}/history")
def get_issue_history(
    request: Request,
    db: DB,
    issue_id: str,
    limit: int = Query(default=12, ge=1, le=50),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_issue(db, issue_id, garden_id)
    internal_id = int(row["id"])
    plant_map, plot_map = _load_issue_links(db, [internal_id])
    plant_ids = plant_map.get(internal_id, [])
    plot_ids = plot_map.get(internal_id, [])
    return {
        "issue_events": _normalize_issue_history(row),
        "journal_entries": _load_related_journal_entries(
            db,
            garden_id=garden_id,
            plant_ids=plant_ids,
            plot_ids=plot_ids,
            limit=limit,
        ),
    }


@router.get("/issues/{issue_id}")
def get_issue(request: Request, db: DB, issue_id: str) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_issue(db, issue_id, garden_id)
    internal_id = int(row["id"])
    plant_map, plot_map = _load_issue_links(db, [internal_id])
    return _serialize_issue(
        row,
        plant_map.get(internal_id, []),
        plot_map.get(internal_id, []),
    )


@router.post("/issues", status_code=201)
def create_issue(
    request: Request,
    db: DB,
    body: CreateIssueBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    prepared_operation = prepare_operation(
        db,
        request=request,
        garden_id=garden_id,
        endpoint=ISSUES_ENDPOINT,
        request_payload=body.model_dump(mode="json"),
        now_ms=current_timestamp_ms(),
    )
    if prepared_operation.replay_target_id is not None:
        return _issue_create_response(
            db,
            garden_id=garden_id,
            target_id=prepared_operation.replay_target_id,
        )

    if body.follow_up_on:
        _validate_date(body.follow_up_on)

    now_ms = current_timestamp_ms()
    issue_public_id = _generate_public_id("iss")
    if prepared_operation.operation is not None:
        reservation = reserve_operation(
            db,
            operation=prepared_operation.operation,
            target_type=ISSUE_TARGET,
            target_id=issue_public_id,
            created_at_ms=now_ms,
        )
        if not reservation.is_owner:
            return _issue_create_response(
                db,
                garden_id=garden_id,
                target_id=reservation.result_id,
            )

    row = db.execute(
        """
        INSERT INTO garden_issues
            (public_id, garden_id, issue_type, title, description, severity, status,
             suspected_cause, treatment_plan, follow_up_on,
             metadata_json, created_by_user_id,
             created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            issue_public_id,
            garden_id,
            body.issue_type,
            body.title,
            body.description,
            body.severity,
            body.suspected_cause,
            body.treatment_plan,
            body.follow_up_on,
            _dump_metadata({}),
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    issue_id = int(row["id"])
    issue_public_id = str(row["public_id"])
    _set_issue_links(db, context, issue_id, body.plant_ids, body.plot_ids)
    issue_row = _fetch_issue(db, issue_public_id, garden_id)
    plant_map, plot_map = _load_issue_links(db, [issue_id])
    plant_ids = plant_map.get(issue_id, [])
    plot_ids = plot_map.get(issue_id, [])
    _append_issue_history_event(
        db,
        issue_row,
        kind="created",
        actor_user_id=context.user_id,
        actor_username=context.username,
        at_ms=now_ms,
        summary="Issue reported",
    )
    _create_issue_journal_entry(
        db,
        garden_id=garden_id,
        issue_row=issue_row,
        actor_user_id=context.user_id,
        plant_ids=plant_ids,
        plot_ids=plot_ids,
        kind="created",
        summary="Issue reported",
        at_ms=now_ms,
    )
    on_issue_created(db, garden_id, issue_id, context.user_id)
    create_issue_created_notifications(
        db,
        garden_id=garden_id,
        issue_public_id=issue_public_id,
        title=str(issue_row.get("title") or "Issue reported"),
        body=str(issue_row.get("description") or ""),
        severity=str(issue_row.get("severity") or "normal"),
        actor_user_id=context.user_id,
    )
    db.commit()
    return {"status": "ok", "id": issue_public_id}


@router.patch("/issues/{issue_id}")
def update_issue(
    request: Request,
    db: DB,
    issue_id: str,
    body: UpdateIssueBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    previous_row = _fetch_issue(db, issue_id, garden_id)
    internal_id = int(previous_row["id"])
    previous_plant_map, previous_plot_map = _load_issue_links(db, [internal_id])
    previous_plant_ids = previous_plant_map.get(internal_id, [])
    previous_plot_ids = previous_plot_map.get(internal_id, [])

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    if "follow_up_on" in updates and updates["follow_up_on"] is not None:
        _validate_date(updates["follow_up_on"])

    now_ms = current_timestamp_ms()
    status_update = str(updates["status"]) if "status" in updates and updates["status"] else None
    previous_status = str(previous_row.get("status") or "open")

    set_clauses: list[str] = []
    params: list = []
    for field in (
        "issue_type",
        "title",
        "description",
        "severity",
        "status",
        "suspected_cause",
        "treatment_plan",
        "follow_up_on",
    ):
        if field in updates:
            set_clauses.append(f"{field} = %s")
            params.append(updates[field])

    if status_update in {"resolved", "dismissed"} and status_update != previous_status:
        set_clauses.append("resolved_by_user_id = %s")
        params.append(context.user_id)
        set_clauses.append("resolved_at_ms = %s")
        params.append(now_ms)
    elif status_update in {"open", "monitoring", "treating"} and previous_status in {
        "resolved",
        "dismissed",
    }:
        set_clauses.append("resolved_by_user_id = NULL")
        set_clauses.append("resolved_at_ms = NULL")

    set_clauses.append("updated_at_ms = %s")
    params.append(now_ms)
    params.append(internal_id)

    db.execute(
        f"UPDATE garden_issues SET {', '.join(set_clauses)} WHERE id = %s",
        params,
    )

    if "plant_ids" in updates:
        plant_ids = updates["plant_ids"] if updates["plant_ids"] is not None else []
        plot_ids_val: list[str] = []
        if "plot_ids" in updates:
            plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        else:
            existing_plots = db.execute(
                "SELECT plot_id FROM garden_issue_plots WHERE issue_id = %s",
                (internal_id,),
            ).fetchall()
            plot_ids_val = [str(r["plot_id"]) for r in existing_plots]
        _set_issue_links(db, context, internal_id, plant_ids, plot_ids_val)
    elif "plot_ids" in updates:
        plot_ids_val = updates["plot_ids"] if updates["plot_ids"] is not None else []
        existing_plants = db.execute(
            "SELECT plt_id FROM garden_issue_plants WHERE issue_id = %s",
            (internal_id,),
        ).fetchall()
        plant_ids_current = [str(r["plt_id"]) for r in existing_plants]
        _set_issue_links(db, context, internal_id, plant_ids_current, plot_ids_val)

    current_row = _fetch_issue(db, issue_id, garden_id)
    current_plant_map, current_plot_map = _load_issue_links(db, [internal_id])
    current_plant_ids = current_plant_map.get(internal_id, [])
    current_plot_ids = current_plot_map.get(internal_id, [])
    summary = _build_update_summary(
        previous_row,
        updates,
        previous_plant_ids,
        previous_plot_ids,
        current_plant_ids,
        current_plot_ids,
    )
    if summary:
        now_ms = int(current_row["updated_at_ms"])
        history_kind: Literal["updated", "resolved", "dismissed", "reopened"] = "updated"
        if status_update == "resolved" and previous_status != "resolved":
            history_kind = "resolved"
        elif status_update == "dismissed" and previous_status != "dismissed":
            history_kind = "dismissed"
        elif status_update in {"open", "monitoring", "treating"} and previous_status in {
            "resolved",
            "dismissed",
        }:
            history_kind = "reopened"
        _append_issue_history_event(
            db,
            current_row,
            kind=history_kind,
            actor_user_id=context.user_id,
            actor_username=context.username,
            at_ms=now_ms,
            summary=summary,
        )
        _create_issue_journal_entry(
            db,
            garden_id=garden_id,
            issue_row=current_row,
            actor_user_id=context.user_id,
            plant_ids=current_plant_ids,
            plot_ids=current_plot_ids,
            kind=history_kind,
            summary=summary,
            at_ms=now_ms,
        )

    if updates.get("status") in {"resolved", "dismissed"}:
        clear_issue_notifications(
            db,
            garden_id=garden_id,
            issue_public_id=str(current_row["public_id"]),
            reason=str(updates["status"]),
            now_ms=int(current_row["updated_at_ms"]),
        )
    if any(
        key in updates
        for key in ("follow_up_on", "status", "title", "severity", "plant_ids", "plot_ids")
    ):
        followup_clears = sync_issue_followup_task(
            db,
            garden_id,
            internal_id,
            context.user_id,
            now_ms=int(current_row["updated_at_ms"]),
        )
        _clear_followup_task_notifications(
            db,
            garden_id=garden_id,
            followup_clears=followup_clears,
            now_ms=int(current_row["updated_at_ms"]),
        )

    db.commit()
    return {"status": "ok"}


@router.post("/issues/{issue_id}/resolve")
def resolve_issue(
    request: Request,
    db: DB,
    issue_id: str,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    existing_row = _fetch_issue(db, issue_id, garden_id)
    internal_id = int(existing_row["id"])

    now_ms = current_timestamp_ms()
    db.execute(
        """
        UPDATE garden_issues
        SET status = 'resolved',
            resolved_by_user_id = %s,
            resolved_at_ms = %s,
            updated_at_ms = %s
        WHERE id = %s
        """,
        (context.user_id, now_ms, now_ms, internal_id),
    )
    current_row = _fetch_issue(db, issue_id, garden_id)
    plant_map, plot_map = _load_issue_links(db, [internal_id])
    plant_ids = plant_map.get(internal_id, [])
    plot_ids = plot_map.get(internal_id, [])
    _append_issue_history_event(
        db,
        current_row,
        kind="resolved",
        actor_user_id=context.user_id,
        actor_username=context.username,
        at_ms=now_ms,
        summary="Issue marked as resolved",
    )
    _create_issue_journal_entry(
        db,
        garden_id=garden_id,
        issue_row=current_row,
        actor_user_id=context.user_id,
        plant_ids=plant_ids,
        plot_ids=plot_ids,
        kind="resolved",
        summary="Issue marked as resolved",
        at_ms=now_ms,
    )
    clear_issue_notifications(
        db,
        garden_id=garden_id,
        issue_public_id=str(current_row["public_id"]),
        reason="resolved",
        now_ms=now_ms,
    )
    followup_clears = sync_issue_followup_task(
        db,
        garden_id,
        internal_id,
        context.user_id,
        now_ms=now_ms,
    )
    _clear_followup_task_notifications(
        db,
        garden_id=garden_id,
        followup_clears=followup_clears,
        now_ms=now_ms,
    )
    db.commit()
    return {"status": "ok"}


@router.delete("/issues/{issue_id}")
def delete_issue(request: Request, db: DB, issue_id: str) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    existing_row = _fetch_issue(db, issue_id, garden_id)
    internal_id = int(existing_row["id"])
    public_id = str(existing_row["public_id"])
    media_storage_pairs = collect_media_cleanup_for_target(
        db,
        garden_id=garden_id,
        target_type="issue",
        target_id=public_id,
    )
    db.execute("DELETE FROM garden_issues WHERE id = %s", (internal_id,))
    db.commit()
    if media_storage_pairs:
        for storage_key, preview_storage_key in media_storage_pairs:
            unlink_storage_keys(storage_key, preview_storage_key)
    return {"status": "ok"}
