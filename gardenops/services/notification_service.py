"""Notification service – create, query, and manage notification events."""

from __future__ import annotations

import hashlib
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
from gardenops.feature_gates import feature_allowed
from gardenops.router_helpers import generate_public_id
from gardenops.services.attention.preferences import (
    AttentionPreferenceSet,
    AttentionSurface,
    apply_preferences,
)
from gardenops.services.attention.service import load_attention_preferences
from gardenops.services.attention.types import (
    SEVERITY_RANK,
    AttentionCategory,
    AttentionItem,
    attention_request_clock,
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
from gardenops.services.task_generator import generate_tasks, reconcile_rain_watering_outcomes
from gardenops.services.weather_service import check_weather_and_generate_alerts
from gardenops.sql_dates import offset_days_iso

EmailSender = Callable[[str, str, str], None]
logger = logging.getLogger(__name__)
_SCHEDULER_LEASE_KEY = "notification_scheduler_lease"
_TASK_NOTIFICATION_LOCK_SEED = 0x474F504E4F544946
_DIGEST_DELIVERY_LOCK_SEED = 0x474F504449474553

NotificationRule = dict[str, bool | str]

_WEATHER_TASK_RULE_SOURCE_PATTERNS = (
    "auto:frost_protect:%",
    "auto:heat_protect:%",
    "auto:dry_water:%",
    "auto:rain_drainage:%",
)
_DRY_WEATHER_TASK_RULE_SOURCE_PATTERN = "auto:dry_water:%"
_NON_DRY_WEATHER_TASK_RULE_SOURCE_PATTERNS = tuple(
    pattern
    for pattern in _WEATHER_TASK_RULE_SOURCE_PATTERNS
    if pattern != _DRY_WEATHER_TASK_RULE_SOURCE_PATTERN
)
_FORECAST_ALERT_TYPES = frozenset({"frost_warning", "heat_wave", "dry_spell", "rain_surplus"})
_FORECAST_RECONCILIATION_SCOPE_KEY = "_forecast_reconciliation_scope"
_WEATHER_TASK_PREFIX_BY_ALERT_TYPE = {
    "frost_warning": "frost_protect",
    "heat_wave": "heat_protect",
    "dry_spell": "dry_water",
    "rain_surplus": "rain_drainage",
}

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
        if isinstance(value.get("in_app_enabled"), bool):
            rule["in_app_enabled"] = value["in_app_enabled"]
        if isinstance(value.get("email_enabled"), bool):
            rule["email_enabled"] = value["email_enabled"]
        min_severity = value.get("min_severity")
        if isinstance(min_severity, str) and min_severity.strip().lower() in SEVERITY_RANK:
            rule["min_severity"] = _normalize_severity(min_severity)
        rules[key] = rule
    return rules


def validate_notification_rules(raw: dict[str, Any]) -> None:
    """Reject malformed nested notification rules before they can be normalized."""
    for key, rule in raw.items():
        if not isinstance(key, str) or key not in _POLICIES_BY_KEY:
            raise ValueError(f"Unsupported notification rule: {key}")
        if not isinstance(rule, dict):
            raise ValueError(f"notification_rules.{key} must be an object")
        unknown_fields = set(rule) - {"in_app_enabled", "email_enabled", "min_severity"}
        if unknown_fields:
            invalid = ", ".join(sorted(str(field) for field in unknown_fields))
            raise ValueError(f"notification_rules.{key} has unsupported fields: {invalid}")
        for field in ("in_app_enabled", "email_enabled"):
            if field in rule and not isinstance(rule[field], bool):
                raise ValueError(f"notification_rules.{key}.{field} must be a boolean")
        if "min_severity" in rule:
            severity = rule["min_severity"]
            if not isinstance(severity, str) or severity.strip().lower() not in SEVERITY_RANK:
                raise ValueError(
                    f"notification_rules.{key}.min_severity must be a supported severity"
                )
        if not bool(_POLICIES_BY_KEY[key]["user_configurable"]):
            default_rule = default_notification_rules()[key]
            normalized_rule = {
                "in_app_enabled": rule.get("in_app_enabled", default_rule["in_app_enabled"]),
                "email_enabled": rule.get("email_enabled", default_rule["email_enabled"]),
                "min_severity": str(rule.get("min_severity", default_rule["min_severity"]))
                .strip()
                .lower(),
            }
            if normalized_rule != default_rule:
                raise ValueError(f"Notification rule is not user configurable: {key}")


def notification_rules_json(raw: dict[str, Any] | None) -> str:
    return json.dumps(normalize_notification_rules(raw), separators=(",", ":"))


def _normalize_severity(severity: str | None) -> str:
    return str(normalize_severity(severity))


def _attention_type_for_notification(
    notification_type: str,
    notification_subtype: str | None,
) -> str:
    if notification_type == "weather_alert":
        if notification_subtype == "frost_warning":
            return "frost_warning"
        if notification_subtype == "rain_surplus":
            return "rain_alert"
        if notification_subtype == "heat_wave":
            return "heat_wave"
        if notification_subtype == "dry_spell":
            return "dry_spell"
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
    metadata_raw = row.get("metadata") or row.get("metadata_json")
    if isinstance(metadata_raw, str):
        try:
            parsed_metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            parsed_metadata = {}
    else:
        parsed_metadata = metadata_raw
    metadata = parsed_metadata if isinstance(parsed_metadata, dict) else {}
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
            **metadata,
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
    respect_quiet_hours: bool = True,
) -> list[dict[str, Any]]:
    allowed: list[dict[str, Any]] = []
    for row in rows:
        item = _attention_item_from_notification_row(
            row,
            fallback_garden_id=garden_id,
            fallback_user_id=user_id,
        )
        if apply_preferences(
            [item],
            preferences,
            surface=surface,
            now_ms=now_ms,
            respect_quiet_hours=respect_quiet_hours,
        ):
            allowed.append(row)
    return allowed


def notification_request_clock(*, now_ms: int | None = None) -> tuple[int, str | None]:
    """Return the shared notification/Attention clock, including test freezes."""
    return attention_request_clock(
        now_ms=now_ms if now_ms is not None else current_timestamp_ms(),
    )


def _notification_today_iso(*, frozen_date: str | None) -> str:
    if frozen_date:
        date.fromisoformat(frozen_date)
        return frozen_date
    return date.today().isoformat()


def _legacy_in_app_enabled(pref_row: dict[str, Any] | None) -> bool:
    if pref_row is None:
        return True
    return _database_boolean(pref_row.get("in_app_enabled"), default=True)


def _database_boolean(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return value == 1
    return default


def digest_delivery_configuration(
    pref_row: dict[str, Any] | None,
    *,
    subscription_tier: str,
) -> dict[str, Any]:
    """Return the entitlement and complete global setup for digest delivery."""
    available = feature_allowed(subscription_tier, "email_notifications")
    email_enabled = _database_boolean(
        pref_row.get("email_enabled") if pref_row is not None else None,
        default=False,
    )
    email_address = str(pref_row.get("email_address") or "") if pref_row is not None else ""
    raw_frequency = (
        str(pref_row.get("digest_frequency") or "none") if pref_row is not None else "daily"
    )
    digest_frequency = raw_frequency if raw_frequency in {"none", "daily", "weekly"} else "none"
    configured = (
        available
        and email_enabled
        and bool(email_address.strip())
        and digest_frequency in {"daily", "weekly"}
    )
    return {
        "available": available,
        "configured": configured,
        "email_enabled": email_enabled,
        "email_address": email_address,
        "digest_frequency": digest_frequency,
    }


def load_digest_delivery_configuration(
    db: DbConn,
    *,
    user_id: int | None,
    subscription_tier: str,
) -> dict[str, Any]:
    row = None
    if user_id is not None:
        loaded = db.execute(
            """
            SELECT email_enabled, email_address, digest_frequency
            FROM user_notification_preferences
            WHERE user_id = %s
            """,
            (user_id,),
        ).fetchone()
        if loaded is not None:
            row = dict(loaded)
    return digest_delivery_configuration(row, subscription_tier=subscription_tier)


def _legacy_email_delivery_enabled(pref_row: dict[str, Any] | None) -> bool:
    if pref_row is None:
        return False
    return bool(
        digest_delivery_configuration(
            pref_row,
            subscription_tier=str(pref_row.get("subscription_tier") or "home"),
        )["configured"]
    )


def _legacy_in_app_enabled_for_user(db: DbConn, user_id: int) -> bool:
    row = db.execute(
        "SELECT in_app_enabled FROM user_notification_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    return _legacy_in_app_enabled(dict(row) if row is not None else None)


def notification_rows_allowed_for_user(
    db: DbConn,
    rows: list[dict[str, Any]],
    *,
    surface: AttentionSurface,
    garden_id: int,
    user_id: int,
    now_ms: int | None = None,
    preferences: AttentionPreferenceSet | None = None,
    legacy_in_app_enabled: bool | None = None,
    respect_quiet_hours: bool = True,
) -> list[dict[str, Any]]:
    """Apply canonical Attention eligibility and the legacy channel capability."""
    if surface == "inbox":
        in_app_enabled = (
            _legacy_in_app_enabled_for_user(db, user_id)
            if legacy_in_app_enabled is None
            else legacy_in_app_enabled
        )
        if not in_app_enabled:
            return []
    return notification_rows_allowed_by_attention(
        rows,
        preferences=preferences or load_attention_preferences(db, user_id),
        surface=surface,
        garden_id=garden_id,
        user_id=user_id,
        now_ms=now_ms,
        respect_quiet_hours=respect_quiet_hours,
    )


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
    now_ms: int | None = None,
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
        now_ms=now_ms,
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
    now_ms: int | None = None,
) -> str:
    metadata_json = json.dumps(metadata, separators=(",", ":")) if metadata else None
    now, _ = notification_request_clock(now_ms=now_ms)
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
    now_value, _ = notification_request_clock(now_ms=now_ms)
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
    non_dry_weather_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(_NON_DRY_WEATHER_TASK_RULE_SOURCE_PATTERNS),
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
              t.rule_source LIKE %s
              AND (
                wa.id IS NULL
                OR wa.dismissed = 1
                OR wa.valid_until < %s
              )
            )
            OR (
              ({non_dry_weather_pattern_clauses})
              AND (
                wa.id IS NULL
                OR wa.dismissed = 1
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
            _DRY_WEATHER_TASK_RULE_SOURCE_PATTERN,
            today_iso,
            *_NON_DRY_WEATHER_TASK_RULE_SOURCE_PATTERNS,
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
        SELECT p.user_id, p.in_app_enabled, p.email_enabled, p.email_address,
               p.digest_frequency, u.subscription_tier
        FROM user_notification_preferences p
        JOIN auth_users u ON u.id = p.user_id
        WHERE p.user_id IN ({placeholders})
        """,  # noqa: S608
        user_ids,
    ).fetchall()
    return {int(row["user_id"]): dict(row) for row in rows}


def _delivery_allowed_for_user(
    db: DbConn,
    pref_rows_by_user: dict[int, dict[str, Any]],
    preferences_by_user: dict[int, AttentionPreferenceSet],
    *,
    garden_id: int,
    user_id: int,
    notification_type: str,
    title: str,
    body: str,
    notification_subtype: str | None = None,
    severity: str | None = "normal",
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> bool:
    legacy_pref = pref_rows_by_user.get(user_id)
    preferences = preferences_by_user.get(user_id)
    if preferences is None:
        preferences = load_attention_preferences(db, user_id)
        preferences_by_user[user_id] = preferences
    candidate = {
        "garden_id": garden_id,
        "user_id": user_id,
        "notification_type": notification_type,
        "notification_subtype": notification_subtype,
        "severity": severity,
        "title": title,
        "body": body,
        "target_type": target_type,
        "target_id": target_id,
        "metadata": metadata or {},
        "created_at_ms": now_ms if now_ms is not None else 0,
    }
    if _legacy_in_app_enabled(legacy_pref) and notification_rows_allowed_for_user(
        db,
        [candidate],
        preferences=preferences,
        legacy_in_app_enabled=True,
        surface="inbox",
        garden_id=garden_id,
        user_id=user_id,
        now_ms=now_ms,
        respect_quiet_hours=False,
    ):
        return True
    # Generation uses durable channel eligibility. Digest cadence and quiet
    # hours are applied only while a pending digest is delivered.
    return _legacy_email_delivery_enabled(legacy_pref) and bool(
        notification_rows_allowed_for_user(
            db,
            [candidate],
            preferences=preferences,
            surface="digest",
            garden_id=garden_id,
            user_id=user_id,
            now_ms=now_ms,
            respect_quiet_hours=False,
        )
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
    now_value, _ = notification_request_clock(now_ms=now_ms)
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


def _lock_task_notification_projection(db: DbConn, *, garden_id: int) -> None:
    """Serialize task projection generation and clearing for one garden."""
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, %s))",
        (f"gardenops:task-notifications:{garden_id}", _TASK_NOTIFICATION_LOCK_SEED),
    )


def create_task_due_notifications_in_transaction(
    db: DbConn,
    garden_id: int,
    *,
    task_public_ids: set[str] | None = None,
    now_ms: int | None = None,
) -> dict[str, int]:
    """Check garden_tasks for tasks due today or overdue, create notifications.

    Returns {"created": N, "skipped": N, "cleared": N}.
    Deduplicates by (garden_id, user_id, notification_type, target_type, target_id)
    among non-dismissed notifications.
    """
    # Serialize the check/insert sequence per garden. The notification schema
    # intentionally keeps delivery history, so a unique active-row constraint
    # cannot express this lifecycle safely.
    _lock_task_notification_projection(db, garden_id=garden_id)
    created = 0
    skipped = 0
    now_value, frozen_date = notification_request_clock(now_ms=now_ms)
    today_iso = _notification_today_iso(frozen_date=frozen_date)
    upcoming_end_iso = offset_days_iso(3, today=date.fromisoformat(today_iso))
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
    existing_history_clause = """
        (
            cleared_at_ms IS NULL
            OR dismissed = 1
            OR COALESCE(clear_reason, '') NOT IN ('preference_hidden', 'superseded')
        )
    """
    if target_filter is not None:
        # An explicit refresh follows a task mutation. Keep user suppression,
        # but let the changed pending task replace ordinary cleared history.
        existing_history_clause = """
            (
                cleared_at_ms IS NULL
                OR dismissed = 1
                OR COALESCE(clear_reason, '') = 'preference_hidden'
            )
        """

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
        now_ms=now_value,
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
    preferences_by_user: dict[int, AttentionPreferenceSet] = {}

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
              AND {existing_history_clause}
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
            if not _delivery_allowed_for_user(
                db,
                pref_rows_by_user,
                preferences_by_user,
                garden_id=garden_id,
                user_id=uid,
                notification_type=ntype,
                title=ntitle,
                body=nbody,
                target_type="task",
                target_id=task_public_id,
                metadata=meta,
                now_ms=now_value,
            ):
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
                now_ms=now_value,
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
                    now_ms=now_value,
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
                    now_ms=now_value,
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
            if not _delivery_allowed_for_user(
                db,
                pref_rows_by_user,
                preferences_by_user,
                garden_id=garden_id,
                user_id=uid,
                notification_type=ntype,
                title=ntitle,
                body=nbody,
                target_type="task",
                target_id=task_public_id,
                metadata=meta,
                now_ms=now_value,
            ):
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
                now_ms=now_value,
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
    *,
    now_ms: int | None = None,
) -> dict[str, int]:
    """Check garden_tasks for tasks due today or overdue, create notifications.

    Returns {"created": N, "skipped": N}.
    Deduplicates by (garden_id, user_id, notification_type, target_type, target_id)
    among non-dismissed notifications.
    """
    result = create_task_due_notifications_in_transaction(
        db,
        garden_id,
        now_ms=now_ms,
    )
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
    *,
    now_ms: int | None = None,
) -> int:
    """Count unread, non-dismissed notifications for a user in a garden."""
    now, _ = notification_request_clock(now_ms=now_ms)
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
        return len(
            notification_rows_allowed_for_user(
                db,
                [dict(row) for row in rows],
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
    *,
    now_ms: int | None = None,
) -> bool:
    """Mark a single notification as read. Returns True if updated."""
    now, _ = notification_request_clock(now_ms=now_ms)
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
    *,
    now_ms: int | None = None,
) -> int:
    """Mark all notifications as read for user in garden. Returns count updated."""
    now, _ = notification_request_clock(now_ms=now_ms)
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
        visible_rows = notification_rows_allowed_for_user(
            db,
            [dict(row) for row in rows],
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
    *,
    now_ms: int | None = None,
) -> bool:
    """Dismiss a notification (soft-delete). Returns True if updated."""
    conditions = ["public_id = %s", "dismissed = 0"]
    now, _ = notification_request_clock(now_ms=now_ms)
    params: list[int | str] = [now, "manual_dismiss", notification_id]
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
    now_value, _ = notification_request_clock(now_ms=now_ms)
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
    if garden_id is not None:
        _lock_task_notification_projection(db, garden_id=garden_id)
    now_value, frozen_date = notification_request_clock(now_ms=now_ms)
    today = today_iso or _notification_today_iso(frozen_date=frozen_date)
    today_start_ms = _date_start_ms(today) or now_value
    non_dry_weather_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(_NON_DRY_WEATHER_TASK_RULE_SOURCE_PATTERNS),
    )
    weekly_watering_pattern_clauses = " OR ".join(
        ["t.rule_source LIKE %s"] * len(GENERATED_WEEKLY_WATERING_RULE_SOURCE_PATTERNS),
    )
    stale_generated_clause = (
        "("
        f"(({weekly_watering_pattern_clauses}) AND COALESCE(t.snoozed_until, t.due_on) < %s)"
        " OR "
        f"(t.rule_source LIKE %s AND "
        "(wa.valid_until < %s OR wa.dismissed = 1 OR wa.id IS NULL))"
        " OR "
        f"(({non_dry_weather_pattern_clauses}) AND "
        "(COALESCE(t.snoozed_until, t.due_on) < %s "
        "OR wa.valid_until < %s OR wa.dismissed = 1 OR wa.id IS NULL))"
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
            _DRY_WEATHER_TASK_RULE_SOURCE_PATTERN,
            today,
            *_NON_DRY_WEATHER_TASK_RULE_SOURCE_PATTERNS,
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
            _DRY_WEATHER_TASK_RULE_SOURCE_PATTERN,
            today,
            *_NON_DRY_WEATHER_TASK_RULE_SOURCE_PATTERNS,
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
    now_value, frozen_date = notification_request_clock(now_ms=now_ms)
    today = today_iso or _notification_today_iso(frozen_date=frozen_date)
    today_start_ms = _date_start_ms(today) or now_value
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
    _lock_task_notification_projection(db, garden_id=garden_id)
    now_value, _ = notification_request_clock(now_ms=now_ms)
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
    now_value, _ = notification_request_clock(now_ms=now_ms)
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
        now_ms=now_value,
    )
    refresh_result = {
        "cleared": cleared,
        "created": int(result.get("created", 0)),
        "skipped": int(result.get("skipped", 0)),
    }
    log_refresh = logger.warning if cleared > 0 and refresh_result["created"] == 0 else logger.info
    log_refresh(
        "Task notification refresh garden_id=%s target_id=%s cleared=%s created=%s skipped=%s",
        garden_id,
        task_public_id,
        refresh_result["cleared"],
        refresh_result["created"],
        refresh_result["skipped"],
    )
    return refresh_result


def clear_issue_notifications(
    db: DbConn,
    *,
    garden_id: int,
    issue_public_id: str,
    reason: str = "resolved",
    now_ms: int | None = None,
) -> int:
    now_value, _ = notification_request_clock(now_ms=now_ms)
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
    now_ms: int | None = None,
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
    preferences_by_user: dict[int, AttentionPreferenceSet] = {}
    now_value, _ = notification_request_clock(now_ms=now_ms)
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
        if not _delivery_allowed_for_user(
            db,
            pref_rows_by_user,
            preferences_by_user,
            garden_id=garden_id,
            user_id=uid,
            notification_type=notification_type,
            title=title,
            body=body,
            notification_subtype=notification_subtype,
            severity=severity,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
            now_ms=now_value,
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
            now_ms=now_value,
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


def _weather_alert_target_id(alert_type: str, valid_from: str) -> str:
    return f"{alert_type}:{valid_from}"


def _weather_alert_identities(alerts: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted(
        {
            (str(alert.get("alert_type") or ""), str(alert.get("valid_from") or ""))
            for alert in alerts
            if alert.get("alert_type") and alert.get("valid_from")
        }
    )


def _split_forecast_reconciliation_scope(
    alerts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str] | None, dict[str, tuple[str, str]] | None]:
    """Separate private forecast scope metadata from real alerts."""
    scoped_alerts: list[dict[str, Any]] = []
    complete_alert_types: set[str] | None = None
    coverage_bounds: dict[str, tuple[str, str]] | None = None
    for alert in alerts:
        marker = alert.get(_FORECAST_RECONCILIATION_SCOPE_KEY)
        if marker is None:
            scoped_alerts.append(alert)
            continue
        if isinstance(marker, list):
            complete_alert_types = {
                str(alert_type) for alert_type in marker if str(alert_type) in _FORECAST_ALERT_TYPES
            }
        elif isinstance(marker, dict):
            raw_types = marker.get("complete_alert_types")
            complete_alert_types = (
                {
                    str(alert_type)
                    for alert_type in raw_types
                    if str(alert_type) in _FORECAST_ALERT_TYPES
                }
                if isinstance(raw_types, list)
                else set()
            )
            coverage_bounds = _parse_forecast_coverage_bounds(marker.get("coverage_bounds"))
        else:
            complete_alert_types = set()
            coverage_bounds = {}
    return scoped_alerts, complete_alert_types, coverage_bounds


def _parse_forecast_coverage_bounds(value: Any) -> dict[str, tuple[str, str]]:
    if not isinstance(value, dict):
        return {}
    bounds: dict[str, tuple[str, str]] = {}
    for alert_type, raw_bounds in value.items():
        if alert_type not in _FORECAST_ALERT_TYPES or not isinstance(raw_bounds, dict):
            continue
        start = raw_bounds.get("start")
        end = raw_bounds.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            continue
        if start_date <= end_date:
            bounds[alert_type] = (start, end)
    return bounds


def _forecast_alert_within_coverage(
    *,
    alert_type: str,
    valid_from: str,
    valid_until: str,
    coverage_bounds: dict[str, tuple[str, str]] | None,
) -> bool:
    if coverage_bounds is None:
        return True
    bounds = coverage_bounds.get(alert_type)
    if bounds is None:
        return False
    try:
        start = date.fromisoformat(valid_from)
        end = date.fromisoformat(valid_until)
        coverage_start = date.fromisoformat(bounds[0])
        coverage_end = date.fromisoformat(bounds[1])
    except ValueError:
        return False
    return coverage_start <= start <= end <= coverage_end


def _parse_weather_lifecycle_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except TypeError, ValueError, json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _record_weather_lifecycle_transition(
    metadata: dict[str, Any],
    transition: dict[str, Any],
) -> None:
    current = metadata.get("lifecycle")
    history = metadata.get("lifecycle_history")
    transitions = list(history) if isinstance(history, list) else []
    if isinstance(current, dict) and current != transition:
        transitions.append(dict(current))
    if transitions:
        metadata["lifecycle_history"] = transitions[-20:]
    metadata["lifecycle"] = transition


def _load_weather_alert_for_update(
    db: DbConn,
    *,
    garden_id: int,
    alert_type: str,
    valid_from: str,
) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT id, alert_type, severity, title, description, valid_from, valid_until, metadata_json
        FROM weather_alerts
        WHERE garden_id = %s
          AND alert_type = %s
          AND valid_from = %s
          AND dismissed = 0
        FOR UPDATE
        """,
        (garden_id, alert_type, valid_from),
    ).fetchone()
    return dict(row) if row is not None else None


def clear_weather_alert_notifications(
    db: DbConn,
    *,
    garden_id: int,
    user_id: int,
    alert_type: str,
    valid_from: str,
    now_ms: int | None = None,
) -> int:
    now_value, _ = notification_request_clock(now_ms=now_ms)
    cur = db.execute(
        """
        UPDATE notification_events
        SET cleared_at_ms = %s,
            clear_reason = 'weather_dismissed'
        WHERE garden_id = %s
          AND user_id = %s
          AND notification_type = 'weather_alert'
          AND target_type = 'weather_alert'
          AND target_id = %s
          AND dismissed = 0
          AND cleared_at_ms IS NULL
        """,
        (
            now_value,
            garden_id,
            user_id,
            _weather_alert_target_id(alert_type, valid_from),
        ),
    )
    return cur.rowcount


def _resolve_missing_forecast_alert_work(
    db: DbConn,
    *,
    garden_id: int,
    alerts: list[dict[str, Any]],
    now_ms: int,
    complete_alert_types: set[str] | None = None,
    coverage_bounds: dict[str, tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Retire active forecast-owned work absent from the covered forecast run."""
    current_identities = set(_weather_alert_identities(alerts))
    rows = db.execute(
        """
        SELECT id, alert_type, valid_from, valid_until, metadata_json
        FROM weather_alerts
        WHERE garden_id = %s
          AND dismissed = 0
          AND alert_type IN ('frost_warning', 'heat_wave', 'dry_spell', 'rain_surplus')
        FOR UPDATE
        """,
        (garden_id,),
    ).fetchall()
    resolved_alerts = 0
    resolved_tasks = 0
    resolved_notifications = 0
    for row in rows:
        alert_type = str(row["alert_type"])
        if complete_alert_types is not None and alert_type not in complete_alert_types:
            continue
        valid_from = str(row["valid_from"])
        if not _forecast_alert_within_coverage(
            alert_type=alert_type,
            valid_from=valid_from,
            valid_until=str(row["valid_until"]),
            coverage_bounds=coverage_bounds,
        ):
            continue
        if (alert_type, valid_from) in current_identities:
            continue
        metadata = _parse_weather_lifecycle_metadata(row["metadata_json"])
        _record_weather_lifecycle_transition(
            metadata,
            {
                "status": "resolved",
                "reason": "absent_from_current_forecast",
                "resolution_kind": "automatic_forecast",
                "resolved_at_ms": now_ms,
                "source": "forecast_reconciliation",
            },
        )
        db.execute(
            "UPDATE weather_alerts SET dismissed = 1, metadata_json = %s WHERE id = %s",
            (json.dumps(metadata, sort_keys=True, separators=(",", ":")), int(row["id"])),
        )
        resolved_alerts += 1
        notification = db.execute(
            """
            UPDATE notification_events
            SET cleared_at_ms = %s, clear_reason = 'forecast_resolved'
            WHERE garden_id = %s
              AND notification_type = 'weather_alert'
              AND target_type = 'weather_alert'
              AND target_id = %s
              AND dismissed = 0
              AND cleared_at_ms IS NULL
            """,
            (now_ms, garden_id, _weather_alert_target_id(alert_type, valid_from)),
        )
        resolved_notifications += notification.rowcount
        task_rows = db.execute(
            """
            SELECT id, public_id, status, due_on, snoozed_until, metadata_json
            FROM garden_tasks
            WHERE garden_id = %s
              AND status IN ('pending', 'snoozed')
              AND rule_source LIKE ANY(%s)
              AND split_part(rule_source, ':', 3) = %s
            FOR UPDATE
            """,
            (garden_id, list(_WEATHER_TASK_RULE_SOURCE_PATTERNS), str(row["id"])),
        ).fetchall()
        for task_row in task_rows:
            task_metadata = _parse_weather_lifecycle_metadata(task_row["metadata_json"])
            _record_weather_lifecycle_transition(
                task_metadata,
                {
                    "status": "resolved",
                    "reason": "absent_from_current_forecast",
                    "resolution_kind": "automatic_forecast",
                    "resolved_at_ms": now_ms,
                    "source": "forecast_reconciliation",
                    "weather_alert_id": int(row["id"]),
                    "previous_status": str(task_row["status"]),
                    "previous_due_on": str(task_row["due_on"] or ""),
                    "previous_snoozed_until": str(task_row["snoozed_until"] or ""),
                },
            )
            task_update = db.execute(
                """
                UPDATE garden_tasks
                SET status = 'skipped',
                    snoozed_until = NULL,
                    completed_by_user_id = NULL,
                    completed_at_ms = NULL,
                    metadata_json = %s,
                    updated_at_ms = %s
                WHERE id = %s AND status IN ('pending', 'snoozed')
                """,
                (
                    json.dumps(task_metadata, sort_keys=True, separators=(",", ":")),
                    now_ms,
                    int(task_row["id"]),
                ),
            )
            if task_update.rowcount != 1:
                continue
            resolved_tasks += 1
            resolved_notifications += clear_task_notifications(
                db,
                garden_id=garden_id,
                task_public_id=str(task_row["public_id"]),
                reason="forecast_resolved",
                now_ms=now_ms,
            )
    return {
        "alerts_resolved": resolved_alerts,
        "tasks_resolved": resolved_tasks,
        "notifications_resolved": resolved_notifications,
    }


def _recover_reappeared_forecast_alert_work(
    db: DbConn,
    *,
    garden_id: int,
    alerts: list[dict[str, Any]],
    now_ms: int,
    complete_alert_types: set[str] | None = None,
    coverage_bounds: dict[str, tuple[str, str]] | None = None,
) -> int:
    """Reopen only work automatically retired by forecast reconciliation."""
    today = datetime.fromtimestamp(now_ms / 1000, UTC).date().isoformat()
    recovered = 0
    for alert_type, valid_from in _weather_alert_identities(alerts):
        if complete_alert_types is not None and alert_type not in complete_alert_types:
            continue
        alert = db.execute(
            """
            SELECT id, valid_from, valid_until
            FROM weather_alerts
            WHERE garden_id = %s
              AND alert_type = %s
              AND valid_from = %s
              AND dismissed = 0
            FOR UPDATE
            """,
            (garden_id, alert_type, valid_from),
        ).fetchone()
        if alert is None or str(alert["valid_until"]) < today:
            continue
        if not _forecast_alert_within_coverage(
            alert_type=alert_type,
            valid_from=str(alert["valid_from"]),
            valid_until=str(alert["valid_until"]),
            coverage_bounds=coverage_bounds,
        ):
            continue
        task_rows = db.execute(
            """
            SELECT id, public_id, task_type, status, due_on, snoozed_until,
                   rule_source, metadata_json
            FROM garden_tasks
            WHERE garden_id = %s
              AND status = 'skipped'
              AND rule_source LIKE ANY(%s)
              AND split_part(rule_source, ':', 3) = %s
            FOR UPDATE
            """,
            (garden_id, list(_WEATHER_TASK_RULE_SOURCE_PATTERNS), str(alert["id"])),
        ).fetchall()
        for task in task_rows:
            metadata = _parse_weather_lifecycle_metadata(task["metadata_json"])
            lifecycle = metadata.get("lifecycle")
            if not isinstance(lifecycle, dict):
                continue
            if lifecycle.get("reason") != "absent_from_current_forecast":
                continue
            if lifecycle.get("resolution_kind") != "automatic_forecast":
                continue
            if int(lifecycle.get("weather_alert_id") or 0) != int(alert["id"]):
                continue

            previous_status = str(lifecycle.get("previous_status") or "pending")
            previous_snoozed_until = str(lifecycle.get("previous_snoozed_until") or "")
            restored_status = (
                "snoozed"
                if previous_status == "snoozed" and previous_snoozed_until >= today
                else "pending"
            )
            restored_snoozed_until = (
                previous_snoozed_until if restored_status == "snoozed" else None
            )
            previous_due_on = str(lifecycle.get("previous_due_on") or task["due_on"] or valid_from)
            restored_due_on = max(previous_due_on, today)
            _record_weather_lifecycle_transition(
                metadata,
                {
                    "status": "active",
                    "reason": "same_identity_reappeared",
                    "recovered_at_ms": now_ms,
                    "source": "forecast_reconciliation",
                    "weather_alert_id": int(alert["id"]),
                },
            )
            update = db.execute(
                """
                UPDATE garden_tasks
                SET status = %s,
                    due_on = %s,
                    snoozed_until = %s,
                    completed_by_user_id = NULL,
                    completed_at_ms = NULL,
                    metadata_json = %s,
                    updated_at_ms = %s
                WHERE id = %s AND status = 'skipped'
                """,
                (
                    restored_status,
                    restored_due_on,
                    restored_snoozed_until,
                    json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                    now_ms,
                    int(task["id"]),
                ),
            )
            if update.rowcount != 1:
                continue
            recovered += 1
            refresh_task_notifications_for_task(
                db,
                garden_id=garden_id,
                task_public_id=str(task["public_id"]),
                now_ms=now_ms,
            )
    return recovered


def _reconcile_authoritative_weather_tasks(
    db: DbConn,
    *,
    garden_id: int,
    alerts: list[dict[str, Any]],
    now_ms: int,
    complete_alert_types: set[str] | None = None,
    coverage_bounds: dict[str, tuple[str, str]] | None = None,
) -> int:
    """Retire stale weather-task projections and keep their validity metadata current."""
    today = datetime.fromtimestamp(now_ms / 1000, UTC).date().isoformat()
    retired = 0
    for alert_type, valid_from in _weather_alert_identities(alerts):
        if complete_alert_types is not None and alert_type not in complete_alert_types:
            continue
        task_prefix = _WEATHER_TASK_PREFIX_BY_ALERT_TYPE.get(alert_type)
        if task_prefix is None:
            continue
        alert = db.execute(
            """
            SELECT id, valid_from, valid_until, metadata_json
            FROM weather_alerts
            WHERE garden_id = %s
              AND alert_type = %s
              AND valid_from = %s
              AND dismissed = 0
            FOR UPDATE
            """,
            (garden_id, alert_type, valid_from),
        ).fetchone()
        if alert is None:
            continue
        if not _forecast_alert_within_coverage(
            alert_type=alert_type,
            valid_from=str(alert["valid_from"]),
            valid_until=str(alert["valid_until"]),
            coverage_bounds=coverage_bounds,
        ):
            continue
        alert_id = int(alert["id"])
        alert_metadata = _parse_weather_lifecycle_metadata(alert["metadata_json"])
        plant_links_authoritative = bool(alert_metadata.get("forecast_plant_links_authoritative"))
        linked_plant_ids: set[str] = set()
        if plant_links_authoritative:
            linked_plant_ids = {
                str(row["plt_id"])
                for row in db.execute(
                    "SELECT plt_id FROM weather_alert_plants WHERE alert_id = %s",
                    (alert_id,),
                ).fetchall()
            }
        task_rows = db.execute(
            """
            SELECT id, public_id, status, rule_source, metadata_json
            FROM garden_tasks
            WHERE garden_id = %s
              AND status IN ('pending', 'snoozed')
              AND rule_source LIKE %s
            FOR UPDATE
            """,
            (garden_id, f"auto:{task_prefix}:{alert_id}:%"),
        ).fetchall()
        for task in task_rows:
            task_metadata = _parse_weather_lifecycle_metadata(task["metadata_json"])
            task_metadata["weather_alert_id"] = alert_id
            task_metadata["weather_valid_from"] = str(alert["valid_from"])
            task_metadata["weather_valid_until"] = str(alert["valid_until"])
            rule_parts = str(task["rule_source"] or "").split(":", 3)
            plant_id = rule_parts[3] if len(rule_parts) == 4 else ""
            terminal_status = ""
            terminal_reason = ""
            if plant_links_authoritative and plant_id not in linked_plant_ids:
                terminal_status = "skipped"
                terminal_reason = "plant_no_longer_affected_by_current_forecast"
            elif str(alert["valid_until"]) < today:
                terminal_status = "expired"
                terminal_reason = "weather_alert_validity_ended"

            if terminal_status:
                _record_weather_lifecycle_transition(
                    task_metadata,
                    {
                        "status": terminal_status,
                        "reason": terminal_reason,
                        "resolution_kind": "authoritative_forecast",
                        "resolved_at_ms": now_ms,
                        "source": "forecast_reconciliation",
                        "weather_alert_id": alert_id,
                    },
                )
                update = db.execute(
                    """
                    UPDATE garden_tasks
                    SET status = %s,
                        snoozed_until = NULL,
                        completed_by_user_id = NULL,
                        completed_at_ms = NULL,
                        metadata_json = %s,
                        updated_at_ms = %s
                    WHERE id = %s AND status IN ('pending', 'snoozed')
                    """,
                    (
                        terminal_status,
                        json.dumps(task_metadata, sort_keys=True, separators=(",", ":")),
                        now_ms,
                        int(task["id"]),
                    ),
                )
                if update.rowcount != 1:
                    continue
                retired += 1
                clear_task_notifications(
                    db,
                    garden_id=garden_id,
                    task_public_id=str(task["public_id"]),
                    reason=("forecast_reconciled" if terminal_status == "skipped" else "expired"),
                    now_ms=now_ms,
                )
                continue

            if task_metadata != _parse_weather_lifecycle_metadata(task["metadata_json"]):
                db.execute(
                    """
                    UPDATE garden_tasks
                    SET metadata_json = %s, updated_at_ms = %s
                    WHERE id = %s AND status IN ('pending', 'snoozed')
                    """,
                    (
                        json.dumps(task_metadata, sort_keys=True, separators=(",", ":")),
                        now_ms,
                        int(task["id"]),
                    ),
                )
    return retired


def _refresh_weather_alert_notification(
    db: DbConn,
    *,
    row: dict[str, Any],
    notification_subtype: str,
    severity: str,
    title: str,
    body: str,
    metadata_json: str,
    expires_at_ms: int | None,
    rearm: bool = False,
) -> bool:
    content_changed = (
        str(row["severity"] or "normal") != severity
        or str(row["title"] or "") != title
        or str(row["body"] or "") != body
        or str(row["metadata_json"] or "") != metadata_json
        or row["expires_at_ms"] != expires_at_ms
        or row["cleared_at_ms"] is not None
        or row["clear_reason"] is not None
    )
    if not content_changed and not rearm:
        return False
    db.execute(
        """
        UPDATE notification_events
        SET notification_subtype = %s,
            severity = %s,
            title = %s,
            body = %s,
            metadata_json = %s,
            expires_at_ms = %s,
            dismissed = 0,
            read_at_ms = CASE WHEN %s THEN NULL ELSE read_at_ms END,
            emailed_at_ms = CASE WHEN %s THEN NULL ELSE emailed_at_ms END,
            cleared_at_ms = NULL,
            clear_reason = NULL
        WHERE id = %s
        """,
        (
            notification_subtype,
            severity,
            title,
            body,
            metadata_json,
            expires_at_ms,
            rearm,
            rearm,
            int(row["id"]),
        ),
    )
    return True


def create_weather_alert_notifications(
    db: DbConn,
    *,
    garden_id: int,
    alerts: list[dict[str, Any]],
    now_ms: int | None = None,
) -> dict[str, int]:
    """Reconcile per-user weather notifications while holding each alert row lock."""
    created = 0
    skipped = 0
    now_value, _ = notification_request_clock(now_ms=now_ms)
    identities = _weather_alert_identities(alerts)
    skipped += len(alerts) - len(identities)
    for alert_type, valid_from in identities:
        alert_row = _load_weather_alert_for_update(
            db,
            garden_id=garden_id,
            alert_type=alert_type,
            valid_from=valid_from,
        )
        if alert_row is None:
            skipped += 1
            continue
        alert_metadata = _parse_weather_lifecycle_metadata(alert_row["metadata_json"])
        rearm_from_reappearance = bool(alert_metadata.get("notification_rearm_pending"))
        members = db.execute(
            "SELECT user_id FROM garden_memberships WHERE garden_id = %s",
            (garden_id,),
        ).fetchall()
        member_ids = [int(member["user_id"]) for member in members]
        if not member_ids:
            if rearm_from_reappearance:
                alert_metadata["notification_rearm_pending"] = False
                db.execute(
                    "UPDATE weather_alerts SET metadata_json = %s WHERE id = %s",
                    (
                        json.dumps(alert_metadata, sort_keys=True, separators=(",", ":")),
                        int(alert_row["id"]),
                    ),
                )
            continue

        target_id = _weather_alert_target_id(alert_type, valid_from)
        severity = _normalize_severity(str(alert_row["severity"] or "normal"))
        title = str(alert_row["title"] or "Weather alert")
        body = str(alert_row["description"] or "")
        valid_until = str(alert_row["valid_until"])
        expires_at_ms = _date_end_ms(valid_until)
        metadata_json = json.dumps(
            {
                "alert_id": int(alert_row["id"]),
                "alert_type": alert_type,
                "severity": severity,
                "valid_from": valid_from,
                "valid_until": valid_until,
            },
            separators=(",", ":"),
        )
        existing_rows = db.execute(
            """
            SELECT user_id, id, notification_subtype, severity, title, body,
                   metadata_json, expires_at_ms, read_at_ms, emailed_at_ms,
                   dismissed, cleared_at_ms, clear_reason
            FROM notification_events
            WHERE garden_id = %s
              AND user_id = ANY(%s)
              AND notification_type = 'weather_alert'
              AND target_type = 'weather_alert'
              AND target_id = %s
            ORDER BY user_id ASC, id DESC
            FOR UPDATE
            """,
            (garden_id, member_ids, target_id),
        ).fetchall()
        rows_by_user: dict[int, list[dict[str, Any]]] = {}
        for row in existing_rows:
            rows_by_user.setdefault(int(row["user_id"]), []).append(dict(row))
        dismissed_rows = db.execute(
            """
            SELECT user_id
            FROM user_attention_item_state
            WHERE garden_id = %s
              AND user_id = ANY(%s)
              AND item_id = %s
              AND user_state = 'dismissed'
            """,
            (garden_id, member_ids, f"attn:weather:alert:{int(alert_row['id'])}"),
        ).fetchall()
        dismissed_user_ids = {int(row["user_id"]) for row in dismissed_rows}
        pref_rows_by_user = _load_pref_rows_for_users(db, member_ids)
        preferences_by_user: dict[int, AttentionPreferenceSet] = {}

        for user_id in member_ids:
            rows = rows_by_user.get(user_id, [])
            if user_id in dismissed_user_ids and not rearm_from_reappearance:
                clear_weather_alert_notifications(
                    db,
                    garden_id=garden_id,
                    user_id=user_id,
                    alert_type=alert_type,
                    valid_from=valid_from,
                    now_ms=now_value,
                )
                skipped += 1
                continue

            active_rows = [
                row for row in rows if not bool(row["dismissed"]) and row["cleared_at_ms"] is None
            ]
            if active_rows:
                active_row = active_rows[0]
                severity_escalated = (
                    SEVERITY_RANK[severity]
                    > SEVERITY_RANK[_normalize_severity(str(active_row["severity"] or "normal"))]
                )
                _refresh_weather_alert_notification(
                    db,
                    row=active_row,
                    notification_subtype=alert_type,
                    severity=severity,
                    title=title,
                    body=body,
                    metadata_json=metadata_json,
                    expires_at_ms=expires_at_ms,
                    rearm=rearm_from_reappearance or severity_escalated,
                )
                for duplicate in active_rows[1:]:
                    db.execute(
                        """
                        UPDATE notification_events
                        SET cleared_at_ms = %s,
                            clear_reason = 'superseded'
                        WHERE id = %s
                        """,
                        (now_value, int(duplicate["id"])),
                    )
                skipped += 1
                continue

            dismissed_row = next((row for row in rows if bool(row["dismissed"])), None)
            if dismissed_row is not None:
                dismissed_severity = _normalize_severity(str(dismissed_row["severity"] or "normal"))
                severity_escalated = SEVERITY_RANK[severity] > SEVERITY_RANK[dismissed_severity]
                if not rearm_from_reappearance and not severity_escalated:
                    skipped += 1
                    continue
                _refresh_weather_alert_notification(
                    db,
                    row=dismissed_row,
                    notification_subtype=alert_type,
                    severity=severity,
                    title=title,
                    body=body,
                    metadata_json=metadata_json,
                    expires_at_ms=expires_at_ms,
                    rearm=True,
                )
                skipped += 1
                continue

            if not _delivery_allowed_for_user(
                db,
                pref_rows_by_user,
                preferences_by_user,
                garden_id=garden_id,
                user_id=user_id,
                notification_type="weather_alert",
                notification_subtype=alert_type,
                severity=severity,
                title=title,
                body=body,
                target_type="weather_alert",
                target_id=target_id,
                metadata=json.loads(metadata_json),
                now_ms=now_value,
            ):
                skipped += 1
                continue

            if rows:
                prior_row = rows[0]
                severity_escalated = (
                    SEVERITY_RANK[severity]
                    > SEVERITY_RANK[_normalize_severity(str(prior_row["severity"] or "normal"))]
                )
                _refresh_weather_alert_notification(
                    db,
                    row=prior_row,
                    notification_subtype=alert_type,
                    severity=severity,
                    title=title,
                    body=body,
                    metadata_json=metadata_json,
                    expires_at_ms=expires_at_ms,
                    rearm=rearm_from_reappearance or severity_escalated,
                )
                skipped += 1
                continue

            _insert_notification(
                db,
                garden_id,
                user_id,
                "weather_alert",
                title,
                body,
                target_type="weather_alert",
                target_id=target_id,
                metadata=json.loads(metadata_json),
                notification_subtype=alert_type,
                severity=severity,
                expires_at_ms=expires_at_ms,
                now_ms=now_value,
            )
            created += 1
        if rearm_from_reappearance:
            alert_metadata["notification_rearm_pending"] = False
            db.execute(
                "UPDATE weather_alerts SET metadata_json = %s WHERE id = %s",
                (
                    json.dumps(alert_metadata, sort_keys=True, separators=(",", ":")),
                    int(alert_row["id"]),
                ),
            )
    return {"created": created, "skipped": skipped}


def reconcile_weather_alert_work(
    db: DbConn,
    *,
    garden_id: int,
    alerts: list[dict[str, Any]],
    actor_user_id: int | None,
    now_ms: int | None = None,
    replace_forecast_alerts: bool = False,
) -> dict[str, int]:
    """Reconcile weather alert notifications and generated tasks in one transaction."""
    now_value, _ = notification_request_clock(now_ms=now_ms)
    forecast_alerts, complete_alert_types, coverage_bounds = _split_forecast_reconciliation_scope(
        alerts
    )
    resolution = {
        "alerts_resolved": 0,
        "tasks_resolved": 0,
        "tasks_recovered": 0,
        "notifications_resolved": 0,
        "tasks_retired": 0,
    }
    if replace_forecast_alerts:
        resolution = _resolve_missing_forecast_alert_work(
            db,
            garden_id=garden_id,
            alerts=forecast_alerts,
            now_ms=now_value,
            complete_alert_types=complete_alert_types,
            coverage_bounds=coverage_bounds,
        )
        resolution["tasks_recovered"] = _recover_reappeared_forecast_alert_work(
            db,
            garden_id=garden_id,
            alerts=forecast_alerts,
            now_ms=now_value,
            complete_alert_types=complete_alert_types,
            coverage_bounds=coverage_bounds,
        )
    notifications = create_weather_alert_notifications(
        db,
        garden_id=garden_id,
        alerts=forecast_alerts,
        now_ms=now_value,
    )
    task_handlers = {
        "frost_warning": on_frost_alert,
        "heat_wave": on_heat_alert,
        "dry_spell": on_dry_spell_alert,
        "rain_surplus": on_rain_alert,
    }
    tasks_created = 0
    for alert_type, valid_from in _weather_alert_identities(forecast_alerts):
        handler = task_handlers.get(alert_type)
        if handler is None:
            continue
        alert_row = _load_weather_alert_for_update(
            db,
            garden_id=garden_id,
            alert_type=alert_type,
            valid_from=valid_from,
        )
        if alert_row is not None:
            tasks_created += handler(
                db,
                garden_id,
                int(alert_row["id"]),
                actor_user_id,
                now_ms=now_value,
            )
    if replace_forecast_alerts:
        resolution["tasks_retired"] = _reconcile_authoritative_weather_tasks(
            db,
            garden_id=garden_id,
            alerts=forecast_alerts,
            now_ms=now_value,
            complete_alert_types=complete_alert_types,
            coverage_bounds=coverage_bounds,
        )
    rain_reconciliation = reconcile_rain_watering_outcomes(
        db,
        garden_id=garden_id,
        now_ms=now_value,
    )
    return {
        "notifications_created": int(notifications["created"]),
        "notifications_skipped": int(notifications["skipped"]),
        "tasks_created": tasks_created,
        "rain_tasks_recovered": rain_reconciliation["recovered"],
        "rain_tasks_adjusted": rain_reconciliation["adjusted"],
        **resolution,
    }


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


def _quiet_time_minute(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 60 if 0 <= value <= 23 else None
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in {1, 2}:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) == 2 else 0
    except ValueError:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


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

    start = data.get("start", data.get("from", data.get("start_hour")))
    end = data.get("end", data.get("to", data.get("end_hour")))
    start_minute = _quiet_time_minute(start)
    end_minute = _quiet_time_minute(end)
    if start_minute is not None and end_minute is not None:
        return start_minute, end_minute
    logger.warning("Could not extract valid quiet hours from: %r", raw)
    return None


def _is_quiet_hours(now_utc: datetime, raw: str | None) -> bool:
    quiet = _parse_quiet_hours(raw)
    if quiet is None:
        return False
    start_minute, end_minute = quiet
    current_minute = now_utc.hour * 60 + now_utc.minute
    if start_minute == end_minute:
        return False
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


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


def _lock_pending_email_digest_delivery(
    db: DbConn,
    *,
    garden_id: int,
    user_id: int,
) -> None:
    """Serialize one recipient's digest claim until the enclosing transaction ends."""
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, %s))",
        (
            f"gardenops:digest-delivery:{garden_id}:{user_id}",
            _DIGEST_DELIVERY_LOCK_SEED,
        ),
    )


def _load_locked_digest_recipient(
    db: DbConn,
    *,
    garden_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    """Revalidate and pin one recipient's delivery configuration."""
    row = db.execute(
        """
        SELECT p.user_id, p.email_enabled, p.email_address,
               p.digest_frequency, u.subscription_tier
        FROM user_notification_preferences p
        JOIN garden_memberships gm ON gm.user_id = p.user_id
        JOIN auth_users u ON u.id = p.user_id
        WHERE gm.garden_id = %s
          AND p.user_id = %s
        FOR UPDATE OF p, gm, u
        """,
        (garden_id, user_id),
    ).fetchone()
    if row is None:
        return None
    recipient = dict(row)
    configuration = digest_delivery_configuration(
        recipient,
        subscription_tier=str(recipient.get("subscription_tier") or "home"),
    )
    if not bool(configuration["configured"]):
        return None
    return {
        **recipient,
        "email_address": str(configuration["email_address"]),
        "digest_frequency": str(configuration["digest_frequency"]),
    }


def deliver_pending_email_digests(
    db: DbConn,
    garden_id: int,
    *,
    email_sender: EmailSender | None = None,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    """Deliver pending digests with per-recipient transactional serialization.

    Digest state is marked only after SMTP returns. SMTP cannot participate in
    the database transaction, so a process crash after successful SMTP but
    before commit remains an unavoidable at-least-once retry case. Keeping the
    pre-send rows pending is safer than marking them first and losing a digest.
    """
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

    now_value, _ = notification_request_clock(now_ms=now_ms)
    processed_users = 0
    emailed_users = 0
    notifications_marked = 0
    skipped_users = 0
    max_events_per_user = _env_int("NOTIFICATION_DIGEST_MAX_EVENTS_PER_USER", 100)

    recipients = db.execute(
        """
        SELECT p.user_id
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
        user_id = int(pref["user_id"])
        _lock_pending_email_digest_delivery(
            db,
            garden_id=garden_id,
            user_id=user_id,
        )
        recipient = _load_locked_digest_recipient(
            db,
            garden_id=garden_id,
            user_id=user_id,
        )
        if recipient is None:
            skipped_users += 1
            continue
        digest_frequency = str(recipient["digest_frequency"])
        interval_ms = _digest_interval_ms(digest_frequency)
        last_sent_row = db.execute(
            """
            SELECT MAX(emailed_at_ms) AS last_sent
            FROM notification_events
            WHERE garden_id = %s
              AND user_id = %s
              AND emailed_at_ms IS NOT NULL
            """,
            (garden_id, user_id),
        ).fetchone()
        last_sent = last_sent_row["last_sent"] if last_sent_row is not None else None
        if interval_ms is not None and last_sent and now_value - int(last_sent) < interval_ms:
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
            (garden_id, user_id, now_value),
        ).fetchall()
        notifications = notification_rows_allowed_for_user(
            db,
            [dict(row) for row in notifications],
            surface="digest",
            garden_id=garden_id,
            user_id=user_id,
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
            email_sender(str(recipient["email_address"]), subject, body)
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
            (now_value, now_value, user_id),
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
    try:
        result = check_weather_and_generate_alerts(db, garden_id, lat, lon)
        downstream = reconcile_weather_alert_work(
            db,
            garden_id=garden_id,
            alerts=list(result.get("alerts", [])),
            actor_user_id=None,
            now_ms=now_ms,
            replace_forecast_alerts=bool(result.get("forecast_available")),
        )
        db.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (%s, %s)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (settings_key, str(now_ms)),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "weather_checks": 1,
        "weather_alerts_created": result.get("alerts_created", 0),
        "weather_tasks_created": downstream["tasks_created"],
        "weather_notifications_created": downstream["notifications_created"],
        "weather_notifications_skipped": downstream["notifications_skipped"],
    }


def _monthly_rain_generation_signature(
    db: DbConn,
    *,
    garden_id: int,
    year: int,
    month: int,
) -> str:
    """Fingerprint active rain coverage relevant to one generation month."""
    month_start = date(year, month, 1)
    month_end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(
        days=1
    )
    rows = db.execute(
        """
        SELECT id, valid_from, valid_until
        FROM weather_alerts
        WHERE garden_id = %s
          AND alert_type = 'rain_surplus'
          AND dismissed = 0
          AND valid_from <= %s
          AND valid_until >= %s
        ORDER BY id ASC
        """,
        (garden_id, month_end.isoformat(), month_start.isoformat()),
    ).fetchall()
    payload = [(int(row["id"]), str(row["valid_from"]), str(row["valid_until"])) for row in rows]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()[:16]


def _auto_generate_monthly_tasks(
    db: DbConn,
    garden_id: int,
    now_ms: int,
    *,
    frozen_date: str | None = None,
) -> dict[str, int | bool]:
    """Generate monthly tasks until rain-suppressed watering is recoverable."""
    today = date.fromisoformat(_notification_today_iso(frozen_date=frozen_date))
    month_key = f"{today.year}-{today.month:02d}"
    settings_key = f"last_task_gen_month:{garden_id}"
    rain_pending_prefix = f"{month_key}:rain_pending"

    row = db.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (settings_key,),
    ).fetchone()
    if row:
        marker = str(row["value"])
        if marker == month_key:
            return {"tasks_skipped": True}
        if marker.startswith(f"{rain_pending_prefix}:"):
            signature = _monthly_rain_generation_signature(
                db,
                garden_id=garden_id,
                year=today.year,
                month=today.month,
            )
            if marker == f"{rain_pending_prefix}:{signature}":
                return {"tasks_skipped": True, "tasks_rain_pending": True}

    result = generate_tasks(
        db,
        garden_id,
        today.month,
        today.year,
        actor_user_id=None,
        now_ms=now_ms,
    )
    rain_suppressed = int(result.get("rain_suppressed", 0))
    generation_marker = month_key
    if rain_suppressed:
        signature = _monthly_rain_generation_signature(
            db,
            garden_id=garden_id,
            year=today.year,
            month=today.month,
        )
        generation_marker = f"{rain_pending_prefix}:{signature}"

    db.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (settings_key, generation_marker),
    )
    db.commit()

    notification_result = {"created": 0, "skipped": 0}
    if result.get("created", 0) > 0:
        month_name = today.strftime("%B %Y")
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
            now_ms=now_ms,
        )

    return {
        "tasks_created": result.get("created", 0),
        "tasks_skipped_dedup": result.get("skipped", 0),
        "tasks_rain_suppressed": rain_suppressed,
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
        "weather_tasks_created": 0,
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
    now_value, frozen_date = notification_request_clock(now_ms=now_ms)
    summary = _empty_maintenance_summary()
    for garden_id in garden_ids:
        summary["gardens_processed"] = int(summary["gardens_processed"]) + 1

        tasks_expired = expire_stale_generated_tasks(
            db,
            garden_id=garden_id,
            today_iso=frozen_date,
            now_ms=now_value,
        )
        summary["tasks_expired"] = int(summary["tasks_expired"]) + tasks_expired

        generated = create_task_due_notifications(db, garden_id, now_ms=now_value)
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
            today_iso=frozen_date,
            now_ms=now_value,
        )
        summary["notifications_marked"] = int(summary["notifications_marked"]) + stale_tasks
        stale_info = clear_stale_informational_notifications(
            db,
            garden_id=garden_id,
            today_iso=frozen_date,
            now_ms=now_value,
        )
        summary["notifications_marked"] = int(summary["notifications_marked"]) + stale_info
        if tasks_expired or expired or stale_tasks or stale_info:
            db.commit()

        gen_result = _auto_generate_monthly_tasks(
            db,
            garden_id,
            now_value,
            frozen_date=frozen_date,
        )
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
        summary["weather_tasks_created"] = int(summary["weather_tasks_created"]) + int(
            weather_result.get("weather_tasks_created", 0)
        )
        summary["notifications_created"] = int(summary["notifications_created"]) + int(
            weather_result.get("weather_notifications_created", 0)
        )
        summary["notifications_skipped"] = int(summary["notifications_skipped"]) + int(
            weather_result.get("weather_notifications_skipped", 0)
        )

        escalation_result = escalate_overdue_follow_ups(
            db,
            garden_id,
            today_iso=_notification_today_iso(frozen_date=frozen_date),
            now_ms=now_value,
        )
        summary["issues_escalated"] = int(summary.get("issues_escalated", 0)) + int(
            escalation_result.get("escalated", 0)
        )

        delivered = deliver_pending_email_digests(
            db,
            garden_id,
            email_sender=email_sender,
            now_ms=now_value,
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
