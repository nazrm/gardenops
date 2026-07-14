import type { GardenTask, TaskType } from "../core/models";
import { getLocale, t } from "../core/i18n";
import { renderEmptyState } from "./emptyState";
import { taskSnoozePolicy } from "../features/taskSnoozePolicy";
import type { OfflineTaskActionState } from "../services/offlineQueue";

export type TaskListDataState = "live" | "cached" | "unavailable";

const TASK_TYPE_ICONS: Record<TaskType, string> = {
  water: "\u{1F4A7}",
  protect: "\u{1F6E1}\uFE0F",
  prune: "\u2702\uFE0F",
  deadhead: "\u{1F33A}",
  divide: "\u{1F500}",
  fertilize: "\u{1F9EA}",
  sow: "\u{1F331}",
  plant_out: "\u{1F33F}",
  observe_bloom: "\u{1F441}\uFE0F",
  harvest: "\u{1F9FA}",
  inspect_issue: "\u{1F50D}",
};

export interface TaskListCallbacks {
  onComplete: (task: GardenTask) => void;
  onSnooze: (task: GardenTask) => void;
  onSnoozeDate: (task: GardenTask) => void;
  onSkip: (task: GardenTask) => void;
  onReschedule: (task: GardenTask) => void;
  onEdit: (task: GardenTask) => void;
  onDelete: (task: GardenTask) => void;
  onPlantClick: (pltId: string) => void;
  onPlotClick: (plotId: string) => void;
  onToggleSelection?: ((task: GardenTask, selected: boolean) => void) | undefined;
  onDiscardOfflineAction?: ((state: OfflineTaskActionState) => void) | undefined;
  onRetryOfflineAction?: ((state: OfflineTaskActionState) => void) | undefined;
  offlineTaskActions?: ReadonlyMap<string, OfflineTaskActionState> | undefined;
  selectedTaskIds?: ReadonlySet<string> | undefined;
  onEmptyAction?: (() => void) | undefined;
  canWrite?: boolean | undefined;
  dataState?: TaskListDataState | undefined;
}

function isBatchActionable(task: GardenTask): boolean {
  return task.status === "pending" || task.status === "snoozed";
}

function getTaskDescription(task: GardenTask): string {
  const noDesc = task.metadata?.["description_no"];
  if (getLocale() === "no") {
    if (typeof noDesc === "string" && noDesc) return noDesc;
  }
  if (task.description) return task.description;
  if (typeof noDesc === "string" && noDesc) return noDesc;
  return "";
}

function isOverdue(dueOn: string): boolean {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(dueOn + "T00:00:00");
  return due < today;
}

function formatDueDate(dueOn: string): string {
  try {
    const d = new Date(dueOn + "T00:00:00");
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return dueOn;
  }
}

function taskWindowText(task: GardenTask): string {
  if (!task.window_start_on || !task.window_end_on) return "";
  return t("calendar.window_hint", {
    start: formatDueDate(task.window_start_on),
    end: formatDueDate(task.window_end_on),
  });
}

function createTaskCard(
  task: GardenTask,
  cbs: TaskListCallbacks,
  plantNames: Map<string, string>,
): HTMLElement {
  const card = document.createElement("div");
  card.className = `task-card${task.status === "completed" ? " task-completed" : ""}`;
  card.dataset["taskId"] = task.id;
  const offlineAction = cbs.offlineTaskActions?.get(task.id);
  if (offlineAction) {
    card.classList.add(`task-offline-${offlineAction.status}`);
    card.dataset["offlineTaskState"] = offlineAction.status;
  }
  if (cbs.selectedTaskIds?.has(task.id)) {
    card.classList.add("task-card-selected");
  }

  const header = document.createElement("div");
  header.className = "task-card-header";

  if (
    cbs.canWrite !== false
    && cbs.onToggleSelection
    && isBatchActionable(task)
    && !offlineAction
  ) {
    const selectionWrap = document.createElement("label");
    selectionWrap.className = "task-card-select";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "task-card-select-checkbox";
    checkbox.checked = cbs.selectedTaskIds?.has(task.id) ?? false;
    checkbox.setAttribute(
      "aria-label",
      t("tasks.select_task", {
        title: task.title || t(`tasks.type_${task.task_type}`),
      }),
    );
    checkbox.addEventListener("change", () => {
      cbs.onToggleSelection?.(task, checkbox.checked);
    });
    selectionWrap.appendChild(checkbox);
    header.appendChild(selectionWrap);
  }

  const icon = document.createElement("span");
  icon.className = "task-card-icon";
  icon.textContent = TASK_TYPE_ICONS[task.task_type] || "";
  header.appendChild(icon);

  const typeLabel = document.createElement("span");
  typeLabel.className = "task-card-type";
  typeLabel.textContent = t(`tasks.type_${task.task_type}`);
  header.appendChild(typeLabel);

  const severity = document.createElement("span");
  severity.className = `task-severity-chip severity-${task.severity}`;
  severity.textContent = t(`tasks.severity_${task.severity}`);
  header.appendChild(severity);

  if (task.status !== "pending") {
    const statusChip = document.createElement("span");
    statusChip.className = `task-status-chip status-${task.status}`;
    statusChip.textContent = t(`tasks.status_${task.status}`);
    header.appendChild(statusChip);
  }

  if (offlineAction) {
    const statusChip = document.createElement("span");
    statusChip.className = `task-status-chip task-offline-status status-${offlineAction.status}`;
    statusChip.textContent = t(`offline.task_${offlineAction.status}`, {
      action: t(`tasks.action_${offlineAction.action}`),
    });
    header.appendChild(statusChip);
  }

  const due = document.createElement("span");
  due.className = "task-card-due";
  if (task.status === "pending" && isOverdue(task.due_on)) {
    due.classList.add("overdue");
    due.textContent = `${t("tasks.overdue")} \u2014 ${formatDueDate(task.due_on)}`;
  } else {
    due.textContent = formatDueDate(task.due_on);
  }
  header.appendChild(due);

  card.appendChild(header);

  if (task.title) {
    const title = document.createElement("div");
    title.className = "task-card-title";
    title.textContent = task.title;
    card.appendChild(title);
  }

  const descText = getTaskDescription(task);
  if (descText) {
    const desc = document.createElement("div");
    desc.className = "task-card-description";
    desc.textContent =
      descText.length > 200
        ? descText.slice(0, 200) + "\u2026"
        : descText;
    card.appendChild(desc);
  }

  const windowText = taskWindowText(task);
  if (windowText) {
    const windowHint = document.createElement("div");
    windowHint.className = "task-card-window";
    windowHint.textContent = windowText;
    card.appendChild(windowHint);
  }

  if (task.plant_ids.length > 0) {
    const tags = document.createElement("div");
    tags.className = "task-card-tags";
    for (const pltId of task.plant_ids) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "journal-tag journal-tag-plant";
      tag.textContent = plantNames.get(pltId) ?? pltId;
      tag.addEventListener("click", () => cbs.onPlantClick(pltId));
      tags.appendChild(tag);
    }
    card.appendChild(tags);
  }

  const footer = document.createElement("div");
  footer.className = "task-card-footer";

  if (offlineAction) {
    const offlineState = document.createElement("div");
    offlineState.className = `task-offline-state task-offline-state--${offlineAction.status}`;
    offlineState.setAttribute("role", offlineAction.status === "failed" ? "alert" : "status");
    const message = document.createElement("span");
    message.textContent = t(`offline.task_${offlineAction.status}`, {
      action: t(`tasks.action_${offlineAction.action}`),
    });
    offlineState.appendChild(message);
    if (offlineAction.status === "failed" && offlineAction.lastError) {
      const error = document.createElement("span");
      error.className = "task-offline-error";
      error.textContent = offlineAction.lastError;
      offlineState.appendChild(error);
    }
    const recovery = document.createElement("div");
    recovery.className = "task-offline-recovery";
    if (offlineAction.status === "failed" && cbs.onRetryOfflineAction) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "task-action-btn task-offline-retry";
      retry.textContent = t("offline.retry");
      retry.addEventListener("click", () => cbs.onRetryOfflineAction?.(offlineAction));
      recovery.appendChild(retry);
    }
    if (cbs.onDiscardOfflineAction) {
      const discard = document.createElement("button");
      discard.type = "button";
      discard.className = "task-action-btn task-offline-discard";
      discard.textContent = t("offline.discard");
      discard.addEventListener("click", () => cbs.onDiscardOfflineAction?.(offlineAction));
      recovery.appendChild(discard);
    }
    offlineState.appendChild(recovery);
    card.appendChild(offlineState);
  }

  if (task.rule_source) {
    const rule = document.createElement("span");
    rule.className = "task-card-rule";
    rule.textContent = t("tasks.rule_generated");
    footer.appendChild(rule);
  }

  const actions = document.createElement("div");
  actions.className = "task-card-actions";

  if (
    cbs.canWrite !== false
    && !offlineAction
    && (task.status === "pending" || task.status === "snoozed")
  ) {
    const completeBtn = document.createElement("button");
    completeBtn.type = "button";
    completeBtn.className = "task-action-btn task-action-complete";
    completeBtn.textContent = t("tasks.action_complete");
    completeBtn.addEventListener("click", () => cbs.onComplete(task));
    actions.appendChild(completeBtn);

    const snoozeBtn = document.createElement("button");
    snoozeBtn.type = "button";
    snoozeBtn.className = "task-action-btn";
    snoozeBtn.textContent = taskSnoozePolicy(task).label;
    snoozeBtn.addEventListener("click", () => cbs.onSnooze(task));
    actions.appendChild(snoozeBtn);

    const snoozeDateBtn = document.createElement("button");
    snoozeDateBtn.type = "button";
    snoozeDateBtn.className = "task-action-btn";
    snoozeDateBtn.textContent = t("tasks.snooze_change_date");
    snoozeDateBtn.addEventListener("click", () => cbs.onSnoozeDate(task));
    actions.appendChild(snoozeDateBtn);

    const rescheduleBtn = document.createElement("button");
    rescheduleBtn.type = "button";
    rescheduleBtn.className = "task-action-btn";
    rescheduleBtn.textContent = t("tasks.action_reschedule");
    rescheduleBtn.addEventListener("click", () => cbs.onReschedule(task));
    actions.appendChild(rescheduleBtn);

    const skipBtn = document.createElement("button");
    skipBtn.type = "button";
    skipBtn.className = "task-action-btn";
    skipBtn.textContent = t("tasks.action_skip");
    skipBtn.addEventListener("click", () => cbs.onSkip(task));
    actions.appendChild(skipBtn);
  }

  if (cbs.canWrite !== false && !offlineAction) {
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "task-action-btn";
    editBtn.textContent = t("common.edit");
    editBtn.addEventListener("click", () => cbs.onEdit(task));
    actions.appendChild(editBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "task-action-btn task-action-delete";
    deleteBtn.textContent = t("common.delete");
    deleteBtn.addEventListener("click", () => cbs.onDelete(task));
    actions.appendChild(deleteBtn);
  }

  if (actions.childElementCount > 0) {
    footer.appendChild(actions);
  }
  card.appendChild(footer);

  return card;
}

export function renderTaskList(
  container: HTMLElement,
  tasks: GardenTask[],
  cbs: TaskListCallbacks,
  plantNames: Map<string, string>,
): void {
  container.replaceChildren();

  if (cbs.dataState === "unavailable") {
    const unavailable = document.createElement("div");
    unavailable.className = "offline-data-state offline-data-state--unavailable";
    unavailable.setAttribute("role", "status");
    unavailable.textContent = t("tasks.offline_unavailable");
    container.appendChild(unavailable);
    return;
  }
  if (cbs.dataState === "cached") {
    const cached = document.createElement("div");
    cached.className = "offline-data-state offline-data-state--cached";
    cached.setAttribute("role", "status");
    cached.textContent = t("tasks.offline_cached");
    container.appendChild(cached);
  }

  if (tasks.length === 0) {
    const emptyContainer = document.createElement("div");
    container.appendChild(emptyContainer);
    renderEmptyState(emptyContainer, {
      icon: "\u2600\uFE0F",
      headline: t("tasks.empty"),
      hint: t("tasks.empty_hint"),
      ctaLabel: cbs.onEmptyAction ? t("tasks.empty_cta") : undefined,
      ctaAction: cbs.onEmptyAction,
    });
    return;
  }

  for (const task of tasks) {
    container.appendChild(createTaskCard(task, cbs, plantNames));
  }
}

export interface TaskFormOptions {
  task?: GardenTask | undefined;
  readOnly?: boolean | undefined;
  onSave: (data: Record<string, unknown>) => Promise<void> | void;
  onCancel: () => void;
}

export function createTaskForm(options: TaskFormOptions): HTMLElement {
  const { task, readOnly = false, onSave, onCancel } = options;
  const form = document.createElement("form");
  form.className = "modal-form";
  form.setAttribute("aria-labelledby", "task-form-title");

  const heading = document.createElement("h2");
  heading.id = "task-form-title";
  heading.textContent = t("tasks.form_title");
  form.appendChild(heading);

  // Task type
  const typeGroup = document.createElement("div");
  typeGroup.className = "form-group";
  const typeLabel = document.createElement("label");
  typeLabel.textContent = t("tasks.form_type");
  const typeSelect = document.createElement("select");
  typeSelect.id = "task-form-type";
  typeLabel.htmlFor = typeSelect.id;
  typeSelect.required = true;
  const taskTypes: Array<[string, string]> = [
    ["water", t("tasks.type_water")],
    ["protect", t("tasks.type_protect")],
    ["prune", t("tasks.type_prune")],
    ["deadhead", t("tasks.type_deadhead")],
    ["divide", t("tasks.type_divide")],
    ["fertilize", t("tasks.type_fertilize")],
    ["sow", t("tasks.type_sow")],
    ["plant_out", t("tasks.type_plant_out")],
    ["observe_bloom", t("tasks.type_observe_bloom")],
    ["harvest", t("tasks.type_harvest")],
    ["inspect_issue", t("tasks.type_inspect_issue")],
  ];
  for (const [val, label] of taskTypes) {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = label;
    if (task?.task_type === val) opt.selected = true;
    typeSelect.appendChild(opt);
  }
  typeGroup.appendChild(typeLabel);
  typeGroup.appendChild(typeSelect);
  form.appendChild(typeGroup);

  // Title
  const titleGroup = document.createElement("div");
  titleGroup.className = "form-group";
  const titleLabel = document.createElement("label");
  titleLabel.textContent = t("tasks.form_name");
  const titleInput = document.createElement("input");
  titleInput.id = "task-form-name";
  titleLabel.htmlFor = titleInput.id;
  titleInput.type = "text";
  titleInput.maxLength = 200;
  titleInput.value = task?.title || "";
  titleGroup.appendChild(titleLabel);
  titleGroup.appendChild(titleInput);
  form.appendChild(titleGroup);

  // Description
  const descGroup = document.createElement("div");
  descGroup.className = "form-group";
  const descLabel = document.createElement("label");
  descLabel.textContent = t("tasks.form_description");
  const descInput = document.createElement("textarea");
  descInput.id = "task-form-description";
  descLabel.htmlFor = descInput.id;
  descInput.maxLength = 4000;
  descInput.rows = 3;
  descInput.value = task?.description || "";
  descGroup.appendChild(descLabel);
  descGroup.appendChild(descInput);
  form.appendChild(descGroup);

  // Severity
  const sevGroup = document.createElement("div");
  sevGroup.className = "form-group";
  const sevLabel = document.createElement("label");
  sevLabel.textContent = t("tasks.form_severity");
  const sevSelect = document.createElement("select");
  sevSelect.id = "task-form-severity";
  sevLabel.htmlFor = sevSelect.id;
  for (const sev of ["low", "normal", "high"] as const) {
    const opt = document.createElement("option");
    opt.value = sev;
    opt.textContent = t(`tasks.severity_${sev}`);
    if ((task?.severity || "normal") === sev) opt.selected = true;
    sevSelect.appendChild(opt);
  }
  sevGroup.appendChild(sevLabel);
  sevGroup.appendChild(sevSelect);
  form.appendChild(sevGroup);

  // Due date
  const dueGroup = document.createElement("div");
  dueGroup.className = "form-group";
  const dueLabel = document.createElement("label");
  dueLabel.textContent = t("tasks.form_due");
  const dueInput = document.createElement("input");
  dueInput.id = "task-form-due";
  dueLabel.htmlFor = dueInput.id;
  dueInput.type = "date";
  dueInput.required = true;
  dueInput.value = task?.due_on || new Date().toISOString().slice(0, 10);
  dueGroup.appendChild(dueLabel);
  dueGroup.appendChild(dueInput);
  form.appendChild(dueGroup);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "form-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = t("common.save");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const data: Record<string, unknown> = {
      task_type: typeSelect.value,
      title: titleInput.value,
      description: descInput.value,
      severity: sevSelect.value,
      due_on: dueInput.value,
    };
    saveBtn.disabled = true;
    cancelBtn.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      await onSave(data);
    } finally {
      if (form.isConnected) {
        saveBtn.disabled = false;
        cancelBtn.disabled = false;
        form.removeAttribute("aria-busy");
      }
    }
  });
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn";
  cancelBtn.textContent = readOnly ? t("common.close") : t("common.cancel");
  cancelBtn.addEventListener("click", onCancel);
  btnRow.appendChild(saveBtn);
  btnRow.appendChild(cancelBtn);
  form.appendChild(btnRow);

  if (readOnly) {
    saveBtn.hidden = true;
    form
      .querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
        "input, textarea, select",
      )
      .forEach((control) => {
        control.disabled = true;
      });
  }

  return form;
}
