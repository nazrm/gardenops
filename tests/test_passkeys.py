import base64
import os
from unittest.mock import patch

from fastapi import HTTPException

import gardenops.db as db
import gardenops.passkeys as passkey_service
from gardenops.incident_controls import set_runtime_flag
from gardenops.passkeys import VerifiedPasskeyAuthentication, VerifiedPasskeyRegistration
from tests.base import BaseApiTest, strong_password


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _fake_registration_credential(credential_id: bytes = b"credential-1") -> dict[str, object]:
    credential_id_b64 = _b64url(credential_id)
    return {
        "id": credential_id_b64,
        "rawId": credential_id_b64,
        "type": "public-key",
        "authenticatorAttachment": "platform",
        "clientExtensionResults": {},
        "response": {
            "attestationObject": _b64url(b"attestation-object"),
            "clientDataJSON": _b64url(b"client-data-json"),
            "transports": ["internal", "hybrid"],
        },
    }


def _fake_authentication_credential(credential_id: bytes = b"credential-1") -> dict[str, object]:
    credential_id_b64 = _b64url(credential_id)
    return {
        "id": credential_id_b64,
        "rawId": credential_id_b64,
        "type": "public-key",
        "authenticatorAttachment": "platform",
        "clientExtensionResults": {},
        "response": {
            "authenticatorData": _b64url(b"authenticator-data"),
            "clientDataJSON": _b64url(b"client-data-json"),
            "signature": _b64url(b"signature"),
            "userHandle": "",
        },
    }


class PasskeyApiTest(BaseApiTest):
    def setUp(self) -> None:
        super().setUp()
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_PASSKEY_RP_ID"] = "testserver"
        os.environ["AUTH_PASSKEY_ORIGINS"] = "http://testserver"

    def tearDown(self) -> None:
        os.environ["AUTH_REQUIRED"] = "false"
        os.environ.pop("AUTH_PASSKEY_RP_ID", None)
        os.environ.pop("AUTH_PASSKEY_ORIGINS", None)
        os.environ.pop("AUTH_PASSKEY_REGISTER_RATE_LIMIT", None)
        os.environ.pop("AUTH_API_KEY", None)
        os.environ.pop("AUTH_ADMIN_MFA_REQUIRED", None)
        super().tearDown()

    def _register_passkey(
        self,
        *,
        username: str = "passkey_user",
        password: str = "passkeypass",
        role: str = "editor",
        credential_id: bytes = b"credential-1",
    ) -> tuple[object, dict[str, str], int]:
        self._create_test_user(username, password, role)
        client, headers = self._authenticated_client(username, password)
        options = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop", "current_password": strong_password(password)},
        )
        self.assertEqual(options.status_code, 200, options.text)
        challenge_token = str(options.json()["challenge_token"])
        with patch(
            "gardenops.passkeys.verify_registration_credential",
            return_value=VerifiedPasskeyRegistration(
                credential_id=credential_id,
                credential_public_key=b"public-key",
                sign_count=1,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            verified = client.post(
                "/api/auth/passkeys/register/verify",
                headers=headers,
                json={
                    "challenge_token": challenge_token,
                    "nickname": "Laptop",
                    "credential": _fake_registration_credential(credential_id),
                },
            )
        self.assertEqual(verified.status_code, 201, verified.text)
        passkey_id = int(verified.json()["passkey"]["id"])
        return client, headers, passkey_id


class TestPasskeyRegistration(PasskeyApiTest):
    def test_auth_status_reports_passkey_capability(self) -> None:
        enabled = self.client.get("/api/auth/status")
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["passkeys_enabled"])

        os.environ.pop("AUTH_PASSKEY_ORIGINS", None)

        disabled = self.client.get("/api/auth/status")
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["passkeys_enabled"])

    def test_register_options_requires_csrf_for_cookie_session(self) -> None:
        self._create_test_user("csrf_passkey_user", "csrf-pass", "editor")
        client, _headers = self._authenticated_client("csrf_passkey_user", "csrf-pass")

        response = client.post(
            "/api/auth/passkeys/register/options",
            json={"nickname": "Laptop"},
        )

        self.assertEqual(response.status_code, 403)

    def test_register_options_requires_complete_passkey_config(self) -> None:
        self._create_test_user("missing_config_passkey_user", "missing-config-pass", "editor")
        client, headers = self._authenticated_client(
            "missing_config_passkey_user",
            "missing-config-pass",
        )
        os.environ.pop("AUTH_PASSKEY_ORIGINS", None)

        response = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop", "current_password": strong_password("missing-config-pass")},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Passkey origins are not configured")
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkey_challenges",
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["count"]), 0)

    def test_api_key_auth_cannot_register_passkey(self) -> None:
        os.environ["AUTH_MODE"] = "hybrid"
        os.environ["AUTH_API_KEY"] = "shared-test-key"

        response = self.client.post(
            "/api/auth/passkeys/register/options",
            headers={"x-api-key": "shared-test-key"},
            json={"nickname": "Laptop"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Session auth user is required")

    def test_spoofed_localhost_http_origin_is_rejected(self) -> None:
        os.environ["AUTH_PASSKEY_ORIGINS"] = "http://localhost.evil"

        with self.assertRaises(HTTPException) as raised:
            passkey_service.passkey_origins()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Invalid passkey origin")

    def test_malformed_relying_party_id_is_rejected(self) -> None:
        os.environ["AUTH_PASSKEY_RP_ID"] = "bad host"

        with self.assertRaises(HTTPException) as raised:
            passkey_service.passkey_rp_id()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Invalid passkey relying party ID")

    def test_register_options_requires_recent_session_reauthentication(self) -> None:
        self._create_test_user("stale_passkey_user", "stale-pass", "editor")
        client, headers = self._authenticated_client("stale_passkey_user", "stale-pass")
        conn = db.get_db()
        try:
            conn.execute("UPDATE auth_sessions SET reauthenticated_at_ms = 1")
            conn.commit()
        finally:
            db.return_db(conn)

        response = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop", "current_password": strong_password("stale-pass")},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Recent reauthentication required")

    def test_register_options_requires_current_password_for_fresh_session(self) -> None:
        self._create_test_user("session_only_passkey_user", "session-only-pass", "editor")
        client, headers = self._authenticated_client(
            "session_only_passkey_user",
            "session-only-pass",
        )

        response = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Current password is required")
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkey_challenges",
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["count"]), 0)

    def test_register_options_bad_current_password_is_rate_limited_without_challenge(self) -> None:
        os.environ["AUTH_PASSKEY_REGISTER_RATE_LIMIT"] = "1"
        self._create_test_user("bad_password_passkey_user", "bad-current-pass", "editor")
        client, headers = self._authenticated_client(
            "bad_password_passkey_user",
            "bad-current-pass",
        )

        first = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop", "current_password": strong_password("wrong-password")},
        )
        second = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={
                "nickname": "Laptop",
                "current_password": strong_password("still-wrong-password"),
            },
        )

        self.assertEqual(first.status_code, 401)
        self.assertEqual(first.json()["detail"], "Current password is incorrect")
        self.assertEqual(second.status_code, 429)
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkey_challenges",
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["count"]), 0)

    def test_admin_password_login_cannot_register_additional_passkey(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        username = "admin_second_passkey_blocked"
        password = "admin-second-pass"
        admin_client, _headers, _passkey_id = self._register_passkey(
            username=username,
            password=password,
            role="admin",
        )
        admin_client.post("/api/auth/logout")
        password_client = self._new_client()
        login = password_client.post(
            "/api/auth/login",
            json={"username": username, "password": strong_password(password)},
        )
        self.assertEqual(login.status_code, 200, login.text)
        csrf = password_client.cookies.get("gardenops_csrf") or ""
        self.assertTrue(csrf)
        password_headers = self._session_headers(csrf)
        conn = db.get_db()
        try:
            before = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkey_challenges",
            ).fetchone()
        finally:
            db.return_db(conn)

        response = password_client.post(
            "/api/auth/passkeys/register/options",
            headers=password_headers,
            json={
                "nickname": "Attacker key",
                "current_password": strong_password(password),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Platform-admin MFA or passkey authentication is required",
        )
        conn = db.get_db()
        try:
            passkey_count = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkeys",
            ).fetchone()
            after = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkey_challenges",
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(passkey_count["count"]), 1)
        self.assertEqual(int(after["count"]), int(before["count"]))

    def test_admin_password_login_cannot_read_admin_surfaces_after_passkey_enrollment(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        username = "admin_passkey_read_blocked"
        password = "admin-read-block-pass"
        admin_client, _headers, _passkey_id = self._register_passkey(
            username=username,
            password=password,
            role="admin",
        )
        admin_client.post("/api/auth/logout")
        password_client = self._new_client()
        login = password_client.post(
            "/api/auth/login",
            json={"username": username, "password": strong_password(password)},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["status"], "ok")
        csrf = password_client.cookies.get("gardenops_csrf") or ""
        password_headers = self._session_headers(csrf)

        me = password_client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertFalse(me.json()["mfa_setup_required"])
        self.assertFalse(me.json()["mfa_authenticated"])
        self.assertIn("passkey", me.json()["mfa_methods"])

        users = password_client.get("/api/auth/users", headers=password_headers)

        self.assertEqual(users.status_code, 403)
        self.assertEqual(
            users.json()["detail"],
            "Platform-admin MFA or passkey authentication is required",
        )

    def test_admin_password_login_cannot_use_non_auth_platform_admin_routes(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        username = "admin_passkey_garden_blocked"
        password = "admin-garden-block-pass"
        admin_client, _headers, _passkey_id = self._register_passkey(
            username=username,
            password=password,
            role="admin",
        )
        admin_client.post("/api/auth/logout")
        password_client = self._new_client()
        login = password_client.post(
            "/api/auth/login",
            json={"username": username, "password": strong_password(password)},
        )
        self.assertEqual(login.status_code, 200, login.text)
        csrf = password_client.cookies.get("gardenops_csrf") or ""
        password_headers = self._session_headers(csrf)

        gardens = password_client.get("/api/gardens", headers=password_headers)
        create_garden = password_client.post(
            "/api/gardens",
            headers=password_headers,
            json={"name": "Blocked Garden"},
        )

        self.assertEqual(gardens.status_code, 403)
        self.assertEqual(
            gardens.json()["detail"],
            "Platform-admin MFA or passkey authentication is required",
        )
        self.assertEqual(create_garden.status_code, 403)
        self.assertEqual(
            create_garden.json()["detail"],
            "Platform-admin MFA or passkey authentication is required",
        )

    def test_admin_password_login_cannot_replace_existing_passkey(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        username = "admin_passkey_replace_blocked"
        password = "admin-replace-block-pass"
        admin_client, _headers, passkey_id = self._register_passkey(
            username=username,
            password=password,
            role="admin",
        )
        admin_client.post("/api/auth/logout")
        password_client = self._new_client()
        login = password_client.post(
            "/api/auth/login",
            json={"username": username, "password": strong_password(password)},
        )
        self.assertEqual(login.status_code, 200, login.text)
        csrf = password_client.cookies.get("gardenops_csrf") or ""
        password_headers = self._session_headers(csrf)
        conn = db.get_db()
        try:
            challenges_before = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM auth_passkey_challenges
                WHERE flow = 'registration'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)

        listed = password_client.get("/api/auth/passkeys", headers=password_headers)
        deleted = password_client.request(
            "DELETE",
            f"/api/auth/passkeys/{passkey_id}",
            headers=password_headers,
            json={"action_reason": "password-only-passkey-replace"},
        )
        registered = password_client.post(
            "/api/auth/passkeys/register/options",
            headers=password_headers,
            json={
                "nickname": "Replacement",
                "current_password": strong_password(password),
            },
        )

        for response in (listed, deleted, registered):
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(
                response.json()["detail"],
                "Platform-admin MFA or passkey authentication is required",
            )
        conn = db.get_db()
        try:
            passkey_count = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkeys",
            ).fetchone()
            challenge_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM auth_passkey_challenges
                WHERE flow = 'registration'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(passkey_count["count"]), 1)
        self.assertEqual(int(challenge_count["count"]), int(challenges_before["count"]))

    def test_admin_first_passkey_setup_does_not_unlock_destructive_actions(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        admin_client, headers, _passkey_id = self._register_passkey(
            username="admin_first_passkey_setup",
            password="admin-first-pass",
            role="admin",
        )

        me = admin_client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertFalse(me.json()["mfa_setup_required"])
        self.assertFalse(me.json()["mfa_authenticated"])

        destructive = admin_client.post(
            "/api/auth/revoke-all-sessions",
            headers=headers,
            json={"action_reason": "first-passkey-setup-must-not-step-up"},
        )
        self.assertEqual(destructive.status_code, 403)
        self.assertEqual(
            destructive.json()["detail"],
            "Platform-admin MFA or passkey authentication is required",
        )

    def test_register_options_returns_required_user_verified_resident_key_options(self) -> None:
        self._create_test_user("options_passkey_user", "options-pass", "editor")
        client, headers = self._authenticated_client("options_passkey_user", "options-pass")

        response = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop", "current_password": strong_password("options-pass")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        public_key = body["publicKey"]
        self.assertTrue(body["challenge_token"])
        self.assertEqual(public_key["rp"]["id"], "testserver")
        self.assertEqual(public_key["attestation"], "none")
        self.assertEqual(public_key["authenticatorSelection"]["residentKey"], "required")
        self.assertEqual(public_key["authenticatorSelection"]["userVerification"], "required")

    def test_register_verify_persists_verified_passkey_and_marks_challenge_used(self) -> None:
        client, _headers, passkey_id = self._register_passkey()

        list_response = client.get("/api/auth/passkeys")
        self.assertEqual(list_response.status_code, 200, list_response.text)
        passkeys = list_response.json()["passkeys"]
        self.assertEqual(len(passkeys), 1)
        self.assertEqual(passkeys[0]["id"], passkey_id)
        self.assertEqual(passkeys[0]["nickname"], "Laptop")
        self.assertEqual(passkeys[0]["credential_device_type"], "multi_device")
        self.assertTrue(passkeys[0]["credential_backed_up"])
        self.assertIn("internal", passkeys[0]["transports"])

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT credential_id, credential_public_key, sign_count
                FROM auth_passkeys
                WHERE id = %s
                """,
                (passkey_id,),
            ).fetchone()
            used_challenges = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM auth_passkey_challenges
                WHERE used_at_ms IS NOT NULL
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["credential_id"], _b64url(b"credential-1"))
        self.assertEqual(row["credential_public_key"], _b64url(b"public-key"))
        self.assertEqual(int(row["sign_count"]), 1)
        self.assertEqual(int(used_challenges["count"]), 1)

    def test_register_verify_rejects_challenge_replay(self) -> None:
        self._create_test_user("replay_passkey_user", "replay-pass", "editor")
        client, headers = self._authenticated_client("replay_passkey_user", "replay-pass")
        options = client.post(
            "/api/auth/passkeys/register/options",
            headers=headers,
            json={"nickname": "Laptop", "current_password": strong_password("replay-pass")},
        )
        self.assertEqual(options.status_code, 200, options.text)
        challenge_token = str(options.json()["challenge_token"])
        payload = {
            "challenge_token": challenge_token,
            "nickname": "Laptop",
            "credential": _fake_registration_credential(),
        }

        with patch(
            "gardenops.passkeys.verify_registration_credential",
            return_value=VerifiedPasskeyRegistration(
                credential_id=b"credential-1",
                credential_public_key=b"public-key",
                sign_count=1,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            first = client.post(
                "/api/auth/passkeys/register/verify",
                headers=headers,
                json=payload,
            )
            second = client.post(
                "/api/auth/passkeys/register/verify",
                headers=headers,
                json=payload,
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["detail"], "Invalid or expired passkey challenge")

    def test_register_verify_rejects_cross_session_challenge_reuse(self) -> None:
        self._create_test_user("cross_session_passkey_user", "cross-session-pass", "editor")
        first_client, first_headers = self._authenticated_client(
            "cross_session_passkey_user",
            "cross-session-pass",
        )
        second_client, second_headers = self._authenticated_client(
            "cross_session_passkey_user",
            "cross-session-pass",
        )
        options = first_client.post(
            "/api/auth/passkeys/register/options",
            headers=first_headers,
            json={"nickname": "Laptop", "current_password": strong_password("cross-session-pass")},
        )
        self.assertEqual(options.status_code, 200, options.text)

        with patch(
            "gardenops.passkeys.verify_registration_credential",
            return_value=VerifiedPasskeyRegistration(
                credential_id=b"credential-cross-session",
                credential_public_key=b"public-key",
                sign_count=1,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ) as verify_mock:
            response = second_client.post(
                "/api/auth/passkeys/register/verify",
                headers=second_headers,
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "nickname": "Laptop",
                    "credential": _fake_registration_credential(b"credential-cross-session"),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid or expired passkey challenge")
        verify_mock.assert_not_called()


class TestPasskeyLogin(PasskeyApiTest):
    def test_login_options_require_username(self) -> None:
        for payload in ({}, {"username": ""}, {"username": "   "}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/auth/passkeys/login/options", json=payload)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], "Username is required")

    def test_login_options_do_not_reveal_username_scoped_credentials(self) -> None:
        client, _headers, _passkey_id = self._register_passkey()
        client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "passkey_user"},
        )

        self.assertEqual(options.status_code, 200, options.text)
        self.assertFalse(options.json()["publicKey"].get("allowCredentials"))

    def test_login_options_do_not_reveal_missing_passkey_users(self) -> None:
        self._create_test_user("password_only_passkey_login_user", "password-only", "editor")
        self._create_test_user("inactive_passkey_login_user", "inactive-pass", "editor")
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET is_active = 0 WHERE username = %s",
                ("inactive_passkey_login_user",),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        for username in (
            "missing_passkey_login_user",
            "password_only_passkey_login_user",
            "inactive_passkey_login_user",
        ):
            with self.subTest(username=username):
                options = self.client.post(
                    "/api/auth/passkeys/login/options",
                    json={"username": username},
                )

                self.assertEqual(options.status_code, 200, options.text)
                body = options.json()
                self.assertTrue(body.get("challenge_token"))
                self.assertIn("publicKey", body)
                self.assertFalse(body["publicKey"].get("allowCredentials"))

    def test_denied_passkey_challenge_cannot_authenticate_other_credential(self) -> None:
        client, _headers, _passkey_id = self._register_passkey()
        client.post("/api/auth/logout")
        self._create_test_user(
            "password_only_denied_passkey_login_user",
            "password-only-denied",
            "editor",
        )
        self._create_test_user("inactive_denied_passkey_login_user", "inactive-denied", "editor")
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET is_active = 0 WHERE username = %s",
                ("inactive_denied_passkey_login_user",),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        for username in (
            "missing_passkey_login_user",
            "password_only_denied_passkey_login_user",
            "inactive_denied_passkey_login_user",
        ):
            with self.subTest(username=username):
                options = self.client.post(
                    "/api/auth/passkeys/login/options",
                    json={"username": username},
                )
                self.assertEqual(options.status_code, 200, options.text)

                with patch("gardenops.passkeys.verify_authentication_credential") as verify_mock:
                    login = self.client.post(
                        "/api/auth/passkeys/login/verify",
                        json={
                            "challenge_token": str(options.json()["challenge_token"]),
                            "credential": _fake_authentication_credential(),
                        },
                    )

                self.assertEqual(login.status_code, 401)
                self.assertEqual(login.json()["detail"], "Invalid passkey authentication")
                verify_mock.assert_not_called()

    def test_public_passkey_verify_failure_does_not_reveal_enrollment(self) -> None:
        client, _headers, _passkey_id = self._register_passkey()
        client.post("/api/auth/logout")
        self._create_test_user("password_only_verify_user", "password-only", "editor")

        failures: list[tuple[int, str]] = []
        for username in (
            "passkey_user",
            "password_only_verify_user",
            "missing_verify_user",
        ):
            with self.subTest(username=username):
                options = self.client.post(
                    "/api/auth/passkeys/login/options",
                    json={"username": username},
                )
                self.assertEqual(options.status_code, 200, options.text)
                response = self.client.post(
                    "/api/auth/passkeys/login/verify",
                    json={
                        "challenge_token": str(options.json()["challenge_token"]),
                        "credential": {},
                    },
                )
                failures.append((response.status_code, response.json()["detail"]))

        self.assertEqual(
            failures,
            [
                (401, "Invalid passkey authentication"),
                (401, "Invalid passkey authentication"),
                (401, "Invalid passkey authentication"),
            ],
        )

    def test_legacy_global_authentication_challenge_cannot_authenticate_credential(self) -> None:
        client, _headers, _passkey_id = self._register_passkey()
        client.post("/api/auth/logout")
        challenge_token = "legacy-global-authentication-challenge-token"
        conn = db.get_db()
        try:
            now_ms = passkey_service.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO auth_passkey_challenges (
                    token_hash,
                    challenge,
                    flow,
                    user_id,
                    session_token_hash,
                    created_at_ms,
                    expires_at_ms,
                    used_at_ms
                )
                VALUES (%s, %s, 'authentication', NULL, NULL, %s, %s, NULL)
                """,
                (
                    passkey_service._hash_token(challenge_token),
                    passkey_service._b64encode(b"legacy-global-authentication-challenge"),
                    now_ms,
                    now_ms + 60000,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch("gardenops.passkeys.verify_authentication_credential") as verify_mock:
            login = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": challenge_token,
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(login.status_code, 401)
        self.assertEqual(login.json()["detail"], "Invalid passkey authentication")
        verify_mock.assert_not_called()

    def test_username_scoped_passkey_challenge_rejects_other_user_credential(self) -> None:
        first_client, _first_headers, _first_passkey_id = self._register_passkey(
            username="first_passkey_login_user",
            password="first-passkey-login-pass",
            credential_id=b"first-passkey-credential",
        )
        second_client, _second_headers, _second_passkey_id = self._register_passkey(
            username="second_passkey_login_user",
            password="second-passkey-login-pass",
            credential_id=b"second-passkey-credential",
        )
        first_client.post("/api/auth/logout")
        second_client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "first_passkey_login_user"},
        )
        self.assertEqual(options.status_code, 200, options.text)

        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ) as verify_mock:
            login = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(
                        b"second-passkey-credential",
                    ),
                },
            )

        self.assertEqual(login.status_code, 401)
        self.assertEqual(login.json()["detail"], "Invalid passkey authentication")
        verify_mock.assert_not_called()

    def test_passkey_login_creates_session_and_updates_credential_counter(self) -> None:
        client, _headers, passkey_id = self._register_passkey()
        client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "passkey_user"},
        )
        self.assertEqual(options.status_code, 200, options.text)
        challenge_token = str(options.json()["challenge_token"])
        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=5,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            login = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": challenge_token,
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["status"], "ok")
        self.assertTrue(self.client.cookies.get("gardenops_session"))
        self.assertTrue(self.client.cookies.get("gardenops_csrf"))
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["username"], "passkey_user")

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT sign_count, last_used_at_ms
                FROM auth_passkeys
                WHERE id = %s
                """,
                (passkey_id,),
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(int(row["sign_count"]), 5)
        self.assertIsNotNone(row["last_used_at_ms"])

    def test_passkey_login_rejects_non_advancing_nonzero_sign_counter(self) -> None:
        client, _headers, _passkey_id = self._register_passkey()
        client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "passkey_user"},
        )
        self.assertEqual(options.status_code, 200, options.text)
        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=1,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            login = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(login.status_code, 401)
        self.assertEqual(login.json()["detail"], "Invalid passkey authentication")

    def test_passkey_login_rejects_zero_counter_downgrade(self) -> None:
        client, _headers, passkey_id = self._register_passkey()
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_passkeys SET sign_count = 5 WHERE id = %s",
                (passkey_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "passkey_user"},
        )
        self.assertEqual(options.status_code, 200, options.text)
        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=0,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            login = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(login.status_code, 401)
        self.assertEqual(login.json()["detail"], "Invalid passkey authentication")
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT sign_count FROM auth_passkeys WHERE id = %s",
                (passkey_id,),
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["sign_count"]), 5)

    def test_malformed_passkey_login_verify_burns_challenge(self) -> None:
        client, _headers, _passkey_id = self._register_passkey()
        client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "passkey_user"},
        )
        self.assertEqual(options.status_code, 200, options.text)
        challenge_token = str(options.json()["challenge_token"])

        malformed = self.client.post(
            "/api/auth/passkeys/login/verify",
            json={"challenge_token": challenge_token, "credential": {}},
        )
        self.assertEqual(malformed.status_code, 401)
        self.assertEqual(malformed.json()["detail"], "Invalid passkey authentication")

        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ) as verify_mock:
            replay = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": challenge_token,
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(replay.status_code, 401)
        self.assertEqual(replay.json()["detail"], "Invalid passkey authentication")
        verify_mock.assert_not_called()

    def test_admin_passkey_login_satisfies_strong_admin_session_controls(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        admin_client, _headers, _passkey_id = self._register_passkey(
            username="admin_passkey_user",
            password="admin-pass",
            role="admin",
        )
        admin_client.post("/api/auth/logout")

        options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "admin_passkey_user"},
        )
        self.assertEqual(options.status_code, 200, options.text)
        self.assertFalse(options.json()["publicKey"].get("allowCredentials"))
        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            login = self.client.post(
                "/api/auth/passkeys/login/verify",
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )
        self.assertEqual(login.status_code, 200, login.text)
        csrf = self.client.cookies.get("gardenops_csrf") or ""
        self.assertTrue(csrf)

        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["username"], "admin_passkey_user")
        self.assertFalse(me.json()["mfa_setup_required"])
        settings = self.client.get("/api/auth/me/settings")
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertFalse(settings.json()["mfa"]["setup_required"])
        self.assertIn("passkey", settings.json()["mfa"]["methods"])

        destructive = self.client.post(
            "/api/auth/revoke-all-sessions",
            headers={"x-csrf-token": csrf},
            json={"action_reason": "passkey-backed-admin-session-test"},
        )
        self.assertEqual(destructive.status_code, 200, destructive.text)

    def test_passkey_reauthenticate_marks_session_strong_for_admin_step_up(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        admin_client, headers, _passkey_id = self._register_passkey(
            username="admin_passkey_reauth_user",
            password="admin-passkey-reauth-pass",
            role="admin",
        )

        blocked = admin_client.post(
            "/api/auth/revoke-all-sessions",
            headers=headers,
            json={"action_reason": "passkey-reauth-before-step-up"},
        )
        self.assertEqual(blocked.status_code, 403)

        options = admin_client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=headers,
            json={},
        )
        self.assertEqual(options.status_code, 200, options.text)
        challenge_token = str(options.json()["challenge_token"])
        self.assertTrue(options.json()["publicKey"].get("allowCredentials"))

        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            verified = admin_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=headers,
                json={
                    "challenge_token": challenge_token,
                    "credential": _fake_authentication_credential(),
                },
            )
        self.assertEqual(verified.status_code, 200, verified.text)
        body = verified.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["csrf_token"])
        self.assertGreater(int(body["reauthenticated_at_ms"]), 0)
        self.assertGreater(int(body["mfa_authenticated_at_ms"]), 0)
        refreshed_headers = self._session_headers(str(body["csrf_token"]))

        destructive = admin_client.post(
            "/api/auth/revoke-all-sessions",
            headers=refreshed_headers,
            json={"action_reason": "passkey-reauth-after-step-up"},
        )
        self.assertEqual(destructive.status_code, 200, destructive.text)

    def test_passkey_reauthenticate_requires_csrf_before_challenge_creation(self) -> None:
        admin_client, _headers, _passkey_id = self._register_passkey(
            username="csrf_reauth_passkey_user",
            password="csrf-reauth-pass",
            role="admin",
        )

        response = admin_client.post("/api/auth/reauthenticate/passkey/options", json={})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Forbidden: invalid or missing CSRF token")
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM auth_passkey_challenges
                WHERE flow = 'reauthentication'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["count"]), 0)

    def test_passkey_reauthenticate_rotates_session_and_rejects_replay(self) -> None:
        admin_client, headers, _passkey_id = self._register_passkey(
            username="reauth_rotate_passkey_user",
            password="reauth-rotate-pass",
            role="admin",
        )
        old_session = admin_client.cookies.get("gardenops_session") or ""
        old_csrf = headers["x-csrf-token"]
        options = admin_client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=headers,
            json={},
        )
        self.assertEqual(options.status_code, 200, options.text)
        challenge_token = str(options.json()["challenge_token"])

        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            verified = admin_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=headers,
                json={
                    "challenge_token": challenge_token,
                    "credential": _fake_authentication_credential(),
                },
            )
        self.assertEqual(verified.status_code, 200, verified.text)
        new_csrf = str(verified.json()["csrf_token"])
        self.assertNotEqual(new_csrf, old_csrf)

        old_session_client = self._new_client()
        old_session_client.cookies.set("gardenops_session", old_session)
        old_me = old_session_client.get("/api/auth/me")
        self.assertEqual(old_me.status_code, 401)

        old_csrf_rejected = admin_client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=headers,
            json={},
        )
        self.assertEqual(old_csrf_rejected.status_code, 403)
        self.assertEqual(
            old_csrf_rejected.json()["detail"],
            "Forbidden: invalid or missing CSRF token",
        )

        with patch("gardenops.passkeys.verify_authentication_credential") as verify_mock:
            replay = admin_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=self._session_headers(new_csrf),
                json={
                    "challenge_token": challenge_token,
                    "credential": _fake_authentication_credential(),
                },
            )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"], "Invalid or expired passkey challenge")
        verify_mock.assert_not_called()

    def test_passkey_reauthenticate_allows_emergency_read_only_recovery(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        admin_client, headers, _passkey_id = self._register_passkey(
            username="emergency_reauth_passkey_user",
            password="emergency-reauth-pass",
            role="admin",
        )
        conn = db.get_db()
        try:
            set_runtime_flag(conn, "emergency_read_only", "1")
            set_runtime_flag(conn, "emergency_read_only_expires_at_ms", "0")
            conn.commit()
        finally:
            db.return_db(conn)

        options = admin_client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=headers,
            json={},
        )
        self.assertEqual(options.status_code, 200, options.text)
        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            verified = admin_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=headers,
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )
        self.assertEqual(verified.status_code, 200, verified.text)

        disabled = admin_client.patch(
            "/api/auth/emergency-read-only",
            headers=self._session_headers(str(verified.json()["csrf_token"])),
            json={
                "enabled": False,
                "action_reason": "passkey-emergency-disable",
            },
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)

    def test_passkey_reauthenticate_verify_rejects_public_login_challenge(self) -> None:
        admin_client, headers, _passkey_id = self._register_passkey(
            username="public_challenge_reauth_user",
            password="public-challenge-pass",
            role="admin",
        )
        public_options = self.client.post(
            "/api/auth/passkeys/login/options",
            json={"username": "public_challenge_reauth_user"},
        )
        self.assertEqual(public_options.status_code, 200, public_options.text)

        with patch("gardenops.passkeys.verify_authentication_credential") as verify_mock:
            response = admin_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=headers,
                json={
                    "challenge_token": str(public_options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid or expired passkey challenge")
        verify_mock.assert_not_called()

    def test_passkey_reauthenticate_verify_rejects_cross_session_challenge_reuse(self) -> None:
        username = "cross_session_reauth_user"
        password = "cross-session-reauth-pass"
        first_client, first_headers, _passkey_id = self._register_passkey(
            username=username,
            password=password,
            role="admin",
        )
        second_client, second_headers = self._authenticated_client(username, password)
        options = first_client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=first_headers,
            json={},
        )
        self.assertEqual(options.status_code, 200, options.text)

        with patch("gardenops.passkeys.verify_authentication_credential") as verify_mock:
            response = second_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=second_headers,
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid or expired passkey challenge")
        verify_mock.assert_not_called()

    def test_passkey_reauthenticate_options_requires_existing_passkey(self) -> None:
        self._create_test_user("no_reauth_passkey_user", "no-reauth-passkey", "editor")
        client, headers = self._authenticated_client("no_reauth_passkey_user", "no-reauth-passkey")

        response = client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Passkey authentication is not available")

    def test_deleting_admin_only_passkey_restores_setup_required_state(self) -> None:
        os.environ["AUTH_ADMIN_MFA_REQUIRED"] = "true"
        admin_client, headers, passkey_id = self._register_passkey(
            username="admin_delete_passkey_user",
            password="admin-delete-pass",
            role="admin",
        )
        options = admin_client.post(
            "/api/auth/reauthenticate/passkey/options",
            headers=headers,
            json={},
        )
        self.assertEqual(options.status_code, 200, options.text)
        with patch(
            "gardenops.passkeys.verify_authentication_credential",
            return_value=VerifiedPasskeyAuthentication(
                new_sign_count=2,
                credential_device_type="multi_device",
                credential_backed_up=True,
            ),
        ):
            verified = admin_client.post(
                "/api/auth/reauthenticate/passkey/verify",
                headers=headers,
                json={
                    "challenge_token": str(options.json()["challenge_token"]),
                    "credential": _fake_authentication_credential(),
                },
            )
        self.assertEqual(verified.status_code, 200, verified.text)
        headers = self._session_headers(str(verified.json()["csrf_token"]))

        delete_response = admin_client.request(
            "DELETE",
            f"/api/auth/passkeys/{passkey_id}",
            headers=headers,
            json={"action_reason": "admin-last-passkey-delete-test"},
        )
        self.assertEqual(delete_response.status_code, 200, delete_response.text)

        me = admin_client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertTrue(me.json()["mfa_setup_required"])
        settings = admin_client.get("/api/auth/me/settings")
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertTrue(settings.json()["mfa"]["setup_required"])
        self.assertNotIn("passkey", settings.json()["mfa"]["methods"])

        destructive = admin_client.post(
            "/api/auth/revoke-all-sessions",
            headers=headers,
            json={"action_reason": "post-delete-passkey-admin-action"},
        )
        self.assertEqual(destructive.status_code, 403)
