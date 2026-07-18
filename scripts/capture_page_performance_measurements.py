#!/usr/bin/env python3
"""Convert validated page-performance JSON into budget-checker evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.check_performance_budgets import (
    DEFAULT_BUDGETS,
    MEASUREMENT_GENERATOR,
    load_budgets,
)

PAGE_PERFORMANCE_SCHEMA_VERSION = 6
ROOT = Path(__file__).resolve().parents[1]
PRIVATE_RESEARCH_ROOT = ROOT / "research"


class CaptureError(ValueError):
    pass


def _require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaptureError(f"{location} must be a mapping")
    return value


def _require_text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureError(f"{location} must be a non-empty string")
    return value


def _require_number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CaptureError(f"{location} must be a finite number")
    if value < 0:
        raise CaptureError(f"{location} must be non-negative")
    return float(value)


def _load_page_result(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"page-performance result cannot be read: {path}") from exc
    return _require_mapping(payload, "page-performance result"), raw


def _require_private_output_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_relative_to(Path("/tmp")):
        return resolved
    if not resolved.is_relative_to(PRIVATE_RESEARCH_ROOT.resolve()):
        raise CaptureError("output must be under /tmp or the ignored research directory")
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", str(resolved)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        timeout=5,
    )
    if ignored.returncode != 0:
        raise CaptureError("output under research must be ignored by Git")
    return resolved


def _validate_page_result(result: dict[str, Any]) -> None:
    measurement = _require_mapping(result.get("measurement"), "page-performance measurement")
    if measurement.get("schemaVersion") != PAGE_PERFORMANCE_SCHEMA_VERSION:
        raise CaptureError(
            f"page-performance measurement schemaVersion must be {PAGE_PERFORMANCE_SCHEMA_VERSION}"
        )
    provenance = _require_mapping(result.get("provenance"), "page-performance provenance")
    git = _require_mapping(provenance.get("git"), "page-performance provenance.git")
    _require_text(git.get("revision"), "page-performance provenance.git.revision")
    if git.get("dirty") is not False:
        raise CaptureError("page-performance result must come from a clean Git revision")
    _require_text(result.get("createdAt"), "page-performance result.createdAt")
    try:
        recorded_at = datetime.fromisoformat(str(result["createdAt"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError("page-performance result.createdAt must be ISO-8601") from exc
    if recorded_at.tzinfo is None:
        raise CaptureError("page-performance result.createdAt must include a timezone")
    runs = result.get("runs")
    if not isinstance(runs, list) or not runs:
        raise CaptureError("page-performance result.runs must be a non-empty list")
    for index, run in enumerate(runs):
        run_mapping = _require_mapping(run, f"page-performance run {index}")
        if run_mapping.get("consoleErrors") != [] or run_mapping.get("pageErrors") != []:
            raise CaptureError(f"page-performance run {index} has browser errors")
        _require_mapping(run_mapping.get("timings"), f"page-performance run {index}.timings")


def _result_source(result: dict[str, Any]) -> dict[str, str]:
    provenance = _require_mapping(result["provenance"], "page-performance provenance")
    comparison = _require_mapping(
        provenance.get("comparison"), "page-performance provenance.comparison"
    )
    options = _require_mapping(
        comparison.get("options"), "page-performance provenance.comparison.options"
    )
    return {
        "api_mode": _require_text(
            comparison.get("apiMode"), "page-performance provenance.comparison.apiMode"
        ),
        "device_profile": _require_text(
            options.get("deviceProfile"),
            "page-performance provenance.comparison.options.deviceProfile",
        ),
        "scenario": _require_text(
            comparison.get("scenario"), "page-performance provenance.comparison.scenario"
        ),
    }


def capture_measurements(
    result: dict[str, Any], budgets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    _validate_page_result(result)
    actual_source = _result_source(result)
    runs = result["runs"]
    measurements: list[dict[str, Any]] = []
    for budget in budgets:
        source = _require_mapping(budget["source"], f"{budget['name']}.source")
        expected_source = {
            "api_mode": source["api_mode"],
            "device_profile": source["device_profile"],
            "scenario": source["scenario"],
        }
        if expected_source != actual_source:
            raise CaptureError(
                f"{budget['name']} source does not match result: "
                f"expected {expected_source!r}, got {actual_source!r}"
            )
        samples = [
            _require_number(
                _require_mapping(run, f"page-performance run {index}")["timings"].get(
                    budget["metric"]["name"]
                ),
                f"page-performance run {index}.timings.{budget['metric']['name']}",
            )
            for index, run in enumerate(runs)
        ]
        measurements.append(
            {
                "budget": budget["name"],
                "journey": budget["journey"],
                "fixture": budget["fixture"],
                "profile": budget["profile"],
                "metric": budget["metric"]["name"],
                "unit": budget["metric"]["unit"],
                "samples": samples,
            }
        )
    return measurements


def _select_budgets(
    budgets: list[dict[str, Any]], selected_names: list[str] | None
) -> list[dict[str, Any]]:
    if not selected_names:
        return budgets
    if len(selected_names) != len(set(selected_names)):
        raise CaptureError("budget selectors must not repeat")
    by_name = {str(budget["name"]): budget for budget in budgets}
    missing = sorted(set(selected_names) - set(by_name))
    if missing:
        raise CaptureError(f"unknown budget selector: {', '.join(missing)}")
    return [by_name[name] for name in selected_names]


def build_document(
    results: list[tuple[dict[str, Any], bytes]], budgets: list[dict[str, Any]]
) -> dict[str, Any]:
    if not results:
        raise CaptureError("at least one page-performance result is required")
    captured: list[dict[str, Any]] = []
    revisions: set[str] = set()
    recorded_at: list[str] = []
    digest = hashlib.sha256()
    remaining = {str(budget["name"]): budget for budget in budgets}
    for result, raw_result in results:
        _validate_page_result(result)
        provenance = _require_mapping(result["provenance"], "page-performance provenance")
        git = _require_mapping(provenance["git"], "page-performance provenance.git")
        revisions.add(
            _require_text(git.get("revision"), "page-performance provenance.git.revision")
        )
        recorded_at.append(
            _require_text(result.get("createdAt"), "page-performance result.createdAt")
        )
        digest.update(raw_result)
        digest.update(b"\\0")
        actual_source = _result_source(result)
        matching = [
            budget
            for budget in remaining.values()
            if {
                "api_mode": budget["source"]["api_mode"],
                "device_profile": budget["source"]["device_profile"],
                "scenario": budget["source"]["scenario"],
            }
            == actual_source
        ]
        if not matching:
            raise CaptureError(
                f"page-performance result source has no selected budget: {actual_source!r}"
            )
        captured.extend(capture_measurements(result, matching))
        for budget in matching:
            del remaining[str(budget["name"])]
    if remaining:
        raise CaptureError(
            "selected budgets have no matching page-performance result: "
            + ", ".join(sorted(remaining))
        )
    if len(revisions) != 1:
        raise CaptureError("page-performance results must share one Git revision")
    return {
        "version": 1,
        "provenance": {
            "generator": MEASUREMENT_GENERATOR,
            "git_dirty": False,
            "git_revision": revisions.pop(),
            "recorded_at": max(recorded_at),
            "run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, digest.hexdigest())),
        },
        "measurements": captured,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture page-performance runs as immutable budget-checker evidence."
    )
    parser.add_argument("results", type=Path, nargs="+", help="page-performance JSON result(s)")
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS)
    parser.add_argument("--budget", dest="budget_names", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output_path = _require_private_output_path(args.output)
        if output_path.exists():
            raise CaptureError(f"refusing to overwrite existing output: {output_path}")
        budgets = _select_budgets(load_budgets(args.budgets), args.budget_names)
        results = [_load_page_result(path) for path in args.results]
        document = build_document(results, budgets)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    except (CaptureError, OSError, ValueError) as exc:
        print(f"Performance measurement capture failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
