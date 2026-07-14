from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontend_source(relative_path: str) -> str:
    return (ROOT / "frontend" / "src" / relative_path).read_text(encoding="utf-8")


class TaskSnoozePolicyFrontendStaticTests(unittest.TestCase):
    def test_selected_date_policy_limits_weather_and_weekly_recurrence(self) -> None:
        policy = frontend_source("features/taskSnoozePolicy.ts")

        for fragment in (
            "export function taskSnoozeDateSafety",
            "weather_valid_until",
            'task.rule_source.trim().startsWith("auto:")',
            "snoozeUntil > weatherDeadline",
            "next_recurrence_on",
            "snoozeUntil >= nextRecurrence",
            "taskSnoozeMaximumDate",
            "snooze_weather_remains_due",
            "snooze_recurrence_remains_due",
        ):
            self.assertIn(fragment, policy)

    def test_date_dialog_rechecks_the_user_selected_date_before_submission(self) -> None:
        flow = frontend_source("features/taskSnoozeFlow.ts")

        self.assertIn("getDateSafety", flow)
        self.assertIn("getDateSafety?.(input.value)", flow)
        self.assertIn('input.addEventListener("input", updateDateSafety)', flow)
        self.assertIn('input.addEventListener("change", updateDateSafety)', flow)
        self.assertIn("safety?.blocked", flow)
        self.assertIn("safety?.confirmationRequired", flow)
        self.assertIn("confirmDialog(", flow)

    def test_confirmed_window_snoozes_carry_the_server_confirmation_bit(self) -> None:
        api = frontend_source("services/api.ts")
        flow = frontend_source("features/taskSnoozeFlow.ts")
        replay = frontend_source("features/offlineFeature.ts")

        self.assertIn("confirm_outside_window?: boolean;", api)
        self.assertIn("onConfirm: (date: string, confirmOutsideWindow?: boolean) => void;", flow)
        self.assertIn("if (!input.value || !input.reportValidity() || safety?.blocked)", flow)
        self.assertIn("onConfirm(input.value, true);", flow)
        self.assertIn('payload["confirm_outside_window"] === true', replay)
        self.assertIn("body.confirm_outside_window = true;", replay)

        for relative_path in (
            "tabs/tasksTab.ts",
            "tabs/calendarTab.ts",
            "features/quickActionsFeature.ts",
            "components/plotInteractions.ts",
        ):
            source = frontend_source(relative_path)
            self.assertIn("confirmOutsideWindow", source)
            self.assertIn("confirm_outside_window: true", source)

    def test_every_task_surface_uses_the_shared_selected_date_guard(self) -> None:
        for relative_path in (
            "tabs/tasksTab.ts",
            "tabs/calendarTab.ts",
            "components/plotInteractions.ts",
            "features/quickActionsFeature.ts",
        ):
            source = frontend_source(relative_path)
            self.assertIn("taskSnoozeDateSafety", source)
            self.assertIn("getDateSafety", source)

        calendar = frontend_source("tabs/calendarTab.ts")
        self.assertIn("fetchTaskApi", calendar)
        self.assertIn("loadCalendarTaskForSnooze", calendar)

    def test_snooze_safety_messages_are_localized_in_both_languages(self) -> None:
        i18n = frontend_source("core/i18n.ts")

        for key in (
            "tasks.snooze_confirm_anyway",
            "tasks.snooze_weather_date_limit",
            "tasks.snooze_weather_remains_due",
            "tasks.snooze_recurrence_date_limit",
            "tasks.snooze_recurrence_remains_due",
        ):
            self.assertEqual(i18n.count(f'"{key}"'), 2)


if __name__ == "__main__":
    unittest.main()
