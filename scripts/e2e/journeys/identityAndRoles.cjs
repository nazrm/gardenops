"use strict";

const crypto = require("node:crypto");
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

async function waitForProactivePasskeyPrompt(page, timeout = 2_000) {
  const dialog = page.locator(".modal:visible").filter({
    has: page.locator(".passkey-prompt-modal"),
  }).last();
  try {
    await dialog.waitFor({ state: "visible", timeout });
    return dialog;
  } catch {
    return null;
  }
}

async function dismissProactivePasskeyPrompt(page) {
  const dialog = await waitForProactivePasskeyPrompt(page);
  if (!dialog) return;
  const dismissed = responseFor(page, "POST", "/api/auth/passkeys/prompt/dismiss");
  await dialog.locator(".confirm-no").click();
  assert((await dismissed).ok(), "Passkey prompt dismissal failed");
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
    const csrf = document.cookie.split("; ")
      .find((entry) => entry.startsWith("gardenops_csrf="))?.split("=").slice(1).join("=") || "";
    const response = await fetch(input.path, {
      body: input.body === undefined ? undefined : JSON.stringify(input.body),
      credentials: "include",
      headers: {
        ...(input.body === undefined ? {} : { "content-type": "application/json" }),
        "x-csrf-token": decodeURIComponent(csrf),
      },
      method: input.method || "GET",
    });
    const text = await response.text();
    let body = text;
    try { body = text ? JSON.parse(text) : null; } catch { /* text response */ }
    return { body, status: response.status };
  }, request);
}

async function expectedHttpFailure(page, diagnostics, request, expectedStatus) {
  const httpMark = diagnostics.httpErrors.length;
  const consoleMark = diagnostics.consoleErrors.length;
  const classifiedMark = diagnostics.classifiedConsoleDiagnostics.length;
  const result = await browserFetch(page, request);
  assert(result.status === expectedStatus,
    `${request.method || "GET"} ${request.path} returned ${result.status}`);
  await waitFor(() => diagnostics.httpErrors.length === httpMark + 1,
    `HTTP diagnostic for ${request.path}`);
  const httpAdded = diagnostics.httpErrors.splice(httpMark);
  assert(httpAdded.length === 1 && httpAdded[0] === `${expectedStatus} ${request.path}`,
    `Unexpected browser HTTP diagnostics for ${request.path}`);
  await waitFor(() => diagnostics.consoleErrors.length === consoleMark + 1,
    `console diagnostic for ${request.path}`);
  const consoleAdded = diagnostics.consoleErrors.splice(consoleMark);
  const classifiedAdded = diagnostics.classifiedConsoleDiagnostics.splice(classifiedMark);
  assert(consoleAdded.length === 1 && classifiedAdded.length === 1
    && classifiedAdded[0].method === (request.method || "GET")
    && classifiedAdded[0].path === request.path
    && classifiedAdded[0].status === expectedStatus,
  `Unexpected browser console diagnostics for ${request.path}`);
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
  return { authenticatorId: result.authenticatorId, client };
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
  const pending = responseFor(page, "PUT", "/api/auth/me/settings");
  await page.locator("#adm-plot-meaning-save").click();
  assert((await pending).ok(), "Identity settings save failed");
  await page.reload({ waitUntil: "domcontentloaded" });
  await openAdminSection(page, "desktop", "settings");
  const persisted = page.locator(".adm-plot-meaning-row").filter({
    has: page.locator(`input[value='${phaseFive(fixture).settings_pattern.toUpperCase()}']`),
  });
  await visible(persisted.first(), "persisted identity setting");
}

async function exercisePasskeys(page, fixture, adminPassword) {
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

  const logoutPending = responseFor(page, "POST", "/api/auth/logout");
  await page.locator("#adm-sign-out").click();
  assert((await logoutPending).ok(), "Passkey test logout failed");
  const gate = page.locator("#auth-gate-form");
  await visible(gate, "passwordless sign-in gate");
  await gate.locator("input[name='username']").fill(fixture.roles.admin);
  const loginPending = responseFor(page, "POST", "/api/auth/passkeys/login/verify");
  await gate.locator("button[type='submit']").click();
  assert((await loginPending).ok(), "Passwordless passkey sign-in failed");
  await waitFor(() => page.locator(".auth-gate").count().then((count) => count === 0),
    "passwordless sign-in completion");
}

async function revokePasskey(page, fixture) {
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
}

async function exerciseTotp(page) {
  await openAdminSection(page, "desktop", "settings");
  const firstStart = responseFor(page, "POST", "/api/auth/mfa/totp/start");
  await page.locator("#adm-mfa-start").click();
  assert((await firstStart).ok(), "Initial TOTP enrollment start failed");
  await visible(page.locator("#adm-mfa-cancel"), "TOTP enrollment cancel");
  const cancelPending = responseFor(page, "POST", "/api/auth/mfa/totp/cancel");
  await page.locator("#adm-mfa-cancel").click();
  await confirmVisibleDialog(page);
  assert((await cancelPending).ok(), "TOTP enrollment cancel failed");

  const secondStart = responseFor(page, "POST", "/api/auth/mfa/totp/start");
  await page.locator("#adm-mfa-start").click();
  assert((await secondStart).ok(), "Second TOTP enrollment start failed");
  const secret = await page.locator("#adm-mfa-secret").inputValue();
  const confirmPending = responseFor(page, "POST", "/api/auth/mfa/totp/confirm");
  await page.locator("#adm-mfa-code").fill(currentTotp(secret));
  await page.locator("#adm-mfa-confirm").click();
  assert((await confirmPending).ok(), "TOTP enrollment confirmation failed");
  await visible(page.locator("#adm-mfa-recovery-output"), "TOTP recovery codes");

  const regeneratePending = responseFor(page, "POST", "/api/auth/mfa/recovery-codes/regenerate");
  await page.locator("#adm-mfa-regenerate").click();
  await confirmVisibleDialog(page);
  await answerPrompt(page, "phase-five-recovery-regeneration");
  assert((await regeneratePending).ok(), "TOTP recovery regeneration failed");
  await visible(page.locator("#adm-mfa-recovery-output"), "regenerated TOTP recovery codes");

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
    const row = page.locator(`[data-session-id]:has-text("${options.username}")`)
      .filter({ has: page.locator(".adm-session-revoke-one") }).first();
    await visible(row, "secondary administrator session");
    const revokePending = page.waitForResponse((response) => (
      response.request().method() === "DELETE"
      && /^\/api\/auth\/sessions\/[^/]+$/.test(new URL(response.url()).pathname)
    ));
    await row.locator(".adm-session-revoke-one").click();
    await confirmVisibleDialog(page);
    await answerPrompt(page, "phase-five-session-revoke");
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

async function acceptInvitation(page, guarded, inviteLink, password, expectedRole, expectedGardenId) {
  assert(inviteLink, `Missing ${expectedRole} invitation link`);
  await page.goto(inviteLink, { waitUntil: "domcontentloaded" });
  await waitFor(() => page.evaluate(() => !location.hash.includes("invite=")),
    `${expectedRole} invitation URL scrubbing`);
  await guarded.startTracing();
  const form = page.locator("#auth-gate-invite-form");
  await visible(form, `${expectedRole} invitation form`);
  await form.locator("input[name='password']").fill(password);
  const acceptPending = responseFor(page, "POST", "/api/auth/invitations/accept");
  await form.locator("button[type='submit']").click();
  assert((await acceptPending).ok(), `${expectedRole} invitation acceptance failed`);
  const continueButton = page.locator(".auth-gate button").filter({ hasText: /Continue/i });
  await visible(continueButton, `${expectedRole} invitation continuation`);
  await continueButton.click();
  await waitFor(() => page.locator(".auth-gate").count().then((count) => count === 0),
    `${expectedRole} invitation sign-in`);
  guarded.markAuthenticated();
  await dismissProactivePasskeyPrompt(page);
  const me = await browserFetch(page, { path: "/api/auth/me" });
  assert(me.status === 200 && me.body.role === expectedRole,
    `Invitation authenticated with role ${me.body?.role}`);
  if (expectedGardenId !== null) {
    assert(me.body.garden_id === expectedGardenId,
      `${expectedRole} invitation selected the wrong garden`);
  }
}

async function exerciseRoleSurface(page, profile, role) {
  await openAdminSection(page, profile, "settings");
  await visible(page.locator("#adm-main"), `${role} identity settings`);
  assert(await page.locator(".adm-nav-btn[data-section='users']").count() === 0,
    `${role} retained platform user controls`);
  assert(await page.locator(".adm-nav-btn[data-section='sessions']").count() === 0,
    `${role} retained platform session administration`);
  assert(await page.locator(".adm-nav-btn[data-section='system']").count() === 0,
    `${role} retained incident administration`);
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
      : role === "viewer" && profile === "desktop"
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
      await exercisePasskeys(page, fixture, options.password);
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
      await exerciseTotp(page);
      result.checks.totp_lifecycle = true;
      await exerciseIncidentControl(page, guarded.diagnostics, fixture);
      result.checks.incident_control = true;
      await revokePasskey(page, fixture);
    } else if (role === "editor" && profile === "desktop") {
      await acceptInvitation(
        page,
        guarded,
        shared.editorInvite,
        INVITEE_PASSWORD,
        "editor",
        null,
      );
      await exerciseRoleSurface(page, profile, role);
      result.checks.editor_identity_surface = true;
    } else if (role === "viewer" && profile === "desktop") {
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
