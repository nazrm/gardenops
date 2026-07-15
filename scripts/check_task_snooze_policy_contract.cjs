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
for (const windowBound of ["window_start_on", "window_end_on"]) {
  if (!source.includes(windowBound)) {
    throw new Error(`Snooze policy must account for ${windowBound}`);
  }
}
if (!source.includes("formatLocalDate")) {
  throw new Error("Snooze policy must format local calendar dates");
}
if (source.includes("toISOString()")) {
  throw new Error("Snooze policy must not derive dates with toISOString()");
}
for (const requiredFragment of [
  "taskSnoozeDateSafety",
  "taskSnoozeMaximumDate",
  "weather_valid_until",
  "snoozeUntil > weatherDeadline",
  "next_recurrence_on",
  "snoozeUntil >= nextRecurrence",
]) {
  if (!source.includes(requiredFragment)) {
    throw new Error(`Snooze policy is missing selected-date safety: ${requiredFragment}`);
  }
}

const snoozeFlow = path.join(root, "frontend/src/features/taskSnoozeFlow.ts");
const snoozeFlowSource = fs.readFileSync(snoozeFlow, "utf8");
for (const requiredFragment of [
  "getDateSafety",
  "input.addEventListener(\"input\", updateDateSafety)",
  "input.addEventListener(\"change\", updateDateSafety)",
  "safety?.blocked",
  "safety?.confirmationRequired",
  "confirmDialog(",
  "await onConfirm(",
  "result === false",
  "tasks.dialog_submit_failed",
  "okBtn.disabled = pending",
]) {
  if (!snoozeFlowSource.includes(requiredFragment)) {
    throw new Error(`Task date dialog is missing selected-date safety: ${requiredFragment}`);
  }
}

const completionHelper = path.join(root, "frontend/src/features/taskCompletionFlow.ts");
if (!fs.existsSync(completionHelper)) {
  throw new Error("Missing taskCompletionFlow.ts");
}
const completionSource = fs.readFileSync(completionHelper, "utf8");
if (!completionSource.includes("needsCompletionDialog")) {
  throw new Error("Completion flow must expose a dialog decision helper");
}
if (!completionSource.includes("canQueueDefaultCompletionOffline")) {
  throw new Error("Completion flow must preserve offline default completion for simple task actions");
}
if (!completionSource.includes("canQueueCompletionOffline")) {
  throw new Error("Completion flow must explicitly allow supported offline bloom outcomes");
}
if (!completionSource.includes("offlineTaskActionLabels")) {
  throw new Error("Completion flow must provide human-readable offline task action labels");
}
if (!completionSource.includes('task.task_type === "observe_bloom"')) {
  throw new Error("Observe-bloom completion must open the completion dialog");
}
if (!completionSource.includes("not_seen_blooming_this_season")) {
  throw new Error("Completion flow must expose the not-seen bloom outcome");
}
if (completionSource.includes("ids.slice(0, 5)")) {
  throw new Error("Large grouped completion must not silently preselect only five plants");
}
for (const requiredFragment of [
  "await onConfirm(body)",
  "result === false",
  "tasks.dialog_submit_failed",
  "confirm.disabled = submitting",
]) {
  if (!completionSource.includes(requiredFragment)) {
    throw new Error(`Completion dialog must await recoverable submission: ${requiredFragment}`);
  }
}

for (const relativePath of [
  "frontend/src/tabs/tasksTab.ts",
  "frontend/src/tabs/calendarTab.ts",
  "frontend/src/components/plotInteractions.ts",
  "frontend/src/features/quickActionsFeature.ts",
]) {
  const surfaceSource = fs.readFileSync(path.join(root, relativePath), "utf8");
  if (!surfaceSource.includes("canQueueDefaultCompletionOffline")) {
    throw new Error(`${relativePath} must preserve offline default completion for simple task actions`);
  }
  if (!surfaceSource.includes("canQueueCompletionOffline")) {
    throw new Error(`${relativePath} must gate supported offline bloom completion`);
  }
  if (!surfaceSource.includes("offlineTaskActionLabels")) {
    throw new Error(`${relativePath} must retain human-readable offline task action labels`);
  }
  if (!surfaceSource.includes("taskSnoozeDateSafety")) {
    throw new Error(`${relativePath} must recheck safety for the selected snooze date`);
  }
  if (!surfaceSource.includes("getDateSafety")) {
    throw new Error(`${relativePath} must pass selected-date safety into the date dialog`);
  }
}
