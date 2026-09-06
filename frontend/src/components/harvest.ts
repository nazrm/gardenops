import type { HarvestEntry, HarvestSummary, LinkedPlot, Plot } from "../core/models";
import { formatPlotLabel } from "../core/models";
import { t } from "../core/i18n";
import { createFieldGroup as _createFieldGroup, createParagraph } from "../core/dom";
import { createChipInput } from "./chipInput";
import { renderPendingMediaPickerLazy } from "./mediaGalleryLoader";
import { renderEmptyState } from "./emptyState";

const QUALITY_ICONS: Record<string, string> = {
  excellent: "\u2B50",
  good: "\u2705",
  fair: "\uD83C\uDD97",
  poor: "\u26A0\uFE0F",
};

type PlotChoice = Pick<Plot, "plot_id" | "zone_name" | "display_name">;

function plotChoiceLabel(plot: PlotChoice): string {
  return formatPlotLabel(plot.plot_id, plot.zone_name, null, plot.display_name);
}

const UNIT_LABELS: Record<string, string> = {
  kg: "kg",
  g: "g",
  lbs: "lbs",
  oz: "oz",
  pieces: "pcs",
  bunches: "bunches",
  liters: "L",
  heads: "heads",
  other: "other",
};

export interface HarvestListCallbacks {
  onEdit: (entry: HarvestEntry) => void;
  onDelete: (entry: HarvestEntry) => void;
  onPlantClick: (pltId: string) => void;
  onPlotClick: (plotId: string) => void;
  onEmptyAction?: (() => void) | undefined;
  canWrite?: boolean | undefined;
}

export function renderHarvestList(
  container: HTMLElement,
  entries: HarvestEntry[],
  cbs: HarvestListCallbacks,
  plantNames?: Map<string, string>,
): void {
  container.replaceChildren();
  if (entries.length === 0) {
    renderEmptyState(container, {
      icon: "\uD83C\uDF3E",
      headline: t("harvest.empty"),
      hint: t("harvest.empty_hint"),
      ctaLabel: cbs.onEmptyAction ? t("harvest.empty_cta") : undefined,
      ctaAction: cbs.onEmptyAction,
    });
    return;
  }
  for (const entry of entries) {
    container.appendChild(createHarvestCard(entry, cbs, plantNames));
  }
}

function createHarvestCard(
  entry: HarvestEntry,
  cbs: HarvestListCallbacks,
  plantNames?: Map<string, string>,
): HTMLElement {
  const card = document.createElement("div");
  card.className = "harvest-card";

  // Header: quality icon + quantity + unit + date
  const header = document.createElement("div");
  header.className = "harvest-card-header";

  const icon = document.createElement("span");
  icon.className = "harvest-card-icon";
  icon.textContent = QUALITY_ICONS[entry.quality] || "\u2705";

  const qty = document.createElement("span");
  qty.className = "harvest-card-quantity";
  qty.textContent = String(entry.quantity);

  const unit = document.createElement("span");
  unit.className = "harvest-card-unit";
  unit.textContent = UNIT_LABELS[entry.unit] || entry.unit;

  const qualityChip = document.createElement("span");
  qualityChip.className = `harvest-quality-chip quality-${entry.quality}`;
  qualityChip.textContent = t(`harvest.quality_${entry.quality}`);

  const dateEl = document.createElement("span");
  dateEl.className = "harvest-card-date";
  dateEl.textContent = entry.occurred_on;

  header.append(icon, qty, unit, qualityChip, dateEl);
  card.appendChild(header);
  if (entry.plant_ids.length > 1 || entry.plot_ids.length > 1) {
    const shared = document.createElement("p");
    shared.textContent = t("harvest.shared_entry");
    card.appendChild(shared);
  }

  // Notes
  if (entry.notes) {
    const notes = document.createElement("div");
    notes.className = "harvest-card-notes";
    notes.textContent = entry.notes.length > 200 ? entry.notes.slice(0, 200) + "\u2026" : entry.notes;
    card.appendChild(notes);
  }

  // Plant/plot tags
  if (entry.plant_ids.length > 0 || entry.plot_ids.length > 0) {
    const tags = document.createElement("div");
    tags.className = "harvest-card-tags";
    for (const pltId of entry.plant_ids) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "journal-tag journal-tag-plant";
      tag.textContent = plantNames?.get(pltId) ?? pltId;
      tag.addEventListener("click", (e) => {
        e.stopPropagation();
        cbs.onPlantClick(pltId);
      });
      tags.appendChild(tag);
    }
    const plotList: LinkedPlot[] = entry.plots ?? entry.plot_ids.map((id: string) => ({ plot_id: id, zone_name: "" }));
    for (const plot of plotList) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "journal-tag journal-tag-plot";
      tag.textContent = formatPlotLabel(
        plot.plot_id,
        plot.zone_name,
        null,
        plot.display_name,
      );
      tag.addEventListener("click", (e) => {
        e.stopPropagation();
        cbs.onPlotClick(plot.plot_id);
      });
      tags.appendChild(tag);
    }
    card.appendChild(tags);
  }

  // Actions
  if (cbs.canWrite !== false) {
    const actions = document.createElement("div");
    actions.className = "harvest-card-actions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "harvest-action-btn";
    editBtn.textContent = t("common.edit");
    editBtn.addEventListener("click", () => cbs.onEdit(entry));
    actions.appendChild(editBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "harvest-action-btn harvest-action-delete";
    deleteBtn.textContent = t("common.delete");
    deleteBtn.addEventListener("click", () => cbs.onDelete(entry));
    actions.appendChild(deleteBtn);

    card.appendChild(actions);
  }

  return card;
}

export function createHarvestForm(options: {
  entry?: HarvestEntry | undefined;
  availablePlots?: PlotChoice[];
  onSave: (data: Record<string, unknown>) => Promise<void>;
  onCancel: () => void;
}): HTMLElement {
  const { entry, availablePlots = [], onSave, onCancel } = options;
  const form = document.createElement("form");
  form.className = "modal-form";

  const heading = document.createElement("h3");
  heading.textContent = t("harvest.form_title");
  form.appendChild(heading);

  // Date
  const dateGroup = createFieldGroup(t("harvest.form_date"));
  const dateInput = document.createElement("input");
  dateInput.type = "date";
  dateInput.name = "occurred_on";
  dateInput.required = true;
  dateInput.value = entry?.occurred_on || new Date().toISOString().slice(0, 10);
  dateGroup.appendChild(dateInput);
  form.appendChild(dateGroup);

  // Quantity
  const qtyGroup = createFieldGroup(t("harvest.form_quantity"));
  const qtyInput = document.createElement("input");
  qtyInput.type = "number";
  qtyInput.name = "quantity";
  qtyInput.min = "0";
  qtyInput.step = "0.01";
  qtyInput.required = true;
  qtyInput.value = entry ? String(entry.quantity) : "";
  qtyGroup.appendChild(qtyInput);
  form.appendChild(qtyGroup);

  // Unit
  const unitGroup = createFieldGroup(t("harvest.form_unit"));
  const unitSelect = document.createElement("select");
  unitSelect.name = "unit";
  const units = ["kg", "g", "lbs", "oz", "pieces", "bunches", "liters", "heads", "other"];
  for (const u of units) {
    const opt = document.createElement("option");
    opt.value = u;
    opt.textContent = t(`harvest.unit_${u}`);
    if ((entry?.unit || "kg") === u) opt.selected = true;
    unitSelect.appendChild(opt);
  }
  unitGroup.appendChild(unitSelect);
  form.appendChild(unitGroup);

  // Quality
  const qualGroup = createFieldGroup(t("harvest.form_quality"));
  const qualSelect = document.createElement("select");
  qualSelect.name = "quality";
  const qualities = ["excellent", "good", "fair", "poor"];
  for (const q of qualities) {
    const opt = document.createElement("option");
    opt.value = q;
    opt.textContent = t(`harvest.quality_${q}`);
    if ((entry?.quality || "good") === q) opt.selected = true;
    qualSelect.appendChild(opt);
  }
  qualGroup.appendChild(qualSelect);
  form.appendChild(qualGroup);

  // Notes
  const notesGroup = createFieldGroup(t("harvest.form_notes"));
  const notesArea = document.createElement("textarea");
  notesArea.name = "notes";
  notesArea.maxLength = 2000;
  notesArea.rows = 3;
  notesArea.value = entry?.notes || "";
  notesGroup.appendChild(notesArea);
  form.appendChild(notesGroup);

  // Plant IDs
  const plantGroup = createFieldGroup(t("harvest.form_plant_ids"));
  const plantInput = document.createElement("input");
  plantInput.type = "text";
  plantInput.name = "plant_ids";
  plantInput.placeholder = "PLT-001, PLT-002";
  plantInput.value = entry?.plant_ids.join(", ") || "";
  plantGroup.appendChild(plantInput);
  form.appendChild(plantGroup);

  // Plot selection
  const plotChoicesById = new Map<string, PlotChoice>();
  for (const plot of availablePlots) {
    plotChoicesById.set(plot.plot_id, plot);
  }
  for (const plot of entry?.plots ?? []) {
    plotChoicesById.set(plot.plot_id, plot);
  }
  for (const plotId of entry?.plot_ids ?? []) {
    if (!plotChoicesById.has(plotId)) {
      plotChoicesById.set(plotId, { plot_id: plotId, zone_name: "" });
    }
  }
  const plotInput = createChipInput({
    label: t("harvest.form_plot_ids"),
    placeholder: t("issues.form_plot_ids_placeholder"),
    items: [...plotChoicesById.values()].sort((left, right) =>
      plotChoiceLabel(left).localeCompare(plotChoiceLabel(right), undefined, { sensitivity: "base" }),
    ),
    getKey: (plot) => plot.plot_id,
    getLabel: (plot) => plotChoiceLabel(plot),
    getSearchText: (plot) => `${plot.plot_id} ${plotChoiceLabel(plot)}`.toLowerCase(),
    selected: entry?.plot_ids ?? [],
  });
  form.appendChild(plotInput.container);

  const pendingFiles: File[] = [];
  const mediaGroup = createFieldGroup(t("media.attach_photos_optional"));
  const mediaPicker = document.createElement("div");
  const renderMedia = (progressPct: number | null = null) => {
    void renderPendingMediaPickerLazy(mediaPicker, {
      files: pendingFiles,
      emptyText: t("media.pending_empty"),
      uploadProgressPct: progressPct,
      onFilesSelected: (files) => {
        pendingFiles.push(...files);
        renderMedia();
      },
      onRemoveFile: (index) => {
        pendingFiles.splice(index, 1);
        renderMedia();
      },
    });
  };
  renderMedia();
  mediaGroup.appendChild(mediaPicker);
  form.appendChild(mediaGroup);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "modal-form-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "btn-primary";
  saveBtn.textContent = t("common.save");
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.textContent = t("common.close");
  cancelBtn.addEventListener("click", onCancel);
  btnRow.append(cancelBtn, saveBtn);
  form.appendChild(btnRow);

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const plantIds = plantInput.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const plotIds = plotInput.getSelectedKeys();
    const data: Record<string, unknown> = {
      occurred_on: dateInput.value,
      quantity: parseFloat(qtyInput.value) || 0,
      unit: unitSelect.value,
      quality: qualSelect.value,
      notes: notesArea.value,
      plant_ids: plantIds,
      plot_ids: plotIds,
      media_files: [...pendingFiles],
    };
    void onSave(data);
  });

  return form;
}

export function renderHarvestSummary(container: HTMLElement, summary: HarvestSummary): void {
  container.replaceChildren();
  container.className = "harvest-summary-panel";

  // Total
  const totalSection = createSummarySection(t("harvest.summary_title"));
  const totalRow = document.createElement("div");
  totalRow.className = "harvest-summary-row";
  const totalLabel = document.createElement("span");
  totalLabel.className = "harvest-summary-label";
  totalLabel.textContent = summary.year !== null ? String(summary.year)
    : `${summary.date_from ?? t("harvest.all_dates")} - ${summary.date_to ?? t("harvest.all_dates")}`;
  const totalVal = document.createElement("span");
  totalVal.className = "harvest-summary-value";
  totalVal.textContent = t("harvest.summary_count", { count: summary.total_entries });
  totalRow.append(totalLabel, totalVal);
  totalSection.appendChild(totalRow);
  container.appendChild(totalSection);

  function quantityRow(labelText: string, value: {
    total_qty: number; unit: string; shared_qty: number; shared_entries: number;
  }): HTMLElement {
    const row = document.createElement("div");
    row.className = "harvest-summary-row";
    const label = document.createElement("span");
    label.className = "harvest-summary-label";
    label.textContent = labelText;
    const amount = document.createElement("span");
    amount.className = "harvest-summary-value";
    amount.textContent = `${value.total_qty} ${value.unit}`;
    if (value.shared_entries > 0) {
      const shared = document.createElement("small");
      shared.textContent = t("harvest.shared_quantity", {
        quantity: value.shared_qty, unit: value.unit, count: value.shared_entries,
      });
      amount.append(document.createElement("br"), shared);
    }
    row.append(label, amount);
    return row;
  }

  if (summary.by_unit.length > 0) {
    const units = createSummarySection(t("harvest.summary_units"));
    for (const unit of summary.by_unit) units.appendChild(quantityRow(unit.unit, unit));
    container.appendChild(units);
  }

  if (summary.by_plant.length > 0) {
    const plantSection = createSummarySection(t("harvest.summary_plants"));
    const attribution = document.createElement("p");
    attribution.textContent = t("harvest.attribution_note");
    plantSection.appendChild(attribution);
    for (const plant of summary.by_plant) plantSection.appendChild(quantityRow(plant.name, plant));
    container.appendChild(plantSection);
  }

  // Monthly
  if (summary.by_month.length > 0) {
    const monthSection = createSummarySection(t("harvest.summary_monthly"));
    for (const month of summary.by_month) {
      monthSection.appendChild(quantityRow(month.month, month));
    }
    container.appendChild(monthSection);
  }

  // Quality distribution
  const qualSection = createSummarySection(t("harvest.summary_quality"));
  const qualTotal = summary.by_quality.excellent + summary.by_quality.good + summary.by_quality.fair + summary.by_quality.poor;
  for (const q of ["excellent", "good", "fair", "poor"] as const) {
    const count = summary.by_quality[q];
    if (count === 0) continue;
    const row = document.createElement("div");
    row.className = "harvest-summary-row";
    const label = document.createElement("span");
    label.className = "harvest-summary-label";
    label.textContent = `${QUALITY_ICONS[q] || ""} ${t(`harvest.quality_${q}`)}`;
    const bar = document.createElement("div");
    bar.className = "harvest-summary-bar";
    const fill = document.createElement("div");
    fill.className = "harvest-summary-bar-fill";
    fill.style.width = `${qualTotal > 0 ? (count / qualTotal) * 100 : 0}%`;
    bar.appendChild(fill);
    const val = document.createElement("span");
    val.className = "harvest-summary-value";
    val.textContent = String(count);
    row.append(label, bar, val);
    qualSection.appendChild(row);
  }
  container.appendChild(qualSection);
}

function createSummarySection(title: string): HTMLElement {
  const section = document.createElement("div");
  section.className = "harvest-summary-section";
  const heading = document.createElement("div");
  heading.className = "harvest-summary-section-title";
  heading.textContent = title;
  section.appendChild(heading);
  return section;
}

function createFieldGroup(label: string): HTMLElement {
  return _createFieldGroup(label, "modal-field-group", "modal-field-label");
}
