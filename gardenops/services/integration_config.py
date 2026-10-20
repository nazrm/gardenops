from __future__ import annotations

import hmac
import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from gardenops.db import DbConn
from gardenops.feature_gates import feature_allowed
from gardenops.security import AuthContext, has_write_access


def mcp_enabled() -> bool:
    value = os.environ.get("MCP_ENABLED", "false").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError("MCP_ENABLED must be true or false")


def integration_token_matches(provided: str) -> bool:
    configured = os.environ.get("MCP_BEARER_TOKEN", "").strip()
    return bool(configured and provided and hmac.compare_digest(provided, configured))


def require_local_mcp_request(request: Request) -> None:
    def local_host(value: str) -> bool:
        if os.environ.get("APP_ENV", "").lower() == "test" and value in {
            "testclient",
            "testserver",
        }:
            return True
        if value == "localhost":
            return True
        try:
            return ipaddress.ip_address(value).is_loopback
        except ValueError:
            return False

    if (
        request.client is None
        or not local_host(request.client.host)
        or not local_host(request.url.hostname or "")
        or any(
            name in {"forwarded", "x-real-ip"} or name.startswith("x-forwarded-")
            for name in request.headers
        )
    ):
        raise HTTPException(status_code=403, detail="MCP requires a direct loopback connection")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            valid_origin = parsed.scheme in {"http", "https"} and local_host(parsed.hostname or "")
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise HTTPException(status_code=403, detail="MCP origin must be loopback")


def _required(name: str, *, minimum: int = 1) -> str:
    value = os.environ.get(name, "").strip()
    if len(value) < minimum or value.lower() in {
        "change-me",
        "changeme",
        "example",
        "placeholder",
        "replace-me",
        "test",
    }:
        raise RuntimeError(f"{name} is missing or uses a placeholder value")
    if len(value) > 255:
        raise RuntimeError(f"{name} must contain at most 255 characters")
    return value


def validate_integration_config() -> None:
    if mcp_enabled():
        _required("MCP_BEARER_TOKEN", minimum=32)
        configured_binding_values()


@dataclass(frozen=True)
class AssistantBinding:
    client_id: str
    actor_id: str
    username: str
    garden_slug: str
    user_id: int
    garden_id: int
    context: AuthContext


def configured_binding_values() -> tuple[str, str, str, str]:
    return (
        _required("MCP_CLIENT_ID"),
        _required("MCP_ACTOR_ID"),
        _required("MCP_USERNAME"),
        _required("MCP_GARDEN_SLUG"),
    )


def resolve_assistant_binding(db: DbConn) -> AssistantBinding:
    client_id, actor_id, username, garden_slug = configured_binding_values()
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
        client_id=client_id,
        actor_id=actor_id,
        username=username,
        garden_slug=garden_slug,
        user_id=int(row["user_id"]),
        garden_id=int(row["garden_id"]),
        context=context,
    )


def assert_source_binding(binding: AssistantBinding, *, client_id: str, actor_id: str) -> None:
    if not hmac.compare_digest(
        client_id.encode(), binding.client_id.encode()
    ) or not hmac.compare_digest(actor_id.encode(), binding.actor_id.encode()):
        raise PermissionError("MCP client is not authorized")
