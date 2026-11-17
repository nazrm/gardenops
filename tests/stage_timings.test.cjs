const assert = require("node:assert/strict");
const { test } = require("node:test");
const { createStageTimings } = require("../scripts/e2e/stageTimings.cjs");

test("stage transitions and interrupted stages report monotonic durations once", () => {
  let time = 10;
  const records = [];
  const timer = createStageTimings("journey", {
    now: () => time,
    write: (line) => records.push(JSON.parse(line)),
  });
  timer.start("setup");
  time = 35;
  timer.start("browser");
  time = 55;
  timer.finish("interrupted");
  timer.finish();
  assert.deepEqual(records.map(({ stage, elapsed_ms, outcome }) => [stage, elapsed_ms, outcome]), [
    ["setup", 25, "finished"], ["browser", 20, "interrupted"],
  ]);
  assert(records.every((record) => record.kind === "gardenops-stage-timing"));
});

test("diagnostic write failures never interrupt verification or cleanup", () => {
  const timer = createStageTimings("journey", { write() { throw new Error("closed sink"); } });
  timer.start("setup");
  assert.doesNotThrow(() => timer.start("cleanup"));
  assert.doesNotThrow(() => timer.finish());
});
