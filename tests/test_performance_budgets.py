from __future__ import annotations

import copy
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.check_performance_budgets import (
    DEFAULT_BUDGETS,
    MEASUREMENT_GENERATOR,
    BudgetSchemaError,
    MeasurementError,
    evaluate,
    load_budgets,
    load_measurements,
    main,
)


def _budget(*, pending: bool = False) -> dict[str, object]:
    return {
        "name": "A1-app-ready",
        "journey": "A1",
        "fixture": "large-v1",
        "profile": "desktop-1440x900",
        "metric": {"name": "appReadyMs", "unit": "ms"},
        "baseline": {
            "status": "pending" if pending else "established",
            "median": None if pending else 100,
            "p75": None if pending else 110,
        },
        "limit": {"type": "regression_percent", "max_percent": 10},
        "sampling": {"minimum_samples": 7, "max_coefficient_of_variation": 0.15},
        "source": {
            "artifact": "page-performance-v6",
            "api_mode": "stub",
            "device_profile": "desktop",
            "scenario": "app-auth",
        },
        "rationale": "Keep app readiness stable.",
        "owner": "frontend-performance",
        "command": "measure-app-ready --runs 8",
    }


def _measurement(samples: list[float]) -> dict[str, object]:
    return {
        "budget": "A1-app-ready",
        "journey": "A1",
        "fixture": "large-v1",
        "profile": "desktop-1440x900",
        "metric": "appReadyMs",
        "unit": "ms",
        "samples": samples,
    }


def _write_budgets(path: Path, budgets: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "budgets": budgets}), encoding="utf-8")


def _write_measurements(path: Path, measurements: list[dict[str, object]]) -> None:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    ).stdout.strip()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "provenance": {
                    "generator": MEASUREMENT_GENERATOR,
                    "git_dirty": False,
                    "git_revision": revision,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "run_id": str(uuid.uuid4()),
                },
                "measurements": measurements,
            }
        ),
        encoding="utf-8",
    )


def test_tracked_budgets_are_established_and_require_measurements() -> None:
    budgets = load_budgets(DEFAULT_BUDGETS)

    expected_names = {
        "M1-large-desktop-app-ready",
        "M1-large-pixel-app-ready",
        "D1-large-desktop-tab-switch",
        "D1-large-pixel-tab-switch",
        *{
            f"{journey}-large-{profile}-focus"
            for journey in ("M3", "D1", "D2", "D4", "D5", "P1", "P2", "P4", "R2", "CROSS-01")
            for profile in ("desktop", "pixel")
        },
    }
    assert {budget["name"] for budget in budgets} == expected_names
    assert all(budget["baseline"]["status"] == "established" for budget in budgets)
    documented_variance_limits = {
        "M1-large-pixel-app-ready": 0.25,
        "D1-large-pixel-tab-switch": 0.30,
        "D4-large-desktop-focus": 0.25,
        "R2-large-desktop-focus": 0.25,
        "D4-large-pixel-focus": 0.25,
        "D5-large-pixel-focus": 0.50,
        "R2-large-pixel-focus": 0.25,
        "CROSS-01-large-pixel-focus": 0.20,
    }
    assert {
        budget["name"]: budget["sampling"]["max_coefficient_of_variation"] for budget in budgets
    } == {name: documented_variance_limits.get(name, 0.15) for name in expected_names}
    results = evaluate(budgets, [])

    assert {(result.status, result.name) for result in results} == {
        ("INCONCLUSIVE", name) for name in expected_names
    }
    assert all(result.detail == "measurement is missing" for result in results)


def test_measurement_passes_both_regression_thresholds() -> None:
    results = evaluate([_budget()], [_measurement([96, 98, 99, 100, 101, 102, 104])])

    assert results[0].status == "PASS"
    assert "median 100.000 <= 110.000" in results[0].detail


def test_measurement_fails_when_threshold_is_exceeded() -> None:
    results = evaluate([_budget()], [_measurement([112, 113, 114, 115, 116, 117, 118])])

    assert results[0].status == "FAIL"
    assert "median 115.000 > 110.000" in results[0].detail


@pytest.mark.parametrize(
    ("samples", "expected_detail"),
    [
        ([100, 101, 99], "requires 7"),
        ([50, 50, 50, 100, 150, 150, 150], "coefficient of variation"),
    ],
)
def test_inadequate_or_noisy_samples_are_inconclusive(
    samples: list[float], expected_detail: str
) -> None:
    results = evaluate([_budget()], [_measurement(samples)])

    assert results[0].status == "INCONCLUSIVE"
    assert expected_detail in results[0].detail


def test_pending_baseline_still_validates_supplied_measurement() -> None:
    measurement = _measurement([100, 100, 100, 100, 100, 100, 100])
    measurement["unexpected"] = True

    results = evaluate([_budget(pending=True)], [measurement])

    assert results[0].status == "INCONCLUSIVE"
    assert "unknown fields: unexpected" in results[0].detail


def test_budget_schema_rejects_unknown_fields(tmp_path: Path) -> None:
    budget = _budget()
    budget["typo_ceiling"] = 123
    path = tmp_path / "budgets.yaml"
    _write_budgets(path, [budget])

    with pytest.raises(BudgetSchemaError, match="unknown fields: typo_ceiling"):
        load_budgets(path)


def test_input_loaders_reject_duplicate_mapping_keys(tmp_path: Path) -> None:
    budget_path = tmp_path / "duplicate-budget.yaml"
    budget_path.write_text("version: 1\nversion: 1\nbudgets: []\n", encoding="utf-8")
    with pytest.raises(BudgetSchemaError, match="duplicate YAML mapping key: version"):
        load_budgets(budget_path)

    measurement_path = tmp_path / "duplicate-measurements.json"
    measurement_path.write_text(
        '{"version":1,"version":1,"provenance":{},"measurements":[]}',
        encoding="utf-8",
    )
    with pytest.raises(MeasurementError, match="duplicate JSON mapping key: version"):
        load_measurements(measurement_path)


def test_cli_never_mutates_budget_or_measurement_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget_path = tmp_path / "budgets.yaml"
    measurement_path = tmp_path / "measurements.json"
    budget = _budget()
    measurement = _measurement([96, 98, 99, 100, 101, 102, 104])
    _write_budgets(budget_path, [copy.deepcopy(budget)])
    _write_measurements(measurement_path, [copy.deepcopy(measurement)])
    before_budget = budget_path.read_bytes()
    before_measurement = measurement_path.read_bytes()

    exit_code = main(["--budgets", str(budget_path), "--measurements", str(measurement_path)])

    assert exit_code == 0
    assert "PASS A1-app-ready" in capsys.readouterr().out
    assert budget_path.read_bytes() == before_budget
    assert measurement_path.read_bytes() == before_measurement


def test_cli_preserves_fail_and_inconclusive_exit_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    budget_path = tmp_path / "budgets.yaml"
    measurement_path = tmp_path / "measurements.json"
    _write_budgets(budget_path, [_budget()])

    _write_measurements(
        measurement_path,
        [_measurement([112, 113, 114, 115, 116, 117, 118])],
    )
    assert main(["--budgets", str(budget_path), "--measurements", str(measurement_path)]) == 1
    assert "OVERALL FAIL" in capsys.readouterr().out

    measurement_path.write_bytes(b"\xff")
    assert main(["--budgets", str(budget_path), "--measurements", str(measurement_path)]) == 2
    assert "OVERALL INCONCLUSIVE" in capsys.readouterr().out
