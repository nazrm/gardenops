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

function phaseOneExpectedAuditEvents() {
  return [
    [1, "DELETE", "/api/gardens/{garden_id}/map-objects/{public_id}/units/{public_id}", 200],
    [9, "DELETE", "/api/gardens/{garden_id}/map-objects/{public_id}", 200],
    [1, "DELETE", "/api/plants/{created_plant_id}", 200],
    [1, "DELETE", "/api/plots/OPT-JOURNEY-A-PLOT/plants/{created_plant_id}", 204],
    [1, "DELETE", "/api/saved-views/{saved_view_id}", 200],
    [1, "DELETE", "/api/snapshots/{public_id}", 200],
    [3, "PATCH", "/api/gardens/{garden_id}/map-objects/{public_id}", 200],
    [1, "PATCH", "/api/gardens/{garden_id}/map-objects/obj_optimization_journeys_a", 200],
    [2, "PATCH", "/api/gardens/{garden_id}/settings", 200],
    [2, "PATCH", "/api/plants/{created_plant_id}", 200],
    [2, "PATCH", "/api/plots/COMPLETE-PHASE-ONE-INDOOR/plants/COMPLETE-PHASE-ONE-BASIL", 200],
    [5, "POST", "/api/auth/login", 200],
    [4, "POST", "/api/auth/reauthenticate", 200],
    [2, "POST", "/api/gardens/{garden_id}/complete-onboarding", 200],
    [1, "POST", "/api/gardens/{garden_id}/map-objects", 403],
    [9, "POST", "/api/gardens/{garden_id}/map-objects", 201],
    [1, "POST", "/api/gardens/{garden_id}/map-objects/{public_id}/units", 201],
    [2, "POST", "/api/gardens", 201],
    [1, "POST", "/api/harvest", 201],
    [3, "POST", "/api/media/summaries", 200],
    [1, "POST", "/api/plants", 201],
    [2, "POST", "/api/plots/OPT-JOURNEY-A-PLOT/plants/{created_plant_id}", 201],
    [1, "POST", "/api/plots/import", 200],
    [1, "POST", "/api/plots/import", 422],
    [1, "POST", "/api/saved-views", 201],
    [2, "POST", "/api/snapshots", 201],
    [1, "POST", "/api/snapshots/{public_id}/restore", 200],
  ].map(([count, method, path, status_code]) => ({ count, method, path, status_code }))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
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

function sanitizeManifestEvidence(manifest) {
  const output = {
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
      dirty: Boolean(manifest.git?.dirty),
      sha: safeIdentifier(manifest.git?.sha),
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
        .filter(([key, value]) => /^[a-z0-9_]{1,80}$/.test(key) && typeof value === "boolean")),
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
  let manifest = {
    browser: "chromium",
    database: null,
    ended_at: null,
    failure: null,
    git: fixture.git,
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
    }
    const finalDatabase = databaseSnapshot();
    manifest.database = {
      observed_audit_state: finalDatabase.audit_state,
      observed_auth_state: finalDatabase.auth_state,
      observed_domain_counts: finalDatabase.domain_counts,
      observed_domain_tables: finalDatabase.domain_tables,
      observed_phase_one_state: finalDatabase.phase_one_state,
    };
    const finalGitSha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }).trim();
    const finalGitDirty = execFileSync("git", ["status", "--porcelain"], { cwd: ROOT, encoding: "utf8" }).trim();
    assert(
      finalGitSha === fixture.git.sha && Boolean(finalGitDirty) === Boolean(fixture.git.dirty),
      "Source revision changed during journey run",
    );
    const domainTableNames = new Set([
      ...Object.keys(fixture.database_snapshot.domain_tables),
      ...Object.keys(finalDatabase.domain_tables),
    ]);
    const changedDomainTables = [...domainTableNames].filter(
      (table) => JSON.stringify(finalDatabase.domain_tables[table])
        !== JSON.stringify(fixture.database_snapshot.domain_tables[table]),
    );
    const phaseOneRan = THROUGH_PHASE >= 1;
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
      assert(finalPhaseOne.temp_map_object_count === 0, "Temporary map objects remain");
      assert(finalPhaseOne.temp_plant_count === 0, "Temporary plant remains");
      assert(finalPhaseOne.temp_saved_view_count === 0, "Temporary saved view remains");
      assert(finalPhaseOne.harvest_count === initialPhaseOne.harvest_count + 1, "Mobile harvest was not persisted");
      assert(finalPhaseOne.journal_count === initialPhaseOne.journal_count + 1, "Harvest journal was not persisted");
      assert(
        finalPhaseOne.mobile_snapshot_count === initialPhaseOne.mobile_snapshot_count + 1,
        "Mobile snapshot was not persisted",
      );
      const onboardingGardens = finalPhaseOne.onboarding_gardens.filter(
        (garden) => ["Phase 1 Onboarding Garden", "Phase 1 Mobile Onboarding Garden"].includes(garden.name)
          && garden.onboarding_complete,
      );
      assert(onboardingGardens.length === 2, "Desktop/mobile onboarding gardens were not persisted exactly once");
      assert(
        finalPhaseOne.onboarding_gardens.length === initialPhaseOne.onboarding_gardens.length + 4,
        "Onboarding did not create the expected visible gardens and legacy default contexts",
      );
      const observedAuditEvents = [...finalDatabase.audit_state.events]
        .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
      assert(
        JSON.stringify(observedAuditEvents) === JSON.stringify(phaseOneExpectedAuditEvents()),
        `Phase 1 audit histogram was unexpected: ${JSON.stringify(observedAuditEvents)}`,
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
        histogram_exact: true,
        login_success_count: finalDatabase.audit_state.expected_login_count,
        phase_one_snapshot_count: finalDatabase.audit_state.expected_phase_one_snapshot_count,
        phase_one_viewer_denial_count: finalDatabase.audit_state.expected_phase_one_viewer_denial_count,
        unexpected_count: 0,
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
      phase_one_mobile_snapshot_count: finalDatabase.phase_one_state.mobile_snapshot_count,
      final: finalDatabase.domain_counts,
      initial: fixture.database_snapshot.domain_counts,
    };
    manifest.filesystem = filesystemState();
    assert(manifest.filesystem.downloads.empty, "Browser journey wrote download files");
    assert(manifest.filesystem.media.empty, "Browser journey wrote media files");
    assert(manifest.filesystem.terrain.empty, "Browser journey wrote terrain files");
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
  assertNoResponseMocks,
  assertPageStructure,
  safeFailure,
  sanitizeManifestEvidence,
  writeManifestAtomic,
};
