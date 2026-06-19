#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { performance } = require("node:perf_hooks");

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
const SCENARIOS = new Set(["app-unauth", "app-auth", "app-auth-large-tabs"]);
const SCENARIO_METRICS = {
  "app-auth": [
    "appShellReadyMs",
    "appReadyMs",
    "tabSwitchMs",
    "domContentLoadedMs",
    "loadEventMs",
    "firstContentfulPaintMs",
    "resourceEncodedBytes",
  ],
  "app-auth-large-tabs": [
    "appShellReadyMs",
    "appReadyMs",
    "mapToGardenMs",
    "gardenToInsightsMs",
    "insightsToMapMs",
    "warmMapToGardenMs",
    "warmGardenToInsightsMs",
    "warmInsightsToMapMs",
    "maxTabSwitchMs",
    "maxLongTaskMs",
    "longTaskCount",
    "mountedCareCards",
    "mountedCareRows",
    "mountedPlantCards",
    "mountedPlantRows",
    "domContentLoadedMs",
    "loadEventMs",
    "firstContentfulPaintMs",
    "resourceEncodedBytes",
  ],
  "app-unauth": [
    "authGateReadyMs",
    "usernameEnterMs",
    "domContentLoadedMs",
    "loadEventMs",
    "firstContentfulPaintMs",
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
  --no-api-stubs                  Let auth API calls hit the target server.
  --skip-interaction              Measure load only; useful when live passkeys intercept Enter.
  --runs <count>                  Measured runs. Default: 3.
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
    headful: false,
    host: DEFAULT_HOST,
    interactionBudgetMs: null,
    json: false,
    maxRegressionMs: 15,
    maxRegressionPct: 5,
    navigationBudgetMs: null,
    outputPath: "",
    port: DEFAULT_PORT,
    renderedRowBudget: null,
    runs: 3,
    scenario: "app-unauth",
    serve: false,
    serveMode: "dev",
    skipInteraction: false,
    stubApi: true,
    tabSwitchBudgetMs: null,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    url: "",
    viewportHeight: 900,
    viewportWidth: 1440,
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
    } else if (arg === "--runs") {
      options.runs = parsePositiveInt(next(), "--runs");
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
  if (!["dev", "preview"].includes(options.serveMode)) {
    throw new Error(`Unknown serve mode: ${options.serveMode}`);
  }
  if (!options.url && !options.serve) {
    options.url = `http://${options.host}:${options.port}/`;
  }
  if (options.serve && !options.url) {
    options.url = `http://${options.host}:${options.port}/`;
  }
  return options;
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
    min: percentile(values, 0),
    median: percentile(values, 0.5),
    p75: percentile(values, 0.75),
    max: percentile(values, 1),
  };
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

function fail(message) {
  console.error(`Page performance check failed: ${message}`);
  process.exit(1);
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

function request(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      res.on("end", () => resolve({ ok: res.statusCode < 500, status: res.statusCode }));
    });
    req.on("error", (err) => resolve({ ok: false, error: err.message }));
    req.setTimeout(1_000, () => {
      req.destroy();
      resolve({ ok: false, error: "timeout" });
    });
  });
}

async function waitForServer(url, timeoutMs, child, readServerLog) {
  const startedAt = performance.now();
  while (performance.now() - startedAt < timeoutMs) {
    if (child.exitCode !== null) {
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
  await waitForServer(options.url, options.timeoutMs, child, () => serverLog);
  return {
    child,
    async stop() {
      if (child.exitCode !== null) return;
      child.kill("SIGTERM");
      await new Promise((resolve) => {
        const timer = setTimeout(() => {
          if (child.exitCode === null) child.kill("SIGKILL");
          resolve();
        }, 1_500);
        child.once("exit", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    },
  };
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
    const totals = resourceRows.reduce(
      (acc, entry) => {
        acc.transferSize += entry.transferSize;
        acc.encodedBodySize += entry.encodedBodySize;
        acc.decodedBodySize += entry.decodedBodySize;
        acc.count += 1;
        return acc;
      },
      { count: 0, decodedBodySize: 0, encodedBodySize: 0, transferSize: 0 },
    );
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
        usernameEnterMs: null,
      },
    };
  }

  await page.fill(usernameSelector, "perf_probe");
  const interactionStartedAt = performance.now();
  await page.click(submitSelector);
  await page.waitForFunction(
    ({ passwordSelector, submitSelector }) => {
      const passwordInput = document.querySelector(passwordSelector);
      const submitButton = document.querySelector(submitSelector);
      const label = passwordInput?.closest("label");
      return (
        passwordInput instanceof HTMLInputElement
        && label instanceof HTMLElement
        && !label.hidden
        && submitButton?.textContent?.trim() === "Login"
        && document.activeElement === passwordInput
      );
    },
    { passwordSelector, submitSelector },
    { timeout: timeoutMs },
  );
  const usernameEnterMs = performance.now() - interactionStartedAt;
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
      usernameEnterMs: roundMs(usernameEnterMs),
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
        && activeMapTab?.getAttribute("aria-selected") === "true"
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
        tabSwitchMs: null,
      },
    };
  }

  const interactionStartedAt = performance.now();
  await page.click("#top-tab-garden");
  await page.waitForFunction(
    () => {
      const plantsView = document.querySelector("#plants-view");
      const gardenTab = document.querySelector("#top-tab-garden");
      const tableBody = document.querySelector("#plants-table-body");
      return (
        plantsView instanceof HTMLElement
        && !plantsView.hidden
        && gardenTab?.getAttribute("aria-selected") === "true"
        && tableBody?.querySelectorAll("tr").length === 1
      );
    },
    undefined,
    { timeout: timeoutMs },
  );
  const tabSwitchMs = performance.now() - interactionStartedAt;
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
      tabSwitchMs: roundMs(tabSwitchMs),
    },
  };
}

async function runAppAuthLargeTabsScenario(page, options) {
  const { timeoutMs, url } = options;
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
    return (
      grid instanceof HTMLElement
      && mapView instanceof HTMLElement
      && !mapView.hidden
      && !grid.querySelector(".map-grid-loading")
      && grid.querySelectorAll(".plot").length >= 600
      && activeMapTab?.getAttribute("aria-selected") === "true"
    );
  };
  await page.waitForFunction(mapReady, undefined, { timeout: timeoutMs });
  const appReadyMs = performance.now() - startedAt;

  const gardenReady = () => {
    const plantsView = document.querySelector("#plants-view");
    const gardenTab = document.querySelector("#top-tab-garden");
    const tableBody = document.querySelector("#plants-table-body");
    const mobileList = document.querySelector("#plants-mobile-list");
    const isMobile = window.innerWidth <= 960;
    const tableReady = tableBody instanceof HTMLElement
      && tableBody.dataset.renderReady === "true"
      && (
        tableBody.dataset.renderMode === "virtual"
          ? tableBody.querySelectorAll("tr[data-virtual-row]").length > 0
          : tableBody.querySelectorAll("tr").length >= 80
      );
    const mobileReady = mobileList instanceof HTMLElement
      && mobileList.dataset.renderReady === "true"
      && (
        mobileList.dataset.renderMode === "virtual"
          ? mobileList.querySelectorAll("[data-virtual-item]").length > 0
          : mobileList.querySelectorAll(".mobile-data-card").length >= 80
      );
    return (
      plantsView instanceof HTMLElement
      && !plantsView.hidden
      && gardenTab?.getAttribute("aria-selected") === "true"
      && (isMobile ? mobileReady : tableReady)
    );
  };
  const careReady = () => {
    const careView = document.querySelector("#care-view");
    const insightsTab = document.querySelector("#top-tab-insights");
    const tableBody = document.querySelector("#care-table-body");
    const mobileList = document.querySelector("#care-mobile-list");
    const isMobile = window.innerWidth <= 960;
    const tableReady = tableBody instanceof HTMLElement
      && tableBody.dataset.renderReady === "true"
      && (
        tableBody.dataset.renderMode === "virtual"
          ? tableBody.querySelectorAll("tr[data-virtual-row]").length > 0
          : tableBody.querySelectorAll("tr").length >= 80
      );
    const mobileReady = mobileList instanceof HTMLElement
      && mobileList.dataset.renderReady === "true"
      && (
        mobileList.dataset.renderMode === "virtual"
          ? mobileList.querySelectorAll("[data-virtual-item]").length > 0
          : mobileList.querySelectorAll(".care-mobile-card").length >= 80
      );
    return (
      careView instanceof HTMLElement
      && !careView.hidden
      && insightsTab?.getAttribute("aria-selected") === "true"
      && (isMobile ? mobileReady : tableReady)
    );
  };
  const tabSelector = (tab) => (
    options.viewportWidth <= 960 ? `#mobile-tab-${tab}` : `#top-tab-${tab}`
  );
  const measureSwitch = async (tab, readyFn) => {
    const interactionStartedAt = performance.now();
    await page.click(tabSelector(tab));
    await page.waitForFunction(readyFn, undefined, { timeout: timeoutMs });
    return performance.now() - interactionStartedAt;
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
      },
    };
  }

  const mapToGardenMs = await measureSwitch("garden", gardenReady);
  const gardenToInsightsMs = await measureSwitch("insights", careReady);
  const insightsToMapMs = await measureSwitch("map", mapReady);
  const warmMapToGardenMs = await measureSwitch("garden", gardenReady);
  const warmGardenToInsightsMs = await measureSwitch("insights", careReady);
  const warmInsightsToMapMs = await measureSwitch("map", mapReady);

  const switchTimings = [
    mapToGardenMs,
    gardenToInsightsMs,
    insightsToMapMs,
    warmMapToGardenMs,
    warmGardenToInsightsMs,
    warmInsightsToMapMs,
  ];
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
  await page.waitForFunction(gardenReady, undefined, { timeout: timeoutMs });
  const plantVirtualScroll = await scrollVirtualSurfaceToEnd(
    "#plants-table-body",
    "#plants-mobile-list",
  );
  await page.click(tabSelector("insights"));
  await page.waitForFunction(careReady, undefined, { timeout: timeoutMs });
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
      gardenToInsightsMs: roundMs(gardenToInsightsMs),
      gotoMs: roundMs(gotoMs),
      insightsToMapMs: roundMs(insightsToMapMs),
      loadEventMs: roundMs(browserMetrics.navigation?.loadEventMs ?? NaN),
      longTaskCount: longTasks.length,
      mapToGardenMs: roundMs(mapToGardenMs),
      maxLongTaskMs: roundMs(maxLongTaskMs),
      maxTabSwitchMs: roundMs(Math.max(...switchTimings)),
      mountedCareCards: finalFlow.careCards,
      mountedCareRows: finalFlow.careRows,
      mountedPlantCards: finalFlow.plantCards,
      mountedPlantRows: finalFlow.plantRows,
      resourceEncodedBytes: browserMetrics.resources.totals.encodedBodySize,
      resourceTransferBytes: browserMetrics.resources.totals.transferSize,
      responseEndMs: roundMs(browserMetrics.navigation?.responseEndMs ?? NaN),
      warmGardenToInsightsMs: roundMs(warmGardenToInsightsMs),
      warmInsightsToMapMs: roundMs(warmInsightsToMapMs),
      warmMapToGardenMs: roundMs(warmMapToGardenMs),
    },
  };
}

async function runMeasuredScenario(browser, options, runIndex) {
  const context = await browser.newContext({
    viewport: { height: options.viewportHeight, width: options.viewportWidth },
  });
  if (options.stubApi) {
    await installScenarioRoutes(context, options.scenario);
  }
  const page = await context.newPage();
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
    return {
      ...result,
      consoleErrors: consoleMessages,
      pageErrors,
      run: runIndex,
    };
  } finally {
    await context.close();
  }
}

function compareSummaries(current, previous, options) {
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
    const exceedsTimingJitter = !isTimingMetric
      || delta === null
      || delta > options.maxRegressionMs;
    return {
      changePct: changePct === null ? null : roundMs(changePct),
      current: currentValue ?? null,
      delta: delta === null ? null : roundMs(delta),
      metric,
      previous: previousValue ?? null,
      regressed: changePct !== null
        && changePct > options.maxRegressionPct
        && exceedsTimingJitter,
    };
  });
}

function enforceBudgets(result, options) {
  const failures = [];
  const navigationMetric = result.scenario === "app-unauth" ? "authGateReadyMs" : "appReadyMs";
  const interactionMetric = result.scenario === "app-auth-large-tabs"
    ? "maxTabSwitchMs"
    : result.scenario === "app-auth"
      ? "tabSwitchMs"
      : "usernameEnterMs";
  const navigationP75 = result.summary.metrics[navigationMetric]?.p75;
  const interactionP75 = result.summary.metrics[interactionMetric]?.p75;
  if (
    options.navigationBudgetMs !== null
    && Number.isFinite(navigationP75)
    && navigationP75 > options.navigationBudgetMs
  ) {
    failures.push(`${navigationMetric} p75 ${navigationP75}ms exceeds ${options.navigationBudgetMs}ms`);
  }
  if (
    options.interactionBudgetMs !== null
    && Number.isFinite(interactionP75)
    && interactionP75 > options.interactionBudgetMs
  ) {
    failures.push(`${interactionMetric} p75 ${interactionP75}ms exceeds ${options.interactionBudgetMs}ms`);
  }
  if (
    options.tabSwitchBudgetMs !== null
    && result.scenario === "app-auth-large-tabs"
  ) {
    for (const metric of [
      "mapToGardenMs",
      "gardenToInsightsMs",
      "insightsToMapMs",
      "warmMapToGardenMs",
      "warmGardenToInsightsMs",
      "warmInsightsToMapMs",
    ]) {
      const p75 = result.summary.metrics[metric]?.p75;
      if (Number.isFinite(p75) && p75 > options.tabSwitchBudgetMs) {
        failures.push(`${metric} p75 ${p75}ms exceeds ${options.tabSwitchBudgetMs}ms`);
      }
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

function writeOutput(outputPath, result) {
  if (!outputPath) return;
  const resolved = path.resolve(process.cwd(), outputPath);
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  fs.writeFileSync(resolved, `${JSON.stringify(result, null, 2)}\n`);
}

function printHuman(result, outputPath) {
  const metrics = result.summary.metrics;
  const fmtMs = (value) => (value === null ? "n/a" : `${value}ms`);
  console.log("Page performance check passed.");
  console.log(`Scenario: ${result.scenario}`);
  console.log(`Serve mode: ${result.serveMode ?? "external"}`);
  console.log(`URL: ${result.url}`);
  console.log(`Runs: ${result.runs.length}`);
  if (result.scenario === "app-auth-large-tabs") {
    console.log(
      `App shell ready: median ${fmtMs(metrics.appShellReadyMs.median)}, p75 ${fmtMs(metrics.appShellReadyMs.p75)}`,
    );
    console.log(
      `App ready: median ${fmtMs(metrics.appReadyMs.median)}, p75 ${fmtMs(metrics.appReadyMs.p75)}`,
    );
    console.log(
      `Tab switches: map->garden ${fmtMs(metrics.mapToGardenMs.median)}, garden->insights ${fmtMs(metrics.gardenToInsightsMs.median)}, insights->map ${fmtMs(metrics.insightsToMapMs.median)}`,
    );
    console.log(
      `Warm switches: map->garden ${fmtMs(metrics.warmMapToGardenMs.median)}, garden->insights ${fmtMs(metrics.warmGardenToInsightsMs.median)}, insights->map ${fmtMs(metrics.warmInsightsToMapMs.median)}`,
    );
    console.log(
      `Max tab switch: median ${fmtMs(metrics.maxTabSwitchMs.median)}, p75 ${fmtMs(metrics.maxTabSwitchMs.p75)}; max long task median ${fmtMs(metrics.maxLongTaskMs.median)}`,
    );
    console.log(
      `Mounted rows: plants median ${metrics.mountedPlantRows.median}, care median ${metrics.mountedCareRows.median}`,
    );
    console.log(
      `Mounted cards: plants median ${metrics.mountedPlantCards.median}, care median ${metrics.mountedCareCards.median}`,
    );
  } else if (result.scenario === "app-auth") {
    console.log(
      `App shell ready: median ${fmtMs(metrics.appShellReadyMs.median)}, p75 ${fmtMs(metrics.appShellReadyMs.p75)}`,
    );
    console.log(
      `App ready: median ${fmtMs(metrics.appReadyMs.median)}, p75 ${fmtMs(metrics.appReadyMs.p75)}`,
    );
    console.log(
      `Garden tab switch: median ${fmtMs(metrics.tabSwitchMs.median)}, p75 ${fmtMs(metrics.tabSwitchMs.p75)}`,
    );
  } else {
    console.log(
      `Auth gate ready: median ${fmtMs(metrics.authGateReadyMs.median)}, p75 ${fmtMs(metrics.authGateReadyMs.p75)}`,
    );
    console.log(
      `Username Enter: median ${fmtMs(metrics.usernameEnterMs.median)}, p75 ${fmtMs(metrics.usernameEnterMs.p75)}`,
    );
  }
  console.log(
    `DOMContentLoaded: median ${fmtMs(metrics.domContentLoadedMs.median)}; load: median ${fmtMs(metrics.loadEventMs.median)}; FCP: median ${fmtMs(metrics.firstContentfulPaintMs.median)}`,
  );
  console.log(
    `Resource encoded bytes: median ${metrics.resourceEncodedBytes.median}; last run resources: ${result.summary.lastRunResources?.totals.count ?? 0}`,
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

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (err) {
    fail(err.message);
  }

  if (options.help) {
    usage();
    return;
  }

  const browserPath = findBrowserPath(options.browserPath);
  if (!browserPath) {
    fail("No Chromium executable found. Set PERF_CHROMIUM or pass --browser.");
  }
  if (!fs.existsSync(PLAYWRIGHT_PATH)) {
    fail("Missing frontend/node_modules/playwright-core. Run npm install in frontend.");
  }

  let server = null;
  let chromium;
  try {
    ({ chromium } = require(PLAYWRIGHT_PATH));
    if (options.serve) {
      server = await startServer(options);
    }

    const browser = await chromium.launch({
      executablePath: browserPath,
      headless: !options.headful,
    });
    try {
      const runs = [];
      for (let i = 1; i <= options.runs; i += 1) {
        runs.push(await runMeasuredScenario(browser, options, i));
      }
      const result = {
        browserPath,
        compare: null,
        createdAt: new Date().toISOString(),
        scenario: options.scenario,
        serveMode: options.serve ? options.serveMode : "external",
        skipInteraction: options.skipInteraction,
        stubApi: options.stubApi,
        summary: summarizeRuns(runs, options.scenario),
        url: options.url,
        runs,
      };

      if (options.comparePath) {
        const previous = JSON.parse(
          fs.readFileSync(path.resolve(process.cwd(), options.comparePath), "utf8"),
        );
        result.compare = compareSummaries(
          result,
          previous,
          options,
        );
        const regressions = result.compare.filter((row) => row.regressed);
        if (regressions.length > 0) {
          throw new Error(
            `Performance regression versus ${options.comparePath}: ${regressions
              .map((row) => `${row.metric} +${row.changePct}%`)
              .join(", ")}`,
          );
        }
      }

      enforceBudgets(result, options);
      writeOutput(options.outputPath, result);
      if (options.json) {
        console.log(JSON.stringify(result, null, 2));
      } else {
        printHuman(result, options.outputPath);
      }
    } finally {
      await browser.close();
    }
  } catch (err) {
    fail(err.message);
  } finally {
    if (server) {
      await server.stop();
    }
  }
}

main();
