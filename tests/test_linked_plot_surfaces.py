from __future__ import annotations

import os
from datetime import date, timedelta

from tests.base import BaseApiTest


class TestLinkedPlotSurfaces(BaseApiTest):
    def test_archived_container_links_keep_name_and_read_only_history(self) -> None:
        garden_id = self._get_default_garden_id()
        created = self.client.post(
            f"/api/gardens/{garden_id}/containers",
            json={"name": "History planter", "container_type": "planter"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        plot_id = created.json()["plot_id"]

        journal = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-08-20",
                "plot_ids": [plot_id],
            },
        )
        self.assertEqual(journal.status_code, 201, journal.text)
        harvest = self.client.post(
            "/api/harvest",
            json={
                "occurred_on": "2026-08-20",
                "quantity": 1,
                "unit": "pieces",
                "plot_ids": [plot_id],
            },
        )
        self.assertEqual(harvest.status_code, 201, harvest.text)
        issue = self.client.post(
            "/api/issues",
            json={
                "issue_type": "other",
                "title": "Historical container note",
                "plot_ids": [plot_id],
            },
        )
        self.assertEqual(issue.status_code, 201, issue.text)
        previous_auth_required = os.environ.get("AUTH_REQUIRED")
        os.environ["AUTH_REQUIRED"] = "true"
        try:
            calendar_client, calendar_headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )
            calendar = calendar_client.post(
                "/api/calendar/manual-events",
                headers=calendar_headers,
                json={
                    "title": "Historical planter check",
                    "event_on": date.today().isoformat(),
                    "plot_ids": [plot_id],
                },
            )
        finally:
            if previous_auth_required is None:
                os.environ.pop("AUTH_REQUIRED", None)
            else:
                os.environ["AUTH_REQUIRED"] = previous_auth_required
        self.assertEqual(calendar.status_code, 201, calendar.text)

        archived = self.client.delete(
            f"/api/gardens/{garden_id}/containers/{plot_id}",
        )
        self.assertEqual(archived.status_code, 200, archived.text)

        for response in (
            self.client.get(f"/api/journal/{journal.json()['id']}"),
            self.client.get(f"/api/harvest/{harvest.json()['id']}"),
            self.client.get(f"/api/issues/{issue.json()['id']}"),
        ):
            self.assertEqual(response.status_code, 200, response.text)
            linked = response.json()["plots"][0]
            self.assertEqual(linked["plot_id"], plot_id)
            self.assertEqual(linked["display_name"], "History planter")
            self.assertIsNotNone(linked["archived_at_ms"])

        history = self.client.get(f"/api/issues/{issue.json()['id']}/history")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(
            history.json()["journal_entries"][0]["plots"][0]["display_name"],
            "History planter",
        )

        today = date.today()
        events = calendar_client.get(
            "/api/calendar/events"
            f"?start={today.isoformat()}&end={(today + timedelta(days=2)).isoformat()}",
            headers=calendar_headers,
        )
        self.assertEqual(events.status_code, 200, events.text)
        event = next(
            event
            for event in events.json()["events"]
            if event["title"] == "Historical planter check"
        )
        self.assertEqual(event["plots"][0]["display_name"], "History planter")
        self.assertIsNotNone(event["plots"][0]["archived_at_ms"])

        active_plot_ids = {plot["plot_id"] for plot in self.client.get("/api/plots").json()}
        self.assertNotIn(plot_id, active_plot_ids)
        self.assertEqual(
            self.client.get(f"/api/plots/{plot_id}/plants").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/plots/{plot_id}/plants/PLT-TEST",
                json={"quantity": 1},
            ).status_code,
            410,
        )
