"""Tests for integrity layer: health endpoints, FK enforcement, consistency."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg

import gardenops.db as db
from gardenops.schema_signature import (
    REQUIRED_COLUMN_DEFAULTS,
    REQUIRED_COLUMN_NULLABILITY,
    REQUIRED_COLUMN_TYPES,
    REQUIRED_COLUMNS,
    REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS,
    REQUIRED_CONSTRAINTS,
    REQUIRED_INDEX_DEFINITION_FRAGMENTS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SchemaSnapshot,
    bootstrap_schema_diagnostics_from_snapshot,
    collect_schema_snapshot,
    missing_schema_parts,
)


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


class MigrationGuardTests(unittest.TestCase):
    """Verify run_migrations is idempotent and does not crash on repeated runs."""

    @staticmethod
    def _complete_schema_snapshot() -> SchemaSnapshot:
        return SchemaSnapshot(
            tables=set(REQUIRED_TABLES),
            columns={table: set(columns) for table, columns in REQUIRED_COLUMNS.items()},
            indexes=set(REQUIRED_INDEXES),
            constraints=set(REQUIRED_CONSTRAINTS),
            column_nullability=dict(REQUIRED_COLUMN_NULLABILITY),
            column_types=dict(REQUIRED_COLUMN_TYPES),
            column_defaults=dict(REQUIRED_COLUMN_DEFAULTS),
            index_definitions={
                name: " ".join(fragments)
                for name, fragments in REQUIRED_INDEX_DEFINITION_FRAGMENTS.items()
            },
            constraint_definitions={
                name: " ".join(fragments)
                for name, fragments in REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS.items()
            },
        )

    @staticmethod
    def _remove_offline_operation_schema(snapshot: SchemaSnapshot) -> None:
        snapshot.tables.remove("offline_create_operations")
        snapshot.columns.pop("offline_create_operations", None)
        snapshot.indexes.difference_update(
            {name for name in snapshot.indexes if "offline_create_operations" in name}
        )
        snapshot.constraints.difference_update(
            {name for name in snapshot.constraints if "offline_create_operations" in name}
        )

    @staticmethod
    def _migration_0027_sql() -> str:
        return (
            Path(__file__).parents[1] / "migrations/0027_inventory_procurement_integrity.sql"
        ).read_text(encoding="utf-8")

    @staticmethod
    def _migration_0028_sql() -> str:
        return (
            Path(__file__).parents[1] / "migrations/0028_auth_session_device_metadata.sql"
        ).read_text(encoding="utf-8")

    def _assert_disposable_database(self, conn: db.DbConn) -> None:
        expected_marker = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "")
        expected_system_identifier = os.environ.get(
            "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", ""
        )
        self.assertTrue(expected_marker, "disposable database marker is required")
        self.assertTrue(
            expected_system_identifier,
            "disposable cluster system identifier is required",
        )
        identity = conn.execute(
            """
            SELECT current_database() AS database_name,
                   current_user AS role_name,
                   host(inet_server_addr()) AS server_address,
                   inet_server_port() AS server_port,
                   current_setting('gardenops.disposable_marker', true) AS marker,
                   system_identifier::text AS system_identifier
            FROM pg_control_system()
            """
        ).fetchone()
        database_name = str(identity["database_name"])
        self.assertTrue(
            database_name == "gardenops_test"
            or (
                database_name.startswith("gardenops_test_shard")
                and database_name.removeprefix("gardenops_test_shard").isdigit()
            ),
            f"unexpected disposable database name: {database_name}",
        )
        self.assertEqual(identity["role_name"], "gardenops_test_runner")
        self.assertEqual(identity["server_address"], "127.0.0.1")
        self.assertNotEqual(int(identity["server_port"]), 5432)
        self.assertEqual(identity["marker"], expected_marker)
        self.assertEqual(identity["system_identifier"], expected_system_identifier)

    def _restore_migration_0028_and_history(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(self._migration_0028_sql())
            conn.commit()
        finally:
            db.return_db(conn)
        db.run_migrations()

    @staticmethod
    def _remove_migration_0027_surface(conn: db.DbConn) -> None:
        conn.execute("""
            ALTER TABLE procurement_items
                DROP CONSTRAINT IF EXISTS ux_procurement_receipt_transaction,
                DROP CONSTRAINT IF EXISTS fk_procurement_receipt_inventory,
                DROP CONSTRAINT IF EXISTS fk_procurement_received_by_user,
                DROP CONSTRAINT IF EXISTS ck_procurement_receipt_provenance,
                DROP CONSTRAINT IF EXISTS ck_procurement_quantity_positive,
                DROP COLUMN IF EXISTS receipt_inventory_item_id,
                DROP COLUMN IF EXISTS receipt_inventory_transaction_id,
                DROP COLUMN IF EXISTS received_by_user_id,
                DROP COLUMN IF EXISTS received_at_ms;
            ALTER TABLE inventory_transactions
                DROP CONSTRAINT IF EXISTS ux_inventory_tx_id_item_garden,
                DROP CONSTRAINT IF EXISTS fk_inventory_tx_garden,
                DROP CONSTRAINT IF EXISTS fk_inventory_tx_item_garden,
                DROP CONSTRAINT IF EXISTS ck_inventory_tx_delta_nonzero,
                DROP COLUMN IF EXISTS garden_id;
            ALTER TABLE inventory_items
                DROP CONSTRAINT IF EXISTS ux_inventory_items_id_garden;
        """)

    @staticmethod
    def _insert_legacy_receipt(conn: db.DbConn, *, slug: str, resolvable: bool) -> dict[str, int]:
        garden_id = int(
            conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (slug, "Migration 0027 test"),
            ).fetchone()["id"]
        )
        item = conn.execute(
            """
            INSERT INTO inventory_items (garden_id, label, created_at_ms)
            VALUES (%s, %s, %s)
            RETURNING id, public_id
            """,
            (garden_id, "Legacy receipt stock", 1_700_000_000_000),
        ).fetchone()
        transaction_id = int(
            conn.execute(
                """
                INSERT INTO inventory_transactions (
                    item_id, delta, occurred_on, created_at_ms
                ) VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (item["id"], 2.5, "2026-07-15", 1_700_000_000_123),
            ).fetchone()["id"]
        )
        metadata = json.dumps(
            {
                "inventory_item_id": item["public_id"],
                "inventory_transaction_id": transaction_id if resolvable else "missing",
            },
            separators=(",", ":"),
        )
        procurement_id = int(
            conn.execute(
                """
                INSERT INTO procurement_items (
                    garden_id, label, status, metadata_json, created_at_ms, updated_at_ms
                ) VALUES (%s, %s, 'received', %s, %s, %s)
                RETURNING id
                """,
                (garden_id, "Legacy received item", metadata, 1_700_000_000_000, 1_700_000_000_123),
            ).fetchone()["id"]
        )
        return {
            "garden_id": garden_id,
            "inventory_item_id": int(item["id"]),
            "inventory_transaction_id": transaction_id,
            "procurement_id": procurement_id,
        }

    def test_run_migrations_idempotent(self) -> None:
        """Re-running run_migrations must not crash."""
        db.run_migrations()
        db.run_migrations()
        conn = db.get_db()
        try:
            diagnostics = bootstrap_schema_diagnostics_from_snapshot(
                collect_schema_snapshot(conn)
            )
        finally:
            db.return_db(conn)

        self.assertEqual(diagnostics["mode"], "verified-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["missing"], [])

    def test_disposable_database_has_complete_zero_to_current_history(self) -> None:
        conn = db.get_db()
        try:
            self._assert_disposable_database(conn)
            versions = [
                int(row["version"])
                for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            diagnostics = bootstrap_schema_diagnostics_from_snapshot(
                collect_schema_snapshot(conn)
            )
        finally:
            db.return_db(conn)

        self.assertEqual(versions, list(range(1, 29)))
        self.assertEqual(diagnostics["mode"], "verified-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["missing"], [])

    def test_untracked_pre_0028_database_stamps_then_upgrades_to_current(self) -> None:
        conn = db.get_db()
        try:
            self._assert_disposable_database(conn)
            conn.execute("""
                ALTER TABLE auth_sessions
                    DROP COLUMN device_label,
                    DROP COLUMN location_hint;
                DELETE FROM schema_migrations;
            """)
            conn.commit()
        finally:
            db.return_db(conn)

        try:
            db.run_migrations()
            conn = db.get_db()
            try:
                versions = {
                    int(row["version"])
                    for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
                }
                snapshot = collect_schema_snapshot(conn)
            finally:
                db.return_db(conn)

            self.assertEqual(versions, set(range(1, 29)))
            self.assertEqual(
                snapshot.column_types["auth_sessions.device_label"],
                "text",
            )
            self.assertEqual(
                snapshot.column_defaults["auth_sessions.location_hint"],
                "''::text",
            )
            self.assertFalse(snapshot.column_nullability["auth_sessions.device_label"])
            self.assertEqual(missing_schema_parts(snapshot), [])
        finally:
            self._restore_migration_0028_and_history()

    def test_partial_0028_database_fails_closed_before_stamping(self) -> None:
        conn = db.get_db()
        try:
            self._assert_disposable_database(conn)
            conn.execute("""
                ALTER TABLE auth_sessions DROP COLUMN location_hint;
                DELETE FROM schema_migrations;
            """)
            conn.commit()
        finally:
            db.return_db(conn)

        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "refusing to stamp migrations.*column:auth_sessions.location_hint",
            ):
                db.run_migrations()
            conn = db.get_db()
            try:
                versions = conn.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
                columns = {
                    str(row["column_name"])
                    for row in conn.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'auth_sessions'
                        """
                    ).fetchall()
                }
            finally:
                db.return_db(conn)
            self.assertEqual(versions, [])
            self.assertIn("device_label", columns)
            self.assertNotIn("location_hint", columns)
        finally:
            self._restore_migration_0028_and_history()

    def test_0028_sql_rerun_is_idempotent(self) -> None:
        conn = db.get_db()
        try:
            self._assert_disposable_database(conn)
            conn.execute(self._migration_0028_sql())
            conn.execute(self._migration_0028_sql())
            self.assertEqual(missing_schema_parts(collect_schema_snapshot(conn)), [])
        finally:
            conn.rollback()
            db.return_db(conn)

    def test_empty_bootstrap_signature_runs_migrations_normally(self) -> None:
        snapshot = SchemaSnapshot(
            tables={"schema_migrations"},
            columns={"schema_migrations": {"version", "applied_at"}},
            indexes=set(),
            constraints={"schema_migrations_pkey"},
        )

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "empty")
        self.assertFalse(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["missing"], [])

    def test_complete_bootstrap_signature_can_be_stamped(self) -> None:
        snapshot = self._complete_schema_snapshot()

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["missing"], [])

    def test_pre_0028_bootstrap_signature_stamps_only_through_0027(self) -> None:
        snapshot = self._complete_schema_snapshot()
        snapshot.columns["auth_sessions"].difference_update(
            {"device_label", "location_hint"}
        )
        for column in (
            "auth_sessions.device_label",
            "auth_sessions.location_hint",
        ):
            snapshot.column_nullability.pop(column)
            snapshot.column_types.pop(column)
            snapshot.column_defaults.pop(column)

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 27)

    def test_auth_session_schema_signature_rejects_missing_table_or_column(self) -> None:
        missing_table = self._complete_schema_snapshot()
        missing_table.tables.remove("auth_sessions")
        missing_table.columns.pop("auth_sessions")
        for column in (
            "auth_sessions.device_label",
            "auth_sessions.location_hint",
        ):
            missing_table.column_nullability.pop(column)
            missing_table.column_types.pop(column)
            missing_table.column_defaults.pop(column)

        table_diagnostics = bootstrap_schema_diagnostics_from_snapshot(missing_table)

        self.assertEqual(table_diagnostics["mode"], "incomplete-existing-schema")
        self.assertFalse(table_diagnostics["can_stamp_migrations"])
        self.assertIn(
            {"kind": "table", "object": "auth_sessions"},
            table_diagnostics["missing"],
        )

        missing_column = self._complete_schema_snapshot()
        missing_column.columns["auth_sessions"].remove("device_label")

        column_diagnostics = bootstrap_schema_diagnostics_from_snapshot(missing_column)

        self.assertEqual(column_diagnostics["mode"], "incomplete-existing-schema")
        self.assertFalse(column_diagnostics["can_stamp_migrations"])
        self.assertIn(
            {"kind": "column", "object": "auth_sessions.device_label"},
            column_diagnostics["missing"],
        )

    def test_auth_session_schema_signature_rejects_wrong_definitions(self) -> None:
        snapshot = self._complete_schema_snapshot()
        snapshot.column_types["auth_sessions.device_label"] = "character varying"
        snapshot.column_defaults["auth_sessions.location_hint"] = "'unknown'::text"
        snapshot.column_nullability["auth_sessions.device_label"] = True

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "incomplete-existing-schema")
        self.assertFalse(diagnostics["can_stamp_migrations"])
        self.assertIn(
            {"kind": "column-type", "object": "auth_sessions.device_label"},
            diagnostics["missing"],
        )
        self.assertIn(
            {"kind": "column-default", "object": "auth_sessions.location_hint"},
            diagnostics["missing"],
        )
        self.assertIn(
            {"kind": "column-nullability", "object": "auth_sessions.device_label"},
            diagnostics["missing"],
        )

    def test_pre_0021_bootstrap_signature_stamps_only_through_0020(self) -> None:
        snapshot = self._complete_schema_snapshot()
        snapshot.indexes.remove("ux_weather_alerts_identity")
        snapshot.index_definitions.pop("ux_weather_alerts_identity", None)
        self._remove_offline_operation_schema(snapshot)

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 20)

    def test_pre_0022_bootstrap_signature_stamps_only_through_0021(self) -> None:
        snapshot = self._complete_schema_snapshot()
        self._remove_offline_operation_schema(snapshot)

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 21)

    def test_pre_0026_bootstrap_signature_stamps_only_through_0025(self) -> None:
        snapshot = self._complete_schema_snapshot()
        snapshot.tables.remove("media_cleanup_jobs")
        snapshot.columns.pop("media_cleanup_jobs")
        snapshot.columns["media_assets"].remove("preview_bytes")
        snapshot.indexes.remove("idx_media_cleanup_jobs_created")
        snapshot.constraints.difference_update(
            {
                "media_cleanup_jobs_pkey",
                "ux_media_cleanup_jobs_storage_keys",
                "ck_media_cleanup_jobs_attempts_nonnegative",
            }
        )

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 25)

    def test_untracked_pre_0021_database_is_upgraded_to_current(self) -> None:
        conn = db.get_db()
        try:
            self._assert_disposable_database(conn)
            conn.execute("DROP TABLE IF EXISTS offline_create_operations CASCADE")
            conn.execute("DROP INDEX IF EXISTS ux_weather_alerts_identity")
            conn.execute("DELETE FROM schema_migrations")
            conn.commit()
        finally:
            db.return_db(conn)

        db.run_migrations()

        conn = db.get_db()
        try:
            versions = {
                int(row["version"])
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            table = conn.execute(
                "SELECT to_regclass('public.offline_create_operations') AS name"
            ).fetchone()
            index = conn.execute(
                "SELECT to_regclass('public.ux_weather_alerts_identity') AS name"
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(versions, set(range(1, 29)))
        self.assertEqual(table["name"], "offline_create_operations")
        self.assertEqual(index["name"], "ux_weather_alerts_identity")

    def test_0027_upgrades_resolvable_legacy_received_procurement(self) -> None:
        conn = db.get_db()
        try:
            self._assert_disposable_database(conn)
            self._remove_migration_0027_surface(conn)
            receipt = self._insert_legacy_receipt(
                conn,
                slug="migration-0027-resolvable",
                resolvable=True,
            )

            conn.execute(self._migration_0027_sql())
            provenance = conn.execute(
                """
                SELECT receipt_inventory_item_id,
                       receipt_inventory_transaction_id,
                       received_at_ms
                FROM procurement_items
                WHERE id = %s
                """,
                (receipt["procurement_id"],),
            ).fetchone()

            self.assertEqual(
                int(provenance["receipt_inventory_item_id"]),
                receipt["inventory_item_id"],
            )
            self.assertEqual(
                int(provenance["receipt_inventory_transaction_id"]),
                receipt["inventory_transaction_id"],
            )
            self.assertEqual(int(provenance["received_at_ms"]), 1_700_000_000_123)

            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE procurement_items
                        SET received_at_ms = NULL
                        WHERE id = %s
                        """,
                        (receipt["procurement_id"],),
                    )
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "UPDATE procurement_items SET status = 'ordered' WHERE id = %s",
                        (receipt["procurement_id"],),
                    )

            self.assertNotIn(
                {
                    "kind": "constraint-definition",
                    "object": "ck_procurement_receipt_provenance",
                },
                missing_schema_parts(collect_schema_snapshot(conn)),
            )
            conn.execute(self._migration_0027_sql())
        finally:
            conn.rollback()
            db.return_db(conn)

    def test_0027_unresolved_legacy_received_row_rolls_back_then_reruns(self) -> None:
        conn = db.get_db()
        migration_sql = self._migration_0027_sql()
        receipt: dict[str, int] | None = None
        try:
            self._assert_disposable_database(conn)
            self._remove_migration_0027_surface(conn)
            receipt = self._insert_legacy_receipt(
                conn,
                slug="migration-0027-unresolvable",
                resolvable=False,
            )
            conn.commit()

            with self.assertRaises(psycopg.errors.CheckViolation) as raised:
                conn.execute(migration_sql)
            self.assertIn("cannot establish receipt provenance", str(raised.exception))
            self.assertIn(
                "Correct metadata_json",
                raised.exception.diag.message_hint or "",
            )
            conn.rollback()

            columns = {
                str(row["column_name"])
                for row in conn.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'procurement_items'
                    """
                ).fetchall()
            }
            self.assertNotIn("receipt_inventory_item_id", columns)
            legacy = conn.execute(
                "SELECT status, metadata_json FROM procurement_items WHERE id = %s",
                (receipt["procurement_id"],),
            ).fetchone()
            self.assertEqual(legacy["status"], "received")
            self.assertIn('"inventory_transaction_id":"missing"', legacy["metadata_json"])

            conn.execute(
                """
                UPDATE procurement_items
                SET metadata_json = jsonb_set(
                    metadata_json::jsonb,
                    '{inventory_transaction_id}',
                    to_jsonb(%s::text)
                )::text
                WHERE id = %s
                """,
                (receipt["inventory_transaction_id"], receipt["procurement_id"]),
            )
            conn.commit()

            conn.execute(migration_sql)
            conn.commit()
            provenance = conn.execute(
                """
                SELECT receipt_inventory_item_id,
                       receipt_inventory_transaction_id,
                       received_at_ms
                FROM procurement_items
                WHERE id = %s
                """,
                (receipt["procurement_id"],),
            ).fetchone()
            self.assertEqual(
                (
                    int(provenance["receipt_inventory_item_id"]),
                    int(provenance["receipt_inventory_transaction_id"]),
                    int(provenance["received_at_ms"]),
                ),
                (
                    receipt["inventory_item_id"],
                    receipt["inventory_transaction_id"],
                    1_700_000_000_123,
                ),
            )
        finally:
            conn.rollback()
            if receipt is not None:
                conn.execute("DELETE FROM gardens WHERE id = %s", (receipt["garden_id"],))
            conn.execute(migration_sql)
            conn.commit()
            db.return_db(conn)

    def test_passkey_schema_signature_covers_migration_surface(self) -> None:
        self.assertTrue(
            {
                "nickname",
                "transports",
                "credential_device_type",
                "credential_backed_up",
                "created_at_ms",
                "updated_at_ms",
                "last_used_at_ms",
            }.issubset(set(REQUIRED_COLUMNS["auth_passkeys"]))
        )
        self.assertTrue(
            {
                "user_id",
                "session_token_hash",
                "invitation_token_hash",
                "invitation_scope",
                "invitation_id",
                "invitee_username",
                "invitation_user_handle",
                "created_at_ms",
            }.issubset(set(REQUIRED_COLUMNS["auth_passkey_challenges"]))
        )
        self.assertTrue(
            {
                "password_auth_disabled",
                "passkey_user_handle",
                "passkey_prompt_dismissed_until_ms",
            }.issubset(set(REQUIRED_COLUMNS["auth_users"]))
        )
        self.assertIn("auth_password_reset_tokens", REQUIRED_TABLES)
        self.assertIn("purpose", REQUIRED_COLUMNS["auth_password_reset_tokens"])
        self.assertIn("idx_auth_passkey_challenges_user", REQUIRED_INDEXES)
        self.assertIn("idx_auth_passkey_challenges_invitation", REQUIRED_INDEXES)
        self.assertIn("ux_auth_users_passkey_user_handle", REQUIRED_INDEXES)
        self.assertIn("auth_passkeys_user_id_fkey", REQUIRED_CONSTRAINTS)
        self.assertIn("auth_passkey_challenges_user_id_fkey", REQUIRED_CONSTRAINTS)
        self.assertIn("ck_auth_users_password_auth_state", REQUIRED_CONSTRAINTS)
        self.assertIn("auth_users.password_hash", REQUIRED_COLUMN_NULLABILITY)
        self.assertIn("idx_auth_passkey_challenges_invitation", REQUIRED_INDEX_DEFINITION_FRAGMENTS)
        self.assertIn("ux_auth_users_passkey_user_handle", REQUIRED_INDEX_DEFINITION_FRAGMENTS)
        self.assertIn(
            "ck_auth_users_password_auth_state",
            REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS,
        )

    def test_attention_schema_signature_covers_migration_surface(self) -> None:
        self.assertTrue(
            {
                "user_attention_preferences",
                "user_attention_item_state",
                "attention_outcomes",
            }.issubset(set(REQUIRED_TABLES))
        )
        self.assertTrue(
            {
                "id",
                "user_id",
                "preset",
                "rules_json",
                "quiet_hours_json",
                "show_no_action_history",
                "metadata_json",
                "created_at_ms",
                "updated_at_ms",
            }.issubset(set(REQUIRED_COLUMNS["user_attention_preferences"]))
        )
        self.assertTrue(
            {
                "id",
                "user_id",
                "garden_id",
                "item_id",
                "user_state",
                "snoozed_until_ms",
                "reason",
                "metadata_json",
                "created_at_ms",
                "updated_at_ms",
            }.issubset(set(REQUIRED_COLUMNS["user_attention_item_state"]))
        )
        self.assertTrue(
            {
                "id",
                "public_id",
                "garden_id",
                "provider",
                "outcome_type",
                "source_type",
                "source_id",
                "source_public_id",
                "title",
                "explanation",
                "reason",
                "target_type",
                "target_id",
                "plant_ids_json",
                "plot_ids_json",
                "recovery_action_json",
                "metadata_json",
                "occurred_at_ms",
                "expires_at_ms",
                "created_at_ms",
                "updated_at_ms",
            }.issubset(set(REQUIRED_COLUMNS["attention_outcomes"]))
        )
        self.assertTrue(
            {
                "idx_user_attention_item_state_garden_user",
                "idx_attention_outcomes_garden_expires",
                "idx_attention_outcomes_source",
                "ux_attention_outcomes_source_kind",
            }.issubset(set(REQUIRED_INDEXES))
        )
        self.assertTrue(
            {
                "ux_user_attention_preferences_user",
                "fk_user_attention_preferences_user",
                "ck_user_attention_preferences_no_action_bool",
                "ux_user_attention_item_state_user_garden_item",
                "fk_user_attention_item_state_user",
                "fk_user_attention_item_state_garden",
                "attention_outcomes_public_id_key",
                "fk_attention_outcomes_garden",
            }.issubset(set(REQUIRED_CONSTRAINTS))
        )

    def test_weather_identity_schema_signature_rejects_pre_migration_schema(self) -> None:
        self.assertIn("ux_weather_alerts_identity", REQUIRED_INDEXES)
        self.assertIn(
            "ux_weather_alerts_identity",
            REQUIRED_INDEX_DEFINITION_FRAGMENTS,
        )
        snapshot = self._complete_schema_snapshot()
        snapshot.indexes.remove("ux_weather_alerts_identity")
        snapshot.index_definitions.pop("ux_weather_alerts_identity", None)

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "incomplete-existing-schema")
        self.assertFalse(diagnostics["can_stamp_migrations"])
        self.assertIn(
            {"kind": "index", "object": "ux_weather_alerts_identity"},
            diagnostics["missing"],
        )

    def test_offline_operation_schema_signature_rejects_pre_migration_schema(self) -> None:
        self.assertIn("offline_create_operations", REQUIRED_TABLES)
        self.assertTrue(
            {
                "garden_id",
                "endpoint",
                "operation_id",
                "request_fingerprint",
                "target_type",
                "target_id",
                "result_id",
                "expires_at_ms",
            }.issubset(set(REQUIRED_COLUMNS["offline_create_operations"]))
        )
        self.assertIn("idx_offline_create_operations_expiry", REQUIRED_INDEXES)
        self.assertIn(
            "ux_offline_create_operations_garden_endpoint_operation",
            REQUIRED_CONSTRAINTS,
        )
        self.assertIn("fk_offline_create_operations_garden", REQUIRED_CONSTRAINTS)
        snapshot = self._complete_schema_snapshot()
        snapshot.tables.remove("offline_create_operations")
        snapshot.columns.pop("offline_create_operations", None)

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "incomplete-existing-schema")
        self.assertFalse(diagnostics["can_stamp_migrations"])
        self.assertIn(
            {"kind": "table", "object": "offline_create_operations"},
            diagnostics["missing"],
        )

    def test_audit_schema_signature_retains_nonunique_request_correlation(self) -> None:
        self.assertIn("request_id", REQUIRED_COLUMNS["audit_events"])
        self.assertIn("ux_audit_events_request_id", REQUIRED_INDEXES)
        self.assertIn(
            "ux_audit_events_request_id",
            REQUIRED_INDEX_DEFINITION_FRAGMENTS,
        )

        snapshot = self._complete_schema_snapshot()
        snapshot.columns["audit_events"].remove("request_id")
        snapshot.indexes.remove("ux_audit_events_request_id")
        snapshot.index_definitions.pop("ux_audit_events_request_id", None)

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 22)

        migration_sql = (
            Path(__file__).parents[1] / "migrations/0024_audit_request_correlation_index.sql"
        ).read_text()
        self.assertIn("DROP INDEX IF EXISTS public.ux_audit_events_request_id", migration_sql)
        self.assertIn("USING btree (request_id, id)", migration_sql)
        self.assertNotIn("USING btree (request_id)\n", migration_sql)

    def test_audit_schema_signature_requires_request_id_then_id_index_keys(self) -> None:
        snapshot = self._complete_schema_snapshot()
        snapshot.index_definitions["ux_audit_events_request_id"] = (
            "CREATE UNIQUE INDEX ux_audit_events_request_id "
            "ON public.audit_events USING btree (id, request_id) "
            "WHERE (request_id <> ''::text)"
        )

        missing = missing_schema_parts(snapshot)

        self.assertIn(
            {"kind": "index-definition", "object": "ux_audit_events_request_id"},
            missing,
        )

    def test_schema_signature_validates_critical_definitions(self) -> None:
        snapshot = self._complete_schema_snapshot()
        snapshot.column_nullability["auth_users.password_hash"] = False
        snapshot.index_definitions["ux_auth_users_passkey_user_handle"] = (
            "CREATE UNIQUE INDEX ux_auth_users_passkey_user_handle ON auth_users (id)"
        )
        snapshot.constraint_definitions["ck_auth_users_password_auth_state"] = "CHECK (true)"
        snapshot.constraint_definitions["ck_procurement_receipt_provenance"] = """
            CHECK (
                (
                    receipt_inventory_item_id IS NULL
                    AND receipt_inventory_transaction_id IS NULL
                    AND received_at_ms IS NULL
                )
                OR (
                    receipt_inventory_item_id IS NOT NULL
                    AND receipt_inventory_transaction_id IS NOT NULL
                    AND received_at_ms IS NOT NULL
                    AND status = 'received'
                )
            )
        """

        missing = missing_schema_parts(snapshot)

        self.assertIn(
            {"kind": "column-nullability", "object": "auth_users.password_hash"},
            missing,
        )
        self.assertIn(
            {"kind": "index-definition", "object": "ux_auth_users_passkey_user_handle"},
            missing,
        )
        self.assertIn(
            {"kind": "constraint-definition", "object": "ck_auth_users_password_auth_state"},
            missing,
        )
        self.assertIn(
            {"kind": "constraint-definition", "object": "ck_procurement_receipt_provenance"},
            missing,
        )

    def test_partial_bootstrap_signature_is_rejected(self) -> None:
        snapshot = SchemaSnapshot(
            tables={"schema_migrations", "gardens"},
            columns={
                "schema_migrations": {"version", "applied_at"},
                "gardens": {"id", "slug", "name"},
            },
            indexes=set(),
            constraints={"schema_migrations_pkey", "gardens_pkey"},
        )

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "incomplete-existing-schema")
        self.assertFalse(diagnostics["can_stamp_migrations"])
        self.assertIn({"kind": "table", "object": "auth_users"}, diagnostics["missing"])
        self.assertIn({"kind": "column", "object": "gardens.owner_user_id"}, diagnostics["missing"])

    def test_incomplete_bootstrap_error_is_operator_actionable(self) -> None:
        diagnostics = {
            "mode": "incomplete-existing-schema",
            "missing": [
                {"kind": "table", "object": "auth_users"},
                {"kind": "column", "object": "gardens.owner_user_id"},
            ],
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "check_backend_integrity.py --bootstrap-only",
        ):
            db._raise_incomplete_bootstrap_schema(diagnostics)


class HealthEndpointTests(unittest.TestCase):
    """Verify public liveness and admin diagnostics routes."""

    def setUp(self) -> None:
        _truncate_all()
        conn = db.get_db()
        try:
            db.ensure_default_garden(conn)
            conn.commit()
        finally:
            db.return_db(conn)

    @staticmethod
    def _request(
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ):
        from starlette.requests import Request as StarletteRequest

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in (headers or {}).items()
            ],
            "client": ("127.0.0.1", 5000),
        }
        return StarletteRequest(scope)

    def test_public_health_is_minimal_even_for_local_admin(self) -> None:
        """Public /api/health should stay minimal in local no-auth mode."""
        from gardenops.routers import health as health_router

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "false",
                "RATE_LIMIT_BACKEND": "memory",
                "INTERNET_EXPOSED": "false",
                "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
            },
        ):
            self.assertEqual(health_router.health(), {"status": "ok"})

    def test_public_health_stays_reachable_when_auth_required(self) -> None:
        """Global auth middleware must not block the public liveness route."""
        from fastapi.testclient import TestClient

        from gardenops.main import app

        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "RATE_LIMIT_BACKEND": "memory",
                "INTERNET_EXPOSED": "false",
                "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
            },
        ):
            with TestClient(app) as client:
                response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_admin_system_health_returns_full_detail_for_local_admin(self) -> None:
        """Local admin fallback should still reach the diagnostics route."""
        from gardenops.routers import health as health_router

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "false",
                "RATE_LIMIT_BACKEND": "memory",
                "INTERNET_EXPOSED": "false",
                "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
            },
        ):
            data = health_router.admin_system_health(
                self._request("/api/admin/system/health"),
            )
            self.assertEqual(data["status"], "ok")
            self.assertIn("db_quick_check", data)
            self.assertEqual(data["db_quick_check"], "ok")
            self.assertIn("fk_violations", data)
            self.assertIn("table_count", data)
            self.assertIn("uptime_seconds", data)

    def test_admin_system_health_requires_admin_auth(self) -> None:
        """Admin diagnostics should not be reachable without admin auth."""
        from fastapi import HTTPException

        from gardenops.routers import health as health_router

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "api_key",
                "AUTH_API_KEY": "health-test-key",
                "RATE_LIMIT_BACKEND": "memory",
                "INTERNET_EXPOSED": "false",
                "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
            },
        ):
            with self.assertRaises(HTTPException) as denied_exc:
                health_router.admin_system_health(
                    self._request("/api/admin/system/health"),
                )
            self.assertEqual(denied_exc.exception.status_code, 401)

            allowed = health_router.admin_system_health(
                self._request(
                    "/api/admin/system/health",
                    headers={"x-api-key": "health-test-key"},
                ),
            )
            self.assertEqual(allowed["status"], "ok")
            self.assertIn("db_quick_check", allowed)

    def test_admin_system_health_accepts_review_bearer_token(self) -> None:
        """Deployed readiness can use a dedicated admin-health token without sessions."""
        from fastapi import HTTPException

        from gardenops.routers import health as health_router

        review_token = "review-health-token-" + ("x" * 40)
        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "RATE_LIMIT_BACKEND": "memory",
                "DEPLOYED_READINESS_ADMIN_BEARER_TOKEN": review_token,
                "INTERNET_EXPOSED": "false",
                "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
            },
        ):
            denied_request = self._request(
                "/api/admin/system/health",
                headers={"authorization": "Bearer wrong-token"},
            )
            with self.assertRaises(HTTPException) as denied_exc:
                health_router.admin_system_health(denied_request)
            self.assertEqual(denied_exc.exception.status_code, 401)

            allowed = health_router.admin_system_health(
                self._request(
                    "/api/admin/system/health",
                    headers={"authorization": f"Bearer {review_token}"},
                ),
            )
            self.assertEqual(allowed["status"], "ok")
            self.assertIn("db_quick_check", allowed)

    def test_admin_system_health_session_fallback_requires_strong_admin_auth(self) -> None:
        """Admin sessions must satisfy the same strong-auth state as global guarded routes."""
        from fastapi import HTTPException

        from gardenops.routers import health as health_router
        from gardenops.security import AuthContext

        request = self._request("/api/admin/system/health")
        weak_admin = AuthContext(
            user_id=7,
            username="admin",
            role="admin",
            auth_type="session",
            mfa_enabled=True,
            mfa_authenticated_at_ms=0,
        )
        with (
            patch.dict(
                "os.environ",
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_ADMIN_MFA_REQUIRED": "true",
                    "RATE_LIMIT_BACKEND": "memory",
                    "INTERNET_EXPOSED": "false",
                    "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
                },
            ),
            patch.object(health_router, "validate_request_auth", return_value=weak_admin),
        ):
            with self.assertRaises(HTTPException) as denied_exc:
                health_router.admin_system_health(request)
            self.assertEqual(denied_exc.exception.status_code, 403)
            self.assertIn("MFA", str(denied_exc.exception.detail))

    def test_admin_system_health_review_token_passes_global_auth_guard(self) -> None:
        """The review token must reach the route instead of dying in middleware."""
        from fastapi.testclient import TestClient

        from gardenops.main import app

        review_token = "review-health-token-" + ("x" * 40)
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "RATE_LIMIT_BACKEND": "memory",
                "DEPLOYED_READINESS_ADMIN_BEARER_TOKEN": review_token,
                "INTERNET_EXPOSED": "false",
                "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver,testclient",
            },
        ):
            with TestClient(app) as client:
                denied = client.get(
                    "/api/admin/system/health",
                    headers={"authorization": "Bearer wrong-token"},
                )
                allowed = client.get(
                    "/api/admin/system/health",
                    headers={"authorization": f"Bearer {review_token}"},
                )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["status"], "ok")

    @patch("gardenops.rate_limit.RedisRateLimitBackend", side_effect=OSError("redis down"))
    def test_startup_fails_when_shared_redis_backend_is_unavailable(
        self,
        _mock_backend,
    ) -> None:
        """Internet-exposed startup fails closed if the shared Redis backend is down."""
        from fastapi.testclient import TestClient

        from gardenops.main import app
        from gardenops.rate_limit import reset_rate_limits

        reset_rate_limits()
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "development",
                "INTERNET_EXPOSED": "true",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_MFA_SECRET_KEY": "test-integrity-mfa-secret-32chars",
                "ALLOW_INSECURE_REMOTE": "false",
                "TRUST_PROXY_HEADERS": "true",
                "TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
                "RATE_LIMIT_BACKEND": "redis",
                "RATE_LIMIT_REDIS_URL": "redis://127.0.0.1:6379/0",
                "ALLOWED_HOSTS": "gardenops.example.com",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "RATE_LIMIT_BACKEND=redis but redis is unavailable",
            ):
                with TestClient(app):
                    pass


if __name__ == "__main__":
    unittest.main()
