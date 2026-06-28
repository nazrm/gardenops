import json
import os
from unittest.mock import patch

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

    def _destructive_admin_headers(self, action_reason: str) -> dict[str, str]:
        _, csrf = self._login_session("test_admin", "testadminpass")
        return self._session_headers(
            csrf,
            extra={"x-action-reason": action_reason},
        )

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

    @staticmethod
    def _unit_count(garden_id: int, map_object_public_id: str | None = None) -> int:
        conn = db.get_db()
        try:
            if map_object_public_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM garden_map_object_units WHERE garden_id = %s",
                    (garden_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM garden_map_object_units u
                    JOIN garden_map_objects o ON o.id = u.map_object_id
                    WHERE u.garden_id = %s AND o.public_id = %s
                    """,
                    (garden_id, map_object_public_id),
                ).fetchone()
            return int(row["c"] if row else 0)
        finally:
            db.return_db(conn)

    def _import_layout(self, payload: dict[str, object], reason: str = "map-object-import"):
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            return self.client.post(
                "/api/plots/import",
                headers=self._destructive_admin_headers(reason),
                json=payload,
            )

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
        self.assertEqual(self._unit_count(garden_id, patio["public_id"]), 0)

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

    def test_viewer_cannot_mutate_existing_map_objects_or_units(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(unit.status_code, 201, unit.text)
        unit_id = unit.json()["public_id"]

        try:
            client, headers = self._create_member_client(
                username="map_mutation_viewer",
                role="viewer",
                garden_id=garden_id,
            )
            responses = [
                client.patch(
                    f"/api/gardens/{garden_id}/map-objects/{patio_id}",
                    headers=headers,
                    json={"name": "Viewer edit"},
                ),
                client.delete(
                    f"/api/gardens/{garden_id}/map-objects/{patio_id}",
                    headers=headers,
                ),
                client.post(
                    f"/api/gardens/{garden_id}/map-objects/{patio_id}/units",
                    headers=headers,
                    json=self._pot_payload(),
                ),
                client.patch(
                    f"/api/gardens/{garden_id}/map-objects/{patio_id}/units/{unit_id}",
                    headers=headers,
                    json={"name": "Viewer unit edit"},
                ),
                client.delete(
                    f"/api/gardens/{garden_id}/map-objects/{patio_id}/units/{unit_id}",
                    headers=headers,
                ),
            ]
            for response in responses:
                with self.subTest(path=response.request.url.path, method=response.request.method):
                    self.assertEqual(response.status_code, 403, response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_non_member_cannot_access_other_garden_map_objects(self) -> None:
        first = self.client.post("/api/gardens", json={"name": "Map Object Garden A"})
        second = self.client.post("/api/gardens", json={"name": "Map Object Garden B"})
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        first_garden_id = int(first.json()["id"])
        second_garden_id = int(second.json()["id"])

        patio = self.client.post(
            f"/api/gardens/{first_garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        unit = self.client.post(
            f"/api/gardens/{first_garden_id}/map-objects/{patio_id}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(unit.status_code, 201, unit.text)
        unit_id = unit.json()["public_id"]

        try:
            client, headers = self._create_member_client(
                username="map_other_garden_editor",
                role="editor",
                garden_id=second_garden_id,
            )
            responses = [
                client.get(f"/api/gardens/{first_garden_id}/map-objects", headers=headers),
                client.post(
                    f"/api/gardens/{first_garden_id}/map-objects",
                    headers=headers,
                    json=self._patio_payload(),
                ),
                client.patch(
                    f"/api/gardens/{first_garden_id}/map-objects/{patio_id}",
                    headers=headers,
                    json={"name": "No access"},
                ),
                client.delete(
                    f"/api/gardens/{first_garden_id}/map-objects/{patio_id}",
                    headers=headers,
                ),
                client.post(
                    f"/api/gardens/{first_garden_id}/map-objects/{patio_id}/units",
                    headers=headers,
                    json=self._pot_payload(),
                ),
                client.patch(
                    f"/api/gardens/{first_garden_id}/map-objects/{patio_id}/units/{unit_id}",
                    headers=headers,
                    json={"name": "No access"},
                ),
                client.delete(
                    f"/api/gardens/{first_garden_id}/map-objects/{patio_id}/units/{unit_id}",
                    headers=headers,
                ),
            ]
            for response in responses:
                with self.subTest(path=response.request.url.path, method=response.request.method):
                    self.assertEqual(response.status_code, 404, response.text)
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

    def test_rejects_units_on_layout_less_map_object(self) -> None:
        garden_id = self._default_garden()
        payload = self._patio_payload()
        payload["has_internal_layout"] = False
        payload["internal_layout"] = None
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=payload,
        )
        self.assertEqual(patio.status_code, 201, patio.text)

        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio.json()['public_id']}/units",
            json=self._pot_payload(),
        )

        self.assertEqual(unit.status_code, 400, unit.text)
        self.assertIn("does not have an internal layout", unit.json()["detail"])

    def test_rejects_disabling_internal_layout_while_units_exist(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(unit.status_code, 201, unit.text)

        disabled = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={"has_internal_layout": False},
        )

        self.assertEqual(disabled.status_code, 409, disabled.text)
        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.status_code, 200, listed.text)
        restored = listed.json()["objects"][0]
        self.assertTrue(restored["has_internal_layout"])
        self.assertEqual(len(restored["units"]), 1)

    def test_patch_object_and_unit_validate_layout_and_preserve_fields(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(unit.status_code, 201, unit.text)
        unit_id = unit.json()["public_id"]

        renamed = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={"name": "Dining patio"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "Dining patio")
        self.assertEqual(renamed.json()["geometry"], {"x": 1, "y": 1, "width": 4, "height": 3})

        unchanged = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={},
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(unchanged.json()["name"], "Dining patio")

        shrink = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={"internal_layout": {"rows": 2, "cols": 2}},
        )
        self.assertEqual(shrink.status_code, 400, shrink.text)
        self.assertIn("does not fit", shrink.json()["detail"])

        moved_unit = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units/{unit_id}",
            json={"name": "Thyme pot"},
        )
        self.assertEqual(moved_unit.status_code, 200, moved_unit.text)
        self.assertEqual(moved_unit.json()["name"], "Thyme pot")
        self.assertEqual(moved_unit.json()["geometry"], {"x": 2, "y": 2, "width": 2, "height": 2})

        unchanged_unit = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units/{unit_id}",
            json={},
        )
        self.assertEqual(unchanged_unit.status_code, 200, unchanged_unit.text)
        self.assertEqual(unchanged_unit.json()["name"], "Thyme pot")

        outside_unit = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}/units/{unit_id}",
            json={"geometry": {"x": 8, "y": 6, "width": 2, "height": 2}},
        )
        self.assertEqual(outside_unit.status_code, 400, outside_unit.text)
        self.assertIn("does not fit", outside_unit.json()["detail"])

    def test_unit_patch_and_delete_are_scoped_to_parent_object(self) -> None:
        garden_id = self._default_garden()
        first = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        second_payload = self._patio_payload()
        second_payload["name"] = "Second patio"
        second_payload["geometry"] = {"x": 8, "y": 1, "width": 3, "height": 3}
        second = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=second_payload,
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        first_id = first.json()["public_id"]
        second_id = second.json()["public_id"]
        unit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects/{first_id}/units",
            json=self._pot_payload(),
        )
        self.assertEqual(unit.status_code, 201, unit.text)
        unit_id = unit.json()["public_id"]

        wrong_parent_patch = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{second_id}/units/{unit_id}",
            json={"name": "Wrong parent"},
        )
        wrong_parent_delete = self.client.delete(
            f"/api/gardens/{garden_id}/map-objects/{second_id}/units/{unit_id}",
        )
        self.assertEqual(wrong_parent_patch.status_code, 404, wrong_parent_patch.text)
        self.assertEqual(wrong_parent_delete.status_code, 404, wrong_parent_delete.text)

        deleted = self.client.delete(
            f"/api/gardens/{garden_id}/map-objects/{first_id}/units/{unit_id}",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self._unit_count(garden_id, first_id), 0)

    def test_grid_shrink_rejects_existing_map_object_overflow(self) -> None:
        garden_id = self._default_garden()
        payload = self._patio_payload()
        payload["geometry"] = {"x": 20, "y": 28, "width": 3, "height": 3}
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=payload,
        )
        self.assertEqual(patio.status_code, 201, patio.text)

        current = self.client.get("/api/layout-state")
        self.assertEqual(current.status_code, 200, current.text)
        body = current.json()
        body["grid_rows"] = 27
        body["grid_cols"] = 22
        resized = self.client.patch("/api/layout-state", json=body)

        self.assertEqual(resized.status_code, 400, resized.text)
        self.assertIn("existing map object", resized.json()["detail"])

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

    def test_import_without_map_objects_preserves_existing_objects(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)

        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        payload = json.loads(export_res.content)
        payload.pop("map_objects", None)

        imported = self._import_layout(payload, "legacy-map-object-preserve")
        self.assertEqual(imported.status_code, 200, imported.text)

        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()["objects"]), 1)
        self.assertEqual(listed.json()["objects"][0]["public_id"], patio.json()["public_id"])

    def test_import_rejects_too_many_nested_units(self) -> None:
        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        payload = json.loads(export_res.content)
        map_objects: list[dict[str, object]] = []
        for object_idx in range(6):
            units = []
            for unit_idx in range(84):
                units.append(
                    {
                        "public_id": f"bulk_unit_{object_idx}_{unit_idx}",
                        "unit_type": "pot",
                        "name": f"Bulk unit {object_idx}-{unit_idx}",
                        "shape_type": "rectangle",
                        "geometry": {
                            "x": unit_idx % 10 + 1,
                            "y": unit_idx // 10 + 1,
                            "width": 1,
                            "height": 1,
                        },
                        "style": {"color": "#c58f5c"},
                        "sort_order": unit_idx,
                    },
                )
            map_objects.append(
                {
                    "public_id": f"bulk_object_{object_idx}",
                    "object_type": "patio",
                    "name": f"Bulk object {object_idx}",
                    "shape_type": "rectangle",
                    "geometry": {"x": object_idx + 1, "y": 1, "width": 1, "height": 1},
                    "style": {"color": "#7d9f7a"},
                    "z_index": object_idx,
                    "has_internal_layout": True,
                    "internal_layout": {"rows": 10, "cols": 10},
                    "units": units,
                },
            )
        payload["map_objects"] = map_objects

        imported = self._import_layout(payload, "too-many-map-units")

        self.assertEqual(imported.status_code, 400, imported.text)
        self.assertIn("Nested unit limit", imported.json()["detail"])

    def test_import_rejects_invalid_map_object_payloads(self) -> None:
        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        base_payload = json.loads(export_res.content)

        invalid_cases = [
            (
                "layout-less-object-with-units",
                {
                    "public_id": "layoutless",
                    "object_type": "patio",
                    "name": "Layoutless",
                    "shape_type": "rectangle",
                    "geometry": {"x": 1, "y": 1, "width": 1, "height": 1},
                    "style": {"color": "#7d9f7a"},
                    "z_index": 0,
                    "has_internal_layout": False,
                    "internal_layout": {"rows": 6, "cols": 8},
                    "units": [self._pot_payload()],
                },
                "Nested units require",
            ),
            (
                "object-outside-grid",
                {
                    "public_id": "outside",
                    "object_type": "patio",
                    "name": "Outside",
                    "shape_type": "rectangle",
                    "geometry": {"x": 22, "y": 30, "width": 2, "height": 2},
                    "style": {"color": "#7d9f7a"},
                    "z_index": 0,
                    "has_internal_layout": True,
                    "internal_layout": {"rows": 6, "cols": 8},
                    "units": [],
                },
                "does not fit",
            ),
            (
                "unit-outside-layout",
                {
                    "public_id": "badunit",
                    "object_type": "patio",
                    "name": "Bad unit",
                    "shape_type": "rectangle",
                    "geometry": {"x": 1, "y": 1, "width": 1, "height": 1},
                    "style": {"color": "#7d9f7a"},
                    "z_index": 0,
                    "has_internal_layout": True,
                    "internal_layout": {"rows": 2, "cols": 2},
                    "units": [
                        {
                            **self._pot_payload(),
                            "geometry": {"x": 2, "y": 2, "width": 2, "height": 2},
                        },
                    ],
                },
                "does not fit",
            ),
        ]
        for reason, item, detail in invalid_cases:
            payload = {**base_payload, "map_objects": [item]}
            imported = self._import_layout(payload, reason)
            with self.subTest(reason=reason):
                self.assertEqual(imported.status_code, 400, imported.text)
                self.assertIn(detail, imported.json()["detail"])

    def test_rejects_invalid_map_object_fields(self) -> None:
        garden_id = self._default_garden()
        invalid_object_type = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={**self._patio_payload(), "object_type": "balcony"},
        )
        invalid_color = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={**self._patio_payload(), "style": {"color": "url(javascript:alert(1))"}},
        )

        self.assertEqual(invalid_object_type.status_code, 422, invalid_object_type.text)
        self.assertEqual(invalid_color.status_code, 422, invalid_color.text)
