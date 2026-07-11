"""Unit tests for gardenops.services.weather_service."""

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import gardenops.db as db
from gardenops.routers.weather import _load_active_alerts
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
from tests.base import DbTestBase, strong_password


class _ReadCountingConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.read_query_count = 0
        self.read_queries: list[tuple[str, Any]] = []

    def execute(self, query: Any, params: Any = None) -> Any:
        if str(query).lstrip().upper().startswith("SELECT"):
            self.read_query_count += 1
            self.read_queries.append((str(query), params))
        return self._connection.execute(query, params)


class _InsertBarrierConnection:
    def __init__(self, connection: Any, barrier: threading.Barrier) -> None:
        self._connection = connection
        self._barrier = barrier

    def execute(self, query: Any, params: Any = None) -> Any:
        if "INSERT INTO weather_alerts" in str(query):
            self._barrier.wait(timeout=10)
        return self._connection.execute(query, params)

    def commit(self) -> None:
        self._connection.commit()


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


class TestLoadActiveAlerts(_WeatherDbTestBase):
    def _insert_active_alert(
        self,
        alert_type: str,
        valid_from_offset: int,
        plant_ids: list[str],
    ) -> int:
        row = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (
                %s, %s, 'normal', %s, '',
                (CURRENT_DATE + %s::integer)::text,
                (CURRENT_DATE + %s::integer)::text,
                '{}', %s
            )
            RETURNING id
            """,
            (
                self.garden_id,
                alert_type,
                alert_type,
                valid_from_offset,
                valid_from_offset + 10,
                db.current_timestamp_ms(),
            ),
        ).fetchone()
        assert row is not None
        alert_id = int(row["id"])

        for plant_id in plant_ids:
            self.conn.execute(
                """
                INSERT INTO plants
                    (plt_id, name, latin, category, bloom_month, color,
                     hardiness, height_cm, light, link)
                VALUES (%s, %s, '', 'busker', '', '', '', NULL, '', '')
                """,
                (plant_id, plant_id),
            )
            self.conn.execute(
                "INSERT INTO weather_alert_plants (alert_id, plt_id) VALUES (%s, %s)",
                (alert_id, plant_id),
            )
        self.conn.commit()
        return alert_id

    def test_load_active_alerts_batches_link_reads_and_orders_plant_ids(self) -> None:
        first_alert_id = self._insert_active_alert(
            "weather-query-count-one",
            0,
            ["WALERT-ONE-Z", "WALERT-ONE-A"],
        )

        one_connection = _ReadCountingConnection(self.conn)
        one_alerts = _load_active_alerts(one_connection, self.garden_id)

        assert [int(alert["id"]) for alert in one_alerts] == [first_alert_id]
        assert one_alerts[0]["plant_ids"] == ["WALERT-ONE-A", "WALERT-ONE-Z"]
        assert one_connection.read_query_count == 2
        plant_query = next(
            query
            for query, _ in one_connection.read_queries
            if "FROM weather_alert_plants" in query
        )
        assert "alert_id = ANY(%s)" in plant_query

        additional_alert_ids = [
            self._insert_active_alert(
                f"weather-query-count-{number}",
                number,
                [f"WALERT-{number}-Z", f"WALERT-{number}-A"],
            )
            for number in range(1, 6)
        ]

        many_connection = _ReadCountingConnection(self.conn)
        many_alerts = _load_active_alerts(many_connection, self.garden_id)

        assert [int(alert["id"]) for alert in many_alerts] == [
            first_alert_id,
            *additional_alert_ids,
        ]
        assert many_connection.read_query_count == one_connection.read_query_count == 2
        assert all(alert["plant_ids"] == sorted(alert["plant_ids"]) for alert in many_alerts)


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


class TestWeatherAlertIdentityMigration(_WeatherDbTestBase):
    def test_migration_normalizes_valid_non_object_metadata(self) -> None:
        migration_sql = (
            Path(__file__).resolve().parents[1] / "migrations" / "0021_weather_alert_identity.sql"
        ).read_text(encoding="utf-8")
        try:
            self.conn.execute("DROP INDEX IF EXISTS public.ux_weather_alerts_identity")
            for index, metadata in enumerate(("[]", "null", '"legacy"', "not-json")):
                self.conn.execute(
                    """
                    INSERT INTO weather_alerts (
                        garden_id, alert_type, severity, title, description,
                        valid_from, valid_until, metadata_json, created_at_ms
                    )
                    VALUES (%s, 'dry_spell', 'normal', 'Legacy', '',
                            %s, %s, %s, %s)
                    """,
                    (
                        self.garden_id,
                        f"2040-01-{index + 1:02d}",
                        f"2040-01-{index + 2:02d}",
                        metadata,
                        db.current_timestamp_ms() + index,
                    ),
                )
            self.conn.execute(migration_sql)
            self.conn.commit()

            rows = self.conn.execute(
                """
                SELECT metadata_json
                FROM weather_alerts
                WHERE garden_id = %s AND title = 'Legacy'
                ORDER BY valid_from
                """,
                (self.garden_id,),
            ).fetchall()
            assert [row["metadata_json"] for row in rows] == ["{}", "{}", "{}", "{}"]
        finally:
            self.conn.rollback()

    def test_migration_consolidates_state_and_rehomes_dependents(self) -> None:
        from gardenops.security import create_user

        user = create_user(
            self.conn,
            username="weather_migration_user",
            password=strong_password("weather-migration-password"),
            role="admin",
        )
        self.conn.commit()
        migration_sql = (
            Path(__file__).resolve().parents[1] / "migrations" / "0021_weather_alert_identity.sql"
        ).read_text(encoding="utf-8")

        try:
            self.conn.execute("DROP INDEX IF EXISTS public.ux_weather_alerts_identity")
            for plant_id in ("MIG-KEEP", "MIG-SHARED", "MIG-MOVE"):
                self.conn.execute(
                    """
                    INSERT INTO plants
                        (plt_id, name, latin, category, bloom_month, color,
                         hardiness, height_cm, light, link)
                    VALUES (%s, %s, '', 'busker', '', '', '', NULL, '', '')
                    """,
                    (plant_id, plant_id),
                )

            first = self.conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'frost_warning', 'low', 'First', '',
                        '2035-01-01', '2035-01-02',
                        '{"old":"kept","shared":"first",'
                        '"plant_advice":[{"plt_id":"MIG-KEEP"}]}', %s)
                RETURNING id
                """,
                (self.garden_id, db.current_timestamp_ms()),
            ).fetchone()
            second = self.conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'frost_warning', 'high', 'Second', '',
                        '2035-01-01', '2035-01-04',
                        '{"new":"kept","shared":"second",'
                        '"plant_advice":[{"plt_id":"MIG-MOVE"}]}', %s)
                RETURNING id
                """,
                (self.garden_id, db.current_timestamp_ms()),
            ).fetchone()
            assert first is not None
            assert second is not None
            first_id = int(first["id"])
            second_id = int(second["id"])
            self.conn.execute(
                "UPDATE weather_alerts SET dismissed = 1 WHERE id = %s",
                (first_id,),
            )

            for alert_id, plant_id in (
                (first_id, "MIG-KEEP"),
                (first_id, "MIG-SHARED"),
                (second_id, "MIG-SHARED"),
                (second_id, "MIG-MOVE"),
            ):
                self.conn.execute(
                    "INSERT INTO weather_alert_plants (alert_id, plt_id) VALUES (%s, %s)",
                    (alert_id, plant_id),
                )

            task = self.conn.execute(
                """
                INSERT INTO garden_tasks (
                    garden_id, task_type, title, description, status, severity,
                    due_on, rule_source, metadata_json, created_at_ms, updated_at_ms
                )
                VALUES (%s, 'protect', 'Protect', '', 'pending', 'high',
                        '2035-01-01', %s, '{}', %s, %s)
                RETURNING id
                """,
                (
                    self.garden_id,
                    f"auto:frost_protect:{first_id}:MIG-KEEP",
                    db.current_timestamp_ms(),
                    db.current_timestamp_ms(),
                ),
            ).fetchone()
            assert task is not None

            outcome = self.conn.execute(
                """
                INSERT INTO attention_outcomes (
                    public_id, garden_id, provider, outcome_type, source_type,
                    source_id, source_public_id, title, explanation, target_type,
                    target_id, metadata_json, occurred_at_ms, expires_at_ms,
                    created_at_ms, updated_at_ms
                )
                VALUES (
                    'attnout_migration_weather', %s, 'weather',
                    'watering_covered_by_rain', 'task_generator', %s,
                    'water:MIG-KEEP:2035-01-01', 'Covered', 'Covered by rain',
                    'plant', 'MIG-KEEP', %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    self.garden_id,
                    str(first_id),
                    json.dumps({"weather_alert_id": str(first_id)}),
                    db.current_timestamp_ms(),
                    db.current_timestamp_ms() + 86_400_000,
                    db.current_timestamp_ms(),
                    db.current_timestamp_ms(),
                ),
            ).fetchone()
            assert outcome is not None

            self.conn.execute(
                """
                INSERT INTO user_attention_item_state (
                    user_id, garden_id, item_id, user_state, reason,
                    metadata_json, created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, %s, 'dismissed', 'reviewed', '{}', %s, %s)
                """,
                (
                    int(user["id"]),
                    self.garden_id,
                    f"attn:weather:alert:{first_id}",
                    db.current_timestamp_ms(),
                    db.current_timestamp_ms(),
                ),
            )

            self.conn.execute(migration_sql)

            alert_rows = self.conn.execute(
                """
                SELECT id, severity, valid_until, dismissed, metadata_json
                FROM weather_alerts
                WHERE garden_id = %s
                  AND alert_type = 'frost_warning'
                  AND valid_from = '2035-01-01'
                """,
                (self.garden_id,),
            ).fetchall()
            assert [int(row["id"]) for row in alert_rows] == [second_id]
            alert_row = alert_rows[0]
            assert str(alert_row["severity"]) == "high"
            assert str(alert_row["valid_until"]) == "2035-01-04"
            assert int(alert_row["dismissed"]) == 0
            alert_metadata = json.loads(str(alert_row["metadata_json"]))
            assert alert_metadata["old"] == "kept"
            assert alert_metadata["new"] == "kept"
            assert alert_metadata["shared"] == "second"
            assert {item["plt_id"] for item in alert_metadata["plant_advice"]} == {
                "MIG-KEEP",
                "MIG-MOVE",
            }

            plant_rows = self.conn.execute(
                "SELECT plt_id FROM weather_alert_plants WHERE alert_id = %s ORDER BY plt_id",
                (second_id,),
            ).fetchall()
            assert [str(row["plt_id"]) for row in plant_rows] == [
                "MIG-KEEP",
                "MIG-MOVE",
                "MIG-SHARED",
            ]

            task_row = self.conn.execute(
                "SELECT rule_source FROM garden_tasks WHERE id = %s",
                (int(task["id"]),),
            ).fetchone()
            assert task_row is not None
            assert str(task_row["rule_source"]) == (
                f"auto:frost_protect:{second_id}:MIG-KEEP"
            )

            outcome_row = self.conn.execute(
                "SELECT source_id, metadata_json FROM attention_outcomes WHERE id = %s",
                (int(outcome["id"]),),
            ).fetchone()
            assert outcome_row is not None
            assert str(outcome_row["source_id"]) == str(second_id)
            assert json.loads(str(outcome_row["metadata_json"]))["weather_alert_id"] == str(
                second_id
            )

            attention_state = self.conn.execute(
                """
                SELECT item_id, user_state
                FROM user_attention_item_state
                WHERE user_id = %s AND garden_id = %s
                """,
                (int(user["id"]), self.garden_id),
            ).fetchone()
            assert attention_state is not None
            assert str(attention_state["item_id"]) == f"attn:weather:alert:{second_id}"
            assert str(attention_state["user_state"]) == "dismissed"

            conflict = self.conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'frost_warning', 'normal', 'Duplicate', '',
                        '2035-01-01', '2035-01-02', '{}', %s)
                ON CONFLICT (garden_id, alert_type, valid_from) DO NOTHING
                RETURNING id
                """,
                (self.garden_id, db.current_timestamp_ms()),
            ).fetchone()
            assert conflict is None
        finally:
            self.conn.rollback()


class TestWeatherAlertIdentityConcurrency(DbTestBase):
    def test_concurrent_creators_share_one_alert_and_its_links(self) -> None:
        self._insert_plant("RACE-PLANT-A", "Race plant A", hardiness="H2")
        self._insert_plant("RACE-PLANT-B", "Race plant B", hardiness="H2")
        alert = {
            "alert_type": "frost_warning",
            "severity": "high",
            "title": "Frost",
            "description": "Cold snap",
            "valid_from": "2035-02-03",
            "valid_until": "2035-02-04",
            "metadata": {"coldest": -8.0},
        }
        insert_barrier = threading.Barrier(2)

        def save_once(index: int) -> tuple[dict[str, int], int]:
            conn = db.get_db()
            try:
                suffix = "A" if index == 0 else "B"
                frost_plants = [
                    {
                        "plt_id": f"RACE-PLANT-{suffix}",
                        "name": f"Race plant {suffix}",
                        "hardiness": "H2",
                        "min_safe_temp": 1.0,
                    },
                ]
                result = save_weather_alerts(
                    _InsertBarrierConnection(conn, insert_barrier),
                    self.garden_id,
                    [alert],
                    frost_plants=frost_plants,
                )
                return result, conn.info.backend_pid
            finally:
                db.return_db(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save_once, range(2)))

        results = [result for result, _ in outcomes]
        backend_pids = {backend_pid for _, backend_pid in outcomes}
        assert len(backend_pids) == 2
        assert sorted(result["created"] for result in results) == [0, 1]
        assert sorted(result["skipped"] for result in results) == [0, 1]

        alert_rows = self.conn.execute(
            """
            SELECT id
            FROM weather_alerts
            WHERE garden_id = %s
              AND alert_type = 'frost_warning'
              AND valid_from = '2035-02-03'
            """,
            (self.garden_id,),
        ).fetchall()
        assert len(alert_rows) == 1
        alert_id = int(alert_rows[0]["id"])
        plant_rows = self.conn.execute(
            "SELECT plt_id FROM weather_alert_plants WHERE alert_id = %s ORDER BY plt_id",
            (alert_id,),
        ).fetchall()
        assert [str(row["plt_id"]) for row in plant_rows] == [
            "RACE-PLANT-A",
            "RACE-PLANT-B",
        ]
        metadata_row = self.conn.execute(
            "SELECT metadata_json FROM weather_alerts WHERE id = %s",
            (alert_id,),
        ).fetchone()
        assert metadata_row is not None
        metadata = json.loads(str(metadata_row["metadata_json"]))
        assert {item["plt_id"] for item in metadata["plant_advice"]} == {
            "RACE-PLANT-A",
            "RACE-PLANT-B",
        }


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
