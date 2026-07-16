import hashlib
from datetime import date
from unittest.mock import patch

import gardenops.db as db
import gardenops.passkeys as passkey_service
from gardenops.incident_controls import public_session_id, set_emergency_read_only
from tests.base import BaseApiTest

AUTH_ENV = {
    "AUTH_REQUIRED": "true",
    "AUTH_MODE": "session",
    "AUTH_API_KEY": "",
}


class TestAuthorizationWriteGates(BaseApiTest):
    def _rows(self, query: str, params: tuple[object, ...] = ()) -> list[dict]:
        conn = db.get_db()
        try:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            db.return_db(conn)

    def _media_files(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                str(path.relative_to(self.test_media_dir))
                for path in self.test_media_dir.rglob("*")
                if path.is_file()
            ),
        )

    def _phase_five_state(self, *, session_user_id: int) -> dict[str, object]:
        return {
            "user_invitations": self._rows(
                """
                SELECT id, invitee_username, role, accepted_at_ms, revoked_at_ms
                FROM auth_user_invitations
                ORDER BY id
                """,
            ),
            "memberships": self._rows(
                """
                SELECT garden_id, user_id, role
                FROM garden_memberships
                ORDER BY garden_id, user_id
                """,
            ),
            "sessions": self._rows(
                "SELECT token_hash FROM auth_sessions WHERE user_id = %s ORDER BY token_hash",
                (session_user_id,),
            ),
            "mfa_users": self._rows(
                """
                SELECT id, mfa_totp_enabled, mfa_totp_secret, mfa_enrolled_at
                FROM auth_users
                ORDER BY id
                """,
            ),
            "mfa_pending": self._rows(
                """
                SELECT user_id, secret_ciphertext, expires_at_ms
                FROM auth_mfa_pending_enrollments
                ORDER BY user_id
                """,
            ),
            "mfa_recovery": self._rows(
                """
                SELECT user_id, code_hash, used_at_ms
                FROM auth_mfa_recovery_codes
                ORDER BY user_id, code_hash
                """,
            ),
            "emergency_flags": self._rows(
                """
                SELECT key, value
                FROM security_runtime_flags
                WHERE key IN ('emergency_read_only', 'emergency_read_only_expires_at_ms')
                ORDER BY key
                """,
            ),
            "media_files": self._media_files(),
        }

    @staticmethod
    def _request(client, method: str, path: str, headers: dict[str, str], body: dict | None):
        return client.request(method, path, headers=headers, json=body)

    def _viewer_client(self, username: str = "write_gate_viewer"):
        viewer = self._create_test_user(username, "viewerpass", role="viewer")
        client, headers = self._authenticated_client(username, "viewerpass")
        return viewer, client, headers

    def _give_viewer_seed_ownership(self, viewer_id: int) -> int:
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            for plot_id in ("B1", "B2"):
                conn.execute(
                    """
                    UPDATE plot_ownership
                    SET owner_user_id = %s
                    WHERE plot_id = %s AND garden_id = %s
                    """,
                    (viewer_id, plot_id, garden_id),
                )
            for plt_id in ("PLT-TEST", "PLT-002"):
                conn.execute(
                    """
                    UPDATE plant_ownership
                    SET owner_user_id = %s
                    WHERE plt_id = %s AND garden_id = %s
                    """,
                    (viewer_id, plt_id, garden_id),
                )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-TEST', 1)
                ON CONFLICT(plot_id, plt_id) DO NOTHING
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)
        return garden_id

    def test_viewer_cannot_mutate_owned_plants(self) -> None:
        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("plant_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))

            csv_text = (
                "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,"
                "link,year_planted,deer_resistant,care_watering,care_soil,care_planting,"
                "care_maintenance,care_notes\n"
                "PLT-TEST,Viewer Edit,,frø,,,,,,,,0,,,,,\n"
            )
            imported = client.post(
                "/api/plants/import-csv",
                headers=headers,
                json={"csv_text": csv_text},
            )
            patched = client.patch(
                "/api/plants/PLT-TEST",
                headers=headers,
                json={"name": "Viewer renamed plant"},
            )
            deleted = client.delete("/api/plants/PLT-TEST", headers=headers)

        self.assertEqual(imported.status_code, 403, imported.text)
        self.assertEqual(patched.status_code, 403, patched.text)
        self.assertEqual(deleted.status_code, 403, deleted.text)
        plant = self._rows(
            "SELECT plt_id, name FROM plants WHERE plt_id = 'PLT-TEST'",
        )
        self.assertEqual(plant, [{"plt_id": "PLT-TEST", "name": "Test Plant"}])
        self.assertEqual(self._media_files(), ())

    def test_viewer_cannot_mutate_owned_plots_or_assignments(self) -> None:
        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("plot_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))

            create_plot = client.post(
                "/api/plots",
                headers=headers,
                json={
                    "plot_id": "VIEWER-PLOT",
                    "zone_code": "V",
                    "zone_name": "Viewer",
                    "plot_number": 9,
                    "grid_row": 8,
                    "grid_col": 8,
                },
            )
            update_plot = client.patch(
                "/api/plots/B1",
                headers=headers,
                json={"color": "#112233"},
            )
            batch_move = client.post(
                "/api/plots/batch-move",
                headers=headers,
                json={
                    "moves": [
                        {"plot_id": "B1", "grid_row": 2, "grid_col": 1},
                        {"plot_id": "B2", "grid_row": 2, "grid_col": 2},
                    ],
                },
            )
            add_assignment = client.post(
                "/api/plots/B2/plants/PLT-TEST",
                headers=headers,
                json={"quantity": 2},
            )
            update_assignment = client.patch(
                "/api/plots/B1/plants/PLT-TEST",
                headers=headers,
                json={"quantity": 3},
            )
            move_assignment = client.post(
                "/api/plots/B1/plants/PLT-TEST/move/B2",
                headers=headers,
            )
            remove_assignment = client.delete(
                "/api/plots/B1/plants/PLT-TEST",
                headers=headers,
            )
            delete_plot = client.delete("/api/plots/B2", headers=headers)

        for response in (
            create_plot,
            update_plot,
            batch_move,
            add_assignment,
            update_assignment,
            move_assignment,
            remove_assignment,
            delete_plot,
        ):
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            self._rows(
                """
                SELECT plot_id, grid_row, grid_col, color
                FROM plots
                WHERE plot_id IN ('B1', 'B2', 'VIEWER-PLOT')
                ORDER BY plot_id
                """,
            ),
            [
                {"plot_id": "B1", "grid_row": 1, "grid_col": 1, "color": None},
                {"plot_id": "B2", "grid_row": 1, "grid_col": 2, "color": None},
            ],
        )
        self.assertEqual(
            self._rows(
                """
                SELECT plot_id, plt_id, quantity
                FROM plot_plants
                WHERE plt_id = 'PLT-TEST'
                ORDER BY plot_id
                """,
            ),
            [{"plot_id": "B1", "plt_id": "PLT-TEST", "quantity": 1}],
        )

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_viewer_cannot_start_workflow_tasks(self, mock_svc_date, mock_router_date) -> None:
        fake_today = date(2026, 3, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("workflow_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))
            response = client.post(
                "/api/workflows/start",
                headers=headers,
                json={"workflow_id": "spring_prep", "selected_steps": ["assess_damage"]},
            )

        self.assertEqual(response.status_code, 403, response.text)
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM garden_tasks
                WHERE rule_source LIKE 'workflow:spring_prep:assess_damage:%'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["count"]), 0)

    def test_viewer_cannot_mutate_shademap_state(self) -> None:
        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("shademap_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))

            elevation = client.patch(
                "/api/plots/elevations",
                headers=headers,
                json={"overrides": {"B1": 41.0}},
            )
            state = client.patch(
                "/api/shademap/state",
                headers=headers,
                json={
                    "mode": "sun-hours",
                    "selected_plot_id": "B1",
                    "analysis_timestamp_ms": 1772443603995,
                    "preset": "summer",
                },
            )
            calibration = client.patch(
                "/api/shademap/calibration",
                headers=headers,
                json={"enabled": False},
            )
            obstacle = client.post(
                "/api/shademap/obstacles",
                headers=headers,
                json={
                    "kind": "tree",
                    "plot_id": "B1",
                    "x": 1.0,
                    "y": 1.0,
                    "height_m": 3.0,
                    "radius_m": 1.0,
                },
            )

        self.assertEqual(elevation.status_code, 403, elevation.text)
        self.assertEqual(state.status_code, 403, state.text)
        self.assertEqual(calibration.status_code, 403, calibration.text)
        self.assertEqual(obstacle.status_code, 403, obstacle.text)
        self.assertEqual(
            self._rows("SELECT plot_id FROM plot_elevations WHERE plot_id = 'B1'"),
            [],
        )
        self.assertEqual(self._rows("SELECT id FROM shademap_obstacles ORDER BY id"), [])

    def test_representative_garden_write_rejects_cross_garden_unauthenticated_and_stale_auth(
        self,
    ) -> None:
        default_garden_id = self._get_default_garden_id()
        cross_user = self._create_test_user("cross_garden_writer", "crosspass", "editor")
        self._create_test_user("stale_garden_writer", "stalepass", "editor")
        conn = db.get_db()
        try:
            foreign_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("write-gate-foreign", "Write Gate Foreign"),
                ).fetchone()["id"],
            )
            conn.execute(
                "DELETE FROM garden_memberships WHERE user_id = %s",
                (int(cross_user["id"]),),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (foreign_garden_id, int(cross_user["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict("os.environ", AUTH_ENV, clear=False):
            cross_client, cross_headers = self._authenticated_client(
                "cross_garden_writer",
                "crosspass",
                garden_id=foreign_garden_id,
            )
            stale_csrf_client, _ = self._authenticated_client(
                "stale_garden_writer",
                "stalepass",
                garden_id=default_garden_id,
            )
            stale_session_client, stale_session_headers = self._authenticated_client(
                "stale_garden_writer",
                "stalepass",
                garden_id=default_garden_id,
            )
            stale_token = stale_session_client.cookies.get("gardenops_session", "")
            conn = db.get_db()
            try:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = %s",
                    (hashlib.sha256(stale_token.encode()).hexdigest(),),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            cases = (
                (cross_client, cross_headers, 404),
                (self._new_client(), {}, 401),
                (
                    stale_csrf_client,
                    {"x-garden-id": str(default_garden_id), "x-csrf-token": "stale"},
                    403,
                ),
                (stale_session_client, stale_session_headers, 401),
            )
            for client, headers, expected in cases:
                with self.subTest(expected=expected):
                    response = client.patch(
                        "/api/plants/PLT-TEST",
                        headers=headers,
                        json={"name": "Unauthorized rename"},
                    )
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertEqual(
                        self._rows("SELECT name FROM plants WHERE plt_id = 'PLT-TEST'"),
                        [{"name": "Test Plant"}],
                    )
                    self.assertEqual(self._media_files(), ())

    def test_membership_role_and_remove_denials_leave_membership_unchanged(self) -> None:
        target = self._create_test_user("membership_target", "targetpass", "viewer")
        self._create_test_user("membership_viewer", "viewerpass", "viewer")
        foreign_admin = self._create_test_user("foreign_garden_admin", "foreignpass", "editor")
        default_garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            foreign_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("membership-foreign", "Membership Foreign"),
                ).fetchone()["id"],
            )
            conn.execute(
                "DELETE FROM garden_memberships WHERE user_id = %s",
                (int(foreign_admin["id"]),),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (foreign_garden_id, int(foreign_admin["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer_client, viewer_headers = self._authenticated_client(
                "membership_viewer",
                "viewerpass",
            )
            foreign_client, foreign_headers = self._authenticated_client(
                "foreign_garden_admin",
                "foreignpass",
                garden_id=foreign_garden_id,
            )
            admin_client, _ = self._authenticated_client("test_admin", "testadminpass")
            actors = (
                (viewer_client, viewer_headers, 403),
                (foreign_client, foreign_headers, 404),
                (self._new_client(), {}, 401),
                (admin_client, {"x-csrf-token": "stale"}, 403),
            )
            writes = (
                (
                    "POST",
                    f"/api/gardens/{default_garden_id}/memberships",
                    {
                        "username": "membership_target",
                        "role": "editor",
                        "action_reason": "authorization sweep",
                    },
                ),
                (
                    "DELETE",
                    f"/api/gardens/{default_garden_id}/memberships/{int(target['id'])}",
                    None,
                ),
            )
            expected_membership = [{"role": "viewer"}]
            for client, headers, expected_status in actors:
                for method, path, body in writes:
                    with self.subTest(method=method, actor_status=expected_status):
                        response = self._request(client, method, path, headers, body)
                        self.assertEqual(response.status_code, expected_status, response.text)
                        self.assertEqual(
                            self._rows(
                                """
                                SELECT role FROM garden_memberships
                                WHERE garden_id = %s AND user_id = %s
                                """,
                                (default_garden_id, int(target["id"])),
                            ),
                            expected_membership,
                        )

    def test_phase_five_admin_write_denials_have_zero_target_side_effects(self) -> None:
        self._create_test_user("phase5_viewer", "viewerpass", "viewer")
        target = self._create_test_user("phase5_session_target", "targetpass", "editor")
        self._create_test_user("phase5_stale_user", "stalepass", "editor")
        conn = db.get_db()
        try:
            now_ms = db.current_timestamp_ms()
            invitation_id = int(
                conn.execute(
                    """
                    INSERT INTO auth_user_invitations (
                        invitee_username, role, token_hash, created_by_user_id,
                        created_at_ms, expires_at_ms
                    ) VALUES (%s, 'editor', %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        "existing_invitee",
                        hashlib.sha256(b"existing-invite").hexdigest(),
                        self._owner_id,
                        now_ms,
                        now_ms + 3_600_000,
                    ),
                ).fetchone()["id"],
            )
            conn.commit()
        finally:
            db.return_db(conn)

        env = {**AUTH_ENV, "AUTH_ADMIN_MFA_REQUIRED": "false"}
        with patch.dict("os.environ", env, clear=False):
            viewer_client, viewer_headers = self._authenticated_client(
                "phase5_viewer",
                "viewerpass",
            )
            target_client, _ = self._authenticated_client(
                "phase5_session_target",
                "targetpass",
            )
            admin_client, _ = self._authenticated_client("test_admin", "testadminpass")
            stale_client, stale_headers = self._authenticated_client(
                "phase5_stale_user",
                "stalepass",
            )
            stale_token = stale_client.cookies.get("gardenops_session", "")
            conn = db.get_db()
            try:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = %s",
                    (hashlib.sha256(stale_token.encode()).hexdigest(),),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            actors = (
                (viewer_client, viewer_headers, 403),
                (self._new_client(), {}, 401),
                (admin_client, {"x-csrf-token": "stale"}, 403),
                (stale_client, stale_headers, 401),
            )
            writes = (
                (
                    "POST",
                    "/api/auth/user-invitations",
                    {
                        "invitee_username": "new_invitee",
                        "role": "editor",
                        "action_reason": "authorization sweep",
                    },
                ),
                ("DELETE", f"/api/auth/user-invitations/{invitation_id}", None),
                (
                    "POST",
                    "/api/auth/revoke-user-sessions",
                    {
                        "username": "phase5_session_target",
                        "action_reason": "authorization sweep",
                    },
                ),
                (
                    "PATCH",
                    "/api/auth/emergency-read-only",
                    {"enabled": True, "action_reason": "authorization sweep"},
                ),
                ("POST", "/api/auth/mfa/totp/start", {}),
                (
                    "POST",
                    "/api/auth/mfa/totp/cancel",
                    {"action_reason": "authorization sweep"},
                ),
                (
                    "POST",
                    "/api/auth/mfa/disable",
                    {"action_reason": "authorization sweep"},
                ),
                (
                    "POST",
                    "/api/auth/mfa/recovery-codes/regenerate",
                    {"action_reason": "authorization sweep"},
                ),
            )
            before = self._phase_five_state(session_user_id=int(target["id"]))
            for client, headers, expected_status in actors:
                for method, path, body in writes:
                    with self.subTest(method=method, path=path, actor_status=expected_status):
                        response = self._request(client, method, path, headers, body)
                        self.assertEqual(response.status_code, expected_status, response.text)
                        self.assertEqual(
                            self._phase_five_state(session_user_id=int(target["id"])),
                            before,
                        )

    def test_passkey_revoke_is_session_and_user_scoped_without_side_effects(self) -> None:
        target = self._create_test_user("passkey_target", "targetpass", "editor")
        self._create_test_user("passkey_viewer", "viewerpass", "viewer")
        foreign_user = self._create_test_user("passkey_foreign", "foreignpass", "editor")
        conn = db.get_db()
        try:
            now_ms = db.current_timestamp_ms()
            passkey_id = int(
                conn.execute(
                    """
                    INSERT INTO auth_passkeys (
                        user_id, credential_id, credential_public_key, sign_count,
                        nickname, transports, credential_device_type,
                        credential_backed_up, created_at_ms, updated_at_ms, last_used_at_ms
                    ) VALUES (%s, %s, %s, 1, 'Target key', 'internal',
                              'multi_device', 1, %s, %s, NULL)
                    RETURNING id
                    """,
                    (
                        int(target["id"]),
                        passkey_service.encode_public_key(b"authorization-target-key"),
                        passkey_service.encode_public_key(b"authorization-public-key"),
                        now_ms,
                        now_ms,
                    ),
                ).fetchone()["id"],
            )
            foreign_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("passkey-foreign", "Passkey Foreign"),
                ).fetchone()["id"],
            )
            conn.execute(
                "DELETE FROM garden_memberships WHERE user_id = %s",
                (int(foreign_user["id"]),),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (foreign_garden_id, int(foreign_user["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer_client, viewer_headers = self._authenticated_client(
                "passkey_viewer",
                "viewerpass",
            )
            foreign_client, foreign_headers = self._authenticated_client(
                "passkey_foreign",
                "foreignpass",
                garden_id=foreign_garden_id,
            )
            target_client, target_headers = self._authenticated_client(
                "passkey_target",
                "targetpass",
            )
            actors = (
                (viewer_client, viewer_headers, 404),
                (foreign_client, foreign_headers, 404),
                (self._new_client(), {}, 401),
                (target_client, {"x-csrf-token": "stale"}, 403),
            )
            expected = self._rows(
                "SELECT id, user_id, nickname FROM auth_passkeys WHERE id = %s",
                (passkey_id,),
            )
            writes = (
                ("DELETE", {"action_reason": "authorization sweep"}),
                (
                    "PATCH",
                    {
                        "nickname": "Unauthorized rename",
                        "action_reason": "authorization sweep",
                    },
                ),
            )
            for client, headers, expected_status in actors:
                for method, body in writes:
                    with self.subTest(method=method, actor_status=expected_status):
                        response = client.request(
                            method,
                            f"/api/auth/passkeys/{passkey_id}",
                            headers=headers,
                            json=body,
                        )
                        self.assertEqual(response.status_code, expected_status, response.text)
                        self.assertEqual(
                            self._rows(
                                "SELECT id, user_id, nickname FROM auth_passkeys WHERE id = %s",
                                (passkey_id,),
                            ),
                            expected,
                        )

            token = target_client.cookies.get("gardenops_session", "")
            conn = db.get_db()
            try:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = %s",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                )
                conn.commit()
            finally:
                db.return_db(conn)
            stale_session = target_client.request(
                "DELETE",
                f"/api/auth/passkeys/{passkey_id}",
                headers=target_headers,
                json={"action_reason": "authorization sweep"},
            )
            self.assertEqual(stale_session.status_code, 401, stale_session.text)
            self.assertEqual(
                self._rows("SELECT id FROM auth_passkeys WHERE id = %s", (passkey_id,)),
                [{"id": passkey_id}],
            )

    def test_session_revoke_is_cross_user_scoped_without_side_effects(self) -> None:
        self._create_test_user("session_viewer", "viewerpass", "viewer")
        self._create_test_user("session_foreign", "foreignpass", "editor")
        target = self._create_test_user("session_target", "targetpass", "editor")

        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer_client, viewer_headers = self._authenticated_client(
                "session_viewer",
                "viewerpass",
            )
            foreign_client, foreign_headers = self._authenticated_client(
                "session_foreign",
                "foreignpass",
            )
            target_client, _ = self._authenticated_client(
                "session_target",
                "targetpass",
            )
            target_token = target_client.cookies.get("gardenops_session", "")
            target_hash = hashlib.sha256(target_token.encode()).hexdigest()
            session_id = public_session_id(target_hash)
            before = self._rows(
                "SELECT token_hash FROM auth_sessions WHERE user_id = %s",
                (int(target["id"]),),
            )
            admin_client, _ = self._authenticated_client("test_admin", "testadminpass")
            actors = (
                (viewer_client, viewer_headers, 404),
                (foreign_client, foreign_headers, 404),
                (self._new_client(), {}, 401),
                (admin_client, {"x-csrf-token": "stale"}, 403),
            )
            for client, headers, expected_status in actors:
                with self.subTest(actor_status=expected_status):
                    response = client.request(
                        "DELETE",
                        f"/api/auth/sessions/{session_id}",
                        headers=headers,
                        json={"action_reason": "authorization sweep"},
                    )
                    self.assertEqual(response.status_code, expected_status, response.text)
                    self.assertEqual(
                        self._rows(
                            "SELECT token_hash FROM auth_sessions WHERE user_id = %s",
                            (int(target["id"]),),
                        ),
                        before,
                    )

    def test_emergency_read_only_blocks_domain_and_filesystem_side_effects(self) -> None:
        env = {**AUTH_ENV, "AUTH_ADMIN_MFA_REQUIRED": "false"}
        with patch.dict("os.environ", env, clear=False):
            admin_client, admin_headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )
            set_emergency_read_only(True)
            self.addCleanup(set_emergency_read_only, False)
            before_plant = self._rows(
                "SELECT name FROM plants WHERE plt_id = 'PLT-TEST'",
            )
            before_files = self._media_files()
            response = admin_client.patch(
                "/api/plants/PLT-TEST",
                headers=admin_headers,
                json={"name": "Blocked emergency rename"},
            )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json()["detail"],
            "Emergency read-only mode is active",
        )
        self.assertEqual(
            self._rows("SELECT name FROM plants WHERE plt_id = 'PLT-TEST'"),
            before_plant,
        )
        self.assertEqual(self._media_files(), before_files)
