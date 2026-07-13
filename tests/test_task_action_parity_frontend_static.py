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
        calendar = frontend_source("tabs/calendarTab.ts")

        self.assertIn("durationMs: 2_000", helper)
        self.assertIn("allowMissingTask: true", tasks_tab)
        self.assertIn("expectedGardenId: task.garden_id", tasks_tab)
        self.assertIn("function isCurrentTaskAction", tasks_tab)
        for surface in (tasks_tab, quick_actions, plot, calendar):
            self.assertIn("getTaskSnoozeCorrectionNotice", surface)
        for surface in (quick_actions, plot, calendar):
            self.assertIn("openTaskDateDialog", surface)
            self.assertNotIn("window.prompt", surface)

    def test_mixed_batch_snooze_requires_a_manual_date_instead_of_first_task_policy(self) -> None:
        tasks_tab = frontend_source("tabs/tasksTab.ts")
        i18n = frontend_source("core/i18n.ts")

        self.assertIn("function batchSnoozePolicy", tasks_tab)
        self.assertIn("selectedTasks.map((task) => taskSnoozePolicy(task))", tasks_tab)
        self.assertIn(
            "policies.every((policy) => policy.defaultDate === first.defaultDate)", tasks_tab
        )
        self.assertIn('t("tasks.batch_snooze_mixed_warning")', tasks_tab)
        self.assertNotIn("const firstSelected = taskItems.find", tasks_tab)
        self.assertIn('"tasks.batch_snooze_mixed_warning"', i18n)

    def test_every_task_surface_keeps_a_manual_snooze_date_command(self) -> None:
        task_cards = frontend_source("components/tasks.ts")
        tasks_tab = frontend_source("tabs/tasksTab.ts")
        calendar = frontend_source("tabs/calendarTab.ts")
        quick_actions = frontend_source("components/quickActions.ts")
        quick_feature = frontend_source("features/quickActionsFeature.ts")
        plot = frontend_source("components/plotInteractions.ts")

        for source in (task_cards, tasks_tab, calendar, quick_actions, quick_feature, plot):
            self.assertIn("onSnoozeDate", source)
        self.assertGreaterEqual(calendar.count('t("tasks.snooze_change_date")'), 1)
        self.assertIn("openSnoozeDateDialog", tasks_tab)
        self.assertIn("openQuickSnoozeDateDialog", quick_feature)
        self.assertIn("openPlotSnoozeDateDialog", plot)

    def test_quick_and_plot_task_pickers_include_today_actionable_snoozed_tasks(self) -> None:
        quick_actions = frontend_source("features/quickActionsFeature.ts")
        plot = frontend_source("components/plotInteractions.ts")

        self.assertGreaterEqual(
            quick_actions.count('tk.status === "pending" || tk.status === "snoozed"'),
            2,
        )
        self.assertIn('fetchTasksApi({ plot_id: plotId, view: "today" })', plot)
        self.assertNotIn('fetchTasksApi({ plot_id: plotId, status: "pending" })', plot)

    def test_today_open_task_resets_filters_reveals_and_focuses_the_target(self) -> None:
        app = frontend_source("app.ts")
        tasks_tab = frontend_source("tabs/tasksTab.ts")
        task_cards = frontend_source("components/tasks.ts")

        self.assertIn("openTaskFromAttention", app)
        self.assertIn("action.target_id || item.target_id", app)
        self.assertIn("export async function openTaskFromAttention", tasks_tab)
        self.assertIn('tasksView = "today";', tasks_tab)
        self.assertIn("tasksOffset = 0;", tasks_tab)
        self.assertIn('typeFilter.value = "";', tasks_tab)
        self.assertIn('statusFilter.value = "";', tasks_tab)
        self.assertIn("fetchTaskApi(options.focusTaskId)", tasks_tab)
        self.assertIn("focusTaskCard", tasks_tab)
        self.assertIn('card.dataset["taskId"] = task.id;', task_cards)

    def test_task_and_calendar_loads_reject_stale_garden_responses(self) -> None:
        app = frontend_source("app.ts")
        tasks_tab = frontend_source("tabs/tasksTab.ts")
        calendar = frontend_source("tabs/calendarTab.ts")

        self.assertIn("tasksTabModule?.resetTasksForGardenSwitch();", app)
        self.assertIn("export function resetTasksForGardenSwitch", tasks_tab)
        self.assertIn("let tasksRequestGeneration = 0;", tasks_tab)
        self.assertIn("function isCurrentTasksRequest", tasks_tab)
        self.assertIn("function isCurrentTask(", tasks_tab)
        self.assertIn("getActiveGardenContext", tasks_tab)
        self.assertIn("function isCurrentCalendarRequest", calendar)

    def test_quick_action_icons_use_stable_symbols_not_emoji_glyphs(self) -> None:
        quick_actions = frontend_source("components/quickActions.ts")

        self.assertIn("const QUICK_ACTION_ICONS", quick_actions)
        self.assertIn('complete: "\\u2713"', quick_actions)
        self.assertNotIn("\\uD83D", quick_actions)
        self.assertNotIn("\\uD83C", quick_actions)
        self.assertNotIn("\\uD83E", quick_actions)

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
