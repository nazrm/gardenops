#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");
const { performance } = require("node:perf_hooks");

const DEFAULT_TIMEOUT_MS = 10_000;

function usage() {
  console.log(`
Usage:
  node scripts/check_backend_performance.cjs --base-url <url> [options]

Options:
  --base-url <url>                  Base URL for relative endpoints.
  --endpoint <name=path-or-url>     Endpoint to measure. Repeatable.
                                    Defaults to health=/api/health and auth_status=/api/auth/status.
  --runs <count>                    Measured runs. Default: 3.
  --timeout-ms <ms>                 Per-request timeout. Default: ${DEFAULT_TIMEOUT_MS}.
  --endpoint-budget-ms <name=ms>    Fail when an endpoint p75 exceeds this budget. Repeatable.
  --all-budget-ms <ms>              Fail when any endpoint p75 exceeds this budget.
  --output <path>                   Write JSON results.
  --compare <path>                  Compare against a previous JSON result.
  --max-regression-pct <number>     Fail compare mode on endpoint median regression. Default: 5.
  --max-regression-ms <number>      Extra timing jitter allowed in compare mode. Default: 15.
  --json                            Print only JSON.
  --help                            Show this help.

Examples:
  node scripts/check_backend_performance.cjs --base-url http://127.0.0.1:8000 --runs 5
  node scripts/check_backend_performance.cjs --base-url https://example.com --endpoint health=/api/health --output /tmp/backend-perf.json
`);
}

function fail(message) {
  console.error(`Backend performance check failed: ${message}`);
  process.exit(1);
}

function parsePositiveInt(value, name) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function parseNonNegativeNumber(value, name) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative number`);
  }
  return parsed;
}

function parseNameValue(value, flagName) {
  const splitAt = value.indexOf("=");
  if (splitAt <= 0 || splitAt === value.length - 1) {
    throw new Error(`${flagName} must use name=value`);
  }
  const name = value.slice(0, splitAt).trim();
  const rawValue = value.slice(splitAt + 1).trim();
  if (!/^[A-Za-z0-9_-]+$/.test(name)) {
    throw new Error(`${flagName} name must contain only letters, numbers, _ or -`);
  }
  return [name, rawValue];
}

function parseArgs(argv) {
  const options = {
    allBudgetMs: null,
    baseUrl: "",
    comparePath: "",
    endpointBudgets: new Map(),
    endpoints: [],
    json: false,
    maxRegressionMs: 15,
    maxRegressionPct: 5,
    outputPath: "",
    runs: 3,
    timeoutMs: DEFAULT_TIMEOUT_MS,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };

    if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg === "--base-url") {
      options.baseUrl = next();
    } else if (arg === "--endpoint") {
      const [name, endpoint] = parseNameValue(next(), "--endpoint");
      options.endpoints.push({ name, endpoint });
    } else if (arg === "--runs") {
      options.runs = parsePositiveInt(next(), "--runs");
    } else if (arg === "--timeout-ms") {
      options.timeoutMs = parsePositiveInt(next(), "--timeout-ms");
    } else if (arg === "--endpoint-budget-ms") {
      const [name, budget] = parseNameValue(next(), "--endpoint-budget-ms");
      options.endpointBudgets.set(name, parseNonNegativeNumber(budget, "--endpoint-budget-ms"));
    } else if (arg === "--all-budget-ms") {
      options.allBudgetMs = parseNonNegativeNumber(next(), "--all-budget-ms");
    } else if (arg === "--output") {
      options.outputPath = next();
    } else if (arg === "--compare") {
      options.comparePath = next();
    } else if (arg === "--max-regression-pct") {
      options.maxRegressionPct = parseNonNegativeNumber(next(), "--max-regression-pct");
    } else if (arg === "--max-regression-ms") {
      options.maxRegressionMs = parseNonNegativeNumber(next(), "--max-regression-ms");
    } else if (arg === "--json") {
      options.json = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }

  if (options.help) return options;
  if (!options.baseUrl) {
    throw new Error("--base-url is required");
  }
  try {
    options.baseUrl = new URL(options.baseUrl).toString().replace(/\/$/, "");
  } catch {
    throw new Error("--base-url must be a valid URL");
  }
  if (options.endpoints.length === 0) {
    options.endpoints = [
      { name: "health", endpoint: "/api/health" },
      { name: "auth_status", endpoint: "/api/auth/status" },
    ];
  }
  const names = new Set();
  for (const { name } of options.endpoints) {
    if (names.has(name)) throw new Error(`Duplicate endpoint name: ${name}`);
    names.add(name);
  }
  for (const name of options.endpointBudgets.keys()) {
    if (!names.has(name)) {
      throw new Error(`Budget references unknown endpoint: ${name}`);
    }
  }
  return options;
}

function roundMs(value) {
  return Number.isFinite(value) ? Math.round(value * 10) / 10 : null;
}

function percentile(values, pct) {
  const sorted = values
    .filter((value) => Number.isFinite(value))
    .slice()
    .sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const index = (sorted.length - 1) * pct;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return roundMs(sorted[lower]);
  const weight = index - lower;
  return roundMs(sorted[lower] * (1 - weight) + sorted[upper] * weight);
}

function resolveEndpointUrl(baseUrl, endpoint) {
  if (/^https?:\/\//i.test(endpoint)) {
    return endpoint;
  }
  return new URL(endpoint.startsWith("/") ? endpoint : `/${endpoint}`, `${baseUrl}/`).toString();
}

function requestEndpoint(url, timeoutMs) {
  return new Promise((resolve) => {
    const startedAt = performance.now();
    const parsed = new URL(url);
    const client = parsed.protocol === "https:" ? https : http;
    const req = client.request(
      parsed,
      {
        headers: {
          accept: "application/json,text/plain,*/*",
          "user-agent": "gardenops-backend-performance-check/1.0",
        },
        method: "GET",
        timeout: timeoutMs,
      },
      (res) => {
        let bytes = 0;
        res.on("data", (chunk) => {
          bytes += chunk.length;
        });
        res.on("end", () => {
          resolve({
            bytes,
            durationMs: roundMs(performance.now() - startedAt),
            ok: res.statusCode >= 200 && res.statusCode < 400,
            status: res.statusCode,
          });
        });
      },
    );
    req.on("timeout", () => {
      req.destroy(new Error(`timeout after ${timeoutMs}ms`));
    });
    req.on("error", (err) => {
      resolve({
        bytes: 0,
        durationMs: roundMs(performance.now() - startedAt),
        error: err.message,
        ok: false,
        status: 0,
      });
    });
    req.end();
  });
}

function summarizeEndpoint(samples) {
  const durations = samples.map((sample) => sample.durationMs);
  const bytes = samples.map((sample) => sample.bytes);
  const statusCodes = samples.reduce((acc, sample) => {
    const key = String(sample.status);
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const okCount = samples.filter((sample) => sample.ok).length;
  const errors = samples
    .map((sample) => sample.error)
    .filter(Boolean);
  return {
    errors,
    maxMs: percentile(durations, 1),
    medianBytes: percentile(bytes, 0.5),
    medianMs: percentile(durations, 0.5),
    minMs: percentile(durations, 0),
    okRate: samples.length === 0 ? 0 : okCount / samples.length,
    p75Ms: percentile(durations, 0.75),
    statusCodes,
  };
}

function summarizeRuns(endpoints, runs) {
  const endpointSummaries = {};
  for (const endpoint of endpoints) {
    const samples = runs.map((run) => run.endpoints[endpoint.name]);
    endpointSummaries[endpoint.name] = summarizeEndpoint(samples);
  }
  return { endpoints: endpointSummaries };
}

function compareSummaries(current, previous, options) {
  const rows = [];
  const currentEndpoints = current.summary.endpoints;
  const previousEndpoints = previous.summary?.endpoints || {};
  for (const [name, currentStats] of Object.entries(currentEndpoints)) {
    const previousStats = previousEndpoints[name];
    const currentValue = currentStats.medianMs;
    const previousValue = previousStats?.medianMs;
    const delta = Number.isFinite(currentValue) && Number.isFinite(previousValue)
      ? currentValue - previousValue
      : null;
    const changePct = Number.isFinite(currentValue) && Number.isFinite(previousValue) && previousValue > 0
      ? ((currentValue - previousValue) / previousValue) * 100
      : null;
    rows.push({
      changePct: changePct === null ? null : roundMs(changePct),
      current: currentValue ?? null,
      delta: delta === null ? null : roundMs(delta),
      endpoint: name,
      previous: previousValue ?? null,
      regressed: changePct !== null
        && changePct > options.maxRegressionPct
        && delta !== null
        && delta > options.maxRegressionMs,
    });
  }
  return rows;
}

function enforceHealthyResponses(result) {
  const failures = [];
  for (const [name, stats] of Object.entries(result.summary.endpoints)) {
    if (stats.okRate !== 1) {
      failures.push(`${name} okRate ${stats.okRate}`);
    }
  }
  if (failures.length > 0) {
    throw new Error(`Endpoint failures: ${failures.join("; ")}`);
  }
}

function enforceBudgets(result, options) {
  const failures = [];
  for (const [name, stats] of Object.entries(result.summary.endpoints)) {
    const endpointBudget = options.endpointBudgets.get(name);
    const budget = endpointBudget ?? options.allBudgetMs;
    if (budget !== null && Number.isFinite(stats.p75Ms) && stats.p75Ms > budget) {
      failures.push(`${name} p75 ${stats.p75Ms}ms exceeds ${budget}ms`);
    }
  }
  if (failures.length > 0) {
    throw new Error(failures.join("; "));
  }
}

function writeOutput(outputPath, result) {
  if (!outputPath) return;
  const resolved = path.resolve(process.cwd(), outputPath);
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  fs.writeFileSync(resolved, `${JSON.stringify(result, null, 2)}\n`);
}

function printHuman(result, outputPath) {
  console.log("Backend performance check passed.");
  console.log(`Base URL: ${result.baseUrl}`);
  console.log(`Runs: ${result.runs}`);
  for (const [name, stats] of Object.entries(result.summary.endpoints)) {
    console.log(
      `- ${name}: median ${stats.medianMs}ms, p75 ${stats.p75Ms}ms, okRate ${stats.okRate}, statuses ${JSON.stringify(stats.statusCodes)}`,
    );
  }
  if (result.compare) {
    console.log("Comparison against baseline:");
    for (const row of result.compare) {
      const change = row.changePct === null ? "n/a" : `${row.changePct}%`;
      const delta = row.delta === null ? "n/a" : `${row.delta}ms`;
      console.log(`- ${row.endpoint}: ${row.previous}ms -> ${row.current}ms (${change}, delta ${delta})`);
    }
  }
  if (outputPath) console.log(`Wrote JSON: ${path.resolve(process.cwd(), outputPath)}`);
}

async function run(options) {
  const endpoints = options.endpoints.map((endpoint) => ({
    ...endpoint,
    url: resolveEndpointUrl(options.baseUrl, endpoint.endpoint),
  }));
  const runRows = [];
  for (let runIndex = 1; runIndex <= options.runs; runIndex += 1) {
    const row = { endpoints: {}, run: runIndex };
    for (const endpoint of endpoints) {
      row.endpoints[endpoint.name] = await requestEndpoint(endpoint.url, options.timeoutMs);
    }
    runRows.push(row);
  }

  const result = {
    baseUrl: options.baseUrl,
    compare: null,
    createdAt: new Date().toISOString(),
    endpointDefinitions: endpoints.map(({ endpoint, name, url }) => ({ endpoint, name, url })),
    runs: options.runs,
    samples: runRows,
    summary: summarizeRuns(endpoints, runRows),
  };

  if (options.comparePath) {
    const previous = JSON.parse(
      fs.readFileSync(path.resolve(process.cwd(), options.comparePath), "utf8"),
    );
    result.compare = compareSummaries(result, previous, options);
    const regressions = result.compare.filter((row) => row.regressed);
    if (regressions.length > 0) {
      throw new Error(
        `Performance regression versus ${options.comparePath}: ${regressions
          .map((row) => `${row.endpoint} +${row.changePct}%`)
          .join(", ")}`,
      );
    }
  }

  enforceHealthyResponses(result);
  enforceBudgets(result, options);
  return result;
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (err) {
    fail(err.message);
  }
  if (options.help) {
    usage();
    return;
  }

  try {
    const result = await run(options);
    writeOutput(options.outputPath, result);
    if (options.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      printHuman(result, options.outputPath);
    }
  } catch (err) {
    fail(err.message);
  }
}

main();
