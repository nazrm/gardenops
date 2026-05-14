"""Shared helpers for router modules.

Extracted from per-router duplicates to a single source of truth.
"""

import json
import secrets
from datetime import date

from fastapi import HTTPException, Request

from gardenops.security import AuthContext, has_write_access, resolve_request_auth_context


def auth_context(request: Request) -> AuthContext:
    return resolve_request_auth_context(request)


def active_garden_id(context: AuthContext) -> int:
    if context.garden_id is None:
        raise HTTPException(status_code=500, detail="Missing garden context")
    return int(context.garden_id)


def require_write(context: AuthContext) -> None:
    if not has_write_access(context):
        raise HTTPException(status_code=403, detail="Write access required")


def is_local_admin_fallback(context: AuthContext) -> bool:
    return context.user_id is None and context.role == "admin"


def effective_role(context: AuthContext) -> str:
    return context.garden_role or context.role


def is_owner_or_admin(context: AuthContext, owner_user_id: int | None) -> bool:
    if context.user_id is None or effective_role(context) == "admin":
        return True
    if owner_user_id is None:
        return False
    return int(owner_user_id) == int(context.user_id)


def validate_date(value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date: {value}") from None


def dedupe_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def parse_metadata(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return {}
    return value if isinstance(value, dict) else {}


def dump_metadata(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def generate_public_id(prefix: str) -> str:
    normalized = "".join(ch for ch in prefix.lower() if ch.isalnum())[:8]
    if not normalized:
        raise ValueError("Public id prefix must contain at least one alphanumeric character")
    return f"{normalized}_{secrets.token_hex(10)}"
