from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import seed_optimization_journeys_e2e

ROOT = Path(__file__).resolve().parents[1]
DISPOSABLE_URL = "postgresql://gardenops@127.0.0.1:55433/gardenops_test"
DISPOSABLE_SYSTEM_IDENTIFIER = "987654321"
DISPOSABLE_MARKER = f"{DISPOSABLE_SYSTEM_IDENTIFIER}.runner-issued-nonce"


def _set_safe_seed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_MODE", "session")
    monkeypatch.setenv("GARDENOPS_ALLOW_DESTRUCTIVE_E2E", "1")
    monkeypatch.setenv("GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE", "1")
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", DISPOSABLE_URL)
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", DISPOSABLE_MARKER)
    monkeypatch.setenv(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        DISPOSABLE_SYSTEM_IDENTIFIER,
    )


def test_optimization_seed_requires_exact_runner_database_and_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)

    seed_optimization_journeys_e2e.require_optimization_journeys_e2e_database(DISPOSABLE_URL)

    with pytest.raises(RuntimeError, match="exactly match"):
        seed_optimization_journeys_e2e.require_optimization_journeys_e2e_database(
            "postgresql://gardenops@127.0.0.1:55434/gardenops_test"
        )
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        seed_optimization_journeys_e2e.require_optimization_journeys_e2e_database(DISPOSABLE_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ALLOW_DESTRUCTIVE_E2E", "0")
    with pytest.raises(RuntimeError, match="GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1"):
        seed_optimization_journeys_e2e.require_optimization_journeys_e2e_database(DISPOSABLE_URL)
    monkeypatch.setenv("GARDENOPS_ALLOW_DESTRUCTIVE_E2E", "1")
    monkeypatch.setenv(
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "postgresql://gardenops@127.0.0.1:5432/gardenops_test",
    )
    with pytest.raises(RuntimeError, match="port 5432"):
        seed_optimization_journeys_e2e.require_optimization_journeys_e2e_database(
            "postgresql://gardenops@127.0.0.1:5432/gardenops_test"
        )


def test_optimization_seed_requires_database_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)

    class MarkerConnection:
        def __init__(self, marker: str, system_identifier: str) -> None:
            self.marker = marker
            self.system_identifier = system_identifier

        def execute(self, statement: str, params: tuple[str, ...] | None = None) -> SimpleNamespace:
            if "current_setting" in statement:
                assert params == ("gardenops.disposable_marker",)
                row = {"disposable_marker": self.marker}
            elif "pg_control_system" in statement:
                row = {"system_identifier": self.system_identifier}
            else:
                raise AssertionError(f"Unexpected query: {statement}")
            return SimpleNamespace(fetchone=lambda: row)

    seed_optimization_journeys_e2e.verify_optimization_journeys_e2e_database_marker(
        MarkerConnection(DISPOSABLE_MARKER, DISPOSABLE_SYSTEM_IDENTIFIER)
    )
    with pytest.raises(RuntimeError, match="does not match"):
        seed_optimization_journeys_e2e.verify_optimization_journeys_e2e_database_marker(
            MarkerConnection("wrong-marker", DISPOSABLE_SYSTEM_IDENTIFIER)
        )
    with pytest.raises(RuntimeError, match="system identifier"):
        seed_optimization_journeys_e2e.verify_optimization_journeys_e2e_database_marker(
            MarkerConnection(DISPOSABLE_MARKER, "123")
        )


def test_optimization_journey_scripts_have_valid_syntax() -> None:
    scripts = [
        ROOT / "scripts" / "seed_optimization_journeys_e2e.py",
        ROOT / "scripts" / "check_optimization_journeys_e2e.cjs",
        ROOT / "scripts" / "run_optimization_journeys_e2e.sh",
    ]
    python_result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(scripts[0])],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert python_result.returncode == 0, python_result.stderr
    node_result = subprocess.run(
        ["node", "--check", str(scripts[1])],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert node_result.returncode == 0, node_result.stderr
    shell_result = subprocess.run(
        ["bash", "-n", str(scripts[2])],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert shell_result.returncode == 0, shell_result.stderr


def test_optimization_browser_checker_uses_real_backend_contracts() -> None:
    source = (ROOT / "scripts" / "check_optimization_journeys_e2e.cjs").read_text()

    assert 'require("../frontend/node_modules/playwright-core")' in source
    assert 'const CHROMIUM_EXECUTABLE = "/usr/bin/chromium"' in source
    assert "page.route(" not in source
    assert "browserContext.route(" not in source
    assert "assertNoResponseMocks()" in source
    assert 'context.route("**/*"' in source
    assert 'route.abort("blockedbyclient")' in source
    assert "context.routeWebSocket(" in source
    assert "route.continue()" in source
    assert ".fulfill(" not in source
    assert "#map-grid" in source
    assert "replaceChildren" in source
    assert "map_first_without_plants" in source
    assert 'record.path === "/api/plants"' in source
    assert "request_start_spread" in source
    assert "layout_restore_semantics" in source
    assert "GARDEN_A_EXTRA_PLOT_ID" in source
    assert "extra assignment" in source
    assert "x-garden-id" in source
    assert "Rapid A/B/A" in source
    assert "garden_scoped_notifications" in source
    assert "garden_scoped_weather" in source
    assert "GARDEN_A_WEATHER_ALERT" in source
    assert "GARDEN_B_WEATHER_ALERT" in source
    assert "mobile_focus_and_scoped_state" in source
    assert "context.setOffline(true)" in source
    assert 'indexedDB.open("gardenops-offline")' in source
    assert "operation_id" in source
    assert "garden_id" in source
    assert "X-Offline-Operation-Id" in source
    assert "offline_media_replay" in source
    assert "offline_task_replay" in source
    assert 'new URL(response.url()).pathname === "/api/media/upload"' in source
    assert "Expected one task replay POST" in source
    assert "#analysis-send-btn" in source
    assert '"/api/ai/garden-chat"' in source
    assert "response.status() === 503" in source
    assert "#adm-garden-delete" in source
    assert "delete_target_session_count" in source
    assert "retainedDeleteState" in source
    assert "operation_counts.media_upload" in source
    assert "operation_counts.task_action" in source
    assert 'page.on("pageerror"' in source
    assert "nonLoopbackRequests" in source
    assert "#mobile-map-layouts-btn" in source
    assert "#mobile-map-tools-btn" in source
    assert "#mobile-garden-select" in source
    assert ".screenshot(" not in source
    assert ".tracing." not in source


def test_optimization_runner_enforces_private_disposable_execution() -> None:
    source = (ROOT / "scripts" / "run_optimization_journeys_e2e.sh").read_text()

    assert "umask 077" in source
    assert "GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1 is required" in source
    assert "scripts/run_fast_postgres_tests.py --command" in source
    assert "GARDENOPS_DISPOSABLE_POSTGRES_URL" in source
    assert "GARDENOPS_DISPOSABLE_POSTGRES_MARKER" in source
    assert "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER" in source
    assert "export APP_ENV=test" in source
    assert "export AUTH_REQUIRED=true" in source
    assert "export AUTH_MODE=session" in source
    assert "export AI_PROVIDER=disabled" in source
    assert "export GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false" in source
    assert "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE=1" in source
    assert "mktemp -d /tmp/gardenops-optimization-journeys.XXXXXX" in source
    assert 'chmod 700 "$PRIVATE_DIR" "$ARTIFACT_DIR" "$LOG_DIR" "$MEDIA_DIR"' in source
    assert 'export MEDIA_STORAGE_DIR="$MEDIA_DIR"' in source
    assert 'chmod 600 "$ARTIFACT_DIR/optimization-journeys-manifest.json"' in source
    assert "setsid" in source
    assert "stop_process_group()" in source
    assert 'kill -TERM -- "-$pid"' in source
    assert 'kill -KILL -- "-$pid"' in source
    assert "--host 127.0.0.1" in source
    assert "port != 5432" in source
    for secret_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "PLANTNET_API_KEY",
        "SHADEMAP_API_KEY",
        "TAILLIGHT_API_KEY",
        "AUTH_API_KEY",
        "AUTH_MFA_SECRET_KEY",
        "SECURITY_TELEMETRY_BEARER_TOKEN",
        "DEPLOYED_READINESS_ADMIN_BEARER_TOKEN",
    ):
        assert secret_name in source


def test_optimization_seed_declares_all_required_fixture_evidence() -> None:
    source = (ROOT / "scripts" / "seed_optimization_journeys_e2e.py").read_text()

    assert "TRUNCATE TABLE" in source
    assert "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE" in source
    assert "GARDENOPS_ALLOW_DESTRUCTIVE_E2E" in source
    assert "pg_control_system()" in source
    assert "GARDEN_A_SLUG" in source
    assert "GARDEN_B_SLUG" in source
    assert "DELETE_TARGET_SLUG" in source
    assert "layout_snapshots" in source
    assert "garden_journal_entries" in source
    assert "garden_tasks" in source
    assert "garden_map_objects" in source
    assert 'json.dumps({"cols": 5, "rows": 4}' in source
    assert "offline_create_operations" in source
    assert "provider_daily_usage" in source
    assert "audit_events" in source
    assert "snapshot" in source


def test_optimization_runner_refuses_direct_execution_without_guards() -> None:
    script = ROOT / "scripts" / "run_optimization_journeys_e2e.sh"
    env = os.environ.copy()
    for name in (
        "GARDENOPS_ALLOW_DESTRUCTIVE_E2E",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
    ):
        env.pop(name, None)
    result = subprocess.run(
        ["bash", str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1 is required" in result.stderr

    env["GARDENOPS_ALLOW_DESTRUCTIVE_E2E"] = "1"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "run_fast_postgres_tests.py --command" in result.stderr
