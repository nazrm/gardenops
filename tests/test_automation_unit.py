"""Unit tests for gardenops.services.automation."""

import json
import unittest
from datetime import date, timedelta
from typing import Any

import gardenops.db as db
from gardenops.router_helpers import generate_public_id
from gardenops.services.automation import (
    _WEATHER_TASK_LOCK_SEED,
    escalate_overdue_follow_ups,
    on_frost_alert,
    on_harvest_logged,
    on_heat_alert,
    on_issue_created,
    on_rain_alert,
)
from tests.base import DbTestBase


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


class TestOnIssueCreated(DbTestBase):
    def _create_issue(
        self,
        title: str = "Aphids on roses",
        severity: str = "normal",
        follow_up_on: str | None = None,
    ) -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """INSERT INTO garden_issues
               (public_id, garden_id, issue_type, title, severity, status,
                follow_up_on, created_by_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, 'pest', %s, %s, 'open', %s, %s, %s, %s)
               RETURNING id""",
            (
                generate_public_id("iss"),
                self.garden_id,
                title,
                severity,
                follow_up_on,
                self._owner_id,
                now_ms,
                now_ms,
            ),
        )
        issue_id = cursor.fetchone()["id"]
        self.conn.commit()
        return issue_id

    def test_creates_followup_task(self) -> None:
        issue_id = self._create_issue(follow_up_on="2026-04-01")
        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        assert task_id > 0
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "inspect_issue"
        assert "Follow up" in task["title"]
        assert task["due_on"] == "2026-04-01"
        assert task["description"], "Follow-up task should have EN description"
        assert "issue iss_" in task["description"]
        meta = json.loads(task["metadata_json"])
        assert "description_no" in meta, "Follow-up task should have NO description"
        assert "sak iss_" in meta["description_no"]

    def test_default_followup_date_when_none(self) -> None:
        issue_id = self._create_issue(follow_up_on=None)
        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        assert task_id > 0
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        expected = (date.today() + timedelta(days=7)).isoformat()
        assert task["due_on"] == expected

    def test_dedup_prevents_duplicate_task(self) -> None:
        issue_id = self._create_issue(follow_up_on="2026-04-01")
        first = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        second = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        assert first > 0
        assert second == 0

    def test_nonexistent_issue_returns_zero(self) -> None:
        result = on_issue_created(self.conn, self.garden_id, 9999, None)
        assert result == 0

    def test_severity_normalization(self) -> None:
        issue_id = self._create_issue(severity="high")
        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        task = self.conn.execute(
            "SELECT severity FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        assert task["severity"] == "high"

    def test_links_plants_and_plots(self) -> None:
        self._insert_plant("IP1", "Rose")
        issue_id = self._create_issue()
        self.conn.execute(
            "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
            (issue_id, "IP1"),
        )
        self.conn.execute(
            "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
            (issue_id, "B1"),
        )
        self.conn.commit()

        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        task_plant = self.conn.execute(
            "SELECT * FROM garden_task_plants WHERE task_id = %s",
            (task_id,),
        ).fetchone()
        assert task_plant is not None
        assert task_plant["plt_id"] == "IP1"

        task_plot = self.conn.execute(
            "SELECT * FROM garden_task_plots WHERE task_id = %s",
            (task_id,),
        ).fetchone()
        assert task_plot is not None
        assert task_plot["plot_id"] == "B1"


class TestOnFrostAlert(DbTestBase):
    def _create_frost_alert(
        self,
        valid_from: str = "2026-01-15",
        coldest: float = -6.0,
    ) -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """INSERT INTO weather_alerts
               (garden_id, alert_type, severity, title, description,
                valid_from, valid_until, metadata_json, created_at_ms)
               VALUES (%s, 'frost_warning', 'high', 'Frost', 'Cold',
                       %s, %s, %s, %s)
               RETURNING id""",
            (self.garden_id, valid_from, valid_from, json.dumps({"coldest": coldest}), now_ms),
        )
        alert_id = cursor.fetchone()["id"]
        self.conn.commit()
        return alert_id

    def _create_owned_plot(self, plot_id: str, garden_id: int) -> None:
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES (%s, %s, 'F', 'Frost bed', 1, 5, 5, '', '')
            """,
            (plot_id, garden_id),
        )
        self.conn.execute(
            """
            INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
            VALUES (%s, %s, %s)
            """,
            (plot_id, self._owner_id, garden_id),
        )

    def test_creates_protection_tasks(self) -> None:
        self._insert_plant("FP1", "Tender rose", hardiness="H3")
        alert_id = self._create_frost_alert()
        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert created == 1
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE %s",
            (f"auto:frost_protect:{alert_id}:FP1",),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "protect"
        assert "Protect from frost" in task["title"]
        assert task["severity"] == "high"
        assert task["description"], "Frost task should have EN description"
        frost_meta = json.loads(task["metadata_json"])
        assert "description_no" in frost_meta, "Frost task should have NO description"

    def test_skips_hardy_plants(self) -> None:
        self._insert_plant("FP2", "Super hardy", hardiness="H7")
        alert_id = self._create_frost_alert()
        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert created == 0

    def test_skips_h6_plants(self) -> None:
        self._insert_plant("FP3", "Very hardy", hardiness="H6")
        alert_id = self._create_frost_alert()
        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert created == 0

    def test_mild_frost_only_targets_temperature_vulnerable_plants(self) -> None:
        self._insert_plant("FP-MILD-TENDER", "Tender", hardiness="H2")
        self._insert_plant("FP-MILD-HARDY", "Hardy enough", hardiness="H3")
        alert_id = self._create_frost_alert(coldest=-1.0)

        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        task_ids = {
            str(row["rule_source"])
            for row in self.conn.execute(
                "SELECT rule_source FROM garden_tasks WHERE garden_id = %s",
                (self.garden_id,),
            ).fetchall()
        }

        assert created == 1
        assert f"auto:frost_protect:{alert_id}:FP-MILD-TENDER" in task_ids
        assert f"auto:frost_protect:{alert_id}:FP-MILD-HARDY" not in task_ids

    def test_frost_without_temperature_does_not_generate_guesswork(self) -> None:
        self._insert_plant("FP-UNKNOWN", "Unknown forecast", hardiness="H2")
        now_ms = db.current_timestamp_ms()
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'frost_warning', 'high', 'Frost', 'Cold',
                    '2026-01-15', '2026-01-15', '{}', %s)
            RETURNING id
            """,
            (self.garden_id, now_ms),
        ).fetchone()
        assert alert is not None

        created = on_frost_alert(self.conn, self.garden_id, int(alert["id"]), self._owner_id)

        assert created == 0

    def test_nonexistent_alert_returns_zero(self) -> None:
        result = on_frost_alert(self.conn, self.garden_id, 9999, None)
        assert result == 0

    def test_dedup_prevents_duplicate_tasks(self) -> None:
        self._insert_plant("FP4", "Tender", hardiness="H2")
        alert_id = self._create_frost_alert()
        first = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        second = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert first == 1
        assert second == 0

    def test_weather_task_generation_locks_alert_deduplication_until_commit(self) -> None:
        self._insert_plant("FP-LOCK", "Locked tender", hardiness="H3")
        alert_id = self._create_frost_alert()

        assert on_frost_alert(self.conn, self.garden_id, alert_id, self._owner_id) == 1
        probe = db.get_db()
        try:
            row = probe.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, %s)) AS acquired",
                (
                    f"gardenops:weather-task:{self.garden_id}:{alert_id}",
                    _WEATHER_TASK_LOCK_SEED,
                ),
            ).fetchone()
            assert row is not None
            assert not bool(row["acquired"])
        finally:
            db.return_db(probe)

        self.conn.commit()
        assert on_frost_alert(self.conn, self.garden_id, alert_id, self._owner_id) == 0

    def test_batched_reads_preserve_dedup_and_garden_scoped_plot_links(self) -> None:
        self._insert_plant("FPB1", "Existing tender", hardiness="H3")
        alert_id = self._create_frost_alert()
        assert on_frost_alert(self.conn, self.garden_id, alert_id, self._owner_id) == 1

        self._insert_plant("FPB2", "New tender", hardiness="H3")
        self._insert_plant("FPB3", "Unrelated hardy", hardiness="H7")
        other_garden = self.conn.execute(
            """
            INSERT INTO gardens (slug, name)
            VALUES ('weather-batch-other', 'Weather batch other')
            RETURNING id
            """,
        ).fetchone()
        assert other_garden is not None
        other_garden_id = int(other_garden["id"])
        self._create_owned_plot("FPB-OWN", self.garden_id)
        self._create_owned_plot("FPB-OTHER", other_garden_id)
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('FPB-OWN', 'FPB2', 1)",
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('FPB-OTHER', 'FPB2', 1)",
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('FPB-OWN', 'FPB3', 1)",
        )
        self.conn.commit()

        recording_connection = _ReadCountingConnection(self.conn)
        created = on_frost_alert(recording_connection, self.garden_id, alert_id, self._owner_id)

        assert created == 1
        task = self.conn.execute(
            """
            SELECT id, title
            FROM garden_tasks
            WHERE garden_id = %s AND rule_source = %s
            """,
            (self.garden_id, f"auto:frost_protect:{alert_id}:FPB2"),
        ).fetchone()
        assert task is not None
        assert task["title"] == "Protect from frost: New tender (FPB-OWN)"
        task_plot_rows = self.conn.execute(
            "SELECT plot_id FROM garden_task_plots WHERE task_id = %s ORDER BY plot_id",
            (int(task["id"]),),
        ).fetchall()
        assert [str(row["plot_id"]) for row in task_plot_rows] == ["FPB-OWN"]
        plot_query, plot_params = next(
            (query, params)
            for query, params in recording_connection.read_queries
            if "FROM plot_plants pp" in query
        )
        assert "pp.plt_id = ANY(%s)" in plot_query
        assert plot_params == (self.garden_id, ["FPB2"])

    def test_weather_task_read_queries_stay_constant_as_matching_plants_grow(self) -> None:
        self._create_owned_plot("FP-SCALE", self.garden_id)
        self._insert_plant("FPS1", "First tender", hardiness="H3")
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('FP-SCALE', 'FPS1', 1)",
        )
        self.conn.commit()
        first_alert_id = self._create_frost_alert()

        first_connection = _ReadCountingConnection(self.conn)
        assert on_frost_alert(first_connection, self.garden_id, first_alert_id, self._owner_id) == 1

        for number in range(2, 10):
            plant_id = f"FPS{number}"
            self._insert_plant(plant_id, f"Tender {number}", hardiness="H3")
            self.conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 1)",
                ("FP-SCALE", plant_id),
            )
        self.conn.commit()
        second_alert_id = self._create_frost_alert("2026-01-16")

        many_connection = _ReadCountingConnection(self.conn)
        assert on_frost_alert(many_connection, self.garden_id, second_alert_id, self._owner_id) == 9

        # The advisory lock adds one fixed query; matching plants never add reads.
        assert first_connection.read_query_count == many_connection.read_query_count == 5


class TestWeatherTaskTyping(DbTestBase):
    def _create_alert(self, alert_type: str, valid_from: str = "2026-07-15") -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """
            INSERT INTO weather_alerts (
                garden_id, alert_type, severity, title, description,
                valid_from, valid_until, metadata_json, created_at_ms
            ) VALUES (%s, %s, 'high', %s, '', %s, %s, '{}', %s)
            RETURNING id
            """,
            (
                self.garden_id,
                alert_type,
                alert_type,
                valid_from,
                valid_from,
                now_ms,
            ),
        )
        alert_id = cursor.fetchone()["id"]
        self.conn.commit()
        return alert_id

    def test_heat_alert_creates_protect_tasks(self) -> None:
        self._insert_plant("HT1", "Heat test", care_watering="Water regularly in summer")
        alert_id = self._create_alert("heatwave")

        created = on_heat_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )

        assert created == 1
        task = self.conn.execute(
            "SELECT task_type FROM garden_tasks WHERE rule_source LIKE %s",
            (f"auto:heat_protect:{alert_id}:HT1",),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "protect"

    def test_rain_alert_creates_protect_tasks(self) -> None:
        self._insert_plant("RN1", "Rain test", care_watering="Water regularly in summer")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('RN1-OUT', %s, 'R', 'Rain bed', 1, 4, 4, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            """
            INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
            VALUES ('RN1-OUT', %s, %s)
            """,
            (self._owner_id, self.garden_id),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('RN1-OUT', 'RN1', 1)",
        )
        self.conn.commit()
        alert_id = self._create_alert("rain_surplus")

        created = on_rain_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )

        assert created == 1
        task = self.conn.execute(
            "SELECT task_type FROM garden_tasks WHERE rule_source LIKE %s",
            (f"auto:rain_drainage:{alert_id}:RN1",),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "protect"

    def test_rain_alert_creates_drainage_only_for_outdoor_placements(self) -> None:
        for plant_id, name in (
            ("RND-OUT", "Outdoor drainage"),
            ("RND-IN", "Indoor drainage"),
            ("RND-UNPLACED", "Unplaced drainage"),
        ):
            self._insert_plant(plant_id, name, care_watering="Water regularly in summer")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES
                ('RND-OUT-PLOT', %s, 'R', 'Outdoor', 1, 5, 5, '', ''),
                ('RND-IN-PLOT', %s, 'I', 'Indoor', 1, NULL, NULL, '', '')
            """,
            (self.garden_id, self.garden_id),
        )
        self.conn.execute(
            """
            INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
            VALUES
                ('RND-OUT-PLOT', %s, %s),
                ('RND-IN-PLOT', %s, %s)
            """,
            (self._owner_id, self.garden_id, self._owner_id, self.garden_id),
        )
        self.conn.execute(
            """
            INSERT INTO plot_plants (plot_id, plt_id, quantity)
            VALUES
                ('RND-OUT-PLOT', 'RND-OUT', 1),
                ('RND-IN-PLOT', 'RND-IN', 1)
            """,
        )
        self.conn.commit()
        alert_id = self._create_alert("rain_surplus")

        created = on_rain_alert(self.conn, self.garden_id, alert_id, self._owner_id)

        rows = self.conn.execute(
            """
            SELECT rule_source
            FROM garden_tasks
            WHERE garden_id = %s
              AND rule_source LIKE %s
            ORDER BY rule_source
            """,
            (self.garden_id, f"auto:rain_drainage:{alert_id}:%"),
        ).fetchall()
        task = self.conn.execute(
            """
            SELECT t.id
            FROM garden_tasks t
            WHERE t.garden_id = %s
              AND t.rule_source = %s
            """,
            (self.garden_id, f"auto:rain_drainage:{alert_id}:RND-OUT"),
        ).fetchone()
        assert task is not None
        plot_rows = self.conn.execute(
            "SELECT plot_id FROM garden_task_plots WHERE task_id = %s ORDER BY plot_id",
            (int(task["id"]),),
        ).fetchall()

        assert created == 1
        assert [str(row["rule_source"]) for row in rows] == [
            f"auto:rain_drainage:{alert_id}:RND-OUT"
        ]
        assert [str(row["plot_id"]) for row in plot_rows] == ["RND-OUT-PLOT"]

    def test_rain_alert_reschedules_watering_and_records_attention_outcome(self) -> None:
        self._insert_plant("RN2", "Rain restore", care_watering="Water regularly in summer")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('RN-OUT', %s, 'R', 'Rain bed', 2, 4, 4, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('RN-OUT', 'RN2', 1)",
        )
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_at_ms, updated_at_ms)
            VALUES ('task_rain_reschedule_existing', %s, 'water',
                    'Water rain restore', '', 'pending', 'normal',
                    '2026-07-15', 'water:RN2:2026-07-15', '{}', 1, 1)
            RETURNING id
            """,
            (self.garden_id,),
        ).fetchone()
        assert task is not None
        self.conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, 'RN2')",
            (int(task["id"]),),
        )
        self.conn.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'RN-OUT')",
            (int(task["id"]),),
        )
        alert_id = self._create_alert("rain_surplus", valid_from="2026-07-15")

        on_rain_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )

        task_after = self.conn.execute(
            """
            SELECT due_on, metadata_json
            FROM garden_tasks
            WHERE garden_id = %s
              AND rule_source = 'water:RN2:2026-07-15'
            """,
            (self.garden_id,),
        ).fetchone()
        outcome = self.conn.execute(
            """
            SELECT outcome_type, source_type, source_public_id, target_type, target_id,
                   plant_ids_json, plot_ids_json, metadata_json, recovery_action_json
            FROM attention_outcomes
            WHERE garden_id = %s
              AND outcome_type = 'watering_rescheduled_by_rain'
              AND source_public_id = 'water:RN2:2026-07-15'
            """,
            (self.garden_id,),
        ).fetchone()

        assert task_after is not None
        task_metadata = json.loads(str(task_after["metadata_json"]))
        assert str(task_after["due_on"]) == "2026-07-16"
        assert task_metadata["rescheduled_from"] == "2026-07-15"
        assert task_metadata["rescheduled_reason"] == "rain_alert"
        assert outcome is not None
        assert str(outcome["source_type"]) == "task_generator"
        assert str(outcome["target_type"]) == "plant"
        assert str(outcome["target_id"]) == "RN2"
        assert json.loads(str(outcome["plant_ids_json"])) == ["RN2"]
        assert json.loads(str(outcome["plot_ids_json"])) == ["RN-OUT"]
        outcome_metadata = json.loads(str(outcome["metadata_json"]))
        recovery_action = json.loads(str(outcome["recovery_action_json"]))
        assert outcome_metadata["due_on"] == "2026-07-15"
        assert outcome_metadata["new_due_on"] == "2026-07-16"
        assert recovery_action["due_on"] == "2026-07-15"
        assert recovery_action["target_id"] == "RN2"

    def test_rain_alert_only_reschedules_watering_for_outdoor_target_plants(self) -> None:
        for plant_id, name in (
            ("RG-OUT", "Outdoor watering"),
            ("RG-INDOOR", "Indoor watering"),
            ("RG-INDOOR-PLOT", "Indoor plot watering"),
            ("RG-UNPLACED", "Unplaced watering"),
        ):
            self._insert_plant(plant_id, name, care_watering="Water regularly in summer")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES
                ('RG-OUT-PLOT', %s, 'R', 'Outdoor', 1, 4, 4, '', ''),
                ('RG-INDOOR-PLOT', %s, 'I', 'Indoor', 1, NULL, NULL, '', '')
            """,
            (self.garden_id, self.garden_id),
        )
        self.conn.execute(
            """
            INSERT INTO plot_plants (plot_id, plt_id, quantity)
            VALUES
                ('RG-OUT-PLOT', 'RG-OUT', 1),
                ('RG-INDOOR-PLOT', 'RG-INDOOR', 1)
            """,
        )
        task_rows = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_at_ms, updated_at_ms)
            VALUES
                ('task_rain_gate_outdoor', %s, 'water', 'Water outdoors', '',
                 'pending', 'normal', '2026-07-15', 'water:RG-OUT:2026-07-15', '{}', 1, 1),
                ('task_rain_gate_indoor', %s, 'water', 'Water indoors', '',
                 'pending', 'normal', '2026-07-15', 'water:RG-INDOOR:2026-07-15', '{}', 1, 1),
                ('task_rain_gate_indoor_plot', %s, 'water', 'Water indoor plot', '',
                 'pending', 'normal', '2026-07-15', 'water:RG-INDOOR-PLOT:2026-07-15', '{}', 1, 1),
                ('task_rain_gate_unplaced', %s, 'water', 'Water unplaced', '',
                 'pending', 'normal', '2026-07-15', 'water:RG-UNPLACED:2026-07-15', '{}', 1, 1)
            RETURNING id, public_id
            """,
            (self.garden_id, self.garden_id, self.garden_id, self.garden_id),
        ).fetchall()
        task_ids = {str(row["public_id"]): int(row["id"]) for row in task_rows}
        for public_id, plant_id in (
            ("task_rain_gate_outdoor", "RG-OUT"),
            ("task_rain_gate_indoor", "RG-INDOOR"),
            ("task_rain_gate_indoor_plot", "RG-INDOOR-PLOT"),
            ("task_rain_gate_unplaced", "RG-UNPLACED"),
        ):
            self.conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (task_ids[public_id], plant_id),
            )
        self.conn.execute(
            """
            INSERT INTO garden_task_plots (task_id, plot_id)
            VALUES
                (%s, 'RG-OUT-PLOT'),
                (%s, 'RG-INDOOR-PLOT')
            """,
            (
                task_ids["task_rain_gate_outdoor"],
                task_ids["task_rain_gate_indoor_plot"],
            ),
        )
        alert_id = self._create_alert("rain_surplus", valid_from="2026-07-15")

        on_rain_alert(self.conn, self.garden_id, alert_id, self._owner_id)

        due_dates = {
            str(row["rule_source"]): str(row["due_on"])
            for row in self.conn.execute(
                """
                SELECT rule_source, due_on
                FROM garden_tasks
                WHERE garden_id = %s
                  AND rule_source LIKE 'water:RG-%%'
                """,
                (self.garden_id,),
            ).fetchall()
        }
        outcome_sources = {
            str(row["source_public_id"])
            for row in self.conn.execute(
                """
                SELECT source_public_id
                FROM attention_outcomes
                WHERE garden_id = %s
                  AND outcome_type = 'watering_rescheduled_by_rain'
                """,
                (self.garden_id,),
            ).fetchall()
        }

        assert due_dates["water:RG-OUT:2026-07-15"] == "2026-07-16"
        assert due_dates["water:RG-INDOOR:2026-07-15"] == "2026-07-15"
        assert due_dates["water:RG-INDOOR-PLOT:2026-07-15"] == "2026-07-15"
        assert due_dates["water:RG-UNPLACED:2026-07-15"] == "2026-07-15"
        assert outcome_sources == {"water:RG-OUT:2026-07-15"}


class TestOnHarvestLogged(DbTestBase):
    def _create_harvest(
        self,
        quantity: float = 2.5,
        unit: str = "kg",
    ) -> int:
        now_ms = db.current_timestamp_ms()
        today = date.today().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO harvest_entries
               (public_id, garden_id, occurred_on, quantity, unit, quality,
                notes, metadata_json, actor_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, %s, %s, %s, 'good', '', '{}', %s, %s, %s)
               RETURNING id""",
            (
                generate_public_id("hrv"),
                self.garden_id,
                today,
                quantity,
                unit,
                self._owner_id,
                now_ms,
                now_ms,
            ),
        )
        harvest_id = cursor.fetchone()["id"]
        self.conn.commit()
        return harvest_id

    def test_creates_rollup(self) -> None:
        h_id = self._create_harvest(2.5, "kg")
        on_harvest_logged(self.conn, self.garden_id, h_id)
        year = date.today().year
        key = f"harvest_rollup:{self.garden_id}:{year}"
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (key,),
        ).fetchone()
        assert row is not None
        import json

        rollup = json.loads(row["value"])
        assert rollup["year"] == year
        assert len(rollup["by_unit"]) == 1
        assert rollup["by_unit"][0]["unit"] == "kg"
        assert rollup["by_unit"][0]["total_qty"] == 2.5

    def test_rollup_aggregates_multiple_entries(self) -> None:
        self._create_harvest(2.0, "kg")
        h2 = self._create_harvest(3.0, "kg")
        self._create_harvest(5.0, "pieces")
        on_harvest_logged(self.conn, self.garden_id, h2)
        year = date.today().year
        key = f"harvest_rollup:{self.garden_id}:{year}"
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (key,),
        ).fetchone()
        import json

        rollup = json.loads(row["value"])
        units = {u["unit"]: u["total_qty"] for u in rollup["by_unit"]}
        assert units["kg"] == 5.0
        assert units["pieces"] == 5.0

    def test_rollup_replaces_on_update(self) -> None:
        h1 = self._create_harvest(1.0, "kg")
        on_harvest_logged(self.conn, self.garden_id, h1)

        h2 = self._create_harvest(2.0, "kg")
        on_harvest_logged(self.conn, self.garden_id, h2)

        year = date.today().year
        key = f"harvest_rollup:{self.garden_id}:{year}"
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM app_settings WHERE key = %s",
            (key,),
        ).fetchone()
        assert count["c"] == 1


class TestEscalateOverdueFollowUps(DbTestBase):
    def _create_issue(
        self,
        title: str = "Aphids on roses",
        severity: str = "normal",
        status: str = "open",
        follow_up_on: str | None = None,
    ) -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """INSERT INTO garden_issues
               (public_id, garden_id, issue_type, title, severity, status,
                follow_up_on, created_by_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, 'pest', %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                generate_public_id("iss"),
                self.garden_id,
                title,
                severity,
                status,
                follow_up_on,
                self._owner_id,
                now_ms,
                now_ms,
            ),
        )
        issue_id = cursor.fetchone()["id"]
        self.conn.commit()
        return issue_id

    def test_escalates_overdue_issue(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        issue_id = self._create_issue(
            severity="normal",
            follow_up_on=yesterday,
        )
        result = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert result == {"escalated": 1}

        issue = self.conn.execute(
            "SELECT severity, public_id FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()
        assert issue["severity"] == "high"

        rule_source = f"auto:escalation:{issue['public_id']}:{yesterday}"
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source = %s",
            (rule_source,),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "inspect_issue"
        assert "Overdue follow-up" in task["title"]
        expected_due = (date.today() + timedelta(days=3)).isoformat()
        assert task["due_on"] == expected_due
        assert task["description"], "Escalation task should have EN description"
        assert issue["public_id"] in task["description"]
        esc_meta = json.loads(task["metadata_json"])
        assert "description_no" in esc_meta, "Escalation task should have NO description"
        assert issue["public_id"] in esc_meta["description_no"]

    def test_skips_resolved_issues(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._create_issue(
            status="resolved",
            follow_up_on=yesterday,
        )
        result = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert result == {"escalated": 0}

    def test_dedup_prevents_double_escalation(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._create_issue(
            severity="low",
            follow_up_on=yesterday,
        )
        first = escalate_overdue_follow_ups(self.conn, self.garden_id)
        second = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert first == {"escalated": 1}
        assert second == {"escalated": 0}

        task_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM garden_tasks WHERE rule_source LIKE 'auto:escalation:%'",
        ).fetchone()
        assert task_count["c"] == 1

    def test_skips_future_followups(self) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self._create_issue(follow_up_on=tomorrow)
        result = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert result == {"escalated": 0}

    def test_links_plants_and_plots(self) -> None:
        self._insert_plant("EP1", "Rose")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        issue_id = self._create_issue(follow_up_on=yesterday)
        self.conn.execute(
            "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
            (issue_id, "EP1"),
        )
        self.conn.execute(
            "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
            (issue_id, "B1"),
        )
        self.conn.commit()

        escalate_overdue_follow_ups(self.conn, self.garden_id)

        issue_public_id = self.conn.execute(
            "SELECT public_id FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()["public_id"]
        rule_source = f"auto:escalation:{issue_public_id}:{yesterday}"
        task = self.conn.execute(
            "SELECT id FROM garden_tasks WHERE rule_source = %s",
            (rule_source,),
        ).fetchone()
        assert task is not None

        task_plant = self.conn.execute(
            "SELECT * FROM garden_task_plants WHERE task_id = %s",
            (task["id"],),
        ).fetchone()
        assert task_plant is not None
        assert task_plant["plt_id"] == "EP1"

        task_plot = self.conn.execute(
            "SELECT * FROM garden_task_plots WHERE task_id = %s",
            (task["id"],),
        ).fetchone()
        assert task_plot is not None
        assert task_plot["plot_id"] == "B1"

    def test_severity_cap_at_critical(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        issue_id = self._create_issue(
            severity="critical",
            follow_up_on=yesterday,
        )
        escalate_overdue_follow_ups(self.conn, self.garden_id)

        issue = self.conn.execute(
            "SELECT severity FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()
        assert issue["severity"] == "critical"

        issue_public_id = self.conn.execute(
            "SELECT public_id FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()["public_id"]
        rule_source = f"auto:escalation:{issue_public_id}:{yesterday}"
        task = self.conn.execute(
            "SELECT severity FROM garden_tasks WHERE rule_source = %s",
            (rule_source,),
        ).fetchone()
        assert task is not None
        assert task["severity"] == "high"


if __name__ == "__main__":
    unittest.main()
