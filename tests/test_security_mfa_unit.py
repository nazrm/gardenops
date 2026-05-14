"""Unit tests for gardenops.security_mfa — encryption, TOTP, recovery codes, enrollment."""

import base64
import unittest
from urllib.parse import parse_qs, urlparse

import gardenops.db as db
from gardenops.security import create_user
from gardenops.security_mfa import (
    _totp_at,
    _totp_period_seconds,
    confirm_totp_enrollment,
    decrypt_mfa_secret,
    disable_totp_mfa,
    encrypt_mfa_secret,
    generate_recovery_codes,
    generate_totp_secret,
    get_user_mfa_status,
    normalize_recovery_code,
    normalize_totp_code,
    start_totp_enrollment,
    totp_provisioning_uri,
    verify_totp,
    verify_totp_with_replay_protection,
    verify_user_second_factor,
)
from tests.base import BaseApiTest, strong_password


class TestEncryptDecryptRoundTrip(BaseApiTest):
    """encrypt_mfa_secret / decrypt_mfa_secret round-trip."""

    def test_round_trip_short_string(self) -> None:
        conn = db.get_db()
        try:
            secret = "JBSWY3DPEHPK3PXP"
            token = encrypt_mfa_secret(conn, secret)
            self.assertIsInstance(token, str)
            self.assertNotEqual(token, secret)
            decrypted = decrypt_mfa_secret(conn, token)
            self.assertEqual(decrypted, secret)
        finally:
            db.return_db(conn)

    def test_round_trip_long_string(self) -> None:
        conn = db.get_db()
        try:
            secret = "A" * 200
            token = encrypt_mfa_secret(conn, secret)
            self.assertEqual(decrypt_mfa_secret(conn, token), secret)
        finally:
            db.return_db(conn)

    def test_different_ciphertexts_same_plaintext(self) -> None:
        """Each encryption produces a unique nonce, so ciphertexts differ."""
        conn = db.get_db()
        try:
            secret = "TESTSECRET"
            t1 = encrypt_mfa_secret(conn, secret)
            t2 = encrypt_mfa_secret(conn, secret)
            self.assertNotEqual(t1, t2)
            self.assertEqual(decrypt_mfa_secret(conn, t1), secret)
            self.assertEqual(decrypt_mfa_secret(conn, t2), secret)
        finally:
            db.return_db(conn)

    def test_decrypt_invalid_payload_raises(self) -> None:
        conn = db.get_db()
        try:
            with self.assertRaises(Exception):
                decrypt_mfa_secret(conn, "x")
        finally:
            db.return_db(conn)


class TestVerifyTotp(unittest.TestCase):
    """verify_totp with valid/invalid codes."""

    def test_valid_code_current_counter(self) -> None:
        secret = generate_totp_secret()
        counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
        code = _totp_at(secret, counter)
        self.assertTrue(verify_totp(secret, code))

    def test_invalid_code_rejected(self) -> None:
        secret = generate_totp_secret()
        self.assertFalse(verify_totp(secret, "000000"))

    def test_wrong_length_rejected(self) -> None:
        secret = generate_totp_secret()
        self.assertFalse(verify_totp(secret, "12345"))

    def test_code_with_spaces_normalized(self) -> None:
        secret = generate_totp_secret()
        counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
        code = _totp_at(secret, counter)
        spaced = f" {code[:3]} {code[3:]} "
        self.assertTrue(verify_totp(secret, spaced))

    def test_code_at_explicit_time(self) -> None:
        secret = generate_totp_secret()
        now_ms = 1_700_000_000_000
        counter = now_ms // (_totp_period_seconds() * 1000)
        code = _totp_at(secret, counter)
        self.assertTrue(verify_totp(secret, code, now_ms=now_ms))


class TestVerifyTotpWithReplayProtection(BaseApiTest):
    """verify_totp_with_replay_protection — replay rejection."""

    def _make_user(self) -> int:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="replay_user",
                password=strong_password("replaypass"),
                role="editor",
            )
            conn.commit()
            return int(user["id"])
        finally:
            db.return_db(conn)

    def test_first_use_accepted(self) -> None:
        user_id = self._make_user()
        secret = generate_totp_secret()
        now_ms = 1_700_000_000_000
        counter = now_ms // (_totp_period_seconds() * 1000)
        code = _totp_at(secret, counter)
        conn = db.get_db()
        try:
            ok = verify_totp_with_replay_protection(
                conn,
                user_id=user_id,
                secret=secret,
                code=code,
                now_ms=now_ms,
            )
            conn.commit()
            self.assertTrue(ok)
        finally:
            db.return_db(conn)

    def test_replay_rejected(self) -> None:
        user_id = self._make_user()
        secret = generate_totp_secret()
        now_ms = 1_700_000_000_000
        counter = now_ms // (_totp_period_seconds() * 1000)
        code = _totp_at(secret, counter)

        conn = db.get_db()
        try:
            self.assertTrue(
                verify_totp_with_replay_protection(
                    conn,
                    user_id=user_id,
                    secret=secret,
                    code=code,
                    now_ms=now_ms,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            self.assertFalse(
                verify_totp_with_replay_protection(
                    conn,
                    user_id=user_id,
                    secret=secret,
                    code=code,
                    now_ms=now_ms,
                ),
            )
        finally:
            db.return_db(conn)

    def test_next_counter_accepted_after_replay(self) -> None:
        user_id = self._make_user()
        secret = generate_totp_secret()
        now_ms = 1_700_000_000_000
        period_ms = _totp_period_seconds() * 1000
        counter = now_ms // period_ms
        code1 = _totp_at(secret, counter)

        conn = db.get_db()
        try:
            verify_totp_with_replay_protection(
                conn,
                user_id=user_id,
                secret=secret,
                code=code1,
                now_ms=now_ms,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        next_ms = (counter + 1) * period_ms
        code2 = _totp_at(secret, counter + 1)
        conn = db.get_db()
        try:
            ok = verify_totp_with_replay_protection(
                conn,
                user_id=user_id,
                secret=secret,
                code=code2,
                now_ms=next_ms,
            )
            conn.commit()
            self.assertTrue(ok)
        finally:
            db.return_db(conn)


class TestGenerateTotpSecret(unittest.TestCase):
    """generate_totp_secret returns valid base32 string."""

    def test_returns_valid_base32(self) -> None:
        secret = generate_totp_secret()
        self.assertIsInstance(secret, str)
        padding = "=" * ((8 - (len(secret) % 8)) % 8)
        decoded = base64.b32decode(secret + padding, casefold=True)
        self.assertEqual(len(decoded), 20)

    def test_unique_each_call(self) -> None:
        secrets_set = {generate_totp_secret() for _ in range(20)}
        self.assertEqual(len(secrets_set), 20)


class TestGenerateRecoveryCodes(unittest.TestCase):
    """generate_recovery_codes — uniqueness, correct count."""

    def test_default_count(self) -> None:
        codes = generate_recovery_codes()
        self.assertEqual(len(codes), 10)

    def test_format_xxxx_xxxx(self) -> None:
        codes = generate_recovery_codes()
        for code in codes:
            parts = code.split("-")
            self.assertEqual(len(parts), 2)
            self.assertEqual(len(parts[0]), 4)
            self.assertEqual(len(parts[1]), 4)

    def test_all_unique(self) -> None:
        codes = generate_recovery_codes()
        self.assertEqual(len(set(codes)), len(codes))

    def test_characters_from_alphabet(self) -> None:
        allowed = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789-")
        codes = generate_recovery_codes()
        for code in codes:
            self.assertTrue(set(code).issubset(allowed), f"Bad chars in {code}")


class TestNormalize(unittest.TestCase):
    """normalize_totp_code and normalize_recovery_code."""

    def test_normalize_totp_strips_non_digits(self) -> None:
        self.assertEqual(normalize_totp_code(" 12 34 56 "), "123456")

    def test_normalize_recovery_strips_dashes(self) -> None:
        self.assertEqual(normalize_recovery_code("abcd-1234"), "ABCD1234")


class TestTotpProvisioningUri(unittest.TestCase):
    """totp_provisioning_uri format check."""

    def test_uri_format(self) -> None:
        uri = totp_provisioning_uri(username="alice", secret="JBSWY3DPEHPK3PXP")
        parsed = urlparse(uri)
        params = parse_qs(parsed.query)
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=JBSWY3DPEHPK3PXP", uri)
        self.assertEqual(params["secret"], ["JBSWY3DPEHPK3PXP"])
        self.assertIn("alice", uri)
        self.assertIn("issuer=", uri)
        self.assertIn(f"period={_totp_period_seconds()}", uri)
        self.assertIn("digits=6", uri)


class TestEnrollmentFlow(BaseApiTest):
    """start_totp_enrollment / confirm_totp_enrollment / disable flow."""

    def _make_user(self, role: str = "editor") -> tuple[int, str]:
        username = "enroll_user"
        password = strong_password("enrollpass")
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username=username,
                password=password,
                role=role,
            )
            conn.commit()
            return int(user["id"]), username
        finally:
            db.return_db(conn)

    def test_start_enrollment(self) -> None:
        user_id, username = self._make_user()
        conn = db.get_db()
        try:
            result = start_totp_enrollment(conn, user_id=user_id, username=username)
            conn.commit()
            self.assertIn("secret", result)
            self.assertIn("provisioning_uri", result)
            self.assertIn("expires_at_ms", result)
            self.assertIsInstance(result["secret"], str)
            self.assertTrue(
                str(result["provisioning_uri"]).startswith("otpauth://totp/"),
            )
        finally:
            db.return_db(conn)

    def test_start_enrollment_when_already_enabled_raises(self) -> None:
        user_id, username = self._make_user()
        conn = db.get_db()
        try:
            enrollment = start_totp_enrollment(conn, user_id=user_id, username=username)
            conn.commit()
            secret = str(enrollment["secret"])
            counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
            code = _totp_at(secret, counter)
            confirm_totp_enrollment(conn, user_id=user_id, code=code)
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            with self.assertRaises(ValueError, msg="MFA is already enabled"):
                start_totp_enrollment(conn, user_id=user_id, username=username)
        finally:
            db.return_db(conn)

    def test_confirm_enrollment_valid_code(self) -> None:
        user_id, username = self._make_user()
        conn = db.get_db()
        try:
            enrollment = start_totp_enrollment(conn, user_id=user_id, username=username)
            conn.commit()
            secret = str(enrollment["secret"])
            counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
            code = _totp_at(secret, counter)
            recovery_codes = confirm_totp_enrollment(conn, user_id=user_id, code=code)
            conn.commit()
            self.assertEqual(len(recovery_codes), 10)
        finally:
            db.return_db(conn)

    def test_confirm_enrollment_invalid_code_raises(self) -> None:
        user_id, username = self._make_user()
        conn = db.get_db()
        try:
            start_totp_enrollment(conn, user_id=user_id, username=username)
            conn.commit()
            with self.assertRaises(ValueError, msg="Invalid verification code"):
                confirm_totp_enrollment(conn, user_id=user_id, code="000000")
        finally:
            db.return_db(conn)

    def test_confirm_without_pending_raises(self) -> None:
        user_id, _ = self._make_user()
        conn = db.get_db()
        try:
            with self.assertRaises(ValueError, msg="No pending MFA enrollment"):
                confirm_totp_enrollment(conn, user_id=user_id, code="123456")
        finally:
            db.return_db(conn)


class TestDisableTotpMfa(BaseApiTest):
    """disable_totp_mfa clears MFA state."""

    def test_disable_clears_mfa(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="disable_user",
                password=strong_password("disablepass"),
                role="editor",
            )
            user_id = int(user["id"])
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            enrollment = start_totp_enrollment(
                conn,
                user_id=user_id,
                username="disable_user",
            )
            conn.commit()
            secret = str(enrollment["secret"])
            counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
            code = _totp_at(secret, counter)
            confirm_totp_enrollment(conn, user_id=user_id, code=code)
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            status_before = get_user_mfa_status(conn, user_id)
            self.assertTrue(status_before["enabled"])
            disable_totp_mfa(conn, user_id=user_id)
            conn.commit()
            status_after = get_user_mfa_status(conn, user_id)
            self.assertFalse(status_after["enabled"])
            self.assertEqual(status_after["recovery_codes_remaining"], 0)
        finally:
            db.return_db(conn)


class TestVerifyUserSecondFactor(BaseApiTest):
    """verify_user_second_factor — TOTP and recovery code paths."""

    def _enroll_mfa(self) -> tuple[int, str, list[str]]:
        """Create user and fully enroll MFA. Returns (user_id, secret, recovery_codes)."""
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="mfa_verify_user",
                password=strong_password("verifypass"),
                role="editor",
            )
            user_id = int(user["id"])
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            enrollment = start_totp_enrollment(
                conn,
                user_id=user_id,
                username="mfa_verify_user",
            )
            conn.commit()
            secret = str(enrollment["secret"])
            counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
            code = _totp_at(secret, counter)
            recovery_codes = confirm_totp_enrollment(conn, user_id=user_id, code=code)
            conn.commit()
            return user_id, secret, recovery_codes
        finally:
            db.return_db(conn)

    def test_totp_path_valid(self) -> None:
        user_id, secret, _ = self._enroll_mfa()
        period_ms = _totp_period_seconds() * 1000
        now_ms = db.current_timestamp_ms()
        counter = now_ms // period_ms
        # Use next counter to avoid replay with the enrollment confirmation code
        (counter + 1) * period_ms
        code = _totp_at(secret, counter + 1)
        conn = db.get_db()
        try:
            ok, method = verify_user_second_factor(
                conn,
                user_id=user_id,
                mfa_code=code,
            )
            conn.commit()
            self.assertTrue(ok)
            self.assertEqual(method, "totp")
        finally:
            db.return_db(conn)

    def test_totp_path_invalid(self) -> None:
        user_id, _, _ = self._enroll_mfa()
        conn = db.get_db()
        try:
            ok, method = verify_user_second_factor(
                conn,
                user_id=user_id,
                mfa_code="000000",
            )
            self.assertFalse(ok)
            self.assertEqual(method, "")
        finally:
            db.return_db(conn)

    def test_recovery_code_path(self) -> None:
        user_id, _, recovery_codes = self._enroll_mfa()
        conn = db.get_db()
        try:
            ok, method = verify_user_second_factor(
                conn,
                user_id=user_id,
                recovery_code=recovery_codes[0],
            )
            conn.commit()
            self.assertTrue(ok)
            self.assertEqual(method, "recovery_code")
        finally:
            db.return_db(conn)

    def test_recovery_code_single_use(self) -> None:
        user_id, _, recovery_codes = self._enroll_mfa()
        code = recovery_codes[0]

        conn = db.get_db()
        try:
            ok1, _ = verify_user_second_factor(
                conn,
                user_id=user_id,
                recovery_code=code,
            )
            conn.commit()
            self.assertTrue(ok1)
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            ok2, _ = verify_user_second_factor(
                conn,
                user_id=user_id,
                recovery_code=code,
            )
            self.assertFalse(ok2)
        finally:
            db.return_db(conn)

    def test_no_mfa_enabled_returns_false(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="nomfa_user",
                password=strong_password("nomfapass"),
                role="editor",
            )
            conn.commit()
            ok, method = verify_user_second_factor(
                conn,
                user_id=int(user["id"]),
                mfa_code="123456",
            )
            self.assertFalse(ok)
            self.assertEqual(method, "")
        finally:
            db.return_db(conn)


class TestGetUserMfaStatus(BaseApiTest):
    """get_user_mfa_status returns correct structure."""

    def test_mfa_disabled_status(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="status_user",
                password=strong_password("statuspass"),
                role="editor",
            )
            conn.commit()
            status = get_user_mfa_status(conn, int(user["id"]))
            self.assertFalse(status["enabled"])
            self.assertFalse(status["pending_enrollment"])
            self.assertEqual(status["recovery_codes_remaining"], 0)
            self.assertEqual(status["methods"], [])
        finally:
            db.return_db(conn)

    def test_admin_setup_required(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="admin_status",
                password=strong_password("adminpass"),
                role="admin",
            )
            conn.commit()
            status = get_user_mfa_status(conn, int(user["id"]))
            self.assertTrue(status["setup_required"])
        finally:
            db.return_db(conn)

    def test_user_not_found_raises(self) -> None:
        conn = db.get_db()
        try:
            with self.assertRaises(ValueError):
                get_user_mfa_status(conn, 999999)
        finally:
            db.return_db(conn)

    def test_mfa_enabled_status(self) -> None:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username="enabled_status",
                password=strong_password("enabledpass"),
                role="editor",
            )
            user_id = int(user["id"])
            conn.commit()
        finally:
            db.return_db(conn)

        conn = db.get_db()
        try:
            enrollment = start_totp_enrollment(
                conn,
                user_id=user_id,
                username="enabled_status",
            )
            conn.commit()
            secret = str(enrollment["secret"])
            counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
            code = _totp_at(secret, counter)
            confirm_totp_enrollment(conn, user_id=user_id, code=code)
            conn.commit()
            status = get_user_mfa_status(conn, user_id)
            self.assertTrue(status["enabled"])
            self.assertFalse(status["pending_enrollment"])
            self.assertGreater(status["recovery_codes_remaining"], 0)
            self.assertEqual(status["methods"], ["totp", "recovery_code"])
        finally:
            db.return_db(conn)
