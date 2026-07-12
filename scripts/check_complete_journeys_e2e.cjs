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
  assert(PHASE === 0 && THROUGH_PHASE === 0, "Requested phase is not implemented");
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
  const output = structuredClone(manifest);
  output.profiles = (output.profiles || []).map((rawProfile) => {
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
        path: /^\/api\/(?:auth\/(?:login|me|status)|attention\/today|version|plots(?:\/alerts|\/elevations)?|dashboard\/badge-counts|gardens|gardens\/\d+\/map-objects|layout-state|notifications)$/.test(String(request.path))
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
    journey_ids: ["A1", "M1", "CROSS-01"],
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
    if (PHASE === 0 && THROUGH_PHASE === 0) {
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
    const finalDatabase = databaseSnapshot();
    const finalGitSha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: ROOT, encoding: "utf8" }).trim();
    const finalGitDirty = execFileSync("git", ["status", "--porcelain"], { cwd: ROOT, encoding: "utf8" }).trim();
    assert(finalGitSha === fixture.git.sha && !finalGitDirty, "Source revision changed during journey run");
    const domainTableNames = new Set([
      ...Object.keys(fixture.database_snapshot.domain_tables),
      ...Object.keys(finalDatabase.domain_tables),
    ]);
    const changedDomainTables = [...domainTableNames].filter(
      (table) => JSON.stringify(finalDatabase.domain_tables[table])
        !== JSON.stringify(fixture.database_snapshot.domain_tables[table]),
    );
    assert(
      changedDomainTables.length === 0,
      `Foundation browser journey changed forbidden domain tables: ${changedDomainTables.join(", ")}`,
    );
    assert(
      fixture.database_snapshot.auth_state.admin_session_count === 0,
      "Foundation fixture unexpectedly started with an administrator session",
    );
    assert(
      finalDatabase.auth_state.admin_session_count === 2,
      `Expected two browser sessions, got ${finalDatabase.auth_state.admin_session_count}`,
    );
    assert(
      finalDatabase.auth_state.invalid_session_count === 0,
      "Foundation login created an invalid session row",
    );
    assert(
      finalDatabase.auth_state.users_expected_digest
        === fixture.database_snapshot.auth_state.users_expected_digest,
      "Foundation login changed auth user state beyond last_login_at",
    );
    const expectedSessionCounts = {
      complete_journeys_e2e_admin: 2,
      complete_journeys_e2e_editor: 0,
      complete_journeys_e2e_viewer: 0,
    };
    assert(
      JSON.stringify(finalDatabase.auth_state.session_user_counts)
        === JSON.stringify(expectedSessionCounts),
      `Foundation login created unexpected user sessions: ${JSON.stringify(finalDatabase.auth_state.session_user_counts)}`,
    );
    assert(
      fixture.database_snapshot.auth_state.admin_last_login_at === null
        && finalDatabase.auth_state.admin_last_login_at !== null,
      "Foundation login did not persist the expected administrator last-login timestamp",
    );
    assert(
      fixture.database_snapshot.audit_state.total_count === 0,
      "Foundation fixture unexpectedly started with audit events",
    );
    assert(
      finalDatabase.audit_state.expected_login_count === 2
        && finalDatabase.audit_state.unexpected_count === 0,
      `Foundation login created unexpected audit state: ${JSON.stringify(finalDatabase.audit_state)}`,
    );
    manifest.database = {
      auth_expected_writes: {
        admin_last_login_updated: true,
        auth_users_other_fields_unchanged: true,
        session_rows_valid: true,
        session_count_after: finalDatabase.auth_state.admin_session_count,
        session_count_before: fixture.database_snapshot.auth_state.admin_session_count,
      },
      audit_expected_writes: {
        login_success_count: finalDatabase.audit_state.expected_login_count,
        unexpected_count: finalDatabase.audit_state.unexpected_count,
      },
      cluster_fingerprint: crypto
        .createHash("sha256")
        .update(
          `${process.env.GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER}:`
          + process.env.GARDENOPS_DISPOSABLE_POSTGRES_MARKER,
        )
        .digest("hex"),
      domain_counts_unchanged: true,
      domain_digests_unchanged: true,
      final: finalDatabase.domain_counts,
      initial: fixture.database_snapshot.domain_counts,
    };
    manifest.filesystem = filesystemState();
    assert(manifest.filesystem.downloads.empty, "Foundation journey wrote download files");
    assert(manifest.filesystem.media.empty, "Foundation journey wrote media files");
    assert(manifest.filesystem.terrain.empty, "Foundation journey wrote terrain files");
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
