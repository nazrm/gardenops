#!/usr/bin/env node
"use strict";

// Run only after integration readiness:
// env PATH=/usr/lib/postgresql/17/bin:/usr/local/bin:/usr/bin:/bin \
//   .venv/bin/python scripts/run_fast_postgres_tests.py --command \
//   --command-database gardenops_task_history_e2e_test -- \
//   env GARDENOPS_EXPERIENCE_E2E=1 bash scripts/run_task_completion_history_e2e.sh
// The original task-history checker runs first. This checker never reseeds SQL.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { chromium, request } = require("../frontend/node_modules/playwright-core");

const ROOT = path.resolve(__dirname, "..");
const BASE = process.env.BASE_URL || "http://127.0.0.1:5173";
const CHROMIUM_EXECUTABLE = process.env.CHROMIUM_EXECUTABLE || "/usr/bin/chromium";
const OUTPUT = path.join(ROOT, "research/experience-e2e");
const DATE = "2026-07-04";
const PLANT = "BLOOM-E2E";
const report = {
  steps: [], screenshots: [], records: [],
  notCovered: [
    "Viewer authorization (runner deliberately uses AUTH_REQUIRED=false admin)",
    "Area/container destinations, harvest/Matrix, and planner variants beyond first-use stocked planting",
    "Partial photo-upload failure, stale task replay, multi-placement bloom scope",
    "Cross-identity/garden draft isolation and quota exhaustion",
  ],
};

function guard() {
  assert.equal(process.env.APP_ENV, "test");
  assert.equal(process.env.AUTH_REQUIRED, "false");
  assert(process.env.GARDENOPS_DISPOSABLE_POSTGRES_MARKER,
    "Use run_fast_postgres_tests.py command mode, never an unmanaged app database");
  assert.equal(process.env.MEDIA_STORAGE_DIR, `${process.env.GARDENOPS_LOGS_DIR}/experience-media`,
    "Media writes must use the managed run's isolated storage, never app uploads");
  assert(["127.0.0.1", "localhost", "[::1]"].includes(new URL(BASE).hostname));
  const check = spawnSync(path.join(ROOT, ".venv/bin/python"), ["-c",
    "import os; from scripts.seed_task_completion_history_e2e import require_task_history_e2e_database; require_task_history_e2e_database(os.environ['DATABASE_URL'])"],
  { cwd: ROOT, encoding: "utf8" });
  assert.equal(check.status, 0, check.stderr);
  const ignored = spawnSync("git", ["check-ignore", "research/experience-e2e/report.json"], { cwd: ROOT });
  assert.equal(ignored.status, 0, "Screenshot/report output must remain ignored");
}

async function poll(read, predicate, label, timeout = 20000) {
  const end = performance.now() + timeout;
  let value;
  do {
    value = await read();
    if (predicate(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 150));
  } while (performance.now() < end);
  assert.fail(`${label}: ${JSON.stringify(value).slice(0, 2000)}`);
}

async function step(name, action) {
  process.stdout.write(`experience: ${name}\n`);
  try {
    await action();
    report.steps.push({ name, status: "passed" });
  } catch (error) {
    report.steps.push({ name, status: "failed", error: error.message });
    throw error;
  }
}

async function screenshot(page, name) {
  const filename = path.join(OUTPUT, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: true });
  report.screenshots.push(filename);
}

async function scrollStable(locator) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try { await locator.scrollIntoViewIfNeeded(); return; }
    catch (error) {
      if (!error.message.includes("not attached") || attempt === 2) throw error;
    }
  }
}

async function storedDrafts(page) {
  return page.evaluate(() => Object.keys(localStorage)
    .filter((key) => key.startsWith("gardenops:journal-draft:v1:"))
    .map((key) => ({ key, draft: JSON.parse(localStorage.getItem(key)) })));
}

async function fit(page, selector) {
  const result = await page.locator(selector).last().evaluate((root) => {
    const rect = root.getBoundingClientRect();
    const overflow = [...root.querySelectorAll("button, input, select, textarea")]
      .filter((el) => el.getClientRects().length && !el.closest("[hidden]"))
      .filter((el) => getComputedStyle(el).opacity !== "0")
      .filter((el) => {
        const r = el.getBoundingClientRect();
        const iconOnly = el.tagName === "BUTTON" && [...el.textContent.trim()].length <= 2;
        if (iconOnly) {
          // Expanded ::after hit targets intentionally exceed the icon button's box.
          const range = document.createRange();
          range.selectNodeContents(el);
          const glyph = range.getBoundingClientRect();
          return r.left < -2 || r.right > innerWidth + 2
            || glyph.left < r.left - 2 || glyph.right > r.right + 2
            || (getComputedStyle(el).overflowY !== "visible"
              && (glyph.top < r.top - 2 || glyph.bottom > r.bottom + 2));
        }
        return r.left < -2 || r.right > innerWidth + 2 || el.scrollWidth > el.clientWidth + 3;
      }).map((el) => ({ html: el.outerHTML.slice(0, 180), clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth, rect: el.getBoundingClientRect().toJSON() }));
    return { left: rect.left, right: rect.right, width: innerWidth,
      pageWidth: document.documentElement.scrollWidth, overflow };
  });
  assert(result.left >= -2 && result.right <= result.width + 2, JSON.stringify(result));
  assert(result.pageWidth <= result.width + 2, `Page overflow: ${JSON.stringify(result)}`);
  assert.deepEqual(result.overflow, [], "Controls must fit without clipped text");
}

async function tab(page, name) {
  // The auth-disabled warning overlays the desktop header in this test mode.
  const button = page.locator(`#top-tab-${name}:visible, #mobile-tab-${name}:visible`).first();
  await button.focus();
  await button.press("Enter");
  await page.waitForLoadState("networkidle");
}

async function submode(page, name) {
  const button = page.locator(`[data-sub-mode="${name}"]:visible`).first();
  await button.focus();
  await button.press("Enter");
  await page.waitForLoadState("networkidle");
}

async function search(page, query, result) {
  const mobile = page.viewportSize().width < 768;
  if (mobile && await page.locator("#mobile-utility-sheet").getAttribute("aria-hidden") === "true") {
    await page.locator("#mobile-utility-btn").focus();
    await page.locator("#mobile-utility-btn").press("Enter");
  }
  const input = page.locator(mobile ? "#mobile-global-plant-search" : ".global-search-input").first();
  await input.fill(query);
  const dropdown = page.locator(`#${await input.getAttribute("data-dropdown-id")}`);
  await dropdown.getByRole("option").filter({ hasText: result }).first().click();
}

function summary(page) { return page.locator(".plant-summary"); }
function composer(page) { return page.locator(".journal-composer"); }
async function installClock(page) {
  await page.addInitScript((now) => {
    const RealDate = Date;
    class FrozenDate extends RealDate {
      constructor(...args) { super(...(args.length ? args : [now])); }
      static now() { return now; }
    }
    window.Date = FrozenDate;
  }, Number(process.env.GARDENOPS_ATTENTION_FROZEN_NOW_MS));
}
async function queuedRecords(page) {
  return page.evaluate(() => new Promise((resolve, reject) => {
    const open = indexedDB.open("gardenops-offline");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      const tx = db.transaction("drafts", "readonly");
      const read = tx.objectStore("drafts").getAll();
      read.onsuccess = () => resolve(read.result);
      read.onerror = () => reject(read.error);
      tx.oncomplete = () => db.close();
      tx.onabort = () => { db.close(); reject(tx.error); };
    };
  }));
}
async function closeSummary(page) {
  await summary(page).locator(".modal-close-btn").focus();
  await summary(page).locator(".modal-close-btn").press("Enter");
  await summary(page).waitFor({ state: "hidden" });
}
async function record(page) {
  await summary(page).getByRole("button", { name: /^Record observation$/i }).click();
  await composer(page).waitFor({ state: "visible" });
}
async function assertContext(page) {
  const lists = composer(page).locator(".journal-chip-list");
  assert.match(await lists.nth(0).innerText(), /Bloom E2E/);
  assert.match(await lists.nth(1).innerText(), /A1/);
  assert.equal(await lists.nth(0).locator(".journal-chip").count(), 1);
  assert.equal(await lists.nth(1).locator(".journal-chip").count(), 1);
}

function browserErrors(page, isOffline) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (!isOffline() && message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      errors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (!isOffline() && response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
  });
  return errors;
}

async function journey(browser, api, mode) {
  const context = await browser.newContext({
    viewport: mode === "mobile" ? { width: 390, height: 844 } : { width: 1440, height: 980 },
    isMobile: mode === "mobile", hasTouch: mode === "mobile", locale: "en-GB", timezoneId: "Europe/Oslo",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  let offline = false;
  const errors = browserErrors(page, () => offline);
  await installClock(page);
  const note = `Experience ${mode} contextual note`;
  const draft = `Experience ${mode} restored draft`;
  const issueTitle = `Experience ${mode} manual issue`;
  const savedNotes = async (text) => (await api(`/api/journal?q=${encodeURIComponent(text)}&limit=100`)).entries;
  let camera;
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await step(`${mode}: plant search, plant-only context, place search and contextual summary`, async () => {
      await tab(page, "map");
      await search(page, "Bloom E2E", /^Bloom E2E$/);
      await summary(page).waitFor({ state: "visible" });
      await record(page);
      assert.equal(await composer(page).locator(".journal-chip-list").nth(1).locator(".journal-chip").count(), 0,
        "Plant-record entry must not infer a place");
      await composer(page).locator(".journal-btn-cancel").click();
      await closeSummary(page);
      await search(page, "A1", /A1/);
      await page.waitForLoadState("networkidle");
      const link = page.locator(".plant-summary-link:visible").filter({ hasText: /^Bloom E2E$/ }).first();
      await link.waitFor();
      await scrollStable(link);
      const placePanel = mode === "desktop" ? ".plot-popover" : ".bottom-sheet";
      await screenshot(page, `${mode}-00-place-details`);
      await step(`${mode}: place details compact fit`, () => fit(page, placePanel)).catch(() => {});
      await link.click();
      await summary(page).waitFor({ state: "visible" });
      camera = await page.locator("#map-camera").evaluate((el) => el.style.transform);
      await fit(page, ".plant-summary");
      await screenshot(page, `${mode}-01-summary`);
    });
    await step(`${mode}: contextual note saves exact plant/place/date and returns focus`, async () => {
      await record(page);
      await assertContext(page);
      await composer(page).locator('[name="notes"]').fill(note);
      await composer(page).locator('[name="occurred_on"]').fill(DATE);
      await fit(page, ".journal-composer");
      await screenshot(page, `${mode}-02-composer`);
      await composer(page).locator(".journal-btn-submit").click();
      await composer(page).waitFor({ state: "hidden" });
      const entries = await poll(() => savedNotes(note), (items) => items.length === 1, "Saved contextual note");
      assert.deepEqual(entries[0].plant_ids, [PLANT]);
      assert.deepEqual(entries[0].plot_ids, ["A1"]);
      assert.equal(entries[0].occurred_on, DATE);
      assert.equal(entries[0].notes, note);
      report.records.push({ journey: mode, kind: "note", id: entries[0].id });
      assert(await summary(page).getByRole("button", { name: /^Record observation$/i })
        .evaluate((el) => el === document.activeElement), "Save must restore invoking action focus");
      assert.equal(await page.locator("#map-camera").evaluate((el) => el.style.transform), camera);
    });
    await step(`${mode}: scoped history persists across filters, pagination and tab return`, async () => {
      await summary(page).getByRole("button", { name: /history/i }).first().click();
      const scope = page.locator("#journal-history-scope");
      await scope.getByText("Bloom E2E", { exact: true }).waitFor();
      await page.locator("#journal-filter-search").fill(note);
      await page.locator("#journal-list .journal-card").filter({ hasText: note }).waitFor();
      await poll(() => page.locator("#journal-list .journal-card").count(),
        (count) => count === 1, "Debounced history search has exactly one result");
      await page.locator("#journal-filter-type").selectOption("observed");
      await page.locator("#journal-filter-search").fill("");
      await poll(() => page.locator("#journal-list .journal-card").count(),
        (count) => count === 50, "Cleared search restores the first history page");
      await page.locator("#journal-pagination").getByRole("button", { name: /^Next$/i }).click();
      await poll(() => page.locator("#journal-pagination").innerText(), (text) => /2\s*(of|\/)\s*2/i.test(text), "Second history page");
      const chips = await page.locator("#journal-list .journal-tag-plant").allTextContents();
      assert(chips.length > 0 && chips.every((text) => text.trim() === "Bloom E2E"), "Pagination leaked plant scope");
      await tab(page, "map");
      await tab(page, "activity");
      assert.equal(await page.locator("#sub-mode-journal").getAttribute("aria-selected"), "true");
      await scope.getByText("Bloom E2E", { exact: true }).waitFor();
      await fit(page, "#journal-history-scope");
      await screenshot(page, `${mode}-03-scoped-history`);
      await scope.getByRole("button", { name: /return|back/i }).click();
      await summary(page).waitFor();
    });
    // Retain a failed retention assertion while allowing later independent writes to be exercised.
    await step(`${mode}: history return retains map camera`, async () => {
      assert.equal(await page.locator("#map-camera").evaluate((el) => el.style.transform), camera);
    }).catch(async () => screenshot(page, `${mode}-camera-retention-failure`));
    await step(`${mode}: unsent draft survives reload and only explicit Save submits`, async () => {
      await record(page);
      await composer(page).locator('[name="notes"]').fill(draft);
      await composer(page).locator('[name="occurred_on"]').fill(DATE);
      const beforeReload = await storedDrafts(page);
      assert.equal(beforeReload.filter((row) => row.draft.notes === draft).length, 1,
        `Unsent draft must be durable before reload: ${JSON.stringify(beforeReload)}`);
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle");
      assert.equal((await savedNotes(draft)).length, 0, "Unsubmitted draft reached backend");
      await tab(page, "activity");
      await submode(page, "journal");
      assert.deepEqual(await storedDrafts(page), beforeReload, "Reload must preserve the draft and owner scope");
      // Journal's static Add button precedes its lazily initialized click handler.
      await page.locator("#journal-list .journal-card").filter({ hasText: note }).waitFor();
      await page.locator("#journal-add-btn").click();
      await page.getByRole("button", { name: /^Resume draft$/i }).click();
      await composer(page).waitFor();
      assert.equal(await composer(page).locator('[name="notes"]').inputValue(), draft);
      assert.equal(await composer(page).locator('[name="occurred_on"]').inputValue(), DATE);
      await assertContext(page);
      await screenshot(page, `${mode}-04-restored-draft`);
      await composer(page).locator(".journal-btn-submit").click();
      await composer(page).waitFor({ state: "hidden" });
      const entries = await poll(() => savedNotes(draft), (items) => items.length === 1, "Restored draft saved");
      assert.deepEqual(entries[0].plant_ids, [PLANT]);
      assert.deepEqual(entries[0].plot_ids, ["A1"]);
      report.records.push({ journey: mode, kind: "restored-draft", id: entries[0].id });
    });
    await step(`${mode}: manual issue without diagnosis and real linked record`, async () => {
      await tab(page, "map");
      await search(page, "A1", /A1/);
      await page.locator(".plant-summary-link:visible").filter({ hasText: /^Bloom E2E$/ }).first().click();
      await summary(page).getByRole("button", { name: /^Report issue$/i }).click();
      const form = page.locator("form.modal-form").filter({ has: page.locator('[name="issue_type"]') });
      await form.waitFor();
      assert.match(await form.innerText(), /Bloom E2E/);
      assert.match(await form.innerText(), /A1/);
      await form.locator('[name="title"]').fill(issueTitle);
      await form.locator('[name="description"]').fill("Synthetic wind damage; recorded manually, no provider.");
      await form.locator('[name="issue_type"]').selectOption("damage");
      await fit(page, "form.modal-form");
      await screenshot(page, `${mode}-05-manual-issue`);
      const originalForm = await form.elementHandle();
      await form.getByRole("button", { name: /^Save$/i }).click();
      await page.waitForFunction((el) => !el.isConnected, originalForm);
      await originalForm.dispose();
      const rows = await poll(async () => (await api("/api/issues?limit=100")).issues.filter((row) => row.title === issueTitle),
        (items) => items.length === 1, "Saved manual issue");
      assert.deepEqual(rows[0].plant_ids, [PLANT]);
      assert.deepEqual(rows[0].plot_ids, ["A1"]);
      assert.equal(rows[0].issue_type, "damage");
      report.records.push({ journey: mode, kind: "issue", id: rows[0].id });
      if (await form.isVisible()) {
        assert.equal(await form.locator('[name="title"]').inputValue(), issueTitle);
        const close = form.locator("xpath=ancestor::*[@role='dialog']").locator(".modal-close-btn");
        await close.focus();
        await close.press("Enter");
      }
      if (await summary(page).isVisible()) await closeSummary(page);
      await tab(page, "activity");
      await submode(page, "issues");
      await page.locator(".issue-card").filter({ hasText: issueTitle }).waitFor();
    });
    await step(`${mode}: unplaced search has a meaningful summary destination`, async () => {
      await tab(page, "map");
      await search(page, "Unplaced experience", /Unplaced experience/);
      await summary(page).getByText(/No current location/i).waitFor();
      await summary(page).getByRole("button", { name: /Place plant/i }).waitFor();
      await fit(page, ".plant-summary");
      await screenshot(page, `${mode}-08-unplaced`);
      await closeSummary(page);
    });
    await step(`${mode}: offline journal/photo persists then syncs once to real backend`, async () => {
      await tab(page, "activity");
      await submode(page, "journal");
      await page.locator("#journal-add-btn").click();
      await composer(page).waitFor();
      const offlineNote = `Experience ${mode} offline photo`;
      await composer(page).locator('[name="notes"]').fill(offlineNote);
      await composer(page).locator(".journal-add-select").nth(0).selectOption(PLANT);
      await composer(page).locator(".journal-add-select").nth(1).selectOption("A1");
      const photo = await page.screenshot();
      await composer(page).locator('input[type="file"]').setInputFiles({ name: `${mode}-synthetic.png`, mimeType: "image/png", buffer: photo });
      offline = true;
      await context.setOffline(true);
      await composer(page).locator(".journal-btn-submit").click();
      await composer(page).waitFor({ state: "hidden" });
      await page.locator(".offline-indicator--offline").waitFor();
      const pending = (await queuedRecords(page)).filter((item) => item.payload.notes === offlineNote);
      assert.equal(pending.length, 1, "One durable journal queue operation");
      assert.equal(pending[0].status, "pending");
      assert.equal(pending[0].payload._serialized_media.length, 1, "Photo bytes serialized in IndexedDB");
      assert(pending[0].operation_id, "Durable replay identity required");
      assert.equal((await savedNotes(offlineNote)).length, 0, "Offline write unexpectedly reached server");
      await screenshot(page, `${mode}-06-offline-pending`);
      await context.setOffline(false);
      offline = false;
      const entries = await poll(() => savedNotes(offlineNote), (items) => items.length === 1, "Offline journal replay", 30000);
      const id = entries[0].id;
      assert.deepEqual(entries[0].plant_ids, [PLANT]);
      assert.deepEqual(entries[0].plot_ids, ["A1"]);
      await poll(() => api(`/api/media?target_type=journal_entry&target_id=${encodeURIComponent(id)}`),
        (result) => result.items?.length === 1, "Offline photo uploaded", 30000);
      await page.locator(".offline-indicator").waitFor({ state: "hidden", timeout: 30000 });
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle");
      await tab(page, "activity");
      await submode(page, "journal");
      await page.locator("#journal-filter-search").fill(offlineNote);
      await page.locator("#journal-list .journal-card").filter({ hasText: offlineNote }).waitFor();
      const thumbnail = page.locator("#journal-list .journal-card-thumb-image").first();
      await thumbnail.waitFor();
      await thumbnail.scrollIntoViewIfNeeded();
      await poll(() => thumbnail.evaluate((el) => el.complete && el.naturalWidth > 0),
        Boolean, "Saved photo renders");
      assert.equal((await savedNotes(offlineNote)).length, 1, "Reload duplicated offline entry");
      report.records.push({ journey: mode, kind: "offline-photo", id });
      await screenshot(page, `${mode}-07-recovered-photo`);
    });
  } catch (error) {
    await screenshot(page, `${mode}-failure`).catch(() => {});
    throw error;
  } finally {
    await context.setOffline(false);
    await step(`${mode}: console and API error audit`, async () => {
      assert.deepEqual(errors, [], "Unexpected browser/API errors");
    }).catch(() => {});
    await context.close();
  }
}

async function taskJourney(browser, api) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: "en-GB" });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  await installClock(page);
  let offline = false;
  const errors = browserErrors(page, () => offline);
  try {
    await step("mobile: backdated offline pruning subset leaves unselected target actionable", async () => {
      await page.goto(BASE, { waitUntil: "networkidle" });
      await tab(page, "activity");
      await submode(page, "tasks");
      const card = page.locator("#tasks-list .task-card").filter({ hasText: "Prune 2 plants" });
      await card.getByRole("button", { name: /^Complete$/i }).click();
      const dialog = page.locator(".task-completion-dialog");
      await dialog.getByLabel("Prune B E2E", { exact: true }).uncheck();
      await dialog.locator('input[type="date"]').fill(DATE);
      await fit(page, ".task-completion-dialog");
      await screenshot(page, "mobile-09-backdated-pruning");
      offline = true;
      await context.setOffline(true);
      await dialog.locator(".confirm-yes").click();
      await dialog.waitFor({ state: "hidden" });
      const before = await api("/api/tasks/tsk_e2e_prune");
      assert.deepEqual([...before.plant_ids].sort(), ["PRUNE-A-E2E", "PRUNE-B-E2E"]);
      await page.locator(".offline-indicator--offline").waitFor();
      await context.setOffline(false);
      offline = false;
      const task = await poll(() => api("/api/tasks/tsk_e2e_prune"),
        (item) => item.plant_ids.length === 1, "Offline pruning subset replay", 30000);
      assert.deepEqual(task.plant_ids, ["PRUNE-B-E2E"]);
      assert.equal(task.status, "pending");
      const entries = (await api("/api/journal?event_type=pruned&plant_id=PRUNE-A-E2E")).entries;
      assert.equal(entries.length, 1);
      assert.equal(entries[0].occurred_on, DATE);
      assert.deepEqual(entries[0].plant_ids, ["PRUNE-A-E2E"]);
      assert.deepEqual(entries[0].plot_ids, ["A1"]);
      assert.equal(entries[0].metadata.source_task_id, "tsk_e2e_prune");
      assert.equal(entries[0].metadata.outcome, "done");
      await page.locator("#tasks-list .task-card").filter({ hasText: "Prune: Prune B E2E" }).waitFor();
      await screenshot(page, "mobile-10-pruning-replayed");
      report.records.push({ kind: "offline-pruning", id: entries[0].id });
      assert.deepEqual(errors, [], "Unexpected task browser/API errors");
    });
  } catch (error) {
    await screenshot(page, "task-failure").catch(() => {});
    throw error;
  } finally {
    await context.setOffline(false);
    await context.close();
  }
}

async function plannerJourney(browser, api) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 980 }, locale: "en-GB" });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  await installClock(page);
  const errors = browserErrors(page, () => false);
  const plantId = "STOCK-EXPERIENCE-E2E";
  const plotId = "E2E-STOCK-PLOT";
  let inventoryReads = 0;
  page.on("request", (req) => {
    if (new URL(req.url()).pathname === "/api/inventory") inventoryReads++;
  });
  try {
    await step("desktop: first-use planner stock prefill, cancel and atomic planting", async () => {
      await api("/api/plants", { plt_id: plantId, name: "Stock experience candidate", category: "flowers", bloom_month: "8" });
      await api("/api/plots", { plot_id: plotId, zone_code: "E", zone_name: "Experience stock", plot_number: 1, grid_row: 6, grid_col: 6 });
      const item = await api("/api/inventory", { plt_id: plantId, label: "Experience stock", inventory_type: "nursery", unit: "pcs" });
      await api(`/api/inventory/${item.id}/transactions`, { delta: "5", reason: "purchased", occurred_on: DATE });
      await page.goto(BASE, { waitUntil: "networkidle" });
      await tab(page, "insights");
      const plannerReady = page.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/planner/suggestions" && response.ok());
      await submode(page, "statistics");
      await plannerReady;
      await page.locator("#stats-mode-planner").click();
      assert.equal(await page.locator("#stats-mode-planner").getAttribute("aria-selected"), "true");
      const group = page.locator(".planner-plot-group").filter({ has: page.getByRole("button", { name: plotId, exact: true }) });
      const card = group.locator(".planner-suggestion-card").filter({ hasText: "Stock experience candidate" });
      await card.waitFor();
      assert.equal(inventoryReads, 0, "Fresh profile must reach planner without initializing Inventory through a prior visit");
      await card.getByRole("button", { name: /Inspect/i }).click();
      const inspection = page.locator(".candidate-inspection");
      const stock = inspection.locator(".candidate-stock-row").filter({ hasText: "Experience stock" });
      await stock.waitFor();
      assert.match(await stock.innerText(), /nursery.*5(?:\.0+)? pcs/);
      await fit(page, ".candidate-inspection");
      await screenshot(page, "desktop-11-planner-first-use");
      await stock.getByRole("button", { name: /Plant/i }).click();
      const form = page.locator(".inventory-modal .inventory-form");
      await form.waitFor();
      assert.equal(await form.locator("#inv-tx-plot").inputValue(), plotId);
      assert.equal(await form.locator("#inv-tx-qty").inputValue(), "1");
      assert.match(await page.locator(".inventory-modal h2").innerText(), /Experience stock/);
      await screenshot(page, "desktop-12-stock-prefilled");
      await form.getByRole("button", { name: /^Cancel$/i }).click();
      await form.waitFor({ state: "hidden" });
      assert.equal(Number((await api(`/api/inventory/${item.id}`)).quantity), 5);
      assert.equal((await api(`/api/plots/${plotId}/plants`)).length, 0, "Cancel created no placement");
      await stock.getByRole("button", { name: /Plant/i }).click();
      await form.locator("#inv-tx-qty").fill("2");
      await form.locator("#inv-tx-date").fill(DATE);
      await form.locator("#inv-tx-notes").fill("Experience first-use stock planting");
      await form.locator('button[type="submit"]').click();
      await form.waitFor({ state: "hidden" });
      await poll(() => api(`/api/inventory/${item.id}`), (row) => Number(row.quantity) === 3, "Stock decremented once");
      const assignments = await api(`/api/plots/${plotId}/plants`);
      assert.equal(assignments.length, 1);
      assert.equal(assignments[0].plt_id, plantId);
      // Stock units (for example packets) are not a count of assigned plants.
      assert.equal(Number(assignments[0].quantity), 1);
      const entries = (await api(`/api/journal?plant_id=${plantId}&plot_id=${plotId}&event_type=planted`)).entries;
      assert.equal(entries.length, 1);
      assert.equal(entries[0].occurred_on, DATE);
      assert.match(entries[0].title, /^Planted 2 pcs from stock$/);
      assert.equal(entries[0].metadata.inventory_item_id, item.id);
      assert.deepEqual(entries[0].plant_ids, [plantId]);
      assert.deepEqual(entries[0].plot_ids, [plotId]);
      await poll(() => stock.innerText(), (text) => /3(?:\.0+)? pcs/.test(text), "Inspection refreshes stock");
      await screenshot(page, "desktop-13-stock-saved");
      report.records.push({ kind: "first-use-stock", inventoryId: item.id, journalId: entries[0].id, plotId,
        remaining: "3", consumedStockUnits: "2", assignmentQuantity: 1 });
      assert.deepEqual(errors, [], "Unexpected first-use planner browser/API errors");
    });
  } catch (error) {
    await screenshot(page, "planner-failure").catch(() => {});
    throw error;
  } finally {
    await context.close();
  }
}

async function main() {
  guard();
  fs.mkdirSync(OUTPUT, { recursive: true });
  const client = await request.newContext({ baseURL: BASE });
  let browser;
  try {
    const gardensResponse = await client.get("/api/gardens");
    assert(gardensResponse.ok(), "Cannot read disposable gardens");
    const gardens = await gardensResponse.json();
    const garden = gardens.find((item) => item.slug === "task-history-e2e");
    assert(garden && gardens.length === 1, "Expected only seeded disposable garden");
    const api = async (url, data) => {
      const response = await client.fetch(url, { method: data === undefined ? "GET" : "POST",
        headers: { "x-garden-id": String(garden.id) }, ...(data === undefined ? {} : { data }) });
      assert(response.ok(), `${response.status()} ${url}: ${await response.text()}`);
      return response.json();
    };
    await step("synthetic API fixtures: unplaced plant and paginated plant history", async () => {
      await api("/api/plants", { plt_id: "UNPLACED-EXPERIENCE-E2E", name: "Unplaced experience", category: "flowers" });
      for (let i = 0; i < 51; i++) {
        await api("/api/journal", { event_type: "observed", occurred_on: DATE,
          notes: `Synthetic experience history ${i}`, plant_ids: [PLANT], plot_ids: ["A1"] });
      }
      await api("/api/journal", { event_type: "observed", occurred_on: DATE,
        notes: "Unrelated plant history must not enter scope", plant_ids: ["FERT-A-E2E"], plot_ids: ["A1"] });
    });
    browser = await chromium.launch({ executablePath: CHROMIUM_EXECUTABLE, headless: true });
    const failures = [];
    for (const mode of ["desktop", "mobile"]) {
      try { await journey(browser, api, mode); }
      catch (error) { failures.push(`${mode}: ${error.stack || error.message}`); }
    }
    try { await taskJourney(browser, api); }
    catch (error) { failures.push(`task: ${error.stack || error.message}`); }
    try { await plannerJourney(browser, api); }
    catch (error) { failures.push(`planner: ${error.stack || error.message}`); }
    assert.equal(failures.length, 0, failures.join("\n"));
    assert.equal(report.steps.filter((item) => item.status === "failed").length, 0,
      "Non-blocking retention assertions failed; see report.json");
  } finally {
    if (browser) await browser.close();
    await client.dispose();
    fs.writeFileSync(path.join(OUTPUT, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
    process.stdout.write(`Experience report: ${path.join(OUTPUT, "report.json")}\n`);
  }
}

if (process.argv.includes("--help")) {
  console.log("Opt in with GARDENOPS_EXPERIENCE_E2E=1 inside run_fast_postgres_tests.py --command --command-database gardenops_task_history_e2e_test -- env GARDENOPS_EXPERIENCE_E2E=1 bash scripts/run_task_completion_history_e2e.sh. Requires main integration readiness. Outputs ignored research/experience-e2e/report.json and screenshots.");
} else {
  main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
}
