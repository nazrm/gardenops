"""Regression tests for safe user lifecycle deletion behavior."""

from __future__ import annotations

import os
from unittest.mock import patch

import gardenops.db as db
from tests.base import BaseApiTest


class AuthUserLifecycleSafetyTests(BaseApiTest):
    def _admin_session_headers(self) -> tuple[object, dict[str, str]]:
        admin_client = self._new_client()
        _, csrf = self._login_session(
            "test_admin",
            "testadminpass",
            client=admin_client,
        )
        return admin_client, self._session_headers(csrf)

    def test_delete_user_with_live_garden_data_deactivates_and_preserves_data(self) -> None:
        target = self._create_test_user("owner_delete_target", "owner-delete-pass", "editor")
        target_id = int(target["id"])
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute(
                    """
                    INSERT INTO gardens (slug, name, owner_user_id)
                    VALUES ('owner-delete-garden', 'Owner Delete Garden', %s)
                    RETURNING id
                    """,
                    (target_id,),
                ).fetchone()["id"],
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin'), (%s, %s, 'admin')
                ON CONFLICT DO NOTHING
                """,
                (garden_id, self._owner_id, garden_id, target_id),
            )
            conn.execute(
                """
                INSERT INTO plots (plot_id, zone_code, zone_name, plot_number, grid_row, grid_col)
                VALUES ('OWNER-DELETE-PLOT', 'B', 'Bed', 50, 25, 20)
                """
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES ('OWNER-DELETE-PLOT', %s, %s)
                """,
                (target_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plants (plt_id, name, category)
                VALUES ('OWNER-DELETE-PLANT', 'Owner Delete Plant', 'busker')
                """
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES ('OWNER-DELETE-PLANT', %s, %s)
                """,
                (target_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client, headers = self._admin_session_headers()
            delete_response = admin_client.delete(
                f"/api/auth/users/{target_id}",
                headers={**headers, "x-action-reason": "owner-delete-test"},
            )

            self.assertEqual(delete_response.status_code, 200, delete_response.text)
            body = delete_response.json()
            self.assertEqual(body["operation"], "deactivated")
            self.assertFalse(body["hard_delete"])
            self.assertTrue(body["transfer_required"])
            self.assertIn("gardens_owned", body["blocking_resources"])
            self.assertIn("plants_owned", body["blocking_resources"])
            self.assertIn("plots_owned", body["blocking_resources"])

            plots_response = admin_client.get(
                "/api/plots",
                headers=self._session_headers(headers["x-csrf-token"], garden_id=garden_id),
            )

        self.assertEqual(plots_response.status_code, 200, plots_response.text)
        self.assertIn("OWNER-DELETE-PLOT", {plot["plot_id"] for plot in plots_response.json()})

        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT is_active FROM auth_users WHERE id = %s",
                (target_id,),
            ).fetchone()
            self.assertIsNotNone(user_row)
            self.assertEqual(int(user_row["is_active"]), 0)
            plot_owner = conn.execute(
                "SELECT owner_user_id FROM plot_ownership WHERE plot_id = 'OWNER-DELETE-PLOT'",
            ).fetchone()
            plant_owner = conn.execute(
                "SELECT owner_user_id FROM plant_ownership WHERE plt_id = 'OWNER-DELETE-PLANT'",
            ).fetchone()
            self.assertEqual(int(plot_owner["owner_user_id"]), target_id)
            self.assertEqual(int(plant_owner["owner_user_id"]), target_id)
        finally:
            db.return_db(conn)

    def test_delete_user_without_live_data_hard_deletes_memberships_and_sessions(self) -> None:
        target = self._create_test_user("hard_delete_target", "hard-delete-pass", "viewer")
        target_id = int(target["id"])
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO auth_sessions (
                    token_hash, user_id, expires_at_ms, created_at_ms, last_seen_at_ms
                )
                VALUES ('hard-delete-target-session', %s, 9999999999999, 1, 1)
                """,
                (target_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client, headers = self._admin_session_headers()
            delete_response = admin_client.delete(
                f"/api/auth/users/{target_id}",
                headers={**headers, "x-action-reason": "hard-delete-test"},
            )

        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        body = delete_response.json()
        self.assertEqual(body["operation"], "hard_deleted")
        self.assertTrue(body["hard_delete"])
        self.assertEqual(body["revoked_sessions"], 1)

        conn = db.get_db()
        try:
            self.assertIsNone(
                conn.execute("SELECT id FROM auth_users WHERE id = %s", (target_id,)).fetchone(),
            )
            membership_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM garden_memberships WHERE user_id = %s",
                    (target_id,),
                ).fetchone()["c"],
            )
            session_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = %s",
                    (target_id,),
                ).fetchone()["c"],
            )
            self.assertEqual(membership_count, 0)
            self.assertEqual(session_count, 0)
        finally:
            db.return_db(conn)

    def test_delete_user_with_audit_history_deactivates_for_retention(self) -> None:
        target = self._create_test_user("audit_delete_target", "audit-delete-pass", "viewer")
        target_id = int(target["id"])
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO audit_events (
                    occurred_at_ms, actor_user_id, actor_username, actor_role,
                    actor_auth_type, method, path, status_code, detail
                )
                VALUES (1, %s, 'audit_delete_target', 'viewer', 'session',
                    'GET', '/api/test', 200, 'retained')
                """,
                (target_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client, headers = self._admin_session_headers()
            delete_response = admin_client.delete(
                f"/api/auth/users/{target_id}",
                headers={**headers, "x-action-reason": "audit-delete-test"},
            )

        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        body = delete_response.json()
        self.assertEqual(body["operation"], "deactivated")
        self.assertFalse(body["hard_delete"])
        self.assertFalse(body["transfer_required"])
        self.assertTrue(body["retention_required"])
        self.assertEqual(body["reference_counts"]["audit_events"], 1)

        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT is_active FROM auth_users WHERE id = %s",
                (target_id,),
            ).fetchone()
            audit_row = conn.execute(
                "SELECT actor_user_id FROM audit_events WHERE actor_user_id = %s",
                (target_id,),
            ).fetchone()
            self.assertIsNotNone(user_row)
            self.assertEqual(int(user_row["is_active"]), 0)
            self.assertIsNotNone(audit_row)
        finally:
            db.return_db(conn)

    def test_garden_owner_fk_restricts_direct_user_delete(self) -> None:
        target = self._create_test_user("fk_owner_target", "fk-owner-pass", "editor")
        target_id = int(target["id"])
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO gardens (slug, name, owner_user_id)
                VALUES ('fk-owner-garden', 'FK Owner Garden', %s)
                """,
                (target_id,),
            )
            with self.assertRaises(db.psycopg.IntegrityError):
                conn.execute("DELETE FROM auth_users WHERE id = %s", (target_id,))
        finally:
            db.return_db(conn)
