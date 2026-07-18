"""Test-only ASGI wrapper used by the Phase 9 performance runner."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from pathlib import Path
from typing import Any

from gardenops import db as gardenops_db
from gardenops.main import app as gardenops_app
from gardenops.performance_queries import (
    QueryEvidenceCollector,
    activate_query_execution_collector,
    reset_query_execution_collector,
)

ASGIScope = MutableMapping[str, Any]
ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]
EnvironmentVerifier = Callable[[Mapping[str, str]], None]


def require_disposable_e2e_environment(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Refuse to run outside the disposable PostgreSQL runner environment."""
    env = os.environ if environ is None else environ
    if env.get("APP_ENV") != "test":
        raise RuntimeError("Phase 9 performance app requires APP_ENV=test")

    database_url = env.get("DATABASE_URL", "")
    disposable_url = env.get("GARDENOPS_DISPOSABLE_POSTGRES_URL", "")
    if not disposable_url or database_url != disposable_url:
        raise RuntimeError(
            "Phase 9 performance app requires DATABASE_URL to exactly match "
            "GARDENOPS_DISPOSABLE_POSTGRES_URL"
        )

    marker = env.get("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "").strip()
    system_identifier = env.get("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", "").strip()
    if not marker or not system_identifier or not system_identifier.isdecimal():
        raise RuntimeError(
            "Phase 9 performance app requires the runner-issued disposable "
            "PostgreSQL marker and system identifier"
        )
    if not marker.startswith(f"{system_identifier}."):
        raise RuntimeError(
            "Phase 9 performance app requires a marker bound to the "
            "runner-issued PostgreSQL system identifier"
        )
    if env.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD") != "1":
        raise RuntimeError("Phase 9 performance app requires complete-journey child mode")


def verify_disposable_database_binding(environ: Mapping[str, str]) -> None:
    """Confirm the process is connected to the runner-marked disposable database."""
    connection = gardenops_db.get_db()
    try:
        row = connection.execute(
            "SELECT current_setting('gardenops.disposable_marker', true) AS marker"
        ).fetchone()
        observed = row["marker"] if isinstance(row, dict) else None
    finally:
        gardenops_db.return_db(connection)
    if observed != environ["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"]:
        raise RuntimeError("Phase 9 performance app database marker does not match the runner")


def _probe_request(scope: ASGIScope) -> bool:
    if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/api/"):
        return False
    headers = scope.get("headers", ())
    return any(
        name.lower() == b"x-gardenops-performance-probe" and value == b"1"
        for name, value in headers
    )


def _probe_label(scope: ASGIScope) -> str:
    for name, value in scope.get("headers", ()):
        if name.lower() != b"x-gardenops-performance-probe-label":
            continue
        try:
            label = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Phase 9 query evidence label must be ASCII") from exc
        if not re.fullmatch(r"[a-z0-9-]{1,80}", label):
            raise RuntimeError("Phase 9 query evidence label is invalid")
        return label
    return ""


def _query_evidence_path(environ: Mapping[str, str]) -> Path | None:
    raw_path = environ.get("GARDENOPS_PERFORMANCE_QUERY_EVIDENCE_PATH", "")
    if not raw_path:
        return None
    artifact_raw = environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR", "")
    if not artifact_raw:
        raise RuntimeError(
            "Phase 9 query evidence requires the complete-journey artifact directory"
        )
    artifact_directory = Path(artifact_raw).resolve(strict=True)
    path = Path(raw_path).resolve()
    if path.parent != artifact_directory or path.name != "phase-nine-query-evidence.jsonl":
        raise RuntimeError("Phase 9 query evidence path must be a direct artifact child")
    return path


def _append_query_evidence(path: Path, value: dict[str, object]) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


class PerformanceASGIApp:
    """Guard a test app and report time to its HTTP response start."""

    def __init__(
        self,
        application: ASGIApp,
        *,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        binding_verifier: EnvironmentVerifier = verify_disposable_database_binding,
    ) -> None:
        self.application = application
        self.environ = environ
        self.clock = clock
        self.binding_verifier = binding_verifier

    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"lifespan", "http"}:
            await self.application(scope, receive, send)
            return

        environment = os.environ if self.environ is None else self.environ
        require_disposable_e2e_environment(environment)
        if scope_type == "lifespan":
            self.binding_verifier(environment)
            await self.application(scope, receive, send)
            return

        started = self.clock()
        collecting = _probe_request(scope)
        probe_label = _probe_label(scope) if collecting else ""
        collector = QueryEvidenceCollector() if collecting else None
        collector_context = activate_query_execution_collector(collector) if collector else None
        response_status: int | None = None

        async def send_with_timing(message: ASGIMessage) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                raw_status = message.get("status")
                response_status = raw_status if isinstance(raw_status, int) else None
                duration_ms = (self.clock() - started) * 1000
                headers = list(message.get("headers", ()))
                headers.append((b"server-timing", f"app;dur={duration_ms:.3f}".encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.application(scope, receive, send_with_timing)
        finally:
            if collector_context is not None:
                reset_query_execution_collector(collector_context)
            if collector is not None:
                evidence_path = _query_evidence_path(environment)
                if evidence_path is not None:
                    _append_query_evidence(
                        evidence_path,
                        {
                            "method": str(scope.get("method", "")),
                            "path": str(scope.get("path", "")),
                            "probe_label": probe_label,
                            "status": response_status,
                            **collector.snapshot(),
                        },
                    )


def create_performance_app(
    application: ASGIApp,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    binding_verifier: EnvironmentVerifier = verify_disposable_database_binding,
) -> PerformanceASGIApp:
    """Construct an independently testable performance wrapper."""
    return PerformanceASGIApp(
        application,
        environ=environ,
        clock=clock,
        binding_verifier=binding_verifier,
    )


app = create_performance_app(gardenops_app)
