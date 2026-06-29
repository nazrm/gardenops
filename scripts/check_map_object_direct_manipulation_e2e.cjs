#!/usr/bin/env node

const { chromium } = require("../frontend/node_modules/playwright-core");

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5173";
const CHROMIUM_EXECUTABLE = process.env.CHROMIUM_EXECUTABLE || "/usr/bin/chromium-browser";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function sameGeometry(actual, expected) {
  return actual
    && actual.x === expected.x
    && actual.y === expected.y
    && actual.width === expected.width
    && actual.height === expected.height;
}

async function waitForPatchCount(patches, count, label) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (patches.length >= count) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Expected ${label} to PATCH object geometry; saw ${patches.length}/${count}`);
}

function makePlot(row, col) {
  return {
    plot_id: `P${row}${col}`,
    zone_code: "B",
    zone_name: "Beds",
    plot_number: row * 10 + col,
    grid_row: row,
    grid_col: col,
    sub_zone: "",
    notes: "",
    color: null,
    plant_count: 0,
    has_tree: false,
    has_bush: false,
    categories: [],
  };
}

function makeAuthProfile() {
  return {
    username: "e2e_admin",
    role: "admin",
    garden_id: 1,
    garden_visible: true,
    garden_role: "admin",
    auth_type: "session",
    write_access: true,
    language: "en",
    shademap_available: false,
    mfa_enabled: false,
    mfa_setup_required: false,
    mfa_authenticated: true,
    mfa_methods: [],
    must_change_password: false,
    passkeys_enabled: false,
    passkey_enrolled: true,
    passkey_count: 1,
    password_auth_disabled: false,
    passkey_prompt_eligible: false,
    passkey_prompt_dismissed_until_ms: 0,
    plot_assignment_meanings: [],
    subscription_tier: "pro",
    allowed_features: [
      "map",
      "plots",
      "plants",
      "journal",
      "media",
      "snapshots",
      "exports_basic",
      "tasks",
      "issues",
      "weather",
      "notifications",
      "shade_map",
      "planner",
      "saved_views",
      "statistics",
      "inventory",
      "care",
      "calendar",
      "exports_full",
      "multi_garden",
      "user_management",
      "mfa",
      "procurement",
      "workflows",
      "ai",
      "audit",
      "admin_panel",
    ],
    security_warnings: [],
  };
}

async function fulfillJson(route, body, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function centerOf(locator) {
  const deadline = Date.now() + 5000;
  let box = null;
  while (Date.now() < deadline) {
    box = await locator.boundingBox();
    if (box && box.width > 0 && box.height > 0) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert(box && box.width > 0 && box.height > 0, `Missing bounding box for ${locator}`);
  return {
    x: box.x + box.width / 2,
    y: box.y + box.height / 2,
    width: box.width,
    height: box.height,
  };
}

async function dispatchPointer(page, locator, deltaX, deltaY, pointerType = "touch") {
  const start = await centerOf(locator);
  await locator.dispatchEvent("pointerdown", {
    bubbles: true,
    cancelable: true,
    button: 0,
    buttons: 1,
    pointerId: 41,
    pointerType,
    clientX: start.x,
    clientY: start.y,
  });
  await page.evaluate(({ x, y }) => {
    window.dispatchEvent(new PointerEvent("pointermove", {
      bubbles: true,
      cancelable: true,
      button: 0,
      buttons: 1,
      pointerId: 41,
      pointerType: "touch",
      clientX: x,
      clientY: y,
    }));
  }, { x: start.x + deltaX, y: start.y + deltaY });
  await page.evaluate(({ x, y }) => {
    window.dispatchEvent(new PointerEvent("pointerup", {
      bubbles: true,
      cancelable: true,
      button: 0,
      buttons: 0,
      pointerId: 41,
      pointerType: "touch",
      clientX: x,
      clientY: y,
    }));
  }, { x: start.x + deltaX, y: start.y + deltaY });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROMIUM_EXECUTABLE,
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });

  const patches = [];
  const plots = [];
  for (let row = 1; row <= 8; row += 1) {
    for (let col = 1; col <= 8; col += 1) {
      plots.push(makePlot(row, col));
    }
  }
  const mapObject = {
    public_id: "obj-e2e-patio",
    object_type: "patio",
    name: "E2E Patio",
    shape_type: "rectangle",
    geometry: { x: 2, y: 2, width: 3, height: 2 },
    style: { color: "#8f9f7d" },
    z_index: 5,
    has_internal_layout: true,
    internal_layout: { rows: 2, cols: 3 },
    units: [],
  };

  page.on("console", (msg) => {
    if (["error", "warning"].includes(msg.type())) {
      console.log(`[browser ${msg.type()}] ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    console.log(`[browser pageerror] ${err.message}`);
    if (err.stack) console.log(err.stack);
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === "GET" && path === "/api/auth/me") {
      return fulfillJson(route, makeAuthProfile());
    }
    if (method === "GET" && path === "/api/auth/status") {
      return fulfillJson(route, {
        bootstrap_required: false,
        passkeys_enabled: false,
        password_policy: {},
      });
    }
    if (method === "GET" && path === "/api/version") {
      return fulfillJson(route, { version: "e2e", git_commit: "e2e", dirty: false });
    }
    if (method === "GET" && path === "/api/gardens") {
      return fulfillJson(route, [{
        id: 1,
        slug: "e2e",
        name: "E2E Garden",
        role: "admin",
        active: true,
        onboarding_complete: true,
        owned_by_current_user: true,
      }]);
    }
    if (method === "GET" && path === "/api/gardens/1/settings") {
      return fulfillJson(route, {
        garden_id: 1,
        name: "E2E Garden",
        grid_rows: 8,
        grid_cols: 8,
        latitude: null,
        longitude: null,
        address: "",
        onboarding_complete: true,
      });
    }
    if (method === "GET" && path === "/api/gardens/1/lidar") {
      return fulfillJson(route, {
        garden_id: 1,
        available: false,
        uploaded: false,
        filename: null,
        uploaded_at_ms: null,
      });
    }
    if (method === "GET" && path === "/api/gardens/1/memberships") {
      return fulfillJson(route, { memberships: [] });
    }
    if (method === "GET" && path === "/api/media/plants/missing-covers") {
      return fulfillJson(route, { items: [], total: 0 });
    }
    if (method === "GET" && path === "/api/plots") {
      return fulfillJson(route, plots);
    }
    if (method === "GET" && path === "/api/layout-state") {
      return fulfillJson(route, {
        row: 1,
        col: 1,
        width: 2,
        height: 2,
        north_degrees: 0,
        grid_rows: 8,
        grid_cols: 8,
      });
    }
    if (method === "GET" && path === "/api/plots/elevations") {
      return fulfillJson(route, {
        available: false,
        elevations: {},
        overrides: {},
        min_m: null,
        max_m: null,
      });
    }
    if (method === "GET" && path === "/api/gardens/1/map-objects") {
      return fulfillJson(route, { objects: [mapObject] });
    }
    if (method === "PATCH" && path === "/api/gardens/1/map-objects/obj-e2e-patio") {
      const body = request.postDataJSON();
      if (body.geometry) {
        mapObject.geometry = { ...body.geometry };
        patches.push({ ...body.geometry });
      }
      return fulfillJson(route, mapObject);
    }
    if (path === "/api/auth/me/settings") {
      return fulfillJson(route, {
        language: "en",
        email_notifications_enabled: false,
        notification_preferences: {},
        mfa: { pending_enrollment: false },
      });
    }
    if (method === "GET" && path === "/api/plants") {
      return fulfillJson(route, []);
    }
    if (path === "/api/auth/user-invitations") {
      return fulfillJson(route, { invitations: [] });
    }
    if (path === "/api/dashboard/badge-counts") {
      return fulfillJson(route, { tasks: 0, notifications: 0, issues: 0 });
    }
    if (path === "/api/auth/emergency-read-only") {
      return fulfillJson(route, { enabled: false, reason: "", expires_at_ms: null });
    }
    if (path === "/api/admin/system/health") {
      return fulfillJson(route, { status: "ok", db_quick_check: "ok" });
    }
    if (path === "/api/admin/provider-settings") {
      return fulfillJson(route, { providers: [] });
    }
    if (path === "/api/auth/mfa") {
      return fulfillJson(route, { enabled: false, methods: [], recovery_codes_remaining: 0 });
    }
    if (path === "/api/auth/users") {
      return fulfillJson(route, { users: [] });
    }
    if (path === "/api/auth/sessions") {
      return fulfillJson(route, { sessions: [] });
    }
    if (path === "/api/auth/audit-events") {
      return fulfillJson(route, { events: [], next_cursor: null });
    }
    if (path === "/api/auth/security-metrics") {
      return fulfillJson(route, { windows: [] });
    }
    if (path === "/api/auth/security-alerts") {
      return fulfillJson(route, { alerts: [] });
    }

    return fulfillJson(route, {});
  });

  await page.addInitScript(() => {
    localStorage.setItem("gardenops-tab", "map");
    localStorage.setItem("gardenops-sub-mode", "plants");
  });

  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await page.locator("#map-grid").waitFor({ state: "visible", timeout: 15000 });
  await page.locator(".map-object-label", { hasText: "E2E Patio" }).waitFor({ state: "visible" });

  const editButton = page.locator("#edit-mode-btn");
  if (await editButton.count()) {
    await editButton.click();
  } else {
    await page.locator("#top-tab-admin").click();
    try {
      await page.locator("#adm-map-open-editor-btn").waitFor({ state: "visible", timeout: 15000 });
    } catch (err) {
      const adminText = await page.locator("#admin-view").evaluate((el) => el.textContent || "").catch(() => "");
      const appText = await page.locator("#app").evaluate((el) => el.textContent || "").catch(() => "");
      throw new Error(
        `Admin map editor button did not render. Admin text: ${adminText.slice(0, 600)} App text: ${appText.slice(0, 600)} Original: ${err.message}`,
      );
    }
    await page.locator("#adm-map-open-editor-btn").click();
  }

  await page.locator(".map-object-label", { hasText: "E2E Patio" }).click();
  const surface = page.locator(".map-object-interaction-surface[data-object-id='obj-e2e-patio']");
  await surface.waitFor({ state: "visible", timeout: 15000 });
  await page.locator(".map-object-resize-handle[data-handle='se']").waitFor({ state: "visible" });

  const gridBox = await page.locator("#map-grid").boundingBox();
  assert(gridBox, "Missing map grid bounding box");
  const cellW = gridBox.width / 8;
  const cellH = gridBox.height / 8;

  await dispatchPointer(page, surface, cellW, cellH, "touch");
  await waitForPatchCount(patches, 1, "touch move");
  assert(
    sameGeometry(patches.at(-1), { x: 3, y: 3, width: 3, height: 2 }),
    `Unexpected move geometry: ${JSON.stringify(patches.at(-1))}`,
  );

  const handle = page.locator(".map-object-resize-handle[data-handle='se']");
  await dispatchPointer(page, handle, cellW, cellH, "touch");
  await waitForPatchCount(patches, 2, "resize");
  assert(
    sameGeometry(patches.at(-1), { x: 3, y: 3, width: 4, height: 3 }),
    `Unexpected resize geometry: ${JSON.stringify(patches.at(-1))}`,
  );

  await surface.focus();
  await page.keyboard.press("ArrowRight");
  await waitForPatchCount(patches, 3, "keyboard move");
  assert(
    sameGeometry(patches.at(-1), { x: 4, y: 3, width: 4, height: 3 }),
    `Unexpected keyboard move geometry: ${JSON.stringify(patches.at(-1))}`,
  );

  await page.keyboard.press("Shift+ArrowDown");
  await waitForPatchCount(patches, 4, "keyboard resize");
  assert(
    sameGeometry(patches.at(-1), { x: 4, y: 3, width: 4, height: 4 }),
    `Unexpected keyboard resize geometry: ${JSON.stringify(patches.at(-1))}`,
  );
  await surface.waitFor({ state: "visible", timeout: 5000 });

  const patchCountBeforeCancel = patches.length;
  await surface.dispatchEvent("pointerdown", {
    bubbles: true,
    cancelable: true,
    button: 0,
    buttons: 1,
    pointerId: 42,
    pointerType: "touch",
    clientX: (await centerOf(surface)).x,
    clientY: (await centerOf(surface)).y,
  });
  await page.evaluate(() => {
    if (typeof Touch === "undefined" || typeof TouchEvent === "undefined") return;
    const first = new Touch({ identifier: 1, target: document.body, clientX: 10, clientY: 10 });
    const second = new Touch({ identifier: 2, target: document.body, clientX: 30, clientY: 30 });
    window.dispatchEvent(new TouchEvent("touchstart", {
      bubbles: true,
      cancelable: true,
      touches: [first, second],
      targetTouches: [first, second],
      changedTouches: [second],
    }));
  });
  await page.evaluate(() => {
    window.dispatchEvent(new PointerEvent("pointerup", {
      bubbles: true,
      cancelable: true,
      button: 0,
      buttons: 0,
      pointerId: 42,
      pointerType: "touch",
      clientX: 1000,
      clientY: 1000,
    }));
  });
  assert(
    patches.length === patchCountBeforeCancel,
    "Expected two-finger touchstart to cancel active object manipulation without PATCH",
  );

  await browser.close();
  console.log("Map object direct manipulation e2e passed.");
}

main().catch(async (err) => {
  console.error(err);
  process.exit(1);
});
