"""Focused schema tests for canonical container plots migration 0031."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

import psycopg

import gardenops.db as db
from gardenops.schema_signature import (
    REQUIRED_COLUMN_DEFAULTS,
    REQUIRED_COLUMN_NULLABILITY,
    REQUIRED_COLUMN_TYPES,
    REQUIRED_COLUMNS,
    REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS,
    REQUIRED_CONSTRAINTS,
    REQUIRED_INDEX_DEFINITION_FRAGMENTS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SchemaSnapshot,
    bootstrap_schema_diagnostics_from_snapshot,
    collect_schema_snapshot,
    missing_schema_parts,
)

MIGRATION_SQL = (
    Path(__file__).parents[1] / "migrations/0031_canonical_container_plots.sql"
).read_text(encoding="utf-8")


def _complete_schema_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        tables=set(REQUIRED_TABLES),
        columns={table: set(columns) for table, columns in REQUIRED_COLUMNS.items()},
        indexes=set(REQUIRED_INDEXES),
        constraints=set(REQUIRED_CONSTRAINTS),
        column_nullability=dict(REQUIRED_COLUMN_NULLABILITY),
        column_types=dict(REQUIRED_COLUMN_TYPES),
        column_defaults=dict(REQUIRED_COLUMN_DEFAULTS),
        index_definitions={
            name: " ".join(fragments)
            for name, fragments in REQUIRED_INDEX_DEFINITION_FRAGMENTS.items()
        },
        constraint_definitions={
            name: " ".join(fragments)
            for name, fragments in REQUIRED_CONSTRAINT_DEFINITION_FRAGMENTS.items()
        },
    )


class CanonicalContainerPlotsSchemaTests(unittest.TestCase):
    def test_current_schema_signature_covers_migration_surface(self) -> None:
        conn = db.get_db()
        try:
            snapshot = collect_schema_snapshot(conn)
            self.assertEqual(missing_schema_parts(snapshot), [])
            self.assertFalse(snapshot.column_nullability["plots.plot_kind"])
            self.assertFalse(snapshot.column_nullability["plots.environment"])
            self.assertEqual(snapshot.column_types["plots.parent_map_object_id"], "bigint")
            self.assertEqual(snapshot.column_types["plots.container_position_x"], "integer")
            self.assertEqual(snapshot.column_types["plots.container_position_y"], "integer")
            self.assertIn("ux_plots_active_container_position", snapshot.indexes)
            self.assertIn("ck_plots_container_position_pair", snapshot.constraints)
            self.assertEqual(snapshot.column_defaults["plots.plot_kind"], "'ground'::text")
            self.assertEqual(snapshot.column_defaults["plots.environment"], "'outdoor'::text")
        finally:
            db.return_db(conn)

    def test_container_position_coordinates_must_be_a_pair(self) -> None:
        conn = db.get_db()
        try:
            suffix = uuid.uuid4().hex
            user = conn.execute(
                """
                INSERT INTO auth_users (username, password_hash, role)
                VALUES (%s, 'test-only-hash', 'admin')
                RETURNING id
                """,
                (f"schema-0032-{suffix}",),
            ).fetchone()
            assert user is not None
            garden = conn.execute(
                """
                INSERT INTO gardens (slug, name, owner_user_id)
                VALUES (%s, 'Schema 0032 garden', %s)
                RETURNING id
                """,
                (f"schema-0032-{suffix}", int(user["id"])),
            ).fetchone()
            assert garden is not None
            conn.execute("SAVEPOINT container_position_pair")
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    """
                    INSERT INTO plots (
                        plot_id, garden_id, zone_code, zone_name, plot_number,
                        plot_kind, display_name, container_type,
                        container_position_x
                    )
                    VALUES (%s, %s, 'C', 'Containers', 0,
                            'container', 'Position pair', 'pot', 0)
                    """,
                    (f"schema0032_pair_{suffix}", int(garden["id"])),
                )
            conn.execute("ROLLBACK TO SAVEPOINT container_position_pair")
            conn.execute("RELEASE SAVEPOINT container_position_pair")
        finally:
            conn.rollback()
            db.return_db(conn)

    def test_pre_0031_bootstrap_can_stamp_existing_history(self) -> None:
        snapshot = _complete_schema_snapshot()
        for column in REQUIRED_COLUMNS["plots"]:
            if column in {
                "plot_kind",
                "display_name",
                "container_type",
                "parent_map_object_id",
                "container_position_x",
                "container_position_y",
                "environment",
                "archived_at_ms",
            }:
                snapshot.columns["plots"].remove(column)
                snapshot.column_nullability.pop(f"plots.{column}", None)
                snapshot.column_types.pop(f"plots.{column}", None)
                snapshot.column_defaults.pop(f"plots.{column}", None)
        snapshot.indexes.remove("idx_plots_active_containers")
        snapshot.indexes.remove("ux_plots_active_container_position")
        snapshot.constraints.difference_update(
            {
                "ck_plots_plot_kind",
                "ck_plots_environment",
                "ck_plots_container_subtype",
                "fk_plots_parent_map_object_garden",
                "ck_plots_container_position_pair",
            }
        )

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 30)

    def test_pre_0032_bootstrap_can_stamp_existing_history(self) -> None:
        snapshot = _complete_schema_snapshot()
        for column in ("container_position_x", "container_position_y"):
            snapshot.columns["plots"].remove(column)
            snapshot.column_nullability.pop(f"plots.{column}")
            snapshot.column_types.pop(f"plots.{column}")
            snapshot.column_defaults.pop(f"plots.{column}")
        snapshot.indexes.remove("ux_plots_active_container_position")
        snapshot.constraints.remove("ck_plots_container_position_pair")

        diagnostics = bootstrap_schema_diagnostics_from_snapshot(snapshot)

        self.assertEqual(diagnostics["mode"], "verified-upgrade-baseline")
        self.assertTrue(diagnostics["can_stamp_migrations"])
        self.assertEqual(diagnostics["stamp_through"], 31)

    def test_migration_converts_legacy_units_and_is_idempotent(self) -> None:
        conn = db.get_db()
        try:
            # Recreate the pre-0031 surface inside this transaction so the test
            # exercises the actual migration rather than only its final schema.
            conn.execute("DROP INDEX IF EXISTS public.ux_plots_active_container_position")
            conn.execute("DROP INDEX IF EXISTS public.idx_plots_active_containers")
            conn.execute(
                """
                ALTER TABLE public.plots
                    DROP CONSTRAINT IF EXISTS ck_plots_container_position_pair,
                    DROP CONSTRAINT IF EXISTS fk_plots_parent_map_object_garden,
                    DROP CONSTRAINT IF EXISTS ck_plots_container_subtype,
                    DROP CONSTRAINT IF EXISTS ck_plots_environment,
                    DROP CONSTRAINT IF EXISTS ck_plots_plot_kind,
                    DROP COLUMN IF EXISTS archived_at_ms,
                    DROP COLUMN IF EXISTS container_position_y,
                    DROP COLUMN IF EXISTS container_position_x,
                    DROP COLUMN IF EXISTS environment,
                    DROP COLUMN IF EXISTS parent_map_object_id,
                    DROP COLUMN IF EXISTS container_type,
                    DROP COLUMN IF EXISTS display_name,
                    DROP COLUMN IF EXISTS plot_kind
                """
            )

            suffix = uuid.uuid4().hex
            username = f"schema-0031-{suffix}"
            user = conn.execute(
                """
                INSERT INTO public.auth_users (username, password_hash, role)
                VALUES (%s, 'test-only-hash', 'admin')
                RETURNING id
                """,
                (username,),
            ).fetchone()
            assert user is not None
            user_id = int(user["id"])
            garden = conn.execute(
                """
                INSERT INTO public.gardens (slug, name, owner_user_id)
                VALUES (%s, 'Schema 0031 garden', %s)
                RETURNING id
                """,
                (f"schema-0031-{suffix}", user_id),
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            conn.execute(
                """
                INSERT INTO public.garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, user_id),
            )
            map_object = conn.execute(
                """
                INSERT INTO public.garden_map_objects (
                    public_id, garden_id, object_type, name, shape_type,
                    geometry_json, style_json, created_by_user_id,
                    created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, 'patio', 'Legacy patio', 'rectangle', '{}', '{}', %s, 1, 1)
                RETURNING id
                """,
                (f"schema0031_object_{suffix}", garden_id, user_id),
            ).fetchone()
            assert map_object is not None
            map_object_id = int(map_object["id"])
            unit_public_id = f"schema0031_unit_{suffix}"
            conn.execute(
                """
                INSERT INTO public.garden_map_object_units (
                    public_id, garden_id, map_object_id, unit_type, name,
                    shape_type, geometry_json, style_json, created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, %s, 'shelf', 'Seed shelf', 'ellipse', '{}', '{}', 1, 1)
                """,
                (unit_public_id, garden_id, map_object_id),
            )
            conn.execute(
                """
                INSERT INTO public.plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col
                )
                VALUES (%s, %s, 'I', 'Indoor', 0, NULL, NULL)
                """,
                (f"schema0031_indoor_{suffix}", garden_id),
            )

            conn.execute(MIGRATION_SQL)

            container = conn.execute(
                """
                SELECT plot_id, garden_id, zone_code, zone_name, plot_number,
                       grid_row, grid_col, plot_kind, display_name, container_type,
                       parent_map_object_id, environment
                FROM public.plots
                WHERE plot_id = 'CONT-' || md5(%s)
                """,
                (unit_public_id,),
            ).fetchone()
            self.assertIsNotNone(container)
            assert container is not None
            self.assertEqual(container["garden_id"], garden_id)
            self.assertEqual(container["zone_code"], "C")
            self.assertEqual(container["zone_name"], "Containers")
            self.assertEqual(container["plot_number"], 0)
            self.assertIsNone(container["grid_row"])
            self.assertIsNone(container["grid_col"])
            self.assertEqual(container["plot_kind"], "container")
            self.assertEqual(container["display_name"], "Seed shelf")
            self.assertEqual(container["container_type"], "other")
            self.assertEqual(container["parent_map_object_id"], map_object_id)
            self.assertEqual(container["environment"], "outdoor")

            ownership = conn.execute(
                """
                SELECT owner_user_id, garden_id
                FROM public.plot_ownership
                WHERE plot_id = %s
                """,
                (container["plot_id"],),
            ).fetchone()
            self.assertIsNotNone(ownership)
            assert ownership is not None
            self.assertEqual(ownership["owner_user_id"], user_id)
            self.assertEqual(ownership["garden_id"], garden_id)

            indoor = conn.execute(
                """
                SELECT plot_kind, environment
                FROM public.plots
                WHERE plot_id = %s
                """,
                (f"schema0031_indoor_{suffix}",),
            ).fetchone()
            self.assertEqual(indoor, {"plot_kind": "indoor", "environment": "indoor"})

            container_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM public.plots
                WHERE plot_kind = 'container' AND plot_id = %s
                """,
                (container["plot_id"],),
            ).fetchone()
            conn.execute(MIGRATION_SQL)
            rerun_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM public.plots
                WHERE plot_kind = 'container' AND plot_id = %s
                """,
                (container["plot_id"],),
            ).fetchone()
            self.assertEqual(rerun_count["count"], container_count["count"])
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM public.plot_ownership WHERE plot_id = %s",
                    (container["plot_id"],),
                ).fetchone()["count"],
                1,
            )

            conn.execute("SAVEPOINT canonical_container_check")
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    """
                    INSERT INTO public.plots (
                        plot_id, garden_id, zone_code, zone_name, plot_number,
                        plot_kind, display_name, container_type
                    )
                    VALUES (%s, %s, 'C', 'Containers', 0, 'container', '', 'pot')
                    """,
                    (f"schema0031_invalid_{suffix}", garden_id),
                )
            conn.execute("ROLLBACK TO SAVEPOINT canonical_container_check")
            conn.execute("RELEASE SAVEPOINT canonical_container_check")

            second_garden = conn.execute(
                """
                INSERT INTO public.gardens (slug, name, owner_user_id)
                VALUES (%s, 'Other garden', %s)
                RETURNING id
                """,
                (f"schema-0031-other-{suffix}", user_id),
            ).fetchone()
            assert second_garden is not None
            conn.execute("SAVEPOINT canonical_container_fk")
            conn.execute(
                """
                INSERT INTO public.plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    plot_kind, display_name, container_type, parent_map_object_id
                )
                VALUES (%s, %s, 'C', 'Containers', 0, 'container', 'Wrong garden', 'pot', %s)
                """,
                (f"schema0031_cross_garden_{suffix}", int(second_garden["id"]), map_object_id),
            )
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute("SET CONSTRAINTS fk_plots_parent_map_object_garden IMMEDIATE")
            conn.execute("ROLLBACK TO SAVEPOINT canonical_container_fk")
            conn.execute("RELEASE SAVEPOINT canonical_container_fk")

            conn.execute(
                "DELETE FROM public.garden_map_objects WHERE id = %s",
                (map_object_id,),
            )
            unparented = conn.execute(
                "SELECT parent_map_object_id FROM public.plots WHERE plot_id = %s",
                (container["plot_id"],),
            ).fetchone()
            self.assertIsNotNone(unparented)
            self.assertIsNone(unparented["parent_map_object_id"])
        finally:
            conn.rollback()
            db.return_db(conn)
