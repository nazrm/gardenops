from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OfflineReplayFrontendStaticTests(unittest.TestCase):
    def test_queue_persists_a_distinct_attachment_operation_id(self) -> None:
        source = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("const DB_VERSION = 4", source)
        self.assertIn("operation_id: generateOperationId()", source)
        self.assertIn("backfillDraftOperationIds", source)
        self.assertIn('payload["_serialized_media"]', source)

    def test_replay_wires_draft_and_attachment_ids_to_transport(self) -> None:
        feature = (ROOT / "frontend" / "src" / "features" / "offlineFeature.ts").read_text(
            encoding="utf-8"
        )
        app = (ROOT / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")
        api = (ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")

        self.assertGreaterEqual(feature.count("operationId: draft.operation_id"), 7)
        self.assertIn("uploadOfflineAttachments", feature)
        self.assertIn("operationId: operationIds[index]!", feature)
        self.assertEqual(feature.count("operationIds: attachmentOperationIds"), 2)
        self.assertIn("uploadOptions.operationId = options.operationIds[i]!", app)
        self.assertIn("OFFLINE_OPERATION_ID_HEADER", api)
        self.assertIn("mergedHeaders.set(OFFLINE_OPERATION_ID_HEADER, options.operationId)", api)

    def test_migration_keeps_only_the_garden_cascade(self) -> None:
        migration = (ROOT / "migrations" / "0022_offline_operation_idempotency.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("FOREIGN KEY (garden_id)", migration)
        self.assertNotIn("FOREIGN KEY (journal_entry_id", migration)
        self.assertNotIn("FOREIGN KEY (issue_id", migration)
        self.assertNotIn("FOREIGN KEY (harvest_entry_id", migration)

    def test_task_action_batches_are_atomic_and_reject_unresolved_conflicts(self) -> None:
        queue = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(
            encoding="utf-8"
        )

        batch = queue.split("export async function enqueueTaskActionBatch", 1)[1].split(
            "export async function enqueueDraft", 1
        )[0]
        self.assertIn('db!.transaction(STORE_NAME, "readwrite")', batch)
        self.assertIn("const existingRequest = store.getAll();", batch)
        self.assertIn("transaction.abort();", batch)
        self.assertIn('draft.status === "failed"', batch)
        self.assertIn("transaction.oncomplete", batch)
        self.assertIn("emitQueueChanged();", batch)
        self.assertIn("await enqueueTaskActionBatch(taskIds.map", tasks)
        self.assertIn('t("tasks.batch_queued", { count: taskIds.length })', tasks)

    def test_terminal_task_failures_are_visible_and_recoverable(self) -> None:
        queue = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )
        task_cards = (ROOT / "frontend" / "src" / "components" / "tasks.ts").read_text(
            encoding="utf-8"
        )
        indicator = (ROOT / "frontend" / "src" / "components" / "offlineIndicator.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn('draft.status === "failed" ? "failed" : "queued"', queue)
        self.assertIn("export async function retryDraft", queue)
        self.assertIn('draft.status = "pending";', queue)
        self.assertIn("onRetryOfflineAction", task_cards)
        self.assertIn("onDiscardOfflineAction", task_cards)
        self.assertIn('role", offlineAction.status === "failed" ? "alert" : "status"', task_cards)
        self.assertIn('failures.setAttribute("role", "alert")', indicator)
        self.assertIn("callbacks.onRetry(draft)", indicator)
        self.assertIn("callbacks.onDiscard(draft)", indicator)

    def test_cold_offline_views_are_honest_and_warm_filters_use_matching_cache(self) -> None:
        app = (ROOT / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(
            encoding="utf-8"
        )
        task_cache = (ROOT / "frontend" / "src" / "services" / "taskCache.ts").read_text(
            encoding="utf-8"
        )
        calendar = (ROOT / "frontend" / "src" / "tabs" / "calendarTab.ts").read_text(
            encoding="utf-8"
        )
        quick_actions = (
            ROOT / "frontend" / "src" / "features" / "quickActionsFeature.ts"
        ).read_text(encoding="utf-8")

        tasks_guard = tasks.index(
            "if (!ctx.isOnline()) {", tasks.index("export async function loadTasks")
        )
        tasks_fetch = tasks.index("await fetchTasksApi(params)", tasks_guard)
        self.assertLess(tasks_guard, tasks_fetch)
        self.assertIn(
            "renderTasksView(options.focusTaskId, request);",
            tasks[tasks_guard:tasks_fetch],
        )
        self.assertIn('tasksDataState = "unavailable";', tasks[tasks_guard:tasks_fetch])
        fetch_error = tasks.split("} catch (err) {", 1)[1].split(
            "async function refreshTaskOfflineActions", 1
        )[0]
        self.assertIn("if (!ctx.isOnline()) {", fetch_error)
        self.assertIn("getCachedTaskList(request.gardenId, params)", fetch_error)
        self.assertIn("renderTasksView(options.focusTaskId, request);", fetch_error)
        self.assertIn("normalizedParams", task_cache)
        self.assertIn("filterCompleteBaseSnapshot", task_cache)
        self.assertIn("entry.params[\"task_type\"] || entry.params[\"status\"]", task_cache)
        self.assertIn("calendarPreferencesCache.get(gardenId)", calendar)
        self.assertIn("calendarEventsCache.get(", calendar)
        self.assertIn('setCalendarDataState("unavailable")', calendar)
        self.assertIn("getCachedTodayTasks(gardenId)", quick_actions)
        self.assertIn(': { dataState: "unavailable", tasks: [] };', quick_actions)

        navigation = app.split("async function refreshActiveNavigationContent", 1)[1].split(
            "function scheduleActiveNavigationContentLoad", 1
        )[0]
        task_guard = navigation.index('if (!isOnline() && subMode === "tasks")')
        calendar_guard = navigation.index('if (!isOnline() && subMode === "calendar")')
        plant_load = navigation.index("await ensurePlantsCacheLoaded();")
        self.assertLess(task_guard, plant_load)
        self.assertLess(calendar_guard, plant_load)
        self.assertIn("await loadTasksTab();", navigation[task_guard:calendar_guard])
        self.assertIn("await loadCalendar();", navigation[calendar_guard:plant_load])


if __name__ == "__main__":
    unittest.main()
