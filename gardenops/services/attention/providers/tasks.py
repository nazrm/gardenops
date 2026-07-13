from __future__ import annotations

import json
from typing import Any

from gardenops.services.attention.outcomes import read_active_attention_outcomes
from gardenops.services.attention.types import (
    AttentionAction,
    AttentionDomainState,
    AttentionItem,
    AttentionProviderKey,
    attention_today_date,
    is_generated_watering_task,
    normalize_severity,
)
from gardenops.services.generated_task_lifecycle import (
    GENERATED_WATERING_RULE_SOURCE_PATTERNS,
    stale_generated_watering_sql,
)

_DAY_MS = 86_400_000
_ACTIVE_BUCKET_LIMIT = 80
_SNOOZED_BUCKET_LIMIT = 40
_TERMINAL_BUCKET_LIMIT = 40


class TaskAttentionProvider:
    key: AttentionProviderKey = "task"

    def __init__(self, *, frozen_date: str | None = None) -> None:
        self.frozen_date = frozen_date

    def collect(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int,
        now_ms: int,
        suppress_rain_handled_watering: bool = True,
    ) -> list[AttentionItem]:
        today = attention_today_date(now_ms=now_ms, frozen_date=self.frozen_date)
        recent_cutoff_ms = now_ms - _DAY_MS
        rows = self._collect_rows(conn, garden_id, today=today, recent_cutoff_ms=recent_cutoff_ms)
        task_ids = [int(row["id"]) for row in rows]
        plot_ids_by_task_id = self._plot_ids_by_task_id(conn, task_ids)
        plant_ids_by_task_id = self._plant_ids_by_task_id(conn, task_ids)
        outdoor_plant_ids_by_task_id = self._outdoor_plant_ids_by_task_id(
            conn,
            garden_id,
            task_ids,
        )
        visible_rows = rows
        if suppress_rain_handled_watering:
            handled_watering_targets = self._handled_watering_targets(
                conn,
                garden_id,
                now_ms=now_ms,
            )
            visible_rows = [
                row
                for row in rows
                if not self._should_suppress_rain_handled_watering(
                    row,
                    outdoor_plant_ids_by_task_id=outdoor_plant_ids_by_task_id,
                    handled_watering_targets=handled_watering_targets,
                    today=today,
                )
            ]
        return [
            self._item_from_row(
                row,
                plot_ids=plot_ids_by_task_id.get(int(row["id"]), ()),
                plant_ids=plant_ids_by_task_id.get(int(row["id"]), ()),
                user_id=user_id,
                today=today,
            )
            for row in visible_rows
        ]

    @staticmethod
    def _collect_rows(
        conn: Any,
        garden_id: int,
        *,
        today: str,
        recent_cutoff_ms: int,
    ) -> list[Any]:
        severity_order = """
            CASE severity
                WHEN 'critical' THEN 3
                WHEN 'high' THEN 2
                WHEN 'normal' THEN 1
                ELSE 0
            END DESC
        """
        base_select = """
            SELECT id, public_id, garden_id, task_type, title, description, status, severity,
                   due_on, snoozed_until, rule_source, metadata_json, completed_at_ms,
                   updated_at_ms
            FROM garden_tasks
            WHERE garden_id = %s
              AND {condition}
            ORDER BY {order_by}
            LIMIT %s
        """
        stale_generated_pending_watering = stale_generated_watering_sql(
            task_alias="",
            action_on_sql="due_on",
            today_sql="%s",
        )
        stale_generated_snoozed_watering = stale_generated_watering_sql(
            task_alias="",
            action_on_sql="snoozed_until",
            today_sql="%s",
        )
        active_overdue = conn.execute(
            base_select.format(
                condition=(
                    f"status = 'pending' AND due_on < %s AND NOT {stale_generated_pending_watering}"
                ),
                order_by=f"{severity_order}, due_on ASC, updated_at_ms DESC, public_id ASC",
            ),
            (
                garden_id,
                today,
                *GENERATED_WATERING_RULE_SOURCE_PATTERNS,
                today,
                _ACTIVE_BUCKET_LIMIT,
            ),
        ).fetchall()
        active_due = conn.execute(
            base_select.format(
                condition="status = 'pending' AND due_on = %s",
                order_by=f"{severity_order}, updated_at_ms DESC, public_id ASC",
            ),
            (garden_id, today, _ACTIVE_BUCKET_LIMIT),
        ).fetchall()
        snoozed_ready = conn.execute(
            base_select.format(
                condition=(
                    "status = 'snoozed' "
                    "AND snoozed_until IS NOT NULL "
                    "AND snoozed_until <= %s "
                    f"AND NOT {stale_generated_snoozed_watering}"
                ),
                order_by=f"snoozed_until ASC, {severity_order}, updated_at_ms DESC, public_id ASC",
            ),
            (
                garden_id,
                today,
                *GENERATED_WATERING_RULE_SOURCE_PATTERNS,
                today,
                _SNOOZED_BUCKET_LIMIT,
            ),
        ).fetchall()
        terminal = conn.execute(
            base_select.format(
                condition=(
                    "("
                    "("
                    "status = 'completed' AND completed_at_ms IS NOT NULL "
                    "AND completed_at_ms >= %s"
                    ") OR (status IN ('skipped', 'expired') AND updated_at_ms >= %s)"
                    ")"
                ),
                order_by="updated_at_ms DESC, public_id ASC",
            ),
            (garden_id, recent_cutoff_ms, recent_cutoff_ms, _TERMINAL_BUCKET_LIMIT),
        ).fetchall()
        return [*active_overdue, *active_due, *snoozed_ready, *terminal]

    @staticmethod
    def _plot_ids_by_task_id(conn: Any, task_ids: list[int]) -> dict[int, tuple[str, ...]]:
        if not task_ids:
            return {}
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = conn.execute(
            f"""
            SELECT task_id, plot_id
            FROM garden_task_plots
            WHERE task_id IN ({placeholders})
            ORDER BY plot_id
            """,
            task_ids,
        ).fetchall()
        plot_ids: dict[int, list[str]] = {task_id: [] for task_id in task_ids}
        for row in rows:
            plot_ids[int(row["task_id"])].append(str(row["plot_id"]))
        return {task_id: tuple(ids) for task_id, ids in plot_ids.items()}

    @staticmethod
    def _plant_ids_by_task_id(conn: Any, task_ids: list[int]) -> dict[int, tuple[str, ...]]:
        if not task_ids:
            return {}
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = conn.execute(
            f"""
            SELECT task_id, plt_id
            FROM garden_task_plants
            WHERE task_id IN ({placeholders})
            ORDER BY plt_id
            """,
            task_ids,
        ).fetchall()
        plant_ids: dict[int, list[str]] = {task_id: [] for task_id in task_ids}
        for row in rows:
            plant_ids[int(row["task_id"])].append(str(row["plt_id"]))
        return {task_id: tuple(ids) for task_id, ids in plant_ids.items()}

    @staticmethod
    def _outdoor_plant_ids_by_task_id(
        conn: Any,
        garden_id: int,
        task_ids: list[int],
    ) -> dict[int, set[str]]:
        if not task_ids:
            return {}
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = conn.execute(
            f"""
            SELECT DISTINCT gtp.task_id, gtp.plt_id
            FROM garden_task_plants gtp
            JOIN plot_plants pp ON pp.plt_id = gtp.plt_id
            JOIN plots p ON p.plot_id = pp.plot_id
            WHERE gtp.task_id IN ({placeholders})
              AND p.garden_id = %s
              AND p.grid_row IS NOT NULL
            """,
            [*task_ids, garden_id],
        ).fetchall()
        outdoor_by_task_id: dict[int, set[str]] = {task_id: set() for task_id in task_ids}
        for row in rows:
            outdoor_by_task_id[int(row["task_id"])].add(str(row["plt_id"]))
        return outdoor_by_task_id

    @staticmethod
    def _handled_watering_targets(
        conn: Any,
        garden_id: int,
        *,
        now_ms: int,
    ) -> dict[str, dict[str, dict[str, set[str]]]]:
        outcomes = read_active_attention_outcomes(
            conn,
            garden_id=garden_id,
            provider="weather",
            outcome_types=("watering_covered_by_rain", "watering_rescheduled_by_rain"),
            target_type="plant",
            now_ms=now_ms,
        )
        handled: dict[str, dict[str, dict[str, set[str]]]] = {}
        for outcome in outcomes:
            if str(outcome["source_type"]) != "task_generator":
                continue
            rule_source = str(outcome["source_public_id"])
            target_id = str(outcome["target_id"])
            if not rule_source or not target_id:
                continue
            metadata = TaskAttentionProvider._parse_metadata(outcome["metadata"])
            covered_due_on = str(metadata.get("new_due_on") or metadata.get("due_on") or "")
            if not covered_due_on:
                continue
            outcome_type = str(outcome["outcome_type"])
            handled.setdefault(rule_source, {}).setdefault(target_id, {}).setdefault(
                outcome_type,
                set(),
            ).add(covered_due_on)
        return handled

    @staticmethod
    def _plant_id_from_generated_water_rule(rule_source: str) -> str:
        parts = rule_source.split(":")
        if len(parts) >= 3 and parts[0] == "water":
            return parts[1]
        if len(parts) >= 4 and parts[:2] == ["auto", "dry_water"]:
            return parts[-1]
        return ""

    @staticmethod
    def _should_suppress_rain_handled_watering(
        row: Any,
        *,
        outdoor_plant_ids_by_task_id: dict[int, set[str]],
        handled_watering_targets: dict[str, dict[str, dict[str, set[str]]]],
        today: str,
    ) -> bool:
        if str(row["status"]) not in {"pending", "snoozed"}:
            return False
        rule_source = str(row["rule_source"] or "")
        if not is_generated_watering_task(str(row["task_type"]), rule_source):
            return False
        target_id = TaskAttentionProvider._plant_id_from_generated_water_rule(rule_source)
        if not target_id:
            return False
        if target_id not in outdoor_plant_ids_by_task_id.get(int(row["id"]), set()):
            return False
        handled_dates = handled_watering_targets.get(rule_source, {}).get(target_id, {})
        if not handled_dates:
            return False
        actionable_on = str(row["snoozed_until"] or row["due_on"])
        if actionable_on in handled_dates.get("watering_covered_by_rain", set()):
            return True
        # Rescheduled watering becomes actionable when its new date arrives.
        return actionable_on > today and actionable_on in handled_dates.get(
            "watering_rescheduled_by_rain", set()
        )

    def _item_from_row(
        self,
        row: Any,
        *,
        plot_ids: tuple[str, ...],
        plant_ids: tuple[str, ...],
        user_id: int,
        today: str,
    ) -> AttentionItem:
        status = str(row["status"])
        public_id = str(row["public_id"])
        due_on = str(row["snoozed_until"] or row["due_on"])
        item_type = self._item_type(status=status, due_on=due_on, today=today)
        active = status in {"pending", "snoozed"}
        metadata = self._parse_metadata(row["metadata_json"])
        group_key = str(metadata.get("group_key") or "").strip()
        return AttentionItem(
            id=f"attn:task:{public_id}",
            provider=self.key,
            type=item_type,
            category="needs_action" if active else "no_action_needed",
            severity=normalize_severity(row["severity"]),
            title=str(row["title"] or ""),
            body=str(row["description"] or ""),
            reason=self._reason(item_type, due_on=due_on, today=today),
            target_type="task",
            target_id=public_id,
            garden_id=int(row["garden_id"]),
            audience_user_id=user_id,
            plant_ids=plant_ids,
            plot_ids=plot_ids,
            due_on=due_on,
            domain_state=self._domain_state(status),
            delivery_eligibility=(("panel_only", "inbox", "digest") if active else ("panel_only",)),
            group_key=group_key or None,
            primary_action=(
                AttentionAction(
                    kind="open_task",
                    label="Open task",
                    target_type="task",
                    target_id=public_id,
                )
                if active
                else None
            ),
            source_label="Tasks",
            updated_at_ms=int(row["updated_at_ms"] or 0),
            metadata={
                "status": status,
                "task_type": str(row["task_type"]),
                "rule_source": str(row["rule_source"] or ""),
                **({"group_key": group_key} if group_key else {}),
            },
        )

    @staticmethod
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

    @staticmethod
    def _item_type(*, status: str, due_on: str, today: str) -> str:
        if status == "pending":
            return "task_overdue" if due_on < today else "task_due"
        if status == "snoozed":
            return "task_snoozed_active"
        if status == "completed":
            return "task_completed"
        if status == "expired":
            return "task_expired"
        return "task_skipped"

    @staticmethod
    def _reason(item_type: str, *, due_on: str, today: str) -> str:
        if item_type == "task_snoozed_active" and due_on < today:
            return "Snooze expired"
        return {
            "task_due": "Due today",
            "task_overdue": "Overdue",
            "task_snoozed_active": "Snoozed until today",
            "task_completed": "Completed",
            "task_expired": "Expired",
            "task_skipped": "Skipped",
        }[item_type]

    @staticmethod
    def _domain_state(status: str) -> AttentionDomainState:
        if status == "completed":
            return "completed"
        if status == "expired":
            return "expired"
        if status == "skipped":
            return "skipped"
        return "active"
