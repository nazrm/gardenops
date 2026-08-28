#!/usr/bin/env node

const fs = require("node:fs");
const https = require("node:https");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const ROUTINE_COOLDOWN_DAYS = 3;
const MAJOR_OR_NEW_DIRECT_COOLDOWN_DAYS = 14;
const REGISTRY_URL = "https://registry.npmjs.org";
const TRUSTED_NPM_BYPASS_SOURCE = "npm audit base/head diff";
const configuredSecurityBypassPath = process.env.GARDENOPS_SECURITY_RELEASE_BYPASS || "";
const SECURITY_BYPASS_PATH =
  configuredSecurityBypassPath ||
  path.join(ROOT, ".gardenops", "security-release-bypass.json");

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

function canonicalDependencyName(name, specifier) {
  if (typeof specifier !== "string" || !specifier.startsWith("npm:")) {
    return name;
  }
  const target = specifier.slice(4);
  const versionSeparator = target.lastIndexOf("@");
  const packageName = versionSeparator > 0 ? target.slice(0, versionSeparator) : target;
  return packageName.includes("/") || !packageName.startsWith("@") ? packageName : name;
}

function dependencyEntries(packageData) {
  return [
    ...Object.entries(packageData.dependencies || {}),
    ...Object.entries(packageData.devDependencies || {}),
    ...Object.entries(packageData.optionalDependencies || {}),
    ...Object.entries(packageData.peerDependencies || {}),
  ];
}

function loadSecurityReleaseBypasses() {
  if (
    configuredSecurityBypassPath &&
    !["1", "true", "yes", "on"].includes(
      (process.env.GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE || "").toLowerCase(),
    )
  ) {
    throw new Error(
      "external bypass file overrides require " +
        "GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE=true",
    );
  }
  if (!fs.existsSync(SECURITY_BYPASS_PATH)) {
    return new Map();
  }

  let data;
  try {
    data = JSON.parse(fs.readFileSync(SECURITY_BYPASS_PATH, "utf8"));
  } catch (error) {
    throw new Error(`${SECURITY_BYPASS_PATH} is not valid JSON: ${error.message}`);
  }

  if (data.schema !== 1) {
    throw new Error(`${SECURITY_BYPASS_PATH} field 'schema' must be 1`);
  }
  const entries = data.npm === undefined ? [] : data.npm;
  if (!Array.isArray(entries)) {
    throw new Error(`${SECURITY_BYPASS_PATH} field 'npm' must be a list`);
  }

  const bypasses = new Map();
  for (const [index, entry] of entries.entries()) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(`${SECURITY_BYPASS_PATH} npm[${index}] must be an object`);
    }

    const packageName = entry.package;
    const fromVersion = entry.from;
    const toVersion = entry.to;
    const advisories = entry.advisories_fixed;
    const source = entry.source;
    if (typeof packageName !== "string" || packageName.length === 0) {
      throw new Error(`${SECURITY_BYPASS_PATH} npm[${index}].package must be a non-empty string`);
    }
    if (typeof fromVersion !== "string" || fromVersion.length === 0) {
      throw new Error(`${SECURITY_BYPASS_PATH} npm[${index}].from must be a non-empty string`);
    }
    if (typeof toVersion !== "string" || toVersion.length === 0) {
      throw new Error(`${SECURITY_BYPASS_PATH} npm[${index}].to must be a non-empty string`);
    }
    if (fromVersion === toVersion) {
      throw new Error(`${SECURITY_BYPASS_PATH} npm[${index}] must change versions`);
    }
    if (source !== TRUSTED_NPM_BYPASS_SOURCE) {
      throw new Error(
        `${SECURITY_BYPASS_PATH} npm[${index}].source must be ` +
          `'${TRUSTED_NPM_BYPASS_SOURCE}'`,
      );
    }
    if (
      !Array.isArray(advisories) ||
      advisories.length === 0 ||
      !advisories.every((advisory) => typeof advisory === "string" && advisory.length > 0)
    ) {
      throw new Error(
        `${SECURITY_BYPASS_PATH} npm[${index}].advisories_fixed must be a non-empty string list`,
      );
    }

    bypasses.set(packageKey(packageName, toVersion), [...new Set(advisories)].sort());
  }

  return bypasses;
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

function collectLockedPackages(root = ROOT) {
  const lockPath = path.join(root, "frontend", "package-lock.json");
  const lockData = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  const packageData = JSON.parse(
    fs.readFileSync(path.join(root, "frontend", "package.json"), "utf8"),
  );
  const directSpecifiers = new Map(dependencyEntries(packageData));

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

  const dependencyPackages = Object.entries(lockData.packages).filter(
    ([packagePath]) => packagePath !== "",
  );
  if (dependencyPackages.length === 0) {
    throw new Error("frontend/package-lock.json does not contain npm dependency package entries");
  }

  const packages = new Map();

  for (const [packagePath, packageInfo] of dependencyPackages) {
    const pathName = packageNameFromLockPath(packagePath);
    const isDirect = pathName && packagePath === `node_modules/${pathName}`;
    const declaredSpecifier = isDirect ? directSpecifiers.get(pathName) : undefined;
    const expectedName = canonicalDependencyName(pathName, declaredSpecifier);
    const lockName = packageInfo && packageInfo.name;
    if (
      typeof lockName === "string" &&
      lockName.length > 0 &&
      lockName !== expectedName
    ) {
      throw new Error(
        `${packagePath} lockfile name ${lockName} does not match manifest identity ${expectedName}`,
      );
    }
    const name = expectedName;
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

function directDependencyNames(root) {
  const packageData = JSON.parse(
    fs.readFileSync(path.join(root, "frontend", "package.json"), "utf8"),
  );
  return new Set(
    dependencyEntries(packageData).map(([name, specifier]) =>
      canonicalDependencyName(name, specifier),
    ),
  );
}

function packageVersions(packages) {
  const versions = new Map();
  for (const { name, version } of packages) {
    if (!versions.has(name)) {
      versions.set(name, new Set());
    }
    versions.get(name).add(version);
  }
  return versions;
}

function versionMajor(version) {
  const match = /^(\d+)/.exec(version);
  return match ? Number.parseInt(match[1], 10) : null;
}

function cooldownFor({ name, version }, baseVersions, baseDirect, headDirect) {
  if (headDirect.has(name) && !baseDirect.has(name)) {
    return { days: MAJOR_OR_NEW_DIRECT_COOLDOWN_DAYS, tier: "new direct dependency" };
  }
  if (headDirect.has(name)) {
    const headMajor = versionMajor(version);
    const priorMajors = new Set(
      Array.from(baseVersions.get(name) || [], (item) => versionMajor(item)),
    );
    if (headMajor !== null && priorMajors.size > 0 && !priorMajors.has(headMajor)) {
      return { days: MAJOR_OR_NEW_DIRECT_COOLDOWN_DAYS, tier: "major direct update" };
    }
  }
  return { days: ROUTINE_COOLDOWN_DAYS, tier: "routine update" };
}

function selectPackagesToCheck(headPackages, basePackages, baseDirect, headDirect) {
  const baseKeys = new Set(basePackages.map(({ name, version }) => packageKey(name, version)));
  const newlyDirect = new Set(Array.from(headDirect).filter((name) => !baseDirect.has(name)));
  return headPackages.filter(
    ({ name, version }) => !baseKeys.has(packageKey(name, version)) || newlyDirect.has(name),
  );
}

function parseArgs(argv) {
  const args = { baseRoot: null, headRoot: ROOT };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--base-root") {
      args.baseRoot = path.resolve(argv[index + 1]);
      index += 1;
    } else if (argv[index] === "--head-root") {
      args.headRoot = path.resolve(argv[index + 1]);
      index += 1;
    } else {
      throw new Error(`unknown argument: ${argv[index]}`);
    }
  }
  return args;
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
  const { baseRoot, headRoot } = parseArgs(process.argv.slice(2));
  const now = new Date();
  const headPackages = collectLockedPackages(headRoot);
  const basePackages = baseRoot ? collectLockedPackages(baseRoot) : [];
  const baseDirect = baseRoot ? directDependencyNames(baseRoot) : new Set();
  const headDirect = directDependencyNames(headRoot);
  const packagesToCheck = baseRoot
    ? selectPackagesToCheck(headPackages, basePackages, baseDirect, headDirect)
    : headPackages;
  const securityBypasses = loadSecurityReleaseBypasses();
  const publishTimes = await mapWithConcurrency(packagesToCheck, 8, lookupPublishTime);
  const baseVersions = packageVersions(basePackages);
  const errors = [];
  const allowed = [];

  for (const { name, version, publishedAt } of publishTimes) {
    const key = packageKey(name, version);
    if (Number.isNaN(publishedAt.getTime())) {
      errors.push(`${key} has an invalid publish time`);
      continue;
    }

    const bypassAdvisories = securityBypasses.get(key);
    if (bypassAdvisories) {
      allowed.push(`${key} fixing ${bypassAdvisories.join(", ")}`);
      continue;
    }

    const { days, tier } = cooldownFor(
      { name, version },
      baseVersions,
      baseDirect,
      headDirect,
    );
    const cutoff = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
    if (publishedAt <= cutoff) {
      continue;
    }

    errors.push(
      `${key} was published at ${publishedAt.toISOString()} inside the ${days}-day ` +
        `cooldown window (${tier})`,
    );
  }

  if (errors.length > 0) {
    for (const error of errors) {
      console.error(`npm release-age check: ${error}`);
    }
    process.exit(1);
  }

  if (allowed.length > 0) {
    console.log("Allowed npm release-age exceptions:");
    for (const item of allowed) {
      console.log(`- ${item}`);
    }
  }
  console.log("npm locked packages satisfy the release-age policy.");
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`npm release-age check: ${error.message}`);
    process.exit(1);
  });
}

module.exports = {
  canonicalDependencyName,
  collectLockedPackages,
  cooldownFor,
  directDependencyNames,
  loadSecurityReleaseBypasses,
  selectPackagesToCheck,
};
