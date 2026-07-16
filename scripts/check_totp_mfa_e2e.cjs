#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const path = require("node:path");

const ROOT_DIR = path.resolve(__dirname, "..");
const { chromium } = require(path.join(
  ROOT_DIR,
  "frontend",
  "node_modules",
  "playwright-core",
));

const BASE_URL = process.env.BASE_URL || "";
const E2E_USERNAME = process.env.GARDENOPS_TOTP_MFA_E2E_USERNAME || "";
const E2E_PASSWORD = process.env.GARDENOPS_TOTP_MFA_E2E_PASSWORD || ""; // push-sanitizer: allow SECRET_ASSIGNMENT - runtime-only disposable fixture
const CHROMIUM_EXECUTABLE = "/usr/bin/chromium";
const TIMEOUT_MS = 30_000;
let journeyStage = "startup";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function requireEnvironment() {
  assert(process.env.APP_ENV === "test", "TOTP MFA E2E requires APP_ENV=test");
  assert(process.env.AUTH_REQUIRED === "true", "TOTP MFA E2E requires session authentication");
  assert(process.env.AUTH_MODE === "session", "TOTP MFA E2E requires AUTH_MODE=session");
  assert(process.env.AUTH_ADMIN_MFA_REQUIRED === "true", "TOTP MFA E2E requires admin MFA");
  assert(process.env.AUTH_MFA_TOTP_PERIOD_SECONDS === "30", "TOTP MFA E2E requires 30-second TOTP windows");
  assert(process.env.AUTH_MFA_TOTP_DIGITS === "6", "TOTP MFA E2E requires six-digit TOTP codes");
  assert(E2E_USERNAME.length > 0 && E2E_PASSWORD.length > 0, "TOTP MFA E2E fixture is unavailable");
  const parsed = new URL(BASE_URL);
  assert(parsed.protocol === "http:", "TOTP MFA E2E must use local HTTP");
  assert(parsed.hostname === "127.0.0.1", "TOTP MFA E2E must use loopback");
  assert(parsed.port && parsed.port !== "5432", "TOTP MFA E2E needs its own local port");
  assert(parsed.pathname === "/", "TOTP MFA E2E base URL must not contain a path");
}

function isAllowedBrowserUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false;
  }
  if (!["http:", "https:", "ws:", "wss:"].includes(parsed.protocol)) return true;
  return ["127.0.0.1", "::1", "localhost"].includes(
    parsed.hostname.toLowerCase().replace(/^\[|\]$/g, ""),
  );
}

async function installLoopbackRequestGuard(context) {
  const blocked = [];
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    if (!isAllowedBrowserUrl(url)) {
      blocked.push(url);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  await context.routeWebSocket(
    (url) => !isAllowedBrowserUrl(url.href),
    (socket) => {
      blocked.push(socket.url());
      socket.close({ code: 1008, reason: "Non-loopback E2E traffic is blocked" });
    },
  );
  return blocked;
}

function decodeBase32(secret) {
  const normalized = String(secret).toUpperCase().replace(/[\s=]/g, "");
  assert(/^[A-Z2-7]+$/.test(normalized), "TOTP enrollment did not provide a Base32 secret");
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let value = 0;
  const bytes = [];
  for (const character of normalized) {
    value = (value << 5) | alphabet.indexOf(character);
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

function totpAt(secret, counter) {
  const counterBytes = Buffer.alloc(8);
  counterBytes.writeBigUInt64BE(BigInt(counter));
  const digest = crypto.createHmac("sha1", decodeBase32(secret)).update(counterBytes).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary = (
    ((digest[offset] & 0x7f) << 24)
    | (digest[offset + 1] << 16)
    | (digest[offset + 2] << 8)
    | digest[offset + 3]
  );
  return String(binary % 1_000_000).padStart(6, "0");
}

function currentTotp(secret) {
  const counter = Math.floor(Date.now() / 30_000);
  return { code: totpAt(secret, counter), counter };
}

function invalidTotp(secret) {
  const counter = Math.floor(Date.now() / 30_000);
  const nearbyCodes = new Set();
  for (let offset = -2; offset <= 2; offset += 1) {
    nearbyCodes.add(totpAt(secret, counter + offset));
  }
  for (const candidate of ["000000", "111111", "222222", "333333", "444444"]) {
    if (!nearbyCodes.has(candidate)) return candidate;
  }
  throw new Error("Could not construct a deliberately invalid TOTP code");
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function freshTotpAfter(secret, acceptedCounter) {
  const deadline = Date.now() + 65_000;
  while (Date.now() < deadline) {
    const candidate = currentTotp(secret);
    if (candidate.counter > acceptedCounter) return candidate;
    const untilNextWindow = 30_000 - (Date.now() % 30_000) + 50;
    await sleep(Math.min(1_000, untilNextWindow));
  }
  throw new Error("Timed out waiting for a fresh TOTP window");
}

async function visible(locator, label, timeout = TIMEOUT_MS) {
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch {
    throw new Error(`Expected ${label}`);
  }
}

async function loginPasswordStage(page) {
  const form = page.locator("#auth-gate-form");
  await visible(form, "the sign-in form");
  await form.locator('input[name="username"]').fill(E2E_USERNAME);
  await form.locator('button[type="submit"]').click();
  await visible(form.locator('input[name="password"]'), "the password sign-in stage");
  await form.locator('input[name="password"]').fill(E2E_PASSWORD);
  await form.locator('button[type="submit"]').click();
  return form;
}

async function completePrompt(page, value) {
  const dialog = page.locator(".modal").last();
  await visible(dialog, "a sensitive-action prompt");
  const dialogHandle = await dialog.elementHandle();
  assert(dialogHandle, "Sensitive-action prompt disappeared unexpectedly");
  await dialog.locator(".prompt-dialog-input").fill(value);
  await dialog.locator(".confirm-yes").click();
  await page.waitForFunction((element) => !element.isConnected, dialogHandle, { timeout: TIMEOUT_MS });
  await dialogHandle.dispose();
}

async function acceptSensitiveConfirmation(page) {
  const dialog = page.locator(".modal").last();
  await visible(dialog, "a sensitive-action confirmation");
  const dialogHandle = await dialog.elementHandle();
  assert(dialogHandle, "Sensitive-action confirmation disappeared unexpectedly");
  await dialog.locator(".confirm-yes").click();
  await page.waitForFunction((element) => !element.isConnected, dialogHandle, { timeout: TIMEOUT_MS });
  await dialogHandle.dispose();
}

async function recoveryDisplaySummary(page) {
  const output = page.locator("#adm-mfa-recovery-output");
  await visible(output, "displayed recovery codes");
  return output.evaluate((element) => {
    const value = element instanceof HTMLTextAreaElement ? element.value : "";
    return {
      displayed: value.trim().length > 0,
      count: value.split(/\r?\n/).filter((line) => line.trim().length > 0).length,
    };
  });
}

async function openMfaSettings(page, controlId) {
  const control = page.locator(`#${controlId}`);
  if (!(await control.isVisible().catch(() => false))) {
    const settings = page.locator('.adm-nav-btn[data-section="settings"]');
    await visible(settings, "the forced admin settings navigation");
    await settings.click();
  }
  await visible(control, "the MFA settings surface");
  return control;
}

async function cancelSensitiveAction(page, regenerate) {
  await regenerate.click();
  const dialog = page.locator(".modal").last();
  await visible(dialog, "the action-reason prompt");
  const dialogHandle = await dialog.elementHandle();
  assert(dialogHandle, "Action-reason prompt disappeared unexpectedly");
  await dialog.locator(".confirm-no").click();
  await page.waitForFunction((element) => !element.isConnected, dialogHandle, { timeout: TIMEOUT_MS });
  await dialogHandle.dispose();
  assert(await page.locator("#adm-mfa-recovery-codes").count() === 0, "Cancellation changed recovery-code state");
}

async function denyWithWrongTotp(page, regenerate, secret) {
  const recoveryDisplayCount = await page.locator("#adm-mfa-recovery-codes").count();
  await regenerate.click();
  await acceptSensitiveConfirmation(page);
  await completePrompt(page, "totp-mfa-e2e-denial");
  await completePrompt(page, E2E_PASSWORD);
  const deniedResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/auth/reauthenticate"
  ));
  await completePrompt(page, invalidTotp(secret));
  const response = await deniedResponse;
  assert(response.status() === 401, `Invalid TOTP reauthentication returned ${response.status()}`);
  await page.waitForTimeout(750);
  assert(
    await page.locator("#adm-mfa-recovery-codes").count() === recoveryDisplayCount,
    "Rejected MFA step-up changed recovery codes",
  );
  assert(await regenerate.isEnabled(), "Rejected MFA step-up disabled the retry control");
}

async function regenerateWithFreshTotp(page, regenerate, secret, acceptedCounter) {
  const fresh = await freshTotpAfter(secret, acceptedCounter);
  await regenerate.click();
  await acceptSensitiveConfirmation(page);
  await completePrompt(page, "totp-mfa-e2e-regeneration");
  await completePrompt(page, E2E_PASSWORD);
  await completePrompt(page, fresh.code);
  const recovery = await recoveryDisplaySummary(page);
  assert(recovery.displayed && recovery.count >= 5, "Successful step-up did not display recovery codes");
}

async function runJourney() {
  journeyStage = "environment validation";
  requireEnvironment();
  let browser;
  try {
    journeyStage = "browser launch";
    browser = await chromium.launch({
      executablePath: CHROMIUM_EXECUTABLE,
      headless: true,
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
    const context = await browser.newContext();
    const blockedRequests = await installLoopbackRequestGuard(context);
    const page = await context.newPage();
    const browserErrors = [];
    page.on("pageerror", (error) => browserErrors.push(error.stack || error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
        browserErrors.push(message.text());
      }
    });
    page.setDefaultTimeout(TIMEOUT_MS);

    journeyStage = "initial page load";
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    journeyStage = "initial password sign-in";
    const initialForm = await loginPasswordStage(page);
    await initialForm.waitFor({ state: "detached", timeout: TIMEOUT_MS });

    journeyStage = "forced MFA setup surface";
    await visible(page.locator("#admin-view"), "the forced admin MFA surface");
    const start = await openMfaSettings(page, "adm-mfa-start");
    assert(await page.locator("#adm-mfa-regenerate").isDisabled(), "MFA was unexpectedly enabled before setup");
    journeyStage = "TOTP enrollment";
    await start.click();
    const secretInput = page.locator("#adm-mfa-secret");
    await visible(secretInput, "the TOTP enrollment secret");
    const secret = await secretInput.inputValue();
    assert(/^[A-Z2-7]{16,}$/.test(secret), "TOTP enrollment secret was invalid");
    const enrollmentTotp = currentTotp(secret);
    await page.locator("#adm-mfa-code").fill(enrollmentTotp.code);
    await page.locator("#adm-mfa-confirm").click();
    const enrollmentRecovery = await recoveryDisplaySummary(page);
    assert(
      enrollmentRecovery.displayed && enrollmentRecovery.count >= 5,
      "Enrollment did not display recovery codes",
    );

    journeyStage = "mobile MFA transition";
    await page.setViewportSize({ height: 844, width: 390 });
    await visible(page.locator("#mobile-tab-map"), "the mobile navigation after enrollment");

    journeyStage = "MFA logout";
    await page.locator("#mobile-utility-btn").click();
    await page.waitForFunction(() => document.body.classList.contains("mobile-utility-open"));
    await visible(page.locator("#mobile-auth-btn"), "the mobile sign-out action");
    await page.locator("#mobile-auth-btn").click();
    journeyStage = "TOTP sign-in";
    const mfaForm = await loginPasswordStage(page);
    await visible(mfaForm.locator('input[name="mfa_code"]'), "the TOTP sign-in stage");
    const loginTotp = currentTotp(secret);
    await mfaForm.locator('input[name="mfa_code"]').fill(loginTotp.code);
    await mfaForm.locator('button[type="submit"]').click();
    await mfaForm.waitFor({ state: "detached", timeout: TIMEOUT_MS });

    journeyStage = "sensitive-action settings";
    const regenerate = await openMfaSettings(page, "adm-mfa-regenerate");
    journeyStage = "sensitive-action cancellation";
    await cancelSensitiveAction(page, regenerate);
    journeyStage = "invalid TOTP step-up";
    await denyWithWrongTotp(page, regenerate, secret);
    journeyStage = "fresh TOTP step-up";
    await regenerateWithFreshTotp(page, regenerate, secret, loginTotp.counter);
    assert(browserErrors.length === 0, `TOTP MFA browser errors: ${browserErrors.join(" | ")}`);
    assert(
      blockedRequests.length === 0,
      `TOTP MFA blocked non-loopback requests: ${blockedRequests.join(" | ")}`,
    );

    await context.close();
  } finally {
    if (browser) await browser.close();
  }
}

runJourney().catch((error) => {
  console.error(`TOTP MFA E2E browser journey failed at ${journeyStage}`);
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
