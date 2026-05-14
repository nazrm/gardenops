from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}")
_BASIC_RE = re.compile(r"(?i)\b(basic)\s+[A-Za-z0-9._~+/=-]{12,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|DATABASE_URL|AUTH)[A-Z0-9_]*)=([^\s&;,]+)",
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_POSTGRES_URL_RE = re.compile(r"postgres(?:ql)?://[^\s\"'<>]+", re.IGNORECASE)
_CALENDAR_FEED_PATH_RE = re.compile(
    r"(?i)(/calendar/subscriptions/)[^/?\s\"'<>]+(\.ics)\b",
)
_SHADEMAP_TERRAIN_PATH_RE = re.compile(
    r"(?i)(/shademap/terrain/[^?\s\"'<>]+)(?:\?[^\s\"'<>]*)?",
)
_PATH_QUERY_RE = re.compile(r"(?P<path>/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+)\?[^\s\"'<>]+")
_SENSITIVE_QUERY_KEYS = {
    "api-key",
    "api_key",
    "apikey",
    "access_token",
    "auth",
    "code",
    "invite",
    "invite_token",
    "key",
    "password",
    "reset",
    "reset_token",
    "secret",
    "signature",
    "token",
}


def _redact_url(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return "[REDACTED_URL]"
    if not parsed.scheme or not parsed.netloc:
        return raw_url
    try:
        port = parsed.port
    except ValueError:
        return "[REDACTED_URL]"
    netloc = parsed.hostname or ""
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = _CALENDAR_FEED_PATH_RE.sub(r"\1[REDACTED]\2", parsed.path)
    if path.startswith("/shademap/terrain/"):
        return urlunsplit((parsed.scheme, netloc, path, "[REDACTED]", ""))
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        redacted = "[REDACTED]" if key.lower() in _SENSITIVE_QUERY_KEYS else value
        query_pairs.append((key, redacted))
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            path,
            urlencode(query_pairs, doseq=True),
            "",
        ),
    )


def redact_sensitive_text(value: object, max_len: int | None = None) -> str:
    text = str(value or "")
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _POSTGRES_URL_RE.sub("[REDACTED_DATABASE_URL]", text)
    text = _BEARER_RE.sub(r"\1 [REDACTED_TOKEN]", text)
    text = _BASIC_RE.sub(r"\1 [REDACTED_TOKEN]", text)
    text = _JWT_RE.sub("[REDACTED_TOKEN]", text)
    text = _ASSIGNMENT_SECRET_RE.sub(r"\1=[REDACTED]", text)
    text = _CALENDAR_FEED_PATH_RE.sub(r"\1[REDACTED]\2", text)
    text = _SHADEMAP_TERRAIN_PATH_RE.sub(r"\1?[REDACTED]", text)
    text = _URL_RE.sub(lambda match: _redact_url(match.group(0)), text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + "... [truncated]"
    return text


def redact_external_log_text(value: object, max_len: int | None = None) -> str:
    text = redact_sensitive_text(value)
    text = _PATH_QUERY_RE.sub(r"\g<path>?[REDACTED]", text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + "... [truncated]"
    return text
