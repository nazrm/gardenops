"use strict";

const { performance } = require("node:perf_hooks");

// Diagnostic only: these records never assert success or enter release evidence.
function createStageTimings(scope, { now = () => performance.now(), write = (line) => process.stderr.write(line) } = {}) {
  let active = null;
  let started = 0;
  function finish(outcome = "finished") {
    if (active === null) return;
    const record = {
      kind: "gardenops-stage-timing",
      scope,
      stage: active,
      elapsed_ms: Math.max(0, Math.round(now() - started)),
      outcome,
    };
    active = null;
    try {
      write(`${JSON.stringify(record)}\n`);
    } catch {
      // A diagnostic sink must not change the test result or interrupt cleanup.
    }
  }
  return {
    start(stage) {
      finish();
      active = stage;
      started = now();
    },
    finish,
  };
}

module.exports = { createStageTimings };
