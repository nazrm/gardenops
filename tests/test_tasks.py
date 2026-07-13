import os
from datetime import date
from unittest.mock import patch

import gardenops.db as db
from gardenops.db import current_timestamp_ms
from gardenops.rate_limit import acquire_concurrency_slot
from gardenops.security import create_user
from gardenops.services.task_generator import generate_tasks
from tests.base import BaseApiTest, strong_password


class TestTasks(BaseApiTest):
    def test_generated_task_list_removes_stale_sow_for_seed_category_without_instructions(
        self,
    ) -> None:
        create = self.client.post(
            "/api/plants",
            json={
                "plt_id": "TASK-SEED-1",
                "name": "Mørkbladig Mitsuba (frø)",
                "category": "frø",
            },
        )
        self.assertEqual(create.status_code, 201, create.text)

        conn = db.get_db()
        try:
            now_ms = current_timestamp_ms()
            task_row = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, description, status,
                     severity, due_on, rule_source, metadata_json,
                     created_by_user_id, created_at_ms, updated_at_ms)
                VALUES (%s, 'sow', 'Sow: Mørkbladig Mitsuba (frø)', '', 'pending',
                        'normal', '2026-03-01', 'sow:TASK-SEED-1:2026-03', '{}',
                        %s, %s, %s)
                RETURNING id
                """,
                (self._get_default_garden_id(), self._owner_id, now_ms, now_ms),
            ).fetchone()
            conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (int(task_row["id"]), "TASK-SEED-1"),
            )
            generate_tasks(
                conn,
                self._get_default_garden_id(),
                3,
                2026,
                self._owner_id,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/tasks?task_type=sow")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(
                task["title"] == "Sow: Mørkbladig Mitsuba (frø)"
                for task in response.json()["tasks"]
            )
        )

    def test_generated_task_cleanup_keeps_manual_sow_tasks(self) -> None:
        create = self.client.post(
            "/api/plants",
            json={
                "plt_id": "TASK-SEED-2",
                "name": "Manual Sow Plant",
                "category": "frø",
            },
        )
        self.assertEqual(create.status_code, 201, create.text)

        manual = self.client.post(
            "/api/tasks",
            json={
                "task_type": "sow",
                "title": "Sow: Manual Sow Plant",
                "due_on": "2026-03-01",
                "plant_ids": ["TASK-SEED-2"],
            },
        )
        self.assertEqual(manual.status_code, 201, manual.text)

        conn = db.get_db()
        try:
            generate_tasks(
                conn,
                self._get_default_garden_id(),
                3,
                2026,
                self._owner_id,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/tasks?task_type=sow")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(task["title"] == "Sow: Manual Sow Plant" for task in response.json()["tasks"])
        )

    def test_generate_does_not_create_auto_sow_even_when_care_mentions_sowing(self) -> None:
        create = self.client.post(
            "/api/plants",
            json={
                "plt_id": "TASK-SEED-3",
                "name": "Blå bergvalmue (frø)",
                "category": "frø",
            },
        )
        self.assertEqual(create.status_code, 201, create.text)
        update = self.client.patch(
            "/api/plants/TASK-SEED-3",
            json={"care_planting": "Så frø tidlig vår innendørs. Plant ut etter frost."},
        )
        self.assertEqual(update.status_code, 200, update.text)

        conn = db.get_db()
        try:
            generate_tasks(
                conn,
                self._get_default_garden_id(),
                4,
                2026,
                self._owner_id,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/tasks?task_type=sow")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(
                task["rule_source"] == "sow:TASK-SEED-3:2026-04"
                for task in response.json()["tasks"]
            )
        )

    def test_task_crud_lifecycle(self) -> None:
        """Create, read, update, and delete a task."""
        # Create
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Water the roses",
                "description": "Use the hose",
                "severity": "high",
                "due_on": "2026-04-01",
            },
        )
        self.assertEqual(r.status_code, 201)
        task_id = r.json()["id"]

        # Read single
        r = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(r.status_code, 200)
        task = r.json()
        self.assertEqual(task["task_type"], "water")
        self.assertEqual(task["title"], "Water the roses")
        self.assertEqual(task["severity"], "high")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["due_on"], "2026-04-01")
        self.assertIsNone(task["window_start_on"])
        self.assertIsNone(task["window_end_on"])
        self.assertIsNone(task["window_kind"])

        # Update
        r = self.client.patch(
            f"/api/tasks/{task_id}",
            json={
                "title": "Water all roses",
                "severity": "low",
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(r.json()["title"], "Water all roses")
        self.assertEqual(r.json()["severity"], "low")

        # List
        r = self.client.get("/api/tasks")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["total"], 1)

        # Delete
        r = self.client.delete(f"/api/tasks/{task_id}")
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(r.status_code, 404)

    def test_recommended_windows_are_persisted_for_calendar_relevant_tasks(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune espalier apples",
                "due_on": "2026-04-01",
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(task["window_kind"], "recommended")
        self.assertEqual(task["window_start_on"], "2026-03-11")
        self.assertEqual(task["window_end_on"], "2026-04-15")

        response = self.client.patch(
            f"/api/tasks/{task_id}",
            json={"due_on": "2026-04-10"},
        )
        self.assertEqual(response.status_code, 200)
        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(task["window_kind"], "recommended")
        self.assertEqual(task["window_start_on"], "2026-03-20")
        self.assertEqual(task["window_end_on"], "2026-04-24")

        response = self.client.patch(
            f"/api/tasks/{task_id}",
            json={
                "window_start_on": "2026-03-25",
                "window_end_on": "2026-04-18",
            },
        )
        self.assertEqual(response.status_code, 200)
        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(task["window_kind"], "manual")
        self.assertEqual(task["window_start_on"], "2026-03-25")
        self.assertEqual(task["window_end_on"], "2026-04-18")

        response = self.client.patch(
            f"/api/tasks/{task_id}",
            json={"window_start_on": None, "window_end_on": None},
        )
        self.assertEqual(response.status_code, 200)
        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertIsNone(task["window_start_on"])
        self.assertIsNone(task["window_end_on"])
        self.assertIsNone(task["window_kind"])

    def test_task_list_views(self) -> None:
        """View filters (today/week/month/overdue) work correctly."""
        # Create a task due today
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune today",
                "due_on": "2026-03-13",
            },
        )
        self.assertEqual(r.status_code, 201)

        # Create a task due next week
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "sow",
                "title": "Sow next week",
                "due_on": "2026-03-20",
            },
        )
        self.assertEqual(r.status_code, 201)

        # Create a task due far future
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "harvest",
                "title": "Harvest later",
                "due_on": "2027-09-01",
            },
        )
        self.assertEqual(r.status_code, 201)

        # Today view should include due-today tasks
        r = self.client.get("/api/tasks?view=today")
        self.assertEqual(r.status_code, 200)
        today_tasks = r.json()["tasks"]
        today_titles = [t["title"] for t in today_tasks]
        self.assertIn("Prune today", today_titles)

        # Week view should include both near tasks
        r = self.client.get("/api/tasks?view=week")
        self.assertEqual(r.status_code, 200)
        week_tasks = r.json()["tasks"]
        week_titles = [t["title"] for t in week_tasks]
        self.assertIn("Prune today", week_titles)
        self.assertIn("Sow next week", week_titles)

        # Month view should include near tasks
        r = self.client.get("/api/tasks?view=month")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["total"], 2)

        # Overdue: tasks due before today
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Overdue task",
                "due_on": "2026-01-01",
            },
        )
        self.assertEqual(r.status_code, 201)
        r = self.client.get("/api/tasks?view=overdue")
        overdue_titles = [t["title"] for t in r.json()["tasks"]]
        self.assertIn("Overdue task", overdue_titles)

    def test_task_list_uses_frozen_attention_date_for_snoozed_tasks(self) -> None:
        create = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Frozen snoozed task",
                "due_on": "2026-07-05",
            },
        )
        self.assertEqual(create.status_code, 201, create.text)

        snooze = self.client.post(
            f"/api/tasks/{create.json()['id']}/action",
            json={"action": "snooze", "snooze_until": "2026-07-12"},
        )
        self.assertEqual(snooze.status_code, 200, snooze.text)

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783252800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
            clear=False,
        ):
            response = self.client.get("/api/tasks?view=today")

        self.assertEqual(response.status_code, 200, response.text)
        titles = {task["title"] for task in response.json()["tasks"]}
        self.assertNotIn("Frozen snoozed task", titles)

    def test_task_action_views_hide_stale_generated_watering_but_keep_manual(self) -> None:
        from gardenops.sql_dates import offset_days_iso

        garden_id = self._get_default_garden_id()
        yesterday = offset_days_iso(-1)
        now_ms = current_timestamp_ms()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                    ('task_generated_old_water', %s, 'water', 'Generated old water', '',
                     'pending', 'normal', %s, %s, '{}', %s, %s),
                    ('task_generated_old_dry_water', %s, 'water', 'Generated old dry water', '',
                     'pending', 'normal', %s, %s, '{}', %s, %s),
                    ('task_manual_old_water', %s, 'water', 'Manual old water', '',
                     'pending', 'normal', %s, '', '{}', %s, %s)
                """,
                (
                    garden_id,
                    yesterday,
                    f"water:STALE:{yesterday}",
                    now_ms,
                    now_ms,
                    garden_id,
                    yesterday,
                    "auto:dry_water:123:STALE",
                    now_ms,
                    now_ms,
                    garden_id,
                    yesterday,
                    now_ms,
                    now_ms,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        for query in (
            "?task_type=water",
            "?status=pending&task_type=water",
            "?view=today&task_type=water",
            "?view=overdue&task_type=water",
        ):
            response = self.client.get(f"/api/tasks{query}")
            self.assertEqual(response.status_code, 200, response.text)
            titles = {task["title"] for task in response.json()["tasks"]}
            self.assertIn("Manual old water", titles)
            self.assertNotIn("Generated old water", titles)
            self.assertNotIn("Generated old dry water", titles)

    def test_expired_tasks_are_history_not_active_action_items(self) -> None:
        from gardenops.sql_dates import offset_days_iso

        garden_id = self._get_default_garden_id()
        yesterday = offset_days_iso(-1)
        now_ms = current_timestamp_ms()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                    ('task_expired_generated_water_history', %s, 'water',
                     'Expired generated water', '', 'expired', 'normal',
                     %s, %s, '{}', %s, %s),
                    ('task_pending_manual_water_history', %s, 'water',
                     'Pending manual water', '', 'pending', 'normal',
                     %s, '', '{}', %s, %s)
                """,
                (
                    garden_id,
                    yesterday,
                    f"water:HISTORY:{yesterday}",
                    now_ms,
                    now_ms,
                    garden_id,
                    yesterday,
                    now_ms,
                    now_ms,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        history = self.client.get("/api/tasks?status=expired&task_type=water")
        self.assertEqual(history.status_code, 200, history.text)
        history_titles = {task["title"] for task in history.json()["tasks"]}
        self.assertIn("Expired generated water", history_titles)
        self.assertNotIn("Pending manual water", history_titles)

        active = self.client.get("/api/tasks?view=today&task_type=water")
        self.assertEqual(active.status_code, 200, active.text)
        active_titles = {task["title"] for task in active.json()["tasks"]}
        self.assertNotIn("Expired generated water", active_titles)
        self.assertIn("Pending manual water", active_titles)

    def test_grouped_horticultural_completion_requires_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Fertilize two plants",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})

        self.assertEqual(response.status_code, 422)
        self.assertIn("completed_plant_ids", response.text)

    def test_non_horticultural_completion_rejects_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "inspect_issue",
                "title": "Inspect aphids",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("completion capture", response.text)

    def test_completed_non_horticultural_task_rejects_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "inspect_issue",
                "title": "Inspect aphids",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
        self.assertEqual(response.status_code, 200, response.text)

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("completion capture", response.text)

    def test_task_actions(self) -> None:
        """Complete, snooze, skip, and reschedule actions work."""
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Action test",
                "due_on": "2026-05-01",
            },
        )
        self.assertEqual(r.status_code, 201)
        task_id = r.json()["id"]

        # Complete
        r = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={
                "action": "complete",
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(r.json()["status"], "completed")
        self.assertIsNotNone(r.json()["completed_at_ms"])

        # Create another for skip
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Skip test",
                "due_on": "2026-05-01",
            },
        )
        task_id2 = r.json()["id"]
        r = self.client.post(
            f"/api/tasks/{task_id2}/action",
            json={
                "action": "skip",
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id2}")
        self.assertEqual(r.json()["status"], "skipped")

        # Create another for snooze
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Snooze test",
                "due_on": "2026-05-01",
            },
        )
        task_id3 = r.json()["id"]
        r = self.client.post(
            f"/api/tasks/{task_id3}/action",
            json={
                "action": "snooze",
                "snooze_until": "2026-06-01",
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id3}")
        self.assertEqual(r.json()["status"], "snoozed")
        self.assertEqual(r.json()["snoozed_until"], "2026-06-01")

        # Snooze without date should fail
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "No date snooze",
                "due_on": "2026-05-01",
            },
        )
        task_id4 = r.json()["id"]
        r = self.client.post(
            f"/api/tasks/{task_id4}/action",
            json={
                "action": "snooze",
            },
        )
        self.assertEqual(r.status_code, 422)

        # Reschedule
        r = self.client.post(
            f"/api/tasks/{task_id4}/action",
            json={
                "action": "reschedule",
                "reschedule_to": "2026-07-15",
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id4}")
        self.assertEqual(r.json()["due_on"], "2026-07-15")
        self.assertEqual(r.json()["status"], "pending")

        # Recommended window is recomputed on quick reschedule actions.
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Reschedule with window",
                "due_on": "2026-05-01",
            },
        )
        self.assertEqual(r.status_code, 201)
        task_id5 = r.json()["id"]
        before = self.client.get(f"/api/tasks/{task_id5}").json()
        r = self.client.post(
            f"/api/tasks/{task_id5}/action",
            json={
                "action": "reschedule",
                "reschedule_to": "2026-05-15",
            },
        )
        self.assertEqual(r.status_code, 200)
        after = self.client.get(f"/api/tasks/{task_id5}").json()
        self.assertEqual(after["due_on"], "2026-05-15")
        self.assertEqual(after["window_kind"], "recommended")
        self.assertNotEqual(after["window_start_on"], before["window_start_on"])
        self.assertEqual(after["window_start_on"], "2026-04-24")
        self.assertEqual(after["window_end_on"], "2026-05-29")

    def test_task_actions_clear_completion_metadata_when_reopened(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Completed then rescheduled",
                "due_on": "2026-05-01",
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        complete = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
        self.assertEqual(complete.status_code, 200)
        completed = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["completed_at_ms"])

        reschedule = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "reschedule", "reschedule_to": "2026-06-01"},
        )
        self.assertEqual(reschedule.status_code, 200)
        reopened = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(reopened["status"], "pending")
        self.assertIsNone(reopened["completed_at_ms"])
        self.assertIsNone(reopened["completed_by_user_id"])
        self.assertEqual(reopened["window_start_on"], "2026-05-11")
        self.assertEqual(reopened["window_end_on"], "2026-06-15")

    def test_reopened_completion_capture_task_records_new_history(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune rose twice",
                "due_on": "2026-05-01",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        first = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
        self.assertEqual(first.status_code, 200, first.text)
        first_journal = self.client.get("/api/journal?event_type=pruned&plant_id=PLT-002").json()
        self.assertEqual(first_journal["total"], 1)

        reschedule = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "reschedule", "reschedule_to": "2026-06-01"},
        )
        self.assertEqual(reschedule.status_code, 200, reschedule.text)

        second = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
        self.assertEqual(second.status_code, 200, second.text)
        second_journal = self.client.get("/api/journal?event_type=pruned&plant_id=PLT-002").json()
        self.assertEqual(second_journal["total"], 2)

    def test_completing_skipped_task_is_rejected(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "water",
                "title": "Skipped then complete",
                "due_on": "2026-05-01",
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]
        skipped = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "skip"},
        )
        self.assertEqual(skipped.status_code, 200)
        repeated = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
        self.assertEqual(repeated.status_code, 409)

    def test_prune_completion_creates_selected_plant_journal_entry(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
                "plot_ids": ["B2"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "notes": "Removed dead stems"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        journal = self.client.get("/api/journal?event_type=pruned&plant_id=PLT-002").json()
        self.assertEqual(journal["total"], 1)
        entry = journal["entries"][0]
        self.assertEqual(entry["plant_ids"], ["PLT-002"])
        self.assertEqual(entry["metadata"]["source"], "task_completion")
        self.assertEqual(entry["metadata"]["source_task_id"], task_id)
        self.assertEqual(entry["metadata"]["source_task_type"], "prune")
        self.assertIn("Removed dead stems", entry["notes"])

    def test_fertilize_completion_creates_selected_plant_journal_entry(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Feed rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
        self.assertEqual(response.status_code, 200, response.text)

        journal = self.client.get("/api/journal?event_type=fertilized&plant_id=PLT-002").json()
        self.assertEqual(journal["total"], 1)
        self.assertEqual(journal["entries"][0]["metadata"]["source_task_type"], "fertilize")

    def test_grouped_fertilize_partial_completion_keeps_only_remaining_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Fertilize 2 plants",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["plant_ids"], ["PLT-002"])

        done_journal = self.client.get(
            "/api/journal?event_type=fertilized&plant_id=PLT-TEST"
        ).json()
        remaining_journal = self.client.get(
            "/api/journal?event_type=fertilized&plant_id=PLT-002"
        ).json()
        self.assertEqual(done_journal["total"], 1)
        self.assertEqual(remaining_journal["total"], 0)

    def test_grouped_partial_completion_splits_plot_links_by_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Fertilize shared beds",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST", "PLT-002"],
                "plot_ids": ["B1", "B2"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        conn = db.get_db()
        try:
            task = conn.execute(
                "SELECT id FROM garden_tasks WHERE public_id = %s",
                (task_id,),
            ).fetchone()
            self.assertIsNotNone(task)
            foreign_garden = conn.execute(
                "INSERT INTO gardens (slug, name) "
                "VALUES ('plot-split-foreign', 'Foreign') RETURNING id",
            ).fetchone()
            self.assertIsNotNone(foreign_garden)
            conn.execute(
                """
                INSERT INTO plots
                    (plot_id, garden_id, zone_code, zone_name, plot_number,
                     grid_row, grid_col, sub_zone, notes)
                VALUES ('FOREIGN-PLOT', %s, 'F', 'Foreign', 1, 1, 1, '', '')
                """,
                (int(foreign_garden["id"]),),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES
                    ('B1', 'PLT-TEST', 1),
                    ('B2', 'PLT-TEST', 1),
                    ('B2', 'PLT-002', 1),
                    ('FOREIGN-PLOT', 'PLT-TEST', 1)
                """,
            )
            conn.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'FOREIGN-PLOT')",
                (int(task["id"]),),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

        task = self.client.get(f"/api/tasks/{task_id}").json()
        journal = self.client.get("/api/journal?event_type=fertilized&plant_id=PLT-TEST").json()
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["plant_ids"], ["PLT-002"])
        self.assertEqual(task["plot_ids"], ["B2"])
        self.assertEqual(journal["total"], 1)
        self.assertEqual(sorted(journal["entries"][0]["plot_ids"]), ["B1", "B2"])

    def test_task_completion_capture_is_idempotent_for_same_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune 2 plants",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]
        body = {"action": "complete", "completed_plant_ids": ["PLT-TEST"]}

        self.assertEqual(
            self.client.post(f"/api/tasks/{task_id}/action", json=body).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/api/tasks/{task_id}/action", json=body).status_code,
            200,
        )

        journal = self.client.get("/api/journal?event_type=pruned&plant_id=PLT-TEST").json()
        self.assertEqual(journal["total"], 1)

    def test_horticultural_completion_rejects_unlinked_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune missing link",
                "due_on": "2026-06-01",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete", "completed_plant_ids": ["PLT-002"]},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("linked to the task", response.text)

    def test_non_bloom_completion_rejects_not_seen_bloom_outcome(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "fertilize",
                "title": "Feed rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={
                "action": "complete",
                "completion_outcome": "not_seen_blooming_this_season",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("observe_bloom", response.text)

    def test_observe_bloom_policy_snooze_records_not_yet_evidence(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "snooze", "snooze_until": "2026-06-08"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        task = self.client.get(f"/api/tasks/{task_id}").json()
        events = task["metadata"]["bloom_observation"]["not_yet_events"]
        self.assertEqual(events[0]["new_snooze_date"], "2026-06-08")
        self.assertEqual(events[0]["source"], "task_snooze_policy")

    def test_observe_bloom_not_seen_this_season_records_observed_without_presence_change(
        self,
    ) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={
                "action": "complete",
                "completion_outcome": "not_seen_blooming_this_season",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        journal = self.client.get("/api/journal?event_type=observed&plant_id=PLT-002").json()
        self.assertEqual(journal["total"], 1)
        self.assertEqual(
            journal["entries"][0]["metadata"]["outcome"],
            "not_seen_blooming_this_season",
        )
        plants = {plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Rose").json()}
        self.assertNotEqual(plants["PLT-002"]["presence_status"], "gone")
        self.assertFalse(plants["PLT-002"]["bloomed_this_year"])
        self.assertNotEqual(plants["PLT-002"]["seen_growing"], False)

    def test_observe_bloom_completion_creates_plant_level_journal_entry(self) -> None:
        assign = self.client.post("/api/plots/B1/plants/PLT-TEST", json={"quantity": 1})
        self.assertEqual(assign.status_code, 201)
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Test plant",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete"},
        )
        self.assertEqual(response.status_code, 200)

        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(task["status"], "completed")

        journal = self.client.get("/api/journal?event_type=bloomed&plant_id=PLT-TEST").json()
        self.assertEqual(journal["total"], 1)
        entry = journal["entries"][0]
        self.assertEqual(entry["occurred_on"], date.today().isoformat())
        self.assertEqual(entry["plant_ids"], ["PLT-TEST"])
        self.assertEqual(entry["plot_ids"], [])
        self.assertEqual(entry["metadata"]["source"], "task_completion")
        self.assertEqual(entry["metadata"]["source_task_id"], task_id)
        self.assertEqual(entry["metadata"]["source_task_type"], "observe_bloom")
        self.assertEqual(task["metadata"]["completion_journal_entry_id"], entry["id"])
        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Test Plant").json()
        }
        self.assertTrue(plants["PLT-TEST"]["seen_growing"])
        self.assertEqual(plants["PLT-TEST"]["seen_growing_date"], date.today().isoformat())
        self.assertEqual(plants["PLT-TEST"]["seen_growing_year"], date.today().year)
        self.assertTrue(plants["PLT-TEST"]["seen_growing_is_current_year"])
        self.assertEqual(plants["PLT-TEST"]["last_bloomed_on"], date.today().isoformat())
        self.assertEqual(plants["PLT-TEST"]["last_bloomed_year"], date.today().year)
        self.assertTrue(plants["PLT-TEST"]["bloomed_this_year"])
        self.assertEqual(plants["PLT-TEST"]["presence_status"], "present")
        assignments = self.client.get("/api/plants/PLT-TEST/assignments").json()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["plot_id"], "B1")
        self.assertTrue(assignments[0]["seen_growing"])
        self.assertEqual(assignments[0]["seen_growing_date"], date.today().isoformat())
        self.assertEqual(assignments[0]["seen_growing_year"], date.today().year)
        self.assertTrue(assignments[0]["seen_growing_is_current_year"])

    def test_observe_bloom_completion_is_idempotent(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
                "plot_ids": ["B2"],
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        for _ in range(2):
            response = self.client.post(
                f"/api/tasks/{task_id}/action",
                json={"action": "complete"},
            )
            self.assertEqual(response.status_code, 200)

        journal = self.client.get("/api/journal?event_type=bloomed&plant_id=PLT-002").json()
        self.assertEqual(journal["total"], 1)
        self.assertEqual(journal["entries"][0]["plot_ids"], [])

    def test_observe_bloom_completion_without_plot_context_marks_single_assignment_seen_growing(
        self,
    ) -> None:
        assign = self.client.post("/api/plots/B2/plants/PLT-002", json={"quantity": 1})
        self.assertEqual(assign.status_code, 201)

        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Rose",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete"},
        )
        self.assertEqual(response.status_code, 200)

        assignments = self.client.get("/api/plants/PLT-002/assignments").json()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["plot_id"], "B2")
        self.assertTrue(assignments[0]["seen_growing"])
        self.assertEqual(assignments[0]["seen_growing_date"], date.today().isoformat())
        plants = {plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Rose").json()}
        self.assertTrue(plants["PLT-002"]["seen_growing"])
        self.assertEqual(plants["PLT-002"]["seen_growing_date"], date.today().isoformat())
        self.assertEqual(plants["PLT-002"]["presence_status"], "present")

    def test_observe_bloom_task_rejects_peer_owned_plant_side_effect(self) -> None:
        owner = self._create_test_user("task_peer_owner", "ownerpass", "editor")
        self._create_test_user("task_peer_editor", "editorpass", "editor")
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE plant_ownership
                SET owner_user_id = %s
                WHERE plt_id = 'PLT-002' AND garden_id = %s
                """,
                (int(owner["id"]), garden_id),
            )
            conn.execute(
                """
                UPDATE plants
                SET seen_growing = NULL, seen_growing_date = NULL
                WHERE plt_id = 'PLT-002'
                """
            )
            conn.commit()
        finally:
            db.return_db(conn)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session("task_peer_editor", "editorpass", client=client)
            response = client.post(
                "/api/tasks",
                headers=self._session_headers(csrf, garden_id=garden_id),
                json={
                    "task_type": "observe_bloom",
                    "title": "Observe bloom: peer plant",
                    "due_on": "2026-06-01",
                    "plant_ids": ["PLT-002"],
                },
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

        self.assertEqual(response.status_code, 404, response.text)
        conn = db.get_db()
        try:
            plant = conn.execute(
                "SELECT seen_growing, seen_growing_date FROM plants WHERE plt_id = 'PLT-002'",
            ).fetchone()
        finally:
            db.return_db(conn)
        assert plant is not None
        self.assertIsNone(plant["seen_growing"])
        self.assertIsNone(plant["seen_growing_date"])

    def test_observe_bloom_task_type_update_rejects_peer_owned_existing_plant(self) -> None:
        owner = self._create_test_user("task_update_peer_owner", "ownerpass", "editor")
        self._create_test_user("task_update_peer_editor", "editorpass", "editor")
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE plant_ownership
                SET owner_user_id = %s
                WHERE plt_id = 'PLT-002' AND garden_id = %s
                """,
                (int(owner["id"]), garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session("task_update_peer_editor", "editorpass", client=client)
            create = client.post(
                "/api/tasks",
                headers=self._session_headers(csrf, garden_id=garden_id),
                json={
                    "task_type": "water",
                    "title": "Water peer plant",
                    "due_on": "2026-06-01",
                    "plant_ids": ["PLT-002"],
                },
            )
            self.assertEqual(create.status_code, 201, create.text)
            task_id = create.json()["id"]

            update = client.patch(
                f"/api/tasks/{task_id}",
                headers=self._session_headers(csrf, garden_id=garden_id),
                json={"task_type": "observe_bloom"},
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

        self.assertEqual(update.status_code, 404, update.text)
        task = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertEqual(task["task_type"], "water")

    def test_observe_bloom_completion_without_plot_context_does_not_guess_multiple_assignments(
        self,
    ) -> None:
        create = self.client.post(
            "/api/plants",
            json={"plt_id": "BL-AMBIG", "name": "Ambiguous Bloom", "category": "frø"},
        )
        self.assertEqual(create.status_code, 201)
        self.assertEqual(
            self.client.post("/api/plots/B1/plants/BL-AMBIG", json={"quantity": 1}).status_code,
            201,
        )
        self.assertEqual(
            self.client.post("/api/plots/B2/plants/BL-AMBIG", json={"quantity": 1}).status_code,
            201,
        )

        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Ambiguous Bloom",
                "due_on": "2026-06-01",
                "plant_ids": ["BL-AMBIG"],
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete"},
        )
        self.assertEqual(response.status_code, 200)

        assignments = self.client.get("/api/plants/BL-AMBIG/assignments").json()
        self.assertEqual(len(assignments), 2)
        self.assertTrue(all(item["seen_growing"] is None for item in assignments))
        self.assertTrue(all(item["seen_growing_date"] is None for item in assignments))
        plants = {
            plant["plt_id"]: plant
            for plant in self.client.get("/api/plants?q=Ambiguous Bloom").json()
        }
        self.assertTrue(plants["BL-AMBIG"]["seen_growing"])
        self.assertEqual(plants["BL-AMBIG"]["seen_growing_date"], date.today().isoformat())
        self.assertEqual(plants["BL-AMBIG"]["presence_status"], "present")

    def test_observe_bloom_completion_keeps_multi_plot_not_seen_history_but_marks_plant_present(
        self,
    ) -> None:
        current_year = date.today().year
        previous_year = current_year - 1
        create = self.client.post(
            "/api/plants",
            json={"plt_id": "BL-MIXED", "name": "Mixed Bloom", "category": "frø"},
        )
        self.assertEqual(create.status_code, 201)
        self.assertEqual(
            self.client.post("/api/plots/B1/plants/BL-MIXED", json={"quantity": 1}).status_code,
            201,
        )
        self.assertEqual(
            self.client.post("/api/plots/B2/plants/BL-MIXED", json={"quantity": 1}).status_code,
            201,
        )
        update = self.client.patch(
            "/api/plots/plants/seen-growing",
            json={
                "updates": [
                    {
                        "plot_id": "B1",
                        "plt_id": "BL-MIXED",
                        "seen_growing": False,
                        "seen_growing_date": str(previous_year),
                    },
                    {
                        "plot_id": "B2",
                        "plt_id": "BL-MIXED",
                        "seen_growing": False,
                        "seen_growing_date": str(current_year),
                    },
                ],
            },
        )
        self.assertEqual(update.status_code, 200)

        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe bloom: Mixed Bloom",
                "due_on": "2026-06-01",
                "plant_ids": ["BL-MIXED"],
            },
        )
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]

        response = self.client.post(
            f"/api/tasks/{task_id}/action",
            json={"action": "complete"},
        )
        self.assertEqual(response.status_code, 200)

        assignments = self.client.get("/api/plants/BL-MIXED/assignments").json()
        self.assertEqual(len(assignments), 2)
        self.assertTrue(all(item["seen_growing"] is False for item in assignments))
        self.assertEqual(
            {item["seen_growing_date"] for item in assignments},
            {str(previous_year), str(current_year)},
        )

        plants = {
            plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Mixed Bloom").json()
        }
        self.assertTrue(plants["BL-MIXED"]["seen_growing"])
        self.assertEqual(plants["BL-MIXED"]["seen_growing_date"], date.today().isoformat())
        self.assertEqual(plants["BL-MIXED"]["presence_status"], "mixed")
        self.assertEqual(plants["BL-MIXED"]["last_not_seen_year"], str(current_year))

    def test_batch_task_actions(self) -> None:
        created_ids: list[str] = []
        for title in ("Batch one", "Batch two", "Batch three"):
            response = self.client.post(
                "/api/tasks",
                json={
                    "task_type": "prune",
                    "title": title,
                    "due_on": "2026-05-01",
                },
            )
            self.assertEqual(response.status_code, 201)
            created_ids.append(response.json()["id"])

        response = self.client.post(
            "/api/tasks/batch-action",
            json={
                "task_ids": created_ids[:2],
                "action": "complete",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)

        for task_id in created_ids[:2]:
            task = self.client.get(f"/api/tasks/{task_id}").json()
            self.assertEqual(task["status"], "completed")
            self.assertIsNotNone(task["completed_at_ms"])

        response = self.client.post(
            "/api/tasks/batch-action",
            json={
                "task_ids": [created_ids[2]],
                "action": "reschedule",
                "reschedule_to": "2026-06-10",
            },
        )
        self.assertEqual(response.status_code, 200)
        task = self.client.get(f"/api/tasks/{created_ids[2]}").json()
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["due_on"], "2026-06-10")

    def test_batch_non_horticultural_completion_rejects_selected_plants(self) -> None:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": "inspect_issue",
                "title": "Inspect aphids",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task_id = response.json()["id"]

        response = self.client.post(
            "/api/tasks/batch-action",
            json={
                "task_ids": [task_id],
                "action": "complete",
                "completed_plant_ids": ["PLT-TEST"],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("completion capture", response.text)

    def test_task_generate_idempotent(self) -> None:
        """Generate creates tasks, second run skips duplicates."""
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            garden_id = int(default_garden["id"])
            # Clean up any prior tasks
            conn.execute("DELETE FROM garden_task_plants")
            conn.execute("DELETE FROM garden_task_plots")
            conn.execute("DELETE FROM garden_tasks")
            # Create a user to satisfy plant_ownership NOT NULL constraint
            user = create_user(
                conn,
                username="taskgen_user",
                password=strong_password("taskgenpass"),
                role="editor",
            )
            user_id = int(user["id"])
            # PLT-002 is a "busker" with bloom_month "juni" from setUp
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                ("PLT-002", user_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        # First generate
        r = self.client.post("/api/tasks/generate")
        self.assertEqual(r.status_code, 200)
        first_created = r.json()["created"]

        # Second generate should skip duplicates
        r = self.client.post("/api/tasks/generate")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["skipped"], first_created)
        self.assertEqual(r.json()["created"], 0)

    def test_task_plant_plot_links(self) -> None:
        """Tasks can be linked to plants and plots."""
        # Create task with plant link
        r = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Watch for bloom",
                "due_on": "2026-06-01",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(r.status_code, 201)
        task_id = r.json()["id"]

        r = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(r.status_code, 200)
        task = r.json()
        self.assertIn("PLT-TEST", task["plant_ids"])
        self.assertIn("B1", task["plot_ids"])

        # Update links
        r = self.client.patch(
            f"/api/tasks/{task_id}",
            json={
                "plant_ids": ["PLT-002"],
                "plot_ids": ["B2"],
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/tasks/{task_id}")
        self.assertIn("PLT-002", r.json()["plant_ids"])
        self.assertIn("B2", r.json()["plot_ids"])

        # Filter by plant
        r = self.client.get("/api/tasks?plant_id=PLT-002")
        self.assertGreaterEqual(r.json()["total"], 1)

        # Filter by plot
        r = self.client.get("/api/tasks?plot_id=B2")
        self.assertGreaterEqual(r.json()["total"], 1)

    def test_refresh_descriptions_regenerates_rule_tasks_and_preserves_customized(self) -> None:
        """POST /api/tasks/refresh-descriptions refreshes generated descriptions only."""
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            garden_id = int(default_garden["id"])
            now_ms = current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, description, status,
                     severity, due_on, rule_source, metadata_json,
                     created_at_ms, updated_at_ms)
                VALUES (%s, 'observe_bloom', 'Observe bloom: Rose', '', 'pending',
                        'normal', '2026-06-01',
                        'bloom_observe:PLT-002:2026-06',
                        '{}', %s, %s)
                """,
                (garden_id, now_ms, now_ms),
            )
            customized_id = str(
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (garden_id, task_type, title, description, status,
                         severity, due_on, rule_source, metadata_json,
                         created_at_ms, updated_at_ms)
                    VALUES (%s, 'prune', 'Prune: PLT-002', 'Keep my custom text', 'pending',
                            'normal', '2026-03-01',
                            'seasonal_prune:PLT-002:2026-03',
                            '{"description_customized": true}', %s, %s)
                    RETURNING public_id
                    """,
                    (garden_id, now_ms, now_ms),
                ).fetchone()["public_id"]
            )
            conn.commit()
        finally:
            db.return_db(conn)

        r = self.client.post("/api/tasks/refresh-descriptions")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreaterEqual(data["updated"], 1)
        tasks = self.client.get("/api/tasks?status=pending").json()["tasks"]
        refreshed = next(
            task for task in tasks if task["rule_source"] == "bloom_observe:PLT-002:2026-06"
        )
        self.assertTrue(refreshed["description"].strip())
        self.assertTrue(str(refreshed["metadata"]["description_no"]).strip())
        self.assertTrue(refreshed["metadata"]["description_generated"])
        self.assertEqual(refreshed["metadata"]["description_source"], "care_instructions")
        customized = self.client.get(f"/api/tasks/{customized_id}").json()
        self.assertEqual(customized["description"], "Keep my custom text")

    def test_refresh_descriptions_can_force_overwrite_customized(self) -> None:
        """POST /api/tasks/refresh-descriptions can overwrite customized text when requested."""
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            garden_id = int(default_garden["id"])
            now_ms = current_timestamp_ms()
            task_id = str(
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (garden_id, task_type, title, description, status,
                         severity, due_on, rule_source, metadata_json,
                         created_at_ms, updated_at_ms)
                    VALUES (%s, 'prune', 'Prune: Rose', 'Keep my custom text', 'pending',
                            'normal', '2026-03-01',
                            'seasonal_prune:PLT-002:2026-03',
                            '{"description_customized": true}', %s, %s)
                    RETURNING public_id
                    """,
                    (garden_id, now_ms, now_ms),
                ).fetchone()["public_id"]
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.post(
            "/api/tasks/refresh-descriptions",
            json={"force_all": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)

        refreshed = self.client.get(f"/api/tasks/{task_id}").json()
        self.assertNotEqual(refreshed["description"], "Keep my custom text")
        self.assertTrue(refreshed["description"].strip())
        self.assertTrue(refreshed["metadata"]["description_generated"])
        self.assertEqual(refreshed["metadata"]["description_source"], "care_instructions")
        self.assertTrue(str(refreshed["metadata"]["description_no"]).strip())
        self.assertNotIn("description_customized", refreshed["metadata"])

    def test_refresh_descriptions_respects_task_description_ai_concurrency_limit(self) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            now_ms = current_timestamp_ms()
            task_id = int(
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                        (garden_id, task_type, title, description, status,
                         severity, due_on, rule_source, metadata_json,
                         created_at_ms, updated_at_ms)
                    VALUES (%s, 'prune', 'Prune: Rose', '', 'pending',
                            'normal', '2026-03-01',
                            'seasonal_prune:PLT-002:2026-03',
                            '{}', %s, %s)
                    RETURNING id
                    """,
                    (garden_id, now_ms, now_ms),
                ).fetchone()["id"]
            )
            conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, 'PLT-002')",
                (task_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with (
            patch.dict(
                os.environ,
                {
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-anthropic-key",
                    "AI_TASK_DESCRIPTION_CONCURRENCY_LIMIT": "1",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.task_generator.generate_task_descriptions_with_ai",
                return_value=[
                    {
                        "task_key": str(task_id),
                        "description_en": "Generated prune description",
                        "description_no": "Generert beskrivelse",
                    }
                ],
            ) as mock_ai,
            acquire_concurrency_slot(bucket="ai-task-descriptions", limit=1),
        ):
            response = self.client.post(
                "/api/tasks/refresh-descriptions",
                json={"force_all": True},
            )

        self.assertEqual(response.status_code, 429, response.text)
        self.assertIn("Concurrent request limit exceeded", response.json()["detail"])
        mock_ai.assert_not_called()
