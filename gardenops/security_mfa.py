from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gardenops.branding import app_name
from gardenops.db import DbConn, current_timestamp_ms

_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ENCRYPTION_NONCE_BYTES = 16
_ENCRYPTION_TAG_BYTES = 32
_AESGCM_NONCE_BYTES = 12
_ENCRYPTION_VERSION_LEGACY = b"\x01"
_ENCRYPTION_VERSION_AESGCM = b"\x02"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_setting(conn: DbConn, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (key,),
    ).fetchone()
    if not row:
        return default
    return str(row["value"] or default)


def _set_setting(conn: DbConn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padding = "=" * ((4 - (len(raw) % 4)) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _master_secret(conn: DbConn) -> bytes:
    configured = os.environ.get("AUTH_MFA_SECRET_KEY", "").strip()
    if configured:
        source = configured
    else:
        source = _get_setting(conn, "auth_mfa_secret_key", "")
        if not source:
            source = secrets.token_hex(32)
            _set_setting(conn, "auth_mfa_secret_key", source)
    return hashlib.sha256(source.encode("utf-8")).digest()


def _derive_keys(conn: DbConn) -> tuple[bytes, bytes]:
    master = _master_secret(conn)
    enc_key = hmac.new(master, b"mfa-enc", hashlib.sha256).digest()
    mac_key = hmac.new(master, b"mfa-mac", hashlib.sha256).digest()
    return enc_key, mac_key


def _derive_aesgcm_key(conn: DbConn) -> bytes:
    master = _master_secret(conn)
    return hmac.new(master, b"mfa-aesgcm-v2", hashlib.sha256).digest()


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(data):
        block = hmac.new(
            key,
            nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        output.extend(block)
        counter += 1
    return bytes(a ^ b for a, b in zip(data, output, strict=False))


def encrypt_mfa_secret(conn: DbConn, secret: str) -> str:
    """Encrypt MFA secret using AES-256-GCM (version 0x02)."""
    aesgcm_key = _derive_aesgcm_key(conn)
    nonce = secrets.token_bytes(_AESGCM_NONCE_BYTES)
    plaintext = secret.encode("utf-8")
    aesgcm = AESGCM(aesgcm_key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext, None)
    return _b64encode(_ENCRYPTION_VERSION_AESGCM + nonce + ciphertext_and_tag)


def _decrypt_legacy_v1(conn: DbConn, raw: bytes) -> str:
    """Decrypt legacy XOR stream cipher (version 0x01 or unversioned)."""
    enc_key, mac_key = _derive_keys(conn)
    if len(raw) < (_ENCRYPTION_NONCE_BYTES + _ENCRYPTION_TAG_BYTES):
        raise ValueError("Invalid MFA secret payload")
    nonce = raw[:_ENCRYPTION_NONCE_BYTES]
    tag = raw[-_ENCRYPTION_TAG_BYTES:]
    ciphertext = raw[_ENCRYPTION_NONCE_BYTES:-_ENCRYPTION_TAG_BYTES]
    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Invalid MFA secret signature")
    return _xor_stream(ciphertext, enc_key, nonce).decode("utf-8")


def _decrypt_aesgcm_v2(conn: DbConn, raw: bytes) -> str:
    """Decrypt AES-256-GCM (version 0x02)."""
    aesgcm_key = _derive_aesgcm_key(conn)
    if len(raw) < _AESGCM_NONCE_BYTES + 1:
        raise ValueError("Invalid MFA secret payload")
    nonce = raw[:_AESGCM_NONCE_BYTES]
    ciphertext_and_tag = raw[_AESGCM_NONCE_BYTES:]
    aesgcm = AESGCM(aesgcm_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
    return plaintext.decode("utf-8")


def decrypt_mfa_secret(conn: DbConn, token: str) -> str:
    """Decrypt MFA secret, dispatching on version byte."""
    raw = _b64decode(token)
    if len(raw) < 2:
        raise ValueError("Invalid MFA secret payload")
    if raw[:1] == _ENCRYPTION_VERSION_AESGCM:
        return _decrypt_aesgcm_v2(conn, raw[1:])
    if raw[:1] == _ENCRYPTION_VERSION_LEGACY:
        return _decrypt_legacy_v1(conn, raw[1:])
    # Unversioned legacy format (pre-migration data)
    return _decrypt_legacy_v1(conn, raw)


def _recovery_hash(conn: DbConn, code: str) -> str:
    master = _master_secret(conn)
    normalized = normalize_recovery_code(code)
    return hmac.new(
        master,
        f"recovery:{normalized}".encode(),
        hashlib.sha256,
    ).hexdigest()


def normalize_totp_code(code: str) -> str:
    return "".join(ch for ch in code.strip() if ch.isdigit())


def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.strip().upper() if ch.isalnum())


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_period_seconds() -> int:
    return max(15, min(_env_int("AUTH_MFA_TOTP_PERIOD_SECONDS", 30), 300))


def _totp_digits() -> int:
    return max(6, min(_env_int("AUTH_MFA_TOTP_DIGITS", 6), 8))


def _totp_time_window_steps() -> int:
    return max(0, min(_env_int("AUTH_MFA_TOTP_WINDOW_STEPS", 1), 5))


def _pending_enrollment_ttl_ms() -> int:
    seconds = max(60, min(_env_int("AUTH_MFA_PENDING_TTL_SECONDS", 10 * 60), 24 * 60 * 60))
    return seconds * 1000


def _recovery_code_count() -> int:
    return max(5, min(_env_int("AUTH_MFA_RECOVERY_CODE_COUNT", 10), 20))


def _decode_totp_secret(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * ((8 - (len(normalized) % 8)) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def _totp_at(secret: str, counter: int) -> str:
    digest = hmac.new(
        _decode_totp_secret(secret),
        counter.to_bytes(8, "big"),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = (
        ((digest[offset] & 0x7F) << 24)
        | (digest[offset + 1] << 16)
        | (digest[offset + 2] << 8)
        | digest[offset + 3]
    )
    return str(binary % (10 ** _totp_digits())).zfill(_totp_digits())


def verify_totp(secret: str, code: str, *, now_ms: int | None = None) -> bool:
    normalized = normalize_totp_code(code)
    if len(normalized) != _totp_digits():
        return False
    current_ms = now_ms or current_timestamp_ms()
    counter = current_ms // (_totp_period_seconds() * 1000)
    window = _totp_time_window_steps()
    for offset in range(-window, window + 1):
        candidate = _totp_at(secret, counter + offset)
        if hmac.compare_digest(candidate, normalized):
            return True
    return False


def verify_totp_with_replay_protection(
    conn: DbConn,
    *,
    user_id: int,
    secret: str,
    code: str,
    now_ms: int | None = None,
) -> bool:
    """Verify TOTP code and prevent replay within the same time window."""
    normalized = normalize_totp_code(code)
    if len(normalized) != _totp_digits():
        return False
    current_ms = now_ms or current_timestamp_ms()
    counter = current_ms // (_totp_period_seconds() * 1000)
    window = _totp_time_window_steps()

    row = conn.execute(
        "SELECT last_totp_counter FROM auth_users WHERE id = %s",
        (user_id,),
    ).fetchone()
    last_counter = int(row["last_totp_counter"] or 0) if row and row["last_totp_counter"] else 0

    for offset in range(-window, window + 1):
        step = counter + offset
        if step <= last_counter:
            continue
        candidate = _totp_at(secret, step)
        if hmac.compare_digest(candidate, normalized):
            updated = conn.execute(
                """
                UPDATE auth_users
                SET last_totp_counter = %s
                WHERE id = %s
                  AND last_totp_counter < %s
                RETURNING id
                """,
                (step, user_id, step),
            ).fetchone()
            return updated is not None
    return False


def totp_provisioning_uri(*, username: str, secret: str) -> str:
    issuer = os.environ.get("AUTH_MFA_TOTP_ISSUER", "").strip() or app_name()
    label = quote(f"{issuer}:{username}")
    issuer_q = quote(issuer)
    return (
        f"otpauth://totp/{label}"
        f"?secret={quote(secret)}&issuer={issuer_q}&period={_totp_period_seconds()}"
        f"&digits={_totp_digits()}"
    )


def generate_recovery_codes() -> list[str]:
    codes: list[str] = []
    for _ in range(_recovery_code_count()):
        left = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4))
        right = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4))
        codes.append(f"{left}-{right}")
    return codes


def _replace_recovery_codes(conn: DbConn, *, user_id: int, codes: list[str]) -> None:
    conn.execute("DELETE FROM auth_mfa_recovery_codes WHERE user_id = %s", (user_id,))
    now_ms = current_timestamp_ms()
    for code in codes:
        conn.execute(
            """
            INSERT INTO auth_mfa_recovery_codes (
                user_id,
                code_hash,
                created_at_ms,
                used_at_ms
            )
            VALUES (%s, %s, %s, NULL)
            """,
            (user_id, _recovery_hash(conn, code), now_ms),
        )


def _cleanup_expired_pending_enrollments(conn: DbConn) -> None:
    conn.execute(
        "DELETE FROM auth_mfa_pending_enrollments WHERE expires_at_ms <= %s",
        (current_timestamp_ms(),),
    )


def get_user_mfa_status(conn: DbConn, user_id: int) -> dict[str, Any]:
    _cleanup_expired_pending_enrollments(conn)
    user_row = conn.execute(
        """
        SELECT role, mfa_totp_enabled, mfa_enrolled_at
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not user_row:
        raise ValueError("User not found")
    pending_row = conn.execute(
        """
        SELECT expires_at_ms
        FROM auth_mfa_pending_enrollments
        WHERE user_id = %s
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    recovery_row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM auth_mfa_recovery_codes
        WHERE user_id = %s AND used_at_ms IS NULL
        """,
        (user_id,),
    ).fetchone()
    enabled = bool(int(user_row["mfa_totp_enabled"]))
    pending_expires_at_ms = (
        int(pending_row["expires_at_ms"])
        if pending_row and pending_row["expires_at_ms"] is not None
        else None
    )
    return {
        "enabled": enabled,
        "setup_required": str(user_row["role"]) == "admin" and not enabled,
        "enrolled_at": user_row["mfa_enrolled_at"],
        "pending_enrollment": pending_expires_at_ms is not None,
        "pending_expires_at_ms": pending_expires_at_ms,
        "recovery_codes_remaining": int(recovery_row["c"] if recovery_row else 0),
        "methods": ["totp", "recovery_code"] if enabled else [],
    }


def start_totp_enrollment(
    conn: DbConn,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    status = get_user_mfa_status(conn, user_id)
    if bool(status["enabled"]):
        raise ValueError("MFA is already enabled")
    secret = generate_totp_secret()
    now_ms = current_timestamp_ms()
    expires_at_ms = now_ms + _pending_enrollment_ttl_ms()
    conn.execute(
        """
        INSERT INTO auth_mfa_pending_enrollments (
            user_id,
            secret_ciphertext,
            created_at_ms,
            expires_at_ms
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            secret_ciphertext = excluded.secret_ciphertext,
            created_at_ms = excluded.created_at_ms,
            expires_at_ms = excluded.expires_at_ms
        """,
        (
            user_id,
            encrypt_mfa_secret(conn, secret),
            now_ms,
            expires_at_ms,
        ),
    )
    return {
        "secret": secret,
        "provisioning_uri": totp_provisioning_uri(username=username, secret=secret),
        "expires_at_ms": expires_at_ms,
    }


def confirm_totp_enrollment(
    conn: DbConn,
    *,
    user_id: int,
    code: str,
) -> list[str]:
    _cleanup_expired_pending_enrollments(conn)
    pending_row = conn.execute(
        """
        SELECT secret_ciphertext
        FROM auth_mfa_pending_enrollments
        WHERE user_id = %s
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not pending_row:
        raise ValueError("No pending MFA enrollment")
    secret = decrypt_mfa_secret(conn, str(pending_row["secret_ciphertext"]))
    if not verify_totp(secret, code):
        raise ValueError("Invalid verification code")
    recovery_codes = generate_recovery_codes()
    conn.execute(
        """
        UPDATE auth_users
        SET
            mfa_totp_secret = %s,
            mfa_totp_enabled = 1,
            mfa_enrolled_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (encrypt_mfa_secret(conn, secret), user_id),
    )
    conn.execute(
        "DELETE FROM auth_mfa_pending_enrollments WHERE user_id = %s",
        (user_id,),
    )
    _replace_recovery_codes(conn, user_id=user_id, codes=recovery_codes)
    return recovery_codes


def disable_totp_mfa(conn: DbConn, *, user_id: int) -> None:
    conn.execute(
        """
        UPDATE auth_users
        SET
            mfa_totp_secret = NULL,
            mfa_totp_enabled = 0,
            mfa_enrolled_at = NULL
        WHERE id = %s
        """,
        (user_id,),
    )
    conn.execute("DELETE FROM auth_mfa_pending_enrollments WHERE user_id = %s", (user_id,))
    conn.execute("DELETE FROM auth_mfa_recovery_codes WHERE user_id = %s", (user_id,))


def regenerate_recovery_codes(conn: DbConn, *, user_id: int) -> list[str]:
    status = get_user_mfa_status(conn, user_id)
    if not bool(status["enabled"]):
        raise ValueError("MFA is not enabled")
    recovery_codes = generate_recovery_codes()
    _replace_recovery_codes(conn, user_id=user_id, codes=recovery_codes)
    return recovery_codes


def verify_user_second_factor(
    conn: DbConn,
    *,
    user_id: int,
    mfa_code: str = "",
    recovery_code: str = "",
) -> tuple[bool, str]:
    user_row = conn.execute(
        """
        SELECT mfa_totp_enabled, mfa_totp_secret
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not user_row or not bool(int(user_row["mfa_totp_enabled"])):
        return False, ""

    normalized_totp = normalize_totp_code(mfa_code)
    if normalized_totp:
        secret_ciphertext = str(user_row["mfa_totp_secret"] or "")
        if not secret_ciphertext:
            return False, ""
        decrypted_secret = decrypt_mfa_secret(conn, secret_ciphertext)
        if verify_totp_with_replay_protection(
            conn,
            user_id=user_id,
            secret=decrypted_secret,
            code=normalized_totp,
        ):
            return True, "totp"

    normalized_recovery = normalize_recovery_code(recovery_code)
    if normalized_recovery:
        code_hash = _recovery_hash(conn, normalized_recovery)
        row = conn.execute(
            """
            UPDATE auth_mfa_recovery_codes
            SET used_at_ms = %s
            WHERE user_id = %s
              AND code_hash = %s
              AND used_at_ms IS NULL
            RETURNING id
            """,
            (current_timestamp_ms(), user_id, code_hash),
        ).fetchone()
        if row:
            return True, "recovery_code"
    return False, ""


def list_active_admins_without_mfa(conn: DbConn) -> list[str]:
    rows = conn.execute(
        """
        SELECT username
        FROM auth_users
        WHERE role = 'admin' AND is_active = 1 AND mfa_totp_enabled = 0
        ORDER BY username
        """,
    ).fetchall()
    return [str(row["username"]) for row in rows]


def migrate_mfa_secrets_to_aesgcm(conn: DbConn) -> int:
    """Re-encrypt all MFA secrets from legacy XOR to AES-256-GCM.

    Returns the number of secrets migrated.
    """
    migrated = 0
    rows = conn.execute(
        "SELECT id, mfa_totp_secret FROM auth_users WHERE mfa_totp_secret IS NOT NULL",
    ).fetchall()
    for row in rows:
        token = str(row["mfa_totp_secret"])
        raw = _b64decode(token)
        if raw[:1] == _ENCRYPTION_VERSION_AESGCM:
            continue
        plaintext = decrypt_mfa_secret(conn, token)
        new_token = encrypt_mfa_secret(conn, plaintext)
        conn.execute(
            "UPDATE auth_users SET mfa_totp_secret = %s WHERE id = %s",
            (new_token, int(row["id"])),
        )
        migrated += 1

    pending = conn.execute(
        "SELECT user_id, secret_ciphertext FROM auth_mfa_pending_enrollments",
    ).fetchall()
    for row in pending:
        token = str(row["secret_ciphertext"])
        raw = _b64decode(token)
        if raw[:1] == _ENCRYPTION_VERSION_AESGCM:
            continue
        plaintext = decrypt_mfa_secret(conn, token)
        new_token = encrypt_mfa_secret(conn, plaintext)
        conn.execute(
            "UPDATE auth_mfa_pending_enrollments SET secret_ciphertext = %s WHERE user_id = %s",
            (new_token, int(row["user_id"])),
        )
        migrated += 1

    return migrated
