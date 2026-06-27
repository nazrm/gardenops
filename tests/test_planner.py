from datetime import date

import gardenops.db as db
from gardenops.router_helpers import generate_public_id
from tests.base import BaseApiTest


class TestPlannerApi(BaseApiTest):
    """Tests for the planting planner suggestion engine."""

    def setUp(self) -> None:
        super().setUp()
        conn = db.get_db()
        default_garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert default_garden is not None
        garden_id = int(default_garden["id"])
        self.garden_id = garden_id

        # Add extra plants with bloom/color/hardiness data
        conn.execute(
            "INSERT INTO plants "
            "(plt_id, name, latin, category, bloom_month, color, "
            "hardiness, height_cm, light, link, deer_resistant) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "PLT-PLANNER",
                "Lavender",
                "Lavandula angustifolia",
                "busker",
                "juni-august",
                "lilla",
                "H5",
                60,
                "sol",
                "",
                1,
            ),
        )
        conn.execute(
            "INSERT INTO plants "
            "(plt_id, name, latin, category, bloom_month, color, "
            "hardiness, height_cm, light, link, deer_resistant) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "PLT-SHADE",
                "Hosta",
                "Hosta sieboldiana",
                "staude",
                "juli",
                "hvit",
                "H6",
                40,
                "shade",
                "",
                0,
            ),
        )
        # Third empty plot
        conn.execute(
            "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            ("B3", "B", "Bed", 3, 1, 3, "", "", None),
        )

        # Commit pending work, disable FK checks, insert ownership, re-enable
        conn.commit()
        for plt_id in ("PLT-TEST", "PLT-002", "PLT-PLANNER", "PLT-SHADE"):
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (plt_id, self._owner_id, garden_id),
            )
        for plot_id in ("B1", "B2", "B3"):
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (plot_id, self._owner_id, garden_id),
            )
        conn.commit()
        # Assign PLT-TEST to B1 — so B2, B3 are empty and PLT-002,PLT-PLANNER,PLT-SHADE unassigned
        conn.execute(
            "INSERT INTO plot_plants "
            "(plot_id, plt_id, quantity) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            ("B1", "PLT-TEST", 1),
        )
        conn.commit()
        db.return_db(conn)

    def test_planner_suggestions_empty_plots(self) -> None:
        """Planner should suggest unassigned plants for empty plots."""
        resp = self.client.get("/api/planner/suggestions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("plots", data)
        self.assertIn("bloom_gaps", data)
        self.assertIn("garden_stats", data)

        stats = data["garden_stats"]
        self.assertGreaterEqual(stats["total_plots"], 3)
        self.assertGreaterEqual(stats["empty_plots"], 2)

        # There should be suggestions for the empty plots
        plots_with_suggestions = [p for p in data["plots"] if len(p["suggestions"]) > 0]
        self.assertGreater(len(plots_with_suggestions), 0)

        # Each suggestion should have required fields
        for ps in data["plots"]:
            for s in ps["suggestions"]:
                self.assertIn("plt_id", s)
                self.assertIn("name", s)
                self.assertIn("score", s)
                self.assertIn("reasons", s)
                self.assertGreater(s["score"], 0)
                self.assertGreater(len(s["reasons"]), 0)

    def test_planner_garden_profile(self) -> None:
        """Verify garden profile returns correct structure."""
        resp = self.client.get("/api/planner/garden-profile")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("total_plots", data)
        self.assertIn("empty_plots", data)
        self.assertIn("planted_plots", data)
        self.assertIn("bloom_coverage", data)
        self.assertIn("bloom_gaps", data)
        self.assertIn("categories", data)
        self.assertIn("colors", data)
        self.assertIn("hardiness_range", data)
        self.assertIn("deer_resistant_count", data)
        self.assertIn("deer_vulnerable_count", data)

        self.assertIsInstance(data["bloom_coverage"], list)
        self.assertIsInstance(data["bloom_gaps"], list)
        self.assertIsInstance(data["categories"], dict)
        self.assertGreaterEqual(data["total_plots"], 3)
        self.assertGreaterEqual(data["planted_plots"], 1)

    def test_planner_suggestions_with_goal(self) -> None:
        """Test goal filtering enhances relevant scores."""
        # Shade goal should boost shade-tolerant plants
        resp = self.client.get("/api/planner/suggestions?goal=shade&limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("plots", data)

        # Deer goal
        resp = self.client.get("/api/planner/suggestions?goal=deer&limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("plots", data)

    def test_planner_companions(self) -> None:
        """Test companion check endpoint."""
        # Check companion/conflict for PLT-002 (busker) in B1 (has PLT-TEST, frø)
        resp = self.client.get("/api/planner/companions?plot_id=B1&plt_id=PLT-002")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("companions", data)
        self.assertIn("conflicts", data)
        self.assertIsInstance(data["companions"], list)
        self.assertIsInstance(data["conflicts"], list)

        # frø + busker should be a companion
        if len(data["companions"]) > 0:
            self.assertIn("description", data["companions"][0])

    def test_companion_candidate_must_belong_to_active_garden(self) -> None:
        """Candidate plant lookup should not read categories from another garden."""
        conn = db.get_db()
        try:
            other_garden = conn.execute(
                """
                INSERT INTO gardens (slug, name, owner_user_id)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                ("planner-other", "Planner Other", self._owner_id),
            ).fetchone()
            assert other_garden is not None
            conn.execute(
                "INSERT INTO plants (plt_id, name, category) VALUES (%s,%s,%s)",
                ("PLT-OTHER-GARDEN", "Foreign Plant", "busker"),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("PLT-OTHER-GARDEN", self._owner_id, int(other_garden["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        resp = self.client.get("/api/planner/companions?plot_id=B1&plt_id=PLT-OTHER-GARDEN")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"companions": [], "conflicts": []})

    def test_planner_specific_plot(self) -> None:
        """Test suggestions for a specific plot_id."""
        resp = self.client.get("/api/planner/suggestions?plot_id=B2")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("plots", data)
        self.assertEqual(len(data["plots"]), 1)
        self.assertEqual(data["plots"][0]["plot_id"], "B2")

    def test_planner_suggestions_use_sunlight_snapshot(self) -> None:
        """Direct-sun snapshots should reward sun-loving candidates on that plot."""
        resp = self.client.get("/api/planner/suggestions?plot_id=B2&sunlit_plot_ids=B2")
        self.assertEqual(resp.status_code, 200)
        suggestions = resp.json()["plots"][0]["suggestions"]

        planner = next(s for s in suggestions if s["plt_id"] == "PLT-PLANNER")
        shade = next(s for s in suggestions if s["plt_id"] == "PLT-SHADE")

        self.assertGreater(planner["score"], shade["score"])
        self.assertTrue(any("sunlit snapshot" in reason.lower() for reason in planner["reasons"]))

    def test_planner_suggestions_apply_succession_rotation(self) -> None:
        """Recent harvests should prefer rotating into a different category."""
        conn = db.get_db()
        now_ms = db.current_timestamp_ms()
        cursor = conn.execute(
            """
            INSERT INTO harvest_entries (
                public_id, garden_id, occurred_on, quantity, unit, quality, notes,
                actor_user_id, created_at_ms, updated_at_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                generate_public_id("hrv"),
                self.garden_id,
                date.today().isoformat(),
                1,
                "kg",
                "good",
                "",
                None,
                now_ms,
                now_ms,
            ),
        )
        entry_id = cursor.fetchone()["id"]
        conn.execute(
            "INSERT INTO harvest_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (entry_id, "PLT-PLANNER"),
        )
        conn.execute(
            "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            (entry_id, "B2"),
        )
        conn.commit()
        db.return_db(conn)

        resp = self.client.get("/api/planner/suggestions?plot_id=B2")
        self.assertEqual(resp.status_code, 200)
        suggestions = resp.json()["plots"][0]["suggestions"]

        shade = next(s for s in suggestions if s["plt_id"] == "PLT-SHADE")
        planner = next(s for s in suggestions if s["plt_id"] == "PLT-PLANNER")

        self.assertTrue(
            any("rotates after recent lavender harvest" in r.lower() for r in shade["reasons"])
        )
        self.assertTrue(
            any("rotating away from busker" in reason.lower() for reason in planner["reasons"])
        )
