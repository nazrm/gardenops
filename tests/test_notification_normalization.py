import json
import os
from unittest.mock import patch

import gardenops.db as db
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password

_FROZEN_NOW_MS = 1783180800000
_FROZEN_DATE = "2026-07-05"


class TestNotificationNormalization(BaseApiTest):
    def _create_pro_member(self, conn, username: str) -> tuple[int, str]:
        password = f"{username}-password"
        user = create_user(
            conn,
            username=username,
            password=strong_password(password),
            role="editor",
        )
        user_id = int(user["id"])
        conn.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (user_id,),
        )
        return user_id, password

    @staticmethod
    def _save_legacy_preferences(
        conn,
        *,
        user_id: int,
        now_ms: int,
        in_app_enabled: bool = True,
        email_address: str = "",
        rules: dict[str, dict[str, object]] | None = None,
        quiet_hours: dict[str, object] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO user_notification_preferences
                (user_id, in_app_enabled, email_enabled, email_address,
                 digest_frequency, quiet_hours_json, task_due_enabled,
                 task_overdue_enabled, rules_json, created_at_ms, updated_at_ms)
            VALUES (%s, %s, %s, %s, 'daily', %s, 1, 1, %s, %s, %s)
            ON CONFLICT(user_id) DO UPDATE SET
                in_app_enabled = excluded.in_app_enabled,
                email_enabled = excluded.email_enabled,
                email_address = excluded.email_address,
                digest_frequency = excluded.digest_frequency,
                quiet_hours_json = excluded.quiet_hours_json,
                rules_json = excluded.rules_json,
                updated_at_ms = excluded.updated_at_ms
            """,
            (
                user_id,
                int(in_app_enabled),
                int(bool(email_address)),
                email_address,
                json.dumps(quiet_hours or {}, separators=(",", ":")),
                json.dumps(rules or {}, separators=(",", ":")),
                now_ms,
                now_ms,
            ),
        )

    @staticmethod
    def _save_attention_preferences(
        conn,
        *,
        user_id: int,
        now_ms: int,
        rules: dict[str, dict[str, object]],
        quiet_hours: dict[str, object] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO user_attention_preferences
                (user_id, preset, rules_json, quiet_hours_json,
                 show_no_action_history, metadata_json, created_at_ms, updated_at_ms)
            VALUES (%s, 'custom', %s, %s, 1, '{}', %s, %s)
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
                json.dumps(rules, separators=(",", ":")),
                json.dumps(quiet_hours or {}, separators=(",", ":")),
                now_ms,
                now_ms,
            ),
        )

    def test_normalized_rules_control_generation_inbox_badge_and_digest(self) -> None:
        from gardenops.services.notification_service import (
            create_notification,
            create_task_due_notifications,
            deliver_pending_email_digests,
        )

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(_FROZEN_NOW_MS),
                "GARDENOPS_ATTENTION_FROZEN_DATE": _FROZEN_DATE,
                "GARDENOPS_SMTP_HOST": "",
                "GARDENOPS_SMTP_FROM": "",
            },
            clear=False,
        ):
            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
                ).fetchone()
                assert garden is not None
                garden_id = int(garden["id"])
                user_id, password = self._create_pro_member(conn, "normalized_delivery_user")
                fallback_user_id, _ = self._create_pro_member(conn, "legacy_fallback_user")
                legacy_rules = {
                    "task_due": {
                        "in_app_enabled": False,
                        "email_enabled": False,
                        "min_severity": "critical",
                    },
                    "task_overdue": {
                        "in_app_enabled": True,
                        "email_enabled": True,
                        "min_severity": "low",
                    },
                }
                self._save_legacy_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    email_address="normalized-delivery@example.test",
                    rules=legacy_rules,
                )
                self._save_attention_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "task_due": {
                            "panel": True,
                            "inbox": True,
                            "digest": True,
                            "min_severity": "low",
                        },
                        "task_overdue": {
                            "panel": True,
                            "inbox": False,
                            "digest": False,
                            "min_severity": "low",
                        },
                    },
                )
                self._save_legacy_preferences(
                    conn,
                    user_id=fallback_user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "task_due": {
                            "in_app_enabled": False,
                            "email_enabled": False,
                            "min_severity": "low",
                        },
                        "task_overdue": {
                            "in_app_enabled": False,
                            "email_enabled": False,
                            "min_severity": "low",
                        },
                    },
                )
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (public_id, garden_id, task_type, title, status, severity,
                         due_on, metadata_json, created_at_ms, updated_at_ms)
                    VALUES
                        ('task_normalized_due', %s, 'water', 'Eligible task', 'pending',
                         'normal', %s, '{}', %s, %s),
                        ('task_normalized_overdue', %s, 'water', 'Ineligible task', 'pending',
                         'normal', '2026-07-04', '{}', %s, %s)
                    """,
                    (
                        garden_id,
                        _FROZEN_DATE,
                        _FROZEN_NOW_MS,
                        _FROZEN_NOW_MS,
                        garden_id,
                        _FROZEN_NOW_MS,
                        _FROZEN_NOW_MS,
                    ),
                )
                conn.commit()

                generated = create_task_due_notifications(conn, garden_id, now_ms=1)
                self.assertGreaterEqual(int(generated["created"]), 1)
                stored_ineligible_id = create_notification(
                    conn,
                    garden_id,
                    user_id,
                    "task_overdue",
                    "Stored ineligible task",
                    "This remains in the durable log only.",
                    target_type="manual",
                    target_id="stored-ineligible",
                    now_ms=1,
                )

                generated_rows = conn.execute(
                    """
                    SELECT notification_type, target_id
                    FROM notification_events
                    WHERE garden_id = %s AND user_id = %s
                    ORDER BY id
                    """,
                    (garden_id, user_id),
                ).fetchall()
                generated_pairs = {
                    (str(row["notification_type"]), str(row["target_id"])) for row in generated_rows
                }
                self.assertIn(("task_due", "task_normalized_due"), generated_pairs)
                self.assertNotIn(("task_overdue", "task_normalized_overdue"), generated_pairs)
                fallback_count = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM notification_events
                    WHERE garden_id = %s
                      AND user_id = %s
                      AND target_id IN ('task_normalized_due', 'task_normalized_overdue')
                    """,
                    (garden_id, fallback_user_id),
                ).fetchone()
                assert fallback_count is not None
                self.assertEqual(int(fallback_count["count"]), 0)

                client, headers = self._authenticated_client(
                    "normalized_delivery_user",
                    password,
                    garden_id=garden_id,
                )
                inbox = client.get("/api/notifications", headers=headers)
                self.assertEqual(inbox.status_code, 200, inbox.text)
                self.assertEqual(
                    [row["target_id"] for row in inbox.json()["notifications"]],
                    ["task_normalized_due"],
                )
                badge = client.get("/api/notifications/count", headers=headers)
                self.assertEqual(badge.status_code, 200, badge.text)
                self.assertEqual(badge.json()["count"], 1)

                sent: list[tuple[str, str, str]] = []
                digest = deliver_pending_email_digests(
                    conn,
                    garden_id,
                    email_sender=lambda recipient, subject, body: sent.append(
                        (recipient, subject, body)
                    ),
                    now_ms=1,
                )
                self.assertEqual(int(digest["emailed_users"]), 1)
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0][0], "normalized-delivery@example.test")
                self.assertIn("Eligible task", sent[0][2])
                self.assertNotIn("Stored ineligible task", sent[0][2])
                stored = conn.execute(
                    "SELECT emailed_at_ms FROM notification_events WHERE public_id = %s",
                    (stored_ineligible_id,),
                ).fetchone()
                assert stored is not None
                self.assertIsNone(stored["emailed_at_ms"])
            finally:
                db.return_db(conn)

    def test_same_garden_user_actions_and_preferences_stay_user_scoped(self) -> None:
        from gardenops.services.notification_service import (
            create_notification,
            deliver_pending_email_digests,
        )

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(_FROZEN_NOW_MS),
                "GARDENOPS_ATTENTION_FROZEN_DATE": _FROZEN_DATE,
                "GARDENOPS_SMTP_HOST": "",
                "GARDENOPS_SMTP_FROM": "",
            },
            clear=False,
        ):
            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
                ).fetchone()
                assert garden is not None
                garden_id = int(garden["id"])
                muted_user_id, muted_password = self._create_pro_member(conn, "muted_same_garden")
                visible_user_id, visible_password = self._create_pro_member(
                    conn,
                    "visible_same_garden",
                )
                for user_id, email_address in (
                    (muted_user_id, "muted-same-garden@example.test"),
                    (visible_user_id, "visible-same-garden@example.test"),
                ):
                    self._save_legacy_preferences(
                        conn,
                        user_id=user_id,
                        now_ms=_FROZEN_NOW_MS,
                        email_address=email_address,
                    )
                self._save_attention_preferences(
                    conn,
                    user_id=muted_user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "task_due": {
                            "panel": True,
                            "inbox": False,
                            "digest": False,
                            "min_severity": "low",
                        }
                    },
                )
                self._save_attention_preferences(
                    conn,
                    user_id=visible_user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "task_due": {
                            "panel": True,
                            "inbox": True,
                            "digest": True,
                            "min_severity": "low",
                        }
                    },
                )
                conn.commit()
                muted_note = create_notification(
                    conn,
                    garden_id,
                    muted_user_id,
                    "task_due",
                    "Muted user task",
                    "Muted user reminder",
                    target_type="manual",
                    target_id="muted-user-task",
                    now_ms=1,
                )
                visible_note = create_notification(
                    conn,
                    garden_id,
                    visible_user_id,
                    "task_due",
                    "Visible user task",
                    "Visible user reminder",
                    target_type="manual",
                    target_id="visible-user-task",
                    now_ms=1,
                )

                muted_client, muted_headers = self._authenticated_client(
                    "muted_same_garden",
                    muted_password,
                    garden_id=garden_id,
                )
                visible_client, visible_headers = self._authenticated_client(
                    "visible_same_garden",
                    visible_password,
                    garden_id=garden_id,
                )
                muted_inbox = muted_client.get("/api/notifications", headers=muted_headers)
                visible_inbox = visible_client.get("/api/notifications", headers=visible_headers)
                self.assertEqual(muted_inbox.status_code, 200, muted_inbox.text)
                self.assertEqual(visible_inbox.status_code, 200, visible_inbox.text)
                self.assertEqual(muted_inbox.json()["notifications"], [])
                self.assertEqual(
                    [row["id"] for row in visible_inbox.json()["notifications"]],
                    [visible_note],
                )
                muted_count = muted_client.get(
                    "/api/notifications/count",
                    headers=muted_headers,
                )
                self.assertEqual(muted_count.json()["count"], 0)
                self.assertEqual(
                    visible_client.get("/api/notifications/count", headers=visible_headers).json()[
                        "count"
                    ],
                    1,
                )

                sent: list[tuple[str, str, str]] = []
                digest = deliver_pending_email_digests(
                    conn,
                    garden_id,
                    email_sender=lambda recipient, subject, body: sent.append(
                        (recipient, subject, body)
                    ),
                    now_ms=1,
                )
                self.assertEqual(int(digest["emailed_users"]), 1)
                self.assertEqual(
                    [message[0] for message in sent],
                    ["visible-same-garden@example.test"],
                )

                dismissed = muted_client.delete(
                    f"/api/notifications/{muted_note}",
                    headers=muted_headers,
                )
                self.assertEqual(dismissed.status_code, 200, dismissed.text)
                states = conn.execute(
                    """
                    SELECT public_id, dismissed, emailed_at_ms
                    FROM notification_events
                    WHERE public_id IN (%s, %s)
                    ORDER BY public_id
                    """,
                    (muted_note, visible_note),
                ).fetchall()
                by_id = {str(row["public_id"]): row for row in states}
                self.assertTrue(bool(by_id[muted_note]["dismissed"]))
                self.assertFalse(bool(by_id[visible_note]["dismissed"]))
                self.assertIsNone(by_id[muted_note]["emailed_at_ms"])
                self.assertEqual(int(by_id[visible_note]["emailed_at_ms"]), _FROZEN_NOW_MS)
            finally:
                db.return_db(conn)

    def test_maintenance_uses_frozen_clock_for_creation_and_injected_delivery(self) -> None:
        from gardenops.services.notification_service import run_notification_maintenance_for_garden

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(_FROZEN_NOW_MS),
                "GARDENOPS_ATTENTION_FROZEN_DATE": _FROZEN_DATE,
                "GARDENOPS_SMTP_HOST": "",
                "GARDENOPS_SMTP_FROM": "",
            },
            clear=False,
        ):
            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
                ).fetchone()
                assert garden is not None
                garden_id = int(garden["id"])
                user_id, _ = self._create_pro_member(conn, "frozen_maintenance_user")
                self._save_legacy_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    email_address="frozen-maintenance@example.test",
                )
                self._save_attention_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "task_due": {
                            "panel": True,
                            "inbox": True,
                            "digest": True,
                            "min_severity": "low",
                        }
                    },
                )
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (public_id, garden_id, task_type, title, status, severity,
                         due_on, metadata_json, created_at_ms, updated_at_ms)
                    VALUES ('task_frozen_maintenance', %s, 'water', 'Frozen maintenance task',
                            'pending', 'normal', %s, '{}', %s, %s)
                    """,
                    (garden_id, _FROZEN_DATE, _FROZEN_NOW_MS, _FROZEN_NOW_MS),
                )
                conn.commit()

                sent: list[tuple[str, str, str]] = []
                result = run_notification_maintenance_for_garden(
                    conn,
                    garden_id=garden_id,
                    email_sender=lambda recipient, subject, body: sent.append(
                        (recipient, subject, body)
                    ),
                    now_ms=1,
                )
                self.assertEqual(int(result["emailed_users"]), 1)
                self.assertEqual(
                    [message[0] for message in sent],
                    ["frozen-maintenance@example.test"],
                )
                notification = conn.execute(
                    """
                    SELECT created_at_ms, emailed_at_ms
                    FROM notification_events
                    WHERE garden_id = %s
                      AND user_id = %s
                      AND target_id = 'task_frozen_maintenance'
                    """,
                    (garden_id, user_id),
                ).fetchone()
                assert notification is not None
                self.assertEqual(int(notification["created_at_ms"]), _FROZEN_NOW_MS)
                self.assertEqual(int(notification["emailed_at_ms"]), _FROZEN_NOW_MS)
                delivery = conn.execute(
                    """
                    SELECT last_email_digest_at_ms
                    FROM user_notification_preferences
                    WHERE user_id = %s
                    """,
                    (user_id,),
                ).fetchone()
                assert delivery is not None
                self.assertEqual(int(delivery["last_email_digest_at_ms"]), _FROZEN_NOW_MS)
            finally:
                db.return_db(conn)

    def test_notification_and_attention_settings_share_one_effective_rule_set(self) -> None:
        from gardenops.services.notification_service import (
            create_notification,
            notification_rows_allowed_for_user,
        )

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(_FROZEN_NOW_MS),
                "GARDENOPS_ATTENTION_FROZEN_DATE": _FROZEN_DATE,
            },
            clear=False,
        ):
            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
                ).fetchone()
                assert garden is not None
                garden_id = int(garden["id"])
                user_id, password = self._create_pro_member(conn, "normalized_settings_user")
                self._save_legacy_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                )
                self._save_attention_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    quiet_hours={
                        "start": "20:00",
                        "end": "05:00",
                        "interruptive": {
                            "enabled": True,
                            "start": "23:00",
                            "end": "06:00",
                        },
                    },
                    rules={
                        "issue_follow_up_due": {
                            "panel": True,
                            "inbox": True,
                            "digest": True,
                            "min_severity": "low",
                        },
                        "issue_follow_up_overdue": {
                            "panel": True,
                            "inbox": True,
                            "digest": True,
                            "min_severity": "low",
                        },
                    },
                )
                notification_id = create_notification(
                    conn,
                    garden_id,
                    user_id,
                    "issue_created",
                    "Review aphid follow-up",
                    "Check whether the intervention worked.",
                    target_type="manual",
                    target_id="issue-settings-bridge",
                    now_ms=_FROZEN_NOW_MS,
                )
                conn.commit()
                stored_row = conn.execute(
                    "SELECT * FROM notification_events WHERE public_id = %s",
                    (notification_id,),
                ).fetchone()
                assert stored_row is not None
                directly_allowed = notification_rows_allowed_for_user(
                    conn,
                    [dict(stored_row)],
                    surface="inbox",
                    garden_id=garden_id,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                )
                self.assertEqual(
                    [str(row["public_id"]) for row in directly_allowed],
                    [notification_id],
                )
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                "normalized_settings_user",
                password,
                garden_id=garden_id,
            )
            stored = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(stored.status_code, 200, stored.text)
            self.assertIn(
                notification_id,
                [row["id"] for row in stored.json()["notifications"]],
            )
            initial_attention = client.get("/api/attention/preferences", headers=headers)
            self.assertEqual(initial_attention.status_code, 200, initial_attention.text)
            self.assertTrue(initial_attention.json()["rules"]["issue_follow_up_due"]["inbox"])
            before = client.get("/api/notifications", headers=headers)
            self.assertEqual(before.status_code, 200, before.text)
            before_ids = [row["id"] for row in before.json()["notifications"]]
            self.assertEqual(before_ids, [notification_id])

            current = client.get("/api/notifications/preferences", headers=headers)
            self.assertEqual(current.status_code, 200, current.text)
            update = current.json()
            update.pop("policy", None)
            update["quiet_hours_json"] = {"start": "21:30", "end": "06:15"}
            update["notification_rules"]["issue_created"] = {
                "in_app_enabled": False,
                "email_enabled": False,
                "min_severity": "high",
            }
            muted = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json=update,
            )
            self.assertEqual(muted.status_code, 200, muted.text)

            muted_inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(muted_inbox.status_code, 200, muted_inbox.text)
            self.assertEqual(muted_inbox.json()["notifications"], [])
            self.assertEqual(
                client.get("/api/notifications/count", headers=headers).json()["count"],
                0,
            )
            attention = client.get("/api/attention/preferences", headers=headers)
            self.assertEqual(attention.status_code, 200, attention.text)
            attention_body = attention.json()
            for key in ("issue_follow_up_due", "issue_follow_up_overdue"):
                self.assertTrue(attention_body["rules"][key]["panel"])
                self.assertFalse(attention_body["rules"][key]["inbox"])
                self.assertFalse(attention_body["rules"][key]["digest"])
                self.assertEqual(attention_body["rules"][key]["min_severity"], "high")
            self.assertEqual(
                attention_body["quiet_hours"]["digest"],
                {"enabled": True, "start": "21:30", "end": "06:15"},
            )
            self.assertNotIn("start", attention_body["quiet_hours"])
            self.assertNotIn("end", attention_body["quiet_hours"])
            self.assertEqual(
                attention_body["quiet_hours"]["interruptive"],
                {"enabled": True, "start": "23:00", "end": "06:00"},
            )

            attention_body["rules"]["issue_follow_up_due"]["inbox"] = True
            attention_body["rules"]["issue_follow_up_overdue"]["inbox"] = True
            attention_body["rules"]["issue_follow_up_due"]["min_severity"] = "low"
            attention_body["rules"]["issue_follow_up_overdue"]["min_severity"] = "low"
            attention_body.pop("user_id", None)
            unmuted = client.put(
                "/api/attention/preferences",
                headers=headers,
                json=attention_body,
            )
            self.assertEqual(unmuted.status_code, 200, unmuted.text)

            projected = client.get("/api/notifications/preferences", headers=headers)
            self.assertEqual(projected.status_code, 200, projected.text)
            self.assertTrue(
                projected.json()["notification_rules"]["issue_created"]["in_app_enabled"]
            )
            restored_inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(restored_inbox.status_code, 200, restored_inbox.text)
            self.assertEqual(
                [row["id"] for row in restored_inbox.json()["notifications"]],
                [notification_id],
            )
