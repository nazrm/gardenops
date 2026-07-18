from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.check_journey_coverage import (
    DEFAULT_MANIFEST,
    DIMENSIONS,
    EXPECTED_JOURNEY_IDS,
    EXPECTED_PHASES,
    CoverageManifestError,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _journey(journey_id: str, *, evidence: list[str] | None = None) -> dict[str, object]:
    evidence_paths = evidence or []
    return {
        "id": journey_id,
        "phase": EXPECTED_PHASES[journey_id],
        "desktop": "required",
        "mobile": "required",
        "roles": "required",
        "offline": "required",
        "provider": "required",
        "database": "required",
        "filesystem": "required",
        "accessibility": "required",
        "performance": "required",
        "evidence": {"desktop": evidence_paths} if evidence_paths else {},
        "notes": {
            dimension: "Direct evidence has not yet established this dimension."
            for dimension in DIMENSIONS
        },
    }


def _write_manifest(path: Path, journeys: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump({"version": 2, "journeys": journeys}), encoding="utf-8")


def _complete_fixture() -> list[dict[str, object]]:
    return [_journey(journey_id) for journey_id in sorted(EXPECTED_JOURNEY_IDS)]


def test_repository_journey_manifest_is_valid_with_open_dimensions() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    assert {journey["id"] for journey in payload["journeys"]} == EXPECTED_JOURNEY_IDS


def test_phase_one_manifest_only_marks_enforced_dimensions_proven() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    journeys = {journey["id"]: journey for journey in payload["journeys"]}
    expected_proven_dimensions = {
        "A3": {"desktop", "mobile", "roles", "database", "performance"},
        "CROSS-01": {"desktop", "mobile", "roles", "database", "performance"},
        "M1": {"desktop", "mobile", "roles", "database", "performance"},
        "M2": {"desktop", "mobile", "roles", "database", "performance"},
        "M3": {"desktop", "mobile", "roles", "database", "performance"},
        "M4": {"desktop", "mobile", "roles", "database", "filesystem", "performance"},
    }
    for journey_id, expected in expected_proven_dimensions.items():
        journey = journeys[journey_id]
        actual = {dimension for dimension in DIMENSIONS if journey[dimension] == "proven"}
        assert actual == expected
        assert set(journey["evidence"]) == expected
        for dimension in expected:
            assert journey["evidence"][dimension]
        evidence_paths = {path for paths in journey["evidence"].values() for path in paths}
        assert "scripts/check_complete_journeys_e2e.cjs" in evidence_paths
        assert "scripts/e2e/journeys/foundation.cjs" in evidence_paths
        assert "tests/test_complete_journey_e2e_scripts.py" in evidence_paths
        assert journey["accessibility"] == "required"
        assert journey["performance"] == "proven"


def test_phase_two_manifest_only_marks_enforced_dimensions_proven() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    journeys = {journey["id"]: journey for journey in payload["journeys"]}
    expected_proven_dimensions = {
        "D1": {"desktop", "mobile", "roles", "provider", "database", "performance"},
        "D2": {"desktop", "mobile", "roles", "offline", "database", "performance"},
        "D3": {"desktop", "mobile", "roles", "filesystem", "database"},
        "D4": {"desktop", "mobile", "roles", "provider", "database", "performance"},
        "D5": {"desktop", "mobile", "roles", "provider", "database", "performance"},
        "R1": {"desktop", "mobile", "roles", "database"},
    }
    for journey_id, expected in expected_proven_dimensions.items():
        journey = journeys[journey_id]
        actual = {dimension for dimension in DIMENSIONS if journey[dimension] == "proven"}
        assert actual == expected
        assert set(journey["evidence"]) == expected
        for dimension in expected:
            assert journey["evidence"][dimension]
        evidence_paths = {path for paths in journey["evidence"].values() for path in paths}
        assert "scripts/check_complete_journeys_e2e.cjs" in evidence_paths
        assert "scripts/e2e/journeys/dailyAttentionWork.cjs" in evidence_paths
        assert "tests/test_complete_journey_e2e_scripts.py" in evidence_paths
        assert journey["accessibility"] == "required"
        assert journey["performance"] == (
            "proven" if journey_id in {"D1", "D2", "D4", "D5"} else "not_applicable"
        )
    assert journeys["D2"]["offline"] == "proven"
    assert journeys["D4"]["provider"] == "proven"
    assert "tests/test_notifications.py" in journeys["D4"]["evidence"]["provider"]


def test_phase_nine_manifest_maps_measured_and_shared_scale_surfaces_to_performance_proof() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    journeys = {journey["id"]: journey for journey in payload["journeys"]}
    performance_proven = {
        "A3",
        "CROSS-01",
        "D1",
        "D2",
        "D4",
        "D5",
        "L1",
        "L2",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "P1",
        "P2",
        "P4",
        "P6",
        "R2",
        "R3",
    }
    for journey_id in performance_proven:
        journey = journeys[journey_id]
        assert journey["performance"] == "proven"
        evidence_paths = journey["evidence"]["performance"]
        assert "scripts/check_complete_journeys_e2e.cjs" in evidence_paths
        assert "scripts/check_page_performance.cjs" in evidence_paths
        assert "tests/test_complete_journey_e2e_scripts.py" in evidence_paths
        assert "tests/test_page_performance_script.py" in evidence_paths
    for journey_id in {"D3", "R1"}:
        assert journeys[journey_id]["performance"] == "not_applicable"
    assert journeys["C6"]["desktop"] == "proven"
    assert journeys["C6"]["mobile"] == "proven"
    assert journeys["C6"]["roles"] == "proven"
    assert journeys["C6"]["database"] == "proven"
    assert journeys["C6"]["provider"] == "not_applicable"
    assert "scripts/e2e/journeys/providersAndTerrain.cjs" in journeys["C6"]["evidence"]["mobile"]
    c4 = journeys["C4"]
    assert {dimension for dimension in DIMENSIONS if c4[dimension] == "proven"} == {
        "desktop",
        "mobile",
        "roles",
        "provider",
        "database",
    }
    assert c4["performance"] == "not_applicable"
    assert "scripts/e2e/journeys/providersAndTerrain.cjs" in c4["evidence"]["roles"]
    assert journeys["I4"]["roles"] == "proven"
    assert "scripts/e2e/journeys/providersAndTerrain.cjs" in journeys["I4"]["evidence"]["roles"]
    assert journeys["P6"]["provider"] == "not_applicable"
    assert "external catalogue adapters" in journeys["P6"]["notes"]["provider"]


def test_duplicate_journey_id_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys.append(_journey("A1"))
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="duplicate journey id: A1"):
        validate_manifest(manifest, repo_root=ROOT)


def test_unknown_state_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "maybe"
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="invalid state"):
        validate_manifest(manifest, repo_root=ROOT)


def test_wrong_owning_phase_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    a1 = next(journey for journey in journeys if journey["id"] == "A1")
    a1["phase"] = 0
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="phase must remain 5"):
        validate_manifest(manifest, repo_root=ROOT)


def test_not_applicable_requires_dimension_reason(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["filesystem"] = "not_applicable"
    journeys[0]["notes"] = {
        dimension: note
        for dimension, note in journeys[0]["notes"].items()
        if dimension != "filesystem"
    }
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="requires notes.filesystem"):
        validate_manifest(manifest, repo_root=ROOT)


def test_nonexistent_evidence_path_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "proven"
    journeys[0]["evidence"] = {"desktop": ["tests/does-not-exist.py"]}
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="evidence path does not exist"):
        validate_manifest(manifest, repo_root=ROOT)


def test_ignored_research_evidence_path_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "proven"
    journeys[0]["evidence"] = {"desktop": ["research/optimization-map/README.md"]}
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="cannot point into ignored research"):
        validate_manifest(manifest, repo_root=ROOT)


def test_untracked_evidence_path_is_rejected(tmp_path: Path) -> None:
    untracked = ROOT / ".journey-coverage-untracked-test"
    untracked.write_text("temporary evidence", encoding="utf-8")
    try:
        journeys = _complete_fixture()
        journeys[0]["desktop"] = "proven"
        journeys[0]["evidence"] = {"desktop": [untracked.name]}
        manifest = tmp_path / "coverage.yaml"
        _write_manifest(manifest, journeys)

        with pytest.raises(CoverageManifestError, match="evidence path is not tracked"):
            validate_manifest(manifest, repo_root=ROOT)
    finally:
        untracked.unlink(missing_ok=True)


def test_symlink_evidence_path_is_rejected(tmp_path: Path) -> None:
    symlink = ROOT / ".journey-coverage-symlink-test"
    symlink.symlink_to(ROOT / ".gitignore")
    try:
        journeys = _complete_fixture()
        journeys[0]["desktop"] = "proven"
        journeys[0]["evidence"] = {"desktop": [symlink.name]}
        manifest = tmp_path / "coverage.yaml"
        _write_manifest(manifest, journeys)

        with pytest.raises(CoverageManifestError, match="must not be a symlink"):
            validate_manifest(manifest, repo_root=ROOT)
    finally:
        symlink.unlink(missing_ok=True)


def test_require_closed_rejects_open_dimensions(tmp_path: Path) -> None:
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, _complete_fixture())

    with pytest.raises(CoverageManifestError, match="open required dimensions"):
        validate_manifest(manifest, repo_root=ROOT, require_closed=True)


def test_proven_dimension_requires_its_own_evidence(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "proven"
    journeys[0]["mobile"] = "proven"
    journeys[0]["evidence"] = {"desktop": [".gitignore"]}
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="mobile=proven requires evidence.mobile"):
        validate_manifest(manifest, repo_root=ROOT)


def test_open_dimension_requires_a_closure_condition(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["notes"] = {}
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="desktop=required requires notes.desktop"):
        validate_manifest(manifest, repo_root=ROOT)


def test_not_applicable_rejects_evidence_and_deferral_reason(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["provider"] = "not_applicable"
    journeys[0]["notes"]["provider"] = "Phase 9 owns provider coverage."
    journeys[0]["evidence"] = {"provider": [".gitignore"]}
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="cannot have evidence.provider"):
        validate_manifest(manifest, repo_root=ROOT)

    journeys[0]["evidence"] = {}
    _write_manifest(manifest, journeys)
    with pytest.raises(CoverageManifestError, match="reason cannot defer closure"):
        validate_manifest(manifest, repo_root=ROOT)


def test_unknown_and_duplicate_dimension_evidence_are_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "proven"
    journeys[0]["evidence"] = {"unknown": [".gitignore"]}
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="evidence has unknown dimensions"):
        validate_manifest(manifest, repo_root=ROOT)

    journeys[0]["evidence"] = {"desktop": [".gitignore", ".gitignore"]}
    _write_manifest(manifest, journeys)
    with pytest.raises(CoverageManifestError, match="contains duplicate paths"):
        validate_manifest(manifest, repo_root=ROOT)
