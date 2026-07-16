import json
import logging
import os
import re
import secrets
import time
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, NoReturn, TypedDict, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field, field_validator

import gardenops.passkeys as passkeys
from gardenops.audit import (
    enqueue_audit_event_telemetry,
    list_audit_events,
    write_audit_event,
    write_required_audit_event,
)
from gardenops.db import DB, DbConn, current_timestamp_ms, executemany
from gardenops.feature_gates import TIER_ORDER, features_for_tier
from gardenops.incident_controls import (
    get_emergency_read_only_status,
    list_active_sessions,
    revoke_all_sessions,
    revoke_session_by_public_id,
    revoke_sessions_by_user,
    set_emergency_read_only,
)
from gardenops.models import StrictBaseModel
from gardenops.platform_secrets import ConfigurationError
from gardenops.provider_settings import get_shademap_api_key
from gardenops.rate_limit import enforce_key_rate_limit, enforce_rate_limit, env_int, env_nonneg_int
from gardenops.security import (
    AUTH_ROLES,
    AuthContext,
    _admin_mfa_enforced_for_role,
    _session_absolute_ttl_ms,
    admin_mfa_required,
    api_key_auth_enabled,
    auth_mode,
    authenticate_user_credentials,
    count_users,
    create_session_for_user,
    create_user,
    csrf_cookie_name,
    csrf_token_for_session_hash,
    csrf_token_for_session_token,
    generate_passkey_user_handle,
    get_password_policy,
    has_write_access,
    hash_password,
    is_auth_required,
    is_loopback_client,
    password_needs_rehash,
    resolve_request_auth_context,
    revoke_session_token,
    session_auth_enabled,
    session_cookie_domain,
    session_cookie_name,
    session_cookie_path,
    session_cookie_samesite,
    session_cookie_secure,
    user_lifecycle_enabled,
    validate_password_policy,
    verify_password,
    verify_password_and_upgrade,
)
from gardenops.security_metrics import (
    record_security_event,
    security_alerts_snapshot,
    security_metrics_snapshot,
)
from gardenops.security_mfa import (
    cancel_totp_enrollment,
    confirm_totp_enrollment,
    disable_totp_mfa,
    get_user_mfa_status,
    regenerate_recovery_codes,
    start_totp_enrollment,
    verify_user_second_factor,
)
from gardenops.services.user_lifecycle import load_user_deletion_impact

logger = logging.getLogger(__name__)

router = APIRouter()

type SameSiteValue = Literal["lax", "strict", "none"]
type InvitationScope = Literal["garden", "personal_garden"]

_GENERIC_INVITATION_TOKEN_DETAIL = "Invalid or expired invitation token"


class SessionCookieKwargs(TypedDict):
    max_age: int
    secure: bool
    samesite: SameSiteValue
    path: str
    domain: str | None


def _coerce_int(value: object) -> int:
    return int(cast(int | float | str, value))


class AdaptiveFrictionFields(StrictBaseModel):
    friction_provider: str = Field(default="", max_length=80)
    friction_token: str = Field(default="", max_length=4096)


class LoginBody(AdaptiveFrictionFields):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    mfa_code: str = Field(default="", max_length=32)
    recovery_code: str = Field(default="", max_length=64)


class BootstrapBody(StrictBaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    role: Literal["viewer", "editor", "admin"] = "admin"


class RevokeUserSessionsBody(StrictBaseModel):
    username: str = Field(min_length=1, max_length=80)
    action_reason: str = Field(default="", max_length=400)


class EmergencyReadOnlyBody(StrictBaseModel):
    enabled: bool
    expires_in_minutes: int | None = Field(default=None, ge=5, le=24 * 60)
    action_reason: str = Field(default="", max_length=400)


class RevokeAllSessionsBody(StrictBaseModel):
    action_reason: str = Field(default="", max_length=400)


class AdminCreateUserBody(StrictBaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    role: Literal["viewer", "editor", "admin"] = "viewer"
    must_change_password: bool = False
    action_reason: str = Field(default="", max_length=400)


class AdminUpdateUserBody(StrictBaseModel):
    role: Literal["viewer", "editor", "admin"] | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None
    deactivated_reason: str = Field(default="", max_length=400)
    action_reason: str = Field(default="", max_length=400)


class ChangePasswordBody(StrictBaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ReauthenticateBody(StrictBaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    mfa_code: str = Field(default="", max_length=32)
    recovery_code: str = Field(default="", max_length=64)


class PasskeyRegistrationOptionsBody(StrictBaseModel):
    nickname: str = Field(default="", max_length=80)
    current_password: str = Field(default="", max_length=200)


class PasskeyRegistrationVerifyBody(StrictBaseModel):
    challenge_token: str = Field(min_length=20, max_length=256)
    nickname: str = Field(default="", max_length=80)
    credential: dict[str, Any]


class PasskeyPromptDismissBody(StrictBaseModel):
    dismiss_for_days: int = Field(default=30, ge=1, le=365)


class PasskeyLoginOptionsBody(StrictBaseModel):
    username: str = Field(default="", max_length=80)


class PasskeyLoginVerifyBody(StrictBaseModel):
    challenge_token: str = Field(min_length=20, max_length=256)
    credential: dict[str, Any]


class PasskeyReauthenticateOptionsBody(StrictBaseModel):
    pass


class PasskeyActionBody(StrictBaseModel):
    action_reason: str = Field(default="", max_length=400)


class PasskeyRenameBody(StrictBaseModel):
    nickname: str = Field(min_length=1, max_length=80)
    action_reason: str = Field(default="", max_length=400)


class ConfirmTotpEnrollmentBody(StrictBaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaActionBody(StrictBaseModel):
    action_reason: str = Field(default="", max_length=400)


class IssueResetTokenBody(StrictBaseModel):
    expires_in_minutes: int | None = Field(default=None, ge=5, le=24 * 60)
    must_change_password: bool = False
    purpose: Literal["password_reset", "passwordless_recovery"] = "password_reset"
    action_reason: str = Field(default="", max_length=400)


class ResetPasswordBody(AdaptiveFrictionFields):
    token: str = Field(min_length=10, max_length=512)
    new_password: str = Field(min_length=1, max_length=200)


class InvitationAcceptBody(AdaptiveFrictionFields):
    token: str = Field(min_length=10, max_length=512)
    password: str = Field(min_length=1, max_length=200)


class InvitationPasskeyRegisterOptionsBody(AdaptiveFrictionFields):
    token: str = Field(min_length=10, max_length=512)
    username: str = Field(min_length=1, max_length=80)


class InvitationPasskeyRegisterVerifyBody(StrictBaseModel):
    challenge_token: str = Field(min_length=20, max_length=256)
    nickname: str = Field(default="", max_length=80)
    credential: dict[str, Any]


class RevokeUserSessionsByIdBody(StrictBaseModel):
    action_reason: str = Field(default="", max_length=400)


class RestartUserOnboardingBody(StrictBaseModel):
    action_reason: str = Field(default="", max_length=400)


class CreateUserInvitationBody(StrictBaseModel):
    invitee_username: str = Field(min_length=1, max_length=80)
    role: Literal["editor", "admin"] = "editor"
    expires_in_minutes: int | None = Field(default=None, ge=5, le=30 * 24 * 60)
    action_reason: str = Field(default="", max_length=400)


def _current_token(request: Request) -> str:
    return request.cookies.get(session_cookie_name(), "").strip()


def _current_token_hash(request: Request) -> str:
    token = _current_token(request)
    if not token:
        return ""
    return sha256(token.encode("utf-8")).hexdigest()


def _require_admin_context(request: Request):
    context = resolve_request_auth_context(request)
    if context.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    if context.auth_type == "session":
        if context.mfa_setup_required:
            raise HTTPException(status_code=403, detail="Admin MFA setup is required")
        if (
            _admin_mfa_enforced_for_context(context)
            and int(context.mfa_authenticated_at_ms or 0) <= 0
        ):
            raise HTTPException(
                status_code=403,
                detail="Platform-admin MFA or passkey authentication is required",
            )
    return context


def _remote_host(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _session_device_label(request: Request) -> str:
    user_agent = re.sub(r"[\x00-\x1f\x7f]+", " ", request.headers.get("user-agent", ""))
    user_agent = user_agent.strip()[:200]
    if not user_agent:
        return "Unknown device"
    platform = next(
        (
            label
            for marker, label in (
                ("Android", "Android"),
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Windows", "Windows"),
                ("Macintosh", "macOS"),
                ("Linux", "Linux"),
            )
            if marker in user_agent
        ),
        "",
    )
    browser = next(
        (
            label
            for marker, label in (
                ("Edg/", "Edge"),
                ("Firefox/", "Firefox"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if marker in user_agent
        ),
        "",
    )
    if browser and platform:
        return f"{browser} on {platform}"
    return user_agent[:120]


def _session_location_hint(request: Request) -> str:
    remote_host = _remote_host(request).strip()[:80]
    if remote_host in {"127.0.0.1", "::1", "testclient"}:
        return "Local device"
    return remote_host


def _hashed_rate_limit_key(prefix: str, raw: str, *, casefold: bool = True) -> str:
    normalized = raw.strip()
    if not normalized:
        return ""
    if casefold:
        normalized = normalized.casefold()
    return f"{prefix}:{sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def _enforce_optional_key_rate_limit(
    *,
    bucket: str,
    key: str,
    env_name: str,
    default_limit: int,
    scope_label: str | None = None,
) -> None:
    limit = env_nonneg_int(env_name, default_limit)
    if limit <= 0 or not key:
        return
    enforce_key_rate_limit(
        bucket=bucket,
        key=key,
        limit=limit,
        window_seconds=60,
        scope_label=scope_label,
    )


def _session_cookie_kwargs(expires_at_ms: int) -> SessionCookieKwargs:
    ttl_seconds = max(1, int((expires_at_ms - int(time.time() * 1000)) / 1000))
    return {
        "max_age": ttl_seconds,
        "secure": session_cookie_secure(),
        "samesite": cast(SameSiteValue, session_cookie_samesite()),
        "path": session_cookie_path(),
        "domain": session_cookie_domain(),
    }


def _set_session_cookies(response: Response, *, token: str, expires_at_ms: int) -> None:
    cookie_kwargs = _session_cookie_kwargs(expires_at_ms)
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        httponly=True,
        **cookie_kwargs,
    )
    response.set_cookie(
        key=csrf_cookie_name(),
        value=csrf_token_for_session_token(token),
        httponly=False,
        **cookie_kwargs,
    )


def _adaptive_friction_mode() -> str:
    raw = os.environ.get("AUTH_ADAPTIVE_FRICTION_MODE", "").strip().lower()
    if raw in {"1", "true", "yes", "on", "require", "required", "enforce"}:
        return "require"
    if raw in {"observe", "log"}:
        return "observe"
    return "off"


def _adaptive_friction_flows() -> set[str]:
    raw = os.environ.get(
        "AUTH_ADAPTIVE_FRICTION_FLOWS",
        "login,reset-password,invitation-accept,invitation-passkey-register",
    ).strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _enforce_adaptive_friction(
    *,
    flow: str,
    friction_provider: str = "",
    friction_token: str = "",
) -> None:
    mode = _adaptive_friction_mode()
    if mode == "off" or flow not in _adaptive_friction_flows():
        return

    metric_suffix = flow.replace("-", "_")
    has_friction = bool(friction_provider.strip() and friction_token.strip())
    if has_friction:
        record_security_event(f"adaptive_friction_present_{metric_suffix}")
        return

    record_security_event(f"adaptive_friction_missing_{metric_suffix}")
    if mode == "require":
        record_security_event("adaptive_friction_required_blocks")
        record_security_event(f"adaptive_friction_required_blocks_{metric_suffix}")
        raise HTTPException(status_code=403, detail="Additional verification required")


def _load_login_candidate(db: DbConn, username: str) -> dict[str, Any] | None:
    normalized = username.strip()
    if not normalized:
        return None
    return db.execute(
        """
        SELECT id, username, role, is_active
        FROM auth_users
        WHERE username = %s
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()


def _audit_user_lifecycle_event(
    request: Request,
    *,
    auth_context,
    status_code: int,
    detail: str,
    garden_id: int | None = None,
    db: DbConn | None = None,
) -> None:
    request.state.audited_by_handler = True
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        remote_host=_remote_host(request),
        detail=detail,
        auth_context=auth_context,
        garden_id=garden_id,
        db=db,
    )


def _commit_required_lifecycle_event(
    request: Request,
    *,
    auth_context: AuthContext | None,
    status_code: int,
    detail: str,
    db: DbConn,
    garden_id: int | None = None,
) -> None:
    request.state.audited_by_handler = True
    try:
        audit_values = write_required_audit_event(
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            remote_host=_remote_host(request),
            detail=detail,
            auth_context=auth_context,
            garden_id=garden_id,
            db=db,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    enqueue_audit_event_telemetry(audit_values, db=db)


def _normalize_action_reason(
    request: Request,
    *,
    body_reason: str = "",
) -> str:
    reason = body_reason.strip() or request.headers.get("x-action-reason", "").strip()
    if not reason:
        return "unspecified"
    return reason[:400]


def _require_action_reason(
    request: Request,
    *,
    body_reason: str = "",
) -> str:
    reason = body_reason.strip() or request.headers.get("x-action-reason", "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Action reason is required")
    return reason[:400]


def _admin_step_up_window_ms() -> int:
    raw = os.environ.get("AUTH_ADMIN_STEP_UP_TTL_SECONDS", "").strip()
    try:
        seconds = int(raw) if raw else 15 * 60
    except ValueError:
        seconds = 15 * 60
    seconds = max(60, min(seconds, 24 * 60 * 60))
    return seconds * 1000


def _admin_mfa_enforced_for_context(context: AuthContext) -> bool:
    return _admin_mfa_enforced_for_role(context.role, mfa_enabled=context.mfa_enabled)


def _context_has_strong_admin_auth(context: AuthContext) -> bool:
    return context.mfa_enabled or int(context.mfa_authenticated_at_ms or 0) > 0


def enforce_destructive_admin_controls(
    request: Request,
    *,
    body_reason: str = "",
) -> tuple[AuthContext, str]:
    context = _require_admin_context(request)
    if context.auth_type != "session" or context.user_id is None or not context.session_token_hash:
        raise HTTPException(
            status_code=403,
            detail="Session-backed admin authentication required",
        )

    action_reason = _require_action_reason(request, body_reason=body_reason)
    if context.must_change_password:
        raise HTTPException(status_code=403, detail="Password change is required")
    if context.mfa_setup_required:
        raise HTTPException(status_code=403, detail="Admin MFA setup is required")
    if _admin_mfa_enforced_for_context(context):
        if not _context_has_strong_admin_auth(context):
            raise HTTPException(
                status_code=403,
                detail="Platform-admin MFA or passkey authentication is required",
            )
        if int(context.mfa_authenticated_at_ms or 0) <= 0:
            raise HTTPException(status_code=403, detail="MFA-backed session required")
    reauthenticated_at_ms = int(context.reauthenticated_at_ms or 0)
    if reauthenticated_at_ms <= 0:
        raise HTTPException(status_code=403, detail="Recent reauthentication required")
    if (reauthenticated_at_ms + _admin_step_up_window_ms()) < current_timestamp_ms():
        raise HTTPException(status_code=403, detail="Recent reauthentication required")
    return context, action_reason


def _require_session_context(request: Request) -> AuthContext:
    context = resolve_request_auth_context(request)
    if context.auth_type != "session" or context.user_id is None or not context.session_token_hash:
        raise HTTPException(status_code=400, detail="Session auth user is required")
    if context.must_change_password:
        raise HTTPException(status_code=403, detail="Password change is required")
    return context


def _require_recent_session_context(
    request: Request,
    *,
    allow_mfa_setup: bool = False,
) -> AuthContext:
    context = _require_session_context(request)
    if context.mfa_setup_required and not allow_mfa_setup:
        raise HTTPException(status_code=403, detail="Admin MFA setup is required")
    reauthenticated_at_ms = int(context.reauthenticated_at_ms or 0)
    if reauthenticated_at_ms <= 0:
        raise HTTPException(status_code=403, detail="Recent reauthentication required")
    if (reauthenticated_at_ms + _admin_step_up_window_ms()) < current_timestamp_ms():
        raise HTTPException(status_code=403, detail="Recent reauthentication required")
    return context


def _user_has_passkey(db: DB, user_id: int) -> bool:
    row = db.execute(
        "SELECT COUNT(*) AS count FROM auth_passkeys WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    return bool(row and int(row["count"] or 0) > 0)


def _verify_current_password_for_context(
    db: DB,
    *,
    context: AuthContext,
    current_password: str,
) -> None:
    if not current_password.strip():
        raise HTTPException(status_code=403, detail="Current password is required")
    if context.user_id is None:
        raise HTTPException(status_code=400, detail="Session auth user is required")
    user_row = db.execute(
        """
        SELECT id, password_hash, password_auth_disabled, is_active
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (context.user_id,),
    ).fetchone()
    if not user_row or int(user_row["is_active"]) != 1:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if int(user_row["password_auth_disabled"]) == 1 or user_row["password_hash"] is None:
        raise HTTPException(status_code=403, detail="Password authentication is unavailable")
    if not verify_password_and_upgrade(
        db,
        user_id=int(user_row["id"]),
        password=current_password,
        password_hash=str(user_row["password_hash"]),
    ):
        record_security_event("auth_failures")
        record_security_event("auth_passkey_registration_password_failures")
        db.commit()
        raise HTTPException(status_code=401, detail="Current password is incorrect")


def _authorize_passkey_registration(
    db: DB,
    *,
    context: AuthContext,
    current_password: str,
) -> None:
    _verify_current_password_for_context(
        db,
        context=context,
        current_password=current_password,
    )
    if (
        context.role == "admin"
        and admin_mfa_required()
        and (context.mfa_enabled or _user_has_passkey(db, int(context.user_id)))
        and int(context.mfa_authenticated_at_ms or 0) <= 0
    ):
        raise HTTPException(
            status_code=403,
            detail="Passkey or MFA authentication required to add another passkey",
        )


def _verify_and_update_passkey_authentication(
    db: DB,
    *,
    challenge: passkeys.ConsumedPasskeyChallenge,
    credential: dict[str, Any],
    expected_user_id: int | None = None,
) -> dict[str, object]:
    try:
        credential_id = passkeys.credential_id_from_public_key_credential(credential)
    except HTTPException:
        record_security_event("auth_failures")
        record_security_event("auth_passkey_failures")
        db.commit()
        raise
    row = db.execute(
        """
        SELECT
            p.id AS passkey_id,
            p.user_id,
            p.credential_public_key,
            p.sign_count,
            u.username,
            u.role,
            u.is_active,
            u.must_change_password
        FROM auth_passkeys p
        JOIN auth_users u ON u.id = p.user_id
        WHERE p.credential_id = %s
        LIMIT 1
        """,
        (credential_id,),
    ).fetchone()
    if (
        not row
        or int(row["is_active"]) != 1
        or (expected_user_id is not None and int(row["user_id"]) != expected_user_id)
        or (challenge.user_id is not None and int(row["user_id"]) != challenge.user_id)
    ):
        record_security_event("auth_failures")
        record_security_event("auth_passkey_failures")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid passkey authentication")
    try:
        verified = passkeys.verify_authentication_credential(
            credential=credential,
            expected_challenge=challenge.challenge,
            credential_public_key=passkeys.b64decode(str(row["credential_public_key"])),
            credential_current_sign_count=int(row["sign_count"]),
        )
    except HTTPException:
        record_security_event("auth_failures")
        record_security_event("auth_passkey_failures")
        db.commit()
        raise
    stored_sign_count = int(row["sign_count"])
    if verified.new_sign_count == 0 and stored_sign_count > 0:
        record_security_event("auth_failures")
        record_security_event("auth_passkey_failures")
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid passkey authentication")
    now_ms = current_timestamp_ms()
    updated = db.execute(
        """
        UPDATE auth_passkeys
        SET
            sign_count = %s,
            credential_device_type = %s,
            credential_backed_up = %s,
            updated_at_ms = %s,
            last_used_at_ms = %s
        WHERE id = %s
          AND (%s = 0 OR sign_count < %s)
        RETURNING id
        """,
        (
            verified.new_sign_count,
            verified.credential_device_type,
            int(verified.credential_backed_up),
            now_ms,
            now_ms,
            int(row["passkey_id"]),
            verified.new_sign_count,
            verified.new_sign_count,
        ),
    ).fetchone()
    if not updated:
        record_security_event("auth_failures")
        record_security_event("auth_passkey_failures")
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid passkey authentication")
    return dict(row)


def _raise_public_passkey_login_failure(db: DB) -> NoReturn:
    record_security_event("auth_failures")
    record_security_event("auth_passkey_failures")
    db.commit()
    raise HTTPException(status_code=401, detail="Invalid passkey authentication")


def _record_destructive_admin_action(metric_suffix: str) -> None:
    record_security_event("destructive_admin_actions")
    record_security_event(f"destructive_admin_actions_{metric_suffix}")


def _lifecycle_detail(event: str, **fields: object) -> str:
    return f"{event} {json.dumps(fields, sort_keys=True, separators=(',', ':'))}"


def _role_rank(role: str) -> int:
    if role == "admin":
        return 2
    if role == "editor":
        return 1
    return 0


def _enforce_lifecycle_rate_limit(
    request: Request,
    *,
    bucket: str,
    env_name: str,
    default_limit: int = 20,
) -> None:
    enforce_rate_limit(
        request,
        bucket=bucket,
        limit=env_int(env_name, default_limit),
        window_seconds=60,
    )


def _record_invalid_reset_password_attempt() -> None:
    record_security_event("invalid_reset_password_attempts")


def _record_invalid_invitation_attempt() -> None:
    record_security_event("invalid_invitation_attempts")


def _require_user_lifecycle_enabled() -> None:
    if not user_lifecycle_enabled():
        raise HTTPException(status_code=404, detail="User lifecycle is disabled")


def _current_user_mfa_settings(
    db: DbConn,
    *,
    user_id: int | None,
    role: str | None = None,
) -> dict[str, object]:
    if user_id is None:
        return {
            "enabled": False,
            "setup_required": False,
            "enrolled_at": None,
            "pending_enrollment": False,
            "pending_expires_at_ms": None,
            "recovery_codes_remaining": 0,
            "methods": [],
        }
    status = get_user_mfa_status(db, user_id)
    if role is not None and role != "admin":
        status["setup_required"] = False
    elif role == "admin" and passkeys.passkeys_configured():
        row = db.execute(
            "SELECT COUNT(*) AS count FROM auth_passkeys WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        if row and int(row["count"] or 0) > 0:
            status["setup_required"] = False
            methods = list(cast(list[str], status.get("methods") or []))
            if "passkey" not in methods:
                methods.append("passkey")
            status["methods"] = methods
    return status


def _auth_user_select_fields(user_alias: str) -> str:
    return f"""
        {user_alias}.id,
        {user_alias}.username,
        {user_alias}.role,
        {user_alias}.is_active,
        {user_alias}.must_change_password,
        {user_alias}.created_by_user_id,
        {user_alias}.deactivated_at,
        {user_alias}.deactivated_reason,
        {user_alias}.created_at,
        {user_alias}.last_login_at,
        {user_alias}.mfa_totp_enabled,
        {user_alias}.mfa_enrolled_at,
        {user_alias}.subscription_tier,
        (
            SELECT gm.garden_id
            FROM garden_memberships gm
            JOIN gardens g ON g.id = gm.garden_id
            WHERE gm.user_id = {user_alias}.id
              AND gm.role IN ('admin', 'editor')
              AND g.slug <> 'default'
            ORDER BY
                CASE WHEN gm.user_id = g.owner_user_id THEN 0 ELSE 1 END,
                CASE gm.role
                    WHEN 'admin' THEN 0
                    WHEN 'editor' THEN 1
                    ELSE 2
                END,
                gm.garden_id
            LIMIT 1
        ) AS managed_garden_id,
        (
            SELECT g.name
            FROM garden_memberships gm
            JOIN gardens g ON g.id = gm.garden_id
            WHERE gm.user_id = {user_alias}.id
              AND gm.role IN ('admin', 'editor')
              AND g.slug <> 'default'
            ORDER BY
                CASE WHEN gm.user_id = g.owner_user_id THEN 0 ELSE 1 END,
                CASE gm.role
                    WHEN 'admin' THEN 0
                    WHEN 'editor' THEN 1
                    ELSE 2
                END,
                gm.garden_id
            LIMIT 1
        ) AS managed_garden_name,
        (
            SELECT g.onboarding_complete
            FROM garden_memberships gm
            JOIN gardens g ON g.id = gm.garden_id
            WHERE gm.user_id = {user_alias}.id
              AND gm.role IN ('admin', 'editor')
              AND g.slug <> 'default'
            ORDER BY
                CASE WHEN gm.user_id = g.owner_user_id THEN 0 ELSE 1 END,
                CASE gm.role
                    WHEN 'admin' THEN 0
                    WHEN 'editor' THEN 1
                    ELSE 2
                END,
                gm.garden_id
            LIMIT 1
        ) AS managed_garden_onboarding_complete,
        (
            SELECT COUNT(*)
            FROM garden_memberships gm
            JOIN gardens g ON g.id = gm.garden_id
            WHERE gm.user_id = {user_alias}.id
              AND gm.role IN ('admin', 'editor')
              AND g.slug <> 'default'
        ) AS managed_garden_count
    """


def _serialize_auth_user(row: dict[str, Any]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "is_active": bool(int(row["is_active"])),
        "must_change_password": bool(int(row["must_change_password"])),
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None
        ),
        "deactivated_at": row["deactivated_at"],
        "deactivated_reason": row["deactivated_reason"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "mfa_enabled": bool(int(row["mfa_totp_enabled"])),
        "mfa_enrolled_at": row["mfa_enrolled_at"],
        "subscription_tier": str(row["subscription_tier"] or "home"),
        "managed_garden_id": (
            int(row["managed_garden_id"]) if row["managed_garden_id"] is not None else None
        ),
        "managed_garden_name": (
            str(row["managed_garden_name"]) if row["managed_garden_name"] is not None else None
        ),
        "managed_garden_onboarding_complete": (
            bool(int(row["managed_garden_onboarding_complete"]))
            if row["managed_garden_onboarding_complete"] is not None
            else None
        ),
        "managed_garden_count": int(row["managed_garden_count"] or 0),
    }


def _serialize_user_invitation(row: dict[str, Any], *, now_ms: int) -> dict[str, object]:
    accepted_at_ms = int(row["accepted_at_ms"]) if row["accepted_at_ms"] is not None else None
    revoked_at_ms = int(row["revoked_at_ms"]) if row["revoked_at_ms"] is not None else None
    expires_at_ms = int(row["expires_at_ms"])
    status = "pending"
    if revoked_at_ms is not None:
        status = "revoked"
    elif accepted_at_ms is not None:
        status = "accepted"
    elif expires_at_ms <= now_ms:
        status = "expired"
    return {
        "id": int(row["id"]),
        "invitee_username": str(row["invitee_username"]),
        "role": str(row["role"]),
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None
        ),
        "created_at_ms": int(row["created_at_ms"]),
        "expires_at_ms": expires_at_ms,
        "accepted_at_ms": accepted_at_ms,
        "accepted_user_id": (
            int(row["accepted_user_id"]) if row["accepted_user_id"] is not None else None
        ),
        "revoked_at_ms": revoked_at_ms,
        "status": status,
        "scope": "personal_garden",
    }


def _load_auth_user(db: DbConn, user_id: int) -> dict[str, Any]:
    row = db.execute(
        f"""
        SELECT
            {_auth_user_select_fields("u")}
        FROM auth_users u
        WHERE u.id = %s
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def _active_admin_count(
    db: DbConn,
    *,
    exclude_user_id: int | None = None,
) -> int:
    if exclude_user_id is None:
        row = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM auth_users
            WHERE role = 'admin' AND is_active = 1
            """,
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM auth_users
            WHERE role = 'admin' AND is_active = 1 AND id != %s
            """,
            (exclude_user_id,),
        ).fetchone()
    return int(row["c"] if row else 0)


def _revoke_sessions_for_user(
    db: DbConn,
    *,
    user_id: int,
    except_token_hash: str | None = None,
) -> int:
    if except_token_hash:
        cursor = db.execute(
            """
            DELETE FROM auth_sessions
            WHERE user_id = %s AND token_hash != %s
            """,
            (user_id, except_token_hash),
        )
    else:
        cursor = db.execute(
            """
            DELETE FROM auth_sessions
            WHERE user_id = %s
            """,
            (user_id,),
        )
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def _revoke_or_mark_admin_promotion_sessions(
    db: DbConn,
    *,
    user_id: int,
    mfa_setup_required: bool,
) -> int:
    if mfa_setup_required:
        cursor = db.execute(
            """
            UPDATE auth_sessions
            SET mfa_setup_required = 1,
                mfa_authenticated_at_ms = 0,
                reauthenticated_at_ms = 0
            WHERE user_id = %s
            """,
            (user_id,),
        )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)
    return _revoke_sessions_for_user(db, user_id=user_id)


def _reset_token_ttl_minutes(requested: int | None) -> int:
    raw = (
        str(requested)
        if requested is not None
        else os.environ.get(
            "AUTH_PASSWORD_RESET_TOKEN_TTL_MINUTES",
            "",
        ).strip()
    )
    try:
        parsed = int(raw) if raw else 60
    except ValueError:
        parsed = 60
    return max(5, min(parsed, 24 * 60))


def _user_invitation_ttl_minutes(requested: int | None) -> int:
    raw = (
        str(requested)
        if requested is not None
        else os.environ.get(
            "AUTH_USER_INVITATION_TTL_MINUTES",
            "",
        ).strip()
    )
    try:
        parsed = int(raw) if raw else 7 * 24 * 60
    except ValueError:
        parsed = 7 * 24 * 60
    return max(5, min(parsed, 30 * 24 * 60))


def _raise_invalid_invitation_token() -> NoReturn:
    _record_invalid_invitation_attempt()
    raise HTTPException(status_code=400, detail=_GENERIC_INVITATION_TOKEN_DETAIL)


def _invitation_is_open(row: dict[str, Any], *, now_ms: int) -> bool:
    return (
        row["accepted_at_ms"] is None
        and row["revoked_at_ms"] is None
        and int(row["expires_at_ms"]) > now_ms
    )


def _select_active_invitation(
    db: DB,
    *,
    scope: InvitationScope,
    token_hash: str,
    now_ms: int,
    invitation_id: int | None = None,
) -> dict[str, Any] | None:
    id_clause = "AND id = %s" if invitation_id is not None else ""
    params: tuple[object, ...] = (
        (token_hash, invitation_id) if invitation_id is not None else (token_hash,)
    )
    if scope == "garden":
        row = db.execute(
            f"""
            SELECT
                id,
                garden_id,
                invitee_username,
                role,
                expires_at_ms,
                accepted_at_ms,
                revoked_at_ms
            FROM garden_invitations
            WHERE token_hash = %s
              {id_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    else:
        row = db.execute(
            f"""
            SELECT
                id,
                invitee_username,
                role,
                expires_at_ms,
                accepted_at_ms,
                revoked_at_ms
            FROM auth_user_invitations
            WHERE token_hash = %s
              {id_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    invitation = dict(row)
    if not _invitation_is_open(invitation, now_ms=now_ms):
        return None
    return invitation


def _load_active_invitation_by_token(
    db: DB,
    *,
    token_hash: str,
    now_ms: int,
) -> tuple[InvitationScope, dict[str, Any]] | None:
    garden_invitation = _select_active_invitation(
        db,
        scope="garden",
        token_hash=token_hash,
        now_ms=now_ms,
    )
    if garden_invitation is not None:
        return "garden", garden_invitation
    personal_invitation = _select_active_invitation(
        db,
        scope="personal_garden",
        token_hash=token_hash,
        now_ms=now_ms,
    )
    if personal_invitation is not None:
        return "personal_garden", personal_invitation
    return None


def _require_invitation_passkey_session_mode() -> None:
    if not is_auth_required():
        raise HTTPException(status_code=400, detail="Authentication is not required")
    if not session_auth_enabled():
        raise HTTPException(status_code=400, detail="Session auth mode is disabled")


def _username_match_key(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())


def _validated_passwordless_invitation_role(
    *,
    invitation_scope: InvitationScope,
    invitation: dict[str, Any],
) -> Literal["viewer", "editor"]:
    invitation_role = str(invitation["role"])
    if invitation_scope == "garden":
        if invitation_role != "viewer":
            _raise_invalid_invitation_token()
        return "viewer"
    if invitation_role not in {"viewer", "editor"}:
        _raise_invalid_invitation_token()
    return cast(Literal["viewer", "editor"], invitation_role)


def _validate_passwordless_invitation_candidate(
    db: DB,
    *,
    invitation_scope: InvitationScope,
    invitation: dict[str, Any],
    username: str,
) -> str:
    invitee_username = str(invitation["invitee_username"]).strip()
    if not invitee_username or _username_match_key(username) != _username_match_key(
        invitee_username
    ):
        _raise_invalid_invitation_token()
    _validated_passwordless_invitation_role(
        invitation_scope=invitation_scope,
        invitation=invitation,
    )
    existing_user = db.execute(
        """
        SELECT id
        FROM auth_users
        WHERE username = %s
        LIMIT 1
        """,
        (invitee_username,),
    ).fetchone()
    if existing_user:
        _raise_invalid_invitation_token()
    return invitee_username


def _accept_invitation_atomically(
    db: DB,
    *,
    invitation_scope: InvitationScope,
    invitation: dict[str, Any],
    token_hash: str,
    user_id: int,
    now_ms: int,
    membership_role: str | None = None,
) -> None:
    invitation_role = str(membership_role or invitation["role"])
    if invitation_scope == "garden":
        accepted = db.execute(
            """
            UPDATE garden_invitations
            SET accepted_at_ms = %s, accepted_user_id = %s
            WHERE id = %s
              AND token_hash = %s
              AND accepted_at_ms IS NULL
              AND revoked_at_ms IS NULL
              AND expires_at_ms > %s
            RETURNING id
            """,
            (
                now_ms,
                user_id,
                int(invitation["id"]),
                token_hash,
                now_ms,
            ),
        ).fetchone()
    else:
        accepted = db.execute(
            """
            UPDATE auth_user_invitations
            SET accepted_at_ms = %s, accepted_user_id = %s
            WHERE id = %s
              AND token_hash = %s
              AND accepted_at_ms IS NULL
              AND revoked_at_ms IS NULL
              AND expires_at_ms > %s
            RETURNING id
            """,
            (
                now_ms,
                user_id,
                int(invitation["id"]),
                token_hash,
                now_ms,
            ),
        ).fetchone()
    if not accepted:
        _raise_invalid_invitation_token()
    if invitation_scope == "garden":
        db.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT(garden_id, user_id) DO UPDATE SET
                role = excluded.role
            """,
            (
                int(invitation["garden_id"]),
                user_id,
                invitation_role,
            ),
        )


@router.get("/auth/status")
def auth_status(db: DB) -> dict[str, object]:
    bootstrap_required = False
    if is_auth_required() and session_auth_enabled():
        bootstrap_required = count_users(db) == 0
    return {
        "auth_required": is_auth_required(),
        "auth_mode": auth_mode(),
        "session_auth_enabled": session_auth_enabled(),
        "api_key_auth_enabled": api_key_auth_enabled(),
        "bootstrap_required": bootstrap_required,
        "user_lifecycle_enabled": user_lifecycle_enabled(),
        "admin_mfa_required": admin_mfa_required(),
        "passkeys_enabled": passkeys.passkeys_configured(),
    }


@router.get("/auth/password-policy")
def auth_password_policy() -> dict[str, object]:
    return get_password_policy()


@router.post("/auth/bootstrap", status_code=201)
def bootstrap_auth_user(body: BootstrapBody, request: Request, db: DB) -> dict[str, object]:
    if not session_auth_enabled():
        raise HTTPException(status_code=400, detail="Session auth mode is disabled")
    if not is_loopback_client(request):
        raise HTTPException(status_code=403, detail="Bootstrap is allowed from loopback only")
    if count_users(db) > 0:
        raise HTTPException(status_code=409, detail="Bootstrap already completed")
    created = create_user(
        db,
        username=body.username,
        password=body.password,
        role=body.role,
    )
    db.commit()
    return {"status": "ok", "user": created}


@router.post("/auth/login")
def auth_login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: DB,
) -> dict[str, object]:
    if not is_auth_required():
        raise HTTPException(status_code=400, detail="Authentication is not required")
    if not session_auth_enabled():
        raise HTTPException(status_code=400, detail="Session auth mode is disabled")
    enforce_rate_limit(
        request,
        bucket="auth-login",
        limit=env_int("AUTH_LOGIN_RATE_LIMIT", 20),
        window_seconds=60,
    )
    if count_users(db) == 0:
        raise HTTPException(
            status_code=409,
            detail="No users exist yet. Run /api/auth/bootstrap from loopback first.",
        )
    login_candidate = _load_login_candidate(db, body.username)
    username_rate_key = _hashed_rate_limit_key("username", body.username)
    _enforce_optional_key_rate_limit(
        bucket="auth-login-username",
        key=username_rate_key,
        env_name="AUTH_LOGIN_USERNAME_RATE_LIMIT",
        default_limit=8,
        scope_label="Username",
    )
    if login_candidate and str(login_candidate["role"]) == "admin":
        _enforce_optional_key_rate_limit(
            bucket="auth-login-admin-username",
            key=username_rate_key,
            env_name="AUTH_LOGIN_ADMIN_USERNAME_RATE_LIMIT",
            default_limit=4,
            scope_label="Admin account",
        )
        _enforce_optional_key_rate_limit(
            bucket="auth-login-admin-host",
            key=_hashed_rate_limit_key("host", _remote_host(request), casefold=False),
            env_name="AUTH_LOGIN_ADMIN_HOST_RATE_LIMIT",
            default_limit=10,
            scope_label="Admin host",
        )
    _enforce_adaptive_friction(
        flow="login",
        friction_provider=body.friction_provider,
        friction_token=body.friction_token,
    )
    remote = _remote_host(request)
    user = authenticate_user_credentials(body.username, body.password)
    if not user:
        record_security_event("auth_failures")
        record_security_event("auth_login_failures")
        if login_candidate and str(login_candidate["role"]) == "admin":
            record_security_event("auth_login_failures_admin")
        logger.warning(
            "Login failed: user=%r ip=%s",
            body.username[:3] + "***" if len(body.username) > 3 else "***",
            remote,
        )
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user_id = int(user["id"])
    role = str(user["role"])
    mfa_enabled = bool(user.get("mfa_enabled"))
    if _admin_mfa_enforced_for_role(role, mfa_enabled=mfa_enabled) and mfa_enabled:
        second_factor_ok, second_factor_method = verify_user_second_factor(
            db,
            user_id=user_id,
            mfa_code=body.mfa_code,
            recovery_code=body.recovery_code,
        )
        if not second_factor_ok:
            record_security_event("auth_failures")
            record_security_event("auth_mfa_failures")
            record_security_event("auth_login_mfa_required")
            logger.info(
                "Login challenged for MFA: user=%r role=%s ip=%s",
                user["username"],
                role,
                remote,
            )
            return {
                "status": "mfa_required",
                "user": {
                    "username": user["username"],
                    "role": role,
                    "must_change_password": bool(user.get("must_change_password")),
                },
                "mfa": {
                    "required": True,
                    "setup_required": False,
                    "methods": ["totp", "recovery_code"],
                },
            }
    else:
        second_factor_method = ""

    mfa_enforced = _admin_mfa_enforced_for_role(role, mfa_enabled=mfa_enabled)
    strong_factor_enrolled = mfa_enabled or _user_has_passkey(db, user_id)
    session_requires_setup = mfa_enforced and not strong_factor_enrolled
    if second_factor_method:
        db.commit()
    token, expires_at_ms = create_session_for_user(
        user_id,
        mfa_authenticated=bool(second_factor_method),
        mfa_setup_required=session_requires_setup,
        device_label=_session_device_label(request),
        location_hint=_session_location_hint(request),
    )
    db.execute(
        "UPDATE auth_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = %s",
        (user_id,),
    )
    db.commit()
    logger.info(
        "Login successful: user=%r role=%s ip=%s mfa_setup_required=%s mfa_method=%s",
        user["username"],
        role,
        remote,
        session_requires_setup,
        second_factor_method or "none",
    )
    ttl_seconds = max(1, int((expires_at_ms - int(time.time() * 1000)) / 1000))
    cookie_kwargs: SessionCookieKwargs = {
        "max_age": ttl_seconds,
        "secure": session_cookie_secure(),
        "samesite": cast(SameSiteValue, session_cookie_samesite()),
        "path": session_cookie_path(),
        "domain": session_cookie_domain(),
    }
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        httponly=True,
        **cookie_kwargs,
    )
    csrf_token = csrf_token_for_session_token(token)
    response.set_cookie(
        key=csrf_cookie_name(),
        value=csrf_token,
        httponly=False,
        **cookie_kwargs,
    )
    status = "ok"
    if bool(user.get("must_change_password")):
        status = "password_change_required"
    elif session_requires_setup:
        status = "mfa_setup_required"
    return {
        "status": status,
        "expires_at_ms": expires_at_ms,
        "user": {
            "username": user["username"],
            "role": role,
            "must_change_password": bool(user.get("must_change_password")),
        },
        "mfa": {
            "required": bool(second_factor_method),
            "setup_required": session_requires_setup,
            "methods": ["totp", "recovery_code"] if mfa_enabled else [],
            "method": second_factor_method or None,
        },
    }


@router.get("/auth/passkeys")
def auth_passkeys(request: Request, db: DB) -> dict[str, object]:
    context = _require_session_context(request)
    rows = db.execute(
        """
        SELECT id, nickname, created_at_ms, last_used_at_ms, transports,
               credential_device_type, credential_backed_up
        FROM auth_passkeys
        WHERE user_id = %s
        ORDER BY created_at_ms DESC, id DESC
        """,
        (context.user_id,),
    ).fetchall()
    return {"passkeys": [passkeys.serialize_passkey(dict(row)) for row in rows]}


@router.post("/auth/passkeys/register/options")
def auth_passkey_register_options(
    body: PasskeyRegistrationOptionsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_recent_session_context(request, allow_mfa_setup=True)
    assert context.user_id is not None
    assert context.session_token_hash is not None
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-passkey-register-options",
        env_name="AUTH_PASSKEY_REGISTER_RATE_LIMIT",
        default_limit=10,
    )
    _authorize_passkey_registration(
        db,
        context=context,
        current_password=body.current_password,
    )
    passkeys.require_passkeys_configured()
    challenge = passkeys.create_challenge(
        db,
        flow="registration",
        user_id=context.user_id,
        session_token_hash=context.session_token_hash,
    )
    public_key = passkeys.registration_options_for_user(
        db,
        user_id=context.user_id,
        username=context.username,
        challenge=challenge.challenge,
        user_handle=str(
            db.execute(
                "SELECT passkey_user_handle FROM auth_users WHERE id = %s",
                (context.user_id,),
            ).fetchone()["passkey_user_handle"]
        ),
    )
    db.commit()
    return {"challenge_token": challenge.token, "publicKey": public_key}


@router.post("/auth/passkeys/register/verify", status_code=201)
def auth_passkey_register_verify(
    body: PasskeyRegistrationVerifyBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_recent_session_context(request, allow_mfa_setup=True)
    assert context.user_id is not None
    assert context.session_token_hash is not None
    challenge = passkeys.consume_challenge(
        db,
        token=body.challenge_token,
        flow="registration",
        user_id=context.user_id,
        session_token_hash=context.session_token_hash,
    )
    try:
        verified = passkeys.verify_registration_credential(
            credential=body.credential,
            expected_challenge=challenge.challenge,
        )
    except HTTPException:
        db.commit()
        raise
    now_ms = current_timestamp_ms()
    credential_id = passkeys.encode_public_key(verified.credential_id)
    row = db.execute(
        """
        INSERT INTO auth_passkeys (
            user_id,
            credential_id,
            credential_public_key,
            sign_count,
            nickname,
            transports,
            credential_device_type,
            credential_backed_up,
            created_at_ms,
            updated_at_ms,
            last_used_at_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
        ON CONFLICT (credential_id) DO NOTHING
        RETURNING id, nickname, created_at_ms, last_used_at_ms, transports,
                  credential_device_type, credential_backed_up
        """,
        (
            context.user_id,
            credential_id,
            passkeys.encode_public_key(verified.credential_public_key),
            verified.sign_count,
            body.nickname.strip(),
            passkeys.credential_transports(body.credential),
            verified.credential_device_type,
            int(verified.credential_backed_up),
            now_ms,
            now_ms,
        ),
    ).fetchone()
    if not row:
        db.commit()
        raise HTTPException(status_code=409, detail="Passkey is already registered")
    db.execute(
        "UPDATE auth_sessions SET mfa_setup_required = 0 WHERE token_hash = %s",
        (context.session_token_hash,),
    )
    request.state.auth_context = replace(
        context,
        mfa_setup_required=False,
        passkey_enrolled=True,
    )
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=201,
        detail=_lifecycle_detail(
            "auth.passkey.register",
            user_id=context.user_id,
            passkey_id=int(row["id"]),
        ),
        db=db,
    )
    return {"status": "ok", "passkey": passkeys.serialize_passkey(dict(row))}


@router.post("/auth/passkeys/prompt/dismiss")
def auth_passkey_prompt_dismiss(
    body: PasskeyPromptDismissBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_session_context(request)
    assert context.user_id is not None
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-passkey-prompt-dismiss",
        env_name="AUTH_PASSKEY_PROMPT_DISMISS_RATE_LIMIT",
        default_limit=20,
    )
    now_ms = current_timestamp_ms()
    dismissed_until_ms = now_ms + (body.dismiss_for_days * 24 * 60 * 60 * 1000)
    db.execute(
        """
        UPDATE auth_users
        SET passkey_prompt_dismissed_until_ms = %s
        WHERE id = %s
        """,
        (dismissed_until_ms, context.user_id),
    )
    db.commit()
    request.state.auth_context = replace(
        context,
        passkey_prompt_dismissed_until_ms=dismissed_until_ms,
    )
    return {
        "status": "ok",
        "passkey_prompt_dismissed_until_ms": dismissed_until_ms,
    }


@router.delete("/auth/passkeys/{passkey_id}")
def auth_passkey_delete(
    passkey_id: int,
    body: PasskeyActionBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_recent_session_context(request)
    action_reason = _normalize_action_reason(request, body_reason=body.action_reason)
    row = db.execute(
        """
        DELETE FROM auth_passkeys
        WHERE id = %s AND user_id = %s
        RETURNING id
        """,
        (passkey_id, context.user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Passkey not found")
    remaining_passkeys = db.execute(
        "SELECT COUNT(*) AS count FROM auth_passkeys WHERE user_id = %s",
        (context.user_id,),
    ).fetchone()
    current_requires_setup = (
        context.role == "admin"
        and admin_mfa_required()
        and not context.mfa_enabled
        and int(remaining_passkeys["count"] if remaining_passkeys else 0) <= 0
    )
    now_ms = current_timestamp_ms()
    if current_requires_setup:
        _revoke_sessions_for_user(
            db,
            user_id=int(context.user_id),
            except_token_hash=context.session_token_hash,
        )
        if context.session_token_hash:
            db.execute(
                """
                UPDATE auth_sessions
                SET
                    mfa_authenticated_at_ms = 0,
                    mfa_setup_required = 1,
                    reauthenticated_at_ms = %s
                WHERE token_hash = %s
                """,
                (now_ms, context.session_token_hash),
            )
    if current_requires_setup:
        request.state.auth_context = replace(
            context,
            mfa_authenticated_at_ms=0,
            mfa_setup_required=True,
            reauthenticated_at_ms=now_ms,
        )
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.passkey.delete",
            user_id=context.user_id,
            passkey_id=passkey_id,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {"status": "ok", "passkey_id": passkey_id}


@router.patch("/auth/passkeys/{passkey_id}")
def auth_passkey_rename(
    passkey_id: int,
    body: PasskeyRenameBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_recent_session_context(request)
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="Passkey nickname is required")
    action_reason = _normalize_action_reason(request, body_reason=body.action_reason)
    row = db.execute(
        """
        UPDATE auth_passkeys
        SET nickname = %s, updated_at_ms = %s
        WHERE id = %s AND user_id = %s
        RETURNING id, nickname, created_at_ms, last_used_at_ms, transports,
                  credential_device_type, credential_backed_up
        """,
        (nickname, current_timestamp_ms(), passkey_id, context.user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Passkey not found")
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.passkey.rename",
            user_id=context.user_id,
            passkey_id=passkey_id,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {"status": "ok", "passkey": passkeys.serialize_passkey(dict(row))}


@router.post("/auth/passkeys/login/options")
def auth_passkey_login_options(
    body: PasskeyLoginOptionsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    if not is_auth_required():
        raise HTTPException(status_code=400, detail="Authentication is not required")
    if not session_auth_enabled():
        raise HTTPException(status_code=400, detail="Session auth mode is disabled")
    passkeys.require_passkeys_configured()
    enforce_rate_limit(
        request,
        bucket="auth-passkey-login",
        limit=env_int("AUTH_PASSKEY_LOGIN_RATE_LIMIT", 20),
        window_seconds=60,
    )
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    user_id: int | None = None
    login_candidate = _load_login_candidate(db, username)
    if (
        login_candidate
        and int(login_candidate["is_active"]) == 1
        and _user_has_passkey(db, int(login_candidate["id"]))
    ):
        user_id = int(login_candidate["id"])
    challenge = passkeys.create_challenge(
        db,
        flow="authentication" if user_id is not None else "authentication_denied",
        user_id=user_id,
    )
    public_key = passkeys.authentication_options(
        db,
        challenge=challenge.challenge,
        user_id=user_id,
        include_allow_credentials=False,
    )
    db.commit()
    return {"challenge_token": challenge.token, "publicKey": public_key}


@router.post("/auth/passkeys/login/verify")
def auth_passkey_login_verify(
    body: PasskeyLoginVerifyBody,
    request: Request,
    response: Response,
    db: DB,
) -> dict[str, object]:
    if not is_auth_required():
        raise HTTPException(status_code=400, detail="Authentication is not required")
    if not session_auth_enabled():
        raise HTTPException(status_code=400, detail="Session auth mode is disabled")
    enforce_rate_limit(
        request,
        bucket="auth-passkey-login",
        limit=env_int("AUTH_PASSKEY_LOGIN_RATE_LIMIT", 20),
        window_seconds=60,
    )
    try:
        challenge = passkeys.consume_public_authentication_challenge(
            db,
            token=body.challenge_token,
        )
    except HTTPException:
        _raise_public_passkey_login_failure(db)
    if challenge.user_id is None:
        _raise_public_passkey_login_failure(db)
    try:
        row = _verify_and_update_passkey_authentication(
            db,
            challenge=challenge,
            credential=body.credential,
            expected_user_id=challenge.user_id,
        )
    except HTTPException as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid passkey authentication",
        ) from exc
    user_id = int(row["user_id"])
    token, expires_at_ms = create_session_for_user(
        user_id,
        mfa_authenticated=True,
        mfa_setup_required=False,
        device_label=_session_device_label(request),
        location_hint=_session_location_hint(request),
    )
    db.execute(
        "UPDATE auth_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = %s",
        (user_id,),
    )
    db.commit()
    _set_session_cookies(response, token=token, expires_at_ms=expires_at_ms)
    remote = _remote_host(request)
    logger.info(
        "Passkey login successful: user=%r role=%s ip=%s",
        row["username"],
        row["role"],
        remote,
    )
    status = "password_change_required" if bool(int(row["must_change_password"])) else "ok"
    return {
        "status": status,
        "expires_at_ms": expires_at_ms,
        "user": {
            "username": row["username"],
            "role": row["role"],
            "must_change_password": bool(int(row["must_change_password"])),
        },
        "mfa": {
            "required": False,
            "setup_required": False,
            "methods": ["passkey"],
            "method": "passkey",
        },
    }


@router.post("/auth/reauthenticate/passkey/options")
def auth_passkey_reauthenticate_options(
    _body: PasskeyReauthenticateOptionsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_session_context(request)
    assert context.user_id is not None
    assert context.session_token_hash is not None
    passkeys.require_passkeys_configured()
    enforce_rate_limit(
        request,
        bucket="auth-passkey-reauthenticate",
        limit=env_int("AUTH_PASSKEY_LOGIN_RATE_LIMIT", 20),
        window_seconds=60,
    )
    if not _user_has_passkey(db, context.user_id):
        raise HTTPException(status_code=403, detail="Passkey authentication is not available")
    challenge = passkeys.create_challenge(
        db,
        flow="reauthentication",
        user_id=context.user_id,
        session_token_hash=context.session_token_hash,
    )
    public_key = passkeys.authentication_options(
        db,
        challenge=challenge.challenge,
        user_id=context.user_id,
    )
    db.commit()
    return {"challenge_token": challenge.token, "publicKey": public_key}


@router.post("/auth/reauthenticate/passkey/verify")
def auth_passkey_reauthenticate_verify(
    body: PasskeyLoginVerifyBody,
    request: Request,
    response: Response,
    db: DB,
) -> dict[str, object]:
    context = _require_session_context(request)
    assert context.user_id is not None
    assert context.session_token_hash is not None
    passkeys.require_passkeys_configured()
    enforce_rate_limit(
        request,
        bucket="auth-passkey-reauthenticate",
        limit=env_int("AUTH_PASSKEY_LOGIN_RATE_LIMIT", 20),
        window_seconds=60,
    )
    challenge = passkeys.consume_challenge(
        db,
        token=body.challenge_token,
        flow="reauthentication",
        user_id=context.user_id,
        session_token_hash=context.session_token_hash,
    )
    row = _verify_and_update_passkey_authentication(
        db,
        challenge=challenge,
        credential=body.credential,
        expected_user_id=context.user_id,
    )

    db.execute(
        "DELETE FROM auth_sessions WHERE token_hash = %s",
        (context.session_token_hash,),
    )
    db.commit()
    new_token, new_expires_at_ms = create_session_for_user(
        context.user_id,
        mfa_authenticated=True,
        mfa_setup_required=False,
        device_label=_session_device_label(request),
        location_hint=_session_location_hint(request),
    )
    now_ms = current_timestamp_ms()
    new_token_hash = sha256(new_token.encode("utf-8")).hexdigest()
    db.execute(
        """
        UPDATE auth_sessions
        SET
            reauthenticated_at_ms = %s,
            mfa_authenticated_at_ms = %s
        WHERE token_hash = %s
        """,
        (now_ms, now_ms, new_token_hash),
    )
    db.commit()
    _set_session_cookies(response, token=new_token, expires_at_ms=new_expires_at_ms)
    request.state.auth_context = replace(
        context,
        session_token_hash=new_token_hash,
        reauthenticated_at_ms=now_ms,
        mfa_authenticated_at_ms=now_ms,
        mfa_setup_required=False,
    )
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.session.reauthenticate",
            user_id=context.user_id,
            mfa_method="passkey",
            passkey_id=int(row["passkey_id"]),
        ),
        db=db,
    )
    new_csrf = csrf_token_for_session_token(new_token)
    return {
        "status": "ok",
        "reauthenticated_at_ms": now_ms,
        "reauthenticated_until_ms": now_ms + _admin_step_up_window_ms(),
        "mfa_authenticated_at_ms": now_ms,
        "csrf_token": new_csrf,
    }


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    logger.info(
        "Logout: user=%r ip=%s",
        context.username,
        _remote_host(request),
    )
    if context.auth_type == "session":
        revoke_session_token(_current_token(request))
    response.delete_cookie(
        key=session_cookie_name(),
        path=session_cookie_path(),
        domain=session_cookie_domain(),
    )
    response.delete_cookie(
        key=csrf_cookie_name(),
        path=session_cookie_path(),
        domain=session_cookie_domain(),
    )
    return {"status": "ok"}


def _collect_security_warnings(context: AuthContext) -> list[str]:
    """Return security warnings for the admin UI. Only shown to admins."""
    if context.role != "admin":
        return []
    warnings: list[str] = []
    if not is_auth_required():
        warnings.append(
            "AUTH_REQUIRED is not enabled. All API requests are unauthenticated. "
            "Set AUTH_REQUIRED=true and restart the server."
        )
    if context.auth_type == "none" and context.username == "local":
        warnings.append(
            "Running without authentication. Anyone with network access has full admin privileges."
        )
    return warnings


@router.get("/auth/me")
def auth_me(request: Request, response: Response, db: DB) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    # Refresh CSRF cookie on every /me call so stale cookies (e.g. after
    # server restart with a new CSRF secret) are silently fixed.
    if context.session_via_cookie and context.session_token_hash:
        fresh_csrf = csrf_token_for_session_hash(context.session_token_hash)
        existing_csrf = request.cookies.get(csrf_cookie_name(), "")
        if existing_csrf != fresh_csrf:
            response.set_cookie(
                key=csrf_cookie_name(),
                value=fresh_csrf,
                httponly=False,
                secure=session_cookie_secure(),
                samesite=cast(SameSiteValue, session_cookie_samesite()),
                path=session_cookie_path(),
                domain=session_cookie_domain(),
            )
    try:
        server_shademap_key = get_shademap_api_key(db) or ""
    except ConfigurationError:
        server_shademap_key = ""
    shademap_available = bool(
        server_shademap_key
        or os.environ.get("SHADEMAP_PUBLIC_API_KEY", "").strip()
        or os.environ.get("SHADEMAP_PUBLIC_KEY", "").strip()
        or os.environ.get("SHADEMAP_CLIENT_KEY", "").strip()
    )
    language = "en"
    if context.user_id is not None:
        row = db.execute(
            "SELECT language FROM auth_users WHERE id = %s",
            (context.user_id,),
        ).fetchone()
        if row and row["language"]:
            language = "no" if str(row["language"]).strip().lower() == "no" else "en"
    mfa = _current_user_mfa_settings(
        db,
        user_id=context.user_id,
        role=context.role,
    )
    garden_visible = False
    if context.garden_id is not None:
        visible_row = db.execute(
            "SELECT 1 FROM gardens WHERE id = %s AND slug <> 'default' LIMIT 1",
            (context.garden_id,),
        ).fetchone()
        garden_visible = visible_row is not None
    password_change_required = bool(context.must_change_password)
    passkeys_enabled = passkeys.passkeys_configured()
    passkey_prompt_dismissed_until_ms = int(context.passkey_prompt_dismissed_until_ms or 0)
    passkey_prompt_eligible = (
        context.auth_type == "session"
        and context.user_id is not None
        and passkeys_enabled
        and not password_change_required
        and not context.password_auth_disabled
        and not context.passkey_enrolled
        and passkey_prompt_dismissed_until_ms <= current_timestamp_ms()
    )
    return {
        "authenticated": True,
        "username": context.username,
        "role": context.role,
        "garden_id": context.garden_id,
        "garden_visible": garden_visible,
        "garden_role": context.garden_role,
        "auth_type": context.auth_type,
        "write_access": has_write_access(context),
        "language": language,
        "shademap_available": shademap_available,
        "mfa_enabled": bool(mfa["enabled"]),
        "mfa_setup_required": bool(context.mfa_setup_required),
        "mfa_authenticated": int(context.mfa_authenticated_at_ms or 0) > 0,
        "mfa_methods": list(cast(list[str], mfa["methods"])),
        "must_change_password": password_change_required,
        "passkeys_enabled": passkeys_enabled,
        "passkey_enrolled": bool(context.passkey_enrolled),
        "passkey_count": int(context.passkey_count),
        "password_auth_disabled": bool(context.password_auth_disabled),
        "passkey_prompt_eligible": passkey_prompt_eligible,
        "passkey_prompt_dismissed_until_ms": passkey_prompt_dismissed_until_ms,
        "plot_assignment_meanings": (
            []
            if password_change_required
            else _list_plot_assignment_meanings(
                db,
                context.user_id,
            )
        ),
        "subscription_tier": context.subscription_tier,
        "allowed_features": (
            [] if password_change_required else sorted(features_for_tier(context.subscription_tier))
        ),
        "security_warnings": _collect_security_warnings(context),
    }


class PlotAssignmentMeaningBody(StrictBaseModel):
    pattern: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)


_PLOT_ASSIGNMENT_PATTERN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]*$")


class MeSettingsBody(StrictBaseModel):
    plot_assignment_meanings: list[PlotAssignmentMeaningBody] | None = None
    language: Literal["en", "no"] | None = None


def _normalize_plot_assignment_pattern(raw: str) -> str:
    pattern = raw.strip().upper()
    if not pattern:
        raise ValueError("Plot assignment pattern is required")
    wildcard = pattern.endswith("*")
    base = pattern[:-1] if wildcard else pattern
    if not base:
        raise ValueError("Plot assignment pattern must include a prefix before *")
    if "*" in base:
        raise ValueError("Only a trailing * wildcard is supported")
    if not _PLOT_ASSIGNMENT_PATTERN_RE.fullmatch(base):
        raise ValueError(
            "Plot assignment patterns may only use letters, digits, underscore, and hyphen",
        )
    return f"{base}*" if wildcard else base


def _list_plot_assignment_meanings(
    db: DbConn,
    user_id: int | None,
) -> list[dict[str, str]]:
    if user_id is None:
        return []
    rows = db.execute(
        """
        SELECT pattern, label, description
        FROM auth_user_plot_assignment_meanings
        WHERE user_id = %s
        ORDER BY LENGTH(pattern) DESC, pattern
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "pattern": str(row["pattern"]),
            "label": str(row["label"]),
            "description": str(row["description"] or ""),
        }
        for row in rows
    ]


def _replace_plot_assignment_meanings(
    db: DbConn,
    *,
    user_id: int,
    meanings: list[PlotAssignmentMeaningBody],
) -> None:
    normalized: list[tuple[str, str, str]] = []
    seen_patterns: set[str] = set()
    for item in meanings:
        pattern = _normalize_plot_assignment_pattern(item.pattern)
        if pattern in seen_patterns:
            raise ValueError(f"Duplicate plot assignment pattern: {pattern}")
        seen_patterns.add(pattern)
        label = item.label.strip()
        if not label:
            raise ValueError(f"Plot assignment label is required for {pattern}")
        normalized.append((pattern, label, item.description.strip()))
    db.execute(
        "DELETE FROM auth_user_plot_assignment_meanings WHERE user_id = %s",
        (user_id,),
    )
    if normalized:
        executemany(
            db,
            """
            INSERT INTO auth_user_plot_assignment_meanings (
                user_id, pattern, label, description, updated_at
            ) VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            [(user_id, pattern, label, description) for pattern, label, description in normalized],
        )


@router.get("/auth/me/settings")
def auth_me_settings(request: Request, db: DB) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    language = "en"
    if context.user_id is not None:
        row = db.execute(
            "SELECT language FROM auth_users WHERE id = %s",
            (context.user_id,),
        ).fetchone()
        if row and row["language"]:
            language = "no" if str(row["language"]).strip().lower() == "no" else "en"
    return {
        "language": language,
        "mfa": _current_user_mfa_settings(
            db,
            user_id=context.user_id,
            role=context.role,
        ),
        "plot_assignment_meanings": _list_plot_assignment_meanings(
            db,
            context.user_id,
        ),
    }


@router.put("/auth/me/settings")
def auth_me_update_settings(
    body: MeSettingsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    field_set = set(body.model_fields_set)
    try:
        if body.plot_assignment_meanings is not None:
            if context.user_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="Per-user plot assignment meanings require a signed-in user session",
                )
            _replace_plot_assignment_meanings(
                db,
                user_id=int(context.user_id),
                meanings=body.plot_assignment_meanings,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "language" in field_set and context.user_id is not None and body.language is not None:
        db.execute(
            "UPDATE auth_users SET language = %s WHERE id = %s",
            (body.language, context.user_id),
        )
    db.commit()
    return {"status": "ok"}


@router.get("/auth/mfa")
def auth_mfa_status(request: Request, db: DB) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    if context.user_id is None:
        raise HTTPException(status_code=400, detail="Session auth user is required")
    return _current_user_mfa_settings(
        db,
        user_id=context.user_id,
        role=context.role,
    )


@router.post("/auth/mfa/totp/start")
def auth_mfa_totp_start(request: Request, db: DB) -> dict[str, object]:
    context = _require_recent_session_context(request, allow_mfa_setup=True)
    if context.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="MFA enrollment is available for platform admins only",
        )
    try:
        enrollment = start_totp_enrollment(
            db,
            user_id=context.user_id,
            username=context.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.mfa.totp.start",
            user_id=context.user_id,
            expires_at_ms=_coerce_int(enrollment["expires_at_ms"]),
        ),
        db=db,
    )
    return {
        "status": "ok",
        "secret": enrollment["secret"],
        "provisioning_uri": enrollment["provisioning_uri"],
        "expires_at_ms": enrollment["expires_at_ms"],
    }


@router.post("/auth/mfa/totp/confirm")
def auth_mfa_totp_confirm(
    body: ConfirmTotpEnrollmentBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_recent_session_context(request, allow_mfa_setup=True)
    if not context.session_token_hash:
        raise HTTPException(status_code=400, detail="Session auth user is required")
    if context.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="MFA enrollment is available for platform admins only",
        )
    try:
        recovery_codes = confirm_totp_enrollment(
            db,
            user_id=context.user_id,
            code=body.code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now_ms = current_timestamp_ms()
    _revoke_sessions_for_user(
        db,
        user_id=context.user_id,
        except_token_hash=context.session_token_hash,
    )
    db.execute(
        """
        UPDATE auth_sessions
        SET
            mfa_authenticated_at_ms = %s,
            mfa_setup_required = 0,
            reauthenticated_at_ms = %s
        WHERE token_hash = %s
        """,
        (now_ms, now_ms, context.session_token_hash),
    )
    request.state.auth_context = replace(
        context,
        mfa_enabled=True,
        mfa_authenticated_at_ms=now_ms,
        mfa_setup_required=False,
        reauthenticated_at_ms=now_ms,
    )
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.mfa.totp.confirm",
            user_id=context.user_id,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "recovery_codes": recovery_codes,
        "mfa": _current_user_mfa_settings(
            db,
            user_id=context.user_id,
            role=context.role,
        ),
    }


@router.post("/auth/mfa/disable")
def auth_mfa_disable(
    body: MfaActionBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    if context.user_id is None:
        raise HTTPException(status_code=400, detail="Session auth user is required")
    disable_totp_mfa(db, user_id=context.user_id)
    _revoke_sessions_for_user(
        db,
        user_id=context.user_id,
        except_token_hash=context.session_token_hash,
    )
    if context.session_token_hash:
        db.execute(
            """
            UPDATE auth_sessions
            SET
                mfa_authenticated_at_ms = 0,
                mfa_setup_required = %s,
                reauthenticated_at_ms = %s
            WHERE token_hash = %s
            """,
            (
                1 if admin_mfa_required() else 0,
                current_timestamp_ms(),
                context.session_token_hash,
            ),
        )
    request.state.auth_context = replace(
        context,
        mfa_enabled=False,
        mfa_authenticated_at_ms=0,
        mfa_setup_required=admin_mfa_required(),
    )
    _record_destructive_admin_action("disable_mfa")
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.mfa.disable",
            user_id=context.user_id,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "mfa": _current_user_mfa_settings(
            db,
            user_id=context.user_id,
            role=context.role,
        ),
    }


@router.post("/auth/mfa/totp/cancel")
def auth_mfa_totp_cancel(
    body: MfaActionBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_recent_session_context(request, allow_mfa_setup=True)
    if context.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="MFA enrollment is available for platform admins only",
        )
    if not cancel_totp_enrollment(db, user_id=int(context.user_id)):
        raise HTTPException(status_code=404, detail="No pending MFA enrollment")
    action_reason = _normalize_action_reason(request, body_reason=body.action_reason)
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.mfa.totp.cancel",
            user_id=context.user_id,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "mfa": _current_user_mfa_settings(
            db,
            user_id=int(context.user_id),
            role=context.role,
        ),
    }


@router.post("/auth/mfa/recovery-codes/regenerate")
def auth_mfa_regenerate_recovery_codes(
    body: MfaActionBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    if context.user_id is None:
        raise HTTPException(status_code=400, detail="Session auth user is required")
    try:
        recovery_codes = regenerate_recovery_codes(db, user_id=context.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.mfa.recovery-codes.regenerate",
            user_id=context.user_id,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "recovery_codes": recovery_codes,
        "mfa": _current_user_mfa_settings(
            db,
            user_id=context.user_id,
            role=context.role,
        ),
    }


@router.get("/auth/users")
def auth_list_users(request: Request, db: DB) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    _require_admin_context(request)
    rows = db.execute(
        f"""
        SELECT
            {_auth_user_select_fields("u")}
        FROM auth_users u
        ORDER BY u.username
        """,
    ).fetchall()
    return {"users": [_serialize_auth_user(row) for row in rows]}


@router.post("/auth/users", status_code=201)
def auth_create_user(
    body: AdminCreateUserBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-create",
        env_name="AUTH_USER_CREATE_RATE_LIMIT",
    )
    created = create_user(
        db,
        username=body.username,
        password=body.password,
        role=body.role,
        created_by_user_id=context.user_id,
        must_change_password=body.must_change_password,
    )
    db.commit()
    created_user_id = _coerce_int(created["id"])
    row = _load_auth_user(db, created_user_id)
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=201,
        detail=_lifecycle_detail(
            "auth.user.create",
            user_id=created_user_id,
            username=str(created["username"]),
            role=str(created["role"]),
            must_change_password=bool(body.must_change_password),
            action_reason=action_reason,
        ),
        db=db,
    )
    return _serialize_auth_user(row)


@router.get("/auth/user-invitations")
def auth_list_user_invitations(request: Request, db: DB) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    _require_admin_context(request)
    rows = db.execute(
        """
        SELECT
            id,
            invitee_username,
            role,
            created_by_user_id,
            created_at_ms,
            expires_at_ms,
            accepted_at_ms,
            accepted_user_id,
            revoked_at_ms
        FROM auth_user_invitations
        ORDER BY created_at_ms DESC, id DESC
        """,
    ).fetchall()
    now_ms = current_timestamp_ms()
    return {
        "invitations": [_serialize_user_invitation(row, now_ms=now_ms) for row in rows],
    }


@router.post("/auth/user-invitations", status_code=201)
def auth_create_user_invitation(
    body: CreateUserInvitationBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-invitation-create",
        env_name="AUTH_USER_INVITATION_CREATE_RATE_LIMIT",
    )
    invitee_username = body.invitee_username.strip()
    if not invitee_username:
        raise HTTPException(status_code=400, detail="Invitee username is required")
    now_ms = current_timestamp_ms()
    ttl_minutes = _user_invitation_ttl_minutes(body.expires_in_minutes)
    expires_at_ms = now_ms + (ttl_minutes * 60 * 1000)
    token = secrets.token_urlsafe(48)
    token_hash = sha256(token.encode("utf-8")).hexdigest()
    db.execute(
        """
        UPDATE auth_user_invitations
        SET revoked_at_ms = %s
        WHERE invitee_username = %s
          AND accepted_at_ms IS NULL
          AND revoked_at_ms IS NULL
          AND expires_at_ms > %s
        """,
        (now_ms, invitee_username, now_ms),
    )
    row = db.execute(
        """
        INSERT INTO auth_user_invitations (
            invitee_username,
            role,
            token_hash,
            created_by_user_id,
            created_at_ms,
            expires_at_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            invitee_username,
            body.role,
            token_hash,
            context.user_id,
            now_ms,
            expires_at_ms,
        ),
    ).fetchone()
    assert row is not None
    invitation_id = _coerce_int(row["id"])
    invitation = db.execute(
        """
        SELECT
            id,
            invitee_username,
            role,
            created_by_user_id,
            created_at_ms,
            expires_at_ms,
            accepted_at_ms,
            accepted_user_id,
            revoked_at_ms
        FROM auth_user_invitations
        WHERE id = %s
        LIMIT 1
        """,
        (invitation_id,),
    ).fetchone()
    if not invitation:
        raise HTTPException(status_code=500, detail="Failed to create invitation")
    db.commit()
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=201,
        detail=_lifecycle_detail(
            "auth.user-invitation.create",
            invitation_id=invitation_id,
            invitee_username=invitee_username,
            role=body.role,
            ttl_minutes=ttl_minutes,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "invite_token": token,
        "invitation": _serialize_user_invitation(invitation, now_ms=now_ms),
    }


@router.delete("/auth/user-invitations/{invitation_id}")
def auth_revoke_user_invitation(
    invitation_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(request)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-invitation-revoke",
        env_name="AUTH_USER_INVITATION_REVOKE_RATE_LIMIT",
    )
    invitation = db.execute(
        """
        SELECT
            invitee_username,
            role,
            accepted_at_ms,
            revoked_at_ms
        FROM auth_user_invitations
        WHERE id = %s
        LIMIT 1
        """,
        (invitation_id,),
    ).fetchone()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if invitation["accepted_at_ms"] is not None:
        raise HTTPException(status_code=409, detail="Invitation already accepted")
    revoked_at_ms = invitation["revoked_at_ms"]
    if revoked_at_ms is None:
        revoked_at_ms = current_timestamp_ms()
        db.execute(
            "UPDATE auth_user_invitations SET revoked_at_ms = %s WHERE id = %s",
            (revoked_at_ms, invitation_id),
        )
        db.commit()
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user-invitation.revoke",
            invitation_id=invitation_id,
            invitee_username=str(invitation["invitee_username"]),
            role=str(invitation["role"]),
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "invitation_id": invitation_id,
        "revoked_at_ms": int(revoked_at_ms),
    }


@router.patch("/auth/users/{user_id}")
def auth_update_user(
    user_id: int,
    body: AdminUpdateUserBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-update",
        env_name="AUTH_USER_UPDATE_RATE_LIMIT",
    )
    if body.role is None and body.is_active is None and body.must_change_password is None:
        raise HTTPException(status_code=400, detail="No mutable fields were provided")

    current = _load_auth_user(db, user_id)
    current_role = str(current["role"])
    current_active = bool(int(current["is_active"]))
    current_must_change = bool(int(current["must_change_password"]))

    next_role = body.role if body.role is not None else current_role
    next_active = body.is_active if body.is_active is not None else current_active
    next_must_change = (
        body.must_change_password if body.must_change_password is not None else current_must_change
    )
    if body.must_change_password is True:
        password_state = db.execute(
            """
            SELECT password_hash, password_auth_disabled
            FROM auth_users
            WHERE id = %s
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if password_state and (
            int(password_state["password_auth_disabled"]) == 1
            or password_state["password_hash"] is None
        ):
            raise HTTPException(
                status_code=400,
                detail="Password change requirement is unavailable for passwordless accounts",
            )

    if current_role == "admin" and current_active and (next_role != "admin" or not next_active):
        if _active_admin_count(db, exclude_user_id=user_id) <= 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove or deactivate the last active admin user",
            )

    next_deactivated_at = current["deactivated_at"]
    next_deactivated_reason = current["deactivated_reason"]
    if body.is_active is not None:
        if body.is_active:
            next_deactivated_at = None
            next_deactivated_reason = None
        else:
            next_deactivated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            next_deactivated_reason = body.deactivated_reason.strip() or "deactivated-by-admin"

    revoked_sessions_on_deactivate = 0
    db.execute(
        """
        UPDATE auth_users
        SET
            role = %s,
            is_active = %s,
            must_change_password = %s,
            deactivated_at = %s,
            deactivated_reason = %s
        WHERE id = %s
        """,
        (
            next_role,
            int(bool(next_active)),
            int(bool(next_must_change)),
            next_deactivated_at,
            next_deactivated_reason,
            user_id,
        ),
    )
    if current_active and not bool(next_active):
        revoked_sessions_on_deactivate = _revoke_sessions_for_user(
            db,
            user_id=user_id,
        )
    elif bool(next_active) and current_role != next_role and next_role == "admin":
        # Promote to pro tier so the new admin can access all features.
        db.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (user_id,),
        )
        mfa_status = _current_user_mfa_settings(db, user_id=user_id, role=next_role)
        revoked_sessions_on_deactivate = _revoke_or_mark_admin_promotion_sessions(
            db,
            user_id=user_id,
            mfa_setup_required=bool(mfa_status["setup_required"]),
        )
    elif bool(next_active) and (not current_must_change) and bool(next_must_change):
        revoked_sessions_on_deactivate = _revoke_sessions_for_user(db, user_id=user_id)
    elif (
        bool(next_active)
        and current_role != next_role
        and AUTH_ROLES.index(next_role) < AUTH_ROLES.index(current_role)
    ):
        _revoke_sessions_for_user(db, user_id=user_id)
    db.commit()

    updated = _load_auth_user(db, user_id)
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.update",
            user_id=user_id,
            role_from=current_role,
            role_to=next_role,
            is_active_from=current_active,
            is_active_to=bool(next_active),
            must_change_password_from=current_must_change,
            must_change_password_to=bool(next_must_change),
            revoked_sessions=revoked_sessions_on_deactivate,
            action_reason=action_reason,
        ),
        db=db,
    )
    return _serialize_auth_user(updated)


@router.delete("/auth/users/{user_id}")
def auth_delete_user(
    user_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(request)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-delete",
        env_name="AUTH_USER_DELETE_RATE_LIMIT",
    )
    current = _load_auth_user(db, user_id)
    current_role = str(current["role"])
    current_active = bool(int(current["is_active"]))

    # Prevent deleting the last active admin
    if current_role == "admin" and current_active:
        if _active_admin_count(db, exclude_user_id=user_id) <= 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete the last active admin user",
            )

    # Prevent self-deletion
    if context.user_id == user_id:
        raise HTTPException(status_code=409, detail="Cannot delete your own account")

    username = str(current["username"])
    deletion_impact = load_user_deletion_impact(db, user_id)
    revoked_sessions = _revoke_sessions_for_user(db, user_id=user_id)

    if deletion_impact.hard_delete_blocked:
        deactivated_at = current["deactivated_at"]
        deactivated_reason = current["deactivated_reason"]
        if current_active:
            deactivated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            deactivated_reason = f"delete-request: {action_reason}"[:400]
            db.execute(
                """
                UPDATE auth_users
                SET is_active = 0,
                    deactivated_at = %s,
                    deactivated_reason = %s
                WHERE id = %s
                """,
                (deactivated_at, deactivated_reason, user_id),
            )
        db.commit()
        _audit_user_lifecycle_event(
            request,
            auth_context=context,
            status_code=200,
            detail=_lifecycle_detail(
                "auth.user.delete-converted-to-deactivate",
                user_id=user_id,
                username=username,
                role=current_role,
                revoked_sessions=revoked_sessions,
                action_reason=action_reason,
                **deletion_impact.response_fields(),
            ),
            db=db,
        )
        return {
            "status": "ok",
            "operation": "deactivated",
            "hard_delete": False,
            "user_id": user_id,
            "username": username,
            "is_active": False,
            "deactivated_at": deactivated_at,
            "deactivated_reason": deactivated_reason,
            "revoked_sessions": revoked_sessions,
            **deletion_impact.response_fields(),
        }

    db.execute("DELETE FROM garden_memberships WHERE user_id = %s", (user_id,))
    db.execute("DELETE FROM auth_users WHERE id = %s", (user_id,))
    db.commit()
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.hard-delete",
            user_id=user_id,
            username=username,
            role=current_role,
            revoked_sessions=revoked_sessions,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "operation": "hard_deleted",
        "hard_delete": True,
        "user_id": user_id,
        "username": username,
        "revoked_sessions": revoked_sessions,
    }


@router.post("/auth/users/{user_id}/revoke-sessions")
def auth_revoke_user_sessions_by_id(
    user_id: int,
    body: RevokeUserSessionsByIdBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-revoke-sessions",
        env_name="AUTH_USER_REVOKE_SESSIONS_RATE_LIMIT",
    )
    user_row = _load_auth_user(db, user_id)
    revoked = _revoke_sessions_for_user(db, user_id=user_id)
    db.commit()
    _record_destructive_admin_action("revoke_user_sessions_by_id")
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.revoke-sessions",
            user_id=user_id,
            username=str(user_row["username"]),
            revoked_sessions=revoked,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "user_id": user_id,
        "username": str(user_row["username"]),
        "revoked_sessions": revoked,
    }


class UpdateTierBody(StrictBaseModel):
    subscription_tier: str
    action_reason: str = Field(default="", max_length=400)

    @field_validator("subscription_tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        if v not in TIER_ORDER:
            msg = f"Invalid tier. Must be one of: {', '.join(TIER_ORDER)}"
            raise ValueError(msg)
        return v


@router.put("/auth/users/{user_id}/tier")
def update_user_tier(
    user_id: int,
    body: UpdateTierBody,
    request: Request,
    db: DB,
) -> dict:
    """Update a user's subscription tier. Admin only."""
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    row = db.execute("SELECT id FROM auth_users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    db.execute(
        "UPDATE auth_users SET subscription_tier = %s WHERE id = %s",
        (body.subscription_tier, user_id),
    )
    db.commit()
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.update-tier",
            user_id=user_id,
            subscription_tier=body.subscription_tier,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {"status": "ok", "subscription_tier": body.subscription_tier}


@router.post("/auth/users/{user_id}/restart-onboarding")
def auth_restart_user_onboarding(
    user_id: int,
    body: RestartUserOnboardingBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-restart-onboarding",
        env_name="AUTH_USER_UPDATE_RATE_LIMIT",
    )
    user_row = _load_auth_user(db, user_id)
    managed_garden_id = user_row["managed_garden_id"]
    if managed_garden_id is None:
        raise HTTPException(
            status_code=409,
            detail="User has no managed garden available for onboarding reset",
        )
    garden_id = int(managed_garden_id)
    garden_name = str(user_row["managed_garden_name"] or f"Garden {garden_id}")
    db.execute(
        "UPDATE gardens SET onboarding_complete = 0 WHERE id = %s",
        (garden_id,),
    )
    db.commit()
    _record_destructive_admin_action("restart_user_onboarding")
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.restart-onboarding",
            user_id=user_id,
            username=str(user_row["username"]),
            garden_id=garden_id,
            garden_name=garden_name,
            action_reason=action_reason,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {
        "status": "ok",
        "user_id": user_id,
        "username": str(user_row["username"]),
        "garden_id": garden_id,
        "garden_name": garden_name,
        "onboarding_complete": False,
    }


@router.post("/auth/reauthenticate")
def auth_reauthenticate(
    body: ReauthenticateBody,
    request: Request,
    response: Response,
    db: DB,
) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-reauthenticate",
        env_name="AUTH_REAUTH_RATE_LIMIT",
        default_limit=10,
    )
    if context.auth_type != "session" or context.user_id is None or not context.session_token_hash:
        raise HTTPException(status_code=400, detail="Session auth user is required")

    user_row = db.execute(
        """
        SELECT
            id,
            username,
            password_hash,
            password_auth_disabled,
            is_active,
            role,
            mfa_totp_enabled
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (context.user_id,),
    ).fetchone()
    if not user_row or int(user_row["is_active"]) != 1:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if int(user_row["password_auth_disabled"]) == 1 or user_row["password_hash"] is None:
        raise HTTPException(status_code=403, detail="Password authentication is unavailable")
    if not verify_password_and_upgrade(
        db,
        user_id=int(user_row["id"]),
        password=body.current_password,
        password_hash=str(user_row["password_hash"]),
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    requires_second_factor = _admin_mfa_enforced_for_role(
        str(user_row["role"]),
        mfa_enabled=bool(int(user_row["mfa_totp_enabled"])),
    )
    if requires_second_factor and not bool(int(user_row["mfa_totp_enabled"])):
        raise HTTPException(
            status_code=403,
            detail="Platform-admin MFA must be enabled before destructive actions",
        )
    second_factor_method = ""
    if bool(int(user_row["mfa_totp_enabled"])):
        second_factor_ok, second_factor_method = verify_user_second_factor(
            db,
            user_id=int(user_row["id"]),
            mfa_code=body.mfa_code,
            recovery_code=body.recovery_code,
        )
        if not second_factor_ok:
            record_security_event("auth_mfa_failures")
            raise HTTPException(
                status_code=401,
                detail="Current multi-factor authentication code is incorrect",
            )

    # Rotate session token on reauthentication to prevent stolen-session elevation.
    # Delete old session and commit first, then create new session (which uses its
    # own DB connection internally).
    db.execute(
        "DELETE FROM auth_sessions WHERE token_hash = %s",
        (context.session_token_hash,),
    )
    db.commit()
    new_token, new_expires_at_ms = create_session_for_user(
        int(user_row["id"]),
        mfa_authenticated=bool(second_factor_method),
        mfa_setup_required=False,
        device_label=_session_device_label(request),
        location_hint=_session_location_hint(request),
    )
    now_ms = current_timestamp_ms()
    new_token_hash = sha256(new_token.encode("utf-8")).hexdigest()
    db.execute(
        """
        UPDATE auth_sessions
        SET reauthenticated_at_ms = %s
        WHERE token_hash = %s
        """,
        (now_ms, new_token_hash),
    )
    db.commit()
    # Set new session + CSRF cookies
    ttl_seconds = max(1, int((new_expires_at_ms - now_ms) / 1000))
    cookie_kwargs: SessionCookieKwargs = {
        "max_age": ttl_seconds,
        "secure": session_cookie_secure(),
        "samesite": cast(SameSiteValue, session_cookie_samesite()),
        "path": session_cookie_path(),
        "domain": session_cookie_domain(),
    }
    response.set_cookie(
        key=session_cookie_name(),
        value=new_token,
        httponly=True,
        **cookie_kwargs,
    )
    response.set_cookie(
        key=csrf_cookie_name(),
        value=csrf_token_for_session_token(new_token),
        httponly=False,
        **cookie_kwargs,
    )
    request.state.auth_context = replace(
        context,
        session_token_hash=new_token_hash,
        reauthenticated_at_ms=now_ms,
        mfa_authenticated_at_ms=(
            now_ms if second_factor_method else context.mfa_authenticated_at_ms
        ),
    )
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.session.reauthenticate",
            user_id=int(user_row["id"]),
            mfa_method=second_factor_method or None,
        ),
        db=db,
    )
    new_csrf = csrf_token_for_session_token(new_token)
    return {
        "status": "ok",
        "reauthenticated_at_ms": now_ms,
        "reauthenticated_until_ms": now_ms + _admin_step_up_window_ms(),
        "csrf_token": new_csrf,
    }


@router.post("/auth/change-password")
def auth_change_password(
    body: ChangePasswordBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context = resolve_request_auth_context(request)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-change-password",
        env_name="AUTH_CHANGE_PASSWORD_RATE_LIMIT",
    )
    if context.user_id is None:
        raise HTTPException(status_code=400, detail="Session auth user is required")

    user_row = db.execute(
        """
        SELECT id, username, password_hash, password_auth_disabled, is_active
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (context.user_id,),
    ).fetchone()
    if not user_row or int(user_row["is_active"]) != 1:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if int(user_row["password_auth_disabled"]) == 1 or user_row["password_hash"] is None:
        raise HTTPException(status_code=403, detail="Password authentication is unavailable")

    current_hash = str(user_row["password_hash"])
    if not verify_password(body.current_password, current_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if verify_password(body.new_password, current_hash):
        raise HTTPException(
            status_code=400,
            detail="New password must differ from current password",
        )

    validate_password_policy(body.new_password, username=str(user_row["username"]))
    now_ms = current_timestamp_ms()
    db.execute(
        """
        UPDATE auth_users
        SET password_hash = %s, password_auth_disabled = 0, must_change_password = 0
        WHERE id = %s
        """,
        (hash_password(body.new_password), int(user_row["id"])),
    )
    revoked = _revoke_sessions_for_user(
        db,
        user_id=int(user_row["id"]),
        except_token_hash=context.session_token_hash,
    )
    if context.session_token_hash:
        db.execute(
            """
            UPDATE auth_sessions
            SET reauthenticated_at_ms = %s
            WHERE token_hash = %s
            """,
            (now_ms, context.session_token_hash),
        )
    db.commit()
    request.state.auth_context = replace(
        context,
        must_change_password=False,
        reauthenticated_at_ms=now_ms,
    )
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.change-password",
            user_id=int(user_row["id"]),
            revoked_sessions=revoked,
        ),
        db=db,
    )
    return {"status": "ok", "revoked_sessions": revoked}


@router.post("/auth/users/{user_id}/issue-reset")
def auth_issue_reset_token(
    user_id: int,
    body: IssueResetTokenBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _enforce_lifecycle_rate_limit(
        request,
        bucket="auth-user-issue-reset",
        env_name="AUTH_USER_ISSUE_RESET_RATE_LIMIT",
    )
    _load_auth_user(db, user_id)
    password_state = db.execute(
        """
        SELECT password_hash, password_auth_disabled
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    target_passwordless = bool(
        password_state
        and (
            int(password_state["password_auth_disabled"]) == 1
            or password_state["password_hash"] is None
        ),
    )
    if body.purpose == "passwordless_recovery" and not target_passwordless:
        raise HTTPException(
            status_code=400,
            detail="Passwordless recovery is only available for passwordless accounts",
        )
    if body.must_change_password and target_passwordless:
        raise HTTPException(
            status_code=400,
            detail="Password change requirement is unavailable for passwordless accounts",
        )
    ttl_minutes = _reset_token_ttl_minutes(body.expires_in_minutes)
    now_ms = current_timestamp_ms()
    expires_at_ms = now_ms + (ttl_minutes * 60 * 1000)

    token = secrets.token_urlsafe(48)
    token_hash = sha256(token.encode("utf-8")).hexdigest()
    db.execute(
        """
        DELETE FROM auth_password_reset_tokens
        WHERE user_id = %s AND used_at_ms IS NULL
        """,
        (user_id,),
    )
    db.execute(
        """
        INSERT INTO auth_password_reset_tokens (
            token_hash,
            user_id,
            created_by_user_id,
            created_at_ms,
            expires_at_ms,
            purpose,
            metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            token_hash,
            user_id,
            context.user_id,
            now_ms,
            expires_at_ms,
            body.purpose,
            f"issued-by-admin reason={action_reason}",
        ),
    )
    revoked_sessions = 0
    if body.must_change_password:
        db.execute(
            "UPDATE auth_users SET must_change_password = 1 WHERE id = %s",
            (user_id,),
        )
        revoked_sessions = _revoke_sessions_for_user(db, user_id=user_id)
    db.commit()
    _audit_user_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.user.issue-reset",
            user_id=user_id,
            ttl_minutes=ttl_minutes,
            must_change_password=bool(body.must_change_password),
            purpose=body.purpose,
            revoked_sessions=revoked_sessions,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "status": "ok",
        "user_id": user_id,
        "reset_token": token,
        "expires_at_ms": expires_at_ms,
        "must_change_password": bool(body.must_change_password),
        "purpose": body.purpose,
        "revoked_sessions": revoked_sessions,
    }


@router.post("/auth/reset-password")
def auth_reset_password(
    body: ResetPasswordBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    enforce_rate_limit(
        request,
        bucket="auth-reset-password",
        limit=env_int("AUTH_RESET_PASSWORD_RATE_LIMIT", 20),
        window_seconds=60,
    )
    now_ms = current_timestamp_ms()
    token_hash = sha256(body.token.strip().encode("utf-8")).hexdigest()
    _enforce_optional_key_rate_limit(
        bucket="auth-reset-password-token",
        key=_hashed_rate_limit_key("reset", token_hash, casefold=False),
        env_name="AUTH_RESET_PASSWORD_TOKEN_RATE_LIMIT",
        default_limit=6,
        scope_label="Reset token",
    )
    _enforce_adaptive_friction(
        flow="reset-password",
        friction_provider=body.friction_provider,
        friction_token=body.friction_token,
    )
    token_row = db.execute(
        """
        SELECT id, user_id, expires_at_ms, used_at_ms, purpose
        FROM auth_password_reset_tokens
        WHERE token_hash = %s
        LIMIT 1
        FOR UPDATE
        """,
        (token_hash,),
    ).fetchone()
    if (
        not token_row
        or token_row["used_at_ms"] is not None
        or int(token_row["expires_at_ms"]) <= now_ms
    ):
        _record_invalid_reset_password_attempt()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    _enforce_optional_key_rate_limit(
        bucket="auth-reset-password-user",
        key=f"user:{int(token_row['user_id'])}",
        env_name="AUTH_RESET_PASSWORD_USER_RATE_LIMIT",
        default_limit=6,
        scope_label="User",
    )

    user_row = db.execute(
        """
        SELECT id, username, password_hash, password_auth_disabled
        FROM auth_users
        WHERE id = %s
        LIMIT 1
        """,
        (int(token_row["user_id"]),),
    ).fetchone()
    if not user_row:
        _record_invalid_reset_password_attempt()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    purpose = str(token_row["purpose"] or "password_reset")
    password_auth_disabled = int(user_row["password_auth_disabled"]) == 1
    if password_auth_disabled and purpose != "passwordless_recovery":
        raise HTTPException(
            status_code=400,
            detail="Password reset is unavailable for this account",
        )
    if purpose == "passwordless_recovery" and not password_auth_disabled:
        raise HTTPException(
            status_code=400,
            detail="Passwordless recovery is only available for passwordless accounts",
        )
    current_hash = user_row["password_hash"]
    if current_hash is not None and verify_password(body.new_password, str(current_hash)):
        raise HTTPException(
            status_code=400,
            detail="New password must differ from current password",
        )

    validate_password_policy(body.new_password, username=str(user_row["username"]))
    db.execute(
        """
        UPDATE auth_users
        SET password_hash = %s, password_auth_disabled = 0, must_change_password = 0
        WHERE id = %s
        """,
        (hash_password(body.new_password), int(user_row["id"])),
    )
    revoked_passkeys = 0
    if purpose == "passwordless_recovery":
        revoked_passkeys = len(
            db.execute(
                """
                DELETE FROM auth_passkeys
                WHERE user_id = %s
                RETURNING id
                """,
                (int(user_row["id"]),),
            ).fetchall(),
        )
    consumed = db.execute(
        """
        UPDATE auth_password_reset_tokens
        SET used_at_ms = %s, used_by_user_id = %s
        WHERE id = %s AND used_at_ms IS NULL
        RETURNING id
        """,
        (now_ms, int(user_row["id"]), int(token_row["id"])),
    ).fetchone()
    if not consumed:
        _record_invalid_reset_password_attempt()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    revoked = _revoke_sessions_for_user(db, user_id=int(user_row["id"]))
    db.commit()
    _audit_user_lifecycle_event(
        request,
        auth_context=None,
        status_code=200,
        detail=_lifecycle_detail(
            (
                "auth.user.passwordless-recovery-reset"
                if purpose == "passwordless_recovery"
                else "auth.user.reset-password"
            ),
            user_id=int(user_row["id"]),
            revoked_sessions=revoked,
            revoked_passkeys=revoked_passkeys,
        ),
        db=db,
    )
    return {"status": "ok", "revoked_sessions": revoked, "revoked_passkeys": revoked_passkeys}


class InvitationPeekBody(StrictBaseModel):
    token: str = Field(min_length=1, max_length=512)


@router.post("/auth/invitations/peek")
def auth_invitation_peek(
    body: InvitationPeekBody,
    request: Request,
    db: DB,
) -> dict[str, str]:
    _require_user_lifecycle_enabled()
    enforce_rate_limit(
        request,
        bucket="auth-invite-peek",
        limit=env_int("AUTH_INVITE_PEEK_RATE_LIMIT", 20),
        window_seconds=60,
    )
    now_ms = current_timestamp_ms()
    token_hash = sha256(body.token.strip().encode("utf-8")).hexdigest()

    for table in ("garden_invitations", "auth_user_invitations"):
        row = db.execute(
            f"SELECT invitee_username, expires_at_ms, accepted_at_ms, revoked_at_ms "  # noqa: S608
            f"FROM {table} WHERE token_hash = %s LIMIT 1",
            (token_hash,),
        ).fetchone()
        if (
            row
            and row["accepted_at_ms"] is None
            and row["revoked_at_ms"] is None
            and int(row["expires_at_ms"]) > now_ms
        ):
            username = str(row["invitee_username"]).strip()
            if username:
                return {"username": username}

    raise HTTPException(status_code=400, detail="Invalid or expired invitation token")


@router.post("/auth/invitations/passkey/register/options")
def auth_invitation_passkey_register_options(
    body: InvitationPasskeyRegisterOptionsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    _require_invitation_passkey_session_mode()
    passkeys.require_passkeys_configured()
    enforce_rate_limit(
        request,
        bucket="auth-invite-passkey-register",
        limit=env_int("AUTH_INVITE_PASSKEY_REGISTER_RATE_LIMIT", 20),
        window_seconds=60,
    )
    now_ms = current_timestamp_ms()
    token_hash = sha256(body.token.strip().encode("utf-8")).hexdigest()
    _enforce_optional_key_rate_limit(
        bucket="auth-invite-passkey-register-token",
        key=_hashed_rate_limit_key("invite", token_hash, casefold=False),
        env_name="AUTH_INVITE_PASSKEY_REGISTER_TOKEN_RATE_LIMIT",
        default_limit=6,
        scope_label="Invitation token",
    )
    _enforce_adaptive_friction(
        flow="invitation-passkey-register",
        friction_provider=body.friction_provider,
        friction_token=body.friction_token,
    )
    loaded = _load_active_invitation_by_token(db, token_hash=token_hash, now_ms=now_ms)
    if loaded is None:
        _raise_invalid_invitation_token()
    invitation_scope, invitation = loaded
    invitee_username = _validate_passwordless_invitation_candidate(
        db,
        invitation_scope=invitation_scope,
        invitation=invitation,
        username=body.username,
    )
    _enforce_optional_key_rate_limit(
        bucket="auth-invite-passkey-register-invitee",
        key=_hashed_rate_limit_key("invitee", invitee_username),
        env_name="AUTH_INVITE_PASSKEY_REGISTER_INVITEE_RATE_LIMIT",
        default_limit=6,
        scope_label="Invitee",
    )
    user_handle = generate_passkey_user_handle()
    challenge = passkeys.create_challenge(
        db,
        flow="registration",
        invitation_token_hash=token_hash,
        invitation_scope=invitation_scope,
        invitation_id=int(invitation["id"]),
        invitee_username=invitee_username,
        invitation_user_handle=user_handle,
    )
    public_key = passkeys.registration_options_for_user(
        db,
        user_id=0,
        username=invitee_username,
        challenge=challenge.challenge,
        user_handle=user_handle,
    )
    db.commit()
    return {"challenge_token": challenge.token, "publicKey": public_key}


@router.post("/auth/invitations/passkey/register/verify", status_code=201)
def auth_invitation_passkey_register_verify(
    body: InvitationPasskeyRegisterVerifyBody,
    request: Request,
    response: Response,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    _require_invitation_passkey_session_mode()
    passkeys.require_passkeys_configured()
    enforce_rate_limit(
        request,
        bucket="auth-invite-passkey-register",
        limit=env_int("AUTH_INVITE_PASSKEY_REGISTER_RATE_LIMIT", 20),
        window_seconds=60,
    )
    challenge_token_hash = sha256(body.challenge_token.strip().encode("utf-8")).hexdigest()
    _enforce_optional_key_rate_limit(
        bucket="auth-invite-passkey-register-challenge",
        key=_hashed_rate_limit_key("passkey-challenge", challenge_token_hash, casefold=False),
        env_name="AUTH_INVITE_PASSKEY_REGISTER_CHALLENGE_RATE_LIMIT",
        default_limit=6,
        scope_label="Passkey challenge",
    )
    challenge = passkeys.consume_challenge(
        db,
        token=body.challenge_token,
        flow="registration",
    )
    db.commit()
    if (
        not challenge.invitation_token_hash
        or challenge.invitation_scope not in {"garden", "personal_garden"}
        or challenge.invitation_id is None
        or not challenge.invitee_username
        or not challenge.invitation_user_handle
    ):
        _raise_invalid_invitation_token()
    invitation_scope = cast(InvitationScope, challenge.invitation_scope)
    _enforce_optional_key_rate_limit(
        bucket="auth-invite-passkey-register-invitee",
        key=_hashed_rate_limit_key("invitee", challenge.invitee_username),
        env_name="AUTH_INVITE_PASSKEY_REGISTER_VERIFY_INVITEE_RATE_LIMIT",
        default_limit=6,
        scope_label="Invitee",
    )
    now_ms = current_timestamp_ms()
    invitation = _select_active_invitation(
        db,
        scope=invitation_scope,
        token_hash=challenge.invitation_token_hash,
        now_ms=now_ms,
        invitation_id=challenge.invitation_id,
    )
    if invitation is None:
        _raise_invalid_invitation_token()
    invitee_username = _validate_passwordless_invitation_candidate(
        db,
        invitation_scope=invitation_scope,
        invitation=invitation,
        username=challenge.invitee_username,
    )
    created_role = _validated_passwordless_invitation_role(
        invitation_scope=invitation_scope,
        invitation=invitation,
    )
    try:
        verified = passkeys.verify_registration_credential(
            credential=body.credential,
            expected_challenge=challenge.challenge,
        )
    except HTTPException:
        db.commit()
        raise
    try:
        created = create_user(
            db,
            username=invitee_username,
            password=None,
            role=created_role,
            password_auth_disabled=True,
            passkey_user_handle=challenge.invitation_user_handle,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            _raise_invalid_invitation_token()
        raise
    user_id = _coerce_int(created["id"])
    credential_id = passkeys.encode_public_key(verified.credential_id)
    row = db.execute(
        """
        INSERT INTO auth_passkeys (
            user_id,
            credential_id,
            credential_public_key,
            sign_count,
            nickname,
            transports,
            credential_device_type,
            credential_backed_up,
            created_at_ms,
            updated_at_ms,
            last_used_at_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
        ON CONFLICT (credential_id) DO NOTHING
        RETURNING id, nickname, created_at_ms, last_used_at_ms, transports,
                  credential_device_type, credential_backed_up
        """,
        (
            user_id,
            credential_id,
            passkeys.encode_public_key(verified.credential_public_key),
            verified.sign_count,
            body.nickname.strip(),
            passkeys.credential_transports(body.credential),
            verified.credential_device_type,
            int(verified.credential_backed_up),
            now_ms,
            now_ms,
        ),
    ).fetchone()
    if not row:
        db.rollback()
        raise HTTPException(status_code=409, detail="Passkey is already registered")
    _accept_invitation_atomically(
        db,
        invitation_scope=invitation_scope,
        invitation=invitation,
        token_hash=challenge.invitation_token_hash,
        user_id=user_id,
        now_ms=now_ms,
        membership_role=created_role,
    )
    _commit_required_lifecycle_event(
        request,
        auth_context=None,
        status_code=201,
        detail=_lifecycle_detail(
            "auth.invitation.passkey-register",
            invitation_id=int(invitation["id"]),
            invitation_scope=invitation_scope,
            garden_id=(int(invitation["garden_id"]) if invitation_scope == "garden" else None),
            user_id=user_id,
            username=invitee_username,
            role=str(invitation["role"]),
            created_user=True,
            passkey_id=int(row["id"]),
            session_establishment_pending=True,
        ),
        garden_id=(int(invitation["garden_id"]) if invitation_scope == "garden" else None),
        db=db,
    )
    session_established = True
    session_message = ""
    expires_at_ms: int | None = None
    try:
        token, expires_at_ms = create_session_for_user(
            user_id,
            mfa_authenticated=True,
            mfa_setup_required=False,
            device_label=_session_device_label(request),
            location_hint=_session_location_hint(request),
        )
        db.execute(
            "UPDATE auth_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = %s",
            (user_id,),
        )
        db.commit()
        _set_session_cookies(response, token=token, expires_at_ms=expires_at_ms)
    except Exception:
        logger.exception("Passwordless invitation created account but session creation failed")
        db.rollback()
        session_established = False
        session_message = "Sign in to continue."
    return {
        "status": "ok",
        "expires_at_ms": expires_at_ms,
        "garden_id": (int(invitation["garden_id"]) if invitation_scope == "garden" else None),
        "user_id": user_id,
        "username": invitee_username,
        "role": str(invitation["role"]),
        "created_user": True,
        "invitation_scope": invitation_scope,
        "passkey": passkeys.serialize_passkey(dict(row)),
        "session_established": session_established,
        "message": session_message,
    }


@router.post("/auth/invitations/accept")
def auth_accept_invitation(
    body: InvitationAcceptBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    enforce_rate_limit(
        request,
        bucket="auth-invite-accept",
        limit=env_int("AUTH_INVITE_ACCEPT_RATE_LIMIT", 20),
        window_seconds=60,
    )
    now_ms = current_timestamp_ms()
    token_hash = sha256(body.token.strip().encode("utf-8")).hexdigest()
    _enforce_optional_key_rate_limit(
        bucket="auth-invite-accept-token",
        key=_hashed_rate_limit_key("invite", token_hash, casefold=False),
        env_name="AUTH_INVITE_ACCEPT_TOKEN_RATE_LIMIT",
        default_limit=6,
        scope_label="Invitation token",
    )
    _enforce_adaptive_friction(
        flow="invitation-accept",
        friction_provider=body.friction_provider,
        friction_token=body.friction_token,
    )
    loaded = _load_active_invitation_by_token(db, token_hash=token_hash, now_ms=now_ms)
    if loaded is None:
        _raise_invalid_invitation_token()
    invitation_scope, invitation = loaded

    invitee_username = str(invitation["invitee_username"]).strip()
    if not invitee_username:
        _raise_invalid_invitation_token()
    _enforce_optional_key_rate_limit(
        bucket="auth-invite-accept-invitee",
        key=_hashed_rate_limit_key("invitee", invitee_username),
        env_name="AUTH_INVITE_ACCEPT_INVITEE_RATE_LIMIT",
        default_limit=6,
        scope_label="Invitee",
    )

    user_row = db.execute(
        """
        SELECT
            id,
            username,
            password_hash,
            password_auth_disabled,
            role,
            is_active,
            mfa_totp_enabled
        FROM auth_users
        WHERE username = %s
        LIMIT 1
        """,
        (invitee_username,),
    ).fetchone()
    user_id: int
    created_user = False
    if user_row:
        if int(user_row["is_active"]) != 1:
            _record_invalid_invitation_attempt()
            raise HTTPException(status_code=401, detail="Invalid invitation credentials")
        if int(user_row["password_auth_disabled"]) == 1 or user_row["password_hash"] is None:
            _record_invalid_invitation_attempt()
            raise HTTPException(status_code=401, detail="Invalid invitation credentials")
        password_hash = str(user_row["password_hash"])
        if not verify_password(body.password, password_hash):
            _record_invalid_invitation_attempt()
            raise HTTPException(status_code=401, detail="Invalid invitation credentials")
        user_id = int(user_row["id"])
        if password_needs_rehash(password_hash):
            db.execute(
                "UPDATE auth_users SET password_hash = %s WHERE id = %s",
                (hash_password(body.password), user_id),
            )
    else:
        created_role = cast(
            Literal["viewer", "editor", "admin"],
            "viewer" if invitation_scope == "garden" else str(invitation["role"]),
        )
        created = create_user(
            db,
            username=invitee_username,
            password=body.password,
            role=created_role,
        )
        user_id = _coerce_int(created["id"])
        created_user = True

    invitation_role = str(invitation["role"])
    promotion_session_updates = 0
    if invitation_scope == "personal_garden" and user_row:
        current_role = str(user_row["role"])
        if _role_rank(invitation_role) > _role_rank(current_role):
            db.execute(
                "UPDATE auth_users SET role = %s WHERE id = %s",
                (invitation_role, user_id),
            )
            if invitation_role == "admin":
                db.execute(
                    "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                    (user_id,),
                )
                mfa_status = _current_user_mfa_settings(
                    db,
                    user_id=user_id,
                    role=invitation_role,
                )
                promotion_session_updates = _revoke_or_mark_admin_promotion_sessions(
                    db,
                    user_id=user_id,
                    mfa_setup_required=bool(mfa_status["setup_required"]),
                )
    _accept_invitation_atomically(
        db,
        invitation_scope=invitation_scope,
        invitation=invitation,
        token_hash=token_hash,
        user_id=user_id,
        now_ms=now_ms,
    )
    _commit_required_lifecycle_event(
        request,
        auth_context=None,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.invitation.accept",
            invitation_id=int(invitation["id"]),
            invitation_scope=invitation_scope,
            garden_id=(int(invitation["garden_id"]) if invitation_scope == "garden" else None),
            user_id=user_id,
            username=invitee_username,
            role=invitation_role,
            created_user=created_user,
            promotion_session_updates=promotion_session_updates,
        ),
        garden_id=(int(invitation["garden_id"]) if invitation_scope == "garden" else None),
        db=db,
    )
    return {
        "status": "ok",
        "garden_id": (int(invitation["garden_id"]) if invitation_scope == "garden" else None),
        "user_id": user_id,
        "username": invitee_username,
        "role": invitation_role,
        "created_user": created_user,
        "invitation_scope": invitation_scope,
    }


@router.get("/auth/audit-events")
def auth_audit_events(
    request: Request,
    db: DB,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    garden_id: int | None = Query(default=None, ge=1),
    actor: str = Query(default=""),
    path_prefix: str = Query(default=""),
    method: str = Query(default=""),
    status_code: int | None = Query(default=None, ge=100, le=599),
    from_ms: int | None = Query(default=None, ge=0),
    to_ms: int | None = Query(default=None, ge=0),
) -> dict[str, object]:
    _require_admin_context(request)
    return list_audit_events(
        db,
        limit=limit,
        offset=offset,
        garden_id=garden_id,
        actor=actor,
        path_prefix=path_prefix,
        method=method,
        status_code=status_code,
        from_ms=from_ms,
        to_ms=to_ms,
    )


@router.get("/auth/security-metrics")
def auth_security_metrics(request: Request) -> dict[str, object]:
    _require_admin_context(request)
    return security_metrics_snapshot()


@router.get("/auth/security-alerts")
def auth_security_alerts(request: Request) -> dict[str, object]:
    _require_admin_context(request)
    return security_alerts_snapshot()


@router.get("/auth/sessions")
def auth_sessions(request: Request, db: DB) -> dict[str, object]:
    context = resolve_request_auth_context(request)
    if context.role == "admin":
        context = _require_admin_context(request)
        user_id = None
    else:
        context = _require_session_context(request)
        user_id = int(context.user_id)
    return {
        "sessions": list_active_sessions(
            db,
            user_id=user_id,
            current_token_hash=context.session_token_hash or "",
            absolute_ttl_ms=_session_absolute_ttl_ms(),
        )
    }


@router.delete("/auth/sessions/{session_id}")
def auth_revoke_session(
    session_id: str,
    body: PasskeyActionBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _require_session_context(request)
    visible_sessions = list_active_sessions(
        db,
        user_id=None if context.role == "admin" else int(context.user_id),
        current_token_hash=context.session_token_hash or "",
        absolute_ttl_ms=_session_absolute_ttl_ms(),
    )
    target = next(
        (session for session in visible_sessions if session["session_id"] == session_id),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if bool(target["current"]):
        raise HTTPException(
            status_code=409,
            detail="Current session cannot be revoked here; use logout",
        )

    target_user_id = int(target["user_id"])
    action_reason = _normalize_action_reason(request, body_reason=body.action_reason)
    if target_user_id != int(context.user_id):
        context, action_reason = enforce_destructive_admin_controls(
            request,
            body_reason=body.action_reason,
        )
    revoked = revoke_session_by_public_id(
        db,
        session_id=session_id,
        owner_user_id=target_user_id,
    )
    if revoked is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.sessions.revoke",
            target_user_id=target_user_id,
            target_username=str(revoked["username"]),
            current_session=False,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {"status": "ok", "session_id": session_id, "current_session": False}


@router.post("/auth/revoke-user-sessions")
def auth_revoke_user_sessions(
    body: RevokeUserSessionsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    revoked = revoke_sessions_by_user(db, body.username)
    _record_destructive_admin_action("revoke_user_sessions")
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.sessions.revoke-user",
            username=body.username.strip(),
            revoked_sessions=revoked,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {"status": "ok", "revoked": revoked, "username": body.username}


@router.post("/auth/revoke-all-sessions")
def auth_revoke_all_sessions(
    body: RevokeAllSessionsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    revoked = revoke_all_sessions(db, except_token_hash=_current_token_hash(request))
    _record_destructive_admin_action("revoke_all_sessions")
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.sessions.revoke-all",
            revoked_sessions=revoked,
            action_reason=action_reason,
        ),
        db=db,
    )
    return {"status": "ok", "revoked": revoked}


@router.get("/auth/emergency-read-only")
def auth_get_emergency_read_only(request: Request, db: DB) -> dict[str, object]:
    _require_admin_context(request)
    status = get_emergency_read_only_status(db)
    return {
        "enabled": bool(status["enabled"]),
        "expires_at_ms": status["expires_at_ms"],
    }


class CheckHibpBody(StrictBaseModel):
    password: str = Field(min_length=1, max_length=1000)


@router.post("/auth/check-hibp")
def auth_check_hibp(
    body: CheckHibpBody,
    request: Request,
) -> dict[str, bool]:
    enforce_rate_limit(
        request,
        bucket="auth-check-hibp",
        limit=env_int("AUTH_CHECK_HIBP_RATE_LIMIT", 10),
        window_seconds=60,
    )
    from gardenops.security import _check_hibp

    return {"breached": _check_hibp(body.password)}


@router.patch("/auth/emergency-read-only")
def auth_set_emergency_read_only(
    body: EmergencyReadOnlyBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    expires_at_ms: int | None = None
    if body.enabled and body.expires_in_minutes is not None:
        expires_at_ms = current_timestamp_ms() + (body.expires_in_minutes * 60 * 1000)
    status = set_emergency_read_only(
        body.enabled,
        expires_at_ms=expires_at_ms,
        conn=db,
    )
    _record_destructive_admin_action("emergency_read_only")
    _commit_required_lifecycle_event(
        request,
        auth_context=context,
        status_code=200,
        detail=_lifecycle_detail(
            "auth.emergency-read-only",
            enabled=bool(status["enabled"]),
            expires_at_ms=status["expires_at_ms"],
            action_reason=action_reason,
        ),
        db=db,
    )
    return {
        "enabled": bool(status["enabled"]),
        "expires_at_ms": status["expires_at_ms"],
    }
