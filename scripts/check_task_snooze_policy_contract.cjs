#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const helper = path.join(root, "frontend/src/features/taskSnoozePolicy.ts");
if (!fs.existsSync(helper)) {
  throw new Error("Missing taskSnoozePolicy.ts");
}
const source = fs.readFileSync(helper, "utf8");
for (const taskType of ["observe_bloom", "prune", "fertilize"]) {
  if (!source.includes(taskType)) {
    throw new Error(`Missing mapped snooze policy for ${taskType}`);
  }
}
if (!source.includes("window_end_on")) {
  throw new Error("Snooze policy must account for window_end_on");
}
