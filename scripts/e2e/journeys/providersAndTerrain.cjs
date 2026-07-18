"use strict";

const crypto = require("node:crypto");

const {
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  dismissProactivePasskeyPrompt,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

const EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const EXPECTED_CHAT_REPLY = "Deterministic test reply: Check soil moisture before watering.";

async function openMobileUtility(page, label) {
  const utility = page.locator("#mobile-utility-sheet");
  if (!await utility.evaluate((element) => element.classList.contains("mobile-utility-sheet--open"))) {
    await page.locator("#mobile-utility-btn:visible").click();
    await waitFor(() => utility.evaluate((element) => (
      element.classList.contains("mobile-utility-sheet--open") && !element.hasAttribute("inert")
    )), `${label} mobile utility open`);
  }
}

async function selectGarden(page, gardenId, label, profile) {
  if (profile === "mobile") await openMobileUtility(page, label);
  const selector = page.locator(profile === "mobile" ? "#mobile-garden-select" : "#garden-select");
  await visible(selector, `${label} garden selector`);
  await selector.selectOption(String(gardenId));
  await waitFor(() => selector.inputValue().then((value) => value === String(gardenId)),
    `${label} garden switch`);
  await waitFor(() => page.locator("body.garden-switch-pending").count().then((count) => count === 0),
    `${label} garden switch settle`);
  if (profile === "mobile") {
    await page.locator("#mobile-utility-close-btn:visible").click();
    await waitFor(() => page.locator("body.mobile-utility-open").count().then((count) => count === 0),
      `${label} mobile utility close`);
  }
}

async function openAnalysis(page, label) {
  await page.locator("[data-tab='insights']:visible").first().click();
  const analysis = page.locator("[data-sub-mode='analysis']:visible").first();
  await visible(analysis, `${label} analysis tab`);
  await analysis.click();
  await visible(page.locator("#analysis-view"), `${label} analysis view`);
}

async function exerciseStaleWeather(page, label) {
  await page.locator("[data-tab='insights']:visible").first().click();
  const care = page.locator("[data-sub-mode='care']:visible").first();
  await visible(care, `${label} care tab`);
  await care.click();
  const staleAge = page.locator("#weather-dashboard .weather-forecast-age[data-forecast-stale='true']");
  await visible(staleAge, `${label} stale weather marker`);
  const refresh = page.locator("#weather-dashboard .weather-check-btn");
  await visible(refresh, `${label} stale weather refresh`);
  const weatherResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/weather/check"
  ));
  await refresh.click();
  const response = await weatherResponse;
  const result = await response.json();
  assert(response.status() === 200, `${label} stale weather refresh failed`);
  assert(result.forecast_available === true && result.alerts_created === 0 && result.alerts_skipped === 0,
    `${label} stale weather refresh treated degraded data as authoritative`);
  await visible(staleAge, `${label} stale weather marker after refresh`);
  return { stale_forecast_visible: true, stale_refresh_non_authoritative: true };
}

async function fixtureControl(page, scenario) {
  const base = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_URL;
  assert(base, "Phase 7 provider fixture URL is missing");
  const result = await page.evaluate(async ({ rawBase, nextScenario }) => {
    const url = new URL(rawBase);
    url.pathname = "/__fixture__/scenario";
    const response = await fetch(url, {
      body: JSON.stringify({ scenario: nextScenario }),
      headers: { "content-type": "application/json" },
      method: "POST",
    });
    return { body: await response.json(), status: response.status };
  }, { rawBase: base, nextScenario: scenario });
  assert(result.status === 200 && result.body?.scenario === scenario,
    `Phase 7 provider fixture did not enter ${scenario}`);
}

async function fixtureState(page) {
  const base = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_URL;
  assert(base, "Phase 7 provider fixture URL is missing");
  const result = await page.evaluate(async (rawBase) => {
    const url = new URL(rawBase);
    url.pathname = "/__fixture__/state";
    const response = await fetch(url);
    return { body: await response.json(), status: response.status };
  }, base);
  assert(result.status === 200, "Phase 7 provider fixture state was unavailable");
  return result.body;
}

async function consumeExpectedQuotaDiagnostics(diagnostics, marks, label) {
  await waitFor(() => diagnostics.httpErrors.length === marks.httpErrors + 1
    && diagnostics.consoleErrors.length === marks.consoleErrors + 1
    && diagnostics.classifiedConsoleDiagnostics.length === marks.classifiedConsoleDiagnostics + 1,
  `${label} quota diagnostic accounting`);
  const httpError = diagnostics.httpErrors.splice(marks.httpErrors, 1)[0];
  const consoleError = diagnostics.consoleErrors.splice(marks.consoleErrors, 1)[0];
  const classified = diagnostics.classifiedConsoleDiagnostics.splice(
    marks.classifiedConsoleDiagnostics,
    1,
  )[0];
  assert(httpError === "429 /api/ai/garden-chat", `${label} quota emitted an unrelated HTTP error`);
  assert(classified?.context === "unexpected-http-response"
    && classified.method === "POST"
    && classified.path === "/api/ai/garden-chat"
    && classified.status === 429, `${label} quota emitted an unrelated console diagnostic`);
  assert(consoleError === [classified.id, classified.context, classified.method,
    classified.status, classified.path].join(" "), `${label} quota console accounting drifted`);
}

async function exerciseChat(page, label, { scenario = "success", diagnostics = null } = {}) {
  await fixtureControl(page, scenario);
  await openAnalysis(page, label);
  const input = page.locator("#analysis-input");
  const send = page.locator("#analysis-send-btn");
  await input.fill(`Phase 7 ${scenario} provider check`);
  const diagnosticMarks = diagnostics ? {
    classifiedConsoleDiagnostics: diagnostics.classifiedConsoleDiagnostics.length,
    consoleErrors: diagnostics.consoleErrors.length,
    httpErrors: diagnostics.httpErrors.length,
  } : null;
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/ai/garden-chat"
  ));
  await send.click();
  const response = await responsePromise;
  if (scenario === "success") {
    assert(response.status() === 200, `${label} chat success returned ${response.status()}`);
    const reply = page.locator("#analysis-messages .chat-bubble.chat-ai:not(.chat-loading):not(.chat-error)").last();
    await visible(reply, `${label} chat reply`);
    assert((await reply.textContent() || "").trim() === EXPECTED_CHAT_REPLY,
      `${label} visible chat reply drifted`);
    assert(await input.isEnabled() && await send.isEnabled(), `${label} chat controls stayed disabled`);
    return { recoverable: true, success: true };
  }
  assert(response.status() === 429, `${label} ${scenario} chat returned ${response.status()}`);
  const failure = page.locator("#analysis-messages .chat-bubble.chat-error").last();
  await visible(failure, `${label} recoverable chat failure`);
  assert((await failure.textContent() || "").trim().length > 0,
    `${label} failure did not explain the recoverable state`);
  assert(await input.isEnabled() && await send.isEnabled(), `${label} failure left chat controls disabled`);
  assert(diagnostics && diagnosticMarks, `${label} quota diagnostics were not captured`);
  await consumeExpectedQuotaDiagnostics(diagnostics, diagnosticMarks, label);
  return { recoverable: true, success: false };
}

async function waitForShadeReady(page, label) {
  const panel = page.locator("#shade-panel");
  await visible(panel, `${label} Shade panel`);
  await waitFor(async () => (
    await panel.getAttribute("data-state") === "ready"
      && await panel.getAttribute("data-simulator") === "external"
  ), `${label} external ShadeMap render`);
  const canvas = page.locator("canvas[data-phase-seven-simulator='true']");
  await visible(canvas, `${label} simulator canvas`);
  return { canvas, panel };
}

async function shadePixels(page, canvas) {
  const screenshot = await canvas.screenshot();
  const sample = await canvas.evaluate((element) => {
    const canvasElement = element;
    const context = canvasElement.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    // The inset overlay is mode-dependent but intentionally static. Sample the background
    // so a date or time change proves the rendered shadow state changed on every viewport.
    const x = Math.min(canvasElement.width - 1, 1);
    const y = Math.min(canvasElement.height - 1, 1);
    return {
      dimensions: [canvasElement.width, canvasElement.height],
      pixel: [...context.getImageData(x, y, 1, 1).data],
    };
  });
  assert(sample && sample.dimensions[0] > 0 && sample.dimensions[1] > 0,
    "Phase 7 Shade canvas is blank");
  assert(sample.pixel.some((value) => value !== 0), "Phase 7 Shade canvas sampled only transparent pixels");
  return { digest: crypto.createHash("sha256").update(screenshot).digest("hex"), ...sample };
}

async function consumeExpectedViewerShadeDiagnostics(diagnostics, marks, label) {
  await waitFor(() => diagnostics.httpErrors.length === marks.httpErrors + 4
    && diagnostics.consoleErrors.length === marks.consoleErrors + 4
    && diagnostics.classifiedConsoleDiagnostics.length === marks.classifiedConsoleDiagnostics + 4,
  `${label} read-only diagnostic accounting`);
  const httpErrors = diagnostics.httpErrors.splice(marks.httpErrors, 4).sort();
  const consoleErrors = diagnostics.consoleErrors.splice(marks.consoleErrors, 4);
  const classified = diagnostics.classifiedConsoleDiagnostics.splice(
    marks.classifiedConsoleDiagnostics,
    4,
  ).sort((left, right) => (
    left.path.localeCompare(right.path) || left.method.localeCompare(right.method)
  ));
  const expected = [
    ["DELETE", "/api/shademap/obstacles/999999"],
    ["PATCH", "/api/shademap/calibration"],
    ["PATCH", "/api/shademap/state"],
    ["POST", "/api/shademap/obstacles"],
  ];
  assert(JSON.stringify(httpErrors) === JSON.stringify(expected.map(([, path]) => `403 ${path}`).sort()),
    `${label} viewer denial emitted unrelated HTTP diagnostics`);
  assert(JSON.stringify(classified.map((entry) => [entry.method, entry.path, entry.status]))
    === JSON.stringify(expected.map(([method, path]) => [method, path, 403]).sort((left, right) => (
      left[1].localeCompare(right[1])
    ))), `${label} viewer denial emitted unrelated console diagnostics`);
  const expectedConsoleErrors = classified.map((entry) => [
    entry.id,
    entry.context,
    entry.method,
    entry.status,
    entry.path,
  ].join(" ")).sort();
  assert(JSON.stringify(consoleErrors.sort()) === JSON.stringify(expectedConsoleErrors),
    `${label} viewer denial console messages drifted`);
}

async function consumeExpectedViewerTerrainDiagnostics(diagnostics, marks, label) {
  await waitFor(() => diagnostics.httpErrors.length === marks.httpErrors + 2
    && diagnostics.consoleErrors.length === marks.consoleErrors + 2
    && diagnostics.classifiedConsoleDiagnostics.length === marks.classifiedConsoleDiagnostics + 2,
  `${label} LiDAR read-only diagnostic accounting`);
  const httpErrors = diagnostics.httpErrors.splice(marks.httpErrors, 2).sort();
  const consoleErrors = diagnostics.consoleErrors.splice(marks.consoleErrors, 2);
  const classified = diagnostics.classifiedConsoleDiagnostics.splice(
    marks.classifiedConsoleDiagnostics,
    2,
  ).sort((left, right) => (
    left.path.localeCompare(right.path) || left.method.localeCompare(right.method)
  ));
  const expected = [
    ["DELETE", "/api/gardens/{garden_id}/lidar"],
    ["POST", "/api/gardens/{garden_id}/lidar"],
  ];
  const actualPaths = classified.map((entry) => entry.path.replace(
    /\/api\/gardens\/\d+\/lidar/,
    "/api/gardens/{garden_id}/lidar",
  ));
  assert(JSON.stringify(httpErrors) === JSON.stringify([
    "403 /api/gardens/{garden_id}/lidar",
    "403 /api/gardens/{garden_id}/lidar",
  ]), `${label} viewer LiDAR denial emitted unrelated HTTP diagnostics`);
  assert(JSON.stringify(classified.map((entry, index) => [entry.method, actualPaths[index], entry.status]))
    === JSON.stringify(expected), `${label} viewer LiDAR denial emitted unrelated console diagnostics`);
  const expectedConsoleErrors = classified.map((entry) => [
    entry.id,
    entry.context,
    entry.method,
    entry.status,
    entry.path,
  ].join(" ")).sort();
  assert(JSON.stringify(consoleErrors.sort()) === JSON.stringify(expectedConsoleErrors),
    `${label} viewer LiDAR denial console messages drifted`);
}

async function assertViewerShadeBoundary(page, panel, label, diagnostics) {
  const mutationControls = [
    "shade-calibration-fill-btn", "shade-calibration-save-btn", "shade-calibration-reset-btn",
    "shade-obstacle-label", "shade-obstacle-kind", "shade-obstacle-plot",
    "shade-obstacle-height", "shade-obstacle-radius", "shade-obstacle-lat",
    "shade-obstacle-lng", "shade-obstacle-active", "shade-obstacle-fill-target-btn",
    "shade-obstacle-save-btn", "shade-obstacle-delete-btn",
  ];
  for (const id of mutationControls) {
    const control = panel.locator(`#${id}`);
    assert(await control.isDisabled(), `${label} viewer could mutate ShadeMap through ${id}`);
  }
  const marks = {
    classifiedConsoleDiagnostics: diagnostics.classifiedConsoleDiagnostics.length,
    consoleErrors: diagnostics.consoleErrors.length,
    httpErrors: diagnostics.httpErrors.length,
  };
  const direct = await page.evaluate(async () => {
    const request = async (path, options = {}) => {
      const response = await fetch(path, { credentials: "include", ...options });
      return { body: await response.json(), status: response.status };
    };
    const stateBefore = await request("/api/shademap/state");
    const calibrationBefore = await request("/api/shademap/calibration");
    const obstaclesBefore = await request("/api/shademap/obstacles");
    const denied = await Promise.all([
      request("/api/shademap/state", {
        body: JSON.stringify({
          analysis_timestamp_ms: 1772443603996,
          mode: "shadow",
          preset: "summer",
          selected_plot_id: null,
        }),
        headers: { "content-type": "application/json" },
        method: "PATCH",
      }),
      request("/api/shademap/calibration", {
        body: JSON.stringify({
          axis_grid_col: null, axis_grid_row: null, axis_latitude: null, axis_longitude: null,
          calibration_type: "house-corners", enabled: true,
          house_ne_latitude: 51.50110, house_ne_longitude: -0.12410,
          house_nw_latitude: 51.50110, house_nw_longitude: -0.12490,
          house_se_latitude: 51.50070, house_se_longitude: -0.12410,
          house_sw_latitude: 51.50070, house_sw_longitude: -0.12490,
          origin_grid_col: null, origin_grid_row: null, origin_latitude: null, origin_longitude: null,
        }),
        headers: { "content-type": "application/json" },
        method: "PATCH",
      }),
      request("/api/shademap/obstacles", {
        body: JSON.stringify({
          active: true, crown_radius_m: 2.4, height_m: 4.8, kind: "tree",
          label: "Viewer must not add this", latitude: 51.50090, linked_plot_id: null,
          longitude: -0.12440,
        }),
        headers: { "content-type": "application/json" },
        method: "POST",
      }),
      request("/api/shademap/obstacles/999999", { method: "DELETE" }),
    ]);
    const stateAfter = await request("/api/shademap/state");
    const calibrationAfter = await request("/api/shademap/calibration");
    const obstaclesAfter = await request("/api/shademap/obstacles");
    return { calibrationAfter, calibrationBefore, denied, obstaclesAfter, obstaclesBefore, stateAfter, stateBefore };
  });
  assert(direct.denied.every((result) => result.status === 403),
    `${label} viewer direct ShadeMap mutation was not denied`);
  assert(JSON.stringify(direct.stateBefore.body) === JSON.stringify(direct.stateAfter.body)
    && JSON.stringify(direct.calibrationBefore.body) === JSON.stringify(direct.calibrationAfter.body)
    && JSON.stringify(direct.obstaclesBefore.body) === JSON.stringify(direct.obstaclesAfter.body),
  `${label} viewer direct ShadeMap mutation changed persisted state`);
  await consumeExpectedViewerShadeDiagnostics(diagnostics, marks, label);
  return true;
}

async function exerciseShade(page, label, {
  diagnostics = null,
  expectTerrain = false,
  mobile = false,
  viewer = false,
} = {}) {
  if (mobile) {
    await page.locator("[data-tab='map']:visible").first().click();
    const trigger = page.locator("#mobile-map-shade-btn:visible");
    await visible(trigger, `${label} mobile Shade trigger`);
    await trigger.click();
    await waitFor(() => page.locator("#shade-panel").evaluate((element) => (
      element.classList.contains("mobile-map-sheet--open") && !element.hasAttribute("inert")
    )), `${label} mobile Shade sheet open`);
  } else {
    await page.locator("#top-tab-map:visible").click();
  }
  const { panel, canvas } = await waitForShadeReady(page, label);
  if (expectTerrain) {
    await waitFor(() => canvas.getAttribute("data-phase-seven-terrain")
      .then((value) => value === "rendered"), `${label} signed terrain tile rendering`);
    assert(await canvas.getAttribute("data-phase-seven-terrain-size") === "256x256",
      `${label} decoded terrain tile dimensions were unexpected`);
  }
  const before = await shadePixels(page, canvas);
  const revision = Number(await panel.getAttribute("data-render-revision"));
  const time = panel.locator("#shade-time-input");
  // Desktop state persists to the shared garden, so mobile can begin at the prior
  // profile's value. Choose the alternate fixed time to prove a real transition.
  const nextTime = await time.inputValue() === "14:30" ? "15:45" : "14:30";
  await time.fill(nextTime);
  await time.dispatchEvent("change");
  await waitFor(async () => (
    Number(await panel.getAttribute("data-render-revision")) > revision
      && await panel.getAttribute("data-state") === "ready"
  ), `${label} Shade time update`);
  const after = await shadePixels(page, canvas);
  assert(before.digest !== after.digest,
    `${label} Shade screenshot did not change after the deterministic time update`);
  assert(JSON.stringify(before.pixel) !== JSON.stringify(after.pixel),
    `${label} Shade canvas sample did not change after the deterministic time update`);
  const mode = panel.locator("#shade-mode-select");
  await visible(mode, `${label} Shade mode select`);
  // The preceding desktop profile may have persisted either valid mode. Change to
  // the opposite mode so this profile always proves a mode transition.
  const nextMode = await mode.inputValue() === "sun-hours" ? "shadow" : "sun-hours";
  await mode.selectOption(nextMode);
  await waitFor(async () => await panel.getAttribute("data-mode") === nextMode
    && await panel.getAttribute("data-state") === "ready", `${label} Shade mode update`);
  const modePixels = await shadePixels(page, canvas);
  assert(after.digest !== modePixels.digest, `${label} Shade mode did not change visible pixels`);

  if (viewer) {
    assert(await panel.getAttribute("data-write-access") === "read-only",
      `${label} viewer Shade panel was not read only`);
    assert(diagnostics, `${label} viewer diagnostics were not captured`);
    await assertViewerShadeBoundary(page, panel, label, diagnostics);
  } else {
    const calibrationDisclosure = panel.locator("details:has(#shade-calibration-fill-btn)");
    await calibrationDisclosure.locator("summary").click();
    const fill = panel.locator("#shade-calibration-fill-btn");
    const save = panel.locator("#shade-calibration-save-btn");
    await visible(fill, `${label} calibration controls`);
    await fill.click();
    const calibrationResponse = page.waitForResponse((response) => (
      response.request().method() === "PATCH"
        && new URL(response.url()).pathname === "/api/shademap/calibration"
    ));
    await save.click();
    assert((await calibrationResponse).status() === 200, `${label} calibration save failed`);
    const obstacleDisclosure = panel.locator("details:has(#shade-obstacle-label)");
    await obstacleDisclosure.locator("summary").click();
    const obstacleLabel = panel.locator("#shade-obstacle-label");
    await obstacleLabel.fill("Phase 7 temporary obstacle");
    const obstacleResponse = page.waitForResponse((response) => (
      response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/shademap/obstacles"
    ));
    await panel.locator("#shade-obstacle-save-btn").click();
    assert((await obstacleResponse).status() === 201, `${label} obstacle save failed`);
    const deleteResponse = page.waitForResponse((response) => (
      response.request().method() === "DELETE"
        && /^\/api\/shademap\/obstacles\/\d+$/.test(new URL(response.url()).pathname)
    ));
    await panel.locator("#shade-obstacle-delete-btn").click();
    assert((await deleteResponse).status() === 200, `${label} obstacle delete failed`);
  }

  if (mobile) {
    await panel.locator("#mobile-map-shade-close-btn:visible").click();
    await waitFor(() => panel.evaluate((element) => (
      !element.classList.contains("mobile-map-sheet--open") && element.hasAttribute("inert")
    )), `${label} closed mobile Shade sheet inertness`);
    assert(await page.locator("#map-camera").evaluate((element) => !element.hasAttribute("inert")),
      `${label} map remained inert after closing Shade`);
  }
  return {
    external_canvas: true,
    pixel_change: true,
    viewer_controls_disabled: viewer,
    viewer_read_only: viewer,
  };
}

async function openAdminGarden(page, profile, label) {
  if (profile === "mobile") {
    await openMobileUtility(page, label);
    await page.locator("#mobile-admin-btn:visible").click();
    await waitFor(() => page.locator("body.mobile-utility-open").count().then((count) => count === 0),
      `${label} mobile utility close after settings navigation`);
  } else {
    await page.locator("#top-tab-admin:visible").click();
  }
  const nav = page.locator(".adm-nav-btn[data-section='garden']:visible");
  await visible(nav, `${label} garden settings`);
  await nav.click();
  await waitFor(() => page.locator("#admin-view").getAttribute("aria-busy").then((value) => value !== "true"),
    `${label} garden settings load`);
}

async function exerciseTerrainUpload(page, options) {
  const label = `Phase 7 ${options.role}:${options.profile} terrain`;
  await openAdminGarden(page, options.profile, label);
  const input = page.locator("#adm-garden-lidar-input");
  await waitFor(() => input.count().then((count) => count === 1), `${label} file input`);
  const response = page.waitForResponse((candidate) => (
    candidate.request().method() === "POST"
      && /^\/api\/gardens\/\d+\/lidar$/.test(new URL(candidate.url()).pathname)
  ));
  await input.setInputFiles(options.terrainFile);
  assert((await response).status() === 201, `${label} upload failed`);
  await waitFor(() => page.locator("#adm-garden-lidar-remove").isEnabled(),
    `${label} upload state`);
  const status = await page.evaluate(async (gardenId) => {
    const response = await fetch(`/api/gardens/${gardenId}/lidar`, { credentials: "include" });
    return { body: await response.json(), status: response.status };
  }, options.fixture.gardens.alpha.id);
  assert(status.status === 200 && status.body.uploaded === true,
    `${label} upload did not persist the expected status`);
  return {
    remove: async () => {
      const remove = page.waitForResponse((candidate) => (
        candidate.request().method() === "DELETE"
          && /^\/api\/gardens\/\d+\/lidar$/.test(new URL(candidate.url()).pathname)
      ));
      page.once("dialog", (dialog) => dialog.accept());
      await page.locator("#adm-garden-lidar-remove").click();
      assert((await remove).status() === 200, `${label} cleanup failed`);
      const deletedStatus = await page.evaluate(async (gardenId) => {
        const response = await fetch(`/api/gardens/${gardenId}/lidar`, { credentials: "include" });
        return { body: await response.json(), status: response.status };
      }, options.fixture.gardens.alpha.id);
      assert(deletedStatus.status === 200 && deletedStatus.body.uploaded === false,
        `${label} cleanup did not persist the expected status`);
      return {
        cleanup_persisted: true,
        upload_and_cleanup: true,
        upload_persisted: true,
      };
    },
  };
}

async function assertViewerTerrainBoundary(page, gardenId, label, diagnostics) {
  await page.locator("#top-tab-admin:visible").click();
  await visible(page.locator("#admin-view"), `${label} settings surface`);
  assert(await page.locator("#adm-garden-lidar-input").count() === 0,
    `${label} viewer was offered a LiDAR upload control`);
  const marks = {
    classifiedConsoleDiagnostics: diagnostics.classifiedConsoleDiagnostics.length,
    consoleErrors: diagnostics.consoleErrors.length,
    httpErrors: diagnostics.httpErrors.length,
  };
  const direct = await page.evaluate(async (id) => {
    const request = async (path, options = {}) => {
      const response = await fetch(path, { credentials: "include", ...options });
      return { body: await response.json(), status: response.status };
    };
    const before = await request(`/api/gardens/${id}/lidar`);
    const denied = await Promise.all([
      request(`/api/gardens/${id}/lidar`, {
        body: new Uint8Array([0, 1, 2, 3]),
        headers: {
          "content-type": "application/octet-stream",
          "x-upload-filename": "viewer-denied.las",
        },
        method: "POST",
      }),
      request(`/api/gardens/${id}/lidar`, { method: "DELETE" }),
    ]);
    const after = await request(`/api/gardens/${id}/lidar`);
    return { after, before, denied };
  }, gardenId);
  assert(direct.denied.every((result) => result.status === 403),
    `${label} viewer direct LiDAR mutation was not denied`);
  assert(JSON.stringify(direct.before.body) === JSON.stringify(direct.after.body),
    `${label} viewer direct LiDAR mutation changed persisted state`);
  await consumeExpectedViewerTerrainDiagnostics(diagnostics, marks, label);
  return true;
}

function credentials(options, role) {
  if (role === "admin") return [options.username, options.password];
  if (role === "editor") return [options.fixture.roles.editor, EDITOR_PASSWORD];
  return [options.fixture.roles.viewer, VIEWER_PASSWORD];
}

async function runProfile(options) {
  const [username, password] = credentials(options, options.role);
  const guarded = await createGuardedContext(
    options.browser,
    options.devices,
    options.profile,
    options.artifactDir,
    `phase-seven-${options.role}-${options.profile}`,
    { baseUrl: options.baseUrl },
  );
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page, { authType: "session", role: options.role, username });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile: options.profile,
    requests: [],
    role: options.role,
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const auth = await authenticate(page, username, password);
    recorder.setGardenId(auth.garden_id);
    guarded.markAuthenticated();
    await dismissProactivePasskeyPrompt(page);
    await selectGarden(
      page,
      options.fixture.gardens.alpha.id,
      `Phase 7 ${options.role}:${options.profile}`,
      options.profile,
    );
    if (options.role === "admin") {
      result.checks.chat_success = await exerciseChat(page, `Phase 7 ${options.profile}`);
      if (options.profile === "desktop") {
        result.checks.chat_quota = await exerciseChat(page, "Phase 7 desktop", {
          diagnostics: guarded.diagnostics,
          scenario: "quota",
        });
        result.checks.weather = await exerciseStaleWeather(page, "Phase 7 desktop");
        await fixtureControl(page, "success");
        const terrain = await exerciseTerrainUpload(page, options);
        result.checks.shade = await exerciseShade(page, "Phase 7 desktop", { expectTerrain: true });
        await openAdminGarden(page, options.profile, "Phase 7 desktop terrain cleanup");
        result.checks.terrain = await terrain.remove();
      } else {
        const terrain = await exerciseTerrainUpload(page, options);
        result.checks.shade = await exerciseShade(page, "Phase 7 mobile", {
          expectTerrain: true,
          mobile: true,
        });
        await openAdminGarden(page, options.profile, "Phase 7 mobile terrain cleanup");
        result.checks.terrain = await terrain.remove();
      }
    } else if (options.role === "viewer") {
      result.checks.shade = await exerciseShade(page, "Phase 7 viewer", {
        diagnostics: guarded.diagnostics,
        viewer: true,
      });
      result.checks.terrain = {
        viewer_write_denied: await assertViewerTerrainBoundary(
          page,
          options.fixture.gardens.alpha.id,
          "Phase 7 viewer terrain",
          guarded.diagnostics,
        ),
      };
    } else {
      const terrain = await exerciseTerrainUpload(page, options);
      result.checks.shade = await exerciseShade(page, "Phase 7 editor", { expectTerrain: true });
      await openAdminGarden(page, options.profile, "Phase 7 editor terrain cleanup");
      result.checks.terrain = await terrain.remove();
    }
    const provider = await fixtureState(page);
    assert(provider.counts?.provider_requests > 0 || options.role === "viewer",
      `Phase 7 ${options.role}:${options.profile} did not exercise the loopback provider`);
    assert(provider.counts?.by_path?.["/shademap/runtime.js"] > 0,
      `Phase 7 ${options.role}:${options.profile} did not load the runtime through GardenOps`);
    result.checks.provider_fixture_redacted = !JSON.stringify(provider).includes("Phase 7");
    result.checks.shademap_runtime_loaded = true;
    result.structure = await assertPageStructure(page, `Phase 7 ${options.role}:${options.profile}`, {
      enforceControlNames: false,
    });
    assertDiagnosticsClean(guarded.diagnostics, `Phase 7 ${options.role}:${options.profile}`);
    result.checks.browser_diagnostics = true;
    result.assertions.passed.push("phase-seven-provider-terrain", "browser-diagnostics-clean");
    await recorder.settle();
    result.requests = recorder.records;
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    result.diagnostics = guarded.diagnostics;
    try { result.trace = await guarded.close(status); } catch (error) { if (!caughtError) caughtError = error; }
  }
  return { error: caughtError, result };
}

async function runProvidersAndTerrain(options, profileRunner = runProfile) {
  const profiles = [
    ["admin", "desktop"],
    ["admin", "mobile"],
    ["editor", "desktop"],
    ["viewer", "desktop"],
  ];
  const results = [];
  for (const [role, profile] of profiles) {
    const outcome = await profileRunner({ ...options, profile, role });
    results.push(outcome.result);
    if (options.onProfile) options.onProfile(outcome.result);
    if (outcome.error) throw outcome.error;
  }
  return results;
}

module.exports = { runProvidersAndTerrain };
