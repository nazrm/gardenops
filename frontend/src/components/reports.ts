import { getLocaleTag, t } from "../core/i18n";
import type { AutomationStatus, GardenerReports } from "../services/api";
import { getAutomationStatusApi } from "../services/api";

export interface GardenerReportsCallbacks {
  onZoneChange: (zoneCode: string) => void;
  onOpenTasks: (view: "overdue" | "week") => void;
  onOpenIssues: (filter?: "open" | "overdue_followups") => void;
  onOpenWeather: () => void;
  onOpenPlants: (pltIds: string[]) => void;
  onOpenBatchJournal: (pltIds: string[]) => void;
  onOpenMap: (plotIds: string[]) => void;
  onOpenCare: (pltIds: string[]) => void;
  onOpenHarvest: () => void;
}

function createElement<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function formatMonth(month: number | null): string {
  if (!month || month < 1 || month > 12) return t("common.na");
  const dt = new Date(Date.UTC(2026, month - 1, 1));
  return new Intl.DateTimeFormat(getLocaleTag(), {
    month: "long",
    timeZone: "UTC",
  }).format(dt);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(getLocaleTag()).format(value);
}

function formatPreviewNames(items: Array<{ name: string }>): string {
  if (items.length === 0) return t("reports.none_ready");
  const preview = items.slice(0, 3).map((item) => item.name);
  if (items.length <= 3) return preview.join(", ");
  return t("reports.preview_more", {
    names: preview.join(", "),
    count: items.length - 3,
  });
}

function formatPreviewPlots(items: Array<{ plot_id: string }>): string {
  if (items.length === 0) return t("reports.none_ready");
  const preview = items.slice(0, 4).map((item) => item.plot_id);
  if (items.length <= 4) return preview.join(", ");
  return t("reports.preview_more", {
    names: preview.join(", "),
    count: items.length - 4,
  });
}

function createActionButton(
  label: string,
  detail: string,
  count: number,
  actionLabel: string,
  disabled: boolean,
  onClick: () => void,
): HTMLElement {
  const button = createElement(
    "button",
    `report-action-card${disabled ? " is-disabled" : ""}`,
  );
  button.type = "button";
  button.disabled = disabled;
  if (!disabled) {
    button.addEventListener("click", onClick);
  }
  button.append(
    createElement("span", "report-action-count", formatNumber(count)),
    createElement("strong", "report-action-title", label),
    createElement("span", "report-action-detail", detail),
    createElement("span", "report-action-link", actionLabel),
  );
  return button;
}

function createSection(
  title: string,
  kicker: string,
  subtitle: string,
): HTMLElement {
  const section = createElement("section", "reports-section");
  const header = createElement("div", "reports-section-header");
  header.append(
    createElement("p", "reports-kicker", kicker),
    createElement("h3", "reports-section-title", title),
    createElement("p", "reports-section-subtitle", subtitle),
  );
  section.appendChild(header);
  return section;
}

export function renderGardenerReports(
  container: HTMLElement,
  data: GardenerReports,
  callbacks: GardenerReportsCallbacks,
): void {
  container.replaceChildren();

  const shell = createElement("div", "reports-dashboard-shell");
  const header = createElement("div", "reports-dashboard-header");
  const copy = createElement("div", "reports-dashboard-copy");
  copy.append(
    createElement("p", "reports-kicker", t("reports.kicker")),
    createElement("h2", "reports-dashboard-title", t("reports.title")),
    createElement(
      "p",
      "reports-dashboard-subtitle",
      data.zone_name
        ? t("reports.subtitle_zone", { zone: data.zone_name })
        : t("reports.subtitle"),
    ),
  );

  const controls = createElement("div", "reports-dashboard-controls");
  const zoneLabel = createElement("label", "reports-zone-filter");
  zoneLabel.appendChild(createElement("span", "", t("reports.zone_label")));
  const select = document.createElement("select");
  select.className = "select-sm";
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = t("reports.zone_all");
  select.appendChild(allOption);
  data.available_zones.forEach((zone) => {
    const option = document.createElement("option");
    option.value = zone.zone_code;
    option.textContent = t("reports.zone_option", {
      code: zone.zone_code,
      name: zone.zone_name,
      count: zone.plot_count,
    });
    if (zone.zone_code === (data.zone_code ?? "")) option.selected = true;
    select.appendChild(option);
  });
  select.addEventListener("change", () => callbacks.onZoneChange(select.value));
  zoneLabel.appendChild(select);
  controls.appendChild(zoneLabel);

  header.append(copy, controls);
  shell.appendChild(header);

  const grid = createElement("div", "reports-grid");

  const needs = createSection(
    t("reports.needs_attention_title"),
    t("reports.needs_attention_kicker"),
    t("reports.needs_attention_subtitle"),
  );
  const needsGrid = createElement("div", "reports-actions-grid");
  needsGrid.append(
    createActionButton(
      t("reports.overdue_tasks_title"),
      t("reports.overdue_tasks_desc"),
      data.needs_attention.overdue_tasks_count,
      t("reports.open_tasks"),
      data.needs_attention.overdue_tasks_count === 0,
      () => callbacks.onOpenTasks("overdue"),
    ),
    createActionButton(
      t("reports.week_tasks_title"),
      t("reports.week_tasks_desc"),
      data.needs_attention.due_this_week_count,
      t("reports.open_tasks"),
      data.needs_attention.due_this_week_count === 0,
      () => callbacks.onOpenTasks("week"),
    ),
    createActionButton(
      t("reports.open_issues_title"),
      t("reports.open_issues_desc"),
      data.needs_attention.open_issues_count,
      t("reports.open_issues"),
      data.needs_attention.open_issues_count === 0,
      () => callbacks.onOpenIssues("open"),
    ),
    createActionButton(
      t("reports.followups_title"),
      t("reports.followups_desc"),
      data.needs_attention.overdue_follow_ups_count,
      t("reports.review_issues"),
      data.needs_attention.overdue_follow_ups_count === 0,
      () => callbacks.onOpenIssues("overdue_followups"),
    ),
    createActionButton(
      t("reports.weather_alerts_title"),
      data.needs_attention.weather_alert_titles.length > 0
        ? data.needs_attention.weather_alert_titles.join(", ")
        : t("reports.weather_alerts_desc"),
      data.needs_attention.active_weather_alerts_count,
      t("reports.open_weather"),
      data.needs_attention.active_weather_alerts_count === 0,
      () => callbacks.onOpenWeather(),
    ),
  );
  needs.appendChild(needsGrid);
  grid.appendChild(needs);

  const bloom = createSection(
    t("reports.bloom_title"),
    t("reports.bloom_kicker"),
    t("reports.bloom_subtitle"),
  );
  const bloomGrid = createElement("div", "reports-dual-grid");
  const bloomNow = createActionButton(
    t("reports.bloom_now_title", { month: formatMonth(data.bloom_now.month) }),
    formatPreviewNames(data.bloom_now.plants),
    data.bloom_now.count,
    t("reports.show_plants"),
    data.bloom_now.plant_ids.length === 0,
    () => callbacks.onOpenPlants(data.bloom_now.plant_ids),
  );
  const bloomNext = createActionButton(
    t("reports.bloom_next_title", { month: formatMonth(data.bloom_next.month) }),
    formatPreviewNames(data.bloom_next.plants),
    data.bloom_next.count,
    t("reports.show_plants"),
    data.bloom_next.plant_ids.length === 0,
    () => callbacks.onOpenPlants(data.bloom_next.plant_ids),
  );
  bloomGrid.append(bloomNow, bloomNext);
  bloom.appendChild(bloomGrid);
  grid.appendChild(bloom);

  const observations = createSection(
    t("reports.observations_title"),
    t("reports.observations_kicker"),
    t("reports.observations_subtitle", {
      months: data.missing_observations.threshold_months,
    }),
  );
  observations.appendChild(
    createActionButton(
      t("reports.observations_cta_title"),
      formatPreviewNames(data.missing_observations.plants),
      data.missing_observations.count,
      t("reports.open_journal"),
      data.missing_observations.plant_ids.length === 0,
      () => callbacks.onOpenBatchJournal(data.missing_observations.plant_ids),
    ),
  );
  grid.appendChild(observations);

  const plotUse = createSection(
    t("reports.plot_use_title"),
    t("reports.plot_use_kicker"),
    t("reports.plot_use_subtitle", { count: data.plot_use.total_plots }),
  );
  const plotGrid = createElement("div", "reports-dual-grid");
  plotGrid.append(
    createActionButton(
      t("reports.empty_plots_title"),
      formatPreviewPlots(data.plot_use.empty_plots),
      data.plot_use.empty_count,
      t("reports.show_on_map"),
      data.plot_use.empty_plot_ids.length === 0,
      () => callbacks.onOpenMap(data.plot_use.empty_plot_ids),
    ),
    createActionButton(
      t("reports.underused_plots_title"),
      formatPreviewPlots(data.plot_use.underused_plots),
      data.plot_use.underused_count,
      t("reports.show_on_map"),
      data.plot_use.underused_plot_ids.length === 0,
      () => callbacks.onOpenMap(data.plot_use.underused_plot_ids),
    ),
  );
  plotUse.appendChild(plotGrid);
  grid.appendChild(plotUse);

  const quality = createSection(
    t("reports.data_quality_title"),
    t("reports.data_quality_kicker"),
    t("reports.data_quality_subtitle"),
  );
  const qualityGrid = createElement("div", "reports-actions-grid");
  qualityGrid.append(
    createActionButton(
      t("reports.missing_care_title"),
      formatPreviewNames(data.data_quality.missing_care_plants),
      data.data_quality.missing_care_count,
      t("reports.open_care"),
      data.data_quality.missing_care_plant_ids.length === 0,
      () => callbacks.onOpenCare(data.data_quality.missing_care_plant_ids),
    ),
    createActionButton(
      t("reports.missing_year_title"),
      formatPreviewNames(data.data_quality.missing_year_plants),
      data.data_quality.missing_year_count,
      t("reports.show_plants"),
      data.data_quality.missing_year_plant_ids.length === 0,
      () => callbacks.onOpenPlants(data.data_quality.missing_year_plant_ids),
    ),
    createActionButton(
      t("reports.missing_cover_title"),
      formatPreviewNames(data.data_quality.missing_cover_plants),
      data.data_quality.missing_cover_count,
      t("reports.show_plants"),
      data.data_quality.missing_cover_plant_ids.length === 0,
      () => callbacks.onOpenPlants(data.data_quality.missing_cover_plant_ids),
    ),
  );
  quality.appendChild(qualityGrid);
  grid.appendChild(quality);

  const yieldSection = createSection(
    t("reports.yield_title"),
    t("reports.yield_kicker"),
    t("reports.yield_subtitle", { year: data.yield_summary.year }),
  );
  const summary = createElement("div", "reports-yield-summary");
  summary.append(
    createElement(
      "div",
      "reports-yield-stat",
      t("reports.yield_total_entries", {
        count: data.yield_summary.total_entries,
      }),
    ),
    createElement(
      "div",
      "reports-yield-stat",
      t("reports.yield_harvested_plots", {
        count: data.yield_summary.harvested_plot_count,
      }),
    ),
    createElement(
      "div",
      "reports-yield-stat",
      t("reports.yield_active_months", {
        count: data.yield_summary.active_month_count,
      }),
    ),
    createElement(
      "div",
      "reports-yield-stat",
      data.yield_summary.best_month
        ? t("reports.yield_best_month", {
          month: formatMonth(data.yield_summary.best_month),
          count: data.yield_summary.best_month_entries,
        })
        : t("reports.yield_none"),
    ),
  );
  yieldSection.appendChild(summary);

  const producerList = createElement("div", "reports-producer-list");
  if (data.yield_summary.top_producers.length === 0) {
    producerList.appendChild(
      createElement("p", "reports-empty-note", t("reports.yield_none")),
    );
  } else {
    data.yield_summary.top_producers.forEach((producer) => {
      const row = createElement("button", "reports-producer-row");
      row.type = "button";
      row.addEventListener("click", () => callbacks.onOpenPlants([producer.plt_id]));
      const name = createElement("strong", "reports-producer-name", producer.name);
      const meta = createElement(
        "span",
        "reports-producer-meta",
        t("reports.yield_producer_meta", {
          count: producer.entries,
          units: producer.units
            .slice(0, 2)
            .map((unit) => `${formatNumber(unit.total_qty)} ${unit.unit}`)
            .join(", "),
        }),
      );
      row.append(name, meta);
      producerList.appendChild(row);
    });
  }
  yieldSection.appendChild(producerList);
  const yieldAction = createElement("button", "report-inline-link", t("reports.open_harvest"));
  yieldAction.type = "button";
  yieldAction.disabled = data.yield_summary.total_entries === 0;
  yieldAction.addEventListener("click", () => callbacks.onOpenHarvest());
  yieldSection.appendChild(yieldAction);
  grid.appendChild(yieldSection);

  const automationSection = createSection(
    t("automation.title"),
    "",
    "",
  );
  const automationContent = createElement("div", "reports-automation-loading");
  automationContent.textContent = "...";
  automationSection.appendChild(automationContent);
  grid.appendChild(automationSection);

  getAutomationStatusApi()
    .then((status: AutomationStatus) => {
      automationContent.textContent = "";
      automationContent.className = "reports-automation-summary";
      if (status.total === 0) {
        automationContent.textContent = t("automation.no_recent");
      } else {
        automationContent.textContent = t("automation.recent_tasks", {
          count: status.total,
        });
      }
    })
    .catch(() => {
      automationContent.textContent = t("automation.no_recent");
      automationContent.className = "reports-automation-summary";
    });

  shell.appendChild(grid);
  container.appendChild(shell);
}
