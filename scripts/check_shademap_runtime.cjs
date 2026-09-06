// Verify the production loader against an operator-supplied, self-hosted SDK.
// No provider credentials or network requests are needed for this smoke test.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const frontendRequire = createRequire(path.resolve(__dirname, "../frontend/package.json"));
const ts = frontendRequire("typescript-compiler-api");
const { chromium } = frontendRequire("playwright-core");

async function main() {
  const runtimePath = process.argv[2];
  assert(runtimePath, "Usage: node scripts/check_shademap_runtime.cjs /absolute/runtime.js");
  const source = fs.readFileSync(path.resolve(__dirname, "../frontend/src/components/shadePanel.ts"), "utf8");
  const start = source.indexOf("let shadeMapRuntimeScriptUrl:");
  const end = source.indexOf("\n}", source.indexOf("async function loadShadeMapRuntime", start)) + 2;
  assert(start > 0 && end > start);
  const loader = ts.transpileModule(source.slice(start, end), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true, args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage();
    const unexpected = [];
    await page.route("**/*", route => {
      const url = route.request().url();
      if (url === "http://gardenops.test/") return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" });
      if (url === "http://gardenops.test/shademap/runtime.js") return route.fulfill({ contentType: "application/javascript", body: fs.readFileSync(runtimePath) });
      unexpected.push(url);
      return route.abort();
    });
    await page.goto("http://gardenops.test/");
    await page.addScriptTag({ path: frontendRequire.resolve("leaflet/dist/leaflet.js") });
    await page.addScriptTag({ content: loader });
    const result = await page.evaluate(async () => {
      await loadShadeMapRuntime("/shademap/runtime.js");
      const first = externalShadeMapRuntime();
      await loadShadeMapRuntime("/shademap/runtime.js");
      return {
        loaded: typeof first === "function" && first === window.ShadeMap,
        scripts: document.querySelectorAll('script[src="/shademap/runtime.js"]').length,
        methods: ["addTo", "setDate", "setSunExposure", "flushSync", "isPositionInSun"].every(name => typeof first.prototype[name] === "function"),
      };
    });
    assert.deepEqual(result, { loaded: true, scripts: 1, methods: true });
    assert.deepEqual(unexpected, []);
    console.log("Self-hosted ShadeMap runtime: loader, Leaflet compatibility, methods and single fetch passed; no external requests.");
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
