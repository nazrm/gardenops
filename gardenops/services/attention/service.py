from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any, Literal, cast

from fastapi import HTTPException

from gardenops.services.attention.preferences import (
    AttentionPreferenceSet,
    apply_preferences,
    resolve_attention_preferences,
)
from gardenops.services.attention.providers import (
    CalendarAttentionProvider,
    IssueAttentionProvider,
    NotificationStatusAttentionProvider,
    TaskAttentionProvider,
    WeatherAttentionProvider,
)
from gardenops.services.attention.ranking import group_attention_items, rank_attention_items
from gardenops.services.attention.types import (
    AttentionAction,
    AttentionItem,
    AttentionProvider,
    AttentionUserState,
)
from gardenops.services.task_generator import restore_generated_watering_task_from_attention_outcome

AttentionPreset = Literal["calm", "balanced", "detailed", "custom"]

_VALID_PRESETS: set[str] = {"calm", "balanced", "detailed", "custom"}
_VALID_USER_STATES: set[str] = {"read", "dismissed", "snoozed", "preference_hidden"}
_SECTION_KEYS = ("needs_attention", "warnings", "coming_up", "no_action_needed")
_SECTION_LIMITS = {
    "needs_attention": 5,
    "warnings": 5,
    "coming_up": 5,
    "no_action_needed": 5,
}
logger = logging.getLogger(__name__)


def _parse_mapping(value: Any) -> dict[str, Any]:
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


def _dump_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _serialize_action(action: AttentionAction | None) -> dict[str, Any] | None:
    if action is None:
        return None
    return {
        "kind": action.kind,
        "label": action.label,
        "target_type": action.target_type,
        "target_id": action.target_id,
        "metadata": action.metadata,
    }


def serialize_attention_preferences(preferences: AttentionPreferenceSet) -> dict[str, Any]:
    return {
        "user_id": preferences.user_id,
        "preset": preferences.preset,
        "rules": preferences.rules,
        "quiet_hours": preferences.quiet_hours,
        "show_no_action_history": preferences.show_no_action_history,
        "metadata": preferences.metadata,
    }


def load_attention_preferences(conn: Any, user_id: int | None) -> AttentionPreferenceSet:
    if user_id is None:
        return resolve_attention_preferences(
            user_id=0,
            legacy_preferences=None,
            saved_attention_preferences=None,
        )

    saved = conn.execute(
        """
        SELECT user_id, preset, rules_json, quiet_hours_json, show_no_action_history,
               metadata_json
        FROM user_attention_preferences
        WHERE user_id = %s
        """,
        (user_id,),
    ).fetchone()
    if saved is not None:
        return resolve_attention_preferences(
            user_id=user_id,
            legacy_preferences=None,
            saved_attention_preferences={
                "user_id": int(saved["user_id"]),
                "preset": str(saved["preset"]),
                "rules_json": str(saved["rules_json"] or "{}"),
                "quiet_hours_json": str(saved["quiet_hours_json"] or "{}"),
                "show_no_action_history": bool(saved["show_no_action_history"]),
                "metadata": str(saved["metadata_json"] or "{}"),
            },
        )

    legacy = conn.execute(
        """
        SELECT in_app_enabled, email_enabled, quiet_hours_json, rules_json,
               task_due_enabled, task_overdue_enabled
        FROM user_notification_preferences
        WHERE user_id = %s
        """,
        (user_id,),
    ).fetchone()
    if legacy is None:
        return resolve_attention_preferences(
            user_id=user_id,
            legacy_preferences=None,
            saved_attention_preferences=None,
        )

    notification_rules = _parse_mapping(legacy["rules_json"])
    if not notification_rules:
        notification_rules = {
            "task_due": {"in_app_enabled": bool(legacy["task_due_enabled"])},
            "task_overdue": {"in_app_enabled": bool(legacy["task_overdue_enabled"])},
        }
    preferences = resolve_attention_preferences(
        user_id=user_id,
        legacy_preferences={
            "in_app_enabled": bool(legacy["in_app_enabled"]),
            "email_enabled": bool(legacy["email_enabled"]),
            "notification_rules": notification_rules,
        },
        saved_attention_preferences=None,
    )
    return replace(preferences, quiet_hours=_parse_mapping(legacy["quiet_hours_json"]))


def save_attention_preferences(
    conn: Any,
    *,
    user_id: int,
    preset: str,
    rules: dict[str, dict[str, Any]],
    quiet_hours: dict[str, Any],
    show_no_action_history: bool,
    metadata: dict[str, Any] | None = None,
    now_ms: int,
) -> AttentionPreferenceSet:
    normalized_preset = preset.strip().lower()
    if normalized_preset not in _VALID_PRESETS:
        raise HTTPException(status_code=422, detail="Invalid attention preference preset")

    conn.execute(
        """
        INSERT INTO user_attention_preferences
            (user_id, preset, rules_json, quiet_hours_json, show_no_action_history,
             metadata_json, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            preset = excluded.preset,
            rules_json = excluded.rules_json,
            quiet_hours_json = excluded.quiet_hours_json,
            show_no_action_history = excluded.show_no_action_history,
            metadata_json = excluded.metadata_json,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            user_id,
            normalized_preset,
            _dump_json(cast(dict[str, Any], rules)),
            _dump_json(quiet_hours),
            int(show_no_action_history),
            _dump_json(metadata),
            now_ms,
            now_ms,
        ),
    )
    return load_attention_preferences(conn, user_id)


def load_user_attention_states(
    conn: Any,
    *,
    garden_id: int,
    user_id: int | None,
    now_ms: int,
) -> dict[str, dict[str, Any]]:
    if user_id is None:
        return {}
    rows = conn.execute(
        """
        SELECT item_id, user_state, snoozed_until_ms, reason, metadata_json, updated_at_ms
        FROM user_attention_item_state
        WHERE garden_id = %s
          AND user_id = %s
        """,
        (garden_id, user_id),
    ).fetchall()
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        user_state = str(row["user_state"])
        snoozed_until_ms = row["snoozed_until_ms"]
        if (
            user_state == "snoozed"
            and snoozed_until_ms is not None
            and int(snoozed_until_ms) <= now_ms
        ):
            continue
        states[str(row["item_id"])] = {
            "user_state": user_state,
            "snoozed_until_ms": int(snoozed_until_ms) if snoozed_until_ms is not None else None,
            "reason": str(row["reason"] or ""),
            "metadata": _parse_mapping(row["metadata_json"]),
            "updated_at_ms": int(row["updated_at_ms"] or 0),
        }
    return states


def set_user_attention_state(
    conn: Any,
    *,
    garden_id: int,
    user_id: int,
    item_id: str,
    user_state: str,
    now_ms: int,
    snoozed_until_ms: int | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if user_state not in _VALID_USER_STATES:
        raise HTTPException(status_code=422, detail="Invalid attention item state")
    if user_state == "snoozed" and snoozed_until_ms is None:
        raise HTTPException(status_code=422, detail="snoozed_until_ms is required")
    if user_state == "snoozed" and snoozed_until_ms is not None and snoozed_until_ms <= now_ms:
        raise HTTPException(status_code=422, detail="snoozed_until_ms must be in the future")

    conn.execute(
        """
        INSERT INTO user_attention_item_state
            (user_id, garden_id, item_id, user_state, snoozed_until_ms, reason,
             metadata_json, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(user_id, garden_id, item_id) DO UPDATE SET
            user_state = excluded.user_state,
            snoozed_until_ms = excluded.snoozed_until_ms,
            reason = excluded.reason,
            metadata_json = excluded.metadata_json,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            user_id,
            garden_id,
            item_id,
            user_state,
            snoozed_until_ms,
            reason,
            _dump_json(metadata),
            now_ms,
            now_ms,
        ),
    )


def restore_user_attention_state(
    conn: Any,
    *,
    garden_id: int,
    user_id: int,
    item_id: str,
) -> None:
    conn.execute(
        """
        DELETE FROM user_attention_item_state
        WHERE garden_id = %s
          AND user_id = %s
          AND item_id = %s
        """,
        (garden_id, user_id, item_id),
    )


def apply_user_states(
    items: list[AttentionItem],
    user_states: dict[str, dict[str, Any]],
    *,
    now_ms: int,
) -> list[AttentionItem]:
    updated: list[AttentionItem] = []
    for item in items:
        state = user_states.get(item.id)
        if state is None:
            updated.append(item)
            continue
        user_state = str(state.get("user_state") or "unread")
        snoozed_until_ms = state.get("snoozed_until_ms")
        if (
            user_state == "snoozed"
            and snoozed_until_ms is not None
            and int(snoozed_until_ms) <= now_ms
        ):
            updated.append(item)
            continue
        updated.append(
            replace(
                item,
                user_state=cast(AttentionUserState, user_state),
                metadata={
                    **item.metadata,
                    "user_state": {
                        "reason": str(state.get("reason") or ""),
                        "snoozed_until_ms": snoozed_until_ms,
                        "updated_at_ms": int(state.get("updated_at_ms") or 0),
                        "metadata": _parse_mapping(state.get("metadata")),
                    },
                },
            )
        )
    return updated


def _section_key(item: AttentionItem) -> str:
    if item.category == "needs_action":
        return "needs_attention"
    if item.category == "warning" or item.category == "system":
        return "warnings"
    if item.category == "upcoming":
        return "coming_up"
    return "no_action_needed"


def _serialize_item(item: AttentionItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "provider": item.provider,
        "type": item.type,
        "category": item.category,
        "severity": item.severity,
        "title": item.title,
        "body": item.body,
        "reason": item.reason,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "plot_ids": list(item.plot_ids),
        "plant_ids": list(item.plant_ids),
        "due_on": item.due_on,
        "domain_state": item.domain_state,
        "user_state": item.user_state,
        "primary_action": _serialize_action(item.primary_action),
        "secondary_actions": [_serialize_action(action) for action in item.secondary_actions],
        "metadata": item.metadata,
        "source_label": item.source_label,
        "updated_at_ms": item.updated_at_ms,
    }


def serialize_today_response(
    *,
    garden_id: int,
    generated_at_ms: int,
    items: list[AttentionItem],
    preferences: AttentionPreferenceSet,
    degraded_providers: list[dict[str, str]],
) -> dict[str, Any]:
    buckets: dict[str, list[AttentionItem]] = {key: [] for key in _SECTION_KEYS}
    for item in items:
        buckets[_section_key(item)].append(item)

    sections: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for key in _SECTION_KEYS:
        section_items = buckets[key]
        counts[key] = len(section_items)
        limit = _SECTION_LIMITS[key]
        sections.append(
            {
                "key": key,
                "count": len(section_items),
                "items": [_serialize_item(item) for item in section_items[:limit]],
            }
        )

    return {
        "garden_id": garden_id,
        "generated_at_ms": generated_at_ms,
        "sections": sections,
        "counts": {
            **counts,
            "total": sum(counts.values()),
        },
        "preferences": serialize_attention_preferences(preferences),
        "degraded_providers": degraded_providers,
    }


def restore_attention_outcome(
    conn: Any,
    *,
    garden_id: int,
    outcome_id: str,
    user_id: int | None,
    now_ms: int,
) -> str:
    row = conn.execute(
        """
        SELECT public_id, provider, outcome_type, source_public_id, target_type,
               target_id, plant_ids_json, plot_ids_json, metadata_json, recovery_action_json
        FROM attention_outcomes
        WHERE garden_id = %s
          AND public_id = %s
          AND expires_at_ms > %s
        """,
        (garden_id, outcome_id, now_ms),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Attention outcome not found")

    outcome_type = str(row["outcome_type"])
    if str(row["provider"]) != "weather" or outcome_type not in {
        "watering_covered_by_rain",
        "watering_rescheduled_by_rain",
    }:
        raise HTTPException(status_code=409, detail="Attention outcome restore is not supported")

    recovery_action = _parse_mapping(row["recovery_action_json"])
    if recovery_action.get("kind") != "restore_generated_watering_task":
        raise HTTPException(status_code=409, detail="Attention outcome recovery action is missing")
    if str(recovery_action.get("source_public_id") or "") != str(row["source_public_id"]):
        raise HTTPException(status_code=422, detail="Attention outcome recovery action is invalid")
    if str(recovery_action.get("target_type") or "") != str(row["target_type"]):
        raise HTTPException(status_code=422, detail="Attention outcome recovery action is invalid")
    if str(recovery_action.get("target_id") or "") != str(row["target_id"]):
        raise HTTPException(status_code=422, detail="Attention outcome recovery action is invalid")

    metadata = _parse_mapping(row["metadata_json"])
    metadata.setdefault("plant_ids", json.loads(str(row["plant_ids_json"] or "[]")))
    metadata.setdefault("plot_ids", json.loads(str(row["plot_ids_json"] or "[]")))
    restore_generated_watering_task_from_attention_outcome(
        conn,
        garden_id=garden_id,
        outcome_public_id=str(row["public_id"]),
        source_public_id=str(row["source_public_id"]),
        target_id=str(row["target_id"]),
        metadata=metadata,
        recovery_action=recovery_action,
        actor_user_id=user_id,
        now_ms=now_ms,
    )
    return "restored"


class AttentionService:
    def __init__(
        self,
        *,
        frozen_date: str | None = None,
        providers: list[AttentionProvider] | None = None,
    ) -> None:
        self.providers: list[AttentionProvider] = (
            list(providers)
            if providers is not None
            else [
                TaskAttentionProvider(frozen_date=frozen_date),
                WeatherAttentionProvider(frozen_date=frozen_date),
                IssueAttentionProvider(frozen_date=frozen_date),
                CalendarAttentionProvider(frozen_date=frozen_date),
                NotificationStatusAttentionProvider(),
            ]
        )

    def _collect_provider_items_with_degradation(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int | None,
        now_ms: int,
        force_degraded_provider: str | None = None,
    ) -> tuple[list[AttentionItem], list[dict[str, str]]]:
        degraded_providers: list[dict[str, str]] = []
        collected: list[AttentionItem] = []
        provider_user_id = int(user_id or 0)
        for idx, provider in enumerate(self.providers):
            if force_degraded_provider == provider.key:
                degraded_providers.append({"provider": provider.key, "reason": "forced_degraded"})
                continue
            savepoint = f"attention_provider_{idx}"
            savepoint_created = False
            try:
                conn.execute(f"SAVEPOINT {savepoint}")
                savepoint_created = True
                collected.extend(
                    provider.collect(
                        conn,
                        garden_id=garden_id,
                        user_id=provider_user_id,
                        now_ms=now_ms,
                    )
                )
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:  # noqa: BLE001 - provider degradation is intentional.
                logger.exception("Attention provider failed", extra={"provider": provider.key})
                if savepoint_created:
                    try:
                        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                    except Exception:
                        logger.exception(
                            "Attention provider savepoint cleanup failed",
                            extra={"provider": provider.key},
                        )
                else:
                    conn.rollback()
                degraded_providers.append({"provider": provider.key, "reason": "provider_failed"})
        return collected, degraded_providers

    def collect_provider_items(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int | None,
        now_ms: int,
    ) -> list[AttentionItem]:
        collected, _degraded = self._collect_provider_items_with_degradation(
            conn,
            garden_id=garden_id,
            user_id=user_id,
            now_ms=now_ms,
        )
        return collected

    def require_item(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int | None,
        item_id: str,
        now_ms: int,
    ) -> None:
        if not any(
            item.id == item_id
            for item in self.collect_provider_items(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=now_ms,
            )
        ):
            raise HTTPException(status_code=404, detail="Attention item not found")

    def today(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int | None,
        now_ms: int,
        force_degraded_provider: str | None = None,
    ) -> dict[str, Any]:
        preferences = load_attention_preferences(conn, user_id)
        user_states = load_user_attention_states(
            conn,
            garden_id=garden_id,
            user_id=user_id,
            now_ms=now_ms,
        )
        collected, degraded_providers = self._collect_provider_items_with_degradation(
            conn,
            garden_id=garden_id,
            user_id=user_id,
            now_ms=now_ms,
            force_degraded_provider=force_degraded_provider,
        )

        with_user_state = apply_user_states(collected, user_states, now_ms=now_ms)
        visible = apply_preferences(with_user_state, preferences, surface="panel")
        ranked = group_attention_items(rank_attention_items(visible))
        return serialize_today_response(
            garden_id=garden_id,
            generated_at_ms=now_ms,
            items=ranked,
            preferences=preferences,
            degraded_providers=degraded_providers,
        )
