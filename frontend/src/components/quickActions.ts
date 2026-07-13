import { t } from "../core/i18n";

export interface QuickActionCallbacks {
  onCompleteTask: () => void;
  onLogJournal: () => void;
  onReportIssue: () => void;
  onLogHarvest: () => void;
  onSnoozeTask: () => void;
  onIdentifyPlant: () => void;
}

type QuickActionTask = { id: string; title: string; task_type: string };

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
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "quick-action-task-item";
      btn.textContent = task.title || `Task #${task.id}`;
      btn.addEventListener("click", () => onSelect(task.id));
      list.appendChild(btn);
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
  backBtn.setAttribute("aria-label", t("quick_actions.back"));
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

  appendTaskPicker(container, tasks, onComplete);
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
  backBtn.setAttribute("aria-label", t("quick_actions.back"));
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

  appendTaskPicker(container, tasks, onSnooze);
}
