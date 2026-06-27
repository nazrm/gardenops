"""Validation helpers for user-visible plant and plot identifiers."""

from __future__ import annotations

import re
from urllib.parse import unquote

from fastapi import HTTPException

_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


def normalize_public_id(value: object, *, field_name: str = "public_id") -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError(f"{field_name} is required")
    decoded = unquote(candidate)
    if decoded != candidate:
        raise ValueError(f"{field_name} must not contain percent-encoded characters")
    if not _PUBLIC_ID_RE.fullmatch(candidate):
        raise ValueError(
            f"{field_name} must be 1-40 characters using letters, numbers, '_' or '-'",
        )
    return candidate


def normalize_public_id_list(values: object, *, field_name: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    return [
        normalize_public_id(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(values)
    ]


def require_public_id(value: object, *, field_name: str = "public_id") -> str:
    try:
        return normalize_public_id(value, field_name=field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
