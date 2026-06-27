import os
import secrets
import threading
import time
from collections import deque
from typing import Any, cast

from fastapi import HTTPException, Request

from gardenops.db import DbConn, get_db, return_db
from gardenops.security_metrics import record_security_event

_LOCK = threading.Lock()
_BUCKETS: dict[str, deque[float]] = {}
_BUCKET_EXPIRES_AT: dict[str, float] = {}
_CONCURRENCY_LOCK = threading.Lock()
_CONCURRENCY_COUNTS: dict[str, int] = {}
_BACKEND_LOCK = threading.Lock()
_BACKEND: RateLimitBackend | None = None
DEFAULT_MAX_BUCKETS = 50000
_PROVIDER_LIMIT_PROFILES: dict[str, dict[str, object]] = {
    "ai-plant-lookup": {
        "label": "AI plant lookup",
        "user_limit_env": "AI_LOOKUP_DAILY_BUDGET_USER",
        "user_limit_default": 18,
        "garden_limit_env": "AI_LOOKUP_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 48,
        "concurrency_limit_env": "AI_LOOKUP_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 2,
    },
    "ai-garden-chat": {
        "label": "AI garden chat",
        "user_limit_env": "AI_CHAT_DAILY_BUDGET_USER",
        "user_limit_default": 24,
        "garden_limit_env": "AI_CHAT_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 72,
        "concurrency_limit_env": "AI_CHAT_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 4,
    },
    "ai-care-instructions": {
        "label": "AI care instructions",
        "user_limit_env": "AI_CARE_DAILY_BUDGET_USER",
        "user_limit_default": 180,
        "garden_limit_env": "AI_CARE_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 480,
        "concurrency_limit_env": "AI_CARE_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 1,
    },
    "ai-identify": {
        "label": "AI plant identification",
        "user_limit_env": "AI_IDENTIFY_DAILY_BUDGET_USER",
        "user_limit_default": 30,
        "garden_limit_env": "AI_IDENTIFY_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 80,
        "concurrency_limit_env": "AI_IDENTIFY_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 2,
    },
    "ai-task-descriptions": {
        "label": "AI task descriptions",
        "user_limit_env": "AI_TASK_DESCRIPTION_DAILY_BUDGET_USER",
        "user_limit_default": 60,
        "garden_limit_env": "AI_TASK_DESCRIPTION_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 180,
        "concurrency_limit_env": "AI_TASK_DESCRIPTION_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 1,
    },
    "ai-diagnose": {
        "label": "AI plant diagnosis",
        "user_limit_env": "AI_DIAGNOSE_DAILY_BUDGET_USER",
        "user_limit_default": 20,
        "garden_limit_env": "AI_DIAGNOSE_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 50,
        "concurrency_limit_env": "AI_DIAGNOSE_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 2,
    },
    "shademap-features-miss": {
        "label": "ShadeMap features miss",
        "user_limit_env": "SHADEMAP_FEATURES_MISS_DAILY_BUDGET_USER",
        "user_limit_default": 180,
        "garden_limit_env": "SHADEMAP_FEATURES_MISS_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 420,
        "concurrency_limit_env": "SHADEMAP_FEATURES_MISS_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 4,
    },
    "shademap-terrain-miss": {
        "label": "ShadeMap terrain miss",
        "user_limit_env": "SHADEMAP_TERRAIN_MISS_DAILY_BUDGET_USER",
        "user_limit_default": 900,
        "garden_limit_env": "SHADEMAP_TERRAIN_MISS_DAILY_BUDGET_GARDEN",
        "garden_limit_default": 2400,
        "concurrency_limit_env": "SHADEMAP_TERRAIN_MISS_CONCURRENCY_LIMIT",
        "concurrency_limit_default": 8,
    },
}


def _normalize_bucket_name(bucket: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in bucket.upper())


def _trust_proxy_headers() -> bool:
    return os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }


def _client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For when proxy headers are trusted."""
    if _trust_proxy_headers():
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip and "," not in real_ip:
            return real_ip
        forwarded = request.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            # The edge proxy overwrites XFF with its validated client address.
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _identity_key(request: Request) -> str:
    auth_context = getattr(request.state, "auth_context", None)
    if auth_context is not None and auth_context.user_id is not None:
        return f"user:{int(auth_context.user_id)}"
    host = _client_ip(request)
    return f"host:{host}"


def _client_key(request: Request, bucket: str) -> str:
    return f"{bucket}:identity:{_identity_key(request)}"


def _prune_expired_buckets(now: float) -> None:
    expired_keys = [key for key, expires_at in _BUCKET_EXPIRES_AT.items() if expires_at <= now]
    for key in expired_keys:
        _BUCKET_EXPIRES_AT.pop(key, None)
        _BUCKETS.pop(key, None)


def _evict_oldest_bucket() -> None:
    if not _BUCKET_EXPIRES_AT:
        return
    oldest_key = min(_BUCKET_EXPIRES_AT, key=_BUCKET_EXPIRES_AT.__getitem__)
    _BUCKET_EXPIRES_AT.pop(oldest_key, None)
    _BUCKETS.pop(oldest_key, None)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def env_nonneg_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return max(0, default)
    try:
        return max(0, int(raw))
    except ValueError:
        return max(0, default)


def _global_limit_for_bucket(bucket: str) -> int:
    specific = os.environ.get(
        f"RATE_LIMIT_GLOBAL_LIMIT_{_normalize_bucket_name(bucket)}",
        "",
    ).strip()
    if specific:
        try:
            val = int(specific)
            return max(1, val) if val > 0 else 0
        except ValueError:
            return 0
    raw = os.environ.get("RATE_LIMIT_GLOBAL_LIMIT", "").strip()
    if not raw:
        return 0
    try:
        val = int(raw)
        return max(1, val) if val > 0 else 0
    except ValueError:
        return 0


class ConcurrencyLease:
    def __init__(self, *, bucket: str, limit: int):
        self.bucket = bucket
        self.limit = max(0, int(limit))

    def __enter__(self) -> ConcurrencyLease:
        if self.limit <= 0:
            return self
        with _CONCURRENCY_LOCK:
            active = _CONCURRENCY_COUNTS.get(self.bucket, 0)
            if active >= self.limit:
                record_security_event("concurrency_limit_hits")
                record_security_event(
                    f"concurrency_limit_hits_{_normalize_bucket_name(self.bucket)}",
                )
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Concurrent request limit exceeded for {self.bucket}. Try again later."
                    ),
                )
            _CONCURRENCY_COUNTS[self.bucket] = active + 1
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.limit <= 0:
            return
        with _CONCURRENCY_LOCK:
            active = _CONCURRENCY_COUNTS.get(self.bucket, 0) - 1
            if active > 0:
                _CONCURRENCY_COUNTS[self.bucket] = active
            else:
                _CONCURRENCY_COUNTS.pop(self.bucket, None)


class RateLimitBackend:
    def consume(self, *, key: str, limit: int, window_seconds: int) -> bool:
        raise NotImplementedError

    def acquire_concurrency_slot(
        self,
        *,
        bucket: str,
        limit: int,
        ttl_seconds: int,
    ) -> str | None:
        return None

    def release_concurrency_slot(self, *, bucket: str, token: str) -> None:
        return None

    def reset(self) -> None:
        raise NotImplementedError


class InMemoryRateLimitBackend(RateLimitBackend):
    def consume(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        max_buckets = env_int("RATE_LIMIT_MAX_BUCKETS", DEFAULT_MAX_BUCKETS)
        with _LOCK:
            _prune_expired_buckets(now)
            if key not in _BUCKETS and len(_BUCKETS) >= max_buckets:
                _evict_oldest_bucket()
            entries = _BUCKETS.setdefault(key, deque())
            while entries and entries[0] < cutoff:
                entries.popleft()
            if len(entries) >= limit:
                return False
            entries.append(now)
            _BUCKET_EXPIRES_AT[key] = now + window_seconds
            return True

    def reset(self) -> None:
        with _LOCK:
            _BUCKETS.clear()
            _BUCKET_EXPIRES_AT.clear()


class RedisRateLimitBackend(RateLimitBackend):
    _LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  redis.call('EXPIRE', key, ttl)
  return 0
end
redis.call('ZADD', key, now_ms, member)
redis.call('EXPIRE', key, ttl)
return 1
"""
    _CONCURRENCY_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - ttl_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  redis.call('EXPIRE', key, ttl)
  return nil
end
redis.call('ZADD', key, now_ms, token)
redis.call('EXPIRE', key, ttl)
return token
"""

    def __init__(self, *, url: str, prefix: str):
        from redis import Redis

        socket_timeout = float(env_int("RATE_LIMIT_REDIS_SOCKET_TIMEOUT_SECONDS", 2))
        connect_timeout = float(env_int("RATE_LIMIT_REDIS_CONNECT_TIMEOUT_SECONDS", 2))
        self._client = Redis.from_url(
            url,
            socket_connect_timeout=connect_timeout,
            socket_timeout=socket_timeout,
            health_check_interval=30,
        )
        self._client.ping()
        self._prefix = prefix

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def consume(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{secrets.token_hex(6)}"
        ttl = max(window_seconds + 5, 10)
        result = self._client.eval(
            self._LUA,
            1,
            self._full_key(key),
            now_ms,
            window_seconds * 1000,
            limit,
            member,
            ttl,
        )
        return bool(result)

    def acquire_concurrency_slot(
        self,
        *,
        bucket: str,
        limit: int,
        ttl_seconds: int,
    ) -> str | None:
        now_ms = int(time.time() * 1000)
        lease_value = f"{now_ms}:{secrets.token_hex(12)}"
        ttl = max(ttl_seconds + 5, 10)
        result = self._client.eval(
            self._CONCURRENCY_LUA,
            1,
            self._full_key(f"concurrency:{bucket}"),
            now_ms,
            ttl_seconds * 1000,
            limit,
            lease_value,
            ttl,
        )
        if result is None:
            return None
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return str(result)

    def release_concurrency_slot(self, *, bucket: str, token: str) -> None:
        self._client.zrem(self._full_key(f"concurrency:{bucket}"), token)

    def reset(self) -> None:
        # Shared redis data should not be mass-deleted by local test helper.
        return


def active_concurrency_snapshot() -> dict[str, int]:
    with _CONCURRENCY_LOCK:
        return dict(_CONCURRENCY_COUNTS)


def provider_limit_profile(feature: str) -> dict[str, int | str]:
    profile = _PROVIDER_LIMIT_PROFILES.get(feature, {})
    label = str(profile.get("label", feature))
    user_limit = 0
    garden_limit = 0
    concurrency_limit = 0

    user_limit_env = str(profile.get("user_limit_env", "")).strip()
    if user_limit_env:
        user_limit = env_nonneg_int(
            user_limit_env,
            int(cast(int | float | str, profile.get("user_limit_default", 0))),
        )

    garden_limit_env = str(profile.get("garden_limit_env", "")).strip()
    if garden_limit_env:
        garden_limit = env_nonneg_int(
            garden_limit_env,
            int(cast(int | float | str, profile.get("garden_limit_default", 0))),
        )

    concurrency_limit_env = str(profile.get("concurrency_limit_env", "")).strip()
    if concurrency_limit_env:
        concurrency_limit = env_nonneg_int(
            concurrency_limit_env,
            int(cast(int | float | str, profile.get("concurrency_limit_default", 0))),
        )

    return {
        "feature": feature,
        "label": label,
        "user_limit": user_limit,
        "garden_limit": garden_limit,
        "concurrency_limit": concurrency_limit,
    }


class SharedConcurrencyLease:
    def __init__(self, *, backend: RateLimitBackend, bucket: str, limit: int):
        self.backend = backend
        self.bucket = bucket
        self.limit = max(0, int(limit))
        self.token = ""
        self.ttl_seconds = env_int("PROVIDER_CONCURRENCY_LEASE_TTL_SECONDS", 120)

    def __enter__(self) -> SharedConcurrencyLease:
        if self.limit <= 0:
            return self
        token = self.backend.acquire_concurrency_slot(
            bucket=self.bucket,
            limit=self.limit,
            ttl_seconds=self.ttl_seconds,
        )
        if not token:
            record_security_event("concurrency_limit_hits")
            record_security_event(
                f"concurrency_limit_hits_{_normalize_bucket_name(self.bucket)}",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Concurrent request limit exceeded for {self.bucket}. Try again later.",
            )
        self.token = token
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.limit <= 0 or not self.token:
            return
        try:
            self.backend.release_concurrency_slot(bucket=self.bucket, token=self.token)
        except Exception:
            record_security_event("concurrency_release_failures")
            record_security_event(
                f"concurrency_release_failures_{_normalize_bucket_name(self.bucket)}",
            )
        finally:
            self.token = ""


def _backend_supports_shared_concurrency(backend: RateLimitBackend) -> bool:
    return type(backend).acquire_concurrency_slot is not RateLimitBackend.acquire_concurrency_slot


def acquire_concurrency_slot(
    *, bucket: str, limit: int
) -> ConcurrencyLease | SharedConcurrencyLease:
    backend = _get_backend()
    if _backend_supports_shared_concurrency(backend):
        return SharedConcurrencyLease(backend=backend, bucket=bucket, limit=limit)
    return ConcurrencyLease(bucket=bucket, limit=limit)


def _provider_usage_day(now_ms: int | None = None) -> str:
    ts = time.time() if now_ms is None else max(0, int(now_ms)) / 1000
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _provider_usage_count(
    conn: DbConn,
    *,
    usage_day: str,
    feature: str,
    scope_type: str,
    scope_id: int,
) -> int:
    row = conn.execute(
        """
        SELECT request_count
        FROM provider_daily_usage
        WHERE usage_day = %s AND feature = %s AND scope_type = %s AND scope_id = %s
        """,
        (usage_day, feature, scope_type, scope_id),
    ).fetchone()
    if not row:
        return 0
    return int(row["request_count"])


def _reserve_provider_usage(
    conn: DbConn,
    *,
    usage_day: str,
    feature: str,
    scope_type: str,
    scope_id: int,
    limit: int,
    request_count: int,
    now_ms: int,
) -> int:
    updated = conn.execute(
        """
        INSERT INTO provider_daily_usage (
            usage_day,
            feature,
            scope_type,
            scope_id,
            request_count,
            last_request_at_ms
        )
        SELECT %s, %s, %s, %s, %s, %s
        WHERE %s <= %s
        ON CONFLICT(usage_day, feature, scope_type, scope_id)
        DO UPDATE SET
            request_count = provider_daily_usage.request_count + excluded.request_count,
            last_request_at_ms = excluded.last_request_at_ms
        WHERE provider_daily_usage.request_count + excluded.request_count <= %s
        RETURNING request_count
        """,
        (
            usage_day,
            feature,
            scope_type,
            scope_id,
            request_count,
            now_ms,
            request_count,
            limit,
            limit,
        ),
    ).fetchone()
    if not updated:
        _record_provider_budget_hit(feature, scope_type)
        scope_label = scope_type.capitalize()
        raise HTTPException(
            status_code=429,
            detail=(f"{scope_label} daily budget exhausted for {feature}. Try again tomorrow."),
        )
    return int(updated["request_count"])


def _record_provider_budget_hit(feature: str, scope_type: str) -> None:
    feature_metric = _normalize_bucket_name(feature)
    scope_metric = _normalize_bucket_name(scope_type)
    record_security_event("provider_budget_hits")
    record_security_event(f"provider_budget_hits_{feature_metric}")
    record_security_event(f"provider_budget_hits_{feature_metric}_{scope_metric}")


def reserve_daily_provider_budget(
    conn: DbConn,
    *,
    feature: str,
    user_id: int | None = None,
    garden_id: int | None = None,
    user_limit: int = 0,
    garden_limit: int = 0,
    request_count: int = 1,
    now_ms: int | None = None,
) -> dict[str, Any]:
    effective_request_count = max(1, int(request_count))
    effective_now_ms = max(0, int(now_ms if now_ms is not None else time.time() * 1000))
    usage_day = _provider_usage_day(effective_now_ms)
    checkpoints: list[tuple[str, int, int]] = []
    if user_limit > 0 and user_id is not None and user_id > 0:
        checkpoints.append(("user", int(user_id), int(user_limit)))
    if garden_limit > 0 and garden_id is not None and garden_id > 0:
        checkpoints.append(("garden", int(garden_id), int(garden_limit)))
    if not checkpoints:
        return {"day": usage_day, "feature": feature}

    status: dict[str, Any] = {"day": usage_day, "feature": feature}
    try:
        for scope_type, scope_id, limit in checkpoints:
            used = _reserve_provider_usage(
                conn,
                usage_day=usage_day,
                feature=feature,
                scope_type=scope_type,
                scope_id=scope_id,
                limit=limit,
                request_count=effective_request_count,
                now_ms=effective_now_ms,
            )
            status[scope_type] = {
                "scope_id": scope_id,
                "used": used,
                "limit": limit,
            }
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return status


def provider_budget_snapshot() -> dict[str, object]:
    usage_day = _provider_usage_day()
    active_concurrency = active_concurrency_snapshot()
    snapshot: dict[str, object] = {
        "day": usage_day,
        "features": [],
        "active_concurrency": active_concurrency,
    }
    conn = get_db()
    try:
        feature_rows: list[dict[str, object]] = []
        for feature in _PROVIDER_LIMIT_PROFILES:
            config = provider_limit_profile(feature)
            totals: dict[str, int] = {"user": 0, "garden": 0}
            for row in conn.execute(
                """
                SELECT scope_type, COALESCE(SUM(request_count), 0) AS total_requests
                FROM provider_daily_usage
                WHERE usage_day = %s AND feature = %s
                GROUP BY scope_type
                """,
                (usage_day, feature),
            ).fetchall():
                totals[str(row["scope_type"])] = int(row["total_requests"] or 0)

            feature_summary: dict[str, object] = {
                "feature": feature,
                "label": config["label"],
                "user_limit": int(config["user_limit"]),
                "garden_limit": int(config["garden_limit"]),
                "concurrency_limit": int(config["concurrency_limit"]),
                "active_concurrency": int(active_concurrency.get(feature, 0)),
                "user_total_requests": totals.get("user", 0),
                "garden_total_requests": totals.get("garden", 0),
                "top_user_scope": None,
                "top_garden_scope": None,
            }
            for scope_type in ("user", "garden"):
                row = conn.execute(
                    """
                    SELECT scope_id, request_count
                    FROM provider_daily_usage
                    WHERE usage_day = %s AND feature = %s AND scope_type = %s
                    ORDER BY request_count DESC, scope_id ASC
                    LIMIT 1
                    """,
                    (usage_day, feature, scope_type),
                ).fetchone()
                if row:
                    feature_summary[f"top_{scope_type}_scope"] = {
                        "scope_id": int(row["scope_id"]),
                        "request_count": int(row["request_count"]),
                        "limit": int(config[f"{scope_type}_limit"]),
                    }
            feature_rows.append(feature_summary)
        snapshot["features"] = feature_rows
        return snapshot
    finally:
        return_db(conn)


def _make_backend() -> RateLimitBackend:
    backend_name = os.environ.get("RATE_LIMIT_BACKEND", "").strip().lower() or "memory"
    if backend_name != "redis":
        app_env = os.environ.get("APP_ENV", "").strip().lower()
        internet_exposed = os.environ.get("INTERNET_EXPOSED", "false").strip().lower() == "true"
        multi_instance = os.environ.get("MULTI_INSTANCE", "false").strip().lower() == "true"
        if app_env in {"prod", "production"} or internet_exposed or multi_instance:
            raise RuntimeError(
                "APP_ENV=production, INTERNET_EXPOSED=true, or MULTI_INSTANCE=true "
                "requires RATE_LIMIT_BACKEND=redis",
            )
        return InMemoryRateLimitBackend()
    redis_url = (
        os.environ.get("RATE_LIMIT_REDIS_URL", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
    )
    if not redis_url:
        raise RuntimeError(
            "RATE_LIMIT_BACKEND=redis requires RATE_LIMIT_REDIS_URL or REDIS_URL",
        )
    prefix = os.environ.get("RATE_LIMIT_REDIS_PREFIX", "gardenops:ratelimit").strip()
    try:
        return RedisRateLimitBackend(url=redis_url, prefix=prefix or "gardenops:ratelimit")
    except Exception:
        raise RuntimeError("RATE_LIMIT_BACKEND=redis but redis is unavailable") from None


def _get_backend() -> RateLimitBackend:
    global _BACKEND  # noqa: PLW0603
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = _make_backend()
        return _BACKEND


def ensure_backend_ready() -> None:
    _get_backend()


def enforce_rate_limit(
    request: Request,
    *,
    bucket: str,
    limit: int,
    window_seconds: int,
) -> None:
    enforce_layered_rate_limit(
        request,
        bucket=bucket,
        identity_limit=limit,
        window_seconds=window_seconds,
    )


def enforce_key_rate_limit(
    *,
    bucket: str,
    key: str,
    limit: int,
    window_seconds: int,
    scope_label: str | None = None,
) -> None:
    normalized_key = key.strip()
    if not normalized_key or limit <= 0:
        return
    backend = _get_backend()
    _consume_or_raise(
        backend=backend,
        key=f"{bucket}:key:{normalized_key}",
        bucket=bucket,
        limit=limit,
        window_seconds=window_seconds,
        scope_label=scope_label,
    )


def _consume_or_raise(
    *,
    backend: RateLimitBackend,
    key: str,
    bucket: str,
    limit: int,
    window_seconds: int,
    scope_label: str | None = None,
    request: Request | None = None,
) -> None:
    if limit <= 0:
        return
    if backend.consume(key=key, limit=limit, window_seconds=window_seconds):
        # Store rate limit info on request state for response headers
        if request is not None:
            if not hasattr(request.state, "rate_limit_info"):
                request.state.rate_limit_info = {
                    "limit": limit,
                    "window": window_seconds,
                }
        return
    record_security_event("rate_limit_hits")
    record_security_event(f"rate_limit_hits_{_normalize_bucket_name(bucket)}")
    detail_prefix = f"{scope_label} " if scope_label else ""
    raise HTTPException(
        status_code=429,
        detail=f"{detail_prefix}rate limit exceeded for {bucket}. Try again later.",
        headers={"Retry-After": str(window_seconds)},
    )


def enforce_layered_rate_limit(
    request: Request,
    *,
    bucket: str,
    identity_limit: int,
    window_seconds: int,
    user_limit: int = 0,
    garden_limit: int = 0,
    global_limit: int | None = None,
) -> None:
    backend = _get_backend()
    identity_key = _client_key(request, bucket)
    _consume_or_raise(
        backend=backend,
        key=identity_key,
        bucket=bucket,
        limit=identity_limit,
        window_seconds=window_seconds,
        request=request,
    )

    auth_context = getattr(request.state, "auth_context", None)
    if user_limit > 0 and auth_context is not None and auth_context.user_id is not None:
        _consume_or_raise(
            backend=backend,
            key=f"{bucket}:user:{int(auth_context.user_id)}",
            bucket=bucket,
            limit=user_limit,
            window_seconds=window_seconds,
            scope_label="User",
        )

    if garden_limit > 0 and auth_context is not None and auth_context.garden_id is not None:
        _consume_or_raise(
            backend=backend,
            key=f"{bucket}:garden:{int(auth_context.garden_id)}",
            bucket=bucket,
            limit=garden_limit,
            window_seconds=window_seconds,
            scope_label="Garden",
        )

    effective_global_limit = (
        _global_limit_for_bucket(bucket) if global_limit is None else max(0, int(global_limit))
    )
    if effective_global_limit > 0:
        _consume_or_raise(
            backend=backend,
            key=f"{bucket}:__global__",
            bucket=bucket,
            limit=effective_global_limit,
            window_seconds=window_seconds,
            scope_label="Global",
        )


def reset_rate_limits() -> None:
    global _BACKEND  # noqa: PLW0603
    with _BACKEND_LOCK:
        if _BACKEND is not None:
            _BACKEND.reset()
        _BACKEND = None
    with _LOCK:
        _BUCKETS.clear()
        _BUCKET_EXPIRES_AT.clear()
    with _CONCURRENCY_LOCK:
        _CONCURRENCY_COUNTS.clear()
