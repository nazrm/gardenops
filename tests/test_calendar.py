from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import gardenops.db as db
from gardenops.db import current_timestamp_ms
from gardenops.services.calendar_service import build_calendar_ics
from tests.base import BaseApiTest


class TestCalendarApi(BaseApiTest):
    def test_calendar_ics_uses_latest_stable_content_timestamp(self) -> None:
        subscription_updated = datetime(2026, 6, 4, 12, 30, tzinfo=UTC)
        ics, _etag, last_modified = build_calendar_ics(
            garden_name="Main garden",
            events=[
                {
                    "id": "manual-1",
                    "kind": "manual_event",
                    "source_key": "garden_event",
                    "title": "Older event",
                    "description": "",
                    "start_on": "2026-06-05",
                    "end_on": "2026-06-06",
                    "updated_at_ms": int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000),
                    "plot_ids": [],
                    "plant_ids": [],
                },
            ],
            generated_at=subscription_updated,
        )

        self.assertIn("DTSTAMP:20260604T123000Z", ics)
        self.assertEqual(last_modified, "Thu, 04 Jun 2026 12:30:00 GMT")

    def test_calendar_ics_escapes_lone_carriage_returns(self) -> None:
        ics, _etag, _last_modified = build_calendar_ics(
            garden_name="Main garden\rX-WR-RELCALID:evil",
            events=[
                {
                    "id": "manual-1",
                    "kind": "manual_event",
                    "source_key": "garden_event",
                    "title": "Water beds\rATTACH:https://example.invalid/evil",
                    "description": "Use the blue hose\rORGANIZER:mailto:evil@example.invalid",
                    "start_on": "2026-06-04",
                    "end_on": "2026-06-05",
                    "updated_at_ms": 1_770_000_000_000,
                    "plot_ids": [],
                    "plant_ids": [],
                },
            ],
            generated_at=datetime(2026, 6, 4, tzinfo=UTC),
        )

        self.assertNotIn("\rX-WR-RELCALID", ics)
        self.assertNotIn("\rATTACH", ics)
        self.assertNotIn("\rORGANIZER", ics)
        self.assertIn(r"Water beds\nATTACH", ics)

    def test_calendar_ics_escapes_text_and_folds_utf8_at_75_octets(self) -> None:
        title = ("Café, basil; row\\two\nlate " * 8).strip()
        description = ("First, line; with \\slash\r\nSecond line " * 8).strip()
        ics, _etag, _last_modified = build_calendar_ics(
            garden_name="Ångström, herb; garden\\north\nannex",
            events=[
                {
                    "id": "manual-1",
                    "kind": "manual_event",
                    "source_key": "garden_event",
                    "title": title,
                    "description": description,
                    "start_on": "2026-06-04",
                    "end_on": "2026-06-05",
                    "updated_at_ms": 1_770_000_000_000,
                    "plot_ids": [],
                    "plant_ids": [],
                },
            ],
            generated_at=datetime(2026, 6, 4, tzinfo=UTC),
        )

        unfolded = ics.replace("\r\n ", "")
        self.assertIn(r"X-WR-CALNAME:Ångström\, herb\; garden\\north\nannex", unfolded)
        self.assertIn(r"SUMMARY:Café\, basil\; row\\two\nlate", unfolded)
        self.assertIn(r"DESCRIPTION:First\, line\; with \\slash\nSecond line", unfolded)
        self.assertIn("DTSTART;VALUE=DATE:20260604\r\n", ics)
        self.assertIn("DTEND;VALUE=DATE:20260605\r\n", ics)
        self.assertTrue(any(line.startswith(" ") for line in ics.split("\r\n")))
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def _insert_task(
        self,
        *,
        title: str,
        task_type: str,
        status: str,
        due_on: date,
        completed_at_ms: int | None = None,
        snoozed_until: date | None = None,
        window_start_on: date | None = None,
        window_end_on: date | None = None,
        window_kind: str | None = None,
        plant_ids: list[str] | None = None,
        plot_ids: list[str] | None = None,
        garden_id: int | None = None,
    ) -> None:
        conn = db.get_db()
        try:
            now_ms = current_timestamp_ms()
            row = conn.execute(
                """
                INSERT INTO garden_tasks
                    (garden_id, task_type, title, description, status, severity, due_on,
                     snoozed_until, window_start_on, window_end_on, window_kind,
                     rule_source, metadata_json, created_by_user_id,
                     completed_by_user_id, completed_at_ms, created_at_ms, updated_at_ms)
                VALUES (
                    %s, %s, %s, '', %s, 'normal', %s, %s, %s, %s, %s, '', '{}',
                    %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    garden_id if garden_id is not None else self._get_default_garden_id(),
                    task_type,
                    title,
                    status,
                    due_on.isoformat(),
                    snoozed_until.isoformat() if snoozed_until else None,
                    window_start_on.isoformat() if window_start_on else None,
                    window_end_on.isoformat() if window_end_on else None,
                    window_kind,
                    self._owner_id,
                    self._owner_id if completed_at_ms is not None else None,
                    completed_at_ms,
                    now_ms,
                    completed_at_ms or now_ms,
                ),
            ).fetchone()
            task_id = int(row["id"])
            for plant_id in plant_ids or []:
                conn.execute(
                    "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                    (task_id, plant_id),
                )
            for plot_id in plot_ids or []:
                conn.execute(
                    "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
                    (task_id, plot_id),
                )
            conn.commit()
        finally:
            db.return_db(conn)

    def _insert_weather_alert(
        self,
        *,
        title: str,
        valid_from: date,
        valid_until: date,
    ) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description, valid_from,
                     valid_until, metadata_json, dismissed, created_at_ms)
                VALUES (%s, 'frost', 'high', %s, '', %s, %s, '{}', 0, %s)
                """,
                (
                    self._get_default_garden_id(),
                    title,
                    valid_from.isoformat(),
                    valid_until.isoformat(),
                    current_timestamp_ms(),
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_calendar_preferences_defaults_match_phase1_product_rules(self) -> None:
        response = self.client.get("/api/calendar/preferences")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertFalse(data["persisted"])
        self.assertEqual(data["preferences"]["selected_preset"], "essential")
        self.assertIn("weather_alert", data["preferences"]["visible_sources"])
        self.assertIn("observe_bloom", data["preferences"]["visible_sources"])
        self.assertFalse(data["preferences"]["include_recent_history"])
        self.assertEqual(data["preferences"]["selected_plant_ids"], [])
        self.assertFalse(data["capabilities"]["can_subscribe"])

    def test_viewer_can_persist_personal_calendar_preferences(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            self._create_test_user("calendar_pref_viewer", "viewerpass", "viewer")
            viewer_client, viewer_headers = self._authenticated_client(
                "calendar_pref_viewer",
                "viewerpass",
            )

            updated = viewer_client.patch(
                "/api/calendar/preferences",
                headers=viewer_headers,
                json={
                    "default_view": "agenda",
                    "include_recent_history": True,
                    "selected_plot_ids": ["B1"],
                },
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["preferences"]["default_view"], "agenda")
            self.assertTrue(updated.json()["preferences"]["include_recent_history"])
            self.assertEqual(updated.json()["preferences"]["selected_plot_ids"], ["B1"])

            loaded = viewer_client.get(
                "/api/calendar/preferences",
                headers=viewer_headers,
            )
            self.assertEqual(loaded.status_code, 200, loaded.text)
            self.assertTrue(loaded.json()["persisted"])
            self.assertEqual(loaded.json()["preferences"], updated.json()["preferences"])
            self.assertFalse(loaded.json()["capabilities"]["can_subscribe"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_calendar_selected_plant_preferences_filter_events_and_export(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            self._insert_task(
                title="Prune espalier apples",
                task_type="prune",
                status="pending",
                due_on=today + timedelta(days=3),
                plant_ids=["PLT-TEST"],
            )
            self._insert_task(
                title="Inspect greenhouse tomatoes",
                task_type="inspect_issue",
                status="pending",
                due_on=today + timedelta(days=4),
                plant_ids=["PLT-002"],
            )
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )

            update_response = client.patch(
                "/api/calendar/preferences",
                headers=headers,
                json={"selected_plant_ids": ["PLT-TEST"]},
            )
            self.assertEqual(update_response.status_code, 200)
            self.assertEqual(
                update_response.json()["preferences"]["selected_plant_ids"],
                ["PLT-TEST"],
            )

            start_on = today.isoformat()
            end_on = (today + timedelta(days=14)).isoformat()
            response = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}",
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            titles = {event["title"] for event in response.json()["events"]}
            self.assertIn("Prune espalier apples", titles)
            self.assertNotIn("Inspect greenhouse tomatoes", titles)

            export_response = client.get(
                f"/api/calendar/export.ics?start={start_on}&end={end_on}",
                headers=headers,
            )
            self.assertEqual(export_response.status_code, 200)
            self.assertIn("Prune espalier apples", export_response.text)
            self.assertNotIn("Inspect greenhouse tomatoes", export_response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_calendar_area_filters_include_indirect_plot_context(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET zone_code = EXCLUDED.zone_code,
                        zone_name = EXCLUDED.zone_name,
                        plot_number = EXCLUDED.plot_number,
                        grid_row = EXCLUDED.grid_row,
                        grid_col = EXCLUDED.grid_col
                    """,
                    ("S1", "S", "Slope", 1, 2, 1, "", "", None),
                )
                conn.execute(
                    (
                        "INSERT INTO plot_plants (plot_id, plt_id, quantity) "
                        "VALUES (%s, %s, 1) ON CONFLICT DO NOTHING"
                    ),
                    ("B1", "PLT-TEST"),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            self._insert_task(
                title="Prune espalier apples",
                task_type="prune",
                status="pending",
                due_on=today + timedelta(days=3),
                plant_ids=["PLT-TEST"],
            )
            self._insert_task(
                title="Inspect slope irrigation",
                task_type="inspect_issue",
                status="pending",
                due_on=today + timedelta(days=4),
                plot_ids=["S1"],
            )
            self._insert_weather_alert(
                title="Night frost warning",
                valid_from=today,
                valid_until=today + timedelta(days=1),
            )

            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )

            update_response = client.patch(
                "/api/calendar/preferences",
                headers=headers,
                json={"selected_zone_codes": ["B"], "selected_plot_ids": ["B1"]},
            )
            self.assertEqual(update_response.status_code, 200)
            self.assertEqual(
                update_response.json()["preferences"]["selected_zone_codes"],
                ["B"],
            )
            self.assertEqual(
                update_response.json()["preferences"]["selected_plot_ids"],
                ["B1"],
            )

            start_on = today.isoformat()
            end_on = (today + timedelta(days=14)).isoformat()
            response = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}",
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            titles = {event["title"] for event in response.json()["events"]}
            self.assertIn("Prune espalier apples", titles)
            self.assertIn("Night frost warning", titles)
            self.assertNotIn("Inspect slope irrigation", titles)

            prune_event = next(
                event
                for event in response.json()["events"]
                if event["title"] == "Prune espalier apples"
            )
            self.assertIn("B1", prune_event["plot_ids"])
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_calendar_indirect_plot_context_is_scoped_to_active_garden(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            default_garden_id = self._get_default_garden_id()
            conn = db.get_db()
            try:
                owner = conn.execute(
                    "SELECT id FROM auth_users WHERE username = 'test_admin'",
                ).fetchone()
                assert owner is not None
                second = conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES ('calendar-g2', 'Calendar G2') "
                    "RETURNING id",
                ).fetchone()
                assert second is not None
                second_garden_id = int(second["id"])
                conn.execute(
                    """
                    INSERT INTO garden_memberships (garden_id, user_id, role)
                    VALUES (%s, %s, 'admin')
                    """,
                    (second_garden_id, int(owner["id"])),
                )
                conn.execute(
                    """
                    INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET zone_code = EXCLUDED.zone_code,
                        zone_name = EXCLUDED.zone_name,
                        plot_number = EXCLUDED.plot_number,
                        grid_row = EXCLUDED.grid_row,
                        grid_col = EXCLUDED.grid_col
                    """,
                    ("XG2", "X", "Other Garden", 1, 4, 4, "", "", None),
                )
                conn.execute(
                    """
                    INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET garden_id = EXCLUDED.garden_id,
                        owner_user_id = EXCLUDED.owner_user_id
                    """,
                    ("XG2", int(owner["id"]), second_garden_id),
                )
                conn.execute(
                    "INSERT INTO plot_plants (plot_id, plt_id, quantity) "
                    "VALUES ('XG2', 'PLT-TEST', 1) ON CONFLICT DO NOTHING",
                )
                conn.commit()
            finally:
                db.return_db(conn)

            self._insert_task(
                title="Default garden plant task",
                task_type="prune",
                status="pending",
                due_on=today + timedelta(days=2),
                plant_ids=["PLT-TEST"],
            )

            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=default_garden_id,
            )
            response = client.get(
                (
                    "/api/calendar/events"
                    f"?start={today.isoformat()}"
                    f"&end={(today + timedelta(days=14)).isoformat()}"
                    "&selected_zone_codes=X"
                ),
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                "Default garden plant task",
                {event["title"] for event in response.json()["events"]},
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_authenticated_calendar_export_stays_in_active_garden_scope(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            default_garden_id = self._get_default_garden_id()
            conn = db.get_db()
            try:
                second = conn.execute(
                    "INSERT INTO gardens (slug, name) "
                    "VALUES ('calendar-export-g2', 'Calendar Export G2') "
                    "RETURNING id",
                ).fetchone()
                assert second is not None
                second_garden_id = int(second["id"])
                conn.execute(
                    "INSERT INTO garden_memberships (garden_id, user_id, role) "
                    "VALUES (%s, %s, 'admin')",
                    (second_garden_id, self._owner_id),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            self._insert_task(
                title="Default garden calendar task",
                task_type="prune",
                status="pending",
                due_on=today + timedelta(days=2),
                garden_id=default_garden_id,
            )
            self._insert_task(
                title="Other garden private calendar task",
                task_type="prune",
                status="pending",
                due_on=today + timedelta(days=2),
                garden_id=second_garden_id,
            )
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=default_garden_id,
            )

            response = client.get(
                (
                    "/api/calendar/export.ics"
                    f"?start={today.isoformat()}&end={(today + timedelta(days=14)).isoformat()}"
                    f"&garden_id={second_garden_id}"
                ),
                headers=headers,
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn("Default garden calendar task", response.text)
            self.assertNotIn("Other garden private calendar task", response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_calendar_events_keep_recent_history_opt_in(self) -> None:
        today = date.today()
        completed_at_ms = int(
            datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        self._insert_task(
            title="Prune espalier apples",
            task_type="prune",
            status="pending",
            due_on=today + timedelta(days=3),
        )
        self._insert_task(
            title="Harvest spring rhubarb",
            task_type="harvest",
            status="completed",
            due_on=today - timedelta(days=1),
            completed_at_ms=completed_at_ms,
        )
        self._insert_weather_alert(
            title="Night frost warning",
            valid_from=today,
            valid_until=today + timedelta(days=1),
        )

        start_on = (today - timedelta(days=2)).isoformat()
        end_on = (today + timedelta(days=14)).isoformat()

        response = self.client.get(f"/api/calendar/events?start={start_on}&end={end_on}")
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        titles = {event["title"] for event in events}
        self.assertIn("Prune espalier apples", titles)
        self.assertIn("Night frost warning", titles)
        self.assertNotIn("Harvest spring rhubarb", titles)

        response = self.client.get(
            f"/api/calendar/events?start={start_on}&end={end_on}&include_recent_history=true"
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        by_title = {event["title"]: event for event in events}
        self.assertEqual(by_title["Prune espalier apples"]["window_state"], "active")
        self.assertEqual(
            by_title["Prune espalier apples"]["window_start_on"],
            (today - timedelta(days=18)).isoformat(),
        )
        self.assertEqual(
            by_title["Prune espalier apples"]["window_end_on"],
            (today + timedelta(days=17)).isoformat(),
        )
        self.assertEqual(by_title["Harvest spring rhubarb"]["status"], "completed")
        self.assertEqual(by_title["Night frost warning"]["kind"], "weather_alert")

    def test_calendar_export_honors_recent_history_override(self) -> None:
        today = date.today()
        completed_at_ms = int(
            datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        self._insert_task(
            title="Harvest spring rhubarb",
            task_type="harvest",
            status="completed",
            due_on=today - timedelta(days=1),
            completed_at_ms=completed_at_ms,
        )

        start_on = (today - timedelta(days=2)).isoformat()
        end_on = (today + timedelta(days=14)).isoformat()

        default_export = self.client.get(f"/api/calendar/export.ics?start={start_on}&end={end_on}")
        self.assertEqual(default_export.status_code, 200)
        self.assertNotIn("Harvest spring rhubarb", default_export.text)

        history_export = self.client.get(
            f"/api/calendar/export.ics?start={start_on}&end={end_on}&include_recent_history=true"
        )
        self.assertEqual(history_export.status_code, 200)
        self.assertIn("Harvest spring rhubarb", history_export.text)

    def test_recent_history_uses_frozen_request_date(self) -> None:
        frozen_date = date(2020, 1, 10)
        completed_on = frozen_date - timedelta(days=5)
        completed_at_ms = int(
            datetime.combine(completed_on, datetime.min.time(), UTC).timestamp() * 1000
        )
        self._insert_task(
            title="Frozen recent completion",
            task_type="prune",
            status="completed",
            due_on=completed_on,
            completed_at_ms=completed_at_ms,
        )

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(
                    int(datetime(2020, 1, 10, tzinfo=UTC).timestamp() * 1000)
                ),
                "GARDENOPS_ATTENTION_FROZEN_DATE": frozen_date.isoformat(),
            },
        ):
            response = self.client.get(
                "/api/calendar/events?start=2019-12-01&end=2020-02-01&include_recent_history=true"
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "Frozen recent completion",
            {event["title"] for event in response.json()["events"]},
        )

    def test_calendar_derives_upcoming_window_state_for_future_task(self) -> None:
        today = date.today()
        self._insert_task(
            title="Sow late carrots",
            task_type="sow",
            status="pending",
            due_on=today + timedelta(days=25),
        )

        response = self.client.get(
            (
                "/api/calendar/events"
                f"?start={today.isoformat()}&end={(today + timedelta(days=40)).isoformat()}"
            ),
        )
        self.assertEqual(response.status_code, 200)
        by_title = {event["title"]: event for event in response.json()["events"]}
        self.assertEqual(by_title["Sow late carrots"]["window_state"], "upcoming")
        self.assertEqual(
            by_title["Sow late carrots"]["window_start_on"],
            (today + timedelta(days=15)).isoformat(),
        )

    def test_calendar_prefers_persisted_task_window_over_due_date_derivation(self) -> None:
        today = date.today()
        self._insert_task(
            title="Prune old currants",
            task_type="prune",
            status="pending",
            due_on=today + timedelta(days=30),
            window_start_on=today - timedelta(days=2),
            window_end_on=today + timedelta(days=6),
            window_kind="manual",
        )

        response = self.client.get(
            (
                "/api/calendar/events"
                f"?start={today.isoformat()}&end={(today + timedelta(days=40)).isoformat()}"
            ),
        )
        self.assertEqual(response.status_code, 200)
        by_title = {event["title"]: event for event in response.json()["events"]}
        event = by_title["Prune old currants"]
        self.assertEqual(event["window_kind"], "manual")
        self.assertEqual(event["window_state"], "active")
        self.assertEqual(event["window_start_on"], (today - timedelta(days=2)).isoformat())
        self.assertEqual(event["window_end_on"], (today + timedelta(days=6)).isoformat())

    def test_writers_can_crud_manual_calendar_events(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET zone_code = EXCLUDED.zone_code,
                        zone_name = EXCLUDED.zone_name,
                        plot_number = EXCLUDED.plot_number,
                        grid_row = EXCLUDED.grid_row,
                        grid_col = EXCLUDED.grid_col
                    """,
                    ("M1", "M", "Manual", 1, 4, 1, "", "", None),
                )
                conn.execute(
                    """
                    INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET owner_user_id = EXCLUDED.owner_user_id,
                        garden_id = EXCLUDED.garden_id
                    """,
                    ("M1", self._owner_id, self._get_default_garden_id()),
                )
                conn.commit()
            finally:
                db.return_db(conn)
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )

            create_response = client.post(
                "/api/calendar/manual-events",
                headers=headers,
                json={
                    "title": "Community pruning day",
                    "event_on": (today + timedelta(days=2)).isoformat(),
                    "description": "Bring loppers and a tarpaulin.",
                    "plot_ids": ["M1"],
                },
            )
            self.assertEqual(create_response.status_code, 201)
            created_event = create_response.json()["event"]
            self.assertEqual(created_event["kind"], "manual_event")
            self.assertEqual(created_event["source_key"], "garden_event")
            self.assertEqual(created_event["plot_ids"], ["M1"])

            start_on = today.isoformat()
            end_on = (today + timedelta(days=14)).isoformat()
            response = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}",
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            titles = {event["title"] for event in response.json()["events"]}
            self.assertIn("Community pruning day", titles)

            export_response = client.get(
                f"/api/calendar/export.ics?start={start_on}&end={end_on}",
                headers=headers,
            )
            self.assertEqual(export_response.status_code, 200)
            self.assertIn("Community pruning day", export_response.text)

            update_response = client.patch(
                f"/api/calendar/manual-events/{created_event['target_id']}",
                headers=headers,
                json={
                    "title": "Community pruning follow-up",
                    "event_on": (today + timedelta(days=4)).isoformat(),
                    "description": "Finish the espalier row.",
                    "plot_ids": [],
                },
            )
            self.assertEqual(update_response.status_code, 200)
            updated_event = update_response.json()["event"]
            self.assertEqual(updated_event["title"], "Community pruning follow-up")
            self.assertEqual(updated_event["plot_ids"], [])

            delete_response = client.delete(
                f"/api/calendar/manual-events/{created_event['target_id']}",
                headers=headers,
            )
            self.assertEqual(delete_response.status_code, 200)

            after_delete = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}",
                headers=headers,
            )
            self.assertEqual(after_delete.status_code, 200)
            titles = {event["title"] for event in after_delete.json()["events"]}
            self.assertNotIn("Community pruning follow-up", titles)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_viewers_cannot_create_manual_calendar_events(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            self._create_test_user("viewer_only", "viewerpass", "viewer")
            viewer_client, viewer_headers = self._authenticated_client(
                "viewer_only",
                "viewerpass",
            )

            response = viewer_client.post(
                "/api/calendar/manual-events",
                headers=viewer_headers,
                json={
                    "title": "Viewer attempt",
                    "event_on": date.today().isoformat(),
                    "description": "",
                    "plot_ids": [],
                },
            )
            self.assertEqual(response.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_manual_calendar_event_follows_plant_plot_context(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET zone_code = EXCLUDED.zone_code,
                        zone_name = EXCLUDED.zone_name,
                        plot_number = EXCLUDED.plot_number,
                        grid_row = EXCLUDED.grid_row,
                        grid_col = EXCLUDED.grid_col
                    """,
                    ("M2", "M", "Moved bed", 2, 4, 2, "", "", None),
                )
                conn.execute(
                    """
                    INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plot_id) DO UPDATE
                    SET owner_user_id = EXCLUDED.owner_user_id,
                        garden_id = EXCLUDED.garden_id
                    """,
                    ("M2", self._owner_id, self._get_default_garden_id()),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plt_id, garden_id) DO UPDATE
                    SET owner_user_id = EXCLUDED.owner_user_id
                    """,
                    ("PLT-TEST", self._owner_id, self._get_default_garden_id()),
                )
                conn.execute("DELETE FROM plot_plants WHERE plt_id = %s", ("PLT-TEST",))
                conn.execute(
                    """
                    INSERT INTO plot_plants (plot_id, plt_id, quantity)
                    VALUES (%s, %s, 1)
                    ON CONFLICT DO NOTHING
                    """,
                    ("B1", "PLT-TEST"),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )

            create_response = client.post(
                "/api/calendar/manual-events",
                headers=headers,
                json={
                    "title": "Check espalier tie points",
                    "event_on": (today + timedelta(days=3)).isoformat(),
                    "description": "Plant-linked event",
                    "plant_ids": ["PLT-TEST"],
                    "plot_ids": [],
                },
            )
            self.assertEqual(create_response.status_code, 201)
            event_id = create_response.json()["event"]["target_id"]

            start_on = today.isoformat()
            end_on = (today + timedelta(days=14)).isoformat()
            before_move = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}&selected_plot_ids=B1",
                headers=headers,
            )
            self.assertEqual(before_move.status_code, 200)
            by_title = {event["title"]: event for event in before_move.json()["events"]}
            self.assertIn("Check espalier tie points", by_title)
            self.assertIn("PLT-TEST", by_title["Check espalier tie points"]["plant_ids"])
            self.assertIn("B1", by_title["Check espalier tie points"]["plot_ids"])

            conn = db.get_db()
            try:
                conn.execute("DELETE FROM plot_plants WHERE plt_id = %s", ("PLT-TEST",))
                conn.execute(
                    """
                    INSERT INTO plot_plants (plot_id, plt_id, quantity)
                    VALUES (%s, %s, 1)
                    ON CONFLICT DO NOTHING
                    """,
                    ("M2", "PLT-TEST"),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            old_plot_response = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}&selected_plot_ids=B1",
                headers=headers,
            )
            self.assertEqual(old_plot_response.status_code, 200)
            self.assertNotIn(
                "Check espalier tie points",
                {event["title"] for event in old_plot_response.json()["events"]},
            )

            new_plot_response = client.get(
                f"/api/calendar/events?start={start_on}&end={end_on}&selected_plot_ids=M2",
                headers=headers,
            )
            self.assertEqual(new_plot_response.status_code, 200)
            by_title = {event["title"]: event for event in new_plot_response.json()["events"]}
            self.assertIn("Check espalier tie points", by_title)
            self.assertIn("M2", by_title["Check espalier tie points"]["plot_ids"])

            delete_response = client.delete(
                f"/api/calendar/manual-events/{event_id}",
                headers=headers,
            )
            self.assertEqual(delete_response.status_code, 200)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_viewers_can_export_but_not_subscribe(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            self._insert_task(
                title="Fertilize currants",
                task_type="fertilize",
                status="pending",
                due_on=today + timedelta(days=2),
            )
            self._create_test_user("viewer_only", "viewerpass", "viewer")
            viewer_client, viewer_headers = self._authenticated_client(
                "viewer_only",
                "viewerpass",
            )

            export_response = viewer_client.get(
                (
                    "/api/calendar/export.ics"
                    f"?start={today.isoformat()}&end={(today + timedelta(days=30)).isoformat()}"
                ),
                headers=viewer_headers,
            )
            self.assertEqual(export_response.status_code, 200)
            self.assertIn("text/calendar", export_response.headers["content-type"])
            self.assertIn("BEGIN:VCALENDAR", export_response.text)
            self.assertIn("Fertilize currants", export_response.text)

            create_response = viewer_client.post(
                "/api/calendar/subscriptions",
                headers=viewer_headers,
                json={"preset_key": "essential"},
            )
            self.assertEqual(create_response.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_editor_subscription_ownership_and_admin_revocation(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            today = date.today()
            self._insert_task(
                title="Inspect codling moth traps",
                task_type="inspect_issue",
                status="pending",
                due_on=today + timedelta(days=1),
            )

            self._create_test_user("caleda", "editorpass", "editor")
            self._create_test_user("caledb", "otherpass", "editor")
            self._create_test_user("caladm", "adminpass", "admin")

            editor_client, editor_headers = self._authenticated_client(
                "caleda",
                "editorpass",
            )
            other_editor_client, other_editor_headers = self._authenticated_client(
                "caledb",
                "otherpass",
            )
            admin_client, admin_headers = self._authenticated_client(
                "caladm",
                "adminpass",
            )

            create_response = editor_client.post(
                "/api/calendar/subscriptions",
                headers=editor_headers,
                json={"preset_key": "high_value"},
            )
            self.assertEqual(create_response.status_code, 201)
            created = create_response.json()
            feed_path = created["feed_path"]

            list_response = editor_client.get(
                "/api/calendar/subscriptions",
                headers=editor_headers,
            )
            self.assertEqual(list_response.status_code, 200)
            self.assertEqual(len(list_response.json()["subscriptions"]), 1)
            self.assertTrue(list_response.json()["subscriptions"][0]["owned_by_me"])

            other_list = other_editor_client.get(
                "/api/calendar/subscriptions",
                headers=other_editor_headers,
            )
            self.assertEqual(other_list.status_code, 200)
            self.assertEqual(other_list.json()["subscriptions"], [])

            other_delete = other_editor_client.delete(
                f"/api/calendar/subscriptions/{created['subscription']['id']}",
                headers=other_editor_headers,
            )
            self.assertEqual(other_delete.status_code, 403)

            feed_response = self.client.get(feed_path)
            self.assertEqual(feed_response.status_code, 200)
            self.assertEqual(feed_response.headers["referrer-policy"], "no-referrer")
            self.assertIn("BEGIN:VCALENDAR", feed_response.text)
            self.assertIn("Inspect codling moth traps", feed_response.text)

            demote_response = admin_client.post(
                f"/api/gardens/{self._get_default_garden_id()}/memberships",
                headers=admin_headers,
                json={"username": "caleda", "role": "viewer"},
            )
            self.assertEqual(demote_response.status_code, 200)

            demoted_feed = self.client.get(feed_path)
            self.assertEqual(demoted_feed.status_code, 404)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_subscription_revoke_invalidates_feed_and_hides_raw_token(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )
            create_response = client.post(
                "/api/calendar/subscriptions",
                headers=headers,
                json={"label": "Private calendar feed", "preset_key": "essential"},
            )
            self.assertEqual(create_response.status_code, 201)
            created = create_response.json()
            feed_path = created["feed_path"]
            token = feed_path.removeprefix("/calendar/subscriptions/").removesuffix(".ics")

            self.assertEqual(create_response.text.count(token), 1)
            self.assertNotIn(token, str(created["subscription"]))
            self.assertNotIn("token_hash", created["subscription"])
            self.assertEqual(self.client.get(feed_path).status_code, 200)

            list_response = client.get("/api/calendar/subscriptions", headers=headers)
            self.assertEqual(list_response.status_code, 200)
            self.assertNotIn(token, list_response.text)

            revoke_response = client.delete(
                f"/api/calendar/subscriptions/{created['subscription']['id']}",
                headers=headers,
            )
            self.assertEqual(revoke_response.status_code, 200)
            self.assertNotIn(token, revoke_response.text)
            self.assertEqual(self.client.get(feed_path).status_code, 404)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_frozen_attention_clock_controls_calendar_timestamps_and_feed_window(self) -> None:
        frozen_date = date(2032, 2, 3)
        frozen_now_ms = int(datetime(2032, 2, 3, tzinfo=UTC).timestamp() * 1000)
        boundary_event_date = frozen_date - timedelta(days=14)

        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(frozen_now_ms),
                "GARDENOPS_ATTENTION_FROZEN_DATE": frozen_date.isoformat(),
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )
            created_event_response = client.post(
                "/api/calendar/manual-events",
                headers=headers,
                json={
                    "title": "Frozen feed boundary event",
                    "event_on": boundary_event_date.isoformat(),
                    "description": "Visible on the frozen subscription boundary.",
                    "plant_ids": [],
                    "plot_ids": [],
                },
            )
            self.assertEqual(created_event_response.status_code, 201)
            event_id = created_event_response.json()["event"]["target_id"]

            updated_event_response = client.patch(
                f"/api/calendar/manual-events/{event_id}",
                headers=headers,
                json={
                    "title": "Frozen feed boundary event updated",
                    "event_on": boundary_event_date.isoformat(),
                    "description": "Visible on the frozen subscription boundary.",
                    "plant_ids": [],
                    "plot_ids": [],
                },
            )
            self.assertEqual(updated_event_response.status_code, 200)

            created_subscription_response = client.post(
                "/api/calendar/subscriptions",
                headers=headers,
                json={"label": "Frozen clock feed", "preset_key": "essential"},
            )
            self.assertEqual(created_subscription_response.status_code, 201)
            subscription = created_subscription_response.json()["subscription"]
            feed_response = self.client.get(created_subscription_response.json()["feed_path"])
            self.assertEqual(feed_response.status_code, 200)
            self.assertIn("Frozen feed boundary event updated", feed_response.text)
            self.assertIn(
                f"DTSTAMP:{datetime.fromtimestamp(frozen_now_ms / 1000, tz=UTC):%Y%m%dT%H%M%SZ}",
                feed_response.text,
            )

            revoke_response = client.delete(
                f"/api/calendar/subscriptions/{subscription['id']}",
                headers=headers,
            )
            self.assertEqual(revoke_response.status_code, 200)

        conn = db.get_db()
        try:
            event_row = conn.execute(
                """
                SELECT created_at_ms, updated_at_ms
                FROM garden_calendar_events
                WHERE public_id = %s
                """,
                (event_id,),
            ).fetchone()
            subscription_row = conn.execute(
                """
                SELECT created_at_ms, updated_at_ms, revoked_at_ms
                FROM calendar_subscriptions
                WHERE public_id = %s
                """,
                (subscription["id"],),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert event_row is not None
        assert subscription_row is not None
        self.assertEqual(int(event_row["created_at_ms"]), frozen_now_ms)
        self.assertEqual(int(event_row["updated_at_ms"]), frozen_now_ms)
        self.assertEqual(int(subscription_row["created_at_ms"]), frozen_now_ms)
        self.assertEqual(int(subscription_row["updated_at_ms"]), frozen_now_ms)
        self.assertEqual(int(subscription_row["revoked_at_ms"]), frozen_now_ms)

    def test_subscription_feed_etag_and_dtstamp_are_stable_for_unchanged_content(self) -> None:
        try:
            os.environ["AUTH_REQUIRED"] = "true"
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
            )
            event_response = client.post(
                "/api/calendar/manual-events",
                headers=headers,
                json={
                    "title": "Stable feed event",
                    "event_on": date.today().isoformat(),
                    "description": "The event content is unchanged.",
                    "plant_ids": [],
                    "plot_ids": [],
                },
            )
            self.assertEqual(event_response.status_code, 201, event_response.text)
            subscription_response = client.post(
                "/api/calendar/subscriptions",
                headers=headers,
                json={"label": "Stable feed", "preset_key": "essential"},
            )
            self.assertEqual(subscription_response.status_code, 201, subscription_response.text)
            feed_path = subscription_response.json()["feed_path"]
            feed_date = date.today()
            clocks = [
                (0, feed_date, datetime(2032, 2, 3, tzinfo=UTC)),
                (0, feed_date, datetime(2032, 2, 4, tzinfo=UTC)),
                (0, feed_date, datetime(2032, 2, 5, tzinfo=UTC)),
            ]
            with patch(
                "gardenops.routers.calendar._calendar_request_clock",
                side_effect=clocks,
            ):
                first = self.client.get(feed_path)
                second = self.client.get(
                    feed_path,
                    headers={"if-none-match": first.headers["etag"]},
                )
                third = self.client.get(feed_path)

            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 304, second.text)
            self.assertEqual(third.status_code, 200, third.text)
            self.assertEqual(first.headers["etag"], third.headers["etag"])
            first_dtstamp = next(
                line for line in first.text.splitlines() if line.startswith("DTSTAMP:")
            )
            third_dtstamp = next(
                line for line in third.text.splitlines() if line.startswith("DTSTAMP:")
            )
            self.assertEqual(first_dtstamp, third_dtstamp)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
