"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { assert, visible } = require("./completeJourneyAssertions.cjs");

const NETWORK_PROTOCOLS = new Set(["http:", "https:", "ws:", "wss:"]);
const LOCAL_PROTOCOLS = new Set(["about:", "blob:", "data:"]);
const PIXEL_7_VIEWPORT = Object.freeze({ height: 839, width: 412 });
const ROUTE_GUARD_PROBE_URL = "http://192.0.2.1/api/complete-journey-route-guard";
const EXPECTED_CONSOLE_DIAGNOSTIC_CONTEXTS = new Set([
  "calendar-feed-revoked",
  "map-import-rejected",
  "network-guard-probe",
  "preauth-session-probe",
  "viewer-task-write-denied",
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

function isDisposableLoopbackHostname(hostname) {
  return hostname.replace(/^\[|\]$/g, "").toLowerCase() === "127.0.0.1";
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
  assert(isDisposableLoopbackHostname(parsed.hostname), `${label} must use 127.0.0.1`);
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

function expectedHttpDiagnosticContext({ authenticated, method, path: pathname, status }) {
  if (!authenticated && method === "GET" && pathname === "/api/auth/me" && status === 401) {
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
  return "unexpected-http-response";
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
  assert(isDisposableLoopbackHostname(parsed.hostname), "BASE_URL must use literal 127.0.0.1");
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
  assert(profileName === "desktop", `Unknown complete journey profile contract: ${profileName}`);
  assert(runtime?.is_mobile === false, "Desktop runtime unexpectedly reports mobile mode");
  assert(runtime?.has_touch === false && runtime?.max_touch_points === 0,
    "Desktop runtime unexpectedly exposes touch input");
  assert(viewport.width === 1440 && viewport.height === 900,
    "Desktop runtime viewport was unexpected");
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
  };
  const pendingHttpConsoleDiagnostics = [];
  const pendingBlockedConsoleDiagnostics = [];
  let diagnosticSequence = 0;
  let authenticated = false;
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
      if (event.context === "preauth-session-probe") {
        diagnostics.ignoredAuth401ConsoleErrors += 1;
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
        authenticated,
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
      if (context === "preauth-session-probe") {
        diagnostics.expectedAuth401Responses += 1;
        return;
      }
      diagnostics.httpErrors.push(`${response.status()} ${parsed.pathname}`);
    });
    page.on("requestfailed", (request) => {
      const failure = request.failure()?.errorText || "unknown failure";
      if (!diagnostics.blockedRequests.includes(safeUrl(request.url()))) {
        diagnostics.requestFailures.push(sanitizeDiagnostic(`${safeUrl(request.url())}: ${failure}`));
      }
    });
  });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
  return {
    context,
    diagnostics,
    markAuthenticated() {
      authenticated = true;
    },
    profile: {
      device: profileName === "mobile" ? "Pixel 7" : "Desktop Chromium",
      has_touch: profileName === "mobile",
      is_mobile: profileName === "mobile",
      name: profileName,
      viewport: profile.viewport,
    },
    async close(status) {
      const traceName = `${artifactLabel}-${status}.zip`;
      const tracePath = path.join(artifactDir, traceName);
      const stagingDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "gardenops-trace-"));
      fs.chmodSync(stagingDirectory, 0o700);
      const stagedTrace = path.join(stagingDirectory, "trace.zip");
      try {
        await context.tracing.stop({ path: stagedTrace });
        fs.copyFileSync(stagedTrace, tracePath, fs.constants.COPYFILE_EXCL);
        const traceStat = fs.lstatSync(tracePath);
        assert(traceStat.isFile() && !traceStat.isSymbolicLink() && traceStat.nlink === 1,
          "Trace output must be a regular, single-link file");
        fs.chmodSync(tracePath, 0o600);
        const sha256 = crypto.createHash("sha256").update(fs.readFileSync(tracePath)).digest("hex");
        await context.close();
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

async function authenticate(page, username, password) {
  assert(username && password, "Complete journey credentials are required");
  const gate = page.locator(".auth-gate");
  await visible(gate, "session sign-in gate");
  const form = gate.locator("#auth-gate-form");
  await form.locator("input[name='username']").fill(username);
  await form.locator("button[type='submit']").click();
  const passwordInput = form.locator("input[name='password']");
  await visible(passwordInput, "session sign-in password field");
  await passwordInput.fill(password);
  await form.locator("button[type='submit']").click();
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

function createApiRecorder(page, actor = {}) {
  const records = [];
  const recordsByRequest = new Map();
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
      const isLogin = parsed.pathname === "/api/auth/login";
      const record = {
        actorAuthType: isLogin ? "none" : (actor.authType || null),
        actorRole: isLogin ? "anonymous" : (actor.role || null),
        actorUsername: isLogin ? "anonymous" : (actor.username || null),
        gardenId: headers["x-garden-id"] || null,
        method: request.method(),
        operationId: headers["x-offline-operation-id"] || null,
        path: parsed.pathname,
        requestId: null,
        statusCode: null,
      };
      records.push(record);
      recordsByRequest.set(request, record);
    });
    targetPage.on("response", (response) => {
      const record = recordsByRequest.get(response.request());
      if (record) {
        record.requestId = response.headers()["x-request-id"] || null;
        record.statusCode = response.status();
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
  expectedHttpDiagnosticContext,
  isAllowedUrl,
  redactTokenShapedSecrets,
  sanitizeDiagnostic,
};
