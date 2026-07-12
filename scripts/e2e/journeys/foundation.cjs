"use strict";

const {
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

function fixtureGarden(fixture, key) {
  const garden = fixture.gardens?.[key];
  assert(garden && Number.isInteger(garden.id), `Missing ${key} fixture garden`);
  return garden;
}

async function assertActiveGardenHeading(page, profile, garden) {
  const selector = page.locator(profile === "mobile" ? "#mobile-garden-select" : "#garden-select");
  await visible(selector, `${profile} active garden control`);
  const selectedText = await selector.locator("option:checked").textContent();
  assert(
    (selectedText || "").trim().startsWith(`${garden.name} (`),
    `${profile} active garden control did not name ${garden.name}`,
  );
  if (profile === "mobile") {
    await visible(page.locator("#mobile-garden-name"), "mobile active garden heading");
    assert(
      (await page.locator("#mobile-garden-name").textContent() || "").trim() === garden.name,
      `Mobile active garden heading did not name ${garden.name}`,
    );
  }
}

async function waitForMapObject(page, objectId, label) {
  await visible(
    page.locator(`.map-object-label[data-object-id='${objectId}']`),
    `${label} map object`,
  );
}

async function openDesktopNotification(page, expected, rejected) {
  const trigger = page.locator("#notification-bell");
  await visible(trigger, "desktop notification trigger");
  if (await page.locator("#notification-panel").isVisible()) await trigger.click();
  await trigger.click();
  const panel = page.locator("#notification-panel");
  await visible(panel, "desktop notification panel");
  await visible(panel.getByText(expected, { exact: true }), `${expected} notification`);
  assert(
    await panel.getByText(rejected, { exact: true }).count() === 0,
    `Notification panel leaked ${rejected}`,
  );
  await trigger.click();
  await panel.waitFor({ state: "hidden" });
}

async function openMobileUtility(page) {
  const trigger = page.locator("#mobile-utility-btn");
  await visible(trigger, "mobile utility trigger");
  if (!await page.locator("body.mobile-utility-open").count()) {
    await trigger.click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
  }
}

async function openMobileNotification(page, expected, rejected) {
  await openMobileUtility(page);
  const trigger = page.locator("#mobile-notification-btn");
  await page.waitForFunction(() => (
    document.getElementById("mobile-notification-btn")
      ?.getAttribute("data-notification-trigger-bound") === "true"
  ));
  await visible(trigger, "mobile notification trigger");
  await trigger.click();
  const panel = page.locator("#notification-panel");
  await visible(panel, "mobile notification panel");
  await visible(panel.getByText(expected, { exact: true }), `${expected} mobile notification`);
  assert(
    await panel.getByText(rejected, { exact: true }).count() === 0,
    `Mobile notification panel leaked ${rejected}`,
  );
}

async function selectGarden(page, profile, garden) {
  if (profile === "mobile") await openMobileUtility(page);
  const selector = page.locator(profile === "mobile" ? "#mobile-garden-select" : "#garden-select");
  await visible(selector, `${profile} garden selector`);
  await selector.selectOption(String(garden.id));
  return selector;
}

async function runProfile({ artifactDir, baseUrl, browser, devices, fixture, password, profile, username }) {
  const alpha = fixtureGarden(fixture, "alpha");
  const beta = fixtureGarden(fixture, "beta");
  const guarded = await createGuardedContext(browser, devices, profile, artifactDir);
  const page = await guarded.context.newPage();
  const recorder = createApiRecorder(page);
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile,
    requests: [],
    role: "admin",
    trace: null,
  };
  let status = "failed";
  let caughtError = null;
  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const profileData = await authenticate(page, username, password);
    guarded.markAuthenticated();
    assert(profileData.role === "admin", "Foundation fixture user is not an administrator");
    result.browser_profile.user_agent = await page.evaluate(() => navigator.userAgent);
    result.browser_profile.max_touch_points = await page.evaluate(() => navigator.maxTouchPoints);
    result.browser_profile.viewport = page.viewportSize();
    await visible(page.locator("#map-grid"), `${profile} Map grid`);
    await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
    await assertActiveGardenHeading(page, profile, alpha);
    assert(
      !recorder.records.some((request) => request.method === "GET" && request.path === "/api/plants"),
      `${profile} Map-first startup fetched /api/plants before plant-dependent work`,
    );
    result.checks.auth_session = true;
    result.checks.map_first_without_plants = true;
    result.assertions.passed.push("authenticated-session", "map-first-without-plants");

    if (profile === "mobile") {
      await openMobileNotification(page, alpha.notification_title, beta.notification_title);
    } else {
      await openDesktopNotification(page, alpha.notification_title, beta.notification_title);
    }
    const selector = await selectGarden(page, profile, beta);
    assert(
      !await page.locator(`.map-object-label[data-object-id='${alpha.object_public_id}']`).isVisible().catch(() => false),
      `${profile} retained Alpha map object immediately after switching to Beta`,
    );
    await waitForMapObject(page, beta.object_public_id, beta.object_label);
    await assertActiveGardenHeading(page, profile, beta);
    if (profile === "mobile") {
      assert(await page.locator("#notification-panel").isHidden(), "Garden switch did not close mobile notifications");
      await openMobileNotification(page, beta.notification_title, alpha.notification_title);
    } else {
      await openDesktopNotification(page, beta.notification_title, alpha.notification_title);
    }

    await selectGarden(page, profile, alpha);
    await waitForMapObject(page, alpha.object_public_id, alpha.object_label);
    await assertActiveGardenHeading(page, profile, alpha);
    assert(await selector.inputValue() === String(alpha.id), `${profile} A/B/A did not finish on Alpha`);
    assert(
      !await page.locator(`.map-object-label[data-object-id='${beta.object_public_id}']`).isVisible().catch(() => false),
      `${profile} rendered stale Beta map state after returning to Alpha`,
    );
    if (profile === "mobile") {
      if (await page.locator("#notification-panel").isVisible()) {
        await page.locator("#mobile-utility-btn").click();
      }
      await openMobileNotification(page, alpha.notification_title, beta.notification_title);
    } else {
      await openDesktopNotification(page, alpha.notification_title, beta.notification_title);
    }
    result.checks.garden_a_b_a = true;
    result.checks.garden_scoped_notifications = true;
    result.assertions.passed.push("ordinary-state-garden-a-b-a", "ordinary-state-garden-scoped-notifications");
    result.assertions.passed.push("active-garden-heading-scoped");
    result.structure = await assertPageStructure(page, `${profile} foundation state`, {
      enforceControlNames: false,
    });
    result.assertions.passed.push(
      "no-duplicate-ids",
      "no-horizontal-overflow",
    );
    result.assertions.skipped.push(
      "full-accessible-name-audit-deferred-to-phase-8",
    );
    assertDiagnosticsClean(guarded.diagnostics, `${profile} foundation journey`);
    result.checks.browser_diagnostics = true;
    result.assertions.passed.push("browser-diagnostics-clean", "outbound-network-clean");
    result.requests = recorder.records;
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    result.diagnostics = guarded.diagnostics;
    try {
      result.trace = await guarded.close(status);
    } catch (error) {
      const closeMessage = "browser profile cleanup failed";
      result.failure = closeMessage;
      result.assertions.failed.push(closeMessage);
      if (!caughtError) caughtError = error;
    }
  }
  return { error: caughtError, result };
}

async function runFoundation(options, profileRunner = runProfile) {
  const results = [];
  for (const profile of ["desktop", "mobile"]) {
    const outcome = await profileRunner({ ...options, profile });
    results.push(outcome.result);
    if (options.onProfile) options.onProfile(outcome.result);
    if (outcome.error) throw outcome.error;
  }
  await waitFor(
    () => results.every((result) => result.checks.garden_a_b_a),
    "foundation profile completion",
  );
  return results;
}

module.exports = { runFoundation };
