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
const PROCUREMENT_PAGE_SIZE = 50;

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
    const result = await fetchProcurementApi(params);
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
    onEdit: (item) => void openProcurementForm(item),
    onTransition: (item, toStatus) =>
      void handleProcurementTransition(item, toStatus),
    onDelete: (item) => void deleteProcurement(item),
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
      try {
        if (existingItem) {
          await updateProcurementApi(
            existingItem.id,
            data,
          );
        } else {
          await createProcurementApi(data);
        }
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
        void loadProcurement();
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
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}

async function handleProcurementTransition(
  item: ProcurementItem,
  toStatus: string,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  try {
    await transitionProcurementApi(item.id, {
      to_status: toStatus,
    });
    ctx.showToast(
      t("procurement.transitioned"),
      "success",
    );
    void loadProcurement();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function deleteProcurement(
  item: ProcurementItem,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const ok = await confirmDialog(
    t("procurement.confirm_delete"),
    t("common.delete"),
  );
  if (!ok) return;
  try {
    await deleteProcurementApi(item.id);
    ctx.showToast(t("procurement.deleted"), "success");
    void loadProcurement();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}
