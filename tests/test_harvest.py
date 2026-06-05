import os

from tests.base import BaseApiTest


class TestHarvestApi(BaseApiTest):
    """Tests for harvest CRUD and summary endpoints."""

    def test_harvest_crud_lifecycle(self) -> None:
        """Create, read, update, delete a harvest entry."""
        # Create
        resp = self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-07-15",
                "quantity": 2.5,
                "unit": "kg",
                "quality": "good",
                "notes": "First tomato harvest",
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        entry_id = data["id"]

        # Read
        resp = self.client.get(f"/api/harvest/{entry_id}")
        self.assertEqual(resp.status_code, 200)
        entry = resp.json()
        self.assertEqual(entry["occurred_on"], "2026-07-15")
        self.assertAlmostEqual(entry["quantity"], 2.5)
        self.assertEqual(entry["unit"], "kg")
        self.assertEqual(entry["quality"], "good")
        self.assertEqual(entry["notes"], "First tomato harvest")

        # Update
        resp = self.client.patch(
            f"/api/harvest/{entry_id}",
            json={
                "quantity": 3.0,
                "quality": "excellent",
                "notes": "Actually more than expected",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        # Verify update
        resp = self.client.get(f"/api/harvest/{entry_id}")
        entry = resp.json()
        self.assertAlmostEqual(entry["quantity"], 3.0)
        self.assertEqual(entry["quality"], "excellent")
        self.assertEqual(entry["notes"], "Actually more than expected")

        # Delete
        resp = self.client.delete(f"/api/harvest/{entry_id}")
        self.assertEqual(resp.status_code, 200)

        # Verify deleted
        resp = self.client.get(f"/api/harvest/{entry_id}")
        self.assertEqual(resp.status_code, 404)

    def test_harvest_list_filters(self) -> None:
        """Filter harvest entries by quality and date range."""
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-01",
                "quantity": 1.0,
                "unit": "kg",
                "quality": "excellent",
            },
        )
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-07-15",
                "quantity": 2.0,
                "unit": "kg",
                "quality": "good",
            },
        )
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-08-20",
                "quantity": 0.5,
                "unit": "kg",
                "quality": "poor",
            },
        )

        # Filter by quality
        resp = self.client.get("/api/harvest?quality=excellent")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["entries"][0]["quality"], "excellent")

        # Filter by date range
        resp = self.client.get("/api/harvest?date_from=2026-07-01&date_to=2026-07-31")
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["entries"][0]["occurred_on"], "2026-07-15")

        # No matches
        resp = self.client.get("/api/harvest?quality=fair")
        data = resp.json()
        self.assertEqual(data["total"], 0)

    def test_harvest_plant_plot_links(self) -> None:
        """Verify plant and plot linking on harvest entries."""
        resp = self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-07-15",
                "quantity": 1.0,
                "unit": "pieces",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(resp.status_code, 201)
        entry_id = resp.json()["id"]

        resp = self.client.get(f"/api/harvest/{entry_id}")
        entry = resp.json()
        self.assertIn("PLT-TEST", entry["plant_ids"])
        self.assertIn("B1", entry["plot_ids"])

        # Update links
        self.client.patch(
            f"/api/harvest/{entry_id}",
            json={"plant_ids": ["PLT-TEST"], "plot_ids": ["B1", "B2"]},
        )
        resp = self.client.get(f"/api/harvest/{entry_id}")
        entry = resp.json()
        self.assertEqual(sorted(entry["plot_ids"]), ["B1", "B2"])

    def test_harvest_summary(self) -> None:
        """Verify summary aggregation."""
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-10",
                "quantity": 2.0,
                "unit": "kg",
                "quality": "excellent",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-20",
                "quantity": 1.5,
                "unit": "kg",
                "quality": "good",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-07-05",
                "quantity": 3.0,
                "unit": "kg",
                "quality": "good",
            },
        )

        resp = self.client.get("/api/harvest/summary?year=2026")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertEqual(summary["year"], 2026)
        self.assertEqual(summary["total_entries"], 3)

        # by_quality
        self.assertEqual(summary["by_quality"]["excellent"], 1)
        self.assertEqual(summary["by_quality"]["good"], 2)

        # by_month: June has 2, July has 1
        months = {m["month"]: m for m in summary["by_month"]}
        self.assertEqual(months[6]["entries"], 2)
        self.assertAlmostEqual(months[6]["total_qty"], 3.5)
        self.assertEqual(months[7]["entries"], 1)
        self.assertAlmostEqual(months[7]["total_qty"], 3.0)

        # by_plant: PLT-TEST should have 3.5 kg total
        plant_entry = next(
            (p for p in summary["by_plant"] if p["plt_id"] == "PLT-TEST"),
            None,
        )
        self.assertIsNotNone(plant_entry)
        assert plant_entry is not None
        self.assertAlmostEqual(plant_entry["total_qty"], 3.5)

    def test_harvest_summary_respects_quality_and_date_filters(self) -> None:
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-10",
                "quantity": 2.0,
                "unit": "kg",
                "quality": "excellent",
            },
        )
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-20",
                "quantity": 1.5,
                "unit": "kg",
                "quality": "good",
            },
        )
        self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-07-05",
                "quantity": 3.0,
                "unit": "kg",
                "quality": "excellent",
            },
        )

        resp = self.client.get(
            "/api/harvest/summary?year=2026&quality=excellent&date_from=2026-06-01&date_to=2026-06-30",
        )
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertEqual(summary["total_entries"], 1)
        self.assertEqual(
            summary["by_quality"],
            {"excellent": 1, "good": 0, "fair": 0, "poor": 0},
        )
        self.assertEqual(summary["by_month"], [{"month": 6, "entries": 1, "total_qty": 2.0}])

    def test_harvest_auth_viewer_denied(self) -> None:
        """Viewer role gets 403 on write operations."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("viewer_hvst", "viewerpass", "viewer")
            self._create_test_user("editor_hvst", "editorpass", "editor")

            editor_client, editor_h = self._authenticated_client(
                "editor_hvst",
                "editorpass",
            )
            r = editor_client.post(
                "/api/harvest",
                headers=editor_h,
                json={
                    "occurred_on": "2026-07-15",
                    "quantity": 1.0,
                    "unit": "kg",
                },
            )
            self.assertEqual(r.status_code, 201)
            entry_id = r.json()["id"]

            viewer_client, viewer_h = self._authenticated_client(
                "viewer_hvst",
                "viewerpass",
            )
            r = viewer_client.get("/api/harvest", headers=viewer_h)
            self.assertEqual(r.status_code, 200)

            r = viewer_client.post(
                "/api/harvest",
                headers=viewer_h,
                json={
                    "occurred_on": "2026-07-15",
                    "quantity": 1.0,
                    "unit": "kg",
                },
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.patch(
                f"/api/harvest/{entry_id}",
                headers=viewer_h,
                json={"quantity": 5.0},
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.delete(f"/api/harvest/{entry_id}", headers=viewer_h)
            self.assertEqual(r.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
