"use strict";

const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const AXE_SOURCE_PATH = path.join(ROOT, "frontend", "node_modules", "axe-core", "axe.min.js");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function visible(locator, label, timeout = 15000) {
  try {
    await locator.waitFor({ state: "visible", timeout });
  } catch (error) {
    throw new Error(`Expected visible ${label}: ${error.message}`);
  }
}

async function waitFor(condition, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function assertPageStructure(page, label, options = {}) {
  const result = await page.evaluate(() => {
    const ids = new Map();
    const duplicateIds = [];
    for (const element of document.querySelectorAll("[id]")) {
      const id = element.id;
      if (ids.has(id)) duplicateIds.push(id);
      ids.set(id, element);
    }
    const viewportWidth = document.documentElement.clientWidth;
    const overflow = Math.max(
      document.documentElement.scrollWidth,
      document.body?.scrollWidth || 0,
    ) - viewportWidth;
    const unnamedControls = [];
    for (const element of document.querySelectorAll("button, a[href], input, select, textarea")) {
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden") continue;
      const rect = element.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const name = element.getAttribute("aria-label")
        || element.getAttribute("title")
        || element.labels?.[0]?.textContent
        || element.textContent
        || "";
      if (!name.trim()) unnamedControls.push(element.id || element.outerHTML.slice(0, 100));
    }
    return { duplicateIds, overflow, unnamedControls };
  });
  assert(
    result.duplicateIds.length === 0,
    `${label} has duplicate IDs: ${result.duplicateIds.join(", ")}`,
  );
  assert(result.overflow <= 1, `${label} has ${result.overflow}px horizontal overflow`);
  if (options.enforceControlNames !== false) {
    assert(
      result.unnamedControls.length === 0,
      `${label} has visible unnamed controls: ${result.unnamedControls.join(" | ")}`,
    );
  }
  return result;
}

async function assertFocusInside(container, label) {
  assert(
    await container.evaluate((element) => element.contains(document.activeElement)),
    `${label} does not contain keyboard focus`,
  );
}

async function assertBusyState(locator, expected, label) {
  const value = await locator.getAttribute("aria-busy");
  assert(value === String(expected), `${label} aria-busy expected ${expected}, received ${value}`);
}

async function assertAxeState(page, stateId) {
  assert(fs.existsSync(AXE_SOURCE_PATH), "Phase 8 requires the pinned axe-core package");
  const axeSource = fs.readFileSync(AXE_SOURCE_PATH, "utf8");
  await page.evaluate((source) => {
    if (typeof window.axe?.run === "function") return;
    const script = document.createElement("script");
    script.textContent = source;
    document.head.append(script);
  }, axeSource);
  const raw = await page.evaluate(async (id) => {
    const result = await window.axe.run(document);
    return {
      state: id,
      violations: result.violations.map((violation) => ({
        impact: violation.impact,
        nodeCount: violation.nodes.length,
        rule: violation.id,
        targets: violation.nodes.map((node) => node.target.join(" ")),
      })),
    };
  }, stateId);
  const blocking = raw.violations.filter((violation) => (
    violation.impact === "critical" || violation.impact === "serious"
  ));
  assert(
    blocking.length === 0,
    `${stateId} has axe serious/critical violations: ${blocking.map((item) => (
      `${item.rule} (${item.impact}, ${item.nodeCount} nodes: ${item.targets.join(", ")})`
    )).join("; ")}`,
  );
  return {
    state: raw.state,
    violations: raw.violations.map(({ impact, nodeCount, rule }) => ({ impact, nodeCount, rule })),
  };
}

async function chromiumAXTree(page) {
  const session = await page.context().newCDPSession(page);
  try {
    await session.send("Accessibility.enable");
    const { nodes } = await session.send("Accessibility.getFullAXTree");
    return nodes;
  } finally {
    await session.detach();
  }
}

function axNodeProperty(node, propertyName) {
  if (propertyName === "role" || propertyName === "name") {
    return node[propertyName]?.value;
  }
  return node.properties?.find((property) => property.name === propertyName)?.value?.value;
}

function assertAXNode(nodes, expected, label) {
  const matches = nodes.filter((node) => (
    !node.ignored
    && Object.entries(expected).every(([property, value]) => axNodeProperty(node, property) === value)
  ));
  assert(matches.length > 0, `${label} was not present in the Chromium accessibility tree`);
  return matches[0];
}

async function assertFocusVisibleAndUnobscured(page, locator, label) {
  const result = await locator.evaluate((element) => {
    const focused = document.activeElement === element || element.contains(document.activeElement);
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const centerX = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const centerY = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
    const hit = document.elementFromPoint(centerX, centerY);
    const unobscured = hit === element || element.contains(hit) || hit?.contains(element) === true;
    const hasIndicator = (style.outlineStyle !== "none" && Number.parseFloat(style.outlineWidth) > 0)
      || style.boxShadow !== "none";
    return {
      focused,
      hasIndicator,
      unobscured,
      withinViewport: rect.top >= 0 && rect.left >= 0 && rect.bottom <= window.innerHeight
        && rect.right <= window.innerWidth,
    };
  });
  assert(result.focused, `${label} does not retain keyboard focus`);
  assert(result.hasIndicator, `${label} has no visible focus indicator`);
  assert(result.unobscured && result.withinViewport, `${label} is obscured or outside the viewport`);
  return result;
}

async function assertTouchTargets(locator, label, minimum = 44) {
  const count = await locator.count();
  assert(count > 0, `${label} has no touch targets`);
  const dimensions = [];
  for (let index = 0; index < count; index += 1) {
    const box = await locator.nth(index).boundingBox();
    assert(box && box.width >= minimum && box.height >= minimum,
      `${label} target ${index + 1} is smaller than ${minimum}px`);
    dimensions.push({ height: Math.round(box.height), width: Math.round(box.width) });
  }
  return dimensions;
}

module.exports = {
  assert,
  assertAXNode,
  assertAxeState,
  assertBusyState,
  assertFocusVisibleAndUnobscured,
  assertFocusInside,
  assertTouchTargets,
  assertPageStructure,
  chromiumAXTree,
  visible,
  waitFor,
};
