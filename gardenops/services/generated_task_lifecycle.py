from __future__ import annotations

from typing import Any

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
