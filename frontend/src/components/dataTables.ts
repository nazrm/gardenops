import type { Plant, PlantPresenceStatus } from "../core/models";
import { formatPlantCategoryLabel, getLocaleTag, t } from "../core/i18n";
import { renderEmptyState } from "./emptyState";
import {
  formatPlotAssignmentMeaning,
  resolvePlotAssignmentMeaning,
} from "../core/plotAssignmentMeanings";
import { sanitizeUrl } from "../core/sanitize";
import type { MediaAsset, PlotAssignmentMeaning } from "../services/api";
import { createLazyMediaThumbnailButton } from "./mediaGalleryLoader";

export interface ColumnDef {
  key: string;
  label: string;
}

type PresenceFilter = "all" | "current" | "gone" | "unobserved";

function plantPresenceStatus(plant: Plant): PlantPresenceStatus {
  return plant.presence_status ?? "present";
}

function plantObservedThisYear(plant: Plant): boolean {
  return Boolean(
    plant.observed_this_year
    || plant.seen_growing_is_current_year
    || plant.bloomed_this_year,
  );
}

function plantPresenceRank(plant: Plant): number {
  const status = plantPresenceStatus(plant);
  if (status === "mixed") return 1;
  if (status === "gone") return 2;
  return 0;
}

function matchesPresenceFilter(
  plant: Plant,
  presenceFilter: PresenceFilter,
): boolean {
  const status = plantPresenceStatus(plant);
  if (presenceFilter === "current") return status !== "gone";
  if (presenceFilter === "gone") return status === "gone";
  if (presenceFilter === "unobserved") return !plantObservedThisYear(plant);
  return true;
}

function plantPresenceBadgeText(plant: Plant): string | null {
  const status = plantPresenceStatus(plant);
  if (status === "gone") {
    return plant.last_not_seen_year
      ? t("plants.status_gone_since", { year: plant.last_not_seen_year })
      : t("plants.plot_not_seen");
  }
  if (status === "mixed") {
    return t("plants.status_mixed");
  }
  if (!plantObservedThisYear(plant)) {
    return t("plants.status_unobserved_this_season");
  }
  return null;
}

function plantPresenceSearchHaystack(plant: Plant): string {
  const status = plantPresenceStatus(plant);
  if (status === "gone") {
    return [
      t("plants.plot_not_seen"),
      t("plants.presence_filter_gone"),
      "gone",
      "not seen",
      "ikke sett",
      plant.last_not_seen_year ?? "",
    ].join(" ");
  }
  if (status === "mixed") {
    return [
      t("plants.status_mixed"),
      "mixed",
      "partly not seen",
      "delvis ikke sett",
      plant.last_not_seen_year ?? "",
    ].join(" ");
  }
  if (!plantObservedThisYear(plant)) {
    return [
      t("plants.status_unobserved_this_season"),
      t("plants.presence_filter_unobserved"),
      "unobserved this season",
      "not observed this season",
      "ikke observert denne sesongen",
      "ikke observert i år",
    ].join(" ");
  }
  return "";
}

function createPresenceBadge(
  plant: Plant,
  extraClass = "",
): HTMLElement | null {
  const label = plantPresenceBadgeText(plant);
  if (!label) return null;
  const badge = document.createElement("span");
  const status = plantPresenceStatus(plant);
  const badgeKind = status === "present" && !plantObservedThisYear(plant)
    ? "unobserved"
    : status;
  badge.className = [
    "plants-presence-badge",
    `plants-presence-badge--${badgeKind}`,
    extraClass,
  ].filter(Boolean).join(" ");
  badge.textContent = label;
  return badge;
}

export function filterPlants(
  plants: Plant[],
  query: string,
  category: string,
  presenceFilter: PresenceFilter = "all",
): Plant[] {
  const q = query.trim().toLowerCase();
  return plants.filter((plant) => {
    const matchCategory = !category || plant.category === category;
    const matchPresence = matchesPresenceFilter(plant, presenceFilter);
    if (!matchCategory || !matchPresence) return false;
    if (!q) return true;
    const haystack =
      `${plant.plt_id} ${plant.name} ${plant.latin || ""} ${plant.hardiness || ""} ${plantPresenceSearchHaystack(plant)}`
        .toLowerCase();
    return haystack.includes(q);
  });
}

interface PlantsTableCallbacks {
  onOpenPlot: (plotId: string) => void;
  onEdit: (plant: Plant) => void;
  knownPlotIds: Set<string>;
  plotAssignmentMeanings: PlotAssignmentMeaning[];
  mediaPreviewByPlantId?: ReadonlyMap<string, MediaAsset | null>;
  onToggleSelect?: (pltId: string) => void;
  selectedIds?: Set<string>;
}

function createMobileFact(label: string, value: string): HTMLElement | null {
  if (!value) return null;
  const fact = document.createElement("div");
  fact.className = "mobile-data-fact";

  const labelEl = document.createElement("span");
  labelEl.className = "mobile-data-label";
  labelEl.textContent = label;

  const valueEl = document.createElement("span");
  valueEl.className = "mobile-data-value";
  valueEl.textContent = value;

  fact.append(labelEl, valueEl);
  return fact;
}

function appendPlotLinks(
  container: HTMLElement,
  plant: Plant,
  onOpenPlot: (plotId: string) => void,
  knownPlotIds: Set<string>,
  plotAssignmentMeanings: PlotAssignmentMeaning[],
): void {
  container.replaceChildren();
  const ids = plant.plot_ids;
  if (!ids || ids.length === 0) {
    const empty = document.createElement("span");
    empty.className = "text-muted";
    empty.textContent = "\u2014";
    container.appendChild(empty);
    return;
  }

  const missingIds = new Set(
    (plant.missing_plot_ids ?? []).filter((plotId) => !knownPlotIds.has(plotId)),
  );
  ids.forEach((id, index) => {
    if (index > 0) container.append(document.createTextNode(" "));
    const meaning = resolvePlotAssignmentMeaning(id, plotAssignmentMeanings);
    const meaningText = formatPlotAssignmentMeaning(meaning);
    if (missingIds.has(id)) {
      const missing = document.createElement("span");
      missing.className = "plot-link-token";
      const missingId = document.createElement("span");
      missingId.className = "plot-link plot-link--missing";
      missingId.title = meaningText
        ? `${t("plants.missing_plot")}. ${meaningText}`
        : t("plants.missing_plot");
      missingId.textContent = id;
      missing.appendChild(missingId);
      if (meaningText) {
        const note = document.createElement("span");
        note.className = "plot-link-note";
        note.textContent = meaningText;
        missing.appendChild(note);
      }
      container.appendChild(missing);
      return;
    }
    const token = document.createElement("span");
    token.className = "plot-link-token";
    const button = document.createElement("button");
    button.className = "text-link plot-link";
    button.dataset["gotoPlot"] = id;
    button.type = "button";
    button.textContent = id;
    if (meaningText) button.title = meaningText;
    button.addEventListener("click", () => onOpenPlot(id));
    token.appendChild(button);
    container.appendChild(token);
  });
}

const CATEGORY_EMOJI: Record<string, string> = {
  "løk": "\uD83E\uDDC5",
  "frø": "\uD83C\uDF31",
  "busker": "\uD83C\uDF3F",
  "baerbusker": "\uD83C\uDF53",
  "trær": "\uD83C\uDF33",
};

function appendCellContent(
  cell: HTMLElement,
  plant: Plant,
  key: string,
  onOpenPlot: (plotId: string) => void,
  knownPlotIds: Set<string>,
  plotAssignmentMeanings: PlotAssignmentMeaning[],
  mediaPreviewByPlantId?: ReadonlyMap<string, MediaAsset | null>,
): void {
  switch (key) {
    case "name": {
      const previewAsset = mediaPreviewByPlantId?.get(plant.plt_id) ?? null;
      const wrap = document.createElement("div");
      wrap.className = "plants-name-cell";
      if (previewAsset) {
        wrap.appendChild(createLazyMediaThumbnailButton(previewAsset, {
          className: "plants-name-thumb",
          imageClassName: "plants-name-thumb-image",
          label: previewAsset.original_filename || `${plant.name} ${t("media.latest_photo")}`,
        }));
      }
      const textWrap = document.createElement("span");
      textWrap.className = "plants-name-text";
      const nameLine = document.createElement("span");
      nameLine.className = "plants-name-title";
      const emoji = CATEGORY_EMOJI[plant.category] ?? "";
      if (emoji) {
        const emojiSpan = document.createElement("span");
        emojiSpan.className = "cat-emoji";
        emojiSpan.title = formatPlantCategoryLabel(plant.category);
        emojiSpan.textContent = emoji;
        nameLine.append(emojiSpan, document.createTextNode(" "));
      }
      nameLine.append(document.createTextNode(plant.name));
      textWrap.appendChild(nameLine);
      const badge = createPresenceBadge(plant);
      if (badge) textWrap.appendChild(badge);
      wrap.appendChild(textWrap);
      cell.appendChild(wrap);
      return;
    }
    case "plot_ids":
      appendPlotLinks(cell, plant, onOpenPlot, knownPlotIds, plotAssignmentMeanings);
      return;
    case "link": {
      const safeLink = sanitizeUrl(plant.link ?? "");
      if (!safeLink) return;
      const link = document.createElement("a");
      link.href = safeLink;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = plant.link ?? "";
      link.textContent = "\u2197";
      cell.appendChild(link);
      return;
    }
    case "bloom_month":
      cell.textContent = formatBloomMonth(plant.bloom_month);
      return;
    case "deer_resistant":
      if (plant.deer_resistant) {
        cell.textContent = "\uD83E\uDD8C";
      }
      return;
    case "height_cm":
      cell.textContent = plant.height_cm != null ? String(plant.height_cm) : "";
      return;
    case "year_planted":
      cell.textContent = plant.year_planted ?? "";
      return;
    default: {
      const val = plant[key as keyof Plant];
      cell.textContent = typeof val === "string" ? val : "";
    }
  }
}

function cellClass(key: string): string {
  if (key === "name") return "col-name";
  if (key === "latin") return "col-latin";
  if (key === "plot_ids") return "plot-links-cell";
  if (key === "link") return "col-link";
  return "col-sm";
}

export type SortField =
  | "name" | "latin" | "bloom_month" | "color"
  | "hardiness" | "height_cm" | "light" | "year_planted"
  | "deer_resistant" | "plot_ids";

export type SortDir = "asc" | "desc";

export function sortPlants(
  plants: Plant[],
  field: SortField,
  dir: SortDir,
): Plant[] {
  const sorted = [...plants];
  const mul = dir === "asc" ? 1 : -1;

  sorted.sort((a, b) => {
    const presenceDelta = plantPresenceRank(a) - plantPresenceRank(b);
    if (presenceDelta !== 0) return presenceDelta;

    if (field === "plot_ids") {
      const la = a.plot_ids?.length ?? 0;
      const lb = b.plot_ids?.length ?? 0;
      const delta = (la - lb) * mul;
      if (delta !== 0) return delta;
      return a.name.localeCompare(b.name);
    }
    if (field === "height_cm") {
      const va = a[field] ?? 0;
      const vb = b[field] ?? 0;
      const delta = (va - vb) * mul;
      if (delta !== 0) return delta;
      return a.name.localeCompare(b.name);
    }
    if (field === "deer_resistant") {
      const delta = ((a[field] ? 1 : 0) - (b[field] ? 1 : 0)) * mul;
      if (delta !== 0) return delta;
      return a.name.localeCompare(b.name);
    }
    const sa = (a[field] ?? "").toLowerCase();
    const sb = (b[field] ?? "").toLowerCase();
    const delta = sa.localeCompare(sb) * mul;
    if (delta !== 0) return delta;
    return a.name.localeCompare(b.name);
  });
  return sorted;
}

export function renderPlantsTableHead(
  thead: HTMLElement,
  columns: ColumnDef[],
  visibleColumns: Set<string>,
  onSelectAll?: () => void,
): void {
  const row = document.createElement("tr");
  if (onSelectAll) {
    const selectTh = document.createElement("th");
    selectTh.className = "col-select";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.setAttribute("aria-label", "Select all");
    cb.addEventListener("change", () => onSelectAll());
    selectTh.appendChild(cb);
    row.appendChild(selectTh);
  }
  columns.forEach((col) => {
    const th = document.createElement("th");
    th.className = `sortable ${cellClass(col.key)}`;
    th.dataset["sort"] = col.key;
    th.dataset["col"] = col.key;
    if (!visibleColumns.has(col.key)) {
      th.style.display = "none";
    }
    th.textContent = col.label;
    row.appendChild(th);
  });
  const action = document.createElement("th");
  action.className = "col-action";
  row.appendChild(action);
  thead.replaceChildren(row);
}

type BloomStatus = "" | "blooming-now" | "bloom-past";

const MONTH_NAMES: Record<string, number> = {
  jan: 1, januar: 1,
  feb: 2, februar: 2,
  mar: 3, mars: 3,
  apr: 4, april: 4,
  mai: 5, may: 5,
  jun: 6, juni: 6,
  jul: 7, juli: 7,
  aug: 8, august: 8,
  sep: 9, september: 9,
  okt: 10, oktober: 10, oct: 10,
  nov: 11, november: 11,
  des: 12, desember: 12, dec: 12,
};

export function parseMonth(s: string): number {
  const trimmed = s.trim().toLowerCase();
  const num = Number(trimmed);
  if (num >= 1 && num <= 12) return num;
  return MONTH_NAMES[trimmed] ?? 0;
}

function monthNumberToName(m: number): string {
  const date = new Date(Date.UTC(2026, m - 1, 1));
  const name = new Intl.DateTimeFormat(getLocaleTag(), {
    month: "long",
    timeZone: "UTC",
  }).format(date);
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function formatBloomMonth(raw: string): string {
  if (!raw) return "";
  const parts = raw.split(/[-–]/).map((s) => s.trim()).filter(Boolean);
  const formatted = parts.map((part) => {
    const num = parseMonth(part);
    return num >= 1 && num <= 12 ? monthNumberToName(num) : part;
  });
  return formatted.join("\u2013");
}

function bloomStatus(bloomMonth: string): BloomStatus {
  if (!bloomMonth) return "";
  const now = new Date().getMonth() + 1;
  const months = bloomMonth.split(/[-–,]/).map(parseMonth).filter(Boolean);
  if (months.length === 0) return "";
  if (months.length === 2 && months[0]! <= months[1]!) {
    if (now >= months[0]! && now <= months[1]!) return "blooming-now";
    if (months[1]! < now) return "bloom-past";
    return "";
  }
  if (months.some((m) => m === now)) return "blooming-now";
  if (months.every((m) => m < now)) return "bloom-past";
  return "";
}

export function renderPlantsTableBody(
  tbody: HTMLElement,
  plants: Plant[],
  columns: ColumnDef[],
  visibleColumns: Set<string>,
  callbacks: PlantsTableCallbacks,
): void {
  const {
    onOpenPlot, onEdit, knownPlotIds, plotAssignmentMeanings, mediaPreviewByPlantId,
    onToggleSelect, selectedIds,
  } = callbacks;
  const totalCols = columns.length + 1 + (onToggleSelect ? 1 : 0);

  if (plants.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = totalCols;
    cell.className = "empty-table";
    cell.textContent = t("plants.no_matches");
    row.appendChild(cell);
    tbody.replaceChildren(row);
    return;
  }

  const rows = plants.map((plant) => {
    const row = document.createElement("tr");
    row.dataset["pltId"] = plant.plt_id;
    const bs = bloomStatus(plant.bloom_month);
    if (bs) row.classList.add(bs);
    const presenceStatus = plantPresenceStatus(plant);
    if (presenceStatus === "gone") row.classList.add("plant-gone");
    if (presenceStatus === "mixed") row.classList.add("plant-mixed");
    if (onToggleSelect && selectedIds?.has(plant.plt_id)) {
      row.classList.add("batch-selected");
    }

    if (onToggleSelect) {
      const selectCell = document.createElement("td");
      selectCell.className = "col-select";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedIds?.has(plant.plt_id) ?? false;
      cb.setAttribute("aria-label", `Select ${plant.name}`);
      cb.addEventListener("change", () => onToggleSelect(plant.plt_id));
      selectCell.appendChild(cb);
      row.appendChild(selectCell);
    }

    columns.forEach((col) => {
      const cell = document.createElement("td");
      cell.className = cellClass(col.key);
      cell.dataset["col"] = col.key;
      if (!visibleColumns.has(col.key)) {
        cell.style.display = "none";
      }
      appendCellContent(
        cell,
        plant,
        col.key,
        onOpenPlot,
        knownPlotIds,
        plotAssignmentMeanings,
        mediaPreviewByPlantId,
      );
      row.appendChild(cell);
    });

    const actionCell = document.createElement("td");
    actionCell.className = "col-action";

    const editButton = document.createElement("button");
    editButton.className = "edit-plant-btn";
    editButton.dataset["editPlt"] = plant.plt_id;
    editButton.title = t("plants.edit_plant");
    editButton.type = "button";
    editButton.textContent = "\u270E";
    editButton.addEventListener("click", () => onEdit(plant));

    actionCell.appendChild(editButton);
    row.appendChild(actionCell);
    return row;
  });

  tbody.replaceChildren(...rows);
}

export function renderPlantsMobileCards(
  container: HTMLElement,
  plants: Plant[],
  callbacks: PlantsTableCallbacks,
): void {
  const {
    onOpenPlot, onEdit, knownPlotIds, plotAssignmentMeanings, mediaPreviewByPlantId,
    onToggleSelect, selectedIds,
  } = callbacks;

  if (plants.length === 0) {
    renderEmptyState(container, {
      icon: "\uD83C\uDF3F",
      headline: t("plants.no_matches"),
    });
    return;
  }

  const cards = plants.map((plant) => {
    const article = document.createElement("article");
    article.className = "mobile-data-card mobile-data-card--plant";
    article.dataset["pltId"] = plant.plt_id;
    const bs = bloomStatus(plant.bloom_month);
    if (bs) article.classList.add(bs);
    const presenceStatus = plantPresenceStatus(plant);
    if (presenceStatus === "gone") article.classList.add("plant-gone");
    if (presenceStatus === "mixed") article.classList.add("plant-mixed");
    if (onToggleSelect && selectedIds?.has(plant.plt_id)) {
      article.classList.add("batch-selected");
    }

    const header = document.createElement("div");
    header.className = "mobile-data-card-header";

    if (onToggleSelect) {
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "mobile-batch-cb";
      cb.checked = selectedIds?.has(plant.plt_id) ?? false;
      cb.setAttribute("aria-label", `Select ${plant.name}`);
      cb.addEventListener("change", () => onToggleSelect(plant.plt_id));
      header.appendChild(cb);
    }

    const copy = document.createElement("div");
    copy.className = "mobile-data-copy";

    const previewAsset = mediaPreviewByPlantId?.get(plant.plt_id) ?? null;
    if (previewAsset) {
      copy.appendChild(createLazyMediaThumbnailButton(previewAsset, {
        className: "mobile-plant-thumb",
        imageClassName: "mobile-plant-thumb-image",
        label: previewAsset.original_filename || `${plant.name} ${t("media.latest_photo")}`,
      }));
    }

    const title = document.createElement("h3");
    title.className = "mobile-data-title";
    title.textContent = plant.name;

    const subtitle = document.createElement("p");
    subtitle.className = "mobile-data-subtitle";
    subtitle.textContent = plant.latin || t("plants.no_latin_name");

    copy.append(title, subtitle);
    const statusBadge = createPresenceBadge(plant, "plants-presence-badge--mobile");
    if (statusBadge) copy.appendChild(statusBadge);

    const actions = document.createElement("div");
    actions.className = "mobile-data-actions";

    const safeLink = sanitizeUrl(plant.link ?? "");
    if (safeLink) {
      const link = document.createElement("a");
      link.className = "mobile-data-action-link";
      link.href = safeLink;
      link.target = "_blank";
      link.rel = "noopener";
      link.setAttribute("aria-label", t("plants.open_plant_link", { name: plant.name }));
      link.textContent = "\u2197";
      actions.appendChild(link);
    }

    const editBtn = document.createElement("button");
    editBtn.className = "mobile-data-action-btn";
    editBtn.type = "button";
    editBtn.dataset["editPlt"] = plant.plt_id;
    editBtn.textContent = t("plants.edit");
    editBtn.addEventListener("click", () => onEdit(plant));
    actions.appendChild(editBtn);

    header.append(copy, actions);

    const chipRow = document.createElement("div");
    chipRow.className = "mobile-data-chip-row";
    const emoji = CATEGORY_EMOJI[plant.category] ?? "";
    const chips = [
      plant.category ? `${emoji ? `${emoji} ` : ""}${formatPlantCategoryLabel(plant.category)}` : "",
      plant.quantity && plant.quantity > 0 ? t("plants.qty_chip", { count: plant.quantity }) : "",
      plant.deer_resistant ? t("plants.deer_resistant") : "",
    ].filter(Boolean);
    chips.forEach((chip) => {
      const chipEl = document.createElement("span");
      chipEl.className = "mobile-data-chip";
      chipEl.textContent = chip;
      chipRow.appendChild(chipEl);
    });

    const factGrid = document.createElement("div");
    factGrid.className = "mobile-data-fact-grid";
    const facts = [
      createMobileFact(t("plants.field_bloom"), formatBloomMonth(plant.bloom_month)),
      createMobileFact(t("plants.field_color"), plant.color),
      createMobileFact(t("plants.field_hardiness"), plant.hardiness),
      createMobileFact(t("plants.field_light"), plant.light),
      createMobileFact(
        t("plants.field_height"),
        plant.height_cm != null ? t("plants.field_height_value", { count: plant.height_cm }) : "",
      ),
      createMobileFact(t("plants.field_year"), plant.year_planted ?? ""),
    ].filter((fact): fact is HTMLElement => fact !== null);
    factGrid.append(...facts);

    const plotRow = document.createElement("div");
    plotRow.className = "mobile-data-plot-row";
    const plotLabel = document.createElement("span");
    plotLabel.className = "mobile-data-label";
    plotLabel.textContent = t("plants.field_plots");
    const plotLinks = document.createElement("div");
    plotLinks.className = "mobile-data-plot-links";
    appendPlotLinks(plotLinks, plant, onOpenPlot, knownPlotIds, plotAssignmentMeanings);
    plotRow.append(plotLabel, plotLinks);

    article.append(header, chipRow, factGrid, plotRow);
    return article;
  });

  container.replaceChildren(...cards);
}

export function syncPlantsSelectionState(
  tbody: HTMLElement,
  mobileList: HTMLElement,
  selectedIds: ReadonlySet<string>,
): void {
  for (const row of tbody.querySelectorAll<HTMLTableRowElement>("tr[data-plt-id]")) {
    const pltId = row.dataset["pltId"] ?? "";
    const selected = selectedIds.has(pltId);
    row.classList.toggle("batch-selected", selected);
    const checkbox = row.querySelector<HTMLInputElement>(".col-select input[type='checkbox']");
    if (checkbox) checkbox.checked = selected;
  }

  for (const card of mobileList.querySelectorAll<HTMLElement>("article[data-plt-id]")) {
    const pltId = card.dataset["pltId"] ?? "";
    const selected = selectedIds.has(pltId);
    card.classList.toggle("batch-selected", selected);
    const checkbox = card.querySelector<HTMLInputElement>(".mobile-batch-cb");
    if (checkbox) checkbox.checked = selected;
  }
}
