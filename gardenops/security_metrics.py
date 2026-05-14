from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Any

from gardenops.db import current_timestamp_ms, get_db, return_db

_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = defaultdict(int)
_EVENTS: dict[str, deque[float]] = defaultdict(deque)
_MAX_WINDOW_SECONDS = 3600


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _prune(now: float) -> None:
    cutoff = now - _MAX_WINDOW_SECONDS
    for key, values in _EVENTS.items():
        while values and values[0] < cutoff:
            values.popleft()


def record_security_event(metric: str, count: int = 1) -> None:
    now = time.monotonic()
    safe_count = max(1, int(count))
    with _LOCK:
        _COUNTERS[metric] += safe_count
        values = _EVENTS[metric]
        for _ in range(safe_count):
            values.append(now)
        _prune(now)


def security_event_rate(metric: str, window_seconds: int) -> int:
    now = time.monotonic()
    cutoff = now - max(1, window_seconds)
    with _LOCK:
        values = _EVENTS.get(metric)
        if not values:
            return 0
        while values and values[0] < now - _MAX_WINDOW_SECONDS:
            values.popleft()
        return sum(1 for ts in values if ts >= cutoff)


def _ratio_pct(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return max(0, min(100, int(round((numerator / denominator) * 100))))


def _recent_destructive_admin_garden_ids(window_seconds: int = 300) -> list[int]:
    cutoff_ms = current_timestamp_ms() - (max(1, window_seconds) * 1000)
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT garden_id
            FROM audit_events
            WHERE occurred_at_ms >= %s
              AND garden_id IS NOT NULL
              AND status_code BETWEEN 200 AND 299
              AND (
                path IN (
                        '/api/plots/import',
                        '/api/auth/revoke-user-sessions',
                        '/api/auth/revoke-all-sessions',
                        '/api/auth/emergency-read-only'
                    )
                    OR (
                        path LIKE '/api/auth/users/%%/revoke-sessions'
                        AND method = 'POST'
                    )
              )
            ORDER BY garden_id
            """,
            (cutoff_ms,),
        ).fetchall()
        return [int(row["garden_id"]) for row in rows if row["garden_id"] is not None]
    except Exception:
        return []
    finally:
        return_db(conn)


def _provider_budget_snapshot() -> dict[str, object]:
    try:
        import gardenops.rate_limit as rate_limit_module

        return rate_limit_module.provider_budget_snapshot()
    except Exception:
        return {
            "day": "",
            "features": [],
            "active_concurrency": {},
        }


def security_metrics_snapshot() -> dict[str, Any]:
    with _LOCK:
        counters = dict(_COUNTERS)
    recent_destructive_admin_garden_ids = _recent_destructive_admin_garden_ids()
    provider_limits = _provider_budget_snapshot()
    exporter: dict[str, object]
    try:
        from gardenops.security_telemetry import security_telemetry_status

        exporter = security_telemetry_status()
    except Exception:
        exporter = {
            "enabled": False,
            "destination": "",
            "pending_count": 0,
            "oldest_pending_at_ms": None,
            "last_attempt_at_ms": None,
            "last_success_at_ms": None,
            "last_error": "",
            "snapshot_interval_seconds": 0,
            "poll_interval_seconds": 0,
        }
    shademap_features_requests_per_5m = security_event_rate("shademap_features_requests_total", 300)
    shademap_features_cache_misses_per_5m = security_event_rate(
        "shademap_features_cache_misses",
        300,
    )
    shademap_terrain_requests_per_5m = security_event_rate("shademap_terrain_requests_total", 300)
    shademap_terrain_remote_misses_per_5m = security_event_rate(
        "shademap_terrain_remote_misses",
        300,
    )
    return {
        "counters": counters,
        "rates": {
            "auth_failures_per_minute": security_event_rate("auth_failures", 60),
            "auth_login_failures_per_minute": security_event_rate("auth_login_failures", 60),
            "auth_login_failures_admin_per_minute": security_event_rate(
                "auth_login_failures_admin",
                60,
            ),
            "rate_limit_hits_per_minute": security_event_rate("rate_limit_hits", 60),
            "mutations_per_minute": security_event_rate("mutations_total", 60),
            "destructive_admin_actions_per_5m": security_event_rate(
                "destructive_admin_actions",
                300,
            ),
            "invalid_reset_password_attempts_per_5m": security_event_rate(
                "invalid_reset_password_attempts",
                300,
            ),
            "invalid_invitation_attempts_per_5m": security_event_rate(
                "invalid_invitation_attempts",
                300,
            ),
            "ai_requests_per_5m": security_event_rate("ai_requests_total", 300),
            "ai_provider_failures_per_5m": security_event_rate("ai_provider_failures", 300),
            "provider_budget_hits_per_5m": security_event_rate("provider_budget_hits", 300),
            "concurrency_limit_hits_per_5m": security_event_rate(
                "concurrency_limit_hits",
                300,
            ),
            "shademap_features_requests_per_5m": shademap_features_requests_per_5m,
            "shademap_features_cache_misses_per_5m": shademap_features_cache_misses_per_5m,
            "shademap_features_cache_miss_ratio_pct_5m": _ratio_pct(
                shademap_features_cache_misses_per_5m,
                shademap_features_requests_per_5m,
            ),
            "shademap_terrain_requests_per_5m": shademap_terrain_requests_per_5m,
            "shademap_terrain_remote_misses_per_5m": shademap_terrain_remote_misses_per_5m,
            "shademap_terrain_remote_miss_ratio_pct_5m": _ratio_pct(
                shademap_terrain_remote_misses_per_5m,
                shademap_terrain_requests_per_5m,
            ),
            "shademap_upstream_failures_per_5m": security_event_rate(
                "shademap_upstream_failures",
                300,
            ),
        },
        "garden_scope": {
            "recent_destructive_admin_garden_ids": recent_destructive_admin_garden_ids,
        },
        "provider_limits": provider_limits,
        "exporter": exporter,
    }


def security_alerts_snapshot() -> dict[str, Any]:
    thresholds = {
        "auth_failures_per_minute": _env_int("ALERT_AUTH_FAILURES_PER_MINUTE", 30),
        "auth_login_failures_admin_per_minute": _env_int(
            "ALERT_ADMIN_LOGIN_FAILURES_PER_MINUTE",
            5,
        ),
        "rate_limit_hits_per_minute": _env_int("ALERT_RATE_LIMIT_HITS_PER_MINUTE", 60),
        "mutations_per_minute": _env_int("ALERT_MUTATIONS_PER_MINUTE", 120),
        "destructive_admin_actions_per_5m": _env_int(
            "ALERT_DESTRUCTIVE_ADMIN_ACTIONS_PER_5M",
            1,
        ),
        "invalid_reset_password_attempts_per_5m": _env_int(
            "ALERT_INVALID_RESET_PASSWORD_ATTEMPTS_PER_5M",
            8,
        ),
        "invalid_invitation_attempts_per_5m": _env_int(
            "ALERT_INVALID_INVITATION_ATTEMPTS_PER_5M",
            8,
        ),
        "ai_provider_failures_per_5m": _env_int("ALERT_AI_PROVIDER_FAILURES_PER_5M", 15),
        "provider_budget_hits_per_5m": _env_int("ALERT_PROVIDER_BUDGET_HITS_PER_5M", 5),
        "concurrency_limit_hits_per_5m": _env_int("ALERT_CONCURRENCY_LIMIT_HITS_PER_5M", 5),
        "shademap_features_cache_misses_per_5m": _env_int(
            "ALERT_SHADEMAP_FEATURES_CACHE_MISSES_PER_5M",
            12,
        ),
        "shademap_features_cache_miss_ratio_pct_5m": _env_int(
            "ALERT_SHADEMAP_FEATURES_CACHE_MISS_RATIO_PCT",
            70,
        ),
        "shademap_terrain_remote_misses_per_5m": _env_int(
            "ALERT_SHADEMAP_TERRAIN_REMOTE_MISSES_PER_5M",
            60,
        ),
        "shademap_terrain_remote_miss_ratio_pct_5m": _env_int(
            "ALERT_SHADEMAP_TERRAIN_REMOTE_MISS_RATIO_PCT",
            35,
        ),
        "shademap_upstream_failures_per_5m": _env_int("ALERT_SHADEMAP_FAILURES_PER_5M", 30),
    }
    metrics_snapshot = security_metrics_snapshot()
    rates: dict[str, Any] = metrics_snapshot["rates"]
    garden_scope: dict[str, Any] = metrics_snapshot.get("garden_scope", {})
    alerts = []
    for name, threshold in thresholds.items():
        value = int(rates.get(name, 0))
        if threshold > 0 and value >= threshold:
            alert: dict[str, Any] = {
                "name": name,
                "value": value,
                "threshold": threshold,
                "severity": "high",
            }
            if name == "destructive_admin_actions_per_5m":
                garden_ids = list(garden_scope.get("recent_destructive_admin_garden_ids", []))
                if garden_ids:
                    alert["garden_ids"] = garden_ids
            alerts.append(alert)

    features_misses = int(rates.get("shademap_features_cache_misses_per_5m", 0))
    features_ratio = int(rates.get("shademap_features_cache_miss_ratio_pct_5m", 0))
    features_requests = int(rates.get("shademap_features_requests_per_5m", 0))
    features_miss_threshold = _env_int("ALERT_SHADEMAP_FEATURES_CACHE_MISSES_PER_5M", 12)
    features_ratio_threshold = _env_int("ALERT_SHADEMAP_FEATURES_CACHE_MISS_RATIO_PCT", 70)
    if (
        features_miss_threshold > 0
        and features_misses >= features_miss_threshold
        and features_ratio >= features_ratio_threshold
    ):
        alerts.append(
            {
                "name": "shademap_features_cache_miss_spike_5m",
                "value": features_misses,
                "threshold": features_miss_threshold,
                "ratio_pct": features_ratio,
                "ratio_threshold_pct": features_ratio_threshold,
                "request_count": features_requests,
                "miss_count": features_misses,
                "severity": "high",
            },
        )

    terrain_misses = int(rates.get("shademap_terrain_remote_misses_per_5m", 0))
    terrain_ratio = int(rates.get("shademap_terrain_remote_miss_ratio_pct_5m", 0))
    terrain_requests = int(rates.get("shademap_terrain_requests_per_5m", 0))
    terrain_miss_threshold = _env_int("ALERT_SHADEMAP_TERRAIN_REMOTE_MISSES_PER_5M", 60)
    terrain_ratio_threshold = _env_int("ALERT_SHADEMAP_TERRAIN_REMOTE_MISS_RATIO_PCT", 35)
    if (
        terrain_miss_threshold > 0
        and terrain_misses >= terrain_miss_threshold
        and terrain_ratio >= terrain_ratio_threshold
    ):
        alerts.append(
            {
                "name": "shademap_terrain_remote_miss_spike_5m",
                "value": terrain_misses,
                "threshold": terrain_miss_threshold,
                "ratio_pct": terrain_ratio,
                "ratio_threshold_pct": terrain_ratio_threshold,
                "request_count": terrain_requests,
                "miss_count": terrain_misses,
                "severity": "high",
            },
        )
    return {"alerts": alerts, "thresholds": thresholds, "rates": rates}


def reset_security_metrics() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _EVENTS.clear()
