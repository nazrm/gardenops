from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import HTTPException
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from gardenops.branding import app_name
from gardenops.db import DbConn, current_timestamp_ms

PasskeyFlow = Literal["registration", "authentication", "authentication_denied", "reauthentication"]

_CHALLENGE_BYTES = 32
_CHALLENGE_TOKEN_BYTES = 32
_CHALLENGE_TTL_MS = 5 * 60 * 1000
_PASSKEY_TIMEOUT_MS = 60_000
_ALLOWED_TRANSPORTS = {"ble", "hybrid", "internal", "nfc", "smart-card", "usb", "cable"}
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class PasskeyChallenge:
    token: str
    challenge: bytes


@dataclass(frozen=True)
class ConsumedPasskeyChallenge:
    id: int
    challenge: bytes
    user_id: int | None
    session_token_hash: str | None
    invitation_token_hash: str | None = None
    invitation_scope: str | None = None
    invitation_id: int | None = None
    invitee_username: str | None = None
    invitation_user_handle: str | None = None


@dataclass(frozen=True)
class VerifiedPasskeyRegistration:
    credential_id: bytes
    credential_public_key: bytes
    sign_count: int
    credential_device_type: str
    credential_backed_up: bool


@dataclass(frozen=True)
class VerifiedPasskeyAuthentication:
    new_sign_count: int
    credential_device_type: str
    credential_backed_up: bool


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64decode(raw: str) -> bytes:
    padding = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        return base64.urlsafe_b64decode(raw + padding)
    except Exception as exc:
        raise ValueError("Invalid base64url value") from exc


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _challenge_ttl_ms() -> int:
    raw = os.environ.get("AUTH_PASSKEY_CHALLENGE_TTL_SECONDS", "").strip()
    try:
        seconds = int(raw) if raw else _CHALLENGE_TTL_MS // 1000
    except ValueError:
        seconds = _CHALLENGE_TTL_MS // 1000
    return max(60, min(seconds, 15 * 60)) * 1000


def _valid_rp_or_origin_host(value: str) -> bool:
    if not value or len(value) > 253 or value.startswith(".") or value.endswith("."):
        return False
    return all(_HOST_LABEL_RE.fullmatch(part) for part in value.split("."))


def passkey_rp_id() -> str:
    value = os.environ.get("AUTH_PASSKEY_RP_ID", "").strip().lower()
    if value:
        if "://" in value or "/" in value or ":" in value or not _valid_rp_or_origin_host(value):
            raise HTTPException(status_code=503, detail="Invalid passkey relying party ID")
        return value
    raise HTTPException(status_code=503, detail="Passkey relying party is not configured")


def passkey_origins() -> list[str]:
    raw = os.environ.get("AUTH_PASSKEY_ORIGINS", "").strip()
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if not origins:
        raise HTTPException(status_code=503, detail="Passkey origins are not configured")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            not parsed.scheme
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(status_code=503, detail="Invalid passkey origin")
        host = (parsed.hostname or "").lower()
        if not _valid_rp_or_origin_host(host):
            raise HTTPException(status_code=503, detail="Invalid passkey origin")
        allowed_dev_origin = parsed.scheme == "http" and host in {
            "localhost",
            "127.0.0.1",
            "testserver",
        }
        if parsed.scheme != "https" and not allowed_dev_origin:
            raise HTTPException(status_code=503, detail="Invalid passkey origin")
    return origins


def passkeys_configured() -> bool:
    try:
        passkey_rp_id()
        passkey_origins()
    except HTTPException:
        return False
    return True


def require_passkeys_configured() -> None:
    passkey_rp_id()
    passkey_origins()


def cleanup_expired_challenges(conn: DbConn) -> None:
    conn.execute(
        "DELETE FROM auth_passkey_challenges WHERE expires_at_ms <= %s",
        (current_timestamp_ms(),),
    )


def create_challenge(
    conn: DbConn,
    *,
    flow: PasskeyFlow,
    user_id: int | None = None,
    session_token_hash: str | None = None,
    invitation_token_hash: str | None = None,
    invitation_scope: str | None = None,
    invitation_id: int | None = None,
    invitee_username: str | None = None,
    invitation_user_handle: str | None = None,
) -> PasskeyChallenge:
    if flow == "authentication" and user_id is None:
        raise HTTPException(
            status_code=400,
            detail="Authentication passkey challenges require a user",
        )
    cleanup_expired_challenges(conn)
    token = secrets.token_urlsafe(_CHALLENGE_TOKEN_BYTES)
    challenge = secrets.token_bytes(_CHALLENGE_BYTES)
    now_ms = current_timestamp_ms()
    conn.execute(
        """
        INSERT INTO auth_passkey_challenges (
            token_hash,
            challenge,
            flow,
            user_id,
            session_token_hash,
            invitation_token_hash,
            invitation_scope,
            invitation_id,
            invitee_username,
            invitation_user_handle,
            created_at_ms,
            expires_at_ms,
            used_at_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
        """,
        (
            _hash_token(token),
            _b64encode(challenge),
            flow,
            user_id,
            session_token_hash,
            invitation_token_hash,
            invitation_scope,
            invitation_id,
            invitee_username,
            invitation_user_handle,
            now_ms,
            now_ms + _challenge_ttl_ms(),
        ),
    )
    return PasskeyChallenge(token=token, challenge=challenge)


def consume_challenge(
    conn: DbConn,
    *,
    token: str,
    flow: PasskeyFlow,
    user_id: int | None = None,
    session_token_hash: str | None = None,
) -> ConsumedPasskeyChallenge:
    if not token:
        raise HTTPException(status_code=400, detail="Invalid or expired passkey challenge")
    now_ms = current_timestamp_ms()
    params: list[object] = [now_ms, _hash_token(token), flow, now_ms]
    user_clause = ""
    if user_id is not None:
        user_clause = "AND user_id = %s"
        params.append(user_id)
    elif flow == "authentication":
        user_clause = "AND user_id IS NOT NULL"
    elif flow == "registration":
        user_clause = "AND user_id IS NULL"
    session_clause = ""
    if session_token_hash is not None:
        session_clause = "AND session_token_hash = %s"
        params.append(session_token_hash)
    row = conn.execute(
        f"""
        UPDATE auth_passkey_challenges
        SET used_at_ms = %s
        WHERE token_hash = %s
          AND flow = %s
          AND used_at_ms IS NULL
          AND expires_at_ms > %s
          {user_clause}
          {session_clause}
        RETURNING
            id,
            challenge,
            user_id,
            session_token_hash,
            invitation_token_hash,
            invitation_scope,
            invitation_id,
            invitee_username,
            invitation_user_handle
        """,
        tuple(params),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired passkey challenge")
    return ConsumedPasskeyChallenge(
        id=int(row["id"]),
        challenge=b64decode(str(row["challenge"])),
        user_id=int(row["user_id"]) if row["user_id"] is not None else None,
        session_token_hash=(
            str(row["session_token_hash"]) if row["session_token_hash"] is not None else None
        ),
        invitation_token_hash=(
            str(row["invitation_token_hash"]) if row["invitation_token_hash"] is not None else None
        ),
        invitation_scope=(
            str(row["invitation_scope"]) if row["invitation_scope"] is not None else None
        ),
        invitation_id=(int(row["invitation_id"]) if row["invitation_id"] is not None else None),
        invitee_username=(
            str(row["invitee_username"]) if row["invitee_username"] is not None else None
        ),
        invitation_user_handle=(
            str(row["invitation_user_handle"])
            if row["invitation_user_handle"] is not None
            else None
        ),
    )


def consume_public_authentication_challenge(
    conn: DbConn,
    *,
    token: str,
) -> ConsumedPasskeyChallenge:
    if not token:
        raise HTTPException(status_code=400, detail="Invalid or expired passkey challenge")
    now_ms = current_timestamp_ms()
    row = conn.execute(
        """
        UPDATE auth_passkey_challenges
        SET used_at_ms = %s
        WHERE token_hash = %s
          AND used_at_ms IS NULL
          AND expires_at_ms > %s
          AND (
              (flow = 'authentication' AND user_id IS NOT NULL)
              OR (flow = 'authentication_denied' AND user_id IS NULL)
          )
        RETURNING id, challenge, user_id, session_token_hash
        """,
        (now_ms, _hash_token(token), now_ms),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired passkey challenge")
    return ConsumedPasskeyChallenge(
        id=int(row["id"]),
        challenge=b64decode(str(row["challenge"])),
        user_id=int(row["user_id"]) if row["user_id"] is not None else None,
        session_token_hash=(
            str(row["session_token_hash"]) if row["session_token_hash"] is not None else None
        ),
    )


def _parse_transports(raw: str) -> list[str]:
    if not raw:
        return []
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    return [item for item in parsed if item in _ALLOWED_TRANSPORTS]


def _credential_descriptors(rows: list[dict[str, Any]]) -> list[PublicKeyCredentialDescriptor]:
    descriptors: list[PublicKeyCredentialDescriptor] = []
    for row in rows:
        transports: list[AuthenticatorTransport] = []
        for transport in _parse_transports(str(row.get("transports") or "")):
            try:
                transports.append(AuthenticatorTransport(transport))
            except ValueError:
                continue
        descriptors.append(
            PublicKeyCredentialDescriptor(
                id=b64decode(str(row["credential_id"])),
                transports=cast(Any, transports) or None,
            )
        )
    return descriptors


def registration_options_for_user(
    conn: DbConn,
    *,
    user_id: int,
    username: str,
    challenge: bytes,
    user_handle: str | None = None,
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT credential_id, transports FROM auth_passkeys WHERE user_id = %s",
        (user_id,),
    ).fetchall()
    options = generate_registration_options(
        rp_id=passkey_rp_id(),
        rp_name=app_name(),
        user_id=(user_handle or str(user_id)).encode("utf-8"),
        user_name=username,
        user_display_name=username,
        challenge=challenge,
        timeout=_PASSKEY_TIMEOUT_MS,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=_credential_descriptors([dict(row) for row in rows]),
    )
    return json.loads(options_to_json(options))


def authentication_options(
    conn: DbConn,
    *,
    challenge: bytes,
    user_id: int | None = None,
    include_allow_credentials: bool = True,
) -> dict[str, Any]:
    allow_credentials = None
    if include_allow_credentials and user_id is not None:
        rows = conn.execute(
            "SELECT credential_id, transports FROM auth_passkeys WHERE user_id = %s",
            (user_id,),
        ).fetchall()
        allow_credentials = _credential_descriptors([dict(row) for row in rows])
    options = generate_authentication_options(
        rp_id=passkey_rp_id(),
        challenge=challenge,
        timeout=_PASSKEY_TIMEOUT_MS,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(options_to_json(options))


def verify_registration_credential(
    *,
    credential: dict[str, Any],
    expected_challenge: bytes,
) -> VerifiedPasskeyRegistration:
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=passkey_rp_id(),
            expected_origin=passkey_origins(),
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid passkey registration") from exc
    return VerifiedPasskeyRegistration(
        credential_id=verification.credential_id,
        credential_public_key=verification.credential_public_key,
        sign_count=int(verification.sign_count),
        credential_device_type=str(verification.credential_device_type),
        credential_backed_up=bool(verification.credential_backed_up),
    )


def verify_authentication_credential(
    *,
    credential: dict[str, Any],
    expected_challenge: bytes,
    credential_public_key: bytes,
    credential_current_sign_count: int,
) -> VerifiedPasskeyAuthentication:
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=passkey_rp_id(),
            expected_origin=passkey_origins(),
            credential_public_key=credential_public_key,
            credential_current_sign_count=credential_current_sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid passkey authentication") from exc
    return VerifiedPasskeyAuthentication(
        new_sign_count=int(verification.new_sign_count),
        credential_device_type=str(verification.credential_device_type),
        credential_backed_up=bool(verification.credential_backed_up),
    )


def credential_transports(credential: dict[str, Any]) -> str:
    response = credential.get("response")
    if not isinstance(response, dict):
        return ""
    raw_transports = response.get("transports")
    if not isinstance(raw_transports, list):
        return ""
    transports = [str(item).strip() for item in raw_transports if str(item).strip()]
    return ",".join(item for item in transports if item in _ALLOWED_TRANSPORTS)


def credential_id_from_public_key_credential(credential: dict[str, Any]) -> str:
    raw = credential.get("rawId") or credential.get("id")
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=400, detail="Invalid passkey credential")
    try:
        return _b64encode(b64decode(raw))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid passkey credential") from exc


def encode_public_key(raw: bytes) -> str:
    return _b64encode(raw)


def serialize_passkey(row: dict[str, Any]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "nickname": str(row["nickname"] or ""),
        "created_at_ms": int(row["created_at_ms"]),
        "last_used_at_ms": (
            int(row["last_used_at_ms"]) if row["last_used_at_ms"] is not None else None
        ),
        "transports": _parse_transports(str(row["transports"] or "")),
        "credential_device_type": str(row["credential_device_type"] or ""),
        "credential_backed_up": bool(int(row["credential_backed_up"] or 0)),
    }
