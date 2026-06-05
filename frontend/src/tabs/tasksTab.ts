import type { AppContext } from "../core/appContext";
import { querySelect } from "../core/dom";
import type { GardenTask } from "../core/models";
import { t } from "../core/i18n";
import {
  type TaskActionRequest,
  batchTaskActionApi,
  fetchTasksApi,
  createTaskApi,
  updateTaskApi,
  taskActionApi,
  deleteTaskApi,
  generateTasksApi,
  refreshTaskDescriptionsApi,
  getApiErrorMessage,
} from "../services/api";
import { buildPlantNameMap } from "../core/plantNames";
import { renderTaskList, createTaskForm } from "../components/tasks";
import { confirmDialog, createModal } from "../components/dialogCore";
import { selectPlot } from "../components/plotInteractions";

let ctx: AppContext;

let taskItems: GardenTask[] = [];
let tasksTotal = 0;
let tasksOffset = 0;
let tasksView = "today";
let selectedTaskIds = new Set<string>();
let taskOperation: "idle" | "generate" | "regenerate" = "idle";
const TASKS_PAGE_SIZE = 50;

function isBatchActionable(task: GardenTask): boolean {
  return task.status === "pending" || task.status === "snoozed";
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
    generateBtn.disabled = !ctx.canWrite() || taskOperation !== "idle";
    generateBtn.setAttribute("aria-busy", taskOperation === "generate" ? "true" : "false");
  }
  const regenerateBtn = document.getElementById("tasks-refresh-desc-btn");
  if (regenerateBtn instanceof HTMLButtonElement) {
    regenerateBtn.disabled = !ctx.canWrite() || taskOperation !== "idle";
    regenerateBtn.setAttribute(
      "aria-busy",
      taskOperation === "regenerate" ? "true" : "false",
    );
  }
  const addBtn = document.getElementById("tasks-add-btn");
  if (addBtn instanceof HTMLButtonElement) {
    addBtn.disabled = !ctx.canWrite() || taskOperation !== "idle";
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

export function initTasksTab(appCtx: AppContext): void {
  ctx = appCtx;

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

export async function loadTasks(): Promise<void> {
  if (!ctx) return;
  try {
    const params: Record<string, string | number> = {
      limit: TASKS_PAGE_SIZE,
      offset: tasksOffset,
      view: tasksView,
    };
    const typeFilter = querySelect("tasks-filter-type")?.value;
    if (typeFilter) params["task_type"] = typeFilter;
    const statusFilter = querySelect("tasks-filter-status")?.value;
    if (statusFilter) params["status"] = statusFilter;
    const result = await fetchTasksApi(params);
    if (result.total > 0 && result.tasks.length === 0 && tasksOffset > 0) {
      tasksOffset = Math.max(
        0,
        Math.floor((result.total - 1) / TASKS_PAGE_SIZE) * TASKS_PAGE_SIZE,
      );
      await loadTasks();
      return;
    }
    taskItems = result.tasks;
    tasksTotal = result.total;
    reconcileSelectionWithVisibleTasks();
    renderTasksView();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function renderTasksView(): void {
  const container = document.getElementById("tasks-list");
  if (!container) return;
  const summary = document.getElementById("tasks-summary");
  if (summary) {
    summary.textContent =
      tasksTotal === 0
        ? t("tasks.summary_none")
        : t("tasks.summary_count", { count: tasksTotal });
  }
  syncTaskHeaderButtons();
  renderTaskOperationProgress();
  renderTaskBatchBar();
  const plantNames = buildPlantNameMap(ctx.getPlants());
  const canWrite = ctx.canWrite();
  renderTaskList(container, taskItems, {
    onComplete: (task) =>
      void handleTaskAction(task.id, "complete"),
    onSnooze: (task) => void openSnoozeDialog(task),
    onSkip: (task) => void handleTaskAction(task.id, "skip"),
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
    selectedTaskIds,
    onEmptyAction: canWrite ? () => void handleGenerateTasks() : undefined,
    canWrite,
  }, plantNames);
  ctx.renderDataExportBars();
  renderTasksPagination();
}

function renderTasksPagination(): void {
  const container = document.getElementById("tasks-pagination");
  if (!container) return;
  container.replaceChildren();
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
      void handleBatchTaskAction("complete");
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
      openDateDialog(
        t("tasks.snooze_prompt") as string,
        new Date(Date.now() + 86_400_000).toISOString().slice(0, 10),
        (date) => void handleBatchTaskAction("snooze", { snooze_until: date }),
      );
    });
    bar.appendChild(snoozeBtn);

    const rescheduleBtn = document.createElement("button");
    rescheduleBtn.type = "button";
    rescheduleBtn.className = "task-action-btn";
    rescheduleBtn.textContent = t("tasks.action_reschedule");
    rescheduleBtn.addEventListener("click", () => {
      const firstSelected = taskItems.find((task) => selectedTaskIds.has(task.id));
      openDateDialog(
        t("tasks.reschedule_prompt") as string,
        firstSelected?.due_on ?? new Date().toISOString().slice(0, 10),
        (date) => void handleBatchTaskAction("reschedule", { reschedule_to: date }),
      );
    });
    bar.appendChild(rescheduleBtn);
  }

  container.appendChild(bar);
}

async function enqueueOfflineTaskAction(
  taskId: string,
  action: TaskActionRequest["action"],
  extra?: Record<string, string>,
): Promise<void> {
  const payload: Record<string, unknown> = {
    task_id: taskId,
    ...extra,
  };
  if (action === "complete") {
    await ctx.enqueueDraft("task_complete", payload);
    return;
  }
  if (action === "skip") {
    await ctx.enqueueDraft("task_skip", payload);
    return;
  }
  if (action === "snooze") {
    await ctx.enqueueDraft("task_snooze", payload);
    return;
  }
  if (action === "reschedule") {
    await ctx.enqueueDraft("task_reschedule", payload);
    return;
  }
  throw new Error(`Unsupported task action: ${action}`);
}

async function handleTaskAction(
  taskId: string,
  action: TaskActionRequest["action"],
  extra?: Record<string, string>,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  if (!ctx.isOnline()) {
    await enqueueOfflineTaskAction(taskId, action, extra);
    ctx.showToast(t("offline.draft_saved"), "success");
    void ctx.refreshOfflineIndicator();
    return;
  }
  try {
    await taskActionApi(taskId, { action, ...extra });
    ctx.showToast(
      t("tasks.action_success", { action }),
      "success",
    );
    void ctx.refreshBadgeCounts();
    void loadTasks();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function handleBatchTaskAction(
  action: TaskActionRequest["action"],
  extra?: Record<string, string>,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const taskIds = getSelectedVisibleTaskIds();
  if (taskIds.length === 0) {
    ctx.showToast(t("tasks.batch_none_selected"), "error");
    return;
  }
  if (!ctx.isOnline()) {
    for (const taskId of taskIds) {
      await enqueueOfflineTaskAction(taskId, action, extra);
    }
    selectedTaskIds.clear();
    renderTasksView();
    ctx.showToast(
      t("tasks.batch_result", { count: taskIds.length }),
      "success",
    );
    void ctx.refreshOfflineIndicator();
    return;
  }
  try {
    const result = await batchTaskActionApi(taskIds, { action, ...extra });
    selectedTaskIds.clear();
    ctx.showToast(
      t("tasks.batch_result", { count: result.updated }),
      "success",
    );
    void ctx.refreshBadgeCounts();
    void loadTasks();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function openDateDialog(
  title: string,
  defaultDate: string,
  onConfirm: (date: string) => void,
): void {
  const { dialog, close } = createModal(title, `
    <div class="modal-content confirm-dialog">
      <h3></h3>
      <input type="date" class="prompt-dialog-input" />
      <div class="button-row">
        <button type="button" class="confirm-yes"></button>
        <button type="button" class="confirm-no"></button>
      </div>
    </div>
  `);
  const heading = dialog.querySelector("h3")!;
  heading.textContent = title;
  const input = dialog.querySelector<HTMLInputElement>("input[type='date']")!;
  input.value = defaultDate;
  input.min = new Date().toISOString().slice(0, 10);
  const cancelBtn = dialog.querySelector<HTMLButtonElement>(".confirm-no")!;
  cancelBtn.textContent = t("common.cancel") as string;
  cancelBtn.addEventListener("click", close);
  const okBtn = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  okBtn.textContent = t("common.save") as string;
  okBtn.addEventListener("click", () => {
    if (input.value) {
      onConfirm(input.value);
      close();
    }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value) {
      onConfirm(input.value);
      close();
    }
  });
  input.focus();
}

function openSnoozeDialog(task: GardenTask): void {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  openDateDialog(
    t("tasks.snooze_prompt") as string,
    tomorrow.toISOString().slice(0, 10),
    (date) =>
      void handleTaskAction(task.id, "snooze", {
        snooze_until: date,
      }),
  );
}

function openRescheduleDialog(task: GardenTask): void {
  openDateDialog(
    t("tasks.reschedule_prompt") as string,
    task.due_on,
    (date) =>
      void handleTaskAction(task.id, "reschedule", {
        reschedule_to: date,
      }),
  );
}

export function openTaskForm(
  existingTask?: GardenTask,
): void {
  const readOnly = Boolean(existingTask) && !ctx.canWrite();
  if (!existingTask && !ctx.ensureWriteAccess()) return;
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
        overlay.remove();
        void loadTasks();
      } catch (err) {
        ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: () => overlay.remove(),
  });
  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  const dialog = document.createElement("div");
  dialog.className = "modal-content";
  dialog.appendChild(form);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
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
