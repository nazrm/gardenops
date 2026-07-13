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
