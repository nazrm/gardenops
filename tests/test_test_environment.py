"""Regression checks for pytest process safety."""

from __future__ import annotations

import os
from pathlib import Path


def test_pytest_uses_disposable_error_log_dir() -> None:
    """Tests must not write synthetic warnings/errors into deployed logs."""
    logs_dir = Path(os.environ["GARDENOPS_LOGS_DIR"]).resolve()
    repo_logs_dir = Path(__file__).resolve().parents[1] / "logs"

    assert logs_dir != repo_logs_dir.resolve()
    assert logs_dir.name.startswith("gardenops-test-logs-")
    assert logs_dir.exists()
