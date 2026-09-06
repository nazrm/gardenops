// Focused browser/component checks with synthetic HTTP responses, not a backend E2E.
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { chromium } = require("../frontend/node_modules/playwright-core");

async function main() {
  const { createServer } = await import("../frontend/node_modules/vite/dist/node/index.js");
  const server = await createServer({
    root: path.resolve(__dirname, "../frontend"), configFile: false,
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{ name: "journal-test-shell", configureServer(vite) {
      vite.middlewares.use((req, res, next) => {
        if (req.url !== "/capture-test") return next();
        res.setHeader("Content-Type", "text/html");
        res.end('<!doctype html><html><head><link rel="stylesheet" href="/src/style.css"></head><body></body></html>');
      });
    } }],
  });
  let browser;
  try {
    await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/chromium", headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const requests = [];
    const savedIssue = { id: "issue-saved", issue_type: "disease", title: "Manual issue text",
      description: "Synthetic diagnosis", severity: "normal", status: "open", suspected_cause: "Synthetic cause",
      treatment_plan: "Synthetic treatment", plant_ids: ["P1"], plot_ids: ["A"], created_at_ms: 1, updated_at_ms: 1 };
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      if (url.pathname === "/api/journal") {
        await route.fulfill({ json: { total: 51, entries: [{
          id: `entry-${url.searchParams.get("offset") || 0}`, event_type: "observed",
          occurred_on: "2026-09-01", title: "Synthetic entry", notes: "", plant_ids: ["P1"],
          plot_ids: ["A"], created_at_ms: 1, updated_at_ms: 1,
        }] } });
      } else if (url.pathname === "/api/ai/diagnose-plant") {
        await route.fulfill({ json: { disclaimer: "Synthetic test only", diagnoses: [{
          likely_cause: "Synthetic cause", description: "Synthetic diagnosis", suggested_treatment: "Synthetic treatment",
          issue_type: "disease", confidence: "normal",
        }] } });
      } else if (url.pathname === "/api/issues" && route.request().method() === "POST") {
        await route.fulfill({ json: { id: savedIssue.id, status: "created" } });
      } else if (url.pathname === "/api/issues/issue-saved") {
        await route.fulfill({ json: savedIssue });
      } else if (url.pathname === "/api/issues/issue-saved/history") {
        await route.fulfill({ json: { issue_events: [], journal_entries: [] } });
      } else if (url.pathname === "/api/issues") {
        await route.fulfill({ json: { issues: [savedIssue], total: 1 } });
      } else await route.fulfill({ json: { items: [], total: 0 } });
    });
    await page.goto(`${origin}/capture-test`);
    const helper = await page.evaluate(async () => {
      const d = await import("/src/services/journalDraft.ts");
      const key = d.journalDraftKey("seed-user", 1);
      const draft = { id: "draft-1", title: "Keep title", notes: "Keep notes", event_type: "observed",
        occurred_on: "2026-09-01", plant_ids: ["P1", "removed"], plot_ids: ["A", "old"], photo_count: 2 };
      d.writeJournalDraft(key, draft);
      const isolated = d.readJournalDraft(d.journalDraftKey("other-user", 1)) === null
        && d.readJournalDraft(d.journalDraftKey("seed-user", 2)) === null;
      d.discardJournalDraft(key, "unrelated-id");
      const retained = d.readJournalDraft(key);
      const validated = d.validateJournalDraft(retained, new Set(["P1"]), new Set(["A"]));
      let conflict = false;
      try { d.writeJournalDraft(key, { ...draft, id: "draft-2" }); } catch { conflict = true; }
      const generation = d.getJournalDraftGeneration();
      d.clearJournalDrafts();
      return { isolated, retained, validated, conflict, cleared: d.readJournalDraft(key) === null,
        invalidated: d.getJournalDraftGeneration() > generation };
    });
    assert(helper.isolated && helper.conflict && helper.cleared && helper.invalidated);
    assert.equal(helper.retained.title, "Keep title");
    assert.deepEqual(helper.validated.missingIds, ["removed", "old"]);
    assert.deepEqual(helper.validated.draft.plant_ids, ["P1"]);
    console.log("PASS draft identity/garden isolation, title, validation, conflict, matching discard, cleanup generation");

    await page.evaluate(async () => {
      const { initJournalTab } = await import("/src/tabs/journalTab.ts");
      const { initIssuesTab } = await import("/src/tabs/issuesTab.ts");
      const api = await import("/src/services/api.ts");
      const queue = await import("/src/services/offlineQueue.ts");
      api.setActiveGardenContext(1);
      queue.setOfflineQueueIdentity("seed-user");
      const profile = { username: "seed-user" };
      const plants = [1, 2, 3, 4].map((i) => ({ plt_id: `P${i}`, name: `Plant ${i}` }));
      const plots = [{ plot_id: "A", zone_name: "North", display_name: "Bed A" }];
      window.captureContext = {
        getPlants: () => plants, getPlots: () => plots, getAuthProfile: () => profile,
        canWrite: () => true, ensureWriteAccess: () => true, ensurePlantsCacheLoaded: async () => {},
        getActiveTab: () => "activity", getSubMode: () => "journal", navigateToSubMode: () => {},
        renderDataExportBars: () => {}, showToast: () => {}, refreshOfflineIndicator: async () => {},
        isOnline: () => true, extractPendingMediaFiles: (data) => data.media_files ?? [],
        withoutPendingMediaFiles: ({ media_files, ...data }) => data,
        uploadTargetMediaFiles: async () => {}, attachReadonlyMediaSection: () => {},
      };
      initJournalTab(window.captureContext);
      initIssuesTab(window.captureContext);
      window.captureJournal = await import("/src/tabs/journalTab.ts");
      window.captureIssues = await import("/src/tabs/issuesTab.ts");
      await window.captureJournal.openJournalComposer(undefined, { plantIds: ["P1", "P2", "P3", "P4"], plotIds: ["A"] });
    });
    assert.deepEqual(await page.locator(".journal-chip-list").first().locator(".journal-chip").allTextContents(),
      ["Plant 1×", "Plant 2×", "Plant 3×", "Plant 4×"]);
    for (let i = 0; i < 4; i++) await page.locator(".journal-chip-list").first().locator("button").first().click();
    assert.equal(await page.locator(".journal-chip-list").first().locator("button").count(), 0);
    await page.locator('[name="title"]').fill("Unfinished title");
    await page.locator('[name="notes"]').fill("Unfinished note");
    const labels = await page.locator(".journal-field-group").evaluateAll((groups) => groups.every((group) => {
      const control = group.querySelector("input, select, textarea");
      return !control || group.querySelector("label")?.htmlFor === control.id;
    }));
    assert(labels);
    await page.locator(".journal-btn-cancel").click();
    await page.evaluate(() => { void window.captureJournal.openJournalComposer(undefined, { plantIds: ["P2"] }); });
    await page.waitForSelector('[role="dialog"]');
    await page.keyboard.press("Escape");
    assert.equal(await page.locator('[role="dialog"]').count(), 0);
    await page.evaluate(() => { void window.captureJournal.openJournalComposer(undefined, { plantIds: ["P2"] }); });
    await page.locator(".modal-content > button").nth(1).click();
    await page.waitForSelector('[name="title"]');
    assert.equal(await page.locator('[name="title"]').inputValue(), "Unfinished title");
    assert.equal(await page.locator(".journal-chip-list").first().locator("button").count(), 0);
    console.log("PASS contextual prefills, repeated named-chip removal, labels, cancel/resume without replacement");

    await page.evaluate(async () => {
      const { createModal } = await import("/src/components/dialogCore.ts");
      createModal("Top only", '<div class="modal-content"><input></div>');
    });
    await page.keyboard.press("Escape");
    assert.equal(await page.locator('[role="dialog"]').count(), 1);
    assert.equal(await page.locator('[name="title"]').inputValue(), "Unfinished title");
    await page.evaluate(() => window.captureJournal.resetJournalForGardenSwitch());
    assert.equal(await page.locator('[role="dialog"]').count(), 0);
    console.log("PASS stacked Escape closes only top and garden reset closes stale composer");

    const cacheHook = fs.readFileSync(path.resolve(__dirname, "../frontend/src/app.ts"), "utf8")
      .match(/ensurePlantsCacheLoaded: (async \(requireReady = false\) => \{[\s\S]*?\n  \}),/)[1];
    const originalDraft = await page.evaluate(async (hook) => {
      const drafts = await import("/src/services/journalDraft.ts");
      drafts.clearJournalDrafts();
      const key = drafts.journalDraftKey("seed-user", 1);
      const draft = { id: "fetch-failure", title: "Keep links", notes: "Keep context", event_type: "observed",
        occurred_on: "2026-09-01", plant_ids: ["P1"], plot_ids: ["A"], photo_count: 0 };
      drafts.writeJournalDraft(key, draft);
      window.realGetPlants = window.captureContext.getPlants;
      window.captureContext.getPlants = () => [];
      // Exercise the actual app-context hook after its underlying loader swallowed a failure.
      window.captureContext.ensurePlantsCacheLoaded = new Function("ensurePlantsCacheLoaded", "plantsCacheLoaded", "t",
        `return (${hook});`)(async () => {}, false, (key) => key);
      await window.captureJournal.openJournalComposer();
      return drafts.readJournalDraft(key);
    }, cacheHook);
    assert.equal(await page.locator('[role="dialog"]').count(), 0);
    assert.deepEqual(originalDraft.plant_ids, ["P1"]);
    await page.evaluate((hook) => {
      window.captureContext.getPlants = window.realGetPlants;
      window.captureContext.ensurePlantsCacheLoaded = new Function("ensurePlantsCacheLoaded", "plantsCacheLoaded", "t",
        `return (${hook});`)(async () => {}, true, (key) => key);
      void window.captureJournal.openJournalComposer();
    }, cacheHook);
    await page.getByRole("button", { name: /^Resume draft$/i }).click();
    await page.locator('[name="title"]').fill("Recovered after retry");
    assert.deepEqual(await page.evaluate(async () => {
      const d = await import("/src/services/journalDraft.ts");
      return d.readJournalDraft(d.journalDraftKey("seed-user", 1)).plant_ids;
    }), ["P1"]);
    await page.locator(".journal-btn-cancel").click();
    console.log("PASS failed plant cache load preserves draft links; successful retry restores and edits them");

    await page.evaluate(() => {
      document.body.innerHTML = '<select id="journal-filter-type"><option value=""></option><option value="observed">Observed</option></select><input id="journal-filter-search"><div id="journal-summary"></div><div id="journal-list"></div><div id="journal-pagination"></div>';
      window.captureJournal.initJournalTab(window.captureContext);
      window.returned = false;
      window.captureJournal.openJournalHistory({ plantId: "P1", label: "Plant 1" }, () => { window.returned = true; });
    });
    await page.locator("#journal-pagination button").last().click();
    await page.waitForFunction(() => document.querySelector('[data-entry-id="entry-50"]'));
    await page.locator("#journal-filter-search").fill("seen");
    await page.waitForFunction(() => document.querySelector('[data-entry-id="entry-0"]'));
    assert(requests.filter((url) => url.pathname === "/api/journal").every((url) => url.searchParams.get("plant_id") === "P1"));
    assert(requests.some((url) => url.searchParams.get("q") === "seen" && url.searchParams.get("offset") === "0"));
    await page.locator("#journal-history-scope button").last().click();
    assert(await page.evaluate(() => window.returned));
    console.log("PASS persistent history scope across pagination/search and Return callback");

    await page.evaluate(() => window.captureIssues.openIssueForm(undefined, { plantIds: ["P1"], plotIds: ["A"] }));
    await page.locator('[name="title"]').fill("Manual issue text");
    await page.locator(".modal-form > .btn-secondary").click();
    assert.equal(await page.locator('[role="dialog"]').count(), 2);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator('[role="dialog"]').count(), 1);
    assert.equal(await page.locator('[name="title"]').inputValue(), "Manual issue text");
    assert((await page.locator(".chip-input__chip-label").allTextContents()).some((label) => label.includes("Plant 1")));
    await page.locator(".modal-form > .btn-secondary").click();
    await page.locator('[role="dialog"]').last().locator('input[type="file"]').setInputFiles({
      name: "symptom.png", mimeType: "image/png", buffer: Buffer.from("synthetic-photo"),
    });
    await page.locator('[role="dialog"]').last().locator(".modal-content > button").last().click();
    await page.locator('[role="dialog"]').last().locator('[role="status"] button').click();
    assert.equal(await page.locator('[name="title"]').inputValue(), "Manual issue text");
    assert.equal(await page.locator('[name="suspected_cause"]').inputValue(), "Synthetic cause");
    assert.equal(await page.locator('[name="issue_type"]').inputValue(), "disease");
    await page.locator('.modal-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector(".plant-journal-history"));
    assert.equal(await page.locator('[name="title"]').inputValue(), "Manual issue text");
    assert.equal(await page.locator(".modal-form > .btn-secondary").count(), 0);
    console.log("PASS explicit diagnosis application preserves entered text and successful save exposes existing issue");
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: "/tmp/gardenops-capture-mobile.png" });
    await page.evaluate(() => window.captureIssues.resetIssuesForGardenSwitch());
    assert.equal(await page.locator('[role="dialog"]').count(), 0);
    console.log("PASS issue prefills, diagnosis cancellation preserves form, mobile fit and stale close");

    await page.evaluate(() => {
      window.issueUploads = [];
      window.issueFailedOnce = false;
      window.captureContext.uploadTargetMediaFiles = async (_type, _id, files, options) => {
        for (const [index, file] of files.entries()) {
          window.issueUploads.push({ name: file.name, operationId: options.operationIds?.[index] });
          if (file.name === "second.png" && !window.issueFailedOnce) {
            window.issueFailedOnce = true;
            throw new Error("Synthetic partial upload");
          }
        }
      };
      window.captureIssues.openIssueForm(undefined, { plantIds: ["P1"], plotIds: ["A"] });
    });
    await page.locator('[name="title"]').fill("Issue photo retry");
    await page.locator('input[type="file"]').setInputFiles(["first.png", "second.png"].map((name) => ({
      name, mimeType: "image/png", buffer: Buffer.from("synthetic image"),
    })));
    await page.locator('.modal-form button[type="submit"]').click();
    await page.getByRole("button", { name: /View saved issue/i }).waitFor();
    await page.locator('.modal-form button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector(".plant-journal-history"));
    const issueUploads = await page.evaluate(() => window.issueUploads);
    assert.deepEqual(issueUploads.map((row) => row.name), ["first.png", "second.png", "second.png"]);
    assert(issueUploads.every((row) => row.operationId));
    assert.equal(issueUploads[1].operationId, issueUploads[2].operationId);
    assert.notEqual(issueUploads[0].operationId, issueUploads[1].operationId);
    await page.evaluate(() => window.captureIssues.resetIssuesForGardenSwitch());
    console.log("PASS issue photo retry skips confirmed files and reuses the failed file operation ID");

    // A saved parent opened from recovery uses the same existing-entry editor.
    for (const failure of ["upload", "link"]) {
      const uploads = [];
      const links = [];
      const updates = [];
      const assets = new Map();
      let failed = false;
      const intercept = async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const headers = request.headers();
        if (url.pathname === "/api/journal/saved-parent" && request.method() === "PATCH") {
          updates.push({ body: request.postDataJSON(), garden: headers["x-garden-id"] });
          return route.fulfill({ json: { status: "updated" } });
        }
        if (url.pathname === "/api/media/upload") {
          const name = headers["x-upload-filename"];
          const operation = headers["x-offline-operation-id"];
          uploads.push({ name, operation, garden: headers["x-garden-id"], parent: url.searchParams.get("target_id") });
          if (!assets.has(operation)) assets.set(operation, `asset-${assets.size + 1}`);
          // Simulate an acknowledged-lost upload: the server already created the asset.
          if (failure === "upload" && name === "second.png" && !failed) {
            failed = true;
            return route.fulfill({ status: 503, json: { detail: "Synthetic upload failure" } });
          }
          return route.fulfill({ json: { asset_id: assets.get(operation) } });
        }
        if (/^\/api\/media\/asset-\d+\/links$/.test(url.pathname)) {
          const body = request.postDataJSON();
          links.push({ asset: url.pathname.split("/")[3], ...body, garden: headers["x-garden-id"] });
          if (failure === "link" && url.pathname.includes("asset-2") && body.target_type === "plot" && !failed) {
            failed = true;
            return route.fulfill({ status: 503, json: { detail: "Synthetic link failure" } });
          }
          return route.fulfill({ json: {} });
        }
        return route.fallback();
      };
      await page.route("**/api/**", intercept);
      await page.evaluate(async () => {
        window.editorSaved = 0;
        window.captureContext.refreshPlantMediaPreviews = async () => {};
        await window.captureJournal.openJournalComposer({
          id: "saved-parent", event_type: "observed", occurred_on: "2026-09-01",
          title: "Recovered parent", notes: "Original", plant_ids: ["P1"], plot_ids: ["A"],
          created_at_ms: 1, updated_at_ms: 1,
        }, { onSaved: () => { window.editorSaved++; } });
        window.retainedComposer = document.querySelector(".journal-composer");
      });
      await page.locator('[name="title"]').fill("Edited saved parent");
      await page.locator('[name="notes"]').fill("Keep retry notes");
      await page.locator('input[type="file"]').setInputFiles(["first.png", "second.png"].map((name) => ({
        name, mimeType: "image/png", buffer: Buffer.from(`synthetic-${name}`),
      })));
      await page.locator(".journal-btn-submit").click();
      await page.waitForFunction(() => !document.querySelector(".journal-btn-submit")?.disabled);
      assert(failed);
      assert.equal(await page.evaluate(() => window.editorSaved), 0);
      assert(await page.evaluate(() => window.retainedComposer === document.querySelector(".journal-composer")));
      assert.equal(await page.locator('[name="title"]').inputValue(), "Edited saved parent");
      assert.equal(await page.locator('[name="notes"]').inputValue(), "Keep retry notes");
      assert.equal(await page.locator('[name="occurred_on"]').inputValue(), "2026-09-01");
      assert.deepEqual(await page.locator(".journal-chip-list").first().locator(".journal-chip").allTextContents(), ["Plant 1×"]);
      assert.equal(await page.locator('.journal-composer img[alt="first.png"]').count(), 1);
      assert.equal(await page.locator('.journal-composer img[alt="second.png"]').count(), 1);
      await page.locator(".journal-btn-submit").click();
      await page.waitForFunction(() => window.editorSaved === 1);
      assert.equal(await page.locator(".journal-composer").count(), 0);
      assert.equal(uploads.filter((u) => u.name === "first.png").length, 1);
      const second = uploads.filter((u) => u.name === "second.png");
      assert.equal(second.length, failure === "upload" ? 2 : 1);
      assert(uploads.every((u) => u.operation && u.garden === "1" && u.parent === "saved-parent"));
      assert.equal(new Set(second.map((u) => u.operation)).size, 1);
      assert.equal(new Set(uploads.map((u) => u.operation)).size, 2);
      assert.equal(assets.size, 2);
      assert.equal(links.filter((l) => l.asset === "asset-1").length, 2);
      assert.equal(links.filter((l) => l.asset === "asset-2" && l.target_type === "plant").length, 1);
      assert.equal(links.filter((l) => l.asset === "asset-2" && l.target_type === "plot").length, failure === "link" ? 2 : 1);
      assert(links.every((l) => l.garden === "1" && l.target_id === (l.target_type === "plant" ? "P1" : "A")));
      assert.equal(updates.length, 2);
      assert(updates.every((u) => u.garden === "1" && u.body.title === "Edited saved parent"
        && u.body.notes === "Keep retry notes" && u.body.plant_ids.join() === "P1" && u.body.plot_ids.join() === "A"));
      await page.unroute("**/api/**", intercept);
      console.log(`PASS existing saved-parent ${failure} retry retains editor/photos/context and stable operation IDs without duplicate work`);
    }

    await page.evaluate(async () => {
      const d = await import("/src/services/journalDraft.ts");
      d.clearJournalDrafts();
      const queue = await import("/src/services/offlineQueue.ts");
      await queue.initOfflineQueue();
      const feature = await import("/src/features/offlineFeature.ts");
      feature.initOfflineFeature({}, { canManageDrafts: () => false });
      await window.captureJournal.openJournalComposer(undefined, { plantIds: ["P1"], plotIds: ["A"] });
    });
    await page.locator('[name="title"]').fill("Online queued entry");
    await page.locator('input[type="file"]').setInputFiles({ name: "leaf.png", mimeType: "image/png", buffer: Buffer.from("synthetic-component-photo") });
    await page.locator(".journal-btn-submit").click();
    await page.waitForFunction(() => !document.querySelector(".journal-composer"));
    const queued = await page.evaluate(async () => {
      const q = await import("/src/services/offlineQueue.ts");
      const d = await import("/src/services/journalDraft.ts");
      return { drafts: await q.getAllDrafts(), textDraft: d.readJournalDraft(d.journalDraftKey("seed-user", 1)) };
    });
    assert.equal(queued.drafts.length, 1);
    assert.equal(queued.drafts[0].payload.title, "Online queued entry");
    assert.equal(queued.drafts[0].payload._serialized_media.length, 1);
    assert.equal(queued.drafts[0].garden_id, 1);
    assert.equal(queued.drafts[0].owner_id, "seed-user");
    assert.equal(queued.textDraft, null);
    console.log("PASS online submission commits entry/photo queue before closing and clears matching text draft");

    await page.evaluate(async () => {
      await window.captureJournal.openJournalComposer();
      window.originalStorageSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        if (key.startsWith("gardenops:journal-draft:")) throw new DOMException("Storage full", "QuotaExceededError");
        return window.originalStorageSetItem.call(this, key, value);
      };
      window.originalIdbAdd = IDBObjectStore.prototype.add;
      IDBObjectStore.prototype.add = function () { throw new DOMException("Storage full", "QuotaExceededError"); };
    });
    await page.locator('[name="title"]').fill("Must remain accessible");
    assert((await page.locator('[role="dialog"] [role="status"]').first().textContent()).length > 0);
    await page.locator(".journal-btn-submit").click();
    await page.waitForFunction(() => !document.querySelector(".journal-btn-submit")?.disabled);
    assert.equal(await page.locator('[name="title"]').inputValue(), "Must remain accessible");
    await page.evaluate(() => {
      Storage.prototype.setItem = window.originalStorageSetItem;
      IDBObjectStore.prototype.add = window.originalIdbAdd;
      window.captureJournal.resetJournalForGardenSwitch();
    });
    console.log("PASS local text storage and IndexedDB quota failures retain editable form");
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
