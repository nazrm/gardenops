import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type { SavedView, SavedViewPreset } from "../core/models";
import { t } from "../core/i18n";
import { renderSavedViewsDropdown } from "../components/savedViews";
import {
  ApiError,
  fetchSavedViewsApi,
  fetchSavedViewPresetsApi,
  createSavedViewApi,
  deleteSavedViewApi,
  getApiErrorMessage,
} from "../services/api";
import {
  loadTasks,
  getTasksView,
  setTasksView,
  setTasksOffset,
} from "../tabs/tasksTab";
import {
  loadHarvest,
  setHarvestOffset,
} from "../tabs/harvestTab";
import {
  loadProcurement,
  setProcurementOffset,
} from "../tabs/procurementTab";

let ctx: AppContext;

let savedViews: SavedView[] = [];
let savedViewPresets: SavedViewPreset[] = [];

const TRANSIENT_SAVED_VIEWS_STATUSES = new Set([0, 502, 503, 504]);

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isTransientSavedViewsError(err: unknown): boolean {
  return err instanceof ApiError
    && TRANSIENT_SAVED_VIEWS_STATUSES.has(err.status);
}

export function initSavedViewsFeature(
  appCtx: AppContext,
): void {
  ctx = appCtx;

  document
    .getElementById("saved-views-trigger")
    ?.addEventListener("click", () => {
      void loadSavedViews().then(() =>
        toggleSavedViewsDropdown(),
      );
    });
  document.addEventListener("click", (e) => {
    const dropdown = document.getElementById(
      "saved-views-dropdown",
    );
    const trigger = document.getElementById(
      "saved-views-trigger",
    );
    if (
      dropdown &&
      trigger &&
      !dropdown.hidden &&
      !dropdown.contains(e.target as Node) &&
      !trigger.contains(e.target as Node)
    ) {
      dropdown.hidden = true;
    }
  });
}

export async function loadSavedViews(): Promise<void> {
  const retryDelaysMs = [0, 300, 900];
  for (const [index, delayMs] of retryDelaysMs.entries()) {
    if (delayMs > 0) {
      await wait(delayMs);
    }
    try {
      const [viewsResult, presetsResult] =
        await Promise.all([
          fetchSavedViewsApi(),
          fetchSavedViewPresetsApi(),
        ]);
      savedViews = viewsResult.views;
      savedViewPresets = presetsResult.presets;
      return;
    } catch (err) {
      const finalAttempt = index === retryDelaysMs.length - 1;
      if (!isTransientSavedViewsError(err) || finalAttempt) {
        // Non-critical: keep the previously loaded state if the view service is unavailable.
        return;
      }
    }
  }
}

function getSavedViewsCallbacks() {
  return {
    onApply: (view: SavedView | SavedViewPreset) => {
      applySavedViewFilters(view);
      const dropdown = document.getElementById(
        "saved-views-dropdown",
      );
      if (dropdown) dropdown.hidden = true;
    },
    onSave: async (
      _viewType: string,
      label: string,
      _filters: Record<string, unknown>,
    ) => {
      try {
        const currentFilters =
          getCurrentFiltersForMode();
        await createSavedViewApi({
          view_type: ctx.getSubMode(),
          label,
          filter_json: currentFilters,
        });
        ctx.showToast(
          t("saved_views.saved"),
          "success",
        );
        await loadSavedViews();
        const dropdown = document.getElementById(
          "saved-views-dropdown",
        );
        if (dropdown && !dropdown.hidden) {
          renderSavedViewsDropdown(
            dropdown,
            savedViews,
            savedViewPresets,
            ctx.getSubMode(),
            getSavedViewsCallbacks(),
          );
        }
      } catch (err) {
        ctx.showToast(
          getApiErrorMessage(err),
          "error",
        );
      }
    },
    onDelete: async (view: SavedView) => {
      try {
        await deleteSavedViewApi(view.id);
        ctx.showToast(
          t("saved_views.deleted"),
          "success",
        );
        await loadSavedViews();
        const dropdown = document.getElementById(
          "saved-views-dropdown",
        );
        if (dropdown && !dropdown.hidden) {
          renderSavedViewsDropdown(
            dropdown,
            savedViews,
            savedViewPresets,
            ctx.getSubMode(),
            getSavedViewsCallbacks(),
          );
        }
      } catch (err) {
        ctx.showToast(
          getApiErrorMessage(err),
          "error",
        );
      }
    },
  };
}

function toggleSavedViewsDropdown(): void {
  const dropdown = document.getElementById(
    "saved-views-dropdown",
  );
  if (!dropdown) return;
  const isHidden = dropdown.hidden;
  dropdown.hidden = !isHidden;
  if (isHidden) {
    renderSavedViewsDropdown(
      dropdown,
      savedViews,
      savedViewPresets,
      ctx.getSubMode(),
      getSavedViewsCallbacks(),
    );
  }
}

function applySavedViewFilters(
  view: SavedView | SavedViewPreset,
): void {
  const filters = view.filter_json;
  const viewType = view.view_type;

  ctx.navigateToSubMode(
    viewType as ReturnType<typeof ctx.getSubMode>,
    { triggerLoads: false },
  );

  if (viewType === "plants") {
    ctx.clearFocusedPlantIds();
    ctx.clearPlantSelection();
    const search = queryInput("plants-search");
    if (search)
      search.value = filters["q"]
        ? String(filters["q"])
        : "";
    const category = querySelect("plants-category");
    if (category)
      category.value = filters["category"]
        ? String(filters["category"])
        : "";
    const presence = querySelect("plants-presence-filter");
    if (presence)
      presence.value = filters["presence"]
        ? String(filters["presence"])
        : "all";
    ctx.renderPlantsTable();
  } else if (viewType === "tasks") {
    setTasksView(
      filters["view"]
        ? String(filters["view"])
        : "today",
    );
    document
      .querySelectorAll<HTMLButtonElement>(
        "[data-tasks-view]",
      )
      .forEach((btn) => {
        btn.classList.toggle(
          "active",
          btn.dataset["tasksView"] === getTasksView(),
        );
      });
    const typeFilter = querySelect("tasks-filter-type");
    if (typeFilter)
      typeFilter.value = filters["task_type"]
        ? String(filters["task_type"])
        : "";
    const statusFilter = querySelect("tasks-filter-status");
    if (statusFilter)
      statusFilter.value = filters["status"]
        ? String(filters["status"])
        : "";
    setTasksOffset(0);
    void loadTasks();
  } else if (viewType === "calendar") {
    const recentHistory = document.getElementById(
      "calendar-recent-history",
    ) as HTMLInputElement | null;
    if (recentHistory && typeof filters["include_recent_history"] === "boolean") {
      recentHistory.checked = Boolean(filters["include_recent_history"]);
    }
    const presetSelect = querySelect("calendar-preset-select");
    if (presetSelect && filters["preset_key"]) {
      presetSelect.value = String(filters["preset_key"]);
    }
    document
      .querySelectorAll<HTMLButtonElement>("[data-calendar-view]")
      .forEach((btn) => {
        btn.classList.toggle(
          "active",
          btn.dataset["calendarView"] === String(filters["view_mode"] || "month"),
        );
      });
    document
      .querySelectorAll<HTMLInputElement>("[data-calendar-source]")
      .forEach((checkbox) => {
        const nextChecked = Array.isArray(filters["visible_sources"])
          ? (filters["visible_sources"] as unknown[])
            .map((value) => String(value))
            .includes(checkbox.dataset["calendarSource"] || "")
          : false;
        checkbox.checked = nextChecked;
      });
    void import("../tabs/calendarTab").then((mod) => {
      mod.applyCalendarSavedView(filters);
    });
  } else if (viewType === "journal") {
    const typeFilter = querySelect("journal-filter-type");
    if (typeFilter) typeFilter.value = filters["event_type"] ? String(filters["event_type"]) : "";
    const searchFilter = queryInput("journal-filter-search");
    if (searchFilter) searchFilter.value = filters["q"] ? String(filters["q"]) : "";
    const actorFilter = queryInput("journal-filter-actor");
    if (actorFilter) actorFilter.value = filters["actor"] ? String(filters["actor"]) : "";
    const fromFilter = queryInput("journal-filter-from");
    if (fromFilter) fromFilter.value = filters["date_from"] ? String(filters["date_from"]) : "";
    const toFilter = queryInput("journal-filter-to");
    if (toFilter) toFilter.value = filters["date_to"] ? String(filters["date_to"]) : "";
    void ctx.setJournalOffset(0).then(() =>
      ctx.loadJournalEntries(),
    );
  } else if (viewType === "issues") {
    const statusF = querySelect("issues-filter-status");
    if (statusF)
      statusF.value = filters["status"]
        ? String(filters["status"])
        : "";
    const typeF = querySelect("issues-filter-type");
    if (typeF)
      typeF.value = filters["issue_type"]
        ? String(filters["issue_type"])
        : "";
    const sevF = querySelect("issues-filter-severity");
    if (sevF)
      sevF.value = filters["severity"]
        ? String(filters["severity"])
        : "";
    void ctx.setIssuesOffset(0).then(() =>
      ctx.loadIssues(),
    );
  } else if (viewType === "inventory") {
    const typeF = querySelect("inventory-type-filter");
    if (typeF)
      typeF.value = filters["inventory_type"]
        ? String(filters["inventory_type"])
        : "";
    const searchF = queryInput("inventory-search");
    if (searchF)
      searchF.value = filters["q"]
        ? String(filters["q"])
        : "";
    ctx.setInventoryOffset(0);
    void ctx.loadInventoryItems();
  } else if (viewType === "harvest") {
    const qualF = querySelect("harvest-filter-quality");
    if (qualF)
      qualF.value = filters["quality"]
        ? String(filters["quality"])
        : "";
    const fromF = queryInput("harvest-filter-from");
    if (fromF)
      fromF.value = filters["date_from"]
        ? String(filters["date_from"])
        : "";
    const toF = queryInput("harvest-filter-to");
    if (toF)
      toF.value = filters["date_to"]
        ? String(filters["date_to"])
        : "";
    setHarvestOffset(0);
    void loadHarvest();
  } else if (viewType === "procurement") {
    const statusF = querySelect("procurement-filter-status");
    if (statusF)
      statusF.value = filters["status"]
        ? String(filters["status"])
        : "";
    const typeF = querySelect("procurement-filter-type");
    if (typeF)
      typeF.value = filters["inventory_type"]
        ? String(filters["inventory_type"])
        : "";
    setProcurementOffset(0);
    void loadProcurement();
  }
}

function getCurrentFiltersForMode(): Record<
  string,
  unknown
> {
  const mode = ctx.getSubMode();
  if (mode === "plants") {
    const search = queryInput("plants-search")?.value || "";
    return {
      q: search,
      category: querySelect("plants-category")?.value || "",
      presence: querySelect("plants-presence-filter")?.value || "all",
    };
  } else if (mode === "tasks") {
    return {
      view: getTasksView(),
      task_type: querySelect("tasks-filter-type")?.value || "",
      status: querySelect("tasks-filter-status")?.value || "",
    };
  } else if (mode === "calendar") {
    const activeView =
      document.querySelector<HTMLButtonElement>("[data-calendar-view].active")?.dataset["calendarView"]
      || "month";
    const visibleSources = Array.from(
      document.querySelectorAll<HTMLInputElement>("[data-calendar-source]:checked"),
    )
      .map((checkbox) => checkbox.dataset["calendarSource"] || "")
      .filter((value) => value.length > 0);
    return {
      view_mode: activeView,
      preset_key: querySelect("calendar-preset-select")?.value || "essential",
      visible_sources: visibleSources,
      include_recent_history:
        (document.getElementById("calendar-recent-history") as HTMLInputElement | null)?.checked
        ?? true,
      selected_plant_ids: (() => {
        const raw = document.getElementById("calendar-plant-filter")?.dataset["calendarSelectedPlantIds"];
        if (!raw) return [];
        try {
          const parsed = JSON.parse(raw);
          return Array.isArray(parsed) ? parsed.map((value) => String(value)) : [];
        } catch {
          return [];
        }
      })(),
      selected_plot_ids: (() => {
        const raw = document.getElementById("calendar-plot-filter")?.dataset["calendarSelectedPlotIds"];
        if (!raw) return [];
        try {
          const parsed = JSON.parse(raw);
          return Array.isArray(parsed) ? parsed.map((value) => String(value)) : [];
        } catch {
          return [];
        }
      })(),
      selected_zone_codes: (() => {
        const raw = document.getElementById("calendar-zone-filter")?.dataset["calendarSelectedZoneCodes"];
        if (!raw) return [];
        try {
          const parsed = JSON.parse(raw);
          return Array.isArray(parsed) ? parsed.map((value) => String(value)) : [];
        } catch {
          return [];
        }
      })(),
    };
  } else if (mode === "journal") {
    return {
      event_type: querySelect("journal-filter-type")?.value || "",
      q: queryInput("journal-filter-search")?.value.trim() || "",
      actor: queryInput("journal-filter-actor")?.value.trim() || "",
      date_from: queryInput("journal-filter-from")?.value || "",
      date_to: queryInput("journal-filter-to")?.value || "",
    };
  } else if (mode === "issues") {
    return {
      status: querySelect("issues-filter-status")?.value || "",
      issue_type: querySelect("issues-filter-type")?.value || "",
      severity: querySelect("issues-filter-severity")?.value || "",
    };
  } else if (mode === "harvest") {
    return {
      quality: querySelect("harvest-filter-quality")?.value || "",
      date_from: queryInput("harvest-filter-from")?.value || "",
      date_to: queryInput("harvest-filter-to")?.value || "",
    };
  } else if (mode === "procurement") {
    return {
      status: querySelect("procurement-filter-status")?.value || "",
      inventory_type: querySelect("procurement-filter-type")?.value || "",
    };
  } else if (mode === "inventory") {
    return {
      inventory_type: querySelect("inventory-type-filter")?.value || "",
      q: queryInput("inventory-search")?.value.trim() || "",
    };
  }
  return {};
}
