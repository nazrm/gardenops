from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import seed_deterministic_provider_e2e

ROOT = Path(__file__).resolve().parents[1]
SEED_SCRIPT = ROOT / "scripts" / "seed_deterministic_provider_e2e.py"
CHECK_SCRIPT = ROOT / "scripts" / "check_deterministic_provider_e2e.cjs"
RUNNER_SCRIPT = ROOT / "scripts" / "run_deterministic_provider_e2e.sh"
DISPOSABLE_URL = "postgresql://gardenops@127.0.0.1:55432/gardenops_test"
DISPOSABLE_SYSTEM_IDENTIFIER = "987654321"
DISPOSABLE_MARKER = f"{DISPOSABLE_SYSTEM_IDENTIFIER}.runner-issued-nonce"


def _set_safe_seed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_MODE", "session")
    monkeypatch.setenv("AI_PROVIDER", "disabled")
    monkeypatch.setenv("GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER", "1")
    monkeypatch.setenv("GARDENOPS_DETERMINISTIC_PROVIDER_E2E_ALLOW_TRUNCATE", "1")
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", DISPOSABLE_URL)
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", DISPOSABLE_MARKER)
    monkeypatch.setenv(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        DISPOSABLE_SYSTEM_IDENTIFIER,
    )


class _Cursor:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object]:
        return self._row


class _MarkerConnection:
    def __init__(self, *, marker: str, system_identifier: str) -> None:
        self.marker = marker
        self.system_identifier = system_identifier

    def execute(self, query: str, _params: object = None) -> _Cursor:
        if "current_setting" in query:
            return _Cursor({"disposable_marker": self.marker})
        if "pg_control_system" in query:
            return _Cursor({"system_identifier": self.system_identifier})
        raise AssertionError(f"Unexpected query: {query}")


def test_seed_guard_requires_exact_disposable_url_marker_and_system_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)

    seed_deterministic_provider_e2e.require_deterministic_provider_e2e_database(
        DISPOSABLE_URL
    )
    seed_deterministic_provider_e2e.verify_deterministic_provider_e2e_database_marker(
        _MarkerConnection(
            marker=DISPOSABLE_MARKER,
            system_identifier=DISPOSABLE_SYSTEM_IDENTIFIER,
        )
    )

    with pytest.raises(RuntimeError, match="exactly match"):
        seed_deterministic_provider_e2e.require_deterministic_provider_e2e_database(
            "postgresql://gardenops@127.0.0.1:55433/gardenops_test"
        )

    monkeypatch.setenv(
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "postgresql://gardenops@127.0.0.1:5432/gardenops_test",
    )
    with pytest.raises(RuntimeError, match="non-5432"):
        seed_deterministic_provider_e2e.require_deterministic_provider_e2e_database(
            "postgresql://gardenops@127.0.0.1:5432/gardenops_test"
        )

    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", DISPOSABLE_URL)
    with pytest.raises(RuntimeError, match="system identifier"):
        seed_deterministic_provider_e2e.verify_deterministic_provider_e2e_database_marker(
            _MarkerConnection(marker=DISPOSABLE_MARKER, system_identifier="123")
        )


def test_seed_guard_rejects_missing_truncate_flag_and_nonloopback_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)
    monkeypatch.delenv("GARDENOPS_DETERMINISTIC_PROVIDER_E2E_ALLOW_TRUNCATE")
    with pytest.raises(RuntimeError, match="ALLOW_TRUNCATE"):
        seed_deterministic_provider_e2e.require_deterministic_provider_e2e_database(
            DISPOSABLE_URL
        )

    _set_safe_seed_environment(monkeypatch)
    remote_url = "postgresql://gardenops@db.example.test:55432/gardenops_test"
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", remote_url)
    with pytest.raises(RuntimeError, match="TCP loopback"):
        seed_deterministic_provider_e2e.require_deterministic_provider_e2e_database(remote_url)


def test_deterministic_provider_e2e_scripts_have_valid_syntax() -> None:
    compile(SEED_SCRIPT.read_text(encoding="utf-8"), str(SEED_SCRIPT), "exec")
    node = subprocess.run(
        ["node", "--check", str(CHECK_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    shell = subprocess.run(
        ["bash", "-n", str(RUNNER_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert node.returncode == 0, node.stderr
    assert shell.returncode == 0, shell.stderr


def test_browser_check_uses_real_loopback_backend_without_route_mocks() -> None:
    source = CHECK_SCRIPT.read_text(encoding="utf-8")
    forbidden = [
        ".".join(("page", "route(")),
        ".".join(("browserContext", "route(")),
    ]

    assert 'require("../frontend/node_modules/playwright-core")' in source
    assert 'const CHROMIUM_EXECUTABLE = "/usr/bin/chromium"' in source
    assert all(needle not in source for needle in forbidden)
    assert 'context.route("**/*"' in source
    assert 'route.abort("blockedbyclient")' in source
    assert "context.routeWebSocket(" in source
    assert "route.continue()" in source
    assert ".fulfill(" not in source
    assert 'page.on("request"' in source
    assert "nonLoopbackRequests" in source
    assert "isLoopbackNetworkUrl" in source
    assert "/api/ai/garden-chat" in source
    assert "response.status() === 200" in source
    assert "EXPECTED_REPLY" in source
    assert "input.isEnabled()" in source
    assert "page.setViewportSize({ height: 844, width: 390 })" in source
    assert "assertProviderUsage(after, 2)" in source


def test_runner_scrubs_provider_environment_and_keeps_output_private() -> None:
    source = RUNNER_SCRIPT.read_text(encoding="utf-8")
    unset_block = source.split("unset \\\n", maxsplit=1)[1].split("\n\nexport", maxsplit=1)[0]

    assert "scripts/run_fast_postgres_tests.py --command" in source
    assert "umask 077" in source
    assert "mktemp -d /tmp/gardenops-deterministic-provider-e2e." in source
    assert 'chmod 700 "$OUTPUT_DIR" "$LOG_DIR"' in source
    assert "export APP_ENV=test" in source
    assert "export AUTH_MODE=session" in source
    assert "export GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false" in source
    assert "export GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER=1" in source
    assert "export AI_PROVIDER=disabled" in source
    assert "assert_backend_log_has_no_vendor_material" in source
    assert "command -v grep" in source
    assert "grep -E -n -i" in source
    assert "rg -n -i" not in source
    assert "provider_daily_usage" in SEED_SCRIPT.read_text(encoding="utf-8")
    assert "dataSnapshot" in CHECK_SCRIPT.read_text(encoding="utf-8")
    assert "screenshot" not in source.lower()
    assert "tracing" not in source.lower()
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "OPENAI_HTTP_PROXY",
        "ANTHROPIC_HTTPS_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "AI_PROVIDER_KEY",
        "AI_PROVIDER_BASE_URL",
        "AI_PROVIDER_URL",
        "PLANTNET_BASE_URL",
    ):
        assert name in unset_block


def test_runner_refuses_direct_execution_without_disposable_command_context() -> None:
    env = os.environ.copy()
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
    ):
        env.pop(name, None)
    result = subprocess.run(
        ["bash", str(RUNNER_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert "run_fast_postgres_tests.py --command" in result.stderr
