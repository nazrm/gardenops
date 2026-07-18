"use strict";

const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { assert, visible } = require("./completeJourneyAssertions.cjs");

const ROOT = path.resolve(__dirname, "..", "..");
const NETWORK_PROTOCOLS = new Set(["http:", "https:", "ws:", "wss:"]);
const LOCAL_PROTOCOLS = new Set(["about:", "blob:", "data:"]);
const PIXEL_7_VIEWPORT = Object.freeze({ height: 839, width: 412 });
const IPAD_GEN_7_VIEWPORT = Object.freeze({ height: 1080, width: 810 });
const REFLOW_200_VIEWPORT = Object.freeze({ height: 450, width: 720 });
const ROUTE_GUARD_PROBE_URL = "http://192.0.2.1/api/complete-journey-route-guard";
const TRACE_CONTROLS = new WeakMap();
const EXPECTED_CONSOLE_DIAGNOSTIC_CONTEXTS = new Set([
  "calendar-feed-revoked",
  "map-import-rejected",
  "network-guard-probe",
  "postauth-signout",
  "preauth-session-probe",
  "viewer-calendar-event-write-denied",
  "viewer-calendar-subscription-write-denied",
  "viewer-harvest-write-denied",
  "viewer-issue-write-denied",
  "viewer-journal-write-denied",
  "viewer-lidar-write-denied",
  "viewer-media-write-denied",
  "viewer-task-write-denied",
  "viewer-weather-refresh-denied",
]);
const EXPECTED_SILENT_HTTP_CONTEXTS = new Set([
  "postauth-signout",
  "preauth-session-probe",
]);
const EXPECTED_ABORTED_REQUEST_PATHS = new Set([
  "/api/calendar/export.ics",
  "/api/dashboard/badge-counts",
]);

function redactTokenShapedSecrets(value) {
  return String(value)
    .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]{8,}/gi, "Bearer [redacted]")
    .replace(/\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|token|password)\b\s*[=:]\s*)[^\s,;&]+/gi,
      "$1[redacted]")
    .replace(/([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)=)[^&#\s]+/gi,
      "$1[redacted]")
    .replace(/([a-z][a-z0-9+.-]*:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1[redacted]@")
    .replace(/\b(?:sk|rk|pk)[_-][A-Za-z0-9_-]{16,}\b/g, "[redacted-token]")
    .replace(/\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b/g, "[redacted-token]")
    .replace(/\bgithub_pat_[A-Za-z0-9_]{20,}\b/g, "[redacted-token]")
    .replace(/\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{6,}\b/g, "[redacted-token]")
    .replace(/\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, "[redacted-token]");
}

function sanitizeDiagnostic(value) {
  return redactTokenShapedSecrets(value)
    ? "[redacted diagnostic; inspect private runner logs]"
    : "";
}

function isLoopbackHostname(hostname) {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (normalized === "::1") return true;
  const octets = normalized.split(".");
  return octets.length === 4
    && octets.every((octet) => /^\d+$/.test(octet) && Number(octet) <= 255)
    && Number(octets[0]) === 127;
}

function isDisposableLoopbackHostname(hostname, { allowLocalhost = false } = {}) {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return normalized === "127.0.0.1" || (allowLocalhost && normalized === "localhost");
}

function normalizedNetworkOrigin(parsed) {
  const protocol = parsed.protocol === "ws:"
    ? "http:"
    : (parsed.protocol === "wss:" ? "https:" : parsed.protocol);
  return `${protocol}//${parsed.host}`;
}

function disposableOrigin(rawUrl, label) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error(`${label} must be an absolute URL`);
  }
  assert(["http:", "https:"].includes(parsed.protocol), `${label} must use HTTP(S)`);
  assert(
    isDisposableLoopbackHostname(parsed.hostname, { allowLocalhost: label === "BASE_URL" }),
    `${label} must use its dedicated loopback hostname`,
  );
  assert(parsed.port && /^\d+$/.test(parsed.port), `${label} must include an explicit port`);
  assert(Number(parsed.port) !== 5432, `${label} must not use PostgreSQL port 5432`);
  assert(!parsed.username && !parsed.password, `${label} must not include credentials`);
  assert(!parsed.search && !parsed.hash, `${label} must not include query or fragment`);
  return normalizedNetworkOrigin(parsed);
}

function allowedBrowserOrigins({
  backendUrl = process.env.GARDENOPS_VITE_PROXY_TARGET || "",
  baseUrl = process.env.BASE_URL || "",
  providerUrl = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_URL || "",
} = {}) {
  const origins = new Set();
  if (baseUrl) origins.add(disposableOrigin(baseUrl, "BASE_URL"));
  if (backendUrl) origins.add(disposableOrigin(backendUrl, "GARDENOPS_VITE_PROXY_TARGET"));
  if (providerUrl) origins.add(disposableOrigin(
    providerUrl,
    "GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_URL",
  ));
  return origins;
}

function isAllowedUrl(rawUrl, allowedOrigins = allowedBrowserOrigins()) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false;
  }
  if (NETWORK_PROTOCOLS.has(parsed.protocol)) {
    return allowedOrigins.has(normalizedNetworkOrigin(parsed));
  }
  return LOCAL_PROTOCOLS.has(parsed.protocol);
}

function safeUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}`;
  } catch {
    return "invalid-url";
  }
}

function expectedHttpDiagnosticContext({ authState, authenticated, method, path: pathname, status }) {
  if (
    authState === "signed-out"
    && method === "GET"
    && pathname.startsWith("/api/")
    && status === 401
  ) {
    return "postauth-signout";
  }
  if (
    authState !== "signed-out"
    && !authenticated
    && method === "GET"
    && pathname === "/api/auth/me"
    && status === 401
  ) {
    return "preauth-session-probe";
  }
  if (method === "POST" && pathname === "/api/plots/import" && [409, 413, 422].includes(status)) {
    return "map-import-rejected";
  }
  if (
    method === "GET"
    && /^\/calendar\/subscriptions\/[^/]+\.ics$/.test(pathname)
    && status === 404
  ) {
    return "calendar-feed-revoked";
  }
  if (
    method === "POST"
    && /^\/api\/tasks\/[^/]+\/action$/.test(pathname)
    && status === 403
  ) {
    return "viewer-task-write-denied";
  }
  if (method === "POST" && pathname === "/api/calendar/manual-events" && status === 403) {
    return "viewer-calendar-event-write-denied";
  }
  if (method === "POST" && pathname === "/api/calendar/subscriptions" && status === 403) {
    return "viewer-calendar-subscription-write-denied";
  }
  if (method === "POST" && pathname === "/api/journal" && status === 403) {
    return "viewer-journal-write-denied";
  }
  if (method === "POST" && pathname === "/api/issues" && status === 403) {
    return "viewer-issue-write-denied";
  }
  if (method === "POST" && pathname === "/api/harvest" && status === 403) {
    return "viewer-harvest-write-denied";
  }
  if (method === "POST" && pathname === "/api/media/upload" && status === 403) {
    return "viewer-media-write-denied";
  }
  if (
    ["DELETE", "POST"].includes(method)
    && /^\/api\/gardens\/\d+\/lidar$/.test(pathname)
    && status === 403
  ) {
    return "viewer-lidar-write-denied";
  }
  if (method === "POST" && pathname === "/api/weather/check" && status === 403) {
    return "viewer-weather-refresh-denied";
  }
  return "unexpected-http-response";
}

function isExpectedSilentHttpContext(context) {
  return EXPECTED_SILENT_HTTP_CONTEXTS.has(context);
}

function consoleStatus(text) {
  const match = /\b([1-5]\d{2})\s+\([^)]+\)/.exec(String(text || ""));
  return match ? Number(match[1]) : null;
}

function diagnosticLabel(event) {
  return [event.id, event.context, event.method, event.status, event.path].join(" ");
}

function assertLoopbackBaseUrl(baseUrl) {
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new Error("Complete journey BASE_URL must be an absolute URL");
  }
  assert(["http:", "https:"].includes(parsed.protocol), "BASE_URL must use HTTP(S)");
  assert(
    isDisposableLoopbackHostname(parsed.hostname, { allowLocalhost: true }),
    "BASE_URL must use localhost or literal 127.0.0.1",
  );
  assert(parsed.port && /^\d+$/.test(parsed.port), "BASE_URL must include an explicit port");
  assert(Number(parsed.port) !== 5432, "BASE_URL must not use PostgreSQL port 5432");
  assert(!parsed.username && !parsed.password, "BASE_URL must not include credentials");
  assert(!parsed.search && !parsed.hash, "BASE_URL must not include query or fragment");
}

function browserProfile(devices, name) {
  if (name === "desktop") {
    return { viewport: { width: 1440, height: 900 } };
  }
  if (name === "mobile") {
    const pixel = devices["Pixel 7"];
    assert(pixel, "Playwright Pixel 7 device profile is unavailable");
    return { ...pixel };
  }
  if (name === "tablet") {
    const tablet = devices["iPad (gen 7)"];
    assert(tablet, "Playwright iPad (gen 7) device profile is unavailable");
    return { ...tablet };
  }
  if (name === "desktop-reduced-motion") {
    return { reducedMotion: "reduce", viewport: { width: 1440, height: 900 } };
  }
  if (name === "mobile-reduced-motion") {
    const pixel = devices["Pixel 7"];
    assert(pixel, "Playwright Pixel 7 device profile is unavailable");
    return { ...pixel, reducedMotion: "reduce" };
  }
  if (name === "desktop-reflow-200") {
    return {
      deviceScaleFactor: 2,
      hasTouch: false,
      isMobile: false,
      screen: { height: 900, width: 1440 },
      viewport: { ...REFLOW_200_VIEWPORT },
    };
  }
  throw new Error(`Unknown complete journey browser profile: ${name}`);
}

function assertBrowserProfileContract(profileName, runtime) {
  const viewport = runtime?.viewport || {};
  if (profileName === "mobile") {
    assert(runtime?.is_mobile === true, "Pixel 7 runtime must report mobile mode");
    assert(runtime?.has_touch === true && runtime?.max_touch_points > 0,
      "Pixel 7 runtime must expose touch input");
    assert(viewport.width === PIXEL_7_VIEWPORT.width && viewport.height === PIXEL_7_VIEWPORT.height,
      "Pixel 7 runtime viewport was unexpected");
    assert(/\bPixel 7\b/i.test(String(runtime?.user_agent || "")),
      "Pixel 7 runtime user agent was unexpected");
    return "pixel-7";
  }
  if (profileName === "tablet") {
    assert(runtime?.is_mobile === true && runtime?.has_touch === true && runtime?.max_touch_points > 0,
      "Tablet runtime must expose mobile touch input");
    assert(viewport.width === IPAD_GEN_7_VIEWPORT.width && viewport.height === IPAD_GEN_7_VIEWPORT.height,
      "Tablet runtime viewport was unexpected");
    assert(/\biPad\b/i.test(String(runtime?.user_agent || "")),
      "Tablet runtime user agent was unexpected");
    return "ipad-gen-7";
  }
  if (profileName === "desktop-reflow-200") {
    assert(runtime?.is_mobile === false && runtime?.has_touch === false,
      "200% reflow runtime unexpectedly exposes mobile touch input");
    assert(viewport.width === REFLOW_200_VIEWPORT.width && viewport.height === REFLOW_200_VIEWPORT.height,
      "200% reflow runtime viewport was unexpected");
    assert(runtime?.device_pixel_ratio === 2,
      "200% reflow runtime device pixel ratio was unexpected");
    return "desktop-reflow-equivalent-200";
  }
  const reducedMotion = profileName.endsWith("-reduced-motion");
  const desktopReduced = profileName === "desktop-reduced-motion";
  const mobileReduced = profileName === "mobile-reduced-motion";
  assert(profileName === "desktop" || desktopReduced || mobileReduced,
    `Unknown complete journey profile contract: ${profileName}`);
  if (mobileReduced) {
    assert(runtime?.is_mobile === true && runtime?.has_touch === true && runtime?.max_touch_points > 0,
      "Reduced-motion Pixel 7 runtime must expose touch input");
    assert(viewport.width === PIXEL_7_VIEWPORT.width && viewport.height === PIXEL_7_VIEWPORT.height,
      "Reduced-motion Pixel 7 runtime viewport was unexpected");
    assert(/\bPixel 7\b/i.test(String(runtime?.user_agent || "")),
      "Reduced-motion Pixel 7 runtime user agent was unexpected");
    assert(runtime?.prefers_reduced_motion === true,
      "Reduced-motion Pixel 7 runtime did not report reduced motion");
    return "pixel-7-reduced-motion";
  }
  assert(runtime?.is_mobile === false, "Desktop runtime unexpectedly reports mobile mode");
  assert(runtime?.has_touch === false && runtime?.max_touch_points === 0,
    "Desktop runtime unexpectedly exposes touch input");
  assert(viewport.width === 1440 && viewport.height === 900,
    "Desktop runtime viewport was unexpected");
  if (reducedMotion) {
    assert(runtime?.prefers_reduced_motion === true,
      "Reduced-motion desktop runtime did not report reduced motion");
    return "desktop-chromium-reduced-motion";
  }
  return "desktop-chromium";
}

async function createGuardedContext(
  browser,
  devices,
  profileName,
  artifactDir,
  artifactLabel = profileName,
  originContract = {},
) {
  const profile = browserProfile(devices, profileName);
  const allowedOrigins = allowedBrowserOrigins(originContract);
  const context = await browser.newContext({
    ...profile,
    locale: "en-US",
    timezoneId: "UTC",
  });
  const diagnostics = {
    blockedRequests: [],
    classifiedConsoleDiagnostics: [],
    consoleErrors: [],
    expectedAuth401Responses: 0,
    httpErrors: [],
    ignoredAuth401ConsoleErrors: 0,
    pageErrors: [],
    requestFailures: [],
    expectedRequestAborts: [],
  };
  const pendingHttpConsoleDiagnostics = [];
  const pendingBlockedConsoleDiagnostics = [];
  let diagnosticSequence = 0;
  let authState = "initial";
  let traceStarted = false;
  const startTracing = async () => {
    if (traceStarted) return;
    await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
    traceStarted = true;
  };
  TRACE_CONTROLS.set(context, { startTracing });
  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    if (!isAllowedUrl(requestUrl, allowedOrigins)) {
      const sanitizedUrl = safeUrl(requestUrl);
      diagnostics.blockedRequests.push(sanitizedUrl);
      pendingBlockedConsoleDiagnostics.push({
        context: sanitizedUrl === ROUTE_GUARD_PROBE_URL
          ? "network-guard-probe"
          : "unexpected-blocked-request",
        method: route.request().method(),
        path: new URL(sanitizedUrl).pathname,
        status: 0,
      });
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  if (typeof context.routeWebSocket === "function") {
    await context.routeWebSocket(
      (url) => !isAllowedUrl(url.href, allowedOrigins),
      (socket) => {
        diagnostics.blockedRequests.push(safeUrl(socket.url()));
        socket.close({ code: 1008, reason: "Out-of-contract E2E traffic is blocked" });
      },
    );
  }
  context.on("page", (page) => {
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const text = message.text();
      const status = consoleStatus(text);
      let locationPath = "";
      try {
        const locationUrl = message.location()?.url || "";
        if (locationUrl) locationPath = new URL(locationUrl).pathname;
      } catch {
        locationPath = "";
      }
      const statusCandidates = status === null ? [] : pendingHttpConsoleDiagnostics
        .map((entry, index) => ({ entry, index }))
        .filter(({ entry }) => entry.status === status);
      const pathCandidates = locationPath
        ? statusCandidates.filter(({ entry }) => entry.path === locationPath)
        : [];
      const candidates = pathCandidates.length > 0 ? pathCandidates : statusCandidates;
      let pendingIndex = candidates.length === 1 ? candidates[0].index : -1;
      let pending = pendingIndex >= 0
        ? pendingHttpConsoleDiagnostics.splice(pendingIndex, 1)[0]
        : null;
      if (!pending && pendingBlockedConsoleDiagnostics.length === 1) {
        pendingIndex = 0;
        pending = pendingBlockedConsoleDiagnostics.splice(pendingIndex, 1)[0];
      }
      const event = {
        context: pending?.context || "unclassified-console-error",
        diagnostic: sanitizeDiagnostic(text),
        id: `console-${++diagnosticSequence}`,
        method: pending?.method || "UNKNOWN",
        path: pending?.path || "unknown",
        status: pending?.status ?? status,
      };
      diagnostics.classifiedConsoleDiagnostics.push(event);
      if (isExpectedSilentHttpContext(event.context)) {
        if (event.context === "preauth-session-probe") {
          diagnostics.ignoredAuth401ConsoleErrors += 1;
        }
        return;
      }
      diagnostics.consoleErrors.push(diagnosticLabel(event));
    });
    page.on("pageerror", (error) => diagnostics.pageErrors.push(sanitizeDiagnostic(error.message)));
    page.on("response", (response) => {
      if (response.status() < 400) return;
      const parsed = new URL(response.url());
      const method = response.request().method();
      const context = expectedHttpDiagnosticContext({
        authState,
        authenticated: authState === "authenticated",
        method,
        path: parsed.pathname,
        status: response.status(),
      });
      pendingHttpConsoleDiagnostics.push({
        context,
        method,
        path: parsed.pathname,
        status: response.status(),
      });
      if (isExpectedSilentHttpContext(context)) {
        if (context === "preauth-session-probe") diagnostics.expectedAuth401Responses += 1;
        return;
      }
      diagnostics.httpErrors.push(`${response.status()} ${parsed.pathname}`);
    });
    page.on("requestfailed", (request) => {
      const failure = request.failure()?.errorText || "unknown failure";
      const parsed = new URL(request.url());
      if (
        request.method() === "GET"
        && failure === "net::ERR_ABORTED"
        && EXPECTED_ABORTED_REQUEST_PATHS.has(parsed.pathname)
      ) {
        diagnostics.expectedRequestAborts.push(`GET ${parsed.pathname}`);
        return;
      }
      if (!diagnostics.blockedRequests.includes(safeUrl(request.url()))) {
        diagnostics.requestFailures.push(sanitizeDiagnostic(`${safeUrl(request.url())}: ${failure}`));
      }
    });
  });
  return {
    context,
    diagnostics,
    markAuthenticated() {
      authState = "authenticated";
    },
    markSignedOut() {
      authState = "signed-out";
    },
    profile: {
      device: profileName === "mobile" || profileName === "mobile-reduced-motion"
        ? "Pixel 7"
        : (profileName === "tablet" ? "iPad (gen 7)" : "Desktop Chromium"),
      has_touch: Boolean(profile.hasTouch),
      is_mobile: Boolean(profile.isMobile),
      name: profileName,
      viewport: profile.viewport,
    },
    startTracing,
    async close(status) {
      const traceName = `${artifactLabel}-${status}.zip`;
      const tracePath = path.join(artifactDir, traceName);
      const stagingDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "gardenops-trace-"));
      fs.chmodSync(stagingDirectory, 0o700);
      const rawTrace = path.join(stagingDirectory, "trace-raw.zip");
      const sanitizedTrace = path.join(stagingDirectory, "trace-sanitized.zip");
      try {
        // A pre-authentication failure still gets a minimal trace, which is sanitized before retention.
        await startTracing();
        await context.tracing.stop({ path: rawTrace });
        execFileSync(path.join(ROOT, ".venv", "bin", "python"), [
          path.join(ROOT, "scripts", "validate_playwright_trace.py"),
          "--sanitize",
          rawTrace,
          sanitizedTrace,
        ], { stdio: "pipe" });
        execFileSync(path.join(ROOT, ".venv", "bin", "python"), [
          path.join(ROOT, "scripts", "validate_playwright_trace.py"),
          sanitizedTrace,
        ], { stdio: "pipe" });
        fs.copyFileSync(sanitizedTrace, tracePath, fs.constants.COPYFILE_EXCL);
        const traceStat = fs.lstatSync(tracePath);
        assert(traceStat.isFile() && !traceStat.isSymbolicLink() && traceStat.nlink === 1,
          "Trace output must be a regular, single-link file");
        fs.chmodSync(tracePath, 0o600);
        const sha256 = crypto.createHash("sha256").update(fs.readFileSync(tracePath)).digest("hex");
        await context.close();
        TRACE_CONTROLS.delete(context);
        return { name: traceName, sha256 };
      } finally {
        fs.rmSync(stagingDirectory, { force: true, recursive: true });
      }
    },
  };
}

function assertDiagnosticsClean(diagnostics, label) {
  assert(
    diagnostics.blockedRequests.length === 0,
    `${label} attempted non-loopback requests: ${diagnostics.blockedRequests.join(", ")}`,
  );
  assert(
    diagnostics.consoleErrors.length === 0,
    `${label} emitted console errors: ${diagnostics.consoleErrors.join(" | ")}`,
  );
  const classified = diagnostics.classifiedConsoleDiagnostics || [];
  assert(
    classified.every((entry) => (
      EXPECTED_CONSOLE_DIAGNOSTIC_CONTEXTS.has(entry?.context)
      && typeof entry?.method === "string"
      && typeof entry?.path === "string"
      && Number.isSafeInteger(entry?.status)
    )),
    `${label} retained an unclassified console diagnostic`,
  );
  assert(
    classified.filter((entry) => entry.context === "preauth-session-probe").length === 1,
    `${label} did not classify exactly one pre-auth console diagnostic`,
  );
  assert(
    diagnostics.expectedAuth401Responses === 1,
    `${label} expected one pre-auth /api/auth/me 401, got ${diagnostics.expectedAuth401Responses}`,
  );
  assert(
    diagnostics.httpErrors.length === 0,
    `${label} had unexpected HTTP errors: ${diagnostics.httpErrors.join(" | ")}`,
  );
  assert(
    diagnostics.pageErrors.length === 0,
    `${label} emitted page errors: ${diagnostics.pageErrors.join(" | ")}`,
  );
  assert(
    diagnostics.requestFailures.length === 0,
    `${label} had request failures: ${diagnostics.requestFailures.join(" | ")}`,
  );
}

async function signInThroughSessionForm(page, username, password) {
  assert(username && password, "Complete journey credentials are required");
  const gate = page.locator(".auth-gate");
  await visible(gate, "session sign-in gate");
  const form = gate.locator("#auth-gate-form");
  await form.locator("input[name='username']").fill(username);
  await form.locator("input[name='username']").press("Enter");
  const passwordInput = form.locator("input[name='password']");
  const passwordFallback = form.locator("#auth-gate-use-password");
  await visible(
    form.locator("input[name='password']:visible, #auth-gate-use-password:visible").first(),
    "session sign-in recovery control",
  );
  if (!(await passwordInput.isVisible())) {
    await visible(passwordFallback, "session sign-in password fallback");
    await passwordFallback.focus();
    await passwordFallback.press("Enter");
  }
  await visible(passwordInput, "session sign-in password field");
  await passwordInput.fill(password);
  await passwordInput.press("Enter");
  await gate.waitFor({ state: "detached", timeout: 15000 });
  const profile = await page.evaluate(async () => {
    const response = await fetch("/api/auth/me", { credentials: "include" });
    return { body: await response.json(), status: response.status };
  });
  assert(profile.status === 200, "Session profile endpoint did not return 200");
  assert(profile.body.username === username, "Session profile does not match fixture user");
  assert(profile.body.auth_type === "session", "Fixture user is not session-authenticated");
  return profile.body;
}

async function authenticate(page, username, password) {
  const profile = await signInThroughSessionForm(page, username, password);
  const traceControl = TRACE_CONTROLS.get(page.context());
  assert(traceControl, "Authenticated browser context has no guarded trace control");
  await traceControl.startTracing();
  return profile;
}

async function dismissProactivePasskeyPrompt(page, timeout = 5_000) {
  const dialog = page.locator(".modal[data-passkey-prompt-ready='true']:visible").filter({
    has: page.locator(".passkey-prompt-modal"),
  }).last();
  try {
    await dialog.waitFor({ state: "visible", timeout });
  } catch {
    return false;
  }
  const dismissed = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/auth/passkeys/prompt/dismiss"
  ));
  await dialog.locator(".confirm-no").click();
  assert((await dismissed).ok(), "Passkey prompt dismissal failed");
  await dialog.waitFor({ state: "detached" });
  return true;
}

function createApiRecorder(page, actor = {}) {
  const records = [];
  const recordsByRequest = new Map();
  const pendingResponseReads = new Set();
  let currentGardenId = Number.isSafeInteger(Number(actor.gardenId))
    && Number(actor.gardenId) > 0 ? String(Number(actor.gardenId)) : null;
  const taskActions = new Set(["complete", "reschedule", "skip", "snooze"]);
  const safeRevision = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const taskActionEvidence = (request, pathname) => {
    let body;
    try {
      body = request.postDataJSON();
    } catch {
      body = null;
    }
    if (!body || typeof body !== "object") return null;
    if (/^\/api\/tasks\/[^/]+\/action$/.test(pathname)) {
      return {
        action: taskActions.has(body.action) ? body.action : null,
        expectedUpdatedAtMs: safeRevision(body.expected_updated_at_ms),
        responseUpdatedAtMs: null,
      };
    }
    if (pathname === "/api/tasks/batch-action") {
      const taskIds = Array.isArray(body.task_ids) ? body.task_ids.map(String) : [];
      const revisions = body.expected_updated_at_ms_by_task_id;
      return {
        action: taskActions.has(body.action) ? body.action : null,
        expectedRevisions: taskIds.map((taskId) => ({
          expectedUpdatedAtMs: safeRevision(revisions?.[taskId]),
          taskId,
        })).sort((left, right) => left.taskId.localeCompare(right.taskId)),
        responseUpdatedCount: null,
      };
    }
    return null;
  };
  const attachPage = (targetPage) => {
    targetPage.on("request", (request) => {
      let parsed;
      try {
        parsed = new URL(request.url());
      } catch {
        return;
      }
      if (!parsed.pathname.startsWith("/api/")) return;
      const headers = request.headers();
      const isAnonymousAuditPath = new Set([
        "/api/auth/invitations/accept",
        "/api/auth/invitations/passkey/register/options",
        "/api/auth/invitations/passkey/register/verify",
        "/api/auth/invitations/peek",
        "/api/auth/login",
        "/api/auth/passkeys/login/options",
        "/api/auth/passkeys/login/verify",
        "/api/client-errors",
      ]).has(parsed.pathname);
      const record = {
        actorAuthType: isAnonymousAuditPath ? "none" : (actor.authType || null),
        actorRole: isAnonymousAuditPath ? "anonymous" : (actor.role || null),
        actorUsername: isAnonymousAuditPath ? "anonymous" : (actor.username || null),
        gardenId: headers["x-garden-id"] || currentGardenId,
        method: request.method(),
        operationId: headers["x-offline-operation-id"] || null,
        path: parsed.pathname,
        requestId: null,
        statusCode: null,
        taskAction: request.method() === "POST" ? taskActionEvidence(request, parsed.pathname) : null,
      };
      records.push(record);
      recordsByRequest.set(request, record);
    });
    targetPage.on("response", (response) => {
      const record = recordsByRequest.get(response.request());
      if (record) {
        record.requestId = response.headers()["x-request-id"] || null;
        record.statusCode = response.status();
        if (new Set([
          "/api/auth/invitations/accept",
          "/api/auth/invitations/passkey/register/verify",
        ]).has(record.path) && response.status() < 400) {
          const responseRead = Promise.resolve()
            .then(() => response.json())
            .then((body) => {
              const gardenId = Number(body?.garden_id);
              if (Number.isSafeInteger(gardenId) && gardenId > 0) {
                record.gardenId = String(gardenId);
                currentGardenId = String(gardenId);
              }
            })
            .catch(() => {})
            .finally(() => pendingResponseReads.delete(responseRead));
          pendingResponseReads.add(responseRead);
        }
        if (record.taskAction) {
          const responseRead = Promise.resolve()
            .then(() => response.json())
            .then((body) => {
              if (Object.hasOwn(record.taskAction, "responseUpdatedAtMs")) {
                record.taskAction.responseUpdatedAtMs = safeRevision(body?.updated_at_ms);
              } else {
                record.taskAction.responseUpdatedCount = safeRevision(body?.updated);
              }
            })
            .catch(() => {})
            .finally(() => pendingResponseReads.delete(responseRead));
          pendingResponseReads.add(responseRead);
        }
      }
    });
    targetPage.on("requestfailed", (request) => {
      const record = recordsByRequest.get(request);
      if (record) record.statusCode = null;
    });
  };
  attachPage(page);
  return {
    attachPage,
    mark: () => records.length,
    records,
    setGardenId(value) {
      const gardenId = Number(value);
      currentGardenId = Number.isSafeInteger(gardenId) && gardenId > 0
        ? String(gardenId) : null;
    },
    settle: async () => {
      while (pendingResponseReads.size > 0) {
        await Promise.all([...pendingResponseReads]);
      }
    },
    since: (mark) => records.slice(mark),
  };
}

module.exports = {
  allowedBrowserOrigins,
  assertDiagnosticsClean,
  assertBrowserProfileContract,
  assertLoopbackBaseUrl,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  dismissProactivePasskeyPrompt,
  expectedHttpDiagnosticContext,
  isAllowedUrl,
  isExpectedSilentHttpContext,
  redactTokenShapedSecrets,
  sanitizeDiagnostic,
  signInThroughSessionForm,
};
