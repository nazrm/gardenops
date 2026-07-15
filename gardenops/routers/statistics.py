from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from gardenops.db import DB, current_timestamp_ms
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.security import has_write_access
from gardenops.services.gardener_reports import get_gardener_reports
from gardenops.services.notification_service import (
    clear_expired_notifications,
    clear_stale_informational_notifications,
    clear_stale_task_notifications,
    get_unread_count,
    notification_request_clock,
)
from gardenops.sql_dates import offset_months_iso

router = APIRouter()
TODAY_DASHBOARD_LIST_LIMIT = 25

_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "januar": 1,
    "feb": 2,
    "februar": 2,
    "mar": 3,
    "mars": 3,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "may": 5,
    "jun": 6,
    "juni": 6,
    "jul": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "okt": 10,
    "oktober": 10,
    "oct": 10,
    "nov": 11,
    "november": 11,
    "des": 12,
    "desember": 12,
    "dec": 12,
}


def _parse_month(s: str) -> int:
    t = s.strip().lower()
    if t.isdigit():
        n = int(t)
        return n if 1 <= n <= 12 else 0
    return _MONTH_NAMES.get(t, 0)


def _bloom_months(raw: str) -> set[int]:
    if not raw:
        return set()
    parts = re.split(r"[-–,]", raw)
    months = [_parse_month(p) for p in parts]
    months = [m for m in months if m]
    if len(months) == 2 and months[0] <= months[1]:
        return set(range(months[0], months[1] + 1))
    return set(months)


@router.get("/statistics/actions")
def get_statistics_actions(db: DB, request: Request) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    stale_cutoff = offset_months_iso(-12)

    # 1. Plants without plot assignments
    unassigned = db.execute(
        """
        SELECT p.plt_id, p.name
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s
          AND NOT EXISTS (
              SELECT 1
              FROM plot_plants pp
              JOIN plot_ownership assignment_po ON assignment_po.plot_id = pp.plot_id
              WHERE pp.plt_id = p.plt_id
                AND assignment_po.garden_id = %s
          )
        ORDER BY p.name
        LIMIT 50
        """,
        (garden_id, garden_id),
    ).fetchall()
    unassigned_plants = [{"plt_id": r["plt_id"], "name": r["name"]} for r in unassigned]

    # 2. Empty plots by zone
    empty_rows = db.execute(
        """
        SELECT pl.plot_id, pl.zone_code
        FROM plots pl
        JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
        WHERE pwo.garden_id = %s
          AND NOT EXISTS (
              SELECT 1
              FROM plot_plants pp
              JOIN plant_ownership assignment_po ON assignment_po.plt_id = pp.plt_id
              WHERE pp.plot_id = pl.plot_id
                AND assignment_po.garden_id = %s
          )
        ORDER BY pl.zone_code, pl.plot_id
        """,
        (garden_id, garden_id),
    ).fetchall()
    zone_empties: dict[str, list[str]] = {}
    for r in empty_rows:
        zone_empties.setdefault(r["zone_code"], []).append(r["plot_id"])
    empty_plots_by_zone = [
        {"zone_code": z, "plot_ids": pids, "count": len(pids)}
        for z, pids in sorted(zone_empties.items())
    ]

    # 3. Bloom gap months
    bloom_rows = db.execute(
        """
        SELECT p.bloom_month
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s AND p.bloom_month != ''
        """,
        (garden_id,),
    ).fetchall()
    covered_months: set[int] = set()
    for r in bloom_rows:
        covered_months |= _bloom_months(r["bloom_month"])
    bloom_gap_months = sorted(m for m in range(1, 13) if m not in covered_months)

    # 4. Plants with no planting year
    no_year = db.execute(
        """
        SELECT p.plt_id, p.name
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s
          AND (p.year_planted IS NULL OR p.year_planted = '')
        ORDER BY p.name
        LIMIT 50
        """,
        (garden_id,),
    ).fetchall()
    no_year_plants = [{"plt_id": r["plt_id"], "name": r["name"]} for r in no_year]

    # 5. Stale plants (no journal activity in 12 months)
    stale = db.execute(
        """
        SELECT p.plt_id, p.name
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        LEFT JOIN garden_journal_entry_plants jep ON jep.plt_id = p.plt_id
        LEFT JOIN garden_journal_entries je
            ON je.id = jep.entry_id
            AND je.garden_id = %s
            AND je.event_type IN ('observed', 'bloomed')
            AND je.occurred_on >= %s
        WHERE po.garden_id = %s AND je.id IS NULL
        ORDER BY p.name
        LIMIT 50
        """,
        (garden_id, stale_cutoff, garden_id),
    ).fetchall()
    stale_plants = [{"plt_id": r["plt_id"], "name": r["name"]} for r in stale]

    # 6. Plants missing care info (light or hardiness)
    missing_care = db.execute(
        """
        SELECT p.plt_id, p.name, p.light, p.hardiness
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s
          AND (
            (p.light IS NULL OR p.light = '')
            OR (p.hardiness IS NULL OR p.hardiness = '')
          )
        ORDER BY p.name
        LIMIT 50
        """,
        (garden_id,),
    ).fetchall()
    missing_care_plants = []
    for r in missing_care:
        missing: list[str] = []
        if not r["light"]:
            missing.append("light")
        if not r["hardiness"]:
            missing.append("hardiness")
        missing_care_plants.append(
            {
                "plt_id": r["plt_id"],
                "name": r["name"],
                "missing": missing,
            }
        )

    return {
        "unassigned_plants": unassigned_plants,
        "empty_plots_by_zone": empty_plots_by_zone,
        "bloom_gap_months": bloom_gap_months,
        "no_year_plants": no_year_plants,
        "stale_plants": stale_plants,
        "missing_care_plants": missing_care_plants,
    }


@router.get("/statistics/automation-status")
def get_automation_status(request: Request, db: DB) -> dict:
    """Show what automation rules have fired recently."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    auto_tasks = db.execute(
        """SELECT rule_source, task_type, title, status, created_at_ms
           FROM garden_tasks
           WHERE garden_id = %s AND rule_source LIKE 'auto:%%'
           ORDER BY created_at_ms DESC
           LIMIT 50""",
        (garden_id,),
    ).fetchall()

    return {
        "automated_tasks": [
            {
                "rule_source": str(r["rule_source"]),
                "task_type": str(r["task_type"]),
                "title": str(r["title"]),
                "status": str(r["status"]),
                "created_at_ms": int(r["created_at_ms"]),
            }
            for r in auto_tasks
        ],
        "total": len(auto_tasks),
    }


@router.get("/statistics/reports")
def get_statistics_reports(
    db: DB,
    request: Request,
    zone_code: str | None = Query(default=None),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    try:
        return get_gardener_reports(db, garden_id=garden_id, zone_code=zone_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/exports/backup")
def export_backup(request: Request, db: DB) -> dict:
    """Internal, nonportable garden snapshot. No restore contract is provided."""
    context = _auth_context(request)
    if not has_write_access(context):
        raise HTTPException(status_code=403, detail="Write access required")
    garden_id = _active_garden_id(context)

    tables = {
        "tasks": "SELECT * FROM garden_tasks WHERE garden_id = %s",
        "journal": ("SELECT * FROM garden_journal_entries WHERE garden_id = %s"),
        "issues": "SELECT * FROM garden_issues WHERE garden_id = %s",
        "harvest": ("SELECT * FROM harvest_entries WHERE garden_id = %s"),
        "inventory": ("SELECT * FROM inventory_items WHERE garden_id = %s"),
        "inventory_transactions": (
            "SELECT t.*, i.public_id AS item_public_id, j.public_id AS journal_entry_public_id "
            "FROM inventory_transactions t "
            "JOIN inventory_items i ON i.id = t.item_id "
            "LEFT JOIN garden_journal_entries j ON j.id = t.journal_entry_id "
            "WHERE i.garden_id = %s"
        ),
        "procurement": ("SELECT * FROM procurement_items WHERE garden_id = %s"),
    }

    backup: dict = {
        "backup_contract": {
            "format": "gardenops-internal-snapshot",
            "portable": False,
            "restore_supported": False,
        },
        "garden_id": garden_id,
        "exported_at_ms": current_timestamp_ms(),
    }
    for key, query in tables.items():
        rows = db.execute(query, (garden_id,)).fetchall()
        serialized_rows: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if key in {"tasks", "journal", "issues", "harvest", "inventory", "procurement"}:
                item["id"] = str(item["public_id"])
                item.pop("public_id", None)
            elif key == "inventory_transactions":
                item["item_id"] = str(item["item_public_id"])
                item.pop("item_public_id", None)
                item["journal_entry_id"] = (
                    str(item["journal_entry_public_id"])
                    if item.get("journal_entry_public_id")
                    else None
                )
                item.pop("journal_entry_public_id", None)
            serialized_rows.append(item)
        backup[key] = serialized_rows

    return backup


@router.get("/dashboard/badge-counts")
def get_badge_counts(db: DB, request: Request) -> dict:
    """Lightweight counts for tab badge indicators."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    now_ms, frozen_date = notification_request_clock()
    today_iso = frozen_date or date.today().isoformat()
    actionable_task_clause = (
        "(status = 'pending' OR "
        "(status = 'snoozed' AND snoozed_until IS NOT NULL AND snoozed_until <= %s))"
    )

    overdue_row = db.execute(
        "SELECT COUNT(*) AS c FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) < %s",
        (garden_id, today_iso, today_iso),
    ).fetchone()
    assert overdue_row is not None
    overdue = overdue_row["c"]

    open_issues_row = db.execute(
        "SELECT COUNT(*) AS c FROM garden_issues "
        "WHERE garden_id = %s "
        "AND status IN ('open', 'monitoring', 'treating')",
        (garden_id,),
    ).fetchone()
    assert open_issues_row is not None
    open_issues = open_issues_row["c"]

    active_alerts_row = db.execute(
        "SELECT COUNT(*) AS c FROM weather_alerts "
        "WHERE garden_id = %s AND dismissed = 0 "
        "AND valid_until >= %s",
        (garden_id, today_iso),
    ).fetchone()
    assert active_alerts_row is not None
    active_alerts = active_alerts_row["c"]
    expired = clear_expired_notifications(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        now_ms=now_ms,
    )
    stale_tasks = clear_stale_task_notifications(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        today_iso=today_iso,
        now_ms=now_ms,
    )
    stale_info = clear_stale_informational_notifications(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        today_iso=today_iso,
        now_ms=now_ms,
    )
    if expired or stale_tasks or stale_info:
        db.commit()
    unread_notifications = get_unread_count(db, garden_id, context.user_id)

    return {
        "overdue_tasks": overdue,
        "open_issues": open_issues,
        "active_alerts": active_alerts,
        "unread_notifications": unread_notifications,
    }


def _extract_today_forecast(
    weather_row: dict[str, Any] | None,
    today_iso: str,
) -> dict | None:
    """Pull today's forecast entry from cached forecast JSON."""
    if not weather_row or not weather_row["forecast_json"]:
        return None
    try:
        data = json.loads(weather_row["forecast_json"])
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        return None
    days = data.get("days") or data.get("forecast", {}).get("days", [])
    for day in days:
        if day.get("date") == today_iso:
            return day
    return days[0] if days else None


@router.get("/dashboard/today")
def get_today_dashboard(db: DB, request: Request) -> dict:
    """Aggregated daily summary: tasks, issues, weather."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    today_iso = date.today().isoformat()
    upcoming_limit = (date.today() + timedelta(days=3)).isoformat()
    actionable_task_clause = (
        "(status = 'pending' OR "
        "(status = 'snoozed' AND snoozed_until IS NOT NULL AND snoozed_until <= %s))"
    )

    due_today_total_row = db.execute(
        "SELECT COUNT(*) AS c "
        "FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) = %s",
        (garden_id, today_iso, today_iso),
    ).fetchone()
    due_today_total = int(due_today_total_row["c"] or 0) if due_today_total_row else 0
    due_today = db.execute(
        "SELECT public_id AS id, task_type, title, severity, "
        "COALESCE(snoozed_until, due_on) AS due_on "
        "FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) = %s "
        "ORDER BY CASE severity "
        "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "  WHEN 'normal' THEN 2 ELSE 3 END, updated_at_ms DESC "
        "LIMIT %s",
        (garden_id, today_iso, today_iso, TODAY_DASHBOARD_LIST_LIMIT),
    ).fetchall()

    overdue_total_row = db.execute(
        "SELECT COUNT(*) AS c "
        "FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) < %s",
        (garden_id, today_iso, today_iso),
    ).fetchone()
    overdue_total = int(overdue_total_row["c"] or 0) if overdue_total_row else 0
    overdue = db.execute(
        "SELECT public_id AS id, task_type, title, severity, "
        "COALESCE(snoozed_until, due_on) AS due_on "
        "FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) < %s "
        "ORDER BY COALESCE(snoozed_until, due_on) ASC, CASE severity "
        "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "  WHEN 'normal' THEN 2 ELSE 3 END, updated_at_ms DESC "
        "LIMIT %s",
        (garden_id, today_iso, today_iso, TODAY_DASHBOARD_LIST_LIMIT),
    ).fetchall()

    upcoming_total_row = db.execute(
        "SELECT COUNT(*) AS c "
        "FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) > %s "
        "AND COALESCE(snoozed_until, due_on) <= %s",
        (garden_id, today_iso, today_iso, upcoming_limit),
    ).fetchone()
    upcoming_total = int(upcoming_total_row["c"] or 0) if upcoming_total_row else 0
    upcoming = db.execute(
        "SELECT public_id AS id, task_type, title, severity, "
        "COALESCE(snoozed_until, due_on) AS due_on "
        "FROM garden_tasks "
        "WHERE garden_id = %s AND "
        f"{actionable_task_clause} "
        "AND COALESCE(snoozed_until, due_on) > %s "
        "AND COALESCE(snoozed_until, due_on) <= %s "
        "ORDER BY COALESCE(snoozed_until, due_on) ASC, CASE severity "
        "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "  WHEN 'normal' THEN 2 ELSE 3 END, updated_at_ms DESC "
        "LIMIT %s",
        (garden_id, today_iso, today_iso, upcoming_limit, TODAY_DASHBOARD_LIST_LIMIT),
    ).fetchall()

    issues_total_row = db.execute(
        "SELECT COUNT(*) AS c "
        "FROM garden_issues "
        "WHERE garden_id = %s "
        "AND status IN ('open', 'monitoring', 'treating')",
        (garden_id,),
    ).fetchone()
    issues_total = int(issues_total_row["c"] or 0) if issues_total_row else 0
    issues = db.execute(
        "SELECT public_id AS id, issue_type, title, severity, status "
        "FROM garden_issues "
        "WHERE garden_id = %s "
        "AND status IN ('open', 'monitoring', 'treating') "
        "ORDER BY CASE severity "
        "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "  WHEN 'normal' THEN 2 ELSE 3 END "
        "LIMIT 5",
        (garden_id,),
    ).fetchall()

    weather_row = db.execute(
        "SELECT forecast_json FROM weather_cache "
        "WHERE garden_id = %s ORDER BY fetched_at_ms DESC LIMIT 1",
        (garden_id,),
    ).fetchone()

    active_alerts_total_row = db.execute(
        "SELECT COUNT(*) AS c "
        "FROM weather_alerts "
        "WHERE garden_id = %s AND dismissed = 0 "
        "AND valid_until >= %s",
        (garden_id, today_iso),
    ).fetchone()
    active_alerts_total = int(active_alerts_total_row["c"] or 0) if active_alerts_total_row else 0
    active_alerts = db.execute(
        "SELECT id, alert_type, severity, title "
        "FROM weather_alerts "
        "WHERE garden_id = %s AND dismissed = 0 "
        "AND valid_until >= %s "
        "ORDER BY CASE severity "
        "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "  WHEN 'normal' THEN 2 ELSE 3 END, created_at_ms DESC "
        "LIMIT %s",
        (garden_id, today_iso, TODAY_DASHBOARD_LIST_LIMIT),
    ).fetchall()

    return {
        "date": today_iso,
        "tasks_due_today": [dict(r) for r in due_today],
        "tasks_due_today_total": due_today_total,
        "tasks_overdue": [dict(r) for r in overdue],
        "tasks_overdue_total": overdue_total,
        "tasks_upcoming": [dict(r) for r in upcoming],
        "tasks_upcoming_total": upcoming_total,
        "active_issues": [dict(r) for r in issues],
        "active_issues_total": issues_total,
        "weather_alerts": [dict(r) for r in active_alerts],
        "weather_alerts_total": active_alerts_total,
        "forecast_today": _extract_today_forecast(
            weather_row,
            today_iso,
        ),
    }
