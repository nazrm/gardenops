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
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const MAP_OBJECT_TYPES = [
  "patio", "terrace", "greenhouse", "shed", "pond", "path", "bed", "other",
];

function fixtureGarden(fixture, key) {
  const garden = fixture.gardens?.[key];
  assert(garden && Number.isInteger(garden.id), `Missing ${key} fixture garden`);
  return garden;
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
    const closeButton = page.locator(
      "#mobile-map-layers-close-btn:visible, #mobile-map-layouts-close-btn:visible, "
      + "#mobile-map-tools-close-btn:visible, #mobile-map-shade-close-btn:visible",
    ).first();
    if (await closeButton.count()) await closeButton.click().catch(() => {});
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

async function exercisePlantAndSavedView(page, diagnostics, fixture, alpha) {
  const plantName = "Phase 1 Browser Mint";
  const renamed = `${plantName} Edited`;
  const savedViewName = "Phase 1 Browser Plant View";
  await openPlants(page);

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
  const row = page.locator("#plants-table-body tr[data-plt-id]").filter({ hasText: plantName });
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
  const editedRow = page.locator("#plants-table-body tr[data-plt-id]").filter({ hasText: renamed });
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
  await openPlants(page);
  await page.locator("#saved-views-trigger").click();
  const reloadedView = page.locator("#saved-views-dropdown .saved-views-item").filter({ hasText: savedViewName });
  await visible(reloadedView, "saved view after reload");
  await reloadedView.locator(".saved-views-item-delete").click();
  await waitFor(async () => await page.getByText(savedViewName, { exact: true }).count() === 0, "saved view deletion");
  if (await page.locator("#saved-views-dropdown").isVisible()) {
    await page.locator("#saved-views-trigger").click();
  }

  await search.fill(renamed);
  const deleteRow = page.locator("#plants-table-body tr[data-plt-id]").filter({ hasText: renamed });
  await visible(deleteRow, "edited plant ready for deletion");
  await deleteRow.locator("[data-edit-plt]").click();
  await page.locator("#delete-edit-plant").click();
  await acceptConfirm(page);
  await waitFor(async () => await deleteRow.count() === 0, "plant deletion cascade");
  return { plantName: renamed, savedViewName };
}

async function mutateIndoorPlant(page, fixture) {
  await page.locator("#top-tab-garden").click();
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

async function enableMapEditor(page) {
  const edit = page.locator("#edit-mode-btn");
  if (await edit.count()) {
    await edit.click();
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

async function exerciseMapObjectEditor(page, diagnostics, alpha) {
  await enableMapEditor(page);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "map object category editor");
  const mapBoundsBefore = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsBefore, "Map grid has no initial dimensions");
  const created = [];
  for (const [index, type] of MAP_OBJECT_TYPES.entries()) {
    created.push(await createMapObject(page, type, index));
  }

  const primary = created[0];
  const primaryId = await primary.row.getAttribute("data-object-id");
  assert(primaryId, "Created primary map object has no public ID");
  await primary.row.locator(".map-object-row-main").click();
  const detail = page.locator("#map-objects-panel .map-object-detail");
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

  if (!await detail.isVisible()) {
    await page.locator("#map-objects-panel .map-object-row").filter({ hasText: primary.name })
      .locator(".map-object-row-main").click();
  }
  await visible(detail, "primary map object details");
  await detail.locator(".map-object-create-row button").first().click();
  const unit = detail.locator(".map-object-unit").last();
  await visible(unit, "created nested map unit");
  await unit.click();
  await acceptConfirm(page);
  await waitFor(async () => await detail.locator(".map-object-unit").count() === 0, "nested unit deletion");

  await reloadAndAccountForAborts(page, diagnostics);
  await enableMapEditor(page);
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "map object editor after reload");
  await visible(page.getByText(primary.name, { exact: true }), "map object geometry after reload");
  const mapBoundsAfter = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsAfter, "Map grid has no dimensions after object mutations");
  assert(
    mapBoundsAfter.width === mapBoundsBefore.width && mapBoundsAfter.height === mapBoundsBefore.height,
    "Map dimensions shifted during map-object labels, selection, or reload",
  );
  for (const item of created) {
    const row = page.locator("#map-objects-panel .map-object-row").filter({ hasText: item.name });
    await deleteMapObjectRow(page, row, item.name);
  }
  await waitFor(async () => await page.getByText(primary.name, { exact: true }).count() === 0, "map object UI cascade");
}

async function updateGardenSettings(page, alpha) {
  await page.locator("#top-tab-admin").click();
  const gardenNav = page.locator(".adm-nav-btn[data-section='garden']");
  await visible(gardenNav, "Garden settings navigation");
  await gardenNav.click();
  const address = page.locator("#adm-garden-address");
  await visible(address, "Garden settings address");
  const original = await address.inputValue();
  await address.fill("Phase 1 browser settings mutation");
  await page.locator("#adm-garden-save").click();
  await waitFor(() => address.inputValue().then((value) => value === "Phase 1 browser settings mutation"), "garden settings update");
  await address.fill(original);
  await page.locator("#adm-garden-save").click();
  await waitFor(() => address.inputValue().then((value) => value === original), `garden ${alpha.name} settings restore`);
}

async function openAdminGarden(page) {
  await page.locator("#top-tab-admin").click();
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

async function exerciseMobileMapObject(page) {
  await openMobileUtility(page);
  await page.locator("#mobile-admin-btn").click();
  await closeMobileSurfaces(page);
  await visible(page.locator("#adm-map-open-editor-btn"), "mobile admin map editor action");
  await page.locator("#adm-map-open-editor-btn").click();
  await visible(page.locator("#map-objects-panel .map-object-custom-form"), "mobile map object editor");
  const mapBoundsBefore = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsBefore, "Mobile map grid has no initial dimensions");
  const created = await createMapObject(page, "patio", 9);
  await deleteMapObjectRow(page, created.row, created.name);
  const mapBoundsAfter = await page.locator("#map-grid").boundingBox();
  assert(mapBoundsAfter, "Mobile map grid has no dimensions after object mutation");
  assert(
    mapBoundsAfter.width === mapBoundsBefore.width && mapBoundsAfter.height === mapBoundsBefore.height,
    "Mobile map dimensions shifted during map-object mutation",
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

async function exerciseSnapshotsAndImport(page, diagnostics, password) {
  const snapshotName = "Phase 1 Restore Snapshot";
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
  const downloadFailureMark = diagnostics.requestFailures.length;
  const downloadAborts = [];
  const downloadFailureListener = (request) => {
    const path = new URL(request.url()).pathname;
    const failure = request.failure()?.errorText || "";
    if (request.method() === "GET" && path === "/api/plots/export" && failure.includes("ERR_ABORTED")) {
      downloadAborts.push(path);
    }
  };
  page.on("requestfailed", downloadFailureListener);
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#adm-map-export-btn").click();
  const download = await downloadPromise;
  const exportedPath = await download.path();
  await page.waitForTimeout(100);
  page.off("requestfailed", downloadFailureListener);
  const downloadFailuresAdded = diagnostics.requestFailures.length - downloadFailureMark;
  assert(downloadFailuresAdded === downloadAborts.length, "Map download produced an unrelated request failure");
  diagnostics.requestFailures.splice(downloadFailureMark, downloadFailuresAdded);
  assert(exportedPath, "Map export did not produce a browser download");
  const importResponsePromise = page.waitForResponse((response) => (
    response.url().includes("/api/plots/import") && response.request().method() === "POST"
  ));
  await page.locator("#import-map-input").setInputFiles(exportedPath);
  await authorizeSensitiveAction(page, password, { confirmFirst: true, reason: "phase-one-map-import" });
  const importResponse = await importResponsePromise;
  assert(importResponse.ok(), `Exported map import failed with ${importResponse.status()}`);
  await waitFor(async () => await page.locator("#map-grid .plot").count() === plotCountBefore, "exported map import postcondition");

  const labelsBeforeMalformed = await page.locator(".map-object-label").allTextContents();
  const malformedHttpMark = diagnostics.httpErrors.length;
  const malformedConsoleMark = diagnostics.consoleErrors.length;
  const malformedResponsePromise = page.waitForResponse((response) => (
    response.url().includes("/api/plots/import") && response.request().method() === "POST"
  ));
  await page.locator("#import-map-input").setInputFiles({
    name: "wrong-version.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"schema_version":999,"plots":[]}'),
  });
  await authorizeSensitiveAction(page, password, { confirmFirst: true, reason: "phase-one-malformed-map-import" });
  const malformedResponse = await malformedResponsePromise;
  assert(malformedResponse.status() === 422, `Malformed map import returned ${malformedResponse.status()}`);
  await waitFor(async () => await page.locator(".toast-error, .toast.error").count() > 0, "malformed import error");
  assert(diagnostics.httpErrors.length === malformedHttpMark + 1, "Malformed import HTTP diagnostic was not isolated");
  assert(diagnostics.consoleErrors.length === malformedConsoleMark + 1, "Malformed import console diagnostic was not isolated");
  diagnostics.httpErrors.splice(malformedHttpMark, 1);
  diagnostics.consoleErrors.splice(malformedConsoleMark, 1);
  assert(
    JSON.stringify(await page.locator(".map-object-label").allTextContents()) === JSON.stringify(labelsBeforeMalformed),
    "Malformed import produced partial visible map-object state",
  );

  await openAdminGarden(page);
  await page.locator("#adm-map-layouts-btn").click();
  row = page.locator("#map-layouts-dialog .snapshot-row").filter({ hasText: snapshotName });
  await row.locator(".snapshot-delete").click();
  await authorizeSensitiveAction(page, password, { reason: "phase-one-snapshot-delete" });
  await waitFor(async () => await page.getByText(snapshotName, { exact: true }).count() === 0, "snapshot deletion");
  await page.keyboard.press("Escape");
}

async function assertViewerDenied(page, alpha, guarded) {
  await visible(page.locator("[data-garden-role]:visible").filter({ hasText: /read-only/i }).first(), "viewer read-only indicator");
  const editButton = page.locator("#edit-mode-btn");
  if (await editButton.count()) assert(await editButton.isDisabled(), "Viewer Edit control is enabled");
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
  const consoleMark = guarded.diagnostics.consoleErrors.length;
  const response = await page.evaluate(async (gardenId) => {
    const csrf = document.cookie.split("; ")
      .find((part) => part.startsWith("gardenops_csrf="))?.slice("gardenops_csrf=".length) || "";
    return (await fetch(`/api/gardens/${gardenId}/map-objects`, {
      body: JSON.stringify({ object_type: "patio", name: "Denied Phase 1 mutation" }),
      credentials: "include",
      headers: { "content-type": "application/json", "x-csrf-token": decodeURIComponent(csrf) },
      method: "POST",
    })).status;
  }, alpha.id);
  assert(response === 403, `Viewer map-object mutation expected 403, got ${response}`);
  const expectedError = `403 /api/gardens/${alpha.id}/map-objects`;
  const errorIndex = guarded.diagnostics.httpErrors.indexOf(expectedError);
  assert(errorIndex >= 0, "Viewer denial was not observed by browser diagnostics");
  guarded.diagnostics.httpErrors.splice(errorIndex, 1);
  await waitFor(
    async () => guarded.diagnostics.consoleErrors.length > consoleMark,
    "viewer denial console diagnostic",
  );
  assert(
    guarded.diagnostics.consoleErrors.length === consoleMark + 1,
    "Viewer denial produced unexpected additional console diagnostics",
  );
  guarded.diagnostics.consoleErrors.splice(consoleMark, 1);
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
      await assertViewerDenied(page, alpha, guarded);
      result.checks.viewer_write_denied = true;
      result.assertions.passed.push("A3-M2-viewer-ui-and-direct-denial");
    } else {
      await assertGlobalSearch(page, profile, alpha);
      if (profile === "desktop") {
        await exercisePlantAndSavedView(page, guarded.diagnostics, fixture, alpha);
        await mutateIndoorPlant(page, fixture);
        await exerciseMapObjectEditor(page, guarded.diagnostics, alpha);
        await updateGardenSettings(page, alpha);
        await openMap(page, profile);
        await exerciseSnapshotsAndImport(page, guarded.diagnostics, password);
        result.checks.desktop_mutation_workflows = true;
        result.assertions.passed.push("M1-M2-M3-M4-desktop-real-ui-mutations");
      } else {
        await exerciseMobileMapObject(page);
        await saveMobileSnapshot(page, fixture);
        await submitMobileQuickAction(page, fixture, alpha);
        result.checks.mobile_map_object_snapshot_and_quick_action = true;
        result.assertions.passed.push("M2-M3-M4-mobile-submitted-actions");
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
    { profile: "desktop", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
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
