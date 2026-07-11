"""Notification service – create, query, and manage notification events."""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from typing import Any, cast

from gardenops.branding import app_name
from gardenops.db import DbConn, current_timestamp_ms
from gardenops.router_helpers import generate_public_id
from gardenops.services.attention.preferences import (
    AttentionPreferenceSet,
    AttentionSurface,
    apply_preferences,
)
from gardenops.services.attention.service import load_attention_preferences
from gardenops.services.attention.types import (
    AttentionCategory,
    AttentionItem,
    normalize_severity,
)
from gardenops.services.automation import (
    escalate_overdue_follow_ups,
    on_dry_spell_alert,
    on_frost_alert,
    on_heat_alert,
    on_rain_alert,
)
from gardenops.services.generated_task_lifecycle import (
    GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS,
    expire_stale_generated_tasks,
)
from gardenops.services.task_generator import generate_tasks
from gardenops.services.weather_service import check_weather_and_generate_alerts
from gardenops.sql_dates import offset_days_iso

EmailSender = Callable[[str, str, str], None]
logger = logging.getLogger(__name__)
_SCHEDULER_LEASE_KEY = "notification_scheduler_lease"

NotificationRule = dict[str, bool | str]

_SEVERITY_RANK = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}

_WEATHER_TASK_RULE_SOURCE_PATTERNS = (
    "auto:frost_protect:%",
    "auto:heat_protect:%",
    "auto:dry_water:%",
    "auto:rain_drainage:%",
)

_NOTIFICATION_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "key": "task_due",
        "group": "tasks",
        "notification_type": "task_due",
        "notification_subtype": None,
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": False,
        "default_min_severity": "low",
        "user_configurable": True,
    },
    {
        "key": "task_overdue",
        "group": "tasks",
        "notification_type": "task_overdue",
        "notification_subtype": None,
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": False,
        "default_min_severity": "low",
        "user_configurable": True,
    },
    {
        "key": "task_upcoming",
        "group": "tasks",
        "notification_type": "task_upcoming",
        "notification_subtype": None,
        "default_in_app_enabled": False,
        "default_email_enabled": False,
        "supports_severity": False,
        "default_min_severity": "low",
        "user_configurable": True,
    },
    {
        "key": "task_generated",
        "group": "tasks",
        "notification_type": "task_generated",
        "notification_subtype": None,
        "default_in_app_enabled": False,
        "default_email_enabled": False,
        "supports_severity": False,
        "default_min_severity": "low",
        "user_configurable": True,
    },
    {
        "key": "issue_created",
        "group": "issues",
        "notification_type": "issue_created",
        "notification_subtype": None,
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": True,
        "default_min_severity": "normal",
        "user_configurable": True,
    },
    {
        "key": "weather_alert:frost_warning",
        "group": "weather",
        "notification_type": "weather_alert",
        "notification_subtype": "frost_warning",
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": True,
        "default_min_severity": "normal",
        "user_configurable": True,
    },
    {
        "key": "weather_alert:heat_wave",
        "group": "weather",
        "notification_type": "weather_alert",
        "notification_subtype": "heat_wave",
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": True,
        "default_min_severity": "normal",
        "user_configurable": True,
    },
    {
        "key": "weather_alert:dry_spell",
        "group": "weather",
        "notification_type": "weather_alert",
        "notification_subtype": "dry_spell",
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": True,
        "default_min_severity": "normal",
        "user_configurable": True,
    },
    {
        "key": "weather_alert:rain_surplus",
        "group": "weather",
        "notification_type": "weather_alert",
        "notification_subtype": "rain_surplus",
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": True,
        "default_min_severity": "normal",
        "user_configurable": True,
    },
    {
        "key": "system",
        "group": "system",
        "notification_type": "system",
        "notification_subtype": None,
        "default_in_app_enabled": True,
        "default_email_enabled": True,
        "supports_severity": True,
        "default_min_severity": "low",
        "user_configurable": False,
    },
)

_POLICIES_BY_KEY = {str(policy["key"]): policy for policy in _NOTIFICATION_POLICIES}


def notification_policy_catalog() -> list[dict[str, Any]]:
    """Return user-facing notification policy metadata for settings UI."""
    return [dict(policy) for policy in _NOTIFICATION_POLICIES]


def _policy_key(notification_type: str, notification_subtype: str | None = None) -> str:
    if notification_subtype:
        key = f"{notification_type}:{notification_subtype}"
        if key in _POLICIES_BY_KEY:
            return key
    return notification_type


def default_notification_rules() -> dict[str, NotificationRule]:
    return {
        str(policy["key"]): {
            "in_app_enabled": bool(policy["default_in_app_enabled"]),
            "email_enabled": bool(policy["default_email_enabled"]),
            "min_severity": str(policy["default_min_severity"]),
        }
        for policy in _NOTIFICATION_POLICIES
    }


def normalize_notification_rules(raw: dict[str, Any] | None) -> dict[str, NotificationRule]:
    defaults = default_notification_rules()
    if not raw:
        return defaults
    rules = {key: dict(value) for key, value in defaults.items()}
    for key, value in raw.items():
        if key not in _POLICIES_BY_KEY or not isinstance(value, dict):
            continue
        policy = _POLICIES_BY_KEY[key]
        if not bool(policy["user_configurable"]):
            continue
        rule = dict(rules[key])
        if "in_app_enabled" in value:
            rule["in_app_enabled"] = bool(value["in_app_enabled"])
        if "email_enabled" in value:
            rule["email_enabled"] = bool(value["email_enabled"])
        min_severity = value.get("min_severity")
        if isinstance(min_severity, str):
            rule["min_severity"] = _normalize_severity(min_severity)
        rules[key] = rule
    return rules


def notification_rules_json(raw: dict[str, Any] | None) -> str:
    return json.dumps(normalize_notification_rules(raw), separators=(",", ":"))


def _normalize_severity(severity: str | None) -> str:
    value = (severity or "normal").strip().lower()
    return value if value in _SEVERITY_RANK else "normal"


def _parse_rules_json(raw: str | None) -> dict[str, NotificationRule]:
    defaults = default_notification_rules()
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        logger.warning("Failed to parse notification rules_json: %r", raw)
        return defaults
    if not isinstance(parsed, dict):
        return defaults

    rules = {key: dict(value) for key, value in defaults.items()}
    for key, value in parsed.items():
        if key not in _POLICIES_BY_KEY or not isinstance(value, dict):
            continue
        rule = dict(rules[key])
        if "in_app_enabled" in value:
            rule["in_app_enabled"] = bool(value["in_app_enabled"])
        if "email_enabled" in value:
            rule["email_enabled"] = bool(value["email_enabled"])
        min_severity = value.get("min_severity")
        if isinstance(min_severity, str):
            rule["min_severity"] = _normalize_severity(min_severity)
        rules[key] = rule
    return rules


def _rules_from_pref_row(row: dict[str, Any] | None) -> dict[str, NotificationRule]:
    rules = _parse_rules_json(str(row["rules_json"]) if row and row.get("rules_json") else None)
    if not row:
        return rules

    # Backward compatibility with the original coarse task preference columns.
    if not row.get("rules_json"):
        if "task_due" in rules:
            rules["task_due"]["in_app_enabled"] = bool(row["task_due_enabled"])
        if "task_overdue" in rules:
            rules["task_overdue"]["in_app_enabled"] = bool(row["task_overdue_enabled"])
    return rules


def _notification_allowed_by_rules(
    rules: dict[str, NotificationRule],
    notification_type: str,
    notification_subtype: str | None,
    severity: str | None,
    *,
    channel: str,
) -> bool:
    key = _policy_key(notification_type, notification_subtype)
    policy = _POLICIES_BY_KEY.get(key) or _POLICIES_BY_KEY.get(notification_type)
    if policy and not bool(policy["user_configurable"]):
        return True
    rule = rules.get(key) or rules.get(notification_type)
    if not rule:
        rule = default_notification_rules().get(key, {})
    enabled_key = "email_enabled" if channel == "email" else "in_app_enabled"
    if not bool(rule.get(enabled_key, True)):
        return False
    min_severity = _normalize_severity(str(rule.get("min_severity", "low")))
    return _SEVERITY_RANK[_normalize_severity(severity)] >= _SEVERITY_RANK[min_severity]


def _attention_type_for_notification(
    notification_type: str,
    notification_subtype: str | None,
) -> str:
    if notification_type == "weather_alert":
        if notification_subtype == "frost_warning":
            return "frost_warning"
        if notification_subtype == "rain_surplus":
            return "rain_alert"
        return "weather_alert"
    if notification_type == "issue_created":
        return "issue_follow_up_due"
    return notification_subtype or notification_type


def _attention_category_for_notification(
    notification_type: str,
    notification_subtype: str | None,
) -> AttentionCategory:
    if notification_type in {"system", "status", "security", "backup"}:
        return "system"
    if notification_subtype in {"system", "status", "security", "backup"}:
        return "system"
    if notification_type == "weather_alert":
        return "warning"
    if notification_type == "task_upcoming":
        return "upcoming"
    return "needs_action"


def _attention_item_from_notification_row(
    row: dict[str, Any],
    *,
    fallback_garden_id: int,
    fallback_user_id: int,
) -> AttentionItem:
    notification_type = str(row.get("notification_type") or "")
    notification_subtype = (
        str(row.get("notification_subtype")) if row.get("notification_subtype") else None
    )
    public_id = str(row.get("public_id") or row.get("id") or "")
    target_type = str(row.get("target_type") or "") or None
    target_id = str(row.get("target_id") or "") or None
    row_garden_id = row.get("garden_id")
    row_user_id = row.get("user_id")
    return AttentionItem(
        id=f"attn:notification-event:{public_id}",
        provider="notification_status",
        type=_attention_type_for_notification(notification_type, notification_subtype),
        category=_attention_category_for_notification(notification_type, notification_subtype),
        severity=normalize_severity(str(row.get("severity") or "normal")),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        reason="Notification",
        target_type=target_type,
        target_id=target_id,
        garden_id=int(row_garden_id) if row_garden_id is not None else fallback_garden_id,
        audience_user_id=int(row_user_id) if row_user_id is not None else fallback_user_id,
        delivery_eligibility=("panel_only", "inbox", "digest"),
        updated_at_ms=int(row.get("created_at_ms") or 0),
        metadata={
            "notification_type": notification_type,
            "notification_subtype": notification_subtype,
        },
    )


def notification_rows_allowed_by_attention(
    rows: list[dict[str, Any]],
    *,
    preferences: AttentionPreferenceSet,
    surface: AttentionSurface,
    garden_id: int,
    user_id: int,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    allowed: list[dict[str, Any]] = []
    for row in rows:
        policy_key = _policy_key(
            str(row.get("notification_type") or ""),
            str(row.get("notification_subtype")) if row.get("notification_subtype") else None,
        )
        policy = _POLICIES_BY_KEY.get(policy_key) or _POLICIES_BY_KEY.get(
            str(row.get("notification_type") or "")
        )
        if policy and not bool(policy["user_configurable"]):
            allowed.append(row)
            continue
        item = _attention_item_from_notification_row(
            row,
            fallback_garden_id=garden_id,
            fallback_user_id=user_id,
        )
        if apply_preferences([item], preferences, surface=surface, now_ms=now_ms):
            allowed.append(row)
    return allowed


def _date_start_ms(date_iso: str | None) -> int | None:
    if not date_iso:
        return None
    try:
        parsed = date.fromisoformat(date_iso)
    except ValueError:
        return None
    return int(datetime.combine(parsed, time.min, tzinfo=UTC).timestamp() * 1000)


def _date_end_ms(date_iso: str | None) -> int | None:
    if not date_iso:
        return None
    try:
        parsed = date.fromisoformat(date_iso)
    except ValueError:
        return None
    return int(datetime.combine(parsed, time.max, tzinfo=UTC).timestamp() * 1000)


def notification_scheduler_enabled() -> bool:
    raw = os.getenv("GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED", "auto").strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    return os.getenv("APP_ENV") != "test"


def notification_scheduler_poll_seconds() -> int:
    raw = os.getenv("GARDENOPS_NOTIFICATION_SCHEDULER_POLL_SECONDS", "900").strip()
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid GARDENOPS_NOTIFICATION_SCHEDULER_POLL_SECONDS=%r; using 900",
            raw,
        )
        return 900
    return max(60, value)


def notification_scheduler_owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _scheduler_lease_ttl_ms(poll_seconds: int | None = None) -> int:
    default_seconds = max((poll_seconds or notification_scheduler_poll_seconds()) * 2, 300)
    raw = os.getenv(
        "GARDENOPS_NOTIFICATION_SCHEDULER_LEASE_SECONDS",
        str(default_seconds),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid GARDENOPS_NOTIFICATION_SCHEDULER_LEASE_SECONDS=%r; using %s",
            raw,
            default_seconds,
        )
        value = default_seconds
    return max(60, value) * 1000


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return max(minimum, default)


def _load_scheduler_lease(db: DbConn) -> dict[str, int | str] | None:
    row = db.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (_SCHEDULER_LEASE_KEY,),
    ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        data = json.loads(str(row["value"]))
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return None
    if not isinstance(data, dict):
        return None
    owner_id = str(data.get("owner_id") or "").strip()
    expires_at_ms = data.get("expires_at_ms")
    if not owner_id or not isinstance(expires_at_ms, int):
        return None
    return {
        "owner_id": owner_id,
        "expires_at_ms": expires_at_ms,
    }


def acquire_notification_scheduler_lease(
    db: DbConn,
    owner_id: str,
    *,
    now_ms: int | None = None,
    poll_seconds: int | None = None,
) -> bool:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    lease_ttl_ms = _scheduler_lease_ttl_ms(poll_seconds)
    # psycopg auto-transactions
    try:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = %s FOR UPDATE",
            (_SCHEDULER_LEASE_KEY,),
        ).fetchone()
        current = None
        if row and row["value"]:
            try:
                data = json.loads(str(row["value"]))
                if isinstance(data, dict):
                    current_owner_id = str(data.get("owner_id") or "").strip()
                    expires_at_ms = data.get("expires_at_ms")
                    if current_owner_id and isinstance(expires_at_ms, int):
                        current = {
                            "owner_id": current_owner_id,
                            "expires_at_ms": expires_at_ms,
                        }
            except (
                TypeError,
                json.JSONDecodeError,
            ):
                current = None
        if current is not None:
            expires_at_ms = int(current["expires_at_ms"])
            current_owner_id = str(current["owner_id"])
            if expires_at_ms > now_value and current_owner_id != owner_id:
                db.rollback()
                return False
        payload = json.dumps(
            {
                "owner_id": owner_id,
                "expires_at_ms": now_value + lease_ttl_ms,
            },
            separators=(",", ":"),
        )
        if row:
            db.execute(
                "UPDATE app_settings SET value = %s WHERE key = %s",
                (payload, _SCHEDULER_LEASE_KEY),
            )
        else:
            inserted = db.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                RETURNING key
                """,
                (_SCHEDULER_LEASE_KEY, payload),
            ).fetchone()
            if not inserted:
                db.rollback()
                return False
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def release_notification_scheduler_lease(
    db: DbConn,
    owner_id: str,
) -> None:
    # psycopg auto-transactions
    try:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = %s FOR UPDATE",
            (_SCHEDULER_LEASE_KEY,),
        ).fetchone()
        current = _load_scheduler_lease(db) if row else None
        if current is not None and str(current["owner_id"]) == owner_id:
            db.execute(
                "DELETE FROM app_settings WHERE key = %s",
                (_SCHEDULER_LEASE_KEY,),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_notification(
    db: DbConn,
    garden_id: int,
    user_id: int | None,
    notification_type: str,
    title: str,
    body: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict | None = None,
    notification_subtype: str | None = None,
    severity: str | None = "normal",
    expires_at_ms: int | None = None,
) -> str:
    """Insert a notification_event and return its public id."""
    notification_id = _insert_notification(
        db,
        garden_id,
        user_id,
        notification_type,
        title,
        body,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
        notification_subtype=notification_subtype,
        severity=severity,
        expires_at_ms=expires_at_ms,
    )
    db.commit()
    return notification_id


def _insert_notification(
    db: DbConn,
    garden_id: int,
    user_id: int | None,
    notification_type: str,
    title: str,
    body: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict | None = None,
    notification_subtype: str | None = None,
    severity: str | None = "normal",
    expires_at_ms: int | None = None,
) -> str:
    metadata_json = json.dumps(metadata, separators=(",", ":")) if metadata else None
    now = current_timestamp_ms()
    public_id = generate_public_id("note")
    row = db.execute(
        """
        INSERT INTO notification_events
            (public_id, garden_id, user_id, notification_type, notification_subtype,
             severity, title, body, target_type, target_id, metadata_json,
             created_at_ms, expires_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING public_id
        """,
        (
            public_id,
            garden_id,
            user_id,
            notification_type,
            notification_subtype,
            _normalize_severity(severity),
            title,
            body,
            target_type,
            target_id,
            metadata_json,
            now,
            expires_at_ms,
        ),
    ).fetchone()
    return str(row["public_id"]) if row else public_id


def _batch_task_plant_names(
    db: DbConn,
    task_ids: list[int],
) -> dict[int, list[str]]:
    """Fetch plant names for multiple tasks in one query."""
    if not task_ids:
        return {}
    placeholders = ",".join(["%s"] * len(task_ids))
    rows = db.execute(
        f"SELECT gtp.task_id, p.name "  # noqa: S608
        f"FROM garden_task_plants gtp "
        f"JOIN plants p ON p.plt_id = gtp.plt_id "
        f"WHERE gtp.task_id IN ({placeholders})",
        task_ids,
    ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["task_id"], []).append(row["name"])
    return result


def _task_metadata(
    task_title: str | None,
    plant_names: list[str],
    due_on: str,
) -> dict:
    """Build notification metadata dict for a task."""
    return {
        "task_title": task_title or "",
        "plants": plant_names,
        "plant_count": len(plant_names),
        "due_on": due_on,
    }


def _task_is_work_order(metadata_raw: object) -> bool:
    if not metadata_raw:
        return False
    try:
        metadata = json.loads(str(metadata_raw))
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return False
    return isinstance(metadata, dict) and bool(metadata.get("work_order"))


def _task_plant_label(plant_names: list[str]) -> str:
    if not plant_names:
        return ""
    if len(plant_names) <= 3:
        return ", ".join(plant_names)
    return f"{len(plant_names)} plants"


def _task_notification_title(
    *,
    label: str,
    task_title: str | None,
    plant_names: list[str],
    metadata_raw: object,
    fallback: str,
) -> str:
    if not task_title:
        return fallback
    if _task_is_work_order(metadata_raw):
        return f"{label}: {task_title}"
    plant_label = _task_plant_label(plant_names)
    if plant_label:
        return f"{label}: {task_title} ({plant_label})"
    return f"{label}: {task_title}"


def _metadata_due_on(raw: object) -> str:
    if not raw:
        return ""
    try:
        metadata = json.loads(str(raw))
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return ""
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("due_on") or "")


def _clear_active_task_notifications_for_targets(
    db: DbConn,
    *,
    garden_id: int,
    target_ids: set[str],
    reason: str,
    now_ms: int | None = None,
) -> int:
    if not target_ids:
        return 0
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    placeholders = ",".join(["%s"] * len(target_ids))
    cur = db.execute(
        f"""
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = %s
        WHERE garden_id = %s
          AND target_type = 'task'
          AND target_id IN ({placeholders})
          AND notification_type IN ('task_due', 'task_overdue', 'task_upcoming')
          AND dismissed = 0
          AND cleared_at_ms IS NULL
        """,  # noqa: S608
        [now_value, reason, garden_id, *sorted(target_ids)],
    )
    return cur.rowcount


def _stale_generated_task_public_ids(
    db: DbConn,
    *,
    garden_id: int,
    today_iso: str,
) -> set[str]:
    weather_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(_WEATHER_TASK_RULE_SOURCE_PATTERNS),
    )
    weekly_watering_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS),
    )
    rows = db.execute(
        f"""
        SELECT t.public_id
        FROM garden_tasks t
        LEFT JOIN weather_alerts wa
          ON wa.garden_id = t.garden_id
         AND wa.id = CASE
            WHEN split_part(t.rule_source, ':', 3) ~ '^[0-9]+$'
            THEN split_part(t.rule_source, ':', 3)::int
            ELSE NULL
         END
        WHERE t.garden_id = %s
          AND (
            t.status = 'pending'
            OR (
                t.status = 'snoozed'
                AND t.snoozed_until IS NOT NULL
                AND t.snoozed_until <= %s
            )
          )
          AND (
            (
              ({weekly_watering_pattern_clauses})
              AND COALESCE(t.snoozed_until, t.due_on) < %s
            )
            OR (
              ({weather_pattern_clauses})
              AND (
                COALESCE(t.snoozed_until, t.due_on) < %s
                OR wa.id IS NULL
                OR wa.valid_until < %s
              )
            )
          )
        """,  # noqa: S608
        [
            garden_id,
            today_iso,
            *GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS,
            today_iso,
            *_WEATHER_TASK_RULE_SOURCE_PATTERNS,
            today_iso,
            today_iso,
        ],
    ).fetchall()
    return {str(row["public_id"]) for row in rows}


def _load_pref_rows_for_users(
    db: DbConn,
    user_ids: list[int],
) -> dict[int, dict[str, Any]]:
    if not user_ids:
        return {}
    placeholders = ",".join(["%s"] * len(user_ids))
    rows = db.execute(
        f"""
        SELECT user_id, in_app_enabled, email_enabled, task_due_enabled,
               task_overdue_enabled, rules_json
        FROM user_notification_preferences
        WHERE user_id IN ({placeholders})
        """,  # noqa: S608
        user_ids,
    ).fetchall()
    return {int(row["user_id"]): dict(row) for row in rows}


def _rules_for_user(
    pref_rows_by_user: dict[int, dict[str, Any]],
    user_id: int,
) -> dict[str, NotificationRule]:
    return _rules_from_pref_row(pref_rows_by_user.get(user_id))


def _in_app_allowed_for_user(
    pref_rows_by_user: dict[int, dict[str, Any]],
    user_id: int,
    notification_type: str,
    notification_subtype: str | None = None,
    severity: str | None = "normal",
) -> bool:
    pref = pref_rows_by_user.get(user_id)
    if pref and not bool(pref.get("in_app_enabled", True)):
        return False
    return _notification_allowed_by_rules(
        _rules_for_user(pref_rows_by_user, user_id),
        notification_type,
        notification_subtype,
        severity,
        channel="in_app",
    )


def _clear_active_notifications_for_target(
    db: DbConn,
    *,
    garden_id: int,
    user_id: int | None,
    target_type: str,
    target_id: str,
    notification_types: tuple[str, ...],
    reason: str,
    now_ms: int | None = None,
) -> int:
    if not notification_types:
        return 0
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    placeholders = ",".join(["%s"] * len(notification_types))
    user_condition = "user_id = %s" if user_id is not None else "user_id IS NULL"
    params: list[Any] = [
        now_value,
        reason,
        garden_id,
        target_type,
        target_id,
        *notification_types,
    ]
    if user_id is not None:
        params.append(user_id)
    cur = db.execute(
        f"""
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = %s
        WHERE garden_id = %s
          AND target_type = %s
          AND target_id = %s
          AND notification_type IN ({placeholders})
          AND dismissed = 0
          AND cleared_at_ms IS NULL
          AND {user_condition}
        """,  # noqa: S608
        params,
    )
    return cur.rowcount


def create_task_due_notifications_in_transaction(
    db: DbConn,
    garden_id: int,
    *,
    task_public_ids: set[str] | None = None,
) -> dict[str, int]:
    """Check garden_tasks for tasks due today or overdue, create notifications.

    Returns {"created": N, "skipped": N, "cleared": N}.
    Deduplicates by (garden_id, user_id, notification_type, target_type, target_id)
    among non-dismissed notifications.
    """
    created = 0
    skipped = 0
    today_iso = offset_days_iso(0)
    upcoming_end_iso = offset_days_iso(3)
    task_scan_limit = _env_int("NOTIFICATION_TASK_SCAN_LIMIT", 500)
    target_filter = (
        {str(task_id).strip() for task_id in task_public_ids if str(task_id).strip()}
        if task_public_ids is not None
        else None
    )
    if target_filter is not None and not target_filter:
        return {"created": 0, "skipped": 0, "cleared": 0}
    target_filter_clause = ""
    target_filter_params: list[str] = []
    if target_filter is not None:
        target_filter_params = sorted(target_filter)
        target_placeholders = ",".join(["%s"] * len(target_filter_params))
        target_filter_clause = f" AND public_id IN ({target_placeholders})"

    members = db.execute(
        "SELECT user_id FROM garden_memberships WHERE garden_id = %s",
        (garden_id,),
    ).fetchall()
    member_ids = [int(row["user_id"]) for row in members]
    if not member_ids:
        return {"created": 0, "skipped": 0, "cleared": 0}

    stale_generated_task_ids = _stale_generated_task_public_ids(
        db,
        garden_id=garden_id,
        today_iso=today_iso,
    )
    if target_filter is not None:
        stale_generated_task_ids = [
            task_id for task_id in stale_generated_task_ids if task_id in target_filter
        ]
    cleared_stale_generated_tasks = _clear_active_task_notifications_for_targets(
        db,
        garden_id=garden_id,
        target_ids=stale_generated_task_ids,
        reason="expired",
    )

    actionable_status_clause = """
        (
            status = 'pending'
            OR (
                status = 'snoozed'
                AND snoozed_until IS NOT NULL
                AND snoozed_until <= %s
            )
        )
    """

    tasks = db.execute(
        f"""
        SELECT id, public_id, title, metadata_json,
               COALESCE(snoozed_until, due_on) AS action_on
        FROM garden_tasks
        WHERE garden_id = %s
          AND {actionable_status_clause}
          AND COALESCE(snoozed_until, due_on) <= %s
          {target_filter_clause}
        ORDER BY COALESCE(snoozed_until, due_on) ASC, updated_at_ms DESC, id ASC
        LIMIT %s
        """,
        [garden_id, today_iso, today_iso, *target_filter_params, task_scan_limit],
    ).fetchall()
    if stale_generated_task_ids:
        tasks = [row for row in tasks if str(row["public_id"]) not in stale_generated_task_ids]

    upcoming_tasks = db.execute(
        f"""
        SELECT id, public_id, title, metadata_json,
               COALESCE(snoozed_until, due_on) AS action_on
        FROM garden_tasks
        WHERE garden_id = %s
          AND {actionable_status_clause}
          AND COALESCE(snoozed_until, due_on) > %s
          AND COALESCE(snoozed_until, due_on) <= %s
          {target_filter_clause}
        ORDER BY COALESCE(snoozed_until, due_on) ASC, updated_at_ms DESC, id ASC
        LIMIT %s
        """,
        [
            garden_id,
            today_iso,
            today_iso,
            upcoming_end_iso,
            *target_filter_params,
            task_scan_limit,
        ],
    ).fetchall()
    if stale_generated_task_ids:
        upcoming_tasks = [
            row for row in upcoming_tasks if str(row["public_id"]) not in stale_generated_task_ids
        ]

    if not tasks and not upcoming_tasks:
        return {"created": 0, "skipped": 0, "cleared": cleared_stale_generated_tasks}

    today = today_iso
    pref_rows_by_user = _load_pref_rows_for_users(db, member_ids)

    all_task_ids = [r["id"] for r in tasks] + [r["id"] for r in upcoming_tasks]
    task_plants = _batch_task_plant_names(db, all_task_ids)

    target_ids = sorted({str(row["public_id"]) for row in [*tasks, *upcoming_tasks]})
    existing_keys: set[tuple[int, str, str, str]] = set()
    if target_ids:
        target_placeholders = ",".join(["%s"] * len(target_ids))
        existing_rows = db.execute(
            f"""
            SELECT user_id, notification_type, target_id, metadata_json
            FROM notification_events
            WHERE garden_id = %s
              AND user_id IS NOT NULL
              AND notification_type IN ('task_due', 'task_overdue', 'task_upcoming')
              AND target_type = 'task'
              AND target_id IN ({target_placeholders})
              AND (
                cleared_at_ms IS NULL
                OR dismissed = 1
                OR COALESCE(clear_reason, '') NOT IN ('preference_hidden', 'superseded')
              )
            """,  # noqa: S608
            [garden_id, *target_ids],
        ).fetchall()
        existing_keys = {
            (
                int(row["user_id"]),
                str(row["notification_type"]),
                str(row["target_id"]),
                _metadata_due_on(row["metadata_json"]),
            )
            for row in existing_rows
        }

    for task_row in tasks:
        task_id = int(task_row["id"])
        task_public_id = str(task_row["public_id"])
        task_title = task_row["title"]
        metadata_raw = task_row["metadata_json"]
        task_due = str(task_row["action_on"])
        plant_names = task_plants.get(task_id, [])

        is_overdue = task_due < today
        ntype = "task_overdue" if is_overdue else "task_due"
        label = "Overdue" if is_overdue else "Due today"
        ntitle = _task_notification_title(
            label=label,
            task_title=str(task_title or ""),
            plant_names=plant_names,
            metadata_raw=metadata_raw,
            fallback="Task overdue" if is_overdue else "Task due today",
        )
        nbody = f"Due on {task_due}" if is_overdue else "Due today"
        meta = _task_metadata(task_title, plant_names, task_due)

        for uid in member_ids:
            if not _in_app_allowed_for_user(pref_rows_by_user, uid, ntype):
                skipped += 1
                continue

            notification_key = (uid, ntype, task_public_id, str(task_due))
            if notification_key in existing_keys:
                skipped += 1
                continue

            expires_at_ms = _date_end_ms(today if is_overdue else task_due)
            _insert_notification(
                db,
                garden_id,
                uid,
                ntype,
                ntitle,
                nbody,
                target_type="task",
                target_id=task_public_id,
                metadata=meta,
                severity="normal",
                expires_at_ms=expires_at_ms,
            )
            existing_keys.add(notification_key)
            if ntype == "task_due":
                _clear_active_notifications_for_target(
                    db,
                    garden_id=garden_id,
                    user_id=uid,
                    target_type="task",
                    target_id=task_public_id,
                    notification_types=("task_upcoming",),
                    reason="superseded",
                )
            elif ntype == "task_overdue":
                _clear_active_notifications_for_target(
                    db,
                    garden_id=garden_id,
                    user_id=uid,
                    target_type="task",
                    target_id=task_public_id,
                    notification_types=("task_due", "task_upcoming"),
                    reason="superseded",
                )
            created += 1

    for task_row in upcoming_tasks:
        task_id = int(task_row["id"])
        task_public_id = str(task_row["public_id"])
        task_title = task_row["title"]
        metadata_raw = task_row["metadata_json"]
        task_due = str(task_row["action_on"])
        plant_names = task_plants.get(task_id, [])

        ntype = "task_upcoming"
        ntitle = _task_notification_title(
            label="Coming up",
            task_title=str(task_title or ""),
            plant_names=plant_names,
            metadata_raw=metadata_raw,
            fallback="Task coming up",
        )
        nbody = f"Due on {task_due}"
        meta = _task_metadata(task_title, plant_names, task_due)

        for uid in member_ids:
            if not _in_app_allowed_for_user(pref_rows_by_user, uid, ntype):
                skipped += 1
                continue

            notification_key = (uid, ntype, task_public_id, str(task_due))
            if notification_key in existing_keys:
                skipped += 1
                continue

            _insert_notification(
                db,
                garden_id,
                uid,
                ntype,
                ntitle,
                nbody,
                target_type="task",
                target_id=task_public_id,
                metadata=meta,
                severity="normal",
                expires_at_ms=_date_start_ms(task_due),
            )
            existing_keys.add(notification_key)
            created += 1

    return {
        "created": created,
        "skipped": skipped,
        "cleared": cleared_stale_generated_tasks,
    }


def create_task_due_notifications(
    db: DbConn,
    garden_id: int,
) -> dict[str, int]:
    """Check garden_tasks for tasks due today or overdue, create notifications.

    Returns {"created": N, "skipped": N}.
    Deduplicates by (garden_id, user_id, notification_type, target_type, target_id)
    among non-dismissed notifications.
    """
    result = create_task_due_notifications_in_transaction(db, garden_id)
    if int(result.get("created", 0)) or int(result.get("cleared", 0)):
        db.commit()
    return {
        "created": int(result.get("created", 0)),
        "skipped": int(result.get("skipped", 0)),
    }


def get_unread_count(
    db: DbConn,
    garden_id: int,
    user_id: int | None,
) -> int:
    """Count unread, non-dismissed notifications for a user in a garden."""
    now = current_timestamp_ms()
    if user_id is not None:
        rows = db.execute(
            """
            SELECT *
            FROM notification_events
            WHERE garden_id = %s
              AND user_id = %s
              AND read_at_ms IS NULL
              AND dismissed = 0
              AND cleared_at_ms IS NULL
              AND (expires_at_ms IS NULL OR expires_at_ms >= %s)
            """,
            (garden_id, user_id, now),
        ).fetchall()
        preferences = load_attention_preferences(db, user_id)
        return len(
            notification_rows_allowed_by_attention(
                [dict(row) for row in rows],
                preferences=preferences,
                surface="inbox",
                garden_id=garden_id,
                user_id=user_id,
                now_ms=now,
            )
        )
    user_condition = "AND user_id = %s" if user_id is not None else ""
    params: tuple[Any, ...] = (
        (
            garden_id,
            user_id,
            now,
        )
        if user_id is not None
        else (garden_id, now)
    )
    row = db.execute(
        f"""
        SELECT COUNT(*) AS c FROM notification_events
        WHERE garden_id = %s {user_condition}
          AND read_at_ms IS NULL
          AND dismissed = 0
          AND cleared_at_ms IS NULL
          AND (expires_at_ms IS NULL OR expires_at_ms >= %s)
        """,  # noqa: S608
        params,
    ).fetchone()
    return int(row["c"]) if row else 0


def mark_read(
    db: DbConn,
    notification_id: str,
    user_id: int | None,
    garden_id: int | None = None,
) -> bool:
    """Mark a single notification as read. Returns True if updated."""
    now = current_timestamp_ms()
    conditions = ["public_id = %s", "read_at_ms IS NULL", "cleared_at_ms IS NULL"]
    params: list[int | str] = [now, notification_id]
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    if garden_id is not None:
        conditions.append("garden_id = %s")
        params.append(garden_id)
    where = " AND ".join(conditions)
    cur = db.execute(
        f"UPDATE notification_events SET read_at_ms = %s WHERE {where}",  # noqa: S608
        params,
    )
    db.commit()
    return cur.rowcount > 0


def mark_all_read(
    db: DbConn,
    garden_id: int,
    user_id: int | None,
) -> int:
    """Mark all notifications as read for user in garden. Returns count updated."""
    now = current_timestamp_ms()
    if user_id is not None:
        rows = db.execute(
            """
            SELECT *
            FROM notification_events
            WHERE garden_id = %s
              AND user_id = %s
              AND read_at_ms IS NULL
              AND dismissed = 0
              AND cleared_at_ms IS NULL
              AND (expires_at_ms IS NULL OR expires_at_ms >= %s)
            """,
            (garden_id, user_id, now),
        ).fetchall()
        preferences = load_attention_preferences(db, user_id)
        visible_rows = notification_rows_allowed_by_attention(
            [dict(row) for row in rows],
            preferences=preferences,
            surface="inbox",
            garden_id=garden_id,
            user_id=user_id,
            now_ms=now,
        )
        if not visible_rows:
            db.commit()
            return 0
        ids = [int(row["id"]) for row in visible_rows]
        placeholders = ",".join(["%s"] * len(ids))
        cur = db.execute(
            f"""
            UPDATE notification_events SET read_at_ms = %s
            WHERE id IN ({placeholders})
            """,  # noqa: S608
            [now, *ids],
        )
    else:
        cur = db.execute(
            """
            UPDATE notification_events SET read_at_ms = %s
            WHERE garden_id = %s
              AND read_at_ms IS NULL
              AND dismissed = 0
              AND cleared_at_ms IS NULL
            """,
            (now, garden_id),
        )
    db.commit()
    return cur.rowcount


def dismiss_notification(
    db: DbConn,
    notification_id: str,
    user_id: int | None,
    garden_id: int | None = None,
) -> bool:
    """Dismiss a notification (soft-delete). Returns True if updated."""
    conditions = ["public_id = %s", "dismissed = 0"]
    params: list[int | str] = [current_timestamp_ms(), "manual_dismiss", notification_id]
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    if garden_id is not None:
        conditions.append("garden_id = %s")
        params.append(garden_id)
    where = " AND ".join(conditions)
    cur = db.execute(
        f"""
        UPDATE notification_events
        SET dismissed = 1,
            cleared_at_ms = COALESCE(cleared_at_ms, %s),
            clear_reason = COALESCE(clear_reason, %s)
        WHERE {where}
        """,  # noqa: S608
        params,
    )
    db.commit()
    return cur.rowcount > 0


def clear_expired_notifications(
    db: DbConn,
    *,
    garden_id: int | None = None,
    user_id: int | None = None,
    now_ms: int | None = None,
) -> int:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    conditions = [
        "dismissed = 0",
        "cleared_at_ms IS NULL",
        "expires_at_ms IS NOT NULL",
        "expires_at_ms < %s",
    ]
    params: list[Any] = [now_value, "expired", now_value]
    if garden_id is not None:
        conditions.append("garden_id = %s")
        params.append(garden_id)
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    where = " AND ".join(conditions)
    cur = db.execute(
        f"""
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = %s
        WHERE {where}
        """,  # noqa: S608
        params,
    )
    return cur.rowcount


def clear_stale_task_notifications(
    db: DbConn,
    *,
    garden_id: int | None = None,
    user_id: int | None = None,
    today_iso: str | None = None,
    now_ms: int | None = None,
) -> int:
    """Clear task notifications whose task target is no longer inbox-actionable."""
    today = today_iso or date.today().isoformat()
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    today_start_ms = _date_start_ms(today) or now_value
    weather_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(_WEATHER_TASK_RULE_SOURCE_PATTERNS),
    )
    weekly_watering_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS),
    )
    stale_generated_clause = (
        "("
        f"(({weekly_watering_pattern_clauses}) AND COALESCE(t.snoozed_until, t.due_on) < %s)"
        " OR "
        f"(({weather_pattern_clauses}) AND "
        "(COALESCE(t.snoozed_until, t.due_on) < %s OR wa.valid_until < %s OR wa.id IS NULL))"
        ")"
    )
    conditions = [
        "n.dismissed = 0",
        "n.cleared_at_ms IS NULL",
        "n.target_type = 'task'",
        "n.notification_type IN ('task_due', 'task_overdue', 'task_upcoming')",
    ]
    filter_params: list[Any] = []
    if garden_id is not None:
        conditions.append("n.garden_id = %s")
        filter_params.append(garden_id)
    if user_id is not None:
        conditions.append("n.user_id = %s")
        filter_params.append(user_id)

    where = " AND ".join(conditions)
    stale_conditions = " OR ".join(
        [
            "t.id IS NULL",
            "(t.status = 'snoozed' AND (t.snoozed_until IS NULL OR t.snoozed_until > %s))",
            "t.status NOT IN ('pending', 'snoozed')",
            "COALESCE(t.snoozed_until, t.due_on) IS NULL",
            stale_generated_clause,
            "(n.notification_type = 'task_due' AND COALESCE(t.snoozed_until, t.due_on) <> %s)",
            "(n.notification_type = 'task_upcoming' AND COALESCE(t.snoozed_until, t.due_on) <= %s)",
            "(n.notification_type = 'task_overdue' AND COALESCE(t.snoozed_until, t.due_on) >= %s)",
            "(n.notification_type = 'task_overdue' AND n.created_at_ms < %s)",
        ],
    )
    reason_case = f"""
        CASE
            WHEN t.id IS NULL THEN 'deleted'
            WHEN t.status = 'completed' THEN 'completed'
            WHEN t.status = 'skipped' THEN 'skipped'
            WHEN t.status = 'snoozed'
                AND (t.snoozed_until IS NULL OR t.snoozed_until > %s) THEN 'snoozed'
            WHEN t.status NOT IN ('pending', 'snoozed') THEN 'expired'
            WHEN COALESCE(t.snoozed_until, t.due_on) IS NULL THEN 'expired'
            WHEN {stale_generated_clause} THEN 'expired'
            WHEN n.notification_type = 'task_due'
                AND COALESCE(t.snoozed_until, t.due_on) <> %s THEN 'expired'
            WHEN n.notification_type = 'task_upcoming'
                AND COALESCE(t.snoozed_until, t.due_on) <= %s THEN 'expired'
            WHEN n.notification_type = 'task_overdue'
                AND COALESCE(t.snoozed_until, t.due_on) >= %s THEN 'superseded'
            WHEN n.notification_type = 'task_overdue' AND n.created_at_ms < %s THEN 'expired'
            ELSE 'expired'
        END
    """
    cur = db.execute(
        f"""
        WITH candidates AS (
            SELECT
                n.id,
                %s::bigint AS cleared_at_ms,
                {reason_case} AS reason
            FROM notification_events n
            LEFT JOIN garden_tasks t
              ON t.garden_id = n.garden_id
             AND t.public_id = n.target_id
            LEFT JOIN weather_alerts wa
              ON wa.garden_id = t.garden_id
             AND wa.id = CASE
                WHEN split_part(t.rule_source, ':', 3) ~ '^[0-9]+$'
                THEN split_part(t.rule_source, ':', 3)::int
                ELSE NULL
             END
            WHERE {where}
              AND ({stale_conditions})
        )
        UPDATE notification_events n
        SET cleared_at_ms = candidates.cleared_at_ms,
            clear_reason = candidates.reason
        FROM candidates
        WHERE n.id = candidates.id
        """,  # noqa: S608
        [
            now_value,
            today,
            *GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS,
            today,
            *_WEATHER_TASK_RULE_SOURCE_PATTERNS,
            today,
            today,
            today,
            today,
            today,
            today_start_ms,
            *filter_params,
            today,
            *GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS,
            today,
            *_WEATHER_TASK_RULE_SOURCE_PATTERNS,
            today,
            today,
            today,
            today,
            today,
            today_start_ms,
        ],
    )
    return cur.rowcount


def clear_stale_informational_notifications(
    db: DbConn,
    *,
    garden_id: int | None = None,
    user_id: int | None = None,
    today_iso: str | None = None,
    now_ms: int | None = None,
) -> int:
    """Clear informational notifications that are no longer current."""
    today = today_iso or date.today().isoformat()
    today_start_ms = _date_start_ms(today) or current_timestamp_ms()
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    conditions = [
        "dismissed = 0",
        "cleared_at_ms IS NULL",
        "((notification_type = 'task_generated') "
        "OR (notification_type = 'system' AND title LIKE 'Smoke %%'))",
        "created_at_ms < %s",
    ]
    params: list[Any] = [now_value, "expired", today_start_ms]
    if garden_id is not None:
        conditions.append("garden_id = %s")
        params.append(garden_id)
    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)
    where = " AND ".join(conditions)
    cur = db.execute(
        f"""
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = %s
        WHERE {where}
        """,  # noqa: S608
        params,
    )
    return cur.rowcount


def clear_task_notifications(
    db: DbConn,
    *,
    garden_id: int,
    task_public_id: str,
    reason: str,
    now_ms: int | None = None,
) -> int:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    cur = db.execute(
        """
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = %s
        WHERE garden_id = %s
          AND target_type = 'task'
          AND target_id = %s
          AND notification_type IN ('task_due', 'task_overdue', 'task_upcoming')
          AND dismissed = 0
          AND cleared_at_ms IS NULL
        """,
        (now_value, reason, garden_id, task_public_id),
    )
    return cur.rowcount


def refresh_task_notifications_for_task(
    db: DbConn,
    *,
    garden_id: int,
    task_public_id: str,
    now_ms: int | None = None,
) -> dict[str, int]:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    cleared = clear_task_notifications(
        db,
        garden_id=garden_id,
        task_public_id=task_public_id,
        reason="superseded",
        now_ms=now_value,
    )
    result = create_task_due_notifications_in_transaction(
        db,
        garden_id,
        task_public_ids={task_public_id},
    )
    return {
        "cleared": cleared,
        "created": int(result.get("created", 0)),
        "skipped": int(result.get("skipped", 0)),
    }


def clear_issue_notifications(
    db: DbConn,
    *,
    garden_id: int,
    issue_public_id: str,
    reason: str = "resolved",
    now_ms: int | None = None,
) -> int:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    cur = db.execute(
        """
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = %s
        WHERE garden_id = %s
          AND target_type = 'issue'
          AND target_id = %s
          AND notification_type = 'issue_created'
          AND dismissed = 0
          AND cleared_at_ms IS NULL
        """,
        (now_value, reason, garden_id, issue_public_id),
    )
    return cur.rowcount


def clear_notifications_hidden_by_preferences(
    db: DbConn,
    *,
    user_id: int,
    in_app_enabled: bool,
    rules: dict[str, NotificationRule],
    now_ms: int | None = None,
) -> int:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    total = 0
    if not in_app_enabled:
        cur = db.execute(
            """
            UPDATE notification_events
            SET cleared_at_ms = %s,
                clear_reason = 'preference_hidden'
            WHERE user_id = %s
              AND dismissed = 0
              AND cleared_at_ms IS NULL
            """,
            (now_value, user_id),
        )
        return cur.rowcount

    for key, policy in _POLICIES_BY_KEY.items():
        if not bool(policy["user_configurable"]):
            continue
        rule = rules.get(key, default_notification_rules().get(key, {}))
        min_severity = _normalize_severity(str(rule.get("min_severity", "low")))
        disabled = not bool(rule.get("in_app_enabled", True))
        severity_floor = _SEVERITY_RANK[min_severity]
        if not disabled and severity_floor <= _SEVERITY_RANK["low"]:
            continue

        subtype = policy["notification_subtype"]
        subtype_sql = (
            "notification_subtype IS NULL" if subtype is None else "notification_subtype = %s"
        )
        params: list[Any] = [
            now_value,
            user_id,
            str(policy["notification_type"]),
        ]
        if subtype is not None:
            params.append(str(subtype))
        severity_filter = ""
        if not disabled:
            allowed = [sev for sev, rank in _SEVERITY_RANK.items() if rank < severity_floor]
            if not allowed:
                continue
            placeholders = ",".join(["%s"] * len(allowed))
            severity_filter = f" AND COALESCE(severity, 'normal') IN ({placeholders})"
            params.extend(allowed)

        cur = db.execute(
            f"""
            UPDATE notification_events
            SET cleared_at_ms = %s,
                clear_reason = 'preference_hidden'
            WHERE user_id = %s
              AND notification_type = %s
              AND {subtype_sql}
              AND dismissed = 0
              AND cleared_at_ms IS NULL
              {severity_filter}
            """,  # noqa: S608
            params,
        )
        total += cur.rowcount
    return total


def create_garden_member_notifications(
    db: DbConn,
    *,
    garden_id: int,
    notification_type: str,
    title: str,
    body: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict | None = None,
    notification_subtype: str | None = None,
    severity: str | None = "normal",
    expires_at_ms: int | None = None,
    exclude_user_id: int | None = None,
) -> dict[str, int]:
    members = db.execute(
        "SELECT user_id FROM garden_memberships WHERE garden_id = %s",
        (garden_id,),
    ).fetchall()
    member_ids = [int(row["user_id"]) for row in members]
    if exclude_user_id is not None:
        member_ids = [uid for uid in member_ids if uid != exclude_user_id]
    if not member_ids:
        return {"created": 0, "skipped": 0}

    pref_rows_by_user = _load_pref_rows_for_users(db, member_ids)
    placeholders = ",".join(["%s"] * len(member_ids))
    target_type_key = target_type or ""
    target_id_key = target_id or ""
    existing_rows = db.execute(
        f"""
        SELECT user_id
        FROM notification_events
        WHERE garden_id = %s
          AND user_id IN ({placeholders})
          AND notification_type = %s
          AND COALESCE(notification_subtype, '') = %s
          AND COALESCE(target_type, '') = %s
          AND COALESCE(target_id, '') = %s
          AND dismissed = 0
          AND cleared_at_ms IS NULL
        """,  # noqa: S608
        [
            garden_id,
            *member_ids,
            notification_type,
            notification_subtype or "",
            target_type_key,
            target_id_key,
        ],
    ).fetchall()
    existing_user_ids = {int(row["user_id"]) for row in existing_rows}

    created = 0
    skipped = 0
    for uid in member_ids:
        if uid in existing_user_ids:
            skipped += 1
            continue
        if not _in_app_allowed_for_user(
            pref_rows_by_user,
            uid,
            notification_type,
            notification_subtype,
            severity,
        ):
            skipped += 1
            continue
        _insert_notification(
            db,
            garden_id,
            uid,
            notification_type,
            title,
            body,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
            notification_subtype=notification_subtype,
            severity=severity,
            expires_at_ms=expires_at_ms,
        )
        created += 1
    return {"created": created, "skipped": skipped}


def create_issue_created_notifications(
    db: DbConn,
    *,
    garden_id: int,
    issue_public_id: str,
    title: str,
    body: str,
    severity: str,
    actor_user_id: int | None,
) -> dict[str, int]:
    return create_garden_member_notifications(
        db,
        garden_id=garden_id,
        notification_type="issue_created",
        title=title or "Issue reported",
        body=body or "A garden issue was reported.",
        target_type="issue",
        target_id=issue_public_id,
        metadata={
            "issue_title": title,
            "issue_severity": severity,
        },
        severity=severity,
        exclude_user_id=actor_user_id,
    )


def create_weather_alert_notifications(
    db: DbConn,
    *,
    garden_id: int,
    alerts: list[dict[str, Any]],
) -> dict[str, int]:
    created = 0
    skipped = 0
    for alert in alerts:
        alert_type = str(alert.get("alert_type") or "")
        valid_from = str(alert.get("valid_from") or "")
        if not alert_type or not valid_from:
            skipped += 1
            continue
        alert_row = db.execute(
            """
            SELECT id, valid_until
            FROM weather_alerts
            WHERE garden_id = %s
              AND alert_type = %s
              AND valid_from = %s
              AND dismissed = 0
            ORDER BY id DESC LIMIT 1
            """,
            (garden_id, alert_type, valid_from),
        ).fetchone()
        if not alert_row:
            skipped += 1
            continue
        valid_until = str(alert.get("valid_until") or alert_row["valid_until"])
        result = create_garden_member_notifications(
            db,
            garden_id=garden_id,
            notification_type="weather_alert",
            notification_subtype=alert_type,
            severity=str(alert.get("severity") or "normal"),
            title=str(alert.get("title") or "Weather alert"),
            body=str(alert.get("description") or ""),
            target_type="weather_alert",
            target_id=f"{alert_type}:{valid_from}",
            expires_at_ms=_date_end_ms(valid_until),
            metadata={
                "alert_type": alert_type,
                "severity": str(alert.get("severity") or "normal"),
                "valid_from": valid_from,
                "valid_until": valid_until,
            },
        )
        created += result["created"]
        skipped += result["skipped"]
    return {"created": created, "skipped": skipped}


def _smtp_settings() -> dict[str, object] | None:
    host = os.getenv("GARDENOPS_SMTP_HOST", "").strip()
    sender = os.getenv("GARDENOPS_SMTP_FROM", "").strip()
    if not host or not sender:
        return None
    port = int(os.getenv("GARDENOPS_SMTP_PORT", "587"))
    username = os.getenv("GARDENOPS_SMTP_USERNAME", "").strip()
    password = os.getenv("GARDENOPS_SMTP_PASSWORD", "")
    use_tls = os.getenv("GARDENOPS_SMTP_TLS", "true").strip().lower() != "false"
    if not use_tls and username:
        logger.warning(
            "SMTP TLS is disabled but credentials are configured — "
            "credentials will be sent in plaintext to %s:%s",
            host,
            port,
        )
    return {
        "host": host,
        "port": port,
        "sender": sender,
        "username": username,
        "password": password,
        "use_tls": use_tls,
    }


def _default_email_sender(recipient: str, subject: str, body: str) -> None:
    import smtplib

    settings = _smtp_settings()
    if not settings:
        raise RuntimeError("SMTP is not configured")

    message = EmailMessage()
    message["From"] = str(settings["sender"])
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    port = int(cast(int | float | str, settings["port"]))
    with smtplib.SMTP(str(settings["host"]), port, timeout=20) as smtp:
        smtp.ehlo()
        if bool(settings["use_tls"]):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        username = str(settings["username"])
        if username:
            smtp.login(username, str(settings["password"]))
        smtp.send_message(message)


def _parse_quiet_hours(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        logger.warning("Failed to parse quiet_hours_json: %r", raw)
        return None
    if not isinstance(data, dict):
        logger.warning("quiet_hours_json is not a dict: %r", raw)
        return None

    start = data.get("start") or data.get("from")
    end = data.get("end") or data.get("to")
    if isinstance(start, str) and isinstance(end, str):
        try:
            start_hour = int(start.split(":")[0])
            end_hour = int(end.split(":")[0])
            if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
                return start_hour, end_hour
        except Exception:
            logger.warning("Could not parse quiet hours time strings from: %r", raw)
            return None
    if isinstance(data.get("start_hour"), int) and isinstance(data.get("end_hour"), int):
        start_hour = int(data["start_hour"])
        end_hour = int(data["end_hour"])
        if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
            return start_hour, end_hour
    logger.warning("Could not extract valid quiet hours from: %r", raw)
    return None


def _is_quiet_hours(now_utc: datetime, raw: str | None) -> bool:
    quiet = _parse_quiet_hours(raw)
    if quiet is None:
        return False
    start_hour, end_hour = quiet
    current_hour = now_utc.hour
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour
    return current_hour >= start_hour or current_hour < end_hour


def _digest_interval_ms(frequency: str) -> int | None:
    if frequency == "daily":
        return int(timedelta(days=1).total_seconds() * 1000)
    if frequency == "weekly":
        return int(timedelta(days=7).total_seconds() * 1000)
    return None


def _build_digest_email_body(notifications: list[dict[str, Any]]) -> str:
    product_name = app_name()
    lines = [
        f"You have new {product_name} reminders:",
        "",
    ]
    for row in notifications:
        title = str(row["title"] or "Garden update")
        body = str(row["body"] or "").strip()
        created_at = datetime.fromtimestamp(int(row["created_at_ms"]) / 1000, tz=UTC)
        lines.append(f"- {title} ({created_at.date().isoformat()})")
        if body:
            lines.append(f"  {body}")
    lines.extend(
        [
            "",
            f"Open {product_name} to review and resolve these items.",
        ]
    )
    return "\n".join(lines)


def deliver_pending_email_digests(
    db: DbConn,
    garden_id: int,
    *,
    email_sender: EmailSender | None = None,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    settings = _smtp_settings()
    if email_sender is None:
        if settings is None:
            return {
                "configured": False,
                "processed_users": 0,
                "emailed_users": 0,
                "notifications_marked": 0,
                "skipped_users": 0,
            }
        email_sender = _default_email_sender

    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    now_utc = datetime.fromtimestamp(now_value / 1000, tz=UTC)
    processed_users = 0
    emailed_users = 0
    notifications_marked = 0
    skipped_users = 0
    max_events_per_user = _env_int("NOTIFICATION_DIGEST_MAX_EVENTS_PER_USER", 100)

    recipients = db.execute(
        """
        SELECT p.user_id,
               p.email_address,
               p.digest_frequency,
               p.quiet_hours_json,
               p.last_email_digest_at_ms,
               p.rules_json,
               p.task_due_enabled,
               p.task_overdue_enabled
        FROM user_notification_preferences p
        JOIN garden_memberships gm ON gm.user_id = p.user_id
        JOIN auth_users u ON u.id = p.user_id
        WHERE gm.garden_id = %s
          AND u.subscription_tier = 'pro'
          AND p.email_enabled = 1
          AND TRIM(p.email_address) != ''
          AND p.digest_frequency IN ('daily', 'weekly')
        ORDER BY p.user_id
        """,
        (garden_id,),
    ).fetchall()

    for pref in recipients:
        processed_users += 1
        digest_frequency = str(pref["digest_frequency"])
        interval_ms = _digest_interval_ms(digest_frequency)
        last_sent = pref["last_email_digest_at_ms"]
        if interval_ms is not None and last_sent and now_value - int(last_sent) < interval_ms:
            skipped_users += 1
            continue
        if _is_quiet_hours(now_utc, pref["quiet_hours_json"]):
            skipped_users += 1
            continue

        notifications = db.execute(
            """
            SELECT id, public_id, garden_id, user_id, notification_type,
                   notification_subtype, severity, title, body, target_type,
                   target_id, metadata_json, created_at_ms
            FROM notification_events
            WHERE garden_id = %s
              AND emailed_at_ms IS NULL
              AND dismissed = 0
              AND cleared_at_ms IS NULL
              AND read_at_ms IS NULL
              AND user_id = %s
              AND (expires_at_ms IS NULL OR expires_at_ms >= %s)
            ORDER BY created_at_ms ASC
            """,
            (garden_id, int(pref["user_id"]), now_value),
        ).fetchall()
        rules = _rules_from_pref_row(dict(pref))
        notifications = [
            row
            for row in notifications
            if _notification_allowed_by_rules(
                rules,
                str(row["notification_type"]),
                str(row["notification_subtype"]) if row["notification_subtype"] else None,
                str(row["severity"]) if row["severity"] else None,
                channel="email",
            )
        ]
        attention_preferences = load_attention_preferences(db, int(pref["user_id"]))
        notifications = notification_rows_allowed_by_attention(
            [dict(row) for row in notifications],
            preferences=attention_preferences,
            surface="digest",
            garden_id=garden_id,
            user_id=int(pref["user_id"]),
            now_ms=now_value,
        )
        notifications = notifications[:max_events_per_user]
        if not notifications:
            continue

        plural = "s" if len(notifications) != 1 else ""
        garden_name_row = db.execute(
            "SELECT name FROM gardens WHERE id = %s",
            (garden_id,),
        ).fetchone()
        garden_name = str(garden_name_row["name"]) if garden_name_row else "Garden"
        subject = f"{app_name()} digest \u2014 {garden_name}: {len(notifications)} update{plural}"
        body = _build_digest_email_body(notifications)
        try:
            email_sender(str(pref["email_address"]), subject, body)
        except Exception:
            skipped_users += 1
            logger.warning(
                "Failed to deliver notification digest for user_id=%s",
                pref["user_id"],
                exc_info=True,
            )
            continue

        ids = [int(row["id"]) for row in notifications]
        placeholders = ",".join(["%s"] * len(ids))
        db.execute(
            f"UPDATE notification_events SET emailed_at_ms = %s WHERE id IN ({placeholders})",
            [now_value, *ids],
        )
        db.execute(
            """
            UPDATE user_notification_preferences
            SET last_email_digest_at_ms = %s, updated_at_ms = %s
            WHERE user_id = %s
            """,
            (now_value, now_value, int(pref["user_id"])),
        )
        emailed_users += 1
        notifications_marked += len(ids)

    db.commit()
    return {
        "configured": True,
        "processed_users": processed_users,
        "emailed_users": emailed_users,
        "notifications_marked": notifications_marked,
        "skipped_users": skipped_users,
    }


_WEATHER_CHECK_COOLDOWN_MS = 3 * 60 * 60 * 1000  # 3 hours


def _run_weather_check_if_due(
    db: DbConn,
    garden_id: int,
    now_ms: int,
) -> dict[str, int | bool]:
    """Run a weather check for a garden if the cooldown has elapsed."""
    settings_key = f"last_weather_check_ms:{garden_id}"
    row = db.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (settings_key,),
    ).fetchone()
    if row and row["value"]:
        try:
            last_check = int(row["value"])
        except (
            ValueError,
            TypeError,
        ):
            last_check = 0
        if now_ms - last_check < _WEATHER_CHECK_COOLDOWN_MS:
            return {"weather_skipped": True}

    garden = db.execute(
        "SELECT latitude, longitude FROM gardens WHERE id = %s",
        (garden_id,),
    ).fetchone()
    if not garden or garden["latitude"] is None or garden["longitude"] is None:
        return {"weather_skipped": True}

    lat = float(garden["latitude"])
    lon = float(garden["longitude"])
    result = check_weather_and_generate_alerts(db, garden_id, lat, lon)
    notification_result = create_weather_alert_notifications(
        db,
        garden_id=garden_id,
        alerts=list(result.get("alerts", [])),
    )

    alert_type_handlers = {
        "frost_warning": on_frost_alert,
        "heat_wave": on_heat_alert,
        "dry_spell": on_dry_spell_alert,
        "rain_surplus": on_rain_alert,
    }
    for alert in result.get("alerts", []):
        handler = alert_type_handlers.get(alert.get("alert_type", ""))
        if not handler:
            continue
        alert_row = db.execute(
            """
            SELECT id FROM weather_alerts
            WHERE garden_id = %s AND alert_type = %s
              AND valid_from = %s AND dismissed = 0
            ORDER BY id DESC LIMIT 1
            """,
            (garden_id, alert["alert_type"], alert["valid_from"]),
        ).fetchone()
        if alert_row:
            handler(db, garden_id, int(alert_row["id"]), None)

    db.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (settings_key, str(now_ms)),
    )
    db.commit()
    return {
        "weather_checks": 1,
        "weather_alerts_created": result.get("alerts_created", 0),
        "weather_notifications_created": notification_result.get("created", 0),
        "weather_notifications_skipped": notification_result.get("skipped", 0),
    }


def _auto_generate_monthly_tasks(
    db: DbConn,
    garden_id: int,
    now_ms: int,
) -> dict[str, int | bool]:
    """Generate seasonal tasks once per calendar month per garden."""
    today = datetime.fromtimestamp(now_ms / 1000, tz=UTC).date()
    month_key = f"{today.year}-{today.month:02d}"
    settings_key = f"last_task_gen_month:{garden_id}"

    row = db.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (settings_key,),
    ).fetchone()
    if row and str(row["value"]) == month_key:
        return {"tasks_skipped": True}

    result = generate_tasks(
        db,
        garden_id,
        today.month,
        today.year,
        actor_user_id=None,
    )

    db.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (settings_key, month_key),
    )
    db.commit()

    notification_result = {"created": 0, "skipped": 0}
    if result.get("created", 0) > 0:
        month_name = datetime.fromtimestamp(
            now_ms / 1000,
            tz=UTC,
        ).strftime("%B %Y")
        notification_result = create_garden_member_notifications(
            db,
            garden_id=garden_id,
            notification_type="task_generated",
            title=f"{result['created']} seasonal tasks generated",
            body=f"Tasks for {month_name} are ready.",
            target_type="task_batch",
            target_id=month_key,
            metadata={
                "month": month_key,
                "created": int(result.get("created", 0)),
            },
            expires_at_ms=_date_end_ms(today.isoformat()),
        )

    return {
        "tasks_created": result.get("created", 0),
        "tasks_skipped_dedup": result.get("skipped", 0),
        "notifications_created": notification_result.get("created", 0),
        "notifications_skipped": notification_result.get("skipped", 0),
    }


def _empty_maintenance_summary() -> dict[str, int | bool]:
    return {
        "gardens_processed": 0,
        "notifications_created": 0,
        "notifications_skipped": 0,
        "tasks_auto_created": 0,
        "tasks_expired": 0,
        "weather_checks": 0,
        "weather_alerts_created": 0,
        "issues_escalated": 0,
        "processed_users": 0,
        "emailed_users": 0,
        "notifications_marked": 0,
        "delivery_skipped_users": 0,
        "configured": False,
    }


def _run_notification_maintenance_for_gardens(
    db: DbConn,
    *,
    garden_ids: list[int],
    email_sender: EmailSender | None = None,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    summary = _empty_maintenance_summary()
    for garden_id in garden_ids:
        summary["gardens_processed"] = int(summary["gardens_processed"]) + 1

        tasks_expired = expire_stale_generated_tasks(
            db,
            garden_id=garden_id,
            now_ms=now_value,
        )
        summary["tasks_expired"] = int(summary["tasks_expired"]) + tasks_expired

        generated = create_task_due_notifications(db, garden_id)
        summary["notifications_created"] = int(summary["notifications_created"]) + int(
            generated["created"]
        )
        summary["notifications_skipped"] = int(summary["notifications_skipped"]) + int(
            generated["skipped"]
        )

        expired = clear_expired_notifications(db, garden_id=garden_id, now_ms=now_value)
        summary["notifications_marked"] = int(summary["notifications_marked"]) + expired
        stale_tasks = clear_stale_task_notifications(
            db,
            garden_id=garden_id,
            now_ms=now_value,
        )
        summary["notifications_marked"] = int(summary["notifications_marked"]) + stale_tasks
        stale_info = clear_stale_informational_notifications(
            db,
            garden_id=garden_id,
            now_ms=now_value,
        )
        summary["notifications_marked"] = int(summary["notifications_marked"]) + stale_info
        if tasks_expired or expired or stale_tasks or stale_info:
            db.commit()

        gen_result = _auto_generate_monthly_tasks(db, garden_id, now_value)
        summary["tasks_auto_created"] = int(summary["tasks_auto_created"]) + int(
            gen_result.get("tasks_created", 0)
        )
        summary["notifications_created"] = int(summary["notifications_created"]) + int(
            gen_result.get("notifications_created", 0)
        )
        summary["notifications_skipped"] = int(summary["notifications_skipped"]) + int(
            gen_result.get("notifications_skipped", 0)
        )
        if int(gen_result.get("notifications_created", 0)) > 0:
            db.commit()

        weather_result = _run_weather_check_if_due(db, garden_id, now_value)
        summary["weather_checks"] = int(summary["weather_checks"]) + int(
            weather_result.get("weather_checks", 0)
        )
        summary["weather_alerts_created"] = int(summary["weather_alerts_created"]) + int(
            weather_result.get("weather_alerts_created", 0)
        )
        summary["notifications_created"] = int(summary["notifications_created"]) + int(
            weather_result.get("weather_notifications_created", 0)
        )
        summary["notifications_skipped"] = int(summary["notifications_skipped"]) + int(
            weather_result.get("weather_notifications_skipped", 0)
        )

        escalation_result = escalate_overdue_follow_ups(db, garden_id)
        summary["issues_escalated"] = int(summary.get("issues_escalated", 0)) + int(
            escalation_result.get("escalated", 0)
        )

        delivered = deliver_pending_email_digests(
            db,
            garden_id,
            email_sender=email_sender,
            now_ms=now_ms,
        )
        summary["configured"] = bool(summary["configured"]) or bool(delivered["configured"])
        summary["processed_users"] = int(summary["processed_users"]) + int(
            delivered["processed_users"]
        )
        summary["emailed_users"] = int(summary["emailed_users"]) + int(delivered["emailed_users"])
        summary["notifications_marked"] = int(summary["notifications_marked"]) + int(
            delivered["notifications_marked"]
        )
        summary["delivery_skipped_users"] = int(summary["delivery_skipped_users"]) + int(
            delivered["skipped_users"]
        )

    return summary


def run_notification_maintenance_for_garden(
    db: DbConn,
    *,
    garden_id: int,
    email_sender: EmailSender | None = None,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    """Generate reminders and deliver pending digests for one garden."""
    exists = db.execute("SELECT 1 FROM gardens WHERE id = %s LIMIT 1", (garden_id,)).fetchone()
    if not exists:
        return _empty_maintenance_summary()
    return _run_notification_maintenance_for_gardens(
        db,
        garden_ids=[garden_id],
        email_sender=email_sender,
        now_ms=now_ms,
    )


def run_notification_maintenance_once(
    db: DbConn,
    *,
    email_sender: EmailSender | None = None,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    """Generate in-app reminders and deliver pending digests across all gardens."""
    garden_rows = db.execute(
        """
        SELECT id
        FROM gardens
        ORDER BY CASE WHEN slug = 'default' THEN 0 ELSE 1 END, id ASC
        """,
    ).fetchall()
    return _run_notification_maintenance_for_gardens(
        db,
        garden_ids=[int(row["id"]) for row in garden_rows],
        email_sender=email_sender,
        now_ms=now_ms,
    )
