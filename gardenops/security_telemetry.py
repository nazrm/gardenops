from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from gardenops.branding import app_user_agent
from gardenops.db import DbConn, current_timestamp_ms, get_db, return_db
from gardenops.redaction import redact_external_log_text, redact_sensitive_text

logger = logging.getLogger(__name__)

_THREAD_LOCK = threading.Lock()
_DRAIN_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()
_EXPORT_THREAD: threading.Thread | None = None
_MAX_ERROR_LENGTH = 500
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _webhook_url() -> str:
    return (
        os.environ.get("SECURITY_TELEMETRY_WEBHOOK_URL", "").strip()
        or os.environ.get("TAILLIGHT_URL", "").strip()
    )


def _delivery_format() -> str:
    configured = os.environ.get("SECURITY_TELEMETRY_WEBHOOK_FORMAT", "").strip().lower()
    if configured in {"raw_json", "taillight_logs"}:
        return configured
    if (
        not os.environ.get("SECURITY_TELEMETRY_WEBHOOK_URL", "").strip()
        and os.environ.get("TAILLIGHT_URL", "").strip()
    ):
        return "taillight_logs"
    return "raw_json"


def _bearer_token() -> str:
    configured = os.environ.get("SECURITY_TELEMETRY_BEARER_TOKEN", "").strip()
    if configured:
        return configured
    if _delivery_format() == "taillight_logs":
        return os.environ.get("TAILLIGHT_API_KEY", "").strip()
    return ""


def _is_safe_webhook_url(url: str, *, allow_http: bool = False) -> bool:
    """Validate webhook URL to prevent SSRF against internal services."""
    if not url:
        return False
    parsed = urlsplit(url)
    allowed_schemes = {"https"} if not allow_http else {"http", "https"}
    if parsed.scheme not in allowed_schemes:
        return False
    hostname = parsed.hostname or ""
    if not hostname:
        return False
    # Block obviously internal hostnames
    if hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:  # noqa: S104
        return False
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        return False
    # Resolve hostname and check for private/reserved IPs
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            addr = ipaddress.ip_address(info[4][0])
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
                return False
    except (
        socket.gaierror,
        ValueError,
    ):
        # If we can't resolve, block it
        return False
    return True


def security_telemetry_enabled() -> bool:
    return bool(_webhook_url())


def _background_export_enabled() -> bool:
    default = os.environ.get("APP_ENV", "").strip().lower() != "test"
    return _env_bool("SECURITY_TELEMETRY_BACKGROUND_EXPORT", default)


def _poll_seconds() -> int:
    return max(1, _env_int("SECURITY_TELEMETRY_POLL_SECONDS", 10))


def _snapshot_interval_seconds() -> int:
    return max(30, _env_int("SECURITY_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS", 60))


def _batch_size() -> int:
    return max(1, min(_env_int("SECURITY_TELEMETRY_BATCH_SIZE", 20), 200))


def _timeout_seconds() -> int:
    return max(1, _env_int("SECURITY_TELEMETRY_TIMEOUT_SECONDS", 5))


def _backoff_seconds(attempt_count: int) -> int:
    exponent = max(0, min(int(attempt_count), 6))
    return min(300, 5 * (2**exponent))


def _instance_label() -> str:
    return (
        os.environ.get("SECURITY_TELEMETRY_INSTANCE", "").strip()
        or os.environ.get("HOSTNAME", "").strip()
        or socket.gethostname()
    )


def _privacy_mode() -> str:
    raw = os.environ.get("SECURITY_TELEMETRY_PRIVACY_MODE", "minimized").strip().lower()
    return raw if raw in {"raw", "minimized"} else "minimized"


def _privacy_salt() -> str:
    return os.environ.get("SECURITY_TELEMETRY_PRIVACY_SALT", "gardenops-security-telemetry")


def _hash_identifier(kind: str, value: object) -> str | None:
    if value is None or value == "":
        return None
    digest = sha256(f"{_privacy_salt()}:{kind}:{value}".encode()).hexdigest()
    return f"sha256:{digest[:24]}"


_IDENTIFIER_KEYS = {
    "actor_user_id",
    "actor_username",
    "client_ip",
    "garden_id",
    "ip",
    "remote_host",
    "scope_id",
    "top_garden_scope",
    "top_ip_scope",
    "top_user_scope",
    "user_id",
    "username",
}

_PATH_KEYS = {"path", "request_path", "url"}
_TEXT_KEYS = {"detail", "error", "last_error", "message", "msg"}


def _minimize_payload(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower()
    if isinstance(value, dict):
        return {str(k): _minimize_payload(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_minimize_payload(item, key=key) for item in value]
    if normalized_key in _IDENTIFIER_KEYS:
        return _hash_identifier(normalized_key, value)
    if normalized_key in _PATH_KEYS:
        return redact_external_log_text(value, 300)
    if normalized_key in _TEXT_KEYS:
        return redact_sensitive_text(value, 300)
    if isinstance(value, str):
        return redact_sensitive_text(value, 500)
    return value


def _truncate_error(err: object) -> str:
    text = str(err).strip() or "unknown error"
    if len(text) <= _MAX_ERROR_LENGTH:
        return text
    return text[: _MAX_ERROR_LENGTH - 3] + "..."


def _destination_label() -> str:
    raw = _webhook_url()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw[:120]
    return f"{parsed.scheme}://{parsed.netloc}"


def _timestamp_from_payload(envelope: dict[str, Any]) -> str:
    emitted_at_ms = envelope.get("emitted_at_ms")
    if isinstance(emitted_at_ms, int | float) and emitted_at_ms > 0:
        return datetime.fromtimestamp(emitted_at_ms / 1000, UTC).isoformat()
    return datetime.now(UTC).isoformat()


def _taillight_log_body(payload_json: str) -> bytes:
    try:
        envelope = json.loads(payload_json)
    except json.JSONDecodeError:
        envelope = {
            "schema_version": 1,
            "source": "gardenops",
            "event_kind": "invalid_security_telemetry_payload",
            "payload": {"size_bytes": len(payload_json.encode("utf-8"))},
        }
    if not isinstance(envelope, dict):
        envelope = {
            "schema_version": 1,
            "source": "gardenops",
            "event_kind": "invalid_security_telemetry_payload",
            "payload": {"payload_type": type(envelope).__name__},
        }
    event_kind = str(envelope.get("event_kind") or "security_telemetry_event")
    log_entry = {
        "timestamp": _timestamp_from_payload(envelope),
        "level": "INFO",
        "msg": f"security telemetry: {event_kind}",
        "service": "gardenops",
        "component": "security-telemetry",
        "host": _instance_label(),
        "attrs": envelope,
    }
    return json.dumps({"logs": [log_entry]}, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (key,),
    ).fetchone()
    if not row:
        return default
    return str(row["value"] or default)


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


def _set_status(
    conn,
    *,
    last_attempt_at_ms: int | None = None,
    last_success_at_ms: int | None = None,
    last_error: str | None = None,
) -> None:
    if last_attempt_at_ms is not None:
        _set_setting(conn, "security_telemetry_last_attempt_at_ms", str(int(last_attempt_at_ms)))
    if last_success_at_ms is not None:
        _set_setting(conn, "security_telemetry_last_success_at_ms", str(int(last_success_at_ms)))
    if last_error is not None:
        _set_setting(conn, "security_telemetry_last_error", last_error)


def _enqueue_with_conn(
    conn,
    *,
    event_kind: str,
    payload: dict[str, Any],
    created_at_ms: int | None = None,
) -> int | None:
    safe_kind = event_kind.strip()
    if not safe_kind:
        return None
    created_ms = int(created_at_ms or current_timestamp_ms())
    envelope = {
        "schema_version": 1,
        "source": "gardenops",
        "event_kind": safe_kind,
        "emitted_at_ms": created_ms,
        "app_env": os.environ.get("APP_ENV", "").strip() or "unknown",
        "instance": _instance_label(),
        "privacy_mode": _privacy_mode(),
        "payload": _minimize_payload(payload) if _privacy_mode() == "minimized" else payload,
    }
    row = conn.execute(
        """
        INSERT INTO security_telemetry_outbox (
            event_kind,
            payload_json,
            created_at_ms,
            available_at_ms,
            attempt_count,
            last_error
        )
        VALUES (%s, %s, %s, %s, 0, '') RETURNING id
        """,
        (
            safe_kind,
            json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            created_ms,
            created_ms,
        ),
    ).fetchone()
    return int(row["id"])


def enqueue_security_telemetry(
    event_kind: str,
    payload: dict[str, Any],
    *,
    created_at_ms: int | None = None,
    db: DbConn | None = None,
) -> None:
    if not security_telemetry_enabled():
        return
    if db is not None:
        _enqueue_with_conn(
            db,
            event_kind=event_kind,
            payload=payload,
            created_at_ms=created_at_ms,
        )
        return
    conn = get_db()
    try:
        _enqueue_with_conn(
            conn,
            event_kind=event_kind,
            payload=payload,
            created_at_ms=created_at_ms,
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to enqueue security telemetry event %s", event_kind)
    finally:
        return_db(conn)


def _pending_snapshot_exists(conn) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM security_telemetry_outbox
        WHERE event_kind = 'security_metrics_snapshot'
        LIMIT 1
        """,
    ).fetchone()
    return bool(row)


def ensure_security_metrics_snapshot_enqueued(*, force: bool = False) -> bool:
    if not security_telemetry_enabled():
        return False
    from gardenops.security_metrics import security_alerts_snapshot, security_metrics_snapshot

    conn = get_db()
    try:
        now_ms = current_timestamp_ms()
        if not force:
            if _pending_snapshot_exists(conn):
                return False
            last_snapshot_ms = int(
                _get_setting(conn, "security_telemetry_last_snapshot_enqueued_at_ms", "0") or 0,
            )
            if last_snapshot_ms > 0:
                elapsed_ms = now_ms - last_snapshot_ms
                if elapsed_ms < (_snapshot_interval_seconds() * 1000):
                    return False
        _enqueue_with_conn(
            conn,
            event_kind="security_metrics_snapshot",
            payload={
                "metrics": security_metrics_snapshot(),
                "alerts": security_alerts_snapshot(),
            },
            created_at_ms=now_ms,
        )
        _set_setting(conn, "security_telemetry_last_snapshot_enqueued_at_ms", str(now_ms))
        conn.commit()
        return True
    except Exception:
        logger.exception("Failed to enqueue security telemetry metrics snapshot")
        return False
    finally:
        return_db(conn)


def _deliver_payload(payload_json: str) -> None:
    url = _webhook_url()
    delivery_format = _delivery_format()
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    allow_http = app_env == "test"
    if not allow_http and urlsplit(url).scheme.lower() != "https":
        raise RuntimeError("Security telemetry webhook must use https outside test mode")
    # SSRF validation is skipped in test mode so focused tests can use local/mock sinks.
    if not allow_http and not _is_safe_webhook_url(url, allow_http=False):
        raise RuntimeError(f"Webhook URL blocked by SSRF protection: {_destination_label()}")
    body = (
        _taillight_log_body(payload_json)
        if delivery_format == "taillight_logs"
        else payload_json.encode("utf-8")
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": app_user_agent("security-telemetry"),
        },
        method="POST",
    )
    bearer_token = _bearer_token()
    if bearer_token:
        request.add_header("Authorization", f"Bearer {bearer_token}")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=_timeout_seconds()) as response:  # noqa: S310
            status = getattr(response, "status", None) or response.getcode()
            if int(status) < 200 or int(status) >= 300:
                raise RuntimeError(f"Security telemetry sink returned HTTP {status}")
            response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in _REDIRECT_STATUS_CODES:
            raise RuntimeError(f"Security telemetry sink redirected with HTTP {exc.code}") from exc
        raise RuntimeError(f"Security telemetry sink returned HTTP {exc.code}") from exc


def drain_security_telemetry_once(*, limit: int | None = None) -> dict[str, int]:
    with _DRAIN_LOCK:
        conn = get_db()
        try:
            if not security_telemetry_enabled():
                pending_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM security_telemetry_outbox",
                ).fetchone()
                assert pending_row is not None
                pending = int(pending_row["c"])
                return {"delivered": 0, "failed": 0, "pending": pending}

            safe_limit = max(1, min(limit or _batch_size(), 200))
            now_ms = current_timestamp_ms()
            rows = conn.execute(
                """
                SELECT id, payload_json, attempt_count
                FROM security_telemetry_outbox
                WHERE available_at_ms <= %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (now_ms, safe_limit),
            ).fetchall()
        finally:
            return_db(conn)

        delivered = 0
        failed = 0
        for row in rows:
            attempt_conn = get_db()
            try:
                _set_status(attempt_conn, last_attempt_at_ms=current_timestamp_ms())
                attempt_conn.commit()
            finally:
                return_db(attempt_conn)

            try:
                _deliver_payload(str(row["payload_json"]))
                result_conn = get_db()
                try:
                    result_conn.execute(
                        "DELETE FROM security_telemetry_outbox WHERE id = %s",
                        (int(row["id"]),),
                    )
                    _set_status(
                        result_conn,
                        last_success_at_ms=current_timestamp_ms(),
                        last_error="",
                    )
                    result_conn.commit()
                finally:
                    return_db(result_conn)
                delivered += 1
            except Exception as exc:
                failed += 1
                attempt_count = int(row["attempt_count"]) + 1
                error_text = _truncate_error(exc)
                result_conn = get_db()
                try:
                    result_conn.execute(
                        """
                        UPDATE security_telemetry_outbox
                        SET attempt_count = %s,
                            available_at_ms = %s,
                            last_error = %s
                        WHERE id = %s
                        """,
                        (
                            attempt_count,
                            current_timestamp_ms() + (_backoff_seconds(attempt_count) * 1000),
                            error_text,
                            int(row["id"]),
                        ),
                    )
                    _set_status(result_conn, last_error=error_text)
                    result_conn.commit()
                finally:
                    return_db(result_conn)

        conn = get_db()
        try:
            pending_row = conn.execute(
                "SELECT COUNT(*) AS c FROM security_telemetry_outbox",
            ).fetchone()
            assert pending_row is not None
            pending = int(pending_row["c"])
            return {"delivered": delivered, "failed": failed, "pending": pending}
        finally:
            return_db(conn)


def security_telemetry_status() -> dict[str, object]:
    conn = get_db()
    try:
        pending_row = conn.execute(
            """
            SELECT
                COUNT(*) AS pending_count,
                MIN(created_at_ms) AS oldest_pending_at_ms
            FROM security_telemetry_outbox
            """,
        ).fetchone()
        pending_count = int(pending_row["pending_count"] if pending_row else 0)
        oldest_pending_at_ms = (
            int(pending_row["oldest_pending_at_ms"])
            if pending_row and pending_row["oldest_pending_at_ms"] is not None
            else None
        )
        last_attempt_raw = _get_setting(conn, "security_telemetry_last_attempt_at_ms", "")
        last_success_raw = _get_setting(conn, "security_telemetry_last_success_at_ms", "")
        last_error = _get_setting(conn, "security_telemetry_last_error", "")
        return {
            "enabled": security_telemetry_enabled(),
            "destination": _destination_label(),
            "delivery_format": _delivery_format() if security_telemetry_enabled() else "",
            "pending_count": pending_count,
            "oldest_pending_at_ms": oldest_pending_at_ms,
            "last_attempt_at_ms": int(last_attempt_raw) if last_attempt_raw.strip() else None,
            "last_success_at_ms": int(last_success_raw) if last_success_raw.strip() else None,
            "last_error": last_error,
            "snapshot_interval_seconds": _snapshot_interval_seconds(),
            "poll_interval_seconds": _poll_seconds(),
        }
    finally:
        return_db(conn)


def _exporter_loop() -> None:
    while not _STOP_EVENT.wait(_poll_seconds()):
        if not security_telemetry_enabled():
            continue
        try:
            ensure_security_metrics_snapshot_enqueued()
            drain_security_telemetry_once()
        except Exception:
            logger.exception("Security telemetry exporter loop failed")


def start_security_telemetry_exporter() -> None:
    if not security_telemetry_enabled() or not _background_export_enabled():
        return
    global _EXPORT_THREAD  # noqa: PLW0603
    with _THREAD_LOCK:
        if _EXPORT_THREAD and _EXPORT_THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _EXPORT_THREAD = threading.Thread(
            target=_exporter_loop,
            name="security-telemetry-exporter",
            daemon=True,
        )
        _EXPORT_THREAD.start()


def stop_security_telemetry_exporter() -> None:
    global _EXPORT_THREAD  # noqa: PLW0603
    _STOP_EVENT.set()
    with _THREAD_LOCK:
        if _EXPORT_THREAD and _EXPORT_THREAD.is_alive():
            _EXPORT_THREAD.join(timeout=2)
        _EXPORT_THREAD = None


def reset_security_telemetry() -> None:
    stop_security_telemetry_exporter()
    conn = get_db()
    try:
        conn.execute("DELETE FROM security_telemetry_outbox")
        conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'security_telemetry_last_attempt_at_ms',
                'security_telemetry_last_success_at_ms',
                'security_telemetry_last_error',
                'security_telemetry_last_snapshot_enqueued_at_ms'
            )
            """,
        )
        conn.commit()
    finally:
        return_db(conn)
