"""Tests for scheduler-integrated automation: weather checks and task gen."""

import unittest
from unittest.mock import patch

import gardenops.db as db
from gardenops.services.notification_service import (
    _auto_generate_monthly_tasks,
    _run_weather_check_if_due,
    notification_rules_json,
    run_notification_maintenance_once,
)
from tests.base import DbTestBase


class TestWeatherCheckCooldown(DbTestBase):
    def test_weather_check_respects_cooldown(self) -> None:
        now_ms = db.current_timestamp_ms()
        recent_ms = now_ms - 60 * 60 * 1000  # 1 hour ago
        self.conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s)",
            (f"last_weather_check_ms:{self.garden_id}", str(recent_ms)),
        )
        self.conn.commit()

        result = _run_weather_check_if_due(self.conn, self.garden_id, now_ms)
        assert result.get("weather_skipped") is True
        assert "weather_checks" not in result


class TestWeatherCheckNoLocation(DbTestBase):
    def test_weather_check_skips_without_location(self) -> None:
        row = self.conn.execute(
            "SELECT latitude, longitude FROM gardens WHERE id = %s",
            (self.garden_id,),
        ).fetchone()
        assert row["latitude"] is None

        now_ms = db.current_timestamp_ms()
        result = _run_weather_check_if_due(self.conn, self.garden_id, now_ms)
        assert result.get("weather_skipped") is True


class TestWeatherCheckRunsAfterCooldown(DbTestBase):
    @patch("gardenops.services.notification_service.check_weather_and_generate_alerts")
    def test_weather_check_runs_when_cooldown_expired(self, mock_check) -> None:
        mock_check.return_value = {
            "forecast_available": True,
            "alerts_created": 1,
            "alerts_skipped": 0,
            "alerts": [
                {
                    "alert_type": "dry_spell",
                    "severity": "normal",
                    "title": "Dry spell",
                    "description": "No rain",
                    "valid_from": "2026-03-20",
                    "valid_until": "2026-03-25",
                    "metadata": {},
                },
            ],
            "frost_vulnerable_plants": [],
            "watering_sensitive_plants": [],
        }

        self.conn.execute(
            "UPDATE gardens SET latitude = 59.9, longitude = 10.7 WHERE id = %s",
            (self.garden_id,),
        )
        self.conn.commit()

        now_ms = db.current_timestamp_ms()
        old_ms = now_ms - 4 * 60 * 60 * 1000  # 4 hours ago
        self.conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s)",
            (f"last_weather_check_ms:{self.garden_id}", str(old_ms)),
        )
        self.conn.commit()

        result = _run_weather_check_if_due(self.conn, self.garden_id, now_ms)
        assert result.get("weather_checks") == 1
        assert result.get("weather_alerts_created") == 1
        mock_check.assert_called_once_with(self.conn, self.garden_id, 59.9, 10.7)


class TestMonthlyTaskGen(DbTestBase):
    def test_monthly_task_gen_runs_once_per_month(self) -> None:
        self._insert_plant(
            "WP1",
            "Thirsty Rose",
            care_watering="regular",
        )
        # Use a July timestamp so the water rule fires
        # 2026-07-15 12:00:00 UTC
        july_ms = 1784116800000
        result1 = _auto_generate_monthly_tasks(
            self.conn,
            self.garden_id,
            july_ms,
        )
        assert result1.get("tasks_created", 0) > 0
        assert result1.get("tasks_skipped") is not True

        result2 = _auto_generate_monthly_tasks(
            self.conn,
            self.garden_id,
            july_ms + 1000,
        )
        assert result2.get("tasks_skipped") is True

    def test_monthly_task_gen_creates_notification(self) -> None:
        self._insert_plant(
            "WP2",
            "Water Me",
            care_watering="regular",
        )
        # Ensure garden membership exists
        self.conn.execute(
            """
            INSERT INTO garden_memberships
                (garden_id, user_id, role)
            VALUES (%s, %s, 'admin') ON CONFLICT DO NOTHING
            """,
            (self.garden_id, self._owner_id),
        )
        now_ms = db.current_timestamp_ms()
        self.conn.execute(
            """
            INSERT INTO user_notification_preferences
                (user_id, rules_json, created_at_ms, updated_at_ms)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                rules_json = excluded.rules_json,
                updated_at_ms = excluded.updated_at_ms
            """,
            (
                self._owner_id,
                notification_rules_json(
                    {
                        "task_generated": {
                            "in_app_enabled": True,
                            "email_enabled": False,
                            "min_severity": "normal",
                        },
                    }
                ),
                now_ms,
                now_ms,
            ),
        )
        self.conn.commit()

        july_ms = 1784116800000
        result = _auto_generate_monthly_tasks(
            self.conn,
            self.garden_id,
            july_ms,
        )
        assert result.get("tasks_created", 0) > 0
        assert result.get("notifications_created", 0) >= 1

        notifications = self.conn.execute(
            """
            SELECT * FROM notification_events
            WHERE garden_id = %s AND notification_type = 'task_generated'
            """,
            (self.garden_id,),
        ).fetchall()
        assert len(notifications) >= 1
        assert "seasonal tasks generated" in notifications[0]["title"]


class TestRunMaintenanceIncludes(DbTestBase):
    def test_run_maintenance_includes_new_keys(self) -> None:
        result = run_notification_maintenance_once(self.conn)
        assert "tasks_auto_created" in result
        assert "tasks_expired" in result
        assert "weather_checks" in result
        assert "weather_alerts_created" in result
        assert "issues_escalated" in result


if __name__ == "__main__":
    unittest.main()
