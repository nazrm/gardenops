"""Tests for C7 recurring care schedules and C8 harvest window alerts."""

import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import gardenops.db as db
from gardenops.services.attention.outcomes import upsert_attention_outcome
from gardenops.services.attention.service import restore_attention_outcome
from gardenops.services.task_generator import (
    generate_task_description_overrides,
    generate_tasks,
    infer_task_description,
    reconcile_rain_watering_outcomes,
    restore_generated_watering_task_from_attention_outcome,
)
from tests.base import DbTestBase


class _RestoreRaceConnection:
    """Synchronize concurrent restore reads without changing production behavior."""

    def __init__(
        self,
        connection: Any,
        outcome_barrier: threading.Barrier,
        task_barrier: threading.Barrier,
    ) -> None:
        self._connection = connection
        self._outcome_barrier = outcome_barrier
        self._task_barrier = task_barrier

    def execute(self, query: Any, params: Any = None) -> Any:
        sql = str(query)
        if "FROM attention_outcomes" in sql and "FOR UPDATE" not in sql:
            self._outcome_barrier.wait(timeout=10)
        elif "FROM garden_tasks" in sql and "rule_source = %s" in sql:
            try:
                # With the advisory lock, the second request cannot reach this
                # read. Time out so the lock holder can finish its transaction.
                self._task_barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
        return self._connection.execute(query, params)


class _PauseAfterRestoreTaskLockConnection:
    """Hold a manual restore after it locks its task row."""

    def __init__(
        self,
        connection: Any,
        task_locked: threading.Event,
        allow_restore: threading.Event,
    ) -> None:
        self._connection = connection
        self._task_locked = task_locked
        self._allow_restore = allow_restore
        self._outcome_locked = False

    def execute(self, query: Any, params: Any = None) -> Any:
        sql = str(query)
        if "FROM attention_outcomes" in sql and "FOR UPDATE" in sql:
            result = self._connection.execute(query, params)
            self._outcome_locked = True
            return result
        if "FROM garden_tasks" in sql and "rule_source = %s" in sql and "FOR UPDATE" in sql:
            if not self._outcome_locked:
                raise AssertionError("manual restore locked its task before its outcome")
            result = self._connection.execute(query, params)
            self._task_locked.set()
            if not self._allow_restore.wait(timeout=5):
                raise TimeoutError("manual restore was not released")
            return result
        return self._connection.execute(query, params)


class _ObserveAutomaticOutcomeLockConnection:
    """Expose when automatic recovery has acquired its outcome row locks."""

    def __init__(self, connection: Any, outcome_locked: threading.Event) -> None:
        self._connection = connection
        self._outcome_locked = outcome_locked

    def execute(self, query: Any, params: Any = None) -> Any:
        result = self._connection.execute(query, params)
        sql = str(query)
        if "FROM attention_outcomes" in sql and "ORDER BY id" in sql and "FOR UPDATE" in sql:
            self._outcome_locked.set()
        return result


class TestHarvestNoTaskForNonHarvestCategory(DbTestBase):
    def test_harvest_no_task_for_non_harvest_category(self) -> None:
        """Plant with category='busker' should NOT get harvest task."""
        self._insert_plant(
            "NH1",
            "Regular Bush",
            category="busker",
            bloom_month="mai",
        )
        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
        )
        tasks = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE garden_id = %s AND task_type = 'harvest'",
            (self.garden_id,),
        ).fetchall()
        assert len(tasks) == 0


class TestBloomObservationSeasonClosure(DbTestBase):
    def test_not_seen_this_season_suppresses_remaining_bloom_window(self) -> None:
        self._insert_plant("BLOOM-CLOSED", "Late rose", bloom_month="mai-juni")
        entry = self.conn.execute(
            """
            INSERT INTO garden_journal_entries
                (public_id, garden_id, event_type, occurred_on, title, notes,
                 metadata_json, actor_user_id, created_at_ms, updated_at_ms)
            VALUES ('jrn_bloom_closed', %s, 'observed', '2026-05-20',
                    'Not seen blooming this season', '', %s, %s, 1, 1)
            RETURNING id
            """,
            (
                self.garden_id,
                json.dumps({"outcome": "not_seen_blooming_this_season"}),
                self._owner_id,
            ),
        ).fetchone()
        assert entry is not None
        self.conn.execute(
            "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (int(entry["id"]), "BLOOM-CLOSED"),
        )

        generate_tasks(self.conn, self.garden_id, 6, 2026, self._owner_id)

        task = self.conn.execute(
            """
            SELECT id FROM garden_tasks
            WHERE garden_id = %s
              AND task_type = 'observe_bloom'
              AND rule_source = 'bloom_observe:BLOOM-CLOSED:2026-06'
            """,
            (self.garden_id,),
        ).fetchone()
        assert task is None


class TestInferTaskDescription(DbTestBase):
    """Verify infer_task_description recovers bilingual text from rule_source."""

    def test_seasonal_prune(self) -> None:
        self._insert_plant(
            "PR1",
            "Glen Ample",
            category="baerbusker",
            care_maintenance="Prune old canes after harvest to keep fruiting wood fresh.",
        )
        generate_tasks(self.conn, self.garden_id, 3, 2026, self._owner_id)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE garden_id = %s AND task_type = 'prune'",
            (self.garden_id,),
        ).fetchone()
        assert task is not None
        cleared = dict(task)
        cleared["description"] = ""
        en, no = infer_task_description(self.conn, cleared)
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Glen Ample" in en
        assert "Glen Ample" in no
        assert "Why:" in en
        assert "Hvorfor:" in no
        assert "fruiting" in en
        assert str(task["window_kind"]) == "recommended"
        assert str(task["window_start_on"]) == "2026-02-08"
        assert str(task["window_end_on"]) == "2026-03-15"

    def test_fertilize(self) -> None:
        self._insert_plant(
            "FE1",
            "Dahlia",
            care_maintenance="fertilize monthly",
        )
        generate_tasks(self.conn, self.garden_id, 4, 2026, self._owner_id)
        tasks = self.conn.execute(
            """
            SELECT * FROM garden_tasks
            WHERE garden_id = %s AND task_type = 'fertilize'
            ORDER BY due_on
            """,
            (self.garden_id,),
        ).fetchall()
        assert len(tasks) == 2
        task = tasks[0]
        assert task is not None
        cleared = dict(task)
        cleared["description"] = ""
        en, no = infer_task_description(self.conn, cleared)
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Dahlia" in en
        assert "Dahlia" in no
        assert [
            (
                str(task["due_on"]),
                str(task["window_start_on"]),
                str(task["window_end_on"]),
                str(task["window_kind"]),
            )
            for task in tasks
        ] == [
            ("2026-04-01", "2026-03-25", "2026-04-08", "recommended"),
            ("2026-04-15", "2026-04-08", "2026-04-22", "recommended"),
        ]

    def test_water(self) -> None:
        self._insert_plant("WA1", "Hydrangea", care_watering="regular")
        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=1_782_864_000_000,
        )
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE garden_id = %s AND task_type = 'water'",
            (self.garden_id,),
        ).fetchone()
        assert task is not None
        cleared = dict(task)
        cleared["description"] = ""
        en, no = infer_task_description(self.conn, cleared)
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Hydrangea" in en
        assert "Hydrangea" in no
        assert "Why:" in en
        assert "Hvorfor:" in no

    def test_manual_and_automatic_rain_recovery_use_frozen_garden_date(self) -> None:
        now_ms = 1_783_180_800_000
        frozen_clock = {
            "APP_ENV": "test",
            "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(now_ms),
            "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
        }
        recovery_action_by_plant: dict[str, dict[str, Any]] = {}
        outcome_by_plant: dict[str, str] = {}
        for plant_id, plant_name in (
            ("RAIN-DATE-MANUAL", "Manual rain date"),
            ("RAIN-DATE-AUTO", "Automatic rain date"),
        ):
            self._insert_plant(plant_id, plant_name, care_watering="regular moisture")
            source_public_id = f"water:{plant_id}:2026-07-04"
            recovery_action = {
                "kind": "restore_generated_watering_task",
                "label": "Restore watering",
                "source_public_id": source_public_id,
                "target_type": "plant",
                "target_id": plant_id,
                "due_on": "2026-07-04",
                "plant_ids": [plant_id],
                "plot_ids": [],
            }
            outcome_by_plant[plant_id] = upsert_attention_outcome(
                self.conn,
                garden_id=self.garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id=f"rain-date-{plant_id}",
                source_public_id=source_public_id,
                target_type="plant",
                target_id=plant_id,
                title="Watering covered by rain",
                explanation="Rain covers this watering.",
                reason="Rain covers watering",
                plant_ids=(plant_id,),
                metadata={"due_on": "2026-07-04", "plant_name": plant_name},
                recovery_action=recovery_action,
                occurred_at_ms=now_ms,
                expires_at_ms=now_ms + 86_400_000,
            )
            recovery_action_by_plant[plant_id] = recovery_action

        with patch.dict(os.environ, frozen_clock, clear=False):
            restored = restore_attention_outcome(
                self.conn,
                garden_id=self.garden_id,
                outcome_id=outcome_by_plant["RAIN-DATE-MANUAL"],
                user_id=self._owner_id,
                now_ms=now_ms,
            )
            automatic = reconcile_rain_watering_outcomes(
                self.conn,
                garden_id=self.garden_id,
                now_ms=now_ms,
            )

        self.assertEqual(restored, "restored")
        self.assertEqual(automatic["recovered"], 1)
        rows = self.conn.execute(
            """
            SELECT rule_source, due_on
            FROM garden_tasks
            WHERE garden_id = %s
              AND rule_source = ANY(%s)
            ORDER BY rule_source
            """,
            (
                self.garden_id,
                [
                    recovery_action_by_plant[plant_id]["source_public_id"]
                    for plant_id in sorted(recovery_action_by_plant)
                ],
            ),
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({str(row["due_on"]) for row in rows}, {"2026-07-05"})

    def test_rain_suppressed_watering_records_attention_outcome(self) -> None:
        self._insert_plant("RW1", "Hydrangea", care_watering="regular moisture")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('RW-OUT', %s, 'R', 'Rain bed', 1, 5, 5, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('RW-OUT', 'RW1', 1)",
        )
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'normal', 'Rain covers watering',
                    'Enough rain is expected', '2026-07-01', '2026-07-01',
                    '{"rain_mm":18}', 1)
            RETURNING id
            """,
            (self.garden_id,),
        ).fetchone()
        assert alert is not None

        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=1_782_864_000_000,
        )

        skipped_task = self.conn.execute(
            """
            SELECT 1 FROM garden_tasks
            WHERE garden_id = %s
              AND task_type = 'water'
              AND rule_source = 'water:RW1:2026-07-01'
            """,
            (self.garden_id,),
        ).fetchone()
        outcome = self.conn.execute(
            """
            SELECT outcome_type, source_type, source_id, source_public_id, target_type, target_id,
                   explanation, metadata_json
            FROM attention_outcomes
            WHERE garden_id = %s
              AND outcome_type = 'watering_covered_by_rain'
              AND source_public_id = 'water:RW1:2026-07-01'
              AND target_type = 'plant'
              AND target_id = 'RW1'
            """,
            (self.garden_id,),
        ).fetchone()

        assert skipped_task is None
        assert outcome is not None
        assert str(outcome["source_type"]) == "task_generator"
        assert str(outcome["source_id"]) == str(alert["id"])
        assert str(outcome["source_public_id"]) == "water:RW1:2026-07-01"
        assert "18 mm rain" in str(outcome["explanation"])
        assert '"due_on":"2026-07-01"' in str(outcome["metadata_json"])

    def test_withdrawn_rain_recovers_suppressed_watering_as_reassessment(self) -> None:
        self._insert_plant("RW-WITHDRAW", "Withdrawn rain", care_watering="regular moisture")
        self.conn.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT DO NOTHING
            """,
            (self.garden_id, self._owner_id),
        )
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('RW-WITHDRAW-OUT', %s, 'R', 'Rain withdrawal', 1, 5, 5, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) "
            "VALUES ('RW-WITHDRAW-OUT', 'RW-WITHDRAW', 1)",
        )
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'high', 'Heavy rain', 'Saturated soil expected',
                    '2026-07-15', '2026-07-17', '{"total_mm":35}', 1)
            RETURNING id
            """,
            (self.garden_id,),
        ).fetchone()
        assert alert is not None
        now_ms = 1_784_044_800_000

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(now_ms),
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-15",
            },
            clear=False,
        ):
            generation = generate_tasks(
                self.conn,
                self.garden_id,
                7,
                2026,
                self._owner_id,
                now_ms=now_ms,
            )
            self.conn.execute(
                "UPDATE weather_alerts SET dismissed = 1 WHERE id = %s",
                (int(alert["id"]),),
            )
            recovery = reconcile_rain_watering_outcomes(
                self.conn,
                garden_id=self.garden_id,
                now_ms=now_ms + 1,
            )

        task = self.conn.execute(
            """
            SELECT public_id, due_on, title, description, metadata_json
            FROM garden_tasks
            WHERE garden_id = %s
              AND rule_source = 'water:RW-WITHDRAW:2026-07-15'
            """,
            (self.garden_id,),
        ).fetchone()
        outcome = self.conn.execute(
            """
            SELECT metadata_json, recovery_action_json
            FROM attention_outcomes
            WHERE garden_id = %s
              AND source_public_id = 'water:RW-WITHDRAW:2026-07-15'
              AND outcome_type = 'watering_covered_by_rain'
            """,
            (self.garden_id,),
        ).fetchone()
        notification = self.conn.execute(
            """
            SELECT notification_type, metadata_json
            FROM notification_events
            WHERE garden_id = %s
              AND target_type = 'task'
              AND target_id = %s
              AND dismissed = 0
              AND cleared_at_ms IS NULL
            """,
            (self.garden_id, str(task["public_id"]) if task is not None else ""),
        ).fetchone()
        assert generation["rain_suppressed"] == 1
        assert recovery["recovered"] == 1
        assert task is not None
        assert str(task["due_on"]) == "2026-07-15"
        assert str(task["title"]) == "Reassess watering: Withdrawn rain"
        assert "water only if" in str(task["description"])
        task_metadata = json.loads(str(task["metadata_json"]))
        assert task_metadata["rain_reassessment_delay_days"] == 2
        assert outcome is not None
        outcome_metadata = json.loads(str(outcome["metadata_json"]))
        assert outcome_metadata["lifecycle"]["status"] == "automatically_recovered"
        assert json.loads(str(outcome["recovery_action_json"])) == {}
        assert notification is not None
        assert str(notification["notification_type"]) == "task_due"
        assert json.loads(str(notification["metadata_json"]))["due_on"] == "2026-07-15"

    def test_rain_suppression_keeps_unplaced_and_indoor_watering_tasks(self) -> None:
        self._insert_plant("RNOPLOT", "Unplaced Hydrangea", care_watering="regular moisture")
        self._insert_plant("RINDOOR", "Indoor Hydrangea", care_watering="regular moisture")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('RW-IN', %s, 'I', 'Indoor', 1, NULL, NULL, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('RW-IN', 'RINDOOR', 1)",
        )
        self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'normal', 'Rain covers outdoor watering',
                    'Enough rain is expected outside', '2026-07-01', '2026-07-01',
                    '{"rain_mm":18}', 1)
            """,
            (self.garden_id,),
        )

        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=1_782_864_000_000,
        )

        task_sources = {
            str(row["rule_source"])
            for row in self.conn.execute(
                """
                SELECT rule_source
                FROM garden_tasks
                WHERE garden_id = %s
                  AND task_type = 'water'
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
                  AND outcome_type = 'watering_covered_by_rain'
                """,
                (self.garden_id,),
            ).fetchall()
        }

        assert "water:RNOPLOT:2026-07-01" in task_sources
        assert "water:RINDOOR:2026-07-01" in task_sources
        assert "water:RNOPLOT:2026-07-01" not in outcome_sources
        assert "water:RINDOOR:2026-07-01" not in outcome_sources

    def test_rain_suppression_keeps_mixed_indoor_outdoor_watering_task(self) -> None:
        self._insert_plant("RMIX", "Mixed placement hydrangea", care_watering="regular moisture")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES
                ('RMIX-OUT', %s, 'R', 'Outdoor', 1, 1, 1, '', ''),
                ('RMIX-IN', %s, 'I', 'Indoor', 1, NULL, NULL, '', '')
            """,
            (self.garden_id, self.garden_id),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) "
            "VALUES ('RMIX-OUT', 'RMIX', 1), ('RMIX-IN', 'RMIX', 1)",
        )
        self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'normal', 'Rain outside', '',
                    '2026-07-01', '2026-07-01', '{}', 1)
            """,
            (self.garden_id,),
        )

        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=1_782_864_000_000,
        )

        task = self.conn.execute(
            """
            SELECT 1
            FROM garden_tasks
            WHERE garden_id = %s AND rule_source = 'water:RMIX:2026-07-01'
            """,
            (self.garden_id,),
        ).fetchone()
        assert task is not None

    def test_monthly_generation_does_not_create_stale_pending_watering(self) -> None:
        self._insert_plant("RSTALE", "Stale watering", care_watering="regular moisture")

        result = generate_tasks(
            self.conn,
            self.garden_id,
            6,
            2026,
            self._owner_id,
            now_ms=1_783_872_000_000,
        )

        row = self.conn.execute(
            """
            SELECT 1
            FROM garden_tasks
            WHERE garden_id = %s
              AND task_type = 'water'
              AND rule_source LIKE 'water:RSTALE:%%'
              AND status IN ('pending', 'snoozed')
            """,
            (self.garden_id,),
        ).fetchone()
        assert result["skipped"] >= 4
        assert row is None

    def test_rain_suppression_preserves_alert_winner_order(self) -> None:
        self._insert_plant("RORDER", "Ordered Hydrangea", care_watering="regular moisture")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('RW-ORDER', %s, 'R', 'Rain order', 1, 5, 5, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('RW-ORDER', 'RORDER', 1)",
        )

        def create_alert(
            title: str,
            valid_from: str,
            valid_until: str,
            created_at_ms: int,
        ) -> int:
            row = self.conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'rain_surplus', 'normal', %s, '', %s, %s, '{}', %s)
                RETURNING id
                """,
                (self.garden_id, title, valid_from, valid_until, created_at_ms),
            ).fetchone()
            assert row is not None
            return int(row["id"])

        earliest = create_alert("Earliest start", "2026-07-01", "2026-07-08", 1)
        create_alert("Later start", "2026-07-07", "2026-07-08", 20)

        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=1_782_864_000_000,
        )

        outcomes = {
            str(row["source_public_id"]): str(row["source_id"])
            for row in self.conn.execute(
                """
                SELECT source_public_id, source_id
                FROM attention_outcomes
                WHERE garden_id = %s
                  AND outcome_type = 'watering_covered_by_rain'
                """,
                (self.garden_id,),
            ).fetchall()
        }
        assert outcomes["water:RORDER:2026-07-01"] == str(earliest)
        assert outcomes["water:RORDER:2026-07-08"] == str(earliest)

    def test_harvest_check(self) -> None:
        self._insert_plant(
            "HA1",
            "Blueberry",
            category="baerbusker",
            bloom_month="mai",
        )
        generate_tasks(self.conn, self.garden_id, 7, 2026, self._owner_id)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE garden_id = %s AND task_type = 'harvest'",
            (self.garden_id,),
        ).fetchone()
        assert task is not None
        cleared = dict(task)
        cleared["description"] = ""
        en, no = infer_task_description(self.conn, cleared)
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Blueberry" in en
        assert "Blueberry" in no
        assert str(task["window_kind"]) == "recommended"
        assert str(task["window_start_on"]) == "2026-07-11"
        assert str(task["window_end_on"]) == "2026-07-22"

    def test_generic_tree_does_not_create_harvest_task(self) -> None:
        self._insert_plant(
            "HA-TREE",
            "Japanese maple",
            category="trær",
            bloom_month="mai",
            care_maintenance="Prune damaged branches in winter.",
            care_notes="Provide nutrients while establishing.",
        )

        generate_tasks(self.conn, self.garden_id, 8, 2026, self._owner_id)

        task = self.conn.execute(
            "SELECT id FROM garden_tasks WHERE garden_id = %s AND task_type = 'harvest'",
            (self.garden_id,),
        ).fetchone()
        assert task is None

    def test_fruit_tree_with_harvest_evidence_creates_harvest_task(self) -> None:
        self._insert_plant(
            "HA-APPLE",
            "Apple tree",
            category="trær",
            bloom_month="mai",
            care_notes="Harvest fruit when fully coloured.",
        )

        generate_tasks(self.conn, self.garden_id, 8, 2026, self._owner_id)

        task = self.conn.execute(
            "SELECT id FROM garden_tasks WHERE garden_id = %s AND task_type = 'harvest'",
            (self.garden_id,),
        ).fetchone()
        assert task is not None

    def test_auto_dry_water(self) -> None:
        self._insert_plant(
            "DW1",
            "Hydrangea",
            care_watering="regular moisture",
        )
        en, no = infer_task_description(
            self.conn,
            {"rule_source": "auto:dry_water:12:DW1"},
        )
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Hydrangea" in en
        assert "Hydrangea" in no

    def test_auto_rain_drainage(self) -> None:
        self._insert_plant(
            "RD1",
            "Astilbe",
            care_watering="keep evenly moist",
        )
        en, no = infer_task_description(
            self.conn,
            {"rule_source": "auto:rain_drainage:99:RD1"},
        )
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Astilbe" in en
        assert "Astilbe" in no

    def test_unknown_rule_source(self) -> None:
        en, no = infer_task_description(self.conn, {"rule_source": "xyzzy:foo:bar"})
        assert en == ""
        assert no == ""

    def test_empty_rule_source(self) -> None:
        en, no = infer_task_description(self.conn, {"rule_source": ""})
        assert en == ""
        assert no == ""


class TestRainAttentionOutcomeRestoreConcurrency(DbTestBase):
    def test_late_restore_moves_pending_watering_to_today(self) -> None:
        self._insert_plant("RESTORE-LATE", "Late restore", care_watering="regular moisture")
        now_ms = 1_783_958_400_000
        source_public_id = "water:RESTORE-LATE:2026-07-05"
        outcome_id = upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_covered_by_rain",
            source_type="task_generator",
            source_id="rain-late",
            source_public_id=source_public_id,
            target_type="plant",
            target_id="RESTORE-LATE",
            title="Watering covered by rain",
            explanation="Rain covers this watering.",
            reason="Rain covers watering",
            plant_ids=("RESTORE-LATE",),
            metadata={"due_on": "2026-07-05", "plant_name": "Late restore"},
            recovery_action={
                "kind": "restore_generated_watering_task",
                "source_public_id": source_public_id,
                "target_type": "plant",
                "target_id": "RESTORE-LATE",
                "due_on": "2026-07-05",
                "plant_ids": ["RESTORE-LATE"],
            },
            occurred_at_ms=now_ms,
            expires_at_ms=now_ms + 86_400_000,
        )
        self.conn.commit()

        restored = restore_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            outcome_id=outcome_id,
            user_id=self._owner_id,
            now_ms=now_ms,
        )

        task = self.conn.execute(
            """
            SELECT due_on, status, metadata_json
            FROM garden_tasks
            WHERE garden_id = %s AND rule_source = %s
            """,
            (self.garden_id, source_public_id),
        ).fetchone()
        assert restored == "restored"
        assert task is not None
        assert str(task["due_on"]) == "2026-07-13"
        assert str(task["status"]) == "pending"
        assert json.loads(str(task["metadata_json"]))["restored_original_due_on"] == "2026-07-05"

    def test_expired_task_does_not_consume_rain_recovery_outcome(self) -> None:
        from gardenops.services.attention.providers.weather import WeatherAttentionProvider

        now_ms = 1783180800000
        expires_at_ms = now_ms + 86_400_000
        source_public_id = "water:EXPIRED-RESTORE:2026-07-05"
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES (%s, %s, 'water', 'Expired watering', '', 'expired', 'normal',
                    '2026-07-05', %s, '{}', %s, %s)
            RETURNING id
            """,
            ("task_expired_restore", self.garden_id, source_public_id, now_ms, now_ms),
        ).fetchone()
        assert task is not None
        outcome_id = upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_rescheduled_by_rain",
            source_type="task_generator",
            source_id="rain-expired-restore",
            source_public_id=source_public_id,
            target_type="plant",
            target_id="EXPIRED-RESTORE",
            title="Watering rescheduled by rain",
            explanation="Rain moved this watering.",
            reason="Rain rescheduled watering",
            plant_ids=("EXPIRED-RESTORE",),
            metadata={"due_on": "2026-07-05", "new_due_on": "2026-07-08"},
            recovery_action={
                "kind": "restore_generated_watering_task",
                "label": "Restore watering",
                "source_public_id": source_public_id,
                "target_type": "plant",
                "target_id": "EXPIRED-RESTORE",
                "due_on": "2026-07-05",
                "plant_ids": ["EXPIRED-RESTORE"],
                "plot_ids": [],
            },
            occurred_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )
        self.conn.commit()

        with self.assertRaises(HTTPException) as raised:
            restore_attention_outcome(
                self.conn,
                garden_id=self.garden_id,
                outcome_id=outcome_id,
                user_id=self._owner_id,
                now_ms=now_ms,
            )

        self.assertEqual(raised.exception.status_code, 409)
        outcome = self.conn.execute(
            """
            SELECT expires_at_ms
            FROM attention_outcomes
            WHERE garden_id = %s AND public_id = %s
            """,
            (self.garden_id, outcome_id),
        ).fetchone()
        assert outcome is not None
        self.assertEqual(int(outcome["expires_at_ms"]), expires_at_ms)
        attention_items = WeatherAttentionProvider(frozen_date="2026-07-05").collect(
            self.conn,
            garden_id=self.garden_id,
            user_id=self._owner_id,
            now_ms=now_ms,
        )
        outcome_item = next(
            item for item in attention_items if item.id == f"attn:outcome:{outcome_id}"
        )
        self.assertEqual(outcome_item.secondary_actions, ())

    def test_completed_task_does_not_expose_weather_recovery_action(self) -> None:
        from gardenops.services.attention.providers.weather import WeatherAttentionProvider

        now_ms = 1783180800000
        source_public_id = "water:COMPLETED-RESTORE:2026-07-05"
        self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json, completed_at_ms,
                 created_at_ms, updated_at_ms)
            VALUES (%s, %s, 'water', 'Completed watering', '', 'completed', 'normal',
                    '2026-07-05', %s, '{}', %s, %s, %s)
            """,
            (
                "task_completed_restore",
                self.garden_id,
                source_public_id,
                now_ms,
                now_ms,
                now_ms,
            ),
        )
        outcome_id = upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_covered_by_rain",
            source_type="task_generator",
            source_id="rain-completed-restore",
            source_public_id=source_public_id,
            target_type="plant",
            target_id="COMPLETED-RESTORE",
            title="Watering covered by rain",
            explanation="Rain covers this watering.",
            reason="Rain covers watering",
            plant_ids=("COMPLETED-RESTORE",),
            metadata={"due_on": "2026-07-05"},
            recovery_action={
                "kind": "restore_generated_watering_task",
                "label": "Restore watering",
                "source_public_id": source_public_id,
                "target_type": "plant",
                "target_id": "COMPLETED-RESTORE",
                "due_on": "2026-07-05",
                "plant_ids": ["COMPLETED-RESTORE"],
                "plot_ids": [],
            },
            occurred_at_ms=now_ms,
            expires_at_ms=now_ms + 86_400_000,
        )
        self.conn.commit()

        attention_items = WeatherAttentionProvider(frozen_date="2026-07-05").collect(
            self.conn,
            garden_id=self.garden_id,
            user_id=self._owner_id,
            now_ms=now_ms,
        )
        outcome_item = next(
            item for item in attention_items if item.id == f"attn:outcome:{outcome_id}"
        )

        self.assertEqual(outcome_item.secondary_actions, ())

    def test_completed_task_restore_keeps_outcome_and_terminal_history_intact(self) -> None:
        self._insert_plant(
            "COMPLETED-RESTORE-REQUEST",
            "Completed restore request",
            care_watering="regular moisture",
        )
        now_ms = 1783180800000
        expires_at_ms = now_ms + 86_400_000
        source_public_id = "water:COMPLETED-RESTORE-REQUEST:2026-07-05"
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json, completed_at_ms,
                 created_at_ms, updated_at_ms)
            VALUES (%s, %s, 'water', 'Completed watering', '', 'completed', 'normal',
                    '2026-07-05', %s, '{}', %s, %s, %s)
            RETURNING id
            """,
            (
                "task_completed_restore_request",
                self.garden_id,
                source_public_id,
                now_ms,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        assert task is not None
        outcome_id = upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_covered_by_rain",
            source_type="task_generator",
            source_id="rain-completed-restore-request",
            source_public_id=source_public_id,
            target_type="plant",
            target_id="COMPLETED-RESTORE-REQUEST",
            title="Watering covered by rain",
            explanation="Rain covers this watering.",
            reason="Rain covers watering",
            plant_ids=("COMPLETED-RESTORE-REQUEST",),
            metadata={"due_on": "2026-07-05"},
            recovery_action={
                "kind": "restore_generated_watering_task",
                "label": "Restore watering",
                "source_public_id": source_public_id,
                "target_type": "plant",
                "target_id": "COMPLETED-RESTORE-REQUEST",
                "due_on": "2026-07-05",
                "plant_ids": ["COMPLETED-RESTORE-REQUEST"],
                "plot_ids": [],
            },
            occurred_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )
        self.conn.commit()

        with self.assertRaises(HTTPException) as raised:
            restore_attention_outcome(
                self.conn,
                garden_id=self.garden_id,
                outcome_id=outcome_id,
                user_id=self._owner_id,
                now_ms=now_ms,
            )

        task_after = self.conn.execute(
            "SELECT status, completed_at_ms FROM garden_tasks WHERE id = %s",
            (int(task["id"]),),
        ).fetchone()
        outcome_after = self.conn.execute(
            """
            SELECT expires_at_ms, recovery_action_json
            FROM attention_outcomes
            WHERE garden_id = %s AND public_id = %s
            """,
            (self.garden_id, outcome_id),
        ).fetchone()

        self.assertEqual(raised.exception.status_code, 409)
        assert task_after is not None
        assert outcome_after is not None
        self.assertEqual(str(task_after["status"]), "completed")
        self.assertEqual(int(task_after["completed_at_ms"]), now_ms)
        self.assertEqual(int(outcome_after["expires_at_ms"]), expires_at_ms)
        self.assertEqual(
            json.loads(str(outcome_after["recovery_action_json"]))["kind"],
            "restore_generated_watering_task",
        )

    def test_concurrent_restores_create_one_generated_watering_task(self) -> None:
        self._insert_plant("RESTORE-RACE", "Restore race", care_watering="regular moisture")
        now_ms = 1783180800000
        source_public_id = "water:RESTORE-RACE:2026-07-05"
        outcome_id = upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_covered_by_rain",
            source_type="task_generator",
            source_id="rain-restore-race",
            source_public_id=source_public_id,
            target_type="plant",
            target_id="RESTORE-RACE",
            title="Watering covered by rain",
            explanation="Rain covers this watering.",
            reason="Rain covers watering",
            plant_ids=("RESTORE-RACE",),
            metadata={"due_on": "2026-07-05", "plant_name": "Restore race"},
            recovery_action={
                "kind": "restore_generated_watering_task",
                "label": "Restore watering",
                "source_public_id": source_public_id,
                "target_type": "plant",
                "target_id": "RESTORE-RACE",
                "due_on": "2026-07-05",
                "plant_ids": ["RESTORE-RACE"],
                "plot_ids": [],
            },
            occurred_at_ms=now_ms,
            expires_at_ms=now_ms + 86_400_000,
        )
        self.conn.commit()
        outcome_barrier = threading.Barrier(2)
        task_barrier = threading.Barrier(2)

        def restore_once(_: int) -> str:
            conn = db.get_db()
            try:
                result = restore_attention_outcome(
                    _RestoreRaceConnection(conn, outcome_barrier, task_barrier),
                    garden_id=self.garden_id,
                    outcome_id=outcome_id,
                    user_id=self._owner_id,
                    now_ms=now_ms,
                )
                conn.commit()
                return result
            finally:
                db.return_db(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(restore_once, range(2)))

        tasks = self.conn.execute(
            """
            SELECT public_id, due_on, status
            FROM garden_tasks
            WHERE garden_id = %s
              AND rule_source = %s
            """,
            (self.garden_id, source_public_id),
        ).fetchall()
        outcome = self.conn.execute(
            """
            SELECT expires_at_ms
            FROM attention_outcomes
            WHERE garden_id = %s
              AND public_id = %s
            """,
            (self.garden_id, outcome_id),
        ).fetchone()

        assert results == ["restored", "restored"]
        assert len(tasks) == 1
        assert str(tasks[0]["due_on"]) == "2026-07-05"
        assert str(tasks[0]["status"]) == "pending"
        assert outcome is not None
        assert int(outcome["expires_at_ms"]) < now_ms

    def test_manual_and_automatic_rain_recovery_share_outcome_then_task_lock_order(
        self,
    ) -> None:
        self._insert_plant("RESTORE-LOCK-ORDER", "Restore lock order")
        now_ms = 1_783_180_800_000
        source_public_id = "water:RESTORE-LOCK-ORDER:2026-07-05"
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, status, severity,
                 due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES ('task_restore_lock_order', %s, 'water', 'Water lock order',
                    'pending', 'normal', '2026-07-05', %s, '{}', %s, %s)
            RETURNING id, public_id
            """,
            (self.garden_id, source_public_id, now_ms, now_ms),
        ).fetchone()
        assert task is not None
        self.conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (int(task["id"]), "RESTORE-LOCK-ORDER"),
        )
        recovery_action = {
            "kind": "restore_generated_watering_task",
            "label": "Restore watering",
            "source_public_id": source_public_id,
            "target_type": "plant",
            "target_id": "RESTORE-LOCK-ORDER",
            "due_on": "2026-07-05",
            "plant_ids": ["RESTORE-LOCK-ORDER"],
            "plot_ids": [],
        }
        outcome_id = upsert_attention_outcome(
            self.conn,
            garden_id=self.garden_id,
            provider="weather",
            outcome_type="watering_covered_by_rain",
            source_type="task_generator",
            source_id="rain-restore-lock-order",
            source_public_id=source_public_id,
            target_type="plant",
            target_id="RESTORE-LOCK-ORDER",
            title="Watering covered by rain",
            explanation="Rain covers this watering.",
            reason="Rain covers watering",
            plant_ids=("RESTORE-LOCK-ORDER",),
            metadata={"due_on": "2026-07-05", "plant_name": "Restore lock order"},
            recovery_action=recovery_action,
            occurred_at_ms=now_ms,
            expires_at_ms=now_ms + 86_400_000,
        )
        self.conn.commit()

        task_locked = threading.Event()
        allow_restore = threading.Event()
        automatic_outcome_locked = threading.Event()

        def manual_restore() -> str:
            conn = db.get_db()
            try:
                result = restore_generated_watering_task_from_attention_outcome(
                    _PauseAfterRestoreTaskLockConnection(
                        conn,
                        task_locked,
                        allow_restore,
                    ),
                    garden_id=self.garden_id,
                    outcome_public_id=outcome_id,
                    source_public_id=source_public_id,
                    target_id="RESTORE-LOCK-ORDER",
                    metadata={"due_on": "2026-07-05", "plant_name": "Restore lock order"},
                    recovery_action=recovery_action,
                    actor_user_id=self._owner_id,
                    now_ms=now_ms,
                )
                conn.commit()
                return result
            finally:
                db.return_db(conn)

        def automatic_restore() -> dict[str, int]:
            conn = db.get_db()
            try:
                result = reconcile_rain_watering_outcomes(
                    _ObserveAutomaticOutcomeLockConnection(conn, automatic_outcome_locked),
                    garden_id=self.garden_id,
                    now_ms=now_ms,
                )
                conn.commit()
                return result
            finally:
                db.return_db(conn)

        with (
            patch.dict(
                os.environ,
                {
                    "APP_ENV": "test",
                    "GARDENOPS_ATTENTION_FROZEN_NOW_MS": str(now_ms),
                    "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
                },
                clear=False,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            manual_future = pool.submit(manual_restore)
            self.assertTrue(task_locked.wait(timeout=5))
            automatic_future = pool.submit(automatic_restore)
            automatic_outcome_locked.wait(timeout=0.5)
            allow_restore.set()
            manual_result = manual_future.result(timeout=8)
            automatic_result = automatic_future.result(timeout=8)

        self.assertEqual(manual_result, "task_restore_lock_order")
        self.assertEqual(automatic_result, {"recovered": 0, "adjusted": 0, "closed": 0})


class TestTaskDescriptionOverrides(unittest.TestCase):
    def test_ai_prompt_skips_watering_tasks(self) -> None:
        mocked_client = MagicMock()
        spec = {
            "task_key": "water-1",
            "task_type": "water",
            "due_on": "2026-07-01",
            "plant": {
                "name": "Hydrangea",
                "category": "busker",
                "light": "halvskygge",
                "hardiness": "H5",
                "care_watering": "regular moisture",
                "care_soil": "",
                "care_planting": "",
                "care_maintenance": "",
                "care_notes": "",
            },
            "fallback_en": (
                "Water Hydrangea regularly in July. Why: steady moisture reduces drought stress."
            ),
            "fallback_no": (
                "Vann Hydrangea jevnlig i juli. Hvorfor: jevn fuktighet reduserer tørkestress."
            ),
        }

        with (
            patch.dict(
                "os.environ",
                {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            overrides = generate_task_description_overrides([spec], preferred_locale="en")

        self.assertEqual(overrides, {})
        mocked_client.messages.create.assert_not_called()

    def test_ai_prompt_skips_work_order_tasks(self) -> None:
        mocked_client = MagicMock()
        spec = {
            "task_key": "work-order-1",
            "task_type": "prune",
            "work_order": True,
            "due_on": "2026-03-01",
            "plant": {
                "name": "Grouped pruning",
                "category": "",
                "light": "",
                "hardiness": "",
                "care_watering": "",
                "care_soil": "",
                "care_planting": "",
                "care_maintenance": "",
                "care_notes": "",
            },
            "fallback_en": "Prune these plants this week.",
            "fallback_no": "Beskjær disse plantene denne uken.",
        }

        with (
            patch.dict(
                "os.environ",
                {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            overrides = generate_task_description_overrides([spec], preferred_locale="en")

        self.assertEqual(overrides, {})
        mocked_client.messages.create.assert_not_called()

    def test_ai_prompt_uses_preferred_locale_and_full_care_context(self) -> None:
        response_block = type(
            "ToolBlock",
            (),
            {
                "type": "tool_use",
                "name": "task_descriptions_batch",
                "input": {
                    "tasks": [
                        {
                            "task_key": "task-1",
                            "description_en": (
                                "Prune Glen Ample now. Why: it keeps fruiting wood young."
                            ),
                            "description_no": (
                                "Beskjær Glen Ample nå. Hvorfor: det holder fruktveden ung."
                            ),
                        }
                    ]
                },
            },
        )()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = type(
            "AnthropicResponse",
            (),
            {"content": [response_block]},
        )()

        spec = {
            "task_key": "task-1",
            "task_type": "prune",
            "due_on": "2026-03-01",
            "plant": {
                "name": "Glen Ample",
                "category": "baerbusker",
                "light": "sol",
                "hardiness": "H5",
                "care_watering": "regular moisture",
                "care_soil": "well-drained fertile soil",
                "care_planting": "sheltered site with support",
                "care_maintenance": "prune old canes after harvest",
                "care_notes": "renew fruiting canes every year",
            },
            "fallback_en": (
                "Prune Glen Ample before spring growth starts. "
                "Why: removing old canes improves airflow and fruiting."
            ),
            "fallback_no": (
                "Beskjær Glen Ample før vårveksten starter. "
                "Hvorfor: gamle skudd må bort for bedre lufting og frukting."
            ),
        }

        with (
            patch.dict(
                "os.environ",
                {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            overrides = generate_task_description_overrides([spec], preferred_locale="no")

        self.assertEqual(
            overrides["task-1"],
            (
                "Prune Glen Ample now. Why: it keeps fruiting wood young.",
                "Beskjær Glen Ample nå. Hvorfor: det holder fruktveden ung.",
            ),
        )
        call = mocked_client.messages.create.call_args
        self.assertIsNotNone(call)
        kwargs = call.kwargs
        self.assertIn("preferred locale is 'no'", kwargs["messages"][0]["content"])
        self.assertIn(
            '"care_soil": "well-drained fertile soil"',
            kwargs["messages"][0]["content"],
        )
        self.assertIn(
            '"care_planting": "sheltered site with support"',
            kwargs["messages"][0]["content"],
        )
        self.assertIn(
            '"care_notes": "renew fruiting canes every year"',
            kwargs["messages"][0]["content"],
        )

    def test_ai_prompt_filters_mixed_batches_to_pruning_only(self) -> None:
        response_block = type(
            "ToolBlock",
            (),
            {
                "type": "tool_use",
                "name": "task_descriptions_batch",
                "input": {
                    "tasks": [
                        {
                            "task_key": "prune-1",
                            "description_en": (
                                "Prune currant now. Why: it keeps fruiting wood productive."
                            ),
                            "description_no": (
                                "Beskjær rips nå. Hvorfor: det holder fruktveden produktiv."
                            ),
                        }
                    ]
                },
            },
        )()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = type(
            "AnthropicResponse",
            (),
            {"content": [response_block]},
        )()
        specs = [
            {
                "task_key": "prune-1",
                "task_type": "prune",
                "due_on": "2026-03-01",
                "plant": {
                    "name": "Currant",
                    "category": "baerbusker",
                    "light": "sol",
                    "hardiness": "H6",
                    "care_watering": "",
                    "care_soil": "",
                    "care_planting": "",
                    "care_maintenance": "prune after harvest",
                    "care_notes": "",
                },
                "fallback_en": (
                    "Prune currant before spring growth starts. "
                    "Why: removing old wood improves fruiting."
                ),
                "fallback_no": (
                    "Beskjær rips før vårveksten starter. "
                    "Hvorfor: fjerning av gammelt treverk bedrer fruktingen."
                ),
            },
            {
                "task_key": "water-1",
                "task_type": "water",
                "due_on": "2026-07-01",
                "plant": {
                    "name": "Hydrangea",
                    "category": "busker",
                    "light": "halvskygge",
                    "hardiness": "H5",
                    "care_watering": "regular moisture",
                    "care_soil": "",
                    "care_planting": "",
                    "care_maintenance": "",
                    "care_notes": "",
                },
                "fallback_en": (
                    "Water Hydrangea regularly in July. "
                    "Why: steady moisture reduces drought stress."
                ),
                "fallback_no": (
                    "Vann Hydrangea jevnlig i juli. Hvorfor: jevn fuktighet reduserer tørkestress."
                ),
            },
        ]

        with (
            patch.dict(
                "os.environ",
                {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            overrides = generate_task_description_overrides(specs, preferred_locale="en")

        self.assertEqual(
            overrides,
            {
                "prune-1": (
                    "Prune currant now. Why: it keeps fruiting wood productive.",
                    "Beskjær rips nå. Hvorfor: det holder fruktveden produktiv.",
                )
            },
        )
        call = mocked_client.messages.create.call_args
        self.assertIsNotNone(call)
        payload = call.kwargs["messages"][0]["content"]
        self.assertIn('"task_key": "prune-1"', payload)
        self.assertNotIn('"task_key": "water-1"', payload)


if __name__ == "__main__":
    unittest.main()
