# Map Object Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users create custom map objects and edit object position, size, shape, color, name, and optional internal layouts without changing the underlying plot model.

**Architecture:** Reuse the existing `garden_map_objects` backend patch endpoint and add the missing frontend update API, panel editor controls, and selected-object map handles. Custom objects are stored as existing `object_type: "other"` records so no new backend schema or migration is required.

**Tech Stack:** TypeScript DOM UI, Vite frontend build, existing FastAPI map object routes, static Node contract checks.

---

## File Structure

- Create `scripts/check_map_object_editor_contract.cjs`: static contract test proving the API/update UI/map-interaction hooks exist.
- Modify `frontend/package.json`: add the new contract check to `npm run build`.
- Modify `frontend/src/services/api.ts`: add `updateMapObjectApi()` using the existing PATCH endpoint.
- Modify `frontend/src/components/mapObjects.ts`: add custom-object creation form plus selected-object edit controls.
- Modify `frontend/src/components/mapView.ts`: add selected-object move/resize controls on the map.
- Modify `frontend/src/app.ts`: wire creation/update callbacks, clamp geometry, refresh state, and pass map-edit callbacks.
- Modify `frontend/src/core/i18n.ts`: add short English and Norwegian labels for the new controls.
- Modify `frontend/src/style.css`: style the compact editor controls and selected-object map handles.
- Modify relevant docs if the documentation upkeep check shows user-facing documentation needs an update.

---

### Task 1: Contract Test

**Files:**
- Create: `scripts/check_map_object_editor_contract.cjs`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write the failing test**

Create a Node script that reads the frontend source and asserts:

```javascript
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
```

Add `"check:map-object-editor": "node ../scripts/check_map_object_editor_contract.cjs"` to `frontend/package.json` and include it in the `build` chain before `tsc --noEmit`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend
npm run check:map-object-editor
```

Expected: FAIL because `updateMapObjectApi` and the new editor callbacks do not exist yet.

---

### Task 2: Frontend API

**Files:**
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Implement the update API**

Add:

```typescript
export async function updateMapObjectApi(
  gardenId: number,
  publicId: string,
  object: Partial<MapObjectInput>,
): Promise<MapObject> {
  return apiPatch<MapObject>(`/api/gardens/${gardenId}/map-objects/${publicId}`, object);
}
```

- [ ] **Step 2: Verify the contract progresses**

Run:

```bash
cd frontend
npm run check:map-object-editor
```

Expected: still FAIL, now on missing UI/app hooks.

---

### Task 3: Panel Editing UI

**Files:**
- Modify: `frontend/src/components/mapObjects.ts`
- Modify: `frontend/src/core/i18n.ts`
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add editor types**

Add `MapObjectInput`, `MapObjectShape`, `MapObjectGeometry`, and `MapObjectInternalLayout` imports. Add callbacks:

```typescript
onCreateCustomObject: (draft: MapObjectCustomDraft) => void;
onUpdateObject: (publicId: string, patch: Partial<MapObjectInput>) => void;
```

- [ ] **Step 2: Add custom-object form**

Add a compact form with fields for name, shape, color, optional internal layout, rows, and columns. On submit, call `onCreateCustomObject()` with `object_type: "other"` implied by app code.

- [ ] **Step 3: Add selected-object editor**

Add a selected-object form for name, shape, color, x, y, width, height, and internal layout. On submit, call `onUpdateObject(selected.public_id, patch)`.

- [ ] **Step 4: Keep nested units optional**

Only render the unit add buttons and unit grid when `selected.has_internal_layout` is true. Render a short disabled state otherwise.

---

### Task 4: App Wiring

**Files:**
- Modify: `frontend/src/app.ts`

- [ ] **Step 1: Import the update API and panel draft type**

Import `updateMapObjectApi` and the custom draft type from the panel module.

- [ ] **Step 2: Add geometry clamping**

Add helpers that clamp object geometry within `state.gridRows` and `state.gridCols`:

```typescript
function clampMapObjectGeometry(geometry: MapObject["geometry"]): MapObject["geometry"] {
  const width = Math.max(1, Math.min(Math.trunc(geometry.width), state.gridCols));
  const height = Math.max(1, Math.min(Math.trunc(geometry.height), state.gridRows));
  const x = Math.max(1, Math.min(Math.trunc(geometry.x), state.gridCols - width + 1));
  const y = Math.max(1, Math.min(Math.trunc(geometry.y), state.gridRows - height + 1));
  return { x, y, width, height };
}
```

- [ ] **Step 3: Add custom create and update flows**

Create `createCustomMapObjectFromSelection(draft)` and `updateMapObject(publicId, patch)`. Both should use existing toast/error patterns and refresh map objects after success.

- [ ] **Step 4: Wire callbacks**

Pass `onCreateCustomObject`, `onUpdateObject`, and direct map object geometry callbacks into the panel and map renderer.

---

### Task 5: Direct Map Editing

**Files:**
- Modify: `frontend/src/components/mapView.ts`
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add map geometry callback**

Extend `RenderMapParams` and `GridCallbacks`:

```typescript
onMapObjectGeometryChange?: (object: MapObject, geometry: MapObject["geometry"]) => void;
```

- [ ] **Step 2: Add selected-object controls**

For the selected object, render a move handle and resize handle buttons on top of the overlay. Use button controls that shift or resize by one grid cell, avoiding drag math conflicts with map zoom and plot drag/drop.

- [ ] **Step 3: Call geometry callback**

Each control should call `onMapObjectGeometryChange(object, nextGeometry)` with clamped geometry handled by app code.

---

### Task 6: Validation

**Files:**
- Modify docs only if required by documentation upkeep.

- [ ] **Step 1: Run focused contract**

Run:

```bash
cd frontend
npm run check:map-object-editor
```

Expected: PASS.

- [ ] **Step 2: Run full frontend build**

Run:

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run backend map-object regression tests**

Run:

```bash
uv run pytest tests/test_map_objects.py -q
```

Expected: PASS.

- [ ] **Step 4: Run documentation upkeep and git push sanitizer checks**

Use the repo-local skills to inspect docs impact and outbound git safety before committing and pushing.

---

## Self-Review

- Spec coverage: the plan covers moving, scaling, side-panel controls, direct map controls, custom objects, optional nested units, and existing backend reuse.
- Placeholder scan: no `TBD`, `TODO`, or unspecified test commands remain.
- Type consistency: all new frontend callbacks use `MapObjectInput` partial patches and existing `MapObject["geometry"]` types.
