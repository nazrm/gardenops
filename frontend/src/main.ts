import "./core/trustedTypes"; // Must stay first — documents that no permissive default policy is installed
import { showOnboarding } from "./components/onboarding";
import type { CameraController } from "./components/camera";
import { initCamera } from "./components/camera";
import { filterPlants, renderPlantsMobileCards, renderPlantsTableBody, renderPlantsTableHead, sortPlants, syncPlantsSelectionState } from "./components/dataTables";
import { renderMediaGalleryLazy } from "./components/mediaGalleryLoader";
import { renderExportBar } from "./components/exportBar";
import type { ColumnDef, SortDir, SortField } from "./components/dataTables";
import {
  clampHouseSize,
  clearSelection,
  moveSelectedPlots,
  selectAll,
  toggleEditMode,
  undo,
  updateSelectionCount,
  updateUndoButton,
} from "./components/editMode";
import type { EditCallbacks } from "./components/editMode";
import {
  clearHighlight,
  handleGlobalSearch,
  handleSearchKeydown,
  hideGlobalSearchDropdowns,
  invalidateSearchCache,
} from "./components/globalSearch";
import { getAppShellMarkup } from "./components/layout";
import { renderMapGrid, applyPlotIndicators, syncSelectedPlots } from "./components/mapView";
import { confirmDialog, promptDialog, promptPasswordDialog } from "./components/dialogCore";
import { showCreatePlantDialogLazy, showCreatePlotDialogLazy, showCreateZoneDialogLazy, showDeleteMenuLazy, showEditPlantDialogLazy, showEditPlotDialogLazy, showElevationEditorLazy } from "./components/gardenDialogsLoader";
import type { AiPlantData } from "./components/overlays";
import { showPlantSearchDialog } from "./features/plantSearchFeature";
import type { PlantSearchDialogParams } from "./features/plantSearchFeature";
import { dismissPopover } from "./components/popover";
import type { ShadePanelController } from "./components/shadePanel";
import {
  invalidatePlotPanelCache,
  selectPlot,
} from "./components/plotInteractions";
import type { PlotCallbacks } from "./components/plotInteractions";
import { wireTopTabs } from "./components/tabs";
import {
  applyZoneVisibility,
  renderZoneToggles,
  zoneToggleZonesFromPlots,
} from "./components/zoneToggle";
import { showToast } from "./components/toast";
import { dismissBottomSheet } from "./components/bottomSheet";
import { dismissDrawer } from "./components/drawer";
import { showDiagnosePlantModal } from "./components/diagnosePlant";
import { showIdentifyPlantModal } from "./components/identifyPlant";
import { initAnalysisTab, renderAnalysisStarters } from "./tabs/analysisTab";
import { initThemeFeature, updateThemeIcon } from "./features/themeFeature";
import {
  initSnapshotsFeature,
  saveLayout,
  toggleSnapshotsDropdown,
  openMobileLayoutsSheet,
  exportMap,
} from "./features/snapshotsFeature";
import {
  initWeatherFeature,
  loadWeather,
} from "./features/weatherFeature";
import {
  initQuickActionsFeature,
  closeQuickActionSheet,
  toggleQuickActionSheet,
  isQuickActionSheetOpen,
} from "./features/quickActionsFeature";
import {
  initOfflineFeature,
  refreshOfflineIndicator,
} from "./features/offlineFeature";
import { showAuthGate, showForcedPasswordChangeGate } from "./features/authGate";
import { getPasskey, isPasskeySupported } from "./features/passkeys";
import {
  GRID_COLS,
  GRID_ROWS,
  HOUSE_MIN_HEIGHT,
  HOUSE_MIN_WIDTH,
  UNDO_STACK_LIMIT,
} from "./core/constants";
import { appName, appSlug } from "./core/branding";
import { queryButton, queryInput, querySelect } from "./core/dom";
import { getLocale, localizeRoot, setLocale, subscribeLocaleChange, t } from "./core/i18n";
import type {
  AppState,
  AppTab,
  CalendarManualEventDraft,
  CameraState,
  GardenIssue,
  GardenTask,
  Plant,
  Plot,
} from "./core/models";
import { setStaticTemplateHtml } from "./core/sanitize";
import { setFeatureGates, isFeatureEnabled } from "./core/featureGates";
import type { AuthUserProfile, GardenSummary } from "./services/api";
import { getExportUrl } from "./services/api";
import {
  ApiError,
  addPlantToPlotApi,
  aiPlantLookup,
  beginPasskeyReauthenticationApi,
  clearStoredAuthToken,
  createGardenApi,
  createPlantApi,
  createPlotApi,
  getNextPlantIdApi,
  createZoneApi,
  deletePlantApi,
  deletePlotApi,
  exportPlantsCsvApi,
  getAppVersionApi,
  getApiErrorMessage,
  getAuthMeApi,
  getAuthStatusApi,
  getGardensApi,
  getActiveGardenContext,
  getLayoutStateApi,
  getPlants,
  getPlotDeleteImpactApi,
  getPlotElevationsApi,
  getPlots,
  updatePlotElevationsApi,
  getShadeMapCalibrationApi,
  getShadeMapConfigApi,
  listShadeMapObstaclesApi,
  getShadeMapStateApi,
  hasStoredAuthToken,
  type HouseLayoutState,
  type LayoutExport,
  type LayoutExportPlot,
  type PersistedShadeMapState,
  type PlotElevations,
  importMapApi,
  importPlantsCsvApi,
  isAuthApiError,
  logoutApi,
  movePlantBetweenPlotsApi,
  removePlantFromPlotApi,
  updatePlotApi,
  reauthenticateApi,
  setActiveGardenContext,
  setOnAuthExpired,
  updateAuthMeSettingsApi,
  updateShadeMapStateApi,
  updatePlantApi,
  updateLayoutStateApi,
  getPlantApi,
  batchUpdatePlantsApi,
  listMediaApi,
  listMediaSummariesApi,
  deleteMediaAssetApi,
  removeMediaLinkApi,
  uploadMediaApi,
  fetchSeasonalSummary,
  fetchIssueApi,
  fetchPlotAlertsApi,
  finishPasskeyReauthenticationApi,
} from "./services/api";
import type {
  MediaAsset,
} from "./services/api";
import {
  initOfflineQueue,
  clearOfflineQueue,
  enqueueDraft,
  isOnline,
} from "./services/offlineQueue";
import {
  clearPrimedInviteToken,
  primeInviteTokenFromLocation,
} from "./core/urlSecurity";
import { initErrorReporter } from "./services/errorReporter";
import type {
  AppContext,
  PlantCreatePrefill,
  SubMode,
} from "./core/appContext";
import {
  initTasksTab,
  loadTasks,
  syncTasksViewButtons,
  getTasksView,
  setTasksView,
  setTasksOffset,
  openTaskForm as openTaskDialog,
} from "./tabs/tasksTab";
import {
  initHarvestTab,
  loadHarvest,
  setHarvestOffset,
} from "./tabs/harvestTab";
import {
  initProcurementTab,
  loadProcurement,
  setProcurementOffset,
} from "./tabs/procurementTab";
import {
  initNotificationsFeature,
  loadNotificationCount,
} from "./features/notificationsFeature";
import {
  initSavedViewsFeature,
} from "./features/savedViewsFeature";
import {
  initCareTab,
  loadCare,
  renderCareView,
  openCareForPlants,
} from "./tabs/careTab";
import {
  setIndoorPlotId,
  setOnAddPlant,
  setOnEditPlant,
  loadIndoorPlants,
  renderIndoorPlants,
} from "./tabs/indoorTab";

// ── State ──────────────────────────────────────────────────
const state: AppState = {
  plots: [],
  plantsCache: [],
  selectedPlotId: null,
  selectedPlotIds: new Set(),
  sunlitPlotIds: new Set(),
  editMode: false,
  housePosition: { row: 9, col: 6 },
  houseSize: { width: 12, height: 8 },
  northDegrees: 0,
  gridRows: GRID_ROWS,
  gridCols: GRID_COLS,
  undoStack: [],
  highlightedPlotIds: new Set(),
  highlightedPlantName: "",
  plotAlerts: null,
};

const gatedFeatureInitState = {
  notifications: false,
  savedViews: false,
  tasks: false,
  procurement: false,
  care: false,
  weather: false,
  analysis: false,
};

// ── Batch selection state ──────────────────────────────────
const selectedPlantIds = new Set<string>();

// ── Media / Navigation state ──────────────────────────────
const MEDIA_SUMMARY_BATCH_SIZE = 80;
const plantMediaPreviewById = new Map<string, MediaAsset | null>();
let plantMediaPreviewSeq = 0;
let focusedPlantIds: Set<string> | null = null;

type PrimaryContentTab = Exclude<AppTab, "map" | "admin">;

const GARDEN_SUB_MODES = ["plants", "inventory", "indoor", "procurement"] as const;
const ACTIVITY_SUB_MODES = ["tasks", "calendar", "journal", "issues", "harvest"] as const;
const INSIGHTS_SUB_MODES = ["care", "statistics", "analysis"] as const;
const PRIMARY_TAB_ORDER: AppTab[] = ["map", "garden", "activity", "insights", "admin"];

const SUB_MODE_META: Record<SubMode, {
  parentTab: PrimaryContentTab;
  feature: string | null;
  panelId?: string;
  rootViewId?: string;
  supportsSavedViews: boolean;
}> = {
  plants: { parentTab: "garden", feature: null, panelId: "plants-tab-content", supportsSavedViews: true },
  inventory: { parentTab: "garden", feature: "inventory", panelId: "inventory-tab-content", supportsSavedViews: true },
  indoor: { parentTab: "garden", feature: null, panelId: "indoor-tab-content", supportsSavedViews: false },
  procurement: { parentTab: "garden", feature: "procurement", panelId: "procurement-tab-content", supportsSavedViews: true },
  tasks: { parentTab: "activity", feature: "tasks", panelId: "tasks-tab-content", supportsSavedViews: true },
  calendar: { parentTab: "activity", feature: "calendar", panelId: "calendar-tab-content", supportsSavedViews: true },
  journal: { parentTab: "activity", feature: null, panelId: "journal-tab-content", supportsSavedViews: true },
  issues: { parentTab: "activity", feature: "issues", panelId: "issues-tab-content", supportsSavedViews: true },
  harvest: { parentTab: "activity", feature: null, panelId: "harvest-tab-content", supportsSavedViews: true },
  care: { parentTab: "insights", feature: "care", rootViewId: "care-view", supportsSavedViews: false },
  statistics: { parentTab: "insights", feature: "statistics", rootViewId: "statistics-view", supportsSavedViews: false },
  analysis: { parentTab: "insights", feature: "planner", rootViewId: "analysis-view", supportsSavedViews: false },
};

// ── Saved views state ────────────────────────────────────

function isSubMode(value: string): value is SubMode {
  return Object.prototype.hasOwnProperty.call(SUB_MODE_META, value);
}

function isPrimaryContentTab(tab: AppTab): tab is PrimaryContentTab {
  return tab === "garden" || tab === "activity" || tab === "insights";
}

function parentTabForSubMode(mode: SubMode): PrimaryContentTab {
  return SUB_MODE_META[mode].parentTab;
}

function getSubModesForTab(tab: PrimaryContentTab): readonly SubMode[] {
  switch (tab) {
    case "garden":
      return GARDEN_SUB_MODES;
    case "activity":
      return ACTIVITY_SUB_MODES;
    case "insights":
      return INSIGHTS_SUB_MODES;
  }
}

function isSubModeEnabled(mode: SubMode): boolean {
  const feature = SUB_MODE_META[mode].feature;
  return feature === null || isFeatureEnabled(feature);
}

function supportsSavedViews(mode: SubMode): boolean {
  return SUB_MODE_META[mode].supportsSavedViews;
}

function defaultSubModeForTab(tab: PrimaryContentTab): SubMode {
  const modes = getSubModesForTab(tab);
  const fallback = modes[0];
  if (!fallback) {
    throw new Error(`missing sub-mode fallback for ${tab}`);
  }
  return modes.find((mode) => isSubModeEnabled(mode)) ?? fallback;
}

function isTabEnabled(tab: AppTab): boolean {
  if (tab === "map") return true;
  if (tab === "admin") return isFeatureEnabled("admin_panel");
  return getSubModesForTab(tab).some((mode) => isSubModeEnabled(mode));
}

function loadFromStorage<T>(
  key: string,
  parse: (raw: string) => T,
  fallback: T,
): T {
  const stored = localStorage.getItem(key);
  if (!stored) return fallback;
  try {
    return parse(stored);
  } catch {
    return fallback;
  }
}

function loadActiveTab(): AppTab {
  return loadFromStorage<AppTab>(
    "gardenops-tab",
    (raw) => {
      if (raw === "map" || raw === "garden" || raw === "activity" || raw === "insights" || raw === "admin") {
        return raw;
      }
      if (raw === "plants") return "garden";
      if (raw === "care" || raw === "analysis" || raw === "statistics") return "insights";
      throw new Error("invalid tab");
    },
    "map",
  );
}

function loadSubMode(): SubMode {
  return loadFromStorage<SubMode>(
    "gardenops-sub-mode",
    (raw) => {
      if (isSubMode(raw)) return raw;
      throw new Error("invalid sub-mode");
    },
    (() => {
      const legacyTab = localStorage.getItem("gardenops-tab");
      if (legacyTab === "care" || legacyTab === "analysis" || legacyTab === "statistics") {
        return legacyTab;
      }
      return "plants";
    })(),
  );
}

let activeTab: AppTab = loadActiveTab();
let subMode: SubMode = loadSubMode();

function normalizeNavigation(tab: AppTab, mode: SubMode): { tab: AppTab; subMode: SubMode } {
  const normalizedMode = isSubModeEnabled(mode)
    ? mode
    : defaultSubModeForTab(parentTabForSubMode(mode));

  if (isAdminMfaSetupRequired()) {
    return { tab: "admin", subMode: normalizedMode };
  }

  let normalizedTab = tab;
  if (!isTabEnabled(normalizedTab)) {
    normalizedTab = PRIMARY_TAB_ORDER.find((candidate) => isTabEnabled(candidate)) ?? "map";
  }

  if (isPrimaryContentTab(normalizedTab)) {
    return {
      tab: normalizedTab,
      subMode:
        parentTabForSubMode(normalizedMode) === normalizedTab && isSubModeEnabled(normalizedMode)
          ? normalizedMode
          : defaultSubModeForTab(normalizedTab),
    };
  }

  return { tab: normalizedTab, subMode: normalizedMode };
}

function persistNavigationState(): void {
  localStorage.setItem("gardenops-tab", activeTab);
  localStorage.setItem("gardenops-sub-mode", subMode);
}

const mapInteraction = {
  draggedPlotId: null as string | null,
  dragStartPosition: null as { row: number; col: number } | null,
  houseMoveSession: null as {
    startX: number;
    startY: number;
    startRow: number;
    startCol: number;
    cellWidth: number;
    cellHeight: number;
    prevRow: number;
    prevCol: number;
  } | null,
  houseResizeSession: null as {
    startX: number;
    startY: number;
    startWidth: number;
    startHeight: number;
    startHouse: { row: number; col: number; width: number; height: number };
  } | null,
  hiddenZones: new Set<string>(),
  activeCatFilter: null as string | null,
  showElevation: false,
  elevationCache: null as PlotElevations | null,
};
function loadSort(): { field: SortField; dir: SortDir } {
  return loadFromStorage(
    "gardenops-sort",
    (raw) => {
      const obj = JSON.parse(raw) as { field: string; dir: string };
      return { field: obj.field as SortField, dir: obj.dir as SortDir };
    },
    { field: "bloom_month" as SortField, dir: "asc" as SortDir },
  );
}

const _initSort = loadSort();
let cameraCtrl: CameraController | null = null;
let shadePanel: ShadePanelController | null = null;
let shadePanelControllerPromise: Promise<ShadePanelController> | null = null;
let _cameraState: CameraState = { x: 0, y: 0, zoom: 1 };
let appStatusAction: (() => void) | null = null;
let layoutPersistTimer: number | null = null;
let shadeMapPersistTimer: number | null = null;
let shadeMapPanelLoadedGardenId: number | null = null;
let shadeMapPanelLoadingGardenId: number | null = null;
let shadeMapPanelLoadPromise: Promise<void> | null = null;
let shadeMapPanelLoadEpoch = 0;
let appVersionPollTimer: number | null = null;
let appVersionVisibilityHandlerBound = false;
let localeUiSubscriptionBound = false;
let gardenOptions: GardenSummary[] = [];
let authProfile: AuthUserProfile | null = null;
let canWriteInGarden = false;
let gardenContextAvailable = false;
let displayedAppVersion = __APP_VERSION__;
let displayedAppVersionUpdatedAtMs: number | null = null;
const APP_VERSION_POLL_MS = 5 * 60_000;

function isAdminMfaSetupRequired(): boolean {
  return Boolean(authProfile?.role === "admin" && authProfile?.mfa_setup_required);
}

const TAB_TITLE_KEYS: Record<Exclude<AppTab, "admin">, string> = {
  map: "nav.map",
  garden: "nav.garden",
  activity: "nav.activity",
  insights: "nav.insights",
};

function adminTabLabel(): string {
  return authProfile?.role === "admin" ? t("nav.settings_admin") : t("nav.settings_user");
}

function tabTitle(tab: AppTab): string {
  if (tab === "admin") return adminTabLabel();
  return t(TAB_TITLE_KEYS[tab]);
}

function syncAdminTabLabels(): void {
  const label = adminTabLabel();
  const title = authProfile?.role === "admin"
    ? t("nav.settings_admin")
    : t("nav.settings_user");
  document.querySelectorAll<HTMLElement>("#top-tab-admin, #mobile-admin-btn").forEach((el) => {
    el.textContent = label;
    el.setAttribute("aria-label", label);
    el.setAttribute("title", title);
  });
}

function syncPrimaryTabLabels(): void {
  (["map", "garden", "activity", "insights"] as const).forEach((tab) => {
    const label = t(TAB_TITLE_KEYS[tab]);
    document.querySelectorAll<HTMLElement>(`[data-tab="${tab}"]`).forEach((el) => {
      const labelSlot = el.querySelector<HTMLElement>("[data-tab-label]");
      if (labelSlot) {
        labelSlot.textContent = label;
      } else {
        el.textContent = label;
      }
      el.setAttribute("aria-label", label);
      el.setAttribute("title", label);
    });
  });
}

function getAuthButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("[data-auth-trigger]"));
}

function getGardenSelects(): HTMLSelectElement[] {
  return Array.from(document.querySelectorAll<HTMLSelectElement>("[data-garden-select]"));
}

function getGardenCreateButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("[data-garden-create]"));
}

function getGardenRoleChips(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-garden-role]"));
}

function getLocaleButtons(): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll<HTMLButtonElement>("[data-locale-option]"));
}

function syncLocaleButtons(): void {
  const locale = getLocale();
  getLocaleButtons().forEach((button) => {
    const isActive = button.dataset["localeOption"] === locale;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

async function persistLocalePreference(nextLocale: "en" | "no"): Promise<void> {
  setLocale(nextLocale);
  if (authProfile) {
    authProfile.language = nextLocale;
  }
  try {
    await updateAuthMeSettingsApi({ language: nextLocale });
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }
}

function canCurrentUserCreateGarden(): boolean {
  const globalRole = authProfile?.role ?? "viewer";
  if (globalRole === "admin") return true;
  if (globalRole !== "editor") return false;
  return !gardenOptions.some((garden) => garden.owned_by_current_user);
}

function formatRelativeUpdatedLabel(lastUpdatedAtMs: number | null): string {
  if (!lastUpdatedAtMs || !Number.isFinite(lastUpdatedAtMs) || lastUpdatedAtMs <= 0) {
    return t("version.updated_recently");
  }
  const diffMs = Math.max(0, Date.now() - lastUpdatedAtMs);
  const minuteMs = 60_000;
  const hourMs = 60 * minuteMs;
  const dayMs = 24 * hourMs;
  if (diffMs < minuteMs) return t("version.updated_just_now");
  if (diffMs < hourMs) {
    const minutes = Math.max(1, Math.floor(diffMs / minuteMs));
    return t("version.updated_minutes", { count: minutes });
  }
  if (diffMs < dayMs) {
    const hours = Math.max(1, Math.floor(diffMs / hourMs));
    return t("version.updated_hours", { count: hours });
  }
  const days = Math.max(1, Math.floor(diffMs / dayMs));
  return t("version.updated_days", { count: days });
}

function formatUpdatedTitle(lastUpdatedAtMs: number | null): string {
  if (!lastUpdatedAtMs || !Number.isFinite(lastUpdatedAtMs) || lastUpdatedAtMs <= 0) {
    return t("version.unknown_title");
  }
  return new Date(lastUpdatedAtMs).toLocaleString(getLocale() === "no" ? "no-NO" : "en-US");
}

function setDisplayedAppVersion(version: string, lastUpdatedAtMs: number | null = null): void {
  displayedAppVersion = version;
  displayedAppVersionUpdatedAtMs = lastUpdatedAtMs;
  document.querySelectorAll<HTMLElement>(".app-version").forEach((el) => {
    el.textContent = version;
    el.title = t("version.title", { version });
  });
  const updatedLabel = formatRelativeUpdatedLabel(lastUpdatedAtMs);
  const updatedTitle = formatUpdatedTitle(lastUpdatedAtMs);
  document.querySelectorAll<HTMLElement>(".app-version-updated").forEach((el) => {
    el.textContent = updatedLabel;
    el.title = updatedTitle;
  });
}

async function refreshAppVersionDisplay(): Promise<void> {
  try {
    const info = await getAppVersionApi();
    setDisplayedAppVersion(
      info.version?.trim() || __APP_VERSION__,
      typeof info.last_updated_at_ms === "number" ? info.last_updated_at_ms : null,
    );
  } catch {
    setDisplayedAppVersion(__APP_VERSION__);
  }
}

function startAppVersionPolling(): void {
  setDisplayedAppVersion(__APP_VERSION__);
  if (appVersionPollTimer !== null) {
    window.clearInterval(appVersionPollTimer);
  }
  void refreshAppVersionDisplay();
  appVersionPollTimer = window.setInterval(() => {
    if (document.visibilityState === "visible") {
      void refreshAppVersionDisplay();
    }
  }, APP_VERSION_POLL_MS);
  if (!appVersionVisibilityHandlerBound) {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        void refreshAppVersionDisplay();
      }
    });
    appVersionVisibilityHandlerBound = true;
  }
}

function applyLocalizedShellText(root: ParentNode = document): void {
  localizeRoot(root);
  syncPrimaryTabLabels();
  syncAdminTabLabels();
  syncLocaleButtons();
  document.querySelectorAll<HTMLButtonElement>("#mobile-theme-toggle").forEach((button) => {
    button.textContent = t("common.theme");
    button.title = t("nav.toggle_theme");
    button.setAttribute("aria-label", t("nav.toggle_theme"));
  });
  updateAuthButton();
  updateMobileHeader();
  setDisplayedAppVersion(displayedAppVersion, displayedAppVersionUpdatedAtMs);
}

function refreshLocalizedSignedInViews(): void {
  plantTableState.columns = plantTableState.columns.map((column) => ({ key: column.key, label: plantColumnLabel(column.key) }));
  updatePlantCsvActionLabels();
  updateThemeIcon();
  syncMobileCapabilities();
  shadePanel?.refreshLocale();
  updateBatchBar();
  if (document.getElementById("plants-view")) {
    renderPlantsTable();
  }
  if (subMode === "journal") {
    renderJournalView();
  }
  if (subMode === "inventory") {
    void loadInventoryItems();
  }
  if (subMode === "calendar") {
    refreshCalendarTabLocalization();
  }
  if (subMode === "care") {
    renderCareView();
  }
  renderDataExportBars();
  if (activeTab === "insights" && subMode === "statistics") {
    void loadStatistics();
  }
  if (!document.querySelector("#analysis-messages .chat-bubble")) {
    renderAnalysisStarters();
  }
  if (authProfile && activeTab === "admin") {
    refreshAdminPanelLocalization();
  }
}

type MobileMapSheetId = "mobile-map-layouts-sheet" | "mobile-map-tools-sheet";

const MOBILE_MAP_SHEET_IDS: MobileMapSheetId[] = [
  "mobile-map-layouts-sheet",
  "mobile-map-tools-sheet",
];

function getTopLevelShadeDisclosures(): HTMLDetailsElement[] {
  return Array.from(document.querySelectorAll<HTMLDetailsElement>("#shade-panel > .shade-disclosure"));
}

function getMobileKeyboardOffset(): number {
  if (!window.visualViewport) return 0;
  const keyboardHeight = window.innerHeight - window.visualViewport.height;
  return keyboardHeight > 50 ? keyboardHeight : 0;
}

function syncMobileViewportOffset(): void {
  document.documentElement.style.setProperty(
    "--mobile-keyboard-offset",
    `${getMobileKeyboardOffset()}px`,
  );
}

function syncMobileShadeDisclosureState(): void {
  const backdrop = document.getElementById("shade-mobile-backdrop");
  if (!(backdrop instanceof HTMLElement)) return;
  const hasOpenDisclosure = isMobile()
    && activeTab === "map"
    && getTopLevelShadeDisclosures().some((details) => details.open);
  backdrop.classList.toggle("shade-mobile-backdrop--visible", hasOpenDisclosure);
  backdrop.setAttribute("aria-hidden", hasOpenDisclosure ? "false" : "true");
  document.body.classList.toggle("shade-mobile-disclosure-open", hasOpenDisclosure);
}

function closeMobileShadeDisclosures(): void {
  getTopLevelShadeDisclosures().forEach((details) => {
    if (details.open) details.open = false;
  });
  syncMobileShadeDisclosureState();
}

function maybeCenterFocusedMobileField(target: EventTarget | null): void {
  if (!isMobile()) return;
  if (
    !(target instanceof HTMLInputElement)
    && !(target instanceof HTMLTextAreaElement)
    && !(target instanceof HTMLSelectElement)
  ) {
    return;
  }
  const overlay = target.closest(
    ".mobile-utility-sheet--open, .mobile-map-sheet--open, #shade-panel > .shade-disclosure[open]",
  );
  if (!(overlay instanceof HTMLElement)) return;
  window.setTimeout(() => {
    target.scrollIntoView({ block: "center", behavior: "smooth" });
  }, 140);
}

function setMobileUtilityOpen(open: boolean): void {
  const sheet = document.getElementById("mobile-utility-sheet");
  const backdrop = document.getElementById("mobile-utility-backdrop");
  const trigger = queryButton("mobile-utility-btn");
  if (!sheet || !backdrop || !trigger) return;
  if (open) {
    setMobileMapSheetOpen(null);
    closeMobileShadeDisclosures();
  }

  sheet.classList.toggle("mobile-utility-sheet--open", open);
  backdrop.classList.toggle("mobile-utility-backdrop--visible", open);
  sheet.setAttribute("aria-hidden", open ? "false" : "true");
  backdrop.setAttribute("aria-hidden", open ? "false" : "true");
  trigger.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("mobile-utility-open", open);
}

function setMobileMapSheetOpen(sheetId: MobileMapSheetId | null): void {
  const backdrop = document.getElementById("mobile-map-sheet-backdrop");
  const layoutsTrigger = queryButton("mobile-map-layouts-btn");
  const toolsTrigger = queryButton("mobile-map-tools-btn");
  if (!backdrop || !layoutsTrigger || !toolsTrigger) return;

  const nextOpen = sheetId && isMobile() && activeTab === "map" ? sheetId : null;
  if (nextOpen) {
    setMobileUtilityOpen(false);
    closeMobileShadeDisclosures();
    const dropdown = document.getElementById("snapshots-dropdown");
    if (dropdown instanceof HTMLElement) dropdown.hidden = true;
  }

  MOBILE_MAP_SHEET_IDS.forEach((id) => {
    const sheet = document.getElementById(id);
    if (!(sheet instanceof HTMLElement)) return;
    const isOpen = id === nextOpen;
    sheet.classList.toggle("mobile-map-sheet--open", isOpen);
    sheet.setAttribute("aria-hidden", isOpen ? "false" : "true");
  });

  backdrop.classList.toggle("mobile-map-sheet-backdrop--visible", nextOpen !== null);
  backdrop.setAttribute("aria-hidden", nextOpen ? "false" : "true");
  layoutsTrigger.setAttribute("aria-expanded", String(nextOpen === "mobile-map-layouts-sheet"));
  toolsTrigger.setAttribute("aria-expanded", String(nextOpen === "mobile-map-tools-sheet"));
  document.body.classList.toggle("mobile-map-sheet-open", nextOpen !== null);
}

function updateMobileHeader(): void {
  const titleEl = document.getElementById("mobile-view-title");
  const gardenEl = document.getElementById("mobile-garden-name");
  if (titleEl) {
    titleEl.textContent = tabTitle(activeTab);
  }
  if (gardenEl) {
    const activeGardenId = getActiveGardenContext();
    const activeGarden = activeGardenId === null
      ? null
      : gardenOptions.find((garden) => garden.id === activeGardenId) ?? null;
    gardenEl.textContent = activeGarden?.name ?? t("nav.active_garden");
  }
}

const editCbs: EditCallbacks = {
  renderPlots,
  fetchPlots,
  persistHouse: persistHouseGeometry,
};

const plotCbs: PlotCallbacks = {
  fetchPlots,
  isMobile,
  canWrite: () => canWriteInGarden,
  deletePlot,
  onEditPlant: (plant) => openEditPlantDialog(plant),
  onEditPlot: (plotId) => openEditPlotDialog(plotId),
  onPlantAssignmentsChanged: async (pltIds) => {
    if (pltIds && pltIds.length > 0 && plantsCacheLoaded) {
      try {
        await refreshPlantsById(pltIds);
        return;
      } catch (err) {
        showFetchError(err);
      }
    }
    invalidatePlantsCache();
    if (activeTab === "garden" || activeTab === "activity") void ensurePlantsLoaded();
    if (activeTab === "insights" && subMode === "care") { void loadCare(); void loadWeather(); }
  },
  onPlotFocusChanged: (plotId) => {
    shadePanel?.setSelectedPlot(plotId);
  },
  onViewJournal: (plotId) => {
    navigateToSubMode("journal");
    void ensureJournalTabInitialized().then(async (journalTab) => {
      journalTab.resetJournalFilters();
      journalTab.setJournalOffset(0);
      await journalTab.loadJournalEntries({ plot_id: plotId });
    });
  },
  onMediaTargetsChanged: (targets) => {
    void refreshPlantMediaPreviews(
      targets
        .filter((target) => target.target_type === "plant")
        .map((target) => target.target_id),
    );
    void refreshJournalMediaPreviews(
      targets
        .filter((target) => target.target_type === "journal_entry")
        .map((target) => target.target_id),
    );
  },
  onCreatePlant: (plotId) => openCreatePlantDialog(plotId),
  onCreateCalendarEvent: (prefill) => {
    void openCalendarEventComposer(prefill);
  },
};

const WRITE_CONTROL_IDS = [
  "edit-mode-btn",
  "create-zone-btn",
  "save-layout-btn",
  "snapshots-btn",
  "import-map-btn",
  "import-csv-btn",
  "add-plant-btn",
  "generate-care-btn",
  "elevation-edit-btn",
  "map-direction-input",
  "map-direction-slider",
  "map-direction-dec-btn",
  "map-direction-inc-btn",
  "mobile-map-save-btn",
  "mobile-map-layouts-save-btn",
  "mobile-create-zone-btn",
  "mobile-import-map-btn",
  "mobile-map-direction-input",
  "mobile-map-direction-dec-btn",
  "mobile-map-direction-inc-btn",
  "mobile-grid-cols-input",
  "mobile-grid-rows-input",
  "mobile-grid-dims-apply-btn",
  "shade-calibration-save-btn",
  "shade-calibration-reset-btn",
  "shade-obstacle-save-btn",
  "shade-obstacle-delete-btn",
  "inventory-add-btn",
  "journal-add-btn",
  "tasks-add-btn",
  "tasks-generate-btn",
  "tasks-refresh-desc-btn",
  "issues-add-btn",
  "harvest-add-btn",
  "procurement-add-btn",
];

// ── AppContext (dependency injection for extracted modules) ──
const appContext: AppContext = {
  get state() { return state; },
  getPlants: () => state.plantsCache,
  getPlots: () => state.plots,
  getActiveTab: () => activeTab,
  getSubMode: () => subMode,
  getAuthProfile: () => authProfile,
  getGardenOptions: () => gardenOptions,
  getPlotCallbacks: () => plotCbs,
  setActiveTab: (tab) => setActiveTab(tab),
  setSubMode: (mode) => setSubMode(mode),
  navigateToSubMode: (mode, opts) => navigateToSubMode(mode, opts),
  renderPlots: () => renderPlots(),
  renderPlantsTable: () => renderPlantsTable(),
  renderCareView: () => renderCareView(),
  renderDataExportBars: () => renderDataExportBars(),
  fetchPlots: () => fetchPlots(),
  ensurePlantsLoaded: () => ensurePlantsLoaded(),
  invalidatePlantsCache: () => invalidatePlantsCache(),
  isMobile,
  canWrite: () => canWriteInGarden,
  ensureWriteAccess,
  showToast: (msg, level) => showToast(msg, level),
  showFetchError,
  getPlantMediaPreviewById: () => plantMediaPreviewById,
  refreshPlantMediaPreviews,
  refreshJournalMediaPreviews,
  extractPendingMediaFiles,
  withoutPendingMediaFiles,
  uploadTargetMediaFiles,
  attachReadonlyMediaSection,
  isOnline,
  enqueueDraft,
  refreshOfflineIndicator,
  applyFocusedPlantFilter,
  setFocusedPlantIds,
  clearFocusedPlantIds,
  clearPlantSelection,
  downloadJsonFile,
  confirmDialog,
  selectPlot: (plotId) => selectPlot(state, plotId, plotCbs),
  focusPlantsInPlantsView,
  openMapForPlots,
  openBatchJournalForPlants,
  openTaskForm: (task) => openTaskForm(task),
  openJournalComposer: () => openJournalComposer(),
  openIssueForm: (issue) => openIssueForm(issue),
  openCalendarEventComposer: (prefill) => openCalendarEventComposer(prefill),
  loadTasks: () => loadTasks(),
  loadJournalEntries: (extra) => loadJournalEntries(extra),
  setJournalOffset: (offset) => setJournalOffset(offset),
  loadIssues: () => loadIssues(),
  loadWeather: () => loadWeather(),
  refreshBadgeCounts: () => loadNotificationCount(),
  setIssuesOffset: (offset) => setIssuesOffset(offset),
  loadInventoryItems: () => loadInventoryItems(),
  setInventoryOffset: (offset) => inventoryTabModule?.setInventoryOffset(offset),
  showAppStatus,
  openCreatePlantDialog: (preselectedPlotId, prefill) =>
    void openCreatePlantDialog(preselectedPlotId, prefill),
};

type AdminPanelModule = typeof import("./components/adminPanel");
type JournalTabModule = typeof import("./tabs/journalTab");
type IssuesTabModule = typeof import("./tabs/issuesTab");
type CalendarTabModule = typeof import("./tabs/calendarTab");
type AttachIssueHistorySectionFn = IssuesTabModule["attachIssueHistorySection"];
type InventoryTabModule = typeof import("./tabs/inventoryTab");
type StatisticsTabModule = typeof import("./tabs/statisticsTab");

let adminPanelModule: AdminPanelModule | null = null;
let adminPanelModulePromise: Promise<AdminPanelModule> | null = null;
let journalTabModule: JournalTabModule | null = null;
let journalTabModulePromise: Promise<JournalTabModule> | null = null;
let issuesTabModule: IssuesTabModule | null = null;
let issuesTabModulePromise: Promise<IssuesTabModule> | null = null;
let calendarTabModule: CalendarTabModule | null = null;
let calendarTabModulePromise: Promise<CalendarTabModule> | null = null;
let inventoryTabModule: InventoryTabModule | null = null;
let inventoryTabModulePromise: Promise<InventoryTabModule> | null = null;
let statisticsTabModule: StatisticsTabModule | null = null;
let statisticsTabModulePromise: Promise<StatisticsTabModule> | null = null;

function ensureAdminPanelModule(): Promise<AdminPanelModule> {
  adminPanelModulePromise ??= import("./components/adminPanel")
    .then((mod) => {
      adminPanelModule = mod;
      mod.setAdminCallbacks({
        onSignOut: () => {
          updateAuthButton();
          void refreshGardenContext();
          refreshDataAfterAuthChange();
          showToast(t("auth.signed_out"), "success");
        },
        onAuthStateChanged: () => {
          void refreshGardenContext().then(() => {
            refreshDataAfterAuthChange();
          });
        },
        onGardenStateChanged: async () => {
          await refreshGardenContext();
          await refreshGardenDataForCurrentContext();
        },
        onRestartOnboarding: async () => {
          await refreshGardenContext();
          const needsOnboarding = await checkOnboardingNeeded();
          if (!needsOnboarding) {
            await refreshGardenDataForCurrentContext();
          }
        },
        getGardenContext: () => ({
          gardens: gardenOptions,
          activeGardenId: getActiveGardenContext(),
        }),
      });
      return mod;
    })
    .catch((err) => {
      adminPanelModulePromise = null;
      throw err;
    });
  return adminPanelModulePromise;
}

async function activateAdminPanel(): Promise<void> {
  const mod = await ensureAdminPanelModule();
  await mod.activateAdminPanel();
}

function refreshAdminPanelLocalization(): void {
  if (adminPanelModule) {
    adminPanelModule.refreshAdminPanelLocalization();
    return;
  }
  if (activeTab === "admin") {
    void ensureAdminPanelModule().then((mod) => mod.refreshAdminPanelLocalization());
  }
}

function ensureJournalTabInitialized(): Promise<JournalTabModule> {
  journalTabModulePromise ??= import("./tabs/journalTab")
    .then((mod) => {
      journalTabModule = mod;
      mod.initJournalTab(appContext);
      return mod;
    })
    .catch((err) => {
      journalTabModulePromise = null;
      throw err;
    });
  return journalTabModulePromise;
}

async function loadJournalEntries(extra?: Record<string, string | number>): Promise<void> {
  const mod = await ensureJournalTabInitialized();
  await mod.loadJournalEntries(extra);
}

async function setJournalOffset(offset: number): Promise<void> {
  const mod = await ensureJournalTabInitialized();
  mod.setJournalOffset(offset);
}

function renderJournalView(): void {
  journalTabModule?.renderJournalView();
}

async function openJournalComposer(): Promise<void> {
  const mod = await ensureJournalTabInitialized();
  mod.openJournalComposer();
}

async function openBatchJournalComposer(pltIds: string[], onClose: () => void): Promise<void> {
  const mod = await ensureJournalTabInitialized();
  mod.openBatchJournalComposer(pltIds, onClose);
}

async function uploadJournalMediaFiles(
  journalEntryId: string | number,
  files: File[],
  opts: { plantIds: string[]; plotIds: string[]; gardenId?: number | null },
): Promise<void> {
  const mod = await ensureJournalTabInitialized();
  await mod.uploadJournalMediaFiles(journalEntryId, files, opts);
}

async function refreshJournalMediaPreviews(entryIds: string[]): Promise<void> {
  if (entryIds.length === 0) return;
  const mod = await ensureJournalTabInitialized();
  await mod.refreshJournalMediaPreviews(entryIds);
}

function clearJournalMediaPreviewCache(): void {
  journalTabModule?.getJournalMediaPreviewById().clear();
}

function ensureIssuesTabInitialized(): Promise<IssuesTabModule> {
  issuesTabModulePromise ??= import("./tabs/issuesTab")
    .then((mod) => {
      issuesTabModule = mod;
      mod.initIssuesTab(appContext);
      return mod;
    })
    .catch((err) => {
      issuesTabModulePromise = null;
      throw err;
    });
  return issuesTabModulePromise;
}

async function loadIssues(): Promise<void> {
  const mod = await ensureIssuesTabInitialized();
  await mod.loadIssues();
}

async function setIssuesOffset(offset: number): Promise<void> {
  const mod = await ensureIssuesTabInitialized();
  mod.setIssuesOffset(offset);
}

async function openTaskForm(existingTask?: GardenTask): Promise<void> {
  openTaskDialog(existingTask);
}

async function openIssueForm(existingIssue?: GardenIssue): Promise<void> {
  const mod = await ensureIssuesTabInitialized();
  mod.openIssueForm(existingIssue);
}

const attachIssueHistorySection: AttachIssueHistorySectionFn = (
  dialog,
  issueId,
) => {
  void ensureIssuesTabInitialized().then((mod) => {
    mod.attachIssueHistorySection(dialog, issueId);
  });
};

function ensureCalendarTabInitialized(): Promise<CalendarTabModule> {
  calendarTabModulePromise ??= import("./tabs/calendarTab")
    .then((mod) => {
      calendarTabModule = mod;
      mod.initCalendarTab(appContext);
      return mod;
    })
    .catch((err) => {
      calendarTabModulePromise = null;
      throw err;
    });
  return calendarTabModulePromise;
}

async function loadCalendar(): Promise<void> {
  const mod = await ensureCalendarTabInitialized();
  await mod.loadCalendar();
}

async function openCalendarEventComposer(
  prefill?: CalendarManualEventDraft,
): Promise<void> {
  if (!ensureWriteAccess()) return;
  navigateToSubMode("calendar");
  const mod = await ensureCalendarTabInitialized();
  await mod.loadCalendar();
  mod.openCalendarManualEventComposer(prefill);
}

function refreshCalendarTabLocalization(): void {
  if (calendarTabModule) {
    calendarTabModule.refreshCalendarLocalization();
    return;
  }
  if (subMode === "calendar") {
    void ensureCalendarTabInitialized().then((mod) => mod.refreshCalendarLocalization());
  }
}

function ensureInventoryTabInitialized(): Promise<InventoryTabModule> {
  inventoryTabModulePromise ??= import("./tabs/inventoryTab")
    .then(async (mod) => {
      inventoryTabModule = mod;
      await ensureJournalTabInitialized();
      mod.initInventoryTab(appContext);
      return mod;
    })
    .catch((err) => {
      inventoryTabModulePromise = null;
      throw err;
    });
  return inventoryTabModulePromise;
}

async function loadInventoryItems(): Promise<void> {
  const mod = await ensureInventoryTabInitialized();
  await mod.loadInventoryItems();
}

function ensureStatisticsTabInitialized(): Promise<StatisticsTabModule> {
  statisticsTabModulePromise ??= import("./tabs/statisticsTab")
    .then((mod) => {
      statisticsTabModule = mod;
      mod.initStatisticsTab(appContext);
      return mod;
    })
    .catch((err) => {
      statisticsTabModulePromise = null;
      throw err;
    });
  return statisticsTabModulePromise;
}

async function loadStatistics(): Promise<void> {
  const mod = await ensureStatisticsTabInitialized();
  await mod.loadStatistics();
}

function resetStatisticsState(): void {
  statisticsTabModule?.resetStatisticsState();
}

type ShadePanelModule = typeof import("./components/shadePanel");

function ensureShadePanelController(): Promise<ShadePanelController> {
  if (shadePanel) {
    return Promise.resolve(shadePanel);
  }
  shadePanelControllerPromise ??= import("./components/shadePanel")
    .then((mod: ShadePanelModule) => {
      const panel = new mod.ShadePanelController({
        onStateChanged: scheduleShadeMapPersist,
        onSunlightSnapshot: (plotIds) => {
          const next = new Set(plotIds);
          if (samePlotIdSet(state.sunlitPlotIds, next)) return;
          const prev = state.sunlitPlotIds;
          state.sunlitPlotIds = next;
          if (!applySunlightDiff(prev, next)) {
            renderPlots();
          }
        },
      });
      panel.init();
      shadePanel = panel;
      syncShadePanelContext();
      return panel;
    })
    .catch((err) => {
      shadePanelControllerPromise = null;
      throw err;
    });
  return shadePanelControllerPromise;
}

// ── Plant table config ─────────────────────────────────────
const DEFAULT_COLUMN_KEYS = [
  "name",
  "latin",
  "plot_ids",
  "year_planted",
  "deer_resistant",
  "bloom_month",
  "color",
  "hardiness",
  "height_cm",
  "light",
  "link",
] as const;

function plantColumnLabel(key: string): string {
  return t(`plants.column_${key}`);
}

function getDefaultColumns(): ColumnDef[] {
  return DEFAULT_COLUMN_KEYS.map((key) => ({ key, label: plantColumnLabel(key) }));
}

function loadColumnOrder(): ColumnDef[] {
  return loadFromStorage(
    "gardenops-col-order",
    (raw) => {
      const keys: unknown = JSON.parse(raw);
      if (!Array.isArray(keys)) throw new Error("invalid");
      const byKey = new Map(
        getDefaultColumns().map((c) => [c.key, c]),
      );
      const ordered: ColumnDef[] = [];
      for (const k of keys as string[]) {
        const col = byKey.get(k);
        if (col) {
          ordered.push(col);
          byKey.delete(k);
        }
      }
      for (const col of byKey.values()) ordered.push(col);
      return ordered;
    },
    getDefaultColumns(),
  );
}

const DEFAULT_VISIBLE = new Set(
  DEFAULT_COLUMN_KEYS,
);

function loadVisibleColumns(): Set<string> {
  return loadFromStorage(
    "gardenops-plant-cols",
    (raw) => {
      const arr: unknown = JSON.parse(raw);
      if (!Array.isArray(arr)) throw new Error("invalid");
      return new Set(arr as string[]);
    },
    new Set(DEFAULT_VISIBLE),
  );
}

const plantTableState = {
  sortField: _initSort.field as SortField,
  sortDir: _initSort.dir as SortDir,
  columns: loadColumnOrder(),
  visibleColumns: loadVisibleColumns(),
};

function saveColumnOrder(): void {
  localStorage.setItem(
    "gardenops-col-order",
    JSON.stringify(plantTableState.columns.map((c) => c.key)),
  );
}

function saveVisibleColumns(): void {
  localStorage.setItem(
    "gardenops-plant-cols",
    JSON.stringify([...plantTableState.visibleColumns]),
  );
}

function isMobile(): boolean {
  return window.innerWidth <= 960;
}

function setFocusedPlantIds(pltIds: string[] | null): void {
  if (!pltIds || pltIds.length === 0) {
    focusedPlantIds = null;
    return;
  }
  focusedPlantIds = new Set(pltIds);
}

function clearFocusedPlantIds(): void {
  focusedPlantIds = null;
}

function applyFocusedPlantFilter(plants: Plant[]): Plant[] {
  if (!focusedPlantIds || focusedPlantIds.size === 0) {
    return plants;
  }
  return plants.filter((plant) => focusedPlantIds?.has(plant.plt_id));
}

function focusPlantsInPlantsView(pltIds: string[]): void {
  setFocusedPlantIds(pltIds);
  selectedPlantIds.clear();
  const search = queryInput("plants-search");
  const category = querySelect("plants-category");
  const presence = querySelect("plants-presence-filter");
  if (search) search.value = "";
  if (category) category.value = "";
  if (presence) presence.value = "all";
  navigateToSubMode("plants");
  updateBatchBar();
  renderPlantsTable();
}

function ensureGatedFeatureInitializers(): void {
  if (isFeatureEnabled("notifications")) {
    initNotificationsFeature(appContext);
    gatedFeatureInitState.notifications = true;
  }
  if (isFeatureEnabled("saved_views") && !gatedFeatureInitState.savedViews) {
    initSavedViewsFeature(appContext);
    gatedFeatureInitState.savedViews = true;
  }
  if (isFeatureEnabled("tasks") && !gatedFeatureInitState.tasks) {
    initTasksTab(appContext);
    gatedFeatureInitState.tasks = true;
  }
  if (isFeatureEnabled("procurement") && !gatedFeatureInitState.procurement) {
    initProcurementTab(appContext);
    gatedFeatureInitState.procurement = true;
  }
  if (isFeatureEnabled("care") && !gatedFeatureInitState.care) {
    initCareTab(appContext);
    gatedFeatureInitState.care = true;
  }
  if (isFeatureEnabled("weather") && !gatedFeatureInitState.weather) {
    initWeatherFeature(appContext);
    gatedFeatureInitState.weather = true;
  }
  if (isFeatureEnabled("planner") && !gatedFeatureInitState.analysis) {
    initAnalysisTab();
    gatedFeatureInitState.analysis = true;
  }
}

function openMapForPlots(plotIds: string[]): void {
  state.highlightedPlotIds = new Set(plotIds);
  setActiveTab("map");
  renderPlots();
  if (plotIds.length > 0) {
    const firstExisting = plotIds.find((plotId) => state.plots.some((plot) => plot.plot_id === plotId));
    if (firstExisting) {
      void selectPlot(state, firstExisting, plotCbs);
    }
  }
}


function openBatchJournalForPlants(pltIds: string[]): void {
  setFocusedPlantIds(pltIds);
  if (!canWriteInGarden) {
    navigateToSubMode("journal");
    void loadJournalEntries();
    return;
  }
  selectedPlantIds.clear();
  for (const id of pltIds) selectedPlantIds.add(id);
  navigateToSubMode("plants");
  updateBatchBar();
  renderPlantsTable();
  void openBatchJournalComposer(pltIds, clearPlantSelection);
}

function parsePlotIdInput(raw: string): string[] {
  const seen = new Set<string>();
  return raw
    .split(/[\n,|]+/)
    .map((value) => value.trim())
    .filter((value) => {
      if (!value || seen.has(value)) {
        return false;
      }
      seen.add(value);
      return true;
    });
}

// ── Layout & Setup ─────────────────────────────────────────
function setupLayout(): void {
  const app = document.getElementById("app");
  if (!app) return;

  document.title = appName();
  setStaticTemplateHtml(app, getAppShellMarkup());
  invalidateSearchCache();
  applyLocalizedShellText(app);
  if (!localeUiSubscriptionBound) {
    subscribeLocaleChange(() => {
      applyLocalizedShellText();
      refreshLocalizedSignedInViews();
    });
    localeUiSubscriptionBound = true;
  }
  startAppVersionPolling();
  updatePlantCsvActionLabels();
  wireTopTabs(setActiveTab);

  applyFeatureGateUi();
  document.querySelectorAll<HTMLButtonElement>(".mobile-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset["tab"] as AppTab | undefined;
      if (tab) setActiveTab(tab);
    });
  });

  const editModeBtn = queryButton("edit-mode-btn");
  const selectAllBtn = queryButton("select-all-btn");
  const clearBtn = queryButton("clear-selection-btn");
  const undoBtn = queryButton("undo-btn");
  const mobileUtilityBtn = queryButton("mobile-utility-btn");
  const mobileUtilityCloseBtn = queryButton("mobile-utility-close-btn");
  const mobileUtilityBackdrop = document.getElementById("mobile-utility-backdrop");

  mobileUtilityBtn?.addEventListener("click", () => {
    const sheet = document.getElementById("mobile-utility-sheet");
    const isOpen = sheet?.classList.contains("mobile-utility-sheet--open") ?? false;
    setMobileUtilityOpen(!isOpen);
  });
  mobileUtilityCloseBtn?.addEventListener("click", () => setMobileUtilityOpen(false));
  mobileUtilityBackdrop?.addEventListener("click", () => setMobileUtilityOpen(false));
  document.getElementById("mobile-admin-btn")?.addEventListener("click", () => {
    setActiveTab("admin");
  });

  getLocaleButtons().forEach((button) => {
    button.addEventListener("click", () => {
      const nextLocale = button.dataset["localeOption"] === "no" ? "no" : "en";
      void persistLocalePreference(nextLocale);
    });
  });

  editModeBtn?.addEventListener("click", () => {
    if (editModeBtn.disabled) return;
    toggleEditMode(state, editCbs);
    updateMapDirectionControlVisibility();
  });
  selectAllBtn?.addEventListener("click", () => selectAll(state, editCbs));
  clearBtn?.addEventListener("click", () => clearSelection(state, editCbs));
  undoBtn?.addEventListener("click", () => void undo(state, editCbs));

  const saveLayoutBtn = queryButton("save-layout-btn");
  const snapshotsBtn = queryButton("snapshots-btn");
  const mobileMapLayoutsBtn = queryButton("mobile-map-layouts-btn");
  const mobileMapSaveBtn = queryButton("mobile-map-save-btn");
  const mobileMapToolsBtn = queryButton("mobile-map-tools-btn");
  const mobileMapLayoutsCloseBtn = queryButton("mobile-map-layouts-close-btn");
  const mobileMapToolsCloseBtn = queryButton("mobile-map-tools-close-btn");
  const mobileMapSheetBackdrop = document.getElementById("mobile-map-sheet-backdrop");
  const mobileMapLayoutsSaveBtn = queryButton("mobile-map-layouts-save-btn");
  const shadeMobileBackdrop = document.getElementById("shade-mobile-backdrop");

  const exportMapBtn = queryButton("export-map-btn");
  const importMapBtn = queryButton("import-map-btn");
  const importMapInput = queryInput("import-map-input");
  const mapDirectionInput = queryInput("map-direction-input");
  const mapDirectionSlider = queryInput("map-direction-slider");
  const mapDirectionDecBtn = queryButton("map-direction-dec-btn");
  const mapDirectionIncBtn = queryButton("map-direction-inc-btn");
  const mobileMapDirectionInput = queryInput("mobile-map-direction-input");
  const mobileMapDirectionDecBtn = queryButton("mobile-map-direction-dec-btn");
  const mobileMapDirectionIncBtn = queryButton("mobile-map-direction-inc-btn");
  const mobileGridColsInput = queryInput("mobile-grid-cols-input");
  const mobileGridRowsInput = queryInput("mobile-grid-rows-input");
  const mobileGridDimsApplyBtn = queryButton("mobile-grid-dims-apply-btn");
  const createZoneBtn = queryButton("create-zone-btn");
  const mobileCreateZoneBtn = queryButton("mobile-create-zone-btn");
  const mobileExportMapBtn = queryButton("mobile-export-map-btn");
  const mobileImportMapBtn = queryButton("mobile-import-map-btn");
  const exportCsvBtn = queryButton("export-csv-btn");
  const importCsvBtn = queryButton("import-csv-btn");
  const importCsvInput = queryInput("import-csv-input");

  saveLayoutBtn?.addEventListener("click", () => void saveLayout());
  snapshotsBtn?.addEventListener("click", () => void toggleSnapshotsDropdown());
  mobileMapLayoutsBtn?.addEventListener("click", () => {
    const sheet = document.getElementById("mobile-map-layouts-sheet");
    const isOpen = sheet?.classList.contains("mobile-map-sheet--open") ?? false;
    if (isOpen) {
      setMobileMapSheetOpen(null);
      return;
    }
    void openMobileLayoutsSheet();
  });
  mobileMapSaveBtn?.addEventListener("click", () => void saveLayout());
  mobileMapToolsBtn?.addEventListener("click", () => {
    const sheet = document.getElementById("mobile-map-tools-sheet");
    const isOpen = sheet?.classList.contains("mobile-map-sheet--open") ?? false;
    setMobileMapSheetOpen(isOpen ? null : "mobile-map-tools-sheet");
  });
  mobileMapLayoutsCloseBtn?.addEventListener("click", () => setMobileMapSheetOpen(null));
  mobileMapToolsCloseBtn?.addEventListener("click", () => setMobileMapSheetOpen(null));
  mobileMapSheetBackdrop?.addEventListener("click", () => setMobileMapSheetOpen(null));
  shadeMobileBackdrop?.addEventListener("click", () => closeMobileShadeDisclosures());
  mobileMapLayoutsSaveBtn?.addEventListener("click", () => {
    void (async () => {
      const saved = await saveLayout();
      if (saved) {
        await openMobileLayoutsSheet();
      }
    })();
  });
  exportMapBtn?.addEventListener("click", () => void exportMap());
  mobileExportMapBtn?.addEventListener("click", () => void exportMap());
  importMapBtn?.addEventListener("click", () => importMapInput?.click());
  mobileImportMapBtn?.addEventListener("click", () => importMapInput?.click());
  importMapInput?.addEventListener("change", () => void importMap());
  const onDirectionInput = (raw: string, persistImmediately = false) => {
    const parsed = Number.parseInt(raw, 10);
    if (Number.isNaN(parsed)) return;
    state.northDegrees = normalizeDegrees(parsed);
    syncDirectionControls();
    renderDirectionLabels();
    syncShadePanelContext();
    if (persistImmediately) {
      void persistHouseGeometry().catch(showFetchError);
    } else {
      scheduleLayoutPersist();
    }
  };
  mapDirectionInput?.addEventListener("input", () => onDirectionInput(mapDirectionInput.value));
  mapDirectionInput?.addEventListener("change", () => onDirectionInput(mapDirectionInput.value, true));
  mapDirectionSlider?.addEventListener("input", () => onDirectionInput(mapDirectionSlider.value));
  mapDirectionSlider?.addEventListener("change", () => onDirectionInput(mapDirectionSlider.value, true));
  mapDirectionDecBtn?.addEventListener("click", () => onDirectionInput(String(state.northDegrees - 5), true));
  mapDirectionIncBtn?.addEventListener("click", () => onDirectionInput(String(state.northDegrees + 5), true));
  mobileMapDirectionInput?.addEventListener("input", () => onDirectionInput(mobileMapDirectionInput.value));
  mobileMapDirectionInput?.addEventListener("change", () => onDirectionInput(mobileMapDirectionInput.value, true));
  mobileMapDirectionDecBtn?.addEventListener("click", () => onDirectionInput(String(state.northDegrees - 5), true));
  mobileMapDirectionIncBtn?.addEventListener("click", () => onDirectionInput(String(state.northDegrees + 5), true));
  importCsvBtn?.addEventListener("click", () => importCsvInput?.click());
  importCsvInput?.addEventListener("change", () => void importPlantsCsv());
  exportCsvBtn?.addEventListener("click", exportPlantsCsv);

  const gridDimsApplyBtn = queryButton("grid-dims-apply-btn");
  gridDimsApplyBtn?.addEventListener("click", () => {
    const colsInput = queryInput("grid-cols-input");
    const rowsInput = queryInput("grid-rows-input");
    void applyGridDimensions(colsInput?.value ?? "", rowsInput?.value ?? "");
  });
  mobileGridDimsApplyBtn?.addEventListener("click", () => {
    void applyGridDimensions(mobileGridColsInput?.value ?? "", mobileGridRowsInput?.value ?? "");
  });
  createZoneBtn?.addEventListener("click", () => openCreateZoneDialog());
  mobileCreateZoneBtn?.addEventListener("click", () => {
    setMobileMapSheetOpen(null);
    openCreateZoneDialog();
  });
  getTopLevelShadeDisclosures().forEach((details) => {
    details.addEventListener("toggle", () => {
      if (details.open && isMobile()) {
        setMobileUtilityOpen(false);
        setMobileMapSheetOpen(null);
        getTopLevelShadeDisclosures().forEach((other) => {
          if (other !== details && other.open) {
            other.open = false;
          }
        });
      }
      syncMobileShadeDisclosureState();
    });
  });

  document.querySelectorAll<HTMLInputElement>(".global-search-input").forEach((input) => {
    input.addEventListener("input", () => handleGlobalSearch(state, renderPlots, input, () => setActiveTab("map")));
    input.addEventListener("keydown", (e) => handleSearchKeydown(state, e, renderPlots, input));
  });
  initThemeFeature();
  initSnapshotsFeature({
    canWrite: () => canWriteInGarden,
    ensureWriteAccess,
    showFetchError,
    confirmDialog,
    authorizeSensitiveAdminAction: confirmSensitiveAdminAction,
    fetchPlots,
    fetchLayoutState,
    setMobileMapSheetOpen,
  });
  getAuthButtons().forEach((btn) => {
    btn.addEventListener("click", () => {
      setMobileUtilityOpen(false);
      void handleAuthButton();
    });
  });
  getGardenSelects().forEach((select) => {
    select.addEventListener("change", () => {
      const next = Number.parseInt(select.value, 10);
      if (!Number.isFinite(next) || next <= 0) return;
      getGardenSelects().forEach((other) => {
        if (other !== select) other.value = select.value;
      });
      void switchGarden(next);
    });
  });

  const createGardenHandler = () => {
    setMobileUtilityOpen(false);
    if (!canCurrentUserCreateGarden()) {
      showToast(t("garden.editor_limit"), "error");
      return;
    }
    void (async () => {
      const name = await promptDialog(t("garden.name_prompt"));
      if (!name?.trim()) return;
      try {
        const garden = await createGardenApi(name.trim());
        setActiveGardenContext(garden.id);
        await refreshGardenContext();
        const needsOnboarding = await checkOnboardingNeeded();
        if (!needsOnboarding) refreshDataAfterAuthChange();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    })();
  };
  getGardenCreateButtons().forEach((btn) => {
    btn.addEventListener("click", createGardenHandler);
  });

  updateAuthButton();
  wireStatusBanner();

  const viewport = document.getElementById("map-viewport");
  const cameraEl = document.getElementById("map-camera");
  if (viewport && cameraEl) {
    cameraCtrl = initCamera(viewport, cameraEl, {
      onTransformChange: (s) => {
        _cameraState = s;
        dismissPopover();
        cameraCtrl?.updateMinimap(state.plots, mapInteraction.hiddenZones);
      },
    }, state.gridRows, state.gridCols);
    requestAnimationFrame(() => cameraCtrl?.fitAll());
  }

  initZoneToggles();
  initCategoryFilters();
  startClock();
  updateMobileHeader();

  document.addEventListener("click", (e) => {
    const target = e.target as Node;
    const insideSearch = Array.from(document.querySelectorAll(".global-search-shell"))
      .some((container) => container.contains(target));
    if (!insideSearch) {
      hideGlobalSearchDropdowns();
    }
  });

  const plantsSearch = queryInput("plants-search");
  const plantsCategory = querySelect("plants-category");
  const plantsPresence = querySelect("plants-presence-filter");
  const plantsSortField = querySelect("plants-sort-field");
  const plantsSortDir = queryButton("plants-sort-dir");
  const addPlantBtn = queryButton("add-plant-btn");
  const colToggleBtn = queryButton("col-toggle-btn");

  plantsSearch?.addEventListener("input", () => {
    clearFocusedPlantIds();
    renderPlantsTable();
  });
  plantsCategory?.addEventListener("change", () => {
    clearFocusedPlantIds();
    renderPlantsTable();
  });
  plantsPresence?.addEventListener("change", () => {
    clearFocusedPlantIds();
    renderPlantsTable();
  });
  plantsSortField?.addEventListener("change", () => {
    plantTableState.sortField = plantsSortField.value as SortField;
    localStorage.setItem("gardenops-sort", JSON.stringify({ field: plantTableState.sortField, dir: plantTableState.sortDir }));
    renderPlantsTable();
  });
  plantsSortDir?.addEventListener("click", () => {
    plantTableState.sortDir = plantTableState.sortDir === "asc" ? "desc" : "asc";
    localStorage.setItem("gardenops-sort", JSON.stringify({ field: plantTableState.sortField, dir: plantTableState.sortDir }));
    renderPlantsTable();
  });
  addPlantBtn?.addEventListener("click", () => openCreatePlantDialog());
  colToggleBtn?.addEventListener("click", toggleColumnDropdown);

  // Shared sub-mode toggle events
  document.querySelectorAll<HTMLButtonElement>("[data-sub-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset["subMode"];
      if (!mode || !isSubMode(mode)) return;
      navigateToSubMode(mode);
    });
  });
  document.querySelectorAll<HTMLButtonElement>("[data-batch]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset["batch"];
      if (action) void handleBatchAction(action);
    });
  });

  // Harvest event listeners (wired by initHarvestTab)
  initHarvestTab(appContext);

  // Mobile FAB + quick action sheet
  initQuickActionsFeature(appContext);

  document.addEventListener("click", (e) => {
    const colDd = document.getElementById("col-toggle-dropdown");
    const colBtn = document.getElementById("col-toggle-btn");
    if (colDd && !colDd.hidden && !colDd.contains(e.target as Node) && e.target !== colBtn) {
      colDd.hidden = true;
    }
  });

  ensureGatedFeatureInitializers();
  syncMobileCapabilities();
  updateMapDirectionControlVisibility();
  syncMobileShadeDisclosureState();
  syncMobileViewportOffset();
}

function updateMapDirectionControlVisibility(): void {
  const menu = document.getElementById("edit-menu-dropdown") as HTMLElement | null;
  const control = document.querySelector(".map-direction-control") as HTMLElement | null;
  const gridDimsControl = document.querySelector(".grid-dims-control") as HTMLElement | null;
  const editBtn = queryButton("edit-mode-btn");
  const visible = activeTab === "map" && state.editMode && !isMobile();
  if (menu) {
    menu.hidden = !visible;
  }
  if (control) {
    control.hidden = !visible;
  }
  if (gridDimsControl) {
    gridDimsControl.hidden = !visible;
  }
  if (editBtn) {
    editBtn.setAttribute("aria-expanded", visible ? "true" : "false");
  }
  syncGridDimensionInputs();
}

function syncShadePanelContext(): void {
  shadePanel?.setGardenContext({
    plots: state.plots,
    selectedPlotId: state.selectedPlotId,
    housePosition: state.housePosition,
    houseSize: state.houseSize,
    northDegrees: state.northDegrees,
  });
}

function samePlotIdSet(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const value of a) {
    if (!b.has(value)) return false;
  }
  return true;
}

function syncSubModeButtons(): void {
  document.querySelectorAll<HTMLButtonElement>("[data-sub-mode]").forEach((btn) => {
    btn.setAttribute("aria-selected", btn.dataset["subMode"] === subMode ? "true" : "false");
  });
}

function syncSubModeGroups(): void {
  document.querySelectorAll<HTMLElement>("[data-parent-tab-group]").forEach((group) => {
    group.hidden = group.dataset["parentTabGroup"] !== activeTab;
  });
}

function syncSubModePanels(): void {
  Object.entries(SUB_MODE_META).forEach(([mode, meta]) => {
    if (!meta.panelId) return;
    const panel = document.getElementById(meta.panelId);
    if (panel) panel.hidden = mode !== subMode;
  });
}

function syncSavedViewsAvailability(): void {
  const shell = document.querySelector<HTMLElement>(".saved-views-shell");
  if (!shell) return;
  shell.hidden = !(activeTab === "garden" || activeTab === "activity")
    || !isFeatureEnabled("saved_views")
    || !supportsSavedViews(subMode);
}

async function refreshActiveNavigationContent(): Promise<void> {
  if (activeTab === "garden" || activeTab === "activity") {
    await ensurePlantsLoaded();
    if (subMode === "journal") {
      await loadJournalEntries();
    } else if (subMode === "calendar") {
      await loadCalendar();
    } else if (subMode === "inventory") {
      await loadInventoryItems();
    } else if (subMode === "tasks") {
      await loadTasks();
    } else if (subMode === "issues") {
      await loadIssues();
    } else if (subMode === "harvest") {
      await loadHarvest();
    } else if (subMode === "procurement") {
      await loadProcurement();
    } else if (subMode === "indoor") {
      await loadIndoorPlants();
      const container = document.getElementById("indoor-tab-content");
      if (container) renderIndoorPlants(container);
    }
    return;
  }
  if (activeTab === "insights") {
    if (subMode === "care") {
      await loadCare();
      await loadWeather();
    } else if (subMode === "statistics") {
      await loadStatistics();
    }
    return;
  }
  if (activeTab === "admin") {
    await activateAdminPanel();
  }
}

function loadActiveNavigationContent(): void {
  void refreshActiveNavigationContent();
}

function applyNavigationState(opts: { triggerLoads?: boolean } = {}): void {
  const { triggerLoads = true } = opts;
  const normalized = normalizeNavigation(activeTab, subMode);
  activeTab = normalized.tab;
  subMode = normalized.subMode;
  persistNavigationState();

  document.querySelectorAll<HTMLButtonElement>(".top-tab").forEach((btn) => {
    const isActive = btn.dataset["tab"] === activeTab;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", isActive ? "true" : "false");
    btn.tabIndex = isActive ? 0 : -1;
  });
  document.querySelectorAll<HTMLButtonElement>(".mobile-tab-btn").forEach((btn) => {
    const isActive = btn.dataset["tab"] === activeTab;
    btn.classList.toggle("active", isActive);
    if (isActive) {
      btn.setAttribute("aria-current", "page");
    } else {
      btn.removeAttribute("aria-current");
    }
  });
  const mobileAdminBtn = queryButton("mobile-admin-btn");
  if (mobileAdminBtn) {
    const isActive = activeTab === "admin";
    mobileAdminBtn.classList.toggle("active", isActive);
    mobileAdminBtn.setAttribute("aria-pressed", isActive ? "true" : "false");
  }

  syncSubModeButtons();
  syncSubModeGroups();
  syncSubModePanels();
  syncSavedViewsAvailability();
  renderDataExportBars();

  const mapView = document.getElementById("map-view");
  const plantsView = document.getElementById("plants-view");
  const careView = document.getElementById("care-view");
  const analysisView = document.getElementById("analysis-view");
  const statsView = document.getElementById("statistics-view");
  const adminView = document.getElementById("admin-view");

  const showSharedDataView = activeTab === "garden" || activeTab === "activity";
  const showCareView = activeTab === "insights" && subMode === "care";
  const showAnalysisView = activeTab === "insights" && subMode === "analysis";
  const showStatsView = activeTab === "insights" && subMode === "statistics";

  mapView?.classList.toggle("active", activeTab === "map");
  plantsView?.classList.toggle("active", showSharedDataView);
  careView?.classList.toggle("active", showCareView);
  analysisView?.classList.toggle("active", showAnalysisView);
  statsView?.classList.toggle("active", showStatsView);
  adminView?.classList.toggle("active", activeTab === "admin");

  if (mapView) mapView.hidden = activeTab !== "map";
  if (plantsView) plantsView.hidden = !showSharedDataView;
  if (careView) careView.hidden = !showCareView;
  if (analysisView) analysisView.hidden = !showAnalysisView;
  if (statsView) statsView.hidden = !showStatsView;
  if (adminView) adminView.hidden = activeTab !== "admin";

  updateMapDirectionControlVisibility();
  if (activeTab === "map") {
    void ensureShadeMapPanelLoaded();
    void loadPlotAlerts();
  }
  updateMobileHeader();
  if (activeTab !== "map" || !isMobile()) {
    setMobileMapSheetOpen(null);
    closeMobileShadeDisclosures();
  }
  if (isMobile()) {
    setMobileUtilityOpen(false);
    syncMobileShadeDisclosureState();
  }
  if (triggerLoads) {
    loadActiveNavigationContent();
  }
}

function setActiveTab(tab: AppTab): void {
  activeTab = tab;
  if (isPrimaryContentTab(tab) && parentTabForSubMode(subMode) !== tab) {
    subMode = defaultSubModeForTab(tab);
  }
  applyNavigationState();
}

function setSubMode(
  mode: SubMode,
  opts: { triggerLoads?: boolean } = {},
): void {
  activeTab = parentTabForSubMode(mode);
  subMode = mode;
  applyNavigationState(opts);
  const scrollContainer = activeTab === "insights"
    ? document.getElementById(SUB_MODE_META[subMode].rootViewId ?? "")
    : document.getElementById("plants-view");
  if (scrollContainer instanceof HTMLElement) {
    scrollContainer.scrollTop = 0;
  }
}

function navigateToSubMode(
  mode: SubMode,
  opts: { triggerLoads?: boolean } = {},
): void {
  setSubMode(mode, opts);
}

// ── Data fetching ──────────────────────────────────────────
function isCurrentGardenRequest(gardenId: number | null): boolean {
  return getActiveGardenContext() === gardenId;
}

async function fetchPlots(retries = 2): Promise<void> {
  const requestGardenId = getActiveGardenContext();
  const grid = document.getElementById("map-grid");
  if (grid && state.plots.length === 0 && retries === 2) {
    const loader = document.createElement("div");
    loader.className = "map-grid-loading";
    grid.replaceChildren(loader);
  }
  try {
    const plots = await getPlots();
    if (!isCurrentGardenRequest(requestGardenId)) return;
    state.plots = plots;
    refreshZoneToggles();
    const indoorPlot = state.plots.find(p => p.zone_code === "I");
    if (indoorPlot) {
      setIndoorPlotId(indoorPlot.plot_id);
      setOnEditPlant((plant) => openEditPlantDialog(plant));
      setOnAddPlant((container) => {
        const params = buildPlantSearchParams(indoorPlot.plot_id);
        // Include the INDOOR plot in the available plots list
        const origGetPlots = params.getPlotOptions;
        params.getPlotOptions = () => {
          const plots = origGetPlots();
          plots.unshift({
            plot_id: indoorPlot.plot_id,
            zone_code: "I",
            grid_row: 0,
            grid_col: 0,
            color: null,
          });
          return plots;
        };
        // Wrap onCreateSubmit to also refresh indoor list
        const origOnCreate = params.onCreateSubmit;
        params.onCreateSubmit = async (data, plotIds) => {
          // Ensure INDOOR plot is always included
          if (!plotIds.includes(indoorPlot.plot_id)) {
            plotIds.push(indoorPlot.plot_id);
          }
          await origOnCreate(data, plotIds);
          await loadIndoorPlants();
          renderIndoorPlants(container);
        };
        // Wrap onPlantAssigned to also refresh indoor list
        const origOnAssigned = params.onPlantAssigned;
        params.onPlantAssigned = () => {
          origOnAssigned();
          void loadIndoorPlants().then(() => renderIndoorPlants(container));
        };
        showPlantSearchDialog(params);
      });
    }
    clearAppStatus();
  } catch (err) {
    if (!isCurrentGardenRequest(requestGardenId)) return;
    if (retries > 0) {
      await new Promise((r) => setTimeout(r, 500));
      if (!isCurrentGardenRequest(requestGardenId)) return;
      return fetchPlots(retries - 1);
    }
    showFetchError(err);
  }
  renderPlots();
  initZoneToggles();
  if ((activeTab === "garden" || activeTab === "activity") && plantsCacheLoaded) {
    renderPlantsTable();
  }
}

function applyHouseState(house: HouseLayoutState): void {
  state.gridRows = house.grid_rows ?? GRID_ROWS;
  state.gridCols = house.grid_cols ?? GRID_COLS;
  state.housePosition = {
    row: house.row,
    col: house.col,
  };
  state.northDegrees = normalizeDegrees(house.north_degrees);
  state.houseSize = clampHouseSize(
    state,
    house.width,
    house.height,
  );
  cameraCtrl?.setGridDims(state.gridRows, state.gridCols);
  syncDirectionControls();
  syncGridDimensionInputs();
  renderDirectionLabels();
}

async function fetchLayoutState(): Promise<void> {
  const requestGardenId = getActiveGardenContext();
  try {
    const house = await getLayoutStateApi();
    if (!isCurrentGardenRequest(requestGardenId)) return;
    applyHouseState(house);
    renderPlots();
  } catch (err) {
    if (!isCurrentGardenRequest(requestGardenId)) return;
    showFetchError(err);
  }
}

function scheduleShadeMapPersist(nextState: PersistedShadeMapState): void {
  if (shadeMapPersistTimer != null) {
    window.clearTimeout(shadeMapPersistTimer);
  }
  shadeMapPersistTimer = window.setTimeout(() => {
    shadeMapPersistTimer = null;
    void updateShadeMapStateApi(nextState).catch(showFetchError);
  }, 120);
}

function resetShadeMapPanelLoadState(): void {
  shadeMapPanelLoadedGardenId = null;
  shadeMapPanelLoadingGardenId = null;
  shadeMapPanelLoadPromise = null;
  shadeMapPanelLoadEpoch += 1;
}

async function ensureShadeMapPanelLoaded(): Promise<void> {
  const gardenId = getActiveGardenContext();
  if (!(authProfile?.shademap_available ?? false) || gardenId === null) {
    return;
  }
  const panel = await ensureShadePanelController();
  if (shadeMapPanelLoadedGardenId === gardenId) {
    if (activeTab === "map") {
      panel.activate();
    }
    return;
  }
  if (shadeMapPanelLoadingGardenId === gardenId && shadeMapPanelLoadPromise) {
    await shadeMapPanelLoadPromise;
    if (shadeMapPanelLoadedGardenId === gardenId && activeTab === "map") {
      panel.activate();
    }
    return;
  }

  const loadEpoch = shadeMapPanelLoadEpoch;
  shadeMapPanelLoadingGardenId = gardenId;
  const loadPromise = (async () => {
    try {
      const [config, persistedState, calibration, obstacles] = await Promise.all([
        getShadeMapConfigApi(),
        getShadeMapStateApi(),
        getShadeMapCalibrationApi(),
        listShadeMapObstaclesApi(),
      ]);
      if (
        loadEpoch !== shadeMapPanelLoadEpoch
        || getActiveGardenContext() !== gardenId
        || !(authProfile?.shademap_available ?? false)
      ) {
        return;
      }
      await panel.load(config, persistedState, calibration, obstacles);
      if (
        loadEpoch !== shadeMapPanelLoadEpoch
        || getActiveGardenContext() !== gardenId
        || !(authProfile?.shademap_available ?? false)
      ) {
        return;
      }
      shadeMapPanelLoadedGardenId = gardenId;
      if (activeTab === "map") {
        panel.activate();
      }
    } catch (err) {
      if (
        loadEpoch !== shadeMapPanelLoadEpoch
        || getActiveGardenContext() !== gardenId
        || !(authProfile?.shademap_available ?? false)
      ) {
        return;
      }
      panel.showError(getApiErrorMessage(err));
      if (isAuthApiError(err)) {
        showFetchError(err);
      }
    } finally {
      if (
        loadEpoch === shadeMapPanelLoadEpoch
        && shadeMapPanelLoadingGardenId === gardenId
      ) {
        shadeMapPanelLoadingGardenId = null;
        shadeMapPanelLoadPromise = null;
      }
    }
  })();
  shadeMapPanelLoadPromise = loadPromise;
  await loadPromise;
}

async function persistHouseGeometry(): Promise<void> {
  await updateLayoutStateApi({
    row: state.housePosition.row,
    col: state.housePosition.col,
    width: state.houseSize.width,
    height: state.houseSize.height,
    north_degrees: state.northDegrees,
    grid_rows: state.gridRows,
    grid_cols: state.gridCols,
  });
}

function normalizeDegrees(value: number): number {
  const wrapped = value % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

function syncDirectionControls(): void {
  const normalized = String(normalizeDegrees(state.northDegrees));
  [
    document.getElementById("map-direction-input"),
    document.getElementById("mobile-map-direction-input"),
  ].forEach((element) => {
    if (element instanceof HTMLInputElement) {
      element.value = normalized;
    }
  });
  const slider = queryInput("map-direction-slider");
  if (slider) slider.value = normalized;
}

function syncGridDimensionInputs(): void {
  const nextCols = String(state.gridCols);
  const nextRows = String(state.gridRows);
  [
    document.getElementById("grid-cols-input"),
    document.getElementById("mobile-grid-cols-input"),
  ].forEach((element) => {
    if (element instanceof HTMLInputElement) {
      element.value = nextCols;
    }
  });
  [
    document.getElementById("grid-rows-input"),
    document.getElementById("mobile-grid-rows-input"),
  ].forEach((element) => {
    if (element instanceof HTMLInputElement) {
      element.value = nextRows;
    }
  });
}

async function applyGridDimensions(nextColsRaw: string, nextRowsRaw: string): Promise<void> {
  const newCols = Number.parseInt(nextColsRaw, 10);
  const newRows = Number.parseInt(nextRowsRaw, 10);
  if (!Number.isFinite(newCols) || !Number.isFinite(newRows) || newCols < 5 || newCols > 100 || newRows < 5 || newRows > 100) {
    showToast(t("map.grid_dimensions_invalid"), "error");
    return;
  }
  try {
    const updated = await updateLayoutStateApi({
      row: state.housePosition.row,
      col: state.housePosition.col,
      width: state.houseSize.width,
      height: state.houseSize.height,
      north_degrees: state.northDegrees,
      grid_rows: newRows,
      grid_cols: newCols,
    });
    applyHouseState(updated);
  } catch (err) {
    syncGridDimensionInputs();
    showFetchError(err);
    return;
  }
  renderPlots();
  showToast(t("map.property_resized", { cols: newCols, rows: newRows }));
}

function scheduleLayoutPersist(): void {
  if (layoutPersistTimer != null) {
    window.clearTimeout(layoutPersistTimer);
  }
  layoutPersistTimer = window.setTimeout(() => {
    layoutPersistTimer = null;
    void persistHouseGeometry().catch(showFetchError);
  }, 180);
}

function cardinalLabel(degrees: number): string {
  const labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return labels[Math.round(normalizeDegrees(degrees) / 45) % labels.length] ?? "N";
}

function formatEdgeLabel(degrees: number): string {
  const normalized = normalizeDegrees(degrees);
  return `${cardinalLabel(normalized)} ${normalized}°`;
}

function renderDirectionLabels(): void {
  const north = normalizeDegrees(state.northDegrees);
  const top = document.getElementById("map-edge-top");
  const right = document.getElementById("map-edge-right");
  const bottom = document.getElementById("map-edge-bottom");
  const left = document.getElementById("map-edge-left");
  if (!top || !right || !bottom || !left) return;
  top.textContent = formatEdgeLabel(north);
  right.textContent = formatEdgeLabel(north + 90);
  bottom.textContent = formatEdgeLabel(north + 180);
  left.textContent = formatEdgeLabel(north + 270);
}

let plantsCacheLoaded = false;
let plantsTableHeadSignature = "";

type PlantPresenceFilter = "all" | "current" | "gone" | "unobserved";

interface PlantsViewState {
  filtered: Plant[];
  sorted: Plant[];
  knownPlotIds: Set<string>;
}

function replaceCachedPlant(nextPlant: Plant): void {
  const index = state.plantsCache.findIndex((plant) => plant.plt_id === nextPlant.plt_id);
  if (index === -1) {
    state.plantsCache.push(nextPlant);
    return;
  }
  state.plantsCache[index] = nextPlant;
}

function removeCachedPlant(pltId: string): void {
  const index = state.plantsCache.findIndex((plant) => plant.plt_id === pltId);
  if (index >= 0) state.plantsCache.splice(index, 1);
}

function invalidatePlantsCache(): void {
  state.plantsCache = [];
  plantsCacheLoaded = false;
  plantMediaPreviewById.clear();
}

async function ensurePlantsLoaded(): Promise<void> {
  const requestGardenId = getActiveGardenContext();
  if (!plantsCacheLoaded) {
    try {
      const plants = await getPlants();
      if (!isCurrentGardenRequest(requestGardenId)) return;
      state.plantsCache = plants;
      plantsCacheLoaded = true;
      clearAppStatus();
    } catch (err) {
      if (!isCurrentGardenRequest(requestGardenId)) return;
      showFetchError(err);
    }
  }
  if (!isCurrentGardenRequest(requestGardenId)) return;
  renderPlantsTable();
}

async function fetchPlantDetails(pltId: string): Promise<Plant | null> {
  try {
    return await getPlantApi(pltId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

function rerenderPlantDependentViews(): void {
  if (activeTab === "garden" || activeTab === "activity") {
    renderPlantsTable();
  }
  if (activeTab === "insights" && subMode === "care") {
    void loadCare();
    void loadWeather();
  }
}

async function refreshPlantsById(
  pltIds: string[],
): Promise<Map<string, Plant | null>> {
  const uniqueIds = Array.from(new Set(pltIds.map((pltId) => pltId.trim()).filter(Boolean)));
  if (uniqueIds.length === 0) return new Map();

  const refreshedEntries = await Promise.all(
    uniqueIds.map(async (pltId) => [pltId, await fetchPlantDetails(pltId)] as const),
  );

  if (plantsCacheLoaded) {
    for (const [pltId, plant] of refreshedEntries) {
      if (plant) {
        replaceCachedPlant(plant);
      } else {
        removeCachedPlant(pltId);
      }
    }
    rerenderPlantDependentViews();
  }

  return new Map(refreshedEntries);
}

function getPlantsViewState(): PlantsViewState {
  const query = (queryInput("plants-search")?.value || "").trim();
  const category = querySelect("plants-category")?.value || "";
  const presence = (querySelect("plants-presence-filter")?.value || "all") as PlantPresenceFilter;
  const filtered = applyFocusedPlantFilter(filterPlants(state.plantsCache, query, category, presence));
  return {
    filtered,
    sorted: sortPlants(filtered, plantTableState.sortField, plantTableState.sortDir),
    knownPlotIds: new Set(state.plots.map((plot) => plot.plot_id)),
  };
}

function syncPlantsHeaderSelection(visiblePlants: Plant[]): void {
  const selectAllCheckbox = document.querySelector<HTMLInputElement>("#plants-table-head .col-select input[type='checkbox']");
  if (!selectAllCheckbox) return;
  if (visiblePlants.length === 0) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    return;
  }
  const selectedCount = visiblePlants.filter((plant) => selectedPlantIds.has(plant.plt_id)).length;
  selectAllCheckbox.checked = selectedCount > 0 && selectedCount === visiblePlants.length;
  selectAllCheckbox.indeterminate = selectedCount > 0 && selectedCount < visiblePlants.length;
}

function syncRenderedPlantSelection(visiblePlants: Plant[] | null = null): void {
  const tbody = document.getElementById("plants-table-body");
  const mobileList = document.getElementById("plants-mobile-list");
  if (tbody && mobileList) {
    syncPlantsSelectionState(tbody, mobileList, selectedPlantIds);
  }
  syncPlantsHeaderSelection(visiblePlants ?? getPlantsViewState().sorted);
}

// ── Plants table ───────────────────────────────────────────
function renderPlantsTable(): void {
  const thead = document.getElementById("plants-table-head");
  const tbody = document.getElementById("plants-table-body");
  const mobileList = document.getElementById("plants-mobile-list");
  const summary = document.getElementById("plants-summary");
  if (!tbody || !mobileList) return;
  const view = getPlantsViewState();

  if (thead) {
    const nextHeadSignature = JSON.stringify({
      columns: plantTableState.columns.map((column) => [column.key, column.label]),
      visibleColumns: [...plantTableState.visibleColumns],
    });
    if (plantsTableHeadSignature !== nextHeadSignature) {
      renderPlantsTableHead(
        thead,
        plantTableState.columns,
        plantTableState.visibleColumns,
        () => toggleSelectAllPlants(),
      );
      thead.querySelectorAll("th.sortable").forEach((th) => {
        th.addEventListener("click", handleSortClick);
      });
      plantsTableHeadSignature = nextHeadSignature;
    }
    syncPlantsHeaderSelection(view.sorted);
  }

  if (summary) {
    const uniquePlants = state.plantsCache.filter((p) => (p.quantity ?? 0) > 0).length;
    const totalBulbsPlanted = state.plantsCache
      .filter((p) => p.category.trim().toLowerCase() === "løk")
      .reduce((sum, p) => sum + (p.quantity ?? 0), 0);
    summary.textContent = t("plants.summary", { unique: uniquePlants, total: totalBulbsPlanted });
  }

  const tableCbs = {
    knownPlotIds: view.knownPlotIds,
    plotAssignmentMeanings: authProfile?.plot_assignment_meanings ?? [],
    mediaPreviewByPlantId: plantMediaPreviewById,
    onOpenPlot: (plotId: string) => {
      setActiveTab("map");
      void selectPlot(state, plotId, plotCbs);
    },
    onEdit: (plant: Plant) => openEditPlantDialog(plant),
    onToggleSelect: (pltId: string) => togglePlantSelection(pltId),
    selectedIds: selectedPlantIds,
  };
  renderPlantsTableBody(tbody, view.sorted, plantTableState.columns, plantTableState.visibleColumns, tableCbs);
  renderPlantsMobileCards(mobileList, view.sorted, tableCbs);
  renderDataExportBars();
  void ensurePlantMediaPreviews(view.sorted.map((plant) => plant.plt_id));

  updateSortIndicators();
}

function updateSortIndicators(): void {
  const thead = document.getElementById("plants-table-head");
  if (!thead) return;
  thead.querySelectorAll<HTMLTableCellElement>("th.sortable").forEach((th) => {
    th.classList.remove("sort-asc", "sort-desc");
    if (th.dataset["sort"] === plantTableState.sortField) {
      th.classList.add(plantTableState.sortDir === "asc" ? "sort-asc" : "sort-desc");
    }
  });
  const fieldSelect = querySelect("plants-sort-field");
  const dirBtn = queryButton("plants-sort-dir");
  if (fieldSelect) fieldSelect.value = plantTableState.sortField;
  if (dirBtn) {
    dirBtn.textContent = plantTableState.sortDir === "asc" ? t("common.asc") : t("common.desc");
    dirBtn.setAttribute("aria-label", t("plants.sort_toggle_current", {
      direction: plantTableState.sortDir === "asc" ? t("common.asc").toLowerCase() : t("common.desc").toLowerCase(),
    }));
  }
}

// ── Batch selection ────────────────────────────────────────

function togglePlantSelection(pltId: string): void {
  if (selectedPlantIds.has(pltId)) {
    selectedPlantIds.delete(pltId);
  } else {
    selectedPlantIds.add(pltId);
  }
  updateBatchBar();
  syncRenderedPlantSelection();
}

function toggleSelectAllPlants(): void {
  const { filtered } = getPlantsViewState();
  const allSelected = filtered.every((plant) => selectedPlantIds.has(plant.plt_id));
  if (allSelected) {
    selectedPlantIds.clear();
  } else {
    for (const plant of filtered) selectedPlantIds.add(plant.plt_id);
  }
  updateBatchBar();
  syncRenderedPlantSelection(filtered);
}

function clearPlantSelection(): void {
  selectedPlantIds.clear();
  updateBatchBar();
  syncRenderedPlantSelection();
}

function updateBatchBar(): void {
  const bar = document.getElementById("batch-bar");
  const count = document.getElementById("batch-count");
  if (!bar) return;
  bar.hidden = selectedPlantIds.size === 0;
  if (count) {
    count.textContent = t("plants.batch_selected", { count: selectedPlantIds.size });
  }
}

async function handleBatchAction(action: string): Promise<void> {
  if (!ensureWriteAccess()) return;
  const ids = [...selectedPlantIds];
  if (ids.length === 0) return;
  const mutatesPlots = action === "assign-plots" || action === "remove-plots";

  if (action === "clear") {
    clearPlantSelection();
    return;
  }

  if (action === "journal") {
    void openBatchJournalComposer(ids, clearPlantSelection);
    return;
  }

  if (action === "year-planted") {
    const year = prompt(t("plants.batch_prompt_year"));
    if (year === null) return;
    await batchUpdatePlantsApi(ids, { year_planted: year || null });
    showToast(t("plants.batch_updated", { count: ids.length }));
  } else if (action === "category") {
    const cat = prompt(t("plants.batch_prompt_category"));
    if (cat === null) return;
    await batchUpdatePlantsApi(ids, { category: cat });
    showToast(t("plants.batch_updated", { count: ids.length }));
  } else if (action === "deer-resistant") {
    const val = confirm(t("plants.batch_confirm_deer_resistant"));
    await batchUpdatePlantsApi(ids, { deer_resistant: val });
    showToast(t("plants.batch_updated", { count: ids.length }));
  } else if (action === "assign-plots" || action === "remove-plots") {
    const raw = prompt(
      action === "assign-plots"
        ? t("plants.batch_prompt_assign_plots")
        : t("plants.batch_prompt_remove_plots"),
    );
    if (raw === null) return;
    const plotIds = parsePlotIdInput(raw);
    if (plotIds.length === 0) return;
    await batchUpdatePlantsApi(
      ids,
      {},
      {
        plot_ids: plotIds,
        action: action === "assign-plots" ? "assign" : "remove",
      },
    );
    showToast(
      action === "assign-plots"
        ? t("plants.batch_assigned_plots", { count: ids.length })
        : t("plants.batch_removed_plots", { count: ids.length }),
    );
  } else if (action === "care-note") {
    const note = prompt(t("plants.batch_prompt_care_note"))?.trim() ?? "";
    if (!note) return;
    await batchUpdatePlantsApi(ids, {}, undefined, { care_note_append: note });
    showToast(t("plants.batch_appended_care_note", { count: ids.length }));
  }

  invalidatePlantsCache();
  await ensurePlantsLoaded();
  if (mutatesPlots) {
    await fetchPlots();
  }
  clearPlantSelection();
}

function extractPendingMediaFiles(payload: Record<string, unknown>): File[] {
  const raw = payload["media_files"];
  if (!Array.isArray(raw)) return [];
  return raw.filter((file): file is File => file instanceof File);
}

function withoutPendingMediaFiles(payload: Record<string, unknown>): Record<string, unknown> {
  const next = { ...payload };
  delete next["media_files"];
  return next;
}

async function uploadTargetMediaFiles(
  targetType: "issue" | "harvest_entry" | "plant" | "plot",
  targetId: string | number,
  files: File[],
  options: {
    setUploadProgress?: (pct: number | null) => void;
    gardenId?: number | null;
  } = {},
): Promise<void> {
  if (files.length === 0) return;
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]!;
    const uploadOptions: Parameters<typeof uploadMediaApi>[0] = {
      targetType,
      targetId,
      file,
      onProgress: (pct) => {
        if (!options.setUploadProgress) return;
        const overall = Math.round(((i + (pct / 100)) / files.length) * 100);
        options.setUploadProgress(overall);
      },
    };
    if (options.gardenId !== undefined) {
      uploadOptions.gardenId = options.gardenId;
    }
    await uploadMediaApi(uploadOptions);
  }
  options.setUploadProgress?.(null);
}

function attachReadonlyMediaSection(
  dialog: HTMLElement,
  options: {
    targetType: "issue" | "harvest_entry";
    targetId: string | number;
    emptyText: string;
  },
): void {
  const section = document.createElement("section");
  section.className = "journal-existing-media";
  const heading = document.createElement("h4");
  heading.className = "journal-existing-media-heading";
  heading.textContent = t("media.attached_photos");
  const container = document.createElement("div");
  section.append(heading, container);
  dialog.appendChild(section);

  const renderExisting = async (): Promise<void> => {
    const result = await listMediaApi({
      target_type: options.targetType,
      target_id: String(options.targetId),
    });
    await renderMediaGalleryLazy(container, {
      assets: result.items,
      emptyText: options.emptyText,
      canUpload: false,
      onDeleteAsset: async (asset) => {
        const ok = await confirmDialog(
          t("media.remove_confirm", {
            name: asset.original_filename || t("media.untitled"),
          }),
          t("media.remove_pending"),
        );
        if (!ok) return;
        await removeMediaLinkApi({
          assetId: asset.asset_id,
          targetType: options.targetType,
          targetId: options.targetId,
        });
        showToast(t("media.removed"), "success");
        await renderExisting();
      },
      onDeleteEverywhereAsset: async (asset) => {
        const ok = await confirmDialog(
          t("media.delete_everywhere_confirm", {
            name: asset.original_filename || t("media.untitled"),
          }),
          t("media.delete_everywhere"),
        );
        if (!ok) return;
        await deleteMediaAssetApi(asset.asset_id);
        showToast(t("media.deleted_everywhere"), "success");
        await renderExisting();
      },
      deleteEverywhereLabel: t("media.delete_everywhere"),
    });
  };

  void renderExisting().catch(() => {
    void renderMediaGalleryLazy(container, {
      assets: [],
      emptyText: options.emptyText,
      canUpload: false,
    });
  });
}

async function ensurePlantMediaPreviews(pltIds: string[]): Promise<void> {
  const requestedIds = Array.from(new Set(pltIds.map((pltId) => pltId.trim()).filter(Boolean)));
  const missingIds = requestedIds.filter((pltId) => !plantMediaPreviewById.has(pltId));
  if (missingIds.length === 0) return;
  const seq = ++plantMediaPreviewSeq;
  try {
    const items: Array<{ target_id: string; asset: MediaAsset }> = [];
    for (let index = 0; index < missingIds.length; index += MEDIA_SUMMARY_BATCH_SIZE) {
      const result = await listMediaSummariesApi({
        targetType: "plant",
        targetIds: missingIds.slice(index, index + MEDIA_SUMMARY_BATCH_SIZE),
      });
      if (seq !== plantMediaPreviewSeq) return;
      items.push(...result.items);
    }
    if (seq !== plantMediaPreviewSeq) return;
    const found = new Map(items.map((item) => [item.target_id, item.asset]));
    for (const pltId of missingIds) {
      plantMediaPreviewById.set(pltId, found.get(pltId) ?? null);
    }
    if (activeTab === "garden" && subMode === "plants") {
      renderPlantsTable();
    }
  } catch {
    // Ignore preview-summary failures; the surrounding list remains usable.
  }
}

async function refreshPlantMediaPreviews(pltIds: string[]): Promise<void> {
  const requestedIds = Array.from(new Set(pltIds.map((pltId) => pltId.trim()).filter(Boolean)));
  if (requestedIds.length === 0) return;
  for (const pltId of requestedIds) {
    plantMediaPreviewById.delete(pltId);
  }
  await ensurePlantMediaPreviews(requestedIds);
}

function downloadJsonFile(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 100);
}

function renderDataExportBars(): void {
  const openPrintable = (resource: string, params?: Record<string, string>) =>
    () => window.open(
      getExportUrl(resource as Parameters<typeof getExportUrl>[0], "html", params),
      "_blank",
      "noopener,noreferrer",
    );
  const exportParams = (params: Record<string, string>): Record<string, string> | undefined =>
    Object.keys(params).length > 0 ? params : undefined;
  const plantsBar = document.getElementById("plants-export-bar");
  if (plantsBar) {
    const params: Record<string, string> = {};
    const search = queryInput("plants-search")?.value.trim() || "";
    const category = querySelect("plants-category")?.value || "";
    const presence = querySelect("plants-presence-filter")?.value || "all";
    if (search) params["q"] = search;
    if (category) params["category"] = category;
    if (presence && presence !== "all") params["presence"] = presence;
    if (focusedPlantIds && focusedPlantIds.size > 0) {
      params["plt_ids"] = Array.from(focusedPlantIds).join(",");
    }
    renderExportBar(
      plantsBar,
      "plants",
      { onPrint: openPrintable("plants", exportParams(params)) },
      exportParams(params),
    );
  }

  const journalBar = document.getElementById("journal-export-bar");
  if (journalBar) {
    const params: Record<string, string> = {};
    const eventType = querySelect("journal-filter-type")?.value || "";
    const search = queryInput("journal-filter-search")?.value.trim() || "";
    const actor = queryInput("journal-filter-actor")?.value.trim() || "";
    const dateFrom = queryInput("journal-filter-from")?.value || "";
    const dateTo = queryInput("journal-filter-to")?.value || "";
    if (eventType) params["event_type"] = eventType;
    if (search) params["q"] = search;
    if (actor) params["actor"] = actor;
    if (dateFrom) params["date_from"] = dateFrom;
    if (dateTo) params["date_to"] = dateTo;
    renderExportBar(journalBar, "journal", { onPrint: () => window.print() }, exportParams(params));
  }

  const inventoryBar = document.getElementById("inventory-export-bar");
  if (inventoryBar) {
    const inventoryType = querySelect("inventory-type-filter")?.value || "";
    const search = queryInput("inventory-search")?.value.trim() || "";
    const params: Record<string, string> = {};
    if (inventoryType) params["inventory_type"] = inventoryType;
    if (search) params["q"] = search;
    renderExportBar(
      inventoryBar,
      "inventory",
      { onPrint: () => window.print() },
      exportParams(params),
    );
  }

  const tasksBar = document.getElementById("tasks-export-bar");
  if (tasksBar) {
    const status = querySelect("tasks-filter-status")?.value || "";
    const taskType = querySelect("tasks-filter-type")?.value || "";
    const taskParams: Record<string, string> = {};
    if (status) taskParams["status"] = status;
    if (taskType) taskParams["task_type"] = taskType;
    renderExportBar(
      tasksBar,
      "tasks",
      { onPrint: openPrintable("tasks", exportParams(taskParams)) },
      exportParams(taskParams),
    );
  }

  const issuesBar = document.getElementById("issues-export-bar");
  if (issuesBar) {
    const status = querySelect("issues-filter-status")?.value || "";
    const issueType = querySelect("issues-filter-type")?.value || "";
    const severity = querySelect("issues-filter-severity")?.value || "";
    const params: Record<string, string> = {};
    if (status) params["status"] = status;
    if (issueType) params["issue_type"] = issueType;
    if (severity) params["severity"] = severity;
    renderExportBar(
      issuesBar,
      "issues",
      { onPrint: openPrintable("issues", exportParams(params)) },
      exportParams(params),
    );
  }

  const harvestBar = document.getElementById("harvest-export-bar");
  if (harvestBar) {
    const params: Record<string, string> = {};
    const quality = querySelect("harvest-filter-quality")?.value || "";
    const dateFrom = queryInput("harvest-filter-from")?.value || "";
    const dateTo = queryInput("harvest-filter-to")?.value || "";
    if (quality) params["quality"] = quality;
    if (dateFrom) params["date_from"] = dateFrom;
    if (dateTo) params["date_to"] = dateTo;
    renderExportBar(
      harvestBar,
      "harvest",
      { onPrint: openPrintable("harvest", exportParams(params)) },
      exportParams(params),
    );
  }

  const procurementBar = document.getElementById("procurement-export-bar");
  if (procurementBar) {
    const status = querySelect("procurement-filter-status")?.value || "";
    const inventoryType = querySelect("procurement-filter-type")?.value || "";
    const params: Record<string, string> = {};
    if (status) params["status"] = status;
    if (inventoryType) params["inventory_type"] = inventoryType;
    renderExportBar(
      procurementBar,
      "procurement",
      { onPrint: () => window.print() },
      exportParams(params),
    );
  }

  const statisticsBar = document.getElementById("statistics-export-bar");
  if (statisticsBar) {
    const params: Record<string, string> = {};
    const zoneCode = statisticsTabModule?.getGardenerReportsZoneCode?.() || "";
    if (zoneCode) params["zone_code"] = zoneCode;
    statisticsBar.replaceChildren();
    const bar = document.createElement("div");
    bar.className = "export-bar";

    const summaryBtn = document.createElement("button");
    summaryBtn.type = "button";
    summaryBtn.className = "btn btn-sm btn-secondary";
    summaryBtn.textContent = t("exports.download_summary");
    summaryBtn.addEventListener("click", () => {
      void (async () => {
        try {
          const summary = await fetchSeasonalSummary(
            exportParams(params),
          );
          downloadJsonFile("gardenops-seasonal-summary.json", summary);
        } catch (err) {
          showToast(getApiErrorMessage(err), "error");
        }
      })();
    });

    const printBtn = document.createElement("button");
    printBtn.type = "button";
    printBtn.className = "btn btn-sm btn-secondary";
    printBtn.textContent = t("exports.print");
    printBtn.addEventListener("click", () => {
      const url = getExportUrl(
        "seasonal-summary",
        "html",
        exportParams(params),
      );
      window.open(url, "_blank", "noopener,noreferrer");
    });

    bar.append(summaryBtn, printBtn);
    statisticsBar.appendChild(bar);
  }
}

function toggleColumnDropdown(): void {
  const dropdown = document.getElementById("col-toggle-dropdown");
  if (!dropdown) return;

  if (!dropdown.hidden) {
    dropdown.hidden = true;
    return;
  }

  buildColumnDropdown(dropdown);
  dropdown.hidden = false;
}

function buildColumnDropdown(dropdown: HTMLElement): void {
  let dragIdx: number | null = null;
  const items = plantTableState.columns.map((col, i) => {
    const label = document.createElement("label");
    label.className = "col-toggle-item";
    label.dataset["colIdx"] = String(i);
    if (col.key !== "name") {
      label.draggable = true;
    }

    const handle = document.createElement("span");
    handle.className = "drag-handle";
    handle.textContent = "\u2261";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = col.key;
    checkbox.checked = plantTableState.visibleColumns.has(col.key);
    checkbox.disabled = col.key === "name";

    label.append(handle, checkbox, document.createTextNode(` ${col.label}`));
    return label;
  });
  dropdown.replaceChildren(...items);

  dropdown.querySelectorAll<HTMLInputElement>(
    "input[type='checkbox']",
  ).forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) {
        plantTableState.visibleColumns.add(cb.value);
      } else {
        plantTableState.visibleColumns.delete(cb.value);
      }
      saveVisibleColumns();
      renderPlantsTable();
    });
  });

  dropdown.querySelectorAll<HTMLElement>(
    ".col-toggle-item[draggable]",
  ).forEach((item) => {
    item.addEventListener("dragstart", (e) => {
      dragIdx = Number(item.dataset["colIdx"]);
      (e as DragEvent).dataTransfer?.setData("text/plain", "");
      item.classList.add("dragging");
    });
    item.addEventListener("dragend", () => {
      dragIdx = null;
      item.classList.remove("dragging");
    });
    item.addEventListener("dragover", (e) => {
      e.preventDefault();
    });
    item.addEventListener("drop", (e) => {
      e.preventDefault();
      const targetIdx = Number(item.dataset["colIdx"]);
      if (dragIdx == null || dragIdx === targetIdx) return;
      const moved = plantTableState.columns.splice(dragIdx, 1)[0];
      if (moved) {
        plantTableState.columns.splice(targetIdx, 0, moved);
        saveColumnOrder();
        renderPlantsTable();
        buildColumnDropdown(dropdown);
      }
    });
  });
}

function handleSortClick(e: Event): void {
  const th = (e.target as HTMLElement).closest<HTMLTableCellElement>("th.sortable");
  if (!th) return;
  const field = th.dataset["sort"] as SortField | undefined;
  if (!field) return;

  if (field === plantTableState.sortField) {
    plantTableState.sortDir = plantTableState.sortDir === "asc" ? "desc" : "asc";
  } else {
    plantTableState.sortField = field;
    plantTableState.sortDir = "asc";
  }
  localStorage.setItem("gardenops-sort", JSON.stringify({ field: plantTableState.sortField, dir: plantTableState.sortDir }));
  renderPlantsTable();
}

// ── Zone toggles ───────────────────────────────────────────
let refreshZoneToggles = (): void => {};

function initZoneToggles(): void {
  const container = document.getElementById("zone-toggles");
  if (!container) {
    refreshZoneToggles = (): void => {};
    return;
  }
  const zoneContainer = container;

  function renderCurrent(): void {
    const zones = zoneToggleZonesFromPlots(state.plots);
    renderZoneToggles(
      zoneContainer,
      zones,
      { hiddenZones: mapInteraction.hiddenZones },
      handleToggle,
    );
    const grid = document.getElementById("map-grid");
    if (grid) {
      applyZoneVisibility(grid, mapInteraction.hiddenZones);
    }
  }

  function handleToggle(zone: string): void {
    if (mapInteraction.hiddenZones.has(zone)) {
      mapInteraction.hiddenZones.delete(zone);
    } else {
      mapInteraction.hiddenZones.add(zone);
    }
    renderCurrent();
  }

  refreshZoneToggles = renderCurrent;
  renderCurrent();
}

function initCategoryFilters(): void {
  document.querySelectorAll<HTMLButtonElement>(".cat-filter-btn[data-cat]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cat = btn.dataset["cat"] ?? "";
      if (mapInteraction.activeCatFilter === cat) {
        mapInteraction.activeCatFilter = null;
        btn.classList.remove("active");
      } else {
        document.querySelectorAll(".cat-filter-btn[data-cat]").forEach((b) => b.classList.remove("active"));
        mapInteraction.activeCatFilter = cat;
        btn.classList.add("active");
      }
      applyCategoryHighlight();
    });
  });

  const elevBtn = document.getElementById("elevation-toggle-btn");
  elevBtn?.addEventListener("click", () => void toggleElevation(elevBtn));

  const elevEditBtn = document.getElementById("elevation-edit-btn");
  elevEditBtn?.addEventListener(
    "click",
    () => void openElevationEditor(),
  );
}

function syncElevEditButton(): void {
  const editBtn = document.getElementById("elevation-edit-btn");
  if (editBtn) editBtn.hidden = !mapInteraction.showElevation;
}

async function toggleElevation(btn: HTMLElement): Promise<void> {
  if (mapInteraction.showElevation) {
    mapInteraction.showElevation = false;
    btn.classList.remove("active");
    syncElevEditButton();
    renderPlots();
    return;
  }
  if (!mapInteraction.elevationCache) {
    try {
      mapInteraction.elevationCache = await getPlotElevationsApi();
    } catch {
      showToast(t("map.elevation_load_failed"), "error");
      return;
    }
    if (!mapInteraction.elevationCache.available) {
      showToast(t("map.lidar_unavailable"), "error");
      mapInteraction.elevationCache = null;
      return;
    }
    if (Object.keys(mapInteraction.elevationCache.elevations).length === 0) {
      showToast(t("map.no_elevation_data"), "error");
      mapInteraction.elevationCache = null;
      return;
    }
  }
  mapInteraction.showElevation = true;
  btn.classList.add("active");
  syncElevEditButton();
  renderPlots();
}

async function openElevationEditor(): Promise<void> {
  if (!mapInteraction.elevationCache) {
    try {
      mapInteraction.elevationCache = await getPlotElevationsApi();
    } catch {
      showToast(t("map.elevation_load_failed"), "error");
      return;
    }
    if (!mapInteraction.elevationCache.available) {
      showToast(t("map.lidar_unavailable"), "error");
      mapInteraction.elevationCache = null;
      return;
    }
    if (Object.keys(mapInteraction.elevationCache.elevations).length === 0) {
      showToast(t("map.no_elevation_data"), "error");
      mapInteraction.elevationCache = null;
      return;
    }
  }

  const zones: Record<string, string> = {};
  for (const p of state.plots) {
    zones[p.plot_id] = p.zone_code;
  }

  const editorPlots = state.plots
    .filter((p) => p.grid_row != null && p.grid_col != null)
    .map((p) => ({
      plot_id: p.plot_id,
      zone_code: p.zone_code,
      grid_row: p.grid_row as number,
      grid_col: p.grid_col as number,
      color: p.color,
    }));

  showElevationEditorLazy({
    elevations: mapInteraction.elevationCache.elevations,
    overrides: mapInteraction.elevationCache.overrides,
    zones,
    plots: editorPlots,
    gridRows: state.gridRows,
    gridCols: state.gridCols,
    onSave: async (ovr) => {
      mapInteraction.elevationCache = await updatePlotElevationsApi(ovr);
      if (mapInteraction.showElevation) renderPlots();
      showToast(t("map.elevation_saved"));
    },
  });
}

function applyCategoryHighlight(): void {
  const grid = document.getElementById("map-grid");
  if (!grid) return;
  const highlightedIds = new Set(
    state.plots
      .filter((plot) => !mapInteraction.activeCatFilter || plot.categories.includes(mapInteraction.activeCatFilter))
      .map((plot) => plot.plot_id),
  );

  grid.querySelectorAll<HTMLElement>(".plot").forEach((el) => {
    el.classList.remove("cat-highlight", "cat-dim");
    if (!mapInteraction.activeCatFilter) return;
    const plotId = el.dataset["plotId"] ?? "";
    if (highlightedIds.has(plotId)) {
      el.classList.add("cat-highlight");
    } else {
      el.classList.add("cat-dim");
    }
  });
}

function applySunlightDiff(previous: Set<string>, next: Set<string>): boolean {
  const grid = document.getElementById("map-grid");
  if (!grid) return false;
  for (const plotId of previous) {
    if (next.has(plotId)) continue;
    const plot = grid.querySelector<HTMLElement>(`.plot[data-plot-id="${plotId}"]`);
    if (!plot) return false;
    plot.classList.remove("sunlit-direct");
    delete plot.dataset["sunlight"];
  }
  for (const plotId of next) {
    if (previous.has(plotId)) continue;
    const plot = grid.querySelector<HTMLElement>(`.plot[data-plot-id="${plotId}"]`);
    if (!plot) return false;
    plot.classList.add("sunlit-direct");
    plot.dataset["sunlight"] = "sun";
  }
  return true;
}

// ── Map rendering ──────────────────────────────────────────
function clearDropGhosts(): void {
  document.querySelectorAll(".drop-ghost").forEach((g) => g.remove());
}

function isHousePositionValid(topRow: number, topCol: number): boolean {
  const w = state.houseSize.width;
  const h = state.houseSize.height;
  if (topRow < 1 || topCol < 1 || topRow + h - 1 > state.gridRows || topCol + w - 1 > state.gridCols) {
    return false;
  }
  for (const plot of state.plots) {
    if (
      plot.grid_row != null &&
      plot.grid_col != null &&
      plot.grid_row >= topRow &&
      plot.grid_row < topRow + h &&
      plot.grid_col >= topCol &&
      plot.grid_col < topCol + w
    ) {
      return false;
    }
  }
  return true;
}

function showDropGhosts(targetRow: number, targetCol: number): void {
  if (!mapInteraction.dragStartPosition || !mapInteraction.draggedPlotId) return;

  const grid = document.getElementById("map-grid");
  if (!grid) return;

  clearDropGhosts();

  const rowOff = targetRow - mapInteraction.dragStartPosition.row;
  const colOff = targetCol - mapInteraction.dragStartPosition.col;

  for (const plotId of state.selectedPlotIds) {
    const plot = state.plots.find((p) => p.plot_id === plotId);
    if (!plot || plot.grid_row == null || plot.grid_col == null) continue;
    const newRow = plot.grid_row + rowOff;
    const newCol = plot.grid_col + colOff;
    if (newRow < 1 || newRow > state.gridRows || newCol < 1 || newCol > state.gridCols) {
      continue;
    }
    const ghost = document.createElement("div");
    ghost.className = "drop-ghost";
    ghost.dataset["zone"] = plot.zone_code;
    ghost.style.gridRow = String(newRow);
    ghost.style.gridColumn = String(newCol);
    grid.appendChild(ghost);
  }
}

async function loadPlotAlerts(): Promise<void> {
  try {
    const data = await fetchPlotAlertsApi();
    state.plotAlerts = {
      task_plots: new Set(data.task_plots),
      issue_plots: new Set(data.issue_plots),
      frost_plots: new Set(data.frost_plots),
    };
    const grid = document.getElementById("map-grid");
    if (grid) applyPlotIndicators(grid, state.plotAlerts);
  } catch {
    // Non-critical — degrade silently
  }
}

function showIndoorPanel(): void {
  const indoorPlot = state.plots.find(p => p.zone_code === "I");
  if (!indoorPlot) return;
  setIndoorPlotId(indoorPlot.plot_id);

  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });

  const dialog = document.createElement("div");
  dialog.className = "modal-content indoor-panel";

  const header = document.createElement("div");
  header.className = "indoor-panel-header";
  const title = document.createElement("h2");
  title.textContent = t("plants.mode_indoor");
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "close-btn";
  closeBtn.setAttribute("aria-label", t("media.close_viewer"));
  closeBtn.textContent = "\u00d7";
  closeBtn.addEventListener("click", () => overlay.remove());
  header.append(title, closeBtn);

  const content = document.createElement("div");
  content.className = "indoor-panel-content";

  dialog.append(header, content);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  void loadIndoorPlants().then(() => {
    renderIndoorPlants(content);
  });

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
    }
  };
  document.addEventListener("keydown", onKey);
}

function syncMapSelectedPlots(): void {
  const grid = document.getElementById("map-grid");
  if (!grid) return;
  syncSelectedPlots(grid, state.selectedPlotIds);
}

function selectPlotRangeInPlace(endPlotId: string): void {
  if (state.selectedPlotIds.size === 0) return;

  const startPlotId = Array.from(state.selectedPlotIds)[0];
  const startPlot = state.plots.find((plot) => plot.plot_id === startPlotId);
  const endPlot = state.plots.find((plot) => plot.plot_id === endPlotId);
  if (!startPlot || !endPlot) return;
  if (startPlot.grid_row == null || startPlot.grid_col == null) return;
  if (endPlot.grid_row == null || endPlot.grid_col == null) return;

  const minRow = Math.min(startPlot.grid_row, endPlot.grid_row);
  const maxRow = Math.max(startPlot.grid_row, endPlot.grid_row);
  const minCol = Math.min(startPlot.grid_col, endPlot.grid_col);
  const maxCol = Math.max(startPlot.grid_col, endPlot.grid_col);

  state.plots.forEach((plot) => {
    if (
      plot.grid_row != null
      && plot.grid_col != null
      && plot.grid_row >= minRow
      && plot.grid_row <= maxRow
      && plot.grid_col >= minCol
      && plot.grid_col <= maxCol
    ) {
      state.selectedPlotIds.add(plot.plot_id);
    }
  });
}

function renderPlots(): void {
  const grid = document.getElementById("map-grid");
  if (!grid) return;

  const elevRange = mapInteraction.elevationCache?.min_m != null && mapInteraction.elevationCache?.max_m != null
    ? { min: mapInteraction.elevationCache.min_m, max: mapInteraction.elevationCache.max_m }
    : null;

  renderMapGrid({
    grid,
    plots: state.plots.filter(p => p.grid_row !== null && p.grid_col !== null),
    gridRows: state.gridRows,
    gridCols: state.gridCols,
    selectedPlotIds: state.selectedPlotIds,
    highlightedPlotIds: state.highlightedPlotIds,
    sunlitPlotIds: state.sunlitPlotIds,
    elevationData: mapInteraction.elevationCache?.elevations ?? null,
    elevationRange: elevRange,
    showElevation: mapInteraction.showElevation,
    editMode: state.editMode,
    housePosition: state.housePosition,
    houseSize: state.houseSize,
    northDegrees: state.northDegrees,
    onPlotClick: (plot, event) => {
      if (state.editMode) {
        if (event.ctrlKey || event.metaKey) {
          if (state.selectedPlotIds.has(plot.plot_id)) {
            state.selectedPlotIds.delete(plot.plot_id);
          } else {
            state.selectedPlotIds.add(plot.plot_id);
          }
          updateSelectionCount(state);
          syncMapSelectedPlots();
        } else if (event.shiftKey && state.selectedPlotIds.size > 0) {
          selectPlotRangeInPlace(plot.plot_id);
          updateSelectionCount(state);
          syncMapSelectedPlots();
        } else {
          state.selectedPlotIds.clear();
          state.selectedPlotIds.add(plot.plot_id);
          updateSelectionCount(state);
          syncMapSelectedPlots();
        }
      } else {
        void selectPlot(state, plot.plot_id, plotCbs);
      }
    },
    onPlotContextMenu: (plot, x, y) => {
      if (!state.editMode) return;
      if (!state.selectedPlotIds.has(plot.plot_id)) {
        state.selectedPlotIds.clear();
        state.selectedPlotIds.add(plot.plot_id);
        updateSelectionCount(state);
        syncMapSelectedPlots();
      }
      showPlotContextMenu(plot.plot_id, x, y);
    },
    onPlotDragStart: (plot, event) => {
      if (!state.editMode) {
        event.preventDefault();
        return;
      }

      if (!state.selectedPlotIds.has(plot.plot_id)) {
        state.selectedPlotIds.clear();
        state.selectedPlotIds.add(plot.plot_id);
        updateSelectionCount(state);
        syncMapSelectedPlots();
      }
      mapInteraction.draggedPlotId = plot.plot_id;
      if (plot.grid_row == null || plot.grid_col == null) return;
      mapInteraction.dragStartPosition = { row: plot.grid_row, col: plot.grid_col };
      const target = event.currentTarget as HTMLElement | null;
      target?.classList.add("dragging");

      document.querySelectorAll(".multi-selected").forEach((sel) => {
        sel.classList.add("dragging");
      });

      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", plot.plot_id);
      }
    },
    onPlotDragEnd: () => {
      document.querySelectorAll(".dragging").forEach((el) => el.classList.remove("dragging"));
      clearDropGhosts();
      mapInteraction.draggedPlotId = null;
      mapInteraction.dragStartPosition = null;
    },
    onDragOverCell: showDropGhosts,
    onDropToCell: (targetRow, targetCol, targetPlotId, event) => {
      clearDropGhosts();

      if (event?.dataTransfer?.types.includes("application/plant-id")) {
        const pltId = event.dataTransfer.getData("application/plant-id");
        const fromPlotId = event.dataTransfer.getData("application/from-plot");
        if (!targetPlotId) {
          showToast(t("map.drop_plant_hint"), "error");
          return;
        }
        if (targetPlotId === fromPlotId) return;
        void movePlantBetweenPlots(fromPlotId, targetPlotId, pltId);
        return;
      }

      if (mapInteraction.draggedPlotId && mapInteraction.dragStartPosition && (!targetPlotId || !state.selectedPlotIds.has(targetPlotId))) {
        void moveSelectedPlots(state, targetRow - mapInteraction.dragStartPosition.row, targetCol - mapInteraction.dragStartPosition.col, editCbs);
      }
    },
    onExtendPlot: (plot) => void extendPlot(plot),
    onEmptyCellClick: (row, col) => {
      if (state.editMode) openCreatePlotDialog(row, col);
    },
    onHouseMoveStart: (event) => {
      if (!state.editMode) return;
      const grid = document.getElementById("map-grid");
      if (!grid) return;
      const rect = grid.getBoundingClientRect();
      mapInteraction.houseMoveSession = {
        startX: event.clientX,
        startY: event.clientY,
        startRow: state.housePosition.row,
        startCol: state.housePosition.col,
        cellWidth: rect.width / state.gridCols,
        cellHeight: rect.height / state.gridRows,
        prevRow: state.housePosition.row,
        prevCol: state.housePosition.col,
      };
      document.body.classList.add("moving-house");
      window.addEventListener("mousemove", onHouseMoveMove);
      window.addEventListener("mouseup", stopHouseMove);
    },
    onHouseResizeStart: startHouseResize,
    onHouseClick: showIndoorPanel,
  });

  if (state.plotAlerts) {
    applyPlotIndicators(grid, state.plotAlerts);
  }

  refreshZoneToggles();
  syncShadePanelContext();
  applyZoneVisibility(grid, mapInteraction.hiddenZones);
  applyCategoryHighlight();
  cameraCtrl?.updateMinimap(state.plots, mapInteraction.hiddenZones);
}

function onHouseMoveMove(event: MouseEvent): void {
  if (!mapInteraction.houseMoveSession) return;
  const rowDelta = Math.round(
    (event.clientY - mapInteraction.houseMoveSession.startY) / mapInteraction.houseMoveSession.cellHeight,
  );
  const colDelta = Math.round(
    (event.clientX - mapInteraction.houseMoveSession.startX) / mapInteraction.houseMoveSession.cellWidth,
  );
  const newRow = mapInteraction.houseMoveSession.startRow + rowDelta;
  const newCol = mapInteraction.houseMoveSession.startCol + colDelta;
  if (newRow === mapInteraction.houseMoveSession.prevRow && newCol === mapInteraction.houseMoveSession.prevCol) return;
  mapInteraction.houseMoveSession.prevRow = newRow;
  mapInteraction.houseMoveSession.prevCol = newCol;

  const house = document.getElementById("house");
  if (!house) return;

  if (isHousePositionValid(newRow, newCol)) {
    state.housePosition = { row: newRow, col: newCol };
    house.style.gridRow = `${newRow} / ${newRow + state.houseSize.height}`;
    house.style.gridColumn = `${newCol} / ${newCol + state.houseSize.width}`;
    house.classList.remove("house--invalid");
  } else {
    // Show where it would go, but mark invalid
    house.style.gridRow = `${newRow} / ${newRow + state.houseSize.height}`;
    house.style.gridColumn = `${newCol} / ${newCol + state.houseSize.width}`;
    house.classList.add("house--invalid");
  }
}

function stopHouseMove(): void {
  if (!mapInteraction.houseMoveSession) return;
  const house = document.getElementById("house");
  const startRow = mapInteraction.houseMoveSession.startRow;
  const startCol = mapInteraction.houseMoveSession.startCol;
  const currentRow = state.housePosition.row;
  const currentCol = state.housePosition.col;

  // If current position is invalid (house--invalid class), revert
  if (house?.classList.contains("house--invalid")) {
    state.housePosition = { row: startRow, col: startCol };
    house.classList.remove("house--invalid");
    renderPlots();
    showToast(t("map.house_overlap_error"), "error");
  } else if (currentRow !== startRow || currentCol !== startCol) {
    // Valid move — record undo and persist
    state.undoStack.push({
      plots: [],
      house: {
        row: startRow,
        col: startCol,
        width: state.houseSize.width,
        height: state.houseSize.height,
      },
    });
    if (state.undoStack.length > UNDO_STACK_LIMIT) {
      state.undoStack.shift();
    }
    updateUndoButton(state);
    void persistHouseGeometry();
  }

  mapInteraction.houseMoveSession = null;
  document.body.classList.remove("moving-house");
  window.removeEventListener("mousemove", onHouseMoveMove);
  window.removeEventListener("mouseup", stopHouseMove);
}

function startHouseResize(event: MouseEvent): void {
  if (!state.editMode) return;
  mapInteraction.houseResizeSession = {
    startX: event.clientX,
    startY: event.clientY,
    startWidth: state.houseSize.width,
    startHeight: state.houseSize.height,
    startHouse: {
      row: state.housePosition.row,
      col: state.housePosition.col,
      width: state.houseSize.width,
      height: state.houseSize.height,
    },
  };
  document.body.classList.add("resizing-house");
  window.addEventListener("mousemove", onHouseResizeMove);
  window.addEventListener("mouseup", stopHouseResize);
}

function onHouseResizeMove(event: MouseEvent): void {
  if (!mapInteraction.houseResizeSession) return;
  const grid = document.getElementById("map-grid");
  if (!grid) return;
  const rect = grid.getBoundingClientRect();
  const cellWidth = rect.width / state.gridCols;
  const cellHeight = rect.height / state.gridRows;
  const widthDelta = Math.round(
    (event.clientX - mapInteraction.houseResizeSession.startX) / cellWidth,
  );
  const heightDelta = Math.round(
    (event.clientY - mapInteraction.houseResizeSession.startY) / cellHeight,
  );
  state.houseSize = clampHouseSize(
    state,
    Math.max(HOUSE_MIN_WIDTH, mapInteraction.houseResizeSession.startWidth + widthDelta),
    Math.max(HOUSE_MIN_HEIGHT, mapInteraction.houseResizeSession.startHeight + heightDelta),
  );
  const house = document.getElementById("house");
  if (!house) return;
  house.style.gridRow = `${state.housePosition.row} / ${state.housePosition.row + state.houseSize.height}`;
  house.style.gridColumn = `${state.housePosition.col} / ${state.housePosition.col + state.houseSize.width}`;
}

function stopHouseResize(): void {
  if (!mapInteraction.houseResizeSession) return;
  const start = mapInteraction.houseResizeSession.startHouse;
  if (
    start.width !== state.houseSize.width ||
    start.height !== state.houseSize.height
  ) {
    state.undoStack.push({
      plots: [],
      house: start,
    });
    if (state.undoStack.length > UNDO_STACK_LIMIT) {
      state.undoStack.shift();
    }
    updateUndoButton(state);
    void persistHouseGeometry();
  }
  mapInteraction.houseResizeSession = null;
  document.body.classList.remove("resizing-house");
  window.removeEventListener("mousemove", onHouseResizeMove);
  window.removeEventListener("mouseup", stopHouseResize);
}

// ── Plot/plant actions ─────────────────────────────────────
async function deletePlot(plotId: string): Promise<void> {
  if (!ensureWriteAccess()) return;
  const plotsToDelete = state.selectedPlotIds.has(plotId)
    ? Array.from(state.selectedPlotIds)
    : [plotId];
  let dependentCount = 0;
  try {
    const impacts = await Promise.all(
      plotsToDelete.map((id) => getPlotDeleteImpactApi(id)),
    );
    dependentCount = impacts.reduce(
      (total, impact) => total + impact.total_dependent_references,
      0,
    );
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
    return;
  }
  const confirmMsg =
    dependentCount > 0
      ? plotsToDelete.length === 1
        ? t("plots.confirm_delete_single_with_impact", {
            count: dependentCount,
            plot: plotsToDelete[0],
          })
        : t("plots.confirm_delete_multiple_with_impact", {
            count: plotsToDelete.length,
            references: dependentCount,
          })
      : plotsToDelete.length === 1
        ? t("plots.confirm_delete_single", { plot: plotsToDelete[0] })
        : t("plots.confirm_delete_multiple", { count: plotsToDelete.length });

  if (!(await confirmDialog(confirmMsg, t("common.delete")))) return;

  try {
    for (const id of plotsToDelete) {
      await deletePlotApi(id);
    }
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }

  state.selectedPlotIds.clear();
  closePanel();
  invalidatePlantsCache();
  await fetchPlots();
}

async function extendPlot(plot: Plot): Promise<void> {
  if (!ensureWriteAccess()) return;
  const zonePlots = state.plots.filter((p) => p.zone_code === plot.zone_code);
  const maxNum = Math.max(...zonePlots.map((p) => p.plot_number));
  const nextNum = maxNum + 1;
  const nextId = `${plot.zone_code}${nextNum}`;

  const occupied = new Set(
    state.plots.map((p) => `${p.grid_row},${p.grid_col}`),
  );

  if (plot.grid_row == null || plot.grid_col == null) return;
  const candidates = [
    { row: plot.grid_row, col: plot.grid_col + 1 },
    { row: plot.grid_row + 1, col: plot.grid_col },
    { row: plot.grid_row, col: plot.grid_col - 1 },
    { row: plot.grid_row - 1, col: plot.grid_col },
  ];

  const target = candidates.find(
    (c) =>
      c.row >= 1 &&
      c.row <= state.gridRows &&
      c.col >= 1 &&
      c.col <= state.gridCols &&
      !occupied.has(`${c.row},${c.col}`),
  );

  if (!target) {
    showToast(t("map.no_free_cell"), "error");
    return;
  }

  try {
    await createPlotApi({
      plot_id: nextId,
      zone_code: plot.zone_code,
      zone_name: plot.zone_name,
      plot_number: nextNum,
      grid_row: target.row,
      grid_col: target.col,
      sub_zone: plot.sub_zone,
      notes: "",
    });
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
    return;
  }

  invalidatePlantsCache();
  await fetchPlots();
}

async function movePlantBetweenPlots(
  fromPlotId: string,
  toPlotId: string,
  pltId: string,
): Promise<void> {
  if (!ensureWriteAccess()) return;
  try {
    await movePlantBetweenPlotsApi(fromPlotId, toPlotId, pltId);
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
    return;
  }
  invalidatePlantsCache();
  await fetchPlots();
  if (activeTab === "garden" || activeTab === "activity") await refreshActiveNavigationContent();
  void selectPlot(state, toPlotId, plotCbs);
}

function showPlotContextMenu(plotId: string, x: number, y: number): void {
  showDeleteMenuLazy({
    x,
    y,
    onEdit: () => openEditPlotDialog(plotId),
    onDelete: () => {
      void deletePlot(plotId);
    },
  });
}

function openCreatePlotDialog(row: number, col: number): void {
  if (!ensureWriteAccess()) return;
  showCreatePlotDialogLazy({
    row,
    col,
    onSubmit: async (data) => {
      await createPlotApi(data);
      invalidatePlantsCache();
      await fetchPlots();
    },
  });
}

function openCreateZoneDialog(): void {
  if (!ensureWriteAccess()) return;
  const activeGardenId = getActiveGardenContext();
  if (!activeGardenId) {
    showToast(t("error.missing_garden"), "error");
    return;
  }
  showCreateZoneDialogLazy({
    gridRows: state.gridRows,
    gridCols: state.gridCols,
    onSubmit: async (data) => {
      const result = await createZoneApi(activeGardenId, data);
      invalidatePlantsCache();
      await fetchPlots();
      if (activeTab !== "map") await refreshActiveNavigationContent();
      if (result.skipped_cells > 0) {
        showToast(
          t("map.zone_created_skipped", {
            created: result.plots_created,
            zone: result.zone_code,
            skipped: result.skipped_cells,
          }),
          "success",
        );
      } else {
        showToast(
          t("map.zone_created", {
            created: result.plots_created,
            zone: result.zone_code,
          }),
          "success",
        );
      }
    },
  });
}

function openEditPlotDialog(plotId: string): void {
  if (!ensureWriteAccess()) return;
  const plot = state.plots.find((p) => p.plot_id === plotId);
  if (!plot) return;

  showEditPlotDialogLazy({
    plotId,
    currentColor: plot.color,
    onSubmit: async (newName, color) => {
      const fields: Record<string, string | number | null> = {};
      if (color !== plot.color) {
        fields["color"] = color;
      }
      if (newName !== plotId) {
        fields["new_plot_id"] = newName;
      }
      if (Object.keys(fields).length === 0) return;
      await updatePlotApi(plotId, fields);
      invalidatePlantsCache();
      await fetchPlots();
    },
  });
}

function openEditPlantDialog(plant: Plant): void {
  if (!ensureWriteAccess()) return;
  const plotOptions = state.plots
    .filter((p) => p.grid_row != null && p.grid_col != null)
    .map((p) => ({
      plot_id: p.plot_id,
      zone_code: p.zone_code,
      grid_row: p.grid_row as number,
      grid_col: p.grid_col as number,
      color: p.color,
    }));
  const originalPlotIds = new Set(plant.plot_ids ?? []);

  showEditPlantDialogLazy({
    plant,
    availablePlots: plotOptions,
    plotAssignmentMeanings: authProfile?.plot_assignment_meanings ?? [],
    gridRows: state.gridRows,
    gridCols: state.gridCols,
    onMediaChanged: (targets) => {
      void refreshPlantMediaPreviews(
        targets
          .filter((target) => target.target_type === "plant")
          .map((target) => target.target_id),
      );
      void refreshJournalMediaPreviews(
        targets
          .filter((target) => target.target_type === "journal_entry")
          .map((target) => target.target_id),
      );
    },
    onSubmit: async (fields, plotIds) => {
      await updatePlantApi(plant.plt_id, fields);

      const newSet = new Set(plotIds);
      const allWarnings: string[] = [];
      const addedPlotIds = plotIds.filter((pid) => !originalPlotIds.has(pid));
      const removedPlotIds = Array.from(originalPlotIds).filter((pid) => !newSet.has(pid));
      const affectedPlotIds = Array.from(new Set([
        ...Array.from(originalPlotIds),
        ...plotIds,
      ]));

      const addedResults = await Promise.all(
        addedPlotIds.map((pid) => addPlantToPlotApi(pid, plant.plt_id)),
      );
      for (const result of addedResults) {
        if (!result.companion_warnings?.length) continue;
        for (const warning of result.companion_warnings) {
          allWarnings.push(warning.description);
        }
      }

      await Promise.all(
        removedPlotIds.map((pid) => removePlantFromPlotApi(pid, plant.plt_id)),
      );
      affectedPlotIds.forEach((plotId) => invalidatePlotPanelCache(plotId));

      await Promise.all([
        fetchPlots(),
        plantsCacheLoaded
          ? refreshPlantsById([plant.plt_id]).then(() => undefined)
          : Promise.resolve(),
      ]);
      if (state.selectedPlotId && affectedPlotIds.includes(state.selectedPlotId)) {
        void selectPlot(state, state.selectedPlotId, plotCbs);
      }
      if (allWarnings.length > 0) {
        showAppStatus(allWarnings.join(" | "));
      }
    },
    onAiUpdate: (q) => aiPlantLookup(q),
    onObservationChanged: async (pltId) => {
      const refreshed = await refreshPlantsById([pltId]);
      return refreshed.get(pltId) ?? null;
    },
    onDelete: (pltId) => void handleDeletePlant(pltId),
    onReportIssue: (pltId) => {
      const p = state.plantsCache.find((pl) => pl.plt_id === pltId);
      const pName = p ? p.name : pltId;
      const plotIds = p?.plot_ids ?? [];
      showDiagnosePlantModal(pltId, plotIds, pName, {
        onIssueCreated: (issueId) => {
          navigateToSubMode("issues");
          void loadIssues();
          void fetchIssueApi(issueId).then(
            (issue) => {
              showToast(t("diagnose.issue_created"), "success");
              void import("./tabs/issuesTab").then((mod) =>
                mod.openIssueForm(issue),
              );
            },
            () => {
              showToast(t("diagnose.issue_created"), "success");
            },
          );
        },
        onClose: () => {},
      });
    },
  });
}

async function handleDeletePlant(pltId: string): Promise<void> {
  if (!ensureWriteAccess()) return;
  const plant = state.plantsCache.find((p) => p.plt_id === pltId);
  const name = plant ? plant.name : pltId;
  const affectedPlotIds = plant?.plot_ids ?? [];
  if (!(await confirmDialog(t("plants.confirm_delete", { name }), t("common.delete")))) return;
  try {
    await deletePlantApi(pltId);
    plantMediaPreviewById.delete(pltId);
    affectedPlotIds.forEach((plotId) => invalidatePlotPanelCache(plotId));
    invalidatePlantsCache();
    await ensurePlantsLoaded();
    await fetchPlots();
    if (state.selectedPlotId && affectedPlotIds.includes(state.selectedPlotId)) {
      void selectPlot(state, state.selectedPlotId, plotCbs);
    }
  } catch (err) {
    showFetchError(err);
  }
}

async function nextPlantId(): Promise<string> {
  try {
    return await getNextPlantIdApi();
  } catch {
    // Fallback to local calculation if endpoint fails
    let max = 0;
    for (const p of state.plantsCache) {
      const m = p.plt_id.match(/^PLT-(\d+)$/);
      if (m) {
        const n = parseInt(m[1] ?? "0", 10);
        if (n > max) max = n;
      }
    }
    return `PLT-${String(max + 1).padStart(3, "0")}`;
  }
}

function buildPlantSearchParams(
  preselectedPlotId?: string,
): PlantSearchDialogParams {
  return {
    ctx: appContext,
    preselectedPlotId,
    getNextId: () => nextPlantId(),  // returns Promise<string>
    getPlotOptions: () =>
      state.plots
        .filter((p) => p.grid_row != null && p.grid_col != null)
        .map((p) => ({
          plot_id: p.plot_id,
          zone_code: p.zone_code,
          grid_row: p.grid_row as number,
          grid_col: p.grid_col as number,
          color: p.color,
        })),
    getPlotAssignmentMeanings: () =>
      authProfile?.plot_assignment_meanings ?? [],
    getGridDims: () => ({
      rows: state.gridRows,
      cols: state.gridCols,
    }),
    onCreateSubmit: async (data, plotIds) => {
      await createPlantApi(data);
      const pltId = data["plt_id"] as string;
      const allWarnings: string[] = [];
      for (const pid of plotIds) {
        const result = await addPlantToPlotApi(pid, pltId);
        if (result.companion_warnings?.length) {
          for (const w of result.companion_warnings) {
            allWarnings.push(w.description);
          }
        }
      }
      invalidatePlantsCache();
      await ensurePlantsLoaded();
      if (plotIds.length > 0) await fetchPlots();
      if (allWarnings.length > 0) {
        showAppStatus(allWarnings.join(" | "));
      }
    },
    onAiLookup: async (q): Promise<AiPlantData> => {
      return await aiPlantLookup(q);
    },
    onEditPlant: (plant) => openEditPlantDialog(plant),
    onPlantAssigned: () => {
      invalidatePlantsCache();
      void ensurePlantsLoaded();
      void fetchPlots();
    },
    onIdentifyFromPhoto: () => {
      showIdentifyPlantModal({
        onAddPlant: (prefill) => {
          void openCreatePlantDialog(
            preselectedPlotId,
            prefill,
          );
        },
        onClose: () => {},
      });
    },
  };
}

function openCreatePlantDialog(
  preselectedPlotId?: string,
  prefill?: PlantCreatePrefill,
): void {
  if (prefill) {
    void openCreatePlantDialogWithPrefill(
      prefill,
      preselectedPlotId,
    );
    return;
  }
  showPlantSearchDialog(
    buildPlantSearchParams(preselectedPlotId),
  );
}

async function openCreatePlantDialogWithPrefill(
  prefill: PlantCreatePrefill,
  preselectedPlotId?: string,
): Promise<void> {
  if (!ensureWriteAccess()) return;

  const params = buildPlantSearchParams(
    preselectedPlotId,
  );
  const dims = params.getGridDims();

  showCreatePlantDialogLazy({
    nextId: await params.getNextId(),
    availablePlots: params.getPlotOptions(),
    plotAssignmentMeanings:
      params.getPlotAssignmentMeanings(),
    gridRows: dims.rows,
    gridCols: dims.cols,
    onSubmit: params.onCreateSubmit,
    onAiLookup: params.onAiLookup,
    prefill,
    preselectedPlotIds: preselectedPlotId
      ? [preselectedPlotId]
      : undefined,
    ...(params.onIdentifyFromPhoto
      ? {
          onIdentifyFromPhoto:
            params.onIdentifyFromPhoto,
        }
      : {}),
  });
}

async function confirmSensitiveAdminAction(
  actionLabel: string,
  defaultReason: string,
): Promise<string | null> {
  const actionReason = (await promptDialog(
    `${actionLabel} reason:`,
    defaultReason,
  ))?.trim() ?? "";
  if (!actionReason) return null;
  if (authProfile?.auth_type === "session" && authProfile.role === "admin") {
    if (authProfile.mfa_methods.includes("passkey") && isPasskeySupported()) {
      try {
        const options = await beginPasskeyReauthenticationApi();
        const credential = await getPasskey(options.publicKey);
        await finishPasskeyReauthenticationApi(options.challenge_token, credential);
        authProfile = { ...authProfile, mfa_authenticated: true };
        return actionReason;
      } catch (err) {
        if (!authProfile.mfa_enabled && err instanceof DOMException && err.name === "NotAllowedError") {
          return null;
        }
        if (!authProfile.mfa_enabled) throw err;
      }
    }
    const currentPassword = (await promptPasswordDialog(
      `Confirm your current password to ${actionLabel.toLowerCase()}:`,
    )) ?? "";
    if (!currentPassword.trim()) return null;
    let reauthOptions: { mfaCode?: string; recoveryCode?: string } = {};
    if (authProfile.mfa_enabled) {
      const mfaCode = await promptDialog(
        "Enter your authenticator code. Leave blank to use a recovery code.",
        "",
      );
      if (mfaCode === null) return null;
      const normalizedCode = mfaCode.trim();
      if (normalizedCode) {
        reauthOptions = { mfaCode: normalizedCode };
      } else {
        const recoveryCode = (await promptDialog("Enter a recovery code:", "")) ?? "";
        if (!recoveryCode.trim()) return null;
        reauthOptions = { recoveryCode: recoveryCode.trim() };
      }
    }
    await reauthenticateApi(currentPassword, reauthOptions);
  }
  return actionReason;
}

async function importMap(): Promise<void> {
  if (!ensureWriteAccess()) return;
  const input = queryInput("import-map-input");
  const file = input?.files?.[0];
  if (!file) return;

  try {
    const text = await file.text();
    const data: unknown = JSON.parse(text);
    let layout: LayoutExport;
    if (Array.isArray(data)) {
      layout = { plots: data as LayoutExportPlot[] };
    } else if (data && typeof data === "object" && Array.isArray((data as LayoutExport).plots)) {
      layout = data as LayoutExport;
    } else {
      showToast(t("map.import_invalid"), "error");
      return;
    }
    if (!(await confirmDialog(t("map.confirm_import", { count: layout.plots.length }), t("map.import_button")))) return;
    const actionReason = await confirmSensitiveAdminAction(
      "Import map layout",
      `ui-map-import:${file.name}`,
    );
    if (!actionReason) return;
    await importMapApi(layout, { actionReason });
    mapInteraction.elevationCache = null;
    await fetchPlots();
    await fetchLayoutState();
  } catch (err) {
    showToast(t("map.import_failed", { error: getApiErrorMessage(err) }), "error");
  } finally {
    if (input) input.value = "";
  }
}

async function exportPlantsCsv(): Promise<void> {
  try {
    const blob = await exportPlantsCsvApi();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = isOwnerScopedPlantCsvUi()
      ? `${appSlug()}-my-plants.csv`
      : `${appSlug()}-plants.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showFetchError(err);
  }
}

async function importPlantsCsv(): Promise<void> {
  if (!ensureWriteAccess()) return;
  const input = queryInput("import-csv-input");
  const file = input?.files?.[0];
  if (!file) return;

  try {
    const csvText = await file.text();
    const ownerScoped = isOwnerScopedPlantCsvUi();
    if (!(await confirmDialog(
      ownerScoped
        ? t("plants.confirm_import_own")
        : t("plants.confirm_import_garden"),
      t("map.import_button"),
    ))) {
      return;
    }
    const result = await importPlantsCsvApi(csvText);
    invalidatePlantsCache();
    await ensurePlantsLoaded();
    await fetchPlots();
    const successTarget = ownerScoped ? "your plant list" : "the active garden";
    showToast(
      `Imported ${result.rows} rows into ${successTarget} (${result.created} created, ${result.updated} updated).`,
      "success",
    );
  } catch (err) {
    showFetchError(err);
  } finally {
    if (input) input.value = "";
  }
}

function closePanel(): void {
  dismissPopover();
  dismissDrawer();
  dismissBottomSheet();
  document.querySelectorAll(".plot").forEach((el) => {
    el.classList.remove("selected");
  });
  state.selectedPlotId = null;
  shadePanel?.setSelectedPlot(null);
}

function startClock(): void {
  const els = Array.from(document.querySelectorAll<HTMLElement>(".app-clock"));
  if (els.length === 0) return;
  const tick = () => {
    const now = new Date();
    const formatted = now.toLocaleTimeString("nb-NO", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    els.forEach((el) => {
      el.textContent = formatted;
    });
  };
  tick();
  setInterval(tick, 1000);
}

function wireStatusBanner(): void {
  const dismissBtn = document.getElementById("app-status-dismiss");
  const actionBtn = document.getElementById("app-status-action");
  dismissBtn?.addEventListener("click", clearAppStatus);
  actionBtn?.addEventListener("click", () => {
    appStatusAction?.();
  });
}

function showAppStatus(message: string, actionLabel?: string, action?: () => void): void {
  const banner = document.getElementById("app-status");
  const text = document.getElementById("app-status-text");
  const actionBtn = queryButton("app-status-action");
  const dismissBtn = queryButton("app-status-dismiss");
  if (!banner || !text || !actionBtn || !dismissBtn) return;
  const normalizedMessage = message.trim();
  if (!normalizedMessage) {
    clearAppStatus();
    return;
  }
  text.textContent = normalizedMessage;
  if (actionLabel && action) {
    actionBtn.hidden = false;
    actionBtn.textContent = actionLabel;
    appStatusAction = action;
  } else {
    actionBtn.hidden = true;
    actionBtn.textContent = "";
    appStatusAction = null;
  }
  dismissBtn.hidden = false;
  banner.hidden = false;
}

function showSecurityWarningBanner(message: string): void {
  const banner = document.getElementById("security-warning-banner");
  if (banner) {
    banner.textContent = message;
    banner.hidden = false;
    return;
  }
  const el = document.createElement("div");
  el.id = "security-warning-banner";
  el.className = "security-warning-banner";
  el.textContent = message;
  document.body.prepend(el);
}

function showSecurityWarnings(me: AuthUserProfile): void {
  const warnings = me.security_warnings ?? [];
  if (warnings.length === 0) return;
  showSecurityWarningBanner(warnings.join(" "));
}

function clearAppStatus(): void {
  const banner = document.getElementById("app-status");
  const text = document.getElementById("app-status-text");
  const actionBtn = queryButton("app-status-action");
  const dismissBtn = queryButton("app-status-dismiss");
  if (banner) banner.hidden = true;
  if (text) text.textContent = "";
  if (actionBtn) {
    actionBtn.hidden = true;
    actionBtn.textContent = "";
  }
  if (dismissBtn) dismissBtn.hidden = true;
  appStatusAction = null;
}

function showAdminMfaSetupStatus(): void {
  showAppStatus(
    t("status.admin_mfa_required"),
    t("status.open_admin_settings"),
    () => {
      setActiveTab("admin");
    },
  );
}

function updateAuthButton(): void {
  const label = authProfile || hasStoredAuthToken() ? t("nav.signed_in") : t("nav.sign_in");
  getAuthButtons().forEach((btn) => {
    btn.textContent = label;
    btn.title = label;
    btn.setAttribute("aria-label", label);
  });
}

function isOwnerScopedPlantCsvUi(): boolean {
  return Boolean(authProfile && authProfile.role !== "admin");
}

function updatePlantCsvActionLabels(): void {
  const importCsvBtn = queryButton("import-csv-btn");
  const exportCsvBtn = queryButton("export-csv-btn");
  const ownerScoped = isOwnerScopedPlantCsvUi();
  if (importCsvBtn) {
    importCsvBtn.textContent = ownerScoped ? t("plants.import_my_csv") : t("plants.import_csv");
    importCsvBtn.title = importCsvBtn.textContent;
  }
  if (exportCsvBtn) {
    exportCsvBtn.textContent = ownerScoped ? t("plants.export_my_csv") : t("plants.export_csv");
    exportCsvBtn.title = exportCsvBtn.textContent;
  }
}

function applyWriteAccessUi(): void {
  canWriteInGarden = authProfile
    ? Boolean(authProfile.write_access)
      && !isAdminMfaSetupRequired()
      && gardenContextAvailable
    : false;
  document.body.classList.toggle("garden-read-only", !canWriteInGarden);
  for (const id of WRITE_CONTROL_IDS) {
    const el = document.getElementById(id);
    if (
      el instanceof HTMLButtonElement
      || el instanceof HTMLInputElement
      || el instanceof HTMLSelectElement
      || el instanceof HTMLTextAreaElement
    ) {
      el.disabled = !canWriteInGarden;
    }
  }
  if (!canWriteInGarden && state.editMode) {
    toggleEditMode(state, editCbs);
  }
  syncMobileCapabilities();
}

function ensureWriteAccess(): boolean {
  if (canWriteInGarden) return true;
  showToast(t("error.write_access"), "error");
  return false;
}

async function refreshGardenContext(): Promise<void> {
  const selects = getGardenSelects();
  const roleChips = getGardenRoleChips();
  if (selects.length === 0 || roleChips.length === 0) return;
  resetShadeMapPanelLoadState();
  gardenContextAvailable = false;

  let me: AuthUserProfile | null = null;
  try {
    me = await getAuthMeApi();
  } catch {
    setActiveGardenContext(null);
    try {
      me = await getAuthMeApi();
    } catch {
      gardenOptions = [];
      authProfile = null;
      selects.forEach((select) => {
        select.hidden = true;
        select.disabled = false;
        select.replaceChildren();
      });
      roleChips.forEach((roleChip) => {
        roleChip.hidden = true;
        roleChip.textContent = "";
      });
      applyWriteAccessUi();
      updateAuthButton();
      updatePlantCsvActionLabels();
      updateShadeMapAvailabilityUi();
      syncAdminTabLabels();
      updateMobileHeader();
      return;
    }
  }

  authProfile = me;
  setFeatureGates(me.subscription_tier ?? "home", me.allowed_features ?? []);
  applyFeatureGateUi();
  showSecurityWarnings(me);
  if (me.language && me.language !== getLocale()) {
    setLocale(me.language);
  }
  updatePlantCsvActionLabels();
  let activeGardenId = getActiveGardenContext();
  if (activeGardenId === null && me.garden_visible && me.garden_id !== null) {
    setActiveGardenContext(me.garden_id);
    activeGardenId = me.garden_id;
  }

  let gardens: GardenSummary[] = [];
  let gardensFetchFailed = false;
  try {
    gardens = await getGardensApi();
  } catch (err) {
    gardensFetchFailed = true;
    gardens = [];
    showToast(getApiErrorMessage(err), "error");
  }
  if (gardens.length === 0 && me.garden_visible && me.garden_id !== null) {
    gardens = [
      {
        id: me.garden_id,
        slug: `garden-${me.garden_id}`,
        name: `Garden ${me.garden_id}`,
        role: me.garden_role ?? me.role,
        active: true,
      },
    ];
  }
  gardenOptions = gardens;

  if (gardenOptions.length > 0) {
    const activeInList = activeGardenId !== null
      && gardenOptions.some((garden) => garden.id === activeGardenId);
    if (!activeInList) {
      const fallbackId = (me.garden_visible ? me.garden_id : null) ?? gardenOptions[0]?.id ?? null;
      setActiveGardenContext(fallbackId);
      activeGardenId = fallbackId;
    }
    selects.forEach((select) => {
      const options = gardenOptions.map((garden) => {
        const option = document.createElement("option");
        option.value = String(garden.id);
        option.selected = activeGardenId === garden.id;
        option.textContent = `${garden.name} (${garden.role})`;
        return option;
      });
      select.replaceChildren(...options);
      if (select.id === "mobile-garden-select") {
        select.hidden = false;
        select.disabled = gardenOptions.length <= 1;
      } else {
        select.hidden = gardenOptions.length <= 1;
      }
    });
  } else {
    selects.forEach((select) => {
      select.hidden = true;
      select.disabled = false;
      select.replaceChildren();
    });
  }

  const roleChipLabel = me.role === "admin"
    ? t(me.write_access ? "role.admin_write" : "role.admin_read_only")
    : t(me.write_access ? "role.write_access" : "role.read_only");
  roleChips.forEach((roleChip) => {
    roleChip.textContent = roleChipLabel;
    roleChip.hidden = false;
  });

  // Only platform admins and editors without an existing non-default managed
  // garden should be able to create a new garden from the shell UI.
  getGardenCreateButtons().forEach((createBtn) => {
    createBtn.hidden = !canCurrentUserCreateGarden();
  });

  gardenContextAvailable = !gardensFetchFailed;
  applyWriteAccessUi();
  updateAuthButton();
  updateShadeMapAvailabilityUi();
  applyLocalizedShellText();
  syncAdminTabLabels();
  updateMobileHeader();
}

function updateShadeMapAvailabilityUi(): void {
  const available = authProfile?.shademap_available ?? false;
  const shadePanelEl = document.getElementById("shade-panel");
  if (shadePanelEl) shadePanelEl.hidden = !available;
  // Re-fit camera after layout change (shade panel shown/hidden affects available width)
  requestAnimationFrame(() => cameraCtrl?.fitAll());
}

/** Hide/show all tier-gated UI elements based on the current feature set. */
function applyFeatureGateUi(): void {
  const visibleTabs: Record<AppTab, boolean> = {
    map: true,
    garden: isTabEnabled("garden"),
    activity: isTabEnabled("activity"),
    insights: isTabEnabled("insights"),
    admin: isTabEnabled("admin"),
  };

  (["garden", "activity", "insights", "admin"] as const).forEach((tab) => {
    const topBtn = document.getElementById(`top-tab-${tab}`);
    if (topBtn) topBtn.hidden = !visibleTabs[tab];

    const mobileBtn = document.getElementById(`mobile-tab-${tab}`);
    if (mobileBtn) mobileBtn.hidden = !visibleTabs[tab];
  });

  (Object.keys(SUB_MODE_META) as SubMode[]).forEach((mode) => {
    document.querySelectorAll<HTMLElement>(`[data-sub-mode="${mode}"]`).forEach((btn) => {
      btn.hidden = !isSubModeEnabled(mode);
    });
  });

  const mobileAdminBtn = document.getElementById("mobile-admin-btn");
  if (mobileAdminBtn) mobileAdminBtn.hidden = !isFeatureEnabled("admin_panel");

  const mobileNotifBtn = document.getElementById("mobile-notification-btn");
  if (mobileNotifBtn) mobileNotifBtn.hidden = !isFeatureEnabled("notifications");

  const bellWrapper = document.querySelector(".notification-bell-wrapper") as HTMLElement | null;
  if (bellWrapper) bellWrapper.hidden = !isFeatureEnabled("notifications");

  ensureGatedFeatureInitializers();

  applyNavigationState({ triggerLoads: false });
}

async function switchGarden(nextGardenId: number): Promise<void> {
  setActiveGardenContext(nextGardenId);
  await refreshGardenContext();
  refreshDataAfterAuthChange();
}

async function refreshGardenDataForCurrentContext(): Promise<void> {
  const requestGardenId = getActiveGardenContext();
  clearAppStatus();
  clearFocusedPlantIds();
  clearPlantSelection();
  resetStatisticsState();
  clearJournalMediaPreviewCache();
  state.highlightedPlotIds.clear();
  if (isAdminMfaSetupRequired()) {
    setActiveTab("admin");
    showAdminMfaSetupStatus();
    return;
  }
  await Promise.all([fetchPlots(), fetchLayoutState()]);
  if (!isCurrentGardenRequest(requestGardenId)) return;
  invalidatePlantsCache();
  if (activeTab === "map") await ensureShadeMapPanelLoaded();
  if (!isCurrentGardenRequest(requestGardenId)) return;
  await refreshActiveNavigationContent();
}

function refreshDataAfterAuthChange(): void {
  void refreshGardenDataForCurrentContext();
}

async function handleAuthButton(): Promise<void> {
  if (authProfile || hasStoredAuthToken()) {
    // Already signed in — sign out and redirect to login
    try {
      await logoutApi();
    } catch {
      // clear local state even if backend token is already invalid
    }
    await clearOfflineQueue().catch(() => undefined);
    adminPanelModule?.resetAdminPanelSensitiveState();
    clearStoredAuthToken();
    authProfile = null;
    setActiveGardenContext(null);
    await showAuthGate(false, false);
    await refreshGardenContext();
    refreshDataAfterAuthChange();
  } else {
    // Not signed in — use the login gate
    try {
      const status = await getAuthStatusApi();
      await showAuthGate(status.bootstrap_required, status.passkeys_enabled);
    } catch {
      await showAuthGate(false, false);
    }
    await refreshGardenContext();
    refreshDataAfterAuthChange();
  }
}

function showFetchError(err: unknown): void {
  const message = getApiErrorMessage(err);
  if (isAuthApiError(err)) {
    showAppStatus(message, t("status.sign_in"), () => {
      void handleAuthButton();
    });
  } else {
    showAppStatus(message);
  }
  showToast(message, "error");
}

function syncMobileCapabilities(): void {
  const editBtn = queryButton("edit-mode-btn");
  const editMenuDropdown = document.getElementById("edit-menu-dropdown") as HTMLElement | null;
  if (!editBtn) return;
  if (isMobile()) {
    if (state.editMode) toggleEditMode(state, editCbs);
    if (editMenuDropdown) editMenuDropdown.hidden = true;
    editBtn.setAttribute("aria-expanded", "false");
    editBtn.disabled = true;
    editBtn.title = t("map.desktop_only");
    editBtn.textContent = t("map.edit_desktop");
  } else {
    editBtn.disabled = !canWriteInGarden;
    editBtn.title = canWriteInGarden ? "" : t("map.read_only");
    editBtn.textContent = canWriteInGarden ? t("map.edit") : t("map.edit_read_only");
  }
  updateMapDirectionControlVisibility();
}

// ── Global keyboard shortcuts ──────────────────────────────
function isInInput(): boolean {
  const el = document.activeElement;
  return (
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement
  );
}

window.addEventListener("keydown", (e) => {
  const mobileUtilitySheet = document.getElementById("mobile-utility-sheet");
  if (e.key === "Escape" && mobileUtilitySheet?.classList.contains("mobile-utility-sheet--open")) {
    setMobileUtilityOpen(false);
    return;
  }
  if (e.key === "Escape" && isQuickActionSheetOpen()) {
    closeQuickActionSheet();
    return;
  }
  const mobileMapSheetOpen = MOBILE_MAP_SHEET_IDS.some((id) => {
    const sheet = document.getElementById(id);
    return sheet?.classList.contains("mobile-map-sheet--open") ?? false;
  });
  if (e.key === "Escape" && mobileMapSheetOpen) {
    setMobileMapSheetOpen(null);
    return;
  }
  const mobileShadeDisclosureOpen = getTopLevelShadeDisclosures().some((details) => details.open);
  if (e.key === "Escape" && mobileShadeDisclosureOpen) {
    closeMobileShadeDisclosures();
    return;
  }
  if (isInInput()) return;
  if (state.editMode && e.key === "Escape") clearSelection(state, editCbs);
  if (state.editMode && (e.ctrlKey || e.metaKey) && e.key === "a") {
    e.preventDefault();
    selectAll(state, editCbs);
  }
  if (state.editMode && (e.ctrlKey || e.metaKey) && e.key === "z") {
    e.preventDefault();
    void undo(state, editCbs);
  }
});

document.addEventListener("focusin", (e) => {
  maybeCenterFocusedMobileField(e.target);
});

window.addEventListener("resize", () => {
  cameraCtrl?.fitAll();
  shadePanel?.invalidateSize();
  syncMobileCapabilities();
  if (!isMobile()) {
    setMobileUtilityOpen(false);
    setMobileMapSheetOpen(null);
    closeMobileShadeDisclosures();
  }
  syncMobileShadeDisclosureState();
  syncMobileViewportOffset();
});

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncMobileViewportOffset);
  window.visualViewport.addEventListener("scroll", syncMobileViewportOffset);
}

// ── Bootstrap ──────────────────────────────────────────────

let _authExpiredPending = false;

function handleAuthExpired(): void {
  if (_authExpiredPending) return;
  _authExpiredPending = true;
  adminPanelModule?.resetAdminPanelSensitiveState();
  clearStoredAuthToken();
  showAppStatus(t("status.session_expired"), t("status.sign_in"), () => {
    _authExpiredPending = false;
    void handleAuthButton();
  });
}

async function bootstrapApp(): Promise<void> {
  primeInviteTokenFromLocation();
  initErrorReporter();
  setOnAuthExpired(handleAuthExpired);

  // Always check authentication before showing any UI.
  // If /api/auth/me fails for any reason, show the login gate.
  let bootstrapRequired = false;
  let passkeysEnabled = false;
  let initialMe: AuthUserProfile | null = null;
  try {
    initialMe = await getAuthMeApi();
    setFeatureGates(initialMe.subscription_tier ?? "home", initialMe.allowed_features ?? []);
    clearPrimedInviteToken();
    if (initialMe.language && initialMe.language !== getLocale()) {
      setLocale(initialMe.language);
    }
    if (initialMe.must_change_password) {
      await showForcedPasswordChangeGate(initialMe.username);
      initialMe = await getAuthMeApi();
      setFeatureGates(initialMe.subscription_tier ?? "home", initialMe.allowed_features ?? []);
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      showSecurityWarningBanner(err.message);
    }
    try {
      const status = await getAuthStatusApi();
      bootstrapRequired = status.bootstrap_required;
      passkeysEnabled = status.passkeys_enabled;
    } catch {
      // can't reach status either — gate will show the real error on submit
    }
    await showAuthGate(bootstrapRequired, passkeysEnabled);
  }

  setupLayout();
  await initOfflineQueue();
  initOfflineFeature({
    extractPendingMediaFiles,
    withoutPendingMediaFiles,
    uploadTargetMediaFiles,
    uploadJournalMediaFiles,
  });
  await refreshGardenContext();
  if (isAdminMfaSetupRequired()) {
    setActiveTab("admin");
    showAdminMfaSetupStatus();
    return;
  }
  setActiveTab(activeTab);

  // If user has no gardens and can create one, auto-create and onboard.
  if (
    gardenContextAvailable
    && gardenOptions.length === 0
    && authProfile
    && canCurrentUserCreateGarden()
  ) {
    try {
      const garden = await createGardenApi(`${authProfile.username}'s garden`);
      setActiveGardenContext(garden.id);
      await refreshGardenContext();
    } catch {
      // Garden creation failed — continue without onboarding
    }
  }

  // Check if active garden needs onboarding.
  const needsOnboarding = await checkOnboardingNeeded();
  if (needsOnboarding) return;

  await Promise.all([fetchPlots(), fetchLayoutState()]);
  if (activeTab === "map") {
    await ensureShadeMapPanelLoaded();
  }
}

async function checkOnboardingNeeded(): Promise<boolean> {
  const activeId = getActiveGardenContext();
  if (activeId === null) return false;
  const activeGarden = gardenOptions.find((g) => g.id === activeId);
  if (!activeGarden || activeGarden.onboarding_complete) return false;
  // Only admins/editors can complete onboarding.
  if (activeGarden.role === "viewer") return false;

  return new Promise((resolve) => {
    const app = document.getElementById("app");
    if (!app) { resolve(false); return; }
    const finishOnboardingFlow = () => {
      // Reload everything after onboarding or admin dismissal.
      void (async () => {
        await refreshGardenContext();
        await Promise.all([fetchPlots(), fetchLayoutState()]);
        if (activeTab === "map") {
          await ensureShadeMapPanelLoaded();
        }
        resolve(true);
      })();
    };
    showOnboarding(app, {
      gardenId: activeId,
      gardenName: activeGarden.name,
      username: authProfile?.username ?? "",
      canDismiss: authProfile?.role === "admin",
      onComplete: finishOnboardingFlow,
      onDismiss: finishOnboardingFlow,
    });
  });
}

void bootstrapApp();
