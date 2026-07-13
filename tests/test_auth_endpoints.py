import os
from unittest.mock import patch

from cryptography.fernet import Fernet

import gardenops.db as db
from gardenops.platform_secrets import OPENAI_API_KEY, get_database_secret, set_database_secret
from tests.base import BaseApiTest, strong_password


class TestAuthStatus(BaseApiTest):
    """Tests for GET /api/auth/status."""

    def test_status_returns_fields(self) -> None:
        resp = self.client.get("/api/auth/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("auth_required", data)
        self.assertIn("auth_mode", data)
        self.assertIn("session_auth_enabled", data)
        self.assertIn("api_key_auth_enabled", data)
        self.assertIn("bootstrap_required", data)
        self.assertIn("user_lifecycle_enabled", data)
        self.assertIn("admin_mfa_required", data)

    def test_status_auth_not_required(self) -> None:
        resp = self.client.get("/api/auth/status")
        data = resp.json()
        self.assertFalse(data["auth_required"])

    def test_status_auth_required(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            resp = self.client.get("/api/auth/status")
            data = resp.json()
            self.assertTrue(data["auth_required"])
            self.assertTrue(data["session_auth_enabled"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestAuthLogout(BaseApiTest):
    """Tests for POST /api/auth/logout."""

    def test_logout_clears_session(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("logout_user", "logoutpass", "admin")
            client, headers = self._authenticated_client(
                "logout_user",
                "logoutpass",
            )
            resp = client.post("/api/auth/logout", headers=headers)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_logout_without_session(self) -> None:
        resp = self.client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class TestAuthMeSettings(BaseApiTest):
    """Tests for GET /api/auth/me/settings and PUT /api/auth/me/settings."""

    def test_get_settings_unauthenticated(self) -> None:
        resp = self.client.get("/api/auth/me/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn("shademap_api_key", data)
        self.assertNotIn("has_shademap_key", data)
        self.assertIn("language", data)
        self.assertIn("mfa", data)

    def test_get_settings_authenticated(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("settings_user", "settingspass", "editor")
            client, headers = self._authenticated_client(
                "settings_user",
                "settingspass",
            )
            resp = client.get("/api/auth/me/settings", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["language"], "en")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_update_settings_shademap_key_is_rejected(self) -> None:
        resp = self.client.put(
            "/api/auth/me/settings",
            json={"shademap_api_key": "test-key-12345678"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_update_settings_language(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("lang_user", "langpassword", "editor")
            client, headers = self._authenticated_client(
                "lang_user",
                "langpassword",
            )
            resp = client.put(
                "/api/auth/me/settings",
                headers=headers,
                json={"language": "no"},
            )
            self.assertEqual(resp.status_code, 200)

            resp = client.get("/api/auth/me/settings", headers=headers)
            self.assertEqual(resp.json()["language"], "no")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestAdminProviderSettings(BaseApiTest):
    """Tests for platform-admin provider settings endpoints."""

    def test_provider_settings_requires_platform_admin(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("provider_editor", "providerpass", "editor")
            client, headers = self._authenticated_client(
                "provider_editor",
                "providerpass",
            )

            read_resp = client.get("/api/admin/provider-settings", headers=headers)
            self.assertEqual(read_resp.status_code, 403)

            write_resp = client.put(
                "/api/admin/provider-settings",
                headers=headers,
                json={"ai_provider": "disabled", "action_reason": "provider-denied"},
            )
            self.assertEqual(write_resp.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_provider_settings_get_returns_safe_secret_metadata(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        fernet_key = Fernet.generate_key().decode()
        try:
            with patch.dict(
                "os.environ",
                {"APP_SECRETS_ENCRYPTION_KEY": fernet_key},
                clear=False,
            ):
                conn = db.get_db()
                try:
                    set_database_secret(
                        conn,
                        OPENAI_API_KEY,
                        "openai endpoint secret 1234",
                        updated_by_user_id=self._owner_id,
                    )
                    conn.commit()
                finally:
                    db.return_db(conn)

                _, csrf = self._login_session("test_admin", "testadminpass")
                resp = self.client.get(
                    "/api/admin/provider-settings",
                    headers=self._session_headers(csrf),
                )
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()
            openai_status = data["secrets"]["openai_api_key"]
            self.assertTrue(openai_status["configured"])
            self.assertEqual(openai_status["source"], "db")
            self.assertEqual(openai_status["last4"], "1234")
            self.assertEqual(openai_status["updated_by_username"], "test_admin")
            self.assertNotIn("openai endpoint secret", resp.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_provider_settings_update_encrypts_secret_and_audits_metadata_only(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        fernet_key = Fernet.generate_key().decode()
        secret_value = "sk-test-provider-secret-9876"
        try:
            with patch.dict(
                "os.environ",
                {"APP_SECRETS_ENCRYPTION_KEY": fernet_key},
                clear=False,
            ):
                _, csrf = self._login_session("test_admin", "testadminpass")
                headers = self._session_headers(csrf)
                headers = self._reauth_and_refresh_headers(
                    self.client,
                    headers,
                    password=strong_password("testadminpass"),
                )
                resp = self.client.put(
                    "/api/admin/provider-settings",
                    headers=headers,
                    json={
                        "ai_provider": "openai",
                        "openai_model": "gpt-test-provider",
                        "openai_api_key": secret_value,
                        "action_reason": "provider-settings-test",
                    },
                )

                conn = db.get_db()
                try:
                    stored_secret = get_database_secret(conn, OPENAI_API_KEY)
                    secret_row = conn.execute(
                        """
                        SELECT encrypted_value
                        FROM public.app_secrets
                        WHERE key = %s
                        """,
                        (OPENAI_API_KEY,),
                    ).fetchone()
                    provider_row = conn.execute(
                        "SELECT value FROM app_settings WHERE key = 'ai_provider'",
                    ).fetchone()
                    audit_rows = conn.execute(
                        """
                        SELECT detail
                        FROM audit_events
                        WHERE path = '/api/admin/provider-settings'
                          AND method = 'PUT'
                        """,
                    ).fetchall()
                finally:
                    db.return_db(conn)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        openai_status = data["secrets"]["openai_api_key"]
        self.assertEqual(data["ai_provider"], "openai")
        self.assertEqual(data["models"]["openai_model"], "gpt-test-provider")
        self.assertEqual(openai_status["source"], "db")
        self.assertEqual(openai_status["last4"], "9876")
        self.assertNotIn(secret_value, resp.text)
        self.assertEqual(stored_secret, secret_value)
        self.assertIsNotNone(secret_row)
        encrypted_value = secret_row["encrypted_value"]
        if isinstance(encrypted_value, memoryview):
            encrypted_bytes = encrypted_value.tobytes()
        else:
            encrypted_bytes = bytes(encrypted_value)
        self.assertNotIn(secret_value.encode("utf-8"), encrypted_bytes)
        self.assertEqual(provider_row["value"], "openai")
        self.assertEqual(len(audit_rows), 1)
        self.assertIn("openai_api_key", audit_rows[0]["detail"])
        self.assertNotIn(secret_value, audit_rows[0]["detail"])


class TestAuthUsersCrud(BaseApiTest):
    """Tests for admin user CRUD: list, create, update, delete."""

    def _admin_client(self) -> tuple:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        self._create_test_user("auth_admin", "adminpasswd", "admin")
        return self._authenticated_client("auth_admin", "adminpasswd")

    def test_list_users(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client, headers = self._admin_client()
            resp = client.get("/api/auth/users", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("users", data)
            self.assertIsInstance(data["users"], list)
            self.assertGreaterEqual(len(data["users"]), 1)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_create_user(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client, headers = self._admin_client()
            headers = self._reauth_and_refresh_headers(
                client,
                headers,
                password=strong_password("adminpasswd"),
            )
            resp = client.post(
                "/api/auth/users",
                headers=headers,
                json={
                    "username": "new_user",
                    "password": strong_password("newuserpass"),
                    "role": "viewer",
                    "action_reason": "test create user",
                },
            )
            self.assertEqual(resp.status_code, 201)
            data = resp.json()
            self.assertEqual(data["username"], "new_user")
            self.assertEqual(data["role"], "viewer")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_update_user_role(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client, headers = self._admin_client()
            headers = self._reauth_and_refresh_headers(
                client,
                headers,
                password=strong_password("adminpasswd"),
            )
            self._create_test_user("upd_target", "targetpass", "viewer")
            conn = db.get_db()
            row = conn.execute("SELECT id FROM auth_users WHERE username = 'upd_target'").fetchone()
            db.return_db(conn)
            user_id = int(row["id"])

            resp = client.patch(
                f"/api/auth/users/{user_id}",
                headers=headers,
                json={"role": "editor", "action_reason": "test update role"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["role"], "editor")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_delete_user(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client, headers = self._admin_client()
            headers = self._reauth_and_refresh_headers(
                client,
                headers,
                password=strong_password("adminpasswd"),
            )
            self._create_test_user("del_target", "delpassword", "viewer")
            conn = db.get_db()
            row = conn.execute("SELECT id FROM auth_users WHERE username = 'del_target'").fetchone()
            db.return_db(conn)
            user_id = int(row["id"])

            resp = client.delete(
                f"/api/auth/users/{user_id}",
                headers={
                    **headers,
                    "x-action-reason": "test delete user",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            self.assertEqual(resp.json()["user_id"], user_id)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_list_users_non_admin_denied(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("viewer_usr", "viewerpass", "viewer")
            client, headers = self._authenticated_client(
                "viewer_usr",
                "viewerpass",
            )
            resp = client.get("/api/auth/users", headers=headers)
            self.assertEqual(resp.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestAuthChangePassword(BaseApiTest):
    """Tests for POST /api/auth/change-password."""

    def test_change_password_success(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("pw_user", "oldpassword", "editor")
            client, headers = self._authenticated_client(
                "pw_user",
                "oldpassword",
            )
            resp = client.post(
                "/api/auth/change-password",
                headers=headers,
                json={
                    "current_password": strong_password("oldpassword"),
                    "new_password": strong_password("newpassword"),
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_change_password_wrong_current(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("pw_fail", "realpasswd", "editor")
            client, headers = self._authenticated_client(
                "pw_fail",
                "realpasswd",
            )
            resp = client.post(
                "/api/auth/change-password",
                headers=headers,
                json={
                    "current_password": strong_password("wrongpassword"),
                    "new_password": strong_password("newpassword"),
                },
            )
            self.assertEqual(resp.status_code, 401)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_change_password_same_as_current(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("pw_same", "samepasswd", "editor")
            client, headers = self._authenticated_client(
                "pw_same",
                "samepasswd",
            )
            resp = client.post(
                "/api/auth/change-password",
                headers=headers,
                json={
                    "current_password": strong_password("samepasswd"),
                    "new_password": strong_password("samepasswd"),
                },
            )
            self.assertEqual(resp.status_code, 400)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestAuthSessions(BaseApiTest):
    """Tests for GET /api/auth/sessions."""

    def test_list_sessions_as_admin(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("sess_admin", "adminpasswd", "admin")
            client, headers = self._authenticated_client(
                "sess_admin",
                "adminpasswd",
            )
            resp = client.get("/api/auth/sessions", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("sessions", data)
            self.assertIsInstance(data["sessions"], list)
            self.assertGreaterEqual(len(data["sessions"]), 1)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_list_sessions_non_admin_denied(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("sess_viewer", "viewerpass", "viewer")
            client, headers = self._authenticated_client(
                "sess_viewer",
                "viewerpass",
            )
            resp = client.get("/api/auth/sessions", headers=headers)
            self.assertEqual(resp.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestAuthRevokeUserSessions(BaseApiTest):
    """Tests for POST /api/auth/revoke-user-sessions."""

    def test_revoke_user_sessions(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("revadm", "adminpasswd", "admin")
            self._create_test_user("revtarget", "targetpass", "editor")
            client, headers = self._authenticated_client(
                "revadm",
                "adminpasswd",
            )
            resp = client.post(
                "/api/auth/revoke-user-sessions",
                headers=headers,
                json={
                    "username": "revtarget",
                    "action_reason": "test revocation",
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["username"], "revtarget")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestAuthEmergencyReadOnly(BaseApiTest):
    """Tests for emergency-read-only GET and PATCH."""

    def test_get_emergency_read_only_status(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("ero_admin", "adminpasswd", "admin")
            client, headers = self._authenticated_client(
                "ero_admin",
                "adminpasswd",
            )
            resp = client.get(
                "/api/auth/emergency-read-only",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("enabled", data)
            self.assertIn("expires_at_ms", data)
            self.assertFalse(data["enabled"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_toggle_emergency_read_only(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("ero_toggle", "adminpasswd", "admin")
            client, headers = self._authenticated_client(
                "ero_toggle",
                "adminpasswd",
            )
            resp = client.patch(
                "/api/auth/emergency-read-only",
                headers=headers,
                json={
                    "enabled": True,
                    "expires_in_minutes": 30,
                    "action_reason": "test toggle on",
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["enabled"])
            self.assertIsNotNone(data["expires_at_ms"])

            resp = client.patch(
                "/api/auth/emergency-read-only",
                headers=headers,
                json={
                    "enabled": False,
                    "action_reason": "test toggle off",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["enabled"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_emergency_read_only_non_admin_denied(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("ero_viewer", "viewerpass", "viewer")
            client, headers = self._authenticated_client(
                "ero_viewer",
                "viewerpass",
            )
            resp = client.get(
                "/api/auth/emergency-read-only",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestPasswordPolicy(BaseApiTest):
    """Tests for GET /api/auth/password-policy."""

    # conftest.py disables all password checks for easy test user creation;
    # override the relevant vars within each test to assert real policy behaviour.
    _POLICY_DEFAULTS = {
        "AUTH_PASSWORD_MIN_LENGTH": "30",
        "AUTH_PASSWORD_REQUIRE_LOWER": "true",
        "AUTH_PASSWORD_REQUIRE_UPPER": "true",
        "AUTH_PASSWORD_REQUIRE_DIGIT": "true",
        "AUTH_PASSWORD_REQUIRE_SYMBOL": "true",
        "AUTH_PASSWORD_REJECT_COMMON": "true",
        "AUTH_PASSWORD_DISALLOW_USERNAME": "true",
        "AUTH_PASSWORD_CHECK_HIBP": "true",
    }

    def test_password_policy_returns_defaults(self) -> None:
        with patch.dict(os.environ, self._POLICY_DEFAULTS, clear=False):
            resp = self.client.get("/api/auth/password-policy")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["min_length"], 30)
            self.assertTrue(data["require_lower"])
            self.assertTrue(data["require_upper"])
            self.assertTrue(data["require_digit"])
            self.assertTrue(data["require_symbol"])
            self.assertTrue(data["reject_common"])
            self.assertTrue(data["disallow_username"])
            self.assertTrue(data["check_hibp"])

    def test_password_policy_respects_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                **self._POLICY_DEFAULTS,
                "AUTH_PASSWORD_MIN_LENGTH": "12",
                "AUTH_PASSWORD_REQUIRE_UPPER": "false",
                "AUTH_PASSWORD_REQUIRE_SYMBOL": "false",
                "AUTH_PASSWORD_CHECK_HIBP": "false",
            },
            clear=False,
        ):
            resp = self.client.get("/api/auth/password-policy")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["min_length"], 12)
            self.assertFalse(data["require_upper"])
            self.assertFalse(data["require_symbol"])
            self.assertFalse(data["check_hibp"])
            # These should still be true (not overridden)
            self.assertTrue(data["require_lower"])
            self.assertTrue(data["require_digit"])


class TestInvitationPeek(BaseApiTest):
    """Tests for POST /api/auth/invitations/peek."""

    def test_peek_valid_garden_invitation(self) -> None:
        import os

        self._create_test_user("peek_admin", "peek-admin-pass", "admin")

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session("peek_admin", "peek-admin-pass", client=client)
            headers = self._session_headers(csrf)

            garden_slug = f"peek-garden-{os.urandom(4).hex()}"
            created = client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Peek Garden", "slug": garden_slug},
            )
            self.assertEqual(created.status_code, 201)
            garden_id = int(created.json()["id"])

            headers = self._reauth_and_refresh_headers(
                client,
                headers,
                password=strong_password("peek-admin-pass"),
            )
            invitation = client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "peek_target_user",
                    "role": "viewer",
                    "action_reason": "peek-invitation-test",
                },
            )
            self.assertEqual(invitation.status_code, 201)
            invite_token = invitation.json()["invite_token"]

            # Peek does not require auth — use a fresh client
            peek_client = self._new_client()
            resp = peek_client.post(
                "/api/auth/invitations/peek",
                json={"token": invite_token},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["username"], "peek_target_user")

    def test_peek_invalid_token_returns_400(self) -> None:
        resp = self.client.post(
            "/api/auth/invitations/peek",
            json={"token": "totally-invalid-token-value"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_peek_missing_token_returns_422(self) -> None:
        resp = self.client.post(
            "/api/auth/invitations/peek",
            json={},
        )
        self.assertEqual(resp.status_code, 422)


class TestCheckHibp(BaseApiTest):
    """Tests for POST /api/auth/check-hibp."""

    def test_check_hibp_returns_not_breached(self) -> None:
        with patch("gardenops.security._check_hibp", return_value=False):
            resp = self.client.post(
                "/api/auth/check-hibp",
                json={"password": strong_password("some-very-long-unique-password-!@#456")},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["breached"])

    def test_check_hibp_returns_breached(self) -> None:
        with patch("gardenops.security._check_hibp", return_value=True):
            resp = self.client.post(
                "/api/auth/check-hibp",
                json={"password": strong_password("password123")},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["breached"])

    def test_check_hibp_missing_password_returns_422(self) -> None:
        resp = self.client.post(
            "/api/auth/check-hibp",
            json={},
        )
        self.assertEqual(resp.status_code, 422)

    def test_check_hibp_disabled_returns_not_breached(self) -> None:
        import os

        with patch.dict(
            os.environ,
            {"AUTH_PASSWORD_CHECK_HIBP": "false"},
            clear=False,
        ):
            resp = self.client.post(
                "/api/auth/check-hibp",
                json={"password": strong_password("anything")},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(resp.json()["breached"])
