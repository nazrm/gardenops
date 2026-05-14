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

        # Verify it appears in alerts
        r = self.client.get("/api/weather/alerts")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["total"], 0)
        found = [a for a in r.json()["alerts"] if a["id"] == alert_id]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["dismissed"])

        # Dismiss it
        r = self.client.post(f"/api/weather/alerts/{alert_id}/dismiss")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "dismissed")

        # Verify it no longer appears
        r = self.client.get("/api/weather/alerts")
        found = [a for a in r.json()["alerts"] if a["id"] == alert_id]
        self.assertEqual(len(found), 0)

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
