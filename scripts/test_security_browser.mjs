// Synthetic API and real browser modules; never connects to a GardenOps backend.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { createServer } = await import(require.resolve("vite"));
const { chromium } = require("playwright-core");
const root = fileURLToPath(new URL("../frontend", import.meta.url));
const appSource = await readFile(`${root}/src/app.ts`, "utf8");
assert.match(appSource, /setActiveGardenContext\(garden.id\);\s+clearGardenScopedStateForSwitch\(\);/);
assert.match(appSource, /onSignOut: async \(\) => \{\s+await handleAuthButton\(\);\s+\}/);
const server = await createServer({
  root, configFile: false, server: { host: "127.0.0.1", port: 0 },
  define: { __APP_VERSION__: '"test"' },
  plugins: [{
    name: "isolated-security-entrypoints",
    enforce: "pre",
    transform(code, id) {
      if (id.endsWith("/src/app.ts")) return code.replace("void bootstrapApp();", `
        export { handleAuthButton, requireOfflineQueueClear, clearGardenScopedStateForSwitch };
        export async function prepareTestAccount() {
          authProfile = { username: "synthetic", role: "editor" };
          inventoryTabModule = await import("./tabs/inventoryTab");
        }
        export function testAccountPresent() { return authProfile !== null; }
      `);
      if (id.endsWith("/src/components/plotInteractions.ts")) return code + "\nexport { loadPlotJournalPreview };";
    },
  }],
});
let browser;
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH, headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const chats = [];
  let journalRequest;
  let logoutStatus = 500;
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, origin, "No external requests allowed");
    if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: `<!doctype html>
      <div id="app"><div id="app-status" hidden><span id="app-status-text"></span>
      <button id="app-status-action"></button><button id="app-status-dismiss"></button></div>
      <div id="analysis-messages"></div><input id="analysis-input"><button id="analysis-send-btn">Send</button>
      <button id="clear-chat-btn">Clear</button><div id="inventory-summary"></div>
      <div id="inventory-mobile-list"></div><table><thead id="inventory-table-head"></thead>
      <tbody id="inventory-table-body"></tbody></table><div id="inventory-pagination"></div></div>` });
    if (!url.pathname.startsWith("/api/")) return route.continue();
    if (url.pathname === "/api/client-errors") return route.fulfill({ status: 204 });
    if (url.pathname === "/api/ai/garden-chat") { chats.push(route); return; }
    if (url.pathname === "/api/journal") { journalRequest = route; return; }
    if (url.pathname === "/api/auth/logout") return route.fulfill({ status: logoutStatus, json: { detail: "Synthetic logout response" } });
    if (url.pathname === "/api/auth/status") return route.fulfill({ json: { auth_required: true, auth_mode: "session", bootstrap_required: false, passkeys_enabled: false } });
    if (url.pathname === "/api/auth/password-policy") return route.fulfill({ json: {} });
    throw new Error(`Unexpected API request ${url.pathname}`);
  });
  await page.goto(origin);
  await page.evaluate(async () => {
    window.api = await import("/src/services/api.ts");
    window.api.setActiveGardenContext(1);
    window.analysis = await import("/src/tabs/analysisTab.ts");
    window.analysis.initAnalysisTab();
    window.securityApp = await import("/src/app.ts");
    await window.securityApp.prepareTestAccount();
    window.panels = await import("/src/components/plotInteractions.ts");
    window.drawer = await import("/src/components/drawer.ts");
  });
  const waitUntil = async predicate => {
    for (let i = 0; i < 200 && !predicate(); i++) await new Promise(resolve => setTimeout(resolve, 25));
    assert.ok(predicate());
  };
  const send = async text => {
    await page.locator("#analysis-input").fill(text);
    await page.locator("#analysis-send-btn").click();
  };
  await send("Account A private question");
  await waitUntil(() => chats.length === 1);
  await page.evaluate(async () => {
    document.getElementById("inventory-table-body").textContent = "Account A inventory";
    await window.securityApp.requireOfflineQueueClear(Promise.resolve());
    document.getElementById("app").removeAttribute("inert");
    window.api.setActiveGardenContext(2);
  });
  assert.equal(await page.locator("#inventory-table-body").textContent(), "");
  assert.doesNotMatch(await page.locator("#analysis-messages").textContent(), /Account A/);
  await send("Account B question");
  await waitUntil(() => chats.length === 2);
  assert.deepEqual(chats[1].request().postDataJSON().history, []);
  await chats[0].fulfill({ json: { reply: "Account A private reply" } });
  await page.waitForTimeout(100);
  assert.equal(await page.locator("#analysis-send-btn").isDisabled(), true);
  assert.doesNotMatch(await page.locator("#analysis-messages").textContent(), /Account A/);
  await chats[1].fulfill({ json: { reply: "Account B reply" } });
  await page.locator(".chat-ai:not(.chat-loading)").waitFor();
  assert.match(await page.locator("#analysis-messages").textContent(), /Account B reply/);
  await send("Pending before clear");
  await waitUntil(() => chats.length === 3);
  await page.locator("#clear-chat-btn").click();
  await chats[2].fulfill({ status: 500, json: { detail: "Old error" } });
  await page.waitForTimeout(100);
  assert.equal(await page.locator(".chat-error").count(), 0);
  await send("Old garden pending question");
  await waitUntil(() => chats.length === 4);
  await page.evaluate(() => {
    window.api.setActiveGardenContext(3);
    window.securityApp.clearGardenScopedStateForSwitch();
  });
  await chats[3].fulfill({ json: { reply: "Old garden private reply" } });
  await page.waitForTimeout(100);
  assert.doesNotMatch(await page.locator("#analysis-messages").textContent(), /Old garden/);
  await send("New garden question");
  await waitUntil(() => chats.length === 5);
  assert.deepEqual(chats[4].request().postDataJSON().history, []);
  await chats[4].fulfill({ json: { reply: "New garden reply" } });
  await page.locator(".chat-ai:not(.chat-loading)").waitFor();
  await page.evaluate(() => {
    window.openSyntheticDrawer = plotId => window.drawer.showDrawer({ plotId, plants: [], onClose() {}, onSearch() {}, onRemove() {}, onEdit() {} });
    window.openSyntheticDrawer("A");
    window.oldJournal = window.panels.loadPlotJournalPreview("A", { onViewJournal() {} });
  });
  await waitUntil(() => Boolean(journalRequest));
  const oldJournal = journalRequest;
  journalRequest = null;
  await page.evaluate(async () => {
    await window.securityApp.requireOfflineQueueClear(Promise.resolve());
    document.getElementById("app").removeAttribute("inert");
    window.openSyntheticDrawer("B");
  });
  const entry = title => ({ id: title, event_type: "observed", occurred_on: "2026-09-28", title });
  await oldJournal.fulfill({ json: { entries: [entry("Account A private journal")], total: 1 } });
  await page.evaluate(() => window.oldJournal);
  assert.doesNotMatch(await page.locator(".drawer").textContent(), /Account A/);
  await page.evaluate(() => { window.newJournal = window.panels.loadPlotJournalPreview("B", { onViewJournal() {} }); });
  await waitUntil(() => Boolean(journalRequest));
  await journalRequest.fulfill({ json: { entries: [entry("Account B journal")], total: 1 } });
  await page.locator(".journal-preview-text").waitFor();
  assert.match(await page.locator(".journal-preview-text").textContent(), /Account B journal/);
  await page.evaluate(() => window.panels.resetPlotPanelsForContextChange());
  for (const status of [500, 403, 401]) {
    logoutStatus = status;
    await page.evaluate(() => window.securityApp.handleAuthButton());
    assert.equal(await page.evaluate(() => window.securityApp.testAccountPresent()), true);
    assert.equal(await page.locator(".auth-gate").count(), 0);
    assert.match(await page.locator("#app-status-text").textContent(), /Sign out failed/);
    assert.doesNotMatch(await page.locator("body").textContent(), /Signed out/);
  }
  logoutStatus = 200;
  await page.evaluate(() => { void window.securityApp.handleAuthButton(); });
  await page.locator(".auth-gate").waitFor();
  assert.equal(await page.evaluate(() => window.securityApp.testAccountPresent()), false);
  assert.deepEqual(errors, []);
  console.log("Security browser regressions passed: identity cleanup, stale chat/journal results, explicit failed logout and successful retry.");
} finally {
  await browser?.close();
  await server.close();
}
