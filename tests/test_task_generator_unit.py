"""Unit tests for gardenops.services.task_generator."""

import json
import unittest

from gardenops.services.task_generator import (
    _bloom_months,
    _parse_month,
    _rule_exists,
    generate_tasks,
)
from tests.base import DbTestBase


class TestParseMonth(unittest.TestCase):
    def test_numeric_strings(self) -> None:
        assert _parse_month("1") == 1
        assert _parse_month("12") == 12
        assert _parse_month("6") == 6

    def test_out_of_range_numeric(self) -> None:
        assert _parse_month("0") == 0
        assert _parse_month("13") == 0

    def test_norwegian_month_names(self) -> None:
        assert _parse_month("jan") == 1
        assert _parse_month("januar") == 1
        assert _parse_month("mai") == 5
        assert _parse_month("juni") == 6
        assert _parse_month("okt") == 10
        assert _parse_month("desember") == 12

    def test_english_month_names(self) -> None:
        assert _parse_month("may") == 5
        assert _parse_month("oct") == 10
        assert _parse_month("dec") == 12

    def test_case_insensitive(self) -> None:
        assert _parse_month("JAN") == 1
        assert _parse_month("Juni") == 6

    def test_unknown(self) -> None:
        assert _parse_month("xyz") == 0
        assert _parse_month("") == 0


class TestBloomMonths(unittest.TestCase):
    def test_range(self) -> None:
        assert _bloom_months("juni-august") == {6, 7, 8}
        assert _bloom_months("6-8") == {6, 7, 8}

    def test_single_month(self) -> None:
        assert _bloom_months("mai") == {5}

    def test_comma_separated(self) -> None:
        result = _bloom_months("mai,juni,juli")
        assert 5 in result
        assert 6 in result
        assert 7 in result

    def test_empty(self) -> None:
        assert _bloom_months("") == set()
        assert _bloom_months("None") == set()

    def test_en_dash_separator(self) -> None:
        assert _bloom_months("jun\u2013aug") == {6, 7, 8}


class TestRuleExists(DbTestBase):
    def test_no_existing_rule(self) -> None:
        assert _rule_exists(self.conn, self.garden_id, "bloom_observe:X:2026-06") is False

    def test_existing_rule(self) -> None:
        self._insert_plant("X1", "Test", category="busker", bloom_month="juni")
        generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        assert _rule_exists(self.conn, self.garden_id, "bloom_observe:X1:2026-06") is True


class TestGenerateTasksBloomObservation(DbTestBase):
    def test_creates_bloom_task(self) -> None:
        self._insert_plant("BL1", "Bloomer", bloom_month="juni")
        result = generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        assert result["created"] >= 1
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'bloom_observe:BL1%'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "observe_bloom"

    def test_no_bloom_task_wrong_month(self) -> None:
        self._insert_plant("BL2", "Bloomer", bloom_month="juni")
        generate_tasks(self.conn, self.garden_id, 1, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'bloom_observe:BL2%'",
        ).fetchone()
        assert task is None

    def test_bloom_generation_prefers_local_observed_month(self) -> None:
        self._insert_plant("BL-LOCAL", "Local Bloomer", bloom_month="juni")
        now_ms = 1_783_180_800_000
        entry = self.conn.execute(
            """
            INSERT INTO garden_journal_entries
                (public_id, garden_id, event_type, occurred_on, title, notes,
                 metadata_json, actor_user_id, created_at_ms, updated_at_ms)
            VALUES ('jrn_local_bloom', %s, 'bloomed', '2025-07-15', '', '',
                    '{}', %s, %s, %s)
            RETURNING id
            """,
            (self.garden_id, self._owner_id, now_ms, now_ms),
        ).fetchone()
        self.conn.execute(
            "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (int(entry["id"]), "BL-LOCAL"),
        )
        self.conn.commit()

        generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        june_task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source = 'bloom_observe:BL-LOCAL:2026-06'",
        ).fetchone()
        assert june_task is None

        generate_tasks(self.conn, self.garden_id, 7, 2026, None)
        july_task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source = 'bloom_observe:BL-LOCAL:2026-07'",
        ).fetchone()
        assert july_task is not None


class TestGenerateTasksPruning(DbTestBase):
    def test_creates_prune_task_march(self) -> None:
        self._insert_plant("PR1", "Bush", category="busker")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "prune"
        assert str(task["rule_source"]).startswith("work_order:prune:")
        metadata = json.loads(str(task["metadata_json"]))
        assert metadata["work_order"] is True
        assert metadata["grouped_task_type"] == "prune"

    def test_creates_prune_task_october(self) -> None:
        self._insert_plant("PR2", "Tree", category="traer")
        generate_tasks(self.conn, self.garden_id, 10, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchone()
        assert task is not None

    def test_unicode_category_traer(self) -> None:
        self._insert_plant("PR3", "Tree", category="tr\u00e6r")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchone()
        assert task is not None

    def test_no_prune_wrong_category(self) -> None:
        self._insert_plant("PR4", "Flower", category="stauder")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchone()
        assert task is None

    def test_no_prune_wrong_month(self) -> None:
        self._insert_plant("PR5", "Bush", category="busker")
        generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchone()
        assert task is None

    def test_groups_prune_tasks_by_week(self) -> None:
        self._insert_plant("PR6", "Currant", category="busker")
        self._insert_plant("PR7", "Apple", category="traer")
        result = generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        tasks = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchall()
        assert result["created"] == 1
        assert len(tasks) == 1
        task = tasks[0]
        assert task["title"] == "Prune 2 plants"
        links = self.conn.execute(
            "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id",
            (task["id"],),
        ).fetchall()
        assert [row["plt_id"] for row in links] == ["PR6", "PR7"]

    def test_grouped_prune_task_links_current_multi_plot_placements(self) -> None:
        self._insert_plant("PRP-OUT-IN", "Currant", category="busker")
        self._insert_plant("PRP-SHARED", "Apple", category="traer")
        self._insert_plant("PRP-UNPLACED", "Gooseberry", category="busker")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES
                ('PRP-OUT-A', %s, 'P', 'Prune bed', 1, 3, 1, '', ''),
                ('PRP-OUT-B', %s, 'P', 'Prune bed', 2, 3, 2, '', ''),
                ('PRP-IN', %s, 'I', 'Greenhouse', 1, NULL, NULL, '', '')
            """,
            (self.garden_id, self.garden_id, self.garden_id),
        )
        self.conn.execute(
            """
            INSERT INTO plot_plants (plot_id, plt_id, quantity)
            VALUES
                ('PRP-OUT-A', 'PRP-OUT-IN', 1),
                ('PRP-IN', 'PRP-OUT-IN', 1),
                ('PRP-OUT-A', 'PRP-SHARED', 1),
                ('PRP-OUT-B', 'PRP-SHARED', 1)
            """,
        )
        self.conn.commit()

        generate_tasks(self.conn, self.garden_id, 3, 2026, None)

        task = self.conn.execute(
            "SELECT id FROM garden_tasks WHERE task_type = 'prune'",
        ).fetchone()
        assert task is not None
        plant_rows = self.conn.execute(
            "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id",
            (int(task["id"]),),
        ).fetchall()
        plot_rows = self.conn.execute(
            "SELECT plot_id FROM garden_task_plots WHERE task_id = %s ORDER BY plot_id",
            (int(task["id"]),),
        ).fetchall()

        assert [str(row["plt_id"]) for row in plant_rows] == [
            "PRP-OUT-IN",
            "PRP-SHARED",
            "PRP-UNPLACED",
        ]
        assert [str(row["plot_id"]) for row in plot_rows] == [
            "PRP-IN",
            "PRP-OUT-A",
            "PRP-OUT-B",
        ]


class TestGenerateTasksFertilize(DbTestBase):
    def test_creates_fertilize_task(self) -> None:
        self._insert_plant(
            "FT1",
            "Rose",
            care_maintenance="Fertilize in spring",
        )
        generate_tasks(self.conn, self.garden_id, 4, 2026, None)
        task = self.conn.execute(
            """
            SELECT * FROM garden_tasks
            WHERE task_type = 'fertilize'
            ORDER BY due_on
            LIMIT 1
            """,
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "fertilize"
        assert str(task["rule_source"]).startswith("work_order:fertilize:")

    def test_norwegian_gjodsl_keyword(self) -> None:
        self._insert_plant(
            "FT2",
            "Busk",
            care_maintenance="Gj\u00f8dsles i april",
        )
        generate_tasks(self.conn, self.garden_id, 5, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'fertilize'",
        ).fetchone()
        assert task is not None

    def test_no_fertilize_wrong_month(self) -> None:
        self._insert_plant("FT3", "Plant", care_maintenance="Fertilize")
        generate_tasks(self.conn, self.garden_id, 8, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'fertilize'",
        ).fetchone()
        assert task is None

    def test_groups_fertilize_tasks_by_week(self) -> None:
        self._insert_plant("FT4", "Rose", care_maintenance="Fertilize monthly")
        self._insert_plant("FT5", "Dahlia", care_maintenance="Fertilize monthly")
        result = generate_tasks(self.conn, self.garden_id, 4, 2026, None)
        tasks = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE task_type = 'fertilize' ORDER BY due_on",
        ).fetchall()
        assert result["created"] == 2
        assert len(tasks) == 2
        assert [task["title"] for task in tasks] == [
            "Fertilize 2 plants",
            "Fertilize 2 plants",
        ]
        for task in tasks:
            links = self.conn.execute(
                "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id",
                (task["id"],),
            ).fetchall()
            assert [row["plt_id"] for row in links] == ["FT4", "FT5"]

    def test_replaces_pending_legacy_generated_prune_tasks(self) -> None:
        self._insert_plant("LG1", "Legacy Bush", category="busker")
        now_ms = 1_800_000_000_000
        row = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, task_type, title, description, status, severity,
                 due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES (%s, 'prune', 'Prune: Legacy Bush', '', 'pending', 'normal',
                    '2026-03-01', 'seasonal_prune:LG1:2026-03', '{}', %s, %s)
            RETURNING id
            """,
            (self.garden_id, now_ms, now_ms),
        ).fetchone()
        self.conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (row["id"], "LG1"),
        )
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        legacy = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source = 'seasonal_prune:LG1:2026-03'",
        ).fetchone()
        grouped = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'work_order:prune:%'",
        ).fetchall()
        assert legacy is None
        assert len(grouped) == 1


class TestGenerateTasksWatering(DbTestBase):
    def test_creates_water_task(self) -> None:
        self._insert_plant("WT1", "Thirsty", care_watering="Water regularly")
        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            None,
            now_ms=1_782_864_000_000,
        )
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'water:WT1%'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "water"

    def test_norwegian_keyword(self) -> None:
        self._insert_plant("WT2", "Plante", care_watering="Vannes jevnlig")
        generate_tasks(
            self.conn,
            self.garden_id,
            6,
            2026,
            None,
            now_ms=1_780_272_000_000,
        )
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'water:WT2%'",
        ).fetchone()
        assert task is not None

    def test_no_water_wrong_month(self) -> None:
        self._insert_plant("WT3", "Plant", care_watering="Water regularly")
        generate_tasks(self.conn, self.garden_id, 1, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'water:WT3%'",
        ).fetchone()
        assert task is None

    def test_watering_stays_ungrouped(self) -> None:
        self._insert_plant("WT4", "Hydrangea", care_watering="Water regularly")
        self._insert_plant("WT5", "Astilbe", care_watering="Water often")
        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            None,
            now_ms=1_782_864_000_000,
        )
        tasks = self.conn.execute(
            "SELECT id, rule_source FROM garden_tasks WHERE task_type = 'water'",
        ).fetchall()
        assert len(tasks) == 8
        for task in tasks:
            links = self.conn.execute(
                "SELECT plt_id FROM garden_task_plants WHERE task_id = %s",
                (task["id"],),
            ).fetchall()
            assert len(links) == 1
            assert str(task["rule_source"]).startswith(f"water:{links[0]['plt_id']}:")


class TestGenerateTasksSowing(DbTestBase):
    def test_no_auto_sow_for_seed_category_even_with_sowing_instructions(self) -> None:
        self._insert_plant(
            "SW1",
            "Seed",
            category="fr\u00f8",
            care_planting="Start indoors in March before planting out later",
        )
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'sow:SW1%'",
        ).fetchone()
        assert task is None

    def test_no_auto_sow_for_seed_category_without_explicit_sowing_instructions(self) -> None:
        self._insert_plant("SW0", "Seed", category="fr\u00f8")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'sow:SW0%'",
        ).fetchone()
        assert task is None


class TestGenerateTasksBulbPlanting(DbTestBase):
    def test_creates_plant_out_task(self) -> None:
        self._insert_plant("BU1", "Tulip", category="l\u00f8k")
        generate_tasks(self.conn, self.garden_id, 9, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'plant_out:BU1%'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "plant_out"

    def test_no_bulb_wrong_month(self) -> None:
        self._insert_plant("BU2", "Tulip", category="l\u00f8k")
        generate_tasks(self.conn, self.garden_id, 5, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'plant_out:BU2%'",
        ).fetchone()
        assert task is None


class TestGenerateTasksDedup(DbTestBase):
    def test_second_run_skips(self) -> None:
        self._insert_plant("DD1", "Bloomer", bloom_month="juni")
        r1 = generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        r2 = generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        assert r1["created"] >= 1
        assert r2["created"] == 0
        assert r2["skipped"] >= 1

    def test_uses_supplied_timestamp_for_generated_rows(self) -> None:
        self._insert_plant("CLOCK1", "Clock plant", care_watering="Water regularly")
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('CLOCK-OUT', %s, 'C', 'Clock bed', 1, 2, 2, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('CLOCK-OUT', 'CLOCK1', 1)",
        )
        self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'normal', 'Clock rain', '',
                    '2026-07-01', '2026-07-01', '{}', 1)
            """,
            (self.garden_id,),
        )
        self.conn.commit()
        now_ms = 1_777_000_000_123

        result = generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            None,
            now_ms=now_ms,
        )

        task_rows = self.conn.execute(
            """
            SELECT created_at_ms, updated_at_ms
            FROM garden_tasks
            WHERE garden_id = %s
            ORDER BY rule_source
            """,
            (self.garden_id,),
        ).fetchall()
        outcome = self.conn.execute(
            """
            SELECT occurred_at_ms, created_at_ms, updated_at_ms
            FROM attention_outcomes
            WHERE garden_id = %s
              AND outcome_type = 'watering_covered_by_rain'
            """,
            (self.garden_id,),
        ).fetchone()

        assert result == {"created": 3, "skipped": 1, "rain_suppressed": 1}
        assert [(int(row["created_at_ms"]), int(row["updated_at_ms"])) for row in task_rows] == [
            (now_ms, now_ms),
            (now_ms, now_ms),
            (now_ms, now_ms),
        ]
        assert outcome is not None
        assert (
            int(outcome["occurred_at_ms"]),
            int(outcome["created_at_ms"]),
            int(outcome["updated_at_ms"]),
        ) == (now_ms, now_ms, now_ms)


if __name__ == "__main__":
    unittest.main()
