import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type { HarvestEntry } from "../core/models";
import { t } from "../core/i18n";
import {
  fetchHarvestApi,
  createHarvestApi,
  updateHarvestApi,
  deleteHarvestApi,
  fetchHarvestSummaryApi,
  getApiErrorMessage,
} from "../services/api";
import {
  renderHarvestList,
  createHarvestForm,
  renderHarvestSummary,
} from "../components/harvest";
import { buildPlantNameMap } from "../core/plantNames";
import { confirmDialog } from "../components/dialogCore";
import { selectPlot } from "../components/plotInteractions";

let ctx: AppContext;

let harvestItems: HarvestEntry[] = [];
let harvestTotal = 0;
let harvestOffset = 0;
let harvestLoadSequence = 0;
const HARVEST_PAGE_SIZE = 50;

export function setHarvestOffset(offset: number): void {
  harvestOffset = offset;
}

export function resetHarvestForGardenSwitch(): void {
  harvestLoadSequence += 1;
  harvestItems = [];
  harvestTotal = 0;
  harvestOffset = 0;
  renderHarvestView();
  const panel = document.getElementById("harvest-summary-panel");
  if (panel instanceof HTMLElement) {
    panel.hidden = true;
    panel.replaceChildren();
  }
}

export function initHarvestTab(appCtx: AppContext): void {
  ctx = appCtx;

  const addButton = document.getElementById("harvest-add-btn");
  if (addButton) {
    addButton.hidden = !ctx.canWrite();
    addButton.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openHarvestForm();
    });
  }
  document
    .getElementById("harvest-summary-btn")
    ?.addEventListener("click", () =>
      void showHarvestSummary(),
    );
  document
    .getElementById("harvest-filter-quality")
    ?.addEventListener("change", () => {
      harvestOffset = 0;
      void loadHarvest();
    });
  document
    .getElementById("harvest-filter-from")
    ?.addEventListener("change", () => {
      harvestOffset = 0;
      void loadHarvest();
    });
  document
    .getElementById("harvest-filter-to")
    ?.addEventListener("change", () => {
      harvestOffset = 0;
      void loadHarvest();
    });
}

export async function loadHarvest(): Promise<void> {
  if (!ctx) return;
  const sequence = ++harvestLoadSequence;
  try {
    const params: Record<string, string | number> = {
      limit: HARVEST_PAGE_SIZE,
      offset: harvestOffset,
    };
    const qualityFilter = querySelect("harvest-filter-quality")?.value;
    if (qualityFilter) params["quality"] = qualityFilter;
    const dateFrom = queryInput("harvest-filter-from")?.value;
    if (dateFrom) params["date_from"] = dateFrom;
    const dateTo = queryInput("harvest-filter-to")?.value;
    if (dateTo) params["date_to"] = dateTo;
    const result = await fetchHarvestApi(params);
    if (sequence !== harvestLoadSequence) return;
    if (result.total > 0 && result.entries.length === 0 && harvestOffset > 0) {
      harvestOffset = Math.max(
        0,
        Math.floor((result.total - 1) / HARVEST_PAGE_SIZE) * HARVEST_PAGE_SIZE,
      );
      await loadHarvest();
      return;
    }
    harvestItems = result.entries;
    harvestTotal = result.total;
    renderHarvestView();
    void refreshHarvestSummaryIfOpen();
  } catch (err) {
    if (sequence !== harvestLoadSequence) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function readHarvestSummaryFilters(): Record<string, string> {
  const params: Record<string, string> = {};
  const quality = querySelect("harvest-filter-quality")?.value || "";
  const dateFrom = queryInput("harvest-filter-from")?.value || "";
  const dateTo = queryInput("harvest-filter-to")?.value || "";
  if (quality) params["quality"] = quality;
  if (dateFrom) params["date_from"] = dateFrom;
  if (dateTo) params["date_to"] = dateTo;
  return params;
}

function renderHarvestView(): void {
  const container = document.getElementById("harvest-list");
  if (!container) return;
  const summary = document.getElementById("harvest-summary");
  if (summary) {
    summary.textContent =
      harvestTotal === 0
        ? t("harvest.summary_none")
        : t("harvest.summary_count", {
            count: harvestTotal,
          });
  }
  const plantNames = buildPlantNameMap(ctx.getPlants());
  renderHarvestList(container, harvestItems, {
    onEdit: (entry) => void openHarvestForm(entry),
    onDelete: (entry) => void handleDeleteHarvest(entry),
    onEmptyAction: ctx.canWrite() ? () => openHarvestForm() : undefined,
    onPlantClick: (pltId) => {
      ctx.focusPlantsInPlantsView([pltId]);
    },
    onPlotClick: (plotId) => {
      ctx.setActiveTab("map");
      void selectPlot(
        ctx.state,
        plotId,
        ctx.getPlotCallbacks(),
      );
    },
    canWrite: ctx.canWrite(),
  }, plantNames);
  ctx.renderDataExportBars();
  renderHarvestPagination();
}

function renderHarvestPagination(): void {
  const container = document.getElementById(
    "harvest-pagination",
  );
  if (!container) return;
  container.replaceChildren();
  if (harvestTotal <= HARVEST_PAGE_SIZE) return;
  const page =
    Math.floor(harvestOffset / HARVEST_PAGE_SIZE) + 1;
  const totalPages = Math.ceil(
    harvestTotal / HARVEST_PAGE_SIZE,
  );
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = t("common.previous");
  prev.disabled = harvestOffset === 0;
  prev.addEventListener("click", () => {
    harvestOffset = Math.max(
      0,
      harvestOffset - HARVEST_PAGE_SIZE,
    );
    void loadHarvest();
  });
  const info = document.createElement("span");
  info.textContent = t("common.page_of", {
    page,
    total: totalPages,
  });
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = t("common.next");
  next.disabled =
    harvestOffset + HARVEST_PAGE_SIZE >= harvestTotal;
  next.addEventListener("click", () => {
    harvestOffset += HARVEST_PAGE_SIZE;
    void loadHarvest();
  });
  container.append(prev, info, next);
}

export function openHarvestForm(
  existingEntry?: HarvestEntry,
): void {
  if (!ctx.ensureWriteAccess()) return;
  const form = createHarvestForm({
    entry: existingEntry,
    onSave: async (data) => {
      try {
        const mediaFiles = ctx.extractPendingMediaFiles(
          data as Record<string, unknown>,
        );
        const harvestPayload =
          ctx.withoutPendingMediaFiles(
            data as Record<string, unknown>,
          );
        let savedHarvestId: string | null = existingEntry?.id ?? null;
        if (existingEntry) {
          await updateHarvestApi(
            existingEntry.id,
            harvestPayload,
          );
        } else if (!ctx.isOnline()) {
          await ctx.enqueueDraft(
            "harvest_create",
            data as Record<string, unknown>,
          );
          ctx.showToast(
            t("offline.draft_saved"),
            "success",
          );
          void ctx.refreshOfflineIndicator();
          overlay.remove();
          return;
        } else {
          const created = await createHarvestApi(
            harvestPayload as Parameters<
              typeof createHarvestApi
            >[0],
          );
          savedHarvestId = created.id;
        }
        if (savedHarvestId) {
          try {
            await ctx.uploadTargetMediaFiles(
              "harvest_entry",
              savedHarvestId,
              mediaFiles,
            );
          } catch {
            ctx.showToast(
              t("media.harvest_upload_partial"),
              "error",
            );
            overlay.remove();
            void loadHarvest();
            return;
          }
        }
        ctx.showToast(
          t(
            existingEntry
              ? "harvest.updated"
              : "harvest.created",
          ),
          "success",
        );
        if (!existingEntry) {
          harvestOffset = 0;
        }
        overlay.remove();
        void loadHarvest();
      } catch (err) {
        ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: () => overlay.remove(),
  });
  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  const dialog = document.createElement("div");
  dialog.className = "modal-content";
  dialog.appendChild(form);
  if (existingEntry) {
    ctx.attachReadonlyMediaSection(dialog, {
      targetType: "harvest_entry",
      targetId: existingEntry.id,
      emptyText: t("media.harvest_empty"),
    });
  }
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}

async function handleDeleteHarvest(
  entry: HarvestEntry,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const ok = await confirmDialog(
    t("harvest.confirm_delete"),
    t("common.delete"),
  );
  if (!ok) return;
  try {
    await deleteHarvestApi(entry.id);
    ctx.showToast(t("harvest.deleted"), "success");
    void loadHarvest();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function showHarvestSummary(): Promise<void> {
  return (async () => {
    const sequence = harvestLoadSequence;
    const panel = document.getElementById(
      "harvest-summary-panel",
    );
    if (!panel) return;
    if (!panel.hidden) {
      panel.hidden = true;
      return;
    }
    try {
      const summary = await fetchHarvestSummaryApi(
        readHarvestSummaryFilters(),
      );
      if (sequence !== harvestLoadSequence) return;
      renderHarvestSummary(panel, summary);
      panel.hidden = false;
    } catch (err) {
      ctx.showToast(getApiErrorMessage(err), "error");
    }
  })();
}

async function refreshHarvestSummaryIfOpen(): Promise<void> {
  const sequence = harvestLoadSequence;
  const panel = document.getElementById(
    "harvest-summary-panel",
  );
  if (!(panel instanceof HTMLElement) || panel.hidden) return;
  try {
    const summary = await fetchHarvestSummaryApi(
      readHarvestSummaryFilters(),
    );
    if (sequence !== harvestLoadSequence) return;
    renderHarvestSummary(panel, summary);
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

export async function openHarvestSummaryPanel(): Promise<void> {
  const sequence = harvestLoadSequence;
  const panel = document.getElementById(
    "harvest-summary-panel",
  );
  if (!panel) return;
  const summary = await fetchHarvestSummaryApi(
    readHarvestSummaryFilters(),
  );
  if (sequence !== harvestLoadSequence) return;
  renderHarvestSummary(panel, summary);
  panel.hidden = false;
}
