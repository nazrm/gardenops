#!/usr/bin/env node
"use strict";

/*
 * Test-only provider fixture. A runner starts one process per fixed scenario
 * and consumes the ready record or ready file to configure vendor base URLs.
 * Request bodies are parsed transiently to derive a value-free shape; they are
 * never logged, returned, or written to disk.
 */

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const HOST = "127.0.0.1";
const SCENARIOS = new Set([
  "success",
  "timeout",
  "malformed",
  "quota",
  "unauthorized",
  "partial",
]);

const PROVIDER_PATHS = new Set(["/v1/responses"]);
const MAX_BODY_BYTES = 20 * 1024 * 1024;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function parseInteger(value, name, { min = 0 } = {}) {
  if (!/^\d+$/.test(value)) fail(`${name} must be a non-negative integer`);
  const parsed = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(parsed) || parsed < min) {
    fail(`${name} must be at least ${min}`);
  }
  return parsed;
}

function parseArguments(argv) {
  const options = {
    port: 0,
    scenario: "success",
    timeoutMs: 2_000,
    readyFile: "",
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const next = () => {
      index += 1;
      if (index >= argv.length) fail(`${argument} requires a value`);
      return argv[index];
    };
    if (argument === "--port") options.port = parseInteger(next(), "--port");
    else if (argument.startsWith("--port=")) {
      options.port = parseInteger(argument.slice("--port=".length), "--port");
    } else if (argument === "--scenario") options.scenario = next();
    else if (argument.startsWith("--scenario=")) {
      options.scenario = argument.slice("--scenario=".length);
    } else if (argument === "--timeout-ms") {
      options.timeoutMs = parseInteger(next(), "--timeout-ms", { min: 1 });
    } else if (argument.startsWith("--timeout-ms=")) {
      options.timeoutMs = parseInteger(
        argument.slice("--timeout-ms=".length),
        "--timeout-ms",
        { min: 1 },
      );
    } else if (argument === "--ready-file") options.readyFile = next();
    else if (argument.startsWith("--ready-file=")) {
      options.readyFile = argument.slice("--ready-file=".length);
    } else if (argument === "--host" || argument.startsWith("--host=")) {
      fail("The fixture host is fixed to 127.0.0.1");
    } else if (argument === "--help") {
      process.stdout.write(
        "Usage: deterministicLoopbackProvider.cjs [--scenario NAME] [--port 0] "
          + "[--timeout-ms 2000] [--ready-file PATH]\n",
      );
      process.exit(0);
    } else {
      fail(`Unknown argument: ${argument}`);
    }
  }
  if (!SCENARIOS.has(options.scenario)) {
    fail(`--scenario must be one of: ${[...SCENARIOS].join(", ")}`);
  }
  return options;
}

const options = parseArguments(process.argv.slice(2));
const state = {
  scenario: options.scenario,
  counts: {
    provider_requests: 0,
    by_path: {},
    by_scenario: { [options.scenario]: 0 },
  },
  requests: [],
};
const sockets = new Set();
const pendingTimers = new Set();
let shuttingDown = false;

function json(response, statusCode, payload, headers = {}) {
  response.writeHead(statusCode, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    ...headers,
  });
  response.end(JSON.stringify(payload));
}

function controlCorsHeaders() {
  return {
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-origin": "*",
    vary: "origin",
  };
}

function contentType(request) {
  return String(request.headers["content-type"] || "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
}

function requestShape(body, request) {
  const shape = {
    body_kind: "other",
    content_type: contentType(request) || null,
    json_keys: [],
  };
  if (body.length === 0) {
    shape.body_kind = "empty";
    return shape;
  }
  if (shape.content_type !== "application/json") {
    shape.body_kind = "binary_or_form";
    return shape;
  }
  try {
    const parsed = JSON.parse(body.toString("utf8"));
    if (Array.isArray(parsed)) {
      shape.body_kind = "json_array";
      shape.json_keys = ["[]"];
    } else if (parsed && typeof parsed === "object") {
      shape.body_kind = "json_object";
      shape.json_keys = Object.keys(parsed).sort();
    } else {
      shape.body_kind = "json_scalar";
    }
  } catch {
    shape.body_kind = "invalid_json";
  }
  return shape;
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("request body exceeds fixture limit"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks)));
    request.on("error", reject);
  });
}

function recordRequest(method, pathname, shape) {
  state.counts.provider_requests += 1;
  state.counts.by_path[pathname] = (state.counts.by_path[pathname] || 0) + 1;
  state.counts.by_scenario[state.scenario] = (state.counts.by_scenario[state.scenario] || 0) + 1;
  state.requests.push({ method, path: pathname, request_shape: shape });
}

function requestJson(body, request) {
  if (contentType(request) !== "application/json") return null;
  try {
    const parsed = JSON.parse(body.toString("utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function responseFormatName(requestPayload) {
  const name = requestPayload?.text?.format?.name;
  return typeof name === "string" ? name : "";
}

function inputText(requestPayload) {
  const messages = Array.isArray(requestPayload?.input) ? requestPayload.input : [];
  const texts = [];
  for (const message of messages) {
    const content = Array.isArray(message?.content) ? message.content : [];
    for (const item of content) {
      if (typeof item?.text === "string") texts.push(item.text);
    }
  }
  return texts;
}

function promptJsonItems(requestPayload, property) {
  const items = [];
  for (const text of inputText(requestPayload)) {
    const newline = text.lastIndexOf("\n");
    if (newline < 0) continue;
    try {
      const parsed = JSON.parse(text.slice(newline + 1));
      if (Array.isArray(parsed)) items.push(...parsed);
    } catch {
      // Prompts are untrusted input and are never retained or logged.
    }
  }
  return items
    .map((item) => item?.[property])
    .filter((value) => typeof value === "string" && value)
    .map((value) => String(value));
}

function structuredOutput(requestPayload) {
  const formatName = responseFormatName(requestPayload);
  if (!formatName) return "Deterministic test reply: Check soil moisture before watering.";
  if (formatName === "plant_candidates") {
    return JSON.stringify({
      candidates: [
        {
          name: "Test rose",
          latin: "Rosa canina",
          scientific_name: "Rosa canina",
          family: "Rosaceae",
          confidence: 0.9,
        },
      ],
    });
  }
  if (formatName === "plant_diagnoses") {
    return JSON.stringify({
      diagnoses: [
        {
          issue_type: "environmental",
          likely_cause: "Dry soil",
          confidence: "low",
          description: "The plant may be mildly drought stressed.",
          suggested_treatment: "Water deeply, then monitor soil moisture.",
          reasoning: "Deterministic loopback fixture.",
          related_history: "",
        },
      ],
    });
  }
  if (formatName === "plant_data") {
    return JSON.stringify({
      name: "Fixture plant",
      latin: "Testus e2e",
      category: "stauder",
      bloom_month: "juni-august",
      color: "green",
      hardiness: "H5",
      height_cm: 45,
      light: "sol",
      link: "",
    });
  }
  if (formatName === "care_instructions_batch") {
    return JSON.stringify({
      plants: promptJsonItems(requestPayload, "plt_id").map((pltId) => ({
        plt_id: pltId,
        care_watering: "Water when the topsoil is dry.",
        care_soil: "Use well-drained garden soil.",
        care_planting: "Plant at the same depth as the root ball.",
        care_maintenance: "Remove damaged growth and check weekly.",
        care_notes: "Deterministic loopback fixture.",
      })),
    });
  }
  if (formatName === "task_descriptions_batch") {
    return JSON.stringify({
      tasks: promptJsonItems(requestPayload, "task_key").map((taskKey) => ({
        task_key: taskKey,
        description_en: "Complete this garden task in the planned window.",
        description_no: "Fullfør denne hageoppgaven i det planlagte tidsvinduet.",
      })),
    });
  }
  return JSON.stringify({ fixture: "unsupported_response_format" });
}

function openAiResponse(requestPayload, { partial = false } = {}) {
  return {
    id: partial ? "resp_fixture_partial" : "resp_fixture_success",
    object: "response",
    created_at: 0,
    status: partial ? "incomplete" : "completed",
    error: null,
    incomplete_details: partial ? { reason: "max_output_tokens" } : null,
    model: "gardenops-loopback-fixture",
    output: [
      {
        id: "msg_fixture_1",
        type: "message",
        role: "assistant",
        status: partial ? "incomplete" : "completed",
        content: [
          {
            type: "output_text",
            text: partial ? "Fixture response is intentionally partial." : structuredOutput(requestPayload),
            annotations: [],
          },
        ],
      },
    ],
  };
}

function providerError(pathname, statusCode, type, message, headers = {}) {
  return {
    statusCode,
    headers,
    payload: { error: { type, code: type, message } },
  };
}

function respond(pathname, response, requestPayload) {
  if (state.scenario === "timeout") {
    const timer = setTimeout(() => {
      pendingTimers.delete(timer);
      if (!response.destroyed) response.destroy();
    }, options.timeoutMs);
    pendingTimers.add(timer);
    response.on("close", () => {
      clearTimeout(timer);
      pendingTimers.delete(timer);
    });
    return;
  }
  if (state.scenario === "malformed") {
    response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    response.end('{"fixture":"malformed"');
    return;
  }
  if (state.scenario === "quota") {
    const error = providerError(
      pathname,
      429,
      "insufficient_quota",
      "Fixture quota exhausted.",
      { "retry-after": "1" },
    );
    json(response, error.statusCode, error.payload, error.headers);
    return;
  }
  if (state.scenario === "unauthorized") {
    const error = providerError(
      pathname,
      401,
      "authentication_error",
      "Fixture credentials rejected.",
    );
    json(response, error.statusCode, error.payload, error.headers);
    return;
  }
  const partial = state.scenario === "partial";

  json(
    response,
    200,
    openAiResponse(requestPayload, { partial }),
  );
}

const server = http.createServer(async (request, response) => {
  const requestUrl = new URL(request.url || "/", `http://${HOST}`);
  const pathname = requestUrl.pathname;
  if (request.method === "GET" && pathname === "/healthz") {
    json(response, 200, { status: "ok", scenario: state.scenario });
    return;
  }

  if (pathname === "/__fixture__/state" && request.method === "OPTIONS") {
    response.writeHead(204, controlCorsHeaders());
    response.end();
    return;
  }
  if (pathname === "/__fixture__/scenario" && request.method === "OPTIONS") {
    response.writeHead(204, controlCorsHeaders());
    response.end();
    return;
  }
  if (request.method === "GET" && pathname === "/__fixture__/state") {
    json(response, 200, state, controlCorsHeaders());
    return;
  }
  if (request.method === "POST" && pathname === "/__fixture__/scenario") {
    try {
      const body = await readBody(request);
      const payload = requestJson(body, request);
      const scenario = payload?.scenario;
      if (typeof scenario !== "string" || !SCENARIOS.has(scenario)) {
        json(response, 400, { error: "scenario must be a supported fixture scenario" }, controlCorsHeaders());
        return;
      }
      state.scenario = scenario;
      if (!(scenario in state.counts.by_scenario)) state.counts.by_scenario[scenario] = 0;
      json(response, 200, { scenario }, controlCorsHeaders());
    } catch {
      if (!response.headersSent) {
        json(response, 400, { error: "invalid scenario control request" }, controlCorsHeaders());
      }
    }
    return;
  }
  if (request.method !== "POST" || !PROVIDER_PATHS.has(pathname)) {
    json(response, 404, { error: "fixture endpoint not found" });
    return;
  }
  try {
    const body = await readBody(request);
    const payload = requestJson(body, request);
    recordRequest(request.method, pathname, requestShape(body, request));
    respond(pathname, response, payload);
  } catch {
    if (!response.headersSent) {
      json(response, 413, { error: "fixture request body exceeds limit" });
    }
  }
});

server.on("connection", (socket) => {
  sockets.add(socket);
  socket.on("close", () => sockets.delete(socket));
});

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const timer of pendingTimers) clearTimeout(timer);
  pendingTimers.clear();
  for (const socket of sockets) socket.destroy();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1_000).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

server.listen({ host: HOST, port: options.port }, () => {
  const address = server.address();
  if (!address || typeof address === "string" || address.address !== HOST) {
    fail("Fixture did not bind to 127.0.0.1");
  }
  const rootUrl = `http://${HOST}:${address.port}`;
  const handoff = {
    fixture: "gardenops-deterministic-loopback-provider",
    host: HOST,
    port: address.port,
    scenario: options.scenario,
    openai_base_url: `${rootUrl}/v1`,
    fixture_base_url: rootUrl,
    state_url: `${rootUrl}/__fixture__/state`,
    control_url: `${rootUrl}/__fixture__/scenario`,
    env: {
      OPENAI_BASE_URL: `${rootUrl}/v1`,
      GARDENOPS_E2E_LOOPBACK_PROVIDER: "1",
      GARDENOPS_E2E_PROVIDER_URL: `${rootUrl}/v1`,
      GARDENOPS_PROVIDER_FIXTURE_STATE_URL: `${rootUrl}/__fixture__/state`,
      GARDENOPS_PROVIDER_FIXTURE_CONTROL_URL: `${rootUrl}/__fixture__/scenario`,
      GARDENOPS_PROVIDER_FIXTURE_SCENARIO: options.scenario,
    },
  };
  const serialized = JSON.stringify(handoff);
  if (options.readyFile) {
    fs.mkdirSync(path.dirname(options.readyFile), { recursive: true });
    fs.writeFileSync(options.readyFile, `${serialized}\n`, { encoding: "utf8", mode: 0o600 });
  }
  process.stdout.write(`GARDENOPS_PROVIDER_FIXTURE_READY=${serialized}\n`);
});
