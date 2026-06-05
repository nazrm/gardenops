#!/usr/bin/env node

const fs = require("node:fs");
const https = require("node:https");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const COOLDOWN_DAYS = 14;
const REGISTRY_URL = "https://registry.npmjs.org";

// These packages were already locked inside the cooldown window when the
// dependency policy was introduced. The dates are the point where the locked
// version has aged out of the 14-day window; remove entries after they expire.
const TEMPORARY_EXCEPTIONS = {
  "@oxc-project/types@0.130.0": "2026-05-25T15:05:37.000Z",
  "@rolldown/binding-android-arm64@1.0.1": "2026-05-27T12:44:25.000Z",
  "@rolldown/binding-darwin-arm64@1.0.1": "2026-05-27T12:44:00.000Z",
  "@rolldown/binding-darwin-x64@1.0.1": "2026-05-27T12:43:16.000Z",
  "@rolldown/binding-freebsd-x64@1.0.1": "2026-05-27T12:43:41.000Z",
  "@rolldown/binding-linux-arm-gnueabihf@1.0.1": "2026-05-27T12:43:47.000Z",
  "@rolldown/binding-linux-arm64-gnu@1.0.1": "2026-05-27T12:43:53.000Z",
  "@rolldown/binding-linux-arm64-musl@1.0.1": "2026-05-27T12:44:07.000Z",
  "@rolldown/binding-linux-ppc64-gnu@1.0.1": "2026-05-27T12:44:43.000Z",
  "@rolldown/binding-linux-s390x-gnu@1.0.1": "2026-05-27T12:44:37.000Z",
  "@rolldown/binding-linux-x64-gnu@1.0.1": "2026-05-27T12:43:29.000Z",
  "@rolldown/binding-linux-x64-musl@1.0.1": "2026-05-27T12:43:35.000Z",
  "@rolldown/binding-openharmony-arm64@1.0.1": "2026-05-27T12:44:13.000Z",
  "@rolldown/binding-wasm32-wasi@1.0.1": "2026-05-27T12:44:30.000Z",
  "@rolldown/binding-win32-arm64-msvc@1.0.1": "2026-05-27T12:44:19.000Z",
  "@rolldown/binding-win32-x64-msvc@1.0.1": "2026-05-27T12:43:22.000Z",
  "@rolldown/pluginutils@1.0.1": "2026-05-27T03:52:30.000Z",
  "playwright-core@1.60.0": "2026-05-25T19:09:41.000Z",
  "rolldown@1.0.1": "2026-05-27T12:44:47.000Z",
  "vite@8.0.13": "2026-05-28T11:11:55.000Z",
};

function packageNameFromLockPath(packagePath) {
  const parts = packagePath.split("/");
  const nodeModulesIndex = parts.lastIndexOf("node_modules");
  if (nodeModulesIndex === -1 || nodeModulesIndex + 1 >= parts.length) {
    return null;
  }

  const firstNamePart = parts[nodeModulesIndex + 1];
  if (firstNamePart.startsWith("@")) {
    const secondNamePart = parts[nodeModulesIndex + 2];
    return secondNamePart ? `${firstNamePart}/${secondNamePart}` : null;
  }
  return firstNamePart;
}

function packageKey(name, version) {
  return `${name}@${version}`;
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(
      url,
      {
        headers: {
          Accept: "application/json",
          "User-Agent": "gardenops-dependency-policy",
        },
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode !== 200) {
            reject(new Error(`${url} returned HTTP ${response.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(new Error(`${url} returned invalid JSON: ${error.message}`));
          }
        });
      },
    );
    request.setTimeout(15000, () => {
      request.destroy(new Error(`${url} timed out`));
    });
    request.on("error", reject);
  });
}

async function mapWithConcurrency(items, concurrency, worker) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function runWorker() {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await worker(items[index]);
    }
  }

  const workers = Array.from({ length: Math.min(concurrency, items.length) }, runWorker);
  await Promise.all(workers);
  return results;
}

function collectLockedPackages() {
  const lockPath = path.join(ROOT, "frontend", "package-lock.json");
  const lockData = JSON.parse(fs.readFileSync(lockPath, "utf8"));

  if (![2, 3].includes(lockData.lockfileVersion)) {
    throw new Error(
      "frontend/package-lock.json must use lockfileVersion 2 or 3 " +
        `to expose per-package release metadata; found ${lockData.lockfileVersion || "<missing>"}`,
    );
  }
  if (
    !lockData.packages ||
    typeof lockData.packages !== "object" ||
    Array.isArray(lockData.packages) ||
    Object.keys(lockData.packages).length === 0
  ) {
    throw new Error("frontend/package-lock.json is missing npm packages metadata");
  }

  const packages = new Map();

  for (const [packagePath, packageInfo] of Object.entries(lockData.packages)) {
    if (packagePath === "") {
      continue;
    }

    const name = packageNameFromLockPath(packagePath);
    const version = packageInfo && packageInfo.version;
    if (!name || typeof version !== "string") {
      continue;
    }
    packages.set(packageKey(name, version), { name, version });
  }

  return Array.from(packages.values()).sort((left, right) =>
    packageKey(left.name, left.version).localeCompare(packageKey(right.name, right.version)),
  );
}

async function lookupPublishTime({ name, version }) {
  const metadataUrl = `${REGISTRY_URL}/${encodeURIComponent(name)}`;
  const metadata = await fetchJson(metadataUrl);
  const publishedAt = metadata.time && metadata.time[version];
  if (typeof publishedAt !== "string") {
    throw new Error(`${packageKey(name, version)} has no publish time in npm registry metadata`);
  }
  return { name, version, publishedAt: new Date(publishedAt) };
}

async function main() {
  const now = new Date();
  const cutoff = new Date(now.getTime() - COOLDOWN_DAYS * 24 * 60 * 60 * 1000);
  const lockedPackages = collectLockedPackages();
  const publishTimes = await mapWithConcurrency(lockedPackages, 8, lookupPublishTime);
  const errors = [];
  const allowed = [];

  for (const { name, version, publishedAt } of publishTimes) {
    const key = packageKey(name, version);
    if (Number.isNaN(publishedAt.getTime())) {
      errors.push(`${key} has an invalid publish time`);
      continue;
    }

    if (publishedAt <= cutoff) {
      continue;
    }

    const exceptionUntilValue = TEMPORARY_EXCEPTIONS[key];
    const exceptionUntil = exceptionUntilValue ? new Date(exceptionUntilValue) : null;
    if (exceptionUntil && now < exceptionUntil) {
      allowed.push(`${key} until ${exceptionUntil.toISOString()}`);
      continue;
    }

    errors.push(
      `${key} was published at ${publishedAt.toISOString()} inside the ${COOLDOWN_DAYS}-day ` +
        "cooldown window",
    );
  }

  if (errors.length > 0) {
    for (const error of errors) {
      console.error(`npm release-age check: ${error}`);
    }
    process.exit(1);
  }

  if (allowed.length > 0) {
    console.log("Temporary npm release-age exceptions:");
    for (const item of allowed) {
      console.log(`- ${item}`);
    }
  }
  console.log("npm locked packages satisfy the release-age policy.");
}

main().catch((error) => {
  console.error(`npm release-age check: ${error.message}`);
  process.exit(1);
});
