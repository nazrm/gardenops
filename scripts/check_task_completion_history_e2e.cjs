#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { chromium } = require("../frontend/node_modules/playwright-core");

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5173";
const ROOT_DIR = path.resolve(__dirname, "..");
const CHROMIUM_EXECUTABLE = process.env.CHROMIUM_EXECUTABLE
  || (fs.existsSync("/usr/bin/chromium-browser") ? "/usr/bin/chromium-browser" : "/usr/bin/chromium");
const FROZEN_NOW_ISO = process.env.GARDENOPS_TASK_HISTORY_E2E_FROZEN_NOW_ISO
  || "2026-07-05T12:00:00.000Z";

function formatLocalDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDays(baseDate, days) {
  const next = new Date(baseDate);
  next.setDate(next.getDate() + days);
  return formatLocalDate(next);
}

const EXPECTED_SNOOZE_DATE = addDays(new Date(FROZEN_NOW_ISO), 7);

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

function e2ePythonEnv() {
  return {
    ...process.env,
    APP_ENV: process.env.APP_ENV || "test",
    AUTH_REQUIRED: process.env.AUTH_REQUIRED || "false",
    GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE:
      process.env.GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE || "1",
    UV_CACHE_DIR: process.env.UV_CACHE_DIR || "/tmp/gardenops-uv-cache",
  };
}

function runE2ePython(args) {
  const env = e2ePythonEnv();
  let command = "uv";
  let commandArgs = ["run", "python", ...args];
  if (process.env.GARDENOPS_TASK_HISTORY_E2E_RUN_DB_AS_POSTGRES === "1") {
    command = "sudo";
    commandArgs = [
      "-u",
      "postgres",
      "env",
      `APP_ENV=${env.APP_ENV}`,
      `AUTH_REQUIRED=${env.AUTH_REQUIRED}`,
      `GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE=${env.GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE}`,
      `GARDENOPS_LOGS_DIR=${env.GARDENOPS_LOGS_DIR || "/tmp/gardenops-task-history-e2e-logs"}`,
      `DATABASE_URL=${env.DATABASE_URL || ""}`,
      `UV_CACHE_DIR=${process.env.GARDENOPS_TASK_HISTORY_E2E_POSTGRES_UV_CACHE_DIR || "/tmp/gardenops-uv-cache-postgres"}`,
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

function dataSnapshot() {
  const raw = runE2ePython(["scripts/seed_task_completion_history_e2e.py", "snapshot"]);
  return JSON.parse(raw);
}

async function waitVisible(locator, label, timeout = 10000) {
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch (err) {
    throw new Error(`Expected visible ${label}: ${err.message}`);
  }
}

async function waitHidden(locator, label, timeout = 10000) {
  try {
    await locator.waitFor({ state: "hidden", timeout });
  } catch (err) {
    throw new Error(`Expected hidden ${label}: ${err.message}`);
  }
}

async function expectAttribute(locator, attribute, expected, label) {
  const actual = await locator.getAttribute(attribute);
  assert(actual === expected, `${label} expected ${attribute}=${expected}, got ${actual}`);
}

async function isFocused(locator) {
  return locator.evaluate((element) => document.activeElement === element);
}

async function setupPage(browser) {
  const page = await browser.newPage({
    locale: "en-GB",
    viewport: { width: 1440, height: 980 },
  });
  const browserErrors = [];
  const resourceLoadFailures = [];
  page.on("console", (msg) => {
    const text = msg.text();
    if (msg.type() === "error" && !text.startsWith("Failed to load resource:")) {
      browserErrors.push(text);
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
  await page.addInitScript((frozenNowIso) => {
    const RealDate = Date;
    const frozenMs = new RealDate(frozenNowIso).getTime();
    function FrozenDate(...args) {
      if (new.target) {
        return args.length === 0 ? new RealDate(frozenMs) : new RealDate(...args);
      }
      return new RealDate(frozenMs).toString();
    }
    Object.setPrototypeOf(FrozenDate, RealDate);
    FrozenDate.prototype = RealDate.prototype;
    FrozenDate.now = () => frozenMs;
    FrozenDate.parse = RealDate.parse;
    FrozenDate.UTC = RealDate.UTC;
    globalThis.Date = FrozenDate;
  }, FROZEN_NOW_ISO);
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
  return { page, browserErrors, resourceLoadFailures };
}

async function openTasksView(page) {
  const activityTab = page.locator("#top-tab-activity");
  await waitVisible(activityTab, "Activity tab");
  await activityTab.focus();
  await page.keyboard.press("Enter");
  const tasksSubmode = page.locator("#sub-mode-tasks");
  await waitVisible(tasksSubmode, "Tasks sub-mode");
  await tasksSubmode.focus();
  await page.keyboard.press("Enter");
  await expectAttribute(tasksSubmode, "aria-selected", "true", "Tasks sub-mode");
  await waitVisible(page.getByRole("heading", { name: /^Garden Tasks$/i }), "Garden Tasks heading");
  await waitVisible(page.locator("#tasks-list"), "Tasks list");
}

function taskCard(page, title) {
  return page.locator("#tasks-list .task-card").filter({ hasText: title }).first();
}

async function openCompletionDialogFromCard(page, card) {
  const button = card.getByRole("button", { name: /^Complete$/i });
  await waitVisible(button, "task complete button");
  await button.focus();
  await button.press("Enter");
  const dialog = page.locator(".task-completion-dialog").last();
  await waitVisible(dialog, "task completion dialog");
  return dialog;
}

async function main() {
  assertNoRouteMocks();

  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROMIUM_EXECUTABLE,
  });

  try {
    const { page, browserErrors, resourceLoadFailures } = await setupPage(browser);
    await openTasksView(page);

    const bloomCard = taskCard(page, "Observe bloom: Bloom E2E");
    const fertilizeCard = taskCard(page, "Fertilize 2 plants");
    const pruneCard = taskCard(page, "Prune 2 plants");
    await waitVisible(bloomCard, "bloom task card");
    await waitVisible(fertilizeCard, "fertilize task card");
    await waitVisible(pruneCard, "prune task card");

    await bloomCard.getByRole("button", { name: /Check again in 1 week/i }).click();
    const changeDateAction = page.locator("#toast-container .toast-action").filter({
      hasText: /Change date|Endre dato/i,
    }).last();
    await waitVisible(changeDateAction, "Change date toast action");
    const bloomToastText = await changeDateAction.evaluate(
      (element) => element.closest(".toast")?.textContent || "",
    );
    assert(
      bloomToastText.includes(EXPECTED_SNOOZE_DATE),
      `Expected snooze toast to mention ${EXPECTED_SNOOZE_DATE}, got ${bloomToastText}`,
    );
    await waitHidden(bloomCard, "bloom task card after snooze");

    await changeDateAction.click();
    const dateDialog = page.locator(".modal").filter({
      has: page.locator("input[type='date']"),
    }).last();
    await waitVisible(dateDialog, "snooze date dialog");
    const dateInput = dateDialog.locator("input[type='date']");
    await waitVisible(dateInput, "snooze date input");
    assert(
      await dateInput.inputValue() === EXPECTED_SNOOZE_DATE,
      `Expected snooze date input to default to ${EXPECTED_SNOOZE_DATE}`,
    );
    await dateDialog.getByRole("button", { name: /^Cancel$/i }).click();
    await waitHidden(dateDialog, "snooze date dialog after cancel");

    const fertilizeDialog = await openCompletionDialogFromCard(page, fertilizeCard);
    const fertA = fertilizeDialog.getByLabel("Fert A E2E", { exact: true });
    const fertB = fertilizeDialog.getByLabel("Fert B E2E", { exact: true });
    await waitVisible(fertA, "fertilize checkbox A");
    await waitVisible(fertB, "fertilize checkbox B");
    assert(await fertA.isChecked(), "Fert A checkbox should start selected");
    assert(await fertB.isChecked(), "Fert B checkbox should start selected");
    await fertB.uncheck();
    assert(await fertA.isChecked(), "Fert A checkbox should stay selected");
    assert(!(await fertB.isChecked()), "Fert B checkbox should be unchecked before completion");
    await fertilizeDialog.getByRole("button", { name: /^Complete$/i }).click();
    await waitHidden(fertilizeDialog, "fertilize completion dialog after submit");

    const updatedFertilizeCard = taskCard(page, "Fertilize: Fert B E2E");
    await waitVisible(updatedFertilizeCard, "updated fertilize task card");

    const snapshot = dataSnapshot();
    const tasksById = Object.fromEntries(snapshot.tasks.map((task) => [task.public_id, task]));
    const bloomTask = tasksById["tsk_e2e_bloom"];
    const fertilizeTask = tasksById["tsk_e2e_fertilize"];
    const pruneTask = tasksById["tsk_e2e_prune"];

    assert(bloomTask, "Snapshot missing bloom task");
    assert(fertilizeTask, "Snapshot missing fertilize task");
    assert(pruneTask, "Snapshot missing prune task");
    assert(bloomTask.status === "snoozed", `Expected bloom task snoozed, got ${bloomTask.status}`);
    assert(
      bloomTask.snoozed_until === EXPECTED_SNOOZE_DATE,
      `Expected bloom snoozed_until ${EXPECTED_SNOOZE_DATE}, got ${bloomTask.snoozed_until}`,
    );
    assert(fertilizeTask.status === "pending", `Expected fertilize task pending, got ${fertilizeTask.status}`);
    assertDeepEqual(
      fertilizeTask.plant_ids,
      ["FERT-B-E2E"],
      "Fertilize task should retain only the remaining plant",
    );
    assert(
      fertilizeTask.title === "Fertilize: Fert B E2E",
      `Expected updated fertilize title, got ${fertilizeTask.title}`,
    );

    const fertilizedEntries = snapshot.journal.filter((entry) => entry.event_type === "fertilized");
    assert(fertilizedEntries.length === 1, `Expected one fertilized journal entry, got ${fertilizedEntries.length}`);
    assertDeepEqual(
      fertilizedEntries[0].plant_ids,
      ["FERT-A-E2E"],
      "Journal entry should only reference the selected fertilized plant",
    );
    assert(
      fertilizedEntries[0].metadata?.source_task_id === "tsk_e2e_fertilize",
      `Expected fertilize journal source task id, got ${JSON.stringify(fertilizedEntries[0].metadata)}`,
    );
    assert(
      fertilizedEntries[0].metadata?.source_task_type === "fertilize",
      `Expected fertilize journal source task type, got ${JSON.stringify(fertilizedEntries[0].metadata)}`,
    );

    const pruneDialog = await openCompletionDialogFromCard(page, pruneCard);
    const pruneA = pruneDialog.getByLabel("Prune A E2E", { exact: true });
    const pruneB = pruneDialog.getByLabel("Prune B E2E", { exact: true });
    const pruneConfirm = pruneDialog.getByRole("button", { name: /^Complete$/i });
    await waitVisible(pruneA, "prune checkbox A");
    await waitVisible(pruneB, "prune checkbox B");
    assert(await isFocused(pruneA), "First prune checkbox should receive initial focus");
    assert(await pruneA.isChecked(), "First prune checkbox should start selected");
    assert(await pruneB.isChecked(), "Second prune checkbox should start selected");

    await page.keyboard.press("Space");
    assert(!(await pruneA.isChecked()), "Space should toggle the focused prune checkbox off");
    await page.keyboard.press("Tab");
    assert(await isFocused(pruneB), "Tab should move focus to the second prune checkbox");
    await page.keyboard.press("Space");
    assert(!(await pruneB.isChecked()), "Space should toggle the second prune checkbox off");
    assert(await pruneConfirm.isDisabled(), "Complete should be disabled when no prune plants are selected");
    await waitVisible(
      pruneDialog.getByText("Select at least one plant.", { exact: true }),
      "prune empty-selection feedback",
    );
    await page.keyboard.press("Space");
    assert(await pruneB.isChecked(), "Space should re-select the second prune checkbox");
    assert(!(await pruneConfirm.isDisabled()), "Complete should re-enable after keyboard re-selection");
    await pruneDialog.getByRole("button", { name: /^Cancel$/i }).click();
    await waitHidden(pruneDialog, "prune completion dialog after cancel");

    assert(browserErrors.length === 0, `Browser errors during task-history check:\n${browserErrors.join("\n")}`);
    assert(
      resourceLoadFailures.length === 0,
      `Resource load failures during task-history check:\n${resourceLoadFailures.join("\n")}`,
    );
    await page.close();
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exit(1);
});
