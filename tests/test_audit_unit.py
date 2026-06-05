"""Unit tests for gardenops.audit."""

import unittest
from unittest.mock import MagicMock, patch

import gardenops.db as db
from gardenops.audit import list_audit_events, write_audit_event
from gardenops.security import AuthContext
from tests.base import strong_password


def _truncate_all() -> None:
    conn = db.get_db()
    try:
        rows = conn.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename != 'schema_migrations'
            """
        ).fetchall()
        tables = [row["tablename"] for row in rows]
        if tables:
            conn.execute("TRUNCATE {} CASCADE".format(", ".join(tables)))
        conn.commit()
    finally:
        db.return_db(conn)


class TestWriteAuditEvent(unittest.TestCase):
    def setUp(self) -> None:
        _truncate_all()
        conn = db.get_db()
        try:
            db.ensure_default_garden(conn)
            conn.commit()
        finally:
            db.return_db(conn)
        self._default_garden_id = self._lookup_default_garden_id()
        self._test_user_id = self._create_test_user()

    def _lookup_default_garden_id(self) -> int:
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert row is not None
            return int(row["id"])
        finally:
            db.return_db(conn)

    def _create_test_user(self) -> int:
        from gardenops.security import create_user

        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="audittester",
                password=strong_password("testpassword123"),
                role="admin",
            )
            conn.commit()
            return int(user["id"])
        finally:
            db.return_db(conn)

    def _count_events(self) -> int:
        conn = db.get_db()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()
            return int(row["c"])
        finally:
            db.return_db(conn)

    @patch("gardenops.audit.enqueue_security_telemetry")
    def test_writes_anonymous_event(self, mock_telemetry: MagicMock) -> None:
        write_audit_event(
            method="GET",
            path="/api/plots",
            status_code=200,
            remote_host="127.0.0.1",
        )
        assert self._count_events() == 1

        conn = db.get_db()
        try:
            row = conn.execute("SELECT * FROM audit_events LIMIT 1").fetchone()
            assert row["method"] == "GET"
            assert row["path"] == "/api/plots"
            assert row["status_code"] == 200
            assert row["actor_username"] == "anonymous"
            assert row["actor_role"] == "anonymous"
            assert row["actor_auth_type"] == "none"
        finally:
            db.return_db(conn)

    @patch("gardenops.audit.enqueue_security_telemetry")
    def test_writes_authenticated_event(self, mock_telemetry: MagicMock) -> None:
        ctx = AuthContext(
            user_id=self._test_user_id,
            username="gardener",
            role="editor",
            auth_type="session",
            garden_id=self._default_garden_id,
        )
        write_audit_event(
            method="POST",
            path="/api/plots/B1/plants",
            status_code=201,
            remote_host="192.168.1.5",
            detail="  Added plant  ",
            auth_context=ctx,
        )
        conn = db.get_db()
        try:
            row = conn.execute("SELECT * FROM audit_events LIMIT 1").fetchone()
            assert row["actor_user_id"] == self._test_user_id
            assert row["actor_username"] == "gardener"
            assert row["actor_role"] == "editor"
            assert row["actor_auth_type"] == "session"
            assert row["garden_id"] == self._default_garden_id
            assert row["detail"] == "Added plant"
        finally:
            db.return_db(conn)

    @patch("gardenops.audit.enqueue_security_telemetry")
    def test_garden_id_from_auth_context(self, mock_telemetry: MagicMock) -> None:
        ctx = AuthContext(
            user_id=None,
            username="admin",
            role="admin",
            auth_type="session",
            garden_id=self._default_garden_id,
        )
        write_audit_event(
            method="GET",
            path="/api/tasks",
            status_code=200,
            remote_host="127.0.0.1",
            auth_context=ctx,
        )
        conn = db.get_db()
        try:
            row = conn.execute("SELECT * FROM audit_events LIMIT 1").fetchone()
            assert row is not None
            assert row["garden_id"] == self._default_garden_id
        finally:
            db.return_db(conn)

    @patch("gardenops.audit.enqueue_security_telemetry")
    def test_explicit_garden_id_overrides_context(self, mock_telemetry: MagicMock) -> None:
        ctx = AuthContext(
            user_id=None,
            username="admin",
            role="admin",
            auth_type="session",
            garden_id=None,
        )
        write_audit_event(
            method="GET",
            path="/api/tasks",
            status_code=200,
            remote_host="127.0.0.1",
            auth_context=ctx,
            garden_id=self._default_garden_id,
        )
        conn = db.get_db()
        try:
            row = conn.execute("SELECT * FROM audit_events LIMIT 1").fetchone()
            assert row is not None
            assert row["garden_id"] == self._default_garden_id
        finally:
            db.return_db(conn)

    def test_exception_suppressed_on_db_error(self) -> None:
        with (
            patch("gardenops.audit.get_db") as mock_get_db,
            patch("gardenops.audit.return_db"),
        ):
            mock_conn = MagicMock()
            mock_conn.execute.side_effect = Exception("DB error")
            mock_get_db.return_value = mock_conn
            write_audit_event(
                method="GET",
                path="/api/plots",
                status_code=200,
                remote_host="127.0.0.1",
            )

    @patch("gardenops.audit.enqueue_security_telemetry")
    def test_telemetry_called(self, mock_telemetry: MagicMock) -> None:
        write_audit_event(
            method="DELETE",
            path="/api/plants/P1",
            status_code=204,
            remote_host="10.0.0.1",
        )
        mock_telemetry.assert_called_once()
        call_args = mock_telemetry.call_args
        assert call_args[0][0] == "audit_event"
        payload = call_args[0][1]
        assert payload["method"] == "DELETE"
        assert payload["path"] == "/api/plants/P1"


class TestListAuditEvents(unittest.TestCase):
    def setUp(self) -> None:
        _truncate_all()
        conn = db.get_db()
        try:
            db.ensure_default_garden(conn)
            conn.commit()
        finally:
            db.return_db(conn)
        self.conn = db.get_db()
        self._seed_events()

    def tearDown(self) -> None:
        db.return_db(self.conn)

    def _seed_events(self) -> None:
        gid_row = self.conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert gid_row is not None
        gid = int(gid_row["id"])

        t = 1700000000000
        events = [
            (
                t,
                None,
                "anonymous",
                "anonymous",
                "none",
                None,
                "GET",
                "/api/plots",
                200,
                "127.0.0.1",
                "",
            ),
            (
                t + 1000,
                None,
                "admin",
                "admin",
                "session",
                gid,
                "POST",
                "/api/plots/B1/plants",
                201,
                "127.0.0.1",
                "added plant",
            ),
            (
                t + 2000,
                None,
                "editor",
                "editor",
                "session",
                gid,
                "DELETE",
                "/api/plants/P1",
                204,
                "10.0.0.1",
                "removed",
            ),
            (
                t + 3000,
                None,
                "admin",
                "admin",
                "api_key",
                None,
                "GET",
                "/api/tasks",
                200,
                "127.0.0.1",
                "",
            ),
            (
                t + 4000,
                None,
                "anonymous",
                "anonymous",
                "none",
                None,
                "GET",
                "/api/weather",
                401,
                "192.168.1.1",
                "unauthorized",
            ),
        ]
        for ev in events:
            self.conn.execute(
                """INSERT INTO audit_events
                   (occurred_at_ms, actor_user_id, actor_username, actor_role,
                    actor_auth_type, garden_id, method, path, status_code,
                    remote_host, detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                ev,
            )
        self.conn.commit()
        self._gid = gid

    def test_returns_all_events(self) -> None:
        result = list_audit_events(self.conn)
        assert result["total"] == 5
        assert len(result["events"]) == 5

    def test_filter_by_garden_id(self) -> None:
        result = list_audit_events(self.conn, garden_id=self._gid)
        assert result["total"] == 2

    def test_filter_by_actor(self) -> None:
        result = list_audit_events(self.conn, actor="admin")
        assert result["total"] == 2

    def test_filter_by_path_prefix(self) -> None:
        result = list_audit_events(self.conn, path_prefix="/api/plots")
        assert result["total"] == 2

    def test_filter_by_method(self) -> None:
        result = list_audit_events(self.conn, method="GET")
        assert result["total"] == 3

    def test_filter_by_status_code(self) -> None:
        result = list_audit_events(self.conn, status_code=401)
        assert result["total"] == 1

    def test_filter_by_time_range(self) -> None:
        base_ms = 1700000000000
        result = list_audit_events(
            self.conn,
            from_ms=base_ms + 1000,
            to_ms=base_ms + 3000,
        )
        assert result["total"] == 3

    def test_limit_and_offset(self) -> None:
        result = list_audit_events(self.conn, limit=2, offset=0)
        assert len(result["events"]) == 2
        assert result["total"] == 5
        assert result["limit"] == 2
        assert result["offset"] == 0

    def test_limit_clamped(self) -> None:
        result = list_audit_events(self.conn, limit=5000)
        assert result["limit"] == 1000

    def test_combined_filters(self) -> None:
        result = list_audit_events(
            self.conn,
            garden_id=self._gid,
            method="POST",
        )
        assert result["total"] == 1
        assert result["events"][0]["actor_username"] == "admin"

    def test_ordered_by_time_descending(self) -> None:
        result = list_audit_events(self.conn)
        times = [ev["occurred_at_ms"] for ev in result["events"]]
        assert times == sorted(times, reverse=True)


if __name__ == "__main__":
    unittest.main()
