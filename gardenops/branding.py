"""Product branding helpers.

Keep these names presentation-only. Internal package names, database roles,
cookie names, and existing deployment files remain stable until a deliberate
compatibility-breaking rename is planned.
"""

from __future__ import annotations

import os
import re

DEFAULT_APP_NAME = "GardenOps"
DEFAULT_APP_SLUG = "gardenops"

_MAX_NAME_LENGTH = 80
_MAX_SLUG_LENGTH = 60
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _env_text(name: str) -> str:
    return os.environ.get(name, "").strip()


def app_name() -> str:
    configured = _env_text("APP_NAME")
    if not configured:
        return DEFAULT_APP_NAME
    cleaned = _CONTROL_PATTERN.sub(" ", configured)
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned[:_MAX_NAME_LENGTH].strip() or DEFAULT_APP_NAME


def _slugify(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    return slug[:_MAX_SLUG_LENGTH].strip("-")


def app_slug() -> str:
    configured = _slugify(_env_text("APP_SLUG"))
    if configured:
        return configured
    return _slugify(app_name()) or DEFAULT_APP_SLUG


def app_user_agent(component: str) -> str:
    normalized_component = _slugify(component) or "client"
    return f"{app_slug()}/1.0 {normalized_component}"
