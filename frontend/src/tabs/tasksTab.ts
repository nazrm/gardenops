import type { AppContext } from "../core/appContext";
import { querySelect } from "../core/dom";
import type { GardenTask } from "../core/models";
import { t } from "../core/i18n";
import {
  type TaskActionRequest,
  batchTaskActionApi,
  fetchTaskApi,
  fetchTasksApi,
  createTaskApi,
  updateTaskApi,
  taskActionApi,
  deleteTaskApi,
  generateTasksApi,
  refreshTaskDescriptionsApi,
  getActiveGardenContext,
  getApiErrorMessage,
  withTaskActionRevision,
} from "../services/api";
import { buildPlantNameMap } from "../core/plantNames";
import { renderTaskList, createTaskForm } from "../components/tasks";
import { confirmDialog, createModal } from "../components/dialogCore";
import { selectPlot } from "../components/plotInteractions";
import {
  formatLocalDate,
  taskSnoozeDateSafety,
  taskSnoozePolicy,
  type TaskSnoozeDateSafety,
} from "../features/taskSnoozePolicy";
import {
  getTaskSnoozeCorrectionNotice,
  openTaskDateDialog,
} from "../features/taskSnoozeFlow";
import {
  canQueueCompletionOffline,
  canQueueDefaultCompletionOffline,
  needsCompletionDialog,
  offlineTaskActionLabels,
  openTaskCompletionDialog,
} from "../features/taskCompletionFlow";
import {
  enqueueTaskActionBatch,
  getTaskActionStates,
  OfflineTaskActionConflictError,
  onConnectivityChange,
  onOfflineQueueChange,
  removeDraft,
  retryDraft,
  type OfflineTaskActionState,
  type TaskActionDraftInput,
  type TaskActionDraftType,
} from "../services/offlineQueue";
import { cacheTaskList, getCachedTaskList } from "../services/taskCache";
import { syncOfflineDraftsNow } from "../features/offlineFeature";

let ctx: AppContext;

let taskItems: GardenTask[] = [];
let tasksTotal = 0;
let tasksOffset = 0;
let tasksView = "today";
let selectedTaskIds = new Set<string>();
let taskOfflineActions = new Map<string, OfflineTaskActionState>();
let tasksDataState: "live" | "cached" | "unavailable" = "unavailable";
let taskOperation: "idle" | "generate" | "regenerate" = "idle";
let tasksRequestGeneration = 0;
let offlineQueueListenerBound = false;
let taskConnectivityListenerBound = false;
const TASKS_PAGE_SIZE = 50;
type TaskActionExtra = Omit<TaskActionRequest, "action" | "expected_updated_at_ms">;

interface TaskActionOptions {
  allowMissingTask?: boolean;
  expectedGardenId?: number | null;
  showSuccessToast?: boolean;
}

interface SnoozeTaskOptions {
  allowMissingTask?: boolean;
}

interface TasksRequestContext {
  gardenId: number;
  generation: number;
}

interface LoadTasksOptions {
  focusTaskId?: string | undefined;
  expectedGardenId?: number | null | undefined;
}

function createTasksRequest(
  expectedGardenId?: number | null,
): TasksRequestContext | null {
  if (
    expectedGardenId !== undefined
    && getActiveGardenContext() !== expectedGardenId
  ) {
    return null;
  }
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return null;
  return {
    gardenId,
    generation: ++tasksRequestGeneration,
  };
}

function isCurrentTasksRequest(request: TasksRequestContext): boolean {
  return (
    request.generation === tasksRequestGeneration
    && request.gardenId === getActiveGardenContext()
  );
}

function isCurrentTask(
  taskId: string,
  gardenId = getActiveGardenContext(),
): boolean {
  return (
    gardenId !== null
    && gardenId === getActiveGardenContext()
    && taskItems.some((task) => task.id === taskId && task.garden_id === gardenId)
  );
}

function isCurrentTaskAction(
  taskId: string,
  gardenId: number | null,
  allowMissingTask = false,
): boolean {
  return (
    gardenId !== null
    && gardenId === getActiveGardenContext()
    && (allowMissingTask || isCurrentTask(taskId, gardenId))
  );
}

function isBatchActionable(task: GardenTask): boolean {
  return (task.status === "pending" || task.status === "snoozed")
    && !taskOfflineActions.has(task.id);
}

function getSelectedVisibleTaskIds(): string[] {
  return taskItems
    .filter((task) => isBatchActionable(task) && selectedTaskIds.has(task.id))
    .map((task) => task.id);
}

function reconcileSelectionWithVisibleTasks(): void {
  const visibleIds = new Set(
    taskItems.filter(isBatchActionable).map((task) => task.id),
  );
  selectedTaskIds = new Set(
    [...selectedTaskIds].filter((taskId) => visibleIds.has(taskId)),
  );
}

function setTaskOperation(next: "idle" | "generate" | "regenerate"): void {
  taskOperation = next;
  syncTaskHeaderButtons();
  renderTaskOperationProgress();
}

function syncTaskHeaderButtons(): void {
  const generateBtn = document.getElementById("tasks-generate-btn");
  if (generateBtn instanceof HTMLButtonElement) {
    generateBtn.disabled = !ctx.canWrite() || !ctx.isOnline() || taskOperation !== "idle";
    generateBtn.setAttribute("aria-busy", taskOperation === "generate" ? "true" : "false");
  }
  const regenerateBtn = document.getElementById("tasks-refresh-desc-btn");
  if (regenerateBtn instanceof HTMLButtonElement) {
    regenerateBtn.disabled = !ctx.canWrite() || !ctx.isOnline() || taskOperation !== "idle";
    regenerateBtn.setAttribute(
      "aria-busy",
      taskOperation === "regenerate" ? "true" : "false",
    );
  }
  const addBtn = document.getElementById("tasks-add-btn");
  if (addBtn instanceof HTMLButtonElement) {
    addBtn.disabled = !ctx.canWrite() || !ctx.isOnline() || taskOperation !== "idle";
  }
}

function renderTaskOperationProgress(): void {
  const container = document.getElementById("tasks-operation-progress");
  const label = document.getElementById("tasks-operation-label");
  const detail = document.getElementById("tasks-operation-detail");
  const bar = document.getElementById("tasks-operation-bar");
  if (
    !(container instanceof HTMLElement) ||
    !(label instanceof HTMLElement) ||
    !(detail instanceof HTMLElement) ||
    !(bar instanceof HTMLProgressElement)
  ) {
    return;
  }
  if (taskOperation === "idle") {
    container.hidden = true;
    label.textContent = t("tasks.progress_generating");
    detail.textContent = t("tasks.progress_generating_detail");
    bar.removeAttribute("value");
    return;
  }
  container.hidden = false;
  bar.removeAttribute("value");
  if (taskOperation === "generate") {
    label.textContent = t("tasks.progress_generating");
    detail.textContent = t("tasks.progress_generating_detail");
    return;
  }
  label.textContent = t("tasks.progress_regenerating");
  detail.textContent = t("tasks.progress_regenerating_detail");
}

export function getTasksView(): string {
  return tasksView;
}

export function setTasksView(view: string): void {
  tasksView = view;
}

export function setTasksOffset(offset: number): void {
  tasksOffset = offset;
}

export function syncTasksViewButtons(): void {
  document
    .querySelectorAll<HTMLButtonElement>("[data-tasks-view]")
    .forEach((btn) => {
      btn.classList.toggle(
        "active",
        btn.dataset["tasksView"] === tasksView,
      );
    });
}

export function resetTasksForGardenSwitch(): void {
  tasksRequestGeneration += 1;
  taskItems = [];
  tasksTotal = 0;
  tasksOffset = 0;
  tasksView = "today";
  selectedTaskIds.clear();
  taskOfflineActions.clear();
  tasksDataState = "unavailable";
  taskOperation = "idle";
  const typeFilter = querySelect("tasks-filter-type");
  if (typeFilter) typeFilter.value = "";
  const statusFilter = querySelect("tasks-filter-status");
  if (statusFilter) statusFilter.value = "";
  syncTasksViewButtons();
  if (ctx) renderTasksView();
}

export function initTasksTab(appCtx: AppContext): void {
  ctx = appCtx;

  if (!offlineQueueListenerBound) {
    offlineQueueListenerBound = true;
    onOfflineQueueChange(() => {
      void refreshTaskOfflineActions(true);
    });
  }
  if (!taskConnectivityListenerBound) {
    taskConnectivityListenerBound = true;
    onConnectivityChange((online) => {
      syncTaskHeaderButtons();
      renderTasksView();
      if (
        online
        && ctx.getActiveTab() === "activity"
        && ctx.getSubMode() === "tasks"
      ) {
        void loadTasks();
      }
    });
  }

  document
    .getElementById("tasks-add-btn")
    ?.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openTaskForm();
    });
  document
    .getElementById("tasks-generate-btn")
    ?.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      void handleGenerateTasks();
    });
  document
    .getElementById("tasks-refresh-desc-btn")
    ?.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      void handleRefreshDescriptions(true);
    });
  document
    .querySelectorAll<HTMLButtonElement>("[data-tasks-view]")
    .forEach((btn) => {
      btn.addEventListener("click", () => {
        tasksView = btn.dataset["tasksView"] || "today";
        document
          .querySelectorAll<HTMLButtonElement>("[data-tasks-view]")
          .forEach((b) => {
            b.classList.toggle("active", b === btn);
          });
        tasksOffset = 0;
        void loadTasks();
      });
    });
  document
    .getElementById("tasks-filter-type")
    ?.addEventListener("change", () => {
      tasksOffset = 0;
      void loadTasks();
    });
  document
    .getElementById("tasks-filter-status")
    ?.addEventListener("change", () => {
      tasksOffset = 0;
      void loadTasks();
    });
  syncTaskHeaderButtons();
  renderTaskOperationProgress();
}

export async function openTaskFromAttention(
  targetTaskId: string,
  expectedGardenId: number | null,
): Promise<void> {
  if (
    !targetTaskId
    || getActiveGardenContext() !== expectedGardenId
  ) {
    return;
  }
  tasksView = "today";
  tasksOffset = 0;
  selectedTaskIds.clear();
  const typeFilter = querySelect("tasks-filter-type");
  if (typeFilter) typeFilter.value = "";
  const statusFilter = querySelect("tasks-filter-status");
  if (statusFilter) statusFilter.value = "";
  syncTasksViewButtons();
  await loadTasks({
    focusTaskId: targetTaskId,
    expectedGardenId,
  });
}

export async function loadTasks(
  options: LoadTasksOptions = {},
): Promise<void> {
  if (!ctx) return;
  const request = createTasksRequest(options.expectedGardenId);
  if (!request) return;
  const params: Record<string, string | number> = {
    limit: TASKS_PAGE_SIZE,
    offset: tasksOffset,
    view: tasksView,
  };
  const typeFilter = querySelect("tasks-filter-type")?.value;
  if (typeFilter) params["task_type"] = typeFilter;
  const statusFilter = querySelect("tasks-filter-status")?.value;
  if (statusFilter) params["status"] = statusFilter;
  if (!ctx.isOnline()) {
    const cached = getCachedTaskList(request.gardenId, params);
    if (cached) {
      taskItems = cached.tasks;
      tasksTotal = cached.total;
      tasksDataState = "cached";
    } else {
      taskItems = [];
      tasksTotal = 0;
      tasksDataState = "unavailable";
    }
    await refreshTaskOfflineActions(false, request.gardenId);
    if (!isCurrentTasksRequest(request)) return;
    reconcileSelectionWithVisibleTasks();
    renderTasksView(options.focusTaskId, request);
    return;
  }
  try {
    const result = await fetchTasksApi(params);
    if (!isCurrentTasksRequest(request)) return;
    cacheTaskList(request.gardenId, params, result);
    if (result.total > 0 && result.tasks.length === 0 && tasksOffset > 0) {
      tasksOffset = Math.max(
        0,
        Math.floor((result.total - 1) / TASKS_PAGE_SIZE) * TASKS_PAGE_SIZE,
      );
      await loadTasks(options);
      return;
    }
    let nextTasks = result.tasks;
    let targetLoadError: unknown | null = null;
    if (
      options.focusTaskId
      && !nextTasks.some((task) => task.id === options.focusTaskId)
    ) {
      try {
        const focusedTask = await fetchTaskApi(options.focusTaskId);
        if (!isCurrentTasksRequest(request)) return;
        nextTasks = [
          focusedTask,
          ...nextTasks.filter((task) => task.id !== focusedTask.id),
        ];
      } catch (err) {
        if (!isCurrentTasksRequest(request)) return;
        targetLoadError = err;
      }
    }
    if (!isCurrentTasksRequest(request)) return;
    taskItems = nextTasks;
    tasksTotal = Math.max(result.total, nextTasks.length);
    tasksDataState = "live";
    await refreshTaskOfflineActions(false, request.gardenId);
    if (!isCurrentTasksRequest(request)) return;
    reconcileSelectionWithVisibleTasks();
    renderTasksView(options.focusTaskId, request);
    if (targetLoadError) {
      ctx.showToast(getApiErrorMessage(targetLoadError), "error");
    }
  } catch (err) {
    if (!isCurrentTasksRequest(request)) return;
    if (!ctx.isOnline()) {
      const cached = getCachedTaskList(request.gardenId, params);
      if (cached) {
        taskItems = cached.tasks;
        tasksTotal = cached.total;
        tasksDataState = "cached";
      } else {
        taskItems = [];
        tasksTotal = 0;
        tasksDataState = "unavailable";
      }
      await refreshTaskOfflineActions(false, request.gardenId);
      if (!isCurrentTasksRequest(request)) return;
      reconcileSelectionWithVisibleTasks();
      renderTasksView(options.focusTaskId, request);
      return;
    }
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function refreshTaskOfflineActions(
  render = false,
  gardenId = getActiveGardenContext(),
): Promise<void> {
  if (gardenId === null) {
    taskOfflineActions.clear();
    if (render) renderTasksView();
    return;
  }
  const states = await getTaskActionStates(gardenId);
  if (gardenId !== getActiveGardenContext()) return;
  taskOfflineActions = states;
  reconcileSelectionWithVisibleTasks();
  if (render) renderTasksView();
}

function offlineTaskActionErrorMessage(error: unknown): string {
  if (error instanceof OfflineTaskActionConflictError) {
    return t(
      error.kind === "duplicate"
        ? "offline.task_duplicate"
        : "offline.task_conflict",
    );
  }
  return getApiErrorMessage(error);
}

async function discardOfflineTaskAction(state: OfflineTaskActionState): Promise<void> {
  const confirmed = await confirmDialog(
    t("offline.discard_confirm"),
    t("offline.discard"),
  );
  if (!confirmed) return;
  await removeDraft(state.draftId);
  await refreshTaskOfflineActions(true);
  void ctx.refreshOfflineIndicator();
}

async function retryOfflineTaskAction(state: OfflineTaskActionState): Promise<void> {
  const changed = await retryDraft(state.draftId);
  if (!changed) return;
  await refreshTaskOfflineActions(true);
  if (ctx.isOnline()) {
    await syncOfflineDraftsNow();
  } else {
    ctx.showToast(t("offline.retry_queued"), "success");
  }
}

function focusTaskCard(
  taskId: string,
  request?: TasksRequestContext,
): void {
  window.requestAnimationFrame(() => {
    if (request && !isCurrentTasksRequest(request)) return;
    const container = document.getElementById("tasks-list");
    if (!(container instanceof HTMLElement)) return;
    const card = Array.from(
      container.querySelectorAll<HTMLElement>("[data-task-id]"),
    ).find((candidate) => candidate.dataset["taskId"] === taskId);
    if (!card) return;
    card.tabIndex = -1;
    card.scrollIntoView({ block: "center" });
    card.focus({ preventScroll: true });
  });
}

function renderTasksView(
  focusTaskId?: string,
  request?: TasksRequestContext,
): void {
  const container = document.getElementById("tasks-list");
  if (!container) return;
  const summary = document.getElementById("tasks-summary");
  if (summary) {
    summary.textContent = tasksDataState === "unavailable"
      ? t("tasks.offline_unavailable")
      : tasksTotal === 0
        ? t("tasks.summary_none")
        : t("tasks.summary_count", { count: tasksTotal });
  }
  syncTaskHeaderButtons();
  renderTaskOperationProgress();
  renderTaskBatchBar();
  const plantNames = buildPlantNameMap(ctx.getPlants());
  const canWrite = ctx.canWrite();
  renderTaskList(container, taskItems, {
    onComplete: (task) => completeTask(task),
    onSnooze: (task) => void openSnoozeDialog(task),
    onSnoozeDate: (task) => openSnoozeDateDialog(task),
    onSkip: (task) => void handleTaskAction(task, "skip"),
    onReschedule: (task) => void openRescheduleDialog(task),
    onEdit: (task) => void openTaskForm(task),
    onDelete: (task) => void deleteTask(task),
    onPlantClick: (pltId) => {
      ctx.focusPlantsInPlantsView([pltId]);
    },
    onPlotClick: (plotId) => {
      ctx.setActiveTab("map");
      void selectPlot(
        ctx.state,
        plotId,
        ctx.getPlotCallbacks(),
      );
    },
    onToggleSelection: canWrite
      ? (task, selected) => {
        if (selected) {
          selectedTaskIds.add(task.id);
        } else {
          selectedTaskIds.delete(task.id);
        }
        renderTasksView();
      }
      : undefined,
    onDiscardOfflineAction: (state) => void discardOfflineTaskAction(state),
    onRetryOfflineAction: (state) => void retryOfflineTaskAction(state),
    offlineTaskActions: taskOfflineActions,
    selectedTaskIds,
    onEmptyAction: canWrite && ctx.isOnline() ? () => void handleGenerateTasks() : undefined,
    canWrite,
    dataState: tasksDataState,
    online: ctx.isOnline(),
  }, plantNames);
  ctx.renderDataExportBars();
  renderTasksPagination();
  if (focusTaskId) focusTaskCard(focusTaskId, request);
}

function renderTasksPagination(): void {
  const container = document.getElementById("tasks-pagination");
  if (!container) return;
  container.replaceChildren();
  if (tasksDataState === "unavailable") return;
  if (tasksTotal <= TASKS_PAGE_SIZE) return;
  const page =
    Math.floor(tasksOffset / TASKS_PAGE_SIZE) + 1;
  const totalPages = Math.ceil(
    tasksTotal / TASKS_PAGE_SIZE,
  );
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = t("common.previous");
  prev.disabled = tasksOffset === 0;
  prev.addEventListener("click", () => {
    tasksOffset = Math.max(
      0,
      tasksOffset - TASKS_PAGE_SIZE,
    );
    void loadTasks();
  });
  const info = document.createElement("span");
  info.textContent = t("common.page_of", {
    page,
    total: totalPages,
  });
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = t("common.next");
  next.disabled =
    tasksOffset + TASKS_PAGE_SIZE >= tasksTotal;
  next.addEventListener("click", () => {
    tasksOffset += TASKS_PAGE_SIZE;
    void loadTasks();
  });
  container.append(prev, info, next);
}

function renderTaskBatchBar(): void {
  const container = document.getElementById("tasks-batch-bar");
  if (!container) return;
  container.replaceChildren();
  if (!ctx.canWrite()) {
    selectedTaskIds.clear();
    container.hidden = true;
    return;
  }
  const actionableVisible = taskItems.filter(isBatchActionable);
  if (actionableVisible.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;

  const selectedCount = getSelectedVisibleTaskIds().length;
  const bar = document.createElement("div");
  bar.className = "task-batch-bar";

  const countLabel = document.createElement("span");
  countLabel.className = "task-batch-count";
  countLabel.textContent = t("tasks.batch_selected", { count: selectedCount });
  bar.appendChild(countLabel);

  const selectVisibleBtn = document.createElement("button");
  selectVisibleBtn.type = "button";
  selectVisibleBtn.className = "task-action-btn";
  selectVisibleBtn.textContent = t("tasks.batch_select_visible");
  selectVisibleBtn.addEventListener("click", () => {
    selectedTaskIds = new Set(actionableVisible.map((task) => task.id));
    renderTasksView();
  });
  bar.appendChild(selectVisibleBtn);

  const clearBtn = document.createElement("button");
  clearBtn.type = "button";
  clearBtn.className = "task-action-btn";
  clearBtn.textContent = t("tasks.batch_clear");
  clearBtn.disabled = selectedCount === 0;
  clearBtn.addEventListener("click", () => {
    selectedTaskIds.clear();
    renderTasksView();
  });
  bar.appendChild(clearBtn);

  if (selectedCount > 0) {
    const completeBtn = document.createElement("button");
    completeBtn.type = "button";
    completeBtn.className = "task-action-btn task-action-complete";
    completeBtn.textContent = t("tasks.action_complete");
    completeBtn.addEventListener("click", () => {
      void handleBatchComplete();
    });
    bar.appendChild(completeBtn);

    const skipBtn = document.createElement("button");
    skipBtn.type = "button";
    skipBtn.className = "task-action-btn";
    skipBtn.textContent = t("tasks.action_skip");
    skipBtn.addEventListener("click", () => {
      void handleBatchTaskAction("skip");
    });
    bar.appendChild(skipBtn);

    const snoozeBtn = document.createElement("button");
    snoozeBtn.type = "button";
    snoozeBtn.className = "task-action-btn";
    snoozeBtn.textContent = t("tasks.action_snooze");
    snoozeBtn.addEventListener("click", () => {
      const selectedTasks = taskItems.filter(
        (task) => isBatchActionable(task) && selectedTaskIds.has(task.id),
      );
      const policy = batchSnoozePolicy(selectedTasks);
      if (policy.blockedMessage) {
        ctx.showToast(policy.blockedMessage, "error");
        return;
      }
      openTaskDateDialog({
        title: t("tasks.snooze_prompt") as string,
        defaultDate: policy.defaultDate,
        onConfirm: (date, confirmOutsideWindow) => void handleBatchTaskAction("snooze", {
          snooze_until: date,
          ...(confirmOutsideWindow ? { confirm_outside_window: true } : {}),
        }),
        warning: policy.warning,
        requireManualDate: policy.requiresManualDate,
        maxDate: policy.maxDate,
        getDateSafety: policy.getDateSafety,
      });
    });
    bar.appendChild(snoozeBtn);

    const rescheduleBtn = document.createElement("button");
    rescheduleBtn.type = "button";
    rescheduleBtn.className = "task-action-btn";
    rescheduleBtn.textContent = t("tasks.action_reschedule");
    rescheduleBtn.addEventListener("click", () => {
      const firstSelectedTask = taskItems.find((task) => selectedTaskIds.has(task.id));
      openTaskDateDialog({
        title: t("tasks.reschedule_prompt") as string,
        defaultDate: firstSelectedTask?.due_on ?? formatLocalDate(new Date()),
        onConfirm: (date) =>
          void handleBatchTaskAction("reschedule", { reschedule_to: date }),
      });
    });
    bar.appendChild(rescheduleBtn);
  }

  container.appendChild(bar);
}

interface BatchSnoozePolicy {
  defaultDate: string;
  warning?: string | undefined;
  requiresManualDate: boolean;
  maxDate?: string | undefined;
  blockedMessage?: string | undefined;
  getDateSafety: (date: string) => TaskSnoozeDateSafety;
}

function batchSnoozeDateSafety(
  selectedTasks: GardenTask[],
  snoozeUntil: string,
): TaskSnoozeDateSafety {
  let confirmation: TaskSnoozeDateSafety | undefined;
  for (const task of selectedTasks) {
    const safety = taskSnoozeDateSafety(task, snoozeUntil);
    if (safety.blocked) return safety;
    if (!confirmation && safety.confirmationRequired) confirmation = safety;
  }
  return confirmation ?? {};
}

function batchSnoozePolicy(selectedTasks: GardenTask[]): BatchSnoozePolicy {
  const policies = selectedTasks.map((task) => taskSnoozePolicy(task));
  const first = policies[0];
  if (!first) {
    return {
      defaultDate: formatLocalDate(new Date()),
      requiresManualDate: false,
      getDateSafety: () => ({}),
    };
  }
  const homogeneous = policies.every((policy) => policy.defaultDate === first.defaultDate);
  const maxDate = policies.reduce<string | undefined>((earliest, policy) => (
    policy.maxDate && (!earliest || policy.maxDate < earliest)
      ? policy.maxDate
      : earliest
  ), undefined);
  return {
    defaultDate: homogeneous ? first.defaultDate : formatLocalDate(new Date()),
    warning: !homogeneous
      ? t("tasks.batch_snooze_mixed_warning") as string
      : policies.find((policy) => policy.manualDateMessage)?.manualDateMessage,
    requiresManualDate: !homogeneous || policies.some((policy) => policy.requireManualDate),
    maxDate,
    blockedMessage: policies.find((policy) => policy.blockedMessage)?.blockedMessage,
    getDateSafety: (date) => batchSnoozeDateSafety(selectedTasks, date),
  };
}

async function enqueueOfflineTaskAction(
  task: GardenTask,
  action: TaskActionRequest["action"],
  extra?: TaskActionExtra,
): Promise<void> {
  await enqueueTaskActionBatch([
    offlineTaskActionInput(action, offlineTaskActionPayload(task, action, extra)),
  ]);
}

function offlineTaskActionPayload(
  task: Pick<GardenTask, "id" | "task_type" | "title" | "updated_at_ms">,
  action: TaskActionRequest["action"],
  extra?: TaskActionExtra,
): Record<string, unknown> {
  const { action: _action, ...payload } = withTaskActionRevision(task, {
    action,
    ...extra,
  });
  return {
    task_id: task.id,
    ...offlineTaskActionLabels(task, action),
    ...payload,
  };
}

function offlineTaskActionInput(
  action: TaskActionRequest["action"],
  payload: Record<string, unknown>,
): TaskActionDraftInput {
  const typeByAction: Record<TaskActionRequest["action"], TaskActionDraftType> = {
    complete: "task_complete",
    skip: "task_skip",
    snooze: "task_snooze",
    reschedule: "task_reschedule",
  };
  return { type: typeByAction[action], payload };
}

async function handleTaskAction(
  task: GardenTask,
  action: TaskActionRequest["action"],
  extra?: TaskActionExtra,
  options: TaskActionOptions = {},
): Promise<boolean> {
  const taskId = task.id;
  const requestGardenId = options.expectedGardenId ?? task.garden_id;
  const actionIsCurrent = (): boolean => isCurrentTaskAction(
    taskId,
    requestGardenId,
    options.allowMissingTask,
  );
  if (!actionIsCurrent()) return false;
  if (!ctx.ensureWriteAccess()) return false;
  if (!ctx.isOnline()) {
    try {
      await enqueueOfflineTaskAction(task, action, extra);
    } catch (err) {
      ctx.showToast(offlineTaskActionErrorMessage(err), "error");
      return false;
    }
    if (!actionIsCurrent()) return false;
    await refreshTaskOfflineActions(true, requestGardenId);
    if (options.showSuccessToast !== false) {
      ctx.showToast(t("offline.draft_saved"), "success");
    }
    void ctx.refreshOfflineIndicator();
    return true;
  }
  try {
    await taskActionApi(taskId, withTaskActionRevision(task, { action, ...extra }));
    if (!actionIsCurrent()) return false;
    if (options.showSuccessToast !== false) {
      ctx.showToast(
        t("tasks.action_success", { action }),
        "success",
      );
    }
    void ctx.refreshBadgeCounts();
    void loadTasks();
    return true;
  } catch (err) {
    if (!actionIsCurrent()) return false;
    ctx.showToast(getApiErrorMessage(err), "error");
    return false;
  }
}

async function handleBatchTaskAction(
  action: TaskActionRequest["action"],
  extra?: TaskActionExtra,
  taskIdOverride?: string[],
): Promise<void> {
  const requestGardenId = getActiveGardenContext();
  if (!ctx.ensureWriteAccess()) return;
  const taskIds = taskIdOverride ?? getSelectedVisibleTaskIds();
  if (taskIds.length === 0) {
    ctx.showToast(t("tasks.batch_none_selected"), "error");
    return;
  }
  if (!taskIds.every((taskId) => isCurrentTask(taskId, requestGardenId))) return;
  if (!ctx.isOnline()) {
    try {
      await enqueueTaskActionBatch(taskIds.map((taskId) => {
        const task = taskItems.find((candidate) => candidate.id === taskId);
        if (!task) throw new Error("Task is no longer available");
        return offlineTaskActionInput(
          action,
          offlineTaskActionPayload(task, action, extra),
        );
      }));
    } catch (err) {
      ctx.showToast(offlineTaskActionErrorMessage(err), "error");
      return;
    }
    if (!taskIds.every((taskId) => isCurrentTask(taskId, requestGardenId))) return;
    selectedTaskIds.clear();
    await refreshTaskOfflineActions(true, requestGardenId);
    ctx.showToast(
      t("tasks.batch_queued", { count: taskIds.length }),
      "success",
    );
    void ctx.refreshOfflineIndicator();
    return;
  }
  try {
    const result = await batchTaskActionApi(taskIds, { action, ...extra });
    if (!taskIds.every((taskId) => isCurrentTask(taskId, requestGardenId))) return;
    selectedTaskIds.clear();
    ctx.showToast(
      t("tasks.batch_result", { count: result.updated }),
      "success",
    );
    void ctx.refreshBadgeCounts();
    void loadTasks();
  } catch (err) {
    if (!taskIds.every((taskId) => isCurrentTask(taskId, requestGardenId))) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function completeTask(task: GardenTask): void {
  if (!needsCompletionDialog(task)) {
    void handleTaskAction(task, "complete");
    return;
  }
  if (!ctx.isOnline()) {
    const needsExplicitOutcome = !canQueueDefaultCompletionOffline(task);
    if (needsExplicitOutcome && !canQueueCompletionOffline(task)) {
      ctx.showToast(t("tasks.complete_grouped_one_by_one"), "error");
      return;
    }
  }
  openTaskCompletionDialog(
    task,
    buildPlantNameMap(ctx.getPlants()),
    (body) => {
      const { action: _action, ...extra } = body;
      void handleTaskAction(task, "complete", extra);
    },
  );
}

async function handleBatchComplete(): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const selectedTasks = taskItems.filter(
    (task) => isBatchActionable(task) && selectedTaskIds.has(task.id),
  );
  if (selectedTasks.length === 0) {
    ctx.showToast(t("tasks.batch_none_selected"), "error");
    return;
  }
  const directTasks = selectedTasks.filter((task) => !needsCompletionDialog(task));
  const detailTasks = selectedTasks.filter(needsCompletionDialog);
  if (detailTasks.length > 0) {
    ctx.showToast(t("tasks.complete_grouped_one_by_one"), "error");
  }
  if (directTasks.length === 0) return;
  await handleBatchTaskAction(
    "complete",
    undefined,
    directTasks.map((task) => task.id),
  );
  for (const task of detailTasks) {
    selectedTaskIds.add(task.id);
  }
  renderTasksView();
}

async function snoozeTaskWithPolicy(
  task: GardenTask,
  snoozeUntil: string,
  options: SnoozeTaskOptions = {},
  confirmOutsideWindow = false,
): Promise<void> {
  const safety = taskSnoozeDateSafety(task, snoozeUntil);
  if (safety.blocked) {
    ctx.showToast(safety.message ?? t("tasks.snooze_prompt"), "error");
    return;
  }
  const online = ctx.isOnline();
  const ok = await handleTaskAction(
    task,
    "snooze",
    {
      snooze_until: snoozeUntil,
      ...(safety.confirmationRequired && confirmOutsideWindow
        ? { confirm_outside_window: true }
        : {}),
    },
    {
      ...(options.allowMissingTask ? { allowMissingTask: true } : {}),
      expectedGardenId: task.garden_id,
      showSuccessToast: false,
    },
  );
  if (!ok) return;
  if (!online) {
    ctx.showToast(t("offline.draft_saved"), "success");
  }
  const notice = getTaskSnoozeCorrectionNotice(snoozeUntil, () => {
    openSnoozeDateDialog(task, snoozeUntil, { allowMissingTask: true });
  });
  ctx.showToast(
    notice.message,
    "success",
    {
      actions: [
        {
          label: notice.actionLabel,
          onClick: notice.onChangeDate,
        },
      ],
      durationMs: notice.durationMs,
    },
  );
}

function openSnoozeDateDialog(
  task: GardenTask,
  defaultDate = taskSnoozePolicy(task).defaultDate,
  options: SnoozeTaskOptions = {},
): void {
  const policy = taskSnoozePolicy(task);
  if (policy.blockedMessage) {
    ctx.showToast(policy.blockedMessage, "error");
    return;
  }
  openTaskDateDialog({
    title: t("tasks.snooze_prompt") as string,
    defaultDate,
    onConfirm: (date, confirmOutsideWindow) =>
      void snoozeTaskWithPolicy(task, date, options, confirmOutsideWindow),
    warning: policy.manualDateMessage,
    requireManualDate: policy.requireManualDate,
    maxDate: policy.maxDate,
    getDateSafety: (date) => taskSnoozeDateSafety(task, date),
  });
}

function openSnoozeDialog(task: GardenTask): void {
  const policy = taskSnoozePolicy(task);
  if (policy.blockedMessage) {
    ctx.showToast(policy.blockedMessage, "error");
    return;
  }
  if (policy.immediate) {
    void snoozeTaskWithPolicy(task, policy.defaultDate);
    return;
  }
  openSnoozeDateDialog(task, policy.defaultDate);
}

function openRescheduleDialog(task: GardenTask): void {
  openTaskDateDialog({
    title: t("tasks.reschedule_prompt") as string,
    defaultDate: task.due_on,
    onConfirm: (date) =>
      void handleTaskAction(task, "reschedule", {
        reschedule_to: date,
      }),
  });
}

export function openTaskForm(
  existingTask?: GardenTask,
): void {
  const readOnly = Boolean(existingTask) && !ctx.canWrite();
  if (!existingTask && !ctx.ensureWriteAccess()) return;
  const { dialog, close } = createModal(
    t("tasks.form_title"),
    '<div class="modal-content task-form-dialog"></div>',
  );
  const form = createTaskForm({
    task: existingTask,
    readOnly,
    onSave: async (data) => {
      try {
        if (existingTask) {
          await updateTaskApi(existingTask.id, data);
        } else {
          await createTaskApi(
            data as Parameters<typeof createTaskApi>[0],
          );
        }
        ctx.showToast(
          t(
            existingTask
              ? "tasks.updated"
              : "tasks.created",
          ),
          "success",
        );
        if (!existingTask) {
          tasksOffset = 0;
        }
        close();
        void loadTasks();
      } catch (err) {
        ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: close,
  });
  dialog.querySelector(".task-form-dialog")?.appendChild(form);
  form.querySelector<HTMLElement>("input:not([disabled]), select:not([disabled])")?.focus();
}

async function deleteTask(task: GardenTask): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const ok = await confirmDialog(
    t("tasks.confirm_delete"),
    t("common.delete"),
  );
  if (!ok) return;
  try {
    await deleteTaskApi(task.id);
    ctx.showToast(t("tasks.deleted"), "success");
    void loadTasks();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function handleRefreshDescriptions(forceAll = false): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  setTaskOperation("regenerate");
  try {
    const result = await refreshTaskDescriptionsApi(forceAll);
    ctx.showToast(
      t("tasks.refresh_result", {
        count: result.updated,
      }),
      "success",
    );
    void loadTasks();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  } finally {
    setTaskOperation("idle");
  }
}

async function handleGenerateTasks(): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  setTaskOperation("generate");
  try {
    const result = await generateTasksApi();
    ctx.showToast(
      t("tasks.generate_result", {
        created: result.created,
        skipped: result.skipped,
      }),
      "success",
    );
    void loadTasks();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  } finally {
    setTaskOperation("idle");
  }
}
