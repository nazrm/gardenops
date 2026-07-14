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
    (
        "authorization",
        re.compile(r"\b(?:Basic|Bearer)\s+(?!\[redacted\])[^\s\"',;]+", re.IGNORECASE),
    ),
    (
        "cookie",
        re.compile(
            r"\b(?:gardenops_session|gardenops_csrf|XSRF-TOKEN)=(?!\[redacted\])[^;\s\"']+",
            re.IGNORECASE,
        ),
    ),
    (
        "sensitive-field",
        re.compile(
            r"[\"']?(?:password|csrf_token|access_token|refresh_token|client_secret|subscription_token)"
            r"[\"']?\s*[:=]\s*[\"'](?!\[redacted\])[^\"']+[\"']",
            re.IGNORECASE,
        ),
    ),
    (
        "subscription-token",
        re.compile(
            r"/calendar/subscriptions/(?!\{(?:redacted|token)\}\.ics)[^/?#\s\"'<>]+\.ics\b",
            re.IGNORECASE,
        ),
    ),
    (
        "sensitive-query",
        re.compile(
            r"[?&](?:token|secret|password|csrf_token)=(?!\[redacted\])[^&#\s\"']+",
            re.IGNORECASE,
        ),
    ),
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _is_redacted(value: object) -> bool:
    return value in (None, "", REDACTED) or (
        isinstance(value, str) and "[redacted" in value.lower()
    )


def _secret_category_for_name(value: object) -> str:
    normalized = _normalized_key(value)
    if normalized in {"authorization", "proxy_authorization"}:
        return "authorization"
    if normalized in {"cookie", "cookies", "gardenops_session", "set_cookie"}:
        return "cookie"
    if "csrf" in normalized or "xsrf" in normalized:
        return "csrf"
    if normalized == "subscription_token":
        return "subscription-token"
    if normalized in {"credential", "credentials", "password"}:
        return "credential"
    if normalized in {"client_secret", "secret"}:
        return "secret"
    return "token"


def _pattern_secret_categories(value: str) -> set[str]:
    return {category for category, pattern in SECRET_PATTERNS if pattern.search(value)}


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


def _json_secret_categories(value: Any) -> set[str]:
    if isinstance(value, list):
        categories: set[str] = set()
        for item in value:
            categories.update(_json_secret_categories(item))
        return categories
    if not isinstance(value, dict):
        return _pattern_secret_categories(value) if isinstance(value, str) else set()

    header_name = str(value.get("name", "")).strip().lower()
    named_value = _normalized_key(header_name)
    categories = set()
    for key, item in value.items():
        normalized = _normalized_key(key)
        if normalized in SENSITIVE_KEYS and not _is_redacted(item):
            categories.add(_secret_category_for_name(normalized))
        if (
            normalized == "value"
            and (header_name in SENSITIVE_HEADERS or named_value in SENSITIVE_KEYS)
            and not _is_redacted(item)
        ):
            categories.add(_secret_category_for_name(header_name))
        categories.update(_json_secret_categories(item))
    return categories


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


def _sanitize_binary(data: bytes) -> bytes:
    text = data.decode("latin-1")
    for _category, pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: "#" * len(match.group(0)), text)
    return text.encode("latin-1")


def _secret_categories(data: bytes) -> set[str]:
    text = _decoded_text(data)
    if text is None:
        return {
            f"binary:{category}" for category in _pattern_secret_categories(data.decode("latin-1"))
        }
    categories = {f"text:{category}" for category in _pattern_secret_categories(text)}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        categories.update(f"structured:{category}" for category in _json_secret_categories(parsed))
    for line in text.splitlines():
        try:
            categories.update(
                f"structured:{category}" for category in _json_secret_categories(json.loads(line))
            )
        except json.JSONDecodeError:
            continue
    return categories


def _contains_secret(data: bytes) -> bool:
    return bool(_secret_categories(data))


def _safe_member_label(name: str) -> str:
    if name in {"trace.trace", "trace.network", "trace.stacks"}:
        return name
    if name.startswith("resources/"):
        return "resources/<member>"
    return "<other-member>"


def sanitize_trace(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise ValueError("trace must be a regular file")
    if destination.exists() or destination.is_symlink():
        raise ValueError("sanitized trace destination must not exist")
    try:
        with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, "x") as output:
            for info in archive.infolist():
                data = archive.read(info)
                text = _decoded_text(data)
                sanitized = (
                    _sanitize_text(text).encode("utf-8")
                    if text is not None
                    else _sanitize_binary(data)
                )
                output.writestr(info, sanitized)
        os.chmod(destination, 0o600)
        validate_trace(destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


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
            raise ValueError(
                "trace archive contains a corrupt member: " + _safe_member_label(corrupt)
            )
        findings: dict[str, set[str]] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            categories = _secret_categories(archive.read(info))
            if categories:
                findings.setdefault(_safe_member_label(info.filename), set()).update(categories)
        if findings:
            diagnostics = "; ".join(
                f"{member}[{','.join(sorted(categories))}]"
                for member, categories in sorted(findings.items())
            )
            raise ValueError(f"trace archive contains secret material: {diagnostics}")


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
