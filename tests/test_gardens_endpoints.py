import io
import os
from pathlib import Path
from unittest.mock import patch

import laspy
import numpy as np
from pyproj import CRS

import gardenops.db as db
from tests.base import BaseApiTest


def _valid_laz_bytes(*, elevation_offset: float = 0.0) -> bytes:
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.add_crs(CRS.from_epsg(32632))
    terrain = laspy.LasData(header)
    terrain.x = np.array([500_000.0, 500_001.0, 500_000.0, 500_001.0])
    terrain.y = np.array([6_640_000.0, 6_640_000.0, 6_640_001.0, 6_640_001.0])
    terrain.z = np.array([10.0, 20.0, 30.0, 40.0]) + elevation_offset
    terrain.classification = np.full(4, 2)
    output = io.BytesIO()
    terrain.write(output, do_compress=True)
    return output.getvalue()


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
        conn = db.get_db()
        try:
            audit_rows = conn.execute(
                """
                SELECT status_code
                FROM audit_events
                WHERE method = 'POST' AND path = '/api/gardens'
                """
            ).fetchall()
        finally:
            db.return_db(conn)
        self.assertEqual([int(row["status_code"]) for row in audit_rows], [201])
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

    def test_geocode_garden_location_returns_bounded_results(self) -> None:
        try:
            client, headers, garden_id = self._create_garden_with_admin()
            with patch(
                "gardenops.routers.gardens._geocode_query",
                return_value=[
                    {
                        "display_name": "Oslo, Norway",
                        "latitude": 59.9139,
                        "longitude": 10.7522,
                    },
                ],
            ) as geocode_mock:
                resp = client.get(
                    f"/api/gardens/{garden_id}/geocode",
                    headers=headers,
                    params={"q": "  Oslo  "},
                )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["results"][0]["display_name"], "Oslo, Norway")
            geocode_mock.assert_called_once_with("Oslo")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_garden_lidar_upload_status_and_delete(self) -> None:
        try:
            client, headers, garden_id = self._create_garden_with_admin()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO shademap_cache (
                        cache_kind, cache_key, fetched_at_ms, content_type,
                        payload_text, payload_blob, garden_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "terrain-tile",
                        f"lidar-upload-{garden_id}",
                        db.current_timestamp_ms(),
                        "application/json",
                        "{}",
                        None,
                        garden_id,
                    ),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            payload = _valid_laz_bytes()
            upload = client.post(
                f"/api/gardens/{garden_id}/lidar",
                headers={
                    **headers,
                    "content-type": "application/octet-stream",
                    "x-upload-filename": "terrain.laz",
                },
                content=payload,
            )
            self.assertEqual(upload.status_code, 201, upload.text)
            upload_body = upload.json()
            self.assertTrue(upload_body["available"])
            self.assertTrue(upload_body["uploaded"])
            self.assertEqual(upload_body["filename"], "terrain.laz")

            stored = self.test_media_dir / "lidar" / f"garden-{garden_id}" / "terrain.laz"
            self.assertTrue(stored.exists())
            self.assertEqual(stored.read_bytes(), payload)

            status = client.get(f"/api/gardens/{garden_id}/lidar", headers=headers)
            self.assertEqual(status.status_code, 200, status.text)
            self.assertTrue(status.json()["uploaded"])

            conn = db.get_db()
            try:
                cached = conn.execute(
                    "SELECT 1 FROM shademap_cache "
                    "WHERE garden_id = %s AND cache_kind = 'terrain-tile'",
                    (garden_id,),
                ).fetchone()
            finally:
                db.return_db(conn)
            self.assertIsNone(cached)

            with patch.object(Path, "unlink", side_effect=PermissionError("storage read-only")):
                pending = client.delete(f"/api/gardens/{garden_id}/lidar", headers=headers)
            self.assertEqual(pending.status_code, 200, pending.text)
            self.assertTrue(pending.json()["uploaded"])
            self.assertEqual(pending.json()["file_cleanup"], "pending")
            self.assertTrue(stored.exists())

            conn = db.get_db()
            try:
                cleanup_row = conn.execute(
                    "SELECT attempts, last_error FROM media_cleanup_jobs WHERE storage_key = %s",
                    (f"lidar/garden-{garden_id}/terrain.laz",),
                ).fetchone()
                assert cleanup_row is not None
                self.assertEqual(int(cleanup_row["attempts"]), 1)
                self.assertIn("PermissionError", str(cleanup_row["last_error"]))
            finally:
                db.return_db(conn)

            deleted = client.delete(f"/api/gardens/{garden_id}/lidar", headers=headers)
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertFalse(deleted.json()["uploaded"])
            self.assertEqual(deleted.json()["file_cleanup"], "complete")
            self.assertFalse(stored.exists())
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_garden_lidar_db_failure_restores_previous_active_file(self) -> None:
        try:
            client, headers, garden_id = self._create_garden_with_admin()
            original = _valid_laz_bytes()
            first = client.post(
                f"/api/gardens/{garden_id}/lidar",
                headers={**headers, "x-upload-filename": "terrain.laz"},
                content=original,
            )
            self.assertEqual(first.status_code, 201, first.text)
            stored = self.test_media_dir / "lidar" / f"garden-{garden_id}" / "terrain.laz"

            with patch(
                "gardenops.routers.gardens._invalidate_garden_terrain_state",
                side_effect=RuntimeError("database invalidation failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "database invalidation failed"):
                    client.post(
                        f"/api/gardens/{garden_id}/lidar",
                        headers={**headers, "x-upload-filename": "replacement.laz"},
                        content=_valid_laz_bytes(elevation_offset=100.0),
                    )

            self.assertEqual(stored.read_bytes(), original)
            self.assertEqual(list(stored.parent.glob(".terrain-*")), [])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_garden_lidar_viewer_editor_and_garden_boundaries(self) -> None:
        try:
            admin_client, admin_headers, garden_id = self._create_garden_with_admin()
            editor = self._create_test_user("lidar_editor", "editorpass", "editor")
            viewer = self._create_test_user("lidar_viewer", "viewerpass", "viewer")
            foreign = self._create_test_user("lidar_foreign", "foreignpass", "editor")
            conn = db.get_db()
            try:
                foreign_garden_id = int(
                    conn.execute(
                        "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                        ("lidar-foreign", "LiDAR Foreign"),
                    ).fetchone()["id"]
                )
                conn.execute(
                    "INSERT INTO garden_memberships (garden_id, user_id, role) "
                    "VALUES (%s, %s, 'editor'), (%s, %s, 'viewer'), (%s, %s, 'editor')",
                    (
                        garden_id,
                        int(editor["id"]),
                        garden_id,
                        int(viewer["id"]),
                        foreign_garden_id,
                        int(foreign["id"]),
                    ),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            editor_client, editor_headers = self._authenticated_client(
                "lidar_editor", "editorpass", garden_id=garden_id
            )
            viewer_client, viewer_headers = self._authenticated_client(
                "lidar_viewer", "viewerpass", garden_id=garden_id
            )
            foreign_client, foreign_headers = self._authenticated_client(
                "lidar_foreign", "foreignpass", garden_id=foreign_garden_id
            )

            upload = editor_client.post(
                f"/api/gardens/{garden_id}/lidar",
                headers={**editor_headers, "x-upload-filename": "terrain.laz"},
                content=_valid_laz_bytes(),
            )
            self.assertEqual(upload.status_code, 201, upload.text)
            self.assertEqual(
                viewer_client.get(
                    f"/api/gardens/{garden_id}/lidar", headers=viewer_headers
                ).status_code,
                200,
            )
            self.assertEqual(
                viewer_client.delete(
                    f"/api/gardens/{garden_id}/lidar", headers=viewer_headers
                ).status_code,
                403,
            )
            self.assertEqual(
                foreign_client.get(
                    f"/api/gardens/{garden_id}/lidar", headers=foreign_headers
                ).status_code,
                404,
            )
            self.assertEqual(
                foreign_client.post(
                    f"/api/gardens/{garden_id}/lidar",
                    headers={**foreign_headers, "x-upload-filename": "terrain.laz"},
                    content=_valid_laz_bytes(),
                ).status_code,
                404,
            )
            removed = admin_client.delete(f"/api/gardens/{garden_id}/lidar", headers=admin_headers)
            self.assertEqual(removed.status_code, 200, removed.text)
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
