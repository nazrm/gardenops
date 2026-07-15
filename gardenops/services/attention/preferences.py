from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gardenops.services.attention.types import (
    SEVERITY_RANK,
    AttentionItem,
    normalize_severity,
)

AttentionSurface = Literal["panel", "inbox", "digest", "interruptive"]

_NON_ACTIVE_DOMAIN_STATES = {
    "completed",
    "skipped",
    "dismissed",
    "expired",
    "superseded",
}
_HIDDEN_USER_STATES = {"dismissed", "snoozed", "preference_hidden"}
_PANEL_ELIGIBILITY = {"panel_only", "inbox", "digest", "interruptive"}
_NON_EMAIL_SURFACES = {"panel", "inbox"}
_LEGACY_RULE_KEY_MAP = {
    "task_due": ("task_due",),
    "task_overdue": ("task_overdue",),
    "task_upcoming": ("task_upcoming",),
    "task_generated": ("task_generated",),
    "issue_created": ("issue_follow_up_due", "issue_follow_up_overdue"),
    "weather_alert:frost_warning": ("frost_warning",),
    "weather_alert:rain_surplus": ("rain_alert",),
    "weather_alert:heat_wave": ("heat_wave",),
    "weather_alert:dry_spell": ("dry_spell",),
}
_ATTENTION_RULE_KEYS = {
    "needs_action",
    "warning",
    "upcoming",
    "no_action_needed",
    "system",
    "task_due",
    "task_overdue",
    "task_upcoming",
    "task_generated",
    "task_snoozed_active",
    "task_completed",
    "task_skipped",
    "task_expired",
    "weather_alert",
    "frost_warning",
    "rain_alert",
    "heat_wave",
    "dry_spell",
    "watering_covered_by_rain",
    "watering_rescheduled_by_rain",
    "issue_follow_up_due",
    "issue_follow_up_overdue",
    "calendar_event_due",
    "calendar_event_upcoming",
}
_GUARDRAIL_TYPES = {
    "frost_warning",
    "security_alert",
    "safety_alert",
    "system",
}
_VALID_PRESETS = {"calm", "balanced", "detailed", "custom"}
_RULE_BOOLEAN_FIELDS = {"enabled", "panel", "inbox", "digest", "interruptive"}
_RULE_FIELDS = _RULE_BOOLEAN_FIELDS | {"min_severity"}
_CUSTOM_RULE_KEYS = _ATTENTION_RULE_KEYS | {"default", "security_alert", "safety_alert"}
_QUIET_HOUR_SURFACES = {"digest", "interruptive"}
_QUIET_HOUR_FIELDS = {"enabled", "start", "end"}


@dataclass(frozen=True)
class AttentionPreferenceSet:
    user_id: int
    preset: str = "balanced"
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    quiet_hours: dict[str, Any] = field(default_factory=dict)
    show_no_action_history: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


def apply_digest_delivery_capability(
    preferences: AttentionPreferenceSet,
    *,
    configured: bool,
) -> AttentionPreferenceSet:
    """Disable digest rules when global email delivery is not fully configured."""
    if configured:
        return preferences
    rules: dict[str, dict[str, Any]] = {}
    for key, rule in preferences.rules.items():
        constrained_rule = dict(rule)
        if constrained_rule.get("digest") is True:
            constrained_rule["digest"] = False
        rules[key] = constrained_rule
    return replace(preferences, rules=rules)


def _preset_rules(preset: str) -> dict[str, dict[str, Any]]:
    if preset == "calm":
        return _with_planned_type_rules(
            {
                "needs_action": {
                    "panel": True,
                    "inbox": False,
                    "digest": False,
                    "min_severity": "low",
                },
                "warning": {
                    "panel": True,
                    "inbox": True,
                    "digest": False,
                    "min_severity": "high",
                },
                "upcoming": {"panel": False, "inbox": False, "digest": False},
                "no_action_needed": {"panel": True, "inbox": False, "digest": False},
                "system": {
                    "panel": True,
                    "inbox": True,
                    "digest": False,
                    "min_severity": "low",
                },
            }
        )
    if preset == "detailed":
        return _with_planned_type_rules(
            {
                "needs_action": {
                    "panel": True,
                    "inbox": True,
                    "digest": True,
                    "min_severity": "low",
                },
                "warning": {
                    "panel": True,
                    "inbox": True,
                    "digest": True,
                    "min_severity": "low",
                },
                "upcoming": {"panel": True, "inbox": True, "digest": False},
                "no_action_needed": {"panel": True, "inbox": False, "digest": False},
                "system": {
                    "panel": True,
                    "inbox": True,
                    "digest": True,
                    "min_severity": "low",
                },
            }
        )
    return _with_planned_type_rules(
        {
            "needs_action": {
                "panel": True,
                "inbox": True,
                "digest": False,
                "min_severity": "low",
            },
            "task_due": {
                "panel": True,
                "inbox": True,
                "digest": False,
                "min_severity": "low",
            },
            "warning": {
                "panel": True,
                "inbox": True,
                "digest": True,
                "min_severity": "normal",
            },
            "upcoming": {
                "panel": True,
                "inbox": False,
                "digest": False,
                "min_severity": "high",
            },
            "no_action_needed": {"panel": True, "inbox": False, "digest": False},
            "system": {
                "panel": True,
                "inbox": True,
                "digest": False,
                "min_severity": "low",
            },
        }
    )


def _copy_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return dict(rule)


def _panel_first_rule() -> dict[str, Any]:
    return {"panel": True, "inbox": False, "digest": False, "min_severity": "low"}


def _visible_action_rule(category_rules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rule = _copy_rule(category_rules["needs_action"])
    rule["panel"] = True
    rule["inbox"] = True
    return rule


def _with_planned_type_rules(
    category_rules: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rules = {key: _copy_rule(rule) for key, rule in category_rules.items()}
    panel_first = _panel_first_rule()
    rules.update(
        {
            "task_due": _copy_rule(category_rules["needs_action"]),
            "task_overdue": _visible_action_rule(category_rules),
            "task_upcoming": _copy_rule(category_rules["upcoming"]),
            "task_generated": _copy_rule(panel_first),
            "task_snoozed_active": _copy_rule(panel_first),
            "task_completed": _copy_rule(panel_first),
            "task_skipped": _copy_rule(panel_first),
            "task_expired": _copy_rule(panel_first),
            "weather_alert": _copy_rule(category_rules["warning"]),
            "frost_warning": _copy_rule(category_rules["warning"]),
            "rain_alert": _copy_rule(category_rules["warning"]),
            "heat_wave": _copy_rule(category_rules["warning"]),
            "dry_spell": _copy_rule(category_rules["warning"]),
            "watering_covered_by_rain": _copy_rule(panel_first),
            "watering_rescheduled_by_rain": _copy_rule(panel_first),
            "issue_follow_up_due": _visible_action_rule(category_rules),
            "issue_follow_up_overdue": _visible_action_rule(category_rules),
            "calendar_event_due": _copy_rule(category_rules["upcoming"]),
            "system": _copy_rule(category_rules["system"]),
        }
    )
    return rules


def _parse_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _require_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _normalize_rule(rule_key: str, raw_rule: Any) -> dict[str, Any]:
    if not isinstance(raw_rule, dict):
        raise ValueError(f"rules.{rule_key} must be an object")
    unknown_fields = set(raw_rule) - _RULE_FIELDS
    if unknown_fields:
        invalid = ", ".join(sorted(str(field) for field in unknown_fields))
        raise ValueError(f"rules.{rule_key} has unsupported fields: {invalid}")
    if not raw_rule:
        raise ValueError(f"rules.{rule_key} must define at least one field")

    normalized: dict[str, Any] = {}
    for rule_field in _RULE_BOOLEAN_FIELDS:
        if rule_field in raw_rule:
            normalized[rule_field] = _require_bool(
                raw_rule[rule_field],
                field_name=f"rules.{rule_key}.{rule_field}",
            )
    if "min_severity" in raw_rule:
        severity = raw_rule["min_severity"]
        if not isinstance(severity, str) or severity.strip().lower() not in SEVERITY_RANK:
            raise ValueError(f"rules.{rule_key}.min_severity must be a supported severity")
        normalized["min_severity"] = severity.strip().lower()
    if normalized.pop("enabled", True) is False:
        for surface in ("panel", "inbox", "digest", "interruptive"):
            normalized[surface] = False
    return normalized


def _normalize_timezone(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("quiet_hours.timezone must be an IANA timezone")
    timezone = value.strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("quiet_hours.timezone must be an IANA timezone") from exc
    return timezone


def normalize_attention_quiet_hours(
    quiet_hours: Any,
    *,
    strict: bool,
) -> dict[str, Any]:
    """Normalize canonical quiet hours while retaining old delivery windows on read."""
    if not isinstance(quiet_hours, dict):
        if strict:
            raise ValueError("quiet_hours must be an object")
        return {}

    if not strict:
        # Legacy notification preferences carry start/end at the top level. They
        # remain UTC until the browser saves a canonical IANA timezone.
        if any(
            key in quiet_hours for key in ("start", "from", "start_hour", "end", "to", "end_hour")
        ):
            return dict(quiet_hours)

    normalized: dict[str, Any] = {}
    unknown_fields = set(quiet_hours) - (_QUIET_HOUR_SURFACES | {"timezone"})
    if unknown_fields:
        if strict:
            invalid = ", ".join(sorted(str(field) for field in unknown_fields))
            raise ValueError(f"quiet_hours has unsupported fields: {invalid}")
        return normalized
    if "timezone" in quiet_hours:
        try:
            normalized["timezone"] = _normalize_timezone(quiet_hours["timezone"])
        except ValueError:
            if strict:
                raise

    for surface in _QUIET_HOUR_SURFACES:
        if surface not in quiet_hours:
            continue
        raw_window = quiet_hours[surface]
        if not isinstance(raw_window, dict):
            if strict:
                raise ValueError(f"quiet_hours.{surface} must be an object")
            continue
        unknown_window_fields = set(raw_window) - _QUIET_HOUR_FIELDS
        if unknown_window_fields:
            if strict:
                invalid = ", ".join(sorted(str(field) for field in unknown_window_fields))
                raise ValueError(f"quiet_hours.{surface} has unsupported fields: {invalid}")
            continue
        enabled = raw_window.get("enabled", False)
        if not isinstance(enabled, bool):
            if strict:
                raise ValueError(f"quiet_hours.{surface}.enabled must be a boolean")
            enabled = False
        start = raw_window.get("start")
        end = raw_window.get("end")
        if enabled and (_quiet_minute(start) is None or _quiet_minute(end) is None):
            if strict:
                raise ValueError(f"quiet_hours.{surface} requires valid start and end times")
            continue
        window: dict[str, Any] = {"enabled": enabled}
        if _quiet_minute(start) is not None:
            window["start"] = str(start).strip()
        if _quiet_minute(end) is not None:
            window["end"] = str(end).strip()
        normalized[surface] = window
    return normalized


def normalize_attention_preference_payload(
    *,
    preset: Any,
    rules: Any,
    quiet_hours: Any,
    show_no_action_history: Any,
    metadata: Any,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any], bool, dict[str, Any]]:
    """Validate the client payload before it becomes durable preference state."""
    if not isinstance(preset, str) or preset.strip().lower() not in _VALID_PRESETS:
        raise ValueError("Invalid attention preference preset")
    normalized_preset = preset.strip().lower()
    if not isinstance(rules, dict):
        raise ValueError("rules must be an object")
    if normalized_preset != "custom" and rules and rules != _preset_rules(normalized_preset):
        raise ValueError("Rules can only be supplied with the custom preset")

    normalized_rules: dict[str, dict[str, Any]]
    if normalized_preset == "custom":
        if not rules:
            raise ValueError("Custom attention preferences require at least one rule")
        normalized_rules = {}
        for raw_key, raw_rule in rules.items():
            if not isinstance(raw_key, str) or raw_key not in _CUSTOM_RULE_KEYS:
                raise ValueError(f"Unsupported attention rule: {raw_key}")
            normalized_rules[raw_key] = _normalize_rule(raw_key, raw_rule)
    else:
        # Presets are expanded when read. Persisting an empty rule set avoids a
        # contradictory fixed-preset row that also contains custom overrides.
        normalized_rules = {}

    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return (
        normalized_preset,
        normalized_rules,
        normalize_attention_quiet_hours(quiet_hours, strict=True),
        _require_bool(show_no_action_history, field_name="show_no_action_history"),
        dict(metadata),
    )


def _preference_set_from_saved(
    user_id: int, saved_attention_preferences: Any
) -> AttentionPreferenceSet:
    if isinstance(saved_attention_preferences, AttentionPreferenceSet):
        return saved_attention_preferences
    saved = _parse_mapping(saved_attention_preferences)
    preset = str(saved.get("preset") or "balanced").strip().lower() or "balanced"
    if preset not in _VALID_PRESETS:
        preset = "balanced"
    rules = _parse_mapping(saved.get("rules") or saved.get("rules_json"))
    try:
        normalized_rules = (
            {
                key: _normalize_rule(key, rule)
                for key, rule in rules.items()
                if key in _CUSTOM_RULE_KEYS and isinstance(key, str)
            }
            if preset == "custom"
            else _preset_rules(preset)
        )
    except ValueError:
        normalized_rules = {}
    if preset == "custom" and not normalized_rules:
        preset = "balanced"
        normalized_rules = _preset_rules(preset)
    quiet_hours = normalize_attention_quiet_hours(
        _parse_mapping(saved.get("quiet_hours") or saved.get("quiet_hours_json")),
        strict=False,
    )
    show_history = saved.get("show_no_action_history", True)
    if not isinstance(show_history, bool):
        show_history = True
    return AttentionPreferenceSet(
        user_id=int(saved.get("user_id") or user_id),
        preset=preset,
        rules=normalized_rules or _preset_rules("balanced"),
        quiet_hours=quiet_hours,
        show_no_action_history=show_history,
        metadata=_parse_mapping(saved.get("metadata")),
    )


def _legacy_rules_and_metadata(
    legacy_preferences: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    rules = _preset_rules("balanced")
    metadata: dict[str, Any] = {}
    unknown_legacy_rules: dict[str, Any] = {}
    for rule_key, legacy_rule in _parse_mapping(
        legacy_preferences.get("notification_rules")
    ).items():
        if not isinstance(legacy_rule, dict):
            continue
        raw_rule_key = str(rule_key)
        normalized_rule_keys = _LEGACY_RULE_KEY_MAP.get(raw_rule_key)
        if normalized_rule_keys is None and raw_rule_key in _ATTENTION_RULE_KEYS:
            normalized_rule_keys = (raw_rule_key,)
        if normalized_rule_keys is None:
            unknown_legacy_rules[raw_rule_key] = dict(legacy_rule)
            continue
        legacy_enabled = legacy_rule.get("in_app_enabled", True)
        if not isinstance(legacy_enabled, bool):
            legacy_enabled = True
        legacy_email_enabled = legacy_rule.get("email_enabled", legacy_enabled)
        if not isinstance(legacy_email_enabled, bool):
            legacy_email_enabled = legacy_enabled
        for normalized_rule_key in normalized_rule_keys:
            rules[normalized_rule_key] = {
                "panel": True,
                "inbox": legacy_enabled,
                "digest": legacy_email_enabled,
                "min_severity": normalize_severity(str(legacy_rule.get("min_severity", "low"))),
            }
    if unknown_legacy_rules:
        metadata["legacy_notification_rules"] = unknown_legacy_rules
    return rules, metadata


def _normalize_notification_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "in_app_enabled": (
            rule["in_app_enabled"] if isinstance(rule.get("in_app_enabled"), bool) else True
        ),
        "email_enabled": (
            rule["email_enabled"] if isinstance(rule.get("email_enabled"), bool) else True
        ),
        "min_severity": normalize_severity(str(rule.get("min_severity", "low"))),
    }


def _notification_rule_projection(
    rules: dict[str, dict[str, Any]],
    target_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    grouped_rules = [rules[target_key] for target_key in target_keys if target_key in rules]
    if not grouped_rules:
        return None
    severities = [
        normalize_severity(str(rule.get("min_severity", "low"))) for rule in grouped_rules
    ]
    return {
        "in_app_enabled": all(bool(rule.get("inbox", False)) for rule in grouped_rules),
        "email_enabled": all(bool(rule.get("digest", False)) for rule in grouped_rules),
        "min_severity": max(severities, key=lambda severity: SEVERITY_RANK[severity]),
    }


def merge_notification_preferences(
    preferences: AttentionPreferenceSet,
    *,
    notification_rules: dict[str, dict[str, Any]],
    quiet_hours: dict[str, Any],
    notification_rule_keys: set[str] | None = None,
) -> AttentionPreferenceSet:
    """Project notification settings into the canonical Attention preference set.

    Global in-app/email switches remain delivery capabilities in the legacy row.
    This projection owns per-category eligibility and the digest quiet window.
    """
    rules = {key: _copy_rule(rule) for key, rule in preferences.rules.items()}
    preset_rules = _preset_rules(
        preferences.preset if preferences.preset in {"calm", "balanced", "detailed"} else "balanced"
    )
    for legacy_key, target_keys in _LEGACY_RULE_KEY_MAP.items():
        if notification_rule_keys is not None and legacy_key not in notification_rule_keys:
            continue
        legacy_rule = notification_rules.get(legacy_key)
        if not isinstance(legacy_rule, dict):
            continue
        projected_rule = _notification_rule_projection(rules, target_keys)
        normalized_rule = _normalize_notification_rule(legacy_rule)
        if projected_rule == normalized_rule:
            continue
        for target_key in target_keys:
            rule = _copy_rule(rules.get(target_key) or preset_rules.get(target_key) or {})
            rule.setdefault("panel", True)
            rule["inbox"] = normalized_rule["in_app_enabled"]
            rule["digest"] = normalized_rule["email_enabled"]
            rule["min_severity"] = normalized_rule["min_severity"]
            rules[target_key] = rule

    normalized_quiet_hours = dict(preferences.quiet_hours)
    for legacy_key in ("active", "end", "end_hour", "from", "start", "start_hour", "to"):
        normalized_quiet_hours.pop(legacy_key, None)
    start = quiet_hours.get("start")
    end = quiet_hours.get("end")
    if isinstance(start, str) and start.strip() and isinstance(end, str) and end.strip():
        normalized_quiet_hours["digest"] = {
            "enabled": True,
            "start": start.strip(),
            "end": end.strip(),
        }
    else:
        current_digest = normalized_quiet_hours.get("digest")
        if not isinstance(current_digest, dict) or bool(current_digest.get("enabled")):
            normalized_quiet_hours.pop("digest", None)
    timezone = quiet_hours.get("timezone")
    if timezone is not None:
        normalized_quiet_hours["timezone"] = _normalize_timezone(timezone)

    return replace(
        preferences,
        preset="custom",
        rules=rules,
        quiet_hours=normalized_quiet_hours,
    )


def notification_rules_from_attention(
    preferences: AttentionPreferenceSet,
) -> dict[str, dict[str, Any]]:
    """Return the notification-rule fields represented by Attention rules."""
    projected: dict[str, dict[str, Any]] = {}
    for legacy_key, target_keys in _LEGACY_RULE_KEY_MAP.items():
        rule = _notification_rule_projection(preferences.rules, target_keys)
        if rule is None:
            continue
        projected[legacy_key] = rule
    return projected


def notification_quiet_hours_from_attention(
    preferences: AttentionPreferenceSet,
) -> dict[str, str] | None:
    """Project an explicitly configured digest quiet window for the legacy UI."""
    digest = preferences.quiet_hours.get("digest")
    if not isinstance(digest, dict):
        return None
    if not bool(digest.get("enabled")):
        return {}
    start = digest.get("start")
    end = digest.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return {}
    projected = {"start": start, "end": end}
    timezone = preferences.quiet_hours.get("timezone")
    if isinstance(timezone, str):
        try:
            projected["timezone"] = _normalize_timezone(timezone)
        except ValueError:
            pass
    return projected


def resolve_attention_preferences(
    *,
    user_id: int,
    legacy_preferences: dict[str, Any] | None,
    saved_attention_preferences: Any,
) -> AttentionPreferenceSet:
    if saved_attention_preferences is not None:
        return _preference_set_from_saved(user_id, saved_attention_preferences)
    if legacy_preferences is not None:
        rules, metadata = _legacy_rules_and_metadata(legacy_preferences)
        return AttentionPreferenceSet(
            user_id=user_id,
            preset="custom",
            rules=rules,
            quiet_hours={},
            show_no_action_history=True,
            metadata=metadata,
        )
    return AttentionPreferenceSet(
        user_id=user_id,
        preset="balanced",
        rules=_preset_rules("balanced"),
        quiet_hours={},
        show_no_action_history=True,
    )


def _domain_state_allows(item: AttentionItem, surface: AttentionSurface) -> bool:
    if item.domain_state == "active":
        return item.category != "no_action_needed" or surface == "panel"
    if item.domain_state == "no_action_needed":
        return item.category == "no_action_needed" and surface == "panel"
    if item.domain_state in {"completed", "skipped", "expired"}:
        return item.category == "no_action_needed" and surface == "panel"
    return item.domain_state not in _NON_ACTIVE_DOMAIN_STATES


def _rule_for_item(item: AttentionItem, preferences: AttentionPreferenceSet) -> dict[str, Any]:
    return (
        preferences.rules.get(item.type)
        or preferences.rules.get(item.category)
        or preferences.rules.get(item.provider)
        or preferences.rules.get("default")
        or {}
    )


def _rule_allows_surface(rule: dict[str, Any], surface: AttentionSurface) -> bool:
    if rule.get("enabled") is False:
        return False
    default = surface == "panel"
    value = rule.get(surface, default)
    return value is True


def _rule_allows_severity(rule: dict[str, Any], item: AttentionItem) -> bool:
    min_severity = normalize_severity(str(rule.get("min_severity", "low")))
    item_severity = normalize_severity(item.severity)
    return SEVERITY_RANK[item_severity] >= SEVERITY_RANK[min_severity]


def _is_guardrail_item(item: AttentionItem) -> bool:
    if SEVERITY_RANK[normalize_severity(item.severity)] < SEVERITY_RANK["high"]:
        return False
    if item.metadata.get("guardrail") is True or item.metadata.get("is_guardrail") is True:
        return True
    return item.type in _GUARDRAIL_TYPES or item.category == "system"


def _is_non_configurable_system_notification(item: AttentionItem) -> bool:
    return item.provider == "notification_status" and (
        item.type == "system" or item.metadata.get("notification_type") == "system"
    )


def _quiet_minute(value: Any) -> int | None:
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


def _minute_in_quiet_window(*, current_minute: int, start_minute: int, end_minute: int) -> bool:
    if start_minute == end_minute:
        return False
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def _quiet_window_bound(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _quiet_hours_active(
    quiet_hours: dict[str, Any],
    surface: AttentionSurface,
    *,
    now_ms: int | None,
) -> bool:
    if surface not in {"digest", "interruptive"}:
        return False
    is_legacy_window = False
    if surface in quiet_hours:
        value = quiet_hours[surface]
    elif "active" in quiet_hours:
        value = quiet_hours["active"]
    elif any(
        key in quiet_hours for key in ("start", "from", "start_hour", "end", "to", "end_hour")
    ):
        # A legacy user_notification_preferences row has one schedule for
        # delivery. Treat it as a normalized fallback window for each
        # delivery surface without imposing an unrelated enabled flag.
        value = quiet_hours
        is_legacy_window = True
    else:
        value = False
    if isinstance(value, dict):
        if any(
            value.get(key) is True for key in ("active", "is_active", "quiet", "in_quiet_hours")
        ):
            return True
        if not is_legacy_window and value.get("enabled") is not True:
            return False
        start_minute = _quiet_minute(_quiet_window_bound(value, ("start", "from", "start_hour")))
        end_minute = _quiet_minute(_quiet_window_bound(value, ("end", "to", "end_hour")))
        if start_minute is None or end_minute is None:
            return False
        now_utc = (
            datetime.fromtimestamp(now_ms / 1000, tz=UTC)
            if now_ms is not None
            else datetime.now(UTC)
        )
        timezone_name = quiet_hours.get("timezone")
        try:
            timezone = ZoneInfo(timezone_name) if isinstance(timezone_name, str) else UTC
        except ZoneInfoNotFoundError:
            timezone = UTC
        now_local = now_utc.astimezone(timezone)
        return _minute_in_quiet_window(
            current_minute=now_local.hour * 60 + now_local.minute,
            start_minute=start_minute,
            end_minute=end_minute,
        )
    return value is True


def _channel_eligible(item: AttentionItem, surface: AttentionSurface) -> bool:
    eligibility = set(item.delivery_eligibility)
    if surface == "panel":
        return bool(eligibility & _PANEL_ELIGIBILITY)
    if surface == "inbox":
        return "inbox" in eligibility
    if surface == "digest":
        return "digest" in eligibility
    return "interruptive" in eligibility


def apply_preferences(
    items: list[AttentionItem],
    preferences: AttentionPreferenceSet,
    *,
    surface: AttentionSurface,
    now_ms: int | None = None,
    respect_quiet_hours: bool = True,
) -> list[AttentionItem]:
    visible: list[AttentionItem] = []
    for item in items:
        if not _domain_state_allows(item, surface):
            continue
        if item.category == "no_action_needed" and not preferences.show_no_action_history:
            continue
        if item.user_state in _HIDDEN_USER_STATES:
            continue
        if not _channel_eligible(item, surface):
            continue
        if _is_non_configurable_system_notification(item):
            if respect_quiet_hours and _quiet_hours_active(
                preferences.quiet_hours,
                surface,
                now_ms=now_ms,
            ):
                continue
            visible.append(
                replace(
                    item,
                    metadata={
                        **item.metadata,
                        "preference_guardrail": True,
                    },
                )
            )
            continue
        if _is_guardrail_item(item) and surface in _NON_EMAIL_SURFACES:
            visible.append(
                replace(
                    item,
                    metadata={
                        **item.metadata,
                        "preference_guardrail": True,
                    },
                )
            )
            continue

        rule = _rule_for_item(item, preferences)
        if not _rule_allows_severity(rule, item):
            continue
        if not _rule_allows_surface(rule, surface):
            continue
        if respect_quiet_hours and _quiet_hours_active(
            preferences.quiet_hours,
            surface,
            now_ms=now_ms,
        ):
            continue
        visible.append(item)
    return visible
