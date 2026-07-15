import os
from concurrent.futures import ThreadPoolExecutor

import gardenops.db as db
from tests.base import BaseApiTest


class TestProcurementApi(BaseApiTest):
    """Tests for procurement CRUD, transitions, and summary endpoints."""

    def test_procurement_crud(self) -> None:
        """Create, read, update, delete a procurement item."""
        # Create
        resp = self.client.post(
            "/api/procurement",
            json={
                "label": "Tulip bulbs",
                "inventory_type": "bulb",
                "vendor_name": "Plantasjen",
                "cost_minor": 2500,
                "quantity": 20,
                "unit": "pieces",
                "notes": "Red tulips for zone B",
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        item_id = data["id"]

        # Read
        resp = self.client.get(f"/api/procurement/{item_id}")
        self.assertEqual(resp.status_code, 200)
        item = resp.json()
        self.assertEqual(item["label"], "Tulip bulbs")
        self.assertEqual(item["inventory_type"], "bulb")
        self.assertEqual(item["vendor_name"], "Plantasjen")
        self.assertEqual(item["cost_minor"], 2500)
        self.assertEqual(item["quantity"], "20")
        self.assertEqual(item["unit"], "pieces")
        self.assertEqual(item["status"], "wanted")
        self.assertEqual(item["notes"], "Red tulips for zone B")

        # Update
        resp = self.client.patch(
            f"/api/procurement/{item_id}",
            json={
                "vendor_name": "Hageland",
                "cost_minor": 3000,
                "notes": "Changed vendor",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        # Verify update
        resp = self.client.get(f"/api/procurement/{item_id}")
        item = resp.json()
        self.assertEqual(item["vendor_name"], "Hageland")
        self.assertEqual(item["cost_minor"], 3000)
        self.assertEqual(item["notes"], "Changed vendor")

        # Delete
        resp = self.client.delete(f"/api/procurement/{item_id}")
        self.assertEqual(resp.status_code, 200)

        # Verify deleted
        resp = self.client.get(f"/api/procurement/{item_id}")
        self.assertEqual(resp.status_code, 404)

    def test_procurement_list_filters(self) -> None:
        """Filter procurement items by status and inventory type."""
        self.client.post(
            "/api/procurement",
            json={
                "label": "Rose bush",
                "inventory_type": "nursery",
                "status": "wanted",
            },
        )
        self.client.post(
            "/api/procurement",
            json={
                "label": "Crocus bulbs",
                "inventory_type": "bulb",
                "status": "wanted",
            },
        )

        # Transition one to ordered
        resp = self.client.get("/api/procurement")
        items = resp.json()["items"]
        rose_id = next(i["id"] for i in items if i["label"] == "Rose bush")
        self.client.post(
            f"/api/procurement/{rose_id}/transition",
            json={"to_status": "ordered", "ordered_on": "2026-03-01"},
        )

        # Filter by status
        resp = self.client.get("/api/procurement?status=wanted")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["label"], "Crocus bulbs")

        resp = self.client.get("/api/procurement?status=ordered")
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["label"], "Rose bush")

        # Filter by type
        resp = self.client.get("/api/procurement?inventory_type=bulb")
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["inventory_type"], "bulb")

        # No matches
        resp = self.client.get("/api/procurement?inventory_type=tuber")
        data = resp.json()
        self.assertEqual(data["total"], 0)

    def test_procurement_transitions(self) -> None:
        """Test the full lifecycle: wanted -> ordered -> shipped -> received, cancel, reopen."""
        resp = self.client.post(
            "/api/procurement",
            json={
                "label": "Lavender seeds",
                "inventory_type": "seed",
            },
        )
        item_id = resp.json()["id"]

        # wanted -> ordered
        resp = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "ordered", "ordered_on": "2026-03-10"},
        )
        self.assertEqual(resp.status_code, 200)
        item = self.client.get(f"/api/procurement/{item_id}").json()
        self.assertEqual(item["status"], "ordered")
        self.assertEqual(item["ordered_on"], "2026-03-10")

        # ordered -> shipped
        resp = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "shipped"},
        )
        self.assertEqual(resp.status_code, 200)
        item = self.client.get(f"/api/procurement/{item_id}").json()
        self.assertEqual(item["status"], "shipped")

        # shipped -> received
        resp = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "received", "received_on": "2026-03-14"},
        )
        self.assertEqual(resp.status_code, 200)
        item = self.client.get(f"/api/procurement/{item_id}").json()
        self.assertEqual(item["status"], "received")
        self.assertEqual(item["received_on"], "2026-03-14")

        # Received records are immutable because their inventory ledger entry is durable.
        resp = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "ordered"},
        )
        self.assertEqual(resp.status_code, 409)

        resp = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "cancelled"},
        )
        self.assertEqual(resp.status_code, 409)
        item = self.client.get(f"/api/procurement/{item_id}").json()
        self.assertEqual(item["status"], "received")

        self.assertEqual(
            self.client.patch(f"/api/procurement/{item_id}", json={"notes": "changed"}).status_code,
            409,
        )
        self.assertEqual(self.client.delete(f"/api/procurement/{item_id}").status_code, 409)

    def test_create_and_patch_cannot_bypass_received_transition(self) -> None:
        direct_create = self.client.post(
            "/api/procurement",
            json={"label": "Direct receipt", "status": "received"},
        )
        self.assertEqual(direct_create.status_code, 422, direct_create.text)

        created = self.client.post("/api/procurement", json={"label": "Patch receipt"})
        self.assertEqual(created.status_code, 201, created.text)
        direct_patch = self.client.patch(
            f"/api/procurement/{created.json()['id']}",
            json={"status": "received"},
        )
        self.assertEqual(direct_patch.status_code, 422, direct_patch.text)

    def test_decimal_receipt_reconciles_and_records_actor_provenance(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            user = self._create_test_user("receipt_actor", "editorpass", "editor")
            client, headers = self._authenticated_client("receipt_actor", "editorpass")
            created = client.post(
                "/api/procurement",
                headers=headers,
                json={
                    "label": "Bulk soil",
                    "inventory_type": "other",
                    "quantity": "2.375",
                    "unit": "kg",
                    "cost_minor": 400,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            item_id = created.json()["id"]
            for target in ("ordered", "shipped"):
                response = client.post(
                    f"/api/procurement/{item_id}/transition",
                    headers=headers,
                    json={"to_status": target},
                )
                self.assertEqual(response.status_code, 200, response.text)
            received = client.post(
                f"/api/procurement/{item_id}/transition",
                headers=headers,
                json={"to_status": "received", "received_on": "2026-03-14"},
            )
            self.assertEqual(received.status_code, 200, received.text)

            procurement = client.get(f"/api/procurement/{item_id}", headers=headers).json()
            inventory_id = procurement["metadata"]["inventory_item_id"]
            inventory = client.get(f"/api/inventory/{inventory_id}", headers=headers).json()
            ledger = client.get(
                f"/api/inventory/{inventory_id}/transactions",
                headers=headers,
            ).json()["transactions"]
            self.assertEqual(procurement["quantity"], "2.375")
            self.assertEqual(inventory["quantity"], "2.375")
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["delta"], "2.375")
            self.assertEqual(ledger[0]["cost_minor"], 950)
            self.assertEqual(ledger[0]["actor_user_id"], int(user["id"]))
            self.assertEqual(ledger[0]["actor_username"], "receipt_actor")
            self.assertGreater(ledger[0]["created_at_ms"], 0)

            conn = db.get_db()
            try:
                provenance = conn.execute(
                    """
                    SELECT received_by_user_id, received_at_ms,
                           receipt_inventory_transaction_id
                    FROM procurement_items
                    WHERE public_id = %s
                    """,
                    (item_id,),
                ).fetchone()
                assert provenance is not None
                self.assertEqual(int(provenance["received_by_user_id"]), int(user["id"]))
                self.assertEqual(int(provenance["received_at_ms"]), ledger[0]["created_at_ms"])
                self.assertEqual(
                    int(provenance["receipt_inventory_transaction_id"]),
                    ledger[0]["id"],
                )
            finally:
                db.return_db(conn)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_concurrent_repeated_receipt_is_idempotent(self) -> None:
        created = self.client.post(
            "/api/procurement",
            json={"label": "Concurrent receipt", "quantity": "1.5", "unit": "kg"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        item_id = created.json()["id"]
        for target in ("ordered", "shipped"):
            response = self.client.post(
                f"/api/procurement/{item_id}/transition",
                json={"to_status": target},
            )
            self.assertEqual(response.status_code, 200, response.text)

        def receive() -> int:
            client = self._new_client()
            response = client.post(
                f"/api/procurement/{item_id}/transition",
                json={"to_status": "received", "received_on": "2026-03-14"},
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: receive(), range(2)))

        self.assertEqual(statuses, [200, 200])
        procurement = self.client.get(f"/api/procurement/{item_id}").json()
        inventory_id = procurement["metadata"]["inventory_item_id"]
        ledger = self.client.get(f"/api/inventory/{inventory_id}/transactions").json()
        self.assertEqual(ledger["total"], 1)
        self.assertEqual(ledger["transactions"][0]["delta"], "1.5")

        repeated = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "received", "received_on": "2026-03-15"},
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(
            self.client.get(f"/api/inventory/{inventory_id}/transactions").json()["total"],
            1,
        )
        self.assertEqual(self.client.delete(f"/api/inventory/{inventory_id}").status_code, 409)

    def test_precision_round_trip_preserves_full_numeric_scale(self) -> None:
        precise = "12345678901234.123456"
        created = self.client.post(
            "/api/procurement",
            json={"label": "Precise bulk", "quantity": precise, "unit": "kg"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        item_id = created.json()["id"]
        response = self.client.get(f"/api/procurement/{item_id}").json()
        self.assertEqual(response["quantity"], precise)
        listed = self.client.get("/api/procurement").json()["items"]
        listed_quantity = next(item for item in listed if item["id"] == item_id)["quantity"]
        self.assertEqual(listed_quantity, precise)

    def test_receipt_provenance_is_garden_scoped(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            first_garden, second_garden, username, password = self._setup_admin_two_gardens()
            first_client, first_headers = self._authenticated_client(
                username,
                password,
                garden_id=first_garden,
            )
            created = first_client.post(
                "/api/procurement",
                headers=first_headers,
                json={"label": "Scoped receipt", "quantity": "0.75", "unit": "kg"},
            )
            self.assertEqual(created.status_code, 201, created.text)
            procurement_id = created.json()["id"]
            for target in ("ordered", "shipped", "received"):
                response = first_client.post(
                    f"/api/procurement/{procurement_id}/transition",
                    headers=first_headers,
                    json={"to_status": target},
                )
                self.assertEqual(response.status_code, 200, response.text)
            procurement = first_client.get(
                f"/api/procurement/{procurement_id}",
                headers=first_headers,
            ).json()
            inventory_id = procurement["metadata"]["inventory_item_id"]

            second_client, second_headers = self._authenticated_client(
                username,
                password,
                garden_id=second_garden,
            )
            self.assertEqual(
                second_client.get(
                    f"/api/procurement/{procurement_id}",
                    headers=second_headers,
                ).status_code,
                404,
            )
            self.assertEqual(
                second_client.get(
                    f"/api/inventory/{inventory_id}",
                    headers=second_headers,
                ).status_code,
                404,
            )

            conn = db.get_db()
            try:
                provenance = conn.execute(
                    """
                    SELECT procurement.garden_id AS procurement_garden_id,
                           inventory.garden_id AS inventory_garden_id,
                           transaction.garden_id AS transaction_garden_id
                    FROM procurement_items AS procurement
                    JOIN inventory_items AS inventory
                      ON inventory.id = procurement.receipt_inventory_item_id
                    JOIN inventory_transactions AS transaction
                      ON transaction.id = procurement.receipt_inventory_transaction_id
                    WHERE procurement.public_id = %s
                    """,
                    (procurement_id,),
                ).fetchone()
                assert provenance is not None
                self.assertEqual(
                    {
                        int(provenance["procurement_garden_id"]),
                        int(provenance["inventory_garden_id"]),
                        int(provenance["transaction_garden_id"]),
                    },
                    {first_garden},
                )
            finally:
                db.return_db(conn)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_inventory_surfaces_procurement_history(self) -> None:
        resp = self.client.post(
            "/api/procurement",
            json={
                "label": "Peony roots",
                "inventory_type": "bare_root",
                "linked_plt_id": "PLT-TEST",
                "vendor_name": "Garden Source",
                "vendor_url": "https://example.test/peony",
                "quantity": 2,
                "unit": "pieces",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        item_id = resp.json()["id"]

        self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "ordered", "ordered_on": "2026-03-10"},
        )
        self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "shipped"},
        )
        received = self.client.post(
            f"/api/procurement/{item_id}/transition",
            json={"to_status": "received", "received_on": "2026-03-14"},
        )
        self.assertEqual(received.status_code, 200, received.text)

        procurement = self.client.get(f"/api/procurement/{item_id}").json()
        inventory_item_id = procurement["metadata"]["inventory_item_id"]
        self.assertIsInstance(inventory_item_id, str)

        inventory = self.client.get("/api/inventory").json()
        self.assertEqual(inventory["total"], 1)
        self.assertEqual(inventory["items"][0]["id"], inventory_item_id)
        history = inventory["items"][0]["procurement_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["vendor_name"], "Garden Source")
        self.assertEqual(history[0]["status"], "received")
        self.assertEqual(history[0]["label"], "Peony roots")

        filtered = self.client.get(f"/api/procurement?inventory_item_id={inventory_item_id}")
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["id"], item_id)

    def test_procurement_summary(self) -> None:
        """Verify summary counts and cost totals."""
        self.client.post(
            "/api/procurement",
            json={
                "label": "Item A",
                "cost_minor": 1000,
                "quantity": 2,
            },
        )
        self.client.post(
            "/api/procurement",
            json={
                "label": "Item B",
                "cost_minor": 500,
                "quantity": 3,
            },
        )

        # Transition one to ordered
        resp = self.client.get("/api/procurement")
        items = resp.json()["items"]
        a_id = next(i["id"] for i in items if i["label"] == "Item A")
        self.client.post(
            f"/api/procurement/{a_id}/transition",
            json={"to_status": "ordered"},
        )

        resp = self.client.get("/api/procurement/summary")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertEqual(summary["wanted"], 1)
        self.assertEqual(summary["ordered"], 1)
        self.assertEqual(summary["total"], 2)
        # Item A: 1000 * 2 = 2000, Item B: 500 * 3 = 1500
        self.assertEqual(summary["total_cost_minor"], 3500)
        self.assertEqual(summary["currency"], "NOK")

    def test_procurement_auth_viewer_denied(self) -> None:
        """Viewer role gets 403 on write operations."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("viewer_proc", "viewerpass", "viewer")
            self._create_test_user("editor_proc", "editorpass", "editor")

            editor_client, editor_h = self._authenticated_client(
                "editor_proc",
                "editorpass",
            )
            r = editor_client.post(
                "/api/procurement",
                headers=editor_h,
                json={
                    "label": "Test seeds",
                    "inventory_type": "seed",
                },
            )
            self.assertEqual(r.status_code, 201)
            item_id = r.json()["id"]

            viewer_client, viewer_h = self._authenticated_client(
                "viewer_proc",
                "viewerpass",
            )
            r = viewer_client.get("/api/procurement", headers=viewer_h)
            self.assertEqual(r.status_code, 200)

            r = viewer_client.post(
                "/api/procurement",
                headers=viewer_h,
                json={
                    "label": "Forbidden item",
                },
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.patch(
                f"/api/procurement/{item_id}",
                headers=viewer_h,
                json={"label": "hacked"},
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.post(
                f"/api/procurement/{item_id}/transition",
                headers=viewer_h,
                json={"to_status": "ordered"},
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.delete(f"/api/procurement/{item_id}", headers=viewer_h)
            self.assertEqual(r.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_procurement_rejects_out_of_scope_links_on_create_and_update(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            user = self._create_test_user("editor_scope", "editorpass", "editor")
            default_garden_id = self._get_default_garden_id()

            conn = db.get_db()
            try:
                other_garden = conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("procurement-other", "Procurement Other"),
                ).fetchone()
                assert other_garden is not None
                other_garden_id = int(other_garden["id"])
                conn.execute(
                    """
                    INSERT INTO plants (plt_id, name, latin, category, bloom_month, color,
                        hardiness, height_cm, light, link)
                    VALUES (%s, %s, '', 'froe', '', '', '', NULL, '', '')
                    """,
                    ("PLT-FOREIGN", "Foreign plant"),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    """,
                    ("PLT-FOREIGN", int(user["id"]), other_garden_id),
                )
                conn.execute(
                    "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("X1", "X", "Other", 1, 99, 99, "", "", None),
                )
                conn.execute(
                    """
                    INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    """,
                    ("X1", int(user["id"]), other_garden_id),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                "editor_scope",
                "editorpass",
                garden_id=default_garden_id,
            )

            create_foreign_plant = client.post(
                "/api/procurement",
                headers=headers,
                json={
                    "label": "Scoped plant",
                    "inventory_type": "seed",
                    "linked_plt_id": "PLT-FOREIGN",
                },
            )
            self.assertEqual(create_foreign_plant.status_code, 404)

            create_foreign_plot = client.post(
                "/api/procurement",
                headers=headers,
                json={
                    "label": "Scoped plot",
                    "inventory_type": "seed",
                    "linked_plot_id": "X1",
                },
            )
            self.assertEqual(create_foreign_plot.status_code, 404)

            created = client.post(
                "/api/procurement",
                headers=headers,
                json={
                    "label": "Default garden item",
                    "inventory_type": "seed",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            item_id = created.json()["id"]

            update_foreign_plant = client.patch(
                f"/api/procurement/{item_id}",
                headers=headers,
                json={"linked_plt_id": "PLT-FOREIGN"},
            )
            self.assertEqual(update_foreign_plant.status_code, 404)

            update_foreign_plot = client.patch(
                f"/api/procurement/{item_id}",
                headers=headers,
                json={"linked_plot_id": "X1"},
            )
            self.assertEqual(update_foreign_plot.status_code, 404)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
