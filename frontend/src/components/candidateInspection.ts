import type { AppContext } from "../core/appContext";
import type { PlantingSuggestion } from "../core/models";
import { t } from "../core/i18n";
import {
  fetchCompanionCheckApi, fetchHarvestApi, fetchJournalEntriesApi,
  getActiveGardenContext, getApiErrorMessage, listInventoryApi,
} from "../services/api";
import { createModal } from "./dialogCore";
import { journalEventLabel } from "./journalPreview";

const LIMIT = 20;
const openCandidates = new Set<() => void>();

export function closeCandidateInspections(): void {
  for (const close of openCandidates) close();
}

export function openCandidateInspection(
  ctx: AppContext,
  plotId: string,
  suggestion: PlantingSuggestion,
  onSaved: () => void,
): void {
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return;
  const profile = ctx.getAuthProfile();
  const { dialog, close } = createModal(suggestion.name,
    '<div class="modal-content candidate-inspection"></div>',
    { onClose: () => openCandidates.delete(close) },
  );
  openCandidates.add(close);
  const current = () => dialog.isConnected && getActiveGardenContext() === gardenId
    && ctx.getAuthProfile() === profile;
  const content = dialog.querySelector<HTMLElement>(".modal-content")!;
  const title = document.createElement("h2");
  title.textContent = suggestion.name;
  const place = document.createElement("p");
  place.textContent = plotId;
  content.append(title, place);

  function section(label: string): HTMLElement {
    const heading = document.createElement("h3");
    heading.textContent = label;
    const body = document.createElement("div");
    content.append(heading, body);
    return body;
  }
  function paragraph(parent: HTMLElement, text: string): void {
    const p = document.createElement("p");
    p.textContent = text;
    parent.appendChild(p);
  }
  function failed(parent: HTMLElement, error: unknown, retry: () => void): void {
    paragraph(parent, getApiErrorMessage(error));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn-secondary";
    button.textContent = t("common.retry");
    button.addEventListener("click", retry);
    parent.appendChild(button);
  }

  const fit = section(t("planner.check_fit"));
  const stock = section(t("planner.recorded_stock"));
  const history = document.createElement("details");
  const historyLabel = document.createElement("summary");
  historyLabel.textContent = t("planner.recorded_here");
  const records = document.createElement("div");
  history.append(historyLabel, records);
  content.appendChild(history);
  let historyLoaded = false;
  let stockSequence = 0;
  let historySequence = 0;

  async function loadFit(): Promise<void> {
    fit.textContent = t("common.loading");
    try {
      const result = await fetchCompanionCheckApi({ plot_id: plotId, plt_id: suggestion.plt_id }, { gardenId: gardenId! });
      if (!current()) return;
      fit.replaceChildren();
      for (const reason of suggestion.reasons) paragraph(fit, reason);
      for (const item of result.companions) paragraph(fit, `${t("planner.fit_good")}: ${item.description}`);
      for (const item of result.conflicts) paragraph(fit, `${t("planner.fit_conflict")}: ${item.description}`);
      if (!result.companions.length && !result.conflicts.length) paragraph(fit, t("planner.fit_none"));
    } catch (error) {
      if (!current()) return;
      fit.replaceChildren();
      failed(fit, error, () => void loadFit());
    }
  }

  async function loadStock(): Promise<void> {
    const sequence = ++stockSequence;
    stock.textContent = t("common.loading");
    try {
      const result = await listInventoryApi({ plt_id: suggestion.plt_id, limit: LIMIT }, { gardenId: gardenId! });
      if (!current() || sequence !== stockSequence) return;
      stock.replaceChildren();
      if (!result.items.length) paragraph(stock, t("planner.stock_empty"));
      if (!ctx.canWrite()) paragraph(stock, t("planner.stock_readonly"));
      const destinationAvailable = ctx.getPlots().some((plot) => plot.plot_id === plotId);
      if (!destinationAvailable) paragraph(stock, t("planner.destination_unavailable"));
      for (const item of result.items) {
        const row = document.createElement("div");
        row.className = "candidate-stock-row";
        paragraph(row, `${item.label} (${item.inventory_type}): ${item.quantity} ${item.unit}`);
        if (Number(item.quantity) <= 0) paragraph(row, t("planner.stock_zero"));
        if (ctx.canWrite()) {
          const plant = document.createElement("button");
          plant.type = "button";
          plant.className = "btn-secondary";
          plant.textContent = t("inventory.modal_plant_stock", { label: item.label });
          plant.disabled = Number(item.quantity) <= 0 || !destinationAvailable;
          plant.addEventListener("click", () => {
            if (!current()) return;
            void ctx.openStockPlanting(item, plotId, () => {
              if (!current()) return;
              void loadFit();
              void loadStock();
              if (history.open) void loadHistory();
              else historyLoaded = false;
              onSaved();
            }, dialog).catch((error: unknown) => {
              if (current()) ctx.showToast(getApiErrorMessage(error), "error");
            });
          });
          row.appendChild(plant);
        }
        stock.appendChild(row);
      }
      if (result.total > result.items.length) paragraph(stock, t("planner.stock_limit", { count: result.items.length, total: result.total }));
    } catch (error) {
      if (!current() || sequence !== stockSequence) return;
      stock.replaceChildren();
      failed(stock, error, () => void loadStock());
    }
  }

  async function loadHistory(): Promise<void> {
    historyLoaded = true;
    const sequence = ++historySequence;
    records.textContent = t("common.loading");
    const params = { plant_id: suggestion.plt_id, plot_id: plotId, limit: LIMIT };
    const [journal, harvest] = await Promise.allSettled([
      fetchJournalEntriesApi(params, { gardenId: gardenId! }),
      fetchHarvestApi(params, { gardenId: gardenId! }),
    ]);
    if (!current() || sequence !== historySequence) return;
    records.replaceChildren();
    const harvestIds = new Set(harvest.status === "fulfilled" ? harvest.value.entries.map((entry) => entry.id) : []);
    const rows: Array<{ date: string; id: string; text: string }> = [];
    if (journal.status === "fulfilled") {
      for (const entry of journal.value.entries) {
        if (harvestIds.has(String(entry.metadata["linked_harvest_entry_id"] ?? ""))) continue;
        rows.push({ date: entry.occurred_on, id: entry.id,
          text: [journalEventLabel(entry.event_type), entry.title, entry.notes].filter(Boolean).join(": ") });
      }
    } else failed(records, journal.reason, () => void loadHistory());
    if (harvest.status === "fulfilled") {
      for (const entry of harvest.value.entries) {
        const shared = entry.plant_ids.length > 1 || entry.plot_ids.length > 1;
        rows.push({ date: entry.occurred_on, id: entry.id, text: `${entry.quantity} ${entry.unit} - ${t(`harvest.quality_${entry.quality}`)}${shared ? ` (${t("harvest.shared_entry")})` : ""}${entry.notes ? `: ${entry.notes}` : ""}` });
      }
    } else failed(records, harvest.reason, () => void loadHistory());
    rows.sort((a, b) => b.date.localeCompare(a.date) || a.id.localeCompare(b.id));
    for (const row of rows) paragraph(records, `${row.date}: ${row.text}`);
    if (!rows.length && journal.status === "fulfilled" && harvest.status === "fulfilled") paragraph(records, t("planner.history_empty"));
    paragraph(records, t("planner.history_limit", { limit: LIMIT }));
  }

  history.addEventListener("toggle", () => {
    if (history.open && !historyLoaded && current()) void loadHistory();
  });
  void loadFit();
  void loadStock();
  dialog.querySelector<HTMLButtonElement>(".modal-close-btn")?.focus();
}
