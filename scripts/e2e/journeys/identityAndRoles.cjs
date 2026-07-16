"use strict";

const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  assertDiagnosticsClean,
  authenticate,
  createApiRecorder,
  createGuardedContext,
} = require("../completeJourneyBrowser.cjs");
const { assert, assertPageStructure, visible, waitFor } = require("../completeJourneyAssertions.cjs");

const EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const INVITEE_PASSWORD = "CompleteJourneysPhaseFiveInvitee!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const VIEWER_INVITEE_PASSWORD = "CompleteJourneysPhaseFiveViewer!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture

function phaseFive(fixture) {
  const value = fixture.phase_five;
  assert(value && typeof value === "object", "Phase 5 fixture is missing");
  return value;
}

async function waitForAdminIdle(page, label) {
  await waitFor(
    () => page.locator("#admin-view").getAttribute("aria-busy")
      .then((value) => value !== "true"),
    label,
  );
}

async function openAdminSection(page, profile, section) {
  if (profile === "mobile") {
    const utility = page.locator("#mobile-utility-btn:visible");
    if (await utility.count() && await page.locator("body.mobile-utility-open").count() === 0) {
      await utility.click();
    }
    await visible(page.locator("#mobile-admin-btn:visible"), "mobile settings button");
    await page.locator("#mobile-admin-btn:visible").click();
  } else {
    await visible(page.locator("#top-tab-admin:visible"), "settings tab");
    await page.locator("#top-tab-admin:visible").click();
  }
  const nav = page.locator(`.adm-nav-btn[data-section='${section}']`);
  await visible(nav, `${section} settings navigation`);
  await nav.click();
  await waitForAdminIdle(page, `${section} settings load`);
}

async function answerPrompt(page, value) {
  const dialog = page.locator(".modal:visible").last();
  await visible(dialog, "identity prompt");
  const input = dialog.locator(".prompt-dialog-input");
  await visible(input, "identity prompt input");
  if (value !== undefined) await input.fill(value);
  await dialog.locator(".confirm-yes").click();
}

async function confirmVisibleDialog(page) {
  const dialog = page.locator("[role='alertdialog']:visible").last();
  await visible(dialog, "identity confirmation");
  await dialog.locator(".confirm-yes").click();
}

async function waitForProactivePasskeyPrompt(page, timeout = 5_000) {
  const dialog = page.locator(".modal[data-passkey-prompt-ready='true']:visible").filter({
    has: page.locator(".passkey-prompt-modal"),
  }).last();
  try {
    await dialog.waitFor({ state: "visible", timeout });
    return dialog;
  } catch {
    return null;
  }
}

async function completeInvitationOnboarding(page, expectedRole) {
  if (expectedRole !== "editor") return;
  const overlay = page.locator(".onboarding-overlay");
  await visible(overlay, "invited editor onboarding");
  await overlay.locator(".onb-next").click();
  await overlay.locator("#onb-garden-name").fill("Phase 5 invited editor garden");
  await overlay.locator(".onb-next").click();
  await overlay.locator("#onb-cols").fill("12");
  await overlay.locator("#onb-rows").fill("12");
  await overlay.locator(".onb-next").click();
  await overlay.locator("#onb-house-row").fill("2");
  await overlay.locator("#onb-house-col").fill("2");
  await overlay.locator("#onb-house-w").fill("3");
  await overlay.locator("#onb-house-h").fill("3");
  await overlay.locator(".onb-next").click();
  await overlay.locator("#onb-address").fill("Phase 5 invitation onboarding address");
  await overlay.locator("#onb-lat").fill("59.91");
  await overlay.locator("#onb-lon").fill("10.75");
  await overlay.locator(".onb-next").click();
  await overlay.locator(".onb-next").click();
  await overlay.locator(".onb-finish").click();
  await overlay.waitFor({ state: "detached", timeout: 20_000 });
}

async function dismissProactivePasskeyPrompt(page) {
  const dialog = await waitForProactivePasskeyPrompt(page);
  if (!dialog) return;
  const dismissButton = dialog.locator(".confirm-no");
  await visible(dismissButton, "proactive passkey dismissal");
  const [dismissed] = await Promise.all([
    responseFor(page, "POST", "/api/auth/passkeys/prompt/dismiss"),
    dismissButton.click({ timeout: 5_000 }),
  ]);
  assert(dismissed.ok(), "Passkey prompt dismissal failed");
  await dialog.waitFor({ state: "detached" });
}

function responseFor(page, method, pathname) {
  return page.waitForResponse((response) => (
    response.request().method() === method
    && new URL(response.url()).pathname === pathname
  ));
}

async function browserFetch(page, request) {
  return page.evaluate(async (input) => {
    const cookieCsrf = document.cookie.split("; ")
      .find((entry) => entry.startsWith("gardenops_csrf="))?.split("=").slice(1).join("=") || "";
    const csrf = input.csrfToken === undefined ? decodeURIComponent(cookieCsrf) : input.csrfToken; // push-sanitizer: allow SECRET_ASSIGNMENT - disposable browser cookie or explicit denial canary
    const response = await fetch(input.path, {
      body: input.body === undefined ? undefined : JSON.stringify(input.body),
      credentials: "include",
      headers: {
        ...(input.body === undefined ? {} : { "content-type": "application/json" }),
        "x-csrf-token": csrf,
      },
      method: input.method || "GET",
    });
    const text = await response.text();
    let body = text;
    try { body = text ? JSON.parse(text) : null; } catch { /* text response */ }
    return { body, status: response.status };
  }, request);
}

async function discardExpectedUiFailure(
  page,
  diagnostics,
  marks,
  method,
  pathname,
  expectedStatus,
) {
  await waitFor(() => diagnostics.httpErrors.length === marks.http + 1,
    `HTTP diagnostic for ${pathname}`);
  const httpAdded = diagnostics.httpErrors.splice(marks.http);
  assert(httpAdded.length === 1 && httpAdded[0] === `${expectedStatus} ${pathname}`,
    `Unexpected browser HTTP diagnostics for ${pathname}`);
  await waitFor(() => diagnostics.consoleErrors.length === marks.console + 1,
    `console diagnostic for ${pathname}`);
  const consoleAdded = diagnostics.consoleErrors.splice(marks.console);
  const classifiedAdded = diagnostics.classifiedConsoleDiagnostics.splice(marks.classified);
  assert(consoleAdded.length === 1 && classifiedAdded.length === 1
    && classifiedAdded[0].method === method
    && classifiedAdded[0].path === pathname
    && classifiedAdded[0].status === expectedStatus,
  `Unexpected browser console diagnostics for ${pathname}`);
}

function diagnosticMarks(diagnostics) {
  return {
    classified: diagnostics.classifiedConsoleDiagnostics.length,
    console: diagnostics.consoleErrors.length,
    http: diagnostics.httpErrors.length,
  };
}

async function expectedHttpFailure(page, diagnostics, request, expectedStatus) {
  const marks = diagnosticMarks(diagnostics);
  const result = await browserFetch(page, request);
  assert(result.status === expectedStatus,
    `${request.method || "GET"} ${request.path} returned ${result.status}`);
  await discardExpectedUiFailure(
    page,
    diagnostics,
    marks,
    request.method || "GET",
    request.path,
    expectedStatus,
  );
  return result;
}

async function enableVirtualAuthenticator(context, page) {
  const client = await context.newCDPSession(page);
  await client.send("WebAuthn.enable");
  const result = await client.send("WebAuthn.addVirtualAuthenticator", {
    options: {
      automaticPresenceSimulation: true,
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      protocol: "ctap2",
      transport: "usb",
    },
  });
  assert(result.authenticatorId, "Virtual WebAuthn authenticator was not created");
  await client.send("WebAuthn.setAutomaticPresenceSimulation", {
    authenticatorId: result.authenticatorId,
    enabled: true,
  });
  await client.send("WebAuthn.setUserVerified", {
    authenticatorId: result.authenticatorId,
    isUserVerified: true,
  });
  return {
    authenticatorId: result.authenticatorId,
    client,
    async setUserVerified(isUserVerified) {
      await client.send("WebAuthn.setUserVerified", {
        authenticatorId: result.authenticatorId,
        isUserVerified,
      });
    },
  };
}

async function signOut(page, label) {
  const control = page.locator("#auth-btn:visible, #mobile-auth-btn:visible").first();
  await visible(control, `${label} sign-out control`);
  const pending = responseFor(page, "POST", "/api/auth/logout");
  await control.click();
  assert((await pending).ok(), `${label} logout failed`);
  await visible(page.locator("#auth-gate-form"), `${label} sign-in gate`);
}

async function enterPasswordLoginStage(page, username, password) {
  const form = page.locator("#auth-gate-form");
  await visible(form, "password sign-in form");
  const usernameInput = form.locator("input[name='username']");
  if (await usernameInput.isVisible()) {
    await usernameInput.fill(username);
    await form.locator("button[type='submit']").click();
  }
  const passwordInput = form.locator("input[name='password']");
  const passwordFallback = form.locator("#auth-gate-use-password");
  await visible(
    form.locator("input[name='password']:visible, #auth-gate-use-password:visible").first(),
    "password fallback",
  );
  if (!(await passwordInput.isVisible())) await passwordFallback.click();
  await visible(passwordInput, "password field");
  await passwordInput.fill(password);
  const pending = responseFor(page, "POST", "/api/auth/login");
  await form.locator("button[type='submit']").click();
  assert((await pending).ok(), "Password stage failed");
  await visible(form.locator("input[name='mfa_code']"), "MFA sign-in stage");
  return form;
}

function decodeBase32(value) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const character of String(value).replace(/=+$/g, "").toUpperCase()) {
    const index = alphabet.indexOf(character);
    assert(index >= 0, "TOTP secret contains invalid base32 data");
    bits += index.toString(2).padStart(5, "0");
  }
  const bytes = [];
  for (let index = 0; index + 8 <= bits.length; index += 8) {
    bytes.push(Number.parseInt(bits.slice(index, index + 8), 2));
  }
  return Buffer.from(bytes);
}

function currentTotp(secret, nowMs = Date.now()) {
  const counter = Math.floor(nowMs / 30_000);
  const buffer = Buffer.alloc(8);
  buffer.writeBigUInt64BE(BigInt(counter));
  const digest = crypto.createHmac("sha1", decodeBase32(secret)).update(buffer).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary = ((digest[offset] & 0x7f) << 24)
    | ((digest[offset + 1] & 0xff) << 16)
    | ((digest[offset + 2] & 0xff) << 8)
    | (digest[offset + 3] & 0xff);
  return String(binary % 1_000_000).padStart(6, "0");
}

async function freshTotpAfter(secret, previousCode) {
  const deadline = Date.now() + 35_000;
  while (Date.now() < deadline) {
    const candidate = currentTotp(secret);
    if (candidate !== previousCode) return candidate;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Timed out waiting for a fresh TOTP code");
}

async function createUserInvitation(page, fixture) {
  await openAdminSection(page, "desktop", "users");
  await page.locator("#adm-user-inv-username").fill(phaseFive(fixture).invitee_username);
  await page.locator("#adm-user-inv-role").selectOption("editor");
  await page.locator("#adm-user-inv-ttl").fill("60");
  const pending = responseFor(page, "POST", "/api/auth/user-invitations");
  await page.locator("#adm-create-user-inv-form button[type='submit']").click();
  await answerPrompt(page, "phase-five-editor-invitation");
  assert((await pending).status() === 201, "Editor invitation creation failed");
  await visible(page.locator("#adm-user-inv-link-input"), "editor invitation link");
  const link = await page.locator("#adm-user-inv-link-input").inputValue();
  assert(link.includes("#invite="), "Editor invitation link is malformed");
  return link;
}

async function createGardenInvitation(page, fixture) {
  await openAdminSection(page, "desktop", "invitations");
  await page.locator("#adm-inv-username").fill(phaseFive(fixture).viewer_invitee_username);
  await page.locator("#adm-inv-ttl").fill("60");
  const pathName = `/api/gardens/${fixture.gardens.alpha.id}/invitations`;
  const pending = responseFor(page, "POST", pathName);
  await page.locator("#adm-create-inv-form button[type='submit']").click();
  await answerPrompt(page, "phase-five-viewer-invitation");
  assert((await pending).status() === 201, "Viewer invitation creation failed");
  await visible(page.locator("#adm-inv-link-input"), "viewer invitation link");
  const link = await page.locator("#adm-inv-link-input").inputValue();
  assert(link.includes("#invite="), "Viewer invitation link is malformed");
  return link;
}

async function exerciseInvalidInvitation(page, diagnostics) {
  const beforeUsers = await browserFetch(page, { path: "/api/auth/users" });
  const beforeInvitations = await browserFetch(page, { path: "/api/auth/user-invitations" });
  assert(beforeUsers.status === 200 && beforeInvitations.status === 200,
    "Invalid invitation baseline could not be loaded");
  await expectedHttpFailure(page, diagnostics, {
    body: {
      password: INVITEE_PASSWORD,
      token: "phase-five-malformed-invitation-token",
    },
    method: "POST",
    path: "/api/auth/invitations/accept",
  }, 400);
  const afterUsers = await browserFetch(page, { path: "/api/auth/users" });
  const afterInvitations = await browserFetch(page, { path: "/api/auth/user-invitations" });
  assert(afterUsers.body.users.length === beforeUsers.body.users.length
    && afterInvitations.body.invitations.length === beforeInvitations.body.invitations.length,
  "Invalid invitation attempt changed account or invitation counts");
  return 0;
}

async function exerciseSettings(page, fixture) {
  await openAdminSection(page, "desktop", "settings");
  await page.locator("#adm-plot-meaning-add").click();
  const row = page.locator(".adm-plot-meaning-row").last();
  await row.locator(".adm-plot-meaning-pattern").fill(phaseFive(fixture).settings_pattern);
  await row.locator(".adm-plot-meaning-label").fill(phaseFive(fixture).settings_label);
  await row.locator(".adm-plot-meaning-description").fill(
    phaseFive(fixture).settings_description,
  );
  const pendingRefreshRequests = new Set();
  let appRefreshStarted = false;
  let lastRefreshActivityAt = Date.now();
  const captureRefreshRequest = (request) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== "GET" || !pathname.startsWith("/api/")) return;
    pendingRefreshRequests.add(request);
    lastRefreshActivityAt = Date.now();
    if (pathname === "/api/gardens") appRefreshStarted = true;
  };
  const settleRefreshRequest = (request) => {
    if (!pendingRefreshRequests.delete(request)) return;
    lastRefreshActivityAt = Date.now();
  };
  page.on("request", captureRefreshRequest);
  page.on("requestfinished", settleRefreshRequest);
  page.on("requestfailed", settleRefreshRequest);
  const pending = responseFor(page, "PUT", "/api/auth/me/settings");
  try {
    await page.locator("#adm-plot-meaning-save").click();
    assert((await pending).ok(), "Identity settings save failed");
    await visible(
      page.locator(".toast-success").filter({ hasText: "Custom plot meanings saved" }).last(),
      "settled identity settings save",
    );
    await waitFor(() => appRefreshStarted, "post-save app refresh");
    await waitFor(
      () => pendingRefreshRequests.size === 0 && Date.now() - lastRefreshActivityAt >= 500,
      "settled post-save app refresh",
    );
  } finally {
    page.off("request", captureRefreshRequest);
    page.off("requestfinished", settleRefreshRequest);
    page.off("requestfailed", settleRefreshRequest);
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  await openAdminSection(page, "desktop", "settings");
  const persisted = page.locator(".adm-plot-meaning-row").filter({
    has: page.locator(`input[value='${phaseFive(fixture).settings_pattern.toUpperCase()}']`),
  });
  await visible(persisted.first(), "persisted identity setting");
}

async function exercisePasskeys(page, fixture, adminPassword, virtualAuthenticator, diagnostics) {
  const proactivePrompt = await waitForProactivePasskeyPrompt(page);
  const registerPending = responseFor(page, "POST", "/api/auth/passkeys/register/verify");
  if (proactivePrompt) {
    await proactivePrompt.locator(".confirm-yes").click();
    await answerPrompt(page, adminPassword);
  } else {
    await openAdminSection(page, "desktop", "settings");
    await page.locator("#adm-passkey-add").click();
    await answerPrompt(page, phaseFive(fixture).passkey_nickname);
    await answerPrompt(page, adminPassword);
  }
  assert((await registerPending).status() === 201, "Passkey registration failed");
  await openAdminSection(page, "desktop", "settings");
  let row = page.locator("[data-passkey-id]").first();
  await visible(row, "registered passkey");

  const renamePending = page.waitForResponse((response) => (
    response.request().method() === "PATCH"
    && /^\/api\/auth\/passkeys\/\d+$/.test(new URL(response.url()).pathname)
  ));
  await row.locator(".adm-passkey-rename").click();
  await answerPrompt(page, phaseFive(fixture).passkey_renamed_nickname);
  await answerPrompt(page, "phase-five-passkey-rename");
  assert((await renamePending).ok(), "Passkey rename failed");
  row = page.locator("[data-passkey-id]").filter({
    hasText: phaseFive(fixture).passkey_renamed_nickname,
  }).first();
  await visible(row, "renamed passkey");

  await signOut(page, "passkey test");
  const gate = page.locator("#auth-gate-form");
  await gate.locator("input[name='username']").fill(fixture.roles.admin);
  await gate.locator("button[type='submit']").click();
  let passkeyAction = gate.locator("button[type='submit']").filter({ hasText: "Use passkey" });
  await visible(passkeyAction, "explicit passwordless passkey action");

  await virtualAuthenticator.setUserVerified(false);
  const rejectedMarks = diagnosticMarks(diagnostics);
  let rejectedResponse = null;
  const captureRejected = (response) => {
    if (response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/auth/passkeys/login/verify") {
      rejectedResponse = response;
    }
  };
  page.on("response", captureRejected);
  await passkeyAction.click();
  await page.waitForTimeout(500);
  page.off("response", captureRejected);
  if (rejectedResponse) {
    assert(rejectedResponse.status() === 400 || rejectedResponse.status() === 401,
      `Rejected user verification returned ${rejectedResponse.status()}`);
    await discardExpectedUiFailure(
      page,
      diagnostics,
      rejectedMarks,
      "POST",
      "/api/auth/passkeys/login/verify",
      rejectedResponse.status(),
    );
  }
  assert(await gate.isVisible(), "Rejected user verification left the sign-in gate");
  const retryMarks = diagnosticMarks(diagnostics);
  await page.reload({ waitUntil: "domcontentloaded" });
  await visible(gate, "user-verification retry gate");
  await discardExpectedUiFailure(
    page,
    diagnostics,
    retryMarks,
    "GET",
    "/api/auth/me",
    401,
  );
  await gate.locator("input[name='username']").fill(fixture.roles.admin);
  await gate.locator("button[type='submit']").click();
  passkeyAction = gate.locator("button[type='submit']").filter({ hasText: "Use passkey" });
  await visible(passkeyAction, "user-verification retry action");

  await virtualAuthenticator.setUserVerified(true);
  const loginPending = responseFor(page, "POST", "/api/auth/passkeys/login/verify");
  await passkeyAction.click();
  assert((await loginPending).ok(), "Passwordless passkey sign-in failed");
  await waitFor(() => page.locator(".auth-gate").count().then((count) => count === 0),
    "passwordless sign-in completion");
}

async function revokePasskey(page, fixture, diagnostics, adminPassword) {
  await openAdminSection(page, "desktop", "settings");
  const row = page.locator("[data-passkey-id]").filter({
    hasText: phaseFive(fixture).passkey_renamed_nickname,
  }).first();
  await visible(row, "passkey awaiting revocation");
  const pending = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && /^\/api\/auth\/passkeys\/\d+$/.test(new URL(response.url()).pathname)
  ));
  await row.locator(".adm-passkey-remove").click();
  await confirmVisibleDialog(page);
  await answerPrompt(page, "phase-five-passkey-revoke");
  assert((await pending).ok(), "Passkey revoke failed");
  await waitFor(() => page.locator("[data-passkey-id]").count().then((count) => count === 0),
    "revoked passkey removal");

  await signOut(page, "revoked passkey");
  const gate = page.locator("#auth-gate-form");
  await gate.locator("input[name='username']").fill(fixture.roles.admin);
  await gate.locator("button[type='submit']").click();
  const passkeyAction = gate.locator("button[type='submit']").filter({ hasText: "Use passkey" });
  await visible(passkeyAction, "revoked passkey sign-in action");
  const marks = diagnosticMarks(diagnostics);
  const deniedPending = responseFor(page, "POST", "/api/auth/passkeys/login/verify");
  await passkeyAction.click();
  const denied = await deniedPending;
  assert(denied.status() === 401, `Revoked passkey authentication returned ${denied.status()}`);
  await discardExpectedUiFailure(
    page,
    diagnostics,
    marks,
    "POST",
    "/api/auth/passkeys/login/verify",
    401,
  );
  assert(await gate.isVisible(), "Revoked passkey denial left the sign-in gate");
  await gate.locator("#auth-gate-use-password").click();
  await gate.locator("input[name='password']").fill(adminPassword);
  const loginPending = responseFor(page, "POST", "/api/auth/login");
  await gate.locator("button[type='submit']").click();
  assert((await loginPending).ok(), "Password recovery after revoked passkey failed");
  await gate.waitFor({ state: "detached", timeout: 15_000 });
}

async function exerciseTotp(page, username, password) {
  await openAdminSection(page, "desktop", "settings");
  const firstStart = responseFor(page, "POST", "/api/auth/mfa/totp/start");
  await page.locator("#adm-mfa-start").click();
  assert((await firstStart).ok(), "Initial TOTP enrollment start failed");
  await visible(page.locator("#adm-mfa-cancel"), "TOTP enrollment cancel");
  const cancelPending = responseFor(page, "POST", "/api/auth/mfa/totp/cancel");
  await page.locator("#adm-mfa-cancel").click();
  await confirmVisibleDialog(page);
  await answerPrompt(page, "phase-five-totp-enrollment-cancel");
  assert((await cancelPending).ok(), "TOTP enrollment cancel failed");
  await waitFor(
    () => page.locator("#adm-mfa-cancel").count().then((count) => count === 0),
    "cancelled TOTP enrollment removal",
  );

  const secondStart = responseFor(page, "POST", "/api/auth/mfa/totp/start");
  await page.locator("#adm-mfa-start").click();
  assert((await secondStart).ok(), "Second TOTP enrollment start failed");
  await visible(page.locator("#adm-mfa-confirm"), "second TOTP enrollment confirmation");
  const secret = await page.locator("#adm-mfa-secret").inputValue();
  const enrollmentCode = currentTotp(secret);
  const confirmPending = responseFor(page, "POST", "/api/auth/mfa/totp/confirm");
  await page.locator("#adm-mfa-code").fill(enrollmentCode);
  await page.locator("#adm-mfa-confirm").click();
  assert((await confirmPending).ok(), "TOTP enrollment confirmation failed");
  await visible(page.locator("#adm-mfa-recovery-output"), "TOTP recovery codes");
  const initialRecoveryCodes = await page.locator("#adm-mfa-recovery-output").inputValue();
  const oldRecoveryCode = initialRecoveryCodes.split(/\r?\n/)
    .map((line) => line.trim()).find(Boolean);
  assert(oldRecoveryCode, "Initial recovery codes were empty");

  const regeneratePending = responseFor(page, "POST", "/api/auth/mfa/recovery-codes/regenerate");
  await page.locator("#adm-mfa-regenerate").click();
  await confirmVisibleDialog(page);
  await answerPrompt(page, "phase-five-recovery-regeneration");
  assert((await regeneratePending).ok(), "TOTP recovery regeneration failed");
  await visible(page.locator("#adm-mfa-recovery-output"), "regenerated TOTP recovery codes");
  await waitFor(
    () => page.locator("#adm-mfa-recovery-output").inputValue()
      .then((value) => value !== initialRecoveryCodes),
    "regenerated TOTP recovery-code repaint",
  );
  const recoveryCode = await page.locator("#adm-mfa-recovery-output").inputValue()
    .then((value) => value.split(/\r?\n/).map((line) => line.trim()).find(Boolean));
  assert(recoveryCode, "Regenerated recovery codes were empty");

  await signOut(page, "recovery-code use");
  let mfaForm = await enterPasswordLoginStage(page, username, password);
  await mfaForm.locator("input[name='recovery_code']").fill(oldRecoveryCode);
  let loginPending = responseFor(page, "POST", "/api/auth/login");
  await mfaForm.locator("button[type='submit']").click();
  const invalidated = await loginPending;
  const invalidatedBody = await invalidated.json();
  assert(invalidated.status() === 200 && invalidatedBody.status === "mfa_required",
    "Pre-regeneration recovery code was not invalidated");
  await mfaForm.locator("input[name='recovery_code']").fill(recoveryCode);
  loginPending = responseFor(page, "POST", "/api/auth/login");
  await mfaForm.locator("button[type='submit']").click();
  const recovered = await loginPending;
  const recoveredBody = await recovered.json();
  assert(recovered.ok() && recoveredBody.status !== "mfa_required",
    "Recovery-code sign-in failed");
  await mfaForm.waitFor({ state: "detached", timeout: 15_000 });

  await signOut(page, "recovery-code reuse");
  mfaForm = await enterPasswordLoginStage(page, username, password);
  await mfaForm.locator("input[name='recovery_code']").fill(recoveryCode);
  loginPending = responseFor(page, "POST", "/api/auth/login");
  await mfaForm.locator("button[type='submit']").click();
  const reused = await loginPending;
  const reusedBody = await reused.json();
  assert(reused.status() === 200 && reusedBody.status === "mfa_required",
    "Reused recovery code was not rejected by the MFA challenge");
  await mfaForm.locator("input[name='recovery_code']").fill("");
  await mfaForm.locator("input[name='mfa_code']").fill(
    await freshTotpAfter(secret, enrollmentCode),
  );
  loginPending = responseFor(page, "POST", "/api/auth/login");
  await mfaForm.locator("button[type='submit']").click();
  assert((await loginPending).ok(), "TOTP fallback after recovery-code reuse failed");
  await mfaForm.waitFor({ state: "detached", timeout: 15_000 });
  await openAdminSection(page, "desktop", "settings");

  const disablePending = responseFor(page, "POST", "/api/auth/mfa/disable");
  await page.locator("#adm-mfa-disable").click();
  await confirmVisibleDialog(page);
  await answerPrompt(page, "phase-five-mfa-disable");
  assert((await disablePending).ok(), "TOTP disable failed");
  await waitFor(() => page.locator("#adm-mfa-disable").isDisabled(), "disabled TOTP controls");
}

async function exerciseSessionRevocation(options, page) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "gardenops-phase-five-session-"));
  const secondary = await createGuardedContext(
    options.browser,
    options.devices,
    "desktop",
    temporary,
    "secondary-admin-session",
  );
  const secondaryPage = await secondary.context.newPage();
  let closed = false;
  try {
    await secondaryPage.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    await authenticate(secondaryPage, options.username, options.password);
    secondary.markAuthenticated();
    await openAdminSection(page, "desktop", "sessions");
    assert(!await page.locator("#admin-view").innerText().then((text) => text.includes("token_hash")),
      "Session UI exposed a token hash label");
    const row = page.locator(`.adm-users-desktop [data-session-id]:has-text("${options.username}")`)
      .filter({ has: page.locator(".adm-session-revoke-one") }).first();
    await visible(row, "secondary administrator session");
    const revokePending = page.waitForResponse((response) => (
      response.request().method() === "DELETE"
      && /^\/api\/auth\/sessions\/[^/]+$/.test(new URL(response.url()).pathname)
    ));
    await row.locator(".adm-session-revoke-one").click();
    await confirmVisibleDialog(page);
    assert((await revokePending).ok(), "Per-session revoke failed");
    const revokedStatus = await secondaryPage.evaluate(async () => (
      await fetch("/api/auth/me", { credentials: "include" })
    ).status);
    assert(revokedStatus === 401, "Revoked browser session remained authorized");
    await secondary.close("passed");
    closed = true;
  } finally {
    if (!closed) await secondary.context.close().catch(() => undefined);
    fs.rmSync(temporary, { force: true, recursive: true });
  }
}

function ageDisposableSession(username, mode) {
  assert(/^[a-z0-9_]{1,80}$/.test(username), "Session-expiry fixture username is invalid");
  assert(["absolute", "idle"].includes(mode), "Session-expiry mode is invalid");
  const assignment = mode === "idle"
    ? "expires_at_ms = created_at_ms"
    : "created_at_ms = 1, last_seen_at_ms = 1";
  execFileSync("psql", [
    process.env.GARDENOPS_DISPOSABLE_POSTGRES_URL,
    "--no-psqlrc",
    "--set=ON_ERROR_STOP=1",
    "--quiet",
    "--command",
    `UPDATE auth_sessions SET ${assignment} WHERE user_id = (`
      + `SELECT id FROM auth_users WHERE username = '${username}'`
      + ");",
  ], { stdio: "pipe" });
}

async function exerciseSessionExpiry(options) {
  const cases = [
    { mode: "idle", password: VIEWER_PASSWORD, username: options.fixture.roles.viewer }, // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
    { mode: "absolute", password: EDITOR_PASSWORD, username: options.fixture.roles.editor }, // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
  ];
  for (const expiry of cases) {
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), `gardenops-phase-five-${expiry.mode}-`));
    const guarded = await createGuardedContext(
      options.browser,
      options.devices,
      "desktop",
      temporary,
      `${expiry.mode}-session-expiry`,
    );
    const expiryPage = await guarded.context.newPage();
    let closed = false;
    try {
      await expiryPage.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
      await authenticate(expiryPage, expiry.username, expiry.password);
      guarded.markAuthenticated();
      await expiryPage.goto("about:blank");
      ageDisposableSession(expiry.username, expiry.mode);
      const expiredSession = await expiryPage.goto(
        new URL("/api/auth/me", options.baseUrl).href,
        { waitUntil: "domcontentloaded" },
      );
      assert(expiredSession?.status() === 401,
        `${expiry.mode} session remained authorized after its expiry boundary`);
      await guarded.close("passed");
      closed = true;
    } finally {
      if (!closed) await guarded.context.close().catch(() => undefined);
      fs.rmSync(temporary, { force: true, recursive: true });
    }
  }
}

async function exerciseLiveRoleRefresh(options, page) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "gardenops-phase-five-role-"));
  const secondary = await createGuardedContext(
    options.browser,
    options.devices,
    "desktop",
    temporary,
    "secondary-editor-role-refresh",
  );
  const secondaryPage = await secondary.context.newPage();
  let closed = false;
  try {
    await secondaryPage.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const initial = await authenticate(secondaryPage, options.fixture.roles.editor, EDITOR_PASSWORD);
    assert(initial.role === "editor", "Role-refresh fixture did not start as editor");
    secondary.markAuthenticated();
    const users = await browserFetch(page, { path: "/api/auth/users" });
    const editor = users.body?.users?.find((user) => (
      user.username === options.fixture.roles.editor
    ));
    assert(users.status === 200 && Number.isSafeInteger(editor?.id),
      "Role-refresh editor account was not found");
    await secondaryPage.goto("about:blank");
    const downgraded = await browserFetch(page, {
      body: { action_reason: "phase-five-live-role-downgrade", role: "viewer" },
      method: "PATCH",
      path: `/api/auth/users/${editor.id}`,
    });
    assert(downgraded.status === 200 && downgraded.body.role === "viewer",
      "Live editor downgrade failed");
    const downgradedSession = await secondaryPage.goto(
      new URL("/api/auth/me", options.baseUrl).href,
      { waitUntil: "domcontentloaded" },
    );
    assert(downgradedSession?.status() === 401,
      "Downgraded account retained its pre-change browser session");
    await secondaryPage.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const viewer = await authenticate(secondaryPage, options.fixture.roles.editor, EDITOR_PASSWORD);
    assert(viewer.role === "viewer", "Downgraded account did not refresh to viewer after sign-in");

    await secondaryPage.goto("about:blank");
    const restored = await browserFetch(page, {
      body: { action_reason: "phase-five-live-role-restore", role: "editor" },
      method: "PATCH",
      path: `/api/auth/users/${editor.id}`,
    });
    assert(restored.status === 200 && restored.body.role === "editor",
      "Live editor role restoration failed");
    const restoredSession = await secondaryPage.goto(
      new URL("/api/auth/me", options.baseUrl).href,
      { waitUntil: "domcontentloaded" },
    );
    assert(restoredSession?.status() === 200,
      "Restored role did not refresh on the surviving browser session");
    const restoredProfile = await restoredSession.json();
    assert(restoredProfile.role === "editor",
      "Restored browser session retained the viewer role");
    await secondaryPage.goto(options.baseUrl, { waitUntil: "domcontentloaded" });
    const refreshed = await browserFetch(secondaryPage, { path: "/api/auth/me" });
    assert(refreshed.status === 200 && refreshed.body.role === "editor",
      "Restored role remained stale after returning to the app");
    await signOut(secondaryPage, "role-refresh secondary session");
    await secondary.close("passed");
    closed = true;
  } finally {
    if (!closed) await secondary.context.close().catch(() => undefined);
    fs.rmSync(temporary, { force: true, recursive: true });
  }
}

async function exerciseIncidentControl(page, diagnostics, fixture) {
  await openAdminSection(page, "desktop", "system");
  const enablePending = responseFor(page, "PATCH", "/api/auth/emergency-read-only");
  await page.locator("#adm-ero-toggle").click();
  await answerPrompt(page, "phase-five-read-only-enable");
  await answerPrompt(page, "15");
  assert((await enablePending).ok(), "Emergency read-only enable failed");
  await waitFor(() => page.locator("#adm-ero-toggle").getAttribute("aria-pressed")
    .then((value) => value === "true"), "emergency read-only enabled state");
  await expectedHttpFailure(page, diagnostics, {
    body: {
      content: "Phase 5 blocked incident write",
      date: "2026-07-15",
      entry_type: "note",
      plant_ids: [],
      plot_ids: [fixture.gardens.alpha.plot_id],
      title: "Phase 5 blocked incident write",
    },
    method: "POST",
    path: "/api/journal",
  }, 503);
  const disablePending = responseFor(page, "PATCH", "/api/auth/emergency-read-only");
  await page.locator("#adm-ero-toggle").click();
  await answerPrompt(page, "phase-five-read-only-disable");
  assert((await disablePending).ok(), "Emergency read-only disable failed");
  await waitFor(() => page.locator("#adm-ero-toggle").getAttribute("aria-pressed")
    .then((value) => value === "false"), "emergency read-only disabled state");
}

async function acceptInvitation(
  page,
  guarded,
  inviteLink,
  password,
  expectedRole,
  expectedGardenId,
  virtualAuthenticator = null,
) {
  assert(inviteLink, `Missing ${expectedRole} invitation link`);
  await page.goto(inviteLink, { waitUntil: "domcontentloaded" });
  await waitFor(() => page.evaluate(() => !location.hash.includes("invite=")),
    `${expectedRole} invitation URL scrubbing`);
  await guarded.startTracing();
  const form = page.locator("#auth-gate-invite-form");
  await visible(form, `${expectedRole} invitation form`);
  if (virtualAuthenticator) {
    const passkeyButton = form.locator("button").filter({ hasText: "Use passkey" });
    await visible(passkeyButton, `${expectedRole} passwordless invitation action`);
    await virtualAuthenticator.setUserVerified(true);
    const acceptPending = responseFor(
      page,
      "POST",
      "/api/auth/invitations/passkey/register/verify",
    );
    await passkeyButton.click();
    assert((await acceptPending).status() === 201,
      `${expectedRole} passwordless invitation acceptance failed`);
  } else {
    await form.locator("input[name='password']").fill(password);
    const acceptPending = responseFor(page, "POST", "/api/auth/invitations/accept");
    await form.locator("button[type='submit']").click();
    assert((await acceptPending).ok(), `${expectedRole} invitation acceptance failed`);
  }
  const continueButton = page.locator(".auth-gate button").filter({ hasText: /Continue/i });
  await visible(continueButton, `${expectedRole} invitation continuation`);
  await continueButton.click();
  await waitFor(() => page.locator(".auth-gate").count().then((count) => count === 0),
    `${expectedRole} invitation sign-in`);
  guarded.markAuthenticated();
  await completeInvitationOnboarding(page, expectedRole);
  await dismissProactivePasskeyPrompt(page);
  const me = await browserFetch(page, { path: "/api/auth/me" });
  assert(me.status === 200 && me.body.role === expectedRole,
    `Invitation authenticated with role ${me.body?.role}`);
  if (expectedGardenId !== null) {
    assert(me.body.garden_id === expectedGardenId,
      `${expectedRole} invitation selected the wrong garden`);
  }
}

async function exerciseEditorAuthorizationDenials(page, diagnostics, fixture) {
  await expectedHttpFailure(page, diagnostics, {
    body: { address: "Phase 5 forbidden cross-garden mutation" },
    method: "PATCH",
    path: `/api/gardens/${fixture.gardens.alpha.id}/settings`,
  }, 404);
  await expectedHttpFailure(page, diagnostics, {
    body: { address: "Phase 5 stale CSRF mutation" },
    csrfToken: "phase-five-stale-csrf-token",
    method: "PATCH",
    path: `/api/gardens/${fixture.gardens.alpha.id}/settings`,
  }, 403);
}

async function exercisePasswordlessPasskeyRedundancy(
  page,
  diagnostics,
  virtualAuthenticator,
) {
  await openAdminSection(page, "desktop", "settings");
  const initialRows = page.locator("[data-passkey-id]");
  await waitFor(() => initialRows.count().then((count) => count === 1),
    "passwordless primary passkey");

  const reauthPending = responseFor(page, "POST", "/api/auth/reauthenticate/passkey/verify");
  const registerPending = responseFor(page, "POST", "/api/auth/passkeys/register/verify");
  await page.locator("#adm-passkey-add").click();
  await answerPrompt(page, "Phase 5 backup garden key");
  assert((await reauthPending).ok(), "Passwordless passkey step-up failed");
  assert((await registerPending).status() === 201,
    "Passwordless backup passkey registration failed");
  await waitFor(() => page.locator("[data-passkey-id]").count().then((count) => count === 2),
    "passwordless backup passkey registration");

  const row = page.locator("[data-passkey-id]").first();
  const deletePending = page.waitForResponse((response) => (
    response.request().method() === "DELETE"
    && /^\/api\/auth\/passkeys\/\d+$/.test(new URL(response.url()).pathname)
  ));
  const removalReauthPending = responseFor(
    page,
    "POST",
    "/api/auth/reauthenticate/passkey/verify",
  );
  await row.locator(".adm-passkey-remove").click();
  await confirmVisibleDialog(page);
  await answerPrompt(page, "phase-five-passwordless-backup-revoke");
  assert((await removalReauthPending).ok(), "Passwordless removal step-up failed");
  assert((await deletePending).ok(), "Passwordless redundant passkey removal failed");
  await waitFor(() => page.locator("[data-passkey-id]").count().then((count) => count === 1),
    "passwordless redundant passkey removal");

  const finalRow = page.locator("[data-passkey-id]").first();
  const finalRemove = finalRow.locator(".adm-passkey-remove");
  assert(await finalRemove.isDisabled(), "Passwordless final passkey removal remained enabled");
  const finalPasskeyId = await finalRow.getAttribute("data-passkey-id");
  assert(/^\d+$/.test(finalPasskeyId || ""), "Passwordless final passkey ID is invalid");
  await expectedHttpFailure(page, diagnostics, {
    body: { action_reason: "phase-five-final-factor-lockout" },
    method: "DELETE",
    path: `/api/auth/passkeys/${finalPasskeyId}`,
  }, 409);
  assert(await finalRemove.isDisabled(), "Final-factor denial changed the passkey controls");
  await virtualAuthenticator.setUserVerified(true);
}

async function exerciseRoleSurface(page, profile, role) {
  await openAdminSection(page, profile, "settings");
  await visible(page.locator("#adm-main"), `${role} identity settings`);
  const platformSections = ["users", "sessions", "system"];
  for (const section of platformSections) {
    const count = await page.locator(`.adm-nav-btn[data-section='${section}']`).count();
    if (role === "admin") {
      assert(count === 1, `admin is missing ${section} administration`);
    } else {
      assert(count === 0, `${role} retained ${section} administration`);
    }
  }
}

async function runProfile(options, shared) {
  const { artifactDir, baseUrl, browser, devices, fixture, profile, role } = options;
  const guarded = await createGuardedContext(
    browser,
    devices,
    profile,
    artifactDir,
    `phase-five-${role}-${profile}`,
  );
  const page = await guarded.context.newPage();
  const username = role === "admin"
    ? options.username
    : role === "editor" && profile === "desktop"
      ? phaseFive(fixture).invitee_username
      : role === "viewer" && profile === "mobile"
        ? phaseFive(fixture).viewer_invitee_username
        : role === "editor" ? fixture.roles.editor : fixture.roles.viewer;
  const recorder = createApiRecorder(page, { authType: "session", role, username });
  const result = {
    assertions: { failed: [], passed: [], skipped: [] },
    browser_profile: guarded.profile,
    checks: {},
    failure: null,
    profile,
    requests: [],
    role,
    trace: null,
  };
  let caughtError = null;
  let status = "failed";
  let virtualAuthenticator = null;
  try {
    if (role === "admin" && profile === "desktop") {
      virtualAuthenticator = await enableVirtualAuthenticator(guarded.context, page);
      await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      await authenticate(page, options.username, options.password);
      guarded.markAuthenticated();
      await exercisePasskeys(
        page,
        fixture,
        options.password,
        virtualAuthenticator,
        guarded.diagnostics,
      );
      result.checks.passkey_lifecycle = true;
      shared.editorInvite = await createUserInvitation(page, fixture);
      shared.viewerInvite = await createGardenInvitation(page, fixture);
      result.checks.invalid_invitation_side_effects = await exerciseInvalidInvitation(
        page,
        guarded.diagnostics,
      );
      result.checks.invitation_lifecycle = true;
      await exerciseSettings(page, fixture);
      result.checks.settings_persistence = true;
      await exerciseSessionRevocation(options, page);
      result.checks.session_revocation = true;
      await exerciseLiveRoleRefresh(options, page);
      result.checks.live_role_refresh = true;
      await exerciseSessionExpiry(options);
      result.checks.idle_and_absolute_session_expiry = true;
      await exerciseTotp(page, options.username, options.password);
      result.checks.totp_lifecycle = true;
      await exerciseIncidentControl(page, guarded.diagnostics, fixture);
      result.checks.incident_control = true;
      await revokePasskey(page, fixture, guarded.diagnostics, options.password);
      result.checks.revoked_passkey_denial = true;
    } else if (role === "editor" && profile === "desktop") {
      virtualAuthenticator = await enableVirtualAuthenticator(guarded.context, page);
      await acceptInvitation(
        page,
        guarded,
        shared.editorInvite,
        INVITEE_PASSWORD,
        "editor",
        null,
        virtualAuthenticator,
      );
      await exercisePasswordlessPasskeyRedundancy(
        page,
        guarded.diagnostics,
        virtualAuthenticator,
      );
      await exerciseEditorAuthorizationDenials(page, guarded.diagnostics, fixture);
      result.checks.passwordless_invitation = true;
      result.checks.passwordless_passkey_redundancy = true;
      result.checks.cross_garden_and_stale_csrf_denials = true;
      await exerciseRoleSurface(page, profile, role);
      result.checks.editor_identity_surface = true;
    } else if (role === "viewer" && profile === "mobile") {
      await acceptInvitation(
        page,
        guarded,
        shared.viewerInvite,
        VIEWER_INVITEE_PASSWORD,
        "viewer",
        fixture.gardens.alpha.id,
      );
      await exerciseRoleSurface(page, profile, role);
      result.checks.viewer_identity_surface = true;
      result.checks.viewer_admin_controls_absent = true;
    } else {
      await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      const password = role === "admin" ? options.password
        : role === "editor" ? EDITOR_PASSWORD : VIEWER_PASSWORD;
      const auth = await authenticate(page, username, password);
      guarded.markAuthenticated();
      assert(auth.role === role, `Phase 5 ${role} fixture role drifted`);
      await dismissProactivePasskeyPrompt(page);
      await exerciseRoleSurface(page, profile, role);
      if (role === "admin") {
        await visible(page.locator(".adm-identity-session-list"), "mobile identity sessions");
        result.checks.mobile_identity_settings = true;
      } else if (role === "editor") {
        result.checks.editor_identity_surface = true;
      } else {
        result.checks.viewer_identity_surface = true;
        result.checks.viewer_admin_controls_absent = true;
      }
    }
    result.structure = await assertPageStructure(page, `Phase 5 ${role}:${profile}`, {
      enforceControlNames: true,
    });
    assertDiagnosticsClean(guarded.diagnostics, `Phase 5 ${role}:${profile}`);
    result.checks.browser_diagnostics = true;
    result.checks.last_completed_step = `${role}-${profile}-complete`;
    result.assertions.passed.push("phase-five-profile-contract", "browser-diagnostics-clean");
    result.requests = recorder.records;
    status = "passed";
  } catch (error) {
    caughtError = error;
    result.failure = "profile journey failed; see top-level sanitized failure";
    result.assertions.failed.push(result.failure);
  } finally {
    if (virtualAuthenticator) {
      await virtualAuthenticator.client.send("WebAuthn.removeVirtualAuthenticator", {
        authenticatorId: virtualAuthenticator.authenticatorId,
      }).catch(() => undefined);
    }
    result.diagnostics = guarded.diagnostics;
    try { result.trace = await guarded.close(status); } catch (error) { if (!caughtError) caughtError = error; }
  }
  return { error: caughtError, result };
}

async function runIdentityAndRoles(options, profileRunner = runProfile) {
  const profiles = [
    ["admin", "desktop"],
    ["admin", "mobile"],
    ["editor", "desktop"],
    ["editor", "mobile"],
    ["viewer", "desktop"],
    ["viewer", "mobile"],
  ];
  const shared = { editorInvite: "", viewerInvite: "" };
  const results = [];
  for (const [role, profile] of profiles) {
    const outcome = await profileRunner({ ...options, profile, role }, shared);
    results.push(outcome.result);
    if (options.onProfile) options.onProfile(outcome.result);
    if (outcome.error) throw outcome.error;
  }
  shared.editorInvite = "";
  shared.viewerInvite = "";
  return results;
}

module.exports = {
  currentTotp,
  runIdentityAndRoles,
};
