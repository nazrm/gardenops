#!/usr/bin/env node
"use strict";

const { execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { assert, assertPageStructure } = require("./e2e/completeJourneyAssertions.cjs");
const {
  assertLoopbackBaseUrl,
  isAllowedUrl,
  sanitizeDiagnostic,
} = require("./e2e/completeJourneyBrowser.cjs");
const { runFoundation } = require("./e2e/journeys/foundation.cjs");
const { runGardenMapPlants } = require("./e2e/journeys/gardenMapPlants.cjs");

const ROOT = path.resolve(__dirname, "..");
const BASE_URL = process.env.BASE_URL || "";
const ARTIFACT_DIR = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR || "";
const FIXTURE_PATH = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_FIXTURE_PATH || "";
const PHASE = Number(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PHASE || "-1");
const THROUGH_PHASE = Number(process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_THROUGH_PHASE || "-1");
const USERNAME = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_USERNAME || "";
const PASSWORD = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_PASSWORD || ""; // push-sanitizer: allow SECRET_ASSIGNMENT - environment lookup only
const CHROMIUM_EXECUTABLE = fs.existsSync("/usr/bin/chromium-browser")
  ? "/usr/bin/chromium-browser"
  : "/usr/bin/chromium";

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
  assert(isAllowedUrl(BASE_URL), "Complete journey BASE_URL is not loopback");
  assert(fs.existsSync(CHROMIUM_EXECUTABLE), "System Chromium is required");
  assert(process.env.GARDENOPS_LOGS_DIR, "Complete journey backend log directory is required");
  assert(PHASE <= 1 && THROUGH_PHASE <= 1, "Requested phase is not implemented");
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

function phaseOneAuditExpectedEvents(loginCount) {
  return [
    [10, "DELETE", "/api/gardens/{garden_id}/map-objects/{public_id}", 200],
    [1, "DELETE", "/api/plants/{created_plant_id}", 200],
    [1, "DELETE", "/api/plots/OPT-JOURNEY-A-PLOT/plants/{created_plant_id}", 204],
    [1, "DELETE", "/api/plots/P1MOBILEPLOTEDITED", 204],
    [1, "DELETE", "/api/saved-views/{saved_view_id}", 200],
    [1, "DELETE", "/api/snapshots/{public_id}", 200],
    [7, "PATCH", "/api/gardens/{garden_id}/map-objects/{public_id}", 200],
    [1, "PATCH", "/api/gardens/{garden_id}/map-objects/obj_optimization_journeys_a", 200],
    [2, "PATCH", "/api/gardens/{garden_id}/map-objects/{public_id}/units/{public_id}", 200],
    [2, "PATCH", "/api/gardens/{garden_id}/settings", 200],
    [2, "PATCH", "/api/plants/{created_plant_id}", 200],
    [1, "PATCH", "/api/plots/P1MOBILEPLOT", 200],
    [2, "PATCH", "/api/plots/COMPLETE-PHASE-ONE-INDOOR/plants/COMPLETE-PHASE-ONE-BASIL", 200],
    [loginCount, "POST", "/api/auth/login", 200],
    [8, "POST", "/api/auth/reauthenticate", 200],
    [2, "POST", "/api/gardens/{garden_id}/complete-onboarding", 200],
    [1, "POST", "/api/gardens/{garden_id}/map-objects", 403],
    [10, "POST", "/api/gardens/{garden_id}/map-objects", 201],
    [2, "POST", "/api/gardens/{garden_id}/map-objects/{public_id}/units", 201],
    [2, "POST", "/api/gardens", 201],
    [1, "POST", "/api/harvest", 201],
    [1, "POST", "/api/plants", 201],
    [1, "POST", "/api/plots", 201],
    [2, "POST", "/api/plots/OPT-JOURNEY-A-PLOT/plants/{created_plant_id}", 201],
    [2, "POST", "/api/plots/import", 200],
    [1, "POST", "/api/plots/import", 409],
    [3, "POST", "/api/plots/import", 422],
    [1, "POST", "/api/saved-views", 201],
    [2, "POST", "/api/snapshots", 201],
    [1, "POST", "/api/snapshots/{public_id}/restore", 200],
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
  return sanitizeDiagnostic(message);
}

function safeIdentifier(value) {
  const text = String(value || "");
  return /^[A-Za-z0-9_.-]{1,100}$/.test(text) ? text : sanitizeDiagnostic(text);
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
  if (depth >= 3 || value === null || typeof value !== "object") return undefined;
  if (Array.isArray(value)) {
    return value.slice(0, 100).flatMap((item) => {
      const sanitized = sanitizeCheckValue(item, depth + 1);
      return sanitized === undefined ? [] : [sanitized];
    });
  }
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => /^[a-z0-9_]{1,80}$/.test(key))
    .flatMap(([key, item]) => {
      const sanitized = sanitizeCheckValue(item, depth + 1);
      return sanitized === undefined ? [] : [[key, sanitized]];
    }));
}

function backendErrorEvidence(logDirectory = process.env.GARDENOPS_LOGS_DIR || "") {
  assert(logDirectory, "Complete journey backend log directory is required");
  const backendLogPath = path.join(logDirectory, "backend.log");
  const structuredLogPath = path.join(logDirectory, "errors.jsonl");
  assert(fs.existsSync(backendLogPath), "Complete journey backend log is missing");
  assert(fs.existsSync(structuredLogPath), "Complete journey structured error log is missing");
  const backendErrorLines = fs.readFileSync(backendLogPath, "utf8")
    .split(/\r?\n/)
    .filter((line) => /\bERROR\b/.test(line)).length;
  const structuredErrorEntries = fs.readFileSync(structuredLogPath, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .filter((line) => {
      try {
        return JSON.parse(line).level === "ERROR";
      } catch {
        return true;
      }
    }).length;
  return {
    backend_error_lines: backendErrorLines,
    structured_error_entries: structuredErrorEntries,
    unexpected_error_count: backendErrorLines + structuredErrorEntries,
  };
}

function assertNoUnexpectedBackendErrors(logDirectory, evidence = backendErrorEvidence(logDirectory)) {
  assert(
    evidence.unexpected_error_count === 0,
    "Unexpected backend ERROR log entries; inspect private runner logs",
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
  for (const [name, username] of Object.entries(expectedByGarden)) {
    const target = observedByName.get(name);
    assert(target?.onboarding_complete === true, `Onboarding did not complete ${name}`);
    assert(target.owner_username === username, `Onboarding owner mismatch for ${name}`);
    assert(
      JSON.stringify(target.memberships) === JSON.stringify([{ role: "admin", username }]),
      `Onboarding membership mismatch for ${name}`,
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

function assertPhaseOneProfileEvidence(profiles) {
  assert(Array.isArray(profiles), "Phase 1 browser profile evidence is missing");
  const expectedProfiles = [
    { profile: "desktop", role: "onboarding", checks: ["onboarding_validation_recovery_complete"] },
    { profile: "mobile", role: "onboarding", checks: ["onboarding_validation_recovery_complete"] },
    { profile: "desktop", role: "admin", checks: ["desktop_admin_mutation_workflows"] },
    { profile: "mobile", role: "admin", checks: ["mobile_supported_writes_and_focus_return"] },
    { profile: "desktop", role: "editor", checks: ["editor_profile_write_affordances_and_admin_denial"] },
    { profile: "desktop", role: "viewer", checks: ["viewer_role_affordances_and_denials"] },
    { profile: "mobile", role: "viewer", checks: ["viewer_role_affordances_and_denials"] },
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
    assert(
      profile.browser_profile?.is_mobile === (expected.profile === "mobile"),
      `Phase 1 browser device evidence was unexpected: ${key}`,
    );
    for (const check of expected.checks) {
      assert(profile.checks?.[check] === true, `Phase 1 browser check is missing: ${key}:${check}`);
    }
    if (expected.role !== "onboarding") {
      assert(profile.checks?.map_first_without_plants === true, `Phase 1 map-first check is missing: ${key}`);
    }
  }
  const desktopAdmin = byKey.get("admin:desktop");
  const rejectedImports = desktopAdmin.checks?.import_rejection_render_churn;
  assert(
    rejectedImports && typeof rejectedImports === "object"
      && ["cross_garden", "oversized", "structurally_incomplete", "unsupported_schema"]
        .every((key) => rejectedImports[key] && typeof rejectedImports[key] === "object"),
    "Phase 1 rejected-import render evidence is missing",
  );
  for (const key of ["admin:desktop", "admin:mobile"]) {
    const profile = byKey.get(key);
    const delayed = profile.checks?.delayed_surfaces;
    const required = ["indoor", "layout", "map-objects", "notifications", "plants", "plot-alerts", "weather"];
    if (profile.profile === "desktop") required.push("admin-settings");
    assert(
      Array.isArray(delayed) && required.every((surface) => delayed.includes(surface)),
      `Phase 1 delayed A/B/A evidence is incomplete: ${key}`,
    );
  }
  return {
    expected_profile_count: expectedProfiles.length,
    profile_matrix_enforced: true,
  };
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

function sanitizeManifestEvidence(manifest) {
  const dirty = Boolean(manifest.git?.dirty);
  const output = {
    backend_log: {
      backend_error_lines: safeNonnegativeInteger(manifest.backend_log?.backend_error_lines),
      structured_error_entries: safeNonnegativeInteger(manifest.backend_log?.structured_error_entries),
      unexpected_error_count: safeNonnegativeInteger(manifest.backend_log?.unexpected_error_count),
    },
    browser: safeIdentifier(manifest.browser),
    database: manifest.database && typeof manifest.database === "object"
      ? structuredClone(manifest.database)
      : null,
    ended_at: safeIdentifier(manifest.ended_at),
    failure: manifest.failure ? sanitizeDiagnostic(manifest.failure) : null,
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
    started_at: safeIdentifier(manifest.started_at),
    status: safeIdentifier(manifest.status),
    suite: safeIdentifier(manifest.suite),
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
      trace: safeIdentifier(rawProfile.trace),
    };
    for (const [key, values] of Object.entries(profile.diagnostics || {})) {
      if (Array.isArray(values)) profile.diagnostics[key] = values.map(sanitizeDiagnostic);
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
        gardenId: request.gardenId === null || /^\d+$/.test(String(request.gardenId))
          ? request.gardenId
          : sanitizeDiagnostic(request.gardenId),
        method: new Set(["DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"])
          .has(String(request.method)) ? String(request.method) : "UNKNOWN",
        path: /^\/api\/(?:auth\/(?:login|me|status)|attention\/today|version|plots(?:\/[^/?]+)?|plants(?:\/[^/?]+)?|dashboard\/badge-counts|gardens(?:\/\d+\/map-objects(?:\/[^/?]+(?:\/units(?:\/[^/?]+)?)?)?)?|layout-state|notifications|saved-views(?:\/presets)?|snapshots)$/.test(String(request.path))
          ? String(request.path)
          : sanitizeDiagnostic(request.path),
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

async function main() {
  assertRunnerEnvironment();
  assertPrivateFiles();
  assertNoResponseMocks();
  const fixture = readJson(FIXTURE_PATH);
  assertFixtureAttentionClock(fixture);
  let manifest = {
    backend_log: null,
    browser: "chromium",
    database: null,
    ended_at: null,
    failure: null,
    git: sourceProvenance(fixture.git),
    journey_ids: [
      ...(PHASE === 0 ? ["A1"] : []),
      ...(THROUGH_PHASE >= 1 ? ["A3", "M1", "M2", "M3", "M4", "CROSS-01"] : []),
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
  let phaseOneAuditEvidence = null;
  let phaseOneProfileEvidence = null;
  let phaseOneProfiles = [];
  let thrownError = null;
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
    if (PHASE === 0) {
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
    }
    if (THROUGH_PHASE >= 1) {
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
    }
    const finalDatabase = databaseSnapshot();
    manifest.database = {
      observed_audit_state: finalDatabase.audit_state,
      observed_auth_state: finalDatabase.auth_state,
      observed_domain_counts: finalDatabase.domain_counts,
      observed_domain_tables: finalDatabase.domain_tables,
      observed_phase_one_state: finalDatabase.phase_one_state,
    };
    const domainTableNames = new Set([
      ...Object.keys(fixture.database_snapshot.domain_tables),
      ...Object.keys(finalDatabase.domain_tables),
    ]);
    const changedDomainTables = [...domainTableNames].filter(
      (table) => JSON.stringify(finalDatabase.domain_tables[table])
        !== JSON.stringify(fixture.database_snapshot.domain_tables[table]),
    );
    const phaseOneRan = THROUGH_PHASE >= 1;
    if (phaseOneRan) phaseOneProfileEvidence = assertPhaseOneProfileEvidence(phaseOneProfiles);
    const allowedChangedTables = phaseOneRan ? new Set([
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
    ]) : new Set();
    assert(
      changedDomainTables.every((table) => allowedChangedTables.has(table)),
      `Browser journey changed forbidden domain tables: ${changedDomainTables.join(", ")}`,
    );
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
    const expectedSessionCounts = {
      [fixture.roles.admin]: manifest.profiles.filter((profile) => profile.role === "admin").length,
      [fixture.roles.editor]: manifest.profiles.filter((profile) => profile.role === "editor").length,
      [fixture.roles.onboarding]: 1,
      [fixture.roles.onboarding_mobile]: 1,
      [fixture.roles.viewer]: manifest.profiles.filter((profile) => profile.role === "viewer").length,
    };
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
      const initialPhaseOne = fixture.database_snapshot.phase_one_state;
      const finalPhaseOne = finalDatabase.phase_one_state;
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
          finalDatabase.domain_tables[table].count
            === fixture.database_snapshot.domain_tables[table].count + delta,
          `Phase 1 ${table} count delta was not ${delta}`,
        );
      }
      assert(finalPhaseOne.alpha_address === initialPhaseOne.alpha_address, "Garden settings were not restored");
      assert(
        JSON.stringify(finalPhaseOne.alpha_map_object) === JSON.stringify(initialPhaseOne.alpha_map_object),
        "Alpha map object geometry/style was not restored",
      );
      assert(
        JSON.stringify(finalPhaseOne.alpha_map_unit) === JSON.stringify(initialPhaseOne.alpha_map_unit),
        "Alpha nested map unit changed",
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
      assert(
        finalPhaseOne.mobile_snapshots.length === 1
          && finalPhaseOne.mobile_snapshots[0].garden_id === fixture.phase_one.mobile_snapshot.garden_id,
        "Mobile snapshot was not owned by the active Alpha garden",
      );
      for (const [label, records] of Object.entries({
        "mobile harvest": finalPhaseOne.mobile_harvests,
        "mobile journal": finalPhaseOne.mobile_journals,
      })) {
        assert(
          Array.isArray(records)
            && records.length === 1
            && records[0].garden_id === fixture.gardens.alpha.id
            && records[0].actor_username === fixture.roles.admin,
          `Phase 1 ${label} was not owned by the active Alpha garden`,
        );
      }
      assert(
        initialPhaseOne.alpha_map_unit_count >= 1
          && finalPhaseOne.alpha_map_unit_count === initialPhaseOne.alpha_map_unit_count,
        "Parent map-object deletion did not cascade its nested unit",
      );
      const expectedLifecycleAudit = {
        assignment_create_count: 2,
        assignment_delete_count: 1,
        nested_unit_create_count: 2,
        nested_unit_direct_delete_count: 0,
        nested_unit_update_count: 2,
        plant_create_count: 1,
        plant_delete_count: 1,
        plant_update_count: 2,
        saved_view_create_count: 1,
        saved_view_delete_count: 1,
      };
      assert(
        JSON.stringify(finalPhaseOne.lifecycle_audit) === JSON.stringify(expectedLifecycleAudit),
        `Phase 1 plant, saved-view, or nested-unit lifecycle was unexpected: ${JSON.stringify(finalPhaseOne.lifecycle_audit)}`,
      );
      assert(
        finalPhaseOne.lifecycle_audit.nested_unit_direct_delete_count === 0,
        "Nested unit must be deleted by its parent cascade rather than a direct delete",
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
      assertExactPhaseOneOnboardingOwnership(finalPhaseOne.onboarding_target_gardens, {
        [fixture.phase_one.onboarding.desktop_garden_name]: fixture.phase_one.onboarding.desktop_username,
        [fixture.phase_one.onboarding.mobile_garden_name]: fixture.phase_one.onboarding.mobile_username,
      });
      assert(
        finalPhaseOne.onboarding_gardens.length === initialPhaseOne.onboarding_gardens.length + 4,
        "Onboarding did not create the expected visible gardens and legacy default contexts",
      );
      phaseOneAuditEvidence = assertPhaseOneAuditContract(
        finalDatabase.audit_state,
        manifest.profiles.length,
      );
      assert(
        finalDatabase.phase_one_state.mobile_snapshot_count
          === fixture.database_snapshot.phase_one_state.mobile_snapshot_count + 1,
        "Phase 1 mobile action did not create exactly one snapshot",
      );
      assert(
        finalDatabase.audit_state.expected_phase_one_snapshot_count === 2,
        "Phase 1 desktop and mobile snapshots did not create exactly two expected audit events",
      );
      assert(
        finalDatabase.audit_state.expected_phase_one_viewer_denial_count <= 1,
        "Phase 1 viewer denial created more than one audit event",
      );
    }
    manifest.database = {
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
        phase_one_viewer_denial_count: finalDatabase.audit_state.expected_phase_one_viewer_denial_count,
        unexpected_count: phaseOneAuditEvidence?.unexpected_count ?? null,
      },
      cluster_fingerprint: crypto
        .createHash("sha256")
        .update(
          `${process.env.GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER}:`
          + process.env.GARDENOPS_DISPOSABLE_POSTGRES_MARKER,
        )
        .digest("hex"),
      allowed_changed_tables: [...allowedChangedTables],
      domain_counts_unchanged: !phaseOneRan,
      domain_digests_unchanged: !phaseOneRan,
      phase_one_enforcement: phaseOneRan ? {
        assignments_and_lifecycle: true,
        browser_profile_matrix: phaseOneProfileEvidence?.profile_matrix_enforced === true,
        cross_garden_links_absent: true,
        mobile_snapshot_garden_owned: true,
        nested_unit_parent_cascade: true,
        onboarding_ownership_and_membership: true,
        seeded_plant_and_saved_view_ownership: true,
        targeted_audit_contract: Boolean(phaseOneAuditEvidence),
      } : null,
      phase_one_mobile_snapshot_count: finalDatabase.phase_one_state.mobile_snapshot_count,
      final: finalDatabase.domain_counts,
      initial: fixture.database_snapshot.domain_counts,
    };
    manifest.filesystem = filesystemState();
    assert(manifest.filesystem.downloads.empty, "Browser journey wrote download files");
    assert(manifest.filesystem.media.empty, "Browser journey wrote media files");
    assert(manifest.filesystem.terrain.empty, "Browser journey wrote terrain files");
    manifest.backend_log = backendErrorEvidence();
    assertNoUnexpectedBackendErrors(undefined, manifest.backend_log);
    await browser.close();
    browser = null;
    manifest.status = "passed";
  } catch (error) {
    thrownError = error;
    manifest.status = "failed";
    manifest.failure = safeFailure(error);
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
  assertNoCrossGardenLinks,
  assertNoLifecycleResidue,
  assertNoUnexpectedBackendErrors,
  assertPhaseOneAuditContract,
  assertPhaseOneProfileEvidence,
  assertNoResponseMocks,
  assertPageStructure,
  assertSourceRevisionStable,
  backendErrorEvidence,
  gitState,
  phaseOneAuditExpectedEvents,
  safeFailure,
  sanitizeManifestEvidence,
  sourceProvenance,
  writeManifestAtomic,
};
