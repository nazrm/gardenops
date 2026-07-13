"""Tests for C7 recurring care schedules and C8 harvest window alerts."""

import json
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
        if "FROM attention_outcomes" in sql:
            self._outcome_barrier.wait(timeout=10)
        elif "FROM garden_tasks" in sql and "rule_source = %s" in sql:
            try:
                # With the advisory lock, the second request cannot reach this
                # read. Time out so the lock holder can finish its transaction.
                self._task_barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
        return self._connection.execute(query, params)


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
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE garden_id = %s AND task_type = 'fertilize'",
            (self.garden_id,),
        ).fetchone()
        assert task is not None
        cleared = dict(task)
        cleared["description"] = ""
        en, no = infer_task_description(self.conn, cleared)
        assert en, "EN description should be non-empty"
        assert no, "NO description should be non-empty"
        assert "Dahlia" in en
        assert "Dahlia" in no

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
