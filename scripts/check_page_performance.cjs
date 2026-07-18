#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { execFileSync, spawn } = require("node:child_process");
const { performance } = require("node:perf_hooks");
const {
  allowedBrowserOrigins,
  dismissProactivePasskeyPrompt,
  isAllowedUrl,
  signInThroughSessionForm,
} = require("./e2e/completeJourneyBrowser.cjs");

const ROOT = path.resolve(__dirname, "..");
const FRONTEND_DIR = path.join(ROOT, "frontend");
const PLAYWRIGHT_PATH = path.join(
  FRONTEND_DIR,
  "node_modules",
  "playwright-core",
);

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 5177;
const DEFAULT_TIMEOUT_MS = 15_000;
const MOBILE_LAYOUT_BREAKPOINT_PX = 960;
const MANAGED_CHILD_STOP_TIMEOUT_MS = 1_500;
const DEVICE_PROFILES = new Set(["desktop", "pixel-7"]);
const SCENARIOS = new Set(["app-unauth", "app-auth", "app-auth-large-tabs"]);
const LARGE_TAB_TRANSITIONS = [
  { name: "mapToActivityTasks", tab: "activity", readiness: "large-tasks" },
  { name: "activityTasksToGarden", tab: "garden", readiness: "large-garden" },
  { name: "gardenToInsights", tab: "insights", readiness: "large-care" },
  { name: "insightsToMap", tab: "map", readiness: "large-map" },
  { name: "warmMapToActivityTasks", tab: "activity", readiness: "large-tasks" },
  { name: "warmActivityTasksToGarden", tab: "garden", readiness: "large-garden" },
  { name: "warmGardenToInsights", tab: "insights", readiness: "large-care" },
  { name: "warmInsightsToMap", tab: "map", readiness: "large-map" },
];
const LARGE_TAB_BROWSER_TIMING_METRICS = LARGE_TAB_TRANSITIONS.flatMap(({ name }) => [
  `${name}BrowserReadyMs`,
  `${name}BrowserPostFrameMs`,
]);
const LARGE_TAB_LEGACY_NODE_TIMING_METRICS = LARGE_TAB_TRANSITIONS.flatMap(({ name }) => [
  `${name}LegacyNodeReadyObservedMs`,
  `${name}LegacyNodePostFrameObservedMs`,
]);
const LARGE_TAB_PLAYWRIGHT_TIMING_METRICS = LARGE_TAB_TRANSITIONS.map(
  ({ name }) => `${name}PlaywrightActionMs`,
);
const GROWTH_PROBE_CYCLES = 5;
const GROWTH_PROBE_NAVIGATIONS = [
  { tab: "activity", readiness: "large-tasks" },
  { tab: "garden", readiness: "large-garden" },
  { tab: "insights", readiness: "large-care" },
  { tab: "map", readiness: "large-map" },
];
const SCENARIO_METRICS = {
  "app-auth": [
    "appShellReadyMs",
    "appReadyMs",
    "tabSwitchBrowserReadyMs",
    "tabSwitchBrowserPostFrameMs",
    "tabSwitchLegacyNodeReadyObservedMs",
    "tabSwitchLegacyNodePostFrameObservedMs",
    "tabSwitchPlaywrightActionMs",
    "domContentLoadedMs",
    "loadEventMs",
    "firstContentfulPaintMs",
    "apiAppServerDurationMs",
    "apiDecodedResponseBytes",
    "apiEncodedResponseBytes",
    "apiResponseCount",
    "resourceEncodedBytes",
  ],
  "app-auth-large-tabs": [
    "appShellReadyMs",
    "appReadyMs",
    ...LARGE_TAB_BROWSER_TIMING_METRICS,
    ...LARGE_TAB_LEGACY_NODE_TIMING_METRICS,
    ...LARGE_TAB_PLAYWRIGHT_TIMING_METRICS,
    "maxTabSwitchBrowserReadyMs",
    "maxTabSwitchBrowserPostFrameMs",
    "maxTabSwitchLegacyNodeReadyObservedMs",
    "maxTabSwitchLegacyNodePostFrameObservedMs",
    "maxPlaywrightActionMs",
    "maxLongTaskMs",
    "longTaskCount",
    "mountedCareCards",
    "mountedCareRows",
    "mountedPlantCards",
    "mountedPlantRows",
    "domContentLoadedMs",
    "loadEventMs",
    "firstContentfulPaintMs",
    "apiAppServerDurationMs",
    "apiDecodedResponseBytes",
    "apiEncodedResponseBytes",
    "apiResponseCount",
    "repeatedNavigationJsHeapUsedDeltaBytes",
    "repeatedNavigationNodesDelta",
    "resourceEncodedBytes",
  ],
  "app-unauth": [
    "authGateReadyMs",
    "usernameEnterBrowserReadyMs",
    "usernameEnterBrowserPostFrameMs",
    "usernameEnterLegacyNodeReadyObservedMs",
    "usernameEnterLegacyNodePostFrameObservedMs",
    "usernameEnterPlaywrightActionMs",
    "domContentLoadedMs",
    "loadEventMs",
    "firstContentfulPaintMs",
    "apiAppServerDurationMs",
    "apiDecodedResponseBytes",
    "apiEncodedResponseBytes",
    "apiResponseCount",
    "resourceEncodedBytes",
  ],
};

function usage() {
  console.log(`
Usage:
  npm run perf:page -- [options]

Options:
  --serve                         Start a managed Vite server for the run.
  --serve-mode <dev|preview>      Managed server mode. Default: dev.
  --url <url>                     Target URL. Defaults to http://127.0.0.1:5177/ with --serve.
  --host <host>                   Managed server host. Default: ${DEFAULT_HOST}.
  --port <port>                   Managed server port. Default: ${DEFAULT_PORT}.
  --scenario <name>               Scenario to run: app-unauth, app-auth, or app-auth-large-tabs. Default: app-unauth.
  --no-api-stubs                  Measure a real loopback backend session; credentials come from GARDENOPS_PAGE_PERF_USERNAME and GARDENOPS_PAGE_PERF_PASSWORD.
  --skip-interaction              Measure load only; useful when live passkeys intercept Enter.
  --device-profile <name>         Browser context: desktop or pixel-7. Default: desktop.
  --evidence-label <label>        Safe label for live query evidence correlation.
  --minimum-map-plots <count>     Minimum rendered map plots for the large-tab flow. Default: 600.
  --minimum-plant-rows <count>    Minimum non-virtualized plant/care rows. Default: 80.
  --warmup-runs <count>           Discarded warmup runs before measurement. Default: 1.
  --runs <count>                  Measured runs after warmup. Default: 7.
  --skip-growth-probe             Omit the large-tab warmup growth probe for a scale-count-only run.
  --output <path>                 Write JSON results.
  --compare <path>                Compare against a previous JSON result.
  --max-regression-pct <number>   Fail compare mode when a core metric regresses. Default: 5.
  --max-regression-ms <number>    Extra timing jitter allowed in compare mode. Default: 15.
  --navigation-budget-ms <ms>     Optional p75 budget for auth-gate readiness.
  --interaction-budget-ms <ms>    Optional p75 budget for username Enter interaction.
  --tab-switch-budget-ms <ms>     Optional p75 budget for every measured tab switch.
  --rendered-row-budget <count>   Optional budget for mounted Plant/Care rows in large-tab scenario.
  --viewport-width <px>           Browser viewport width. Default: 1440.
  --viewport-height <px>          Browser viewport height. Default: 900.
  --browser <path>                Chromium executable path. Also supports PERF_CHROMIUM.
  --timeout-ms <ms>               Browser and server wait timeout. Default: ${DEFAULT_TIMEOUT_MS}.
  --headful                       Run Chromium with a visible browser.
  --json                          Print only JSON.
  --help                          Show this help.

Examples:
  npm run perf:page -- --serve --runs 5 --output /tmp/gardenops-page-baseline.json
  npm run perf:page -- --serve --serve-mode preview --runs 5 --output /tmp/gardenops-page-prod-baseline.json
  npm run perf:page -- --url https://example.com/ --no-api-stubs --skip-interaction --runs 5
  npm run perf:page -- --serve --compare /tmp/gardenops-page-baseline.json
`);
}

function parsePositiveInt(value, name) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function parseNonNegativeNumber(value, name) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative number`);
  }
  return parsed;
}

function parseArgs(argv) {
  const options = {
    browserPath: process.env.PERF_CHROMIUM || "",
    comparePath: "",
    deviceProfile: "desktop",
    evidenceLabel: "",
    headful: false,
    host: DEFAULT_HOST,
    interactionBudgetMs: null,
    json: false,
    maxRegressionMs: 15,
    maxRegressionPct: 5,
    minimumMapPlots: 600,
    minimumPlantRows: 80,
    navigationBudgetMs: null,
    outputPath: "",
    port: DEFAULT_PORT,
    renderedRowBudget: null,
    runs: 7,
    scenario: "app-unauth",
    serve: false,
    serveMode: "dev",
    skipInteraction: false,
    skipGrowthProbe: false,
    stubApi: true,
    tabSwitchBudgetMs: null,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    url: "",
    viewportHeight: 900,
    viewportWidth: 1440,
    warmupRuns: 1,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };

    if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg === "--serve") {
      options.serve = true;
    } else if (arg === "--serve-mode") {
      options.serveMode = next();
    } else if (arg === "--url") {
      options.url = next();
    } else if (arg === "--host") {
      options.host = next();
    } else if (arg === "--port") {
      options.port = parsePositiveInt(next(), "--port");
    } else if (arg === "--scenario") {
      options.scenario = next();
    } else if (arg === "--no-api-stubs") {
      options.stubApi = false;
    } else if (arg === "--skip-interaction") {
      options.skipInteraction = true;
    } else if (arg === "--device-profile") {
      options.deviceProfile = next();
    } else if (arg === "--evidence-label") {
      options.evidenceLabel = next();
    } else if (arg === "--minimum-map-plots") {
      options.minimumMapPlots = parsePositiveInt(next(), "--minimum-map-plots");
    } else if (arg === "--minimum-plant-rows") {
      options.minimumPlantRows = parsePositiveInt(next(), "--minimum-plant-rows");
    } else if (arg === "--warmup-runs") {
      options.warmupRuns = parsePositiveInt(next(), "--warmup-runs");
    } else if (arg === "--runs") {
      options.runs = parsePositiveInt(next(), "--runs");
    } else if (arg === "--skip-growth-probe") {
      options.skipGrowthProbe = true;
    } else if (arg === "--output") {
      options.outputPath = next();
    } else if (arg === "--compare") {
      options.comparePath = next();
    } else if (arg === "--max-regression-pct") {
      options.maxRegressionPct = parseNonNegativeNumber(
        next(),
        "--max-regression-pct",
      );
    } else if (arg === "--max-regression-ms") {
      options.maxRegressionMs = parseNonNegativeNumber(
        next(),
        "--max-regression-ms",
      );
    } else if (arg === "--navigation-budget-ms") {
      options.navigationBudgetMs = parseNonNegativeNumber(
        next(),
        "--navigation-budget-ms",
      );
    } else if (arg === "--interaction-budget-ms") {
      options.interactionBudgetMs = parseNonNegativeNumber(
        next(),
        "--interaction-budget-ms",
      );
    } else if (arg === "--tab-switch-budget-ms") {
      options.tabSwitchBudgetMs = parseNonNegativeNumber(
        next(),
        "--tab-switch-budget-ms",
      );
    } else if (arg === "--rendered-row-budget") {
      options.renderedRowBudget = parsePositiveInt(
        next(),
        "--rendered-row-budget",
      );
    } else if (arg === "--browser") {
      options.browserPath = next();
    } else if (arg === "--timeout-ms") {
      options.timeoutMs = parsePositiveInt(next(), "--timeout-ms");
    } else if (arg === "--viewport-width") {
      options.viewportWidth = parsePositiveInt(next(), "--viewport-width");
    } else if (arg === "--viewport-height") {
      options.viewportHeight = parsePositiveInt(next(), "--viewport-height");
    } else if (arg === "--headful") {
      options.headful = true;
    } else if (arg === "--json") {
      options.json = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }

  if (!SCENARIOS.has(options.scenario)) {
    throw new Error(`Unknown scenario: ${options.scenario}`);
  }
  if (!DEVICE_PROFILES.has(options.deviceProfile)) {
    throw new Error(`Unknown device profile: ${options.deviceProfile}`);
  }
  if (!["dev", "preview"].includes(options.serveMode)) {
    throw new Error(`Unknown serve mode: ${options.serveMode}`);
  }
  if (!options.url && !options.serve) {
    options.url = `http://${options.host}:${options.port}/`;
  }
  if (options.serve && !options.url) {
    options.url = `http://${options.host}:${options.port}/`;
  }
  validateOptionCompatibility(options);
  return options;
}

function validateOptionCompatibility(options) {
  if (!options.stubApi) {
    let target;
    try {
      target = new URL(options.url);
    } catch {
      throw new Error("--no-api-stubs requires an absolute loopback target URL");
    }
    if (
      !["127.0.0.1", "localhost"].includes(target.hostname)
      || target.protocol !== "http:"
      || !target.port
      || Number(target.port) === 5432
    ) {
      throw new Error("--no-api-stubs requires an HTTP loopback target on a non-5432 port");
    }
  }
  if (options.evidenceLabel && !/^[a-z0-9-]{1,80}$/.test(options.evidenceLabel)) {
    throw new Error("--evidence-label must use lowercase letters, digits, and hyphens only");
  }
  if (options.evidenceLabel && options.stubApi) {
    throw new Error("--evidence-label requires --no-api-stubs");
  }
  if (options.skipGrowthProbe && options.scenario !== "app-auth-large-tabs") {
    throw new Error("--skip-growth-probe requires --scenario app-auth-large-tabs");
  }
  if (
    (options.minimumMapPlots !== 600 || options.minimumPlantRows !== 80)
    && options.scenario !== "app-auth-large-tabs"
  ) {
    throw new Error(
      "--minimum-map-plots and --minimum-plant-rows require --scenario app-auth-large-tabs",
    );
  }
  if (options.tabSwitchBudgetMs !== null) {
    if (!new Set(["app-auth", "app-auth-large-tabs"]).has(options.scenario)) {
      throw new Error(
        "--tab-switch-budget-ms requires an app-auth or app-auth-large-tabs scenario",
      );
    }
    if (options.skipInteraction) {
      throw new Error(
        "--tab-switch-budget-ms requires measured tab transitions; remove --skip-interaction",
      );
    }
  }
  if (options.interactionBudgetMs !== null && options.skipInteraction) {
    throw new Error(
      "--interaction-budget-ms requires a measured interaction; remove --skip-interaction",
    );
  }
}

function liveCredentialsFor(options) {
  if (options.stubApi || options.scenario === "app-unauth") return null;
  const credentials = Object.fromEntries([
    ["password", process.env.GARDENOPS_PAGE_PERF_PASSWORD || ""],
    ["username", process.env.GARDENOPS_PAGE_PERF_USERNAME || ""],
  ]);
  if (!credentials.username || !credentials.password) {
    throw new Error(
      "--no-api-stubs authenticated scenarios require GARDENOPS_PAGE_PERF_USERNAME and GARDENOPS_PAGE_PERF_PASSWORD",
    );
  }
  return credentials;
}

function liveGardenNameFor(options) {
  if (options.stubApi || options.scenario === "app-unauth") return "";
  return process.env.GARDENOPS_PAGE_PERF_GARDEN_NAME || "";
}

async function installLiveNetworkGuard(context, options) {
  const allowedOrigins = allowedBrowserOrigins({ baseUrl: options.url });
  const blockedRequests = [];
  await context.route("**/*", async (route) => {
    if (isAllowedUrl(route.request().url(), allowedOrigins)) {
      await route.continue();
      return;
    }
    blockedRequests.push(route.request().url());
    await route.abort();
  });
  return {
    assertNoBlockedRequests() {
      if (blockedRequests.length > 0) {
        throw new Error(`Live performance run attempted ${blockedRequests.length} non-loopback request(s)`);
      }
    },
  };
}

function roundMs(value) {
  return Number.isFinite(value) ? Math.round(value * 10) / 10 : null;
}

function percentile(values, pct) {
  const sorted = values
    .filter((value) => Number.isFinite(value))
    .slice()
    .sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const index = (sorted.length - 1) * pct;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return roundMs(sorted[lower]);
  const weight = index - lower;
  return roundMs(sorted[lower] * (1 - weight) + sorted[upper] * weight);
}

function metricStats(runs, metricName) {
  const values = runs
    .map((run) => run.timings[metricName])
    .filter((value) => Number.isFinite(value));
  return {
    n: values.length,
    min: percentile(values, 0),
    median: percentile(values, 0.5),
    p75: percentile(values, 0.75),
    max: percentile(values, 1),
  };
}

async function createCdpMetricsSession(page) {
  try {
    const session = await page.context().newCDPSession(page);
    await session.send("Performance.enable");
    return session;
  } catch {
    return null;
  }
}

async function collectCdpMetrics(session) {
  if (!session) return null;
  try {
    const result = await session.send("Performance.getMetrics");
    return Object.fromEntries(
      result.metrics.map((metric) => [metric.name, metric.value]),
    );
  } catch {
    return null;
  }
}

async function collectGarbage(session) {
  if (!session) return false;
  try {
    await session.send("HeapProfiler.collectGarbage");
    return true;
  } catch {
    return false;
  }
}

async function waitForAnimationFrames(page, count = 2) {
  await page.evaluate(async (frameCount) => {
    for (let frame = 0; frame < frameCount; frame += 1) {
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }
  }, count);
}

function metricDeltaMs(before, after, name) {
  const beforeValue = before?.[name];
  const afterValue = after?.[name];
  if (!Number.isFinite(beforeValue) || !Number.isFinite(afterValue)) {
    return null;
  }
  return roundMs((afterValue - beforeValue) * 1_000);
}

function metricDeltaCount(before, after, name) {
  const beforeValue = before?.[name];
  const afterValue = after?.[name];
  if (!Number.isFinite(beforeValue) || !Number.isFinite(afterValue)) {
    return null;
  }
  return roundMs(afterValue - beforeValue);
}

function summarizeCdpDelta(before, after) {
  const recalcStyleDurationMs = metricDeltaMs(before, after, "RecalcStyleDuration");
  const layoutDurationMs = metricDeltaMs(before, after, "LayoutDuration");
  const styleLayoutDurationMs = Number.isFinite(recalcStyleDurationMs)
    && Number.isFinite(layoutDurationMs)
    ? roundMs(recalcStyleDurationMs + layoutDurationMs)
    : null;
  return {
    jsHeapUsedDeltaBytes: metricDeltaCount(before, after, "JSHeapUsedSize"),
    layoutCount: metricDeltaCount(before, after, "LayoutCount"),
    layoutDurationMs,
    nodesDelta: metricDeltaCount(before, after, "Nodes"),
    recalcStyleCount: metricDeltaCount(before, after, "RecalcStyleCount"),
    recalcStyleDurationMs,
    scriptDurationMs: metricDeltaMs(before, after, "ScriptDuration"),
    styleLayoutDurationMs,
    taskDurationMs: metricDeltaMs(before, after, "TaskDuration"),
  };
}

async function installBrowserInteractionTiming(page, {
  readiness,
  targetSelector,
  timeoutMs,
}) {
  return page.evaluate(({
    readiness: readinessConfig,
    targetSelector: selector,
    timeoutMs: browserTimeoutMs,
  }) => {
    const target = document.querySelector(selector);
    if (!(target instanceof HTMLElement)) {
      throw new Error(`Could not instrument interaction target ${selector}`);
    }
    const state = window.__gardenopsPerfInteractionTiming ??= {
      entries: {},
      nextId: 0,
    };
    const id = String(++state.nextId);
    state.entries[id] = {
      intentAtMs: null,
      intentEvent: null,
      postFrameAtMs: null,
      preparedAtMs: performance.now(),
      readyAtMs: null,
      readiness: readinessConfig.name,
      status: "armed",
    };
    const entry = state.entries[id];
    let mutationObserver = null;
    let pollFrame = null;
    let timeoutId = null;

    const cleanup = () => {
      target.removeEventListener("pointerdown", recordIntent, true);
      target.removeEventListener("click", recordIntent, true);
      mutationObserver?.disconnect();
      if (pollFrame !== null) cancelAnimationFrame(pollFrame);
      if (timeoutId !== null) clearTimeout(timeoutId);
    };

    const matchesReadiness = () => {
      const args = readinessConfig.args ?? {};
      if (readinessConfig.name === "auth-password-step") {
        const passwordInput = document.querySelector(args.passwordSelector);
        const submitButton = document.querySelector(args.submitSelector);
        const label = passwordInput?.closest("label");
        return (
          passwordInput instanceof HTMLInputElement
          && label instanceof HTMLElement
          && !label.hidden
          && submitButton?.textContent?.trim() === "Login"
          && document.activeElement === passwordInput
        );
      }
      if (readinessConfig.name === "app-garden-tab") {
        const plantsView = document.querySelector("#plants-view");
        const gardenTab = document.querySelector(
          args.selectedTabSelector ?? "#top-tab-garden",
        );
        const gardenTabSelected = gardenTab?.getAttribute("aria-selected") === "true"
          || gardenTab?.getAttribute("aria-current") === "page";
        const tableBody = document.querySelector("#plants-table-body");
        const mobileList = document.querySelector("#plants-mobile-list");
        const isMobile = window.innerWidth <= args.mobileLayoutBreakpointPx;
        const tableReady = tableBody?.querySelectorAll("tr").length === 1;
        const mobileReady = mobileList?.querySelectorAll(".mobile-data-card").length === 1;
        return (
          plantsView instanceof HTMLElement
          && !plantsView.hidden
          && gardenTabSelected
          && (isMobile ? mobileReady : tableReady)
        );
      }
      if (readinessConfig.name === "large-map") {
        const grid = document.querySelector("#map-grid");
        const activeMapTab = document.querySelector("#top-tab-map");
        const mapView = document.querySelector("#map-view");
        const minimumMapPlots = Number(args.minimumMapPlots ?? 600);
        return (
          grid instanceof HTMLElement
          && mapView instanceof HTMLElement
          && !mapView.hidden
          && !grid.querySelector(".map-grid-loading")
          && grid.querySelectorAll(".plot").length >= minimumMapPlots
          && (activeMapTab?.getAttribute("aria-selected") === "true"
            || activeMapTab?.getAttribute("aria-current") === "page")
        );
      }
      if (readinessConfig.name === "large-garden") {
        const plantsView = document.querySelector("#plants-view");
        const gardenTab = document.querySelector("#top-tab-garden");
        const tableBody = document.querySelector("#plants-table-body");
        const mobileList = document.querySelector("#plants-mobile-list");
        const minimumPlantRows = Number(args.minimumPlantRows ?? 80);
        const isMobile = window.innerWidth <= args.mobileLayoutBreakpointPx;
        const tableReady = tableBody instanceof HTMLElement
          && tableBody.dataset.renderReady === "true"
          && (
            tableBody.dataset.renderMode === "virtual"
              ? tableBody.querySelectorAll("tr[data-virtual-row]").length > 0
              : tableBody.querySelectorAll("tr").length >= minimumPlantRows
          );
        const mobileReady = mobileList instanceof HTMLElement
          && mobileList.dataset.renderReady === "true"
          && (
            mobileList.dataset.renderMode === "virtual"
              ? mobileList.querySelectorAll("[data-virtual-item]").length > 0
              : mobileList.querySelectorAll(".mobile-data-card").length >= minimumPlantRows
          );
        return (
          plantsView instanceof HTMLElement
          && !plantsView.hidden
          && (gardenTab?.getAttribute("aria-selected") === "true"
            || gardenTab?.getAttribute("aria-current") === "page")
          && (isMobile ? mobileReady : tableReady)
        );
      }
      if (readinessConfig.name === "large-care") {
        const careView = document.querySelector("#care-view");
        const insightsTab = document.querySelector("#top-tab-insights");
        const tableBody = document.querySelector("#care-table-body");
        const mobileList = document.querySelector("#care-mobile-list");
        const minimumPlantRows = Number(args.minimumPlantRows ?? 80);
        const isMobile = window.innerWidth <= args.mobileLayoutBreakpointPx;
        const tableReady = tableBody instanceof HTMLElement
          && tableBody.dataset.renderReady === "true"
          && (
            tableBody.dataset.renderMode === "virtual"
              ? tableBody.querySelectorAll("tr[data-virtual-row]").length > 0
              : tableBody.querySelectorAll("tr").length >= minimumPlantRows
          );
        const mobileReady = mobileList instanceof HTMLElement
          && mobileList.dataset.renderReady === "true"
          && (
            mobileList.dataset.renderMode === "virtual"
              ? mobileList.querySelectorAll("[data-virtual-item]").length > 0
              : mobileList.querySelectorAll(".care-mobile-card").length >= minimumPlantRows
          );
        return (
          careView instanceof HTMLElement
          && !careView.hidden
          && (insightsTab?.getAttribute("aria-selected") === "true"
            || insightsTab?.getAttribute("aria-current") === "page")
          && (isMobile ? mobileReady : tableReady)
        );
      }
      if (readinessConfig.name === "large-tasks") {
        const activityTab = document.querySelector("#top-tab-activity");
        const taskContent = document.querySelector("#tasks-tab-content");
        const taskList = document.querySelector("#tasks-list");
        return (
          taskContent instanceof HTMLElement
          && !taskContent.hidden
          && taskList instanceof HTMLElement
          && taskList.querySelector("[data-task-id]") !== null
          && (activityTab?.getAttribute("aria-selected") === "true"
            || activityTab?.getAttribute("aria-current") === "page")
        );
      }
      throw new Error(`Unknown browser readiness predicate ${readinessConfig.name}`);
    };

    const finishWithError = (message) => {
      if (entry.status === "complete" || entry.status === "timed-out") return;
      entry.error = message;
      entry.status = "error";
      cleanup();
    };

    const stampPostFrameProxy = () => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (entry.status !== "ready") return;
          entry.postFrameAtMs = performance.now();
          entry.status = "complete";
          cleanup();
        });
      });
    };

    const checkReadiness = () => {
      if (entry.status !== "armed" || !Number.isFinite(entry.intentAtMs)) return;
      try {
        if (!matchesReadiness()) return;
      } catch (err) {
        finishWithError(`Browser readiness predicate failed: ${err.message}`);
        return;
      }
      entry.readyAtMs = performance.now();
      entry.status = "ready";
      mutationObserver?.disconnect();
      if (pollFrame !== null) cancelAnimationFrame(pollFrame);
      stampPostFrameProxy();
    };

    const pollReadiness = () => {
      checkReadiness();
      if (entry.status === "armed") {
        pollFrame = requestAnimationFrame(pollReadiness);
      }
    };

    const recordIntent = (event) => {
      if (entry.status !== "armed" || Number.isFinite(entry.intentAtMs)) return;
      entry.intentAtMs = performance.now();
      entry.intentEvent = event.type;
      checkReadiness();
    };

    target.addEventListener("pointerdown", recordIntent, true);
    target.addEventListener("click", recordIntent, true);
    mutationObserver = new MutationObserver(checkReadiness);
    mutationObserver.observe(document.documentElement, {
      attributes: true,
      characterData: true,
      childList: true,
      subtree: true,
    });
    pollFrame = requestAnimationFrame(pollReadiness);
    timeoutId = window.setTimeout(() => {
      if (entry.status === "complete") return;
      entry.error = `Timed out waiting for browser readiness predicate ${readinessConfig.name}`;
      entry.status = "timed-out";
      cleanup();
    }, browserTimeoutMs);
    return id;
  }, { readiness, targetSelector, timeoutMs });
}

async function waitForBrowserInteractionState(
  page,
  interactionId,
  state,
  timeoutMs,
) {
  const timingHandle = await page.waitForFunction(
    ({ id, desiredState }) => {
      const entry = window.__gardenopsPerfInteractionTiming?.entries?.[id];
      if (!entry) {
        return { error: `Missing browser interaction timing ${id}`, status: "missing" };
      }
      const reachedState = desiredState === "ready"
        ? Number.isFinite(entry.readyAtMs)
        : entry.status === "complete";
      if (
        !reachedState
        && entry.status !== "error"
        && entry.status !== "timed-out"
      ) {
        return false;
      }
      return {
        error: entry.error ?? null,
        intentAtMs: entry.intentAtMs,
        intentEvent: entry.intentEvent,
        postFrameAtMs: entry.postFrameAtMs,
        preparedAtMs: entry.preparedAtMs,
        readyAtMs: entry.readyAtMs,
        readiness: entry.readiness,
        status: entry.status,
      };
    },
    { desiredState: state, id: interactionId },
    { timeout: timeoutMs + 1_000 },
  );
  try {
    const timing = await timingHandle.jsonValue();
    if (timing.status === "missing" || timing.status === "error" || timing.status === "timed-out") {
      throw new Error(timing.error ?? `Browser interaction timing did not reach ${state}`);
    }
    return timing;
  } finally {
    await timingHandle.dispose();
  }
}

async function discardBrowserInteractionTiming(page, interactionId) {
  try {
    await page.evaluate((id) => {
      const state = window.__gardenopsPerfInteractionTiming;
      if (state?.entries) delete state.entries[id];
    }, interactionId);
  } catch {
    // The page can already be closed after an interaction failure.
  }
}

function summarizeBrowserInteractionTiming(timing) {
  const intentAtMs = timing?.intentAtMs;
  const readyAtMs = timing?.readyAtMs;
  const postFrameAtMs = timing?.postFrameAtMs;
  return {
    browserIntentEvent: typeof timing?.intentEvent === "string"
      ? timing.intentEvent
      : null,
    browserIntentToPostFrameMs: Number.isFinite(intentAtMs)
      && Number.isFinite(postFrameAtMs)
      ? roundMs(postFrameAtMs - intentAtMs)
    : null,
    browserIntentToReadyMs: Number.isFinite(intentAtMs)
      && Number.isFinite(readyAtMs)
      ? roundMs(readyAtMs - intentAtMs)
      : null,
  };
}

async function clickAndMeasureInteraction(page, targetSelector, readiness, timeoutMs) {
  const interactionId = await installBrowserInteractionTiming(page, {
    readiness,
    targetSelector,
    timeoutMs,
  });
  const legacyStartedAt = performance.now();
  let completed = false;
  try {
    const clickStartedAt = performance.now();
    await page.click(targetSelector, { timeout: timeoutMs });
    const playwrightActionMs = performance.now() - clickStartedAt;
    await waitForBrowserInteractionState(page, interactionId, "ready", timeoutMs);
    const legacyNodeReadyObservedMs = performance.now() - legacyStartedAt;
    const browserTiming = await waitForBrowserInteractionState(
      page,
      interactionId,
      "complete",
      timeoutMs,
    );
    const legacyNodePostFrameObservedMs = performance.now() - legacyStartedAt;
    await discardBrowserInteractionTiming(page, interactionId);
    completed = true;
    return {
      browserTiming,
      browserTimingSummary: summarizeBrowserInteractionTiming(browserTiming),
      legacyNodePostFrameObservedMs,
      legacyNodeReadyObservedMs,
      playwrightActionMs,
    };
  } finally {
    if (!completed) await discardBrowserInteractionTiming(page, interactionId);
  }
}

async function collectDomSnapshot(page) {
  return page.evaluate(() => {
    const countNodes = (selector) => {
      const root = document.querySelector(selector);
      return root instanceof HTMLElement
        ? root.getElementsByTagName("*").length + 1
        : 0;
    };
    return {
      careViewNodes: countNodes("#care-view"),
      mapViewNodes: countNodes("#map-view"),
      plantRows: document.querySelector("#plants-table-body")?.querySelectorAll("tr").length ?? 0,
      plantsViewNodes: countNodes("#plants-view"),
      plotCount: document.querySelector("#map-grid")?.querySelectorAll(".plot").length ?? 0,
      statsViewNodes: countNodes("#statistics-view"),
      totalNodes: document.getElementsByTagName("*").length,
    };
  });
}

async function waitForScenarioReadiness(page, predicate, {
  label,
  timeoutMs,
}) {
  try {
    await page.waitForFunction(predicate, undefined, { timeout: timeoutMs });
  } catch (error) {
    let state = null;
    try {
      state = await page.evaluate(() => ({
        activeTab: document.querySelector("[role='tab'][aria-selected='true']")?.id ?? "",
        activeGardenId: sessionStorage.getItem("gardenops-active-garden-id") ?? "",
        appStatus: document.querySelector("#app-status-text")?.textContent?.trim() ?? "",
        careRows: document.querySelector("#care-table-body")?.querySelectorAll("tr").length ?? 0,
        careViewHidden: document.querySelector("#care-view")?.hidden ?? null,
        mapAriaCurrent: document.querySelector("#top-tab-map")?.getAttribute("aria-current") ?? "",
        mapLoading: Boolean(document.querySelector("#map-grid .map-grid-loading")),
        mapPlots: document.querySelector("#map-grid")?.querySelectorAll(".plot").length ?? 0,
        mapViewHidden: document.querySelector("#map-view")?.hidden ?? null,
        plotResources: performance.getEntriesByType("resource")
          .filter((entry) => entry.name.includes("/api/plots"))
          .map((entry) => entry.name.replace(/^https?:\/\/[^/]+/, "")),
        plantRows: document.querySelector("#plants-table-body")?.querySelectorAll("tr").length ?? 0,
        plantsViewHidden: document.querySelector("#plants-view")?.hidden ?? null,
      }));
    } catch {
      // Preserve the original timeout if the page has already closed.
    }
    throw new Error(
      `${label} readiness did not settle (${errorMessage(error)}): ${JSON.stringify(state)}`,
      {
      cause: error,
      },
    );
  }
}

function summarizeRuns(runs, scenario) {
  const metricNames = new Set(SCENARIO_METRICS[scenario] ?? []);
  for (const run of runs) {
    Object.keys(run.timings).forEach((metricName) => {
      metricNames.add(metricName);
    });
  }
  const metrics = {};
  for (const metricName of metricNames) {
    metrics[metricName] = metricStats(runs, metricName);
  }

  const lastRun = runs[runs.length - 1];
  return {
    metrics,
    lastRunResources: lastRun?.resources ?? null,
  };
}

function createBrowserContextOptions(options) {
  if (options.deviceProfile === "pixel-7") {
    const { devices } = require(PLAYWRIGHT_PATH);
    const pixel7 = devices["Pixel 7"];
    if (!pixel7) {
      throw new Error("Playwright Pixel 7 device profile is unavailable");
    }
    const { defaultBrowserType: _defaultBrowserType, ...contextOptions } = pixel7;
    return contextOptions;
  }
  return {
    deviceScaleFactor: 1,
    hasTouch: false,
    isMobile: false,
    viewport: {
      height: options.viewportHeight,
      width: options.viewportWidth,
    },
  };
}

function buildViewportProfile(options) {
  const contextOptions = createBrowserContextOptions(options);
  const deviceProfile = options.deviceProfile ?? "desktop";
  const mobileDeviceEmulation = contextOptions.isMobile === true;
  const responsiveMobileBreakpoint = mobileDeviceEmulation
    || contextOptions.viewport.width <= MOBILE_LAYOUT_BREAKPOINT_PX;
  return {
    deviceProfile,
    label: mobileDeviceEmulation
      ? "Playwright Pixel 7 mobile emulation"
      : responsiveMobileBreakpoint
        ? "responsive mobile-breakpoint desktop Chromium"
        : "desktop-breakpoint desktop Chromium",
    mobileDeviceEmulation,
    responsiveLayout: responsiveMobileBreakpoint
      ? "mobile-breakpoint"
      : "desktop-breakpoint",
    strategy: mobileDeviceEmulation ? "playwright-device-descriptor" : "viewport-only",
    viewport: { ...contextOptions.viewport },
  };
}

function tabSelectorForViewport(options, tab) {
  const viewportProfile = buildViewportProfile(options);
  return viewportProfile.responsiveLayout === "mobile-breakpoint"
    ? `#mobile-tab-${tab}`
    : `#top-tab-${tab}`;
}

function buildMeasurementMetadata(options) {
  const viewportProfile = buildViewportProfile(options);
  const contextOptions = createBrowserContextOptions(options);
  return {
    browserContext: {
      ...contextOptions,
      mobileEmulation: {
        deviceProfile: viewportProfile.deviceProfile,
        enabled: viewportProfile.mobileDeviceEmulation,
        responsiveBreakpointPx: MOBILE_LAYOUT_BREAKPOINT_PX,
        responsiveLayout: viewportProfile.responsiveLayout,
        strategy: viewportProfile.strategy,
      },
      userAgentOverride: contextOptions.userAgent ?? null,
      viewportProfile,
    },
    interactionTiming: {
      browserPostFrameFields: {
        end: "Browser performance clock after readiness and a browser-scheduled double requestAnimationFrame.",
        interpretation: "Post-frame proxy only; it is not a guaranteed first presentation timestamp.",
        start: "Target pointerdown; target click is a fallback when pointerdown is unavailable.",
      },
      legacyNodeFields: {
        deprecatedDetailAliases: "tabSwitchDetails.presentedMs, readyMs, and wallMs are retained legacy Node diagnostic aliases; use the explicitly named *LegacyNode*Ms fields for new consumers.",
        description: "*LegacyNode*Ms fields are Node wall-clock observations from immediately before Playwright click through browser readiness or post-frame completion. They include protocol delay and actionability overhead and are diagnostic-only.",
      },
      playwrightActionFields: {
        description: "*PlaywrightActionMs is the Playwright click call duration, including locator/actionability overhead. It is diagnostic-only and not browser render time.",
      },
    },
    schemaVersion: 6,
  };
}

function readGitBuffer(args) {
  try {
    return execFileSync("git", args, {
      cwd: ROOT,
      maxBuffer: 64 * 1024 * 1024,
      stdio: ["ignore", "pipe", "ignore"],
    });
  } catch {
    return null;
  }
}

function readGitOutput(args) {
  const output = readGitBuffer(args);
  return output === null ? null : output.toString("utf8").trim();
}

function collectDirtyTreeContentHash() {
  const diff = readGitBuffer(["diff", "--no-ext-diff", "--binary", "HEAD", "--"]);
  const untracked = readGitBuffer(["ls-files", "--others", "--exclude-standard", "-z"]);
  if (diff === null || untracked === null) return null;

  const hash = createHash("sha256");
  hash.update("git-diff-head\0");
  hash.update(diff);
  hash.update("untracked-files\0");
  for (const relativePath of untracked.toString("utf8").split("\u0000")) {
    if (!relativePath) continue;
    const filePath = path.resolve(ROOT, relativePath);
    if (!filePath.startsWith(`${ROOT}${path.sep}`)) return null;
    try {
      hash.update(relativePath);
      hash.update("\0");
      hash.update(fs.readFileSync(filePath));
      hash.update("\0");
    } catch {
      return null;
    }
  }
  return hash.digest("hex");
}

function collectGitProvenance() {
  const status = readGitOutput(["status", "--porcelain=v1", "--untracked-files=all"]);
  const dirty = status === null ? null : status.length > 0;
  return {
    contentHash: dirty ? collectDirtyTreeContentHash() : null,
    dirty,
    revision: readGitOutput(["rev-parse", "HEAD"]),
  };
}

function buildComparisonProvenance({ browserPath, browserVersion, options }) {
  return {
    apiMode: options.stubApi ? "stub" : "live",
    browser: {
      engine: "chromium",
      path: browserPath,
      version: browserVersion,
    },
    options: {
      deviceProfile: options.deviceProfile,
      headful: options.headful,
      interactionMode: options.skipInteraction ? "load-only" : "measured",
      minimumMapPlots: options.minimumMapPlots,
      minimumPlantRows: options.minimumPlantRows,
      serveMode: options.serve ? options.serveMode : "external",
      skipGrowthProbe: options.skipGrowthProbe,
      targetUrl: options.url,
      timeoutMs: options.timeoutMs,
    },
    scenario: options.scenario,
    viewportProfile: buildViewportProfile(options),
  };
}

function buildReproducibilityProvenance({
  argv,
  browserPath,
  browserVersion,
  options,
}) {
  return {
    browser: {
      path: browserPath,
      version: browserVersion,
    },
    comparison: buildComparisonProvenance({
      browserPath,
      browserVersion,
      options,
    }),
    evidence: {
      durableCiEvidence: false,
      note: "This JSON is local diagnostic evidence, not durable CI evidence unless a CI job produces and retains it.",
    },
    git: collectGitProvenance(),
    invocation: {
      argv: [...argv],
      effectiveOptions: { ...options },
    },
    runCount: options.runs,
    warmupRunCount: options.warmupRuns,
    viewportProfile: buildViewportProfile(options),
  };
}

function findBrowserPath(explicitPath) {
  const candidates = [
    explicitPath,
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return "";
}

function isReadyStatus(status) {
  return Number.isInteger(status) && status >= 200 && status < 400;
}

function request(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      res.on("end", () => resolve({
        ok: isReadyStatus(res.statusCode),
        status: res.statusCode,
      }));
    });
    req.on("error", (err) => resolve({ ok: false, error: err.message }));
    req.setTimeout(1_000, () => {
      req.destroy();
      resolve({ ok: false, error: "timeout" });
    });
  });
}

function hasChildExited(child) {
  return Boolean(child) && (
    (child.exitCode !== null && child.exitCode !== undefined)
    || (child.signalCode !== null && child.signalCode !== undefined)
  );
}

function waitForChildExit(child, timeoutMs) {
  if (hasChildExited(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (exited) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeListener("exit", onExit);
      child.removeListener("error", onError);
      resolve(exited);
    };
    const onExit = () => finish(true);
    const onError = () => finish(true);
    const timer = setTimeout(() => finish(hasChildExited(child)), timeoutMs);
    child.once("exit", onExit);
    child.once("error", onError);
    if (hasChildExited(child)) finish(true);
  });
}

function processGroupExists(pid) {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (err) {
    if (err.code === "ESRCH") return false;
    throw err;
  }
}

async function waitForProcessGroupExit(pid, timeoutMs) {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    if (!processGroupExists(pid)) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return !processGroupExists(pid);
}

function signalManagedChild(child, signal) {
  if (!child) return false;
  const pid = child.pid;
  if (Number.isInteger(pid) && process.platform !== "win32") {
    try {
      process.kill(-pid, signal);
      return true;
    } catch (err) {
      if (err.code === "ESRCH") return false;
      throw err;
    }
  }
  if (hasChildExited(child)) return false;
  try {
    const delivered = child.kill(signal);
    if (delivered || hasChildExited(child)) return true;
  } catch (err) {
    throw new Error(`Could not send ${signal} to managed server process: ${err.message}`);
  }
  throw new Error(`Could not send ${signal} to managed server process`);
}

async function stopManagedChild(child) {
  if (!child) return;
  const pid = child.pid;
  if (Number.isInteger(pid) && process.platform !== "win32") {
    if (!processGroupExists(pid)) return;
    signalManagedChild(child, "SIGTERM");
    if (await waitForProcessGroupExit(pid, MANAGED_CHILD_STOP_TIMEOUT_MS)) return;
    signalManagedChild(child, "SIGKILL");
    if (await waitForProcessGroupExit(pid, MANAGED_CHILD_STOP_TIMEOUT_MS)) return;
    throw new Error("Managed server process group did not exit after SIGKILL");
  }
  if (hasChildExited(child)) return;
  signalManagedChild(child, "SIGTERM");
  if (await waitForChildExit(child, MANAGED_CHILD_STOP_TIMEOUT_MS)) return;
  signalManagedChild(child, "SIGKILL");
  if (await waitForChildExit(child, MANAGED_CHILD_STOP_TIMEOUT_MS)) return;
  throw new Error("Managed server process did not exit after SIGKILL");
}

async function waitForServer(
  url,
  timeoutMs,
  child,
  readServerLog,
  readChildError = () => null,
) {
  const startedAt = performance.now();
  while (performance.now() - startedAt < timeoutMs) {
    const childError = readChildError();
    if (childError) {
      throw new Error(
        `Vite server failed before becoming ready: ${childError.message}.\n${readServerLog()}`,
      );
    }
    if (hasChildExited(child)) {
      throw new Error(
        `Vite server exited before becoming ready.\n${readServerLog()}`,
      );
    }
    const result = await request(url);
    if (result.ok) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${url}.\n${readServerLog()}`);
}

async function startServer(options) {
  let serverLog = "";
  let childError = null;
  const command = options.serveMode === "preview" ? "preview" : "dev";
  const child = spawn(
    "npm",
    [
      "run",
      command,
      "--",
      "--host",
      options.host,
      "--port",
      String(options.port),
      "--strictPort",
    ],
    {
      cwd: FRONTEND_DIR,
      detached: process.platform !== "win32",
      env: { ...process.env, BROWSER: "none" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const appendLog = (chunk) => {
    serverLog += chunk.toString();
    if (serverLog.length > 6_000) {
      serverLog = serverLog.slice(serverLog.length - 6_000);
    }
  };
  child.stdout.on("data", appendLog);
  child.stderr.on("data", appendLog);
  child.on("error", (err) => {
    childError = err;
    appendLog(`${err.message}\n`);
  });
  const server = {
    child,
    stop: () => stopManagedChild(child),
  };
  try {
    await waitForServer(
      options.url,
      options.timeoutMs,
      child,
      () => serverLog,
      () => childError,
    );
    return server;
  } catch (err) {
    try {
      await server.stop();
    } catch (cleanupErr) {
      err.message = `${err.message}\nFailed to clean up managed server: ${cleanupErr.message}`;
    }
    throw err;
  }
}

function apiJson(status, body) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

function buildAuthPerformanceData(large) {
  const profile = {
    username: "perf_probe",
    role: "editor",
    garden_id: 1,
    garden_visible: true,
    garden_role: "editor",
    auth_type: "session",
    write_access: true,
    language: "en",
    shademap_available: false,
    mfa_enabled: false,
    mfa_setup_required: false,
    mfa_authenticated: true,
    mfa_methods: [],
    must_change_password: false,
    plot_assignment_meanings: [],
    subscription_tier: "enthusiast",
    allowed_features: [
      "map",
      "plots",
      "plants",
      "journal",
      "harvest_basic",
      "theme",
      "snapshots",
      "exports_basic",
      "care",
      "statistics",
      "weather",
    ],
    security_warnings: [],
  };
  const garden = {
    id: 1,
    slug: "perf-garden",
    name: "Performance Garden",
    role: "editor",
    active: true,
    onboarding_complete: true,
    owned_by_current_user: true,
  };

  const makePlant = (index, plotIds) => {
    const categories = ["frø", "løk", "busker", "trær", "baerbusker"];
    const category = categories[index % categories.length];
    return {
      plt_id: `PLT-${String(index + 1).padStart(4, "0")}`,
      name: `Performance Plant ${String(index + 1).padStart(4, "0")}`,
      latin: `Planta perf ${index + 1}`,
      category,
      bloom_month: `${(index % 9) + 3}-${(index % 9) + 4}`,
      color: ["red", "blue", "white", "yellow", "purple"][index % 5],
      hardiness: index % 4 === 0 ? "H3" : "H5",
      height_cm: 20 + (index % 180),
      light: index % 3 === 0 ? "Full sun" : "Partial shade",
      link: "",
      year_planted: String(2020 + (index % 7)),
      deer_resistant: index % 6 === 0,
      care_watering: "Water when the top soil is dry.",
      care_soil: "Well-drained soil.",
      care_planting: "Plant at standard depth.",
      care_maintenance: "Remove spent growth through the season.",
      care_notes: index % 5 === 0 ? "Watch growth after heavy rain." : "",
      quantity: 1 + (index % 3),
      plot_ids: plotIds,
      seen_growing: true,
      seen_growing_date: null,
      seen_growing_year: 2026,
      seen_growing_is_current_year: true,
      observed_this_year: true,
      last_bloomed_on: null,
      last_bloomed_year: null,
      bloomed_this_year: index % 7 === 0,
      presence_status: "present",
      last_not_seen_year: null,
    };
  };

  if (!large) {
    const plots = [
      {
        plot_id: "A1",
        zone_code: "A",
        zone_name: "North Bed",
        plot_number: 1,
        grid_row: 1,
        grid_col: 1,
        sub_zone: "",
        notes: "",
        color: "#8fbf62",
        plant_count: 1,
        has_tree: false,
        has_bush: false,
        categories: ["frø"],
      },
      {
        plot_id: "A2",
        zone_code: "A",
        zone_name: "North Bed",
        plot_number: 2,
        grid_row: 1,
        grid_col: 2,
        sub_zone: "",
        notes: "",
        color: null,
        plant_count: 0,
        has_tree: false,
        has_bush: false,
        categories: [],
      },
      {
        plot_id: "B1",
        zone_code: "B",
        zone_name: "South Bed",
        plot_number: 1,
        grid_row: 2,
        grid_col: 1,
        sub_zone: "",
        notes: "",
        color: "#c4a35a",
        plant_count: 0,
        has_tree: false,
        has_bush: true,
        categories: ["busker"],
      },
    ];
    return {
      garden,
      layoutState: {
        row: 1,
        col: 4,
        width: 3,
        height: 2,
        north_degrees: 0,
        grid_rows: 6,
        grid_cols: 8,
      },
      mapObjects: [],
      plants: [makePlant(0, ["A1"])],
      plots,
      profile,
    };
  }

  const rows = 30;
  const cols = 20;
  const plots = [];
  const plantCounts = new Map();
  for (let row = 1; row <= rows; row += 1) {
    for (let col = 1; col <= cols; col += 1) {
      const index = (row - 1) * cols + col;
      const zoneNumber = Math.floor((row - 1) / 3) + 1;
      const plotId = `Z${String(zoneNumber).padStart(2, "0")}-${String(col).padStart(2, "0")}-${String(row).padStart(2, "0")}`;
      plantCounts.set(plotId, 0);
      plots.push({
        plot_id: plotId,
        zone_code: `Z${String(zoneNumber).padStart(2, "0")}`,
        zone_name: `Zone ${zoneNumber}`,
        plot_number: index,
        grid_row: row,
        grid_col: col,
        sub_zone: "",
        notes: "",
        color: index % 5 === 0 ? "#8fbf62" : null,
        plant_count: 0,
        has_tree: index % 23 === 0,
        has_bush: index % 17 === 0,
        categories: [],
      });
    }
  }

  const plants = [];
  const assignmentCount = 1_028;
  let assigned = 0;
  for (let index = 0; index < 900; index += 1) {
    const primary = plots[(index * 7) % plots.length].plot_id;
    const plotIds = [primary];
    assigned += 1;
    if (assigned < assignmentCount) {
      const secondary = plots[(index * 13 + 41) % plots.length].plot_id;
      if (secondary !== primary) {
        plotIds.push(secondary);
        assigned += 1;
      }
    }
    for (const plotId of plotIds) {
      plantCounts.set(plotId, (plantCounts.get(plotId) ?? 0) + 1);
    }
    plants.push(makePlant(index, plotIds));
  }

  for (const plot of plots) {
    const count = plantCounts.get(plot.plot_id) ?? 0;
    plot.plant_count = count;
    if (count > 0) {
      plot.categories = ["frø"];
    }
  }

  return {
    garden,
    layoutState: {
      row: 2,
      col: 8,
      width: 4,
      height: 3,
      north_degrees: 0,
      grid_rows: rows,
      grid_cols: cols,
    },
    mapObjects: [],
    plants,
    plots,
    profile,
  };
}

async function installScenarioRoutes(context, scenario) {
  if (scenario === "app-unauth") {
    await context.route("**/api/auth/me", (route) => {
      route.fulfill(apiJson(401, { detail: "Not authenticated" }));
    });
    await context.route("**/api/auth/status", (route) => {
      route.fulfill(
        apiJson(200, {
          auth_required: true,
          auth_mode: "session",
          session_auth_enabled: true,
          api_key_auth_enabled: false,
          bootstrap_required: false,
          user_lifecycle_enabled: true,
          admin_mfa_required: false,
          passkeys_enabled: false,
        }),
      );
    });
    return;
  }

  if (scenario !== "app-auth" && scenario !== "app-auth-large-tabs") return;

  const fixture = buildAuthPerformanceData(scenario === "app-auth-large-tabs");

  await context.route("**/api/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/media/summaries") {
      route.fulfill(apiJson(200, {
        target_type: "plant",
        items: [],
      }));
      return;
    }
    if (request.method() !== "GET") {
      route.fulfill(apiJson(405, { detail: "Method not allowed in performance stub" }));
      return;
    }
    const responses = new Map([
      ["/api/auth/me", fixture.profile],
      ["/api/gardens", [fixture.garden]],
      ["/api/gardens/1/map-objects", { objects: fixture.mapObjects }],
      ["/api/version", {
        version: "perf",
        base_version: "perf",
        git_commit: null,
        dirty: false,
        last_updated_at_ms: Date.now(),
      }],
      ["/api/plots", fixture.plots],
      ["/api/layout-state", fixture.layoutState],
      ["/api/plots/elevations", {
        available: false,
        elevations: {},
        overrides: {},
        min_m: null,
        max_m: null,
      }],
      ["/api/plots/alerts", {
        alerts: [],
        plot_ids: [],
        generated_at_ms: Date.now(),
      }],
      ["/api/plants", fixture.plants],
      ["/api/weather/summary", {
        forecast_available: false,
        forecast_days: [],
        alerts: [],
        frost_vulnerable_plants: [],
        watering_sensitive_plants: [],
      }],
    ]);
    const response = responses.get(url.pathname);
    if (response === undefined) {
      route.fulfill(apiJson(404, { detail: `Unhandled performance stub path: ${url.pathname}` }));
      return;
    }
    route.fulfill(apiJson(200, response));
  });
}

function apiResponseDetails(response) {
  const status = response.status();
  if (!Number.isFinite(status)) return null;
  let url;
  try {
    url = new URL(response.url());
  } catch {
    return null;
  }
  if (!url.pathname.startsWith("/api/")) return null;
  let method = "UNKNOWN";
  try {
    method = response.request().method();
  } catch {
    // Keep a usable error diagnostic if a nonstandard response lacks a request.
  }
  return {
    method,
    path: `${url.pathname}${url.search}`,
    pathname: url.pathname,
    status,
  };
}

function apiResponseErrorDetails(response) {
  const details = apiResponseDetails(response);
  return details?.status >= 400 ? details : null;
}

function isExpectedApiErrorResponse(scenario, details) {
  return scenario === "app-unauth"
    && details.method === "GET"
    && details.pathname === "/api/auth/me"
    && details.status === 401;
}

function createApiResponseTracker(page, scenario) {
  const unexpectedResponses = [];
  const pendingCaptures = [];
  const timings = [];
  const parseAppDuration = (header) => {
    if (typeof header !== "string") return null;
    const match = header.match(/(?:^|,)\s*app\s*(?:;[^,]*)?;\s*dur\s*=\s*([0-9]+(?:\.[0-9]+)?)/i);
    if (!match) return null;
    const duration = Number(match[1]);
    return Number.isFinite(duration) && duration >= 0 ? duration : null;
  };
  const onResponse = (response) => {
    const details = apiResponseDetails(response);
    const errorDetails = details?.status >= 400 ? details : null;
    if (errorDetails && !isExpectedApiErrorResponse(scenario, errorDetails)) {
      unexpectedResponses.push(errorDetails);
    }
    if (!details) return;
    const headers = typeof response.allHeaders === "function"
      ? response.allHeaders()
      : Promise.resolve({});
    const capture = headers.then((responseHeaders) => {
      const appDurationMs = parseAppDuration(responseHeaders["server-timing"]);
      timings.push({
        appDurationMs,
        path: details.pathname,
        status: details.status,
      });
    }).catch(() => {
      // Browser failures are collected separately; missing optional timing never hides them.
    });
    pendingCaptures.push(capture);
  };
  page.on("response", onResponse);
  return {
    assertNoUnexpectedResponses() {
      if (unexpectedResponses.length === 0) return;
      throw new Error(
        `Unexpected API response errors in ${scenario}: ${unexpectedResponses
          .map((details) => `${details.method} ${details.path} -> ${details.status}`)
          .join(", ")}`,
      );
    },
    async flush() {
      await Promise.all(pendingCaptures);
    },
    summary() {
      const measured = timings
        .map((entry) => entry.appDurationMs)
        .filter((duration) => Number.isFinite(duration));
      return {
        appServerDurationMs: measured.length > 0
          ? roundMs(measured.reduce((total, duration) => total + duration, 0))
          : null,
        appServerTimedResponseCount: measured.length,
        responseCount: timings.length,
      };
    },
    detach() {
      page.off?.("response", onResponse);
    },
    unexpectedResponses,
  };
}

function byEncodedSizeDesc(left, right) {
  return right.encodedBodySize - left.encodedBodySize;
}

async function collectBrowserMetrics(page) {
  return page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const paints = performance.getEntriesByType("paint");
    const resources = performance.getEntriesByType("resource");
    const firstPaint = paints.find((entry) => entry.name === "first-paint");
    const fcp = paints.find((entry) => entry.name === "first-contentful-paint");
    const resourceRows = resources.map((entry) => ({
      name: entry.name,
      initiatorType: entry.initiatorType,
      duration: entry.duration,
      transferSize: entry.transferSize || 0,
      encodedBodySize: entry.encodedBodySize || 0,
      decodedBodySize: entry.decodedBodySize || 0,
    }));
    const resourceTotals = (entries) => entries.reduce(
      (acc, entry) => {
        acc.transferSize += entry.transferSize;
        acc.encodedBodySize += entry.encodedBodySize;
        acc.decodedBodySize += entry.decodedBodySize;
        acc.count += 1;
        return acc;
      },
      { count: 0, decodedBodySize: 0, encodedBodySize: 0, transferSize: 0 },
    );
    const apiResources = resourceRows.filter((entry) => {
      try {
        return new URL(entry.name).pathname.startsWith("/api/");
      } catch {
        return false;
      }
    });
    const totals = resourceTotals(resourceRows);
    return {
      navigation: nav
        ? {
            domContentLoadedMs: nav.domContentLoadedEventEnd,
            loadEventMs: nav.loadEventEnd,
            responseEndMs: nav.responseEnd,
            transferSize: nav.transferSize || 0,
            encodedBodySize: nav.encodedBodySize || 0,
          }
        : null,
      paints: {
        firstPaintMs: firstPaint?.startTime ?? null,
        firstContentfulPaintMs: fcp?.startTime ?? null,
      },
      resources: {
        api: {
          totals: resourceTotals(apiResources),
        },
        totals,
        byType: resourceRows.reduce((acc, entry) => {
          const key = entry.initiatorType || "other";
          acc[key] ??= { count: 0, encodedBodySize: 0, transferSize: 0 };
          acc[key].count += 1;
          acc[key].encodedBodySize += entry.encodedBodySize;
          acc[key].transferSize += entry.transferSize;
          return acc;
        }, {}),
        largest: resourceRows
          .slice()
          .sort((left, right) => right.encodedBodySize - left.encodedBodySize)
          .slice(0, 12),
      },
    };
  });
}

function trimResourceName(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

function normalizeResources(resources) {
  if (!resources) return null;
  return {
    ...resources,
    largest: resources.largest
      .slice()
      .sort(byEncodedSizeDesc)
      .map((entry) => ({
        ...entry,
        duration: roundMs(entry.duration),
        name: trimResourceName(entry.name),
      })),
  };
}

async function runAppUnauthScenario(page, options) {
  const { timeoutMs, url } = options;
  const startedAt = performance.now();
  const response = await page.goto(url, {
    waitUntil: "domcontentloaded",
    timeout: timeoutMs,
  });
  if (!response || !response.ok()) {
    throw new Error(`Navigation failed with status ${response?.status() ?? "unknown"}`);
  }
  const gotoMs = performance.now() - startedAt;

  const usernameSelector = '#auth-gate-form input[name="username"]';
  const passwordSelector = '#auth-gate-form input[name="password"]';
  const submitSelector = '#auth-gate-form button[type="submit"]';

  await page.waitForSelector(usernameSelector, { state: "visible", timeout: timeoutMs });
  const authGateReadyMs = performance.now() - startedAt;
  const initialFlow = await page.evaluate(
    ({ passwordSelector, submitSelector, usernameSelector }) => {
      const usernameInput = document.querySelector(usernameSelector);
      const passwordInput = document.querySelector(passwordSelector);
      const submitButton = document.querySelector(submitSelector);
      const passwordElement = passwordInput;
      const passwordLabel = passwordElement instanceof HTMLElement
        ? passwordElement.closest("label")
        : null;
      const passwordRendered = passwordLabel instanceof HTMLElement
        && window.getComputedStyle(passwordLabel).display !== "none"
        && passwordLabel.getClientRects().length > 0;
      return {
        activeElementName: document.activeElement?.getAttribute("name") ?? "",
        buttonText: submitButton?.textContent?.trim() ?? "",
        passwordHidden: passwordLabel instanceof HTMLElement
          ? passwordLabel.hidden === true
          : false,
        passwordPlaceholder: passwordElement?.getAttribute("placeholder") ?? "",
        passwordRendered,
        usernamePlaceholder: usernameInput?.getAttribute("placeholder") ?? "",
      };
    },
    { passwordSelector, submitSelector, usernameSelector },
  );

  if (initialFlow.usernamePlaceholder !== "Username") {
    throw new Error(`Expected username placeholder "Username", got "${initialFlow.usernamePlaceholder}"`);
  }
  if (initialFlow.buttonText !== "Enter") {
    throw new Error(`Expected initial submit button "Enter", got "${initialFlow.buttonText}"`);
  }
  if (!initialFlow.passwordHidden) {
    throw new Error("Expected password field to be hidden on username step");
  }
  if (initialFlow.passwordRendered) {
    throw new Error("Expected password field to be visually hidden on username step");
  }

  if (options.skipInteraction) {
    const browserMetrics = await collectBrowserMetrics(page);
    return {
      flow: {
        initial: initialFlow,
        final: null,
      },
      resources: normalizeResources(browserMetrics.resources),
      timings: {
        authGateReadyMs: roundMs(authGateReadyMs),
        domContentLoadedMs: roundMs(browserMetrics.navigation?.domContentLoadedMs ?? NaN),
        firstContentfulPaintMs: roundMs(browserMetrics.paints.firstContentfulPaintMs ?? NaN),
        firstPaintMs: roundMs(browserMetrics.paints.firstPaintMs ?? NaN),
        gotoMs: roundMs(gotoMs),
        loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
        resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
        resourceTransferBytes: browserMetrics.resources.totals.transferSize,
        responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
        usernameEnterBrowserPostFrameMs: null,
        usernameEnterBrowserReadyMs: null,
        usernameEnterLegacyNodePostFrameObservedMs: null,
        usernameEnterLegacyNodeReadyObservedMs: null,
        usernameEnterPlaywrightActionMs: null,
      },
    };
  }

  await page.fill(usernameSelector, "perf_probe");
  const interaction = await clickAndMeasureInteraction(
    page,
    submitSelector,
    {
      args: { passwordSelector, submitSelector },
      name: "auth-password-step",
    },
    timeoutMs,
  );
  const finalFlow = await page.evaluate(
    ({ passwordSelector, submitSelector, usernameSelector }) => {
      const usernameInput = document.querySelector(usernameSelector);
      const passwordInput = document.querySelector(passwordSelector);
      const submitButton = document.querySelector(submitSelector);
      const label = passwordInput?.closest("label");
      return {
        buttonText: submitButton?.textContent?.trim() ?? "",
        passwordFocused: document.activeElement === passwordInput,
        passwordPlaceholder: passwordInput?.getAttribute("placeholder") ?? "",
        passwordVisible: label instanceof HTMLElement ? !label.hidden : false,
        usernameValue: usernameInput instanceof HTMLInputElement ? usernameInput.value : "",
      };
    },
    { passwordSelector, submitSelector, usernameSelector },
  );
  if (finalFlow.passwordPlaceholder !== "Password") {
    throw new Error(`Expected password placeholder "Password", got "${finalFlow.passwordPlaceholder}"`);
  }

  const browserMetrics = await collectBrowserMetrics(page);
  return {
    flow: {
      initial: initialFlow,
      final: finalFlow,
    },
    resources: normalizeResources(browserMetrics.resources),
    timings: {
      authGateReadyMs: roundMs(authGateReadyMs),
      domContentLoadedMs: roundMs(browserMetrics.navigation?.domContentLoadedMs ?? NaN),
      firstContentfulPaintMs: roundMs(browserMetrics.paints.firstContentfulPaintMs ?? NaN),
      firstPaintMs: roundMs(browserMetrics.paints.firstPaintMs ?? NaN),
      gotoMs: roundMs(gotoMs),
      loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
      resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
      resourceTransferBytes: browserMetrics.resources.totals.transferSize,
      responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
      usernameEnterBrowserPostFrameMs:
        interaction.browserTimingSummary.browserIntentToPostFrameMs,
      usernameEnterBrowserReadyMs:
        interaction.browserTimingSummary.browserIntentToReadyMs,
      usernameEnterLegacyNodePostFrameObservedMs:
        roundMs(interaction.legacyNodePostFrameObservedMs),
      usernameEnterLegacyNodeReadyObservedMs:
        roundMs(interaction.legacyNodeReadyObservedMs),
      usernameEnterPlaywrightActionMs: roundMs(interaction.playwrightActionMs),
    },
  };
}

async function runAppAuthScenario(page, options) {
  const { timeoutMs, url } = options;
  const startedAt = performance.now();
  const response = await page.goto(url, {
    waitUntil: "domcontentloaded",
    timeout: timeoutMs,
  });
  if (!response || !response.ok()) {
    throw new Error(`Navigation failed with status ${response?.status() ?? "unknown"}`);
  }
  const gotoMs = performance.now() - startedAt;

  await page.waitForSelector(".app-shell", { state: "visible", timeout: timeoutMs });
  const appShellReadyMs = performance.now() - startedAt;

  await page.waitForFunction(
    () => {
      const grid = document.querySelector("#map-grid");
      const activeMapTab = document.querySelector("#top-tab-map");
      return (
        grid instanceof HTMLElement
        && !grid.querySelector(".map-grid-loading")
        && grid.querySelectorAll(".plot").length >= 3
        && (activeMapTab?.getAttribute("aria-selected") === "true"
          || activeMapTab?.getAttribute("aria-current") === "page")
      );
    },
    undefined,
    { timeout: timeoutMs },
  );
  const appReadyMs = performance.now() - startedAt;
  const initialFlow = await page.evaluate(() => {
    const grid = document.querySelector("#map-grid");
    const gardenSelect = document.querySelector("#garden-select");
    return {
      activeTab: document.querySelector("[role='tab'][aria-selected='true']")?.id ?? "",
      gardenName: gardenSelect instanceof HTMLSelectElement
        ? gardenSelect.selectedOptions[0]?.textContent?.trim() ?? ""
        : "",
      plotCount: grid?.querySelectorAll(".plot").length ?? 0,
    };
  });

  if (initialFlow.plotCount < 3) {
    throw new Error(`Expected authenticated map to render at least 3 plots, got ${initialFlow.plotCount}`);
  }

  if (options.skipInteraction) {
    const browserMetrics = await collectBrowserMetrics(page);
    return {
      flow: {
        initial: initialFlow,
        final: null,
      },
      resources: normalizeResources(browserMetrics.resources),
      timings: {
        appReadyMs: roundMs(appReadyMs),
        appShellReadyMs: roundMs(appShellReadyMs),
        domContentLoadedMs: roundMs(browserMetrics.navigation?.domContentLoadedMs ?? NaN),
        firstContentfulPaintMs: roundMs(browserMetrics.paints.firstContentfulPaintMs ?? NaN),
        firstPaintMs: roundMs(browserMetrics.paints.firstPaintMs ?? NaN),
        gotoMs: roundMs(gotoMs),
        loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
        resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
        resourceTransferBytes: browserMetrics.resources.totals.transferSize,
        responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
        tabSwitchBrowserPostFrameMs: null,
        tabSwitchBrowserReadyMs: null,
        tabSwitchLegacyNodePostFrameObservedMs: null,
        tabSwitchLegacyNodeReadyObservedMs: null,
        tabSwitchPlaywrightActionMs: null,
      },
    };
  }

  const gardenTabSelector = tabSelectorForViewport(options, "garden");
  const interaction = await clickAndMeasureInteraction(
    page,
    gardenTabSelector,
    {
      args: {
        mobileLayoutBreakpointPx: MOBILE_LAYOUT_BREAKPOINT_PX,
        selectedTabSelector: gardenTabSelector,
      },
      name: "app-garden-tab",
    },
    timeoutMs,
  );
  const finalFlow = await page.evaluate(() => {
    const tableBody = document.querySelector("#plants-table-body");
    return {
      activeTab: document.querySelector("[role='tab'][aria-selected='true']")?.id ?? "",
      plantRows: tableBody?.querySelectorAll("tr").length ?? 0,
      firstPlant: tableBody?.querySelector("tr")?.textContent?.trim() ?? "",
    };
  });

  const browserMetrics = await collectBrowserMetrics(page);
  return {
    flow: {
      initial: initialFlow,
      final: finalFlow,
    },
    resources: normalizeResources(browserMetrics.resources),
    timings: {
      appReadyMs: roundMs(appReadyMs),
      appShellReadyMs: roundMs(appShellReadyMs),
      domContentLoadedMs: roundMs(browserMetrics.navigation?.domContentLoadedMs ?? NaN),
      firstContentfulPaintMs: roundMs(browserMetrics.paints.firstContentfulPaintMs ?? NaN),
      firstPaintMs: roundMs(browserMetrics.paints.firstPaintMs ?? NaN),
      gotoMs: roundMs(gotoMs),
      loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
      resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
      resourceTransferBytes: browserMetrics.resources.totals.transferSize,
      responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
      tabSwitchBrowserPostFrameMs:
        interaction.browserTimingSummary.browserIntentToPostFrameMs,
      tabSwitchBrowserReadyMs:
        interaction.browserTimingSummary.browserIntentToReadyMs,
      tabSwitchLegacyNodePostFrameObservedMs:
        roundMs(interaction.legacyNodePostFrameObservedMs),
      tabSwitchLegacyNodeReadyObservedMs:
        roundMs(interaction.legacyNodeReadyObservedMs),
      tabSwitchPlaywrightActionMs: roundMs(interaction.playwrightActionMs),
    },
  };
}

async function runAppAuthLargeTabsScenario(page, options) {
  const { timeoutMs, url } = options;
  const initialApiRequests = [];
  const onInitialRequest = (request) => {
    try {
      const requestUrl = new URL(request.url());
      if (requestUrl.pathname.startsWith("/api/")) {
        initialApiRequests.push(`${request.method()} ${requestUrl.pathname}${requestUrl.search}`);
      }
    } catch {
      // Keep performance collection running if Chromium reports a nonstandard URL.
    }
  };
  page.on("request", onInitialRequest);
  await page.addInitScript((thresholds) => {
    window.__gardenopsPerfLargeTabThresholds = thresholds;
  }, {
    minimumMapPlots: options.minimumMapPlots,
    minimumPlantRows: options.minimumPlantRows,
  });
  await page.addInitScript(() => {
    window.__gardenopsPerfLongTasks = [];
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__gardenopsPerfLongTasks.push({
            duration: entry.duration,
            name: entry.name,
            startTime: entry.startTime,
          });
        }
      });
      observer.observe({ entryTypes: ["longtask"] });
    } catch {
      // Long Task API is not available in every browser context.
    }
  });

  const startedAt = performance.now();
  const response = await page.goto(url, {
    waitUntil: "domcontentloaded",
    timeout: timeoutMs,
  });
  if (!response || !response.ok()) {
    throw new Error(`Navigation failed with status ${response?.status() ?? "unknown"}`);
  }
  const gotoMs = performance.now() - startedAt;

  await page.waitForSelector(".app-shell", { state: "visible", timeout: timeoutMs });
  const appShellReadyMs = performance.now() - startedAt;

  const mapReady = () => {
    const grid = document.querySelector("#map-grid");
    const activeMapTab = document.querySelector("#top-tab-map");
    const mapView = document.querySelector("#map-view");
    const minimumMapPlots = Number(
      window.__gardenopsPerfLargeTabThresholds?.minimumMapPlots ?? 600,
    );
    return (
      grid instanceof HTMLElement
      && mapView instanceof HTMLElement
      && !mapView.hidden
      && !grid.querySelector(".map-grid-loading")
      && grid.querySelectorAll(".plot").length >= minimumMapPlots
      && (activeMapTab?.getAttribute("aria-selected") === "true"
        || activeMapTab?.getAttribute("aria-current") === "page")
    );
  };
  try {
    await waitForScenarioReadiness(page, mapReady, {
      label: "large map",
      timeoutMs,
    });
  } catch (error) {
    throw new Error(
      `${errorMessage(error)}; requested readiness timeout: ${timeoutMs}ms; `
      + `observed initial API requests: ${initialApiRequests.join(", ")}`,
    );
  }
  const appReadyMs = performance.now() - startedAt;
  await page.waitForTimeout(50);
  page.off?.("request", onInitialRequest);
  if (initialApiRequests.some((request) => request === "GET /api/plants")) {
    throw new Error("Map-first startup fetched the full /api/plants catalogue");
  }

  const gardenReady = () => {
    const plantsView = document.querySelector("#plants-view");
    const gardenTab = document.querySelector("#top-tab-garden");
    const tableBody = document.querySelector("#plants-table-body");
    const mobileList = document.querySelector("#plants-mobile-list");
    const minimumPlantRows = Number(
      window.__gardenopsPerfLargeTabThresholds?.minimumPlantRows ?? 80,
    );
    const isMobile = window.innerWidth <= 960;
    const tableReady = tableBody instanceof HTMLElement
      && tableBody.dataset.renderReady === "true"
      && (
        tableBody.dataset.renderMode === "virtual"
          ? tableBody.querySelectorAll("tr[data-virtual-row]").length > 0
          : tableBody.querySelectorAll("tr").length >= minimumPlantRows
      );
    const mobileReady = mobileList instanceof HTMLElement
      && mobileList.dataset.renderReady === "true"
      && (
        mobileList.dataset.renderMode === "virtual"
          ? mobileList.querySelectorAll("[data-virtual-item]").length > 0
          : mobileList.querySelectorAll(".mobile-data-card").length >= minimumPlantRows
      );
    return (
      plantsView instanceof HTMLElement
      && !plantsView.hidden
      && (gardenTab?.getAttribute("aria-selected") === "true"
        || gardenTab?.getAttribute("aria-current") === "page")
      && (isMobile ? mobileReady : tableReady)
    );
  };
  const careReady = () => {
    const careView = document.querySelector("#care-view");
    const insightsTab = document.querySelector("#top-tab-insights");
    const tableBody = document.querySelector("#care-table-body");
    const mobileList = document.querySelector("#care-mobile-list");
    const minimumPlantRows = Number(
      window.__gardenopsPerfLargeTabThresholds?.minimumPlantRows ?? 80,
    );
    const isMobile = window.innerWidth <= 960;
    const tableReady = tableBody instanceof HTMLElement
      && tableBody.dataset.renderReady === "true"
      && (
        tableBody.dataset.renderMode === "virtual"
          ? tableBody.querySelectorAll("tr[data-virtual-row]").length > 0
          : tableBody.querySelectorAll("tr").length >= minimumPlantRows
      );
    const mobileReady = mobileList instanceof HTMLElement
      && mobileList.dataset.renderReady === "true"
      && (
        mobileList.dataset.renderMode === "virtual"
          ? mobileList.querySelectorAll("[data-virtual-item]").length > 0
          : mobileList.querySelectorAll(".care-mobile-card").length >= minimumPlantRows
      );
    return (
      careView instanceof HTMLElement
      && !careView.hidden
      && (insightsTab?.getAttribute("aria-selected") === "true"
        || insightsTab?.getAttribute("aria-current") === "page")
      && (isMobile ? mobileReady : tableReady)
    );
  };
  const tasksReady = () => {
    const activityTab = document.querySelector("#top-tab-activity");
    const taskContent = document.querySelector("#tasks-tab-content");
    const taskList = document.querySelector("#tasks-list");
    return (
      taskContent instanceof HTMLElement
      && !taskContent.hidden
      && taskList instanceof HTMLElement
      && taskList.querySelector("[data-task-id]") !== null
      && (activityTab?.getAttribute("aria-selected") === "true"
        || activityTab?.getAttribute("aria-current") === "page")
    );
  };
  const tabSelector = (tab) => tabSelectorForViewport(options, tab);
  const readinessPredicates = {
    "large-care": careReady,
    "large-garden": gardenReady,
    "large-map": mapReady,
    "large-tasks": tasksReady,
  };
  const cdpSession = await createCdpMetricsSession(page);
  const repeatedNavigationCdpBefore = await collectCdpMetrics(cdpSession);
  const repeatedNavigationDomBefore = await collectDomSnapshot(page);
  const measureSwitch = async (name, tab, readiness) => {
    const beforeCdp = await collectCdpMetrics(cdpSession);
    const domBefore = await collectDomSnapshot(page);
    const target = tabSelector(tab);
    const interaction = await clickAndMeasureInteraction(
      page,
      target,
      {
        args: {
          minimumMapPlots: options.minimumMapPlots,
          minimumPlantRows: options.minimumPlantRows,
          mobileLayoutBreakpointPx: MOBILE_LAYOUT_BREAKPOINT_PX,
        },
        name: readiness,
      },
      timeoutMs,
    );
    const browserStartedAt = interaction.browserTiming.intentAtMs
      ?? interaction.browserTiming.preparedAtMs;
    const browserEndedAt = interaction.browserTiming.postFrameAtMs;
    const afterCdp = await collectCdpMetrics(cdpSession);
    const domAfter = await collectDomSnapshot(page);
    const longTasks = await page.evaluate(
      ({ end, start }) => (window.__gardenopsPerfLongTasks ?? [])
        .filter((entry) => entry.startTime >= start && entry.startTime <= end)
        .map((entry) => ({
          duration: Math.round(entry.duration * 10) / 10,
          name: entry.name,
          startTime: Math.round(entry.startTime * 10) / 10,
        })),
      { end: browserEndedAt, start: browserStartedAt },
    );
    const networkDuringSwitch = await page.evaluate(
      ({ end, start }) => performance.getEntriesByType("resource")
        .filter((entry) => entry.startTime >= start && entry.startTime <= end)
        .map((entry) => ({
          duration: Math.round(entry.duration * 10) / 10,
          initiatorType: entry.initiatorType,
          name: entry.name,
          startTime: Math.round(entry.startTime * 10) / 10,
          transferSize: entry.transferSize ?? 0,
        })),
      { end: browserEndedAt, start: browserStartedAt },
    );
    const cdpDelta = summarizeCdpDelta(beforeCdp, afterCdp);
    return {
      cdpDelta,
      dom: {
        after: domAfter,
        before: domBefore,
        totalNodesDelta: domAfter.totalNodes - domBefore.totalNodes,
      },
      browserIntentEvent: interaction.browserTimingSummary.browserIntentEvent,
      browserPostFrameMs:
        interaction.browserTimingSummary.browserIntentToPostFrameMs,
      browserReadyMs:
        interaction.browserTimingSummary.browserIntentToReadyMs,
      legacyNodeDiagnostics: {
        postFrameObservedMs: roundMs(interaction.legacyNodePostFrameObservedMs),
        readyObservedMs: roundMs(interaction.legacyNodeReadyObservedMs),
      },
      longTasks,
      name,
      networkDuringSwitch,
      playwrightActionMs: roundMs(interaction.playwrightActionMs),
      // Deprecated legacy aliases retained for detailed diagnostic consumers.
      presentedMs: roundMs(interaction.legacyNodePostFrameObservedMs),
      readyMs: roundMs(interaction.legacyNodeReadyObservedMs),
      target,
      wallMs: roundMs(interaction.legacyNodePostFrameObservedMs),
    };
  };

  const initialFlow = await page.evaluate(() => {
    const grid = document.querySelector("#map-grid");
    const gardenSelect = document.querySelector("#garden-select");
    return {
      activeTab: document.querySelector("[role='tab'][aria-selected='true']")?.id ?? "",
      gardenName: gardenSelect instanceof HTMLSelectElement
        ? gardenSelect.selectedOptions[0]?.textContent?.trim() ?? ""
        : "",
      plotCount: grid?.querySelectorAll(".plot").length ?? 0,
    };
  });
  initialFlow.apiRequests = initialApiRequests;

  if (options.skipInteraction) {
    const browserMetrics = await collectBrowserMetrics(page);
    return {
      flow: {
        initial: initialFlow,
        final: null,
      },
      resources: normalizeResources(browserMetrics.resources),
      timings: {
        appReadyMs: roundMs(appReadyMs),
        appShellReadyMs: roundMs(appShellReadyMs),
        domContentLoadedMs: roundMs(browserMetrics.navigation?.domContentLoadedMs ?? NaN),
        firstContentfulPaintMs: roundMs(browserMetrics.paints.firstContentfulPaintMs ?? NaN),
        firstPaintMs: roundMs(browserMetrics.paints.firstPaintMs ?? NaN),
        gotoMs: roundMs(gotoMs),
        loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
        maxTabSwitchBrowserPostFrameMs: null,
        maxTabSwitchBrowserReadyMs: null,
        maxTabSwitchLegacyNodePostFrameObservedMs: null,
        maxTabSwitchLegacyNodeReadyObservedMs: null,
        maxPlaywrightActionMs: null,
        resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
        resourceTransferBytes: browserMetrics.resources.totals.transferSize,
        responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
      },
    };
  }

  const tabSwitchDetails = [];
  for (const transition of LARGE_TAB_TRANSITIONS) {
    tabSwitchDetails.push(await measureSwitch(
      transition.name,
      transition.tab,
      transition.readiness,
    ));
  }
  const switchByName = Object.fromEntries(
    tabSwitchDetails.map((entry) => [entry.name, entry]),
  );
  const longTasks = await page.evaluate(() => window.__gardenopsPerfLongTasks ?? []);
  const maxLongTaskMs = longTasks.reduce(
    (max, entry) => Math.max(max, Number(entry.duration) || 0),
    0,
  );
  const scrollVirtualSurfaceToEnd = async (
    tableBodySelector,
    listSelector,
  ) => page.evaluate(
    async ({ listSelector, tableBodySelector }) => {
      const isMobile = window.innerWidth <= 960;
      const body = document.querySelector(tableBodySelector);
      const list = document.querySelector(listSelector);
      const scrollElement = isMobile
        ? list
        : body instanceof HTMLElement
          ? body.closest(".table-wrap")
          : null;
      if (!(scrollElement instanceof HTMLElement)) {
        return { ok: false, reason: "missing virtual surface" };
      }
      for (let attempt = 0; attempt < 3; attempt += 1) {
        scrollElement.scrollTop = scrollElement.scrollHeight;
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
      for (let i = 0; i < 8; i += 1) {
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
      const virtualItems = isMobile && list instanceof HTMLElement
        ? Array.from(list.querySelectorAll("[data-virtual-item]"))
        : body instanceof HTMLElement
          ? Array.from(body.querySelectorAll("tr[data-virtual-row]"))
          : [];
      const lastItem = virtualItems.at(-1);
      const firstItem = virtualItems[0];
      const virtualHost = isMobile ? list : body;
      return {
        ok: true,
        firstId: firstItem?.getAttribute("data-plt-id")
          ?? firstItem?.getAttribute("data-care-plt")
          ?? "",
        itemCount: virtualItems.length,
        lastId: lastItem?.getAttribute("data-plt-id")
          ?? lastItem?.getAttribute("data-care-plt")
          ?? "",
        surface: isMobile ? "list" : "table",
        virtualEnd: virtualHost instanceof HTMLElement ? virtualHost.dataset.virtualEnd ?? "" : "",
        virtualStart: virtualHost instanceof HTMLElement ? virtualHost.dataset.virtualStart ?? "" : "",
      };
    },
    { listSelector, tableBodySelector },
  );
  await page.click(tabSelector("garden"));
  await waitForScenarioReadiness(page, gardenReady, {
    label: "large garden",
    timeoutMs,
  });
  const plantVirtualScroll = await scrollVirtualSurfaceToEnd(
    "#plants-table-body",
    "#plants-mobile-list",
  );
  await page.click(tabSelector("insights"));
  await waitForScenarioReadiness(page, careReady, {
    label: "large care",
    timeoutMs,
  });
  const careVirtualScroll = await scrollVirtualSurfaceToEnd(
    "#care-table-body",
    "#care-mobile-list",
  );

  const finalFlow = await page.evaluate(() => ({
    activeTab: document.querySelector("[role='tab'][aria-selected='true']")?.id ?? "",
    careCards: document.querySelector("#care-mobile-list")?.querySelectorAll("[data-virtual-item]").length ?? 0,
    careRenderComplete: document.querySelector("#care-table-body")?.dataset.renderComplete ?? "",
    careRenderedRows: document.querySelector("#care-table-body")?.dataset.renderedRows ?? "",
    careRows: document.querySelector("#care-table-body")?.querySelectorAll("tr").length ?? 0,
    layoutMode: window.innerWidth <= 960 ? "mobile" : "desktop",
    plantCards: document.querySelector("#plants-mobile-list")?.querySelectorAll("[data-virtual-item]").length ?? 0,
    plantRenderComplete: document.querySelector("#plants-table-body")?.dataset.renderComplete ?? "",
    plantRenderedRows: document.querySelector("#plants-table-body")?.dataset.renderedRows ?? "",
    plantRows: document.querySelector("#plants-table-body")?.querySelectorAll("tr").length ?? 0,
    plotCount: document.querySelector("#map-grid")?.querySelectorAll(".plot").length ?? 0,
    virtualizedCareCards: document.querySelector("#care-mobile-list")?.dataset.renderMode === "virtual",
    virtualizedCare: document.querySelector("#care-table-body")?.dataset.renderMode === "virtual",
    virtualizedPlantCards: document.querySelector("#plants-mobile-list")?.dataset.renderMode === "virtual",
    virtualizedPlants: document.querySelector("#plants-table-body")?.dataset.renderMode === "virtual",
  }));
  finalFlow.plantVirtualScroll = plantVirtualScroll;
  finalFlow.careVirtualScroll = careVirtualScroll;
  const repeatedNavigationCdpAfter = await collectCdpMetrics(cdpSession);
  const repeatedNavigationDomAfter = await collectDomSnapshot(page);
  const repeatedNavigationCdpDelta = summarizeCdpDelta(
    repeatedNavigationCdpBefore,
    repeatedNavigationCdpAfter,
  );
  finalFlow.repeatedNavigation = {
    cdp: repeatedNavigationCdpDelta,
    dom: {
      after: repeatedNavigationDomAfter,
      before: repeatedNavigationDomBefore,
      totalNodesDelta: repeatedNavigationDomAfter.totalNodes - repeatedNavigationDomBefore.totalNodes,
    },
  };

  const maxPhaseValue = (readValue) => {
    const values = tabSwitchDetails
      .map(readValue)
      .filter((value) => Number.isFinite(value));
    return values.length > 0 ? roundMs(Math.max(...values)) : null;
  };
  const transitionTimingFields = (suffix, readValue) => Object.fromEntries(
    LARGE_TAB_TRANSITIONS.map(({ name }) => [
      `${name}${suffix}`,
      roundMs(readValue(switchByName[name])),
    ]),
  );
  const browserMetrics = await collectBrowserMetrics(page);
  let growthProbe = null;
  if (options.growthProbe) {
    const garbageCollectionBefore = await collectGarbage(cdpSession);
    await waitForAnimationFrames(page);
    const cdpBefore = await collectCdpMetrics(cdpSession);
    const domBefore = await collectDomSnapshot(page);
    let completedNavigations = 0;
    for (let cycle = 0; cycle < GROWTH_PROBE_CYCLES; cycle += 1) {
      for (const transition of GROWTH_PROBE_NAVIGATIONS) {
        const predicate = readinessPredicates[transition.readiness];
        if (!predicate) {
          throw new Error(`Missing growth-probe readiness predicate: ${transition.readiness}`);
        }
        await page.click(tabSelector(transition.tab), { timeout: timeoutMs });
        await waitForScenarioReadiness(page, predicate, {
          label: `growth probe ${cycle + 1} ${transition.tab}`,
          timeoutMs,
        });
        completedNavigations += 1;
      }
    }
    const garbageCollectionAfter = await collectGarbage(cdpSession);
    await waitForAnimationFrames(page);
    const cdpAfter = await collectCdpMetrics(cdpSession);
    const domAfter = await collectDomSnapshot(page);
    growthProbe = {
      completedNavigations,
      configuredNavigations: GROWTH_PROBE_CYCLES * GROWTH_PROBE_NAVIGATIONS.length,
      cycles: GROWTH_PROBE_CYCLES,
      cdp: summarizeCdpDelta(cdpBefore, cdpAfter),
      dom: {
        after: domAfter,
        before: domBefore,
        totalNodesDelta: domAfter.totalNodes - domBefore.totalNodes,
      },
      forcedGarbageCollection: {
        after: garbageCollectionAfter,
        before: garbageCollectionBefore,
      },
    };
  }
  return {
    flow: {
      initial: initialFlow,
      final: finalFlow,
    },
    growthProbe,
    resources: normalizeResources(browserMetrics.resources),
    tabSwitchDetails,
    timings: {
      appReadyMs: roundMs(appReadyMs),
      appShellReadyMs: roundMs(appShellReadyMs),
      domContentLoadedMs: roundMs(browserMetrics.navigation?.domContentLoadedMs ?? NaN),
      firstContentfulPaintMs: roundMs(browserMetrics.paints.firstContentfulPaintMs ?? NaN),
      firstPaintMs: roundMs(browserMetrics.paints.firstPaintMs ?? NaN),
      gotoMs: roundMs(gotoMs),
      loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
      longTaskCount: longTasks.length,
      ...transitionTimingFields("BrowserReadyMs", (entry) => entry.browserReadyMs),
      ...transitionTimingFields("BrowserPostFrameMs", (entry) => entry.browserPostFrameMs),
      ...transitionTimingFields(
        "LegacyNodeReadyObservedMs",
        (entry) => entry.legacyNodeDiagnostics.readyObservedMs,
      ),
      ...transitionTimingFields(
        "LegacyNodePostFrameObservedMs",
        (entry) => entry.legacyNodeDiagnostics.postFrameObservedMs,
      ),
      ...transitionTimingFields("PlaywrightActionMs", (entry) => entry.playwrightActionMs),
      maxTabSwitchBrowserPostFrameMs:
        maxPhaseValue((entry) => entry.browserPostFrameMs),
      maxTabSwitchBrowserReadyMs:
        maxPhaseValue((entry) => entry.browserReadyMs),
      maxTabSwitchLegacyNodePostFrameObservedMs:
        maxPhaseValue((entry) => entry.legacyNodeDiagnostics.postFrameObservedMs),
      maxTabSwitchLegacyNodeReadyObservedMs:
        maxPhaseValue((entry) => entry.legacyNodeDiagnostics.readyObservedMs),
      maxPlaywrightActionMs: maxPhaseValue((entry) => entry.playwrightActionMs),
      maxScriptDurationMs: maxPhaseValue((entry) => entry.cdpDelta.scriptDurationMs),
      maxStyleLayoutDurationMs: maxPhaseValue((entry) => entry.cdpDelta.styleLayoutDurationMs),
      maxLongTaskMs: roundMs(maxLongTaskMs),
      mountedCareCards: finalFlow.careCards,
      mountedCareRows: finalFlow.careRows,
      mountedPlantCards: finalFlow.plantCards,
      mountedPlantRows: finalFlow.plantRows,
      resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
      resourceTransferBytes: browserMetrics.resources.totals.transferSize,
      apiDecodedResponseBytes: browserMetrics.resources.api.totals.decodedBodySize,
      apiEncodedResponseBytes: browserMetrics.resources.api.totals.encodedBodySize,
      repeatedNavigationJsHeapUsedDeltaBytes: repeatedNavigationCdpDelta.jsHeapUsedDeltaBytes,
      repeatedNavigationNodesDelta: finalFlow.repeatedNavigation.dom.totalNodesDelta,
      responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
      warmInsightsToMapScriptDurationMs: switchByName.warmInsightsToMap.cdpDelta.scriptDurationMs,
      warmInsightsToMapStyleLayoutDurationMs: switchByName.warmInsightsToMap.cdpDelta.styleLayoutDurationMs,
    },
  };
}

async function assertBrowserDeviceProfile(page, options) {
  const deviceProfile = options.deviceProfile ?? "desktop";
  const contextOptions = createBrowserContextOptions(options);
  const runtime = await page.evaluate(() => ({
    coarsePointer: window.matchMedia("(pointer: coarse)").matches,
    innerHeight: window.innerHeight,
    innerWidth: window.innerWidth,
    maxTouchPoints: navigator.maxTouchPoints,
    userAgent: navigator.userAgent,
  }));
  const expectedViewport = contextOptions.viewport;
  if (
    runtime.innerWidth !== expectedViewport.width
    || runtime.innerHeight !== expectedViewport.height
  ) {
    throw new Error(
      `Browser profile ${deviceProfile} viewport mismatch: expected ${expectedViewport.width}x${expectedViewport.height}, got ${runtime.innerWidth}x${runtime.innerHeight}`,
    );
  }
  if (deviceProfile === "pixel-7") {
    if (runtime.maxTouchPoints <= 0) {
      throw new Error("Browser profile pixel-7 did not expose touch input");
    }
    if (!runtime.userAgent.includes("Pixel 7")) {
      throw new Error("Browser profile pixel-7 did not expose the Pixel 7 user agent");
    }
  } else if (runtime.maxTouchPoints !== 0) {
    throw new Error("Browser profile desktop unexpectedly exposed touch input");
  }
  return {
    ...runtime,
    deviceProfile,
  };
}

function assertValidBrowserSample({ consoleMessages, pageErrors, runIndex }) {
  if (consoleMessages.length === 0 && pageErrors.length === 0) return;
  const messages = [
    ...consoleMessages.map((message) => `console error: ${message}`),
    ...pageErrors.map((message) => `page error: ${message}`),
  ];
  throw new Error(`Performance run ${runIndex} recorded browser errors: ${messages.join(" | ")}`);
}

async function selectLivePerformanceGarden(page, gardenName, timeoutMs) {
  if (!gardenName) return null;
  const gardenSelector = page.locator("[data-garden-select]:visible").first();
  await gardenSelector.waitFor({ state: "visible", timeout: timeoutMs });
  const gardenId = await gardenSelector.evaluate((select, expectedName) => {
    if (!(select instanceof HTMLSelectElement)) return "";
    const option = [...select.options].find((candidate) => (
      candidate.textContent?.trim().startsWith(`${expectedName} (`)
    ));
    return option?.value ?? "";
  }, gardenName);
  if (!gardenId) {
    throw new Error(`Live performance garden was not available: ${gardenName}`);
  }
  const gardenRefresh = page.waitForResponse((response) => {
    try {
      const request = response.request();
      const url = new URL(response.url());
      return request.method() === "GET"
        && url.pathname === `/api/gardens/${gardenId}/map-objects`
        && response.ok();
    } catch {
      return false;
    }
  }, { timeout: timeoutMs });
  await gardenSelector.selectOption(gardenId);
  await gardenRefresh;
  return Number(gardenId);
}

async function createLiveSessionStorageState(browser, options, credentials, gardenName = "") {
  if (!credentials) return null;
  const context = await browser.newContext(createBrowserContextOptions(options));
  const networkGuard = await installLiveNetworkGuard(context, options);
  try {
    const page = await context.newPage();
    const response = await page.goto(options.url, {
      waitUntil: "domcontentloaded",
      timeout: options.timeoutMs,
    });
    if (!response || !response.ok()) {
      throw new Error(`Live session bootstrap failed with status ${response?.status() ?? "unknown"}`);
    }
    await signInThroughSessionForm(page, credentials.username, credentials.password);
    await dismissProactivePasskeyPrompt(page);
    const gardenId = await selectLivePerformanceGarden(page, gardenName, options.timeoutMs);
    if (gardenId !== null) {
      const mapTab = page.locator(tabSelectorForViewport(options, "map"));
      await mapTab.click({ timeout: options.timeoutMs });
      await page.waitForFunction(() => {
        const grid = document.querySelector("#map-grid");
        const mapView = document.querySelector("#map-view");
        const activeMapTab = document.querySelector("#top-tab-map");
        return (
          grid instanceof HTMLElement
          && mapView instanceof HTMLElement
          && !mapView.hidden
          && !grid.querySelector(".map-grid-loading")
          && grid.querySelector(".plot") !== null
          && activeMapTab?.getAttribute("aria-current") === "page"
        );
      }, undefined, { timeout: options.timeoutMs });
    }
    networkGuard.assertNoBlockedRequests();
    return {
      gardenId,
      storageState: await context.storageState(),
    };
  } finally {
    await context.close();
  }
}

async function runMeasuredScenario(browser, options, runIndex, liveSession = null) {
  const context = await browser.newContext({
    ...createBrowserContextOptions(options),
    ...(!options.stubApi ? {
      extraHTTPHeaders: {
        "X-GardenOps-Performance-Probe": "1",
        ...(options.evidenceLabel
          ? { "X-GardenOps-Performance-Probe-Label": options.evidenceLabel }
          : {}),
      },
    } : {}),
    ...(liveSession ? { storageState: liveSession.storageState } : {}),
  });
  const networkGuard = options.stubApi ? null : await installLiveNetworkGuard(context, options);
  try {
    if (options.stubApi) {
      await installScenarioRoutes(context, options.scenario);
    }
    const page = await context.newPage();
    if (liveSession?.gardenId) {
      await page.addInitScript((gardenId) => {
        sessionStorage.setItem("gardenops-active-garden-id", String(gardenId));
      }, liveSession.gardenId);
    }
    const apiResponseTracker = createApiResponseTracker(page, options.scenario);
    const consoleMessages = [];
    const pageErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleMessages.push(message.text());
      }
    });
    page.on("pageerror", (err) => pageErrors.push(err.message));
    try {
      const result = options.scenario === "app-auth-large-tabs"
        ? await runAppAuthLargeTabsScenario(page, options)
        : options.scenario === "app-auth"
          ? await runAppAuthScenario(page, options)
          : await runAppUnauthScenario(page, options);
      await apiResponseTracker.flush();
      apiResponseTracker.assertNoUnexpectedResponses();
      networkGuard?.assertNoBlockedRequests();
      const browserProfile = await assertBrowserDeviceProfile(page, options);
      assertValidBrowserSample({ consoleMessages, pageErrors, runIndex });
      const apiResponseSummary = apiResponseTracker.summary();
      result.timings.apiAppServerDurationMs = apiResponseSummary.appServerDurationMs;
      result.timings.apiResponseCount = apiResponseSummary.responseCount;
      result.timings.apiAppServerTimedResponseCount = apiResponseSummary.appServerTimedResponseCount;
      return {
        ...result,
        browserProfile,
        consoleErrors: consoleMessages,
        pageErrors,
        run: runIndex,
      };
    } finally {
      apiResponseTracker.detach();
    }
  } finally {
    await context.close();
  }
}

function comparisonValue(value) {
  return JSON.stringify(value);
}

function assertComparableProvenance(current, previous) {
  const currentComparison = current?.provenance?.comparison;
  const previousComparison = previous?.provenance?.comparison;
  const failures = [];
  if (!currentComparison || !previousComparison) {
    failures.push("both results must include provenance.comparison");
  } else {
    for (const field of [
      "scenario",
      "apiMode",
      "viewportProfile",
      "browser",
      "options",
    ]) {
      if (!(field in currentComparison) || !(field in previousComparison)) {
        failures.push(`both results must include provenance.comparison.${field}`);
      } else if (comparisonValue(currentComparison[field]) !== comparisonValue(previousComparison[field])) {
        failures.push(`provenance.comparison.${field} differs`);
      }
    }
  }

  for (const [label, git] of [
    ["current", current?.provenance?.git],
    ["baseline", previous?.provenance?.git],
  ]) {
    if (!git || typeof git.revision !== "string" || typeof git.dirty !== "boolean") {
      failures.push(`${label} result has incomplete git provenance`);
    } else if (git.dirty && (typeof git.contentHash !== "string" || !git.contentHash)) {
      failures.push(
        `${label} dirty working tree is missing a content hash; refusing ambiguous dirty comparison`,
      );
    }
  }

  if (failures.length > 0) {
    throw new Error(`Incompatible performance baseline: ${failures.join("; ")}`);
  }
}

function isComparisonGatedMetric(metric) {
  return metric === "authGateReadyMs"
    || metric === "appShellReadyMs"
    || metric === "appReadyMs"
    || metric === "domContentLoadedMs"
    || metric === "loadEventMs"
    || metric === "firstContentfulPaintMs"
    || metric === "resourceEncodedBytes"
    || metric.endsWith("BrowserReadyMs")
    || metric.endsWith("BrowserPostFrameMs");
}

function compareSummaries(current, previous, options) {
  assertComparableProvenance(current, previous);
  const coreMetrics = Object.keys(current.summary.metrics);
  return coreMetrics.map((metric) => {
    const currentValue = current.summary.metrics[metric]?.median;
    const previousValue = previous.summary?.metrics?.[metric]?.median;
    const delta = Number.isFinite(currentValue) && Number.isFinite(previousValue)
      ? currentValue - previousValue
      : null;
    const changePct = Number.isFinite(currentValue) && Number.isFinite(previousValue) && previousValue > 0
      ? ((currentValue - previousValue) / previousValue) * 100
      : null;
    const isTimingMetric = metric !== "resourceEncodedBytes";
    const comparisonGated = isComparisonGatedMetric(metric);
    const exceedsTimingJitter = !isTimingMetric
      || delta === null
      || delta > options.maxRegressionMs;
    return {
      changePct: changePct === null ? null : roundMs(changePct),
      current: currentValue ?? null,
      delta: delta === null ? null : roundMs(delta),
      metric,
      previous: previousValue ?? null,
      comparisonGated,
      regressed: comparisonGated
        && changePct !== null
        && changePct > options.maxRegressionPct
        && exceedsTimingJitter,
    };
  });
}

function enforceBudgets(result, options) {
  const failures = [];
  const expectedSamples = result.runs.length;
  const navigationMetric = result.scenario === "app-unauth" ? "authGateReadyMs" : "appReadyMs";
  const navigationStats = result.summary.metrics[navigationMetric];
  if (options.navigationBudgetMs !== null) {
    if (navigationStats?.n !== expectedSamples) {
      failures.push(
        `${navigationMetric} has ${navigationStats?.n ?? 0}/${expectedSamples} measured samples (navigation budget)`,
      );
    } else if (!Number.isFinite(navigationStats.p75)) {
      failures.push(`${navigationMetric} has no measured p75 (navigation budget)`);
    } else if (navigationStats.p75 > options.navigationBudgetMs) {
      failures.push(
        `${navigationMetric} p75 ${navigationStats.p75}ms exceeds ${options.navigationBudgetMs}ms`,
      );
    }
  }

  const enforceInteractionBudget = (metric, budgetMs, budgetLabel) => {
    const stats = result.summary.metrics[metric];
    if (stats?.n !== expectedSamples) {
      failures.push(
        `${metric} has ${stats?.n ?? 0}/${expectedSamples} measured samples (${budgetLabel})`,
      );
    } else if (!Number.isFinite(stats.p75)) {
      failures.push(`${metric} has no measured p75 (${budgetLabel})`);
    } else if (stats.p75 > budgetMs) {
      failures.push(`${metric} p75 ${stats.p75}ms exceeds ${budgetMs}ms (${budgetLabel})`);
    }
  };
  const isLargeTabs = result.scenario === "app-auth-large-tabs";
  if (options.interactionBudgetMs !== null) {
    if (isLargeTabs) {
      for (const { name } of LARGE_TAB_TRANSITIONS) {
        enforceInteractionBudget(
          `${name}BrowserPostFrameMs`,
          options.interactionBudgetMs,
          "interaction budget",
        );
      }
    } else {
      const metric = result.scenario === "app-auth"
        ? "tabSwitchBrowserPostFrameMs"
        : "usernameEnterBrowserPostFrameMs";
      enforceInteractionBudget(metric, options.interactionBudgetMs, "interaction budget");
    }
  }
  if (options.tabSwitchBudgetMs !== null) {
    if (result.scenario === "app-auth") {
      enforceInteractionBudget(
        "tabSwitchBrowserPostFrameMs",
        options.tabSwitchBudgetMs,
        "tab-switch budget",
      );
    } else if (isLargeTabs) {
      for (const { name } of LARGE_TAB_TRANSITIONS) {
        enforceInteractionBudget(
          `${name}BrowserPostFrameMs`,
          options.tabSwitchBudgetMs,
          "tab-switch budget",
        );
      }
    } else {
      failures.push(
        "--tab-switch-budget-ms requires an app-auth or app-auth-large-tabs scenario",
      );
    }
  }
  if (
    options.renderedRowBudget !== null
    && result.scenario === "app-auth-large-tabs"
  ) {
    for (const run of result.runs) {
      const isMobile = run.flow?.final?.layoutMode === "mobile";
      const plantCards = run.flow?.final?.plantCards;
      const plantRows = run.flow?.final?.plantRows;
      const careCards = run.flow?.final?.careCards;
      const careRows = run.flow?.final?.careRows;
      if (Number.isFinite(plantCards) && plantCards > options.renderedRowBudget) {
        failures.push(`mounted plant cards ${plantCards} exceeds ${options.renderedRowBudget}`);
      }
      if (Number.isFinite(plantRows) && plantRows > options.renderedRowBudget) {
        failures.push(`mounted plant rows ${plantRows} exceeds ${options.renderedRowBudget}`);
      }
      if (Number.isFinite(careCards) && careCards > options.renderedRowBudget) {
        failures.push(`mounted care cards ${careCards} exceeds ${options.renderedRowBudget}`);
      }
      if (Number.isFinite(careRows) && careRows > options.renderedRowBudget) {
        failures.push(`mounted care rows ${careRows} exceeds ${options.renderedRowBudget}`);
      }
      if (!isMobile && run.flow?.final?.virtualizedPlants !== true) {
        failures.push("plants table did not use virtual rendering");
      }
      if (!isMobile && run.flow?.final?.virtualizedCare !== true) {
        failures.push("care table did not use virtual rendering");
      }
      if (isMobile && run.flow?.final?.virtualizedPlantCards !== true) {
        failures.push("plants mobile list did not use virtual rendering");
      }
      if (isMobile && run.flow?.final?.virtualizedCareCards !== true) {
        failures.push("care mobile list did not use virtual rendering");
      }
      if (Number(run.flow?.final?.plantVirtualScroll?.virtualEnd) < 900) {
        failures.push("plants virtual surface did not scroll to final item range");
      }
      if (Number(run.flow?.final?.careVirtualScroll?.virtualEnd) < 900) {
        failures.push("care virtual surface did not scroll to final item range");
      }
    }
  }
  if (failures.length > 0) {
    throw new Error(failures.join("; "));
  }
}

function enforceComparison(result, options) {
  const regressions = result.compare?.filter((row) => row.regressed) ?? [];
  if (regressions.length > 0) {
    throw new Error(
      `Performance regression versus ${options.comparePath}: ${regressions
        .map((row) => `${row.metric} +${row.changePct}%`)
        .join(", ")}`,
    );
  }
}

function writeOutput(outputPath, result) {
  if (!outputPath) return;
  const resolved = path.resolve(process.cwd(), outputPath);
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  fs.writeFileSync(resolved, `${JSON.stringify(result, null, 2)}\n`);
}

function persistAndValidateResult(result, options) {
  writeOutput(options.outputPath, result);
  enforceComparison(result, options);
  enforceBudgets(result, options);
}

function printHuman(result, outputPath) {
  const metrics = result.summary.metrics;
  const fmtMs = (value) => (Number.isFinite(value) ? `${value}ms` : "n/a");
  const metricSummary = (name) => {
    const metric = metrics[name] ?? {};
    return `median ${fmtMs(metric.median)}, p75 ${fmtMs(metric.p75)}, n ${metric.n ?? 0}`;
  };
  const resourceSummary = () => {
    const metric = metrics.resourceEncodedBytes ?? {};
    const fmtBytes = (value) => (Number.isFinite(value) ? value : "n/a");
    return `median ${fmtBytes(metric.median)}, p75 ${fmtBytes(metric.p75)}, n ${metric.n ?? 0}`;
  };
  const transitionSummary = (transition) => (
    `${transition} ${metricSummary(`${transition}BrowserPostFrameMs`)}`
  );
  console.log(`Scenario: ${result.scenario}`);
  console.log(`Serve mode: ${result.serveMode ?? "external"}`);
  console.log(`URL: ${result.url}`);
  console.log(`Runs: ${result.runs.length}`);
  console.log(
    `Viewport profile: ${result.provenance?.viewportProfile?.label ?? result.measurement?.browserContext?.viewportProfile?.label ?? "unknown"}`,
  );
  if (result.scenario === "app-auth-large-tabs") {
    console.log(
      `App shell ready: ${metricSummary("appShellReadyMs")}`,
    );
    console.log(
      `App ready: ${metricSummary("appReadyMs")}`,
    );
    console.log(
      `Browser post-frame proxy (budgeted; not guaranteed first presentation): ${LARGE_TAB_TRANSITIONS.map(({ name }) => transitionSummary(name)).join("; ")}`,
    );
    console.log(
      `Browser post-frame aggregate max: ${metricSummary("maxTabSwitchBrowserPostFrameMs")}; browser readiness aggregate max: ${metricSummary("maxTabSwitchBrowserReadyMs")}`,
    );
    console.log(
      `Playwright actionability overhead (diagnostic only): ${metricSummary("maxPlaywrightActionMs")}`,
    );
    console.log(
      `Legacy Node observation (diagnostic only): ready ${metricSummary("maxTabSwitchLegacyNodeReadyObservedMs")}; post-frame ${metricSummary("maxTabSwitchLegacyNodePostFrameObservedMs")}`,
    );
    console.log(
      `Switch work: max long task ${metricSummary("maxLongTaskMs")}; max JS ${metricSummary("maxScriptDurationMs")}; max style/layout ${metricSummary("maxStyleLayoutDurationMs")}`,
    );
    console.log(
      `Mounted rows: plants median ${metrics.mountedPlantRows?.median ?? "n/a"}, care median ${metrics.mountedCareRows?.median ?? "n/a"}`,
    );
    console.log(
      `Mounted cards: plants median ${metrics.mountedPlantCards?.median ?? "n/a"}, care median ${metrics.mountedCareCards?.median ?? "n/a"}`,
    );
  } else if (result.scenario === "app-auth") {
    console.log(
      `App shell ready: ${metricSummary("appShellReadyMs")}`,
    );
    console.log(
      `App ready: ${metricSummary("appReadyMs")}`,
    );
    console.log(
      `Garden tab browser post-frame proxy (budgeted; not guaranteed first presentation): ${metricSummary("tabSwitchBrowserPostFrameMs")}`,
    );
    console.log(
      `Browser readiness: ${metricSummary("tabSwitchBrowserReadyMs")}; Playwright actionability overhead (diagnostic only): ${metricSummary("tabSwitchPlaywrightActionMs")}`,
    );
    console.log(
      `Legacy Node observation (diagnostic only): ready ${metricSummary("tabSwitchLegacyNodeReadyObservedMs")}; post-frame ${metricSummary("tabSwitchLegacyNodePostFrameObservedMs")}`,
    );
  } else {
    console.log(
      `Auth gate ready: ${metricSummary("authGateReadyMs")}`,
    );
    console.log(
      `Username Enter browser post-frame proxy (budgeted; not guaranteed first presentation): ${metricSummary("usernameEnterBrowserPostFrameMs")}`,
    );
    console.log(
      `Browser readiness: ${metricSummary("usernameEnterBrowserReadyMs")}; Playwright actionability overhead (diagnostic only): ${metricSummary("usernameEnterPlaywrightActionMs")}`,
    );
    console.log(
      `Legacy Node observation (diagnostic only): ready ${metricSummary("usernameEnterLegacyNodeReadyObservedMs")}; post-frame ${metricSummary("usernameEnterLegacyNodePostFrameObservedMs")}`,
    );
  }
  console.log(
    `DOMContentLoaded: ${metricSummary("domContentLoadedMs")}; load: ${metricSummary("loadEventMs")}; FCP: ${metricSummary("firstContentfulPaintMs")}`,
  );
  console.log(
    `Resource encoded bytes: ${resourceSummary()}; last run resources: ${result.summary.lastRunResources?.totals.count ?? 0}`,
  );
  if (result.compare) {
    console.log("Comparison against baseline:");
    for (const row of result.compare) {
      const change = row.changePct === null ? "n/a" : `${row.changePct}%`;
      const delta = row.delta === null ? "n/a" : `${row.delta}`;
      console.log(`- ${row.metric}: ${row.previous} -> ${row.current} (${change}, delta ${delta})`);
    }
  }
  if (outputPath) console.log(`Wrote JSON: ${path.resolve(process.cwd(), outputPath)}`);
}

async function cleanupManagedResources(browser, server) {
  const cleanupErrors = [];
  if (browser) {
    try {
      await browser.close();
    } catch (err) {
      cleanupErrors.push(`browser: ${errorMessage(err)}`);
    }
  }
  if (server) {
    try {
      await server.stop();
    } catch (err) {
      cleanupErrors.push(`server: ${errorMessage(err)}`);
    }
  }
  if (cleanupErrors.length > 0) {
    throw new Error(`Page performance cleanup failed: ${cleanupErrors.join("; ")}`);
  }
}

async function emitSuccessAfterCleanup({ browser, emit, server }) {
  await cleanupManagedResources(browser, server);
  await emit();
}

async function main() {
  let server = null;
  let browser = null;
  let primaryError = null;
  let options = null;
  let result = null;
  try {
    options = parseArgs(process.argv.slice(2));
    if (options.help) {
      usage();
      return;
    }

    const browserPath = findBrowserPath(options.browserPath);
    if (!browserPath) {
      throw new Error("No Chromium executable found. Set PERF_CHROMIUM or pass --browser.");
    }
    if (!fs.existsSync(PLAYWRIGHT_PATH)) {
      throw new Error("Missing frontend/node_modules/playwright-core. Run npm install in frontend.");
    }

    let chromium;
    ({ chromium } = require(PLAYWRIGHT_PATH));
    if (options.serve) {
      server = await startServer(options);
    }

    browser = await chromium.launch({
      executablePath: browserPath,
      headless: !options.headful,
    });
    const browserVersion = browser.version();
    const liveSession = await createLiveSessionStorageState(
      browser,
      options,
      liveCredentialsFor(options),
      liveGardenNameFor(options),
    );
    const warmupRuns = [];
    for (let i = 1; i <= options.warmupRuns; i += 1) {
      warmupRuns.push(
        await runMeasuredScenario(browser, {
          ...options,
          growthProbe: options.scenario === "app-auth-large-tabs"
            && !options.skipGrowthProbe && i === 1,
        }, `warmup-${i}`, liveSession),
      );
    }
    const runs = [];
    for (let i = 1; i <= options.runs; i += 1) {
      runs.push(await runMeasuredScenario(browser, options, i, liveSession));
    }
    result = {
      browserPath,
      compare: null,
      createdAt: new Date().toISOString(),
      measurement: buildMeasurementMetadata(options),
      provenance: buildReproducibilityProvenance({
        argv: process.argv.slice(2),
        browserPath,
        browserVersion,
        options,
      }),
      scenario: options.scenario,
      serveMode: options.serve ? options.serveMode : "external",
      skipInteraction: options.skipInteraction,
      stubApi: options.stubApi,
      summary: summarizeRuns(runs, options.scenario),
      url: options.url,
      warmupRuns,
      growthProbe: warmupRuns.find((run) => run.growthProbe)?.growthProbe ?? null,
      runs,
    };

    // Keep completed-run evidence even if baseline loading or validation fails.
    writeOutput(options.outputPath, result);

    if (options.comparePath) {
      const previous = JSON.parse(
        fs.readFileSync(path.resolve(process.cwd(), options.comparePath), "utf8"),
      );
      result.compare = compareSummaries(
        result,
        previous,
        options,
      );
    }

    persistAndValidateResult(result, options);
  } catch (err) {
    primaryError = err;
  }

  if (primaryError) {
    try {
      await cleanupManagedResources(browser, server);
    } catch (cleanupError) {
      throw new Error(`${errorMessage(primaryError)}\n${errorMessage(cleanupError)}`);
    }
    throw primaryError;
  }

  await emitSuccessAfterCleanup({
    browser,
    server,
    emit: async () => {
      if (options.json) {
        console.log(JSON.stringify(result, null, 2));
      } else {
        console.log("Page performance check passed.");
        printHuman(result, options.outputPath);
      }
    },
  });
}

function errorMessage(err) {
  return err instanceof Error ? err.message : String(err);
}

module.exports = {
  assertComparableProvenance,
  buildAuthPerformanceData,
  buildComparisonProvenance,
  buildReproducibilityProvenance,
  buildMeasurementMetadata,
  cleanupManagedResources,
  compareSummaries,
  createLiveSessionStorageState,
  createApiResponseTracker,
  emitSuccessAfterCleanup,
  enforceBudgets,
  installScenarioRoutes,
  installLiveNetworkGuard,
  isReadyStatus,
  metricStats,
  liveCredentialsFor,
  liveGardenNameFor,
  parseArgs,
  persistAndValidateResult,
  request,
  startServer,
  tabSelectorForViewport,
  waitForServer,
};

if (require.main === module) {
  main().catch((err) => {
    console.error(`Page performance check failed: ${errorMessage(err)}`);
    process.exitCode = 1;
  });
}
