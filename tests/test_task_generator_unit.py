"""Unit tests for gardenops.services.task_generator."""

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


class TestGenerateTasksPruning(DbTestBase):
    def test_creates_prune_task_march(self) -> None:
        self._insert_plant("PR1", "Bush", category="busker")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'seasonal_prune:PR1%'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "prune"

    def test_creates_prune_task_october(self) -> None:
        self._insert_plant("PR2", "Tree", category="traer")
        generate_tasks(self.conn, self.garden_id, 10, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'seasonal_prune:PR2%'",
        ).fetchone()
        assert task is not None

    def test_unicode_category_traer(self) -> None:
        self._insert_plant("PR3", "Tree", category="tr\u00e6r")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'seasonal_prune:PR3%'",
        ).fetchone()
        assert task is not None

    def test_no_prune_wrong_category(self) -> None:
        self._insert_plant("PR4", "Flower", category="stauder")
        generate_tasks(self.conn, self.garden_id, 3, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'seasonal_prune:PR4%'",
        ).fetchone()
        assert task is None

    def test_no_prune_wrong_month(self) -> None:
        self._insert_plant("PR5", "Bush", category="busker")
        generate_tasks(self.conn, self.garden_id, 6, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'seasonal_prune:PR5%'",
        ).fetchone()
        assert task is None


class TestGenerateTasksFertilize(DbTestBase):
    def test_creates_fertilize_task(self) -> None:
        self._insert_plant(
            "FT1",
            "Rose",
            care_maintenance="Fertilize in spring",
        )
        generate_tasks(self.conn, self.garden_id, 4, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'fertilize:FT1%'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "fertilize"

    def test_norwegian_gjodsl_keyword(self) -> None:
        self._insert_plant(
            "FT2",
            "Busk",
            care_maintenance="Gj\u00f8dsles i april",
        )
        generate_tasks(self.conn, self.garden_id, 5, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'fertilize:FT2%'",
        ).fetchone()
        assert task is not None

    def test_no_fertilize_wrong_month(self) -> None:
        self._insert_plant("FT3", "Plant", care_maintenance="Fertilize")
        generate_tasks(self.conn, self.garden_id, 8, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'fertilize:FT3%'",
        ).fetchone()
        assert task is None


class TestGenerateTasksWatering(DbTestBase):
    def test_creates_water_task(self) -> None:
        self._insert_plant("WT1", "Thirsty", care_watering="Water regularly")
        generate_tasks(self.conn, self.garden_id, 7, 2026, None)
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE 'water:WT1%'",
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "water"

    def test_norwegian_keyword(self) -> None:
        self._insert_plant("WT2", "Plante", care_watering="Vannes jevnlig")
        generate_tasks(self.conn, self.garden_id, 6, 2026, None)
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


if __name__ == "__main__":
    unittest.main()
