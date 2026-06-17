import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import MagicMock, patch

import psycopg

import gardenops.db as db
from gardenops.main import app
from gardenops.router_helpers import generate_public_id
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password


class TestPlots(BaseApiTest):
    def _insert_plot_reference_matrix(self, plot_id: str) -> int:
        garden_id = self._get_default_garden_id()
        now_ms = db.current_timestamp_ms()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plots (plot_id, zone_code, zone_name, plot_number, grid_row, grid_col)
                VALUES (%s, 'R', 'Reference', 90, 26, 20)
                """,
                (plot_id,),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (plot_id, self._owner_id, garden_id),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, 'PLT-TEST', 1)",
                (plot_id,),
            )
            task_id = int(
                conn.execute(
                    """
                    INSERT INTO garden_tasks (
                        garden_id, task_type, title, status, severity, due_on,
                        created_at_ms, updated_at_ms
                    )
                    VALUES (%s, 'water', 'Reference task', 'pending', 'normal',
                        '2026-05-12', %s, %s)
                    RETURNING id
                    """,
                    (garden_id, now_ms, now_ms),
                ).fetchone()["id"],
            )
            conn.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
                (task_id, plot_id),
            )
            issue_id = int(
                conn.execute(
                    """
                    INSERT INTO garden_issues (
                        public_id, garden_id, issue_type, title, severity, status,
                        created_at_ms, updated_at_ms
                    )
                    VALUES (%s, %s, 'pest', 'Reference issue', 'normal', 'open', %s, %s)
                    RETURNING id
                    """,
                    (generate_public_id("iss"), garden_id, now_ms, now_ms),
                ).fetchone()["id"],
            )
            conn.execute(
                "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
                (issue_id, plot_id),
            )
            journal_id = int(
                conn.execute(
                    """
                    INSERT INTO garden_journal_entries (
                        public_id, garden_id, event_type, occurred_on, title,
                        created_at_ms, updated_at_ms
                    )
                    VALUES (%s, %s, 'note', '2026-05-12', 'Reference journal', %s, %s)
                    RETURNING id
                    """,
                    (generate_public_id("jrn"), garden_id, now_ms, now_ms),
                ).fetchone()["id"],
            )
            conn.execute(
                "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
                (journal_id, plot_id),
            )
            harvest_id = int(
                conn.execute(
                    """
                    INSERT INTO harvest_entries (
                        public_id, garden_id, occurred_on, quantity, unit, quality,
                        created_at_ms, updated_at_ms
                    )
                    VALUES (%s, %s, '2026-05-12', 1, 'kg', 'good', %s, %s)
                    RETURNING id
                    """,
                    (generate_public_id("hrv"), garden_id, now_ms, now_ms),
                ).fetchone()["id"],
            )
            conn.execute(
                "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
                (harvest_id, plot_id),
            )
            event_id = int(
                conn.execute(
                    """
                    INSERT INTO garden_calendar_events (
                        public_id, garden_id, title, event_on, created_by_user_id,
                        updated_by_user_id, created_at_ms, updated_at_ms
                    )
                    VALUES (%s, %s, 'Reference event', '2026-05-12', %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        generate_public_id("cal"),
                        garden_id,
                        self._owner_id,
                        self._owner_id,
                        now_ms,
                        now_ms,
                    ),
                ).fetchone()["id"],
            )
            conn.execute(
                "INSERT INTO garden_calendar_event_plots (event_id, plot_id) VALUES (%s, %s)",
                (event_id, plot_id),
            )
            conn.execute(
                """
                INSERT INTO media_assets (
                    asset_id, garden_id, storage_key, preview_storage_key,
                    original_filename, mime_type, bytes, width, height, created_at_ms
                )
                VALUES (%s, %s, %s, %s, 'plot.jpg', 'image/jpeg', 1, 1, 1, %s)
                """,
                (
                    f"asset-{plot_id}",
                    garden_id,
                    f"original/{plot_id}.jpg",
                    f"preview/{plot_id}.jpg",
                    now_ms,
                ),
            )
            conn.execute(
                """
                INSERT INTO media_links (asset_id, target_type, target_id)
                VALUES (%s, 'plot', %s)
                """,
                (f"asset-{plot_id}", plot_id),
            )
            conn.execute(
                """
                INSERT INTO shademap_obstacles (
                    label, kind, linked_plot_id, latitude, longitude, height_m,
                    crown_radius_m, active, garden_id
                )
                VALUES ('Reference tree', 'tree', %s, 60.0, 5.0, 3.0, 2.0, 1, %s)
                """,
                (plot_id, garden_id),
            )
            conn.execute(
                """
                UPDATE shademap_state
                SET selected_plot_id = %s
                WHERE garden_id = %s
                """,
                (plot_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_elevations (plot_id, elevation_m, cache_sig, garden_id)
                VALUES (%s, 12.5, 'sig-reference', %s)
                """,
                (plot_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_elevation_overrides (plot_id, elevation_m, garden_id)
                VALUES (%s, 13.0, %s)
                """,
                (plot_id, garden_id),
            )
            conn.commit()
            return garden_id
        finally:
            db.return_db(conn)

    def _plot_reference_count(self, table: str, column: str, plot_id: str) -> int:
        conn = db.get_db()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = %s",
                (plot_id,),
            ).fetchone()
            return int(row["c"])
        finally:
            db.return_db(conn)

    def test_list_plots(self) -> None:
        response = self.client.get("/api/plots")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(any(p["plot_id"] == "B1" for p in data))

    def test_concurrent_read_requests_do_not_error(self) -> None:
        paths = [
            "/api/plots",
            "/api/plants",
            "/api/layout-state",
            "/api/snapshots",
        ]

        def fetch(path: str) -> int:
            return self.client.get(path).status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(fetch, paths * 5))

        self.assertTrue(all(status == 200 for status in statuses), statuses)

    def test_layout_state_round_trip(self) -> None:
        initial = self.client.get("/api/layout-state")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(
            initial.json(),
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

        updated = self.client.patch(
            "/api/layout-state",
            json={"row": 4, "col": 5, "width": 10, "height": 7, "north_degrees": 93},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.json(),
            {
                "row": 4,
                "col": 5,
                "width": 10,
                "height": 7,
                "north_degrees": 93,
                "grid_rows": 30,
                "grid_cols": 22,
            },
        )

        fetched = self.client.get("/api/layout-state")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(
            fetched.json(),
            {
                "row": 4,
                "col": 5,
                "width": 10,
                "height": 7,
                "north_degrees": 93,
                "grid_rows": 30,
                "grid_cols": 22,
            },
        )

    def test_create_plot_rejects_out_of_bounds_column(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "X1",
                "zone_code": "X",
                "zone_name": "Test Zone",
                "plot_number": 1,
                "grid_row": 1,
                "grid_col": 101,
                "sub_zone": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_create_plot_rejects_duplicate_id(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "B1",
                "zone_code": "B",
                "zone_name": "Bed",
                "plot_number": 1,
                "grid_row": 1,
                "grid_col": 1,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_plot_not_found(self) -> None:
        response = self.client.delete("/api/plots/NONEXISTENT")
        self.assertEqual(response.status_code, 404)

    def test_update_plot_color(self) -> None:
        response = self.client.patch(
            "/api/plots/B1",
            json={"color": "#6dbb6d"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["color"], "#6dbb6d")

    def test_update_plot_clear_color(self) -> None:
        self.client.patch(
            "/api/plots/B1",
            json={"color": "#6dbb6d"},
        )
        response = self.client.patch(
            "/api/plots/B1",
            json={"color": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["color"])

    def test_rename_plot(self) -> None:
        self.client.post(
            "/api/plots",
            json={
                "plot_id": "RENAME1",
                "zone_code": "R",
                "zone_name": "Rename",
                "plot_number": 1,
                "grid_row": 5,
                "grid_col": 5,
            },
        )
        response = self.client.patch(
            "/api/plots/RENAME1",
            json={"new_plot_id": "RENAMED1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plot_id"], "RENAMED1")

        get = self.client.get("/api/plots")
        ids = [p["plot_id"] for p in get.json()]
        self.assertIn("RENAMED1", ids)
        self.assertNotIn("RENAME1", ids)

    def test_rename_plot_updates_plot_plants(self) -> None:
        self.client.post(
            "/api/plots",
            json={
                "plot_id": "RREF1",
                "zone_code": "R",
                "zone_name": "Rename",
                "plot_number": 2,
                "grid_row": 6,
                "grid_col": 6,
            },
        )
        self.client.post(
            "/api/plots/RREF1/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.client.patch(
            "/api/plots/RREF1",
            json={"new_plot_id": "RREF2"},
        )
        plants = self.client.get("/api/plots/RREF2/plants").json()
        self.assertTrue(
            any(p["plt_id"] == "PLT-TEST" for p in plants),
        )

    def test_rename_plot_updates_all_reference_tables(self) -> None:
        old_id = "RALL1"
        new_id = "RALL2"
        garden_id = self._insert_plot_reference_matrix(old_id)

        response = self.client.patch(f"/api/plots/{old_id}", json={"new_plot_id": new_id})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["plot_id"], new_id)
        for table, column in (
            ("plots", "plot_id"),
            ("plot_ownership", "plot_id"),
            ("plot_plants", "plot_id"),
            ("garden_task_plots", "plot_id"),
            ("garden_issue_plots", "plot_id"),
            ("garden_journal_entry_plots", "plot_id"),
            ("harvest_entry_plots", "plot_id"),
            ("garden_calendar_event_plots", "plot_id"),
            ("plot_elevations", "plot_id"),
            ("plot_elevation_overrides", "plot_id"),
        ):
            with self.subTest(table=table):
                self.assertEqual(self._plot_reference_count(table, column, old_id), 0)
                self.assertEqual(self._plot_reference_count(table, column, new_id), 1)
        self.assertEqual(self._plot_reference_count("media_links", "target_id", old_id), 0)
        self.assertEqual(self._plot_reference_count("media_links", "target_id", new_id), 1)
        self.assertEqual(
            self._plot_reference_count("shademap_obstacles", "linked_plot_id", old_id),
            0,
        )
        self.assertEqual(
            self._plot_reference_count("shademap_obstacles", "linked_plot_id", new_id),
            1,
        )
        conn = db.get_db()
        try:
            state = conn.execute(
                "SELECT selected_plot_id FROM shademap_state WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            self.assertEqual(state["selected_plot_id"], new_id)
        finally:
            db.return_db(conn)

    def test_rename_plot_duplicate_rejected(self) -> None:
        response = self.client.patch(
            "/api/plots/B2",
            json={"new_plot_id": "B1"},
        )
        self.assertEqual(response.status_code, 400)

    def test_update_quantity_rejects_non_positive_quantity(self) -> None:
        self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 1},
        )
        response = self.client.patch(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 0},
        )
        self.assertEqual(response.status_code, 422)

    def test_add_plant_to_nonexistent_plot_is_stored_as_custom_assignment(self) -> None:
        response = self.client.post(
            "/api/plots/KASSE/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.assertEqual(response.status_code, 201)
        listed = self.client.get("/api/plants?q=Test Plant")
        self.assertEqual(listed.status_code, 200)
        plant = next(p for p in listed.json() if p["plt_id"] == "PLT-TEST")
        self.assertIn("KASSE", plant["plot_ids"])
        self.assertIn("KASSE", plant["missing_plot_ids"])

    def test_add_plant_returns_companion_warnings_field(self) -> None:
        response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("companion_warnings", data)
        self.assertIsInstance(data["companion_warnings"], list)

    def test_add_nonexistent_plant_to_plot(self) -> None:
        response = self.client.post(
            "/api/plots/B1/plants/NOPE",
            json={"quantity": 1},
        )
        self.assertEqual(response.status_code, 404)

    def test_move_plant_between_plots(self) -> None:
        self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 2},
        )
        response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST/move/B2",
        )
        self.assertEqual(response.status_code, 200)
        moved = response.json()
        self.assertEqual(moved["quantity"], 2)

        b1 = self.client.get("/api/plots/B1/plants").json()
        self.assertFalse(any(p["plt_id"] == "PLT-TEST" for p in b1))
        b2 = self.client.get("/api/plots/B2/plants").json()
        self.assertTrue(any(p["plt_id"] == "PLT-TEST" for p in b2))

    def test_plants_include_plot_ids(self) -> None:
        """The list_plants endpoint includes plot_ids array."""
        self.client.post(
            "/api/plots/B2/plants/PLT-002",
            json={"quantity": 1},
        )
        response = self.client.get("/api/plants?q=Rose")
        data = response.json()
        rose = next(
            (p for p in data if p["plt_id"] == "PLT-002"),
            None,
        )
        self.assertIsNotNone(rose)
        assert rose is not None
        self.assertIn("B2", rose["plot_ids"])

    def test_plants_flag_missing_plot_ids_and_resolve_after_plot_creation(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
                ("KASSE", "PLT-TEST", 2),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        before = self.client.get("/api/plants?q=Test Plant")
        self.assertEqual(before.status_code, 200)
        plant_before = next(p for p in before.json() if p["plt_id"] == "PLT-TEST")
        self.assertIn("KASSE", plant_before["plot_ids"])
        self.assertIn("KASSE", plant_before["missing_plot_ids"])

        created = self.client.post(
            "/api/plots",
            json={
                "plot_id": "KASSE",
                "zone_code": "K",
                "zone_name": "Kasse",
                "plot_number": 1,
                "grid_row": 5,
                "grid_col": 5,
                "sub_zone": "",
                "notes": "",
                "color": None,
            },
        )
        self.assertEqual(created.status_code, 201)

        after = self.client.get("/api/plants?q=Test Plant")
        self.assertEqual(after.status_code, 200)
        plant_after = next(p for p in after.json() if p["plt_id"] == "PLT-TEST")
        self.assertIn("KASSE", plant_after["plot_ids"])
        self.assertEqual(plant_after["missing_plot_ids"], [])

    def test_add_and_remove_orphan_plot_assignment_via_runtime_routes(self) -> None:
        added = self.client.post(
            "/api/plots/K1/plants/PLT-TEST",
            json={"quantity": 2},
        )
        self.assertEqual(added.status_code, 201)

        after_add = self.client.get("/api/plants?q=Test Plant")
        self.assertEqual(after_add.status_code, 200)
        plant = next(p for p in after_add.json() if p["plt_id"] == "PLT-TEST")
        self.assertIn("K1", plant["plot_ids"])
        self.assertIn("K1", plant["missing_plot_ids"])

        removed = self.client.delete("/api/plots/K1/plants/PLT-TEST")
        self.assertEqual(removed.status_code, 204)

        after_remove = self.client.get("/api/plants?q=Test Plant")
        self.assertEqual(after_remove.status_code, 200)
        plant_after_remove = next(p for p in after_remove.json() if p["plt_id"] == "PLT-TEST")
        self.assertNotIn("K1", plant_after_remove["plot_ids"])

    def test_batch_move_plots(self) -> None:
        response = self.client.post(
            "/api/plots/batch-move",
            json={
                "moves": [
                    {
                        "plot_id": "B1",
                        "grid_row": 2,
                        "grid_col": 2,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["moved"], 1)

        self.client.post(
            "/api/plots/batch-move",
            json={
                "moves": [
                    {
                        "plot_id": "B1",
                        "grid_row": 1,
                        "grid_col": 1,
                    },
                ],
            },
        )

    def test_batch_move_nonexistent_plot(self) -> None:
        response = self.client.post(
            "/api/plots/batch-move",
            json={
                "moves": [
                    {
                        "plot_id": "NOPE",
                        "grid_row": 1,
                        "grid_col": 1,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_batch_move_supports_swap(self) -> None:
        response = self.client.post(
            "/api/plots/batch-move",
            json={
                "moves": [
                    {
                        "plot_id": "B1",
                        "grid_row": 1,
                        "grid_col": 2,
                    },
                    {
                        "plot_id": "B2",
                        "grid_row": 1,
                        "grid_col": 1,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        plots = {
            item["plot_id"]: (item["grid_row"], item["grid_col"])
            for item in self.client.get("/api/plots").json()
            if item["plot_id"] in {"B1", "B2"}
        }
        self.assertEqual(plots["B1"], (1, 2))
        self.assertEqual(plots["B2"], (1, 1))

    def test_batch_move_rejects_duplicate_plot_ids(self) -> None:
        response = self.client.post(
            "/api/plots/batch-move",
            json={
                "moves": [
                    {
                        "plot_id": "B1",
                        "grid_row": 2,
                        "grid_col": 1,
                    },
                    {
                        "plot_id": "B1",
                        "grid_row": 3,
                        "grid_col": 1,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_batch_move_rejects_same_garden_peer_editor_plot(self) -> None:
        os.environ.update(
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
        )
        try:
            owner = self._create_test_user("plot_batch_owner", "plotownerpass", role="editor")
            self._create_test_user("plot_batch_peer", "plotpeerpass", role="editor")
            garden_id = self._get_default_garden_id()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    UPDATE plot_ownership
                    SET owner_user_id = %s
                    WHERE plot_id = %s AND garden_id = %s
                    """,
                    (int(owner["id"]), "B1", garden_id),
                )
                conn.execute(
                    "UPDATE plots SET grid_row = 1, grid_col = 1 WHERE plot_id = %s",
                    ("B1",),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            peer_client, peer_headers = self._authenticated_client(
                "plot_batch_peer",
                "plotpeerpass",
            )
            response = peer_client.post(
                "/api/plots/batch-move",
                headers=peer_headers,
                json={"moves": [{"plot_id": "B1", "grid_row": 2, "grid_col": 2}]},
            )
            self.assertEqual(response.status_code, 404, response.text)

            conn = db.get_db()
            try:
                row = conn.execute(
                    "SELECT grid_row, grid_col FROM plots WHERE plot_id = %s",
                    ("B1",),
                ).fetchone()
            finally:
                db.return_db(conn)
            self.assertEqual((int(row["grid_row"]), int(row["grid_col"])), (1, 1))
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_plot_assignment_meanings_are_saved_per_user(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="plot_meaning_alice",
                password=strong_password("alicepass123"),
                role="editor",
            )
            create_user(
                conn,
                username="plot_meaning_bob",
                password=strong_password("bobpass123"),
                role="editor",
            )
            conn.commit()
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            alice_client = self._new_client()
            bob_client = self._new_client()
            _, alice_csrf = self._login_session(
                "plot_meaning_alice", "alicepass123", client=alice_client
            )
            _, bob_csrf = self._login_session("plot_meaning_bob", "bobpass123", client=bob_client)

            language_saved = alice_client.put(
                "/api/auth/me/settings",
                headers=self._session_headers(alice_csrf, garden_id=default_garden_id),
                json={
                    "language": "no",
                },
            )
            self.assertEqual(language_saved.status_code, 200)

            saved = alice_client.put(
                "/api/auth/me/settings",
                headers=self._session_headers(alice_csrf, garden_id=default_garden_id),
                json={
                    "plot_assignment_meanings": [
                        {
                            "pattern": "K*",
                            "label": "Planters",
                            "description": "Kitchen and container range",
                        },
                        {
                            "pattern": "P*",
                            "label": "Plen strips",
                            "description": "Shared lawn planting band",
                        },
                    ],
                },
            )
            self.assertEqual(saved.status_code, 200)

            alice_settings = alice_client.get(
                "/api/auth/me/settings",
                headers=self._session_headers(alice_csrf, garden_id=default_garden_id),
            )
            self.assertEqual(alice_settings.status_code, 200)
            self.assertEqual(
                alice_settings.json()["plot_assignment_meanings"],
                [
                    {
                        "pattern": "K*",
                        "label": "Planters",
                        "description": "Kitchen and container range",
                    },
                    {
                        "pattern": "P*",
                        "label": "Plen strips",
                        "description": "Shared lawn planting band",
                    },
                ],
            )
            self.assertEqual(alice_settings.json()["language"], "no")

            alice_me = alice_client.get(
                "/api/auth/me",
                headers=self._session_headers(alice_csrf, garden_id=default_garden_id),
            )
            self.assertEqual(alice_me.status_code, 200)
            self.assertEqual(alice_me.json()["language"], "no")
            self.assertEqual(
                alice_me.json()["plot_assignment_meanings"][0]["pattern"],
                "K*",
            )

            bob_settings = bob_client.get(
                "/api/auth/me/settings",
                headers=self._session_headers(bob_csrf, garden_id=default_garden_id),
            )
            self.assertEqual(bob_settings.status_code, 200)
            self.assertEqual(bob_settings.json()["language"], "en")
            self.assertEqual(bob_settings.json()["plot_assignment_meanings"], [])

    def test_version_endpoint_is_public_and_returns_dynamic_verbose_version(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["base_version"], "0.1.1")
        self.assertTrue(str(payload["version"]).startswith("0.1.1."))
        version_parts = str(payload["version"]).split(".")
        self.assertGreaterEqual(len(version_parts), 5)
        self.assertRegex(version_parts[-2], r"^\d{12}$")
        self.assertRegex(version_parts[-1], r"^[A-Za-z]+$")
        self.assertIsInstance(payload["dirty"], bool)
        self.assertTrue(isinstance(payload["git_commit"], str) or payload["git_commit"] is None)
        self.assertTrue(
            isinstance(payload["last_updated_at_ms"], int) or payload["last_updated_at_ms"] is None,
        )

    def test_default_garden_exists_and_singleton_state_is_scoped(self) -> None:
        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id, slug FROM gardens WHERE slug = 'default'",
            ).fetchone()
            self.assertIsNotNone(garden)
            assert garden is not None
            default_garden_id = int(garden["id"])

            for table_name in ("layout_state", "shademap_state", "shademap_calibration"):
                row = conn.execute(
                    f"SELECT garden_id FROM {table_name} WHERE garden_id = %s LIMIT 1",  # noqa: S608
                    (default_garden_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(int(row["garden_id"]), default_garden_id)
        finally:
            db.return_db(conn)

    def test_create_user_assigns_default_garden_membership(self) -> None:
        conn = db.get_db()
        try:
            created = create_user(
                conn,
                username="gardenmember1",
                password=strong_password("gardenmemberpass123"),
                role="editor",
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT g.slug, gm.role
                FROM garden_memberships gm
                JOIN gardens g ON g.id = gm.garden_id
                WHERE gm.user_id = %s
                """,
                (int(created["id"]),),
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(str(row["slug"]), "default")
            self.assertEqual(str(row["role"]), "editor")
        finally:
            db.return_db(conn)

    def test_platform_admin_can_restart_user_onboarding_for_managed_garden(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="restart_onboard_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="restart_onboard_editor",
                password=strong_password("editorpass123"),
                role="editor",
            )
            cursor = conn.execute(
                """
                INSERT INTO gardens (slug, name, onboarding_complete)
                VALUES (%s, %s, 1)
            RETURNING id
            """,
                (f"restart-onboarding-{os.urandom(3).hex()}", "Restart Target Garden"),
            )
            target_garden_id = cursor.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (target_garden_id, int(target["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session(
                "restart_onboard_admin",
                "adminpass123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)

            admin_headers = self._reauth_and_refresh_headers(
                admin_client,
                admin_headers,
                password=strong_password("adminpass123"),
            )

            reset = admin_client.post(
                f"/api/auth/users/{int(target['id'])}/restart-onboarding",
                headers=admin_headers,
                json={"action_reason": "test-restart-onboarding"},
            )
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(reset.json()["garden_id"], target_garden_id)
            self.assertEqual(reset.json()["onboarding_complete"], False)

            listed = admin_client.get("/api/auth/users", headers=admin_headers)
            self.assertEqual(listed.status_code, 200)
            target_row = next(
                user for user in listed.json()["users"] if int(user["id"]) == int(target["id"])
            )
            self.assertEqual(target_row["managed_garden_id"], target_garden_id)
            self.assertEqual(target_row["managed_garden_name"], "Restart Target Garden")
            self.assertEqual(target_row["managed_garden_onboarding_complete"], False)

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT onboarding_complete FROM gardens WHERE id = %s LIMIT 1",
                (target_garden_id,),
            ).fetchone()
            assert garden is not None
            self.assertEqual(int(garden["onboarding_complete"]), 0)
        finally:
            db.return_db(conn)

    def test_garden_invitation_create_list_revoke_flow(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="invite_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("invite_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            garden_slug = f"invite-garden-{os.urandom(4).hex()}"
            created_garden = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Invite Garden", "slug": garden_slug},
            )
            self.assertEqual(created_garden.status_code, 201)
            garden_id = int(created_garden.json()["id"])

            headers = self._reauth_and_refresh_headers(
                self.client,
                headers,
                password=strong_password("admin-password-123"),
            )
            invitation = self.client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "invited_pending",
                    "role": "viewer",
                    "expires_in_minutes": 30,
                    "action_reason": "create-pending-garden-invite",
                },
            )
            self.assertEqual(invitation.status_code, 201)
            invitation_body = invitation.json()
            invite_token = invitation_body["invite_token"]
            invitation_id = int(invitation_body["invitation"]["id"])
            self.assertTrue(invite_token)
            self.assertEqual(invitation_body["invitation"]["status"], "pending")

            listed = self.client.get(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
            )
            self.assertEqual(listed.status_code, 200)
            listed_ids = {int(row["id"]) for row in listed.json()["invitations"]}
            self.assertIn(invitation_id, listed_ids)

            revoked = self.client.delete(
                f"/api/gardens/{garden_id}/invitations/{invitation_id}",
                headers={
                    **headers,
                    "x-action-reason": "revoke-pending-garden-invite",
                },
            )
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(int(revoked.json()["invitation_id"]), invitation_id)

            listed_after = self.client.get(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
            )
            self.assertEqual(listed_after.status_code, 200)
            row = next(
                row for row in listed_after.json()["invitations"] if int(row["id"]) == invitation_id
            )
            self.assertEqual(row["status"], "revoked")
            self.assertIsNotNone(row["revoked_at_ms"])

            revoked_accept = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("invited-password-123")},
            )
            self.assertEqual(revoked_accept.status_code, 400)
            self.assertEqual(
                revoked_accept.json()["detail"],
                "Invalid or expired invitation token",
            )

    def test_invalid_invitation_attempts_raise_alert_and_target_limit(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="invite_alert_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            create_user(
                conn,
                username="invite_existing_user",
                password=strong_password("invitepass123"),
                role="viewer",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_INVITE_ACCEPT_RATE_LIMIT": "50",
                "AUTH_INVITE_ACCEPT_TOKEN_RATE_LIMIT": "1",
                "ALERT_INVALID_INVITATION_ATTEMPTS_PER_5M": "1",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session(
                "invite_alert_admin",
                "adminpass123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)

            created_garden = admin_client.post(
                "/api/gardens",
                headers=admin_headers,
                json={"name": "Invite Alert Garden", "slug": f"invite-alert-{os.urandom(4).hex()}"},
            )
            self.assertEqual(created_garden.status_code, 201)
            garden_id = int(created_garden.json()["id"])
            admin_headers = self._reauth_and_refresh_headers(
                admin_client,
                admin_headers,
                password=strong_password("adminpass123"),
            )

            invitation = admin_client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=admin_headers,
                json={
                    "invitee_username": "invite_existing_user",
                    "role": "viewer",
                    "expires_in_minutes": 30,
                    "action_reason": "create-invalid-attempt-test-invite",
                },
            )
            self.assertEqual(invitation.status_code, 201)
            invite_token = invitation.json()["invite_token"]

            first = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("wrong-pass")},
            )
            second = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("wrong-pass")},
            )
            alerts = admin_client.get("/api/auth/security-alerts", headers=admin_headers)

        self.assertEqual(first.status_code, 401)
        self.assertEqual(first.json()["detail"], "Invalid invitation credentials")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(alerts.status_code, 200)
        alert_names = {alert["name"] for alert in alerts.json().get("alerts", [])}
        self.assertIn("invalid_invitation_attempts_per_5m", alert_names)

    def test_lifecycle_audit_detail_includes_structured_reason_metadata(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="audit_reason_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="audit_reason_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("audit_reason_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            issued = self.client.post(
                f"/api/auth/users/{target_id}/issue-reset",
                headers=headers,
                json={
                    "expires_in_minutes": 30,
                    "must_change_password": True,
                    "action_reason": "security-rotation",
                },
            )
            self.assertEqual(issued.status_code, 200)

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT detail
                FROM audit_events
                WHERE detail LIKE 'auth.user.issue-reset %'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            detail = str(row["detail"])
            self.assertTrue(detail.startswith("auth.user.issue-reset "))
            payload = json.loads(detail.split(" ", 1)[1])
            self.assertEqual(payload["user_id"], target_id)
            self.assertEqual(payload["action_reason"], "security-rotation")
            self.assertTrue(payload["must_change_password"])
        finally:
            db.return_db(conn)

    def test_mutation_requests_are_written_to_audit_log(self) -> None:
        response = self.client.post("/api/snapshots", json={"name": "audit-check"})
        self.assertEqual(response.status_code, 201)
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            self.assertIsNotNone(default_garden)
            assert default_garden is not None
            row = conn.execute(
                """
                SELECT method, path, status_code, actor_username, garden_id
                FROM audit_events
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["method"], "POST")
            self.assertEqual(row["path"], "/api/snapshots")
            self.assertEqual(row["status_code"], 201)
            self.assertEqual(row["actor_username"], "local")
            self.assertEqual(int(row["garden_id"]), int(default_garden["id"]))
        finally:
            db.return_db(conn)

    def test_get_requests_are_not_written_to_audit_log(self) -> None:
        response = self.client.get("/api/plots")
        self.assertEqual(response.status_code, 200)
        conn = db.get_db()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()
            self.assertEqual(int(row["c"]), 0)
        finally:
            db.return_db(conn)

    def test_destructive_admin_alerts_include_recent_garden_scope(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="admin3b",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            self.assertIsNotNone(default_garden)
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "ALERT_DESTRUCTIVE_ADMIN_ACTIONS_PER_5M": "1",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session("admin3b", "adminpass123", client=admin_client)
            admin_headers = self._session_headers(admin_csrf, garden_id=default_garden_id)

            enabled = admin_client.patch(
                "/api/auth/emergency-read-only",
                headers=admin_headers,
                json={
                    "enabled": True,
                    "action_reason": "garden-alert-scope-test",
                    "expires_in_minutes": 5,
                },
            )
            self.assertEqual(enabled.status_code, 200)

            metrics = admin_client.get("/api/auth/security-metrics", headers=admin_headers)
            self.assertEqual(metrics.status_code, 200)
            self.assertEqual(
                metrics.json()["garden_scope"]["recent_destructive_admin_garden_ids"],
                [default_garden_id],
            )

            alerts = admin_client.get("/api/auth/security-alerts", headers=admin_headers)
            self.assertEqual(alerts.status_code, 200)
            destructive_alert = next(
                alert
                for alert in alerts.json().get("alerts", [])
                if alert["name"] == "destructive_admin_actions_per_5m"
            )
            self.assertEqual(destructive_alert["garden_ids"], [default_garden_id])

    def test_foreign_garden_selection_returns_404(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="alicegarden",
                password=strong_password("alicepass123"),
                role="editor",
            )
            bob = create_user(
                conn, username="bobgarden", password=strong_password("bobpass123"), role="editor"
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                ("bob-private", "Bob Private Garden"),
            )
            bob_garden_id = cursor.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'editor')
                """,
                (bob_garden_id, int(bob["id"])),
            )
            conn.execute(
                "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("BG1", "B", "Bob", 11, 3, 3, "", "", None),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("BG1", int(bob["id"]), bob_garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            alice_client = self._new_client()
            bob_client = self._new_client()
            self._login_session("alicegarden", "alicepass123", client=alice_client)
            alice_headers = {"x-garden-id": str(bob_garden_id)}
            denied = alice_client.get("/api/plots", headers=alice_headers)
            self.assertEqual(denied.status_code, 404)

            self._login_session("bobgarden", "bobpass123", client=bob_client)
            bob_headers = {"x-garden-id": str(bob_garden_id)}
            allowed = bob_client.get("/api/plots", headers=bob_headers)
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("BG1", {p["plot_id"] for p in allowed.json()})

    def test_garden_management_create_list_and_membership_flow(self) -> None:
        conn = db.get_db()
        try:
            admin = create_user(
                conn,
                username="gardenadmin",
                password=strong_password("gardenpass123"),
                role="admin",
            )
            editor = create_user(
                conn,
                username="gardeneditor",
                password=strong_password("editorpass123"),
                role="editor",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("gardenadmin", "gardenpass123")
            headers = self._session_headers(csrf)

            listed_before = self.client.get("/api/gardens")
            self.assertEqual(listed_before.status_code, 200)
            self.assertFalse(any(g["slug"] == "default" for g in listed_before.json()))

            slug = f"orchard-{os.urandom(4).hex()}"
            created = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Orchard Garden", "slug": slug},
            )
            self.assertEqual(created.status_code, 201)
            created_body = created.json()
            garden_id = int(created_body["id"])
            self.assertEqual(created_body["slug"], slug)

            listed_after = self.client.get("/api/gardens")
            self.assertEqual(listed_after.status_code, 200)
            self.assertIn(garden_id, {int(g["id"]) for g in listed_after.json()})

            invited = self.client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={"username": "gardeneditor", "role": "viewer"},
            )
            self.assertEqual(invited.status_code, 200)
            self.assertEqual(invited.json()["role"], "viewer")

            memberships = self.client.get(f"/api/gardens/{garden_id}/memberships")
            self.assertEqual(memberships.status_code, 200)
            rows = memberships.json()["memberships"]
            editor_row = next(row for row in rows if row["username"] == "gardeneditor")
            self.assertEqual(editor_row["role"], "viewer")

            promoted = self.client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={"username": "gardeneditor", "role": "editor"},
            )
            self.assertEqual(promoted.status_code, 200)
            self.assertEqual(promoted.json()["role"], "editor")

            removed = self.client.delete(
                f"/api/gardens/{garden_id}/memberships/{int(editor['id'])}",
                headers=headers,
            )
            self.assertEqual(removed.status_code, 200)

            memberships_after = self.client.get(f"/api/gardens/{garden_id}/memberships")
            self.assertEqual(memberships_after.status_code, 200)
            usernames = {row["username"] for row in memberships_after.json()["memberships"]}
            self.assertNotIn("gardeneditor", usernames)

            delete_last_admin = self.client.delete(
                f"/api/gardens/{garden_id}/memberships/{int(admin['id'])}",
                headers=headers,
            )
            self.assertEqual(delete_last_admin.status_code, 409)

    def test_garden_creation_is_limited_to_global_editor_or_admin_and_one_editor_garden(
        self,
    ) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="garden_limit_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            create_user(
                conn,
                username="garden_limit_editor",
                password=strong_password("editorpass123"),
                role="editor",
            )
            viewer = create_user(
                conn,
                username="garden_limit_viewer",
                password=strong_password("viewerpass123"),
                role="viewer",
            )
            cursor = conn.execute(
                """
                INSERT INTO gardens (slug, name, onboarding_complete)
                VALUES (%s, %s, 1)
                RETURNING id
                """,
                (f"viewer-managed-{os.urandom(3).hex()}", "Viewer Managed Garden"),
            )
            viewer_managed_garden_id = cursor.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (viewer_managed_garden_id, int(viewer["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session(
                "garden_limit_admin",
                "adminpass123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)

            blank_name = admin_client.post(
                "/api/gardens",
                headers=admin_headers,
                json={"name": "   "},
            )
            self.assertEqual(blank_name.status_code, 400)
            self.assertEqual(blank_name.json()["detail"], "Garden name cannot be empty")

            editor_client = self._new_client()
            _, editor_csrf = self._login_session(
                "garden_limit_editor",
                "editorpass123",
                client=editor_client,
            )
            editor_headers = self._session_headers(editor_csrf)

            first = editor_client.post(
                "/api/gardens",
                headers=editor_headers,
                json={"name": "  Editor Garden  "},
            )
            self.assertEqual(first.status_code, 201)
            self.assertEqual(first.json()["name"], "Editor Garden")

            second = editor_client.post(
                "/api/gardens",
                headers=editor_headers,
                json={"name": "Second Editor Garden"},
            )
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.json()["detail"], "Editors can only create one own garden")

            viewer_client = self._new_client()
            _, viewer_csrf = self._login_session(
                "garden_limit_viewer",
                "viewerpass123",
                client=viewer_client,
            )
            viewer_headers = self._session_headers(
                viewer_csrf,
                garden_id=viewer_managed_garden_id,
            )
            denied = viewer_client.post(
                "/api/gardens",
                headers=viewer_headers,
                json={"name": "Should Not Work"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["detail"], "Editor or admin role required")

    def test_platform_admin_can_delete_nondefault_garden_and_cleanup_state(self) -> None:
        conn = db.get_db()
        try:
            admin = create_user(
                conn,
                username="garden_delete_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
            cursor = conn.execute(
                """
                INSERT INTO gardens (slug, name, onboarding_complete)
                VALUES (%s, %s, 1)
                RETURNING id
                """,
                (f"delete-me-{os.urandom(3).hex()}", "Delete Me"),
            )
            garden_id = cursor.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, int(admin["id"])),
            )
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, zone_code, zone_name, plot_number, grid_row, grid_col,
                    sub_zone, notes, color
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                ("DEL1", "D", "Delete Zone", 1, 2, 2, "", "", "#4a7c59"),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("DEL1", int(admin["id"]), garden_id),
            )
            conn.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color, hardiness,
                    height_cm, light, link
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                ("DEL-ONLY", "Delete Only Plant", "", "frø", "", "", "", None, "", ""),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("DEL-ONLY", int(admin["id"]), garden_id),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 1)",
                ("DEL1", "DEL-ONLY"),
            )
            conn.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color, hardiness,
                    height_cm, light, link
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                ("SHARED-PLANT", "Shared Plant", "", "frø", "", "", "", None, "", ""),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("SHARED-PLANT", int(admin["id"]), garden_id),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("SHARED-PLANT", int(admin["id"]), default_garden_id),
            )
            conn.execute(
                """
                INSERT INTO layout_snapshots (public_id, name, data, garden_id)
                VALUES (%s, %s, %s, %s)
                """,
                (generate_public_id("snap"), "delete-snapshot", '{"plots":[]}', garden_id),
            )
            conn.execute(
                """
                INSERT INTO shademap_obstacles (
                    label, kind, linked_plot_id, latitude, longitude, height_m,
                    crown_radius_m, active, garden_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                ("Delete Tree", "tree", "DEL1", 60.0, 5.0, 3.0, 2.0, 1, garden_id),
            )
            conn.execute(
                """
                INSERT INTO shademap_cache (
                    cache_kind, cache_key, fetched_at_ms, content_type, payload_text,
                    payload_blob, garden_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "features",
                    f"delete-{garden_id}",
                    db.current_timestamp_ms(),
                    "application/json",
                    "{}",
                    None,
                    garden_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO plot_elevations (plot_id, elevation_m, cache_sig, garden_id)
                VALUES (%s, %s, %s, %s)
                """,
                ("DEL1", 12.5, "sig-delete", garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_elevation_overrides (plot_id, elevation_m, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("DEL1", 13.0, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, csrf = self._login_session(
                "garden_delete_admin",
                "adminpass123",
                client=admin_client,
            )
            headers = self._session_headers(csrf, garden_id=garden_id)
            headers = self._reauth_and_refresh_headers(
                admin_client,
                headers,
                password=strong_password("adminpass123"),
            )

            deleted = admin_client.delete(
                f"/api/gardens/{garden_id}",
                headers={**headers, "x-action-reason": "delete-test-garden"},
            )
            self.assertEqual(deleted.status_code, 200)
            payload = deleted.json()
            self.assertEqual(payload["garden_id"], garden_id)
            self.assertEqual(payload["plots_deleted"], 1)
            self.assertEqual(payload["snapshots_deleted"], 1)
            self.assertEqual(payload["plants_deleted"], 1)

        conn = db.get_db()
        try:
            self.assertIsNone(
                conn.execute("SELECT 1 FROM gardens WHERE id = %s", (garden_id,)).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM plot_ownership WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM plant_ownership WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM plots WHERE plot_id = 'DEL1'").fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM plot_plants WHERE plot_id = 'DEL1' OR plt_id = 'DEL-ONLY'"
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM plants WHERE plt_id = 'DEL-ONLY'").fetchone(),
            )
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM plants WHERE plt_id = 'SHARED-PLANT'").fetchone(),
            )
            self.assertIsNotNone(
                conn.execute(
                    """
                    SELECT 1
                    FROM plant_ownership
                    WHERE plt_id = 'SHARED-PLANT' AND garden_id = %s
                    """,
                    (default_garden_id,),
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM layout_snapshots WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM shademap_obstacles WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM shademap_cache WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM plot_elevations WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM plot_elevation_overrides WHERE garden_id = %s", (garden_id,)
                ).fetchone(),
            )
        finally:
            db.return_db(conn)

    def test_default_garden_cannot_be_deleted(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="garden_default_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, csrf = self._login_session(
                "garden_default_admin",
                "adminpass123",
                client=admin_client,
            )
            headers = self._session_headers(csrf, garden_id=default_garden_id)
            headers = self._reauth_and_refresh_headers(
                admin_client,
                headers,
                password=strong_password("adminpass123"),
            )

            blocked = admin_client.delete(
                f"/api/gardens/{default_garden_id}",
                headers={**headers, "x-action-reason": "delete-default-garden"},
            )
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["detail"], "Default garden cannot be deleted")

    def test_complete_onboarding_manual_rolls_back_when_house_is_invalid(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="onboardadmin",
                password=strong_password("onboardpass123"),
                role="admin",
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"onboard-manual-{os.urandom(3).hex()}", "Needs Onboarding"),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("onboardadmin",),
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
            _, csrf = self._login_session("onboardadmin", "onboardpass123")
            headers = self._session_headers(csrf, garden_id=garden_id)
            response = self.client.post(
                f"/api/gardens/{garden_id}/complete-onboarding",
                headers=headers,
                json={
                    "name": "Should Roll Back",
                    "grid_rows": 5,
                    "grid_cols": 5,
                    "latitude": None,
                    "longitude": None,
                    "address": "",
                    "mode": "manual",
                    "house": {
                        "row": 1,
                        "col": 1,
                        "width": 6,
                        "height": 2,
                        "north_degrees": 0,
                        "grid_rows": 5,
                        "grid_cols": 5,
                    },
                    "zones": [
                        {
                            "zone_code": "R",
                            "zone_name": "Rollback Zone",
                            "start_row": 4,
                            "start_col": 4,
                            "end_row": 5,
                            "end_col": 5,
                            "color": "#4a7c59",
                        },
                    ],
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("House does not fit", response.json()["detail"])

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT name, grid_rows, grid_cols, onboarding_complete FROM gardens WHERE id = %s",
                (garden_id,),
            ).fetchone()
            assert garden is not None
            self.assertEqual(garden["name"], "Needs Onboarding")
            self.assertEqual(int(garden["grid_rows"]), 30)
            self.assertEqual(int(garden["grid_cols"]), 22)
            self.assertEqual(int(garden["onboarding_complete"]), 0)
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM layout_state WHERE garden_id = %s LIMIT 1",
                    (garden_id,),
                ).fetchone(),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM plot_ownership WHERE garden_id = %s",
                    (garden_id,),
                ).fetchone()["c"],
                0,
            )
        finally:
            db.return_db(conn)

    def test_complete_onboarding_accepts_legacy_mutation_methods(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="onboardcompat",
                password=strong_password("compatpass123"),
                role="admin",
            )
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("onboardcompat",),
            ).fetchone()
            assert user is not None
            garden_ids: list[int] = []
            for label in ("post", "patch", "put"):
                cursor = conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    (f"onboard-compat-{label}-{os.urandom(3).hex()}", f"Compat {label}"),
                )
                garden_id = cursor.fetchone()["id"]
                garden_ids.append(garden_id)
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
            _, csrf = self._login_session("onboardcompat", "compatpass123")
            for index, (method_name, garden_id) in enumerate(
                zip(("post", "patch", "put"), garden_ids, strict=True),
            ):
                house_row = 70 + (index * 8)
                house_col = 70 + (index * 8)
                zone_start_row = house_row + 4
                zone_start_col = house_col + 4
                headers = self._session_headers(csrf, garden_id=garden_id)
                response = getattr(self.client, method_name)(
                    f"/api/gardens/{garden_id}/complete-onboarding",
                    headers=headers,
                    json={
                        "name": f"Completed {method_name}",
                        "grid_rows": 100,
                        "grid_cols": 100,
                        "latitude": None,
                        "longitude": None,
                        "address": "",
                        "mode": "manual",
                        "house": {
                            "row": house_row,
                            "col": house_col,
                            "width": 2,
                            "height": 2,
                            "north_degrees": 0,
                            "grid_rows": 100,
                            "grid_cols": 100,
                        },
                        "zones": [
                            {
                                "zone_code": "A",
                                "zone_name": "Area A",
                                "start_row": zone_start_row,
                                "start_col": zone_start_col,
                                "end_row": zone_start_row + 1,
                                "end_col": zone_start_col + 1,
                                "color": "#4a7c59",
                            },
                        ],
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            for index, (method_name, garden_id) in enumerate(
                zip(("post", "patch", "put"), garden_ids, strict=True),
            ):
                house_row = 70 + (index * 8)
                house_col = 70 + (index * 8)
                garden = conn.execute(
                    "SELECT name, onboarding_complete FROM gardens WHERE id = %s",
                    (garden_id,),
                ).fetchone()
                assert garden is not None
                self.assertEqual(garden["name"], f"Completed {method_name}")
                self.assertEqual(int(garden["onboarding_complete"]), 1)
                layout = conn.execute(
                    """
                    SELECT house_row, house_col, house_width, house_height
                    FROM layout_state
                    WHERE garden_id = %s
                    """,
                    (garden_id,),
                ).fetchone()
                assert layout is not None
                self.assertEqual(int(layout["house_row"]), house_row)
                self.assertEqual(int(layout["house_col"]), house_col)
        finally:
            db.return_db(conn)

    def test_update_garden_settings_syncs_layout_grid_and_clears_location(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="gardensettings",
                password=strong_password("gardenpass123"),
                role="admin",
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"garden-settings-{os.urandom(3).hex()}", "Garden Settings"),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("gardensettings",),
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
            _, csrf = self._login_session("gardensettings", "gardenpass123")
            headers = self._session_headers(csrf, garden_id=garden_id)

            initial_layout = self.client.get("/api/layout-state", headers=headers)
            self.assertEqual(initial_layout.status_code, 200)

            updated = self.client.patch(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
                json={
                    "name": "Renamed Garden",
                    "grid_rows": 34,
                    "grid_cols": 26,
                    "latitude": 59.9127,
                    "longitude": 10.7461,
                    "address": "Oslo",
                },
            )
            self.assertEqual(updated.status_code, 200)
            payload = updated.json()
            self.assertEqual(payload["name"], "Renamed Garden")
            self.assertEqual(payload["grid_rows"], 34)
            self.assertEqual(payload["grid_cols"], 26)
            self.assertEqual(payload["latitude"], 59.9127)
            self.assertEqual(payload["longitude"], 10.7461)
            self.assertEqual(payload["address"], "Oslo")

            layout = self.client.get("/api/layout-state", headers=headers)
            self.assertEqual(layout.status_code, 200)
            self.assertEqual(layout.json()["grid_rows"], 34)
            self.assertEqual(layout.json()["grid_cols"], 26)
            self.assertEqual(layout.json()["row"], 9)
            self.assertEqual(layout.json()["col"], 6)

            cleared = self.client.patch(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
                json={
                    "latitude": None,
                    "longitude": None,
                    "address": "",
                },
            )
            self.assertEqual(cleared.status_code, 200)
            self.assertIsNone(cleared.json()["latitude"])
            self.assertIsNone(cleared.json()["longitude"])
            self.assertEqual(cleared.json()["address"], "")

    def test_update_garden_settings_rejects_grid_smaller_than_existing_plot(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="gridshrink", password=strong_password("gardenpass123"), role="admin"
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"grid-shrink-{os.urandom(3).hex()}", "Grid Shrink"),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("gridshrink",),
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
            _, csrf = self._login_session("gridshrink", "gardenpass123")
            headers = self._session_headers(csrf, garden_id=garden_id)

            created = self.client.post(
                "/api/plots",
                headers=headers,
                json={
                    "plot_id": "EDGE1",
                    "zone_code": "E",
                    "zone_name": "Edge",
                    "plot_number": 1,
                    "grid_row": 20,
                    "grid_col": 20,
                },
            )
            self.assertEqual(created.status_code, 201)

            rejected = self.client.patch(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
                json={"grid_rows": 18, "grid_cols": 18},
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertIn("existing plot EDGE1", rejected.json()["detail"])

    def test_complete_onboarding_manual_rejects_zone_house_overlap(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="zonehouse", password=strong_password("onboardpass123"), role="admin"
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"zone-house-{os.urandom(3).hex()}", "Zone House"),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("zonehouse",),
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
            _, csrf = self._login_session("zonehouse", "onboardpass123")
            headers = self._session_headers(csrf, garden_id=garden_id)
            response = self.client.post(
                f"/api/gardens/{garden_id}/complete-onboarding",
                headers=headers,
                json={
                    "name": "Zone House",
                    "grid_rows": 20,
                    "grid_cols": 20,
                    "latitude": None,
                    "longitude": None,
                    "address": "",
                    "mode": "manual",
                    "house": {
                        "row": 5,
                        "col": 5,
                        "width": 4,
                        "height": 3,
                        "north_degrees": 0,
                        "grid_rows": 20,
                        "grid_cols": 20,
                    },
                    "zones": [
                        {
                            "zone_code": "B",
                            "zone_name": "Beds",
                            "start_row": 4,
                            "start_col": 4,
                            "end_row": 6,
                            "end_col": 6,
                            "color": "#4a7c59",
                        },
                    ],
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("overlaps the house", response.json()["detail"])

    def test_create_zone_reports_skipped_cells(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="zonereport", password=strong_password("zonepass123"), role="admin"
            )
            cursor = conn.execute(
                """
                INSERT INTO gardens (slug, name, grid_rows, grid_cols)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (f"zone-report-{os.urandom(3).hex()}", "Zone Report", 100, 100),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("zonereport",),
            ).fetchone()
            assert user is not None
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, int(user["id"])),
            )
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color
                )
                VALUES (%s, %s, %s, %s, %s, %s, '', '', NULL)
                """,
                ("EXA1", "EXA", "Existing", 1, 80, 80),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("EXA1", int(user["id"]), garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("zonereport", "zonepass123")
            headers = self._session_headers(csrf, garden_id=garden_id)

            self.client.get("/api/layout-state", headers=headers)

            response = self.client.post(
                f"/api/gardens/{garden_id}/zones",
                headers=headers,
                json={
                    "zone_code": "B",
                    "zone_name": "Beds",
                    "start_row": 80,
                    "start_col": 80,
                    "end_row": 80,
                    "end_col": 81,
                    "color": "#4a7c59",
                },
            )
            self.assertEqual(response.status_code, 201)
            payload = response.json()
            self.assertEqual(payload["requested_cells"], 2)
            self.assertEqual(payload["plots_created"], 1)
            self.assertEqual(payload["skipped_cells"], 1)
            self.assertEqual(len(payload["plots"]), 1)
            self.assertTrue(str(payload["plots"][0]["plot_id"]).startswith("B"))

    def test_create_zone_rejects_house_overlap(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="zoneoverlap", password=strong_password("zonepass123"), role="admin"
            )
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"zone-overlap-{os.urandom(3).hex()}", "Zone Overlap"),
            )
            garden_id = cursor.fetchone()["id"]
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
                ("zoneoverlap",),
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
            _, csrf = self._login_session("zoneoverlap", "zonepass123")
            headers = self._session_headers(csrf, garden_id=garden_id)

            self.client.get("/api/layout-state", headers=headers)
            response = self.client.post(
                f"/api/gardens/{garden_id}/zones",
                headers=headers,
                json={
                    "zone_code": "H",
                    "zone_name": "House Clash",
                    "start_row": 9,
                    "start_col": 6,
                    "end_row": 10,
                    "end_col": 7,
                    "color": "#4a7c59",
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("overlaps the house", response.json()["detail"])

    def test_garden_membership_last_admin_invariant_uses_active_admins_only(self) -> None:
        conn = db.get_db()
        try:
            owner = create_user(
                conn, username="activeowner", password=strong_password("ownerpass123"), role="admin"
            )
            helper = create_user(
                conn,
                username="inactivehelper",
                password=strong_password("helperpass123"),
                role="admin",
            )
            conn.commit()
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"active-admin-{os.urandom(3).hex()}", "Active Admin Guard"),
            )
            garden_id = cursor.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, int(owner["id"])),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, int(helper["id"])),
            )
            conn.execute(
                "UPDATE auth_users SET is_active = 0 WHERE id = %s",
                (int(helper["id"]),),
            )
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = %s",
                (int(helper["id"]),),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("activeowner", "ownerpass123")
            headers = self._session_headers(csrf)

            demote_last_active = self.client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={"username": "activeowner", "role": "viewer"},
            )
            self.assertEqual(demote_last_active.status_code, 409)
            self.assertIn("at least one admin", demote_last_active.json()["detail"])

            remove_inactive_admin = self.client.delete(
                f"/api/gardens/{garden_id}/memberships/{int(helper['id'])}",
                headers=headers,
            )
            self.assertEqual(remove_inactive_admin.status_code, 200)

    def test_garden_membership_mutations_emit_audit_events(self) -> None:
        conn = db.get_db()
        try:
            target = create_user(
                conn,
                username="auditeditor",
                password=strong_password("auditpass123"),
                role="editor",
            )
            create_user(
                conn, username="auditadmin", password=strong_password("auditadmin123"), role="admin"
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("auditadmin", "auditadmin123")
            headers = self._session_headers(csrf)

            slug = f"audit-garden-{os.urandom(3).hex()}"
            created = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Audit Garden", "slug": slug},
            )
            self.assertEqual(created.status_code, 201)
            garden_id = int(created.json()["id"])

            add_membership = self.client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={"username": "auditeditor", "role": "viewer"},
            )
            self.assertEqual(add_membership.status_code, 200)

            update_membership = self.client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={"username": "auditeditor", "role": "editor"},
            )
            self.assertEqual(update_membership.status_code, 200)

            remove_membership = self.client.delete(
                f"/api/gardens/{garden_id}/memberships/{int(target['id'])}",
                headers=headers,
            )
            self.assertEqual(remove_membership.status_code, 200)

        conn = db.get_db()
        try:
            create_event = conn.execute(
                """
                SELECT garden_id
                FROM audit_events
                WHERE detail LIKE %s
                LIMIT 1
                """,
                ("garden.create %",),
            ).fetchone()
            self.assertIsNotNone(create_event)
            assert create_event is not None
            self.assertEqual(int(create_event["garden_id"]), garden_id)

            upsert_events = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM audit_events
                WHERE detail LIKE %s
                  AND garden_id = %s
                """,
                ("garden.membership.upsert %", garden_id),
            ).fetchone()
            self.assertGreaterEqual(int(upsert_events["c"]), 2)

            remove_event = conn.execute(
                """
                SELECT garden_id
                FROM audit_events
                WHERE detail LIKE %s
                LIMIT 1
                """,
                ("garden.membership.remove %",),
            ).fetchone()
            self.assertIsNotNone(remove_event)
            assert remove_event is not None
            self.assertEqual(int(remove_event["garden_id"]), garden_id)
        finally:
            db.return_db(conn)

    def test_layout_state_isolated_by_garden_context(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session(username, password)
            default_headers = self._session_headers(csrf, garden_id=default_garden_id)
            second_headers = self._session_headers(csrf, garden_id=second_garden_id)

            update_default = self.client.patch(
                "/api/layout-state",
                headers=default_headers,
                json={"row": 3, "col": 4, "width": 9, "height": 6, "north_degrees": 45},
            )
            self.assertEqual(update_default.status_code, 200)

            update_second = self.client.patch(
                "/api/layout-state",
                headers=second_headers,
                json={"row": 12, "col": 10, "width": 7, "height": 5, "north_degrees": 135},
            )
            self.assertEqual(update_second.status_code, 200)

            default_layout = self.client.get("/api/layout-state", headers=default_headers)
            self.assertEqual(default_layout.status_code, 200)
            self.assertEqual(
                default_layout.json(),
                {
                    "row": 3,
                    "col": 4,
                    "width": 9,
                    "height": 6,
                    "north_degrees": 45,
                    "grid_rows": 30,
                    "grid_cols": 22,
                },
            )

            second_layout = self.client.get("/api/layout-state", headers=second_headers)
            self.assertEqual(second_layout.status_code, 200)
            self.assertEqual(
                second_layout.json(),
                {
                    "row": 12,
                    "col": 10,
                    "width": 7,
                    "height": 5,
                    "north_degrees": 135,
                    "grid_rows": 30,
                    "grid_cols": 22,
                },
            )

    def test_trusted_host_middleware_rejects_unknown_host(self) -> None:
        response = self.client.get("/api/plots", headers={"host": "evil.example"})
        self.assertEqual(response.status_code, 400)

    def test_production_rejects_untrusted_forwarding_headers(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "AUTH_REQUIRED": "false",
                "TRUST_PROXY_HEADERS": "false",
            },
            clear=False,
        ):
            response = self.client.get(
                "/api/plots",
                headers={"x-forwarded-for": "203.0.113.4"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("TRUST_PROXY_HEADERS", response.json()["detail"])

    def test_api_request_body_limit_is_enforced(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAX_API_BODY_BYTES": "24",
            },
            clear=False,
        ):
            response = self.client.post(
                "/api/snapshots",
                json={"name": "this payload is definitely too large"},
            )
        self.assertEqual(response.status_code, 413)

    def test_api_request_body_limit_is_enforced_without_content_length(self) -> None:
        async def send_lengthless_request() -> int:
            body = b'{"username":"' + (b"a" * 64) + b'","password":"pw"}'
            messages = [
                {"type": "http.request", "body": body[:16], "more_body": True},
                {"type": "http.request", "body": body[16:], "more_body": False},
            ]
            sent: list[dict[str, object]] = []

            async def receive() -> dict[str, object]:
                if messages:
                    return messages.pop(0)
                return {"type": "http.disconnect"}

            async def send(message: dict[str, object]) -> None:
                sent.append(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/auth/login",
                "raw_path": b"/api/auth/login",
                "query_string": b"",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"application/json"),
                ],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            }
            await app(scope, receive, send)
            start = next(message for message in sent if message["type"] == "http.response.start")
            return int(start["status"])

        with patch.dict(os.environ, {"MAX_API_BODY_BYTES": "24"}, clear=False):
            status_code = asyncio.run(send_lengthless_request())
        self.assertEqual(status_code, 413)

    def test_csp_report_only_header_is_present(self) -> None:
        with patch.dict(
            os.environ,
            {"CSP_REPORT_ONLY": "true"},
            clear=False,
        ):
            response = self.client.get("/api/plots")
        self.assertEqual(response.status_code, 200)
        self.assertIn("content-security-policy-report-only", response.headers)
        policy = response.headers["content-security-policy-report-only"]
        self.assertIn("report-uri", policy)
        self.assertIn("require-trusted-types-for 'script'", policy)
        self.assertIn("trusted-types gardenops-html default", policy)

    def test_csp_report_endpoint_is_public_and_accepts_json(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            response = self.client.post(
                "/api/security/csp-report",
                json={"csp-report": {"blocked-uri": "https://example.com"}},
            )
        self.assertEqual(response.status_code, 204)

    def test_ai_garden_chat_daily_budget_enforced(self) -> None:
        self._create_test_user("ai_budget_user", "ai-budget-pass", role="editor")

        response_block = type("TextBlock", (), {"type": "text", "text": "Use compost."})()
        response_payload = type("AnthropicResponse", (), {"content": [response_block]})()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = response_payload

        with (
            patch.dict(
                os.environ,
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_MODE": "session",
                    "AUTH_API_KEY": "",
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                    "AI_CHAT_DAILY_BUDGET_USER": "1",
                    "AI_CHAT_DAILY_BUDGET_GARDEN": "5",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.ai_provider.Anthropic",
                return_value=mocked_client,
            ),
        ):
            client = self._new_client()
            _, csrf = self._login_session("ai_budget_user", "ai-budget-pass", client=client)
            headers = self._session_headers(csrf)

            first = client.post(
                "/api/ai/garden-chat",
                headers=headers,
                json={"message": "What should I plant?", "history": []},
            )
            second = client.post(
                "/api/ai/garden-chat",
                headers=headers,
                json={"message": "And next?", "history": []},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["reply"], "Use compost.")
        self.assertEqual(second.status_code, 429)
        self.assertIn("daily budget exhausted", second.json()["detail"])

    def test_plot_plants_not_found(self) -> None:
        response = self.client.get(
            "/api/plots/NONEXISTENT/plants",
        )
        self.assertEqual(response.status_code, 404)

    def test_remove_nonexistent_plant_from_plot(self) -> None:
        response = self.client.delete(
            "/api/plots/B1/plants/NOPE",
        )
        self.assertEqual(response.status_code, 404)

    def test_create_plot_exceeds_grid_rows(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "OOB1",
                "zone_code": "X",
                "zone_name": "X",
                "plot_number": 1,
                "grid_row": 101,
                "grid_col": 1,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_create_plot_exceeds_grid_cols(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "OOB2",
                "zone_code": "X",
                "zone_name": "X",
                "plot_number": 1,
                "grid_row": 1,
                "grid_col": 101,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_create_plot_zero_row(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "OOB3",
                "zone_code": "X",
                "zone_name": "X",
                "plot_number": 1,
                "grid_row": 0,
                "grid_col": 1,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_create_duplicate_plot_id(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "B1",
                "zone_code": "B",
                "zone_name": "Bed",
                "plot_number": 1,
                "grid_row": 15,
                "grid_col": 15,
            },
        )
        self.assertIn(response.status_code, [400, 409])

    def test_delete_plot_cleans_up_assignments(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            conn.execute(
                "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                ("DEL1", "X", "Test", 1, 28, 20, "", "", None),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES ('DEL1', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (self._owner_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('DEL1', 'PLT-TEST', 1)
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.delete("/api/plots/DEL1")
        self.assertEqual(response.status_code, 204)

        conn = db.get_db()
        orphans = conn.execute(
            "SELECT COUNT(*) AS c FROM plot_plants WHERE plot_id = 'DEL1'",
        ).fetchone()["c"]
        db.return_db(conn)
        self.assertEqual(orphans, 0)

    def test_delete_plot_cleans_all_reference_tables(self) -> None:
        plot_id = "DALL1"
        garden_id = self._insert_plot_reference_matrix(plot_id)

        response = self.client.delete(f"/api/plots/{plot_id}")

        self.assertEqual(response.status_code, 204, response.text)
        for table, column in (
            ("plots", "plot_id"),
            ("plot_ownership", "plot_id"),
            ("plot_plants", "plot_id"),
            ("garden_task_plots", "plot_id"),
            ("garden_issue_plots", "plot_id"),
            ("garden_journal_entry_plots", "plot_id"),
            ("harvest_entry_plots", "plot_id"),
            ("garden_calendar_event_plots", "plot_id"),
            ("plot_elevations", "plot_id"),
            ("plot_elevation_overrides", "plot_id"),
            ("media_links", "target_id"),
        ):
            with self.subTest(table=table):
                self.assertEqual(self._plot_reference_count(table, column, plot_id), 0)
        conn = db.get_db()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = %s",
                    (f"asset-{plot_id}",),
                ).fetchone(),
            )
            obstacle = conn.execute(
                """
                SELECT linked_plot_id
                FROM shademap_obstacles
                WHERE garden_id = %s AND label = 'Reference tree'
                """,
                (garden_id,),
            ).fetchone()
            self.assertIsNotNone(obstacle)
            self.assertIsNone(obstacle["linked_plot_id"])
            state = conn.execute(
                "SELECT selected_plot_id FROM shademap_state WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            self.assertIsNone(state["selected_plot_id"])
        finally:
            db.return_db(conn)

    def test_plot_delete_impact_counts_reference_tables(self) -> None:
        plot_id = "DIMP1"
        self._insert_plot_reference_matrix(plot_id)

        response = self.client.get(f"/api/plots/{plot_id}/delete-impact")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["plot_id"], plot_id)
        self.assertTrue(payload["has_dependents"])
        self.assertEqual(payload["total_dependent_references"], 12)
        self.assertEqual(
            payload["counts"],
            {
                "garden_calendar_event_plots": 1,
                "garden_issue_plots": 1,
                "garden_journal_entry_plots": 1,
                "garden_task_plots": 1,
                "harvest_entry_plots": 1,
                "media_assets_removed": 1,
                "media_links": 1,
                "plot_elevation_overrides": 1,
                "plot_elevations": 1,
                "plot_ownership": 1,
                "plot_plants": 1,
                "plots": 1,
                "shademap_obstacles": 1,
                "shademap_state": 1,
            },
        )

    def test_create_plot_on_occupied_cell(self) -> None:
        response = self.client.post(
            "/api/plots",
            json={
                "plot_id": "COLLISION",
                "zone_code": "B",
                "zone_name": "Bed",
                "plot_number": 99,
                "grid_row": 1,
                "grid_col": 1,
            },
        )
        self.assertIn(response.status_code, [400, 409])

    def test_concurrent_create_plot_attempts_cannot_double_book_cell(self) -> None:
        def create(plot_id: str) -> int:
            client = self._new_client()
            response = client.post(
                "/api/plots",
                json={
                    "plot_id": plot_id,
                    "zone_code": "C",
                    "zone_name": "Concurrent",
                    "plot_number": 1 if plot_id.endswith("A") else 2,
                    "grid_row": 25,
                    "grid_col": 21,
                },
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(create, ("CONCUR-A", "CONCUR-B")))

        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertTrue(all(status in {201, 400, 409} for status in statuses), statuses)

        plots = self.client.get("/api/plots").json()
        occupants = [
            plot["plot_id"] for plot in plots if plot["grid_row"] == 25 and plot["grid_col"] == 21
        ]
        self.assertEqual(len(occupants), 1)

    def test_database_rejects_duplicate_plot_cell_in_same_garden(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col
                )
                VALUES ('DBDUP-A', %s, 'D', 'Database', 1, 26, 21)
                """,
                (garden_id,),
            )
            with self.assertRaises(psycopg.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO plots (
                        plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col
                    )
                    VALUES ('DBDUP-B', %s, 'D', 'Database', 2, 26, 21)
                    """,
                    (garden_id,),
                )
            conn.rollback()
        finally:
            db.return_db(conn)

    def test_same_grid_cell_allowed_in_different_gardens(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session(username, password)
            default_headers = self._session_headers(csrf, garden_id=default_garden_id)
            second_headers = self._session_headers(csrf, garden_id=second_garden_id)

            first = self.client.post(
                "/api/plots",
                headers=default_headers,
                json={
                    "plot_id": "DG-CELL",
                    "zone_code": "D",
                    "zone_name": "Default",
                    "plot_number": 101,
                    "grid_row": 27,
                    "grid_col": 21,
                },
            )
            second = self.client.post(
                "/api/plots",
                headers=second_headers,
                json={
                    "plot_id": "SG-CELL",
                    "zone_code": "S",
                    "zone_name": "Second",
                    "plot_number": 102,
                    "grid_row": 27,
                    "grid_col": 21,
                },
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)

    def test_batch_move_to_same_target(self) -> None:
        conn = db.get_db()
        conn.execute(
            "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            ("MV1", "X", "Test", 1, 27, 1, "", "", None),
        )
        conn.execute(
            "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            ("MV2", "X", "Test", 2, 27, 2, "", "", None),
        )
        conn.commit()
        db.return_db(conn)

        response = self.client.post(
            "/api/plots/batch-move",
            json={
                "moves": [
                    {"plot_id": "MV1", "grid_row": 30, "grid_col": 22},
                    {"plot_id": "MV2", "grid_row": 30, "grid_col": 22},
                ],
            },
        )
        self.assertIn(response.status_code, [400, 409])

    def test_move_plant_to_nonexistent_plot(self) -> None:
        self.client.post("/api/plots/B1/plants/PLT-TEST", json={"quantity": 1})
        response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST/move/NONEXISTENT",
        )
        self.assertEqual(response.status_code, 404)
        self.client.delete("/api/plots/B1/plants/PLT-TEST")

    def test_delete_nonexistent_plot(self) -> None:
        response = self.client.delete("/api/plots/NOPE-999")
        self.assertEqual(response.status_code, 404)

    def test_plot_alerts_returns_structure(self) -> None:
        resp = self.client.get("/api/plots/alerts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("task_plots", data)
        self.assertIn("issue_plots", data)
        self.assertIn("frost_plots", data)
        self.assertIsInstance(data["task_plots"], list)
        self.assertIsInstance(data["issue_plots"], list)
        self.assertIsInstance(data["frost_plots"], list)

    def test_bulk_seen_growing_update(self) -> None:
        self.client.post("/api/plants", json={"plt_id": "SG-1", "name": "Test", "category": "frø"})
        self.client.post("/api/plots/B1/plants/SG-1", json={"quantity": 1})
        self.client.post("/api/plots/B2/plants/SG-1", json={"quantity": 1})
        response = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={
                "updates": [
                    {
                        "plot_id": "B1",
                        "plt_id": "SG-1",
                        "seen_growing": True,
                        "seen_growing_date": "2026-03-23",
                    },
                    {
                        "plot_id": "B2",
                        "plt_id": "SG-1",
                        "seen_growing": False,
                        "seen_growing_date": "2026",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)

    def test_list_plants_reports_presence_status(self) -> None:
        current_year = date.today().year
        previous_year = current_year - 1
        self.client.post(
            "/api/plants",
            json={"plt_id": "SG-PRESENT", "name": "Status Present", "category": "frø"},
        )
        self.client.post("/api/plots/B1/plants/SG-PRESENT", json={"quantity": 1})

        self.client.post(
            "/api/plants", json={"plt_id": "SG-MIXED", "name": "Status Mixed", "category": "frø"}
        )
        self.client.post("/api/plots/B1/plants/SG-MIXED", json={"quantity": 1})
        self.client.post("/api/plots/B2/plants/SG-MIXED", json={"quantity": 1})

        self.client.post(
            "/api/plants", json={"plt_id": "SG-GONE", "name": "Status Gone", "category": "frø"}
        )
        self.client.post("/api/plots/B1/plants/SG-GONE", json={"quantity": 1})
        self.client.post("/api/plots/B2/plants/SG-GONE", json={"quantity": 1})

        update = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={
                "updates": [
                    {
                        "plot_id": "B1",
                        "plt_id": "SG-MIXED",
                        "seen_growing": True,
                        "seen_growing_date": f"{current_year}-03-23",
                    },
                    {
                        "plot_id": "B2",
                        "plt_id": "SG-MIXED",
                        "seen_growing": False,
                        "seen_growing_date": str(current_year),
                    },
                    {
                        "plot_id": "B1",
                        "plt_id": "SG-GONE",
                        "seen_growing": False,
                        "seen_growing_date": str(previous_year),
                    },
                    {
                        "plot_id": "B2",
                        "plt_id": "SG-GONE",
                        "seen_growing": False,
                        "seen_growing_date": str(current_year),
                    },
                ],
            },
        )
        self.assertEqual(update.status_code, 200)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Status").json()
        }
        self.assertEqual(plants["SG-PRESENT"]["presence_status"], "present")
        self.assertIsNone(plants["SG-PRESENT"]["last_not_seen_year"])
        self.assertEqual(plants["SG-MIXED"]["presence_status"], "mixed")
        self.assertEqual(plants["SG-MIXED"]["last_not_seen_year"], str(current_year))
        self.assertEqual(plants["SG-GONE"]["presence_status"], "mixed")
        self.assertEqual(plants["SG-GONE"]["last_not_seen_year"], str(current_year))

    def test_list_plants_ignores_historical_seen_growing_for_current_presence(self) -> None:
        previous_year = date.today().year - 1
        self.client.post(
            "/api/plants",
            json={"plt_id": "SG-HIST", "name": "Status Historical", "category": "frø"},
        )
        self.client.post("/api/plots/B1/plants/SG-HIST", json={"quantity": 1})
        self.client.post("/api/plots/B2/plants/SG-HIST", json={"quantity": 1})

        update = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={
                "updates": [
                    {
                        "plot_id": "B1",
                        "plt_id": "SG-HIST",
                        "seen_growing": False,
                        "seen_growing_date": str(previous_year),
                    },
                    {
                        "plot_id": "B2",
                        "plt_id": "SG-HIST",
                        "seen_growing": True,
                        "seen_growing_date": f"{previous_year}-04-01",
                    },
                ],
            },
        )
        self.assertEqual(update.status_code, 200)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Historical").json()
        }
        self.assertFalse(plants["SG-HIST"]["observed_this_year"])
        self.assertEqual(plants["SG-HIST"]["presence_status"], "present")
        self.assertIsNone(plants["SG-HIST"]["last_not_seen_year"])

    def test_bulk_seen_growing_rejects_missing_row(self) -> None:
        response = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={"updates": [{"plot_id": "NOPE", "plt_id": "NOPE", "seen_growing": True}]},
        )
        self.assertIn(response.status_code, (400, 404))

    def test_bulk_seen_growing_rejects_day_on_not_seen(self) -> None:
        self.client.post("/api/plants", json={"plt_id": "SG-2", "name": "T2", "category": "frø"})
        self.client.post("/api/plots/B1/plants/SG-2", json={"quantity": 1})
        response = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={
                "updates": [
                    {
                        "plot_id": "B1",
                        "plt_id": "SG-2",
                        "seen_growing": False,
                        "seen_growing_date": "2026-03-23",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_bulk_seen_growing_rejects_foreign_garden_row(self) -> None:
        os.environ.update(
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            }
        )
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()
            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            default_headers = self._session_headers(csrf, garden_id=gid1)
            second_headers = self._session_headers(csrf, garden_id=gid2)
            plot_id = f"SG2-PLOT-{os.urandom(3).hex()}"
            plant_id = f"SG2-PLANT-{os.urandom(3).hex()}"

            created_plot = client.post(
                "/api/plots",
                headers=second_headers,
                json={
                    "plot_id": plot_id,
                    "zone_code": "P",
                    "zone_name": "Seen Growing Garden Two",
                    "plot_number": 1,
                    "grid_row": 96,
                    "grid_col": 96,
                    "sub_zone": "",
                    "notes": "",
                    "color": None,
                },
            )
            self.assertEqual(created_plot.status_code, 201, created_plot.text)

            created_plant = client.post(
                "/api/plants",
                headers=second_headers,
                json={"plt_id": plant_id, "name": "Seen Growing Plant", "category": "frø"},
            )
            self.assertEqual(created_plant.status_code, 201, created_plant.text)

            assigned = client.post(
                f"/api/plots/{plot_id}/plants/{plant_id}",
                headers=second_headers,
                json={"quantity": 1},
            )
            self.assertEqual(assigned.status_code, 201, assigned.text)

            response = client.patch(
                "/api/plots/plants/seen-growing",
                headers=default_headers,
                json={
                    "updates": [
                        {
                            "plot_id": plot_id,
                            "plt_id": plant_id,
                            "seen_growing": True,
                            "seen_growing_date": "2026-03-23",
                        },
                    ],
                },
            )
            self.assertEqual(response.status_code, 404, response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_bulk_seen_growing_rejects_poisoned_pair_for_plant_outside_active_garden(
        self,
    ) -> None:
        editor = self._create_test_user("seen_owner", "seenownerpass", role="editor")
        default_garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            second_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("seen-growing-second", "Seen Growing Second"),
                ).fetchone()["id"],
            )
            conn.execute(
                """
                UPDATE plot_ownership
                SET owner_user_id = %s
                WHERE plot_id = 'B1' AND garden_id = %s
                """,
                (int(editor["id"]), default_garden_id),
            )
            conn.execute(
                """
                INSERT INTO plants (plt_id, name, category)
                VALUES ('SG-FOREIGN-PLANT', 'Foreign status plant', 'frø')
                """,
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES ('SG-FOREIGN-PLANT', %s, %s)
                """,
                (int(editor["id"]), second_garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'SG-FOREIGN-PLANT', 1)
                """,
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
            _, csrf = self._login_session("seen_owner", "seenownerpass", client=client)
            response = client.patch(
                "/api/plots/plants/seen-growing",
                headers=self._session_headers(csrf, garden_id=default_garden_id),
                json={
                    "updates": [
                        {
                            "plot_id": "B1",
                            "plt_id": "SG-FOREIGN-PLANT",
                            "seen_growing": True,
                            "seen_growing_date": "2026-06-04",
                        },
                    ],
                },
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("not found", response.json()["detail"])
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT seen_growing, seen_growing_date
                FROM plot_plants
                WHERE plot_id = 'B1' AND plt_id = 'SG-FOREIGN-PLANT'
                """,
            ).fetchone()
            assert row is not None
            self.assertFalse(bool(row["seen_growing"]))
            self.assertIsNone(row["seen_growing_date"])
        finally:
            db.return_db(conn)
