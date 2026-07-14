#!/usr/bin/env node
"use strict";

const { execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { assert, assertPageStructure } = require("./e2e/completeJourneyAssertions.cjs");
const {
  assertLoopbackBaseUrl,
  assertBrowserProfileContract,
  assertDiagnosticsClean,
  allowedBrowserOrigins,
  authenticate,
  createApiRecorder,
  createGuardedContext,
  isAllowedUrl,
  redactTokenShapedSecrets,
  sanitizeDiagnostic,
} = require("./e2e/completeJourneyBrowser.cjs");
const { runFoundation } = require("./e2e/journeys/foundation.cjs");
const { runGardenMapPlants } = require("./e2e/journeys/gardenMapPlants.cjs");
const { runDailyAttentionWork } = require("./e2e/journeys/dailyAttentionWork.cjs");

const ROOT = path.resolve(__dirname, "..");
const BASE_URL = process.env.BASE_URL || "";
const ARTIFACT_DIR = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR || "";
const FIXTURE_PATH = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_FIXTURE_PATH || "";
const PHASE = Number(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PHASE || "-1");
const THROUGH_PHASE = Number(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_THROUGH_PHASE || "-1");
const USERNAME = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_USERNAME || "";
const PASSWORD = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PASSWORD || ""; // push-sanitizer: allow SECRET_ASSIGNMENT - environment lookup only
const CHROMIUM_LAUNCHER = fs.existsSync("/usr/bin/chromium-browser")
  ? "/usr/bin/chromium-browser"
  : "/usr/bin/chromium";
const CHROMIUM_EXECUTABLE = resolveChromiumExecutable(CHROMIUM_LAUNCHER);
const PHASE_TWO_PROFILE_ORDER = [
  "admin:desktop",
  "admin:mobile",
  "editor:desktop",
  "editor:mobile",
  "viewer:desktop",
  "viewer:mobile",
];
const PHASE_TWO_READ_ONLY_PERMUTATION_ORDER = [
  "viewer:mobile",
  "admin:desktop",
  "editor:mobile",
  "viewer:desktop",
  "admin:mobile",
  "editor:desktop",
];
const PHASE_TWO_EDITOR_PASSWORD = "CompleteJourneysEditorE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
const PHASE_TWO_VIEWER_PASSWORD = "CompleteJourneysViewerE2E!Passphrase2026"; // push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture

function phaseSelected(phase) {
  return phase >= PHASE && phase <= THROUGH_PHASE;
}

function expectedSessionUserCounts(fixture, profiles, phaseOneRan) {
  return Object.fromEntries([
    [fixture.roles.admin, profiles.filter((profile) => profile.role === "admin").length],
    [fixture.roles.editor, profiles.filter((profile) => profile.role === "editor").length],
    [fixture.roles.onboarding, phaseOneRan ? 1 : 0],
    [fixture.roles.onboarding_mobile, phaseOneRan ? 1 : 0],
    [fixture.roles.viewer, profiles.filter((profile) => profile.role === "viewer").length],
  ]);
}

function assertRunnerEnvironment() {
  assert(process.env.APP_ENV === "test", "Complete journey E2E requires APP_ENV=test");
  assert(process.env.AUTH_REQUIRED === "true", "Complete journey E2E requires session auth");
  assert(process.env.AUTH_MODE === "session", "Complete journey E2E requires AUTH_MODE=session");
  assert(
    process.env.DATABASE_URL === process.env.GARDENOPS_DISPOSABLE_POSTGRES_URL,
    "Complete journey DATABASE_URL must match the disposable runner URL",
  );
  assert(Number.isInteger(PHASE) && PHASE >= 0 && PHASE <= 9, "Invalid phase selection");
  assert(
    Number.isInteger(THROUGH_PHASE) && THROUGH_PHASE >= PHASE && THROUGH_PHASE <= 9,
    "Invalid through-phase selection",
  );
  assertLoopbackBaseUrl(BASE_URL);
  assert(
    process.env.GARDENOPS_VITE_PROXY_TARGET,
    "Complete journey browser contract requires a disposable backend origin",
  );
  const browserOrigins = allowedBrowserOrigins({
    backendUrl: process.env.GARDENOPS_VITE_PROXY_TARGET || "",
    baseUrl: BASE_URL,
    providerUrl: process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_URL || "",
  });
  assert(browserOrigins.size >= 2,
    "Complete journey browser contract must include distinct frontend and backend origins");
  assert(isAllowedUrl(BASE_URL, browserOrigins), "Complete journey BASE_URL is not in its browser contract");
  assert(fs.existsSync(CHROMIUM_EXECUTABLE), "System Chromium is required");
  assert(process.env.GARDENOPS_LOGS_DIR, "Complete journey backend log directory is required");
  assert(PHASE <= 2 && THROUGH_PHASE <= 2, "Requested phase is not implemented");
}

function assertPrivateFiles() {
  const artifactReal = fs.realpathSync.native(ARTIFACT_DIR);
  const researchReal = fs.realpathSync.native(path.join(ROOT, "research"));
  const relative = path.relative(researchReal, artifactReal);
  assert(relative && !relative.startsWith("..") && !path.isAbsolute(relative), "Unsafe artifact directory");
  const fixtureReal = fs.realpathSync.native(FIXTURE_PATH);
  assert(path.dirname(fixtureReal) === artifactReal, "Fixture JSON must be inside artifact directory");
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function databaseSnapshot() {
  const output = execFileSync(
    path.join(ROOT, ".venv", "bin", "python"),
    [path.join(ROOT, "scripts", "seed_complete_journeys_e2e.py"), "--snapshot"],
    { cwd: ROOT, encoding: "utf8", env: process.env },
  );
  return JSON.parse(output.trim()).database_snapshot;
}

function preparePhaseTwoFixtures() {
  const output = execFileSync(
    path.join(ROOT, ".venv", "bin", "python"),
    [path.join(ROOT, "scripts", "seed_complete_journeys_e2e.py"), "--prepare-phase-two"],
    { cwd: ROOT, encoding: "utf8", env: process.env },
  );
  return JSON.parse(output.trim());
}

function runPhaseTwoMaintenance() {
  const output = execFileSync(
    path.join(ROOT, ".venv", "bin", "python"),
    [path.join(ROOT, "scripts", "seed_complete_journeys_e2e.py"), "--phase-two-maintenance"],
    { cwd: ROOT, encoding: "utf8", env: process.env },
  );
  return JSON.parse(output.trim());
}

function runPhaseTwoPreferenceDelivery() {
  const output = execFileSync(
    path.join(ROOT, ".venv", "bin", "python"),
    [path.join(ROOT, "scripts", "seed_complete_journeys_e2e.py"), "--phase-two-preference-delivery"],
    { cwd: ROOT, encoding: "utf8", env: process.env },
  );
  return JSON.parse(output.trim());
}

function phaseOneAuditExpectedEvents(loginCount) {
  return [
    [10, "DELETE", "/api/gardens/{garden_id}/map-objects/{public_id}", 200],
    [2, "DELETE", "/api/gardens/{garden_id}/map-objects/{public_id}/units/{public_id}", 200],
    [3, "DELETE", "/api/plants/{created_plant_id}", 200],
    [2, "DELETE", "/api/plots/OPT-JOURNEY-A-PLOT/plants/{created_plant_id}", 204],
    [1, "DELETE", "/api/plots/P1EDITORASSIGN/plants/{created_plant_id}", 204],
    [1, "DELETE", "/api/plots/P1MOBILEPLOTEDITED", 204],
    [3, "DELETE", "/api/saved-views/{saved_view_id}", 200],
    [1, "DELETE", "/api/snapshots/{public_id}", 200],
    [7, "PATCH", "/api/gardens/{garden_id}/map-objects/{public_id}", 200],
    [1, "PATCH", "/api/gardens/{garden_id}/map-objects/obj_optimization_journeys_a", 200],
    [2, "PATCH", "/api/gardens/{garden_id}/map-objects/{public_id}/units/{public_id}", 200],
    [4, "PATCH", "/api/gardens/{garden_id}/settings", 200],
    [6, "PATCH", "/api/layout-state", 200],
    [6, "PATCH", "/api/plants/{created_plant_id}", 200],
    [1, "PATCH", "/api/plots/P1MOBILEPLOT", 200],
    [4, "PATCH", "/api/plots/COMPLETE-PHASE-ONE-INDOOR/plants/COMPLETE-PHASE-ONE-BASIL", 200],
    [loginCount, "POST", "/api/auth/login", 200],
    [9, "POST", "/api/auth/reauthenticate", 200],
    [2, "POST", "/api/gardens/{garden_id}/complete-onboarding", 200],
    [10, "POST", "/api/gardens/{garden_id}/map-objects", 201],
    [4, "POST", "/api/gardens/{garden_id}/map-objects/{public_id}/units", 201],
    [2, "POST", "/api/gardens", 201],
    [1, "POST", "/api/harvest", 201],
    [3, "POST", "/api/plants", 201],
    [1, "POST", "/api/plots", 201],
    [4, "POST", "/api/plots/OPT-JOURNEY-A-PLOT/plants/{created_plant_id}", 201],
    [2, "POST", "/api/plots/P1EDITORASSIGN/plants/{created_plant_id}", 201],
    [2, "POST", "/api/plots/import", 200],
    [1, "POST", "/api/plots/import", 409],
    [3, "POST", "/api/plots/import", 422],
    [3, "POST", "/api/saved-views", 201],
    [2, "POST", "/api/snapshots", 201],
    [2, "POST", "/api/snapshots/{public_id}/restore", 200],
  ].map(([count, method, path, status_code]) => ({ count, method, path, status_code }));
}

function auditEventKey({ method, path: eventPath, status_code: statusCode }) {
  return `${method} ${statusCode} ${eventPath}`;
}

function assertPhaseOneAuditContract(auditState, loginCount) {
  assert(Array.isArray(auditState?.events), "Phase 1 audit events are missing");
  const expectedEvents = phaseOneAuditExpectedEvents(loginCount);
  const expectedByKey = new Map(expectedEvents.map((event) => [auditEventKey(event), event.count]));
  const flexibleReadEventKeys = new Set([
    auditEventKey({ method: "POST", path: "/api/media/summaries", status_code: 200 }),
  ]);
  const observedByKey = new Map();
  for (const event of auditState.events) {
    const key = auditEventKey(event);
    assert(!observedByKey.has(key), `Phase 1 audit event was duplicated: ${key}`);
    assert(Number.isSafeInteger(event.count) && event.count > 0, `Invalid Phase 1 audit count: ${key}`);
    assert(
      expectedByKey.has(key) || flexibleReadEventKeys.has(key),
      `Unexpected Phase 1 audit event: ${key}`,
    );
    observedByKey.set(key, event.count);
  }
  for (const [key, expectedCount] of expectedByKey) {
    assert(
      observedByKey.get(key) === expectedCount,
      `Phase 1 audit event count was unexpected for ${key}`,
    );
  }
  return {
    expected_event_types: expectedByKey.size,
    flexible_read_event_types: flexibleReadEventKeys.size,
    unexpected_count: 0,
  };
}

function directoryState(directory) {
  const entries = fs.readdirSync(directory, { withFileTypes: true });
  return {
    empty: entries.length === 0,
    entries: entries.map((entry) => entry.name).sort(),
  };
}

function filesystemState() {
  return {
    artifacts: fs.readdirSync(ARTIFACT_DIR).sort(),
    downloads: directoryState(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_DOWNLOAD_DIR),
    media: directoryState(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_MEDIA_DIR),
    terrain: directoryState(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_TERRAIN_DIR),
  };
}

function safeFailure(error) {
  let message = error instanceof Error ? error.message : String(error);
  const redactions = [PASSWORD, process.env.DATABASE_URL || "", process.env.GARDENOPS_DISPOSABLE_POSTGRES_URL || ""];
  for (const value of redactions) {
    if (value) message = message.split(value).join("[redacted]");
  }
  return sanitizeDiagnostic(redactTokenShapedSecrets(message));
}

function writePrivateFailure(error) {
  const logDirectory = process.env.GARDENOPS_LOGS_DIR || "";
  if (!logDirectory) return;
  let detail = error instanceof Error
    ? (error.stack || `${error.name}: ${error.message}`)
    : String(error);
  const redactions = [
    PASSWORD,
    process.env.DATABASE_URL || "",
    process.env.GARDENOPS_DISPOSABLE_POSTGRES_URL || "",
  ];
  for (const value of redactions) {
    if (value) detail = detail.split(value).join("[redacted]");
  }
  detail = redactTokenShapedSecrets(detail);
  try {
    const failureLog = path.join(logDirectory, "complete-journeys-browser-error.log");
    fs.appendFileSync(
      failureLog,
      `${new Date().toISOString()} ${detail}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
    fs.chmodSync(failureLog, 0o600);
  } catch {
    // Failure evidence is best effort and must never replace the original error.
  }
}

function safeIdentifier(value) {
  const text = String(value || "");
  return redactTokenShapedSecrets(text) === text && /^[A-Za-z0-9_.-]{1,100}$/.test(text)
    ? text
    : sanitizeDiagnostic(text);
}

function safeUtcTimestamp(value) {
  const text = String(value || "");
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{3}))?Z$/.exec(text);
  if (!match) return safeIdentifier(text);
  const timestamp = Date.parse(text);
  const canonical = `${match[1]}.${match[2] || "000"}Z`;
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString() === canonical
    ? text
    : safeIdentifier(text);
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function isElfExecutable(filePath) {
  try {
    const descriptor = fs.openSync(filePath, "r");
    try {
      const magic = Buffer.alloc(4);
      return fs.readSync(descriptor, magic, 0, magic.length, 0) === magic.length
        && magic.equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46]));
    } finally {
      fs.closeSync(descriptor);
    }
  } catch {
    return false;
  }
}

function resolveChromiumExecutable(launcherPath) {
  const candidates = [];
  if (fs.existsSync(launcherPath)) candidates.push(fs.realpathSync.native(launcherPath));
  candidates.push(
    "/usr/lib/chromium/chromium",
    "/usr/lib/chromium-browser/chromium-browser",
  );
  return candidates.find((candidate) => isElfExecutable(candidate)) || launcherPath;
}

function sha256File(filePath) {
  const digest = crypto.createHash("sha256");
  const descriptor = fs.openSync(filePath, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    for (;;) {
      const count = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (count === 0) break;
      digest.update(buffer.subarray(0, count));
    }
  } finally {
    fs.closeSync(descriptor);
  }
  return digest.digest("hex");
}

function fileBinding(filePath) {
  const stat = fs.statSync(filePath);
  assert(stat.isFile(), `Evidence binding is not a file: ${path.basename(filePath)}`);
  return {
    sha256: sha256File(filePath),
    size_bytes: stat.size,
  };
}

function resolvedExecutableBinding(filePath) {
  const resolvedPath = fs.realpathSync.native(filePath);
  assert(resolvedPath === filePath, "Chromium executable binding must use its resolved path");
  assert(isElfExecutable(resolvedPath), "Chromium executable binding must identify an ELF binary");
  return {
    ...fileBinding(resolvedPath),
    resolved_regular_file: true,
  };
}

function evidenceBinding() {
  const packageJson = readJson(path.join(ROOT, "frontend", "package.json"));
  const packageLockPath = path.join(ROOT, "frontend", "package-lock.json");
  const packageLock = readJson(packageLockPath);
  const uvLockPath = path.join(ROOT, "uv.lock");
  const uvLock = fs.readFileSync(uvLockPath, "utf8");
  const uvLockVersionMatch = /^version\s*=\s*(?:"([^"]+)"|([0-9]+))/m.exec(uvLock);
  const uvLockVersion = uvLockVersionMatch?.[1] || uvLockVersionMatch?.[2] || "";
  return {
    fixture: fileBinding(FIXTURE_PATH),
    lockfiles: {
      frontend_package_lock: {
        ...fileBinding(packageLockPath),
        format_version: packageLock.lockfileVersion,
      },
      uv_lock: {
        ...fileBinding(uvLockPath),
        format_version: uvLockVersion,
      },
    },
    runtime: {
      node_version: process.version,
      platform: process.platform,
      architecture: process.arch,
      frontend_package_version: String(packageJson.version || ""),
      playwright_core_version: String(packageJson.devDependencies?.["playwright-core"] || ""),
      chromium_launcher: fileBinding(fs.realpathSync.native(CHROMIUM_LAUNCHER)),
      chromium_executable: resolvedExecutableBinding(CHROMIUM_EXECUTABLE),
      chromium_version: null,
    },
  };
}

function isSafeRequestId(value) {
  return /^[A-Za-z0-9._-]{1,64}$/.test(String(value || ""));
}

function canonicalProjectionDigests(manifest) {
  const projection = {
    database: manifest.database,
    evidence_binding: manifest.evidence_binding,
    profiles: manifest.profiles,
    trace_artifacts: manifest.trace_artifacts,
  };
  const auditProjection = projection.database?.audit_projection;
  if (manifest.status === "passed") {
    assert(
      auditProjection && typeof auditProjection === "object"
        && Array.isArray(auditProjection.events),
      "Passed manifest is missing its sanitized audit projection",
    );
  }
  return {
    audit_snapshot: auditProjection && typeof auditProjection === "object"
      ? sha256(canonicalJson(auditProjection))
      : null,
    final_database: sha256(canonicalJson(projection.database)),
    final_projection: sha256(canonicalJson(projection)),
    profiles: sha256(canonicalJson(projection.profiles)),
  };
}

function auditManifestProjection(auditState) {
  assert(auditState && typeof auditState === "object", "Audit manifest projection is missing");
  assert(Array.isArray(auditState.events), "Audit manifest event histogram is missing");
  for (const [label, value] of [
    ["expected login count", auditState.expected_login_count],
    ["expected Phase 1 snapshot count", auditState.expected_phase_one_snapshot_count],
    ["total count", auditState.total_count],
  ]) {
    assert(Number.isSafeInteger(value) && value >= 0,
      `Audit manifest ${label} is invalid`);
  }
  const events = auditState.events.map((event) => {
    assert(Number.isSafeInteger(event?.count) && event.count > 0,
      "Audit manifest event count is invalid");
    assert(["DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"].includes(event?.method),
      "Audit manifest event method is invalid");
    assert(isSafeAuditProjectionPath(event?.path), "Audit manifest event path is invalid");
    assert(Number.isSafeInteger(event?.status_code)
      && event.status_code >= 100 && event.status_code <= 599,
    "Audit manifest event status is invalid");
    return {
      count: event.count,
      method: event.method,
      path: event.path,
      status_code: event.status_code,
    };
  }).sort((left, right) => auditEventKey(left).localeCompare(auditEventKey(right)));
  const projection = {
    events,
    expected_login_count: auditState.expected_login_count,
    expected_phase_one_snapshot_count: auditState.expected_phase_one_snapshot_count,
    total_count: auditState.total_count,
  };
  assert(
    projection.events.reduce((total, event) => total + event.count, 0)
      === projection.total_count,
    "Audit manifest projection total disagrees with its event histogram",
  );
  return projection;
}

function isSafeAuditProjectionPath(value) {
  const pathname = String(value || "");
  return pathname === "/api/media/summaries"
    || phaseOneAuditExpectedEvents(0).some((event) => event.path === pathname)
    || isPhaseTwoAuditPath(pathname);
}

function assertWholeTableProjectionCoverage(initial, final, allowedTables) {
  assert(initial && typeof initial === "object", "Initial whole-table projection is missing");
  assert(final && typeof final === "object", "Final whole-table projection is missing");
  const initialTables = Object.keys(initial).sort();
  const finalTables = Object.keys(final).sort();
  assert(canonicalJson(initialTables) === canonicalJson(finalTables),
    "Whole-table projection coverage changed during the run");
  for (const table of allowedTables) {
    assert(Object.hasOwn(initial, table) && Object.hasOwn(final, table),
      `Allowed table lacks a whole-table projection: ${table}`);
  }
  for (const [boundary, projection] of [["initial", initial], ["final", final]]) {
    for (const [table, evidence] of Object.entries(projection)) {
      assert(Number.isSafeInteger(evidence?.count) && evidence.count >= 0,
        `${boundary} whole-table count is invalid: ${table}`);
      assert(/^[a-f0-9]{32}$/.test(String(evidence?.digest || "")),
        `${boundary} whole-table digest is invalid: ${table}`);
    }
  }
  return {
    all_public_domain_tables_projected: true,
    projected_table_count: finalTables.length,
  };
}

function gitOutput(args) {
  try {
    return execFileSync("git", args, { cwd: ROOT, encoding: "buffer" });
  } catch (error) {
    if (error?.status === 0 && Buffer.isBuffer(error.stdout)) return error.stdout;
    throw error;
  }
}

function gitState() {
  const status = gitOutput(["status", "--porcelain=v1", "--untracked-files=all"]);
  const indexDiff = gitOutput(["diff", "--no-ext-diff", "--binary", "--cached", "HEAD"]);
  const worktreeDiff = gitOutput(["diff", "--no-ext-diff", "--binary", "HEAD"]);
  const untrackedPaths = gitOutput(["ls-files", "--others", "--exclude-standard", "-z"])
    .toString("utf8").split("\0").filter(Boolean).sort();
  const fingerprint = crypto.createHash("sha256");
  for (const value of [status, indexDiff, worktreeDiff]) {
    fingerprint.update(value);
    fingerprint.update("\0");
  }
  for (const relativePath of untrackedPaths) {
    const candidate = path.join(ROOT, relativePath);
    fingerprint.update(relativePath);
    fingerprint.update("\0");
    let kind = "missing";
    let content = Buffer.alloc(0);
    try {
      const candidateStat = fs.lstatSync(candidate);
      if (candidateStat.isFile()) {
        kind = "file";
        content = fs.readFileSync(candidate);
      } else if (candidateStat.isSymbolicLink()) {
        kind = "symlink";
        content = Buffer.from(fs.readlinkSync(candidate));
      } else {
        kind = "other";
      }
    } catch {
      // A concurrent create/remove remains evidence of a changed source tree.
    }
    fingerprint.update(kind);
    fingerprint.update("\0");
    fingerprint.update(content);
    fingerprint.update("\0");
  }
  const sha = gitOutput(["rev-parse", "HEAD"]).toString("utf8").trim();
  return {
    dirty: Boolean(status.toString("utf8").trim()),
    sha,
    worktree_fingerprint: fingerprint.digest("hex"),
  };
}

function sourceProvenance(state) {
  const dirty = Boolean(state?.dirty);
  const sha = String(state?.sha || "");
  return {
    ...state,
    clean: !dirty,
    dirty,
    final_head: sha,
    sha,
  };
}

function assertSourceRevisionStable(initial, final) {
  assert(initial && typeof initial === "object", "Fixture has no initial source provenance");
  assert(final && typeof final === "object", "Run has no final source provenance");
  assert(initial.sha === final.sha, "Source revision changed during journey run");
  assert(!initial.dirty && !final.dirty,
    "Complete journey provenance requires a clean source worktree");
  assert(Boolean(initial.dirty) === Boolean(final.dirty), "Source cleanliness changed during journey run");
  assert(
    typeof initial.worktree_fingerprint === "string"
      && initial.worktree_fingerprint === final.worktree_fingerprint,
    "Source worktree changed during journey run",
  );
}

function safeNonnegativeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function sanitizeCheckValue(value, depth = 0) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") return safeIdentifier(value);
  if (depth >= 4 || value === null || typeof value !== "object") return undefined;
  if (Array.isArray(value)) {
    return value.slice(0, 100).flatMap((item) => {
      const sanitized = sanitizeCheckValue(item, depth + 1);
      return sanitized === undefined ? [] : [sanitized];
    });
  }
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => /^[a-z0-9_-]{1,80}$/.test(key))
    .flatMap(([key, item]) => {
      const sanitized = sanitizeCheckValue(item, depth + 1);
      return sanitized === undefined ? [] : [[key, sanitized]];
    }));
}

function sanitizeDatabaseEvidence(value, depth = 0) {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : sanitizeDiagnostic(value);
  if (typeof value === "string") {
    return redactTokenShapedSecrets(value) === value && /^[A-Za-z0-9_.:-]{1,160}$/.test(value)
      ? value
      : sanitizeDiagnostic(value);
  }
  if (depth >= 8 || !value || typeof value !== "object") return sanitizeDiagnostic("invalid database evidence");
  if (Array.isArray(value)) {
    return value.slice(0, 500).map((item) => sanitizeDatabaseEvidence(item, depth + 1));
  }
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => /^[a-z0-9_-]{1,100}$/i.test(key))
    .map(([key, item]) => [key, sanitizeDatabaseEvidence(item, depth + 1)]));
}

function sanitizeTraceEvidence(value) {
  if (typeof value === "string") {
    return { name: safeIdentifier(value), sha256: null };
  }
  const name = safeIdentifier(value?.name);
  const sha256 = String(value?.sha256 || "");
  return {
    name,
    sha256: /^[a-f0-9]{64}$/.test(sha256) ? sha256 : null,
  };
}

function assertTraceArtifacts(profiles, artifactDirectory = ARTIFACT_DIR) {
  assert(Array.isArray(profiles), "Trace evidence profiles are missing");
  const artifactRoot = fs.realpathSync.native(artifactDirectory);
  return profiles.map((profile) => {
    const trace = profile?.trace;
    assert(trace && typeof trace === "object", "Browser profile has no hashed trace evidence");
    const name = String(trace.name || "");
    const sha256 = String(trace.sha256 || "");
    assert(
      /^[a-z0-9-]{1,160}-(?:passed|failed)\.zip$/.test(name),
      "Trace artifact name is invalid",
    );
    assert(/^[a-f0-9]{64}$/.test(sha256), "Trace artifact SHA-256 is invalid");
    const tracePath = path.join(artifactRoot, name);
    const relative = path.relative(artifactRoot, tracePath);
    assert(relative && !relative.startsWith("..") && !path.isAbsolute(relative), "Trace artifact escaped run directory");
    const stat = fs.lstatSync(tracePath);
    assert(stat.isFile() && !stat.isSymbolicLink() && stat.nlink === 1,
      "Trace artifact must be a regular, single-link file");
    const observed = crypto.createHash("sha256").update(fs.readFileSync(tracePath)).digest("hex");
    assert(observed === sha256, `Trace artifact hash mismatch: ${name}`);
    return { name, sha256 };
  });
}

function backendErrorEvidence(logDirectory = process.env.GARDENOPS_LOGS_DIR || "") {
  assert(logDirectory, "Complete journey backend log directory is required");
  const backendLogPath = path.join(logDirectory, "backend.log");
  const structuredLogPath = path.join(logDirectory, "errors.jsonl");
  assert(fs.existsSync(backendLogPath), "Complete journey backend log is missing");
  assert(fs.existsSync(structuredLogPath), "Complete journey structured error log is missing");
  const backendLevels = fs.readFileSync(backendLogPath, "utf8")
    .split(/\r?\n/)
    .reduce((counts, line) => {
      for (const level of ["ERROR", "CRITICAL", "FATAL"]) {
        if (new RegExp(`\\b${level}\\b`).test(line)) counts[level] += 1;
      }
      return counts;
    }, { CRITICAL: 0, ERROR: 0, FATAL: 0 });
  const structuredLevels = fs.readFileSync(structuredLogPath, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .reduce((counts, line) => {
      try {
        const level = String(JSON.parse(line).level || "").toUpperCase();
        if (["ERROR", "CRITICAL", "FATAL"].includes(level)) counts[level] += 1;
      } catch {
        counts.ERROR += 1;
      }
      return counts;
    }, { CRITICAL: 0, ERROR: 0, FATAL: 0 });
  return {
    backend_critical_lines: backendLevels.CRITICAL,
    backend_error_lines: backendLevels.ERROR,
    backend_fatal_lines: backendLevels.FATAL,
    structured_critical_entries: structuredLevels.CRITICAL,
    structured_error_entries: structuredLevels.ERROR,
    structured_fatal_entries: structuredLevels.FATAL,
    unexpected_error_count: Object.values(backendLevels).reduce((sum, count) => sum + count, 0)
      + Object.values(structuredLevels).reduce((sum, count) => sum + count, 0),
  };
}

function assertNoUnexpectedBackendErrors(logDirectory, evidence = backendErrorEvidence(logDirectory)) {
  assert(
    evidence.unexpected_error_count === 0,
    "Unexpected backend ERROR, CRITICAL, or FATAL log entries; inspect private runner logs",
  );
  return evidence;
}

function assertExactPhaseOneOnboardingOwnership(targets, expectedByGarden) {
  assert(Array.isArray(targets), "Phase 1 onboarding target gardens are missing");
  const expectedNames = Object.keys(expectedByGarden).sort();
  assert(targets.length === expectedNames.length, "Onboarding did not create the expected target gardens");
  const observedByName = new Map();
  for (const target of targets) {
    assert(!observedByName.has(target.name), `Onboarding created duplicate garden ${target.name}`);
    observedByName.set(target.name, target);
  }
  assert(
    JSON.stringify([...observedByName.keys()].sort()) === JSON.stringify(expectedNames),
    "Onboarding created an unexpected target garden",
  );
  for (const [name, expected] of Object.entries(expectedByGarden)) {
    assert(expected && typeof expected === "object", `Onboarding expectation is invalid for ${name}`);
    const target = observedByName.get(name);
    assert(target?.onboarding_complete === true, `Onboarding did not complete ${name}`);
    assert(target.owner_username === expected.owner_username, `Onboarding owner mismatch for ${name}`);
    assert(
      canonicalJson(target.memberships) === canonicalJson([
        { role: "admin", username: expected.owner_username },
      ]),
      `Onboarding membership mismatch for ${name}`,
    );
    for (const field of ["address", "grid_cols", "grid_rows", "latitude", "longitude"]) {
      assert(target[field] === expected[field], `Onboarding ${field} mismatch for ${name}`);
    }
    assert(
      canonicalJson(target.layout) === canonicalJson(expected.layout),
      `Onboarding layout configuration mismatch for ${name}`,
    );
  }
}

function assertNoCrossGardenLinks(links, label) {
  assert(links && typeof links === "object", `${label} cross-garden evidence is missing`);
  for (const [key, count] of Object.entries(links)) {
    assert(count === 0, `${label} retained cross-garden link: ${key}`);
  }
}

function assertNoLifecycleResidue(lifecycle, label) {
  assert(lifecycle && typeof lifecycle === "object", `${label} lifecycle evidence is missing`);
  for (const [key, count] of Object.entries(lifecycle)) {
    assert(count === 0, `${label} retained lifecycle record: ${key}`);
  }
}

function assertPhaseZeroProfileEvidence(profiles) {
  assert(Array.isArray(profiles), "Phase 0 browser profile evidence is missing");
  const expectedProfiles = ["desktop", "mobile"];
  assert(profiles.length === expectedProfiles.length, "Phase 0 browser profile count was unexpected");
  const byProfile = new Map();
  for (const profile of profiles) {
    assert(!byProfile.has(profile?.profile), `Phase 0 browser profile was duplicated: ${profile?.profile}`);
    byProfile.set(profile?.profile, profile);
  }
  for (const profileName of expectedProfiles) {
    const profile = byProfile.get(profileName);
    assert(profile, `Phase 0 browser profile is missing: ${profileName}`);
    assert(profile.role === "admin", `Phase 0 role was unexpected: ${profileName}`);
    assert(profile.failure === null, `Phase 0 browser profile failed: ${profileName}`);
    assert((profile.assertions?.failed || []).length === 0, `Phase 0 assertions failed: ${profileName}`);
    assert(profile.checks?.auth_session === true, `Phase 0 session check is missing: ${profileName}`);
    assert(
      profile.checks?.garden_a_b_a === true,
      `Phase 0 A/B/A check is missing: ${profileName}`,
    );
    assert(
      profile.checks?.garden_scoped_notifications === true,
      `Phase 0 notification scope check is missing: ${profileName}`,
    );
    assert(
      profile.checks?.map_first_without_plants === true,
      `Phase 0 map-first check is missing: ${profileName}`,
    );
    assert(
      profile.checks?.browser_diagnostics === true,
      `Phase 0 browser diagnostics missing: ${profileName}`,
    );
    assert(
      profile.browser_profile?.is_mobile === (profileName === "mobile"),
      `Phase 0 browser device evidence was unexpected: ${profileName}`,
    );
  }
  return {
    expected_profile_count: expectedProfiles.length,
    profile_matrix_enforced: true,
  };
}

function assertPhaseOneProfileEvidence(profiles) {
  assert(Array.isArray(profiles), "Phase 1 browser profile evidence is missing");
  const expectedProfiles = [
    { profile: "desktop", role: "onboarding", checks: ["onboarding_validation_recovery_complete"] },
    { profile: "mobile", role: "onboarding", checks: ["onboarding_validation_recovery_complete"] },
    {
      profile: "desktop",
      role: "admin",
      checks: [
        "desktop_admin_mutation_workflows",
        "indoor_reload_persistence",
        "saved_view_delete_confirmation",
        "role_cross_garden_response_isolation",
      ],
    },
    {
      profile: "mobile",
      role: "admin",
      checks: [
        "garden_settings_reload_persistence",
        "indoor_reload_persistence",
        "mobile_supported_writes_and_focus_return",
        "saved_view_delete_confirmation",
        "role_cross_garden_response_isolation",
      ],
    },
    {
      profile: "desktop",
      role: "editor",
      checks: [
        "editor_profile_write_affordances_and_admin_denial",
        "editor_m1_m3_supported_writes",
        "editor_a3_settings_and_m4_layout_write",
        "editor_settings_layout_reload_persistence",
        "saved_view_delete_confirmation",
        "role_cross_garden_response_isolation",
      ],
    },
    {
      profile: "mobile",
      role: "editor",
      checks: [
        "editor_profile_write_affordances_and_admin_denial",
        "mobile_editor_plot_edit_workflow",
        "role_cross_garden_response_isolation",
      ],
    },
    {
      profile: "desktop",
      role: "viewer",
      checks: [
        "viewer_role_affordances_and_denials",
        "viewer_m1_m3_read_only_behavior",
        "viewer_a3_m4_write_unavailable",
        "role_cross_garden_response_isolation",
      ],
    },
    {
      profile: "mobile",
      role: "viewer",
      checks: [
        "viewer_role_affordances_and_denials",
        "viewer_m1_m3_read_only_behavior",
        "role_cross_garden_response_isolation",
      ],
    },
  ];
  assert(profiles.length === expectedProfiles.length, "Phase 1 browser profile count was unexpected");
  const byKey = new Map();
  for (const profile of profiles) {
    const key = `${profile?.role}:${profile?.profile}`;
    assert(!byKey.has(key), `Phase 1 browser profile was duplicated: ${key}`);
    byKey.set(key, profile);
  }
  for (const expected of expectedProfiles) {
    const key = `${expected.role}:${expected.profile}`;
    const profile = byKey.get(key);
    assert(profile, `Phase 1 browser profile is missing: ${key}`);
    assert(profile.failure === null, `Phase 1 browser profile failed: ${key}`);
    assert((profile.assertions?.failed || []).length === 0, `Phase 1 assertions failed: ${key}`);
    assert((profile.assertions?.skipped || []).length === 0, `Phase 1 assertions were skipped: ${key}`);
    assert(profile.checks?.browser_diagnostics === true, `Phase 1 browser diagnostics missing: ${key}`);
    const expectedMobile = expected.profile === "mobile";
    assert(
      profile.browser_profile?.is_mobile === expectedMobile,
      `Phase 1 browser device evidence was unexpected: ${key}`,
    );
    assert(
      profile.browser_profile?.has_touch === expectedMobile
        && (expectedMobile
          ? profile.browser_profile?.max_touch_points > 0
          : profile.browser_profile?.max_touch_points === 0),
      `Phase 1 runtime touch evidence was unexpected: ${key}`,
    );
    for (const check of expected.checks) {
      assert(profile.checks?.[check] === true, `Phase 1 browser check is missing: ${key}:${check}`);
    }
    if (expected.role !== "onboarding") {
      assert(profile.checks?.map_first_without_plants === true, `Phase 1 map-first check is missing: ${key}`);
    }
  }
  const desktopAdmin = byKey.get("admin:desktop");
  const importEvidence = desktopAdmin.checks?.import_rejection_render_churn;
  const rejectedImports = importEvidence?.rejected_import_render_churn;
  assert(
    rejectedImports && typeof rejectedImports === "object"
      && ["cross_garden", "malformed_json", "oversized", "structurally_incomplete", "unsupported_schema"]
        .every((key) => rejectedImports[key] && typeof rejectedImports[key] === "object"),
    "Phase 1 rejected-import render evidence is missing",
  );
  const malformedImport = rejectedImports.malformed_json;
  assert(
    malformedImport.client_error_visible === true
      && malformedImport.import_request_count === 0
      && malformedImport.input_cleared === true
      && malformedImport.render_churn?.attributes === 0
      && malformedImport.render_churn?.child_lists === 0
      && malformedImport.render_churn?.added === 0
      && malformedImport.render_churn?.removed === 0,
    "Phase 1 malformed JSON import was not rejected cleanly before the network boundary",
  );
  const transitions = importEvidence?.successful_map_state_transitions;
  const divergentImport = transitions?.divergent_import;
  const snapshotRestore = transitions?.snapshot_restore;
  assert(
    divergentImport && typeof divergentImport === "object"
      && typeof divergentImport.target_plot_id === "string"
      && divergentImport.target_plot_id.length > 0
      && [divergentImport.imported_cell, divergentImport.original_cell]
        .every((cell) => cell && Number.isSafeInteger(cell.row) && Number.isSafeInteger(cell.col)),
    "Phase 1 successful divergent-import evidence is missing",
  );
  assert(
    divergentImport.imported_cell.row !== divergentImport.original_cell.row
      || divergentImport.imported_cell.col !== divergentImport.original_cell.col,
    "Phase 1 successful import did not change the target plot cell",
  );
  assert(
    snapshotRestore && typeof snapshotRestore === "object"
      && Number.isSafeInteger(snapshotRestore.mutation_count)
      && snapshotRestore.mutation_count > 0
      && snapshotRestore.replace_children_calls === 1
      && canonicalJson(snapshotRestore.restored_render_counts)
        === canonicalJson(snapshotRestore.snapshot_render_counts),
    "Phase 1 snapshot restore render evidence is incomplete",
  );
  for (const key of ["admin:desktop", "admin:mobile"]) {
    const profile = byKey.get(key);
    const delayed = profile.checks?.delayed_surfaces;
    const alphaRequired = [
      "admin-settings",
      "indoor",
      "layout",
      "map-objects",
      "notifications",
      "plants",
      "plot-alerts",
      "plots",
      "saved-views",
      "weather",
    ];
    const betaRequired = alphaRequired;
    const raceEvidence = delayed?.per_surface;
    assert(
      delayed && typeof delayed === "object"
        && Array.isArray(delayed.alpha_started_surfaces)
        && Array.isArray(delayed.beta_held_surfaces)
        && Number.isSafeInteger(delayed.beta_held_response_count)
        && delayed.beta_held_response_count >= betaRequired.length
        && alphaRequired.every((surface) => delayed.alpha_started_surfaces.includes(surface))
        && betaRequired.every((surface) => delayed.beta_held_surfaces.includes(surface))
        && raceEvidence && typeof raceEvidence === "object"
        && canonicalJson(Object.keys(raceEvidence).sort()) === canonicalJson(betaRequired.slice().sort())
        && betaRequired.every((surface) => {
          const evidence = raceEvidence[surface];
          return evidence && typeof evidence === "object"
            && evidence.alpha_target_started === true
            && evidence.beta_content_never_landed === true
            && evidence.beta_response_arrived === true
            && Number.isSafeInteger(evidence.beta_response_completion_count)
            && evidence.beta_response_completion_count >= 1
            && evidence.beta_target_held === true
            && evidence.network_guard_reached === true
            && ["controlled", "physical"].includes(evidence.alpha_selection_mode)
            && ["automatic", "controlled", "physical"].includes(evidence.alpha_trigger_mode)
            && ["automatic", "controlled", "physical"].includes(evidence.beta_trigger_mode);
        }),
      `Phase 1 delayed A/B/A evidence is incomplete: ${key}`,
    );
    if (profile.profile === "desktop") {
      assert(
        canonicalJson(delayed.admin_settings_draft_isolation) === canonicalJson({
          alpha_draft_restored_after_background_load: true,
          baseline_restored_without_persisting: true,
          beta_never_received_alpha_draft: true,
        }),
        "Phase 1 desktop delayed settings-draft isolation evidence is incomplete",
      );
    } else {
      assert(
        delayed.admin_settings_draft_isolation === undefined,
        "Phase 1 mobile race unexpectedly claimed desktop settings-draft evidence",
      );
    }
  }
  for (const key of ["editor:desktop", "editor:mobile", "viewer:desktop", "viewer:mobile"]) {
    const profile = byKey.get(key);
    const delayed = profile.checks?.role_delayed_surfaces;
    const evidence = delayed?.per_surface?.plots;
    assert(
      profile.checks?.role_cross_garden_response_isolation === true
        && canonicalJson(delayed?.alpha_started_surfaces) === canonicalJson(["plots"])
        && canonicalJson(delayed?.beta_held_surfaces) === canonicalJson(["plots"])
        && Number.isSafeInteger(delayed?.beta_held_response_count)
        && delayed.beta_held_response_count >= 1
        && evidence?.alpha_target_started === true
        && evidence?.beta_content_never_landed === true
        && evidence?.beta_response_arrived === true
        && Number.isSafeInteger(evidence?.beta_response_completion_count)
        && evidence.beta_response_completion_count >= 1
        && evidence?.beta_target_held === true
        && evidence?.network_guard_reached === true,
      `Phase 1 role delayed A/B/A evidence is incomplete: ${key}`,
    );
  }
  return {
    expected_profile_count: expectedProfiles.length,
    profile_matrix_enforced: true,
  };
}

async function freezeReadOnlyProbeClock(context, nowMs) {
  await context.addInitScript((frozenNowMs) => {
    const RealDate = Date;
    function FrozenDate(...args) {
      if (new.target) {
        return args.length === 0 ? new RealDate(frozenNowMs) : new RealDate(...args);
      }
      return new RealDate(frozenNowMs).toString();
    }
    Object.setPrototypeOf(FrozenDate, RealDate);
    FrozenDate.prototype = RealDate.prototype;
    FrozenDate.now = () => frozenNowMs;
    FrozenDate.parse = RealDate.parse;
    FrozenDate.UTC = RealDate.UTC;
    globalThis.Date = FrozenDate;
  }, nowMs);
}

async function runPhaseTwoReadOnlyPermutation({
  artifactDir,
  baseUrl,
  browser,
  devices,
  fixture,
  onProfile,
  password,
  username,
}) {
  const credentials = {
    admin: { password, username },
    editor: { password: PHASE_TWO_EDITOR_PASSWORD, username: fixture.roles.editor },
    viewer: { password: PHASE_TWO_VIEWER_PASSWORD, username: fixture.roles.viewer },
  };
  for (const [index, key] of PHASE_TWO_READ_ONLY_PERMUTATION_ORDER.entries()) {
    const [role, profileName] = key.split(":");
    const guarded = await createGuardedContext(
      browser,
      devices,
      profileName,
      artifactDir,
      `phase-two-read-only-${String(index + 1).padStart(2, "0")}-${role}-${profileName}`,
      { baseUrl },
    );
    await freezeReadOnlyProbeClock(guarded.context, fixture.clock.attention_now_ms);
    const page = await guarded.context.newPage();
    const recorder = createApiRecorder(page, {
      authType: "session",
      role,
      username: credentials[role].username,
    });
    const result = {
      assertions: { failed: [], passed: [], skipped: [] },
      browser_profile: guarded.profile,
      checks: {
        execution_model: "fresh-context-read-only-permutation",
        probe_sequence: index + 1,
      },
      diagnostics: null,
      failure: null,
      profile: profileName,
      requests: [],
      role,
      trace: null,
    };
    let caughtError = null;
    let status = "failed";
    try {
      await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
      const authenticatedUser = await authenticate(
        page,
        credentials[role].username,
        credentials[role].password,
      );
      guarded.markAuthenticated();
      assert(authenticatedUser.role === role,
        `Phase 2 read-only probe role was unexpected: ${key}`);
      result.browser_profile.user_agent = await page.evaluate(() => navigator.userAgent);
      result.browser_profile.max_touch_points = await page.evaluate(() => navigator.maxTouchPoints);
      result.browser_profile.has_touch = result.browser_profile.max_touch_points > 0;
      result.browser_profile.viewport = page.viewportSize();
      result.browser_profile.user_agent_contract = assertBrowserProfileContract(
        profileName,
        result.browser_profile,
      );
      await page.locator("#map-grid").waitFor({ state: "visible", timeout: 15000 });
      const scopedReads = await page.evaluate(async (gardenId) => {
        const responses = await Promise.all([
          fetch("/api/attention/today", {
            credentials: "include",
            headers: { "x-garden-id": String(gardenId) },
          }),
          fetch("/api/tasks", {
            credentials: "include",
            headers: { "x-garden-id": String(gardenId) },
          }),
        ]);
        return responses.map((response) => response.status);
      }, fixture.gardens.alpha.id);
      assert(canonicalJson(scopedReads) === canonicalJson([200, 200]),
        `Phase 2 read-only scoped probes failed: ${key}`);
      const unexpectedMutationRequests = recorder.records.filter((request) => (
        ["DELETE", "PATCH", "POST", "PUT"].includes(request.method)
        && ![
          "/api/auth/login",
          "/api/media/summaries",
        ].includes(request.path)
      ));
      assert(unexpectedMutationRequests.length === 0,
        `Phase 2 read-surface probe issued a domain mutation request: ${key}`);
      assertDiagnosticsClean(guarded.diagnostics, `${key} Phase 2 read-only permutation`);
      result.checks.browser_diagnostics = true;
      result.checks.domain_mutation_requests_absent = true;
      result.checks.phase_two_read_only_scope_probe = true;
      result.checks.shared_state_mutation_claimed = false;
      result.assertions.passed.push(`phase-two-read-only-${role}-${profileName}`);
      status = "passed";
    } catch (error) {
      caughtError = error;
      result.failure = "Phase 2 read-only permutation probe failed; inspect private evidence";
      result.assertions.failed.push(result.failure);
    } finally {
      result.diagnostics = guarded.diagnostics;
      result.requests = recorder.records;
      try {
        result.trace = await guarded.close(status);
      } catch (error) {
        if (!caughtError) caughtError = error;
      }
      onProfile(result);
    }
    if (caughtError) throw caughtError;
  }
}

function assertPhaseTwoReadOnlyPermutationEvidence(profiles) {
  assert(Array.isArray(profiles), "Phase 2 read-only permutation evidence is missing");
  const observedOrder = profiles.map(({ role, profile }) => `${role}:${profile}`);
  assert(canonicalJson(observedOrder) === canonicalJson(PHASE_TWO_READ_ONLY_PERMUTATION_ORDER),
    "Phase 2 read-only probes did not follow the declared permutation");
  for (const [index, profile] of profiles.entries()) {
    assert(profile.failure === null, `Phase 2 read-only probe failed: ${observedOrder[index]}`);
    assert(profile.checks?.execution_model === "fresh-context-read-only-permutation"
      && profile.checks?.domain_mutation_requests_absent === true
      && profile.checks?.probe_sequence === index + 1
      && profile.checks?.phase_two_read_only_scope_probe === true
      && profile.checks?.shared_state_mutation_claimed === false,
    `Phase 2 read-only probe evidence was incomplete: ${observedOrder[index]}`);
    assert(profile.checks?.browser_diagnostics === true,
      `Phase 2 read-only probe diagnostics were missing: ${observedOrder[index]}`);
  }
  return {
    execution_model: "fresh-context-read-only-permutation",
    expected_profile_count: PHASE_TWO_READ_ONLY_PERMUTATION_ORDER.length,
    profile_order: [...PHASE_TWO_READ_ONLY_PERMUTATION_ORDER],
    shared_state_mutation_claimed: false,
  };
}

function assertPhaseTwoProfileOrder(profiles) {
  assert(Array.isArray(profiles), "Phase 2 browser profile evidence is missing");
  const observedOrder = profiles.map(({ role, profile }) => `${role}:${profile}`);
  assert(canonicalJson(observedOrder) === canonicalJson(PHASE_TWO_PROFILE_ORDER),
    "Phase 2 profiles did not follow the declared shared-state choreography");
  return [...PHASE_TWO_PROFILE_ORDER];
}

function assertPhaseTwoProfileEvidence(profiles) {
  assert(Array.isArray(profiles), "Phase 2 browser profile evidence is missing");
  const expectedProfiles = [
    {
      profile: "desktop",
      role: "admin",
      checks: [
        "admin_daily_attention_workflow",
        "grouped_completion_restrictions_across_surfaces",
        "task_surface_parity",
        "calendar_lifecycle_export_subscription",
        "calendar_export_selected_garden_scope",
        "calendar_feed_token_revocation",
        "ics_export_integrity_scope_redaction",
        "notification_preferences_and_accessibility",
        "notification_attention_projection_after_refresh",
        "notification_settings_aba_race",
        "tasks_calendar_subscriptions_aba_race",
        "stale_dom_assertions",
        "post_mutation_reload_surfaces",
        "post_mutation_reload_journal_records",
      ],
    },
    {
      profile: "mobile",
      role: "admin",
      checks: [
        "mobile_today_quick_actions_weather",
        "mobile_quick_actions_accessibility",
        "weather_idempotency_cross_surface_refresh",
        "weather_concurrent_identity_deduplication",
        "mobile_calendar_notification_focus_inert",
        "mobile_calendar_month_week_list_navigation",
        "mobile_partial_grouped_task_work",
        "mobile_snooze_manual_date",
        "immediate_snooze_correction_action",
        "mobile_calendar_lifecycle",
        "mobile_notification_preference_mutation",
        "mobile_history_reload",
      ],
    },
    {
      profile: "desktop",
      role: "editor",
      checks: ["editor_calendar_action_and_weather_scope", "editor_weather_deduplicated_surfaces"],
    },
    {
      profile: "mobile",
      role: "editor",
      checks: [
        "editor_offline_task_replay",
        "editor_offline_task_actions_replay",
      ],
    },
    {
      profile: "desktop",
      role: "viewer",
      checks: [
        "viewer_read_only_and_denial",
        "viewer_direct_forbidden_task_write",
        "viewer_today_weather_affordances",
      ],
    },
    {
      profile: "mobile",
      role: "viewer",
      checks: [
        "viewer_read_only_and_denial",
        "viewer_direct_forbidden_task_write",
        "viewer_today_weather_affordances",
      ],
    },
  ];
  assert(profiles.length === expectedProfiles.length, "Phase 2 browser profile count was unexpected");
  const expectedOrder = assertPhaseTwoProfileOrder(profiles);
  const byKey = new Map();
  for (const profile of profiles) {
    const key = `${profile?.role}:${profile?.profile}`;
    assert(!byKey.has(key), `Phase 2 browser profile was duplicated: ${key}`);
    byKey.set(key, profile);
  }
  for (const expected of expectedProfiles) {
    const key = `${expected.role}:${expected.profile}`;
    const profile = byKey.get(key);
    assert(profile, `Phase 2 browser profile is missing: ${key}`);
    assert(profile.failure === null, `Phase 2 browser profile failed: ${key}`);
    assert((profile.assertions?.failed || []).length === 0, `Phase 2 assertions failed: ${key}`);
    assert((profile.assertions?.skipped || []).length === 0, `Phase 2 assertions were skipped: ${key}`);
    assert(profile.checks?.browser_diagnostics === true, `Phase 2 browser diagnostics missing: ${key}`);
    assert(profile.checks?.map_first_without_plants === true, `Phase 2 map-first check is missing: ${key}`);
    const expectedMobile = expected.profile === "mobile";
    assert(
      profile.browser_profile?.is_mobile === expectedMobile,
      `Phase 2 browser device evidence was unexpected: ${key}`,
    );
    assert(
      profile.browser_profile?.has_touch === expectedMobile
        && (expectedMobile
          ? profile.browser_profile?.max_touch_points > 0
          : profile.browser_profile?.max_touch_points === 0),
      `Phase 2 runtime touch evidence was unexpected: ${key}`,
    );
    const expectedUserAgentContract = assertBrowserProfileContract(
      expected.profile,
      profile.browser_profile,
    );
    assert(profile.browser_profile?.user_agent_contract === expectedUserAgentContract,
      `Phase 2 user-agent contract evidence was unexpected: ${key}`);
    for (const check of expected.checks) {
      assert(profile.checks?.[check] === true, `Phase 2 browser check is missing: ${key}:${check}`);
    }
  }
  return {
    execution_model: "ordered-shared-state-choreography",
    expected_profile_count: expectedProfiles.length,
    isolated_profile_execution_claimed: false,
    profile_order: expectedOrder,
    profile_order_enforced: true,
    profile_matrix_enforced: true,
  };
}

function isPhaseTwoAuditPath(pathname) {
  return [
    /^\/api\/auth\/login$/,
    /^\/api\/attention\/(?:preferences|items\/[^/]+\/(?:read|dismiss|snooze|restore)|outcomes\/[^/]+\/restore)$/,
    /^\/api\/calendar\/(?:preferences|manual-events(?:\/[^/]+)?|subscriptions(?:\/[^/]+)?)$/,
    /^\/api\/media\/summaries$/,
    /^\/api\/notifications(?:\/(?:preferences|[^/]+(?:\/(?:dismiss|read))?))?$/,
    /^\/api\/tasks(?:\/(?:batch-action|[^/]+\/action))?$/,
    /^\/api\/weather\/(?:check|alerts\/\d+\/dismiss)$/,
  ].some((pattern) => pattern.test(pathname));
}

function phaseTwoBrowserMutationRecords(profiles, fixture) {
  const auditMutationMethods = new Set(["DELETE", "PATCH", "POST", "PUT"]);
  const mutations = (profiles || []).flatMap((profile) => (
    (profile?.requests || []).flatMap((request) => {
      if (!auditMutationMethods.has(request?.method)) return [];
      assert(Number.isSafeInteger(request.statusCode),
        `Phase 2 mutation response status is missing: ${request.method} ${request.path}`);
      const isViewerDenial = request.statusCode === 403 && profile.role === "viewer";
      assert(
        isViewerDenial || (request.statusCode >= 200 && request.statusCode < 300),
        `Phase 2 browser mutation did not have an expected response: ${request.method} ${request.path}`,
      );
      if (request.statusCode >= 200 && request.statusCode < 300) {
        assert(isPhaseTwoAuditPath(request.path),
          `Unknown successful Phase 2 browser mutation path: ${request.method} ${request.path}`);
      }
      assert(isPhaseTwoAuditPath(request.path),
        `Unknown Phase 2 browser mutation path: ${request.method} ${request.path}`);
      const isLogin = request.path === "/api/auth/login";
      const gardenId = isLogin ? null : (request.gardenId === null ? null : Number(request.gardenId));
      assert(gardenId === null || (Number.isSafeInteger(gardenId) && gardenId > 0),
        `Phase 2 mutation garden ID is invalid: ${request.method} ${request.path}`);
      const expectedActor = isLogin ? {
        authType: "none", role: "anonymous", username: "anonymous",
      } : {
        authType: "session", role: profile.role, username: fixture.roles[profile.role],
      };
      assert(
        request.actorAuthType === expectedActor.authType
          && request.actorRole === expectedActor.role
          && request.actorUsername === expectedActor.username,
        `Phase 2 browser mutation actor was incomplete: ${request.method} ${request.path}`,
      );
      assert(isSafeRequestId(request.requestId),
        `Phase 2 browser mutation lacks a request ID: ${request.method} ${request.path}`);
      return [{
        actor_auth_type: request.actorAuthType,
        actor_role: request.actorRole,
        actor_username: request.actorUsername,
        garden_id: gardenId,
        method: request.method,
        path: request.path,
        request_id: request.requestId,
        status_code: request.statusCode,
      }];
    })
  ));
  return mutations;
}

function auditRecordMatchesBrowserMutation(event, request) {
  return event.method === request.method
    && event.path === request.path
    && event.status_code === request.status_code
    && event.actor_username === request.actor_username
    && event.actor_role === request.actor_role
    && event.actor_auth_type === request.actor_auth_type
    && event.garden_id === request.garden_id
    && event.request_id === request.request_id;
}

function assertPhaseTwoAuditEvents(beforeAudit, finalAudit, profiles, fixture) {
  assert(Array.isArray(beforeAudit?.records), "Phase 2 audit boundary records are missing");
  assert(Array.isArray(finalAudit?.records), "Final Phase 2 audit records are missing");
  const beforeById = new Map();
  for (const record of beforeAudit.records) {
    assert(Number.isSafeInteger(record?.id) && record.id > 0, "Phase 2 audit boundary has an invalid ID");
    assert(!beforeById.has(record.id), `Phase 2 audit boundary duplicated ID: ${record.id}`);
    beforeById.set(record.id, record);
  }
  const finalById = new Map();
  for (const record of finalAudit.records) {
    assert(Number.isSafeInteger(record?.id) && record.id > 0, "Final Phase 2 audit record has an invalid ID");
    assert(!finalById.has(record.id), `Final Phase 2 audit record was duplicated: ${record.id}`);
    finalById.set(record.id, record);
  }
  for (const [id, record] of beforeById) {
    assert(finalById.has(id), `Phase 2 unexpectedly deleted audit record: ${id}`);
    assert(canonicalJson(finalById.get(id)) === canonicalJson(record),
      `Phase 2 unexpectedly mutated audit record: ${id}`);
  }
  const phaseTwoAuditEvents = [...finalById.values()]
    .filter((record) => !beforeById.has(record.id))
    .sort((left, right) => left.id - right.id);
  assert(phaseTwoAuditEvents.length > 0, "Phase 2 did not persist any audit events");
  const unmatchedBrowserMutations = phaseTwoBrowserMutationRecords(profiles, fixture);
  for (const event of phaseTwoAuditEvents) {
    assert(
      Number.isSafeInteger(event.occurred_at_ms) && event.occurred_at_ms > 0,
      `Phase 2 audit event timestamp was invalid: ${event.id}`,
    );
    assert(
      typeof event.path === "string" && isPhaseTwoAuditPath(event.path),
      `Unexpected Phase 2 audit event path: ${event.method} ${event.path}`,
    );
    assert(["DELETE", "PATCH", "POST", "PUT"].includes(event.method),
      `Phase 2 audit event used a non-mutation method: ${event.method} ${event.path}`);
    assert(Number.isSafeInteger(event.status_code) && (
      (event.status_code >= 200 && event.status_code < 300) || event.status_code === 403
    ), `Phase 2 audit event had an unexpected response: ${event.method} ${event.path}`);
    assert(isSafeRequestId(event.request_id),
      `Phase 2 audit event lacks a request ID: ${event.id}`);
    const requestIndex = unmatchedBrowserMutations.findIndex((request) => (
      auditRecordMatchesBrowserMutation(event, request)
    ));
    assert(requestIndex >= 0,
      `Phase 2 audit event lacks an exact browser mutation: ${event.method} ${event.path}`);
    unmatchedBrowserMutations.splice(requestIndex, 1);
  }
  const unloggedPutMutations = unmatchedBrowserMutations.filter(
    (request) => request.method === "PUT",
  ).length;
  assert(unloggedPutMutations === 0,
    "Phase 2 PUT browser mutations must have exactly one matching audit event");
  assert(unmatchedBrowserMutations.length === 0,
    "Phase 2 audited browser mutations lacked exactly one audit event");
  const auditTimestampsFrozen = phaseTwoAuditEvents.every(
    (event) => event.occurred_at_ms === fixture.clock.attention_now_ms,
  );
  return {
    phase_two_audit_event_count: phaseTwoAuditEvents.length,
    phase_two_audit_correlation_exact: true,
    phase_two_audit_events_exact: true,
    phase_two_audit_mutations_one_to_one: true,
    phase_two_audit_server_ids_one_to_one: true,
    phase_two_audit_timestamps_frozen: auditTimestampsFrozen,
    phase_two_audit_wall_clock_uncontrolled: true,
    phase_two_unlogged_put_mutation_count: unloggedPutMutations,
  };
}

function assertPhaseTwoOfflineOperationReplay(profiles, state, fixture) {
  const profile = (profiles || []).find(
    (entry) => entry?.role === "editor" && entry?.profile === "mobile",
  );
  const replay = profile?.checks?.offline_task_operation_ids;
  assert(replay && typeof replay === "object", "Phase 2 offline operation-ID evidence is missing");
  const queued = replay.queued_operations;
  const remaining = replay.remaining_operations;
  const replayed = replay.replayed_operations;
  assert(Array.isArray(queued) && Array.isArray(remaining) && Array.isArray(replayed),
    "Phase 2 offline operation-ID evidence is incomplete");
  assert(remaining.length === 0, "Phase 2 offline queue retained operation IDs after replay");
  const expected = [
    { action: "complete", key: "editor_offline", type: "task_complete" },
    { action: "skip", key: "prune_desktop", type: "task_skip" },
    { action: "snooze", key: "stale_manual_water", type: "task_snooze" },
    { action: "reschedule", key: "fertilize_grouped", type: "task_reschedule" },
  ].sort((left, right) => `${left.type}:${left.key}`.localeCompare(`${right.type}:${right.key}`));
  assert(queued.length === expected.length && replayed.length === expected.length,
    "Phase 2 offline operation-ID count was unexpected");
  const queuedByTask = new Map();
  for (const item of queued) {
    assert(typeof item?.task_id === "string" && typeof item?.type === "string",
      "Phase 2 queued offline operation identity was invalid");
    assert(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      String(item.operation_id),
    ), "Phase 2 queued offline operation ID was not UUIDv4");
    assert(!queuedByTask.has(item.task_id), "Phase 2 queued offline operation target was duplicated");
    queuedByTask.set(item.task_id, item);
  }
  assert(new Set([...queuedByTask.values()].map((item) => item.operation_id)).size === queuedByTask.size,
    "Phase 2 queued offline operation ID was reused");
  assert(
    canonicalJson([...queuedByTask.values()].map((item) => ({ task_id: item.task_id, type: item.type }))
      .sort((left, right) => `${left.type}:${left.task_id}`.localeCompare(`${right.type}:${right.task_id}`)))
      === canonicalJson(expected.map((item) => ({
        task_id: fixture.phase_two.task_ids[item.key], type: item.type,
      }))),
    "Phase 2 queued offline operation targets were unexpected",
  );
  const replayedByTask = new Map();
  for (const item of replayed) {
    assert(typeof item?.task_id === "string" && typeof item?.action === "string",
      "Phase 2 replayed offline operation identity was invalid");
    assert(item.operation_id === queuedByTask.get(item.task_id)?.operation_id,
      "Phase 2 replayed offline operation ID changed");
    assert(!replayedByTask.has(item.task_id), "Phase 2 replayed offline operation target was duplicated");
    replayedByTask.set(item.task_id, item);
  }
  assert(
    canonicalJson([...replayedByTask.values()].map((item) => ({ action: item.action, task_id: item.task_id }))
      .sort((left, right) => `${left.action}:${left.task_id}`.localeCompare(`${right.action}:${right.task_id}`)))
      === canonicalJson(expected.map((item) => ({
        action: item.action, task_id: fixture.phase_two.task_ids[item.key],
      })).sort((left, right) => `${left.action}:${left.task_id}`.localeCompare(`${right.action}:${right.task_id}`))),
    "Phase 2 replayed offline operations were unexpected",
  );
  const durableByTask = new Map((state.offline_operations || []).map((item) => [item.target_id, item]));
  for (const [taskId, queuedItem] of queuedByTask) {
    assert(durableByTask.get(taskId)?.operation_id === queuedItem.operation_id,
      "Phase 2 durable offline operation ID did not match the queued replay ID");
  }
  return { offline_operation_ids_preserved_before_after_replay: true };
}

function expectedPhaseTwoCanonicalAttentionRules() {
  const panelFirst = { digest: false, inbox: false, min_severity: "low", panel: true };
  const needsAction = { digest: false, inbox: true, min_severity: "low", panel: true };
  const warning = { digest: true, inbox: true, min_severity: "normal", panel: true };
  const upcoming = { digest: false, inbox: false, min_severity: "high", panel: true };
  const mutedIssueWithDigest = { digest: true, inbox: false, min_severity: "normal", panel: true };
  return {
    calendar_event_due: { ...upcoming },
    dry_spell: { ...warning },
    frost_warning: { ...warning },
    heat_wave: { ...warning },
    issue_follow_up_due: { ...mutedIssueWithDigest },
    issue_follow_up_overdue: { ...mutedIssueWithDigest },
    needs_action: { ...needsAction },
    no_action_needed: { digest: false, inbox: false, panel: true },
    rain_alert: { ...warning },
    system: { digest: false, inbox: true, min_severity: "low", panel: true },
    task_completed: { ...panelFirst },
    task_due: { ...needsAction },
    task_expired: { ...panelFirst },
    task_generated: { ...panelFirst },
    task_overdue: { ...needsAction },
    task_skipped: { ...panelFirst },
    task_snoozed_active: { ...panelFirst },
    task_upcoming: { ...upcoming },
    upcoming: { ...upcoming },
    warning: { ...warning },
    watering_covered_by_rain: { ...panelFirst },
    watering_rescheduled_by_rain: { ...panelFirst },
    weather_alert: { ...warning },
  };
}

function assertPhaseTwoScopedMutableRows(semantic, finalRows, fixture) {
  assert(semantic && typeof semantic === "object", "Phase 2 scoped mutable-row evidence is missing");
  const before = semantic.rows_before;
  const after = semantic.rows_after;
  assert(before && after && typeof before === "object" && typeof after === "object",
    "Phase 2 scoped mutable-row boundaries are missing");
  const expectedDeliveryNotifications = new Set([
    fixture.phase_two.preference_delivery.eligible.public_id,
    fixture.phase_two.preference_delivery.ineligible.public_id,
  ]);
  for (const table of ["tasks", "notifications", "weather_alerts"]) {
    assert(Array.isArray(before[table]) && Array.isArray(after[table]) && Array.isArray(finalRows[table]),
      `Phase 2 scoped ${table} projection is missing`);
    const projection = (rows, label) => {
      const values = rows.map((row) => ({
        identity: table === "weather_alerts" ? `row:${row.row_id}` : `public:${row.public_id}`,
        row_id: row.row_id,
      }))
        .sort((left, right) => left.row_id - right.row_id);
      assert(values.every((row) => Number.isSafeInteger(row.row_id) && row.row_id > 0
        && typeof row.identity === "string" && row.identity.length > 0),
      `Phase 2 scoped ${table} ${label} projection has an invalid identity`);
      assert(new Set(values.map((row) => row.row_id)).size === values.length,
        `Phase 2 scoped ${table} ${label} projection duplicated a row ID`);
      assert(new Set(values.map((row) => row.identity)).size === values.length,
        `Phase 2 scoped ${table} ${label} projection duplicated an identity`);
      return values;
    };
    const beforeProjection = projection(before[table], "before-maintenance");
    const afterProjection = projection(after[table], "after-maintenance");
    const finalProjection = projection(finalRows[table], "final");
    const afterIdentities = new Set(afterProjection.map((row) => row.identity));
    for (const row of beforeProjection) {
      assert(afterIdentities.has(row.identity),
        `Phase 2 maintenance deleted scoped ${table} row: ${row.identity}`);
    }
    const expectedIdentities = new Set(afterIdentities);
    if (table === "notifications") {
      for (const publicId of expectedDeliveryNotifications) {
        expectedIdentities.add(`public:${publicId}`);
      }
      if (
        fixture.gardens?.alpha?.id
        && fixture.phase_two.task_ids?.fertilize_grouped
        && fixture.roles?.admin
        && fixture.roles?.editor
        && fixture.roles?.viewer
        && fixture.clock?.attention_now_ms
      ) {
        const browserCreated = finalRows.notifications.filter((row) => (
          !expectedIdentities.has(`public:${row.public_id}`)
          && row.garden_id === fixture.gardens.alpha.id
          && row.target_id === fixture.phase_two.task_ids.fertilize_grouped
          && row.target_type === "task"
          && row.notification_type === "task_due"
          && row.created_at_ms === fixture.clock.attention_now_ms
          && row.cleared_at_ms === fixture.clock.attention_now_ms
        ));
        const browserCreatedByUser = new Map(browserCreated.map((row) => [row.username, row]));
        assert(browserCreated.length === 3 && browserCreatedByUser.size === 3,
          "Phase 2 browser created an unexpected grouped-task notification set");
        assert(browserCreatedByUser.get(fixture.roles.admin)?.clear_reason === "expired",
          "Phase 2 admin grouped-task notification did not retain its expected clear reason");
        assert(browserCreatedByUser.get(fixture.roles.editor)?.clear_reason === "expired",
          "Phase 2 editor grouped-task notification did not retain its expected clear reason");
        assert(browserCreatedByUser.get(fixture.roles.viewer)?.clear_reason === "rescheduled",
          "Phase 2 viewer grouped-task notification did not retain its expected clear reason");
        for (const row of browserCreated) {
          expectedIdentities.add(`public:${row.public_id}`);
        }
      }
    }
    assert(
      canonicalJson(finalProjection.map((row) => row.identity).sort())
        === canonicalJson([...expectedIdentities].sort()),
      `Phase 2 scoped ${table} projection contained an extra or missing mutable row`,
    );
    assert(finalProjection.length === expectedIdentities.size,
      `Phase 2 scoped ${table} count did not match its exact projection`);
  }
  return { scoped_mutable_row_projections_exact: true };
}

function semanticHistogram(rows, keyForRow) {
  const counts = {};
  for (const row of rows) {
    const key = String(keyForRow(row) || "");
    assert(key, "Phase 2 maintenance semantic histogram has an empty key");
    counts[key] = (counts[key] || 0) + 1;
  }
  return Object.fromEntries(Object.entries(counts).sort(([left], [right]) => (
    left.localeCompare(right)
  )));
}

function assertPhaseTwoMaintenanceSpec(createdByTable, summary, fixture) {
  const spec = fixture?.phase_two?.maintenance_expectations;
  assert(spec && typeof spec === "object", "Phase 2 maintenance reference specification is missing");
  const exact = (actual, expected, label) => assert(
    canonicalJson(actual) === canonicalJson(expected),
    `Phase 2 maintenance ${label} diverged from the fixture specification`,
  );
  const taskRows = createdByTable.tasks.created;
  const notificationRows = createdByTable.notifications.created;
  const weatherRows = createdByTable.weather_alerts.created;
  exact(taskRows.length, spec.created?.tasks?.total, "task count");
  exact(
    semanticHistogram(taskRows, (row) => row.task_type),
    spec.created?.tasks?.by_type,
    "task-type histogram",
  );
  exact(
    semanticHistogram(taskRows, (row) => String(row.rule_source || "").split(":")[0]),
    spec.created?.tasks?.by_rule_family,
    "task rule-family histogram",
  );
  exact(notificationRows.length, spec.created?.notifications?.total, "notification count");
  exact(
    semanticHistogram(
      notificationRows,
      (row) => `${row.notification_type}:${row.notification_subtype || ""}`,
    ),
    spec.created?.notifications?.by_type,
    "notification type/subtype histogram",
  );
  const roleByUsername = new Map(Object.entries(fixture.roles || {}).map(
    ([role, username]) => [username, role],
  ));
  exact(
    semanticHistogram(notificationRows, (row) => roleByUsername.get(row.username)),
    spec.created?.notifications?.by_role,
    "notification role histogram",
  );
  exact(weatherRows.length, spec.created?.weather_alerts?.total, "weather-alert count");
  exact(
    semanticHistogram(weatherRows, (row) => row.alert_type),
    spec.created?.weather_alerts?.by_type,
    "weather-alert type histogram",
  );
  exact(
    Object.fromEntries(["notifications", "tasks", "weather_alerts"].map((table) => (
      [table, createdByTable[table].mutated_existing.length]
    ))),
    spec.mutated_existing,
    "existing-row mutation histogram",
  );
  exact({
    configured: summary?.configured,
    gardens_processed: summary?.gardens_processed,
    notifications_created: summary?.notifications_created,
    tasks_auto_created: summary?.tasks_auto_created,
    tasks_expired: summary?.tasks_expired,
    weather_alerts_created: summary?.weather_alerts_created,
    weather_tasks_created: summary?.weather_tasks_created,
  }, spec.summary, "summary");
  return { maintenance_fixture_spec_exact: true };
}

function assertPhaseTwoMaintenanceSemanticState(maintenance, state, fixture, preferenceDelivery) {
  const semantic = maintenance?.maintenance_semantic_state;
  assert(semantic && typeof semantic === "object", "Phase 2 maintenance semantic state is missing");
  assert(semantic.frozen_now_ms === fixture.clock.attention_now_ms,
    "Phase 2 maintenance semantic state did not use the frozen timestamp");
  const createdByTable = semantic.maintenance_created;
  assert(createdByTable && typeof createdByTable === "object",
    "Phase 2 maintenance created-row evidence is missing");
  const expectedTables = ["tasks", "notifications", "weather_alerts"];
  const fixtureSpecEvidence = assertPhaseTwoMaintenanceSpec(
    createdByTable,
    maintenance.summary,
    fixture,
  );
  const createdCounts = {};
  for (const table of expectedTables) {
    const evidence = createdByTable[table];
    assert(evidence && typeof evidence === "object", `Phase 2 maintenance ${table} evidence is missing`);
    assert(Array.isArray(evidence.created) && Array.isArray(evidence.mutated_existing),
      `Phase 2 maintenance ${table} semantic rows are missing`);
    const rowIds = new Set();
    for (const section of ["created", "mutated_existing"]) {
      for (const entry of evidence[section]) {
        const row = section === "mutated_existing"
          ? assertMaintenanceMutationPair(entry, table)
          : entry;
        assert(Number.isSafeInteger(row?.row_id) && row.row_id > 0,
          `Phase 2 maintenance ${table} row has an invalid identity`);
        assert(!rowIds.has(row.row_id),
          `Phase 2 maintenance ${table} row was duplicated across semantic evidence`);
        rowIds.add(row.row_id);
        assert(row.garden_id === fixture.gardens.alpha.id,
          `Phase 2 maintenance ${table} changed the wrong garden`);
      }
    }
    createdCounts[table] = evidence.created.length;
  }
  assertExpectedMaintenanceMutations(createdByTable, fixture);

  const tasks = createdByTable.tasks.created;
  const taskPublicIds = new Set();
  for (const task of tasks) {
    assert(typeof task.public_id === "string" && task.public_id.length > 0,
      "Phase 2 maintenance task has no public ID");
    assert(!taskPublicIds.has(task.public_id),
      `Phase 2 maintenance created duplicate task: ${task.public_id}`);
    taskPublicIds.add(task.public_id);
    assert(task.created_at_ms === fixture.clock.attention_now_ms
      && task.updated_at_ms === fixture.clock.attention_now_ms,
    `Phase 2 maintenance task timestamp was not frozen: ${task.public_id}`);
    assert(typeof task.task_type === "string" && task.task_type.length > 0
      && typeof task.rule_source === "string" && task.rule_source.length > 0
      && typeof task.status === "string" && task.status.length > 0,
    `Phase 2 maintenance task semantics were incomplete: ${task.public_id}`);
    assert(Array.isArray(task.plant_ids) && Array.isArray(task.plot_ids),
      `Phase 2 maintenance task links were missing: ${task.public_id}`);
  }

  const notifications = createdByTable.notifications.created;
  const notificationPublicIds = new Set();
  for (const notification of notifications) {
    assert(typeof notification.public_id === "string" && notification.public_id.length > 0,
      "Phase 2 maintenance notification has no public ID");
    assert(!notificationPublicIds.has(notification.public_id),
      `Phase 2 maintenance created duplicate notification: ${notification.public_id}`);
    notificationPublicIds.add(notification.public_id);
    assert(notification.created_at_ms === fixture.clock.attention_now_ms,
      `Phase 2 maintenance notification timestamp was not frozen: ${notification.public_id}`);
    assert(typeof notification.notification_type === "string" && notification.notification_type.length > 0
      && typeof notification.target_type === "string" && notification.target_type.length > 0
      && typeof notification.target_id === "string" && notification.target_id.length > 0,
    `Phase 2 maintenance notification semantics were incomplete: ${notification.public_id}`);
  }

  const weatherAlerts = createdByTable.weather_alerts.created;
  const weatherKeys = new Set();
  for (const alert of weatherAlerts) {
    const key = `${alert.alert_type}:${alert.valid_from}:${alert.valid_until}:${alert.row_id}`;
    assert(!weatherKeys.has(key), `Phase 2 maintenance created duplicate weather alert: ${key}`);
    weatherKeys.add(key);
    assert(alert.created_at_ms === fixture.clock.attention_now_ms,
      `Phase 2 maintenance weather alert timestamp was not frozen: ${alert.row_id}`);
    assert(typeof alert.alert_type === "string" && alert.alert_type.length > 0
      && typeof alert.severity === "string" && alert.severity.length > 0
      && Array.isArray(alert.plant_ids),
    `Phase 2 maintenance weather alert semantics were incomplete: ${alert.row_id}`);
  }

  // summary counts alone are never accepted as Phase 2 maintenance evidence.
  assert(
    maintenance.summary?.tasks_auto_created + maintenance.summary?.weather_tasks_created
      === createdCounts.tasks,
    "Phase 2 maintenance task summary disagreed with semantic rows");
  assert(maintenance.summary?.notifications_created === createdCounts.notifications,
    "Phase 2 maintenance notification summary disagreed with semantic rows");
  assert(maintenance.summary?.weather_alerts_created === createdCounts.weather_alerts,
    "Phase 2 maintenance weather summary disagreed with semantic rows");
  const finalRows = state?.maintenance_rows;
  assert(finalRows && typeof finalRows === "object",
    "Final Phase 2 maintenance-row projection is missing");
  assert(Array.isArray(preferenceDelivery?.delivery_notifications),
    "Phase 2 maintenance notification delivery boundary is missing");
  const deliveredIds = new Set(
    preferenceDelivery.delivery_notifications.map((notification) => notification.public_id),
  );
  for (const table of expectedTables) {
    assert(Array.isArray(finalRows[table]),
      `Final Phase 2 maintenance ${table} projection is missing`);
    const expectedRows = createdByTable[table].created;
    const finalByRowId = new Map(finalRows[table].map((row) => [row.row_id, row]));
    assert(finalByRowId.size === finalRows[table].length,
      `Final Phase 2 maintenance ${table} projection duplicated a row`);
    for (const expected of expectedRows) {
      const finalRow = finalByRowId.get(expected.row_id);
      assert(finalRow, `Phase 2 maintenance ${table} row disappeared: ${expected.row_id}`);
      if (table === "notifications") {
        exactMaintenanceNotification(
          finalRow,
          expectedPhaseTwoMaintenanceNotification(expected, fixture, deliveredIds),
        );
      } else {
        assert(canonicalJson(finalRow) === canonicalJson(expected),
          `Phase 2 unexpectedly mutated maintenance ${table} row: ${expected.row_id}`);
      }
    }
  }
  return {
    ...fixtureSpecEvidence,
    ...assertPhaseTwoScopedMutableRows(semantic, finalRows, fixture),
  };
}

function assertMaintenanceMutationPair(pair, table) {
  assert(pair && typeof pair === "object", `Phase 2 maintenance ${table} mutation is missing`);
  const before = pair.before;
  const after = pair.after;
  assert(before && typeof before === "object" && after && typeof after === "object",
    `Phase 2 maintenance ${table} mutation lacks before/after evidence`);
  assert(Number.isSafeInteger(before.row_id) && before.row_id > 0 && before.row_id === after.row_id,
    `Phase 2 maintenance ${table} mutation changed row identity`);
  assert(before.garden_id === after.garden_id,
    `Phase 2 maintenance ${table} mutation changed garden ownership`);
  assert(canonicalJson(before) !== canonicalJson(after),
    `Phase 2 maintenance ${table} mutation did not contain a change`);
  return after;
}

function expectedPhaseTwoFrostMaintenanceRow(fixture) {
  const initial = fixture.phase_two.seeded_state.weather_alerts.find((alert) => (
    alert.garden_id === fixture.gardens.alpha.id && alert.alert_type === "frost_warning"
  ));
  assert(initial, "Phase 2 seeded alpha frost alert is missing");
  return {
    alert_type: "frost_warning",
    created_at_ms: initial.created_at_ms,
    description: `Frost expected on 1 day(s). Coldest: -3.0\u00b0C on ${initial.valid_from}. Protect tender plants.`,
    dismissed: false,
    garden_id: fixture.gardens.alpha.id,
    metadata: {
      coldest: -3,
      coldest_date: initial.valid_from,
      frost_days: [[initial.valid_from, -3]],
      plant_advice: [{
        hardiness: "H1",
        min_safe_temp: 15,
        name: fixture.phase_two.plant_names.fertilize_mobile,
        plt_id: fixture.phase_two.plant_ids.fertilize_mobile,
      }],
    },
    plant_ids: [...initial.plant_ids, fixture.phase_two.plant_ids.fertilize_mobile].sort(),
    row_id: initial.id,
    severity: "normal",
    title: "Frost warning: -3\u00b0C expected",
    valid_from: initial.valid_from,
    valid_until: initial.valid_until,
  };
}

function assertExpectedMaintenanceMutations(createdByTable, fixture) {
  const taskMutations = createdByTable.tasks.mutated_existing;
  assert(taskMutations.length === 1,
    "Phase 2 maintenance mutated an unexpected number of existing tasks");
  const taskMutation = taskMutations[0];
  const beforeTask = taskMutation.before;
  const afterTask = taskMutation.after;
  assert(
    beforeTask.public_id === fixture.phase_two.task_ids.stale_generated_water
      && afterTask.public_id === fixture.phase_two.task_ids.stale_generated_water,
    "Phase 2 maintenance mutated an unexpected existing task",
  );
  assert(beforeTask.status === "pending" && afterTask.status === "expired",
    "Phase 2 maintenance stale generated task expiration was unexpected");
  assert(afterTask.updated_at_ms === fixture.clock.attention_now_ms,
    "Phase 2 maintenance stale generated task mutation was not frozen");
  const changedTaskFields = Object.keys(afterTask).filter(
    (field) => canonicalJson(beforeTask[field]) !== canonicalJson(afterTask[field]),
  ).sort();
  assert(canonicalJson(changedTaskFields) === canonicalJson(["metadata", "status"]),
    "Phase 2 maintenance changed unexpected stale generated task fields");
  assert(afterTask.metadata?.lifecycle?.status === "expired"
    && afterTask.metadata?.lifecycle?.expired_at_ms === fixture.clock.attention_now_ms,
  "Phase 2 maintenance stale generated task lifecycle was unexpected");

  assert(createdByTable.notifications.mutated_existing.length === 0,
    "Phase 2 maintenance unexpectedly mutated an existing notification row");
  const weatherMutations = createdByTable.weather_alerts.mutated_existing;
  assert(weatherMutations.length === 1,
    "Phase 2 maintenance mutated an unexpected number of existing weather alerts");
  const weatherMutation = weatherMutations[0];
  const afterWeather = assertMaintenanceMutationPair(weatherMutation, "weather_alerts");
  assert(canonicalJson(afterWeather) === canonicalJson(expectedPhaseTwoFrostMaintenanceRow(fixture)),
    "Phase 2 maintenance frost alert refresh was unexpected");
  const changedWeatherFields = Object.keys(afterWeather).filter(
    (field) => canonicalJson(weatherMutation.before[field]) !== canonicalJson(afterWeather[field]),
  ).sort();
  assert(canonicalJson(changedWeatherFields) === canonicalJson([
    "description", "metadata", "plant_ids", "title",
  ]), "Phase 2 maintenance changed unexpected frost alert fields");
  return true;
}

function exactMaintenanceNotification(actual, expected) {
  const fields = [
    "body",
    "created_at_ms",
    "dismissed",
    "emailed_at_ms",
    "expires_at_ms",
    "garden_id",
    "metadata",
    "notification_subtype",
    "notification_type",
    "public_id",
    "read_at_ms",
    "row_id",
    "severity",
    "target_id",
    "target_type",
    "title",
    "username",
    "cleared_at_ms",
    "clear_reason",
  ];
  for (const field of fields) {
    const context = field === "clear_reason"
      ? ` actual=${canonicalJson(actual[field])} expected=${canonicalJson(expected[field])}`
        + ` user=${canonicalJson(expected.username)} type=${canonicalJson(expected.notification_type)}`
        + ` target=${canonicalJson(expected.target_id)}`
        + ` created_at_ms=${canonicalJson(expected.created_at_ms)}`
        + ` expires_at_ms=${canonicalJson(expected.expires_at_ms)}`
      : "";
    assert(canonicalJson(actual[field]) === canonicalJson(expected[field]),
      `Phase 2 unexpectedly mutated maintenance notification ${field}: ${expected.public_id}${context}`);
  }
}

function expectedPhaseTwoMaintenanceNotification(notification, fixture, deliveredIds) {
  const expected = { ...notification };
  if (deliveredIds.has(notification.public_id)) {
    expected.emailed_at_ms = fixture.clock.attention_now_ms;
  }
  if (!["task_due", "task_overdue", "task_upcoming"].includes(notification.notification_type)) {
    return expected;
  }
  if (notification.cleared_at_ms !== null) return expected;
  const phaseTwoDayStartMs = Date.parse(`${fixture.phase_two.date}T00:00:00Z`);
  if (
    (
      Number.isSafeInteger(notification.expires_at_ms)
      && notification.expires_at_ms < fixture.clock.attention_now_ms
    )
    || (
      notification.notification_type === "task_overdue"
      && Number.isSafeInteger(notification.created_at_ms)
      && notification.created_at_ms < phaseTwoDayStartMs
    )
  ) {
    expected.cleared_at_ms = fixture.clock.attention_now_ms;
    expected.clear_reason = "expired";
    return expected;
  }
  const roleStage = new Map([
    [fixture.roles.admin, 0],
    [fixture.roles.editor, 1],
    [fixture.roles.viewer, 2],
  ]);
  const actionByTask = new Map([
    [fixture.phase_two.task_ids.bloom_desktop, { reason: "completed", stage: 0 }],
    [fixture.phase_two.task_ids.fertilize_grouped, { reason: "superseded", stage: 0 }],
    [fixture.phase_two.task_ids.prune_desktop, { reason: "snoozed", stage: 0 }],
    [fixture.phase_two.task_ids.batch_a, { reason: "completed", stage: 0 }],
    [fixture.phase_two.task_ids.batch_b, { reason: "completed", stage: 0 }],
    [fixture.phase_two.task_ids.bloom_mobile, { reason: "completed", stage: 0 }],
    [fixture.phase_two.task_ids.fertilize_mobile, { reason: "snoozed", stage: 0 }],
    [fixture.phase_two.task_ids.plot_drawer, { reason: "completed", stage: 0 }],
    [fixture.phase_two.task_ids.snooze_correction, { reason: "snoozed", stage: 0 }],
    [fixture.phase_two.task_ids.editor_prune, { reason: "completed", stage: 1 }],
    [fixture.phase_two.task_ids.editor_offline, { reason: "completed", stage: 1 }],
  ]);
  const notificationStage = roleStage.get(notification.username);
  assert(Number.isSafeInteger(notificationStage),
    `Phase 2 task notification had an unexpected user: ${notification.public_id}`);
  const action = actionByTask.get(notification.target_id);
  expected.cleared_at_ms = fixture.clock.attention_now_ms;
  expected.clear_reason = !action || notificationStage <= action.stage ? "expired" : action.reason;
  return expected;
}

function assertPhaseTwoDatabaseState(state, fixture, maintenance, preferenceDelivery) {
  assert(state && typeof state === "object", "Phase 2 database state is missing");
  const phase = fixture?.phase_two;
  assert(phase && typeof phase === "object", "Phase 2 fixture state is missing");
  const exact = (actual, expected, message) => {
    if (canonicalJson(actual) === canonicalJson(expected)) return;
    const actualRecord = actual && typeof actual === "object" && !Array.isArray(actual) ? actual : {};
    const expectedRecord = expected && typeof expected === "object" && !Array.isArray(expected)
      ? expected
      : {};
    const differingFields = [...new Set([
      ...Object.keys(actualRecord),
      ...Object.keys(expectedRecord),
    ])]
      .filter((field) => canonicalJson(actualRecord[field]) !== canonicalJson(expectedRecord[field]))
      .sort();
    assert(false, `${message}; differing fields: ${differingFields.join(", ") || "root"}`);
  };
  for (const field of [
    "calendar_events",
    "calendar_subscriptions",
    "item_states",
    "journal",
    "notifications",
    "offline_operations",
    "outcomes",
    "preferences",
    "tasks",
    "weather_alerts",
  ]) {
    assert(Array.isArray(state[field]), `Phase 2 ${field} projection is missing`);
  }

  const expectedTaskIds = Object.values(phase.task_ids).sort();
  assert(state.tasks.length === expectedTaskIds.length, "Phase 2 fixture task count changed");
  const taskById = new Map(state.tasks.map((task) => [task.public_id, task]));
  assert(taskById.size === state.tasks.length, "Phase 2 fixture task IDs were duplicated");
  exact([...taskById.keys()].sort(), expectedTaskIds, "Phase 2 fixture task IDs changed");
  const initialTaskById = new Map(
    phase.seeded_state.tasks.map((task) => [task.public_id, task]),
  );
  const task = (key) => {
    const value = taskById.get(phase.task_ids[key]);
    assert(value, `Phase 2 task is missing: ${key}`);
    return value;
  };
  for (const finalTask of state.tasks) {
    const initialTask = initialTaskById.get(finalTask.public_id);
    assert(initialTask, `Phase 2 task was not seeded: ${finalTask.public_id}`);
    for (const field of [
      "garden_id",
      "plot_ids",
      "public_id",
      "rule_source",
      "task_type",
      "window_end_on",
      "window_kind",
      "window_start_on",
    ]) {
      exact(
        finalTask[field],
        initialTask[field],
        `Phase 2 task ${field} changed unexpectedly: ${finalTask.public_id}`,
      );
    }
    assert(finalTask.created_at_ms === fixture.clock.attention_now_ms
      && finalTask.updated_at_ms === fixture.clock.attention_now_ms,
    `Phase 2 task timestamps were not frozen: ${finalTask.public_id}`);
  }

  const completedByKey = {
    batch_a: fixture.roles.admin,
    batch_b: fixture.roles.admin,
    bloom_desktop: fixture.roles.admin,
    bloom_mobile: fixture.roles.admin,
    editor_offline: fixture.roles.editor,
    editor_prune: fixture.roles.editor,
    fertilize_mobile: fixture.roles.admin,
    plot_drawer: fixture.roles.admin,
  };
  for (const [key, username] of Object.entries(completedByKey)) {
    const completed = task(key);
    assert(completed.status === "completed", `Phase 2 task was not completed: ${key}`);
    assert(
      completed.completed_by_username === username,
      `Phase 2 task completion actor was unexpected: ${key}`,
    );
    assert(
      completed.completed_at_ms === fixture.clock.attention_now_ms,
      `Phase 2 task completion clock was not deterministic: ${key}`,
    );
    assert(completed.snoozed_until === null, `Completed Phase 2 task remained snoozed: ${key}`);
  }

  const grouped = task("fertilize_grouped");
  assert(grouped.status === "pending", "Partial grouped fertilizing did not remain pending");
  assert(grouped.completed_at_ms === null && grouped.completed_by_username === null,
    "Partial grouped fertilizing was marked complete");
  assert(grouped.due_on === phase.offline.reschedule_date,
    "Offline reschedule did not retain the requested grouped fertilize date");
  assert(grouped.snoozed_until === null,
    "Offline reschedule left the grouped fertilize task snoozed");
  exact(grouped.plant_ids, [phase.plant_ids.fertilize_b],
    "Partial grouped fertilizing retained the wrong plants");
  assert(grouped.title === `Fertilize: ${phase.plant_names.fertilize_b}`,
    "Partial grouped fertilizing title did not describe the remaining plant");

  const correction = task("snooze_correction");
  assert(correction.status === "snoozed",
    "Immediate snooze correction task did not remain snoozed after manual correction");
  assert(correction.due_on === phase.snooze_correction.due_date,
    "Immediate snooze correction changed the dedicated task due date");
  assert(correction.snoozed_until === phase.manual_date,
    "Immediate snooze correction did not retain the manually corrected date");
  assert(correction.completed_at_ms === null && correction.completed_by_username === null,
    "Immediate snooze correction task was unexpectedly completed");
  exact(correction.metadata, { fixture: "complete_journeys_phase_2" },
    "Immediate snooze correction task metadata changed");

  const skippedPrune = task("prune_desktop");
  assert(skippedPrune.status === "skipped", "Offline skip did not supersede the manual prune snooze");
  assert(skippedPrune.snoozed_until === null,
    "Offline skipped prune task retained the manual snooze date");
  assert(skippedPrune.completed_at_ms === null && skippedPrune.completed_by_username === null,
    "Offline skipped prune task was marked complete");

  const staleGenerated = task("stale_generated_water");
  assert(staleGenerated.status === "expired", "Stale generated watering task did not expire");
  exact(staleGenerated.metadata, {
    fixture: "complete_journeys_phase_2",
    lifecycle: {
      action_on: "2026-06-20",
      expired_at_ms: fixture.clock.attention_now_ms,
      expired_on: phase.date,
      reason: "stale_generated_watering",
      source: "generated_task_lifecycle",
      status: "expired",
    },
  }, "Stale generated watering lifecycle evidence was unexpected");
  const staleManual = task("stale_manual_water");
  assert(staleManual.status === "snoozed", "Offline snooze did not update the manual overdue watering task");
  assert(staleManual.snoozed_until === phase.offline.snooze_date,
    "Offline manual watering snooze date was unexpected");
  assert(staleManual.completed_at_ms === null && staleManual.completed_by_username === null,
    "Offline snoozed manual watering task was marked complete");
  exact(staleManual.metadata, { fixture: "complete_journeys_phase_2" },
    "Manual overdue watering task metadata changed");

  const rainAlerts = state.weather_alerts.filter(
    (alert) => alert.garden_id === fixture.gardens.beta.id && alert.alert_type === "rain_surplus",
  );
  assert(rainAlerts.length === 1, "Phase 2 rain alert count was unexpected");
  const rainAlert = rainAlerts[0];
  const expectedRainValidUntil = "2026-07-14";
  const expectedRainReassessmentOn = "2026-07-16";
  const rainOutdoor = task("rain_outdoor");
  assert(rainAlert.valid_until === expectedRainValidUntil,
    "Phase 2 rain alert validity window was unexpected");
  assert(rainOutdoor.status === "pending" && rainOutdoor.due_on === expectedRainReassessmentOn,
    "Outdoor watering task was not rescheduled after rain");
  assert(rainOutdoor.title === "Reassess after rain: Water Phase 2 Rain Outdoor Basil",
    "Outdoor watering task did not become a moisture reassessment");
  exact(rainOutdoor.metadata, {
    fixture: "complete_journeys_phase_2",
    rain_original_description: (
      "Deterministic Phase 2 task fixture for Water Phase 2 Rain Outdoor Basil."
    ),
    rain_original_title: "Water Phase 2 Rain Outdoor Basil",
    rain_reassessment_delay_days: 2,
    rain_reassessment_policy: "check_root_zone_moisture_before_watering",
    rescheduled_alert_valid_until: expectedRainValidUntil,
    rescheduled_from: phase.date,
    rescheduled_reason: "rain_alert",
    rescheduled_weather_alert_id: rainAlert.id,
  }, "Outdoor watering reschedule metadata was unexpected");
  for (const key of ["rain_indoor", "rain_unplaced"]) {
    const unaffected = task(key);
    assert(unaffected.status === "pending" && unaffected.due_on === phase.date,
      `Rain rescheduled an ineligible watering task: ${key}`);
    exact(unaffected.metadata, { fixture: "complete_journeys_phase_2" },
      `Rain changed metadata for an ineligible watering task: ${key}`);
  }
  const viewerTask = task("viewer_read_only");
  assert(viewerTask.status === "pending" && viewerTask.completed_at_ms === null,
    "Viewer changed a read-only Phase 2 task");
  exact(viewerTask, initialTaskById.get(phase.task_ids.viewer_read_only),
    "Viewer direct forbidden write changed the read-only Phase 2 task projection");

  const journalExpectations = {
    [phase.task_ids.bloom_desktop]: {
      actor_username: fixture.roles.admin,
      event_type: "bloomed",
      outcome: "done",
      plant_id: phase.plant_ids.bloom_desktop,
      plot_ids: [phase.plot_ids.alpha],
      task_type: "observe_bloom",
      title: "",
    },
    [phase.task_ids.bloom_mobile]: {
      actor_username: fixture.roles.admin,
      event_type: "observed",
      outcome: "not_seen_blooming_this_season",
      plant_id: phase.plant_ids.bloom_mobile,
      plot_ids: [phase.plot_ids.alpha],
      task_type: "observe_bloom",
      title: "Not seen blooming this season",
    },
    [phase.task_ids.editor_prune]: {
      actor_username: fixture.roles.editor,
      event_type: "pruned",
      outcome: "done",
      plant_id: phase.plant_ids.editor_prune,
      plot_ids: [phase.plot_ids.alpha],
      task_type: "prune",
      title: "",
    },
    [phase.task_ids.fertilize_grouped]: {
      actor_username: fixture.roles.admin,
      event_type: "fertilized",
      outcome: "done",
      plant_id: phase.plant_ids.fertilize_a,
      plot_ids: [phase.plot_ids.alpha],
      task_type: "fertilize",
      title: "",
    },
    [phase.task_ids.fertilize_mobile]: {
      actor_username: fixture.roles.admin,
      event_type: "fertilized",
      outcome: "done",
      plant_id: phase.plant_ids.fertilize_mobile,
      plot_ids: [phase.plot_ids.alpha],
      task_type: "fertilize",
      title: "",
    },
  };
  assert(
    state.journal.length === Object.keys(journalExpectations).length,
    "Phase 2 completion journal count was unexpected",
  );
  const journalByTask = new Map();
  for (const entry of state.journal) {
    const sourceTaskId = entry.metadata?.source_task_id;
    assert(typeof sourceTaskId === "string" && sourceTaskId,
      "Phase 2 journal entry has no source task");
    assert(!journalByTask.has(sourceTaskId),
      `Phase 2 task produced duplicate journal entries: ${sourceTaskId}`);
    journalByTask.set(sourceTaskId, entry);
  }
  const journalPublicIds = new Set();
  for (const [taskId, expected] of Object.entries(journalExpectations)) {
    const entry = journalByTask.get(taskId);
    assert(entry, `Phase 2 completion journal is missing: ${taskId}`);
    assert(/^jrn_[a-z0-9]+$/.test(entry.public_id),
      `Phase 2 completion journal ID is invalid: ${taskId}`);
    journalPublicIds.add(entry.public_id);
    exact({
      actor_username: entry.actor_username,
      event_type: entry.event_type,
      garden_id: entry.garden_id,
      occurred_on: entry.occurred_on,
      plant_ids: entry.plant_ids,
      plot_ids: entry.plot_ids,
      title: entry.title,
    }, {
      actor_username: expected.actor_username,
      event_type: expected.event_type,
      garden_id: fixture.gardens.alpha.id,
      occurred_on: phase.date,
      plant_ids: [expected.plant_id],
      plot_ids: expected.plot_ids,
      title: expected.title,
    }, `Phase 2 completion journal fields were unexpected: ${taskId}`);
    exact(entry.metadata, {
      outcome: expected.outcome,
      selected_plant_ids: [expected.plant_id],
      selected_plot_ids: expected.plot_ids,
      source: "task_completion",
      source_task_id: taskId,
      source_task_type: expected.task_type,
    }, `Phase 2 completion journal metadata was unexpected: ${taskId}`);
    const captureTask = taskById.get(taskId);
    const captureKey = `${taskId}:${expected.event_type}:${expected.outcome}:${expected.plant_id}`;
    exact(captureTask.metadata?.completion_journal_entries, {
      [captureKey]: entry.public_id,
    }, `Phase 2 task journal capture was not idempotent: ${taskId}`);
    if (taskId === phase.task_ids.bloom_desktop) {
      assert(captureTask.metadata?.completion_journal_entry_id === entry.public_id,
        "Bloom completion did not retain its journal link");
    }
  }
  assert(journalPublicIds.size === state.journal.length,
    "Phase 2 completion journal public IDs were duplicated");

  const seededObservations = phase.seeded_state.plant_observations;
  const finalObservations = state.plant_observations;
  assert(seededObservations && finalObservations,
    "Phase 2 plant observation projection is missing");
  const expectedObservations = structuredClone(seededObservations);
  const desktopPlantId = phase.plant_ids.bloom_desktop;
  const desktopPlant = expectedObservations.plants.find((row) => row.plant_id === desktopPlantId);
  const desktopAssignment = expectedObservations.assignments.find(
    (row) => row.plant_id === desktopPlantId && row.plot_id === phase.plot_ids.alpha,
  );
  assert(desktopPlant && desktopAssignment,
    "Phase 2 desktop bloom observation fixture is incomplete");
  desktopPlant.seen_growing = true;
  desktopPlant.seen_growing_date = phase.date;
  desktopAssignment.seen_growing = true;
  desktopAssignment.seen_growing_date = phase.date;
  exact(finalObservations, expectedObservations,
    "Phase 2 bloom completion changed the wrong plant observation state");

  exact(state.calendar_events, phase.seeded_state.calendar_events,
    "Phase 2 calendar CRUD did not restore the seeded event graph exactly");
  assert(state.calendar_subscriptions.length === 1,
    "Phase 2 calendar subscription lifecycle count was unexpected");
  const subscription = state.calendar_subscriptions[0];
  exact({
    creator_username: subscription.creator_username,
    garden_id: subscription.garden_id,
    label: subscription.label,
    owner_username: subscription.owner_username,
    preset_key: subscription.preset_key,
    revoked: subscription.revoked,
    scope: subscription.scope,
    token_hash_length: subscription.token_hash_length,
  }, {
    creator_username: fixture.roles.admin,
    garden_id: fixture.gardens.alpha.id,
    label: "Phase 2 Admin Feed",
    owner_username: fixture.roles.admin,
    preset_key: "essential",
    revoked: true,
    scope: {
      visible_sources: [
        "garden_event",
        "protect",
        "prune",
        "fertilize",
        "sow",
        "plant_out",
        "observe_bloom",
        "harvest",
        "inspect_issue",
        "weather_alert",
      ],
    },
    token_hash_length: 64,
  }, "Phase 2 revoked calendar subscription fields were unexpected");
  assert(/^calsub_[a-z0-9]+$/.test(subscription.public_id),
    "Phase 2 calendar subscription ID is invalid");
  assert(/^\.\.\.[A-Za-z0-9_-]{6}$/.test(subscription.token_hint),
    "Phase 2 calendar subscription token hint is invalid");
  assert(!Object.hasOwn(subscription, "token_hash"),
    "Phase 2 database evidence exposed a calendar subscription token hash");

  const initialPreferenceByUser = new Map(
    phase.seeded_state.preferences.map((preference) => [preference.username, preference]),
  );
  const finalPreferenceByUser = new Map(
    state.preferences.map((preference) => [preference.username, preference]),
  );
  assert(finalPreferenceByUser.size === 3, "Phase 2 preference user count was unexpected");
  exact(finalPreferenceByUser.get(fixture.roles.editor), initialPreferenceByUser.get(fixture.roles.editor),
    `Phase 2 changed ${fixture.roles.editor} preferences`);
  const initialViewerPreference = initialPreferenceByUser.get(fixture.roles.viewer);
  const finalViewerPreference = finalPreferenceByUser.get(fixture.roles.viewer);
  assert(initialViewerPreference && finalViewerPreference, "Phase 2 viewer preferences are missing");
  exact(finalViewerPreference, {
    ...initialViewerPreference,
    attention_metadata: { weather_aware_watering_suppression: true },
    attention_quiet_hours: {
      digest: { enabled: false, end: "07:00", start: "22:00" },
      timezone: "UTC",
    },
  }, "Phase 2 viewer personal preference normalization was unexpected");
  const initialAdminPreference = initialPreferenceByUser.get(fixture.roles.admin);
  const finalAdminPreference = finalPreferenceByUser.get(fixture.roles.admin);
  assert(initialAdminPreference && finalAdminPreference, "Phase 2 admin preferences are missing");
  exact(finalAdminPreference, {
    ...initialAdminPreference,
    attention_quiet_hours: {
      digest: { enabled: true, end: "07:15", start: "22:30" },
      timezone: "UTC",
    },
    attention_rules: expectedPhaseTwoCanonicalAttentionRules(),
    digest_frequency: "weekly",
    email_enabled: true,
    legacy_quiet_hours: { end: "07:15", start: "22:30", timezone: "UTC" },
    notification_rules: {
      issue_created: { email_enabled: true, in_app_enabled: false, min_severity: "normal" },
      system: { email_enabled: true, in_app_enabled: true, min_severity: "low" },
      task_due: { email_enabled: false, in_app_enabled: true, min_severity: "low" },
      task_generated: { email_enabled: false, in_app_enabled: false, min_severity: "low" },
      task_overdue: { email_enabled: false, in_app_enabled: true, min_severity: "low" },
      task_upcoming: { email_enabled: false, in_app_enabled: false, min_severity: "high" },
      "weather_alert:dry_spell": {
        email_enabled: true, in_app_enabled: true, min_severity: "normal",
      },
      "weather_alert:frost_warning": {
        email_enabled: true, in_app_enabled: true, min_severity: "normal",
      },
      "weather_alert:heat_wave": {
        email_enabled: true, in_app_enabled: true, min_severity: "normal",
      },
      "weather_alert:rain_surplus": {
        email_enabled: true, in_app_enabled: true, min_severity: "normal",
      },
    },
    preset: "custom",
  }, "Phase 2 admin notification preferences were not normalized exactly");
  for (const key of ["issue_follow_up_due", "issue_follow_up_overdue"]) {
    exact(finalAdminPreference.attention_rules?.[key], {
      digest: true,
      inbox: false,
      min_severity: "normal",
      panel: true,
    }, `Phase 2 muted issue-created attention rule was not projected exactly: ${key}`);
  }
  exact(finalAdminPreference.notification_rules?.issue_created, {
    email_enabled: true,
    in_app_enabled: false,
    min_severity: "normal",
  }, "Phase 2 muted issue-created legacy projection was not exact");
  exact(finalAdminPreference.attention_quiet_hours, {
    digest: { enabled: true, end: "07:15", start: "22:30" },
    timezone: "UTC",
  }, "Phase 2 canonical quiet hours retained legacy top-level keys");

  const notificationIds = state.notifications.map((notification) => notification.public_id);
  assert(new Set(notificationIds).size === notificationIds.length,
    "Phase 2 notification public IDs were duplicated");
  const finalNotificationById = new Map(
    state.notifications.map((notification) => [notification.public_id, notification]),
  );
  assert(state.maintenance_rows && Array.isArray(state.maintenance_rows.notifications),
    "Phase 2 maintenance notification projection is missing");
  const taskIds = new Set(Object.values(phase.task_ids));
  const expectedNotificationIds = new Set(
    state.maintenance_rows.notifications
      .filter((notification) => (
        notification.public_id.startsWith("note_complete_p2")
        || taskIds.has(notification.target_id)
      ))
      .map((notification) => notification.public_id),
  );
  for (const notification of phase.seeded_state.notifications) {
    expectedNotificationIds.add(notification.public_id);
  }
  expectedNotificationIds.add(phase.preference_delivery.eligible.public_id);
  expectedNotificationIds.add(phase.preference_delivery.ineligible.public_id);
  const afterMaintenanceNotifications = maintenance?.maintenance_semantic_state?.rows_after?.notifications;
  assert(Array.isArray(afterMaintenanceNotifications),
    "Phase 2 after-maintenance notification boundary is missing");
  const afterMaintenanceNotificationIds = new Set(
    afterMaintenanceNotifications.map((notification) => notification.public_id),
  );
  const groupedTaskNotificationRows = [];
  const groupedTaskNotificationUsers = new Set();
  for (const notification of state.notifications) {
    if (
      !afterMaintenanceNotificationIds.has(notification.public_id)
      && notification.target_id === phase.task_ids.fertilize_grouped
    ) {
      groupedTaskNotificationRows.push({
        clear_reason: notification.clear_reason,
        cleared: notification.cleared,
        notification_type: notification.notification_type,
        username: notification.username,
      });
    }
    if (
      !afterMaintenanceNotificationIds.has(notification.public_id)
      && notification.target_id === phase.task_ids.fertilize_grouped
      && notification.notification_type === "task_due"
      && ["expired", "rescheduled"].includes(notification.clear_reason)
    ) {
      expectedNotificationIds.add(notification.public_id);
      groupedTaskNotificationUsers.add(notification.username);
    }
  }
  const actualGroupedTaskNotificationUsers = [...groupedTaskNotificationUsers].sort();
  const expectedGroupedTaskNotificationUsers = [
    fixture.roles.admin,
    fixture.roles.editor,
    fixture.roles.viewer,
  ].sort();
  exact(
    actualGroupedTaskNotificationUsers,
    expectedGroupedTaskNotificationUsers,
    `Phase 2 grouped-task notification users were unexpected: actual=${canonicalJson(
      actualGroupedTaskNotificationUsers,
    )}; expected=${canonicalJson(expectedGroupedTaskNotificationUsers)}; rows=${canonicalJson(
      groupedTaskNotificationRows,
    )}`,
  );
  exact(notificationIds.slice().sort(), [...expectedNotificationIds].sort(),
    "Phase 2 task and seeded notification projection identities were unexpected");
  for (const initialNotification of phase.seeded_state.notifications) {
    const expectedNotification = initialNotification.public_id === phase.notification_public_id
      ? { ...initialNotification, emailed: true }
      : initialNotification;
    exact(finalNotificationById.get(initialNotification.public_id), expectedNotification,
      `Phase 2 durable notification changed: ${initialNotification.public_id}`);
  }
  const preferenceDeliveryFixture = phase.preference_delivery;
  assert(preferenceDeliveryFixture && typeof preferenceDeliveryFixture === "object",
    "Phase 2 preference delivery fixture is missing");
  assert(preferenceDelivery && typeof preferenceDelivery === "object",
    "Phase 2 preference delivery evidence is missing");
  assert(preferenceDelivery.triggered_at_ms === preferenceDeliveryFixture.occurred_at_ms,
    "Phase 2 preference delivery did not use the frozen timestamp");
  assert(preferenceDelivery.garden_id === fixture.gardens.alpha.id,
    "Phase 2 preference delivery ran for the wrong garden");
  assert(Number.isSafeInteger(preferenceDelivery.delivery_badge_count)
    && preferenceDelivery.delivery_badge_count > 0,
  "Phase 2 preference delivery did not retain an eligible unread badge");
  const expectedPreferenceDeliveryIssues = [
    {
      created_at_ms: preferenceDeliveryFixture.occurred_at_ms,
      creator_username: fixture.roles.admin,
      description: preferenceDeliveryFixture.eligible.body,
      follow_up_on: phase.date,
      garden_id: fixture.gardens.alpha.id,
      issue_type: "other",
      metadata: { fixture: "complete_journeys_phase_2", preference_delivery: true },
      public_id: preferenceDeliveryFixture.eligible.issue_public_id,
      severity: preferenceDeliveryFixture.eligible.severity,
      status: "open",
      title: preferenceDeliveryFixture.eligible.title,
      updated_at_ms: preferenceDeliveryFixture.occurred_at_ms,
    },
    {
      created_at_ms: preferenceDeliveryFixture.occurred_at_ms,
      creator_username: fixture.roles.admin,
      description: preferenceDeliveryFixture.ineligible.body,
      follow_up_on: phase.date,
      garden_id: fixture.gardens.alpha.id,
      issue_type: "other",
      metadata: { fixture: "complete_journeys_phase_2", preference_delivery: true },
      public_id: preferenceDeliveryFixture.ineligible.issue_public_id,
      severity: preferenceDeliveryFixture.ineligible.severity,
      status: "open",
      title: preferenceDeliveryFixture.ineligible.title,
      updated_at_ms: preferenceDeliveryFixture.occurred_at_ms,
    },
  ];
  exact(state.preference_delivery_issues, expectedPreferenceDeliveryIssues,
    "Phase 2 post-save preference delivery issue rows were not exact");
  exact(preferenceDelivery.preference_delivery_issues, expectedPreferenceDeliveryIssues,
    "Phase 2 delivery trigger did not retain exact issue evidence");
  const expectedPreferenceDeliveryRows = [
    {
      body: preferenceDeliveryFixture.eligible.body,
      clear_reason: null,
      cleared: false,
      created_at_ms: preferenceDeliveryFixture.occurred_at_ms,
      dismissed: false,
      emailed: true,
      expires_at_ms: preferenceDeliveryFixture.occurred_at_ms + 7 * 86_400_000,
      garden_id: fixture.gardens.alpha.id,
      metadata: { fixture: "complete_journeys_phase_2", preference_delivery: true },
      notification_subtype: "",
      notification_type: "issue_created",
      public_id: preferenceDeliveryFixture.eligible.public_id,
      read: false,
      severity: preferenceDeliveryFixture.eligible.severity,
      target_id: preferenceDeliveryFixture.eligible.issue_public_id,
      target_type: "issue",
      title: preferenceDeliveryFixture.eligible.title,
      username: fixture.roles.admin,
    },
    {
      body: preferenceDeliveryFixture.ineligible.body,
      clear_reason: null,
      cleared: false,
      created_at_ms: preferenceDeliveryFixture.occurred_at_ms,
      dismissed: false,
      emailed: false,
      expires_at_ms: preferenceDeliveryFixture.occurred_at_ms + 7 * 86_400_000,
      garden_id: fixture.gardens.alpha.id,
      metadata: { fixture: "complete_journeys_phase_2", preference_delivery: true },
      notification_subtype: "",
      notification_type: "issue_created",
      public_id: preferenceDeliveryFixture.ineligible.public_id,
      read: false,
      severity: preferenceDeliveryFixture.ineligible.severity,
      target_id: preferenceDeliveryFixture.ineligible.issue_public_id,
      target_type: "issue",
      title: preferenceDeliveryFixture.ineligible.title,
      username: fixture.roles.admin,
    },
  ];
  const actualPreferenceDeliveryRows = expectedPreferenceDeliveryRows.map((expected) => (
    finalNotificationById.get(expected.public_id)
  ));
  exact(actualPreferenceDeliveryRows, expectedPreferenceDeliveryRows,
    "Phase 2 post-save preference delivery rows were not exact");
  const persistedDeliveryRows = preferenceDelivery.preference_delivery_rows || [];
  assert(persistedDeliveryRows.length === 2,
    "Phase 2 preference delivery evidence did not retain both explicit rows");
  const persistedById = new Map(persistedDeliveryRows.map((row) => [row.public_id, row]));
  assert(
    persistedById.get(preferenceDeliveryFixture.eligible.public_id)?.emailed_at_ms
      === preferenceDeliveryFixture.occurred_at_ms
      && persistedById.get(preferenceDeliveryFixture.ineligible.public_id)?.emailed_at_ms === null,
    "Phase 2 preference delivery evidence did not distinguish eligible and ineligible rows",
  );
  const deliveredNotificationIds = (preferenceDelivery.delivery_notifications || []).map(
    (notification) => notification.public_id,
  );
  assert(deliveredNotificationIds.includes(phase.notification_public_id)
    && deliveredNotificationIds.includes(preferenceDeliveryFixture.eligible.public_id)
    && !deliveredNotificationIds.includes(preferenceDeliveryFixture.ineligible.public_id),
  "Phase 2 post-save delivery did not apply the saved issue-created eligibility rule");

  const weatherIds = state.weather_alerts.map((alert) => alert.id);
  assert(new Set(weatherIds).size === weatherIds.length,
    "Phase 2 weather alert IDs were duplicated");
  const finalWeatherById = new Map(state.weather_alerts.map((alert) => [alert.id, alert]));
  const weatherMutations = maintenance.maintenance_semantic_state.maintenance_created
    .weather_alerts.mutated_existing;
  const mutatedWeatherById = new Map(weatherMutations.map((mutation) => (
    [mutation.after.row_id, mutation.after]
  )));
  for (const initialAlert of phase.seeded_state.weather_alerts) {
    const mutated = mutatedWeatherById.get(initialAlert.id);
    let expectedAlert = initialAlert;
    if (mutated) {
      expectedAlert = {
        alert_type: mutated.alert_type,
        created_at_ms: mutated.created_at_ms,
        dismissed: mutated.dismissed,
        garden_id: mutated.garden_id,
        id: mutated.row_id,
        metadata: mutated.metadata,
        plant_ids: mutated.plant_ids,
        severity: mutated.severity,
        title: mutated.title,
        valid_from: mutated.valid_from,
        valid_until: mutated.valid_until,
      };
    }
    if (
      initialAlert.garden_id === fixture.gardens.beta.id
      && initialAlert.alert_type === "frost_warning"
    ) {
      expectedAlert = {
        ...initialAlert,
        dismissed: true,
        metadata: {
          ...initialAlert.metadata,
          lifecycle: {
            reason: "absent_from_current_forecast",
            resolution_kind: "automatic_forecast",
            resolved_at_ms: fixture.clock.attention_now_ms,
            source: "forecast_reconciliation",
            status: "resolved",
          },
        },
      };
    }
    exact(finalWeatherById.get(initialAlert.id), expectedAlert,
      `Phase 2 seeded weather alert changed: ${initialAlert.id}`);
  }
  const expectedWeatherIds = new Set(
    state.maintenance_rows.weather_alerts.map((alert) => alert.row_id),
  );
  for (const alert of phase.seeded_state.weather_alerts) expectedWeatherIds.add(alert.id);
  expectedWeatherIds.add(rainAlert.id);
  exact(weatherIds.slice().sort((left, right) => left - right), [...expectedWeatherIds].sort(
    (left, right) => left - right,
  ), "Phase 2 generated weather alert identities were unexpected");
  assert(rainAlert.created_at_ms === fixture.clock.attention_now_ms,
    "Phase 2 rain alert timestamp was not frozen");
  exact({
    dismissed: rainAlert.dismissed,
    plant_ids: rainAlert.plant_ids,
    severity: rainAlert.severity,
    valid_from: rainAlert.valid_from,
    valid_until: rainAlert.valid_until,
  }, {
    dismissed: false,
    plant_ids: [
      phase.plant_ids.rain_indoor,
      phase.plant_ids.rain_outdoor,
      phase.plant_ids.rain_unplaced,
    ].sort(),
    severity: "normal",
    valid_from: phase.date,
    valid_until: "2026-07-14",
  }, "Phase 2 rain alert fields were unexpected");
  assert(rainAlert.metadata?.rain_days === 3 && rainAlert.metadata?.total_mm === 16,
    "Phase 2 rain alert metadata was unexpected");

  assert(state.item_states.length === 1,
    "Phase 2 user-scoped weather dismissal count was unexpected");
  exact(state.item_states[0], {
    garden_id: fixture.gardens.beta.id,
    item_id: `attn:weather:alert:${rainAlert.id}`,
    metadata: {},
    reason: "",
    snoozed_until_ms: null,
    user_state: "dismissed",
    username: fixture.roles.admin,
  }, "Phase 2 weather dismissal was not user scoped");

  assert(state.outcomes.length === 1, "Phase 2 rain outcome count was unexpected");
  const outcome = state.outcomes[0];
  assert(/^attnout_[a-z0-9]+$/.test(outcome.public_id),
    "Phase 2 rain outcome ID is invalid");
  assert(outcome.expires_at_ms - outcome.occurred_at_ms === 30 * 86_400_000,
    "Phase 2 rain outcome retention was unexpected");
  exact({
    garden_id: outcome.garden_id,
    outcome_type: outcome.outcome_type,
    plant_ids: outcome.plant_ids,
    plot_ids: outcome.plot_ids,
    provider: outcome.provider,
    recovery_action: outcome.recovery_action,
    source_id: outcome.source_id,
    source_public_id: outcome.source_public_id,
    source_type: outcome.source_type,
    target_id: outcome.target_id,
    target_type: outcome.target_type,
  }, {
    garden_id: fixture.gardens.beta.id,
    outcome_type: "watering_rescheduled_by_rain",
    plant_ids: [phase.plant_ids.rain_outdoor],
    plot_ids: [phase.plot_ids.beta],
    provider: "weather",
    recovery_action: {
      due_on: phase.date,
      kind: "restore_generated_watering_task",
      label: "Restore watering",
      plant_ids: [phase.plant_ids.rain_outdoor],
      plot_ids: [phase.plot_ids.beta],
      source_public_id: task("rain_outdoor").rule_source,
      target_id: phase.plant_ids.rain_outdoor,
      target_type: "plant",
    },
    source_id: String(rainAlert.id),
    source_public_id: task("rain_outdoor").rule_source,
    source_type: "task_generator",
    target_id: phase.plant_ids.rain_outdoor,
    target_type: "plant",
  }, "Phase 2 rain outcome linkage was unexpected");
  assert(
    outcome.metadata?.weather_alert_id === String(rainAlert.id)
      && outcome.metadata?.task_public_id === phase.task_ids.rain_outdoor
      && outcome.metadata?.due_on === phase.date
      && outcome.metadata?.new_due_on === rainOutdoor.due_on
      && outcome.metadata?.rain_mm === 16,
    "Phase 2 rain outcome metadata was unexpected",
  );

  const offlineTaskKeys = [
    "editor_offline",
    "prune_desktop",
    "stale_manual_water",
    "fertilize_grouped",
  ];
  assert(state.offline_operations.length === offlineTaskKeys.length,
    "Phase 2 offline task replay operation count was unexpected");
  const offlineOperationByTarget = new Map(
    state.offline_operations.map((operation) => [operation.target_id, operation]),
  );
  assert(offlineOperationByTarget.size === offlineTaskKeys.length,
    "Phase 2 offline task replay targets were duplicated");
  exact([...offlineOperationByTarget.keys()].sort(), offlineTaskKeys.map(
    (key) => phase.task_ids[key],
  ).sort(), "Phase 2 offline task replay targets were unexpected");
  for (const key of offlineTaskKeys) {
    const taskId = phase.task_ids[key];
    const operation = offlineOperationByTarget.get(taskId);
    assert(operation, `Phase 2 offline task replay operation is missing: ${key}`);
    assert(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      operation.operation_id,
    ), `Phase 2 offline task operation ID is not a UUIDv4: ${key}`);
    assert(/^[0-9a-f]{64}$/.test(operation.request_fingerprint),
      `Phase 2 offline task request fingerprint is invalid: ${key}`);
    exact({
      endpoint: operation.endpoint,
      garden_id: operation.garden_id,
      result_id: operation.result_id,
      target_id: operation.target_id,
      target_type: operation.target_type,
    }, {
      endpoint: "task_action",
      garden_id: fixture.gardens.alpha.id,
      result_id: taskId,
      target_id: taskId,
      target_type: "task",
    }, `Phase 2 offline task replay linkage was unexpected: ${key}`);
  }

  assert(maintenance && typeof maintenance === "object",
    "Phase 2 maintenance evidence is missing");
  const maintenanceSemanticEvidence = assertPhaseTwoMaintenanceSemanticState(
    maintenance,
    state,
    fixture,
    preferenceDelivery,
  );
  assert(maintenance.delivery_count === 0 && maintenance.deliveries?.length === 0,
    "Phase 2 pre-save maintenance unexpectedly delivered email");
  assert(maintenance.garden_id === fixture.gardens.alpha.id,
    "Phase 2 maintenance ran for the wrong garden");
  exact({
    configured: maintenance.summary?.configured,
    gardens_processed: maintenance.summary?.gardens_processed,
    notifications_created: maintenance.summary?.notifications_created,
    tasks_auto_created: maintenance.summary?.tasks_auto_created,
    tasks_expired: maintenance.summary?.tasks_expired,
    weather_alerts_created: maintenance.summary?.weather_alerts_created,
    weather_tasks_created: maintenance.summary?.weather_tasks_created,
  }, fixture.phase_two.maintenance_expectations.summary,
  "Phase 2 maintenance summary was unexpected");
  assert(
    maintenance.deliveries.every((delivery) => (
      Number.isSafeInteger(delivery.body_length) && delivery.body_length > 0
      && Number.isSafeInteger(delivery.recipient_length) && delivery.recipient_length > 0
      && Number.isSafeInteger(delivery.subject_length) && delivery.subject_length > 0
    )),
    "Phase 2 digest delivery evidence was invalid",
  );
  assert(preferenceDelivery.delivery_count === preferenceDelivery.deliveries?.length
    && preferenceDelivery.delivery_count >= 1,
  "Phase 2 post-save preference delivery did not produce deterministic delivery evidence");
  assert(
    preferenceDelivery.deliveries.every((delivery) => (
      Number.isSafeInteger(delivery.body_length) && delivery.body_length > 0
      && Number.isSafeInteger(delivery.recipient_length) && delivery.recipient_length > 0
      && Number.isSafeInteger(delivery.subject_length) && delivery.subject_length > 0
    )),
  "Phase 2 post-save preference delivery evidence was invalid");

  return {
    calendar_lifecycle_exact: true,
    completion_history_exact: true,
    maintenance_exact: true,
    maintenance_semantic_state_exact: true,
    ...maintenanceSemanticEvidence,
    notification_preferences_exact: true,
    preference_delivery_exact: true,
    offline_replay_exact: true,
    rain_automation_exact: true,
    task_lifecycle_exact: true,
    user_scoped_weather_dismissal_exact: true,
  };
}

function assertExactPhaseOneMobileSnapshot(snapshots, expected) {
  assert(Array.isArray(snapshots), "Phase 1 mobile snapshot evidence is missing");
  assert(snapshots.length === 1, "Phase 1 did not retain exactly one mobile snapshot");
  const snapshot = snapshots[0];
  assert(snapshot?.garden_id === expected.garden_id, "Mobile snapshot was not owned by Alpha");
  assert(
    snapshot?.garden_owner_username === expected.garden_owner_username,
    "Mobile snapshot garden owner was unexpected",
  );
  assert(snapshot?.name === expected.name, "Mobile snapshot name was unexpected");
  assert(
    typeof snapshot?.public_id === "string" && snapshot.public_id.startsWith("snap_"),
    "Mobile snapshot has no public identifier",
  );
  const payload = snapshot?.payload;
  assert(payload && typeof payload === "object" && !Array.isArray(payload), "Mobile snapshot payload is missing");
  assert(
    canonicalJson(Object.keys(payload).sort()) === canonicalJson([
      "house",
      "map_objects",
      "plots",
      "schema_version",
      "shademap",
      "shademap_calibration",
      "shademap_obstacles",
    ]),
    "Mobile snapshot payload fields were unexpected",
  );
  assert(
    canonicalJson(payload) === canonicalJson(expected.payload),
    "Mobile snapshot payload did not match the final Alpha snapshot projection",
  );
}

function assertExactPhaseOneRestoreImportGraphs(initialGraphs, finalGraphs) {
  const expectedGardens = ["alpha", "beta"];
  assert(initialGraphs && typeof initialGraphs === "object", "Fixture restore/import graph is missing");
  assert(finalGraphs && typeof finalGraphs === "object", "Final restore/import graph is missing");
  assert(
    canonicalJson(Object.keys(initialGraphs).sort()) === canonicalJson(expectedGardens),
    "Fixture restore/import graph has unexpected gardens",
  );
  assert(
    canonicalJson(Object.keys(finalGraphs).sort()) === canonicalJson(expectedGardens),
    "Final restore/import graph has unexpected gardens",
  );
  for (const garden of expectedGardens) {
    assert(
      canonicalJson(finalGraphs[garden]) === canonicalJson(initialGraphs[garden]),
      `Restore/import changed the final ${garden} plot, layout, map-object, or assignment graph`,
    );
  }
}

function assertExactPhaseOneOnboardingGraphs(graphs, expectedByName) {
  assert(graphs && typeof graphs === "object", "Phase 1 onboarding graphs are missing");
  const expectedNames = Object.keys(expectedByName).sort();
  assert(
    canonicalJson(Object.keys(graphs).sort()) === canonicalJson(expectedNames),
    "Onboarding graph names were unexpected",
  );
  for (const [name, expected] of Object.entries(expectedByName)) {
    const graph = graphs[name];
    assert(graph && typeof graph === "object", `Onboarding graph is missing: ${name}`);
    const garden = graph.garden;
    assert(garden && typeof garden === "object", `Onboarding garden is missing: ${name}`);
    assert(Number.isSafeInteger(garden.id) && garden.id > 0, `Onboarding garden ID is invalid: ${name}`);
    for (const field of [
      "address",
      "grid_cols",
      "grid_rows",
      "latitude",
      "longitude",
      "onboarding_complete",
      "owner_username",
      "slug",
    ]) {
      assert(garden[field] === expected[field], `Onboarding graph ${field} mismatch: ${name}`);
    }
    assert(garden.name === name, `Onboarding graph name mismatch: ${name}`);
    assert(
      canonicalJson(graph.layout) === canonicalJson(expected.layout),
      `Onboarding layout graph mismatch: ${name}`,
    );
    const expectedPlot = [{
      color: "",
      garden_id: garden.id,
      grid_col: null,
      grid_row: null,
      notes: "",
      owner_username: expected.owner_username,
      plot_id: `INDOOR-${garden.id}`,
      plot_number: 0,
      sub_zone: "",
      zone_code: "I",
      zone_name: "Innendors",
    }];
    assert(
      canonicalJson(graph.plots) === canonicalJson(expectedPlot),
      `Onboarding generated plot and ownership graph mismatch: ${name}`,
    );
    for (const field of ["assignments", "map_objects", "plants"]) {
      assert(
        Array.isArray(graph[field]) && graph[field].length === 0,
        `Onboarding graph retained unexpected ${field}: ${name}`,
      );
    }
  }
}

function assertExactPhaseOneOnboardingDefaultContext(context, fixture) {
  assert(context && typeof context === "object", "Onboarding default context is missing");
  assert(Array.isArray(context.gardens), "Onboarding default gardens are missing");
  assert(Array.isArray(context.memberships), "Onboarding default memberships are missing");
  assert(context.gardens.length === 1, "Onboarding did not create exactly one default garden");
  const garden = context.gardens[0];
  assert(Number.isSafeInteger(garden?.id) && garden.id > 0, "Onboarding default garden ID is invalid");
  const expectedGarden = {
    address: "",
    grid_cols: 22,
    grid_rows: 30,
    latitude: null,
    layout_count: 0,
    longitude: null,
    map_object_count: 0,
    name: "Default Garden",
    onboarding_complete: false,
    owner_username: null,
    plot_count: 0,
    slug: "default",
  };
  for (const [field, value] of Object.entries(expectedGarden)) {
    assert(garden[field] === value, `Onboarding default garden ${field} was unexpected`);
  }
  const expectedMemberships = [
    { garden_id: garden.id, role: "editor", username: fixture.roles.onboarding },
    { garden_id: garden.id, role: "editor", username: fixture.roles.onboarding_mobile },
  ].sort((left, right) => left.username.localeCompare(right.username));
  assert(
    canonicalJson(context.memberships) === canonicalJson(expectedMemberships),
    "Onboarding default memberships were unexpected",
  );
}

function assertExactPhaseOneQuickActionRecords(records, fixture) {
  assert(records && typeof records === "object", "Phase 1 quick-action records are missing");
  for (const field of ["harvest_rollups", "harvests", "journals"]) {
    assert(Array.isArray(records[field]), `Phase 1 quick-action ${field} are missing`);
  }
  assert(records.harvests.length === 1, "Phase 1 did not retain exactly one quick-action harvest");
  assert(records.journals.length === 1, "Phase 1 did not retain exactly one quick-action journal");
  assert(records.harvest_rollups.length === 1, "Phase 1 did not retain exactly one harvest rollup");
  const harvest = records.harvests[0];
  const journal = records.journals[0];
  const alphaId = fixture.gardens.alpha.id;
  const date = fixture.clock.attention_date;
  assert(
    typeof harvest.public_id === "string" && harvest.public_id.startsWith("hrv_"),
    "Quick-action harvest public ID is invalid",
  );
  assert(
    typeof journal.public_id === "string" && journal.public_id.startsWith("jrn_"),
    "Quick-action journal public ID is invalid",
  );
  const expectedLinks = {
    plant_ids: [fixture.phase_one.indoor.plant_id],
    plot_ids: [fixture.gardens.alpha.plot_id],
  };
  assert(
    canonicalJson({
      actor_username: harvest.actor_username,
      garden_id: harvest.garden_id,
      notes: harvest.notes,
      occurred_on: harvest.occurred_on,
      plant_ids: harvest.plant_ids,
      plot_ids: harvest.plot_ids,
      quality: harvest.quality,
      quantity: harvest.quantity,
      unit: harvest.unit,
    }) === canonicalJson({
      actor_username: fixture.roles.admin,
      garden_id: alphaId,
      notes: "Phase 1 mobile quick action",
      occurred_on: date,
      ...expectedLinks,
      quality: "good",
      quantity: 1,
      unit: "kg",
    }),
    "Quick-action harvest fields or links were unexpected",
  );
  assert(
    canonicalJson(harvest.metadata) === canonicalJson({ journal_entry_id: journal.public_id }),
    "Quick-action harvest did not link to its journal",
  );
  assert(
    canonicalJson({
      actor_username: journal.actor_username,
      event_type: journal.event_type,
      garden_id: journal.garden_id,
      notes: journal.notes,
      occurred_on: journal.occurred_on,
      plant_ids: journal.plant_ids,
      plot_ids: journal.plot_ids,
      title: journal.title,
    }) === canonicalJson({
      actor_username: fixture.roles.admin,
      event_type: "harvested",
      garden_id: alphaId,
      notes: "Phase 1 mobile quick action",
      occurred_on: date,
      ...expectedLinks,
      title: `Harvested 1 kg from ${fixture.phase_one.indoor.plant_id}`,
    }),
    "Quick-action journal fields or links were unexpected",
  );
  assert(
    canonicalJson(journal.metadata) === canonicalJson({
      linked_harvest_entry_id: harvest.public_id,
      quantity: 1,
      source: "auto:harvest",
      unit: "kg",
    }),
    "Quick-action journal did not link to its harvest",
  );
  const expectedYear = Number(date.slice(0, 4));
  const rollup = records.harvest_rollups[0];
  assert(
    canonicalJson(rollup) === canonicalJson({
      key: `harvest_rollup:${alphaId}:${expectedYear}`,
      value: {
        by_unit: [{ entries: 1, total_qty: 1, unit: "kg" }],
        garden_id: alphaId,
        year: expectedYear,
      },
    }),
    "Quick-action harvest rollup key or value was unexpected",
  );
}

function assertPhaseOneStableDomainProjection(initialProjection, finalProjection) {
  assert(
    initialProjection && typeof initialProjection === "object",
    "Phase 1 initial stable-domain projection is missing",
  );
  assert(
    finalProjection && typeof finalProjection === "object",
    "Phase 1 final stable-domain projection is missing",
  );
  assert(
    canonicalJson(finalProjection) === canonicalJson(initialProjection),
    "Phase 1 changed a non-retained semantic row in a mutable domain table",
  );
}

function expectedPhaseOneRestoreGraphsAfterPhaseTwo(phaseOneGraphs, fixture) {
  const expected = structuredClone(phaseOneGraphs);
  const plantId = fixture?.phase_two?.plant_ids?.bloom_desktop;
  const observedOn = fixture?.phase_two?.date;
  if (!plantId || !observedOn) return expected;
  const alpha = expected?.alpha;
  assert(alpha && typeof alpha === "object", "Phase 1 Alpha restore graph is missing");
  for (const collection of ["plants", "assignments"]) {
    const matches = (alpha[collection] || []).filter((row) => row.plant_id === plantId);
    assert(matches.length === 1,
      `Phase 2 bloom observation has unexpected Phase 1 ${collection} linkage`);
    matches[0].seen_growing = true;
    matches[0].seen_growing_date = observedOn;
  }
  return expected;
}

function expectedPhaseOneStableDomainProjectionAfterPhaseTwo(
  phaseOneProjection,
  fixture,
  gardenId,
) {
  const expected = structuredClone(phaseOneProjection);
  if (!fixture || gardenId == null) return expected;
  const attentionDate = fixture?.phase_two?.date;
  const attentionNowMs = fixture?.clock?.attention_now_ms;
  assert(/^\d{4}-\d{2}-\d{2}$/.test(attentionDate || ""),
    "Phase 2 fixture date is missing for scheduler checkpoints");
  assert(Number.isSafeInteger(attentionNowMs),
    "Phase 2 fixture timestamp is missing for scheduler checkpoints");
  assert(Array.isArray(expected.app_settings),
    "Phase 1 stable-domain app settings projection is missing");
  const checkpointRows = [
    { key: `last_task_gen_month:${gardenId}`, value: attentionDate.slice(0, 7) },
    { key: `last_weather_check_ms:${gardenId}`, value: String(attentionNowMs) },
  ];
  const checkpointKeys = new Set(checkpointRows.map((row) => row.key));
  expected.app_settings = expected.app_settings
    .filter((row) => !checkpointKeys.has(row.key))
    .concat(checkpointRows)
    .sort((left, right) => canonicalJson(left).localeCompare(canonicalJson(right)));
  return expected;
}

function assertPhaseOneStatePreservedAfterPhaseTwo(phaseOneBoundary, finalState, fixture = null) {
  assert(
    phaseOneBoundary && typeof phaseOneBoundary === "object",
    "Phase 1 boundary state is missing before Phase 2",
  );
  assert(
    finalState && typeof finalState === "object",
    "Phase 1 final state is missing after Phase 2",
  );
  const scopedFields = [
    "alpha_address",
    "alpha_id",
    "alpha_map_object",
    "alpha_map_unit",
    "alpha_map_unit_count",
    "alpha_snapshot_payload",
    "beta_id",
    "browser_lifecycle",
    "cross_garden_links",
    "harvest_count",
    "indoor_assignment",
    "indoor_room_label",
    "journal_count",
    "lifecycle_audit",
    "mobile_harvests",
    "mobile_journals",
    "mobile_snapshot_count",
    "mobile_snapshots",
    "onboarding_default_context",
    "onboarding_gardens",
    "onboarding_target_gardens",
    "onboarding_target_graphs",
    "quick_action_records",
    "restore_import_graphs",
    "saved_view",
    "seeded_saved_views",
    "stable_domain_projection",
    "temp_map_object_count",
    "temp_plant_count",
    "temp_saved_view_count",
    "viewer",
  ];
  let compared = 0;
  for (const field of scopedFields) {
    if (!Object.hasOwn(phaseOneBoundary, field)) continue;
    assert(Object.hasOwn(finalState, field), `Phase 1 ${field} disappeared during Phase 2`);
    let expected = phaseOneBoundary[field];
    if (field === "restore_import_graphs") {
      expected = expectedPhaseOneRestoreGraphsAfterPhaseTwo(expected, fixture);
    } else if (field === "stable_domain_projection") {
      expected = expectedPhaseOneStableDomainProjectionAfterPhaseTwo(
        expected,
        fixture,
        phaseOneBoundary.alpha_id,
      );
    }
    assert(
      canonicalJson(finalState[field]) === canonicalJson(expected),
      `Phase 1 scoped ${field} changed during Phase 2`,
    );
    compared += 1;
  }
  assert(compared > 0, "Phase 1 boundary contained no scoped state to preserve through Phase 2");
}

function assertFixtureAttentionClock(fixture) {
  const clock = fixture?.clock;
  assert(clock && typeof clock === "object", "Fixture has no frozen attention clock");
  assert(
    process.env.GARDENOPS_ATTENTION_FROZEN_DATE === clock.attention_date,
    "Runner attention date does not match fixture provenance",
  );
  assert(
    process.env.GARDENOPS_ATTENTION_FROZEN_NOW_MS === String(clock.attention_now_ms),
    "Runner attention timestamp does not match fixture provenance",
  );
}

function isSafeManifestRequestPath(value) {
  const requestPath = String(value || "");
  return [
    /^\/api\/auth\/(?:login|me|status)$/,
    /^\/api\/attention\/(?:preferences|today)$/,
    /^\/api\/calendar\/(?:events|export\.ics|preferences|manual-events(?:\/[^/?]+)?|subscriptions(?:\/[^/?]+)?)$/,
    /^\/api\/dashboard\/badge-counts$/,
    /^\/api\/gardens(?:\/\d+\/map-objects(?:\/[^/?]+(?:\/units(?:\/[^/?]+)?)?)?)?$/,
    /^\/api\/journal(?:\/[^/?]+)?$/,
    /^\/api\/layout-state$/,
    /^\/api\/media(?:\/summaries)?$/,
    /^\/api\/notifications(?:\/(?:generate|preferences|read-all|[^/?]+(?:\/(?:dismiss|read))?))?$/,
    /^\/api\/plants(?:\/[^/?]+)?$/,
    /^\/api\/plots(?:\/(?:alerts|elevations|[^/?]+(?:\/(?:plant-alerts|plants|tasks))?))?$/,
    /^\/api\/security\/csp-report$/,
    /^\/api\/snapshots$/,
    /^\/api\/saved-views(?:\/presets)?$/,
    /^\/api\/tasks(?:\/(?:batch-action|[^/?]+(?:\/action)?))?$/,
    /^\/api\/version$/,
    /^\/api\/weather\/(?:check|summary|alerts(?:\/\d+\/dismiss)?)$/,
  ].some((pattern) => pattern.test(requestPath));
}

function isSafeDiagnosticPath(value) {
  return isSafeManifestRequestPath(value)
    || String(value || "") === "/api/complete-journey-route-guard";
}

function sanitizeFileBinding(value) {
  return {
    format_version: (
      (typeof value?.format_version === "string" && /^[A-Za-z0-9_.-]{1,40}$/.test(value.format_version))
      || Number.isSafeInteger(value?.format_version)
    ) ? value.format_version : null,
    sha256: /^[a-f0-9]{64}$/.test(String(value?.sha256 || "")) ? value.sha256 : null,
    size_bytes: Number.isSafeInteger(value?.size_bytes) && value.size_bytes >= 0
      ? value.size_bytes
      : null,
    resolved_regular_file: value?.resolved_regular_file === true,
  };
}

function sanitizeClassifiedConsoleDiagnostic(value) {
  return {
    context: safeIdentifier(value?.context),
    diagnostic: sanitizeDiagnostic(value?.diagnostic),
    id: safeIdentifier(value?.id),
    method: new Set(["DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"])
      .has(String(value?.method)) ? String(value.method) : "UNKNOWN",
    path: isSafeDiagnosticPath(value?.path)
      ? String(value.path)
      : sanitizeDiagnostic(value?.path),
    status: Number.isSafeInteger(value?.status) && value.status >= 0 && value.status <= 599
      ? value.status
      : null,
  };
}

function sanitizeManifestEvidence(manifest) {
  const dirty = Boolean(manifest.git?.dirty);
  const output = {
    backend_log: {
      backend_critical_lines: safeNonnegativeInteger(manifest.backend_log?.backend_critical_lines),
      backend_error_lines: safeNonnegativeInteger(manifest.backend_log?.backend_error_lines),
      backend_fatal_lines: safeNonnegativeInteger(manifest.backend_log?.backend_fatal_lines),
      structured_critical_entries: safeNonnegativeInteger(
        manifest.backend_log?.structured_critical_entries,
      ),
      structured_error_entries: safeNonnegativeInteger(manifest.backend_log?.structured_error_entries),
      structured_fatal_entries: safeNonnegativeInteger(manifest.backend_log?.structured_fatal_entries),
      unexpected_error_count: safeNonnegativeInteger(manifest.backend_log?.unexpected_error_count),
    },
    browser: safeIdentifier(manifest.browser),
    database: manifest.database && typeof manifest.database === "object"
      ? sanitizeDatabaseEvidence(manifest.database)
      : null,
    evidence_binding: manifest.evidence_binding && typeof manifest.evidence_binding === "object"
      ? {
        fixture: sanitizeFileBinding(manifest.evidence_binding.fixture),
        lockfiles: {
          frontend_package_lock: sanitizeFileBinding(
            manifest.evidence_binding.lockfiles?.frontend_package_lock,
          ),
          uv_lock: sanitizeFileBinding(manifest.evidence_binding.lockfiles?.uv_lock),
        },
        runtime: {
          architecture: safeIdentifier(manifest.evidence_binding.runtime?.architecture),
          chromium_executable: sanitizeFileBinding(
            manifest.evidence_binding.runtime?.chromium_executable,
          ),
          chromium_launcher: sanitizeFileBinding(
            manifest.evidence_binding.runtime?.chromium_launcher,
          ),
          chromium_version: safeIdentifier(manifest.evidence_binding.runtime?.chromium_version),
          frontend_package_version: safeIdentifier(
            manifest.evidence_binding.runtime?.frontend_package_version,
          ),
          node_version: safeIdentifier(manifest.evidence_binding.runtime?.node_version),
          platform: safeIdentifier(manifest.evidence_binding.runtime?.platform),
          playwright_core_version: safeIdentifier(
            manifest.evidence_binding.runtime?.playwright_core_version,
          ),
        },
      }
      : null,
    ended_at: safeUtcTimestamp(manifest.ended_at),
    failure: manifest.failure ? sanitizeDiagnostic(manifest.failure) : null,
    failure_stage: manifest.failure_stage ? safeIdentifier(manifest.failure_stage) : null,
    filesystem: manifest.filesystem && typeof manifest.filesystem === "object"
      ? structuredClone(manifest.filesystem)
      : null,
    git: {
      clean: !dirty,
      dirty,
      final_head: safeIdentifier(manifest.git?.sha),
      sha: safeIdentifier(manifest.git?.sha),
      worktree_fingerprint: safeIdentifier(manifest.git?.worktree_fingerprint),
    },
    journey_ids: (manifest.journey_ids || []).map(safeIdentifier),
    phase: Number(manifest.phase || 0),
    profiles: [],
    run_id: safeIdentifier(manifest.run_id),
    started_at: safeUtcTimestamp(manifest.started_at),
    status: safeIdentifier(manifest.status),
    suite: safeIdentifier(manifest.suite),
    trace_artifacts: (manifest.trace_artifacts || []).map(sanitizeTraceEvidence),
    through_phase: Number(manifest.through_phase || 0),
  };
  output.profiles = (manifest.profiles || []).map((rawProfile) => {
    const profile = {
      assertions: {
        failed: (rawProfile.assertions?.failed || []).map(safeIdentifier),
        passed: (rawProfile.assertions?.passed || []).map(safeIdentifier),
        skipped: (rawProfile.assertions?.skipped || []).map(safeIdentifier),
      },
      browser_profile: {
        has_touch: Boolean(rawProfile.browser_profile?.has_touch),
        is_mobile: Boolean(rawProfile.browser_profile?.is_mobile),
        max_touch_points: Number(rawProfile.browser_profile?.max_touch_points || 0),
        name: safeIdentifier(rawProfile.browser_profile?.name),
        user_agent_contract: new Set(["desktop-chromium", "pixel-7"])
          .has(rawProfile.browser_profile?.user_agent_contract)
          ? rawProfile.browser_profile.user_agent_contract
          : null,
        viewport: {
          height: Number(rawProfile.browser_profile?.viewport?.height || 0),
          width: Number(rawProfile.browser_profile?.viewport?.width || 0),
        },
      },
      checks: Object.fromEntries(Object.entries(rawProfile.checks || {})
        .filter(([key]) => /^[a-z0-9_]{1,80}$/.test(key))
        .flatMap(([key, value]) => {
          const sanitized = sanitizeCheckValue(value);
          return sanitized === undefined ? [] : [[key, sanitized]];
        })),
      diagnostics: {
        blockedRequests: (rawProfile.diagnostics?.blockedRequests || []).map(sanitizeDiagnostic),
        classifiedConsoleDiagnostics: (
          rawProfile.diagnostics?.classifiedConsoleDiagnostics || []
        ).map(sanitizeClassifiedConsoleDiagnostic),
        consoleErrors: (rawProfile.diagnostics?.consoleErrors || []).map(sanitizeDiagnostic),
        expectedAuth401Responses: Number(rawProfile.diagnostics?.expectedAuth401Responses || 0),
        httpErrors: (rawProfile.diagnostics?.httpErrors || []).map(sanitizeDiagnostic),
        ignoredAuth401ConsoleErrors: Number(rawProfile.diagnostics?.ignoredAuth401ConsoleErrors || 0),
        pageErrors: (rawProfile.diagnostics?.pageErrors || []).map(sanitizeDiagnostic),
        requestFailures: (rawProfile.diagnostics?.requestFailures || []).map(sanitizeDiagnostic),
      },
      failure: rawProfile.failure ? sanitizeDiagnostic(rawProfile.failure) : null,
      profile: safeIdentifier(rawProfile.profile),
      requests: structuredClone(rawProfile.requests || []),
      role: safeIdentifier(rawProfile.role),
      structure: {
        duplicateIds: structuredClone(rawProfile.structure?.duplicateIds || []),
        overflow: Number(rawProfile.structure?.overflow || 0),
        unnamedControls: structuredClone(rawProfile.structure?.unnamedControls || []),
      },
      trace: sanitizeTraceEvidence(rawProfile.trace),
    };
    for (const [key, values] of Object.entries(profile.diagnostics || {})) {
      if (key !== "classifiedConsoleDiagnostics" && Array.isArray(values)) {
        profile.diagnostics[key] = values.map(sanitizeDiagnostic);
      }
    }
    if (profile.structure) {
      for (const key of ["duplicateIds", "unnamedControls"]) {
        if (Array.isArray(profile.structure[key])) {
          profile.structure[key] = profile.structure[key].map(safeIdentifier);
        }
      }
    }
    if (Array.isArray(profile.requests)) {
      profile.requests = profile.requests.map((request) => ({
        actorAuthType: new Set(["none", "session"]).has(String(request.actorAuthType))
          ? String(request.actorAuthType) : null,
        actorRole: safeIdentifier(request.actorRole),
        actorUsername: safeIdentifier(request.actorUsername),
        gardenId: request.gardenId === null || /^\d+$/.test(String(request.gardenId))
          ? request.gardenId
          : sanitizeDiagnostic(request.gardenId),
        method: new Set(["DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"])
          .has(String(request.method)) ? String(request.method) : "UNKNOWN",
        operationId: /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
          .test(String(request.operationId || ""))
          ? String(request.operationId) : null,
        path: isSafeManifestRequestPath(request.path)
          ? String(request.path)
          : sanitizeDiagnostic(request.path),
        requestId: isSafeRequestId(request.requestId) ? String(request.requestId) : null,
        statusCode: Number.isSafeInteger(request.statusCode) && request.statusCode >= 100
          && request.statusCode <= 599 ? request.statusCode : null,
      }));
    }
    return profile;
  });
  if (output.filesystem?.artifacts) {
    output.filesystem.artifacts = output.filesystem.artifacts.map(safeIdentifier);
  }
  for (const key of ["downloads", "media", "terrain"]) {
    if (output.filesystem?.[key]?.entries) {
      output.filesystem[key].entries = output.filesystem[key].entries.map(safeIdentifier);
    }
  }
  output.canonical_projection_digests = canonicalProjectionDigests(output);
  return output;
}

function writeManifestAtomic(manifest) {
  const target = path.join(ARTIFACT_DIR, "complete-journeys-manifest.json");
  const temporary = `${target}.tmp-${process.pid}`;
  const descriptor = fs.openSync(temporary, "wx", 0o600);
  const safeManifest = sanitizeManifestEvidence(manifest);
  try {
    fs.writeFileSync(descriptor, `${JSON.stringify(safeManifest, null, 2)}\n`, "utf8");
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
  fs.renameSync(temporary, target);
  fs.chmodSync(target, 0o600);
  return safeManifest;
}

function assertNoResponseMocks() {
  const sources = [
    fs.readFileSync(__filename, "utf8"),
    fs.readFileSync(path.join(ROOT, "scripts/e2e/journeys/foundation.cjs"), "utf8"),
    fs.readFileSync(path.join(ROOT, "scripts/e2e/journeys/gardenMapPlants.cjs"), "utf8"),
    fs.readFileSync(path.join(ROOT, "scripts/e2e/journeys/dailyAttentionWork.cjs"), "utf8"),
  ].join("\n");
  const forbiddenCalls = [
    `route.${"fulfill"}(`,
    `context.${"addCookies"}(`,
    `page.${"setContent"}(`,
  ];
  for (const forbidden of forbiddenCalls) {
    assert(!sources.includes(forbidden), `Complete journey harness must not use ${forbidden}`);
  }
}

function assertNoNodeRequestClients() {
  const sources = [
    fs.readFileSync(path.join(ROOT, "scripts/e2e/completeJourneyBrowser.cjs"), "utf8"),
    fs.readFileSync(path.join(ROOT, "scripts/e2e/journeys/gardenMapPlants.cjs"), "utf8"),
    fs.readFileSync(path.join(ROOT, "scripts/e2e/journeys/dailyAttentionWork.cjs"), "utf8"),
  ].join("\n");
  for (const forbidden of [
    "context.request",
    "page.context().request",
    "request.newContext",
  ]) {
    assert(!sources.includes(forbidden), `Complete journey browser proof must not use ${forbidden}`);
  }
}

async function main() {
  assertRunnerEnvironment();
  assertPrivateFiles();
  assertNoResponseMocks();
  assertNoNodeRequestClients();
  const fixture = readJson(FIXTURE_PATH);
  assertFixtureAttentionClock(fixture);
  let manifest = {
    backend_log: null,
    browser: "chromium",
    database: {
      audit_projection: auditManifestProjection(fixture.database_snapshot.audit_state),
    },
    evidence_binding: evidenceBinding(),
    ended_at: null,
    failure: null,
    failure_stage: null,
    git: sourceProvenance(fixture.git),
    journey_ids: [
      ...(phaseSelected(0) ? ["A1"] : []),
      ...(phaseSelected(1) ? ["A3", "M1", "M2", "M3", "M4", "CROSS-01"] : []),
      ...(phaseSelected(2) ? ["D1", "D2", "D3", "D4", "D5", "R1"] : []),
    ],
    phase: PHASE,
    profiles: [],
    run_id: crypto.randomUUID(),
    started_at: new Date().toISOString(),
    status: "running",
    suite: "complete-journeys-e2e",
    through_phase: THROUGH_PHASE,
  };
  let browser;
  let phaseZeroProfileEvidence = null;
  let phaseZeroProfiles = [];
  let phaseOneAuditEvidence = null;
  let phaseOneDatabase = null;
  let phaseOneStatePreservedAfterPhaseTwo = false;
  let phaseOneProfileEvidence = null;
  let phaseOneProfiles = [];
  let phaseTwoDatabaseEvidence = null;
  let phaseTwoAuditEvidence = null;
  let phaseTwoAuditBaseline = null;
  let phaseTwoPreparation = null;
  let phaseTwoMaintenance = null;
  let phaseTwoPreferenceDelivery = null;
  let phaseTwoProfileEvidence = null;
  let phaseTwoProfiles = [];
  let phaseTwoReadOnlyPermutationEvidence = null;
  let phaseTwoReadOnlyProfiles = [];
  let thrownError = null;
  let currentStage = "runner-startup";
  try {
    const { chromium, devices } = require("../frontend/node_modules/playwright-core");
    browser = await chromium.launch({
      downloadsPath: process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_DOWNLOAD_DIR,
      env: {
        HOME: process.env.HOME || "/tmp",
        LANG: process.env.LANG || "C.UTF-8",
        PATH: process.env.PATH || "/usr/bin:/bin",
        TMPDIR: process.env.TMPDIR || "/tmp",
      },
      executablePath: CHROMIUM_EXECUTABLE,
      headless: true,
    });
    manifest.evidence_binding.runtime.chromium_version = await browser.version();
    if (phaseSelected(0)) {
      currentStage = "phase-zero-browser";
      const phaseZeroProfileStart = manifest.profiles.length;
      await runFoundation({
        artifactDir: ARTIFACT_DIR,
        baseUrl: BASE_URL,
        browser,
        devices,
        fixture,
        onProfile: (profile) => manifest.profiles.push(profile),
        password: PASSWORD,
        username: USERNAME,
      });
      phaseZeroProfiles = manifest.profiles.slice(phaseZeroProfileStart);
      phaseZeroProfileEvidence = assertPhaseZeroProfileEvidence(phaseZeroProfiles);
    }
    if (phaseSelected(1)) {
      currentStage = "phase-one-browser";
      const phaseOneProfileStart = manifest.profiles.length;
      await runGardenMapPlants({
        artifactDir: ARTIFACT_DIR,
        baseUrl: BASE_URL,
        browser,
        devices,
        fixture,
        onProfile: (profile) => manifest.profiles.push(profile),
        password: PASSWORD,
        username: USERNAME,
      });
      phaseOneProfiles = manifest.profiles.slice(phaseOneProfileStart);
      currentStage = "phase-one-database-boundary";
      phaseOneDatabase = databaseSnapshot();
    }
    if (phaseSelected(2)) {
      currentStage = "phase-two-prerequisites";
      phaseTwoPreparation = preparePhaseTwoFixtures();
      assert(
        canonicalJson(phaseTwoPreparation) === canonicalJson({
          fetched_at_ms: fixture.clock.attention_now_ms,
          garden_ids: [fixture.gardens.alpha.id, fixture.gardens.beta.id].sort((a, b) => a - b),
          weather_cache_rows: 2,
        }),
        "Phase 2 deterministic weather preparation was unexpected",
      );
      currentStage = "phase-two-maintenance";
      phaseTwoMaintenance = runPhaseTwoMaintenance();
      phaseTwoAuditBaseline = databaseSnapshot().audit_state;
      currentStage = "phase-two-read-only-permutation";
      const phaseTwoReadOnlyStart = manifest.profiles.length;
      await runPhaseTwoReadOnlyPermutation({
        artifactDir: ARTIFACT_DIR,
        baseUrl: BASE_URL,
        browser,
        devices,
        fixture,
        onProfile: (profile) => manifest.profiles.push(profile),
        password: PASSWORD,
        username: USERNAME,
      });
      phaseTwoReadOnlyProfiles = manifest.profiles.slice(phaseTwoReadOnlyStart);
      phaseTwoReadOnlyPermutationEvidence = assertPhaseTwoReadOnlyPermutationEvidence(
        phaseTwoReadOnlyProfiles,
      );
      currentStage = "phase-two-browser";
      const phaseTwoProfileStart = manifest.profiles.length;
      await runDailyAttentionWork({
        artifactDir: ARTIFACT_DIR,
        baseUrl: BASE_URL,
        browser,
        devices,
        fixture,
        onProfile: (profile) => manifest.profiles.push(profile),
        onPreferencesSaved: () => {
          assert(!phaseTwoPreferenceDelivery,
            "Phase 2 preference delivery fixture ran more than once");
          phaseTwoPreferenceDelivery = runPhaseTwoPreferenceDelivery();
          return phaseTwoPreferenceDelivery;
        },
        password: PASSWORD,
        username: USERNAME,
      });
      assert(phaseTwoPreferenceDelivery,
        "Phase 2 browser did not run the post-save preference delivery boundary");
      phaseTwoProfiles = manifest.profiles.slice(phaseTwoProfileStart);
    }
    currentStage = "final-database-snapshot";
    const finalDatabase = databaseSnapshot();
    currentStage = "cumulative-assertions";
    manifest.database = {
      audit_projection: auditManifestProjection(finalDatabase.audit_state),
      whole_table_projections: {
        final: finalDatabase.domain_tables,
        initial: fixture.database_snapshot.domain_tables,
        phase_one_boundary: phaseOneDatabase?.domain_tables ?? null,
      },
    };
    const domainTableNames = new Set([
      ...Object.keys(fixture.database_snapshot.domain_tables),
      ...Object.keys(finalDatabase.domain_tables),
    ]);
    const changedDomainTables = [...domainTableNames].filter(
      (table) => JSON.stringify(finalDatabase.domain_tables[table])
        !== JSON.stringify(fixture.database_snapshot.domain_tables[table]),
    );
    const phaseOneRan = phaseSelected(1);
    const phaseTwoRan = phaseSelected(2);
    if (phaseOneRan) phaseOneProfileEvidence = assertPhaseOneProfileEvidence(phaseOneProfiles);
    if (phaseTwoRan) {
      phaseTwoProfileEvidence = assertPhaseTwoProfileEvidence(phaseTwoProfiles);
      phaseTwoDatabaseEvidence = {
        ...assertPhaseTwoDatabaseState(
          finalDatabase.phase_two_state,
          fixture,
          phaseTwoMaintenance,
          phaseTwoPreferenceDelivery,
        ),
        ...assertPhaseTwoOfflineOperationReplay(phaseTwoProfiles, finalDatabase.phase_two_state, fixture),
      };
      phaseTwoAuditEvidence = assertPhaseTwoAuditEvents(
        phaseTwoAuditBaseline,
        finalDatabase.audit_state,
        [...phaseTwoReadOnlyProfiles, ...phaseTwoProfiles],
        fixture,
      );
      if (phaseOneRan) {
        assert(phaseOneDatabase, "Phase 1 database boundary snapshot is missing before Phase 2");
        assertPhaseOneStatePreservedAfterPhaseTwo(
          phaseOneDatabase.phase_one_state,
          finalDatabase.phase_one_state,
          fixture,
        );
        phaseOneStatePreservedAfterPhaseTwo = true;
      }
    }
    const phaseOneSemanticDeltaTables = phaseOneRan ? new Set([
      "app_settings",
      "garden_journal_entries",
      "garden_journal_entry_plants",
      "garden_journal_entry_plots",
      "garden_map_object_units",
      "garden_map_objects",
      "garden_memberships",
      "gardens",
      "harvest_entries",
      "harvest_entry_plants",
      "harvest_entry_plots",
      "layout_snapshots",
      "layout_state",
      "plot_ownership",
      "plots",
      "weather_cache",
    ]) : new Set();
    const phaseOneChangedDomainTables = phaseOneRan ? [...new Set([
      ...Object.keys(fixture.database_snapshot.domain_tables),
      ...Object.keys(phaseOneDatabase?.domain_tables || {}),
    ])].filter(
      (table) => JSON.stringify(phaseOneDatabase?.domain_tables?.[table])
        !== JSON.stringify(fixture.database_snapshot.domain_tables[table]),
    ) : [];
    const phaseOneForbiddenDomainTables = phaseOneChangedDomainTables.filter(
      (table) => !phaseOneSemanticDeltaTables.has(table),
    );
    assert(
      phaseOneForbiddenDomainTables.length === 0,
      `Phase 1 changed forbidden domain tables: ${phaseOneForbiddenDomainTables.join(", ")}`,
    );
    const phaseTwoSemanticDeltaTables = phaseTwoRan ? new Set([
      ...phaseOneSemanticDeltaTables,
      "app_settings",
      "attention_outcomes",
      "calendar_subscriptions",
      "garden_calendar_event_plants",
      "garden_calendar_event_plots",
      "garden_calendar_events",
      "garden_issues",
      "garden_journal_entries",
      "garden_journal_entry_plants",
      "garden_journal_entry_plots",
      "garden_task_plants",
      "garden_task_plots",
      "garden_tasks",
      "notification_events",
      "offline_create_operations",
      "plants",
      "plot_plants",
      "user_attention_item_state",
      "user_attention_preferences",
      "user_calendar_preferences",
      "user_notification_preferences",
      "weather_alert_plants",
      "weather_alerts",
    ]) : phaseOneSemanticDeltaTables;
    const forbiddenDomainTables = changedDomainTables.filter(
      (table) => !phaseTwoSemanticDeltaTables.has(table),
    );
    assert(
      forbiddenDomainTables.length === 0,
      `Cumulative journey changed forbidden domain tables: ${forbiddenDomainTables.join(", ")}`,
    );
    const wholeTableProjectionEvidence = assertWholeTableProjectionCoverage(
      fixture.database_snapshot.domain_tables,
      finalDatabase.domain_tables,
      phaseTwoSemanticDeltaTables,
    );
    for (const [table, count] of Object.entries(finalDatabase.domain_counts)) {
      if (Object.hasOwn(finalDatabase.domain_tables, table)) {
        assert(finalDatabase.domain_tables[table].count === count,
          `Final whole-table projection count disagreed for ${table}`);
      }
    }
    assert(
      fixture.database_snapshot.auth_state.admin_session_count === 0,
      "Foundation fixture unexpectedly started with an administrator session",
    );
    assert(
      finalDatabase.auth_state.admin_session_count
        === manifest.profiles.filter((profile) => profile.role === "admin").length,
      "Administrator session count did not match browser profiles",
    );
    assert(
      finalDatabase.auth_state.invalid_session_count === 0,
      "Foundation login created an invalid session row",
    );
    assert(
      finalDatabase.auth_state.users_expected_digest
        === fixture.database_snapshot.auth_state.users_expected_digest,
      "Browser login changed auth user state beyond last_login_at",
    );
    const expectedSessionCounts = expectedSessionUserCounts(
      fixture,
      manifest.profiles,
      phaseOneRan,
    );
    assert(
      JSON.stringify(finalDatabase.auth_state.session_user_counts)
        === JSON.stringify(expectedSessionCounts),
      `Browser login created unexpected user sessions: ${JSON.stringify(finalDatabase.auth_state.session_user_counts)}`,
    );
    assert(
      fixture.database_snapshot.auth_state.admin_last_login_at === null
        && finalDatabase.auth_state.admin_last_login_at !== null,
      "Browser login did not persist the expected administrator last-login timestamp",
    );
    assert(
      fixture.database_snapshot.audit_state.total_count === 0,
      "Foundation fixture unexpectedly started with audit events",
    );
    assert(
      finalDatabase.audit_state.expected_login_count === manifest.profiles.length,
      `Browser journey login audit count was unexpected: ${JSON.stringify(finalDatabase.audit_state)}`,
    );
    if (phaseOneRan) {
      assert(phaseOneDatabase, "Phase 1 database boundary snapshot is missing");
      const phaseOneProfileCount = phaseZeroProfiles.length + phaseOneProfiles.length;
      const initialPhaseOne = fixture.database_snapshot.phase_one_state;
      const finalPhaseOne = phaseOneDatabase.phase_one_state;
      const expectedCountDeltas = {
        app_settings: 1,
        garden_journal_entries: 1,
        garden_journal_entry_plants: 1,
        garden_journal_entry_plots: 1,
        garden_map_object_units: 0,
        garden_map_objects: 0,
        garden_memberships: 4,
        gardens: 3,
        harvest_entries: 1,
        harvest_entry_plants: 1,
        harvest_entry_plots: 1,
        layout_snapshots: 1,
        layout_state: 2,
        plot_ownership: 2,
        plots: 2,
      };
      for (const [table, delta] of Object.entries(expectedCountDeltas)) {
        assert(
          phaseOneDatabase.domain_tables[table].count
            === fixture.database_snapshot.domain_tables[table].count + delta,
          `Phase 1 ${table} count delta was not ${delta}`,
        );
      }
      assertPhaseOneStableDomainProjection(
        initialPhaseOne.stable_domain_projection,
        finalPhaseOne.stable_domain_projection,
      );
      assert(finalPhaseOne.alpha_address === initialPhaseOne.alpha_address, "Garden settings were not restored");
      assert(
        JSON.stringify(finalPhaseOne.alpha_map_object) === JSON.stringify(initialPhaseOne.alpha_map_object),
        "Alpha map object geometry/style was not restored",
      );
      assert(
        JSON.stringify(finalPhaseOne.alpha_map_unit) === JSON.stringify(initialPhaseOne.alpha_map_unit),
        "Alpha nested map unit changed",
      );
      assertExactPhaseOneRestoreImportGraphs(
        initialPhaseOne.restore_import_graphs,
        finalPhaseOne.restore_import_graphs,
      );
      assert(finalPhaseOne.indoor_room_label === initialPhaseOne.indoor_room_label, "Indoor room was not restored");
      const expectedIndoorAssignment = {
        plant_garden_id: fixture.phase_one.indoor.garden_id,
        plant_id: fixture.phase_one.indoor.plant_id,
        plant_owner_username: fixture.phase_one.indoor.owner_username,
        plot_garden_id: fixture.phase_one.indoor.garden_id,
        plot_id: fixture.phase_one.indoor.plot_id,
        plot_owner_username: fixture.phase_one.indoor.owner_username,
        quantity: fixture.phase_one.indoor.quantity,
        room_label: fixture.phase_one.indoor.room_label,
        seen_growing: fixture.phase_one.indoor.seen_growing,
        seen_growing_date: fixture.phase_one.indoor.seen_growing_date,
      };
      assert(
        JSON.stringify(initialPhaseOne.indoor_assignment) === JSON.stringify(expectedIndoorAssignment),
        "Phase 1 fixture indoor assignment ownership is incorrect",
      );
      assert(
        JSON.stringify(finalPhaseOne.indoor_assignment) === JSON.stringify(expectedIndoorAssignment),
        "Phase 1 indoor assignment semantics were not preserved",
      );
      const expectedSeededSavedView = [{
        garden_id: fixture.phase_one.saved_view.garden_id,
        is_preset: false,
        label: fixture.phase_one.saved_view.label,
        owner_username: fixture.phase_one.saved_view.owner_username,
        view_type: fixture.phase_one.saved_view.view_type,
      }];
      assert(
        JSON.stringify(initialPhaseOne.seeded_saved_views) === JSON.stringify(expectedSeededSavedView),
        "Phase 1 fixture saved-view ownership is incorrect",
      );
      assert(
        JSON.stringify(finalPhaseOne.seeded_saved_views) === JSON.stringify(expectedSeededSavedView),
        "Seeded saved-view ownership changed during Phase 1",
      );
      assertNoLifecycleResidue(initialPhaseOne.browser_lifecycle, "Phase 1 fixture");
      assertNoLifecycleResidue(finalPhaseOne.browser_lifecycle, "Phase 1");
      assertNoCrossGardenLinks(initialPhaseOne.cross_garden_links, "Phase 1 fixture");
      assertNoCrossGardenLinks(finalPhaseOne.cross_garden_links, "Phase 1");
      assert(finalPhaseOne.temp_map_object_count === 0, "Temporary map objects remain");
      assert(finalPhaseOne.temp_plant_count === 0, "Temporary plant remains");
      assert(finalPhaseOne.temp_saved_view_count === 0, "Temporary saved view remains");
      assert(finalPhaseOne.harvest_count === initialPhaseOne.harvest_count + 1, "Mobile harvest was not persisted");
      assert(finalPhaseOne.journal_count === initialPhaseOne.journal_count + 1, "Harvest journal was not persisted");
      assert(
        finalPhaseOne.mobile_snapshot_count === initialPhaseOne.mobile_snapshot_count + 1,
        "Mobile snapshot was not persisted",
      );
      assert(initialPhaseOne.mobile_snapshots.length === 0, "Fixture unexpectedly has a mobile snapshot");
      assertExactPhaseOneMobileSnapshot(finalPhaseOne.mobile_snapshots, {
        garden_id: fixture.phase_one.mobile_snapshot.garden_id,
        garden_owner_username: fixture.phase_one.mobile_snapshot.owner_username,
        name: fixture.phase_one.mobile_snapshot.name,
        payload: finalPhaseOne.alpha_snapshot_payload,
      });
      assert(
        canonicalJson(initialPhaseOne.quick_action_records) === canonicalJson({
          harvest_rollups: [], harvests: [], journals: [],
        }),
        "Fixture unexpectedly has retained quick-action records",
      );
      assertExactPhaseOneQuickActionRecords(finalPhaseOne.quick_action_records, fixture);
      assert(
        initialPhaseOne.alpha_map_unit_count >= 1
          && finalPhaseOne.alpha_map_unit_count === initialPhaseOne.alpha_map_unit_count,
        "Parent map-object deletion did not cascade its nested unit",
      );
      const expectedLifecycleAudit = {
        assignment_create_count: 4,
        assignment_delete_count: 2,
        nested_unit_create_count: 4,
        nested_unit_direct_delete_count: 2,
        nested_unit_update_count: 2,
        plant_create_count: 2,
        plant_delete_count: 2,
        plant_update_count: 4,
        saved_view_create_count: 2,
        saved_view_delete_count: 2,
      };
      assert(
        JSON.stringify(finalPhaseOne.lifecycle_audit) === JSON.stringify(expectedLifecycleAudit),
        `Phase 1 plant, saved-view, or nested-unit lifecycle was unexpected: ${JSON.stringify(finalPhaseOne.lifecycle_audit)}`,
      );
      assert(
        finalPhaseOne.lifecycle_audit.nested_unit_direct_delete_count === 2,
        "Nested unit direct deletion was not exercised once per administrator device",
      );
      const onboardingGardens = finalPhaseOne.onboarding_gardens.filter(
        (garden) => [
          fixture.phase_one.onboarding.desktop_garden_name,
          fixture.phase_one.onboarding.mobile_garden_name,
        ].includes(garden.name)
          && garden.onboarding_complete,
      );
      assert(onboardingGardens.length === 2, "Desktop/mobile onboarding gardens were not persisted exactly once");
      assert(initialPhaseOne.onboarding_target_gardens.length === 0, "Fixture unexpectedly has onboarding targets");
      assert(
        canonicalJson(initialPhaseOne.onboarding_target_graphs) === canonicalJson({}),
        "Fixture unexpectedly has onboarding target graphs",
      );
      assert(
        canonicalJson(initialPhaseOne.onboarding_default_context) === canonicalJson({
          gardens: [], memberships: [],
        }),
        "Fixture unexpectedly has an onboarding default context",
      );
      assertExactPhaseOneOnboardingOwnership(finalPhaseOne.onboarding_target_gardens, {
        [fixture.phase_one.onboarding.desktop_garden_name]: {
          address: fixture.phase_one.onboarding.address,
          grid_cols: fixture.phase_one.onboarding.grid_cols,
          grid_rows: fixture.phase_one.onboarding.grid_rows,
          latitude: fixture.phase_one.onboarding.latitude,
          layout: fixture.phase_one.onboarding.house,
          longitude: fixture.phase_one.onboarding.longitude,
          owner_username: fixture.phase_one.onboarding.desktop_username,
        },
        [fixture.phase_one.onboarding.mobile_garden_name]: {
          address: fixture.phase_one.onboarding.address,
          grid_cols: fixture.phase_one.onboarding.grid_cols,
          grid_rows: fixture.phase_one.onboarding.grid_rows,
          latitude: fixture.phase_one.onboarding.latitude,
          layout: fixture.phase_one.onboarding.house,
          longitude: fixture.phase_one.onboarding.longitude,
          owner_username: fixture.phase_one.onboarding.mobile_username,
        },
      });
      assertExactPhaseOneOnboardingGraphs(finalPhaseOne.onboarding_target_graphs, {
        [fixture.phase_one.onboarding.desktop_garden_name]: {
          address: fixture.phase_one.onboarding.address,
          grid_cols: fixture.phase_one.onboarding.grid_cols,
          grid_rows: fixture.phase_one.onboarding.grid_rows,
          latitude: fixture.phase_one.onboarding.latitude,
          layout: fixture.phase_one.onboarding.house,
          longitude: fixture.phase_one.onboarding.longitude,
          onboarding_complete: true,
          owner_username: fixture.phase_one.onboarding.desktop_username,
          slug: fixture.phase_one.onboarding.desktop_garden_slug,
        },
        [fixture.phase_one.onboarding.mobile_garden_name]: {
          address: fixture.phase_one.onboarding.address,
          grid_cols: fixture.phase_one.onboarding.grid_cols,
          grid_rows: fixture.phase_one.onboarding.grid_rows,
          latitude: fixture.phase_one.onboarding.latitude,
          layout: fixture.phase_one.onboarding.house,
          longitude: fixture.phase_one.onboarding.longitude,
          onboarding_complete: true,
          owner_username: fixture.phase_one.onboarding.mobile_username,
          slug: fixture.phase_one.onboarding.mobile_garden_slug,
        },
      });
      assertExactPhaseOneOnboardingDefaultContext(
        finalPhaseOne.onboarding_default_context,
        fixture,
      );
      assert(
        finalPhaseOne.onboarding_gardens.length === initialPhaseOne.onboarding_gardens.length + 4,
        "Onboarding did not create the expected visible gardens and legacy default contexts",
      );
      phaseOneAuditEvidence = assertPhaseOneAuditContract(
        phaseOneDatabase.audit_state,
        phaseOneProfileCount,
      );
      assert(
        phaseOneDatabase.phase_one_state.mobile_snapshot_count
          === fixture.database_snapshot.phase_one_state.mobile_snapshot_count + 1,
        "Phase 1 mobile action did not create exactly one snapshot",
      );
      assert(
        phaseOneDatabase.audit_state.expected_phase_one_snapshot_count === 2,
        "Phase 1 desktop and mobile snapshots did not create exactly two expected audit events",
      );
    }
    manifest.database = {
      audit_projection: auditManifestProjection(finalDatabase.audit_state),
      auth_expected_writes: {
        admin_last_login_updated: true,
        auth_users_other_fields_unchanged: true,
        session_rows_valid: true,
        session_count_after: finalDatabase.auth_state.admin_session_count,
        session_count_before: fixture.database_snapshot.auth_state.admin_session_count,
      },
      audit_expected_writes: {
        flexible_read_event_types: phaseOneAuditEvidence?.flexible_read_event_types ?? 0,
        expected_event_types: phaseOneAuditEvidence?.expected_event_types ?? 0,
        phase_one_contract_enforced: Boolean(phaseOneAuditEvidence),
        login_success_count: finalDatabase.audit_state.expected_login_count,
        phase_one_snapshot_count: finalDatabase.audit_state.expected_phase_one_snapshot_count,
        phase_two_audit_event_count: phaseTwoAuditEvidence?.phase_two_audit_event_count ?? 0,
        phase_two_audit_events_exact: phaseTwoAuditEvidence?.phase_two_audit_events_exact ?? null,
        phase_two_audit_timestamps_frozen:
          phaseTwoAuditEvidence?.phase_two_audit_timestamps_frozen ?? null,
        unexpected_count: phaseOneAuditEvidence?.unexpected_count ?? null,
      },
      cluster_fingerprint: crypto
        .createHash("sha256")
        .update(
          `${process.env.GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER}:`
          + process.env.GARDENOPS_DISPOSABLE_POSTGRES_MARKER,
        )
        .digest("hex"),
      semantic_delta_tables: [...phaseTwoSemanticDeltaTables].sort(),
      whole_table_projection_coverage: wholeTableProjectionEvidence,
      whole_table_projections: {
        final: finalDatabase.domain_tables,
        initial: fixture.database_snapshot.domain_tables,
        phase_one_boundary: phaseOneDatabase?.domain_tables ?? null,
      },
      domain_counts_unchanged: !phaseOneRan && !phaseTwoRan,
      domain_digests_unchanged: !phaseOneRan && !phaseTwoRan,
      phase_zero_enforcement: phaseZeroProfileEvidence ? {
        browser_profile_matrix: phaseZeroProfileEvidence.profile_matrix_enforced === true,
        cumulative_before_phase_one: true,
      } : null,
      phase_one_enforcement: phaseOneRan ? {
        assignments_and_lifecycle: true,
        browser_profile_matrix: phaseOneProfileEvidence?.profile_matrix_enforced === true,
        cross_garden_links_absent: true,
        mobile_snapshot_garden_owned: true,
        nested_unit_parent_cascade: true,
        onboarding_default_context_exact: true,
        onboarding_generated_plot_ownership_and_layout_graph: true,
        onboarding_grid_location_and_ownership: true,
        quick_action_harvest_journal_links_and_rollup_exact: true,
        restore_import_graph_unchanged: true,
        seeded_plant_and_saved_view_ownership: true,
        snapshot_payload_and_ownership_exact: true,
        phase_one_scoped_state_preserved_after_phase_two: phaseTwoRan
          ? phaseOneStatePreservedAfterPhaseTwo
          : null,
        stable_mutable_domain_rows_unchanged: true,
        targeted_audit_contract: Boolean(phaseOneAuditEvidence),
      } : null,
      phase_one_mobile_snapshot_count: phaseOneDatabase?.phase_one_state.mobile_snapshot_count
        ?? finalDatabase.phase_one_state.mobile_snapshot_count,
      phase_two_enforcement: phaseTwoRan ? {
        ...phaseTwoDatabaseEvidence,
        ...phaseTwoAuditEvidence,
        browser_profile_matrix: phaseTwoProfileEvidence?.profile_matrix_enforced === true,
        profile_execution: phaseTwoProfileEvidence,
        read_only_permutation_execution: phaseTwoReadOnlyPermutationEvidence,
        phase_fixture_scope: PHASE === 2 && THROUGH_PHASE === 2
          ? "fresh-fixture-phase-two-only"
          : "fresh-fixture-cumulative-through-phase-two",
        maintenance: phaseTwoMaintenance,
        preference_delivery: phaseTwoPreferenceDelivery,
        preparation: phaseTwoPreparation,
        whole_table_projection_coverage: wholeTableProjectionEvidence,
      } : null,
      phase_boundaries: {
        phase_one_audit_total: phaseOneDatabase?.audit_state.total_count ?? null,
        phase_one_profile_count: phaseOneRan
          ? phaseZeroProfiles.length + phaseOneProfiles.length
          : null,
        phase_two_profile_count: phaseTwoRan ? phaseTwoProfiles.length : null,
        phase_two_read_only_permutation_profile_count: phaseTwoRan
          ? phaseTwoReadOnlyProfiles.length
          : null,
      },
      final: finalDatabase.domain_counts,
      initial: fixture.database_snapshot.domain_counts,
    };
    manifest.filesystem = filesystemState();
    assert(manifest.filesystem.downloads.empty, "Browser journey wrote download files");
    assert(manifest.filesystem.media.empty, "Browser journey wrote media files");
    assert(manifest.filesystem.terrain.empty, "Browser journey wrote terrain files");
    manifest.trace_artifacts = assertTraceArtifacts(manifest.profiles);
    manifest.backend_log = backendErrorEvidence();
    assertNoUnexpectedBackendErrors(undefined, manifest.backend_log);
    await browser.close();
    browser = null;
    manifest.status = "passed";
    currentStage = "complete";
  } catch (error) {
    thrownError = error;
    writePrivateFailure(error);
    manifest.status = "failed";
    manifest.failure = safeFailure(error);
    manifest.failure_stage = currentStage;
  } finally {
    if (browser) {
      try {
        await browser.close();
        browser = null;
      } catch (error) {
        manifest.status = "failed";
        manifest.failure = safeFailure(error);
        if (!thrownError) thrownError = error;
      }
    }
    try {
      const finalGit = gitState();
      manifest.git = sourceProvenance(finalGit);
      if (!thrownError) assertSourceRevisionStable(fixture.git, finalGit);
    } catch (error) {
      if (!thrownError) {
        thrownError = error;
        manifest.status = "failed";
        manifest.failure = safeFailure(error);
      }
    }
    manifest.ended_at = new Date().toISOString();
    if (!manifest.filesystem) {
      try {
        manifest.filesystem = filesystemState();
      } catch (error) {
        manifest.filesystem = { error: safeFailure(error) };
      }
    }
    if (!manifest.trace_artifacts && manifest.profiles.length > 0) {
      try {
        manifest.trace_artifacts = assertTraceArtifacts(manifest.profiles);
      } catch (error) {
        manifest.trace_artifacts = [];
        if (!thrownError) {
          thrownError = error;
          manifest.status = "failed";
          manifest.failure = safeFailure(error);
        }
      }
    }
    manifest = writeManifestAtomic(manifest);
  }
  if (thrownError) throw thrownError;
  process.stdout.write(`${JSON.stringify(manifest)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`Complete journey E2E failed: ${safeFailure(error)}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  assertExactPhaseOneOnboardingOwnership,
  assertExactPhaseOneOnboardingGraphs,
  assertExactPhaseOneOnboardingDefaultContext,
  assertExactPhaseOneQuickActionRecords,
  assertExactPhaseOneMobileSnapshot,
  assertExactPhaseOneRestoreImportGraphs,
  assertPhaseOneStatePreservedAfterPhaseTwo,
  assertPhaseOneStableDomainProjection,
  assertNoCrossGardenLinks,
  assertNoLifecycleResidue,
  assertNoUnexpectedBackendErrors,
  assertPhaseZeroProfileEvidence,
  assertPhaseOneAuditContract,
  assertPhaseOneProfileEvidence,
  assertPhaseTwoAuditEvents,
  assertPhaseTwoDatabaseState,
  assertPhaseTwoOfflineOperationReplay,
  assertPhaseTwoMaintenanceSpec,
  assertPhaseTwoScopedMutableRows,
  assertExpectedMaintenanceMutations,
  exactMaintenanceNotification,
  assertPhaseTwoProfileEvidence,
  assertPhaseTwoProfileOrder,
  assertPhaseTwoReadOnlyPermutationEvidence,
  assertNoResponseMocks,
  assertNoNodeRequestClients,
  assertTraceArtifacts,
  assertPageStructure,
  assertSourceRevisionStable,
  backendErrorEvidence,
  expectedPhaseOneRestoreGraphsAfterPhaseTwo,
  expectedPhaseOneStableDomainProjectionAfterPhaseTwo,
  expectedSessionUserCounts,
  gitState,
  isSafeManifestRequestPath,
  isSafeRequestId,
  isPhaseTwoAuditPath,
  phaseTwoBrowserMutationRecords,
  phaseOneAuditExpectedEvents,
  phaseSelected,
  safeUtcTimestamp,
  safeFailure,
  sanitizeManifestEvidence,
  sanitizeDatabaseEvidence,
  canonicalProjectionDigests,
  auditManifestProjection,
  assertWholeTableProjectionCoverage,
  evidenceBinding,
  isElfExecutable,
  resolveChromiumExecutable,
  sourceProvenance,
  writeManifestAtomic,
  runPhaseTwoReadOnlyPermutation,
};
