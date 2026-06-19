import type { AppContext } from "../core/appContext";
import { queryButton, queryInput, querySelect } from "../core/dom";
import type { Plant } from "../core/models";
import type {
  CareSortDir,
  CareSortField,
} from "../components/careTable";
import { t } from "../core/i18n";
import {
  filterCarePlants,
  renderCareMobileCards,
  renderCareTableBody,
  renderCareTableHead,
  showCareOverlay,
  sortCarePlants,
} from "../components/careTable";
import {
  generateMissingCareInstructionsApi,
  getApiErrorMessage,
  getPlants,
} from "../services/api";

let ctx: AppContext;

let careSortField: CareSortField = "name";
let careSortDir: CareSortDir = "asc";
let generatingMissingCare = false;
let generatingMissingCareCompleted = 0;
let generatingMissingCareTotal = 0;
const CARE_GENERATION_REQUEST_BATCH_SIZE = 6;
let careTableHeadInitialized = false;
let careRenderSignature = "";

export function initCareTab(appCtx: AppContext): void {
  ctx = appCtx;

  const careSearch = queryInput("care-search");
  const careCategory = querySelect("care-category");
  const careSortFieldSelect = querySelect("care-sort-field");
  const careSortDirBtn = queryButton("care-sort-dir");
  const generateCareBtn = queryButton("generate-care-btn");

  careSearch?.addEventListener("input", () => {
    ctx.clearFocusedPlantIds();
    renderCareView();
  });
  careCategory?.addEventListener("change", () => {
    ctx.clearFocusedPlantIds();
    renderCareView();
  });
  careSortFieldSelect?.addEventListener("change", () => {
    careSortField =
      careSortFieldSelect.value as CareSortField;
    renderCareView();
  });
  careSortDirBtn?.addEventListener("click", () => {
    careSortDir = careSortDir === "asc" ? "desc" : "asc";
    renderCareView();
  });
  generateCareBtn?.addEventListener("click", () => {
    void generateMissingCareInstructions();
  });
}

export async function loadCare(): Promise<void> {
  if (!ctx) return;
  await ctx.ensurePlantsCacheLoaded();
  renderCareView();
}

function plantHasCareInstructions(
  plant: Plant,
): boolean {
  return Boolean(
    plant.care_watering.trim() ||
      plant.care_soil.trim() ||
      plant.care_planting.trim() ||
      plant.care_maintenance.trim() ||
      plant.care_notes.trim(),
  );
}

function updateCareSummary(
  totalCount: number,
  shownCount: number,
  missingCount: number,
): void {
  const summary = document.getElementById("care-summary");
  if (!summary) return;
  const shownLabel =
    shownCount === totalCount
      ? t("care.summary_all", { count: totalCount })
      : t("care.summary_filtered", {
          shown: shownCount,
          total: totalCount,
        });
  const missingLabel =
    missingCount === 1
      ? t("care.summary_missing_one")
      : t("care.summary_missing_many", {
          count: missingCount,
        });
  summary.textContent = `${shownLabel} · ${missingLabel}`;
}

function syncGenerateCareButton(
  missingCount: number,
): void {
  const button = document.getElementById(
    "generate-care-btn",
  );
  if (!(button instanceof HTMLButtonElement)) return;
  if (generatingMissingCare) {
    if (generatingMissingCareTotal > 0) {
      button.textContent = t(
        "care.generating_progress",
        {
          completed: generatingMissingCareCompleted,
          total: generatingMissingCareTotal,
        },
      );
    } else {
      button.textContent = t("care.generating");
    }
    button.title = t("care.generating_title");
  } else if (ctx.getPlants().length === 0) {
    button.textContent = t("care.loading");
    button.title = t("care.loading_title");
  } else if (missingCount > 0) {
    button.textContent = t("care.generate_missing", {
      count: missingCount,
    });
    button.title = t("care.generate_missing_title", {
      count: missingCount,
    });
  } else {
    button.textContent = t("care.all_generated");
    button.title = t("care.all_generated_title");
  }
  button.disabled =
    generatingMissingCare ||
    ctx.getPlants().length === 0 ||
    !ctx.canWrite();
  if (!ctx.canWrite()) {
    button.title = t("care.read_only_title");
  }
}

function renderCareGenerationProgress(): void {
  const container = document.getElementById(
    "care-generation-progress",
  );
  const label = document.getElementById(
    "care-generation-label",
  );
  const count = document.getElementById(
    "care-generation-count",
  );
  const bar = document.getElementById(
    "care-generation-bar",
  );
  if (
    !(container instanceof HTMLElement) ||
    !(label instanceof HTMLElement) ||
    !(count instanceof HTMLElement) ||
    !(bar instanceof HTMLProgressElement)
  ) {
    return;
  }
  if (
    !generatingMissingCare ||
    generatingMissingCareTotal <= 0
  ) {
    container.hidden = true;
    bar.max = 1;
    bar.value = 0;
    label.textContent = t("care.progress_preparing");
    count.textContent = "0 / 0";
    return;
  }
  const completed = Math.max(
    0,
    Math.min(
      generatingMissingCareCompleted,
      generatingMissingCareTotal,
    ),
  );
  container.hidden = false;
  bar.max = Math.max(1, generatingMissingCareTotal);
  bar.value = completed;
  label.textContent =
    completed >= generatingMissingCareTotal
      ? t("care.progress_finalizing")
      : t("care.progress_generating");
  count.textContent = `${completed} / ${generatingMissingCareTotal}`;
}

export function renderCareView(): void {
  const thead = document.getElementById(
    "care-table-head",
  );
  const tbody = document.getElementById(
    "care-table-body",
  );
  const mobileList = document.getElementById(
    "care-mobile-list",
  );
  if (!tbody || !mobileList) return;

  if (thead && !careTableHeadInitialized) {
    renderCareTableHead(thead);
    thead
      .querySelectorAll("th.sortable")
      .forEach((th) => {
        th.addEventListener("click", handleCareSortClick);
      });
    careTableHeadInitialized = true;
  }

  const query = (queryInput("care-search")?.value ?? "").trim();
  const category = querySelect("care-category")?.value ?? "";
  const plants = ctx.getPlants();
  const filtered = filterCarePlants(
    ctx.applyFocusedPlantFilter(plants),
    query,
    category,
  );
  const sorted = sortCarePlants(
    filtered,
    careSortField,
    careSortDir,
  );
  const missingCount = plants.filter(
    (plant) => !plantHasCareInstructions(plant),
  ).length;
  updateCareSummary(plants.length, sorted.length, missingCount);
  syncGenerateCareButton(missingCount);
  renderCareGenerationProgress();
  const layoutMode = ctx.isMobile() ? "mobile" : "desktop";
  const nextRenderSignature = JSON.stringify({
    cacheRevision: ctx.getPlantsCacheRevision(),
    layoutMode,
    query,
    category,
    sortField: careSortField,
    sortDir: careSortDir,
    canWrite: ctx.canWrite(),
    generatingMissingCare,
    generatingMissingCareCompleted,
    generatingMissingCareTotal,
    plants: sorted.map((plant) => plant.plt_id).join("|"),
  });
  if (careRenderSignature !== nextRenderSignature) {
    const callbacks = {
      onPlantClick: (plant: Plant) => showCareOverlay(plant),
    };
    if (layoutMode === "desktop") {
      renderCareTableBody(tbody, sorted, callbacks);
      if (mobileList.childElementCount > 0) mobileList.replaceChildren();
    } else {
      if (tbody.childElementCount > 0) tbody.replaceChildren();
      renderCareMobileCards(mobileList, sorted, callbacks);
    }
    careRenderSignature = nextRenderSignature;
  }
  updateCareSortIndicators();
}

function handleCareSortClick(e: Event): void {
  const th = (e.currentTarget as HTMLElement).closest(
    "th",
  );
  const field = th?.dataset["sort"] as
    | CareSortField
    | undefined;
  if (!field) return;
  if (field === careSortField) {
    careSortDir = careSortDir === "asc" ? "desc" : "asc";
  } else {
    careSortField = field;
    careSortDir = "asc";
  }
  renderCareView();
}

function updateCareSortIndicators(): void {
  document
    .querySelectorAll<HTMLTableCellElement>(
      "#care-table-head th.sortable",
    )
    .forEach((th) => {
      th.classList.remove("sort-asc", "sort-desc");
      if (th.dataset["sort"] === careSortField) {
        th.classList.add(
          careSortDir === "asc"
            ? "sort-asc"
            : "sort-desc",
        );
      }
    });
  const fieldSelect = querySelect("care-sort-field");
  const dirBtn = queryButton("care-sort-dir");
  if (fieldSelect) fieldSelect.value = careSortField;
  if (dirBtn) {
    dirBtn.textContent =
      careSortDir === "asc"
        ? t("common.asc")
        : t("common.desc");
    dirBtn.setAttribute(
      "aria-label",
      t("care.sort_toggle_current", {
        direction:
          careSortDir === "asc"
            ? t("common.asc").toLowerCase()
            : t("common.desc").toLowerCase(),
      }),
    );
  }
}

async function generateMissingCareInstructions(): Promise<void> {
  if (generatingMissingCare) return;
  if (!ctx.ensureWriteAccess()) return;

  const plants = ctx.getPlants();
  const initialMissingCount = plants.filter(
    (plant) => !plantHasCareInstructions(plant),
  ).length;
  const regenerate = initialMissingCount === 0;
  const totalToProcess = regenerate
    ? plants.length
    : initialMissingCount;

  generatingMissingCare = true;
  generatingMissingCareCompleted = 0;
  generatingMissingCareTotal = totalToProcess;
  renderCareView();
  try {
    let result =
      await generateMissingCareInstructionsApi({
        maxPlants: CARE_GENERATION_REQUEST_BATCH_SIZE,
        regenerate,
      });
    let totalGenerated = result.generated;
    generatingMissingCareTotal = Math.max(
      totalToProcess,
      result.missing_before,
    );
    generatingMissingCareCompleted =
      generatingMissingCareTotal -
      result.remaining_without_care;
    renderCareView();

    while (result.has_more) {
      if (
        result.generated === 0 &&
        result.attempted === 0
      ) {
        throw new Error(
          "Care generation did not process any plants.",
        );
      }
      result = await generateMissingCareInstructionsApi({
        maxPlants: CARE_GENERATION_REQUEST_BATCH_SIZE,
        regenerate,
      });
      totalGenerated += result.generated;
      generatingMissingCareTotal = Math.max(
        generatingMissingCareTotal,
        result.missing_before,
      );
      generatingMissingCareCompleted =
        generatingMissingCareTotal -
        result.remaining_without_care;
      renderCareView();
    }

    ctx.invalidatePlantsCache();
    ctx.setPlantsCache(await getPlants());
    const remainingCount = result.remaining_without_care;
    if (result.status === "partial") {
      ctx.showToast(
        t("care.generated_partial_failure", {
          generated: totalGenerated,
          remaining: remainingCount,
        }),
        "error",
      );
    } else if (totalGenerated === 0) {
      ctx.showToast(
        t("care.all_already_generated"),
        "success",
      );
    } else if (remainingCount > 0) {
      ctx.showToast(
        t("care.generated_partial", {
          generated: totalGenerated,
          remaining: remainingCount,
        }),
        "success",
      );
    } else {
      ctx.showToast(
        t("care.generated_success", {
          count: totalGenerated,
        }),
        "success",
      );
    }
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  } finally {
    generatingMissingCare = false;
    generatingMissingCareCompleted = 0;
    generatingMissingCareTotal = 0;
    renderCareView();
  }
}

export function openCareForPlants(
  pltIds: string[],
): void {
  ctx.setFocusedPlantIds(pltIds);
  const search = queryInput("care-search");
  const category = querySelect("care-category");
  if (search) search.value = "";
  if (category) category.value = "";
  ctx.navigateToSubMode("care");
  renderCareView();
}
