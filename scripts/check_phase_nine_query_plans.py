"""Collect private, read-only query-plan summaries for the Phase 9 large garden."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from gardenops.services.generated_task_lifecycle import (
    GENERATED_WATERING_RULE_SOURCE_PATTERNS,
    stale_generated_watering_sql,
)

EXPECTED_GARDEN_NAME = "Complete Journeys Scale Large"
EXPECTED_OUTPUT_NAME = "phase-nine-query-plans.json"
FROZEN_TODAY_SQL = "DATE '2026-07-12'"


@dataclass(frozen=True)
class PlanSpec:
    name: str
    sql: str
    params: Sequence[object]


def _require_environment(environ: Mapping[str, str]) -> None:
    required = (
        "DATABASE_URL",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
    )
    if any(not environ.get(name) for name in required):
        raise RuntimeError("Phase 9 query-plan pass requires disposable runner evidence")
    if environ.get("APP_ENV") != "test":
        raise RuntimeError("Phase 9 query-plan pass requires APP_ENV=test")
    if environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD") != "1":
        raise RuntimeError("Phase 9 query-plan pass requires complete-journey child mode")
    if environ["DATABASE_URL"] != environ["GARDENOPS_DISPOSABLE_POSTGRES_URL"]:
        raise RuntimeError("Phase 9 query-plan pass requires the runner-issued database URL")
    system_identifier = environ["GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"]
    marker = environ["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"]
    if not system_identifier.isdecimal() or not marker.startswith(f"{system_identifier}."):
        raise RuntimeError("Phase 9 query-plan pass requires a runner-bound marker")


def _output_path(raw_output: str, environ: Mapping[str, str]) -> Path:
    artifact_raw = environ["GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR"]
    requested_artifact = Path(artifact_raw)
    if requested_artifact.is_symlink():
        raise RuntimeError("Phase 9 query-plan artifact directory must not be a symlink")
    artifact_dir = requested_artifact.resolve(strict=True)
    if not artifact_dir.is_dir():
        raise RuntimeError("Phase 9 query-plan artifact directory is unavailable")

    target = Path(raw_output).resolve(strict=False)
    if target.name != EXPECTED_OUTPUT_NAME or target.parent != artifact_dir:
        raise RuntimeError("Phase 9 query-plan output must be the fixed artifact child")
    if target.exists():
        raise RuntimeError("Phase 9 query-plan output must not overwrite prior evidence")
    return target


def _task_where_clause() -> tuple[str, list[object]]:
    actionable_date = "COALESCE(t.snoozed_until::date, t.due_on::date)"
    actionable_status = (
        "(t.status = 'pending' OR (t.status = 'snoozed' "
        f"AND t.snoozed_until::date <= {FROZEN_TODAY_SQL}))"
    )
    stale_generated_watering = stale_generated_watering_sql(
        action_on_sql=actionable_date,
        today_sql=FROZEN_TODAY_SQL,
    )
    where = " AND ".join(
        (
            "t.garden_id = %s",
            f"({actionable_status} AND {actionable_date} <= {FROZEN_TODAY_SQL})",
            f"NOT (t.status IN ('pending', 'snoozed') AND {stale_generated_watering})",
        )
    )
    return where, list(GENERATED_WATERING_RULE_SOURCE_PATTERNS)


def build_plan_specs(garden_id: int) -> tuple[PlanSpec, ...]:
    """Return the fixed, route-derived query allowlist without request data."""
    task_where, task_patterns = _task_where_clause()
    return (
        PlanSpec(
            name="map_objects",
            sql="""
                SELECT *
                FROM garden_map_objects
                WHERE garden_id = %s
                ORDER BY z_index, id
            """,
            params=(garden_id,),
        ),
        PlanSpec(
            name="plots",
            sql="""
                SELECT p.*, COUNT(pown.plt_id) AS plant_count,
                    SUM(CASE WHEN pl.category = 'trær' THEN 1 ELSE 0 END) AS tree_count,
                    SUM(CASE WHEN pl.category IN ('busker', 'baerbusker') THEN 1 ELSE 0 END)
                        AS bush_count
                FROM plots p
                LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
                LEFT JOIN plot_plants pp ON pp.plot_id = p.plot_id
                LEFT JOIN plant_ownership pown
                    ON pown.plt_id = pp.plt_id AND pown.garden_id = po.garden_id
                LEFT JOIN plants pl ON pl.plt_id = pown.plt_id
                WHERE po.garden_id = %s
                GROUP BY p.plot_id, po.owner_user_id
                ORDER BY p.zone_code, p.plot_number
            """,
            params=(garden_id,),
        ),
        PlanSpec(
            name="plants",
            sql="""
                SELECT p.*
                FROM plants p
                LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id
                WHERE po.garden_id = %s
                ORDER BY p.name, p.plt_id
            """,
            params=(garden_id,),
        ),
        PlanSpec(
            name="plant_assignments",
            sql="""
                SELECT pp.plt_id, pp.plot_id, pp.quantity, pp.seen_growing, pp.seen_growing_date
                FROM plot_plants pp
                LEFT JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
                WHERE pwo.garden_id = %s
                  AND pp.plt_id IN (
                    SELECT po.plt_id
                    FROM plant_ownership po
                    WHERE po.garden_id = %s
                  )
                ORDER BY pp.plt_id, pp.plot_id
            """,
            params=(garden_id, garden_id),
        ),
        PlanSpec(
            name="tasks_count",
            sql=f"SELECT COUNT(*) FROM garden_tasks t WHERE {task_where}",
            params=(garden_id, *task_patterns),
        ),
        PlanSpec(
            name="tasks_page",
            sql=f"""
                SELECT t.*
                FROM garden_tasks t
                WHERE {task_where}
                ORDER BY COALESCE(t.snoozed_until::date, t.due_on::date) ASC, t.created_at_ms DESC
                LIMIT %s OFFSET %s
            """,
            params=(garden_id, *task_patterns, 50, 0),
        ),
    )


def _summarize_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    root = plan.get("Plan")
    if not isinstance(root, Mapping):
        raise RuntimeError("EXPLAIN returned no root plan")

    node_types: set[str] = set()

    def visit(node: Mapping[str, Any]) -> None:
        node_type = node.get("Node Type")
        if isinstance(node_type, str):
            node_types.add(node_type)
        children = node.get("Plans", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, Mapping):
                    visit(child)

    visit(root)
    return {
        "actual_rows": int(root.get("Actual Rows", 0)),
        "execution_ms": round(float(plan.get("Execution Time", 0.0)), 3),
        "node_types": sorted(node_types),
        "plan_rows": int(root.get("Plan Rows", 0)),
        "planning_ms": round(float(plan.get("Planning Time", 0.0)), 3),
        "root_node_type": str(root.get("Node Type", "")),
    }


def collect_report(conn: psycopg.Connection[dict[str, Any]], marker: str) -> dict[str, Any]:
    marker_row = conn.execute(
        "SELECT current_setting('gardenops.disposable_marker', true) AS marker"
    ).fetchone()
    if not marker_row or marker_row["marker"] != marker:
        raise RuntimeError("Phase 9 query-plan pass database marker did not match the runner")
    garden_rows = conn.execute(
        "SELECT id FROM gardens WHERE name = %s ORDER BY id",
        (EXPECTED_GARDEN_NAME,),
    ).fetchall()
    if len(garden_rows) != 1:
        raise RuntimeError("Phase 9 large garden is unavailable for query planning")

    plans: list[dict[str, Any]] = []
    for spec in build_plan_specs(int(garden_rows[0]["id"])):
        row = conn.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + spec.sql,
            spec.params,
        ).fetchone()
        payload = row.get("QUERY PLAN") if row else None
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], Mapping):
            raise RuntimeError(f"Phase 9 EXPLAIN returned invalid evidence for {spec.name}")
        plans.append({"name": spec.name, **_summarize_plan(payload[0])})
    return {
        "mode": "read-only-explain-analyze",
        "plans": plans,
        "schema_version": 1,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    environ = os.environ
    _require_environment(environ)
    output = _output_path(args.output, environ)
    with psycopg.connect(
        environ["DATABASE_URL"],
        connect_timeout=10,
        row_factory=dict_row,
    ) as conn:
        conn.execute("BEGIN READ ONLY")
        try:
            conn.execute("SET LOCAL lock_timeout = '1000ms'")
            conn.execute("SET LOCAL statement_timeout = '5000ms'")
            report = collect_report(conn, environ["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"])
        finally:
            conn.rollback()
    with output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, separators=(",", ":"), sort_keys=True))
        handle.write("\n")
    output.chmod(0o600)
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
