import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type { GardenProfile, PlannerResult } from "../core/models";
import type { PlannerCallbacks, WorkflowCallbacks } from "../components/planner";
import type { GardenerReportsCallbacks } from "../components/reports";
import type { StatisticsCallbacks } from "../components/statistics";
import type { StatisticsActions } from "../services/api";
import { t } from "../core/i18n";
import { renderStatistics } from "../components/statistics";
import { renderPlannerDashboard } from "../components/planner";
import { renderGardenerReports } from "../components/reports";
import {
  getApiErrorMessage,
  fetchIssueApi,
  fetchTaskApi,
  getStatisticsActionsApi,
  fetchPlannerSuggestionsApi,
  fetchGardenProfileApi,
  fetchGardenerReportsApi,
  fetchCompanionCheckApi,
  fetchTodayDashboardApi,
  fetchAvailableWorkflowsApi,
  startWorkflowApi,
} from "../services/api";
import type { AvailableWorkflow } from "../services/api";
import type { TodayDashboard } from "../services/api";
import { renderTodayDashboard } from "../components/today";
import {
  loadTasks,
  setTasksView,
  setTasksOffset,
  syncTasksViewButtons,
} from "../tabs/tasksTab";
import {
  loadHarvest,
  setHarvestOffset,
  openHarvestSummaryPanel,
} from "../tabs/harvestTab";
type StatsMode = "today" | "overview" | "reports" | "planner";

let ctx: AppContext;
let statsMode: StatsMode = "today";

let statisticsActionsCache: StatisticsActions | null =
  null;
let gardenerReportsZoneCode = "";
let plannerResult: PlannerResult | null = null;
let gardenProfile: GardenProfile | null = null;
let plannerGoal = "";

export function initStatisticsTab(
  appCtx: AppContext,
): void {
  ctx = appCtx;
  document
    .querySelectorAll<HTMLButtonElement>("[data-stats-mode]")
    .forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.dataset["statsMode"] as StatsMode;
        setStatsMode(mode);
      });
    });
}

function setStatsMode(mode: StatsMode): void {
  statsMode = mode;
  document
    .querySelectorAll<HTMLButtonElement>("[data-stats-mode]")
    .forEach((btn) => {
      btn.setAttribute(
        "aria-selected",
        btn.dataset["statsMode"] === mode ? "true" : "false",
      );
    });
  const today = document.getElementById("today-dashboard");
  const overview = document.getElementById("statistics-content");
  const reports = document.getElementById("reports-dashboard");
  const planner = document.getElementById("planner-dashboard");
  if (today) today.hidden = mode !== "today";
  if (overview) overview.hidden = mode !== "overview";
  if (reports) reports.hidden = mode !== "reports";
  if (planner) planner.hidden = mode !== "planner";
  const scrollRegion = document.querySelector(
    ".statistics-scroll-region",
  );
  if (scrollRegion) scrollRegion.scrollTop = 0;
}

export function getStatisticsActionsCache(): StatisticsActions | null {
  return statisticsActionsCache;
}

export function getStatisticsCallbacks(): StatisticsCallbacks {
  return statisticsCallbacks;
}

export function resetStatisticsState(): void {
  statisticsActionsCache = null;
  gardenerReportsZoneCode = "";
}

export function getGardenerReportsZoneCode(): string {
  return gardenerReportsZoneCode;
}

export async function loadStatistics(): Promise<void> {
  if (!ctx) return;
  if (ctx.state.plantsCache.length === 0) {
    await ctx.ensurePlantsCacheLoaded();
  }
  let actions: StatisticsActions | null = null;
  try {
    actions = await getStatisticsActionsApi();
  } catch {
    // degrade gracefully
  }
  statisticsActionsCache = actions;
  const container = document.getElementById(
    "statistics-content",
  );
  if (container) {
    renderStatistics(
      container,
      ctx.state.plots,
      ctx.state.plantsCache,
      actions,
      statisticsCallbacks,
    );
  }
  ctx.renderDataExportBars();
  void loadTodayDashboard();
  void loadGardenerReports(
    gardenerReportsZoneCode || undefined,
  );
  void loadPlanner();
}

async function loadTodayDashboard(): Promise<void> {
  const container = document.getElementById(
    "today-dashboard",
  );
  if (!container) return;
  try {
    const data: TodayDashboard =
      await fetchTodayDashboardApi();
    renderTodayDashboard(container, data, {
      onTaskClick: async (taskId) => {
        ctx.navigateToSubMode("tasks");
        void loadTasks();
        try {
          const task = await fetchTaskApi(taskId);
          await ctx.openTaskForm(task);
        } catch {
          // keep the task list open as fallback
        }
      },
      onIssueClick: async (issueId) => {
        ctx.navigateToSubMode("issues");
        void ctx.loadIssues();
        try {
          const issue = await fetchIssueApi(issueId);
          await ctx.openIssueForm(issue);
        } catch {
          // keep the issues list open as fallback
        }
      },
      onWeatherClick: () => {
        ctx.navigateToSubMode("care");
        void ctx.loadCare();
      },
    });
  } catch {
    container.replaceChildren();
  }
}

async function loadGardenerReports(
  zoneCode?: string,
): Promise<void> {
  const container = document.getElementById(
    "reports-dashboard",
  );
  if (!container) return;
  try {
    const result = await fetchGardenerReportsApi(
      zoneCode ? { zone_code: zoneCode } : undefined,
    );
    gardenerReportsZoneCode = result.zone_code ?? "";
    renderGardenerReports(
      container,
      result,
      gardenerReportsCallbacks,
    );
  } catch {
    container.replaceChildren();
  }
}

async function loadPlanner(
  goal?: string,
): Promise<void> {
  try {
    const params: Record<string, string | number> = {
      limit: 10,
    };
    if (goal) params["goal"] = goal;
    if (ctx.state.sunlitPlotIds.size > 0) {
      params["sunlit_plot_ids"] = Array.from(
        ctx.state.sunlitPlotIds,
      ).join(",");
    }
    const [result, profile, wfResp] = await Promise.all([
      fetchPlannerSuggestionsApi(params),
      fetchGardenProfileApi(),
      fetchAvailableWorkflowsApi().catch(
        () => ({ workflows: [] as AvailableWorkflow[] }),
      ),
    ]);
    plannerResult = result;
    gardenProfile = profile;
    plannerGoal = goal || "";
    const container = document.getElementById(
      "planner-dashboard",
    );
    if (container && plannerResult && gardenProfile) {
      const cbs: PlannerCallbacks = {
        onPlantClick: (pltId) => {
          ctx.focusPlantsInPlantsView([pltId]);
        },
        onPlotClick: (plotId) => {
          ctx.setActiveTab("map");
          void ctx.selectPlot(plotId);
        },
        onRefresh: (newGoal) =>
          void loadPlanner(newGoal),
        onPreviewCandidate: (plotId, suggestion) => {
          ctx.state.highlightedPlotIds = new Set([
            plotId,
          ]);
          ctx.setActiveTab("map");
          ctx.renderPlots();
          void ctx.selectPlot(plotId);
          ctx.showAppStatus(
            `${suggestion.name} \u2192 ${plotId}`,
            t("planner.check_fit"),
            () =>
              void cbs.onInspectCandidate(
                plotId,
                suggestion,
              ),
          );
        },
        onInspectCandidate: (plotId, suggestion) => {
          void (async () => {
            try {
              const fit =
                await fetchCompanionCheckApi({
                  plot_id: plotId,
                  plt_id: suggestion.plt_id,
                });
              const companionCopy = fit.companions
                .map((item) => item.description)
                .join(" · ");
              const conflictCopy = fit.conflicts
                .map((item) => item.description)
                .join(" · ");
              const parts: string[] = [];
              if (companionCopy)
                parts.push(
                  `${t("planner.fit_good")}: ${companionCopy}`,
                );
              if (conflictCopy)
                parts.push(
                  `${t("planner.fit_conflict")}: ${conflictCopy}`,
                );
              if (parts.length === 0)
                parts.push(t("planner.fit_none"));
              ctx.showAppStatus(
                `${suggestion.name} · ${parts.join(" | ")}`,
                t("planner.preview_on_map"),
                () =>
                  cbs.onPreviewCandidate(
                    plotId,
                    suggestion,
                  ),
              );
            } catch (err) {
              ctx.showToast(
                getApiErrorMessage(err),
                "error",
              );
            }
          })();
        },
      };
      const wfCbs: WorkflowCallbacks = {
        onStart: (workflowId, selectedSteps) => {
          void (async () => {
            try {
              const res = await startWorkflowApi(
                workflowId,
                selectedSteps,
              );
              ctx.showToast(
                t("workflow.started", {
                  count: res.created,
                }),
              );
              void loadPlanner(plannerGoal || undefined);
            } catch (err) {
              ctx.showToast(
                getApiErrorMessage(err),
                "error",
              );
            }
          })();
        },
      };
      renderPlannerDashboard(
        container,
        plannerResult,
        gardenProfile,
        cbs,
        plannerGoal,
        wfResp.workflows,
        wfCbs,
      );
    }
  } catch {
    // Non-critical feature — degrade gracefully
  }
}

const statisticsCallbacks: StatisticsCallbacks = {
  onFilterPlants: (pltIds, _label) => {
    ctx.focusPlantsInPlantsView(pltIds);
  },
  onNavigateMap: (plotIds) => {
    ctx.openMapForPlots(plotIds);
  },
  onNavigateCare: (pltIds) => {
    ctx.openCareForPlants(pltIds);
  },
  onOpenBatchJournal: (pltIds) => {
    ctx.openBatchJournalForPlants(pltIds);
  },
  onReviewBloomGap: () => {
    const bloomSection =
      document.querySelector<HTMLElement>(
        ".bloom-calendar",
      );
    bloomSection?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  },
};

const gardenerReportsCallbacks: GardenerReportsCallbacks =
  {
    onZoneChange: (zoneCode) => {
      gardenerReportsZoneCode = zoneCode;
      void loadGardenerReports(zoneCode || undefined);
    },
    onOpenTasks: (view) => {
      setTasksView(view);
      setTasksOffset(0);
      syncTasksViewButtons();
      ctx.navigateToSubMode("tasks");
      const typeFilter = querySelect("tasks-filter-type");
      const statusFilter = querySelect("tasks-filter-status");
      if (typeFilter) typeFilter.value = "";
      if (statusFilter)
        statusFilter.value =
          view === "overdue" ? "pending" : "";
      void loadTasks();
    },
    onOpenIssues: (filter) => {
      ctx.navigateToSubMode("issues");
      const statusFilter = querySelect("issues-filter-status");
      const typeFilter = querySelect("issues-filter-type");
      const severityFilter = querySelect("issues-filter-severity");
      if (
        filter === "open" ||
        filter === "overdue_followups"
      ) {
        if (statusFilter) statusFilter.value = "open";
        if (typeFilter) typeFilter.value = "";
        if (severityFilter) severityFilter.value = "";
      } else {
        if (statusFilter) statusFilter.value = "";
        if (typeFilter) typeFilter.value = "";
        if (severityFilter) severityFilter.value = "";
      }
      void ctx.setIssuesOffset(0).then(() =>
        ctx.loadIssues(),
      );
    },
    onOpenWeather: () => {
      ctx.navigateToSubMode("care");
      void ctx.loadCare();
    },
    onOpenPlants: (pltIds) => {
      ctx.focusPlantsInPlantsView(pltIds);
    },
    onOpenBatchJournal: (pltIds) => {
      ctx.openBatchJournalForPlants(pltIds);
    },
    onOpenMap: (plotIds) => {
      ctx.openMapForPlots(plotIds);
    },
    onOpenCare: (pltIds) => {
      ctx.openCareForPlants(pltIds);
    },
    onOpenHarvest: () => {
      setHarvestOffset(0);
      ctx.navigateToSubMode("harvest");
      const qualityFilter = querySelect("harvest-filter-quality");
      const fromFilter = queryInput("harvest-filter-from");
      const toFilter = queryInput("harvest-filter-to");
      if (qualityFilter) qualityFilter.value = "";
      if (fromFilter) fromFilter.value = "";
      if (toFilter) toFilter.value = "";
      void (async () => {
        await loadHarvest();
        try {
          await openHarvestSummaryPanel();
        } catch (err) {
          ctx.showToast(
            getApiErrorMessage(err),
            "error",
          );
        }
      })();
    },
  };
