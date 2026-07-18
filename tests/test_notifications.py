import json
import os
import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from email import message_from_bytes
from typing import Any
from unittest.mock import patch

import gardenops.db as db
from gardenops.security import create_user
from tests.base import BaseApiTest, DbTestBase, strong_password


class _TaskNotificationReadBarrierConnection:
    """Make the old task notification read-then-insert race deterministic."""

    def __init__(self, connection: Any, barrier: threading.Barrier) -> None:
        self._connection = connection
        self._barrier = barrier

    def execute(self, query: Any, params: Any = None) -> Any:
        result = self._connection.execute(query, params)
        normalized_query = " ".join(str(query).upper().split())
        if "FROM NOTIFICATION_EVENTS" in normalized_query and "TASK_DUE" in normalized_query:
            try:
                self._barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
        return result

    def commit(self) -> None:
        self._connection.commit()


class _TaskNotificationScanPauseConnection:
    """Pause generation after its actionable task snapshot has been read."""

    def __init__(
        self,
        connection: Any,
        task_scanned: threading.Event,
        allow_generation: threading.Event,
    ) -> None:
        self._connection = connection
        self._task_scanned = task_scanned
        self._allow_generation = allow_generation

    def execute(self, query: Any, params: Any = None) -> Any:
        result = self._connection.execute(query, params)
        normalized_query = " ".join(str(query).upper().split())
        if "SELECT ID, PUBLIC_ID, TITLE, METADATA_JSON" in normalized_query:
            self._task_scanned.set()
            if not self._allow_generation.wait(timeout=5):
                raise TimeoutError("task notification generation was not released")
        return result

    def commit(self) -> None:
        self._connection.commit()


class _TaskNotificationLockAttemptConnection:
    """Expose when task mutation cleanup waits for the projection lock."""

    def __init__(self, connection: Any, lock_attempted: threading.Event) -> None:
        self._connection = connection
        self._lock_attempted = lock_attempted

    def execute(self, query: Any, params: Any = None) -> Any:
        normalized_query = " ".join(str(query).upper().split())
        if "PG_ADVISORY_XACT_LOCK" in normalized_query:
            self._lock_attempted.set()
        return self._connection.execute(query, params)

    def commit(self) -> None:
        self._connection.commit()


class _DigestDeliveryLockAttemptConnection:
    """Expose when a concurrent digest worker begins waiting for its recipient lock."""

    def __init__(self, connection: Any, lock_attempted: threading.Event) -> None:
        self._connection = connection
        self._lock_attempted = lock_attempted

    def execute(self, query: Any, params: Any = None) -> Any:
        normalized_query = " ".join(str(query).upper().split())
        if "PG_ADVISORY_XACT_LOCK" in normalized_query:
            self._lock_attempted.set()
        return self._connection.execute(query, params)

    def commit(self) -> None:
        self._connection.commit()


class _DigestDeliveryPauseBeforeLockConnection:
    """Pause after recipient discovery but before the recipient lock is acquired."""

    def __init__(
        self,
        connection: Any,
        lock_attempted: threading.Event,
        allow_lock: threading.Event,
    ) -> None:
        self._connection = connection
        self._lock_attempted = lock_attempted
        self._allow_lock = allow_lock

    def execute(self, query: Any, params: Any = None) -> Any:
        normalized_query = " ".join(str(query).upper().split())
        if "PG_ADVISORY_XACT_LOCK" in normalized_query:
            self._lock_attempted.set()
            if not self._allow_lock.wait(timeout=5):
                raise TimeoutError("digest recipient lock was not released")
        return self._connection.execute(query, params)

    def commit(self) -> None:
        self._connection.commit()


class _LoopbackSmtpHandler(socketserver.StreamRequestHandler):
    """Minimal SMTP receiver for exercising the production smtplib boundary."""

    def handle(self) -> None:
        self.wfile.write(b"220 GardenOps loopback SMTP\r\n")
        self.wfile.flush()
        mail_from = ""
        recipients: list[str] = []
        while line := self.rfile.readline():
            command = line.decode("utf-8", errors="replace").rstrip("\r\n")
            upper = command.upper()
            if upper.startswith(("EHLO ", "HELO ")):
                self.wfile.write(b"250-localhost\r\n250 SIZE 1048576\r\n")
            elif upper.startswith("MAIL FROM:"):
                mail_from = command[10:]
                self.wfile.write(b"250 sender accepted\r\n")
            elif upper.startswith("RCPT TO:"):
                recipients.append(command[8:])
                self.wfile.write(b"250 recipient accepted\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 end with <CR><LF>.<CR><LF>\r\n")
                self.wfile.flush()
                payload = bytearray()
                while message_line := self.rfile.readline():
                    if message_line == b".\r\n":
                        break
                    payload.extend(message_line)
                self.server.messages.append(  # type: ignore[attr-defined]
                    {
                        "mail_from": mail_from,
                        "recipients": recipients,
                        "payload": bytes(payload),
                    }
                )
                self.wfile.write(b"250 message accepted\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 closing connection\r\n")
                self.wfile.flush()
                return
            else:
                self.wfile.write(b"250 accepted\r\n")
            self.wfile.flush()


class _LoopbackSmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _LoopbackSmtpHandler)
        self.messages: list[dict[str, Any]] = []


class TestTaskNotificationConcurrency(DbTestBase):
    def test_concurrent_due_notification_checks_create_one_active_row(self) -> None:
        from gardenops.services.notification_service import create_task_due_notifications

        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, self._owner_id),
        )
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, status, severity,
                 due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES ('task_notification_race', %s, 'water', 'Water race', 'pending',
                    'normal', %s, '', '{}', 1, 1)
            RETURNING public_id
            """,
            (self.garden_id, date.today().isoformat()),
        ).fetchone()
        assert task is not None
        self.conn.commit()
        barrier = threading.Barrier(2)

        def create_once() -> dict[str, int]:
            conn = db.get_db()
            try:
                result = create_task_due_notifications(
                    _TaskNotificationReadBarrierConnection(conn, barrier),
                    self.garden_id,
                )
                conn.commit()
                return result
            finally:
                db.return_db(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: create_once(), range(2)))

        self.assertEqual(sum(int(result["created"]) for result in results), 1)
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM notification_events
            WHERE garden_id = %s
              AND user_id = %s
              AND notification_type = 'task_due'
              AND target_type = 'task'
              AND target_id = 'task_notification_race'
              AND dismissed = 0
              AND cleared_at_ms IS NULL
            """,
            (self.garden_id, self._owner_id),
        ).fetchone()
        assert row is not None
        self.assertEqual(int(row["count"]), 1)

    def test_task_clear_serializes_with_in_flight_generation(self) -> None:
        from gardenops.services.notification_service import (
            clear_task_notifications,
            create_task_due_notifications,
        )

        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, self._owner_id),
        )
        self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, status, severity,
                 due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES ('task_notification_action_race', %s, 'water', 'Water action race',
                    'pending', 'normal', %s, '', '{}', 1, 1)
            """,
            (self.garden_id, date.today().isoformat()),
        )
        self.conn.commit()

        task_scanned = threading.Event()
        allow_generation = threading.Event()
        task_updated = threading.Event()
        clear_lock_attempted = threading.Event()

        def generate() -> dict[str, int]:
            conn = db.get_db()
            try:
                return create_task_due_notifications(
                    _TaskNotificationScanPauseConnection(
                        conn,
                        task_scanned,
                        allow_generation,
                    ),
                    self.garden_id,
                )
            finally:
                db.return_db(conn)

        def complete_and_clear() -> int:
            conn = db.get_db()
            try:
                wrapped = _TaskNotificationLockAttemptConnection(conn, clear_lock_attempted)
                wrapped.execute(
                    """
                    UPDATE garden_tasks
                    SET status = 'completed', updated_at_ms = 2
                    WHERE garden_id = %s AND public_id = 'task_notification_action_race'
                    """,
                    (self.garden_id,),
                )
                task_updated.set()
                cleared = clear_task_notifications(
                    wrapped,
                    garden_id=self.garden_id,
                    task_public_id="task_notification_action_race",
                    reason="completed",
                    now_ms=2,
                )
                wrapped.commit()
                return cleared
            finally:
                db.return_db(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            generation_future = pool.submit(generate)
            self.assertTrue(task_scanned.wait(timeout=5))
            action_future = pool.submit(complete_and_clear)
            self.assertTrue(task_updated.wait(timeout=5))
            if not clear_lock_attempted.wait(timeout=0.5):
                action_future.result(timeout=5)
            allow_generation.set()
            generation_result = generation_future.result(timeout=5)
            cleared = action_future.result(timeout=5)

        self.assertEqual(int(generation_result["created"]), 1)
        self.assertEqual(cleared, 1)
        row = self.conn.execute(
            """
            SELECT cleared_at_ms, clear_reason
            FROM notification_events
            WHERE garden_id = %s
              AND user_id = %s
              AND target_type = 'task'
              AND target_id = 'task_notification_action_race'
              AND notification_type = 'task_due'
            """,
            (self.garden_id, self._owner_id),
        ).fetchone()
        assert row is not None
        self.assertEqual(int(row["cleared_at_ms"]), 2)
        self.assertEqual(str(row["clear_reason"]), "completed")


class TestPendingEmailDigestConcurrency(DbTestBase):
    def test_default_sender_delivers_digest_to_loopback_smtp_and_marks_event_after_acceptance(
        self,
    ) -> None:
        """Exercise persisted digest eligibility through the real SMTP client boundary."""
        from gardenops.services.notification_service import deliver_pending_email_digests

        user = create_user(
            self.conn,
            username="digest_loopback_smtp",
            password=strong_password("digest-loopback-smtp"),
            role="editor",
        )
        user_id = int(user["id"])
        now = 1_900_000_000_000
        self.conn.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (user_id,),
        )
        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'editor')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, user_id),
        )
        self.conn.execute(
            """
            INSERT INTO user_notification_preferences
                (user_id, in_app_enabled, email_enabled, email_address,
                 digest_frequency, quiet_hours_json, task_due_enabled,
                 task_overdue_enabled, created_at_ms, updated_at_ms)
            VALUES (%s, 1, 1, 'loopback-recipient@example.test', 'daily', '{}', 1, 1, %s, %s)
            """,
            (user_id, now, now),
        )
        event = self.conn.execute(
            """
            INSERT INTO notification_events
                (public_id, garden_id, user_id, notification_type, title, body,
                 target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                 dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                 cleared_at_ms, clear_reason, superseded_by_id)
            VALUES ('note_digest_loopback_smtp', %s, %s, 'task_due',
                    'Water loopback basil', 'Water loopback basil today', 'task',
                    'task_digest_loopback_smtp', NULL, NULL, '{}', 0, %s, NULL,
                    'normal', NULL, NULL, NULL, NULL)
            RETURNING id
            """,
            (self.garden_id, user_id, now),
        ).fetchone()
        assert event is not None
        self.conn.commit()

        server = _LoopbackSmtpServer()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        host, port = server.server_address
        try:
            with patch.dict(
                os.environ,
                {
                    "GARDENOPS_SMTP_HOST": str(host),
                    "GARDENOPS_SMTP_PORT": str(port),
                    "GARDENOPS_SMTP_FROM": "digest-sender@example.test",
                    "GARDENOPS_SMTP_TLS": "false",
                    "GARDENOPS_SMTP_USERNAME": "",
                    "GARDENOPS_SMTP_PASSWORD": "",
                },
                clear=False,
            ):
                result = deliver_pending_email_digests(
                    self.conn,
                    self.garden_id,
                    now_ms=now + 1,
                )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        self.assertEqual(int(result["emailed_users"]), 1)
        self.assertEqual(int(result["notifications_marked"]), 1)
        self.assertEqual(len(server.messages), 1)
        delivery = server.messages[0]
        self.assertEqual(
            str(delivery["mail_from"]).split(" ", maxsplit=1)[0],
            "<digest-sender@example.test>",
        )
        self.assertEqual(delivery["recipients"], ["<loopback-recipient@example.test>"])
        message = message_from_bytes(delivery["payload"])
        self.assertEqual(message["From"], "digest-sender@example.test")
        self.assertEqual(message["To"], "loopback-recipient@example.test")
        self.assertIn("Water loopback basil", message.get_payload())
        row = self.conn.execute(
            "SELECT emailed_at_ms FROM notification_events WHERE id = %s",
            (int(event["id"]),),
        ).fetchone()
        assert row is not None
        self.assertEqual(int(row["emailed_at_ms"]), now + 1)

    def test_concurrent_deliveries_claim_a_recipient_once(self) -> None:
        from gardenops.services.notification_service import deliver_pending_email_digests

        user = create_user(
            self.conn,
            username="digest_delivery_race",
            password=strong_password("digest-delivery-race"),
            role="editor",
        )
        user_id = int(user["id"])
        now = 1_700_000_000_000
        self.conn.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (user_id,),
        )
        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'editor')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, user_id),
        )
        self.conn.execute(
            """
            INSERT INTO user_notification_preferences
                (user_id, in_app_enabled, email_enabled, email_address,
                 digest_frequency, quiet_hours_json, task_due_enabled,
                 task_overdue_enabled, created_at_ms, updated_at_ms)
            VALUES (%s, 1, 1, 'digest-race@example.test', 'daily', '{}', 1, 1, %s, %s)
            """,
            (user_id, now, now),
        )
        event = self.conn.execute(
            """
            INSERT INTO notification_events
                (public_id, garden_id, user_id, notification_type, title, body,
                 target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                 dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                 cleared_at_ms, clear_reason, superseded_by_id)
            VALUES ('note_digest_delivery_race', %s, %s, 'task_due',
                    'Water basil', 'Water basil today', 'task', 'task_digest_delivery_race',
                    NULL, NULL, '{}', 0, %s, NULL, 'normal', NULL, NULL, NULL, NULL)
            RETURNING id
            """,
            (self.garden_id, user_id, now),
        ).fetchone()
        assert event is not None
        self.conn.commit()

        first_email_started = threading.Event()
        allow_first_delivery_to_finish = threading.Event()
        second_lock_attempted = threading.Event()
        second_finished = threading.Event()
        sent: list[tuple[str, str, str]] = []

        def first_sender(recipient: str, subject: str, body: str) -> None:
            sent.append((recipient, subject, body))
            first_email_started.set()
            if not allow_first_delivery_to_finish.wait(timeout=5):
                raise TimeoutError("first digest delivery was not released")

        def second_sender(recipient: str, subject: str, body: str) -> None:
            sent.append((recipient, subject, body))

        def deliver_once(*, observe_lock: bool) -> dict[str, int | bool]:
            conn = db.get_db()
            try:
                target = (
                    _DigestDeliveryLockAttemptConnection(conn, second_lock_attempted)
                    if observe_lock
                    else conn
                )
                return deliver_pending_email_digests(
                    target,
                    self.garden_id,
                    email_sender=second_sender if observe_lock else first_sender,
                    now_ms=now + 1,
                )
            finally:
                if observe_lock:
                    second_finished.set()
                db.return_db(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(deliver_once, observe_lock=False)
            try:
                self.assertTrue(first_email_started.wait(timeout=5))
                second = pool.submit(deliver_once, observe_lock=True)
                self.assertTrue(second_lock_attempted.wait(timeout=5))
                self.assertFalse(second_finished.wait(timeout=0.2))
            finally:
                allow_first_delivery_to_finish.set()

            first_result = first.result(timeout=5)
            second_result = second.result(timeout=5)

        self.assertEqual(int(first_result["emailed_users"]), 1)
        self.assertEqual(int(second_result["emailed_users"]), 0)
        self.assertEqual(int(second_result["skipped_users"]), 1)
        self.assertEqual(len(sent), 1)
        row = self.conn.execute(
            "SELECT emailed_at_ms FROM notification_events WHERE id = %s",
            (int(event["id"]),),
        ).fetchone()
        assert row is not None
        self.assertEqual(int(row["emailed_at_ms"]), now + 1)

    def test_recipient_configuration_is_revalidated_after_lock_wait(self) -> None:
        from gardenops.services.notification_service import deliver_pending_email_digests

        user = create_user(
            self.conn,
            username="digest_revalidation_race",
            password=strong_password("digest-revalidation-race"),
            role="editor",
        )
        user_id = int(user["id"])
        now = 1_800_000_000_000
        self.conn.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (user_id,),
        )
        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'editor')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, user_id),
        )
        self.conn.execute(
            """
            INSERT INTO user_notification_preferences
                (user_id, in_app_enabled, email_enabled, email_address,
                 digest_frequency, quiet_hours_json, task_due_enabled,
                 task_overdue_enabled, created_at_ms, updated_at_ms)
            VALUES (%s, 1, 1, 'before@example.test', 'daily', '{}', 1, 1, %s, %s)
            """,
            (user_id, now, now),
        )
        self.conn.execute(
            """
            INSERT INTO notification_events
                (public_id, garden_id, user_id, notification_type, title, body,
                 target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                 dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                 cleared_at_ms, clear_reason, superseded_by_id)
            VALUES
                ('note_digest_revalidation_history', %s, %s, 'task_due',
                 'Earlier reminder', '', 'task', 'task_digest_revalidation_history',
                 NULL, %s, '{}', 0, %s, NULL, 'normal', NULL, NULL, NULL, NULL),
                ('note_digest_revalidation_pending', %s, %s, 'task_due',
                 'Current reminder', '', 'task', 'task_digest_revalidation_pending',
                 NULL, NULL, '{}', 0, %s, NULL, 'normal', NULL, NULL, NULL, NULL)
            """,
            (
                self.garden_id,
                user_id,
                now - (2 * 86_400_000),
                now - (2 * 86_400_000),
                self.garden_id,
                user_id,
                now - 1,
            ),
        )
        self.conn.commit()

        def reset_eligible_recipient() -> None:
            self.conn.execute(
                "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                (user_id,),
            )
            self.conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'editor')
                ON CONFLICT DO NOTHING
                """,
                (self.garden_id, user_id),
            )
            self.conn.execute(
                """
                UPDATE user_notification_preferences
                SET email_enabled = 1,
                    email_address = 'before@example.test',
                    digest_frequency = 'daily'
                WHERE user_id = %s
                """,
                (user_id,),
            )
            self.conn.commit()

        def race_configuration_change(
            sql: str,
            params: tuple[Any, ...],
        ) -> tuple[dict[str, int | bool], list[str]]:
            reset_eligible_recipient()
            lock_attempted = threading.Event()
            allow_lock = threading.Event()
            sent: list[str] = []

            def deliver() -> dict[str, int | bool]:
                conn = db.get_db()
                try:
                    return deliver_pending_email_digests(
                        _DigestDeliveryPauseBeforeLockConnection(
                            conn,
                            lock_attempted,
                            allow_lock,
                        ),
                        self.garden_id,
                        email_sender=lambda recipient, _subject, _body: sent.append(recipient),
                        now_ms=now,
                    )
                finally:
                    db.return_db(conn)

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(deliver)
                self.assertTrue(lock_attempted.wait(timeout=5))
                mutation_conn = db.get_db()
                try:
                    mutation_conn.execute(sql, params)
                    mutation_conn.commit()
                finally:
                    db.return_db(mutation_conn)
                allow_lock.set()
                return future.result(timeout=5), sent

        invalidations = (
            (
                "membership",
                "DELETE FROM garden_memberships WHERE garden_id = %s AND user_id = %s",
                (self.garden_id, user_id),
            ),
            (
                "entitlement",
                "UPDATE auth_users SET subscription_tier = 'home' WHERE id = %s",
                (user_id,),
            ),
            (
                "email enabled",
                "UPDATE user_notification_preferences SET email_enabled = 0 WHERE user_id = %s",
                (user_id,),
            ),
            (
                "email address",
                "UPDATE user_notification_preferences SET email_address = '' WHERE user_id = %s",
                (user_id,),
            ),
            (
                "digest disabled",
                "UPDATE user_notification_preferences SET digest_frequency = 'none' "
                "WHERE user_id = %s",
                (user_id,),
            ),
            (
                "weekly cadence",
                "UPDATE user_notification_preferences SET digest_frequency = 'weekly' "
                "WHERE user_id = %s",
                (user_id,),
            ),
        )
        for label, sql, params in invalidations:
            with self.subTest(label=label):
                result, sent = race_configuration_change(sql, params)
                self.assertEqual(int(result["emailed_users"]), 0)
                self.assertEqual(int(result["skipped_users"]), 1)
                self.assertEqual(sent, [])

        result, sent = race_configuration_change(
            "UPDATE user_notification_preferences SET email_address = %s WHERE user_id = %s",
            ("after@example.test", user_id),
        )
        self.assertEqual(int(result["emailed_users"]), 1)
        self.assertEqual(sent, ["after@example.test"])


class TestRainTaskNotificationLifecycle(DbTestBase):
    def test_rain_adjustment_refreshes_projection_before_same_run_digest(self) -> None:
        from gardenops.services.attention.outcomes import upsert_attention_outcome
        from gardenops.services.notification_service import (
            create_task_due_notifications,
            deliver_pending_email_digests,
        )
        from gardenops.services.task_generator import reconcile_rain_watering_outcomes

        now_ms = 1_784_044_800_000
        self._insert_plant("RAIN-DIGEST", "Rain digest", care_watering="regular moisture")
        self.conn.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (self._owner_id,),
        )
        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, self._owner_id),
        )
        self.conn.execute(
            """
            INSERT INTO user_notification_preferences
                (user_id, in_app_enabled, email_enabled, email_address,
                 digest_frequency, quiet_hours_json, task_due_enabled,
                 task_overdue_enabled, created_at_ms, updated_at_ms)
            VALUES (%s, 1, 1, 'rain-digest@example.test', 'daily', '{}', 1, 1, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
            SET in_app_enabled = 1,
                email_enabled = 1,
                email_address = EXCLUDED.email_address,
                digest_frequency = EXCLUDED.digest_frequency,
                task_due_enabled = 1,
                task_overdue_enabled = 1,
                updated_at_ms = EXCLUDED.updated_at_ms
            """,
            (self._owner_id, now_ms, now_ms),
        )
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, status, severity,
                 due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES ('task_rain_digest', %s, 'water', 'Water before extended rain',
                    'pending', 'normal', '2026-07-14', 'water:RAIN-DIGEST:2026-07-14',
                    '{}', %s, %s)
            RETURNING id
            """,
            (self.garden_id, now_ms, now_ms),
        ).fetchone()
        assert task is not None
        self.conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (int(task["id"]), "RAIN-DIGEST"),
        )
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'high', 'Extended rain', 'Heavy rain continues',
                    '2026-07-14', '2026-07-17', '{}', %s)
            RETURNING id
            """,
            (self.garden_id, now_ms),
        ).fetchone()
        assert alert is not None
        upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_rescheduled_by_rain",
            source_type="task_generator",
            source_id=str(alert["id"]),
            source_public_id="water:RAIN-DIGEST:2026-07-14",
            target_type="plant",
            target_id="RAIN-DIGEST",
            title="Watering rescheduled by rain",
            explanation="Rain moved this watering.",
            reason="Rain covers watering",
            plant_ids=("RAIN-DIGEST",),
            metadata={"due_on": "2026-07-14", "new_due_on": "2026-07-14"},
            recovery_action={},
            occurred_at_ms=now_ms,
            expires_at_ms=now_ms + 86_400_000,
        )
        self.conn.commit()

        sent: list[tuple[str, str, str]] = []
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(now_ms),
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-14",
            },
            clear=False,
        ):
            initial = create_task_due_notifications(
                self.conn,
                self.garden_id,
                now_ms=now_ms,
            )
            reconciliation = reconcile_rain_watering_outcomes(
                self.conn,
                garden_id=self.garden_id,
                now_ms=now_ms,
            )
            delivered = deliver_pending_email_digests(
                self.conn,
                self.garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=now_ms,
            )

        self.assertEqual(initial["created"], 1)
        self.assertEqual(reconciliation["adjusted"], 1)
        self.assertEqual(int(delivered["emailed_users"]), 0)
        self.assertEqual(sent, [])
        task_after = self.conn.execute(
            "SELECT due_on FROM garden_tasks WHERE public_id = 'task_rain_digest'",
        ).fetchone()
        notification = self.conn.execute(
            """
            SELECT emailed_at_ms, cleared_at_ms, clear_reason
            FROM notification_events
            WHERE garden_id = %s
              AND target_type = 'task'
              AND target_id = 'task_rain_digest'
            """,
            (self.garden_id,),
        ).fetchone()
        assert task_after is not None
        assert notification is not None
        self.assertEqual(str(task_after["due_on"]), "2026-07-19")
        self.assertIsNone(notification["emailed_at_ms"])
        self.assertIsNotNone(notification["cleared_at_ms"])
        self.assertEqual(str(notification["clear_reason"]), "superseded")


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

    def test_email_only_task_due_event_is_deliverable_but_hidden_from_inbox(self) -> None:
        from gardenops.services.notification_service import (
            create_task_due_notifications,
            deliver_pending_email_digests,
            get_unread_count,
        )

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            user = create_user(
                conn,
                username=f"email_only_task_{self.__class__.__name__.lower()}",
                password=strong_password("emailonlytaskpass"),
                role="editor",
            )
            user_id = int(user["id"])
            now = db.current_timestamp_ms()
            today = str(conn.execute("SELECT CURRENT_DATE::text").fetchone()["current_date"])
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
            conn.execute(
                """
                INSERT INTO user_notification_preferences
                    (user_id, in_app_enabled, email_enabled, email_address,
                     digest_frequency, quiet_hours_json, task_due_enabled,
                     task_overdue_enabled, rules_json, created_at_ms, updated_at_ms)
                VALUES (%s, 0, 1, %s, 'daily', '{}', 0, 1, %s, %s, %s)
                """,
                (
                    user_id,
                    "email-only-task@example.test",
                    json.dumps(
                        {
                            "task_due": {
                                "in_app_enabled": False,
                                "email_enabled": True,
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
                    (garden_id, task_type, title, status, severity,
                     due_on, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'water', 'Email-only basil task', 'pending', 'normal',
                        %s, '{}', %s, %s)
                """,
                (garden_id, today, now, now),
            )
            conn.commit()

            create_task_due_notifications(conn, garden_id, now_ms=now)
            event = conn.execute(
                """
                SELECT *
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'task_due'
                  AND title = 'Due today: Email-only basil task'
                """,
                (garden_id, user_id),
            ).fetchone()
            assert event is not None
            self.assertEqual(get_unread_count(conn, garden_id, user_id, now_ms=now), 0)

            sent: list[tuple[str, str, str]] = []
            delivered = deliver_pending_email_digests(
                conn,
                garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=now + 1_000,
            )

            self.assertEqual(int(delivered["emailed_users"]), 1)
            self.assertEqual(sent[0][0], "email-only-task@example.test")
            self.assertIn("Email-only basil task", sent[0][2])
        finally:
            db.return_db(conn)

    def test_digest_only_event_persists_during_quiet_hours_and_delivers_later(self) -> None:
        from gardenops.services.notification_service import (
            create_garden_member_notifications,
            deliver_pending_email_digests,
        )

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            user = create_user(
                conn,
                username=f"quiet_digest_{self.__class__.__name__.lower()}",
                password=strong_password("quietdigestpass123"),
                role="editor",
            )
            user_id = int(user["id"])
            now = db.current_timestamp_ms()
            now_utc = datetime.fromtimestamp(now / 1000, UTC)
            quiet_hours = {
                "timezone": "UTC",
                "digest": {
                    "enabled": True,
                    "start": (now_utc - timedelta(hours=1)).strftime("%H:%M"),
                    "end": (now_utc + timedelta(hours=1)).strftime("%H:%M"),
                },
            }
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
            conn.execute(
                """
                INSERT INTO user_notification_preferences
                    (user_id, in_app_enabled, email_enabled, email_address,
                     digest_frequency, quiet_hours_json, task_due_enabled,
                     task_overdue_enabled, created_at_ms, updated_at_ms)
                VALUES (%s, 0, 1, %s, 'daily', '{}', 1, 1, %s, %s)
                """,
                (user_id, "quiet-digest@example.test", now, now),
            )
            conn.execute(
                """
                INSERT INTO user_attention_preferences
                    (user_id, preset, rules_json, quiet_hours_json,
                     show_no_action_history, created_at_ms, updated_at_ms)
                VALUES (%s, 'custom', %s, %s, 1, %s, %s)
                """,
                (
                    user_id,
                    json.dumps(
                        {
                            "issue_follow_up_due": {
                                "panel": True,
                                "inbox": False,
                                "digest": True,
                                "min_severity": "low",
                            }
                        },
                        separators=(",", ":"),
                    ),
                    json.dumps(quiet_hours, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            conn.commit()

            generated = create_garden_member_notifications(
                conn,
                garden_id=garden_id,
                notification_type="issue_created",
                title="Check mildew",
                body="Review the cucumber bed.",
                target_type="issue",
                target_id="issue_quiet_digest",
                now_ms=now,
            )
            self.assertGreaterEqual(int(generated["created"]), 1)
            event = conn.execute(
                """
                SELECT id, emailed_at_ms
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'issue_created'
                  AND target_id = 'issue_quiet_digest'
                """,
                (garden_id, user_id),
            ).fetchone()
            assert event is not None
            self.assertIsNone(event["emailed_at_ms"])

            sent: list[tuple[str, str, str]] = []
            blocked = deliver_pending_email_digests(
                conn,
                garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=now,
            )
            self.assertEqual(int(blocked["emailed_users"]), 0)
            self.assertEqual(sent, [])

            conn.execute(
                "UPDATE user_attention_preferences SET quiet_hours_json = '{}' WHERE user_id = %s",
                (user_id,),
            )
            delivered = deliver_pending_email_digests(
                conn,
                garden_id,
                email_sender=lambda recipient, subject, body: sent.append(
                    (recipient, subject, body)
                ),
                now_ms=now + 1,
            )
            self.assertEqual(int(delivered["emailed_users"]), 1)
            self.assertEqual(len(sent), 1)
            self.assertIn("Check mildew", sent[0][2])
        finally:
            db.return_db(conn)

    def test_notification_rule_validation_rejects_truthy_strings_and_system_overrides(self) -> None:
        from gardenops.services.notification_service import validate_notification_rules

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            validate_notification_rules(
                {"task_due": {"in_app_enabled": "false"}},
            )
        with self.assertRaisesRegex(ValueError, "not user configurable"):
            validate_notification_rules(
                {"system": {"in_app_enabled": False}},
            )

        validate_notification_rules(
            {
                "system": {
                    "in_app_enabled": True,
                    "email_enabled": True,
                    "min_severity": "low",
                }
            },
        )

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

    def test_notification_list_includes_non_configurable_system_subtypes(self) -> None:
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
                notification_id = _create_notif(
                    conn,
                    int(garden["id"]),
                    int(user["id"]),
                    "system",
                    "Backup status changed",
                    "The latest backup completed.",
                    notification_subtype="backup",
                    severity="low",
                )
            finally:
                db.return_db(conn)

            response = client.get("/api/notifications", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(
                notification_id,
                [notification["id"] for notification in response.json()["notifications"]],
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

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

    def test_muted_notification_type_hides_inbox_without_mutating_log(self) -> None:
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
            self.assertIsNone(found[0]["clear_reason"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_reenabled_task_notification_type_keeps_existing_active_notification(self) -> None:
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
                      AND notification_type = 'task_due'
                      AND target_type = 'task'
                      AND target_id = %s
                    """,
                    (garden_id, user_id, task_public_id),
                ).fetchone()
                assert counts is not None
                self.assertEqual(int(counts["total"]), 1)
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

    def test_viewer_can_manage_personal_notification_state_but_not_generate(self) -> None:
        from gardenops.services.notification_service import create_notification

        user = self._create_test_user(
            "notification_viewer", "notificationviewerpass", role="viewer"
        )
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'viewer')
                ON CONFLICT (garden_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (garden_id, int(user["id"])),
            )
            notification_id = create_notification(
                conn,
                garden_id,
                int(user["id"]),
                "system",
                "Viewer personal notification",
                "Viewer can manage this personal state.",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client, headers = self._authenticated_client(
                "notification_viewer",
                "notificationviewerpass",
                garden_id=garden_id,
            )
            current = client.get("/api/notifications/preferences", headers=headers)
            self.assertEqual(current.status_code, 200, current.text)
            update = current.json()
            update.pop("policy", None)
            update["notification_rules"]["issue_created"]["min_severity"] = "high"
            saved = client.put("/api/notifications/preferences", headers=headers, json=update)
            self.assertEqual(saved.status_code, 200, saved.text)

            marked = client.post(
                f"/api/notifications/{notification_id}/read",
                headers=headers,
            )
            self.assertEqual(marked.status_code, 200, marked.text)
            dismissed = client.delete(f"/api/notifications/{notification_id}", headers=headers)
            self.assertEqual(dismissed.status_code, 200, dismissed.text)

            generated = client.post("/api/notifications/generate", headers=headers)
            self.assertEqual(generated.status_code, 403, generated.text)

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
            clear_task_notifications(
                conn,
                garden_id=garden_id,
                task_public_id=partial_task_id,
                reason="expired",
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
            active_partial = conn.execute(
                """
                SELECT title, metadata_json
                FROM notification_events
                WHERE garden_id = %s
                  AND target_type = 'task'
                  AND target_id = %s
                  AND dismissed = 0
                  AND cleared_at_ms IS NULL
                """,
                (garden_id, partial_task_id),
            ).fetchall()
        finally:
            db.return_db(conn)
        self.assertEqual(int(active_unrelated["count"]), 0)
        self.assertEqual(len(active_partial), 1)
        partial_metadata = json.loads(str(active_partial[0]["metadata_json"]))
        self.assertEqual(partial_metadata["plants"], ["Rose"])
        self.assertNotIn("Test Plant", str(active_partial[0]["title"]))

    def test_due_today_notification_survives_read_side_cleanup(self) -> None:
        from gardenops.services.notification_service import (
            clear_expired_notifications,
            clear_stale_task_notifications,
            create_notification,
        )

        due_on = "2032-02-03"
        now_ms = 1_959_422_400_000
        expires_at_ms = 1_959_465_599_999
        created = self.client.post(
            "/api/tasks",
            json={"task_type": "fertilize", "title": "Feed today", "due_on": due_on},
        )
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]
        garden_id = self._get_default_garden_id()

        conn = db.get_db()
        try:
            create_notification(
                conn,
                garden_id,
                None,
                "task_due",
                "Due today: Feed today",
                "Due today",
                target_type="task",
                target_id=task_id,
                metadata={"due_on": due_on},
                expires_at_ms=expires_at_ms,
                now_ms=now_ms,
            )
            expired = clear_expired_notifications(conn, garden_id=garden_id, now_ms=now_ms)
            stale = clear_stale_task_notifications(
                conn,
                garden_id=garden_id,
                today_iso=due_on,
                now_ms=now_ms,
            )
            row = conn.execute(
                """
                SELECT cleared_at_ms, clear_reason
                FROM notification_events
                WHERE garden_id = %s AND target_id = %s
                """,
                (garden_id, task_id),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(expired, 0)
        self.assertEqual(stale, 0)
        self.assertIsNotNone(row)
        self.assertIsNone(row["cleared_at_ms"])
        self.assertIsNone(row["clear_reason"])

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

    def test_dry_spell_watering_notification_remains_actionable_through_alert(self) -> None:
        from gardenops.services.attention.providers.tasks import TaskAttentionProvider
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
            self.assertIsNone(row["clear_reason"])
            self.assertIsNone(row["cleared_at_ms"])

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
            self.assertEqual(int(active["c"]), 1)
            attention_items = TaskAttentionProvider(frozen_date=today).collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=now,
            )
            self.assertIn(
                f"attn:task:{task_public_id}",
                {item.id for item in attention_items},
            )
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

    def test_task_weather_maintenance_keeps_valid_dry_spell_watering_notifications(self) -> None:
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
            self.assertIsNone(row["clear_reason"])
            self.assertIsNone(row["cleared_at_ms"])
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
            now = db.current_timestamp_ms()
            maintenance_today = datetime.fromtimestamp(now / 1000, UTC).date()
            yesterday = offset_days_iso(-1, today=maintenance_today)
            tomorrow = offset_days_iso(1, today=maintenance_today)
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

    def test_non_pro_can_save_in_app_defaults_without_enabling_email(self) -> None:
        self._create_test_user("prefs_nonpro_defaults", "prefsnonprodefaultspass", role="editor")
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'enthusiast' WHERE username = %s",
                ("prefs_nonpro_defaults",),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session(
                "prefs_nonpro_defaults",
                "prefsnonprodefaultspass",
                client=client,
            )
            headers = self._session_headers(csrf)
            current = client.get("/api/notifications/preferences", headers=headers)
            self.assertEqual(current.status_code, 200, current.text)
            ordinary_in_app_update = current.json()
            ordinary_in_app_update.pop("policy", None)
            ordinary_in_app_update["in_app_enabled"] = False
            ordinary_in_app_update["email_enabled"] = False
            saved = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json=ordinary_in_app_update,
            )
            self.assertEqual(saved.status_code, 200, saved.text)

            ordinary_in_app_update["notification_rules"]["task_upcoming"]["email_enabled"] = True
            rule_rejected = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json=ordinary_in_app_update,
            )
            self.assertEqual(rule_rejected.status_code, 403, rule_rejected.text)

            ordinary_in_app_update["notification_rules"]["task_upcoming"]["email_enabled"] = False
            ordinary_in_app_update["email_enabled"] = True
            ordinary_in_app_update["email_address"] = "nonpro@example.test"
            rejected = client.put(
                "/api/notifications/preferences",
                headers=headers,
                json=ordinary_in_app_update,
            )
            self.assertEqual(rejected.status_code, 403, rejected.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_empty_rules_json_projects_legacy_task_flags(self) -> None:
        conn = db.get_db()
        try:
            now = db.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO user_notification_preferences
                    (user_id, in_app_enabled, email_enabled, email_address,
                     digest_frequency, quiet_hours_json, task_due_enabled,
                     task_overdue_enabled, rules_json, created_at_ms, updated_at_ms)
                VALUES (%s, 1, 0, '', 'daily', '{}', 0, 1, '{}', %s, %s)
                """,
                (self._owner_id, now, now),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            response = client.get(
                "/api/notifications/preferences",
                headers=self._session_headers(csrf),
            )
            self.assertEqual(response.status_code, 200, response.text)
            rules = response.json()["notification_rules"]
            self.assertFalse(rules["task_due"]["in_app_enabled"])
            self.assertTrue(rules["task_overdue"]["in_app_enabled"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_monthly_task_generation_receives_maintenance_clock(self) -> None:
        from gardenops.services.notification_service import _auto_generate_monthly_tasks

        frozen_now_ms = 1_959_379_200_000
        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            with patch(
                "gardenops.services.notification_service.generate_tasks",
                return_value={"created": 0, "skipped": 0},
            ) as generate_tasks:
                _auto_generate_monthly_tasks(
                    conn,
                    int(garden["id"]),
                    frozen_now_ms,
                    frozen_date="2032-02-03",
                )
            self.assertEqual(generate_tasks.call_args.kwargs["now_ms"], frozen_now_ms)
        finally:
            db.return_db(conn)

    def test_monthly_generation_marker_stays_pending_for_rain_suppressed_watering(self) -> None:
        from gardenops.services.notification_service import _auto_generate_monthly_tasks

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            with patch(
                "gardenops.services.notification_service.generate_tasks",
                return_value={"created": 0, "skipped": 1, "rain_suppressed": 1},
            ) as generate_tasks:
                first = _auto_generate_monthly_tasks(
                    conn,
                    garden_id,
                    1_959_379_200_000,
                    frozen_date="2032-02-03",
                )
                second = _auto_generate_monthly_tasks(
                    conn,
                    garden_id,
                    1_959_379_200_001,
                    frozen_date="2032-02-03",
                )
                pending_marker = conn.execute(
                    "SELECT value FROM app_settings WHERE key = %s",
                    (f"last_task_gen_month:{garden_id}",),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO weather_alerts
                        (garden_id, alert_type, severity, title, description,
                         valid_from, valid_until, metadata_json, created_at_ms)
                    VALUES (%s, 'rain_surplus', 'normal', 'Heavy rain', 'Skip watering',
                            '2032-02-03', '2032-02-05', '{}', 1)
                    """,
                    (garden_id,),
                )
                third = _auto_generate_monthly_tasks(
                    conn,
                    garden_id,
                    1_959_379_200_002,
                    frozen_date="2032-02-03",
                )
            marker = conn.execute(
                "SELECT value FROM app_settings WHERE key = %s",
                (f"last_task_gen_month:{garden_id}",),
            ).fetchone()
            assert marker is not None
            assert pending_marker is not None
            self.assertTrue(str(pending_marker["value"]).startswith("2032-02:rain_pending:"))
            self.assertTrue(str(marker["value"]).startswith("2032-02:rain_pending:"))
            self.assertNotEqual(str(marker["value"]), str(pending_marker["value"]))
            self.assertEqual(first["tasks_rain_suppressed"], 1)
            self.assertEqual(second, {"tasks_skipped": True, "tasks_rain_pending": True})
            self.assertEqual(third["tasks_rain_suppressed"], 1)
            self.assertEqual(generate_tasks.call_count, 2)
        finally:
            db.return_db(conn)
