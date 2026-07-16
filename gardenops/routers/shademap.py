"""Backend-managed ShadeMap config, cache, and upstream proxy routes."""

from __future__ import annotations

import csv
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import numpy as np
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import Request as FastAPIRequest

import gardenops.db as db_module
from gardenops.branding import app_user_agent
from gardenops.db import (
    DB,
    SHADEMAP_MODES,
    SHADEMAP_OBSTACLE_KINDS,
    SHADEMAP_PRESETS,
    DbConn,
    current_timestamp_ms,
    db_dep,
    default_shademap_calibration,
    default_shademap_state,
    return_db,
)
from gardenops.e2e_fixture import complete_journey_loopback_fixture_enabled
from gardenops.models import (
    ShadeMapCalibrationBody,
    ShadeMapObstacleBody,
    ShadeMapStateBody,
    StrictBaseModel,
)
from gardenops.observability import observability_extra
from gardenops.provider_settings import get_shademap_api_key
from gardenops.rate_limit import (
    acquire_concurrency_slot,
    enforce_layered_rate_limit,
    env_int,
    env_nonneg_int,
    provider_limit_profile,
    reserve_daily_provider_budget,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.security_metrics import record_security_event
from gardenops.services.lidar_terrain import (
    LocalTerrainDataset,
    decode_terrarium_png,
    encode_terrarium_png,
    local_terrain_available,
    local_terrain_signature,
    restore_dataset,
    sample_elevations_wgs84,
    sample_local_terrain_tile,
    serialize_dataset,
    terrain_path_for_signature,
)

router = APIRouter()
asset_router = APIRouter()
logger = logging.getLogger(__name__)


DEFAULT_LATITUDE: Final[float] = 51.50095
DEFAULT_LONGITUDE: Final[float] = -0.12448
DEFAULT_ZOOM: Final[int] = 17
DEFAULT_LABEL: Final[str] = "House"
DEFAULT_SHARE_URL: Final[str] = (
    "https://shademap.app/@51.50095,-0.12451,16.18754z,0t,0b,0p,0m,RGVtbyBIb3VzZQ!51.50095!-0.12448"
)
DEFAULT_TERRAIN_URL_TEMPLATE: Final[str] = (
    "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
)
DEFAULT_OVERPASS_URL: Final[str] = "https://overpass-api.de/api/interpreter"
DEFAULT_OVERPASS_URLS: Final[tuple[str, ...]] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
SDK_LOAD_URL: Final[str] = "https://shademap.app/sdk/load"
_LOOPBACK_PROVIDER_ENV: Final[str] = "GARDENOPS_E2E_LOOPBACK_PROVIDER"
_LOOPBACK_PROVIDER_URL_ENV: Final[str] = "GARDENOPS_E2E_PROVIDER_URL"
_LOOPBACK_SDK_LOAD_PATH: Final[str] = "/shademap/sdk/load"
_LOOPBACK_RUNTIME_SCRIPT_PATH: Final[str] = "/shademap/runtime.js"
UPSTREAM_MAX_BYTES: Final[int] = 5 * 1024 * 1024
_ALLOWED_UPSTREAM_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    "shademap.app",
    "s3.amazonaws.com",
    "amazonaws.com",
    "overpass-api.de",
    "overpass.kumi.systems",
    "overpass.private.coffee",
)
SDK_CACHE_TTL_MS: Final[int] = 12 * 60 * 60 * 1000
RUNTIME_SCRIPT_CACHE_TTL_MS: Final[int] = 5 * 60 * 1000
FEATURE_CACHE_TTL_MS: Final[int] = 7 * 24 * 60 * 60 * 1000
FEATURE_CACHE_MAX_ROWS: Final[int] = 4000
TERRAIN_MAX_ZOOM: Final[int] = 15
TERRAIN_ROUTE_MAX_ZOOM: Final[int] = 22
TERRAIN_TILE_SIZE: Final[int] = 256
FEATURES_MIN_ZOOM: Final[int] = 15
DEFAULT_TILE_TOKEN_TTL_SECONDS: Final[int] = 10 * 60
MIN_TILE_TOKEN_TTL_SECONDS: Final[int] = 60
MAX_TILE_TOKEN_TTL_SECONDS: Final[int] = 60 * 60
TERRAIN_CACHE_MAX_ROWS: Final[int] = 20000
SDK_CACHE_MAX_ROWS: Final[int] = 64
DEFAULT_BUILDING_HEIGHT_METERS: Final[float] = 3.0
DEFAULT_HOUSE_HEIGHT_METERS: Final[float] = 9.0
DEFAULT_TREE_HEIGHT_METERS: Final[float] = 4.5

_RUNTIME_SCRIPT_CACHE_LOCK = threading.Lock()
_RUNTIME_SCRIPT_CACHE: dict[str, tuple[int, bytes, str]] = {}
MIN_TREE_CANOPY_RADIUS_METERS: Final[float] = 1.2
MAX_TREE_CANOPY_RADIUS_METERS: Final[float] = 3.5
TREE_CANOPY_SIDES: Final[int] = 8
EARTH_RADIUS_METERS: Final[float] = 6378137.0
NUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"-?\d+(\.\d+)?")
MONTH_LABELS: Final[tuple[str, ...]] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
MONTHLY_ESTIMATE_CSV_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "soltider_estimated.csv"
)
_E2E_MONTHLY_ESTIMATE_CSV_ENV: Final[str] = "GARDENOPS_E2E_SHADEMAP_ESTIMATE_CSV"
_E2E_ARTIFACT_DIR_ENV: Final[str] = "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR"
_E2E_MONTHLY_ESTIMATE_FILENAME: Final[str] = "phase-seven-sun.csv"
_CARDINALITY_LOCK = threading.Lock()
_DISTINCT_SIGNATURES: dict[str, dict[str, float]] = {}


def _active_garden_id(request: FastAPIRequest) -> int:
    context = _auth_context(request)
    if context.garden_id is None:
        raise HTTPException(status_code=500, detail="Missing garden context")
    return int(context.garden_id)


def reset_shademap_abuse_tracking() -> None:
    with _CARDINALITY_LOCK:
        _DISTINCT_SIGNATURES.clear()


def _read_api_key(
    request: FastAPIRequest | None = None,
    db: DbConn | None = None,
) -> str:
    del request
    return get_shademap_api_key(db) or ""


def _read_public_api_key(
    request: FastAPIRequest | None = None,
    db: DbConn | None = None,
) -> str:
    del request, db
    for name in ("SHADEMAP_PUBLIC_API_KEY", "SHADEMAP_PUBLIC_KEY", "SHADEMAP_CLIENT_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _read_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _terrain_source_template() -> str:
    template = os.environ.get("SHADEMAP_TERRAIN_URL_TEMPLATE", "").strip()
    return template or DEFAULT_TERRAIN_URL_TEMPLATE


def _overpass_urls() -> tuple[str, ...]:
    explicit = os.environ.get("SHADEMAP_OVERPASS_URL", "").strip()
    if explicit:
        return (explicit,)
    multi = os.environ.get("SHADEMAP_OVERPASS_URLS", "").strip()
    if multi:
        urls = tuple(candidate.strip() for candidate in multi.split(",") if candidate.strip())
        if urls:
            return urls
    return DEFAULT_OVERPASS_URLS


_PUBLIC_TILE_SECRET_PLACEHOLDERS = {"change-me", "<generate-a-unique-random-secret>"}


def _tile_signing_secret() -> str:
    value = os.environ.get("SHADEMAP_TILE_SIGNING_SECRET", "").strip()
    if value and value.lower() not in _PUBLIC_TILE_SECRET_PLACEHOLDERS:
        return value
    raise RuntimeError("SHADEMAP_TILE_SIGNING_SECRET not configured")


def _cache_get(
    db: DbConn,
    garden_id: int,
    cache_kind: str,
    cache_key: str,
) -> dict[str, Any] | None:
    return db.execute(
        """
        SELECT fetched_at_ms, content_type, payload_text, payload_blob
        FROM shademap_cache
        WHERE garden_id = %s AND cache_kind = %s AND cache_key = %s
        """,
        (garden_id, cache_kind, cache_key),
    ).fetchone()


def _cache_put(
    db: DbConn,
    garden_id: int,
    cache_kind: str,
    cache_key: str,
    *,
    content_type: str,
    payload_text: str | None = None,
    payload_blob: bytes | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO shademap_cache (
            garden_id, cache_kind, cache_key,
            fetched_at_ms, content_type,
            payload_text, payload_blob
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(cache_kind, cache_key) DO UPDATE SET
            garden_id = excluded.garden_id,
            fetched_at_ms = excluded.fetched_at_ms,
            content_type = excluded.content_type,
            payload_text = excluded.payload_text,
            payload_blob = excluded.payload_blob
        """,
        (
            garden_id,
            cache_kind,
            cache_key,
            current_timestamp_ms(),
            content_type,
            payload_text,
            payload_blob,
        ),
    )
    _prune_cache(db, garden_id, cache_kind)


def _prune_cache(db: DbConn, garden_id: int, cache_kind: str) -> None:
    """Bound cache growth and drop stale rows per cache kind."""
    if cache_kind == "terrain-tile":
        ttl_ms = FEATURE_CACHE_TTL_MS
        max_rows = TERRAIN_CACHE_MAX_ROWS
    elif cache_kind == "features":
        ttl_ms = FEATURE_CACHE_TTL_MS
        max_rows = FEATURE_CACHE_MAX_ROWS
    else:
        ttl_ms = SDK_CACHE_TTL_MS
        max_rows = SDK_CACHE_MAX_ROWS

    cutoff_ms = current_timestamp_ms() - ttl_ms
    db.execute(
        "DELETE FROM shademap_cache"
        " WHERE garden_id = %s AND cache_kind = %s"
        " AND fetched_at_ms < %s",
        (garden_id, cache_kind, cutoff_ms),
    )
    db.execute(
        """
        DELETE FROM shademap_cache
        WHERE garden_id = %s
          AND cache_kind = %s
          AND cache_key IN (
              SELECT cache_key
              FROM shademap_cache
              WHERE garden_id = %s AND cache_kind = %s
              ORDER BY fetched_at_ms DESC
              OFFSET %s
          )
        """,
        (garden_id, cache_kind, garden_id, cache_kind, max_rows),
    )


def _cache_fresh(row: dict[str, Any] | None, ttl_ms: int) -> bool:
    if not row:
        return False
    fetched_at_ms = int(row["fetched_at_ms"])
    return current_timestamp_ms() - fetched_at_ms <= ttl_ms


def _load_grid_from_db(signature: str) -> LocalTerrainDataset | None:
    """Load cached LiDAR grid from DB if signature matches."""
    conn = db_module.get_db()
    try:
        row = conn.execute(
            "SELECT * FROM lidar_grid_cache WHERE id = 1 AND signature = %s",
            (signature,),
        ).fetchone()
        if not row:
            return None
        path = terrain_path_for_signature(signature)
        if not path:
            return None
        return restore_dataset(dict(row), path, signature)
    finally:
        return_db(conn)


def _save_grid_to_db(signature: str, dataset: LocalTerrainDataset) -> None:
    """Persist parsed LiDAR grid to DB for fast restarts."""
    data = serialize_dataset(dataset)
    conn = db_module.get_db()
    try:
        conn.execute(
            """
            INSERT INTO lidar_grid_cache (
                id, signature, grid_blob, grid_rows, grid_cols,
                min_x, max_x, min_y, max_y, resolution_m, crs_wkt
            )
            VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                signature = excluded.signature,
                grid_blob = excluded.grid_blob,
                grid_rows = excluded.grid_rows,
                grid_cols = excluded.grid_cols,
                min_x = excluded.min_x,
                max_x = excluded.max_x,
                min_y = excluded.min_y,
                max_y = excluded.max_y,
                resolution_m = excluded.resolution_m,
                crs_wkt = excluded.crs_wkt
            """,
            (
                signature,
                data["grid_blob"],
                data["grid_rows"],
                data["grid_cols"],
                data["min_x"],
                data["max_x"],
                data["min_y"],
                data["max_y"],
                data["resolution_m"],
                data["crs_wkt"],
            ),
        )
        conn.commit()
    finally:
        return_db(conn)


def _tile_token(*, garden_id: int) -> tuple[str, int]:
    expires_at_ms = current_timestamp_ms() + _tile_token_ttl_ms()
    payload = f"{expires_at_ms}:{garden_id}"
    signature = hmac.new(
        _tile_signing_secret().encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hexdigest()
    return f"{payload}.{signature}", expires_at_ms


def _tile_token_ttl_ms() -> int:
    seconds = env_int("SHADEMAP_TILE_TOKEN_TTL_SECONDS", DEFAULT_TILE_TOKEN_TTL_SECONDS)
    seconds = max(MIN_TILE_TOKEN_TTL_SECONDS, min(seconds, MAX_TILE_TOKEN_TTL_SECONDS))
    return seconds * 1000


def _validate_tile_token(token: str, *, garden_id: int) -> int:
    payload, separator, signature = token.partition(".")
    if not payload or not separator or not signature:
        raise HTTPException(status_code=401, detail="Invalid ShadeMap terrain token")
    parts = payload.split(":", 1)
    if len(parts) != 2:
        # Legacy format without garden_id — reject
        raise HTTPException(status_code=401, detail="Invalid ShadeMap terrain token")
    try:
        expires_at_ms = int(parts[0])
        token_garden_id = int(parts[1])
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid ShadeMap terrain token") from exc
    if token_garden_id != garden_id:
        raise HTTPException(status_code=401, detail="Invalid ShadeMap terrain token")
    expected = hmac.new(
        _tile_signing_secret().encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid ShadeMap terrain token")
    if expires_at_ms < current_timestamp_ms():
        raise HTTPException(status_code=401, detail="ShadeMap terrain token expired")
    return expires_at_ms


def _terrain_tile_response_headers(*, token_expires_at_ms: int) -> dict[str, str]:
    remaining_seconds = max(0, int((token_expires_at_ms - current_timestamp_ms()) / 1000))
    max_age = min(remaining_seconds, 300)
    return {"Cache-Control": f"private, max-age={max_age}"}


def _sdk_cache_key(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _host_allowed(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _ALLOWED_UPSTREAM_HOST_SUFFIXES
    )


def _reject_non_public_host(hostname: str, port: int) -> None:
    try:
        addrinfo = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=502, detail="ShadeMap upstream host could not resolve"
        ) from exc
    if not addrinfo:
        raise HTTPException(status_code=502, detail="ShadeMap upstream host could not resolve")
    for family, _, _, _, sockaddr in addrinfo:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            raise HTTPException(status_code=502, detail="ShadeMap upstream host is not public")


def _validate_upstream_url(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid ShadeMap upstream URL") from exc
    if parsed.scheme.lower() != "https":
        raise HTTPException(status_code=502, detail="ShadeMap upstream URL must use https")
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=502,
            detail="ShadeMap upstream URL must not contain credentials",
        )
    hostname = parsed.hostname or ""
    if not hostname or not _host_allowed(hostname):
        raise HTTPException(status_code=502, detail="ShadeMap upstream host is not allowed")
    port = parsed.port
    if port is not None and port != 443:
        raise HTTPException(status_code=502, detail="ShadeMap upstream port is not allowed")
    _reject_non_public_host(hostname, port or 443)
    return raw_url


def _loopback_sdk_validation_url() -> str | None:
    """Return the strict, test-only ShadeMap fixture endpoint when opted in."""
    origin = _loopback_provider_origin()
    if origin is None:
        return None

    return f"{origin}{_LOOPBACK_SDK_LOAD_PATH}"


def _loopback_provider_origin() -> str | None:
    """Return the runner-bound local provider origin when explicitly enabled."""
    if not complete_journey_loopback_fixture_enabled():
        return None

    raw_url = os.environ.get(_LOOPBACK_PROVIDER_URL_ENV, "").strip()
    if not raw_url:
        raise HTTPException(status_code=503, detail="Invalid ShadeMap loopback fixture URL")
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Invalid ShadeMap loopback fixture URL",
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or port <= 0
        or port == 5432
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/v1"
        or parsed.query
        or parsed.fragment
        or "?" in raw_url
        or "#" in raw_url
    ):
        raise HTTPException(status_code=503, detail="Invalid ShadeMap loopback fixture URL")
    return f"http://127.0.0.1:{port}"


def _runtime_script_upstream_url() -> str | None:
    """Return the allowlisted licensed ShadeMap runtime script, if configured."""
    loopback_origin = _loopback_provider_origin()
    if loopback_origin is not None:
        return f"{loopback_origin}{_LOOPBACK_RUNTIME_SCRIPT_PATH}"
    raw_url = os.environ.get("SHADEMAP_RUNTIME_SCRIPT_URL", "").strip()
    if not raw_url:
        return None
    return _validate_upstream_url(raw_url)


def _request_validated_bytes(
    safe_url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[bytes, str]:
    """Request an already-validated upstream URL."""
    request_headers = {
        "User-Agent": app_user_agent("shademap-client"),
        **(headers or {}),
    }
    request = Request(safe_url, data=body, headers=request_headers, method=method)
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            content_type = response.headers.get_content_type() or "application/octet-stream"
            payload = response.read(UPSTREAM_MAX_BYTES + 1)
            if len(payload) > UPSTREAM_MAX_BYTES:
                raise HTTPException(status_code=502, detail="ShadeMap upstream response too large")
            return payload, content_type
    except HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            record_security_event("shademap_upstream_failures")
            raise HTTPException(status_code=502, detail="ShadeMap upstream redirected") from exc
        logger.warning(
            "ShadeMap upstream HTTP error: %s",
            exc.reason,
            extra=observability_extra(
                error_kind="upstream_failure",
                upstream="shademap",
                feature_area="shademap-upstream",
            ),
            exc_info=True,
        )
        record_security_event("shademap_upstream_failures")
        raise HTTPException(status_code=502, detail="ShadeMap upstream request failed") from exc
    except URLError as exc:
        logger.warning(
            "ShadeMap upstream URL error: %s",
            exc.reason,
            extra=observability_extra(
                error_kind="upstream_failure",
                upstream="shademap",
                feature_area="shademap-upstream",
            ),
            exc_info=True,
        )
        record_security_event("shademap_upstream_failures")
        raise HTTPException(status_code=502, detail="ShadeMap upstream request failed") from exc
    except TimeoutError as exc:
        logger.warning(
            "ShadeMap upstream request timed out",
            extra=observability_extra(
                error_kind="upstream_failure",
                upstream="shademap",
                feature_area="shademap-upstream",
            ),
            exc_info=True,
        )
        record_security_event("shademap_upstream_failures")
        raise HTTPException(status_code=502, detail="Request timed out") from exc


def _request_bytes(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> tuple[bytes, str]:
    safe_url = _validate_upstream_url(url)
    return _request_validated_bytes(
        safe_url,
        method=method,
        body=body,
        headers=headers,
        timeout=timeout,
    )


def _validate_remote_terrain_content_type(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "image/png":
        raise HTTPException(
            status_code=502,
            detail="ShadeMap terrain upstream returned a non-PNG tile",
        )
    return media_type


def _parse_decimal(raw: str) -> float | None:
    value = raw.strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _monthly_estimate_csv_path() -> Path:
    """Use a generated sun-data fixture only inside the explicit loopback E2E mode."""
    if not complete_journey_loopback_fixture_enabled():
        return MONTHLY_ESTIMATE_CSV_PATH

    raw_path = os.environ.get(_E2E_MONTHLY_ESTIMATE_CSV_ENV, "")
    raw_artifact_dir = os.environ.get(_E2E_ARTIFACT_DIR_ENV, "")
    if not raw_path or not raw_artifact_dir:
        return MONTHLY_ESTIMATE_CSV_PATH

    candidate = Path(raw_path)
    artifact_dir = Path(raw_artifact_dir)
    if (
        not candidate.is_absolute()
        or candidate.name != _E2E_MONTHLY_ESTIMATE_FILENAME
        or candidate.is_symlink()
        or artifact_dir.is_symlink()
    ):
        return MONTHLY_ESTIMATE_CSV_PATH
    try:
        resolved_artifact_dir = artifact_dir.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except OSError:
        return MONTHLY_ESTIMATE_CSV_PATH
    if (
        not resolved_artifact_dir.is_dir()
        or candidate.parent != resolved_artifact_dir
        or resolved_candidate.parent != resolved_artifact_dir
        or not resolved_candidate.is_file()
    ):
        return MONTHLY_ESTIMATE_CSV_PATH
    return resolved_candidate


def _load_monthly_estimated_sun() -> dict[str, object]:
    csv_path = _monthly_estimate_csv_path()
    if not csv_path.exists():
        raise HTTPException(status_code=503, detail="soltider_estimated.csv not found")

    monthly_totals = [0.0] * 12
    monthly_counts = [0] * 12
    source_dates: list[str] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            date_value = str(row.get("dato", "")).strip()
            hours_raw = str(row.get("timer_sol", "")).strip()
            if not date_value:
                continue
            try:
                month_index = int(date_value.split("-")[1]) - 1
            except (IndexError, ValueError) as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Invalid soltider_estimated.csv date format",
                ) from exc
            if month_index < 0 or month_index >= 12:
                raise HTTPException(
                    status_code=502,
                    detail="Invalid soltider_estimated.csv month value",
                )
            hours = _parse_decimal(hours_raw)
            if hours is None:
                continue
            monthly_totals[month_index] += hours
            monthly_counts[month_index] += 1
            source_dates.append(date_value)

    if not source_dates:
        raise HTTPException(
            status_code=503,
            detail="soltider_estimated.csv contains no usable rows",
        )

    values = []
    for month_index, label in enumerate(MONTH_LABELS):
        count = monthly_counts[month_index]
        hours = monthly_totals[month_index] / count if count else 0.0
        values.append(
            {
                "month": month_index + 1,
                "month_label": label,
                "hours": hours,
                "sample_days": count,
            },
        )

    return {
        "source_name": csv_path.name,
        "source_date_start": min(source_dates),
        "source_date_end": max(source_dates),
        "values": values,
    }


def _perform_sdk_validation(api_key: str) -> None:
    payload = json.dumps({"api_key": api_key}).encode("utf-8")
    try:
        loopback_url = _loopback_sdk_validation_url()
        if loopback_url is None:
            _request_bytes(
                SDK_LOAD_URL,
                method="POST",
                body=payload,
                headers={"Content-Type": "application/json"},
                timeout=15.0,
            )
        else:
            _request_validated_bytes(
                loopback_url,
                method="POST",
                body=payload,
                headers={"Content-Type": "application/json"},
                timeout=15.0,
            )
    except HTTPException as exc:
        status = 503 if "invalid" in exc.detail.lower() or "key" in exc.detail.lower() else 502
        raise HTTPException(
            status_code=status,
            detail=f"ShadeMap API validation failed: {exc.detail}",
        ) from exc


def _ensure_sdk_ready(
    db: DbConn,
    garden_id: int,
    request: FastAPIRequest | None = None,
) -> tuple[str, str]:
    api_key = _read_api_key(request, db)
    if not api_key:
        raise HTTPException(status_code=503, detail="SHADEMAP API key not configured")

    cache_key = _sdk_cache_key(api_key)
    cached = _cache_get(db, garden_id, "sdk-load", cache_key)
    if _cache_fresh(cached, SDK_CACHE_TTL_MS):
        return "ready", "hit"

    try:
        _perform_sdk_validation(api_key)
    except HTTPException as exc:
        if exc.status_code == 502 and cached and cached["payload_text"] == "ok":
            logger.warning(
                "ShadeMap SDK validation failed; using stale validated cache",
                extra=observability_extra(
                    error_kind="stale_cache_fallback",
                    upstream="shademap",
                    feature_area="shademap-sdk",
                ),
            )
            return "degraded", "stale-fallback"
        raise
    _cache_put(
        db,
        garden_id,
        "sdk-load",
        cache_key,
        content_type="text/plain",
        payload_text="ok",
    )
    db.commit()
    return "ready", "miss"


def _override_signature(db: DbConn, garden_id: int) -> str:
    """Hash current elevation overrides for cache key differentiation."""
    rows = db.execute(
        """
        SELECT plot_id, elevation_m
        FROM plot_elevation_overrides
        WHERE garden_id = %s
        ORDER BY plot_id
        """,
        (garden_id,),
    ).fetchall()
    if not rows:
        return ""
    parts = [f"{row['plot_id']}:{row['elevation_m']}" for row in rows]
    return sha256("|".join(parts).encode()).hexdigest()[:12]


def _house_terrain_signature(db: DbConn, garden_id: int) -> str:
    """Hash house layout + height for terrain cache key."""
    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return ""
    height_m = _read_float(
        "SHADEMAP_HOUSE_HEIGHT_METERS",
        DEFAULT_HOUSE_HEIGHT_METERS,
    )
    parts = [
        str(ctx.house_row),
        str(ctx.house_col),
        str(ctx.house_width),
        str(ctx.house_height),
        str(ctx.north_degrees),
        f"{height_m:.2f}",
    ]
    if ctx.calibration:
        parts.extend(f"{ctx.calibration[k]:.8f}" for k in sorted(ctx.calibration))
    return sha256("|".join(parts).encode()).hexdigest()[:12]


def _terrain_cache_key(
    z: int,
    x: int,
    y: int,
    garden_id: int,
    override_sig: str = "",
    house_sig: str = "",
) -> str:
    local_sig = local_terrain_signature(garden_id)
    if local_sig:
        base = f"g:{garden_id}:local:{local_sig}:{z}:{x}:{y}"
        extra = ":".join(s for s in (override_sig, house_sig) if s)
        return f"{base}:{extra}" if extra else base
    base = f"g:{garden_id}:remote:{_terrain_source_template()}:{z}:{x}:{y}"
    extra = ":".join(s for s in (override_sig, house_sig) if s)
    return f"{base}:{extra}" if extra else base


def _terrain_source_url(z: int, x: int, y: int) -> str:
    return _terrain_source_template().format(z=z, x=x, y=y)


def _validate_tile_coords(z: int, x: int, y: int) -> None:
    if z < 0 or z > TERRAIN_ROUTE_MAX_ZOOM:
        raise HTTPException(status_code=404, detail="ShadeMap terrain tile not found")
    tile_count = 1 << z
    if x < 0 or y < 0 or x >= tile_count or y >= tile_count:
        raise HTTPException(status_code=404, detail="ShadeMap terrain tile not found")


def _request_identity(request: FastAPIRequest) -> str:
    auth = request.headers.get("authorization", "").strip()
    api_key = request.headers.get("x-api-key", "").strip()
    host = request.client.host if request.client else "unknown"
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif api_key:
        token = api_key
    if token:
        return f"token:{sha256(token.encode('utf-8')).hexdigest()[:24]}"
    return f"host:{host}"


def _enforce_distinct_signature_budget(
    *,
    request: FastAPIRequest,
    namespace: str,
    signature: str,
    max_unique: int,
    window_seconds: int,
) -> None:
    if max_unique <= 0:
        return
    now = time.monotonic()
    cutoff = now - window_seconds
    identity = _request_identity(request)
    key = f"{namespace}:{identity}"
    with _CARDINALITY_LOCK:
        seen = _DISTINCT_SIGNATURES.setdefault(key, {})
        stale = [sig for sig, ts in seen.items() if ts < cutoff]
        for sig in stale:
            seen.pop(sig, None)
        if signature not in seen and len(seen) >= max_unique:
            raise HTTPException(
                status_code=429,
                detail=f"Too many distinct {namespace} requests in a short window",
            )
        seen[signature] = now


def _lon_to_tile_x(lon: float, zoom: int) -> float:
    n = 2**zoom
    return (lon + 180.0) / 360.0 * n


def _lat_to_tile_y(lat: float, zoom: int) -> float:
    n = 2**zoom
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    return (
        (
            1.0
            - math.log(
                math.tan(lat_rad) + 1.0 / math.cos(lat_rad),
            )
            / math.pi
        )
        / 2.0
        * n
    )


def _bbox_tile_span(
    north: float,
    south: float,
    east: float,
    west: float,
    zoom: int,
) -> int:
    min_x = int(math.floor(_lon_to_tile_x(west, zoom)))
    max_x = int(math.floor(_lon_to_tile_x(east, zoom)))
    min_y = int(math.floor(_lat_to_tile_y(north, zoom)))
    max_y = int(math.floor(_lat_to_tile_y(south, zoom)))
    span_x = max(1, (max_x - min_x) + 1)
    span_y = max(1, (max_y - min_y) + 1)
    return span_x * span_y


def _apply_elevation_overrides(
    elevations: np.ndarray,
    z: int,
    x: int,
    y: int,
    db: DbConn,
    garden_id: int,
) -> np.ndarray:
    """Stamp elevation overrides onto a terrain tile array."""
    overrides = db.execute(
        "SELECT plot_id, elevation_m FROM plot_elevation_overrides WHERE garden_id = %s",
        (garden_id,),
    ).fetchall()
    if not overrides:
        return elevations

    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return elevations

    plots_by_id: dict[str, tuple[int, int]] = {}
    plot_rows = db.execute(
        """
        SELECT p.plot_id, p.grid_row, p.grid_col
        FROM plots p
        JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE po.garden_id = %s AND p.grid_row IS NOT NULL
        """,
        (garden_id,),
    ).fetchall()
    for row in plot_rows:
        plots_by_id[str(row["plot_id"])] = (
            int(row["grid_row"]),
            int(row["grid_col"]),
        )

    tile_size = elevations.shape[0]
    n = 2**z

    result = np.array(elevations, copy=True)
    for override in overrides:
        plot_id = str(override["plot_id"])
        elev = float(override["elevation_m"])
        grid_pos = plots_by_id.get(plot_id)
        if grid_pos is None:
            continue
        center_row = float(grid_pos[0]) - 0.5
        center_col = float(grid_pos[1]) - 0.5
        lat, lng = _grid_point_lat_lng_with_calibration(
            ctx.calibration,
            latitude=ctx.latitude,
            longitude=ctx.longitude,
            house_row=ctx.house_row,
            house_col=ctx.house_col,
            house_width=ctx.house_width,
            house_height=ctx.house_height,
            north_degrees=ctx.north_degrees,
            grid_col=center_col,
            grid_row=center_row,
        )
        tile_x_f = (lng + 180.0) / 360.0 * n
        lat_rad = math.radians(lat)
        tile_y_f = (
            (
                1.0
                - math.log(
                    math.tan(lat_rad) + 1.0 / math.cos(lat_rad),
                )
                / math.pi
            )
            / 2.0
            * n
        )

        px = int((tile_x_f - x) * tile_size)
        py = int((tile_y_f - y) * tile_size)

        for dy in range(-1, 2):
            for dx in range(-1, 2):
                ry = py + dy
                rx = px + dx
                if 0 <= ry < tile_size and 0 <= rx < tile_size:
                    result[ry, rx] = elev

    return result


def _lat_lng_to_tile_pixel(
    lat: float,
    lng: float,
    z: int,
    tile_x: int,
    tile_y: int,
    tile_size: int,
) -> tuple[int, int]:
    """Convert WGS84 lat/lng to pixel coordinates within a tile."""
    n = 2**z
    px = int(((lng + 180.0) / 360.0 * n - tile_x) * tile_size)
    lat_rad = math.radians(lat)
    py = int(
        (
            (
                1.0
                - math.log(
                    math.tan(lat_rad) + 1.0 / math.cos(lat_rad),
                )
                / math.pi
            )
            / 2.0
            * n
            - tile_y
        )
        * tile_size
    )
    return px, py


def _apply_house_to_terrain(
    elevations: np.ndarray,
    z: int,
    x: int,
    y: int,
    db: DbConn,
    garden_id: int,
) -> np.ndarray:
    """Stamp house footprint into terrain as ground + house height.

    Bypasses the SDK building rasterizer by encoding the house
    directly into terrain elevation, ensuring shadows are cast.
    """
    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return elevations

    house_height_m = max(
        1.0,
        _read_float(
            "SHADEMAP_HOUSE_HEIGHT_METERS",
            DEFAULT_HOUSE_HEIGHT_METERS,
        ),
    )

    west_edge = ctx.house_col - 1
    east_edge = west_edge + ctx.house_width
    north_edge = ctx.house_row - 1
    south_edge = north_edge + ctx.house_height

    corners = [
        (west_edge, north_edge),
        (east_edge, north_edge),
        (east_edge, south_edge),
        (west_edge, south_edge),
    ]
    corner_latlngs = [
        _grid_point_lat_lng_with_calibration(
            ctx.calibration,
            latitude=ctx.latitude,
            longitude=ctx.longitude,
            house_row=ctx.house_row,
            house_col=ctx.house_col,
            house_width=ctx.house_width,
            house_height=ctx.house_height,
            north_degrees=ctx.north_degrees,
            grid_col=float(c),
            grid_row=float(r),
        )
        for c, r in corners
    ]

    tile_size = elevations.shape[0]
    pixel_coords = [
        _lat_lng_to_tile_pixel(lat, lng, z, x, y, tile_size) for lat, lng in corner_latlngs
    ]

    pxs = [p[0] for p in pixel_coords]
    pys = [p[1] for p in pixel_coords]
    min_px, max_px = min(pxs), max(pxs)
    min_py, max_py = min(pys), max(pys)

    if max_px < 0 or min_px >= tile_size:
        return elevations
    if max_py < 0 or min_py >= tile_size:
        return elevations

    min_px = max(min_px - 1, 0)
    max_px = min(max_px + 1, tile_size - 1)
    min_py = max(min_py - 1, 0)
    max_py = min(max_py + 1, tile_size - 1)

    result = np.array(elevations, copy=True)
    for py in range(min_py, max_py + 1):
        for px in range(min_px, max_px + 1):
            ground = float(elevations[py, px])
            result[py, px] = ground + house_height_m

    return result


def _house_overlaps_tile(
    z: int,
    tile_x: int,
    tile_y: int,
    db: DbConn,
    garden_id: int,
) -> bool:
    """Check if the house footprint overlaps a terrain tile."""
    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return False

    center_lat, center_lng = _grid_point_lat_lng_with_calibration(
        ctx.calibration,
        latitude=ctx.latitude,
        longitude=ctx.longitude,
        house_row=ctx.house_row,
        house_col=ctx.house_col,
        house_width=ctx.house_width,
        house_height=ctx.house_height,
        north_degrees=ctx.north_degrees,
        grid_col=float(ctx.house_col - 1 + ctx.house_width / 2),
        grid_row=float(ctx.house_row - 1 + ctx.house_height / 2),
    )

    n = 2**z
    cx = int((center_lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(center_lat)
    cy = int(
        (
            1.0
            - math.log(
                math.tan(lat_rad) + 1.0 / math.cos(lat_rad),
            )
            / math.pi
        )
        / 2.0
        * n
    )
    return abs(cx - tile_x) <= 1 and abs(cy - tile_y) <= 1


def _normalized_bounds(
    north: float,
    south: float,
    east: float,
    west: float,
) -> tuple[float, float, float, float]:
    if north <= south:
        raise HTTPException(status_code=400, detail="ShadeMap north bound must exceed south")
    if east <= west:
        raise HTTPException(status_code=400, detail="ShadeMap east bound must exceed west")
    return (
        round(north, 5),
        round(south, 5),
        round(east, 5),
        round(west, 5),
    )


def _features_cache_key(
    north: float,
    south: float,
    east: float,
    west: float,
    zoom: int,
    garden_id: int,
) -> str:
    payload = json.dumps(
        {
            "garden_id": garden_id,
            "north": north,
            "south": south,
            "east": east,
            "west": west,
            "zoom": zoom,
            "sources": _overpass_urls(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _read_layout_state(db: DbConn, garden_id: int) -> dict[str, Any] | None:
    return db.execute(
        """
        SELECT house_row, house_col, house_width, house_height, north_degrees
        FROM layout_state
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()


def _read_shademap_calibration_row(
    db: DbConn,
    garden_id: int,
) -> dict[str, Any] | None:
    return db.execute(
        """
        SELECT
            enabled,
            calibration_type,
            origin_grid_col,
            origin_grid_row,
            origin_latitude,
            origin_longitude,
            axis_grid_col,
            axis_grid_row,
            axis_latitude,
            axis_longitude,
            house_nw_latitude,
            house_nw_longitude,
            house_ne_latitude,
            house_ne_longitude,
            house_se_latitude,
            house_se_longitude,
            house_sw_latitude,
            house_sw_longitude
        FROM shademap_calibration
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()


def _read_tree_rows(
    db: DbConn,
    garden_id: int,
    *,
    allow_unowned: bool = False,
) -> list[dict[str, Any]]:
    if allow_unowned:
        return db.execute(
            """
            SELECT
                p.plot_id,
                p.grid_row,
                p.grid_col,
                MAX(COALESCE(pl.height_cm, 0)) AS max_height_cm,
                SUM(pp.quantity) AS total_quantity
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            JOIN plot_plants pp ON pp.plot_id = p.plot_id
            JOIN plants pl ON pl.plt_id = pp.plt_id
            WHERE (po.garden_id = %s OR po.garden_id IS NULL)
              AND pl.category = 'trær'
              AND p.grid_row IS NOT NULL
            GROUP BY p.plot_id, p.grid_row, p.grid_col
            ORDER BY p.plot_id
            """,
            (garden_id,),
        ).fetchall()
    return db.execute(
        """
        SELECT
            p.plot_id,
            p.grid_row,
            p.grid_col,
            MAX(COALESCE(pl.height_cm, 0)) AS max_height_cm,
            SUM(pp.quantity) AS total_quantity
        FROM plots p
        JOIN plot_ownership po ON po.plot_id = p.plot_id
        JOIN plot_plants pp ON pp.plot_id = p.plot_id
        JOIN plants pl ON pl.plt_id = pp.plt_id
        WHERE po.garden_id = %s AND pl.category = 'trær' AND p.grid_row IS NOT NULL
        GROUP BY p.plot_id, p.grid_row, p.grid_col
        ORDER BY p.plot_id
        """,
        (garden_id,),
    ).fetchall()


def _read_manual_obstacle_rows(
    db: DbConn,
    garden_id: int,
) -> list[dict[str, Any]]:
    return db.execute(
        """
        SELECT
            id,
            label,
            kind,
            linked_plot_id,
            latitude,
            longitude,
            height_m,
            crown_radius_m
        FROM shademap_obstacles
        WHERE garden_id = %s AND active = 1
        ORDER BY id
        """,
        (garden_id,),
    ).fetchall()


def _parse_number(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = NUMERIC_PATTERN.search(str(raw))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _coerce_float(raw: object) -> float:
    return float(cast(int | float | str, raw))


def _coerce_int(raw: object) -> int:
    return int(cast(int | float | str, raw))


def _coordinate_pair(node: Sequence[object]) -> tuple[float, float] | None:
    if len(node) < 2:
        return None
    first = node[0]
    second = node[1]
    if not isinstance(first, (int, float)) or not isinstance(second, (int, float)):
        return None
    return float(first), float(second)


def _feature_height(tags: dict[str, object]) -> float:
    height = _parse_number(tags.get("render_height"))
    if height and height > 0:
        return height
    height = _parse_number(tags.get("height"))
    if height and height > 0:
        return height
    levels = _parse_number(tags.get("building:levels"))
    if levels and levels > 0:
        return max(levels * 3.04, DEFAULT_BUILDING_HEIGHT_METERS)
    return DEFAULT_BUILDING_HEIGHT_METERS


def _closed_ring(points: Sequence[Mapping[str, object]]) -> list[list[float]] | None:
    ring: list[list[float]] = []
    for point in points:
        if "lon" not in point or "lat" not in point:
            continue
        ring.append(
            [
                _coerce_float(point["lon"]),
                _coerce_float(point["lat"]),
            ]
        )
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _geometry_bounds(geometry: Mapping[str, object]) -> tuple[float, float, float, float] | None:
    def walk(node: object) -> list[tuple[float, float]]:
        if isinstance(node, list):
            if pair := _coordinate_pair(node):
                return [pair]
            points: list[tuple[float, float]] = []
            for item in node:
                points.extend(walk(item))
            return points
        return []

    coordinates = geometry.get("coordinates")
    points = walk(coordinates)
    if not points:
        return None
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return (
        max(latitudes),
        min(latitudes),
        max(longitudes),
        min(longitudes),
    )


def _geometry_in_bounds(
    geometry: Mapping[str, object],
    north: float,
    south: float,
    east: float,
    west: float,
) -> bool:
    """Check if geometry falls within viewport bounds."""
    bounds = _geometry_bounds(geometry)
    if not bounds:
        return False
    g_north, g_south, g_east, g_west = bounds
    return not (g_north < south or g_south > north or g_east < west or g_west > east)


def _bounds_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    first_north, first_south, first_east, first_west = first
    second_north, second_south, second_east, second_west = second
    return not (
        first_north < second_south
        or first_south > second_north
        or first_east < second_west
        or first_west > second_east
    )


def _grid_point_lat_lng_from_house_anchor(
    *,
    latitude: float,
    longitude: float,
    house_row: int,
    house_col: int,
    house_width: int,
    house_height: int,
    north_degrees: int,
    grid_col: float,
    grid_row: float,
) -> tuple[float, float]:
    house_center_col = house_col - 0.5 + house_width / 2
    house_center_row = house_row - 0.5 + house_height / 2
    delta_x = grid_col - house_center_col
    delta_y = grid_row - house_center_row
    theta = math.radians(north_degrees)

    east_meters = delta_x * math.cos(theta) - delta_y * math.sin(theta)
    north_meters = -(delta_x * math.sin(theta) + delta_y * math.cos(theta))

    delta_lat = math.degrees(north_meters / EARTH_RADIUS_METERS)
    delta_lng = math.degrees(
        east_meters / (EARTH_RADIUS_METERS * math.cos(math.radians(latitude))),
    )
    return latitude + delta_lat, longitude + delta_lng


def _offset_lat_lng(
    *,
    latitude: float,
    longitude: float,
    east_meters: float,
    north_meters: float,
) -> tuple[float, float]:
    delta_lat = math.degrees(north_meters / EARTH_RADIUS_METERS)
    delta_lng = math.degrees(
        east_meters / (EARTH_RADIUS_METERS * math.cos(math.radians(latitude))),
    )
    return latitude + delta_lat, longitude + delta_lng


def _regular_polygon_ring(
    *,
    latitude: float,
    longitude: float,
    radius_meters: float,
    sides: int = TREE_CANOPY_SIDES,
) -> list[list[float]]:
    ring: list[list[float]] = []
    for index in range(sides):
        angle = (2 * math.pi * index) / sides
        east_meters = math.cos(angle) * radius_meters
        north_meters = math.sin(angle) * radius_meters
        point_lat, point_lng = _offset_lat_lng(
            latitude=latitude,
            longitude=longitude,
            east_meters=east_meters,
            north_meters=north_meters,
        )
        ring.append([point_lng, point_lat])
    ring.append(ring[0])
    return ring


def _meters_per_degree_latitude() -> float:
    return EARTH_RADIUS_METERS * math.pi / 180.0


def _meters_per_degree_longitude(latitude: float) -> float:
    return _meters_per_degree_latitude() * math.cos(math.radians(latitude))


def _lat_lng_to_local_east_north(
    *,
    latitude: float,
    longitude: float,
    reference_latitude: float,
    reference_longitude: float,
) -> tuple[float, float]:
    east = (longitude - reference_longitude) * _meters_per_degree_longitude(reference_latitude)
    north = (latitude - reference_latitude) * _meters_per_degree_latitude()
    return east, north


def _fit_similarity_transform(
    grid_points: np.ndarray,
    world_points: np.ndarray,
    *,
    reference_latitude: float,
    reference_longitude: float,
) -> dict[str, float] | None:
    if grid_points.shape != world_points.shape or grid_points.shape[0] < 2:
        return None

    grid_mean = grid_points.mean(axis=0)
    world_mean = world_points.mean(axis=0)
    centered_grid = grid_points - grid_mean
    centered_world = world_points - world_mean
    grid_variance = float(np.sum(centered_grid * centered_grid) / grid_points.shape[0])
    if grid_variance <= 1e-9:
        return None

    covariance = (centered_world.T @ centered_grid) / grid_points.shape[0]
    u_matrix, singular_values, vt_matrix = np.linalg.svd(covariance)
    sign_matrix = np.eye(2, dtype=np.float64)
    if np.linalg.det(u_matrix) * np.linalg.det(vt_matrix) < 0:
        sign_matrix[-1, -1] = -1
    rotation = u_matrix @ sign_matrix @ vt_matrix
    scale = float(np.trace(np.diag(singular_values) @ sign_matrix) / grid_variance)
    translation = world_mean - scale * (rotation @ grid_mean)

    return {
        "reference_latitude": reference_latitude,
        "reference_longitude": reference_longitude,
        "translation_east_m": float(translation[0]),
        "translation_north_m": float(translation[1]),
        "scale_meters": scale,
        "cos_theta": float(rotation[0, 0]),
        "sin_theta": float(rotation[1, 0]),
    }


def _house_grid_corner_points(layout: dict[str, Any]) -> np.ndarray:
    house_row = float(layout["house_row"])
    house_col = float(layout["house_col"])
    house_width = float(layout["house_width"])
    house_height = float(layout["house_height"])
    west_edge = house_col - 1.0
    east_edge = west_edge + house_width
    north_edge = house_row - 1.0
    south_edge = north_edge + house_height
    return np.array(
        [
            [west_edge, -north_edge],
            [east_edge, -north_edge],
            [east_edge, -south_edge],
            [west_edge, -south_edge],
        ],
        dtype=np.float64,
    )


def _calibration_transform(db: DbConn, garden_id: int) -> dict[str, float] | None:
    row = _read_shademap_calibration_row(db, garden_id)
    if not row or not bool(row["enabled"]):
        return None

    calibration_type = str(row["calibration_type"] or "two-point")
    if calibration_type == "house-corners":
        layout = _read_layout_state(db, garden_id)
        if not layout:
            return None
        try:
            house_points = np.array(
                [
                    [float(row["house_nw_latitude"]), float(row["house_nw_longitude"])],
                    [float(row["house_ne_latitude"]), float(row["house_ne_longitude"])],
                    [float(row["house_se_latitude"]), float(row["house_se_longitude"])],
                    [float(row["house_sw_latitude"]), float(row["house_sw_longitude"])],
                ],
                dtype=np.float64,
            )
        except (
            TypeError,
            ValueError,
        ):
            return None
        reference_latitude = float(np.mean(house_points[:, 0]))
        reference_longitude = float(np.mean(house_points[:, 1]))
        world_points = np.array(
            [
                _lat_lng_to_local_east_north(
                    latitude=float(latitude),
                    longitude=float(longitude),
                    reference_latitude=reference_latitude,
                    reference_longitude=reference_longitude,
                )
                for latitude, longitude in house_points
            ],
            dtype=np.float64,
        )
        return _fit_similarity_transform(
            _house_grid_corner_points(layout),
            world_points,
            reference_latitude=reference_latitude,
            reference_longitude=reference_longitude,
        )

    try:
        origin_grid_col = float(row["origin_grid_col"])
        origin_grid_row = float(row["origin_grid_row"])
        origin_latitude = float(row["origin_latitude"])
        origin_longitude = float(row["origin_longitude"])
        axis_grid_col = float(row["axis_grid_col"])
        axis_grid_row = float(row["axis_grid_row"])
        axis_latitude = float(row["axis_latitude"])
        axis_longitude = float(row["axis_longitude"])
    except (
        TypeError,
        ValueError,
    ):
        return None

    world_points = np.array(
        [
            [0.0, 0.0],
            _lat_lng_to_local_east_north(
                latitude=axis_latitude,
                longitude=axis_longitude,
                reference_latitude=origin_latitude,
                reference_longitude=origin_longitude,
            ),
        ],
        dtype=np.float64,
    )
    grid_points = np.array(
        [
            [origin_grid_col, -origin_grid_row],
            [axis_grid_col, -axis_grid_row],
        ],
        dtype=np.float64,
    )
    return _fit_similarity_transform(
        grid_points,
        world_points,
        reference_latitude=origin_latitude,
        reference_longitude=origin_longitude,
    )


def _grid_point_lat_lng_with_calibration(
    calibration: dict[str, float] | None,
    *,
    latitude: float,
    longitude: float,
    house_row: int,
    house_col: int,
    house_width: int,
    house_height: int,
    north_degrees: int,
    grid_col: float,
    grid_row: float,
) -> tuple[float, float]:
    if calibration:
        north_grid = -grid_row
        east_meters = calibration["translation_east_m"] + calibration["scale_meters"] * (
            grid_col * calibration["cos_theta"] - north_grid * calibration["sin_theta"]
        )
        north_meters = calibration["translation_north_m"] + calibration["scale_meters"] * (
            grid_col * calibration["sin_theta"] + north_grid * calibration["cos_theta"]
        )
        return _offset_lat_lng(
            latitude=calibration["reference_latitude"],
            longitude=calibration["reference_longitude"],
            east_meters=east_meters,
            north_meters=north_meters,
        )

    return _grid_point_lat_lng_from_house_anchor(
        latitude=latitude,
        longitude=longitude,
        house_row=house_row,
        house_col=house_col,
        house_width=house_width,
        house_height=house_height,
        north_degrees=north_degrees,
        grid_col=grid_col,
        grid_row=grid_row,
    )


@dataclass(frozen=True, slots=True)
class GeoContext:
    """Common geographic context for grid-to-lat/lng conversions."""

    layout: dict[str, Any]
    latitude: float
    longitude: float
    house_row: int
    house_col: int
    house_width: int
    house_height: int
    north_degrees: int
    calibration: dict[str, float] | None


def _garden_coordinates(db: DbConn, garden_id: int) -> tuple[float, float]:
    row = db.execute(
        "SELECT latitude, longitude FROM gardens WHERE id = %s LIMIT 1",
        (garden_id,),
    ).fetchone()
    if row and row["latitude"] is not None and row["longitude"] is not None:
        return float(row["latitude"]), float(row["longitude"])
    return (
        _read_float("SHADEMAP_LAT", DEFAULT_LATITUDE),
        _read_float("SHADEMAP_LNG", DEFAULT_LONGITUDE),
    )


def _load_geo_context(db: DbConn, garden_id: int) -> GeoContext | None:
    """Load layout, coordinates, and calibration transform."""
    layout = _read_layout_state(db, garden_id)
    if not layout:
        return None
    latitude, longitude = _garden_coordinates(db, garden_id)
    return GeoContext(
        layout=layout,
        latitude=latitude,
        longitude=longitude,
        house_row=int(layout["house_row"]),
        house_col=int(layout["house_col"]),
        house_width=int(layout["house_width"]),
        house_height=int(layout["house_height"]),
        north_degrees=int(layout["north_degrees"]),
        calibration=_calibration_transform(db, garden_id),
    )


def _planner_house_feature(
    db: DbConn,
    *,
    garden_id: int,
    north: float,
    south: float,
    east: float,
    west: float,
) -> dict[str, object] | None:
    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return None

    west_edge = ctx.house_col - 1
    east_edge = west_edge + ctx.house_width
    north_edge = ctx.house_row - 1
    south_edge = north_edge + ctx.house_height

    nw_lat, nw_lng = _grid_point_lat_lng_with_calibration(
        ctx.calibration,
        latitude=ctx.latitude,
        longitude=ctx.longitude,
        house_row=ctx.house_row,
        house_col=ctx.house_col,
        house_width=ctx.house_width,
        house_height=ctx.house_height,
        north_degrees=ctx.north_degrees,
        grid_col=west_edge,
        grid_row=north_edge,
    )
    ne_lat, ne_lng = _grid_point_lat_lng_with_calibration(
        ctx.calibration,
        latitude=ctx.latitude,
        longitude=ctx.longitude,
        house_row=ctx.house_row,
        house_col=ctx.house_col,
        house_width=ctx.house_width,
        house_height=ctx.house_height,
        north_degrees=ctx.north_degrees,
        grid_col=east_edge,
        grid_row=north_edge,
    )
    se_lat, se_lng = _grid_point_lat_lng_with_calibration(
        ctx.calibration,
        latitude=ctx.latitude,
        longitude=ctx.longitude,
        house_row=ctx.house_row,
        house_col=ctx.house_col,
        house_width=ctx.house_width,
        house_height=ctx.house_height,
        north_degrees=ctx.north_degrees,
        grid_col=east_edge,
        grid_row=south_edge,
    )
    sw_lat, sw_lng = _grid_point_lat_lng_with_calibration(
        ctx.calibration,
        latitude=ctx.latitude,
        longitude=ctx.longitude,
        house_row=ctx.house_row,
        house_col=ctx.house_col,
        house_width=ctx.house_width,
        house_height=ctx.house_height,
        north_degrees=ctx.north_degrees,
        grid_col=west_edge,
        grid_row=south_edge,
    )

    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [nw_lng, nw_lat],
                [ne_lng, ne_lat],
                [se_lng, se_lat],
                [sw_lng, sw_lat],
                [nw_lng, nw_lat],
            ]
        ],
    }
    if not _geometry_in_bounds(geometry, north, south, east, west):
        return None

    house_height_m = max(
        1.0,
        _read_float("SHADEMAP_HOUSE_HEIGHT_METERS", DEFAULT_HOUSE_HEIGHT_METERS),
    )
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "height": house_height_m,
            "render_height": house_height_m,
            "name": "Planner house",
            "source_id": "gardenops-house",
        },
    }


MAX_SANE_TREE_HEIGHT_METERS: Final[float] = 40.0


def _tree_feature_height_meters(row: dict[str, Any]) -> float:
    height_cm = float(row["max_height_cm"] or 0)
    if height_cm > 0:
        return min(
            max(height_cm / 100.0, DEFAULT_TREE_HEIGHT_METERS),
            MAX_SANE_TREE_HEIGHT_METERS,
        )
    return DEFAULT_TREE_HEIGHT_METERS


def _tree_feature_radius_meters(row: dict[str, Any], height_meters: float) -> float:
    quantity = max(int(row["total_quantity"] or 1), 1)
    scaled = max(MIN_TREE_CANOPY_RADIUS_METERS, height_meters * 0.18)
    quantity_scale = min(1.4, 1.0 + max(quantity - 1, 0) * 0.1)
    return min(MAX_TREE_CANOPY_RADIUS_METERS, scaled * quantity_scale)


def _build_radial_feature(
    *,
    center_lat: float,
    center_lng: float,
    height_meters: float,
    radius_meters: float,
    name: str,
    source_id: str,
    north: float,
    south: float,
    east: float,
    west: float,
    sides: int = TREE_CANOPY_SIDES,
) -> dict[str, object] | None:
    """Build a circular polygon GeoJSON Feature, or None if out of bounds."""
    geometry: dict[str, object] = {
        "type": "Polygon",
        "coordinates": [
            [
                *(
                    _regular_polygon_ring(
                        latitude=center_lat,
                        longitude=center_lng,
                        radius_meters=radius_meters,
                        sides=sides,
                    )
                )
            ]
        ],
    }
    if not _geometry_in_bounds(geometry, north, south, east, west):
        return None
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "height": height_meters,
            "render_height": height_meters,
            "name": name,
            "source_id": source_id,
        },
    }


def _planner_tree_features(
    db: DbConn,
    *,
    garden_id: int,
    north: float,
    south: float,
    east: float,
    west: float,
    excluded_plot_ids: set[str] | None = None,
    allow_unowned: bool = False,
) -> list[dict[str, object]]:
    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return []

    features: list[dict[str, object]] = []
    for row in _read_tree_rows(db, garden_id, allow_unowned=allow_unowned):
        plot_id = str(row["plot_id"])
        if excluded_plot_ids and plot_id in excluded_plot_ids:
            continue
        center_lat, center_lng = _grid_point_lat_lng_with_calibration(
            ctx.calibration,
            latitude=ctx.latitude,
            longitude=ctx.longitude,
            house_row=ctx.house_row,
            house_col=ctx.house_col,
            house_width=ctx.house_width,
            house_height=ctx.house_height,
            north_degrees=ctx.north_degrees,
            grid_col=float(row["grid_col"]) - 0.5,
            grid_row=float(row["grid_row"]) - 0.5,
        )
        height_meters = _tree_feature_height_meters(row)
        radius_meters = _tree_feature_radius_meters(row, height_meters)
        feature = _build_radial_feature(
            center_lat=center_lat,
            center_lng=center_lng,
            height_meters=height_meters,
            radius_meters=radius_meters,
            name=f"{plot_id} tree canopy",
            source_id=f"gardenops-tree:{plot_id}",
            north=north,
            south=south,
            east=east,
            west=west,
        )
        if feature:
            features.append(feature)
    return features


def _manual_obstacle_features(
    db: DbConn,
    *,
    garden_id: int,
    north: float,
    south: float,
    east: float,
    west: float,
) -> tuple[list[dict[str, object]], set[str]]:
    features: list[dict[str, object]] = []
    linked_plot_ids: set[str] = set()
    for row in _read_manual_obstacle_rows(db, garden_id):
        obstacle_id = int(row["id"])
        height_m = float(row["height_m"])
        sides = 8 if str(row["kind"]) == "tree" else 4
        feature = _build_radial_feature(
            center_lat=float(row["latitude"]),
            center_lng=float(row["longitude"]),
            height_meters=height_m,
            radius_meters=float(row["crown_radius_m"]),
            name=str(row["label"]),
            source_id=f"gardenops-obstacle:{obstacle_id}",
            north=north,
            south=south,
            east=east,
            west=west,
            sides=sides,
        )
        if feature:
            linked_plot_id = row["linked_plot_id"]
            if linked_plot_id:
                linked_plot_ids.add(str(linked_plot_id))
            features.append(feature)
    return features, linked_plot_ids


def _merge_planner_house_feature(
    features: list[dict[str, object]],
    db: DbConn,
    *,
    garden_id: int,
    north: float,
    south: float,
    east: float,
    west: float,
) -> list[dict[str, object]]:
    planner_house = _planner_house_feature(
        db,
        garden_id=garden_id,
        north=north,
        south=south,
        east=east,
        west=west,
    )
    if not planner_house:
        return features

    planner_geometry = planner_house.get("geometry")
    if not isinstance(planner_geometry, dict):
        return features

    planner_bounds = _geometry_bounds(cast(Mapping[str, object], planner_geometry))
    if not planner_bounds:
        return features

    filtered: list[dict[str, object]] = []
    for feature in features:
        feature_geometry = feature.get("geometry")
        if isinstance(feature_geometry, dict):
            feature_bounds = _geometry_bounds(cast(Mapping[str, object], feature_geometry))
            if feature_bounds and _bounds_overlap(planner_bounds, feature_bounds):
                continue
        filtered.append(feature)
    return [*filtered, planner_house]


def _merge_local_shademap_features(
    features: list[dict[str, object]],
    db: DbConn,
    *,
    garden_id: int,
    north: float,
    south: float,
    east: float,
    west: float,
    allow_unowned_tree_plots: bool = False,
) -> list[dict[str, object]]:
    merged = _merge_planner_house_feature(
        features,
        db,
        garden_id=garden_id,
        north=north,
        south=south,
        east=east,
        west=west,
    )
    manual_features, linked_plot_ids = _manual_obstacle_features(
        db,
        garden_id=garden_id,
        north=north,
        south=south,
        east=east,
        west=west,
    )
    if manual_features:
        merged = [*merged, *manual_features]
    tree_features = _planner_tree_features(
        db,
        garden_id=garden_id,
        north=north,
        south=south,
        east=east,
        west=west,
        excluded_plot_ids=linked_plot_ids,
        allow_unowned=allow_unowned_tree_plots,
    )
    if not tree_features:
        return merged
    return [*merged, *tree_features]


def _build_feature(
    geometry: dict[str, object],
    tags: dict[str, object],
    source_id: str,
) -> dict[str, object]:
    height = _feature_height(tags)
    name = str(tags.get("name", "")).strip()
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "height": height,
            "render_height": height,
            "name": name,
            "source_id": source_id,
        },
    }


def _relation_geometry(members: Sequence[Mapping[str, object]]) -> dict[str, object] | None:
    outer_rings: list[list[list[list[float]]]] = []
    inner_rings: list[list[list[float]]] = []
    for member in members:
        if member.get("type") != "way":
            continue
        geometry = member.get("geometry")
        if not isinstance(geometry, list) or not all(isinstance(point, dict) for point in geometry):
            continue
        ring = _closed_ring(cast(list[Mapping[str, object]], geometry))
        if not ring:
            continue
        if member.get("role") == "inner":
            inner_rings.append(ring)
        else:
            outer_rings.append([ring])

    if not outer_rings:
        return None
    if len(outer_rings) == 1:
        coordinates = [outer_rings[0][0], *inner_rings]
        return {"type": "Polygon", "coordinates": coordinates}
    return {"type": "MultiPolygon", "coordinates": outer_rings}


def _overpass_to_features(payload: dict[str, object]) -> list[dict[str, object]]:
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        return []

    features: list[dict[str, object]] = []
    for element in raw_elements:
        if not isinstance(element, dict):
            continue
        element_data = cast(dict[str, object], element)
        tags = element_data.get("tags")
        if not isinstance(tags, dict):
            tags = {}
        source_id = (
            f"{str(element_data.get('type') or 'element')}/"
            f"{str(element_data.get('id') or 'unknown')}"
        )
        element_type = element_data.get("type")
        if element_type == "way":
            geometry = element_data.get("geometry")
            if not isinstance(geometry, list) or not all(
                isinstance(point, dict) for point in geometry
            ):
                continue
            ring = _closed_ring(cast(list[Mapping[str, object]], geometry))
            if not ring:
                continue
            features.append(
                _build_feature(
                    {"type": "Polygon", "coordinates": [ring]},
                    cast(dict[str, object], tags),
                    source_id,
                ),
            )
            continue
        if element_type == "relation":
            members = element_data.get("members")
            if not isinstance(members, list) or not all(
                isinstance(member, dict) for member in members
            ):
                continue
            geometry = _relation_geometry(cast(list[Mapping[str, object]], members))
            if not geometry:
                continue
            features.append(
                _build_feature(geometry, cast(dict[str, object], tags), source_id),
            )
    return features


def _fetch_overpass_features(
    north: float,
    south: float,
    east: float,
    west: float,
) -> list[dict[str, object]]:
    query = (
        "[out:json][timeout:25];"
        "("
        f'way["building"]({south},{west},{north},{east});'
        f'relation["building"]({south},{west},{north},{east});'
        ");"
        "out body geom;"
    )
    body = urlencode({"data": query}).encode("utf-8")
    errors: list[str] = []
    for source_url in _overpass_urls():
        try:
            payload, _ = _request_bytes(
                source_url,
                method="POST",
                body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=30.0,
            )
        except HTTPException as exc:
            errors.append(f"{source_url}: {exc.detail}")
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            errors.append(f"{source_url}: Invalid Overpass response")
            continue
        return _overpass_to_features(parsed)
    raise HTTPException(
        status_code=502,
        detail=" ; ".join(errors) if errors else "Overpass feature lookup failed",
    )


@router.get("/shademap/config")
def get_shademap_config(db: DB, request: FastAPIRequest) -> dict[str, object]:
    api_key = _read_public_api_key(request, db)
    if not api_key:
        raise HTTPException(status_code=503, detail="SHADEMAP public API key not configured")
    garden_id = _active_garden_id(request)
    provider_state, sdk_cache_status = _ensure_sdk_ready(db, garden_id, request)
    runtime_script_url = _runtime_script_upstream_url()
    latitude, longitude = _garden_coordinates(db, garden_id)
    terrain_max_zoom = 18 if local_terrain_available(garden_id) else TERRAIN_MAX_ZOOM
    try:
        terrain_token, terrain_token_expires_at_ms = _tile_token(garden_id=garden_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "api_key": api_key,
        "latitude": latitude,
        "longitude": longitude,
        "zoom": max(FEATURES_MIN_ZOOM, _read_int("SHADEMAP_ZOOM", DEFAULT_ZOOM)),
        "label": os.environ.get("SHADEMAP_LABEL", DEFAULT_LABEL).strip() or DEFAULT_LABEL,
        "share_url": os.environ.get("SHADEMAP_SHARE_URL", DEFAULT_SHARE_URL).strip()
        or DEFAULT_SHARE_URL,
        "terrain_url_template": (
            f"/shademap/terrain/{{z}}/{{x}}/{{y}}.png?token={terrain_token}&garden_id={garden_id}"
        ),
        "terrain_token_expires_at_ms": terrain_token_expires_at_ms,
        "terrain_max_zoom": terrain_max_zoom,
        "terrain_tile_size": TERRAIN_TILE_SIZE,
        "features_min_zoom": FEATURES_MIN_ZOOM,
        "provider_state": provider_state,
        "sdk_cache_status": sdk_cache_status,
        "runtime_script_url": "/shademap/runtime.js" if runtime_script_url else None,
    }


@asset_router.get("/shademap/runtime.js")
def get_shademap_runtime_script(request: FastAPIRequest, db: DB) -> Response:
    """Serve the configured licensed runtime through the GardenOps origin."""
    # Keep the upstream runtime behind the same garden-auth boundary as terrain
    # tiles. The browser needs no direct vendor URL or credential.
    _active_garden_id(request)
    record_security_event("shademap_runtime_script_requests_total")
    enforce_layered_rate_limit(
        request,
        bucket="shademap-runtime-script",
        identity_limit=env_int("SHADEMAP_RUNTIME_SCRIPT_RATE_LIMIT", 20),
        window_seconds=60,
        user_limit=env_nonneg_int("SHADEMAP_RUNTIME_SCRIPT_RATE_LIMIT_USER", 20),
        garden_limit=env_nonneg_int("SHADEMAP_RUNTIME_SCRIPT_RATE_LIMIT_GARDEN", 40),
        global_limit=env_nonneg_int("SHADEMAP_RUNTIME_SCRIPT_RATE_LIMIT_GLOBAL", 200),
    )
    upstream_url = _runtime_script_upstream_url()
    if upstream_url is None:
        raise HTTPException(status_code=404, detail="ShadeMap runtime script is not configured")
    cached = _runtime_script_cache_get(upstream_url)
    if cached is None:
        with acquire_concurrency_slot(
            bucket="shademap-runtime-script",
            limit=env_int("SHADEMAP_RUNTIME_SCRIPT_CONCURRENCY_LIMIT", 2),
        ):
            cached = _runtime_script_cache_get(upstream_url)
            if cached is None:
                if upstream_url.startswith("http://127.0.0.1:"):
                    payload, content_type = _request_validated_bytes(upstream_url, timeout=20)
                else:
                    payload, content_type = _request_bytes(upstream_url, timeout=20)
                cached = _runtime_script_cache_put(upstream_url, payload, content_type)
    payload, content_type = cached
    return Response(
        content=payload,
        media_type="application/javascript",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def _runtime_script_cache_get(upstream_url: str) -> tuple[bytes, str] | None:
    now_ms = int(time.time() * 1000)
    with _RUNTIME_SCRIPT_CACHE_LOCK:
        entry = _RUNTIME_SCRIPT_CACHE.get(upstream_url)
        if entry is None or now_ms - entry[0] >= RUNTIME_SCRIPT_CACHE_TTL_MS:
            return None
        return entry[1], entry[2]


def _runtime_script_cache_put(
    upstream_url: str,
    payload: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {
        "application/javascript",
        "application/ecmascript",
        "text/javascript",
    }:
        raise HTTPException(
            status_code=502, detail="ShadeMap runtime returned a non-JavaScript response"
        )
    with _RUNTIME_SCRIPT_CACHE_LOCK:
        _RUNTIME_SCRIPT_CACHE[upstream_url] = (int(time.time() * 1000), payload, content_type)
    return payload, content_type


def _clear_runtime_script_cache() -> None:
    """Clear the process-local runtime cache for test isolation."""
    with _RUNTIME_SCRIPT_CACHE_LOCK:
        _RUNTIME_SCRIPT_CACHE.clear()


@router.get("/shademap/monthly-estimated-sun")
def get_shademap_monthly_estimated_sun() -> dict[str, object]:
    return _load_monthly_estimated_sun()


@router.get("/shademap/sun-window")
def get_shademap_sun_window(
    month: int = Query(..., ge=1, le=12),
    day: int = Query(..., ge=1, le=31),
) -> dict[str, str | None]:
    """Return sol_opp/sol_ned for a given month-day from soltider_estimated.csv."""
    csv_path = _monthly_estimate_csv_path()
    if not csv_path.exists():
        raise HTTPException(
            status_code=503,
            detail="soltider_estimated.csv not found",
        )

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            date_value = str(row.get("dato", "")).strip()
            if not date_value:
                continue
            parts = date_value.split("-")
            if len(parts) < 3:
                continue
            try:
                row_month = int(parts[1])
                row_day = int(parts[2])
            except ValueError:
                continue
            if row_month == month and row_day == day:
                sol_opp = str(row.get("sol_opp", "")).strip() or None
                sol_ned = str(row.get("sol_ned", "")).strip() or None
                return {"sol_opp": sol_opp, "sol_ned": sol_ned}

    return {"sol_opp": None, "sol_ned": None}


@router.get("/shademap/features")
def get_shademap_features(
    request: FastAPIRequest,
    north: float = Query(...),
    south: float = Query(...),
    east: float = Query(...),
    west: float = Query(...),
    zoom: int = Query(..., ge=0, le=22),
    db: DbConn = Depends(db_dep),
) -> dict[str, Any]:
    context = _auth_context(request)
    garden_id = _active_garden_id(request)
    allow_unowned_tree_plots = _is_local_admin_fallback(context)
    record_security_event("shademap_features_requests_total")
    enforce_layered_rate_limit(
        request,
        bucket="shademap-features",
        identity_limit=env_int("SHADEMAP_FEATURES_RATE_LIMIT", 60),
        window_seconds=60,
        user_limit=env_nonneg_int("SHADEMAP_FEATURES_RATE_LIMIT_USER", 60),
        garden_limit=env_nonneg_int("SHADEMAP_FEATURES_RATE_LIMIT_GARDEN", 120),
        global_limit=env_nonneg_int("SHADEMAP_FEATURES_RATE_LIMIT_GLOBAL", 600),
    )
    normalized_north, normalized_south, normalized_east, normalized_west = _normalized_bounds(
        north,
        south,
        east,
        west,
    )
    if zoom < FEATURES_MIN_ZOOM:
        return {"features": []}
    max_bbox_tiles = env_int("SHADEMAP_FEATURES_MAX_BBOX_TILES", 512)
    if (
        _bbox_tile_span(
            normalized_north,
            normalized_south,
            normalized_east,
            normalized_west,
            zoom,
        )
        > max_bbox_tiles
    ):
        raise HTTPException(
            status_code=400,
            detail="Requested ShadeMap bounds are too large for this zoom level",
        )

    cache_key = _features_cache_key(
        normalized_north,
        normalized_south,
        normalized_east,
        normalized_west,
        zoom,
        garden_id,
    )
    cached = _cache_get(db, garden_id, "features", cache_key)
    if cached is not None and _cache_fresh(cached, FEATURE_CACHE_TTL_MS) and cached["payload_text"]:
        payload = json.loads(str(cached["payload_text"]))
        payload["features"] = _merge_local_shademap_features(
            list(payload.get("features", [])),
            db,
            garden_id=garden_id,
            north=normalized_north,
            south=normalized_south,
            east=normalized_east,
            west=normalized_west,
            allow_unowned_tree_plots=allow_unowned_tree_plots,
        )
        return payload
    _enforce_distinct_signature_budget(
        request=request,
        namespace="shademap-features",
        signature=cache_key,
        max_unique=env_int("SHADEMAP_FEATURES_MAX_DISTINCT_BOUNDS", 80),
        window_seconds=env_int("SHADEMAP_FEATURES_DISTINCT_WINDOW_SECONDS", 300),
    )
    enforce_layered_rate_limit(
        request,
        bucket="shademap-features-miss",
        identity_limit=env_int("SHADEMAP_FEATURES_MISS_RATE_LIMIT", 12),
        window_seconds=60,
        user_limit=env_nonneg_int("SHADEMAP_FEATURES_MISS_RATE_LIMIT_USER", 12),
        garden_limit=env_nonneg_int("SHADEMAP_FEATURES_MISS_RATE_LIMIT_GARDEN", 20),
        global_limit=env_nonneg_int("SHADEMAP_FEATURES_MISS_RATE_LIMIT_GLOBAL", 120),
    )
    feature_limits = provider_limit_profile("shademap-features-miss")
    reserve_daily_provider_budget(
        db,
        feature="shademap-features-miss",
        user_id=context.user_id,
        garden_id=garden_id,
        user_limit=int(feature_limits["user_limit"]),
        garden_limit=int(feature_limits["garden_limit"]),
    )
    record_security_event("shademap_features_cache_misses")

    try:
        with acquire_concurrency_slot(
            bucket="shademap-features-miss",
            limit=int(feature_limits["concurrency_limit"]),
        ):
            features = _fetch_overpass_features(
                normalized_north,
                normalized_south,
                normalized_east,
                normalized_west,
            )
    except HTTPException:
        if cached and cached["payload_text"]:
            payload = json.loads(str(cached["payload_text"]))
            payload["features"] = _merge_local_shademap_features(
                list(payload.get("features", [])),
                db,
                garden_id=garden_id,
                north=normalized_north,
                south=normalized_south,
                east=normalized_east,
                west=normalized_west,
                allow_unowned_tree_plots=allow_unowned_tree_plots,
            )
            return payload
        return {
            "features": _merge_local_shademap_features(
                [],
                db,
                garden_id=garden_id,
                north=normalized_north,
                south=normalized_south,
                east=normalized_east,
                west=normalized_west,
                allow_unowned_tree_plots=allow_unowned_tree_plots,
            ),
        }

    payload = {"features": features}
    _cache_put(
        db,
        garden_id,
        "features",
        cache_key,
        content_type="application/json",
        payload_text=json.dumps(payload, separators=(",", ":")),
    )
    db.commit()
    payload["features"] = _merge_local_shademap_features(
        features,
        db,
        garden_id=garden_id,
        north=normalized_north,
        south=normalized_south,
        east=normalized_east,
        west=normalized_west,
        allow_unowned_tree_plots=allow_unowned_tree_plots,
    )
    return payload


def _prepare_terrain_tile(
    elevations: np.ndarray,
    z: int,
    x: int,
    y: int,
    db: DbConn,
    garden_id: int,
) -> bytes:
    """Apply overrides and house stamp, encode to Terrarium PNG."""
    elevations = _apply_elevation_overrides(elevations, z, x, y, db, garden_id)
    elevations = _apply_house_to_terrain(elevations, z, x, y, db, garden_id)
    return encode_terrarium_png(elevations)


@asset_router.get("/shademap/terrain/{z}/{x}/{y}.png")
def get_shademap_terrain_tile(
    request: FastAPIRequest,
    z: int,
    x: int,
    y: int,
    token: str = Query(..., min_length=1),
    db: DbConn = Depends(db_dep),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(request)
    record_security_event("shademap_terrain_requests_total")
    enforce_layered_rate_limit(
        request,
        bucket="shademap-terrain",
        identity_limit=env_int("SHADEMAP_TERRAIN_RATE_LIMIT", 240),
        window_seconds=60,
        user_limit=env_nonneg_int("SHADEMAP_TERRAIN_RATE_LIMIT_USER", 240),
        garden_limit=env_nonneg_int("SHADEMAP_TERRAIN_RATE_LIMIT_GARDEN", 480),
        global_limit=env_nonneg_int("SHADEMAP_TERRAIN_RATE_LIMIT_GLOBAL", 2400),
    )
    _validate_tile_coords(z, x, y)
    token_expires_at_ms = _validate_tile_token(token, garden_id=garden_id)
    tile_sig = f"{z}:{x}:{y}"
    _enforce_distinct_signature_budget(
        request=request,
        namespace="shademap-terrain",
        signature=tile_sig,
        max_unique=env_int("SHADEMAP_TERRAIN_MAX_DISTINCT_TILES", 220),
        window_seconds=env_int("SHADEMAP_TERRAIN_DISTINCT_WINDOW_SECONDS", 300),
    )

    override_sig = _override_signature(db, garden_id)
    house_sig = _house_terrain_signature(db, garden_id)
    cache_key = _terrain_cache_key(z, x, y, garden_id, override_sig, house_sig)
    cached = _cache_get(db, garden_id, "terrain-tile", cache_key)
    if cached and cached["payload_blob"] is not None:
        return Response(
            content=bytes(cached["payload_blob"]),
            media_type=str(cached["content_type"] or "image/png"),
            headers=_terrain_tile_response_headers(token_expires_at_ms=token_expires_at_ms),
        )

    enforce_layered_rate_limit(
        request,
        bucket="shademap-terrain-miss",
        identity_limit=env_int("SHADEMAP_TERRAIN_MISS_RATE_LIMIT", 80),
        window_seconds=60,
        user_limit=env_nonneg_int("SHADEMAP_TERRAIN_MISS_RATE_LIMIT_USER", 80),
        garden_limit=env_nonneg_int("SHADEMAP_TERRAIN_MISS_RATE_LIMIT_GARDEN", 120),
        global_limit=env_nonneg_int("SHADEMAP_TERRAIN_MISS_RATE_LIMIT_GLOBAL", 800),
    )
    local_tile = sample_local_terrain_tile(z, x, y, garden_id)
    if local_tile is not None and local_tile.fully_covered:
        payload = _prepare_terrain_tile(
            local_tile.elevations,
            z,
            x,
            y,
            db,
            garden_id,
        )
        _cache_put(
            db,
            garden_id,
            "terrain-tile",
            cache_key,
            content_type="image/png",
            payload_blob=payload,
        )
        db.commit()
        return Response(
            content=payload,
            media_type="image/png",
            headers=_terrain_tile_response_headers(token_expires_at_ms=token_expires_at_ms),
        )

    terrain_limits = provider_limit_profile("shademap-terrain-miss")
    reserve_daily_provider_budget(
        db,
        feature="shademap-terrain-miss",
        user_id=context.user_id,
        garden_id=garden_id,
        user_limit=int(terrain_limits["user_limit"]),
        garden_limit=int(terrain_limits["garden_limit"]),
    )
    record_security_event("shademap_terrain_remote_misses")
    try:
        with acquire_concurrency_slot(
            bucket="shademap-terrain-miss",
            limit=int(terrain_limits["concurrency_limit"]),
        ):
            payload, content_type = _request_bytes(
                _terrain_source_url(z, x, y),
                headers={"Accept": "image/png,image/*;q=0.8,*/*;q=0.1"},
            )
            content_type = _validate_remote_terrain_content_type(content_type)
    except HTTPException:
        if cached and cached["payload_blob"] is not None:
            return Response(
                content=bytes(cached["payload_blob"]),
                media_type=str(cached["content_type"] or "image/png"),
                headers=_terrain_tile_response_headers(token_expires_at_ms=token_expires_at_ms),
            )
        if local_tile is None:
            raise
        payload = _prepare_terrain_tile(
            local_tile.elevations,
            z,
            x,
            y,
            db,
            garden_id,
        )
        _cache_put(
            db,
            garden_id,
            "terrain-tile",
            cache_key,
            content_type="image/png",
            payload_blob=payload,
        )
        db.commit()
        return Response(
            content=payload,
            media_type="image/png",
            headers=_terrain_tile_response_headers(token_expires_at_ms=token_expires_at_ms),
        )

    if local_tile is not None and not local_tile.fully_covered:
        remote_elevations = decode_terrarium_png(payload)
        merged = np.where(
            local_tile.coverage_mask,
            local_tile.elevations,
            remote_elevations,
        )
        payload = _prepare_terrain_tile(merged, z, x, y, db, garden_id)
        content_type = "image/png"
    elif _house_overlaps_tile(z, x, y, db, garden_id):
        remote_elevations = decode_terrarium_png(payload)
        stamped = _apply_house_to_terrain(
            remote_elevations,
            z,
            x,
            y,
            db,
            garden_id,
        )
        if stamped is not remote_elevations:
            payload = encode_terrarium_png(stamped)
            content_type = "image/png"

    _cache_put(
        db,
        garden_id,
        "terrain-tile",
        cache_key,
        content_type=content_type,
        payload_blob=payload,
    )
    db.commit()
    return Response(
        content=payload,
        media_type=content_type,
        headers=_terrain_tile_response_headers(token_expires_at_ms=token_expires_at_ms),
    )


@router.get("/plots/elevations")
def get_plot_elevations(request: FastAPIRequest, db: DB) -> dict[str, object]:
    """Per-plot elevations from local LiDAR, cached in DB."""
    context = _auth_context(request)
    garden_id = _active_garden_id(request)
    terrain_sig = local_terrain_signature(garden_id)
    if not terrain_sig:
        return {
            "available": False,
            "elevations": {},
            "overrides": {},
            "min_m": None,
            "max_m": None,
        }

    if _is_local_admin_fallback(context):
        plots = db.execute(
            """
            SELECT DISTINCT p.plot_id, p.grid_row, p.grid_col
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE (po.garden_id = %s OR po.garden_id IS NULL) AND p.grid_row IS NOT NULL
            ORDER BY p.plot_id
            """,
            (garden_id,),
        ).fetchall()
    else:
        plots = db.execute(
            """
            SELECT p.plot_id, p.grid_row, p.grid_col
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s AND p.grid_row IS NOT NULL
            ORDER BY p.plot_id
            """,
            (garden_id,),
        ).fetchall()
    if not plots:
        return {
            "available": True,
            "elevations": {},
            "overrides": {},
            "min_m": None,
            "max_m": None,
        }

    layout = _read_layout_state(db, garden_id)
    calibration = _calibration_transform(db, garden_id)
    cache_sig = _elevation_cache_sig(terrain_sig, plots, layout, calibration, garden_id)

    cached = _load_cached_elevations(db, cache_sig, plots, garden_id)
    if cached is None:
        cached = _compute_and_cache_elevations(
            db,
            cache_sig,
            plots,
            layout,
            calibration,
            garden_id,
        )

    if not cached:
        return {
            "available": True,
            "elevations": {},
            "overrides": {},
            "min_m": None,
            "max_m": None,
        }

    overrides = _load_elevation_overrides(db, garden_id)
    merged = {**cached, **overrides}
    vals = list(merged.values())
    return {
        "available": True,
        "elevations": merged,
        "overrides": overrides,
        "min_m": round(min(vals), 2),
        "max_m": round(max(vals), 2),
    }


def _elevation_cache_sig(
    terrain_sig: str,
    plots: list[dict[str, Any]],
    layout: dict[str, Any] | None,
    calibration: dict[str, float] | None,
    garden_id: int,
) -> str:
    """Build a combined signature covering terrain, plot positions, and geo config."""
    parts = [terrain_sig, f"g:{garden_id}"]
    for row in plots:
        parts.append(f"{row['plot_id']}:{row['grid_row']},{row['grid_col']}")
    if layout:
        parts.append(
            f"L:{layout['house_row']},{layout['house_col']},"
            f"{layout['house_width']},{layout['house_height']},"
            f"{layout['north_degrees']}",
        )
    if calibration:
        cal_parts = ",".join(f"{key}={calibration[key]:.10f}" for key in sorted(calibration.keys()))
        parts.append(f"C:{cal_parts}")
    return sha256("|".join(parts).encode()).hexdigest()[:16]


def _load_cached_elevations(
    db: DbConn,
    cache_sig: str,
    plots: list[dict[str, Any]],
    garden_id: int,
) -> dict[str, float] | None:
    """Return cached elevations if signature matches, else None."""
    plot_ids = {str(row["plot_id"]) for row in plots}
    rows = db.execute(
        """
        SELECT plot_id, elevation_m, cache_sig
        FROM plot_elevations
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchall()
    if not rows:
        return None

    if str(rows[0]["cache_sig"]) != cache_sig:
        return None

    cached_ids = set()
    result: dict[str, float] = {}
    for row in rows:
        pid = str(row["plot_id"])
        if str(row["cache_sig"]) != cache_sig:
            return None
        cached_ids.add(pid)
        if pid in plot_ids:
            result[pid] = round(float(row["elevation_m"]), 2)

    if not plot_ids.issubset(cached_ids):
        return None

    return result


def _compute_and_cache_elevations(
    db: DbConn,
    cache_sig: str,
    plots: list[dict[str, Any]],
    layout: dict[str, Any] | None,
    calibration: dict[str, float] | None,
    garden_id: int,
) -> dict[str, float]:
    """Sample elevations via LiDAR, store in DB, return results."""
    if not layout:
        return {}

    ctx = _load_geo_context(db, garden_id)
    if not ctx:
        return {}

    lats: list[float] = []
    lngs: list[float] = []
    plot_ids: list[str] = []
    for row in plots:
        center_row = float(row["grid_row"]) - 0.5
        center_col = float(row["grid_col"]) - 0.5
        lat, lng = _grid_point_lat_lng_with_calibration(
            ctx.calibration,
            latitude=ctx.latitude,
            longitude=ctx.longitude,
            house_row=ctx.house_row,
            house_col=ctx.house_col,
            house_width=ctx.house_width,
            house_height=ctx.house_height,
            north_degrees=ctx.north_degrees,
            grid_col=center_col,
            grid_row=center_row,
        )
        lats.append(lat)
        lngs.append(lng)
        plot_ids.append(str(row["plot_id"]))

    elevations = sample_elevations_wgs84(
        np.array(lats, dtype=np.float64),
        np.array(lngs, dtype=np.float64),
        garden_id,
    )

    db.execute("DELETE FROM plot_elevations WHERE garden_id = %s", (garden_id,))
    result: dict[str, float] = {}
    for pid, elev in zip(plot_ids, elevations, strict=True):
        elev_f = float(elev)
        if not math.isfinite(elev_f):
            continue
        db.execute(
            """
            INSERT INTO plot_elevations (plot_id, elevation_m, cache_sig, garden_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(plot_id) DO UPDATE SET
                elevation_m = excluded.elevation_m,
                cache_sig = excluded.cache_sig,
                garden_id = excluded.garden_id
            """,
            (pid, round(elev_f, 2), cache_sig, garden_id),
        )
        result[pid] = round(elev_f, 2)
    db.commit()
    return result


def _load_elevation_overrides(
    db: DbConn,
    garden_id: int,
) -> dict[str, float]:
    rows = db.execute(
        """
        SELECT plot_id, elevation_m
        FROM plot_elevation_overrides
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchall()
    return {str(row["plot_id"]): round(float(row["elevation_m"]), 2) for row in rows}


class ElevationOverridesBody(StrictBaseModel):
    overrides: dict[str, float | None]


@router.patch("/plots/elevations")
def patch_plot_elevations(
    body: ElevationOverridesBody,
    request: FastAPIRequest,
    db: DB,
) -> dict[str, object]:
    """Upsert or delete elevation overrides.

    Body: {"overrides": {"B2": 41.0, "B3": null}}
    Non-null values upsert; null values delete (restore LiDAR).
    """
    context = _auth_context(request)
    garden_id = _active_garden_id(request)
    overrides = body.overrides
    if _is_local_admin_fallback(context):
        valid_rows = db.execute(
            """
            SELECT DISTINCT p.plot_id
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE (po.garden_id = %s OR po.garden_id IS NULL) AND p.grid_row IS NOT NULL
            """,
            (garden_id,),
        ).fetchall()
    else:
        valid_rows = db.execute(
            """
            SELECT p.plot_id
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s AND p.grid_row IS NOT NULL
            """,
            (garden_id,),
        ).fetchall()
    valid_plot_ids = {str(row["plot_id"]) for row in valid_rows}
    unknown = [plot_id for plot_id in overrides if plot_id not in valid_plot_ids]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown plot IDs: {', '.join(sorted(unknown))}",
        )

    for plot_id, value in overrides.items():
        if value is None:
            db.execute(
                "DELETE FROM plot_elevation_overrides WHERE plot_id = %s AND garden_id = %s",
                (plot_id, garden_id),
            )
        else:
            try:
                db.execute(
                    """
                    INSERT INTO plot_elevation_overrides (plot_id, elevation_m, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plot_id) DO UPDATE SET
                        elevation_m = excluded.elevation_m,
                        garden_id = excluded.garden_id
                    """,
                    (plot_id, round(float(value), 2), garden_id),
                )
            except psycopg.IntegrityError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid override for plot {plot_id}",
                ) from exc
    db.execute(
        "DELETE FROM shademap_cache WHERE garden_id = %s AND cache_kind = 'terrain-tile'",
        (garden_id,),
    )
    db.commit()
    return get_plot_elevations(request, db)


# ---------------------------------------------------------------------------
# Shademap state, calibration, and obstacle management
# (moved from main.py — P2.2)
# ---------------------------------------------------------------------------


def get_shademap_state(db: DbConn, *, garden_id: int) -> dict[str, object]:
    row = db.execute(
        """
        SELECT mode, selected_plot_id, analysis_timestamp_ms, preset
        FROM shademap_state
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        default = default_shademap_state()
        set_shademap_state(db, default, garden_id=garden_id)
        db.commit()
        return default

    selected_plot_id = row["selected_plot_id"]
    if selected_plot_id and not _plot_in_garden(db, str(selected_plot_id), garden_id):
        selected_plot_id = None

    return {
        "mode": str(row["mode"]),
        "selected_plot_id": selected_plot_id,
        "analysis_timestamp_ms": int(row["analysis_timestamp_ms"]),
        "preset": str(row["preset"]),
    }


def _plot_in_garden(db: DbConn, plot_id: str, garden_id: int) -> bool:
    return bool(
        db.execute(
            """
            SELECT 1
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.plot_id = %s AND po.garden_id = %s
            """,
            (plot_id, garden_id),
        ).fetchone()
    )


def get_shademap_calibration(
    db: DbConn,
    *,
    garden_id: int,
) -> dict[str, object]:
    row = db.execute(
        """
        SELECT
            enabled, calibration_type,
            origin_grid_col, origin_grid_row,
            origin_latitude, origin_longitude,
            axis_grid_col, axis_grid_row,
            axis_latitude, axis_longitude,
            house_nw_latitude, house_nw_longitude,
            house_ne_latitude, house_ne_longitude,
            house_se_latitude, house_se_longitude,
            house_sw_latitude, house_sw_longitude
        FROM shademap_calibration
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        default = default_shademap_calibration()
        set_shademap_calibration(db, default, garden_id=garden_id)
        db.commit()
        return default
    return {
        "enabled": bool(row["enabled"]),
        "calibration_type": str(row["calibration_type"] or "two-point"),
        "origin_grid_col": row["origin_grid_col"],
        "origin_grid_row": row["origin_grid_row"],
        "origin_latitude": row["origin_latitude"],
        "origin_longitude": row["origin_longitude"],
        "axis_grid_col": row["axis_grid_col"],
        "axis_grid_row": row["axis_grid_row"],
        "axis_latitude": row["axis_latitude"],
        "axis_longitude": row["axis_longitude"],
        "house_nw_latitude": row["house_nw_latitude"],
        "house_nw_longitude": row["house_nw_longitude"],
        "house_ne_latitude": row["house_ne_latitude"],
        "house_ne_longitude": row["house_ne_longitude"],
        "house_se_latitude": row["house_se_latitude"],
        "house_se_longitude": row["house_se_longitude"],
        "house_sw_latitude": row["house_sw_latitude"],
        "house_sw_longitude": row["house_sw_longitude"],
    }


def set_shademap_state(
    db: DbConn,
    state: dict[str, object],
    *,
    garden_id: int,
) -> dict[str, object]:
    mode = str(state["mode"])
    if mode not in SHADEMAP_MODES:
        raise HTTPException(status_code=400, detail="Invalid ShadeMap mode")

    preset = str(state["preset"])
    if preset not in SHADEMAP_PRESETS:
        raise HTTPException(status_code=400, detail="Invalid ShadeMap preset")

    selected_plot_id = state.get("selected_plot_id")
    if isinstance(selected_plot_id, str):
        selected_plot_id = selected_plot_id.strip() or None
    elif selected_plot_id is not None:
        selected_plot_id = str(selected_plot_id)

    if selected_plot_id and not _plot_in_garden(db, str(selected_plot_id), garden_id):
        raise HTTPException(status_code=404, detail="ShadeMap plot target not found")

    analysis_timestamp_ms = _coerce_int(state["analysis_timestamp_ms"])
    if analysis_timestamp_ms < 0:
        raise HTTPException(status_code=400, detail="Invalid ShadeMap timestamp")

    db.execute(
        """
        INSERT INTO shademap_state (
            garden_id, mode, selected_plot_id, analysis_timestamp_ms, preset
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(garden_id) DO UPDATE SET
            mode = excluded.mode,
            selected_plot_id = excluded.selected_plot_id,
            analysis_timestamp_ms = excluded.analysis_timestamp_ms,
            preset = excluded.preset
        """,
        (garden_id, mode, selected_plot_id, analysis_timestamp_ms, preset),
    )
    return {
        "mode": mode,
        "selected_plot_id": selected_plot_id,
        "analysis_timestamp_ms": analysis_timestamp_ms,
        "preset": preset,
    }


def _validate_grid_point(name: str, grid_col: float, grid_row: float) -> None:
    if grid_col < 0 or grid_col > 100:
        raise HTTPException(
            status_code=400,
            detail=f"{name} grid column is out of range",
        )
    if grid_row < 0 or grid_row > 100:
        raise HTTPException(
            status_code=400,
            detail=f"{name} grid row is out of range",
        )


_CALIBRATION_FIELDS = (
    "calibration_type",
    "origin_grid_col",
    "origin_grid_row",
    "origin_latitude",
    "origin_longitude",
    "axis_grid_col",
    "axis_grid_row",
    "axis_latitude",
    "axis_longitude",
    "house_nw_latitude",
    "house_nw_longitude",
    "house_ne_latitude",
    "house_ne_longitude",
    "house_se_latitude",
    "house_se_longitude",
    "house_sw_latitude",
    "house_sw_longitude",
)


def _validate_two_point(
    calibration: dict[str, object],
) -> dict[str, float | None]:
    """Validate two-point calibration and return cleaned values."""
    required = (
        "origin_grid_col",
        "origin_grid_row",
        "origin_latitude",
        "origin_longitude",
        "axis_grid_col",
        "axis_grid_row",
        "axis_latitude",
        "axis_longitude",
    )
    missing = [n for n in required if calibration.get(n) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=("Missing ShadeMap calibration fields: " + ", ".join(missing)),
        )

    vals = {n: _coerce_float(calibration[n]) for n in required}
    _validate_grid_point("Origin", vals["origin_grid_col"], vals["origin_grid_row"])
    _validate_grid_point("Axis", vals["axis_grid_col"], vals["axis_grid_row"])

    if (
        abs(vals["origin_grid_col"] - vals["axis_grid_col"]) < 1e-9
        and abs(vals["origin_grid_row"] - vals["axis_grid_row"]) < 1e-9
    ):
        raise HTTPException(
            status_code=400,
            detail="Calibration anchors must use different grid points",
        )
    if (
        abs(vals["origin_latitude"] - vals["axis_latitude"]) < 1e-12
        and abs(vals["origin_longitude"] - vals["axis_longitude"]) < 1e-12
    ):
        raise HTTPException(
            status_code=400,
            detail=("Calibration anchors must use different coordinates"),
        )

    return {
        **vals,
        "house_nw_latitude": None,
        "house_nw_longitude": None,
        "house_ne_latitude": None,
        "house_ne_longitude": None,
        "house_se_latitude": None,
        "house_se_longitude": None,
        "house_sw_latitude": None,
        "house_sw_longitude": None,
    }


def _validate_house_corners(
    calibration: dict[str, object],
) -> dict[str, float | None]:
    """Validate house-corners calibration and return cleaned values."""
    required = (
        "house_nw_latitude",
        "house_nw_longitude",
        "house_ne_latitude",
        "house_ne_longitude",
        "house_se_latitude",
        "house_se_longitude",
        "house_sw_latitude",
        "house_sw_longitude",
    )
    missing = [n for n in required if calibration.get(n) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=("Missing ShadeMap house corner fields: " + ", ".join(missing)),
        )

    vals = {n: _coerce_float(calibration[n]) for n in required}
    points = [
        (vals["house_nw_latitude"], vals["house_nw_longitude"]),
        (vals["house_ne_latitude"], vals["house_ne_longitude"]),
        (vals["house_se_latitude"], vals["house_se_longitude"]),
        (vals["house_sw_latitude"], vals["house_sw_longitude"]),
    ]
    unique = {(round(lat, 10), round(lng, 10)) for lat, lng in points}
    if len(unique) < 4:
        raise HTTPException(
            status_code=400,
            detail="House calibration corners must be distinct",
        )

    return {
        "origin_grid_col": None,
        "origin_grid_row": None,
        "origin_latitude": None,
        "origin_longitude": None,
        "axis_grid_col": None,
        "axis_grid_row": None,
        "axis_latitude": None,
        "axis_longitude": None,
        **vals,
    }


def _upsert_calibration(
    db: DbConn,
    calibration_type: str,
    fields: dict[str, float | None],
    garden_id: int,
) -> dict[str, object]:
    """Write calibration to DB and return the saved state."""
    params = tuple(fields[f] for f in _CALIBRATION_FIELDS[1:])
    db.execute(
        """
        INSERT INTO shademap_calibration (
            garden_id, enabled, calibration_type,
            origin_grid_col, origin_grid_row,
            origin_latitude, origin_longitude,
            axis_grid_col, axis_grid_row,
            axis_latitude, axis_longitude,
            house_nw_latitude, house_nw_longitude,
            house_ne_latitude, house_ne_longitude,
            house_se_latitude, house_se_longitude,
            house_sw_latitude, house_sw_longitude
        )
        VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(garden_id) DO UPDATE SET
            enabled = 1,
            calibration_type = excluded.calibration_type,
            origin_grid_col = excluded.origin_grid_col,
            origin_grid_row = excluded.origin_grid_row,
            origin_latitude = excluded.origin_latitude,
            origin_longitude = excluded.origin_longitude,
            axis_grid_col = excluded.axis_grid_col,
            axis_grid_row = excluded.axis_grid_row,
            axis_latitude = excluded.axis_latitude,
            axis_longitude = excluded.axis_longitude,
            house_nw_latitude = excluded.house_nw_latitude,
            house_nw_longitude = excluded.house_nw_longitude,
            house_ne_latitude = excluded.house_ne_latitude,
            house_ne_longitude = excluded.house_ne_longitude,
            house_se_latitude = excluded.house_se_latitude,
            house_se_longitude = excluded.house_se_longitude,
            house_sw_latitude = excluded.house_sw_latitude,
            house_sw_longitude = excluded.house_sw_longitude
        """,
        (garden_id, calibration_type, *params),
    )
    return {
        "enabled": True,
        "calibration_type": calibration_type,
        **fields,
    }


def set_shademap_calibration(
    db: DbConn,
    calibration: dict[str, object],
    *,
    garden_id: int,
) -> dict[str, object]:
    if not bool(calibration.get("enabled")):
        db.execute(
            """
            INSERT INTO shademap_calibration (
                garden_id, enabled, calibration_type,
                origin_grid_col, origin_grid_row,
                origin_latitude, origin_longitude,
                axis_grid_col, axis_grid_row,
                axis_latitude, axis_longitude,
                house_nw_latitude, house_nw_longitude,
                house_ne_latitude, house_ne_longitude,
                house_se_latitude, house_se_longitude,
                house_sw_latitude, house_sw_longitude
            )
            VALUES (
                %s, 0, 'house-corners',
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
            )
            ON CONFLICT(garden_id) DO UPDATE SET
                enabled = 0,
                calibration_type = 'house-corners',
                origin_grid_col = NULL, origin_grid_row = NULL,
                origin_latitude = NULL, origin_longitude = NULL,
                axis_grid_col = NULL, axis_grid_row = NULL,
                axis_latitude = NULL, axis_longitude = NULL,
                house_nw_latitude = NULL, house_nw_longitude = NULL,
                house_ne_latitude = NULL, house_ne_longitude = NULL,
                house_se_latitude = NULL, house_se_longitude = NULL,
                house_sw_latitude = NULL, house_sw_longitude = NULL
            """,
            (garden_id,),
        )
        return default_shademap_calibration()

    calibration_type = str(calibration.get("calibration_type") or "house-corners")
    if calibration_type not in {"two-point", "house-corners"}:
        raise HTTPException(status_code=400, detail="Invalid ShadeMap calibration type")

    if calibration_type == "house-corners":
        house_keys = (
            "house_nw_latitude",
            "house_nw_longitude",
            "house_ne_latitude",
            "house_ne_longitude",
            "house_se_latitude",
            "house_se_longitude",
            "house_sw_latitude",
            "house_sw_longitude",
        )
        two_point_keys = (
            "origin_grid_col",
            "origin_grid_row",
            "origin_latitude",
            "origin_longitude",
            "axis_grid_col",
            "axis_grid_row",
            "axis_latitude",
            "axis_longitude",
        )
        if all(calibration.get(k) is None for k in house_keys) and all(
            calibration.get(k) is not None for k in two_point_keys
        ):
            calibration_type = "two-point"

    if calibration_type == "two-point":
        fields = _validate_two_point(calibration)
    else:
        fields = _validate_house_corners(calibration)

    return _upsert_calibration(db, calibration_type, fields, garden_id)


def _normalize_plot_link(
    db: DbConn,
    garden_id: int,
    plot_id: object,
    *,
    allow_unowned: bool = False,
) -> str | None:
    if plot_id is None:
        return None
    normalized = str(plot_id).strip()
    if not normalized:
        return None
    if allow_unowned:
        row = db.execute(
            """
            SELECT 1
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.plot_id = %s
              AND (po.garden_id = %s OR po.garden_id IS NULL)
              AND p.grid_row IS NOT NULL
            LIMIT 1
            """,
            (normalized, garden_id),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT 1
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.plot_id = %s AND po.garden_id = %s AND p.grid_row IS NOT NULL
            LIMIT 1
            """,
            (normalized, garden_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Linked plot not found")
    return normalized


def _serialize_shademap_obstacle(
    row: dict[str, Any],
) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "label": str(row["label"]),
        "kind": str(row["kind"]),
        "linked_plot_id": row["linked_plot_id"],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "height_m": float(row["height_m"]),
        "crown_radius_m": float(row["crown_radius_m"]),
        "active": bool(row["active"]),
    }


def list_shademap_obstacles(
    db: DbConn,
    *,
    garden_id: int,
) -> list[dict[str, object]]:
    rows = db.execute(
        """
        SELECT id, label, kind, linked_plot_id,
               latitude, longitude, height_m, crown_radius_m, active
        FROM shademap_obstacles
        WHERE garden_id = %s
        ORDER BY id
        """,
        (garden_id,),
    ).fetchall()
    return [_serialize_shademap_obstacle(row) for row in rows]


def _save_shademap_obstacle(
    db: DbConn,
    garden_id: int,
    obstacle: dict[str, object],
    *,
    obstacle_id: int | None = None,
    allow_unowned_plot_links: bool = False,
) -> dict[str, object]:
    label = str(obstacle["label"]).strip()
    if not label:
        raise HTTPException(
            status_code=400,
            detail="ShadeMap obstacle label is required",
        )

    kind = str(obstacle["kind"])
    if kind not in SHADEMAP_OBSTACLE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid ShadeMap obstacle kind")

    linked_plot_id = _normalize_plot_link(
        db,
        garden_id,
        obstacle.get("linked_plot_id"),
        allow_unowned=allow_unowned_plot_links,
    )
    latitude = _coerce_float(obstacle["latitude"])
    longitude = _coerce_float(obstacle["longitude"])
    height_m = _coerce_float(obstacle["height_m"])
    crown_radius_m = _coerce_float(obstacle["crown_radius_m"])
    active = int(bool(obstacle.get("active", True)))

    if obstacle_id is None:
        orow = db.execute(
            """
            INSERT INTO shademap_obstacles (
                garden_id, label, kind, linked_plot_id,
                latitude, longitude, height_m, crown_radius_m, active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                garden_id,
                label,
                kind,
                linked_plot_id,
                latitude,
                longitude,
                height_m,
                crown_radius_m,
                active,
            ),
        ).fetchone()
        assert orow is not None
        obstacle_id = int(orow["id"])
    else:
        updated = db.execute(
            """
            UPDATE shademap_obstacles
            SET label = %s, kind = %s, linked_plot_id = %s,
                latitude = %s, longitude = %s, height_m = %s,
                crown_radius_m = %s, active = %s
            WHERE id = %s AND garden_id = %s
            """,
            (
                label,
                kind,
                linked_plot_id,
                latitude,
                longitude,
                height_m,
                crown_radius_m,
                active,
                obstacle_id,
                garden_id,
            ),
        )
        if updated.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="ShadeMap obstacle not found",
            )

    row = db.execute(
        """
        SELECT id, label, kind, linked_plot_id,
               latitude, longitude, height_m, crown_radius_m, active
        FROM shademap_obstacles
        WHERE id = %s AND garden_id = %s
        """,
        (obstacle_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="ShadeMap obstacle not found")
    return _serialize_shademap_obstacle(row)


def replace_shademap_obstacles(
    db: DbConn,
    obstacles: list[dict[str, object]],
    *,
    garden_id: int,
) -> list[dict[str, object]]:
    db.execute("DELETE FROM shademap_obstacles WHERE garden_id = %s", (garden_id,))
    return [_save_shademap_obstacle(db, garden_id, o) for o in obstacles]


@router.get("/shademap/state")
def get_shademap_state_api(request: FastAPIRequest, db: DB) -> dict[str, object]:
    return get_shademap_state(db, garden_id=_active_garden_id(request))


@router.patch("/shademap/state")
def update_shademap_state(
    body: ShadeMapStateBody,
    request: FastAPIRequest,
    db: DB,
) -> dict[str, object]:
    garden_id = _active_garden_id(request)
    updated = set_shademap_state(db, body.model_dump(), garden_id=garden_id)
    db.commit()
    return updated


@router.get("/shademap/calibration")
def get_shademap_calibration_api(request: FastAPIRequest, db: DB) -> dict[str, object]:
    return get_shademap_calibration(db, garden_id=_active_garden_id(request))


@router.patch("/shademap/calibration")
def update_shademap_calibration(
    body: ShadeMapCalibrationBody,
    request: FastAPIRequest,
    db: DB,
) -> dict[str, object]:
    garden_id = _active_garden_id(request)
    updated = set_shademap_calibration(db, body.model_dump(), garden_id=garden_id)
    db.execute(
        "DELETE FROM shademap_cache WHERE garden_id = %s AND cache_kind = 'terrain-tile'",
        (garden_id,),
    )
    db.commit()
    return updated


@router.get("/shademap/obstacles")
def list_shademap_obstacles_api(request: FastAPIRequest, db: DB) -> list[dict[str, object]]:
    return list_shademap_obstacles(db, garden_id=_active_garden_id(request))


@router.post("/shademap/obstacles", status_code=201)
def create_shademap_obstacle(
    body: ShadeMapObstacleBody,
    request: FastAPIRequest,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    created = _save_shademap_obstacle(
        db,
        _active_garden_id(request),
        body.model_dump(),
        allow_unowned_plot_links=_is_local_admin_fallback(context),
    )
    db.commit()
    return created


@router.patch("/shademap/obstacles/{obstacle_id}")
def update_shademap_obstacle(
    obstacle_id: int,
    body: ShadeMapObstacleBody,
    request: FastAPIRequest,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    updated = _save_shademap_obstacle(
        db,
        _active_garden_id(request),
        body.model_dump(),
        obstacle_id=obstacle_id,
        allow_unowned_plot_links=_is_local_admin_fallback(context),
    )
    db.commit()
    return updated


@router.delete("/shademap/obstacles/{obstacle_id}")
def delete_shademap_obstacle(
    obstacle_id: int,
    request: FastAPIRequest,
    db: DB,
) -> dict[str, object]:
    garden_id = _active_garden_id(request)
    deleted = db.execute(
        "DELETE FROM shademap_obstacles WHERE id = %s AND garden_id = %s",
        (obstacle_id, garden_id),
    )
    if deleted.rowcount == 0:
        raise HTTPException(status_code=404, detail="ShadeMap obstacle not found")
    db.commit()
    return {"status": "ok"}
