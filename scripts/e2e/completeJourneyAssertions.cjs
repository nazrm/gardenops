"use strict";

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

module.exports = {
  assert,
  assertBusyState,
  assertFocusInside,
  assertPageStructure,
  visible,
  waitFor,
};
