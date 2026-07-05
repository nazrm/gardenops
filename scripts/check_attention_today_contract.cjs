#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
const assertIncludes = (source, needle, message) => {
  if (!source.includes(needle)) throw new Error(message);
};

const models = read("frontend/src/core/models.ts");
const api = read("frontend/src/services/api.ts");
const layout = read("frontend/src/components/layout.ts");
const panel = read("frontend/src/components/attentionTodayPanel.ts");
const app = read("frontend/src/app.ts");
const styles = read("frontend/src/style.css");
const e2e = read("scripts/check_attention_today_e2e.cjs");
const seed = read("scripts/seed_attention_today_e2e.py");
const pkg = JSON.parse(read("frontend/package.json"));

assertIncludes(models, "export interface AttentionTodayResponse", "missing AttentionTodayResponse model");
assertIncludes(models, "export interface AttentionItem", "missing AttentionItem model");
assertIncludes(api, "export async function fetchAttentionTodayApi", "missing fetchAttentionTodayApi");
assertIncludes(layout, "attention-today-panel", "missing desktop Today panel anchor");
assertIncludes(layout, "attention-today-mobile-handle", "missing mobile Today handle");
assertIncludes(panel, "document.createElement", "Today panel must use DOM construction, not broad innerHTML templates");
assertIncludes(panel, "data-testid", "Today panel needs stable Playwright hooks");
assertIncludes(panel, "destroy(): void", "Today panel controller must support teardown");
assertIncludes(panel, "closeMobileSheet(): void", "Today panel controller must expose mobile sheet cleanup");
assertIncludes(panel, "refreshSequence", "Today panel must guard against stale refresh responses");
assertIncludes(panel, "setMobileSheetHidden", "closed mobile Today sheet must be hidden from focus order");
assertIncludes(panel, "trapMobileSheetFocus", "mobile Today dialog must trap keyboard focus while open");
assertIncludes(panel, "Escape", "mobile Today dialog must close on Escape");
assertIncludes(panel, "severityLabel", "Attention severity must be exposed as text, not only color");
assertIncludes(panel, "attention-today-child-details", "grouped Attention items must render expandable children");
assertIncludes(panel, "attention-today-overflow", "bounded sections must expose overflow affordance");
assertIncludes(panel, "attention-show-no-action-history", "Attention settings must expose no-action history");
assertIncludes(panel, "aria-expanded", "Today section toggles must expose expanded state");
assertIncludes(panel, "aria-controls", "Today section toggles must reference controlled content");
assertIncludes(panel, "attention-preferences-dialog", "missing Attention settings dialog");
assertIncludes(app, "initAttentionTodayPanel", "app must initialize Attention Today panel");
assertIncludes(app, "fetchAttentionTodayApi", "app must call Attention Today API");
assertIncludes(app, "attentionTodayPanel?.destroy()", "app must teardown Today panel when feature gate disables it");
assertIncludes(app, "attentionTodayPanel?.closeMobileSheet()", "app must close mobile Today sheet outside mobile map context");
assertIncludes(app, "action.kind === \"open_issue\"", "Attention issue actions must route to Issues");
assertIncludes(app, "action.kind === \"open_attention_detail\"", "group summary actions must not fall through to Tasks");
assertIncludes(app, "onViewSection", "section overflow affordance must route out of the compact panel");
assertIncludes(styles, ".attention-today-panel", "missing desktop panel styles");
assertIncludes(styles, "max-height: min(60dvh", "mobile Today sheet must keep the map-first 60vh cap");
assertIncludes(styles, "@media (max-width: 1180px)", "missing tablet map-first layout guard");
assertIncludes(styles, "@media (prefers-reduced-motion: reduce)", "missing reduced motion handling");
assertIncludes(e2e, "snapshot-notifications", "E2E must snapshot notifications around Today reads");
assertIncludes(e2e, "Water indoor basil", "E2E must cover indoor manual watering");
assertIncludes(e2e, "Water hydrangea", "E2E must prove generated outdoor watering is suppressed");
assertIncludes(e2e, "Check mildew on cucumber", "E2E must cover issue follow-up navigation");
assertIncludes(seed, "require_attention_e2e_database", "E2E seed must keep the database safety guard");
assertIncludes(seed, "watering_covered_by_rain", "E2E seed must include rain-covered watering outcome");
assertIncludes(seed, "Extra rain outcome", "E2E seed must cover bounded no-action overflow");
if (!pkg.scripts || !pkg.scripts["check:attention-today"]) {
  throw new Error("missing check:attention-today package script");
}
