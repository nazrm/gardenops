#!/usr/bin/env python3
"""Sanitize and validate retained Playwright trace archives."""

from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

REDACTED = "[redacted]"
SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "client_secret",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "csrf",
    "csrf_token",
    "gardenops_csrf",
    "gardenops_session",
    "password",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "set_cookie",
    "subscription_token",
    "token",
    "token_hash",
    "x_csrf_token",
    "x_xsrf_token",
    "xsrf_token",
}
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
}
SECRET_PATTERNS = (
    re.compile(r"\b(?:Basic|Bearer)\s+(?!\[redacted\])[^\s\"',;]+", re.IGNORECASE),
    re.compile(
        r"\b(?:gardenops_session|gardenops_csrf|XSRF-TOKEN)=(?!\[redacted\])[^;\s\"']+",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"']?(?:password|csrf_token|access_token|refresh_token|client_secret|subscription_token)"
        r"[\"']?\s*[:=]\s*[\"'](?!\[redacted\])[^\"']+[\"']",
        re.IGNORECASE,
    ),
    re.compile(
        r"/calendar/subscriptions/(?!\{(?:redacted|token)\}\.ics)[^/?#\s\"'<>]+\.ics\b",
        re.IGNORECASE,
    ),
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _is_redacted(value: object) -> bool:
    return value in (None, "", REDACTED) or (
        isinstance(value, str) and "[redacted" in value.lower()
    )


def _sanitize_string(value: str) -> str:
    sanitized = re.sub(
        r"\b(Basic|Bearer)\s+[^\s\"',;]+",
        rf"\1 {REDACTED}",
        value,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\b(gardenops_session|gardenops_csrf|XSRF-TOKEN)=[^;\s\"']+",
        rf"\1={REDACTED}",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"(/calendar/subscriptions/)[^/?#\s\"'<>]+(\.ics\b)",
        r"\1{redacted}\2",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"([?&](?:token|secret|password|csrf_token)=)[^&#\s\"']+",
        rf"\1{REDACTED}",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"([\"']?(?:password|csrf_token|access_token|refresh_token|client_secret|subscription_token)"
        r"[\"']?\s*[:=]\s*)[\"'][^\"']*[\"']",
        rf"\1\"{REDACTED}\"",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if not isinstance(value, dict):
        return _sanitize_string(value) if isinstance(value, str) else value

    header_name = str(value.get("name", "")).strip().lower()
    named_value = _normalized_key(header_name)
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        normalized = _normalized_key(key)
        if normalized in SENSITIVE_KEYS or (
            normalized == "value"
            and (header_name in SENSITIVE_HEADERS or named_value in SENSITIVE_KEYS)
        ):
            sanitized[key] = REDACTED
        else:
            sanitized[key] = _sanitize_json(item)
    return sanitized


def _json_secret_path(value: Any) -> bool:
    if isinstance(value, list):
        return any(_json_secret_path(item) for item in value)
    if not isinstance(value, dict):
        return isinstance(value, str) and any(pattern.search(value) for pattern in SECRET_PATTERNS)

    header_name = str(value.get("name", "")).strip().lower()
    named_value = _normalized_key(header_name)
    for key, item in value.items():
        normalized = _normalized_key(key)
        if normalized in SENSITIVE_KEYS and not _is_redacted(item):
            return True
        if (
            normalized == "value"
            and (header_name in SENSITIVE_HEADERS or named_value in SENSITIVE_KEYS)
            and not _is_redacted(item)
        ):
            return True
        if _json_secret_path(item):
            return True
    return False


def _decoded_text(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _sanitize_text(text: str) -> str:
    try:
        return json.dumps(_sanitize_json(json.loads(text)), separators=(",", ":"))
    except json.JSONDecodeError:
        pass

    lines = text.splitlines(keepends=True)
    if lines:
        sanitized_lines: list[str] = []
        parsed_any = False
        for line in lines:
            ending = "\n" if line.endswith("\n") else ""
            content = line[:-1] if ending else line
            try:
                sanitized_lines.append(
                    json.dumps(_sanitize_json(json.loads(content)), separators=(",", ":")) + ending
                )
                parsed_any = True
            except json.JSONDecodeError:
                sanitized_lines.append(_sanitize_string(line))
        if parsed_any:
            return "".join(sanitized_lines)
    return _sanitize_string(text)


def _contains_secret(data: bytes) -> bool:
    text = _decoded_text(data)
    if text is None:
        return any(pattern.search(data.decode("latin-1")) for pattern in SECRET_PATTERNS)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None and _json_secret_path(parsed):
        return True
    for line in text.splitlines():
        try:
            if _json_secret_path(json.loads(line)):
                return True
        except json.JSONDecodeError:
            continue
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def sanitize_trace(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise ValueError("trace must be a regular file")
    if destination.exists() or destination.is_symlink():
        raise ValueError("sanitized trace destination must not exist")
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, "x") as output:
        for info in archive.infolist():
            data = archive.read(info)
            text = _decoded_text(data)
            sanitized = _sanitize_text(text).encode("utf-8") if text is not None else data
            output.writestr(info, sanitized)
    os.chmod(destination, 0o600)
    validate_trace(destination)


def validate_trace(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("trace must be a regular file")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for required in ("trace.trace", "trace.network"):
            if required not in names:
                raise ValueError(f"trace archive is missing {required}")
            info = archive.getinfo(required)
            if info.file_size <= 0:
                raise ValueError(f"trace archive is missing non-empty {required}")
        corrupt = archive.testzip()
        if corrupt is not None:
            raise ValueError(f"trace archive contains a corrupt member: {corrupt}")
        retained_members = (info for info in archive.infolist() if not info.is_dir())
        if any(_contains_secret(archive.read(info)) for info in retained_members):
            raise ValueError("trace archive contains secret material")


def main() -> int:
    sanitize = len(sys.argv) == 4 and sys.argv[1] == "--sanitize"
    if len(sys.argv) != 2 and not sanitize:
        print(
            "usage: validate_playwright_trace.py TRACE.zip | "
            "validate_playwright_trace.py --sanitize SOURCE.zip DESTINATION.zip",
            file=sys.stderr,
        )
        return 2
    try:
        if sanitize:
            sanitize_trace(Path(sys.argv[2]), Path(sys.argv[3]))
        else:
            validate_trace(Path(sys.argv[1]))
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        print(f"invalid Playwright trace: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
