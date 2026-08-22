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
            "geometry": {"x": 18, "y": 1, "width": 4, "height": 3},
            "style": {"color": "#7d9f7a"},
            "z_index": 2,
            "has_internal_layout": True,
            "internal_layout": {"rows": 6, "cols": 8},
        }

    @staticmethod
    def _legacy_unit_payload() -> dict[str, object]:
        return {
            "unit_type": "pot",
            "name": "Rosemary pot",
            "shape_type": "ellipse",
            "geometry": {"x": 2, "y": 2, "width": 2, "height": 2},
            "style": {"color": "#c58f5c"},
            "sort_order": 1,
        }

    @staticmethod
    def _container_payload(parent_object_public_id: str) -> dict[str, object]:
        return {
            "name": "Rosemary pot",
            "container_type": "pot",
            "parent_object_public_id": parent_object_public_id,
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
    def _seed_map_object(
        garden_id: int,
        *,
        public_id: str,
        name: str,
        geometry: dict[str, int],
    ) -> None:
        conn = db.get_db()
        now_ms = db.current_timestamp_ms()
        try:
            conn.execute(
                """
                INSERT INTO garden_map_objects (
                    public_id, garden_id, object_type, name, shape_type,
                    geometry_json, style_json, z_index, has_internal_layout,
                    internal_layout_json, created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, 'patio', %s, 'rectangle', %s, %s, 0, 0, %s, %s, %s)
                """,
                (
                    public_id,
                    garden_id,
                    name,
                    json.dumps(geometry, separators=(",", ":")),
                    json.dumps({"color": "#7d9f7a"}),
                    json.dumps({"rows": 6, "cols": 8}),
                    now_ms,
                    now_ms,
                ),
            )
            conn.commit()
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

    def test_editor_can_create_list_and_delete_area(self) -> None:
        garden_id = self._default_garden()
        create_path = f"/api/gardens/{garden_id}/map-objects"

        created = self.client.post(
            create_path,
            json=self._patio_payload(),
        )
        self.assertEqual(created.status_code, 201, created.text)
        patio = created.json()
        self.assertEqual(patio["object_type"], "patio")
        self.assertEqual(patio["name"], "Kitchen patio")
        self.assertEqual(patio["geometry"], {"x": 18, "y": 1, "width": 4, "height": 3})
        self.assertEqual(patio["style"], {"color": "#7d9f7a"})
        self.assertEqual(patio["internal_layout"], {"rows": 6, "cols": 8})
        self.assertEqual(patio["container_count"], 0)
        self.assertEqual(patio["plant_count"], 0)
        self.assertEqual(patio["containers"], [])

        conn = db.get_db()
        try:
            audit_rows = conn.execute(
                """
                SELECT status_code
                FROM audit_events
                WHERE method = 'POST' AND path = %s
                """,
                (create_path,),
            ).fetchall()
        finally:
            db.return_db(conn)
        self.assertEqual([int(row["status_code"]) for row in audit_rows], [201])

        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.status_code, 200, listed.text)
        objects = listed.json()["objects"]
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["public_id"], patio["public_id"])
        self.assertEqual(objects[0]["containers"], [])
        self.assertEqual(listed.json()["containers"], [])

        deleted = self.client.delete(f"/api/gardens/{garden_id}/map-objects/{patio['public_id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["unparented_containers"], 0)

        deleted_again = self.client.delete(
            f"/api/gardens/{garden_id}/map-objects/{patio['public_id']}"
        )
        self.assertEqual(deleted_again.status_code, 404, deleted_again.text)

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

    def test_viewer_cannot_mutate_existing_map_objects(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]

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
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (first_garden_id, self._owner_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        patio = self.client.post(
            f"/api/gardens/{first_garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        container = self.client.post(
            f"/api/gardens/{first_garden_id}/containers",
            json=self._container_payload(patio_id),
        )
        self.assertEqual(container.status_code, 201, container.text)
        container_id = container.json()["plot_id"]

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
                    f"/api/gardens/{first_garden_id}/containers",
                    headers=headers,
                    json=self._container_payload(patio_id),
                ),
                client.get(
                    f"/api/gardens/{first_garden_id}/containers/{container_id}",
                    headers=headers,
                ),
                client.patch(
                    f"/api/gardens/{first_garden_id}/containers/{container_id}",
                    headers=headers,
                    json={"name": "No access"},
                ),
                client.delete(
                    f"/api/gardens/{first_garden_id}/containers/{container_id}",
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

    def test_rejects_plot_house_and_area_overlaps(self) -> None:
        garden_id = self._default_garden()

        plot_overlap = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={
                **self._patio_payload(),
                "geometry": {"x": 1, "y": 1, "width": 1, "height": 1},
            },
        )
        self.assertEqual(plot_overlap.status_code, 409, plot_overlap.text)
        self.assertIn("plot B1", plot_overlap.json()["detail"])

        house_overlap = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={
                **self._patio_payload(),
                "geometry": {"x": 6, "y": 9, "width": 1, "height": 1},
            },
        )
        self.assertEqual(house_overlap.status_code, 409, house_overlap.text)
        self.assertIn("house", house_overlap.json()["detail"])

        first = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(first.status_code, 201, first.text)
        area_overlap = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={
                **self._patio_payload(),
                "name": "Overlapping area",
                "geometry": {"x": 18, "y": 1, "width": 1, "height": 1},
            },
        )
        self.assertEqual(area_overlap.status_code, 409, area_overlap.text)
        self.assertIn("area Kitchen patio", area_overlap.json()["detail"])

    def test_edge_adjacency_and_container_plot_do_not_block(self) -> None:
        garden_id = self._default_garden()
        first = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(first.status_code, 201, first.text)
        container = self.client.post(
            f"/api/gardens/{garden_id}/containers",
            json=self._container_payload(first.json()["public_id"]),
        )
        self.assertEqual(container.status_code, 201, container.text)

        adjacent = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={
                **self._patio_payload(),
                "name": "Edge area",
                "geometry": {"x": 22, "y": 1, "width": 1, "height": 1},
            },
        )
        self.assertEqual(adjacent.status_code, 201, adjacent.text)

        container_clear = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={
                **self._patio_payload(),
                "name": "Container-clear area",
                "geometry": {"x": 3, "y": 1, "width": 1, "height": 1},
            },
        )
        self.assertEqual(container_clear.status_code, 201, container_clear.text)

    def test_legacy_overlap_allows_metadata_and_same_geometry_but_rejects_changed_geometry(
        self,
    ) -> None:
        garden_id = self._default_garden()
        self._seed_map_object(
            garden_id,
            public_id="legacy-area-a",
            name="Legacy area A",
            geometry={"x": 18, "y": 1, "width": 4, "height": 3},
        )
        self._seed_map_object(
            garden_id,
            public_id="legacy-area-b",
            name="Legacy area B",
            geometry={"x": 20, "y": 2, "width": 2, "height": 2},
        )

        metadata = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/legacy-area-a",
            json={"name": "Renamed legacy area"},
        )
        self.assertEqual(metadata.status_code, 200, metadata.text)

        same_geometry = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/legacy-area-a",
            json={"geometry": {"height": 3, "width": 4, "y": 1, "x": 18}},
        )
        self.assertEqual(same_geometry.status_code, 200, same_geometry.text)

        changed_geometry = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/legacy-area-a",
            json={
                "geometry": {"x": 19, "y": 1, "width": 4, "height": 3},
            },
        )
        self.assertEqual(changed_geometry.status_code, 409, changed_geometry.text)
        self.assertIn("area Legacy area B", changed_geometry.json()["detail"])

    def test_layout_state_rejects_house_overlap_with_area(self) -> None:
        garden_id = self._default_garden()
        area = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(area.status_code, 201, area.text)

        current = self.client.get("/api/layout-state")
        self.assertEqual(current.status_code, 200, current.text)
        body = current.json()
        body.update({"row": 1, "col": 18, "width": 1, "height": 1})

        updated = self.client.patch("/api/layout-state", json=body)

        self.assertEqual(updated.status_code, 409, updated.text)
        self.assertIn("area Kitchen patio", updated.json()["detail"])

    def test_patch_area_preserves_fields_and_containers_ignore_internal_layout(self) -> None:
        garden_id = self._default_garden()
        patio = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(patio.status_code, 201, patio.text)
        patio_id = patio.json()["public_id"]
        container = self.client.post(
            f"/api/gardens/{garden_id}/containers",
            json=self._container_payload(patio_id),
        )
        self.assertEqual(container.status_code, 201, container.text)
        container_id = container.json()["plot_id"]

        renamed = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={"name": "Dining patio"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "Dining patio")
        self.assertEqual(renamed.json()["geometry"], {"x": 18, "y": 1, "width": 4, "height": 3})

        unchanged = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={},
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(unchanged.json()["name"], "Dining patio")

        layout_changed = self.client.patch(
            f"/api/gardens/{garden_id}/map-objects/{patio_id}",
            json={
                "has_internal_layout": False,
                "internal_layout": {"rows": 2, "cols": 2},
            },
        )
        self.assertEqual(layout_changed.status_code, 200, layout_changed.text)
        self.assertFalse(layout_changed.json()["has_internal_layout"])
        self.assertEqual(layout_changed.json()["internal_layout"], {"rows": 2, "cols": 2})
        self.assertEqual(layout_changed.json()["container_count"], 1)
        self.assertEqual(layout_changed.json()["containers"][0]["plot_id"], container_id)

        readable = self.client.get(
            f"/api/gardens/{garden_id}/containers/{container_id}",
        )
        self.assertEqual(readable.status_code, 200, readable.text)

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

    def test_rejects_map_object_count_limit(self) -> None:
        garden_id = self._default_garden()
        self._seed_map_object_count(garden_id, 200)

        over_limit = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json=self._patio_payload(),
        )
        self.assertEqual(over_limit.status_code, 400, over_limit.text)
        self.assertIn("limit", over_limit.json()["detail"].lower())

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

    def test_v1_import_rejects_too_many_units_after_translation(self) -> None:
        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        payload = json.loads(export_res.content)
        payload["schema_version"] = 1
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
        self.assertIn("Container limit", imported.json()["detail"])

    def test_v1_import_translates_units_without_internal_layout_rules(self) -> None:
        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        payload = json.loads(export_res.content)
        payload["schema_version"] = 1
        legacy_unit = {
            **self._legacy_unit_payload(),
            "public_id": "legacy-pot",
            "geometry": {"x": 2, "y": 2, "width": 2, "height": 2},
        }
        payload["map_objects"] = [
            {
                "public_id": "layoutless",
                "object_type": "patio",
                "name": "Layoutless patio",
                "shape_type": "rectangle",
                "geometry": {"x": 1, "y": 1, "width": 1, "height": 1},
                "style": {"color": "#7d9f7a"},
                "z_index": 0,
                "has_internal_layout": False,
                "internal_layout": {"rows": 1, "cols": 1},
                "units": [legacy_unit],
            },
        ]

        imported = self._import_layout(payload, "legacy-unit-translation")
        self.assertEqual(imported.status_code, 200, imported.text)

        garden_id = self._default_garden()
        listed = self.client.get(f"/api/gardens/{garden_id}/map-objects")
        self.assertEqual(listed.status_code, 200, listed.text)
        area = listed.json()["objects"][0]
        self.assertFalse(area["has_internal_layout"])
        self.assertEqual(area["container_count"], 1)
        self.assertEqual(area["containers"][0]["plot_id"], "CONT-31a0c0d7979dc5938233e65709d548e7")

    def test_import_rejects_map_object_outside_grid(self) -> None:
        export_res = self.client.get("/api/plots/export")
        self.assertEqual(export_res.status_code, 200, export_res.text)
        payload = json.loads(export_res.content)
        payload["map_objects"] = [
            {
                "public_id": "outside",
                "object_type": "patio",
                "name": "Outside",
                "shape_type": "rectangle",
                "geometry": {"x": 22, "y": 30, "width": 2, "height": 2},
                "style": {"color": "#7d9f7a"},
                "z_index": 0,
                "has_internal_layout": False,
                "internal_layout": None,
                "units": [],
            },
        ]

        imported = self._import_layout(payload, "object-outside-grid")
        self.assertEqual(imported.status_code, 400, imported.text)
        self.assertIn("does not fit", imported.json()["detail"])

    def test_accepts_balcony_and_rejects_invalid_map_object_fields(self) -> None:
        garden_id = self._default_garden()
        balcony = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={**self._patio_payload(), "object_type": "balcony"},
        )
        invalid_object_type = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={**self._patio_payload(), "object_type": "bench"},
        )
        invalid_color = self.client.post(
            f"/api/gardens/{garden_id}/map-objects",
            json={**self._patio_payload(), "style": {"color": "url(javascript:alert(1))"}},
        )

        self.assertEqual(balcony.status_code, 201, balcony.text)
        self.assertEqual(balcony.json()["object_type"], "balcony")
        self.assertEqual(invalid_object_type.status_code, 422, invalid_object_type.text)
        self.assertEqual(invalid_color.status_code, 422, invalid_color.text)
