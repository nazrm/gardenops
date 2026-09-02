from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from gardenops.db import DbConn
from gardenops.feature_gates import feature_allowed
from gardenops.security import AuthContext, has_write_access

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_TOKEN_PLACEHOLDERS = frozenset(
    {"change-me", "changeme", "example", "placeholder", "replace-me", "test"}
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def mcp_enabled() -> bool:
    return _env_bool("MCP_ENABLED")


def matrix_enabled() -> bool:
    return _env_bool("MATRIX_ENABLED")


def mcp_bearer_token() -> str:
    return os.environ.get("MCP_BEARER_TOKEN", "").strip()


def integration_token_matches(provided: str) -> bool:
    configured = mcp_bearer_token()
    return bool(configured and provided and hmac.compare_digest(provided, configured))


def _require_secret(name: str, *, min_length: int = 1) -> str:
    value = os.environ.get(name, "").strip()
    if len(value) < min_length or value.lower() in _TOKEN_PLACEHOLDERS:
        raise RuntimeError(f"{name} is missing or uses a placeholder value")
    return value


@dataclass(frozen=True)
class AssistantBinding:
    room_id: str
    sender_id: str
    username: str
    garden_slug: str
    user_id: int
    garden_id: int
    context: AuthContext


@dataclass(frozen=True)
class MatrixRuntimeConfig:
    homeserver_url: str
    user_id: str
    access_token: str
    device_id: str
    store_path: str
    e2ee: bool
    room_id: str
    allowed_sender: str
    gardenops_username: str
    garden_slug: str
    trigger_mode: str
    timezone: str
    capture_ttl_days: int
    sync_timeout_ms: int
    max_pending_events: int
    mcp_url: str
    mcp_token: str


def validate_integration_config() -> None:
    if mcp_enabled():
        _require_secret("MCP_BEARER_TOKEN", min_length=32)
    if not matrix_enabled():
        return
    if not mcp_enabled():
        raise RuntimeError("MATRIX_ENABLED=true requires MCP_ENABLED=true")
    matrix_runtime_config()


def matrix_runtime_config() -> MatrixRuntimeConfig:
    homeserver_url = _require_secret("MATRIX_HOMESERVER_URL")
    parsed_homeserver = urlsplit(homeserver_url)
    if parsed_homeserver.scheme not in {"http", "https"} or not parsed_homeserver.netloc:
        raise RuntimeError("MATRIX_HOMESERVER_URL must be an absolute HTTP(S) URL")
    user_id = _require_secret("MATRIX_USER_ID")
    room_id = _require_secret("MATRIX_ROOM_ID")
    sender = _require_secret("MATRIX_ALLOWED_SENDER")
    if not user_id.startswith("@") or ":" not in user_id:
        raise RuntimeError("MATRIX_USER_ID must be an exact Matrix user ID")
    if not sender.startswith("@") or ":" not in sender:
        raise RuntimeError("MATRIX_ALLOWED_SENDER must be an exact Matrix user ID")
    if hmac.compare_digest(user_id, sender):
        raise RuntimeError("MATRIX_ALLOWED_SENDER must not be the Matrix bot user")
    if not room_id.startswith("!") or ":" not in room_id:
        raise RuntimeError("MATRIX_ROOM_ID must be an exact Matrix room ID")
    trigger_mode = os.environ.get("MATRIX_TRIGGER_MODE", "mention").strip().lower()
    if trigger_mode not in {"mention", "all"}:
        raise RuntimeError("MATRIX_TRIGGER_MODE must be mention or all")
    mcp_url = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp").strip()
    parsed_mcp = urlsplit(mcp_url)
    if (
        parsed_mcp.scheme != "http"
        or parsed_mcp.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed_mcp.path != "/mcp"
        or parsed_mcp.username is not None
        or parsed_mcp.password is not None
        or parsed_mcp.query
        or parsed_mcp.fragment
    ):
        raise RuntimeError("MCP_URL must be a loopback HTTP URL ending in /mcp")
    return MatrixRuntimeConfig(
        homeserver_url=homeserver_url.rstrip("/"),
        user_id=user_id,
        access_token=_require_secret("MATRIX_ACCESS_TOKEN"),
        device_id=_require_secret("MATRIX_DEVICE_ID"),
        store_path=(
            os.environ.get("MATRIX_STORE_PATH", "/opt/gardenops/matrix").strip()
            or "/opt/gardenops/matrix"
        ),
        e2ee=_env_bool("MATRIX_E2EE", True),
        room_id=room_id,
        allowed_sender=sender,
        gardenops_username=_require_secret("MATRIX_GARDENOPS_USERNAME"),
        garden_slug=_require_secret("MATRIX_GARDEN_SLUG"),
        trigger_mode=trigger_mode,
        timezone=os.environ.get("MATRIX_TIMEZONE", "Europe/Oslo").strip() or "Europe/Oslo",
        capture_ttl_days=_env_int("MATRIX_CAPTURE_TTL_DAYS", 7, minimum=1, maximum=30),
        sync_timeout_ms=_env_int("MATRIX_SYNC_TIMEOUT_MS", 30_000, minimum=1_000, maximum=120_000),
        max_pending_events=_env_int("MATRIX_MAX_PENDING_EVENTS", 20, minimum=1, maximum=100),
        mcp_url=mcp_url,
        mcp_token=_require_secret("MCP_BEARER_TOKEN", min_length=32),
    )


def configured_binding_values() -> tuple[str, str, str, str]:
    return (
        os.environ.get("MATRIX_ROOM_ID", "").strip(),
        os.environ.get("MATRIX_ALLOWED_SENDER", "").strip(),
        os.environ.get("MATRIX_GARDENOPS_USERNAME", "").strip(),
        os.environ.get("MATRIX_GARDEN_SLUG", "").strip(),
    )


def resolve_assistant_binding(db: DbConn) -> AssistantBinding:
    room_id, sender_id, username, garden_slug = configured_binding_values()
    if not all((room_id, sender_id, username, garden_slug)):
        raise RuntimeError("Matrix GardenOps binding is incomplete")
    if not room_id.startswith("!") or ":" not in room_id:
        raise RuntimeError("MATRIX_ROOM_ID must be an exact Matrix room ID")
    if not sender_id.startswith("@") or ":" not in sender_id:
        raise RuntimeError("MATRIX_ALLOWED_SENDER must be an exact Matrix user ID")
    row = db.execute(
        """
        SELECT u.id AS user_id, u.username, u.role AS platform_role,
               u.subscription_tier, g.id AS garden_id, g.slug AS garden_slug,
               gm.role AS garden_role
        FROM auth_users u
        JOIN garden_memberships gm ON gm.user_id = u.id
        JOIN gardens g ON g.id = gm.garden_id
        WHERE u.username = %s AND u.is_active = 1 AND g.slug = %s
        LIMIT 1
        """,
        (username, garden_slug),
    ).fetchone()
    if not row:
        raise RuntimeError("Configured GardenOps user/garden membership was not found")
    platform_role = str(row["platform_role"])
    garden_role = str(row["garden_role"])
    if platform_role not in {"viewer", "editor", "admin"} or garden_role not in {
        "viewer",
        "editor",
        "admin",
    }:
        raise RuntimeError("Configured GardenOps binding has an invalid role")
    context = AuthContext(
        user_id=int(row["user_id"]),
        username=str(row["username"]),
        role=platform_role,  # type: ignore[arg-type]
        auth_type="session",
        garden_id=int(row["garden_id"]),
        garden_role=garden_role,  # type: ignore[arg-type]
        subscription_tier=str(row["subscription_tier"] or "home"),
    )
    if not has_write_access(context):
        raise RuntimeError("Configured GardenOps binding requires editor or admin access")
    if not feature_allowed(context.subscription_tier, "ai"):
        raise RuntimeError("Configured GardenOps user does not have the AI feature entitlement")
    return AssistantBinding(
        room_id=room_id,
        sender_id=sender_id,
        username=username,
        garden_slug=garden_slug,
        user_id=int(row["user_id"]),
        garden_id=int(row["garden_id"]),
        context=context,
    )


def assert_source_binding(binding: AssistantBinding, *, room_id: str, sender_id: str) -> None:
    if not hmac.compare_digest(room_id, binding.room_id) or not hmac.compare_digest(
        sender_id, binding.sender_id
    ):
        raise PermissionError("Matrix source is not authorized")
