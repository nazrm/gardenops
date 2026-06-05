import { getLocaleTag, localizeEnum, t } from "../core/i18n";
import type {
  TodayDashboard,
  TodayTask,
  TodayIssue,
  TodayWeatherAlert,
  TodayForecast,
} from "../services/api";

export interface TodayCallbacks {
  onTaskClick: (taskId: string) => void;
  onIssueClick: (issueId: string) => void;
  onWeatherClick: () => void;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00Z");
  return new Intl.DateTimeFormat(getLocaleTag(), {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(d);
}

function severityClass(severity: string): string {
  if (severity === "critical" || severity === "high") {
    return "today-card--danger";
  }
  if (severity === "normal") return "today-card--warn";
  return "";
}

function buildTaskCard(
  task: TodayTask,
  onClick: () => void,
): HTMLElement {
  const card = el("button", `today-card ${severityClass(task.severity)}`);
  card.type = "button";
  const title = el("span", "today-card-title", task.title);
  const meta = el(
    "span",
    "today-card-meta",
    `${localizeEnum("task_type", task.task_type)} · ${localizeEnum("severity", task.severity)}`,
  );
  card.append(title, meta);
  card.addEventListener("click", onClick);
  return card;
}

function buildIssueCard(
  issue: TodayIssue,
  onClick: () => void,
): HTMLElement {
  const card = el("button", `today-card ${severityClass(issue.severity)}`);
  card.type = "button";
  const title = el("span", "today-card-title", issue.title);
  const meta = el(
    "span",
    "today-card-meta",
    `${localizeEnum("issue_type", issue.issue_type)}`
      + ` · ${localizeEnum("severity", issue.severity)}`
      + ` · ${localizeEnum("issue_status", issue.status)}`,
  );
  card.append(title, meta);
  card.addEventListener("click", onClick);
  return card;
}

function buildAlertCard(
  alert: TodayWeatherAlert,
  onClick: () => void,
): HTMLElement {
  const card = el("button", `today-card ${severityClass(alert.severity)}`);
  card.type = "button";
  const title = el("span", "today-card-title", alert.title);
  const meta = el(
    "span",
    "today-card-meta",
    `${localizeEnum("alert_type", alert.alert_type)} · ${localizeEnum("severity", alert.severity)}`,
  );
  card.append(title, meta);
  card.addEventListener("click", onClick);
  return card;
}

function buildForecastStrip(
  forecast: TodayForecast,
): HTMLElement {
  const strip = el("div", "today-forecast-strip");
  const parts: string[] = [];
  if (
    forecast.temp_min !== undefined &&
    forecast.temp_max !== undefined
  ) {
    parts.push(`${forecast.temp_min}°–${forecast.temp_max}°`);
  }
  if (
    forecast.precipitation !== undefined &&
    forecast.precipitation > 0
  ) {
    parts.push(
      `${forecast.precipitation} mm`,
    );
  }
  if (parts.length === 0) parts.push("—");
  strip.textContent = parts.join(" · ");
  return strip;
}

function buildSection(
  kicker: string,
  title: string,
  cards: HTMLElement[],
  emptyText?: string,
): HTMLElement {
  const section = el("div", "today-section");
  const header = el("div", "today-section-header");
  header.append(
    el("p", "reports-kicker", kicker),
    el("h3", "reports-section-title", title),
  );
  section.appendChild(header);
  if (cards.length === 0 && emptyText) {
    const empty = el("p", "today-empty", emptyText);
    section.appendChild(empty);
  } else {
    const list = el("div", "today-card-list");
    for (const card of cards) list.appendChild(card);
    section.appendChild(list);
  }
  return section;
}

function totalBadge(visibleCount: number, totalCount?: number): string {
  const total = typeof totalCount === "number" ? totalCount : visibleCount;
  return total > visibleCount ? `${visibleCount}/${total}` : `${visibleCount}`;
}

export function renderTodayDashboard(
  container: HTMLElement,
  data: TodayDashboard,
  callbacks: TodayCallbacks,
): void {
  container.replaceChildren();

  const dateHeader = el("div", "today-header");
  dateHeader.append(
    el("h2", "today-title", t("today.title")),
    el("p", "today-date", formatDate(data.date)),
  );
  container.appendChild(dateHeader);

  if (data.forecast_today) {
    const forecastSection = el("div", "today-section");
    const forecastHeader = el("div", "today-section-header");
    forecastHeader.append(
      el("p", "reports-kicker", t("today.forecast")),
    );
    forecastSection.append(
      forecastHeader,
      buildForecastStrip(data.forecast_today),
    );
    container.appendChild(forecastSection);
  }

  if (data.tasks_overdue.length > 0) {
    const cards = data.tasks_overdue.map((task) =>
      buildTaskCard(task, () =>
        callbacks.onTaskClick(task.id),
      ),
    );
    container.appendChild(
      buildSection(
        t("today.overdue"),
        totalBadge(data.tasks_overdue.length, data.tasks_overdue_total),
        cards,
      ),
    );
  }

  const todayCards = data.tasks_due_today.map((task) =>
    buildTaskCard(task, () =>
      callbacks.onTaskClick(task.id),
    ),
  );
  container.appendChild(
    buildSection(
      t("today.due_today"),
      totalBadge(data.tasks_due_today.length, data.tasks_due_today_total),
      todayCards,
      t("today.no_tasks"),
    ),
  );

  if (data.weather_alerts.length > 0) {
    const alertCards = data.weather_alerts.map((alert) =>
      buildAlertCard(alert, () =>
        callbacks.onWeatherClick(),
      ),
    );
    container.appendChild(
      buildSection(
        t("today.weather"),
        totalBadge(data.weather_alerts.length, data.weather_alerts_total),
        alertCards,
      ),
    );
  }

  if (data.active_issues.length > 0) {
    const issueCards = data.active_issues.map((issue) =>
      buildIssueCard(issue, () =>
        callbacks.onIssueClick(issue.id),
      ),
    );
    container.appendChild(
      buildSection(
        t("today.issues"),
        totalBadge(data.active_issues.length, data.active_issues_total),
        issueCards,
      ),
    );
  }

  if (data.tasks_upcoming.length > 0) {
    const upcomingCards = data.tasks_upcoming.map(
      (task) =>
        buildTaskCard(task, () =>
          callbacks.onTaskClick(task.id),
        ),
    );
    container.appendChild(
      buildSection(
        t("today.upcoming"),
        totalBadge(data.tasks_upcoming.length, data.tasks_upcoming_total),
        upcomingCards,
      ),
    );
  }

  if (
    data.tasks_overdue.length === 0 &&
    data.tasks_due_today.length === 0 &&
    data.weather_alerts.length === 0 &&
    data.active_issues.length === 0
  ) {
    const allClear = el(
      "div",
      "today-all-clear",
    );
    allClear.append(
      el("p", "today-all-clear-text", t("today.all_clear")),
    );
    container.appendChild(allClear);
  }
}
