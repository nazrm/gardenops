import os

from tests.base import BaseApiTest


class TestGardensList(BaseApiTest):
    """Tests for GET /api/gardens."""

    def test_list_gardens_unauthenticated(self) -> None:
        """Without auth, local admin fallback excludes the default garden."""
        resp = self.client.get("/api/gardens")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)

    def test_list_gardens_authenticated(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("glist_admin", "adminpasswd", "admin")
            client, headers = self._authenticated_client(
                "glist_admin",
                "adminpasswd",
            )
            resp = client.get("/api/gardens", headers=headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIsInstance(data, list)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestGardenSettings(BaseApiTest):
    """Tests for GET/PATCH /api/gardens/{id}/settings."""

    def _create_garden_with_admin(self) -> tuple:
        """Create an admin, garden, and membership. Returns (client, headers, garden_id)."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        self._create_test_user("gs_admin", "adminpasswd", "admin")
        client, headers = self._authenticated_client(
            "gs_admin",
            "adminpasswd",
        )

        resp = client.post(
            "/api/gardens",
            headers=headers,
            json={"name": "Settings Test Garden"},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        garden_id = resp.json()["id"]
        return client, headers, garden_id

    def test_get_garden_settings(self) -> None:
        try:
            client, headers, garden_id = self._create_garden_with_admin()
            resp = client.get(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["garden_id"], garden_id)
            self.assertIn("name", data)
            self.assertIn("grid_rows", data)
            self.assertIn("grid_cols", data)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_update_garden_settings_name(self) -> None:
        try:
            client, headers, garden_id = self._create_garden_with_admin()
            resp = client.patch(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
                json={"name": "Updated Name"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["name"], "Updated Name")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_update_garden_settings_no_fields_400(self) -> None:
        try:
            client, headers, garden_id = self._create_garden_with_admin()
            resp = client.patch(
                f"/api/gardens/{garden_id}/settings",
                headers=headers,
                json={},
            )
            self.assertEqual(resp.status_code, 400)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_get_garden_settings_nonexistent(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("gs_miss", "adminpasswd", "admin")
            client, headers = self._authenticated_client(
                "gs_miss",
                "adminpasswd",
            )
            resp = client.get(
                "/api/gardens/99999/settings",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 404)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


class TestGardenMemberships(BaseApiTest):
    """Tests for memberships CRUD on /api/gardens/{id}/memberships."""

    def _setup_garden_and_admin(self) -> tuple:
        """Create admin, garden, and return (client, headers, garden_id, admin_user_id)."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        admin_user = self._create_test_user("gm_admin", "adminpasswd", "admin")
        client, headers = self._authenticated_client(
            "gm_admin",
            "adminpasswd",
        )
        resp = client.post(
            "/api/gardens",
            headers=headers,
            json={"name": "Membership Test Garden"},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        garden_id = resp.json()["id"]
        return client, headers, garden_id, admin_user["id"]

    def test_list_memberships(self) -> None:
        try:
            client, headers, garden_id, _ = self._setup_garden_and_admin()
            resp = client.get(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("garden_id", data)
            self.assertIn("memberships", data)
            self.assertEqual(data["garden_id"], garden_id)
            self.assertGreaterEqual(len(data["memberships"]), 1)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_add_and_remove_membership(self) -> None:
        try:
            client, headers, garden_id, _ = self._setup_garden_and_admin()
            self._create_test_user("gm_member", "memberpass", "editor")

            resp = client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={
                    "username": "gm_member",
                    "role": "editor",
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["username"], "gm_member")
            self.assertEqual(data["role"], "editor")
            member_user_id = data["user_id"]

            resp = client.get(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
            )
            usernames = [m["username"] for m in resp.json()["memberships"]]
            self.assertIn("gm_member", usernames)

            resp = client.delete(
                f"/api/gardens/{garden_id}/memberships/{member_user_id}",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_add_membership_user_not_found(self) -> None:
        try:
            client, headers, garden_id, _ = self._setup_garden_and_admin()
            resp = client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={
                    "username": "nonexistent_user",
                    "role": "viewer",
                },
            )
            self.assertEqual(resp.status_code, 404)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_viewer_cannot_list_memberships(self) -> None:
        try:
            client, headers, garden_id, _ = self._setup_garden_and_admin()
            self._create_test_user("gm_viewer", "viewerpass", "viewer")

            client.post(
                f"/api/gardens/{garden_id}/memberships",
                headers=headers,
                json={"username": "gm_viewer", "role": "viewer"},
            )

            viewer_client, viewer_headers = self._authenticated_client(
                "gm_viewer",
                "viewerpass",
            )
            resp = viewer_client.get(
                f"/api/gardens/{garden_id}/memberships",
                headers=viewer_headers,
            )
            self.assertEqual(resp.status_code, 404)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
