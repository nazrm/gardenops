import os

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
        self.assertEqual(data["quantity"], 0)

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
        self.assertEqual(resp.json()["quantity"], 7)

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
