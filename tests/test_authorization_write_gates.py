from datetime import date
from unittest.mock import patch

import gardenops.db as db
from tests.base import BaseApiTest

AUTH_ENV = {
    "AUTH_REQUIRED": "true",
    "AUTH_MODE": "session",
    "AUTH_API_KEY": "",
}


class TestAuthorizationWriteGates(BaseApiTest):
    def _viewer_client(self, username: str = "write_gate_viewer"):
        viewer = self._create_test_user(username, "viewerpass", role="viewer")
        client, headers = self._authenticated_client(username, "viewerpass")
        return viewer, client, headers

    def _give_viewer_seed_ownership(self, viewer_id: int) -> int:
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            for plot_id in ("B1", "B2"):
                conn.execute(
                    """
                    UPDATE plot_ownership
                    SET owner_user_id = %s
                    WHERE plot_id = %s AND garden_id = %s
                    """,
                    (viewer_id, plot_id, garden_id),
                )
            for plt_id in ("PLT-TEST", "PLT-002"):
                conn.execute(
                    """
                    UPDATE plant_ownership
                    SET owner_user_id = %s
                    WHERE plt_id = %s AND garden_id = %s
                    """,
                    (viewer_id, plt_id, garden_id),
                )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-TEST', 1)
                ON CONFLICT(plot_id, plt_id) DO NOTHING
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)
        return garden_id

    def test_viewer_cannot_mutate_owned_plants(self) -> None:
        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("plant_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))

            csv_text = (
                "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,light,"
                "link,year_planted,deer_resistant,care_watering,care_soil,care_planting,"
                "care_maintenance,care_notes\n"
                "PLT-TEST,Viewer Edit,,frø,,,,,,,,0,,,,,\n"
            )
            imported = client.post(
                "/api/plants/import-csv",
                headers=headers,
                json={"csv_text": csv_text},
            )
            patched = client.patch(
                "/api/plants/PLT-TEST",
                headers=headers,
                json={"name": "Viewer renamed plant"},
            )
            deleted = client.delete("/api/plants/PLT-TEST", headers=headers)

        self.assertEqual(imported.status_code, 403, imported.text)
        self.assertEqual(patched.status_code, 403, patched.text)
        self.assertEqual(deleted.status_code, 403, deleted.text)

    def test_viewer_cannot_mutate_owned_plots_or_assignments(self) -> None:
        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("plot_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))

            create_plot = client.post(
                "/api/plots",
                headers=headers,
                json={
                    "plot_id": "VIEWER-PLOT",
                    "zone_code": "V",
                    "zone_name": "Viewer",
                    "plot_number": 9,
                    "grid_row": 8,
                    "grid_col": 8,
                },
            )
            update_plot = client.patch(
                "/api/plots/B1",
                headers=headers,
                json={"color": "#112233"},
            )
            batch_move = client.post(
                "/api/plots/batch-move",
                headers=headers,
                json={
                    "moves": [
                        {"plot_id": "B1", "grid_row": 2, "grid_col": 1},
                        {"plot_id": "B2", "grid_row": 2, "grid_col": 2},
                    ],
                },
            )
            add_assignment = client.post(
                "/api/plots/B2/plants/PLT-TEST",
                headers=headers,
                json={"quantity": 2},
            )
            update_assignment = client.patch(
                "/api/plots/B1/plants/PLT-TEST",
                headers=headers,
                json={"quantity": 3},
            )
            move_assignment = client.post(
                "/api/plots/B1/plants/PLT-TEST/move/B2",
                headers=headers,
            )
            remove_assignment = client.delete(
                "/api/plots/B1/plants/PLT-TEST",
                headers=headers,
            )
            delete_plot = client.delete("/api/plots/B2", headers=headers)

        for response in (
            create_plot,
            update_plot,
            batch_move,
            add_assignment,
            update_assignment,
            move_assignment,
            remove_assignment,
            delete_plot,
        ):
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 403, response.text)

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_viewer_cannot_start_workflow_tasks(self, mock_svc_date, mock_router_date) -> None:
        fake_today = date(2026, 3, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("workflow_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))
            response = client.post(
                "/api/workflows/start",
                headers=headers,
                json={"workflow_id": "spring_prep", "selected_steps": ["assess_damage"]},
            )

        self.assertEqual(response.status_code, 403, response.text)
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM garden_tasks
                WHERE rule_source LIKE 'workflow:spring_prep:assess_damage:%'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertEqual(int(row["count"]), 0)

    def test_viewer_cannot_mutate_shademap_state(self) -> None:
        with patch.dict("os.environ", AUTH_ENV, clear=False):
            viewer, client, headers = self._viewer_client("shademap_write_gate_viewer")
            self._give_viewer_seed_ownership(int(viewer["id"]))

            elevation = client.patch(
                "/api/plots/elevations",
                headers=headers,
                json={"overrides": {"B1": 41.0}},
            )
            state = client.patch(
                "/api/shademap/state",
                headers=headers,
                json={
                    "mode": "sun-hours",
                    "selected_plot_id": "B1",
                    "analysis_timestamp_ms": 1772443603995,
                    "preset": "summer",
                },
            )
            calibration = client.patch(
                "/api/shademap/calibration",
                headers=headers,
                json={"enabled": False},
            )
            obstacle = client.post(
                "/api/shademap/obstacles",
                headers=headers,
                json={
                    "kind": "tree",
                    "plot_id": "B1",
                    "x": 1.0,
                    "y": 1.0,
                    "height_m": 3.0,
                    "radius_m": 1.0,
                },
            )

        self.assertEqual(elevation.status_code, 403, elevation.text)
        self.assertEqual(state.status_code, 403, state.text)
        self.assertEqual(calibration.status_code, 403, calibration.text)
        self.assertEqual(obstacle.status_code, 403, obstacle.text)
