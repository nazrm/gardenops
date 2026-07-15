import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type { ProcurementItem } from "../core/models";
import { t } from "../core/i18n";
import {
  fetchProcurementApi,
  createProcurementApi,
  updateProcurementApi,
  transitionProcurementApi,
  deleteProcurementApi,
  getActiveGardenContext,
  getApiErrorMessage,
} from "../services/api";
import {
  renderProcurementList,
  createProcurementForm,
} from "../components/procurement";
import { buildPlantNameMap } from "../core/plantNames";
import { confirmDialog } from "../components/dialogCore";
import { selectPlot } from "../components/plotInteractions";

let ctx: AppContext;

let procurementItems: ProcurementItem[] = [];
let procurementTotal = 0;
let procurementOffset = 0;
let procurementRequestGeneration = 0;
const procurementPendingActions = new Set<string>();
const PROCUREMENT_PAGE_SIZE = 50;

interface ProcurementRequestContext {
  gardenId: number;
  generation: number;
}

function createProcurementRequest(): ProcurementRequestContext | null {
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return null;
  return { gardenId, generation: ++procurementRequestGeneration };
}

function isCurrentProcurementRequest(request: ProcurementRequestContext): boolean {
  return request.generation === procurementRequestGeneration
    && request.gardenId === getActiveGardenContext();
}

function isCurrentProcurementGarden(gardenId: number): boolean {
  return gardenId === getActiveGardenContext();
}

export function resetProcurementForGardenSwitch(): void {
  procurementRequestGeneration += 1;
  procurementItems = [];
  procurementTotal = 0;
  procurementOffset = 0;
  procurementPendingActions.clear();
  document.getElementById("procurement-summary")?.replaceChildren();
  document.getElementById("procurement-list")?.replaceChildren();
  document.getElementById("procurement-pagination")?.replaceChildren();
  const statusFilter = querySelect("procurement-filter-status");
  const typeFilter = querySelect("procurement-filter-type");
  if (statusFilter) statusFilter.value = "";
  if (typeFilter) typeFilter.value = "";
  document.querySelectorAll(".procurement-modal").forEach((modal) => modal.remove());
}

export function setProcurementOffset(
  offset: number,
): void {
  procurementOffset = offset;
}

export function initProcurementTab(
  appCtx: AppContext,
): void {
  ctx = appCtx;

  document
    .getElementById("procurement-add-btn")
    ?.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openProcurementForm();
    });
  document
    .getElementById("procurement-filter-status")
    ?.addEventListener("change", () => {
      procurementOffset = 0;
      void loadProcurement();
    });
  document
    .getElementById("procurement-filter-type")
    ?.addEventListener("change", () => {
      procurementOffset = 0;
      void loadProcurement();
    });
}

export async function loadProcurement(): Promise<void> {
  if (!ctx) return;
  const request = createProcurementRequest();
  if (!request) return;
  try {
    const params: Record<string, string | number> = {
      limit: PROCUREMENT_PAGE_SIZE,
      offset: procurementOffset,
    };
    const statusFilter = querySelect("procurement-filter-status")?.value;
    if (statusFilter) params["status"] = statusFilter;
    const typeFilter = querySelect("procurement-filter-type")?.value;
    if (typeFilter)
      params["inventory_type"] = typeFilter;
    const result = await fetchProcurementApi(params, { gardenId: request.gardenId });
    if (!isCurrentProcurementRequest(request)) return;
    if (result.total > 0 && result.items.length === 0 && procurementOffset > 0) {
      procurementOffset = Math.max(
        0,
        Math.floor((result.total - 1) / PROCUREMENT_PAGE_SIZE) * PROCUREMENT_PAGE_SIZE,
      );
      await loadProcurement();
      return;
    }
    procurementItems = result.items;
    procurementTotal = result.total;
    renderProcurementView();
  } catch (err) {
    if (!isCurrentProcurementRequest(request)) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function renderProcurementView(): void {
  const container = document.getElementById(
    "procurement-list",
  );
  if (!container) return;
  const summary = document.getElementById(
    "procurement-summary",
  );
  if (summary) {
    summary.textContent =
      procurementTotal === 0
        ? t("procurement.summary_none")
        : t("procurement.summary_count", {
            count: procurementTotal,
          });
  }
  const plantNames = buildPlantNameMap(ctx.getPlants());
  renderProcurementList(container, procurementItems, {
    onEdit: (item) => openProcurementForm(item),
    onTransition: (item, toStatus) =>
      handleProcurementTransition(item, toStatus),
    onDelete: (item) => deleteProcurement(item),
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
  renderProcurementPagination();
}

function renderProcurementPagination(): void {
  const container = document.getElementById(
    "procurement-pagination",
  );
  if (!container) return;
  container.replaceChildren();
  if (procurementTotal <= PROCUREMENT_PAGE_SIZE) return;
  const page =
    Math.floor(
      procurementOffset / PROCUREMENT_PAGE_SIZE,
    ) + 1;
  const totalPages = Math.ceil(
    procurementTotal / PROCUREMENT_PAGE_SIZE,
  );
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = t("common.previous");
  prev.disabled = procurementOffset === 0;
  prev.addEventListener("click", () => {
    procurementOffset = Math.max(
      0,
      procurementOffset - PROCUREMENT_PAGE_SIZE,
    );
    void loadProcurement();
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
    procurementOffset + PROCUREMENT_PAGE_SIZE >=
    procurementTotal;
  next.addEventListener("click", () => {
    procurementOffset += PROCUREMENT_PAGE_SIZE;
    void loadProcurement();
  });
  container.append(prev, info, next);
}

function openProcurementForm(
  existingItem?: ProcurementItem,
): void {
  if (!ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  if (
    gardenId === null
    || (existingItem && existingItem.garden_id !== gardenId)
  ) return;
  const form = createProcurementForm({
    item: existingItem,
    availablePlants: ctx.getPlants().map((p) => ({
      plt_id: p.plt_id,
      name: p.name,
    })),
    availablePlots: ctx
      .getPlots()
      .map((p) => p.plot_id)
      .sort(),
    onSave: async (data) => {
      const actionKey = existingItem ? `update:${existingItem.id}` : "create";
      if (
        procurementPendingActions.has(actionKey)
        || !isCurrentProcurementGarden(gardenId)
      ) return;
      procurementPendingActions.add(actionKey);
      try {
        if (existingItem) {
          await updateProcurementApi(
            existingItem.id,
            data,
            { gardenId },
          );
        } else {
          await createProcurementApi(data, { gardenId });
        }
        if (!isCurrentProcurementGarden(gardenId)) return;
        ctx.showToast(
          t(
            existingItem
              ? "procurement.updated"
              : "procurement.created",
          ),
          "success",
        );
        if (!existingItem) {
          procurementOffset = 0;
        }
        overlay.remove();
        await loadProcurement();
      } catch (err) {
        if (isCurrentProcurementGarden(gardenId)) {
          ctx.showToast(getApiErrorMessage(err), "error");
        }
      } finally {
        procurementPendingActions.delete(actionKey);
      }
    },
    onCancel: () => overlay.remove(),
  });
  const overlay = document.createElement("div");
  overlay.className = "modal procurement-modal";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  const dialog = document.createElement("div");
  dialog.className = "modal-content";
  dialog.appendChild(form);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}

async function handleProcurementTransition(
  item: ProcurementItem,
  toStatus: string,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  const actionKey = `transition:${item.id}`;
  if (
    gardenId === null
    || item.garden_id !== gardenId
    || procurementPendingActions.has(actionKey)
  ) return;
  procurementPendingActions.add(actionKey);
  try {
    await transitionProcurementApi(
      item.id,
      { to_status: toStatus },
      { gardenId },
    );
    if (!isCurrentProcurementGarden(gardenId)) return;
    ctx.showToast(
      t("procurement.transitioned"),
      "success",
    );
    await loadProcurement();
  } catch (err) {
    if (isCurrentProcurementGarden(gardenId)) {
      ctx.showToast(getApiErrorMessage(err), "error");
    }
  } finally {
    procurementPendingActions.delete(actionKey);
  }
}

async function deleteProcurement(
  item: ProcurementItem,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  const actionKey = `delete:${item.id}`;
  if (
    gardenId === null
    || item.garden_id !== gardenId
    || procurementPendingActions.has(actionKey)
  ) return;
  procurementPendingActions.add(actionKey);
  try {
    const ok = await confirmDialog(
      t("procurement.confirm_delete"),
      t("common.delete"),
    );
    if (!ok || !isCurrentProcurementGarden(gardenId)) return;
    await deleteProcurementApi(item.id, { gardenId });
    if (!isCurrentProcurementGarden(gardenId)) return;
    ctx.showToast(t("procurement.deleted"), "success");
    await loadProcurement();
  } catch (err) {
    if (isCurrentProcurementGarden(gardenId)) {
      ctx.showToast(getApiErrorMessage(err), "error");
    }
  } finally {
    procurementPendingActions.delete(actionKey);
  }
}
