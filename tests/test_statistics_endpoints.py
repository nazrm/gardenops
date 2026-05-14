from datetime import date

import gardenops.db as db
from tests.base import BaseApiTest


class TestStatisticsActions(BaseApiTest):
    """Tests for GET /api/statistics/actions."""

    def setUp(self) -> None:
        super().setUp()
        conn = db.get_db()
        garden_row = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert garden_row is not None
        self.garden_id = int(garden_row["id"])
        for plt_id in ("PLT-TEST", "PLT-002"):
            conn.execute(
                """
                INSERT INTO plant_ownership
                    (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (plt_id, self._owner_id, self.garden_id),
            )
        for plot_id in ("B1", "B2"):
            conn.execute(
                """
                INSERT INTO plot_ownership
                    (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (plot_id, self._owner_id, self.garden_id),
            )
        conn.commit()
        db.return_db(conn)

    def test_actions_returns_expected_structure(self) -> None:
        resp = self.client.get("/api/statistics/actions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("unassigned_plants", data)
        self.assertIn("empty_plots_by_zone", data)
        self.assertIn("bloom_gap_months", data)
        self.assertIn("no_year_plants", data)
        self.assertIn("stale_plants", data)
        self.assertIn("missing_care_plants", data)

    def test_actions_detects_unassigned_plants(self) -> None:
        resp = self.client.get("/api/statistics/actions")
        data = resp.json()
        unassigned_ids = [p["plt_id"] for p in data["unassigned_plants"]]
        self.assertIn("PLT-TEST", unassigned_ids)
        self.assertIn("PLT-002", unassigned_ids)

    def test_actions_detects_empty_plots(self) -> None:
        resp = self.client.get("/api/statistics/actions")
        data = resp.json()
        all_empty_plot_ids = []
        for zone in data["empty_plots_by_zone"]:
            all_empty_plot_ids.extend(zone["plot_ids"])
            self.assertIn("zone_code", zone)
            self.assertIn("count", zone)
            self.assertEqual(zone["count"], len(zone["plot_ids"]))
        self.assertIn("B1", all_empty_plot_ids)
        self.assertIn("B2", all_empty_plot_ids)

    def test_actions_bloom_gap_months_are_valid(self) -> None:
        resp = self.client.get("/api/statistics/actions")
        data = resp.json()
        for month in data["bloom_gap_months"]:
            self.assertGreaterEqual(month, 1)
            self.assertLessEqual(month, 12)

    def test_actions_detects_missing_care(self) -> None:
        resp = self.client.get("/api/statistics/actions")
        data = resp.json()
        test_plant = next(
            (p for p in data["missing_care_plants"] if p["plt_id"] == "PLT-TEST"),
            None,
        )
        self.assertIsNotNone(test_plant)
        assert test_plant is not None
        self.assertIn("missing", test_plant)
        self.assertIn("light", test_plant["missing"])
        self.assertIn("hardiness", test_plant["missing"])

    def test_actions_assigned_plant_not_listed(self) -> None:
        """Assigning a plant to a plot should remove it from unassigned."""
        conn = db.get_db()
        conn.execute(
            "INSERT INTO plot_plants "
            "(plot_id, plt_id, quantity) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            ("B1", "PLT-TEST", 1),
        )
        conn.commit()
        db.return_db(conn)

        resp = self.client.get("/api/statistics/actions")
        data = resp.json()
        unassigned_ids = [p["plt_id"] for p in data["unassigned_plants"]]
        self.assertNotIn("PLT-TEST", unassigned_ids)

    def test_actions_stale_plants_require_observation_events(self) -> None:
        today = date.today().isoformat()
        watered = self.client.post(
            "/api/journal",
            json={
                "event_type": "watered",
                "occurred_on": today,
                "title": "Watered test plant",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(watered.status_code, 201, watered.text)
        observed = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": today,
                "title": "Observed rose",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(observed.status_code, 201, observed.text)

        resp = self.client.get("/api/statistics/actions")
        self.assertEqual(resp.status_code, 200)
        stale_ids = {plant["plt_id"] for plant in resp.json()["stale_plants"]}
        self.assertIn("PLT-TEST", stale_ids)
        self.assertNotIn("PLT-002", stale_ids)


class TestStatisticsAutomationStatus(BaseApiTest):
    """Tests for GET /api/statistics/automation-status."""

    def test_automation_status_returns_structure(self) -> None:
        resp = self.client.get("/api/statistics/automation-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("automated_tasks", data)
        self.assertIn("total", data)
        self.assertIsInstance(data["automated_tasks"], list)
        self.assertEqual(data["total"], 0)


class TestStatisticsReports(BaseApiTest):
    """Tests for GET /api/statistics/reports."""

    def test_reports_returns_ok(self) -> None:
        resp = self.client.get("/api/statistics/reports")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, dict)


class TestBadgeCounts(BaseApiTest):
    """Tests for GET /api/dashboard/badge-counts."""

    def test_badge_counts_returns_structure(self) -> None:
        resp = self.client.get("/api/dashboard/badge-counts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("overdue_tasks", data)
        self.assertIn("open_issues", data)
        self.assertIn("active_alerts", data)
        self.assertIn("unread_notifications", data)
        self.assertIsInstance(data["overdue_tasks"], int)
        self.assertIsInstance(data["open_issues"], int)
        self.assertIsInstance(data["active_alerts"], int)
        self.assertIsInstance(data["unread_notifications"], int)


class TestTodayDashboard(BaseApiTest):
    """Tests for GET /api/dashboard/today."""

    def test_today_returns_structure(self) -> None:
        resp = self.client.get("/api/dashboard/today")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("date", data)
        self.assertIn("tasks_due_today", data)
        self.assertIn("tasks_overdue", data)
        self.assertIn("tasks_upcoming", data)
        self.assertIn("active_issues", data)
        self.assertIn("weather_alerts", data)
        self.assertIn("forecast_today", data)
        self.assertIsInstance(data["tasks_due_today"], list)
        self.assertIsInstance(data["tasks_overdue"], list)
        self.assertIsInstance(data["tasks_upcoming"], list)
        self.assertIsInstance(data["active_issues"], list)
        self.assertIsInstance(data["weather_alerts"], list)


class TestExportsBackup(BaseApiTest):
    """Tests for GET /api/exports/backup."""

    def test_backup_returns_structure(self) -> None:
        resp = self.client.get("/api/exports/backup")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("garden_id", data)
        self.assertIn("exported_at_ms", data)
        self.assertIn("tasks", data)
        self.assertIn("journal", data)
        self.assertIn("issues", data)
        self.assertIn("harvest", data)
        self.assertIn("inventory", data)
        self.assertIn("inventory_transactions", data)
        self.assertIn("procurement", data)
