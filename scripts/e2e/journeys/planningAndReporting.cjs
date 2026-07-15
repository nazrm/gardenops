"use strict";

const {
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

const EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture

function phaseFour(fixture) {
  const value = fixture.phase_four;
  assert(value && typeof value === "object", "Phase 4 fixture is missing");
  return value;
}

async function browserFetch(page, gardenId, request) {
  return page.evaluate(async ({ activeGardenId, input }) => {
    const csrf = document.cookie.split("; ")
      .find((entry) => entry.startsWith("gardenops_csrf="))?.split("=").slice(1).join("=") || "";
    const headers = {
      "x-csrf-token": decodeURIComponent(csrf),
      "x-garden-id": String(activeGardenId),
      ...(input.body === undefined ? {} : { "content-type": "application/json" }),
    };
    const response = await fetch(input.path, {
      body: input.body === undefined ? undefined : JSON.stringify(input.body),
      credentials: "include",
      headers,
      method: input.method || "GET",
    });
    const text = await response.text();
    let body = text;
    try { body = text ? JSON.parse(text) : null; } catch { /* export bodies may be text */ }
    return {
      body,
      contentType: response.headers.get("content-type") || "",
      disposition: response.headers.get("content-disposition") || "",
      status: response.status,
    };
  }, { activeGardenId: gardenId, input: request });
}

async function ok(page, gardenId, request, expectedStatus = 200) {
  const response = await browserFetch(page, gardenId, request);
  assert(response.status === expectedStatus,
    `${request.method || "GET"} ${request.path} returned ${response.status}`);
  return response.body;
}

function waitForApiResponse(page, method, path) {
  return page.waitForResponse((response) => (
    response.request().method() === method
    && new URL(response.url()).pathname === path
  ));
}

async function openSubMode(page, parent, mode, panel) {
  await page.locator(`[data-tab='${parent}']:visible`).first().click();
  const button = page.locator(`[data-sub-mode='${mode}']:visible`).first();
  await visible(button, `${mode} sub-mode`);
  await button.click();
  await visible(page.locator(panel), `${mode} panel`);
}

async function openInsightsMode(page, mode, panel) {
  await page.locator("[data-tab='insights']:visible").first().click();
  const button = page.locator(`#stats-mode-${mode}:visible`);
  await visible(button, `${mode} statistics mode`);
  await button.click();
  await visible(page.locator(panel), `${mode} statistics panel`);
}

async function readDownload(download, label) {
  const stream = await download.createReadStream();
  assert(stream, `${label} download stream is unavailable`);
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8");
  await download.delete();
  return text;
}

function parseCsv(text) {
  const rows = [];
  let field = "";
  let row = [];
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted && char === '"' && text[index + 1] === '"') {
      field += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (!quoted && char === ",") {
      row.push(field); field = "";
    } else if (!quoted && (char === "\n" || char === "\r")) {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      row.push(field); field = "";
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
    } else {
      field += char;
    }
  }
  assert(!quoted, "CSV download ended inside a quoted field");
  if (field || row.length) { row.push(field); rows.push(row); }
  const header = rows.shift() || [];
  return rows.map((values) => Object.fromEntries(header.map((key, index) => [key, values[index] || ""])));
}

function assertNoSecrets(text, fixture) {
  for (const forbidden of phaseFour(fixture).forbidden_export_fragments) {
    assert(!text.toLowerCase().includes(forbidden.toLowerCase()),
      `Phase 4 export leaked forbidden content: ${forbidden}`);
  }
}

async function downloadFrom(page, diagnostics, selector, label) {
  const failureMark = diagnostics.requestFailures.length;
  const expectedAbortMark = diagnostics.expectedRequestAborts.length;
  const aborted = [];
  const listener = (request) => {
    if ((request.failure()?.errorText || "").includes("ERR_ABORTED")) aborted.push(request.url());
  };
  page.on("requestfailed", listener);
  try {
    const pending = page.waitForEvent("download");
    const control = typeof selector === "string" ? page.locator(selector) : selector;
    await control.click();
    const download = await pending;
    assert(aborted.length <= 1, `${label} produced duplicate download aborts`);
    return await readDownload(download, label);
  } finally {
    page.off("requestfailed", listener);
    const failuresAdded = diagnostics.requestFailures.length - failureMark;
    const expectedAdded = diagnostics.expectedRequestAborts.length - expectedAbortMark;
    assert(failuresAdded + expectedAdded === aborted.length,
      `${label} download failure accounting diverged`);
    diagnostics.requestFailures.splice(failureMark, failuresAdded);
  }
}

async function createInventoryLedgerThroughUi(page, fixture) {
  const spec = phaseFour(fixture).inventory;
  const gardenId = fixture.gardens.alpha.id;
  await openSubMode(page, "garden", "inventory", "#inventory-tab-content");
  await page.locator("#inventory-add-btn:visible").click();
  const itemDialog = page.locator(".inventory-modal:visible");
  await visible(itemDialog, "inventory create dialog");
  await itemDialog.locator("#inv-label").fill(spec.label);
  await itemDialog.locator("#inv-type").selectOption(spec.inventory_type);
  await itemDialog.locator("#inv-unit").fill(spec.unit);
  const createPending = waitForApiResponse(page, "POST", "/api/inventory");
  await itemDialog.locator("button[type='submit']").click();
  const createResponse = await createPending;
  assert(createResponse.status() === 201, "Inventory UI create did not return 201");
  const created = await createResponse.json();
  await waitFor(async () => await page.locator(".inventory-modal").count() === 0,
    "inventory create dialog to close");
  await visible(
    page.locator("#inventory-table-body tr[data-item-id]").filter({ hasText: spec.label }).first(),
    "created inventory row",
  );

  for (const transaction of spec.transactions) {
    const row = page.locator("#inventory-table-body tr[data-item-id]")
      .filter({ hasText: spec.label }).first();
    await row.locator(transaction.delta > 0 ? ".inventory-action-add" : ".inventory-action-use").click();
    const transactionDialog = page.locator(".inventory-modal:visible");
    await visible(transactionDialog, "inventory transaction dialog");
    await transactionDialog.locator("#inv-tx-qty").fill(String(Math.abs(transaction.delta)));
    await transactionDialog.locator("#inv-tx-reason").selectOption(transaction.reason);
    await transactionDialog.locator("#inv-tx-date").fill(phaseFour(fixture).date);
    const source = transactionDialog.locator("#inv-tx-source");
    if (await source.count()) await source.fill(transaction.source_name);
    const path = `/api/inventory/${encodeURIComponent(created.id)}/transactions`;
    const transactionPending = waitForApiResponse(page, "POST", path);
    await transactionDialog.locator("button[type='submit']").click();
    const transactionResponse = await transactionPending;
    assert(transactionResponse.status() === 201,
      `Inventory UI transaction returned ${transactionResponse.status()}`);
    await waitFor(async () => await page.locator(".inventory-modal").count() === 0,
      "inventory transaction dialog to close");
  }
  const item = await ok(page, gardenId, {
    path: `/api/inventory/${encodeURIComponent(created.id)}`,
  });
  const history = await ok(page, gardenId, {
    path: `/api/inventory/${encodeURIComponent(created.id)}/transactions`,
  });
  assert(item.quantity === spec.expected_quantity, "Inventory quantity did not equal the exact ledger sum");
  assert(history.total === 3, "Inventory ledger did not retain exactly three corrections");
  const sum = history.transactions.reduce((total, row) => total + Number(row.delta), 0);
  assert(sum === spec.expected_quantity, "Inventory transaction deltas did not sum exactly");
  return created.id;
}

async function createProcurementLifecycleThroughUi(page, fixture) {
  const spec = phaseFour(fixture).procurement;
  const gardenId = fixture.gardens.alpha.id;
  await openSubMode(page, "garden", "procurement", "#procurement-tab-content");
  await page.locator("#procurement-add-btn:visible").click();
  const dialog = page.locator(".procurement-modal:visible");
  await visible(dialog, "procurement create dialog");
  await dialog.locator("#procurement-label").fill(spec.label);
  await dialog.locator("#procurement-type").selectOption(spec.inventory_type);
  await dialog.locator("#procurement-vendor").fill(spec.vendor_name);
  await dialog.locator("#procurement-quantity").fill(String(spec.quantity));
  await dialog.locator("#procurement-unit").fill(spec.unit);
  await dialog.locator("#procurement-ordered-on").fill(spec.ordered_on);
  const createPending = waitForApiResponse(page, "POST", "/api/procurement");
  await dialog.locator("#procurement-save-btn").click();
  const createResponse = await createPending;
  assert(createResponse.status() === 201, "Procurement UI create did not return 201");
  const created = await createResponse.json();
  await waitFor(async () => await page.locator(".procurement-modal").count() === 0,
    "procurement create dialog to close");

  const transitionLabels = {
    ordered: "Mark Ordered",
    shipped: "Mark Shipped",
    received: "Mark Received",
  };
  for (const toStatus of spec.transitions.slice(0, 3)) {
    const card = page.locator(".procurement-card").filter({ hasText: spec.label }).first();
    await visible(card, `procurement ${toStatus} card`);
    const path = `/api/procurement/${encodeURIComponent(created.id)}/transition`;
    const transitionPending = waitForApiResponse(page, "POST", path);
    await card.getByRole("button", { name: transitionLabels[toStatus], exact: true }).click();
    const transitionResponse = await transitionPending;
    assert(transitionResponse.status() === 200,
      `Procurement UI ${toStatus} transition returned ${transitionResponse.status()}`);
    await waitFor(async () => (
      await page.locator(`.procurement-card.status-${toStatus}`)
        .filter({ hasText: spec.label }).count()
    ) === 1, `procurement ${toStatus} state`);
  }

  await ok(page, gardenId, {
    body: { received_on: spec.received_on, to_status: "received" },
    method: "POST",
    path: `/api/procurement/${encodeURIComponent(created.id)}/transition`,
  });
  const item = await ok(page, gardenId, {
    path: `/api/procurement/${encodeURIComponent(created.id)}`,
  });
  assert(item.status === "received", "Procurement lifecycle did not finish received");
  assert(typeof item.metadata?.inventory_item_id === "string"
    && Number.isSafeInteger(item.metadata?.inventory_transaction_id),
    "Received procurement item omitted durable receipt provenance");
  const inventory = await ok(page, gardenId, {
    path: `/api/inventory/${encodeURIComponent(item.metadata.inventory_item_id)}/transactions`,
  });
  assert(inventory.total === 1 && inventory.transactions[0].delta === spec.quantity,
    "Repeated received transition created a duplicate or incorrect receipt transaction");
  return created.id;
}

async function exercisePlannerAndReportsThroughUi(page, fixture) {
  const gardenId = fixture.gardens.alpha.id;
  const goal = phaseFour(fixture).planner_goal;
  await openInsightsMode(page, "planner", "#planner-dashboard");
  const goalLabels = {
    color: "Color",
    deer: "Deer-safe",
    edible: "Edible",
    low_maintenance: "Low care",
    shade: "Shade",
  };
  const goalPending = waitForApiResponse(page, "PUT", "/api/planner/goal");
  await page.locator(".planner-goal-btn").getByText(goalLabels[goal], { exact: true }).click();
  const goalResponse = await goalPending;
  assert(goalResponse.status() === 200, "Planner goal UI save did not return 200");
  await waitFor(async () => (
    await page.locator(".planner-goal-btn.active").innerText()
  ) === goalLabels[goal], "planner goal active state");
  const saved = await ok(page, gardenId, { path: "/api/planner/goal" });
  assert(saved.goal === goal, "Planner goal did not persist");

  const available = await ok(page, gardenId, { path: "/api/workflows/available" });
  assert(Array.isArray(available.workflows) && available.workflows.length > 0,
    "No supported seasonal workflow was available");
  const workflow = available.workflows[0];
  const selected = workflow.steps.slice(0, Math.min(2, workflow.steps.length)).map((step) => step.id);
  assert(selected.length > 0, "Supported workflow did not expose selectable steps");
  const workflowCard = page.locator(".workflow-card").filter({ hasText: workflow.name }).first();
  await visible(workflowCard, "supported seasonal workflow");
  const checkboxes = workflowCard.locator("input[type='checkbox']");
  for (let index = selected.length; index < await checkboxes.count(); index += 1) {
    await checkboxes.nth(index).uncheck();
  }
  const workflowPending = waitForApiResponse(page, "POST", "/api/workflows/start");
  await workflowCard.getByRole("button", { name: "Start workflow", exact: true }).click();
  const workflowResponse = await workflowPending;
  assert(workflowResponse.status() === 200, "Workflow UI start did not return 200");
  const first = await workflowResponse.json();
  const second = await ok(page, gardenId, {
    body: { selected_steps: selected, workflow_id: workflow.id },
    method: "POST", path: "/api/workflows/start",
  });
  assert(first.created === selected.length && first.skipped === 0,
    "First workflow start did not create each selected task exactly once");
  assert(second.created === 0 && second.skipped === selected.length,
    "Repeated workflow start was not idempotent");

  const reports = await ok(page, gardenId, { path: "/api/statistics/reports" });
  for (const key of ["overdue_tasks_count", "due_this_week_count", "open_issues_count"]) {
    assert(Number.isSafeInteger(reports.needs_attention[key]) && reports.needs_attention[key] >= 0,
      `Report total is invalid: ${key}`);
  }
  let scopedReport = null;
  if (reports.available_zones.length > 0) {
    const zone = reports.available_zones[0].zone_code;
    const scoped = await ok(page, gardenId, {
      path: `/api/statistics/reports?zone_code=${encodeURIComponent(zone)}`,
    });
    assert(scoped.zone_code === zone, "Zone report did not retain its requested scope");
    assert(scoped.plot_use.total_plots === reports.available_zones[0].plot_count,
      "Zone report plot total diverged from its source rows");
    scopedReport = scoped;
  }

  await visible(page.locator(".planner-goal-btn").getByText("All", { exact: true }),
    "planner all-goals control after workflow refresh");
  const clearPending = waitForApiResponse(page, "PUT", "/api/planner/goal");
  await page.locator(".planner-goal-btn").getByText("All", { exact: true }).click();
  const clearResponse = await clearPending;
  assert(clearResponse.status() === 200, "Planner goal UI clear did not return 200");
  const cleared = await ok(page, gardenId, { path: "/api/planner/goal" });
  assert(cleared.goal === null, "Planner goal clear did not persist");
  return {
    report: reports,
    scopedReport,
    workflowId: workflow.id,
    workflowSteps: selected,
  };
}

async function exerciseDownloads(page, diagnostics, fixture) {
  await openSubMode(page, "garden", "inventory", "#inventory-tab-content");
  await visible(page.locator("#inventory-table-body").getByText(phaseFour(fixture).inventory.label),
    "Phase 4 inventory row");
  const csvText = await downloadFrom(
    page, diagnostics,
    page.locator("#inventory-export-bar").getByRole("button", { name: "Download CSV" }),
    "inventory CSV",
  );
  const csvRows = parseCsv(csvText);
  const csvMatch = csvRows.find((row) => row.label === `'${phaseFour(fixture).inventory.label}`);
  assert(csvMatch && Number(csvMatch.quantity) === phaseFour(fixture).inventory.expected_quantity,
    "CSV export lost formula escaping, quoting, or exact inventory quantity");
  assert(csvRows.every((row) => !String(row.label).includes(phaseFour(fixture).procurement.label)),
    "Inventory CSV included an unrelated resource row");
  assertNoSecrets(csvText, fixture);

  const jsonText = await downloadFrom(
    page, diagnostics,
    page.locator("#inventory-export-bar").getByRole("button", { name: "Download JSON" }),
    "inventory JSON",
  );
  const parsed = JSON.parse(jsonText);
  assert(Array.isArray(parsed.inventory), "Inventory JSON export was not structurally valid");
  const jsonMatch = parsed.inventory.find((row) => row.label === phaseFour(fixture).inventory.label);
  assert(jsonMatch && jsonMatch.quantity === phaseFour(fixture).inventory.expected_quantity,
    "Inventory JSON export lost exact quantity or garden scope");
  assertNoSecrets(jsonText, fixture);

  await openSubMode(page, "activity", "calendar", "#calendar-tab-content");
  const icsText = await downloadFrom(
    page, diagnostics, "#calendar-export-btn", "calendar ICS",
  );
  assert(icsText.startsWith("BEGIN:VCALENDAR\r\n") && icsText.includes("END:VCALENDAR\r\n"),
    "ICS export did not have a complete calendar structure");
  const seededDate = fixture.phase_two.calendar.seeded_event_on.replaceAll("-", "");
  assert(icsText.includes(`DTSTART;VALUE=DATE:${seededDate}`),
    "ICS export omitted the exact seeded calendar date");
  assert(icsText.includes(fixture.phase_two.calendar.seeded_title),
    "ICS export omitted the exact garden-scoped calendar event");
  assertNoSecrets(icsText, fixture);
  return { csv_rows: csvRows.length, ics_events: (icsText.match(/BEGIN:VEVENT/g) || []).length };
}

async function exerciseCatalogue(page, fixture) {
  const gardenId = fixture.gardens.alpha.id;
  const local = await ok(page, gardenId, { path: "/api/plants?limit=5&offset=0" });
  assert(Array.isArray(local.items || local.plants || local), "Local plant catalogue response is invalid");
  const external = await ok(page, gardenId, { path: "/api/external-plants?q=rose" });
  assert(Array.isArray(external) && external.length === 0,
    "External catalogue must remain explicitly not applicable until a public species catalogue exists");
  await openSubMode(page, "insights", "care", "#care-view");
  await visible(page.locator("#care-view:visible"), "care surface");

  if (!await page.locator(".global-search-input:visible").count()) {
    const utility = page.locator("#mobile-utility-btn:visible");
    if (await utility.count()) {
      await utility.click();
      await waitFor(async () => await page.locator("body.mobile-utility-open").count() === 1,
        "mobile catalogue search controls");
    }
  }
  const search = page.locator(".global-search-input:visible").first();
  if (await search.count()) {
    const dropdownId = await search.getAttribute("data-dropdown-id");
    assert(dropdownId, "Visible catalogue search omitted its dropdown target");
    await search.fill(fixture.gardens.alpha.plant_name);
    await visible(page.locator(`#${dropdownId} .dropdown-item`).first(),
      "local catalogue search result");
    if (await page.locator("body.mobile-utility-open").count()) {
      await page.locator("#mobile-utility-close-btn").click();
      await waitFor(async () => await page.locator("body.mobile-utility-open").count() === 0,
        "mobile catalogue controls to close");
    }
  }
}

async function exerciseCorrectiveNavigation(page) {
  await openInsightsMode(page, "reports", "#reports-dashboard");
  const action = page.locator(".report-action-card:not(.is-disabled):not([disabled])").first();
  if (await action.count() === 0) return { supported: false };
  await action.click();
  await waitFor(async () => (
    await page.locator("#tasks-tab-content:visible, #issues-tab-content:visible, #care-view:visible, #map-view:visible")
      .count()
  ) > 0, "report corrective navigation target");
  return { supported: true };
}

async function exerciseEditorWriteBoundary(page, fixture) {
  const gardenId = fixture.gardens.alpha.id;
  const inventory = await ok(page, gardenId, {
    body: { inventory_type: "other", label: "Phase 4 editor temporary item", unit: "pieces" },
    method: "POST", path: "/api/inventory",
  }, 201);
  await ok(page, gardenId, {
    method: "DELETE", path: `/api/inventory/${encodeURIComponent(inventory.id)}`,
  });
  const procurement = await ok(page, gardenId, {
    body: {
      inventory_type: "other",
      label: "Phase 4 editor temporary procurement",
      quantity: 1,
      unit: "pieces",
    },
    method: "POST", path: "/api/procurement",
  }, 201);
  await ok(page, gardenId, {
    method: "DELETE", path: `/api/procurement/${encodeURIComponent(procurement.id)}`,
  });
  await ok(page, gardenId, {
    body: { goal: "shade" }, method: "PUT", path: "/api/planner/goal",
  });
  await ok(page, gardenId, {
    body: { goal: null }, method: "PUT", path: "/api/planner/goal",
  });
  await openSubMode(page, "insights", "care", "#care-view");
  const generateCare = page.locator("#generate-care-btn:visible");
  await visible(generateCare, "editor care generation control");
  assert(!await generateCare.isDisabled(), "Editor care generation control was disabled");
}

async function assertViewerForbidden(page, diagnostics, gardenId, request) {
  const beforeHttp = diagnostics.httpErrors.length;
  const beforeConsole = diagnostics.consoleErrors.length;
  const beforeClassified = diagnostics.classifiedConsoleDiagnostics.length;
  const response = await browserFetch(page, gardenId, request);
  assert(response.status === 403, `Viewer direct write was not forbidden: ${request.path}`);
  await waitFor(() => diagnostics.httpErrors.length === beforeHttp + 1,
    `viewer forbidden response ${request.path}`);
  const errors = diagnostics.httpErrors.splice(beforeHttp);
  assert(JSON.stringify(errors) === JSON.stringify([`403 ${request.path}`]),
    `Viewer write diagnostics were unexpected: ${request.path}`);
  await waitFor(() => diagnostics.consoleErrors.length >= beforeConsole + 1,
    `viewer forbidden console response ${request.path}`);
  diagnostics.consoleErrors.splice(beforeConsole);
  diagnostics.classifiedConsoleDiagnostics.splice(beforeClassified);
}

async function exerciseViewer(page, diagnostics, fixture, profile) {
  await openSubMode(page, "garden", "inventory", "#inventory-tab-content");
  assert(await page.locator("#inventory-add-btn:visible").count() === 0
    || await page.locator("#inventory-add-btn").isDisabled(),
  `${profile} viewer retained inventory create access`);
  assert(await page.locator(".inventory-action-add:visible, .inventory-action-use:visible").count() === 0,
    `${profile} viewer retained inventory mutation controls`);
  await openSubMode(page, "garden", "procurement", "#procurement-tab-content");
  assert(await page.locator("#procurement-add-btn:visible").count() === 0
    || await page.locator("#procurement-add-btn").isDisabled(),
  `${profile} viewer retained procurement create access`);
  assert(await page.locator(".procurement-action-transition:visible").count() === 0,
    `${profile} viewer retained procurement transition controls`);
  await openSubMode(page, "insights", "care", "#care-view");
  const generateCare = page.locator("#generate-care-btn:visible");
  if (await generateCare.count()) {
    assert(await generateCare.isDisabled(), `${profile} viewer retained care generation access`);
  }
  const gardenId = fixture.gardens.alpha.id;
  for (const request of [
    { body: { inventory_type: "seed", label: "viewer denied", unit: "kg" }, method: "POST", path: "/api/inventory" },
    { body: { inventory_type: "seed", label: "viewer denied", quantity: 1, unit: "pieces" }, method: "POST", path: "/api/procurement" },
    {
      body: { selected_steps: ["review_growth"], workflow_id: "midsummer_check" },
      method: "POST",
      path: "/api/workflows/start",
    },
  ]) await assertViewerForbidden(page, diagnostics, gardenId, request);
}

async function exerciseMobileDenseControls(page, fixture) {
  await openSubMode(page, "garden", "inventory", "#inventory-tab-content");
  const card = page.locator("#inventory-mobile-list .inventory-card")
    .filter({ hasText: phaseFour(fixture).inventory.label }).first();
  await visible(card, "mobile inventory card");
  for (const selector of [".inventory-action-history", ".inventory-action-add", ".inventory-action-use"]) {
    const control = card.locator(selector);
    await visible(control, `mobile inventory control ${selector}`);
    const box = await control.boundingBox();
    assert(box && box.width >= 24 && box.height >= 24, `Mobile dense control collapsed: ${selector}`);
  }
  await openSubMode(page, "garden", "procurement", "#procurement-tab-content");
  await visible(page.locator(".procurement-card").filter({ hasText: phaseFour(fixture).procurement.label }),
    "mobile procurement card");
  await openInsightsMode(page, "planner", "#planner-dashboard");
  await visible(page.locator(".planner-goal-btn").first(), "mobile planner goal controls");
  await openInsightsMode(page, "reports", "#reports-dashboard");
  await visible(page.locator(".reports-zone-filter select"), "mobile report zone control");
}

async function exerciseDelayedGardenResponses(page, fixture) {
  const alphaId = String(fixture.gardens.alpha.id);
  const betaId = String(fixture.gardens.beta.id);
  const surfaces = [
    ["**/api/inventory*", "garden", "inventory", "#inventory-tab-content"],
    ["**/api/procurement*", "garden", "procurement", "#procurement-tab-content"],
    ["**/api/planner/suggestions*", "insights", "planner", "#planner-dashboard"],
    ["**/api/statistics/reports*", "insights", "reports", "#reports-dashboard"],
  ];
  for (const [pattern, parent, mode, panel] of surfaces) {
    let release;
    const delayed = new Promise((resolve) => { release = resolve; });
    let captured = false;
    await page.route(pattern, async (route) => {
      if (!captured && route.request().headers()["x-garden-id"] === alphaId) {
        captured = true;
        await delayed;
      }
      await route.continue();
    });
    if (parent === "garden") await openSubMode(page, parent, mode, panel);
    else await openInsightsMode(page, mode, panel);
    await waitFor(() => captured, `${mode} delayed Alpha request`);
    const selector = page.locator("#mobile-garden-select");
    if (!await selector.isVisible()) {
      await page.locator("#mobile-utility-btn").click();
      await waitFor(async () => await page.locator("body.mobile-utility-open").count() === 1,
        `${mode} mobile utility sheet`);
    }
    await visible(selector, `${mode} mobile garden selector`);
    await selector.selectOption(betaId);
    release();
    await waitFor(async () => await selector.inputValue() === betaId, `${mode} Beta selection`);
    await page.waitForLoadState("networkidle");
    const text = await page.locator(panel).innerText();
    assert(!text.includes(phaseFour(fixture).inventory.label)
      && !text.includes(phaseFour(fixture).procurement.label),
    `${mode} delayed Alpha response replaced current Beta state`);
    await page.unroute(pattern);
    await selector.selectOption(alphaId);
    await page.waitForLoadState("networkidle");
    if (await page.locator("body.mobile-utility-open").count()) {
      await page.locator("#mobile-utility-btn").click();
      await waitFor(async () => await page.locator("body.mobile-utility-open").count() === 0,
        `${mode} mobile utility close`);
    }
  }
}

async function runProfile(options) {
  const { artifactDir, baseUrl, browser, devices, fixture, profile, role } = options;
  const login = role === "admin"
    ? [options.username, options.password]
    : role === "editor"
      ? [fixture.roles.editor, EDITOR_PASSWORD]
      : [fixture.roles.viewer, VIEWER_PASSWORD];
  const guarded = await createGuardedContext(
    browser, devices, profile, artifactDir, `phase-four-${role}-${profile}`,
  );
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page, { authType: "session", role, username: login[0] });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile,
    requests: [],
    role,
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const auth = await authenticate(page, login[0], login[1]);
    guarded.markAuthenticated();
    assert(auth.role === role, `Phase 4 ${role} fixture role drifted`);
    if (role === "admin" && profile === "desktop") {
      result.checks.inventory_item_id = await createInventoryLedgerThroughUi(page, fixture);
      result.checks.procurement_item_id = await createProcurementLifecycleThroughUi(page, fixture);
      result.checks.planner = await exercisePlannerAndReportsThroughUi(page, fixture);
      result.checks.downloads = await exerciseDownloads(page, guarded.diagnostics, fixture);
      result.checks.corrective_navigation = await exerciseCorrectiveNavigation(page);
      await exerciseCatalogue(page, fixture);
      result.checks.admin_desktop = true;
    } else if (role === "admin") {
      await exerciseMobileDenseControls(page, fixture);
      result.checks.downloads = await exerciseDownloads(page, guarded.diagnostics, fixture);
      await exerciseCatalogue(page, fixture);
      await exerciseDelayedGardenResponses(page, fixture);
      result.checks.mobile_catalogue_and_care = true;
      result.checks.mobile_dense_controls = true;
      result.checks.delayed_garden_responses = true;
    } else if (role === "editor") {
      await exerciseEditorWriteBoundary(page, fixture);
      await exerciseCatalogue(page, fixture);
      await openInsightsMode(page, "planner", "#planner-dashboard");
      await visible(page.locator(".planner-goal-btn").first(), "editor planner controls");
      result.checks.editor_supported_read_surfaces = true;
      result.checks.editor_write_boundary = true;
    } else {
      await exerciseViewer(page, guarded.diagnostics, fixture, profile);
      result.checks.viewer_ui_denial = true;
      result.checks.viewer_direct_write_denial = true;
    }
    result.structure = await assertPageStructure(page, `Phase 4 ${role}:${profile}`, {
      enforceControlNames: false,
    });
    assertDiagnosticsClean(guarded.diagnostics, `Phase 4 ${role}:${profile}`);
    result.checks.browser_diagnostics = true;
    result.checks.last_completed_step = `${role}-${profile}-complete`;
    result.assertions.passed.push("phase-four-profile-contract", "browser-diagnostics-clean");
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

async function runPlanningAndReporting(options, profileRunner = runProfile) {
  const profiles = [
    ["admin", "desktop"],
    ["editor", "desktop"],
    ["admin", "mobile"],
    ["viewer", "desktop"],
    ["viewer", "mobile"],
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

module.exports = {
  parseCsv,
  runPlanningAndReporting,
};
