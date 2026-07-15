from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontend_source(relative_path: str) -> str:
    return (ROOT / "frontend" / "src" / relative_path).read_text(encoding="utf-8")


def between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


class AttentionTodayActionRoutingStaticTests(unittest.TestCase):
    def test_today_actions_dispatch_by_kind_and_only_select_plot_in_its_case(self) -> None:
        app = frontend_source("app.ts")
        handler = between(
            app,
            "async function handleAttentionTodayAction",
            "async function handleAttentionTodayViewSection",
        )

        self.assertIn("switch (action.kind)", handler)
        self.assertNotIn("action.target_type", handler)
        self.assertNotIn("item.target_type", handler)
        self.assertEqual(handler.count("appContext.selectPlot("), 1)
        select_plot_case = between(handler, 'case "select_plot":', 'case "focus_plant":')
        self.assertIn("await appContext.selectPlot(action.target_id);", select_plot_case)

    def test_cross_view_today_navigation_closes_today_and_plot_overlays(self) -> None:
        app = frontend_source("app.ts")
        cleanup = between(
            app,
            "function closeAttentionTodayNavigationOverlays",
            "async function openAttentionIssueTarget",
        )
        issue_navigation = between(
            app,
            "async function openAttentionIssueTarget",
            "async function handleAttentionTodayAction",
        )
        handler = between(
            app,
            "async function handleAttentionTodayAction",
            "async function handleAttentionTodayViewSection",
        )
        view_section = between(
            app,
            "async function handleAttentionTodayViewSection",
            "function syncAttentionTodayAvailability",
        )

        self.assertIn("attentionTodayPanel?.closeMobileSheet();", cleanup)
        self.assertIn("closePanel();", cleanup)
        self.assertIn("closeAttentionTodayNavigationOverlays();", issue_navigation)
        self.assertGreaterEqual(handler.count("closeAttentionTodayNavigationOverlays();"), 3)
        self.assertIn("closeAttentionTodayNavigationOverlays();", view_section)

    def test_open_issue_keeps_list_fallback_and_opens_exact_target(self) -> None:
        app = frontend_source("app.ts")
        issue_navigation = between(
            app,
            "async function openAttentionIssueTarget",
            "async function handleAttentionTodayAction",
        )

        self.assertIn('action.target_id || item.target_id || ""', issue_navigation)
        self.assertLess(
            issue_navigation.index('navigateToSubMode("issues");'),
            issue_navigation.index("await loadIssues();"),
        )
        self.assertLess(
            issue_navigation.index("await loadIssues();"),
            issue_navigation.index("await fetchIssueApi(targetIssueId);"),
        )
        self.assertIn("await openIssueForm(issue);", issue_navigation)
        self.assertIn('showToast(getApiErrorMessage(err), "error");', issue_navigation)

    def test_failed_plot_action_is_caught_and_reported_by_the_panel(self) -> None:
        panel = frontend_source("components/attentionTodayPanel.ts")
        run_action = between(panel, "async function runAction", "function createActionButton")

        self.assertIn("await handler(item, action);", run_action)
        self.assertIn("} catch (err) {", run_action)
        self.assertIn("options.onError?.(message);", run_action)
        self.assertIn("} finally {", run_action)
        self.assertIn('button.setAttribute("aria-busy", "false");', run_action)


if __name__ == "__main__":
    unittest.main()
