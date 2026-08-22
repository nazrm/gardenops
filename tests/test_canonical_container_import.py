"""Focused v2 layout export/import coverage for canonical containers."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import HTTPException

import gardenops.db as db
import gardenops.main as garden_main
from tests.base import BaseApiTest


class TestCanonicalContainerImport(BaseApiTest):
    def _admin_headers(self, reason: str) -> dict[str, str]:
        _, csrf = self._login_session("test_admin", "testadminpass")
        return self._session_headers(csrf, extra={"x-action-reason": reason})

    def _import(self, payload: dict[str, object], reason: str) -> object:
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            return self.client.post(
                "/api/plots/import",
                headers=self._admin_headers(reason),
                json=payload,
            )

    def _insert_area_and_container(
        self,
        *,
        area_public_id: str,
        container_plot_id: str,
        container_name: str,
    ) -> None:
        garden_id = self._get_default_garden_id()
        now_ms = db.current_timestamp_ms()
        conn = db.get_db()
        try:
            area = conn.execute(
                """
                INSERT INTO garden_map_objects (
                    public_id, garden_id, object_type, name, shape_type,
                    geometry_json, style_json, z_index, has_internal_layout,
                    internal_layout_json, created_by_user_id, created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, 'patio', 'North patio', 'rectangle',
                        '{\"x\":1,\"y\":1,\"width\":4,\"height\":3}',
                        '{\"color\":\"#7d9f7a\"}', 0, 0,
                        '{\"rows\":6,\"cols\":8}',
                        %s, %s, %s)
                RETURNING id
                """,
                (area_public_id, garden_id, self._owner_id, now_ms, now_ms),
            ).fetchone()
            assert area is not None
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, plot_kind, display_name, container_type,
                    parent_map_object_id, environment
                )
                VALUES (%s, %s, 'C', 'Containers', 0, NULL, NULL, 'container',
                        %s, 'pot', %s, 'outdoor')
                """,
                (container_plot_id, garden_id, container_name, int(area["id"])),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (container_plot_id, self._owner_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_export_is_v2_and_contains_container_fields_without_assignments(self) -> None:
        self._insert_area_and_container(
            area_public_id="export-area",
            container_plot_id="EXPORT-CONT",
            container_name="Blue planter",
        )
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plants (plt_id, name, latin, category)
                VALUES (%s, %s, '', 'busker')
                """,
                ("PLT-EXPORT", "Export fixture plant"),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("PLT-EXPORT", self._owner_id, self._get_default_garden_id()),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 3)",
                ("EXPORT-CONT", "PLT-EXPORT"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/plots/export")
        self.assertEqual(response.status_code, 200, response.text)
        payload = json.loads(response.content)

        self.assertEqual(payload["schema_version"], 2)
        container = next(plot for plot in payload["plots"] if plot["plot_id"] == "EXPORT-CONT")
        self.assertEqual(container["plot_kind"], "container")
        self.assertEqual(container["display_name"], "Blue planter")
        self.assertEqual(container["container_type"], "pot")
        self.assertEqual(container["parent_object_public_id"], "export-area")
        self.assertNotIn("plot_plants", json.dumps(payload))
        self.assertNotIn("containers", payload["map_objects"][0])
        self.assertNotIn("units", payload["map_objects"][0])

    def test_v2_restore_preserves_omitted_container_and_resolves_parent(self) -> None:
        self._insert_area_and_container(
            area_public_id="restore-area",
            container_plot_id="KEEP-CONT",
            container_name="Keep planter",
        )
        self._insert_area_and_container(
            area_public_id="restore-area-2",
            container_plot_id="OMIT-CONT",
            container_name="Omitted planter",
        )
        exported = json.loads(self.client.get("/api/plots/export").content)
        exported["plots"] = [plot for plot in exported["plots"] if plot["plot_id"] != "OMIT-CONT"]

        response = self._import(exported, "canonical-container-restore")
        self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            rows = conn.execute(
                """
                SELECT p.plot_id, p.parent_map_object_id, o.public_id AS parent_public_id
                FROM plots p
                LEFT JOIN garden_map_objects o ON o.id = p.parent_map_object_id
                WHERE p.plot_id IN ('KEEP-CONT', 'OMIT-CONT')
                ORDER BY p.plot_id
                """,
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["plot_id"], "KEEP-CONT")
            self.assertEqual(rows[0]["parent_public_id"], "restore-area")
            self.assertEqual(rows[1]["plot_id"], "OMIT-CONT")
            self.assertEqual(rows[1]["parent_public_id"], "restore-area-2")
        finally:
            db.return_db(conn)

        without_parent_area = dict(exported)
        without_parent_area["map_objects"] = [
            item for item in exported["map_objects"] if item["public_id"] != "restore-area-2"
        ]
        response = self._import(without_parent_area, "canonical-container-detach")
        self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT parent_map_object_id FROM plots WHERE plot_id = 'OMIT-CONT'",
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertIsNone(row["parent_map_object_id"])
        finally:
            db.return_db(conn)

    def test_restore_rejects_archiving_an_assigned_container(self) -> None:
        self._insert_area_and_container(
            area_public_id="archive-area",
            container_plot_id="ARCHIVE-CONT",
            container_name="Assigned planter",
        )
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plants (plt_id, name, latin, category) VALUES (%s, %s, '', 'busker')",
                ("ARCHIVE-PLANT", "Assigned plant"),
            )
            conn.execute(
                "INSERT INTO plant_ownership "
                "(plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
                ("ARCHIVE-PLANT", self._owner_id, garden_id),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 1)",
                ("ARCHIVE-CONT", "ARCHIVE-PLANT"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        payload = json.loads(self.client.get("/api/plots/export").content)
        for plot in payload["plots"]:
            if plot["plot_id"] == "ARCHIVE-CONT":
                plot["archived_at_ms"] = db.current_timestamp_ms()

        response = self._import(payload, "archive-assigned-container")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("plant assignments", response.json()["detail"])

        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT archived_at_ms FROM plots WHERE plot_id = 'ARCHIVE-CONT'",
            ).fetchone()
            assignment = conn.execute(
                "SELECT quantity FROM plot_plants WHERE plot_id = 'ARCHIVE-CONT'",
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNotNone(assignment)
            assert row is not None
            self.assertIsNone(row["archived_at_ms"])
        finally:
            db.return_db(conn)

    def test_v2_container_id_conflict_fails_before_replacement(self) -> None:
        self._insert_area_and_container(
            area_public_id="conflict-area",
            container_plot_id="CONFLICT-CONT",
            container_name="Existing container",
        )
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE plots
                SET plot_kind = 'ground', display_name = NULL, container_type = NULL,
                    parent_map_object_id = NULL
                WHERE plot_id = 'CONFLICT-CONT'
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        payload = {
            "schema_version": 2,
            "plots": [
                {
                    "plot_id": "CONFLICT-CONT",
                    "zone_code": "C",
                    "zone_name": "Containers",
                    "plot_number": 0,
                    "grid_row": None,
                    "grid_col": None,
                    "plot_kind": "container",
                    "display_name": "New container",
                    "container_type": "pot",
                    "parent_object_public_id": None,
                    "environment": "outdoor",
                    "archived_at_ms": None,
                },
            ],
            "map_objects": [],
        }
        response = self._import(payload, "canonical-container-conflict")
        self.assertEqual(response.status_code, 409, response.text)

        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT plot_kind, display_name FROM plots WHERE plot_id = %s",
                ("CONFLICT-CONT",),
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["plot_kind"], "ground")
            self.assertIsNone(row["display_name"])
        finally:
            db.return_db(conn)

    def test_v1_units_are_translated_by_map_object_import_contract(self) -> None:
        payload = {
            "schema_version": 1,
            "plots": [
                {
                    "plot_id": "V1-PLOT",
                    "zone_code": "A",
                    "zone_name": "Area",
                    "plot_number": 1,
                    "grid_row": 1,
                    "grid_col": 1,
                },
                {
                    "plot_id": "V1-INDOOR",
                    "zone_code": "I",
                    "zone_name": "Indoors",
                    "plot_number": 0,
                    "grid_row": 20,
                    "grid_col": 20,
                },
            ],
            "map_objects": [
                {
                    "public_id": "legacy-area",
                    "object_type": "patio",
                    "name": "Legacy patio",
                    "shape_type": "rectangle",
                    "geometry": {"x": 1, "y": 1, "width": 3, "height": 2},
                    "has_internal_layout": True,
                    "internal_layout": {"rows": 6, "cols": 8},
                    "units": [
                        {
                            "public_id": "legacy-unit",
                            "unit_type": "shelf",
                            "name": "Seed shelf",
                            "shape_type": "ellipse",
                            "geometry": {"x": 1, "y": 1, "width": 1, "height": 1},
                        },
                    ],
                },
            ],
        }
        response = self._import(payload, "legacy-unit-import")
        self.assertEqual(response.status_code, 200, response.text)

        repeat = self._import(payload, "legacy-unit-import-repeat")
        self.assertEqual(repeat.status_code, 200, repeat.text)

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT plot_kind, display_name, container_type, parent_map_object_id
                FROM plots
                WHERE plot_id = 'CONT-' || md5('legacy-unit')
                """,
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["plot_kind"], "container")
            self.assertEqual(row["display_name"], "Seed shelf")
            self.assertEqual(row["container_type"], "other")
            parent = conn.execute(
                "SELECT public_id FROM garden_map_objects WHERE id = %s",
                (row["parent_map_object_id"],),
            ).fetchone()
            self.assertEqual(parent["public_id"], "legacy-area")
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM plots WHERE plot_id = 'CONT-' || md5('legacy-unit')",
            ).fetchone()
            self.assertEqual(int(count["count"]), 1)
            indoor = conn.execute(
                """
                SELECT plot_kind, environment, grid_row, grid_col
                FROM plots
                WHERE plot_id = 'V1-INDOOR'
                """,
            ).fetchone()
            self.assertIsNotNone(indoor)
            assert indoor is not None
            self.assertEqual(indoor["plot_kind"], "indoor")
            self.assertEqual(indoor["environment"], "indoor")
            self.assertIsNone(indoor["grid_row"])
            self.assertIsNone(indoor["grid_col"])
        finally:
            db.return_db(conn)

    def test_concurrent_restore_cannot_take_over_a_plot_across_gardens(self) -> None:
        first_garden_id, second_garden_id, _, _ = self._setup_admin_two_gardens()
        payload = [
            {
                "plot_id": "RESTORE-RACE-PLOT",
                "zone_code": "A",
                "zone_name": "Race area",
                "plot_number": 1,
                "grid_row": 5,
                "grid_col": 5,
                "plot_kind": "ground",
                "environment": "outdoor",
            },
        ]
        barrier = threading.Barrier(2)
        actual_lock = garden_main.lock_garden_layout

        def synchronized_lock(conn: object, garden_id: int) -> None:
            barrier.wait(timeout=5)
            actual_lock(conn, garden_id)  # type: ignore[arg-type]

        def restore(garden_id: int) -> int:
            conn = db.get_db()
            try:
                try:
                    return garden_main.restore_snapshot_data(
                        conn,
                        payload,
                        garden_id=garden_id,
                        owner_user_id=self._owner_id,
                        schema_version=2,
                    )
                except HTTPException as exc:
                    return exc.status_code
            finally:
                db.return_db(conn)

        with patch.object(garden_main, "lock_garden_layout", synchronized_lock):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(restore, (first_garden_id, second_garden_id)),
                )

        self.assertCountEqual(results, [1, 409])
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT garden_id FROM plots WHERE plot_id = 'RESTORE-RACE-PLOT'",
            ).fetchone()
            ownership = conn.execute(
                "SELECT garden_id FROM plot_ownership WHERE plot_id = 'RESTORE-RACE-PLOT'",
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNotNone(ownership)
            assert row is not None
            assert ownership is not None
            self.assertIn(int(row["garden_id"]), {first_garden_id, second_garden_id})
            self.assertEqual(int(row["garden_id"]), int(ownership["garden_id"]))
        finally:
            db.return_db(conn)
