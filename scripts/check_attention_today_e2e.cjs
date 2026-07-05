#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { chromium } = require("../frontend/node_modules/playwright-core");

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5173";
const ROOT_DIR = path.resolve(__dirname, "..");
const CHROMIUM_EXECUTABLE = process.env.CHROMIUM_EXECUTABLE
  || (fs.existsSync("/usr/bin/chromium-browser") ? "/usr/bin/chromium-browser" : "/usr/bin/chromium");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertDeepEqual(actual, expected, message) {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  assert(actualJson === expectedJson, `${message}\nexpected: ${expectedJson}\nactual: ${actualJson}`);
}

function assertNoRouteMocks() {
  const source = fs.readFileSync(__filename, "utf8");
  const needles = [
    ["page", "route("].join("."),
    ["browserContext", "route("].join("."),
    ["context", "route("].join("."),
  ];
  const found = needles.filter((needle) => source.includes(needle));
  assert(found.length === 0, `This E2E must use real backend routes, found: ${found.join(", ")}`);
}

async function waitVisible(locator, label, timeout = 10000) {
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch (err) {
    throw new Error(`Expected visible ${label}: ${err.message}`);
  }
}

async function waitAttached(locator, label, timeout = 10000) {
  try {
    await locator.waitFor({ state: "attached", timeout });
  } catch (err) {
    throw new Error(`Expected attached ${label}: ${err.message}`);
  }
}

async function visibleCount(locator) {
  return locator.evaluateAll((elements) => elements.filter((element) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0
      && rect.height > 0
      && style.display !== "none"
      && style.visibility !== "hidden"
      && element.getAttribute("aria-hidden") !== "true";
  }).length);
}

async function boundingBox(locator, label) {
  await waitVisible(locator, label);
  const box = await locator.boundingBox();
  assert(box && box.width > 0 && box.height > 0, `Missing layout box for ${label}`);
  return box;
}

async function expectAttribute(locator, attribute, expected, label) {
  const actual = await locator.getAttribute(attribute);
  assert(actual === expected, `${label} expected ${attribute}=${expected}, got ${actual}`);
}

async function activateTabByKeyboard(page, selector, label) {
  const tab = page.locator(selector);
  await waitVisible(tab, label);
  await tab.focus();
  await page.keyboard.press("Enter");
}

function e2ePythonEnv() {
  return {
    ...process.env,
    APP_ENV: process.env.APP_ENV || "test",
    AUTH_REQUIRED: process.env.AUTH_REQUIRED || "false",
    GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE:
      process.env.GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE || "1",
    GARDENOPS_ATTENTION_FROZEN_NOW_MS:
      process.env.GARDENOPS_ATTENTION_FROZEN_NOW_MS || "1783180800000",
    GARDENOPS_ATTENTION_FROZEN_DATE:
      process.env.GARDENOPS_ATTENTION_FROZEN_DATE || "2026-07-05",
    UV_CACHE_DIR: process.env.UV_CACHE_DIR || "/tmp/gardenops-uv-cache",
  };
}

function runE2ePython(args) {
  const env = e2ePythonEnv();
  let command = "uv";
  let commandArgs = ["run", "python", ...args];
  if (process.env.GARDENOPS_ATTENTION_E2E_RUN_DB_AS_POSTGRES === "1") {
    command = "sudo";
    commandArgs = [
      "-u",
      "postgres",
      "env",
      `APP_ENV=${env.APP_ENV}`,
      `AUTH_REQUIRED=${env.AUTH_REQUIRED}`,
      `GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE=${env.GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE}`,
      `GARDENOPS_ATTENTION_FROZEN_NOW_MS=${env.GARDENOPS_ATTENTION_FROZEN_NOW_MS}`,
      `GARDENOPS_ATTENTION_FROZEN_DATE=${env.GARDENOPS_ATTENTION_FROZEN_DATE}`,
      `GARDENOPS_LOGS_DIR=${env.GARDENOPS_LOGS_DIR || "/tmp/gardenops-attention-e2e-logs"}`,
      `DATABASE_URL=${env.DATABASE_URL || ""}`,
      `UV_CACHE_DIR=${process.env.GARDENOPS_ATTENTION_E2E_POSTGRES_UV_CACHE_DIR || "/tmp/gardenops-uv-cache-postgres"}`,
      "uv",
      "run",
      "python",
      ...args,
    ];
  }
  const result = spawnSync(command, commandArgs, {
    cwd: ROOT_DIR,
    env,
    encoding: "utf8",
  });
  assert(
    result.status === 0,
    `E2E Python command failed: ${command} ${commandArgs.join(" ")}\n${result.stderr || result.stdout}`,
  );
  return result.stdout.trim();
}

function notificationSnapshot() {
  const raw = runE2ePython(["scripts/seed_attention_today_e2e.py", "snapshot-notifications"]);
  return JSON.parse(raw);
}

function assertNoActiveGeneratedWateringNotification(rows) {
  const activeWatering = rows.filter((row) => (
    row.dismissed === 0
    && row.cleared_at_ms === null
    && row.target_type === "task"
    && row.target_id === "tsk_attention_today_e2e_water_hydrangea"
  ));
  assert(
    activeWatering.length === 0,
    `Expected no active generated hydrangea watering notification, found ${JSON.stringify(activeWatering)}`,
  );
}

async function assertMinTouchTarget(locator, label) {
  const box = await boundingBox(locator, label);
  assert(
    box.width >= 44 && box.height >= 44,
    `${label} touch target must be at least 44x44px, got ${Math.round(box.width)}x${Math.round(box.height)}`,
  );
}

async function setupPage(browser, viewport) {
  const page = await browser.newPage({ viewport });
  const browserErrors = [];
  const resourceLoadFailures = [];
  page.on("console", (msg) => {
    const text = msg.text();
    if (msg.type() === "error" && !text.startsWith("Failed to load resource:")) {
      browserErrors.push(msg.text());
    }
  });
  page.on("pageerror", (err) => {
    browserErrors.push(err.stack || err.message);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/") && response.status() >= 400) {
      browserErrors.push(`API ${response.status()} ${url.pathname}`);
    } else if (response.status() >= 400) {
      resourceLoadFailures.push(`Resource ${response.status()} ${url.pathname}`);
    }
  });
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
  return { page, browserErrors, resourceLoadFailures };
}

async function checkDesktop(browser) {
  const notificationRowsBefore = notificationSnapshot();
  assertNoActiveGeneratedWateringNotification(notificationRowsBefore);
  const { page, browserErrors, resourceLoadFailures } = await setupPage(browser, { width: 1440, height: 980 });
  const mapTabpanel = page.getByRole("tabpanel", { name: /map/i });
  await waitVisible(mapTabpanel, "Map tabpanel");

  const panel = page.getByTestId("attention-today-panel");
  await waitVisible(panel, "attention-today-panel");
  const todayRegion = page.getByRole("region", { name: /^Today$/i });
  await waitVisible(todayRegion, "Today region");

  await waitVisible(todayRegion.getByText("Water indoor basil", { exact: true }), "seeded indoor basil task");
  await waitVisible(todayRegion.getByText("Check mildew on cucumber", { exact: true }), "seeded issue follow-up");
  await waitVisible(todayRegion.getByText("18 mm rain expected", { exact: true }), "seeded rain warning");
  await waitVisible(
    todayRegion.getByText("Backup status needs review", { exact: true }),
    "seeded status notification",
  );
  const activeHydrangeaCount = await visibleCount(
    todayRegion
      .getByTestId("attention-today-section-needs_attention")
      .getByText("Water hydrangea", { exact: true }),
  );
  assert(activeHydrangeaCount === 0, "Generated outdoor hydrangea watering should be absent from active attention");
  const todayHeadings = page.getByRole("heading", { name: /^Today$/i });
  const headingCount = await visibleCount(todayHeadings);
  assert(headingCount === 1, `Expected exactly one visible Today heading, got ${headingCount}`);

  const noAction = todayRegion.getByTestId("attention-today-section-no_action_needed");
  await waitVisible(noAction, "no-action section");
  const noActionSummary = noAction.locator("summary");
  await expectAttribute(noActionSummary, "aria-expanded", "false", "no-action summary");
  const initiallyOpen = await noAction.evaluate((element) => element.hasAttribute("open"));
  assert(!initiallyOpen, "No-action section should be initially collapsed");
  await noActionSummary.click();
  await expectAttribute(noActionSummary, "aria-expanded", "true", "no-action summary after expand");
  const expandedOpen = await noAction.evaluate((element) => element.hasAttribute("open"));
  assert(expandedOpen, "No-action section should expand when clicked");
  await waitVisible(
    noAction.getByText("18 mm rain expected already covers scheduled watering for Hydrangea.", { exact: true }),
    "rain-covered watering explanation",
  );

  const panelBox = await boundingBox(panel, "attention-today-panel");
  assert(panelBox.width <= 360, `Desktop Today panel should be <= 360px wide, got ${panelBox.width}`);

  const stage = page.locator(".map-stage").first();
  const stageBox = await boundingBox(stage, "map stage");
  const mapLayoutBox = await boundingBox(page.locator(".map-layout"), "map layout");
  assert(
    stageBox.width >= mapLayoutBox.width * 0.6,
    `Map stage should be at least 60% of the map workspace, got ${stageBox.width}/${mapLayoutBox.width}`,
  );
  assert(stageBox.x + stageBox.width <= panelBox.x, "Map stage should not overlap the Today panel");

  const layersPanel = page.locator("#map-layers-panel");
  await waitVisible(layersPanel, "map controls panel");
  await assertMinTouchTarget(todayRegion.getByRole("button", { name: /open task/i }), "task primary action");
  await assertMinTouchTarget(todayRegion.getByRole("button", { name: /open issue/i }), "issue primary action");

  const settingsButton = todayRegion.getByTestId("attention-today-settings");
  await settingsButton.click();
  const settingsDialog = page.getByRole("dialog", { name: /attention settings/i });
  await waitVisible(settingsDialog, "attention settings dialog");
  for (const label of ["Calm", "Balanced", "Detailed", "Custom"]) {
    await waitVisible(settingsDialog.getByLabel(label, { exact: true }), `${label} preference option`);
  }
  const balancedOption = settingsDialog.getByLabel("Balanced", { exact: true });
  assert(await balancedOption.isChecked(), "Balanced preset should be selected before edits");
  await settingsDialog.getByLabel("Calm", { exact: true }).check();
  await page.keyboard.press("Shift+Tab");
  const focusInsideDialog = await settingsDialog.evaluate((dialog) => dialog.contains(document.activeElement));
  assert(focusInsideDialog, "Attention settings dialog should trap keyboard focus");
  await settingsDialog.getByRole("button", { name: /^Cancel$/i }).click();

  await settingsButton.click();
  const settingsAfterCancel = page.getByRole("dialog", { name: /attention settings/i });
  await waitVisible(settingsAfterCancel, "attention settings dialog after cancel");
  assert(
    await settingsAfterCancel.getByLabel("Balanced", { exact: true }).isChecked(),
    "Cancel should preserve the saved Balanced preset",
  );
  await settingsAfterCancel.getByLabel("Detailed", { exact: true }).check();
  await settingsAfterCancel.getByRole("button", { name: /^Save$/i }).click();
  await settingsAfterCancel.waitFor({ state: "detached", timeout: 10000 });
  await waitVisible(todayRegion.getByText("Shown because", { exact: false }), "preference guardrail copy");

  await page.evaluate(() => {
    window.__attentionMapStagePointerDowns = 0;
    const stageElement = document.querySelector(".map-stage");
    stageElement?.addEventListener("pointerdown", () => {
      window.__attentionMapStagePointerDowns += 1;
    }, { once: true });
  });
  const beforeTransform = await page.locator("#map-camera").evaluate((element) => {
    return window.getComputedStyle(element).transform;
  });
  await page.mouse.move(stageBox.x + stageBox.width / 2, stageBox.y + stageBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(stageBox.x + stageBox.width / 2 + 90, stageBox.y + stageBox.height / 2 + 35, {
    steps: 8,
  });
  await page.mouse.up();
  const afterTransform = await page.locator("#map-camera").evaluate((element) => {
    return window.getComputedStyle(element).transform;
  });
  const pointerDowns = await page.evaluate(() => window.__attentionMapStagePointerDowns || 0);
  assert(
    pointerDowns > 0 || beforeTransform !== afterTransform,
    "Pointer drag outside the Today panel did not reach the map stage or move the map camera",
  );

  await todayRegion.getByRole("button", { name: /open task/i }).click();
  await waitVisible(page.getByRole("tabpanel", { name: /activity/i }), "Activity tabpanel");
  await waitVisible(page.getByRole("heading", { name: /^Garden Tasks$/i }), "Garden Tasks heading");
  await expectAttribute(page.locator("#top-tab-activity"), "aria-selected", "true", "Activity tab");
  await expectAttribute(page.locator("#sub-mode-tasks"), "aria-selected", "true", "Tasks sub-mode");
  await waitVisible(page.locator("#tasks-list").getByText("Water indoor basil", { exact: true }), "task in Tasks view");

  await activateTabByKeyboard(page, "#top-tab-map", "Map tab");
  await waitVisible(todayRegion, "Today region after returning to Map");
  await todayRegion
    .getByTestId("attention-today-primary-action-attn-issue-iss_attention_today_e2e_mildew")
    .click();
  await waitVisible(page.getByRole("tabpanel", { name: /activity/i }), "Activity tabpanel after issue action");
  await waitVisible(page.getByRole("heading", { name: /^Garden Issues$/i }), "Garden Issues heading");
  await expectAttribute(page.locator("#top-tab-activity"), "aria-selected", "true", "Activity tab after issue action");
  await expectAttribute(page.locator("#sub-mode-issues"), "aria-selected", "true", "Issues sub-mode");
  await waitVisible(
    page.locator("#issues-list").getByText("Check mildew on cucumber", { exact: true }),
    "issue in Issues view",
  );
  await activateTabByKeyboard(page, "#top-tab-map", "Map tab after issue action");
  await waitVisible(mapTabpanel, "Map tabpanel after issue context restore");
  await expectAttribute(page.locator("#top-tab-map"), "aria-selected", "true", "Map tab after issue context restore");
  await waitVisible(todayRegion, "Today region after issue context restore");

  const notificationRowsAfter = notificationSnapshot();
  assertDeepEqual(
    notificationRowsAfter,
    notificationRowsBefore,
    "Opening Today and using Attention actions must not mutate notification_events",
  );

  assert(browserErrors.length === 0, `Browser errors during desktop check:\n${browserErrors.join("\n")}`);
  assert(
    resourceLoadFailures.length === 0,
    `Resource load failures during desktop check:\n${resourceLoadFailures.join("\n")}`,
  );
  await page.close();
}

async function checkMobile(browser) {
  const { page, browserErrors, resourceLoadFailures } = await setupPage(browser, { width: 390, height: 820 });
  const handle = page.getByTestId("attention-today-mobile-handle");
  await waitVisible(handle, "mobile Today handle");
  await expectAttribute(handle, "aria-expanded", "false", "mobile Today handle");
  await assertMinTouchTarget(handle, "mobile Today handle");

  await handle.focus();
  await page.keyboard.press("Enter");
  await expectAttribute(handle, "aria-expanded", "true", "mobile Today handle");
  const sheet = page.getByRole("dialog", { name: /^Today$/i });
  await waitVisible(sheet, "mobile Today sheet");
  await waitVisible(sheet.getByText("Water indoor basil", { exact: true }), "mobile indoor basil task");

  await page.keyboard.press("Escape");
  await expectAttribute(handle, "aria-expanded", "false", "mobile Today handle after Escape");
  const focusedTestId = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
  assert(focusedTestId === "attention-today-mobile-handle", "Focus should return to the mobile Today handle");

  await handle.focus();
  await page.keyboard.press("Enter");
  await expectAttribute(handle, "aria-expanded", "true", "mobile Today handle after reopen");
  await page.getByTestId("attention-today-mobile-close").click();
  await expectAttribute(handle, "aria-expanded", "false", "mobile Today handle after close");
  const focusedAfterClose = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
  assert(focusedAfterClose === "attention-today-mobile-handle", "Close button should return focus to the handle");

  assert(browserErrors.length === 0, `Browser errors during mobile check:\n${browserErrors.join("\n")}`);
  assert(
    resourceLoadFailures.length === 0,
    `Resource load failures during mobile check:\n${resourceLoadFailures.join("\n")}`,
  );
  await page.close();
}

async function main() {
  assertNoRouteMocks();
  const browser = await chromium.launch({
    executablePath: CHROMIUM_EXECUTABLE,
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    await checkDesktop(browser);
    await checkMobile(browser);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
