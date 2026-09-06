// Actual main.ts and browser storage, with synthetic auth responses. No backend data.
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("../frontend/node_modules/playwright-core");

async function snapshot(page) {
  return page.evaluate(async () => {
    const drafts = Object.fromEntries(Object.entries(localStorage)
      .filter(([key]) => key.startsWith("gardenops:journal-draft:v1:")));
    const queued = await new Promise((resolve, reject) => {
      const open = indexedDB.open("gardenops-offline");
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result;
        const transaction = db.transaction("drafts", "readonly");
        const request = transaction.objectStore("drafts").getAll();
        transaction.oncomplete = () => { db.close(); resolve(request.result); };
        transaction.onerror = () => { db.close(); reject(transaction.error); };
      };
    });
    return { drafts, queued };
  });
}

async function main() {
  const { createServer } = await import("../frontend/node_modules/vite/dist/node/index.js");
  const server = await createServer({
    root: path.resolve(__dirname, "../frontend"),
    configFile: false,
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{ name: "auth-seed-shell", configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (request.url !== "/auth-seed") return next();
        response.setHeader("Content-Type", "text/html");
        response.end('<!doctype html><html><body><div id="app"></div></body></html>');
      });
    } }],
  });
  let browser;
  try {
    await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    browser = await chromium.launch({
      executablePath: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/chromium",
      headless: true,
    });
    const page = await browser.newPage();
    const pageErrors = [];
    const writes = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    let outcomes = [];
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      if (request.method() !== "GET") writes.push(request.url());
      const url = new URL(request.url());
      if (url.pathname === "/api/auth/me") {
        const status = outcomes.shift();
        if (status === 0) return route.abort("internetdisconnected");
        if (status !== 200) return route.fulfill({ status, json: { detail: `Synthetic ${status}` } });
        return route.fulfill({ json: { username: "known-user", language: "en", must_change_password: false } });
      }
      if (url.pathname === "/api/auth/status") {
        return route.fulfill({ json: { bootstrap_required: false, passkeys_enabled: false } });
      }
      return route.fulfill({ json: {} });
    });
    // Stop at the verified lazy-app boundary so automatic sync cannot alter the seed.
    await page.route("**/src/app.ts", (route) => route.fulfill({
      contentType: "application/javascript",
      body: "window.__verifiedProfile = window.__gardenopsInitialAuthProfile;",
    }));
    const seed = async () => {
      await page.goto(`${origin}/auth-seed`);
      await page.evaluate(async () => {
        const queue = await import("/src/services/offlineQueue.ts");
        const api = await import("/src/services/api.ts");
        const drafts = await import("/src/services/journalDraft.ts");
        api.setActiveGardenContext(1);
        await queue.initOfflineQueue();
        queue.setOfflineQueueIdentity("known-user");
        await queue.enqueueDraft("journal", { _garden_id: 1, event_type: "observed",
          occurred_on: "2026-09-01", notes: "Unsynced entry", plant_ids: [], plot_ids: [] });
        drafts.writeJournalDraft(drafts.journalDraftKey("known-user", 1), {
          id: "text-draft", occurred_on: "2026-09-01", event_type: "observed",
          title: "Unsubmitted title", notes: "Unsubmitted notes", plant_ids: [], plot_ids: [], photo_count: 0,
        });
      });
      return snapshot(page);
    };
    const original = await seed();
    outcomes = [0, 503, 200];
    await page.goto(origin);
    for (const status of [0, 503]) {
      await page.locator("#auth-verification-retry").waitFor();
      if (status === 503) await page.getByText("Synthetic 503", { exact: true }).last().waitFor();
      assert.deepEqual(await snapshot(page), original);
      assert.equal(await page.locator("#auth-gate-form").count(), 0);
      assert.equal(await page.evaluate(() => document.getElementById("app").inert), true);
      assert.equal(await page.evaluate(() => Boolean(window.__verifiedProfile)), false);
      assert.deepEqual(writes, []);
      await page.locator("#auth-verification-retry button").click();
    }
    await page.waitForFunction(() => window.__verifiedProfile?.username === "known-user");
    assert.deepEqual(await snapshot(page), original);
    console.log("PASS actual entrypoint: network failure and 503 preserve IndexedDB/text drafts; verified retry retains identical work and operation IDs.");

    for (const status of [401, 403]) {
      if (status === 403) await seed();
      outcomes = [status];
      await page.goto(origin);
      await page.locator("#auth-gate-form").waitFor();
      assert.deepEqual(await snapshot(page), { drafts: {}, queued: [] });
      assert.equal(await page.evaluate(() => Boolean(window.__verifiedProfile)), false);
    }
    console.log("PASS actual entrypoint: authApi 401/403 clear committed browser work before exposing login.");

    await seed();
    await page.addInitScript(() => {
      const remove = IDBCursor.prototype.delete;
      IDBCursor.prototype.delete = function () {
        if (this.source.name === "drafts" && !window.__allowPrivateClear) {
          throw new DOMException("Synthetic clear failure", "UnknownError");
        }
        return remove.call(this);
      };
    });
    outcomes = [401];
    await page.goto(origin);
    await page.locator("#auth-verification-retry").waitFor();
    assert.equal(await page.locator("#auth-gate-form").count(), 0);
    assert.equal((await snapshot(page)).queued.length, 1);
    await page.evaluate(() => { window.__allowPrivateClear = true; });
    await page.locator("#auth-verification-retry button").click();
    await page.locator("#auth-gate-form").waitFor();
    assert.deepEqual(await snapshot(page), { drafts: {}, queued: [] });
    assert.deepEqual(writes, []);
    assert.deepEqual(pageErrors, []);
    console.log("PASS cleanup failure blocks login until successful retry; no replay or browser errors.");
  } finally {
    await browser?.close();
    await server.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
