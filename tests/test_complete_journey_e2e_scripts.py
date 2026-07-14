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
        ROOT / "scripts" / "e2e" / "journeys" / "dailyAttentionWork.cjs",
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


def test_runner_accepts_phase_two_selection_before_parent_validation() -> None:
    result = _run_runner("--child", "2", "2", str(ROOT / "research" / "phase-two-child"))
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
        ["bash", str(runner), "--phase", "3"],
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
    app_source = (ROOT / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")
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
        "assertMalformedMapImportRejectedClientSide",
        "observeMapRenderChurn",
        "observeMapReplaceChildren",
        "captureLayoutDomState",
        "exerciseEditorGardenSettingsAndLayoutWrite",
        "import_rejection_render_churn",
        "successful_map_state_transitions",
        "saveMobileSnapshot",
        "editor_m1_m3_supported_writes",
        "viewer_m1_m3_read_only_behavior",
        "viewer_a3_m4_write_unavailable",
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
        "malformed-map.json",
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
    assert journey_source.count("page.evaluate(async") == 0
    assert "const unitUpdate = await issueBrowserRequest" not in journey_source
    assert "route.fulfill" not in journey_source
    delay_start = journey_source.index("async function delayGardenSwitchResponses")
    delay_end = journey_source.index("async function assertGlobalSearch", delay_start)
    delay_source = journey_source[delay_start:delay_end]
    assert "await route.fallback()" in delay_source
    assert "await route.continue()" not in delay_source
    assert "assertDelayedRouteFallsThroughToNetworkGuard" in delay_source
    assert "ROUTE_GUARD_PROBE_URL" in delay_source
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
    assert "fitPersistedHouseSizeToGrid" in app_source
    assert "state.houseSize = fitPersistedHouseSizeToGrid(house);" in app_source
    assert "phaseSelected(1)" in checker_source
    assert "snapshotRestore.replace_children_calls === 1" in checker_source
    assert "beta_response_completion_count" in checker_source
    assert "expected_phase_one_viewer_denial_count" not in checker_source
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
        "saved_view_delete_confirmation",
        "indoor_reload_persistence",
        "garden_settings_reload_persistence",
        "editor_settings_layout_reload_persistence",
        "malformed_json",
        "runtime touch evidence",
    ):
        assert marker in checker_source


def test_mobile_surface_cleanup_does_not_clear_focused_map_object_selection() -> None:
    journey_source = (ROOT / "scripts" / "e2e" / "journeys" / "gardenMapPlants.cjs").read_text(
        encoding="utf-8"
    )
    helper_start = journey_source.index("async function closeMobileSurfaces(page)")
    helper_end = journey_source.index("async function openMobileUtility", helper_start)
    helper_source = journey_source[helper_start:helper_end]
    map_sheet_start = helper_source.index("body.mobile-map-sheet-open")
    map_sheet_source = helper_source[map_sheet_start:]

    assert 'page.keyboard.press("Escape")' not in map_sheet_source
    assert '".mobile-map-sheet--open [data-mobile-map-sheet-initial-focus]"' in map_sheet_source
    assert "#mobile-map-layers-close-btn:visible" not in map_sheet_source
    assert "await closeButton.click()" in map_sheet_source
    assert 'page.locator("#mobile-map-sheet-backdrop").click' in map_sheet_source


def test_phase_two_fixture_and_journey_wiring_are_declared() -> None:
    journey_path = ROOT / "scripts" / "e2e" / "journeys" / "dailyAttentionWork.cjs"
    journey_source = journey_path.read_text(encoding="utf-8")
    checker_source = CHECKER.read_text(encoding="utf-8")
    runner_source = RUNNER.read_text(encoding="utf-8")

    assert "MAX_IMPLEMENTED_PHASE=2" in runner_source
    assert "runDailyAttentionWork" in journey_source
    assert 'require("./e2e/journeys/dailyAttentionWork.cjs")' in checker_source
    assert "phaseSelected(2)" in checker_source
    assert "preparePhaseTwoFixtures" in checker_source
    assert '"--prepare-phase-two"' in checker_source
    for journey_id in ("D1", "D2", "D3", "D4", "D5", "R1"):
        assert f'"{journey_id}"' in checker_source


def test_phase_two_adversarial_attention_evidence_contract_is_declared() -> None:
    journey_source = (ROOT / "scripts" / "e2e" / "journeys" / "dailyAttentionWork.cjs").read_text(
        encoding="utf-8"
    )
    checker_source = CHECKER.read_text(encoding="utf-8")
    seed_source = (ROOT / "scripts" / "seed_complete_journeys_e2e.py").read_text(encoding="utf-8")

    for marker in (
        "exerciseCalendarSubscriptionFeed",
        "page.waitForResponse",
        "feed_path",
        "page-origin feed fetch",
        "page.waitForRequest",
        "exportRequest.url()",
        'searchParams.get("garden_id")',
        "assertCalendarExportIcs",
        "unescapeIcsText",
        "Calendar export leaked credential material",
        "exercisePostMutationReload",
        'page.reload({ waitUntil: "domcontentloaded" })',
        "exerciseMobileCalendarAndNotifications",
        "mobile notification trigger focus return",
        "exerciseNotificationSettingsRace",
        "**/api/notifications/preferences",
        "assertDeduplicatedWeatherCheck",
        "runConcurrentWeatherChecks",
        "Concurrent visible weather checks created or failed to deduplicate a logical alert",
        "exerciseEditorWeatherDeduplication",
        "Offline task actions reached the server before connectivity returned",
        "exerciseImmediateSnoozeCorrection",
        "calendar week view for immediate snooze correction",
        "Calendar correction task remained in the visible week after its +1 week snooze",
        "2s Change date correction action after immediate snooze",
        "immediate one-week snooze mutation",
        "Quick Actions date-dialog parent restoration after submit",
        "Quick Actions completion-dialog parent restoration after submit",
        'action: "complete"',
        'action: "skip"',
        'action: "snooze"',
        'action: "reschedule"',
        "Muted legacy issue-created notification returned after reload",
        "issueCreatedRuleControls",
        "New issues: Email",
        'selectOption("normal")',
        "completed desktop bloom journal card after reload",
        "mobile grouped fertilize journal after reload",
        "Mobile Quick Actions did not expose dialog semantics",
        "Mobile Quick Actions did not inert the main background surface",
        "mobile Quick Actions FAB focus restoration",
    ):
        assert marker in journey_source
    assert "navigator.clipboard.readText" not in journey_source
    assert "systemRuleControls" not in journey_source

    for marker in (
        "writePrivateFailure",
        "complete-journeys-browser-error.log",
        "expectedPhaseTwoCanonicalAttentionRules",
        'preset: "custom"',
        "issue_follow_up_due",
        "issue_follow_up_overdue",
        "offlineTaskKeys",
        "snooze_correction",
        "calendar_feed_token_revocation",
        "calendar_export_selected_garden_scope",
        "ics_export_integrity_scope_redaction",
        "weather_idempotency_cross_surface_refresh",
        "weather_concurrent_identity_deduplication",
        "mobile_quick_actions_accessibility",
        "mobile_calendar_month_week_list_navigation",
        "editor_weather_deduplicated_surfaces",
        "post_mutation_reload_journal_records",
        "mutedIssueWithDigest",
        'notification_type: "issue_created"',
        "saved issue-created eligibility rule",
        "Phase 2 canonical quiet hours retained legacy top-level keys",
        "Phase 2 deterministic weather preparation was unexpected",
    ):
        assert marker in checker_source

    for marker in (
        "PHASE_TWO_CALENDAR_DESCRIPTION",
        "PHASE_TWO_OFFLINE_SNOOZE_DATE",
        "PHASE_TWO_OFFLINE_RESCHEDULE_DATE",
        "PHASE_TWO_SNOOZE_CORRECTION_DUE_DATE",
        "_reset_phase_two_weather_cache",
        'sys.argv[1:] == ["--prepare-phase-two"]',
        '"seeded_description": PHASE_TWO_CALENDAR_DESCRIPTION',
        "expected_issue_attention_rule",
        '"offline": {',
    ):
        assert marker in seed_source
    assert '"token":' not in seed_source
    assert '"feed_url":' not in seed_source


def test_phase_two_subscription_probe_is_wired_to_classified_diagnostics() -> None:
    journey_path = ROOT / "scripts" / "e2e" / "journeys" / "dailyAttentionWork.cjs"
    journey_source = journey_path.read_text(encoding="utf-8")
    helper_start = journey_source.index("async function exerciseCalendarSubscriptionFeed(")
    helper_end = journey_source.index("async function exerciseCalendarLifecycle", helper_start)
    helper_source = journey_source[helper_start:helper_end]
    lifecycle_source = journey_source[
        helper_end : journey_source.index("async function openNotifications", helper_end)
    ]

    assert (
        "async function exerciseCalendarSubscriptionFeed(page, diagnostics, onCreated = null)"
        in helper_source
    )
    assert "page-origin feed fetch" in helper_source
    assert "const revokedStatus = await page.evaluate" in helper_source
    assert 'labelDialog.locator(".prompt-dialog-input").fill(label)' in helper_source
    assert 'labelDialog.locator(".confirm-yes").click()' in helper_source
    assert 'page.once("dialog"' not in helper_source
    assert (
        "exerciseCalendarSubscriptionFeed(page, diagnostics, onSubscriptionCreated);"
        in lifecycle_source
    )
    assert "navigator.clipboard.readText" not in helper_source

    browser_source = (ROOT / "scripts/e2e/completeJourneyBrowser.cjs").read_text(encoding="utf-8")
    assert '"calendar-feed-revoked"' in browser_source
    assert "classifiedConsoleDiagnostics" in browser_source
    assert "expectedHttpDiagnosticContext" in browser_source

    result = subprocess.run(
        ["node", "--check", str(journey_path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_profile_runner_receives_merged_profile_options() -> None:
    script = r"""
const { runDailyAttentionWork } = require('./scripts/e2e/journeys/dailyAttentionWork.cjs');
const observed = [];
runDailyAttentionWork({
  fixture: { roles: { editor: 'editor', viewer: 'viewer' } },
  password: 'admin-password',
  username: 'admin',
}, async (options) => {
  if (!options || !options.profile || !options.role || !options.username || !options.password) {
    throw new Error('profile options were not merged');
  }
  observed.push(`${options.role}:${options.profile}`);
  return { error: null, result: { profile: options.profile, role: options.role } };
}).then(() => {
  const expected = [
    'admin:desktop', 'admin:mobile',
    'editor:desktop', 'editor:mobile',
    'viewer:desktop', 'viewer:mobile',
  ];
  if (JSON.stringify(observed) !== JSON.stringify(expected)) process.exitCode = 2;
}).catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
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


def test_phase_selection_distinguishes_focused_and_cumulative_runs() -> None:
    script = r"""
const { phaseSelected } = require('./scripts/check_complete_journeys_e2e.cjs');
if (phaseSelected(0) || phaseSelected(1) || !phaseSelected(2) || phaseSelected(3)) {
  process.exitCode = 1;
}
"""
    env = {
        **os.environ,
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_PHASE": "2",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_THROUGH_PHASE": "2",
    }
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_focused_phase_session_expectation_preserves_zero_count_users() -> None:
    script = r"""
const { expectedSessionUserCounts } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = { roles: {
  admin: 'admin', editor: 'editor', onboarding: 'onboarding',
  onboarding_mobile: 'onboarding-mobile', viewer: 'viewer',
} };
const profiles = [
  { role: 'admin' }, { role: 'admin' },
  { role: 'editor' }, { role: 'editor' },
  { role: 'viewer' }, { role: 'viewer' },
];
const observed = expectedSessionUserCounts(fixture, profiles, false);
const expected = {
  admin: 2, editor: 2, onboarding: 0, 'onboarding-mobile': 0, viewer: 2,
};
if (JSON.stringify(observed) !== JSON.stringify(expected)) process.exitCode = 1;
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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


def test_console_diagnostics_require_specific_request_status_and_context() -> None:
    script = """
const {
  assertDiagnosticsClean,
  expectedHttpDiagnosticContext,
} = require('./scripts/e2e/completeJourneyBrowser.cjs');
const classify = (authenticated, method, path, status) => expectedHttpDiagnosticContext({
  authenticated, method, path, status,
});
if (classify(false, 'GET', '/api/auth/me', 401) !== 'preauth-session-probe') process.exit(3);
if (classify(true, 'GET', '/api/auth/me', 401) !== 'unexpected-http-response') process.exit(4);
if (classify(true, 'POST', '/api/tasks/task-1/action', 403) !== 'viewer-task-write-denied') {
  process.exit(5);
}
if (classify(true, 'POST', '/api/tasks/task-1/action', 500) !== 'unexpected-http-response') {
  process.exit(6);
}
if (classify(
  true, 'GET', '/calendar/subscriptions/feed-token.ics', 404,
) !== 'calendar-feed-revoked') process.exit(9);
if (classify(
  true, 'GET', '/api/calendar/subscriptions/feed-token.ics', 404,
) !== 'unexpected-http-response') process.exit(10);
const base = {
  blockedRequests: [], consoleErrors: [], expectedAuth401Responses: 1, httpErrors: [],
  pageErrors: [], requestFailures: [],
};
assertDiagnosticsClean({
  ...base,
  classifiedConsoleDiagnostics: [{
    context: 'preauth-session-probe', method: 'GET', path: '/api/auth/me', status: 401,
  }],
}, 'valid');
try {
  assertDiagnosticsClean({
    ...base,
    classifiedConsoleDiagnostics: [
      { context: 'preauth-session-probe', method: 'GET', path: '/api/auth/me', status: 401 },
      {
        context: 'unexpected-http-response', method: 'POST',
        path: '/api/tasks/task-1/action', status: 500,
      },
    ],
  }, 'tampered');
  process.exit(7);
} catch (error) {
  if (!String(error.message).includes('unclassified console diagnostic')) process.exit(8);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_manifest_sanitizer_drops_unknown_root_fields_and_normalizes_requests() -> None:
    script = """
const { sanitizeManifestEvidence } = require('./scripts/check_complete_journeys_e2e.cjs');
const result = sanitizeManifestEvidence({
  browser: 'chromium', database: null, ended_at: '2026-07-12T00:00:00Z',
  failure: null, filesystem: null, git: { dirty: false, sha: 'abc' },
  journey_ids: ['M1'], phase: 1, profiles: [{
    assertions: {}, browser_profile: {}, checks: {}, diagnostics: {
      classifiedConsoleDiagnostics: [{
        context: 'viewer-task-write-denied', diagnostic: 'raw secret=hidden',
        id: 'console-1', method: 'POST', path: '/api/tasks/task-1/action', status: 403,
      }],
    }, profile: 'desktop',
    requests: [{ gardenId: '1', method: 'GET', path: '/api/saved-views' }],
    role: 'admin', trace: 'x',
  }], run_id: 'run', started_at: '2026-07-12T00:00:00Z', status: 'running',
  suite: 'complete-journeys-e2e', through_phase: 1, injected: 'must-not-survive',
});
if ('injected' in result) process.exit(3);
if (result.profiles[0].requests[0].path !== '/api/saved-views') process.exit(4);
if (result.started_at !== '2026-07-12T00:00:00Z') process.exit(5);
if (result.ended_at !== '2026-07-12T00:00:00Z') process.exit(6);
const diagnostic = result.profiles[0].diagnostics.classifiedConsoleDiagnostics[0];
if (diagnostic.context !== 'viewer-task-write-denied' || diagnostic.status !== 403) process.exit(7);
if (diagnostic.path !== '/api/tasks/task-1/action') process.exit(8);
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
      import_rejection_render_churn: {
        rejected_import_render_churn: {
          malformed_json: {
            render_churn: { added: 0, attributes: 0, child_lists: 0, removed: 0 },
          },
        },
      },
      phase_counts: { expected: 7, passed: 7 },
      retry_count: 2,
      boolean_check: true,
    },
    requests: [], role: 'admin', trace: 'x',
  }], run_id: 'run', started_at: '2026-07-12T00:00:00Z', status: 'running',
  suite: 'complete-journeys-e2e', through_phase: 1,
});
const checks = result.profiles[0].checks;
const expectedDelayedSurfaces = ['plants', 'weather'];
if (
  JSON.stringify(checks.delayed_surfaces) !== JSON.stringify(expectedDelayedSurfaces)
) process.exit(3);
if (checks.phase_counts.expected !== 7 || checks.phase_counts.passed !== 7) process.exit(4);
if (checks.retry_count !== 2 || checks.boolean_check !== true) process.exit(5);
if (checks.import_rejection_render_churn.rejected_import_render_churn
  .malformed_json.render_churn.child_lists !== 0) process.exit(9);
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


def test_checker_requires_clean_and_stable_source_provenance() -> None:
    script = """
const { assertSourceRevisionStable } = require('./scripts/check_complete_journeys_e2e.cjs');
const initial = { sha: 'abc123', dirty: false, worktree_fingerprint: 'a'.repeat(64) };
assertSourceRevisionStable(initial, { ...initial });
try {
  assertSourceRevisionStable(initial, { ...initial, worktree_fingerprint: 'b'.repeat(64) });
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('worktree changed')) process.exit(4);
}
try {
  assertSourceRevisionStable({ ...initial, dirty: true }, { ...initial, dirty: true });
  process.exit(5);
} catch (error) {
  if (!String(error.message).includes('clean source worktree')) process.exit(6);
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
  sanitizeManifestEvidence,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const delayedEvidence = (surfaces, desktop = false) => ({
  alpha_started_surfaces: surfaces,
  beta_held_response_count: surfaces.length,
  beta_held_surfaces: surfaces,
  per_surface: Object.fromEntries(surfaces.map((surface) => [surface, {
    alpha_selection_mode: 'physical', alpha_target_started: true,
    alpha_trigger_mode: 'automatic', beta_content_never_landed: true,
    beta_response_arrived: true, beta_response_completion_count: 1,
    beta_target_held: true, beta_trigger_mode: 'automatic', network_guard_reached: true,
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
  browser_profile: {
    has_touch: name === 'mobile',
    is_mobile: name === 'mobile',
    max_touch_points: name === 'mobile' ? 1 : 0,
  },
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
    indoor_reload_persistence: true,
    import_rejection_render_churn: {
      rejected_import_render_churn: {
        cross_garden: {},
        malformed_json: {
          client_error_visible: true,
          import_request_count: 0,
          input_cleared: true,
          render_churn: { added: 0, attributes: 0, child_lists: 0, removed: 0 },
        },
        oversized: {}, structurally_incomplete: {}, unsupported_schema: {},
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
    saved_view_delete_confirmation: true,
  }),
  profile('admin', 'mobile', {
    delayed_surfaces: delayedMobile,
    garden_settings_reload_persistence: true,
    indoor_reload_persistence: true,
    map_first_without_plants: true,
    mobile_supported_writes_and_focus_return: true,
    role_cross_garden_response_isolation: true,
    saved_view_delete_confirmation: true,
  }),
  profile('editor', 'desktop', {
    editor_a3_settings_and_m4_layout_write: true,
    editor_m1_m3_supported_writes: true,
    editor_profile_write_affordances_and_admin_denial: true,
    editor_settings_layout_reload_persistence: true,
    map_first_without_plants: true,
    role_cross_garden_response_isolation: true,
    role_delayed_surfaces: delayedPlots,
    saved_view_delete_confirmation: true,
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
    viewer_a3_m4_write_unavailable: true,
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
assertPhaseOneProfileEvidence(sanitizeManifestEvidence({ profiles }).profiles);
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
const prohibitedDirectViewerDenials = [
  ['POST', '/api/gardens/{garden_id}/map-objects', 403],
  ['PATCH', '/api/gardens/{garden_id}/settings', 403],
  ['POST', '/api/snapshots', 403],
  ['POST', '/api/plots/import', 403],
];
for (const [method, path, statusCode] of prohibitedDirectViewerDenials) {
  if (phaseOneAuditExpectedEvents(8).some((event) => (
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


def test_backend_error_evidence_counts_error_critical_and_fatal_without_log_contents(
    tmp_path: Path,
) -> None:
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
fs.appendFileSync(`${{directory}}/backend.log`, 'CRITICAL: synthetic backend failure\\n');
fs.appendFileSync(`${{directory}}/backend.log`, 'FATAL: synthetic backend failure\\n');
fs.appendFileSync(`${{directory}}/errors.jsonl`, '{{"level":"ERROR"}}\\n');
fs.appendFileSync(`${{directory}}/errors.jsonl`, '{{"level":"CRITICAL"}}\\n');
fs.appendFileSync(`${{directory}}/errors.jsonl`, '{{"level":"FATAL"}}\\n');
const evidence = backendErrorEvidence(directory);
if (evidence.backend_error_lines !== 1 || evidence.backend_critical_lines !== 1
  || evidence.backend_fatal_lines !== 1 || evidence.structured_error_entries !== 1
  || evidence.structured_critical_entries !== 1 || evidence.structured_fatal_entries !== 1
  || evidence.unexpected_error_count !== 6) process.exit(3);
try {{
  assertNoUnexpectedBackendErrors(directory);
  process.exit(4);
}} catch (error) {{
  if (String(error.message) !== (
    'Unexpected backend ERROR, CRITICAL, or FATAL log entries; inspect private runner logs'
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
const stripeCanary = ['sk', 'live', 'abcdefghijklmnopqrstuvwxyz012345'].join('_');
const githubCanary = ['gh', 'p_abcdefghijklmnopqrstuvwxyz0123456789'].join('');
const value = sanitizeDiagnostic(
  [
    'Authorization: Bearer canary-value',
    'https://x/?api_key=key-value&refresh_token=refresh-value',
    ['OPENAI', 'API', 'KEY=provider-value'].join('_') + ' AUTH_PASSWORD:password-value',
    'AWS_SECRET_ACCESS_KEY=cloud-value CLIENT_SECRET=client-value',
    `${stripeCanary} ${githubCanary}`,
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJqd3QtY2FuYXJ5In0.signature-canary',
  ].join(' '),
);
const leaked = [
  'canary-value', 'key-value', 'refresh-value', 'provider-value',
  'password-value', 'cloud-value', 'client-value', stripeCanary,
  githubCanary, 'signature-canary',
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


def test_manifest_sanitizer_redacts_token_shaped_identifier_and_database_values() -> None:
    script = """
const { sanitizeManifestEvidence } = require('./scripts/check_complete_journeys_e2e.cjs');
const specimen = ['sk', 'live', 'abcdefghijklmnopqrstuvwxyz012345'].join('_');
const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJqd3QtY2FuYXJ5In0.signature-canary';
const result = sanitizeManifestEvidence({
  database: { nested: { token: specimen, jwt } },
  profiles: [{
    assertions: { passed: [specimen] }, browser_profile: {},
    checks: { token: specimen }, diagnostics: {},
    requests: [{ actorUsername: specimen, gardenId: 1, method: 'POST', operationId: specimen,
      path: '/api/tasks/tsk_example/action', statusCode: 200 }], structure: {},
  }],
});
const serialized = JSON.stringify(result);
if (serialized.includes(specimen) || serialized.includes(jwt)) process.exit(3);
if (!serialized.includes('[redacted diagnostic; inspect private runner logs]')) process.exit(4);
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


def test_phase_two_manifest_request_paths_are_bounded_and_readable() -> None:
    script = """
const { isSafeManifestRequestPath } = require('./scripts/check_complete_journeys_e2e.cjs');
const expected = [
  '/api/calendar/events',
  '/api/calendar/manual-events/calevt_example',
  '/api/calendar/subscriptions/calsub_example',
  '/api/media/summaries',
  '/api/notifications/note_example',
  '/api/plots/PLOT-1/plant-alerts',
  '/api/plots/PLOT-1/plants',
  '/api/security/csp-report',
  '/api/tasks/tsk_example/action',
  '/api/weather/alerts/7/dismiss',
];
if (!expected.every(isSafeManifestRequestPath)) process.exit(3);
for (const unsafe of [
  '/api/auth/sessions',
  '/api/calendar/subscriptions/token/secret',
  '/api/not-a-real-phase-two-route',
  '/outside-api',
]) {
  if (isSafeManifestRequestPath(unsafe)) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_subscription_feed_url_is_redacted_from_manifest_evidence() -> None:
    script = r"""
const { sanitizeManifestEvidence } = require('./scripts/check_complete_journeys_e2e.cjs');
const token = 'calendar-feed-token-must-not-escape';
const url = `/calendar/subscriptions/${token}.ics`;
const manifest = sanitizeManifestEvidence({
  profiles: [{
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: {},
    diagnostics: { httpErrors: [`404 ${url}`] },
    requests: [{ gardenId: 1, method: 'GET', path: url }],
    structure: {},
  }],
});
const serialized = JSON.stringify(manifest);
if (serialized.includes(token) || serialized.includes(url)) process.exit(2);
if (!serialized.includes('[redacted diagnostic')) process.exit(3);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_complete_journey_browser_guard_uses_exact_contract_origins_and_pixel_7_runtime() -> None:
    script = """
const {
  allowedBrowserOrigins,
  assertBrowserProfileContract,
  isAllowedUrl,
} = require('./scripts/e2e/completeJourneyBrowser.cjs');
const origins = allowedBrowserOrigins({
  backendUrl: 'http://127.0.0.1:43102',
  baseUrl: 'http://127.0.0.1:43101',
  providerUrl: 'http://127.0.0.1:43103',
});
if (!isAllowedUrl('http://127.0.0.1:43101/api/tasks', origins)) process.exit(3);
if (!isAllowedUrl('ws://127.0.0.1:43101/vite', origins)) process.exit(4);
for (const url of [
  'http://127.0.0.1:43104/api/tasks',
  'http://127.0.0.2:43101/api/tasks',
  'http://localhost:43101/api/tasks',
]) {
  if (isAllowedUrl(url, origins)) process.exit(5);
}
assertBrowserProfileContract('mobile', {
  has_touch: true,
  is_mobile: true,
  max_touch_points: 5,
  user_agent: 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36',
  viewport: { width: 412, height: 839 },
});
try {
  assertBrowserProfileContract('mobile', {
    has_touch: true,
    is_mobile: true,
    max_touch_points: 1,
    user_agent: 'Mozilla/5.0 (Android)',
    viewport: { width: 390, height: 844 },
  });
  process.exit(6);
} catch (error) {
  if (!/Pixel 7/i.test(String(error.message))) process.exit(7);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_pixel_7_user_agent_contract_is_persisted_with_profile_evidence() -> None:
    checker_source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")
    assert "user_agent_contract === expectedUserAgentContract" in checker_source
    script = """
const {
  assertBrowserProfileContract,
} = require('./scripts/e2e/completeJourneyBrowser.cjs');
const { sanitizeManifestEvidence } = require('./scripts/check_complete_journeys_e2e.cjs');
const runtime = {
  has_touch: true,
  is_mobile: true,
  max_touch_points: 5,
  user_agent: 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36',
  viewport: { width: 412, height: 839 },
};
const userAgentContract = assertBrowserProfileContract('mobile', runtime);
if (userAgentContract !== 'pixel-7') process.exit(3);
const manifest = sanitizeManifestEvidence({
  profiles: [{
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: { ...runtime, user_agent_contract: userAgentContract },
    diagnostics: {}, requests: [], structure: {},
  }],
});
if (manifest.profiles[0].browser_profile.user_agent_contract !== 'pixel-7') process.exit(4);
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_journey_forbids_node_request_clients_and_unscoped_notification_mutation() -> (
    None
):
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")
    assert "context().request" not in source
    assert ".context().request" not in source
    assert "page.context().request" not in source
    assert 'const remaining = panel.locator(".notification-item").first()' not in source
    assert "Phase 2 explicit notification fixture" in source
    assert "page-origin feed fetch" in source


def test_phase_two_delayed_races_do_not_wait_for_the_intentionally_held_beta_surface() -> None:
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")

    assert "{ waitForSettle = true } = {}" in source
    assert (
        source.count(
            'selectGarden(page, "desktop", fixture.gardens.beta.id, { waitForSettle: false })'
        )
        == 3
    )
    assert "waitForSettle: false });\n      await openTasks" not in source
    assert "waitForSettle: false });\n      await startCalendar" not in source


def test_phase_two_evidence_contract_preserves_phase_one_and_sanitizes_trace_database_evidence(
    tmp_path: Path,
) -> None:
    checker_source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")
    for marker in (
        '"alpha_snapshot_payload"',
        '"lifecycle_audit"',
        '"onboarding_target_graphs"',
        '"temp_saved_view_count"',
    ):
        assert marker in checker_source
    seed_source = (ROOT / "scripts/seed_complete_journeys_e2e.py").read_text(encoding="utf-8")
    stable_projection = seed_source.split("def _phase_one_stable_domain_projection", 1)[1].split(
        "def _entry_link_ids", 1
    )[0]
    assert "phase_two_task_ids" in stable_projection
    assert "source_task_id" in stable_projection
    script = """
const {
  assertPhaseOneStatePreservedAfterPhaseTwo,
  expectedPhaseOneRestoreGraphsAfterPhaseTwo,
  expectedPhaseOneStableDomainProjectionAfterPhaseTwo,
  sanitizeManifestEvidence,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const state = {
  alpha_address: 'restored address',
  alpha_map_object: { public_id: 'mapobj_alpha' },
  alpha_map_unit: { public_id: 'mapunit_alpha' },
  indoor_assignment: { plot_id: 'COMPLETE-PHASE-ONE-INDOOR' },
  restore_import_graphs: { alpha: { plots: [] } },
  saved_view: { label: 'Complete Phase One Basil View' },
  stable_domain_projection: { gardens: [{ id: 1 }] },
};
assertPhaseOneStatePreservedAfterPhaseTwo(state, structuredClone(state));
const fixture = {
  phase_two: { date: '2026-07-12', plant_ids: { bloom_desktop: 'P2-BLOOM' } },
};
const bloomBoundary = {
  restore_import_graphs: {
    alpha: {
      assignments: [{ plant_id: 'P2-BLOOM', seen_growing: true, seen_growing_date: '2026-07-01' }],
      plants: [{ plant_id: 'P2-BLOOM', seen_growing: true, seen_growing_date: '2026-07-01' }],
    },
  },
};
const bloomFinal = structuredClone(bloomBoundary);
for (const collection of ['assignments', 'plants']) {
  bloomFinal.restore_import_graphs.alpha[collection][0].seen_growing_date = '2026-07-12';
}
assertPhaseOneStatePreservedAfterPhaseTwo(bloomBoundary, bloomFinal, fixture);
const expectedBloom = expectedPhaseOneRestoreGraphsAfterPhaseTwo(
  bloomBoundary.restore_import_graphs,
  fixture,
);
if (expectedBloom.alpha.plants[0].seen_growing_date !== '2026-07-12') process.exit(7);
const maintenanceBoundary = {
  alpha_id: 17,
  stable_domain_projection: {
    app_settings: [{ key: 'unrelated', value: 'preserved' }],
    gardens: [{ id: 23 }],
  },
};
const maintenanceFixture = {
  clock: { attention_now_ms: 1783857600000 },
  phase_two: { date: '2026-07-12' },
};
const maintenanceFinal = structuredClone(maintenanceBoundary);
maintenanceFinal.stable_domain_projection.app_settings.push(
  { key: 'last_task_gen_month:17', value: '2026-07' },
  { key: 'last_weather_check_ms:17', value: '1783857600000' },
);
maintenanceFinal.stable_domain_projection.app_settings.sort((left, right) =>
  JSON.stringify(left).localeCompare(JSON.stringify(right)),
);
assertPhaseOneStatePreservedAfterPhaseTwo(
  maintenanceBoundary,
  maintenanceFinal,
  maintenanceFixture,
);
const expectedMaintenance = expectedPhaseOneStableDomainProjectionAfterPhaseTwo(
  maintenanceBoundary.stable_domain_projection,
  maintenanceFixture,
  maintenanceBoundary.alpha_id,
);
if (expectedMaintenance.app_settings.length !== 3) process.exit(10);
try {
  const unrelatedSetting = structuredClone(maintenanceFinal);
  unrelatedSetting.stable_domain_projection.app_settings.push({ key: 'unexpected', value: '1' });
  assertPhaseOneStatePreservedAfterPhaseTwo(
    maintenanceBoundary,
    unrelatedSetting,
    maintenanceFixture,
  );
  process.exit(11);
} catch (error) {
  if (!/stable_domain_projection/.test(String(error.message))) process.exit(12);
}
try {
  const unrelatedBloomMutation = structuredClone(bloomFinal);
  unrelatedBloomMutation.restore_import_graphs.alpha.plants[0].name = 'changed';
  assertPhaseOneStatePreservedAfterPhaseTwo(bloomBoundary, unrelatedBloomMutation, fixture);
  process.exit(8);
} catch (error) {
  if (!/restore_import_graphs/.test(String(error.message))) process.exit(9);
}
try {
  const mutated = structuredClone(state);
  mutated.alpha_address = 'Phase 2 changed Phase 1';
  assertPhaseOneStatePreservedAfterPhaseTwo(state, mutated);
  process.exit(3);
} catch (error) {
  if (!/Phase 1.*Phase 2/i.test(String(error.message))) process.exit(4);
}
const secret = ['postgresql://u:p', '@dummy.example/db'].join('');
const result = sanitizeManifestEvidence({
  database: {
    phase_two_maintenance: {
      nested: { detail: secret },
      deliveries: [{ recipient: secret }],
    },
  },
  profiles: [{
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: {}, diagnostics: {}, requests: [], structure: {},
    trace: { name: 'mobile-admin-passed.zip', sha256: 'a'.repeat(64) },
  }],
});
const serialized = JSON.stringify(result);
if (serialized.includes(secret)) process.exit(5);
const trace = result.profiles[0].trace;
if (trace.name !== 'mobile-admin-passed.zip' || trace.sha256 !== 'a'.repeat(64)) process.exit(6);
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_audit_paths_include_visible_reads_and_preference_writes_only() -> None:
    script = """
const { isPhaseTwoAuditPath } = require('./scripts/check_complete_journeys_e2e.cjs');
for (const expected of [
  '/api/attention/preferences',
  '/api/attention/items/attn:task:task-1/snooze',
  '/api/attention/outcomes/outcome-1/restore',
  '/api/calendar/preferences',
  '/api/media/summaries',
  '/api/notifications/preferences',
  '/api/tasks/tsk_example/action',
]) {
  if (!isPhaseTwoAuditPath(expected)) process.exit(3);
}
for (const unexpected of [
  '/api/media',
  '/api/media/summaries/private',
  '/api/attention/items/attn:task:task-1/delete',
  '/api/not-a-phase-two-route',
]) {
  if (isPhaseTwoAuditPath(unexpected)) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_audit_correlation_requires_exact_actor_auth_garden_and_request_pairing() -> None:
    script = """
const { assertPhaseTwoAuditEvents } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = {
  clock: { attention_now_ms: 1783857600000 },
  roles: { admin: 'admin', viewer: 'viewer' },
};
const request = {
  actorAuthType: 'session', actorRole: 'viewer', actorUsername: 'viewer',
  gardenId: '7', method: 'POST', path: '/api/tasks/tsk_example/action',
  requestId: 'viewer-task-denial-1', statusCode: 403,
};
const event = {
  actor_auth_type: 'session', actor_role: 'viewer', actor_username: 'viewer',
  garden_id: 7, id: 41, method: 'POST', occurred_at_ms: 1783857600001,
  path: '/api/tasks/tsk_example/action', request_id: 'viewer-task-denial-1', status_code: 403,
};
const profiles = [{ profile: 'mobile', role: 'viewer', requests: [request] }];
const evidence = assertPhaseTwoAuditEvents(
  { records: [] }, { records: [event] }, profiles, fixture,
);
if (evidence.phase_two_audit_mutations_one_to_one !== true
  || evidence.phase_two_audit_wall_clock_uncontrolled !== true) process.exit(3);
try {
  assertPhaseTwoAuditEvents(
    { records: [] },
    { records: [{ ...event, actor_username: 'wrong-user' }] },
    profiles,
    fixture,
  );
  process.exit(4);
} catch (error) {
  if (!/exact browser mutation/.test(String(error.message))) process.exit(5);
}
try {
  assertPhaseTwoAuditEvents(
    { records: [] },
    { records: [{ ...event, id: 42 }, event] },
    profiles,
    fixture,
  );
  process.exit(6);
} catch (error) {
  if (!/exact browser mutation/.test(String(error.message))) process.exit(7);
}
const putRequest = {
  actorAuthType: 'session', actorRole: 'admin', actorUsername: 'admin',
  gardenId: '7', method: 'PUT', path: '/api/notifications/preferences',
  requestId: 'admin-preferences-put-1', statusCode: 200,
};
const putEvent = {
  actor_auth_type: 'session', actor_role: 'admin', actor_username: 'admin',
  garden_id: 7, id: 43, method: 'PUT', occurred_at_ms: 1783857600002,
  path: '/api/notifications/preferences', request_id: 'admin-preferences-put-1', status_code: 200,
};
assertPhaseTwoAuditEvents(
  { records: [] },
  { records: [event, putEvent] },
  [
    { profile: 'mobile', role: 'viewer', requests: [request] },
    { profile: 'desktop', role: 'admin', requests: [putRequest] },
  ],
  fixture,
);
try {
  assertPhaseTwoAuditEvents(
    { records: [] },
    { records: [event] },
    [
      { profile: 'mobile', role: 'viewer', requests: [request] },
      { profile: 'desktop', role: 'admin', requests: [putRequest] },
    ],
    fixture,
  );
  process.exit(8);
} catch (error) {
  if (!/PUT browser mutations/.test(String(error.message))) process.exit(9);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_scoped_mutable_projection_rejects_extra_allowed_table_rows_additional() -> None:
    script = """
const { assertPhaseTwoScopedMutableRows } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = {
  clock: { attention_now_ms: 1783857600000 },
  gardens: { alpha: { id: 1 } },
  phase_two: {
    preference_delivery: {
      eligible: { public_id: 'note_delivery_eligible' },
      ineligible: { public_id: 'note_delivery_ineligible' },
    },
    task_ids: { fertilize_grouped: 'task-fertilize' },
  },
  roles: { editor: 'editor', viewer: 'viewer' },
};
const semantic = {
  rows_before: {
    tasks: [{ public_id: 'tsk_before', row_id: 1 }],
    notifications: [{ public_id: 'note_before', row_id: 2 }],
    weather_alerts: [{ row_id: 3 }],
  },
  rows_after: {
    tasks: [{ public_id: 'tsk_before', row_id: 1 }, { public_id: 'tsk_created', row_id: 4 }],
    notifications: [{ public_id: 'note_before', row_id: 2 }],
    weather_alerts: [{ row_id: 3 }, { row_id: 5 }],
  },
};
const finalRows = {
  tasks: structuredClone(semantic.rows_after.tasks),
  notifications: [
    ...structuredClone(semantic.rows_after.notifications),
    { public_id: 'note_delivery_eligible', row_id: 6 },
    { public_id: 'note_delivery_ineligible', row_id: 7 },
    {
      cleared_at_ms: 1783857600000, clear_reason: 'expired', created_at_ms: 1783857600000,
      garden_id: 1, notification_type: 'task_due', public_id: 'note_editor', row_id: 8,
      target_id: 'task-fertilize', target_type: 'task', username: 'editor',
    },
    {
      cleared_at_ms: 1783857600000, clear_reason: 'rescheduled', created_at_ms: 1783857600000,
      garden_id: 1, notification_type: 'task_due', public_id: 'note_viewer', row_id: 9,
      target_id: 'task-fertilize', target_type: 'task', username: 'viewer',
    },
  ],
  weather_alerts: structuredClone(semantic.rows_after.weather_alerts),
};
assertPhaseTwoScopedMutableRows(semantic, finalRows, fixture);
try {
  const changed = structuredClone(finalRows);
  changed.weather_alerts.push({ row_id: 8 });
  assertPhaseTwoScopedMutableRows(semantic, changed, fixture);
  process.exit(3);
} catch (error) {
  if (!/extra or missing mutable row/.test(String(error.message))) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_maintenance_reference_spec_rejects_observed_histogram_drift() -> None:
    script = """
const { assertPhaseTwoMaintenanceSpec } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = {
  roles: { admin: 'admin-user', editor: 'editor-user', viewer: 'viewer-user' },
  phase_two: { maintenance_expectations: {
    created: {
      tasks: { total: 2, by_type: { protect: 1, water: 1 }, by_rule_family: { auto: 1, water: 1 } },
      notifications: {
        total: 3, by_role: { admin: 1, editor: 1, viewer: 1 },
        by_type: { 'task_due:': 3 },
      },
      weather_alerts: { total: 1, by_type: { heat_wave: 1 } },
    },
    mutated_existing: { notifications: 0, tasks: 1, weather_alerts: 0 },
    summary: {
      configured: true, gardens_processed: 1, notifications_created: 3,
      tasks_auto_created: 1, tasks_expired: 1, weather_alerts_created: 1,
      weather_tasks_created: 1,
    },
  } },
};
const created = {
  tasks: {
    created: [
      { rule_source: 'water:plant:date', task_type: 'water' },
      { rule_source: 'auto:heat:plant', task_type: 'protect' },
    ],
    mutated_existing: [{}],
  },
  notifications: {
    created: ['admin-user', 'editor-user', 'viewer-user'].map((username) => ({
      notification_subtype: '', notification_type: 'task_due', username,
    })),
    mutated_existing: [],
  },
  weather_alerts: { created: [{ alert_type: 'heat_wave' }], mutated_existing: [] },
};
assertPhaseTwoMaintenanceSpec(created, fixture.phase_two.maintenance_expectations.summary, fixture);
try {
  const drifted = structuredClone(created);
  drifted.tasks.created.push({ rule_source: 'auto:unexpected:plant', task_type: 'protect' });
  assertPhaseTwoMaintenanceSpec(
    drifted, fixture.phase_two.maintenance_expectations.summary, fixture,
  );
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('task count')) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_whole_table_projection_contract_rejects_unprojected_allowed_table() -> None:
    script = """
const {
  assertWholeTableProjectionCoverage,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const row = (count, digest) => ({ count, digest: digest.repeat(32) });
const initial = { garden_tasks: row(2, 'a'), notification_events: row(3, 'b') };
const final = { garden_tasks: row(4, 'c'), notification_events: row(5, 'd') };
assertWholeTableProjectionCoverage(
  initial, final, new Set(['garden_tasks', 'notification_events']),
);
try {
  assertWholeTableProjectionCoverage(
    initial,
    { garden_tasks: final.garden_tasks },
    new Set(['garden_tasks', 'notification_events']),
  );
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('coverage changed')) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_profile_order_declares_and_rejects_choreography_permutation() -> None:
    script = """
const { assertPhaseTwoProfileOrder } = require('./scripts/check_complete_journeys_e2e.cjs');
const profiles = [
  ['admin', 'desktop'], ['admin', 'mobile'], ['editor', 'desktop'],
  ['editor', 'mobile'], ['viewer', 'desktop'], ['viewer', 'mobile'],
].map(([role, profile]) => ({ role, profile }));
assertPhaseTwoProfileOrder(profiles);
try {
  assertPhaseTwoProfileOrder([profiles[1], profiles[0], ...profiles.slice(2)]);
  process.exit(3);
} catch (error) {
  if (!String(error.message).includes('shared-state choreography')) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_read_only_profiles_prove_a_distinct_fresh_context_permutation() -> None:
    script = """
const {
  assertPhaseTwoReadOnlyPermutationEvidence,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const order = [
  ['viewer', 'mobile'], ['admin', 'desktop'], ['editor', 'mobile'],
  ['viewer', 'desktop'], ['admin', 'mobile'], ['editor', 'desktop'],
];
const profiles = order.map(([role, profile], index) => ({
  checks: {
    browser_diagnostics: true,
    domain_mutation_requests_absent: true,
    execution_model: 'fresh-context-read-only-permutation',
    phase_two_read_only_scope_probe: true,
    probe_sequence: index + 1,
    shared_state_mutation_claimed: false,
  },
  failure: null,
  profile,
  role,
}));
const evidence = assertPhaseTwoReadOnlyPermutationEvidence(profiles);
if (evidence.shared_state_mutation_claimed !== false || evidence.expected_profile_count !== 6) {
  process.exit(3);
}
try {
  assertPhaseTwoReadOnlyPermutationEvidence([...profiles].reverse());
  process.exit(4);
} catch (error) {
  if (!String(error.message).includes('declared permutation')) process.exit(5);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr

    checker_source = CHECKER.read_text(encoding="utf-8")
    assert "runPhaseTwoReadOnlyPermutation" in checker_source
    assert 'currentStage = "phase-two-read-only-permutation"' in checker_source
    assert "unexpectedMutationRequests.length === 0" in checker_source


def test_phase_two_profile_contract_requires_mobile_lifecycle_and_viewer_today_weather_checks() -> (
    None
):
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")
    journey_source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(
        encoding="utf-8"
    )
    for marker in (
        "mobile_partial_grouped_task_work",
        "mobile_snooze_manual_date",
        "mobile_calendar_lifecycle",
        "mobile_notification_preference_mutation",
        "mobile_history_reload",
        "viewer_today_weather_affordances",
        "tasks_calendar_subscriptions_aba_race",
        "stale_dom_assertions",
    ):
        assert marker in source
    assert "recorder.attachPage(peer);" in journey_source


def test_phase_two_completion_journals_retain_task_plot_context() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    journal_expectations = source.split("const journalExpectations = {", 1)[1].split(
        "assert(\n    state.journal.length", 1
    )[0]

    for task_key in ("bloom_desktop", "bloom_mobile"):
        task_expectation = journal_expectations.split(
            f"[phase.task_ids.{task_key}]: {{", 1
        )[1].split("    },", 1)[0]
        assert "plot_ids: [phase.plot_ids.alpha]," in task_expectation


def test_phase_two_mobile_quick_action_keeps_manual_date_completion_actionable() -> None:
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")
    quick_actions = source.split("async function completeMobileQuickActions", 1)[1].split(
        "async function exerciseEditorCalendar", 1
    )[0]

    assert ".fill(fixture.phase_two.date);" in quick_actions
    assert ".fill(fixture.phase_two.manual_date);" not in quick_actions


def test_phase_two_snooze_correction_opens_mobile_week_overflow() -> None:
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")
    correction = source.split("async function exerciseImmediateSnoozeCorrection", 1)[1].split(
        "async function snoozePruneWithManualDate", 1
    )[0]

    assert 'page.locator(".fc-daygrid-more-link:visible").last()' in correction
    assert 'page.locator(".fc-popover .fc-event:visible")' in correction


def test_phase_two_offline_calendar_is_loaded_before_connectivity_is_lost() -> None:
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")
    offline = source.split("async function exerciseOfflineTask", 1)[1].split(
        "async function exerciseViewer", 1
    )[0]

    warmup = offline.index('"calendar skip task before going offline"')
    cold_disconnect = offline.index("page.context().setOffline(true)")
    warm_disconnect = offline.index("page.context().setOffline(true)", warmup)
    assert cold_disconnect < warmup < warm_disconnect
    assert (
        'openSubMode(page, "mobile", "activity", "tasks", "#tasks-tab-content")'
        in offline[cold_disconnect:warmup]
    )
    assert 'await openTasks(page, "mobile");' in offline[warmup:warm_disconnect]


def test_attention_preferences_strip_legacy_quiet_hours_before_save() -> None:
    source = (ROOT / "frontend/src/components/attentionTodayPanel.ts").read_text(encoding="utf-8")
    collector = source.split("function collectQuietHours", 1)[1].split(
        "function collectMetadata", 1
    )[0]

    for field in ("active", "end", "end_hour", "from", "start", "start_hour", "to"):
        assert f'"{field}"' in collector
    assert "delete quietHours[field]" in collector


def test_phase_two_checker_allows_only_exact_viewer_personal_preference_changes() -> None:
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")

    assert "Phase 2 viewer personal preference normalization was unexpected" in source
    assert "attention_metadata: { weather_aware_watering_suppression: true }" in source
    assert 'digest: { enabled: false, end: "07:00", start: "22:00" }' in source


def test_phase_two_viewer_calendar_preference_matches_patch_request() -> None:
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")
    viewer = source.split("async function exerciseViewer", 1)[1].split(
        "async function runProfile", 1
    )[0]

    assert 'response.request().method() !== "PATCH"' in viewer
    assert 'response.request().method() !== "PUT"' not in viewer


def test_phase_two_notification_projection_uses_exact_identities_not_magic_count() -> None:
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")

    assert "state.notifications.length === 43" not in source
    assert "Phase 2 task and seeded notification projection identities were unexpected" in source
    assert "expectedNotificationIds" in source
    assert "groupedTaskNotificationUsers" in source
    assert "!afterMaintenanceNotificationIds.has(notification.public_id)" in source


def test_phase_two_weather_projection_uses_exact_identities_not_magic_count() -> None:
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")

    assert "phase.seeded_state.weather_alerts.length + 4" not in source
    assert "Phase 2 generated weather alert identities were unexpected" in source
    assert "expectedWeatherIds" in source


def test_phase_two_rain_reassessment_expectation_is_horticulturally_explicit() -> None:
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")

    assert 'const expectedRainValidUntil = "2026-07-14";' in source
    assert 'const expectedRainReassessmentOn = "2026-07-16";' in source
    assert 'rain_reassessment_delay_days: 2' in source
    assert 'rain_reassessment_policy: "check_root_zone_moisture_before_watering"' in source
    assert 'rainOutdoor.due_on === "2026-07-15"' not in source
    assert 'reason: "absent_from_current_forecast"' in source
    assert 'source: "forecast_reconciliation"' in source
    assert "resolved_at_ms: fixture.clock.attention_now_ms" in source


def test_phase_two_maintenance_summary_is_derived_from_independent_fixture_spec() -> None:
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")

    seed_source = (ROOT / "scripts/seed_complete_journeys_e2e.py").read_text(encoding="utf-8")
    assert "assertPhaseTwoMaintenanceSpec" in source
    assert "maintenance_expectations.summary" in source
    assert "maintenanceCreated.notifications.created.length" not in source
    assert "PHASE_TWO_MAINTENANCE_EXPECTATIONS" in seed_source
    assert '"by_rule_family"' in seed_source
    assert '"by_role"' in seed_source
    assert '"mutated_existing"' in seed_source


def test_phase_two_maintenance_notifications_have_exact_post_journey_lifecycle() -> None:
    source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")

    assert "expectedPhaseTwoMaintenanceNotification" in source
    assert 'reason: "superseded", stage: 0' in source
    assert 'reason: "snoozed", stage: 1' in source
    assert 'notificationStage <= action.stage ? "expired" : action.reason' in source
    assert "preferenceDelivery?.delivery_notifications" in source


def test_phase_two_post_save_delivery_uses_explicit_fixture_events_and_exact_evidence() -> None:
    journey_source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(
        encoding="utf-8"
    )
    checker_source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")
    seed_source = (ROOT / "scripts/seed_complete_journeys_e2e.py").read_text(encoding="utf-8")
    for marker in (
        "PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID",
        "PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID",
        "PHASE_TWO_DELIVERY_ELIGIBLE_ISSUE_PUBLIC_ID",
        "PHASE_TWO_DELIVERY_INELIGIBLE_ISSUE_PUBLIC_ID",
        "--phase-two-preference-delivery",
        "deliver_pending_email_digests",
        "preference_delivery_issues",
        "_run_phase_two_preference_delivery",
    ):
        assert marker in seed_source
    for marker in (
        "onPreferencesSaved",
        "eligible delivery notification in Today",
        "ineligible delivery notification leaked into inbox",
        "post-save preference delivery badge",
    ):
        assert marker in journey_source
    for marker in (
        "preference_delivery: phaseTwoPreferenceDelivery",
        "preference_delivery_exact",
        "expectedPreferenceDeliveryIssues",
        '"garden_issues"',
        "preference_delivery_rows",
        "delivery_badge_count",
    ):
        assert marker in checker_source


def test_phase_two_harness_forbids_direct_mutation_probes_and_verifies_trace_artifacts() -> None:
    garden_map_source = (ROOT / "scripts/e2e/journeys/gardenMapPlants.cjs").read_text(
        encoding="utf-8"
    )
    checker_source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")
    assert "issueBrowserRequest" not in garden_map_source
    assert "assertExpectedBrowserFailure" not in garden_map_source
    assert "assertTraceArtifacts" in checker_source
    assert "trace_artifacts" in checker_source
    assert "sha256" in checker_source
    assert "if (!manifest.trace_artifacts && manifest.profiles.length > 0)" in checker_source


def test_phase_two_database_contract_covers_maintenance_and_audit_semantics() -> None:
    checker_source = (ROOT / "scripts/check_complete_journeys_e2e.cjs").read_text(encoding="utf-8")
    seed_source = (ROOT / "scripts/seed_complete_journeys_e2e.py").read_text(encoding="utf-8")
    for marker in (
        "assertExpectedMaintenanceMutations",
        "maintenance_semantic_state",
        "maintenance_created",
        "maintenance_rows",
        "phase_two_audit_events",
        "assertPhaseOneStatePreservedAfterPhaseTwo",
        "phase_one_scoped_state_preserved_after_phase_two",
    ):
        assert marker in checker_source or marker in seed_source
    assert '"before": before_by_id[row_id]' in seed_source
    assert '"after": after_by_id[row_id]' in seed_source
    assert "summary counts alone" in checker_source


def test_phase_two_maintenance_mutation_contract_rejects_unexpected_fields() -> None:
    script = """
const { assertExpectedMaintenanceMutations } = require('./scripts/check_complete_journeys_e2e.cjs');
if (typeof assertExpectedMaintenanceMutations !== 'function') process.exit(2);
const before = {
  created_at_ms: 1783857600000,
  garden_id: 1,
  metadata: { fixture: 'complete_journeys_phase_2' },
  public_id: 'tsk_complete_p2_stale_generated_water',
  row_id: 10,
  status: 'pending',
  title: 'Water Phase 2 stale generated mint',
  updated_at_ms: 1783857600000,
};
const after = {
  ...before,
  metadata: {
    fixture: 'complete_journeys_phase_2',
    lifecycle: { expired_at_ms: 1783857600000, status: 'expired' },
  },
  status: 'expired',
};
const weatherBefore = {
  alert_type: 'frost_warning', created_at_ms: 1783983645512,
  description: 'Earlier frost forecast', dismissed: false, garden_id: 1,
  metadata: { coldest: -1, coldest_date: '2026-07-12', frost_days: [['2026-07-12', -1]] },
  plant_ids: ['OPT-JOURNEY-A-PLANT'], row_id: 1, severity: 'normal',
  title: 'Frost warning: -1\u00b0C expected', valid_from: '2026-07-12', valid_until: '2026-07-21',
};
const weatherAfter = {
  ...weatherBefore,
  description: 'Frost expected on 1 day(s). Coldest: -3.0\u00b0C on 2026-07-12. '
    + 'Protect tender plants.',
  metadata: {
    coldest: -3, coldest_date: '2026-07-12', frost_days: [['2026-07-12', -3]],
    plant_advice: [{
      hardiness: 'H1', min_safe_temp: 15, name: 'Phase 2 Mobile Tomato',
      plt_id: 'COMPLETE-P2-FERT-MOBILE',
    }],
  },
  plant_ids: ['COMPLETE-P2-FERT-MOBILE', 'OPT-JOURNEY-A-PLANT'],
  title: 'Frost warning: -3\u00b0C expected',
};
const evidence = {
  notifications: { mutated_existing: [] },
  tasks: { mutated_existing: [{ before, after }] },
  weather_alerts: { mutated_existing: [{ before: weatherBefore, after: weatherAfter }] },
};
const fixture = {
  clock: { attention_now_ms: 1783857600000 },
  gardens: { alpha: { id: 1 } },
  phase_two: {
    plant_ids: { fertilize_mobile: 'COMPLETE-P2-FERT-MOBILE' },
    plant_names: { fertilize_mobile: 'Phase 2 Mobile Tomato' },
    seeded_state: { weather_alerts: [{
      alert_type: weatherBefore.alert_type, created_at_ms: weatherBefore.created_at_ms,
      dismissed: false, garden_id: 1, id: 1, metadata: weatherBefore.metadata,
      plant_ids: weatherBefore.plant_ids, severity: weatherBefore.severity,
      title: weatherBefore.title, valid_from: weatherBefore.valid_from,
      valid_until: weatherBefore.valid_until,
    }] },
    task_ids: { stale_generated_water: before.public_id },
  },
};
assertExpectedMaintenanceMutations(evidence, fixture);
try {
  assertExpectedMaintenanceMutations({
    ...evidence,
    tasks: { mutated_existing: [{ before, after: { ...after, title: 'unexpected' } }] },
  }, fixture);
  process.exit(3);
} catch (error) {
  if (!/unexpected stale generated task fields/i.test(String(error.message))) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_mutation_audit_requires_exact_actor_auth_garden_and_count() -> None:
    script = """
const { assertPhaseTwoAuditEvents } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = {
  clock: { attention_now_ms: 1783857600000 },
  roles: { admin: 'admin-user', editor: 'editor-user', viewer: 'viewer-user' },
};
const profile = {
  profile: 'desktop', role: 'admin', requests: [
    {
      actorAuthType: 'none', actorRole: 'anonymous', actorUsername: 'anonymous',
      gardenId: null, method: 'POST', path: '/api/auth/login',
      requestId: 'admin-login-request-1', statusCode: 200,
    },
    {
      actorAuthType: 'session', actorRole: 'admin', actorUsername: 'admin-user',
      gardenId: 7, method: 'POST', path: '/api/tasks/task-1/action',
      requestId: 'admin-task-action-1', statusCode: 200,
    },
  ],
};
const records = [
  {
    actor_auth_type: 'none', actor_role: 'anonymous', actor_username: 'anonymous',
    garden_id: null, id: 1, method: 'POST', occurred_at_ms: 1783857600001,
    path: '/api/auth/login', request_id: 'admin-login-request-1', status_code: 200,
  },
  {
    actor_auth_type: 'session', actor_role: 'admin', actor_username: 'admin-user',
    garden_id: 7, id: 2, method: 'POST', occurred_at_ms: 1783857600002,
    path: '/api/tasks/task-1/action', request_id: 'admin-task-action-1', status_code: 200,
  },
];
assertPhaseTwoAuditEvents({ records: [] }, { records }, [profile], fixture);
for (const changed of [
  [...records, { ...records[1], id: 3 }],
  [{ ...records[0] }, { ...records[1], actor_username: 'viewer-user' }],
  [{ ...records[0] }, { ...records[1], garden_id: null }],
  [{ ...records[0] }, { ...records[1], garden_id: 8 }],
]) {
  try {
    assertPhaseTwoAuditEvents({ records: [] }, { records: changed }, [profile], fixture);
    process.exit(3);
  } catch (error) {
    if (!/exact browser mutation/i.test(String(error.message))) process.exit(4);
  }
}
try {
  assertPhaseTwoAuditEvents({ records: [] }, { records: [records[0]] }, [profile], fixture);
  process.exit(5);
} catch (error) {
  if (!/lacked exactly one audit event/i.test(String(error.message))) process.exit(6);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_offline_operation_ids_match_queue_replay_and_database() -> None:
    script = """
const {
  assertPhaseTwoOfflineOperationReplay,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = {
  phase_two: {
    preference_delivery: { eligible: { public_id: 'n1' }, ineligible: { public_id: 'n2' } },
    task_ids: {
      editor_offline: 'task-complete', fertilize_grouped: 'task-reschedule',
      prune_desktop: 'task-skip', stale_manual_water: 'task-snooze',
    },
  },
};
const ids = {
  'task-complete': '11111111-1111-4111-8111-111111111111',
  'task-reschedule': '22222222-2222-4222-8222-222222222222',
  'task-skip': '33333333-3333-4333-8333-333333333333',
  'task-snooze': '44444444-4444-4444-8444-444444444444',
};
const queued_operations = [
  ['task-complete', 'task_complete'], ['task-reschedule', 'task_reschedule'],
  ['task-skip', 'task_skip'], ['task-snooze', 'task_snooze'],
].map(([task_id, type]) => ({ operation_id: ids[task_id], task_id, type }));
const replayed_operations = [
  ['task-complete', 'complete'], ['task-reschedule', 'reschedule'],
  ['task-skip', 'skip'], ['task-snooze', 'snooze'],
].map(([task_id, action]) => ({ action, operation_id: ids[task_id], task_id }));
const profiles = [{
  profile: 'mobile', role: 'editor', checks: {
    offline_task_operation_ids: {
      queued_operations, remaining_operations: [], replayed_operations,
    },
  },
}];
const state = {
  offline_operations: queued_operations.map((item) => ({
    operation_id: item.operation_id, target_id: item.task_id,
  })),
};
assertPhaseTwoOfflineOperationReplay(profiles, state, fixture);
try {
  const changed = structuredClone(state);
  changed.offline_operations[0].operation_id = ids['task-skip'];
  assertPhaseTwoOfflineOperationReplay(profiles, changed, fixture);
  process.exit(3);
} catch (error) {
  if (!/durable offline operation ID/i.test(String(error.message))) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_token_shaped_private_diagnostics_are_redacted_before_write() -> None:
    script = """
const { redactTokenShapedSecrets } = require('./scripts/e2e/completeJourneyBrowser.cjs');
const sampleA = ['Authorization:', 'Bearer', 'opaque-token-value-0123456789'].join(' ');
const sampleB = ['access', ['to', 'ken=access-token-value-0123456789'].join('')].join('_');
const sampleC = ['sk', 'proj', ['to', 'ken-value-0123456789'].join('')].join('-');
const sampleD = ['postgresql:', '', 'user:db-password-value@host', 'database'].join('/');
const value = redactTokenShapedSecrets([
  sampleA,
  sampleB,
  sampleC,
  sampleD,
  'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature-value-0123456789',
].join(' '));
for (const secret of [
  'opaque-token-value-0123456789',
  'access-token-value-0123456789',
  sampleC,
  'db-password-value', 'eyJhbGciOiJIUzI1NiJ9', 'signature-value-0123456789',
]) {
  if (value.includes(secret)) process.exit(3);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_scoped_mutable_projection_rejects_extra_allowed_table_rows() -> None:
    script = """
const { assertPhaseTwoScopedMutableRows } = require('./scripts/check_complete_journeys_e2e.cjs');
const row = (row_id, public_id) => ({ row_id, public_id });
const semantic = {
  rows_before: {
    tasks: [row(1, 'task-seeded')],
    notifications: [row(2, 'note-seeded')],
    weather_alerts: [row(3, 'weather-seeded')],
  },
  rows_after: {
    tasks: [row(1, 'task-seeded'), row(4, 'task-generated')],
    notifications: [row(2, 'note-seeded')],
    weather_alerts: [row(3, 'weather-seeded')],
  },
};
const finalRows = {
  tasks: structuredClone(semantic.rows_after.tasks),
  notifications: [
    row(2, 'note-seeded'),
    row(5, 'note-delivery-eligible'),
    row(6, 'note-delivery-ineligible'),
  ],
  weather_alerts: structuredClone(semantic.rows_after.weather_alerts),
};
const fixture = { phase_two: { preference_delivery: {
  eligible: { public_id: 'note-delivery-eligible' },
  ineligible: { public_id: 'note-delivery-ineligible' },
} } };
assertPhaseTwoScopedMutableRows(semantic, finalRows, fixture);
try {
  const changed = structuredClone(finalRows);
  changed.tasks.push(row(7, 'task-extra'));
  assertPhaseTwoScopedMutableRows(semantic, changed, fixture);
  process.exit(3);
} catch (error) {
  if (!/extra or missing mutable row/i.test(String(error.message))) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_rejects_unknown_successful_browser_mutations_before_audit_filtering() -> None:
    script = r"""
const { phaseTwoBrowserMutationRecords } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = { roles: { admin: 'admin' } };
const profiles = [{
  profile: 'desktop', role: 'admin', requests: [{
    actorAuthType: 'session', actorRole: 'admin', actorUsername: 'admin', gardenId: 7,
    method: 'POST', path: '/api/journal', requestId: 'unexpected-journal-post-1', statusCode: 201,
  }],
}];
try {
  phaseTwoBrowserMutationRecords(profiles, fixture);
  process.exit(3);
} catch (error) {
  const expected = /Unknown successful Phase 2 browser mutation path.*POST \/api\/journal/;
  if (!expected.test(String(error.message))) {
    process.exit(4);
  }
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_phase_two_audit_correlation_rejects_request_id_tampering_from_peer_pages() -> None:
    script = """
const { assertPhaseTwoAuditEvents } = require('./scripts/check_complete_journeys_e2e.cjs');
const fixture = { clock: { attention_now_ms: 1783857600000 }, roles: { admin: 'admin' } };
const request = (requestId) => ({
  actorAuthType: 'session', actorRole: 'admin', actorUsername: 'admin', gardenId: 7,
  method: 'POST', path: '/api/weather/check', requestId, statusCode: 200,
});
const event = (id, request_id) => ({
  actor_auth_type: 'session', actor_role: 'admin', actor_username: 'admin', garden_id: 7,
  id, method: 'POST', occurred_at_ms: 1783857600000, path: '/api/weather/check',
  request_id, status_code: 200,
});
const profiles = [
  { profile: 'desktop', role: 'admin', requests: [request('desktop-weather-request-1')] },
  { profile: 'desktop-peer', role: 'admin', requests: [request('peer-weather-request-1')] },
];
assertPhaseTwoAuditEvents(
  { records: [] },
  { records: [event(1, 'desktop-weather-request-1'), event(2, 'peer-weather-request-1')] },
  profiles,
  fixture,
);
try {
  assertPhaseTwoAuditEvents(
    { records: [] },
    { records: [event(1, 'desktop-weather-request-1'), event(2, 'desktop-weather-request-1')] },
    profiles,
    fixture,
  );
  process.exit(3);
} catch (error) {
  if (!/exact browser mutation/.test(String(error.message))) process.exit(4);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_browser_api_recorder_persists_response_request_ids_for_primary_and_peer_pages() -> None:
    script = """
const { createApiRecorder } = require('./scripts/e2e/completeJourneyBrowser.cjs');
const page = () => {
  const handlers = new Map();
  return {
    emit(event, value) { for (const handler of handlers.get(event) || []) handler(value); },
    on(event, handler) { handlers.set(event, [...(handlers.get(event) || []), handler]); },
  };
};
const request = (path) => ({
  headers: () => ({ 'x-garden-id': '7' }), method: () => 'POST', url: () => `http://127.0.0.1${path}`,
});
const response = (value, requestId) => ({
  headers: () => ({ 'x-request-id': requestId }), request: () => value, status: () => 200,
});
const primary = page();
const peer = page();
const recorder = createApiRecorder(primary, {
  authType: 'session', role: 'admin', username: 'admin',
});
recorder.attachPage(peer);
const primaryRequest = request('/api/weather/check');
const peerRequest = request('/api/weather/check');
primary.emit('request', primaryRequest);
peer.emit('request', peerRequest);
primary.emit('response', response(primaryRequest, 'primary-request-id-1'));
peer.emit('response', response(peerRequest, 'peer-request-id-1'));
if (recorder.records.length !== 2) process.exit(3);
if (recorder.records[0].requestId !== 'primary-request-id-1') process.exit(4);
if (recorder.records[1].requestId !== 'peer-request-id-1') process.exit(5);
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_maintenance_notification_reconciliation_rejects_lifecycle_field_tampering() -> None:
    script = """
const { exactMaintenanceNotification } = require('./scripts/check_complete_journeys_e2e.cjs');
const expected = {
  body: 'body', cleared_at_ms: 4, clear_reason: 'expired', created_at_ms: 1, dismissed: true,
  emailed_at_ms: 3, expires_at_ms: null, garden_id: 7, metadata: {}, notification_subtype: '',
  notification_type: 'task_due', public_id: 'note-1', read_at_ms: 2, row_id: 1, severity: 'normal',
  target_id: 'task-1', target_type: 'task', title: 'title', username: 'admin',
};
exactMaintenanceNotification({ ...expected }, expected);
for (const field of ['dismissed', 'read_at_ms', 'emailed_at_ms', 'cleared_at_ms', 'clear_reason']) {
  const actual = { ...expected, [field]: field === 'dismissed' ? false : 'tampered' };
  try {
    exactMaintenanceNotification(actual, expected);
    process.exit(3);
  } catch (error) {
    if (!String(error.message).includes(field)) process.exit(4);
  }
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_public_manifest_binds_fixture_runtime_lockfiles_and_recomputable_digests() -> None:
    script = """
const {
  auditManifestProjection,
  canonicalProjectionDigests,
  sanitizeManifestEvidence,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const binding = {
  fixture: { sha256: 'a'.repeat(64), size_bytes: 11 },
  lockfiles: {
    frontend_package_lock: { format_version: 3, sha256: 'b'.repeat(64), size_bytes: 12 },
    uv_lock: { format_version: '1', sha256: 'c'.repeat(64), size_bytes: 13 },
  },
  runtime: {
    chromium_launcher: { sha256: 'e'.repeat(64), size_bytes: 15 },
    architecture: 'x64', chromium_executable: {
      resolved_regular_file: true, sha256: 'd'.repeat(64), size_bytes: 14,
    },
    chromium_version: '140.0.0.0', frontend_package_version: '0.1.1', node_version: 'v24.0.0',
    platform: 'linux', playwright_core_version: '1.61.0',
  },
};
const auditState = {
  events: [{ count: 1, method: 'POST', path: '/api/auth/login', status_code: 200 }],
  expected_login_count: 1, expected_phase_one_snapshot_count: 0, total_count: 1,
};
const manifest = sanitizeManifestEvidence({
  evidence_binding: binding, profiles: [], database: {
    audit_projection: auditManifestProjection(auditState),
    safe: true,
  },
});
if (!manifest.evidence_binding
    || manifest.evidence_binding.fixture.sha256 !== binding.fixture.sha256) process.exit(3);
if (JSON.stringify(manifest.canonical_projection_digests)
    !== JSON.stringify(canonicalProjectionDigests(manifest))) process.exit(4);
if (!manifest.evidence_binding.runtime.chromium_executable.resolved_regular_file) process.exit(6);
if (manifest.evidence_binding.runtime.chromium_launcher.sha256 !== 'e'.repeat(64)) process.exit(9);
if (!/^[a-f0-9]{64}$/.test(manifest.canonical_projection_digests.audit_snapshot)) process.exit(7);
const tampered = structuredClone(manifest);
tampered.database.safe = false;
if (tampered.canonical_projection_digests.final_database
    === canonicalProjectionDigests(tampered).final_database) process.exit(5);
const auditTampered = structuredClone(manifest);
auditTampered.database.audit_projection.total_count = 2;
if (auditTampered.canonical_projection_digests.audit_snapshot
    === canonicalProjectionDigests(auditTampered).audit_snapshot) process.exit(8);
try {
  auditManifestProjection({ ...auditState, total_count: 2 });
  process.exit(12);
} catch (error) {
  if (!String(error.message).includes('event histogram')) process.exit(13);
}
try {
  sanitizeManifestEvidence({ database: null, profiles: [], status: 'passed' });
  process.exit(10);
} catch (error) {
  if (!String(error.message).includes('sanitized audit projection')) process.exit(11);
}
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr


def test_chromium_evidence_resolves_and_hashes_the_elf_payload() -> None:
    script = """
const {
  isElfExecutable,
  resolveChromiumExecutable,
} = require('./scripts/check_complete_journeys_e2e.cjs');
const resolved = resolveChromiumExecutable('/usr/bin/chromium');
if (resolved === '/usr/bin/chromium') process.exit(3);
if (!isElfExecutable(resolved)) process.exit(4);
const source = require('node:fs').readFileSync('./scripts/check_complete_journeys_e2e.cjs', 'utf8');
if (!source.includes('executablePath: CHROMIUM_EXECUTABLE')) process.exit(5);
if (!source.includes('chromium_launcher: fileBinding')) process.exit(6);
if (!source.includes('chromium_executable: resolvedExecutableBinding')) process.exit(7);
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, check=False, text=True
    )
    assert result.returncode == 0, result.stderr
