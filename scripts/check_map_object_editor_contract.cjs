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
assertIncludes(mapViewSource, "map-object-resize-handle", "missing resize handles");
assertIncludes(appSource, "updateMapObjectApi", "app must call update API");
assertIncludes(appSource, "createCustomMapObjectFromSelection", "missing custom-object creation flow");
assertIncludes(appSource, "updateMapObjectGeometry", "missing map geometry update flow");

console.log("Map object editor contract checks passed.");
