#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ROOT_DIR = path.resolve(__dirname, "..");
const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5183";
const ARTIFACT_DIR = process.env.GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ARTIFACT_DIR || "";
const CHROMIUM_EXECUTABLE = "/usr/bin/chromium";
const E2E_USERNAME = process.env.GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_USERNAME || "";
const E2E_PASSWORD = process.env.GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PASSWORD || ""; // push-sanitizer: allow SECRET_ASSIGNMENT - runtime-only disposable fixture
const RESTORE_SNAPSHOT_NAME = "Optimization restore layout";
const OFFLINE_REPLAY_TITLE = "Optimization Offline Journal Replay";
const GARDEN_A_NOTIFICATION = "Optimization Garden A notice";
const GARDEN_B_NOTIFICATION = "Optimization Garden B notice";
const GARDEN_A_PLOT_ID = "OPT-JOURNEY-A-PLOT";
const GARDEN_A_EXTRA_PLOT_ID = "OPT-JOURNEY-A-EXTRA";
const GARDEN_A_PLANT_ID = "OPT-JOURNEY-A-PLANT";
const GARDEN_A_PLANT_NAME = "Optimization A Plant";
const GARDEN_A_TASK_ID = "tsk_optimization_journeys_a";
const GARDEN_A_TASK_TITLE = "Water Optimization A Plant";
const GARDEN_A_OBJECT = "Optimization A Map Object";
const GARDEN_B_OBJECT = "Optimization B Map Object";
const DELETE_TARGET_OBJECT = "E2E Delete Target Map Object";
const GARDEN_A_OBJECT_ID = "obj_optimization_journeys_a";
const GARDEN_B_OBJECT_ID = "obj_optimization_journeys_b";
const DELETE_TARGET_OBJECT_ID = "obj_optimization_journeys_delete_target";
const DELETE_TARGET_SLUG = "e2e-optimization-delete-target";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertNoResponseMocks() {
  const source = fs.readFileSync(__filename, "utf8");
  const fulfillNeedle = [".", "fulfill("].join("");
  assert(!source.includes(fulfillNeedle), "Real-backend journey checker must not fulfill product responses");
}

function assertPrivateArtifactDirectory() {
  assert(ARTIFACT_DIR, "Optimization journey E2E artifact directory is required");
  const requested = path.resolve(ARTIFACT_DIR);
  const stat = fs.lstatSync(requested);
  assert(stat.isDirectory() && !stat.isSymbolicLink(), "Optimization journey artifacts require a real directory");
  const resolved = fs.realpathSync.native(requested);
  assert(
    resolved.startsWith("/tmp/gardenops-optimization-journeys."),
    "Optimization journey artifacts must use the private /tmp runner directory",
  );
  assert((stat.mode & 0o077) === 0, "Optimization journey artifact directory must be owner-private");
}

function assertRunnerEnvironment() {
  assert(process.env.APP_ENV === "test", "Optimization journey E2E requires APP_ENV=test");
  assert(process.env.AUTH_REQUIRED === "true", "Optimization journey E2E requires AUTH_REQUIRED=true");
  assert(process.env.AUTH_MODE === "session", "Optimization journey E2E requires AUTH_MODE=session");
  assert(process.env.AI_PROVIDER === "disabled", "Optimization journey E2E requires AI_PROVIDER=disabled");
  assert(
    process.env.GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED === "false",
    "Optimization journey E2E requires the notification scheduler disabled",
  );
  assert(
    process.env.GARDENOPS_ALLOW_DESTRUCTIVE_E2E === "1",
    "Optimization journey E2E requires GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1",
  );
  assert(
    process.env.GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE === "1",
    "Optimization journey E2E requires an explicit truncate guard",
  );
  const issuedUrl = process.env.GARDENOPS_DISPOSABLE_POSTGRES_URL || "";
  assert(issuedUrl && process.env.DATABASE_URL === issuedUrl, "DATABASE_URL must exactly match the runner-issued URL");
  const marker = process.env.GARDENOPS_DISPOSABLE_POSTGRES_MARKER || "";
  const systemIdentifier = process.env.GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER || "";
  assert(marker.startsWith(`${systemIdentifier}.`) && systemIdentifier, "Disposable marker is not bound to the runner system identifier");
  const parsed = new URL(issuedUrl);
  assert(parsed.hostname === "127.0.0.1", "Disposable database must use loopback TCP");
  assert(parsed.port !== "5432" && parsed.port, "Disposable database must not use port 5432");
  assert(parsed.pathname === "/gardenops_test", "Disposable database must be gardenops_test");
}

async function installLoopbackRequestGuard(context) {
  const blocked = [];
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      blocked.push(url);
      await route.abort("blockedbyclient");
      return;
    }
    if (
      ["http:", "https:", "ws:", "wss:"].includes(parsed.protocol)
      && !["127.0.0.1", "::1", "localhost"].includes(parsed.hostname)
    ) {
      blocked.push(url);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  await context.routeWebSocket(
    (url) => {
      const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
      return !["127.0.0.1", "::1", "localhost"].includes(hostname);
    },
    (socket) => {
      blocked.push(socket.url());
      socket.close({ code: 1008, reason: "Non-loopback E2E traffic is blocked" });
    },
  );
  return blocked;
}

function createBrowserDiagnostics(page, blockedRequests) {
  const errors = [];
  const nonLoopbackRequests = [];
  page.on("pageerror", (error) => errors.push(error.stack || error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      errors.push(message.text());
    }
  });
  page.on("request", (request) => {
    let parsed;
    try {
      parsed = new URL(request.url());
    } catch {
      return;
    }
    if (!["http:", "https:", "ws:", "wss:"].includes(parsed.protocol)) return;
    if (!["127.0.0.1", "::1", "localhost"].includes(parsed.hostname)) {
      nonLoopbackRequests.push(`${parsed.protocol}//${parsed.host}${parsed.pathname}`);
    }
  });
  return {
    assertClean(label) {
      assert(errors.length === 0, `${label} browser errors:\n${errors.join("\n")}`);
      assert(
        nonLoopbackRequests.length === 0,
        `${label} made non-loopback requests:\n${nonLoopbackRequests.join("\n")}`,
      );
      assert(
        blockedRequests.length === 0,
        `${label} blocked non-loopback requests:\n${blockedRequests.join("\n")}`,
      );
    },
  };
}

function writeManifest(manifest) {
  const manifestPath = path.join(ARTIFACT_DIR, "optimization-journeys-manifest.json");
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`, { mode: 0o600 });
  fs.chmodSync(manifestPath, 0o600);
  return manifestPath;
}

function safeFailureMessage(error) {
  const message = error instanceof Error ? error.message : "journey assertion failed";
  return message.replaceAll(E2E_PASSWORD, "[redacted]").slice(0, 400);
}

function runSnapshot(options = {}) {
  const python = process.env.GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PYTHON
    || path.join(ROOT_DIR, ".venv", "bin", "python");
  const env = { ...process.env };
  if (options.targetGardenId !== undefined) {
    env.GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_SNAPSHOT_TARGET_GARDEN_ID = String(options.targetGardenId);
  }
  const result = spawnSync(
    python,
    ["scripts/seed_optimization_journeys_e2e.py", "snapshot"],
    { cwd: ROOT_DIR, encoding: "utf8", env },
  );
  assert(result.status === 0, "Optimization journey database snapshot failed");
  try {
    return JSON.parse(result.stdout.trim());
  } catch {
    throw new Error("Optimization journey database snapshot was not valid JSON");
  }
}

async function visible(locator, label, timeout = 15000) {
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch (error) {
    throw new Error(`Expected visible ${label}: ${error.message}`);
  }
}

async function waitFor(condition, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function createApiRecorder(page) {
  const records = [];
  const byRequest = new Map();
  page.on("request", (request) => {
    let parsed;
    try {
      parsed = new URL(request.url());
    } catch {
      return;
    }
    if (!parsed.pathname.startsWith("/api/")) return;
    const headers = request.headers();
    const record = {
      gardenId: headers["x-garden-id"] || null,
      method: request.method(),
      path: parsed.pathname,
      startedAt: Date.now(),
      status: null,
      finishedAt: null,
    };
    records.push(record);
    byRequest.set(request, record);
  });
  page.on("response", (response) => {
    const record = byRequest.get(response.request());
    if (!record) return;
    record.status = response.status();
  });
  page.on("requestfinished", (request) => {
    const record = byRequest.get(request);
    if (!record) return;
    record.finishedAt = Date.now();
  });
  page.on("requestfailed", (request) => {
    const record = byRequest.get(request);
    if (!record) return;
    record.finishedAt = Date.now();
  });
  return {
    mark: () => records.length,
    records,
    since: (marker) => records.slice(marker),
  };
}

function mapRequestKind(record, gardenId) {
  if (record.method !== "GET" || record.gardenId !== String(gardenId)) return null;
  if (record.path === "/api/plots") return "plots";
  if (record.path === "/api/layout-state") return "layout";
  if (record.path === `/api/gardens/${gardenId}/map-objects`) return "mapObjects";
  return null;
}

function isScopedGardenRequest(record) {
  if (!record.path.startsWith("/api/")) return false;
  return record.path === "/api/plots"
    || record.path.startsWith("/api/plots/")
    || record.path === "/api/layout-state"
    || record.path.startsWith("/api/journal")
    || record.path.startsWith("/api/tasks")
    || record.path.startsWith("/api/care")
    || record.path.startsWith("/api/attention")
    || /^\/api\/gardens\/\d+\//.test(record.path);
}

async function authenticate(page) {
  assert(E2E_USERNAME, "Optimization journey E2E username is required");
  assert(E2E_PASSWORD, "Optimization journey E2E password is required");
  const gate = page.locator(".auth-gate");
  await visible(gate, "session sign-in gate");
  const form = gate.locator("#auth-gate-form");
  await form.locator("input[name='username']").fill(E2E_USERNAME);
  await form.locator("button[type='submit']").click();
  const password = form.locator("input[name='password']");
  await visible(password, "session sign-in password field");
  await password.fill(E2E_PASSWORD);
  await form.locator("button[type='submit']").click();
  await gate.waitFor({ state: "detached", timeout: 15000 });
  const profile = await page.evaluate(async () => {
    const response = await fetch("/api/auth/me", { credentials: "include" });
    return { body: await response.json(), status: response.status };
  });
  assert(profile.status === 200, "Session profile endpoint did not return 200");
  assert(profile.body.username === E2E_USERNAME, "Session profile user does not match fixture administrator");
  assert(profile.body.auth_type === "session", "Fixture administrator is not using session authentication");
  assert(profile.body.role === "admin", "Fixture user is not a platform administrator");
}

async function openPrimaryTab(page, tab) {
  const button = page.locator(`#top-tab-${tab}`);
  await visible(button, `${tab} primary tab`);
  await button.click();
  await visible(page.locator(`#top-tab-${tab}[aria-selected='true']`), `${tab} selected tab`);
}

async function openSubMode(page, mode, rootSelector) {
  const button = page.locator(`[data-sub-mode='${mode}']`).filter({ visible: true }).first();
  await visible(button, `${mode} sub-mode`);
  await button.click();
  await visible(page.locator(rootSelector), `${mode} surface`);
}

async function acceptConfirm(page, label) {
  const dialog = page.locator(".modal[role='alertdialog']").last();
  await visible(dialog, `${label} confirmation`);
  await dialog.locator(".confirm-yes").click();
}

async function cancelConfirm(page, label) {
  const dialog = page.locator(".modal[role='alertdialog']").last();
  await visible(dialog, `${label} confirmation`);
  await dialog.locator(".confirm-no").click();
}

async function fillPrompt(page, value, inputType, label) {
  const input = page.locator(`.modal input.prompt-dialog-input[type='${inputType}']`).last();
  await visible(input, `${label} ${inputType} prompt`);
  await input.fill(value);
  await input.locator("xpath=ancestor::div[contains(@class, 'modal')]").locator(".confirm-yes").click();
}

async function browserJson(page, path, options = {}) {
  return page.evaluate(async ({ body, gardenId, method, requestPath }) => {
    const csrf = document.cookie.split("; ").find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const headers = {
      "X-CSRF-Token": csrf,
      ...(gardenId == null ? {} : { "X-Garden-Id": String(gardenId) }),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    };
    const response = await fetch(requestPath, {
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      headers,
      method,
    });
    const text = await response.text();
    let responseBody = null;
    if (text) {
      try {
        responseBody = JSON.parse(text);
      } catch {
        responseBody = text;
      }
    }
    return { body: responseBody, status: response.status };
  }, {
    body: options.body,
    gardenId: options.gardenId,
    method: options.method || "GET",
    requestPath: path,
  });
}

async function readRestoreState(page, gardenAId) {
  const [layout, plots, objects, plants] = await Promise.all([
    browserJson(page, "/api/layout-state", { gardenId: gardenAId }),
    browserJson(page, "/api/plots", { gardenId: gardenAId }),
    browserJson(page, `/api/gardens/${gardenAId}/map-objects`, { gardenId: gardenAId }),
    browserJson(page, "/api/plants", { gardenId: gardenAId }),
  ]);
  for (const response of [layout, plots, objects, plants]) {
    assert(response.status === 200, `Restore-state read returned ${response.status}`);
  }
  const plotRows = Array.isArray(plots.body) ? plots.body : plots.body?.plots;
  const plantRows = Array.isArray(plants.body) ? plants.body : plants.body?.plants;
  return {
    extraPlot: plotRows.find((item) => item.plot_id === GARDEN_A_EXTRA_PLOT_ID),
    layout: layout.body,
    object: objects.body.objects.find((item) => item.public_id === GARDEN_A_OBJECT_ID),
    plant: plantRows.find((item) => item.plt_id === GARDEN_A_PLANT_ID),
    plot: plotRows.find((item) => item.plot_id === GARDEN_A_PLOT_ID),
  };
}

async function mutateRestoreState(page, gardenAId) {
  const mutations = await Promise.all([
    browserJson(page, "/api/layout-state", {
      body: { row: 5, col: 8, width: 3, height: 2, north_degrees: 91, grid_rows: 12, grid_cols: 16 },
      gardenId: gardenAId,
      method: "PATCH",
    }),
    browserJson(page, `/api/plots/${GARDEN_A_PLOT_ID}`, {
      body: { grid_row: 10, grid_col: 12 },
      gardenId: gardenAId,
      method: "PATCH",
    }),
    browserJson(page, `/api/gardens/${gardenAId}/map-objects/${GARDEN_A_OBJECT_ID}`, {
      body: { geometry: { x: 1, y: 1, width: 2, height: 2 } },
      gardenId: gardenAId,
      method: "PATCH",
    }),
    browserJson(page, "/api/plots", {
      body: {
        plot_id: GARDEN_A_EXTRA_PLOT_ID,
        zone_code: "X",
        zone_name: "Divergent extra plot",
        plot_number: 2,
        grid_row: 8,
        grid_col: 14,
      },
      gardenId: gardenAId,
      method: "POST",
    }),
  ]);
  assert(
    mutations.every((response) => [200, 201].includes(response.status)),
    `Divergent restore fixture mutation failed: ${mutations.map((item) => item.status).join(",")}`,
  );
  const assignment = await browserJson(
    page,
    `/api/plots/${GARDEN_A_EXTRA_PLOT_ID}/plants/${GARDEN_A_PLANT_ID}`,
    {
      body: { quantity: 1 },
      gardenId: gardenAId,
      method: "POST",
    },
  );
  assert(assignment.status === 201, `Divergent plant assignment returned ${assignment.status}`);
  await page.reload({ waitUntil: "domcontentloaded" });
  await visible(page.locator("#map-grid"), "Map grid after divergent fixture mutation");
  await waitForMapObject(page, GARDEN_A_OBJECT_ID, GARDEN_A_OBJECT);
  const divergent = await readRestoreState(page, gardenAId);
  assert(divergent.layout.row === 5 && divergent.layout.col === 8, "Layout did not diverge before restore");
  assert(divergent.plot.grid_row === 10 && divergent.plot.grid_col === 12, "Plot did not diverge before restore");
  assert(divergent.object.geometry.x === 1 && divergent.object.geometry.y === 1, "Map object did not diverge before restore");
  assert(divergent.extraPlot, "Divergent extra plot was not created before restore");
  assert(divergent.plant.plot_ids.includes(GARDEN_A_EXTRA_PLOT_ID), "Plant assignment did not diverge before restore");

  await openPrimaryTab(page, "garden");
  await openSubMode(page, "plants", "#plants-tab-content");
  const plantRow = page.locator("#plants-tab-content tr").filter({ hasText: GARDEN_A_PLANT_NAME }).first();
  await visible(plantRow, "divergent Garden A plant row");
  assert(
    (await plantRow.locator(".plot-links-cell").textContent() || "").includes(GARDEN_A_EXTRA_PLOT_ID),
    "Plants UI did not load the divergent extra assignment before restore",
  );
  await openPrimaryTab(page, "map");
}

async function installMapGridInstrumentation(page) {
  await page.evaluate(() => {
    const grid = document.getElementById("map-grid");
    if (!(grid instanceof HTMLElement)) throw new Error("map grid is unavailable for instrumentation");
    const original = grid.replaceChildren.bind(grid);
    let replaceCount = 0;
    Object.defineProperty(grid, "replaceChildren", {
      configurable: true,
      value: (...children) => {
        replaceCount += 1;
        return original(...children);
      },
      writable: true,
    });
    window.__optimizationJourneyMapGrid = {
      getReplaceCount: () => replaceCount,
    };
  });
}

async function getMapGridReplaceCount(page) {
  return page.evaluate(() => window.__optimizationJourneyMapGrid?.getReplaceCount?.() ?? -1);
}

async function runMapFirstAndRestore(page, recorder, manifest, gardenAId) {
  await visible(page.locator("#map-view"), "initial Map view");
  await visible(page.locator("#map-grid"), "initial Map grid");
  await waitForMapObject(page, GARDEN_A_OBJECT_ID, GARDEN_A_OBJECT);
  await page.waitForTimeout(100);
  const mapFirstPlantRequests = recorder.records.filter(
    (record) => record.method === "GET" && record.path === "/api/plants",
  );
  assert(
    mapFirstPlantRequests.length === 0,
    "Map-first startup made GET /api/plants before a plant-dependent workflow",
  );
  manifest.checks.map_first_without_plants = true;

  await mutateRestoreState(page, gardenAId);

  await openPrimaryTab(page, "admin");
  await visible(page.locator("#admin-view"), "admin view for layout restore");
  const gardenSection = page.locator(".adm-nav-btn[data-section='garden']");
  await visible(gardenSection, "admin garden section");
  await gardenSection.click();
  const layoutsButton = page.locator("#adm-map-layouts-btn");
  await visible(layoutsButton, "garden layout list action");
  await layoutsButton.click();
  const layoutsDialog = page.locator("#map-layouts-dialog");
  await visible(layoutsDialog, "layout snapshot dialog");
  const restoreButton = layoutsDialog.locator(".snapshot-restore").filter({
    hasText: RESTORE_SNAPSHOT_NAME,
  });
  await visible(restoreButton, "seeded layout restore action");
  await page.waitForTimeout(150);
  await installMapGridInstrumentation(page);

  const marker = recorder.mark();
  const startedAt = Date.now();
  const restored = page.waitForResponse((response) => {
    const request = response.request();
    return request.method() === "POST"
      && response.url().includes("/api/snapshots/")
      && response.url().endsWith("/restore");
  });
  await restoreButton.click();
  await acceptConfirm(page, "layout restore");
  await fillPrompt(page, "optimization-journey-layout-restore", "text", "layout restore reason");
  await fillPrompt(page, E2E_PASSWORD, "password", "layout restore password");
  const restoreResponse = await restored;
  assert(
    restoreResponse.status() === 200,
    `Layout restore did not return 200 (received ${restoreResponse.status()})`,
  );
  await visible(page.locator("#map-view"), "Map view after layout restore");

  await waitFor(() => {
    const kinds = new Set(
      recorder.since(marker)
        .map((record) => mapRequestKind(record, gardenAId))
        .filter(Boolean),
    );
    return kinds.has("plots") && kinds.has("layout") && kinds.has("mapObjects");
  }, "parallel layout, plots, and map-object refresh requests");
  await waitFor(() => recorder.since(marker).filter(
    (record) => mapRequestKind(record, gardenAId),
  ).every((record) => record.finishedAt !== null), "layout refresh completion");

  const mapRequests = {};
  for (const record of recorder.since(marker)) {
    const kind = mapRequestKind(record, gardenAId);
    if (kind && !mapRequests[kind]) mapRequests[kind] = record;
  }
  const requestValues = Object.values(mapRequests);
  assert(requestValues.length === 3, "Layout restore did not produce all three map refresh GETs");
  const starts = requestValues.map((record) => record.startedAt);
  const ends = requestValues.map((record) => record.finishedAt || Date.now());
  const startSpreadMs = Math.max(...starts) - Math.min(...starts);
  const commonOverlapMs = Math.min(...ends) - Math.max(...starts);
  assert(startSpreadMs <= 300, "Map layout, plot, and object GETs did not start concurrently");
  assert(commonOverlapMs >= -25, "Map layout, plot, and object GETs did not overlap");
  const replaceCount = await getMapGridReplaceCount(page);
  assert(replaceCount === 1, `Expected one map-grid replaceChildren call after restore, got ${replaceCount}`);
  const restoredState = await readRestoreState(page, gardenAId);
  assert(restoredState.layout.row === 2 && restoredState.layout.col === 2, "Snapshot did not restore house layout values");
  assert(restoredState.layout.north_degrees === 18, "Snapshot did not restore north orientation");
  assert(restoredState.plot.grid_row === 7 && restoredState.plot.grid_col === 3, "Snapshot did not restore plot coordinates");
  assert(restoredState.object.geometry.x === 9 && restoredState.object.geometry.y === 2, "Snapshot did not restore map-object geometry");
  assert(restoredState.plant.plot_ids.includes(GARDEN_A_PLOT_ID), "Snapshot did not restore plant assignment");
  assert(!restoredState.extraPlot, "Snapshot retained a plot absent from the saved layout");
  assert(!restoredState.plant.plot_ids.includes(GARDEN_A_EXTRA_PLOT_ID), "Snapshot retained an assignment to a removed plot");
  await openPrimaryTab(page, "garden");
  await openSubMode(page, "plants", "#plants-tab-content");
  const restoredPlantRow = page.locator("#plants-tab-content tr").filter({ hasText: GARDEN_A_PLANT_NAME }).first();
  await visible(restoredPlantRow, "restored Garden A plant row");
  assert(
    (await restoredPlantRow.locator(".plot-links-cell").textContent() || "").includes(GARDEN_A_PLOT_ID),
    "Plants UI retained the pre-restore assignment cache",
  );
  assert(
    !(await restoredPlantRow.locator(".plot-links-cell").textContent() || "").includes(GARDEN_A_EXTRA_PLOT_ID),
    "Plants UI retained the removed divergent plot assignment",
  );
  await openPrimaryTab(page, "map");
  manifest.checks.layout_restore = true;
  manifest.checks.layout_restore_semantics = true;
  manifest.timings_ms.layout_restore = {
    duration: Date.now() - startedAt,
    grid_replace_children: replaceCount,
    request_start_spread: startSpreadMs,
  };
}

async function waitForMapObject(page, objectId, objectName) {
  await visible(
    page.locator(`.map-object-label[data-object-id='${objectId}']`),
    `${objectName} map object`,
  );
}

async function switchGardenAndAssert(
  page,
  recorder,
  fromObjectId,
  fromObjectName,
  toObjectId,
  toObjectName,
  gardenId,
) {
  const select = page.locator("#garden-select");
  await visible(select, "desktop garden selector");
  const marker = recorder.mark();
  await select.selectOption(String(gardenId));
  const oldObjectStillVisible = await page
    .locator(`.map-object-label[data-object-id='${fromObjectId}']`)
    .isVisible()
    .catch(() => false);
  assert(!oldObjectStillVisible, `Old garden label ${fromObjectName} was not cleared immediately`);
  await waitForMapObject(page, toObjectId, toObjectName);
  await waitFor(() => recorder.since(marker).filter(isScopedGardenRequest).length >= 3, "scoped garden refresh requests");
  const scopedRequests = recorder.since(marker).filter(isScopedGardenRequest);
  assert(
    scopedRequests.every((record) => record.gardenId === String(gardenId)),
    `A scoped request used a stale x-garden-id while switching to ${gardenId}`,
  );
  return scopedRequests.length;
}

async function openNotificationPanelAndAssert(page, expectedTitle, rejectedTitle) {
  const trigger = page.locator("#notification-bell");
  await visible(trigger, "desktop notification trigger");
  await trigger.click();
  const panel = page.locator("#notification-panel");
  await visible(panel, "garden-scoped notification panel");
  await visible(panel.getByText(expectedTitle, { exact: true }), `${expectedTitle} notification`);
  assert(
    await panel.getByText(rejectedTitle, { exact: true }).count() === 0,
    `Notification panel leaked ${rejectedTitle}`,
  );
}

async function runGardenSwitching(page, recorder, manifest, ids) {
  await openNotificationPanelAndAssert(page, GARDEN_A_NOTIFICATION, GARDEN_B_NOTIFICATION);
  const toBRequests = await switchGardenAndAssert(
    page,
    recorder,
    GARDEN_A_OBJECT_ID,
    GARDEN_A_OBJECT,
    GARDEN_B_OBJECT_ID,
    GARDEN_B_OBJECT,
    ids.b,
  );
  assert(await page.locator("#notification-panel").isHidden(), "Garden switch did not close the old notification panel");
  await openNotificationPanelAndAssert(page, GARDEN_B_NOTIFICATION, GARDEN_A_NOTIFICATION);
  await page.locator("#notification-bell").click();
  const backToARequests = await switchGardenAndAssert(
    page,
    recorder,
    GARDEN_B_OBJECT_ID,
    GARDEN_B_OBJECT,
    GARDEN_A_OBJECT_ID,
    GARDEN_A_OBJECT,
    ids.a,
  );

  const select = page.locator("#garden-select");
  const rapidMarker = recorder.mark();
  await select.selectOption(String(ids.b));
  await select.selectOption(String(ids.a));
  await waitForMapObject(page, GARDEN_A_OBJECT_ID, GARDEN_A_OBJECT);
  assert(await select.inputValue() === String(ids.a), "Rapid A/B/A selection did not keep Garden A final state");
  const staleBVisible = await page
    .locator(`.map-object-label[data-object-id='${GARDEN_B_OBJECT_ID}']`)
    .isVisible()
    .catch(() => false);
  assert(!staleBVisible, "Rapid A/B/A selection rendered stale Garden B map data");
  await waitFor(() => recorder.since(rapidMarker).filter(isScopedGardenRequest).length >= 3, "rapid switch scoped requests");
  manifest.checks.garden_switching = true;
  manifest.checks.garden_scoped_notifications = true;
  manifest.timings_ms.garden_switch = {
    rapid_request_count: recorder.since(rapidMarker).filter(isScopedGardenRequest).length,
    switch_a_to_b_scoped_requests: toBRequests,
    switch_b_to_a_scoped_requests: backToARequests,
  };
}

async function enterPlantWorkflow(page) {
  await openPrimaryTab(page, "garden");
  await openSubMode(page, "plants", "#plants-tab-content");
  await visible(
    page.locator("#plants-tab-content").getByText("Optimization A Plant", { exact: true }),
    "Garden A plant after entering the plant workflow",
  );
}

async function readOfflineDrafts(page) {
  return page.evaluate(async () => {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("gardenops-offline");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
    });
    try {
      const transaction = database.transaction("drafts", "readonly");
      const store = transaction.objectStore("drafts");
      const rows = await new Promise((resolve, reject) => {
        const request = store.getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("IndexedDB read failed"));
      });
      return rows.map((draft) => ({
        garden_id: draft.garden_id,
        id: draft.id,
        operation_id: draft.operation_id,
        payload: draft.payload,
        serialized_media: Array.isArray(draft.payload?._serialized_media)
          ? draft.payload._serialized_media.map((item) => ({
            bytes: Array.from(new Uint8Array(item.buffer)),
            name: item.name,
            operation_id: item.operation_id,
            type: item.type,
          }))
          : [],
        status: draft.status,
        type: draft.type,
      }));
    } finally {
      database.close();
    }
  });
}

async function runOfflineJournalReplay(page, context, recorder, manifest, gardenAId) {
  await openPrimaryTab(page, "activity");
  const journalLoaded = page.waitForResponse((response) => (
    response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/journal"
  ));
  await openSubMode(page, "journal", "#journal-tab-content");
  const journalResponse = await journalLoaded;
  assert(journalResponse.status() === 200, `Initial journal load returned ${journalResponse.status()}`);
  await visible(page.locator("#journal-add-btn"), "journal add action");
  await context.setOffline(true);
  assert(await page.evaluate(() => navigator.onLine === false), "Playwright offline mode did not set navigator offline");
  const offlinePostMarker = recorder.mark();
  await page.locator("#journal-add-btn").click();
  const composer = page.locator(".journal-composer");
  await visible(composer, "offline journal composer");
  await composer.locator("select[name='event_type']").selectOption("observed");
  await composer.locator("input[name='occurred_on']").fill("2026-07-10");
  await composer.locator("input[name='title']").fill(OFFLINE_REPLAY_TITLE);
  await composer.locator("textarea[name='notes']").fill("Queued while offline for idempotent replay.");
  await composer.locator(".media-file-input").setInputFiles({
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGMMqFjAwMDAxMDAwMDAAAAQugFsZnyF3gAAAABJRU5ErkJggg==",
      "base64",
    ),
    mimeType: "image/png",
    name: "offline-replay.png",
  });
  await composer.locator(".journal-btn-submit").click();
  await composer.waitFor({ state: "detached", timeout: 10000 });
  const queuedPosts = recorder.since(offlinePostMarker).filter(
    (record) => record.method === "POST" && record.path === "/api/journal",
  );
  assert(queuedPosts.length === 0, "Offline journal creation made a network POST instead of queuing");

  const drafts = await readOfflineDrafts(page);
  assert(drafts.length === 1, `Expected one queued offline journal draft, got ${drafts.length}`);
  const draft = drafts[0];
  assert(draft.type === "journal", "Queued draft is not a journal operation");
  assert(typeof draft.operation_id === "string" && draft.operation_id.length > 0, "Queued journal draft lacks a stable operation_id");
  assert(draft.garden_id === gardenAId, "Queued journal draft did not preserve its original garden_id");
  assert(draft.payload?.title === OFFLINE_REPLAY_TITLE, "Queued journal draft did not preserve its original payload");
  assert(draft.serialized_media.length === 1, "Queued journal draft did not retain its attachment");
  const attachment = draft.serialized_media[0];
  assert(
    typeof attachment.operation_id === "string" && attachment.operation_id.length > 0,
    "Queued journal attachment lacks a stable operation_id",
  );

  const replayMarker = recorder.mark();
  const initialReplayResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/journal"
      && response.request().headers()["x-offline-operation-id"] === draft.operation_id
  ));
  const initialMediaResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/media/upload"
      && response.request().headers()["x-offline-operation-id"] === attachment.operation_id
  ));
  await context.setOffline(false);
  let replayStarted = false;
  try {
    await waitFor(
      () => recorder.since(replayMarker).some(
        (record) => record.method === "POST" && record.path === "/api/journal",
      ),
      "automatic offline replay start",
      1200,
    );
    replayStarted = true;
  } catch {
    // Playwright network emulation is not required to dispatch an online event.
  }
  if (!replayStarted) {
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
  }
  await waitFor(
    () => recorder.since(replayMarker).some(
      (record) => record.method === "POST" && record.path === "/api/journal" && record.status === 201,
    ),
    "real journal replay POST",
  );
  await waitFor(async () => (await readOfflineDrafts(page)).length === 0, "offline queue clear after replay");
  const replayPosts = recorder.since(replayMarker).filter(
    (record) => record.method === "POST" && record.path === "/api/journal",
  );
  assert(replayPosts.length === 1, `Expected exactly one replay POST, got ${replayPosts.length}`);
  assert(replayPosts[0].gardenId === String(gardenAId), "Replay POST did not use the draft's original x-garden-id");
  const initialReplay = await initialReplayResponse;
  const initialReplayBody = await initialReplay.json();
  assert(typeof initialReplayBody.id === "string" && initialReplayBody.id, "Initial replay did not return a journal resource id");
  const initialMedia = await initialMediaResponse;
  assert(initialMedia.status() === 201, "Initial offline attachment replay did not return 201");
  const initialMediaBody = await initialMedia.json();
  const initialRecord = replayPosts[0];
  await waitFor(() => initialRecord.finishedAt !== null, "initial replay response completion");
  const { _serialized_media: _serializedMedia, ...journalReplayPayload } = draft.payload;
  const replayed = await page.evaluate(async ({ gardenId, operationId, payload }) => {
    const csrf = document.cookie.split("; ").find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const response = await fetch("/api/journal", {
      body: JSON.stringify(payload),
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "X-Garden-Id": String(gardenId),
        "X-Offline-Operation-Id": operationId,
      },
      method: "POST",
    });
    return { body: await response.json(), status: response.status };
  }, {
    gardenId: gardenAId,
    operationId: draft.operation_id,
    payload: journalReplayPayload,
  });
  assert(replayed.status === 201, "Repeated offline operation POST did not return the create status");
  assert(
    replayed.body.id === initialReplayBody.id,
    "Repeated journal operation_id did not resolve to the same resource",
  );
  const mediaReplayed = await page.evaluate(async ({ gardenId, journalId, media }) => {
    const csrf = document.cookie.split("; ").find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const response = await fetch(
      `/api/media/upload?target_type=journal_entry&target_id=${encodeURIComponent(journalId)}`,
      {
        body: new Uint8Array(media.bytes),
        credentials: "include",
        headers: {
          "Content-Type": media.type,
          "X-CSRF-Token": csrf,
          "X-Garden-Id": String(gardenId),
          "X-Offline-Operation-Id": media.operation_id,
          "X-Upload-Filename": media.name,
        },
        method: "POST",
      },
    );
    return { body: await response.json(), status: response.status };
  }, {
    gardenId: gardenAId,
    journalId: initialReplayBody.id,
    media: attachment,
  });
  assert(mediaReplayed.status === 201, "Repeated offline media operation did not return 201");
  assert(
    mediaReplayed.body.asset_id === initialMediaBody.asset_id,
    "Repeated attachment operation_id did not resolve to the same media asset",
  );
  manifest.checks.offline_journal_replay = true;
  manifest.checks.offline_media_replay = true;
  manifest.timings_ms.offline_journal_replay = {
    initial_replay_posts: replayPosts.length,
    queue_cleared: true,
    stable_operation_id: true,
  };
}

async function runOfflineTaskReplay(page, context, recorder, manifest, gardenAId) {
  await openPrimaryTab(page, "activity");
  const tasksLoaded = page.waitForResponse((response) => (
    response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/tasks"
  ));
  await openSubMode(page, "tasks", "#tasks-tab-content");
  assert((await tasksLoaded).status() === 200, "Initial task load did not return 200");
  const card = page.locator("#tasks-tab-content .task-card").filter({ hasText: GARDEN_A_TASK_TITLE }).first();
  await visible(card, "offline replay task card");
  await context.setOffline(true);
  await card.locator(".task-action-btn").filter({ hasText: "Skip" }).click();
  await waitFor(async () => (await readOfflineDrafts(page)).length === 1, "queued offline task action");
  const drafts = await readOfflineDrafts(page);
  const draft = drafts[0];
  assert(draft.type === "task_skip", "Queued task draft has the wrong action type");
  assert(draft.payload?.task_id === GARDEN_A_TASK_ID, "Queued task draft lost its task id");
  assert(draft.garden_id === gardenAId, "Queued task draft lost its garden id");
  assert(typeof draft.operation_id === "string" && draft.operation_id, "Queued task draft lacks an operation id");

  const replayMarker = recorder.mark();
  const initialResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/tasks/${GARDEN_A_TASK_ID}/action`
      && response.request().headers()["x-offline-operation-id"] === draft.operation_id
  ));
  await context.setOffline(false);
  let replayStarted = false;
  try {
    await waitFor(
      () => recorder.since(replayMarker).some(
        (record) => record.method === "POST" && record.path === `/api/tasks/${GARDEN_A_TASK_ID}/action`,
      ),
      "automatic offline task replay start",
      1200,
    );
    replayStarted = true;
  } catch {
    // Playwright network emulation may not dispatch the browser online event.
  }
  if (!replayStarted) {
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
  }
  const response = await initialResponse;
  assert(response.status() === 200, "Offline task replay did not return 200");
  await waitFor(async () => (await readOfflineDrafts(page)).length === 0, "offline task queue clear");
  const replayPosts = recorder.since(replayMarker).filter(
    (record) => record.method === "POST" && record.path === `/api/tasks/${GARDEN_A_TASK_ID}/action`,
  );
  assert(replayPosts.length === 1, `Expected one task replay POST, got ${replayPosts.length}`);
  const repeated = await page.evaluate(async ({ gardenId, operationId, taskId }) => {
    const csrf = document.cookie.split("; ").find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/action`, {
      body: JSON.stringify({ action: "skip" }),
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "X-Garden-Id": String(gardenId),
        "X-Offline-Operation-Id": operationId,
      },
      method: "POST",
    });
    return { body: await response.json(), status: response.status };
  }, { gardenId: gardenAId, operationId: draft.operation_id, taskId: GARDEN_A_TASK_ID });
  assert(repeated.status === 200 && repeated.body.status === "ok", "Repeated task operation was not idempotent");
  manifest.checks.offline_task_replay = true;
}

async function runProviderDisabledAnalysis(page, manifest) {
  await openPrimaryTab(page, "insights");
  await openSubMode(page, "analysis", "#analysis-view");
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/ai/garden-chat"
  ));
  await page.locator("#analysis-input").fill("What should I do with this garden today?");
  await page.locator("#analysis-send-btn").click();
  const response = await responsePromise;
  assert(response.status() === 503, "Provider-disabled analysis did not return a real 503");
  const responseBody = await response.json();
  assert(
    responseBody.detail === "AI provider not configured",
    "Provider-disabled analysis returned an unexpected backend error contract",
  );
  const errorBubble = page.locator("#analysis-messages .chat-error").last();
  await visible(errorBubble, "recoverable provider-disabled analysis error");
  assert(
    (await errorBubble.textContent() || "").trim().length > 0,
    "Provider-disabled analysis rendered an empty recoverable error",
  );
  assert(await page.locator("#analysis-input").isEnabled(), "Analysis input did not recover after provider-disabled error");
  assert(await page.locator("#analysis-send-btn").isEnabled(), "Analysis send action did not recover after provider-disabled error");
  manifest.checks.provider_disabled_analysis = true;
}

async function runDisposableGardenDeletion(page, recorder, manifest, targetGardenId) {
  await page.locator("#garden-select").selectOption(String(targetGardenId));
  await openPrimaryTab(page, "map");
  await waitForMapObject(page, DELETE_TARGET_OBJECT_ID, DELETE_TARGET_OBJECT);
  await openPrimaryTab(page, "admin");
  const gardenSection = page.locator(".adm-nav-btn[data-section='garden']");
  await visible(gardenSection, "delete target admin garden section");
  await gardenSection.click();
  const deleteButton = page.locator("#adm-garden-delete");
  await visible(deleteButton, "disposable target delete action");

  const cancelMarker = recorder.mark();
  await deleteButton.click();
  await cancelConfirm(page, "disposable garden delete");
  await page.waitForTimeout(250);
  const cancelledMutations = recorder.since(cancelMarker).filter(
    (record) => ["POST", "PUT", "PATCH", "DELETE"].includes(record.method),
  );
  assert(cancelledMutations.length === 0, "Cancelled garden deletion made a mutation request");
  const afterCancel = runSnapshot({ targetGardenId });
  assert(afterCancel.gardens.delete_target.exists, "Cancelled garden deletion removed the target garden");
  assert(afterCancel.audit.delete_target_count === 0, "Cancelled garden deletion wrote an audit row");

  const deletionResponse = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
      && new URL(response.url()).pathname === `/api/gardens/${targetGardenId}`
  ));
  await deleteButton.click();
  await acceptConfirm(page, "disposable garden delete");
  await fillPrompt(page, "optimization-journey-delete-target", "text", "disposable garden delete reason");
  await fillPrompt(page, E2E_PASSWORD, "password", "disposable garden delete password");
  const response = await deletionResponse;
  assert(response.status() === 200, "Disposable target garden deletion did not return 200");
  await waitFor(async () => (
    await page.locator(`#garden-select option[value='${targetGardenId}']`).count()
  ) === 0, "deleted target garden removal from selector");
  manifest.checks.disposable_garden_deletion = true;
}

async function runMobileSmoke(browser, manifest, gardenAId, gardenBId) {
  const { devices } = require("../frontend/node_modules/playwright-core");
  const { defaultBrowserType: _defaultBrowserType, ...pixel7 } = devices["Pixel 7"];
  const context = await browser.newContext({ ...pixel7, viewport: { height: 844, width: 390 } });
  const blockedRequests = await installLoopbackRequestGuard(context);
  const page = await context.newPage();
  const diagnostics = createBrowserDiagnostics(page, blockedRequests);
  try {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await authenticate(page);
    await visible(page.locator("#map-grid"), "mobile Map grid");
    const layoutsButton = page.locator("#mobile-map-layouts-btn");
    await visible(layoutsButton, "mobile map layouts action");
    await layoutsButton.click();
    await page.waitForFunction(() => (
      document.getElementById("mobile-map-layouts-sheet")?.getAttribute("aria-hidden") === "false"
    ));
    assert(
      await page.locator("#mobile-map-layouts-sheet").evaluate((sheet) => !sheet.hasAttribute("inert") && sheet.contains(document.activeElement)),
      "Open mobile layouts sheet did not receive keyboard focus",
    );
    await visible(
      page.locator("#mobile-snapshots-list").getByText(RESTORE_SNAPSHOT_NAME, { exact: true }),
      "mobile seeded layout snapshot",
    );
    await page.locator("#mobile-map-layouts-close-btn").click();
    await page.waitForFunction(() => (
      document.getElementById("mobile-map-layouts-sheet")?.getAttribute("aria-hidden") === "true"
    ));
    assert(await page.locator("#mobile-map-layouts-sheet").getAttribute("inert") !== null, "Closed layouts sheet is not inert");
    assert(await layoutsButton.evaluate((button) => document.activeElement === button), "Layouts sheet did not restore trigger focus");
    const toolsButton = page.locator("#mobile-map-tools-btn");
    await visible(toolsButton, "mobile map tools action");
    await toolsButton.click();
    await page.waitForFunction(() => (
      document.getElementById("mobile-map-tools-sheet")?.getAttribute("aria-hidden") === "false"
    ));
    assert(
      await page.locator("#mobile-map-tools-sheet").evaluate((sheet) => !sheet.hasAttribute("inert") && sheet.contains(document.activeElement)),
      "Open mobile tools sheet did not receive keyboard focus",
    );
    await page.locator("#mobile-map-tools-close-btn").click();
    await page.waitForFunction(() => (
      document.getElementById("mobile-map-tools-sheet")?.getAttribute("aria-hidden") === "true"
    ));
    assert(await page.locator("#mobile-map-tools-sheet").getAttribute("inert") !== null, "Closed tools sheet is not inert");
    assert(await toolsButton.evaluate((button) => document.activeElement === button), "Tools sheet did not restore trigger focus");
    for (let index = 0; index < 8; index += 1) await page.keyboard.press("Tab");
    assert(
      await page.evaluate(() => !document.activeElement?.closest("#mobile-map-layouts-sheet, #mobile-map-tools-sheet")),
      "Keyboard focus entered a closed mobile Map sheet",
    );

    const mobileNotificationButton = page.locator("#mobile-notification-btn");
    const mobileUtilityButton = page.locator("#mobile-utility-btn");
    await visible(mobileUtilityButton, "mobile utility trigger");
    await page.waitForFunction(() => (
      document.getElementById("mobile-notification-btn")
        ?.getAttribute("data-notification-trigger-bound") === "true"
    ));
    assert(await page.locator("#mobile-utility-sheet").getAttribute("inert") !== null, "Closed mobile utility sheet is not inert");
    await mobileUtilityButton.click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
    assert(await page.locator("#mobile-utility-sheet").getAttribute("inert") === null, "Open mobile utility sheet remained inert");
    assert(
      await page.locator("#mobile-utility-close-btn").evaluate((button) => document.activeElement === button),
      "Mobile utility sheet did not receive initial focus",
    );
    await visible(mobileNotificationButton, "mobile notification trigger");
    await mobileNotificationButton.click();
    await page.waitForTimeout(150);
    const mobileNotificationState = await page.evaluate(() => ({
      activeGarden: document.querySelector("[data-garden-select]")?.value || null,
      bound: document.getElementById("mobile-notification-btn")
        ?.getAttribute("data-notification-trigger-bound"),
      panelHidden: document.getElementById("notification-panel")?.hidden,
      ready: document.documentElement.getAttribute("data-notification-feature-ready"),
      utilityOpen: document.body.classList.contains("mobile-utility-open"),
    }));
    assert(
      mobileNotificationState.panelHidden === false,
      `Mobile notification trigger did not open its panel: ${JSON.stringify(mobileNotificationState)}`,
    );
    await visible(page.locator("#notification-panel").getByText(GARDEN_A_NOTIFICATION, { exact: true }), "mobile Garden A notification");
    assert(
      await page.locator("#notification-panel").evaluate((panel) => document.activeElement === panel),
      "Mobile notification panel did not receive focus after the utility sheet closed",
    );
    await mobileUtilityButton.click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
    const select = page.locator("#mobile-garden-select");
    await visible(select, "mobile garden selector");
    await select.selectOption(String(gardenBId));
    await waitForMapObject(page, GARDEN_B_OBJECT_ID, GARDEN_B_OBJECT);
    assert(await page.locator("#notification-panel").isHidden(), "Mobile garden switch did not close notifications");
    await mobileUtilityButton.click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
    await mobileNotificationButton.click();
    await visible(page.locator("#notification-panel").getByText(GARDEN_B_NOTIFICATION, { exact: true }), "mobile Garden B notification");
    assert(
      await page.locator("#notification-panel").getByText(GARDEN_A_NOTIFICATION, { exact: true }).count() === 0,
      "Mobile notifications leaked Garden A state into Garden B",
    );
    await mobileUtilityButton.click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
    await mobileNotificationButton.click();
    await mobileUtilityButton.click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
    await select.selectOption(String(gardenAId));
    await waitForMapObject(page, GARDEN_A_OBJECT_ID, GARDEN_A_OBJECT);
    await page.locator("#mobile-tab-activity").click();
    await openSubMode(page, "tasks", "#tasks-tab-content");
    const replayedTask = page.locator("#tasks-tab-content .task-card").filter({ hasText: GARDEN_A_TASK_TITLE }).first();
    assert(await replayedTask.count() === 0, "Skipped task remained in the actionable mobile task view");
    manifest.checks.mobile_replayed_task_removed_from_attention = true;
    diagnostics.assertClean("Mobile optimization journey");
    manifest.checks.mobile_smoke = true;
    manifest.checks.mobile_focus_and_scoped_state = true;
  } finally {
    await context.close();
  }
}

async function runDesktopJourneys(browser, manifest) {
  const context = await browser.newContext({ viewport: { height: 960, width: 1440 } });
  const blockedRequests = await installLoopbackRequestGuard(context);
  const page = await context.newPage();
  const recorder = createApiRecorder(page);
  const diagnostics = createBrowserDiagnostics(page, blockedRequests);
  try {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await authenticate(page);
    const initial = runSnapshot();
    const ids = {
      a: initial.gardens.a.id,
      b: initial.gardens.b.id,
      deleteTarget: initial.gardens.delete_target.id,
    };
    assert(Number.isInteger(ids.a) && Number.isInteger(ids.b) && Number.isInteger(ids.deleteTarget), "Seed snapshot did not provide garden identifiers");
    assert(initial.gardens.a.exists && initial.gardens.b.exists, "Two active fixture gardens were not seeded");
    assert(initial.gardens.delete_target.exists, "Disposable e2e-prefixed delete target was not seeded");
    assert(initial.gardens.a.counts.layout_snapshots >= 1, "Seeded Garden A has no layout snapshot");
    assert(initial.gardens.delete_target.counts.garden_journal_entries >= 1, "Delete target lacks related journal state");

    await runMapFirstAndRestore(page, recorder, manifest, ids.a);
    await runGardenSwitching(page, recorder, manifest, ids);
    await enterPlantWorkflow(page);
    await runOfflineJournalReplay(page, context, recorder, manifest, ids.a);
    await runOfflineTaskReplay(page, context, recorder, manifest, ids.a);
    await runProviderDisabledAnalysis(page, manifest);
    await runDisposableGardenDeletion(page, recorder, manifest, ids.deleteTarget);

    const finalSnapshot = runSnapshot({ targetGardenId: ids.deleteTarget });
    assert(!finalSnapshot.gardens.delete_target.exists, "Disposable delete target still exists after confirmed deletion");
    const retainedDeleteState = Object.entries(finalSnapshot.gardens.delete_target.counts)
      .filter(([, count]) => count !== 0);
    assert(
      retainedDeleteState.length === 0,
      `Deleted disposable target retained related state: ${JSON.stringify(retainedDeleteState)}`,
    );
    assert(finalSnapshot.audit.delete_target_count === 1, "Confirmed target deletion did not create exactly one durable audit row");
    assert(
      finalSnapshot.audit.delete_target_session_count === 1,
      "Durable delete audit was not attributed to session-backed fixture admin",
    );
    assert(finalSnapshot.journal.offline_replay_entry_count === 1, "Offline replay created an unexpected journal resource count");
    assert(finalSnapshot.offline.operation_counts.journal === 1, "Offline journal replay did not retain exactly one operation");
    assert(finalSnapshot.offline.operation_counts.media_upload === 1, "Offline media replay did not retain exactly one operation");
    assert(finalSnapshot.offline.operation_counts.task_action === 1, "Offline task replay did not retain exactly one operation");
    assert(finalSnapshot.offline.media_asset_count === 1, "Offline media replay created an unexpected asset count");
    assert(finalSnapshot.offline.task_status === "skipped", "Offline task replay did not persist the skipped state");
    assert(finalSnapshot.provider_usage_rows === 0, "Provider-disabled analysis wrote provider usage rows");
    diagnostics.assertClean("Desktop optimization journey");
    manifest.database = finalSnapshot;
    manifest.checks.database_postconditions = true;
    return ids;
  } finally {
    await context.close();
  }
}

async function main() {
  assertNoResponseMocks();
  assertPrivateArtifactDirectory();
  assertRunnerEnvironment();
  assert(fs.existsSync(CHROMIUM_EXECUTABLE), "Expected /usr/bin/chromium for optimization journey E2E");
  const { chromium } = require("../frontend/node_modules/playwright-core");
  const manifest = {
    browser: "chromium",
    checks: {},
    database: null,
    run_id: crypto.randomUUID(),
    status: "running",
    suite: "optimization-journeys-e2e",
    timings_ms: {},
  };
  let browser;
  try {
    browser = await chromium.launch({ executablePath: CHROMIUM_EXECUTABLE, headless: true });
    const ids = await runDesktopJourneys(browser, manifest);
    await runMobileSmoke(browser, manifest, ids.a, ids.b);
    manifest.status = "passed";
  } catch (error) {
    manifest.status = "failed";
    manifest.failure = safeFailureMessage(error);
    throw error;
  } finally {
    writeManifest(manifest);
    if (browser) await browser.close();
  }
  process.stdout.write(`${JSON.stringify(manifest)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`Optimization journey E2E failed: ${error.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = { assertNoResponseMocks };
