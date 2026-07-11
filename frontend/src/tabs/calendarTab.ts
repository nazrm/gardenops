import { Calendar, type EventContentArg, type EventMountArg } from "@fullcalendar/core";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import listPlugin from "@fullcalendar/list";
import timeGridPlugin from "@fullcalendar/timegrid";
import enGbLocale from "@fullcalendar/core/locales/en-gb";
import nbLocale from "@fullcalendar/core/locales/nb";

import { createChipInput, type ChipInputResult } from "../components/chipInput";
import { createModal } from "../components/dialogCore";
import type { AppContext } from "../core/appContext";
import { queryButton, querySelect } from "../core/dom";
import { getLocale, t } from "../core/i18n";
import type {
  CalendarCapabilities,
  CalendarEvent,
  CalendarManualEventDraft,
  CalendarManualEventInput,
  CalendarPresetDefinition,
  CalendarPresetKey,
  CalendarSourceDefinition,
  CalendarSourceKey,
  CalendarSubscription,
  CalendarViewMode,
  GardenTask,
  Plant,
  Plot,
} from "../core/models";
import {
  buildCalendarExportUrl,
  createCalendarSubscriptionApi,
  createCalendarManualEventApi,
  deleteCalendarManualEventApi,
  deleteCalendarSubscriptionApi,
  fetchCalendarEventsApi,
  fetchCalendarPreferencesApi,
  getApiErrorMessage,
  listCalendarSubscriptionsApi,
  type TaskActionRequest,
  taskActionApi,
  updateCalendarManualEventApi,
  updateCalendarPreferencesApi,
} from "../services/api";
import { taskSnoozePolicy } from "../features/taskSnoozePolicy";
import {
  canQueueDefaultCompletionOffline,
  needsCompletionDialog,
  openTaskCompletionDialog,
} from "../features/taskCompletionFlow";

let ctx: AppContext;
let calendar: Calendar | null = null;
let currentEventsById = new Map<string, CalendarEvent>();
let availableSources: CalendarSourceDefinition[] = [];
let presets: CalendarPresetDefinition[] = [];
let capabilities: CalendarCapabilities = {
  can_subscribe: false,
  can_revoke_all: false,
};
let subscriptions: CalendarSubscription[] = [];
let currentViewMode: CalendarViewMode = "month";
let currentPreset: CalendarPresetKey = "essential";
let visibleSources = new Set<CalendarSourceKey>();
let includeRecentHistory = false;
let selectedPlantIds = new Set<string>();
let selectedPlotIds = new Set<string>();
let selectedZoneCodes = new Set<string>();
let selectedEventId: string | null = null;
let currentSummaryCount = 0;
let preferencesLoaded = false;
let initBound = false;
let calendarPlantInput: ChipInputResult | null = null;
let calendarPlotInput: ChipInputResult | null = null;
let calendarZoneInput: ChipInputResult | null = null;

function dedupeOrdered(values: string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const raw of values) {
    const value = String(raw || "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    ordered.push(value);
  }
  return ordered;
}

interface CalendarZoneOption {
  zone_code: string;
  zone_name: string;
}

function nextAnimationFrame(): Promise<void> {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => resolve());
  });
}

function currentLocaleObject() {
  return getLocale() === "no" ? nbLocale : enGbLocale;
}

function defaultResponsiveView(): CalendarViewMode {
  return window.matchMedia("(max-width: 760px)").matches ? "agenda" : "month";
}

function fullCalendarView(mode: CalendarViewMode): string {
  switch (mode) {
    case "week":
      return "timeGridWeek";
    case "agenda":
      return "listMonth";
    case "month":
    default:
      return "dayGridMonth";
  }
}

function inferViewMode(type: string): CalendarViewMode {
  if (type === "timeGridWeek") return "week";
  if (type === "listMonth" || type === "listWeek") return "agenda";
  return "month";
}

function presetSourceKeys(presetKey: CalendarPresetKey): CalendarSourceKey[] {
  return presets.find((preset) => preset.key === presetKey)?.source_keys ?? [];
}

function visibleSourceList(): CalendarSourceKey[] {
  return availableSources
    .map((source) => source.key)
    .filter((key) => visibleSources.has(key));
}

function selectedPlantIdList(): string[] {
  return Array.from(selectedPlantIds);
}

function selectedPlotIdList(): string[] {
  return Array.from(selectedPlotIds);
}

function selectedZoneCodeList(): string[] {
  return Array.from(selectedZoneCodes);
}

function availableCalendarPlants(): Plant[] {
  const seen = new Set<string>();
  return ctx
    .getPlants()
    .filter((plant) => {
      if (!plant.plt_id || seen.has(plant.plt_id)) return false;
      seen.add(plant.plt_id);
      return true;
    })
    .slice()
    .sort((left, right) => {
      const nameDiff = left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
      if (nameDiff !== 0) return nameDiff;
      return left.plt_id.localeCompare(right.plt_id, undefined, { sensitivity: "base" });
    });
}

function availableCalendarPlots(): Plot[] {
  const seen = new Set<string>();
  return ctx
    .getPlots()
    .filter((plot) => {
      if (!plot.plot_id || seen.has(plot.plot_id)) return false;
      seen.add(plot.plot_id);
      return true;
    })
    .slice()
    .sort((left, right) => {
      const zoneDiff = left.zone_name.localeCompare(right.zone_name, undefined, { sensitivity: "base" });
      if (zoneDiff !== 0) return zoneDiff;
      return left.plot_id.localeCompare(right.plot_id, undefined, { sensitivity: "base" });
    });
}

function availableCalendarZones(): CalendarZoneOption[] {
  const byZoneCode = new Map<string, CalendarZoneOption>();
  for (const plot of ctx.getPlots()) {
    const zoneCode = String(plot.zone_code || "").trim().toUpperCase();
    if (!zoneCode || byZoneCode.has(zoneCode)) continue;
    byZoneCode.set(zoneCode, {
      zone_code: zoneCode,
      zone_name: plot.zone_name,
    });
  }
  return Array.from(byZoneCode.values()).sort((left, right) => {
    const nameDiff = left.zone_name.localeCompare(right.zone_name, undefined, { sensitivity: "base" });
    if (nameDiff !== 0) return nameDiff;
    return left.zone_code.localeCompare(right.zone_code, undefined, { sensitivity: "base" });
  });
}

function sourceLabel(sourceKey: CalendarSourceKey): string {
  if (sourceKey === "weather_alert") return t("calendar.source_weather_alert");
  if (sourceKey === "garden_event") return t("calendar.source_garden_event");
  return t(`tasks.type_${sourceKey}`);
}

function presetLabel(presetKey: CalendarPresetKey): string {
  switch (presetKey) {
    case "all_care":
      return t("calendar.preset_all_care");
    case "watering":
      return t("calendar.preset_watering");
    case "harvest_season":
      return t("calendar.preset_harvest_season");
    case "high_value":
      return t("calendar.preset_high_value");
    case "essential":
    default:
      return t("calendar.preset_essential");
  }
}

function taskStatusLabel(status: string): string {
  switch (status) {
    case "completed":
      return t("tasks.status_completed");
    case "skipped":
      return t("tasks.status_skipped");
    case "snoozed":
      return t("tasks.status_snoozed");
    case "expired":
      return t("tasks.status_expired");
    case "pending":
    default:
      return t("tasks.status_pending");
  }
}

function windowLabel(sourceKey: CalendarSourceKey): string {
  switch (sourceKey) {
    case "prune":
      return t("calendar.window_label_prune");
    case "sow":
      return t("calendar.window_label_sow");
    case "plant_out":
      return t("calendar.window_label_plant_out");
    case "harvest":
      return t("calendar.window_label_harvest");
    default:
      return t("calendar.window_label_generic");
  }
}

function windowRangeText(event: CalendarEvent): string {
  if (!event.window_start_on || !event.window_end_on) return "";
  return t("calendar.window_range", {
    start: event.window_start_on,
    end: event.window_end_on,
  });
}

function windowSummaryText(event: CalendarEvent): string {
  if (!event.window_start_on || !event.window_end_on) return "";
  const label = windowLabel(event.source_key);
  switch (event.window_state) {
    case "active":
      return t("calendar.window_summary_active", { label });
    case "upcoming":
      return t("calendar.window_summary_upcoming", {
        label,
        start: event.window_start_on,
      });
    case "elapsed":
      return t("calendar.window_summary_elapsed", {
        label,
        end: event.window_end_on,
      });
    default:
      return t("calendar.window_summary_generic", { label });
  }
}

function secondaryEventText(event: CalendarEvent): string {
  if (event.kind === "weather_alert" && event.valid_from && event.valid_until) {
    return t("calendar.meta_valid_range", {
      start: event.valid_from,
      end: event.valid_until,
    });
  }
  if (event.window_start_on && event.window_end_on) {
    return windowSummaryText(event);
  }
  if (event.status === "snoozed" && event.snoozed_until) {
    return t("calendar.meta_snoozed_until", { date: event.snoozed_until });
  }
  if (event.status === "completed") {
    return t("calendar.meta_completed");
  }
  if (event.status === "skipped") {
    return t("calendar.meta_skipped");
  }
  if (event.due_on) {
    return t("calendar.meta_due_date", { date: event.due_on });
  }
  return "";
}

function eventRank(event: CalendarEvent): number {
  if (event.kind === "weather_alert") return 7;
  const sourceRanks: Partial<Record<CalendarSourceKey, number>> = {
    garden_event: -1,
    protect: 0,
    prune: 1,
    plant_out: 2,
    sow: 3,
    harvest: 4,
    fertilize: 5,
    inspect_issue: 6,
    observe_bloom: 7,
    deadhead: 8,
    divide: 9,
    water: 10,
  };
  const baseRank = sourceRanks[event.source_key] ?? 11;
  return (event.status === "completed" || event.status === "skipped")
    ? baseRank + 20
    : baseRank;
}

function localIsoDate(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function defaultManualEventDate(existing?: CalendarEvent): string {
  if (existing?.start_on) return existing.start_on;
  const selected = selectedEventId ? currentEventsById.get(selectedEventId) : undefined;
  if (selected?.start_on) return selected.start_on;
  const focused = calendar?.getDate();
  if (focused instanceof Date && !Number.isNaN(focused.getTime())) {
    return localIsoDate(focused);
  }
  return localIsoDate(new Date());
}

function resolveDraftPlantIds(
  existing: CalendarEvent | undefined,
  draft: CalendarManualEventDraft | undefined,
): string[] {
  return dedupeOrdered([...(existing?.plant_ids ?? []), ...(draft?.plant_ids ?? [])]);
}

function resolveDraftPlotIds(
  existing: CalendarEvent | undefined,
  draft: CalendarManualEventDraft | undefined,
): string[] {
  return dedupeOrdered([...(existing?.plot_ids ?? []), ...(draft?.plot_ids ?? [])]);
}

function buildGridEventNode(event: CalendarEvent): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "calendar-event-shell";
  if (event.window_state) {
    wrapper.classList.add("calendar-event-shell-has-window");
    wrapper.classList.add(`calendar-window-state-${event.window_state}`);
  }
  if (event.kind === "weather_alert") {
    wrapper.classList.add("calendar-event-shell-alert");
  }

  const title = document.createElement("span");
  title.className = "calendar-event-title";
  title.textContent = event.title;
  wrapper.appendChild(title);

  if (event.window_state) {
    const indicator = document.createElement("span");
    indicator.className = "calendar-event-window-indicator";
    indicator.ariaHidden = "true";
    wrapper.appendChild(indicator);
  }
  return wrapper;
}

function buildAgendaEventNode(event: CalendarEvent): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "calendar-event-agenda";
  if (event.window_state) {
    wrapper.classList.add(`calendar-window-state-${event.window_state}`);
  }

  const title = document.createElement("div");
  title.className = "calendar-event-agenda-title";
  title.textContent = event.title;
  wrapper.appendChild(title);

  const metaText = secondaryEventText(event);
  if (metaText) {
    const meta = document.createElement("div");
    meta.className = "calendar-event-agenda-meta";
    meta.textContent = metaText;
    wrapper.appendChild(meta);
  }
  return wrapper;
}

function renderCalendarEventContent(arg: EventContentArg): { domNodes: Node[] } | { text: string } {
  const event = currentEventsById.get(arg.event.id);
  if (!event) return { text: arg.event.title };
  if (inferViewMode(arg.view.type) === "agenda") {
    return { domNodes: [buildAgendaEventNode(event)] };
  }
  return { domNodes: [buildGridEventNode(event)] };
}

function handleCalendarEventMount(arg: EventMountArg): void {
  const event = currentEventsById.get(arg.event.id);
  if (!event) return;
  arg.el.dataset["calendarRenderedEvent"] = "true";
  arg.el.dataset["calendarEventId"] = event.id;
  arg.el.dataset["calendarKind"] = event.kind;
  arg.el.dataset["calendarSource"] = event.source_key;
  arg.el.dataset["calendarStatus"] = event.status;
  arg.el.dataset["calendarTitle"] = event.title;
  if (event.window_state) {
    arg.el.dataset["calendarWindowState"] = event.window_state;
  } else {
    delete arg.el.dataset["calendarWindowState"];
  }
  const tooltipLines = [event.title, secondaryEventText(event), windowRangeText(event)].filter(Boolean);
  if (tooltipLines.length > 0) {
    arg.el.title = tooltipLines.join("\n");
  }
}

function setLoading(isLoading: boolean): void {
  const loading = document.getElementById("calendar-loading");
  if (loading) loading.hidden = !isLoading;
}

function updateSummary(count: number): void {
  currentSummaryCount = count;
  const summary = document.getElementById("calendar-summary");
  if (!summary) return;
  summary.textContent = count === 0
    ? t("calendar.summary_none")
    : t("calendar.summary_count", { count });
}

function renderFilterState(): void {
  const container = document.getElementById("calendar-filter-state");
  if (!(container instanceof HTMLElement)) return;
  const parts: string[] = [];
  if (selectedPlantIds.size > 0) {
    parts.push(t("calendar.filter_state_plants", { count: selectedPlantIds.size }));
  }
  if (selectedZoneCodes.size > 0) {
    parts.push(t("calendar.filter_state_zones", { count: selectedZoneCodes.size }));
  }
  if (selectedPlotIds.size > 0) {
    parts.push(t("calendar.filter_state_plots", { count: selectedPlotIds.size }));
  }
  container.hidden = parts.length === 0;
  container.textContent = parts.length === 0
    ? ""
    : t("calendar.filter_state_summary", { filters: parts.join(" · ") });
}

function renderHeaderActions(): void {
  const newEventButton = queryButton("calendar-new-event-btn");
  if (newEventButton) {
    newEventButton.hidden = !ctx.canWrite();
  }
  const newFeedButton = queryButton("calendar-new-feed-btn");
  if (newFeedButton) {
    newFeedButton.hidden = !capabilities.can_subscribe;
  }
}

function syncViewButtons(): void {
  document
    .querySelectorAll<HTMLButtonElement>("[data-calendar-view]")
    .forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset["calendarView"] === currentViewMode,
      );
    });
}

function syncControlsFromState(): void {
  const presetSelect = querySelect("calendar-preset-select");
  if (presetSelect) presetSelect.value = currentPreset;
  const historyCheckbox = document.getElementById("calendar-recent-history");
  if (historyCheckbox instanceof HTMLInputElement) {
    historyCheckbox.checked = includeRecentHistory;
  }
  syncViewButtons();
}

function syncFilterDatasets(): void {
  const plantContainer = document.getElementById("calendar-plant-filter");
  if (plantContainer instanceof HTMLElement) {
    plantContainer.dataset["calendarSelectedPlantIds"] = JSON.stringify(selectedPlantIdList());
  }
  const plotContainer = document.getElementById("calendar-plot-filter");
  if (plotContainer instanceof HTMLElement) {
    plotContainer.dataset["calendarSelectedPlotIds"] = JSON.stringify(selectedPlotIdList());
  }
  const zoneContainer = document.getElementById("calendar-zone-filter");
  if (zoneContainer instanceof HTMLElement) {
    zoneContainer.dataset["calendarSelectedZoneCodes"] = JSON.stringify(selectedZoneCodeList());
  }
}

function syncCalendarFiltersFromInputs(): void {
  const nextPlantIds = calendarPlantInput?.getSelectedKeys() ?? [];
  const nextPlotIds = calendarPlotInput?.getSelectedKeys() ?? [];
  const nextZoneCodes = calendarZoneInput?.getSelectedKeys() ?? [];
  const nextSerialized = JSON.stringify({
    plants: nextPlantIds,
    plots: nextPlotIds,
    zones: nextZoneCodes,
  });
  const currentSerialized = JSON.stringify({
    plants: selectedPlantIdList(),
    plots: selectedPlotIdList(),
    zones: selectedZoneCodeList(),
  });
  if (nextSerialized === currentSerialized) return;
  selectedPlantIds = new Set(nextPlantIds);
  selectedPlotIds = new Set(nextPlotIds);
  selectedZoneCodes = new Set(nextZoneCodes);
  syncFilterDatasets();
  renderFilterState();
  updateSummary(currentSummaryCount);
  void persistPreferences();
  calendar?.refetchEvents();
}

function queueCalendarFilterSync(): void {
  window.requestAnimationFrame(() => {
    syncCalendarFiltersFromInputs();
  });
}

function bindFilterInputEvents(chipInput: ChipInputResult): void {
  chipInput.container.addEventListener("mousedown", queueCalendarFilterSync);
  chipInput.container.addEventListener("click", queueCalendarFilterSync);
  chipInput.container.addEventListener("input", queueCalendarFilterSync);
  chipInput.container.addEventListener("keydown", queueCalendarFilterSync);
}

function renderPlantFilter(): void {
  const container = document.getElementById("calendar-plant-filter");
  if (!(container instanceof HTMLElement)) return;
  if (calendarPlantInput) {
    calendarPlantInput.destroy();
    calendarPlantInput = null;
  }
  container.replaceChildren();
  const chipInput = createChipInput({
    label: t("calendar.plant_filter_label"),
    placeholder: t("calendar.plant_filter_placeholder"),
    items: availableCalendarPlants(),
    getKey: (plant) => plant.plt_id,
    getLabel: (plant) => `${plant.name} (${plant.plt_id})`,
    getSearchText: (plant) => `${plant.plt_id} ${plant.name}`.toLowerCase(),
    selected: selectedPlantIdList(),
  });
  chipInput.container.classList.add("calendar-plant-filter-input");
  bindFilterInputEvents(chipInput);
  container.appendChild(chipInput.container);
  calendarPlantInput = chipInput;
  syncFilterDatasets();
}

function renderPlotFilter(): void {
  const container = document.getElementById("calendar-plot-filter");
  if (!(container instanceof HTMLElement)) return;
  if (calendarPlotInput) {
    calendarPlotInput.destroy();
    calendarPlotInput = null;
  }
  container.replaceChildren();
  const chipInput = createChipInput({
    label: t("calendar.plot_filter_label"),
    placeholder: t("calendar.plot_filter_placeholder"),
    items: availableCalendarPlots(),
    getKey: (plot) => plot.plot_id,
    getLabel: (plot) => `${plot.plot_id} (${plot.zone_name})`,
    getSearchText: (plot) => `${plot.plot_id} ${plot.zone_name} ${plot.zone_code}`.toLowerCase(),
    selected: selectedPlotIdList(),
  });
  chipInput.container.classList.add("calendar-plot-filter-input");
  bindFilterInputEvents(chipInput);
  container.appendChild(chipInput.container);
  calendarPlotInput = chipInput;
  syncFilterDatasets();
}

function renderZoneFilter(): void {
  const container = document.getElementById("calendar-zone-filter");
  if (!(container instanceof HTMLElement)) return;
  if (calendarZoneInput) {
    calendarZoneInput.destroy();
    calendarZoneInput = null;
  }
  container.replaceChildren();
  const chipInput = createChipInput({
    label: t("calendar.zone_filter_label"),
    placeholder: t("calendar.zone_filter_placeholder"),
    items: availableCalendarZones(),
    getKey: (zone) => zone.zone_code,
    getLabel: (zone) => `${zone.zone_name} (${zone.zone_code})`,
    getSearchText: (zone) => `${zone.zone_code} ${zone.zone_name}`.toLowerCase(),
    selected: selectedZoneCodeList(),
  });
  chipInput.container.classList.add("calendar-zone-filter-input");
  bindFilterInputEvents(chipInput);
  container.appendChild(chipInput.container);
  calendarZoneInput = chipInput;
  syncFilterDatasets();
}

function renderPresetOptions(): void {
  const select = querySelect("calendar-preset-select");
  if (!select) return;
  const currentValue = currentPreset;
  select.replaceChildren();
  for (const preset of presets) {
    const option = document.createElement("option");
    option.value = preset.key;
    option.textContent = presetLabel(preset.key);
    select.appendChild(option);
  }
  select.value = currentValue;
}

function renderSourceFilters(): void {
  const container = document.getElementById("calendar-source-filters");
  if (!(container instanceof HTMLElement)) return;
  container.replaceChildren();
  for (const source of availableSources) {
    const label = document.createElement("label");
    label.className = "calendar-source-chip";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset["calendarSource"] = source.key;
    checkbox.checked = visibleSources.has(source.key);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        visibleSources.add(source.key);
      } else {
        visibleSources.delete(source.key);
      }
      void persistPreferences();
      calendar?.refetchEvents();
    });
    const text = document.createElement("span");
    text.textContent = sourceLabel(source.key);
    label.append(checkbox, text);
    container.appendChild(label);
  }
}

function renderRangeLabel(label: string): void {
  const rangeLabel = document.getElementById("calendar-range-label");
  if (rangeLabel) rangeLabel.textContent = label;
}

function renderSubscriptionsPanel(): void {
  const panel = document.getElementById("calendar-subscriptions-panel");
  if (!(panel instanceof HTMLElement)) return;
  panel.hidden = !capabilities.can_subscribe;
  panel.replaceChildren();
  if (!capabilities.can_subscribe) return;

  const header = document.createElement("div");
  header.className = "calendar-subscriptions-header";
  const title = document.createElement("h3");
  title.textContent = t("calendar.subscriptions_title");
  const helper = document.createElement("p");
  helper.className = "calendar-subscriptions-helper";
  helper.textContent = t("calendar.subscriptions_helper");
  header.append(title, helper);
  panel.appendChild(header);

  if (subscriptions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "calendar-subscriptions-empty";
    empty.textContent = t("calendar.subscriptions_empty");
    panel.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "calendar-subscriptions-list";
  for (const subscription of subscriptions) {
    const item = document.createElement("div");
    item.className = "calendar-subscription-item";

    const text = document.createElement("div");
    text.className = "calendar-subscription-copy";
    const label = document.createElement("strong");
    label.textContent = subscription.label;
    const meta = document.createElement("span");
    meta.textContent = t("calendar.subscription_hint", {
      token: subscription.token_hint,
    });
    text.append(label, meta);
    item.appendChild(text);

    if (subscription.can_revoke) {
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "btn btn-sm";
      revoke.textContent = t("calendar.revoke_feed");
      revoke.addEventListener("click", () => {
        void revokeSubscription(subscription);
      });
      item.appendChild(revoke);
    }
    list.appendChild(item);
  }
  panel.appendChild(list);
}

function plantLabel(pltId: string): string {
  return ctx.getPlants().find((plant) => plant.plt_id === pltId)?.name || pltId;
}

function calendarTaskForCompletion(event: CalendarEvent) {
  return {
    task_type: event.source_key as GardenTask["task_type"],
    plant_ids: event.plant_ids,
  };
}

function calendarTaskForSnooze(event: CalendarEvent): GardenTask {
  return {
    id: event.target_id,
    garden_id: 0,
    task_type: event.source_key as GardenTask["task_type"],
    title: event.title,
    description: event.description,
    status: event.status as GardenTask["status"],
    severity: event.severity as GardenTask["severity"],
    due_on: event.due_on ?? event.start_on,
    snoozed_until: event.snoozed_until ?? null,
    window_start_on: event.window_start_on ?? null,
    window_end_on: event.window_end_on ?? null,
    window_kind: null,
    rule_source: "",
    metadata: {},
    created_by_user_id: null,
    completed_by_user_id: null,
    completed_at_ms: event.completed_at_ms ?? null,
    created_at_ms: event.created_at_ms,
    updated_at_ms: event.updated_at_ms,
    plant_ids: event.plant_ids,
    plot_ids: event.plot_ids,
  };
}

function plotLabel(plotId: string): string {
  const plot = ctx.getPlots().find((candidate) => candidate.plot_id === plotId);
  return plot ? `${plot.plot_id} · ${plot.zone_name}` : plotId;
}

function detailRow(label: string, value: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "calendar-detail-row";
  const labelEl = document.createElement("span");
  labelEl.className = "calendar-detail-row-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "calendar-detail-row-value";
  valueEl.textContent = value;
  row.append(labelEl, valueEl);
  return row;
}

function actionButton(label: string, onClick: () => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-sm";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

async function openManualEventDialog(
  existing?: CalendarEvent,
  draft?: CalendarManualEventDraft,
): Promise<void> {
  if (existing && existing.kind !== "manual_event") return;
  const isEditing = existing?.kind === "manual_event";
  const headingText = isEditing ? t("calendar.manual_edit_title") : t("calendar.manual_create_title");
  const submitText = isEditing ? t("common.save") : t("common.create");
  const { dialog, close } = createModal(
    headingText,
    `
      <div class="modal-content modal-content-wide">
        <h3>${headingText}</h3>
        <form id="calendar-manual-event-form" class="calendar-manual-event-form">
          <label>
            ${t("calendar.manual_title")}
            <input type="text" name="title" maxlength="160" required />
          </label>
          <label>
            ${t("calendar.manual_date")}
            <input type="date" name="event_on" required />
          </label>
          <label>
            ${t("calendar.manual_description")}
            <textarea name="description" rows="5" maxlength="4000"></textarea>
          </label>
          <div id="calendar-manual-event-plant-input"></div>
          <div id="calendar-manual-event-plot-input"></div>
          <div class="button-row">
            <button type="submit">${submitText}</button>
            <button type="button" data-calendar-manual-cancel>${t("common.cancel")}</button>
          </div>
        </form>
      </div>
    `,
  );

  const titleInput = dialog.querySelector<HTMLInputElement>("input[name='title']");
  const dateInput = dialog.querySelector<HTMLInputElement>("input[name='event_on']");
  const descriptionInput = dialog.querySelector<HTMLTextAreaElement>("textarea[name='description']");
  const plantField = dialog.querySelector<HTMLElement>("#calendar-manual-event-plant-input");
  const plotField = dialog.querySelector<HTMLElement>("#calendar-manual-event-plot-input");
  const form = dialog.querySelector<HTMLFormElement>("#calendar-manual-event-form");
  const submitButton = form?.querySelector<HTMLButtonElement>("button[type='submit']") ?? null;

  if (titleInput) titleInput.value = existing?.title ?? draft?.title ?? "";
  if (dateInput) dateInput.value = draft?.event_on ?? defaultManualEventDate(existing);
  if (descriptionInput) descriptionInput.value = existing?.description ?? draft?.description ?? "";

  const plantInput = createChipInput({
    label: t("calendar.manual_plants"),
    placeholder: t("calendar.manual_plant_placeholder"),
    items: availableCalendarPlants(),
    getKey: (plant) => plant.plt_id,
    getLabel: (plant) => `${plant.name} (${plant.plt_id})`,
    getSearchText: (plant) => `${plant.plt_id} ${plant.name}`.toLowerCase(),
    selected: resolveDraftPlantIds(existing, draft),
  });
  plantInput.container.classList.add("calendar-manual-event-plant-input");
  plantField?.appendChild(plantInput.container);

  const plotInput = createChipInput({
    label: t("calendar.manual_plots"),
    placeholder: t("calendar.manual_plot_placeholder"),
    items: availableCalendarPlots(),
    getKey: (plot) => plot.plot_id,
    getLabel: (plot) => `${plot.plot_id} (${plot.zone_name})`,
    getSearchText: (plot) => `${plot.plot_id} ${plot.zone_name} ${plot.zone_code}`.toLowerCase(),
    selected: resolveDraftPlotIds(existing, draft),
  });
  plotInput.container.classList.add("calendar-manual-event-plot-input");
  plotField?.appendChild(plotInput.container);
  const closeDialog = () => {
    plantInput.destroy();
    plotInput.destroy();
    close();
  };

  dialog.querySelector<HTMLElement>("[data-calendar-manual-cancel]")?.addEventListener("click", closeDialog);

  form?.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    const payload: CalendarManualEventInput = {
      title: titleInput?.value.trim() ?? "",
      event_on: dateInput?.value ?? "",
      description: descriptionInput?.value.trim() ?? "",
      plant_ids: plantInput.getSelectedKeys(),
      plot_ids: plotInput.getSelectedKeys(),
    };
    if (!payload.title || !payload.event_on) {
      ctx.showToast(t("calendar.manual_missing_required"), "error");
      return;
    }
    if (submitButton) submitButton.disabled = true;
    try {
      const result = isEditing && existing
        ? await updateCalendarManualEventApi(existing.target_id, payload)
        : await createCalendarManualEventApi(payload);
      selectedEventId = result.event.id;
      closeDialog();
      ctx.showToast(
        t(isEditing ? "calendar.manual_updated" : "calendar.manual_created"),
        "success",
      );
      await loadCalendar();
    } catch (err) {
      ctx.showToast(getApiErrorMessage(err), "error");
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

async function deleteManualEvent(event: CalendarEvent): Promise<void> {
  if (event.kind !== "manual_event") return;
  const confirmed = await ctx.confirmDialog(
    t("calendar.manual_delete_confirm", { title: event.title }),
    t("common.delete"),
  );
  if (!confirmed) return;
  try {
    await deleteCalendarManualEventApi(event.target_id);
    if (selectedEventId === event.id) {
      selectedEventId = null;
    }
    ctx.showToast(t("calendar.manual_deleted"), "success");
    await loadCalendar();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function renderDetail(event?: CalendarEvent): void {
  const panel = document.getElementById("calendar-detail-panel");
  if (!(panel instanceof HTMLElement)) return;
  delete panel.dataset["calendarSelectedEventId"];
  delete panel.dataset["calendarSelectedSource"];
  delete panel.dataset["calendarSelectedStatus"];
  delete panel.dataset["calendarSelectedKind"];
  delete panel.dataset["calendarSelectedTitle"];
  panel.replaceChildren();
  if (!event) {
    const empty = document.createElement("div");
    empty.className = "calendar-detail-empty";
    empty.textContent = t("calendar.select_event");
    panel.appendChild(empty);
    return;
  }
  panel.dataset["calendarSelectedEventId"] = event.id;
  panel.dataset["calendarSelectedSource"] = event.source_key;
  panel.dataset["calendarSelectedStatus"] = event.status;
  panel.dataset["calendarSelectedKind"] = event.kind;
  panel.dataset["calendarSelectedTitle"] = event.title;

  const header = document.createElement("div");
  header.className = "calendar-detail-header";
  const title = document.createElement("h3");
  title.dataset["calendarDetailTitle"] = "true";
  title.textContent = event.title;
  const badge = document.createElement("span");
  badge.className = `calendar-detail-badge calendar-source-${event.source_key.replaceAll("_", "-")}`;
  badge.textContent = sourceLabel(event.source_key);
  header.append(title, badge);
  panel.appendChild(header);

  const meta = document.createElement("div");
  meta.className = "calendar-detail-meta";
  meta.appendChild(detailRow(t("calendar.detail_when"), event.start_on));
  if (event.kind === "task" && event.due_on) {
    meta.appendChild(detailRow(t("calendar.detail_due"), event.due_on));
    meta.appendChild(detailRow(t("calendar.detail_status"), taskStatusLabel(event.status)));
  }
  if (event.kind === "weather_alert" && event.valid_from && event.valid_until) {
    meta.appendChild(
      detailRow(
        t("calendar.detail_valid"),
        `${event.valid_from} → ${event.valid_until}`,
      ),
    );
  }
  panel.appendChild(meta);

  if (event.description.trim()) {
    const description = document.createElement("p");
    description.className = "calendar-detail-description";
    description.textContent = event.description;
    panel.appendChild(description);
  }

  if (event.window_start_on && event.window_end_on) {
    const hint = document.createElement("div");
    hint.className = "calendar-detail-window";
    const summary = document.createElement("strong");
    summary.dataset["calendarDetailWindowSummary"] = "true";
    summary.textContent = windowSummaryText(event);
    const range = document.createElement("span");
    range.dataset["calendarDetailWindowRange"] = "true";
    range.textContent = windowRangeText(event);
    hint.append(summary, range);
    panel.appendChild(hint);
  }

  if (event.plant_ids.length > 0) {
    const section = document.createElement("div");
    section.className = "calendar-detail-section";
    const heading = document.createElement("strong");
    heading.textContent = t("calendar.detail_plants");
    section.appendChild(heading);
    const list = document.createElement("div");
    list.className = "calendar-detail-tags";
    for (const pltId of event.plant_ids) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "calendar-link-chip";
      button.textContent = plantLabel(pltId);
      button.addEventListener("click", () => {
        ctx.navigateToSubMode("plants");
        ctx.focusPlantsInPlantsView([pltId]);
      });
      list.appendChild(button);
    }
    section.appendChild(list);
    panel.appendChild(section);
  }

  if (event.plot_ids.length > 0) {
    const section = document.createElement("div");
    section.className = "calendar-detail-section";
    const heading = document.createElement("strong");
    heading.textContent = t("calendar.detail_plots");
    section.appendChild(heading);
    const list = document.createElement("div");
    list.className = "calendar-detail-tags";
    for (const plotId of event.plot_ids) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "calendar-link-chip";
      button.textContent = plotLabel(plotId);
      button.addEventListener("click", () => {
        ctx.setActiveTab("map");
        void ctx.selectPlot(plotId);
      });
      list.appendChild(button);
    }
    section.appendChild(list);
    panel.appendChild(section);
  }

  if (event.kind === "manual_event" && ctx.canWrite()) {
    const actions = document.createElement("div");
    actions.className = "calendar-detail-actions";
    const editButton = actionButton(t("common.edit"), () => {
      void openManualEventDialog(event);
    });
    editButton.dataset["calendarDetailEditManualEvent"] = "true";
    const deleteButton = actionButton(t("common.delete"), () => {
      void deleteManualEvent(event);
    });
    deleteButton.dataset["calendarDetailDeleteManualEvent"] = "true";
    actions.append(editButton, deleteButton);
    panel.appendChild(actions);
  }

  if (event.kind === "task" && ctx.canWrite() && (event.status === "pending" || event.status === "snoozed")) {
    const actions = document.createElement("div");
    actions.className = "calendar-detail-actions";
    actions.appendChild(
      actionButton(t("tasks.action_complete"), () => {
        completeCalendarTask(event);
      }),
    );
    actions.appendChild(
      actionButton(t("tasks.action_skip"), () => {
        void runTaskAction(event, { action: "skip" });
      }),
    );
    actions.appendChild(
      actionButton(t("tasks.action_snooze"), () => {
        void snoozeCalendarTask(event);
      }),
    );
    actions.appendChild(
      actionButton(t("tasks.action_reschedule"), () => {
        void promptTaskAction(event, "reschedule");
      }),
    );
    panel.appendChild(actions);
  }
}

async function runTaskAction(
  event: CalendarEvent,
  body: TaskActionRequest,
  options: { showSuccessToast?: boolean } = {},
): Promise<boolean> {
  if (!ctx.isOnline()) {
    if (body.action === "complete" && body.completed_plant_ids?.length) {
      ctx.showToast(t("tasks.complete_grouped_one_by_one"), "error");
      return false;
    }
    await enqueueOfflineCalendarTaskAction(event.target_id, body);
    ctx.showToast(t("offline.draft_saved"), "success");
    void ctx.refreshOfflineIndicator();
    return true;
  }
  try {
    await taskActionApi(event.target_id, body);
    if (options.showSuccessToast !== false) {
      ctx.showToast(t("tasks.action_success", { action: body.action }), "success");
    }
    void ctx.refreshBadgeCounts();
    await loadCalendar();
    return true;
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
    return false;
  }
}

async function enqueueOfflineCalendarTaskAction(
  taskId: string,
  body: TaskActionRequest,
): Promise<void> {
  if (body.action === "complete") {
    await ctx.enqueueDraft("task_complete", { task_id: taskId });
    return;
  }
  if (body.action === "skip") {
    await ctx.enqueueDraft("task_skip", { task_id: taskId });
    return;
  }
  if (body.action === "snooze") {
    await ctx.enqueueDraft("task_snooze", {
      task_id: taskId,
      snooze_until: body.snooze_until,
    });
    return;
  }
  if (body.action === "reschedule") {
    await ctx.enqueueDraft("task_reschedule", {
      task_id: taskId,
      reschedule_to: body.reschedule_to,
    });
    return;
  }
  throw new Error(`Unsupported calendar task action: ${body.action}`);
}

function completeCalendarTask(event: CalendarEvent): void {
  const task = calendarTaskForCompletion(event);
  if (!needsCompletionDialog(task)) {
    void runTaskAction(event, { action: "complete" });
    return;
  }
  if (!ctx.isOnline()) {
    if (canQueueDefaultCompletionOffline(task)) {
      void runTaskAction(event, { action: "complete" });
      return;
    }
    ctx.showToast(t("tasks.complete_grouped_one_by_one"), "error");
    return;
  }
  const plantNames = new Map(ctx.getPlants().map((plant) => [plant.plt_id, plant.name]));
  openTaskCompletionDialog(task, plantNames, (body) => {
    void runTaskAction(event, body);
  });
}

async function snoozeCalendarTask(event: CalendarEvent): Promise<void> {
  const task = calendarTaskForSnooze(event);
  const policy = taskSnoozePolicy(task);
  if (!policy.immediate) {
    await promptTaskAction(event, "snooze", policy.defaultDate, policy.warning);
    return;
  }
  const online = ctx.isOnline();
  const ok = await runTaskAction(
    event,
    { action: "snooze", snooze_until: policy.defaultDate },
    { showSuccessToast: false },
  );
  if (!ok || !online) return;
  ctx.showToast(
    t("tasks.snoozed_until_toast", { date: policy.defaultDate }),
    "success",
    {
      actions: [
        {
          label: t("tasks.snooze_change_date"),
          onClick: () => {
            void promptTaskAction(event, "snooze", policy.defaultDate);
          },
        },
      ],
      durationMs: 5000,
    },
  );
}

async function promptTaskAction(
  event: CalendarEvent,
  action: "snooze" | "reschedule",
  defaultDate = event.due_on || event.start_on,
  warning?: string,
): Promise<void> {
  const promptText = action === "snooze" ? t("tasks.snooze_prompt") : t("tasks.reschedule_prompt");
  const value = window.prompt(warning ? `${warning}\n\n${promptText}` : promptText, defaultDate);
  if (!value) return;
  const body: TaskActionRequest = action === "snooze"
    ? { action: "snooze", snooze_until: value }
    : { action: "reschedule", reschedule_to: value };
  await runTaskAction(event, body);
}

async function persistPreferences(): Promise<void> {
  if (!preferencesLoaded) return;
  try {
    await updateCalendarPreferencesApi({
      default_view: currentViewMode,
      selected_preset: currentPreset,
      visible_sources: visibleSourceList(),
      include_recent_history: includeRecentHistory,
      selected_plant_ids: selectedPlantIdList(),
      selected_plot_ids: selectedPlotIdList(),
      selected_zone_codes: selectedZoneCodeList(),
    });
  } catch {
    // Non-critical. Keep local state responsive even if persistence fails.
  }
}

async function fetchPreferences(): Promise<void> {
  const result = await fetchCalendarPreferencesApi();
  availableSources = result.available_sources;
  presets = result.presets;
  capabilities = result.capabilities;
  currentViewMode = result.persisted ? result.preferences.default_view : defaultResponsiveView();
  currentPreset = result.preferences.selected_preset;
  visibleSources = new Set(result.preferences.visible_sources);
  includeRecentHistory = result.preferences.include_recent_history;
  selectedPlantIds = new Set(result.preferences.selected_plant_ids || []);
  selectedPlotIds = new Set(result.preferences.selected_plot_ids || []);
  selectedZoneCodes = new Set(result.preferences.selected_zone_codes || []);
  preferencesLoaded = true;
  renderPresetOptions();
  syncControlsFromState();
  renderZoneFilter();
  renderPlotFilter();
  renderPlantFilter();
  renderFilterState();
  renderSourceFilters();
  renderHeaderActions();
  renderSubscriptionsPanel();
}

async function refreshSubscriptions(): Promise<void> {
  if (!capabilities.can_subscribe) {
    subscriptions = [];
    renderSubscriptionsPanel();
    return;
  }
  try {
    const result = await listCalendarSubscriptionsApi();
    subscriptions = result.subscriptions;
    renderSubscriptionsPanel();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function revokeSubscription(subscription: CalendarSubscription): Promise<void> {
  const confirmed = await ctx.confirmDialog(
    t("calendar.revoke_confirm", { label: subscription.label }),
    t("calendar.revoke_feed"),
  );
  if (!confirmed) return;
  try {
    await deleteCalendarSubscriptionApi(subscription.id);
    ctx.showToast(t("calendar.feed_revoked"), "success");
    await refreshSubscriptions();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function createSubscription(): Promise<void> {
  const label = window.prompt(t("calendar.feed_label_prompt"), "");
  if (label === null) return;
  try {
    const payload: {
      label?: string;
      preset_key: CalendarPresetKey;
      visible_sources: CalendarSourceKey[];
    } = {
      preset_key: currentPreset,
      visible_sources: visibleSourceList(),
    };
    const trimmedLabel = label.trim();
    if (trimmedLabel) {
      payload.label = trimmedLabel;
    }
    const result = await createCalendarSubscriptionApi(payload);
    const feedUrl = new URL(result.feed_path, window.location.origin).toString();
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(feedUrl);
      ctx.showToast(t("calendar.feed_copied"), "success");
    } else {
      window.prompt(t("calendar.feed_copy_prompt"), feedUrl);
    }
    await refreshSubscriptions();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function exportCalendar(): void {
  if (!calendar) return;
  const view = calendar.view;
  const start = view.activeStart.toISOString().slice(0, 10);
  const end = view.activeEnd.toISOString().slice(0, 10);
  window.location.assign(
    buildCalendarExportUrl({
      start,
      end,
      preset: currentPreset,
      visible_sources: visibleSourceList().join(","),
      include_recent_history: includeRecentHistory,
      selected_plant_ids: selectedPlantIdList().join(","),
      selected_plot_ids: selectedPlotIdList().join(","),
      selected_zone_codes: selectedZoneCodeList().join(","),
    }),
  );
}

function ensureCalendarInstance(): Calendar {
  const root = document.getElementById("calendar-root");
  if (!(root instanceof HTMLElement)) {
    throw new Error("Missing calendar root");
  }
  if (calendar) return calendar;
  const eventOrderComparator = ((left: { id: string; title: string }, right: { id: string; title: string }) => {
    const leftEvent = currentEventsById.get(left.id);
    const rightEvent = currentEventsById.get(right.id);
    if (leftEvent && rightEvent) {
      const diff = eventRank(leftEvent) - eventRank(rightEvent);
      if (diff !== 0) return diff;
    }
    return left.title.localeCompare(right.title);
  }) as unknown as string;
  calendar = new Calendar(root, {
    plugins: [dayGridPlugin, timeGridPlugin, listPlugin, interactionPlugin],
    initialView: fullCalendarView(currentViewMode),
    locale: currentLocaleObject(),
    locales: [enGbLocale, nbLocale],
    firstDay: 1,
    headerToolbar: false,
    height: "auto",
    dayMaxEventRows: window.matchMedia("(max-width: 760px)").matches ? 3 : 4,
    eventMaxStack: window.matchMedia("(max-width: 760px)").matches ? 2 : 3,
    displayEventTime: false,
    eventOrder: eventOrderComparator,
    eventContent: renderCalendarEventContent,
    eventDidMount: handleCalendarEventMount,
    events: async (fetchInfo, successCallback, failureCallback) => {
      try {
        setLoading(true);
        const result = await fetchCalendarEventsApi({
          start: fetchInfo.startStr.slice(0, 10),
          end: fetchInfo.endStr.slice(0, 10),
          preset: currentPreset,
          visible_sources: visibleSourceList().join(","),
          include_recent_history: includeRecentHistory,
          selected_plant_ids: selectedPlantIdList().join(","),
          selected_plot_ids: selectedPlotIdList().join(","),
          selected_zone_codes: selectedZoneCodeList().join(","),
        });
        const nextSelectedPlantIds = new Set(result.selected_plant_ids || []);
        const nextSelectedPlotIds = new Set(result.selected_plot_ids || []);
        const nextSelectedZoneCodes = new Set(result.selected_zone_codes || []);
        const selectionChanged = JSON.stringify({
          plants: Array.from(nextSelectedPlantIds),
          plots: Array.from(nextSelectedPlotIds),
          zones: Array.from(nextSelectedZoneCodes),
        }) !== JSON.stringify({
          plants: selectedPlantIdList(),
          plots: selectedPlotIdList(),
          zones: selectedZoneCodeList(),
        });
        selectedPlantIds = nextSelectedPlantIds;
        selectedPlotIds = nextSelectedPlotIds;
        selectedZoneCodes = nextSelectedZoneCodes;
        syncFilterDatasets();
        if (selectionChanged) {
          renderZoneFilter();
          renderPlotFilter();
          renderPlantFilter();
        }
        renderFilterState();
        currentEventsById = new Map(result.events.map((event) => [event.id, event]));
        updateSummary(result.events.length);
        if (selectedEventId && !currentEventsById.has(selectedEventId)) {
          selectedEventId = null;
        }
        renderDetail(selectedEventId ? currentEventsById.get(selectedEventId) : undefined);
        successCallback(
          result.events.map((event) => ({
            id: event.id,
            title: event.title,
            start: event.start_on,
            end: event.end_on,
            allDay: event.all_day,
            classNames: [
              "calendar-event",
              `calendar-kind-${event.kind}`,
              `calendar-source-${event.source_key.replaceAll("_", "-")}`,
              `calendar-status-${event.status.replaceAll("_", "-")}`,
              event.window_state ? `calendar-window-state-${event.window_state}` : "",
              event.window_start_on && event.window_end_on ? "calendar-has-window" : "",
            ],
            extendedProps: {
              sourceKey: event.source_key,
              status: event.status,
              windowState: event.window_state || "",
            },
          })),
        );
      } catch (err) {
        failureCallback(err as Error);
        ctx.showToast(getApiErrorMessage(err), "error");
      } finally {
        setLoading(false);
      }
    },
    datesSet: (arg) => {
      currentViewMode = inferViewMode(arg.view.type);
      syncViewButtons();
      renderRangeLabel(arg.view.title);
    },
    eventClick: (arg) => {
      selectedEventId = arg.event.id;
      renderDetail(currentEventsById.get(arg.event.id));
    },
    noEventsContent: () => t("calendar.no_events"),
  });
  calendar.render();
  return calendar;
}

async function changeView(mode: CalendarViewMode): Promise<void> {
  currentViewMode = mode;
  syncViewButtons();
  const instance = ensureCalendarInstance();
  await nextAnimationFrame();
  instance.render();
  instance.changeView(fullCalendarView(mode));
  instance.updateSize();
  await persistPreferences();
}

export function applyCalendarSavedView(filters: Record<string, unknown>): void {
  if (typeof filters["preset_key"] === "string") {
    currentPreset = filters["preset_key"] as CalendarPresetKey;
  }
  if (Array.isArray(filters["visible_sources"])) {
    visibleSources = new Set(
      filters["visible_sources"].map((value) => String(value) as CalendarSourceKey),
    );
  } else {
    visibleSources = new Set(presetSourceKeys(currentPreset));
  }
  if (typeof filters["include_recent_history"] === "boolean") {
    includeRecentHistory = Boolean(filters["include_recent_history"]);
  }
  if (Array.isArray(filters["selected_plant_ids"])) {
    selectedPlantIds = new Set(filters["selected_plant_ids"].map((value) => String(value)));
  } else {
    selectedPlantIds = new Set();
  }
  if (Array.isArray(filters["selected_plot_ids"])) {
    selectedPlotIds = new Set(filters["selected_plot_ids"].map((value) => String(value)));
  } else {
    selectedPlotIds = new Set();
  }
  if (Array.isArray(filters["selected_zone_codes"])) {
    selectedZoneCodes = new Set(filters["selected_zone_codes"].map((value) => String(value)));
  } else {
    selectedZoneCodes = new Set();
  }
  if (typeof filters["view_mode"] === "string") {
    currentViewMode = filters["view_mode"] as CalendarViewMode;
  }
  renderPresetOptions();
  syncControlsFromState();
  renderZoneFilter();
  renderPlotFilter();
  renderPlantFilter();
  renderFilterState();
  renderSourceFilters();
  if (calendar) {
    calendar.changeView(fullCalendarView(currentViewMode));
    calendar.refetchEvents();
  }
  void persistPreferences();
}

export function refreshCalendarLocalization(): void {
  renderPresetOptions();
  renderZoneFilter();
  renderPlotFilter();
  renderPlantFilter();
  renderFilterState();
  renderSourceFilters();
  renderHeaderActions();
  renderSubscriptionsPanel();
  updateSummary(currentSummaryCount);
  renderDetail(selectedEventId ? currentEventsById.get(selectedEventId) : undefined);
  if (calendar) {
    calendar.setOption("locale", currentLocaleObject());
  }
}

export function initCalendarTab(appCtx: AppContext): void {
  ctx = appCtx;
  if (initBound) return;
  initBound = true;

  document
    .querySelectorAll<HTMLButtonElement>("[data-calendar-view]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const nextView = (button.dataset["calendarView"] || "month") as CalendarViewMode;
        void changeView(nextView);
      });
    });

  querySelect("calendar-preset-select")?.addEventListener("change", () => {
    currentPreset = (querySelect("calendar-preset-select")?.value || "essential") as CalendarPresetKey;
    visibleSources = new Set(presetSourceKeys(currentPreset));
    renderSourceFilters();
    syncControlsFromState();
    void persistPreferences();
    calendar?.refetchEvents();
  });

  const historyCheckbox = document.getElementById("calendar-recent-history");
  historyCheckbox?.addEventListener("change", () => {
    includeRecentHistory = historyCheckbox instanceof HTMLInputElement
      ? historyCheckbox.checked
      : includeRecentHistory;
    void persistPreferences();
    calendar?.refetchEvents();
  });

  queryButton("calendar-prev-btn")?.addEventListener("click", () => {
    calendar?.prev();
  });
  queryButton("calendar-today-btn")?.addEventListener("click", () => {
    calendar?.today();
  });
  queryButton("calendar-next-btn")?.addEventListener("click", () => {
    calendar?.next();
  });
  queryButton("calendar-export-btn")?.addEventListener("click", exportCalendar);
  queryButton("calendar-new-event-btn")?.addEventListener("click", () => {
    void openManualEventDialog();
  });
  queryButton("calendar-new-feed-btn")?.addEventListener("click", () => {
    void createSubscription();
  });
}

export function openCalendarManualEventComposer(
  draft?: CalendarManualEventDraft,
): void {
  void openManualEventDialog(undefined, draft);
}

export async function loadCalendar(): Promise<void> {
  try {
    if (!preferencesLoaded) {
      await fetchPreferences();
    } else {
      renderPresetOptions();
      syncControlsFromState();
      renderZoneFilter();
      renderPlotFilter();
      renderPlantFilter();
      renderFilterState();
      renderSourceFilters();
    }
    renderHeaderActions();
    const instance = ensureCalendarInstance();
    await nextAnimationFrame();
    instance.render();
    instance.changeView(fullCalendarView(currentViewMode));
    instance.updateSize();
    instance.refetchEvents();
    await refreshSubscriptions();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}
