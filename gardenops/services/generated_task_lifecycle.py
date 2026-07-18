from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from gardenops.db import DbConn, current_timestamp_ms
from gardenops.services.plant_traits import harvest_offset_months

GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS = ("water:%",)
GENERATED_WATERING_RULE_SOURCE_PATTERNS = (
    *GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS,
    "auto:dry_water:%",
)
GENERATED_NON_WATERING_WEATHER_RULE_SOURCE_PATTERNS = (
    "auto:frost_protect:%",
    "auto:heat_protect:%",
    "auto:rain_drainage:%",
)


def is_generated_watering_rule_source(rule_source: str | None) -> bool:
    value = (rule_source or "").strip()
    return value.startswith("water:") or value.startswith("auto:dry_water:")


def is_generated_non_watering_weather_rule_source(rule_source: str | None) -> bool:
    value = (rule_source or "").strip()
    return value.startswith(("auto:frost_protect:", "auto:heat_protect:", "auto:rain_drainage:"))


def is_stale_generated_watering_task(
    *,
    task_type: str,
    rule_source: str | None,
    action_on: Any,
    today: str,
    alert_valid_until: Any = None,
    alert_active: bool = True,
) -> bool:
    if task_type != "water" or not is_generated_watering_rule_source(rule_source):
        return False
    value = (rule_source or "").strip()
    if value.startswith("auto:dry_water:"):
        return not alert_active or not alert_valid_until or str(alert_valid_until) < today
    return bool(action_on) and str(action_on) < today


def stale_generated_watering_sql(
    *,
    task_alias: str = "t",
    action_on_sql: str | None = None,
    today_sql: str = "%s",
) -> str:
    prefix = f"{task_alias}." if task_alias else ""
    correlation_prefix = prefix or "garden_tasks."
    action_on = action_on_sql or f"COALESCE({prefix}snoozed_until, {prefix}due_on)"
    action_on_date = f"({action_on})::date"
    today_date = f"({today_sql})::date"
    return (
        f"({prefix}task_type = 'water' "
        "AND ("
        f"({prefix}rule_source LIKE %s AND {action_on_date} < {today_date}) "
        "OR "
        f"({prefix}rule_source LIKE %s AND NOT EXISTS ("
        "SELECT 1 FROM weather_alerts lifecycle_weather_alert "
        f"WHERE lifecycle_weather_alert.garden_id = {correlation_prefix}garden_id "
        "AND lifecycle_weather_alert.id = CASE "
        f"WHEN split_part({correlation_prefix}rule_source, ':', 3) ~ '^[0-9]+$' "
        f"THEN split_part({correlation_prefix}rule_source, ':', 3)::int ELSE NULL END "
        "AND lifecycle_weather_alert.dismissed = 0 "
        f"AND lifecycle_weather_alert.valid_until::date >= {today_date}"
        "))))"
    )


def stale_generated_non_watering_weather_sql(
    *,
    task_alias: str = "t",
    today_sql: str = "%s",
) -> str:
    """Return SQL for active weather tasks whose alert no longer applies."""
    prefix = f"{task_alias}." if task_alias else ""
    correlation_prefix = prefix or "garden_tasks."
    pattern_clauses = " OR ".join(
        f"{prefix}rule_source LIKE %s"
        for _pattern in GENERATED_NON_WATERING_WEATHER_RULE_SOURCE_PATTERNS
    )
    today_date = f"({today_sql})::date"
    return (
        f"({prefix}task_type <> 'water' "
        f"AND ({pattern_clauses}) "
        "AND NOT EXISTS ("
        "SELECT 1 FROM weather_alerts lifecycle_weather_alert "
        f"WHERE lifecycle_weather_alert.garden_id = {correlation_prefix}garden_id "
        "AND lifecycle_weather_alert.id = CASE "
        f"WHEN split_part({correlation_prefix}rule_source, ':', 3) ~ '^[0-9]+$' "
        f"THEN split_part({correlation_prefix}rule_source, ':', 3)::int ELSE NULL END "
        "AND lifecycle_weather_alert.dismissed = 0 "
        f"AND lifecycle_weather_alert.valid_until::date >= {today_date}"
        "))"
    )


def _today_iso_from_now_ms(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, UTC).date().isoformat()


def _parse_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _terminal_metadata(
    metadata_json: Any,
    *,
    status: str,
    reason: str,
    now_ms: int,
    today_iso: str,
    action_on: str,
) -> str:
    metadata = _parse_metadata(metadata_json)
    lifecycle = metadata.get("lifecycle")
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    lifecycle.update(
        {
            "status": status,
            "reason": reason,
            "action_on": action_on,
            "source": "generated_task_lifecycle",
        }
    )
    if status == "expired":
        lifecycle["expired_at_ms"] = now_ms
        lifecycle["expired_on"] = today_iso
    else:
        lifecycle["skipped_at_ms"] = now_ms
        lifecycle["skipped_on"] = today_iso
    metadata["lifecycle"] = lifecycle
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


def _invalid_generated_harvest_rows(db: DbConn, garden_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT t.id, t.task_type, t.rule_source, t.metadata_json,
               COALESCE(t.snoozed_until, t.due_on) AS action_on,
               p.plt_id, p.name, p.category,
               p.care_watering, p.care_soil, p.care_planting,
               p.care_maintenance, p.care_notes
        FROM garden_tasks t
        LEFT JOIN garden_task_plants gtp ON gtp.task_id = t.id
        LEFT JOIN plants p ON p.plt_id = gtp.plt_id
        WHERE t.garden_id = %s
          AND t.task_type = 'harvest'
          AND t.status IN ('pending', 'snoozed')
          AND t.rule_source LIKE 'harvest_check:%%'
        ORDER BY t.id ASC, p.plt_id ASC
        FOR UPDATE OF t SKIP LOCKED
        """,
        (garden_id,),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["id"]), []).append(dict(row))

    invalid: list[dict[str, Any]] = []
    for task_rows in grouped.values():
        eligible = any(
            row.get("plt_id") and harvest_offset_months(row) is not None for row in task_rows
        )
        if not eligible:
            invalid.append(task_rows[0])
    return invalid


def expire_stale_generated_tasks(
    db: DbConn,
    *,
    garden_id: int,
    today_iso: str | None = None,
    now_ms: int | None = None,
) -> int:
    """Expire generated task advice that is no longer valid garden work."""
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    today = today_iso or _today_iso_from_now_ms(now_value)
    stale_generated_watering = stale_generated_watering_sql(
        action_on_sql="COALESCE(t.snoozed_until, t.due_on)",
        today_sql="%s",
    )
    stale_generated_non_watering = stale_generated_non_watering_weather_sql(today_sql="%s")
    rows = [
        dict(row)
        for row in db.execute(
            f"""
        SELECT t.id, t.task_type, t.rule_source, t.metadata_json,
               COALESCE(t.snoozed_until, t.due_on) AS action_on,
               weather_alert.id AS weather_alert_id,
               weather_alert.dismissed AS weather_alert_dismissed
        FROM garden_tasks t
        LEFT JOIN weather_alerts weather_alert
          ON weather_alert.garden_id = t.garden_id
         AND weather_alert.id = CASE
            WHEN split_part(t.rule_source, ':', 3) ~ '^[0-9]+$'
            THEN split_part(t.rule_source, ':', 3)::int ELSE NULL
         END
        WHERE t.garden_id = %s
          AND t.status IN ('pending', 'snoozed')
          AND ({stale_generated_watering} OR {stale_generated_non_watering})
        ORDER BY t.id ASC
        FOR UPDATE OF t SKIP LOCKED
        """,  # noqa: S608
            [
                garden_id,
                GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS[0],
                today,
                "auto:dry_water:%",
                today,
                *GENERATED_NON_WATERING_WEATHER_RULE_SOURCE_PATTERNS,
                today,
            ],
        ).fetchall()
    ]
    rows.extend(_invalid_generated_harvest_rows(db, garden_id))
    for row in rows:
        terminal_status = "expired"
        terminal_reason = "stale_generated_watering"
        if str(row["task_type"] or "") == "harvest":
            terminal_reason = "harvest_not_applicable"
        elif is_generated_non_watering_weather_rule_source(str(row["rule_source"] or "")):
            if row["weather_alert_id"] is None or bool(row["weather_alert_dismissed"]):
                terminal_status = "skipped"
                terminal_reason = "weather_alert_resolved"
            else:
                terminal_reason = "weather_alert_validity_ended"
        db.execute(
            """
            UPDATE garden_tasks
            SET status = %s,
                snoozed_until = NULL,
                completed_by_user_id = NULL,
                completed_at_ms = NULL,
                metadata_json = %s,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (
                terminal_status,
                _terminal_metadata(
                    row["metadata_json"],
                    status=terminal_status,
                    reason=terminal_reason,
                    now_ms=now_value,
                    today_iso=today,
                    action_on=str(row["action_on"] or ""),
                ),
                now_value,
                int(row["id"]),
            ),
        )
    return len(rows)
