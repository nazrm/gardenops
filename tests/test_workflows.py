from datetime import date
from unittest.mock import patch

import gardenops.db as db
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password


class TestWorkflowEndpoints(BaseApiTest):
    def _seed_owned_plants(self) -> int:
        """Seed plants with ownership for the default garden.

        Returns the garden_id used.
        """
        conn = db.get_db()
        try:
            garden_row = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert garden_row is not None
            garden_id = int(garden_row["id"])

            user = create_user(
                conn,
                username="wf_user",
                password=strong_password("wf_pass1"),
                role="editor",
            )
            user_id = int(user["id"])

            # PLT-TEST (category=fro) and PLT-002 (category=busker)
            # are seeded by BaseApiTest._seed_data
            for plt_id in ("PLT-TEST", "PLT-002"):
                conn.execute(
                    """
                    INSERT INTO plant_ownership
                        (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id
                    """,
                    (plt_id, user_id, garden_id),
                )

            # Assign plots to garden via plot_ownership
            for plot_id in ("B1", "B2"):
                conn.execute(
                    """
                    INSERT INTO plot_ownership
                        (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plot_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id,
                        garden_id = excluded.garden_id
                    """,
                    (plot_id, user_id, garden_id),
                )

            # Assign PLT-002 to B1 via plot_plants
            conn.execute(
                """
                INSERT INTO plot_plants
                    (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-002', 1) ON CONFLICT DO NOTHING
                """,
            )

            conn.commit()
        finally:
            db.return_db(conn)
        return garden_id

    @patch("gardenops.routers.workflows.date")
    def test_available_workflows_returns_matching_month(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 3, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        r = self.client.get("/api/workflows/available")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        wf_ids = [w["id"] for w in data["workflows"]]
        self.assertIn("spring_prep", wf_ids)
        spring = next(w for w in data["workflows"] if w["id"] == "spring_prep")
        self.assertEqual(spring["step_count"], 5)
        self.assertEqual(len(spring["steps"]), 5)
        self.assertEqual(spring["steps"][0]["id"], "assess_damage")

    @patch("gardenops.routers.workflows.date")
    def test_available_workflows_empty_for_wrong_month(self, mock_date) -> None:
        mock_date.today.return_value = date(2026, 12, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        r = self.client.get("/api/workflows/available")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["workflows"], [])

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_start_workflow_creates_tasks(self, mock_svc_date, mock_router_date) -> None:
        self._seed_owned_plants()
        fake_today = date(2026, 3, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        r = self.client.post(
            "/api/workflows/start",
            json={
                "workflow_id": "spring_prep",
                "selected_steps": [
                    "assess_damage",
                    "prune",
                    "prepare_soil",
                    "plan_plantings",
                    "watering_schedule",
                ],
            },
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["created"], 5)
        self.assertEqual(data["skipped"], 0)
        self.assertEqual(data["workflow_id"], "spring_prep")

        # Verify tasks have staggered due dates (3 days apart)
        tasks_r = self.client.get("/api/tasks?task_type=inspect_issue,prune,fertilize,sow,water")
        tasks = tasks_r.json()["tasks"]
        wf_tasks = [t for t in tasks if t["rule_source"].startswith("workflow:spring_prep:")]
        self.assertEqual(len(wf_tasks), 5)
        due_dates = sorted(t["due_on"] for t in wf_tasks)
        self.assertEqual(due_dates[0], "2026-03-15")
        self.assertEqual(due_dates[1], "2026-03-18")
        self.assertEqual(due_dates[2], "2026-03-21")
        self.assertEqual(due_dates[3], "2026-03-24")
        self.assertEqual(due_dates[4], "2026-03-27")

        # Verify bilingual descriptions are set
        for wt in wf_tasks:
            self.assertTrue(wt["description"], f"Task {wt['rule_source']} should have description")
            meta = wt.get("metadata", {}) or {}
            self.assertIn("description_no", meta, f"Task {wt['rule_source']} should have NO desc")

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_start_workflow_dedup(self, mock_svc_date, mock_router_date) -> None:
        self._seed_owned_plants()
        fake_today = date(2026, 3, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        body = {
            "workflow_id": "spring_prep",
            "selected_steps": ["assess_damage", "prune"],
        }
        r1 = self.client.post("/api/workflows/start", json=body)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["created"], 2)

        r2 = self.client.post("/api/workflows/start", json=body)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["created"], 0)
        self.assertEqual(r2.json()["skipped"], 2)

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_start_workflow_selected_steps(self, mock_svc_date, mock_router_date) -> None:
        self._seed_owned_plants()
        fake_today = date(2026, 3, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        r = self.client.post(
            "/api/workflows/start",
            json={
                "workflow_id": "spring_prep",
                "selected_steps": ["assess_damage", "prune"],
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["created"], 2)

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_end_of_season_protect_step_creates_protect_task(
        self,
        mock_svc_date,
        mock_router_date,
    ) -> None:
        self._seed_owned_plants()
        fake_today = date(2026, 9, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        response = self.client.post(
            "/api/workflows/start",
            json={
                "workflow_id": "end_of_season",
                "selected_steps": ["protect"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["created"], 1)

        tasks = self.client.get("/api/tasks?task_type=protect").json()["tasks"]
        workflow_tasks = [
            task
            for task in tasks
            if task["rule_source"].startswith("workflow:end_of_season:protect:")
        ]
        self.assertEqual(len(workflow_tasks), 1)
        self.assertEqual(workflow_tasks[0]["task_type"], "protect")
        self.assertIn("PLT-002", workflow_tasks[0]["plant_ids"])

    def test_start_workflow_unknown_returns_404(self) -> None:
        r = self.client.post(
            "/api/workflows/start",
            json={
                "workflow_id": "nonexistent_workflow",
                "selected_steps": ["step1"],
            },
        )
        self.assertEqual(r.status_code, 404)

    @patch("gardenops.routers.workflows.date")
    @patch("gardenops.services.workflow_service.date")
    def test_start_workflow_links_plants_and_plots(self, mock_svc_date, mock_router_date) -> None:
        self._seed_owned_plants()
        fake_today = date(2026, 3, 15)
        mock_router_date.today.return_value = fake_today
        mock_router_date.side_effect = lambda *a, **kw: date(*a, **kw)
        mock_svc_date.today.return_value = fake_today
        mock_svc_date.side_effect = lambda *a, **kw: date(*a, **kw)

        # "prune" scope is "woody" which matches PLT-002 (busker)
        r = self.client.post(
            "/api/workflows/start",
            json={
                "workflow_id": "spring_prep",
                "selected_steps": ["prune"],
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["created"], 1)

        # Find the created task and check its links
        conn = db.get_db()
        try:
            task_row = conn.execute(
                """
                SELECT id FROM garden_tasks
                WHERE rule_source LIKE 'workflow:spring_prep:prune:%'
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(task_row)
            task_id = int(task_row["id"])

            plant_rows = conn.execute(
                "SELECT plt_id FROM garden_task_plants WHERE task_id = %s",
                (task_id,),
            ).fetchall()
            plant_ids = [str(r["plt_id"]) for r in plant_rows]
            self.assertIn("PLT-002", plant_ids)

            plot_rows = conn.execute(
                "SELECT plot_id FROM garden_task_plots WHERE task_id = %s",
                (task_id,),
            ).fetchall()
            plot_ids = [str(r["plot_id"]) for r in plot_rows]
            # PLT-002 is assigned to B1 via plot_plants
            self.assertIn("B1", plot_ids)
        finally:
            db.return_db(conn)
