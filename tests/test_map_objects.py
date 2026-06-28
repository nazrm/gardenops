import json
import os

import gardenops.db as db
from tests.base import BaseApiTest


class TestMapObjects(BaseApiTest):
    def _default_garden(self) -> int:
        return self._get_default_garden_id()

    def _create_member_client(
        self,
        *,
        username: str,
        role: str,
        garden_id: int,
    ) -> tuple[object, dict[str, str]]:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        user = self._create_test_user(username, f"{username}-pass", role)
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT(garden_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (garden_id, int(user["id"]), role),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        return self._authenticated_client(username, f"{username}-pass", garden_id=garden_id)

    @staticmethod
    def _patio_payload() -> dict[str, object]:
        return {
            "object_type": "patio",
            "name": "Kitchen patio",
            "shape_type": "rectangle",
            "geometry": {"x": 1, "y": 1, "width": 4, "height": 3},
            "style": {"color": "#7d9f7a"},
            "z_index": 2,
            "has_internal_layout": True,
            "internal_layout": {"rows": 6, "cols": 8},
        }

    @staticmethod
    def _pot_payload() -> dict[str, object]:
        return {
            "unit_type": "pot",
            "name": "Rosemary pot",
            "shape_type": "ellipse",
            "geometry": {"x": 2, "y": 2, "width": 2, "height": 2},
            "style": {"color": "#c58f5c"},
            "sort_order": 1,
        }

    @staticmethod
    def _seed_map_object_count(garden_id: int, count: int) -> None:
        conn = db.get_db()
        now_ms = db.current_timestamp_ms()
        try:
            db.executemany(
                conn,
                """
                INSERT INTO garden_map_objects (
                    public_id, garden_id, object_type, name, shape_type,
                    geometry_json, style_json, z_index, has_internal_layout,
                    internal_layout_json, created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, 'patio', %s, 'rectangle', %s, %s, 0, 1, %s, %s, %s)
                """,
                [
                    (
                        f"seed_mapobj_{idx}",
                        garden_id,
                        f"Seed Patio {idx}",
                        json.dumps({"x": 1, "y": 1, "width": 1, "height": 1}),
                        json.dumps({"color": "#7d9f7a"}),
                        json.dumps({"rows": 6, "cols": 8}),
                        now_ms,
                        now_ms,
                    )
                    for idx in range(count)
                ],
            )
            conn.commit()
        finally:
            db.return_db(conn)

    @staticmethod
    def _seed_unit_count(garden_id: int, map_object_public_id: str, count: int) -> None:
        conn = db.get_db()
        now_ms = db.current_timestamp_ms()
        try:
            row = conn.execute(
                """
                SELECT id FROM garden_map_objects
                WHERE garden_id = %s AND public_id = %s
                LIMIT 1
                """,
                (garden_id, map_object_public_id),
            ).fetchone()
            assert row is not None
            db.executemany(
                conn,
                """
                INSERT INTO garden_map_object_units (
                    public_id, garden_id, map_object_id, unit_type, name,
                    shape_type, geometry_json, style_json, sort_order,
                    created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, %s, 'pot', %s, 'ellipse', %s, %s, %s, %s, %s)
                """,
                [
                    (
                        f"seed_mapunit_{idx}",
                        garden_id,
                        int(row["id"]),
                        f"Seed Pot {idx}",
                        json.dumps({"x": 1, "y": 1, "width": 1, "height": 1}),
                        json.dumps({"color": "#c58f5c"}),
                        idx,
                        now_ms,
                        now_ms,
                    )
                    for idx in range(count)
                ],
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_editor_can_create_list_and_delete_patio_with_nested_unit(self) -> None:
        garden_id = self._default_garden()

        created = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(created.status_code, 201, created.text)
        patio = created.json()
        self.assertEqual(patio["object_type"], "patio")
        self.assertEqual(patio["name"], "Kitchen patio")
        self.assertEqual(patio["geometry"], {"x": 1, "y": 1, "width": 4, "height": 3})
        self.assertEqual(patio["style"], {"color": "#7d9f7a"})
        self.assertEqual(patio["internal_layout"], {"rows": 6, "cols": 8})
        self.assertEqual(patio["units"], [])

        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio['public_id']}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(unit.status_code, 201, unit.text)
        self.assertEqual(unit.json()["shape_type"], "ellipse")

        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.status_code, 200, listed.text)
        objects = listed.json()["objects"]
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["public_id"], patio["public_id"])
        self.assertEqual(len(objects[0]["units"]), 1)
        self.assertEqual(objects[0]["units"][0]["name"], "Rosemary pot")

        deleted = self.client.delete(f"/api/gardens/{garden_id}/map-objects/{patio['public_id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["deleted_units"], 1)

        listed_after_delete = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed_after_delete.status_code, 200, listed_after_delete.text)
        self.assertEqual(listed_after_delete.json()["objects"], [])

    def test_viewer_can_list_but_cannot_create_map_objects(self) -> None:
        garden_id = self._default_garden()
        try:
            client, headers = self._create_member_client(
                username="map_viewer",
                role="viewer",
                garden_id=garden_id,
            )

            listed = client.get(f"/api/gardens/{garden_id}/map-objects", headers=headers)
            self.assertEqual(listed.status_code, 200, listed.text)

            created = client.post(
                f"/api/gardens/{garden_id}/map-objects",
                headers=headers,
                json=self._patio_payload(),
            )
            self.assertEqual(created.status_code, 403, created.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_rejects_top_level_geometry_outside_garden_grid(self) -> None:
        garden_id = self._default_garden()
        payload = self._patio_payload()
        payload["geometry"] = {"x": 21, "y": 29, "width": 4, "height": 3}

        created = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=payload,
        )

        self.assertEqual(created.status_code, 400, created.text)
        self.assertIn("does not fit", created.json()["detail"])

    def test_rejects_nested_unit_outside_internal_layout(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        payload = self._pot_payload()
        payload["geometry"] = {"x": 7, "y": 6, "width": 3, "height": 2}

        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio.json()['public_id']}/units",
            json=payload,
        )

        self.assertEqual(unit.status_code, 400, unit.text)
        self.assertIn("does not fit", unit.json()["detail"])

    def test_cross_garden_object_cannot_receive_units(self) -> None:
        first_garden_id = self._default_garden()
        second = self.client.post("/api/gardens", json={"name": "Second Garden"})
        self.assertEqual(second.status_code, 201, second.text)
        second_garden_id = int(second.json()["id"])
        patio = self.client.post(
            f"/api/gardens/{first_garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)

        unit = self.client.post(
            f"/api/gardens/{second_garden_id}/map-objects/{patio.json()['public_id']}/units",
            json=self._pot_payload(),
        )

        self.assertEqual(unit.status_code, 404, unit.text)

    def test_rejects_map_object_and_unit_count_limits(self) -> None:
        garden_id = self._default_garden()
        self._seed_map_object_count(garden_id, 200)

        over_limit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(over_limit.status_code, 400, over_limit.text)
        self.assertIn("limit", over_limit.json()["detail"].lower())

        other_garden = self.client.post("/api/gardens", json={"name": "Unit Limit Garden"})
        self.assertEqual(other_garden.status_code, 201, other_garden.text)
        unit_garden_id = int(other_garden.json()["id"])
        patio = self.client.post(
            f"/api/gardens/{unit_garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]

        self._seed_unit_count(unit_garden_id, patio_id, 100)

        over_unit_limit = self.client.post(
            f"/api/gardens/{unit_garden_id}/map-objects/{patio_id}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(over_unit_limit.status_code, 400, over_unit_limit.text)
        self.assertIn("limit", over_unit_limit.json()["detail"].lower())
