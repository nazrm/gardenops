import os
from datetime import date
from unittest.mock import MagicMock, patch

import gardenops.db as db
from tests.base import BaseApiTest

_AUTH_ENV = {
    "AUTH_REQUIRED": "true",
    "AUTH_MODE": "session",
    "AUTH_API_KEY": "",
}


class TestPlants(BaseApiTest):
    def test_add_plant_rejects_non_positive_quantity(self) -> None:
        response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 0},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_plant(self) -> None:
        response = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-NEW",
                "name": "New Flower",
                "category": "frø",
            },
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["plt_id"], "PLT-NEW")

    def test_create_duplicate_plant_succeeds_silently(self) -> None:
        response = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-TEST",
                "name": "Duplicate",
                "category": "frø",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["plt_id"], "PLT-TEST")

    def test_update_plant(self) -> None:
        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={"color": "blue"},
        )
        self.assertEqual(response.status_code, 200)

    def test_update_plant_seen_growing(self) -> None:
        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={"seen_growing": True, "seen_growing_date": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Test Plant").json()
        }
        self.assertTrue(plants["PLT-TEST"]["seen_growing"])
        self.assertEqual(plants["PLT-TEST"]["seen_growing_date"], date.today().isoformat())
        self.assertEqual(plants["PLT-TEST"]["seen_growing_year"], date.today().year)
        self.assertTrue(plants["PLT-TEST"]["seen_growing_is_current_year"])
        self.assertTrue(plants["PLT-TEST"]["observed_this_year"])
        self.assertEqual(plants["PLT-TEST"]["presence_status"], "present")

    def test_update_plant_seen_growing_exposes_non_current_year_metadata(self) -> None:
        previous_year = date.today().year - 1
        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={"seen_growing": True, "seen_growing_date": f"{previous_year}-05-01"},
        )
        self.assertEqual(response.status_code, 200)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Test Plant").json()
        }
        self.assertTrue(plants["PLT-TEST"]["seen_growing"])
        self.assertEqual(plants["PLT-TEST"]["seen_growing_date"], f"{previous_year}-05-01")
        self.assertEqual(plants["PLT-TEST"]["seen_growing_year"], previous_year)
        self.assertFalse(plants["PLT-TEST"]["seen_growing_is_current_year"])
        self.assertFalse(plants["PLT-TEST"]["observed_this_year"])

    def test_clear_plant_seen_growing(self) -> None:
        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={"seen_growing": True, "seen_growing_date": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={"seen_growing": None, "seen_growing_date": None},
        )
        self.assertEqual(response.status_code, 200)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Test Plant").json()
        }
        self.assertIsNone(plants["PLT-TEST"]["seen_growing"])
        self.assertIsNone(plants["PLT-TEST"]["seen_growing_date"])

    def test_update_plant_seen_growing_rejects_invalid_date(self) -> None:
        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={"seen_growing": True, "seen_growing_date": "2026-13-40"},
        )
        self.assertEqual(response.status_code, 422)

    def test_plant_assignments_expose_seen_growing_year_metadata(self) -> None:
        current_year = date.today().year
        previous_year = current_year - 1

        self.client.post(
            "/api/plants", json={"plt_id": "SG-META", "name": "Meta", "category": "frø"}
        )
        self.client.post("/api/plots/B1/plants/SG-META", json={"quantity": 1})
        self.client.post("/api/plots/B2/plants/SG-META", json={"quantity": 1})

        response = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={
                "updates": [
                    {
                        "plot_id": "B1",
                        "plt_id": "SG-META",
                        "seen_growing": True,
                        "seen_growing_date": f"{current_year}-03-23",
                    },
                    {
                        "plot_id": "B2",
                        "plt_id": "SG-META",
                        "seen_growing": False,
                        "seen_growing_date": str(previous_year),
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)

        assignments = {
            item["plot_id"]: item
            for item in self.client.get("/api/plants/SG-META/assignments").json()
        }
        self.assertEqual(assignments["B1"]["seen_growing_year"], current_year)
        self.assertTrue(assignments["B1"]["seen_growing_is_current_year"])
        self.assertEqual(assignments["B2"]["seen_growing_year"], previous_year)
        self.assertFalse(assignments["B2"]["seen_growing_is_current_year"])

        plants = {plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Meta").json()}
        self.assertTrue(plants["SG-META"]["observed_this_year"])

    def test_list_plants_exposes_last_bloom_year_metadata(self) -> None:
        previous_year = date.today().year - 1
        response = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": f"{previous_year}-06-15",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(response.status_code, 201)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Test Plant").json()
        }
        self.assertEqual(plants["PLT-TEST"]["last_bloomed_on"], f"{previous_year}-06-15")
        self.assertEqual(plants["PLT-TEST"]["last_bloomed_year"], previous_year)
        self.assertFalse(plants["PLT-TEST"]["bloomed_this_year"])
        self.assertFalse(plants["PLT-TEST"]["observed_this_year"])

    def test_list_plants_counts_current_year_bloom_as_observed_this_year(self) -> None:
        assign_response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.assertEqual(assign_response.status_code, 201)

        response = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": date.today().isoformat(),
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(response.status_code, 201)

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Test Plant").json()
        }
        self.assertEqual(plants["PLT-TEST"]["last_bloomed_on"], date.today().isoformat())
        self.assertEqual(plants["PLT-TEST"]["last_bloomed_year"], date.today().year)
        self.assertTrue(plants["PLT-TEST"]["seen_growing"])
        self.assertEqual(plants["PLT-TEST"]["seen_growing_date"], date.today().isoformat())
        self.assertTrue(plants["PLT-TEST"]["bloomed_this_year"])
        self.assertTrue(plants["PLT-TEST"]["observed_this_year"])
        assignments = self.client.get("/api/plants/PLT-TEST/assignments").json()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["plot_id"], "B1")
        self.assertTrue(assignments[0]["seen_growing"])
        self.assertEqual(assignments[0]["seen_growing_date"], date.today().isoformat())

    def test_search_plants_returns_limited_minimal_results_by_default(self) -> None:
        for index in range(3):
            response = self.client.post(
                "/api/plants",
                json={
                    "plt_id": f"PLT-SEARCH-{index}",
                    "name": f"Search Flower {index}",
                    "category": "frø",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)

        response = self.client.get("/api/plants/search?q=Search&limit=2")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertTrue(all("plt_id" in plant for plant in data))
        self.assertTrue(all("name" in plant for plant in data))
        self.assertTrue(all("latin" in plant for plant in data))
        self.assertTrue(all("category" in plant for plant in data))
        self.assertTrue(all("plot_ids" not in plant for plant in data))
        self.assertTrue(all("quantity" not in plant for plant in data))
        self.assertTrue(all("presence_status" not in plant for plant in data))

    def test_search_plants_can_include_assignments(self) -> None:
        assign_response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 2},
        )
        self.assertEqual(assign_response.status_code, 201)

        response = self.client.get("/api/plants/search?q=Test&limit=5&include_assignments=true")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["plt_id"], "PLT-TEST")
        self.assertEqual(data[0]["plot_ids"], ["B1"])
        self.assertEqual(data[0]["quantity"], 2)

    def test_get_plant_details_returns_single_plant_shape(self) -> None:
        assign_response = self.client.post(
            "/api/plots/B1/plants/PLT-TEST",
            json={"quantity": 1},
        )
        self.assertEqual(assign_response.status_code, 201)

        bloom_response = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": date.today().isoformat(),
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(bloom_response.status_code, 201)

        response = self.client.get("/api/plants/PLT-TEST/details")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["plt_id"], "PLT-TEST")
        self.assertEqual(data["plot_ids"], ["B1"])
        self.assertTrue(data["seen_growing"])
        self.assertEqual(data["seen_growing_date"], date.today().isoformat())
        self.assertTrue(data["bloomed_this_year"])
        self.assertTrue(data["observed_this_year"])

    def test_update_nonexistent_plant(self) -> None:
        response = self.client.patch(
            "/api/plants/NOPE",
            json={"color": "blue"},
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_plant_cascade(self) -> None:
        """Deleting a plant removes it from all plot assignments."""
        self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-DEL",
                "name": "To Delete",
                "category": "frø",
            },
        )
        self.client.post(
            "/api/plots/B1/plants/PLT-DEL",
            json={"quantity": 1},
        )
        response = self.client.delete("/api/plants/PLT-DEL")
        self.assertEqual(response.status_code, 200)

        plots = self.client.get("/api/plants/PLT-DEL/plots").json()
        self.assertEqual(plots, [])

    def test_search_plants_empty_query(self) -> None:
        response = self.client.get("/api/plants")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data), 0)

    def test_search_plants_by_name(self) -> None:
        response = self.client.get("/api/plants?q=Rose")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(
            any(p["name"] == "Rose" for p in data),
        )

    def test_search_plants_no_results(self) -> None:
        response = self.client.get(
            "/api/plants?q=zzzznonexistent",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 0)

    def test_generate_missing_care_updates_only_missing_plants(self) -> None:
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
        finally:
            db.return_db(conn)

        created = self._create_test_user("care_writer", "carewriterpass123", role="editor")
        user_id = int(created["id"])

        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE plants SET care_notes = %s WHERE plt_id = %s",
                ("Already documented.", "PLT-002"),
            )
            conn.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color,
                    hardiness, height_cm, light, link
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    "PLT-003",
                    "Lavender",
                    "Lavandula angustifolia",
                    "frø",
                    "juli",
                    "lilla",
                    "H4",
                    45,
                    "sol",
                    "",
                ),
            )
            db.executemany(
                conn,
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                [
                    ("PLT-TEST", user_id, default_garden_id),
                    ("PLT-002", user_id, default_garden_id),
                    ("PLT-003", user_id, default_garden_id),
                ],
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response_block = type(
            "ToolBlock",
            (),
            {
                "type": "tool_use",
                "name": "care_instructions_batch",
                "input": {
                    "plants": [
                        {
                            "plt_id": "PLT-003",
                            "care_watering": (
                                "Vann dypt, men la det tørke lett opp mellom vanningene."
                            ),
                            "care_soil": "Bruk veldrenert, mager jord i full sol.",
                            "care_planting": (
                                "Plant når jorden er varm og risikoen for hard frost er lav."
                            ),
                            "care_maintenance": (
                                "Klipp bort visne blomster og unngå vintervåt jord."
                            ),
                            "care_notes": "Dekk lett ved barfrost i utsatte områder.",
                        },
                        {
                            "plt_id": "PLT-TEST",
                            "care_watering": "Hold jorden jevnt fuktig den første vekstsesongen.",
                            "care_soil": "Bruk næringsrik jord med god drenering.",
                            "care_planting": "Plant etter siste frost og vann godt ved etablering.",
                            "care_maintenance": "Fjern skadde deler og gi lett næring om våren.",
                            "care_notes": "Følg med på snegler i fuktige perioder.",
                        },
                    ],
                },
            },
        )()
        response_payload = type("AnthropicResponse", (), {"content": [response_block]})()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = response_payload

        with (
            patch.dict(
                os.environ,
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_MODE": "session",
                    "AUTH_API_KEY": "",
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.ai_provider.Anthropic",
                return_value=mocked_client,
            ),
        ):
            client = self._new_client()
            _, csrf = self._login_session("care_writer", "carewriterpass123", client=client)
            headers = self._session_headers(csrf, garden_id=default_garden_id)

            response = client.post(
                "/api/ai/generate-missing-care",
                headers=headers,
                json={},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "generated": 2,
                "missing_before": 2,
                "remaining_without_care": 0,
                "updated_plant_ids": ["PLT-003", "PLT-TEST"],
                "attempted": 2,
                "has_more": False,
            },
        )
        mocked_client.messages.create.assert_called_once()

        conn = db.get_db()
        try:
            updated = conn.execute(
                """
                SELECT plt_id, care_watering, care_soil, care_planting,
                    care_maintenance, care_notes
                FROM plants
                WHERE plt_id IN ('PLT-002', 'PLT-003', 'PLT-TEST')
                ORDER BY plt_id
                """,
            ).fetchall()
        finally:
            db.return_db(conn)

        rows = {str(row["plt_id"]): row for row in updated}
        self.assertEqual(str(rows["PLT-002"]["care_notes"]), "Already documented.")
        self.assertEqual(
            str(rows["PLT-003"]["care_watering"]),
            "Vann dypt, men la det tørke lett opp mellom vanningene.",
        )
        self.assertEqual(
            str(rows["PLT-TEST"]["care_watering"]),
            "Hold jorden jevnt fuktig den første vekstsesongen.",
        )

    def test_generate_missing_care_respects_max_plants_and_reports_remaining(self) -> None:
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
        finally:
            db.return_db(conn)

        created = self._create_test_user("care_chunk_user", "carechunkpass123", role="editor")
        user_id = int(created["id"])

        conn = db.get_db()
        try:
            conn.execute("DELETE FROM plant_ownership")
            db.executemany(
                conn,
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color,
                    hardiness, height_cm, light, link
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        "PLT-CARE-1",
                        "Akeleie",
                        "Aquilegia vulgaris",
                        "frø",
                        "juni",
                        "blå",
                        "H7",
                        70,
                        "halvskygge",
                        "",
                    ),
                    (
                        "PLT-CARE-2",
                        "Lavendel",
                        "Lavandula angustifolia",
                        "frø",
                        "juli",
                        "lilla",
                        "H4",
                        45,
                        "sol",
                        "",
                    ),
                    (
                        "PLT-CARE-3",
                        "Timian",
                        "Thymus vulgaris",
                        "frø",
                        "juli",
                        "rosa",
                        "H4",
                        25,
                        "sol",
                        "",
                    ),
                ],
            )
            db.executemany(
                conn,
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                [
                    ("PLT-CARE-1", user_id, default_garden_id),
                    ("PLT-CARE-2", user_id, default_garden_id),
                    ("PLT-CARE-3", user_id, default_garden_id),
                ],
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response_block = type(
            "ToolBlock",
            (),
            {
                "type": "tool_use",
                "name": "care_instructions_batch",
                "input": {
                    "plants": [
                        {
                            "plt_id": "PLT-CARE-1",
                            "care_watering": "Vann jevnt i etableringsfasen.",
                            "care_soil": "Bruk jevnt fuktig jord med god struktur.",
                            "care_planting": "Plant i vår eller tidlig høst.",
                            "care_maintenance": "Klipp bort visne blomster etter hovedflor.",
                            "care_notes": "Del eldre tuer ved behov.",
                        },
                        {
                            "plt_id": "PLT-CARE-2",
                            "care_watering": "Vann sparsomt når planten er etablert.",
                            "care_soil": "Bruk mager, veldrenert jord.",
                            "care_planting": "Plant solrikt og lunt etter siste frost.",
                            "care_maintenance": "Klipp lett tilbake om våren.",
                            "care_notes": "Beskytt mot vintervåte forhold.",
                        },
                    ],
                },
            },
        )()
        response_payload = type("AnthropicResponse", (), {"content": [response_block]})()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = response_payload

        with (
            patch.dict(
                os.environ,
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_MODE": "session",
                    "AUTH_API_KEY": "",
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.ai_provider.Anthropic",
                return_value=mocked_client,
            ),
        ):
            client = self._new_client()
            _, csrf = self._login_session("care_chunk_user", "carechunkpass123", client=client)
            headers = self._session_headers(csrf, garden_id=default_garden_id)

            response = client.post(
                "/api/ai/generate-missing-care",
                headers=headers,
                json={"max_plants": 2},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "generated": 2,
                "missing_before": 3,
                "remaining_without_care": 1,
                "updated_plant_ids": ["PLT-CARE-1", "PLT-CARE-2"],
                "attempted": 2,
                "has_more": True,
            },
        )
        mocked_client.messages.create.assert_called_once()

    def test_generate_missing_care_daily_budget_counts_plants(self) -> None:
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
        finally:
            db.return_db(conn)

        created = self._create_test_user("care_budget_user", "carebudgetpass123", role="editor")
        user_id = int(created["id"])

        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color,
                    hardiness, height_cm, light, link
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    "PLT-003",
                    "Lavender",
                    "Lavandula angustifolia",
                    "frø",
                    "juli",
                    "lilla",
                    "H4",
                    45,
                    "sol",
                    "",
                ),
            )
            db.executemany(
                conn,
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                [
                    ("PLT-TEST", user_id, default_garden_id),
                    ("PLT-003", user_id, default_garden_id),
                ],
            )
            conn.commit()
        finally:
            db.return_db(conn)

        mocked_client = MagicMock()

        with (
            patch.dict(
                os.environ,
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_MODE": "session",
                    "AUTH_API_KEY": "",
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                    "AI_CARE_DAILY_BUDGET_USER": "1",
                    "AI_CARE_DAILY_BUDGET_GARDEN": "5",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.ai_provider.Anthropic",
                return_value=mocked_client,
            ),
        ):
            client = self._new_client()
            _, csrf = self._login_session("care_budget_user", "carebudgetpass123", client=client)
            headers = self._session_headers(csrf, garden_id=default_garden_id)

            response = client.post(
                "/api/ai/generate-missing-care",
                headers=headers,
                json={},
            )

        self.assertEqual(response.status_code, 429)
        self.assertIn("daily budget exhausted", response.json()["detail"])
        mocked_client.messages.create.assert_not_called()

    def test_update_plant_empty_body_ok(self) -> None:
        response = self.client.patch(
            "/api/plants/PLT-TEST",
            json={},
        )
        self.assertEqual(response.status_code, 200)

    def test_update_nonexistent_plant_by_name(self) -> None:
        response = self.client.patch(
            "/api/plants/NO-SUCH-PLANT",
            json={"name": "whatever"},
        )
        self.assertEqual(response.status_code, 404)

    def test_inventory_rejects_unknown_linked_plant(self) -> None:
        r = self.client.post(
            "/api/inventory",
            json={
                "label": "Ghost tuber",
                "plt_id": "PLT-NOPE",
            },
        )
        self.assertEqual(r.status_code, 404)
        self.assertIn("not found in active garden", r.json()["detail"])

    def test_inventory_search_filters_server_side(self) -> None:
        r = self.client.post(
            "/api/inventory",
            json={
                "label": "Tulip seed stock",
                "inventory_type": "seed",
                "plt_id": "PLT-TEST",
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        r = self.client.post(
            "/api/inventory",
            json={
                "label": "Dahlia tubers",
                "inventory_type": "tuber",
            },
        )
        self.assertEqual(r.status_code, 201, r.text)

        r = self.client.get("/api/inventory?q=tulip")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["label"], "Tulip seed stock")

    def test_viewer_cannot_create_plant(self) -> None:
        """Viewer role gets 403 on POST /api/plants."""
        os.environ.update(_AUTH_ENV)
        try:
            self._create_test_user(
                "plant_viewer",
                "viewerpass",
                "viewer",
            )
            client, headers = self._authenticated_client(
                "plant_viewer",
                "viewerpass",
            )
            resp = client.post(
                "/api/plants",
                headers=headers,
                json={
                    "plt_id": "PLT-HACK",
                    "name": "Hacked Plant",
                    "category": "frø",
                },
            )
            self.assertEqual(resp.status_code, 403)
            self.assertIn(
                "write access required",
                resp.json()["detail"].lower(),
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_external_plants_excludes_garden_owned_rows(self) -> None:
        """GET /external-plants does not expose plants from any garden."""
        os.environ.update(_AUTH_ENV)
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()

            conn = db.get_db()
            try:
                user_row = conn.execute(
                    "SELECT id FROM auth_users WHERE username = %s",
                    (username,),
                ).fetchone()
                assert user_row is not None
                uid = int(user_row["id"])

                conn.execute(
                    """INSERT INTO plants
                       (plt_id, name, latin, category,
                        bloom_month, color, hardiness,
                        height_cm, light, link)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        "PLT-G1",
                        "Lavendel",
                        "Lavandula",
                        "busker",
                        "",
                        "",
                        "",
                        None,
                        "",
                        "",
                    ),
                )
                conn.execute(
                    """INSERT INTO plant_ownership
                       (plt_id, owner_user_id, garden_id)
                       VALUES (%s, %s, %s)""",
                    ("PLT-G1", uid, gid1),
                )
                conn.execute(
                    """INSERT INTO plants
                       (plt_id, name, latin, category,
                        bloom_month, color, hardiness,
                        height_cm, light, link)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        "PLT-G2",
                        "Lavendel Blad",
                        "Lavandula latifolia",
                        "busker",
                        "",
                        "",
                        "",
                        None,
                        "",
                        "",
                    ),
                )
                conn.execute(
                    """INSERT INTO plant_ownership
                       (plt_id, owner_user_id, garden_id)
                       VALUES (%s, %s, %s)""",
                    ("PLT-G2", uid, gid2),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                username,
                password,
                garden_id=gid1,
            )
            resp = client.get(
                "/api/external-plants?q=Lav",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data, [])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_next_plant_id_is_scoped_to_active_garden_and_insertable(self) -> None:
        os.environ.update(_AUTH_ENV)
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plants (plt_id, name, category)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plt_id) DO NOTHING
                    """,
                    ("PLT-001", "Global collision", "busker"),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    ("PLT-001", self._owner_id, gid1),
                )
                conn.commit()
            finally:
                db.return_db(conn)
            client, headers = self._authenticated_client(
                username,
                password,
                garden_id=gid2,
            )

            response = client.get("/api/plants/next-id", headers=headers)

            self.assertEqual(response.status_code, 200)
            next_id = response.json()["next_id"]
            self.assertNotEqual(next_id, "PLT-001")
            self.assertTrue(next_id.startswith(f"PLT-G{gid2}-"))

            create_response = client.post(
                "/api/plants",
                headers=headers,
                json={"plt_id": next_id, "name": "Garden two safe id", "category": "frø"},
            )
            self.assertEqual(create_response.status_code, 201, create_response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_create_and_import_reject_foreign_garden_plant_ids(self) -> None:
        os.environ.update(_AUTH_ENV)
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()
            conn = db.get_db()
            try:
                user_row = conn.execute(
                    "SELECT id FROM auth_users WHERE username = %s",
                    (username,),
                ).fetchone()
                assert user_row is not None
                uid = int(user_row["id"])
                conn.execute(
                    """
                    INSERT INTO plants (
                        plt_id, name, latin, category, bloom_month, color,
                        hardiness, height_cm, light, link
                    )
                    VALUES (%s, %s, '', 'busker', '', '', '', NULL, '', '')
                    """,
                    ("PLT-FOREIGN", "Foreign garden plant"),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    """,
                    ("PLT-FOREIGN", uid, gid2),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                username,
                password,
                garden_id=gid1,
            )
            create_response = client.post(
                "/api/plants",
                headers=headers,
                json={
                    "plt_id": "PLT-FOREIGN",
                    "name": "Hijacked plant",
                    "category": "busker",
                },
            )
            self.assertEqual(create_response.status_code, 409)
            self.assertIn("already belongs to another garden", create_response.json()["detail"])

            csv_text = "\n".join(
                [
                    (
                        "plt_id,name,latin,category,bloom_month,color,hardiness,height_cm,"
                        "light,link,year_planted,deer_resistant"
                    ),
                    ",".join(
                        [
                            "PLT-FOREIGN",
                            "Hijacked CSV",
                            "",
                            "busker",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "false",
                        ],
                    ),
                ],
            )
            import_response = client.post(
                "/api/plants/import-csv",
                headers=headers,
                json={"csv_text": csv_text},
            )
            self.assertEqual(import_response.status_code, 409)
            self.assertIn("already belongs to another garden", import_response.json()["detail"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_batch_update_rejects_plot_from_other_active_garden(self) -> None:
        os.environ.update(_AUTH_ENV)
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    ("PLT-TEST", self._owner_id, gid1),
                )
                conn.commit()
            finally:
                db.return_db(conn)
            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            default_headers = self._session_headers(csrf, garden_id=gid1)
            second_headers = self._session_headers(csrf, garden_id=gid2)
            foreign_plot_id = f"BG2-PLOT-{os.urandom(3).hex()}"

            created = client.post(
                "/api/plots",
                headers=second_headers,
                json={
                    "plot_id": foreign_plot_id,
                    "zone_code": "P",
                    "zone_name": "Batch Garden Two",
                    "plot_number": 1,
                    "grid_row": 97,
                    "grid_col": 97,
                    "sub_zone": "",
                    "notes": "",
                    "color": None,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)

            response = client.post(
                "/api/plants/batch-update",
                headers=default_headers,
                json={
                    "plt_ids": ["PLT-TEST"],
                    "updates": {},
                    "plot_ids": [foreign_plot_id],
                    "plot_action": "assign",
                },
            )
            self.assertEqual(response.status_code, 404, response.text)
            self.assertIn("not found in active garden", response.json()["detail"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_batch_update_rejects_shared_global_plant_mutation(self) -> None:
        os.environ.update(_AUTH_ENV)
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    ("PLT-TEST", self._owner_id, gid1),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    ("PLT-TEST", self._owner_id, gid2),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            response = client.post(
                "/api/plants/batch-update",
                headers=self._session_headers(csrf, garden_id=gid1),
                json={
                    "plt_ids": ["PLT-TEST"],
                    "updates": {"color": "red"},
                    "plot_ids": [],
                    "plot_action": "assign",
                },
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("shared with another garden", response.json()["detail"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_batch_update_rejects_unowned_plot_target(self) -> None:
        os.environ.update(_AUTH_ENV)
        try:
            gid1, _gid2, username, password = self._setup_admin_two_gardens()
            unowned_plot_id = f"UNOWNED-BATCH-{os.urandom(3).hex()}"
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    ("PLT-TEST", self._owner_id, gid1),
                )
                conn.execute(
                    """
                    INSERT INTO plots (
                        plot_id, zone_code, zone_name, plot_number,
                        grid_row, grid_col, sub_zone, notes, color
                    )
                    VALUES (%s, 'U', 'Unowned', 1, NULL, NULL, '', '', NULL)
                    """,
                    (unowned_plot_id,),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            response = client.post(
                "/api/plants/batch-update",
                headers=self._session_headers(csrf, garden_id=gid1),
                json={
                    "plt_ids": ["PLT-TEST"],
                    "updates": {},
                    "plot_ids": [unowned_plot_id],
                    "plot_action": "assign",
                },
            )
            self.assertEqual(response.status_code, 404, response.text)
            self.assertIn("not found in active garden", response.json()["detail"])

            conn = db.get_db()
            try:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM plot_plants
                    WHERE plot_id = %s AND plt_id = 'PLT-TEST'
                    """,
                    (unowned_plot_id,),
                ).fetchone()
                self.assertIsNone(row)
            finally:
                db.return_db(conn)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
