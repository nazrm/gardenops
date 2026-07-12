#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "journey_coverage.yaml"

EXPECTED_JOURNEY_IDS = frozenset(
    {
        "A1",
        "A2",
        "A3",
        "A4",
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "CROSS-01",
        "CROSS-02",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "I1",
        "I2",
        "I3",
        "I4",
        "INT-01",
        "L1",
        "L2",
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "OFF-01",
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "R1",
        "R2",
        "R3",
    }
)
EXPECTED_PHASES = {
    "A1": 5,
    "A2": 5,
    "A3": 1,
    "A4": 5,
    "C1": 5,
    "C2": 6,
    "C3": 5,
    "C4": 7,
    "C5": 5,
    "C6": 7,
    "CROSS-01": 1,
    "CROSS-02": 5,
    "D1": 2,
    "D2": 2,
    "D3": 2,
    "D4": 2,
    "D5": 2,
    "I1": 4,
    "I2": 3,
    "I3": 3,
    "I4": 7,
    "INT-01": 6,
    "L1": 4,
    "L2": 4,
    "M1": 1,
    "M2": 1,
    "M3": 1,
    "M4": 1,
    "M5": 7,
    "OFF-01": 6,
    "P1": 3,
    "P2": 3,
    "P3": 3,
    "P4": 4,
    "P5": 3,
    "P6": 4,
    "R1": 2,
    "R2": 4,
    "R3": 4,
}

DIMENSIONS = (
    "desktop",
    "mobile",
    "roles",
    "offline",
    "provider",
    "database",
    "filesystem",
    "accessibility",
    "performance",
)
ALLOWED_STATES = frozenset({"required", "proven", "not_applicable"})
REQUIRED_FIELDS = frozenset({"id", "phase", *DIMENSIONS, "evidence", "notes"})


class CoverageManifestError(ValueError):
    pass


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CoverageManifestError(f"manifest does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise CoverageManifestError(f"manifest is invalid YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise CoverageManifestError("manifest root must be a mapping")
    return payload


def _validate_evidence_path(raw_path: str, *, repo_root: Path, journey_id: str) -> None:
    evidence_path = Path(raw_path)
    if evidence_path.is_absolute() or ".." in evidence_path.parts:
        raise CoverageManifestError(
            f"{journey_id}: evidence path must be repository-relative without traversal: {raw_path}"
        )
    if evidence_path.parts and evidence_path.parts[0] == "research":
        raise CoverageManifestError(
            f"{journey_id}: durable evidence cannot point into ignored research/: {raw_path}"
        )
    repository_path = repo_root / evidence_path
    if repository_path.is_symlink():
        raise CoverageManifestError(
            f"{journey_id}: durable evidence path must not be a symlink: {raw_path}"
        )
    resolved = repository_path.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise CoverageManifestError(
            f"{journey_id}: evidence path escapes repository: {raw_path}"
        ) from exc
    if not resolved.is_file():
        raise CoverageManifestError(f"{journey_id}: evidence path does not exist: {raw_path}")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", raw_path],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )
    if tracked.returncode != 0:
        raise CoverageManifestError(f"{journey_id}: evidence path is not tracked: {raw_path}")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", raw_path],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )
    if ignored.returncode == 0:
        raise CoverageManifestError(f"{journey_id}: evidence path is ignored: {raw_path}")


def validate_manifest(
    path: Path = DEFAULT_MANIFEST,
    *,
    repo_root: Path = ROOT,
    require_closed: bool = False,
) -> dict[str, Any]:
    payload = _load_manifest(path)
    unknown_root_fields = set(payload) - {"version", "journeys"}
    if unknown_root_fields:
        raise CoverageManifestError(
            f"manifest has unknown root fields: {', '.join(sorted(unknown_root_fields))}"
        )
    if payload.get("version") != 1:
        raise CoverageManifestError("manifest version must be 1")
    journeys = payload.get("journeys")
    if not isinstance(journeys, list):
        raise CoverageManifestError("manifest journeys must be a list")

    seen_ids: set[str] = set()
    open_dimensions: list[str] = []
    for index, journey in enumerate(journeys):
        if not isinstance(journey, dict):
            raise CoverageManifestError(f"journey at index {index} must be a mapping")
        fields = set(journey)
        missing_fields = REQUIRED_FIELDS - fields
        unknown_fields = fields - REQUIRED_FIELDS
        if missing_fields:
            raise CoverageManifestError(
                f"journey at index {index} is missing fields: {', '.join(sorted(missing_fields))}"
            )
        if unknown_fields:
            raise CoverageManifestError(
                f"journey at index {index} has unknown fields: {', '.join(sorted(unknown_fields))}"
            )

        journey_id = journey["id"]
        if not isinstance(journey_id, str) or not journey_id:
            raise CoverageManifestError(f"journey at index {index} has invalid id")
        if journey_id in seen_ids:
            raise CoverageManifestError(f"duplicate journey id: {journey_id}")
        seen_ids.add(journey_id)

        phase = journey["phase"]
        if not isinstance(phase, int) or isinstance(phase, bool) or not 0 <= phase <= 9:
            raise CoverageManifestError(f"{journey_id}: phase must be an integer from 0 to 9")
        expected_phase = EXPECTED_PHASES.get(journey_id)
        if expected_phase is not None and phase != expected_phase:
            raise CoverageManifestError(
                f"{journey_id}: phase must remain {expected_phase}, received {phase}"
            )

        notes = journey["notes"]
        if not isinstance(notes, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and value.strip()
            for key, value in notes.items()
        ):
            raise CoverageManifestError(f"{journey_id}: notes must be a string-to-string mapping")

        has_proven_dimension = False
        for dimension in DIMENSIONS:
            state = journey[dimension]
            if state not in ALLOWED_STATES:
                raise CoverageManifestError(
                    f"{journey_id}: {dimension} has invalid state {state!r}"
                )
            if state == "required":
                open_dimensions.append(f"{journey_id}.{dimension}")
            elif state == "proven":
                has_proven_dimension = True
            elif dimension not in notes:
                raise CoverageManifestError(
                    f"{journey_id}: {dimension}=not_applicable requires notes.{dimension}"
                )

        evidence = journey["evidence"]
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise CoverageManifestError(f"{journey_id}: evidence must be a list of paths")
        if has_proven_dimension and not evidence:
            raise CoverageManifestError(
                f"{journey_id}: proven dimensions require durable evidence paths"
            )
        for evidence_path in evidence:
            _validate_evidence_path(evidence_path, repo_root=repo_root, journey_id=journey_id)

    missing_ids = EXPECTED_JOURNEY_IDS - seen_ids
    unknown_ids = seen_ids - EXPECTED_JOURNEY_IDS
    if missing_ids:
        missing_list = ", ".join(sorted(missing_ids))
        raise CoverageManifestError(f"manifest is missing journey ids: {missing_list}")
    if unknown_ids:
        unknown_list = ", ".join(sorted(unknown_ids))
        raise CoverageManifestError(f"manifest has unknown journey ids: {unknown_list}")
    if require_closed and open_dimensions:
        raise CoverageManifestError(
            "manifest has open required dimensions: " + ", ".join(sorted(open_dimensions))
        )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate GardenOps journey coverage evidence")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--allow-open", action="store_true")
    mode.add_argument("--require-closed", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = validate_manifest(
            args.manifest,
            repo_root=ROOT,
            require_closed=args.require_closed,
        )
    except CoverageManifestError as exc:
        print(f"Journey coverage check failed: {exc}", file=sys.stderr)
        return 1
    print(f"Journey coverage check passed for {len(payload['journeys'])} journeys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
