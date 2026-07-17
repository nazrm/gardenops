"use strict";

const {
  assertBrowserProfileContract,
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  dismissProactivePasskeyPrompt,
} = require("../completeJourneyBrowser.cjs");
const {
  assert,
  assertAXNode,
  assertAxeState,
  assertFocusInside,
  assertFocusVisibleAndUnobscured,
  assertPageStructure,
  assertTouchTargets,
  chromiumAXTree,
  visible,
  waitFor,
} = require("../completeJourneyAssertions.cjs");

const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture

function profileLabel(options) {
  return `Phase 8 ${options.role}:${options.profile}`;
}

function taskTitle(fixture, key) {
  const taskId = fixture.phase_two?.task_ids?.[key];
  const title = fixture.phase_two?.task_titles?.[taskId];
  assert(taskId && title, `Missing Phase 8 task fixture: ${key}`);
  return title;
}

async function browserRuntime(page) {
  return {
    ...await page.evaluate(() => ({
      device_pixel_ratio: window.devicePixelRatio,
      has_touch: navigator.maxTouchPoints > 0,
      is_mobile: /Mobi|Android|iPad/i.test(navigator.userAgent),
      max_touch_points: navigator.maxTouchPoints,
      prefers_reduced_motion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      user_agent: navigator.userAgent,
    })),
    viewport: page.viewportSize(),
  };
}

function tabButton(page, tab) {
  return page.locator(`[data-tab='${tab}']:visible`).first();
}

async function focusByKeyboard(page, locator, label, { reverse = false } = {}) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (await locator.evaluate((element) => document.activeElement === element)) return;
    await page.keyboard.press(reverse ? "Shift+Tab" : "Tab");
  }
  assert(false, `${label} was not reachable by ${reverse ? "Shift+Tab" : "Tab"}`);
}

async function activateTab(page, tab, label) {
  const button = tabButton(page, tab);
  await visible(button, `${label} ${tab} tab`);
  await focusByKeyboard(page, button, `${label} ${tab} tab`);
  await assertFocusVisibleAndUnobscured(page, button, `${label} ${tab} tab`);
  await button.press("Enter");
  return button;
}

async function openTasks(page, label) {
  await activateTab(page, "activity", label);
  const tasks = page.locator("#sub-mode-tasks:visible, [data-sub-mode='tasks']:visible").first();
  await visible(tasks, `${label} tasks sub-mode`);
  await focusByKeyboard(page, tasks, `${label} tasks sub-mode`);
  await assertFocusVisibleAndUnobscured(page, tasks, `${label} tasks sub-mode`);
  await tasks.press("Enter");
  await visible(page.locator("#tasks-tab-content"), `${label} tasks content`);
  await visible(page.locator("#tasks-list"), `${label} task list`);
}

async function exerciseMapAndToday(page, options, result) {
  const label = profileLabel(options);
  const map = await activateTab(page, "map", label);
  const nextTab = tabButton(page, "garden");
  await page.keyboard.press("Tab");
  await waitFor(async () => await nextTab.evaluate((element) => document.activeElement === element),
    `${label} primary navigation tab order`);
  await assertFocusVisibleAndUnobscured(page, nextTab, `${label} Garden navigation control after keyboard traversal`);
  await nextTab.press("Enter");
  await visible(page.locator("#plants-view"), `${label} Garden tab panel`);
  await focusByKeyboard(page, map, `${label} Map navigation return`);
  await map.press("Enter");
  await visible(page.locator("#map-grid"), `${label} map-first surface`);
  result.checks.map_populated = true;
  result.axe.push(await assertAxeState(page, "authenticated-map"));

  const mobileHandle = page.locator("#attention-today-mobile-handle:visible");
  let surface;
  if (await mobileHandle.count()) {
    await focusByKeyboard(page, mobileHandle, `${label} Today handle`);
    await assertFocusVisibleAndUnobscured(page, mobileHandle, `${label} Today handle`);
    await mobileHandle.press("Enter");
    surface = page.locator("#attention-today-mobile-sheet");
    await visible(surface, `${label} Today sheet`);
    assert(await surface.getAttribute("aria-modal") === "true", `${label} Today sheet is not modal`);
    await assertFocusInside(surface, `${label} Today sheet`);
  } else {
    surface = page.locator("#attention-today-panel");
    await visible(surface, `${label} Today panel`);
  }
  const noAction = surface.locator('[data-testid="attention-today-section-no_action_needed"]');
  await visible(noAction.locator("summary"), `${label} no-action-needed disclosure`);
  const summary = noAction.locator("summary");
  await focusByKeyboard(page, summary, `${label} no-action-needed disclosure`);
  await assertFocusVisibleAndUnobscured(page, summary, `${label} no-action-needed disclosure`);
  await summary.press("Space");
  await waitFor(async () => await noAction.evaluate((element) => element.hasAttribute("open")),
    `${label} expanded no-action-needed history`);
  result.checks.today_disclosure = true;
  result.axe.push(await assertAxeState(page, "today-attention"));

  if (await mobileHandle.count()) {
    const close = surface.locator("[data-testid='attention-today-mobile-close']");
    await visible(close, `${label} Today close control`);
    await focusByKeyboard(page, close, `${label} Today close control`);
    await close.press("Enter");
    await surface.waitFor({ state: "hidden" });
    await waitFor(async () => await mobileHandle.evaluate((element) => document.activeElement === element),
      `${label} Today trigger focus return`);
    result.checks.today_focus_return = true;
    result.touch_targets = await assertTouchTargets(mobileHandle, `${label} Today handle`);
    const mobileFab = page.locator("#mobile-fab:visible");
    if (await mobileFab.count()) {
      result.touch_targets.push(...await assertTouchTargets(mobileFab, `${label} Quick Actions trigger`));
    }
  }
}

async function exerciseTaskValidation(page, options, result) {
  const label = profileLabel(options);
  await openTasks(page, label);
  const title = taskTitle(options.fixture, "fertilize_grouped");
  const card = page.locator("#tasks-list .task-card").filter({ hasText: title }).first();
  await visible(card, `${label} grouped fertilize task`);
  const complete = card.getByRole("button", { name: /^Complete$/i });
  await visible(complete, `${label} grouped task Complete action`);
  await focusByKeyboard(page, complete, `${label} grouped task Complete action`);
  await complete.press("Enter");
  const dialog = page.locator(".modal").filter({ has: page.locator(".task-completion-dialog") }).last();
  await visible(dialog, `${label} task completion dialog`);
  await assertFocusInside(dialog, `${label} task completion dialog`);
  const initialFocus = dialog.locator(".task-completion-list input[type='checkbox']").first();
  await assertFocusVisibleAndUnobscured(page, initialFocus, `${label} task completion initial plant selection focus`);
  const clear = dialog.locator(".task-completion-clear");
  await focusByKeyboard(page, clear, `${label} task completion clear action`);
  await clear.press("Enter");
  const feedback = dialog.locator(".task-completion-feedback");
  await visible(feedback, `${label} task completion validation feedback`);
  assert(await dialog.locator(".confirm-yes").isDisabled(),
    `${label} task completion allowed an empty plant selection`);
  const tree = await chromiumAXTree(page);
  assertAXNode(tree, { role: "dialog" }, `${label} task completion dialog`);
  assertAXNode(tree, { role: "status" }, `${label} task completion validation feedback`);
  const snapshot = await dialog.ariaSnapshot();
  assert(snapshot.includes("dialog"), `${label} task completion aria snapshot lost dialog semantics`);
  result.checks.task_completion_validation = true;
  result.axe.push(await assertAxeState(page, "task-completion-validation"));
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await waitFor(async () => await complete.evaluate((element) => document.activeElement === element),
    `${label} task completion Escape focus return`);
  if (options.profile !== "desktop") return;

  await complete.press("Enter");
  const cancelledDialog = page.locator(".modal").filter({ has: page.locator(".task-completion-dialog") }).last();
  await visible(cancelledDialog, `${label} task completion dialog for Cancel`);
  const cancel = cancelledDialog.locator(".confirm-no");
  await focusByKeyboard(page, cancel, `${label} task completion Cancel action`);
  await cancel.press("Enter");
  await cancelledDialog.waitFor({ state: "detached" });
  await waitFor(async () => await complete.evaluate((element) => document.activeElement === element),
    `${label} task completion Cancel focus return`);

  await complete.press("Enter");
  const confirmDialog = page.locator(".modal").filter({ has: page.locator(".task-completion-dialog") }).last();
  await visible(confirmDialog, `${label} task completion dialog for confirmation`);
  const selectAll = confirmDialog.locator(".task-completion-select-all");
  await focusByKeyboard(page, selectAll, `${label} task completion select all action`);
  await selectAll.press("Space");
  const confirm = confirmDialog.locator(".confirm-yes");
  assert(!await confirm.isDisabled(), `${label} task completion confirmation remained disabled`);
  const completed = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/tasks/${options.fixture.phase_two.task_ids.fertilize_grouped}/action`
  ));
  await focusByKeyboard(page, confirm, `${label} task completion confirmation action`);
  await confirm.press("Enter");
  assert((await completed).status() === 200, `${label} task completion did not succeed`);
  await confirmDialog.waitFor({ state: "detached" });
  await card.waitFor({ state: "hidden" });
  result.checks.task_completion_confirmed = true;
}

async function exerciseNotifications(page, options, result) {
  const label = profileLabel(options);
  await activateTab(page, "map", label);
  const bell = page.locator("#notification-bell:visible");
  await visible(bell, `${label} notification bell`);
  await focusByKeyboard(page, bell, `${label} notification bell`);
  await assertFocusVisibleAndUnobscured(page, bell, `${label} notification bell`);
  await bell.press("Enter");
  const panel = page.locator("#notification-panel");
  await visible(panel, `${label} notification panel`);
  await assertFocusInside(panel, `${label} notification panel`);
  const tabs = panel.locator("[role='tab']");
  await visible(tabs.first(), `${label} notification tabs`);
  await focusByKeyboard(page, tabs.first(), `${label} notification tabs`);
  await tabs.first().press("ArrowRight");
  await waitFor(async () => await panel.locator("[role='tab'][aria-selected='true']").count() === 1,
    `${label} notification tab state`);
  const tree = await chromiumAXTree(page);
  assertAXNode(tree, { role: "dialog" }, `${label} notification panel`);
  assertAXNode(tree, { role: "tablist" }, `${label} notification tablist`);
  result.checks.notifications_keyboard = true;
  result.axe.push(await assertAxeState(page, "notification-panel"));
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
  await waitFor(async () => await bell.evaluate((element) => document.activeElement === element),
    `${label} notification trigger focus return`);
  result.checks.notifications_focus_return = true;
}

async function assertViewerTaskWriteDenied(page, diagnostics, fixture, label) {
  const taskId = fixture.phase_two.task_ids.fertilize_mobile;
  const before = {
    console: diagnostics.consoleErrors.length,
    http: diagnostics.httpErrors.length,
  };
  const response = await page.evaluate(async ({ gardenId, requestPath }) => {
    const csrf = document.cookie
      .split("; ")
      .find((part) => part.startsWith("gardenops_csrf="))
      ?.slice("gardenops_csrf=".length) || "";
    const result = await fetch(requestPath, {
      body: JSON.stringify({ action: "complete" }),
      credentials: "include",
      headers: {
        "content-type": "application/json",
        "x-csrf-token": decodeURIComponent(csrf),
        "x-garden-id": String(gardenId),
      },
      method: "POST",
    });
    return { body: await result.json(), status: result.status };
  }, {
    gardenId: fixture.gardens.alpha.id,
    requestPath: `/api/tasks/${encodeURIComponent(taskId)}/action`,
  });
  assert(response.status === 403 && response.body?.detail === "Forbidden: write access required",
    `${label} viewer task mutation did not reach the authorization boundary`);
  await waitFor(() => diagnostics.httpErrors.length === before.http + 1,
    `${label} viewer task denial response`);
  const httpErrors = diagnostics.httpErrors.splice(before.http);
  assert(JSON.stringify(httpErrors) === JSON.stringify([`403 /api/tasks/${taskId}/action`]),
    `${label} viewer task denial emitted unexpected HTTP diagnostics`);
  await waitFor(() => diagnostics.consoleErrors.length === before.console + 1,
    `${label} viewer task denial console diagnostic`);
  diagnostics.consoleErrors.splice(before.console, 1);
}

async function exerciseViewerBoundary(page, options, result) {
  const label = profileLabel(options);
  await activateTab(page, "map", label);
  await visible(page.locator("#map-grid"), `${label} map-first surface`);
  await visible(page.locator("#garden-role-chip"), `${label} role chip`);
  await openTasks(page, label);
  const add = page.locator("#tasks-add-btn");
  assert(await add.count() === 0 || await add.isDisabled(),
    `${label} viewer retained a task creation mutation`);
  assert(await page.locator("#tasks-list .task-card").count() > 0,
    `${label} viewer lost access to readable task information`);
  await assertViewerTaskWriteDenied(page, result.diagnostics, options.fixture, label);
  result.checks.viewer_read_only = true;
  result.checks.viewer_direct_task_write_denied = true;
  result.checks.viewer_readable_tasks = true;
  result.axe.push(await assertAxeState(page, "read-only-map-and-tasks"));
}

async function runProfile(options) {
  const username = options.role === "viewer" ? options.fixture.roles.viewer : options.username;
  const password = options.role === "viewer" ? VIEWER_PASSWORD : options.password;
  const guarded = await createGuardedContext(
    options.browser,
    options.devices,
    options.profile,
    options.artifactDir,
    `phase-eight-${options.role}-${options.profile}`,
    { baseUrl: options.baseUrl },
  );
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page, {
    authType: "session",
    role: options.role,
    username,
  });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    axe: [],
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile: options.profile,
    requests: [],
    role: options.role,
    touch_targets: [],
    trace: null,
  };
  result.diagnostics = guarded.diagnostics;
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const auth = await authenticate(page, username, password);
    recorder.setGardenId(auth.garden_id);
    guarded.markAuthenticated();
    await dismissProactivePasskeyPrompt(page);
    result.browser_profile = { ...result.browser_profile, ...await browserRuntime(page) };
    result.browser_profile.user_agent_contract = assertBrowserProfileContract(
      options.profile,
      result.browser_profile,
    );
    if (options.role === "viewer") {
      await exerciseViewerBoundary(page, options, result);
    } else {
      await exerciseMapAndToday(page, options, result);
      if (options.profile === "desktop" || options.profile === "tablet") {
        await exerciseTaskValidation(page, options, result);
      }
      if (options.profile === "desktop") await exerciseNotifications(page, options, result);
    }
    result.structure = await assertPageStructure(page, profileLabel(options), {
      enforceControlNames: false,
    });
    assertDiagnosticsClean(guarded.diagnostics, profileLabel(options));
    result.checks.browser_diagnostics = true;
    result.assertions.passed.push("phase-eight-accessibility-responsive", "browser-diagnostics-clean");
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    result.diagnostics = guarded.diagnostics;
    await recorder.settle();
    result.requests = recorder.records;
    try { result.trace = await guarded.close(status); } catch (error) { if (!caughtError) caughtError = error; }
  }
  return { error: caughtError, result };
}

async function runAccessibilityAndResponsive(options, profileRunner = runProfile) {
  const profiles = [
    ["admin", "desktop"],
    ["admin", "mobile"],
    ["admin", "tablet"],
    ["admin", "desktop-reduced-motion"],
    ["admin", "mobile-reduced-motion"],
    ["admin", "desktop-reflow-200"],
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

module.exports = { runAccessibilityAndResponsive };
