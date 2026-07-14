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
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(encoding="utf-8")

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

    def test_pending_snooze_date_correction_atomically_replaces_one_draft(self) -> None:
        queue = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )

        correction = queue.split("function isPendingTaskSnoozeCorrection", 1)[1].split(
            "function createDraft", 1
        )[0]
        batch = queue.split("export async function enqueueTaskActionBatch", 1)[1].split(
            "export async function enqueueDraft", 1
        )[0]
        self.assertIn('existing.status === "pending"', correction)
        self.assertEqual(correction.count('=== "task_snooze"'), 2)
        self.assertIn('existing.payload["snooze_until"]', correction)
        self.assertIn('requested.payload["snooze_until"]', correction)
        self.assertIn("id: existing.id", correction)
        self.assertIn(
            'expected_updated_at_ms: existing.payload["expected_updated_at_ms"]',
            correction,
        )
        self.assertIn("snoozeCorrections.set(", batch)
        self.assertIn("correction ? store.put(correction) : store.add(draft)", batch)
        self.assertIn("const syncingDraft = await markSyncing(draft.id);", queue)
        self.assertIn("await handler(syncingDraft.payload, syncingDraft);", queue)

    def test_task_action_replay_preserves_the_task_revision(self) -> None:
        api = (ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(encoding="utf-8")
        replay = (ROOT / "frontend" / "src" / "features" / "offlineFeature.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("expected_updated_at_ms?: number;", api)
        self.assertIn("export type RevisionedTaskActionRequest", api)
        self.assertIn("export function withTaskActionRevision", api)
        self.assertIn("expected_updated_at_ms: task.updated_at_ms", api)
        self.assertIn('Omit<TaskActionRequest, "action" | "expected_updated_at_ms">', tasks)
        self.assertIn("const result = await taskActionApi(", tasks)
        self.assertIn("withTaskActionRevision(task, { action, ...extra })", tasks)
        self.assertIn("offlineTaskActionPayload(task, action, extra)", tasks)
        self.assertIn('const expectedUpdatedAtMs = payload["expected_updated_at_ms"];', replay)
        self.assertIn("expected_updated_at_ms: expectedUpdatedAtMs", replay)
        self.assertIn("Offline task action is missing its expected revision", replay)

    def test_online_batch_actions_send_each_selected_task_revision(self) -> None:
        api = (ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(encoding="utf-8")

        self.assertIn("export type BatchTaskActionRequest", api)
        self.assertIn("expected_updated_at_ms_by_task_id: Record<string, number>", api)
        self.assertIn("export function withBatchTaskActionRevisions", api)
        self.assertIn("withBatchTaskActionRevisions(batchTasks, { action, ...extra })", tasks)

    def test_online_task_actions_refresh_revision_for_immediate_corrections(self) -> None:
        expected_refreshes = {
            "tabs/tasksTab.ts": "task.updated_at_ms = result.updated_at_ms;",
            "tabs/calendarTab.ts": ("target.taskRevision.updated_at_ms = result.updated_at_ms;"),
            "components/plotInteractions.ts": ("task.updated_at_ms = result.updated_at_ms;"),
            "features/quickActionsFeature.ts": ("task.updated_at_ms = result.updated_at_ms;"),
        }

        for relative_path, refresh in expected_refreshes.items():
            source = (ROOT / "frontend" / "src" / relative_path).read_text(encoding="utf-8")
            self.assertIn(refresh, source)

    def test_every_task_action_surface_stamps_the_current_task_revision(self) -> None:
        expected_stamps = {
            "tabs/tasksTab.ts": "withTaskActionRevision(task, { action, ...extra })",
            "tabs/calendarTab.ts": "withTaskActionRevision(target.taskRevision, body)",
            "features/quickActionsFeature.ts": (
                "const actionBody = withTaskActionRevision(task, body);"
            ),
            "components/plotInteractions.ts": (
                "const actionBody = withTaskActionRevision(task, body);"
            ),
        }

        for relative_path, stamp in expected_stamps.items():
            source = (ROOT / "frontend" / "src" / relative_path).read_text(encoding="utf-8")
            self.assertIn(stamp, source)

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
        styles = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

        self.assertIn('status: "queued" | "syncing" | "failed"', queue)
        self.assertIn('draft.status === "syncing"', queue)
        self.assertIn("syncingCount:", queue)
        self.assertIn("export async function retryDraft", queue)
        self.assertIn('draft.status = "pending";', queue)
        self.assertIn("onRetryOfflineAction", task_cards)
        self.assertIn("onDiscardOfflineAction", task_cards)
        self.assertIn('role", offlineAction.status === "failed" ? "alert" : "status"', task_cards)
        self.assertIn('failures.setAttribute("role", "alert")', indicator)
        self.assertIn("callbacks.onRetry(draft)", indicator)
        self.assertIn("callbacks.onDiscard(draft)", indicator)
        self.assertIn('t("offline.failed_task_action"', indicator)
        self.assertIn('payload["task_label"]', indicator)
        self.assertIn('payload["action_label"]', indicator)
        mobile_indicator = styles.rsplit("@media (max-width: 960px) {", 1)[1]
        self.assertIn(".offline-indicator-wrapper", mobile_indicator)
        self.assertIn("top: auto;", mobile_indicator)
        self.assertIn(
            "bottom: calc(78px + env(safe-area-inset-bottom, 0px) + var(--sp-2));",
            mobile_indicator,
        )

    def test_online_startup_and_reopen_replay_pending_work_once_per_active_sync(self) -> None:
        feature = (ROOT / "frontend" / "src" / "features" / "offlineFeature.ts").read_text(
            encoding="utf-8"
        )
        queue = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("let syncInFlight: Promise<void> | null = null;", feature)
        self.assertIn("void syncPendingOfflineDrafts();", feature)
        self.assertIn('window.addEventListener("focus"', feature)
        self.assertIn('document.addEventListener("visibilitychange"', feature)
        self.assertIn('document.visibilityState === "visible"', feature)
        self.assertIn("if (snapshot.pendingCount > 0)", feature)
        self.assertIn("let activeSync: Promise<SyncResult> | null = null;", queue)
        self.assertIn("await markSyncing(draft.id)", queue)
        self.assertIn('IDBKeyRange.only("syncing")', queue)
        self.assertIn('status: "pending"', queue)

    def test_online_transition_reloads_the_active_initialized_task_view(self) -> None:
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(encoding="utf-8")
        calendar = (ROOT / "frontend" / "src" / "tabs" / "calendarTab.ts").read_text(
            encoding="utf-8"
        )

        tasks_connectivity = tasks.split("onConnectivityChange((online) =>", 1)[1].split("});", 1)[
            0
        ]
        calendar_connectivity = calendar.split("onConnectivityChange((online) =>", 1)[1].split(
            "});", 1
        )[0]
        self.assertIn('ctx.getActiveTab() === "activity"', tasks_connectivity)
        self.assertIn('ctx.getSubMode() === "tasks"', tasks_connectivity)
        self.assertIn("void loadTasks();", tasks_connectivity)
        self.assertIn('ctx.getActiveTab() === "activity"', calendar_connectivity)
        self.assertIn('ctx.getSubMode() === "calendar"', calendar_connectivity)
        self.assertIn("void loadCalendar();", calendar_connectivity)

    def test_transient_retries_are_bounded_and_failures_keep_human_labels(self) -> None:
        queue = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )
        toast = (ROOT / "frontend" / "src" / "components" / "toast.ts").read_text(encoding="utf-8")
        styles = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

        self.assertIn("const MAX_TRANSIENT_ATTEMPTS_PER_SYNC = 2", queue)
        self.assertIn("function isTransientSyncError", queue)
        self.assertIn("for (let attempt = 1; attempt <= MAX_TRANSIENT_ATTEMPTS_PER_SYNC", queue)
        self.assertIn("await waitForTransientRetry(attempt)", queue)
        self.assertIn("task_label", queue)
        self.assertIn("action_label", queue)
        self.assertIn("const MAX_VISIBLE_TOASTS = 3", toast)
        self.assertIn("const activeToasts = new Map", toast)
        self.assertIn("while (visibleToasts.length >= MAX_VISIBLE_TOASTS)", toast)
        self.assertIn("removeToast(oldest, true)", toast)
        self.assertIn("body.offline-recovery-open #toast-container", styles)
        self.assertIn("--offline-recovery-offset", styles)

    def test_cold_offline_views_are_honest_and_warm_filters_use_matching_cache(self) -> None:
        app = (ROOT / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")
        tasks = (ROOT / "frontend" / "src" / "tabs" / "tasksTab.ts").read_text(encoding="utf-8")
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
        self.assertIn('entry.params["task_type"] || entry.params["status"]', task_cache)
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
        self.assertIn("if (tasksTabModule)", navigation[task_guard:calendar_guard])
        self.assertIn("renderColdOfflineTasksShell();", navigation[task_guard:calendar_guard])
        self.assertIn("if (calendarTabModule)", navigation[calendar_guard:plant_load])
        self.assertIn("renderColdOfflineCalendarShell();", navigation[calendar_guard:plant_load])
        self.assertIn("function renderColdOfflineTasksShell(): void", app)
        self.assertIn("function renderColdOfflineCalendarShell(): void", app)
        self.assertIn("container.replaceChildren(unavailable);", app)
        self.assertIn("root.hidden = true;", app)


if __name__ == "__main__":
    unittest.main()
