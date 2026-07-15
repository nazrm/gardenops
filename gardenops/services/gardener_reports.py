"""Smart gardener reports for the Statistics dashboard."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import cast

from gardenops.db import DbConn
from gardenops.sql_dates import month_number_sql, offset_days_iso, offset_months_iso

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

_PREVIEW_LIMIT = 8
_OBSERVATION_STALE_MONTHS = 12


@dataclass(frozen=True)
class ReportScope:
    zone_code: str | None
    zone_name: str | None
    available_zones: list[dict[str, object]]
    plot_rows: list[dict[str, object]]
    plot_ids: list[str]
    plant_rows: list[dict[str, object]]
    plant_ids: list[str]


def _parse_month(value: str) -> int:
    raw = value.strip().lower()
    if raw.isdigit():
        month = int(raw)
        return month if 1 <= month <= 12 else 0
    return _MONTH_NAMES.get(raw, 0)


def _bloom_months(raw: str) -> set[int]:
    if not raw:
        return set()
    parts = re.split(r"[-–,]", raw)
    months = [_parse_month(part) for part in parts]
    months = [month for month in months if month]
    if len(months) == 2 and months[0] <= months[1]:
        return set(range(months[0], months[1] + 1))
    return set(months)


def _normalize_zone_code(zone_code: str | None) -> str | None:
    if zone_code is None:
        return None
    value = str(zone_code).strip()
    return value or None


def _coerce_int(raw: object) -> int:
    return int(cast(int | float | str, raw))


def _coerce_float(raw: object) -> float:
    return float(cast(int | float | str, raw))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _preview_plants(
    rows: list[dict[str, object]],
    *,
    limit: int = _PREVIEW_LIMIT,
) -> list[dict[str, str]]:
    return [
        {
            "plt_id": str(row["plt_id"]),
            "name": str(row["name"] or row["plt_id"]),
        }
        for row in rows[:limit]
    ]


def _preview_plots(
    rows: list[dict[str, object]],
    *,
    limit: int = _PREVIEW_LIMIT,
) -> list[dict[str, str]]:
    return [
        {
            "plot_id": str(row["plot_id"]),
            "zone_code": str(row["zone_code"]),
            "zone_name": str(row["zone_name"]),
        }
        for row in rows[:limit]
    ]


def _scope_link_filter(
    *,
    entity_alias: str,
    entity_id_column: str,
    plot_link_table: str,
    plot_link_column: str,
    plant_link_table: str,
    plant_link_column: str,
    plot_ids: list[str],
    plant_ids: list[str],
) -> tuple[str, list[str]]:
    if not plot_ids and not plant_ids:
        return " AND 0", []

    clauses: list[str] = []
    params: list[str] = []

    if plot_ids:
        placeholders = ",".join(["%s"] * len(plot_ids))
        clauses.append(
            f"{entity_alias}.{entity_id_column} IN "
            f"(SELECT {plot_link_column} FROM {plot_link_table} WHERE plot_id IN ({placeholders}))"
        )
        params.extend(plot_ids)

    if plant_ids:
        placeholders = ",".join(["%s"] * len(plant_ids))
        clauses.append(
            f"{entity_alias}.{entity_id_column} IN "
            f"(SELECT {plant_link_column} FROM {plant_link_table} WHERE plt_id IN ({placeholders}))"
        )
        params.extend(plant_ids)

    return f" AND ({' OR '.join(clauses)})", params


def _build_scope(
    db: DbConn,
    *,
    garden_id: int,
    zone_code: str | None,
) -> ReportScope:
    requested_zone = _normalize_zone_code(zone_code)
    zone_rows = db.execute(
        """
        SELECT pl.zone_code, MIN(pl.zone_name) AS zone_name, COUNT(*) AS plot_count
        FROM plots pl
        JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
        WHERE pwo.garden_id = %s
        GROUP BY pl.zone_code
        ORDER BY pl.zone_code
        """,
        (garden_id,),
    ).fetchall()

    available_zones = [
        {
            "zone_code": str(row["zone_code"]),
            "zone_name": str(row["zone_name"]),
            "plot_count": int(row["plot_count"]),
        }
        for row in zone_rows
    ]
    zone_lookup = {str(row["zone_code"]): str(row["zone_name"]) for row in zone_rows}
    if requested_zone and requested_zone not in zone_lookup:
        raise ValueError("Zone not found in active garden")

    plot_params: list[object] = [garden_id]
    zone_sql = ""
    if requested_zone:
        zone_sql = " AND pl.zone_code = %s"
        plot_params.append(requested_zone)
    plot_rows = [
        dict(row)
        for row in db.execute(
            f"""
            SELECT
                pl.plot_id,
                pl.zone_code,
                pl.zone_name,
                COUNT(DISTINCT pp.plt_id) AS plant_slots,
                COALESCE(SUM(pp.quantity), 0) AS total_quantity
            FROM plots pl
            JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
            LEFT JOIN (
                SELECT assignments.plot_id, assignments.plt_id, assignments.quantity
                FROM plot_plants assignments
                JOIN plant_ownership assigned_po
                  ON assigned_po.plt_id = assignments.plt_id
                WHERE assigned_po.garden_id = %s
            ) pp ON pp.plot_id = pl.plot_id
            WHERE pwo.garden_id = %s{zone_sql}
            GROUP BY pl.plot_id, pl.zone_code, pl.zone_name
            ORDER BY pl.zone_code, pl.plot_id
            """,
            [garden_id, *plot_params],
        ).fetchall()
    ]
    plot_ids = [str(row["plot_id"]) for row in plot_rows]

    if requested_zone:
        plant_rows = [
            dict(row)
            for row in db.execute(
                """
                SELECT DISTINCT
                    p.plt_id,
                    p.name,
                    p.latin,
                    p.bloom_month,
                    p.light,
                    p.hardiness,
                    p.year_planted
                FROM plants p
                JOIN plant_ownership po ON po.plt_id = p.plt_id
                JOIN plot_plants pp ON pp.plt_id = p.plt_id
                JOIN plots pl ON pl.plot_id = pp.plot_id
                JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
                WHERE po.garden_id = %s AND pwo.garden_id = %s AND pl.zone_code = %s
                ORDER BY p.name
                """,
                (garden_id, garden_id, requested_zone),
            ).fetchall()
        ]
    else:
        plant_rows = [
            dict(row)
            for row in db.execute(
                """
                SELECT
                    p.plt_id,
                    p.name,
                    p.latin,
                    p.bloom_month,
                    p.light,
                    p.hardiness,
                    p.year_planted
                FROM plants p
                JOIN plant_ownership po ON po.plt_id = p.plt_id
                WHERE po.garden_id = %s
                ORDER BY p.name
                """,
                (garden_id,),
            ).fetchall()
        ]

    plant_ids = _dedupe([str(row["plt_id"]) for row in plant_rows])

    return ReportScope(
        zone_code=requested_zone,
        zone_name=zone_lookup.get(requested_zone) if requested_zone else None,
        available_zones=cast(list[dict[str, object]], available_zones),
        plot_rows=plot_rows,
        plot_ids=plot_ids,
        plant_rows=plant_rows,
        plant_ids=plant_ids,
    )


def _build_needs_attention(
    db: DbConn,
    *,
    garden_id: int,
    scope: ReportScope,
) -> dict[str, object]:
    today_iso = offset_days_iso(0)
    week_end_iso = offset_days_iso(7)
    task_scope_sql = ""
    task_scope_params: list[str] = []
    issue_scope_sql = ""
    issue_scope_params: list[str] = []
    if scope.zone_code:
        task_scope_sql, task_scope_params = _scope_link_filter(
            entity_alias="t",
            entity_id_column="id",
            plot_link_table="garden_task_plots",
            plot_link_column="task_id",
            plant_link_table="garden_task_plants",
            plant_link_column="task_id",
            plot_ids=scope.plot_ids,
            plant_ids=scope.plant_ids,
        )
        issue_scope_sql, issue_scope_params = _scope_link_filter(
            entity_alias="i",
            entity_id_column="id",
            plot_link_table="garden_issue_plots",
            plot_link_column="issue_id",
            plant_link_table="garden_issue_plants",
            plant_link_column="issue_id",
            plot_ids=scope.plot_ids,
            plant_ids=scope.plant_ids,
        )

    overdue_task_rows = db.execute(
        f"""
        SELECT t.public_id
        FROM garden_tasks t
        WHERE t.garden_id = %s AND t.status = 'pending' AND t.due_on < %s
        {task_scope_sql}
        ORDER BY t.due_on, t.public_id
        """,
        [garden_id, today_iso, *task_scope_params],
    ).fetchall()
    due_this_week_rows = db.execute(
        f"""
        SELECT t.public_id
        FROM garden_tasks t
        WHERE t.garden_id = %s
          AND t.status = 'pending'
          AND t.due_on >= %s
          AND t.due_on <= %s
        {task_scope_sql}
        ORDER BY t.due_on, t.public_id
        """,
        [garden_id, today_iso, week_end_iso, *task_scope_params],
    ).fetchall()

    unresolved_statuses = ("open", "monitoring", "treating")
    unresolved_placeholders = ",".join(["%s"] * len(unresolved_statuses))
    open_issue_rows = db.execute(
        f"""
        SELECT i.public_id
        FROM garden_issues i
        WHERE i.garden_id = %s AND i.status IN ({unresolved_placeholders})
        {issue_scope_sql}
        ORDER BY i.created_at_ms DESC, i.public_id
        """,
        [garden_id, *unresolved_statuses, *issue_scope_params],
    ).fetchall()
    overdue_followup_rows = db.execute(
        f"""
        SELECT i.public_id
        FROM garden_issues i
        WHERE i.garden_id = %s
          AND i.status IN ({unresolved_placeholders})
          AND i.follow_up_on IS NOT NULL
          AND i.follow_up_on < %s
        {issue_scope_sql}
        ORDER BY i.follow_up_on, i.public_id
        """,
        [garden_id, *unresolved_statuses, today_iso, *issue_scope_params],
    ).fetchall()

    alert_scope_sql = ""
    alert_scope_params: list[str] = []
    if scope.zone_code:
        if scope.plant_ids:
            placeholders = ",".join(["%s"] * len(scope.plant_ids))
            alert_scope_sql = f"""
              AND (
                NOT EXISTS (
                    SELECT 1
                    FROM weather_alert_plants wap
                    WHERE wap.alert_id = wa.id
                )
                OR EXISTS (
                    SELECT 1
                    FROM weather_alert_plants wap
                    WHERE wap.alert_id = wa.id
                      AND wap.plt_id IN ({placeholders})
                )
              )
            """
            alert_scope_params.extend(scope.plant_ids)
        else:
            alert_scope_sql = """
              AND NOT EXISTS (
                SELECT 1
                FROM weather_alert_plants wap
                WHERE wap.alert_id = wa.id
              )
            """

    alert_rows = db.execute(
        f"""
        SELECT wa.id, wa.title
        FROM weather_alerts wa
        WHERE wa.garden_id = %s
          AND wa.dismissed = 0
          AND wa.valid_until >= %s
          {alert_scope_sql}
        ORDER BY
          CASE wa.severity WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
          wa.valid_from ASC
        """,
        [garden_id, today_iso, *alert_scope_params],
    ).fetchall()

    return {
        "overdue_tasks_count": len(overdue_task_rows),
        "overdue_task_ids": [str(row["public_id"]) for row in overdue_task_rows],
        "due_this_week_count": len(due_this_week_rows),
        "due_this_week_task_ids": [str(row["public_id"]) for row in due_this_week_rows],
        "open_issues_count": len(open_issue_rows),
        "open_issue_ids": [str(row["public_id"]) for row in open_issue_rows],
        "overdue_follow_ups_count": len(overdue_followup_rows),
        "overdue_follow_up_issue_ids": [str(row["public_id"]) for row in overdue_followup_rows],
        "active_weather_alerts_count": len(alert_rows),
        "active_weather_alert_ids": [int(row["id"]) for row in alert_rows],
        "weather_alert_titles": [str(row["title"]) for row in alert_rows[:3]],
    }


def _build_bloom_block(
    *,
    plant_rows: list[dict[str, object]],
    month_number: int,
) -> dict[str, object]:
    matching = [
        row for row in plant_rows if month_number in _bloom_months(str(row["bloom_month"] or ""))
    ]
    return {
        "month": month_number,
        "count": len(matching),
        "plant_ids": [str(row["plt_id"]) for row in matching],
        "plants": _preview_plants(matching),
    }


def _build_missing_observations(
    db: DbConn,
    *,
    garden_id: int,
    plant_rows: list[dict[str, object]],
    plant_ids: list[str],
) -> dict[str, object]:
    if not plant_ids:
        return {
            "threshold_months": _OBSERVATION_STALE_MONTHS,
            "count": 0,
            "plant_ids": [],
            "plants": [],
        }

    placeholders = ",".join(["%s"] * len(plant_ids))
    last_seen_rows = db.execute(
        f"""
        SELECT jep.plt_id, MAX(je.occurred_on) AS last_seen
        FROM garden_journal_entry_plants jep
        JOIN garden_journal_entries je ON je.id = jep.entry_id
        WHERE je.garden_id = %s
          AND je.event_type IN ('observed', 'bloomed')
          AND jep.plt_id IN ({placeholders})
        GROUP BY jep.plt_id
        """,
        [garden_id, *plant_ids],
    ).fetchall()
    last_seen_map = {
        str(row["plt_id"]): str(row["last_seen"]) for row in last_seen_rows if row["last_seen"]
    }
    threshold_iso = offset_months_iso(-_OBSERVATION_STALE_MONTHS)
    stale_rows = [
        row
        for row in plant_rows
        if not last_seen_map.get(str(row["plt_id"]))
        or last_seen_map[str(row["plt_id"])] < threshold_iso
    ]
    return {
        "threshold_months": _OBSERVATION_STALE_MONTHS,
        "count": len(stale_rows),
        "plant_ids": [str(row["plt_id"]) for row in stale_rows],
        "plants": _preview_plants(stale_rows),
    }


def _build_plot_use(plot_rows: list[dict[str, object]]) -> dict[str, object]:
    empty_rows = [row for row in plot_rows if _coerce_int(row["plant_slots"]) <= 0]
    underused_rows = [
        row
        for row in plot_rows
        if _coerce_int(row["plant_slots"]) == 1 and _coerce_float(row["total_quantity"]) <= 1
    ]
    return {
        "total_plots": len(plot_rows),
        "empty_count": len(empty_rows),
        "empty_plot_ids": [str(row["plot_id"]) for row in empty_rows],
        "empty_plots": _preview_plots(empty_rows),
        "underused_count": len(underused_rows),
        "underused_plot_ids": [str(row["plot_id"]) for row in underused_rows],
        "underused_plots": _preview_plots(underused_rows),
    }


def _build_data_quality(
    db: DbConn,
    *,
    garden_id: int,
    plant_rows: list[dict[str, object]],
    plant_ids: list[str],
) -> dict[str, object]:
    missing_care_rows = [
        row
        for row in plant_rows
        if not str(row["light"] or "").strip() or not str(row["hardiness"] or "").strip()
    ]
    missing_year_rows = [row for row in plant_rows if not str(row["year_planted"] or "").strip()]

    covered_ids: set[str] = set()
    if plant_ids:
        placeholders = ",".join(["%s"] * len(plant_ids))
        cover_rows = db.execute(
            f"""
            SELECT plt_id
            FROM plant_media_covers
            WHERE garden_id = %s AND plt_id IN ({placeholders})
            """,
            [garden_id, *plant_ids],
        ).fetchall()
        covered_ids = {str(row["plt_id"]) for row in cover_rows}
    missing_cover_rows = [row for row in plant_rows if str(row["plt_id"]) not in covered_ids]

    return {
        "missing_care_count": len(missing_care_rows),
        "missing_care_plant_ids": [str(row["plt_id"]) for row in missing_care_rows],
        "missing_care_plants": _preview_plants(missing_care_rows),
        "missing_year_count": len(missing_year_rows),
        "missing_year_plant_ids": [str(row["plt_id"]) for row in missing_year_rows],
        "missing_year_plants": _preview_plants(missing_year_rows),
        "missing_cover_count": len(missing_cover_rows),
        "missing_cover_plant_ids": [str(row["plt_id"]) for row in missing_cover_rows],
        "missing_cover_plants": _preview_plants(missing_cover_rows),
    }


def _build_yield_summary(
    db: DbConn,
    *,
    garden_id: int,
    scope: ReportScope,
) -> dict[str, object]:
    target_year = date.today().year
    month_expr = month_number_sql("he.occurred_on")
    params: list[object] = [garden_id, f"{target_year}-01-01", f"{target_year}-12-31"]
    where = [
        "he.garden_id = %s",
        "he.occurred_on >= %s",
        "he.occurred_on <= %s",
    ]

    if scope.zone_code:
        scope_sql, scope_params = _scope_link_filter(
            entity_alias="he",
            entity_id_column="id",
            plot_link_table="harvest_entry_plots",
            plot_link_column="entry_id",
            plant_link_table="harvest_entry_plants",
            plant_link_column="entry_id",
            plot_ids=scope.plot_ids,
            plant_ids=scope.plant_ids,
        )
        where.append(scope_sql.removeprefix(" AND "))
        params.extend(scope_params)

    where_sql = " AND ".join(where)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM harvest_entries he
        WHERE {where_sql}
        """,
        params,
    ).fetchone()
    total_entries = int(total_row["c"]) if total_row else 0

    if total_entries == 0:
        return {
            "year": target_year,
            "total_entries": 0,
            "harvested_plot_count": 0,
            "active_month_count": 0,
            "best_month": None,
            "best_month_entries": 0,
            "top_producers": [],
        }

    harvested_plot_count = 0
    if scope.zone_code:
        harvested_plot_count_row = (
            db.execute(
                f"""
            SELECT COUNT(DISTINCT hep.plot_id) AS c
            FROM harvest_entries he
            JOIN harvest_entry_plots hep ON hep.entry_id = he.id
            WHERE {where_sql} AND hep.plot_id IN ({",".join("%s" for _ in scope.plot_ids)})
            """,
                [*params, *scope.plot_ids],
            ).fetchone()
            if scope.plot_ids
            else None
        )
    else:
        harvested_plot_count_row = db.execute(
            f"""
            SELECT COUNT(DISTINCT hep.plot_id) AS c
            FROM harvest_entries he
            JOIN harvest_entry_plots hep ON hep.entry_id = he.id
            WHERE {where_sql}
              AND EXISTS (
                  SELECT 1
                  FROM plot_ownership harvest_plot_ownership
                  WHERE harvest_plot_ownership.plot_id = hep.plot_id
                    AND harvest_plot_ownership.garden_id = %s
              )
            """,
            [*params, garden_id],
        ).fetchone()
    if harvested_plot_count_row:
        harvested_plot_count = int(harvested_plot_count_row["c"])

    by_month_rows = db.execute(
        f"""
        SELECT {month_expr} AS month, COUNT(*) AS entries
        FROM harvest_entries he
        WHERE {where_sql}
        GROUP BY month
        ORDER BY month
        """,
        params,
    ).fetchall()
    best_month = None
    best_month_entries = 0
    if by_month_rows:
        best = max(by_month_rows, key=lambda row: int(row["entries"]))
        best_month = int(best["month"])
        best_month_entries = int(best["entries"])

    producer_rows = db.execute(
        f"""
        SELECT
            hep.plt_id,
            COALESCE(p.name, hep.plt_id) AS name,
            he.unit,
            SUM(he.quantity) AS total_qty,
            COUNT(*) AS entries
        FROM harvest_entries he
        JOIN harvest_entry_plants hep ON hep.entry_id = he.id
        LEFT JOIN plants p ON p.plt_id = hep.plt_id
        WHERE {where_sql}
          AND EXISTS (
              SELECT 1
              FROM plant_ownership harvest_plant_ownership
              WHERE harvest_plant_ownership.plt_id = hep.plt_id
                AND harvest_plant_ownership.garden_id = %s
          )
        GROUP BY hep.plt_id, p.name, he.unit
        ORDER BY COUNT(*) DESC, SUM(he.quantity) DESC, name ASC
        """,
        [*params, garden_id],
    ).fetchall()
    producer_map: dict[str, dict[str, object]] = {}
    for row in producer_rows:
        plt_id = str(row["plt_id"])
        producer = producer_map.setdefault(
            plt_id,
            {
                "plt_id": plt_id,
                "name": str(row["name"] or plt_id),
                "entries": 0,
                "units": [],
            },
        )
        producer["entries"] = _coerce_int(producer["entries"]) + _coerce_int(row["entries"])
        units = cast(list[dict[str, object]], producer["units"])
        units.append(
            {
                "unit": str(row["unit"]),
                "total_qty": _coerce_float(row["total_qty"]),
            }
        )
    top_producers = sorted(
        producer_map.values(),
        key=lambda item: (-int(item["entries"]), str(item["name"]).lower()),
    )[:5]

    return {
        "year": target_year,
        "total_entries": total_entries,
        "harvested_plot_count": harvested_plot_count,
        "active_month_count": len(by_month_rows),
        "best_month": best_month,
        "best_month_entries": best_month_entries,
        "top_producers": top_producers,
    }


def get_gardener_reports(
    db: DbConn,
    *,
    garden_id: int,
    zone_code: str | None = None,
) -> dict[str, object]:
    scope = _build_scope(db, garden_id=garden_id, zone_code=zone_code)
    current_month = date.today().month
    next_month = 1 if current_month == 12 else current_month + 1

    return {
        "zone_code": scope.zone_code,
        "zone_name": scope.zone_name,
        "available_zones": scope.available_zones,
        "needs_attention": _build_needs_attention(db, garden_id=garden_id, scope=scope),
        "bloom_now": _build_bloom_block(
            plant_rows=scope.plant_rows,
            month_number=current_month,
        ),
        "bloom_next": _build_bloom_block(
            plant_rows=scope.plant_rows,
            month_number=next_month,
        ),
        "missing_observations": _build_missing_observations(
            db,
            garden_id=garden_id,
            plant_rows=scope.plant_rows,
            plant_ids=scope.plant_ids,
        ),
        "plot_use": _build_plot_use(scope.plot_rows),
        "data_quality": _build_data_quality(
            db,
            garden_id=garden_id,
            plant_rows=scope.plant_rows,
            plant_ids=scope.plant_ids,
        ),
        "yield_summary": _build_yield_summary(
            db,
            garden_id=garden_id,
            scope=scope,
        ),
    }
