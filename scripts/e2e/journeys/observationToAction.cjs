"use strict";

const fs = require("node:fs");
const {
  assertBrowserProfileContract,
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  dismissProactivePasskeyPrompt,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

const EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const IMAGE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGMMqFjAwMDAxMDAwMDAAAAQugFsZnyF3gAAAABJRU5ErkJggg==",
  "base64",
);

function phaseThreeFixture(fixture) {
  const phaseThree = fixture.phase_three;
  assert(phaseThree && typeof phaseThree === "object", "Missing fixture.phase_three");
  assert(typeof phaseThree.date === "string", "Missing fixture.phase_three.date");
  assert(typeof phaseThree.bloom_edit_date === "string",
    "Missing fixture.phase_three.bloom_edit_date");
  assert(phaseThree.labels && phaseThree.media && phaseThree.operation_slots,
    "Incomplete fixture.phase_three contract");
  return phaseThree;
}

function mediaInput(options, key = "observation") {
  const candidates = options.mediaInputs || {};
  const candidate = candidates[key]
    || (key === "diagnosis" ? candidates.oriented_jpeg || candidates.orientedJpeg : null)
    || candidates.reference_png
    || candidates.referencePng
    || candidates.image;
  assert(typeof candidate === "string" && candidate,
    `Missing options.mediaInputs.${key} (or reference_png)`);
  assert(fs.existsSync(candidate), `Phase 3 media input does not exist: ${candidate}`);
  return candidate;
}

function fixtureTargets(fixture, role, domain = "journal") {
  const viewer = fixture.phase_one?.viewer?.alpha;
  const plantId = role === "viewer"
    ? viewer?.plant_id
    : fixture.phase_three?.plant_ids?.[domain] || fixture.phase_two?.plant_ids?.bloom_desktop;
  const plotId = role === "viewer"
    ? viewer?.plot_id
    : fixture.phase_three?.plot_ids?.alpha || fixture.phase_two?.plot_ids?.alpha;
  assert(typeof plantId === "string" && plantId, `Missing Phase 3 ${role} plant target`);
  assert(typeof plotId === "string" && plotId, `Missing Phase 3 ${role} plot target`);
  return { plantId, plotId };
}

async function openPrimary(page, profile, tab) {
  const selector = profile === "mobile" ? `#mobile-tab-${tab}` : `#top-tab-${tab}`;
  const button = page.locator(`${selector}:visible`).first();
  await visible(button, `${profile} ${tab} primary navigation`);
  await button.click();
}

async function openActivityMode(page, profile, mode, contentSelector) {
  await openPrimary(page, profile, "activity");
  const button = page.locator(
    `#sub-mode-${mode}:visible, [data-sub-mode='${mode}']:visible`,
  ).first();
  await visible(button, `${profile} ${mode} activity mode`);
  await button.click();
  await visible(page.locator(contentSelector), `${profile} ${mode} content`);
}

async function selectGarden(page, profile, gardenId) {
  if (profile === "mobile" && !await page.locator("body.mobile-utility-open").count()) {
    await page.locator("#mobile-utility-btn").click();
    await waitFor(
      async () => await page.locator("body.mobile-utility-open").count() === 1,
      "mobile utility to open for garden selection",
    );
  }
  const select = page.locator(profile === "mobile" ? "#mobile-garden-select" : "#garden-select");
  await visible(select, `${profile} garden selector`);
  await select.selectOption(String(gardenId));
  await waitFor(
    async () => await select.inputValue() === String(gardenId),
    `${profile} active garden ${gardenId}`,
  );
  await waitFor(
    async () => !await page.locator("body.garden-switch-pending").count(),
    `${profile} garden switch ${gardenId} to settle`,
  );
  if (profile === "mobile" && await page.locator("body.mobile-utility-open").count()) {
    await page.locator("#mobile-utility-close-btn").click();
  }
}

function plantRecord(page, profile, name) {
  const selector = profile === "mobile"
    ? "#plants-mobile-list article[data-plt-id]"
    : "#plants-table-body tr[data-plt-id]";
  return page.locator(selector).filter({ hasText: name });
}

async function openPlants(page, profile) {
  await openPrimary(page, profile, "garden");
  const plantsMode = page.locator("#sub-mode-plants:visible");
  await visible(plantsMode, `${profile} plants mode`);
  await plantsMode.click();
  await visible(page.locator("#plants-search"), `${profile} plants surface`);
}

async function addPlotAssignment(form, plotId) {
  const search = form.locator("#plot-assign-search");
  await search.fill(plotId);
  const option = form.locator(`.plot-dd-item[data-plot='${plotId}']`);
  if (await option.count()) await option.click();
  else await search.press("Enter");
  await visible(form.locator(`.plot-chip[data-plot='${plotId}']`),
    `plant plot assignment ${plotId}`);
}

async function acceptConfirm(page, label) {
  const dialog = page.locator("[role='alertdialog']");
  await visible(dialog, label);
  await dialog.locator(".confirm-yes").click();
  await dialog.waitFor({ state: "detached" });
}

async function pageFetch(page, gardenId, request) {
  return page.evaluate(async ({ gardenId: activeGardenId, request: input }) => {
    const csrf = document.cookie.split("; ")
      .find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const headers = {
      "x-csrf-token": decodeURIComponent(csrf),
      "x-garden-id": String(activeGardenId),
      ...(input.headers || {}),
    };
    let body;
    if (input.body !== undefined) {
      headers["content-type"] = input.contentType || "application/json";
      body = input.contentType && input.contentType !== "application/json"
        ? new Uint8Array(input.body)
        : JSON.stringify(input.body);
    }
    const response = await fetch(input.path, {
      body,
      credentials: "include",
      headers,
      method: input.method || "POST",
    });
    const text = await response.text();
    let responseBody = text;
    try { responseBody = text ? JSON.parse(text) : null; } catch { /* non-JSON probe response */ }
    return { body: responseBody, status: response.status };
  }, { gardenId, request });
}

function removeExpectedHttpError(diagnostics, before, method, path, status) {
  const expected = `${status} ${path.split("?", 1)[0]}`;
  const added = diagnostics.httpErrors.splice(before);
  assert(
    added.length === 1 && added[0] === expected,
    `${method} ${path} produced unexpected diagnostics: ${JSON.stringify(added)}`,
  );
}

function removeExpectedProbeConsoleError(
  diagnostics,
  beforeConsole,
  beforeClassified,
  method,
  path,
  status,
) {
  const labels = diagnostics.consoleErrors.splice(beforeConsole);
  const classified = diagnostics.classifiedConsoleDiagnostics.splice(beforeClassified);
  assert(labels.length === 1
    && classified.length === 1
    && classified[0].context === "unexpected-http-response"
    && classified[0].method === method
    && classified[0].path === path
    && classified[0].status === status,
  `${method} ${path} ${status} console diagnostics were not isolated exactly`);
}

async function assertViewerForbidden(page, diagnostics, gardenId, request) {
  const beforeHttp = diagnostics.httpErrors.length;
  const beforeConsole = diagnostics.consoleErrors.length;
  const response = await pageFetch(page, gardenId, request);
  assert(response.status === 403, `Viewer ${request.path} write returned ${response.status}`);
  assert(response.body?.detail === "Forbidden: write access required",
    `Viewer ${request.path} write missed the write authorization gate`);
  await waitFor(() => diagnostics.httpErrors.length === beforeHttp + 1,
    `viewer ${request.path} forbidden response`);
  removeExpectedHttpError(diagnostics, beforeHttp, request.method || "POST", request.path, 403);
  await waitFor(() => diagnostics.consoleErrors.length >= beforeConsole + 1,
    `viewer ${request.path} forbidden console response`);
  diagnostics.consoleErrors.splice(beforeConsole);
}

async function exerciseViewer(page, diagnostics, profile, fixture) {
  const gardenId = fixture.gardens.alpha.id;
  const date = phaseThreeFixture(fixture).date;
  const { plantId, plotId } = fixtureTargets(fixture, "viewer");
  const surfaces = [
    ["journal", "#journal-tab-content", "#journal-add-btn", ".journal-card-actions"],
    [
      "issues",
      "#issues-tab-content",
      "#issues-add-btn",
      ".issue-action-resolve, .issue-action-reopen, .issue-action-delete",
    ],
    ["harvest", "#harvest-tab-content", "#harvest-add-btn", ".harvest-card-actions"],
  ];
  for (const [mode, content, add, actions] of surfaces) {
    await openActivityMode(page, profile, mode, content);
    await visible(page.locator(content), `${profile} viewer readable ${mode} surface`);
    assert(await page.locator(`${add}:visible`).count() === 0,
      `${profile} viewer received ${mode} create control`);
    assert(await page.locator(`${content} ${actions} button`).count() === 0,
      `${profile} viewer received ${mode} mutation controls`);
  }

  await openPlants(page, profile);
  const addPlant = page.locator("#add-plant-btn:visible");
  await visible(addPlant, `${profile} viewer plants surface`);
  assert(await addPlant.isDisabled(),
    `${profile} viewer received a usable plant creation entry point`);
  assert(await page.locator("#identify-from-photo-btn:visible").count() === 0,
    `${profile} viewer received an identification mutation entry point`);

  await openActivityMode(page, profile, "issues", "#issues-tab-content");
  const readableIssue = page.locator(".issue-card").first();
  await visible(readableIssue, `${profile} viewer issue summary`);
  await readableIssue.getByRole("button", { name: "View details", exact: true }).click();
  const details = page.getByRole("dialog").last();
  await visible(details, `${profile} viewer issue details`);
  await visible(details.locator(".plant-journal-history"), `${profile} viewer issue history`);
  await visible(details.locator(".journal-existing-media .media-gallery"),
    `${profile} viewer issue media evidence`);
  assert(await details.getByRole("button", { name: /remove|delete everywhere/i }).count() === 0,
    `${profile} viewer received issue media mutation controls`);
  await details.getByRole("button", { name: "Close", exact: true }).click();

  await assertViewerForbidden(page, diagnostics, gardenId, {
    path: "/api/journal",
    body: { event_type: "observed", occurred_on: date, title: "Forbidden viewer note", notes: "denial", plant_ids: [plantId], plot_ids: [plotId] },
  });
  await assertViewerForbidden(page, diagnostics, gardenId, {
    path: "/api/issues",
    body: { issue_type: "other", title: "Forbidden viewer issue", description: "denial", severity: "normal", plant_ids: [plantId], plot_ids: [plotId] },
  });
  await assertViewerForbidden(page, diagnostics, gardenId, {
    path: "/api/harvest",
    body: { occurred_on: date, quantity: 1.25, unit: "kg", quality: "good", notes: "denial", plant_ids: [plantId], plot_ids: [plotId] },
  });
  await assertViewerForbidden(page, diagnostics, gardenId, {
    path: "/api/media/upload?target_type=plant&target_id=" + encodeURIComponent(plantId),
    body: Array.from(IMAGE_PNG),
    contentType: "image/png",
    headers: { "x-upload-filename": "forbidden-viewer.png" },
  });
  return {
    direct_write_statuses: [403, 403, 403, 403],
    identification_write_entry_disabled: true,
    issue_details_readable: true,
    diagnosis_write_entry_hidden: true,
    readable_surfaces: 3,
  };
}

async function addComposerLinks(composer, plantId, plotId) {
  const selects = composer.locator("select.journal-add-select");
  if (await selects.count() >= 1) await selects.nth(0).selectOption(plantId);
  if (await selects.count() >= 2) await selects.nth(1).selectOption(plotId);
}

async function setPlantCoverThroughUi(page, profile, journalCard, plantId) {
  await journalCard.locator(".journal-tag-plant").click();
  const plantRecord = page.locator(
    profile === "mobile"
      ? `#plants-mobile-list article[data-plt-id='${plantId}']`
      : `#plants-table-body tr[data-plt-id='${plantId}']`,
  );
  await visible(plantRecord, "journal-linked plant record");
  await plantRecord.locator("[data-edit-plt]").click();
  const editPlant = page.locator("#edit-plant-form");
  await visible(editPlant, "linked plant edit form");
  const setCover = editPlant.getByRole("button", { name: /set as cover/i }).first();
  await visible(setCover, "set uploaded image as plant cover action");
  const coverResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/media/plants/${plantId}/cover`
  ));
  await setCover.click();
  assert((await coverResponse).status() === 200, "Set-cover action failed");
  await visible(editPlant.locator(".media-cover-note"), "plant cover badge");
  await editPlant.locator("#cancel-edit-plant").click();
}

async function exerciseJournalAndMedia(page, profile, options) {
  const { fixture } = options;
  const phaseThree = phaseThreeFixture(fixture);
  const targets = fixtureTargets(fixture, options.role, "journal");
  const plantId = options.identifiedPlantId || targets.plantId;
  const plotId = targets.plotId;
  const eventType = options.identifiedPlantId ? "bloomed" : "observed";
  const title = phaseThree.labels.journal_online;
  const editedTitle = `${title} edited`;
  await openActivityMode(page, profile, "journal", "#journal-tab-content");
  await page.locator("#journal-add-btn").click();
  const composer = page.locator(".journal-composer");
  await visible(composer, `${profile} journal composer`);
  await composer.locator("select[name='event_type']").selectOption(eventType);
  await composer.locator("input[name='occurred_on']").fill(phaseThree.date);
  await composer.locator("input[name='title']").fill(title);
  await composer.locator("textarea[name='notes']").fill("Observed leaf colour and linked the source plant and plot.");
  await addComposerLinks(composer, plantId, plotId);
  await composer.locator(".media-file-input").setInputFiles([
    mediaInput(options),
    mediaInput(options, "diagnosis"),
  ]);
  await waitFor(async () => await composer.locator(".media-card .media-thumb").count() === 2,
    "two journal upload previews");
  const createResponse = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/journal"
  ));
  const uploadResults = [];
  const collectUpload = (response) => {
    if (response.request().method() !== "POST"
      || new URL(response.url()).pathname !== "/api/media/upload") return;
    uploadResults.push({ status: response.status() });
  };
  page.on("response", collectUpload);
  await composer.locator(".journal-btn-submit").click();
  const created = await createResponse;
  assert(created.status() === 201, `Journal create returned ${created.status()}`);
  const entry = await created.json();
  await waitFor(() => uploadResults.length === 2, "two committed journal media uploads");
  page.off("response", collectUpload);
  assert(uploadResults.every((result) => result.status === 201),
    `Journal media uploads returned ${uploadResults.map((result) => result.status).join(", ")}`);
  if (eventType === "bloomed") {
    const observed = await pageFetch(page, fixture.gardens.alpha.id, {
      method: "GET",
      path: `/api/plants/${plantId}/details`,
    });
    assert(observed.status === 200 && observed.body?.seen_growing === true
      && observed.body?.seen_growing_date === phaseThree.date,
    "Bloom creation did not update the linked plant observation state");
  }
  const card = page.locator(".journal-card").filter({ hasText: title }).first();
  await visible(card, "created journal card");
  await visible(card.locator(".journal-tag-plant"), "journal plant link");
  await visible(card.locator(".journal-tag-plot"), "journal plot link");
  await visible(card.locator(".journal-card-thumb"), "journal representative media thumbnail");
  await card.locator(".journal-card-thumb").click();
  const preview = page.locator(".media-lightbox");
  await visible(preview, "journal photo preview");
  const dimensions = await preview.locator(".media-lightbox-image").evaluate((image) => ({
    height: image.naturalHeight,
    width: image.naturalWidth,
  }));
  assert(dimensions.height === phaseThree.media.oriented_jpeg.normalized_height
    && dimensions.width === phaseThree.media.oriented_jpeg.normalized_width,
  `Representative media dimensions were ${dimensions.width}x${dimensions.height}`);
  await preview.locator(".media-lightbox-close").click();

  await setPlantCoverThroughUi(page, profile, card, plantId);
  await openActivityMode(page, profile, "journal", "#journal-tab-content");
  const cardAfterCover = page.locator(".journal-card").filter({ hasText: title }).first();
  await visible(cardAfterCover, "journal card after setting plant cover");

  await cardAfterCover.getByRole("button", { name: "Edit", exact: true }).click();
  const edit = page.locator(".journal-composer");
  await visible(edit, "journal edit composer");
  await edit.locator("input[name='title']").fill(editedTitle);
  if (eventType === "bloomed") {
    await edit.locator("input[name='occurred_on']").fill(phaseThree.bloom_edit_date);
  }
  await edit.locator(".journal-btn-submit").click();
  const editedCard = page.locator(".journal-card").filter({ hasText: editedTitle }).first();
  await visible(editedCard, "edited journal card");
  if (eventType === "bloomed") {
    const observed = await pageFetch(page, fixture.gardens.alpha.id, {
      method: "GET",
      path: `/api/plants/${plantId}/details`,
    });
    assert(observed.status === 200 && observed.body?.seen_growing === true
      && observed.body?.seen_growing_date === phaseThree.bloom_edit_date,
    "Bloom date edit did not reconcile the linked plant observation state");
  }
  await page.locator("#journal-filter-search").fill(editedTitle);
  await visible(editedCard, "filtered journal card");
  assert(await page.locator(".journal-card").count() === 1, "Journal search did not isolate the edited entry");
  await page.locator("#journal-filter-search").fill("");
  await waitFor(async () => await page.locator(".journal-card").count() >= 1, "journal filter reset");

  await editedCard.getByRole("button", { name: "Edit", exact: true }).click();
  const mediaGallery = page.locator(".journal-existing-media, .media-gallery").last();
  await visible(mediaGallery, "journal existing media gallery");
  assert(await mediaGallery.locator(".media-card").count() === 2,
    "Journal edit gallery did not retain both uploaded media assets");
  for (let remaining = 2; remaining > 0; remaining -= 1) {
    const deleteEverywhere = mediaGallery
      .getByRole("button", { name: /delete everywhere/i })
      .first();
    await visible(deleteEverywhere, "delete uploaded media everywhere action");
    await deleteEverywhere.click();
    await acceptConfirm(page, "delete uploaded media everywhere confirmation");
    await waitFor(
      async () => await mediaGallery.locator(".media-card").count() === remaining - 1,
      `journal media delete everywhere (${remaining - 1} remaining)`,
    );
  }
  await page.keyboard.press("Escape");

  const deleteCard = page.locator(".journal-card").filter({ hasText: editedTitle }).first();
  await visible(deleteCard, "journal card before delete");
  await deleteCard.getByRole("button", { name: "Delete", exact: true }).click();
  await acceptConfirm(page, "journal delete confirmation");
  await deleteCard.waitFor({ state: "detached" });
  if (eventType === "bloomed") {
    const observed = await pageFetch(page, fixture.gardens.alpha.id, {
      method: "GET",
      path: `/api/plants/${plantId}/details`,
    });
    assert(observed.status === 200 && observed.body?.seen_growing == null
      && observed.body?.seen_growing_date == null,
    "Bloom deletion left derived observation state on the linked plant");
  }
  return {
    asset_count: uploadResults.length,
    bloom_date_reconciled: eventType === "bloomed",
    dimensions,
    entry_id: entry.id,
    linked_plant: plantId,
    linked_plot: plotId,
  };
}

async function exerciseIdentifyPlant(page, profile, options) {
  const phaseThree = phaseThreeFixture(options.fixture);
  const name = phaseThree.labels.identified_plant;
  await openPlants(page, profile);
  await page.locator("#add-plant-btn").click();
  await visible(page.locator("#plant-search-create-btn"), "plant create-new action");
  await page.locator("#plant-search-create-btn").click();
  let form = page.locator("#create-plant-form");
  await visible(form, "plant create form before photo identification");
  const identifyFromPhoto = page.locator("#identify-from-photo-btn:visible");
  await visible(identifyFromPhoto, "identify-from-photo action");
  await identifyFromPhoto.click();

  const identify = page.getByRole("dialog", { name: /identify plant/i });
  await visible(identify, "identify plant dialog");
  await identify.locator("input[type='file']").setInputFiles(mediaInput(options));
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/ai/identify-plant"
  ));
  await identify.getByRole("button", { name: "Identify", exact: true }).click();
  const response = await responsePromise;
  assert(response.status() === 200, `Plant identification returned ${response.status()}`);
  const candidate = identify.locator(".candidate-card").filter({ hasText: "Test rose" }).first();
  await visible(candidate, "deterministic plant identification candidate");
  await visible(candidate.getByText("Rosa canina", { exact: true }), "identified Latin name");
  await candidate.getByRole("button", { name: /add to garden/i }).click();

  form = page.locator("#create-plant-form");
  await visible(form, "identified plant prefilled create form");
  assert(await form.locator("input[name='name']").inputValue() === "Test rose",
    "Identify result did not prefill the plant name");
  assert(await form.locator("input[name='latin']").inputValue() === "Rosa canina",
    "Identify result did not prefill the Latin name");
  await form.locator("input[name='name']").fill(name);
  await form.locator("select[name='category']").selectOption("busker");
  const plotId = fixtureTargets(options.fixture, options.role, "journal").plotId;
  await addPlotAssignment(form, plotId);
  const createResponse = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/plants"
  ));
  await form.locator("button[type='submit']").click();
  assert((await createResponse).status() === 201, "Identified plant creation failed");
  const row = plantRecord(page, profile, name);
  await visible(row, "identified plant record");
  const plantId = await row.getAttribute("data-plt-id");
  assert(plantId, "Identified plant record has no plant ID");

  return {
    candidate_latin: "Rosa canina",
    candidate_name: "Test rose",
    create_from_prefill: true,
    explicit_add_action: true,
    plant_id: plantId,
    plant_name: name,
    plot_id: plotId,
  };
}

async function exerciseIdentifyAdvisory(page, profile, options) {
  await openPlants(page, profile);
  await page.locator("#add-plant-btn").click();
  await visible(page.locator("#plant-search-create-btn"), "plant create-new action");
  await page.locator("#plant-search-create-btn").click();
  await visible(page.locator("#create-plant-form"), "plant create form before photo identification");
  const identifyFromPhoto = page.locator("#identify-from-photo-btn:visible");
  await visible(identifyFromPhoto, "identify-from-photo action");
  await identifyFromPhoto.click();

  const identify = page.getByRole("dialog", { name: /identify plant/i });
  await visible(identify, "identify plant dialog");
  await identify.locator("input[type='file']").setInputFiles(mediaInput(options));
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/ai/identify-plant"
  ));
  await identify.getByRole("button", { name: "Identify", exact: true }).click();
  const response = await responsePromise;
  assert(response.status() === 200, `Plant identification returned ${response.status()}`);
  const candidate = identify.locator(".candidate-card").filter({ hasText: "Test rose" }).first();
  await visible(candidate, "deterministic plant identification candidate");
  await visible(candidate.getByText("Rosa canina", { exact: true }), "identified Latin name");
  await visible(candidate.getByRole("button", { name: /add to garden/i }),
    "identification requires an explicit add action");
  await identify.getByRole("button", { name: "Close", exact: true }).click();
  await identify.waitFor({ state: "detached" });
  return {
    advisory_non_mutating: true,
    candidate_latin: "Rosa canina",
    candidate_name: "Test rose",
    explicit_add_action_available: true,
  };
}

async function deleteIdentifiedPlant(page, profile, identified) {
  await openPlants(page, profile);
  const row = plantRecord(page, profile, identified.plant_name);
  await visible(row, "identified plant ready for cleanup");
  await row.locator("[data-edit-plt]").click();
  await visible(page.locator("#edit-plant-form"), "identified plant cleanup form");
  const deleteResponse = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
      && new URL(response.url()).pathname === `/api/plants/${identified.plant_id}`
  ));
  await page.locator("#delete-edit-plant").click();
  await acceptConfirm(page, "identified plant delete confirmation");
  assert((await deleteResponse).status() === 200, "Identified plant deletion failed");
  await row.waitFor({ state: "detached" });
}

function issueCard(page, title) {
  return page.locator(".issue-card").filter({ hasText: title }).first();
}

async function exerciseDiagnosisAndIssue(page, profile, options) {
  const phaseThree = phaseThreeFixture(options.fixture);
  const { plantId, plotId } = fixtureTargets(options.fixture, options.role, "issue");
  const title = phaseThree.labels.issue_online;
  const initialIssuesResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/issues"
  ));
  await openActivityMode(page, profile, "issues", "#issues-tab-content");
  const initialIssues = await initialIssuesResponse;
  assert(initialIssues.status() === 200, "Initial issue list load failed");
  const initialIssuePayload = await initialIssues.json();
  await waitFor(
    async () => await page.locator(".issue-card").count() === initialIssuePayload.issues.length,
    "initial issue list rendering",
  );
  const beforeCount = initialIssuePayload.issues.length;
  await page.locator("#issues-add-btn").click();
  const issueDialog = page.getByRole("dialog").last();
  await visible(issueDialog, "new issue dialog");
  const diagnoseButton = issueDialog.getByRole("button", { name: "What's wrong?" });
  await visible(diagnoseButton, "diagnosis advisory entry point");
  await diagnoseButton.click();
  const diagnosis = page.getByRole("dialog", { name: "What's wrong?" });
  await visible(diagnosis, "diagnosis advisory dialog");
  await diagnosis.locator("input[type='file']").setInputFiles(mediaInput(options, "diagnosis"));
  const diagnosisPreview = diagnosis.locator(".identify-photo-preview");
  await visible(diagnosisPreview, "diagnosis image preview");
  const diagnosisDimensions = await diagnosisPreview.evaluate((image) => ({
    naturalHeight: image.naturalHeight,
    naturalWidth: image.naturalWidth,
  }));
  assert(
    diagnosisDimensions.naturalWidth === phaseThree.media.oriented_jpeg.normalized_width
      && diagnosisDimensions.naturalHeight === phaseThree.media.oriented_jpeg.normalized_height,
    `Oriented diagnosis preview was ${diagnosisDimensions.naturalWidth}x${diagnosisDimensions.naturalHeight}`,
  );
  await diagnosis.getByRole("button", { name: "Diagnose", exact: true }).click();
  await visible(diagnosis.locator(".diagnosis-disclaimer"), "diagnosis advisory disclaimer");
  assert(await page.locator(".issue-card").count() === beforeCount,
    "Diagnosis advisory mutated issue state before explicit tracking");
  const track = diagnosis.getByRole("button", { name: /track this issue/i }).first();
  await visible(track, "diagnosis explicit track-this-issue action");
  await track.click();
  await waitFor(async () => await page.locator(".issue-card").count() === beforeCount + 1,
    "diagnosis issue tracking");
  const trackedIssueForm = page.locator(".modal-form").last();
  if (await trackedIssueForm.isVisible()) await page.keyboard.press("Escape");
  const diagnosedCard = issueCard(page, "Dry soil");
  await visible(diagnosedCard, "diagnosis-created issue card");
  await diagnosedCard.getByRole("button", { name: "Delete", exact: true }).click();
  await acceptConfirm(page, "diagnosed issue cleanup confirmation");

  await page.locator("#issues-add-btn").click();
  const form = page.locator(".modal-form").last();
  await visible(form, "issue create form");
  await form.locator("select[name='issue_type']").selectOption("disease");
  await form.locator("input[name='title']").fill(title);
  await form.locator("textarea[name='description']").fill("Spots observed on lower leaves.");
  await form.locator("select[name='severity']").selectOption("high");
  await form.locator("textarea[name='suspected_cause']").fill("Moisture stress");
  await form.locator("textarea[name='treatment_plan']").fill("Remove affected leaves and monitor.");
  await form.locator("input[name='follow_up_on']").fill("2026-07-22");
  const chips = form.locator("input[role='combobox'], input[type='search']");
  if (await chips.count() >= 1) {
    await chips.nth(0).fill(plantId);
    await page.getByRole("option").filter({ hasText: plantId }).first().click();
  }
  if (await chips.count() >= 2) {
    await chips.nth(1).fill(plotId);
    await page.getByRole("option").filter({ hasText: plotId }).first().click();
  }
  await form.locator(".media-file-input").setInputFiles(mediaInput(options));
  const issueCreateResponse = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/issues"
  ));
  await form.getByRole("button", { name: "Save", exact: true }).click();
  const createdIssueResponse = await issueCreateResponse;
  assert(createdIssueResponse.status() === 201,
    `Issue create returned ${createdIssueResponse.status()}`);
  const createdIssue = await createdIssueResponse.json();
  const card = issueCard(page, title);
  await visible(card, "created issue card");
  await card.getByRole("button", { name: "Settings", exact: true }).click();
  const edit = page.locator(".modal-form").last();
  await visible(edit, "issue edit form and history");
  await visible(page.locator(".plant-journal-history"), "issue history section");
  await edit.locator("textarea[name='description']").fill("Spots reduced after treatment; continue monitoring.");
  await edit.locator("select[name='severity']").selectOption("normal");
  await edit.getByRole("button", { name: "Save", exact: true }).click();
  await visible(issueCard(page, title), "updated issue card");
  await issueCard(page, title).getByRole("button", { name: "Settings", exact: true }).click();
  await visible(page.locator(".plant-journal-history .journal-preview-row").first(), "issue update history row");
  await page.keyboard.press("Escape");
  await issueCard(page, title).getByRole("button", { name: "Resolve", exact: true }).click();
  await acceptConfirm(page, "issue resolve confirmation");
  const resolved = issueCard(page, title);
  await visible(resolved.locator(".status-resolved"), "resolved issue status");
  const repeatedResolve = await pageFetch(page, options.fixture.gardens.alpha.id, {
    path: `/api/issues/${createdIssue.id}/resolve`,
    body: {},
  });
  assert(repeatedResolve.status === 200, `Repeated issue resolve returned ${repeatedResolve.status}`);
  const reopenControls = resolved.getByRole("button", { name: /reopen/i });
  await visible(reopenControls.first(), "visible issue reopen action");
  await reopenControls.first().click();
  await visible(issueCard(page, title).locator(".status-open"), "reopened issue status");
  await resolved.getByRole("button", { name: "Delete", exact: true }).click();
  await acceptConfirm(page, "issue delete confirmation");
  await resolved.waitFor({ state: "detached" });
  return {
    advisory_non_mutating_before_track: true,
    create_edit_follow_up_history_resolve_delete: true,
    repeated_resolve_status: repeatedResolve.status,
    reopen_ui_available: true,
  };
}

async function exerciseDiagnosisAdvisory(page, profile, options) {
  const initialIssuesResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/issues"
  ));
  await openActivityMode(page, profile, "issues", "#issues-tab-content");
  const initialIssues = await initialIssuesResponse;
  assert(initialIssues.status() === 200, "Initial issue list load failed");
  const initialIssuePayload = await initialIssues.json();
  await waitFor(
    async () => await page.locator(".issue-card").count() === initialIssuePayload.issues.length,
    "initial issue list rendering",
  );
  const beforeCount = initialIssuePayload.issues.length;
  await page.locator("#issues-add-btn").click();
  const issueDialog = page.getByRole("dialog").last();
  await visible(issueDialog, "new issue dialog");
  const diagnoseButton = issueDialog.getByRole("button", { name: "What's wrong?" });
  await visible(diagnoseButton, "diagnosis advisory entry point");
  await diagnoseButton.click();
  const diagnosis = page.getByRole("dialog", { name: "What's wrong?" });
  await visible(diagnosis, "diagnosis advisory dialog");
  await diagnosis.locator("input[type='file']").setInputFiles(mediaInput(options, "diagnosis"));
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/ai/diagnose-plant"
  ));
  await diagnosis.getByRole("button", { name: "Diagnose", exact: true }).click();
  assert((await responsePromise).status() === 200, "Photo diagnosis returned a non-success response");
  await visible(diagnosis.locator(".diagnosis-disclaimer"), "diagnosis advisory disclaimer");
  await visible(diagnosis.getByRole("button", { name: /track this issue/i }).first(),
    "diagnosis explicit track-this-issue action");
  assert(await page.locator(".issue-card").count() === beforeCount,
    "Diagnosis advisory mutated issue state before explicit tracking");
  await diagnosis.getByRole("button", { name: "Close", exact: true }).click();
  await diagnosis.waitFor({ state: "detached" });
  return {
    advisory_non_mutating: true,
    explicit_track_action_available: true,
  };
}

function harvestCard(page, notes) {
  return page.locator(".harvest-card").filter({ hasText: notes }).first();
}

async function fillHarvest(form, { date, notes, quantity, unit }) {
  await form.locator("input[name='occurred_on']").fill(date);
  await form.locator("input[name='quantity']").fill(quantity);
  await form.locator("select[name='unit']").selectOption(unit);
  await form.locator("select[name='quality']").selectOption("excellent");
  await form.locator("textarea[name='notes']").fill(notes);
}

async function exerciseHarvest(page, profile, options) {
  const phaseThree = phaseThreeFixture(options.fixture);
  const { plantId, plotId } = fixtureTargets(options.fixture, options.role, "harvest");
  const notes = phaseThree.labels.harvest_online;
  await openActivityMode(page, profile, "harvest", "#harvest-tab-content");
  await page.locator("#harvest-add-btn").click();
  const form = page.locator(".harvest-form, .modal-form").last();
  await visible(form, "harvest create form");
  if (profile === "mobile") {
    const quantity = form.locator("input[name='quantity']");
    let attemptedCreates = 0;
    const observeCreate = (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/harvest") {
        attemptedCreates += 1;
      }
    };
    page.on("request", observeCreate);
    await quantity.fill("1.375");
    await form.getByRole("button", { name: "Save", exact: true }).click();
    await page.waitForTimeout(100);
    page.off("request", observeCreate);
    assert(await form.isVisible(), "Mobile harvest accepted a step-mismatched decimal quantity");
    assert(attemptedCreates === 0, "Invalid mobile harvest quantity reached the API");
    assert(await quantity.evaluate((input) => input.validity.stepMismatch),
      "Mobile harvest did not expose decimal step validation");
    await quantity.fill("1.25");
    await form.locator("select[name='unit']").selectOption("kg");
    assert(await quantity.inputValue() === "1.25", "Mobile harvest lost decimal precision");
    assert(await form.locator("select[name='unit']").inputValue() === "kg", "Mobile harvest lost selected unit");
  }
  await fillHarvest(form, { date: phaseThree.date, notes, quantity: "1.25", unit: "kg" });
  await form.locator("input[name='plant_ids']").fill(plantId);
  await form.locator("input[name='plot_ids']").fill(plotId);
  await form.locator(".media-file-input").setInputFiles(mediaInput(options));
  await form.getByRole("button", { name: "Save", exact: true }).click();
  const card = harvestCard(page, notes);
  await visible(card, "created harvest card");
  await visible(card.getByText("1.25", { exact: true }), "harvest decimal quantity");
  await card.getByRole("button", { name: "Settings", exact: true }).click();
  const edit = page.locator(".harvest-form, .modal-form").last();
  await edit.locator("textarea[name='notes']").fill(`${notes} edited`);
  await edit.locator("input[name='quantity']").fill("2.25");
  await edit.getByRole("button", { name: "Save", exact: true }).click();
  const edited = harvestCard(page, `${notes} edited`);
  await visible(edited, "edited harvest card");
  await page.locator("#harvest-summary-btn").click();
  await visible(page.locator("#harvest-summary-panel"), "harvest summary panel");
  await edited.getByRole("button", { name: "Delete", exact: true }).click();
  await acceptConfirm(page, "harvest delete confirmation");
  await edited.waitFor({ state: "detached" });
  return { decimal_quantity: 1.25, edited_quantity: 2.25, summary_visible: true, unit: "kg" };
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
      const rows = await new Promise((resolve, reject) => {
        const request = transaction.objectStore("drafts").getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("IndexedDB read failed"));
      });
      return rows.map((draft) => ({
        garden_id: draft.garden_id,
        operation_id: draft.operation_id,
        payload: draft.payload,
        status: draft.status,
        type: draft.type,
      }));
    } finally { database.close(); }
  });
}

async function assignOfflineOperationSlots(page, slots) {
  return page.evaluate(async (operationSlots) => {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("gardenops-offline");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
    });
    try {
      const transaction = database.transaction("drafts", "readwrite");
      const store = transaction.objectStore("drafts");
      const rows = await new Promise((resolve, reject) => {
        const request = store.getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("IndexedDB read failed"));
      });
      for (const draft of rows) {
        const slot = draft.type === "journal"
          ? operationSlots.journal
          : draft.type === "issue_create"
            ? operationSlots.issue
            : operationSlots.harvest;
        const mediaSlot = draft.type === "journal"
          ? operationSlots.media
          : `${operationSlots.media}-${draft.type === "issue_create" ? "issue" : "harvest"}`;
        draft.operation_id = slot;
        if (Array.isArray(draft.payload?._serialized_media)) {
          draft.payload._serialized_media = draft.payload._serialized_media.map((media, index) => ({
            ...media,
            operation_id: index === 0 ? mediaSlot : `${mediaSlot}-${index + 1}`,
          }));
        }
        store.put(draft);
      }
      await new Promise((resolve, reject) => {
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error || new Error("IndexedDB update failed"));
        transaction.onabort = () => reject(transaction.error || new Error("IndexedDB update aborted"));
      });
    } finally { database.close(); }
  }, slots);
}

function generatedOfflineOperationIds(drafts) {
  return drafts.flatMap((draft) => [
    draft.operation_id,
    ...(Array.isArray(draft.payload?._serialized_media)
      ? draft.payload._serialized_media.map((media) => media.operation_id)
      : []),
  ]);
}

function assertGeneratedOfflineOperationIds(drafts) {
  assert(drafts.length === 3, `Expected three app-generated offline drafts, got ${drafts.length}`);
  const ids = generatedOfflineOperationIds(drafts);
  assert(ids.length === 6, `Expected six app-generated offline operation IDs, got ${ids.length}`);
  assert(ids.every((value) => (
    typeof value === "string"
      && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)
  )), "The app generated an invalid offline operation UUID");
  assert(new Set(ids).size === ids.length,
    "The app generated duplicate offline create or attachment operation IDs");
  return ids;
}

async function exerciseDelayedIssueGardenSwitch(page, profile, fixture, alphaIssueTitle) {
  const alphaGardenId = String(fixture.gardens.alpha.id);
  const betaGardenId = fixture.gardens.beta.id;
  let captured = false;
  let delayed = false;
  let releaseRequest = () => {};
  const released = new Promise((resolve) => { releaseRequest = resolve; });
  const handler = async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (delayed
      || request.method() !== "GET"
      || pathname !== "/api/issues"
      || request.headers()["x-garden-id"] !== alphaGardenId) {
      await route.continue();
      return;
    }
    delayed = true;
    captured = true;
    await released;
    await route.continue();
  };
  await page.route("**/api/issues*", handler);
  try {
    await openActivityMode(page, profile, "issues", "#issues-tab-content");
    await waitFor(() => captured, "delayed Alpha issue response capture");
    const staleResponse = page.waitForResponse((response) => (
      response.request().method() === "GET"
        && new URL(response.url()).pathname === "/api/issues"
        && response.request().headers()["x-garden-id"] === alphaGardenId
    ));
    await selectGarden(page, profile, betaGardenId);
    releaseRequest();
    await staleResponse;
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(50);
    assert(await issueCard(page, alphaIssueTitle).count() === 0,
      "Late Alpha issue response replaced the Beta garden UI");
    return true;
  } finally {
    releaseRequest();
    await page.unroute("**/api/issues*", handler);
  }
}

async function enqueueOfflineCreate(page, profile, options, kind) {
  const phaseThree = phaseThreeFixture(options.fixture);
  const title = kind === "journal"
    ? phaseThree.labels.journal_offline
    : kind === "issue_create"
      ? phaseThree.labels.issue_offline
      : phaseThree.labels.harvest_offline;
  if (kind === "journal") {
    await openActivityMode(page, profile, "journal", "#journal-tab-content");
    await page.locator("#journal-add-btn").click();
    const form = page.locator(".journal-composer");
    await form.locator("input[name='occurred_on']").fill(phaseThree.date);
    await form.locator("input[name='title']").fill(title);
    await form.locator("textarea[name='notes']").fill("Queued observation with attachment.");
    await form.locator(".media-file-input").setInputFiles(mediaInput(options));
    await form.locator(".journal-btn-submit").click();
  } else if (kind === "issue_create") {
    const { plantId, plotId } = fixtureTargets(options.fixture, options.role, "issue");
    await openActivityMode(page, profile, "issues", "#issues-tab-content");
    await page.locator("#issues-add-btn").click();
    const form = page.locator(".modal-form").last();
    await form.locator("input[name='title']").fill(title);
    await form.locator("textarea[name='description']").fill("Queued issue observation.");
    await form.locator("input[name='follow_up_on']").fill("2026-07-23");
    const chips = form.locator("input[role='combobox']");
    await chips.nth(0).fill(plantId);
    await form.getByRole("option").filter({ hasText: plantId }).first().click();
    await chips.nth(1).fill(plotId);
    await form.getByRole("option").filter({ hasText: plotId }).first().click();
    await form.locator(".media-file-input").setInputFiles(mediaInput(options));
    await form.getByRole("button", { name: "Save", exact: true }).click();
  } else {
    const { plantId, plotId } = fixtureTargets(options.fixture, options.role, "harvest");
    await openActivityMode(page, profile, "harvest", "#harvest-tab-content");
    await page.locator("#harvest-add-btn").click();
    const form = page.locator(".harvest-form, .modal-form").last();
    await fillHarvest(form, {
      date: phaseThree.date,
      notes: title,
      quantity: "0.75",
      unit: "kg",
    });
    await form.locator("input[name='plant_ids']").fill(plantId);
    await form.locator("input[name='plot_ids']").fill(plotId);
    await form.locator(".media-file-input").setInputFiles(mediaInput(options));
    await form.getByRole("button", { name: "Save", exact: true }).click();
  }
  return title;
}

async function exerciseOfflineCreates(page, context, diagnostics, profile, options) {
  const gardenId = options.fixture.gardens.alpha.id;
  const phaseThree = phaseThreeFixture(options.fixture);
  for (const [mode, selector] of [
    ["journal", "#journal-tab-content"],
    ["issues", "#issues-tab-content"],
    ["harvest", "#harvest-tab-content"],
  ]) {
    await openActivityMode(page, profile, mode, selector);
    await page.waitForLoadState("networkidle");
  }
  const offlinePaths = new Set(["/api/harvest", "/api/issues", "/api/journal"]);
  const offlineRequestMark = diagnostics.requestFailures.length;
  const offlineConsoleMark = diagnostics.consoleErrors.length;
  const offlineClassifiedMark = diagnostics.classifiedConsoleDiagnostics.length;
  const offlineFailures = [];
  const captureOfflineFailure = (request) => {
    const failure = request.failure()?.errorText || "";
    if (!failure.includes("ERR_INTERNET_DISCONNECTED")) return;
    offlineFailures.push({
      method: request.method(),
      path: new URL(request.url()).pathname,
    });
  };
  page.on("requestfailed", captureOfflineFailure);
  await context.setOffline(true);
  assert(await page.evaluate(() => navigator.onLine) === false, "Offline emulation did not update navigator.onLine");
  const titles = [];
  for (const kind of ["journal", "issue_create", "harvest_create"]) {
    titles.push(await enqueueOfflineCreate(page, profile, options, kind));
  }
  page.off("requestfailed", captureOfflineFailure);
  assert(offlineFailures.length >= offlinePaths.size,
    `Expected offline read failures for three domains, got ${offlineFailures.length}`);
  assert(offlineFailures.every((failure) => (
    failure.method === "GET" && offlinePaths.has(failure.path)
  )), "Offline queueing attempted an unexpected network request");
  assert(new Set(offlineFailures.map((failure) => failure.path)).size === offlinePaths.size,
    "Offline queueing did not exercise journal, issue, and harvest read fallback");
  await waitFor(
    () => diagnostics.requestFailures.length - offlineRequestMark === offlineFailures.length,
    "offline request failure accounting",
  );
  await waitFor(
    () => diagnostics.consoleErrors.length - offlineConsoleMark === offlineFailures.length,
    "offline console failure accounting",
  );
  const offlineConsoleDiagnostics = diagnostics.classifiedConsoleDiagnostics.slice(
    offlineClassifiedMark,
  );
  assert(offlineConsoleDiagnostics.length === offlineFailures.length
    && offlineConsoleDiagnostics.every((entry) => (
      entry.context === "unclassified-console-error"
      && entry.method === "UNKNOWN"
      && entry.status == null
      && entry.path === "unknown"
    )), "Offline browser diagnostics did not match the captured request failures");
  diagnostics.requestFailures.splice(offlineRequestMark, offlineFailures.length);
  diagnostics.consoleErrors.splice(offlineConsoleMark, offlineFailures.length);
  diagnostics.classifiedConsoleDiagnostics.splice(offlineClassifiedMark, offlineFailures.length);
  const generatedQueued = await readOfflineDrafts(page);
  const generatedIds = assertGeneratedOfflineOperationIds(generatedQueued);
  const generatedIdsAfterReread = assertGeneratedOfflineOperationIds(await readOfflineDrafts(page));
  assert(JSON.stringify(generatedIdsAfterReread) === JSON.stringify(generatedIds),
    "App-generated offline operation IDs were not stable in IndexedDB");
  await assignOfflineOperationSlots(page, phaseThree.operation_slots);
  const queued = await readOfflineDrafts(page);
  assert(queued.length === 3, `Expected three Phase 3 offline creates, got ${queued.length}`);
  assert(new Set(queued.map((draft) => draft.operation_id)).size === 3,
    "Offline creates reused an operation ID");
  assert(
    JSON.stringify(queued.map((draft) => draft.operation_id).sort()) === JSON.stringify([
      phaseThree.operation_slots.harvest,
      phaseThree.operation_slots.issue,
      phaseThree.operation_slots.journal,
    ].sort()),
    "Offline create operation IDs did not use the Phase 3 oracle slots",
  );
  assert(queued.every((draft) => draft.garden_id === gardenId),
    "Offline create lost its original garden ID");
  const queuedJournal = queued.find((draft) => draft.type === "journal");
  assert(queuedJournal?.payload?._serialized_media?.[0]?.operation_id === phaseThree.operation_slots.media,
    "Offline attachment lacks the persistent Phase 3 media operation ID");

  const replayed = [];
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    const operationId = response.request().headers()["x-offline-operation-id"];
    if (response.request().method() !== "POST" || !operationId || response.status() !== 201) return;
    if (["/api/journal", "/api/issues", "/api/harvest"].includes(path)) {
      const body = await response.json().catch(() => null);
      replayed.push({ body, operation_id: operationId, path });
    }
  });
  await page.evaluate((operationId) => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const requestUrl = typeof input === "string" ? input : input.url;
      const headers = new Headers(init.headers || (typeof input === "string" ? undefined : input.headers));
      const shouldDrop = !window.__phaseThreeAckDropped
        && new URL(requestUrl, window.location.href).pathname === "/api/journal"
        && headers.get("x-offline-operation-id") === operationId;
      const response = await nativeFetch(input, init);
      if (!shouldDrop) return response;
      window.__phaseThreeAckDropped = true;
      await new Promise(() => {});
      throw new Error("unreachable lost-ack guard");
    };
  }, phaseThree.operation_slots.journal);
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await waitFor(
    async () => await page.evaluate(() => window.__phaseThreeAckDropped === true),
    "journal server commit before simulated acknowledgement loss",
  );
  assert((await readOfflineDrafts(page)).some((draft) => (
    draft.operation_id === phaseThree.operation_slots.journal && draft.status === "syncing"
  )), "Lost-ack journal draft was not retained as syncing before reload");
  await page.reload({ waitUntil: "domcontentloaded" });
  await visible(
    page.locator(profile === "mobile" ? "#mobile-tab-map" : "#top-tab-map"),
    "authenticated navigation after lost-ack reload",
  );
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await waitFor(
    () => new Set(replayed.map((item) => item.operation_id)).size === 3,
    "three distinct offline create replays after lost acknowledgement",
  );
  await waitFor(async () => (await readOfflineDrafts(page)).length === 0, "offline create queue to drain");
  await context.setOffline(true);
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.waitForTimeout(350);
  assert(new Set(replayed.map((item) => item.operation_id)).size === 3,
    "Second reconnect changed the replayed operation identity set");

  const journalDraft = queued.find((draft) => draft.type === "journal");
  const journalReplay = replayed.find((item) => item.path === "/api/journal");
  assert(journalDraft && journalReplay?.body?.id, "Journal replay evidence is incomplete");
  const { _serialized_media: _media, ...changedPayload } = journalDraft.payload;
  changedPayload.title = `${changedPayload.title} changed fingerprint`;
  let beforeHttp = diagnostics.httpErrors.length;
  let beforeConsole = diagnostics.consoleErrors.length;
  let beforeClassified = diagnostics.classifiedConsoleDiagnostics.length;
  const conflict = await pageFetch(page, gardenId, {
    path: "/api/journal",
    body: changedPayload,
    headers: { "x-offline-operation-id": journalDraft.operation_id },
  });
  assert(conflict.status === 409, `Changed fingerprint probe returned ${conflict.status}`);
  await waitFor(() => diagnostics.httpErrors.length === beforeHttp + 1, "changed fingerprint diagnostic");
  removeExpectedHttpError(diagnostics, beforeHttp, "POST", "/api/journal", 409);
  await waitFor(() => diagnostics.consoleErrors.length >= beforeConsole + 1, "changed fingerprint console diagnostic");
  removeExpectedProbeConsoleError(
    diagnostics, beforeConsole, beforeClassified, "POST", "/api/journal", 409,
  );

  await openActivityMode(page, profile, "journal", "#journal-tab-content");
  const replayCard = page.locator(".journal-card").filter({ hasText: titles[0] }).first();
  await visible(replayCard, "replayed journal before tombstone probe");
  await replayCard.getByRole("button", { name: "Delete", exact: true }).click();
  await acceptConfirm(page, "replayed journal delete confirmation");
  beforeHttp = diagnostics.httpErrors.length;
  beforeConsole = diagnostics.consoleErrors.length;
  beforeClassified = diagnostics.classifiedConsoleDiagnostics.length;
  const tombstone = await pageFetch(page, gardenId, {
    path: "/api/journal",
    body: { ...journalDraft.payload, _serialized_media: undefined },
    headers: { "x-offline-operation-id": journalDraft.operation_id },
  });
  assert(tombstone.status === 410, `Deleted target replay probe returned ${tombstone.status}`);
  await waitFor(() => diagnostics.httpErrors.length === beforeHttp + 1, "deleted target diagnostic");
  removeExpectedHttpError(diagnostics, beforeHttp, "POST", "/api/journal", 410);
  await waitFor(() => diagnostics.consoleErrors.length >= beforeConsole + 1, "deleted target console diagnostic");
  removeExpectedProbeConsoleError(
    diagnostics, beforeConsole, beforeClassified, "POST", "/api/journal", 410,
  );
  const staleResponseRejected = await exerciseDelayedIssueGardenSwitch(
    page,
    profile,
    options.fixture,
    titles[1],
  );
  await openActivityMode(page, profile, "issues", "#issues-tab-content");
  assert(await issueCard(page, phaseThree.labels.issue_offline).count() === 0,
    "Alpha offline issue leaked into the Beta garden UI");
  await openActivityMode(page, profile, "harvest", "#harvest-tab-content");
  assert(await harvestCard(page, phaseThree.labels.harvest_offline).count() === 0,
    "Alpha offline harvest leaked into the Beta garden UI");
  return {
    changed_fingerprint_status: conflict.status,
    deleted_target_status: tombstone.status,
    generated_operation_ids_stable: true,
    offline_network_failures: offlineFailures.length,
    queued_operations: queued.map((draft) => ({ operation_id: draft.operation_id, type: draft.type })),
    reconnect_count: 2,
    replayed_operations: [...new Map(replayed.map((item) => [
      item.operation_id,
      { operation_id: item.operation_id, path: item.path },
    ])).values()],
    response_ack_loss_simulated: true,
    retained_records_garden_isolated: true,
    stale_response_rejected_after_garden_switch: staleResponseRejected,
  };
}

async function runProfile(options) {
  const run = options;
  const guarded = await createGuardedContext(
    run.browser,
    run.devices,
    run.profile,
    run.artifactDir,
    `phase-three-${run.profile}-${run.role}`,
    { baseUrl: run.baseUrl },
  );
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page, {
    authType: "session",
    role: run.role,
    username: run.username,
  });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile: run.profile,
    requests: [],
    role: run.role,
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(run.baseUrl, { waitUntil: "domcontentloaded" });
    const authenticated = await authenticate(page, run.username, run.password);
    guarded.markAuthenticated();
    assert(authenticated.role === run.role, `Expected ${run.role}, authenticated as ${authenticated.role}`);
    await dismissProactivePasskeyPrompt(page);
    result.browser_profile.user_agent = await page.evaluate(() => navigator.userAgent);
    result.browser_profile.max_touch_points = await page.evaluate(() => navigator.maxTouchPoints);
    result.browser_profile.has_touch = result.browser_profile.max_touch_points > 0;
    result.browser_profile.viewport = page.viewportSize();
    result.browser_profile.user_agent_contract = assertBrowserProfileContract(run.profile, result.browser_profile);
    await visible(page.locator("#map-grid"), "Phase 3 map-first surface");
    await selectGarden(page, run.profile, run.fixture.gardens.alpha.id);
    result.checks.auth_session = true;
    result.checks.last_completed_step = "profile-setup";

    if (run.role === "viewer") {
      result.checks.viewer_read_only = await exerciseViewer(
        page, guarded.diagnostics, run.profile, run.fixture,
      );
      result.checks.last_completed_step = "viewer-read-only-denials";
    } else if (run.role === "admin" && run.profile === "desktop") {
      result.checks.identify_plant = await exerciseIdentifyPlant(page, run.profile, run);
      result.checks.last_completed_step = "identify-plant-explicit-add";
      result.checks.journal_media = await exerciseJournalAndMedia(page, run.profile, {
        ...run,
        identifiedPlantId: result.checks.identify_plant.plant_id,
      });
      result.checks.last_completed_step = "journal-media-lifecycle";
      await deleteIdentifiedPlant(page, run.profile, result.checks.identify_plant);
      result.checks.identify_plant.deleted_after_observation = true;
      result.checks.issue_lifecycle = await exerciseDiagnosisAndIssue(page, run.profile, run);
      result.checks.last_completed_step = "diagnosis-issue-lifecycle";
      result.checks.harvest_lifecycle = await exerciseHarvest(page, run.profile, run);
      result.checks.last_completed_step = "harvest-lifecycle";
    } else if (run.role === "editor" && run.profile === "desktop") {
      result.checks.identify_advisory = await exerciseIdentifyAdvisory(page, run.profile, run);
      result.checks.last_completed_step = "editor-identify-advisory";
      result.checks.diagnosis_advisory = await exerciseDiagnosisAdvisory(page, run.profile, run);
      result.checks.last_completed_step = "editor-diagnosis-advisory";
      result.checks.journal_media = await exerciseJournalAndMedia(page, run.profile, run);
      result.checks.last_completed_step = "editor-journal-media";
      result.checks.harvest_lifecycle = await exerciseHarvest(page, run.profile, run);
      result.checks.last_completed_step = "editor-harvest";
    } else if (run.role === "admin" && run.profile === "mobile") {
      result.checks.harvest_lifecycle = await exerciseHarvest(page, run.profile, run);
      result.checks.last_completed_step = "mobile-decimal-unit-harvest";
      result.checks.issue_lifecycle = await exerciseDiagnosisAndIssue(page, run.profile, run);
      result.checks.last_completed_step = "mobile-issue-lifecycle";
      result.checks.identify_advisory = await exerciseIdentifyAdvisory(page, run.profile, run);
      result.checks.last_completed_step = "mobile-identify-advisory";
    } else {
      result.checks.offline_replay = await exerciseOfflineCreates(
        page, guarded.context, guarded.diagnostics, run.profile, run,
      );
      result.checks.last_completed_step = "offline-create-attachment-idempotency";
    }

    result.structure = await assertPageStructure(
      page,
      `${run.profile} ${run.role} Phase 3`,
      { enforceControlNames: true },
    );
    assertDiagnosticsClean(guarded.diagnostics, `${run.profile} ${run.role} Phase 3`);
    result.checks.browser_diagnostics = true;
    result.assertions.passed.push(`phase-three-${run.role}-${run.profile}`);
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "Phase 3 profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    await recorder.settle();
    result.requests = recorder.records;
    result.diagnostics = guarded.diagnostics;
    try {
      result.trace = await guarded.close(status);
    } catch (error) {
      if (!caughtError) caughtError = error;
    }
  }
  return { error: caughtError, result };
}

async function runObservationToAction(options, profileRunner = runProfile) {
  const runs = [
    { profile: "desktop", role: "admin", username: options.username, password: options.password },
    { profile: "desktop", role: "editor", username: options.fixture.roles.editor, password: EDITOR_PASSWORD },
    { profile: "mobile", role: "admin", username: options.username, password: options.password },
    { profile: "mobile", role: "editor", username: options.fixture.roles.editor, password: EDITOR_PASSWORD },
    { profile: "desktop", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
    { profile: "mobile", role: "viewer", username: options.fixture.roles.viewer, password: VIEWER_PASSWORD },
  ];
  const results = [];
  for (const run of runs) {
    const outcome = await profileRunner({ ...options, ...run });
    results.push(outcome.result);
    if (options.onProfile) options.onProfile(outcome.result);
    if (outcome.error) throw outcome.error;
  }
  await waitFor(
    () => results.length === 6 && results.every((result) => result.checks.last_completed_step),
    "Phase 3 ordered profile completion",
  );
  return results;
}

module.exports = { runObservationToAction };
