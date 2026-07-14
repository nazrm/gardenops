import { t } from "../core/i18n";

export interface QuickActionCallbacks {
  onCompleteTask: () => void;
  onLogJournal: () => void;
  onReportIssue: () => void;
  onLogHarvest: () => void;
  onSnoozeTask: () => void;
  onIdentifyPlant: () => void;
}

type QuickActionTask = {
  id: string;
  title: string;
  task_type: string;
  snooze_label?: string;
  offline_status?: "queued" | "failed";
};

export type QuickActionDataState = "live" | "cached" | "unavailable";

const QUICK_ACTION_ICONS = {
  complete: "\u2713",
  journal: "J",
  issue: "!",
  harvest: "+",
  snooze: "\u21b7",
  identify: "?",
} as const;

export interface QuickActionSnoozeNotice {
  message: string;
  actionLabel: string;
  durationMs: number;
  onChangeDate: () => void;
}

function appendTaskPicker(
  container: HTMLElement,
  tasks: ReadonlyArray<QuickActionTask>,
  onSelect: (taskId: string) => void,
  secondaryAction?: {
    label: string;
    onSelect: (taskId: string) => void;
  },
): void {
  const search = document.createElement("input");
  search.type = "search";
  search.className = "quick-action-task-search";
  search.placeholder = t("quick_actions.search_tasks");
  search.setAttribute("aria-label", t("quick_actions.search_tasks"));
  search.autocomplete = "off";

  const list = document.createElement("div");
  list.className = "quick-action-task-list";

  const renderMatches = (): void => {
    const query = search.value.trim().toLocaleLowerCase();
    const matches = query
      ? tasks.filter((task) => task.title.toLocaleLowerCase().includes(query))
      : tasks;
    list.replaceChildren();
    for (const task of matches) {
      const taskLabel = task.title || `Task #${task.id}`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "quick-action-task-item";
      const primaryLabel = task.snooze_label
        ? `${taskLabel}: ${task.snooze_label}`
        : taskLabel;
      const stateLabel = task.offline_status
        ? t(`offline.task_${task.offline_status}`, { action: "" }).trim()
        : "";
      const accessibleLabel = stateLabel ? `${primaryLabel}: ${stateLabel}` : primaryLabel;
      btn.textContent = accessibleLabel;
      btn.setAttribute("aria-label", accessibleLabel);
      btn.disabled = Boolean(task.offline_status);
      if (task.offline_status) {
        btn.classList.add(`quick-action-task-item--${task.offline_status}`);
        btn.dataset["offlineTaskState"] = task.offline_status;
      }
      btn.addEventListener("click", () => onSelect(task.id));
      list.appendChild(btn);
      if (secondaryAction) {
        const secondary = document.createElement("button");
        secondary.type = "button";
        secondary.className = "quick-action-task-item";
        secondary.textContent = secondaryAction.label;
        secondary.setAttribute("aria-label", `${taskLabel}: ${secondaryAction.label}`);
        secondary.disabled = Boolean(task.offline_status);
        secondary.addEventListener("click", () => secondaryAction.onSelect(task.id));
        list.appendChild(secondary);
      }
    }
    if (matches.length === 0) {
      const empty = document.createElement("p");
      empty.className = "quick-action-empty";
      empty.textContent = t("quick_actions.no_matching_tasks");
      list.appendChild(empty);
    }
  };

  search.addEventListener("input", renderMatches);
  container.append(search, list);
  renderMatches();
}

function appendQuickActionDataState(
  container: HTMLElement,
  dataState: QuickActionDataState,
): boolean {
  if (dataState === "live") return false;
  const status = document.createElement("div");
  status.className = `offline-data-state offline-data-state--${dataState}`;
  status.setAttribute("role", "status");
  status.textContent = t(
    dataState === "cached"
      ? "quick_actions.offline_cached"
      : "quick_actions.offline_unavailable",
  );
  container.appendChild(status);
  return dataState === "unavailable";
}

export function appendQuickActionSnoozeNotice(
  container: HTMLElement,
  notice: QuickActionSnoozeNotice,
): void {
  const element = document.createElement("div");
  element.className = "quick-action-snooze-notice";
  element.setAttribute("role", "status");
  element.setAttribute("aria-live", "polite");

  const message = document.createElement("span");
  message.textContent = notice.message;

  const action = document.createElement("button");
  action.type = "button";
  action.className = "quick-action-snooze-notice-action";
  action.textContent = notice.actionLabel;

  element.append(message, action);
  const header = container.querySelector(".quick-action-header");
  if (header) {
    header.insertAdjacentElement("afterend", element);
  } else {
    container.prepend(element);
  }

  let remainingMs = notice.durationMs;
  let startedAt = Date.now();
  let paused = false;
  let timeout = window.setTimeout(remove, remainingMs);

  function remove(): void {
    window.clearTimeout(timeout);
    element.remove();
  }

  function pause(): void {
    if (paused || !element.isConnected) return;
    paused = true;
    window.clearTimeout(timeout);
    remainingMs = Math.max(0, remainingMs - (Date.now() - startedAt));
  }

  function resume(): void {
    if (!paused || !element.isConnected) return;
    paused = false;
    startedAt = Date.now();
    timeout = window.setTimeout(remove, remainingMs);
  }

  action.addEventListener("click", () => {
    remove();
    notice.onChangeDate();
  });
  element.addEventListener("mouseenter", pause);
  element.addEventListener("mouseleave", resume);
  element.addEventListener("focusin", pause);
  element.addEventListener("focusout", () => {
    if (!element.contains(document.activeElement)) resume();
  });
}

export function renderQuickActionSheet(
  container: HTMLElement,
  cbs: QuickActionCallbacks,
): void {
  container.replaceChildren();

  const title = document.createElement("h3");
  title.className = "quick-action-title";
  title.textContent = t("quick_actions.title");
  container.appendChild(title);

  const grid = document.createElement("div");
  grid.className = "quick-action-grid";

  const actions: ReadonlyArray<{
    icon: string;
    label: string;
    key: string;
    cb: () => void;
  }> = [
    { icon: QUICK_ACTION_ICONS.complete, label: t("quick_actions.complete_task"), key: "complete-task", cb: cbs.onCompleteTask },
    { icon: QUICK_ACTION_ICONS.journal, label: t("quick_actions.log_journal"), key: "log-journal", cb: cbs.onLogJournal },
    { icon: QUICK_ACTION_ICONS.issue, label: t("quick_actions.report_issue"), key: "report-issue", cb: cbs.onReportIssue },
    { icon: QUICK_ACTION_ICONS.harvest, label: t("quick_actions.log_harvest"), key: "log-harvest", cb: cbs.onLogHarvest },
    { icon: QUICK_ACTION_ICONS.snooze, label: t("quick_actions.snooze_task"), key: "snooze-task", cb: cbs.onSnoozeTask },
    { icon: QUICK_ACTION_ICONS.identify, label: t("quick_actions.identify_plant"), key: "identify-plant", cb: cbs.onIdentifyPlant },
  ];

  for (const action of actions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick-action-btn";
    btn.dataset["quickAction"] = action.key;

    const icon = document.createElement("span");
    icon.className = "quick-action-icon";
    icon.textContent = action.icon;

    const label = document.createElement("span");
    label.className = "quick-action-label";
    label.textContent = action.label;

    btn.append(icon, label);
    btn.addEventListener("click", () => action.cb());
    grid.appendChild(btn);
  }

  container.appendChild(grid);
}

export function renderTaskQuickComplete(
  container: HTMLElement,
  tasks: ReadonlyArray<QuickActionTask>,
  onComplete: (taskId: string) => void,
  onBack: () => void,
  dataState: QuickActionDataState = "live",
): void {
  container.replaceChildren();

  const header = document.createElement("div");
  header.className = "quick-action-header";

  const backBtn = document.createElement("button");
  backBtn.type = "button";
  backBtn.className = "quick-action-back";
  backBtn.textContent = "\u2190";
  backBtn.setAttribute("aria-label", t("quick_actions.back"));
  backBtn.addEventListener("click", onBack);

  const title = document.createElement("h3");
  title.className = "quick-action-title";
  title.textContent = t("quick_actions.select_task");

  header.append(backBtn, title);
  container.appendChild(header);

  if (appendQuickActionDataState(container, dataState)) return;

  if (tasks.length === 0) {
    const empty = document.createElement("p");
    empty.className = "quick-action-empty";
    empty.textContent = t("quick_actions.no_pending_tasks");
    container.appendChild(empty);
    return;
  }

  appendTaskPicker(container, tasks, onComplete);
}

export function renderTaskQuickSnooze(
  container: HTMLElement,
  tasks: ReadonlyArray<QuickActionTask>,
  onSnooze: (taskId: string) => void,
  onSnoozeDate: (taskId: string) => void,
  onBack: () => void,
  dataState: QuickActionDataState = "live",
): void {
  container.replaceChildren();

  const header = document.createElement("div");
  header.className = "quick-action-header";

  const backBtn = document.createElement("button");
  backBtn.type = "button";
  backBtn.className = "quick-action-back";
  backBtn.textContent = "\u2190";
  backBtn.setAttribute("aria-label", t("quick_actions.back"));
  backBtn.addEventListener("click", onBack);

  const title = document.createElement("h3");
  title.className = "quick-action-title";
  title.textContent = t("quick_actions.select_to_snooze");

  header.append(backBtn, title);
  container.appendChild(header);

  if (appendQuickActionDataState(container, dataState)) return;

  if (tasks.length === 0) {
    const empty = document.createElement("p");
    empty.className = "quick-action-empty";
    empty.textContent = t("quick_actions.no_pending_tasks");
    container.appendChild(empty);
    return;
  }

  appendTaskPicker(container, tasks, onSnooze, {
    label: t("tasks.snooze_change_date") as string,
    onSelect: onSnoozeDate,
  });
}
