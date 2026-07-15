import csv
import io
import os
import unittest
from datetime import date
from unittest.mock import patch

import gardenops.db as db
from gardenops.routers.exports import (
    HARVEST_COLUMNS,
    ISSUE_COLUMNS,
    JOURNAL_COLUMNS,
    PROCUREMENT_COLUMNS,
    TASK_COLUMNS,
    _csv_response,
)
from tests.base import BaseApiTest


class TestExportCsvHelpers(unittest.TestCase):
    def test_csv_response_sanitizes_formula_values(self) -> None:
        response = _csv_response(
            rows=[{"name": "=SUM(1,1)", "notes": None}],
            columns=["name", "notes"],
            filename="test.csv",
        )
        rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
        self.assertEqual(rows, [{"name": "'=SUM(1,1)", "notes": ""}])

    def test_csv_response_sanitizes_formula_after_leading_spaces(self) -> None:
        response = _csv_response(
            rows=[{"name": "  =SUM(1,1)"}],
            columns=["name"],
            filename="test.csv",
        )
        rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
        self.assertEqual(rows, [{"name": "'  =SUM(1,1)"}])


class TestExportsApi(BaseApiTest):
    def _assert_public_export_shape(self, row: dict, columns: list[str]) -> None:
        self.assertEqual(set(row), set(columns))
        self.assertIsInstance(row["id"], str)
        self.assertFalse(row["id"].isdigit())
        for internal_key in (
            "garden_id",
            "public_id",
            "metadata_json",
            "actor_user_id",
            "created_by_user_id",
            "completed_by_user_id",
            "resolved_by_user_id",
        ):
            self.assertNotIn(internal_key, row)

    def test_export_plants_csv(self) -> None:
        conn = db.get_db()
        conn.execute(
            "INSERT INTO plants (plt_id, name, category) VALUES (%s, %s, %s)",
            ("test-export-1", "Test Rose", "busker"),
        )
        conn.commit()
        db.return_db(conn)

        resp = self.client.get("/api/exports/plants?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn("plt_id", resp.text)
        self.assertIn("Test Rose", resp.text)

    def test_export_plants_csv_sanitizes_formula_values(self) -> None:
        conn = db.get_db()
        conn.execute(
            "INSERT INTO plants (plt_id, name, category) VALUES (%s, %s, %s)",
            ("test-export-formula", "=SUM(1,1)", "busker"),
        )
        conn.commit()
        db.return_db(conn)

        resp = self.client.get("/api/exports/plants?format=csv")
        self.assertEqual(resp.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        exported = next(row for row in rows if row["plt_id"] == "test-export-formula")
        self.assertEqual(exported["name"], "'=SUM(1,1)")

    def test_export_plants_json(self) -> None:
        resp = self.client.get("/api/exports/plants?format=json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("plants", data)

    def test_html_exports_are_html_and_escape_resource_values(self) -> None:
        issue = self.client.post(
            "/api/issues",
            json={
                "issue_type": "pest",
                "title": "<script>alert(1)</script>",
                "severity": "normal",
            },
        )
        self.assertEqual(issue.status_code, 201, issue.text)

        for resource in (
            "plants",
            "tasks",
            "journal",
            "harvest",
            "inventory",
            "issues",
            "procurement",
            "seasonal-summary",
        ):
            with self.subTest(resource=resource):
                resp = self.client.get(f"/api/exports/{resource}?format=html")
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertIn("text/html", resp.headers["content-type"])
                self.assertTrue(resp.text.startswith("<!DOCTYPE html>"))

        issues_html = self.client.get("/api/exports/issues?format=html").text
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", issues_html)
        self.assertNotIn("<script>alert(1)</script>", issues_html)

    def test_exports_reject_unsupported_formats(self) -> None:
        for path in (
            "/api/exports/plants?format=zip",
            "/api/exports/plants?format=ics",
            "/api/exports/seasonal-summary?format=csv",
        ):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 422, resp.text)

    def test_exports_reject_invalid_or_reversed_dates(self) -> None:
        reversed_range = self.client.get(
            "/api/exports/journal?format=json&date_from=2026-06-30&date_to=2026-06-01",
        )
        self.assertEqual(reversed_range.status_code, 422, reversed_range.text)
        invalid_year = self.client.get("/api/exports/harvest?format=json&year=0")
        self.assertEqual(invalid_year.status_code, 422, invalid_year.text)

    def test_export_tasks_csv(self) -> None:
        resp = self.client.get("/api/exports/tasks?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn("task_type", resp.text)

    def test_export_journal_csv(self) -> None:
        resp = self.client.get("/api/exports/journal?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn("event_type", resp.text)

    def test_export_harvest_csv(self) -> None:
        resp = self.client.get("/api/exports/harvest?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])

    def test_export_issues_csv(self) -> None:
        resp = self.client.get("/api/exports/issues?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])

    def test_export_inventory_csv_includes_procurement_snapshot(self) -> None:
        create_item = self.client.post(
            "/api/inventory",
            json={
                "plt_id": "PLT-TEST",
                "label": "Test bulbs",
                "inventory_type": "bulb",
                "unit": "pieces",
            },
        )
        self.assertEqual(create_item.status_code, 201, create_item.text)
        item_id = create_item.json()["id"]

        tx_resp = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": 4,
                "reason": "purchased",
                "source_name": "Seed Co",
                "cost_minor": 4900,
                "occurred_on": "2026-03-15",
                "storage_location": "shed",
                "notes": "Fresh stock",
            },
        )
        self.assertEqual(tx_resp.status_code, 201, tx_resp.text)

        create_procurement = self.client.post(
            "/api/procurement",
            json={
                "label": "Test bulbs",
                "inventory_type": "bulb",
                "linked_plt_id": "PLT-TEST",
                "vendor_name": "Seed Co",
                "status": "shipped",
                "cost_minor": 4900,
                "currency": "NOK",
                "quantity": 4,
                "unit": "pieces",
            },
        )
        self.assertEqual(create_procurement.status_code, 201, create_procurement.text)
        procurement_id = create_procurement.json()["id"]

        transition = self.client.post(
            f"/api/procurement/{procurement_id}/transition",
            json={"to_status": "received", "received_on": "2026-03-15"},
        )
        self.assertEqual(transition.status_code, 200, transition.text)

        resp = self.client.get("/api/exports/inventory?format=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.headers["content-type"])
        self.assertIn("recent_vendor_name", resp.text)
        self.assertIn("Seed Co", resp.text)
        self.assertIn("Test bulbs", resp.text)

    def test_export_inventory_respects_query_garden_context(self) -> None:
        conn = db.get_db()
        default_garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert default_garden is not None
        default_garden_id = int(default_garden["id"])
        second_garden = conn.execute(
            "INSERT INTO gardens (slug, name) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
            ("exports-alt", "Alternate Export Garden"),
        )
        second_garden_id = second_garden.fetchone()["id"]
        now_ms = db.current_timestamp_ms()
        conn.execute(
            """
            INSERT INTO inventory_items (
                garden_id, plt_id, label, inventory_type, unit, created_at_ms
            ) VALUES (%s, NULL, %s, 'seed', 'packs', %s)
            """,
            (default_garden_id, "Default Garden Seeds", now_ms),
        )
        conn.execute(
            """
            INSERT INTO inventory_items (
                garden_id, plt_id, label, inventory_type, unit, created_at_ms
            ) VALUES (%s, NULL, %s, 'seed', 'packs', %s)
            """,
            (second_garden_id, "Alternate Garden Seeds", now_ms),
        )
        conn.commit()
        db.return_db(conn)

        default_resp = self.client.get("/api/exports/inventory?format=json")
        self.assertEqual(default_resp.status_code, 200)
        default_items = default_resp.json()["inventory"]
        self.assertTrue(any(item["label"] == "Default Garden Seeds" for item in default_items))
        self.assertFalse(any(item["label"] == "Alternate Garden Seeds" for item in default_items))

        scoped_resp = self.client.get(
            f"/api/exports/inventory?format=json&garden_id={second_garden_id}",
        )
        self.assertEqual(scoped_resp.status_code, 200)
        scoped_items = scoped_resp.json()["inventory"]
        self.assertTrue(any(item["label"] == "Alternate Garden Seeds" for item in scoped_items))
        self.assertFalse(any(item["label"] == "Default Garden Seeds" for item in scoped_items))

    def test_viewer_can_read_public_exports(self) -> None:
        self._create_test_user("exports_viewer", "exportsviewerpass", role="viewer")
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session("exports_viewer", "exportsviewerpass", client=client)
            headers = self._session_headers(csrf)
            for path in (
                "/api/exports/plants?format=json",
                "/api/exports/tasks?format=csv",
                "/api/exports/issues?format=html",
                "/api/exports/seasonal-summary?format=json",
            ):
                with self.subTest(path=path):
                    resp = client.get(path, headers=headers)
                    self.assertEqual(resp.status_code, 200, resp.text)

    def test_task_export_omits_foreign_garden_links(self) -> None:
        task = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Scoped export task",
                "due_on": "2026-06-10",
            },
        )
        self.assertEqual(task.status_code, 201, task.text)

        conn = db.get_db()
        try:
            second_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("exports-links-foreign", "Foreign Link Garden"),
                ).fetchone()["id"]
            )
            conn.execute(
                "INSERT INTO plants (plt_id, name, category) VALUES (%s, %s, %s)",
                ("PLT-EXPORT-FOREIGN", "Foreign export plant", "other"),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("PLT-EXPORT-FOREIGN", self._owner_id, second_garden_id),
            )
            task_row = conn.execute(
                "SELECT id FROM garden_tasks WHERE public_id = %s",
                (task.json()["id"],),
            ).fetchone()
            assert task_row is not None
            conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (int(task_row["id"]), "PLT-EXPORT-FOREIGN"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        rows = self.client.get("/api/exports/tasks?format=json").json()["tasks"]
        exported = next(row for row in rows if row["title"] == "Scoped export task")
        self.assertEqual(exported["plant_ids"], "")

    def test_seasonal_summary_excludes_foreign_garden_bloom_data(self) -> None:
        conn = db.get_db()
        try:
            second_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("exports-bloom-foreign", "Foreign Bloom Garden"),
                ).fetchone()["id"]
            )
            conn.execute(
                """
                INSERT INTO plants (plt_id, name, category, bloom_month)
                VALUES (%s, %s, %s, %s)
                """,
                ("PLT-BLOOM-FOREIGN", "Foreign bloom secret", "other", "1"),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("PLT-BLOOM-FOREIGN", self._owner_id, second_garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/exports/seasonal-summary?format=json")
        self.assertEqual(response.status_code, 200, response.text)
        names = {name for month in response.json()["bloom_calendar"] for name in month["plants"]}
        self.assertNotIn("Foreign bloom secret", names)

    def test_export_procurement_json_with_status_filter(self) -> None:
        create_procurement = self.client.post(
            "/api/procurement",
            json={
                "label": "Tray inserts",
                "inventory_type": "other",
                "vendor_name": "Garden Shop",
                "status": "wanted",
                "cost_minor": 1200,
                "currency": "NOK",
                "quantity": 2,
                "unit": "packs",
            },
        )
        self.assertEqual(create_procurement.status_code, 201, create_procurement.text)

        resp = self.client.get("/api/exports/procurement?format=json&status=wanted")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("procurement", data)
        self.assertTrue(any(item["label"] == "Tray inserts" for item in data["procurement"]))

    def test_export_seasonal_summary(self) -> None:
        resp = self.client.get("/api/exports/seasonal-summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("bloom_calendar", data)
        self.assertIn("task_summary", data)
        self.assertIn("harvest_summary", data)
        self.assertIn("issue_summary", data)

    def test_export_tasks_with_status_filter(self) -> None:
        resp = self.client.get("/api/exports/tasks?format=json&status=pending")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("tasks", data)

    def test_json_exports_use_public_serializers(self) -> None:
        task = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Serializer task",
                "due_on": "2026-06-10",
            },
        )
        self.assertEqual(task.status_code, 201, task.text)
        journal = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-06-10",
                "title": "Serializer journal",
                "notes": "Exportable note",
            },
        )
        self.assertEqual(journal.status_code, 201, journal.text)
        harvest = self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-10",
                "quantity": 1,
                "unit": "kg",
                "quality": "good",
                "notes": "Serializer harvest",
            },
        )
        self.assertEqual(harvest.status_code, 201, harvest.text)
        issue = self.client.post(
            "/api/issues",
            json={
                "issue_type": "pest",
                "title": "Serializer issue",
                "severity": "normal",
            },
        )
        self.assertEqual(issue.status_code, 201, issue.text)
        procurement = self.client.post(
            "/api/procurement",
            json={
                "label": "Serializer tray",
                "inventory_type": "other",
                "vendor_name": "Garden Shop",
                "status": "wanted",
                "quantity": 2,
                "unit": "packs",
            },
        )
        self.assertEqual(procurement.status_code, 201, procurement.text)

        tasks = self.client.get("/api/exports/tasks?format=json").json()["tasks"]
        task_row = next(row for row in tasks if row["title"] == "Serializer task")
        self._assert_public_export_shape(task_row, TASK_COLUMNS)

        journals = self.client.get("/api/exports/journal?format=json").json()["journal"]
        journal_row = next(row for row in journals if row["title"] == "Serializer journal")
        self._assert_public_export_shape(journal_row, JOURNAL_COLUMNS)

        harvests = self.client.get("/api/exports/harvest?format=json").json()["harvest"]
        harvest_row = next(row for row in harvests if row["notes"] == "Serializer harvest")
        self._assert_public_export_shape(harvest_row, HARVEST_COLUMNS)

        issues = self.client.get("/api/exports/issues?format=json").json()["issues"]
        issue_row = next(row for row in issues if row["title"] == "Serializer issue")
        self._assert_public_export_shape(issue_row, ISSUE_COLUMNS)

        procurements = self.client.get("/api/exports/procurement?format=json").json()["procurement"]
        procurement_row = next(row for row in procurements if row["label"] == "Serializer tray")
        self._assert_public_export_shape(procurement_row, PROCUREMENT_COLUMNS)

    def test_inventory_exports_preserve_decimal_ledger_quantity(self) -> None:
        created = self.client.post(
            "/api/inventory",
            json={"label": "Decimal feed", "inventory_type": "other", "unit": "kg"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        item_id = created.json()["id"]
        transaction = self.client.post(
            f"/api/inventory/{item_id}/transactions",
            json={
                "delta": 1.375,
                "occurred_on": "2026-06-10",
                "reason": "purchased",
            },
        )
        self.assertEqual(transaction.status_code, 201, transaction.text)

        json_rows = self.client.get("/api/exports/inventory?format=json").json()["inventory"]
        json_row = next(row for row in json_rows if row["id"] == item_id)
        self.assertEqual(json_row["quantity"], 1.375)

        csv_rows = list(
            csv.DictReader(io.StringIO(self.client.get("/api/exports/inventory?format=csv").text))
        )
        csv_row = next(row for row in csv_rows if row["id"] == item_id)
        self.assertEqual(csv_row["quantity"], "1.375")

    def test_csv_and_json_task_exports_share_public_structure_without_metadata(self) -> None:
        task = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Structure task",
                "due_on": "2026-06-10",
            },
        )
        self.assertEqual(task.status_code, 201, task.text)
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE garden_tasks SET metadata_json = %s WHERE public_id = %s",
                (
                    '{"internal_path":"/srv/gardenops/private","token":"secret-value"}',
                    task.json()["id"],
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        json_response = self.client.get("/api/exports/tasks?format=json")
        csv_response = self.client.get("/api/exports/tasks?format=csv")
        json_row = next(
            row for row in json_response.json()["tasks"] if row["title"] == "Structure task"
        )
        csv_row = next(
            row
            for row in csv.DictReader(io.StringIO(csv_response.text))
            if row["title"] == "Structure task"
        )
        self.assertEqual(list(json_row), TASK_COLUMNS)
        self.assertEqual(list(csv_row), TASK_COLUMNS)
        self.assertIsInstance(json_row["due_on"], str)
        for response_text in (json_response.text, csv_response.text):
            self.assertNotIn("/srv/gardenops/private", response_text)
            self.assertNotIn("secret-value", response_text)

    def test_export_plants_respects_presence_and_focus_filters(self) -> None:
        self.client.post("/api/plots/B1/plants/PLT-TEST", json={"quantity": 1})
        self.client.post("/api/plots/B2/plants/PLT-002", json={"quantity": 1})
        bloom_resp = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": date.today().isoformat(),
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(bloom_resp.status_code, 201, bloom_resp.text)

        resp = self.client.get(
            "/api/exports/plants?format=json&presence=unobserved&plt_ids=PLT-TEST,PLT-002",
        )
        self.assertEqual(resp.status_code, 200)
        plant_ids = {item["plt_id"] for item in resp.json()["plants"]}
        self.assertEqual(plant_ids, {"PLT-002"})

    def test_export_journal_respects_event_type_query_and_date_filters(self) -> None:
        first = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": "2026-06-15",
                "title": "Target bloom",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/api/journal",
            json={
                "event_type": "watered",
                "occurred_on": "2026-06-16",
                "title": "Ignore watering",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(second.status_code, 201, second.text)

        resp = self.client.get(
            "/api/exports/journal?format=json&event_type=bloomed&q=Target&date_from=2026-06-15&date_to=2026-06-15",
        )
        self.assertEqual(resp.status_code, 200)
        entries = resp.json()["journal"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event_type"], "bloomed")
        self.assertEqual(entries[0]["title"], "Target bloom")

    def test_export_issues_with_status_filter(self) -> None:
        resp = self.client.get("/api/exports/issues?format=json&status=open")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("issues", data)

    def test_export_issues_respects_type_and_severity_filters(self) -> None:
        pest = self.client.post(
            "/api/issues",
            json={
                "issue_type": "pest",
                "title": "Target pest issue",
                "severity": "high",
            },
        )
        self.assertEqual(pest.status_code, 201, pest.text)
        disease = self.client.post(
            "/api/issues",
            json={
                "issue_type": "disease",
                "title": "Ignore disease issue",
                "severity": "low",
            },
        )
        self.assertEqual(disease.status_code, 201, disease.text)

        resp = self.client.get(
            "/api/exports/issues?format=json&status=open&issue_type=pest&severity=high",
        )
        self.assertEqual(resp.status_code, 200)
        issues = resp.json()["issues"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["title"], "Target pest issue")

    def test_export_harvest_with_year_filter(self) -> None:
        resp = self.client.get("/api/exports/harvest?format=json&year=2026")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("harvest", data)

    def test_export_harvest_respects_quality_and_date_filters(self) -> None:
        first = self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-06-05",
                "quantity": 1.0,
                "unit": "kg",
                "quality": "excellent",
            },
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-07-05",
                "quantity": 2.0,
                "unit": "kg",
                "quality": "good",
            },
        )
        self.assertEqual(second.status_code, 201, second.text)

        resp = self.client.get(
            "/api/exports/harvest?format=json&quality=excellent&date_from=2026-06-01&date_to=2026-06-30",
        )
        self.assertEqual(resp.status_code, 200)
        harvest = resp.json()["harvest"]
        self.assertEqual(len(harvest), 1)
        self.assertEqual(harvest[0]["quality"], "excellent")
        self.assertEqual(harvest[0]["occurred_on"], "2026-06-05")

    def test_export_seasonal_summary_respects_zone_code(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                ("P1", garden_id, "P", "Plen", 1, 2, 1, "", "", None),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                ("P1", self._owner_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        first = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Bed task",
                "due_on": "2026-06-10",
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Lawn task",
                "due_on": "2026-06-10",
                "plot_ids": ["P1"],
            },
        )
        self.assertEqual(second.status_code, 201, second.text)

        resp = self.client.get("/api/exports/seasonal-summary?zone_code=P")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertEqual(summary["task_summary"], {"pending": 1})

    def test_export_csv_content_disposition(self) -> None:
        resp = self.client.get("/api/exports/plants?format=csv")
        self.assertEqual(resp.status_code, 200)
        disposition = resp.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("gardenops-plants-", disposition)
        self.assertIn(".csv", disposition)
