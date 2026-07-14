import json
from unittest.mock import patch

import gardenops.db as db
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password


class TestWeather(BaseApiTest):
    def test_weather_summary_no_location(self) -> None:
        """Garden without lat/lng returns empty weather summary."""
        r = self.client.get("/api/weather/summary")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["forecast_available"])
        self.assertEqual(data["forecast_days"], [])
        self.assertEqual(data["alerts"], [])

    def test_weather_check_no_location(self) -> None:
        """Weather check returns 422 when no location configured."""
        r = self.client.post("/api/weather/check")
        self.assertEqual(r.status_code, 422)

    def test_weather_check_resolves_absent_forecast_owned_work(self) -> None:
        from gardenops.services.attention.service import set_user_attention_state
        from gardenops.services.notification_service import create_notification

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
            ).fetchone()
            user = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'test_admin'"
            ).fetchone()
            assert garden is not None
            assert user is not None
            garden_id = int(garden["id"])
            user_id = int(user["id"])
            conn.execute(
                "UPDATE gardens SET latitude = 59.9, longitude = 10.7 WHERE id = %s",
                (garden_id,),
            )
            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'dry_spell', 'normal', 'Dry spell', 'Water regularly.',
                        '2032-02-03', '2032-02-06', '{}', 1)
                RETURNING id
                """,
                (garden_id,),
            ).fetchone()
            assert alert is not None
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES ('task_forecast_resolve', %s, 'water', 'Water forecast plant',
                        'pending', 'normal', '2032-02-03', %s, '{}', 1, 1)
                RETURNING public_id
                """,
                (garden_id, f"auto:dry_water:{int(alert['id'])}:PLT-TEST"),
            ).fetchone()
            assert task is not None
            conn.commit()
            create_notification(
                conn,
                garden_id,
                user_id,
                "weather_alert",
                "Dry spell",
                "Water regularly.",
                target_type="weather_alert",
                target_id="dry_spell:2032-02-03",
            )
            set_user_attention_state(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                item_id=f"attn:weather:alert:{int(alert['id'])}",
                user_state="dismissed",
                now_ms=db.current_timestamp_ms(),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch(
            "gardenops.routers.weather.check_weather_and_generate_alerts",
            return_value={
                "forecast_available": True,
                "alerts_created": 0,
                "alerts_skipped": 0,
                "alerts": [],
            },
        ):
            response = self.client.post("/api/weather/check")
        self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            alert_row = conn.execute(
                "SELECT dismissed FROM weather_alerts WHERE id = %s",
                (int(alert["id"]),),
            ).fetchone()
            task_row = conn.execute(
                "SELECT status FROM garden_tasks WHERE public_id = 'task_forecast_resolve'"
            ).fetchone()
            notification = conn.execute(
                """
                SELECT clear_reason
                FROM notification_events
                WHERE target_id = 'dry_spell:2032-02-03'
                """
            ).fetchone()
            state = conn.execute(
                """
                SELECT user_state
                FROM user_attention_item_state
                WHERE garden_id = %s
                  AND user_id = %s
                  AND item_id = %s
                """,
                (garden_id, user_id, f"attn:weather:alert:{int(alert['id'])}"),
            ).fetchone()
        finally:
            db.return_db(conn)
        assert alert_row is not None
        assert task_row is not None
        assert notification is not None
        assert state is not None
        self.assertTrue(bool(alert_row["dismissed"]))
        self.assertEqual(str(task_row["status"]), "skipped")
        self.assertEqual(str(notification["clear_reason"]), "forecast_resolved")
        self.assertEqual(str(state["user_state"]), "dismissed")

    def test_same_identity_reappearance_recovers_only_automatically_resolved_task(self) -> None:
        from gardenops.services.notification_service import reconcile_weather_alert_work
        from gardenops.services.weather_service import save_weather_alerts

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1"
            ).fetchone()
            dates = conn.execute(
                """
                SELECT CURRENT_DATE::text AS today,
                       (CURRENT_DATE + INTERVAL '3 days')::date::text AS valid_until
                """
            ).fetchone()
            assert garden is not None
            assert dates is not None
            garden_id = int(garden["id"])
            today = str(dates["today"])
            conn.execute(
                "UPDATE plants SET care_watering = 'Water regularly' WHERE plt_id = 'PLT-002'"
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-002', 1)
                ON CONFLICT DO NOTHING
                """,
            )
            alert = {
                "alert_type": "dry_spell",
                "severity": "normal",
                "title": "Returning dry spell",
                "description": "Water outdoor plants regularly.",
                "valid_from": today,
                "valid_until": str(dates["valid_until"]),
                "metadata": {"days": 6},
            }
            now_ms = db.current_timestamp_ms()
            save_weather_alerts(conn, garden_id, [alert])
            initial = reconcile_weather_alert_work(
                conn,
                garden_id=garden_id,
                alerts=[alert],
                actor_user_id=None,
                now_ms=now_ms,
                replace_forecast_alerts=True,
            )
            resolved = reconcile_weather_alert_work(
                conn,
                garden_id=garden_id,
                alerts=[],
                actor_user_id=None,
                now_ms=now_ms + 1,
                replace_forecast_alerts=True,
            )
            save_weather_alerts(conn, garden_id, [alert])
            recovered = reconcile_weather_alert_work(
                conn,
                garden_id=garden_id,
                alerts=[alert],
                actor_user_id=None,
                now_ms=now_ms + 2,
                replace_forecast_alerts=True,
            )
            tasks = conn.execute(
                """
                SELECT status, metadata_json
                FROM garden_tasks
                WHERE garden_id = %s
                  AND rule_source LIKE 'auto:dry_water:%%:PLT-002'
                """,
                (garden_id,),
            ).fetchall()
            alert_row = conn.execute(
                """
                SELECT dismissed, metadata_json
                FROM weather_alerts
                WHERE garden_id = %s
                  AND alert_type = 'dry_spell'
                  AND valid_from = %s
                """,
                (garden_id, today),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert initial["tasks_created"] == 1
        assert resolved["tasks_resolved"] == 1
        assert recovered["tasks_recovered"] == 1
        assert len(tasks) == 1
        assert str(tasks[0]["status"]) == "pending"
        task_metadata = json.loads(str(tasks[0]["metadata_json"]))
        assert task_metadata["lifecycle"]["reason"] == "same_identity_reappeared"
        assert any(
            event.get("resolution_kind") == "automatic_forecast"
            for event in task_metadata["lifecycle_history"]
        )
        assert alert_row is not None
        assert not bool(alert_row["dismissed"])
        alert_metadata = json.loads(str(alert_row["metadata_json"]))
        assert alert_metadata["lifecycle"]["reason"] == "reappeared_in_current_forecast"
        assert any(
            event.get("resolution_kind") == "automatic_forecast"
            for event in alert_metadata["lifecycle_history"]
        )

    def test_weather_check_rolls_back_alerts_when_reconciliation_fails(self) -> None:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default' LIMIT 1").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                "UPDATE gardens SET latitude = 59.9, longitude = 10.7 WHERE id = %s",
                (garden_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        forecast = {
            "daily": {
                "time": ["2032-02-03"],
                "temperature_2m_min": [-4.0],
                "temperature_2m_max": [2.0],
                "precipitation_sum": [0.0],
            },
        }
        with (
            patch(
                "gardenops.services.weather_service.get_or_fetch_forecast", return_value=forecast
            ),
            patch(
                "gardenops.routers.weather.reconcile_weather_alert_work",
                side_effect=RuntimeError("reconciliation failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "reconciliation failed"),
        ):
            self.client.post("/api/weather/check")

        conn = db.get_db()
        try:
            alert = conn.execute(
                """
                SELECT 1
                FROM weather_alerts
                WHERE garden_id = %s
                  AND alert_type = 'frost_warning'
                  AND valid_from = '2032-02-03'
                """,
                (garden_id,),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertIsNone(alert)

    @patch("gardenops.services.weather_service.urllib.request.urlopen")
    def test_weather_endpoints_degrade_without_external_egress(self, mock_urlopen) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE gardens SET latitude = 59.9, longitude = 10.7 WHERE slug = 'default'",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        mock_urlopen.side_effect = AssertionError("unexpected weather-provider egress")
        with patch.dict(
            "os.environ",
            {"GARDENOPS_WEATHER_EXTERNAL_FETCH_ENABLED": "false"},
        ):
            forecast = self.client.get("/api/weather/forecast")
            check = self.client.post("/api/weather/check")

        self.assertEqual(forecast.status_code, 200)
        self.assertEqual(forecast.json(), {"forecast_available": False, "daily": {}})
        self.assertEqual(check.status_code, 200)
        self.assertEqual(
            check.json(),
            {"forecast_available": False, "alerts_created": 0, "alerts_skipped": 0},
        )
        mock_urlopen.assert_not_called()

    def test_weather_alerts_empty(self) -> None:
        """No alerts by default."""
        r = self.client.get("/api/weather/alerts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["alerts"], [])
        self.assertEqual(r.json()["total"], 0)

    def test_weather_alert_dismiss(self) -> None:
        """Create alert directly, then dismiss it via API."""
        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])
        now = db.current_timestamp_ms()

        conn.execute(
            "UPDATE gardens SET latitude = 59.9, longitude = 10.7 WHERE id = %s",
            (garden_id,),
        )

        conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'frost_warning', 'high', 'Test frost', 'Test desc',
                    CURRENT_DATE::text, (CURRENT_DATE + INTERVAL '7 days')::date::text, '{}', %s)
            """,
            (garden_id, now),
        )
        conn.commit()
        alert_id = conn.execute(
            "SELECT id FROM weather_alerts WHERE garden_id = %s ORDER BY id DESC LIMIT 1",
            (garden_id,),
        ).fetchone()["id"]
        db.return_db(conn)

        self._create_test_user("weather_peer", "weather-peer-pass", role="admin")
        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
        ):
            user_client, user_headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            peer_client, peer_headers = self._authenticated_client(
                "weather_peer",
                "weather-peer-pass",
                garden_id=garden_id,
            )

            initial = user_client.get("/api/weather/alerts", headers=user_headers)
            self.assertEqual(initial.status_code, 200)
            self.assertIn(alert_id, {alert["id"] for alert in initial.json()["alerts"]})

            dismissed = user_client.post(
                f"/api/weather/alerts/{alert_id}/dismiss",
                headers=user_headers,
            )
            self.assertEqual(dismissed.status_code, 200)
            self.assertEqual(dismissed.json()["status"], "dismissed")

            own_alerts = user_client.get("/api/weather/alerts", headers=user_headers)
            peer_alerts = peer_client.get("/api/weather/alerts", headers=peer_headers)
            own_summary = user_client.get("/api/weather/summary", headers=user_headers)
            peer_summary = peer_client.get("/api/weather/summary", headers=peer_headers)

        self.assertNotIn(alert_id, {alert["id"] for alert in own_alerts.json()["alerts"]})
        self.assertIn(alert_id, {alert["id"] for alert in peer_alerts.json()["alerts"]})
        self.assertNotIn(alert_id, {alert["id"] for alert in own_summary.json()["alerts"]})
        self.assertIn(alert_id, {alert["id"] for alert in peer_summary.json()["alerts"]})

        conn = db.get_db()
        try:
            domain_alert = conn.execute(
                "SELECT dismissed FROM weather_alerts WHERE id = %s",
                (alert_id,),
            ).fetchone()
            user_state = conn.execute(
                """
                SELECT user_state
                FROM user_attention_item_state
                WHERE user_id = %s
                  AND garden_id = %s
                  AND item_id = %s
                """,
                (self._owner_id, garden_id, f"attn:weather:alert:{alert_id}"),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert domain_alert is not None
        assert user_state is not None
        self.assertFalse(bool(domain_alert["dismissed"]))
        self.assertEqual(str(user_state["user_state"]), "dismissed")

    def test_weather_alert_dismiss_uses_frozen_attention_timestamp(self) -> None:
        frozen_now_ms = 1_959_379_200_000
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default' LIMIT 1").fetchone()[
                    "id"
                ]
            )
            row = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'frost_warning', 'high', 'Frozen frost', '',
                        '2032-02-03', '2032-02-04', '{}', %s)
                RETURNING id
                """,
                (garden_id, frozen_now_ms),
            ).fetchone()
            conn.commit()
        finally:
            db.return_db(conn)

        assert row is not None
        alert_id = int(row["id"])
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "test",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(frozen_now_ms),
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2032-02-03",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            response = client.post(
                f"/api/weather/alerts/{alert_id}/dismiss",
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)

        conn = db.get_db()
        try:
            state = conn.execute(
                """
                SELECT created_at_ms, updated_at_ms
                FROM user_attention_item_state
                WHERE garden_id = %s
                  AND user_id = %s
                  AND item_id = %s
                """,
                (garden_id, self._owner_id, f"attn:weather:alert:{alert_id}"),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert state is not None
        self.assertEqual(int(state["created_at_ms"]), frozen_now_ms)
        self.assertEqual(int(state["updated_at_ms"]), frozen_now_ms)

    def test_weather_alert_reads_use_frozen_attention_date(self) -> None:
        frozen_now_ms = 1_783_180_800_000
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default' LIMIT 1").fetchone()[
                    "id"
                ]
            )
            row = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'heat_wave', 'high', 'Frozen-date heat', '',
                        '2026-07-05', '2026-07-05', '{}', %s)
                RETURNING id
                """,
                (garden_id, frozen_now_ms),
            ).fetchone()
            conn.commit()
        finally:
            db.return_db(conn)

        assert row is not None
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "test",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(frozen_now_ms),
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            response = self.client.get("/api/weather/alerts")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(int(row["id"]), {alert["id"] for alert in response.json()["alerts"]})

    def test_weather_alerts_hide_legacy_global_dismissals(self) -> None:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default' LIMIT 1").fetchone()[
                    "id"
                ]
            )
            row = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, dismissed, created_at_ms)
                VALUES (%s, 'frost_warning', 'high', 'Legacy dismissal', '',
                        CURRENT_DATE::text, (CURRENT_DATE + INTERVAL '1 day')::date::text,
                        '{}', 1, %s)
                RETURNING id
                """,
                (garden_id, db.current_timestamp_ms()),
            ).fetchone()
            conn.commit()
        finally:
            db.return_db(conn)

        assert row is not None
        response = self.client.get("/api/weather/alerts")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(int(row["id"]), {alert["id"] for alert in response.json()["alerts"]})

    def test_weather_analyze_forecast_frost(self) -> None:
        """Unit test analyze_forecast with frost data."""
        from gardenops.services.weather_service import analyze_forecast as _analyze

        forecast = {
            "daily": {
                "time": ["2026-03-14", "2026-03-15", "2026-03-16"],
                "temperature_2m_min": [-3.0, -1.0, 2.0],
                "temperature_2m_max": [5.0, 4.0, 8.0],
                "precipitation_sum": [0.0, 0.0, 0.0],
            }
        }
        alerts = _analyze(forecast)
        frost_alerts = [a for a in alerts if a["alert_type"] == "frost_warning"]
        self.assertEqual(len(frost_alerts), 1)
        self.assertEqual(frost_alerts[0]["severity"], "normal")
        self.assertIn("frost_days", frost_alerts[0]["metadata"])
        self.assertEqual(frost_alerts[0]["valid_from"], "2026-03-14")

    def test_weather_analyze_forecast_dry(self) -> None:
        """Unit test analyze_forecast with dry spell data."""
        from gardenops.services.weather_service import analyze_forecast as _analyze

        forecast = {
            "daily": {
                "time": [
                    "2026-03-14",
                    "2026-03-15",
                    "2026-03-16",
                    "2026-03-17",
                    "2026-03-18",
                    "2026-03-19",
                    "2026-03-20",
                ],
                "temperature_2m_min": [5.0] * 7,
                "temperature_2m_max": [15.0] * 7,
                "precipitation_sum": [0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0],
            }
        }
        alerts = _analyze(forecast)
        dry_alerts = [a for a in alerts if a["alert_type"] == "dry_spell"]
        self.assertEqual(len(dry_alerts), 1)
        self.assertEqual(dry_alerts[0]["valid_from"], "2026-03-14")

    def test_weather_frost_vulnerable_plants(self) -> None:
        """Test plant vulnerability detection based on hardiness codes."""
        from gardenops.services.weather_service import find_frost_vulnerable_plants

        conn = db.get_db()
        garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        garden_id = int(garden["id"])

        # Create a user for plant ownership
        user = create_user(
            conn,
            username="weather_test_user",
            password=strong_password("weatherpass"),
            role="editor",
        )
        user_id = int(user["id"])

        # PLT-002 has hardiness H5 (min safe temp = -15)
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

        # At -3 degrees, H5 plants (-15 min) should NOT be vulnerable
        vulnerable = find_frost_vulnerable_plants(conn, garden_id, -3.0)
        h5_found = [v for v in vulnerable if v["plt_id"] == "PLT-002"]
        self.assertEqual(len(h5_found), 0)

        # At -20 degrees, H5 plants (-15 min) SHOULD be vulnerable
        vulnerable = find_frost_vulnerable_plants(conn, garden_id, -20.0)
        h5_found = [v for v in vulnerable if v["plt_id"] == "PLT-002"]
        self.assertEqual(len(h5_found), 1)
        self.assertEqual(h5_found[0]["name"], "Rose")

        db.return_db(conn)

    def test_weather_check_retries_downstream_work_for_an_existing_alert(self) -> None:
        """A retry must reconcile notifications and tasks after alert persistence succeeded."""
        from gardenops.services.weather_service import save_weather_alerts

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            today_row = conn.execute("SELECT CURRENT_DATE::text AS current_date").fetchone()
            tomorrow_row = conn.execute(
                "SELECT (CURRENT_DATE + INTERVAL '1 day')::date::text AS tomorrow"
            ).fetchone()
            assert garden is not None
            assert today_row is not None
            assert tomorrow_row is not None
            garden_id = int(garden["id"])
            today = str(today_row["current_date"])
            tomorrow = str(tomorrow_row["tomorrow"])
            alert = {
                "alert_type": "frost_warning",
                "severity": "high",
                "title": "Retry frost warning",
                "description": "Protect vulnerable plants after retry.",
                "valid_from": today,
                "valid_until": tomorrow,
                "metadata": {"coldest": -20.0},
            }
            conn.execute(
                "UPDATE gardens SET latitude = 59.9, longitude = 10.7 WHERE id = %s",
                (garden_id,),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT DO NOTHING
                """,
                (garden_id, self._owner_id),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-002', 1)
                ON CONFLICT DO NOTHING
                """,
            )
            conn.commit()
            save_weather_alerts(conn, garden_id, [alert])
            conn.commit()
        finally:
            db.return_db(conn)

        with patch(
            "gardenops.routers.weather.check_weather_and_generate_alerts",
            return_value={
                "forecast_available": True,
                "alerts_created": 0,
                "alerts_skipped": 1,
                "alerts": [alert],
                "frost_vulnerable_plants": [],
                "watering_sensitive_plants": [],
            },
        ):
            response = self.client.post("/api/weather/check")

        self.assertEqual(response.status_code, 200, response.text)
        conn = db.get_db()
        try:
            notification = conn.execute(
                """
                SELECT 1
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'weather_alert'
                  AND target_type = 'weather_alert'
                  AND target_id = %s
                  AND cleared_at_ms IS NULL
                """,
                (garden_id, self._owner_id, f"frost_warning:{today}"),
            ).fetchone()
            task_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM garden_tasks
                WHERE garden_id = %s
                  AND rule_source LIKE 'auto:frost_protect:%%'
                """,
                (garden_id,),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertIsNotNone(notification)
        assert task_count is not None
        self.assertEqual(int(task_count["count"]), 1)

    def test_weather_dismissal_suppresses_notification_until_escalation_reopens_it(self) -> None:
        from gardenops.services.notification_service import create_weather_alert_notifications
        from gardenops.services.weather_service import save_weather_alerts

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            today_row = conn.execute("SELECT CURRENT_DATE::text AS current_date").fetchone()
            tomorrow_row = conn.execute(
                "SELECT (CURRENT_DATE + INTERVAL '1 day')::date::text AS tomorrow"
            ).fetchone()
            assert garden is not None
            assert today_row is not None
            assert tomorrow_row is not None
            garden_id = int(garden["id"])
            today = str(today_row["current_date"])
            tomorrow = str(tomorrow_row["tomorrow"])
            alert = {
                "alert_type": "frost_warning",
                "severity": "normal",
                "title": "Initial frost warning",
                "description": "Monitor the forecast.",
                "valid_from": today,
                "valid_until": tomorrow,
                "metadata": {"coldest": -1.0},
            }
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT DO NOTHING
                """,
                (garden_id, self._owner_id),
            )
            conn.commit()
            save_weather_alerts(conn, garden_id, [alert])
            created = create_weather_alert_notifications(
                conn,
                garden_id=garden_id,
                alerts=[alert],
            )
            conn.execute(
                """
                UPDATE notification_events
                SET emailed_at_ms = 123
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'weather_alert'
                  AND target_id = %s
                """,
                (garden_id, self._owner_id, f"frost_warning:{today}"),
            )
            conn.commit()
            self.assertEqual(created["created"], 1)
            row = conn.execute(
                """
                SELECT id
                FROM weather_alerts
                WHERE garden_id = %s
                  AND alert_type = 'frost_warning'
                  AND valid_from = %s
                """,
                (garden_id, today),
            ).fetchone()
            assert row is not None
            alert_id = int(row["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            self.assertEqual(
                client.get("/api/notifications/count", headers=headers).json()["count"],
                1,
            )
            dismissed = client.post(
                f"/api/weather/alerts/{alert_id}/dismiss",
                headers=headers,
            )
            self.assertEqual(dismissed.status_code, 200, dismissed.text)
            self.assertEqual(
                client.get("/api/notifications/count", headers=headers).json()["count"],
                0,
            )

            conn = db.get_db()
            try:
                suppressed = create_weather_alert_notifications(
                    conn,
                    garden_id=garden_id,
                    alerts=[alert],
                )
                conn.commit()
                self.assertEqual(suppressed["created"], 0)
                active_after_regeneration = conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM notification_events
                    WHERE garden_id = %s
                      AND user_id = %s
                      AND notification_type = 'weather_alert'
                      AND target_id = %s
                      AND cleared_at_ms IS NULL
                    """,
                    (garden_id, self._owner_id, f"frost_warning:{today}"),
                ).fetchone()
                assert active_after_regeneration is not None
                self.assertEqual(int(active_after_regeneration["count"]), 0)

                escalated = {
                    **alert,
                    "severity": "high",
                    "title": "Escalated frost warning",
                    "description": "Protect plants immediately.",
                    "metadata": {"coldest": -8.0},
                }
                save_weather_alerts(conn, garden_id, [escalated])
                create_weather_alert_notifications(
                    conn,
                    garden_id=garden_id,
                    alerts=[escalated],
                )
                conn.commit()
                notifications = conn.execute(
                    """
                    SELECT title, body, severity, cleared_at_ms, read_at_ms, emailed_at_ms
                    FROM notification_events
                    WHERE garden_id = %s
                      AND user_id = %s
                      AND notification_type = 'weather_alert'
                      AND target_id = %s
                    ORDER BY id
                    """,
                    (garden_id, self._owner_id, f"frost_warning:{today}"),
                ).fetchall()
            finally:
                db.return_db(conn)

            self.assertEqual(len(notifications), 1)
            reopened = notifications[0]
            self.assertEqual(reopened["title"], "Escalated frost warning")
            self.assertEqual(reopened["body"], "Protect plants immediately.")
            self.assertEqual(reopened["severity"], "high")
            self.assertIsNone(reopened["cleared_at_ms"])
            self.assertIsNone(reopened["read_at_ms"])
            self.assertIsNone(reopened["emailed_at_ms"])

    def test_dismissed_weather_notification_does_not_regenerate(self) -> None:
        from gardenops.services.notification_service import (
            create_weather_alert_notifications,
            dismiss_notification,
        )
        from gardenops.services.weather_service import save_weather_alerts

        conn = db.get_db()
        try:
            garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            today_row = conn.execute("SELECT CURRENT_DATE::text AS current_date").fetchone()
            tomorrow_row = conn.execute(
                "SELECT (CURRENT_DATE + INTERVAL '1 day')::date::text AS tomorrow"
            ).fetchone()
            assert garden is not None
            assert today_row is not None
            assert tomorrow_row is not None
            garden_id = int(garden["id"])
            today = str(today_row["current_date"])
            alert = {
                "alert_type": "rain_alert",
                "severity": "normal",
                "title": "Heavy rain",
                "description": "Expect saturated soil.",
                "valid_from": today,
                "valid_until": str(tomorrow_row["tomorrow"]),
                "metadata": {},
            }
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT DO NOTHING
                """,
                (garden_id, self._owner_id),
            )
            conn.commit()
            save_weather_alerts(conn, garden_id, [alert])
            create_weather_alert_notifications(conn, garden_id=garden_id, alerts=[alert])
            conn.commit()
            notification = conn.execute(
                """
                SELECT public_id
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'weather_alert'
                  AND target_id = %s
                """,
                (garden_id, self._owner_id, f"rain_alert:{today}"),
            ).fetchone()
            assert notification is not None
            self.assertTrue(
                dismiss_notification(
                    conn,
                    str(notification["public_id"]),
                    self._owner_id,
                    garden_id,
                )
            )

            retried = create_weather_alert_notifications(
                conn,
                garden_id=garden_id,
                alerts=[alert],
            )
            conn.commit()
            rows = conn.execute(
                """
                SELECT dismissed, cleared_at_ms, severity, read_at_ms, emailed_at_ms
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'weather_alert'
                  AND target_id = %s
                """,
                (garden_id, self._owner_id, f"rain_alert:{today}"),
            ).fetchall()
            self.assertEqual(retried["created"], 0)
            self.assertEqual(len(rows), 1)
            self.assertTrue(bool(rows[0]["dismissed"]))
            self.assertIsNotNone(rows[0]["cleared_at_ms"])

            escalated = {
                **alert,
                "severity": "high",
                "title": "Severe rain",
                "description": "Protect vulnerable beds now.",
            }
            save_weather_alerts(conn, garden_id, [escalated])
            escalated_result = create_weather_alert_notifications(
                conn,
                garden_id=garden_id,
                alerts=[escalated],
            )
            conn.commit()
            reopened = conn.execute(
                """
                SELECT dismissed, cleared_at_ms, severity, read_at_ms, emailed_at_ms
                FROM notification_events
                WHERE garden_id = %s
                  AND user_id = %s
                  AND notification_type = 'weather_alert'
                  AND target_id = %s
                """,
                (garden_id, self._owner_id, f"rain_alert:{today}"),
            ).fetchone()
            assert reopened is not None
            self.assertEqual(escalated_result["created"], 0)
            self.assertFalse(bool(reopened["dismissed"]))
            self.assertIsNone(reopened["cleared_at_ms"])
            self.assertEqual(str(reopened["severity"]), "high")
            self.assertIsNone(reopened["read_at_ms"])
            self.assertIsNone(reopened["emailed_at_ms"])
        finally:
            db.return_db(conn)
