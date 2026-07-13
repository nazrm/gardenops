import asyncio
import csv
import io
import ipaddress
import json
import logging
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

from fastapi import Body, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import Field  # noqa: E402
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from gardenops.audit import reserve_mutation_audit_event, write_audit_event  # noqa: E402
from gardenops.branding import app_name, app_slug, app_user_agent  # noqa: E402
from gardenops.constants import (  # noqa: E402
    GRID_COLS,
    GRID_ROWS,
    HOUSE_DEFAULT_COL,
    HOUSE_DEFAULT_HEIGHT,
    HOUSE_DEFAULT_ROW,
    HOUSE_DEFAULT_WIDTH,
    MAP_DEFAULT_NORTH_DEGREES,
)
from gardenops.db import (  # noqa: E402
    DB,
    DbConn,
    close_pool,
    db_foreign_key_violations,
    db_quick_check,
    default_shademap_state,
    get_db,
    init_db,
    return_db,
)
from gardenops.events import notify_garden_modified  # noqa: E402
from gardenops.feature_gates import feature_allowed, feature_for_route  # noqa: E402
from gardenops.incident_controls import is_emergency_read_only  # noqa: E402
from gardenops.models import (  # noqa: E402
    ImportBody,
    LayoutStateBody,
    SnapshotBody,
    StrictBaseModel,
)
from gardenops.observability import (  # noqa: E402
    RequestContextFilter,
    bind_request_context,
    generate_request_id,
    normalize_request_id,
    reset_request_context,
)
from gardenops.rate_limit import enforce_rate_limit, ensure_backend_ready, env_int  # noqa: E402
from gardenops.redaction import redact_external_log_text, redact_sensitive_text  # noqa: E402
from gardenops.request_body import read_and_cache_body_limited, read_body_limited  # noqa: E402
from gardenops.router_helpers import generate_public_id as _generate_public_id  # noqa: E402
from gardenops.router_helpers import (  # noqa: E402
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.routers.ai import router as ai_router  # noqa: E402
from gardenops.routers.attention import router as attention_router  # noqa: E402
from gardenops.routers.auth import (  # noqa: E402
    enforce_destructive_admin_controls,
)
from gardenops.routers.auth import router as auth_router  # noqa: E402
from gardenops.routers.calendar import feed_router as calendar_feed_router  # noqa: E402
from gardenops.routers.calendar import router as calendar_router  # noqa: E402
from gardenops.routers.exports import router as exports_router  # noqa: E402
from gardenops.routers.external import router as external_router  # noqa: E402
from gardenops.routers.gardens import router as gardens_router  # noqa: E402
from gardenops.routers.harvest import router as harvest_router  # noqa: E402
from gardenops.routers.health import router as health_router  # noqa: E402
from gardenops.routers.inventory import router as inventory_router  # noqa: E402
from gardenops.routers.issues import router as issues_router  # noqa: E402
from gardenops.routers.journal import router as journal_router  # noqa: E402
from gardenops.routers.map_objects import (  # noqa: E402
    replace_map_objects,
    snapshot_map_objects,
)
from gardenops.routers.map_objects import (  # noqa: E402
    router as map_objects_router,
)
from gardenops.routers.media import router as media_router  # noqa: E402
from gardenops.routers.notifications import router as notifications_router  # noqa: E402
from gardenops.routers.planner import router as planner_router  # noqa: E402
from gardenops.routers.plants import (  # noqa: E402
    PLANT_ASSIGNMENTS_COLUMN,
    PLANT_CSV_EXPORT_COLUMNS,
    _assignment_rows_for_plant,
    _plant_scope_sql,
    _serialize_plot_assignments,
)
from gardenops.routers.plants import (  # noqa: E402
    router as plants_router,
)
from gardenops.routers.plots import router as plots_router  # noqa: E402
from gardenops.routers.procurement import router as procurement_router  # noqa: E402
from gardenops.routers.provider_settings import router as provider_settings_router  # noqa: E402
from gardenops.routers.saved_views import router as saved_views_router  # noqa: E402
from gardenops.routers.shademap import (  # noqa: E402
    asset_router as shademap_asset_router,
)
from gardenops.routers.shademap import (  # noqa: E402
    get_shademap_calibration,
    get_shademap_state,
    list_shademap_obstacles,
    replace_shademap_obstacles,
    set_shademap_calibration,
    set_shademap_state,
)
from gardenops.routers.shademap import (  # noqa: E402
    router as shademap_router,
)
from gardenops.routers.statistics import router as statistics_router  # noqa: E402
from gardenops.routers.tasks import router as tasks_router  # noqa: E402
from gardenops.routers.weather import router as weather_router  # noqa: E402
from gardenops.routers.workflows import router as workflows_router  # noqa: E402
from gardenops.security import (  # noqa: E402
    admin_mfa_required,
    auth_mode,
    create_user,
    csrf_token_matches_context,
    ensure_bootstrap_user_from_env,
    has_write_access,
    is_auth_required,
    resolve_garden_context,
    resolve_request_auth_context,
    session_auth_enabled,
    session_cookie_name,
    session_cookie_samesite,
    session_cookie_secure,
    validate_request_auth,
    warn_csrf_secret_not_configured,
)
from gardenops.security_metrics import record_security_event  # noqa: E402
from gardenops.security_telemetry import (  # noqa: E402
    security_telemetry_enabled,
    start_security_telemetry_exporter,
    stop_security_telemetry_exporter,
)
from gardenops.services.garden_layout_lock import lock_garden_layout  # noqa: E402
from gardenops.services.media_store import unlink_storage_keys  # noqa: E402
from gardenops.services.notification_service import (  # noqa: E402
    acquire_notification_scheduler_lease,
    notification_scheduler_enabled,
    notification_scheduler_owner_id,
    notification_scheduler_poll_seconds,
    release_notification_scheduler_lease,
    run_notification_maintenance_once,
)
from gardenops.services.plot_references import delete_plots_for_replacement  # noqa: E402

ROOT = Path(__file__).parent.parent
DIST = ROOT / "frontend" / "dist"
FRONTEND_PACKAGE_JSON = ROOT / "frontend" / "package.json"
LOGS_DIR = Path(os.environ.get("GARDENOPS_LOGS_DIR", "") or ROOT / "logs")
logger = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


_LOG_MSG_MAX_LEN = 500
_LOG_TRACEBACK_MAX_LEN = 2000
_MFA_SECRET_PLACEHOLDERS = frozenset(
    {
        "generate-at-least-32-random-characters",
        "<generate-at-least-32-random-characters>",
    },
)
_TAILLOG_MAX_QUEUE = 1000
_TAILLOG_TIMEOUT_SECONDS = 3
_TAILLOG_SKIP_FIELDS = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


class TaillightLogHandler(logging.Handler):
    """Small dependency-free handler for Taillight-compatible log ingest."""

    dropped: int
    send_failed: int

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        service: str,
        component: str,
    ) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.api_key = api_key
        self.service = service
        self.component = component
        self.dropped = 0
        self.send_failed = 0
        self._closed = threading.Event()
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue(
            maxsize=_TAILLOG_MAX_QUEUE,
        )
        self._worker = threading.Thread(
            target=self._worker_main,
            name="taillight-log-shipper",
            daemon=True,
        )
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed.is_set():
            self.dropped += 1
            return
        try:
            self._queue.put_nowait(self._build_entry(record))
        except queue.Full:
            self.dropped += 1
        except Exception:
            self.send_failed += 1
            if logging.raiseExceptions:
                self.handleError(record)

    def shutdown(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            self.dropped += 1
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(None)
            except queue.Empty:
                pass
        self._worker.join(timeout=_TAILLOG_TIMEOUT_SECONDS + 1)

    def close(self) -> None:
        self.shutdown()
        super().close()

    def _build_entry(self, record: logging.LogRecord) -> dict[str, object]:
        attrs: dict[str, object] = {}
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _TAILLOG_SKIP_FIELDS:
                continue
            attrs[key] = self._safe_attr(value)
        if record.exc_info:
            attrs["exception"] = _sanitize_log_str(
                logging.Formatter().formatException(record.exc_info),
                _LOG_TRACEBACK_MAX_LEN,
            )
        return {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_external_log_text(record.getMessage(), _LOG_MSG_MAX_LEN),
            "service": self.service,
            "component": self.component,
            "attrs": attrs,
        }

    def _safe_attr(self, value: object) -> object:
        if value is None or isinstance(value, bool | int | float):
            return value
        return redact_external_log_text(str(value), 300)

    def _worker_main(self) -> None:
        while True:
            entry = self._queue.get()
            try:
                if entry is None:
                    return
                self._post_entry(entry)
            except Exception:
                self.send_failed += 1
            finally:
                self._queue.task_done()

    def _post_entry(self, entry: dict[str, object]) -> None:
        body = json.dumps(
            {"logs": [entry]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": app_user_agent("app-logs"),
            },
            method="POST",
        )
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=_TAILLOG_TIMEOUT_SECONDS) as response:  # noqa: S310
                status = getattr(response, "status", None) or response.getcode()
                if int(status) < 200 or int(status) >= 300:
                    raise RuntimeError(f"Taillight log sink returned HTTP {status}")
                response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Taillight log sink returned HTTP {exc.code}") from exc


class AdminActionBody(StrictBaseModel):
    action_reason: str = Field(default="", max_length=400)


def _sanitize_log_str(value: str, max_len: int = _LOG_MSG_MAX_LEN) -> str:
    """Truncate and strip control characters from a log string."""
    return redact_sensitive_text(value, max_len)


def _client_report_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = urlsplit(raw).path or "/"
    except ValueError:
        path = raw.split("#", 1)[0].split("%s", 1)[0]
    return _sanitize_log_str(path[:200], 200)


def _setup_error_log() -> None:
    """Configure a rotating JSONL error log at logs/errors.jsonl."""
    LOGS_DIR.mkdir(exist_ok=True)
    from logging.handlers import RotatingFileHandler

    context_filter = RequestContextFilter()
    handler = RotatingFileHandler(
        LOGS_DIR / "errors.jsonl",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.WARNING)
    handler.addFilter(context_filter)

    class JsonlFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            entry: dict[str, object] = {
                "ts": datetime.fromtimestamp(
                    record.created,
                    tz=UTC,
                ).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": _sanitize_log_str(record.getMessage()),
            }
            if record.exc_info and record.exc_info[1]:
                import traceback

                raw_tb = "".join(
                    traceback.format_exception(*record.exc_info),
                )
                entry["traceback"] = _sanitize_log_str(
                    raw_tb,
                    _LOG_TRACEBACK_MAX_LEN,
                )
            if hasattr(record, "path"):
                entry["path"] = _sanitize_log_str(
                    str(record.path),
                    200,  # type: ignore[attr-defined]
                )
            if hasattr(record, "method"):
                entry["method"] = _sanitize_log_str(
                    str(record.method),
                    10,  # type: ignore[attr-defined]
                )
            if hasattr(record, "status_code"):
                entry["status_code"] = record.status_code  # type: ignore[attr-defined]
            if hasattr(record, "request_id"):
                entry["request_id"] = _sanitize_log_str(
                    str(record.request_id),
                    80,  # type: ignore[attr-defined]
                )
            if hasattr(record, "report_request_id"):
                entry["report_request_id"] = _sanitize_log_str(
                    str(record.report_request_id),
                    80,  # type: ignore[attr-defined]
                )
            if hasattr(record, "api_path"):
                entry["api_path"] = _sanitize_log_str(
                    str(record.api_path),
                    200,  # type: ignore[attr-defined]
                )
            if hasattr(record, "error_kind"):
                entry["error_kind"] = _sanitize_log_str(
                    str(record.error_kind),
                    40,  # type: ignore[attr-defined]
                )
            if hasattr(record, "upstream"):
                entry["upstream"] = _sanitize_log_str(
                    str(record.upstream),
                    80,  # type: ignore[attr-defined]
                )
            if hasattr(record, "feature_area"):
                entry["feature_area"] = _sanitize_log_str(
                    str(record.feature_area),
                    80,  # type: ignore[attr-defined]
                )
            if hasattr(record, "garden_id"):
                entry["garden_id"] = record.garden_id  # type: ignore[attr-defined]
            if hasattr(record, "user_id"):
                entry["user_id"] = record.user_id  # type: ignore[attr-defined]
            if hasattr(record, "client_ts"):
                entry["client_ts"] = _sanitize_log_str(
                    str(record.client_ts),
                    40,  # type: ignore[attr-defined]
                )
            if hasattr(record, "client_stack"):
                entry["client_stack"] = _sanitize_log_str(
                    str(record.client_stack),
                    _LOG_TRACEBACK_MAX_LEN,  # type: ignore[attr-defined]
                )
            if hasattr(record, "source"):
                entry["source"] = _sanitize_log_str(
                    str(record.source),
                    200,  # type: ignore[attr-defined]
                )
            if hasattr(record, "client_lineno"):
                entry["client_lineno"] = record.client_lineno  # type: ignore[attr-defined]
            if hasattr(record, "client_colno"):
                entry["client_colno"] = record.client_colno  # type: ignore[attr-defined]
            if hasattr(record, "handled"):
                entry["handled"] = bool(record.handled)  # type: ignore[attr-defined]
            return json.dumps(entry, default=str)

    handler.setFormatter(JsonlFormatter())
    logging.getLogger().addHandler(handler)

    # Ship logs to Taillight-compatible ingest (opt-in via TAILLIGHT_URL env var).
    taillight_url = os.environ.get("TAILLIGHT_URL", "")
    taillight_key = os.environ.get("TAILLIGHT_API_KEY", "")
    if taillight_url:
        tl_handler = TaillightLogHandler(
            endpoint=taillight_url,
            api_key=taillight_key,
            service=app_slug(),
            component="web",
        )
        tl_handler.setLevel(logging.DEBUG)
        tl_handler.addFilter(context_filter)
        logging.getLogger().addHandler(tl_handler)
        # Lower root logger so DEBUG/INFO records reach Taillight.
        # The JSONL error handler still filters to WARNING+ via its own level.
        if logging.getLogger().level > logging.DEBUG:
            logging.getLogger().setLevel(logging.DEBUG)
        # Ensure uvicorn access/error logs propagate to root (and thus Taillight).
        # Uvicorn sets propagate=False on its loggers by default.
        for _uv_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            logging.getLogger(_uv_name).propagate = True


_VERSION_CACHE_LOCK = threading.Lock()
_VERSION_CACHE_TTL_SECONDS = 30.0
_VERSION_CACHE: dict[str, object] = {
    "fetched_at": 0.0,
    "payload": None,
}
_GIT_FILE_FALLBACK_LOGGED = False

DEFAULT_DEV_CORS_ORIGINS = "http://localhost:5173"
DEFAULT_DEV_ALLOWED_HOSTS = "localhost,127.0.0.1,[::1],::1,testserver,testclient"
_VERSION_ADJECTIVES = (
    "Amber",
    "Arctic",
    "Bold",
    "Bright",
    "Copper",
    "Daring",
    "Ember",
    "Fjord",
    "Forest",
    "Golden",
    "Granite",
    "Harbor",
    "Honey",
    "Juniper",
    "Kindled",
    "Lively",
    "Meadow",
    "Moss",
    "North",
    "Oak",
    "Pine",
    "Quiet",
    "River",
    "Sable",
    "Solar",
    "Spruce",
    "Stone",
    "Tidal",
    "Verdant",
    "Wild",
    "Winter",
    "Zephyr",
)
_VERSION_ANIMALS = (
    "Badger",
    "Bee",
    "Falcon",
    "Fox",
    "Heron",
    "Lark",
    "Lynx",
    "Marten",
    "Mongoose",
    "Otter",
    "Owl",
    "Panda",
    "Peregrine",
    "Pika",
    "Raven",
    "Seal",
    "Stag",
    "Swift",
    "Tern",
    "Thrush",
    "Tiger",
    "Toad",
    "Trout",
    "Viper",
    "Weasel",
    "Whale",
    "Wolf",
    "Wren",
    "Yak",
    "Zebra",
    "Auk",
    "Orca",
)


def _base_app_version() -> str:
    try:
        package_meta = json.loads(FRONTEND_PACKAGE_JSON.read_text(encoding="utf-8"))
        package_version = str(package_meta.get("version", "")).strip()
        if package_version:
            return package_version
        display_version = str(package_meta.get("displayVersion", "")).strip()
        if display_version:
            return display_version
    except Exception:
        logger.debug("Failed to read frontend package metadata for app version", exc_info=True)
    return "unknown"


def _repo_codename(seed: str) -> str:
    digest = sha256(seed.encode("utf-8")).hexdigest()
    adjective = _VERSION_ADJECTIVES[int(digest[:8], 16) % len(_VERSION_ADJECTIVES)]
    animal = _VERSION_ANIMALS[int(digest[8:16], 16) % len(_VERSION_ANIMALS)]
    return f"{adjective}{animal}"


def _build_number(last_updated_at_ms: int | None) -> str:
    if not last_updated_at_ms or last_updated_at_ms <= 0:
        return "000000000000"
    return datetime.fromtimestamp(last_updated_at_ms / 1000, UTC).strftime("%y%m%d%H%M%S")


def _git_command(*args: str) -> list[str]:
    return ["git", "-c", f"safe.directory={ROOT}", *args]


def _dirty_tracked_paths() -> list[Path]:
    dirty_paths: set[Path] = set()
    for args in (
        _git_command("diff", "--name-only", "-z"),
        _git_command("diff", "--name-only", "--cached", "-z"),
    ):
        raw = subprocess.run(
            args,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for token in raw.split("\0"):
            if token:
                dirty_paths.add(Path(token))
    return sorted(dirty_paths, key=lambda path: path.as_posix())


def _git_head_from_files() -> str | None:
    git_dir = ROOT / ".git"
    head_file = git_dir / "HEAD"
    try:
        head = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head:
        return None
    if not head.startswith("ref:"):
        return head[:12]
    ref_name = head.removeprefix("ref:").strip()
    try:
        ref_commit = (git_dir / ref_name).read_text(encoding="utf-8").strip()
        if ref_commit:
            return ref_commit[:12]
    except OSError:
        pass
    try:
        packed_refs = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in packed_refs:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("^"):
            continue
        commit, _, name = stripped.partition(" ")
        if name == ref_name and commit:
            return commit[:12]
    return None


def _git_version_state() -> tuple[str | None, bool, int | None, str]:
    global _GIT_FILE_FALLBACK_LOGGED
    try:
        commit = subprocess.run(
            _git_command("rev-parse", "--short", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        committed_at_raw = subprocess.run(
            _git_command("log", "-1", "--format=%ct", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        committed_at_ms = None
        if committed_at_raw:
            committed_at_ms = max(0, int(committed_at_raw)) * 1000
        dirty_paths = _dirty_tracked_paths()
        dirty = bool(dirty_paths)
        last_updated_at_ms = committed_at_ms
        state_parts = [commit or "", str(committed_at_ms or 0)]
        for relative_path in dirty_paths:
            absolute_path = ROOT / relative_path
            relative = relative_path.as_posix()
            if absolute_path.exists():
                stat_result = absolute_path.stat()
                modified_at_ms = max(0, stat_result.st_mtime_ns // 1_000_000)
                if last_updated_at_ms is None or modified_at_ms > last_updated_at_ms:
                    last_updated_at_ms = modified_at_ms
                state_parts.append(f"{relative}:{stat_result.st_size}:{stat_result.st_mtime_ns}")
            else:
                state_parts.append(f"{relative}:deleted")
        build_number = _build_number(last_updated_at_ms)
        state_parts.append(build_number)
        codename = _repo_codename("|".join(state_parts))
        return commit or None, dirty, last_updated_at_ms, f"{build_number}.{codename}"
    except Exception:
        fallback_commit = _git_head_from_files()
        if fallback_commit:
            if not _GIT_FILE_FALLBACK_LOGGED:
                logger.debug("Resolved git version state from .git files after git CLI failed")
                _GIT_FILE_FALLBACK_LOGGED = True
            codename = _repo_codename(f"{fallback_commit}:git-file-fallback")
            return fallback_commit, False, None, f"000000000000.{codename}"
        logger.debug("Failed to resolve git version state", exc_info=True)
        return None, False, None, "000000000000.UnknownBuild"


def _app_version_payload() -> dict[str, object]:
    now = time.monotonic()
    with _VERSION_CACHE_LOCK:
        cached = _VERSION_CACHE.get("payload")
        fetched_at_raw = _VERSION_CACHE.get("fetched_at")
        fetched_at = (
            float(cast(int | float | str, fetched_at_raw)) if fetched_at_raw is not None else 0.0
        )
        if isinstance(cached, dict) and (now - fetched_at) < _VERSION_CACHE_TTL_SECONDS:
            return cast(dict[str, object], cached)

        base_version = _base_app_version()
        commit, dirty, last_updated_at_ms, dynamic_suffix = _git_version_state()
        version = f"{base_version}.{dynamic_suffix}"

        payload = {
            "version": version,
            "base_version": base_version,
            "git_commit": commit,
            "dirty": dirty,
            "last_updated_at_ms": last_updated_at_ms,
        }
        _VERSION_CACHE["fetched_at"] = now
        _VERSION_CACHE["payload"] = payload
        return dict(payload)


def _app_env() -> str:
    return os.environ.get("APP_ENV", "").strip().lower()


def _is_production() -> bool:
    return _app_env() in {"prod", "production"}


def _is_internet_exposed() -> bool:
    return os.environ.get("INTERNET_EXPOSED", "false").strip().lower() == "true"


def _is_multi_instance() -> bool:
    return os.environ.get("MULTI_INSTANCE", "false").strip().lower() == "true"


def _shared_rate_limits_required() -> bool:
    return _is_production() or _is_internet_exposed() or _is_multi_instance()


def _validate_shared_rate_limit_backend() -> None:
    if not _shared_rate_limits_required():
        return
    backend = os.environ.get("RATE_LIMIT_BACKEND", "").strip().lower() or "memory"
    if backend != "redis":
        raise RuntimeError(
            "APP_ENV=production, INTERNET_EXPOSED=true, or MULTI_INSTANCE=true "
            "requires RATE_LIMIT_BACKEND=redis",
        )
    redis_url = (
        os.environ.get("RATE_LIMIT_REDIS_URL", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
    )
    if not redis_url:
        raise RuntimeError(
            "RATE_LIMIT_BACKEND=redis requires RATE_LIMIT_REDIS_URL or REDIS_URL",
        )


def _cors_allow_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.environ.get("CORS_ALLOW_ORIGINS", DEFAULT_DEV_CORS_ORIGINS).split(",")
        if origin.strip()
    ]


def _allowed_hosts() -> list[str]:
    raw = os.environ.get("ALLOWED_HOSTS", DEFAULT_DEV_ALLOWED_HOSTS)
    return [host.strip() for host in raw.split(",") if host.strip()]


def _trusted_proxy_cidrs() -> list[str]:
    raw = os.environ.get("TRUSTED_PROXY_CIDRS", "")
    return [cidr.strip() for cidr in raw.split(",") if cidr.strip()]


def _trusted_proxy_networks() -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for cidr in _trusted_proxy_cidrs():
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise RuntimeError(f"Invalid TRUSTED_PROXY_CIDRS entry: {cidr}") from exc
        if network.prefixlen == 0:
            raise RuntimeError("TRUSTED_PROXY_CIDRS forbids catch-all networks")
        networks.append(network)
    return tuple(networks)


def _api_docs_enabled() -> bool:
    default = "false" if (_is_production() or _is_internet_exposed()) else "true"
    raw = os.environ.get("API_DOCS_ENABLED", "").strip().lower()
    if not raw:
        raw = default
    return raw == "true"


def _validate_runtime_security_config() -> None:
    _validate_shared_rate_limit_backend()
    strict_cookie_mode = _is_internet_exposed() or _is_production()
    if strict_cookie_mode:
        if not session_cookie_secure():
            raise RuntimeError(
                "APP_ENV=production or INTERNET_EXPOSED=true requires "
                "AUTH_SESSION_COOKIE_SECURE=true",
            )
        if session_cookie_samesite() == "none":
            raise RuntimeError(
                "APP_ENV=production or INTERNET_EXPOSED=true forbids "
                "AUTH_SESSION_COOKIE_SAMESITE=none",
            )
        if is_auth_required() and session_auth_enabled():
            mfa_secret = os.environ.get("AUTH_MFA_SECRET_KEY", "").strip()
            if not mfa_secret:
                raise RuntimeError(
                    "APP_ENV=production or INTERNET_EXPOSED=true requires AUTH_MFA_SECRET_KEY "
                    "to keep MFA secrets out of the database",
                )
            if len(mfa_secret) < 32:
                raise RuntimeError(
                    "APP_ENV=production or INTERNET_EXPOSED=true requires "
                    "AUTH_MFA_SECRET_KEY to be at least 32 characters",
                )
            if mfa_secret in _MFA_SECRET_PLACEHOLDERS:
                raise RuntimeError(
                    "APP_ENV=production or INTERNET_EXPOSED=true requires "
                    "AUTH_MFA_SECRET_KEY to be generated secret material, not a placeholder",
                )
        if security_telemetry_enabled():
            telemetry_privacy_mode = (
                os.environ.get("SECURITY_TELEMETRY_PRIVACY_MODE", "minimized").strip().lower()
            )
            telemetry_privacy_salt = os.environ.get(
                "SECURITY_TELEMETRY_PRIVACY_SALT",
                "",
            ).strip()
            if telemetry_privacy_mode == "minimized" and not telemetry_privacy_salt:
                raise RuntimeError(
                    "APP_ENV=production or INTERNET_EXPOSED=true requires "
                    "SECURITY_TELEMETRY_PRIVACY_SALT when security telemetry is enabled",
                )
            if telemetry_privacy_salt == "gardenops-security-telemetry":
                raise RuntimeError(
                    "APP_ENV=production or INTERNET_EXPOSED=true requires "
                    "SECURITY_TELEMETRY_PRIVACY_SALT to be deployment-specific",
                )

    if _is_internet_exposed():
        if not is_auth_required():
            raise RuntimeError("INTERNET_EXPOSED=true requires AUTH_REQUIRED=true")
        resolved_auth_mode = auth_mode()
        if resolved_auth_mode != "session":
            raise RuntimeError(
                "INTERNET_EXPOSED=true requires AUTH_MODE=session and forbids api_key/hybrid auth",
            )
        if os.environ.get("AUTH_API_KEY", "").strip():
            raise RuntimeError(
                "INTERNET_EXPOSED=true forbids AUTH_API_KEY to prevent shared-key fallback",
            )
        if os.environ.get("ALLOW_INSECURE_REMOTE", "").strip().lower() == "true":
            raise RuntimeError("INTERNET_EXPOSED=true forbids ALLOW_INSECURE_REMOTE=true")
        if not _trust_proxy_headers():
            msg = (
                "INTERNET_EXPOSED=true requires"
                " TRUST_PROXY_HEADERS=true for reverse-proxy deployments"
            )
            raise RuntimeError(msg)
        trusted_proxy_networks = _trusted_proxy_networks()
        if not trusted_proxy_networks:
            raise RuntimeError("INTERNET_EXPOSED=true requires explicit TRUSTED_PROXY_CIDRS")
        allowed_hosts = _allowed_hosts()
        if not allowed_hosts:
            raise RuntimeError("INTERNET_EXPOSED=true requires explicit ALLOWED_HOSTS")
        if any(host == "*" for host in allowed_hosts):
            raise RuntimeError("INTERNET_EXPOSED=true forbids wildcard ALLOWED_HOSTS")
        if _csp_report_only():
            raise RuntimeError("INTERNET_EXPOSED=true forbids CSP_REPORT_ONLY=true")
        if _api_docs_enabled():
            raise RuntimeError("INTERNET_EXPOSED=true forbids API_DOCS_ENABLED=true")

    if not _is_production():
        return
    if not is_auth_required():
        raise RuntimeError("APP_ENV=production requires AUTH_REQUIRED=true")
    configured_auth_mode = os.environ.get("AUTH_MODE", "").strip().lower()
    if configured_auth_mode in {"api_key", "hybrid"}:
        raise RuntimeError("APP_ENV=production requires AUTH_MODE=session")
    if os.environ.get("ALLOW_INSECURE_REMOTE", "").strip().lower() == "true":
        raise RuntimeError("APP_ENV=production forbids ALLOW_INSECURE_REMOTE=true")

    origins = _cors_allow_origins()
    if not origins:
        raise RuntimeError("APP_ENV=production requires explicit CORS_ALLOW_ORIGINS")
    for origin in origins:
        lower = origin.lower()
        if lower == "*":
            raise RuntimeError("APP_ENV=production forbids wildcard CORS origins")
        if not lower.startswith("https://"):
            raise RuntimeError("APP_ENV=production requires https CORS origins")
        if "localhost" in lower or "127.0.0.1" in lower:
            raise RuntimeError("APP_ENV=production requires non-localhost CORS origins")
    allowed_hosts = _allowed_hosts()
    if not allowed_hosts:
        raise RuntimeError("APP_ENV=production requires explicit ALLOWED_HOSTS")
    if any(host == "*" for host in allowed_hosts):
        raise RuntimeError("APP_ENV=production forbids wildcard ALLOWED_HOSTS")
    _local = {"localhost", "127.0.0.1", "::1", "[::1]", "testserver"}
    if any(host in _local for host in allowed_hosts):
        raise RuntimeError("APP_ENV=production requires non-localhost ALLOWED_HOSTS")
    if _api_docs_enabled():
        raise RuntimeError("APP_ENV=production forbids API_DOCS_ENABLED=true")


def _trust_proxy_headers() -> bool:
    return os.environ.get("TRUST_PROXY_HEADERS", "false").strip().lower() == "true"


def _forwarding_headers_present(request: Request) -> bool:
    return any(
        request.headers.get(name)
        for name in (
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-forwarded-host",
            "x-real-ip",
            "forwarded",
        )
    )


def _client_ip_forwarding_header_present(request: Request) -> bool:
    return bool(
        request.headers.get("x-forwarded-for", "").strip()
        or request.headers.get("x-real-ip", "").strip()
    )


def _request_source_ip(request: Request) -> IPAddress | None:
    host = request.client.host if request.client else ""
    if not host:
        return None
    if host == "testclient":
        host = "127.0.0.1"
    normalized = host.strip().strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(normalized)
    except ValueError:
        return None


def _request_from_trusted_proxy(request: Request) -> bool:
    source_ip = _request_source_ip(request)
    if source_ip is None:
        return False
    return any(source_ip in network for network in _trusted_proxy_networks())


def _normalized_forwarded_host(value: str) -> str:
    raw = value.split(",", 1)[0].strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(f"//{raw}")
    except ValueError:
        return ""
    return (parsed.hostname or raw.strip("[]")).lower()


def _forwarded_host_allowed(request: Request) -> bool:
    forwarded_host = _normalized_forwarded_host(
        request.headers.get("x-forwarded-host", ""),
    )
    if not forwarded_host:
        return False
    allowed_hosts = {
        normalized
        for normalized in (_normalized_forwarded_host(host) for host in _allowed_hosts())
        if normalized
    }
    return forwarded_host in allowed_hosts


def _edge_proxy_violation_detail(request: Request) -> str | None:
    if not _is_internet_exposed():
        return None
    if not _trust_proxy_headers():
        return "INTERNET_EXPOSED=true requires TRUST_PROXY_HEADERS=true"
    if not _forwarding_headers_present(request):
        return (
            "Internet-exposed deployments require requests to arrive through the trusted edge proxy"
        )
    if not _client_ip_forwarding_header_present(request):
        return "Internet-exposed deployments require X-Forwarded-For or X-Real-IP"
    if not _request_from_trusted_proxy(request):
        return "Forwarded headers are only accepted from TRUSTED_PROXY_CIDRS"
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if forwarded_proto != "https":
        return "HTTPS is required for internet-exposed deployments"
    if not request.headers.get("x-forwarded-host", "").strip():
        return "Internet-exposed deployments require X-Forwarded-Host from the trusted edge"
    if not _forwarded_host_allowed(request):
        return "X-Forwarded-Host must match ALLOWED_HOSTS"
    return None


def _max_body_bytes_for_path(path: str) -> int:
    if path in {"/api/plots/import", "/api/plants/import-csv"}:
        return env_int("MAX_IMPORT_BODY_BYTES", 8 * 1024 * 1024)
    if path == "/api/media/upload":
        return env_int("MEDIA_MAX_UPLOAD_BYTES", 6 * 1024 * 1024)
    if path in {"/api/ai/identify-plant", "/api/ai/diagnose-plant"}:
        return env_int("MAX_AI_PHOTO_BODY_BYTES", 5 * 1024 * 1024)
    return env_int("MAX_API_BODY_BYTES", 1 * 1024 * 1024)


def _request_timeout_seconds(path: str) -> float:
    if path.startswith("/shademap/terrain/"):
        return float(env_int("TERRAIN_REQUEST_TIMEOUT_SECONDS", 20))
    return float(env_int("API_REQUEST_TIMEOUT_SECONDS", 30))


_MUTATION_METRIC_BUCKETS = {
    "ai",
    "auth",
    "calendar",
    "client-errors",
    "exports",
    "external",
    "gardens",
    "harvest",
    "inventory",
    "issues",
    "journal",
    "media",
    "notifications",
    "planner",
    "plants",
    "plots",
    "procurement",
    "provider-settings",
    "saved-views",
    "security",
    "shademap",
    "snapshots",
    "statistics",
    "tasks",
    "weather",
    "workflows",
}


def _mutation_metric_bucket(path: str) -> str:
    parts = path.split("/")
    segment = parts[2].strip().lower() if len(parts) > 2 else "root"
    if segment not in _MUTATION_METRIC_BUCKETS:
        return "other"
    return "".join(char if char.isalnum() else "_" for char in segment)


def _shademap_import_fields_present(
    *,
    shademap: object | None,
    shademap_calibration: object | None,
    shademap_obstacles: object | None,
) -> bool:
    return (
        shademap is not None or shademap_calibration is not None or shademap_obstacles is not None
    )


def _require_shademap_import_allowed(subscription_tier: str, *, detail: str) -> None:
    if feature_allowed(subscription_tier, "shade_map"):
        return
    raise HTTPException(status_code=403, detail=detail)


def _admin_mfa_setup_path_allowed(path: str) -> bool:
    return path in {
        "/api/auth/me",
        "/api/auth/me/settings",
        "/api/auth/logout",
        "/api/auth/reauthenticate",
        "/api/auth/passkeys",
        "/api/auth/passkeys/register/options",
        "/api/auth/passkeys/register/verify",
    } or path.startswith("/api/auth/mfa")


def _admin_strong_auth_path_allowed(path: str) -> bool:
    return path in {
        "/api/auth/status",
        "/api/auth/me",
        "/api/auth/me/settings",
        "/api/auth/logout",
        "/api/auth/reauthenticate",
        "/api/auth/reauthenticate/passkey/options",
        "/api/auth/reauthenticate/passkey/verify",
    }


def _admin_session_requires_strong_auth(context: Any) -> bool:
    return (
        context.auth_type == "session"
        and context.role == "admin"
        and (admin_mfa_required() or context.mfa_enabled or context.passkey_enrolled)
        and int(context.mfa_authenticated_at_ms or 0) <= 0
    )


def _forced_password_change_path_allowed(path: str) -> bool:
    return path in {
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/change-password",
    }


def _is_personal_attention_mutation_path(path: str) -> bool:
    normalized_path = path.rstrip("/") or "/"
    if normalized_path == "/api/attention/preferences":
        return True
    if not normalized_path.startswith("/api/attention/items/"):
        return False
    return normalized_path.rsplit("/", 1)[-1] in {"read", "dismiss", "snooze", "restore"}


def _csp_report_only() -> bool:
    default = "true" if not _is_internet_exposed() else "false"
    return os.environ.get("CSP_REPORT_ONLY", default).strip().lower() == "true"


def _csp_report_uri() -> str:
    return os.environ.get("CSP_REPORT_URI", "/api/security/csp-report").strip()


def _csp_policy() -> str:
    connect_src = [
        "'self'",
        "https://api.anthropic.com",
        "https://shademap.app",
        "https://overpass-api.de",
        "https://overpass.kumi.systems",
        "https://overpass.private.coffee",
        "https://s3.amazonaws.com",
    ]
    report_uri = _csp_report_uri()
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "require-trusted-types-for 'script'",
        "trusted-types gardenops-html default",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "img-src 'self' data: blob: https://*.amazonaws.com https://*.shademap.app",
        f"connect-src {' '.join(connect_src)}",
        "font-src 'self' data: https://fonts.gstatic.com",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
    if report_uri:
        directives.append(f"report-uri {report_uri}")
    return "; ".join(directives)


async def _notification_scheduler_loop() -> None:
    poll_seconds = notification_scheduler_poll_seconds()
    owner_id = notification_scheduler_owner_id()
    logger.info(
        "Notification scheduler enabled (poll=%ss, owner=%s)",
        poll_seconds,
        owner_id,
    )
    while True:
        conn = get_db()
        try:
            if acquire_notification_scheduler_lease(
                conn,
                owner_id,
                poll_seconds=poll_seconds,
            ):
                result = await asyncio.to_thread(run_notification_maintenance_once, conn)
                if (
                    int(result["notifications_created"]) > 0
                    or int(result["emailed_users"]) > 0
                    or int(result["notifications_marked"]) > 0
                    or int(result.get("tasks_auto_created", 0)) > 0
                    or int(result.get("weather_alerts_created", 0)) > 0
                    or int(result.get("issues_escalated", 0)) > 0
                ):
                    logger.info("Notification scheduler processed maintenance: %s", result)
            else:
                logger.debug("Notification scheduler lease is currently held elsewhere")
        except Exception:
            logger.exception("Notification scheduler tick failed")
        finally:
            with suppress(Exception):
                release_notification_scheduler_lease(conn, owner_id)
            return_db(conn)
        await asyncio.sleep(poll_seconds)


def _startup_integrity_check() -> None:
    """Verify database integrity before accepting requests."""
    conn = get_db()
    try:
        # Hard gate: B-tree corruption
        detail = db_quick_check(conn)
        if detail != "ok":
            raise RuntimeError(f"Database failed integrity check at startup: {detail}")

        # Soft gate: FK violations (warn only)
        violations = db_foreign_key_violations(conn)
        if violations:
            logger.warning(
                "Startup FK check: %d violations found",
                len(violations),
            )
            for v in violations[:10]:
                logger.warning(
                    "  FK violation: table=%s, rowid=%s, references=%s",
                    v[0],
                    v[1],
                    v[2],
                )

        # Seed health endpoint cache if available
        try:
            from gardenops.routers.health import seed_cache

            seed_cache(
                db_ok=True,
                quick_check_detail=detail,
                fk_violations=len(violations),
            )
        except ImportError:
            pass  # Health router not yet added
    finally:
        return_db(conn)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _setup_error_log()
    _validate_runtime_security_config()
    ensure_backend_ready()
    init_db()
    _startup_integrity_check()
    ensure_bootstrap_user_from_env()
    warn_csrf_secret_not_configured()
    start_security_telemetry_exporter()
    scheduler_task: asyncio.Task[None] | None = None

    from gardenops.routers.shademap import _load_grid_from_db, _save_grid_to_db
    from gardenops.services.lidar_terrain import set_grid_cache_callbacks

    set_grid_cache_callbacks(_load_grid_from_db, _save_grid_to_db)
    if notification_scheduler_enabled():
        scheduler_task = asyncio.create_task(
            _notification_scheduler_loop(),
            name="notification-scheduler",
        )
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        stop_security_telemetry_exporter()
        close_pool()
        # Flush Taillight log handler on shutdown
        for h in logging.getLogger().handlers:
            shutdown_fn = getattr(h, "shutdown", None)
            if callable(shutdown_fn):
                shutdown_fn()


app = FastAPI(
    title=app_name(),
    lifespan=lifespan,
    docs_url="/docs" if _api_docs_enabled() else None,
    redoc_url="/redoc" if _api_docs_enabled() else None,
    openapi_url="/openapi.json" if _api_docs_enabled() else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    expose_headers=["X-Request-ID"],
    allow_headers=[
        "content-type",
        "authorization",
        "x-api-key",
        "x-garden-id",
        "x-csrf-token",
        "x-xsrf-token",
        "x-request-id",
    ],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())

app.include_router(shademap_router, prefix="/api")
app.include_router(shademap_asset_router)
app.include_router(auth_router, prefix="/api")
app.include_router(gardens_router, prefix="/api")
app.include_router(plots_router, prefix="/api")
app.include_router(map_objects_router, prefix="/api")
app.include_router(plants_router, prefix="/api")
app.include_router(external_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(provider_settings_router, prefix="/api")
app.include_router(journal_router, prefix="/api")
app.include_router(media_router, prefix="/api")
app.include_router(statistics_router, prefix="/api")
app.include_router(inventory_router, prefix="/api")
app.include_router(calendar_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(attention_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(weather_router, prefix="/api")
app.include_router(issues_router, prefix="/api")
app.include_router(saved_views_router, prefix="/api")
app.include_router(harvest_router, prefix="/api")
app.include_router(procurement_router, prefix="/api")
app.include_router(planner_router, prefix="/api")
app.include_router(exports_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(calendar_feed_router)


@app.middleware("http")
async def edge_origin_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    detail = _edge_proxy_violation_detail(request)
    if detail is not None:
        record_security_event("edge_origin_rejections")
        return JSONResponse(status_code=403, content={"detail": detail})
    return await call_next(request)


@app.middleware("http")
async def auth_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    path = request.url.path
    protected = path.startswith("/api") or path.startswith("/shademap/terrain/")
    public_auth_paths = {
        "/api/health",
        "/api/version",
        "/api/auth/status",
        "/api/auth/bootstrap",
        "/api/auth/login",
        "/api/auth/passkeys/login/options",
        "/api/auth/passkeys/login/verify",
        "/api/auth/reset-password",
        "/api/auth/password-policy",
        "/api/auth/invitations/accept",
        "/api/auth/invitations/passkey/register/options",
        "/api/auth/invitations/passkey/register/verify",
        "/api/auth/invitations/peek",
        "/api/auth/check-hibp",
        "/api/admin/system/health",
        "/api/security/csp-report",
        "/api/client-errors",
    }
    csrf_exempt_mutation_paths = {
        "/api/auth/bootstrap",
        "/api/auth/login",
        "/api/auth/passkeys/login/options",
        "/api/auth/passkeys/login/verify",
        "/api/auth/reset-password",
        "/api/auth/invitations/accept",
        "/api/auth/invitations/passkey/register/options",
        "/api/auth/invitations/passkey/register/verify",
        "/api/auth/invitations/peek",
        "/api/auth/check-hibp",
        "/api/security/csp-report",
        "/api/client-errors",
    }
    auth_context = None
    remote_host = request.client.host if request.client else ""

    def _audit_mutation(status_code: int, detail: str = "") -> None:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        if not path.startswith("/api"):
            return
        # Route handlers that audit directly set this flag to avoid
        # write-lock contention from the middleware opening a second connection
        if getattr(request.state, "audited_by_handler", False):
            return
        write_audit_event(
            method=request.method,
            path=path,
            status_code=status_code,
            remote_host=remote_host,
            detail=detail,
            auth_context=auth_context,
        )

    if protected:
        # Let CORS middleware answer browser preflight checks.
        if request.method == "OPTIONS":
            return await call_next(request)
        edge_detail = _edge_proxy_violation_detail(request)
        if edge_detail is not None:
            record_security_event("edge_origin_rejections")
            return JSONResponse(status_code=403, content={"detail": edge_detail})
        if _is_production():
            if _forwarding_headers_present(request) and not _trust_proxy_headers():
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": (
                            "Forwarding headers are present but TRUST_PROXY_HEADERS is false. "
                            "Rejecting to avoid unsafe proxy trust assumptions."
                        ),
                    },
                )
            if _trust_proxy_headers():
                forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
                if forwarded_proto and forwarded_proto != "https":
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "HTTPS is required in production"},
                    )
        if path not in public_auth_paths:
            try:
                conn: DbConn | None = None
                try:
                    if (
                        session_auth_enabled()
                        and request.cookies.get(
                            session_cookie_name(),
                            "",
                        ).strip()
                    ):
                        conn = get_db()
                        auth_context = validate_request_auth(request, conn=conn)
                    else:
                        auth_context = validate_request_auth(request)
                    if path != "/api/security/csp-report" and (
                        path.startswith("/api") or path.startswith("/shademap/terrain/")
                    ):
                        if conn is None:
                            conn = get_db()
                        auth_context = resolve_garden_context(conn, request, auth_context)
                        conn.commit()
                finally:
                    if conn is not None:
                        return_db(conn)
                request.state.auth_context = auth_context
            except HTTPException as exc:
                if exc.status_code == 401:
                    record_security_event("auth_failures")
                    logger.warning(
                        "Auth rejected: %s %s ip=%s",
                        request.method,
                        path,
                        remote_host,
                    )
                    try:
                        enforce_rate_limit(
                            request,
                            bucket="auth-fail",
                            limit=env_int("AUTH_FAIL_RATE_LIMIT", 20),
                            window_seconds=60,
                        )
                    except HTTPException as rate_exc:
                        _audit_mutation(rate_exc.status_code, rate_exc.detail)
                        return JSONResponse(
                            status_code=rate_exc.status_code,
                            content={"detail": rate_exc.detail},
                        )
                _audit_mutation(exc.status_code, exc.detail)
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            if (
                auth_context
                and auth_context.must_change_password
                and protected
                and not _forced_password_change_path_allowed(path)
            ):
                _audit_mutation(403, "Password change required")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Password change is required before full access"},
                )
            # ── Feature tier gating ──
            required_feature = feature_for_route(path)
            if required_feature is not None and auth_context is not None:
                if not feature_allowed(auth_context.subscription_tier, required_feature):
                    _audit_mutation(403, "tier_denied")
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Feature not available on your plan"},
                    )
            if (
                auth_context
                and auth_context.mfa_setup_required
                and protected
                and not _admin_mfa_setup_path_allowed(path)
            ):
                _audit_mutation(403, "Admin MFA setup required")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Admin MFA setup is required before full access"},
                )
            if (
                auth_context
                and not auth_context.mfa_setup_required
                and protected
                and _admin_session_requires_strong_auth(auth_context)
                and not _admin_strong_auth_path_allowed(path)
            ):
                _audit_mutation(403, "Platform-admin strong authentication required")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Platform-admin MFA or passkey authentication is required"},
                )
            if (
                auth_context
                and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and path.startswith("/api")
                and not path.startswith("/api/auth/")
                and not path.startswith("/api/ai/")
                and not _is_personal_attention_mutation_path(path)
                and not has_write_access(auth_context)
            ):
                _audit_mutation(403, "Forbidden: write access required")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: write access required"},
                )
            if (
                auth_context
                and auth_context.session_via_cookie
                and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and path.startswith("/api")
                and path not in csrf_exempt_mutation_paths
            ):
                csrf_token = (
                    request.headers.get("x-csrf-token", "").strip()
                    or request.headers.get("x-xsrf-token", "").strip()
                )
                if not csrf_token_matches_context(auth_context, csrf_token):
                    _audit_mutation(403, "Forbidden: invalid or missing CSRF token")
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Forbidden: invalid or missing CSRF token"},
                    )
    if request.method == "POST" and path in {
        "/api/plots/import",
        "/api/plants/import-csv",
    }:
        enforce_rate_limit(
            request,
            bucket=f"mutate:{path}",
            limit=env_int("MUTATION_RATE_LIMIT", 20),
            window_seconds=60,
        )
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith("/api/")
        and path
        not in {
            "/api/plots/import",
            "/api/plants/import-csv",
            "/api/security/csp-report",
        }
        and not path.startswith("/api/auth/")
        and not path.startswith("/api/ai/")
    ):
        enforce_rate_limit(
            request,
            bucket="api-mutation",
            limit=env_int("API_MUTATION_RATE_LIMIT", 120),
            window_seconds=60,
        )
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith("/api")
        and is_emergency_read_only()
    ):
        emergency_exempt_paths = {
            "/api/auth/logout",
            "/api/auth/reauthenticate",
            "/api/auth/reauthenticate/passkey/options",
            "/api/auth/reauthenticate/passkey/verify",
            "/api/auth/emergency-read-only",
            "/api/auth/revoke-all-sessions",
            "/api/auth/revoke-user-sessions",
            "/api/client-errors",
            "/api/security/csp-report",
        }
        is_session_control_path = path.startswith("/api/auth/users/") and path.endswith(
            "/revoke-sessions"
        )
        if path not in emergency_exempt_paths and not is_session_control_path:
            _audit_mutation(503, "Emergency read-only mode active")
            return JSONResponse(
                status_code=503,
                content={"detail": "Emergency read-only mode is active"},
            )
    if request.method in {"POST", "PATCH", "PUT"} and path.startswith("/api"):
        max_body_bytes = _max_body_bytes_for_path(path)
        raw_length = request.headers.get("content-length", "").strip()
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
            if content_length > max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        request_timeout = _request_timeout_seconds(path)
        try:
            await asyncio.wait_for(
                read_and_cache_body_limited(request, max_body_bytes),
                timeout=request_timeout,
            )
        except TimeoutError:
            _audit_mutation(504, "Request timeout exceeded")
            return JSONResponse(status_code=504, content={"detail": "Request timed out"})
        except HTTPException as exc:
            if exc.status_code == 413:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
            raise
    should_reserve_mutation_audit = (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith("/api")
        and path not in {"/api/security/csp-report", "/api/client-errors"}
    )
    if should_reserve_mutation_audit:
        try:
            reserve_mutation_audit_event(
                method=request.method,
                path=path,
                remote_host=remote_host,
                auth_context=auth_context,
            )
        except Exception:
            logger.error(
                "Mutation blocked because its audit reservation failed: %s %s",
                request.method,
                path,
                exc_info=True,
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "Mutation audit is temporarily unavailable"},
            )
    try:
        response = await asyncio.wait_for(
            call_next(request),
            timeout=_request_timeout_seconds(path),
        )
    except TimeoutError:
        _audit_mutation(504, "Request timeout exceeded")
        return JSONResponse(status_code=504, content={"detail": "Request timed out"})
    _audit_mutation(response.status_code)
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith("/api")
        and not path.startswith("/api/security/csp-report")
    ):
        record_security_event("mutations_total")
        record_security_event(f"mutations_{request.method}_{_mutation_metric_bucket(path)}")
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    csp_header = (
        "Content-Security-Policy-Report-Only" if _csp_report_only() else "Content-Security-Policy"
    )
    response.headers.setdefault(csp_header, _csp_policy())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), payment=()",
    )
    # Cache-busting: HTML pages must revalidate; hashed assets can cache forever
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    # RateLimit-* response headers (draft-ietf-httpapi-ratelimit-headers)
    rate_info = getattr(request.state, "rate_limit_info", None)
    if rate_info:
        response.headers.setdefault("RateLimit-Limit", str(rate_info["limit"]))
        policy = f"{rate_info['limit']};w={rate_info['window']}"
        response.headers.setdefault("RateLimit-Policy", policy)
    return response


_error_logger = logging.getLogger("gardenops.errors")


@app.middleware("http")
async def error_log_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    try:
        response = await call_next(request)
    except Exception:
        _error_logger.exception(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
            extra={
                "path": request.url.path,
                "method": request.method,
                "status_code": 500,
            },
        )
        raise
    if response.status_code >= 500:
        _error_logger.error(
            "%s %s returned %d",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
            },
        )
    return response


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    raw_request_id = request.headers.get("x-request-id", "")
    request_id = normalize_request_id(raw_request_id) or generate_request_id()
    request.state.request_id = request_id
    tokens = bind_request_context(
        request_id=request_id,
        path=request.url.path,
        method=request.method,
    )
    try:
        response = await call_next(request)
    finally:
        reset_request_context(tokens)
    response.headers.setdefault("X-Request-ID", request_id)
    return response


@app.post("/api/client-errors", include_in_schema=False)
async def client_error_report(request: Request) -> Response:
    """Receive error reports from the frontend."""
    enforce_rate_limit(
        request,
        bucket="client-error-report",
        limit=env_int("CLIENT_ERROR_RATE_LIMIT", 60),
        window_seconds=60,
    )
    try:
        body = await read_body_limited(request, 8192)
    except HTTPException:
        return Response(status_code=204)
    if not body:
        return Response(status_code=204)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=204)
    if not isinstance(payload, dict):
        return Response(status_code=204)
    # Only extract known structured fields — never log arbitrary user text verbatim
    msg = _sanitize_log_str(str(payload.get("message", "unknown"))[:300], 300)
    err_type = _sanitize_log_str(str(payload.get("type", ""))[:30], 30)
    source = _sanitize_log_str(str(payload.get("source", ""))[:200], 200)
    stack = _sanitize_log_str(
        str(payload.get("stack", ""))[:_LOG_TRACEBACK_MAX_LEN],
        _LOG_TRACEBACK_MAX_LEN,
    )
    client_ts = _sanitize_log_str(str(payload.get("ts", ""))[:40], 40)
    api_path = _client_report_path(payload.get("api_path", ""))
    request_id = normalize_request_id(payload.get("request_id", ""))
    report_request_id = normalize_request_id(getattr(request.state, "request_id", ""))
    lineno = payload.get("lineno")
    colno = payload.get("colno")
    try:
        status_code = int(payload.get("status_code", 0) or 0)
    except TypeError, ValueError:
        status_code = 0
    if status_code < 0 or status_code > 599:
        status_code = 0
    handled = bool(payload.get("handled", False))
    loc = f" at {source}:{lineno}:{colno}" if source and lineno else ""
    _error_logger.warning(
        "Client %s: %s%s",
        err_type or "error",
        msg,
        loc,
        extra={
            "path": _client_report_path(payload.get("url", "")),
            "method": "CLIENT",
            "status_code": status_code,
            "request_id": request_id,
            "report_request_id": report_request_id,
            "api_path": api_path,
            "source": source,
            "client_lineno": lineno,
            "client_colno": colno,
            "client_ts": client_ts,
            "client_stack": stack,
            "error_kind": err_type or ("api_error" if handled else "client_runtime"),
            "handled": handled,
        },
    )
    return Response(status_code=204)


@app.post("/api/security/csp-report", include_in_schema=False)
async def csp_report(request: Request) -> Response:
    enforce_rate_limit(
        request,
        bucket="csp-report",
        limit=env_int("CSP_REPORT_RATE_LIMIT", 120),
        window_seconds=60,
    )
    max_bytes = env_int("CSP_REPORT_MAX_BYTES", 32768)
    try:
        body = await read_body_limited(request, max_bytes)
    except HTTPException:
        return Response(status_code=204)
    payload: object = {}
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body.decode("utf-8", errors="replace")[:2000]}
    logger.warning("CSP report received: %s", redact_sensitive_text(payload, 2000))
    return Response(status_code=204)


@app.get("/api/version")
def app_version() -> dict[str, object]:
    return _app_version_payload()


def _default_house_state(
    *,
    grid_rows: int = GRID_ROWS,
    grid_cols: int = GRID_COLS,
) -> dict[str, int]:
    safe_grid_rows = max(5, min(int(grid_rows), 100))
    safe_grid_cols = max(5, min(int(grid_cols), 100))
    width = max(1, min(HOUSE_DEFAULT_WIDTH, safe_grid_cols))
    height = max(1, min(HOUSE_DEFAULT_HEIGHT, safe_grid_rows))
    max_row = max(1, safe_grid_rows - height + 1)
    max_col = max(1, safe_grid_cols - width + 1)
    return {
        "row": max(1, min(HOUSE_DEFAULT_ROW, max_row)),
        "col": max(1, min(HOUSE_DEFAULT_COL, max_col)),
        "width": width,
        "height": height,
        "north_degrees": MAP_DEFAULT_NORTH_DEGREES,
        "grid_rows": safe_grid_rows,
        "grid_cols": safe_grid_cols,
    }


def _active_garden_id(request: Request) -> int:
    context = resolve_request_auth_context(request)
    if context.garden_id is None:
        raise HTTPException(status_code=500, detail="Missing garden context")
    return int(context.garden_id)


def _owner_user_for_garden(
    db: DbConn,
    *,
    garden_id: int,
    preferred_user_id: int | None,
) -> int:
    if preferred_user_id is not None:
        member = db.execute(
            """
            SELECT 1
            FROM garden_memberships
            WHERE garden_id = %s AND user_id = %s
            LIMIT 1
            """,
            (garden_id, preferred_user_id),
        ).fetchone()
        if member:
            return preferred_user_id
    row = db.execute(
        """
        SELECT gm.user_id
        FROM garden_memberships gm
        JOIN auth_users u ON u.id = gm.user_id
        WHERE gm.garden_id = %s AND u.is_active = 1
        ORDER BY CASE gm.role
            WHEN 'admin' THEN 0
            WHEN 'editor' THEN 1
            ELSE 2
        END, gm.user_id
        LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if row:
        return int(row["user_id"])
    if not is_auth_required() and preferred_user_id is None:
        fallback = db.execute(
            """
            SELECT id
            FROM auth_users
            WHERE username = '__local_admin__' AND is_active = 1
            LIMIT 1
            """,
        ).fetchone()
        if fallback:
            fallback_user_id = _coerce_required_int(fallback["id"])
        else:
            created = create_user(
                db,
                username="__local_admin__",
                password="local-admin-bootstrap",
                role="admin",
            )
            fallback_user_id = _coerce_required_int(created["id"])
        db.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT(garden_id, user_id) DO UPDATE SET
                role = excluded.role
            """,
            (garden_id, fallback_user_id),
        )
        return fallback_user_id
    raise HTTPException(
        status_code=409,
        detail="No active garden member is available to own imported plots",
    )


def get_layout_state(db: DbConn, garden_id: int) -> dict[str, int]:
    row = db.execute(
        """
        SELECT house_row, house_col, house_width, house_height, north_degrees,
               grid_rows, grid_cols
        FROM layout_state
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        garden = db.execute(
            "SELECT grid_rows, grid_cols FROM gardens WHERE id = %s LIMIT 1",
            (garden_id,),
        ).fetchone()
        default = _default_house_state(
            grid_rows=int(garden["grid_rows"]) if garden else GRID_ROWS,
            grid_cols=int(garden["grid_cols"]) if garden else GRID_COLS,
        )
        set_layout_state(db, default, garden_id=garden_id)
        db.commit()
        return default
    return {
        "row": int(row["house_row"]),
        "col": int(row["house_col"]),
        "width": int(row["house_width"]),
        "height": int(row["house_height"]),
        "north_degrees": int(row["north_degrees"]),
        "grid_rows": int(row["grid_rows"]),
        "grid_cols": int(row["grid_cols"]),
    }


def _coerce_north_degrees(raw: object) -> int:
    if raw is None:
        return MAP_DEFAULT_NORTH_DEGREES
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        legacy = {
            "north": 0,
            "east": 90,
            "south": 180,
            "west": 270,
        }
        if lowered in legacy:
            return legacy[lowered]
    try:
        return _coerce_required_int(raw) % 360
    except (
        ValueError,
        TypeError,
    ):
        return MAP_DEFAULT_NORTH_DEGREES


def _coerce_required_int(value: object) -> int:
    return int(cast(int | float | str, value))


def _coerce_optional_int(value: object, default: int) -> int:
    if value is None:
        return default
    return _coerce_required_int(value)


def _ensure_garden_plots_fit_grid(
    db: DbConn,
    *,
    garden_id: int,
    grid_rows: int,
    grid_cols: int,
) -> None:
    overflow = db.execute(
        """
        SELECT p.plot_id, p.grid_row, p.grid_col
        FROM plots p
        JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE po.garden_id = %s
          AND (p.grid_row > %s OR p.grid_col > %s)
        ORDER BY p.grid_row DESC, p.grid_col DESC
        LIMIT 1
        """,
        (garden_id, grid_rows, grid_cols),
    ).fetchone()
    if overflow:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Grid is too small for existing plot {overflow['plot_id']} "
                f"at row {overflow['grid_row']}, col {overflow['grid_col']}"
            ),
        )


def _ensure_garden_map_objects_fit_grid(
    db: DbConn,
    *,
    garden_id: int,
    grid_rows: int,
    grid_cols: int,
) -> None:
    rows = db.execute(
        """
        SELECT public_id, name, geometry_json
        FROM garden_map_objects
        WHERE garden_id = %s
        ORDER BY z_index DESC, id DESC
        """,
        (garden_id,),
    ).fetchall()
    for row in rows:
        try:
            geometry = json.loads(str(row["geometry_json"] or "{}"))
            x = _coerce_required_int(geometry["x"])
            y = _coerce_required_int(geometry["y"])
            width = _coerce_required_int(geometry["width"])
            height = _coerce_required_int(geometry["height"])
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Existing map object {row['public_id']} has invalid geometry",
            ) from exc
        if x + width - 1 > grid_cols or y + height - 1 > grid_rows:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Grid is too small for existing map object {row['name']} at row {y}, col {x}"
                ),
            )


def set_layout_state(
    db: DbConn,
    house: Mapping[str, object],
    *,
    garden_id: int,
) -> dict[str, int]:
    row = _coerce_required_int(house["row"])
    col = _coerce_required_int(house["col"])
    width = _coerce_required_int(house["width"])
    height = _coerce_required_int(house["height"])
    raw_north_degrees = house.get("north_degrees")
    if raw_north_degrees is None:
        raw_north_degrees = house.get("direction", MAP_DEFAULT_NORTH_DEGREES)
    north_degrees = _coerce_north_degrees(raw_north_degrees) % 360
    grid_rows = _coerce_optional_int(house.get("grid_rows"), GRID_ROWS)
    grid_cols = _coerce_optional_int(house.get("grid_cols"), GRID_COLS)
    grid_rows = max(5, min(grid_rows, 100))
    grid_cols = max(5, min(grid_cols, 100))
    if row + height - 1 > grid_rows or col + width - 1 > grid_cols:
        raise HTTPException(status_code=400, detail="House does not fit within the grid")
    db.execute(
        """
        INSERT INTO layout_state (
            garden_id, house_row, house_col, house_width, house_height,
            north_degrees, grid_rows, grid_cols
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(garden_id) DO UPDATE SET
            house_row = excluded.house_row,
            house_col = excluded.house_col,
            house_width = excluded.house_width,
            house_height = excluded.house_height,
            north_degrees = excluded.north_degrees,
            grid_rows = excluded.grid_rows,
            grid_cols = excluded.grid_cols
        """,
        (garden_id, row, col, width, height, north_degrees, grid_rows, grid_cols),
    )
    return {
        "row": row,
        "col": col,
        "width": width,
        "height": height,
        "north_degrees": north_degrees,
        "grid_rows": grid_rows,
        "grid_cols": grid_cols,
    }


def snapshot_layout(
    db: DbConn,
    garden_id: int,
    *,
    include_unowned: bool = False,
) -> str:
    if include_unowned:
        rows = db.execute(
            """
            SELECT DISTINCT p.plot_id, p.zone_code, p.zone_name, p.plot_number,
                p.grid_row, p.grid_col, COALESCE(p.sub_zone, '') AS sub_zone,
                COALESCE(p.notes, '') AS notes, p.color
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s OR po.garden_id IS NULL
            ORDER BY p.plot_id
            """,
            (garden_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT p.plot_id, p.zone_code, p.zone_name, p.plot_number,
                p.grid_row, p.grid_col, COALESCE(p.sub_zone, '') AS sub_zone,
                COALESCE(p.notes, '') AS notes, p.color
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s
            ORDER BY p.plot_id
            """,
            (garden_id,),
        ).fetchall()
    payload = {
        "schema_version": 1,
        "plots": [dict(r) for r in rows],
        "house": get_layout_state(db, garden_id),
        "shademap": get_shademap_state(db, garden_id=garden_id),
        "shademap_calibration": get_shademap_calibration(db, garden_id=garden_id),
        "shademap_obstacles": list_shademap_obstacles(db, garden_id=garden_id),
        "map_objects": snapshot_map_objects(db, garden_id),
    }
    return json.dumps(payload)


def restore_snapshot_data(
    db: DbConn,
    plots: list[dict[str, object]],
    *,
    garden_id: int,
    owner_user_id: int,
    house: dict[str, object] | None = None,
    shademap: dict[str, object] | None = None,
    shademap_calibration: dict[str, object] | None = None,
    shademap_obstacles: list[dict[str, object]] | None = None,
    map_objects: list[dict[str, Any]] | None = None,
    manage_transaction: bool = True,
    media_storage_pairs_out: list[tuple[str, str]] | None = None,
) -> int:
    if not plots:
        raise HTTPException(status_code=400, detail="Import must contain at least one plot")

    seen: set[str] = set()
    seen_cells: set[tuple[int, int]] = set()
    for p in plots:
        pid = str(p["plot_id"])
        if pid in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate plot_id in import: {pid}")
        seen.add(pid)
        raw_row = p.get("grid_row")
        raw_col = p.get("grid_col")
        if raw_row is not None and raw_col is not None:
            row = _coerce_required_int(raw_row)
            col = _coerce_required_int(raw_col)
            cell = (row, col)
            if cell in seen_cells:
                raise HTTPException(
                    status_code=400,
                    detail=f"Duplicate grid cell in import: ({row}, {col})",
                )
            seen_cells.add(cell)

    if seen:
        placeholders = ",".join("%s" for _ in seen)
        foreign = db.execute(
            f"""
            SELECT po.plot_id
            FROM plot_ownership po
            WHERE po.plot_id IN ({placeholders}) AND po.garden_id != %s
            LIMIT 1
            """,
            [*sorted(seen), garden_id],
        ).fetchone()
        if foreign:
            raise HTTPException(
                status_code=409,
                detail=f"Plot ID {foreign['plot_id']} is already owned by another garden",
            )

    if manage_transaction:
        db.commit()
    media_storage_pairs: list[tuple[str, str]] = []
    try:
        db.execute("SET CONSTRAINTS ALL DEFERRED")
        lock_garden_layout(db, garden_id)
        existing_owner_rows = db.execute(
            "SELECT plot_id, owner_user_id FROM plot_ownership WHERE garden_id = %s",
            (garden_id,),
        ).fetchall()
        existing_owner_by_plot_id = {
            str(row["plot_id"]): _coerce_required_int(row["owner_user_id"])
            for row in existing_owner_rows
        }
        target_plot_ids = set(seen)
        existing_plot_ids = set(existing_owner_by_plot_id)
        removed_plot_ids = sorted(existing_plot_ids - target_plot_ids)
        retained_plot_ids = sorted(existing_plot_ids & target_plot_ids)
        if removed_plot_ids:
            replacement_result = delete_plots_for_replacement(
                db,
                garden_id=garden_id,
                plot_ids=removed_plot_ids,
            )
            media_storage_pairs.extend(replacement_result.media_storage_pairs)
        if retained_plot_ids:
            db.execute(
                """
                UPDATE plots
                SET grid_row = NULL, grid_col = NULL
                WHERE garden_id = %s AND plot_id = ANY(%s)
                """,
                (garden_id, retained_plot_ids),
            )
        for p in plots:
            plot_id = str(p["plot_id"])
            db.execute(
                "INSERT INTO plots"
                " (plot_id,garden_id,zone_code,zone_name,plot_number,"
                "grid_row,grid_col,sub_zone,notes,color)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (plot_id) DO UPDATE SET"
                " garden_id=EXCLUDED.garden_id,"
                " zone_code=EXCLUDED.zone_code,"
                " zone_name=EXCLUDED.zone_name,"
                " plot_number=EXCLUDED.plot_number,"
                " grid_row=EXCLUDED.grid_row,"
                " grid_col=EXCLUDED.grid_col,"
                " sub_zone=EXCLUDED.sub_zone,"
                " notes=EXCLUDED.notes,"
                " color=EXCLUDED.color",
                (
                    plot_id,
                    garden_id,
                    p["zone_code"],
                    p["zone_name"],
                    p["plot_number"],
                    p["grid_row"],
                    p["grid_col"],
                    p.get("sub_zone", ""),
                    p.get("notes", ""),
                    p.get("color"),
                ),
            )
            db.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plot_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    garden_id = excluded.garden_id
                """,
                (
                    plot_id,
                    existing_owner_by_plot_id.get(plot_id, owner_user_id),
                    garden_id,
                ),
            )
        if house is None:
            garden = db.execute(
                "SELECT grid_rows, grid_cols FROM gardens WHERE id = %s LIMIT 1",
                (garden_id,),
            ).fetchone()
            updated_house = set_layout_state(
                db,
                _default_house_state(
                    grid_rows=int(garden["grid_rows"]) if garden else GRID_ROWS,
                    grid_cols=int(garden["grid_cols"]) if garden else GRID_COLS,
                ),
                garden_id=garden_id,
            )
        else:
            updated_house = set_layout_state(
                db,
                {
                    "row": _coerce_required_int(house["row"]),
                    "col": _coerce_required_int(house["col"]),
                    "width": _coerce_required_int(house["width"]),
                    "height": _coerce_required_int(house["height"]),
                    "north_degrees": _coerce_north_degrees(
                        house.get("north_degrees")
                        if house.get("north_degrees") is not None
                        else house.get("direction", MAP_DEFAULT_NORTH_DEGREES),
                    ),
                    "grid_rows": _coerce_optional_int(house.get("grid_rows"), GRID_ROWS),
                    "grid_cols": _coerce_optional_int(house.get("grid_cols"), GRID_COLS),
                },
                garden_id=garden_id,
            )
        db.execute(
            "UPDATE gardens SET grid_rows = %s, grid_cols = %s WHERE id = %s",
            (updated_house["grid_rows"], updated_house["grid_cols"], garden_id),
        )
        if shademap is None:
            set_shademap_state(db, default_shademap_state(), garden_id=garden_id)
        else:
            set_shademap_state(
                db,
                {
                    "mode": shademap["mode"],
                    "selected_plot_id": shademap.get("selected_plot_id"),
                    "analysis_timestamp_ms": _coerce_required_int(
                        shademap["analysis_timestamp_ms"],
                    ),
                    "preset": shademap["preset"],
                },
                garden_id=garden_id,
            )
        if shademap_calibration is not None:
            set_shademap_calibration(db, shademap_calibration, garden_id=garden_id)
        if shademap_obstacles is not None:
            replace_shademap_obstacles(db, shademap_obstacles, garden_id=garden_id)
        if map_objects is None:
            _ensure_garden_map_objects_fit_grid(
                db,
                garden_id=garden_id,
                grid_rows=updated_house["grid_rows"],
                grid_cols=updated_house["grid_cols"],
            )
        replace_map_objects(
            db,
            garden_id=garden_id,
            map_objects=map_objects,
            created_by_user_id=owner_user_id,
        )
        db.execute("DELETE FROM plot_elevations WHERE garden_id = %s", (garden_id,))
        db.execute("DELETE FROM plot_elevation_overrides WHERE garden_id = %s", (garden_id,))
        db.execute(
            "DELETE FROM shademap_cache WHERE garden_id = %s "
            "AND cache_kind IN ('terrain-tile', 'features')",
            (garden_id,),
        )
        if manage_transaction:
            db.commit()
        if media_storage_pairs_out is not None:
            media_storage_pairs_out.extend(media_storage_pairs)
        elif manage_transaction:
            for storage_key, preview_storage_key in media_storage_pairs:
                unlink_storage_keys(storage_key, preview_storage_key)
    except Exception:
        if manage_transaction:
            db.rollback()
        raise
    return len(plots)


def parse_layout_payload(
    raw: object,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[dict[str, Any]] | None,
    list[dict[str, Any]] | None,
]:
    if isinstance(raw, list):
        return cast(list[dict[str, Any]], raw), None, None, None, None, None
    if isinstance(raw, dict):
        payload = cast(dict[str, object], raw)
        schema_version = payload.get("schema_version", 1)
        if schema_version != 1:
            raise HTTPException(status_code=400, detail="Unsupported layout schema version")
        plots = payload.get("plots")
        house = payload.get("house")
        shademap = payload.get("shademap")
        shademap_calibration = payload.get("shademap_calibration")
        shademap_obstacles = payload.get("shademap_obstacles")
        map_objects = payload.get("map_objects")
        if isinstance(plots, list):
            return (
                cast(list[dict[str, Any]], plots),
                cast(dict[str, Any], house) if isinstance(house, dict) else None,
                cast(dict[str, Any], shademap) if isinstance(shademap, dict) else None,
                cast(dict[str, Any], shademap_calibration)
                if isinstance(shademap_calibration, dict)
                else None,
                cast(list[dict[str, Any]], shademap_obstacles)
                if isinstance(shademap_obstacles, list)
                else None,
                cast(list[dict[str, Any]], map_objects) if isinstance(map_objects, list) else None,
            )
    raise HTTPException(status_code=400, detail="Invalid layout payload")


@app.get("/api/layout-state")
def get_layout_state_api(db: DB, request: Request) -> dict[str, int]:
    return get_layout_state(db, _active_garden_id(request))


@app.patch("/api/layout-state")
def update_layout_state(body: LayoutStateBody, db: DB, request: Request) -> dict[str, int]:
    garden_id = _active_garden_id(request)
    _ensure_garden_plots_fit_grid(
        db,
        garden_id=garden_id,
        grid_rows=body.grid_rows,
        grid_cols=body.grid_cols,
    )
    _ensure_garden_map_objects_fit_grid(
        db,
        garden_id=garden_id,
        grid_rows=body.grid_rows,
        grid_cols=body.grid_cols,
    )
    updated = set_layout_state(db, body.model_dump(), garden_id=garden_id)
    db.execute(
        "UPDATE gardens SET grid_rows = %s, grid_cols = %s WHERE id = %s",
        (updated["grid_rows"], updated["grid_cols"], garden_id),
    )
    db.execute(
        "DELETE FROM shademap_cache WHERE garden_id = %s AND cache_kind = 'terrain-tile'",
        (garden_id,),
    )
    db.commit()
    return updated


@app.post("/api/snapshots", status_code=201)
def save_snapshot(body: SnapshotBody, db: DB, request: Request) -> dict:
    context = resolve_request_auth_context(request)
    if context.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    garden_id = _active_garden_id(request)
    data = snapshot_layout(
        db,
        garden_id,
        include_unowned=_is_local_admin_fallback(context),
    )
    db.execute(
        "INSERT INTO layout_snapshots (public_id, name, data, garden_id) VALUES (%s, %s, %s, %s)",
        (_generate_public_id("snap"), body.name, data, garden_id),
    )
    db.commit()
    return {"status": "ok"}


@app.get("/api/snapshots")
def list_snapshots(db: DB, request: Request) -> list[dict]:
    if resolve_request_auth_context(request).role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    garden_id = _active_garden_id(request)
    rows = db.execute(
        """
        SELECT public_id AS id, name, created_at
        FROM layout_snapshots
        WHERE garden_id = %s
        ORDER BY created_at DESC
        """,
        (garden_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    snapshot_id: str,
    db: DB,
    request: Request,
    body: AdminActionBody | None = Body(default=None),
) -> dict:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason if body else "",
    )
    garden_id = _active_garden_id(request)
    owner_user_id = _owner_user_for_garden(
        db,
        garden_id=garden_id,
        preferred_user_id=context.user_id,
    )
    row = db.execute(
        "SELECT data FROM layout_snapshots WHERE public_id = %s AND garden_id = %s",
        (snapshot_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot not found")
    (
        plots,
        house,
        shademap,
        shademap_calibration,
        shademap_obstacles,
        map_objects,
    ) = parse_layout_payload(json.loads(row["data"]))
    if _shademap_import_fields_present(
        shademap=shademap,
        shademap_calibration=shademap_calibration,
        shademap_obstacles=shademap_obstacles,
    ):
        _require_shademap_import_allowed(
            context.subscription_tier,
            detail="ShadeMap snapshot restore fields require the shade_map feature",
        )
    count = restore_snapshot_data(
        db,
        plots,
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        house=house,
        shademap=shademap,
        shademap_calibration=shademap_calibration,
        shademap_obstacles=shademap_obstacles,
        map_objects=map_objects,
    )
    request.state.audited_by_handler = True
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=200,
        remote_host=request.client.host if request.client else "",
        detail=json.dumps(
            {
                "event": "layout.snapshot.restore",
                "snapshot_id": snapshot_id,
                "garden_id": garden_id,
                "action_reason": action_reason,
                "plots": count,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        auth_context=context,
        db=db,
    )
    notify_garden_modified()
    return {"status": "ok", "plots": count}


@app.delete("/api/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str, db: DB, request: Request) -> dict:
    context, action_reason = enforce_destructive_admin_controls(request)
    garden_id = _active_garden_id(request)
    db.execute(
        "DELETE FROM layout_snapshots WHERE public_id = %s AND garden_id = %s",
        (snapshot_id, garden_id),
    )
    db.commit()
    request.state.audited_by_handler = True
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=200,
        remote_host=request.client.host if request.client else "",
        detail=json.dumps(
            {
                "event": "layout.snapshot.delete",
                "snapshot_id": snapshot_id,
                "garden_id": garden_id,
                "action_reason": action_reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        auth_context=context,
        db=db,
    )
    return {"status": "ok"}


@app.get("/api/plots/export")
def export_plots(db: DB, request: Request) -> Response:
    context = resolve_request_auth_context(request)
    if context.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    data = snapshot_layout(
        db,
        _active_garden_id(request),
        include_unowned=_is_local_admin_fallback(context),
    )
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={app_slug()}-map.json",
        },
    )


@app.post("/api/plots/import")
def import_plots(body: ImportBody, db: DB, request: Request) -> dict:
    context, action_reason = enforce_destructive_admin_controls(request)
    garden_id = _active_garden_id(request)
    if _shademap_import_fields_present(
        shademap=body.shademap,
        shademap_calibration=body.shademap_calibration,
        shademap_obstacles=body.shademap_obstacles,
    ):
        _require_shademap_import_allowed(
            context.subscription_tier,
            detail="ShadeMap import fields require the shade_map feature",
        )
    owner_user_id = _owner_user_for_garden(
        db,
        garden_id=garden_id,
        preferred_user_id=context.user_id,
    )
    count = restore_snapshot_data(
        db,
        [item.model_dump() for item in body.plots],
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        house=body.house.model_dump() if body.house else None,
        shademap=body.shademap.model_dump() if body.shademap else None,
        shademap_calibration=(
            body.shademap_calibration.model_dump() if body.shademap_calibration else None
        ),
        shademap_obstacles=(
            [item.model_dump() for item in body.shademap_obstacles]
            if body.shademap_obstacles is not None
            else None
        ),
        map_objects=(
            [item.model_dump() for item in body.map_objects]
            if body.map_objects is not None
            else None
        ),
    )
    notify_garden_modified()
    record_security_event("destructive_admin_actions")
    record_security_event("destructive_admin_actions_import_plots")
    request.state.audited_by_handler = True
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=200,
        remote_host=request.client.host if request.client else "",
        detail=(
            "app.plots.import "
            + json.dumps(
                {
                    "action_reason": action_reason,
                    "garden_id": garden_id,
                    "plots": count,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        auth_context=context,
        db=db,
    )
    return {"status": "ok", "plots": count}


_CSV_FORMULA_PREFIXES = {"=", "+", "-", "@", "\t", "\r", "\n"}


def _sanitize_csv_value(value: str) -> str:
    """Prevent CSV formula injection by prefixing dangerous values with a single quote."""
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return f"'{value}"
    return value


@app.get("/api/plants/export-csv")
def export_plants_csv(db: DB, request: Request) -> Response:
    context = resolve_request_auth_context(request)
    garden_id = _active_garden_id(request)
    scope_sql, scope_params = _plant_scope_sql(context)
    rows = db.execute(
        (
            """
            SELECT DISTINCT p.*
            FROM plants p
            LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE 1=1
            """
            + scope_sql
            + """
            ORDER BY p.name
            """
        ),
        scope_params,
    ).fetchall()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PLANT_CSV_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        plant = dict(row)
        assignment_rows = _assignment_rows_for_plant(
            db,
            plt_id=str(plant["plt_id"]),
            garden_id=garden_id,
        )
        export_row: dict[str, object | str] = {}
        for column in PLANT_CSV_EXPORT_COLUMNS:
            if column == PLANT_ASSIGNMENTS_COLUMN:
                export_row[column] = _sanitize_csv_value(
                    _serialize_plot_assignments(assignment_rows),
                )
                continue
            value = plant.get(column)
            export_row[column] = _sanitize_csv_value("" if value is None else str(value))
        writer.writerow(export_row)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={app_slug()}-plants.csv"},
    )


if DIST.exists():
    app.mount(
        "/assets",
        GZipMiddleware(StaticFiles(directory=DIST / "assets"), minimum_size=1024),
        name="static-assets",
    )
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")
