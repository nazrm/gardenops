"""Request-scoped observability helpers for correlation IDs and structured logs."""

from __future__ import annotations

import contextvars
import logging
import re
import secrets
from typing import NamedTuple

_REQUEST_ID_MAX_LEN = 64
_REQUEST_ID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gardenops_request_id",
    default="",
)
_request_path_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gardenops_request_path",
    default="",
)
_request_method_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "gardenops_request_method",
    default="",
)


class RequestContextTokens(NamedTuple):
    request_id: contextvars.Token[str]
    path: contextvars.Token[str]
    method: contextvars.Token[str]


def normalize_request_id(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    value = _REQUEST_ID_SANITIZE_RE.sub("-", value).strip(".-_")
    if not value:
        return ""
    return value[:_REQUEST_ID_MAX_LEN]


def generate_request_id() -> str:
    return secrets.token_hex(16)


def bind_request_context(*, request_id: str, path: str, method: str) -> RequestContextTokens:
    return RequestContextTokens(
        request_id=_request_id_ctx.set(normalize_request_id(request_id)),
        path=_request_path_ctx.set(str(path or "")),
        method=_request_method_ctx.set(str(method or "").upper()),
    )


def reset_request_context(tokens: RequestContextTokens) -> None:
    _request_id_ctx.reset(tokens.request_id)
    _request_path_ctx.reset(tokens.path)
    _request_method_ctx.reset(tokens.method)


def get_request_id() -> str:
    return _request_id_ctx.get("")


def get_request_path() -> str:
    return _request_path_ctx.get("")


def get_request_method() -> str:
    return _request_method_ctx.get("")


def observability_extra(**fields: object) -> dict[str, object]:
    extra: dict[str, object] = {}
    request_id = get_request_id()
    request_path = get_request_path()
    request_method = get_request_method()
    if request_id and "request_id" not in fields:
        extra["request_id"] = request_id
    if request_path and "path" not in fields:
        extra["path"] = request_path
    if request_method and "method" not in fields:
        extra["method"] = request_method
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        extra[key] = value
    return extra


class RequestContextFilter(logging.Filter):
    """Attach request-scoped contextvars to log records when available."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", ""):
            request_id = get_request_id()
            if request_id:
                record.request_id = request_id
        if not getattr(record, "path", ""):
            path = get_request_path()
            if path:
                record.path = path
        if not getattr(record, "method", ""):
            method = get_request_method()
            if method:
                record.method = method
        return True
