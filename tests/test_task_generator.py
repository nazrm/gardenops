"""Tests for C7 recurring care schedules and C8 harvest window alerts."""

import unittest
from unittest.mock import MagicMock, patch

from gardenops.services.task_generator import (
    generate_task_description_overrides,
    generate_tasks,
    infer_task_description,
)
from tests.base import DbTestBase


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
        generate_tasks(self.conn, self.garden_id, 7, 2026, self._owner_id)
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
