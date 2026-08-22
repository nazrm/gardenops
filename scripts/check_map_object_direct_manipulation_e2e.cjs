#!/usr/bin/env node

const { chromium } = require("../frontend/node_modules/playwright-core");

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5173";
const CHROMIUM_EXECUTABLE = process.env.CHROMIUM_EXECUTABLE || "/usr/bin/chromium-browser";
const OBJECT_ID = "obj-e2e-patio";
const GRID_SIZE = 8;
const CORNER_HANDLES = ["nw", "ne", "se", "sw"];
const BLOCKER_PLOT_ID = "P46";

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

async function waitForNoPatch(patches, count, label) {
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert(
    patches.length === count,
    `Expected ${label} to send no PATCH; saw ${patches.length - count} unexpected PATCH(es)`,
  );
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
    can_assign: true,
    has_tree: false,
    has_bush: false,
    categories: [],
    plot_kind: "ground",
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

async function dispatchPointerUp(page, point, pointerType, pointerId) {
  await page.evaluate(({ x, y, pointerType: type, pointerId: id }) => {
    window.dispatchEvent(new PointerEvent("pointerup", {
      bubbles: true,
      cancelable: true,
      button: 0,
      buttons: 0,
      pointerId: id,
      pointerType: type,
      clientX: x,
      clientY: y,
    }));
  }, { ...point, pointerType, pointerId });
}

async function dispatchPointer(
  page,
  locator,
  deltaX,
  deltaY,
  pointerType = "touch",
  release = true,
  pointerId = 41,
) {
  const start = await centerOf(locator);
  const end = { x: start.x + deltaX, y: start.y + deltaY };
  await locator.dispatchEvent("pointerdown", {
    bubbles: true,
    cancelable: true,
    button: 0,
    buttons: 1,
    pointerId,
    pointerType,
    clientX: start.x,
    clientY: start.y,
  });
  await page.evaluate(({ x, y, pointerType: type, pointerId: id }) => {
    window.dispatchEvent(new PointerEvent("pointermove", {
      bubbles: true,
      cancelable: true,
      button: 0,
      buttons: 1,
      pointerId: id,
      pointerType: type,
      clientX: x,
      clientY: y,
    }));
  }, { ...end, pointerType, pointerId });
  if (release) await dispatchPointerUp(page, end, pointerType, pointerId);
  return end;
}

async function waitForDimensions(page, expected, label) {
  const deadline = Date.now() + 5000;
  let actual = null;
  while (Date.now() < deadline) {
    const disclosure = page.locator("details.map-object-layout-disclosure");
    if (await disclosure.count()) {
      const inputs = disclosure.locator(
        ".map-object-position-grid input[type='number']",
      );
      if (await inputs.count() === 4) {
        const values = await inputs.evaluateAll((items) => items.map((item) => Number(item.value)));
        actual = {
          y: values[0],
          x: values[1],
          width: values[2],
          height: values[3],
        };
        if (sameGeometry(actual, expected)) return;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(
    `Expected ${label} dimensions ${JSON.stringify(expected)}; saw ${JSON.stringify(actual)}`,
  );
}

async function waitForControlPlacement(page, expected, label) {
  const row = `${expected.y} / ${expected.y + expected.height}`;
  const column = `${expected.x} / ${expected.x + expected.width}`;
  try {
    await page.waitForFunction(({ objectId, expectedRow, expectedColumn }) => {
      const controls = document.querySelector(
        `.map-object-direct-controls[data-object-id='${objectId}']`,
      );
      return controls?.style.gridRow === expectedRow && controls?.style.gridColumn === expectedColumn;
    }, { objectId: OBJECT_ID, expectedRow: row, expectedColumn: column }, { timeout: 5000 });
  } catch (err) {
    throw new Error(`${label}: ${err.message}`);
  }
}

async function waitForPreview(page, visible) {
  await page.waitForFunction(({ objectId, expectedVisible }) => {
    const preview = document.querySelector(
      `.map-object-preview[data-object-id='${objectId}']`,
    );
    return Boolean(preview) && preview.hidden === !expectedVisible;
  }, { objectId: OBJECT_ID, expectedVisible: visible }, { timeout: 5000 });
}

async function waitForPreviewDimensions(page, expected, label) {
  try {
    await page.waitForFunction(({ objectId, width, height }) => {
      const preview = document.querySelector(
        `.map-object-preview[data-object-id='${objectId}']`,
      );
      const dimensions = preview?.querySelector(".map-object-preview-dimensions")?.textContent || "";
      return Boolean(preview) && !preview.hidden
        && dimensions.includes(String(width))
        && dimensions.includes(String(height));
    }, { objectId: OBJECT_ID, width: expected.width, height: expected.height }, { timeout: 5000 });
  } catch (err) {
    throw new Error(`${label}: ${err.message}`);
  }
}

async function readPreviewState(page) {
  return page.locator(
    `.map-object-direct-controls[data-object-id='${OBJECT_ID}']`,
  ).evaluate((controls) => {
    const nodes = [controls, ...controls.querySelectorAll("*")];
    const marker = nodes.map((node) => {
      const className = typeof node.className === "string" ? node.className : "";
      const attributes = Array.from(node.attributes, (attribute) => `${attribute.name}=${attribute.value}`);
      return [className, node.textContent || "", ...attributes].join(" ");
    }).join(" ").toLowerCase();
    const preview = controls.querySelector(".map-object-preview");
    return { visible: Boolean(preview) && !preview.hidden, marker };
  });
}

async function assertInvalidPreview(page) {
  await waitForPreview(page, true);
  const state = await readPreviewState(page);
  assert(state.visible, "Invalid map-object drop did not show a preview");
  assert(
    state.marker.includes("map-object-preview--invalid"),
    `Invalid map-object preview did not expose invalid state: ${state.marker}`,
  );
  assert(
    await page.locator(`.plot.map-object-conflict[data-plot-id='${BLOCKER_PLOT_ID}']`).count() === 1,
    "Invalid map-object preview did not mark the blocking plot",
  );
}

async function assertUsableTouchTarget(locator, label) {
  const metrics = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const before = getComputedStyle(element, "::before");
    const px = (value) => Number.parseFloat(value) || 0;
    const extraWidth = Math.max(0, -px(before.left)) + Math.max(0, -px(before.right));
    const extraHeight = Math.max(0, -px(before.top)) + Math.max(0, -px(before.bottom));
    return {
      width: rect.width + extraWidth,
      height: rect.height + extraHeight,
    };
  });
  assert(
    metrics.width >= 44 && metrics.height >= 44,
    `${label} touch target is too small: ${JSON.stringify(metrics)}`,
  );
}

async function dispatchTwoFingerTouchStart(page) {
  const supported = await page.evaluate(
    () => typeof Touch !== "undefined" && typeof TouchEvent !== "undefined",
  );
  assert(supported, "Browser does not expose TouchEvent for two-finger cancel coverage");
  await page.evaluate(() => {
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
}

function attachDiagnostics(page) {
  page.on("console", (msg) => {
    if (["error", "warning"].includes(msg.type())) {
      console.log(`[browser ${msg.type()}] ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    console.log(`[browser pageerror] ${err.message}`);
    if (err.stack) console.log(err.stack);
  });
}

async function openMapPage(page) {
  await page.addInitScript(() => {
    localStorage.setItem("gardenops-tab", "map");
    localStorage.setItem("gardenops-sub-mode", "plants");
  });

  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await page.locator("#map-grid").waitFor({ state: "visible", timeout: 15000 });
  const label = page.locator(".map-object-label", { hasText: "E2E Patio" });
  await label.waitFor({ state: "visible" });

  const editButton = page.locator("#edit-mode-btn");
  if (await editButton.count()) {
    const mobileLayersButton = page.locator("#mobile-map-layers-btn");
    if (await mobileLayersButton.isVisible()) {
      await mobileLayersButton.click();
      await editButton.click();
      await page.locator("#mobile-map-layers-close-btn").click();
    } else {
      await editButton.click();
    }
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

  await label.click();
  const surface = page.locator(
    `.map-object-interaction-surface[data-object-id='${OBJECT_ID}']`,
  );
  await surface.waitFor({ state: "visible", timeout: 15000 });
  return surface;
}

async function main() {
  const browser = await chromium.launch({
    executablePath: CHROMIUM_EXECUTABLE,
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });

  const patches = [];
  const movedPlotRequests = [];
  const containerPositionPatches = [];
  const plots = [makePlot(4, 6), makePlot(8, 1)];
  const mapObject = {
    public_id: OBJECT_ID,
    object_type: "patio",
    name: "E2E Patio",
    shape_type: "rectangle",
    geometry: { x: 2, y: 2, width: 2, height: 2 },
    style: { color: "#8f9f7d" },
    z_index: 5,
    container_count: 0,
    plant_count: 0,
    containers: [],
  };

  attachDiagnostics(page);

  const apiRoute = async (route) => {
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
        row: 8,
        col: 8,
        width: 1,
        height: 1,
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
      return fulfillJson(route, { objects: [mapObject], containers: [] });
    }
    if (
      method === "POST"
      && path === `/api/gardens/1/map-objects/${OBJECT_ID}/containers/from-plots`
    ) {
      const body = request.postDataJSON();
      movedPlotRequests.push(body);
      for (const plotId of body.plot_ids) {
        const plotIndex = plots.findIndex((plot) => plot.plot_id === plotId);
        assert(plotIndex >= 0, `Move request referenced missing plot ${plotId}`);
        Object.assign(plots[plotIndex], {
          plot_kind: "container",
          display_name: plotId,
          container_type: body.container_type,
          parent_map_object_public_id: OBJECT_ID,
          grid_row: null,
          grid_col: null,
        });
        mapObject.containers.push({
          plot_id: plotId,
          display_name: plotId,
          container_type: body.container_type,
          environment: "outdoor",
          plant_count: 0,
          parent_map_object_public_id: OBJECT_ID,
          position_x: mapObject.containers.length,
          position_y: 0,
        });
      }
      mapObject.container_count = mapObject.containers.length;
      return fulfillJson(route, mapObject);
    }
    if (method === "PATCH" && path === "/api/gardens/1/containers/P81") {
      const body = request.postDataJSON();
      const container = mapObject.containers.find((item) => item.plot_id === "P81");
      assert(container, "Container position PATCH referenced missing P81");
      container.position_x = body.position_x;
      container.position_y = body.position_y;
      containerPositionPatches.push(body);
      return fulfillJson(route, container);
    }
    if (method === "PATCH" && path === `/api/gardens/1/map-objects/${OBJECT_ID}`) {
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
    if (method === "GET" && path === "/api/plots/P81/plants") {
      return fulfillJson(route, []);
    }
    if (method === "GET" && path === "/api/tasks") {
      return fulfillJson(route, { tasks: [], total: 0 });
    }
    if (method === "GET" && path === "/api/journal") {
      return fulfillJson(route, { entries: [], total: 0 });
    }
    if (method === "GET" && path === "/api/media") {
      return fulfillJson(route, { items: [], total: 0 });
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
  };
  await page.route("**/api/**", apiRoute);
  const surface = await openMapPage(page);

  const initialGeometry = { x: 2, y: 2, width: 2, height: 2 };
  const afterResize = { x: 3, y: 2, width: 3, height: 3 };
  const afterKeyboardMove = { x: 3, y: 1, width: 3, height: 3 };
  const afterKeyboardResize = { x: 3, y: 1, width: 3, height: 4 };

  await waitForDimensions(page, initialGeometry, "initial patio");
  const gridBox = await page.locator("#map-grid").boundingBox();
  assert(gridBox, "Missing desktop map grid bounding box");
  const cellW = gridBox.width / GRID_SIZE;
  const cellH = gridBox.height / GRID_SIZE;

  await dispatchPointer(page, surface, cellW, 0, "mouse");
  await waitForPatchCount(patches, 1, "desktop body move");
  assert(
    sameGeometry(patches.at(-1), { x: 3, y: 2, width: 2, height: 2 }),
    `Unexpected desktop move geometry: ${JSON.stringify(patches.at(-1))}`,
  );
  await waitForDimensions(page, { x: 3, y: 2, width: 2, height: 2 }, "moved patio");

  const handle = page.locator(".map-object-resize-handle[data-handle='se']");
  const resizeEnd = await dispatchPointer(page, handle, cellW, cellH, "mouse", false, 44);
  await waitForPreviewDimensions(page, { width: 3, height: 3 }, "desktop resize preview");
  await dispatchPointerUp(page, resizeEnd, "mouse", 44);
  await waitForPatchCount(patches, 2, "desktop resize");
  assert(
    sameGeometry(patches.at(-1), afterResize),
    `Unexpected desktop resize geometry: ${JSON.stringify(patches.at(-1))}`,
  );
  await waitForDimensions(page, afterResize, "resized patio");

  const invalidStart = { ...afterResize };
  const invalidEnd = await dispatchPointer(
    page,
    surface,
    cellW * 2,
    cellH * 2,
    "mouse",
    false,
    43,
  );
  await assertInvalidPreview(page);
  await waitForPreviewDimensions(page, { width: 3, height: 3 }, "invalid desktop preview");
  assert(patches.length === 2, "Invalid desktop drop sent a PATCH during preview");
  await dispatchPointerUp(page, invalidEnd, "mouse", 43);
  await waitForNoPatch(patches, 2, "invalid desktop drop");
  await waitForControlPlacement(page, invalidStart, "invalid desktop drop");
  await waitForPreview(page, false);
  await waitForDimensions(page, invalidStart, "restored patio after invalid drop");

  const surfaceSelector = `.map-object-interaction-surface[data-object-id='${OBJECT_ID}']`;
  await page.locator(surfaceSelector).focus();
  await page.keyboard.press("ArrowUp");
  await waitForPatchCount(patches, 3, "keyboard move");
  assert(
    sameGeometry(patches.at(-1), afterKeyboardMove),
    `Unexpected keyboard move geometry: ${JSON.stringify(patches.at(-1))}`,
  );
  await waitForDimensions(page, afterKeyboardMove, "keyboard-moved patio");

  await page.locator(surfaceSelector).focus();
  await page.keyboard.press("Shift+ArrowDown");
  await waitForPatchCount(patches, 4, "keyboard resize");
  assert(
    sameGeometry(patches.at(-1), afterKeyboardResize),
    `Unexpected keyboard resize geometry: ${JSON.stringify(patches.at(-1))}`,
  );
  await waitForDimensions(page, afterKeyboardResize, "keyboard-resized patio");

  await page.locator(surfaceSelector).focus();
  await page.keyboard.press("ArrowRight");
  await waitForNoPatch(patches, 4, "invalid keyboard move");
  await waitForDimensions(page, afterKeyboardResize, "keyboard collision restore");

  await page.locator(".plot[data-plot-id='P81']").click();
  const moveDisclosure = page.locator(".map-object-move-disclosure");
  await moveDisclosure.locator("summary").click();
  await moveDisclosure.locator("select").selectOption("planter");
  await moveDisclosure.locator("button[type='submit']").click();
  await page.waitForFunction(() => !document.querySelector(".plot[data-plot-id='P81']"));
  assert(movedPlotRequests.length === 1, "Expected one selected-plot move request");
  assert(
    JSON.stringify(movedPlotRequests[0]) === JSON.stringify({
      plot_ids: ["P81"],
      container_type: "planter",
    }),
    `Unexpected selected-plot move body: ${JSON.stringify(movedPlotRequests[0])}`,
  );
  await page.locator(".map-container-row-main", { hasText: "P81" }).waitFor({ state: "visible" });
  const containerMarker = page.locator(".map-container-marker", { hasText: "P81" });
  await containerMarker.waitFor({ state: "visible" });
  const containerBox = await containerMarker.boundingBox();
  const regularPlotBox = await page.locator(`.plot[data-plot-id='${BLOCKER_PLOT_ID}']`).boundingBox();
  assert(containerBox && regularPlotBox, "Missing contained or regular plot dimensions");
  assert(
    Math.abs(containerBox.width - regularPlotBox.width) < 1
      && Math.abs(containerBox.height - regularPlotBox.height) < 1,
    `Contained plot is not one regular cell: ${JSON.stringify({ containerBox, regularPlotBox })}`,
  );
  await containerMarker.click();
  await page.locator(".drawer.drawer-open").waitFor({ state: "visible" });
  const drawerTitle = await page.locator("#plot-drawer-title").textContent();
  assert(
    drawerTitle === "P81",
    `Clicking the contained plot did not open P81: ${drawerTitle}`,
  );
  await page.locator(".drawer .close-btn").click();
  await dispatchPointer(page, containerMarker, cellW, 0, "mouse");
  await waitForPatchCount(containerPositionPatches, 1, "desktop contained-plot move");
  await page.waitForFunction(() => (
    document.querySelector(".map-container-marker[data-container-plot-id='P81']")
      ?.getAttribute("data-position-x") === "1"
  ));
  assert(
    JSON.stringify(containerPositionPatches) === JSON.stringify([{ position_x: 1, position_y: 0 }]),
    `Unexpected contained-plot PATCH: ${JSON.stringify(containerPositionPatches)}`,
  );
  assert(
    await page.locator(".plot.multi-selected").count() === 0,
    "Moved plot remained selected on the map",
  );

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const mobilePage = await mobileContext.newPage();
  attachDiagnostics(mobilePage);
  await mobilePage.route("**/api/**", apiRoute);
  const mobileSurface = await openMapPage(mobilePage);
  assert(
    await mobilePage.evaluate(() => window.matchMedia("(pointer: coarse)").matches),
    "Mobile context did not expose a coarse pointer",
  );

  const mobileHandleNames = await mobilePage.locator(
    ".map-object-resize-handle:visible",
  ).evaluateAll((items) => items.map((item) => item.dataset.handle).sort());
  assert(
    JSON.stringify(mobileHandleNames) === JSON.stringify([...CORNER_HANDLES].sort()),
    `Coarse-pointer editor exposed unexpected resize handles: ${JSON.stringify(mobileHandleNames)}`,
  );
  for (const handleName of CORNER_HANDLES) {
    const mobileHandle = mobilePage.locator(
      `.map-object-resize-handle[data-handle='${handleName}']`,
    );
    await mobileHandle.waitFor({ state: "visible" });
    await assertUsableTouchTarget(mobileHandle, `Mobile ${handleName}`);
  }

  await waitForDimensions(mobilePage, afterKeyboardResize, "mobile patio");
  const mobileGridBox = await mobilePage.locator("#map-grid").boundingBox();
  assert(mobileGridBox, "Missing mobile map grid bounding box");
  const mobileCellW = mobileGridBox.width / GRID_SIZE;
  const mobileCellH = mobileGridBox.height / GRID_SIZE;
  const mobileContainer = mobilePage.locator(".map-container-marker", { hasText: "P81" });
  await mobileContainer.waitFor({ state: "visible" });
  await dispatchPointer(mobilePage, mobileContainer, 0, mobileCellH, "touch");
  await waitForPatchCount(containerPositionPatches, 2, "mobile contained-plot move");
  await mobilePage.waitForFunction(() => (
    document.querySelector(".map-container-marker[data-container-plot-id='P81']")
      ?.getAttribute("data-position-y") === "1"
  ));
  assert(
    JSON.stringify(containerPositionPatches.at(-1)) === JSON.stringify({ position_x: 1, position_y: 1 }),
    `Unexpected mobile contained-plot PATCH: ${JSON.stringify(containerPositionPatches.at(-1))}`,
  );
  const mobileHandle = mobilePage.locator(
    ".map-object-resize-handle[data-handle='nw']",
  );
  await dispatchPointer(mobilePage, mobileHandle, -mobileCellW, 0, "touch");
  await waitForPatchCount(patches, 5, "mobile touch resize");
  const afterMobileResize = { x: 2, y: 1, width: 4, height: 4 };
  assert(
    sameGeometry(patches.at(-1), afterMobileResize),
    `Unexpected mobile resize geometry: ${JSON.stringify(patches.at(-1))}`,
  );
  await waitForDimensions(mobilePage, afterMobileResize, "mobile resized patio");

  const patchCountBeforeCancel = patches.length;
  const cancelEnd = await dispatchPointer(
    mobilePage,
    mobileSurface,
    -mobileCellW,
    0,
    "touch",
    false,
    42,
  );
  await dispatchTwoFingerTouchStart(mobilePage);
  await dispatchPointerUp(mobilePage, cancelEnd, "touch", 42);
  await waitForNoPatch(patches, patchCountBeforeCancel, "two-finger cancel");
  await waitForControlPlacement(mobilePage, afterMobileResize, "two-finger cancel");
  await waitForPreview(mobilePage, false);

  await browser.close();
  console.log("Map object direct manipulation e2e passed.");
}

main().catch(async (err) => {
  console.error(err);
  process.exit(1);
});
