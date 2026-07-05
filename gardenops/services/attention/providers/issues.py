from __future__ import annotations

from typing import Any

from gardenops.services.attention.types import (
    AttentionAction,
    AttentionItem,
    AttentionProviderKey,
    attention_today_date,
    normalize_severity,
)

_DAY_MS = 86_400_000
_ISSUE_LIMIT = 80


class IssueAttentionProvider:
    key: AttentionProviderKey = "issue"

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
        recent_cutoff_ms = now_ms - _DAY_MS
        rows = self._collect_rows(
            conn,
            garden_id=garden_id,
            today=today,
            recent_cutoff_ms=recent_cutoff_ms,
        )
        issue_ids = [int(row["id"]) for row in rows]
        plant_ids_by_issue_id = self._plant_ids_by_issue_id(conn, issue_ids)
        plot_ids_by_issue_id = self._plot_ids_by_issue_id(conn, issue_ids)
        return [
            self._item_from_row(
                row,
                plant_ids=plant_ids_by_issue_id.get(int(row["id"]), ()),
                plot_ids=plot_ids_by_issue_id.get(int(row["id"]), ()),
                user_id=user_id,
                today=today,
            )
            for row in rows
        ]

    @staticmethod
    def _collect_rows(
        conn: Any,
        *,
        garden_id: int,
        today: str,
        recent_cutoff_ms: int,
    ) -> list[Any]:
        return conn.execute(
            """
            SELECT id, public_id, garden_id, issue_type, title, description, severity, status,
                   follow_up_on, resolved_at_ms, created_at_ms, updated_at_ms
            FROM garden_issues
            WHERE garden_id = %s
              AND (
                    (
                        status = 'open'
                        AND (
                            severity IN ('high', 'critical')
                            OR (follow_up_on IS NOT NULL AND follow_up_on <= %s)
                        )
                    )
                    OR (
                        status <> 'open'
                        AND resolved_at_ms IS NOT NULL
                        AND resolved_at_ms >= %s
                    )
              )
            ORDER BY
                CASE
                    WHEN status = 'open' AND follow_up_on IS NOT NULL AND follow_up_on < %s THEN 0
                    WHEN status = 'open' AND severity = 'critical' THEN 1
                    WHEN status = 'open' AND severity = 'high' THEN 2
                    WHEN status = 'open' AND follow_up_on = %s THEN 3
                    ELSE 4
                END ASC,
                follow_up_on ASC NULLS LAST,
                updated_at_ms DESC,
                public_id ASC
            LIMIT %s
            """,
            (garden_id, today, recent_cutoff_ms, today, today, _ISSUE_LIMIT),
        ).fetchall()

    @staticmethod
    def _plant_ids_by_issue_id(conn: Any, issue_ids: list[int]) -> dict[int, tuple[str, ...]]:
        if not issue_ids:
            return {}
        placeholders = ",".join(["%s"] * len(issue_ids))
        rows = conn.execute(
            f"""
            SELECT issue_id, plt_id
            FROM garden_issue_plants
            WHERE issue_id IN ({placeholders})
            ORDER BY plt_id
            """,
            issue_ids,
        ).fetchall()
        plant_ids: dict[int, list[str]] = {issue_id: [] for issue_id in issue_ids}
        for row in rows:
            plant_ids[int(row["issue_id"])].append(str(row["plt_id"]))
        return {issue_id: tuple(ids) for issue_id, ids in plant_ids.items()}

    @staticmethod
    def _plot_ids_by_issue_id(conn: Any, issue_ids: list[int]) -> dict[int, tuple[str, ...]]:
        if not issue_ids:
            return {}
        placeholders = ",".join(["%s"] * len(issue_ids))
        rows = conn.execute(
            f"""
            SELECT issue_id, plot_id
            FROM garden_issue_plots
            WHERE issue_id IN ({placeholders})
            ORDER BY plot_id
            """,
            issue_ids,
        ).fetchall()
        plot_ids: dict[int, list[str]] = {issue_id: [] for issue_id in issue_ids}
        for row in rows:
            plot_ids[int(row["issue_id"])].append(str(row["plot_id"]))
        return {issue_id: tuple(ids) for issue_id, ids in plot_ids.items()}

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
        status = str(row["status"])
        follow_up_on = str(row["follow_up_on"] or "") or None
        item_type = self._item_type(
            status=status,
            severity=str(row["severity"]),
            follow_up_on=follow_up_on,
            today=today,
        )
        active = status == "open"
        return AttentionItem(
            id=f"attn:issue:{public_id}",
            provider=self.key,
            type=item_type,
            category="needs_action" if active else "no_action_needed",
            severity=normalize_severity(row["severity"]) if active else "low",
            title=str(row["title"] or ""),
            body=str(row["description"] or ""),
            reason=self._reason(item_type, follow_up_on=follow_up_on),
            target_type="issue",
            target_id=public_id,
            garden_id=int(row["garden_id"]),
            audience_user_id=user_id,
            plant_ids=plant_ids,
            plot_ids=plot_ids,
            due_on=follow_up_on,
            domain_state="active" if active else "no_action_needed",
            delivery_eligibility=(("panel_only", "inbox", "digest") if active else ("panel_only",)),
            primary_action=(
                AttentionAction(
                    kind="open_issue",
                    label="Open issue",
                    target_type="issue",
                    target_id=public_id,
                )
                if active
                else None
            ),
            rank=self._rank(item_type),
            source_label="Issues",
            updated_at_ms=int(row["updated_at_ms"] or row["resolved_at_ms"] or 0),
            metadata={
                "status": status,
                "issue_type": str(row["issue_type"]),
                "resolved_at_ms": int(row["resolved_at_ms"] or 0) or None,
            },
        )

    @staticmethod
    def _item_type(
        *,
        status: str,
        severity: str,
        follow_up_on: str | None,
        today: str,
    ) -> str:
        if status != "open":
            return "issue_resolved"
        if follow_up_on and follow_up_on < today:
            return "issue_follow_up_overdue"
        if follow_up_on == today:
            return "issue_follow_up_due"
        return "issue_critical" if severity == "critical" else "issue_high_severity"

    @staticmethod
    def _reason(item_type: str, *, follow_up_on: str | None) -> str:
        if item_type == "issue_follow_up_overdue":
            return f"Follow-up overdue since {follow_up_on}"
        if item_type == "issue_follow_up_due":
            return "Follow-up due today"
        if item_type == "issue_critical":
            return "Critical issue open"
        if item_type == "issue_high_severity":
            return "High severity issue open"
        return "Resolved"

    @staticmethod
    def _rank(item_type: str) -> int:
        return {
            "issue_follow_up_overdue": 20,
            "issue_critical": 25,
            "issue_high_severity": 30,
            "issue_follow_up_due": 40,
            "issue_resolved": 600,
        }.get(item_type, 100)
