"use strict";

const {
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  dismissProactivePasskeyPrompt,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

async function openJournal(page) {
  await page.locator("#top-tab-activity:visible").click();
  await page.locator("#sub-mode-journal:visible, [data-sub-mode='journal']:visible").first().click();
  await visible(page.locator("#journal-tab-content"), "Phase 6 journal surface");
}

async function selectGarden(page, gardenId) {
  const select = page.locator("#garden-select:visible");
  await visible(select, "Phase 6 garden selector");
  await select.selectOption(String(gardenId));
  await waitFor(async () => await select.inputValue() === String(gardenId),
    `Phase 6 active garden ${gardenId}`);
  await waitFor(async () => !await page.locator("body.garden-switch-pending").count(),
    `Phase 6 garden ${gardenId} switch`);
}

async function readDrafts(page) {
  return page.evaluate(async () => {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("gardenops-offline");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Offline database open failed"));
    });
    try {
      const transaction = database.transaction("drafts", "readonly");
      return await new Promise((resolve, reject) => {
        const request = transaction.objectStore("drafts").getAll();
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error || new Error("Offline drafts read failed"));
      });
    } finally {
      database.close();
    }
  });
}

async function insertFailedDrafts(page, gardenId, journalPayload) {
  await page.evaluate(async ({ activeGardenId, rows }) => {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open("gardenops-offline");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Offline database open failed"));
    });
    try {
      const transaction = database.transaction("drafts", "readwrite");
      const store = transaction.objectStore("drafts");
      for (const [index, row] of rows.entries()) {
        store.add({
          ...row,
          created_at_ms: Date.now() + index,
          garden_id: activeGardenId,
          last_error: row.last_status === 409
            ? "This saved operation conflicts with changed server data."
            : "The saved operation target no longer exists.",
          operation_id: crypto.randomUUID(),
          retry_count: 1,
          status: "failed",
        });
      }
      await new Promise((resolve, reject) => {
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error || new Error("Failed drafts write failed"));
        transaction.onabort = () => reject(transaction.error || new Error("Failed drafts write aborted"));
      });
      window.dispatchEvent(new CustomEvent("gardenops:offline-queue-changed"));
    } finally {
      database.close();
    }
  }, {
    activeGardenId: gardenId,
    rows: [
      {
        type: "journal",
        last_status: 409,
        payload: {
          ...journalPayload,
          notes: "Phase 6 explicit retry-as-new observation.",
          title: "Conflicting journal observation",
        },
      },
      { type: "issue_create", last_status: 410, payload: { title: "Removed issue target" } },
      { type: "harvest_create", last_status: 409, payload: { notes: "Conflicting harvest record" } },
      {
        type: "task_complete",
        last_status: 410,
        payload: { action_label: "Complete", task_id: "tsk_phase_six_removed", task_label: "Removed care task" },
      },
      {
        type: "plant_media_upload",
        last_status: 410,
        payload: {
          _serialized_media: [{ buffer: new ArrayBuffer(1), name: "removed-target.jpg", operation_id: crypto.randomUUID(), type: "image/jpeg" }],
          target_id: "PLT-PHASE-SIX-REMOVED",
          target_label: "Removed plant",
        },
      },
    ],
  });
}

async function browserJson(page, path, gardenId) {
  return page.evaluate(async ({ requestPath, activeGardenId }) => {
    const response = await fetch(requestPath, {
      credentials: "include",
      headers: { "x-garden-id": String(activeGardenId) },
    });
    return { body: await response.json(), status: response.status };
  }, { activeGardenId: gardenId, requestPath: path });
}

async function enqueueOfflineJournal(page, fixture, title) {
  const journalLoaded = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "GET"
      && url.pathname === "/api/journal"
      && response.status() === 200;
  });
  const mediaPreviewsLoaded = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST"
      && url.pathname === "/api/media/summaries"
      && response.status() === 200;
  });
  await openJournal(page);
  await Promise.all([journalLoaded, mediaPreviewsLoaded]);
  await page.context().setOffline(true);
  await page.locator("#journal-add-btn").click();
  const form = page.locator(".journal-composer");
  await form.locator("input[name='occurred_on']").fill(fixture.clock.attention_date);
  await form.locator("input[name='title']").fill(title);
  await form.locator("textarea[name='notes']").fill("Phase 6 real lost acknowledgement replay.");
  await form.locator(".journal-btn-submit").click();
  await waitFor(async () => (await readDrafts(page)).length === 1, "Phase 6 queued journal draft");
  await visible(page.locator("#offline-indicator .offline-indicator--offline"),
    "Phase 6 offline queued state");
  return (await readDrafts(page))[0];
}

function diagnosticMarks(diagnostics) {
  return {
    classified: diagnostics.classifiedConsoleDiagnostics.length,
    console: diagnostics.consoleErrors.length,
    request: diagnostics.requestFailures.length,
  };
}

function captureNetworkFailures(page) {
  const failures = [];
  const listener = (request) => failures.push({
    error: request.failure()?.errorText || "unknown failure",
    method: request.method(),
    path: new URL(request.url()).pathname,
  });
  page.on("requestfailed", listener);
  return {
    failures,
    stop: () => page.off("requestfailed", listener),
  };
}

async function consumeExpectedNetworkFailure(diagnostics, marks, capture, expected, label) {
  await waitFor(() => (
    diagnostics.requestFailures.length === marks.request + 1
      && diagnostics.consoleErrors.length === marks.console + 1
      && diagnostics.classifiedConsoleDiagnostics.length === marks.classified + 1
      && capture.failures.length === 1
  ), `${label} diagnostic accounting`);
  diagnostics.requestFailures.splice(marks.request, 1);
  const consoleFailure = diagnostics.consoleErrors.splice(marks.console, 1)[0] || "";
  const classified = diagnostics.classifiedConsoleDiagnostics.splice(marks.classified, 1)[0];
  capture.stop();
  const requestFailure = capture.failures[0];
  assert(requestFailure.method === expected.method
    && requestFailure.path === expected.path
    && requestFailure.error.includes(expected.error),
    `${label} produced an unrelated request failure`);
  assert(consoleFailure.includes("unclassified-console-error"),
    `${label} did not produce the expected Chromium console failure`);
  assert(classified?.context === "unclassified-console-error"
    && classified.method === "UNKNOWN"
    && classified.path === "unknown"
    && classified.status == null,
  `${label} console failure was unexpectedly classified as an HTTP response`);
}

async function runOfflineProfile(options) {
  const guarded = await createGuardedContext(
    options.browser,
    options.devices,
    "desktop",
    options.artifactDir,
    "phase-six-admin-desktop",
    { baseUrl: options.baseUrl },
  );
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page, {
    authType: "session",
    role: "admin",
    username: options.username,
  });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile: "desktop",
    requests: [],
    role: "admin",
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const auth = await authenticate(page, options.username, options.password);
    recorder.setGardenId(auth.garden_id);
    guarded.markAuthenticated();
    await dismissProactivePasskeyPrompt(page);
    await selectGarden(page, options.fixture.gardens.alpha.id);

    const title = options.oracle.phase_six.fixture.journal_title;
    const offlineQueueFailureMarks = diagnosticMarks(guarded.diagnostics);
    const offlineQueueFailureCapture = captureNetworkFailures(page);
    const queued = await enqueueOfflineJournal(page, options.fixture, title);
    await page.waitForTimeout(200);
    offlineQueueFailureCapture.stop();
    assert(guarded.diagnostics.requestFailures.length === offlineQueueFailureMarks.request
      && guarded.diagnostics.consoleErrors.length === offlineQueueFailureMarks.console
      && guarded.diagnostics.classifiedConsoleDiagnostics.length === offlineQueueFailureMarks.classified
      && offlineQueueFailureCapture.failures.length === 0,
    "Phase 6 offline queue attempted a network request");
    assert(queued.garden_id === options.fixture.gardens.alpha.id,
      "Phase 6 queued draft lost its source garden");
    assert(/^[0-9a-f-]{36}$/.test(queued.operation_id),
      "Phase 6 queued draft lacks an operation ID");

    let deliveryCount = 0;
    let firstResponse = null;
    let releaseFirst;
    let releaseReplay;
    const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
    const replayGate = new Promise((resolve) => { releaseReplay = resolve; });
    let firstCaptured;
    let replayCaptured;
    const firstSeen = new Promise((resolve) => { firstCaptured = resolve; });
    const replaySeen = new Promise((resolve) => { replayCaptured = resolve; });
    const lostAckRoute = async (route) => {
      const request = route.request();
      if (request.method() !== "POST"
        || request.headers()["x-offline-operation-id"] !== queued.operation_id) {
        await route.continue();
        return;
      }
      deliveryCount += 1;
      if (deliveryCount === 1) {
        firstCaptured();
        await firstGate;
        const response = await route.fetch();
        firstResponse = { body: await response.json(), status: response.status() };
        await route.abort("failed");
        return;
      }
      replayCaptured();
      await replayGate;
      await route.continue();
    };
    await page.route("**/api/journal", lostAckRoute);
    const lostAckFailureMarks = diagnosticMarks(guarded.diagnostics);
    const lostAckFailureCapture = captureNetworkFailures(page);
    await page.context().setOffline(false);
    await firstSeen;
    await waitFor(async () => (await readDrafts(page))[0]?.status === "syncing",
      "Phase 6 retrying queue state");
    await visible(page.locator("#offline-indicator .offline-indicator--syncing"),
      "Phase 6 visible retrying state");
    await selectGarden(page, options.fixture.gardens.beta.id);
    releaseFirst();
    await replaySeen;
    assert(firstResponse?.status === 201 && firstResponse.body?.id,
      "Phase 6 lost-ack mutation did not commit before response drop");

    const alphaState = await browserJson(page, "/api/journal?limit=100", options.fixture.gardens.alpha.id);
    const betaState = await browserJson(page, "/api/journal?limit=100", options.fixture.gardens.beta.id);
    const entries = (payload) => payload?.entries || payload?.items || payload || [];
    assert(alphaState.status === 200 && entries(alphaState.body).some((entry) => entry.title === title),
      "Phase 6 independent postcondition did not observe the committed journal");
    assert(betaState.status === 200 && !entries(betaState.body).some((entry) => entry.title === title),
      "Phase 6 Garden A draft replayed into Garden B");
    releaseReplay();
    await waitFor(async () => (await readDrafts(page)).length === 0, "Phase 6 replay queue drain");
    await consumeExpectedNetworkFailure(
      guarded.diagnostics,
      lostAckFailureMarks,
      lostAckFailureCapture,
      { error: "ERR_FAILED", method: "POST", path: "/api/journal" },
      "Phase 6 lost acknowledgement",
    );
    await page.unroute("**/api/journal", lostAckRoute);

    for (let reconnect = 0; reconnect < 2; reconnect += 1) {
      await page.context().setOffline(true);
      await page.context().setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
    }
    await page.waitForTimeout(400);
    assert(deliveryCount === 2, "Phase 6 repeated reconnect duplicated the journal mutation");

    const failedGarden = options.fixture.gardens.beta.id;
    await insertFailedDrafts(page, failedGarden, queued.payload);
    const failureToggle = page.locator("#offline-indicator .offline-indicator-toggle");
    await visible(failureToggle, "Phase 6 compact failed-work toggle");
    assert(await failureToggle.getAttribute("aria-expanded") === "false",
      "Phase 6 failed-work recovery was not collapsed by default");
    assert(await page.locator("#offline-failures-panel").isHidden(),
      "Phase 6 failed-work recovery panel covered the app by default");
    await failureToggle.click();
    await waitFor(async () => await failureToggle.getAttribute("aria-expanded") === "true",
      "Phase 6 failed-work recovery expansion");
    const failures = page.locator("#offline-indicator .offline-failure-row");
    await waitFor(async () => await failures.count() === 5, "Phase 6 five-family failed state");
    assert(await failures.getByRole("button", { name: "Retry as new", exact: true }).count() === 3,
      "Phase 6 recreatable terminal failures were not explicitly recoverable as new operations");
    assert(await failures.getByRole("button", { name: "Discard", exact: true }).count() === 5,
      "Phase 6 terminal failures were not discardable");
    for (const label of [
      "Conflicting journal observation",
      "Removed issue target",
      "Conflicting harvest record",
      "Removed care task",
      "Removed plant: removed-target.jpg",
    ]) {
      await visible(failures.filter({ hasText: label }).first(), `Phase 6 failed ${label}`);
    }

    const conflictBefore = (await readDrafts(page)).find((draft) => (
      draft.payload?.title === "Conflicting journal observation"
    ));
    assert(conflictBefore, "Phase 6 conflict draft is missing before recovery");
    await page.context().setOffline(true);
    await failures.filter({ hasText: "Conflicting journal observation" })
      .getByRole("button", { name: "Retry as new", exact: true }).click();
    await waitFor(async () => {
      const recovered = (await readDrafts(page)).find((draft) => draft.id === conflictBefore.id);
      return recovered?.status === "pending"
        && recovered.operation_id !== conflictBefore.operation_id;
    }, "Phase 6 explicit retry-as-new identity renewal");
    await page.context().setOffline(false);
    await waitFor(async () => (await readDrafts(page)).length === 4,
      "Phase 6 retry-as-new replacement delivery");
    const betaReplacement = await browserJson(
      page,
      "/api/journal?limit=100",
      options.fixture.gardens.beta.id,
    );
    const betaReplacementEntries = betaReplacement.body?.entries
      || betaReplacement.body?.items
      || betaReplacement.body
      || [];
    assert(betaReplacement.status === 200
      && betaReplacementEntries.filter((entry) => (
        entry.title === "Conflicting journal observation"
      )).length === 1,
    "Phase 6 retry-as-new did not create exactly one replacement in its source garden");
    await waitFor(async () => await failures.count() === 4,
      "Phase 6 recovered draft removal from failed work");
    const rerenderedFailureToggle = page.locator("#offline-indicator .offline-indicator-toggle");
    assert(await rerenderedFailureToggle.getAttribute("aria-expanded") === "true",
      "Phase 6 failed-work recovery closed while the user was handling failures");
    await rerenderedFailureToggle.click();
    await waitFor(async () => (
      await page.locator("#offline-indicator .offline-indicator-toggle").getAttribute("aria-expanded")
    ) === "false", "Phase 6 failed-work recovery returns to compact state");

    const signOut = page.locator("#auth-btn:visible");
    guarded.markSignedOut();
    await signOut.click();
    await visible(page.locator("#auth-gate-form"), "Phase 6 sign-in after queue clear");
    assert((await readDrafts(page)).length === 0, "Phase 6 logout retained another account's drafts");
    await authenticate(page, options.username, options.password);
    guarded.markAuthenticated();

    result.structure = await assertPageStructure(page, "Phase 6 admin:desktop");
    assertDiagnosticsClean(guarded.diagnostics, "Phase 6 admin:desktop");
    result.checks = {
      account_queue_cleared_on_logout: true,
      failed_families_visible: ["journal", "issues", "harvest", "task_action", "media_upload"],
      failed_recovery_collapsed_by_default: true,
      garden_isolation: true,
      independent_postcondition: true,
      lost_ack_route_fetch: true,
      operation_id: queued.operation_id,
      reconnect_count: 3,
      replay_delivery_count: deliveryCount,
      retry_as_new_replacement_count: 1,
      retry_as_new_identity_renewed: true,
      injected_terminal_fixture_statuses: [409, 410],
    };
    result.assertions.passed.push("phase-six-offline-replay", "browser-diagnostics-clean");
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

async function runOfflineAndFailureRecovery(options, profileRunner = runOfflineProfile) {
  const outcome = await profileRunner(options);
  if (options.onProfile) options.onProfile(outcome.result);
  if (outcome.error) throw outcome.error;
  return [outcome.result];
}

module.exports = { runOfflineAndFailureRecovery };
