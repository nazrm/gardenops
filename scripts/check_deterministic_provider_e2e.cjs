#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("../frontend/node_modules/playwright-core");

const ROOT_DIR = path.resolve(__dirname, "..");
const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5184";
const CHROMIUM_EXECUTABLE = "/usr/bin/chromium";
const E2E_USERNAME = process.env.GARDENOPS_DETERMINISTIC_PROVIDER_E2E_USERNAME;
const E2E_PASSWORD = process.env.GARDENOPS_DETERMINISTIC_PROVIDER_E2E_PASSWORD;
const E2E_QUESTION = "What should I check before watering?";
const EXPECTED_REPLY = "Deterministic test reply: Check soil moisture before watering.";
const PROVIDER_FEATURE = "ai-garden-chat";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertNoResponseMocks() {
  const source = fs.readFileSync(__filename, "utf8");
  const fulfillNeedle = [".", "fulfill("].join("");
  assert(!source.includes(fulfillNeedle), "This E2E must not fulfill or mock product responses");
}

function loopbackHostname(hostname) {
  return ["127.0.0.1", "::1", "localhost"].includes(
    hostname.toLowerCase().replace(/^\[|\]$/g, ""),
  );
}

function isLoopbackNetworkUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false;
  }
  if (!["http:", "https:", "ws:", "wss:"].includes(parsed.protocol)) return true;
  return loopbackHostname(parsed.hostname);
}

function assertLoopbackBaseUrl() {
  const parsed = new URL(BASE_URL);
  assert(parsed.protocol === "http:", `BASE_URL must use http: ${BASE_URL}`);
  assert(loopbackHostname(parsed.hostname), `BASE_URL must be loopback: ${BASE_URL}`);
  assert(parsed.port, `BASE_URL must include the local frontend port: ${BASE_URL}`);
}

async function installLoopbackRequestGuard(context) {
  const blocked = [];
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    if (!isLoopbackNetworkUrl(url)) {
      blocked.push(url);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  await context.routeWebSocket(
    (url) => !isLoopbackNetworkUrl(url.href),
    (socket) => {
      blocked.push(socket.url());
      socket.close({ code: 1008, reason: "Non-loopback E2E traffic is blocked" });
    },
  );
  return blocked;
}

function dataSnapshot() {
  const result = spawnSync(
    path.join(ROOT_DIR, ".venv", "bin", "python"),
    ["scripts/seed_deterministic_provider_e2e.py", "snapshot"],
    {
      cwd: ROOT_DIR,
      encoding: "utf8",
      env: process.env,
    },
  );
  assert(
    result.status === 0,
    `Database snapshot failed:\n${result.stderr || result.stdout || result.error || "unknown error"}`,
  );
  try {
    return JSON.parse(result.stdout);
  } catch (error) {
    throw new Error(`Database snapshot was not JSON: ${error.message}\n${result.stdout}`);
  }
}

function assertSeededFixture(snapshot) {
  assert(snapshot.status === "seeded", `Fixture snapshot status was ${snapshot.status}`);
  assert(snapshot.garden_count === 1, `Expected one active garden, got ${snapshot.garden_count}`);
  assert(snapshot.planner_enabled === true, "Fixture admin must have the planner feature");
  assert(snapshot.garden?.membership_role === "admin", "Fixture garden membership must be admin");
  assert(snapshot.user?.username === E2E_USERNAME, "Fixture username does not match browser user");
}

function assertProviderUsage(snapshot, expectedRequestCount) {
  const rows = snapshot.provider_usage;
  assert(Array.isArray(rows), "Snapshot did not include provider usage rows");
  assert(rows.length === 2, `Expected one user and one garden budget row, got ${rows.length}`);
  const expectedScopes = {
    garden: snapshot.garden.id,
    user: snapshot.user.id,
  };
  for (const row of rows) {
    assert(row.feature === PROVIDER_FEATURE, `Unexpected provider feature ${row.feature}`);
    assert(
      row.request_count === expectedRequestCount,
      `Expected ${expectedRequestCount} ${row.scope_type} requests, got ${row.request_count}`,
    );
    assert(
      row.scope_id === expectedScopes[row.scope_type],
      `Provider usage ${row.scope_type} scope did not match the fixture`,
    );
    assert(/^\d{4}-\d{2}-\d{2}$/.test(row.usage_day), "Provider usage day was invalid");
  }
}

async function signIn(page) {
  const form = page.locator("#auth-gate-form");
  await form.waitFor({ state: "visible", timeout: 10000 });
  await form.locator('input[name="username"]').fill(E2E_USERNAME);
  await form.locator('button[type="submit"]').click();
  const password = form.locator('input[name="password"]');
  await password.waitFor({ state: "visible", timeout: 10000 });
  await password.fill(E2E_PASSWORD);
  const loginResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST" && url.pathname === "/api/auth/login";
  });
  await form.locator('button[type="submit"]').click();
  const response = await loginResponse;
  assert(response.status() === 200, `Login returned ${response.status()}`);
  await page.locator(".auth-gate").waitFor({ state: "detached", timeout: 10000 });
  await page.locator("[data-tab='insights']:visible").first().waitFor({ state: "visible", timeout: 10000 });
}

async function openInsightsAnalysis(page) {
  await page.locator("[data-tab='insights']:visible").first().click();
  const analysisTab = page.locator("[data-sub-mode='analysis']:visible").first();
  await analysisTab.click();
  await page.locator("#analysis-view").waitFor({ state: "visible", timeout: 10000 });
}

async function sendDeterministicChat(page) {
  await openInsightsAnalysis(page);
  const input = page.locator("#analysis-input");
  const send = page.locator("#analysis-send-btn");
  await input.fill(E2E_QUESTION);
  const chatResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST" && url.pathname === "/api/ai/garden-chat";
  });
  await send.click();
  const response = await chatResponse;
  assert(response.status() === 200, `Garden chat returned ${response.status()}`);
  const reply = page.locator(
    "#analysis-messages .chat-bubble.chat-ai:not(.chat-loading):not(.chat-error)",
  ).last();
  await reply.waitFor({ state: "visible", timeout: 10000 });
  assert(
    (await reply.textContent() || "").trim() === EXPECTED_REPLY,
    "Visible garden-chat reply was not the exact deterministic response",
  );
  assert(await input.isEnabled(), "Analysis input was not reenabled after the response");
  assert(await send.isEnabled(), "Analysis send control was not reenabled after the response");
}

async function main() {
  assertNoResponseMocks();
  assertLoopbackBaseUrl();
  assert(fs.existsSync(CHROMIUM_EXECUTABLE), `${CHROMIUM_EXECUTABLE} is required`);
  assert(E2E_USERNAME, "GARDENOPS_DETERMINISTIC_PROVIDER_E2E_USERNAME is required");
  assert(E2E_PASSWORD, "GARDENOPS_DETERMINISTIC_PROVIDER_E2E_PASSWORD is required");

  const before = dataSnapshot();
  assertSeededFixture(before);
  assert(before.provider_usage.length === 0, "Fixture must start with no provider usage");

  const browser = await chromium.launch({
    args: ["--no-sandbox"],
    executablePath: CHROMIUM_EXECUTABLE,
    headless: true,
  });
  const context = await browser.newContext({ viewport: { height: 900, width: 1440 } });
  const blockedRequests = await installLoopbackRequestGuard(context);
  const page = await context.newPage();
  const browserErrors = [];
  const nonLoopbackRequests = [];
  page.on("pageerror", (error) => browserErrors.push(error.stack || error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      browserErrors.push(message.text());
    }
  });
  page.on("request", (request) => {
    if (!isLoopbackNetworkUrl(request.url())) nonLoopbackRequests.push(request.url());
  });

  try {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await signIn(page);

    const profile = await page.evaluate(async () => {
      const response = await fetch("/api/auth/me");
      return { body: await response.json(), status: response.status };
    });
    assert(profile.status === 200, `/api/auth/me returned ${profile.status}`);
    assert(profile.body.garden_id === before.garden.id, "Signed-in user did not get the seeded garden");
    assert(
      Array.isArray(profile.body.allowed_features) && profile.body.allowed_features.includes("planner"),
      "Signed-in user did not receive the planner feature",
    );

    await sendDeterministicChat(page);
    await page.setViewportSize({ height: 844, width: 390 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#mobile-tab-insights").waitFor({ state: "visible", timeout: 10000 });
    await sendDeterministicChat(page);

    const after = dataSnapshot();
    assertSeededFixture(after);
    assertProviderUsage(after, 2);
    assert(
      nonLoopbackRequests.length === 0,
      `Browser made non-loopback requests:\n${nonLoopbackRequests.join("\n")}`,
    );
    assert(blockedRequests.length === 0, `Blocked non-loopback requests:\n${blockedRequests.join("\n")}`);
    assert(browserErrors.length === 0, `Browser errors:\n${browserErrors.join("\n")}`);
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
