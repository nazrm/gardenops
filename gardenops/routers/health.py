"""Public liveness and admin diagnostics endpoints."""

import hmac
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import gardenops.db as db
from gardenops.security import validate_request_auth

logger = logging.getLogger(__name__)
router = APIRouter()
MIN_REVIEW_TOKEN_LENGTH = 32

_BACKUPS_DIR = Path(__file__).parent.parent.parent / "backups"
_LAST_BACKUP_FILE = _BACKUPS_DIR / ".last_backup"

# Cached integrity results (seeded by startup gate, refreshed on TTL)
_cache: dict[str, object] = {}
_cache_ts: float = 0.0
_CACHE_TTL: float = 60.0
_start_time: float = time.monotonic()


def seed_cache(
    *,
    db_ok: bool,
    quick_check_detail: str,
    fk_violations: int,
) -> None:
    """Called by startup integrity gate to seed initial cache."""
    global _cache, _cache_ts, _start_time
    _cache = {
        "db_quick_check": quick_check_detail,
        "db_ok": db_ok,
        "fk_violations": fk_violations,
    }
    _cache_ts = time.monotonic()
    _start_time = time.monotonic()


def _get_cached_checks() -> dict:
    global _cache, _cache_ts
    now = time.monotonic()
    if now - _cache_ts < _CACHE_TTL and _cache:
        return dict(_cache)

    conn = db.get_db()
    try:
        try:
            qc_detail = db.db_quick_check(conn)
            db_ok = qc_detail == "ok"
        except Exception as e:
            db_ok = False
            qc_detail = str(e)[:200]

        try:
            fk_violations = len(db.db_foreign_key_violations(conn))
        except Exception:
            fk_violations = -1
    finally:
        db.return_db(conn)

    result: dict[str, object] = {
        "db_quick_check": "ok" if db_ok else qc_detail,
        "db_ok": db_ok,
        "fk_violations": fk_violations,
    }
    _cache = result
    _cache_ts = now
    return dict(result)


def _get_status(checks: dict) -> str:
    if not checks["db_ok"]:
        return "corrupt"
    if checks["fk_violations"] > 0:
        return "degraded"
    return "ok"


def _get_last_backup() -> str | None:
    try:
        if _LAST_BACKUP_FILE.exists():
            return _LAST_BACKUP_FILE.read_text().strip()
    except Exception:
        pass
    return None


def _build_diagnostics_payload(checks: dict[str, object]) -> dict[str, object]:
    status = _get_status(checks)

    payload = {
        "status": status,
        "db_quick_check": checks["db_quick_check"],
        "fk_violations": checks["fk_violations"],
        "last_backup": _get_last_backup(),
        "uptime_seconds": round(time.monotonic() - _start_time),
    }
    conn = db.get_db()
    try:
        payload["table_count"] = db.db_table_count(conn)
    finally:
        db.return_db(conn)

    # Taillight log shipper health (if active)
    for h in logging.getLogger().handlers:
        if hasattr(h, "dropped") and hasattr(h, "send_failed"):
            payload["taillight"] = {
                "dropped": h.dropped,
                "send_failed": h.send_failed,
            }
            break

    return payload


def _review_admin_health_token() -> str:
    token = os.environ.get("DEPLOYED_READINESS_ADMIN_BEARER_TOKEN", "").strip()
    return token if len(token) >= MIN_REVIEW_TOKEN_LENGTH else ""


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _has_review_admin_health_access(request: Request) -> bool:
    configured = _review_admin_health_token()
    provided = _bearer_token(request)
    return bool(configured and provided and hmac.compare_digest(provided, configured))


def _require_admin_health_access(request: Request) -> None:
    if _has_review_admin_health_access(request):
        return
    context = validate_request_auth(request)
    if context.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("/health")
def health() -> dict[str, str]:
    checks = _get_cached_checks()
    return {"status": _get_status(checks)}


@router.get("/admin/system/health")
def admin_system_health(request: Request) -> dict[str, object]:
    _require_admin_health_access(request)
    return _build_diagnostics_payload(_get_cached_checks())
