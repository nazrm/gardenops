"""Unit tests for gardenops.security — password, CSRF, session, user creation."""

import unittest
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

import gardenops.db as db
from gardenops.security import (
    AuthContext,
    _authenticate_session_token,
    _hash_token,
    _legacy_pbkdf2_hash_password,
    authenticate_user_credentials,
    create_session_for_user,
    create_user,
    csrf_token_for_session_token,
    csrf_token_matches_context,
    hash_password,
    is_loopback_client,
    resolve_request_auth_context,
    validate_password_policy,
    verify_password,
)
from tests.base import BaseApiTest, strong_password


class TestHashVerifyPassword(unittest.TestCase):
    """hash_password / verify_password round-trip."""

    def test_round_trip(self) -> None:
        pw = "Sup3r$ecretP@ss!"
        hashed = hash_password(pw)
        self.assertTrue(verify_password(pw, hashed))

    def test_wrong_password_rejected(self) -> None:
        hashed = hash_password("correct-horse")
        self.assertFalse(verify_password("wrong-horse", hashed))

    def test_hash_format(self) -> None:
        hashed = hash_password("testpass")
        self.assertTrue(hashed.startswith("$argon2id$"))

    def test_different_hashes_same_password(self) -> None:
        """Random salt means different hashes each time."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        self.assertNotEqual(h1, h2)
        self.assertTrue(verify_password("same", h1))
        self.assertTrue(verify_password("same", h2))

    def test_malformed_hash_returns_false(self) -> None:
        self.assertFalse(verify_password("anything", "not-a-valid-hash"))

    def test_wrong_scheme_returns_false(self) -> None:
        self.assertFalse(verify_password("test", "bcrypt$100$salt$hash"))

    def test_legacy_hashes_still_verify(self) -> None:
        hashed = _legacy_pbkdf2_hash_password(strong_password("legacy-pass"))
        self.assertTrue(verify_password(strong_password("legacy-pass"), hashed))
        self.assertFalse(verify_password("wrong-pass", hashed))


class TestValidatePasswordPolicy:
    """validate_password_policy — min length, complexity rules, username check."""

    _env_overrides = {
        "AUTH_PASSWORD_MIN_LENGTH": "10",
        "AUTH_PASSWORD_CHECK_HIBP": "false",
        "AUTH_PASSWORD_REJECT_COMMON": "true",
        "AUTH_PASSWORD_REQUIRE_LOWER": "true",
        "AUTH_PASSWORD_REQUIRE_UPPER": "true",
        "AUTH_PASSWORD_REQUIRE_DIGIT": "true",
        "AUTH_PASSWORD_REQUIRE_SYMBOL": "true",
        "AUTH_PASSWORD_DISALLOW_USERNAME": "true",
    }

    @pytest.mark.parametrize(
        "password,username",
        [
            ("Str0ng!Pass", "alice"),
        ],
    )
    def test_valid_password_passes(self, password: str, username: str) -> None:
        with patch.dict("os.environ", self._env_overrides):
            validate_password_policy(password, username=username)

    @pytest.mark.parametrize(
        "password,username,detail_substr",
        [
            ("Sh0rt!", "alice", "at least 10"),
            ("password123", "alice", "common"),
            ("Password123", "alice", None),
            ("Hello-alice-World1!", "alice", "username"),
            ("ALLUPPERC4SE!", "bob", "lowercase"),
            ("alllowerc4se!", "bob", "uppercase"),
            ("NoDigitsHere!", "bob", "digit"),
            ("NoSymbols1Here", "bob", "symbol"),
        ],
    )
    def test_rejected_password(
        self, password: str, username: str, detail_substr: str | None
    ) -> None:
        with patch.dict("os.environ", self._env_overrides):
            with pytest.raises(HTTPException) as exc_info:
                validate_password_policy(password, username=username)
            assert exc_info.value.status_code == 400
            if detail_substr is not None:
                assert detail_substr in str(exc_info.value.detail).lower()


class TestCsrfTokens(BaseApiTest):
    """csrf_token_for_session_token / csrf_token_matches_context round-trip."""

    def test_round_trip(self) -> None:
        token = "test-session-token-abc123"
        csrf = csrf_token_for_session_token(token)
        self.assertIsInstance(csrf, str)
        self.assertTrue(len(csrf) > 0)

        context = AuthContext(
            user_id=1,
            username="testuser",
            role="editor",
            auth_type="session",
            session_token_hash=_hash_token(token),
        )
        self.assertTrue(csrf_token_matches_context(context, csrf))

    def test_wrong_csrf_rejected(self) -> None:
        token = "real-session-token"
        context = AuthContext(
            user_id=1,
            username="testuser",
            role="editor",
            auth_type="session",
            session_token_hash=_hash_token(token),
        )
        self.assertFalse(csrf_token_matches_context(context, "wrong-csrf-value"))

    def test_no_session_hash_returns_false(self) -> None:
        context = AuthContext(
            user_id=1,
            username="testuser",
            role="editor",
            auth_type="session",
            session_token_hash=None,
        )
        self.assertFalse(csrf_token_matches_context(context, "any"))

    def test_empty_provided_returns_false(self) -> None:
        context = AuthContext(
            user_id=1,
            username="testuser",
            role="editor",
            auth_type="session",
            session_token_hash=_hash_token("tok"),
        )
        self.assertFalse(csrf_token_matches_context(context, ""))


class TestCreateSessionAndAuthenticate(BaseApiTest):
    """create_session_for_user / _authenticate_session_token (needs DB)."""

    def _make_user(self, *, role: str = "editor") -> int:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="session_user",
                password=strong_password("sessionpass"),
                role=role,
            )
            conn.commit()
            return int(user["id"])
        finally:
            db.return_db(conn)

    def test_create_and_authenticate(self) -> None:
        user_id = self._make_user()
        token, expires_at_ms = create_session_for_user(user_id)
        self.assertIsInstance(token, str)
        self.assertGreater(expires_at_ms, db.current_timestamp_ms())

        context = _authenticate_session_token(token)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.user_id, user_id)
        self.assertEqual(context.username, "session_user")
        self.assertEqual(context.auth_type, "session")

    def test_invalid_token_returns_none(self) -> None:
        context = _authenticate_session_token("nonexistent-token")
        self.assertIsNone(context)

    def test_mfa_flags_propagated(self) -> None:
        user_id = self._make_user(role="admin")
        with patch.dict("os.environ", {"AUTH_ADMIN_MFA_REQUIRED": "true"}):
            token, _ = create_session_for_user(
                user_id,
                mfa_authenticated=True,
                mfa_setup_required=True,
            )
            context = _authenticate_session_token(token)
        self.assertIsNotNone(context)
        assert context is not None
        assert context.mfa_authenticated_at_ms is not None
        self.assertGreater(context.mfa_authenticated_at_ms, 0)
        self.assertTrue(context.mfa_setup_required)


class TestResolveRequestAuthContext(unittest.TestCase):
    @staticmethod
    def _make_request() -> StarletteRequest:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/plots/B1/plants",
            "headers": [],
            "client": ("127.0.0.1", 5000),
        }
        return StarletteRequest(scope)

    def test_reuses_request_scoped_db_connection_when_idle(self) -> None:
        request = self._make_request()
        conn = unittest.mock.Mock()
        conn.info.transaction_status = db.TransactionStatus.IDLE
        request.state._db_conn = conn
        base = AuthContext(
            user_id=1,
            username="plot_admin",
            role="admin",
            auth_type="session",
        )
        resolved = AuthContext(
            user_id=1,
            username="plot_admin",
            role="admin",
            auth_type="session",
            garden_id=7,
            garden_role="admin",
        )

        with (
            patch("gardenops.security.validate_request_auth", return_value=base) as validate_auth,
            patch(
                "gardenops.security.resolve_garden_context",
                return_value=resolved,
            ) as resolve_garden,
            patch("gardenops.security.get_db") as get_db,
            patch("gardenops.security.return_db") as return_db,
        ):
            result = resolve_request_auth_context(request)

        self.assertIs(result, resolved)
        self.assertIs(request.state.auth_context, resolved)
        validate_auth.assert_called_once_with(request, conn=conn)
        resolve_garden.assert_called_once_with(conn, request, base)
        conn.commit.assert_called_once_with()
        get_db.assert_not_called()
        return_db.assert_not_called()


class TestAuthenticateUserCredentials(BaseApiTest):
    """authenticate_user_credentials — success and failure (needs DB)."""

    def _make_user(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="cred_user",
                password=strong_password("credpass1"),
                role="editor",
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_valid_credentials(self) -> None:
        self._make_user()
        result = authenticate_user_credentials("cred_user", strong_password("credpass1"))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["username"], "cred_user")
        self.assertEqual(result["role"], "editor")

    def test_valid_credentials_upgrade_legacy_hash(self) -> None:
        self._make_user()
        legacy_hash = _legacy_pbkdf2_hash_password(strong_password("credpass1"))
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET password_hash = %s WHERE username = %s",
                (legacy_hash, "cred_user"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        result = authenticate_user_credentials("cred_user", strong_password("credpass1"))
        self.assertIsNotNone(result)

        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT password_hash FROM auth_users WHERE username = %s",
                ("cred_user",),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertIsNotNone(row)
        updated_hash = str(row["password_hash"])
        self.assertTrue(updated_hash.startswith("$argon2id$"))
        self.assertTrue(verify_password(strong_password("credpass1"), updated_hash))

    def test_wrong_password_returns_none(self) -> None:
        self._make_user()
        result = authenticate_user_credentials("cred_user", strong_password("wrongpass"))
        self.assertIsNone(result)

    def test_nonexistent_user_returns_none(self) -> None:
        result = authenticate_user_credentials("ghost", strong_password("anypass"))
        self.assertIsNone(result)

    def test_inactive_user_returns_none(self) -> None:
        self._make_user()
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET is_active = 0 WHERE username = 'cred_user'",
            )
            conn.commit()
        finally:
            db.return_db(conn)
        result = authenticate_user_credentials("cred_user", strong_password("credpass1"))
        self.assertIsNone(result)


class TestCreateUser(BaseApiTest):
    """create_user creates user correctly (needs DB)."""

    def test_creates_user(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="new_user",
                password=strong_password("newpass12"),
                role="viewer",
            )
            conn.commit()
            self.assertEqual(user["username"], "new_user")
            self.assertEqual(user["role"], "viewer")
            self.assertTrue(user["is_active"])
        finally:
            db.return_db(conn)

    def test_duplicate_username_raises(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="dup_user",
                password=strong_password("duppass12"),
                role="editor",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            with self.assertRaises(HTTPException) as cm:
                create_user(
                    conn,
                    username="dup_user",
                    password=strong_password("duppass99"),
                    role="editor",
                )
            self.assertEqual(cm.exception.status_code, 409)
        finally:
            db.return_db(conn)

    def test_empty_username_raises(self) -> None:
        conn = db.get_db()
        try:
            with self.assertRaises(HTTPException) as cm:
                create_user(conn, username="", password=strong_password("anypass12"), role="editor")
            self.assertEqual(cm.exception.status_code, 400)
        finally:
            db.return_db(conn)

    def test_invalid_role_raises(self) -> None:
        conn = db.get_db()
        try:
            with self.assertRaises(HTTPException) as cm:
                create_user(
                    conn,
                    username="badrole",
                    password=strong_password("anypass12"),
                    role="superadmin",
                )
            self.assertEqual(cm.exception.status_code, 400)
        finally:
            db.return_db(conn)

    def test_must_change_password_flag(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="pwchange_user",
                password=strong_password("changepass"),
                role="editor",
                must_change_password=True,
            )
            conn.commit()
            self.assertTrue(user["must_change_password"])
        finally:
            db.return_db(conn)


class TestIsLoopbackClient:
    """is_loopback_client — various IPs."""

    @staticmethod
    def _make_request(
        host: str,
        *,
        forwarded_for: str = "",
        real_ip: str = "",
    ) -> StarletteRequest:
        headers: list[tuple[bytes, bytes]] = []
        if forwarded_for:
            headers.append((b"x-forwarded-for", forwarded_for.encode()))
        if real_ip:
            headers.append((b"x-real-ip", real_ip.encode()))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (host, 5000),
        }
        return StarletteRequest(scope)

    @pytest.mark.parametrize(
        "host,forwarded_for,real_ip,expected",
        [
            ("127.0.0.1", "", "", True),
            ("::1", "", "", True),
            ("testclient", "", "", True),
            ("localhost", "", "", True),
            ("192.168.1.100", "", "", False),
            ("127.0.0.1", "10.0.0.1", "", False),
            ("127.0.0.1", "", "10.0.0.1", False),
        ],
    )
    def test_loopback_detection(
        self,
        host: str,
        forwarded_for: str,
        real_ip: str,
        expected: bool,
    ) -> None:
        req = self._make_request(host, forwarded_for=forwarded_for, real_ip=real_ip)
        assert is_loopback_client(req) == expected

    def test_no_client_not_loopback(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": None,
        }
        req = StarletteRequest(scope)
        assert is_loopback_client(req) is False
