import type {
  AppState,
  AppTab,
  CalendarManualEventDraft,
  GardenIssue,
  GardenTask,
  HarvestEntry,
  Plant,
  Plot,
} from "./models";
import type { AuthUserProfile, GardenSummary, MediaAsset } from "../services/api";
import type { PlotCallbacks } from "../components/plotInteractions";

export type GardenSubMode =
  | "plants"
  | "inventory"
  | "indoor"
  | "procurement";

export type ActivitySubMode =
  | "tasks"
  | "calendar"
  | "journal"
  | "issues"
  | "harvest";

export type InsightsSubMode =
  | "care"
  | "statistics"
  | "analysis";

export type SubMode =
  | GardenSubMode
  | ActivitySubMode
  | InsightsSubMode;

export interface PlantCreatePrefill {
  name?: string;
  latin?: string;
  category?: string;
  bloom_month?: string;
  color?: string;
  hardiness?: string;
  height_cm?: number;
  light?: string;
  link?: string;
}

export interface AppContext {
  readonly state: AppState;
  getPlants(): Plant[];
  getPlots(): Plot[];
  getActiveTab(): AppTab;
  getSubMode(): SubMode;
  getAuthProfile(): AuthUserProfile | null;
  getGardenOptions(): GardenSummary[];
  getPlotCallbacks(): PlotCallbacks;

  setActiveTab(tab: AppTab): void;
  setSubMode(mode: SubMode): void;
  navigateToSubMode(
    mode: SubMode,
    opts?: { triggerLoads?: boolean },
  ): void;

  renderPlots(): void;
  renderPlantsTable(): void;
  renderCareView(): void;
  openCareForPlants(pltIds: string[]): void;
  loadCare(): Promise<void>;
  renderDataExportBars(): void;

  fetchPlots(): Promise<void>;
  ensurePlantsCacheLoaded(): Promise<void>;
  ensurePlantsLoaded(): Promise<void>;
  getPlantsCacheRevision(): number;
  setPlantsCache(plants: Plant[]): void;
  invalidatePlantsCache(): void;

  isMobile(): boolean;
  canWrite(): boolean;
  ensureWriteAccess(): boolean;
  showToast(
    msg: string,
    level?: "success" | "error",
  ): void;
  showFetchError(err: unknown): void;

  getPlantMediaPreviewById(): Map<string, MediaAsset | null>;
  refreshPlantMediaPreviews(pltIds: string[]): Promise<void>;
  refreshJournalMediaPreviews(
    entryIds: Array<string | number>,
  ): Promise<void>;
  extractPendingMediaFiles(
    data: Record<string, unknown>,
  ): File[];
  withoutPendingMediaFiles(
    data: Record<string, unknown>,
  ): Record<string, unknown>;
  uploadTargetMediaFiles(
    targetType: string,
    targetId: number | string,
    files: File[],
    options?: { gardenId?: number | null },
  ): Promise<void>;
  attachReadonlyMediaSection(
    container: HTMLElement,
    opts: {
      targetType: string;
      targetId: number | string;
      emptyText: string;
    },
  ): void;

  isOnline(): boolean;
  enqueueDraft(
    type: string,
    payload: Record<string, unknown>,
  ): Promise<number>;
  refreshOfflineIndicator(): Promise<void>;

  applyFocusedPlantFilter(plants: Plant[]): Plant[];
  setFocusedPlantIds(
    pltIds: string[] | null,
  ): void;
  clearFocusedPlantIds(): void;
  clearPlantSelection(): void;

  downloadJsonFile(filename: string, payload: unknown): void;
  confirmDialog(
    message: string,
    confirmLabel: string,
  ): Promise<boolean>;
  selectPlot(plotId: string): Promise<void>;

  focusPlantsInPlantsView(pltIds: string[]): void;
  openMapForPlots(plotIds: string[]): void;
  openBatchJournalForPlants(pltIds: string[]): void;
  openTaskForm(task?: GardenTask): Promise<void>;
  openHarvestForm(entry?: HarvestEntry): Promise<void>;
  openJournalComposer(): Promise<void>;
  openIssueForm(issue?: GardenIssue): Promise<void>;
  openCalendarEventComposer(
    prefill?: CalendarManualEventDraft,
  ): Promise<void>;
  loadTasks(): Promise<void>;
  loadJournalEntries(
    extra?: Record<string, string | number>,
  ): Promise<void>;
  setJournalOffset(offset: number): Promise<void>;
  loadIssues(): Promise<void>;
  loadWeather(): Promise<void>;
  refreshBadgeCounts(): Promise<void>;
  setIssuesOffset(offset: number): Promise<void>;
  loadInventoryItems(): Promise<void>;
  setInventoryOffset(offset: number): void;
  showAppStatus(
    message: string,
    actionLabel?: string,
    action?: () => void,
  ): void;
  openCreatePlantDialog(
    preselectedPlotId?: string,
    prefill?: PlantCreatePrefill,
  ): void;
}
