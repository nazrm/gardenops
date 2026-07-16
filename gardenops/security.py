import base64
import hashlib
import hmac
import logging
import os
import secrets
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from psycopg.pq import TransactionStatus

from gardenops.branding import app_user_agent
from gardenops.db import (
    DbConn,
    current_timestamp_ms,
    ensure_data_ownership,
    ensure_default_garden,
    ensure_default_garden_membership,
    get_db,
    request_scoped_db_conn,
    return_db,
)

_logger = logging.getLogger(__name__)

Role = Literal["viewer", "editor", "admin"]
AuthType = Literal["none", "session", "api_key"]
AUTH_ROLES: tuple[Role, ...] = ("viewer", "editor", "admin")

_LEGACY_PASSWORD_SCHEME = "pbkdf2_sha256"
_LEGACY_PASSWORD_ITERATIONS = 240_000
_CSRF_FALLBACK_SECRET: str | None = None
_COMMON_WEAK_PASSWORDS = {
    "password",
    "password123",
    "12345678",
    "qwerty123",
    "letmein123",
    "admin123",
}
_DEFAULT_GARDEN_SLUG = "default"
_ARGON2_HASHER: PasswordHasher | None = None
_PASSKEY_USER_HANDLE_BYTES = 32


@dataclass(frozen=True)
class AuthContext:
    user_id: int | None
    username: str
    role: Role
    auth_type: AuthType
    garden_id: int | None = None
    garden_role: Role | None = None
    session_token_hash: str | None = None
    reauthenticated_at_ms: int | None = None
    mfa_authenticated_at_ms: int | None = None
    mfa_enabled: bool = False
    passkey_enrolled: bool = False
    mfa_setup_required: bool = False
    session_via_cookie: bool = False
    subscription_tier: str = "home"
    must_change_password: bool = False
    passkey_count: int = 0
    password_auth_disabled: bool = False
    passkey_prompt_dismissed_until_ms: int = 0


def is_auth_required() -> bool:
    return os.environ.get("AUTH_REQUIRED", "false").strip().lower() == "true"


def user_lifecycle_enabled() -> bool:
    raw = os.environ.get("AUTH_USER_LIFECYCLE_ENABLED", "true").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _allow_insecure_remote() -> bool:
    return os.environ.get("ALLOW_INSECURE_REMOTE", "false").strip().lower() == "true"


def _app_env() -> str:
    return os.environ.get("APP_ENV", "").strip().lower()


def _is_internet_exposed() -> bool:
    return os.environ.get("INTERNET_EXPOSED", "false").strip().lower() == "true"


def admin_mfa_required() -> bool:
    raw = os.environ.get("AUTH_ADMIN_MFA_REQUIRED", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return _app_env() in {"prod", "production"} or _is_internet_exposed()


def _admin_mfa_enforced_for_role(role: str, *, mfa_enabled: bool) -> bool:
    return role == "admin" and (mfa_enabled or admin_mfa_required())


def auth_mode() -> str:
    configured = os.environ.get("AUTH_MODE", "").strip().lower()
    if configured in {"session", "api_key", "hybrid"}:
        return configured
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env in {"prod", "production"}:
        # Legacy shared API key is disabled by default in production.
        return "session"
    return "hybrid"


def session_auth_enabled() -> bool:
    return auth_mode() in {"session", "hybrid"}


def api_key_auth_enabled() -> bool:
    return auth_mode() in {"api_key", "hybrid"}


def is_loopback_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient", "localhost"}:
        return False
    if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        return False
    return True


def _get_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _configured_api_key() -> str:
    return os.environ.get("AUTH_API_KEY", "").strip()


def session_cookie_name() -> str:
    raw = os.environ.get("AUTH_SESSION_COOKIE_NAME", "gardenops_session").strip()
    return raw or "gardenops_session"


def csrf_cookie_name() -> str:
    return os.environ.get("AUTH_CSRF_COOKIE_NAME", "gardenops_csrf").strip() or "gardenops_csrf"


def _cookie_secure_default() -> bool:
    return _app_env() in {"prod", "production"} or _is_internet_exposed()


def session_cookie_secure() -> bool:
    raw = os.environ.get("AUTH_SESSION_COOKIE_SECURE", "").strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    return _cookie_secure_default()


def session_cookie_samesite() -> str:
    raw = os.environ.get("AUTH_SESSION_COOKIE_SAMESITE", "lax").strip().lower()
    if raw in {"lax", "strict", "none"}:
        return raw
    return "lax"


def session_cookie_domain() -> str | None:
    value = os.environ.get("AUTH_SESSION_COOKIE_DOMAIN", "").strip()
    return value or None


def session_cookie_path() -> str:
    value = os.environ.get("AUTH_SESSION_COOKIE_PATH", "/").strip()
    return value or "/"


def _csrf_secret() -> str:
    configured = os.environ.get("AUTH_CSRF_SECRET", "").strip()
    if configured:
        return configured
    global _CSRF_FALLBACK_SECRET  # noqa: PLW0603
    if _CSRF_FALLBACK_SECRET is not None:
        return _CSRF_FALLBACK_SECRET
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM security_runtime_flags WHERE key = 'csrf_secret'"
        ).fetchone()
        if row and row["value"]:
            _CSRF_FALLBACK_SECRET = str(row["value"])
        else:
            _CSRF_FALLBACK_SECRET = secrets.token_hex(32)
            conn.execute(
                "INSERT INTO security_runtime_flags (key, value) "
                "VALUES ('csrf_secret', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (_CSRF_FALLBACK_SECRET,),
            )
            conn.commit()
    finally:
        return_db(conn)
    return _CSRF_FALLBACK_SECRET


def _csrf_token_from_hash(token_hash: str) -> str:
    return hmac.new(
        _csrf_secret().encode("utf-8"),
        token_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def csrf_token_for_session_token(token: str) -> str:
    return _csrf_token_from_hash(_hash_token(token))


def csrf_token_for_session_hash(token_hash: str) -> str:
    return _csrf_token_from_hash(token_hash)


def csrf_token_matches_context(context: AuthContext, provided: str) -> bool:
    if not context.session_token_hash:
        return False
    candidate = provided.strip()
    if not candidate:
        return False
    expected = _csrf_token_from_hash(context.session_token_hash)
    return hmac.compare_digest(candidate, expected)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _password_salt() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")


def _use_fast_test_password_hashing() -> bool:
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        return False
    if _is_internet_exposed():
        return False
    raw = os.environ.get("AUTH_PASSWORD_HASH_FAST_FOR_TESTS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _argon2_hasher() -> PasswordHasher:
    global _ARGON2_HASHER  # noqa: PLW0603
    if _ARGON2_HASHER is not None:
        return _ARGON2_HASHER
    if _use_fast_test_password_hashing():
        _ARGON2_HASHER = PasswordHasher(
            time_cost=1,
            memory_cost=1024,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        return _ARGON2_HASHER
    _ARGON2_HASHER = PasswordHasher(
        time_cost=3,
        memory_cost=65_536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
    )
    return _ARGON2_HASHER


def _legacy_pbkdf2_hash_password(password: str) -> str:
    salt = _password_salt()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _LEGACY_PASSWORD_ITERATIONS,
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{_LEGACY_PASSWORD_SCHEME}${_LEGACY_PASSWORD_ITERATIONS}${salt}${encoded}"


def hash_password(password: str) -> str:
    return _argon2_hasher().hash(password)


def generate_passkey_user_handle() -> str:
    return secrets.token_urlsafe(_PASSKEY_USER_HANDLE_BYTES)


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("$argon2id$"):
        try:
            return bool(_argon2_hasher().verify(password_hash, password))
        except (InvalidHashError, VerificationError):  # fmt: skip
            return False
    parts = password_hash.split("$")
    if len(parts) != 4:
        return False
    scheme, iterations_raw, salt, expected = parts
    if scheme != _LEGACY_PASSWORD_SCHEME:
        return False
    try:
        iterations = int(iterations_raw)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(encoded, expected)


def password_needs_rehash(password_hash: str) -> bool:
    if password_hash.startswith("$argon2id$"):
        try:
            return _argon2_hasher().check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
    parts = password_hash.split("$")
    if len(parts) != 4:
        return False
    return parts[0] == _LEGACY_PASSWORD_SCHEME


def verify_password_and_upgrade(
    conn: DbConn,
    *,
    user_id: int,
    password: str,
    password_hash: str,
) -> bool:
    if not verify_password(password, password_hash):
        return False
    if password_needs_rehash(password_hash):
        conn.execute(
            "UPDATE auth_users SET password_hash = %s WHERE id = %s",
            (hash_password(password), user_id),
        )
        conn.commit()
    return True


def _session_ttl_ms() -> int:
    raw = os.environ.get("AUTH_SESSION_TTL_HOURS", "").strip()
    try:
        hours = int(raw) if raw else 12
    except ValueError:
        hours = 12
    hours = max(1, min(hours, 24 * 30))
    return hours * 60 * 60 * 1000


def _session_absolute_ttl_ms() -> int:
    raw = os.environ.get("AUTH_SESSION_ABSOLUTE_TTL_HOURS", "").strip()
    try:
        hours = int(raw) if raw else 24 * 7
    except ValueError:
        hours = 24 * 7
    hours = max(1, min(hours, 24 * 365))
    return hours * 60 * 60 * 1000


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def password_min_length() -> int:
    raw = os.environ.get("AUTH_PASSWORD_MIN_LENGTH", "").strip()
    try:
        parsed = int(raw) if raw else 30
    except ValueError:
        parsed = 30
    return max(8, min(parsed, 200))


def get_password_policy() -> dict[str, object]:
    """Return the current password policy configuration as a dict."""
    return {
        "min_length": password_min_length(),
        "require_lower": _env_bool("AUTH_PASSWORD_REQUIRE_LOWER", True),
        "require_upper": _env_bool("AUTH_PASSWORD_REQUIRE_UPPER", True),
        "require_digit": _env_bool("AUTH_PASSWORD_REQUIRE_DIGIT", True),
        "require_symbol": _env_bool("AUTH_PASSWORD_REQUIRE_SYMBOL", True),
        "reject_common": _env_bool("AUTH_PASSWORD_REJECT_COMMON", True),
        "disallow_username": _env_bool("AUTH_PASSWORD_DISALLOW_USERNAME", True),
        "check_hibp": _env_bool("AUTH_PASSWORD_CHECK_HIBP", True),
    }


def _check_hibp(password: str) -> bool:
    """Check password against Have I Been Pwned Passwords API (k-anonymity).

    Returns True if the password has been found in breaches.
    Silently returns False on network errors to avoid blocking auth.
    """
    if not _env_bool("AUTH_PASSWORD_CHECK_HIBP", True):
        return False
    sha1 = (
        hashlib.sha1(  # noqa: S324
            password.encode("utf-8"),
        )
        .hexdigest()
        .upper()
    )
    prefix, suffix = sha1[:5], sha1[5:]
    url = f"https://api.pwnedpasswords.com/range/{prefix}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": app_user_agent("password-check")},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
        for line in body.splitlines():
            parts = line.strip().split(":")
            if len(parts) >= 2 and parts[0] == suffix:
                return True
    except TimeoutError:
        _logger.warning("HIBP password check timed out for prefix %s", prefix)
    except Exception:
        _logger.debug("HIBP check failed (network); allowing password")
    return False


def validate_password_policy(password: str, *, username: str | None = None) -> None:
    min_len = password_min_length()
    if len(password) < min_len:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {min_len} characters",
        )

    lowered = password.lower()
    if _env_bool("AUTH_PASSWORD_REJECT_COMMON", True) and lowered in _COMMON_WEAK_PASSWORDS:
        raise HTTPException(status_code=400, detail="Password is too common")

    if _env_bool("AUTH_PASSWORD_DISALLOW_USERNAME", True):
        candidate_username = (username or "").strip().lower()
        if candidate_username and candidate_username in lowered:
            raise HTTPException(
                status_code=400,
                detail="Password must not include username",
            )

    if _env_bool("AUTH_PASSWORD_REQUIRE_LOWER", True) and not any(ch.islower() for ch in password):
        raise HTTPException(status_code=400, detail="Password must include a lowercase letter")
    if _env_bool("AUTH_PASSWORD_REQUIRE_UPPER", True) and not any(ch.isupper() for ch in password):
        raise HTTPException(status_code=400, detail="Password must include an uppercase letter")
    if _env_bool("AUTH_PASSWORD_REQUIRE_DIGIT", True) and not any(ch.isdigit() for ch in password):
        raise HTTPException(status_code=400, detail="Password must include a digit")
    if _env_bool("AUTH_PASSWORD_REQUIRE_SYMBOL", True):
        symbols = set("!@#$%^&*()-_=+[]{};:,.?/|~`")
        if not any(ch in symbols for ch in password):
            raise HTTPException(status_code=400, detail="Password must include a symbol")

    if _check_hibp(password):
        raise HTTPException(
            status_code=400,
            detail=(
                "This password has appeared in a known data breach. "
                "Please choose a different password."
            ),
        )


def _coerce_role(raw: object, *, fallback: Role = "viewer") -> Role:
    role = str(raw)
    if role in AUTH_ROLES:
        return cast(Role, role)
    return fallback


def _requested_garden_id(request: Request) -> int | None:
    raw = request.headers.get("x-garden-id", "").strip()
    if not raw and "query_string" in request.scope:
        raw = request.query_params.get("garden_id", "").strip()
    if not raw:
        return None
    try:
        garden_id = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid garden identifier") from exc
    if garden_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid garden identifier")
    return garden_id


def _garden_exists(conn: DbConn, garden_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM gardens WHERE id = %s",
        (garden_id,),
    ).fetchone()
    return row is not None


def _resolve_user_garden_context(
    conn: DbConn,
    *,
    context: AuthContext,
    requested_garden_id: int | None,
) -> AuthContext:
    if context.user_id is None:
        raise HTTPException(status_code=500, detail="Invalid auth context")

    if requested_garden_id is not None:
        membership = conn.execute(
            """
            SELECT role
            FROM garden_memberships
            WHERE user_id = %s AND garden_id = %s
            LIMIT 1
            """,
            (context.user_id, requested_garden_id),
        ).fetchone()
        if membership:
            return replace(
                context,
                garden_id=requested_garden_id,
                garden_role=_coerce_role(membership["role"], fallback=context.role),
            )
        if context.role == "admin" and _garden_exists(conn, requested_garden_id):
            return replace(
                context,
                garden_id=requested_garden_id,
                garden_role="admin",
            )
        raise HTTPException(status_code=404, detail="Garden not found")

    primary = conn.execute(
        """
        SELECT gm.garden_id, gm.role
        FROM garden_memberships gm
        JOIN gardens g ON g.id = gm.garden_id
        WHERE gm.user_id = %s
          AND g.slug <> %s
        ORDER BY CASE WHEN g.owner_user_id = %s THEN 0 ELSE 1 END,
        CASE gm.role
            WHEN 'admin' THEN 0
            WHEN 'editor' THEN 1
            ELSE 2
        END, gm.garden_id
        LIMIT 1
        """,
        (context.user_id, _DEFAULT_GARDEN_SLUG, context.user_id),
    ).fetchone()
    if primary:
        return replace(
            context,
            garden_id=int(primary["garden_id"]),
            garden_role=_coerce_role(primary["role"], fallback=context.role),
        )

    owned = conn.execute(
        """
        SELECT id
        FROM gardens
        WHERE owner_user_id = %s AND slug <> %s
        ORDER BY id
        LIMIT 1
        """,
        (context.user_id, _DEFAULT_GARDEN_SLUG),
    ).fetchone()
    if owned:
        return replace(
            context,
            garden_id=int(owned["id"]),
            garden_role="admin",
        )

    hidden = conn.execute(
        """
        SELECT gm.garden_id, gm.role
        FROM garden_memberships gm
        JOIN gardens g ON g.id = gm.garden_id
        WHERE gm.user_id = %s AND g.slug = %s
        LIMIT 1
        """,
        (context.user_id, _DEFAULT_GARDEN_SLUG),
    ).fetchone()
    if hidden:
        return replace(
            context,
            garden_id=int(hidden["garden_id"]),
            garden_role=_coerce_role(hidden["role"], fallback=context.role),
        )

    user_row = conn.execute(
        "SELECT role FROM auth_users WHERE id = %s AND is_active = 1 LIMIT 1",
        (context.user_id,),
    ).fetchone()
    if not user_row:
        raise HTTPException(status_code=403, detail="User account is inactive")
    role = _coerce_role(user_row["role"], fallback=context.role)
    if role in ("viewer", "editor"):
        default_garden_id = ensure_default_garden_membership(
            conn,
            user_id=context.user_id,
            role=role,
        )
        return replace(context, garden_id=default_garden_id, garden_role=role)
    admin_default = conn.execute(
        """
        SELECT id
        FROM gardens
        WHERE slug <> %s
        ORDER BY id
        LIMIT 1
        """,
        (_DEFAULT_GARDEN_SLUG,),
    ).fetchone()
    return replace(
        context,
        garden_id=int(admin_default["id"]) if admin_default else None,
        garden_role=role,
    )


def count_users(conn: DbConn) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM auth_users").fetchone()
    return int(row["c"] if row else 0)


def create_user(
    conn: DbConn,
    *,
    username: str,
    password: str | None,
    role: Role,
    created_by_user_id: int | None = None,
    must_change_password: bool = False,
    password_auth_disabled: bool = False,
    passkey_user_handle: str | None = None,
) -> dict[str, object]:
    users_before = count_users(conn)
    normalized = username.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Username is required")
    if len(normalized) > 80:
        raise HTTPException(status_code=400, detail="Username is too long")
    if role not in AUTH_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if password_auth_disabled:
        password_hash: str | None = None
        must_change_password = False
    else:
        if password is None or not password:
            raise HTTPException(status_code=400, detail="Password is required")
        validate_password_policy(password, username=normalized)
        password_hash = hash_password(password)
    try:
        conn.execute(
            """
            INSERT INTO auth_users (
                username,
                password_hash,
                password_auth_disabled,
                passkey_user_handle,
                role,
                created_by_user_id,
                must_change_password
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                normalized,
                password_hash,
                int(bool(password_auth_disabled)),
                passkey_user_handle or generate_passkey_user_handle(),
                role,
                created_by_user_id,
                int(bool(must_change_password)),
            ),
        )
    except psycopg.IntegrityError as exc:
        constraint = str(getattr(getattr(exc, "diag", None), "constraint_name", "") or "")
        if constraint == "ux_auth_users_username":
            raise HTTPException(status_code=409, detail="Username already exists") from exc
        if constraint == "ux_auth_users_passkey_user_handle":
            raise HTTPException(
                status_code=409,
                detail="Passkey user handle already exists",
            ) from exc
        raise HTTPException(status_code=500, detail="Failed to create user") from exc
    row = conn.execute(
        """
        SELECT
            id,
            username,
            role,
            is_active,
            created_by_user_id,
            must_change_password,
            password_auth_disabled,
            passkey_user_handle,
            passkey_prompt_dismissed_until_ms
        FROM auth_users
        WHERE username = %s
        """,
        (normalized,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create user")
    # Admin users always get pro tier so they can access all features.
    if role == "admin":
        conn.execute(
            "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
            (int(row["id"]),),
        )
    ensure_default_garden_membership(
        conn,
        user_id=int(row["id"]),
        role=str(row["role"]),
    )
    if users_before == 0:
        ensure_data_ownership(conn, int(row["id"]))
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "is_active": bool(int(row["is_active"])),
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None
        ),
        "must_change_password": bool(int(row["must_change_password"])),
        "password_auth_disabled": bool(int(row["password_auth_disabled"])),
        "passkey_user_handle": str(row["passkey_user_handle"] or ""),
        "passkey_prompt_dismissed_until_ms": int(row["passkey_prompt_dismissed_until_ms"] or 0),
    }


def warn_csrf_secret_not_configured() -> None:
    """Log a warning if AUTH_CSRF_SECRET is not set.

    Call once at startup so operators know the CSRF secret is
    auto-generated rather than explicitly configured.
    """
    if not os.environ.get("AUTH_CSRF_SECRET", "").strip():
        _logger.warning(
            "AUTH_CSRF_SECRET is not configured; using auto-generated secret from database"
        )


def ensure_bootstrap_user_from_env() -> None:
    if not session_auth_enabled():
        return
    username = os.environ.get("AUTH_BOOTSTRAP_USERNAME", "").strip()
    password = os.environ.get("AUTH_BOOTSTRAP_PASSWORD", "").strip()
    role = os.environ.get("AUTH_BOOTSTRAP_ROLE", "admin").strip().lower() or "admin"
    if not username or not password:
        return
    conn = get_db()
    try:
        if count_users(conn) > 0:
            return
        create_user(
            conn,
            username=username,
            password=password,
            role=_coerce_role(role, fallback="admin"),
        )
        conn.commit()
    finally:
        return_db(conn)


def _authenticate_session_token(
    token: str,
    *,
    via_cookie: bool = False,
    conn: DbConn | None = None,
) -> AuthContext | None:
    owns_conn = conn is None
    conn = get_db() if conn is None else conn
    try:
        now_ms = current_timestamp_ms()
        token_hash = _hash_token(token)
        row = conn.execute(
            """
            SELECT
                s.user_id,
                s.expires_at_ms,
                s.created_at_ms,
                s.reauthenticated_at_ms,
                s.mfa_authenticated_at_ms,
                s.mfa_setup_required,
                u.username,
                u.role,
                u.is_active,
                u.must_change_password,
                u.password_auth_disabled,
                u.passkey_prompt_dismissed_until_ms,
                u.mfa_totp_enabled,
                u.subscription_tier,
                (
                    SELECT COUNT(*)
                    FROM auth_passkeys p
                    WHERE p.user_id = s.user_id
                ) AS passkey_count
            FROM auth_sessions s
            JOIN auth_users u ON u.id = s.user_id
            WHERE s.token_hash = %s
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        if int(row["is_active"]) != 1:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))
            conn.commit()
            return None
        expires_at_ms = int(row["expires_at_ms"])
        absolute_expires_at_ms = int(row["created_at_ms"]) + _session_absolute_ttl_ms()
        if expires_at_ms <= now_ms or absolute_expires_at_ms <= now_ms:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))
            conn.commit()
            return None
        role = _coerce_role(row["role"])
        mfa_enabled = bool(int(row["mfa_totp_enabled"]))
        passkey_count = int(row["passkey_count"] or 0)
        passkey_enrolled = passkey_count > 0
        mfa_authenticated_at_ms = int(row["mfa_authenticated_at_ms"])
        stored_mfa_setup_required = bool(int(row["mfa_setup_required"]))
        current_mfa_setup_required = _admin_mfa_enforced_for_role(
            role, mfa_enabled=mfa_enabled
        ) and not (mfa_enabled or passkey_enrolled)
        # Sliding session: extend expiry when more than half the TTL has elapsed,
        # so active users don't get logged out mid-use.
        ttl = _session_ttl_ms()
        remaining = expires_at_ms - now_ms
        if remaining < ttl // 2:
            expires_at_ms = min(now_ms + ttl, absolute_expires_at_ms)
            conn.execute(
                "UPDATE auth_sessions SET last_seen_at_ms = %s, expires_at_ms = %s "
                "WHERE token_hash = %s",
                (now_ms, expires_at_ms, token_hash),
            )
        else:
            conn.execute(
                "UPDATE auth_sessions SET last_seen_at_ms = %s WHERE token_hash = %s",
                (now_ms, token_hash),
            )
        if stored_mfa_setup_required != current_mfa_setup_required:
            conn.execute(
                "UPDATE auth_sessions SET mfa_setup_required = %s WHERE token_hash = %s",
                (int(current_mfa_setup_required), token_hash),
            )
        conn.commit()
        return AuthContext(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            role=role,
            auth_type="session",
            session_token_hash=token_hash,
            reauthenticated_at_ms=int(row["reauthenticated_at_ms"]),
            mfa_authenticated_at_ms=mfa_authenticated_at_ms,
            mfa_enabled=mfa_enabled,
            passkey_enrolled=passkey_enrolled,
            mfa_setup_required=current_mfa_setup_required,
            session_via_cookie=via_cookie,
            subscription_tier=str(row["subscription_tier"] or "home"),
            must_change_password=bool(int(row["must_change_password"])),
            passkey_count=passkey_count,
            password_auth_disabled=bool(int(row["password_auth_disabled"])),
            passkey_prompt_dismissed_until_ms=int(row["passkey_prompt_dismissed_until_ms"] or 0),
        )
    finally:
        if owns_conn:
            return_db(conn)


def create_session_for_user(
    user_id: int,
    *,
    mfa_authenticated: bool = False,
    mfa_setup_required: bool = False,
    device_label: str = "",
    location_hint: str = "",
) -> tuple[str, int]:
    token = secrets.token_urlsafe(48)
    token_hash = _hash_token(token)
    now_ms = current_timestamp_ms()
    expires_at_ms = now_ms + min(_session_ttl_ms(), _session_absolute_ttl_ms())
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO auth_sessions
                (
                    token_hash,
                    user_id,
                    expires_at_ms,
                    created_at_ms,
                    last_seen_at_ms,
                    reauthenticated_at_ms,
                    mfa_authenticated_at_ms,
                    mfa_setup_required,
                    device_label,
                    location_hint
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                token_hash,
                user_id,
                expires_at_ms,
                now_ms,
                now_ms,
                now_ms,
                now_ms if mfa_authenticated else 0,
                int(bool(mfa_setup_required)),
                device_label.strip()[:120],
                location_hint.strip()[:80],
            ),
        )
        conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at_ms <= %s",
            (now_ms,),
        )
        conn.commit()
    finally:
        return_db(conn)
    return token, expires_at_ms


def revoke_session_token(token: str) -> None:
    if not token:
        return
    token_hash = _hash_token(token)
    conn = get_db()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))
        conn.commit()
    finally:
        return_db(conn)


def authenticate_user_credentials(username: str, password: str) -> dict[str, Any] | None:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                id,
                username,
                password_hash,
                password_auth_disabled,
                role,
                is_active,
                must_change_password,
                mfa_totp_enabled
            FROM auth_users
            WHERE username = %s
            """,
            (username.strip(),),
        ).fetchone()
        if not row:
            return None
        if int(row["is_active"]) != 1:
            return None
        if int(row["password_auth_disabled"]) == 1 or row["password_hash"] is None:
            return None
        if not verify_password_and_upgrade(
            conn,
            user_id=int(row["id"]),
            password=password,
            password_hash=str(row["password_hash"]),
        ):
            return None
        return {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "role": str(row["role"]),
            "must_change_password": bool(int(row["must_change_password"])),
            "mfa_enabled": bool(int(row["mfa_totp_enabled"])),
        }
    finally:
        return_db(conn)


def validate_request_auth(
    request: Request,
    conn: DbConn | None = None,
) -> AuthContext:
    if not is_auth_required():
        if is_loopback_client(request) or _allow_insecure_remote():
            return AuthContext(
                user_id=None,
                username="local",
                role="admin",
                auth_type="none",
                subscription_tier="pro",
            )
        raise HTTPException(
            status_code=503,
            detail=(
                "Remote access is disabled until AUTH_REQUIRED=true and "
                "session auth is configured, or ALLOW_INSECURE_REMOTE=true "
                "is explicitly set."
            ),
        )

    cookie_token = request.cookies.get(session_cookie_name(), "").strip()
    if cookie_token and session_auth_enabled():
        context = _authenticate_session_token(
            cookie_token,
            via_cookie=True,
            conn=conn,
        )
        if context:
            return context

    if api_key_auth_enabled():
        bearer = _get_bearer_token(request)
        configured = _configured_api_key()
        if not configured:
            raise HTTPException(
                status_code=503,
                detail="AUTH_REQUIRED=true but AUTH_API_KEY is not configured",
            )
        provided = request.headers.get("x-api-key", "").strip()
        if not provided:
            provided = bearer
        if provided and hmac.compare_digest(provided, configured):
            return AuthContext(
                user_id=None,
                username="api-key",
                role="admin",
                auth_type="api_key",
                subscription_tier="pro",
            )

    detail = "Unauthorized" if api_key_auth_enabled() else "Unauthorized: session token required"
    raise HTTPException(status_code=401, detail=detail)


def resolve_garden_context(
    conn: DbConn,
    request: Request,
    context: AuthContext,
) -> AuthContext:
    requested_garden_id = _requested_garden_id(request)
    if context.user_id is None:
        if requested_garden_id is not None:
            if not _garden_exists(conn, requested_garden_id):
                raise HTTPException(status_code=404, detail="Garden not found")
            return replace(
                context,
                garden_id=requested_garden_id,
                garden_role="admin",
            )
        return replace(
            context,
            garden_id=ensure_default_garden(conn),
            garden_role="admin",
        )
    return _resolve_user_garden_context(
        conn,
        context=context,
        requested_garden_id=requested_garden_id,
    )


def _request_auth_conn(
    request: Request,
) -> DbConn | None:
    conn = request_scoped_db_conn(request)
    if conn is None:
        return None
    if conn.info.transaction_status != TransactionStatus.IDLE:
        return None
    return conn


def resolve_request_auth_context(
    request: Request,
    conn: DbConn | None = None,
) -> AuthContext:
    requested_garden_id = _requested_garden_id(request)
    existing = getattr(request.state, "auth_context", None)
    if isinstance(existing, AuthContext):
        if requested_garden_id is None or existing.garden_id == requested_garden_id:
            return existing
        base = replace(existing, garden_id=None, garden_role=None)
    else:
        conn = conn if conn is not None else _request_auth_conn(request)
        base = validate_request_auth(request, conn=conn)
    owns_conn = conn is None
    conn = get_db() if conn is None else conn
    try:
        resolved = resolve_garden_context(conn, request, base)
        conn.commit()
    finally:
        if owns_conn:
            return_db(conn)
    request.state.auth_context = resolved
    return resolved


def has_write_access(context: AuthContext) -> bool:
    effective_role = context.garden_role or context.role
    return effective_role in {"editor", "admin"}
