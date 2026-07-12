from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.seed_complete_journeys_e2e import (
    _require_child_environment,
    _write_json_exclusive,
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_complete_journeys_e2e.sh"
SEEDER = ROOT / "scripts" / "seed_complete_journeys_e2e.py"
CHECKER = ROOT / "scripts" / "check_complete_journeys_e2e.cjs"


def _run_runner(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER), *args],
        cwd=ROOT,
        env=env or os.environ.copy(),
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def test_phase_zero_complete_journey_files_exist() -> None:
    expected = (
        RUNNER,
        SEEDER,
        CHECKER,
        ROOT / "scripts" / "e2e" / "completeJourneyBrowser.cjs",
        ROOT / "scripts" / "e2e" / "completeJourneyAssertions.cjs",
        ROOT / "scripts" / "e2e" / "completeJourneyApi.cjs",
        ROOT / "scripts" / "e2e" / "journeys" / "foundation.cjs",
    )
    assert all(path.is_file() for path in expected)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--phase", "10"), "phase"),
        (("--phase", "0", "--phase", "1"), "duplicate"),
        (("--through-phase", "-1"), "phase"),
        (("--unknown",), "usage"),
        (("--child",), "usage"),
    ],
)
def test_runner_rejects_invalid_arguments_before_starting_children(
    args: tuple[str, ...], message: str
) -> None:
    result = _run_runner(*args)
    assert result.returncode == 2
    assert message.lower() in result.stderr.lower()
    assert "uvicorn" not in result.stdout


def test_runner_child_rejects_unverified_parent_before_environment() -> None:
    env = os.environ.copy()
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
    ):
        env.pop(name, None)
    result = _run_runner("--child", "0", "0", str(ROOT / "research" / "bad-child"), env=env)
    assert result.returncode == 2
    assert "run_fast_postgres_tests.py" in result.stderr


def test_seeder_rejects_missing_disposable_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "APP_ENV",
        "AUTH_MODE",
        "AUTH_REQUIRED",
        "DATABASE_URL",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="disposable runner child"):
        _require_child_environment()


def test_runner_rejects_unimplemented_phase_before_starting_children() -> None:
    result = _run_runner("--phase", "1")
    assert result.returncode == 2
    assert "not implemented" in result.stderr.lower()


def test_runner_rejects_preexisting_artifact_directory() -> None:
    artifact = ROOT / "research" / "optimization-map" / "runs" / "preexisting-phase-zero-test"
    artifact.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR"] = str(artifact)
    try:
        result = _run_runner("--phase", "0", env=env)
        assert result.returncode == 2
        assert "newly created" in result.stderr.lower()
    finally:
        artifact.rmdir()


def test_runner_creates_missing_ignored_research_root_in_fresh_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy2(RUNNER, checkout / "scripts" / RUNNER.name)
    shutil.copy2(ROOT / ".gitignore", checkout / ".gitignore")
    subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True, timeout=20)
    runner = checkout / "scripts" / "run_complete_journeys_e2e.sh"
    result = subprocess.run(
        ["bash", str(runner), "--phase", "1"],
        cwd=checkout,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "not implemented" in result.stderr.lower()
    assert (checkout / "research").is_dir()


@pytest.mark.parametrize(
    ("ports", "expected_returncode"),
    [
        (("41000", "41001", "41002"), 0),
        (("5432", "41001", "41002"), 2),
        (("41000", "41000", "41002"), 2),
    ],
)
def test_runner_port_validation_is_behavioral(
    ports: tuple[str, str, str], expected_returncode: int
) -> None:
    result = _run_runner("--self-test-ports", *ports)
    assert result.returncode == expected_returncode


def test_runner_environment_scrub_is_behavioral() -> None:
    env = os.environ.copy()
    env.update(
        {
            "ANTHROPIC_API_KEY": "disposable-canary",
            "DATABASE_URL": "disposable-canary",
            "OPENAI_API_KEY": "disposable-canary",
        }
    )
    result = _run_runner("--self-test-scrub", env=env)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("variable", ["BASH_ENV", "PYTHONPATH", "NODE_OPTIONS"])
def test_runner_entrypoint_strips_interpreter_startup_overrides(variable: str) -> None:
    env = os.environ.copy()
    env[variable] = "/tmp/disallowed-startup-override"
    result = _run_runner("--self-test-ports", "41000", "41001", "41002", env=env)
    assert result.returncode == 0, result.stderr


def test_runner_process_group_teardown_is_behavioral() -> None:
    result = _run_runner("--self-test-process-group")
    assert result.returncode == 0, result.stderr


def test_runner_success_cleanup_and_failure_retention_are_behavioral() -> None:
    result = _run_runner("--self-test-cleanup")
    assert result.returncode == 0, result.stderr
    assert "retained" in result.stderr.lower()


def test_seeder_exclusive_output_rejects_symlink_and_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR", str(tmp_path))
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    output = tmp_path / "fixture.json"
    output.symlink_to(victim)
    with pytest.raises(FileExistsError):
        _write_json_exclusive(output, {"safe": True})
    assert victim.read_text(encoding="utf-8") == "unchanged"
    output.unlink()
    os.link(victim, output)
    with pytest.raises(FileExistsError):
        _write_json_exclusive(output, {"safe": True})
    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_seeder_rejects_marker_not_bound_to_system_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "APP_ENV": "test",
        "AUTH_MODE": "session",
        "AUTH_REQUIRED": "true",
        "DATABASE_URL": "postgresql://127.0.0.1:55432/gardenops_test",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": "1",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "999.marker",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": ("postgresql://127.0.0.1:55432/gardenops_test"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="not bound"):
        _require_child_environment()


def test_runner_source_contains_required_safety_boundaries() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for required in (
        "run_fast_postgres_tests.py",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        "git check-ignore",
        "realpath",
        "mktemp -d",
        "umask 077",
        "setsid",
        "kill -TERM",
        "kill -KILL",
        "127.0.0.1",
        "5432",
        "seed_complete_journeys_e2e.py",
        "check_complete_journeys_e2e.cjs",
    ):
        assert required in source
    for secret_family in (
        "OPENAI_",
        "ANTHROPIC_",
        "PLANTNET_",
        "SHADEMAP_",
        "AWS_",
        "AZURE_",
        "GCP_",
        "DATABASE_URL",
        "BASH_ENV",
        "NODE_OPTIONS",
        "PYTHONPATH",
    ):
        assert secret_family in source
    seeder_source = SEEDER.read_text(encoding="utf-8")
    assert "pg_control_system()" in (
        ROOT / "scripts" / "seed_optimization_journeys_e2e.py"
    ).read_text(encoding="utf-8")
    assert "verify_optimization_journeys_e2e_database_marker" in seeder_source


def test_seeder_refuses_direct_execution() -> None:
    env = os.environ.copy()
    for name in (
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
    ):
        env.pop(name, None)
    result = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), str(SEEDER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode != 0
    assert "complete journey" in (result.stderr + result.stdout).lower()


def test_browser_harness_has_no_response_mocking() -> None:
    script = "require('./scripts/check_complete_journeys_e2e.cjs').assertNoResponseMocks()"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_read_only_browser_api_helper_rejects_mutation_and_body() -> None:
    script = """
const { browserJson } = require('./scripts/e2e/completeJourneyApi.cjs');
const page = { evaluate: () => { throw new Error('evaluate must not run'); } };
Promise.allSettled([
  browserJson(page, '/api/plants/1', { method: 'DELETE' }),
  browserJson(page, '/api/plants', { body: { name: 'unsafe' } }),
]).then((results) => {
  if (!results.every((result) => result.status === 'rejected')) process.exit(3);
});
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_diagnostics_redact_bearer_tokens_and_secret_parameters() -> None:
    script = """
const { sanitizeDiagnostic } = require('./scripts/e2e/completeJourneyBrowser.cjs');
const value = sanitizeDiagnostic(
  [
    'Authorization: Bearer canary-value',
    'https://x/?api_key=key-value&refresh_token=refresh-value',
    'OPENAI_API_KEY=provider-value AUTH_PASSWORD:password-value',
    'AWS_SECRET_ACCESS_KEY=cloud-value CLIENT_SECRET=client-value',
  ].join(' '),
);
const leaked = [
  'canary-value', 'key-value', 'refresh-value', 'provider-value',
  'password-value', 'cloud-value', 'client-value',
].some((item) => value.includes(item));
if (leaked) process.exit(3);
if (value !== '[redacted diagnostic; inspect private runner logs]') process.exit(4);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_top_level_failure_serializer_redacts_prefixed_secrets_behaviorally() -> None:
    script = """
const { safeFailure } = require('./scripts/check_complete_journeys_e2e.cjs');
const output = safeFailure(new Error(
  'COHERE_API_KEY=provider-value refresh_token=refresh-value CLIENT_SECRET:client-value',
));
if (['provider-value', 'refresh-value', 'client-value'].some((item) => output.includes(item))) {
  process.exit(3);
}
if (output !== '[redacted diagnostic; inspect private runner logs]') process.exit(4);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_checker_cli_never_prints_untrusted_failure_text(tmp_path: Path) -> None:
    secret = '"cookie":"session=quoted secret value" postgresql+psycopg://user:pass@host/db'
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "AUTH_MODE": "session",
            "AUTH_REQUIRED": "true",
            "BASE_URL": f"http://{secret}@127.0.0.1:1",
            "DATABASE_URL": "postgresql://127.0.0.1:1/test",
            "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": str(tmp_path),
            "GARDENOPS_COMPLETE_JOURNEYS_E2E_FIXTURE_PATH": str(tmp_path / "missing.json"),
            "GARDENOPS_COMPLETE_JOURNEYS_E2E_PHASE": "0",
            "GARDENOPS_COMPLETE_JOURNEYS_E2E_THROUGH_PHASE": "0",
            "GARDENOPS_DISPOSABLE_POSTGRES_URL": "postgresql://127.0.0.1:1/test",
        }
    )
    result = subprocess.run(
        ["node", str(CHECKER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode != 0
    assert secret not in result.stderr
    assert "quoted secret value" not in result.stderr
    assert "[redacted diagnostic" in result.stderr


def test_trace_writer_rejects_preexisting_symlink(tmp_path: Path) -> None:
    script = """
const fs = require('node:fs');
const { createGuardedContext } = require('./scripts/e2e/completeJourneyBrowser.cjs');
const artifact = process.argv[1];
const victim = process.argv[2];
fs.writeFileSync(victim, 'unchanged');
fs.symlinkSync(victim, `${artifact}/desktop-passed.zip`);
const context = {
  route: async () => {}, on: () => {},
  tracing: { start: async () => {}, stop: async ({ path }) => fs.writeFileSync(path, 'trace') },
  close: async () => {},
};
const browser = { newContext: async () => context };
createGuardedContext(browser, {}, 'desktop', artifact)
  .then((guarded) => guarded.close('passed'))
  .then(() => process.exit(3))
  .catch(() => {
    if (fs.readFileSync(victim, 'utf8') !== 'unchanged') process.exit(4);
  });
"""
    victim = tmp_path / "victim"
    result = subprocess.run(
        ["node", "-e", script, str(tmp_path), str(victim)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_foundation_failure_is_reported_before_error_propagates() -> None:
    script = """
const { runFoundation } = require('./scripts/e2e/journeys/foundation.cjs');
const recorded = [];
const failure = new Error('synthetic profile failure');
const runner = async ({ profile }) => ({
  error: failure,
  result: {
    assertions: { failed: ['profile journey failed'], passed: [], skipped: [] },
    diagnostics: { pageErrors: ['synthetic'] },
    failure: 'profile journey failed; see top-level sanitized failure',
    profile,
    trace: `${profile}-failed.zip`,
  },
});
runFoundation({ onProfile: (profile) => recorded.push(profile) }, runner)
  .then(() => process.exit(3))
  .catch((error) => {
    if (error !== failure || recorded.length !== 1 || recorded[0].profile !== 'desktop') {
      process.exit(4);
    }
  });
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_manifest_writer_is_atomic_and_private(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR"] = str(tmp_path)
    payload = {"status": "test", "run_id": "phase-zero-test", "profiles": []}
    script = (
        "const m=require('./scripts/check_complete_journeys_e2e.cjs');"
        f"m.writeManifestAtomic({json.dumps(payload)});"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    manifest = tmp_path / "complete-journeys-manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == payload
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp-*"))


def test_manifest_writer_sanitizes_all_browser_derived_structures(tmp_path: Path) -> None:
    secret = '"cookie":"session=quoted secret value" postgresql+psycopg://u:p@host/db'
    payload = {
        "profiles": [
            {
                "diagnostics": {"blockedRequests": [secret]},
                "requests": [{"gardenId": secret, "method": secret, "path": f"/api/{secret}"}],
                "structure": {"duplicateIds": [secret], "unnamedControls": [secret]},
            }
        ],
        "filesystem": {
            "artifacts": [secret],
            "downloads": {"entries": [secret]},
            "media": {"entries": [secret]},
            "terrain": {"entries": [secret]},
        },
    }
    env = os.environ.copy()
    env["GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR"] = str(tmp_path)
    script = (
        "const m=require('./scripts/check_complete_journeys_e2e.cjs');"
        f"m.writeManifestAtomic({json.dumps(payload)});"
    )
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    serialized = (tmp_path / "complete-journeys-manifest.json").read_text(encoding="utf-8")
    assert secret not in serialized
    assert "quoted secret value" not in serialized
    assert serialized.count("[redacted diagnostic") >= 8
