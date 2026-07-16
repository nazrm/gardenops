import L from "leaflet";
import "leaflet/dist/leaflet.css";

import type { Plot } from "../core/models";
import { getLocaleTag, t } from "../core/i18n";
import type {
  PersistedShadeMapState,
  ShadeMapCalibration,
  ShadeMapFeature,
  ShadeMapConfig,
  ShadeMapMonthlyEstimateValue,
  ShadeMapObstacle,
  ShadeMapObstacleInput,
  ShadeMapMode,
  ShadeMapPreset,
  SunWindow,
} from "../services/api";
import { sanitizeUrl } from "../core/sanitize";
import { queryInput, querySelect, queryButton } from "../core/dom";
import {
  buildShadeMapTerrainUrl,
  createShadeMapObstacleApi,
  deleteShadeMapObstacleApi,
  getApiErrorMessage,
  getShadeMapConfigApi,
  getShadeMapFeaturesApi,
  getShadeMapMonthlyEstimatedSunApi,
  getSunWindowApi,
  updateShadeMapCalibrationApi,
  updateShadeMapObstacleApi,
} from "../services/api";

type TargetKind = "house" | "plot";

interface GardenContext {
  plots: Plot[];
  selectedPlotId: string | null;
  housePosition: { row: number; col: number };
  houseSize: { width: number; height: number };
  northDegrees: number;
}

interface TargetPoint {
  id: string;
  kind: TargetKind;
  label: string;
  latitude: number;
  longitude: number;
}

interface ComparisonResult {
  monthLabel: string;
  hours: number;
}

interface DaylightWindow {
  sunrise: Date;
  sunset: Date;
  allDaylight: boolean;
}

interface TerrainTileFetchResult {
  status: number | null;
  objectUrl: string | null;
}

interface GridCalibrationTransform {
  referenceLatitude: number;
  referenceLongitude: number;
  translationEastM: number;
  translationNorthM: number;
  scaleMeters: number;
  cosTheta: number;
  sinTheta: number;
}

interface HouseCornerLatLng {
  latitude: number;
  longitude: number;
}

interface CalibrationCornerQa {
  label: string;
  errorM: number;
  fittedCorner: HouseCornerLatLng;
  actualCorner: HouseCornerLatLng;
}

interface CalibrationQa {
  rmsErrorM: number;
  maxErrorM: number;
  fittedCorners: HouseCornerLatLng[];
  actualCorners: HouseCornerLatLng[];
  cornerErrors: CalibrationCornerQa[];
}

interface ShadeRuntime {
  _gl?: WebGLRenderingContext | WebGL2RenderingContext;
  _canvas?: HTMLCanvasElement | null;
  _canvasOverlay?: { remove?: () => void } | null;
  _simulationUnavailable?: boolean;
  onRemove?: () => void;
  removeAllListeners?: () => void;
}

interface ShadeMapOptions {
  apiKey: string;
  date?: Date;
  color?: string;
  opacity?: number;
  belowCanopy?: boolean;
  terrainSource?: {
    maxZoom: number;
    tileSize: number;
    getSourceUrl: (params: { x: number; y: number; z: number }) => string;
    getElevation: (params: { r: number; g: number; b: number; a: number }) => number;
  };
  getFeatures?: () => Promise<Record<string, unknown>[]>;
  getSize?: () => { width: number; height: number };
  debug?: (message: string) => void;
}

class ShadeMap {
  readonly _simulationUnavailable = true;
  private idleHandlers = new Set<() => void>();
  private idleTimer: number | null = null;
  private map: L.Map | null = null;
  private date: Date;

  constructor(private readonly options: ShadeMapOptions) {
    this.date = options.date ?? new Date();
  }

  addTo(map: L.Map): this {
    this.map = map;
    this.options.debug?.("Bundled build does not include the external shadow simulator.");
    this.scheduleIdle();
    return this;
  }

  on(event: "idle", handler: () => void): () => void {
    if (event !== "idle") return () => undefined;
    this.idleHandlers.add(handler);
    this.scheduleIdle();
    return () => this.removeListener(event, handler);
  }

  once(event: "idle", handler: () => void): () => void {
    const wrapped = () => {
      this.removeListener(event, wrapped);
      handler();
    };
    return this.on(event, wrapped);
  }

  removeListener(event: "idle", handler: () => void): void {
    if (event === "idle") {
      this.idleHandlers.delete(handler);
    }
  }

  removeAllListeners(): void {
    this.idleHandlers.clear();
  }

  setDate(date: Date): this {
    this.date = date;
    this.scheduleIdle();
    return this;
  }

  async setSunExposure(_enabled: boolean): Promise<this> {
    this.scheduleIdle();
    return this;
  }

  async isPositionInSun(_x: number, _y: number): Promise<boolean> {
    return false;
  }

  async isPositionInShade(_x: number, _y: number): Promise<boolean> {
    return false;
  }

  async getHoursOfSun(_x: number, _y: number): Promise<number> {
    return 0;
  }

  flushSync(): void {
    this.scheduleIdle();
  }

  onRemove(): this {
    this.map = null;
    if (this.idleTimer != null) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    this.idleHandlers.clear();
    return this;
  }

  private scheduleIdle(): void {
    if (!this.map || this.idleTimer != null) return;
    this.idleTimer = window.setTimeout(() => {
      this.idleTimer = null;
      for (const handler of this.idleHandlers) {
        handler();
      }
    }, 0);
  }
}

/** The production bundle has a non-rendering fallback when no licensed runtime is configured. */
function createShadeMapRuntime(options: ShadeMapOptions): ShadeMap {
  const RuntimeShadeMap = externalShadeMapRuntime();
  if (RuntimeShadeMap) {
    return new RuntimeShadeMap(options);
  }
  return new ShadeMap(options);
}

let shadeMapRuntimeScriptUrl: string | null = null;
let shadeMapRuntimeScriptPromise: Promise<void> | null = null;
let shadeMapRuntimeTrustedTypesPolicy: {
  createScriptURL: (url: string) => unknown;
} | null = null;

function externalShadeMapRuntime(): (new (options: ShadeMapOptions) => ShadeMap) | null {
  const candidate = (globalThis as typeof globalThis & { GardenOpsShadeMap?: unknown })
    .GardenOpsShadeMap;
  return typeof candidate === "function"
    ? candidate as new (options: ShadeMapOptions) => ShadeMap
    : null;
}

function trustedShadeMapRuntimeScriptUrl(runtimeScriptUrl: string): string {
  const trustedTypes = (globalThis as typeof globalThis & {
    trustedTypes?: {
      createPolicy: (
        name: string,
        rules: { createScriptURL: (url: string) => string },
      ) => { createScriptURL: (url: string) => unknown };
    };
  }).trustedTypes;
  if (!trustedTypes) return runtimeScriptUrl;
  if (!shadeMapRuntimeTrustedTypesPolicy) {
    shadeMapRuntimeTrustedTypesPolicy = trustedTypes.createPolicy("gardenops-html", {
      createScriptURL: (url: string) => {
        if (url !== "/shademap/runtime.js") {
          throw new TypeError("ShadeMap runtime URL is not a GardenOps asset path");
        }
        return url;
      },
    });
  }
  return shadeMapRuntimeTrustedTypesPolicy.createScriptURL(runtimeScriptUrl) as string;
}

async function loadShadeMapRuntime(runtimeScriptUrl: string | null): Promise<void> {
  if (externalShadeMapRuntime() || !runtimeScriptUrl) return;
  if (runtimeScriptUrl !== "/shademap/runtime.js") {
    throw new Error("ShadeMap runtime URL is not a GardenOps asset path");
  }
  if (shadeMapRuntimeScriptPromise && shadeMapRuntimeScriptUrl === runtimeScriptUrl) {
    return shadeMapRuntimeScriptPromise;
  }

  shadeMapRuntimeScriptUrl = runtimeScriptUrl;
  shadeMapRuntimeScriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.async = true;
    script.src = trustedShadeMapRuntimeScriptUrl(runtimeScriptUrl);
    script.onload = () => {
      if (externalShadeMapRuntime()) {
        resolve();
      } else {
        reject(new Error("ShadeMap runtime did not register GardenOpsShadeMap"));
      }
    };
    script.onerror = () => reject(new Error("ShadeMap runtime could not be loaded"));
    document.head.append(script);
  }).catch((error: unknown) => {
    shadeMapRuntimeScriptPromise = null;
    shadeMapRuntimeScriptUrl = null;
    throw error;
  });
  return shadeMapRuntimeScriptPromise;
}

const HOUSE_TARGET_ID = "house";
const SVG_NS = "http://www.w3.org/2000/svg";
const EARTH_RADIUS_METERS = 6378137;
const SHADE_PRESETS: ShadeMapPreset[] = ["now", "custom", "spring", "summer", "autumn", "winter"];
const HOUSE_CORNER_LABELS = ["NW", "NE", "SE", "SW"] as const;
const SHADEMAP_DEBUG_NOISE = new Set(["_draw()", "_reset()"]);
const SHADE_READY_FALLBACK_MS = 900;
const TERRAIN_TILE_PATH_RE = /^\/shademap\/terrain\/(\d+)\/(\d+)\/(\d+)\.png$/;
const PLOT_SAMPLE_OFFSETS: ReadonlyArray<{ x: number; y: number }> = [
  { x: 0, y: 0 },
  { x: -0.34, y: -0.34 },
  { x: 0.34, y: -0.34 },
  { x: -0.34, y: 0.34 },
  { x: 0.34, y: 0.34 },
  { x: 0, y: -0.38 },
  { x: 0, y: 0.38 },
  { x: -0.38, y: 0 },
  { x: 0.38, y: 0 },
];
const CENTER_SAMPLE_WEIGHT = 2;
const SUN_CLASSIFY_MIN_PERCENT = 60;
const SUNLIGHT_SNAPSHOT_DEBOUNCE_MS = 120;
const DEFAULT_BASEMAP_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const BASEMAP_TILE_URL = import.meta.env["VITE_SHADEMAP_BASEMAP_URL"]?.trim()
  || DEFAULT_BASEMAP_TILE_URL;
const TARGET_MARKER_STYLE = {
  radius: 7,
  weight: 3,
  color: "#ffffff",
  fillColor: "#1a7a3a",
  fillOpacity: 0.95,
};
const terrainImageRequestIds = new WeakMap<HTMLImageElement, number>();
const terrainImageBlobUrls = new WeakMap<HTMLImageElement, string>();

let terrainImageRecoverer: ((url: string) => Promise<string | null>) | null = null;
let terrainImageRecoveryInstalled = false;
let terrainImageRequestSeq = 0;

function parseTerrainTileCoords(rawUrl: string): { z: number; x: number; y: number } | null {
  try {
    const url = new URL(rawUrl, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    const match = url.pathname.match(TERRAIN_TILE_PATH_RE);
    if (!match) return null;
    const [, z, x, y] = match;
    return {
      z: Number.parseInt(z ?? "", 10),
      x: Number.parseInt(x ?? "", 10),
      y: Number.parseInt(y ?? "", 10),
    };
  } catch {
    return null;
  }
}

function isTerrainTileUrl(rawUrl: string): boolean {
  return parseTerrainTileCoords(rawUrl) !== null;
}

function revokeTerrainImageBlobUrl(image: HTMLImageElement): void {
  const blobUrl = terrainImageBlobUrls.get(image);
  if (!blobUrl) return;
  terrainImageBlobUrls.delete(image);
  URL.revokeObjectURL(blobUrl);
}

function installTerrainImageRecovery(): void {
  if (terrainImageRecoveryInstalled || typeof window === "undefined") return;
  const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
  if (!descriptor?.get || !descriptor.set) return;

  Object.defineProperty(HTMLImageElement.prototype, "src", {
    configurable: true,
    enumerable: descriptor.enumerable ?? true,
    get(this: HTMLImageElement): string {
      return descriptor.get?.call(this) as string;
    },
    set(this: HTMLImageElement, value: string): void {
      const rawValue = String(value ?? "");
      const recoverer = terrainImageRecoverer;
      if (!recoverer || !isTerrainTileUrl(rawValue)) {
        revokeTerrainImageBlobUrl(this);
        descriptor.set?.call(this, rawValue);
        return;
      }

      terrainImageRequestSeq += 1;
      const requestId = terrainImageRequestSeq;
      terrainImageRequestIds.set(this, requestId);

      void recoverer(rawValue).then((resolvedUrl) => {
        if (terrainImageRequestIds.get(this) !== requestId) return;
        revokeTerrainImageBlobUrl(this);

        if (resolvedUrl?.startsWith("blob:")) {
          terrainImageBlobUrls.set(this, resolvedUrl);
          const cleanup = () => {
            if (terrainImageBlobUrls.get(this) === resolvedUrl) {
              revokeTerrainImageBlobUrl(this);
            }
          };
          this.addEventListener("load", cleanup, { once: true });
          this.addEventListener("error", cleanup, { once: true });
        }

        descriptor.set?.call(this, resolvedUrl || rawValue);
      }).catch(() => {
        if (terrainImageRequestIds.get(this) !== requestId) return;
        revokeTerrainImageBlobUrl(this);
        descriptor.set?.call(this, rawValue);
      });
    },
  });

  terrainImageRecoveryInstalled = true;
}

function setTerrainImageRecoverer(
  recoverer: ((url: string) => Promise<string | null>) | null,
): void {
  installTerrainImageRecovery();
  terrainImageRecoverer = recoverer;
}

function createSvgNode<K extends keyof SVGElementTagNameMap>(
  tag: K,
): SVGElementTagNameMap[K] {
  return document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
}

function defaultCalibration(): ShadeMapCalibration {
  return {
    enabled: false,
    calibration_type: "house-corners",
    origin_grid_col: null,
    origin_grid_row: null,
    origin_latitude: null,
    origin_longitude: null,
    axis_grid_col: null,
    axis_grid_row: null,
    axis_latitude: null,
    axis_longitude: null,
    house_nw_latitude: null,
    house_nw_longitude: null,
    house_ne_latitude: null,
    house_ne_longitude: null,
    house_se_latitude: null,
    house_se_longitude: null,
    house_sw_latitude: null,
    house_sw_longitude: null,
  };
}

function defaultObstacleInput(lat: number, lng: number): ShadeMapObstacleInput {
  return {
    label: "",
    kind: "tree",
    linked_plot_id: null,
    latitude: lat,
    longitude: lng,
    height_m: 4.5,
    crown_radius_m: 2,
    active: true,
  };
}

function decodeTerrariumElevation({ r, g, b }: { r: number; g: number; b: number; a: number }): number {
  return (r * 256 + g + b / 256) - 32768;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function formatDateInputValue(value: Date): string {
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

function formatTimeInputValue(value: Date): string {
  return `${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

function formatDisplayDate(value: Date): string {
  return new Intl.DateTimeFormat(getLocaleTag(), {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(value);
}

function formatMonthLabel(monthNumber: number): string {
  const date = new Date(2024, Math.max(0, Math.min(11, monthNumber - 1)), 1);
  return new Intl.DateTimeFormat(getLocaleTag(), { month: "short" }).format(date);
}

function parseLocalDateTime(dateValue: string, timeValue: string): Date | null {
  const dateParts = dateValue.split("-").map((part) => Number.parseInt(part, 10));
  const timeParts = timeValue.split(":").map((part) => Number.parseInt(part, 10));
  if (dateParts.length !== 3 || timeParts.length !== 2) return null;
  const [year, month, day] = dateParts;
  const [hours, minutes] = timeParts;
  if (!year || !month || !day || hours == null || minutes == null) return null;
  const parsed = new Date(year, month - 1, day, hours, minutes, 0, 0);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate(), 0, 0, 0, 0);
}

function endOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate(), 23, 59, 59, 999);
}

function clampMinutes(minutes: number): number {
  return Math.max(0, Math.min(23 * 60 + 59, minutes));
}

function minutesOfDay(value: Date): number {
  return value.getHours() * 60 + value.getMinutes();
}

function dateWithMinutes(value: Date, minutes: number): Date {
  const clamped = clampMinutes(minutes);
  const hours = Math.floor(clamped / 60);
  const remainder = clamped % 60;
  return new Date(value.getFullYear(), value.getMonth(), value.getDate(), hours, remainder, 0, 0);
}

function dayOfYear(value: Date): number {
  const start = new Date(value.getFullYear(), 0, 0);
  const diffMs = value.getTime() - start.getTime();
  return Math.max(1, Math.floor(diffMs / 86400000));
}

function computeDaylightWindow(date: Date, latitude: number, longitude: number): DaylightWindow | null {
  const gamma = (2 * Math.PI / 365) * (dayOfYear(date) - 1);
  const equationOfTime = 229.18 * (
    0.000075
    + 0.001868 * Math.cos(gamma)
    - 0.032077 * Math.sin(gamma)
    - 0.014615 * Math.cos(2 * gamma)
    - 0.040849 * Math.sin(2 * gamma)
  );
  const solarDeclination = (
    0.006918
    - 0.399912 * Math.cos(gamma)
    + 0.070257 * Math.sin(gamma)
    - 0.006758 * Math.cos(2 * gamma)
    + 0.000907 * Math.sin(2 * gamma)
    - 0.002697 * Math.cos(3 * gamma)
    + 0.00148 * Math.sin(3 * gamma)
  );
  const latitudeRad = (latitude * Math.PI) / 180;
  const solarZenith = (90.833 * Math.PI) / 180;
  const cosHourAngle = (
    Math.cos(solarZenith) / (Math.cos(latitudeRad) * Math.cos(solarDeclination))
    - Math.tan(latitudeRad) * Math.tan(solarDeclination)
  );
  if (cosHourAngle >= 1) return null;
  if (cosHourAngle <= -1) {
    return {
      sunrise: startOfDay(date),
      sunset: dateWithMinutes(date, 23 * 60 + 59),
      allDaylight: true,
    };
  }

  const hourAngleMinutes = (Math.acos(cosHourAngle) * 180 / Math.PI) * 4;
  const solarNoonMinutes = 720 - (4 * longitude) - equationOfTime - date.getTimezoneOffset();
  return {
    sunrise: dateWithMinutes(date, Math.round(solarNoonMinutes - hourAngleMinutes)),
    sunset: dateWithMinutes(date, Math.round(solarNoonMinutes + hourAngleMinutes)),
    allDaylight: false,
  };
}

function fitSimilarityTransform(
  gridPoints: Array<{ x: number; y: number }>,
  worldPoints: Array<{ x: number; y: number }>,
  referenceLatitude: number,
  referenceLongitude: number,
): GridCalibrationTransform | null {
  if (gridPoints.length !== worldPoints.length || gridPoints.length < 2) return null;
  const gridMean = gridPoints.reduce(
    (acc, point) => ({ x: acc.x + point.x, y: acc.y + point.y }),
    { x: 0, y: 0 },
  );
  gridMean.x /= gridPoints.length;
  gridMean.y /= gridPoints.length;
  const worldMean = worldPoints.reduce(
    (acc, point) => ({ x: acc.x + point.x, y: acc.y + point.y }),
    { x: 0, y: 0 },
  );
  worldMean.x /= worldPoints.length;
  worldMean.y /= worldPoints.length;

  const centeredGrid = gridPoints.map((point) => ({ x: point.x - gridMean.x, y: point.y - gridMean.y }));
  const centeredWorld = worldPoints.map((point) => ({ x: point.x - worldMean.x, y: point.y - worldMean.y }));
  const gridVariance = centeredGrid.reduce((sum, point) => sum + point.x * point.x + point.y * point.y, 0) / gridPoints.length;
  if (gridVariance <= 1e-9) return null;

  let c00 = 0;
  let c01 = 0;
  let c10 = 0;
  let c11 = 0;
  for (let index = 0; index < gridPoints.length; index += 1) {
    const g = centeredGrid[index];
    const w = centeredWorld[index];
    if (!g || !w) return null;
    c00 += w.x * g.x;
    c01 += w.x * g.y;
    c10 += w.y * g.x;
    c11 += w.y * g.y;
  }
  c00 /= gridPoints.length;
  c01 /= gridPoints.length;
  c10 /= gridPoints.length;
  c11 /= gridPoints.length;

  // Closed-form 2D similarity fit; equivalent to least-squares Procrustes.
  const a = c00 + c11;
  const b = c10 - c01;
  const norm = Math.hypot(a, b);
  if (norm <= 1e-9) return null;
  const cosTheta = a / norm;
  const sinTheta = b / norm;
  const scaleMeters = norm / gridVariance;
  const translationEastM = worldMean.x - scaleMeters * ((cosTheta * gridMean.x) - (sinTheta * gridMean.y));
  const translationNorthM = worldMean.y - scaleMeters * ((sinTheta * gridMean.x) + (cosTheta * gridMean.y));

  return {
    referenceLatitude,
    referenceLongitude,
    translationEastM,
    translationNorthM,
    scaleMeters,
    cosTheta,
    sinTheta,
  };
}

function parseNumberInput(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function metersPerDegreeLatitude(): number {
  return EARTH_RADIUS_METERS * Math.PI / 180;
}

function metersPerDegreeLongitude(latitude: number): number {
  return metersPerDegreeLatitude() * Math.cos((latitude * Math.PI) / 180);
}

function latLngToLocalEastNorth(
  latitude: number,
  longitude: number,
  referenceLatitude: number,
  referenceLongitude: number,
): { east: number; north: number } {
  return {
    east: (longitude - referenceLongitude) * metersPerDegreeLongitude(referenceLatitude),
    north: (latitude - referenceLatitude) * metersPerDegreeLatitude(),
  };
}

function plotTargetId(plotId: string): string {
  return `plot:${plotId}`;
}

function seasonalPreset(preset: string, year: number): Date {
  switch (preset) {
    case "spring":
      return new Date(year, 2, 20, 12, 0, 0, 0);
    case "summer":
      return new Date(year, 5, 21, 12, 0, 0, 0);
    case "autumn":
      return new Date(year, 8, 22, 12, 0, 0, 0);
    case "winter":
      return new Date(year, 11, 21, 12, 0, 0, 0);
    default:
      return new Date();
  }
}

function isShadePreset(value: string): value is ShadeMapPreset {
  return SHADE_PRESETS.includes(value as ShadeMapPreset);
}

export class ShadePanelController {
  private readonly onStateChanged: ((state: PersistedShadeMapState) => void) | null = null;

  private readonly onSunlightSnapshot: ((plotIds: string[]) => void) | null = null;

  private map: L.Map | null = null;

  private houseMarker: L.CircleMarker | null = null;

  private targetMarker: L.CircleMarker | null = null;

  private shadeLayer: ShadeMap | null = null;

  private currentDate = new Date();

  private config: ShadeMapConfig | null = null;

  private terrainConfigRefreshPromise: Promise<ShadeMapConfig> | null = null;

  private garden: GardenContext | null = null;

  private calibration: ShadeMapCalibration = defaultCalibration();

  private obstacles: ShadeMapObstacle[] = [];

  private activeObstacleId: number | null = null;

  private activeTargetId = HOUSE_TARGET_ID;

  private activePreset: ShadeMapPreset = "now";

  private activeMode: ShadeMapMode = "shadow";

  private controlsWired = false;

  private estimatedComparisonLoaded = false;

  private estimatedComparisonLoading = false;

  private estimatedComparisonResults: ComparisonResult[] = [];

  private estimatedComparisonMeta: { sourceName: string; sourceDateStart: string; sourceDateEnd: string } | null = null;

  private estimatedComparisonErrorMessage: string | null = null;

  private shadeLayerReady = false;

  private detachIdleHandler: (() => void) | null = null;

  private pendingVisibleLayerBuild = false;

  private activationTimer: number | null = null;

  private sunlightSnapshotSeq = 0;

  private sunlightSnapshotEpoch = 0;

  private sunlightSnapshotTimer: number | null = null;

  private sunWindow: SunWindow | null = null;

  private sunWindowDay: string | null = null;

  private playbackActive = false;

  private playbackStepMinutes = 15;

  private playbackFrameDelayMs = 650;

  private playbackTimer: number | null = null;

  private readyFallbackTimer: number | null = null;

  private readyFallbackSeq = 0;

  private applyingShadeSettings = false;

  private gardenContextEpoch = 0;

  private shadeSettingsEpoch = 0;

  private renderRevision = 0;

  private writeAccessObserver: MutationObserver | null = null;

  private readonly idleHandler = () => {
    if (this.applyingShadeSettings) return;
    if (this.tryRefreshReadyLayer()) return;
    this.scheduleReadyFallback();
  };

  constructor(options?: {
    onStateChanged?: (state: PersistedShadeMapState) => void;
    onSunlightSnapshot?: (plotIds: string[]) => void;
  }) {
    this.onStateChanged = options?.onStateChanged ?? null;
    this.onSunlightSnapshot = options?.onSunlightSnapshot ?? null;
  }

  init(): void {
    installTerrainImageRecovery();
    this.ensureModeControl();
    this.wireControls();
    this.syncInputs();
    this.syncPlaybackControls();
    this.setSummary(t("shade.loading_config"));
    this.setDebugMessage("");
    this.renderComparisonStatus(t("shade.comparison_loading"));
    this.renderComparison([]);
    this.writeAccessObserver = new MutationObserver(() => this.syncWriteAccess());
    this.writeAccessObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    this.syncWriteAccess();
  }

  async load(
    config: ShadeMapConfig,
    persistedState?: PersistedShadeMapState,
    calibration?: ShadeMapCalibration,
    obstacles?: ShadeMapObstacle[],
  ): Promise<void> {
    await loadShadeMapRuntime(config.runtime_script_url);
    this.config = config;
    this.publishRenderState("loading");
    setTerrainImageRecoverer((url) => this.resolveTerrainImageUrl(url));
    if (persistedState) {
      this.applyPersistedState(persistedState);
    }
    this.calibration = calibration ? { ...calibration } : defaultCalibration();
    this.obstacles = obstacles ? [...obstacles] : [];
    this.activeObstacleId = this.obstacles[0]?.id ?? null;
    this.writeStaticMeta();
    this.syncInputs();

    this.syncPresetSelect();
    this.syncTargetOptions();
    this.renderCalibrationEditor();
    this.renderObstacleEditor();
    this.syncWriteAccess();
    this.updateTargetMarker();
    void this.loadEstimatedComparison();
    this.pendingVisibleLayerBuild = true;
    this.scheduleActivation();
  }

  private async fetchTerrainTileImage(url: string): Promise<TerrainTileFetchResult> {
    try {
      const response = await fetch(url, { credentials: "same-origin" });
      if (!response.ok) {
        return { status: response.status, objectUrl: null };
      }
      return {
        status: response.status,
        objectUrl: URL.createObjectURL(await response.blob()),
      };
    } catch {
      return { status: null, objectUrl: null };
    }
  }

  private async refreshTerrainConfig(): Promise<ShadeMapConfig> {
    if (this.terrainConfigRefreshPromise) {
      return this.terrainConfigRefreshPromise;
    }

    const contextEpoch = this.gardenContextEpoch;
    const refreshPromise = getShadeMapConfigApi()
      .then((refreshed) => {
        if (contextEpoch !== this.gardenContextEpoch) {
          throw new Error("Stale ShadeMap terrain config response rejected");
        }
        if (this.config) {
          Object.assign(this.config, refreshed);
          return this.config;
        }
        this.config = refreshed;
        return refreshed;
      })
      .finally(() => {
        if (this.terrainConfigRefreshPromise === refreshPromise) {
          this.terrainConfigRefreshPromise = null;
        }
      });

    this.terrainConfigRefreshPromise = refreshPromise;
    return refreshPromise;
  }

  private async resolveTerrainImageUrl(url: string): Promise<string | null> {
    const initial = await this.fetchTerrainTileImage(url);
    if (initial.objectUrl) {
      return initial.objectUrl;
    }
    if (initial.status !== 401 && initial.status !== 403) {
      return null;
    }

    const coords = parseTerrainTileCoords(url);
    if (!coords) {
      return null;
    }

    try {
      const refreshedConfig = await this.refreshTerrainConfig();
      const retryUrl = buildShadeMapTerrainUrl(refreshedConfig.terrain_url_template, coords);
      const retry = await this.fetchTerrainTileImage(retryUrl);
      if (retry.objectUrl) {
        this.setDebugMessage("");
        return retry.objectUrl;
      }
      this.setDebugMessage("Terrain tile token refresh failed.");
      return null;
    } catch (err) {
      const message = getApiErrorMessage(err);
      this.setDebugMessage(`Terrain tile refresh failed: ${message}`);
      return null;
    }
  }

  activate(): void {
    void this.loadEstimatedComparison();
    this.scheduleActivation();
  }

  setGardenContext(context: GardenContext): void {
    this.gardenContextEpoch += 1;
    this.terrainConfigRefreshPromise = null;
    this.cancelSunlightSnapshotSchedule(false);
    this.garden = {
      plots: [...context.plots],
      selectedPlotId: context.selectedPlotId,
      housePosition: { ...context.housePosition },
      houseSize: { ...context.houseSize },
      northDegrees: context.northDegrees,
    };
    if (context.selectedPlotId) {
      this.activeTargetId = plotTargetId(context.selectedPlotId);
    } else if (!this.findTargetById(this.activeTargetId)) {
      this.activeTargetId = HOUSE_TARGET_ID;
      this.emitStateChange();
    }
    this.syncTargetOptions();
    this.renderCalibrationEditor();
    this.renderObstacleEditor();
    this.updateTargetMarker();
    void this.refreshStatus();
  }

  setSelectedPlot(plotId: string | null): void {
    this.cancelSunlightSnapshotSchedule(false);
    if (plotId) {
      this.activeTargetId = plotTargetId(plotId);
    } else if (this.activeTargetId !== HOUSE_TARGET_ID) {
      this.activeTargetId = HOUSE_TARGET_ID;
    }
    this.syncTargetOptions();
    this.updateTargetMarker();
    this.emitStateChange();
    void this.refreshStatus();
  }

  showError(message: string): void {
    this.clearReadyFallback();
    this.applyingShadeSettings = false;
    this.stopPlayback();
    this.setSummary(message);
    this.setDebugMessage(message);
    this.cancelSunlightSnapshotSchedule(true);
    if (!this.estimatedComparisonLoaded && !this.estimatedComparisonLoading) {
      this.renderComparisonStatus(t("shade.comparison_unavailable_short"));
    }
    this.publishRenderState("error", message);
  }

  invalidateSize(): void {
    if (!this.isMapRenderable()) return;
    window.setTimeout(() => {
      if (!this.isMapRenderable()) return;
      this.map?.invalidateSize(false);
    }, 30);
  }

  private mountMap(config: ShadeMapConfig): void {
    const mapEl = this.getMapElement();
    if (!mapEl) return;
    const houseCenter = this.getHouseTargetCoordinates();

    if (!this.map) {
      this.map = L.map(mapEl, {
        zoomControl: true,
        attributionControl: true,
      });
      L.tileLayer(BASEMAP_TILE_URL, {
        maxZoom: 20,
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(this.map);

      this.houseMarker = L.circleMarker([houseCenter.latitude, houseCenter.longitude], {
        radius: 8,
        weight: 3,
        color: "#ffffff",
        fillColor: "#d35322",
        fillOpacity: 0.95,
      }).addTo(this.map);
      const houseTooltipLabel = document.createElement("span");
      houseTooltipLabel.textContent = t("shade.house_center", { label: config.label });
      this.houseMarker.bindTooltip(houseTooltipLabel, {
        direction: "top",
        offset: [0, -6],
      });

      this.targetMarker = L.circleMarker([houseCenter.latitude, houseCenter.longitude], TARGET_MARKER_STYLE).addTo(this.map);

    } else {
      this.houseMarker?.setLatLng([houseCenter.latitude, houseCenter.longitude]);
    }

    this.map.setView([houseCenter.latitude, houseCenter.longitude], config.zoom);
  }

  private scheduleActivation(): void {
    if (this.activationTimer != null) {
      window.clearTimeout(this.activationTimer);
    }
    this.activationTimer = window.setTimeout(() => {
      this.activationTimer = null;
      void this.activateVisibleMap();
    }, 80);
  }

  private async activateVisibleMap(): Promise<void> {
    const config = this.config;
    if (!config || !this.isMapRenderable()) return;

    this.mountMap(config);
    await new Promise<void>((resolve) => {
      if (!this.map) {
        resolve();
        return;
      }
      this.map.whenReady(() => resolve());
    });
    this.map?.invalidateSize(false);

    await new Promise<void>((resolve) => {
      window.requestAnimationFrame(() => resolve());
    });

    if (!this.isMapRenderable()) return;

    if (this.pendingVisibleLayerBuild || !this.shadeLayer) {
      this.rebuildShadeLayer(config);
      this.pendingVisibleLayerBuild = false;
    }

    this.updateTargetMarker();
    void this.applyShadeSettings();
  }

  private rebuildShadeLayer(config: ShadeMapConfig): void {
    if (!this.map) return;
    this.destroyShadeLayer();
    this.shadeLayerReady = false;
    const contextEpoch = this.gardenContextEpoch;
    const layer = createShadeMapRuntime({
      apiKey: config.api_key,
      date: this.currentDate,
      color: "#1e293b",
      opacity: 0.38,
      getSize: () => {
        const size = this.map?.getSize();
        return {
          width: Math.max(1, size?.x ?? 1),
          height: Math.max(1, size?.y ?? 1),
        };
      },
      debug: (message: string) => {
        const trimmed = message.trim();
        if (!trimmed || SHADEMAP_DEBUG_NOISE.has(trimmed)) return;
        this.setDebugMessage(`ShadeMap debug: ${trimmed}`);
      },
      terrainSource: {
        maxZoom: config.terrain_max_zoom,
        tileSize: config.terrain_tile_size,
        getSourceUrl: ({ x, y, z }: { x: number; y: number; z: number }) => buildShadeMapTerrainUrl(
          config.terrain_url_template,
          { x, y, z },
        ),
        getElevation: decodeTerrariumElevation,
      },
      getFeatures: async () => {
        if (!this.map) return [];
        const zoom = Math.round(this.map.getZoom());
        if (zoom < config.features_min_zoom) return [];
        const bounds = this.map.getBounds().pad(0.15);
        try {
          const features = await getShadeMapFeaturesApi({
            north: bounds.getNorth(),
            south: bounds.getSouth(),
            east: bounds.getEast(),
            west: bounds.getWest(),
            zoom,
          });
          if (contextEpoch !== this.gardenContextEpoch) return [];
          const house = features.find((f) => {
            const rec = f as Record<string, unknown>;
            const p = rec["properties"] as Record<string, unknown> | undefined;
            return p && p["source_id"] === "gardenops-house";
          });
          if (house) {
            const rec = house as Record<string, unknown>;
            const p = rec["properties"] as Record<string, unknown>;
            const g = rec["geometry"] as Record<string, unknown>;
            console.info(
              "[ShadeMap] House feature present — height:",
              p["height"],
              "coords:",
              JSON.stringify((g["coordinates"] as unknown[])[0]),
            );
          } else {
            console.warn("[ShadeMap] House feature MISSING from", features.length, "features");
          }
          return features as ShadeMapFeature[];
        } catch (err) {
          const message = getApiErrorMessage(err);
          console.error("ShadeMap feature proxy failed", err);
          this.setDebugMessage(`Feature fetch failed: ${message}`);
          return [];
        }
      },
    });
    this.detachIdleHandler = layer.on("idle", this.idleHandler);
    this.shadeLayer = layer.addTo(this.map);
  }

  private applyPersistedState(state: PersistedShadeMapState): void {
    this.activeMode = state.mode;
    this.activePreset = state.preset;
    this.activeTargetId = state.selected_plot_id
      ? plotTargetId(state.selected_plot_id)
      : HOUSE_TARGET_ID;
    const persistedDate = new Date(state.analysis_timestamp_ms);
    if (!Number.isNaN(persistedDate.getTime())) {
      this.currentDate = persistedDate;
    }
  }

  private buildPersistedState(): PersistedShadeMapState {
    return {
      mode: this.activeMode,
      selected_plot_id: this.activeTargetId === HOUSE_TARGET_ID
        ? null
        : this.activeTargetId.slice("plot:".length),
      analysis_timestamp_ms: this.currentDate.getTime(),
      preset: this.activePreset,
    };
  }

  private emitStateChange(): void {
    if (this.canWrite()) {
      this.onStateChanged?.(this.buildPersistedState());
    }
  }

  private emitSunlightSnapshot(plotIds: string[]): void {
    this.onSunlightSnapshot?.(plotIds);
  }

  private cancelSunlightSnapshotSchedule(emitEmpty: boolean): void {
    this.sunlightSnapshotEpoch += 1;
    if (this.sunlightSnapshotTimer != null) {
      window.clearTimeout(this.sunlightSnapshotTimer);
      this.sunlightSnapshotTimer = null;
    }
    if (emitEmpty) this.emitSunlightSnapshot([]);
  }

  private scheduleSunlightSnapshot(): void {
    const epoch = ++this.sunlightSnapshotEpoch;
    if (this.sunlightSnapshotTimer != null) {
      window.clearTimeout(this.sunlightSnapshotTimer);
    }
    this.sunlightSnapshotTimer = window.setTimeout(() => {
      this.sunlightSnapshotTimer = null;
      if (epoch !== this.sunlightSnapshotEpoch) return;
      void this.computeSunlitPlotSnapshot(epoch);
    }, SUNLIGHT_SNAPSHOT_DEBOUNCE_MS);
  }

  private getPlaybackWindow(): DaylightWindow | null {
    if (!this.config) return null;
    return computeDaylightWindow(this.currentDate, this.config.latitude, this.config.longitude);
  }

  private syncPlaybackControls(): void {
    const playbackBtn = queryButton("shade-playback-btn");
    const stepSelect = querySelect("shade-step-select");
    const speedSelect = querySelect("shade-speed-select");
    if (playbackBtn) {
      playbackBtn.textContent = this.playbackActive ? t("shade.pause") : t("shade.play");
      playbackBtn.setAttribute("aria-pressed", this.playbackActive ? "true" : "false");
    }
    if (stepSelect) stepSelect.value = String(this.playbackStepMinutes);
    if (speedSelect) speedSelect.value = String(this.playbackFrameDelayMs);
  }

  private stopPlayback(): void {
    if (this.playbackTimer != null) {
      window.clearTimeout(this.playbackTimer);
      this.playbackTimer = null;
    }
    if (!this.playbackActive) return;
    this.playbackActive = false;
    this.syncPlaybackControls();
  }

  private startPlayback(): void {
    let needsRefresh = false;
    const playbackWindow = this.getPlaybackWindow();
    if (!playbackWindow) {
      this.setSummary(t("shade.summary_no_daylight", { date: formatDisplayDate(this.currentDate) }));
      this.syncPlaybackControls();
      return;
    }
    if (!playbackWindow.allDaylight) {
      const currentMinutes = minutesOfDay(this.currentDate);
      const sunriseMinutes = minutesOfDay(playbackWindow.sunrise);
      const sunsetMinutes = minutesOfDay(playbackWindow.sunset);
      if (currentMinutes < sunriseMinutes || currentMinutes >= sunsetMinutes) {
        this.currentDate = new Date(playbackWindow.sunrise.getTime());
        this.activePreset = "custom";
        this.syncInputs();
        this.syncPresetSelect();
        this.emitStateChange();
        needsRefresh = true;
      }
    }
    if (this.playbackActive) return;
    this.playbackActive = true;
    this.syncPlaybackControls();
    if (needsRefresh || !this.shadeLayerReady) {
      if (!this.shadeLayer) {
        if (this.config && this.map && this.isMapRenderable()) {
          this.rebuildShadeLayer(this.config);
        } else {
          this.pendingVisibleLayerBuild = true;
          this.scheduleActivation();
          return;
        }
      }
      void this.applyShadeSettings();
    } else if (this.shadeLayerReady) {
      this.scheduleNextPlaybackStep();
    }
  }

  private scheduleNextPlaybackStep(): void {
    if (!this.playbackActive) return;
    if (this.playbackTimer != null) {
      window.clearTimeout(this.playbackTimer);
    }
    this.playbackTimer = window.setTimeout(() => {
      this.playbackTimer = null;
      this.advancePlaybackTime();
    }, this.playbackFrameDelayMs);
  }

  private advancePlaybackTime(): void {
    if (!this.playbackActive) return;
    const playbackWindow = this.getPlaybackWindow();
    if (!playbackWindow) {
      this.stopPlayback();
      this.setSummary(t("shade.summary_no_daylight", { date: formatDisplayDate(this.currentDate) }));
      return;
    }
    const nextDate = new Date(this.currentDate.getTime() + this.playbackStepMinutes * 60 * 1000);
    const reachedSunset = !playbackWindow.allDaylight && nextDate.getTime() >= playbackWindow.sunset.getTime();
    this.currentDate = reachedSunset
      ? new Date(playbackWindow.sunset.getTime())
      : nextDate;
    this.activePreset = "custom";
    this.syncInputs();
    this.syncPresetSelect();
    this.emitStateChange();
    if (reachedSunset) {
      this.stopPlayback();
    }
    void this.applyShadeSettings();
  }

  private wireControls(): void {
    if (this.controlsWired) return;
    this.controlsWired = true;

    this._wireDateTimeControls();
    this._wirePlaybackControls();
    this._wireCalibrationControls();
    this._wireObstacleControls();
    this._wirePresetControls();
    this._wireModeControls();
  }

  private ensureModeControl(): void {
    if (document.getElementById("shade-mode-select")) return;
    const group = document.querySelector<HTMLElement>("#shade-panel .shade-time-group");
    if (!group) return;
    const select = document.createElement("select");
    select.id = "shade-mode-select";
    select.dataset["testid"] = "shade-mode-select";
    select.setAttribute("aria-label", "Shade display mode");
    const shadow = document.createElement("option");
    shadow.value = "shadow";
    shadow.textContent = "Shadow";
    const sunHours = document.createElement("option");
    sunHours.value = "sun-hours";
    sunHours.textContent = "Sun hours";
    select.append(shadow, sunHours);
    group.prepend(select);
  }

  private _wireModeControls(): void {
    const select = querySelect("shade-mode-select");
    select?.addEventListener("change", () => {
      if (select.value !== "shadow" && select.value !== "sun-hours") return;
      this.stopPlayback();
      this.activeMode = select.value;
      this.emitStateChange();
      void this.applyShadeSettings();
    });
  }

  private _wireDateTimeControls(): void {
    const dateInput = queryInput("shade-date-input");
    const timeInput = queryInput("shade-time-input");
    const targetSelect = querySelect("shade-target-select");

    const updateDate = () => {
      const next = parseLocalDateTime(dateInput?.value ?? "", timeInput?.value ?? "");
      if (!next) return;
      this.stopPlayback();
      this.currentDate = next;
      this.activePreset = "custom";
      this.syncPresetSelect();
      this.emitStateChange();
      void this.applyShadeSettings();
    };

    dateInput?.addEventListener("change", updateDate);
    timeInput?.addEventListener("change", updateDate);
    targetSelect?.addEventListener("change", () => {
      this.activeTargetId = targetSelect.value || HOUSE_TARGET_ID;
      this.updateTargetMarker();
      this.emitStateChange();
      void this.refreshStatus();
    });
  }

  private _wirePlaybackControls(): void {
    const playbackBtn = queryButton("shade-playback-btn");
    const stepSelect = querySelect("shade-step-select");
    const speedSelect = querySelect("shade-speed-select");

    playbackBtn?.addEventListener("click", () => {
      if (this.playbackActive) {
        this.stopPlayback();
      } else {
        this.startPlayback();
      }
    });
    stepSelect?.addEventListener("change", () => {
      const parsed = Number.parseInt(stepSelect.value, 10);
      if (!Number.isFinite(parsed) || parsed <= 0) return;
      this.playbackStepMinutes = parsed;
      this.syncPlaybackControls();
    });
    speedSelect?.addEventListener("change", () => {
      const parsed = Number.parseInt(speedSelect.value, 10);
      if (!Number.isFinite(parsed) || parsed < 100) return;
      this.playbackFrameDelayMs = parsed;
      this.syncPlaybackControls();
      if (this.playbackActive) this.scheduleNextPlaybackStep();
    });
  }

  private _wireCalibrationControls(): void {
    const calibrationSaveBtn = queryButton("shade-calibration-save-btn");
    const calibrationResetBtn = queryButton("shade-calibration-reset-btn");
    const calibrationFillBtn = queryButton("shade-calibration-fill-btn");

    calibrationSaveBtn?.addEventListener("click", () => {
      void this.saveCalibrationFromInputs();
    });
    calibrationResetBtn?.addEventListener("click", () => {
      void this.resetCalibration();
    });
    calibrationFillBtn?.addEventListener("click", () => {
      this.fillCalibrationFromCurrentEstimate();
    });
  }

  private _wireObstacleControls(): void {
    const obstacleSelect = querySelect("shade-obstacle-select");
    const obstaclePlotSelect = querySelect("shade-obstacle-plot");
    const obstacleFillTargetBtn = queryButton("shade-obstacle-fill-target-btn");
    const obstacleSaveBtn = queryButton("shade-obstacle-save-btn");
    const obstacleDeleteBtn = queryButton("shade-obstacle-delete-btn");

    obstacleSelect?.addEventListener("change", () => {
      const value = obstacleSelect.value;
      if (value === "new") {
        this.activeObstacleId = null;
      } else {
        const parsed = Number.parseInt(value, 10);
        this.activeObstacleId = Number.isFinite(parsed) ? parsed : null;
      }
      this.renderObstacleEditor();
    });
    obstaclePlotSelect?.addEventListener("change", () => {
      if (this.activeObstacleId != null) return;
      const plotId = obstaclePlotSelect.value || null;
      if (!plotId) return;
      const target = this.findTargetById(plotTargetId(plotId));
      if (!target) return;
      const latInput = queryInput("shade-obstacle-lat");
      const lngInput = queryInput("shade-obstacle-lng");
      if (latInput) latInput.value = target.latitude.toFixed(6);
      if (lngInput) lngInput.value = target.longitude.toFixed(6);
    });
    obstacleFillTargetBtn?.addEventListener("click", () => {
      this.fillObstacleFromActiveTarget();
    });
    obstacleSaveBtn?.addEventListener("click", () => {
      void this.saveObstacleFromInputs();
    });
    obstacleDeleteBtn?.addEventListener("click", () => {
      void this.deleteActiveObstacle();
    });
  }

  private _wirePresetControls(): void {
    const presetSelect = querySelect("shade-preset-select");
    presetSelect?.addEventListener("change", () => {
      const preset = presetSelect.value;
      if (!isShadePreset(preset)) return;
      this.stopPlayback();
      this.activePreset = preset;
      const next = preset === "now"
        ? new Date()
        : seasonalPreset(preset, this.currentDate.getFullYear());
      this.currentDate = next;
      this.syncInputs();
      this.syncPresetSelect();
      this.emitStateChange();
      void this.applyShadeSettings();
    });
  }

  private renderCalibrationEditor(): void {
    const calibration = this.calibration;
    const setValue = (id: string, value: number | null) => {
      const input = queryInput(id);
      if (!input) return;
      input.value = value == null ? "" : String(value);
    };
    setValue("shade-cal-house-nw-lat", calibration.house_nw_latitude);
    setValue("shade-cal-house-nw-lng", calibration.house_nw_longitude);
    setValue("shade-cal-house-ne-lat", calibration.house_ne_latitude);
    setValue("shade-cal-house-ne-lng", calibration.house_ne_longitude);
    setValue("shade-cal-house-se-lat", calibration.house_se_latitude);
    setValue("shade-cal-house-se-lng", calibration.house_se_longitude);
    setValue("shade-cal-house-sw-lat", calibration.house_sw_latitude);
    setValue("shade-cal-house-sw-lng", calibration.house_sw_longitude);
    const status = document.getElementById("shade-calibration-status");
    const qa = this.computeCalibrationQa();
    const transform = this.getCalibrationTransform();
    if (status) {
      if (qa) {
        status.textContent = t("shade.calibration_active", {
          rms: qa.rmsErrorM.toFixed(2),
          max: qa.maxErrorM.toFixed(2),
        });
      } else if (this.calibration.enabled && transform) {
        status.textContent = t("shade.calibration_legacy");
      } else {
        status.textContent = t("shade.calibration_fallback");
      }
    }
    this.renderCalibrationQa(qa);
  }

  private readCalibrationInputs(enabled: boolean): ShadeMapCalibration {
    const read = (id: string) => {
      const input = queryInput(id);
      return parseNumberInput(input?.value ?? "");
    };
    return {
      enabled,
      calibration_type: "house-corners",
      origin_grid_col: this.calibration.origin_grid_col,
      origin_grid_row: this.calibration.origin_grid_row,
      origin_latitude: this.calibration.origin_latitude,
      origin_longitude: this.calibration.origin_longitude,
      axis_grid_col: this.calibration.axis_grid_col,
      axis_grid_row: this.calibration.axis_grid_row,
      axis_latitude: this.calibration.axis_latitude,
      axis_longitude: this.calibration.axis_longitude,
      house_nw_latitude: read("shade-cal-house-nw-lat"),
      house_nw_longitude: read("shade-cal-house-nw-lng"),
      house_ne_latitude: read("shade-cal-house-ne-lat"),
      house_ne_longitude: read("shade-cal-house-ne-lng"),
      house_se_latitude: read("shade-cal-house-se-lat"),
      house_se_longitude: read("shade-cal-house-se-lng"),
      house_sw_latitude: read("shade-cal-house-sw-lat"),
      house_sw_longitude: read("shade-cal-house-sw-lng"),
    };
  }

  private fillCalibrationFromCurrentEstimate(): void {
    const estimated = this.computeEstimatedHouseCorners();
    if (!estimated || estimated.length < 4) return;
    const [nw, ne, se, sw] = estimated as [HouseCornerLatLng, HouseCornerLatLng, HouseCornerLatLng, HouseCornerLatLng];
    const setValue = (id: string, value: number) => {
      const input = queryInput(id);
      if (input) input.value = value.toFixed(6);
    };
    setValue("shade-cal-house-nw-lat", nw.latitude);
    setValue("shade-cal-house-nw-lng", nw.longitude);
    setValue("shade-cal-house-ne-lat", ne.latitude);
    setValue("shade-cal-house-ne-lng", ne.longitude);
    setValue("shade-cal-house-se-lat", se.latitude);
    setValue("shade-cal-house-se-lng", se.longitude);
    setValue("shade-cal-house-sw-lat", sw.latitude);
    setValue("shade-cal-house-sw-lng", sw.longitude);
  }

  private async saveCalibrationFromInputs(): Promise<void> {
    if (!this.canWrite()) return;
    try {
      const saved = await updateShadeMapCalibrationApi(this.readCalibrationInputs(true));
      this.calibration = saved;
      this.renderCalibrationEditor();
      this.applyLocalGeometryRefresh(t("shade.summary_calibration_saved"));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setDebugMessage(`Calibration save failed: ${message}`);
      this.showError(`ShadeMap calibration failed: ${message}`);
    }
  }

  private async resetCalibration(): Promise<void> {
    if (!this.canWrite()) return;
    try {
      const saved = await updateShadeMapCalibrationApi(defaultCalibration());
      this.calibration = saved;
      this.renderCalibrationEditor();
      this.applyLocalGeometryRefresh(t("shade.summary_calibration_reset"));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setDebugMessage(`Calibration reset failed: ${message}`);
      this.showError(`ShadeMap calibration reset failed: ${message}`);
    }
  }

  private renderObstacleEditor(): void {
    const obstacleSelect = querySelect("shade-obstacle-select");
    const plotSelect = querySelect("shade-obstacle-plot");
    const deleteBtn = queryButton("shade-obstacle-delete-btn");

    if (obstacleSelect) {
      obstacleSelect.replaceChildren();
      const defaultOption = document.createElement("option");
      defaultOption.value = "new";
      defaultOption.textContent = t("shade.obstacle_new");
      obstacleSelect.appendChild(defaultOption);
      for (const obstacle of this.obstacles) {
        const option = document.createElement("option");
        option.value = String(obstacle.id);
        option.textContent = `${obstacle.label} (${obstacle.kind})`;
        obstacleSelect.appendChild(option);
      }
      obstacleSelect.value = this.activeObstacleId == null ? "new" : String(this.activeObstacleId);
    }

    if (plotSelect) {
      plotSelect.replaceChildren();
      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.textContent = t("shade.obstacle_no_plot");
      plotSelect.appendChild(defaultOption);
      for (const plot of this.garden?.plots ?? []) {
        const option = document.createElement("option");
        option.value = plot.plot_id;
        option.textContent = `${plot.plot_id} · ${plot.zone_name} ${plot.plot_number}`;
        plotSelect.appendChild(option);
      }
    }

    const fallbackTarget = this.getActiveTarget();
    const fallbackLat = fallbackTarget?.latitude ?? this.config?.latitude ?? 0;
    const fallbackLng = fallbackTarget?.longitude ?? this.config?.longitude ?? 0;
    const current = this.activeObstacleId == null
      ? defaultObstacleInput(fallbackLat, fallbackLng)
      : this.obstacles.find((item) => item.id === this.activeObstacleId)
        ?? defaultObstacleInput(fallbackLat, fallbackLng);

    const setInputValue = (id: string, value: string) => {
      const input = queryInput(id);
      if (input) input.value = value;
    };
    const setSelectValue = (id: string, value: string) => {
      const select = querySelect(id);
      if (select) select.value = value;
    };
    const activeCheckbox = queryInput("shade-obstacle-active");

    setInputValue("shade-obstacle-label", current.label);
    setSelectValue("shade-obstacle-kind", current.kind);
    setSelectValue("shade-obstacle-plot", current.linked_plot_id ?? "");
    setInputValue("shade-obstacle-height", current.height_m.toString());
    setInputValue("shade-obstacle-radius", current.crown_radius_m.toString());
    setInputValue("shade-obstacle-lat", current.latitude.toFixed(6));
    setInputValue("shade-obstacle-lng", current.longitude.toFixed(6));
    if (activeCheckbox) activeCheckbox.checked = current.active;
    if (deleteBtn) deleteBtn.disabled = !this.canWrite() || this.activeObstacleId == null;

    const status = document.getElementById("shade-obstacle-status");
    if (status) {
      status.textContent = this.activeObstacleId == null
        ? t("shade.obstacle_status_new")
        : t("shade.obstacle_status_existing");
    }
  }

  private readObstacleInputs(): ShadeMapObstacleInput {
    const readValue = (id: string) => queryInput(id)?.value?.trim() ?? "";
    const kind = (querySelect("shade-obstacle-kind")?.value ?? "tree") as ShadeMapObstacleInput["kind"];
    const linkedPlotId = (querySelect("shade-obstacle-plot")?.value ?? "").trim() || null;
    const active = queryInput("shade-obstacle-active")?.checked ?? true;
    const latitude = parseNumberInput(readValue("shade-obstacle-lat"));
    const longitude = parseNumberInput(readValue("shade-obstacle-lng"));
    const heightM = parseNumberInput(readValue("shade-obstacle-height"));
    const crownRadiusM = parseNumberInput(readValue("shade-obstacle-radius"));
    if (latitude == null || longitude == null || heightM == null || crownRadiusM == null) {
      throw new Error(t("shade.summary_obstacle_required_fields"));
    }
    return {
      label: readValue("shade-obstacle-label"),
      kind,
      linked_plot_id: linkedPlotId,
      latitude,
      longitude,
      height_m: heightM,
      crown_radius_m: crownRadiusM,
      active,
    };
  }

  private fillObstacleFromActiveTarget(): void {
    const target = this.getActiveTarget();
    if (!target) return;
    const latInput = queryInput("shade-obstacle-lat");
    const lngInput = queryInput("shade-obstacle-lng");
    const labelInput = queryInput("shade-obstacle-label");
    const plotSelect = querySelect("shade-obstacle-plot");
    if (latInput) latInput.value = target.latitude.toFixed(6);
    if (lngInput) lngInput.value = target.longitude.toFixed(6);
    if (labelInput && !labelInput.value.trim()) labelInput.value = target.label;
    if (plotSelect && target.kind === "plot") plotSelect.value = target.id.slice("plot:".length);
  }

  private async saveObstacleFromInputs(): Promise<void> {
    if (!this.canWrite()) return;
    try {
      const body = this.readObstacleInputs();
      const saved = this.activeObstacleId == null
        ? await createShadeMapObstacleApi(body)
        : await updateShadeMapObstacleApi(this.activeObstacleId, body);
      const existingIndex = this.obstacles.findIndex((item) => item.id === saved.id);
      if (existingIndex >= 0) {
        this.obstacles.splice(existingIndex, 1, saved);
      } else {
        this.obstacles.push(saved);
        this.obstacles.sort((a, b) => a.id - b.id);
      }
      this.activeObstacleId = saved.id;
      this.renderObstacleEditor();
      this.applyLocalGeometryRefresh(t("shade.summary_obstacle_saved"));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setDebugMessage(`Obstacle save failed: ${message}`);
      this.showError(`ShadeMap obstacle save failed: ${message}`);
    }
  }

  private async deleteActiveObstacle(): Promise<void> {
    if (!this.canWrite()) return;
    if (this.activeObstacleId == null) return;
    try {
      await deleteShadeMapObstacleApi(this.activeObstacleId);
      this.obstacles = this.obstacles.filter((item) => item.id !== this.activeObstacleId);
      this.activeObstacleId = this.obstacles[0]?.id ?? null;
      this.renderObstacleEditor();
      this.applyLocalGeometryRefresh(t("shade.summary_obstacle_deleted"));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setDebugMessage(`Obstacle delete failed: ${message}`);
      this.showError(`ShadeMap obstacle delete failed: ${message}`);
    }
  }

  private applyLocalGeometryRefresh(summary: string): void {
    this.setSummary(summary);
    this.renderCalibrationEditor();
    this.renderObstacleEditor();
    this.syncTargetOptions();
    if (this.config && this.isMapRenderable()) {
      this.mountMap(this.config);
    }
    this.updateTargetMarker();
    this.cancelSunlightSnapshotSchedule(true);
    if (this.config && this.map && this.isMapRenderable()) {
      this.rebuildShadeLayer(this.config);
      void this.applyShadeSettings();
    } else {
      this.pendingVisibleLayerBuild = true;
      this.scheduleActivation();
      void this.refreshStatus();
    }
  }

  private async applyShadeSettings(): Promise<void> {
    const shadeLayer = this.shadeLayer;
    if (!shadeLayer) return;
    const settingsEpoch = ++this.shadeSettingsEpoch;
    this.syncPlaybackControls();
    this.renderRevision += 1;
    this.publishRenderState("loading");
    this.shadeLayerReady = false;
    this.applyingShadeSettings = true;
    const target = this.getActiveTarget();
    const targetLabel = target?.label ?? t("shade.house_center", { label: t("shade.house_label") });
    this.setSummary(t("shade.summary_checking", { target: targetLabel }));
    let applyError: unknown = null;
    try {
      shadeLayer.setDate(this.currentDate);
      await shadeLayer.setSunExposure(this.activeMode === "sun-hours");
    } catch (error) {
      applyError = error;
    } finally {
      if (this.shadeLayer !== shadeLayer || settingsEpoch !== this.shadeSettingsEpoch) return;
      this.applyingShadeSettings = false;
    }
    if (this.shadeLayer !== shadeLayer || settingsEpoch !== this.shadeSettingsEpoch) return;
    if (applyError) {
      const message = applyError instanceof Error ? applyError.message : String(applyError);
      console.error("ShadeMap apply settings failed", applyError);
      this.showError(`ShadeMap failed to apply shading settings: ${message}`);
      return;
    }
    if (this.tryRefreshReadyLayer()) return;
    this.scheduleReadyFallback();
  }

  private clearReadyFallback(): void {
    if (this.readyFallbackTimer != null) {
      window.clearTimeout(this.readyFallbackTimer);
      this.readyFallbackTimer = null;
    }
  }

  private destroyShadeLayer(): void {
    this.clearReadyFallback();
    this.applyingShadeSettings = false;
    this.cancelSunlightSnapshotSchedule(true);
    const layer = this.shadeLayer as (ShadeMap & ShadeRuntime) | null;
    this.detachIdleHandler?.();
    this.detachIdleHandler = null;
    if (!layer) {
      this.shadeLayerReady = false;
      return;
    }
    try {
      layer.onRemove?.();
    } catch (error) {
      console.warn("ShadeMap onRemove failed", error);
    }
    try {
      layer._canvasOverlay?.remove?.();
    } catch (error) {
      console.warn("ShadeMap canvas overlay removal failed", error);
    }
    try {
      layer.removeAllListeners?.();
    } catch (error) {
      console.warn("ShadeMap listener cleanup failed", error);
    }
    this.shadeLayer = null;
    this.shadeLayerReady = false;
  }

  private isShadeLayerSampleable(): boolean {
    if (!this.map || !this.shadeLayer) return false;
    if (this.shadeSimulationUnavailable()) return true;
    const runtime = this.getShadeRuntime();
    const canvas = runtime?._canvas;
    const gl = runtime?._gl;
    if (!canvas || !gl || canvas.width < 1 || canvas.height < 1) return false;
    const size = this.map.getSize();
    return Math.abs(canvas.width - size.x) <= 1 && Math.abs(canvas.height - size.y) <= 1;
  }

  private tryRefreshReadyLayer(): boolean {
    if (!this.shadeLayer) return false;
    if (this.shadeSimulationUnavailable()) {
      this.clearReadyFallback();
      this.shadeLayerReady = true;
      void this.refreshStatus();
      return true;
    }
    if (!this.isShadeLayerSampleable()) return false;
    try {
      this.shadeLayer.flushSync();
    } catch (error) {
      console.warn("ShadeMap flushSync failed", error);
    }
    this.clearReadyFallback();
    this.shadeLayerReady = true;
    void this.refreshStatus();
    return true;
  }

  private scheduleReadyFallback(): void {
    this.clearReadyFallback();
    const seq = ++this.readyFallbackSeq;
    this.readyFallbackTimer = window.setTimeout(() => {
      this.readyFallbackTimer = null;
      if (seq !== this.readyFallbackSeq) return;
      if (this.tryRefreshReadyLayer()) return;
      this.setDebugMessage("ShadeMap render timeout: rebuilding hidden layer.");
      this.pendingVisibleLayerBuild = true;
      this.destroyShadeLayer();
      this.map?.invalidateSize(false);
      this.scheduleActivation();
    }, SHADE_READY_FALLBACK_MS);
  }

  private async refreshStatus(): Promise<void> {
    if (!this.map || !this.shadeLayer || !this.config) return;
    if (!this.shadeLayerReady) return;
    const target = this.getActiveTarget();
    if (!target) return;

    try {
      if (this.shadeSimulationUnavailable()) {
        await this.ensureSunWindow(this.currentDate);
        const estHours = this.getEstimatedDailyHours();
        this.setSummary(estHours != null
          ? t("shade.summary_estimate_only_with_est", {
            target: target.label,
            hours: estHours.toFixed(1),
          })
          : t("shade.summary_estimate_only", {
            target: target.label,
          }));
        this.setDebugMessage(t("shade.debug_simulator_unavailable"));
        this.publishRenderState("ready");
        this.cancelSunlightSnapshotSchedule(true);
        if (this.playbackActive) this.scheduleNextPlaybackStep();
        return;
      }
      this.shadeLayer.flushSync();
      await this.ensureSunWindow(this.currentDate);
      const withinSunWindow = this.isWithinSunWindow(this.currentDate);
      const inSun = this.pixelIsSun(this.sampleTargetPixel(target));
      if (inSun == null) {
        this.pendingVisibleLayerBuild = true;
        this.setDebugMessage("ShadeMap sampling missed the target; retrying layer build.");
        this.cancelSunlightSnapshotSchedule(true);
        this.scheduleActivation();
        return;
      }
      const displaySun = withinSunWindow && inSun;
      const sunLabel = displaySun ? t("shade.state_direct_sun") : t("shade.state_shade");
      const estHours = this.getEstimatedDailyHours();
      this.setSummary(estHours != null
        ? t("shade.summary_ready_with_est", {
          target: target.label,
          state: sunLabel,
          hours: estHours.toFixed(1),
        })
        : t("shade.summary_ready", {
          target: target.label,
          state: sunLabel,
        }));
      this.setDebugMessage("");
      this.publishRenderState("ready");
      this.scheduleSunlightSnapshot();
      if (this.playbackActive) this.scheduleNextPlaybackStep();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error("ShadeMap refresh failed", error);
      this.showError(`ShadeMap failed to calculate shading: ${message}`);
    }
  }

  private syncInputs(): void {
    const dateInput = queryInput("shade-date-input");
    const timeInput = queryInput("shade-time-input");
    if (dateInput) dateInput.value = formatDateInputValue(this.currentDate);
    if (timeInput) timeInput.value = formatTimeInputValue(this.currentDate);
    const localeTag = getLocaleTag();
    if (dateInput) dateInput.lang = localeTag;
    if (timeInput) timeInput.lang = localeTag;
    const modeSelect = querySelect("shade-mode-select");
    if (modeSelect) modeSelect.value = this.activeMode;
    this.syncPlaybackControls();
  }

  private syncPresetSelect(): void {
    const select = querySelect("shade-preset-select");
    if (select) select.value = this.activePreset;
  }

  private writeStaticMeta(): void {
    if (!this.config) return;
    const link = document.getElementById("shade-open-link");
    if (link instanceof HTMLAnchorElement) {
      const safeUrl = sanitizeUrl(this.config.share_url);
      link.hidden = !safeUrl;
      if (safeUrl) {
        link.href = safeUrl;
      }
    }
  }

  private syncTargetOptions(): void {
    const select = querySelect("shade-target-select");
    const field = document.getElementById("shade-target-field");
    const target = this.getActiveTarget();
    const targets = this.listTargets();
    if (select) {
      select.replaceChildren();
      for (const item of targets) {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        select.appendChild(option);
      }
      select.value = target?.id ?? HOUSE_TARGET_ID;
      select.hidden = targets.length <= 1;
    }
    if (field instanceof HTMLElement) {
      field.hidden = targets.length <= 1;
    }
  }

  private updateTargetMarker(): void {
    if (!this.targetMarker || !this.map) return;
    const target = this.getActiveTarget();
    if (!target) {
      this.targetMarker.remove();
      return;
    }
    this.targetMarker.setLatLng([target.latitude, target.longitude]);
    const tooltipLabel = document.createElement("span");
    tooltipLabel.textContent = target.label;
    this.targetMarker.bindTooltip(tooltipLabel, {
      direction: "top",
      offset: [0, -8],
    });
    if (!this.map.hasLayer(this.targetMarker)) {
      this.targetMarker.addTo(this.map);
    }
  }

  private getActiveTarget(): TargetPoint | null {
    return this.findTargetById(this.activeTargetId) ?? this.findTargetById(HOUSE_TARGET_ID) ?? null;
  }

  private listTargets(): TargetPoint[] {
    const houseTarget = this.getHouseTargetCoordinates();
    if (!this.config) return [];
    const targets: TargetPoint[] = [
      {
        id: HOUSE_TARGET_ID,
        kind: "house",
        label: t("shade.house_center", { label: this.config.label }),
        latitude: houseTarget.latitude,
        longitude: houseTarget.longitude,
      },
    ];
    if (!this.garden) return targets;

    for (const plot of this.garden.plots) {
      const latLng = this.computePlotLatLng(plot);
      if (!latLng) continue;
      targets.push({
        id: plotTargetId(plot.plot_id),
        kind: "plot",
        label: `${plot.plot_id} · ${plot.zone_name} ${plot.plot_number}`,
        latitude: latLng.latitude,
        longitude: latLng.longitude,
      });
    }
    return targets;
  }

  private findTargetById(targetId: string): TargetPoint | undefined {
    return this.listTargets().find((target) => target.id === targetId);
  }

  private getHouseCornerGridPoints(): Array<{ gridCol: number; gridRow: number }> | null {
    if (!this.garden) return null;
    const westEdge = this.garden.housePosition.col - 1;
    const eastEdge = westEdge + this.garden.houseSize.width;
    const northEdge = this.garden.housePosition.row - 1;
    const southEdge = northEdge + this.garden.houseSize.height;
    return [
      { gridCol: westEdge, gridRow: northEdge },
      { gridCol: eastEdge, gridRow: northEdge },
      { gridCol: eastEdge, gridRow: southEdge },
      { gridCol: westEdge, gridRow: southEdge },
    ];
  }

  private getCalibratedHouseCorners(): HouseCornerLatLng[] | null {
    const calibration = this.calibration;
    if (calibration.calibration_type !== "house-corners") return null;
    const values = [
      calibration.house_nw_latitude,
      calibration.house_nw_longitude,
      calibration.house_ne_latitude,
      calibration.house_ne_longitude,
      calibration.house_se_latitude,
      calibration.house_se_longitude,
      calibration.house_sw_latitude,
      calibration.house_sw_longitude,
    ];
    if (values.some((value) => value == null)) return null;
    return [
      { latitude: calibration.house_nw_latitude as number, longitude: calibration.house_nw_longitude as number },
      { latitude: calibration.house_ne_latitude as number, longitude: calibration.house_ne_longitude as number },
      { latitude: calibration.house_se_latitude as number, longitude: calibration.house_se_longitude as number },
      { latitude: calibration.house_sw_latitude as number, longitude: calibration.house_sw_longitude as number },
    ];
  }

  private getCalibrationTransform(): GridCalibrationTransform | null {
    if (!this.calibration.enabled) return null;
    const houseCorners = this.getCalibratedHouseCorners();
    const houseGridCorners = this.getHouseCornerGridPoints();
    if (this.calibration.calibration_type === "house-corners" && houseCorners && houseGridCorners) {
      const referenceLatitude = houseCorners.reduce((sum, point) => sum + point.latitude, 0) / houseCorners.length;
      const referenceLongitude = houseCorners.reduce((sum, point) => sum + point.longitude, 0) / houseCorners.length;
      return fitSimilarityTransform(
        houseGridCorners.map((point) => ({ x: point.gridCol, y: -point.gridRow })),
        houseCorners.map((point) => {
          const local = latLngToLocalEastNorth(point.latitude, point.longitude, referenceLatitude, referenceLongitude);
          return { x: local.east, y: local.north };
        }),
        referenceLatitude,
        referenceLongitude,
      );
    }

    const originGridCol = this.calibration.origin_grid_col;
    const originGridRow = this.calibration.origin_grid_row;
    const originLatitude = this.calibration.origin_latitude;
    const originLongitude = this.calibration.origin_longitude;
    const axisGridCol = this.calibration.axis_grid_col;
    const axisGridRow = this.calibration.axis_grid_row;
    const axisLatitude = this.calibration.axis_latitude;
    const axisLongitude = this.calibration.axis_longitude;
    if (
      originGridCol == null
      || originGridRow == null
      || originLatitude == null
      || originLongitude == null
      || axisGridCol == null
      || axisGridRow == null
      || axisLatitude == null
      || axisLongitude == null
    ) {
      return null;
    }

    const axisLocal = latLngToLocalEastNorth(axisLatitude, axisLongitude, originLatitude, originLongitude);
    return fitSimilarityTransform(
      [
        { x: originGridCol, y: -originGridRow },
        { x: axisGridCol, y: -axisGridRow },
      ],
      [
        { x: 0, y: 0 },
        { x: axisLocal.east, y: axisLocal.north },
      ],
      originLatitude,
      originLongitude,
    );
  }

  private offsetLatLngFromMeters(
    latitude: number,
    longitude: number,
    eastMeters: number,
    northMeters: number,
  ): { latitude: number; longitude: number } {
    const deltaLat = (northMeters / EARTH_RADIUS_METERS) * (180 / Math.PI);
    const deltaLng = (eastMeters / (EARTH_RADIUS_METERS * Math.cos((latitude * Math.PI) / 180))) * (180 / Math.PI);
    return {
      latitude: latitude + deltaLat,
      longitude: longitude + deltaLng,
    };
  }

  private getHouseTargetCoordinates(): { latitude: number; longitude: number } {
    if (!this.config || !this.garden) {
      return {
        latitude: this.config?.latitude ?? 0,
        longitude: this.config?.longitude ?? 0,
      };
    }
    const houseCenterCol = this.garden.housePosition.col - 0.5 + this.garden.houseSize.width / 2;
    const houseCenterRow = this.garden.housePosition.row - 0.5 + this.garden.houseSize.height / 2;
    return this.computeGridPointLatLng(houseCenterCol, houseCenterRow);
  }

  private computeEstimatedHouseCorners(): HouseCornerLatLng[] | null {
    const corners = this.getHouseCornerGridPoints();
    if (!corners) return null;
    return corners.map((corner) => this.computeGridPointLatLng(corner.gridCol, corner.gridRow));
  }

  private computeCalibrationQa(): CalibrationQa | null {
    const actualCorners = this.getCalibratedHouseCorners();
    const houseGridCorners = this.getHouseCornerGridPoints();
    const calibration = this.getCalibrationTransform();
    if (!this.calibration.enabled || !actualCorners || !houseGridCorners || !calibration) return null;

    const fittedCorners = houseGridCorners.map((corner) => this.computeGridPointLatLng(corner.gridCol, corner.gridRow));
    const cornerErrors = fittedCorners.map((point, index) => {
      const actual = actualCorners[index];
      const label = HOUSE_CORNER_LABELS[index] ?? `C${index + 1}`;
      if (!actual) {
        return {
          label,
          errorM: 0,
          fittedCorner: point,
          actualCorner: point,
        };
      }
      const delta = latLngToLocalEastNorth(point.latitude, point.longitude, actual.latitude, actual.longitude);
      return {
        label,
        errorM: Math.hypot(delta.east, delta.north),
        fittedCorner: point,
        actualCorner: actual,
      };
    });
    const meanSquare = cornerErrors.reduce((sum, item) => sum + item.errorM * item.errorM, 0) / cornerErrors.length;
    return {
      rmsErrorM: Math.sqrt(meanSquare),
      maxErrorM: Math.max(...cornerErrors.map((item) => item.errorM)),
      fittedCorners,
      actualCorners,
      cornerErrors,
    };
  }

  private renderCalibrationQa(qa: CalibrationQa | null): void {
    const rms = document.getElementById("shade-calibration-rms");
    const max = document.getElementById("shade-calibration-max");
    const overlay = document.getElementById("shade-calibration-overlay");
    if (rms) rms.textContent = qa
      ? t("shade.calibration_rms", { value: qa.rmsErrorM.toFixed(2) })
      : t("shade.calibration_rms_na");
    if (max) max.textContent = qa
      ? t("shade.calibration_max", { value: qa.maxErrorM.toFixed(2) })
      : t("shade.calibration_max_na");
    if (!overlay) return;
    if (!qa) {
      const empty = document.createElement("p");
      empty.className = "shade-comparison-empty";
      empty.textContent = t("shade.calibration_empty");
      overlay.replaceChildren(empty);
      return;
    }

    const toMeters = (points: HouseCornerLatLng[]) => {
      const referenceLatitude = points.reduce((sum, point) => sum + point.latitude, 0) / points.length;
      const referenceLongitude = points.reduce((sum, point) => sum + point.longitude, 0) / points.length;
      return points.map((point) => latLngToLocalEastNorth(point.latitude, point.longitude, referenceLatitude, referenceLongitude));
    };
    const actual = toMeters(qa.actualCorners);
    const fitted = qa.fittedCorners.map((point) => latLngToLocalEastNorth(
      point.latitude,
      point.longitude,
      qa.actualCorners.reduce((sum, item) => sum + item.latitude, 0) / qa.actualCorners.length,
      qa.actualCorners.reduce((sum, item) => sum + item.longitude, 0) / qa.actualCorners.length,
    ));
    const allPoints = [...actual, ...fitted];
    const minX = Math.min(...allPoints.map((point) => point.east));
    const maxX = Math.max(...allPoints.map((point) => point.east));
    const minY = Math.min(...allPoints.map((point) => point.north));
    const maxY = Math.max(...allPoints.map((point) => point.north));
    const width = Math.max(maxX - minX, 1);
    const height = Math.max(maxY - minY, 1);
    const padding = 12;
    const mapPoint = (point: { east: number; north: number }) => {
      const x = padding + ((point.east - minX) / width) * (240 - padding * 2);
      const y = 180 - padding - ((point.north - minY) / height) * (180 - padding * 2);
      return {
        x,
        y,
      };
    };
    const actualSvgPoints = actual.map(mapPoint);
    const fittedSvgPoints = fitted.map(mapPoint);
    const polygonPoints = (points: Array<{ x: number; y: number }>) =>
      points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");

    const inner = document.createElement("div");
    inner.className = "shade-calibration-overlay-inner";

    const svg = createSvgNode("svg");
    svg.setAttribute("viewBox", "0 0 240 180");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", t("shade.calibration_overlay_aria"));

    const rect = createSvgNode("rect");
    rect.setAttribute("x", "0");
    rect.setAttribute("y", "0");
    rect.setAttribute("width", "240");
    rect.setAttribute("height", "180");
    rect.setAttribute("fill", "transparent");
    svg.appendChild(rect);

    const actualPolygon = createSvgNode("polygon");
    actualPolygon.setAttribute("points", polygonPoints(actualSvgPoints));
    actualPolygon.setAttribute("fill", "rgba(18, 106, 138, 0.18)");
    actualPolygon.setAttribute("stroke", "#126a8a");
    actualPolygon.setAttribute("stroke-width", "2");
    svg.appendChild(actualPolygon);

    const fittedPolygon = createSvgNode("polygon");
    fittedPolygon.setAttribute("points", polygonPoints(fittedSvgPoints));
    fittedPolygon.setAttribute("fill", "rgba(211, 83, 34, 0.12)");
    fittedPolygon.setAttribute("stroke", "#d35322");
    fittedPolygon.setAttribute("stroke-width", "2");
    fittedPolygon.setAttribute("stroke-dasharray", "6 4");
    svg.appendChild(fittedPolygon);

    qa.cornerErrors.forEach((corner, index) => {
      const actualPoint = actualSvgPoints[index] ?? { x: padding, y: padding };
      const fittedPoint = fittedSvgPoints[index] ?? { x: padding, y: padding };
      const labelX = Math.min(actualPoint.x + 7, 206);
      const labelY = Math.max(actualPoint.y - 8, 18);

      const line = createSvgNode("line");
      line.setAttribute("x1", actualPoint.x.toFixed(1));
      line.setAttribute("y1", actualPoint.y.toFixed(1));
      line.setAttribute("x2", fittedPoint.x.toFixed(1));
      line.setAttribute("y2", fittedPoint.y.toFixed(1));
      line.setAttribute("stroke", "rgba(40, 62, 78, 0.32)");
      line.setAttribute("stroke-width", "1.5");
      svg.appendChild(line);

      const actualCircle = createSvgNode("circle");
      actualCircle.setAttribute("cx", actualPoint.x.toFixed(1));
      actualCircle.setAttribute("cy", actualPoint.y.toFixed(1));
      actualCircle.setAttribute("r", "4.2");
      actualCircle.setAttribute("fill", "#126a8a");
      actualCircle.setAttribute("stroke", "#ffffff");
      actualCircle.setAttribute("stroke-width", "1.5");
      svg.appendChild(actualCircle);

      const label = createSvgNode("text");
      label.setAttribute("x", labelX.toFixed(1));
      label.setAttribute("y", labelY.toFixed(1));
      label.setAttribute("fill", "#126a8a");
      label.setAttribute("font-size", "10.5");
      label.setAttribute("font-weight", "700");
      label.textContent = corner.label;
      svg.appendChild(label);

      const fittedCircle = createSvgNode("circle");
      fittedCircle.setAttribute("cx", fittedPoint.x.toFixed(1));
      fittedCircle.setAttribute("cy", fittedPoint.y.toFixed(1));
      fittedCircle.setAttribute("r", "3.2");
      fittedCircle.setAttribute("fill", "#d35322");
      fittedCircle.setAttribute("stroke", "#ffffff");
      fittedCircle.setAttribute("stroke-width", "1.2");
      svg.appendChild(fittedCircle);
    });

    [
      { x: "12", y: "20", text: t("shade.calibration_legend_actual") },
      { x: "12", y: "36", text: t("shade.calibration_legend_fitted") },
      { x: "12", y: "52", text: t("shade.calibration_legend_residual") },
    ].forEach((item) => {
      const text = createSvgNode("text");
      text.setAttribute("x", item.x);
      text.setAttribute("y", item.y);
      text.setAttribute("fill", "currentColor");
      text.setAttribute("font-size", "11");
      text.textContent = item.text;
      svg.appendChild(text);
    });

    const cornerList = document.createElement("div");
    cornerList.className = "shade-calibration-corner-list";
    cornerList.setAttribute("aria-label", t("shade.calibration_corner_errors_aria"));
    qa.cornerErrors.forEach((corner) => {
      const chip = document.createElement("span");
      chip.className = "shade-calibration-corner-chip";
      const label = document.createElement("strong");
      label.textContent = corner.label;
      const error = document.createElement("span");
      error.textContent = `${corner.errorM.toFixed(2)}m`;
      chip.append(label, error);
      cornerList.appendChild(chip);
    });

    inner.append(svg, cornerList);
    overlay.replaceChildren(inner);
  }

  private computeGridPointLatLng(gridCol: number, gridRow: number): { latitude: number; longitude: number } {
    if (!this.config || !this.garden) {
      return {
        latitude: this.config?.latitude ?? 0,
        longitude: this.config?.longitude ?? 0,
      };
    }

    const calibration = this.getCalibrationTransform();
    if (calibration) {
      const northGrid = -gridRow;
      const eastMeters = calibration.translationEastM + calibration.scaleMeters * (
        gridCol * calibration.cosTheta
        - northGrid * calibration.sinTheta
      );
      const northMeters = calibration.translationNorthM + calibration.scaleMeters * (
        gridCol * calibration.sinTheta
        + northGrid * calibration.cosTheta
      );
      return this.offsetLatLngFromMeters(
        calibration.referenceLatitude,
        calibration.referenceLongitude,
        eastMeters,
        northMeters,
      );
    }

    const houseCenterCol = this.garden.housePosition.col - 0.5 + this.garden.houseSize.width / 2;
    const houseCenterRow = this.garden.housePosition.row - 0.5 + this.garden.houseSize.height / 2;
    const deltaX = gridCol - houseCenterCol;
    const deltaY = gridRow - houseCenterRow;
    const theta = (this.garden.northDegrees * Math.PI) / 180;

    const eastMeters = deltaX * Math.cos(theta) - deltaY * Math.sin(theta);
    const northMeters = -(deltaX * Math.sin(theta) + deltaY * Math.cos(theta));

    return this.offsetLatLngFromMeters(this.config.latitude, this.config.longitude, eastMeters, northMeters);
  }

  private computePlotLatLng(plot: Plot): { latitude: number; longitude: number } | null {
    if (plot.grid_col == null || plot.grid_row == null) return null;
    return this.computeGridPointLatLng(plot.grid_col - 0.5, plot.grid_row - 0.5);
  }

  private getShadeRuntime(): ShadeRuntime | null {
    return (this.shadeLayer as unknown as ShadeRuntime | null) ?? null;
  }

  private shadeSimulationUnavailable(): boolean {
    return this.getShadeRuntime()?._simulationUnavailable === true;
  }

  private readShadePixel(point: L.Point): Uint8Array | null {
    const runtime = this.getShadeRuntime();
    const gl = runtime?._gl;
    const canvas = runtime?._canvas;
    if (!gl || !canvas) return null;

    const x = Math.round(point.x);
    const y = Math.round(point.y);
    if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) {
      return null;
    }

    const pixel = new Uint8Array(4);
    const glY = canvas.height - y - 1;
    gl.readPixels(x, glY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    return pixel;
  }

  private sampleTargetPixel(target: TargetPoint): Uint8Array | null {
    if (!this.map) return null;
    const point = this.map.latLngToContainerPoint([target.latitude, target.longitude]);
    return this.readShadePixel(point);
  }

  private pixelIsSun(pixel: Uint8Array | null): boolean | null {
    if (!pixel) return null;
    return pixel[0] === 0 && pixel[1] === 0 && pixel[2] === 0 && pixel[3] === 0;
  }

  private async ensureSunWindow(date: Date): Promise<void> {
    const dayKey = `${date.getMonth() + 1}-${date.getDate()}`;
    if (this.sunWindowDay === dayKey) return;
    try {
      this.sunWindow = await getSunWindowApi(
        date.getMonth() + 1,
        date.getDate(),
      );
      this.sunWindowDay = dayKey;
    } catch (error) {
      console.error("Failed to fetch sun window", error);
      this.sunWindow = null;
      this.sunWindowDay = dayKey;
    }
  }

  private isWithinSunWindow(date: Date): boolean {
    if (!this.sunWindow) return true;
    const { sol_opp, sol_ned } = this.sunWindow;
    if (!sol_opp || !sol_ned) return false;
    const minutes = date.getHours() * 60 + date.getMinutes();
    const [oppH, oppM] = sol_opp.split(":").map(Number);
    const [nedH, nedM] = sol_ned.split(":").map(Number);
    if (oppH == null || oppM == null || nedH == null || nedM == null) {
      return true;
    }
    const oppMinutes = oppH * 60 + oppM;
    const nedMinutes = nedH * 60 + nedM;
    return minutes >= oppMinutes && minutes <= nedMinutes;
  }

  private decodeHoursFromPixel(pixel: Uint8Array | null, durationMs: number): number | null {
    if (!pixel) return null;
    const red = pixel[0] ?? 0;
    const green = pixel[1] ?? 0;
    const blue = pixel[2] ?? 0;
    const r = Math.min(red * 2, 255);
    const g = Math.min(green * 2, 255);
    const b = Math.min(blue * 2, 255);
    let timeShare = 0;
    if (r + g + b !== 0) {
      timeShare = r > 0 ? (r / 255) * 0.5 + 0.5 : b > 0 ? 0.5 * (1 - b / 255) : 0.5;
    }
    return Math.abs((timeShare * durationMs) / 1000 / 3600);
  }

  private computePlotSampleLatLngs(plot: Plot): Array<{ latitude: number; longitude: number }> {
    if (plot.grid_col == null || plot.grid_row == null) return [];
    const centerCol = plot.grid_col - 0.5;
    const centerRow = plot.grid_row - 0.5;
    return PLOT_SAMPLE_OFFSETS.map((offset) => (
      this.computeGridPointLatLng(centerCol + offset.x, centerRow + offset.y)
    ));
  }

  private async computeSunlitPlotSnapshot(epoch: number): Promise<void> {
    if (epoch !== this.sunlightSnapshotEpoch) return;
    if (!this.map || !this.shadeLayer || !this.garden) {
      if (epoch !== this.sunlightSnapshotEpoch) return;
      this.emitSunlightSnapshot([]);
      return;
    }
    const seq = ++this.sunlightSnapshotSeq;

    // Gate per-plot sun detection using the CSV sun window.
    // The ShadeMap shader only ray-traces ~450 m at zoom 17,
    // which can be too short to capture distant terrain obstructions.
    // The CSV provides ground-truth sun-window times.
    if (!this.isWithinSunWindow(this.currentDate)) {
      if (epoch !== this.sunlightSnapshotEpoch || seq !== this.sunlightSnapshotSeq) return;
      this.emitSunlightSnapshot([]);
      return;
    }

    const plots = [...this.garden.plots];
    const runtime = this.getShadeRuntime();
    const gl = runtime?._gl;
    const canvas = runtime?._canvas;
    if (!gl || !canvas) {
      if (epoch !== this.sunlightSnapshotEpoch) return;
      this.emitSunlightSnapshot([]);
      return;
    }

    try {
      this.shadeLayer.flushSync();
      const w = canvas.width;
      const h = canvas.height;

      // Read entire canvas once for fast CPU-side lookups.
      // Individual gl.readPixels calls per sample point are slow
      // due to GPU-CPU sync overhead.
      const allPixels = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, allPixels);

      const sunlight = plots.map((plot) => {
        const samplePoints = this.computePlotSampleLatLngs(plot);
        let sunWeight = 0;
        let totalWeight = 0;
        let centerSun = false;
        let centerSampled = false;
        for (const [index, latLng] of samplePoints.entries()) {
          const point = this.map?.latLngToContainerPoint(
            [latLng.latitude, latLng.longitude],
          );
          if (!point) continue;
          const px = Math.round(point.x);
          const py = Math.round(point.y);
          if (px < 0 || py < 0 || px >= w || py >= h) continue;
          const glY = h - py - 1;
          const alpha = allPixels[(glY * w + px) * 4 + 3];
          const weight = index === 0 ? CENTER_SAMPLE_WEIGHT : 1;
          const isSun = alpha === 0;
          totalWeight += weight;
          if (isSun) sunWeight += weight;
          if (index === 0) {
            centerSampled = true;
            centerSun = isSun;
          }
        }
        if (totalWeight < CENTER_SAMPLE_WEIGHT || !centerSampled || !centerSun) {
          return null;
        }
        const sunPercent = (sunWeight / totalWeight) * 100;
        return sunPercent >= SUN_CLASSIFY_MIN_PERCENT ? plot.plot_id : null;
      });

      const sunIds = sunlight.filter(
        (plotId): plotId is string => typeof plotId === "string",
      );
      if (epoch !== this.sunlightSnapshotEpoch || seq !== this.sunlightSnapshotSeq) return;
      this.emitSunlightSnapshot(sunIds);
    } catch (error) {
      console.error("ShadeMap plot sunlight snapshot failed", error);
      if (epoch !== this.sunlightSnapshotEpoch || seq !== this.sunlightSnapshotSeq) return;
      this.emitSunlightSnapshot([]);
    }
  }

  private async loadEstimatedComparison(): Promise<void> {
    if (this.estimatedComparisonLoaded || this.estimatedComparisonLoading) return;
    this.estimatedComparisonLoading = true;
    this.estimatedComparisonMeta = null;
    this.estimatedComparisonErrorMessage = null;
    this.renderComparisonStatus(t("shade.comparison_loading"));

    try {
      const response = await getShadeMapMonthlyEstimatedSunApi();
      const results: ComparisonResult[] = response.values.map((item: ShadeMapMonthlyEstimateValue) => ({
        monthLabel: Number.isFinite(item.month) ? formatMonthLabel(item.month) : String(item.month),
        hours: item.hours,
      }));
      this.estimatedComparisonResults = results;
      this.estimatedComparisonMeta = {
        sourceName: response.source_name,
        sourceDateStart: response.source_date_start,
        sourceDateEnd: response.source_date_end,
      };
      this.renderComparison(results);
      this.renderComparisonStatus(
        t("shade.comparison_loaded", {
          source: response.source_name,
          start: response.source_date_start,
          end: response.source_date_end,
        }),
      );
      this.estimatedComparisonLoaded = true;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error("Estimated monthly sun load failed", error);
      this.renderComparison([]);
      this.estimatedComparisonErrorMessage = message;
      this.renderComparisonStatus(t("shade.comparison_unavailable", { message }));
    } finally {
      this.estimatedComparisonLoading = false;
    }
  }

  private renderComparison(results: ComparisonResult[]): void {
    const list = document.getElementById("shade-comparison-list");
    if (!list) return;
    if (results.length === 0) {
      const empty = document.createElement("p");
      empty.className = "shade-comparison-empty";
      empty.textContent = t("shade.comparison_empty");
      list.replaceChildren(empty);
      return;
    }
    const maxHours = Math.max(...results.map((item) => item.hours), 0.1);
    const rows = results.map((item) => {
      const width = Math.max(6, Math.round((item.hours / maxHours) * 100));
      const row = document.createElement("div");
      row.className = "shade-comparison-row";

      const month = document.createElement("span");
      month.className = "shade-comparison-month";
      month.textContent = item.monthLabel;

      const bar = document.createElement("span");
      bar.className = "shade-comparison-bar";
      const fill = document.createElement("span");
      fill.style.width = `${width}%`;
      bar.appendChild(fill);

      const hours = document.createElement("span");
      hours.className = "shade-comparison-hours";
      hours.textContent = `${item.hours.toFixed(1)} h`;

      row.append(month, bar, hours);
      return row;
    });
    list.replaceChildren(...rows);
  }

  private renderComparisonStatus(message: string): void {
    const status = document.getElementById("shade-comparison-status");
    if (status) status.textContent = message;
  }

  refreshLocale(): void {
    this.syncInputs();
    this.renderCalibrationEditor();
    this.renderObstacleEditor();
    this.renderComparison(this.estimatedComparisonResults);
    if (this.estimatedComparisonLoading) {
      this.renderComparisonStatus(t("shade.comparison_loading"));
      return;
    }
    if (this.estimatedComparisonMeta) {
      this.renderComparisonStatus(t("shade.comparison_loaded", {
        source: this.estimatedComparisonMeta.sourceName,
        start: this.estimatedComparisonMeta.sourceDateStart,
        end: this.estimatedComparisonMeta.sourceDateEnd,
      }));
      return;
    }
    if (this.estimatedComparisonErrorMessage) {
      this.renderComparisonStatus(t("shade.comparison_unavailable", { message: this.estimatedComparisonErrorMessage }));
      return;
    }
    if (!this.estimatedComparisonLoaded) {
      this.renderComparisonStatus(t("shade.comparison_loading"));
    }
  }

  private getEstimatedDailyHours(): number | null {
    const monthIndex = this.currentDate.getMonth();
    const entry = this.estimatedComparisonResults[monthIndex];
    return entry ? entry.hours : null;
  }

  private setSummary(message: string): void {
    const summary = document.getElementById("shade-summary");
    if (summary) summary.textContent = message;
  }

  private setDebugMessage(message: string): void {
    const debug = document.getElementById("shade-debug");
    if (!debug) return;
    const trimmed = message.trim();
    debug.textContent = trimmed;
    debug.hidden = trimmed.length === 0;
  }

  private canWrite(): boolean {
    return !document.body.classList.contains("garden-read-only");
  }

  private syncWriteAccess(): void {
    const canWrite = this.canWrite();
    const root = this.getRoot();
    if (root) root.dataset["writeAccess"] = canWrite ? "write" : "read-only";
    const editorIds = [
      "shade-cal-house-nw-lat", "shade-cal-house-nw-lng",
      "shade-cal-house-ne-lat", "shade-cal-house-ne-lng",
      "shade-cal-house-se-lat", "shade-cal-house-se-lng",
      "shade-cal-house-sw-lat", "shade-cal-house-sw-lng",
      "shade-calibration-fill-btn", "shade-calibration-save-btn", "shade-calibration-reset-btn",
      "shade-obstacle-label", "shade-obstacle-kind", "shade-obstacle-plot",
      "shade-obstacle-height", "shade-obstacle-radius", "shade-obstacle-lat",
      "shade-obstacle-lng", "shade-obstacle-active", "shade-obstacle-fill-target-btn",
      "shade-obstacle-save-btn", "shade-obstacle-delete-btn",
    ];
    for (const id of editorIds) {
      const control = document.getElementById(id);
      if (control instanceof HTMLButtonElement
        || control instanceof HTMLInputElement
        || control instanceof HTMLSelectElement) {
        control.disabled = !canWrite;
      }
    }
  }

  private publishRenderState(state: "loading" | "ready" | "error", error = ""): void {
    const root = this.getRoot();
    if (!root) return;
    const canvas = this.getShadeRuntime()?._canvas;
    const mapEl = this.getMapElement();
    root.dataset["state"] = state;
    root.dataset["mode"] = this.activeMode;
    root.dataset["preset"] = this.activePreset;
    root.dataset["analysisTimestampMs"] = String(this.currentDate.getTime());
    root.dataset["targetId"] = this.activeTargetId;
    root.dataset["renderRevision"] = String(this.renderRevision);
    root.dataset["simulator"] = this.shadeLayer == null
      ? "pending"
      : this.shadeSimulationUnavailable() ? "unavailable" : "external";
    root.dataset["providerState"] = this.config?.provider_state ?? "unknown";
    root.dataset["sdkCacheStatus"] = this.config?.sdk_cache_status ?? "unknown";
    root.dataset["terrainTokenExpiresAtMs"] = String(this.config?.terrain_token_expires_at_ms ?? 0);
    root.dataset["canvasWidth"] = String(canvas?.width ?? 0);
    root.dataset["canvasHeight"] = String(canvas?.height ?? 0);
    root.dataset["mapWidth"] = String(mapEl?.clientWidth ?? 0);
    root.dataset["mapHeight"] = String(mapEl?.clientHeight ?? 0);
    root.dataset["renderError"] = error;
    root.dispatchEvent(new CustomEvent("gardenops:shade-render-state", {
      bubbles: true,
      detail: { ...root.dataset },
    }));
  }

  private getRoot(): HTMLElement | null {
    return document.getElementById("shade-panel");
  }

  private getMapElement(): HTMLElement | null {
    return document.getElementById("shade-map");
  }

  private isMapRenderable(): boolean {
    const mapEl = this.getMapElement();
    if (!mapEl) return false;
    if (mapEl.getClientRects().length === 0) return false;
    return mapEl.clientWidth > 0 && mapEl.clientHeight > 0;
  }
}
