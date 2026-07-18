import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type {
  InventoryItem,
} from "../services/api";
import { t } from "../core/i18n";
import {
  clearInventoryList,
  renderInventoryList,
  renderInventoryTable,
  renderTransactionHistory,
  createInventoryItemForm,
  createStockTransactionForm,
} from "../components/inventory";
import { buildPlantNameMap } from "../core/plantNames";
import {
  listInventoryApi,
  createInventoryItemApi,
  updateInventoryItemApi,
  deleteInventoryItemApi,
  listInventoryTransactionsApi,
  addInventoryTransactionApi,
  absoluteDecimalString,
  plantFromInventoryApi,
  createJournalEntryApi,
  getActiveGardenContext,
  getApiErrorMessage,
} from "../services/api";
import { loadJournalEntries } from "../tabs/journalTab";

let ctx: AppContext;

let inventoryItems: InventoryItem[] = [];
let inventoryTotal = 0;
let inventoryOffset = 0;
let inventoryRequestGeneration = 0;
const inventoryPendingActions = new Set<string>();
const INVENTORY_PAGE_SIZE = 50;
const inventoryDesktopLayoutQuery = window.matchMedia("(min-width: 961px)");
let inventoryViewLoaded = false;

interface InventoryRequestContext {
  gardenId: number;
  generation: number;
}

function createInventoryRequest(): InventoryRequestContext | null {
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return null;
  return { gardenId, generation: ++inventoryRequestGeneration };
}

function isCurrentInventoryRequest(request: InventoryRequestContext): boolean {
  return request.generation === inventoryRequestGeneration
    && request.gardenId === getActiveGardenContext();
}

function isCurrentInventoryGarden(gardenId: number): boolean {
  return gardenId === getActiveGardenContext();
}

export function resetInventoryForGardenSwitch(): void {
  inventoryRequestGeneration += 1;
  inventoryItems = [];
  inventoryTotal = 0;
  inventoryOffset = 0;
  inventoryViewLoaded = false;
  inventoryPendingActions.clear();
  document.getElementById("inventory-summary")?.replaceChildren();
  const mobileList = document.getElementById("inventory-mobile-list");
  if (mobileList) clearInventoryList(mobileList);
  document.getElementById("inventory-table-body")?.replaceChildren();
  document.getElementById("inventory-pagination")?.replaceChildren();
  const typeFilter = querySelect("inventory-type-filter");
  const search = queryInput("inventory-search");
  if (typeFilter) typeFilter.value = "";
  if (search) search.value = "";
  document.querySelectorAll(".inventory-modal").forEach((modal) => modal.remove());
}

export function setInventoryOffset(
  offset: number,
): void {
  inventoryOffset = offset;
}

export function initInventoryTab(
  appCtx: AppContext,
): void {
  ctx = appCtx;

  document
    .getElementById("inventory-add-btn")
    ?.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openInventoryItemForm();
    });
  document
    .getElementById("inventory-type-filter")
    ?.addEventListener("change", () => {
      inventoryOffset = 0;
      void loadInventoryItems();
    });
  document
    .getElementById("inventory-search")
    ?.addEventListener("input", () => {
      inventoryOffset = 0;
      void loadInventoryItems();
    });
  inventoryDesktopLayoutQuery.addEventListener("change", () => {
    if (inventoryViewLoaded) renderInventoryView();
  });
}

export async function loadInventoryItems(): Promise<void> {
  if (!ctx) return;
  const request = createInventoryRequest();
  if (!request) return;
  const typeFilter = querySelect("inventory-type-filter")?.value || "";
  const search = queryInput("inventory-search")?.value || "";
  try {
    const params: {
      inventory_type?: string;
      q?: string;
      limit: number;
      offset: number;
    } = {
      limit: INVENTORY_PAGE_SIZE,
      offset: inventoryOffset,
    };
    if (typeFilter) params.inventory_type = typeFilter;
    if (search) params.q = search;
    const result = await listInventoryApi(params, { gardenId: request.gardenId });
    if (!isCurrentInventoryRequest(request)) return;
    if (result.total > 0 && result.items.length === 0 && inventoryOffset > 0) {
      inventoryOffset = Math.max(
        0,
        Math.floor((result.total - 1) / INVENTORY_PAGE_SIZE) * INVENTORY_PAGE_SIZE,
      );
      await loadInventoryItems();
      return;
    }
    inventoryItems = result.items;
    inventoryTotal = result.total;
    inventoryViewLoaded = true;
    renderInventoryView();
  } catch (err) {
    if (!isCurrentInventoryRequest(request)) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function renderInventoryView(): void {
  const summary = document.getElementById(
    "inventory-summary",
  );
  if (summary) {
    summary.textContent =
      inventoryTotal === 0
        ? t("inventory.summary_none")
        : t("inventory.summary_count", {
            count: inventoryTotal,
          });
  }

  const plantNames = buildPlantNameMap(ctx.getPlants());
  const isDesktop = inventoryDesktopLayoutQuery.matches;

  const mobileList = document.getElementById(
    "inventory-mobile-list",
  );
  const thead = document.getElementById(
    "inventory-table-head",
  );
  const tbody = document.getElementById(
    "inventory-table-body",
  );

  if (isDesktop) {
    if (mobileList) clearInventoryList(mobileList);
    if (thead && tbody) {
      renderInventoryTable(
        thead,
        tbody,
        inventoryItems,
        {
          ...inventoryCallbacks,
          canWrite: ctx.canWrite(),
        },
        plantNames,
      );
    }
  } else if (mobileList) {
    thead?.replaceChildren();
    tbody?.replaceChildren();
    renderInventoryList(
      mobileList,
      inventoryItems,
      {
        ...inventoryCallbacks,
        canWrite: ctx.canWrite(),
      },
      plantNames,
    );
  }

  ctx.renderDataExportBars();
  renderInventoryPagination();
}

function renderInventoryPagination(): void {
  const container = document.getElementById(
    "inventory-pagination",
  );
  if (!container) return;
  container.replaceChildren();
  if (inventoryTotal <= INVENTORY_PAGE_SIZE) return;

  const page =
    Math.floor(inventoryOffset / INVENTORY_PAGE_SIZE) +
    1;
  const totalPages = Math.ceil(
    inventoryTotal / INVENTORY_PAGE_SIZE,
  );

  const prev = document.createElement("button");
  prev.textContent = `\u2190 ${t("common.previous")}`;
  prev.disabled = inventoryOffset === 0;
  prev.addEventListener("click", () => {
    inventoryOffset = Math.max(
      0,
      inventoryOffset - INVENTORY_PAGE_SIZE,
    );
    void loadInventoryItems();
  });

  const info = document.createElement("span");
  info.textContent = t("common.page_of", {
    page,
    total: totalPages,
  });

  const next = document.createElement("button");
  next.textContent = `${t("common.next")} \u2192`;
  next.disabled =
    inventoryOffset + INVENTORY_PAGE_SIZE >=
    inventoryTotal;
  next.addEventListener("click", () => {
    inventoryOffset += INVENTORY_PAGE_SIZE;
    void loadInventoryItems();
  });

  container.append(prev, info, next);
}

const inventoryCallbacks = {
  onAddStock: (item: InventoryItem) =>
    openStockModal(item, "add"),
  onConsumeStock: (item: InventoryItem) =>
    openStockModal(item, "consume"),
  onPlantFromStock: (item: InventoryItem) =>
    openStockModal(item, "plant"),
  onEdit: (item: InventoryItem) =>
    openInventoryItemForm(item),
  onDelete: (item: InventoryItem) =>
    void deleteInventoryItem(item),
  onViewTransactions: (item: InventoryItem) =>
    void openTransactionHistory(item),
  onPlantClick: (pltId: string) => {
    const plant = ctx
      .getPlants()
      .find((p) => p.plt_id === pltId);
    if (plant) {
      ctx.focusPlantsInPlantsView([pltId]);
    }
  },
};

function openInventoryItemForm(
  existing?: InventoryItem,
): void {
  if (!ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  if (gardenId === null || (existing && existing.garden_id !== gardenId)) return;
  const modal = document.createElement("div");
  modal.className = "modal inventory-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  const content = document.createElement("div");
  content.className = "modal-content";

  const heading = document.createElement("h2");
  heading.textContent = existing
    ? t("inventory.modal_edit_item")
    : t("inventory.modal_add_item");
  content.appendChild(heading);

  function closeModal(): void {
    document.removeEventListener("keydown", onEscape);
    modal.remove();
  }
  function onEscape(e: KeyboardEvent): void {
    if (e.key === "Escape") closeModal();
  }
  document.addEventListener("keydown", onEscape);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  const plants = ctx
    .getPlants()
    .map((p) => ({
      plt_id: p.plt_id,
      name: p.name,
    }));

  const formOpts: Parameters<
    typeof createInventoryItemForm
  >[0] = {
    plants,
    onSubmit: async (data) => {
      const actionKey = existing ? `update:${existing.id}` : "create";
      if (inventoryPendingActions.has(actionKey) || !isCurrentInventoryGarden(gardenId)) return;
      inventoryPendingActions.add(actionKey);
      try {
        if (existing) {
          await updateInventoryItemApi(
            existing.id,
            data,
            { gardenId },
          );
          if (isCurrentInventoryGarden(gardenId)) {
            ctx.showToast(t("inventory.item_updated"));
          }
        } else {
          await createInventoryItemApi(data, { gardenId });
          if (isCurrentInventoryGarden(gardenId)) {
            ctx.showToast(t("inventory.item_created"));
          }
          inventoryOffset = 0;
        }
        if (!isCurrentInventoryGarden(gardenId)) return;
        closeModal();
        await loadInventoryItems();
      } catch (err) {
        if (isCurrentInventoryGarden(gardenId)) {
          ctx.showToast(getApiErrorMessage(err), "error");
        }
      } finally {
        inventoryPendingActions.delete(actionKey);
      }
    },
    onCancel: closeModal,
  };
  if (existing) formOpts.existing = existing;
  const form = createInventoryItemForm(formOpts);

  content.appendChild(form);
  modal.appendChild(content);
  document.body.appendChild(modal);
}

function openStockModal(
  item: InventoryItem,
  mode: "add" | "consume" | "plant",
): void {
  if (!ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  if (gardenId === null || item.garden_id !== gardenId) return;
  const modal = document.createElement("div");
  modal.className = "modal inventory-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  const content = document.createElement("div");
  content.className = "modal-content";

  const heading = document.createElement("h2");
  heading.textContent =
    mode === "add"
      ? t("inventory.modal_add_stock", {
          label: item.label,
        })
      : mode === "plant"
        ? t("inventory.modal_plant_stock", {
            label: item.label,
          })
        : t("inventory.modal_use_stock", {
            label: item.label,
          });
  content.appendChild(heading);

  function closeModal(): void {
    document.removeEventListener("keydown", onEscape);
    modal.remove();
  }
  function onEscape(e: KeyboardEvent): void {
    if (e.key === "Escape") closeModal();
  }
  document.addEventListener("keydown", onEscape);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  const plots = ctx
    .getPlots()
    .map((p) => ({
      plot_id: p.plot_id,
      zone_code: p.zone_code,
    }));

  let plantOperationId: string | null = null;
  const form = createStockTransactionForm({
    item,
    mode,
    plots,
    onSubmit: async (data) => {
      const actionKey = `transaction:${item.id}`;
      if (inventoryPendingActions.has(actionKey) || !isCurrentInventoryGarden(gardenId)) return;
      inventoryPendingActions.add(actionKey);
      try {
        if (mode === "plant") {
          if (!data.plot_id) return;
          plantOperationId ??= crypto.randomUUID();
          await plantFromInventoryApi(
            item.id,
            {
              quantity: absoluteDecimalString(data.delta),
              plot_id: data.plot_id,
              occurred_on: data.occurred_on,
              notes: data.notes,
            },
            { gardenId, operationId: plantOperationId },
          );
        } else {
          let journalEntryId: string | null = null;
          if (data.create_journal && item.plt_id) {
          const reasonLabel =
            data.reason ||
            (mode === "add" ? "purchased" : "planted");
          const journalBody: Parameters<
            typeof createJournalEntryApi
          >[0] = {
            event_type:
              data.reason === "planted"
                ? "planted"
                : data.reason === "harvested"
                  ? "harvested"
                  : data.reason === "divided"
                    ? "divided"
                    : "observed",
            occurred_on: data.occurred_on,
            title:
              mode === "add"
                ? t(
                    "inventory.journal_title_added",
                    {
                      quantity: absoluteDecimalString(data.delta),
                      unit: item.unit,
                      reason: reasonLabel,
                    },
                  )
                : t(
                      "inventory.journal_title_used",
                      {
                        quantity: absoluteDecimalString(data.delta),
                        unit: item.unit,
                        reason: reasonLabel,
                      },
                    ),
            plant_ids: [item.plt_id],
            plot_ids: data.plot_id
              ? [data.plot_id]
              : [],
          };
          if (data.notes)
            journalBody.notes = data.notes;
          const result =
            await createJournalEntryApi(journalBody, { gardenId });
          journalEntryId = result.id;
          }

          await addInventoryTransactionApi(
            item.id,
            {
              delta: data.delta,
              reason: data.reason,
              source_name: data.source_name,
              cost_minor: data.cost_minor,
              occurred_on: data.occurred_on,
              storage_location: data.storage_location,
              notes: data.notes,
              journal_entry_id: journalEntryId,
            },
            { gardenId },
          );
        }

        if (!isCurrentInventoryGarden(gardenId)) return;
        ctx.showToast(
          mode === "add"
            ? t("inventory.stock_added")
            : mode === "plant"
              ? t("inventory.stock_planted")
              : t("inventory.stock_used"),
        );
        closeModal();
        await loadInventoryItems();
        if (data.create_journal)
          void loadJournalEntries();
      } catch (err) {
        if (isCurrentInventoryGarden(gardenId)) {
          ctx.showToast(getApiErrorMessage(err), "error");
        }
      } finally {
        inventoryPendingActions.delete(actionKey);
      }
    },
    onCancel: closeModal,
  });

  content.appendChild(form);
  modal.appendChild(content);
  document.body.appendChild(modal);
}

async function openTransactionHistory(
  item: InventoryItem,
): Promise<void> {
  const gardenId = getActiveGardenContext();
  if (gardenId === null || item.garden_id !== gardenId) return;
  const modal = document.createElement("div");
  modal.className = "modal inventory-modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  const content = document.createElement("div");
  content.className = "modal-content";

  const heading = document.createElement("h2");
  heading.textContent = t(
    "inventory.modal_history",
    { label: item.label },
  );
  content.appendChild(heading);

  function closeModal(): void {
    document.removeEventListener("keydown", onEscape);
    modal.remove();
  }
  function onEscape(e: KeyboardEvent): void {
    if (e.key === "Escape") closeModal();
  }
  document.addEventListener("keydown", onEscape);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  const histContainer = document.createElement("div");
  histContainer.className = "inventory-tx-list";
  const loading = document.createElement("p");
  loading.textContent = t("common.loading");
  histContainer.appendChild(loading);
  content.appendChild(histContainer);

  const closeBtn = document.createElement("button");
  closeBtn.className = "btn-secondary";
  closeBtn.textContent = t("common.close");
  closeBtn.style.marginTop = "var(--sp-3)";
  closeBtn.addEventListener("click", closeModal);
  content.appendChild(closeBtn);

  modal.appendChild(content);
  document.body.appendChild(modal);

  try {
    const result =
      await listInventoryTransactionsApi(item.id, {
        limit: 100,
      }, { gardenId });
    if (!isCurrentInventoryGarden(gardenId) || !modal.isConnected) return;
    renderTransactionHistory(
      histContainer,
      result.transactions,
    );
  } catch (err) {
    if (!isCurrentInventoryGarden(gardenId) || !modal.isConnected) return;
    histContainer.textContent = t(
      "inventory.history_load_failed",
    );
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function deleteInventoryItem(
  item: InventoryItem,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  const actionKey = `delete:${item.id}`;
  if (
    gardenId === null
    || item.garden_id !== gardenId
    || inventoryPendingActions.has(actionKey)
  ) return;
  inventoryPendingActions.add(actionKey);
  try {
    const ok = await ctx.confirmDialog(
      t("inventory.delete_confirm", {
        label: item.label,
      }),
      t("common.delete"),
    );
    if (!ok) return;
    await deleteInventoryItemApi(item.id, { gardenId });
    if (!isCurrentInventoryGarden(gardenId)) return;
    ctx.showToast(t("inventory.item_deleted"));
    await loadInventoryItems();
  } catch (err) {
    if (isCurrentInventoryGarden(gardenId)) {
      ctx.showToast(getApiErrorMessage(err), "error");
    }
  } finally {
    inventoryPendingActions.delete(actionKey);
  }
}
