import csv
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import gardenops.db as db
from gardenops.routers.plants import PLANT_CSV_EXPORT_COLUMNS
from gardenops.security import create_user
from gardenops.services.media_store import prepare_media_asset
from gardenops.services.plant_cover_import import (
    _sanitize_remote_url,
    discover_cover_from_plant_link,
)
from tests.base import BaseApiTest, strong_password


class TestExportImport(BaseApiTest):
    def _destructive_admin_headers(self, action_reason: str) -> dict[str, str]:
        _, csrf = self._login_session("test_admin", "testadminpass")
        return self._session_headers(
            csrf,
            extra={"x-action-reason": action_reason},
        )

    def test_snapshot_lifecycle(self) -> None:
        """Save, list, restore, and delete a snapshot."""
        self.client.patch(
            "/api/layout-state",
            json={"row": 3, "col": 4, "width": 9, "height": 6, "north_degrees": 181},
        )
        self.client.patch(
            "/api/shademap/state",
            json={
                "mode": "sun-hours",
                "selected_plot_id": "B1",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "autumn",
            },
        )
        self.client.patch(
            "/api/shademap/calibration",
            json={
                "enabled": True,
                "origin_grid_col": 6.5,
                "origin_grid_row": 9.5,
                "origin_latitude": 51.50095,
                "origin_longitude": -0.12448,
                "axis_grid_col": 14.5,
                "axis_grid_row": 9.5,
                "axis_latitude": 51.50095,
                "axis_longitude": -0.12472,
            },
        )
        self.client.post(
            "/api/shademap/obstacles",
            json={
                "label": "Walnut tree",
                "kind": "tree",
                "linked_plot_id": "B2",
                "latitude": 51.50088,
                "longitude": -0.12438,
                "height_m": 6.0,
                "crown_radius_m": 2.8,
                "active": True,
            },
        )
        save_res = self.client.post(
            "/api/snapshots",
            json={"name": "test-snap"},
        )
        self.assertEqual(save_res.status_code, 201)

        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-TEST', 1)
                ON CONFLICT (plot_id, plt_id) DO UPDATE SET quantity = excluded.quantity
                """
            )
            conn.commit()
        finally:
            db.return_db(conn)
        extra_plot = self.client.post(
            "/api/plots",
            json={
                "plot_id": "SNAP-EXTRA",
                "zone_code": "S",
                "zone_name": "Snapshot extra",
                "plot_number": 99,
                "grid_row": 29,
                "grid_col": 22,
            },
        )
        self.assertEqual(extra_plot.status_code, 201, extra_plot.text)
        extra_assignment = self.client.post(
            "/api/plots/SNAP-EXTRA/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.assertEqual(extra_assignment.status_code, 201, extra_assignment.text)

        list_res = self.client.get("/api/snapshots")
        self.assertEqual(list_res.status_code, 200)
        snapshots = list_res.json()
        self.assertGreater(len(snapshots), 0)
        snap_id = snapshots[0]["id"]
        self.assertTrue(snap_id.startswith("snap_"))

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_headers = self._destructive_admin_headers("snapshot-lifecycle-restore")
            restore_res = self.client.post(
                f"/api/snapshots/{snap_id}/restore",
                headers=admin_headers,
            )
        self.assertEqual(restore_res.status_code, 200)
        conn = db.get_db()
        try:
            retained_assignment = conn.execute(
                "SELECT 1 FROM plot_plants WHERE plot_id = 'B1' AND plt_id = 'PLT-TEST'"
            ).fetchone()
            removed_plot = conn.execute(
                "SELECT 1 FROM plots WHERE plot_id = 'SNAP-EXTRA'"
            ).fetchone()
            self.assertIsNotNone(retained_assignment)
            self.assertIsNone(removed_plot)
        finally:
            db.return_db(conn)
        house = self.client.get("/api/layout-state")
        self.assertEqual(house.status_code, 200)
        self.assertEqual(
            house.json(),
            {
                "row": 3,
                "col": 4,
                "width": 9,
                "height": 6,
                "north_degrees": 181,
                "grid_rows": 30,
                "grid_cols": 22,
            },
        )
        shademap = self.client.get("/api/shademap/state")
        self.assertEqual(shademap.status_code, 200)
        self.assertEqual(
            shademap.json(),
            {
                "mode": "sun-hours",
                "selected_plot_id": "B1",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "autumn",
            },
        )
        calibration = self.client.get("/api/shademap/calibration")
        self.assertEqual(calibration.status_code, 200)
        self.assertEqual(calibration.json()["enabled"], True)
        obstacles = self.client.get("/api/shademap/obstacles")
        self.assertEqual(obstacles.status_code, 200)
        self.assertEqual(len(obstacles.json()), 1)
        self.assertEqual(obstacles.json()[0]["label"], "Walnut tree")

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_headers = self._destructive_admin_headers("snapshot-lifecycle-delete")
            del_res = self.client.delete(
                f"/api/snapshots/{snap_id}",
                headers=admin_headers,
            )
        self.assertEqual(del_res.status_code, 200)

    def test_restore_nonexistent_snapshot(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            response = self.client.post(
                "/api/snapshots/99999/restore",
                headers=self._destructive_admin_headers("missing-snapshot-restore"),
            )
        self.assertEqual(response.status_code, 404)

    def test_export_plots(self) -> None:
        self.client.patch(
            "/api/layout-state",
            json={"row": 2, "col": 3, "width": 8, "height": 5, "north_degrees": 274},
        )
        self.client.patch(
            "/api/shademap/state",
            json={
                "mode": "sun-hours",
                "selected_plot_id": "B2",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "winter",
            },
        )
        self.client.patch(
            "/api/shademap/calibration",
            json={
                "enabled": True,
                "origin_grid_col": 6.5,
                "origin_grid_row": 9.5,
                "origin_latitude": 51.50095,
                "origin_longitude": -0.12448,
                "axis_grid_col": 12.5,
                "axis_grid_row": 9.5,
                "axis_latitude": 51.50095,
                "axis_longitude": -0.12465,
            },
        )
        self.client.post(
            "/api/shademap/obstacles",
            json={
                "label": "Apple tree",
                "kind": "tree",
                "linked_plot_id": "B1",
                "latitude": 51.50091,
                "longitude": -0.12439,
                "height_m": 5.0,
                "crown_radius_m": 2.2,
                "active": True,
            },
        )
        response = self.client.get("/api/plots/export")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data["plots"], list)
        self.assertGreater(len(data["plots"]), 0)
        self.assertEqual(
            data["house"],
            {
                "row": 2,
                "col": 3,
                "width": 8,
                "height": 5,
                "north_degrees": 274,
                "grid_rows": 30,
                "grid_cols": 22,
            },
        )
        self.assertEqual(
            data["shademap"],
            {
                "mode": "sun-hours",
                "selected_plot_id": "B2",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "winter",
            },
        )
        self.assertEqual(data["shademap_calibration"]["enabled"], True)
        self.assertEqual(len(data["shademap_obstacles"]), 1)
        self.assertEqual(data["shademap_obstacles"][0]["label"], "Apple tree")

    def test_import_plots(self) -> None:
        self.client.patch(
            "/api/layout-state",
            json={
                "row": 6,
                "col": 7,
                "width": 11,
                "height": 4,
                "north_degrees": 123,
                "grid_rows": 41,
                "grid_cols": 39,
            },
        )
        self.client.patch(
            "/api/shademap/state",
            json={
                "mode": "sun-hours",
                "selected_plot_id": "B1",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "spring",
            },
        )
        self.client.patch(
            "/api/shademap/calibration",
            json={
                "enabled": True,
                "origin_grid_col": 6.5,
                "origin_grid_row": 9.5,
                "origin_latitude": 51.50095,
                "origin_longitude": -0.12448,
                "axis_grid_col": 12.5,
                "axis_grid_row": 9.5,
                "axis_latitude": 51.50095,
                "axis_longitude": -0.12465,
            },
        )
        self.client.post(
            "/api/shademap/obstacles",
            json={
                "label": "Cherry tree",
                "kind": "tree",
                "linked_plot_id": "B1",
                "latitude": 51.50092,
                "longitude": -0.12441,
                "height_m": 4.9,
                "crown_radius_m": 1.9,
                "active": True,
            },
        )
        export_res = self.client.get("/api/plots/export")
        exported = json.loads(export_res.content)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            import_res = self.client.post(
                "/api/plots/import",
                headers=self._destructive_admin_headers("plot-import-round-trip"),
                json=exported,
            )
        self.assertEqual(import_res.status_code, 200)
        self.assertEqual(
            import_res.json()["plots"],
            len(exported["plots"]),
        )
        house = self.client.get("/api/layout-state")
        self.assertEqual(house.status_code, 200)
        self.assertEqual(house.json(), exported["house"])
        shademap = self.client.get("/api/shademap/state")
        self.assertEqual(shademap.status_code, 200)
        self.assertEqual(shademap.json(), exported["shademap"])
        calibration = self.client.get("/api/shademap/calibration")
        self.assertEqual(calibration.status_code, 200)
        self.assertEqual(calibration.json(), exported["shademap_calibration"])
        obstacles = self.client.get("/api/shademap/obstacles")
        self.assertEqual(obstacles.status_code, 200)
        self.assertEqual(len(obstacles.json()), 1)
        self.assertEqual(obstacles.json()[0]["label"], "Cherry tree")

    def test_import_plots_accepts_legacy_cardinal_direction(self) -> None:
        export_res = self.client.get("/api/plots/export")
        exported = json.loads(export_res.content)
        exported["house"]["direction"] = "west"
        exported["house"].pop("north_degrees", None)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            import_res = self.client.post(
                "/api/plots/import",
                headers=self._destructive_admin_headers("legacy-direction-import"),
                json=exported,
            )
        self.assertEqual(import_res.status_code, 200)

        house = self.client.get("/api/layout-state")
        self.assertEqual(house.status_code, 200)
        self.assertEqual(house.json()["north_degrees"], 270)

    def test_map_objects_round_trip_through_layout_export_import(self) -> None:
        garden_id = self._get_default_garden_id()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={
                "object_type": "patio",
                "name": "Kitchen patio",
                "shape_type": "rectangle",
                "geometry": {"x": 1, "y": 1, "width": 4, "height": 3},
                "style": {"color": "#7d9f7a"},
                "z_index": 2,
                "has_internal_layout": True,
                "internal_layout": {"rows": 6, "cols": 8},
            },
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        pot = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units",
            json={
                "unit_type": "pot",
                "name": "Rosemary pot",
                "shape_type": "ellipse",
                "geometry": {"x": 2, "y": 2, "width": 2, "height": 2},
                "style": {"color": "#c58f5c"},
                "sort_order": 1,
            },
        )
        self.assertEqual(pot.status_code, 201, pot.text)

        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        exported = json.loads(export_res.content)
        self.assertEqual(len(exported["map_objects"]), 1)
        self.assertEqual(exported["map_objects"][0]["public_id"], patio_id)
        self.assertEqual(exported["map_objects"][0]["units"][0]["name"], "Rosemary pot")

        delete_res = self.client.delete(f"/api/gardens/{garden_id}/map-objects/{patio_id}")
        self.assertEqual(delete_res.status_code, 200, delete_res.text)
        empty_res = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(empty_res.status_code, 200, empty_res.text)
        self.assertEqual(empty_res.json()["objects"], [])

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            import_res = self.client.post(
                "/api/plots/import",
                headers=self._destructive_admin_headers("map-object-round-trip"),
                json=exported,
            )
        self.assertEqual(import_res.status_code, 200, import_res.text)
        restored_res = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(restored_res.status_code, 200, restored_res.text)
        restored = restored_res.json()["objects"]
        exported_object = exported["map_objects"][0]
        exported_unit = exported_object["units"][0]
        self.assertEqual(len(restored), 1)
        restored_object = restored[0]
        for field in (
            "public_id",
            "object_type",
            "name",
            "shape_type",
            "geometry",
            "style",
            "z_index",
            "has_internal_layout",
            "internal_layout",
        ):
            with self.subTest(field=field):
                self.assertEqual(restored_object[field], exported_object[field])
        self.assertEqual(len(restored_object["units"]), 1)
        restored_unit = restored_object["units"][0]
        for field in (
            "public_id",
            "unit_type",
            "name",
            "shape_type",
            "geometry",
            "style",
            "sort_order",
        ):
            with self.subTest(field=f"unit.{field}"):
                self.assertEqual(restored_unit[field], exported_unit[field])

    def test_export_plants_csv(self) -> None:
        response = self.client.get("/api/plants/export-csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("name", response.text)

    def test_export_plants_csv_includes_all_fields_and_plot_assignments(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE plants
                SET year_planted = %s, deer_resistant = %s,
                    care_watering = %s, care_soil = %s, care_planting = %s,
                    care_maintenance = %s, care_notes = %s
                WHERE plt_id = %s
                """,
                (
                    "2024",
                    1,
                    "Water deeply once per week.",
                    "Use loose, fertile soil.",
                    "Plant after the last frost.",
                    "Deadhead through summer.",
                    "Mulch before winter.",
                    "PLT-TEST",
                ),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                ("B1", "PLT-TEST", 2),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                ("KASSE", "PLT-TEST", 1),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/plants/export-csv")
        self.assertEqual(response.status_code, 200)

        reader = csv.DictReader(io.StringIO(response.text))
        self.assertEqual(reader.fieldnames, PLANT_CSV_EXPORT_COLUMNS)
        rows = list(reader)
        exported = next(row for row in rows if row["plt_id"] == "PLT-TEST")
        self.assertEqual(exported["year_planted"], "2024")
        self.assertEqual(exported["deer_resistant"], "1")
        self.assertEqual(exported["care_watering"], "Water deeply once per week.")
        self.assertEqual(exported["care_soil"], "Use loose, fertile soil.")
        self.assertEqual(exported["care_planting"], "Plant after the last frost.")
        self.assertEqual(exported["care_maintenance"], "Deadhead through summer.")
        self.assertEqual(exported["care_notes"], "Mulch before winter.")
        self.assertEqual(
            json.loads(exported["plot_assignments"]),
            [
                {"plot_id": "B1", "quantity": 2, "seen_growing": None, "seen_growing_date": None},
                {
                    "plot_id": "KASSE",
                    "quantity": 1,
                    "seen_growing": None,
                    "seen_growing_date": None,
                },
            ],
        )

    def test_export_plants_csv_empty_dataset_still_writes_headers(self) -> None:
        conn = db.get_db()
        try:
            conn.execute("DELETE FROM plot_plants")
            conn.execute("DELETE FROM plants")
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/plants/export-csv")
        self.assertEqual(response.status_code, 200)
        reader = csv.reader(io.StringIO(response.text))
        self.assertEqual(next(reader), PLANT_CSV_EXPORT_COLUMNS)
        self.assertEqual(list(reader), [])

    def test_import_plants_csv_round_trip(self) -> None:
        export_res = self.client.get("/api/plants/export-csv")
        self.assertEqual(export_res.status_code, 200)

        import_res = self.client.post(
            "/api/plants/import-csv",
            json={"csv_text": export_res.text},
        )
        self.assertEqual(import_res.status_code, 200)
        payload = import_res.json()
        self.assertGreaterEqual(payload["rows"], 1)
        self.assertGreaterEqual(payload["updated"], 1)

    def test_import_plants_csv_round_trip_restores_all_fields_and_plot_assignments(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE plants
                SET year_planted = %s, deer_resistant = %s,
                    care_watering = %s, care_soil = %s, care_planting = %s,
                    care_maintenance = %s, care_notes = %s
                WHERE plt_id = %s
                """,
                (
                    "2024",
                    1,
                    "Water deeply once per week.",
                    "Use loose, fertile soil.",
                    "Plant after the last frost.",
                    "Deadhead through summer.",
                    "Mulch before winter.",
                    "PLT-TEST",
                ),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (
                    plot_id, plt_id, quantity, seen_growing, seen_growing_date
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                ("B1", "PLT-TEST", 2, 1, "2024-06-12"),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                ("KASSE", "PLT-TEST", 1),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        export_res = self.client.get("/api/plants/export-csv")
        self.assertEqual(export_res.status_code, 200)

        conn = db.get_db()
        try:
            conn.execute("DELETE FROM plot_plants WHERE plt_id = %s", ("PLT-TEST",))
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
                ("B2", "PLT-TEST", 4),
            )
            conn.execute(
                """
                UPDATE plants
                SET year_planted = NULL, deer_resistant = 0,
                    care_watering = '', care_soil = '', care_planting = '',
                    care_maintenance = '', care_notes = ''
                WHERE plt_id = %s
                """,
                ("PLT-TEST",),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        import_res = self.client.post(
            "/api/plants/import-csv",
            json={"csv_text": export_res.text},
        )
        self.assertEqual(import_res.status_code, 200)

        conn = db.get_db()
        try:
            plant = conn.execute(
                """
                SELECT year_planted, deer_resistant, care_watering,
                    care_soil, care_planting, care_maintenance, care_notes
                FROM plants
                WHERE plt_id = %s
                """,
                ("PLT-TEST",),
            ).fetchone()
            assignments = conn.execute(
                """
                SELECT plot_id, quantity, seen_growing, seen_growing_date
                FROM plot_plants
                WHERE plt_id = %s
                ORDER BY plot_id
                """,
                ("PLT-TEST",),
            ).fetchall()
        finally:
            db.return_db(conn)

        assert plant is not None
        self.assertEqual(str(plant["year_planted"]), "2024")
        self.assertEqual(int(plant["deer_resistant"]), 1)
        self.assertEqual(str(plant["care_watering"]), "Water deeply once per week.")
        self.assertEqual(str(plant["care_soil"]), "Use loose, fertile soil.")
        self.assertEqual(str(plant["care_planting"]), "Plant after the last frost.")
        self.assertEqual(str(plant["care_maintenance"]), "Deadhead through summer.")
        self.assertEqual(str(plant["care_notes"]), "Mulch before winter.")
        self.assertEqual(
            [
                (
                    str(row["plot_id"]),
                    int(row["quantity"]),
                    None if row["seen_growing"] is None else bool(row["seen_growing"]),
                    None if row["seen_growing_date"] is None else str(row["seen_growing_date"]),
                )
                for row in assignments
            ],
            [("B1", 2, True, "2024-06-12"), ("KASSE", 1, None, None)],
        )

    def test_import_plants_csv_preserves_plot_assignments(self) -> None:
        self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 2},
        )
        csv_text = (
            "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,link,year_planted,deer_resistant\n"
            "PLT-TEST,Imported Test,Testus plantus,frø,juli,hvit,H4,140,sol,,2025,1\n"
        )
        import_res = self.client.post(
            "/api/plants/import-csv",
            json={"csv_text": csv_text},
        )
        self.assertEqual(import_res.status_code, 200)

        plant = next(
            p
            for p in self.client.get("/api/plants?q=Imported Test").json()
            if p["plt_id"] == "PLT-TEST"
        )
        self.assertIn("B1", plant["plot_ids"])
        self.assertEqual(plant["name"], "Imported Test")

    def test_import_plants_csv_rejects_plot_assignments_outside_active_garden(self) -> None:
        gid1, gid2, username, password = self._setup_admin_two_gardens()
        conn = db.get_db()
        try:
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s",
                (username,),
            ).fetchone()
            assert user is not None
            conn.execute(
                """
                INSERT INTO plots (plot_id, zone_code, zone_name, plot_number, grid_row, grid_col)
                VALUES ('CSV-G2-PLOT', 'X', 'Other Garden', 1, 10, 10)
                """,
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES ('CSV-G2-PLOT', %s, %s)
                """,
                (int(user["id"]), gid2),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        csv_text = (
            "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,link,"
            "year_planted,deer_resistant,care_watering,care_soil,care_planting,"
            "care_maintenance,care_notes,plot_assignments\n"
            "PLT-TEST,Imported Test,Testus plantus,frø,juli,hvit,H4,140,sol,,2025,1,,,,,,"
            '"[{""plot_id"":""CSV-G2-PLOT"",""quantity"":1}]"\n'
        )
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            response = client.post(
                "/api/plants/import-csv",
                headers=self._session_headers(csrf, garden_id=gid1),
                json={"csv_text": csv_text},
            )

        self.assertEqual(response.status_code, 404, response.text)

        conn = db.get_db()
        try:
            leaked = conn.execute(
                """
                SELECT 1
                FROM plot_plants
                WHERE plot_id = 'CSV-G2-PLOT' AND plt_id = 'PLT-TEST'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertIsNone(leaked)

    def test_import_plants_csv_rolls_back_on_assignment_error(self) -> None:
        csv_text = (
            "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,link,"
            "year_planted,deer_resistant,care_watering,care_soil,care_planting,"
            "care_maintenance,care_notes,plot_assignments\n"
            "PLT-TEST,Broken Import,Testus plantus,frø,juli,hvit,H4,140,sol,,2025,1,,,,,,"
            '"[{""plot_id"":""B1"",""quantity"":1}]"\n'
        )

        with patch("gardenops.routers.plants.executemany", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/plants/import-csv",
                    json={"csv_text": csv_text},
                )

        conn = db.get_db()
        try:
            plant = conn.execute(
                "SELECT name, category, year_planted FROM plants WHERE plt_id = %s",
                ("PLT-TEST",),
            ).fetchone()
            assignments = conn.execute(
                "SELECT plot_id FROM plot_plants WHERE plt_id = %s ORDER BY plot_id",
                ("PLT-TEST",),
            ).fetchall()
        finally:
            db.return_db(conn)

        assert plant is not None
        self.assertEqual(str(plant["name"]), "Test Plant")
        self.assertEqual(str(plant["category"]), "froe")
        self.assertFalse(assignments)

    def test_import_plants_csv_creates_owner_row_for_admin_fallback_context(self) -> None:
        garden_id = self._get_default_garden_id()
        csv_text = (
            "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,link,"
            "year_planted,deer_resistant\n"
            "PLT-CSVOWN,CSV Owner Plant,,staude,,,,,,," + "2026,0\n"
        )

        import_res = self.client.post(
            "/api/plants/import-csv",
            json={"csv_text": csv_text},
        )
        self.assertEqual(import_res.status_code, 200)

        conn = db.get_db()
        try:
            owner_row = conn.execute(
                """
                SELECT owner_user_id, garden_id
                FROM plant_ownership
                WHERE plt_id = %s
                """,
                ("PLT-CSVOWN",),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert owner_row is not None
        self.assertEqual(int(owner_row["garden_id"]), garden_id)
        self.assertGreater(int(owner_row["owner_user_id"]), 0)

    def test_non_admin_export_plants_csv_is_owner_scoped(self) -> None:
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
            alice = create_user(
                conn,
                username="plant_export_alice",
                password=strong_password("alicepass123"),
                role="editor",
            )
            bob = create_user(
                conn,
                username="plant_export_bob",
                password=strong_password("bobpass123"),
                role="editor",
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                ("PLT-TEST", int(alice["id"]), default_garden_id),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                ("PLT-002", int(bob["id"]), default_garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session("plant_export_alice", "alicepass123", client=client)
            response = client.get(
                "/api/plants/export-csv",
                headers=self._session_headers(csrf, garden_id=default_garden_id),
            )

        self.assertEqual(response.status_code, 200)
        exported_ids = {row["plt_id"] for row in csv.DictReader(io.StringIO(response.text))}
        # Editors see all plants in their garden (not just their own)
        self.assertIn("PLT-TEST", exported_ids)
        self.assertIn("PLT-002", exported_ids)

    def test_non_admin_import_plants_csv_rejects_other_users_plants(self) -> None:
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
            alice = create_user(
                conn,
                username="plant_import_alice",
                password=strong_password("alicepass123"),
                role="editor",
            )
            bob = create_user(
                conn,
                username="plant_import_bob",
                password=strong_password("bobpass123"),
                role="editor",
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                ("PLT-TEST", int(alice["id"]), default_garden_id),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                ("PLT-002", int(bob["id"]), default_garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        csv_text = (
            "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,link,year_planted,deer_resistant\n"
            "PLT-002,Stolen Rose,Rosa canina,busker,juni,rød,H5,150,sol,,2025,0\n"
        )
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session("plant_import_alice", "alicepass123", client=client)
            response = client.post(
                "/api/plants/import-csv",
                headers=self._session_headers(csrf, garden_id=default_garden_id),
                json={"csv_text": csv_text},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("owned by another user", response.json()["detail"])

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT p.name, po.owner_user_id
                FROM plants p
                JOIN plant_ownership po ON po.plt_id = p.plt_id
                WHERE p.plt_id = %s AND po.garden_id = %s
                """,
                ("PLT-002", default_garden_id),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert row is not None
        self.assertEqual(str(row["name"]), "Rose")
        self.assertEqual(int(row["owner_user_id"]), int(bob["id"]))

    def test_complete_onboarding_import_preserves_imported_layout(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="importadmin",
                password=strong_password("importpass123"),
                role="admin",
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"onboard-import-{os.urandom(3).hex()}", "Import Me"),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("importadmin",),
            ).fetchone()
            assert user is not None
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, int(user["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("importadmin", "importpass123")
            headers = self._session_headers(csrf, garden_id=garden_id)
            response = self.client.post(
                f"/api/gardens/{garden_id}/complete-onboarding",
                headers=headers,
                json={
                    "name": "Imported Garden",
                    "grid_rows": 30,
                    "grid_cols": 22,
                    "latitude": 51.50095,
                    "longitude": -0.12448,
                    "address": "Demo City",
                    "mode": "import",
                    "imported_layout": {
                        "plots": [
                            {
                                "plot_id": "Q1",
                                "zone_code": "Q",
                                "zone_name": "Queue",
                                "plot_number": 1,
                                "grid_row": 2,
                                "grid_col": 3,
                            },
                            {
                                "plot_id": "Q2",
                                "zone_code": "Q",
                                "zone_name": "Queue",
                                "plot_number": 2,
                                "grid_row": 2,
                                "grid_col": 4,
                            },
                        ],
                        "house": {
                            "row": 3,
                            "col": 2,
                            "width": 4,
                            "height": 3,
                            "north_degrees": 90,
                            "grid_rows": 14,
                            "grid_cols": 11,
                        },
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["mode"], "import")
            self.assertEqual(payload["plots_created"], 2)
            self.assertTrue(payload["onboarding_complete"])
            self.assertEqual(payload["grid_rows"], 14)
            self.assertEqual(payload["grid_cols"], 11)

            settings = self.client.get(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
            )
            self.assertEqual(settings.status_code, 200)
            self.assertEqual(settings.json()["name"], "Imported Garden")
            self.assertEqual(settings.json()["address"], "Demo City")
            self.assertEqual(settings.json()["grid_rows"], 14)
            self.assertEqual(settings.json()["grid_cols"], 11)

            layout = self.client.get("/api/layout-state", headers=headers)
            self.assertEqual(layout.status_code, 200)
            self.assertEqual(
                layout.json(),
                {
                    "row": 3,
                    "col": 2,
                    "width": 4,
                    "height": 3,
                    "north_degrees": 90,
                    "grid_rows": 14,
                    "grid_cols": 11,
                },
            )

            plots = self.client.get("/api/plots", headers=headers)
            self.assertEqual(plots.status_code, 200)
            plot_ids = {plot["plot_id"] for plot in plots.json()}
            self.assertTrue({"Q1", "Q2"}.issubset(plot_ids))
            # The INDOOR plot is also created automatically during onboarding
            indoor_plots = {pid for pid in plot_ids if pid.startswith("INDOOR-")}
            self.assertEqual(len(indoor_plots), 1)

    def test_snapshot_restore_only_changes_selected_garden(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session(username, password)
            default_headers = self._session_headers(csrf, garden_id=default_garden_id)
            second_headers = self._session_headers(csrf, garden_id=second_garden_id)

            create_default_plot = self.client.post(
                "/api/plots",
                headers=default_headers,
                json={
                    "plot_id": "DG1",
                    "zone_code": "D",
                    "zone_name": "Default",
                    "plot_number": 1,
                    "grid_row": 10,
                    "grid_col": 10,
                },
            )
            self.assertEqual(create_default_plot.status_code, 201)

            create_second_plot = self.client.post(
                "/api/plots",
                headers=second_headers,
                json={
                    "plot_id": "SG1",
                    "zone_code": "S",
                    "zone_name": "Second",
                    "plot_number": 1,
                    "grid_row": 14,
                    "grid_col": 14,
                },
            )
            self.assertEqual(create_second_plot.status_code, 201)

            export_second = self.client.get("/api/plots/export", headers=second_headers)
            self.assertEqual(export_second.status_code, 200)
            payload = json.loads(export_second.content)
            payload["plots"] = [
                {
                    "plot_id": "SG2",
                    "zone_code": "S",
                    "zone_name": "Second",
                    "plot_number": 2,
                    "grid_row": 15,
                    "grid_col": 15,
                    "sub_zone": "",
                    "notes": "",
                    "color": None,
                },
            ]
            payload["house"] = {
                "row": 4,
                "col": 5,
                "width": 7,
                "height": 6,
                "north_degrees": 210,
            }

            import_second = self.client.post(
                "/api/plots/import",
                headers={
                    **second_headers,
                    "x-action-reason": "second-garden-import",
                },
                json=payload,
            )
            self.assertEqual(import_second.status_code, 200)

            default_plots = self.client.get("/api/plots", headers=default_headers)
            self.assertEqual(default_plots.status_code, 200)
            self.assertIn("DG1", {row["plot_id"] for row in default_plots.json()})
            self.assertNotIn("SG2", {row["plot_id"] for row in default_plots.json()})

            second_plots = self.client.get("/api/plots", headers=second_headers)
            self.assertEqual(second_plots.status_code, 200)
            self.assertIn("SG2", {row["plot_id"] for row in second_plots.json()})
            self.assertNotIn("DG1", {row["plot_id"] for row in second_plots.json()})

            default_layout = self.client.get("/api/layout-state", headers=default_headers)
            self.assertEqual(default_layout.status_code, 200)
            self.assertEqual(
                default_layout.json(),
                {
                    "row": 9,
                    "col": 6,
                    "width": 12,
                    "height": 8,
                    "north_degrees": 0,
                    "grid_rows": 30,
                    "grid_cols": 22,
                },
            )

            second_layout = self.client.get("/api/layout-state", headers=second_headers)
            self.assertEqual(second_layout.status_code, 200)
            self.assertEqual(
                second_layout.json(),
                {
                    "row": 4,
                    "col": 5,
                    "width": 7,
                    "height": 6,
                    "north_degrees": 210,
                    "grid_rows": 30,
                    "grid_cols": 22,
                },
            )

    def test_import_rejects_duplicate_grid_cells(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            response = self.client.post(
                "/api/plots/import",
                headers=self._destructive_admin_headers("duplicate-cell-import"),
                json={
                    "plots": [
                        {
                            "plot_id": "X1",
                            "zone_code": "X",
                            "zone_name": "X",
                            "plot_number": 1,
                            "grid_row": 1,
                            "grid_col": 1,
                            "sub_zone": "",
                            "notes": "",
                            "color": None,
                        },
                        {
                            "plot_id": "X2",
                            "zone_code": "X",
                            "zone_name": "X",
                            "plot_number": 2,
                            "grid_row": 1,
                            "grid_col": 1,
                            "sub_zone": "",
                            "notes": "",
                            "color": None,
                        },
                    ],
                },
            )
        self.assertEqual(response.status_code, 400)

    def test_media_bulk_populate_missing_covers_imports_remote_and_skips_mismatch(self) -> None:
        for plant_id in ("PLT-TEST", "PLT-002"):
            uploaded = self.client.post(
                f"/api/media/upload?target_type=plant&target_id={plant_id}",
                content=self._image_bytes(fmt="PNG", size=(120, 80)),
                headers={
                    "content-type": "image/png",
                    "x-upload-filename": f"{plant_id.lower()}-cover.png",
                },
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.text)

        created_ok = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-REMOTE-OK",
                "name": "Remote OK",
                "latin": "Remoteus plantae",
                "category": "frø",
                "link": "https://example.com/remoteus-plantae",
            },
        )
        self.assertEqual(created_ok.status_code, 201, created_ok.text)

        created_skip = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-REMOTE-SKIP",
                "name": "Remote Skip",
                "latin": "Skipus plantae",
                "category": "frø",
                "link": "https://example.com/not-the-same-plant",
            },
        )
        self.assertEqual(created_skip.status_code, 201, created_skip.text)

        def mock_discover(plant_link: str, latin_name: str):
            if "remoteus-plantae" in plant_link:
                prepared = prepare_media_asset(
                    payload=self._image_bytes(fmt="PNG", size=(200, 150)),
                    declared_content_type="image/png",
                    original_filename="remote-cover.png",
                )
                return SimpleNamespace(
                    prepared_asset=prepared,
                    source_page_url=plant_link,
                    source_image_url="https://cdn.example.com/remoteus-plantae.png",
                    source_title=latin_name,
                )
            raise HTTPException(status_code=422, detail="Latin name did not match the linked page")

        with patch(
            "gardenops.routers.media.discover_cover_from_plant_link", side_effect=mock_discover
        ):
            os.environ["AUTH_REQUIRED"] = "true"
            os.environ["AUTH_MODE"] = "session"
            os.environ["AUTH_API_KEY"] = ""
            try:
                self._create_test_user("remote_cover_admin", "remote-cover-pass", role="admin")
                admin_client = self._new_client()
                _, csrf = self._login_session(
                    "remote_cover_admin",
                    "remote-cover-pass",
                    client=admin_client,
                )
                headers = self._session_headers(csrf)
                headers = self._reauth_and_refresh_headers(
                    admin_client,
                    headers,
                    password=strong_password("remote-cover-pass"),
                )
                result = admin_client.post(
                    "/api/media/plants/populate-missing-covers",
                    headers={
                        **headers,
                        "x-action-reason": "populate-remote-covers",
                    },
                    json={"max_plants": 10},
                )
            finally:
                os.environ["AUTH_REQUIRED"] = "false"
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["total_without_cover_before"], 2)
        self.assertEqual(body["adopted_existing"], 0)
        self.assertEqual(body["imported_remote"], 1)
        self.assertEqual(body["skipped"], 1)
        self.assertEqual(body["remaining_without_cover"], 1)
        self.assertFalse(body["has_more"])
        self.assertCountEqual(
            [item["plant_id"] for item in body["items"]],
            ["PLT-REMOTE-OK", "PLT-REMOTE-SKIP"],
        )

        summary = self.client.post(
            "/api/media/summaries",
            json={"target_type": "plant", "target_ids": ["PLT-REMOTE-OK", "PLT-REMOTE-SKIP"]},
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        summary_items = {item["target_id"]: item["asset"] for item in summary.json()["items"]}
        self.assertIn("PLT-REMOTE-OK", summary_items)
        self.assertEqual(summary_items["PLT-REMOTE-OK"]["original_filename"], "remote-cover.png")
        self.assertTrue(summary_items["PLT-REMOTE-OK"]["is_cover"])
        self.assertNotIn("PLT-REMOTE-SKIP", summary_items)

        report = self.client.get("/api/media/plants/missing-covers?limit=10")
        self.assertEqual(report.status_code, 200, report.text)
        report_items = {item["plant_id"]: item for item in report.json()["items"]}
        self.assertEqual(report_items["PLT-REMOTE-SKIP"]["reason_code"], "remote_error")
        self.assertIn("Latin name did not match", report_items["PLT-REMOTE-SKIP"]["status_detail"])

    def test_plant_cover_import_sanitizes_non_ascii_url_paths(self) -> None:
        with patch("gardenops.services.plant_cover_import._reject_non_public_host"):
            sanitized = _sanitize_remote_url(
                "https://www.plantasjen.no/gulrorgras-moontears®-o29-cm-gronn-548624.html",
            )
        self.assertEqual(
            sanitized,
            "https://www.plantasjen.no/gulrorgras-moontears%C2%AE-o29-cm-gronn-548624.html",
        )

    def test_plant_cover_import_trusts_rhs_source_without_latin_name_match(self) -> None:
        page_url = "https://www.rhs.org.uk/plants/123/example/details"
        image_url = "https://images.rhs.org.uk/example-cover.png"
        html = f"""
        <html>
          <head>
            <title>Generic RHS page</title>
            <meta property=\"og:image\" content=\"{image_url}\" />
          </head>
          <body>
            <h1>Generic RHS page</h1>
          </body>
        </html>
        """.encode()

        def fake_fetch(raw_url: str, *, max_bytes: int, accept_prefixes: tuple[str, ...]):
            if raw_url == page_url:
                return page_url, "text/html", html
            if raw_url == image_url:
                return image_url, "image/png", self._image_bytes(fmt="PNG", size=(200, 150))
            raise AssertionError(f"Unexpected fetch: {raw_url}")

        with patch(
            "gardenops.services.plant_cover_import._fetch_remote_response", side_effect=fake_fetch
        ):
            prepared = discover_cover_from_plant_link(page_url, "Mismatchus plantae")

        self.assertEqual(prepared.source_page_url, page_url)
        self.assertEqual(prepared.source_image_url, image_url)
        self.assertEqual(prepared.prepared_asset.mime_type, "image/png")
