#!/usr/bin/env python3
"""Produce a sanitized, structured summary of recent errors from errors.jsonl.

Usage: python3 scripts/summarize_errors.py [minutes] [--user-facing] [--grouped] [--limit N]

Reads the configured errors.jsonl, filters to the last N minutes (default 5),
and outputs a short structured summary
with only safe fields.  Free-text message content is truncated and
stripped of control characters so it cannot smuggle instructions
into an LLM context that consumes this output.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gardenops.redaction import redact_sensitive_text  # noqa: E402

DEFAULT_LOG_FILE = ROOT / "logs" / "errors.jsonl"
MSG_MAX = 200
TRACEBACK_MAX = 500
SYNTHETIC_CSP_PROBE = "CSP report received: {'csp-report': {'blocked-uri': 'https://example.com'}}"


def sanitize(value: str, max_len: int = MSG_MAX) -> str:
    """Strip control chars, redact common secrets, and truncate."""
    return redact_sensitive_text(value, max_len=max_len)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("minutes", nargs="?", type=int, default=5)
    parser.add_argument("--user-facing", action="store_true")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Explicit errors.jsonl path. Defaults to GARDENOPS_LOGS_DIR/errors.jsonl or repo logs.",
    )
    parser.add_argument(
        "--require-log",
        action="store_true",
        help="Exit nonzero if the log file is missing.",
    )
    parser.add_argument(
        "--grouped",
        action="store_true",
        help="Group non-user-facing output by sanitized error signature.",
    )
    parser.add_argument(
        "--exclude-synthetic",
        action="store_true",
        help=(
            "Drop known test/smoke entries and expected anonymous auth bootstrap probes "
            "from deployed-readiness summaries."
        ),
    )
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def _default_log_file() -> Path:
    logs_dir = os.environ.get("GARDENOPS_LOGS_DIR", "").strip()
    if logs_dir:
        return Path(logs_dir) / "errors.jsonl"
    return DEFAULT_LOG_FILE


def _is_synthetic_entry(entry: dict[str, object]) -> bool:
    message = str(entry.get("message", ""))
    traceback = str(entry.get("traceback", ""))
    path = str(entry.get("path", ""))
    if "ip=testclient" in message:
        return True
    if "unittest/mock.py" in traceback:
        return True
    return path == "/api/security/csp-report" and message == SYNTHETIC_CSP_PROBE


def _is_expected_anonymous_auth_probe(entry: dict[str, object]) -> bool:
    method = str(entry.get("method", ""))
    path = str(entry.get("path", ""))
    api_path = str(entry.get("api_path", ""))
    error_kind = str(entry.get("error_kind", ""))
    message = str(entry.get("message", ""))
    status_code = str(entry.get("status_code", ""))
    handled = entry.get("handled")

    if method == "CLIENT":
        return (
            error_kind == "api_error"
            and handled is True
            and api_path == "/api/auth/me"
            and status_code == "401"
            and message == "Client api_error: Unauthorized: session token required"
        )

    return (
        method == "GET"
        and path == "/api/auth/me"
        and message.startswith("Auth rejected: GET /api/auth/me ")
    )


def _is_deployed_readiness_admin_probe(entry: dict[str, object]) -> bool:
    return (
        str(entry.get("method", "")) == "GET"
        and str(entry.get("path", "")) == "/api/admin/system/health"
        and str(entry.get("message", "")).startswith(
            "Auth rejected: GET /api/admin/system/health ",
        )
    )


def _load_recent_entries(
    log_file: Path,
    minutes: int,
    *,
    exclude_synthetic: bool,
) -> list[dict[str, object]]:
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    results: list[dict[str, object]] = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue

            if ts < cutoff:
                continue

            if exclude_synthetic and (
                _is_synthetic_entry(entry)
                or _is_expected_anonymous_auth_probe(entry)
                or _is_deployed_readiness_admin_probe(entry)
            ):
                continue

            results.append({"ts": ts, "entry": entry})
    return results


def _summarize_grouped(entries: list[dict[str, object]], *, minutes: int, limit: int) -> int:
    groups: dict[tuple[str, str, str, str, str, str, str], dict[str, object]] = {}
    for item in entries:
        entry = item["entry"]
        assert isinstance(entry, dict)
        key = (
            sanitize(str(entry.get("level", "")), 10),
            sanitize(str(entry.get("logger", "")), 80),
            sanitize(str(entry.get("message", "")), MSG_MAX),
            sanitize(str(entry.get("path", "")), 150),
            sanitize(str(entry.get("method", "")), 10),
            str(entry.get("status_code", ""))[:5],
            sanitize(str(entry.get("error_kind", "")), 40),
        )
        bucket = groups.setdefault(
            key,
            {
                "count": 0,
                "latest": item["ts"],
                "request_ids": [],
            },
        )
        bucket["count"] = int(bucket["count"]) + 1
        latest = bucket["latest"]
        if (
            isinstance(latest, datetime)
            and isinstance(item["ts"], datetime)
            and item["ts"] > latest
        ):
            bucket["latest"] = item["ts"]
        request_id = sanitize(str(entry.get("request_id", "")), 80)
        if request_id:
            request_ids = bucket["request_ids"]
            assert isinstance(request_ids, list)
            if request_id not in request_ids and len(request_ids) < 3:
                request_ids.append(request_id)

    if not groups:
        print(f"No new errors in the last {minutes} minutes.")
        return 0

    ordered = sorted(
        groups.items(),
        key=lambda kv: (
            int(kv[1]["count"]),
            kv[1]["latest"],
        ),
        reverse=True,
    )
    print(f"=== {len(ordered)} error group(s) in the last {minutes} minutes ===\n")
    for idx, (key, bucket) in enumerate(ordered[: max(1, limit)], 1):
        level, logger, message, path, method, status_code, error_kind = key
        latest = bucket["latest"]
        request_ids = bucket["request_ids"]
        print(f"--- Group {idx} ---")
        latest_text = latest.strftime("%H:%M:%S") if isinstance(latest, datetime) else latest
        print(f"  latest: {latest_text}")
        print(f"  count: {bucket['count']}")
        print(f"  level: {level}")
        print(f"  logger: {logger}")
        print(f"  message: {message}")
        if path:
            print(f"  path: {path}")
        if method:
            print(f"  method: {method}")
        if status_code:
            print(f"  status_code: {status_code}")
        if error_kind:
            print(f"  error_kind: {error_kind}")
        if request_ids:
            print(f"  request_ids: {', '.join(request_ids)}")
        print()
    return len(ordered)


def _summarize_user_facing(entries: list[dict[str, object]], *, minutes: int, limit: int) -> int:
    groups: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for item in entries:
        entry = item["entry"]
        assert isinstance(entry, dict)
        method = str(entry.get("method", ""))
        error_kind = str(entry.get("error_kind", ""))
        if method != "CLIENT" and error_kind not in {"api_error", "client_runtime"}:
            continue
        key = (
            sanitize(error_kind or "client_runtime", 40),
            sanitize(str(entry.get("message", "")), MSG_MAX),
            sanitize(str(entry.get("path", "")), 150),
            sanitize(str(entry.get("api_path", "")), 150),
            str(entry.get("status_code", ""))[:5],
        )
        bucket = groups.setdefault(
            key,
            {
                "count": 0,
                "latest": item["ts"],
                "request_ids": [],
            },
        )
        bucket["count"] = int(bucket["count"]) + 1
        latest = bucket["latest"]
        if (
            isinstance(latest, datetime)
            and isinstance(item["ts"], datetime)
            and item["ts"] > latest
        ):
            bucket["latest"] = item["ts"]
        request_id = sanitize(str(entry.get("request_id", "")), 80)
        if request_id:
            request_ids = bucket["request_ids"]
            assert isinstance(request_ids, list)
            if request_id not in request_ids and len(request_ids) < 3:
                request_ids.append(request_id)

    if not groups:
        print(f"No user-facing errors in the last {minutes} minutes.")
        return 0

    ordered = sorted(
        groups.items(),
        key=lambda kv: (
            int(kv[1]["count"]),
            kv[1]["latest"],
        ),
        reverse=True,
    )
    print(f"=== {len(ordered)} user-facing error group(s) in the last {minutes} minutes ===\n")
    for idx, (key, bucket) in enumerate(ordered[: max(1, limit)], 1):
        error_kind, message, path, api_path, status_code = key
        latest = bucket["latest"]
        request_ids = bucket["request_ids"]
        print(f"--- Group {idx} ---")
        latest_text = latest.strftime("%H:%M:%S") if isinstance(latest, datetime) else latest
        print(f"  latest: {latest_text}")
        print(f"  count: {bucket['count']}")
        print(f"  error_kind: {error_kind}")
        print(f"  message: {message}")
        if path:
            print(f"  path: {path}")
        if api_path:
            print(f"  api_path: {api_path}")
        if status_code:
            print(f"  status_code: {status_code}")
        if request_ids:
            print(f"  request_ids: {', '.join(request_ids)}")
        print()
    return len(ordered)


def main() -> int:
    args = parse_args()
    minutes = max(1, int(args.minutes))
    limit = max(1, int(args.limit))
    log_file = args.log_file or _default_log_file()

    if not log_file.exists():
        print(f"No error log found at {log_file}")
        return 2 if args.require_log else 0

    entries = _load_recent_entries(
        log_file,
        minutes,
        exclude_synthetic=args.exclude_synthetic,
    )
    if args.user_facing:
        _summarize_user_facing(entries, minutes=minutes, limit=limit)
        return 0

    if args.grouped:
        _summarize_grouped(entries, minutes=minutes, limit=limit)
        return 0

    results: list[dict[str, str]] = []
    for item in entries:
        ts = item["ts"]
        entry = item["entry"]
        assert isinstance(ts, datetime)
        assert isinstance(entry, dict)
        msg = str(entry.get("message", ""))
        summary: dict[str, str] = {
            "time": ts.strftime("%H:%M:%S"),
            "level": sanitize(str(entry.get("level", "")), 10),
            "logger": sanitize(str(entry.get("logger", "")), 80),
            "message": sanitize(msg, MSG_MAX),
        }
        if entry.get("path"):
            summary["path"] = sanitize(str(entry["path"]), 150)
        if entry.get("api_path"):
            summary["api_path"] = sanitize(str(entry["api_path"]), 150)
        if entry.get("method"):
            summary["method"] = sanitize(str(entry["method"]), 10)
        if entry.get("status_code"):
            summary["status_code"] = str(entry["status_code"])[:5]
        if entry.get("request_id"):
            summary["request_id"] = sanitize(str(entry["request_id"]), 80)
        if entry.get("error_kind"):
            summary["error_kind"] = sanitize(str(entry["error_kind"]), 40)
        if entry.get("traceback"):
            summary["traceback_tail"] = sanitize(
                str(entry["traceback"])[-TRACEBACK_MAX:],
                TRACEBACK_MAX,
            )
        if entry.get("client_stack"):
            summary["client_stack_tail"] = sanitize(
                str(entry["client_stack"])[-TRACEBACK_MAX:],
                TRACEBACK_MAX,
            )
        results.append(summary)

    if not results:
        print(f"No new errors in the last {minutes} minutes.")
        return 0

    print(f"=== {len(results)} error(s) in the last {minutes} minutes ===\n")
    for i, r in enumerate(results, 1):
        print(f"--- Error {i} ---")
        for key, val in r.items():
            print(f"  {key}: {val}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
