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
        "evidence": evidence or [],
        "notes": {},
    }


def _write_manifest(path: Path, journeys: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "journeys": journeys}), encoding="utf-8")


def _complete_fixture() -> list[dict[str, object]]:
    return [_journey(journey_id) for journey_id in sorted(EXPECTED_JOURNEY_IDS)]


def test_repository_journey_manifest_is_valid_with_open_dimensions() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    assert {journey["id"] for journey in payload["journeys"]} == EXPECTED_JOURNEY_IDS


def test_phase_one_manifest_only_marks_enforced_dimensions_proven() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    journeys = {journey["id"]: journey for journey in payload["journeys"]}
    expected_proven_dimensions = {
        "A3": {"desktop", "mobile", "roles", "database"},
        "CROSS-01": {"desktop", "mobile", "roles", "database"},
        "M1": {"desktop", "mobile", "roles", "database"},
        "M2": {"desktop", "mobile", "roles", "database"},
        "M3": {"desktop", "mobile", "roles", "database"},
        "M4": {"desktop", "mobile", "roles", "database", "filesystem"},
    }
    for journey_id, expected in expected_proven_dimensions.items():
        journey = journeys[journey_id]
        actual = {dimension for dimension in DIMENSIONS if journey[dimension] == "proven"}
        assert actual == expected
        assert "scripts/check_complete_journeys_e2e.cjs" in journey["evidence"]
        assert "scripts/e2e/journeys/foundation.cjs" in journey["evidence"]
        assert "tests/test_complete_journey_e2e_scripts.py" in journey["evidence"]
        assert journey["accessibility"] == "required"
        assert journey["performance"] == "required"


def test_phase_two_manifest_only_marks_enforced_dimensions_proven() -> None:
    payload = validate_manifest(DEFAULT_MANIFEST, repo_root=ROOT)
    journeys = {journey["id"]: journey for journey in payload["journeys"]}
    expected_proven_dimensions = {
        "D1": {"desktop", "mobile", "provider"},
        "D2": {"desktop", "mobile", "roles", "offline"},
        "D3": {"desktop", "mobile", "roles", "filesystem"},
        "D4": {"desktop", "mobile", "roles"},
        "D5": {"desktop", "mobile", "roles"},
        "R1": {"desktop", "mobile"},
    }
    for journey_id, expected in expected_proven_dimensions.items():
        journey = journeys[journey_id]
        actual = {dimension for dimension in DIMENSIONS if journey[dimension] == "proven"}
        assert actual == expected
        assert "scripts/check_complete_journeys_e2e.cjs" in journey["evidence"]
        assert "scripts/e2e/journeys/dailyAttentionWork.cjs" in journey["evidence"]
        assert "tests/test_complete_journey_e2e_scripts.py" in journey["evidence"]
        assert journey["accessibility"] == "required"
        assert journey["performance"] == "required"
    assert journeys["D2"]["offline"] == "proven"
    assert journeys["D4"]["provider"] == "required"


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
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="requires notes.filesystem"):
        validate_manifest(manifest, repo_root=ROOT)


def test_nonexistent_evidence_path_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "proven"
    journeys[0]["evidence"] = ["tests/does-not-exist.py"]
    manifest = tmp_path / "coverage.yaml"
    _write_manifest(manifest, journeys)

    with pytest.raises(CoverageManifestError, match="evidence path does not exist"):
        validate_manifest(manifest, repo_root=ROOT)


def test_ignored_research_evidence_path_is_rejected(tmp_path: Path) -> None:
    journeys = _complete_fixture()
    journeys[0]["desktop"] = "proven"
    journeys[0]["evidence"] = ["research/optimization-map/README.md"]
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
        journeys[0]["evidence"] = [untracked.name]
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
        journeys[0]["evidence"] = [symlink.name]
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
