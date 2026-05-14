"""Unit tests for gardenops.services.weather_service."""

import json
import unittest
from unittest.mock import patch

import pytest

import gardenops.db as db
from gardenops.services.weather_service import (
    _aggregate_met_timeseries,
    _min_temp_for_hardiness,
    _parse_hardiness,
    analyze_forecast,
    check_weather_and_generate_alerts,
    fetch_forecast,
    find_frost_vulnerable_plants,
    save_weather_alerts,
)
from tests.base import strong_password


class TestParseHardiness:
    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("H5", "H5"),
            ("H7", "H7"),
            ("H1", "H1"),
            ("h4", "H4"),
            ("h7", "H7"),
            ("RHS H5 hardy", "H5"),
            ("  h3 zone ", "H3"),
        ],
    )
    def test_valid_hardiness(self, input_val: str, expected: str) -> None:
        assert _parse_hardiness(input_val) == expected

    @pytest.mark.parametrize(
        "input_val",
        [
            "",
            "   ",
            "hardy",
            "zone 5",
            "H0",
            "H8",
        ],
    )
    def test_invalid_hardiness(self, input_val: str) -> None:
        assert _parse_hardiness(input_val) is None


class TestMinTempForHardiness(unittest.TestCase):
    def test_known_values(self) -> None:
        assert _min_temp_for_hardiness("H1") == 15.0
        assert _min_temp_for_hardiness("H4") == -10.0
        assert _min_temp_for_hardiness("H7") == -20.0

    def test_fallback(self) -> None:
        assert _min_temp_for_hardiness("H9") == -20.0
        assert _min_temp_for_hardiness("") == -20.0


class TestAggregateMetTimeseries(unittest.TestCase):
    def _make_entry(
        self,
        time: str,
        temp: float,
        wind: float,
        precip: float,
    ) -> dict:
        return {
            "time": time,
            "data": {
                "instant": {
                    "details": {
                        "air_temperature": temp,
                        "wind_speed": wind,
                    },
                },
                "next_1_hours": {
                    "summary": {"symbol_code": "cloudy"},
                    "details": {"precipitation_amount": precip},
                },
            },
        }

    def test_aggregates_two_days(self) -> None:
        raw = {
            "properties": {
                "timeseries": [
                    self._make_entry("2026-03-16T06:00:00Z", 2.0, 3.0, 0.5),
                    self._make_entry("2026-03-16T12:00:00Z", 8.0, 5.0, 1.0),
                    self._make_entry("2026-03-17T06:00:00Z", -1.0, 7.0, 0.0),
                    self._make_entry("2026-03-17T12:00:00Z", 4.0, 2.0, 0.0),
                ],
            },
        }
        result = _aggregate_met_timeseries(raw)
        daily = result["daily"]
        assert daily["time"] == ["2026-03-16", "2026-03-17"]
        assert daily["temperature_2m_min"] == [2.0, -1.0]
        assert daily["temperature_2m_max"] == [8.0, 4.0]
        assert daily["precipitation_sum"] == [1.5, 0.0]
        assert daily["wind_speed_10m_max"] == [5.0, 7.0]

    def test_empty_timeseries(self) -> None:
        assert _aggregate_met_timeseries({}) == {}
        assert _aggregate_met_timeseries({"properties": {"timeseries": []}}) == {}

    def test_limits_to_seven_days(self) -> None:
        entries = []
        for day in range(1, 12):
            entries.append(
                self._make_entry(f"2026-03-{day:02d}T12:00:00Z", 5.0, 3.0, 0.0),
            )
        raw = {"properties": {"timeseries": entries}}
        result = _aggregate_met_timeseries(raw)
        assert len(result["daily"]["time"]) == 7


class TestFetchForecast(unittest.TestCase):
    @patch("gardenops.services.weather_service.urllib.request.urlopen")
    def test_uses_met_compact_query_url(
        self,
        mock_urlopen: unittest.mock.MagicMock,
    ) -> None:
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "properties": {
                    "timeseries": [
                        {
                            "time": "2026-03-16T12:00:00Z",
                            "data": {
                                "instant": {
                                    "details": {
                                        "air_temperature": 8.0,
                                        "wind_speed": 5.0,
                                    },
                                },
                                "next_1_hours": {
                                    "details": {"precipitation_amount": 0.0},
                                },
                            },
                        },
                    ],
                },
            },
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = fetch_forecast(60.123456, 5.987654)

        assert result["daily"]["time"] == ["2026-03-16"]
        request = mock_urlopen.call_args.args[0]
        assert request.full_url == (
            "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=60.1235&lon=5.9877"
        )
        assert request.get_header("User-agent") == "gardenops/1.0 weather-service"


class TestAnalyzeForecast(unittest.TestCase):
    def test_frost_detection_low_severity(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-03-15", "2026-03-16", "2026-03-17"],
                "temperature_2m_min": [2.0, -1.0, 3.0],
                "temperature_2m_max": [10.0, 5.0, 12.0],
                "precipitation_sum": [0.0, 0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        frost = [a for a in alerts if a["alert_type"] == "frost_warning"]
        assert len(frost) == 1
        assert frost[0]["severity"] == "low"

    def test_frost_detection_high_severity(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-01-10", "2026-01-11"],
                "temperature_2m_min": [-8.0, -3.0],
                "temperature_2m_max": [0.0, 2.0],
                "precipitation_sum": [0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        frost = [a for a in alerts if a["alert_type"] == "frost_warning"]
        assert len(frost) == 1
        assert frost[0]["severity"] == "high"

    def test_frost_detection_normal_severity(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-01-10"],
                "temperature_2m_min": [-3.0],
                "temperature_2m_max": [5.0],
                "precipitation_sum": [0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        frost = [a for a in alerts if a["alert_type"] == "frost_warning"]
        assert len(frost) == 1
        assert frost[0]["severity"] == "normal"

    def test_no_frost(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-07-01", "2026-07-02"],
                "temperature_2m_min": [15.0, 16.0],
                "temperature_2m_max": [25.0, 26.0],
                "precipitation_sum": [0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        frost = [a for a in alerts if a["alert_type"] == "frost_warning"]
        assert len(frost) == 0

    def test_heat_wave_detection(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-07-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [20.0] * 7,
                "temperature_2m_max": [32.0, 33.0, 31.0, 25.0, 20.0, 18.0, 19.0],
                "precipitation_sum": [0.0] * 7,
            },
        }
        alerts = analyze_forecast(forecast)
        heat = [a for a in alerts if a["alert_type"] == "heat_wave"]
        assert len(heat) == 1
        assert heat[0]["severity"] == "normal"
        assert heat[0]["metadata"]["days"] == 3

    def test_heat_wave_high_severity(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-07-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [22.0] * 7,
                "temperature_2m_max": [36.0, 37.0, 35.0, 30.0, 25.0, 20.0, 19.0],
                "precipitation_sum": [0.0] * 7,
            },
        }
        alerts = analyze_forecast(forecast)
        heat = [a for a in alerts if a["alert_type"] == "heat_wave"]
        assert len(heat) == 1
        assert heat[0]["severity"] == "high"

    def test_heat_wave_trailing_streak(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-07-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [20.0] * 7,
                "temperature_2m_max": [20.0, 20.0, 20.0, 20.0, 31.0, 32.0, 33.0],
                "precipitation_sum": [0.0] * 7,
            },
        }
        alerts = analyze_forecast(forecast)
        heat = [a for a in alerts if a["alert_type"] == "heat_wave"]
        assert len(heat) == 1

    def test_no_heat_wave_short_streak(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "temperature_2m_min": [20.0, 20.0, 20.0],
                "temperature_2m_max": [31.0, 31.0, 25.0],
                "precipitation_sum": [0.0, 0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        heat = [a for a in alerts if a["alert_type"] == "heat_wave"]
        assert len(heat) == 0

    def test_dry_spell_detection(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-07-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [15.0] * 7,
                "temperature_2m_max": [25.0] * 7,
                "precipitation_sum": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0],
            },
        }
        alerts = analyze_forecast(forecast)
        dry = [a for a in alerts if a["alert_type"] == "dry_spell"]
        assert len(dry) == 1
        assert dry[0]["severity"] == "normal"
        assert dry[0]["metadata"]["days"] == 6

    def test_dry_spell_high_severity(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-07-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [15.0] * 7,
                "temperature_2m_max": [25.0] * 7,
                "precipitation_sum": [0.0] * 7,
            },
        }
        alerts = analyze_forecast(forecast)
        dry = [a for a in alerts if a["alert_type"] == "dry_spell"]
        assert len(dry) == 1
        assert dry[0]["severity"] == "high"

    def test_rain_surplus_detection(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-06-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [10.0] * 7,
                "temperature_2m_max": [20.0] * 7,
                "precipitation_sum": [5.0, 6.0, 7.0, 0.0, 0.0, 0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        rain = [a for a in alerts if a["alert_type"] == "rain_surplus"]
        assert len(rain) == 1
        assert rain[0]["severity"] == "normal"

    def test_rain_surplus_high_severity(self) -> None:
        forecast = {
            "daily": {
                "time": [f"2026-06-{d:02d}" for d in range(1, 8)],
                "temperature_2m_min": [10.0] * 7,
                "temperature_2m_max": [20.0] * 7,
                "precipitation_sum": [12.0, 11.0, 10.0, 0.0, 0.0, 0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        rain = [a for a in alerts if a["alert_type"] == "rain_surplus"]
        assert len(rain) == 1
        assert rain[0]["severity"] == "high"

    def test_empty_forecast(self) -> None:
        assert analyze_forecast({}) == []
        assert analyze_forecast({"daily": {}}) == []
        assert analyze_forecast({"daily": {"time": []}}) == []

    def test_none_values_in_data(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-03-15", "2026-03-16"],
                "temperature_2m_min": [None, 5.0],
                "temperature_2m_max": [None, 20.0],
                "precipitation_sum": [None, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        frost = [a for a in alerts if a["alert_type"] == "frost_warning"]
        assert len(frost) == 0


class _WeatherDbTestBase(unittest.TestCase):
    """Base for tests that need a real Postgres DB with the full schema."""

    def setUp(self) -> None:
        # Truncate all tables for isolation
        conn = db.get_db()
        try:
            rows = conn.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename != 'schema_migrations'
                """
            ).fetchall()
            tables = [row["tablename"] for row in rows]
            if tables:
                conn.execute("TRUNCATE {} CASCADE".format(", ".join(tables)))
            db.ensure_default_garden(conn)
            conn.commit()
        finally:
            db.return_db(conn)
        self.conn = db.get_db()
        self.garden_id = self._get_garden_id()

    def tearDown(self) -> None:
        db.return_db(self.conn)

    def _get_garden_id(self) -> int:
        row = self.conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert row is not None
        return int(row["id"])


class TestFindFrostVulnerablePlants(_WeatherDbTestBase):
    def _ensure_owner_user(self) -> int:
        row = self.conn.execute("SELECT id FROM auth_users LIMIT 1").fetchone()
        if row:
            return int(row["id"])
        from gardenops.security import create_user

        user = create_user(
            self.conn,
            username="testowner",
            password=strong_password("testpassword123"),
            role="admin",
        )
        self.conn.commit()
        return int(user["id"])

    def setUp(self) -> None:
        super().setUp()
        self._owner_id = self._ensure_owner_user()

    def _insert_plant(
        self,
        plt_id: str,
        name: str,
        hardiness: str,
        category: str = "busker",
    ) -> None:
        self.conn.execute(
            "INSERT INTO plants "
            "(plt_id, name, latin, category, bloom_month, "
            "color, hardiness, height_cm, light, link) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (plt_id, name, "", category, "", "", hardiness, None, "", ""),
        )
        self.conn.execute(
            "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (plt_id, self._owner_id, self.garden_id),
        )
        self.conn.commit()

    def test_finds_vulnerable_h2_plant(self) -> None:
        self._insert_plant("P1", "Tender plant", "H2")
        result = find_frost_vulnerable_plants(self.conn, self.garden_id, -3.0)
        assert len(result) == 1
        assert result[0]["plt_id"] == "P1"
        assert result[0]["min_safe_temp"] == 1.0

    def test_skips_fully_hardy_plant(self) -> None:
        self._insert_plant("P2", "Hardy plant", "H7")
        result = find_frost_vulnerable_plants(self.conn, self.garden_id, -15.0)
        assert len(result) == 0

    def test_mixed_hardiness(self) -> None:
        self._insert_plant("P3", "Tender", "H1")
        self._insert_plant("P4", "Hardy", "H6")
        result = find_frost_vulnerable_plants(self.conn, self.garden_id, -12.0)
        ids = {r["plt_id"] for r in result}
        assert "P3" in ids
        assert "P4" not in ids

    def test_empty_hardiness_skipped(self) -> None:
        self._insert_plant("P5", "No hardiness", "")
        result = find_frost_vulnerable_plants(self.conn, self.garden_id, -5.0)
        assert len(result) == 0


class TestSaveWeatherAlerts(_WeatherDbTestBase):
    def test_creates_alerts(self) -> None:
        alerts = [
            {
                "alert_type": "frost_warning",
                "severity": "high",
                "title": "Frost",
                "description": "Cold snap",
                "valid_from": "2026-01-10",
                "valid_until": "2026-01-11",
                "metadata": {"coldest": -8.0},
            },
        ]
        result = save_weather_alerts(self.conn, self.garden_id, alerts)
        assert result["created"] == 1
        assert result["skipped"] == 0

    def test_deduplication(self) -> None:
        alerts = [
            {
                "alert_type": "frost_warning",
                "severity": "high",
                "title": "Frost",
                "description": "Cold snap",
                "valid_from": "2026-01-10",
                "valid_until": "2026-01-11",
                "metadata": {},
            },
        ]
        save_weather_alerts(self.conn, self.garden_id, alerts)
        result = save_weather_alerts(self.conn, self.garden_id, alerts)
        assert result["created"] == 0
        assert result["skipped"] == 1

    def test_dismissed_alert_still_deduplicates_same_weather_window(self) -> None:
        alerts = [
            {
                "alert_type": "dry_spell",
                "severity": "normal",
                "title": "Dry spell",
                "description": "Water regularly",
                "valid_from": "2026-07-01",
                "valid_until": "2026-07-06",
                "metadata": {},
            },
        ]
        save_weather_alerts(self.conn, self.garden_id, alerts)
        self.conn.execute(
            """
            UPDATE weather_alerts
            SET dismissed = 1
            WHERE garden_id = %s
              AND alert_type = 'dry_spell'
              AND valid_from = '2026-07-01'
            """,
            (self.garden_id,),
        )
        self.conn.commit()

        result = save_weather_alerts(self.conn, self.garden_id, alerts)
        assert result["created"] == 0
        assert result["skipped"] == 1

        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM weather_alerts
            WHERE garden_id = %s
              AND alert_type = 'dry_spell'
              AND valid_from = '2026-07-01'
            """,
            (self.garden_id,),
        ).fetchone()
        assert row is not None
        assert int(row["c"]) == 1


class TestSaveWeatherAlertsPlantAdvice(_WeatherDbTestBase):
    def _make_frost_alert(self) -> dict:
        return {
            "alert_type": "frost_warning",
            "severity": "high",
            "title": "Frost",
            "description": "Cold snap",
            "valid_from": "2026-01-10",
            "valid_until": "2026-01-11",
            "metadata": {
                "frost_days": [("2026-01-10", -8.0)],
                "coldest": -8.0,
                "coldest_date": "2026-01-10",
            },
        }

    def _make_dry_alert(self) -> dict:
        return {
            "alert_type": "dry_spell",
            "severity": "normal",
            "title": "Dry spell",
            "description": "No rain",
            "valid_from": "2026-07-01",
            "valid_until": "2026-07-06",
            "metadata": {"days": 6},
        }

    def test_frost_alert_includes_plant_advice(self) -> None:
        self.conn.execute(
            "INSERT INTO plants (plt_id, name, latin, category, bloom_month,"
            " color, hardiness, height_cm, light, link)"
            " VALUES ('P1', 'Tender Plant', '', 'busker', '', '', 'H2', NULL, '', '')",
        )
        self.conn.commit()
        frost_plants = [
            {"plt_id": "P1", "name": "Tender Plant", "hardiness": "H2", "min_safe_temp": 1.0},
        ]
        result = save_weather_alerts(
            self.conn,
            self.garden_id,
            [self._make_frost_alert()],
            frost_plants=frost_plants,
        )
        assert result["created"] == 1
        row = self.conn.execute(
            "SELECT metadata_json FROM weather_alerts WHERE alert_type = 'frost_warning'",
        ).fetchone()
        assert row is not None
        meta = json.loads(row["metadata_json"])
        assert "plant_advice" in meta
        assert len(meta["plant_advice"]) == 1
        advice = meta["plant_advice"][0]
        assert advice["plt_id"] == "P1"
        assert advice["name"] == "Tender Plant"
        assert advice["hardiness"] == "H2"
        assert advice["min_safe_temp"] == 1.0

    def test_dry_spell_includes_watering_plant_advice(self) -> None:
        self.conn.execute(
            "INSERT INTO plants (plt_id, name, latin, category, bloom_month,"
            " color, hardiness, height_cm, light, link)"
            " VALUES ('P2', 'Thirsty Plant', '', 'busker', '', '', '', NULL, '', '')",
        )
        self.conn.commit()
        watering_plants = [
            {"plt_id": "P2", "name": "Thirsty Plant", "care_watering": "Water regularly"},
        ]
        result = save_weather_alerts(
            self.conn,
            self.garden_id,
            [self._make_dry_alert()],
            watering_plants=watering_plants,
        )
        assert result["created"] == 1
        row = self.conn.execute(
            "SELECT metadata_json FROM weather_alerts WHERE alert_type = 'dry_spell'",
        ).fetchone()
        assert row is not None
        meta = json.loads(row["metadata_json"])
        assert "plant_advice" in meta
        assert len(meta["plant_advice"]) == 1
        advice = meta["plant_advice"][0]
        assert advice["plt_id"] == "P2"
        assert advice["name"] == "Thirsty Plant"
        assert advice["care_watering"] == "Water regularly"

    def test_coldest_date_in_frost_metadata(self) -> None:
        forecast = {
            "daily": {
                "time": ["2026-01-10", "2026-01-11"],
                "temperature_2m_min": [-8.0, -3.0],
                "temperature_2m_max": [0.0, 2.0],
                "precipitation_sum": [0.0, 0.0],
            },
        }
        alerts = analyze_forecast(forecast)
        frost = [a for a in alerts if a["alert_type"] == "frost_warning"]
        assert len(frost) == 1
        assert "coldest_date" in frost[0]["metadata"]
        assert frost[0]["metadata"]["coldest_date"] == "2026-01-10"


class TestCheckWeatherEndToEnd(_WeatherDbTestBase):
    @patch("gardenops.services.weather_service.get_or_fetch_forecast")
    def test_full_pipeline_with_frost(self, mock_fetch: unittest.mock.MagicMock) -> None:
        mock_fetch.return_value = {
            "daily": {
                "time": ["2026-01-10", "2026-01-11"],
                "temperature_2m_min": [-8.0, -3.0],
                "temperature_2m_max": [0.0, 2.0],
                "precipitation_sum": [0.0, 0.0],
            },
        }
        result = check_weather_and_generate_alerts(
            self.conn,
            self.garden_id,
            59.91,
            10.75,
        )
        assert result["forecast_available"] is True
        assert result["alerts_created"] >= 1
        found_types = {a["alert_type"] for a in result["alerts"]}
        assert "frost_warning" in found_types

    @patch("gardenops.services.weather_service.get_or_fetch_forecast")
    def test_empty_forecast(self, mock_fetch: unittest.mock.MagicMock) -> None:
        mock_fetch.return_value = {}
        result = check_weather_and_generate_alerts(
            self.conn,
            self.garden_id,
            59.91,
            10.75,
        )
        assert result["forecast_available"] is False
        assert result["alerts_created"] == 0


if __name__ == "__main__":
    unittest.main()
