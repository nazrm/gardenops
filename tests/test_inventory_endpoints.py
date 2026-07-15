import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import gardenops.db as db
from tests.base import BaseApiTest


class TestInventoryItemCrud(BaseApiTest):
    """Tests for single-item GET, PATCH, DELETE on /api/inventory/{item_id}."""

    def _create_inventory_item(
        self,
        *,
        label: str = "Test Seeds",
        inventory_type: str = "seed",
        plt_id: str | None = None,
    ) -> str:
        body: dict = {
            "label": label,
            "inventory_type": inventory_type,
            "unit": "pcs",
        }
        if plt_id:
            body["plt_id"] = plt_id
        resp = self.client.post("/api/inventory", json=body)
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    def test_get_single_item(self) -> None:
        item_id = self._create_inventory_item()
        resp = self.client.get(f"/api/inventory/{item_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], item_id)
        self.assertEqual(data["label"], "Test Seeds")
        self.assertEqual(data["inventory_type"], "seed")
        self.assertEqual(data["unit"], "pcs")
        self.assertEqual(data["quantity"], "0")

    def test_get_nonexistent_item_404(self) -> None:
        resp = self.client.get("/api/inventory/99999")
        self.assertEqual(resp.status_code, 404)

    def test_update_item_label(self) -> None:
        item_id = self._create_inventory_item()
        resp = self.client.patch(
            f"/api/inventory/{item_id}",
            json={"label": "Updated Label"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        resp = self.client.get(f"/api/inventory/{item_id}")
        self.assertEqual(resp.json()["label"], "Updated Label")

    def test_update_item_type(self) -> None:
        item_id = self._create_inventory_item()
        resp = self.client.patch(
            f"/api/inventory/{item_id}",
            json={"inventory_type": "bulb"},
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/api/inventory/{item_id}")
        self.assertEqual(resp.json()["inventory_type"], "bulb")

    def test_update_item_no_fields(self) -> None:
        item_id = self._create_inventory_item()
        resp = self.client.patch(
            f"/api/inventory/{item_id}",
            json={},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_delete_item(self) -> None:
        item_id = self._create_inventory_item()
        resp = self.client.delete(f"/api/inventory/{item_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        resp = self.client.get(f"/api/inventory/{item_id}")
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_item_404(self) -> None:
        resp = self.client.delete("/api/inventory/99999")
        self.assertEqual(resp.status_code, 404)

    def test_delete_rejects_ledger_history_after_contending_mutation_commits(self) -> None:
        item_id = self._create_inventory_item()
        conn = db.get_db()
        try:
            item = conn.execute(
                """
                SELECT id, garden_id FROM inventory_items
                WHERE public_id = %s
                FOR UPDATE
                """,
                (item_id,),
            ).fetchone()
            assert item is not None

            with ThreadPoolExecutor(max_workers=1) as pool:
                pending_delete = pool.submit(
                    lambda: self._new_client().delete(f"/api/inventory/{item_id}"),
                )
                conn.execute(
                    """
                    INSERT INTO inventory_transactions
                        (item_id, garden_id, delta, reason, occurred_on, created_at_ms)
                    VALUES (%s, %s, %s, 'adjusted', '2026-03-15', 1)
                    """,
                    (int(item["id"]), int(item["garden_id"]), Decimal("0.125")),
                )
                conn.commit()
                deleted = pending_delete.result(timeout=5)

            self.assertEqual(deleted.status_code, 409, deleted.text)
            self.assertIn("ledger history", deleted.json()["detail"])
            ledger = self.client.get(f"/api/inventory/{item_id}/transactions").json()
            self.assertEqual(ledger["total"], 1)
            self.assertEqual(ledger["transactions"][0]["delta"], "0.125")
        finally:
            db.return_db(conn)


class TestInventoryTransactions(BaseApiTest):
    """Tests for transactions: GET and POST on /api/inventory/{item_id}/transactions."""

    def _create_item(self) -> str:
        resp = self.client.post(
            "/api/inventory",
            json={
                "label": "Tx Test Item",
                "inventory_type": "seed",
                "unit": "pcs",
            },
        )
        self.assertEqual(resp.status_code, 201)
        return resp.json()["id"]

    def test_list_transactions_empty(self) -> None:
        item_id = self._create_item()
        resp = self.client.get(f"/api/inventory/{item_id}/transactions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("transactions", data)
        self.assertIn("total", data)
        self.assertEqual(data["total"], 0)
        self.assertEqual(len(data["transactions"]), 0)

    def test_create_transaction(self) -> None:
        item_id = self._create_item()
        resp = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": 10,
                "reason": "purchased",
                "source_name": "Garden Center",
                "occurred_on": "2026-03-15",
                "notes": "Initial stock",
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("id", data)

    def test_transaction_updates_quantity(self) -> None:
        item_id = self._create_item()
        self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": 10,
                "reason": "purchased",
                "occurred_on": "2026-03-15",
            },
        )
        self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": -3,
                "reason": "sowed",
                "occurred_on": "2026-03-16",
            },
        )

        resp = self.client.get(f"/api/inventory/{item_id}")
        self.assertEqual(resp.json()["quantity"], "7")

    def test_list_transactions_after_adds(self) -> None:
        item_id = self._create_item()
        self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": 5,
                "reason": "purchased",
                "occurred_on": "2026-03-10",
            },
        )
        self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": -2,
                "reason": "sowed",
                "occurred_on": "2026-03-12",
            },
        )

        resp = self.client.get(f"/api/inventory/{item_id}/transactions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["transactions"]), 2)

        for tx in data["transactions"]:
            self.assertIn("id", tx)
            self.assertEqual(tx["item_id"], item_id)
            self.assertIn("delta", tx)
            self.assertIn("reason", tx)
            self.assertIn("occurred_on", tx)
            self.assertIn("created_at_ms", tx)

    def test_transaction_for_nonexistent_item_404(self) -> None:
        resp = self.client.post(
            "/api/inventory/99999/transactions",
            json={
                "delta": 1,
                "occurred_on": "2026-03-15",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_transaction_invalid_date_422(self) -> None:
        item_id = self._create_item()
        resp = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": 1,
                "occurred_on": "not-a-date",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_transaction_rejects_zero_and_insufficient_stock(self) -> None:
        item_id = self._create_item()

        zero = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={"delta": 0, "occurred_on": "2026-03-15"},
        )
        self.assertEqual(zero.status_code, 422, zero.text)

        insufficient = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={"delta": -0.25, "occurred_on": "2026-03-15"},
        )
        self.assertEqual(insufficient.status_code, 409, insufficient.text)
        self.assertEqual(self.client.get(f"/api/inventory/{item_id}").json()["quantity"], "0")

    def test_decimal_transactions_reconcile_with_actor_and_timestamp(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            user = self._create_test_user("inventory_decimal", "editorpass", "editor")
            client, headers = self._authenticated_client("inventory_decimal", "editorpass")
            created = client.post(
                "/api/inventory",
                headers=headers,
                json={"label": "Compost", "inventory_type": "other", "unit": "kg"},
            )
            self.assertEqual(created.status_code, 201, created.text)
            item_id = created.json()["id"]

            for delta in ("2.75", "-0.625"):
                response = client.post(
                    f"/api/inventory/{item_id}/transactions",
                    headers=headers,
                    json={"delta": delta, "reason": "adjusted", "occurred_on": "2026-03-15"},
                )
                self.assertEqual(response.status_code, 201, response.text)

            item = client.get(f"/api/inventory/{item_id}", headers=headers).json()
            ledger = client.get(
                f"/api/inventory/{item_id}/transactions",
                headers=headers,
            ).json()["transactions"]
            self.assertEqual(item["quantity"], "2.125")
            self.assertEqual(
                sum(Decimal(transaction["delta"]) for transaction in ledger),
                Decimal("2.125"),
            )
            self.assertTrue(
                all(transaction["actor_user_id"] == int(user["id"]) for transaction in ledger)
            )
            self.assertTrue(
                all(transaction["actor_username"] == "inventory_decimal" for transaction in ledger)
            )
            self.assertTrue(all(transaction["created_at_ms"] > 0 for transaction in ledger))
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_concurrent_consumption_never_makes_stock_negative(self) -> None:
        item_id = self._create_item()
        seeded = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={"delta": 1, "reason": "purchased", "occurred_on": "2026-03-15"},
        )
        self.assertEqual(seeded.status_code, 201, seeded.text)

        def consume() -> int:
            client = self._new_client()
            response = client.post(
                f"/api/inventory/{item_id}/transactions",
                json={"delta": -1, "reason": "sowed", "occurred_on": "2026-03-16"},
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: consume(), range(2)))

        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.client.get(f"/api/inventory/{item_id}").json()["quantity"], "0")
        ledger = self.client.get(f"/api/inventory/{item_id}/transactions").json()
        self.assertEqual(ledger["total"], 2)

    def test_precision_round_trip_uses_canonical_decimal_strings(self) -> None:
        item_id = self._create_item()
        precise = "12345678901234.123456"
        created = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={"delta": precise, "reason": "purchased", "occurred_on": "2026-03-15"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(self.client.get(f"/api/inventory/{item_id}").json()["quantity"], precise)
        ledger = self.client.get(f"/api/inventory/{item_id}/transactions").json()
        self.assertEqual(ledger["transactions"][0]["delta"], precise)

        conn = db.get_db()
        try:
            stored = conn.execute(
                """
                SELECT t.delta
                FROM inventory_transactions t
                JOIN inventory_items i ON i.id = t.item_id
                WHERE i.public_id = %s
                """,
                (item_id,),
            ).fetchone()
            assert stored is not None
            self.assertEqual(stored["delta"], Decimal(precise))
        finally:
            db.return_db(conn)

        too_many_integer_digits = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": "123456789012345",
                "reason": "purchased",
                "occurred_on": "2026-03-15",
            },
        )
        self.assertEqual(too_many_integer_digits.status_code, 422, too_many_integer_digits.text)


class TestPlantFromStock(BaseApiTest):
    def _seed_linked_stock(self, quantity: str = "1") -> str:
        created = self.client.post(
            "/api/inventory",
            json={
                "label": "Linked seeds",
                "inventory_type": "seed",
                "unit": "pcs",
                "plt_id": "PLT-TEST",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        item_id = created.json()["id"]
        stocked = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={"delta": quantity, "reason": "purchased", "occurred_on": "2026-03-15"},
        )
        self.assertEqual(stocked.status_code, 201, stocked.text)
        return item_id

    @staticmethod
    def _plant_body(quantity: str = "0.75") -> dict[str, str]:
        return {
            "quantity": quantity,
            "plot_id": "B1",
            "occurred_on": "2026-03-16",
            "notes": "Atomic planting",
        }

    def test_command_is_atomic_and_identical_replay_is_idempotent(self) -> None:
        item_id = self._seed_linked_stock()
        operation_id = str(uuid4())
        headers = {"X-Offline-Operation-Id": operation_id}
        first = self.client.post(
            f"/api/inventory/{item_id}/plant",
            headers=headers,
            json=self._plant_body(),
        )
        replay = self.client.post(
            f"/api/inventory/{item_id}/plant",
            headers=headers,
            json=self._plant_body(),
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(self.client.get(f"/api/inventory/{item_id}").json()["quantity"], "0.25")

        journal = self.client.get(f"/api/journal/{first.json()['journal_entry_id']}").json()
        self.assertEqual(journal["plant_ids"], ["PLT-TEST"])
        self.assertEqual(journal["plot_ids"], ["B1"])
        ledger = self.client.get(f"/api/inventory/{item_id}/transactions").json()
        self.assertEqual(ledger["total"], 2)
        self.assertEqual(ledger["transactions"][0]["delta"], "-0.75")

        changed = self.client.post(
            f"/api/inventory/{item_id}/plant",
            headers=headers,
            json=self._plant_body("0.5"),
        )
        self.assertEqual(changed.status_code, 409, changed.text)

        journal_with_same_client_operation_id = self.client.post(
            "/api/journal",
            headers=headers,
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-16",
                "title": "Independent journal operation",
            },
        )
        self.assertEqual(
            journal_with_same_client_operation_id.status_code,
            201,
            journal_with_same_client_operation_id.text,
        )
        self.assertNotEqual(
            journal_with_same_client_operation_id.json()["id"],
            first.json()["journal_entry_id"],
        )

        conn = db.get_db()
        try:
            operation_ids = {
                str(row["operation_id"])
                for row in conn.execute(
                    """
                    SELECT operation_id
                    FROM offline_create_operations
                    WHERE operation_id IN (%s, %s)
                    """,
                    (operation_id, f"inventory-plant:{operation_id}"),
                ).fetchall()
            }
            self.assertEqual(
                operation_ids,
                {operation_id, f"inventory-plant:{operation_id}"},
            )
        finally:
            db.return_db(conn)

    def test_insufficient_stock_rolls_back_all_command_side_effects(self) -> None:
        item_id = self._seed_linked_stock("0.5")
        failed = self.client.post(
            f"/api/inventory/{item_id}/plant",
            headers={"X-Offline-Operation-Id": str(uuid4())},
            json=self._plant_body("0.75"),
        )
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertEqual(self.client.get(f"/api/inventory/{item_id}").json()["quantity"], "0.5")
        ledger = self.client.get(f"/api/inventory/{item_id}/transactions").json()
        self.assertEqual(ledger["total"], 1)
        self.assertEqual(self.client.get("/api/journal?event_type=planted").json()["total"], 0)

        conn = db.get_db()
        try:
            assignment = conn.execute(
                "SELECT 1 FROM plot_plants WHERE plot_id = 'B1' AND plt_id = 'PLT-TEST'",
            ).fetchone()
            self.assertIsNone(assignment)
        finally:
            db.return_db(conn)

    def test_concurrent_commands_cannot_overdraw_stock(self) -> None:
        item_id = self._seed_linked_stock("1")

        def plant() -> int:
            response = self._new_client().post(
                f"/api/inventory/{item_id}/plant",
                headers={"X-Offline-Operation-Id": str(uuid4())},
                json=self._plant_body("0.75"),
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: plant(), range(2)))

        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.client.get(f"/api/inventory/{item_id}").json()["quantity"], "0.25")
        self.assertEqual(self.client.get("/api/journal?event_type=planted").json()["total"], 1)

    def test_viewer_denied_write(self) -> None:
        """Viewer role should get 403 on inventory write operations."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("inv_editor", "editorpass", "editor")
            self._create_test_user("inv_viewer", "viewerpass", "viewer")

            ed_client, ed_h = self._authenticated_client(
                "inv_editor",
                "editorpass",
            )
            resp = ed_client.post(
                "/api/inventory",
                headers=ed_h,
                json={
                    "label": "Auth Test",
                    "inventory_type": "seed",
                    "unit": "pcs",
                },
            )
            self.assertEqual(resp.status_code, 201)
            item_id = resp.json()["id"]

            vw_client, vw_h = self._authenticated_client(
                "inv_viewer",
                "viewerpass",
            )

            resp = vw_client.get(
                f"/api/inventory/{item_id}",
                headers=vw_h,
            )
            self.assertEqual(resp.status_code, 200)

            resp = vw_client.patch(
                f"/api/inventory/{item_id}",
                headers=vw_h,
                json={"label": "Hacked"},
            )
            self.assertEqual(resp.status_code, 403)

            resp = vw_client.delete(
                f"/api/inventory/{item_id}",
                headers=vw_h,
            )
            self.assertEqual(resp.status_code, 403)

            resp = vw_client.post(
                f"/api/inventory/{item_id}/transactions",
                headers=vw_h,
                json={
                    "delta": 1,
                    "occurred_on": "2026-03-15",
                },
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
