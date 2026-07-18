from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.capture_page_performance_measurements import (
    CaptureError,
    build_document,
    capture_measurements,
    main,
)
from scripts.check_performance_budgets import (
    DEFAULT_BUDGETS,
    load_budgets,
)
from scripts.check_performance_budgets import (
    main as check_budgets,
)

ROOT = Path(__file__).resolve().parents[1]


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _page_result(
    samples: list[float],
    *,
    api_mode: str = "live",
    device_profile: str = "desktop",
    scenario: str = "app-auth-large-tabs",
    metric: str = "appReadyMs",
) -> dict[str, object]:
    return {
        "createdAt": datetime.now(UTC).isoformat(),
        "measurement": {"schemaVersion": 6},
        "provenance": {
            "comparison": {
                "apiMode": api_mode,
                "options": {"deviceProfile": device_profile},
                "scenario": scenario,
            },
            "git": {"dirty": False, "revision": _revision()},
        },
        "runs": [
            {
                "consoleErrors": [],
                "pageErrors": [],
                "timings": {metric: sample},
            }
            for sample in samples
        ],
    }


def test_capture_maps_only_measured_runs_into_budget_evidence() -> None:
    budgets = load_budgets(DEFAULT_BUDGETS)
    result = _page_result([100, 101, 102, 103, 104, 105, 106])

    document = build_document([(result, json.dumps(result).encode("utf-8"))], budgets[:1])

    measurement = document["measurements"][0]
    assert measurement["budget"] == "M1-large-desktop-app-ready"
    assert measurement["samples"] == [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    assert document["provenance"]["git_dirty"] is False
    assert document["provenance"]["git_revision"] == _revision()


def test_capture_rejects_mismatched_source_or_browser_errors() -> None:
    budgets = load_budgets(DEFAULT_BUDGETS)
    mismatch = _page_result([100] * 7)
    mismatch["provenance"]["comparison"]["apiMode"] = "stub"  # type: ignore[index]
    with pytest.raises(CaptureError, match="source does not match"):
        capture_measurements(mismatch, budgets)

    browser_error = _page_result([100] * 7)
    browser_error["runs"][0]["consoleErrors"] = ["unexpected error"]  # type: ignore[index]
    with pytest.raises(CaptureError, match="has browser errors"):
        capture_measurements(browser_error, budgets)


def test_capture_never_overwrites_and_produces_checker_input(tmp_path: Path) -> None:
    result = _page_result([96, 98, 99, 100, 101, 102, 104])
    result_path = tmp_path / "page-performance.json"
    output_path = tmp_path / "measurements.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    command = [
        str(result_path),
        "--budget",
        "M1-large-desktop-app-ready",
        "--output",
        str(output_path),
    ]
    assert main(command) == 0
    assert check_budgets(["--measurements", str(output_path)]) == 2
    before = output_path.read_bytes()
    assert main(command) == 2
    assert output_path.read_bytes() == before


def test_capture_rejects_tracked_output_paths(tmp_path: Path) -> None:
    result = _page_result([100] * 7)
    result_path = tmp_path / "page-performance.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    assert (
        main(
            [
                str(result_path),
                "--budget",
                "M1-large-desktop-app-ready",
                "--output",
                str(ROOT / "measurements.json"),
            ]
        )
        == 2
    )


def test_capture_combines_matching_desktop_and_pixel_results() -> None:
    budgets = load_budgets(DEFAULT_BUDGETS)
    desktop = _page_result([100] * 7)
    pixel = _page_result([200] * 7, device_profile="pixel-7")

    document = build_document(
        [
            (desktop, json.dumps(desktop).encode("utf-8")),
            (pixel, json.dumps(pixel).encode("utf-8")),
        ],
        [budgets[0], budgets[1]],
    )

    assert [measurement["budget"] for measurement in document["measurements"]] == [
        "M1-large-desktop-app-ready",
        "M1-large-pixel-app-ready",
    ]


def test_capture_rejects_a_selected_budget_without_matching_result() -> None:
    budgets = load_budgets(DEFAULT_BUDGETS)
    desktop = _page_result([100] * 7)

    with pytest.raises(CaptureError, match="no matching page-performance result"):
        build_document(
            [(desktop, json.dumps(desktop).encode("utf-8"))],
            [budgets[0], budgets[1]],
        )
