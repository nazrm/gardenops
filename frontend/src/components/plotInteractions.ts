import type {
  AppState,
  GardenTask,
  Plant,
  TaskListResponse,
} from "../core/models";
import { t } from "../core/i18n";
import {
  addPlantToPlotApi,
  deleteMediaAssetApi,
  fetchJournalEntriesApi,
  fetchTasksApi,
  getActiveGardenContext,
  getApiErrorMessage,
  getPlotPlantAlerts,
  getPlotPlants,
  listMediaApi,
  listMediaSummariesApi,
  removeMediaLinkApi,
  removePlantFromPlotApi,
  searchPlantsApi,
  taskActionApi,
  uploadMediaApi,
  withTaskActionRevision,
} from "../services/api";
import type { MediaAsset, MediaLinkRef, TaskActionRequest } from "../services/api";
import type { PlantAlertType } from "./plantCard";
import {
  enqueueDraft,
  getTaskActionStates,
  isOnline,
  onConnectivityChange,
  onOfflineQueueChange,
  OfflineTaskActionConflictError,
  type OfflineTaskActionState,
} from "../services/offlineQueue";
import {
  cacheTaskList,
  getCachedTaskList,
} from "../services/taskCache";
import { renderMediaGalleryLazy } from "./mediaGalleryLoader";
import { showToast } from "./toast";
import {
  dismissBottomSheet,
  getSheetJournalPreview,
  getSheetMediaPreview,
  getSheetSearchResults,
  getSheetTasksPreview,
  showBottomSheet,
  updateBottomSheetPlantsSection,
} from "./bottomSheet";
import {
  createCollapsibleSection,
  dismissDrawer,
  getDrawerJournalPreview,
  getDrawerMediaPreview,
  getDrawerSearchResults,
  getDrawerTasksPreview,
  showDrawer,
  updateDrawerPlantsSection,
} from "./drawer";
import { renderPlotJournalPreviewLazy } from "./journalPreviewLoader";
import { confirmDialog } from "./dialogCore";
import { dismissPopover, showPopover } from "./popover";
import { renderSearchResults } from "./sidebar";
import {
  formatLocalDate,
  taskSnoozeDateSafety,
  taskSnoozePolicy,
} from "../features/taskSnoozePolicy";
import {
  getTaskSnoozeCorrectionNotice,
  openTaskDateDialog,
} from "../features/taskSnoozeFlow";
import {
  canQueueCompletionOffline,
  canQueueDefaultCompletionOffline,
  needsCompletionDialog,
  offlineTaskActionLabels,
  openTaskCompletionDialog,
  taskCompletionActionLabel,
} from "../features/taskCompletionFlow";

export interface PlotCallbacks {
  fetchPlots: () => Promise<void>;
  ensurePlantsCacheLoaded: () => Promise<void>;
  isMobile: () => boolean;
  canWrite: () => boolean;
  deletePlot: (plotId: string) => Promise<void>;
  onEditPlant: (plant: Plant) => void;
  onEditPlot: (plotId: string) => void;
  onPlantAssignmentsChanged: (pltIds?: string[]) => Promise<void> | void;
  onPlotFocusChanged: (plotId: string | null) => void;
  onViewJournal: (plotId: string) => void;
  onMediaTargetsChanged: (targets: MediaLinkRef[]) => void;
  onCreatePlant?: ((preselectedPlotId: string) => void) | undefined;
  onCreateCalendarEvent?:
    | ((prefill: { plant_ids?: string[]; plot_ids?: string[] }) => void)
    | undefined;
}

let plotSelectionSeq = 0;
let plantSearchSeq = 0;
let plotMediaSeq = 0;
let plotTasksSeq = 0;
let plantSearchTimerId: ReturnType<typeof setTimeout> | null = null;
let activePlotTasksPanel: {
  state: AppState;
  plotId: string;
  cbs: PlotCallbacks;
} | null = null;
let plotTaskPanelListenersBound = false;

const PLANT_SEARCH_DEBOUNCE_MS = 250;
const PLOT_PANEL_CACHE_TTL_MS = 5_000;

interface CacheEntry<T> {
  expiresAtMs: number;
  value: T;
}

interface VersionedPromise<T> {
  promise: Promise<T>;
  version: number;
}

interface PlotSupplementalData {
  mediaPreviewByPlantId: Map<string, MediaAsset | null>;
  plantAlertsByPlantId: Map<string, PlantAlertType[]>;
}

const plotPlantsCache = new Map<string, CacheEntry<Plant[]>>();
const plotPlantsRequests = new Map<string, VersionedPromise<Plant[]>>();
const plotSupplementalCache = new Map<
  string,
  CacheEntry<PlotSupplementalData>
>();
const plotSupplementalRequests = new Map<
  string,
  VersionedPromise<PlotSupplementalData>
>();
const plotCacheVersions = new Map<string, number>();

function sortPlantsForPlotPanel(plants: Plant[]): Plant[] {
  return [...plants].sort(
    (a, b) =>
      a.category.localeCompare(b.category) ||
      a.name.localeCompare(b.name),
  );
}

function createEmptyMediaPreviewMap(
  plants: Plant[],
): Map<string, MediaAsset | null> {
  return new Map(plants.map((plant) => [plant.plt_id, null]));
}

function buildPlantAlertMap(
  alertsRes: Awaited<ReturnType<typeof getPlotPlantAlerts>> | null,
): Map<string, PlantAlertType[]> {
  const plantAlertsByPlantId = new Map<string, PlantAlertType[]>();
  if (!alertsRes) return plantAlertsByPlantId;
  for (const [pltId, types] of Object.entries(alertsRes.plant_alerts)) {
    plantAlertsByPlantId.set(pltId, [...(types as PlantAlertType[])]);
  }
  return plantAlertsByPlantId;
}

function clonePlantAlertMap(
  source: Map<string, PlantAlertType[]>,
): Map<string, PlantAlertType[]> {
  return new Map(
    Array.from(source.entries(), ([pltId, types]) => [
      pltId,
      [...types],
    ]),
  );
}

function clonePlotSupplementalData(
  source: PlotSupplementalData,
): PlotSupplementalData {
  return {
    mediaPreviewByPlantId: new Map(source.mediaPreviewByPlantId),
    plantAlertsByPlantId: clonePlantAlertMap(source.plantAlertsByPlantId),
  };
}

function getPlotCacheVersion(plotId: string): number {
  return plotCacheVersions.get(plotId) ?? 0;
}

function getFreshCacheValue<T>(
  cache: Map<string, CacheEntry<T>>,
  key: string,
): T | null {
  const cached = cache.get(key);
  if (!cached) return null;
  if (cached.expiresAtMs <= Date.now()) {
    cache.delete(key);
    return null;
  }
  return cached.value;
}

export function invalidatePlotPanelCache(plotId: string): void {
  plotCacheVersions.set(plotId, getPlotCacheVersion(plotId) + 1);
  plotPlantsCache.delete(plotId);
  plotSupplementalCache.delete(plotId);
}

function getPlotTasksPreviewContainer(): HTMLElement | null {
  return getDrawerTasksPreview() ?? getSheetTasksPreview();
}

function getActivePlotPanelModalParent(): HTMLElement | null {
  return getPlotTasksPreviewContainer()?.closest<HTMLElement>(
    ".drawer, .bottom-sheet",
  ) ?? null;
}

async function refreshActivePlotTasksPreview(): Promise<void> {
  const active = activePlotTasksPanel;
  if (!active || active.state.selectedPlotId !== active.plotId) return;
  await loadPlotTasksPreview(active.state, active.plotId, active.cbs);
}

function bindPlotTaskPanelListeners(): void {
  if (plotTaskPanelListenersBound) return;
  plotTaskPanelListenersBound = true;
  onConnectivityChange(() => {
    void refreshActivePlotTasksPreview();
  });
  onOfflineQueueChange(() => {
    void refreshActivePlotTasksPreview();
  });
}

function activatePlotTasksPanel(
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
): void {
  activePlotTasksPanel = { state, plotId, cbs };
  bindPlotTaskPanelListeners();
}

function deactivatePlotTasksPanel(): void {
  activePlotTasksPanel = null;
  plotTasksSeq += 1;
}

function getPanelCallbacks(
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
): {
  canWrite: boolean;
  onClose: () => void;
  onRemove: (pltId: string) => void;
  onEdit: (plant: Plant) => void;
  onCreateCalendarEvent?: (
    prefill: { plant_ids?: string[]; plot_ids?: string[] },
  ) => void;
} {
  const canWrite = cbs.canWrite();
  return {
    canWrite,
    onClose: () => closePanel(state, cbs),
    onRemove: (pltId) =>
      void removePlant(state, plotId, pltId, cbs),
    onEdit: (plant) => cbs.onEditPlant(plant),
    ...(canWrite && cbs.onCreateCalendarEvent
      ? { onCreateCalendarEvent: cbs.onCreateCalendarEvent }
      : {}),
  };
}

async function getPlotPlantsCached(
  plotId: string,
): Promise<Plant[]> {
  const cached = getFreshCacheValue(plotPlantsCache, plotId);
  if (cached) return [...cached];

  const version = getPlotCacheVersion(plotId);
  const existing = plotPlantsRequests.get(plotId);
  if (existing && existing.version === version) {
    const plants = await existing.promise;
    return [...plants];
  }

  const promise = (async () => {
    const plants = sortPlantsForPlotPanel(await getPlotPlants(plotId));
    if (getPlotCacheVersion(plotId) === version) {
      plotPlantsCache.set(plotId, {
        expiresAtMs: Date.now() + PLOT_PANEL_CACHE_TTL_MS,
        value: plants,
      });
    }
    return plants;
  })();
  plotPlantsRequests.set(plotId, { promise, version });
  try {
    const plants = await promise;
    return [...plants];
  } finally {
    const activeRequest = plotPlantsRequests.get(plotId);
    if (activeRequest?.version === version && activeRequest.promise === promise) {
      plotPlantsRequests.delete(plotId);
    }
  }
}

function getCachedPlotSupplementalData(
  plotId: string,
): PlotSupplementalData | null {
  const cached = getFreshCacheValue(plotSupplementalCache, plotId);
  return cached ? clonePlotSupplementalData(cached) : null;
}

async function getPlotSupplementalData(
  plotId: string,
  plants: Plant[],
): Promise<PlotSupplementalData> {
  if (plants.length === 0) {
    return {
      mediaPreviewByPlantId: new Map(),
      plantAlertsByPlantId: new Map(),
    };
  }

  const cached = getCachedPlotSupplementalData(plotId);
  if (cached) return cached;

  const version = getPlotCacheVersion(plotId);
  const existing = plotSupplementalRequests.get(plotId);
  if (existing && existing.version === version) {
    return clonePlotSupplementalData(await existing.promise);
  }

  const promise = (async () => {
    const [mediaRes, alertsRes] = await Promise.allSettled([
      fetchPlantMediaPreviewMap(plants.map((plant) => plant.plt_id)),
      getPlotPlantAlerts(plotId),
    ]);
    const data: PlotSupplementalData = {
      mediaPreviewByPlantId:
        mediaRes.status === "fulfilled"
          ? mediaRes.value
          : createEmptyMediaPreviewMap(plants),
      plantAlertsByPlantId:
        alertsRes.status === "fulfilled"
          ? buildPlantAlertMap(alertsRes.value)
          : new Map(),
    };
    if (getPlotCacheVersion(plotId) === version) {
      plotSupplementalCache.set(plotId, {
        expiresAtMs: Date.now() + PLOT_PANEL_CACHE_TTL_MS,
        value: data,
      });
    }
    return data;
  })();
  plotSupplementalRequests.set(plotId, { promise, version });
  try {
    return clonePlotSupplementalData(await promise);
  } finally {
    const activeRequest = plotSupplementalRequests.get(plotId);
    if (activeRequest?.version === version && activeRequest.promise === promise) {
      plotSupplementalRequests.delete(plotId);
    }
  }
}

function cancelPendingPlantSearch(): void {
  plantSearchSeq += 1;
  if (plantSearchTimerId !== null) {
    clearTimeout(plantSearchTimerId);
    plantSearchTimerId = null;
  }
}

function formatTaskDue(
  task: GardenTask,
): { text: string; overdue: boolean } {
  const today = formatLocalDate(new Date());
  if (task.status === "completed") {
    const doneDate = task.completed_at_ms
      ? formatLocalDate(new Date(task.completed_at_ms))
      : today;
    return {
      text: t("plot_drawer.completed_on", { date: doneDate }) as string,
      overdue: false,
    };
  }
  const dueOn = task.status === "snoozed" && task.snoozed_until
    ? task.snoozed_until
    : task.due_on;
  if (task.status === "expired" || dueOn < today) {
    const diff = Math.max(1, Math.round(
      (new Date(today).getTime() - new Date(dueOn).getTime()) /
        86_400_000,
    ));
    return {
      text: t("plot_drawer.overdue_by", { days: diff }) as string,
      overdue: true,
    };
  }
  if (dueOn === today) {
    return { text: t("plot_drawer.due_today") as string, overdue: false };
  }
  return {
    text: t("plot_drawer.due_on", { date: dueOn }) as string,
    overdue: false,
  };
}

interface PlotTaskCardCallbacks {
  offlineAction?: OfflineTaskActionState | undefined;
  onComplete?: (() => void) | undefined;
  onSkip?: (() => void) | undefined;
  onSnooze?: (() => void) | undefined;
  onSnoozeDate?: (() => void) | undefined;
  onReschedule?: (() => void) | undefined;
}

function appendTaskCardAction(
  container: HTMLElement,
  className: string,
  label: string,
  icon: string,
  onClick: () => void,
): void {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `drawer-task-action ${className}`;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.textContent = icon;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  container.appendChild(button);
}

function renderTaskCard(
  task: GardenTask,
  callbacks: PlotTaskCardCallbacks,
): HTMLElement {
  const card = document.createElement("div");
  card.className = "drawer-task-card";
  card.dataset["taskId"] = task.id;
  if (task.status === "completed") card.classList.add("task-completed");
  if (callbacks.offlineAction) {
    card.classList.add(`task-offline-${callbacks.offlineAction.status}`);
    card.dataset["offlineTaskState"] = callbacks.offlineAction.status;
  }

  const dot = document.createElement("span");
  dot.className = `drawer-task-severity severity-${task.severity}`;

  const info = document.createElement("div");
  info.className = "drawer-task-info";

  const titleEl = document.createElement("div");
  titleEl.className = "drawer-task-title";
  titleEl.textContent = task.title;

  const due = formatTaskDue(task);
  const dueEl = document.createElement("div");
  dueEl.className = "drawer-task-due";
  if (due.overdue) dueEl.classList.add("overdue");
  dueEl.textContent = due.text;

  info.append(titleEl, dueEl);
  if (callbacks.offlineAction) {
    const offlineState = document.createElement("div");
    offlineState.className = `drawer-task-offline-state task-offline-state task-offline-state--${callbacks.offlineAction.status}`;
    offlineState.setAttribute(
      "role",
      callbacks.offlineAction.status === "failed" ? "alert" : "status",
    );
    offlineState.textContent = t(`offline.task_${callbacks.offlineAction.status}`, {
      action: t(`tasks.action_${callbacks.offlineAction.action}`),
    });
    if (callbacks.offlineAction.status === "failed" && callbacks.offlineAction.lastError) {
      const error = document.createElement("span");
      error.className = "task-offline-error";
      error.textContent = callbacks.offlineAction.lastError;
      offlineState.appendChild(error);
    }
    info.appendChild(offlineState);
  }
  card.append(dot, info);

  if (task.status !== "completed" && !callbacks.offlineAction) {
    const actions = document.createElement("div");
    actions.className = "drawer-task-actions";
    if (callbacks.onComplete) {
      appendTaskCardAction(
        actions,
        "action-complete",
        taskCompletionActionLabel(task),
        "\u2713",
        callbacks.onComplete,
      );
    }
    if (callbacks.onSnooze) {
      appendTaskCardAction(
        actions,
        "action-snooze",
        taskSnoozePolicy(task).label,
        "\u{1F552}",
        callbacks.onSnooze,
      );
    }
    if (callbacks.onSnoozeDate) {
      appendTaskCardAction(
        actions,
        "action-snooze-date",
        t("tasks.snooze_change_date") as string,
        "...",
        callbacks.onSnoozeDate,
      );
    }
    if (callbacks.onReschedule) {
      appendTaskCardAction(
        actions,
        "action-reschedule",
        t("tasks.action_reschedule") as string,
        "\u21B7",
        callbacks.onReschedule,
      );
    }
    if (callbacks.onSkip) {
      appendTaskCardAction(
        actions,
        "action-skip",
        t("tasks.action_skip") as string,
        "\u00d7",
        callbacks.onSkip,
      );
    }
    if (actions.childElementCount > 0) card.appendChild(actions);
  }

  return card;
}

type PlotTaskPreviewDataState = "live" | "cached" | "unavailable";

interface PlotTaskPreviewData {
  actionableRes: TaskListResponse;
  completedRes: TaskListResponse;
  dataState: PlotTaskPreviewDataState;
}

interface PlotTaskPreviewLoadOptions {
  focusTaskId?: string | undefined;
}

function getCachedPlotTaskPreview(
  gardenId: number,
  plotId: string,
): PlotTaskPreviewData | null {
  const actionableRes = getCachedTaskList(gardenId, {
    plot_id: plotId,
    view: "today",
  });
  const completedRes = getCachedTaskList(gardenId, {
    plot_id: plotId,
    status: "completed",
  });
  if (!actionableRes || !completedRes) return null;
  return { actionableRes, completedRes, dataState: "cached" };
}

async function getPlotTaskPreview(
  gardenId: number,
  plotId: string,
): Promise<PlotTaskPreviewData> {
  if (!isOnline()) {
    return getCachedPlotTaskPreview(gardenId, plotId) ?? {
      actionableRes: { tasks: [], total: 0 },
      completedRes: { tasks: [], total: 0 },
      dataState: "unavailable",
    };
  }

  try {
    const [actionableRes, completedRes] = await Promise.all([
      fetchTasksApi({ plot_id: plotId, view: "today" }),
      fetchTasksApi({ plot_id: plotId, status: "completed" }),
    ]);
    cacheTaskList(gardenId, { plot_id: plotId, view: "today" }, actionableRes);
    cacheTaskList(
      gardenId,
      { plot_id: plotId, status: "completed" },
      completedRes,
    );
    return { actionableRes, completedRes, dataState: "live" };
  } catch (err) {
    if (!isOnline()) {
      return getCachedPlotTaskPreview(gardenId, plotId) ?? {
        actionableRes: { tasks: [], total: 0 },
        completedRes: { tasks: [], total: 0 },
        dataState: "unavailable",
      };
    }
    throw err;
  }
}

function appendPlotTaskPreviewDataState(
  body: HTMLElement,
  dataState: Exclude<PlotTaskPreviewDataState, "live">,
): void {
  const status = document.createElement("div");
  status.className = `offline-data-state offline-data-state--${dataState}`;
  status.setAttribute("role", "status");
  status.textContent = t(
    dataState === "cached"
      ? "tasks.offline_cached"
      : "tasks.offline_unavailable",
  ) as string;
  body.appendChild(status);
}

function restorePlotTaskPreviewFocus(
  section: HTMLElement,
  focusTaskId?: string,
): void {
  if (!focusTaskId) return;
  const taskCard = section.querySelector<HTMLElement>(
    `.drawer-task-card[data-task-id="${CSS.escape(focusTaskId)}"]`,
  );
  const target = taskCard
    ?? section.querySelector<HTMLElement>(".drawer-section-header");
  if (!target) return;
  if (taskCard) taskCard.tabIndex = -1;
  window.requestAnimationFrame(() => {
    if (target.isConnected && !target.closest("[inert]")) target.focus();
  });
}

async function loadPlotTasksPreview(
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
  options: PlotTaskPreviewLoadOptions = {},
): Promise<boolean> {
  const seq = ++plotTasksSeq;
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return false;
  const container = getPlotTasksPreviewContainer();
  if (!container) return false;

  try {
    const [preview, offlineActions] = await Promise.all([
      getPlotTaskPreview(gardenId, plotId),
      getTaskActionStates(gardenId),
    ]);
    if (
      seq !== plotTasksSeq
      || state.selectedPlotId !== plotId
      || getActiveGardenContext() !== gardenId
    ) return false;

    const { actionableRes, completedRes, dataState } = preview;

    const oneWeekAgo = Date.now() - 7 * 86_400_000;
    const recentlyCompleted = completedRes.tasks.filter(
      (task) =>
        task.completed_at_ms != null &&
        task.completed_at_ms >= oneWeekAgo,
    );

    const allTasks = [
      ...actionableRes.tasks.sort((a, b) =>
        a.due_on.localeCompare(b.due_on),
      ),
      ...recentlyCompleted.sort(
        (a, b) =>
          (b.completed_at_ms ?? 0) - (a.completed_at_ms ?? 0),
      ),
    ];

    const body = document.createElement("div");
    body.className = "drawer-section-body";

    if (dataState !== "live") {
      appendPlotTaskPreviewDataState(body, dataState);
    }

    if (dataState !== "unavailable" && allTasks.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-message";
      empty.textContent = t("plot_drawer.no_tasks") as string;
      body.appendChild(empty);
    } else if (dataState !== "unavailable") {
      for (const task of allTasks) {
        const card = renderTaskCard(
          task,
          offlineActions.has(task.id)
            ? { offlineAction: offlineActions.get(task.id) }
            : cbs.canWrite()
            ? {
                ...(isOnline() || canQueueCompletionOffline(task)
                  ? { onComplete: () => void completeTaskInline(task, card, state, plotId, cbs) }
                  : {}),
                onSkip: () => void skipTaskInline(task, card, state, plotId, cbs),
                onSnooze: () => void snoozeTaskInline(task, card, state, plotId, cbs),
                onSnoozeDate: () =>
                  openPlotSnoozeDateDialog(task, card, state, plotId, cbs),
                onReschedule: () =>
                  openPlotRescheduleDialog(task, card, state, plotId, cbs),
              }
            : {},
        );
        body.appendChild(card);
      }
    }

    const section = createCollapsibleSection(
      t("plot_drawer.tasks_section") as string,
      actionableRes.tasks.length,
      body,
    );
    container.replaceChildren(section);
    restorePlotTaskPreviewFocus(section, options.focusTaskId);
    return true;
  } catch (err) {
    console.warn("Plot task preview failed:", err);
    if (
      seq === plotTasksSeq
      && state.selectedPlotId === plotId
      && getActiveGardenContext() === gardenId
    ) {
      const body = document.createElement("div");
      body.className = "drawer-section-body";
      appendPlotTaskPreviewDataState(body, "unavailable");
      const section = createCollapsibleSection(
        t("plot_drawer.tasks_section") as string,
        0,
        body,
      );
      container.replaceChildren(section);
    }
    return false;
  }
}

async function enqueuePlotOfflineTaskAction(
  task: GardenTask,
  body: TaskActionRequest,
): Promise<void> {
  const draftTypeByAction: Record<TaskActionRequest["action"], string> = {
    complete: "task_complete",
    skip: "task_skip",
    snooze: "task_snooze",
    reschedule: "task_reschedule",
  };
  const { action, ...payload } = withTaskActionRevision(task, body);
  await enqueueDraft(draftTypeByAction[action], {
    task_id: task.id,
    ...offlineTaskActionLabels(task, action),
    ...payload,
  });
}

async function submitPlotTaskAction(
  task: GardenTask,
  card: HTMLElement,
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
  body: TaskActionRequest,
  successMessage?: string,
): Promise<boolean> {
  if (!cbs.canWrite()) {
    showToast(t("error.write_access"), "error");
    return false;
  }
  if (
    state.selectedPlotId !== plotId
    || getActiveGardenContext() !== task.garden_id
  ) {
    return false;
  }
  const actionBody = withTaskActionRevision(task, body);
  try {
    if (!isOnline()) {
      await enqueuePlotOfflineTaskAction(task, actionBody);
      showToast(t("offline.draft_saved"), "success");
      await loadPlotTasksPreview(state, plotId, cbs, {
        focusTaskId: task.id,
      });
      return true;
    }
    const result = await taskActionApi(task.id, actionBody);
    task.updated_at_ms = result.updated_at_ms;
    card.remove();
    if (successMessage) showToast(successMessage, "success");
    await loadPlotTasksPreview(state, plotId, cbs, {
      focusTaskId: task.id,
    });
    return true;
  } catch (err) {
    showToast(
      err instanceof OfflineTaskActionConflictError
        ? t(err.kind === "duplicate" ? "offline.task_duplicate" : "offline.task_conflict")
        : getApiErrorMessage(err),
      "error",
    );
    return false;
  }
}

async function completeTaskInline(
  task: GardenTask,
  card: HTMLElement,
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
  body: TaskActionRequest = { action: "complete" },
): Promise<boolean> {
  if (needsCompletionDialog(task) && !body.completed_plant_ids?.length) {
    if (!isOnline()) {
      const needsExplicitOutcome = !canQueueDefaultCompletionOffline(task);
      if (needsExplicitOutcome && !canQueueCompletionOffline(task)) {
        showToast(t("tasks.complete_grouped_one_by_one"), "error");
        return false;
      }
    }
    if (isOnline()) {
      await cbs.ensurePlantsCacheLoaded();
    }
    if (state.selectedPlotId !== plotId) return false;
    const modalParent = getActivePlotPanelModalParent();
    if (!modalParent) return false;
    const plantNames = new Map(state.plantsCache.map((plant) => [plant.plt_id, plant.name]));
    openTaskCompletionDialog(
      task,
      plantNames,
      (completionBody) => completeTaskInline(
        task,
        card,
        state,
        plotId,
        cbs,
        completionBody,
      ),
      { modalParent },
    );
    return false;
  }
  return submitPlotTaskAction(
    task,
    card,
    state,
    plotId,
    cbs,
    body,
    t("plot_drawer.task_completed_toast") as string,
  );
}

async function skipTaskInline(
  task: GardenTask,
  card: HTMLElement,
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
): Promise<boolean> {
  return submitPlotTaskAction(
    task,
    card,
    state,
    plotId,
    cbs,
    { action: "skip" },
    t("tasks.action_success", { action: "skip" }) as string,
  );
}

function openPlotSnoozeDateDialog(
  task: GardenTask,
  card: HTMLElement,
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
  defaultDate = taskSnoozePolicy(task).defaultDate,
): void {
  const policy = taskSnoozePolicy(task);
  if (policy.blockedMessage) {
    showToast(policy.blockedMessage, "error");
    return;
  }
  if (state.selectedPlotId !== plotId) return;
  const modalParent = getActivePlotPanelModalParent();
  if (!modalParent) return;
  openTaskDateDialog({
    title: t("tasks.snooze_prompt") as string,
    defaultDate,
    warning: policy.manualDateMessage,
    requireManualDate: policy.requireManualDate,
    maxDate: policy.maxDate,
    getDateSafety: (date) => taskSnoozeDateSafety(task, date),
    onConfirm: (date, confirmOutsideWindow) =>
      snoozeTaskInline(task, card, state, plotId, cbs, date, confirmOutsideWindow),
    modalParent,
  });
}

async function snoozeTaskInline(
  task: GardenTask,
  card: HTMLElement,
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
  requestedDate?: string,
  confirmOutsideWindow = false,
): Promise<boolean> {
  const policy = taskSnoozePolicy(task);
  if (policy.blockedMessage) {
    showToast(policy.blockedMessage, "error");
    return false;
  }
  if (!requestedDate && !policy.immediate) {
    openPlotSnoozeDateDialog(
      task,
      card,
      state,
      plotId,
      cbs,
      policy.defaultDate,
    );
    return false;
  }
  const snoozeUntil = requestedDate ?? policy.defaultDate;
  const safety = taskSnoozeDateSafety(task, snoozeUntil);
  if (safety.blocked) {
    showToast(safety.message ?? t("tasks.snooze_prompt"), "error");
    return false;
  }
  const completed = await submitPlotTaskAction(
    task,
    card,
    state,
    plotId,
    cbs,
    {
      action: "snooze",
      snooze_until: snoozeUntil,
      ...(safety.confirmationRequired && confirmOutsideWindow
        ? { confirm_outside_window: true }
        : {}),
    },
  );
  if (!completed) return false;
  const notice = getTaskSnoozeCorrectionNotice(snoozeUntil, () => {
    openPlotSnoozeDateDialog(task, card, state, plotId, cbs, snoozeUntil);
  });
  showToast(notice.message, "success", {
    actions: [{ label: notice.actionLabel, onClick: notice.onChangeDate }],
    durationMs: notice.durationMs,
  });
  return true;
}

function openPlotRescheduleDialog(
  task: GardenTask,
  card: HTMLElement,
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
): void {
  if (state.selectedPlotId !== plotId) return;
  const modalParent = getActivePlotPanelModalParent();
  if (!modalParent) return;
  openTaskDateDialog({
    title: t("tasks.reschedule_prompt") as string,
    defaultDate: task.due_on,
    onConfirm: (date) =>
      submitPlotTaskAction(
        task,
        card,
        state,
        plotId,
        cbs,
        { action: "reschedule", reschedule_to: date },
        t("tasks.action_success", { action: "reschedule" }) as string,
      ),
    modalParent,
  });
}

async function fetchPlantMediaPreviewMap(
  pltIds: string[],
): Promise<Map<string, MediaAsset | null>> {
  const requestedIds = Array.from(new Set(pltIds.map((pltId) => pltId.trim()).filter(Boolean)));
  if (requestedIds.length === 0) {
    return new Map();
  }
  const result = await listMediaSummariesApi({
    targetType: "plant",
    targetIds: requestedIds,
  });
  const found = new Map(result.items.map((item) => [item.target_id, item.asset]));
  return new Map(requestedIds.map((pltId) => [pltId, found.get(pltId) ?? null]));
}

async function loadPlotJournalPreview(
  plotId: string,
  cbs: PlotCallbacks,
): Promise<void> {
  try {
    const result = await fetchJournalEntriesApi({
      plot_id: plotId,
      limit: 5,
      offset: 0,
    });
    const container =
      getDrawerJournalPreview() ?? getSheetJournalPreview();
    if (!container) return;
    renderPlotJournalPreviewLazy(container, result.entries, () => {
      cbs.onViewJournal(plotId);
    });
  } catch {
    // Silently ignore — preview is non-critical
  }
}

async function loadPlotMediaPreview(
  plotId: string,
  cbs: PlotCallbacks,
): Promise<void> {
  const seq = ++plotMediaSeq;
  const container = getDrawerMediaPreview() ?? getSheetMediaPreview();
  if (!container) return;
  let assets: MediaAsset[] = [];
  const render = (progressPct: number | null = null) => {
    const activeContainer = getDrawerMediaPreview() ?? getSheetMediaPreview();
    if (!activeContainer) return;
    void renderMediaGalleryLazy(activeContainer, {
      assets,
      emptyText: t("media.plot_empty"),
      canUpload: cbs.canWrite(),
      uploadProgressPct: progressPct,
      onFilesSelected: (files) => {
        void (async () => {
          try {
            if (!isOnline()) {
              await enqueueDraft("plot_media_upload", {
                target_id: plotId,
                media_files: [...files],
              });
              showToast(t("offline.draft_saved"), "success");
              return;
            }
            for (let i = 0; i < files.length; i += 1) {
              const file = files[i]!;
              await uploadMediaApi({
                targetType: "plot",
                targetId: plotId,
                file,
                onProgress: (pct) => {
                  const overall = Math.round(((i + (pct / 100)) / files.length) * 100);
                  render(overall);
                },
              });
            }
            const refreshed = await listMediaApi({
              target_type: "plot",
              target_id: plotId,
              limit: 12,
            });
            assets = refreshed.items;
            render(null);
            showToast(t("media.upload_complete", { count: files.length }));
          } catch (err) {
            render(null);
            showToast(getApiErrorMessage(err), "error");
          }
        })();
      },
      onDeleteAsset: (asset) => {
        void (async () => {
          const confirmed = await confirmDialog(
            t("media.remove_confirm", { name: asset.original_filename || t("media.untitled") }),
            t("common.remove"),
          );
          if (!confirmed) return;
          try {
            await removeMediaLinkApi({
              assetId: asset.asset_id,
              targetType: "plot",
              targetId: plotId,
            });
            assets = assets.filter((candidate) => candidate.asset_id !== asset.asset_id);
            render(null);
            showToast(t("media.removed"));
          } catch (err) {
            showToast(getApiErrorMessage(err), "error");
          }
        })();
      },
      onDeleteEverywhereAsset: (asset) => {
        void (async () => {
          const confirmed = await confirmDialog(
            t("media.delete_everywhere_confirm", {
              name: asset.original_filename || t("media.untitled"),
              count: asset.targets.length,
            }),
            t("media.delete_everywhere"),
          );
          if (!confirmed) return;
          try {
            await deleteMediaAssetApi(asset.asset_id);
            assets = assets.filter((candidate) => candidate.asset_id !== asset.asset_id);
            render(null);
            cbs.onMediaTargetsChanged(asset.targets);
            showToast(t("media.deleted_everywhere"));
          } catch (err) {
            showToast(getApiErrorMessage(err), "error");
          }
        })();
      },
      deleteLabel: t("common.remove"),
      deleteEverywhereLabel: t("media.delete_everywhere"),
    });
  };
  try {
    const result = await listMediaApi({
      target_type: "plot",
      target_id: plotId,
      limit: 12,
    });
    if (seq !== plotMediaSeq) return;
    assets = result.items;
    render(null);
  } catch {
    // Silently ignore — photo preview is non-critical
  }
}

function closePanel(state: AppState, cbs: PlotCallbacks): void {
  deactivatePlotTasksPanel();
  cancelPendingPlantSearch();
  dismissPopover();
  dismissDrawer();
  dismissBottomSheet();
  document.querySelectorAll(".plot").forEach((el) => {
    el.classList.remove("selected");
  });
  state.selectedPlotId = null;
  cbs.onPlotFocusChanged(null);
}

async function hydrateActivePlotPanel(
  state: AppState,
  plotId: string,
  plants: Plant[],
  cbs: PlotCallbacks,
  seq: number,
): Promise<void> {
  const supplemental = await getPlotSupplementalData(plotId, plants);
  if (seq !== plotSelectionSeq || state.selectedPlotId !== plotId) return;
  const callbacks = getPanelCallbacks(state, plotId, cbs);
  updateDrawerPlantsSection({
    plotId,
    plants,
    mediaPreviewByPlantId: supplemental.mediaPreviewByPlantId,
    plantAlertsByPlantId: supplemental.plantAlertsByPlantId,
    ...callbacks,
  });
  updateBottomSheetPlantsSection({
    plotId,
    plants,
    mediaPreviewByPlantId: supplemental.mediaPreviewByPlantId,
    plantAlertsByPlantId: supplemental.plantAlertsByPlantId,
    ...callbacks,
  });
}

async function refreshSelectedPlotPlants(
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
): Promise<void> {
  if (state.selectedPlotId !== plotId) return;
  const seq = ++plotSelectionSeq;
  const plants = await getPlotPlantsCached(plotId);
  if (seq !== plotSelectionSeq || state.selectedPlotId !== plotId) return;
  const callbacks = getPanelCallbacks(state, plotId, cbs);
  updateDrawerPlantsSection({
    plotId,
    plants,
    mediaPreviewByPlantId: createEmptyMediaPreviewMap(plants),
    plantAlertsByPlantId: new Map(),
    ...callbacks,
  });
  updateBottomSheetPlantsSection({
    plotId,
    plants,
    mediaPreviewByPlantId: createEmptyMediaPreviewMap(plants),
    plantAlertsByPlantId: new Map(),
    ...callbacks,
  });
  void hydrateActivePlotPanel(state, plotId, plants, cbs, seq);
}

export async function selectPlot(
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
  anchorEl?: HTMLElement,
): Promise<void> {
  deactivatePlotTasksPanel();
  cancelPendingPlantSearch();
  dismissPopover();
  dismissDrawer();
  dismissBottomSheet();

  document.querySelectorAll(".plot").forEach((el) => {
    el.classList.toggle(
      "selected",
      el.getAttribute("data-plot-id") === plotId,
    );
  });

  state.selectedPlotId = plotId;
  cbs.onPlotFocusChanged(plotId);
  const seq = ++plotSelectionSeq;
  const plot = state.plots.find((p) => p.plot_id === plotId);
  if (!plot) return;

  const topPlants = await getPlotPlantsCached(plotId);
  if (seq !== plotSelectionSeq || state.selectedPlotId !== plotId) return;

  if (cbs.isMobile()) {
    const supplemental = getCachedPlotSupplementalData(plotId);
    const panelCallbacks = getPanelCallbacks(state, plotId, cbs);
    showBottomSheet({
      plotId,
      plants: topPlants,
      ...(supplemental
        ? {
            mediaPreviewByPlantId: supplemental.mediaPreviewByPlantId,
            plantAlertsByPlantId: supplemental.plantAlertsByPlantId,
          }
        : {}),
      onSearch: (e) => void handlePlantSearch(state, e, cbs),
      ...panelCallbacks,
      ...(cbs.canWrite()
        ? {
            onEditPlot: () => cbs.onEditPlot(plotId),
            onDeletePlot: () => void cbs.deletePlot(plotId),
          }
        : {}),
      ...(cbs.canWrite() && cbs.onCreatePlant
        ? { onCreatePlant: cbs.onCreatePlant }
        : {}),
    });
    activatePlotTasksPanel(state, plotId, cbs);
    void hydrateActivePlotPanel(state, plotId, topPlants, cbs, seq);
    void loadPlotTasksPreview(state, plotId, cbs);
    void loadPlotJournalPreview(plotId, cbs);
    void loadPlotMediaPreview(plotId, cbs);
    return;
  }

  const anchor =
    anchorEl ??
    document.querySelector(`[data-plot-id="${plotId}"]`);
  if (!anchor) return;

  const anchorRect = anchor.getBoundingClientRect();
  const viewportRect =
    document
      .getElementById("map-viewport")
      ?.getBoundingClientRect() ??
    document.body.getBoundingClientRect();

  showPopover({
    plotId,
    zone: plot.zone_code,
    plantCount: plot.plant_count,
    plants: topPlants,
    anchorRect,
    viewportRect,
    onViewDetails: () => void openDrawerForPlot(state, plotId, cbs),
    onEdit: cbs.canWrite() ? () => cbs.onEditPlot(plotId) : undefined,
    onDismiss: () => {
      state.selectedPlotId = null;
      cbs.onPlotFocusChanged(null);
      document
        .querySelectorAll(".plot.selected")
        .forEach((el) => el.classList.remove("selected"));
    },
  });
}

export async function openDrawerForPlot(
  state: AppState,
  plotId: string,
  cbs: PlotCallbacks,
): Promise<void> {
  deactivatePlotTasksPanel();
  cancelPendingPlantSearch();
  const seq = ++plotSelectionSeq;
  state.selectedPlotId = plotId;
  cbs.onPlotFocusChanged(plotId);
  const plants = await getPlotPlantsCached(plotId);
  if (seq !== plotSelectionSeq || state.selectedPlotId !== plotId) return;
  const supplemental = getCachedPlotSupplementalData(plotId);
  const panelCallbacks = getPanelCallbacks(state, plotId, cbs);

  showDrawer({
    plotId,
    plants,
    ...(supplemental
      ? {
          mediaPreviewByPlantId: supplemental.mediaPreviewByPlantId,
          plantAlertsByPlantId: supplemental.plantAlertsByPlantId,
        }
      : {}),
    onSearch: (e) => void handlePlantSearch(state, e, cbs),
    ...panelCallbacks,
    ...(cbs.canWrite()
      ? { onDeletePlot: () => void cbs.deletePlot(plotId) }
      : {}),
    ...(cbs.canWrite() && cbs.onCreatePlant
      ? { onCreatePlant: cbs.onCreatePlant }
      : {}),
  });
  activatePlotTasksPanel(state, plotId, cbs);
  void hydrateActivePlotPanel(state, plotId, plants, cbs, seq);
  void loadPlotTasksPreview(state, plotId, cbs);
  void loadPlotJournalPreview(plotId, cbs);
  void loadPlotMediaPreview(plotId, cbs);
}

export async function handlePlantSearch(
  state: AppState,
  event: Event,
  cbs: PlotCallbacks,
): Promise<void> {
  const input = event.target as HTMLInputElement;
  const query = input.value.trim();
  const resultsDiv =
    getDrawerSearchResults() ?? getSheetSearchResults();
  if (!resultsDiv) return;
  if (!cbs.canWrite()) {
    cancelPendingPlantSearch();
    resultsDiv.replaceChildren();
    return;
  }

  if (query.length < 2) {
    cancelPendingPlantSearch();
    resultsDiv.replaceChildren();
    return;
  }

  cancelPendingPlantSearch();
  resultsDiv.replaceChildren();
  const seq = ++plantSearchSeq;
  plantSearchTimerId = setTimeout(() => {
    plantSearchTimerId = null;
    void (async () => {
      const plants = await searchPlantsApi(query, { limit: 10 });
      if (seq !== plantSearchSeq || input.value.trim() !== query) return;
      renderSearchResults(resultsDiv, plants, (pltId) => {
        if (!state.selectedPlotId) return;
        void addPlantToPlot(state, state.selectedPlotId, pltId, cbs);
      });
    })();
  }, PLANT_SEARCH_DEBOUNCE_MS);
}

export async function addPlantToPlot(
  state: AppState,
  plotId: string,
  pltId: string,
  cbs: PlotCallbacks,
): Promise<void> {
  if (!cbs.canWrite()) {
    showToast(t("error.write_access"), "error");
    return;
  }
  try {
    await addPlantToPlotApi(plotId, pltId, 1);
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
    return;
  }

  const drawerSearch = document.getElementById(
    "drawer-plant-search",
  ) as HTMLInputElement | null;
  const sheetSearch = document.getElementById(
    "sheet-plant-search",
  ) as HTMLInputElement | null;
  if (drawerSearch) drawerSearch.value = "";
  if (sheetSearch) sheetSearch.value = "";
  cancelPendingPlantSearch();

  const resultsDiv =
    getDrawerSearchResults() ?? getSheetSearchResults();
  if (resultsDiv) resultsDiv.replaceChildren();

  invalidatePlotPanelCache(plotId);
  const assignmentsChanged = Promise.resolve(
    cbs.onPlantAssignmentsChanged([pltId]),
  );
  await Promise.all([
    cbs.fetchPlots(),
    assignmentsChanged,
    refreshSelectedPlotPlants(state, plotId, cbs).catch((err) => {
      showToast(getApiErrorMessage(err), "error");
    }),
  ]);
}

export async function removePlant(
  state: AppState,
  plotId: string,
  pltId: string,
  cbs: PlotCallbacks,
): Promise<void> {
  if (!cbs.canWrite()) {
    showToast(t("error.write_access"), "error");
    return;
  }
  if (!(await confirmDialog(t("plots.confirm_remove_plant"), t("common.remove")))) return;
  try {
    await removePlantFromPlotApi(plotId, pltId);
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
    return;
  }

  invalidatePlotPanelCache(plotId);
  const assignmentsChanged = Promise.resolve(
    cbs.onPlantAssignmentsChanged([pltId]),
  );
  await Promise.all([
    cbs.fetchPlots(),
    assignmentsChanged,
    refreshSelectedPlotPlants(state, plotId, cbs).catch((err) => {
      showToast(getApiErrorMessage(err), "error");
    }),
  ]);
}
