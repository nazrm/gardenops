#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5182";
const ROOT_DIR = path.resolve(__dirname, "..");
const ARTIFACT_DIR_INPUT = process.env.GARDENOPS_UI_FLOW_E2E_ARTIFACT_DIR;
const CHROMIUM_EXECUTABLE = process.env.CHROMIUM_EXECUTABLE
  || (fs.existsSync("/usr/bin/chromium-browser")
    ? "/usr/bin/chromium-browser"
    : "/usr/bin/chromium");
const E2E_USERNAME = process.env.GARDENOPS_UI_FLOW_E2E_USERNAME;
const E2E_PASSWORD = process.env.GARDENOPS_UI_FLOW_E2E_PASSWORD;
const E2E_EDITOR_USERNAME = process.env.GARDENOPS_UI_FLOW_E2E_EDITOR_USERNAME;
const E2E_EDITOR_PASSWORD = process.env.GARDENOPS_UI_FLOW_E2E_EDITOR_PASSWORD;
const E2E_VIEWER_USERNAME = process.env.GARDENOPS_UI_FLOW_E2E_VIEWER_USERNAME;
const E2E_VIEWER_PASSWORD = process.env.GARDENOPS_UI_FLOW_E2E_VIEWER_PASSWORD;
const VIEWPORT_FILTER = process.env.GARDENOPS_UI_FLOW_E2E_VIEWPORT || "all";
const ALLOW_UNLABELED_CONTROLS =
  process.env.GARDENOPS_UI_FLOW_E2E_ALLOW_UNLABELED === "1";
const SCREENSHOT_CAPTURE_ATTEMPTS = 3;
const SCREENSHOT_MAX_BLACK_PIXEL_RATIO = 0.03;

let ARTIFACT_DIR;
let SCREENSHOT_DIR;
let TRACE_DIR;
let chromium;
let devices;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const BROWSER_NETWORK_PROTOCOLS = new Set(["http:", "https:", "ws:", "wss:"]);
const LOCAL_NON_NETWORK_PROTOCOLS = new Set(["about:", "blob:", "data:"]);

function isLoopbackHostname(hostname) {
  const normalizedHost = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (normalizedHost === "::1") return true;
  const octets = normalizedHost.split(".");
  return octets.length === 4
    && octets.every((octet) => /^\d+$/.test(octet) && Number(octet) <= 255)
    && Number(octets[0]) === 127;
}

function isAllowedBrowserRequestUrl(requestUrl) {
  let parsed;
  try {
    parsed = new URL(requestUrl);
  } catch {
    return false;
  }
  if (BROWSER_NETWORK_PROTOCOLS.has(parsed.protocol)) {
    return isLoopbackHostname(parsed.hostname);
  }
  return LOCAL_NON_NETWORK_PROTOCOLS.has(parsed.protocol);
}

function describeBrowserRequestUrl(requestUrl) {
  try {
    const parsed = new URL(requestUrl);
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}`;
  } catch {
    return "invalid request URL";
  }
}

function assertLoopbackBaseUrl(baseUrl) {
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new Error("UI-flow E2E BASE_URL must be an absolute loopback URL");
  }
  assert(
    ["http:", "https:"].includes(parsed.protocol),
    "UI-flow E2E BASE_URL must use HTTP(S)",
  );
  assert(
    isLoopbackHostname(parsed.hostname),
    "UI-flow E2E BASE_URL must use a literal loopback host",
  );
  assert(
    !parsed.username && !parsed.password && !parsed.search && !parsed.hash,
    "UI-flow E2E BASE_URL must not include credentials, query, or fragment",
  );
}

async function createLoopbackRequestGuard(browserContext) {
  const nonLoopbackRequests = [];
  await browserContext.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    if (!isAllowedBrowserRequestUrl(requestUrl)) {
      nonLoopbackRequests.push(describeBrowserRequestUrl(requestUrl));
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  await browserContext.routeWebSocket(
    (url) => !isAllowedBrowserRequestUrl(url.href),
    (socket) => {
      nonLoopbackRequests.push(describeBrowserRequestUrl(socket.url()));
      socket.close({ code: 1008, reason: "Non-loopback E2E traffic is blocked" });
    },
  );
  browserContext.on("request", (request) => {
    const requestUrl = request.url();
    if (!isAllowedBrowserRequestUrl(requestUrl)) {
      nonLoopbackRequests.push(describeBrowserRequestUrl(requestUrl));
    }
  });
  return {
    nonLoopbackRequests,
    assertClean(label) {
      assert(
        nonLoopbackRequests.length === 0,
        `${label} made non-loopback requests:\n${nonLoopbackRequests.join("\n")}`,
      );
    },
  };
}

function isStrictDescendant(parentPath, childPath) {
  const relativePath = path.relative(parentPath, childPath);
  return relativePath !== ""
    && relativePath !== ".."
    && !relativePath.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relativePath);
}

function assertNoTraversalSegments(inputPath) {
  const segments = inputPath.split(/[\\/]+/);
  assert(
    !segments.includes(".."),
    "UI-flow E2E artifact directory must not contain '..' path traversal",
  );
}

function assertGitIgnoredResearchDirectory(rootDir, researchDir) {
  let researchStatus;
  try {
    researchStatus = fs.lstatSync(researchDir);
  } catch (error) {
    throw new Error(
      `UI-flow E2E artifact directory requires research/: ${error.message}`,
    );
  }
  assert(
    researchStatus.isDirectory() && !researchStatus.isSymbolicLink(),
    "UI-flow E2E artifact directory requires a non-symlink research/ directory",
  );
  try {
    execFileSync("git", ["check-ignore", "-q", "--", "research"], {
      cwd: rootDir,
      stdio: "ignore",
    });
  } catch {
    throw new Error(
      "UI-flow E2E artifact directory requires the repository research/ directory to be gitignored",
    );
  }
}

function resolveWithExistingAncestor(targetPath) {
  const missingSegments = [];
  let currentPath = targetPath;
  while (true) {
    try {
      return path.join(fs.realpathSync.native(currentPath), ...missingSegments);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const parentPath = path.dirname(currentPath);
      assert(
        parentPath !== currentPath,
        `Could not resolve UI-flow E2E artifact directory: ${targetPath}`,
      );
      missingSegments.unshift(path.basename(currentPath));
      currentPath = parentPath;
    }
  }
}

function assertNoSymlinkComponents(parentPath, childPath) {
  let currentPath = parentPath;
  for (const segment of path.relative(parentPath, childPath).split(path.sep)) {
    if (!segment) continue;
    currentPath = path.join(currentPath, segment);
    let status;
    try {
      status = fs.lstatSync(currentPath);
    } catch (error) {
      if (error?.code === "ENOENT") return;
      throw error;
    }
    assert(
      !status.isSymbolicLink(),
      `UI-flow E2E artifact directory must not traverse symlink: ${currentPath}`,
    );
  }
}

function resolveArtifactDirectory(artifactDirInput = ARTIFACT_DIR_INPUT, rootDir = ROOT_DIR) {
  const resolvedRootDir = fs.realpathSync.native(rootDir);
  const researchDir = path.join(resolvedRootDir, "research");
  assertGitIgnoredResearchDirectory(resolvedRootDir, researchDir);
  const requestedPath = artifactDirInput
    || path.join(researchDir, "optimization-map");
  assert(
    typeof requestedPath === "string" && requestedPath.trim(),
    "UI-flow E2E artifact directory must be a non-empty path",
  );
  assertNoTraversalSegments(requestedPath);
  const candidatePath = path.resolve(resolvedRootDir, requestedPath);
  assert(
    isStrictDescendant(researchDir, candidatePath),
    "UI-flow E2E artifact directory must be a nested directory under research/",
  );
  assertNoSymlinkComponents(researchDir, candidatePath);
  const resolvedArtifactDir = resolveWithExistingAncestor(candidatePath);
  assert(
    isStrictDescendant(researchDir, resolvedArtifactDir),
    "Resolved UI-flow E2E artifact directory escapes research/",
  );
  return resolvedArtifactDir;
}

function assertSafeArtifactChild(artifactDir, childName) {
  const childPath = path.join(artifactDir, childName);
  assertNoSymlinkComponents(artifactDir, childPath);
  const resolvedChildPath = resolveWithExistingAncestor(childPath);
  assert(
    isStrictDescendant(artifactDir, resolvedChildPath),
    `UI-flow E2E artifact ${childName} directory escapes its artifact root`,
  );
  return childPath;
}

function prepareArtifactDirectories() {
  fs.mkdirSync(resolveArtifactDirectory(), { recursive: true });
  ARTIFACT_DIR = resolveArtifactDirectory();
  SCREENSHOT_DIR = assertSafeArtifactChild(ARTIFACT_DIR, "screenshots");
  TRACE_DIR = assertSafeArtifactChild(ARTIFACT_DIR, "traces");
  fs.rmSync(SCREENSHOT_DIR, { force: true, recursive: true });
  fs.rmSync(TRACE_DIR, { force: true, recursive: true });
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  fs.mkdirSync(TRACE_DIR, { recursive: true });
}

function gitOutput(args) {
  try {
    return execFileSync("git", args, {
      cwd: ROOT_DIR,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return null;
  }
}

function sourceHash(relativePath) {
  const source = fs.readFileSync(path.join(ROOT_DIR, relativePath));
  return crypto.createHash("sha256").update(source).digest("hex");
}

function assertNoResponseMocks() {
  const source = fs.readFileSync(__filename, "utf8");
  const fulfillNeedle = [".", "fulfill("].join("");
  assert(!source.includes(fulfillNeedle), "UI-flow E2E must not fulfill product responses");
}

async function visible(locator, label, timeout = 15000) {
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch (err) {
    throw new Error(`Expected visible ${label}: ${err.message}`);
  }
}

async function settle(page) {
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  await page.waitForTimeout(550);
}

async function captureViewportScreenshot(page) {
  return page.screenshot({
    animations: "disabled",
    captureBeyondViewport: false,
    fullPage: false,
    type: "png",
  });
}

async function captureSurfaceScreenshot(root) {
  return root.screenshot({
    animations: "disabled",
    type: "png",
  });
}

async function screenshotPixelHealth(page, screenshot) {
  return page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.ceil(image.naturalWidth / 8));
    canvas.height = Math.max(1, Math.ceil(image.naturalHeight / 8));
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("Could not inspect screenshot pixels");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let blackPixels = 0;
    let transparentPixels = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index + 3] < 250) {
        transparentPixels += 1;
        continue;
      }
      if (
        pixels[index] <= 2
        && pixels[index + 1] <= 2
        && pixels[index + 2] <= 2
      ) {
        blackPixels += 1;
      }
    }
    const pixelCount = pixels.length / 4;
    const blackPixelRatio = blackPixels / pixelCount;
    const transparentPixelRatio = transparentPixels / pixelCount;
    return {
      blackPixelRatio,
      invalidPixelRatio: blackPixelRatio + transparentPixelRatio,
      transparentPixelRatio,
    };
  }, screenshot.toString("base64"));
}

async function captureReliableScreenshot(page, capture, outputPath, label) {
  let best = null;
  for (let attempt = 1; attempt <= SCREENSHOT_CAPTURE_ATTEMPTS; attempt += 1) {
    const screenshot = await capture();
    const pixelHealth = await screenshotPixelHealth(page, screenshot);
    if (!best || pixelHealth.invalidPixelRatio < best.invalidPixelRatio) {
      best = { attempt, screenshot, ...pixelHealth };
    }
    if (pixelHealth.invalidPixelRatio <= SCREENSHOT_MAX_BLACK_PIXEL_RATIO) break;
    await page.waitForTimeout(250);
  }
  assert(best, `Could not capture ${label}`);
  fs.writeFileSync(outputPath, best.screenshot);
  assert(
    best.invalidPixelRatio <= SCREENSHOT_MAX_BLACK_PIXEL_RATIO,
    `${label} screenshot has ${(best.invalidPixelRatio * 100).toFixed(1)}% black/transparent pixels`,
  );
  return {
    attempts: best.attempt,
    blackPixelRatio: Number(best.blackPixelRatio.toFixed(4)),
    invalidPixelRatio: Number(best.invalidPixelRatio.toFixed(4)),
    transparentPixelRatio: Number(best.transparentPixelRatio.toFixed(4)),
  };
}

async function inspectSurface(page, rootSelector) {
  return page.evaluate((selector) => {
    const root = document.querySelector(selector);
    const duplicateIds = [];
    const seenIds = new Set();
    for (const element of document.querySelectorAll("[id]")) {
      if (seenIds.has(element.id)) duplicateIds.push(element.id);
      seenIds.add(element.id);
    }
    const visibleInteractive = Array.from(
      (root || document).querySelectorAll("button,input,select,textarea,a[href]"),
    ).filter((element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0
        && rect.height > 0
        && style.display !== "none"
        && style.visibility !== "hidden";
    });
    const unlabeledElements = visibleInteractive.filter((element) => {
      if (element instanceof HTMLInputElement && element.type === "hidden") return false;
      const text = (element.textContent || "").trim();
      const labelledBy = (element.getAttribute("aria-labelledby") || "")
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean)
        .join(" ");
      const explicitLabel = element.id
        ? document.querySelector(`label[for='${CSS.escape(element.id)}']`)
        : null;
      const labelText = (
        explicitLabel?.textContent
        || element.closest("label")?.textContent
        || ""
      ).trim();
      const name = element.getAttribute("aria-label")
        || element.getAttribute("title")
        || labelledBy
        || labelText
        || text;
      return name.trim().length === 0;
    });
    const unlabeledControlSamples = unlabeledElements.slice(0, 12).map((element) => ({
      className: element.className || null,
      id: element.id || null,
      placeholder: element.getAttribute("placeholder"),
      tag: element.tagName.toLowerCase(),
      type: element.getAttribute("type"),
    }));
    const headings = Array.from(
      (root || document).querySelectorAll("h1,h2,h3,[role='heading']"),
    ).filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }).map((element) => (element.textContent || "").trim()).filter(Boolean).slice(0, 8);
    const rect = root instanceof HTMLElement ? root.getBoundingClientRect() : null;
    return {
      duplicateIds: Array.from(new Set(duplicateIds)),
      headings,
      horizontalOverflowPx: Math.max(0, document.documentElement.scrollWidth - innerWidth),
      rootBox: rect ? {
        height: Math.round(rect.height),
        width: Math.round(rect.width),
        x: Math.round(rect.x),
        y: Math.round(rect.y),
      } : null,
      rootHidden: root instanceof HTMLElement ? root.hidden : null,
      unlabeledControlSamples,
      unlabeledControls: unlabeledElements.length,
      viewport: { height: innerHeight, width: innerWidth },
      visibleInteractiveCount: visibleInteractive.length,
    };
  }, rootSelector);
}

async function captureStep(page, manifest, options) {
  const { id, label, rootSelector, viewportName } = options;
  const root = page.locator(rootSelector);
  await visible(root, `${viewportName} ${label}`);
  await settle(page);
  const inspection = await inspectSurface(page, rootSelector);
  assert(inspection.rootBox?.width > 0, `${label} has no rendered width`);
  assert(inspection.rootBox?.height > 0, `${label} has no rendered height`);
  assert(inspection.rootHidden === false, `${label} root is hidden`);
  assert(inspection.duplicateIds.length === 0, `${label} has duplicate IDs: ${inspection.duplicateIds.join(", ")}`);
  assert(
    ALLOW_UNLABELED_CONTROLS || inspection.unlabeledControls === 0,
    `${label} has unlabeled controls: ${JSON.stringify(inspection.unlabeledControlSamples)}`,
  );
  for (const expectedText of options.expectedText || []) {
    await visible(
      root.getByText(expectedText, { exact: false }).filter({ visible: true }).first(),
      `${viewportName} ${label} seeded text "${expectedText}"`,
    );
  }
  const outputDir = path.join(SCREENSHOT_DIR, viewportName);
  fs.mkdirSync(outputDir, { recursive: true });
  const screenshotPath = path.join(outputDir, `${id}.png`);
  const screenshotHealth = await captureReliableScreenshot(
    page,
    () => captureViewportScreenshot(page),
    screenshotPath,
    `${viewportName} ${label}`,
  );
  const size = fs.statSync(screenshotPath).size;
  assert(size >= 5000, `${label} screenshot appears blank (${size} bytes)`);
  let surfaceScreenshot = null;
  let surfaceScreenshotHealth = null;
  if (inspection.rootBox.height > inspection.viewport.height) {
    const surfacePath = path.join(outputDir, `${id}-surface.png`);
    surfaceScreenshotHealth = await captureReliableScreenshot(
      page,
      () => captureSurfaceScreenshot(root),
      surfacePath,
      `${viewportName} ${label} complete surface`,
    );
    assert(
      fs.statSync(surfacePath).size >= 5000,
      `${label} complete-surface screenshot appears blank`,
    );
    surfaceScreenshot = path.relative(ROOT_DIR, surfacePath);
  }
  manifest.steps.push({
    health: "captured",
    id,
    inspection,
    label,
    screenshot: path.relative(ROOT_DIR, screenshotPath),
    screenshotHealth,
    surfaceScreenshot,
    surfaceScreenshotHealth,
    viewport: viewportName,
  });
}

async function clickAndWait(page, selector, rootSelector, label) {
  const control = page.locator(selector).filter({ visible: true }).first();
  await visible(control, `${label} control`);
  const startedAt = Date.now();
  await control.click();
  await visible(page.locator(rootSelector), label);
  await settle(page);
  return Date.now() - startedAt;
}

async function authenticate(page, manifest, viewportName, credentials = null) {
  const username = credentials?.username || E2E_USERNAME;
  const passwordValue = credentials?.password || E2E_PASSWORD; // push-sanitizer: allow SECRET_ASSIGNMENT - runtime-only disposable fixture
  assert(username, "UI-flow E2E username is required");
  assert(passwordValue, "UI-flow E2E password is required");
  const gate = page.locator(".auth-gate");
  await visible(gate, `${viewportName} sign-in gate`);
  if (manifest) {
    await captureStep(page, manifest, {
      expectedText: ["Username", "Enter"],
      id: "sign-in",
      label: "Sign in",
      rootSelector: ".auth-gate",
      viewportName,
    });
  }
  const form = gate.locator("#auth-gate-form");
  await form.locator("input[name='username']").fill(username);
  await form.locator("button[type='submit']").click();
  const password = form.locator("input[name='password']");
  await visible(password, `${viewportName} password field`);
  await password.fill(passwordValue);
  await form.locator("button[type='submit']").click();
  await gate.waitFor({ state: "detached", timeout: 15000 });
}

async function openPrimaryTab(page, tab, mobile) {
  if (tab === "admin" && mobile) {
    await page.locator("#mobile-utility-btn").click();
    await visible(page.locator("#mobile-utility-sheet"), "mobile utility sheet");
    await page.locator("#mobile-admin-btn").click();
    await visible(page.locator("#admin-view"), "Admin view");
    return;
  }
  const selector = mobile ? `#mobile-tab-${tab}` : `#top-tab-${tab}`;
  await page.locator(selector).click();
  const selectedSelector = mobile ? selector : `#top-tab-${tab}[aria-selected='true']`;
  await visible(page.locator(selectedSelector), `${tab} selected tab`);
}

async function openSubMode(page, mode, rootSelector) {
  const button = page.locator(`[data-sub-mode='${mode}']`).filter({ visible: true }).first();
  await visible(button, `${mode} submode`);
  await button.click();
  await visible(page.locator(rootSelector), `${mode} surface`);
  await settle(page);
}

async function assertLastControlReachablePastFab(page, rootSelector, label) {
  const fab = page.locator("#mobile-fab");
  if (!await fab.isVisible()) return;
  const controls = page.locator(rootSelector)
    .locator("button,input,select,textarea,a[href]")
    .filter({ visible: true });
  if (await controls.count() === 0) return;
  const control = controls.last();
  await control.evaluate((element) => element.scrollIntoView({ block: "center" }));
  await settle(page);
  const [controlBox, fabBox] = await Promise.all([control.boundingBox(), fab.boundingBox()]);
  assert(controlBox && fabBox, `${label} reachability boxes are unavailable`);
  const overlaps = !(
    controlBox.x + controlBox.width <= fabBox.x
    || fabBox.x + fabBox.width <= controlBox.x
    || controlBox.y + controlBox.height <= fabBox.y
    || fabBox.y + fabBox.height <= controlBox.y
  );
  assert(!overlaps, `${label} final control remains blocked by the mobile FAB`);
}

async function captureSubModes(page, manifest, viewportName, modes) {
  for (const mode of modes) {
    await openSubMode(page, mode.id, mode.rootSelector);
    await captureStep(page, manifest, {
      expectedText: mode.expectedText,
      id: mode.id,
      label: mode.label,
      rootSelector: mode.rootSelector,
      viewportName,
    });
    if (mode.verifyFabReachability) {
      await assertLastControlReachablePastFab(
        page,
        mode.rootSelector,
        `${viewportName} ${mode.label}`,
      );
    }
  }
}

async function captureNotificationPanel(page, manifest, viewportName, mobile) {
  let trigger;
  if (mobile) {
    await page.locator("#mobile-utility-btn").click();
    await visible(page.locator("#mobile-utility-sheet"), "mobile utility sheet for notifications");
    trigger = page.locator("#mobile-notification-btn");
  } else {
    trigger = page.locator("#notification-bell");
  }
  await trigger.click();
  await captureStep(page, manifest, {
    expectedText: ["Inbox", "Log", "No notifications"],
    id: "notifications",
    label: "Notification inbox",
    rootSelector: "#notification-panel",
    viewportName,
  });
  const notificationTabs = page.locator("#notification-panel .notification-panel-tab");
  assert(await notificationTabs.count() === 2, "Notification panel must expose Inbox and Log");
  await notificationTabs.nth(1).click();
  await settle(page);
  await captureStep(page, manifest, {
    expectedText: ["Heavy rain may cover watering", "covered by the forecast"],
    id: "notification-log",
    label: "Notification log",
    rootSelector: "#notification-panel",
    viewportName,
  });
  if (mobile) {
    assert(
      !await page.locator("#mobile-utility-sheet").evaluate(
        (element) => element.classList.contains("mobile-utility-sheet--open"),
      ),
      "Opening mobile notifications must close the utility sheet",
    );
    await page.locator("#map-view").click({ position: { x: 12, y: 12 } });
  } else {
    await trigger.click();
  }
  await page.locator("#notification-panel").waitFor({ state: "hidden" });
}

async function captureMapOverlays(page, manifest, viewportName, mobile) {
  await openPrimaryTab(page, "map", mobile);
  if (mobile) {
    assert(
      await page.locator("#mobile-fab").evaluate(
        (element) => element.classList.contains("mobile-fab--map-active"),
      ),
      "Mobile FAB must use its map-scoped position on Map",
    );
  }
  await captureStep(page, manifest, {
    expectedText: mobile
      ? ["Seedling greenhouse"]
      : ["Seedling greenhouse", "Water tomato bed"],
    id: "map",
    label: "Map overview",
    rootSelector: "#map-view",
    viewportName,
  });
  if (mobile) {
    const todayHandle = page.locator("#attention-today-mobile-handle");
    await visible(todayHandle, "mobile Today handle");
    await todayHandle.click();
    await captureStep(page, manifest, {
      expectedText: ["Today", "Water tomato bed", "Heavy rain expected", "Greenhouse check-in"],
      id: "map-today",
      label: "Map Today sheet",
      rootSelector: "#attention-today-mobile-sheet",
      viewportName,
    });
    await page.locator("[data-testid='attention-today-mobile-close']").click();
    await page.locator("#mobile-fab").click();
    await captureStep(page, manifest, {
      expectedText: ["Complete task", "Log entry", "Report issue", "Log harvest", "Snooze task", "Identify plant"],
      id: "quick-actions",
      label: "Quick actions",
      rootSelector: "#mobile-quick-actions",
      viewportName,
    });
    await page.keyboard.press("Escape");
    return;
  }
  await captureStep(page, manifest, {
    expectedText: ["Today", "Water tomato bed", "Heavy rain expected", "Greenhouse check-in"],
    id: "map-today",
    label: "Map Today panel",
    rootSelector: "#attention-today-panel",
    viewportName,
  });
}

async function captureStatisticsModes(page, manifest, viewportName) {
  const modes = [
    ["today", "Statistics Today", "#today-dashboard", ["Today"]],
    ["overview", "Statistics overview", "#statistics-content", ["Garden Statistics"]],
    ["reports", "Reports", "#reports-dashboard", ["Reports"]],
    ["planner", "Planner", "#planner-dashboard", ["Planting Planner"]],
  ];
  for (const [mode, label, rootSelector, expectedText] of modes) {
    await clickAndWait(page, `[data-stats-mode='${mode}']`, rootSelector, label);
    await captureStep(page, manifest, {
      expectedText,
      id: `statistics-${mode}`,
      label,
      rootSelector,
      viewportName,
    });
  }
}

async function captureAdminSections(page, manifest, viewportName, mobile) {
  await openPrimaryTab(page, "admin", mobile);
  await visible(page.locator("#admin-view .adm-layout"), "Admin console");
  if (mobile) {
    assert(
      !await page.locator("#mobile-fab").isVisible(),
      "Mobile Quick Actions FAB must not obscure the Admin console",
    );
  }
  const seededAdminSections = [
    ["settings", ["My Settings"]],
    ["garden", ["Garden"]],
    ["invitations", ["Invitations"]],
    ["users", ["Users", "3 registered accounts"]],
    ["sessions", ["Sessions"]],
    ["audit", ["Audit"]],
    ["system", ["System"]],
  ];
  for (const [section, expectedText] of seededAdminSections) {
    const button = page.locator(`.adm-nav-btn[data-section='${section}']`);
    assert(
      await button.count() === 1,
      `Expected exactly one seeded-admin navigation button for ${section}`,
    );
    await visible(button, `${section} seeded-admin navigation button`);
    assert(
      await button.isEnabled(),
      `${section} seeded-admin navigation button is disabled`,
    );
    await button.click();
    await visible(
      page.locator(`.adm-nav-btn.adm-nav-btn--active[data-section='${section}']`),
      `${section} active seeded-admin navigation button`,
    );
    await visible(page.locator("#adm-main"), `${section} admin section`);
    await settle(page);
    await captureStep(page, manifest, {
      expectedText,
      id: `admin-${section}`,
      label: `Admin ${section}`,
      rootSelector: "#admin-view",
      viewportName,
    });
  }
}

async function runViewport(browser, manifest, options) {
  const { mobile, name, viewport } = options;
  const { defaultBrowserType: _defaultBrowserType, ...mobileDevice } = devices["Pixel 7"];
  const contextOptions = mobile
    ? { ...mobileDevice, viewport }
    : { viewport };
  const context = await browser.newContext(contextOptions);
  manifest.contexts[name] = {
    deviceScaleFactor: contextOptions.deviceScaleFactor || 1,
    hasTouch: Boolean(contextOptions.hasTouch),
    isMobile: Boolean(contextOptions.isMobile),
    userAgent: contextOptions.userAgent || null,
    viewport,
  };
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  const page = await context.newPage();
  const loopbackRequestGuard = await createLoopbackRequestGuard(context);
  const browserErrors = [];
  let authenticationPending = true;
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && !text.startsWith("Failed to load resource:")) {
      browserErrors.push(`console: ${text}`);
    }
  });
  page.on("pageerror", (error) => browserErrors.push(`pageerror: ${error.stack || error.message}`));
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/") && response.status() >= 400) {
      if (
        authenticationPending
        && response.status() === 401
        && url.pathname === "/api/auth/me"
      ) return;
      browserErrors.push(`api: ${response.status()} ${url.pathname}`);
    }
  });

  try {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await authenticate(page, manifest, name);
    authenticationPending = false;
    await visible(page.locator("#map-view"), `${name} initial Map`);
    await captureMapOverlays(page, manifest, name, mobile);
    await captureNotificationPanel(page, manifest, name, mobile);

    await openPrimaryTab(page, "garden", mobile);
    if (mobile) {
      assert(
        !await page.locator("#mobile-fab").evaluate(
          (element) => element.classList.contains("mobile-fab--map-active"),
        ),
        "Mobile FAB must leave its map-scoped state away from Map",
      );
    }
    await captureSubModes(page, manifest, name, [
      { expectedText: ["Balcony Tomato", "Butterhead Lettuce", "Genovese Basil"], id: "plants", label: "Garden Plants", rootSelector: "#plants-tab-content" },
      { expectedText: ["Tomato seed packet", "2 packets"], id: "inventory", label: "Garden Inventory", rootSelector: "#inventory-tab-content" },
      { expectedText: ["Genovese Basil", "Greenhouse shelf"], id: "indoor", label: "Indoor plants", rootSelector: "#indoor-tab-content" },
      { expectedText: ["Compost refill", "Oslo Soil Co"], id: "procurement", label: "Garden Procurement", rootSelector: "#procurement-tab-content" },
    ]);

    await openPrimaryTab(page, "activity", mobile);
    await captureSubModes(page, manifest, name, [
      { expectedText: ["Water tomato bed", "Balcony Tomato"], id: "tasks", label: "Activity Tasks", rootSelector: "#tasks-tab-content", verifyFabReachability: mobile },
      { expectedText: ["Garden Calendar", "Greenhouse check-in"], id: "calendar", label: "Activity Calendar", rootSelector: "#calendar-tab-content" },
      { expectedText: ["Tomato truss observation"], id: "journal", label: "Activity Journal", rootSelector: "#journal-tab-content" },
      { expectedText: ["Aphids on tomato tips"], id: "issues", label: "Activity Issues", rootSelector: "#issues-tab-content" },
      { expectedText: ["0.65", "First tomato harvest"], id: "harvest", label: "Activity Harvest", rootSelector: "#harvest-tab-content" },
    ]);

    await openPrimaryTab(page, "insights", mobile);
    await captureSubModes(page, manifest, name, [
      { expectedText: ["Heavy rain:", "18mm"], id: "care", label: "Insights Care", rootSelector: "#care-view" },
      { expectedText: ["Statistics"], id: "statistics", label: "Insights Statistics", rootSelector: "#statistics-view" },
    ]);
    await captureStatisticsModes(page, manifest, name);
    await openSubMode(page, "analysis", "#analysis-view");
    await captureStep(page, manifest, {
      expectedText: ["Garden Analysis", "Bloom window check", "Empty plot plan"],
      id: "analysis",
      label: "Insights Analysis",
      rootSelector: "#analysis-view",
      viewportName: name,
    });

    await captureAdminSections(page, manifest, name, mobile);
    loopbackRequestGuard.assertClean(`${name} browser`);
    assert(browserErrors.length === 0, `${name} browser/API errors:\n${browserErrors.join("\n")}`);
  } finally {
    manifest.browserErrors[name] = browserErrors;
    manifest.nonLoopbackRequests[name] = loopbackRequestGuard.nonLoopbackRequests;
    const tracePath = path.join(TRACE_DIR, `ui-flow-${name}.zip`);
    await context.tracing.stop({ path: tracePath });
    fs.chmodSync(tracePath, 0o600);
    await context.close();
  }
}

async function runRoleAccessCheck(browser, manifest, options) {
  const { mobile = false, password, role, username, writeAccess } = options;
  const checkName = `${role}-${mobile ? "mobile" : "desktop"}`;
  assert(username, `${role} UI-flow E2E username is required`);
  assert(password, `${role} UI-flow E2E password is required`);
  const viewport = mobile ? { height: 844, width: 390 } : { height: 800, width: 1280 };
  const { defaultBrowserType: _defaultBrowserType, ...mobileDevice } = devices["Pixel 7"];
  const context = await browser.newContext(
    mobile ? { ...mobileDevice, viewport } : { viewport },
  );
  const page = await context.newPage();
  const loopbackRequestGuard = await createLoopbackRequestGuard(context);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await authenticate(page, null, `${checkName} role`, { password, username });
    await visible(page.locator("#map-view"), `${checkName} Map`);
    const profile = await page.evaluate(async () => {
      const response = await fetch("/api/auth/me");
      return { body: await response.json(), status: response.status };
    });
    assert(profile.status === 200, `${role} /api/auth/me returned ${profile.status}`);
    assert(profile.body.username === username, `${role} profile username mismatch`);
    assert(profile.body.role === role, `${role} profile role mismatch`);
    assert(profile.body.garden_role === role, `${role} garden role mismatch`);
    assert(profile.body.write_access === writeAccess, `${role} write access mismatch`);

    await openPrimaryTab(page, "garden", mobile);
    await openSubMode(page, "plants", "#plants-tab-content");
    await visible(page.getByText("Balcony Tomato", { exact: true }).first(), `${role} plant data`);
    const addPlantVisible = await page.locator("#add-plant-btn").isVisible();
    const addPlantEnabled = await page.locator("#add-plant-btn").isEnabled();
    const importPlantsEnabled = await page.locator("#import-csv-btn").isEnabled();
    const mobileFabEnabled = await page.locator("#mobile-fab").isEnabled();
    assert(addPlantVisible, `${role} Add plant control was not rendered`);
    assert(
      addPlantEnabled === writeAccess,
      `${role} Add plant enabled state did not match write access`,
    );
    assert(
      importPlantsEnabled === writeAccess,
      `${role} Import plants enabled state did not match write access`,
    );
    assert(
      mobileFabEnabled === writeAccess,
      `${role} mobile quick actions enabled state did not match write access`,
    );
    const plantWriteControls = await page.locator(
      "#plants-tab-content [data-edit-plt], #plants-tab-content .col-select input",
    ).count();
    assert(
      (plantWriteControls > 0) === writeAccess,
      `${role} plant row write controls did not match write access`,
    );

    await openSubMode(page, "indoor", "#indoor-tab-content");
    await visible(
      page.locator("#indoor-tab-content").getByText("Genovese Basil", { exact: true }).first(),
      `${role} Indoor data`,
    );
    const indoorWriteControls = await page.locator(
      "#indoor-tab-content button[data-edit],"
      + "#indoor-tab-content button[data-remove],"
      + "#indoor-tab-content .indoor-room-row button,"
      + "#indoor-tab-content > button.btn-primary",
    ).count();
    assert(
      (indoorWriteControls > 0) === writeAccess,
      `${role} Indoor write controls did not match write access`,
    );

    const adminUsersAccess = await page.evaluate(async () => {
      const response = await fetch("/api/auth/users");
      return response.status;
    });
    assert(adminUsersAccess === 403, `${role} admin user API returned ${adminUsersAccess}`);

    const adminEntry = page.locator(mobile ? "#mobile-admin-btn" : "#top-tab-admin");
    const adminEntryVisible = await adminEntry.isVisible();
    let privilegedSections = 0;
    if (adminEntryVisible) {
      await openPrimaryTab(page, "admin", false);
      await visible(page.locator("#admin-view"), `${role} settings view`);
      privilegedSections = await page.locator(
        ".adm-nav-btn[data-section='users'],"
        + ".adm-nav-btn[data-section='sessions'],"
        + ".adm-nav-btn[data-section='audit'],"
        + ".adm-nav-btn[data-section='system']",
      ).count();
    } else {
      assert(await page.locator("#admin-view").isHidden(), `${role} hidden settings view was exposed`);
    }
    assert(privilegedSections === 0, `${role} exposed ${privilegedSections} admin-only sections`);
    loopbackRequestGuard.assertClean(`${checkName} browser`);
    assert(errors.length === 0, `${role} browser errors: ${errors.join("; ")}`);
    manifest.roleChecks.push({
      addPlantEnabled,
      addPlantVisible,
      adminEntryVisible,
      adminUsersAccess,
      gardenRole: profile.body.garden_role,
      importPlantsEnabled,
      indoorWriteControls,
      mobileFabEnabled,
      plantWriteControls,
      privilegedSections,
      role,
      status: "passed",
      username,
      viewport: mobile ? "mobile" : "desktop",
      writeAccess: profile.body.write_access,
    });
  } finally {
    manifest.nonLoopbackRequests[checkName] = loopbackRequestGuard.nonLoopbackRequests;
    await context.close();
  }
}

async function main() {
  assertNoResponseMocks();
  assertLoopbackBaseUrl(BASE_URL);
  assert(
    ["all", "desktop", "mobile"].includes(VIEWPORT_FILTER),
    `Invalid GARDENOPS_UI_FLOW_E2E_VIEWPORT: ${VIEWPORT_FILTER}`,
  );
  prepareArtifactDirectories();
  ({ chromium, devices } = require("../frontend/node_modules/playwright-core"));
  const manifest = {
    baseUrl: BASE_URL,
    browser: {},
    browserErrors: {},
    contexts: {},
    nonLoopbackRequests: {},
    createdAt: new Date().toISOString(),
    provenance: {
      argv: process.argv.slice(2),
      gitDirty: Boolean(gitOutput(["status", "--short"])),
      gitRevision: gitOutput(["rev-parse", "HEAD"]),
      sourceHashes: Object.fromEntries([
        "scripts/check_ui_flow_map_e2e.cjs",
        "scripts/run_ui_flow_map_e2e.sh",
        "scripts/seed_ui_flow_map_e2e.py",
      ].map((relativePath) => [relativePath, sourceHash(relativePath)])),
    },
    roleChecks: [],
    runId: crypto.randomUUID(),
    status: "running",
    steps: [],
  };
  const manifestPath = path.join(TRACE_DIR, "ui-flow-manifest.json");
  const browser = await chromium.launch({
    executablePath: CHROMIUM_EXECUTABLE,
    headless: true,
  });
  manifest.browser = {
    executablePath: CHROMIUM_EXECUTABLE,
    version: await browser.version(),
  };
  try {
    if (VIEWPORT_FILTER !== "mobile") {
      await runViewport(browser, manifest, {
        mobile: false,
        name: "desktop",
        viewport: { width: 1440, height: 960 },
      });
    }
    if (VIEWPORT_FILTER !== "desktop") {
      await runViewport(browser, manifest, {
        mobile: true,
        name: "mobile",
        viewport: { width: 390, height: 844 },
      });
    }
    if (VIEWPORT_FILTER === "all") {
      for (const mobile of [false, true]) {
        await runRoleAccessCheck(browser, manifest, {
          mobile,
          password: E2E_EDITOR_PASSWORD,
          role: "editor",
          username: E2E_EDITOR_USERNAME,
          writeAccess: true,
        });
        await runRoleAccessCheck(browser, manifest, {
          mobile,
          password: E2E_VIEWER_PASSWORD,
          role: "viewer",
          username: E2E_VIEWER_USERNAME,
          writeAccess: false,
        });
      }
    }
    manifest.completedAt = new Date().toISOString();
    manifest.status = "passed";
  } catch (error) {
    manifest.completedAt = new Date().toISOString();
    manifest.failure = error.stack || error.message;
    manifest.status = "failed";
    throw error;
  } finally {
    fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
    fs.chmodSync(manifestPath, 0o600);
    await browser.close();
  }
  console.log(`UI-flow map E2E passed: ${manifest.steps.length} captured surfaces`);
  console.log(`Manifest: ${manifestPath}`);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error.message);
    process.exitCode = 1;
  });
}

module.exports = {
  assertLoopbackBaseUrl,
  isAllowedBrowserRequestUrl,
  resolveArtifactDirectory,
};
