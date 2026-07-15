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
  fetchPlannerGoalApi,
  getActiveGardenContext,
  savePlannerGoalApi,
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
let plannerGoalSaveQueue: Promise<void> = Promise.resolve();
let workflowRefreshHook: (() => Promise<void>) | null = null;
const statisticsRequestGeneration = {
  overview: 0,
  today: 0,
  reports: 0,
  planner: 0,
};

type StatisticsRequestKind = keyof typeof statisticsRequestGeneration;

interface StatisticsRequestContext {
  gardenId: number;
  generation: number;
  kind: StatisticsRequestKind;
}

function createStatisticsRequest(
  kind: StatisticsRequestKind,
): StatisticsRequestContext | null {
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return null;
  const generation = ++statisticsRequestGeneration[kind];
  return { gardenId, generation, kind };
}

function isCurrentStatisticsRequest(request: StatisticsRequestContext): boolean {
  return request.generation === statisticsRequestGeneration[request.kind]
    && request.gardenId === getActiveGardenContext();
}

export function initStatisticsTab(
  appCtx: AppContext,
  onWorkflowStarted?: () => Promise<void>,
): void {
  ctx = appCtx;
  workflowRefreshHook = onWorkflowStarted ?? null;
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
  statisticsRequestGeneration.overview += 1;
  statisticsRequestGeneration.today += 1;
  statisticsRequestGeneration.reports += 1;
  statisticsRequestGeneration.planner += 1;
  statisticsActionsCache = null;
  gardenerReportsZoneCode = "";
  plannerResult = null;
  gardenProfile = null;
  plannerGoal = "";
  for (const id of [
    "today-dashboard",
    "statistics-content",
    "reports-dashboard",
    "planner-dashboard",
  ]) {
    document.getElementById(id)?.replaceChildren();
  }
}

export function getGardenerReportsZoneCode(): string {
  return gardenerReportsZoneCode;
}

export async function loadStatistics(): Promise<void> {
  if (!ctx) return;
  const request = createStatisticsRequest("overview");
  if (!request) return;
  if (ctx.state.plantsCache.length === 0) {
    await ctx.ensurePlantsCacheLoaded();
    if (!isCurrentStatisticsRequest(request)) return;
  }
  let actions: StatisticsActions | null = null;
  try {
    actions = await getStatisticsActionsApi({ gardenId: request.gardenId });
  } catch {
    // degrade gracefully
  }
  if (!isCurrentStatisticsRequest(request)) return;
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
  const request = createStatisticsRequest("today");
  if (!request) return;
  const container = document.getElementById(
    "today-dashboard",
  );
  if (!container) return;
  try {
    const data: TodayDashboard =
      await fetchTodayDashboardApi({ gardenId: request.gardenId });
    if (!isCurrentStatisticsRequest(request)) return;
    renderTodayDashboard(container, data, {
      onTaskClick: async (taskId) => {
        ctx.navigateToSubMode("tasks");
        void loadTasks();
        try {
          const task = await fetchTaskApi(taskId, { gardenId: request.gardenId });
          if (!isCurrentStatisticsRequest(request)) return;
          await ctx.openTaskForm(task);
        } catch {
          // keep the task list open as fallback
        }
      },
      onIssueClick: async (issueId) => {
        ctx.navigateToSubMode("issues");
        void ctx.loadIssues();
        try {
          const issue = await fetchIssueApi(issueId, { gardenId: request.gardenId });
          if (!isCurrentStatisticsRequest(request)) return;
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
    if (!isCurrentStatisticsRequest(request)) return;
    container.replaceChildren();
  }
}

async function loadGardenerReports(
  zoneCode?: string,
): Promise<void> {
  const request = createStatisticsRequest("reports");
  if (!request) return;
  const container = document.getElementById(
    "reports-dashboard",
  );
  if (!container) return;
  try {
    const result = await fetchGardenerReportsApi(
      zoneCode ? { zone_code: zoneCode } : undefined,
      { gardenId: request.gardenId },
    );
    if (!isCurrentStatisticsRequest(request)) return;
    gardenerReportsZoneCode = result.zone_code ?? "";
    renderGardenerReports(
      container,
      result,
      gardenerReportsCallbacks,
      {
        gardenId: request.gardenId,
        isCurrent: () => isCurrentStatisticsRequest(request),
      },
    );
    ctx.renderDataExportBars();
  } catch {
    if (!isCurrentStatisticsRequest(request)) return;
    container.replaceChildren();
  }
}

async function loadPlanner(
  goalOverride?: string,
  persistGoal = false,
): Promise<void> {
  const request = createStatisticsRequest("planner");
  if (!request) return;
  try {
    let goal = goalOverride;
    if (goal === undefined) {
      goal = (await fetchPlannerGoalApi({ gardenId: request.gardenId })) ?? "";
      if (!isCurrentStatisticsRequest(request)) return;
    } else if (persistGoal) {
      plannerGoalSaveQueue = plannerGoalSaveQueue
        .catch(() => undefined)
        .then(() => savePlannerGoalApi(goal || null, { gardenId: request.gardenId }));
      await plannerGoalSaveQueue;
      if (!isCurrentStatisticsRequest(request)) return;
    }
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
      fetchPlannerSuggestionsApi(params, { gardenId: request.gardenId }),
      fetchGardenProfileApi({ gardenId: request.gardenId }),
      fetchAvailableWorkflowsApi({ gardenId: request.gardenId }).catch(
        () => ({ workflows: [] as AvailableWorkflow[] }),
      ),
    ]);
    if (!isCurrentStatisticsRequest(request)) return;
    plannerResult = result;
    gardenProfile = profile;
    plannerGoal = goal;
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
          void loadPlanner(newGoal, true),
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
                }, { gardenId: request.gardenId });
              if (!isCurrentStatisticsRequest(request)) return;
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
          return (async () => {
            try {
              const res = await startWorkflowApi(
                workflowId,
                selectedSteps,
                { gardenId: request.gardenId },
              );
              if (!isCurrentStatisticsRequest(request)) return;
              ctx.showToast(
                t("workflow.started", {
                  count: res.created,
                }),
              );
              await Promise.allSettled([
                loadTodayDashboard(),
                ctx.loadTasks(),
                ctx.refreshBadgeCounts(),
                workflowRefreshHook?.() ?? Promise.resolve(),
              ]);
              if (!isCurrentStatisticsRequest(request)) return;
              await loadPlanner(plannerGoal, false);
            } catch (err) {
              if (isCurrentStatisticsRequest(request)) {
                ctx.showToast(getApiErrorMessage(err), "error");
              }
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
        ctx.canWrite(),
      );
    }
  } catch {
    if (!isCurrentStatisticsRequest(request)) return;
    document.getElementById("planner-dashboard")?.replaceChildren();
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
      ctx.renderDataExportBars();
      void loadGardenerReports(zoneCode || undefined);
    },
    onOpenTasks: (view, taskIds) => {
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
      void (async () => {
        await loadTasks();
        if (taskIds.length !== 1) return;
        const gardenId = getActiveGardenContext();
        if (gardenId === null) return;
        try {
          const task = await fetchTaskApi(taskIds[0]!, { gardenId });
          if (gardenId === getActiveGardenContext()) await ctx.openTaskForm(task);
        } catch {
          // The scoped task list remains available as fallback.
        }
      })();
    },
    onOpenIssues: (filter, issueIds) => {
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
      void (async () => {
        await ctx.setIssuesOffset(0);
        await ctx.loadIssues();
        if (issueIds.length !== 1) return;
        const gardenId = getActiveGardenContext();
        if (gardenId === null) return;
        try {
          const issue = await fetchIssueApi(issueIds[0]!, { gardenId });
          if (gardenId === getActiveGardenContext()) await ctx.openIssueForm(issue);
        } catch {
          // The supported issue filters remain available as fallback.
        }
      })();
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
