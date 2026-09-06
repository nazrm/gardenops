// Run: node tests/task_completion_frontend.cjs
// Chromium integration of real Vite-served modules; synthetic HTTP, no backend E2E.
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("../frontend/node_modules/playwright-core");

async function main() {
  const { createServer } = await import("../frontend/node_modules/vite/dist/node/index.js");
  const server = await createServer({
    root: path.resolve(__dirname, "../frontend"), configFile: false,
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{ name: "completion-test-shell", configureServer(vite) {
      vite.middlewares.use((req, res, next) => {
        if (req.url !== "/completion-test") return next();
        res.setHeader("Content-Type", "text/html");
        res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/src/style.css"></head><body></body></html>');
      });
    } }],
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({
      executablePath: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/chromium", headless: true,
    });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const placements = { moved: ["NEW"], unlinked: ["NEW"], P1: ["A"], P2: ["B"] };
    const placementRequests = [];
    const actions = [];
    const task = (id, plantIds, plotIds = []) => ({
      id, garden_id: 1, task_type: "observe_bloom", title: `Bloom ${id}`,
      status: "pending", plant_ids: plantIds, plot_ids: plotIds,
      observation_timezone: "Europe/Oslo", updated_at_ms: 123,
    });
    let tasks = [task("quick", ["moved"], ["OLD"]), task("remaining", ["P1"]),
      { ...task("other", ["P2"]), title: "Unrelated task" }];
    let listRequests = 0;
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const match = url.pathname.match(/^\/api\/plants\/([^/]+)\/plots$/);
      if (match && Object.hasOwn(placements, match[1])) {
        placementRequests.push({ plant: match[1], garden: request.headers()["x-garden-id"] });
        return route.fulfill({ json: placements[match[1]] });
      }
      if (url.pathname === "/api/tasks" && request.method() === "GET") {
        listRequests++;
        return route.fulfill({ json: { tasks, total: tasks.length } });
      }
      if (url.pathname === "/api/tasks/quick/action" && request.method() === "POST") {
        actions.push(request.postDataJSON());
        tasks = tasks.filter((item) => item.id !== "quick");
        return route.fulfill({ json: { status: "completed", updated_at_ms: 456 } });
      }
      errors.push(`Unexpected API: ${request.method()} ${url.pathname}`);
      return route.fulfill({ status: 500, json: { detail: "Unexpected test request" } });
    });
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/completion-test`);
    await page.evaluate(async () => {
      const api = await import("/src/services/api.ts");
      const queue = await import("/src/services/offlineQueue.ts");
      api.setActiveGardenContext(1);
      queue.setOfflineQueueIdentity("completion-regression");
      await queue.initOfflineQueue();
      window.completion = await import("/src/features/taskCompletionFlow.ts");
      window.quickActions = await import("/src/features/quickActionsFeature.ts");
      window.submissions = [];
    });
    const open = async (fixture) => {
      await page.evaluate((fixture) => {
        window.completion.openTaskCompletionDialog(fixture,
          new Map(fixture.plant_ids.map((id) => [id, id])),
          (body) => { window.submissions.push(body); return true; },
          { plotNames: new Map(["OLD", "NEW", "A", "B"].map((id) => [id, `Bed ${id}`])) });
      }, fixture);
      await page.waitForFunction(() => {
        const button = document.querySelector(".task-completion-dialog .confirm-yes");
        return button && !button.disabled;
      });
    };
    const choices = () => page.locator('.task-observed-locations input[type="checkbox"]')
      .evaluateAll((inputs) => inputs.map((input) => ({ value: input.value, checked: input.checked })));
    const submit = async (plantIds, plotIds) => {
      await page.locator(".task-occurrence-date input").fill("2026-01-02");
      await page.locator(".task-completion-dialog .confirm-yes").click();
      await page.waitForFunction(() => !document.querySelector(".task-completion-dialog"));
      assert.deepEqual(await page.evaluate(() => window.submissions.at(-1)), {
        action: "complete", completed_plant_ids: plantIds, completion_outcome: "done",
        occurred_on: "2026-01-02", observed_plot_ids: plotIds,
      });
    };

    await open(task("stale", ["moved"], ["OLD"]));
    assert.deepEqual(await choices(), [{ value: "NEW", checked: true }]);
    await submit(["moved"], ["NEW"]);
    console.log("PASS stale OLD task links replaced by current NEW API placement, preselected and submitted");

    await open(task("no-links", ["unlinked"]));
    assert.deepEqual(await choices(), [{ value: "NEW", checked: true }]);
    await submit(["unlinked"], ["NEW"]);
    console.log("PASS task without plot links offers and submits current assignments");

    await open(task("group", ["P1", "P2"], ["A", "B"]));
    assert.deepEqual(await choices(), []);
    await submit(["P1", "P2"], []);
    await open(task("group-one", ["P1", "P2"], ["A", "B"]));
    await page.locator('.task-completion-list input[value="P2"]').uncheck();
    assert.deepEqual(await choices(), [{ value: "A", checked: true }]);
    await submit(["P1"], ["A"]);
    console.log("PASS disjoint grouped placements stay plant-only; selecting P1 offers only A");

    const requestsBeforeOffline = placementRequests.length;
    await context.setOffline(true);
    assert.equal(await page.evaluate(() => navigator.onLine), false);
    await open(task("cached", ["moved"], ["OLD"]));
    assert.deepEqual(await choices(), [{ value: "NEW", checked: true }]);
    await submit(["moved"], ["NEW"]);
    await open(task("unknown", ["unknown"], ["OLD"]));
    assert.deepEqual(await choices(), []);
    await submit(["unknown"], []);
    assert.equal(placementRequests.length, requestsBeforeOffline);
    console.log("PASS offline uses confirmed NEW, not stale OLD; unknown plant remains plant-only");
    await context.setOffline(false);

    await page.evaluate(() => {
      document.body.innerHTML = '<button id="mobile-fab">Open</button><div id="mobile-fab-backdrop" aria-hidden="true"></div><section id="mobile-quick-actions" role="dialog" aria-hidden="true" inert><button id="mobile-quick-actions-close-btn">Close</button><div id="mobile-quick-actions-content"></div></section>';
      window.toasts = [];
      window.quickActions.initQuickActionsFeature({
        canWrite: () => true, ensureWriteAccess: () => true,
        ensurePlantsCacheLoaded: async () => {},
        getPlants: () => ["moved", "P1", "P2"].map((plt_id) => ({ plt_id, name: plt_id })),
        getPlots: () => ["OLD", "NEW", "A", "B"].map((plot_id) => ({ plot_id, display_name: plot_id })),
        showToast: (...args) => window.toasts.push(args), refreshBadgeCounts: async () => {},
      });
    });
    await page.locator("#mobile-fab").click();
    await page.locator('[data-quick-action="complete-task"]').click();
    const search = page.locator(".quick-action-task-search");
    await search.fill("Bloom");
    assert.equal(await page.locator('[data-task-id="other"]').count(), 0);
    await page.locator('[data-task-id="quick"]').click();
    await page.waitForFunction(() => {
      const button = document.querySelector(".task-completion-dialog .confirm-yes");
      return button && !button.disabled;
    });
    assert.equal(await page.locator("#mobile-quick-actions").evaluate((el) => el.inert), true);
    assert.equal(await search.inputValue(), "Bloom");
    assert.deepEqual(await choices(), [{ value: "NEW", checked: true }]);
    await page.locator(".task-occurrence-date input").fill("2026-01-02");
    await page.locator(".task-completion-dialog .confirm-yes").click();
    await page.waitForFunction(() => !document.querySelector(".task-completion-dialog")
      && document.activeElement === document.querySelector(".quick-action-task-search"));
    assert.equal(await page.locator("#mobile-quick-actions").evaluate((el) => el.inert), false);
    assert.equal(await search.inputValue(), "Bloom");
    assert.equal(await page.locator('[data-task-id="quick"]').count(), 0);
    assert.equal(await page.locator('[data-task-id="remaining"]').count(), 1);
    assert.equal(await page.locator('[data-task-id="other"]').count(), 0);
    assert.equal(listRequests, 2, "successful completion must refresh the actual quick picker");
    assert.equal(actions.length, 1);
    assert.equal(actions[0].occurred_on, "2026-01-02");
    assert.deepEqual(actions[0].observed_plot_ids, ["NEW"]);
    assert.deepEqual(actions[0].completed_plant_ids, ["moved"]);
    assert.equal(actions[0].action, "complete");
    assert.equal(actions[0].completion_outcome, "done");
    assert.equal(await page.evaluate(() => window.toasts.some((args) => args[1] === "error")), false);
    assert(placementRequests.every((request) => request.garden === "1"));
    assert.deepEqual(errors, []);
    console.log("PASS real quickActionsFeature successful dated completion preserves query/filter and restores search focus after parent inert release");
  } finally {
    try { await browser?.close(); } finally { await server.close(); }
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
