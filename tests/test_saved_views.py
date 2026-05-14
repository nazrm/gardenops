import os

from tests.base import BaseApiTest


class TestSavedViewsApi(BaseApiTest):
    """Tests for saved views CRUD endpoints."""

    def test_saved_view_crud(self) -> None:
        """Create, list, update, delete a saved view."""
        # Create
        resp = self.client.post(
            "/api/saved-views",
            json={
                "view_type": "plants",
                "label": "My plant filter",
                "filter_json": {"q": "rose"},
                "sort_order": 1,
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        view_id = data["id"]

        # List
        resp = self.client.get("/api/saved-views")
        self.assertEqual(resp.status_code, 200)
        views = resp.json()["views"]
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["label"], "My plant filter")
        self.assertEqual(views[0]["view_type"], "plants")
        self.assertEqual(views[0]["filter_json"], {"q": "rose"})
        self.assertEqual(views[0]["sort_order"], 1)
        self.assertFalse(views[0]["is_preset"])

        # Update
        resp = self.client.patch(
            f"/api/saved-views/{view_id}",
            json={"label": "Updated filter", "filter_json": {"q": "tulip"}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        # Verify update
        resp = self.client.get("/api/saved-views")
        views = resp.json()["views"]
        self.assertEqual(views[0]["label"], "Updated filter")
        self.assertEqual(views[0]["filter_json"], {"q": "tulip"})

        # Delete
        resp = self.client.delete(f"/api/saved-views/{view_id}")
        self.assertEqual(resp.status_code, 200)

        # Verify deleted
        resp = self.client.get("/api/saved-views")
        self.assertEqual(len(resp.json()["views"]), 0)

    def test_saved_view_presets(self) -> None:
        """Fetch presets returns expected items."""
        resp = self.client.get("/api/saved-views/presets")
        self.assertEqual(resp.status_code, 200)
        presets = resp.json()["presets"]
        self.assertGreater(len(presets), 0)
        # Verify structure
        for p in presets:
            self.assertIn("view_type", p)
            self.assertIn("label", p)
            self.assertIn("filter_json", p)
            self.assertIn("preset_key", p)
        # Verify known presets exist
        keys = {p["preset_key"] for p in presets}
        self.assertIn("missing_photos", keys)
        self.assertIn("tasks_week", keys)
        self.assertIn("issues_open", keys)
        self.assertIn("journal_recent", keys)
        self.assertIn("inventory_empty", keys)
        self.assertIn("calendar_essential", keys)
        self.assertIn("calendar_high_value", keys)
        calendar_preset = next(
            preset for preset in presets if preset["preset_key"] == "calendar_essential"
        )
        self.assertIn("weather_alert", calendar_preset["filter_json"]["visible_sources"])
        self.assertIn("observe_bloom", calendar_preset["filter_json"]["visible_sources"])
        self.assertFalse(calendar_preset["filter_json"]["include_recent_history"])

    def test_saved_view_filter_by_type(self) -> None:
        """List with view_type filter returns only matching views."""
        # Create views of different types
        self.client.post(
            "/api/saved-views",
            json={"view_type": "plants", "label": "Plant view", "filter_json": {}},
        )
        self.client.post(
            "/api/saved-views",
            json={"view_type": "tasks", "label": "Task view", "filter_json": {}},
        )
        self.client.post(
            "/api/saved-views",
            json={"view_type": "journal", "label": "Journal view", "filter_json": {}},
        )

        # Filter by plants
        resp = self.client.get("/api/saved-views?view_type=plants")
        self.assertEqual(resp.status_code, 200)
        views = resp.json()["views"]
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["view_type"], "plants")

        # Filter by tasks
        resp = self.client.get("/api/saved-views?view_type=tasks")
        views = resp.json()["views"]
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["view_type"], "tasks")

        # All views
        resp = self.client.get("/api/saved-views")
        views = resp.json()["views"]
        self.assertEqual(len(views), 3)

    def test_saved_view_ownership(self) -> None:
        """User can only update/delete own views."""
        try:
            os.environ["AUTH_REQUIRED"] = "true"

            self._create_test_user("sv_editor", "editorpass", "editor")
            self._create_test_user("sv_other", "otherpass", "editor")

            editor_client, editor_h = self._authenticated_client(
                "sv_editor",
                "editorpass",
            )
            r = editor_client.post(
                "/api/saved-views",
                headers=editor_h,
                json={
                    "view_type": "plants",
                    "label": "Editor view",
                    "filter_json": {"q": "test"},
                },
            )
            self.assertEqual(r.status_code, 201)
            view_id = r.json()["id"]

            other_client, other_h = self._authenticated_client(
                "sv_other",
                "otherpass",
            )
            r = other_client.get("/api/saved-views", headers=other_h)
            self.assertEqual(r.status_code, 200)

            r = other_client.patch(
                f"/api/saved-views/{view_id}",
                headers=other_h,
                json={"label": "hacked"},
            )
            self.assertEqual(r.status_code, 403)

            r = other_client.delete(
                f"/api/saved-views/{view_id}",
                headers=other_h,
            )
            self.assertEqual(r.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
