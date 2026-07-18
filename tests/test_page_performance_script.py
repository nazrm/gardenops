import json
import os
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_page_perf(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(ROOT / "scripts" / "check_page_performance.cjs"), *args],
        cwd=ROOT / "frontend",
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def _run_harness_probe(
    source: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    probe_env = os.environ.copy()
    probe_env["PERF_SCRIPT"] = str(ROOT / "scripts" / "check_page_performance.cjs")
    if env:
        probe_env.update(env)
    return subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        capture_output=True,
        check=False,
        env=probe_env,
        text=True,
        timeout=20,
    )


def _serve_status(status: int) -> tuple[ThreadingHTTPServer, str]:
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"performance probe"
            self.send_response(status)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_pids(path: Path) -> list[int]:
    if not path.exists():
        return []
    return [int(pid) for pid in path.read_text().split()]


def _kill_pids(path: Path) -> None:
    for pid in _read_pids(path):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue


def test_page_performance_script_documents_authenticated_app_scenario() -> None:
    result = _run_page_perf("--help")

    assert result.returncode == 0
    assert "app-unauth, app-auth, app-auth-large-tabs, or app-auth-focus-matrix" in result.stdout


def test_page_performance_focus_matrix_parser_requires_real_measured_backend() -> None:
    result = _run_harness_probe(
        """
const { parseArgs } = require(process.env.PERF_SCRIPT);
const parseError = (args) => {
  try { parseArgs(args); return ""; } catch (error) { return error.message; }
};
const base = [
  "--scenario", "app-auth-focus-matrix",
  "--url", "http://127.0.0.1:8123/",
];
console.log(JSON.stringify({
  stub: parseError(base),
  skipped: parseError([...base, "--no-api-stubs", "--skip-interaction"]),
  valid: parseArgs([...base, "--no-api-stubs"]).scenario,
}));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "stub": "--scenario app-auth-focus-matrix requires --no-api-stubs",
        "skipped": "--scenario app-auth-focus-matrix requires measured interactions",
        "valid": "app-auth-focus-matrix",
    }


def test_page_performance_focus_matrix_contract_is_complete_and_ordered() -> None:
    result = _run_harness_probe(
        """
const { FOCUS_MATRIX_CONTRACT, FOCUS_MATRIX_IDS } = require(process.env.PERF_SCRIPT);
console.log(JSON.stringify({ contract: FOCUS_MATRIX_CONTRACT, ids: FOCUS_MATRIX_IDS }));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    expected_ids = ["M3", "D1", "D2", "D4", "D5", "P1", "P2", "P4", "R2", "CROSS-01"]
    assert payload["ids"] == expected_ids
    assert [entry["id"] for entry in payload["contract"]] == expected_ids
    assert all(entry["surface"] for entry in payload["contract"])
    assert all(entry["expected"] for entry in payload["contract"])
    assert all(entry["requests"] for entry in payload["contract"])


def test_page_performance_focus_matrix_uses_visible_seeded_content_only() -> None:
    result = _run_harness_probe(
        """
const { selectorWithVisibleMatches } = require(process.env.PERF_SCRIPT);
const inventorySelector = "#inventory-table-body tr, "
  + "#inventory-mobile-list .inventory-card";
console.log(JSON.stringify({
  desktop: selectorWithVisibleMatches(inventorySelector),
  single: selectorWithVisibleMatches("#notification-panel .notification-item"),
}));
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "desktop": (
            "#inventory-table-body tr:visible, "
            "#inventory-mobile-list .inventory-card:visible"
        ),
        "single": "#notification-panel .notification-item:visible",
    }


def test_page_performance_focus_matrix_static_proof_contract() -> None:
    script = (ROOT / "scripts" / "check_page_performance.cjs").read_text()

    for selector in (
        "#mobile-tab-${tab}",
        "#top-tab-${tab}",
        "#attention-today-mobile-handle",
        "#mobile-notification-btn",
        "#notification-bell",
        "#sub-mode-journal:visible",
        "#sub-mode-issues:visible",
        "#sub-mode-inventory:visible",
        "#stats-mode-reports:visible",
        "#mobile-garden-select",
        "#garden-select",
    ):
        assert selector in script
    for field in (
        "focusId",
        "activeGarden",
        "surface",
        "expectedVisibleSeededContent",
        "scopedRequests",
        "browserErrors",
        "browserPostFrameMs",
        "browserReadyMs",
    ):
        assert field in script
    assert "installScenarioRoutes(context, options.scenario)" in script
    assert 'options.scenario === "app-auth-focus-matrix" && options.stubApi' in script


def test_page_performance_readiness_rejects_4xx() -> None:
    server, url = _serve_status(404)
    try:
        result = _run_harness_probe(
            """
const { request, waitForServer } = require(process.env.PERF_SCRIPT);
(async () => {
  const response = await request(process.env.PERF_URL);
  let waitError = "";
  try {
    await waitForServer(
      process.env.PERF_URL,
      20,
      { exitCode: null, signalCode: null },
      () => "test server log",
    );
  } catch (error) {
    waitError = error.message;
  }
  console.log(JSON.stringify({ response, waitError }));
})().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
""",
            env={"PERF_URL": url},
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["response"] == {"ok": False, "status": 404}
    assert "Timed out waiting" in payload["waitError"]


def test_page_performance_persists_output_before_budget_failure(tmp_path: Path) -> None:
    output_path = tmp_path / "failed-budget.json"
    result = _run_harness_probe(
        """
const { persistAndValidateResult } = require(process.env.PERF_SCRIPT);
const result = {
  compare: null,
  runs: [{}],
  scenario: "app-unauth",
  summary: {
    metrics: {
      authGateReadyMs: { n: 1, p75: 125 },
      usernameEnterMs: { n: 1, p75: 10 },
    },
  },
};
const options = {
  comparePath: "",
  interactionBudgetMs: null,
  navigationBudgetMs: 100,
  outputPath: process.env.PERF_OUTPUT,
  renderedRowBudget: null,
  tabSwitchBudgetMs: null,
};
let error = "";
try {
  persistAndValidateResult(result, options);
} catch (caught) {
  error = caught.message;
}
console.log(JSON.stringify({ error }));
""",
        env={"PERF_OUTPUT": str(output_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "authGateReadyMs p75 125ms exceeds 100ms" in json.loads(result.stdout)["error"]
    persisted = json.loads(output_path.read_text())
    assert persisted["summary"]["metrics"]["authGateReadyMs"]["p75"] == 125


def test_page_performance_metadata_documents_timing_and_mobile_assumptions() -> None:
    result = _run_harness_probe(
        """
const {
  buildMeasurementMetadata,
  buildReproducibilityProvenance,
} = require(process.env.PERF_SCRIPT);
const options = {
  browserPath: "",
  comparePath: "",
  deviceProfile: "desktop",
  headful: false,
  host: "127.0.0.1",
  interactionBudgetMs: null,
  json: true,
  maxRegressionMs: 15,
  maxRegressionPct: 5,
  navigationBudgetMs: null,
  outputPath: "",
  port: 5177,
  renderedRowBudget: null,
  runs: 2,
  scenario: "app-unauth",
  serve: false,
  serveMode: "dev",
  skipInteraction: false,
  stubApi: true,
  tabSwitchBudgetMs: null,
  timeoutMs: 15000,
  url: "http://127.0.0.1:5177/",
  viewportHeight: 844,
  viewportWidth: 390,
  warmupRuns: 1,
};
console.log(JSON.stringify({
  metadata: buildMeasurementMetadata(options),
  provenance: buildReproducibilityProvenance({
    argv: ["--scenario", "app-unauth", "--runs", "2"],
    browserPath: "/usr/bin/chromium",
    browserVersion: "123.0.0.0",
    options,
  }),
}));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    metadata = payload["metadata"]
    provenance = payload["provenance"]
    assert metadata["schemaVersion"] == 6
    assert metadata["browserContext"]["viewport"] == {"height": 844, "width": 390}
    assert metadata["browserContext"]["isMobile"] is False
    assert metadata["browserContext"]["hasTouch"] is False
    assert metadata["browserContext"]["mobileEmulation"] == {
        "deviceProfile": "desktop",
        "enabled": False,
        "responsiveBreakpointPx": 960,
        "responsiveLayout": "mobile-breakpoint",
        "strategy": "viewport-only",
    }
    assert metadata["browserContext"]["viewportProfile"] == {
        "deviceProfile": "desktop",
        "label": "responsive mobile-breakpoint desktop Chromium",
        "mobileDeviceEmulation": False,
        "responsiveLayout": "mobile-breakpoint",
        "strategy": "viewport-only",
        "viewport": {"height": 844, "width": 390},
    }
    assert "pointerdown" in metadata["interactionTiming"]["browserPostFrameFields"]["start"]
    assert (
        "not a guaranteed first presentation"
        in (metadata["interactionTiming"]["browserPostFrameFields"]["interpretation"])
    )
    assert "actionability" in metadata["interactionTiming"]["legacyNodeFields"]["description"]
    assert (
        "not browser render time"
        in metadata["interactionTiming"]["playwrightActionFields"]["description"]
    )
    assert provenance["browser"] == {
        "path": "/usr/bin/chromium",
        "version": "123.0.0.0",
    }
    assert provenance["git"]["revision"]
    assert isinstance(provenance["git"]["dirty"], bool)
    if provenance["git"]["dirty"]:
        assert isinstance(provenance["git"]["contentHash"], str)
        assert len(provenance["git"]["contentHash"]) == 64
    else:
        assert provenance["git"]["contentHash"] is None
    assert provenance["comparison"] == {
        "apiMode": "stub",
        "browser": {
            "engine": "chromium",
            "path": "/usr/bin/chromium",
            "version": "123.0.0.0",
        },
        "options": {
            "deviceProfile": "desktop",
            "headful": False,
            "interactionMode": "measured",
            "serveMode": "external",
            "targetUrl": "http://127.0.0.1:5177/",
            "timeoutMs": 15000,
        },
        "scenario": "app-unauth",
        "viewportProfile": metadata["browserContext"]["viewportProfile"],
    }
    assert provenance["invocation"]["argv"] == ["--scenario", "app-unauth", "--runs", "2"]
    assert provenance["invocation"]["effectiveOptions"] == {
        "browserPath": "",
        "comparePath": "",
        "deviceProfile": "desktop",
        "headful": False,
        "host": "127.0.0.1",
        "interactionBudgetMs": None,
        "json": True,
        "maxRegressionMs": 15,
        "maxRegressionPct": 5,
        "navigationBudgetMs": None,
        "outputPath": "",
        "port": 5177,
        "renderedRowBudget": None,
        "runs": 2,
        "scenario": "app-unauth",
        "serve": False,
        "serveMode": "dev",
        "skipInteraction": False,
        "stubApi": True,
        "tabSwitchBudgetMs": None,
        "timeoutMs": 15000,
        "url": "http://127.0.0.1:5177/",
        "viewportHeight": 844,
        "viewportWidth": 390,
        "warmupRuns": 1,
    }
    assert provenance["runCount"] == 2
    assert provenance["warmupRunCount"] == 1
    assert provenance["viewportProfile"]["label"] == (
        "responsive mobile-breakpoint desktop Chromium"
    )
    assert provenance["evidence"]["durableCiEvidence"] is False
    script = (ROOT / "scripts" / "check_page_performance.cjs").read_text()
    assert "process.exit(" not in script
    assert "usernameEnterBrowserPostFrameMs" in script
    assert "usernameEnterLegacyNodeReadyObservedMs" in script
    assert "usernameEnterPlaywrightActionMs" in script
    assert "tabSwitchBrowserPostFrameMs" in script
    assert "LARGE_TAB_TRANSITIONS" in script
    assert "`${name}BrowserPostFrameMs`" in script
    assert "tabSwitchPlaywrightActionMs" in script
    assert "initialApiRequests" in script
    assert "Map-first startup fetched the full /api/plants catalogue" in script
    assert 'const mapTab = page.locator(tabSelectorForViewport(options, "map"))' in script
    assert "await mapTab.click({ timeout: options.timeoutMs });" in script
    assert "window.__gardenopsPerfLargeTabThresholds = thresholds" in script
    assert "window.__gardenopsPerfLargeTabThresholds?.minimumMapPlots" in script
    assert "window.__gardenopsPerfLargeTabThresholds?.minimumPlantRows" in script
    assert "const minimumMapPlots = Number(args.minimumMapPlots ?? 600);" in script
    assert "const minimumPlantRows = Number(args.minimumPlantRows ?? 80);" in script
    assert "minimumMapPlots: options.minimumMapPlots" in script
    assert "minimumPlantRows: options.minimumPlantRows" in script
    assert "requestAnimationFrame(() => {\n        requestAnimationFrame" in script


def test_page_performance_auth_stub_covers_map_objects_and_fails_api_errors() -> None:
    result = _run_harness_probe(
        """
const {
  createApiResponseTracker,
  installScenarioRoutes,
} = require(process.env.PERF_SCRIPT);
const routes = [];
const context = {
  route: async (pattern, handler) => { routes.push({ pattern, handler }); },
};
const response = (status, url, method = "GET") => ({
  request: () => ({ method: () => method }),
  status: () => status,
  url: () => url,
});
(async () => {
  await installScenarioRoutes(context, "app-auth");
  const apiRoute = routes.find((entry) => entry.pattern === "**/api/**");
  let mapResponse = null;
  apiRoute.handler({
    fulfill: (payload) => { mapResponse = payload; },
    request: () => ({
      method: () => "GET",
      url: () => "http://perf.test/api/gardens/1/map-objects",
    }),
  });

  let listener = null;
  const page = {
    off: () => {},
    on: (event, handler) => { if (event === "response") listener = handler; },
  };
  const unauthTracker = createApiResponseTracker(page, "app-unauth");
  listener(response(401, "http://perf.test/api/auth/me"));
  let expectedAuthError = "";
  try {
    unauthTracker.assertNoUnexpectedResponses();
  } catch (caught) {
    expectedAuthError = caught.message;
  }

  const authTracker = createApiResponseTracker(page, "app-auth");
  listener(response(404, "http://perf.test/api/gardens/1/map-objects"));
  let unexpectedError = "";
  try {
    authTracker.assertNoUnexpectedResponses();
  } catch (caught) {
    unexpectedError = caught.message;
  }
  console.log(JSON.stringify({
    expectedAuthError,
    mapBody: JSON.parse(mapResponse.body),
    mapStatus: mapResponse.status,
    unexpectedError,
  }));
})().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mapStatus"] == 200
    assert payload["mapBody"] == {"objects": []}
    assert payload["expectedAuthError"] == ""
    assert "GET /api/gardens/1/map-objects -> 404" in payload["unexpectedError"]


def test_page_performance_tracks_server_timing_without_changing_api_error_contract() -> None:
    result = _run_harness_probe(
        """
const { createApiResponseTracker } = require(process.env.PERF_SCRIPT);
let listener = null;
const page = {
  off: () => {},
  on: (event, handler) => { if (event === "response") listener = handler; },
};
const response = (path, serverTiming) => ({
  allHeaders: async () => ({ "server-timing": serverTiming }),
  request: () => ({ method: () => "GET" }),
  status: () => 200,
  url: () => `http://perf.test${path}`,
});
(async () => {
  const tracker = createApiResponseTracker(page, "app-auth-large-tabs");
  listener(response("/api/plots", "db;dur=2.5, app;dur=7.25"));
  listener(response("/api/gardens/1/map-objects", "app;desc=route;dur=1.5"));
  listener(response("/assets/app.js", "app;dur=999"));
  await tracker.flush();
  tracker.assertNoUnexpectedResponses();
  console.log(JSON.stringify(tracker.summary()));
})().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "appServerDurationMs": 8.8,
        "appServerTimedResponseCount": 2,
        "responseCount": 2,
    }


def test_page_performance_records_api_payload_navigation_and_growth_metrics() -> None:
    script = (ROOT / "scripts" / "check_page_performance.cjs").read_text(encoding="utf-8")

    for metric in (
        "apiAppServerDurationMs",
        "apiDecodedResponseBytes",
        "apiEncodedResponseBytes",
        "apiResponseCount",
        "repeatedNavigationJsHeapUsedDeltaBytes",
        "repeatedNavigationNodesDelta",
    ):
        assert metric in script
    assert 'new URL(entry.name).pathname.startsWith("/api/")' in script
    assert "repeatedNavigationCdpBefore" in script
    assert "repeatedNavigationCdpAfter" in script
    assert "apiResponseTracker.flush()" in script
    assert "GROWTH_PROBE_CYCLES = 5" in script
    assert "GROWTH_PROBE_NAVIGATIONS" in script
    assert "HeapProfiler.collectGarbage" in script
    assert "completedNavigations" in script
    assert "growthProbe: warmupRuns.find" in script


def test_page_performance_app_auth_selects_responsive_tab_target() -> None:
    result = _run_harness_probe(
        """
const { tabSelectorForViewport } = require(process.env.PERF_SCRIPT);
console.log(JSON.stringify({
  desktop: tabSelectorForViewport({ viewportWidth: 1440 }, "garden"),
  mobile: tabSelectorForViewport({ viewportWidth: 390 }, "garden"),
  pixel7: tabSelectorForViewport({
    deviceProfile: "pixel-7",
    viewportWidth: 1440,
    viewportHeight: 900,
  }, "garden"),
}));
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "desktop": "#top-tab-garden",
        "mobile": "#mobile-tab-garden",
        "pixel7": "#mobile-tab-garden",
    }
    script = (ROOT / "scripts" / "check_page_performance.cjs").read_text()
    app_auth = script.split("async function runAppAuthScenario", 1)[1].split(
        "async function runAppAuthLargeTabsScenario", 1
    )[0]
    assert 'tabSelectorForViewport(options, "garden")' in app_auth
    session_bootstrap = script.split("async function createLiveSessionStorageState", 1)[1].split(
        "async function runMeasuredScenario", 1
    )[0]
    assert 'tabSelectorForViewport(options, "map")' in session_bootstrap
    assert "selectedTabSelector: gardenTabSelector" in app_auth
    assert "mobileLayoutBreakpointPx: MOBILE_LAYOUT_BREAKPOINT_PX" in app_auth
    assert 'gardenTab?.getAttribute("aria-current") === "page"' in script
    assert 'activeMapTab?.getAttribute("aria-current") === "page"' in script
    assert 'insightsTab?.getAttribute("aria-current") === "page"' in script
    assert 'mobileList?.querySelectorAll(".mobile-data-card").length === 1' in script
    assert "assertValidBrowserSample" in script


def test_page_performance_uses_a_real_pixel_7_device_profile() -> None:
    result = _run_harness_probe(
        """
const {
  buildComparisonProvenance,
  buildMeasurementMetadata,
  parseArgs,
} = require(process.env.PERF_SCRIPT);
const parseError = (argv) => {
  try {
    parseArgs(argv);
    return "";
  } catch (caught) {
    return caught.message;
  }
};
const options = parseArgs([
  "--device-profile", "pixel-7",
  "--warmup-runs", "2",
  "--runs", "7",
]);
const scaleOptions = parseArgs([
  "--no-api-stubs",
  "--url", "http://127.0.0.1:5177/",
  "--scenario", "app-auth-large-tabs",
  "--evidence-label", "phase-nine-small-desktop",
  "--skip-growth-probe",
  "--minimum-map-plots", "12",
  "--minimum-plant-rows", "24",
]);
console.log(JSON.stringify({
  defaults: parseArgs([]),
  invalidEvidenceLabel: parseError(["--evidence-label", "Phase-Nine"]),
  invalidMinimumScenario: parseError(["--minimum-map-plots", "12"]),
  invalidProfile: parseError(["--device-profile", "tablet"]),
  metadata: buildMeasurementMetadata(options),
  comparison: buildComparisonProvenance({
    browserPath: "/usr/bin/chromium",
    browserVersion: "123",
    options,
  }),
  options,
  scaleOptions,
}));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["defaults"]["deviceProfile"] == "desktop"
    assert payload["defaults"]["runs"] == 7
    assert payload["defaults"]["warmupRuns"] == 1
    assert "lowercase letters" in payload["invalidEvidenceLabel"]
    assert "require --scenario app-auth-large-tabs" in payload["invalidMinimumScenario"]
    assert "Unknown device profile: tablet" in payload["invalidProfile"]
    assert payload["options"]["deviceProfile"] == "pixel-7"
    assert payload["options"]["runs"] == 7
    assert payload["options"]["warmupRuns"] == 2
    assert payload["scaleOptions"]["evidenceLabel"] == "phase-nine-small-desktop"
    assert payload["scaleOptions"]["skipGrowthProbe"] is True
    assert payload["scaleOptions"]["minimumMapPlots"] == 12
    assert payload["scaleOptions"]["minimumPlantRows"] == 24
    assert payload["comparison"]["options"]["skipGrowthProbe"] is False
    assert payload["comparison"]["options"]["minimumMapPlots"] == 600
    assert payload["comparison"]["options"]["minimumPlantRows"] == 80
    browser_context = payload["metadata"]["browserContext"]
    assert browser_context["isMobile"] is True
    assert browser_context["hasTouch"] is True
    assert "Pixel 7" in browser_context["userAgentOverride"]
    assert browser_context["mobileEmulation"] == {
        "deviceProfile": "pixel-7",
        "enabled": True,
        "responsiveBreakpointPx": 960,
        "responsiveLayout": "mobile-breakpoint",
        "strategy": "playwright-device-descriptor",
    }
    assert browser_context["viewportProfile"]["label"] == ("Playwright Pixel 7 mobile emulation")
    assert browser_context["viewportProfile"]["strategy"] == ("playwright-device-descriptor")


def test_page_performance_live_mode_requires_loopback_and_environment_credentials() -> None:
    result = _run_harness_probe(
        """
const { liveCredentialsFor, parseArgs } = require(process.env.PERF_SCRIPT);
const errorFor = (fn) => {
  try {
    fn();
    return "";
  } catch (caught) {
    return caught.message;
  }
};
const local = parseArgs([
  "--no-api-stubs",
  "--scenario", "app-auth",
  "--url", "http://localhost:5177/",
]);
console.log(JSON.stringify({
  credentials: errorFor(() => liveCredentialsFor(local)),
  remote: errorFor(() => parseArgs([
    "--no-api-stubs",
    "--url", "https://example.com/",
  ])),
}));
""",
        env={
            "GARDENOPS_PAGE_PERF_PASSWORD": "",
            "GARDENOPS_PAGE_PERF_USERNAME": "",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "GARDENOPS_PAGE_PERF_USERNAME" in payload["credentials"]
    assert "HTTP loopback target" in payload["remote"]
    script = (ROOT / "scripts" / "check_page_performance.cjs").read_text()
    assert "signInThroughSessionForm" in script
    assert "storageState: liveSession.storageState" in script
    assert "GARDENOPS_PAGE_PERF_GARDEN_NAME" in script
    assert "gardenops-active-garden-id" in script
    assert "non-loopback request" in script


def test_page_performance_metric_stats_reports_sample_count() -> None:
    result = _run_harness_probe(
        """
const { metricStats } = require(process.env.PERF_SCRIPT);
console.log(JSON.stringify(metricStats([
  { timings: { probeMs: 10 } },
  { timings: { probeMs: null } },
  { timings: {} },
  { timings: { probeMs: 30 } },
], "probeMs")));
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "n": 2,
        "min": 10,
        "median": 20,
        "p75": 25,
        "max": 30,
    }


def test_page_performance_budgets_use_browser_post_frame_metrics() -> None:
    result = _run_harness_probe(
        """
const { enforceBudgets } = require(process.env.PERF_SCRIPT);
const noBudget = {
  interactionBudgetMs: null,
  navigationBudgetMs: null,
  renderedRowBudget: null,
  tabSwitchBudgetMs: null,
};
const run = (scenario, metrics, options) => {
  let error = "";
  try {
    enforceBudgets({ runs: [{}, {}, {}], scenario, summary: { metrics } }, options);
  } catch (caught) {
    error = caught.message;
  }
  return error;
};
const p75 = (value, n = 3) => ({ n, p75: value });
const largeMetrics = {
  appReadyMs: p75(10),
  mapToActivityTasksBrowserPostFrameMs: p75(20),
  activityTasksToGardenBrowserPostFrameMs: p75(20),
  gardenToInsightsBrowserPostFrameMs: p75(20),
  insightsToMapBrowserPostFrameMs: p75(20),
  warmMapToActivityTasksBrowserPostFrameMs: p75(20),
  warmActivityTasksToGardenBrowserPostFrameMs: p75(20),
  warmGardenToInsightsBrowserPostFrameMs: p75(20),
  warmInsightsToMapBrowserPostFrameMs: p75(20),
  maxTabSwitchLegacyNodePostFrameObservedMs: p75(999),
  maxPlaywrightActionMs: p75(999),
};
const genericMetrics = {
  authGateReadyMs: p75(10),
  usernameEnterBrowserPostFrameMs: p75(20),
  usernameEnterLegacyNodePostFrameObservedMs: p75(999),
  usernameEnterPlaywrightActionMs: p75(999),
};
const appAuthMetrics = {
  appReadyMs: p75(10),
  tabSwitchBrowserPostFrameMs: p75(20),
  tabSwitchLegacyNodePostFrameObservedMs: p75(999),
  tabSwitchPlaywrightActionMs: p75(999),
};
const genericPass = run("app-unauth", genericMetrics, {
  ...noBudget,
  interactionBudgetMs: 50,
});
const genericFail = run("app-unauth", {
  ...genericMetrics,
  usernameEnterBrowserPostFrameMs: p75(60),
}, {
  ...noBudget,
  interactionBudgetMs: 50,
});
const appAuthTabBudgetPass = run("app-auth", appAuthMetrics, {
  ...noBudget,
  tabSwitchBudgetMs: 50,
});
const appAuthTabBudgetFail = run("app-auth", {
  ...appAuthMetrics,
  tabSwitchBrowserPostFrameMs: p75(60),
}, {
  ...noBudget,
  tabSwitchBudgetMs: 50,
});
const appAuthMissingTabMeasurement = run("app-auth", {
  ...appAuthMetrics,
  tabSwitchBrowserPostFrameMs: p75(null),
}, {
  ...noBudget,
  tabSwitchBudgetMs: 50,
});
const appAuthPartialTabMeasurement = run("app-auth", {
  ...appAuthMetrics,
  tabSwitchBrowserPostFrameMs: p75(20, 2),
}, {
  ...noBudget,
  tabSwitchBudgetMs: 50,
});
const largePass = run("app-auth-large-tabs", largeMetrics, {
  ...noBudget,
  interactionBudgetMs: 50,
  tabSwitchBudgetMs: 50,
});
const largeInteractionFail = run("app-auth-large-tabs", {
  ...largeMetrics,
  gardenToInsightsBrowserPostFrameMs: p75(60),
}, {
  ...noBudget,
  interactionBudgetMs: 50,
});
const largeTabBudgetFail = run("app-auth-large-tabs", {
  ...largeMetrics,
  warmInsightsToMapBrowserPostFrameMs: p75(60),
}, {
  ...noBudget,
  tabSwitchBudgetMs: 50,
});
console.log(JSON.stringify({
  appAuthMissingTabMeasurement,
  appAuthPartialTabMeasurement,
  appAuthTabBudgetFail,
  appAuthTabBudgetPass,
  genericFail,
  genericPass,
  largeInteractionFail,
  largePass,
  largeTabBudgetFail,
}));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["genericPass"] == ""
    assert "usernameEnterBrowserPostFrameMs" in payload["genericFail"]
    assert "usernameEnterLegacyNodePostFrameObservedMs" not in payload["genericFail"]
    assert payload["appAuthTabBudgetPass"] == ""
    assert "tabSwitchBrowserPostFrameMs" in payload["appAuthTabBudgetFail"]
    assert "tab-switch budget" in payload["appAuthTabBudgetFail"]
    assert "has no measured p75" in payload["appAuthMissingTabMeasurement"]
    assert "has 2/3 measured samples" in payload["appAuthPartialTabMeasurement"]
    assert payload["largePass"] == ""
    assert "gardenToInsightsBrowserPostFrameMs" in payload["largeInteractionFail"]
    assert "interaction budget" in payload["largeInteractionFail"]
    assert "warmInsightsToMapBrowserPostFrameMs" in payload["largeTabBudgetFail"]
    assert "tab-switch budget" in payload["largeTabBudgetFail"]


def test_page_performance_rejects_unmeasurable_tab_switch_budgets() -> None:
    result = _run_harness_probe(
        """
const { parseArgs } = require(process.env.PERF_SCRIPT);
const parseError = (argv) => {
  try {
    parseArgs(argv);
    return "";
  } catch (caught) {
    return caught.message;
  }
};
console.log(JSON.stringify({
  skipped: parseError([
    "--scenario", "app-auth",
    "--skip-interaction",
    "--tab-switch-budget-ms", "50",
  ]),
  skippedInteraction: parseError([
    "--scenario", "app-unauth",
    "--skip-interaction",
    "--interaction-budget-ms", "50",
  ]),
  unauth: parseError([
    "--scenario", "app-unauth",
    "--tab-switch-budget-ms", "50",
  ]),
}));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "requires measured tab transitions" in payload["skipped"]
    assert "requires a measured interaction" in payload["skippedInteraction"]
    assert "requires an app-auth or app-auth-large-tabs scenario" in payload["unauth"]


def test_page_performance_comparison_does_not_gate_diagnostic_timings() -> None:
    result = _run_harness_probe(
        """
const { compareSummaries } = require(process.env.PERF_SCRIPT);
const metric = (median) => ({ median });
const provenance = {
  comparison: {
    apiMode: "stub",
    browser: { engine: "chromium", path: "/usr/bin/chromium", version: "123" },
    options: {
      headful: false,
      interactionMode: "measured",
      serveMode: "dev",
      targetUrl: "http://perf.test/",
    },
    scenario: "app-auth-large-tabs",
    viewportProfile: { viewport: { height: 900, width: 1440 } },
  },
  git: { contentHash: null, dirty: false, revision: "baseline" },
};
const previous = { provenance, summary: { metrics: {
  mapToGardenBrowserPostFrameMs: metric(100),
  mapToGardenLegacyNodePostFrameObservedMs: metric(100),
  mapToGardenPlaywrightActionMs: metric(100),
  maxLongTaskMs: metric(100),
  mountedPlantRows: metric(100),
} } };
const current = { provenance: structuredClone(provenance), summary: { metrics: {
  mapToGardenBrowserPostFrameMs: metric(200),
  mapToGardenLegacyNodePostFrameObservedMs: metric(900),
  mapToGardenPlaywrightActionMs: metric(900),
  maxLongTaskMs: metric(900),
  mountedPlantRows: metric(900),
} } };
console.log(JSON.stringify(compareSummaries(current, previous, {
  maxRegressionMs: 0,
  maxRegressionPct: 5,
})));
""",
    )

    assert result.returncode == 0, result.stderr
    rows = {row["metric"]: row for row in json.loads(result.stdout)}
    assert rows["mapToGardenBrowserPostFrameMs"]["comparisonGated"] is True
    assert rows["mapToGardenBrowserPostFrameMs"]["regressed"] is True
    assert rows["mapToGardenLegacyNodePostFrameObservedMs"]["comparisonGated"] is False
    assert rows["mapToGardenLegacyNodePostFrameObservedMs"]["regressed"] is False
    assert rows["mapToGardenPlaywrightActionMs"]["comparisonGated"] is False
    assert rows["mapToGardenPlaywrightActionMs"]["regressed"] is False
    assert rows["maxLongTaskMs"]["comparisonGated"] is False
    assert rows["maxLongTaskMs"]["regressed"] is False
    assert rows["mountedPlantRows"]["comparisonGated"] is False
    assert rows["mountedPlantRows"]["regressed"] is False


def test_page_performance_comparison_rejects_incompatible_provenance() -> None:
    result = _run_harness_probe(
        """
const { compareSummaries } = require(process.env.PERF_SCRIPT);
const comparison = {
  apiMode: "stub",
  browser: { engine: "chromium", path: "/usr/bin/chromium", version: "123" },
  options: {
    headful: false,
    interactionMode: "measured",
    serveMode: "dev",
    targetUrl: "http://perf.test/",
  },
  scenario: "app-auth",
  viewportProfile: { viewport: { height: 900, width: 1440 } },
};
const makeResult = (comparisonPatch = {}, gitPatch = {}) => ({
  provenance: {
    comparison: { ...comparison, ...comparisonPatch },
    git: {
      contentHash: null,
      dirty: false,
      revision: "same-commit",
      ...gitPatch,
    },
  },
  summary: { metrics: { appReadyMs: { median: 100 } } },
});
const errorFor = (current, baseline) => {
  try {
    compareSummaries(current, baseline, {
      maxRegressionMs: 0,
      maxRegressionPct: 5,
    });
    return "";
  } catch (caught) {
    return caught.message;
  }
};
const baseline = makeResult();
console.log(JSON.stringify({
  apiMode: errorFor(makeResult({ apiMode: "live" }), baseline),
  browser: errorFor(makeResult({
    browser: { ...comparison.browser, version: "124" },
  }), baseline),
  dirtyWithIdentity: errorFor(
    makeResult({}, { contentHash: "current-diff", dirty: true }),
    makeResult({}, { contentHash: "baseline-diff", dirty: true }),
  ),
  dirtyWithoutIdentity: errorFor(
    makeResult({}, { dirty: true }),
    makeResult({}, { dirty: true }),
  ),
  options: errorFor(makeResult({
    options: { ...comparison.options, interactionMode: "load-only" },
  }), baseline),
  scenario: errorFor(makeResult({ scenario: "app-unauth" }), baseline),
  viewport: errorFor(makeResult({
    viewportProfile: { viewport: { height: 844, width: 390 } },
  }), baseline),
}));
""",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "provenance.comparison.apiMode differs" in payload["apiMode"]
    assert "provenance.comparison.browser differs" in payload["browser"]
    assert payload["dirtyWithIdentity"] == ""
    assert "dirty working tree is missing a content hash" in payload["dirtyWithoutIdentity"]
    assert "provenance.comparison.options differs" in payload["options"]
    assert "provenance.comparison.scenario differs" in payload["scenario"]
    assert "provenance.comparison.viewportProfile differs" in payload["viewport"]


def test_page_performance_does_not_emit_success_when_cleanup_fails() -> None:
    result = _run_harness_probe(
        """
const { emitSuccessAfterCleanup } = require(process.env.PERF_SCRIPT);
(async () => {
  let emitted = false;
  let serverStopped = false;
  let error = "";
  try {
    await emitSuccessAfterCleanup({
      browser: { close: async () => { throw new Error("browser close failed"); } },
      server: { stop: async () => { serverStopped = true; } },
      emit: async () => { emitted = true; },
    });
  } catch (caught) {
    error = caught.message;
  }
  console.log(JSON.stringify({ emitted, error, serverStopped }));
})().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "emitted": False,
        "error": "Page performance cleanup failed: browser: browser close failed",
        "serverStopped": True,
    }


def test_page_performance_startup_failure_cleans_managed_process_group(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pid_path = tmp_path / "managed-pids"
    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        """#!/bin/sh
exec node -e '
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const child = spawn("sleep", ["30"], { stdio: "ignore" });
fs.writeFileSync(process.env.PERF_CHILD_PIDS, String(child.pid));
process.exit(0);
'
""",
    )
    fake_npm.chmod(0o755)
    port = _unused_port()
    try:
        result = _run_harness_probe(
            """
const fs = require("node:fs");
const { startServer } = require(process.env.PERF_SCRIPT);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const isAlive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code !== "ESRCH";
  }
};
(async () => {
  let error = "";
  try {
    await startServer({
      host: "127.0.0.1",
      port: Number(process.env.PERF_PORT),
      serveMode: "dev",
      timeoutMs: 500,
      url: process.env.PERF_URL,
    });
  } catch (caught) {
    error = caught.message;
  }
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (fs.existsSync(process.env.PERF_CHILD_PIDS)) {
      const pids = fs.readFileSync(process.env.PERF_CHILD_PIDS, "utf8")
        .trim()
        .split(/\\s+/)
        .filter(Boolean)
        .map(Number);
      if (pids.length > 0 && pids.every((pid) => !isAlive(pid))) break;
    }
    await sleep(50);
  }
  const pids = fs.existsSync(process.env.PERF_CHILD_PIDS)
    ? fs.readFileSync(process.env.PERF_CHILD_PIDS, "utf8")
      .trim()
      .split(/\\s+/)
      .filter(Boolean)
      .map(Number)
    : [];
  console.log(JSON.stringify({ error, pids, alive: pids.map(isAlive) }));
})().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
""",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "PERF_CHILD_PIDS": str(pid_path),
                "PERF_PORT": str(port),
                "PERF_URL": f"http://127.0.0.1:{port}/",
            },
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert any(
            message in payload["error"]
            for message in (
                "Vite server exited before becoming ready",
                "Timed out waiting",
            )
        )
        assert len(payload["pids"]) == 1
        assert payload["alive"] == [False]
    finally:
        _kill_pids(pid_path)
        time.sleep(0.01)
