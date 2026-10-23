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
const MANAGED_PROVIDER_SECRET = "phase-seven-managed-provider-fixture-4242"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const PROVIDER_SETTINGS_PATH = "/api/admin/provider-settings";

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

async function openAdminSettings(page, profile, label) {
  if (profile === "mobile") {
    await openMobileUtility(page, label);
    await page.locator("#mobile-admin-btn:visible").click();
    await waitFor(() => page.locator("body.mobile-utility-open").count().then((count) => count === 0),
      `${label} mobile utility close after settings navigation`);
  } else {
    await page.locator("#top-tab-admin:visible").click();
  }
  const settings = page.locator(".adm-nav-btn[data-section='settings']:visible");
  await visible(settings, `${label} settings navigation`);
  await settings.click();
  await waitFor(() => page.locator("#admin-view").getAttribute("aria-busy")
    .then((value) => value !== "true"), `${label} settings load`);
}

function responseFor(page, method, pathname) {
  return page.waitForResponse((response) => (
    response.request().method() === method
      && new URL(response.url()).pathname === pathname
  ));
}

async function answerPrompt(page, value, label) {
  const dialog = page.locator(".modal:visible").last();
  await visible(dialog, `${label} prompt`);
  const dialogHandle = await dialog.elementHandle();
  assert(dialogHandle, `${label} prompt element was unavailable`);
  const input = dialog.locator(".prompt-dialog-input");
  await visible(input, `${label} prompt input`);
  await input.fill(value);
  try {
    await dialog.locator(".confirm-yes").click();
    await page.waitForFunction((element) => !element.isConnected, dialogHandle);
  } finally {
    await dialogHandle.dispose();
  }
}

async function browserJsonRequest(page, request) {
  return page.evaluate(async (input) => {
    const cookieCsrf = document.cookie.split("; ")
      .find((entry) => entry.startsWith("gardenops_csrf="))?.split("=").slice(1).join("=") || "";
    const response = await fetch(input.path, {
      body: input.body === undefined ? undefined : JSON.stringify(input.body),
      credentials: "include",
      headers: {
        ...(input.body === undefined ? {} : { "content-type": "application/json" }),
        "x-csrf-token": decodeURIComponent(cookieCsrf),
      },
      method: input.method,
    });
    const text = await response.text();
    let body = text;
    try { body = text ? JSON.parse(text) : null; } catch { /* text response */ }
    return { body, status: response.status };
  }, request);
}

function diagnosticMarks(diagnostics) {
  return {
    classified: diagnostics.classifiedConsoleDiagnostics.length,
    console: diagnostics.consoleErrors.length,
    http: diagnostics.httpErrors.length,
  };
}

async function expectProviderSettingsForbidden(page, diagnostics, request, label) {
  const marks = diagnosticMarks(diagnostics);
  const result = await browserJsonRequest(page, request);
  assert(result.status === 403, `${label} ${request.method} provider settings was not denied`);
  assert(!JSON.stringify(result.body).includes(MANAGED_PROVIDER_SECRET),
    `${label} provider settings denial exposed a managed secret`);
  await waitFor(() => diagnostics.httpErrors.length === marks.http + 1
    && diagnostics.consoleErrors.length === marks.console + 1
    && diagnostics.classifiedConsoleDiagnostics.length === marks.classified + 1,
  `${label} provider settings denial diagnostics`);
  const httpError = diagnostics.httpErrors.splice(marks.http, 1)[0];
  const consoleError = diagnostics.consoleErrors.splice(marks.console, 1)[0];
  const classified = diagnostics.classifiedConsoleDiagnostics.splice(marks.classified, 1)[0];
  assert(httpError === `403 ${PROVIDER_SETTINGS_PATH}`,
    `${label} provider settings denial emitted an unrelated HTTP error`);
  assert(classified?.method === request.method
    && classified.path === PROVIDER_SETTINGS_PATH
    && classified.status === 403,
  `${label} provider settings denial emitted an unrelated console diagnostic`);
  assert(consoleError === [classified.id, classified.context, classified.method,
    classified.status, classified.path].join(" "),
  `${label} provider settings denial console accounting drifted`);
  return true;
}

async function saveProviderSettings(page, password, label, reason) {
  const reauthentication = responseFor(page, "POST", "/api/auth/reauthenticate");
  const update = responseFor(page, "PUT", PROVIDER_SETTINGS_PATH);
  await page.locator("#adm-provider-save:visible").click();
  await answerPrompt(page, reason, `${label} action reason`);
  await answerPrompt(page, password, `${label} password confirmation`);
  assert((await reauthentication).ok(), `${label} provider settings reauthentication failed`);
  const response = await update;
  assert(response.status() === 200, `${label} provider settings update failed`);
  return response.json();
}

function providerSecretStatus(summary, label) {
  const status = summary?.secrets?.openai_api_key;
  assert(status && typeof status === "object", `${label} OpenAI secret metadata is missing`);
  return status;
}

async function exerciseProviderSettingsAdmin(page, profile, password, label) {
  await openAdminSettings(page, profile, label);
  const secretInput = page.locator("#adm-provider-secret-openai_api_key:visible");
  await visible(secretInput, `${label} provider secret input`);
  assert(await secretInput.isEnabled(), `${label} provider secret input is disabled`);
  if (profile === "mobile") {
    const refresh = responseFor(page, "GET", PROVIDER_SETTINGS_PATH);
    await page.locator("#adm-provider-refresh:visible").click();
    assert((await refresh).ok(), `${label} provider settings refresh failed`);
    const current = await browserJsonRequest(page, { method: "GET", path: PROVIDER_SETTINGS_PATH });
    assert(current.status === 200, `${label} provider settings state was unavailable`);
    const status = providerSecretStatus(current.body, label);
    assert(status.source === "env" && status.configured === true,
      `${label} provider settings did not retain the environment fallback after cleanup`);
    return { mobile_surface_reachable: true, refresh_preserved_redacted_summary: true };
  }

  const baseline = await browserJsonRequest(page, { method: "GET", path: PROVIDER_SETTINGS_PATH });
  assert(baseline.status === 200, `${label} provider settings baseline was unavailable`);
  assert(!JSON.stringify(baseline.body).includes(MANAGED_PROVIDER_SECRET),
    `${label} provider settings baseline exposed a managed secret`);
  await secretInput.fill(MANAGED_PROVIDER_SECRET);
  const saved = await saveProviderSettings(
    page,
    password,
    label,
    "phase-seven-provider-secret-set",
  );
  const managed = providerSecretStatus(saved, label);
  assert(managed.configured === true && managed.source === "db" && managed.last4 === "4242",
    `${label} provider settings did not return redacted managed-secret metadata`);
  assert(!JSON.stringify(saved).includes(MANAGED_PROVIDER_SECRET),
    `${label} provider settings response exposed the managed secret`);
  await waitFor(() => page.locator("#adm-provider-secret-openai_api_key:visible").inputValue()
    .then((value) => value === ""), `${label} provider secret input clear after save`);

  const persisted = await browserJsonRequest(page, { method: "GET", path: PROVIDER_SETTINGS_PATH });
  assert(persisted.status === 200 && providerSecretStatus(persisted.body, label).source === "db",
    `${label} provider settings did not persist managed-secret metadata`);
  assert(!JSON.stringify(persisted.body).includes(MANAGED_PROVIDER_SECRET),
    `${label} persisted provider settings exposed the managed secret`);

  const clear = page.locator("#adm-provider-clear-openai_api_key:visible");
  assert(await clear.isEnabled(), `${label} provider secret clear control is disabled`);
  await clear.check();
  const cleared = await saveProviderSettings(
    page,
    password,
    label,
    "phase-seven-provider-secret-clear",
  );
  const fallback = providerSecretStatus(cleared, label);
  assert(fallback.configured === true && fallback.source === "env",
    `${label} provider secret clear did not restore the environment fallback`);
  assert(!JSON.stringify(cleared).includes(MANAGED_PROVIDER_SECRET),
    `${label} cleared provider settings response exposed the managed secret`);

  const finalState = await browserJsonRequest(page, { method: "GET", path: PROVIDER_SETTINGS_PATH });
  assert(finalState.status === 200 && providerSecretStatus(finalState.body, label).source === "env",
    `${label} provider secret cleanup did not persist`);
  assert(!JSON.stringify(finalState.body).includes(MANAGED_PROVIDER_SECRET),
    `${label} provider secret cleanup exposed the managed secret`);
  return {
    cleanup_persisted: true,
    managed_secret_metadata_redacted: true,
    reauthentication_enforced: true,
  };
}

async function assertProviderSettingsRoleBoundary(page, diagnostics, profile, label) {
  await openAdminSettings(page, profile, label);
  assert(await page.locator("#adm-provider-save").count() === 0,
    `${label} displayed provider settings controls to a non-admin`);
  const readDenied = await expectProviderSettingsForbidden(page, diagnostics, {
    method: "GET",
    path: PROVIDER_SETTINGS_PATH,
  }, label);
  const writeDenied = await expectProviderSettingsForbidden(page, diagnostics, {
    body: { action_reason: "phase-seven-provider-settings-denied", ai_provider: "disabled" },
    method: "PUT",
    path: PROVIDER_SETTINGS_PATH,
  }, label);
  return { api_read_denied: readDenied, api_write_denied: writeDenied, ui_hidden: true };
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

        result.checks.provider_settings = await exerciseProviderSettingsAdmin(
          page,
          options.profile,
          password,
          "Phase 7 desktop provider settings",
        );
      } else {

        result.checks.provider_settings = await exerciseProviderSettingsAdmin(
          page,
          options.profile,
          password,
          "Phase 7 mobile provider settings",
        );
      }
    } else if (options.role === "viewer") {
      result.checks.chat_success = await exerciseChat(page, "Phase 7 viewer");

      result.checks.provider_settings = await assertProviderSettingsRoleBoundary(
        page,
        guarded.diagnostics,
        options.profile,
        "Phase 7 viewer provider settings",
      );
    } else {
      result.checks.chat_success = await exerciseChat(page, "Phase 7 editor");

      result.checks.provider_settings = await assertProviderSettingsRoleBoundary(
        page,
        guarded.diagnostics,
        options.profile,
        "Phase 7 editor provider settings",
      );
    }
    const provider = await fixtureState(page);
    assert(provider.counts?.provider_requests > 0 || options.role === "viewer",
      `Phase 7 ${options.role}:${options.profile} did not exercise the loopback provider`);

    result.checks.provider_fixture_redacted = !JSON.stringify(provider).includes("Phase 7");

    result.structure = await assertPageStructure(page, `Phase 7 ${options.role}:${options.profile}`, {
      enforceControlNames: false,
    });
    assertDiagnosticsClean(guarded.diagnostics, `Phase 7 ${options.role}:${options.profile}`);
    result.checks.browser_diagnostics = true;
    result.assertions.passed.push("phase-seven-provider-weather", "browser-diagnostics-clean");
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

async function runProvidersAndWeather(options, profileRunner = runProfile) {
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

module.exports = { runProvidersAndWeather };
