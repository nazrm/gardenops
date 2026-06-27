#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "frontend", "src", "services", "api.ts");
const source = fs.readFileSync(sourcePath, "utf8");

function fail(message) {
  console.error(`AI chat client contract check failed: ${message}`);
  process.exit(1);
}

const optionsMatch = source.match(/type ApiRequestOptions = \{(?<body>[\s\S]*?)\n\};/);
if (!optionsMatch?.groups?.body.includes("timeoutMessage?: string;")) {
  fail("ApiRequestOptions must support an endpoint-specific timeoutMessage.");
}

if (!source.includes("const AI_CHAT_TIMEOUT_MS = 90_000;")) {
  fail("garden chat must use AI_CHAT_TIMEOUT_MS = 90_000.");
}

if (!source.includes('const AI_CHAT_TIMEOUT_MESSAGE = "AI request timed out";')) {
  fail("garden chat must define the AI-specific timeout message.");
}

const gardenChatMatch = source.match(
  /export async function gardenChatApi\([\s\S]*?return data\.reply;\n\}/,
);
if (!gardenChatMatch) {
  fail("gardenChatApi function was not found.");
}

const gardenChatSource = gardenChatMatch[0];
if (!gardenChatSource.includes("timeoutMs: AI_CHAT_TIMEOUT_MS")) {
  fail("gardenChatApi must pass AI_CHAT_TIMEOUT_MS to apiPost.");
}
if (!gardenChatSource.includes("timeoutMessage: AI_CHAT_TIMEOUT_MESSAGE")) {
  fail("gardenChatApi must pass AI_CHAT_TIMEOUT_MESSAGE to apiPost.");
}
