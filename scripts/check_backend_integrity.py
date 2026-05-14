#!/usr/bin/env python3
"""Read-only backend data and schema integrity audit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import psycopg
import psycopg.rows

from gardenops.schema_signature import (
    REQUIRED_COLUMNS,
    REQUIRED_CONSTRAINTS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    bootstrap_schema_diagnostics_from_snapshot,
    collect_schema_snapshot,
    missing_schema_parts,
)

MAX_EXAMPLES = 5


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: str
    blocking: bool
    count: int
    examples: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "blocking": self.blocking,
            "count": self.count,
            "examples": self.examples,
        }


@dataclass(frozen=True)
class StalePlotReferenceSpec:
    finding_id: str
    title: str
    bad_rows_sql: str
    example_sql: str


STALE_PLOT_REFERENCE_SPECS = (
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.garden_issue_plots",
        title="garden_issue_plots rows reference missing plots",
        bad_rows_sql="""
            SELECT gip.issue_id, gip.plot_id, gi.garden_id
            FROM garden_issue_plots gip
            JOIN garden_issues gi ON gi.id = gip.issue_id
            LEFT JOIN plots p ON p.plot_id = gip.plot_id
            WHERE p.plot_id IS NULL
        """,
        example_sql="""
            SELECT gip.issue_id, gip.plot_id, gi.garden_id
            FROM garden_issue_plots gip
            JOIN garden_issues gi ON gi.id = gip.issue_id
            LEFT JOIN plots p ON p.plot_id = gip.plot_id
            WHERE p.plot_id IS NULL
            ORDER BY gi.garden_id, gip.issue_id, gip.plot_id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.garden_task_plots",
        title="garden_task_plots rows reference missing plots",
        bad_rows_sql="""
            SELECT gtp.task_id, gtp.plot_id, gt.garden_id
            FROM garden_task_plots gtp
            JOIN garden_tasks gt ON gt.id = gtp.task_id
            LEFT JOIN plots p ON p.plot_id = gtp.plot_id
            WHERE p.plot_id IS NULL
        """,
        example_sql="""
            SELECT gtp.task_id, gtp.plot_id, gt.garden_id
            FROM garden_task_plots gtp
            JOIN garden_tasks gt ON gt.id = gtp.task_id
            LEFT JOIN plots p ON p.plot_id = gtp.plot_id
            WHERE p.plot_id IS NULL
            ORDER BY gt.garden_id, gtp.task_id, gtp.plot_id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.garden_journal_entry_plots",
        title="garden_journal_entry_plots rows reference missing plots",
        bad_rows_sql="""
            SELECT gjep.entry_id, gjep.plot_id, gje.garden_id
            FROM garden_journal_entry_plots gjep
            JOIN garden_journal_entries gje ON gje.id = gjep.entry_id
            LEFT JOIN plots p ON p.plot_id = gjep.plot_id
            WHERE p.plot_id IS NULL
        """,
        example_sql="""
            SELECT gjep.entry_id, gjep.plot_id, gje.garden_id
            FROM garden_journal_entry_plots gjep
            JOIN garden_journal_entries gje ON gje.id = gjep.entry_id
            LEFT JOIN plots p ON p.plot_id = gjep.plot_id
            WHERE p.plot_id IS NULL
            ORDER BY gje.garden_id, gjep.entry_id, gjep.plot_id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.harvest_entry_plots",
        title="harvest_entry_plots rows reference missing plots",
        bad_rows_sql="""
            SELECT hep.entry_id, hep.plot_id, he.garden_id
            FROM harvest_entry_plots hep
            JOIN harvest_entries he ON he.id = hep.entry_id
            LEFT JOIN plots p ON p.plot_id = hep.plot_id
            WHERE p.plot_id IS NULL
        """,
        example_sql="""
            SELECT hep.entry_id, hep.plot_id, he.garden_id
            FROM harvest_entry_plots hep
            JOIN harvest_entries he ON he.id = hep.entry_id
            LEFT JOIN plots p ON p.plot_id = hep.plot_id
            WHERE p.plot_id IS NULL
            ORDER BY he.garden_id, hep.entry_id, hep.plot_id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.garden_calendar_event_plots",
        title="garden_calendar_event_plots rows reference missing plots",
        bad_rows_sql="""
            SELECT gcep.event_id, gcep.plot_id, gce.garden_id
            FROM garden_calendar_event_plots gcep
            JOIN garden_calendar_events gce ON gce.id = gcep.event_id
            LEFT JOIN plots p ON p.plot_id = gcep.plot_id
            WHERE p.plot_id IS NULL
        """,
        example_sql="""
            SELECT gcep.event_id, gcep.plot_id, gce.garden_id
            FROM garden_calendar_event_plots gcep
            JOIN garden_calendar_events gce ON gce.id = gcep.event_id
            LEFT JOIN plots p ON p.plot_id = gcep.plot_id
            WHERE p.plot_id IS NULL
            ORDER BY gce.garden_id, gcep.event_id, gcep.plot_id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.media_links_plot",
        title="media_links plot targets reference missing plots",
        bad_rows_sql="""
            SELECT ml.asset_id, ml.target_id AS plot_id, ma.garden_id
            FROM media_links ml
            JOIN media_assets ma ON ma.asset_id = ml.asset_id
            LEFT JOIN plots p ON p.plot_id = ml.target_id
            WHERE ml.target_type = 'plot' AND p.plot_id IS NULL
        """,
        example_sql="""
            SELECT ml.asset_id, ml.target_id AS plot_id, ma.garden_id
            FROM media_links ml
            JOIN media_assets ma ON ma.asset_id = ml.asset_id
            LEFT JOIN plots p ON p.plot_id = ml.target_id
            WHERE ml.target_type = 'plot' AND p.plot_id IS NULL
            ORDER BY ma.garden_id, ml.asset_id, ml.target_id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.shademap_obstacles",
        title="shademap_obstacles linked_plot_id values reference missing plots",
        bad_rows_sql="""
            SELECT so.id, so.garden_id, so.linked_plot_id AS plot_id
            FROM shademap_obstacles so
            LEFT JOIN plots p ON p.plot_id = so.linked_plot_id
            WHERE so.linked_plot_id IS NOT NULL AND p.plot_id IS NULL
        """,
        example_sql="""
            SELECT so.id, so.garden_id, so.linked_plot_id AS plot_id
            FROM shademap_obstacles so
            LEFT JOIN plots p ON p.plot_id = so.linked_plot_id
            WHERE so.linked_plot_id IS NOT NULL AND p.plot_id IS NULL
            ORDER BY so.garden_id, so.id
            LIMIT %s
        """,
    ),
    StalePlotReferenceSpec(
        finding_id="stale_plot_reference.shademap_state",
        title="shademap_state selected_plot_id values reference missing plots",
        bad_rows_sql="""
            SELECT ss.id, ss.garden_id, ss.selected_plot_id AS plot_id
            FROM shademap_state ss
            LEFT JOIN plots p ON p.plot_id = ss.selected_plot_id
            WHERE ss.selected_plot_id IS NOT NULL AND p.plot_id IS NULL
        """,
        example_sql="""
            SELECT ss.id, ss.garden_id, ss.selected_plot_id AS plot_id
            FROM shademap_state ss
            LEFT JOIN plots p ON p.plot_id = ss.selected_plot_id
            WHERE ss.selected_plot_id IS NOT NULL AND p.plot_id IS NULL
            ORDER BY ss.garden_id, ss.id
            LIMIT %s
        """,
    ),
)


def _as_json_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, dict):
        return {str(key): _as_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_as_json_value(item) for item in value]
    return str(value)


def _example_rows(rows: list[Any]) -> list[dict[str, object]]:
    examples: list[dict[str, object]] = []
    for row in rows:
        examples.append({str(key): _as_json_value(value) for key, value in dict(row).items()})
    return examples


def _count_rows(conn: psycopg.Connection[Any], bad_rows_sql: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS count FROM ({bad_rows_sql}) AS bad_rows",
    ).fetchone()
    assert row is not None
    return int(row["count"])


def _finding_from_query(
    conn: psycopg.Connection[Any],
    *,
    finding_id: str,
    title: str,
    severity: str,
    blocking: bool,
    bad_rows_sql: str,
    example_sql: str,
) -> Finding:
    count = _count_rows(conn, bad_rows_sql)
    rows = conn.execute(example_sql, (MAX_EXAMPLES,)).fetchall() if count else []
    return Finding(
        id=finding_id,
        title=title,
        severity=severity,
        blocking=blocking,
        count=count,
        examples=_example_rows(rows),
    )


def _duplicate_layout_cells(conn: psycopg.Connection[Any]) -> Finding:
    bad_rows_sql = """
        SELECT po.garden_id, p.grid_row, p.grid_col, COUNT(*) AS duplicate_count
        FROM plots p
        JOIN plot_ownership po ON po.plot_id = p.plot_id
        JOIN gardens g ON g.id = po.garden_id
        WHERE p.grid_row IS NOT NULL
          AND p.grid_col IS NOT NULL
          AND p.zone_code <> 'I'
          AND p.plot_id NOT LIKE 'INDOOR-%%'
        GROUP BY po.garden_id, p.grid_row, p.grid_col
        HAVING COUNT(*) > 1
    """
    example_sql = """
        SELECT
            po.garden_id,
            p.grid_row,
            p.grid_col,
            COUNT(*) AS duplicate_count,
            ARRAY_AGG(p.plot_id ORDER BY p.plot_id) AS plot_ids
        FROM plots p
        JOIN plot_ownership po ON po.plot_id = p.plot_id
        JOIN gardens g ON g.id = po.garden_id
        WHERE p.grid_row IS NOT NULL
          AND p.grid_col IS NOT NULL
          AND p.zone_code <> 'I'
          AND p.plot_id NOT LIKE 'INDOOR-%%'
        GROUP BY po.garden_id, p.grid_row, p.grid_col
        HAVING COUNT(*) > 1
        ORDER BY po.garden_id, p.grid_row, p.grid_col
        LIMIT %s
    """
    return _finding_from_query(
        conn,
        finding_id="duplicate_layout_cells",
        title="Outdoor plot layout cells are duplicated within a garden",
        severity="error",
        blocking=True,
        bad_rows_sql=bad_rows_sql,
        example_sql=example_sql,
    )


def _stale_plot_reference_findings(conn: psycopg.Connection[Any]) -> list[Finding]:
    return [
        _finding_from_query(
            conn,
            finding_id=spec.finding_id,
            title=spec.title,
            severity="error",
            blocking=True,
            bad_rows_sql=spec.bad_rows_sql,
            example_sql=spec.example_sql,
        )
        for spec in STALE_PLOT_REFERENCE_SPECS
    ]


def _missing_plot_ownership(conn: psycopg.Connection[Any]) -> Finding:
    return _finding_from_query(
        conn,
        finding_id="plot_missing_garden_ownership",
        title="Plot records have no garden ownership row",
        severity="error",
        blocking=True,
        bad_rows_sql="""
            SELECT p.plot_id
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.plot_id IS NULL
        """,
        example_sql="""
            SELECT p.plot_id
            FROM plots p
            LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.plot_id IS NULL
            ORDER BY p.plot_id
            LIMIT %s
        """,
    )


def _plot_garden_ownership_mismatch(conn: psycopg.Connection[Any]) -> Finding:
    return _finding_from_query(
        conn,
        finding_id="plot_garden_ownership_mismatch",
        title="plots.garden_id disagrees with plot_ownership.garden_id",
        severity="error",
        blocking=True,
        bad_rows_sql="""
            SELECT p.plot_id, p.garden_id AS plot_garden_id, po.garden_id AS ownership_garden_id
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.garden_id IS DISTINCT FROM po.garden_id
        """,
        example_sql="""
            SELECT p.plot_id, p.garden_id AS plot_garden_id, po.garden_id AS ownership_garden_id
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.garden_id IS DISTINCT FROM po.garden_id
            ORDER BY p.plot_id
            LIMIT %s
        """,
    )


def _missing_plant_ownership(conn: psycopg.Connection[Any]) -> Finding:
    return _finding_from_query(
        conn,
        finding_id="plant_missing_garden_ownership",
        title="Plant records have no garden ownership row",
        severity="error",
        blocking=True,
        bad_rows_sql="""
            SELECT p.plt_id
            FROM plants p
            LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.plt_id IS NULL
        """,
        example_sql="""
            SELECT p.plt_id
            FROM plants p
            LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.plt_id IS NULL
            ORDER BY p.plt_id
            LIMIT %s
        """,
    )


def _garden_owner_integrity(conn: psycopg.Connection[Any]) -> Finding:
    return _finding_from_query(
        conn,
        finding_id="garden_owner_not_active_user",
        title="gardens.owner_user_id does not point at an active auth user",
        severity="error",
        blocking=True,
        bad_rows_sql="""
            SELECT
                g.id AS garden_id,
                g.owner_user_id,
                CASE
                    WHEN u.id IS NULL THEN 'missing_user'
                    WHEN u.is_active <> 1 THEN 'inactive_user'
                    ELSE 'unknown'
                END AS reason
            FROM gardens g
            LEFT JOIN auth_users u ON u.id = g.owner_user_id
            WHERE g.owner_user_id IS NOT NULL
              AND (u.id IS NULL OR u.is_active <> 1)
        """,
        example_sql="""
            SELECT
                g.id AS garden_id,
                g.owner_user_id,
                CASE
                    WHEN u.id IS NULL THEN 'missing_user'
                    WHEN u.is_active <> 1 THEN 'inactive_user'
                    ELSE 'unknown'
                END AS reason
            FROM gardens g
            LEFT JOIN auth_users u ON u.id = g.owner_user_id
            WHERE g.owner_user_id IS NOT NULL
              AND (u.id IS NULL OR u.is_active <> 1)
            ORDER BY g.id
            LIMIT %s
        """,
    )


def _user_hard_delete_ownership_summary(conn: psycopg.Connection[Any]) -> Finding:
    bad_rows_sql = """
        WITH user_refs AS (
            SELECT owner_user_id AS user_id, 'gardens_owned' AS resource, COUNT(*) AS count
            FROM gardens
            WHERE owner_user_id IS NOT NULL
            GROUP BY owner_user_id
            UNION ALL
            SELECT owner_user_id AS user_id, 'plants_owned' AS resource, COUNT(*) AS count
            FROM plant_ownership
            GROUP BY owner_user_id
            UNION ALL
            SELECT owner_user_id AS user_id, 'plots_owned' AS resource, COUNT(*) AS count
            FROM plot_ownership
            GROUP BY owner_user_id
            UNION ALL
            SELECT created_by_user_id AS user_id, 'tasks_created' AS resource, COUNT(*) AS count
            FROM garden_tasks
            WHERE created_by_user_id IS NOT NULL
            GROUP BY created_by_user_id
            UNION ALL
            SELECT completed_by_user_id AS user_id, 'tasks_completed' AS resource, COUNT(*) AS count
            FROM garden_tasks
            WHERE completed_by_user_id IS NOT NULL
            GROUP BY completed_by_user_id
            UNION ALL
            SELECT actor_user_id AS user_id, 'journal_entries' AS resource, COUNT(*) AS count
            FROM garden_journal_entries
            WHERE actor_user_id IS NOT NULL
            GROUP BY actor_user_id
            UNION ALL
            SELECT actor_user_id AS user_id, 'harvest_entries' AS resource, COUNT(*) AS count
            FROM harvest_entries
            WHERE actor_user_id IS NOT NULL
            GROUP BY actor_user_id
            UNION ALL
            SELECT created_by_user_id AS user_id, 'issues_created' AS resource, COUNT(*) AS count
            FROM garden_issues
            WHERE created_by_user_id IS NOT NULL
            GROUP BY created_by_user_id
            UNION ALL
            SELECT resolved_by_user_id AS user_id, 'issues_resolved' AS resource, COUNT(*) AS count
            FROM garden_issues
            WHERE resolved_by_user_id IS NOT NULL
            GROUP BY resolved_by_user_id
            UNION ALL
            SELECT actor_user_id AS user_id, 'media_assets' AS resource, COUNT(*) AS count
            FROM media_assets
            WHERE actor_user_id IS NOT NULL
            GROUP BY actor_user_id
            UNION ALL
            SELECT actor_user_id AS user_id, 'audit_events' AS resource, COUNT(*) AS count
            FROM audit_events
            WHERE actor_user_id IS NOT NULL
            GROUP BY actor_user_id
        )
        SELECT
            u.id AS user_id,
            u.is_active,
            SUM(user_refs.count) AS total_references,
            JSONB_OBJECT_AGG(user_refs.resource, user_refs.count ORDER BY user_refs.resource)
                AS resource_counts
        FROM user_refs
        JOIN auth_users u ON u.id = user_refs.user_id
        GROUP BY u.id, u.is_active
    """
    example_sql = f"""
        SELECT *
        FROM ({bad_rows_sql}) AS ownership_summary
        ORDER BY total_references DESC, user_id
        LIMIT %s
    """
    return _finding_from_query(
        conn,
        finding_id="user_hard_delete_ownership_summary",
        title="Users own or authored data that must be handled before hard delete",
        severity="info",
        blocking=False,
        bad_rows_sql=bad_rows_sql,
        example_sql=example_sql,
    )


def _schema_signature(conn: psycopg.Connection[Any]) -> Finding:
    snapshot = collect_schema_snapshot(conn)
    missing = missing_schema_parts(
        snapshot,
        required_tables=REQUIRED_TABLES,
        required_columns=REQUIRED_COLUMNS,
        required_indexes=REQUIRED_INDEXES,
        required_constraints=REQUIRED_CONSTRAINTS,
    )

    return Finding(
        id="schema_signature_missing",
        title="Required backend schema tables, columns, indexes, or constraints are missing",
        severity="error",
        blocking=True,
        count=len(missing),
        examples=missing[:MAX_EXAMPLES],
    )


def collect_bootstrap_report(conn: psycopg.Connection[Any]) -> dict[str, object]:
    diagnostics = bootstrap_schema_diagnostics_from_snapshot(collect_schema_snapshot(conn))
    raw_missing = diagnostics.get("missing", [])
    missing = raw_missing if isinstance(raw_missing, list) else []
    ok = diagnostics["mode"] != "incomplete-existing-schema"
    return {
        "ok": ok,
        "mode": diagnostics["mode"],
        "can_stamp_migrations": diagnostics["can_stamp_migrations"],
        "existing_tables": diagnostics["existing_tables"],
        "missing_count": len(missing),
        "missing": missing,
    }


def collect_findings(conn: psycopg.Connection[Any]) -> list[Finding]:
    return [
        _duplicate_layout_cells(conn),
        *_stale_plot_reference_findings(conn),
        _missing_plot_ownership(conn),
        _plot_garden_ownership_mismatch(conn),
        _missing_plant_ownership(conn),
        _garden_owner_integrity(conn),
        _user_hard_delete_ownership_summary(conn),
        _schema_signature(conn),
    ]


def collect_report(conn: psycopg.Connection[Any]) -> dict[str, object]:
    findings = collect_findings(conn)
    blocking_count = sum(1 for finding in findings if finding.blocking and finding.count > 0)
    return {
        "ok": blocking_count == 0,
        "blocking_findings": blocking_count,
        "finding_count": len(findings),
        "findings": [finding.as_dict() for finding in findings],
    }


def _format_text(report: dict[str, object]) -> str:
    if "mode" in report and "missing_count" in report:
        if report["ok"] and report["mode"] == "empty":
            return "OK: empty database can run migrations normally"
        if report["ok"] and report["mode"] == "verified-baseline":
            return "OK: existing schema matches verified migration bootstrap baseline"
        lines = [
            "BLOCKED: existing schema is incomplete for migration bootstrap",
            f"mode={report['mode']} missing_count={report['missing_count']}",
        ]
        missing = report.get("missing")
        if isinstance(missing, list) and missing:
            lines.append(f"examples: {json.dumps(missing[:MAX_EXAMPLES], sort_keys=True)}")
        return "\n".join(lines)

    findings = report["findings"]
    assert isinstance(findings, list)
    header = (
        "OK: no blocking backend integrity findings"
        if report["ok"]
        else f"BLOCKED: {report['blocking_findings']} blocking backend integrity finding(s)"
    )
    lines = [header]
    for raw_finding in findings:
        assert isinstance(raw_finding, dict)
        status = "FAIL" if raw_finding["blocking"] and raw_finding["count"] else "OK"
        if not raw_finding["blocking"] and raw_finding["count"]:
            status = "INFO"
        lines.append(
            "[{status}] {id} count={count} severity={severity}".format(
                status=status,
                id=raw_finding["id"],
                count=raw_finding["count"],
                severity=raw_finding["severity"],
            ),
        )
        examples = raw_finding.get("examples")
        if examples:
            lines.append(f"  examples: {json.dumps(examples, sort_keys=True)}")
    return "\n".join(lines)


def _production_like_env() -> bool:
    app_env = os.environ.get("APP_ENV", "").strip().casefold()
    internet_exposed = os.environ.get("INTERNET_EXPOSED", "").strip().casefold()
    return app_env in {"prod", "production"} or internet_exposed in {"1", "true", "yes", "on"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Postgres URL to audit. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Allow running when APP_ENV=production or INTERNET_EXPOSED=true.",
    )
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="Only report whether an existing schema can be safely stamped at startup.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = str(args.database_url).strip()
    if not database_url:
        print("DATABASE_URL is required for backend integrity checks.", file=sys.stderr)
        return 2
    if _production_like_env() and not args.allow_production:
        print(
            "Refusing production-like backend integrity check without --allow-production.",
            file=sys.stderr,
        )
        return 2

    with psycopg.connect(
        database_url,
        row_factory=psycopg.rows.dict_row,
        connect_timeout=10,
    ) as conn:
        conn.execute("BEGIN READ ONLY")
        try:
            report = collect_bootstrap_report(conn) if args.bootstrap_only else collect_report(conn)
        finally:
            conn.rollback()

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_format_text(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
