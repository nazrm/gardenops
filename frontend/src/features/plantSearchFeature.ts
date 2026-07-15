import type { AppContext } from "../core/appContext";
import type { Plant } from "../core/models";
import { t, formatPlantCategoryLabel } from "../core/i18n";
import { escapeHtml as esc } from "../core/sanitize";
import { createModal } from "../components/dialogCore";
import { showCreatePlantDialogLazy } from "../components/gardenDialogsLoader";
import type {
  AiPlantData,
  PlotOption,
} from "../components/overlays";
import type { PlotAssignmentMeaning } from "../services/api";
import {
  addPlantToPlotApi,
  getActiveGardenContext,
  getPlantApi,
  getApiErrorMessage,
  searchPlantCatalog,
  searchPlantsApi,
} from "../services/api";
import type { CatalogPlant, PlantSearchResult } from "../services/api";
import { showToast } from "../components/toast";

export interface PlantSearchDialogParams {
  ctx: AppContext;
  preselectedPlotId?: string | undefined;
  getNextId: () => string | Promise<string>;
  getPlotOptions: () => PlotOption[];
  getPlotAssignmentMeanings: () => PlotAssignmentMeaning[];
  getGridDims: () => { rows: number; cols: number };
  onCreateSubmit: (
    data: Record<string, string | number | boolean | null>,
    plotIds: string[],
  ) => Promise<void>;
  onAiLookup: (q: string) => Promise<AiPlantData>;
  onEditPlant: (plant: Plant) => void;
  onPlantAssigned: () => void;
  onIdentifyFromPhoto?: () => void;
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null;

function debounce(fn: () => void, ms: number): void {
  if (debounceTimer !== null) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(fn, ms);
}

function escHtml(s: string): string {
  return esc(s);
}

function renderLocalResults(
  container: HTMLElement,
  plants: PlantSearchResult[],
  params: PlantSearchDialogParams,
  closeDialog: () => void,
  gardenId: number,
): void {
  container.replaceChildren();
  if (plants.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;

  const heading = document.createElement("h4");
  heading.className = "plant-search-section-heading";
  heading.textContent = t("plants.search_section_yours");
  container.appendChild(heading);

  for (const plant of plants.slice(0, 10)) {
    const row = document.createElement("div");
    row.className = "plant-search-result";

    const info = document.createElement("div");
    info.className = "plant-search-result-info";

    const nameEl = document.createElement("strong");
    nameEl.textContent = plant.name;
    info.appendChild(nameEl);

    if (plant.latin) {
      info.appendChild(document.createTextNode(" "));
      const latin = document.createElement("em");
      latin.textContent = plant.latin;
      info.appendChild(latin);
    }

    const meta = document.createElement("div");
    meta.className = "plant-search-result-meta";

    const badge = document.createElement("span");
    badge.className = "category-badge";
    badge.textContent = formatPlantCategoryLabel(plant.category);
    meta.appendChild(badge);

    if (plant.quantity != null && plant.quantity > 0) {
      const qty = document.createElement("span");
      qty.className = "plant-search-qty";
      qty.textContent = t("plants.qty_planted", {
        count: plant.quantity,
      });
      meta.appendChild(qty);
    }

    if (plant.plot_ids && plant.plot_ids.length > 0) {
      const plots = document.createElement("span");
      plots.className = "plant-search-plots";
      plots.textContent = plant.plot_ids.join(", ");
      meta.appendChild(plots);
    }

    info.appendChild(meta);
    row.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "plant-search-result-actions";

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn-sm btn-outline";
    addBtn.textContent = t("plants.search_add_to_plot");
    addBtn.addEventListener("click", () => {
      showInlinePlotPicker(
        addBtn,
        plant.plt_id,
        params,
        closeDialog,
        gardenId,
      );
    });
    actions.appendChild(addBtn);

    const detailBtn = document.createElement("button");
    detailBtn.type = "button";
    detailBtn.className = "btn-sm btn-outline";
    detailBtn.textContent = t("plants.search_open_details");
    detailBtn.addEventListener("click", () => {
      void (async () => {
        try {
          const fullPlant = await getPlantApi(plant.plt_id, { gardenId });
          if (gardenId !== getActiveGardenContext()) return;
          closeDialog();
          params.onEditPlant(fullPlant);
        } catch (err) {
          showToast(getApiErrorMessage(err), "error");
        }
      })();
    });
    actions.appendChild(detailBtn);

    row.appendChild(actions);
    container.appendChild(row);
  }
}

function showInlinePlotPicker(
  anchor: HTMLElement,
  pltId: string,
  params: PlantSearchDialogParams,
  closeDialog: () => void,
  gardenId: number,
): void {
  const existing = anchor.parentElement?.querySelector(
    ".inline-plot-picker",
  );
  if (existing) {
    existing.remove();
    return;
  }

  const plotOptions = params.getPlotOptions();
  if (plotOptions.length === 0) return;

  const picker = document.createElement("div");
  picker.className = "inline-plot-picker";

  if (params.preselectedPlotId) {
    const preBtn = document.createElement("button");
    preBtn.type = "button";
    preBtn.className = "btn-sm btn-primary";
    preBtn.textContent = params.preselectedPlotId;
    preBtn.addEventListener("click", () => {
      void assignPlantToPlot(
        pltId,
        params.preselectedPlotId!,
        params,
        closeDialog,
        gardenId,
      );
    });
    picker.appendChild(preBtn);

    const sep = document.createElement("hr");
    sep.className = "picker-sep";
    picker.appendChild(sep);
  }

  const shown = plotOptions.slice(0, 12);
  for (const plot of shown) {
    if (plot.plot_id === params.preselectedPlotId) continue;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-sm btn-outline";
    btn.textContent = plot.plot_id;
    btn.addEventListener("click", () => {
      void assignPlantToPlot(
        pltId,
        plot.plot_id,
        params,
        closeDialog,
        gardenId,
      );
    });
    picker.appendChild(btn);
  }

  anchor.parentElement?.appendChild(picker);
}

async function assignPlantToPlot(
  pltId: string,
  plotId: string,
  params: PlantSearchDialogParams,
  closeDialog: () => void,
  gardenId: number,
): Promise<void> {
  if (gardenId !== getActiveGardenContext()) return;
  try {
    await addPlantToPlotApi(plotId, pltId, 1, null, { gardenId });
    if (gardenId !== getActiveGardenContext()) return;
    showToast(
      t("plants.search_add_to_plot") + `: ${plotId}`,
      "success",
    );
    params.onPlantAssigned();
    closeDialog();
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }
}

function renderCatalogResults(
  container: HTMLElement,
  plants: CatalogPlant[],
  params: PlantSearchDialogParams,
  closeDialog: () => void,
): void {
  container.replaceChildren();
  if (plants.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;

  const heading = document.createElement("h4");
  heading.className = "plant-search-section-heading";
  heading.textContent = t("plants.search_section_catalog");
  container.appendChild(heading);

  for (const plant of plants.slice(0, 10)) {
    const row = document.createElement("div");
    row.className = "plant-search-result";

    const info = document.createElement("div");
    info.className = "plant-search-result-info";

    const nameEl = document.createElement("strong");
    nameEl.textContent = plant.name;
    info.appendChild(nameEl);

    if (plant.latin) {
      info.appendChild(document.createTextNode(" "));
      const latin = document.createElement("em");
      latin.textContent = plant.latin;
      info.appendChild(latin);
    }

    const meta = document.createElement("div");
    meta.className = "plant-search-result-meta";
    const badge = document.createElement("span");
    badge.className = "category-badge";
    badge.textContent = formatPlantCategoryLabel(plant.category);
    meta.appendChild(badge);
    info.appendChild(meta);

    row.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "plant-search-result-actions";

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn-sm btn-primary";
    addBtn.textContent = "+";
    addBtn.title = t("plants.search_create_manual");
    addBtn.addEventListener("click", () => {
      closeDialog();
      openCreateWithPrefill(params, plant);
    });
    actions.appendChild(addBtn);

    row.appendChild(actions);
    container.appendChild(row);
  }
}

async function openCreateWithPrefill(
  params: PlantSearchDialogParams,
  template: CatalogPlant,
): Promise<void> {
  const dims = params.getGridDims();
  showCreatePlantDialogLazy({
    nextId: await params.getNextId(),
    availablePlots: params.getPlotOptions(),
    plotAssignmentMeanings: params.getPlotAssignmentMeanings(),
    gridRows: dims.rows,
    gridCols: dims.cols,
    onSubmit: params.onCreateSubmit,
    onAiLookup: params.onAiLookup,
    prefill: {
      name: template.name,
      latin: template.latin,
      category: template.category,
      bloom_month: template.bloom_month,
      color: template.color,
      hardiness: template.hardiness,
      height_cm: template.height_cm ?? 0,
      light: template.light,
      ...(template.link ? { link: template.link } : {}),
    },
    preselectedPlotIds: params.preselectedPlotId
      ? [params.preselectedPlotId]
      : undefined,
    ...(params.onIdentifyFromPhoto ? { onIdentifyFromPhoto: params.onIdentifyFromPhoto } : {}),
  });
}

async function openCreateFromQuery(
  params: PlantSearchDialogParams,
  query: string,
): Promise<void> {
  const dims = params.getGridDims();
  const prefill: Partial<AiPlantData> = {};
  if (query) prefill.name = query;

  showCreatePlantDialogLazy({
    nextId: await params.getNextId(),
    availablePlots: params.getPlotOptions(),
    plotAssignmentMeanings: params.getPlotAssignmentMeanings(),
    gridRows: dims.rows,
    gridCols: dims.cols,
    onSubmit: params.onCreateSubmit,
    onAiLookup: params.onAiLookup,
    prefill: query ? prefill : undefined,
    preselectedPlotIds: params.preselectedPlotId
      ? [params.preselectedPlotId]
      : undefined,
    ...(params.onIdentifyFromPhoto ? { onIdentifyFromPhoto: params.onIdentifyFromPhoto } : {}),
  });
}

export function showPlantSearchDialog(
  params: PlantSearchDialogParams,
): void {
  if (!params.ctx.ensureWriteAccess()) return;
  const dialogGardenId = getActiveGardenContext();
  if (dialogGardenId === null) return;

  let disposed = false;
  let searchGeneration = 0;
  const { dialog, close: rawClose } = createModal(
    t("plants.search_modal_title"),
    `<div class="modal-content plant-search-modal">
      <h3>${escHtml(t("plants.search_modal_title"))}</h3>
      <input
        type="text"
        id="plant-search-input"
        class="plant-search-input"
        placeholder="${escHtml(t("plants.search_modal_placeholder"))}"
        autocomplete="off"
      />
      <div id="plant-search-local" class="plant-search-section"
        hidden></div>
      <div id="plant-search-catalog-link-area"
        class="plant-search-catalog-link-area" hidden>
        <button type="button" id="plant-search-catalog-trigger"
          class="btn-link">
          ${escHtml(t("plants.search_catalog_link"))}
        </button>
      </div>
      <div id="plant-search-catalog" class="plant-search-section"
        hidden></div>
      <div id="plant-search-empty" class="plant-search-empty"
        hidden></div>
      <div class="plant-search-footer">
        <span class="plant-search-not-found">
          ${escHtml(t("plants.search_not_found"))}
        </span>
        <button type="button" id="plant-search-create-btn"
          class="btn-link">
          ${escHtml(t("plants.search_create_manual"))}
        </button>
      </div>
    </div>`,
  );
  const closeDialog = () => {
    disposed = true;
    searchGeneration += 1;
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    rawClose();
  };

  // Override the generic close button to use our cleanup wrapper
  const modalCloseBtn = dialog.querySelector<HTMLButtonElement>(".modal-close-btn");
  if (modalCloseBtn) {
    const fresh = modalCloseBtn.cloneNode(true) as HTMLButtonElement;
    fresh.addEventListener("click", closeDialog);
    modalCloseBtn.replaceWith(fresh);
  }

  const input = dialog.querySelector<HTMLInputElement>(
    "#plant-search-input",
  );
  const localSection = dialog.querySelector<HTMLElement>(
    "#plant-search-local",
  );
  const catalogSection = dialog.querySelector<HTMLElement>(
    "#plant-search-catalog",
  );
  const catalogLinkArea = dialog.querySelector<HTMLElement>(
    "#plant-search-catalog-link-area",
  );
  const catalogTrigger = dialog.querySelector<HTMLButtonElement>(
    "#plant-search-catalog-trigger",
  );
  const emptySection = dialog.querySelector<HTMLElement>(
    "#plant-search-empty",
  );
  const createBtn = dialog.querySelector<HTMLButtonElement>(
    "#plant-search-create-btn",
  );

  let lastLocalCount = 0;
  let catalogSearched = false;
  let catalogState: "idle" | "loading" | "available" | "empty" | "degraded" = "idle";
  let catalogError = "";
  let localError = "";

  const doSearch = (query: string) => {
    if (query.length < 2) {
      if (localSection) {
        localSection.replaceChildren();
        localSection.hidden = true;
      }
      if (catalogSection) {
        catalogSection.replaceChildren();
        catalogSection.hidden = true;
      }
      if (catalogLinkArea) catalogLinkArea.hidden = true;
      if (emptySection) emptySection.hidden = true;
      lastLocalCount = 0;
      catalogSearched = false;
      catalogState = "idle";
      catalogError = "";
      localError = "";
      return;
    }

    const generation = ++searchGeneration;
    void searchPlantsApi(query, {
      limit: 10,
      includeAssignments: true,
      gardenId: dialogGardenId,
    }).then((plants) => {
      if (
        disposed
        || generation !== searchGeneration
        || dialogGardenId !== getActiveGardenContext()
        || input?.value.trim() !== query
      ) return;
      lastLocalCount = plants.length;
      localError = "";
      if (localSection) {
        renderLocalResults(
          localSection,
          plants,
          params,
          closeDialog,
          dialogGardenId,
        );
      }

      if (plants.length < 5 && !catalogSearched) {
        void doCatalogSearch(query);
      } else if (plants.length >= 5) {
        if (catalogLinkArea) catalogLinkArea.hidden = false;
        if (catalogSection) {
          catalogSection.replaceChildren();
          catalogSection.hidden = true;
        }
        catalogSearched = false;
      }

      updateEmptyState(query, plants.length);
    }).catch((err: unknown) => {
      if (
        disposed
        || generation !== searchGeneration
        || dialogGardenId !== getActiveGardenContext()
        || input?.value.trim() !== query
      ) return;
      lastLocalCount = 0;
      localError = getApiErrorMessage(err);
      updateEmptyState(query, 0, 0);
    });
  };

  const doCatalogSearch = async (query: string) => {
    catalogSearched = true;
    catalogState = "loading";
    catalogError = "";
    const generation = searchGeneration;
    if (catalogLinkArea) catalogLinkArea.hidden = true;
    try {
      const results = await searchPlantCatalog(query);
      if (
        disposed
        || generation !== searchGeneration
        || dialogGardenId !== getActiveGardenContext()
        || input?.value.trim() !== query
      ) return;
      catalogState = results.length > 0 ? "available" : "empty";
      if (catalogSection) {
        renderCatalogResults(
          catalogSection,
          results,
          params,
          closeDialog,
        );
      }
      updateEmptyState(query, lastLocalCount, results.length);
    } catch (err) {
      if (
        disposed
        || generation !== searchGeneration
        || dialogGardenId !== getActiveGardenContext()
        || input?.value.trim() !== query
      ) return;
      catalogState = "degraded";
      catalogError = getApiErrorMessage(err);
      if (catalogSection) {
        catalogSection.replaceChildren();
        catalogSection.hidden = true;
      }
      updateEmptyState(query, lastLocalCount, 0);
    }
  };

  const updateEmptyState = (
    query: string,
    localCount: number,
    catalogCount?: number,
  ) => {
    if (!emptySection) return;
    if (catalogState === "loading") {
      emptySection.hidden = true;
      return;
    }
    const totalResults = localCount + (catalogCount ?? 0);
    if (totalResults === 0 && query.length >= 2) {
      emptySection.hidden = false;
      emptySection.replaceChildren();
      const msg = document.createElement("p");
      msg.textContent = localError
        || (catalogState === "degraded" ? catalogError : "")
        || t("plants.search_no_results", { query });
      emptySection.appendChild(msg);

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-primary";
      btn.textContent = t("plants.search_create_from_query", {
        query,
      });
      btn.addEventListener("click", () => {
        closeDialog();
        openCreateFromQuery(params, query);
      });
      emptySection.appendChild(btn);
    } else {
      emptySection.hidden = true;
    }
  };

  input?.addEventListener("input", () => {
    const query = input.value.trim();
    debounce(() => doSearch(query), 300);
  });

  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const query = input.value.trim();
      if (query.length === 0) {
        closeDialog();
        openCreateFromQuery(params, "");
      }
    }
  });

  catalogTrigger?.addEventListener("click", () => {
    const query = input?.value.trim() ?? "";
    if (query.length >= 2) {
      void doCatalogSearch(query);
    }
  });

  createBtn?.addEventListener("click", () => {
    const query = input?.value.trim() ?? "";
    closeDialog();
    openCreateFromQuery(params, query);
  });

  input?.focus();
}

export function initPlantSearchFeature(
  _ctx: AppContext,
): void {
  // Initialization hook for the module pattern.
  // The dialog is opened imperatively via showPlantSearchDialog,
  // so no DOM wiring is needed here.
}
