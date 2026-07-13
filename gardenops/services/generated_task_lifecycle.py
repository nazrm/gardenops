from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from gardenops.db import DbConn, current_timestamp_ms

GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS = ("water:%",)
GENERATED_WATERING_RULE_SOURCE_PATTERNS = (
    *GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS,
    "auto:dry_water:%",
)


def is_generated_watering_rule_source(rule_source: str | None) -> bool:
    value = (rule_source or "").strip()
    return value.startswith("water:") or value.startswith("auto:dry_water:")


def is_stale_generated_watering_task(
    *,
    task_type: str,
    rule_source: str | None,
    action_on: Any,
    today: str,
) -> bool:
    return (
        task_type == "water"
        and is_generated_watering_rule_source(rule_source)
        and bool(action_on)
        and str(action_on) < today
    )


def stale_generated_watering_sql(
    *,
    task_alias: str = "t",
    action_on_sql: str | None = None,
    today_sql: str = "%s",
) -> str:
    prefix = f"{task_alias}." if task_alias else ""
    action_on = action_on_sql or f"COALESCE({prefix}snoozed_until, {prefix}due_on)"
    return (
        f"({prefix}task_type = 'water' "
        f"AND ({prefix}rule_source LIKE %s OR {prefix}rule_source LIKE %s) "
        f"AND {action_on} < {today_sql})"
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


def _expiry_metadata(
    metadata_json: Any,
    *,
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
            "status": "expired",
            "reason": "stale_generated_watering",
            "expired_at_ms": now_ms,
            "expired_on": today_iso,
            "action_on": action_on,
            "source": "generated_task_lifecycle",
        }
    )
    metadata["lifecycle"] = lifecycle
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


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
    rows = db.execute(
        f"""
        SELECT t.id, t.metadata_json, COALESCE(t.snoozed_until, t.due_on) AS action_on
        FROM garden_tasks t
        WHERE t.garden_id = %s
          AND t.status IN ('pending', 'snoozed')
          AND {stale_generated_watering}
        ORDER BY t.id ASC
        FOR UPDATE OF t SKIP LOCKED
        """,  # noqa: S608
        [
            garden_id,
            *GENERATED_WATERING_RULE_SOURCE_PATTERNS,
            today,
        ],
    ).fetchall()
    for row in rows:
        db.execute(
            """
            UPDATE garden_tasks
            SET status = 'expired',
                snoozed_until = NULL,
                completed_by_user_id = NULL,
                completed_at_ms = NULL,
                metadata_json = %s,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (
                _expiry_metadata(
                    row["metadata_json"],
                    now_ms=now_value,
                    today_iso=today,
                    action_on=str(row["action_on"] or ""),
                ),
                now_value,
                int(row["id"]),
            ),
        )
    return len(rows)
