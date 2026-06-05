import os
import unittest

import gardenops.db as db
from gardenops.security import create_user
from gardenops.services.notification_service import create_notification
from tests.base import BaseApiTest, strong_password


class TestIssueApi(BaseApiTest):
    """Tests for the garden issues CRUD endpoints."""

    def _issue_followup_task(self, issue_id: str) -> dict:
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT public_id, status, due_on, snoozed_until, completed_at_ms,
                       completed_by_user_id
                FROM garden_tasks
                WHERE rule_source = %s
                """,
                (f"auto:issue_followup:{issue_id}",),
            ).fetchone()
            assert row is not None
            return dict(row)
        finally:
            db.return_db(conn)

    def test_issue_crud_lifecycle(self) -> None:
        """Create, read, update, delete an issue."""
        # Create
        resp = self.client.post(
            "/api/issues",
            json={
                "issue_type": "pest",
                "title": "Aphids on roses",
                "description": "Small green insects on rose buds",
                "severity": "high",
                "suspected_cause": "Warm weather",
                "treatment_plan": "Neem oil spray",
                "follow_up_on": "2026-04-01",
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        issue_id = data["id"]

        # Read
        resp = self.client.get(f"/api/issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)
        issue = resp.json()
        self.assertEqual(issue["issue_type"], "pest")
        self.assertEqual(issue["title"], "Aphids on roses")
        self.assertEqual(issue["severity"], "high")
        self.assertEqual(issue["status"], "open")
        self.assertEqual(issue["suspected_cause"], "Warm weather")
        self.assertEqual(issue["treatment_plan"], "Neem oil spray")
        self.assertEqual(issue["follow_up_on"], "2026-04-01")

        # Update
        resp = self.client.patch(
            f"/api/issues/{issue_id}",
            json={
                "severity": "critical",
                "status": "treating",
                "treatment_plan": "Neem oil spray + ladybugs",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        # Verify update
        resp = self.client.get(f"/api/issues/{issue_id}")
        issue = resp.json()
        self.assertEqual(issue["severity"], "critical")
        self.assertEqual(issue["status"], "treating")
        self.assertEqual(issue["treatment_plan"], "Neem oil spray + ladybugs")

        # Delete
        resp = self.client.delete(f"/api/issues/{issue_id}")
        self.assertEqual(resp.status_code, 200)

        # Verify deleted
        resp = self.client.get(f"/api/issues/{issue_id}")
        self.assertEqual(resp.status_code, 404)

    def test_issue_list_filters(self) -> None:
        """Filter issues by status, type, and severity."""
        # Create a few issues
        self.client.post(
            "/api/issues",
            json={"issue_type": "pest", "title": "Issue 1", "severity": "low"},
        )
        self.client.post(
            "/api/issues",
            json={
                "issue_type": "disease",
                "title": "Issue 2",
                "severity": "high",
            },
        )
        self.client.post(
            "/api/issues",
            json={
                "issue_type": "fungal",
                "title": "Issue 3",
                "severity": "critical",
            },
        )

        # Filter by type
        resp = self.client.get("/api/issues?issue_type=pest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["issues"][0]["issue_type"], "pest")

        # Filter by severity
        resp = self.client.get("/api/issues?severity=high")
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["issues"][0]["severity"], "high")

        # Filter by status (all are open)
        resp = self.client.get("/api/issues?status=open")
        data = resp.json()
        self.assertEqual(data["total"], 3)

        # No matches for resolved
        resp = self.client.get("/api/issues?status=resolved")
        data = resp.json()
        self.assertEqual(data["total"], 0)

    def test_issue_resolve(self) -> None:
        """Resolve an issue via the resolve endpoint."""
        resp = self.client.post(
            "/api/issues",
            json={"issue_type": "damage", "title": "Broken branch"},
        )
        issue_id = resp.json()["id"]

        resp = self.client.post(f"/api/issues/{issue_id}/resolve")
        self.assertEqual(resp.status_code, 200)

        # Verify resolved
        resp = self.client.get(f"/api/issues/{issue_id}")
        issue = resp.json()
        self.assertEqual(issue["status"], "resolved")
        self.assertIsNotNone(issue["resolved_at_ms"])

    def test_issue_followup_task_tracks_due_date_and_resolution(self) -> None:
        resp = self.client.post(
            "/api/issues",
            json={
                "issue_type": "pest",
                "title": "Aphids",
                "follow_up_on": "2026-04-01",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        issue_id = resp.json()["id"]
        task = self._issue_followup_task(issue_id)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["due_on"], "2026-04-01")
        task_id = str(task["public_id"])

        conn = db.get_db()
        try:
            create_notification(
                conn,
                self._get_default_garden_id(),
                self._owner_id,
                "task_due",
                "Task due",
                "Follow up",
                target_type="task",
                target_id=task_id,
            )
        finally:
            db.return_db(conn)

        patch_resp = self.client.patch(
            f"/api/issues/{issue_id}",
            json={"follow_up_on": "2026-04-10", "title": "Aphids again"},
        )
        self.assertEqual(patch_resp.status_code, 200, patch_resp.text)
        task = self._issue_followup_task(issue_id)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["due_on"], "2026-04-10")

        conn = db.get_db()
        try:
            create_notification(
                conn,
                self._get_default_garden_id(),
                self._owner_id,
                "task_due",
                "Task due again",
                "Follow up again",
                target_type="task",
                target_id=task_id,
            )
        finally:
            db.return_db(conn)

        resolve = self.client.post(f"/api/issues/{issue_id}/resolve")
        self.assertEqual(resolve.status_code, 200, resolve.text)
        task = self._issue_followup_task(issue_id)
        self.assertEqual(task["status"], "skipped")
        self.assertIsNone(task["completed_at_ms"])
        self.assertIsNone(task["completed_by_user_id"])

        conn = db.get_db()
        try:
            notification = conn.execute(
                """
                SELECT cleared_at_ms, clear_reason
                FROM notification_events
                WHERE target_type = 'task' AND target_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            assert notification is not None
            self.assertIsNotNone(notification["cleared_at_ms"])
            self.assertEqual(str(notification["clear_reason"]), "issue_resolved")
        finally:
            db.return_db(conn)

    def test_issue_patch_status_transitions_keep_resolution_metadata_consistent(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            client = self._new_client()
            _, csrf = self._login_session("test_admin", "testadminpass", client=client)
            headers = self._session_headers(csrf)
            resp = client.post(
                "/api/issues",
                headers=headers,
                json={"issue_type": "damage", "title": "Wind damage"},
            )
            self.assertEqual(resp.status_code, 201)
            issue_id = resp.json()["id"]

            resolved = client.patch(
                f"/api/issues/{issue_id}",
                headers=headers,
                json={"status": "resolved"},
            )
            self.assertEqual(resolved.status_code, 200)

            detail = client.get(f"/api/issues/{issue_id}", headers=headers)
            self.assertEqual(detail.status_code, 200)
            issue = detail.json()
            self.assertEqual(issue["status"], "resolved")
            self.assertIsNotNone(issue["resolved_at_ms"])
            self.assertIsNotNone(issue["resolved_by_user_id"])

            history = client.get(f"/api/issues/{issue_id}/history", headers=headers)
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()["issue_events"][0]["kind"], "resolved")

            reopened = client.patch(
                f"/api/issues/{issue_id}",
                headers=headers,
                json={"status": "monitoring"},
            )
            self.assertEqual(reopened.status_code, 200)

            detail = client.get(f"/api/issues/{issue_id}", headers=headers)
            issue = detail.json()
            self.assertEqual(issue["status"], "monitoring")
            self.assertIsNone(issue["resolved_at_ms"])
            self.assertIsNone(issue["resolved_by_user_id"])

            history = client.get(f"/api/issues/{issue_id}/history", headers=headers)
            self.assertEqual(history.status_code, 200)
            self.assertIn(
                "reopened",
                [event["kind"] for event in history.json()["issue_events"]],
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_issue_plant_plot_links(self) -> None:
        """Verify plant and plot linking on issues."""
        resp = self.client.post(
            "/api/issues",
            json={
                "issue_type": "nutrient",
                "title": "Yellow leaves",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(resp.status_code, 201)
        issue_id = resp.json()["id"]

        resp = self.client.get(f"/api/issues/{issue_id}")
        issue = resp.json()
        self.assertIn("PLT-TEST", issue["plant_ids"])
        self.assertIn("B1", issue["plot_ids"])

        # Update links
        self.client.patch(
            f"/api/issues/{issue_id}",
            json={"plant_ids": ["PLT-TEST"], "plot_ids": ["B1", "B2"]},
        )
        resp = self.client.get(f"/api/issues/{issue_id}")
        issue = resp.json()
        self.assertEqual(sorted(issue["plot_ids"]), ["B1", "B2"])

    def test_issue_summary(self) -> None:
        """Verify summary counts by status."""
        self.client.post(
            "/api/issues",
            json={"issue_type": "pest", "title": "A"},
        )
        self.client.post(
            "/api/issues",
            json={"issue_type": "disease", "title": "B"},
        )
        resp_create = self.client.post(
            "/api/issues",
            json={"issue_type": "fungal", "title": "C"},
        )
        # Resolve the third one
        self.client.post(f"/api/issues/{resp_create.json()['id']}/resolve")

        resp = self.client.get("/api/issues/summary")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertEqual(summary["open"], 2)
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["total"], 3)

    def test_issue_history_and_journal_integration(self) -> None:
        """Issue lifecycle events should surface in both issue history and journal history."""
        resp = self.client.post(
            "/api/issues",
            json={
                "issue_type": "pest",
                "title": "Aphids on roses",
                "description": "Sticky leaves on the new growth",
                "severity": "high",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(resp.status_code, 201)
        issue_id = resp.json()["id"]

        journal_resp = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-15",
                "title": "Manual check",
                "notes": "Checked the underside of the leaves.",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(journal_resp.status_code, 201)

        patch_resp = self.client.patch(
            f"/api/issues/{issue_id}",
            json={
                "status": "treating",
                "severity": "critical",
                "treatment_plan": "Neem oil spray",
            },
        )
        self.assertEqual(patch_resp.status_code, 200)

        resolve_resp = self.client.post(f"/api/issues/{issue_id}/resolve")
        self.assertEqual(resolve_resp.status_code, 200)

        history_resp = self.client.get(f"/api/issues/{issue_id}/history")
        self.assertEqual(history_resp.status_code, 200)
        history = history_resp.json()
        self.assertEqual(
            [event["kind"] for event in history["issue_events"][:3]],
            ["resolved", "updated", "created"],
        )
        self.assertIn(
            "Severity high -> critical",
            history["issue_events"][1]["summary"],
        )
        journal_titles = [entry["title"] for entry in history["journal_entries"]]
        self.assertIn("Issue reported: Aphids on roses", journal_titles)
        self.assertIn("Issue updated: Aphids on roses", journal_titles)
        self.assertIn("Issue resolved: Aphids on roses", journal_titles)
        self.assertIn("Manual check", journal_titles)

        related_journal = self.client.get("/api/journal?plant_id=PLT-TEST")
        self.assertEqual(related_journal.status_code, 200)
        related_titles = [entry["title"] for entry in related_journal.json()["entries"]]
        self.assertIn("Issue reported: Aphids on roses", related_titles)
        self.assertIn("Issue resolved: Aphids on roses", related_titles)

    @unittest.skip(
        "Test setup needs rework for Postgres; viewer garden membership is still unresolved"
    )
    def test_issue_auth_viewer_denied(self) -> None:
        """Viewer role gets 403 on write operations."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_USER_LIFECYCLE_ENABLED"] = "true"
        try:
            # Create admin who will own the garden and create the issue
            conn = db.get_db()
            create_user(
                conn, username="admin_iss", password=strong_password("adminpass"), role="admin"
            )
            conn.commit()
            db.return_db(conn)

            admin_client = self._new_client()
            _, admin_csrf = self._login_session("admin_iss", "adminpass", client=admin_client)

            # Create garden
            garden_r = admin_client.post(
                "/api/gardens",
                headers={"x-csrf-token": admin_csrf},
                json={"name": "Issue Auth Test"},
            )
            self.assertEqual(garden_r.status_code, 201, garden_r.text)
            garden_id = garden_r.json()["id"]
            admin_h = self._session_headers(admin_csrf, garden_id=garden_id)

            # Create viewer user via admin API
            viewer_r = admin_client.post(
                "/api/auth/users",
                headers=admin_h,
                json={
                    "username": "viewer_iss",
                    "password": strong_password("viewerpass"),
                    "role": "viewer",
                },
            )
            self.assertEqual(viewer_r.status_code, 201, viewer_r.text)
            viewer_user_id = viewer_r.json()["id"]

            # Add viewer to the garden via DB
            conn = db.get_db()
            conn.execute(
                "INSERT INTO garden_memberships (garden_id, user_id, role) "
                "VALUES (%s, %s, 'viewer') ON CONFLICT DO NOTHING",
                (garden_id, viewer_user_id),
            )
            conn.commit()
            db.return_db(conn)

            # Admin creates an issue
            r = admin_client.post(
                "/api/issues",
                headers=admin_h,
                json={
                    "issue_type": "pest",
                    "title": "Admin issue",
                },
            )
            self.assertEqual(r.status_code, 201)
            issue_id = r.json()["id"]

            # Viewer can read but not write
            viewer_client = self._new_client()
            _, viewer_csrf = self._login_session("viewer_iss", "viewerpass", client=viewer_client)
            viewer_h = self._session_headers(viewer_csrf, garden_id=garden_id)

            r = viewer_client.get("/api/issues", headers=viewer_h)
            self.assertEqual(r.status_code, 200)

            r = viewer_client.post(
                "/api/issues",
                headers=viewer_h,
                json={
                    "issue_type": "pest",
                    "title": "Should fail",
                },
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.patch(
                f"/api/issues/{issue_id}",
                headers=viewer_h,
                json={"title": "hack"},
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.delete(f"/api/issues/{issue_id}", headers=viewer_h)
            self.assertEqual(r.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
