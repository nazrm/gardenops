#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");

function readSource(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

function assertIncludes(source, needle, message) {
  if (!source.includes(needle)) {
    throw new Error(`${message}: expected ${needle}`);
  }
}

function assertExcludes(source, needle, message) {
  if (source.includes(needle)) {
    throw new Error(`${message}: unexpected ${needle}`);
  }
}

const apiSource = readSource("frontend/src/services/api.ts");
const panelSource = readSource("frontend/src/components/mapObjects.ts");
const mapViewSource = readSource("frontend/src/components/mapView.ts");
const appSource = readSource("frontend/src/app.ts");

assertIncludes(apiSource, "export async function updateMapObjectApi", "missing update API");
assertIncludes(apiSource, "apiPatch<MapObject>", "update API must use PATCH");
assertIncludes(panelSource, "onCreateCustomObject", "missing custom create callback");
assertIncludes(panelSource, "onUpdateObject", "missing object update callback");
assertIncludes(panelSource, "map-object-custom-form", "missing custom-object form");
assertIncludes(panelSource, "map-object-geometry-form", "missing geometry editor form");
assertIncludes(mapViewSource, "onMapObjectGeometryChange", "missing map geometry callback");
assertIncludes(mapViewSource, "onMapObjectManipulationStart", "missing direct manipulation start callback");
assertIncludes(mapViewSource, "map-object-interaction-surface", "missing object drag surface");
assertIncludes(mapViewSource, "map-object-resize-handle", "missing object resize handles");
assertIncludes(mapViewSource, "map-object-preview", "missing object manipulation preview");
assertIncludes(mapViewSource, "aria-keyshortcuts", "selected object must expose keyboard editing affordances");
assertExcludes(mapViewSource, "makeObjectEditButton", "old text-button object controls must be removed");
assertExcludes(mapViewSource, "map-object-move-handle", "old compass object move controls must be removed");
assertExcludes(mapViewSource, "+W", "old text resize controls must be removed");
assertExcludes(mapViewSource, "-W", "old text resize controls must be removed");
assertIncludes(appSource, "updateMapObjectApi", "app must call update API");
assertIncludes(appSource, "createCustomMapObjectFromSelection", "missing custom-object creation flow");
assertIncludes(appSource, "updateMapObjectGeometry", "missing map geometry update flow");
assertIncludes(appSource, "mapObjectManipulationSession", "missing object manipulation session state");
assertIncludes(appSource, "startMapObjectManipulation", "missing object manipulation start flow");
assertIncludes(appSource, "cancelMapObjectManipulation", "missing object manipulation cancel flow");
assertIncludes(appSource, "commitMapObjectManipulation", "missing object manipulation commit flow");

console.log("Map object editor contract checks passed.");
