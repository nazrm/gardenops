from __future__ import annotations

import json
from typing import Any, cast

from gardenops.services.attention.types import (
    AttentionItem,
    AttentionProviderKey,
    AttentionUserState,
    normalize_severity,
)

_STATUS_TYPES = ("system", "status", "security", "backup")
_NOTIFICATION_LIMIT = 80


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


class NotificationStatusAttentionProvider:
    key: AttentionProviderKey = "notification_status"

    def collect(
        self,
        conn: Any,
        *,
        garden_id: int,
        user_id: int,
        now_ms: int,
    ) -> list[AttentionItem]:
        rows = conn.execute(
            """
            SELECT id, public_id, garden_id, user_id, notification_type,
                   notification_subtype, severity, title, body, target_type, target_id,
                   read_at_ms, metadata_json, created_at_ms, expires_at_ms
            FROM notification_events
            WHERE garden_id = %s
              AND (user_id IS NULL OR user_id = %s)
              AND dismissed = 0
              AND cleared_at_ms IS NULL
              AND (expires_at_ms IS NULL OR expires_at_ms > %s)
              AND (
                    notification_type IN ('system', 'status', 'security', 'backup')
                    OR notification_subtype IN ('system', 'status', 'security', 'backup')
                    OR target_type = 'status'
              )
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 3
                    WHEN 'high' THEN 2
                    WHEN 'normal' THEN 1
                    ELSE 0
                END DESC,
                created_at_ms DESC,
                public_id ASC
            LIMIT %s
            """,
            (garden_id, user_id, now_ms, _NOTIFICATION_LIMIT),
        ).fetchall()
        return [self._item_from_row(row, fallback_user_id=user_id) for row in rows]

    def _item_from_row(self, row: Any, *, fallback_user_id: int) -> AttentionItem:
        public_id = str(row["public_id"])
        metadata = _parse_mapping(row["metadata_json"])
        notification_type = str(row["notification_type"] or "")
        notification_subtype = str(row["notification_subtype"] or "")
        item_type = notification_subtype or notification_type or "status"
        target_type = str(row["target_type"] or metadata.get("target_type") or "") or None
        target_id = str(row["target_id"] or metadata.get("target_id") or "") or None
        row_user_id = row["user_id"]
        return AttentionItem(
            id=f"attn:notification:{public_id}",
            provider=self.key,
            type=item_type,
            category="system",
            severity=normalize_severity(row["severity"]),
            title=str(row["title"] or ""),
            body=str(row["body"] or ""),
            reason=self._reason(notification_type, notification_subtype),
            target_type=target_type,
            target_id=target_id,
            garden_id=int(row["garden_id"]),
            audience_user_id=int(row_user_id) if row_user_id is not None else fallback_user_id,
            user_state=cast(
                AttentionUserState,
                "read" if row["read_at_ms"] is not None else "unread",
            ),
            source_label="Notifications",
            updated_at_ms=int(row["created_at_ms"] or 0),
            metadata={
                **metadata,
                "notification_type": notification_type,
                "notification_subtype": notification_subtype,
                "expires_at_ms": int(row["expires_at_ms"] or 0) or None,
            },
        )

    @staticmethod
    def _reason(notification_type: str, notification_subtype: str) -> str:
        if notification_subtype:
            return f"{notification_subtype.replace('_', ' ').title()} status"
        if notification_type:
            return f"{notification_type.replace('_', ' ').title()} status"
        return "System status"
