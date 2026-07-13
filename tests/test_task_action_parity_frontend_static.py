from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontend_source(relative_path: str) -> str:
    return (ROOT / "frontend" / "src" / relative_path).read_text(encoding="utf-8")


class TaskActionParityFrontendStaticTests(unittest.TestCase):
    def test_task_cards_render_the_wired_reschedule_action(self) -> None:
        source = frontend_source("components/tasks.ts")

        self.assertIn("const rescheduleBtn", source)
        self.assertIn('rescheduleBtn.textContent = t("tasks.action_reschedule")', source)
        self.assertIn("cbs.onReschedule(task)", source)

    def test_snooze_correction_uses_one_shared_two_second_policy(self) -> None:
        helper = frontend_source("features/taskSnoozeFlow.ts")
        tasks_tab = frontend_source("tabs/tasksTab.ts")
        quick_actions = frontend_source("features/quickActionsFeature.ts")
        plot = frontend_source("components/plotInteractions.ts")

        self.assertIn("durationMs: 2_000", helper)
        for surface in (tasks_tab, quick_actions, plot):
            self.assertIn("getTaskSnoozeCorrectionNotice", surface)
        for surface in (quick_actions, plot):
            self.assertIn("openTaskDateDialog", surface)
            self.assertNotIn("window.prompt", surface)

    def test_task_offline_replay_keeps_each_supported_action_idempotent(self) -> None:
        tasks_tab = frontend_source("tabs/tasksTab.ts")
        plot = frontend_source("components/plotInteractions.ts")
        replay = frontend_source("features/offlineFeature.ts")

        for draft_type in (
            "task_complete",
            "task_skip",
            "task_snooze",
            "task_reschedule",
        ):
            self.assertIn(draft_type, tasks_tab)
            self.assertIn(draft_type, plot)
        for action in ("task_complete", "task_skip", "task_snooze", "task_reschedule"):
            self.assertIn(f"{action}: async", replay)
        self.assertGreaterEqual(replay.count("operationId: draft.operation_id"), 4)

    def test_plot_cards_expose_each_supported_task_action(self) -> None:
        source = frontend_source("components/plotInteractions.ts")

        for callback in ("onComplete", "onSkip", "onSnooze", "onReschedule"):
            self.assertIn(callback, source)
        for action_class in (
            "action-complete",
            "action-skip",
            "action-snooze",
            "action-reschedule",
        ):
            self.assertIn(action_class, source)

    def test_mobile_quick_actions_is_a_focus_managed_dialog(self) -> None:
        layout = frontend_source("components/layout.ts")
        feature = frontend_source("features/quickActionsFeature.ts")

        marker = 'id="mobile-quick-actions"'
        start = layout.index(marker)
        sheet_markup = layout[start : start + 500]
        self.assertIn('role="dialog"', sheet_markup)
        self.assertIn('aria-modal="true"', sheet_markup)
        self.assertIn('aria-hidden="true"', sheet_markup)
        self.assertIn("inert", sheet_markup)
        self.assertIn('aria-controls="mobile-quick-actions"', layout)
        self.assertIn('aria-expanded="false"', layout)
        self.assertIn("trapFocus(sheet)", feature)
        self.assertIn("setQuickActionBackgroundInert(sheet, true)", feature)
        self.assertIn("setQuickActionBackgroundInert(sheet, false)", feature)
        self.assertIn('event.key !== "Escape"', feature)
        self.assertIn("fab.focus()", feature)
        self.assertIn("onClose: () => focusQuickActionSheet(true)", feature)


if __name__ == "__main__":
    unittest.main()
