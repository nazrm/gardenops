"""Focused coverage for canonical container plot assignment behavior."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import gardenops.db as db
from tests.base import BaseApiTest


class TestContainerPlotMoves(BaseApiTest):
    def _insert_plant(self, plt_id: str, name: str) -> None:
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plants (plt_id, name, category) VALUES (%s, %s, 'busker')",
                (plt_id, name),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (plt_id, self._owner_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def _insert_container(
        self,
        plot_id: str,
        *,
        environment: str = "outdoor",
        archived_at_ms: int | None = None,
        owner_user_id: int | None = None,
    ) -> None:
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color,
                    plot_kind, display_name, container_type,
                    parent_map_object_id, environment, archived_at_ms
                )
                VALUES (%s, %s, 'C', 'Containers', 0, NULL, NULL, '', '', NULL,
                        'container', %s, 'pot', NULL, %s, %s)
                """,
                (plot_id, garden_id, plot_id, environment, archived_at_ms),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (plot_id, owner_user_id or self._owner_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def _insert_indoor_plot(self, plot_id: str) -> None:
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color,
                    plot_kind, display_name, container_type,
                    parent_map_object_id, environment, archived_at_ms
                )
                VALUES (%s, %s, 'I', 'Indoor', 1, NULL, NULL, '', '', NULL,
                        'indoor', NULL, NULL, NULL, 'indoor', NULL)
                """,
                (plot_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (plot_id, self._owner_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_partial_move_preserves_observations_and_normalizes_room_labels(self) -> None:
        self._insert_indoor_plot("INDOOR-1")
        self._insert_container("CONT-1")
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plot_plants (
                    plot_id, plt_id, quantity, seen_growing,
                    seen_growing_date, room_label
                )
                VALUES ('INDOOR-1', 'PLT-TEST', 5, 1, '2026-05-01', 'Window shelf')
                """,
            )
            conn.execute(
                """
                INSERT INTO plot_plants (
                    plot_id, plt_id, quantity, seen_growing,
                    seen_growing_date, room_label
                )
                VALUES ('B2', 'PLT-TEST', 2, 0, '2025', 'stale outdoor label')
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        partial = self.client.post(
            "/api/plots/INDOOR-1/plants/PLT-TEST/move/CONT-1",
            json={"quantity": 2},
        )
        self.assertEqual(partial.status_code, 200, partial.text)
        self.assertEqual(partial.json()["remaining_quantity"], 3)

        indoor = self.client.get("/api/plots/INDOOR-1/plants").json()[0]
        container = self.client.get("/api/plots/CONT-1/plants").json()[0]
        self.assertEqual(indoor["quantity"], 3)
        self.assertEqual(indoor["seen_growing"], 1)
        self.assertEqual(indoor["seen_growing_date"], "2026-05-01")
        self.assertEqual(indoor["room_label"], "Window shelf")
        self.assertEqual(container["quantity"], 2)
        self.assertEqual(container["seen_growing"], 1)
        self.assertEqual(container["seen_growing_date"], "2026-05-01")
        self.assertIsNone(container["room_label"])

        merged = self.client.post(
            "/api/plots/CONT-1/plants/PLT-TEST/move/B2",
        )
        self.assertEqual(merged.status_code, 200, merged.text)
        b2 = self.client.get("/api/plots/B2/plants").json()[0]
        self.assertEqual(b2["quantity"], 4)
        self.assertEqual(b2["seen_growing"], 0)
        self.assertEqual(b2["seen_growing_date"], "2025")
        self.assertIsNone(b2["room_label"])

    @patch.dict(
        os.environ,
        {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
        clear=False,
    )
    def test_shared_container_is_readable_but_viewer_cannot_assign(self) -> None:
        editor = self._create_test_user("container_editor", "container-editor-pass", role="editor")
        viewer = self._create_test_user("container_viewer", "container-viewer-pass", role="viewer")
        garden_id = self._get_default_garden_id()
        self._insert_container("SHARED-C", owner_user_id=self._owner_id)
        self._insert_plant("EDITOR-PLANT", "Editor Plant")
        self._insert_plant("VIEWER-PLANT", "Viewer Plant")

        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE plant_ownership SET owner_user_id = %s WHERE plt_id = 'EDITOR-PLANT'",
                (editor["id"],),
            )
            conn.execute(
                "UPDATE plant_ownership SET owner_user_id = %s WHERE plt_id = 'VIEWER-PLANT'",
                (viewer["id"],),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        editor_client, editor_headers = self._authenticated_client(
            "container_editor",
            "container-editor-pass",
            garden_id=garden_id,
        )
        editor_plots = editor_client.get("/api/plots", headers=editor_headers)
        self.assertTrue(
            next(plot for plot in editor_plots.json() if plot["plot_id"] == "SHARED-C")[
                "can_assign"
            ]
        )
        placed = editor_client.post(
            "/api/plots/SHARED-C/plants/EDITOR-PLANT",
            headers=editor_headers,
            json={"quantity": 1},
        )
        self.assertEqual(placed.status_code, 201, placed.text)

        viewer_client, viewer_headers = self._authenticated_client(
            "container_viewer",
            "container-viewer-pass",
            garden_id=garden_id,
        )
        plot_list = viewer_client.get("/api/plots", headers=viewer_headers)
        self.assertEqual(plot_list.status_code, 200, plot_list.text)
        self.assertIn("SHARED-C", {plot["plot_id"] for plot in plot_list.json()})
        self.assertFalse(
            next(plot for plot in plot_list.json() if plot["plot_id"] == "SHARED-C")[
                "can_assign"
            ]
        )
        visible = viewer_client.get("/api/plots/SHARED-C/plants", headers=viewer_headers)
        self.assertEqual(visible.status_code, 200, visible.text)
        self.assertEqual([p["plt_id"] for p in visible.json()], ["EDITOR-PLANT"])

        denied = viewer_client.post(
            "/api/plots/SHARED-C/plants/VIEWER-PLANT",
            headers=viewer_headers,
            json={"quantity": 1},
        )
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_archived_containers_are_hidden_and_cannot_receive_assignments(self) -> None:
        self._insert_container("ACTIVE-C")
        self._insert_container("ARCHIVED-C", archived_at_ms=1770000000000)

        listed = self.client.get("/api/plots")
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_ids = {plot["plot_id"] for plot in listed.json()}
        self.assertIn("ACTIVE-C", listed_ids)
        self.assertNotIn("ARCHIVED-C", listed_ids)

        add = self.client.post(
            "/api/plots/ARCHIVED-C/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.assertEqual(add.status_code, 410, add.text)

        move = self.client.post(
            "/api/plots/B1/plants/PLT-TEST/move/ARCHIVED-C",
        )
        self.assertEqual(move.status_code, 410, move.text)

    def test_ordinary_plot_routes_cannot_mutate_container_rows(self) -> None:
        self._insert_container("GUARDED-C")

        patch_response = self.client.patch(
            "/api/plots/GUARDED-C",
            json={"zone_name": "wrong path"},
        )
        batch_response = self.client.post(
            "/api/plots/batch-move",
            json={"moves": [{"plot_id": "GUARDED-C", "grid_row": 4, "grid_col": 4}]},
        )
        delete_response = self.client.delete("/api/plots/GUARDED-C")

        for response in (patch_response, batch_response, delete_response):
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 409, response.text)

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT plot_kind, display_name, archived_at_ms
                FROM plots
                WHERE plot_id = 'GUARDED-C'
                """,
            ).fetchone()
            self.assertEqual(row["plot_kind"], "container")
            self.assertEqual(row["display_name"], "GUARDED-C")
            self.assertIsNone(row["archived_at_ms"])
        finally:
            db.return_db(conn)


if __name__ == "__main__":
    unittest.main()
