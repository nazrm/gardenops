"""Tests for integrity layer: health endpoints, FK enforcement, consistency."""

import unittest
from pathlib import Path
from unittest.mock import patch

import gardenops.db as db
from gardenops.schema_signature import (
    REQUIRED_COLUMN_NULLABILITY,
    REQUIRED_COLUMNS,
    REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS,
    REQUIRED_CONSTRAINTS,
    REQUIRED_INDEX_DEFINITION_FRAGMENTS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SchemaSnapshot,
    bootstrap_schema_diagnostics_from_snapshot,
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

    def test_run_migrations_idempotent(self) -> None:
        """Re-running run_migrations must not crash."""
        db.run_migrations()
        db.run_migrations()

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

    def test_untracked_pre_0021_database_is_upgraded_to_current(self) -> None:
        conn = db.get_db()
        try:
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

        self.assertEqual(versions, set(range(1, 26)))
        self.assertEqual(table["name"], "offline_create_operations")
        self.assertEqual(index["name"], "ux_weather_alerts_identity")

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
