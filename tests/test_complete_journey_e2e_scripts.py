from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scripts.seed_complete_journeys_e2e import (
    _frozen_attention_clock,
    _require_child_environment,
    _write_json_exclusive,
)
from scripts.seed_optimization_journeys_e2e import _weather_alert_window

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
        ROOT / "scripts" / "e2e" / "journeys" / "gardenMapPlants.cjs",
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


def test_runner_accepts_phase_one_selection_before_parent_validation() -> None:
    result = _run_runner("--child", "1", "1", str(ROOT / "research" / "phase-one-child"))
    assert result.returncode == 2
    assert "not implemented" not in result.stderr.lower()
    assert "run_fast_postgres_tests.py" in result.stderr


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
        ["bash", str(runner), "--phase", "2"],
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
        "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-12",
        "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783857600000",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "999.marker",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": ("postgresql://127.0.0.1:55432/gardenops_test"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="not bound"):
        _require_child_environment()


def test_seeder_requires_a_consistent_frozen_attention_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GARDENOPS_ATTENTION_FROZEN_DATE", raising=False)
    monkeypatch.delenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", raising=False)
    with pytest.raises(RuntimeError, match="requires a frozen attention clock"):
        _frozen_attention_clock()

    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-12")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783857600000")
    assert _frozen_attention_clock() == {
        "attention_date": "2026-07-12",
        "attention_now_ms": 1783857600000,
    }

    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-11")
    with pytest.raises(RuntimeError, match="date and timestamp must agree"):
        _frozen_attention_clock()


def test_optimization_weather_fixture_survives_midnight_boundary() -> None:
    assert _weather_alert_window(today=date(2026, 7, 12)) == (
        "2026-07-10",
        "2026-07-19",
    )


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
        "GARDENOPS_ATTENTION_FROZEN_DATE",
        "GARDENOPS_ATTENTION_FROZEN_NOW_MS",
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


def test_phase_one_fixture_and_journey_wiring_are_declared() -> None:
    seeder_source = SEEDER.read_text(encoding="utf-8")
    journey_source = (ROOT / "scripts" / "e2e" / "journeys" / "gardenMapPlants.cjs").read_text(
        encoding="utf-8"
    )
    checker_source = CHECKER.read_text(encoding="utf-8")
    for marker in (
        "PHASE_ONE_INDOOR_PLOT_ID",
        "PHASE_ONE_INDOOR_PLANT_ID",
        "PHASE_ONE_BETA_INDOOR_PLOT_ID",
        "PHASE_ONE_BETA_INDOOR_ROOM_LABEL",
        "PHASE_ONE_VIEWER_GARDENS",
        "PHASE_ONE_MAP_UNIT_ID",
        "PHASE_ONE_SAVED_VIEW_LABEL",
        "PHASE_ONE_MOBILE_SNAPSHOT_NAME",
        "PHASE_ONE_BROWSER_PLANT_ID",
        "PHASE_ONE_ONBOARDING_HOUSE",
        "_frozen_attention_clock",
        "_garden_graph",
        "_seed_phase_one_fixtures",
        "_seed_viewer_owned_garden_content",
        "_phase_one_fixture_state",
        "_snapshot_payload_projection",
        "_phase_one_stable_domain_projection",
        "_quick_action_records",
        "alpha_snapshot_payload",
        "onboarding_default_context",
        "onboarding_target_gardens",
        "onboarding_target_graphs",
        "cross_garden_links",
        "assignments_with_cross_garden_ownership",
        "lifecycle_audit",
        "restore_import_graphs",
        "stable_domain_projection",
        'subscription_tier="pro"',
    ):
        assert marker in seeder_source
    for marker in (
        "delayGardenSwitchResponses",
        "runOnboardingProfile",
        "assertGlobalSearch",
        "exercisePlantAndSavedView",
        "mutateIndoorPlant",
        "exerciseDiscoverableMobilePlotEdit",
        "createMobileEditorPlot",
        "exerciseMapObjectEditor",
        "exerciseEditorMapObjectWrite",
        "exerciseMobileMapObject",
        "exerciseSnapshotsAndImport",
        "exerciseMobileMapImport",
        "submitMobileQuickAction",
        "assertViewerDenied",
        "viewerFixtureGarden",
        '"viewer-owned plant record"',
        '"viewer-owned read-only plot"',
        "assertEditorAffordances",
        'assignmentPlotId: "P1EDITORASSIGN"',
        "assertMobileFocusReturn",
        "assertRejectedMapImport",
        "observeMapRenderChurn",
        "observeMapReplaceChildren",
        "captureLayoutDomState",
        "exerciseEditorGardenSettingsAndLayoutWrite",
        "issueBrowserRequest",
        "import_rejection_render_churn",
        "successful_map_state_transitions",
        "saveMobileSnapshot",
        "editor_m1_m3_supported_writes",
        "viewer_m1_m3_read_only_behavior",
        "viewer_a3_m4_write_denials",
        "role_cross_garden_response_isolation",
        "role_delayed_surfaces",
    ):
        assert marker in journey_source
    for substantive_marker in (
        "#onb-garden-name",
        ".onb-validation--error",
        "#create-plant-form",
        "#edit-plant-form",
        "#plants-mobile-list",
        ".saved-views-save-btn",
        ".indoor-room-input",
        ".drawer-edit-plot-btn",
        "#edit-plot-form",
        ".map-object-type-select",
        ".map-object-interaction-surface",
        ".map-object-unit",
        ".map-object-unit-form",
        "deleted_units === 1",
        "Input.dispatchTouchEvent",
        "has no non-interactive browser hit-test point",
        ".snapshot-restore",
        "#import-map-input",
        "#mobile-import-map-btn",
        "#mobile-map-tools-btn",
        "structurally-incomplete-map.json",
        "oversized-map.json",
        "cross-garden map import",
        "divergent-successful-map.json",
        "replace_children_calls",
        "[data-quick-action='log-harvest']",
        "admin-settings",
        "admin_settings_draft_isolation",
        "beta_response_arrived",
        "beta_response_completion_count",
        "response.finished()",
        "plot-alerts",
        "plots",
        "beta_held_surfaces",
    ):
        assert substantive_marker in journey_source
    for obsolete_skip in (
        "onboarding-and-garden-lifecycle-mutation-not-yet-wired",
        "map-object-and-snapshot-restore-mutations-require-existing-reauthorization-flow",
        "offline-provider-and-file-import-dimensions-not-applicable-to-phase-one-browser-slice",
    ):
        assert obsolete_skip not in journey_source
    assert journey_source.count("page.evaluate(async") == 1
    assert "const unitUpdate = await issueBrowserRequest" not in journey_source
    assert "route.fulfill" not in journey_source
    assert "assertions.skipped.push" not in journey_source
    assert '{ profile: "desktop", role: "editor"' in journey_source
    assert '{ profile: "mobile", role: "editor"' in journey_source
    assert '{ profile: "mobile", role: "viewer"' in journey_source
    desktop_admin_start = journey_source.index('} else if (profile === "desktop") {')
    desktop_admin_end = journey_source.index("      } else {", desktop_admin_start)
    desktop_admin_branch = journey_source[desktop_admin_start:desktop_admin_end]
    for marker in (
        "exercisePlantAndSavedView(",
        "mutateIndoorPlant(",
        '"admin desktop"',
        "desktop_admin_mutation_workflows = true",
    ):
        assert marker in desktop_admin_branch
    assert "runGardenMapPlants" in checker_source
    assert "THROUGH_PHASE >= 1" in checker_source
    assert "snapshotRestore.replace_children_calls === 1" in checker_source
    assert "beta_response_completion_count" in checker_source
    assert "expected_phase_one_viewer_denial_count === 4" in checker_source
    assert "expected_phase_one_viewer_denial_count <= 1" not in checker_source
    assert "role_cross_garden_response_isolation" in checker_source
    assert "role_delayed_surfaces" in checker_source
    for marker in (
        "assertExactPhaseOneOnboardingOwnership",
        "assertExactPhaseOneOnboardingGraphs",
        "assertExactPhaseOneOnboardingDefaultContext",
        "assertExactPhaseOneQuickActionRecords",
        "assertExactPhaseOneMobileSnapshot",
        "assertExactPhaseOneRestoreImportGraphs",
        "assertPhaseOneStableDomainProjection",
        "assertNoCrossGardenLinks",
        "assertNoLifecycleResidue",
        "assertNoUnexpectedBackendErrors",
        "assertPhaseOneAuditContract",
        "assertPhaseOneProfileEvidence",
        "assertPhaseZeroProfileEvidence",
        "assertSourceRevisionStable",
        "safeUtcTimestamp",
        "sourceProvenance",
        "nested_unit_direct_delete_count",
        "nested_unit_update_count",
    ):
        assert marker in checker_source


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


def test_manifest_sanitizer_drops_unknown_root_fields_and_normalizes_requests() -> None:
    script = """
const { sanitizeManifestEvidence } = require('./scripts/check_complete_journeys_e2e.cjs');
const result = sanitizeManifestEvidence({
  browser: 'chromium', database: null, ended_at: '2026-07-12T00:00:00Z',
  failure: null, filesystem: null, git: { dirty: false, sha: 'abc' },
  journey_ids: ['M1'], phase: 1, profiles: [{
    assertions: {}, browser_profile: {}, checks: {}, diagnostics: {}, profile: 'desktop',
    requests: [{ gardenId: '1', method: 'GET', path: '/api/saved-views' }],
    role: 'admin', trace: 'x',
  }], run_id: 'run', started_at: '2026-07-12T00:00:00Z', status: 'passed',
  suite: 'complete-journeys-e2e', through_phase: 1, injected: 'must-not-survive',
});
if ('injected' in result) process.exit(3);
if (result.profiles[0].requests[0].path !== '/api/saved-views') process.exit(4);
if (result.started_at !== '2026-07-12T00:00:00Z') process.exit(5);
if (result.ended_at !== '2026-07-12T00:00:00Z') process.exit(6);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_manifest_sanitizer_preserves_safe_nonboolean_checks_and_final_provenance() -> None:
    script = """
const {
  sanitizeManifestEvidence,
  sourceProvenance,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const result = sanitizeManifestEvidence({
  backend_log: { backend_error_lines: 0, structured_error_entries: 0, unexpected_error_count: 0 },
  browser: 'chromium', database: null, ended_at: '2026-07-12T00:00:00Z',
  failure: null, filesystem: null,
  git: {
    clean: true, dirty: true, final_head: 'lying-head', sha: 'abc123',
    worktree_fingerprint: 'a'.repeat(64),
  },
  journey_ids: ['M1'], phase: 1, profiles: [{
    assertions: {}, browser_profile: {}, diagnostics: {}, profile: 'desktop',
    checks: {
      delayed_surfaces: ['plants', 'weather'],
      phase_counts: { expected: 7, passed: 7 },
      retry_count: 2,
      boolean_check: true,
    },
    requests: [], role: 'admin', trace: 'x',
  }], run_id: 'run', started_at: '2026-07-12T00:00:00Z', status: 'passed',
  suite: 'complete-journeys-e2e', through_phase: 1,
});
const checks = result.profiles[0].checks;
const expectedDelayedSurfaces = ['plants', 'weather'];
if (
  JSON.stringify(checks.delayed_surfaces) !== JSON.stringify(expectedDelayedSurfaces)
) process.exit(3);
if (checks.phase_counts.expected !== 7 || checks.phase_counts.passed !== 7) process.exit(4);
if (checks.retry_count !== 2 || checks.boolean_check !== true) process.exit(5);
if (
  result.git.final_head !== 'abc123' || result.git.clean !== false || result.git.dirty !== true
) process.exit(6);
if (result.git.worktree_fingerprint !== 'a'.repeat(64)) process.exit(7);
const provenance = sourceProvenance({
  dirty: false,
  sha: 'final-head',
  worktree_fingerprint: 'b'.repeat(64),
});
if (provenance.clean !== true || provenance.final_head !== 'final-head') process.exit(8);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_manifest_sanitizer_preserves_valid_utc_timestamps_without_accepting_invalid_values() -> (
    None
):
    script = """
const {
  safeUtcTimestamp,
  sanitizeManifestEvidence,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const started = '2026-07-12T09:08:07.654Z';
const ended = '2026-07-12T09:08:08Z';
const result = sanitizeManifestEvidence({ started_at: started, ended_at: ended });
if (result.started_at !== started || result.ended_at !== ended) process.exit(3);
if (safeUtcTimestamp('2026-02-30T09:08:07Z') !== (
  '[redacted diagnostic; inspect private runner logs]'
)) {
  process.exit(4);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_checker_rejects_source_changes_even_when_the_worktree_stays_dirty() -> None:
    script = """
const { assertSourceRevisionStable } = require('./scripts/check_complete_journeys_e2e.cjs');
const initial = { sha: 'abc123', dirty: true, worktree_fingerprint: 'a'.repeat(64) };
assertSourceRevisionStable(initial, { ...initial });
try {
  assertSourceRevisionStable(initial, { ...initial, worktree_fingerprint: 'b'.repeat(64) });
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('worktree changed')) process.exit(4);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_phase_one_onboarding_contract_rejects_wrong_owner_or_membership() -> None:
    script = """
const {
  assertExactPhaseOneOnboardingOwnership,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const config = {
  address: 'Phase 1 onboarding address', grid_cols: 12, grid_rows: 12,
  latitude: 59.91, longitude: 10.75,
  layout: {
    col: 2, grid_cols: 12, grid_rows: 12, height: 3,
    north_degrees: 0, row: 2, width: 3,
  },
};
const expected = {
  'Desktop garden': { ...config, owner_username: 'desktop-user' },
  'Mobile garden': { ...config, owner_username: 'mobile-user' },
};
const target = (name, username) => ({
  ...config, name, onboarding_complete: true, owner_username: username,
  memberships: [{ role: 'admin', username }],
});
assertExactPhaseOneOnboardingOwnership([
  target('Desktop garden', 'desktop-user'),
  target('Mobile garden', 'mobile-user'),
], expected);
try {
  assertExactPhaseOneOnboardingOwnership([
    { ...target('Desktop garden', 'desktop-user'), owner_username: 'wrong-user' },
    target('Mobile garden', 'mobile-user'),
  ], expected);
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('owner mismatch')) process.exit(4);
}
try {
  assertExactPhaseOneOnboardingOwnership([
    {
      ...target('Desktop garden', 'desktop-user'),
      memberships: [{ role: 'viewer', username: 'desktop-user' }],
    },
    target('Mobile garden', 'mobile-user'),
  ], expected);
  process.exit(5);
} catch (error) {
  if (!String(error.message).includes('membership mismatch')) process.exit(6);
}
try {
  assertExactPhaseOneOnboardingOwnership([
    { ...target('Desktop garden', 'desktop-user'), grid_cols: 13 },
    target('Mobile garden', 'mobile-user'),
  ], expected);
  process.exit(7);
} catch (error) {
  if (!String(error.message).includes('grid_cols mismatch')) process.exit(8);
}
try {
  assertExactPhaseOneOnboardingOwnership([
    {
      ...target('Desktop garden', 'desktop-user'),
      layout: { ...config.layout, north_degrees: 90 },
    },
    target('Mobile garden', 'mobile-user'),
  ], expected);
  process.exit(9);
} catch (error) {
  if (!String(error.message).includes('layout configuration mismatch')) process.exit(10);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_phase_one_profile_and_audit_contracts_require_complete_evidence() -> None:
    script = """
const {
  assertNoCrossGardenLinks,
  assertPhaseOneAuditContract,
  assertPhaseOneProfileEvidence,
  assertPhaseZeroProfileEvidence,
  phaseOneAuditExpectedEvents,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const delayedEvidence = (surfaces, desktop = false) => ({
  alpha_started_surfaces: surfaces,
  beta_held_response_count: surfaces.length,
  beta_held_surfaces: surfaces,
  per_surface: Object.fromEntries(surfaces.map((surface) => [surface, {
    alpha_selection_mode: 'physical', alpha_target_started: true,
    alpha_trigger_mode: 'automatic', beta_content_never_landed: true,
    beta_response_arrived: true, beta_response_completion_count: 1,
    beta_target_held: true, beta_trigger_mode: 'automatic',
  }])),
  ...(desktop ? {
    admin_settings_draft_isolation: {
      alpha_draft_restored_after_background_load: true,
      baseline_restored_without_persisting: true,
      beta_never_received_alpha_draft: true,
    },
  } : {}),
});
const delayedDesktop = delayedEvidence([
  'admin-settings', 'indoor', 'layout', 'map-objects',
  'notifications', 'plants', 'plot-alerts', 'plots', 'saved-views', 'weather',
], true);
const delayedMobile = delayedEvidence([
  'admin-settings', 'indoor', 'layout', 'map-objects',
  'notifications', 'plants', 'plot-alerts', 'plots', 'saved-views', 'weather',
]);
const delayedPlots = delayedEvidence(['plots']);
const profile = (role, name, checks) => ({
  assertions: { failed: [], skipped: [] },
  browser_profile: { is_mobile: name === 'mobile' },
  checks: { browser_diagnostics: true, ...checks },
  failure: null,
  profile: name,
  role,
});
const profiles = [
  profile('onboarding', 'desktop', { onboarding_validation_recovery_complete: true }),
  profile('onboarding', 'mobile', { onboarding_validation_recovery_complete: true }),
  profile('admin', 'desktop', {
    desktop_admin_mutation_workflows: true,
    import_rejection_render_churn: {
      rejected_import_render_churn: {
        cross_garden: {}, oversized: {}, structurally_incomplete: {}, unsupported_schema: {},
      },
      successful_map_state_transitions: {
        divergent_import: {
          imported_cell: { col: 2, row: 2 },
          original_cell: { col: 1, row: 1 },
          target_plot_id: 'OPT-JOURNEY-A-PLOT',
        },
        snapshot_restore: {
          mutation_count: 1,
          replace_children_calls: 1,
          restored_render_counts: { children: 3, labels: 1, plots: 2 },
          snapshot_render_counts: { children: 3, labels: 1, plots: 2 },
        },
      },
    },
    delayed_surfaces: delayedDesktop,
    map_first_without_plants: true,
    role_cross_garden_response_isolation: true,
  }),
  profile('admin', 'mobile', {
    delayed_surfaces: delayedMobile,
    map_first_without_plants: true,
    mobile_supported_writes_and_focus_return: true,
    role_cross_garden_response_isolation: true,
  }),
  profile('editor', 'desktop', {
    editor_a3_settings_and_m4_layout_write: true,
    editor_m1_m3_supported_writes: true,
    editor_profile_write_affordances_and_admin_denial: true,
    map_first_without_plants: true,
    role_cross_garden_response_isolation: true,
    role_delayed_surfaces: delayedPlots,
  }),
  profile('editor', 'mobile', {
    editor_profile_write_affordances_and_admin_denial: true,
    map_first_without_plants: true,
    mobile_editor_plot_edit_workflow: true,
    role_cross_garden_response_isolation: true,
    role_delayed_surfaces: delayedPlots,
  }),
  profile('viewer', 'desktop', {
    map_first_without_plants: true,
    viewer_a3_m4_write_denials: true,
    viewer_m1_m3_read_only_behavior: true,
    viewer_role_affordances_and_denials: true,
    role_cross_garden_response_isolation: true,
    role_delayed_surfaces: delayedPlots,
  }),
  profile('viewer', 'mobile', {
    map_first_without_plants: true,
    viewer_m1_m3_read_only_behavior: true,
    viewer_role_affordances_and_denials: true,
    role_cross_garden_response_isolation: true,
    role_delayed_surfaces: delayedPlots,
  }),
];
assertPhaseOneProfileEvidence(profiles);
const phaseZeroProfiles = ['desktop', 'mobile'].map((name) => profile('admin', name, {
  auth_session: true,
  garden_a_b_a: true,
  garden_scoped_notifications: true,
  map_first_without_plants: true,
}));
assertPhaseZeroProfileEvidence(phaseZeroProfiles);
assertNoCrossGardenLinks({
  assignments_with_cross_garden_ownership: 0,
  map_unit_parent_garden_mismatch: 0,
}, 'fixture');
try {
  assertNoCrossGardenLinks({ assignments_with_cross_garden_ownership: 1 }, 'fixture');
  process.exit(2);
} catch (error) {
  if (!String(error.message).includes('assignments_with_cross_garden_ownership')) process.exit(3);
}
const audit = { events: [
  ...phaseOneAuditExpectedEvents(8),
  { count: 4, method: 'POST', path: '/api/media/summaries', status_code: 200 },
] };
const expectedViewerDenials = [
  ['POST', '/api/gardens/{garden_id}/map-objects', 403],
  ['PATCH', '/api/gardens/{garden_id}/settings', 403],
  ['POST', '/api/snapshots', 403],
  ['POST', '/api/plots/import', 403],
];
for (const [method, path, statusCode] of expectedViewerDenials) {
  if (!phaseOneAuditExpectedEvents(8).some((event) => (
    event.count === 1
      && event.method === method
      && event.path === path
      && event.status_code === statusCode
  ))) process.exit(15);
}
const evidence = assertPhaseOneAuditContract(audit, 8);
if (evidence.unexpected_count !== 0 || evidence.flexible_read_event_types !== 1) process.exit(4);
const incomplete = structuredClone(profiles);
incomplete[3].checks.mobile_supported_writes_and_focus_return = false;
try {
  assertPhaseOneProfileEvidence(incomplete);
  process.exit(5);
} catch (error) {
  if (!String(error.message).includes('browser check is missing')) process.exit(6);
}
const incompleteRoleIsolation = structuredClone(profiles);
incompleteRoleIsolation[4].checks.role_cross_garden_response_isolation = false;
try {
  assertPhaseOneProfileEvidence(incompleteRoleIsolation);
  process.exit(9);
} catch (error) {
  if (!String(error.message).includes('browser check is missing')) process.exit(10);
}
const incoherentSnapshot = structuredClone(profiles);
incoherentSnapshot[2].checks.import_rejection_render_churn
  .successful_map_state_transitions.snapshot_restore.replace_children_calls = 2;
try {
  assertPhaseOneProfileEvidence(incoherentSnapshot);
  process.exit(11);
} catch (error) {
  if (!/snapshot restore/i.test(String(error.message))) process.exit(12);
}
const incompleteRace = structuredClone(profiles);
incompleteRace[2].checks.delayed_surfaces.per_surface.plots.beta_response_completion_count = 0;
try {
  assertPhaseOneProfileEvidence(incompleteRace);
  process.exit(13);
} catch (error) {
  if (!/delayed A\\/B\\/A/i.test(String(error.message))) process.exit(14);
}
const incompleteRoleRace = structuredClone(profiles);
incompleteRoleRace[6].checks.role_delayed_surfaces
  .per_surface.plots.beta_response_completion_count = 0;
try {
  assertPhaseOneProfileEvidence(incompleteRoleRace);
  process.exit(15);
} catch (error) {
  if (!/delayed A\\/B\\/A/i.test(String(error.message))) process.exit(16);
}
audit.events.push({ count: 1, method: 'POST', path: '/api/unexpected', status_code: 200 });
try {
  assertPhaseOneAuditContract(audit, 8);
  process.exit(17);
} catch (error) {
  if (!String(error.message).includes('Unexpected Phase 1 audit event')) process.exit(18);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_phase_one_snapshot_and_restore_import_graph_contracts_are_exact() -> None:
    script = """
const {
  assertExactPhaseOneMobileSnapshot,
  assertExactPhaseOneRestoreImportGraphs,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const payload = {
  house: { col: 2, grid_cols: 12, grid_rows: 12, height: 3, north_degrees: 0, row: 2, width: 3 },
  map_objects: [{
    geometry: { height: 1, width: 2, x: 1, y: 1 }, has_internal_layout: true,
    internal_layout: { cols: 2, rows: 1 }, name: 'Alpha greenhouse', object_type: 'greenhouse',
    public_id: 'mapobj_alpha', shape_type: 'rectangle', style: { color: '#7d9f7a' },
    units: [{
      geometry: { height: 1, width: 1, x: 1, y: 1 }, name: 'Bench', public_id: 'mapunit_alpha',
      shape_type: 'rectangle', sort_order: 1, style: { color: '#b7c98a' }, unit_type: 'planter',
    }], z_index: 2,
  }],
  plots: [{
    color: '#7d9f7a', grid_col: 1, grid_row: 1, notes: '', plot_id: 'OPT-JOURNEY-A-PLOT',
    plot_number: 1, sub_zone: '', zone_code: 'A', zone_name: 'Alpha',
  }],
  schema_version: 1,
  shademap: { analysis_timestamp_ms: 0, mode: 'off', preset: 'default', selected_plot_id: null },
  shademap_calibration: null,
  shademap_obstacles: [],
};
const snapshot = {
  garden_id: 1, garden_owner_username: 'admin',
  name: 'Mobile action snapshot', public_id: 'snap_alpha',
  payload,
};
assertExactPhaseOneMobileSnapshot([snapshot], {
  garden_id: 1, garden_owner_username: 'admin', name: 'Mobile action snapshot', payload,
});
const graphs = {
  alpha: {
    assignments: [{ plant_id: 'PLT-A', plot_id: 'OPT-JOURNEY-A-PLOT', quantity: 1 }],
    garden: { id: 1, slug: 'alpha' }, layout: payload.house,
    map_objects: payload.map_objects, plants: [{ plant_id: 'PLT-A' }], plots: payload.plots,
  },
  beta: {
    assignments: [], garden: { id: 2, slug: 'beta' }, layout: payload.house,
    map_objects: [], plants: [], plots: [],
  },
};
assertExactPhaseOneRestoreImportGraphs(graphs, structuredClone(graphs));
try {
  const changed = structuredClone(graphs);
  changed.alpha.assignments[0].quantity = 2;
  assertExactPhaseOneRestoreImportGraphs(graphs, changed);
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('assignment graph')) process.exit(4);
}
try {
  const changedSnapshot = structuredClone(snapshot);
  changedSnapshot.payload.plots[0].grid_col = 3;
  assertExactPhaseOneMobileSnapshot([changedSnapshot], {
    garden_id: 1, garden_owner_username: 'admin', name: 'Mobile action snapshot', payload,
  });
  process.exit(5);
} catch (error) {
  if (!String(error.message).includes('payload did not match')) process.exit(6);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_phase_one_persistent_delta_contracts_reject_same_table_mutations() -> None:
    script = """
const {
  assertExactPhaseOneOnboardingDefaultContext,
  assertExactPhaseOneOnboardingGraphs,
  assertExactPhaseOneQuickActionRecords,
  assertPhaseOneStableDomainProjection,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = {
  clock: { attention_date: '2026-07-12' },
  gardens: { alpha: { id: 7, plot_id: 'OPT-JOURNEY-A-PLOT' } },
  phase_one: { indoor: { plant_id: 'COMPLETE-PHASE-ONE-BASIL' } },
  roles: {
    admin: 'admin', onboarding: 'onboarding-user', onboarding_mobile: 'mobile-user',
  },
};
const house = {
  col: 2, grid_cols: 12, grid_rows: 12, height: 3,
  north_degrees: 0, row: 2, width: 3,
};
const graph = (name, slug, owner, id) => ({
  assignments: [],
  garden: {
    address: 'Phase 1 onboarding address', grid_cols: 12, grid_rows: 12, id,
    latitude: 59.91, longitude: 10.75, name, onboarding_complete: true,
    owner_username: owner, slug,
  },
  layout: house,
  map_objects: [],
  plants: [],
  plots: [{
    color: '', garden_id: id, grid_col: null, grid_row: null, notes: '',
    owner_username: owner, plot_id: `INDOOR-${id}`, plot_number: 0, sub_zone: '',
    zone_code: 'I', zone_name: 'Innendors',
  }],
});
const expectedGraphs = {
  'Desktop garden': {
    address: 'Phase 1 onboarding address', grid_cols: 12, grid_rows: 12,
    latitude: 59.91, layout: house, longitude: 10.75, onboarding_complete: true,
    owner_username: 'onboarding-user', slug: 'desktop-garden',
  },
  'Mobile garden': {
    address: 'Phase 1 onboarding address', grid_cols: 12, grid_rows: 12,
    latitude: 59.91, layout: house, longitude: 10.75, onboarding_complete: true,
    owner_username: 'mobile-user', slug: 'mobile-garden',
  },
};
const graphs = {
  'Desktop garden': graph('Desktop garden', 'desktop-garden', 'onboarding-user', 11),
  'Mobile garden': graph('Mobile garden', 'mobile-garden', 'mobile-user', 12),
};
assertExactPhaseOneOnboardingGraphs(graphs, expectedGraphs);
const defaultContext = {
  gardens: [{
    address: '', grid_cols: 22, grid_rows: 30, id: 10, latitude: null, layout_count: 0,
    longitude: null, map_object_count: 0, name: 'Default Garden', onboarding_complete: false,
    owner_username: null, plot_count: 0, slug: 'default',
  }],
  memberships: [
    { garden_id: 10, role: 'editor', username: 'mobile-user' },
    { garden_id: 10, role: 'editor', username: 'onboarding-user' },
  ],
};
assertExactPhaseOneOnboardingDefaultContext(defaultContext, fixture);
const quickAction = {
  harvest_rollups: [{
    key: 'harvest_rollup:7:2026',
    value: { by_unit: [{ entries: 1, total_qty: 1, unit: 'kg' }], garden_id: 7, year: 2026 },
  }],
  harvests: [{
    actor_username: 'admin', garden_id: 7, metadata: { journal_entry_id: 'jrn_action' },
    notes: 'Phase 1 mobile quick action', occurred_on: '2026-07-12',
    plant_ids: ['COMPLETE-PHASE-ONE-BASIL'], plot_ids: ['OPT-JOURNEY-A-PLOT'],
    public_id: 'hrv_action', quality: 'good', quantity: 1, unit: 'kg',
  }],
  journals: [{
    actor_username: 'admin', event_type: 'harvested', garden_id: 7,
    metadata: {
      linked_harvest_entry_id: 'hrv_action', quantity: 1, source: 'auto:harvest', unit: 'kg',
    },
    notes: 'Phase 1 mobile quick action', occurred_on: '2026-07-12',
    plant_ids: ['COMPLETE-PHASE-ONE-BASIL'], plot_ids: ['OPT-JOURNEY-A-PLOT'],
    public_id: 'jrn_action', title: 'Harvested 1 kg from COMPLETE-PHASE-ONE-BASIL',
  }],
};
assertExactPhaseOneQuickActionRecords(quickAction, fixture);
const baseline = { app_settings: [], gardens: [{ id: 99, name: 'Unaffected' }] };
assertPhaseOneStableDomainProjection(baseline, structuredClone(baseline));
try {
  const changed = structuredClone(baseline);
  changed.gardens[0].name = 'Mutated in same table';
  assertPhaseOneStableDomainProjection(baseline, changed);
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('non-retained semantic row')) process.exit(4);
}
try {
  const changed = structuredClone(quickAction);
  changed.harvest_rollups[0].value.by_unit[0].total_qty = 2;
  assertExactPhaseOneQuickActionRecords(changed, fixture);
  process.exit(5);
} catch (error) {
  if (!String(error.message).includes('rollup key or value')) process.exit(6);
}
try {
  const changed = structuredClone(graphs);
  changed['Desktop garden'].plots[0].owner_username = 'wrong-owner';
  assertExactPhaseOneOnboardingGraphs(changed, expectedGraphs);
  process.exit(7);
} catch (error) {
  if (!String(error.message).includes('generated plot and ownership graph')) process.exit(8);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_backend_error_evidence_surfaces_errors_without_log_contents(tmp_path: Path) -> None:
    (tmp_path / "backend.log").write_text("INFO: backend ready\n", encoding="utf-8")
    (tmp_path / "errors.jsonl").write_text('{"level":"WARNING"}\n', encoding="utf-8")
    script = f"""
const fs = require('node:fs');
const {{
  assertNoUnexpectedBackendErrors,
  backendErrorEvidence,
}} = require('./scripts/check_complete_journeys_e2e.cjs');
const directory = {json.dumps(str(tmp_path))};
assertNoUnexpectedBackendErrors(directory);
fs.appendFileSync(`${{directory}}/backend.log`, 'ERROR: synthetic backend failure\\n');
fs.appendFileSync(`${{directory}}/errors.jsonl`, '{{"level":"ERROR"}}\\n');
const evidence = backendErrorEvidence(directory);
if (evidence.backend_error_lines !== 1 || evidence.structured_error_entries !== 1) process.exit(3);
try {{
  assertNoUnexpectedBackendErrors(directory);
  process.exit(4);
}} catch (error) {{
  if (String(error.message) !== (
    'Unexpected backend ERROR log entries; inspect private runner logs'
  )) process.exit(5);
}}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
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
    written = json.loads(manifest.read_text(encoding="utf-8"))
    assert written["status"] == payload["status"]
    assert written["run_id"] == payload["run_id"]
    assert written["profiles"] == payload["profiles"]
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
