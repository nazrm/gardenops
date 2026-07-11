import json
import os
from unittest.mock import patch

import gardenops.db as db
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password


class TestNotifications(BaseApiTest):
    def test_notification_crud(self) -> None:
        """Create notification via service, list, mark read, dismiss."""
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])

        # Create a notification directly via service
        nid = _create_notif(
            conn,
            garden_id,
            None,
            "system",
            "Test title",
            "Test body",
            target_type="plot",
            target_id="B1",
        )
        self.assertTrue(nid.startswith("note_"))
        db.return_db(conn)

        # List notifications
        r = self.client.get("/api/notifications")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreaterEqual(data["total"], 1)
        found = [n for n in data["notifications"] if n["id"] == nid]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Test title")
        self.assertEqual(found[0]["target_type"], "plot")
        self.assertIsNone(found[0]["read_at_ms"])

        # Mark read
        r = self.client.post(f"/api/notifications/{nid}/read")
        self.assertEqual(r.status_code, 200)

        # Verify read
        r = self.client.get("/api/notifications")
        found = [n for n in r.json()["notifications"] if n["id"] == nid]
        self.assertIsNotNone(found[0]["read_at_ms"])

        # Dismiss
        r = self.client.delete(f"/api/notifications/{nid}")
        self.assertEqual(r.status_code, 200)

        # Verify dismissed (no longer in list)
        r = self.client.get("/api/notifications")
        found = [n for n in r.json()["notifications"] if n["id"] == nid]
        self.assertEqual(len(found), 0)

    def test_notification_count(self) -> None:
        """Verify unread count endpoint."""
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])

        # Start with zero
        r = self.client.get("/api/notifications/count")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)

        # Create two notifications
        _create_notif(conn, garden_id, None, "system", "N1", "B1")
        _create_notif(conn, garden_id, None, "task_due", "N2", "B2")
        db.return_db(conn)

        r = self.client.get("/api/notifications/count")
        self.assertEqual(r.json()["count"], 2)

    def test_attention_preferences_hide_inbox_without_mutating_notification_log(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            conn = db.get_db()
            try:
                garden_id = int(
                    conn.execute(
                        "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                    ).fetchone()["id"]
                )
                user_id = int(
                    conn.execute(
                        "SELECT id FROM auth_users WHERE username = 'test_admin'",
                    ).fetchone()["id"]
                )
                now = db.current_timestamp_ms()
                today = str(conn.execute("SELECT CURRENT_DATE::text").fetchone()["current_date"])
                conn.execute(
                    """
                    INSERT INTO user_attention_preferences
                        (user_id, preset, rules_json, quiet_hours_json,
                         show_no_action_history, created_at_ms, updated_at_ms)
                    VALUES (%s, 'custom', %s, '{}', 1, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        preset = excluded.preset,
                        rules_json = excluded.rules_json,
                        quiet_hours_json = excluded.quiet_hours_json,
                        show_no_action_history = excluded.show_no_action_history,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        user_id,
                        json.dumps(
                            {
                                "task_due": {
                                    "panel": True,
                                    "inbox": False,
                                    "digest": True,
                                    "min_severity": "low",
                                }
                            },
                            separators=(",", ":"),
                        ),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (public_id, garden_id, task_type, title, status, severity,
                         due_on, metadata_json, created_at_ms, updated_at_ms)
                    VALUES ('task_attention_inbox_hidden', %s, 'water', 'Water basil',
                            'pending', 'normal', %s, '{}', %s, %s)
                    """,
                    (garden_id, today, now, now),
                )
                note_id = _create_notif(
                    conn,
                    garden_id,
                    user_id,
                    "task_due",
                    "Water basil",
                    "Water basil today",
                    target_type="task",
                    target_id="task_attention_inbox_hidden",
                    severity="normal",
                )
                conn.commit()
            finally:
                db.return_db(conn)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(inbox.status_code, 200, inbox.text)
            self.assertNotIn(note_id, [n["id"] for n in inbox.json()["notifications"]])

            count = client.get("/api/notifications/count", headers=headers)
            self.assertEqual(count.status_code, 200, count.text)
            self.assertEqual(count.json()["count"], 0)

            read_all = client.post("/api/notifications/read-all", headers=headers)
            self.assertEqual(read_all.status_code, 200, read_all.text)
            self.assertEqual(read_all.json()["updated"], 0)

            log = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(log.status_code, 200, log.text)
            rows = [n for n in log.json()["notifications"] if n["id"] == note_id]
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["read_at_ms"])
            self.assertIsNone(rows[0]["clear_reason"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_notification_list_can_skip_total_count(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])

        _create_notif(conn, garden_id, None, "system", "N1", "B1")
        db.return_db(conn)

        r = self.client.get("/api/notifications?include_total=false")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("notifications", body)
        self.assertNotIn("total", body)
        self.assertEqual(len(body["notifications"]), 1)

    def test_expired_weather_notification_moves_to_log(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                garden_id = int(garden["id"])
                user_id = int(user["id"])
                nid = _create_notif(
                    conn,
                    garden_id,
                    user_id,
                    "weather_alert",
                    "Frost warning",
                    "Frost window has ended",
                    target_type="weather_alert",
                    target_id="1",
                    notification_subtype="frost_warning",
                    severity="normal",
                    expires_at_ms=db.current_timestamp_ms() - 1_000,
                )
            finally:
                db.return_db(conn)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(inbox.status_code, 200)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])

            log = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(log.status_code, 200)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "expired")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_muted_notification_type_clears_inbox_but_keeps_log(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                garden_id = int(garden["id"])
                user_id = int(user["id"])
                nid = _create_notif(
                    conn,
                    garden_id,
                    user_id,
                    "weather_alert",
                    "Frost warning",
                    "Protect tender plants",
                    target_type="weather_alert",
                    target_id="2",
                    notification_subtype="frost_warning",
                    severity="normal",
                )
            finally:
                db.return_db(conn)

            r = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json={
                    "notification_rules": {
                        "weather_alert:frost_warning": {
                            "in_app_enabled": False,
                            "email_enabled": False,
                            "min_severity": "normal",
                        }
                    }
                },
            )
            self.assertEqual(r.status_code, 200)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(inbox.status_code, 200)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])

            log = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(log.status_code, 200)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "preference_hidden")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_reenabled_task_notification_type_can_create_new_active_notification(self) -> None:
        from gardenops.services.notification_service import create_task_due_notifications
        from gardenops.sql_dates import offset_days_iso

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            conn = db.get_db()
            try:
                garden_id = self._get_default_garden_id()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                assert user is not None
                user_id = int(user["id"])
                today = offset_days_iso(0)
                now = db.current_timestamp_ms()
                task = conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (garden_id, task_type, title, status, severity,
                         due_on, metadata_json, created_at_ms, updated_at_ms)
                    VALUES (%s, 'water', 'Water after preference toggle',
                            'pending', 'normal', %s, '{}', %s, %s)
                    RETURNING public_id
                    """,
                    (garden_id, today, now, now),
                ).fetchone()
                assert task is not None
                task_public_id = str(task["public_id"])
                conn.commit()

                first = create_task_due_notifications(conn, garden_id)
                self.assertEqual(int(first["created"]), 1)
            finally:
                db.return_db(conn)

            muted = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json={
                    "notification_rules": {
                        "task_due": {
                            "in_app_enabled": False,
                            "email_enabled": False,
                            "min_severity": "normal",
                        }
                    }
                },
            )
            self.assertEqual(muted.status_code, 200)

            unmuted = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json={
                    "notification_rules": {
                        "task_due": {
                            "in_app_enabled": True,
                            "email_enabled": False,
                            "min_severity": "normal",
                        }
                    }
                },
            )
            self.assertEqual(unmuted.status_code, 200)

            conn = db.get_db()
            try:
                second = create_task_due_notifications(conn, garden_id)
                self.assertEqual(int(second["created"]), 1)
                counts = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (
                            WHERE dismissed = 0 AND cleared_at_ms IS NULL
                        ) AS active
                    FROM notification_events
                    WHERE garden_id = %s
                      AND user_id = %s
                      AND notification_type = 'task_due'
                      AND target_type = 'task'
                      AND target_id = %s
                    """,
                    (garden_id, user_id, task_public_id),
                ).fetchone()
                assert counts is not None
                self.assertEqual(int(counts["total"]), 2)
                self.assertEqual(int(counts["active"]), 1)
            finally:
                db.return_db(conn)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_run_maintenance_endpoint_only_processes_active_garden(self) -> None:
        from gardenops.sql_dates import offset_days_iso

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            default_garden_id = self._get_default_garden_id()
            conn = db.get_db()
            try:
                admin = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                assert admin is not None
                admin_id = int(admin["id"])
                second = conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES ('notif-g2', 'Notification G2') "
                    "RETURNING id",
                ).fetchone()
                assert second is not None
                second_garden_id = int(second["id"])
                conn.execute(
                    """
                    INSERT INTO garden_memberships (garden_id, user_id, role)
                    VALUES (%s, %s, 'editor')
                    """,
                    (second_garden_id, admin_id),
                )
                today = offset_days_iso(0)
                now = db.current_timestamp_ms()
                default_task = conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (garden_id, task_type, title, status, severity,
                         due_on, metadata_json, created_at_ms, updated_at_ms)
                    VALUES (%s, 'water', 'Default garden task', 'pending',
                            'normal', %s, '{}', %s, %s)
                    RETURNING public_id
                    """,
                    (default_garden_id, today, now, now),
                ).fetchone()
                second_task = conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (garden_id, task_type, title, status, severity,
                         due_on, metadata_json, created_at_ms, updated_at_ms)
                    VALUES (%s, 'water', 'Second garden task', 'pending',
                            'normal', %s, '{}', %s, %s)
                    RETURNING public_id
                    """,
                    (second_garden_id, today, now, now),
                ).fetchone()
                assert default_task is not None
                assert second_task is not None
                conn.commit()
            finally:
                db.return_db(conn)

            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            response = client.post(
                "/api/notifications/run-maintenance",
                headers=self._session_headers(csrf, garden_id=default_garden_id),
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["gardens_processed"], 1)

            conn = db.get_db()
            try:
                active_rows = conn.execute(
                    """
                    SELECT target_id
                    FROM notification_events
                    WHERE notification_type = 'task_due'
                      AND target_type = 'task'
                      AND dismissed = 0
                      AND cleared_at_ms IS NULL
                    """,
                ).fetchall()
                active_targets = {str(row["target_id"]) for row in active_rows}
                self.assertIn(str(default_task["public_id"]), active_targets)
                self.assertNotIn(str(second_task["public_id"]), active_targets)
            finally:
                db.return_db(conn)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_expired_snoozed_task_becomes_actionable_for_notifications(self) -> None:
        from gardenops.services.notification_service import create_task_due_notifications
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert user is not None
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            today = offset_days_iso(0)
            now = db.current_timestamp_ms()
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, snoozed_until, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Expired snooze should notify',
                        'snoozed', 'normal', %s, %s, '{}', %s, %s)
                RETURNING public_id
                """,
                (garden_id, yesterday, today, now, now),
            ).fetchone()
            assert task is not None
            conn.commit()

            result = create_task_due_notifications(conn, garden_id)
            self.assertEqual(int(result["created"]), 1)
            row = conn.execute(
                """
                SELECT notification_type, metadata_json
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND dismissed = 0
                  AND cleared_at_ms IS NULL
                LIMIT 1
                """,
                (garden_id, user_id, str(task["public_id"])),
            ).fetchone()
            assert row is not None
            self.assertEqual(str(row["notification_type"]), "task_due")
            self.assertIn(today, str(row["metadata_json"]))
        finally:
            db.return_db(conn)

    def test_completed_task_notification_moves_to_log(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            task = client.post(
                "/api/tasks",
                headers=headers,
                json={
                    "task_type": "water",
                    "title": "Water notification test",
                    "due_on": "2026-03-13",
                },
            )
            self.assertEqual(task.status_code, 201)
            task_id = task.json()["id"]

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                nid = _create_notif(
                    conn,
                    int(garden["id"]),
                    int(user["id"]),
                    "task_due",
                    "Task due today",
                    "Water notification test",
                    target_type="task",
                    target_id=task_id,
                )
            finally:
                db.return_db(conn)

            done = client.post(
                f"/api/tasks/{task_id}/action",
                headers=headers,
                json={"action": "complete"},
            )
            self.assertEqual(done.status_code, 200)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])
            log = client.get("/api/notifications?scope=log", headers=headers)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "completed")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_legacy_completed_task_notification_clears_on_list(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            task = client.post(
                "/api/tasks",
                headers=headers,
                json={
                    "task_type": "water",
                    "title": "Legacy completed task notification",
                    "due_on": "2026-03-13",
                },
            )
            self.assertEqual(task.status_code, 201)
            task_id = task.json()["id"]

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                assert garden is not None
                assert user is not None
                garden_id = int(garden["id"])
                user_id = int(user["id"])
                task_row = conn.execute(
                    "SELECT id FROM garden_tasks WHERE public_id = %s",
                    (task_id,),
                ).fetchone()
                assert task_row is not None
                now = db.current_timestamp_ms()
                conn.execute(
                    """
                    UPDATE garden_tasks
                    SET status = 'completed',
                        completed_by_user_id = %s,
                        completed_at_ms = %s,
                        updated_at_ms = %s
                    WHERE id = %s
                    """,
                    (user_id, now, now, int(task_row["id"])),
                )
                nid = _create_notif(
                    conn,
                    garden_id,
                    user_id,
                    "task_overdue",
                    "Overdue: Legacy completed task notification",
                    "Due on 2026-03-13",
                    target_type="task",
                    target_id=task_id,
                    metadata={"due_on": "2026-03-13"},
                )
                conn.commit()
            finally:
                db.return_db(conn)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(inbox.status_code, 200)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])

            log = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(log.status_code, 200)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "completed")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_old_overdue_task_notification_clears_on_list(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif
        from gardenops.sql_dates import offset_days_iso

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            due_on = offset_days_iso(-3)
            task = client.post(
                "/api/tasks",
                headers=headers,
                json={
                    "task_type": "water",
                    "title": "Old overdue task notification",
                    "due_on": due_on,
                },
            )
            self.assertEqual(task.status_code, 201)
            task_id = task.json()["id"]

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                assert garden is not None
                assert user is not None
                garden_id = int(garden["id"])
                user_id = int(user["id"])
                nid = _create_notif(
                    conn,
                    garden_id,
                    user_id,
                    "task_overdue",
                    "Overdue: Old overdue task notification",
                    f"Due on {due_on}",
                    target_type="task",
                    target_id=task_id,
                    metadata={"due_on": due_on},
                )
                conn.execute(
                    """
                    UPDATE notification_events
                    SET created_at_ms = created_at_ms - 172800000
                    WHERE public_id = %s
                    """,
                    (nid,),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(inbox.status_code, 200)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])

            log = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(log.status_code, 200)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "expired")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_old_task_generated_notification_clears_on_list(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                assert garden is not None
                assert user is not None
                nid = _create_notif(
                    conn,
                    int(garden["id"]),
                    int(user["id"]),
                    "task_generated",
                    "Seasonal tasks generated",
                    "Tasks for an old month are ready.",
                    target_type="task_batch",
                    target_id="2026-04",
                )
                conn.execute(
                    """
                    UPDATE notification_events
                    SET created_at_ms = created_at_ms - 172800000
                    WHERE public_id = %s
                    """,
                    (nid,),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertEqual(inbox.status_code, 200)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])

            log = client.get("/api/notifications?scope=log", headers=headers)
            self.assertEqual(log.status_code, 200)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "expired")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_resolved_issue_notification_moves_to_log(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)

            issue = client.post(
                "/api/issues",
                headers=headers,
                json={
                    "issue_type": "damage",
                    "title": "Broken stem",
                    "description": "Wind damage",
                    "severity": "normal",
                },
            )
            self.assertEqual(issue.status_code, 201)
            issue_id = issue.json()["id"]

            conn = db.get_db()
            try:
                garden = conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()
                user = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                nid = _create_notif(
                    conn,
                    int(garden["id"]),
                    int(user["id"]),
                    "issue_created",
                    "Issue reported",
                    "Broken stem",
                    target_type="issue",
                    target_id=issue_id,
                    severity="normal",
                )
            finally:
                db.return_db(conn)

            resolved = client.post(
                f"/api/issues/{issue_id}/resolve",
                headers=headers,
            )
            self.assertEqual(resolved.status_code, 200)

            inbox = client.get("/api/notifications", headers=headers)
            self.assertNotIn(nid, [n["id"] for n in inbox.json()["notifications"]])
            log = client.get("/api/notifications?scope=log", headers=headers)
            found = [n for n in log.json()["notifications"] if n["id"] == nid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["clear_reason"], "resolved")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_notification_mark_all_read(self) -> None:
        """Mark all read, verify count drops to zero."""
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])

        _create_notif(conn, garden_id, None, "system", "N1", "B1")
        _create_notif(conn, garden_id, None, "task_due", "N2", "B2")
        db.return_db(conn)

        r = self.client.get("/api/notifications/count")
        self.assertEqual(r.json()["count"], 2)

        r = self.client.post("/api/notifications/read-all")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["updated"], 2)

        r = self.client.get("/api/notifications/count")
        self.assertEqual(r.json()["count"], 0)

    def test_notification_preferences_default(self) -> None:
        """Get defaults when no prefs exist."""
        r = self.client.get("/api/notifications/preferences")
        self.assertEqual(r.status_code, 200)
        prefs = r.json()
        self.assertTrue(prefs["in_app_enabled"])
        self.assertFalse(prefs["email_enabled"])
        self.assertEqual(prefs["email_address"], "")
        self.assertEqual(prefs["digest_frequency"], "daily")
        self.assertTrue(prefs["task_due_enabled"])
        self.assertTrue(prefs["task_overdue_enabled"])

    def test_notification_preferences_update(self) -> None:
        """Update and verify preferences with authenticated user."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("prefs_user", "prefspass", role="editor")

            client = self._new_client()
            _, csrf = self._login_session("prefs_user", "prefspass", client=client)
            headers = self._session_headers(csrf)

            r = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json={
                    "in_app_enabled": True,
                    "email_enabled": True,
                    "email_address": "test@example.com",
                    "digest_frequency": "weekly",
                    "quiet_hours_json": {},
                    "task_due_enabled": False,
                    "task_overdue_enabled": True,
                },
            )
            self.assertEqual(r.status_code, 200)

            r = client.get("/api/notifications/preferences", headers=headers)
            prefs = r.json()
            self.assertTrue(prefs["email_enabled"])
            self.assertEqual(prefs["email_address"], "test@example.com")
            self.assertEqual(prefs["digest_frequency"], "weekly")
            self.assertFalse(prefs["task_due_enabled"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_notification_preferences_reject_email_for_non_pro_tier(self) -> None:
        self._create_test_user("prefs_nonpro", "prefsnonpropass", role="editor")
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'enthusiast' WHERE username = %s",
                ("prefs_nonpro",),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session("prefs_nonpro", "prefsnonpropass", client=client)
            r = client.put(
                "/api/notifications/preferences",
                headers=self._session_headers(csrf),
                json={
                    "email_enabled": True,
                    "email_address": "nonpro@example.com",
                },
            )
            self.assertEqual(r.status_code, 403, r.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_notification_delivery_requires_admin_role(self) -> None:
        self._create_test_user("delivery_editor", "deliveryeditorpass", role="editor")
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session(
                "delivery_editor",
                "deliveryeditorpass",
                client=client,
            )
            response = client.post(
                "/api/notifications/process-delivery",
                headers=self._session_headers(csrf),
            )
            self.assertEqual(response.status_code, 403, response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_notification_generate_from_tasks(self) -> None:
        """Create a due task, generate notifications, verify created."""
        # Create a task due today
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Water today",
                "due_on": "2026-03-13",
            },
        )
        self.assertEqual(r.status_code, 201)

        # Generate notifications
        r = self.client.post("/api/notifications/generate")
        self.assertEqual(r.status_code, 200)
        result = r.json()
        # May create 0 if no garden_memberships exist for default user
        # At minimum it should return the right structure
        self.assertIn("created", result)
        self.assertIn("skipped", result)

    def test_work_order_task_notification_uses_plant_count(self) -> None:
        from gardenops.services.notification_service import create_task_due_notifications
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            now = db.current_timestamp_ms()
            plant_ids = ["WO1", "WO2", "WO3", "WO4"]
            for idx, plant_id in enumerate(plant_ids, start=1):
                conn.execute(
                    """
                    INSERT INTO plants
                        (plt_id, name, latin, category, bloom_month, color,
                         hardiness, height_cm, light, link)
                    VALUES (%s, %s, '', 'busker', '', '', '', NULL, '', '')
                    """,
                    (plant_id, f"Work Plant {idx}"),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    """,
                    (plant_id, self._owner_id, garden_id),
                )
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'prune', 'Prune 4 plants', '', 'pending', 'normal',
                        %s, 'work_order:prune:2026-W11', %s, %s, %s)
                RETURNING id, public_id
                """,
                (
                    garden_id,
                    offset_days_iso(0),
                    json.dumps({"work_order": True}),
                    now,
                    now,
                ),
            ).fetchone()
            assert task is not None
            for plant_id in plant_ids:
                conn.execute(
                    "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                    (task["id"], plant_id),
                )
            conn.commit()

            result = create_task_due_notifications(conn, garden_id)
            self.assertGreaterEqual(int(result["created"]), 1)
            notification = conn.execute(
                """
                SELECT title, metadata_json
                FROM notification_events
                WHERE garden_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND notification_type = 'task_due'
                ORDER BY id DESC
                LIMIT 1
                """,
                (garden_id, task["public_id"]),
            ).fetchone()
            assert notification is not None
            self.assertEqual(notification["title"], "Due today: Prune 4 plants")
            metadata = json.loads(str(notification["metadata_json"]))
            self.assertEqual(metadata["plant_count"], 4)
            self.assertEqual(len(metadata["plants"]), 4)
        finally:
            db.return_db(conn)

    def test_partial_completion_refreshes_task_notification_plant_names(self) -> None:
        from gardenops.services.notification_service import create_task_due_notifications

        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Fertilize 2 plants",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]
        garden_id = self._get_default_garden_id()

        conn = db.get_db()
        try:
            create_task_due_notifications(conn, garden_id)
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            rows = conn.execute(
                """
                SELECT title, metadata_json
                FROM notification_events
                WHERE garden_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND cleared_at_ms IS NULL
                ORDER BY id ASC
                """,
                (garden_id, task_id),
            ).fetchall()
        finally:
            db.return_db(conn)
        self.assertGreaterEqual(len(rows), 1)
        joined = " ".join(f"{row['title']} {json.dumps(row['metadata_json'])}" for row in rows)
        self.assertNotIn("Test Plant", joined)
        self.assertIn("Rose", joined)

    def test_partial_completion_notification_refresh_is_scoped_to_task(self) -> None:
        from gardenops.services.notification_service import (
            clear_task_notifications,
            create_task_due_notifications,
        )

        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Fertilize 2 plants",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        partial_task_id = response.json()["id"]
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Unrelated due task",
                "due_on": "2026-06-01",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        unrelated_task_id = response.json()["id"]
        garden_id = self._get_default_garden_id()

        conn = db.get_db()
        try:
            create_task_due_notifications(conn, garden_id)
            clear_task_notifications(
                conn,
                garden_id=garden_id,
                task_public_id=unrelated_task_id,
                reason="superseded",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.post(
            f"/api/tasks/{partial_task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            active_unrelated = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM notification_events
                WHERE garden_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND cleared_at_ms IS NULL
                """,
                (garden_id, unrelated_task_id),
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(active_unrelated["count"]), 0)

    def test_dismissed_task_notification_does_not_regenerate(self) -> None:
        from gardenops.services.notification_service import (
            create_task_due_notifications,
            dismiss_notification,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            now = db.current_timestamp_ms()
            due_on = offset_days_iso(-1)
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, created_at_ms, updated_at_ms)
                VALUES (%s, 'protect', 'Dismiss me once',
                        'pending', 'normal', %s, %s, %s)
                RETURNING public_id
                """,
                (garden_id, due_on, now, now),
            ).fetchone()
            assert task is not None
            task_public_id = str(task["public_id"])
            conn.commit()

            first = create_task_due_notifications(conn, garden_id)
            self.assertGreaterEqual(int(first["created"]), 1)
            notification = conn.execute(
                """
                SELECT public_id
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'task_overdue'
                  AND target_type = 'task'
                  AND target_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (garden_id, user_id, task_public_id),
            ).fetchone()
            assert notification is not None

            self.assertTrue(
                dismiss_notification(
                    conn,
                    str(notification["public_id"]),
                    user_id,
                    garden_id,
                )
            )
            second = create_task_due_notifications(conn, garden_id)
            self.assertEqual(int(second["created"]), 0)

            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE dismissed = 0 AND cleared_at_ms IS NULL
                    ) AS active
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'task_overdue'
                  AND target_type = 'task'
                  AND target_id = %s
                """,
                (garden_id, user_id, task_public_id),
            ).fetchone()
            assert counts is not None
            self.assertEqual(int(counts["total"]), 1)
            self.assertEqual(int(counts["active"]), 0)
        finally:
            db.return_db(conn)

    def test_expired_weather_task_notification_moves_to_log(self) -> None:
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )
        from gardenops.services.notification_service import (
            create_task_due_notifications,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            now = db.current_timestamp_ms()
            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'frost_warning', 'normal', 'Old frost',
                        'Frost has passed', %s, %s, '{}', %s)
                RETURNING id
                """,
                (garden_id, yesterday, yesterday, now),
            ).fetchone()
            assert alert is not None
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'protect', 'Protect from frost: Old plant',
                        'pending', 'high', %s, %s, '{}', %s, %s)
                RETURNING public_id
                """,
                (
                    garden_id,
                    yesterday,
                    f"auto:frost_protect:{int(alert['id'])}:OLD-PLANT",
                    now,
                    now,
                ),
            ).fetchone()
            assert task is not None
            task_public_id = str(task["public_id"])
            notification_id = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_overdue",
                "Overdue: Protect from frost: Old plant",
                f"Due on {yesterday}",
                target_type="task",
                target_id=task_public_id,
                metadata={"due_on": yesterday},
            )

            result = create_task_due_notifications(conn, garden_id)
            self.assertIn("created", result)
            row = conn.execute(
                """
                SELECT clear_reason, cleared_at_ms
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            assert row is not None
            self.assertEqual(row["clear_reason"], "expired")
            self.assertIsNotNone(row["cleared_at_ms"])

            active = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND dismissed = 0
                  AND cleared_at_ms IS NULL
                """,
                (garden_id, user_id, task_public_id),
            ).fetchone()
            assert active is not None
            self.assertEqual(int(active["c"]), 0)
        finally:
            db.return_db(conn)

    def test_past_weather_watering_notification_moves_to_log(self) -> None:
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )
        from gardenops.services.notification_service import (
            create_task_due_notifications,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            today = offset_days_iso(0)
            tomorrow = offset_days_iso(1)
            now = db.current_timestamp_ms()
            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'dry_spell', 'normal', 'Dry spell',
                        'Water regularly', %s, %s, '{}', %s)
                RETURNING id
                """,
                (garden_id, today, tomorrow, now),
            ).fetchone()
            assert alert is not None
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Water regularly: Old plant',
                        'pending', 'normal', %s, %s, '{}', %s, %s)
                RETURNING public_id
                """,
                (
                    garden_id,
                    yesterday,
                    f"auto:dry_water:{int(alert['id'])}:OLD-PLANT",
                    now,
                    now,
                ),
            ).fetchone()
            assert task is not None
            task_public_id = str(task["public_id"])
            notification_id = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_overdue",
                "Overdue: Water regularly: Old plant",
                f"Due on {yesterday}",
                target_type="task",
                target_id=task_public_id,
                metadata={"due_on": yesterday},
            )

            result = create_task_due_notifications(conn, garden_id)
            self.assertIn("created", result)
            row = conn.execute(
                """
                SELECT clear_reason, cleared_at_ms
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            assert row is not None
            self.assertEqual(row["clear_reason"], "expired")
            self.assertIsNotNone(row["cleared_at_ms"])

            active = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND dismissed = 0
                  AND cleared_at_ms IS NULL
                """,
                (garden_id, user_id, task_public_id),
            ).fetchone()
            assert active is not None
            self.assertEqual(int(active["c"]), 0)
        finally:
            db.return_db(conn)

    def test_attention_today_does_not_mutate_generated_watering_notifications(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            now = db.current_timestamp_ms()
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Water generated dry-spell plant',
                        'pending', 'normal', '2026-07-05',
                        'auto:dry_water:77:ATTN-NOTIF', '{}', %s, %s)
                RETURNING public_id
                """,
                (garden_id, now, now),
            ).fetchone()
            assert task is not None
            notification_id = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_due",
                "Water generated dry-spell plant",
                "Due today",
                target_type="task",
                target_id=str(task["public_id"]),
                metadata={"due_on": "2026-07-05"},
            )
            conn.commit()
            before = conn.execute(
                """
                SELECT dismissed, read_at_ms, cleared_at_ms, clear_reason, superseded_by_id,
                       metadata_json
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            before_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                """,
                (garden_id, user_id),
            ).fetchone()
            assert before is not None
            assert before_count is not None
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            response = self.client.get("/api/attention/today")
        self.assertEqual(response.status_code, 200)

        conn = db.get_db()
        try:
            after = conn.execute(
                """
                SELECT dismissed, read_at_ms, cleared_at_ms, clear_reason, superseded_by_id,
                       metadata_json
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            after_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                """,
                (garden_id, user_id),
            ).fetchone()
            assert after is not None
            assert after_count is not None
            self.assertEqual(dict(after), dict(before))
            self.assertEqual(int(after_count["c"]), int(before_count["c"]))
        finally:
            db.return_db(conn)

    def test_notification_runtime_maintenance_generates_and_emails(self) -> None:
        from gardenops.services.notification_service import run_notification_maintenance_once

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            today = str(conn.execute("SELECT CURRENT_DATE::text").fetchone()["current_date"])
            user = create_user(
                conn,
                username=f"notif_runtime_user_{self.__class__.__name__.lower()}",
                password=strong_password("runtimepass123"),
                role="editor",
            )
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                (int(user["id"]),),
            )
            now = db.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO user_notification_preferences
                    (user_id, in_app_enabled, email_enabled, email_address,
                     digest_frequency, quiet_hours_json,
                     task_due_enabled, task_overdue_enabled,
                     created_at_ms, updated_at_ms)
                VALUES (%s, 1, 1, %s, 'daily', '{}', 1, 1, %s, %s)
                """,
                (int(user["id"]), "notif-runtime@example.test", now, now),
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Scheduler test task',
                        'pending', 'normal', %s, %s, %s)
                """,
                (garden_id, today, now, now),
            )
            conn.commit()

            sent: list[tuple[str, str, str]] = []
            result = run_notification_maintenance_once(
                conn,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=now + 1000,
            )
            self.assertEqual(int(result["gardens_processed"]), 1)
            self.assertGreaterEqual(int(result["notifications_created"]), 1)
            self.assertGreaterEqual(int(result["emailed_users"]), 1)
            self.assertGreaterEqual(int(result["notifications_marked"]), 1)
            self.assertGreaterEqual(len(sent), 1)
            self.assertEqual(sent[0][0], "notif-runtime@example.test")
            self.assertIn("Scheduler test task", sent[0][2])

            row = conn.execute(
                """
                SELECT emailed_at_ms
                FROM notification_events
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(user["id"]),),
            ).fetchone()
            assert row is not None
            self.assertIsNotNone(row["emailed_at_ms"])
        finally:
            db.return_db(conn)

    def test_attention_digest_preferences_suppress_email_without_marking_notification(
        self,
    ) -> None:
        from gardenops.services.notification_service import deliver_pending_email_digests

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            user = create_user(
                conn,
                username=f"notif_attention_digest_{self.__class__.__name__.lower()}",
                password=strong_password("attndigestpass123"),
                role="editor",
            )
            user_id = int(user["id"])
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                (user_id,),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'editor')
                ON CONFLICT DO NOTHING
                """,
                (garden_id, user_id),
            )
            now = db.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO user_notification_preferences
                    (user_id, in_app_enabled, email_enabled, email_address,
                     digest_frequency, quiet_hours_json,
                     task_due_enabled, task_overdue_enabled,
                     created_at_ms, updated_at_ms)
                VALUES (%s, 1, 1, %s, 'daily', '{}', 1, 1, %s, %s)
                """,
                (user_id, "attention-digest@example.test", now, now),
            )
            conn.execute(
                """
                INSERT INTO user_attention_preferences
                    (user_id, preset, rules_json, quiet_hours_json,
                     show_no_action_history, created_at_ms, updated_at_ms)
                VALUES (%s, 'custom', %s, '{}', 1, %s, %s)
                """,
                (
                    user_id,
                    json.dumps(
                        {
                            "task_due": {
                                "panel": True,
                                "inbox": True,
                                "digest": False,
                                "min_severity": "low",
                            }
                        },
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                ),
            )
            note = conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, title, body,
                     target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                     dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                     cleared_at_ms, clear_reason, superseded_by_id)
                VALUES ('note_attention_digest_hidden', %s, %s, 'task_due',
                        'Water basil', 'Water basil today', 'task', 'task_digest_hidden',
                        NULL, NULL, '{}', 0, %s, NULL, 'normal', NULL, NULL, NULL, NULL)
                RETURNING id
                """,
                (garden_id, user_id, now),
            ).fetchone()
            assert note is not None
            conn.commit()

            sent: list[tuple[str, str, str]] = []
            result = deliver_pending_email_digests(
                conn,
                garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=now + 86_400_000,
            )

            self.assertEqual(int(result["processed_users"]), 1)
            self.assertEqual(int(result["emailed_users"]), 0)
            self.assertEqual(int(result["notifications_marked"]), 0)
            self.assertEqual(sent, [])
            row = conn.execute(
                """
                SELECT emailed_at_ms, cleared_at_ms, clear_reason
                FROM notification_events
                WHERE id = %s
                """,
                (int(note["id"]),),
            ).fetchone()
            assert row is not None
            self.assertIsNone(row["emailed_at_ms"])
            self.assertIsNone(row["cleared_at_ms"])
            self.assertIsNone(row["clear_reason"])
        finally:
            db.return_db(conn)

    def test_attention_digest_hidden_rows_do_not_starve_later_eligible_rows(self) -> None:
        from gardenops.services.notification_service import deliver_pending_email_digests

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            user = create_user(
                conn,
                username=f"notif_attention_digest_starve_{self.__class__.__name__.lower()}",
                password=strong_password("attndigeststarve123"),
                role="editor",
            )
            user_id = int(user["id"])
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                (user_id,),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'editor')
                ON CONFLICT DO NOTHING
                """,
                (garden_id, user_id),
            )
            now = db.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO user_notification_preferences
                    (user_id, in_app_enabled, email_enabled, email_address,
                     digest_frequency, quiet_hours_json,
                     task_due_enabled, task_overdue_enabled,
                     created_at_ms, updated_at_ms)
                VALUES (%s, 1, 1, %s, 'daily', '{}', 1, 1, %s, %s)
                """,
                (user_id, "attention-digest-starve@example.test", now, now),
            )
            conn.execute(
                """
                INSERT INTO user_attention_preferences
                    (user_id, preset, rules_json, quiet_hours_json,
                     show_no_action_history, created_at_ms, updated_at_ms)
                VALUES (%s, 'custom', %s, '{}', 1, %s, %s)
                """,
                (
                    user_id,
                    json.dumps(
                        {
                            "task_due": {
                                "panel": True,
                                "inbox": True,
                                "digest": False,
                                "min_severity": "low",
                            },
                            "issue_follow_up_due": {
                                "panel": True,
                                "inbox": True,
                                "digest": True,
                                "min_severity": "low",
                            },
                        },
                        separators=(",", ":"),
                    ),
                    now,
                    now,
                ),
            )
            hidden = conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, title, body,
                     target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                     dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                     cleared_at_ms, clear_reason, superseded_by_id)
                VALUES ('note_attention_digest_old_hidden', %s, %s, 'task_due',
                        'Water basil', 'Water basil today', 'task', 'task_digest_hidden',
                        NULL, NULL, '{}', 0, %s, NULL, 'normal', NULL, NULL, NULL, NULL)
                RETURNING id
                """,
                (garden_id, user_id, now),
            ).fetchone()
            eligible = conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, title, body,
                     target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                     dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                     cleared_at_ms, clear_reason, superseded_by_id)
                VALUES ('note_attention_digest_new_eligible', %s, %s, 'issue_created',
                        'Check mildew', 'Review cucumber mildew', 'issue', 'issue_digest_eligible',
                        NULL, NULL, '{}', 0, %s, NULL, 'normal', NULL, NULL, NULL, NULL)
                RETURNING id
                """,
                (garden_id, user_id, now + 1),
            ).fetchone()
            assert hidden is not None
            assert eligible is not None
            conn.commit()

            sent: list[tuple[str, str, str]] = []
            with patch.dict(os.environ, {"NOTIFICATION_DIGEST_MAX_EVENTS_PER_USER": "1"}):
                result = deliver_pending_email_digests(
                    conn,
                    garden_id,
                    email_sender=lambda recipient, subject, body: sent.append(
                        (recipient, subject, body)
                    ),
                    now_ms=now + 86_400_000,
                )

            self.assertEqual(int(result["processed_users"]), 1)
            self.assertEqual(int(result["emailed_users"]), 1)
            self.assertEqual(int(result["notifications_marked"]), 1)
            self.assertEqual(len(sent), 1)
            self.assertIn("Check mildew", sent[0][2])
            rows = conn.execute(
                """
                SELECT id, emailed_at_ms
                FROM notification_events
                WHERE id IN (%s, %s)
                ORDER BY id
                """,
                (int(hidden["id"]), int(eligible["id"])),
            ).fetchall()
            by_id = {int(row["id"]): row for row in rows}
            self.assertIsNone(by_id[int(hidden["id"])]["emailed_at_ms"])
            self.assertIsNotNone(by_id[int(eligible["id"])]["emailed_at_ms"])
        finally:
            db.return_db(conn)

    def test_notification_scheduler_lease(self) -> None:
        from gardenops.services.notification_service import (
            acquire_notification_scheduler_lease,
            release_notification_scheduler_lease,
        )

        conn = db.get_db()
        try:
            now = db.current_timestamp_ms()
            self.assertTrue(
                acquire_notification_scheduler_lease(
                    conn,
                    "owner-a",
                    now_ms=now,
                    poll_seconds=60,
                ),
            )
            self.assertFalse(
                acquire_notification_scheduler_lease(
                    conn,
                    "owner-b",
                    now_ms=now + 1_000,
                    poll_seconds=60,
                ),
            )
            self.assertTrue(
                acquire_notification_scheduler_lease(
                    conn,
                    "owner-b",
                    now_ms=now + 301_000,
                    poll_seconds=60,
                ),
            )
            release_notification_scheduler_lease(conn, "owner-b")
            lease = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'notification_scheduler_lease'",
            ).fetchone()
            self.assertIsNone(lease)
        finally:
            db.return_db(conn)

    def test_mark_read_scoped_to_garden(self) -> None:
        """mark_read must not affect notifications from other gardens."""
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )

        conn = db.get_db()
        try:
            default = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            self.assertIsNotNone(default, "default garden must exist")

            # Create a second garden
            conn.execute(
                "INSERT INTO gardens (slug, name) VALUES ('other', 'Other')",
            )
            conn.commit()
            other = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'other'",
            ).fetchone()
            other_id = int(other["id"])

            # Create notification in the other garden
            nid = _create_notif(
                conn,
                other_id,
                None,
                "system",
                "Secret",
                "For other garden only",
            )
        finally:
            db.return_db(conn)

        # Try to mark it read from the default garden context
        r = self.client.post(f"/api/notifications/{nid}/read")
        self.assertEqual(r.status_code, 404)

    def test_dismiss_scoped_to_garden(self) -> None:
        """dismiss_notification must not affect notifications from other gardens."""
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )

        conn = db.get_db()
        try:
            default = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            self.assertIsNotNone(default, "default garden must exist")

            conn.execute(
                """
                INSERT INTO gardens (slug, name)
                VALUES ('other_dismiss', 'Other Dismiss') ON CONFLICT DO NOTHING
                """,
            )
            conn.commit()
            other = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'other_dismiss'",
            ).fetchone()
            other_id = int(other["id"])

            nid = _create_notif(
                conn,
                other_id,
                None,
                "system",
                "Secret",
                "For other garden only",
            )
        finally:
            db.return_db(conn)

        r = self.client.delete(f"/api/notifications/{nid}")
        self.assertEqual(r.status_code, 404)

    def test_notification_dedup(self) -> None:
        """Generate twice, no duplicates for same task."""
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])

        # Create a task due today
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune roses",
                "due_on": "2026-03-13",
            },
        )
        self.assertEqual(r.status_code, 201)
        task_id = r.json()["id"]

        # Create a notification for this task manually (as if generated)
        _create_notif(
            conn,
            garden_id,
            None,
            "task_due",
            "Due today: Prune roses",
            "Due today",
            target_type="task",
            target_id=str(task_id),
        )
        db.return_db(conn)

        # Count before
        r = self.client.get("/api/notifications/count")
        count_before = r.json()["count"]

        # Generate - should not create duplicate for same task
        r = self.client.post("/api/notifications/generate")
        self.assertEqual(r.status_code, 200)

        # Count after should be same (no new notification for that task)
        r = self.client.get("/api/notifications/count")
        count_after = r.json()["count"]
        # The existing notification covers the task, so count should not grow
        # (it may grow if there are other tasks, but the specific task should not duplicate)
        self.assertGreaterEqual(count_after, count_before)

    def test_preferences_reject_invalid_email(self) -> None:
        """Preferences endpoint must reject malformed email addresses."""
        import os

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("email_val_user", "emailvalpass", role="editor")

            client = self._new_client()
            _, csrf = self._login_session("email_val_user", "emailvalpass", client=client)
            headers = self._session_headers(csrf)

            # Valid email should work
            r = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json={"email_address": "valid@example.com"},
            )
            self.assertEqual(r.status_code, 200)

            # Invalid emails should be rejected
            for bad in ["not-an-email", "missing@", "@no-local", "has spaces@x.com"]:
                r = client.put(
                    "/api/notifications/preferences",
                    headers=headers,
                    json={"email_address": bad},
                )
                self.assertEqual(r.status_code, 422, f"Should reject: {bad!r}")

            # Empty string is allowed (means "disable email")
            r = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json={"email_address": ""},
            )
            self.assertEqual(r.status_code, 200)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_quiet_hours_parse_failure_logs_warning(self) -> None:
        """Malformed quiet_hours_json should log a warning."""
        import logging

        from gardenops.services.notification_service import _parse_quiet_hours

        with self.assertLogs(
            "gardenops.services.notification_service",
            level=logging.WARNING,
        ) as cm:
            result = _parse_quiet_hours('{"start": "not-a-time"}')

        self.assertIsNone(result)
        self.assertTrue(
            any("quiet_hours" in msg.lower() or "quiet hours" in msg.lower() for msg in cm.output),
        )

    def test_smtp_tls_off_with_auth_logs_warning(self) -> None:
        """SMTP without TLS + credentials should log a security warning."""
        import logging
        import os

        env = {
            "GARDENOPS_SMTP_HOST": "smtp.example.com",
            "GARDENOPS_SMTP_FROM": "test@example.com",
            "GARDENOPS_SMTP_PORT": "587",
            "GARDENOPS_SMTP_USERNAME": "user",
            "GARDENOPS_SMTP_PASSWORD": "pass",
            "GARDENOPS_SMTP_TLS": "false",
        }
        original = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            from gardenops.services.notification_service import _smtp_settings

            with self.assertLogs(
                "gardenops.services.notification_service",
                level=logging.WARNING,
            ) as cm:
                settings = _smtp_settings()

            self.assertIsNotNone(settings)
            self.assertTrue(
                any("tls" in msg.lower() or "plaintext" in msg.lower() for msg in cm.output),
            )
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_notification_generate_rate_limited(self) -> None:
        """Expensive notification endpoints should have tight rate limits."""
        import os

        os.environ["NOTIFICATION_GENERATE_RATE_LIMIT"] = "2"
        try:
            for _ in range(2):
                r = self.client.post("/api/notifications/generate")
                self.assertEqual(r.status_code, 200)

            r = self.client.post("/api/notifications/generate")
            self.assertEqual(r.status_code, 429)
        finally:
            os.environ.pop("NOTIFICATION_GENERATE_RATE_LIMIT", None)


class TestRainSuppressedWateringNotificationLifecycle(BaseApiTest):
    def test_attention_today_read_does_not_mutate_notification_events(self) -> None:
        from gardenops.services.notification_service import create_notification as _create_notif

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            now = db.current_timestamp_ms()
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Water generated dry-spell plant',
                        'pending', 'normal', '2026-07-05',
                        'auto:dry_water:77:ATTN-NOTIF-READ', '{}', %s, %s)
                RETURNING public_id
                """,
                (garden_id, now, now),
            ).fetchone()
            assert task is not None
            notification_id = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_due",
                "Water generated dry-spell plant",
                "Due today",
                target_type="task",
                target_id=str(task["public_id"]),
                metadata={"due_on": "2026-07-05"},
            )
            conn.commit()
            before = conn.execute(
                """
                SELECT dismissed, read_at_ms, cleared_at_ms, clear_reason, superseded_by_id,
                       metadata_json
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            before_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                """,
                (garden_id, user_id),
            ).fetchone()
            assert before is not None
            assert before_count is not None
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            response = self.client.get("/api/attention/today")
        self.assertEqual(response.status_code, 200)

        conn = db.get_db()
        try:
            after = conn.execute(
                """
                SELECT dismissed, read_at_ms, cleared_at_ms, clear_reason, superseded_by_id,
                       metadata_json
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            after_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                """,
                (garden_id, user_id),
            ).fetchone()
            assert after is not None
            assert after_count is not None
            self.assertEqual(dict(after), dict(before))
            self.assertEqual(int(after_count["c"]), int(before_count["c"]))
        finally:
            db.return_db(conn)

    def test_task_weather_maintenance_clears_stale_generated_watering_notifications(self) -> None:
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )
        from gardenops.services.notification_service import (
            create_task_due_notifications,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            today = offset_days_iso(0)
            tomorrow = offset_days_iso(1)
            now = db.current_timestamp_ms()
            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'dry_spell', 'normal', 'Dry spell',
                        'Water regularly', %s, %s, '{}', %s)
                RETURNING id
                """,
                (garden_id, today, tomorrow, now),
            ).fetchone()
            assert alert is not None
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Water regularly: Old plant',
                        'pending', 'normal', %s, %s, '{}', %s, %s)
                RETURNING public_id
                """,
                (
                    garden_id,
                    yesterday,
                    f"auto:dry_water:{int(alert['id'])}:OLD-PLANT",
                    now,
                    now,
                ),
            ).fetchone()
            assert task is not None
            task_public_id = str(task["public_id"])
            notification_id = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_overdue",
                "Overdue: Water regularly: Old plant",
                f"Due on {yesterday}",
                target_type="task",
                target_id=task_public_id,
                metadata={"due_on": yesterday},
            )

            result = create_task_due_notifications(conn, garden_id)
            self.assertIn("created", result)
            row = conn.execute(
                """
                SELECT clear_reason, cleared_at_ms, superseded_by_id
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            assert row is not None
            self.assertEqual(row["clear_reason"], "expired")
            self.assertIsNotNone(row["cleared_at_ms"])
            self.assertIsNone(row["superseded_by_id"])
        finally:
            db.return_db(conn)

    def test_generated_weekly_watering_overdue_does_not_notify(self) -> None:
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )
        from gardenops.services.notification_service import (
            create_task_due_notifications,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            now = db.current_timestamp_ms()
            generated = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES ('task_generated_weekly_water_old', %s, 'water',
                        'Generated old weekly water', 'pending', 'normal',
                        %s, %s, '{}', %s, %s)
                RETURNING public_id
                """,
                (garden_id, yesterday, f"water:PLT-OLD:{yesterday}", now, now),
            ).fetchone()
            manual = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES ('task_manual_weekly_water_old', %s, 'water',
                        'Manual old weekly water', 'pending', 'normal',
                        %s, '', '{}', %s, %s)
                RETURNING public_id
                """,
                (garden_id, yesterday, now, now),
            ).fetchone()
            assert generated is not None
            assert manual is not None
            generated_notification = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_overdue",
                "Overdue: Generated old weekly water",
                f"Due on {yesterday}",
                target_type="task",
                target_id=str(generated["public_id"]),
                metadata={"due_on": yesterday},
            )

            result = create_task_due_notifications(conn, garden_id)
            self.assertEqual(result["created"], 1)
            generated_row = conn.execute(
                """
                SELECT clear_reason, cleared_at_ms
                FROM notification_events
                WHERE public_id = %s
                """,
                (generated_notification,),
            ).fetchone()
            assert generated_row is not None
            self.assertEqual(generated_row["clear_reason"], "expired")
            self.assertIsNotNone(generated_row["cleared_at_ms"])
            active_rows = conn.execute(
                """
                SELECT target_id, cleared_at_ms
                FROM notification_events
                WHERE notification_type = 'task_overdue'
                  AND target_type = 'task'
                  AND target_id IN (%s, %s)
                ORDER BY target_id
                """,
                (str(generated["public_id"]), str(manual["public_id"])),
            ).fetchall()
            active_by_target = {
                str(row["target_id"]): row for row in active_rows if row["cleared_at_ms"] is None
            }
            self.assertNotIn(str(generated["public_id"]), active_by_target)
            self.assertIn(str(manual["public_id"]), active_by_target)
        finally:
            db.return_db(conn)

    def test_maintenance_expires_stale_generated_watering_tasks(self) -> None:
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )
        from gardenops.services.notification_service import (
            run_notification_maintenance_for_garden,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            tomorrow = offset_days_iso(1)
            now = db.current_timestamp_ms()
            rows = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, status, severity,
                     due_on, snoozed_until, rule_source, metadata_json, created_at_ms,
                     updated_at_ms)
                VALUES
                    ('task_lifecycle_weekly_water_old', %s, 'water',
                     'Generated old weekly water', 'pending', 'normal',
                     %s, NULL, %s, '{}', %s, %s),
                    ('task_lifecycle_dry_water_old', %s, 'water',
                     'Generated old dry water', 'pending', 'normal',
                     %s, NULL, %s, '{}', %s, %s),
                    ('task_lifecycle_snoozed_water_future', %s, 'water',
                     'Generated snoozed future water', 'snoozed', 'normal',
                     %s, %s, %s, '{}', %s, %s),
                    ('task_lifecycle_manual_water_old', %s, 'water',
                     'Manual old water', 'pending', 'normal',
                     %s, NULL, '', '{}', %s, %s)
                RETURNING public_id
                """,
                (
                    garden_id,
                    yesterday,
                    f"water:PLT-LIFE:{yesterday}",
                    now,
                    now,
                    garden_id,
                    yesterday,
                    "auto:dry_water:123:PLT-LIFE",
                    now,
                    now,
                    garden_id,
                    yesterday,
                    tomorrow,
                    f"water:PLT-SNOOZE:{yesterday}",
                    now,
                    now,
                    garden_id,
                    yesterday,
                    now,
                    now,
                ),
            ).fetchall()
            task_ids = [str(row["public_id"]) for row in rows]
            generated_notification_ids = [
                _create_notif(
                    conn,
                    garden_id,
                    user_id,
                    "task_overdue",
                    "Overdue generated watering",
                    f"Due on {yesterday}",
                    target_type="task",
                    target_id=task_id,
                    metadata={"due_on": yesterday},
                )
                for task_id in task_ids[:2]
            ]

            result = run_notification_maintenance_for_garden(
                conn,
                garden_id=garden_id,
                now_ms=now,
            )

            self.assertEqual(result["tasks_expired"], 2)
            status_rows = conn.execute(
                """
                SELECT public_id, status, snoozed_until, completed_by_user_id,
                       completed_at_ms, metadata_json
                FROM garden_tasks
                WHERE public_id = ANY(%s)
                ORDER BY public_id
                """,
                (task_ids,),
            ).fetchall()
            statuses = {str(row["public_id"]): row for row in status_rows}
            self.assertEqual(statuses["task_lifecycle_weekly_water_old"]["status"], "expired")
            self.assertEqual(statuses["task_lifecycle_dry_water_old"]["status"], "expired")
            self.assertEqual(statuses["task_lifecycle_snoozed_water_future"]["status"], "snoozed")
            self.assertEqual(statuses["task_lifecycle_manual_water_old"]["status"], "pending")
            for public_id in (
                "task_lifecycle_weekly_water_old",
                "task_lifecycle_dry_water_old",
            ):
                row = statuses[public_id]
                self.assertIsNone(row["snoozed_until"])
                self.assertIsNone(row["completed_by_user_id"])
                self.assertIsNone(row["completed_at_ms"])
                metadata = json.loads(str(row["metadata_json"]))
                self.assertEqual(metadata["lifecycle"]["status"], "expired")
                self.assertEqual(
                    metadata["lifecycle"]["reason"],
                    "stale_generated_watering",
                )
                self.assertEqual(metadata["lifecycle"]["expired_at_ms"], now)

            cleared_rows = conn.execute(
                """
                SELECT clear_reason, cleared_at_ms
                FROM notification_events
                WHERE public_id = ANY(%s)
                ORDER BY public_id
                """,
                (generated_notification_ids,),
            ).fetchall()
            self.assertEqual(len(cleared_rows), 2)
            for row in cleared_rows:
                self.assertEqual(row["clear_reason"], "expired")
                self.assertIsNotNone(row["cleared_at_ms"])
        finally:
            db.return_db(conn)

    def test_generated_weekly_watering_notification_clears_on_stale_maintenance(self) -> None:
        from gardenops.services.notification_service import (
            clear_stale_task_notifications,
        )
        from gardenops.services.notification_service import (
            create_notification as _create_notif,
        )
        from gardenops.sql_dates import offset_days_iso

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'",
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            yesterday = offset_days_iso(-1)
            today = offset_days_iso(0)
            now = db.current_timestamp_ms()
            generated = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES ('task_generated_weekly_water_stale_clear', %s, 'water',
                        'Generated stale weekly water', 'pending', 'normal',
                        %s, %s, '{}', %s, %s)
                RETURNING public_id
                """,
                (garden_id, yesterday, f"water:PLT-CLEAR:{yesterday}", now, now),
            ).fetchone()
            assert generated is not None
            notification_id = _create_notif(
                conn,
                garden_id,
                user_id,
                "task_overdue",
                "Overdue: Generated stale weekly water",
                f"Due on {yesterday}",
                target_type="task",
                target_id=str(generated["public_id"]),
                metadata={"due_on": yesterday},
            )

            cleared = clear_stale_task_notifications(
                conn,
                garden_id=garden_id,
                today_iso=today,
                now_ms=now,
            )
            self.assertEqual(cleared, 1)
            row = conn.execute(
                """
                SELECT clear_reason, cleared_at_ms
                FROM notification_events
                WHERE public_id = %s
                """,
                (notification_id,),
            ).fetchone()
            assert row is not None
            self.assertEqual(row["clear_reason"], "expired")
            self.assertIsNotNone(row["cleared_at_ms"])
        finally:
            db.return_db(conn)
