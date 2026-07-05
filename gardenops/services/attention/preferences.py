from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal

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
    "weather_alert:frost_warning": "frost_warning",
}
_ATTENTION_RULE_KEYS = {
    "needs_action",
    "warning",
    "upcoming",
    "no_action_needed",
    "system",
    "task_due",
    "task_overdue",
    "task_snoozed_active",
    "task_completed",
    "task_skipped",
    "frost_warning",
    "rain_alert",
    "watering_covered_by_rain",
    "watering_rescheduled_by_rain",
    "issue_follow_up_due",
    "issue_follow_up_overdue",
    "calendar_event_due",
}
_GUARDRAIL_TYPES = {
    "frost_warning",
    "security_alert",
    "safety_alert",
    "system",
}


@dataclass(frozen=True)
class AttentionPreferenceSet:
    user_id: int
    preset: str = "balanced"
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    quiet_hours: dict[str, Any] = field(default_factory=dict)
    show_no_action_history: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


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
            "task_snoozed_active": _copy_rule(panel_first),
            "task_completed": _copy_rule(panel_first),
            "task_skipped": _copy_rule(panel_first),
            "frost_warning": _copy_rule(category_rules["warning"]),
            "rain_alert": _copy_rule(category_rules["warning"]),
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


def _preference_set_from_saved(
    user_id: int, saved_attention_preferences: Any
) -> AttentionPreferenceSet:
    if isinstance(saved_attention_preferences, AttentionPreferenceSet):
        return saved_attention_preferences
    saved = _parse_mapping(saved_attention_preferences)
    preset = str(saved.get("preset") or "balanced").strip().lower() or "balanced"
    rules = _parse_mapping(saved.get("rules") or saved.get("rules_json"))
    quiet_hours = _parse_mapping(saved.get("quiet_hours") or saved.get("quiet_hours_json"))
    show_history = bool(saved.get("show_no_action_history", True))
    return AttentionPreferenceSet(
        user_id=int(saved.get("user_id") or user_id),
        preset=preset,
        rules=rules or _preset_rules(preset),
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
    global_inbox = bool(legacy_preferences.get("in_app_enabled", True))
    global_digest = bool(legacy_preferences.get("email_enabled", False))
    for rule_key, legacy_rule in _parse_mapping(
        legacy_preferences.get("notification_rules")
    ).items():
        if not isinstance(legacy_rule, dict):
            continue
        raw_rule_key = str(rule_key)
        normalized_rule_key = _LEGACY_RULE_KEY_MAP.get(raw_rule_key)
        if normalized_rule_key is None and raw_rule_key in _ATTENTION_RULE_KEYS:
            normalized_rule_key = raw_rule_key
        if normalized_rule_key is None:
            unknown_legacy_rules[raw_rule_key] = dict(legacy_rule)
            continue
        rules[normalized_rule_key] = {
            "panel": True,
            "inbox": global_inbox and bool(legacy_rule.get("in_app_enabled", True)),
            "digest": global_digest and bool(legacy_rule.get("email_enabled", False)),
            "min_severity": normalize_severity(str(legacy_rule.get("min_severity", "low"))),
        }
    if not global_inbox:
        for rule in rules.values():
            rule["inbox"] = False
    if not global_digest:
        for rule in rules.values():
            rule["digest"] = False
    if unknown_legacy_rules:
        metadata["legacy_notification_rules"] = unknown_legacy_rules
    return rules, metadata


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
    if item.domain_state in {"completed", "skipped"}:
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
    return bool(rule.get(surface, default))


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


def _quiet_hours_active(quiet_hours: dict[str, Any], surface: AttentionSurface) -> bool:
    if surface not in {"digest", "interruptive"}:
        return False
    value = quiet_hours.get(surface, quiet_hours.get("active", False))
    if isinstance(value, dict):
        return bool(
            value.get("active")
            or value.get("is_active")
            or value.get("quiet")
            or value.get("in_quiet_hours")
        )
    return bool(value)


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
        if _quiet_hours_active(preferences.quiet_hours, surface):
            continue
        visible.append(item)
    return visible
