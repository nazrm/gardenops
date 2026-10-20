from pathlib import Path

from gardenops.schema_signature import collect_schema_snapshot, missing_schema_parts
from tests.base import DbTestBase

ROOT = Path(__file__).resolve().parents[1]


class TestMCPMigration(DbTestBase):
    def test_upgrade_preserves_history_and_retires_pending_proposals(self) -> None:
        conn = self.conn
        try:
            conn.execute("DROP TABLE assistant_requests")
            for version in ("0034", "0035"):
                conn.execute(next((ROOT / "migrations").glob(f"{version}_*.sql")).read_text())
            for state in ("proposal", "applied"):
                conn.execute(
                    """
                    INSERT INTO assistant_requests (
                        public_id, garden_id, actor_user_id, source_room_id, source_event_id,
                        source_sender_id, request_kind, state, created_at_ms, updated_at_ms,
                        expires_at_ms
                    ) VALUES (%s, %s, %s, 'old-client', %s, 'old-actor', 'journal', %s, 1, 1, 2)
                    """,
                    (state, self.garden_id, self._owner_id, state, state),
                )
            conn.execute((ROOT / "migrations/0036_mcp_only_assistant.sql").read_text())
            conn.execute((ROOT / "migrations/0036_mcp_only_assistant.sql").read_text())
            rows = conn.execute(
                "SELECT public_id, state, source_channel, source_client_id, source_actor_id "
                "FROM assistant_requests ORDER BY public_id"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["state"] for row in rows], ["applied", "expired"])
            for row in rows:
                self.assertEqual(row["source_channel"], "mcp")
                self.assertEqual(row["source_client_id"], "old-client")
                self.assertEqual(row["source_actor_id"], "old-actor")
            self.assertEqual(missing_schema_parts(collect_schema_snapshot(conn)), [])
        finally:
            conn.rollback()
