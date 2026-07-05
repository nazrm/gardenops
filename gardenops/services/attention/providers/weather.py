from __future__ import annotations

import json
from typing import Any

from gardenops.services.attention.outcomes import read_active_attention_outcomes
from gardenops.services.attention.types import (
    AttentionAction,
    AttentionItem,
    AttentionProviderKey,
    attention_today_date,
    normalize_severity,
)

_OUTCOME_TYPES = ("watering_covered_by_rain", "watering_rescheduled_by_rain")


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


class WeatherAttentionProvider:
    key: AttentionProviderKey = "weather"

    def __init__(self, *, frozen_date: str | None = None) -> None:
        self.frozen_date = frozen_date

    def collect(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int,
        now_ms: int,
    ) -> list[AttentionItem]:
        today = attention_today_date(now_ms=now_ms, frozen_date=self.frozen_date)
        items = self._active_alert_items(
            conn,
            garden_id=garden_id,
            user_id=user_id,
            today=today,
        )
        items.extend(
            self._outcome_items(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=now_ms,
            )
        )
        return items

    def _active_alert_items(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int,
        today: str,
    ) -> list[AttentionItem]:
        rows = conn.execute(
            """
            SELECT id, garden_id, alert_type, severity, title, description,
                   valid_from, valid_until, metadata_json, created_at_ms
            FROM weather_alerts
            WHERE garden_id = %s
              AND dismissed = 0
              AND valid_from <= %s
              AND valid_until >= %s
            ORDER BY valid_from ASC, created_at_ms DESC, id ASC
            LIMIT 50
            """,
            (garden_id, today, today),
        ).fetchall()
        items: list[AttentionItem] = []
        for row in rows:
            alert_type = str(row["alert_type"])
            item_type = "rain_alert" if alert_type == "rain_surplus" else "weather_alert"
            items.append(
                AttentionItem(
                    id=f"attn:weather:alert:{row['id']}",
                    provider=self.key,
                    type=item_type,
                    category="warning",
                    severity=normalize_severity(row["severity"]),
                    title=str(row["title"] or ""),
                    body=str(row["description"] or ""),
                    reason=self._alert_reason(alert_type),
                    target_type="weather_alert",
                    target_id=str(row["id"]),
                    garden_id=int(row["garden_id"]),
                    audience_user_id=user_id,
                    valid_from=str(row["valid_from"]),
                    valid_until=str(row["valid_until"]),
                    source_label="Weather",
                    updated_at_ms=int(row["created_at_ms"] or 0),
                    metadata={
                        "alert_type": alert_type,
                        **_parse_mapping(row["metadata_json"]),
                    },
                )
            )
        return items

    def _outcome_items(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int,
        now_ms: int,
    ) -> list[AttentionItem]:
        rows = read_active_attention_outcomes(
            conn,
            garden_id=garden_id,
            provider=self.key,
            outcome_types=_OUTCOME_TYPES,
            now_ms=now_ms,
        )
        items: list[AttentionItem] = []
        for row in rows:
            recovery_action = dict(row["recovery_action"])
            secondary_actions: tuple[AttentionAction, ...] = ()
            if recovery_action:
                secondary_actions = (
                    AttentionAction(
                        kind="restore_attention_outcome",
                        label=str(recovery_action.get("label") or "Restore"),
                        target_type="attention_outcome",
                        target_id=str(row["public_id"]),
                        metadata=recovery_action,
                    ),
                )
            items.append(
                AttentionItem(
                    id=f"attn:outcome:{row['public_id']}",
                    provider=self.key,
                    type=str(row["outcome_type"]),
                    category="no_action_needed",
                    severity="low",
                    title=str(row["title"]),
                    body=str(row["explanation"]),
                    reason=str(row["reason"] or "No action needed"),
                    target_type=str(row["target_type"] or "") or None,
                    target_id=str(row["target_id"] or "") or None,
                    garden_id=garden_id,
                    audience_user_id=user_id,
                    plant_ids=tuple(row["plant_ids"]),
                    plot_ids=tuple(row["plot_ids"]),
                    domain_state="no_action_needed",
                    source_label="Weather",
                    updated_at_ms=int(row["updated_at_ms"] or row["occurred_at_ms"]),
                    secondary_actions=secondary_actions,
                    metadata={
                        **dict(row["metadata"]),
                        "recovery_action": recovery_action,
                    },
                )
            )
        return items

    @staticmethod
    def _alert_reason(alert_type: str) -> str:
        if alert_type == "rain_surplus":
            return "Rain affects watering"
        if alert_type == "dry_spell":
            return "Dry weather affects watering"
        if alert_type == "heat_wave":
            return "Heat affects watering"
        if alert_type == "frost_warning":
            return "Frost risk"
        return "Active weather alert"
