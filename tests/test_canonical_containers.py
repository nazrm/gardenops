"""Focused API coverage for canonical plot-backed containers."""

from __future__ import annotations

import os

import gardenops.db as db
from tests.base import BaseApiTest


class TestCanonicalContainers(BaseApiTest):
    @staticmethod
    def _area_payload(name: str = "North patio") -> dict[str, object]:
        return {
            "object_type": "patio",
            "name": name,
            "shape_type": "rectangle",
            "geometry": {"x": 1, "y": 1, "width": 4, "height": 3},
            "has_internal_layout": False,
        }

    def _create_area(self, garden_id: int) -> dict[str, object]:
        response = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._area_payload(),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _create_container(
        self,
        garden_id: int,
        *,
        parent_object_public_id: str | None = None,
        name: str = "Blue planter",
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": name,
            "container_type": "planter",
        }
        if parent_object_public_id is not None:
            payload["parent_object_public_id"] = parent_object_public_id
        response = self.client.post(
            f"/api/gardens/{garden_id}/containers",
            json=payload,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _create_member_client(
        self,
        *,
        username: str,
        role: str,
        garden_id: int,
    ) -> tuple[object, dict[str, str]]:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        user = self._create_test_user(username, f"{username}-password", role)
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (garden_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (garden_id, int(user["id"]), role),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        return self._authenticated_client(username, f"{username}-password", garden_id=garden_id)

    def test_member_reads_canonical_containers_and_area_counts(self) -> None:
        garden_id = self._get_default_garden_id()
        area = self._create_area(garden_id)
        container = self._create_container(
            garden_id,
            parent_object_public_id=str(area["public_id"]),
        )

        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
                (container["plot_id"], "PLT-TEST", 3),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.status_code, 200, listed.text)
        object_payload = listed.json()["objects"][0]
        self.assertEqual(object_payload["container_count"], 1)
        self.assertEqual(object_payload["plant_count"], 1)
        self.assertEqual(object_payload["plant_quantity"], 3)
        self.assertEqual(object_payload["containers"][0]["plot_id"], container["plot_id"])
        self.assertEqual(object_payload["containers"][0]["plant_quantity"], 3)

        viewer, headers = self._create_member_client(
            username="container_viewer",
            role="viewer",
            garden_id=garden_id,
        )
        try:
            readable = viewer.get(
                f"/api/gardens/{garden_id}/containers/{container['plot_id']}",
                headers=headers,
            )
            self.assertEqual(readable.status_code, 200, readable.text)
            denied = viewer.patch(
                f"/api/gardens/{garden_id}/containers/{container['plot_id']}",
                headers=headers,
                json={"name": "Viewer edit"},
            )
            self.assertEqual(denied.status_code, 403, denied.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_editor_can_create_and_reparent_container_without_changing_plot_id(self) -> None:
        garden_id = self._get_default_garden_id()
        first_area = self._create_area(garden_id)
        second_response = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._area_payload("South patio"),
        )
        self.assertEqual(second_response.status_code, 201, second_response.text)
        second_area = second_response.json()
        editor, headers = self._create_member_client(
            username="container_editor",
            role="editor",
            garden_id=garden_id,
        )
        try:
            created = editor.post(
                f"/api/gardens/{garden_id}/containers",
                headers=headers,
                json={
                    "name": "Blue planter",
                    "container_type": "planter",
                    "parent_object_public_id": first_area["public_id"],
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            container = created.json()
            patched = editor.patch(
                f"/api/gardens/{garden_id}/containers/{container['plot_id']}",
                headers=headers,
                json={
                    "name": "Moved planter",
                    "parent_object_public_id": second_area["public_id"],
                },
            )
            self.assertEqual(patched.status_code, 200, patched.text)
            self.assertEqual(patched.json()["plot_id"], container["plot_id"])
            self.assertEqual(patched.json()["name"], "Moved planter")
            self.assertEqual(
                patched.json()["parent_object_public_id"],
                second_area["public_id"],
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_container_parent_cannot_cross_gardens(self) -> None:
        garden_id = self._get_default_garden_id()
        _, other_garden_id, _, _ = self._setup_admin_two_gardens()
        other_area = self._create_area(other_garden_id)

        response = self.client.post(
            f"/api/gardens/{garden_id}/containers",
            json={
                "name": "Foreign planter",
                "container_type": "planter",
                "parent_object_public_id": other_area["public_id"],
            },
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_area_delete_unparents_container_and_does_not_delete_it(self) -> None:
        garden_id = self._get_default_garden_id()
        area = self._create_area(garden_id)
        container = self._create_container(
            garden_id,
            parent_object_public_id=str(area["public_id"]),
        )

        deleted = self.client.delete(
            f"/api/gardens/{garden_id}/map-objects/{area['public_id']}"
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["unparented_containers"], 1)

        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT parent_map_object_id FROM plots WHERE plot_id = %s",
                (container["plot_id"],),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row["parent_map_object_id"])
        finally:
            db.return_db(conn)

    def test_archive_requires_admin_and_blocks_occupied_container(self) -> None:
        garden_id = self._get_default_garden_id()
        area = self._create_area(garden_id)
        container = self._create_container(
            garden_id,
            parent_object_public_id=str(area["public_id"]),
        )
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 2)",
                (container["plot_id"], "PLT-TEST"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        occupied = self.client.delete(
            f"/api/gardens/{garden_id}/containers/{container['plot_id']}"
        )
        self.assertEqual(occupied.status_code, 409, occupied.text)
        self.assertEqual(occupied.json()["detail"]["plant_quantity"], 2)

        conn = db.get_db()
        try:
            conn.execute(
                "DELETE FROM plot_plants WHERE plot_id = %s",
                (container["plot_id"],),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        archived = self.client.delete(
            f"/api/gardens/{garden_id}/containers/{container['plot_id']}"
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["status"], "archived")
        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.json()["containers"], [])

    def test_legacy_unit_mutations_are_gone_without_touching_legacy_table(self) -> None:
        garden_id = self._get_default_garden_id()
        path = f"/api/gardens/{garden_id}/map-objects/old-area/units/old-unit"
        responses = [
            self.client.post(
                f"/api/gardens/{garden_id}/map-objects/old-area/units",
                json={"ignored": True},
            ),
            self.client.patch(path, json={"ignored": True}),
            self.client.delete(path),
        ]
        for response in responses:
            with self.subTest(method=response.request.method):
                self.assertEqual(response.status_code, 410, response.text)
