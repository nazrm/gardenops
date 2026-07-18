"""Unit tests for the isolated Phase 9 read-only query-plan collector."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_phase_nine_query_plans.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase_nine_query_plans", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


class _Cursor:
    def __init__(self, *, all_rows: list[dict] | None = None, one_row: dict | None = None) -> None:
        self._all_rows = all_rows or []
        self._one_row = one_row

    def fetchall(self) -> list[dict]:
        return self._all_rows

    def fetchone(self) -> dict | None:
        return self._one_row


class _Connection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str, _params: object = None) -> _Cursor:
        self.queries.append(query)
        if "current_setting" in query:
            return _Cursor(one_row={"marker": "123.runner-issued-marker"})
        if "SELECT id FROM gardens" in query:
            return _Cursor(all_rows=[{"id": 73}])
        if query.startswith("EXPLAIN"):
            return _Cursor(
                one_row={
                    "QUERY PLAN": [
                        {
                            "Execution Time": 1.25,
                            "Planning Time": 0.5,
                            "Plan": {
                                "Actual Rows": 12,
                                "Node Type": "Sort",
                                "Plan Rows": 15,
                                "Plans": [{"Node Type": "Seq Scan"}],
                            },
                        }
                    ]
                }
            )
        raise AssertionError(f"Unexpected query: {query}")


def test_plan_specs_are_fixed_route_derived_selects() -> None:
    specs = SCRIPT.build_plan_specs(73)

    assert [spec.name for spec in specs] == [
        "map_objects",
        "plots",
        "plants",
        "plant_assignments",
        "tasks_count",
        "tasks_page",
    ]
    assert all(spec.sql.lstrip().upper().startswith("SELECT") for spec in specs)
    assert all(";" not in spec.sql for spec in specs)
    assert all("Complete Journeys Scale Large" not in spec.sql for spec in specs)


def test_collector_summarizes_allowlisted_explain_without_sql_or_parameters() -> None:
    conn = _Connection()

    report = SCRIPT.collect_report(conn, "123.runner-issued-marker")

    assert report["mode"] == "read-only-explain-analyze"
    assert report["schema_version"] == 1
    assert [plan["name"] for plan in report["plans"]] == [
        "map_objects",
        "plots",
        "plants",
        "plant_assignments",
        "tasks_count",
        "tasks_page",
    ]
    assert all(plan["node_types"] == ["Seq Scan", "Sort"] for plan in report["plans"])
    assert all("sql" not in plan and "params" not in plan for plan in report["plans"])
    assert sum(query.startswith("EXPLAIN") for query in conn.queries) == 6


def test_output_path_rejects_non_artifact_or_existing_targets(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    environ = {"GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": str(artifact)}
    output = artifact / "phase-nine-query-plans.json"

    assert SCRIPT._output_path(str(output), environ) == output

    output.touch()
    with pytest.raises(RuntimeError, match="must not overwrite"):
        SCRIPT._output_path(str(output), environ)
    with pytest.raises(RuntimeError, match="fixed artifact child"):
        SCRIPT._output_path(str(artifact / "wrong.json"), environ)


def test_environment_requires_runner_bound_disposable_database() -> None:
    environ = {
        "APP_ENV": "test",
        "DATABASE_URL": "postgresql://example/gardenops_test",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": "/tmp/artifact",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": "1",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "123.runner-issued-marker",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": "postgresql://example/gardenops_test",
    }

    SCRIPT._require_environment(environ)
    environ["GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD"] = "0"
    with pytest.raises(RuntimeError, match="child mode"):
        SCRIPT._require_environment(environ)
