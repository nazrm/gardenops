from unittest.mock import patch

import gardenops.db as db
from tests.base import BaseApiTest

AUTH_MFA_ENV = {
    "AUTH_REQUIRED": "true",
    "AUTH_MODE": "session",
    "AUTH_API_KEY": "",
    "AUTH_ADMIN_MFA_REQUIRED": "true",
}


class TestAuthMfaStepUp(BaseApiTest):
    def _admin_client(self, username: str):
        self._create_test_user(username, "adminpass", role="admin")
        client = self._new_client()
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": self._strong("adminpass")},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["status"], "mfa_setup_required")
        csrf = client.cookies.get("gardenops_csrf") or ""
        self.assertTrue(csrf)
        return client, self._session_headers(csrf)

    @staticmethod
    def _strong(password: str) -> str:
        from tests.base import strong_password

        return strong_password(password)

    @staticmethod
    def _age_sessions() -> None:
        conn = db.get_db()
        try:
            conn.execute("UPDATE auth_sessions SET reauthenticated_at_ms = 1")
            conn.commit()
        finally:
            db.return_db(conn)

    def test_stale_admin_session_cannot_start_totp_enrollment(self) -> None:
        with patch.dict("os.environ", AUTH_MFA_ENV, clear=False):
            client, headers = self._admin_client("stale_totp_start_admin")
            self._age_sessions()

            response = client.post("/api/auth/mfa/totp/start", headers=headers)

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "Recent reauthentication required")

    def test_stale_admin_session_cannot_confirm_started_totp_enrollment(self) -> None:
        with patch.dict("os.environ", AUTH_MFA_ENV, clear=False):
            client, headers = self._admin_client("stale_totp_confirm_admin")
            start = client.post("/api/auth/mfa/totp/start", headers=headers)
            self.assertEqual(start.status_code, 200, start.text)
            secret = str(start.json()["secret"])
            self._age_sessions()

            response = client.post(
                "/api/auth/mfa/totp/confirm",
                headers=headers,
                json={"code": self._totp_code(secret)},
            )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "Recent reauthentication required")

    def test_pending_totp_enrollment_can_be_cancelled(self) -> None:
        with patch.dict("os.environ", AUTH_MFA_ENV, clear=False):
            client, headers = self._admin_client("cancel_totp_admin")
            start = client.post("/api/auth/mfa/totp/start", headers=headers)
            self.assertEqual(start.status_code, 200, start.text)

            cancelled = client.post(
                "/api/auth/mfa/totp/cancel",
                headers=headers,
                json={"action_reason": "enrollment-abandoned"},
            )

        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertFalse(cancelled.json()["mfa"]["pending_enrollment"])
        conn = db.get_db()
        try:
            pending = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_mfa_pending_enrollments",
            ).fetchone()
            audit = conn.execute(
                "SELECT detail FROM audit_events WHERE path = %s ORDER BY id DESC LIMIT 1",
                ("/api/auth/mfa/totp/cancel",),
            ).fetchone()
            self.assertEqual(int(pending["count"]), 0)
            self.assertIn("auth.mfa.totp.cancel", str(audit["detail"]))
        finally:
            db.return_db(conn)
