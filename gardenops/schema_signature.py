"""Backend schema signature checks shared by startup and integrity auditing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

REQUIRED_TABLES = (
    "schema_migrations",
    "auth_users",
    "audit_events",
    "gardens",
    "plots",
    "plot_ownership",
    "plants",
    "plant_ownership",
    "garden_tasks",
    "garden_task_plots",
    "garden_issues",
    "garden_issue_plots",
    "garden_journal_entries",
    "garden_journal_entry_plots",
    "harvest_entries",
    "harvest_entry_plots",
    "garden_calendar_events",
    "garden_calendar_event_plots",
    "media_assets",
    "media_links",
    "shademap_state",
    "shademap_obstacles",
)

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "schema_migrations": ("version", "applied_at"),
    "auth_users": (
        "id",
        "username",
        "role",
        "is_active",
        "must_change_password",
        "mfa_totp_enabled",
        "subscription_tier",
    ),
    "audit_events": ("id", "actor_user_id", "actor_username", "garden_id"),
    "gardens": ("id", "slug", "name", "owner_user_id"),
    "plots": ("plot_id", "garden_id", "zone_code", "zone_name", "grid_row", "grid_col"),
    "plot_ownership": ("plot_id", "owner_user_id", "garden_id"),
    "plants": ("plt_id", "name", "category"),
    "plant_ownership": ("plt_id", "owner_user_id", "garden_id"),
    "garden_tasks": ("id", "garden_id", "created_by_user_id", "completed_by_user_id"),
    "garden_task_plots": ("task_id", "plot_id"),
    "garden_issues": ("id", "garden_id", "created_by_user_id", "resolved_by_user_id"),
    "garden_issue_plots": ("issue_id", "plot_id"),
    "garden_journal_entries": ("id", "garden_id", "actor_user_id"),
    "garden_journal_entry_plots": ("entry_id", "plot_id"),
    "harvest_entries": ("id", "garden_id", "actor_user_id"),
    "harvest_entry_plots": ("entry_id", "plot_id"),
    "garden_calendar_events": ("id", "garden_id"),
    "garden_calendar_event_plots": ("event_id", "plot_id"),
    "media_assets": ("asset_id", "garden_id", "actor_user_id"),
    "media_links": ("asset_id", "target_type", "target_id"),
    "shademap_state": ("id", "garden_id", "selected_plot_id"),
    "shademap_obstacles": ("id", "garden_id", "linked_plot_id"),
}

REQUIRED_INDEXES = (
    "ux_plots_garden_grid_cell",
    "idx_plots_garden",
    "idx_plot_ownership_garden",
    "idx_plant_ownership_garden",
    "idx_gipl_plot",
    "idx_gtpl_plot",
    "idx_gjepl_plot",
    "idx_hepl_plot",
    "idx_garden_calendar_event_plots_plot",
    "idx_media_links_target",
    "idx_shademap_state_garden",
    "idx_shademap_obstacles_garden",
)

REQUIRED_CONSTRAINTS = (
    "schema_migrations_pkey",
    "auth_users_pkey",
    "gardens_pkey",
    "plots_pkey",
    "plot_ownership_pkey",
    "fk_plots_garden_id_gardens",
    "plants_pkey",
    "plant_ownership_pkey",
    "media_links_pkey",
    "fk_plot_ownership_plot_id_plots",
    "fk_plant_ownership_plt_id_plants",
    "fk_media_links_asset_id_media_assets",
    "fk_gardens_owner_user_id_auth_users",
    "fk_shademap_state_garden_id_gardens",
)

_IGNORED_BOOTSTRAP_TABLES = frozenset({"schema_migrations"})


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: set[str]
    columns: dict[str, set[str]]
    indexes: set[str]
    constraints: set[str]


def collect_schema_snapshot(conn: psycopg.Connection[Any]) -> SchemaSnapshot:
    table_rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """,
    ).fetchall()
    tables = {str(row["table_name"]) for row in table_rows}

    column_rows = conn.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """,
    ).fetchall()
    columns: dict[str, set[str]] = {}
    for row in column_rows:
        columns.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))

    index_rows = conn.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
        """,
    ).fetchall()
    indexes = {str(row["indexname"]) for row in index_rows}

    constraint_rows = conn.execute(
        """
        SELECT conname
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        """,
    ).fetchall()
    constraints = {str(row["conname"]) for row in constraint_rows}

    return SchemaSnapshot(
        tables=tables,
        columns=columns,
        indexes=indexes,
        constraints=constraints,
    )


def missing_schema_parts(
    snapshot: SchemaSnapshot,
    *,
    required_tables: tuple[str, ...] = REQUIRED_TABLES,
    required_columns: Mapping[str, tuple[str, ...]] = REQUIRED_COLUMNS,
    required_indexes: tuple[str, ...] = REQUIRED_INDEXES,
    required_constraints: tuple[str, ...] = REQUIRED_CONSTRAINTS,
) -> list[dict[str, object]]:
    missing: list[dict[str, object]] = []
    for table in required_tables:
        if table not in snapshot.tables:
            missing.append({"kind": "table", "object": table})
    for table, columns in required_columns.items():
        actual = snapshot.columns.get(table, set())
        for column in columns:
            if column not in actual:
                missing.append({"kind": "column", "object": f"{table}.{column}"})
    for index in required_indexes:
        if index not in snapshot.indexes:
            missing.append({"kind": "index", "object": index})
    for constraint in required_constraints:
        if constraint not in snapshot.constraints:
            missing.append({"kind": "constraint", "object": constraint})
    return missing


def format_schema_part(part: Mapping[str, object]) -> str:
    return f"{part['kind']}:{part['object']}"


def existing_public_schema_tables(snapshot: SchemaSnapshot) -> set[str]:
    return snapshot.tables - _IGNORED_BOOTSTRAP_TABLES


def bootstrap_schema_diagnostics_from_snapshot(
    snapshot: SchemaSnapshot,
) -> dict[str, object]:
    existing_tables = sorted(existing_public_schema_tables(snapshot))
    if not existing_tables:
        return {
            "mode": "empty",
            "can_stamp_migrations": False,
            "existing_tables": [],
            "missing": [],
        }

    missing = missing_schema_parts(snapshot)
    return {
        "mode": "verified-baseline" if not missing else "incomplete-existing-schema",
        "can_stamp_migrations": not missing,
        "existing_tables": existing_tables,
        "missing": missing,
    }
