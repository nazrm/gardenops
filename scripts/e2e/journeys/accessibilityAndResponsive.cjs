"use strict";

const {
  assertBrowserProfileContract,
  assertDiagnosticsClean,
  authenticate,
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

async function activateTab(page, tab, label) {
  const button = tabButton(page, tab);
  await visible(button, `${label} ${tab} tab`);
  await button.focus();
  await assertFocusVisibleAndUnobscured(page, button, `${label} ${tab} tab`);
  await button.press("Enter");
  return button;
}

async function openTasks(page, label) {
  await activateTab(page, "activity", label);
  const tasks = page.locator("#sub-mode-tasks:visible, [data-sub-mode='tasks']:visible").first();
  await visible(tasks, `${label} tasks sub-mode`);
  await tasks.focus();
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
  await assertFocusVisibleAndUnobscured(page, nextTab, `${label} Garden tab after keyboard navigation`);
  await map.focus();
  await map.press("Enter");
  await visible(page.locator("#map-grid"), `${label} map-first surface`);
  result.checks.map_populated = true;
  result.axe.push(await assertAxeState(page, "authenticated-map"));

  const mobileHandle = page.locator("#attention-today-mobile-handle:visible");
  let surface;
  if (await mobileHandle.count()) {
    await mobileHandle.focus();
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
  await summary.focus();
  await assertFocusVisibleAndUnobscured(page, summary, `${label} no-action-needed disclosure`);
  await summary.press("Space");
  await waitFor(async () => await noAction.evaluate((element) => element.hasAttribute("open")),
    `${label} expanded no-action-needed history`);
  result.checks.today_disclosure = true;
  result.axe.push(await assertAxeState(page, "today-attention"));

  if (await mobileHandle.count()) {
    const close = surface.locator("[data-testid='attention-today-mobile-close']");
    await visible(close, `${label} Today close control`);
    await close.focus();
    await close.press("Enter");
    await surface.waitFor({ state: "hidden" });
    await waitFor(async () => await mobileHandle.evaluate((element) => document.activeElement === element),
      `${label} Today trigger focus return`);
    result.checks.today_focus_return = true;
    result.touch_targets = [
      ...await assertTouchTargets(mobileHandle, `${label} Today handle`),
      ...await assertTouchTargets(page.locator("#mobile-fab:visible"), `${label} Quick Actions trigger`),
    ];
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
  await complete.focus();
  await complete.press("Enter");
  const dialog = page.locator(".modal").filter({ has: page.locator(".task-completion-dialog") }).last();
  await visible(dialog, `${label} task completion dialog`);
  await assertFocusInside(dialog, `${label} task completion dialog`);
  const initialFocus = dialog.locator(".task-completion-select-all");
  await assertFocusVisibleAndUnobscured(page, initialFocus, `${label} task completion initial focus`);
  await dialog.locator(".task-completion-clear").focus();
  await dialog.locator(".task-completion-clear").press("Enter");
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
  await dialog.locator(".confirm-no").focus();
  await dialog.locator(".confirm-no").press("Enter");
  await dialog.waitFor({ state: "detached" });
}

async function exerciseNotifications(page, options, result) {
  const label = profileLabel(options);
  await activateTab(page, "map", label);
  const bell = page.locator("#notification-bell:visible");
  await visible(bell, `${label} notification bell`);
  await bell.focus();
  await assertFocusVisibleAndUnobscured(page, bell, `${label} notification bell`);
  await bell.press("Enter");
  const panel = page.locator("#notification-panel");
  await visible(panel, `${label} notification panel`);
  await assertFocusInside(panel, `${label} notification panel`);
  const tabs = panel.locator("[role='tab']");
  await visible(tabs.first(), `${label} notification tabs`);
  await tabs.first().focus();
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

async function exerciseViewerBoundary(page, options, result) {
  const label = profileLabel(options);
  await activateTab(page, "map", label);
  await visible(page.locator("#map-grid"), `${label} map-first surface`);
  await visible(page.locator("#garden-role-chip"), `${label} role chip`);
  await openTasks(page, label);
  const add = page.locator("#tasks-add-btn");
  assert(await add.count() === 0 || await add.isDisabled(),
    `${label} viewer retained a task creation mutation`);
  result.checks.viewer_read_only = true;
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
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    axe: [],
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile: options.profile,
    role: options.role,
    touch_targets: [],
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  try {
    await page.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const auth = await authenticate(page, username, password);
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
