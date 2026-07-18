"""Tests for the Phase 9 test-only ASGI performance wrapper."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Any

import pytest

from scripts.e2e.performanceFastapiApp import create_performance_app

DISPOSABLE_URL = "postgresql://runner@127.0.0.1:55432/gardenops_test"


def _safe_environment() -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "DATABASE_URL": DISPOSABLE_URL,
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": "1",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": DISPOSABLE_URL,
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "123.runner-marker",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
    }


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _run_app(
    app: Any,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    path: str = "/",
    scope_type: str = "http",
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {"type": scope_type}
    if scope_type == "http":
        scope.update({"headers": headers or [], "method": "GET", "path": path})
    asyncio.run(app(scope, _receive, send))
    return messages


def _clock(values: Iterable[float]) -> Any:
    iterator = iter(values)
    return lambda: next(iterator)


async def _fake_app(
    _scope: dict[str, Any],
    _receive: Any,
    send: Any,
) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("APP_ENV", "production", "APP_ENV=test"),
        ("DATABASE_URL", "postgresql://wrong/database", "exactly match"),
        ("GARDENOPS_DISPOSABLE_POSTGRES_URL", "", "exactly match"),
        ("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "", "marker"),
        ("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", "", "system identifier"),
        ("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "999.not-this-run", "bound"),
        ("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD", "", "child mode"),
    ],
)
def test_wrapper_refuses_startup_without_disposable_runner_environment(
    name: str,
    value: str,
    message: str,
) -> None:
    environment = _safe_environment()
    environment[name] = value
    app = create_performance_app(_fake_app, environ=environment)

    with pytest.raises(RuntimeError, match=message):
        _run_app(app, scope_type="lifespan")


def test_wrapper_adds_application_duration_server_timing() -> None:
    app = create_performance_app(
        _fake_app,
        environ=_safe_environment(),
        clock=_clock((10.0, 10.012345)),
    )

    messages = _run_app(app)

    assert messages[0]["headers"] == [(b"server-timing", b"app;dur=12.345")]


def test_wrapper_preserves_existing_server_timing_header() -> None:
    async def app_with_timing(_scope: dict[str, Any], _receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"server-timing", b"db;dur=4.2")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    app = create_performance_app(
        app_with_timing,
        environ=_safe_environment(),
        clock=_clock((5.0, 5.001)),
    )

    messages = _run_app(app)

    assert messages[0]["headers"] == [
        (b"server-timing", b"db;dur=4.2"),
        (b"server-timing", b"app;dur=1.000"),
    ]


def test_wrapper_verifies_database_binding_only_during_lifespan() -> None:
    verified: list[dict[str, str]] = []
    environment = _safe_environment()
    app = create_performance_app(
        _fake_app,
        environ=environment,
        binding_verifier=lambda env: verified.append(dict(env)),
    )

    _run_app(app)
    _run_app(app, scope_type="lifespan")

    assert verified == [environment]


def test_wrapper_writes_parameter_free_probe_query_evidence(tmp_path: Any) -> None:
    environment = _safe_environment()
    evidence_path = tmp_path / "phase-nine-query-evidence.jsonl"
    environment.update(
        {
            "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": str(tmp_path),
            "GARDENOPS_PERFORMANCE_QUERY_EVIDENCE_PATH": str(evidence_path),
        }
    )
    app = create_performance_app(
        _fake_app,
        environ=environment,
        binding_verifier=lambda _env: None,
    )

    _run_app(
        app,
        headers=[(b"x-gardenops-performance-probe", b"1")],
        path="/api/plots",
    )

    assert evidence_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == {
        "batch_rows": 0,
        "method": "GET",
        "path": "/api/plots",
        "probe_label": "",
        "query_count": 0,
        "statement_fingerprints": {},
        "status": 200,
    }
