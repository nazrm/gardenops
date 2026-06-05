import { t } from "../core/i18n";

export interface QuickActionCallbacks {
  onCompleteTask: () => void;
  onLogJournal: () => void;
  onReportIssue: () => void;
  onLogHarvest: () => void;
  onSnoozeTask: () => void;
  onIdentifyPlant: () => void;
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
    { icon: "\u2705", label: t("quick_actions.complete_task"), key: "complete-task", cb: cbs.onCompleteTask },
    { icon: "\uD83D\uDCDD", label: t("quick_actions.log_journal"), key: "log-journal", cb: cbs.onLogJournal },
    { icon: "\uD83D\uDC1B", label: t("quick_actions.report_issue"), key: "report-issue", cb: cbs.onReportIssue },
    { icon: "\uD83E\uDDFA", label: t("quick_actions.log_harvest"), key: "log-harvest", cb: cbs.onLogHarvest },
    { icon: "\u23F0", label: t("quick_actions.snooze_task"), key: "snooze-task", cb: cbs.onSnoozeTask },
    { icon: "\uD83C\uDF3F", label: t("quick_actions.identify_plant"), key: "identify-plant", cb: cbs.onIdentifyPlant },
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
  tasks: ReadonlyArray<{ id: string; title: string; task_type: string }>,
  onComplete: (taskId: string) => void,
  onBack: () => void,
): void {
  container.replaceChildren();

  const header = document.createElement("div");
  header.className = "quick-action-header";

  const backBtn = document.createElement("button");
  backBtn.type = "button";
  backBtn.className = "quick-action-back";
  backBtn.textContent = "\u2190";
  backBtn.addEventListener("click", onBack);

  const title = document.createElement("h3");
  title.className = "quick-action-title";
  title.textContent = t("quick_actions.select_task");

  header.append(backBtn, title);
  container.appendChild(header);

  if (tasks.length === 0) {
    const empty = document.createElement("p");
    empty.className = "quick-action-empty";
    empty.textContent = t("quick_actions.no_pending_tasks");
    container.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "quick-action-task-list";

  for (const task of tasks) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick-action-task-item";
    btn.textContent = task.title || `Task #${task.id}`;
    btn.addEventListener("click", () => onComplete(task.id));
    list.appendChild(btn);
  }

  container.appendChild(list);
}

export function renderTaskQuickSnooze(
  container: HTMLElement,
  tasks: ReadonlyArray<{ id: string; title: string; task_type: string }>,
  onSnooze: (taskId: string) => void,
  onBack: () => void,
): void {
  container.replaceChildren();

  const header = document.createElement("div");
  header.className = "quick-action-header";

  const backBtn = document.createElement("button");
  backBtn.type = "button";
  backBtn.className = "quick-action-back";
  backBtn.textContent = "\u2190";
  backBtn.addEventListener("click", onBack);

  const title = document.createElement("h3");
  title.className = "quick-action-title";
  title.textContent = t("quick_actions.select_to_snooze");

  header.append(backBtn, title);
  container.appendChild(header);

  if (tasks.length === 0) {
    const empty = document.createElement("p");
    empty.className = "quick-action-empty";
    empty.textContent = t("quick_actions.no_pending_tasks");
    container.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "quick-action-task-list";

  for (const task of tasks) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick-action-task-item";
    btn.textContent = task.title || `Task #${task.id}`;
    btn.addEventListener("click", () => onSnooze(task.id));
    list.appendChild(btn);
  }

  container.appendChild(list);
}
