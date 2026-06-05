import gardenops.db as db
from tests.base import BaseApiTest


class TestIndoorPlotsMigration(BaseApiTest):
    def test_plots_table_allows_null_grid_coords(self) -> None:
        """INDOOR plots must be insertable with NULL grid_row/grid_col."""
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plots "
                "(plot_id, zone_code, zone_name, plot_number, grid_row, grid_col) "
                "VALUES ('TEST-INDOOR', 'I', 'Innendors', 0, NULL, NULL) "
                "ON CONFLICT DO NOTHING"
            )
            conn.commit()
            row = conn.execute(
                "SELECT grid_row, grid_col FROM plots WHERE plot_id = 'TEST-INDOOR'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row["grid_row"])
            self.assertIsNone(row["grid_col"])
        finally:
            db.return_db(conn)

    def test_plot_plants_room_label_column_exists(self) -> None:
        """plot_plants must have a room_label column."""
        conn = db.get_db()
        try:
            cols = [
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'plot_plants'"
                ).fetchall()
            ]
            self.assertIn("room_label", cols)
        finally:
            db.return_db(conn)

    def test_multiple_null_coords_allowed(self) -> None:
        """Multiple plots with (NULL, NULL) coords must be allowed (indoor plants)."""
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plots "
                "(plot_id, zone_code, zone_name, plot_number, grid_row, grid_col) "
                "VALUES ('TEST-NULL-1', 'I', 'Innendors', 0, NULL, NULL) "
                "ON CONFLICT DO NOTHING"
            )
            conn.execute(
                "INSERT INTO plots "
                "(plot_id, zone_code, zone_name, plot_number, grid_row, grid_col) "
                "VALUES ('TEST-NULL-2', 'I', 'Innendors', 0, NULL, NULL) "
                "ON CONFLICT DO NOTHING"
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM plots WHERE plot_id IN ('TEST-NULL-1', 'TEST-NULL-2')"
            ).fetchone()["c"]
            self.assertEqual(count, 2)
        finally:
            db.return_db(conn)


class TestEnsureIndoorPlot(BaseApiTest):
    def setUp(self) -> None:
        super().setUp()
        # Ensure at least one auth_user exists for fallback ownership lookup
        conn = db.get_db()
        try:
            if conn.execute("SELECT COUNT(*) AS c FROM auth_users").fetchone()["c"] == 0:
                from gardenops.security import create_user

                create_user(
                    conn,
                    username="indoor_test_user",
                    password="Test!abcdefghij1234567890Aa-Z9",
                    role="admin",
                )
                conn.commit()
        finally:
            db.return_db(conn)

    def test_ensure_indoor_plot_creates_plot(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            db.ensure_indoor_plot(conn, garden_id)
            row = conn.execute(
                "SELECT * FROM plots WHERE plot_id = %s", (f"INDOOR-{garden_id}",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["zone_code"], "I")
            self.assertEqual(row["zone_name"], "Innendors")
            self.assertIsNone(row["grid_row"])
            self.assertIsNone(row["grid_col"])
        finally:
            db.return_db(conn)

    def test_ensure_indoor_plot_creates_ownership(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            db.ensure_indoor_plot(conn, garden_id)
            row = conn.execute(
                "SELECT * FROM plot_ownership WHERE plot_id = %s", (f"INDOOR-{garden_id}",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["garden_id"], garden_id)
        finally:
            db.return_db(conn)

    def test_ensure_indoor_plot_is_idempotent(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            db.ensure_indoor_plot(conn, garden_id)
            db.ensure_indoor_plot(conn, garden_id)  # Should not raise
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM plots WHERE plot_id = %s", (f"INDOOR-{garden_id}",)
            ).fetchone()["c"]
            self.assertEqual(count, 1)
        finally:
            db.return_db(conn)

    def test_ensure_indoor_plot_multiple_gardens(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            conn.execute(
                "INSERT INTO gardens (slug, name) VALUES ('test-g2', 'Test Garden 2') "
                "ON CONFLICT DO NOTHING"
            )
            conn.commit()
            g2_row = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'test-g2' LIMIT 1"
            ).fetchone()
            g2_id = int(g2_row["id"])
            db.ensure_indoor_plot(conn, garden_id)
            db.ensure_indoor_plot(conn, g2_id)
            rows = conn.execute("SELECT plot_id FROM plots WHERE zone_code = 'I'").fetchall()
            ids = {r["plot_id"] for r in rows}
            self.assertEqual(ids, {f"INDOOR-{garden_id}", f"INDOOR-{g2_id}"})
        finally:
            db.return_db(conn)

    # room_label max length constraint removed during Postgres migration;
    # application-level validation now handles this.


class TestIndoorPlotGuards(BaseApiTest):
    def setUp(self) -> None:
        super().setUp()
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            db.ensure_indoor_plot(conn, garden_id)
        finally:
            db.return_db(conn)

    def test_create_plot_rejects_zone_code_I(self) -> None:
        resp = self.client.post(
            "/api/plots",
            json={
                "plot_id": "I99",
                "zone_code": "I",
                "zone_name": "Fake",
                "plot_number": 99,
                "grid_row": 1,
                "grid_col": 1,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reserved", resp.json()["detail"].lower())

    def test_delete_indoor_plot_rejected(self) -> None:
        garden_id = self._get_default_garden_id()
        resp = self.client.delete(f"/api/plots/INDOOR-{garden_id}")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("indoor", resp.json()["detail"].lower())

    def test_update_indoor_plot_rejects_grid_coords(self) -> None:
        garden_id = self._get_default_garden_id()
        resp = self.client.patch(f"/api/plots/INDOOR-{garden_id}", json={"grid_row": 5})
        self.assertEqual(resp.status_code, 400)

    def test_update_indoor_plot_allows_notes(self) -> None:
        garden_id = self._get_default_garden_id()
        resp = self.client.patch(f"/api/plots/INDOOR-{garden_id}", json={"notes": "test note"})
        self.assertEqual(resp.status_code, 200)

    def test_list_plots_exclude_indoor(self) -> None:
        all_plots = self.client.get("/api/plots").json()
        indoor_in_all = [p for p in all_plots if p["zone_code"] == "I"]
        self.assertTrue(len(indoor_in_all) >= 1)
        filtered = self.client.get("/api/plots?exclude_indoor=true").json()
        indoor_in_filtered = [p for p in filtered if p["zone_code"] == "I"]
        self.assertEqual(len(indoor_in_filtered), 0)

    def test_get_plot_alerts_separates_indoor(self) -> None:
        resp = self.client.get("/api/plots/alerts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("indoor_alerts", data)
        self.assertIn("tasks", data["indoor_alerts"])
        self.assertIn("issues", data["indoor_alerts"])

    def test_update_plot_rejects_zone_code_I(self) -> None:
        resp = self.client.patch("/api/plots/B1", json={"zone_code": "I"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reserved", resp.json()["detail"].lower())

    def test_shademap_does_not_crash_with_indoor_plot(self) -> None:
        resp = self.client.get("/api/shademap/status")
        self.assertIn(resp.status_code, {200, 400, 403, 404})


class TestIndoorFullFlow(BaseApiTest):
    """Integration test: full indoor plants lifecycle."""

    def setUp(self) -> None:
        super().setUp()
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            db.ensure_indoor_plot(conn, garden_id)
            # Insert test plants (test DB starts empty)
            user_row = conn.execute("SELECT MIN(id) AS uid FROM auth_users").fetchone()
            garden_row = conn.execute("SELECT MIN(id) AS gid FROM gardens").fetchone()
            uid = user_row["uid"] if user_row else 1
            gid = garden_row["gid"] if garden_row else 1
            for i, name in enumerate(["TestPlant A", "TestPlant B", "TestPlant C"], start=1):
                conn.execute(
                    "INSERT INTO plants (plt_id, name, category) VALUES (%s, %s, 'busker') "
                    "ON CONFLICT DO NOTHING",
                    (f"TEST-PLT-{i}", name),
                )
                conn.execute(
                    "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (f"TEST-PLT-{i}", uid, gid),
                )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_full_indoor_flow(self) -> None:
        # 1. INDOOR plot appears in plot list
        plots = self.client.get("/api/plots").json()
        indoor = [p for p in plots if p["zone_code"] == "I"]
        self.assertEqual(len(indoor), 1)
        indoor_id = indoor[0]["plot_id"]
        self.assertIsNone(indoor[0]["grid_row"])

        # 2. Excluded from map-targeted list
        map_plots = self.client.get("/api/plots?exclude_indoor=true").json()
        self.assertFalse(any(p["zone_code"] == "I" for p in map_plots))

        # 3. Use test plant IDs inserted in setUp
        pid1, pid2, pid3 = "TEST-PLT-1", "TEST-PLT-2", "TEST-PLT-3"

        # 4. Add plants with room labels
        r1 = self.client.post(
            f"/api/plots/{indoor_id}/plants/{pid1}",
            json={
                "quantity": 3,
                "room_label": "Kjokken",
            },
        )
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r1.json()["room_label"], "Kjokken")

        r2 = self.client.post(
            f"/api/plots/{indoor_id}/plants/{pid2}",
            json={
                "quantity": 1,
                "room_label": "Stue",
            },
        )
        self.assertEqual(r2.status_code, 201)

        r3 = self.client.post(
            f"/api/plots/{indoor_id}/plants/{pid3}",
            json={
                "quantity": 2,
            },
        )
        self.assertEqual(r3.status_code, 201)
        self.assertIsNone(r3.json()["room_label"])

        # 5. List plants with room_label
        plants = self.client.get(f"/api/plots/{indoor_id}/plants").json()
        self.assertEqual(len(plants), 3)
        by_id = {p["plt_id"]: p for p in plants}
        self.assertEqual(by_id[pid1]["room_label"], "Kjokken")
        self.assertEqual(by_id[pid2]["room_label"], "Stue")
        self.assertIsNone(by_id[pid3]["room_label"])

        # 6. Room labels autocomplete
        labels = self.client.get(f"/api/plots/{indoor_id}/room-labels").json()
        self.assertEqual(sorted(labels), ["Kjokken", "Stue"])

        # 7. Update room label
        r4 = self.client.patch(
            f"/api/plots/{indoor_id}/plants/{pid3}",
            json={
                "quantity": 2,
                "room_label": "Soverom",
            },
        )
        self.assertEqual(r4.status_code, 200)
        labels2 = self.client.get(f"/api/plots/{indoor_id}/room-labels").json()
        self.assertIn("Soverom", labels2)

        # 8. Remove plant
        r5 = self.client.delete(f"/api/plots/{indoor_id}/plants/{pid2}")
        self.assertEqual(r5.status_code, 204)
        plants2 = self.client.get(f"/api/plots/{indoor_id}/plants").json()
        self.assertEqual(len(plants2), 2)

        # 8. Cannot delete INDOOR plot
        r6 = self.client.delete(f"/api/plots/{indoor_id}")
        self.assertEqual(r6.status_code, 400)

        # 9. Alerts separate indoor
        alerts = self.client.get("/api/plots/alerts").json()
        self.assertNotIn(indoor_id, alerts["task_plots"])
        self.assertIn("indoor_alerts", alerts)
