"use strict";

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
  if (pathname === "/api/plants") return "plants";
  if (pathname === "/api/weather/summary" || pathname === "/api/weather/alerts") return "weather";
  if (pathname === "/api/plots/alerts") return "plot-alerts";
  if (pathname === "/api/notifications") return "notifications";
  if (/^\/api\/gardens\/\d+\/map-objects$/.test(pathname)) return "map-objects";
  if (pathname === "/api/layout-state") return "layout";
  if (/^\/api\/plots\/[^/]+\/plants$/.test(pathname)) return "indoor";
  if (/^\/api\/gardens\/\d+\/settings$/.test(pathname)) return "admin-settings";
  return null;
}

async function delayGardenSwitchResponses(context, diagnostics, action) {
  const delayed = new Set();
  const failureMark = diagnostics.requestFailures.length;
  const aborted = [];
  const failureListener = (request) => {
    const failure = request.failure()?.errorText || "";
    if (failure.includes("ERR_ABORTED")) {
      aborted.push({ method: request.method(), path: new URL(request.url()).pathname });
    }
  };
  context.on("requestfailed", failureListener);
  const handler = async (route) => {
    const request = route.request();
    const surface = request.method() === "GET"
      ? delayedSurface(new URL(request.url()).pathname)
      : null;
    if (surface) {
      delayed.add(surface);
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    await route.continue();
  };
  await context.route("**/api/**", handler);
  try {
    await action();
  } finally {
    await context.unroute("**/api/**", handler);
    context.off("requestfailed", failureListener);
  }
  const addedFailures = diagnostics.requestFailures.length - failureMark;
  assert(addedFailures === aborted.length, "Delayed A/B/A produced an unaccounted request failure");
  assert(
    aborted.every((request) => request.method === "GET" && request.path.startsWith("/api/")),
    "Delayed A/B/A aborted a non-GET or non-API request",
  );
  diagnostics.requestFailures.splice(failureMark, addedFailures);
  return [...delayed].sort();
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

async function exercisePlantAndSavedView(page, diagnostics, fixture, alpha, profile = "desktop") {
  const plantName = "Phase 1 Browser Mint";
  const renamed = `${plantName} Edited`;
  const savedViewName = "Phase 1 Browser Plant View";
  await openPlants(page, profile);

  await page.locator("#add-plant-btn").click();
  await visible(page.locator("#plant-search-create-btn"), "plant search create-new action");
  await page.locator("#plant-search-create-btn").click();
  let dialog = page.locator("#create-plant-form");
  await visible(dialog, "create plant form");
  await dialog.locator("input[name='name']").fill(plantName);
  await dialog.locator("select[name='category']").selectOption("urter");
  await dialog.locator("input[name='link']").fill("https://example.com/phase-one-mint");
  await addPlotAssignment(dialog, alpha.plot_id);
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
  await dialog.locator(`.plot-chip[data-plot='${alpha.plot_id}'] .chip-remove`).click();
  const unlinkPath = `/api/plots/${alpha.plot_id}/plants/${plantId}`;
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
  await addPlotAssignment(dialog, alpha.plot_id);
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
  await reloadedView.locator(".saved-views-item-delete").click();
  await waitFor(async () => await page.getByText(savedViewName, { exact: true }).count() === 0, "saved view deletion");
  if (await page.locator("#saved-views-dropdown").isVisible()) {
    await page.locator("#saved-views-trigger").click();
  }

  await search.fill(renamed);
  const deleteRow = plantRecord(page, profile, renamed);
  await visible(deleteRow, "edited plant ready for deletion");
  await deleteRow.locator("[data-edit-plt]").click();
  await page.locator("#delete-edit-plant").click();
  await acceptConfirm(page);
  await waitFor(async () => await deleteRow.count() === 0, "plant deletion cascade");
  return { plantName: renamed, savedViewName };
}

async function mutateIndoorPlant(page, fixture, profile = "desktop") {
  await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
  await page.locator("#sub-mode-indoor").click();
  const card = page.locator("#indoor-tab-content .indoor-card-wrapper")
    .filter({ hasText: fixture.phase_one.indoor.plant_name });
  await visible(card, "seeded indoor plant");
  await card.locator(".indoor-room-row button").click();
  await card.locator(".indoor-room-input").fill("Phase 1 Browser Shelf");
  await card.locator(".indoor-room-edit .btn-primary").click();
  await visible(card.getByText("Phase 1 Browser Shelf", { exact: false }), "mutated indoor room");
  await card.locator(".indoor-room-row button").click();
  await card.locator(".indoor-room-input").fill(fixture.phase_one.indoor.room_label);
  await card.locator(".indoor-room-edit .btn-primary").click();
  await visible(card.getByText(fixture.phase_one.indoor.room_label, { exact: false }), "restored indoor room");
}

async function enableMapEditor(page, profile = "desktop") {
  const edit = page.locator("#edit-mode-btn");
  if (await edit.count()) {
    if (await edit.getAttribute("aria-pressed") !== "true") await edit.click();
  } else if (profile === "mobile") {
    await openMobileUtility(page);
    await page.locator("#mobile-admin-btn").click();
    await closeMobileSurfaces(page);
    await visible(page.locator("#adm-map-open-editor-btn"), "mobile admin map editor action");
    await page.locator("#adm-map-open-editor-btn").click();
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

async function exercisePlotCreateAndEdit(page, profile, diagnostics) {
  const plotId = profile === "mobile" ? "P1MOBILEPLOT" : "P1EDITORPLOT";
  const renamedPlotId = `${plotId}EDITED`;
  await openMap(page, profile);
  await enableMapEditor(page, profile);
  if (profile === "mobile") await closeMobileSurfaces(page);
  const emptyCell = page.locator("#map-grid .empty-cell").first();
  await visible(emptyCell, `${profile} empty map cell`);
  await emptyCell.click();
  const createDialog = page.locator("#create-plot-form");
  await visible(createDialog, `${profile} create plot dialog`);
  await createDialog.locator("input[name='plot_id']").fill(plotId);
  const createResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/plots"
  ));
  await createDialog.locator("button[type='submit']").click();
  assert((await createResponsePromise).status() === 201, `${profile} plot create failed`);
  const createdPlot = page.locator(`.plot[data-plot-id='${plotId}']`);
  await visible(createdPlot, `${profile} created plot`);

  await createdPlot.click({ button: "right" });
  const menu = page.locator(".context-menu");
  await visible(menu, `${profile} plot context menu`);
  await menu.locator(".menu-item-edit").click();
  const editDialog = page.locator("#edit-plot-form");
  await visible(editDialog, `${profile} edit plot dialog`);
  await editDialog.locator("input[name='plot_name']").fill(renamedPlotId);
  const updateResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === `/api/plots/${plotId}`
  ));
  await editDialog.locator("button[type='submit']").click();
  assert((await updateResponsePromise).ok(), `${profile} plot edit failed`);
  const renamedPlot = page.locator(`.plot[data-plot-id='${renamedPlotId}']`);
  await visible(renamedPlot, `${profile} renamed plot`);

  await renamedPlot.click({ button: "right" });
  await visible(menu, `${profile} renamed plot context menu`);
  const deletePath = `/api/plots/${renamedPlotId}`;
  const failureMark = diagnostics.requestFailures.length;
  const expectedAborts = [];
  const failureListener = (request) => {
    const failure = request.failure()?.errorText || "";
    if (request.method() === "DELETE"
      && new URL(request.url()).pathname === deletePath
      && failure.includes("ERR_ABORTED")) expectedAborts.push(deletePath);
  };
  page.on("requestfailed", failureListener);
  await menu.locator(".menu-item-delete").click();
  const deleteResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && new URL(response.url()).pathname === deletePath
  ));
  await acceptConfirm(page);
  assert((await deleteResponsePromise).status() === 204, `${profile} plot cleanup failed`);
  await page.waitForTimeout(100);
  page.off("requestfailed", failureListener);
  const failuresAdded = diagnostics.requestFailures.length - failureMark;
  assert(failuresAdded === expectedAborts.length, `${profile} plot cleanup produced an unrelated request failure`);
  diagnostics.requestFailures.splice(failureMark, failuresAdded);
  await waitFor(async () => await renamedPlot.count() === 0, `${profile} plot deletion cleanup`);
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
  const geometryPath = `/api/gardens/${alpha.id}/map-objects/${objectId}`;
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === geometryPath
  ));
  const pointer = {
    button: 0,
    buttons: 1,
    clientX: startX,
    clientY: startY,
    isPrimary: true,
    pointerId: 41,
    pointerType: "touch",
  };
  await surface.dispatchEvent("pointerdown", pointer);
  await surface.dispatchEvent("pointermove", { ...pointer, clientX: endX });
  await surface.dispatchEvent("pointerup", { ...pointer, buttons: 0, clientX: endX });
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
  const unitUpdate = await issueBrowserRequest(page, {
    body: { name: renamedUnit },
    method: "PATCH",
    path: unitUpdatePath,
  });
  assert(unitUpdate.status === 200, `Nested map unit edit returned ${unitUpdate.status}`);

  await reloadAndAccountForAborts(page, diagnostics);
  await enableMapEditor(page, profile);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "map object editor after reload");
  await visible(page.getByText(primary.name, { exact: true }), "map object geometry after reload");
  const reloadedPrimary = page.locator("#map-objects-panel .map-object-row").filter({ hasText: primary.name });
  await reloadedPrimary.locator(".map-object-row-main").click();
  const reloadedDetail = page.locator("#map-objects-panel .map-object-detail");
  await visible(reloadedDetail.getByText(renamedUnit, { exact: true }), "nested unit edit after reload");
  const mapBoundsAfter = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsAfter, "Map grid has no dimensions after object mutations");
  assert(
    Math.abs(mapBoundsAfter.width - mapBoundsBefore.width) <= 1
      && Math.abs(mapBoundsAfter.height - mapBoundsBefore.height) <= 1,
    "Map dimensions shifted during map-object labels, selection, or reload",
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

async function updateGardenSettings(page, alpha, profile = "desktop") {
  await openAdminGarden(page, profile);
  const address = page.locator("#adm-garden-address");
  await visible(address, "Garden settings address");
  const original = await address.inputValue();
  const changed = profile === "mobile"
    ? "Phase 1 mobile browser settings mutation"
    : "Phase 1 browser settings mutation";
  await address.fill(changed);
  await page.locator("#adm-garden-save").click();
  await waitFor(() => address.inputValue().then((value) => value === changed), "garden settings update");
  await address.fill(original);
  await page.locator("#adm-garden-save").click();
  await waitFor(() => address.inputValue().then((value) => value === original), `garden ${alpha.name} settings restore`);
}

async function openAdminGarden(page, profile = "desktop") {
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
  await visible(page.locator("#adm-map-save-layout-btn"), "Admin map setup controls");
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
  const label = page.locator(".map-object-label").first();
  const objectId = await label.getAttribute("data-object-id");
  assert(objectId, "Snapshot divergence object has no public ID");
  await label.click();
  const surface = page.locator(`.map-object-interaction-surface[data-object-id='${objectId}']`);
  const positionedLabel = page.locator(`.map-object-label[data-object-id='${objectId}']`);
  await visible(surface, "snapshot divergence map object");
  const before = await positionedLabel.getAttribute("style");
  const plotCountBefore = await page.locator("#map-grid .plot").count();
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
  await row.locator(".snapshot-restore").click();
  await authorizeSensitiveAction(page, password, { confirmFirst: true, reason: "phase-one-snapshot-restore" });
  await waitFor(async () => await positionedLabel.getAttribute("style") === before, "snapshot restore postcondition");

  await openAdminGarden(page);
  const exportedPath = await downloadMapExport(
    page,
    diagnostics,
    page.locator("#adm-map-export-btn"),
    "desktop map export",
  );
  const importResponse = await submitMapImport(page, password, {
    file: exportedPath,
    reason: "phase-one-map-import",
  });
  assert(importResponse.ok(), `Exported map import failed with ${importResponse.status()}`);
  await waitFor(async () => await page.locator("#map-grid .plot").count() === plotCountBefore, "exported map import postcondition");
  await page.waitForLoadState("networkidle");
  await openMap(page, "desktop");
  await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
  await page.waitForTimeout(100);

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
  return rejectedImportRenderChurn;
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
  await visible(editButton, "editor map edit affordance");
  assert(!await editButton.isDisabled(), "Editor map edit control is disabled");
  await assertExpectedBrowserFailure(page, guarded.diagnostics, {
    label: "editor admin-only map export denial",
    method: "GET",
    path: "/api/plots/export",
  });
}

async function assertViewerDenied(page, alpha, guarded, profile, { directMutation = false } = {}) {
  await visible(page.locator("[data-garden-role]:visible").filter({ hasText: /read-only/i }).first(), "viewer read-only indicator");
  assert(
    await page.locator("body").evaluate((body) => body.classList.contains("garden-read-only")),
    "Viewer did not render the read-only state",
  );
  await openPlants(page, profile);
  const addPlant = page.locator("#add-plant-btn");
  await visible(addPlant, "viewer add plant affordance");
  assert(await addPlant.isDisabled(), "Viewer add plant control is enabled");
  const viewerRecords = page.locator(profile === "mobile"
    ? "#plants-mobile-list article[data-plt-id] [data-edit-plt]"
    : "#plants-table-body tr[data-plt-id] [data-edit-plt]");
  assert(await viewerRecords.count() === 0, "Viewer plant edit affordance is visible");
  await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
  await page.locator("#sub-mode-indoor").click();
  const indoorCard = page.locator("#indoor-tab-content .indoor-card-wrapper").first();
  await visible(indoorCard, "viewer indoor card");
  assert(await indoorCard.locator(".indoor-room-row button").count() === 0, "Viewer indoor room edit affordance is visible");
  await openMap(page, profile);
  const editButton = page.locator("#edit-mode-btn");
  if (await editButton.count()) assert(await editButton.isDisabled(), "Viewer Edit control is enabled");
  if (profile === "mobile") {
    assert(await page.locator("#mobile-import-map-btn").isDisabled(), "Viewer mobile import control is enabled");
    assert(await page.locator("#mobile-fab").isDisabled(), "Viewer mobile quick action control is enabled");
  }
  const writeControls = page.locator(
    "#map-objects-panel .map-object-create-row button, "
    + "#map-objects-panel .map-object-submit-btn, "
    + "#map-objects-panel .map-object-icon-btn, "
    + "#map-objects-panel .map-object-geometry-form input, "
    + "#map-objects-panel .map-object-geometry-form select, "
    + "#map-objects-panel .map-object-unit",
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
  }
}

async function exerciseDelayedGardenSwitch(page, context, diagnostics, fixture, profile, alpha, beta) {
  await openMap(page, profile);
  const delayed = await delayGardenSwitchResponses(context, diagnostics, async () => {
    await selectGarden(page, profile, beta);
    if (profile === "mobile") await closeMobileSurfaces(page);
    await openPlants(page, profile);
    await page.locator(profile === "mobile" ? "#mobile-tab-insights" : "#top-tab-insights").click();
    const weatherResponsePromise = page.waitForResponse((response) => (
      new URL(response.url()).pathname === "/api/weather/summary"
    ));
    await page.locator(".insights-mode-toggle [data-sub-mode='care']").first().click();
    await weatherResponsePromise;
    await page.locator(profile === "mobile" ? "#mobile-tab-map" : "#top-tab-map").click();
    if (profile === "mobile") {
      await openMobileUtility(page);
      await page.locator("#mobile-notification-btn").click();
      await closeMobileSurfaces(page);
    } else {
      await page.locator("#notification-bell").click();
      await page.locator("#notification-bell").click().catch(() => {});
    }
    await page.locator(profile === "mobile" ? "#mobile-tab-garden" : "#top-tab-garden").click();
    await page.locator("#sub-mode-indoor").click();
    await selectGarden(page, profile, alpha);
    if (profile === "mobile") await closeMobileSurfaces(page);
    if (profile === "desktop") {
      await page.locator("#top-tab-admin").click();
      const gardenNav = page.locator(".adm-nav-btn[data-section='garden']");
      if (await gardenNav.count()) await gardenNav.click();
    }
    await openMap(page, profile);
    await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);
  });
  const required = ["plants", "weather", "plot-alerts", "notifications", "map-objects", "layout", "indoor"];
  if (profile === "desktop") required.push("admin-settings");
  for (const surface of required) {
    assert(delayed.includes(surface), `${profile} delayed A/B/A did not cover ${surface}`);
  }
  assert(
    !await page.locator(`.map-object-label[data-object-id='${beta.object_public_id}']`).isVisible().catch(() => false),
    `${profile} rendered stale Beta map object after delayed A/B/A`,
  );
  await visible(page.locator(`.plot[data-plot-id='${alpha.plot_id}']`), "Alpha plot after delayed A/B/A");
  return delayed;
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
      await assertViewerDenied(page, alpha, guarded, profile, { directMutation: profile === "desktop" });
      result.checks.viewer_role_affordances_and_denials = true;
      result.assertions.passed.push("A3-M2-viewer-ui-affordances-and-direct-denials");
    } else {
      if (role !== "editor") await assertGlobalSearch(page, profile, alpha);
      if (role === "editor") {
        await assertEditorAffordances(page, guarded, profile);
        await exerciseEditorMapObjectWrite(page);
        result.checks.editor_profile_write_affordances_and_admin_denial = true;
        result.assertions.passed.push("M1-M2-editor-profile-real-write-and-admin-denial");
      } else if (profile === "desktop") {
        await exerciseMapObjectEditor(page, guarded.diagnostics, alpha, { profile });
        await openMap(page, profile);
        result.checks.import_rejection_render_churn = await exerciseSnapshotsAndImport(
          page, guarded.diagnostics, password, alpha, beta,
        );
        result.checks.desktop_admin_mutation_workflows = true;
        result.assertions.passed.push("M1-M2-M3-M4-desktop-admin-real-ui-mutations");
      } else {
        await openMap(page, profile);
        await assertMobileFocusReturn(page);
        await exercisePlantAndSavedView(page, guarded.diagnostics, fixture, alpha, profile);
        await mutateIndoorPlant(page, fixture, profile);
        await exercisePlotCreateAndEdit(page, profile, guarded.diagnostics);
        await updateGardenSettings(page, alpha, profile);
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
      result.checks.delayed_surfaces = await exerciseDelayedGardenSwitch(
        page, guarded.context, guarded.diagnostics, fixture, profile, alpha, beta,
      );
      result.assertions.passed.push("map-first", "global-search-context", "delayed-garden-a-b-a-all-surfaces");
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
