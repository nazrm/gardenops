"use strict";

const fs = require("node:fs");
const {
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

const ONBOARDING_PASSWORD = "CompleteJourneysOnboardingE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const MOBILE_ONBOARDING_PASSWORD = "CompleteJourneysMobileOnboardingE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const MAP_OBJECT_TYPES = [
  "patio", "terrace", "greenhouse", "shed", "pond", "path", "bed", "other",
];

function fixtureGarden(fixture, key) {
  const garden = fixture.gardens?.[key];
  assert(garden && Number.isInteger(garden.id), `Missing ${key} fixture garden`);
  return garden;
}

function viewerFixtureGarden(fixture, key, garden) {
  const viewer = fixture.phase_one?.viewer?.[key];
  assert(
    viewer
      && viewer.garden_id === garden.id
      && typeof viewer.plant_id === "string"
      && typeof viewer.plant_name === "string"
      && typeof viewer.plot_id === "string",
    `Missing viewer-owned ${key} fixture content`,
  );
  return { ...garden, ...viewer };
}

function plantRecord(page, profile, name) {
  const selector = profile === "mobile"
    ? "#plants-mobile-list article[data-plt-id]"
    : "#plants-table-body tr[data-plt-id]";
  return page.locator(selector).filter({ hasText: name });
}

async function issueBrowserRequest(page, { body = null, headers = {}, method, path }) {
  return page.evaluate(async ({ requestBody, requestHeaders, requestMethod, requestPath }) => {
    const csrf = document.cookie.split("; ")
      .find((part) => part.startsWith("gardenops_csrf="))?.slice("gardenops_csrf=".length) || "";
    const response = await fetch(requestPath, {
      body: requestBody === null ? undefined : JSON.stringify(requestBody),
      credentials: "include",
      headers: {
        ...(requestBody === null ? {} : { "content-type": "application/json" }),
        ...(csrf ? { "x-csrf-token": decodeURIComponent(csrf) } : {}),
        ...requestHeaders,
      },
      method: requestMethod,
    });
    return { status: response.status };
  }, {
    requestBody: body,
    requestHeaders: headers,
    requestMethod: method,
    requestPath: path,
  });
}

async function assertExpectedBrowserFailure(page, diagnostics, {
  body = null,
  headers = {},
  label,
  method,
  path,
  status = 403,
}) {
  const httpMark = diagnostics.httpErrors.length;
  const consoleMark = diagnostics.consoleErrors.length;
  const response = await issueBrowserRequest(page, { body, headers, method, path });
  assert(response.status === status, `${label} expected ${status}, got ${response.status}`);
  const expectedError = `${status} ${path}`;
  await waitFor(
    async () => diagnostics.httpErrors.slice(httpMark).includes(expectedError),
    `${label} HTTP diagnostic`,
  );
  assert(
    diagnostics.httpErrors.length === httpMark + 1,
    `${label} produced unexpected HTTP diagnostics`,
  );
  diagnostics.httpErrors.splice(httpMark, 1);
  await waitFor(
    async () => diagnostics.consoleErrors.length === consoleMark + 1,
    `${label} console diagnostic`,
  );
  diagnostics.consoleErrors.splice(consoleMark, 1);
}

async function captureMapRenderState(page) {
  return page.locator("#map-grid").evaluate((grid) => ({
    children: Array.from(grid.children).map((child) => ({
      className: child.className,
      objectId: child.getAttribute("data-object-id"),
      plotId: child.getAttribute("data-plot-id"),
      style: child.getAttribute("style"),
    })),
    gridLabel: grid.getAttribute("data-grid-label"),
    labels: Array.from(grid.querySelectorAll(".map-object-label")).map((label) => ({
      objectId: label.getAttribute("data-object-id"),
      style: label.getAttribute("style"),
      text: label.textContent,
    })),
    plots: Array.from(grid.querySelectorAll(".plot")).map((plot) => ({
      plotId: plot.getAttribute("data-plot-id"),
      style: plot.getAttribute("style"),
    })),
  }));
}

function mapRenderCounts(state) {
  return {
    children: state.children.length,
    labels: state.labels.length,
    plots: state.plots.length,
  };
}

function totalRenderMutations(observation) {
  return observation.attributes + observation.childLists + observation.added + observation.removed;
}

async function observeMapRenderChurn(page) {
  const grid = await page.locator("#map-grid").elementHandle();
  assert(grid, "Map grid is unavailable for render-churn observation");
  await grid.evaluate((element) => {
    const observation = { attributes: 0, childLists: 0, added: 0, removed: 0 };
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "attributes") observation.attributes += 1;
        if (mutation.type === "childList") {
          observation.childLists += 1;
          observation.added += mutation.addedNodes.length;
          observation.removed += mutation.removedNodes.length;
        }
      }
    });
    observer.observe(element, { attributes: true, childList: true, subtree: true });
    element.__phaseOneRenderChurn = { observation, observer };
  });
  return {
    stop: async () => {
      const observation = await grid.evaluate((element) => {
        const active = element.__phaseOneRenderChurn;
        if (!active) throw new Error("Map render-churn observer was lost");
        active.observer.disconnect();
        delete element.__phaseOneRenderChurn;
        return active.observation;
      });
      await grid.dispose();
      return observation;
    },
  };
}

async function observeMapReplaceChildren(page) {
  const grid = await page.locator("#map-grid").elementHandle();
  assert(grid, "Map grid is unavailable for coherent-render observation");
  await grid.evaluate((element) => {
    const descriptor = Object.getOwnPropertyDescriptor(element, "replaceChildren");
    const original = element.replaceChildren;
    const observation = { calls: 0, descriptor, original };
    Object.defineProperty(element, "replaceChildren", {
      configurable: true,
      value(...nodes) {
        observation.calls += 1;
        return observation.original.apply(this, nodes);
      },
      writable: true,
    });
    element.__phaseOneReplaceChildren = observation;
  });
  return {
    stop: async () => {
      const observation = await grid.evaluate((element) => {
        const active = element.__phaseOneReplaceChildren;
        if (!active) throw new Error("Map replaceChildren observer was lost");
        if (active.descriptor) {
          Object.defineProperty(element, "replaceChildren", active.descriptor);
        } else {
          delete element.replaceChildren;
        }
        delete element.__phaseOneReplaceChildren;
        return { replace_children_calls: active.calls };
      });
      await grid.dispose();
      return observation;
    },
  };
}

async function acceptConfirm(page) {
  const dialog = page.locator("[role='alertdialog']");
  await visible(dialog, "confirmation dialog");
  await dialog.locator(".confirm-yes").click();
}

async function fillPrompt(page, value, type = "text") {
  const input = page.locator(`.confirm-dialog input[type='${type}']`).last();
  await visible(input, `${type} prompt`);
  await input.fill(value);
  await input.locator("xpath=ancestor::*[contains(@class,'confirm-dialog')]")
    .locator(".confirm-yes").click();
}

async function authorizeSensitiveAction(page, password, { confirmFirst = false, reason }) {
  if (confirmFirst) await acceptConfirm(page);
  await fillPrompt(page, reason);
  await fillPrompt(page, password, "password");
}

async function reloadAndAccountForAborts(page, diagnostics) {
  const diagnosticMark = diagnostics.requestFailures.length;
  const aborted = [];
  const listener = (request) => {
    const failure = request.failure()?.errorText || "";
    if (failure.includes("ERR_ABORTED")) {
      aborted.push({ method: request.method(), path: new URL(request.url()).pathname });
    }
  };
  page.on("requestfailed", listener);
  try {
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
  } finally {
    page.off("requestfailed", listener);
  }
  const added = diagnostics.requestFailures.length - diagnosticMark;
  assert(added === aborted.length, "Reload produced unaccounted request failures");
  assert(
    aborted.every((request) => request.method === "GET" && request.path.startsWith("/api/")),
    "Reload aborted a non-GET or non-API request",
  );
  diagnostics.requestFailures.splice(diagnosticMark, added);
  return aborted;
}

async function closeMobileSurfaces(page) {
  if (await page.locator("#saved-views-dropdown:not([hidden])").count()) {
    await page.keyboard.press("Escape");
  }
  if (await page.locator("body.mobile-utility-open").count()) {
    await page.locator("#mobile-utility-close-btn").click().catch(() => {});
  }
  if (await page.locator("#mobile-quick-actions[aria-hidden='false']").count()) {
    await page.locator("#mobile-fab-backdrop").click({ force: true }).catch(() => {});
  }
  if (await page.locator("body.mobile-map-sheet-open").count()) {
    await page.keyboard.press("Escape");
    const closeButton = page.locator(
      "#mobile-map-layers-close-btn:visible, #mobile-map-layouts-close-btn:visible, "
      + "#mobile-map-tools-close-btn:visible, #mobile-map-shade-close-btn:visible",
    ).first();
    if (await page.locator("body.mobile-map-sheet-open").count() && await closeButton.count()) {
      await closeButton.click().catch(() => {});
    }
    if (await page.locator("body.mobile-map-sheet-open").count()) {
      await page.locator("#mobile-map-sheet-backdrop").click({ force: true }).catch(() => {});
    }
  }
  await page.waitForFunction(() => (
    !document.body.classList.contains("mobile-utility-open")
    && !document.body.classList.contains("mobile-map-sheet-open")
    && document.querySelector("#mobile-quick-actions")?.getAttribute("aria-hidden") !== "false"
    && document.querySelector("#saved-views-dropdown")?.hasAttribute("hidden") !== false
  ));
}

async function openMobileUtility(page) {
  await closeMobileSurfaces(page);
  await page.locator("#mobile-utility-btn").click();
  await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
}

async function assertMobileFocusReturn(page) {
  await closeMobileSurfaces(page);
  const utilityTrigger = page.locator("#mobile-utility-btn");
  await utilityTrigger.focus();
  await utilityTrigger.click();
  await visible(page.locator("#mobile-utility-sheet"), "mobile utility sheet");
  await page.locator("#mobile-utility-close-btn").click();
  await waitFor(
    async () => await utilityTrigger.evaluate((element) => document.activeElement === element),
    "mobile utility focus return",
  );

  const toolsTrigger = page.locator("#mobile-map-tools-btn");
  await toolsTrigger.focus();
  await toolsTrigger.click();
  await visible(page.locator("#mobile-map-tools-sheet"), "mobile map tools sheet");
  await page.locator("#mobile-map-tools-close-btn").click();
  await waitFor(
    async () => await toolsTrigger.evaluate((element) => document.activeElement === element),
    "mobile map tools focus return",
  );
}

async function openMap(page, profile) {
  await closeMobileSurfaces(page);
  const tab = page.locator(profile === "mobile" ? "#mobile-tab-map" : "#top-tab-map");
  await visible(tab, `${profile} Map tab`);
  await tab.click();
  await visible(page.locator("#map-grid"), `${profile} Map grid after navigation`);
}

async function selectGarden(page, profile, garden) {
  if (profile === "mobile") await openMobileUtility(page);
  const selector = page.locator(profile === "mobile" ? "#mobile-garden-select" : "#garden-select");
  await visible(selector, `${profile} garden selector`);
  await selector.selectOption(String(garden.id));
  return selector;
}

async function waitForMapObject(page, objectId, label) {
  await visible(page.locator(`.map-object-label[data-object-id='${objectId}']`), `${label} map object`);
}

function delayedSurface(pathname) {
  if (pathname === "/api/plots") return "plots";
  if (pathname === "/api/plants") return "plants";
  if (pathname === "/api/weather/summary" || pathname === "/api/weather/alerts") return "weather";
  if (pathname === "/api/plots/alerts") return "plot-alerts";
  if (pathname === "/api/notifications") return "notifications";
  if (/^\/api\/gardens\/\d+\/map-objects$/.test(pathname)) return "map-objects";
  if (pathname === "/api/layout-state") return "layout";
  if (/^\/api\/plots\/[^/]+\/plants$/.test(pathname)) return "indoor";
  if (/^\/api\/gardens\/\d+\/settings$/.test(pathname)) return "admin-settings";
  if (pathname === "/api/saved-views") return "saved-views";
  return null;
}

function gardenIdFromRequest(request) {
  const rawGardenId = request.headers()["x-garden-id"];
  const gardenId = Number(rawGardenId);
  return Number.isSafeInteger(gardenId) && gardenId > 0 ? gardenId : null;
}

async function delayGardenSwitchResponses(page, context, diagnostics, { alpha, beta }, surface, action) {
  const betaResolvers = [];
  const heldBetaRequests = new Set();
  const completedBetaRequests = new Set();
  const betaResponseCompletionFailures = [];
  const timeline = [];
  let sequence = 0;
  let alphaTargetStarted = false;
  let betaReleaseIssued = false;
  let betaResponsesContinued = 0;
  let betaTargetHeld = false;
  let heldResponseCountWhenAlphaStarted = 0;
  const failureMark = diagnostics.requestFailures.length;
  const aborted = [];
  const failureListener = (request) => {
    const failure = request.failure()?.errorText || "";
    if (failure.includes("ERR_ABORTED")) {
      aborted.push({ method: request.method(), path: new URL(request.url()).pathname });
    }
  };
  context.on("requestfailed", failureListener);
  const responseListener = (response) => {
    const request = response.request();
    if (!heldBetaRequests.has(request)) return;
    void response.finished()
      .then((failure) => {
        if (failure) {
          betaResponseCompletionFailures.push(String(failure));
          return;
        }
        completedBetaRequests.add(request);
      })
      .catch((error) => betaResponseCompletionFailures.push(String(error)));
  };
  page.on("response", responseListener);
  const handler = async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const requestSurface = request.method() === "GET" ? delayedSurface(pathname) : null;
    const gardenId = gardenIdFromRequest(request);
    const isBetaTarget = request.method() === "GET"
      && gardenId === beta.id
      && requestSurface === surface;
    const isHeldBetaResponse = !betaReleaseIssued && isBetaTarget;
    if (isBetaTarget) {
      betaTargetHeld = true;
      timeline.push({ event: "beta-target-held", sequence: ++sequence, surface });
    }
    if (
      request.method() === "GET"
      && gardenId === alpha.id
      && requestSurface === surface
      && !alphaTargetStarted
    ) {
      alphaTargetStarted = true;
      heldResponseCountWhenAlphaStarted = betaResolvers.length;
      timeline.push({ event: "alpha-target-started", sequence: ++sequence, surface });
    }
    if (isHeldBetaResponse) {
      heldBetaRequests.add(request);
      await new Promise((resolve) => betaResolvers.push(resolve));
    }
    await route.continue();
    if (isHeldBetaResponse) betaResponsesContinued += 1;
  };
  await context.route("**/api/**", handler);
  let routeInstalled = true;
  const gate = {
    async waitForAlphaTarget(label) {
      await waitFor(() => alphaTargetStarted, label);
    },
    async waitForBetaTarget(label) {
      await waitFor(() => betaTargetHeld, label);
    },
    releaseBetaResponses() {
      assert(!betaReleaseIssued, "A/B/A race released Beta responses more than once");
      assert(betaTargetHeld, `A/B/A race released Beta before holding ${surface}`);
      assert(alphaTargetStarted, `A/B/A race released Beta before Alpha ${surface} began`);
      assert(betaResolvers.length > 0, "A/B/A race had no held Beta responses to release");
      betaReleaseIssued = true;
      timeline.push({ event: "beta-released", sequence: ++sequence, held: betaResolvers.length, surface });
      for (const resolve of betaResolvers) resolve();
    },
  };
  try {
    await action(gate);
    assert(betaReleaseIssued, "A/B/A race action completed without releasing Beta responses");
    await waitFor(
      () => betaResponsesContinued === betaResolvers.length,
      "released Beta responses to continue",
    );
    await context.unroute("**/api/**", handler);
    routeInstalled = false;
    await waitFor(
      () => (
        betaResponseCompletionFailures.length > 0
        || completedBetaRequests.size === heldBetaRequests.size
      ),
      `released Beta ${surface} responses to finish`,
    );
    assert(
      betaResponseCompletionFailures.length === 0,
      `released Beta ${surface} response failed: ${betaResponseCompletionFailures.join(", ")}`,
    );
  } finally {
    if (!betaReleaseIssued) {
      betaReleaseIssued = true;
      for (const resolve of betaResolvers) resolve();
    }
    if (routeInstalled) await context.unroute("**/api/**", handler);
    context.off("requestfailed", failureListener);
    page.off("response", responseListener);
  }
  const addedFailures = diagnostics.requestFailures.length - failureMark;
  assert(addedFailures === aborted.length, "Delayed A/B/A produced an unaccounted request failure");
  assert(
    aborted.every((request) => request.method === "GET" && request.path.startsWith("/api/")),
    "Delayed A/B/A aborted a non-GET or non-API request",
  );
  diagnostics.requestFailures.splice(failureMark, addedFailures);
  const betaTarget = timeline.find((entry) => entry.event === "beta-target-held");
  const alphaTarget = timeline.find((entry) => entry.event === "alpha-target-started");
  const betaRelease = timeline.find((entry) => entry.event === "beta-released");
  assert(betaTarget && alphaTarget && betaRelease, `A/B/A ${surface} timeline is incomplete`);
  assert(
    betaTarget.sequence < alphaTarget.sequence
      && alphaTarget.sequence < betaRelease.sequence
      && heldResponseCountWhenAlphaStarted > 0,
    `A/B/A ${surface} did not overlap held Beta responses with Alpha requests`,
  );
  return {
    beta_held_response_count: heldBetaRequests.size,
    beta_response_completion_count: completedBetaRequests.size,
    surface,
  };
}

async function assertGlobalSearch(page, profile, alpha) {
  if (profile === "mobile") await openMobileUtility(page);
  const input = page.locator(profile === "mobile" ? "#mobile-global-plant-search" : "#global-plant-search");
  await visible(input, `${profile} global plant search`);
  await input.fill(alpha.plant_name);
  const result = page.locator(".global-search-dropdown .dropdown-item").filter({ hasText: alpha.plant_name });
  await visible(result, `${profile} global search result`);
  await result.click();
  await waitFor(
    async () => await page.locator(`.plot[data-plot-id='${alpha.plot_id}'].highlighted`).count() > 0,
    `${profile} global search map context`,
  );
}

async function openPlants(page, profile = "desktop") {
  await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
  await page.locator("#sub-mode-plants").click();
  await visible(page.locator("#plants-search"), "plants search");
}

async function addPlotAssignment(dialog, plotId) {
  const search = dialog.locator("#plot-assign-search");
  await search.fill(plotId);
  const option = dialog.locator(`.plot-dd-item[data-plot='${plotId}']`);
  if (await option.count()) await option.click();
  else await search.press("Enter");
  await visible(dialog.locator(`.plot-chip[data-plot='${plotId}']`), `plot assignment ${plotId}`);
}

async function exercisePlantAndSavedView(
  page,
  diagnostics,
  fixture,
  alpha,
  profile = "desktop",
  lifecycleLabel = profile,
  { assignmentPlotId = alpha.plot_id } = {},
) {
  const suffix = lifecycleLabel.replace(/[^a-z0-9]+/gi, " ").trim();
  const plantName = `Phase 1 Browser Mint ${suffix}`;
  const renamed = `${plantName} Edited`;
  const savedViewName = `Phase 1 Browser Plant View ${suffix}`;
  await openPlants(page, profile);

  await page.locator("#add-plant-btn").click();
  await visible(page.locator("#plant-search-create-btn"), "plant search create-new action");
  await page.locator("#plant-search-create-btn").click();
  let dialog = page.locator("#create-plant-form");
  await visible(dialog, "create plant form");
  await dialog.locator("input[name='name']").fill(plantName);
  await dialog.locator("select[name='category']").selectOption("urter");
  await dialog.locator("input[name='link']").fill("https://example.com/phase-one-mint");
  await addPlotAssignment(dialog, assignmentPlotId);
  await dialog.locator("button[type='submit']").click();
  const row = plantRecord(page, profile, plantName);
  await visible(row, "created plant row");
  const plantId = await row.getAttribute("data-plt-id");
  assert(plantId, "Created plant row has no plant ID");

  await row.locator("[data-edit-plt]").click();
  dialog = page.locator("#edit-plant-form");
  await visible(dialog, "edit plant form");
  await dialog.locator("input[name='name']").fill(renamed);
  await dialog.locator("input[name='link']").fill("https://example.com/phase-one-mint-edited");
  await dialog.locator(`.plot-chip[data-plot='${assignmentPlotId}'] .chip-remove`).click();
  const unlinkPath = `/api/plots/${assignmentPlotId}/plants/${plantId}`;
  const unlinkFailureMark = diagnostics.requestFailures.length;
  const unlinkAborts = [];
  const unlinkFailureListener = (request) => {
    const failure = request.failure()?.errorText || "";
    if (
      request.method() === "DELETE"
      && new URL(request.url()).pathname === unlinkPath
      && failure.includes("ERR_ABORTED")
    ) unlinkAborts.push(unlinkPath);
  };
  page.on("requestfailed", unlinkFailureListener);
  const unlinkResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE" && new URL(response.url()).pathname === unlinkPath
  ));
  await dialog.locator("button[type='submit']").click();
  const unlinkResponse = await unlinkResponsePromise;
  assert(unlinkResponse.status() === 204, `Plant unlink returned ${unlinkResponse.status()}`);
  await page.waitForTimeout(100);
  page.off("requestfailed", unlinkFailureListener);
  const unlinkFailuresAdded = diagnostics.requestFailures.length - unlinkFailureMark;
  assert(unlinkFailuresAdded === unlinkAborts.length, "Plant unlink produced an unrelated request failure");
  diagnostics.requestFailures.splice(unlinkFailureMark, unlinkFailuresAdded);
  const editedRow = plantRecord(page, profile, renamed);
  await visible(editedRow, "edited and unlinked plant row");

  await editedRow.locator("[data-edit-plt]").click();
  dialog = page.locator("#edit-plant-form");
  await visible(dialog, "unlinked plant edit form");
  await addPlotAssignment(dialog, assignmentPlotId);
  await dialog.locator("button[type='submit']").click();
  await visible(editedRow, "relinked plant row");

  const search = page.locator("#plants-search");
  await search.fill(renamed);
  await page.locator("#saved-views-trigger").click();
  page.once("dialog", (nativeDialog) => nativeDialog.accept(savedViewName));
  await page.locator("#saved-views-dropdown .saved-views-save-btn").click();
  let savedView = page.locator("#saved-views-dropdown .saved-views-item").filter({ hasText: savedViewName });
  await visible(savedView, "created saved view");
  await search.fill("");
  savedView = page.locator("#saved-views-dropdown .saved-views-item").filter({ hasText: savedViewName });
  if (!await savedView.isVisible()) await page.locator("#saved-views-trigger").click();
  await visible(savedView, "saved view ready to apply");
  await savedView.locator(".saved-views-item-label").click();
  await waitFor(() => search.inputValue().then((value) => value === renamed), "saved view application");
  await reloadAndAccountForAborts(page, diagnostics);
  await visible(page.locator(".app-shell"), "app after saved view reload");
  await openPlants(page, profile);
  await page.locator("#saved-views-trigger").click();
  const reloadedView = page.locator("#saved-views-dropdown .saved-views-item").filter({ hasText: savedViewName });
  await visible(reloadedView, "saved view after reload");
  let savedViewDeleteRequests = 0;
  const savedViewDeleteListener = (request) => {
    if (
      request.method() === "DELETE"
      && new URL(request.url()).pathname.startsWith("/api/saved-views/")
    ) savedViewDeleteRequests += 1;
  };
  page.on("request", savedViewDeleteListener);
  await reloadedView.locator(".saved-views-item-delete").click();
  await visible(page.locator("[role='alertdialog']"), "saved view delete confirmation");
  await page.locator("[role='alertdialog'] .confirm-no").click();
  await page.waitForTimeout(100);
  assert(savedViewDeleteRequests === 0, "Cancelled saved view deletion sent a request");
  if (!await reloadedView.isVisible()) await page.locator("#saved-views-trigger").click();
  await visible(reloadedView, "saved view retained after cancelled deletion");
  const savedViewDeleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname.startsWith("/api/saved-views/")
  ));
  const savedViewRefreshResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/saved-views"
  ));
  await reloadedView.locator(".saved-views-item-delete").click();
  await acceptConfirm(page);
  assert((await savedViewDeleteResponsePromise).ok(), "Confirmed saved view deletion failed");
  assert((await savedViewRefreshResponsePromise).ok(), "Saved view list did not refresh after deletion");
  page.off("request", savedViewDeleteListener);
  assert(savedViewDeleteRequests === 1, "Confirmed saved view deletion sent an unexpected request count");
  if (!await page.locator("#saved-views-dropdown").isVisible()) {
    await page.locator("#saved-views-trigger").click();
  }
  await waitFor(async () => await reloadedView.count() === 0, "saved view deletion");
  await page.locator("#saved-views-trigger").click();

  await search.fill(renamed);
  const deleteRow = plantRecord(page, profile, renamed);
  await visible(deleteRow, "edited plant ready for deletion");
  await deleteRow.locator("[data-edit-plt]").click();
  await page.locator("#delete-edit-plant").click();
  await acceptConfirm(page);
  await waitFor(async () => await deleteRow.count() === 0, "plant deletion cascade");
  return { plantName: renamed, savedViewName };
}

async function mutateIndoorPlant(
  page,
  diagnostics,
  fixture,
  profile = "desktop",
  lifecycleLabel = profile,
) {
  await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
  await page.locator("#sub-mode-indoor").click();
  const card = page.locator("#indoor-tab-content .indoor-card-wrapper")
    .filter({ hasText: fixture.phase_one.indoor.plant_name });
  await visible(card, "seeded indoor plant");
  await card.locator(".indoor-room-row button").click();
  const shelf = `Phase 1 Browser Shelf ${lifecycleLabel.replace(/[^a-z0-9]+/gi, " ").trim()}`;
  await card.locator(".indoor-room-input").fill(shelf);
  await card.locator(".indoor-room-edit .btn-primary").click();
  await visible(card.getByText(shelf, { exact: false }), "mutated indoor room");
  await reloadAndAccountForAborts(page, diagnostics);
  await openIndoor(page, profile);
  await visible(card.getByText(shelf, { exact: false }), "persisted indoor room after reload");
  await card.locator(".indoor-room-row button").click();
  await card.locator(".indoor-room-input").fill(fixture.phase_one.indoor.room_label);
  await card.locator(".indoor-room-edit .btn-primary").click();
  await visible(card.getByText(fixture.phase_one.indoor.room_label, { exact: false }), "restored indoor room");
}

async function enableMapEditor(page, profile = "desktop") {
  const edit = page.locator("#edit-mode-btn");
  if (profile === "mobile") {
    const layers = page.locator("#map-layers-panel.mobile-map-sheet--open");
    if (!await layers.count()) await page.locator("#mobile-map-layers-btn").click();
    await visible(layers, "mobile map layers");
    await visible(edit, "mobile map edit action");
    if (await edit.getAttribute("aria-pressed") !== "true") await edit.click();
  } else if (await edit.isVisible()) {
    if (await edit.getAttribute("aria-pressed") !== "true") await edit.click();
  } else {
    await page.locator("#top-tab-admin").click();
    await visible(page.locator("#adm-map-open-editor-btn"), "admin map editor action");
    await page.locator("#adm-map-open-editor-btn").click();
  }
}

async function createMapObject(page, type, index) {
  const form = page.locator("#map-objects-panel .map-object-custom-form");
  const name = `Phase 1 ${type} ${index}`;
  const fields = form.locator(".map-object-identity-grid");
  await fields.locator("input[type='text']").fill(name);
  await fields.locator(".map-object-type-select").selectOption(type);
  await fields.locator("select").nth(1).selectOption(index % 2 ? "ellipse" : "rectangle");
  if (index === 0) await form.locator("input[type='checkbox']").check();
  await form.locator("button[type='submit']").click();
  const row = page.locator("#map-objects-panel .map-object-row").filter({ hasText: name });
  await visible(row, `created ${type} map object`);
  return { name, row };
}

async function deleteMapObjectRow(page, row, name) {
  await row.locator(".map-object-icon-btn").click();
  await acceptConfirm(page);
  await waitFor(async () => await page.getByText(name, { exact: true }).count() === 0, `delete ${name}`);
}

async function tapMapTarget(page, target, label) {
  const point = await target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const candidates = [
      [0.2, 0.2], [0.5, 0.75], [0.75, 0.75], [0.25, 0.5], [0.75, 0.5],
    ];
    for (const [xRatio, yRatio] of candidates) {
      const x = rect.left + rect.width * xRatio;
      const y = rect.top + rect.height * yRatio;
      const hit = document.elementFromPoint(x, y);
      if (
        hit
        && (hit === element || element.contains(hit))
        && !hit.closest("button, a, input, select, textarea, [role='button']")
      ) {
        return { x, y };
      }
    }
    return null;
  });
  assert(point, `${label} has no non-interactive browser hit-test point`);
  await page.touchscreen.tap(point.x, point.y);
}

async function editMobilePlotThroughBottomSheet(page, fromPlotId, toPlotId) {
  const plot = page.locator(`.plot[data-plot-id='${fromPlotId}']`);
  await visible(plot, `mobile plot ${fromPlotId}`);
  await tapMapTarget(page, plot, `mobile plot ${fromPlotId}`);
  const editAction = page.locator(`.drawer-edit-plot-btn[data-edit-plot='${fromPlotId}']`);
  await visible(editAction, `mobile edit action for ${fromPlotId}`);
  assert(
    await editAction.getAttribute("aria-label") === "Edit plot",
    "Mobile plot edit action is not discoverable by its accessible name",
  );
  await editAction.click();
  const editDialog = page.locator("#edit-plot-form");
  await visible(editDialog, `mobile plot edit dialog for ${fromPlotId}`);
  await editDialog.locator("input[name='plot_name']").fill(toPlotId);
  const updateResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === `/api/plots/${fromPlotId}`
  ));
  await editDialog.locator("button[type='submit']").click();
  assert((await updateResponsePromise).ok(), `Mobile plot edit failed for ${fromPlotId}`);
  await visible(page.locator(`.plot[data-plot-id='${toPlotId}']`), `mobile renamed plot ${toPlotId}`);
}

async function deleteMobilePlotThroughBottomSheet(page, diagnostics, plotId) {
  const plot = page.locator(`.plot[data-plot-id='${plotId}']`);
  await visible(plot, `mobile plot ${plotId} ready to delete`);
  await tapMapTarget(page, plot, `mobile plot ${plotId} ready to delete`);
  const deleteAction = page.locator(`.drawer-delete-plot-btn[data-delete-plot='${plotId}']`);
  await visible(deleteAction, `mobile delete action for ${plotId}`);
  assert(
    await deleteAction.getAttribute("aria-label") === "Delete plot",
    "Mobile plot delete action is not discoverable by its accessible name",
  );
  const deletePath = `/api/plots/${plotId}`;
  const failureMark = diagnostics.requestFailures.length;
  const expectedAborts = [];
  const failureListener = (request) => {
    const failure = request.failure()?.errorText || "";
    if (request.method() === "DELETE"
      && new URL(request.url()).pathname === deletePath
      && failure.includes("ERR_ABORTED")) expectedAborts.push(deletePath);
  };
  page.on("requestfailed", failureListener);
  try {
    const deleteResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "DELETE"
      && new URL(response.url()).pathname === deletePath
    ));
    await deleteAction.click();
    await acceptConfirm(page);
    assert((await deleteResponsePromise).status() === 204, `Mobile plot delete failed for ${plotId}`);
    await waitFor(async () => await plot.count() === 0, `mobile plot deletion ${plotId}`);
  } finally {
    await page.waitForTimeout(100);
    page.off("requestfailed", failureListener);
  }
  const failuresAdded = diagnostics.requestFailures.length - failureMark;
  assert(
    failuresAdded === expectedAborts.length,
    "Mobile plot delete produced an unrelated request failure",
  );
  diagnostics.requestFailures.splice(failureMark, failuresAdded);
}

async function createMobileEditorPlot(page) {
  const plotId = "P1MOBILEPLOT";
  await openMap(page, "mobile");
  await enableMapEditor(page, "mobile");
  await closeMobileSurfaces(page);
  const emptyCell = page.locator("#map-grid .empty-cell").first();
  await visible(emptyCell, "mobile empty map cell for editor plot lifecycle");
  await emptyCell.click();
  const createDialog = page.locator("#create-plot-form");
  await visible(createDialog, "mobile plot create dialog for editor lifecycle");
  await createDialog.locator("input[name='plot_id']").fill(plotId);
  const createResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/plots"
  ));
  await createDialog.locator("button[type='submit']").click();
  assert((await createResponsePromise).status() === 201, "Mobile editor plot creation failed");
  await visible(page.locator(`.plot[data-plot-id='${plotId}']`), "mobile editor-owned plot");
}

async function exerciseDiscoverableMobilePlotEdit(page, diagnostics) {
  const plotId = "P1MOBILEPLOT";
  const renamedPlotId = `${plotId}EDITED`;
  await createMobileEditorPlot(page);
  await openMap(page, "mobile");
  await enableMapEditor(page, "mobile");
  await closeMobileSurfaces(page);
  await editMobilePlotThroughBottomSheet(page, plotId, renamedPlotId);
  await deleteMobilePlotThroughBottomSheet(page, diagnostics, renamedPlotId);
}

async function moveMapObjectWithTouch(page, surface, objectId, alpha) {
  const grid = page.locator("#map-grid");
  const [gridBox, surfaceBox, gridDimensions] = await Promise.all([
    grid.boundingBox(),
    surface.boundingBox(),
    grid.evaluate((element) => ({
      cols: Number(getComputedStyle(element).getPropertyValue("--grid-cols")),
      rows: Number(getComputedStyle(element).getPropertyValue("--grid-rows")),
    })),
  ]);
  assert(gridBox && surfaceBox, "Touch manipulation needs visible map geometry");
  assert(gridDimensions.cols > 0 && gridDimensions.rows > 0, "Touch manipulation has invalid map dimensions");
  const startX = surfaceBox.x + surfaceBox.width / 2;
  const startY = surfaceBox.y + surfaceBox.height / 2;
  const cellWidth = gridBox.width / gridDimensions.cols;
  const endX = Math.min(gridBox.x + gridBox.width - 2, startX + cellWidth);
  const positionedLabel = page.locator(`.map-object-label[data-object-id='${objectId}']`);
  const styleBeforeTouchMove = await positionedLabel.getAttribute("style");
  const hitTest = await surface.evaluate((element, { x, y }) => {
    const hit = document.elementFromPoint(x, y);
    return hit !== null && (hit === element || element.contains(hit));
  }, { x: startX, y: startY });
  assert(hitTest, "Touch manipulation start point does not hit the map-object surface");
  const geometryPath = `/api/gardens/${alpha.id}/map-objects/${objectId}`;
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === geometryPath
  ));
  const protocol = await page.context().newCDPSession(page);
  const touchPoint = (x, y) => ({
    force: 1,
    id: 41,
    radiusX: 1,
    radiusY: 1,
    x,
    y,
  });
  try {
    await protocol.send("Input.dispatchTouchEvent", {
      touchPoints: [touchPoint(startX, startY)],
      type: "touchStart",
    });
    await protocol.send("Input.dispatchTouchEvent", {
      touchPoints: [touchPoint(endX, startY)],
      type: "touchMove",
    });
    await protocol.send("Input.dispatchTouchEvent", {
      touchPoints: [],
      type: "touchEnd",
    });
  } finally {
    await protocol.detach().catch(() => {});
  }
  assert((await responsePromise).ok(), "Touch map object move PATCH failed");
  await waitFor(
    async () => await positionedLabel.getAttribute("style") !== styleBeforeTouchMove,
    "touch map object move render",
  );
}

async function exerciseMapObjectEditor(page, diagnostics, alpha, { profile = "desktop", useTouch = false } = {}) {
  await enableMapEditor(page, profile);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "map object category editor");
  const mapBoundsBefore = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsBefore, "Map grid has no initial dimensions");
  const created = [];
  const objectTypes = profile === "mobile" ? ["patio"] : MAP_OBJECT_TYPES;
  for (const [index, type] of objectTypes.entries()) {
    created.push(await createMapObject(page, type, index));
  }

  const primary = created[0];
  const primaryId = await primary.row.getAttribute("data-object-id");
  assert(primaryId, "Created primary map object has no public ID");
  const detail = page.locator("#map-objects-panel .map-object-detail").filter({ hasText: primary.name });
  if (!await detail.isVisible()) await primary.row.locator(".map-object-row-main").click();
  await visible(detail, "primary map object details");
  const positionInputs = detail.locator(".map-object-position-grid input");
  await positionInputs.nth(0).fill("2");
  await positionInputs.nth(1).fill("2");
  await positionInputs.nth(2).fill("3");
  await positionInputs.nth(3).fill("2");
  const geometryPath = `/api/gardens/${alpha.id}/map-objects/${primaryId}`;
  const waitForGeometryPatch = () => page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === geometryPath
  ));
  let geometryResponsePromise = waitForGeometryPatch();
  await detail.locator(".map-object-geometry-form button[type='submit']").click();
  assert((await geometryResponsePromise).ok(), "Map object geometry form PATCH failed");
  const surface = page.locator(`.map-object-interaction-surface[data-object-id='${primaryId}']`);
  await visible(surface, "direct manipulation surface");
  await surface.focus();
  const positionedPrimaryLabel = page.locator(`.map-object-label[data-object-id='${primaryId}']`);
  const styleBeforeKeyboardMove = await positionedPrimaryLabel.getAttribute("style");
  geometryResponsePromise = waitForGeometryPatch();
  await surface.press("ArrowRight");
  assert((await geometryResponsePromise).ok(), "Map object keyboard move PATCH failed");
  await waitFor(
    async () => await positionedPrimaryLabel.getAttribute("style") !== styleBeforeKeyboardMove,
    "map object keyboard move render",
  );
  await surface.focus();
  geometryResponsePromise = waitForGeometryPatch();
  await surface.press("Shift+ArrowDown");
  assert((await geometryResponsePromise).ok(), "Map object keyboard resize PATCH failed");
  if (useTouch) {
    await closeMobileSurfaces(page);
    await visible(surface, "mobile touch manipulation surface");
    await moveMapObjectWithTouch(page, surface, primaryId, alpha);
    await page.locator("#mobile-map-layers-btn").click();
    await visible(page.locator("#map-layers-panel"), "mobile map layers after touch manipulation");
  }

  if (!await detail.isVisible()) {
    await page.locator("#map-objects-panel .map-object-row").filter({ hasText: primary.name })
      .locator(".map-object-row-main").click();
  }
  await visible(detail, "primary map object details");
  const unitCreatePath = `/api/gardens/${alpha.id}/map-objects/${primaryId}/units`;
  const unitCreateResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === unitCreatePath
  ));
  await detail.locator(".map-object-create-row button").first().click();
  const unitCreateResponse = await unitCreateResponsePromise;
  assert(unitCreateResponse.status() === 201, "Nested map unit create failed");
  const createdUnit = await unitCreateResponse.json();
  assert(createdUnit && typeof createdUnit.public_id === "string", "Created nested map unit has no public ID");
  const renamedUnit = `Phase 1 ${profile} nested unit edited`;
  const unitUpdatePath = `${unitCreatePath}/${createdUnit.public_id}`;
  const unitTile = detail.locator(".map-object-unit").filter({ hasText: createdUnit.name }).first();
  await visible(unitTile, "created nested map unit tile");
  await unitTile.click();
  const unitForm = detail.locator(".map-object-unit-form");
  await visible(unitForm, "nested map unit editor");
  const unitIdentity = unitForm.locator(".map-object-identity-grid");
  await unitIdentity.locator("input[type='text']").fill(renamedUnit);
  await unitIdentity.locator("select").nth(0).selectOption("shelf");
  await unitIdentity.locator("select").nth(1).selectOption("ellipse");
  await unitIdentity.locator("input[type='color']").fill("#4c7e99");
  const unitPosition = unitForm.locator(".map-object-position-grid input");
  await unitPosition.nth(0).fill("2");
  await unitPosition.nth(1).fill("2");
  await unitPosition.nth(2).fill("1");
  await unitPosition.nth(3).fill("1");
  const unitUpdateResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === unitUpdatePath
  ));
  await unitForm.locator("button[type='submit']").click();
  assert((await unitUpdateResponsePromise).ok(), "Nested map unit editor PATCH failed");
  await visible(
    detail.locator(".map-object-unit").filter({ hasText: renamedUnit }),
    "nested unit editor update",
  );
  const mapBoundsAfterMutation = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsAfterMutation, "Map grid has no dimensions after object mutations");
  assert(
    Math.abs(mapBoundsAfterMutation.width - mapBoundsBefore.width) <= 1
      && Math.abs(mapBoundsAfterMutation.height - mapBoundsBefore.height) <= 1,
    "Map dimensions shifted during map-object labels or selection",
  );

  await reloadAndAccountForAborts(page, diagnostics);
  await enableMapEditor(page, profile);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "map object editor after reload");
  await visible(page.getByText(primary.name, { exact: true }), "map object geometry after reload");
  const reloadedPrimary = page.locator("#map-objects-panel .map-object-row").filter({ hasText: primary.name });
  await reloadedPrimary.locator(".map-object-row-main").click();
  const reloadedDetail = page.locator("#map-objects-panel .map-object-detail");
  const reloadedUnit = reloadedDetail.locator(".map-object-unit").filter({ hasText: renamedUnit });
  await visible(reloadedUnit, "nested unit edit after reload");
  await reloadedUnit.click();
  const reloadedUnitForm = reloadedDetail.locator(".map-object-unit-form");
  await visible(reloadedUnitForm, "reloaded nested unit editor for direct delete");
  const directUnitDeleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname === unitUpdatePath
  ));
  await reloadedUnitForm.locator(".map-object-create-row button").filter({ hasText: "Delete" }).click();
  await acceptConfirm(page);
  assert((await directUnitDeleteResponsePromise).ok(), "Direct nested unit delete failed");
  await waitFor(async () => await reloadedUnit.count() === 0, "direct nested unit removal");

  const cascadeUnitCreateResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === unitCreatePath
  ));
  await reloadedDetail.locator(".map-object-create-row button").first().click();
  const cascadeUnitCreateResponse = await cascadeUnitCreateResponsePromise;
  assert(cascadeUnitCreateResponse.status() === 201, "Cascade nested unit create failed");
  const cascadeUnit = await cascadeUnitCreateResponse.json();
  await visible(
    reloadedDetail.locator(".map-object-unit").filter({ hasText: cascadeUnit.name }),
    "nested unit retained for parent cascade",
  );
  const parentDeletePath = `/api/gardens/${alpha.id}/map-objects/${primaryId}`;
  const parentDeleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname === parentDeletePath
  ));
  await deleteMapObjectRow(page, reloadedPrimary, primary.name);
  const parentDeleteResponse = await parentDeleteResponsePromise;
  assert(parentDeleteResponse.ok(), "Map object parent delete failed");
  assert(
    (await parentDeleteResponse.json()).deleted_units === 1,
    "Map object parent delete did not report the nested-unit cascade",
  );
  await reloadAndAccountForAborts(page, diagnostics);
  assert(
    await page.locator(`.map-object-label[data-object-id='${primaryId}']`).count() === 0,
    "Map object delete did not cascade its nested unit from the reloaded map",
  );
  await enableMapEditor(page, profile);
  for (const item of created.slice(1)) {
    const row = page.locator("#map-objects-panel .map-object-row").filter({ hasText: item.name });
    await deleteMapObjectRow(page, row, item.name);
  }
  await waitFor(async () => await page.getByText(primary.name, { exact: true }).count() === 0, "map object UI cascade");
}

async function updateGardenSettings(
  page,
  diagnostics,
  alpha,
  profile = "desktop",
  mutationLabel = profile,
) {
  await openAdminGarden(page, profile);
  const address = page.locator("#adm-garden-address");
  await visible(address, "Garden settings address");
  const original = await address.inputValue();
  const changed = `Phase 1 ${mutationLabel} settings mutation`;
  const settingsPath = `/api/gardens/${alpha.id}/settings`;
  const saveAddress = async (value, label) => {
    await address.fill(value);
    const responsePromise = page.waitForResponse((response) => (
      response.request().method() === "PATCH"
      && new URL(response.url()).pathname === settingsPath
    ));
    await page.locator("#adm-garden-save").click();
    assert((await responsePromise).ok(), `${label} settings PATCH failed`);
    await waitFor(() => address.inputValue().then((current) => current === value), label);
  };
  await saveAddress(changed, "garden settings update");
  await reloadAndAccountForAborts(page, diagnostics);
  await openAdminGarden(page, profile);
  await visible(address, "garden settings after mutation reload");
  assert(await address.inputValue() === changed, "Garden settings mutation was not durable after reload");
  await saveAddress(original, `garden ${alpha.name} settings restore`);
}

async function openAdminGardenSection(page, profile = "desktop") {
  if (profile === "mobile") {
    await openMobileUtility(page);
    await page.locator("#mobile-admin-btn").click();
    await closeMobileSurfaces(page);
  } else {
    await page.locator("#top-tab-admin").click();
  }
  const gardenNav = page.locator(".adm-nav-btn[data-section='garden']");
  await visible(gardenNav, "Garden settings navigation");
  await gardenNav.click();
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  await waitFor(
    () => page.locator("#admin-view").getAttribute("aria-busy")
      .then((value) => value !== "true"),
    "Admin garden section load",
  );
}

async function openAdminGarden(page, profile = "desktop") {
  await openAdminGardenSection(page, profile);
  await visible(page.locator("#adm-map-save-layout-btn"), "Admin map setup controls");
}

async function exerciseEditorGardenSettingsAndLayoutWrite(
  page,
  diagnostics,
  alpha,
  profile = "desktop",
) {
  await updateGardenSettings(page, diagnostics, alpha, profile, `${profile} editor`);
  await openAdminGarden(page, profile);
  const north = page.locator("#adm-map-north-input");
  await visible(north, "editor north-direction layout control");
  const original = Number(await north.inputValue());
  assert(Number.isInteger(original), "Editor north-direction control has no integer value");
  const changed = (original + 1) % 360;
  const applyNorth = async (degrees, label) => {
    await north.fill(String(degrees));
    const responsePromise = page.waitForResponse((response) => (
      response.request().method() === "PATCH"
      && new URL(response.url()).pathname === "/api/layout-state"
    ));
    await page.locator("#adm-map-north-apply-btn").click();
    assert((await responsePromise).ok(), `${label} layout PATCH failed`);
    await waitFor(() => north.inputValue().then((value) => value === String(degrees)), label);
  };
  await applyNorth(changed, "editor layout update");
  await reloadAndAccountForAborts(page, diagnostics);
  await openAdminGarden(page, profile);
  await visible(north, "editor north direction after mutation reload");
  assert(await north.inputValue() === String(changed), "Editor layout mutation was not durable after reload");
  await applyNorth(original, "editor layout restore");
}

async function saveMobileSnapshot(page, fixture) {
  await closeMobileSurfaces(page);
  await page.locator("#mobile-map-layouts-btn").click();
  const sheet = page.locator("#mobile-map-layouts-sheet");
  await visible(sheet, "mobile layouts sheet");
  page.once("dialog", (dialog) => dialog.accept(fixture.phase_one.mobile_snapshot.name));
  await sheet.locator("#mobile-map-layouts-save-btn").click();
  await visible(sheet.getByText(fixture.phase_one.mobile_snapshot.name, { exact: true }), "saved mobile snapshot");
  await closeMobileSurfaces(page);
}

async function exerciseEditorMapObjectWrite(page) {
  await openMap(page, "desktop");
  await enableMapEditor(page);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "editor map object editor");
  const mapBoundsBefore = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsBefore, "Editor map grid has no initial dimensions");
  const created = await createMapObject(page, "patio", 9);
  await deleteMapObjectRow(page, created.row, created.name);
  const mapBoundsAfter = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsAfter, "Editor map grid has no dimensions after object mutation");
  assert(
    mapBoundsAfter.width === mapBoundsBefore.width && mapBoundsAfter.height === mapBoundsBefore.height,
    "Editor map dimensions shifted during map-object mutation",
  );
}

async function exerciseMobileMapObject(page, alpha) {
  await openMap(page, "mobile");
  await enableMapEditor(page);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "mobile map object editor");
  const mapBoundsBefore = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsBefore, "Mobile map grid has no initial dimensions");
  const created = await createMapObject(page, "terrace", 10);
  const objectId = await created.row.getAttribute("data-object-id");
  assert(objectId, "Mobile map object has no public ID");
  const surface = page.locator(`.map-object-interaction-surface[data-object-id='${objectId}']`);
  await visible(surface, "mobile direct manipulation surface");
  await moveMapObjectWithTouch(page, surface, objectId, alpha);
  await deleteMapObjectRow(page, created.row, created.name);
  const mapBoundsAfter = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsAfter, "Mobile map grid has no dimensions after object mutation");
  assert(
    mapBoundsAfter.width === mapBoundsBefore.width && mapBoundsAfter.height === mapBoundsBefore.height,
    "Mobile map dimensions shifted during map-object touch mutation",
  );
}

async function submitMobileQuickAction(page, fixture, alpha) {
  await closeMobileSurfaces(page);
  await page.locator("#mobile-fab").click();
  const sheet = page.locator("#mobile-quick-actions[aria-hidden='false']");
  await visible(sheet, "mobile quick actions");
  await sheet.locator("[data-quick-action='log-harvest']").click();
  const form = page.locator(".modal-form");
  await visible(form, "quick action harvest form");
  await form.locator("input[name='quantity']").fill("1");
  await form.locator("textarea[name='notes']").fill("Phase 1 mobile quick action");
  await form.locator("input[name='plant_ids']").fill(fixture.phase_one.indoor.plant_id);
  await form.locator("input[name='plot_ids']").fill(alpha.plot_id);
  await form.locator("button[type='submit']").click();
  await waitFor(async () => await page.locator(".modal-form").count() === 0, "mobile quick action submission");
}

async function downloadMapExport(page, diagnostics, trigger, label) {
  const failureMark = diagnostics.requestFailures.length;
  const aborted = [];
  const failureListener = (request) => {
    const path = new URL(request.url()).pathname;
    const failure = request.failure()?.errorText || "";
    if (request.method() === "GET" && path === "/api/plots/export" && failure.includes("ERR_ABORTED")) {
      aborted.push(path);
    }
  };
  page.on("requestfailed", failureListener);
  try {
    const downloadPromise = page.waitForEvent("download");
    await trigger.click();
    const download = await downloadPromise;
    const exportedPath = await download.path();
    await page.waitForTimeout(100);
    assert(exportedPath, `${label} did not produce a browser download`);
    return exportedPath;
  } finally {
    page.off("requestfailed", failureListener);
    const addedFailures = diagnostics.requestFailures.length - failureMark;
    assert(addedFailures === aborted.length, `${label} produced an unrelated request failure`);
    diagnostics.requestFailures.splice(failureMark, addedFailures);
  }
}

async function submitMapImport(page, password, { file, mobile = false, reason }) {
  const responsePromise = page.waitForResponse((response) => (
    new URL(response.url()).pathname === "/api/plots/import"
    && response.request().method() === "POST"
  ));
  if (mobile) {
    const chooserPromise = page.waitForEvent("filechooser");
    await page.locator("#mobile-import-map-btn").click();
    const chooser = await chooserPromise;
    await chooser.setFiles(file);
  } else {
    await page.locator("#import-map-input").setInputFiles(file);
  }
  await authorizeSensitiveAction(page, password, { confirmFirst: true, reason });
  return responsePromise;
}

async function assertRejectedMapImport(page, diagnostics, password, {
  expectedStatus,
  file,
  label,
  reason,
}) {
  const before = await captureMapRenderState(page);
  const observer = await observeMapRenderChurn(page);
  const httpMark = diagnostics.httpErrors.length;
  const consoleMark = diagnostics.consoleErrors.length;
  let churn;
  try {
    const response = await submitMapImport(page, password, { file, reason });
    assert(response.status() === expectedStatus, `${label} returned ${response.status()}`);
    await waitFor(
      async () => await page.locator(".toast-error, .toast.error").count() > 0,
      `${label} error`,
    );
    await waitFor(
      async () => diagnostics.httpErrors.length === httpMark + 1,
      `${label} HTTP diagnostic`,
    );
    assert(
      diagnostics.httpErrors.length === httpMark + 1,
      `${label} HTTP diagnostic was not isolated`,
    );
    await waitFor(
      async () => diagnostics.consoleErrors.length === consoleMark + 1,
      `${label} console diagnostic`,
    );
    diagnostics.httpErrors.splice(httpMark, 1);
    diagnostics.consoleErrors.splice(consoleMark, 1);
    assert(
      JSON.stringify(await captureMapRenderState(page)) === JSON.stringify(before),
      `${label} produced partial visible map state`,
    );
  } finally {
    churn = await observer.stop();
  }
  assert(
    churn.attributes === 0 && churn.childLists === 0 && churn.added === 0 && churn.removed === 0,
    `${label} churned the rendered map after rejection: ${JSON.stringify(churn)}`,
  );
  return churn;
}

function oversizedMapImportFile() {
  return {
    buffer: Buffer.from(JSON.stringify({
      schema_version: 1,
      plots: Array.from({ length: 1001 }, (_, index) => ({
        color: "#7d9f7a",
        grid_col: index % 100 + 1,
        grid_row: Math.floor(index / 100) + 1,
        notes: "",
        plot_id: `P1OVERSIZE${index}`,
        plot_number: index + 1,
        sub_zone: "",
        zone_code: "OV",
        zone_name: "Oversized import",
      })),
    })),
    mimeType: "application/json",
    name: "oversized-map.json",
  };
}

async function exerciseMobileMapImport(page, diagnostics, password) {
  await openMap(page, "mobile");
  await closeMobileSurfaces(page);
  await page.locator("#mobile-map-tools-btn").click();
  await visible(page.locator("#mobile-map-tools-sheet"), "mobile map tools sheet for import");
  const plotCountBefore = await page.locator("#map-grid .plot").count();
  const exportedPath = await downloadMapExport(
    page,
    diagnostics,
    page.locator("#mobile-export-map-btn"),
    "mobile map export",
  );
  const response = await submitMapImport(page, password, {
    file: exportedPath,
    mobile: true,
    reason: "phase-one-mobile-map-import",
  });
  assert(response.ok(), `Mobile exported map import failed with ${response.status()}`);
  await waitFor(
    async () => await page.locator("#map-grid .plot").count() === plotCountBefore,
    "mobile exported map import postcondition",
  );
  await page.waitForLoadState("networkidle");
  await closeMobileSurfaces(page);
}

function divergentMapImportFile(exportedPath, plotId, targetCell) {
  let layout;
  try {
    layout = JSON.parse(fs.readFileSync(exportedPath, "utf8"));
  } catch {
    throw new Error("Exported map did not contain readable JSON");
  }
  assert(
    layout && typeof layout === "object" && Array.isArray(layout.plots),
    "Exported map did not contain a plot layout",
  );
  const plot = layout.plots.find((candidate) => candidate.plot_id === plotId);
  assert(plot, `Exported map did not contain ${plotId}`);
  const original = { col: Number(plot.grid_col), row: Number(plot.grid_row) };
  assert(
    Number.isInteger(original.row) && Number.isInteger(original.col),
    `${plotId} did not have a positioned exported map cell`,
  );
  assert(
    original.row !== targetCell.row || original.col !== targetCell.col,
    "Divergent map import selected the plot's existing cell",
  );
  plot.grid_row = targetCell.row;
  plot.grid_col = targetCell.col;
  return {
    file: {
      buffer: Buffer.from(JSON.stringify(layout)),
      mimeType: "application/json",
      name: "divergent-successful-map.json",
    },
    original,
    target: targetCell,
  };
}

async function exerciseSnapshotsAndImport(page, diagnostics, password, alpha, beta) {
  const snapshotName = "Phase 1 Restore Snapshot";
  const rejectedImportRenderChurn = {};
  await openAdminGarden(page);
  page.once("dialog", (dialog) => dialog.accept(snapshotName));
  await page.locator("#adm-map-save-layout-btn").click();
  await page.locator("#adm-map-layouts-btn").click();
  let row = page.locator("#map-layouts-dialog .snapshot-row").filter({ hasText: snapshotName });
  await visible(row, "saved restore snapshot");

  await page.keyboard.press("Escape");
  await enableMapEditor(page);
  const snapshotRenderState = await captureMapRenderState(page);
  const snapshotRenderCounts = mapRenderCounts(snapshotRenderState);
  const label = page.locator(".map-object-label").first();
  const objectId = await label.getAttribute("data-object-id");
  assert(objectId, "Snapshot divergence object has no public ID");
  await label.click();
  const surface = page.locator(`.map-object-interaction-surface[data-object-id='${objectId}']`);
  const positionedLabel = page.locator(`.map-object-label[data-object-id='${objectId}']`);
  await visible(surface, "snapshot divergence map object");
  const before = await positionedLabel.getAttribute("style");
  await surface.focus();
  await surface.press("ArrowRight");
  await waitFor(async () => await positionedLabel.getAttribute("style") !== before, "snapshot visible divergence");
  const diverged = await positionedLabel.getAttribute("style");

  await openAdminGarden(page);
  await page.locator("#adm-map-layouts-btn").click();
  row = page.locator("#map-layouts-dialog .snapshot-row").filter({ hasText: snapshotName });
  await row.locator(".snapshot-restore").click();
  await page.locator("[role='alertdialog'] .confirm-no").click();
  assert(await positionedLabel.getAttribute("style") === diverged, "Cancelled restore changed visible state");
  const restoreObserver = await observeMapRenderChurn(page);
  const replaceChildrenObserver = await observeMapReplaceChildren(page);
  let snapshotRestoreChurn;
  let snapshotRestoreRender;
  await row.locator(".snapshot-restore").click();
  try {
    const restoreResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && /^\/api\/snapshots\/[^/]+\/restore$/.test(new URL(response.url()).pathname)
    ));
    await authorizeSensitiveAction(page, password, { confirmFirst: true, reason: "phase-one-snapshot-restore" });
    assert((await restoreResponsePromise).ok(), "Snapshot restore returned an error");
    await waitFor(
      async () => JSON.stringify(await captureMapRenderState(page)) === JSON.stringify(snapshotRenderState),
      "snapshot restore rendered state",
    );
  } finally {
    snapshotRestoreChurn = await restoreObserver.stop();
    snapshotRestoreRender = await replaceChildrenObserver.stop();
  }
  const restoredSnapshotState = await captureMapRenderState(page);
  const restoredSnapshotCounts = mapRenderCounts(restoredSnapshotState);
  assert(
    JSON.stringify(restoredSnapshotCounts) === JSON.stringify(snapshotRenderCounts),
    "Snapshot restore changed rendered map counts",
  );
  assert(
    totalRenderMutations(snapshotRestoreChurn) > 0,
    "Successful snapshot restore produced no observed map render mutations",
  );
  assert(
    snapshotRestoreRender.replace_children_calls === 1,
    `Snapshot restore rendered ${snapshotRestoreRender.replace_children_calls} map-grid cycles instead of one`,
  );

  await openAdminGarden(page);
  const exportedPath = await downloadMapExport(
    page,
    diagnostics,
    page.locator("#adm-map-export-btn"),
    "desktop map export",
  );
  await openMap(page, "desktop");
  await enableMapEditor(page);
  const emptyCell = page.locator("#map-grid .empty-cell").first();
  await visible(emptyCell, "empty cell for divergent map import");
  const divergentImport = divergentMapImportFile(exportedPath, alpha.plot_id, await emptyCell.evaluate((cell) => ({
    col: Number(cell.getAttribute("data-col")),
    row: Number(cell.getAttribute("data-row")),
  })));
  const importStateBefore = await captureMapRenderState(page);
  const importResponse = await submitMapImport(page, password, {
    file: divergentImport.file,
    reason: "phase-one-divergent-map-import",
  });
  assert(importResponse.ok(), `Divergent map import failed with ${importResponse.status()}`);
  const importedPlot = page.locator(`.plot[data-plot-id='${alpha.plot_id}']`);
  await waitFor(async () => (
    await importedPlot.getAttribute("data-row") === String(divergentImport.target.row)
      && await importedPlot.getAttribute("data-col") === String(divergentImport.target.col)
  ), "divergent successful map import state");
  const divergentImportState = await captureMapRenderState(page);
  assert(
    JSON.stringify(divergentImportState) !== JSON.stringify(importStateBefore),
    "Successful map import did not render divergent state",
  );
  await page.waitForLoadState("networkidle");

  await openAdminGarden(page);
  await page.locator("#adm-map-layouts-btn").click();
  row = page.locator("#map-layouts-dialog .snapshot-row").filter({ hasText: snapshotName });
  await visible(row, "snapshot available to restore divergent import");
  const finalRestoreResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && /^\/api\/snapshots\/[^/]+\/restore$/.test(new URL(response.url()).pathname)
  ));
  await row.locator(".snapshot-restore").click();
  await authorizeSensitiveAction(page, password, { confirmFirst: true, reason: "phase-one-restore-divergent-import" });
  assert((await finalRestoreResponsePromise).ok(), "Final snapshot restore returned an error");
  await waitFor(async () => (
    await importedPlot.getAttribute("data-row") === String(divergentImport.original.row)
      && await importedPlot.getAttribute("data-col") === String(divergentImport.original.col)
  ), "snapshot restoration after divergent import");
  await waitFor(
    async () => JSON.stringify(await captureMapRenderState(page)) === JSON.stringify(snapshotRenderState),
    "restored snapshot state after divergent import",
  );
  await openMap(page, "desktop");
  await waitForMapObject(page, alpha.object_public_id, alpha.object_label);

  await openMap(page, "desktop");
  rejectedImportRenderChurn.unsupported_schema = await assertRejectedMapImport(page, diagnostics, password, {
    expectedStatus: 422,
    file: {
      buffer: Buffer.from('{"schema_version":999,"plots":[]}'),
      mimeType: "application/json",
      name: "wrong-version.json",
    },
    label: "unsupported-schema map import",
    reason: "phase-one-malformed-map-import",
  });
  rejectedImportRenderChurn.structurally_incomplete = await assertRejectedMapImport(page, diagnostics, password, {
    expectedStatus: 422,
    file: {
      buffer: Buffer.from('{"schema_version":1,"plots":[{"plot_id":"INCOMPLETE"}]}'),
      mimeType: "application/json",
      name: "structurally-incomplete-map.json",
    },
    label: "structurally incomplete map import",
    reason: "phase-one-incomplete-map-import",
  });
  rejectedImportRenderChurn.oversized = await assertRejectedMapImport(page, diagnostics, password, {
    expectedStatus: 422,
    file: oversizedMapImportFile(),
    label: "oversized map import",
    reason: "phase-one-oversized-map-import",
  });

  await selectGarden(page, "desktop", beta);
  await openMap(page, "desktop");
  await waitForMapObject(page, beta.object_public_id, beta.object_label);
  await openAdminGarden(page);
  const betaExportedPath = await downloadMapExport(
    page,
    diagnostics,
    page.locator("#adm-map-export-btn"),
    "cross-garden map export",
  );
  await selectGarden(page, "desktop", alpha);
  await openMap(page, "desktop");
  await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
  rejectedImportRenderChurn.cross_garden = await assertRejectedMapImport(page, diagnostics, password, {
    expectedStatus: 409,
    file: betaExportedPath,
    label: "cross-garden map import",
    reason: "phase-one-cross-garden-map-import",
  });

  await openAdminGarden(page);
  await page.locator("#adm-map-layouts-btn").click();
  row = page.locator("#map-layouts-dialog .snapshot-row").filter({ hasText: snapshotName });
  await row.locator(".snapshot-delete").click();
  await authorizeSensitiveAction(page, password, { reason: "phase-one-snapshot-delete" });
  await waitFor(async () => await page.getByText(snapshotName, { exact: true }).count() === 0, "snapshot deletion");
  await page.keyboard.press("Escape");
  return {
    rejected_import_render_churn: rejectedImportRenderChurn,
    successful_map_state_transitions: {
      divergent_import: {
        imported_cell: divergentImport.target,
        original_cell: divergentImport.original,
        target_plot_id: alpha.plot_id,
      },
      snapshot_restore: {
        mutation_count: totalRenderMutations(snapshotRestoreChurn),
        replace_children_calls: snapshotRestoreRender.replace_children_calls,
        restored_render_counts: restoredSnapshotCounts,
        snapshot_render_counts: snapshotRenderCounts,
      },
    },
  };
}

async function assertEditorAffordances(page, guarded, profile) {
  assert(
    !await page.locator("body").evaluate((body) => body.classList.contains("garden-read-only")),
    "Editor rendered as read-only",
  );
  await openPlants(page, profile);
  const addPlant = page.locator("#add-plant-btn");
  await visible(addPlant, "editor add plant affordance");
  assert(!await addPlant.isDisabled(), "Editor add plant control is disabled");
  await openMap(page, profile);
  const editButton = page.locator("#edit-mode-btn");
  if (profile === "mobile") await page.locator("#mobile-map-layers-btn").click();
  await visible(editButton, "editor map edit affordance");
  assert(!await editButton.isDisabled(), "Editor map edit control is disabled");
  await assertExpectedBrowserFailure(page, guarded.diagnostics, {
    label: "editor admin-only map export denial",
    method: "GET",
    path: "/api/plots/export",
  });
}

async function assertViewerSettingsWriteUnavailable(page, profile) {
  if (profile === "mobile") {
    await openMobileUtility(page);
    const admin = page.locator("#mobile-admin-btn");
    await visible(admin, "viewer mobile admin entry");
    await admin.click();
    await closeMobileSurfaces(page);
  } else {
    const admin = page.locator("#top-tab-admin");
    await visible(admin, "viewer desktop admin entry");
    await admin.click();
  }
  await waitFor(
    async () => await page.locator(".adm-layout").count() > 0,
    "viewer admin panel",
  );
  const save = page.locator("#adm-garden-save");
  assert(
    await save.count() === 0 || await save.isDisabled(),
    "Viewer garden-settings save control is writable",
  );
}

async function assertViewerDenied(page, alpha, guarded, profile, { directMutation = false } = {}) {
  await visible(page.locator("[data-garden-role]:visible").filter({ hasText: /read-only/i }).first(), "viewer read-only indicator");
  assert(
    await page.locator("body").evaluate((body) => body.classList.contains("garden-read-only")),
    "Viewer did not render the read-only state",
  );
  await openPlants(page, profile);
  await visible(plantRecord(page, profile, alpha.plant_name), "viewer-owned plant record");
  const addPlant = page.locator("#add-plant-btn");
  await visible(addPlant, "viewer add plant affordance");
  assert(await addPlant.isDisabled(), "Viewer add plant control is enabled");
  const viewerRecords = page.locator(profile === "mobile"
    ? "#plants-mobile-list article[data-plt-id] [data-edit-plt]"
    : "#plants-table-body tr[data-plt-id] [data-edit-plt]");
  assert(await viewerRecords.count() === 0, "Viewer plant edit affordance is visible");
  await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
  await page.locator("#sub-mode-indoor").click();
  await page.waitForLoadState("networkidle");
  assert(
    await page.locator("#indoor-tab-content .indoor-room-row button").count() === 0,
    "Viewer indoor room edit affordance is visible",
  );
  await assertViewerSettingsWriteUnavailable(page, profile);
  await openMap(page, profile);
  await visible(
    page.locator(`.plot[data-plot-id='${alpha.plot_id}']`),
    "viewer-owned read-only plot",
  );
  const editButton = page.locator("#edit-mode-btn");
  if (await editButton.count()) assert(await editButton.isDisabled(), "Viewer Edit control is enabled");
  if (profile === "mobile") {
    assert(await page.locator("#mobile-import-map-btn").isDisabled(), "Viewer mobile import control is enabled");
    assert(await page.locator("#mobile-fab").isDisabled(), "Viewer mobile quick action control is enabled");
    await page.locator("#mobile-map-layers-btn").click();
    await visible(page.locator("#map-layers-panel"), "viewer mobile map layers");
  }
  const mapObjectRow = page.locator("#map-objects-panel .map-object-row")
    .filter({ hasText: alpha.object_label });
  await visible(mapObjectRow, "viewer map-object inspection row");
  await mapObjectRow.locator(".map-object-row-main").click();
  const unitTile = page.locator("#map-objects-panel .map-object-unit").first();
  await visible(unitTile, "viewer nested-unit inspection tile");
  assert(!await unitTile.isDisabled(), "Viewer nested-unit inspection tile is disabled");
  await unitTile.click();
  const unitForm = page.locator("#map-objects-panel .map-object-unit-form");
  await visible(unitForm, "viewer nested-unit inspection form");
  const unitControls = unitForm.locator("input, select, button");
  assert(await unitControls.count() > 0, "Viewer nested-unit inspection form has no controls");
  for (let index = 0; index < await unitControls.count(); index += 1) {
    assert(await unitControls.nth(index).isDisabled(), "Viewer nested-unit inspection mutation control is enabled");
  }
  const writeControls = page.locator(
    "#map-objects-panel .map-object-create-row button, "
    + "#map-objects-panel .map-object-submit-btn, "
    + "#map-objects-panel .map-object-icon-btn, "
    + "#map-objects-panel .map-object-geometry-form input, "
    + "#map-objects-panel .map-object-geometry-form select",
  );
  const count = await writeControls.count();
  for (let index = 0; index < count; index += 1) {
    assert(await writeControls.nth(index).isDisabled(), "Viewer map-object write control is enabled");
  }
  assert(
    await page.locator(".map-object-interaction-surface").count() === 0,
    "Viewer received direct map manipulation surfaces",
  );
  await assertExpectedBrowserFailure(page, guarded.diagnostics, {
    label: `${profile} viewer admin-only map export denial`,
    method: "GET",
    path: "/api/plots/export",
  });
  if (directMutation) {
    await assertExpectedBrowserFailure(page, guarded.diagnostics, {
      body: { object_type: "patio", name: "Denied Phase 1 mutation" },
      label: "viewer map-object mutation denial",
      method: "POST",
      path: `/api/gardens/${alpha.id}/map-objects`,
    });
    await assertExpectedBrowserFailure(page, guarded.diagnostics, {
      body: { address: "Viewer denied settings mutation" },
      label: "viewer garden-settings mutation denial",
      method: "PATCH",
      path: `/api/gardens/${alpha.id}/settings`,
    });
    await assertExpectedBrowserFailure(page, guarded.diagnostics, {
      body: { name: "Viewer denied snapshot" },
      headers: { "x-action-reason": "viewer-snapshot-denial" },
      label: "viewer snapshot write denial",
      method: "POST",
      path: "/api/snapshots",
    });
    await assertExpectedBrowserFailure(page, guarded.diagnostics, {
      body: { plots: [], schema_version: 1 },
      headers: { "x-action-reason": "viewer-import-denial" },
      label: "viewer map import denial",
      method: "POST",
      path: "/api/plots/import",
    });
  }
}

async function ensureNotificationPanelOpen(page, profile) {
  const panel = page.locator("#notification-panel");
  if (await panel.isVisible().catch(() => false)) return panel;
  if (profile === "mobile") {
    await openMobileUtility(page);
    await page.locator("#mobile-notification-btn").click();
  } else {
    await page.locator("#notification-bell").click();
  }
  await visible(panel, "notification panel");
  return panel;
}

const GARDEN_RACE_SURFACES = [
  "plots",
  "layout",
  "map-objects",
  "plants",
  "saved-views",
  "indoor",
  "admin-settings",
  "notifications",
  "plot-alerts",
  "weather",
];

function weatherRaceTitle(garden, alpha) {
  return garden.id === alpha.id
    ? "Frost warning: -1°C expected"
    : "Frost warning: -9°C expected";
}

async function gardenSwitchIsPending(page) {
  return page.locator("body").evaluate((body) => body.classList.contains("garden-switch-pending"));
}

async function waitForGardenSwitchSettled(page, label) {
  await waitFor(
    () => page.locator("body").evaluate((body) => !body.classList.contains("garden-switch-pending")),
    label,
  );
}

async function dispatchGardenSelection(page, profile, garden) {
  const selector = profile === "mobile" ? "#mobile-garden-select" : "#garden-select";
  await page.evaluate(({ gardenId, selectSelector }) => {
    const select = document.querySelector(selectSelector);
    if (!(select instanceof HTMLSelectElement)) {
      throw new Error(`Missing controlled garden selector ${selectSelector}`);
    }
    select.value = String(gardenId);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }, { gardenId: garden.id, selectSelector: selector });
}

async function selectGardenForRace(page, profile, garden) {
  if (!await gardenSwitchIsPending(page)) {
    await selectGarden(page, profile, garden);
    return "physical";
  }
  await dispatchGardenSelection(page, profile, garden);
  return "controlled";
}

async function triggerGardenRaceSurface(page, profile, surface) {
  const selector = surface === "saved-views"
    ? "#saved-views-trigger"
    : (surface === "notifications"
      ? (profile === "mobile" ? "#mobile-notification-btn" : "#notification-bell")
      : null);
  if (!selector) return "automatic";
  const trigger = page.locator(selector);
  if (!await gardenSwitchIsPending(page) && await trigger.isVisible().catch(() => false)) {
    await trigger.click();
    return "physical";
  }
  // Switching deliberately makes interactive roots inert. This invokes the existing UI
  // handler only when a physical click is unavailable, so it still starts the real GET.
  await page.evaluate((triggerSelector) => {
    const trigger = document.querySelector(triggerSelector);
    if (!(trigger instanceof HTMLElement)) {
      throw new Error(`Missing pending race trigger ${triggerSelector}`);
    }
    trigger.click();
  }, selector);
  return "controlled";
}

async function openIndoor(page, profile) {
  await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
  await page.locator("#sub-mode-indoor").click();
}

async function openCare(page, profile) {
  await page.locator(profile === "mobile" ? "#mobile-tab-insights" : "#top-tab-insights").click();
  const care = page.locator(".insights-mode-toggle [data-sub-mode='care']").first();
  await visible(care, "care mode action");
  await care.click();
}

async function captureLayoutDomState(page) {
  return page.locator("#map-grid").evaluate((grid) => {
    const house = grid.querySelector("#house");
    const northLabels = ["map-edge-top", "map-edge-right", "map-edge-bottom", "map-edge-left"]
      .map((id) => document.getElementById(id)?.textContent ?? "");
    return {
      grid_columns: grid.style.getPropertyValue("--grid-cols"),
      grid_label: grid.dataset.gridLabel ?? null,
      grid_rows: grid.style.getPropertyValue("--grid-rows"),
      house: house instanceof HTMLElement ? {
        grid_column: house.style.gridColumn,
        grid_row: house.style.gridRow,
      } : null,
      north_degrees: grid.dataset.northDegrees ?? null,
      north_labels: northLabels,
    };
  });
}

async function assertLayoutDomState(page, expected, label) {
  const actual = await captureLayoutDomState(page);
  assert(actual.house, `${label} has no rendered house`);
  assert(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${label} changed after delayed A/B/A: ${JSON.stringify(actual)}`,
  );
}

async function applyAdminNorthDirection(page, profile, degrees, label) {
  await openAdminGarden(page, profile);
  const north = page.locator("#adm-map-north-input");
  await visible(north, `${label} north-direction control`);
  await north.fill(String(degrees));
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === "/api/layout-state"
  ));
  await page.locator("#adm-map-north-apply-btn").click();
  assert((await responsePromise).ok(), `${label} layout PATCH failed`);
  await waitFor(() => north.inputValue().then((value) => value === String(degrees)), label);
}

async function stageAlphaLayoutRaceState(page, profile) {
  await openAdminGarden(page, profile);
  const north = page.locator("#adm-map-north-input");
  await visible(north, "Alpha layout race north-direction control");
  const originalNorth = Number(await north.inputValue());
  assert(Number.isInteger(originalNorth), "Alpha layout race north direction is not an integer");
  const raceNorth = (originalNorth + 17) % 360;
  await applyAdminNorthDirection(page, profile, raceNorth, "Alpha layout race setup");
  await openMap(page, profile);
  return { original_north: originalNorth, race_north: raceNorth };
}

async function restoreAlphaLayoutRaceState(page, profile, state) {
  await applyAdminNorthDirection(page, profile, state.original_north, "Alpha layout race cleanup");
  await openMap(page, profile);
}

async function prepareGardenRaceSurface(page, fixture, profile, alpha, surface) {
  switch (surface) {
    case "plots":
    case "layout":
    case "map-objects":
      await openMap(page, profile);
      await visible(page.locator(`.plot[data-plot-id='${alpha.plot_id}']`), `${surface} Alpha map preparation`);
      if (surface === "map-objects") {
        await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
      }
      return;
    case "plants":
      await openPlants(page, profile);
      await visible(plantRecord(page, profile, alpha.plant_name), "Alpha plants race preparation");
      return;
    case "saved-views":
      await openPlants(page, profile);
      await page.locator("#saved-views-trigger").click();
      await visible(
        page.locator("#saved-views-dropdown .saved-views-item")
          .filter({ hasText: fixture.phase_one.saved_view.label }),
        "Alpha saved-view race preparation",
      );
      return;
    case "indoor": {
      await openIndoor(page, profile);
      const indoor = page.locator("#indoor-tab-content .indoor-card-wrapper")
        .filter({ hasText: fixture.phase_one.indoor.plant_name });
      await visible(indoor, "Alpha indoor race preparation");
      return;
    }
    case "admin-settings":
      await openAdminGarden(page, profile);
      await visible(page.locator("#adm-garden-name"), "Alpha settings race preparation");
      return;
    case "notifications":
      await openMap(page, profile);
      await ensureNotificationPanelOpen(page, profile);
      await visible(
        page.locator("#notification-panel").getByText(alpha.notification_title, { exact: true }),
        "Alpha notifications race preparation",
      );
      return;
    case "plot-alerts":
      await openMap(page, profile);
      await visible(
        page.locator(`.plot[data-plot-id='${alpha.plot_id}'] .plot-indicators`),
        "Alpha plot-alert race preparation",
      );
      return;
    case "weather":
      await openCare(page, profile);
      await visible(
        page.locator("#weather-dashboard")
          .getByText(weatherRaceTitle(alpha, alpha), { exact: true }),
        "Alpha weather race preparation",
      );
      return;
    default:
      throw new Error(`Unsupported delayed garden race surface ${surface}`);
  }
}

async function waitForAlphaSurfaceContentBeforeRelease(
  page,
  fixture,
  profile,
  alpha,
  surface,
  { alphaLayoutState = null } = {},
) {
  switch (surface) {
    case "plots":
      await visible(page.locator(`.plot[data-plot-id='${alpha.plot_id}']`), "Alpha plots before Beta release");
      return;
    case "layout":
      assert(alphaLayoutState, "Alpha layout state was not captured before release");
      await waitFor(async () => {
        const actual = await captureLayoutDomState(page);
        return JSON.stringify(actual) === JSON.stringify(alphaLayoutState);
      }, "Alpha layout state before Beta release");
      return;
    case "map-objects":
      await waitForMapObject(page, alpha.object_public_id, `Alpha ${surface} before Beta release`);
      return;
    case "plants":
      await visible(plantRecord(page, profile, alpha.plant_name), "Alpha plants before Beta release");
      return;
    case "saved-views":
      await visible(
        page.locator("#saved-views-dropdown .saved-views-item")
          .filter({ hasText: fixture.phase_one.saved_view.label }),
        "Alpha saved view before Beta release",
      );
      return;
    case "indoor":
      await visible(
        page.locator("#indoor-tab-content .indoor-card-wrapper")
          .filter({ hasText: fixture.phase_one.indoor.plant_name }),
        "Alpha indoor content before Beta release",
      );
      return;
    case "admin-settings":
      await visible(page.locator("#adm-garden-name"), "Alpha settings before Beta release");
      return;
    case "notifications":
      await visible(
        page.locator("#notification-panel").getByText(alpha.notification_title, { exact: true }),
        "Alpha notification before Beta release",
      );
      return;
    case "plot-alerts":
      await visible(
        page.locator(`.plot[data-plot-id='${alpha.plot_id}'] .plot-indicators`),
        "Alpha plot alert before Beta release",
      );
      return;
    case "weather":
      await visible(
        page.locator("#weather-dashboard")
          .getByText(weatherRaceTitle(alpha, alpha), { exact: true }),
        "Alpha weather before Beta release",
      );
      return;
    default:
      throw new Error(`Unsupported Alpha pending assertion ${surface}`);
  }
}

async function assertBetaSurfaceDidNotLandWhileHeld(page, profile, alpha, beta, surface) {
  switch (surface) {
    case "plots":
      assert(
        await page.locator(`.plot[data-plot-id='${beta.plot_id}']`).count() === 0,
        "Held Beta plots content landed before release",
      );
      return;
    case "map-objects":
      assert(
        await page.locator(`.map-object-label[data-object-id='${beta.object_public_id}']`).count() === 0,
        "Held Beta map-object content landed before release",
      );
      return;
    case "plants":
      assert(await plantRecord(page, profile, beta.plant_name).count() === 0, "Held Beta plants content landed before release");
      return;
    case "indoor":
      assert(
        await page.locator("#indoor-tab-content").getByText(beta.plant_name, { exact: true }).count() === 0,
        "Held Beta indoor content landed before release",
      );
      return;
    case "notifications":
      assert(
        await page.locator("#notification-panel")
          .getByText(beta.notification_title, { exact: true }).count() === 0,
        "Held Beta notification content landed before release",
      );
      return;
    case "plot-alerts":
      assert(
        await page.locator(`.plot[data-plot-id='${beta.plot_id}'] .plot-indicators`).count() === 0,
        "Held Beta plot-alert content landed before release",
      );
      return;
    case "weather":
      assert(
        await page.locator("#weather-dashboard")
          .getByText(weatherRaceTitle(beta, alpha), { exact: true }).count() === 0,
        "Held Beta weather content landed before release",
      );
      return;
    case "layout":
    case "saved-views":
    case "admin-settings":
      return;
    default:
      throw new Error(`Unsupported held Beta assertion ${surface}`);
  }
}

async function assertAlphaSurfaceAfterGardenRace(
  page,
  fixture,
  profile,
  alpha,
  beta,
  surface,
  { alphaLayoutState = null } = {},
) {
  switch (surface) {
    case "plots":
      await openMap(page, profile);
      await visible(page.locator(`.plot[data-plot-id='${alpha.plot_id}']`), "Alpha plots after delayed A/B/A");
      assert(
        await page.locator(`.plot[data-plot-id='${beta.plot_id}']`).count() === 0,
        `${profile} rendered stale Beta plots content after delayed A/B/A`,
      );
      return;
    case "layout":
      assert(alphaLayoutState, "Alpha layout state was not captured before delayed A/B/A");
      await assertLayoutDomState(page, alphaLayoutState, "Alpha layout DOM state");
      await visible(page.locator(`.plot[data-plot-id='${alpha.plot_id}']`), "Alpha layout plot after delayed A/B/A");
      return;
    case "map-objects":
      await openMap(page, profile);
      await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
      assert(
        await page.locator(`.map-object-label[data-object-id='${beta.object_public_id}']`).count() === 0,
        `${profile} rendered stale Beta map object after delayed A/B/A`,
      );
      return;
    case "plants":
      await openPlants(page, profile);
      await visible(plantRecord(page, profile, alpha.plant_name), "Alpha plants after delayed A/B/A");
      assert(await plantRecord(page, profile, beta.plant_name).count() === 0, `${profile} rendered stale Beta plants after delayed A/B/A`);
      return;
    case "saved-views":
      await visible(
        page.locator("#saved-views-dropdown .saved-views-item")
          .filter({ hasText: fixture.phase_one.saved_view.label }),
        "Alpha saved view retained after delayed A/B/A",
      );
      return;
    case "indoor":
      await openIndoor(page, profile);
      await visible(
        page.locator("#indoor-tab-content .indoor-card-wrapper")
          .filter({ hasText: fixture.phase_one.indoor.plant_name }),
        "Alpha indoor content after delayed A/B/A",
      );
      assert(
        await page.locator("#indoor-tab-content").getByText(beta.plant_name, { exact: true }).count() === 0,
        `${profile} rendered stale Beta indoor content after delayed A/B/A`,
      );
      return;
    case "admin-settings": {
      await openAdminGarden(page, profile);
      const name = page.locator("#adm-garden-name");
      await visible(name, "Alpha garden settings after delayed A/B/A");
      assert(await name.inputValue() === alpha.name, `${profile} rendered Beta garden settings after delayed A/B/A`);
      assert(await name.inputValue() !== beta.name, `${profile} retained Beta garden settings after delayed A/B/A`);
      return;
    }
    case "notifications":
      await openMap(page, profile);
      await ensureNotificationPanelOpen(page, profile);
      await visible(
        page.locator("#notification-panel").getByText(alpha.notification_title, { exact: true }),
        "Alpha notification after delayed A/B/A",
      );
      assert(
        await page.locator("#notification-panel")
          .getByText(beta.notification_title, { exact: true }).count() === 0,
        `${profile} rendered stale Beta notifications after delayed A/B/A`,
      );
      return;
    case "plot-alerts":
      await openMap(page, profile);
      await visible(
        page.locator(`.plot[data-plot-id='${alpha.plot_id}'] .plot-indicators`),
        "Alpha plot alerts after delayed A/B/A",
      );
      assert(
        await page.locator(`.plot[data-plot-id='${beta.plot_id}'] .plot-indicators`).count() === 0,
        `${profile} rendered stale Beta plot alerts after delayed A/B/A`,
      );
      return;
    case "weather":
      await openCare(page, profile);
      await visible(
        page.locator("#weather-dashboard")
          .getByText(weatherRaceTitle(alpha, alpha), { exact: true }),
        "Alpha weather after delayed A/B/A",
      );
      assert(
        await page.locator("#weather-dashboard")
          .getByText(weatherRaceTitle(beta, alpha), { exact: true }).count() === 0,
        `${profile} rendered stale Beta weather after delayed A/B/A`,
      );
      return;
    default:
      throw new Error(`Unsupported delayed garden race surface ${surface}`);
  }
}

async function stageDesktopAdminSettingsDraft(page) {
  const form = page.locator("#adm-garden-settings-form");
  const address = page.locator("#adm-garden-address");
  await visible(form, "Alpha garden settings draft form");
  await visible(address, "Alpha garden settings draft address");
  const baselineAddress = await address.inputValue();
  const draft = "Phase 1 desktop Alpha A/B/A unsaved settings draft";
  await address.fill(draft);
  assert(await address.inputValue() === draft, "Could not stage recognizable Alpha garden settings draft");
  return { baselineAddress, draft };
}

async function assertDraftDoesNotBelongToBeta(page, beta, draft) {
  const state = await page.evaluate(() => {
    const form = document.querySelector("#adm-garden-settings-form");
    const address = document.querySelector("#adm-garden-address");
    return {
      address: address instanceof HTMLInputElement ? address.value : null,
      gardenId: form instanceof HTMLElement ? form.dataset.gardenId ?? null : null,
    };
  });
  assert(
    !(state.gardenId === String(beta.id) && state.address === draft),
    "Beta garden settings received the Alpha unsaved draft",
  );
}

async function assertAlphaDraftRestored(page, alpha, draft) {
  await waitFor(async () => {
    const form = page.locator("#adm-garden-settings-form");
    const address = page.locator("#adm-garden-address");
    return (
      await form.getAttribute("data-garden-id") === String(alpha.id)
      && await address.inputValue() === draft
    );
  }, "Alpha garden settings draft restoration");
}

async function restoreDesktopAdminSettingsDraftBaseline(page, profile, draftState) {
  const address = page.locator("#adm-garden-address");
  await address.fill(draftState.baselineAddress);
  await openMap(page, profile);
  await openAdminGarden(page, profile);
  assert(
    await page.locator("#adm-garden-address").inputValue() === draftState.baselineAddress,
    "Alpha settings draft baseline did not return without persisting",
  );
}

async function exerciseDelayedGardenSwitch(
  page,
  context,
  diagnostics,
  fixture,
  profile,
  alpha,
  beta,
  { surfaces = GARDEN_RACE_SURFACES } = {},
) {
  assert(Array.isArray(surfaces) && surfaces.length > 0, "Delayed garden race requires at least one surface");
  assert(
    surfaces.every((surface) => GARDEN_RACE_SURFACES.includes(surface)),
    "Delayed garden race received an unsupported surface",
  );
  assert(new Set(surfaces).size === surfaces.length, "Delayed garden race repeated a surface");
  const delayedEvidence = {
    alpha_started_surfaces: [],
    beta_held_response_count: 0,
    beta_held_surfaces: [],
    per_surface: {},
  };
  for (const surface of surfaces) {
    const layoutRaceState = surface === "layout"
      ? await stageAlphaLayoutRaceState(page, profile)
      : null;
    await prepareGardenRaceSurface(page, fixture, profile, alpha, surface);
    const alphaLayoutState = surface === "layout" ? await captureLayoutDomState(page) : null;
    const draftState = profile === "desktop" && surface === "admin-settings"
      ? await stageDesktopAdminSettingsDraft(page)
      : null;
    const settingsPatches = [];
    const settingsPatchListener = (request) => {
      if (
        request.method() === "PATCH"
        && new URL(request.url()).pathname === `/api/gardens/${alpha.id}/settings`
      ) settingsPatches.push(request.url());
    };
    if (draftState) page.on("request", settingsPatchListener);
    try {
      const surfaceRace = await delayGardenSwitchResponses(
        page,
        context,
        diagnostics,
        { alpha, beta },
        surface,
        async (gate) => {
          await selectGarden(page, profile, beta);
          const betaTriggerMode = await triggerGardenRaceSurface(page, profile, surface);
          await gate.waitForBetaTarget(`${profile} held Beta ${surface}`);
          if (draftState) await assertDraftDoesNotBelongToBeta(page, beta, draftState.draft);

          // A held active-surface request retains the app's inert switch guard. Background
          // requests may settle first, in which case the normal selector remains usable.
          const alphaSelectionMode = await selectGardenForRace(page, profile, alpha);
          const alphaTriggerMode = await triggerGardenRaceSurface(page, profile, surface);
          await gate.waitForAlphaTarget(`${profile} started Alpha ${surface}`);
          await waitForAlphaSurfaceContentBeforeRelease(
            page, fixture, profile, alpha, surface, { alphaLayoutState },
          );
          if (draftState) await assertAlphaDraftRestored(page, alpha, draftState.draft);
          await assertBetaSurfaceDidNotLandWhileHeld(page, profile, alpha, beta, surface);
          gate.releaseBetaResponses();
          delayedEvidence.per_surface[surface] = {
            alpha_selection_mode: alphaSelectionMode,
            alpha_target_started: true,
            alpha_trigger_mode: alphaTriggerMode,
            beta_content_never_landed: true,
            beta_response_completion_count: 0,
            beta_target_held: true,
            beta_trigger_mode: betaTriggerMode,
          };
        },
      );
      await waitForGardenSwitchSettled(page, `${profile} ${surface} switch settlement`);
      await assertAlphaSurfaceAfterGardenRace(
        page, fixture, profile, alpha, beta, surface, { alphaLayoutState },
      );
      if (layoutRaceState) {
        await restoreAlphaLayoutRaceState(page, profile, layoutRaceState);
      }
      if (draftState) {
        await assertAlphaDraftRestored(page, alpha, draftState.draft);
        await restoreDesktopAdminSettingsDraftBaseline(page, profile, draftState);
        assert(settingsPatches.length === 0, "Unsaved Alpha settings draft cleanup persisted a PATCH");
        delayedEvidence.admin_settings_draft_isolation = {
          alpha_draft_restored_after_background_load: true,
          baseline_restored_without_persisting: true,
          beta_never_received_alpha_draft: true,
        };
      }
      delayedEvidence.alpha_started_surfaces.push(surface);
      delayedEvidence.beta_held_surfaces.push(surface);
      delayedEvidence.beta_held_response_count += surfaceRace.beta_held_response_count;
      assert(
        surfaceRace.beta_response_completion_count === surfaceRace.beta_held_response_count,
        `${profile} released Beta ${surface} response did not finish`,
      );
      delayedEvidence.per_surface[surface].beta_response_completion_count = (
        surfaceRace.beta_response_completion_count
      );
      delayedEvidence.per_surface[surface].beta_response_arrived = true;
    } finally {
      if (draftState) page.off("request", settingsPatchListener);
    }
  }
  delayedEvidence.alpha_started_surfaces.sort();
  delayedEvidence.beta_held_surfaces.sort();
  return delayedEvidence;
}

async function runOnboardingProfile(options, { password, profile, username }) {
  const { artifactDir, baseUrl, browser, devices, fixture } = options;
  const guarded = await createGuardedContext(
    browser, devices, profile, artifactDir, `${profile}-onboarding`,
  );
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page);
  const result = {
    assertions: { failed: [], passed: [], skipped: [] }, browser_profile: guarded.profile,
    checks: {}, failure: null, profile, requests: [], role: "onboarding", trace: null,
  };
  let status = "failed";
  let caughtError = null;
  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await authenticate(page, username, password);
    guarded.markAuthenticated();
    const overlay = page.locator(".onboarding-overlay");
    await visible(overlay, "real onboarding overlay");
    await overlay.locator(".onb-next").click();
    const name = profile === "mobile"
      ? "Phase 1 Mobile Onboarding Garden"
      : "Phase 1 Onboarding Garden";
    await overlay.locator("#onb-garden-name").fill(name);
    await overlay.locator(".onb-next").click();
    await overlay.locator("#onb-cols").fill("12");
    await overlay.locator("#onb-rows").fill("12");
    await overlay.locator(".onb-next").click();
    await overlay.locator("#onb-house-row").fill("2");
    await overlay.locator("#onb-house-col").fill("2");
    await overlay.locator("#onb-house-w").fill("3");
    await overlay.locator("#onb-house-h").fill("3");
    await overlay.locator(".onb-next").click();
    await overlay.locator("#onb-address").fill("Phase 1 onboarding address");
    await overlay.locator("#onb-lat").fill("59.91");
    await overlay.locator("#onb-lon").fill("10.75");
    await overlay.locator(".onb-next").click();
    await overlay.locator("#onb-zcode").fill("ERR");
    await overlay.locator("#onb-zname").fill("Overlapping validation zone");
    await overlay.locator("#onb-zsc").fill("2");
    await overlay.locator("#onb-zsr").fill("2");
    await overlay.locator("#onb-zec").fill("3");
    await overlay.locator("#onb-zer").fill("3");
    await overlay.locator("#onb-zone-add-btn").click();
    await overlay.locator(".onb-next").click();
    await visible(overlay.locator(".onb-validation--error"), "onboarding validation error");
    await overlay.locator(".onb-back").click();
    await overlay.locator(".onb-back").click();
    await overlay.locator(".onb-back").click();
    await overlay.locator(".onb-back").click();
    await overlay.locator(".onb-back").click();
    assert(await overlay.locator("#onb-garden-name").inputValue() === name, "Onboarding lost entered name after validation error");
    for (let index = 0; index < 4; index += 1) await overlay.locator(".onb-next").click();
    await overlay.locator(".onb-zone-remove").click();
    await overlay.locator(".onb-next").click();
    await overlay.locator(".onb-finish").click();
    await overlay.waitFor({ state: "detached", timeout: 20000 });
    await page.waitForLoadState("networkidle");
    await reloadAndAccountForAborts(page, guarded.diagnostics);
    await visible(page.locator("#map-grid"), "completed onboarding after reload");
    assert(await page.locator(".onboarding-overlay").count() === 0, "Onboarding reopened after completion reload");
    result.checks.onboarding_validation_recovery_complete = true;
    result.assertions.passed.push("A3-onboarding-validation-recovery-reload");
    result.structure = await assertPageStructure(page, `${profile} Phase 1 onboarding`, { enforceControlNames: false });
    assertDiagnosticsClean(guarded.diagnostics, `${profile} Phase 1 onboarding`);
    result.checks.browser_diagnostics = true;
    result.requests = recorder.records;
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "onboarding journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    result.diagnostics = guarded.diagnostics;
    try { result.trace = await guarded.close(status); }
    catch (error) { if (!caughtError) caughtError = error; }
  }
  return { error: caughtError, result };
}

async function runProfile({ artifactDir, baseUrl, browser, devices, fixture, password, profile, role, username }) {
  const alpha = fixtureGarden(fixture, "alpha");
  const beta = fixtureGarden(fixture, "beta");
  const roleAlpha = role === "viewer" ? viewerFixtureGarden(fixture, "alpha", alpha) : alpha;
  const roleBeta = role === "viewer" ? viewerFixtureGarden(fixture, "beta", beta) : beta;
  const guarded = await createGuardedContext(
    browser, devices, profile, artifactDir, `${profile}-${role}`,
  );
  const page = await guarded.context.newPage();
  const rawRequestFailures = [];
  page.on("requestfailed", (request) => {
    rawRequestFailures.push({
      failure: request.failure()?.errorText || "unknown",
      method: request.method(),
      path: new URL(request.url()).pathname,
    });
  });
  const recorder = createApiRecorder(page);
  const result = {
    assertions: { failed: [], passed: [], skipped: [] }, browser_profile: guarded.profile,
    checks: {}, failure: null, profile, requests: [], role, trace: null,
  };
  let status = "failed";
  let caughtError = null;
  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const profileData = await authenticate(page, username, password);
    guarded.markAuthenticated();
    assert(profileData.role === role, `Fixture role mismatch: expected ${role}`);
    result.browser_profile.user_agent = await page.evaluate(() => navigator.userAgent);
    result.browser_profile.max_touch_points = await page.evaluate(() => navigator.maxTouchPoints);
    result.browser_profile.viewport = page.viewportSize();
    await visible(page.locator("#map-grid"), `${profile} Map grid`);
    await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
    assert(!recorder.records.some((request) => request.method === "GET" && request.path === "/api/plants"), `${profile} map-first startup fetched /api/plants`);
    result.checks.map_first_without_plants = true;

    if (role === "viewer") {
      await assertViewerDenied(page, roleAlpha, guarded, profile, { directMutation: profile === "desktop" });
      result.checks.viewer_role_affordances_and_denials = true;
      result.checks.viewer_m1_m3_read_only_behavior = true;
      if (profile === "desktop") result.checks.viewer_a3_m4_write_denials = true;
      result.assertions.passed.push("A3-M1-M2-M3-M4-viewer-read-only-affordances-and-denials");
    } else {
      if (role !== "editor") await assertGlobalSearch(page, profile, alpha);
      if (role === "editor") {
        await assertEditorAffordances(page, guarded, profile);
        if (profile === "mobile") {
          await exerciseDiscoverableMobilePlotEdit(page, guarded.diagnostics);
          result.checks.mobile_editor_plot_edit_workflow = true;
        } else {
          await exercisePlantAndSavedView(
            page, guarded.diagnostics, fixture, alpha, profile, "editor desktop",
            { assignmentPlotId: "P1EDITORASSIGN" },
          );
          result.checks.saved_view_delete_confirmation = true;
          await exerciseEditorGardenSettingsAndLayoutWrite(
            page, guarded.diagnostics, alpha, profile,
          );
          result.checks.editor_settings_layout_reload_persistence = true;
          await exerciseEditorMapObjectWrite(page);
          result.checks.editor_m1_m3_supported_writes = true;
          result.checks.editor_a3_settings_and_m4_layout_write = true;
        }
        result.checks.editor_profile_write_affordances_and_admin_denial = true;
        result.assertions.passed.push("A3-M1-M2-M3-M4-editor-profile-real-write-and-admin-denial");
      } else if (profile === "desktop") {
        await exercisePlantAndSavedView(
          page, guarded.diagnostics, fixture, alpha, profile, "admin desktop",
        );
        result.checks.saved_view_delete_confirmation = true;
        await mutateIndoorPlant(page, guarded.diagnostics, fixture, profile, "admin desktop");
        result.checks.indoor_reload_persistence = true;
        await openMap(page, profile);
        await exerciseMapObjectEditor(page, guarded.diagnostics, alpha, { profile });
        await openMap(page, profile);
        const mapStateTransitions = await exerciseSnapshotsAndImport(
          page, guarded.diagnostics, password, alpha, beta,
        );
        result.checks.import_rejection_render_churn = mapStateTransitions;
        result.checks.desktop_admin_mutation_workflows = true;
        result.assertions.passed.push("M1-M2-M3-M4-desktop-admin-real-ui-mutations");
      } else {
        await openMap(page, profile);
        await assertMobileFocusReturn(page);
        await exercisePlantAndSavedView(
          page, guarded.diagnostics, fixture, alpha, profile, "admin mobile",
        );
        result.checks.saved_view_delete_confirmation = true;
        await mutateIndoorPlant(page, guarded.diagnostics, fixture, profile, "admin mobile");
        result.checks.indoor_reload_persistence = true;
        await updateGardenSettings(
          page, guarded.diagnostics, alpha, profile, "mobile admin",
        );
        result.checks.garden_settings_reload_persistence = true;
        await openMap(page, profile);
        await exerciseMapObjectEditor(page, guarded.diagnostics, alpha, {
          profile,
          useTouch: true,
        });
        await saveMobileSnapshot(page, fixture);
        await exerciseMobileMapImport(page, guarded.diagnostics, password);
        await submitMobileQuickAction(page, fixture, alpha);
        result.checks.mobile_supported_writes_and_focus_return = true;
        result.assertions.passed.push("M1-M2-M3-M4-mobile-real-ui-writes-and-focus-return");
      }
      if (role === "editor") result.assertions.passed.push("map-first");
    }
    if (["admin", "editor", "viewer"].includes(role)) {
      const isAdmin = role === "admin";
      const delayedGardenRace = await exerciseDelayedGardenSwitch(
        page,
        guarded.context,
        guarded.diagnostics,
        fixture,
        profile,
        roleAlpha,
        roleBeta,
        { surfaces: isAdmin ? GARDEN_RACE_SURFACES : ["plots"] },
      );
      if (isAdmin) {
        result.checks.delayed_surfaces = delayedGardenRace;
        result.assertions.passed.push(
          "map-first",
          "global-search-context",
          "delayed-garden-a-b-a-all-surfaces",
        );
      } else {
        result.checks.role_delayed_surfaces = delayedGardenRace;
        result.assertions.passed.push("role-delayed-garden-a-b-a-plots");
      }
      result.checks.role_cross_garden_response_isolation = true;
    }
    result.structure = await assertPageStructure(page, `${profile} Phase 1 ${role}`, { enforceControlNames: false });
    result.assertions.passed.push("no-duplicate-ids", "no-horizontal-overflow");
    assert(result.assertions.skipped.length === 0, `${profile} ${role} left Phase 1 assertions skipped`);
    if (guarded.diagnostics.requestFailures.length > 0) {
      for (const failure of rawRequestFailures) {
        const key = `failure_${failure.method}_${failure.path}_${failure.failure}`
          .toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 80);
        result.checks[key] = true;
      }
    }
    assertDiagnosticsClean(guarded.diagnostics, `${profile} Phase 1 ${role}`);
    result.checks.browser_diagnostics = true;
    result.requests = recorder.records;
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    result.diagnostics = guarded.diagnostics;
    try { result.trace = await guarded.close(status); }
    catch (error) { if (!caughtError) caughtError = error; }
  }
  return { error: caughtError, result };
}

async function runGardenMapPlants(options, profileRunner = runProfile) {
  const runs = [
    { profile: "desktop", role: "admin", username: options.username, password: options.password },
    { profile: "mobile", role: "admin", username: options.username, password: options.password },
    { profile: "desktop", role: "editor", username: options.fixture.roles.editor, password: EDITOR_PASSWORD },
    { profile: "mobile", role: "editor", username: options.fixture.roles.editor, password: EDITOR_PASSWORD },
    { profile: "desktop", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
    { profile: "mobile", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
  ];
  const onboardingRuns = [
    {
      password: ONBOARDING_PASSWORD,
      profile: "desktop",
      username: options.fixture.phase_one.onboarding.desktop_username,
    },
    {
      password: MOBILE_ONBOARDING_PASSWORD,
      profile: "mobile",
      username: options.fixture.phase_one.onboarding.mobile_username,
    },
  ];
  for (const onboardingRun of onboardingRuns) {
    const onboarding = await runOnboardingProfile(options, onboardingRun);
    if (options.onProfile) options.onProfile(onboarding.result);
    if (onboarding.error) throw onboarding.error;
  }
  for (const run of runs) {
    const outcome = await profileRunner({ ...options, ...run });
    if (options.onProfile) options.onProfile(outcome.result);
    if (outcome.error) throw outcome.error;
  }
}

module.exports = { runGardenMapPlants };
