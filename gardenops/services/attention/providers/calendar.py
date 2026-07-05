from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from gardenops.services.attention.types import (
    AttentionItem,
    AttentionProviderKey,
    attention_today_date,
)

_CALENDAR_LIMIT = 80


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


class CalendarAttentionProvider:
    key: AttentionProviderKey = "calendar"

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
        start = date.fromisoformat(today)
        end = start + timedelta(days=7)
        rows = self._collect_rows(conn, garden_id=garden_id, start=start, end=end)
        event_ids = [int(row["id"]) for row in rows]
        plant_ids_by_event_id = self._plant_ids_by_event_id(conn, event_ids)
        plot_ids_by_event_id = self._plot_ids_by_event_id(conn, event_ids)
        items: list[AttentionItem] = []
        for row in rows:
            if self._is_duplicate(row):
                continue
            items.append(
                self._item_from_row(
                    row,
                    plant_ids=plant_ids_by_event_id.get(int(row["id"]), ()),
                    plot_ids=plot_ids_by_event_id.get(int(row["id"]), ()),
                    user_id=user_id,
                    today=today,
                )
            )
        return items

    @staticmethod
    def _collect_rows(
        conn: Any,
        *,
        garden_id: int,
        start: date,
        end: date,
    ) -> list[Any]:
        return conn.execute(
            """
            SELECT id, public_id, garden_id, title, description, event_on,
                   created_at_ms, updated_at_ms
            FROM garden_calendar_events
            WHERE garden_id = %s
              AND event_on >= %s
              AND event_on <= %s
            ORDER BY event_on ASC, updated_at_ms DESC, public_id ASC
            LIMIT %s
            """,
            (garden_id, start.isoformat(), end.isoformat(), _CALENDAR_LIMIT),
        ).fetchall()

    @staticmethod
    def _plant_ids_by_event_id(conn: Any, event_ids: list[int]) -> dict[int, tuple[str, ...]]:
        if not event_ids:
            return {}
        placeholders = ",".join(["%s"] * len(event_ids))
        rows = conn.execute(
            f"""
            SELECT event_id, plt_id
            FROM garden_calendar_event_plants
            WHERE event_id IN ({placeholders})
            ORDER BY plt_id
            """,
            event_ids,
        ).fetchall()
        plant_ids: dict[int, list[str]] = {event_id: [] for event_id in event_ids}
        for row in rows:
            plant_ids[int(row["event_id"])].append(str(row["plt_id"]))
        return {event_id: tuple(ids) for event_id, ids in plant_ids.items()}

    @staticmethod
    def _plot_ids_by_event_id(conn: Any, event_ids: list[int]) -> dict[int, tuple[str, ...]]:
        if not event_ids:
            return {}
        placeholders = ",".join(["%s"] * len(event_ids))
        rows = conn.execute(
            f"""
            SELECT event_id, plot_id
            FROM garden_calendar_event_plots
            WHERE event_id IN ({placeholders})
            ORDER BY plot_id
            """,
            event_ids,
        ).fetchall()
        plot_ids: dict[int, list[str]] = {event_id: [] for event_id in event_ids}
        for row in rows:
            plot_ids[int(row["event_id"])].append(str(row["plot_id"]))
        return {event_id: tuple(ids) for event_id, ids in plot_ids.items()}

    @staticmethod
    def _metadata_has_duplicate_target(metadata: dict[str, Any]) -> bool:
        target_type = str(metadata.get("target_type") or metadata.get("source_type") or "")
        target_id = str(
            metadata.get("target_id")
            or metadata.get("source_id")
            or metadata.get("task_id")
            or metadata.get("task_public_id")
            or metadata.get("issue_id")
            or metadata.get("issue_public_id")
            or metadata.get("weather_alert_id")
            or ""
        )
        return bool(target_id and target_type in {"task", "issue", "weather", "weather_alert"})

    @staticmethod
    def _is_duplicate(row: Any) -> bool:
        metadata = _parse_mapping(row.get("metadata_json") if hasattr(row, "get") else None)
        target_type = str(row.get("target_type") or "") if hasattr(row, "get") else ""
        target_id = str(row.get("target_id") or "") if hasattr(row, "get") else ""
        source_key = str(row.get("source_key") or "") if hasattr(row, "get") else ""
        if target_id and target_type in {"task", "issue", "weather", "weather_alert"}:
            return True
        if source_key in {"task", "issue", "weather_alert"}:
            return True
        return CalendarAttentionProvider._metadata_has_duplicate_target(metadata)

    def _item_from_row(
        self,
        row: Any,
        *,
        plant_ids: tuple[str, ...],
        plot_ids: tuple[str, ...],
        user_id: int,
        today: str,
    ) -> AttentionItem:
        public_id = str(row["public_id"])
        event_on = str(row["event_on"])
        due_today = event_on == today
        return AttentionItem(
            id=f"attn:calendar:{public_id}",
            provider=self.key,
            type="calendar_event_due" if due_today else "calendar_event_upcoming",
            category="needs_action" if due_today else "upcoming",
            severity="high",
            title=str(row["title"] or ""),
            body=str(row["description"] or ""),
            reason="Due today" if due_today else "Upcoming",
            target_type="manual_event",
            target_id=public_id,
            garden_id=int(row["garden_id"]),
            audience_user_id=user_id,
            plant_ids=plant_ids,
            plot_ids=plot_ids,
            due_on=event_on,
            delivery_eligibility=("panel_only", "inbox", "digest"),
            rank=350 if due_today else 450,
            source_label="Calendar",
            updated_at_ms=int(row["updated_at_ms"] or row["created_at_ms"] or 0),
            metadata={"event_on": event_on},
        )
