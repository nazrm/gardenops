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

    def test_persisted_windows_require_confirmation_at_both_bounds_for_every_type(self) -> None:
        policy = frontend_source("features/taskSnoozePolicy.ts")
        safety = policy.split("export function taskSnoozeDateSafety", 1)[1].split(
            "export function taskSnoozePolicy", 1
        )[0]

        self.assertIn("dateFromValue(task.window_start_on)", safety)
        self.assertIn("snoozeUntil < windowStart", safety)
        self.assertIn("dateFromValue(task.window_end_on)", safety)
        self.assertIn("snoozeUntil > windowEnd", safety)
        self.assertNotIn('task.task_type === "prune"', safety)
        self.assertNotIn('task.task_type === "fertilize"', safety)

    def test_task_dialogs_await_submission_and_keep_recoverable_failure_state(self) -> None:
        date_flow = frontend_source("features/taskSnoozeFlow.ts")
        completion_flow = frontend_source("features/taskCompletionFlow.ts")

        for source, awaited_call, pending_guard in (
            (date_flow, "await onConfirm(", "okBtn.disabled = pending"),
            (completion_flow, "await onConfirm(body)", "confirm.disabled = submitting"),
        ):
            self.assertIn("Promise<boolean | void>", source)
            self.assertIn(awaited_call, source)
            self.assertIn("result === false", source)
            self.assertIn('t("tasks.dialog_submit_failed")', source)
            self.assertIn(pending_guard, source)
            self.assertIn("dialog.isConnected", source)

        self.assertIn('class="task-date-dialog-feedback"', date_flow)
        self.assertIn("input.value", date_flow)
        self.assertIn("submitError", completion_flow)

    def test_confirmed_window_snoozes_carry_the_server_confirmation_bit(self) -> None:
        api = frontend_source("services/api.ts")
        flow = frontend_source("features/taskSnoozeFlow.ts")
        replay = frontend_source("features/offlineFeature.ts")

        self.assertIn("confirm_outside_window?: boolean;", api)
        self.assertIn("Promise<boolean | void>", flow)
        self.assertIn("if (!input.value || !input.reportValidity() || safety?.blocked)", flow)
        self.assertIn("confirmOutsideWindow = true;", flow)
        self.assertIn("confirmOutsideWindow || undefined", flow)
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
            "tasks.snooze_window_start_warning",
            "tasks.snooze_weather_date_limit",
            "tasks.snooze_weather_remains_due",
            "tasks.snooze_recurrence_date_limit",
            "tasks.snooze_recurrence_remains_due",
        ):
            self.assertEqual(i18n.count(f'"{key}"'), 2)


if __name__ == "__main__":
    unittest.main()
