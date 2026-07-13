import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from gardenops.routers.tasks import _task_action_clock, _task_list_date_expressions

ROOT = Path(__file__).resolve().parents[1]


def test_task_list_uses_current_date_outside_test_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "not-a-timestamp")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "not-a-date")

    expressions = _task_list_date_expressions()

    assert expressions["today"] == "CURRENT_DATE"
    assert expressions["plus_7_days"] == "(CURRENT_DATE + INTERVAL '7 days')::date"


def test_task_list_uses_paired_frozen_test_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783252800000")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05")

    expressions = _task_list_date_expressions()

    assert expressions["today"] == "DATE '2026-07-05'"
    assert expressions["plus_7_days"] == "(DATE '2026-07-05' + INTERVAL '7 days')::date"


def test_task_actions_use_the_same_paired_frozen_test_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783252800000")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05")

    assert _task_action_clock() == (1783252800000, "2026-07-05")


def test_task_list_rejects_invalid_frozen_date_before_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783252800000")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05'; SELECT 1; --")

    with pytest.raises(HTTPException, match="Invalid date") as exc_info:
        _task_list_date_expressions()

    assert exc_info.value.status_code == 422


def test_task_list_requires_a_paired_frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783252800000")
    monkeypatch.delenv("GARDENOPS_ATTENTION_FROZEN_DATE", raising=False)

    with pytest.raises(RuntimeError, match="requires both"):
        _task_list_date_expressions()


def test_task_history_runner_propagates_the_paired_attention_clock() -> None:
    runner = ROOT / "scripts" / "run_task_completion_history_e2e.sh"
    checker = ROOT / "scripts" / "check_task_completion_history_e2e.cjs"
    runner_source = runner.read_text(encoding="utf-8")
    checker_source = checker.read_text(encoding="utf-8")

    assert "export GARDENOPS_ATTENTION_FROZEN_NOW_MS=1783252800000" in runner_source
    assert "export GARDENOPS_ATTENTION_FROZEN_DATE=2026-07-05" in runner_source
    postgres_runner = runner_source[runner_source.index("sudo -u postgres env ") :]
    assert (
        'GARDENOPS_ATTENTION_FROZEN_NOW_MS="$GARDENOPS_ATTENTION_FROZEN_NOW_MS"' in postgres_runner
    )
    assert 'GARDENOPS_ATTENTION_FROZEN_DATE="$GARDENOPS_ATTENTION_FROZEN_DATE"' in postgres_runner
    assert (
        "const FROZEN_NOW_MS = Number(process.env.GARDENOPS_ATTENTION_FROZEN_NOW_MS"
        in checker_source
    )
    assert "const FROZEN_DATE = process.env.GARDENOPS_ATTENTION_FROZEN_DATE" in checker_source
    assert "GARDENOPS_TASK_HISTORY_E2E_FROZEN_NOW_ISO" not in checker_source
    assert (
        "`GARDENOPS_ATTENTION_FROZEN_NOW_MS=${env.GARDENOPS_ATTENTION_FROZEN_NOW_MS}`"
        in checker_source
    )
    assert (
        "`GARDENOPS_ATTENTION_FROZEN_DATE=${env.GARDENOPS_ATTENTION_FROZEN_DATE}`" in checker_source
    )

    bash_result = subprocess.run(
        ["bash", "-n", str(runner)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert bash_result.returncode == 0, bash_result.stderr
    node_result = subprocess.run(
        ["node", "--check", str(checker)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert node_result.returncode == 0, node_result.stderr
