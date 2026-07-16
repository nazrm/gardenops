"""Unit tests for gardenops.incident_controls — emergency read-only flag, TTL expiry."""

import gardenops.db as db
from gardenops.incident_controls import (
    get_emergency_read_only_status,
    get_runtime_flag,
    is_emergency_read_only,
    set_emergency_read_only,
    set_runtime_flag,
)
from tests.base import BaseApiTest


class TestSetAndGetEmergencyReadOnly(BaseApiTest):
    """set_emergency_read_only / get_emergency_read_only_status round-trip."""

    def test_enable_read_only(self) -> None:
        result = set_emergency_read_only(True)
        self.assertTrue(result["enabled"])

    def test_disable_read_only(self) -> None:
        set_emergency_read_only(True)
        result = set_emergency_read_only(False)
        self.assertFalse(result["enabled"])

    def test_default_is_disabled(self) -> None:
        conn = db.get_db()
        try:
            status = get_emergency_read_only_status(conn)
            self.assertFalse(status["enabled"])
        finally:
            db.return_db(conn)

    def test_enable_with_expiry(self) -> None:
        future_ms = db.current_timestamp_ms() + 60_000
        result = set_emergency_read_only(True, expires_at_ms=future_ms)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["expires_at_ms"], future_ms)

    def test_disable_clears_expiry(self) -> None:
        future_ms = db.current_timestamp_ms() + 60_000
        set_emergency_read_only(True, expires_at_ms=future_ms)
        result = set_emergency_read_only(False)
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["expires_at_ms"])


class TestTtlAutoExpiry(BaseApiTest):
    """TTL expiry is effective without a hidden status-read mutation."""

    def test_expired_flag_auto_disables(self) -> None:
        past_ms = db.current_timestamp_ms() - 1000
        conn = db.get_db()
        try:
            set_runtime_flag(conn, "emergency_read_only", "1")
            set_runtime_flag(
                conn,
                "emergency_read_only_expires_at_ms",
                str(past_ms),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            status = get_emergency_read_only_status(conn)
            self.assertFalse(status["enabled"])
            self.assertIsNone(status["expires_at_ms"])
            self.assertEqual(get_runtime_flag(conn, "emergency_read_only"), "1")
            self.assertEqual(
                get_runtime_flag(conn, "emergency_read_only_expires_at_ms"),
                str(past_ms),
            )
        finally:
            db.return_db(conn)

    def test_unexpired_flag_remains_enabled(self) -> None:
        future_ms = db.current_timestamp_ms() + 60_000
        conn = db.get_db()
        try:
            set_runtime_flag(conn, "emergency_read_only", "1")
            set_runtime_flag(
                conn,
                "emergency_read_only_expires_at_ms",
                str(future_ms),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            status = get_emergency_read_only_status(conn)
            self.assertTrue(status["enabled"])
            self.assertEqual(status["expires_at_ms"], future_ms)
        finally:
            db.return_db(conn)


class TestIsEmergencyReadOnly(BaseApiTest):
    """is_emergency_read_only helper — convenience wrapper."""

    def test_false_by_default(self) -> None:
        self.assertFalse(is_emergency_read_only())

    def test_true_when_enabled(self) -> None:
        set_emergency_read_only(True)
        self.assertTrue(is_emergency_read_only())

    def test_false_after_disable(self) -> None:
        set_emergency_read_only(True)
        set_emergency_read_only(False)
        self.assertFalse(is_emergency_read_only())

    def test_false_when_expired(self) -> None:
        past_ms = db.current_timestamp_ms() - 1000
        conn = db.get_db()
        try:
            set_runtime_flag(conn, "emergency_read_only", "1")
            set_runtime_flag(
                conn,
                "emergency_read_only_expires_at_ms",
                str(past_ms),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        self.assertFalse(is_emergency_read_only())
