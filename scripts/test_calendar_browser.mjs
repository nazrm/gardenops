// Isolated browser regression: synthetic API only, no backend or credentials.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { createServer } = await import(require.resolve("vite"));
const { chromium } = require("playwright-core");
const root = fileURLToPath(new URL("../frontend", import.meta.url));
const server = await createServer({ root, configFile: false, server: { host: "127.0.0.1", port: 0 }, define: { __APP_VERSION__: '"test"' } });
let browser;
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH, headless: true });
  const page = await browser.newPage({ timezoneId: "Europe/Oslo" });
  page.setDefaultTimeout(10000);
  const errors = [];
  const queries = [];
  page.on("pageerror", error => errors.push(error.message));
  const preferences = { default_view: "month", selected_preset: "essential", visible_sources: ["garden_event"], include_recent_history: false, selected_plant_ids: [], selected_plot_ids: [], selected_zone_codes: [] };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, origin, "No external requests allowed");
    if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: '<!doctype html><div id="calendar-root" class="calendar-root"></div><div id="calendar-range-label"></div><div id="calendar-detail"></div><button data-calendar-view="month">month</button><button data-calendar-view="week">week</button><button data-calendar-view="agenda">agenda</button><button id="calendar-next-btn">next</button><button id="calendar-prev-btn">prev</button>' });
    if (!url.pathname.startsWith("/api/")) return route.continue();
    let body;
    if (url.pathname === "/api/calendar/preferences") {
      if (route.request().method() === "PATCH") body = { status: "ok", preferences: { ...preferences, ...route.request().postDataJSON() } };
      else body = { preferences, persisted: true, available_sources: [], presets: [], capabilities: {}, available_views: ["month", "week", "agenda"] };
    } else if (url.pathname === "/api/calendar/events") {
      queries.push(Object.fromEntries(url.searchParams));
      body = { events: [{ id: "synthetic-1", kind: "manual_event", source_key: "garden_event", title: "Synthetic calendar event", description: "", start_on: "2026-10-25", end_on: "2026-10-26", all_day: true, status: "active", read_only: true, target_type: "manual_event", target_id: "synthetic-1", plant_ids: [], plot_ids: [], updated_at_ms: 0, created_at_ms: 0 }], selected_plant_ids: [], selected_plot_ids: [], selected_zone_codes: [] };
    } else if (url.pathname === "/api/calendar/subscriptions") body = { subscriptions: [] };
    else throw new Error(`Unexpected synthetic API path: ${url.pathname}`);
    return route.fulfill({ json: body });
  });
  await page.clock.setFixedTime(new Date("2026-10-25T12:00:00+01:00"));
  await page.goto(origin);
  await page.evaluate(async () => {
    await import("/src/style.css");
    const api = await import("/src/services/api.ts");
    api.setActiveGardenContext(1);
    const offline = await import("/src/services/offlineQueue.ts");
    await offline.initOfflineQueue();
    const tab = await import("/src/tabs/calendarTab.ts");
    window.calendarTestTab = tab;
    tab.initCalendarTab({ isOnline: () => true, canWrite: () => false, getPlants: () => [], getPlots: () => [], getActiveTab: () => "activity", getSubMode: () => "calendar", showToast: message => { throw new Error(message); } });
    await tab.loadCalendar();
  });
  const event = page.locator('[data-calendar-event-id="synthetic-1"]');
  await event.first().waitFor();
  if (process.env.CALENDAR_TEST_SCREENSHOT) await page.screenshot({ path: process.env.CALENDAR_TEST_SCREENSHOT, fullPage: true });
  assert.equal(await event.first().locator(".garden-calendar-event-inner").evaluate(el => getComputedStyle(el).color), "rgb(39, 70, 92)");
  assert.equal(await event.first().getAttribute("data-calendar-source"), "garden_event");
  assert.match(await event.first().getAttribute("class"), /calendar-source-garden-event/);
  assert.ok(await page.locator(".garden-calendar-day-heading").count());
  for (const mode of ["week", "agenda", "month"]) {
    await page.locator(`[data-calendar-view="${mode}"]`).click();
    await page.waitForFunction(mode => document.querySelector(`[data-calendar-event-id="synthetic-1"].garden-calendar-${mode === "agenda" ? "list" : "grid"}-event`), mode);
    assert.match(await event.first().textContent(), /Synthetic calendar event/);
  }
  await page.locator("#calendar-next-btn").click();
  await page.waitForFunction(() => !document.querySelector('[data-calendar-event-id="synthetic-1"]'));
  await page.locator("#calendar-prev-btn").click();
  await event.first().waitFor();
  await page.evaluate(async () => {
    const i18n = await import("/src/core/i18n.ts");
    i18n.setLocale("no", { persist: false });
    window.calendarTestTab.refreshCalendarLocalization();
  });
  await page.waitForFunction(() => document.querySelector("#calendar-range-label")?.textContent?.toLowerCase().includes("oktober"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { document.querySelector("#calendar-root").hidden = true; });
  await page.evaluate(() => { document.querySelector("#calendar-root").hidden = false; });
  await event.first().waitFor({ state: "visible" });
  assert.ok(queries.some(query => query.start === "2026-09-28" && query.end === "2026-11-09"), "Month boundaries remain local dates across DST");
  assert.deepEqual(errors, []);
  console.log("Calendar browser regression passed: real v7 rendering, views, navigation, DST range, source classes and responsive reveal.");
} finally {
  await browser?.close();
  await server.close();
}
