import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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

    def test_saved_attention_quiet_hours_clear_legacy_notification_window(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
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
                user_id, password = self._create_pro_member(
                    conn,
                    "canonical_quiet_hours_user",
                )
                self._save_legacy_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    quiet_hours={
                        "start": "21:30",
                        "end": "06:15",
                        "timezone": "Europe/Oslo",
                    },
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
                    quiet_hours={},
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                "canonical_quiet_hours_user",
                password,
                garden_id=garden_id,
            )
            notification_preferences = client.get(
                "/api/notifications/preferences",
                headers=headers,
            )
            self.assertEqual(
                notification_preferences.status_code,
                200,
                notification_preferences.text,
            )
            attention_preferences = client.get(
                "/api/attention/preferences",
                headers=headers,
            )
            self.assertEqual(
                attention_preferences.status_code,
                200,
                attention_preferences.text,
            )

        self.assertEqual(notification_preferences.json()["quiet_hours_json"], {})
        self.assertEqual(attention_preferences.json()["quiet_hours"], {})

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
            attention_body.pop("digest_delivery", None)
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

    def test_notification_sync_preserves_concurrent_attention_preference_update(self) -> None:
        import gardenops.routers.attention as attention_router
        import gardenops.routers.notifications as notification_router

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
                user_id, password = self._create_pro_member(
                    conn,
                    "concurrent_preference_sync",
                )
                self._save_legacy_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                )
                self._save_attention_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "task_due": {
                            "panel": True,
                            "inbox": True,
                            "digest": False,
                            "min_severity": "low",
                        }
                    },
                )
                conn.commit()
            finally:
                db.return_db(conn)

            attention_client, attention_headers = self._authenticated_client(
                "concurrent_preference_sync",
                password,
                garden_id=garden_id,
            )
            notification_client, notification_headers = self._authenticated_client(
                "concurrent_preference_sync",
                password,
                garden_id=garden_id,
            )
            attention_payload = attention_client.get(
                "/api/attention/preferences",
                headers=attention_headers,
            ).json()
            attention_payload.pop("user_id", None)
            attention_payload["show_no_action_history"] = False
            attention_payload["metadata"]["concurrent_attention_update"] = True

            notification_payload = notification_client.get(
                "/api/notifications/preferences",
                headers=notification_headers,
            ).json()
            notification_payload.pop("policy", None)
            notification_payload["notification_rules"]["task_due"] = {
                "in_app_enabled": False,
                "email_enabled": False,
                "min_severity": "high",
            }

            real_attention_save = attention_router.save_attention_preferences
            real_notification_lock = notification_router.lock_attention_preferences
            attention_saved = threading.Event()
            allow_attention_commit = threading.Event()
            notification_lock_attempted = threading.Event()

            def delayed_attention_save(*args, **kwargs):
                saved = real_attention_save(*args, **kwargs)
                attention_saved.set()
                if not allow_attention_commit.wait(timeout=5):
                    raise AssertionError("Timed out waiting to release Attention preference write")
                return saved

            def observed_notification_lock(*args, **kwargs):
                notification_lock_attempted.set()
                return real_notification_lock(*args, **kwargs)

            with (
                patch.object(
                    attention_router,
                    "save_attention_preferences",
                    side_effect=delayed_attention_save,
                ),
                patch.object(
                    notification_router,
                    "lock_attention_preferences",
                    side_effect=observed_notification_lock,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                attention_future = executor.submit(
                    attention_client.put,
                    "/api/attention/preferences",
                    headers=attention_headers,
                    json=attention_payload,
                )
                self.assertTrue(attention_saved.wait(timeout=5))
                notification_future = executor.submit(
                    notification_client.put,
                    "/api/notifications/preferences",
                    headers=notification_headers,
                    json=notification_payload,
                )
                self.assertTrue(notification_lock_attempted.wait(timeout=5))
                self.assertFalse(notification_future.done())
                allow_attention_commit.set()
                attention_response = attention_future.result(timeout=5)
                notification_response = notification_future.result(timeout=5)

            self.assertEqual(attention_response.status_code, 200, attention_response.text)
            self.assertEqual(notification_response.status_code, 200, notification_response.text)
            final = attention_client.get(
                "/api/attention/preferences",
                headers=attention_headers,
            )
            self.assertEqual(final.status_code, 200, final.text)
            final_body = final.json()
            self.assertFalse(final_body["show_no_action_history"])
            self.assertTrue(final_body["metadata"]["concurrent_attention_update"])
            self.assertFalse(final_body["rules"]["task_due"]["inbox"])
            self.assertEqual(final_body["rules"]["task_due"]["min_severity"], "high")

    def test_notification_settings_round_trip_preserves_distinct_grouped_attention_rules(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
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
                user_id, password = self._create_pro_member(conn, "grouped_rule_round_trip")
                conn.execute(
                    """
                    INSERT INTO garden_memberships (garden_id, user_id, role)
                    VALUES (%s, %s, 'editor')
                    ON CONFLICT DO NOTHING
                    """,
                    (garden_id, user_id),
                )
                self._save_legacy_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "issue_created": {
                            "in_app_enabled": False,
                            "email_enabled": False,
                            "min_severity": "high",
                        }
                    },
                )
                self._save_attention_preferences(
                    conn,
                    user_id=user_id,
                    now_ms=_FROZEN_NOW_MS,
                    rules={
                        "issue_follow_up_due": {
                            "panel": True,
                            "inbox": True,
                            "digest": True,
                            "min_severity": "low",
                        },
                        "issue_follow_up_overdue": {
                            "panel": True,
                            "inbox": False,
                            "digest": False,
                            "min_severity": "high",
                        },
                    },
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                "grouped_rule_round_trip",
                password,
                garden_id=garden_id,
            )
            current = client.get("/api/notifications/preferences", headers=headers)
            self.assertEqual(current.status_code, 200, current.text)
            unchanged = current.json()
            unchanged.pop("policy", None)
            saved = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json=unchanged,
            )
            self.assertEqual(saved.status_code, 200, saved.text)

            conn = db.get_db()
            try:
                after_round_trip = conn.execute(
                    "SELECT rules_json FROM user_attention_preferences WHERE user_id = %s",
                    (user_id,),
                ).fetchone()
            finally:
                db.return_db(conn)
            assert after_round_trip is not None
            rules_after_round_trip = json.loads(str(after_round_trip["rules_json"]))
            self.assertEqual(rules_after_round_trip["issue_follow_up_due"]["inbox"], True)
            self.assertEqual(rules_after_round_trip["issue_follow_up_due"]["digest"], True)
            self.assertEqual(rules_after_round_trip["issue_follow_up_due"]["min_severity"], "low")
            self.assertEqual(rules_after_round_trip["issue_follow_up_overdue"]["inbox"], False)
            self.assertEqual(rules_after_round_trip["issue_follow_up_overdue"]["digest"], False)
            self.assertEqual(
                rules_after_round_trip["issue_follow_up_overdue"]["min_severity"],
                "high",
            )

            unchanged["notification_rules"]["issue_created"] = {
                "in_app_enabled": False,
                "email_enabled": False,
                "min_severity": "critical",
            }
            changed = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json=unchanged,
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            conn = db.get_db()
            try:
                after_change = conn.execute(
                    "SELECT rules_json FROM user_attention_preferences WHERE user_id = %s",
                    (user_id,),
                ).fetchone()
            finally:
                db.return_db(conn)
            assert after_change is not None
            rules_after_change = json.loads(str(after_change["rules_json"]))
            for key in ("issue_follow_up_due", "issue_follow_up_overdue"):
                self.assertFalse(rules_after_change[key]["inbox"])
                self.assertFalse(rules_after_change[key]["digest"])
                self.assertEqual(rules_after_change[key]["min_severity"], "critical")

    def test_digest_cadence_is_scoped_to_the_garden_that_was_delivered(self) -> None:
        from gardenops.services.notification_service import (
            create_notification,
            deliver_pending_email_digests,
        )

        conn = db.get_db()
        try:
            first_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
            ).fetchone()
            assert first_garden is not None
            first_garden_id = int(first_garden["id"])
            second_garden = conn.execute(
                """
                INSERT INTO gardens (slug, name)
                VALUES ('digest-second', 'Digest Second')
                RETURNING id
                """
            ).fetchone()
            assert second_garden is not None
            second_garden_id = int(second_garden["id"])
            user_id, _password = self._create_pro_member(conn, "digest_two_gardens")
            for garden_id in (first_garden_id, second_garden_id):
                conn.execute(
                    """
                    INSERT INTO garden_memberships (garden_id, user_id, role)
                    VALUES (%s, %s, 'editor')
                    ON CONFLICT DO NOTHING
                    """,
                    (garden_id, user_id),
                )
            self._save_legacy_preferences(
                conn,
                user_id=user_id,
                now_ms=_FROZEN_NOW_MS,
                email_address="digest-two-gardens@example.test",
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
            conn.commit()
            create_notification(
                conn,
                first_garden_id,
                user_id,
                "task_due",
                "First garden task",
                "First garden needs attention.",
                target_type="task",
                target_id="first-garden-task",
                now_ms=_FROZEN_NOW_MS,
            )
            create_notification(
                conn,
                second_garden_id,
                user_id,
                "task_due",
                "Second garden task",
                "Second garden needs attention.",
                target_type="task",
                target_id="second-garden-task",
                now_ms=_FROZEN_NOW_MS,
            )
            sent: list[tuple[str, str, str]] = []
            first_delivery = deliver_pending_email_digests(
                conn,
                first_garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=_FROZEN_NOW_MS,
            )
            second_delivery = deliver_pending_email_digests(
                conn,
                second_garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=_FROZEN_NOW_MS,
            )
        finally:
            db.return_db(conn)

        self.assertEqual(int(first_delivery["emailed_users"]), 1)
        self.assertEqual(int(second_delivery["emailed_users"]), 1)
        self.assertEqual(len(sent), 2)
        self.assertIn("First garden task", sent[0][2])
        self.assertIn("Second garden task", sent[1][2])
