"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { assert, visible } = require("./completeJourneyAssertions.cjs");

const NETWORK_PROTOCOLS = new Set(["http:", "https:", "ws:", "wss:"]);
const LOCAL_PROTOCOLS = new Set(["about:", "blob:", "data:"]);

function sanitizeDiagnostic(value) {
  return String(value) ? "[redacted diagnostic; inspect private runner logs]" : "";
}

function isLoopbackHostname(hostname) {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (normalized === "::1") return true;
  const octets = normalized.split(".");
  return octets.length === 4
    && octets.every((octet) => /^\d+$/.test(octet) && Number(octet) <= 255)
    && Number(octets[0]) === 127;
}

function isAllowedUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false;
  }
  if (NETWORK_PROTOCOLS.has(parsed.protocol)) return isLoopbackHostname(parsed.hostname);
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

function assertLoopbackBaseUrl(baseUrl) {
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new Error("Complete journey BASE_URL must be an absolute URL");
  }
  assert(["http:", "https:"].includes(parsed.protocol), "BASE_URL must use HTTP(S)");
  assert(isLoopbackHostname(parsed.hostname), "BASE_URL must use a literal loopback host");
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

async function createGuardedContext(browser, devices, profileName, artifactDir) {
  const profile = browserProfile(devices, profileName);
  const context = await browser.newContext({
    ...profile,
    locale: "en-US",
    timezoneId: "UTC",
  });
  const diagnostics = {
    blockedRequests: [],
    consoleErrors: [],
    expectedAuth401Responses: 0,
    httpErrors: [],
    ignoredAuth401ConsoleErrors: 0,
    pageErrors: [],
    requestFailures: [],
  };
  let authenticated = false;
  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    if (!isAllowedUrl(requestUrl)) {
      diagnostics.blockedRequests.push(safeUrl(requestUrl));
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  if (typeof context.routeWebSocket === "function") {
    await context.routeWebSocket(
      (url) => !isAllowedUrl(url.href),
      (socket) => {
        diagnostics.blockedRequests.push(safeUrl(socket.url()));
        socket.close({ code: 1008, reason: "Non-loopback E2E traffic is blocked" });
      },
    );
  }
  context.on("page", (page) => {
    page.on("console", (message) => {
      if (message.type() !== "error") return;
      const text = message.text();
      if (
        text.startsWith("Failed to load resource:")
        && text.includes("401 (Unauthorized)")
        && diagnostics.expectedAuth401Responses > diagnostics.ignoredAuth401ConsoleErrors
      ) {
        diagnostics.ignoredAuth401ConsoleErrors += 1;
        return;
      }
      diagnostics.consoleErrors.push(sanitizeDiagnostic(text));
    });
    page.on("pageerror", (error) => diagnostics.pageErrors.push(sanitizeDiagnostic(error.message)));
    page.on("response", (response) => {
      if (response.status() < 400) return;
      const parsed = new URL(response.url());
      if (!authenticated && response.status() === 401 && parsed.pathname === "/api/auth/me") {
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
      has_touch: profileName === "mobile",
      is_mobile: profileName === "mobile",
      name: profileName,
      viewport: profile.viewport,
    },
    async close(status) {
      const traceName = `${profileName}-${status}.zip`;
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
        await context.close();
      } finally {
        fs.rmSync(stagingDirectory, { force: true, recursive: true });
      }
      return traceName;
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

function createApiRecorder(page) {
  const records = [];
  page.on("request", (request) => {
    let parsed;
    try {
      parsed = new URL(request.url());
    } catch {
      return;
    }
    if (!parsed.pathname.startsWith("/api/")) return;
    const headers = request.headers();
    records.push({
      gardenId: headers["x-garden-id"] || null,
      method: request.method(),
      path: parsed.pathname,
    });
  });
  return { mark: () => records.length, records, since: (mark) => records.slice(mark) };
}

module.exports = {
  assertDiagnosticsClean,
  assertLoopbackBaseUrl,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  isAllowedUrl,
  sanitizeDiagnostic,
};
