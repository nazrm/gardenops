# Map Objects And Nested Layouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user create a top-level map object such as a patio or terrace, open it as a focused nested layout, and add shaped local planting units such as pots and planters without changing the existing ground-plot model.

**Architecture:** Add a garden-scoped map-object API backed by two new tables: one for top-level map objects and one for nested planting units. Render map objects as an SVG/HTML overlay aligned with the existing CSS grid; render nested layouts as a focused panel opened from a selected object. Keep existing `plots` as canonical ground-grid cells and do not attach plants, tasks, alerts, harvests, or reports to nested planting units in this first slice.

**Tech Stack:** FastAPI, Pydantic strict request models, PostgreSQL SQL migrations, vanilla TypeScript, Vite, existing GardenOps map CSS/DOM renderer, pytest, TypeScript typecheck/build, Playwright/browser smoke flow where available.

**Implementation status:** Implemented on branch `codex/map-objects-nested-layouts`
on 2026-06-28. The final implementation covers persisted map objects, nested
layout units, import/export, snapshot refresh, viewer/editor permissions,
frontend layer controls, map overlays, docs, and backend/frontend tests.
Validation completed with:

- `npm run typecheck`
- `npm run build`
- `UV_CACHE_DIR=/tmp/gardenops-uv-cache uv run python scripts/check_env_docs.py`
- `git diff --check`
- `uv run python scripts/run_fast_postgres_tests.py --full-suite --shards 4`
- Chromium E2E against a disposable local backend and PostgreSQL database:
  login, create patio, add pot, verify API, verify export, reload, select the
  persisted map label, and verify the nested unit is visible.

---

## Scope

This plan implements the first production-safe slice of the recommended design:

- Top-level map objects: `patio`, `terrace`, `greenhouse`, `shed`, `pond`, `path`, `bed`, `other`.
- Shape types: `rectangle`, `ellipse`.
- Nested planting units inside map objects: `pot`, `planter`, `raised_bed`, `shelf`, `other`.
- Nested unit shape types: `rectangle`, `ellipse`.
- Geometry stored in grid-relative coordinates.
- Import/export and snapshots include map objects and nested units.
- UI lets a real user create a patio from selected grid cells, open its nested layout, add a pot or planter, and see the data persist after reload.

This plan intentionally does **not** implement plant/task/journal/harvest/calendar assignment to nested units. That requires a later `planting_locations` abstraction and updates across the existing plot-linked tables.

## File Structure

- Create `migrations/0018_map_objects_nested_layouts.sql`
  - Defines `garden_map_objects` and `garden_map_object_units`.
  - Adds garden-scoped indexes, unique public IDs, and cascade delete from object to units.
- Modify `gardenops/schema_signature.py`
  - Adds the new tables, columns, indexes, and constraints to bootstrap/integrity checks.
- Create `gardenops/routers/map_objects.py`
  - Owns validation, serialization, CRUD routes, editor authorization, garden scoping, rate limits, and audit events for map objects and units.
- Modify `gardenops/main.py`
  - Includes the new router.
  - Extends snapshot/export/import payloads to include map objects.
- Modify `gardenops/models.py`
  - Adds strict import/export models for map objects and nested units.
- Modify `gardenops/admin_edge_policy.py`
  - Adds generic API route exceptions and edge location rules for garden-scoped map-object CRUD.
- Modify `tests/test_admin_edge_policy.py`
  - Verifies the new garden-scoped map-object routes are classified as generic API, not platform-admin routes.
- Create `tests/test_map_objects.py`
  - Backend API and import/export tests.
- Modify `tests/test_integrity.py`
  - Verifies schema signature coverage for the new tables.
- Modify `tests/test_strict_request_models.py`
  - Verifies strict request model behavior for map objects and nested units.
- Modify `frontend/src/core/models.ts`
  - Adds `MapObject`, `MapObjectUnit`, geometry, and draft shape types.
- Modify `frontend/src/services/api.ts`
  - Adds typed map-object API functions and import/export payload types.
- Modify `frontend/src/components/mapView.ts`
  - Renders map-object overlays aligned to the existing grid and emits object click events.
- Create `frontend/src/components/mapObjects.ts`
  - Renders the layer controls, create form, object list, and nested layout editor.
- Modify `frontend/src/components/layout.ts`
  - Adds the map-objects layer section in the existing map layers panel.
- Modify `frontend/src/app.ts`
  - Loads map objects, wires create/update/delete, opens nested layout, and refreshes overlays.
- Modify `frontend/src/style.css`
  - Styles map-object overlays, nested layout panel, unit shapes, and mobile states.
- Modify `README.md`
  - Adds a concise feature bullet for map objects and nested layouts.

## Data Model

`garden_map_objects`

- `id bigint generated by default as identity primary key`
- `public_id text unique not null`
- `garden_id bigint not null references gardens(id) on delete cascade`
- `object_type text not null`
- `name text not null`
- `shape_type text not null`
- `geometry_json text not null`
- `style_json text not null`
- `z_index bigint not null default 0`
- `has_internal_layout bigint not null default 0`
- `internal_layout_json text not null default '{}'`
- `created_by_user_id bigint references auth_users(id) on delete set null`
- `created_at_ms bigint not null`
- `updated_at_ms bigint not null`

`garden_map_object_units`

- `id bigint generated by default as identity primary key`
- `public_id text unique not null`
- `garden_id bigint not null references gardens(id) on delete cascade`
- `map_object_id bigint not null references garden_map_objects(id) on delete cascade`
- `unit_type text not null`
- `name text not null`
- `shape_type text not null`
- `geometry_json text not null`
- `style_json text not null`
- `sort_order bigint not null default 0`
- `created_at_ms bigint not null`
- `updated_at_ms bigint not null`

The unit table must also enforce that `garden_id` matches the parent object. Add `UNIQUE (id, garden_id)` on `garden_map_objects`, then add a composite foreign key from `garden_map_object_units (map_object_id, garden_id)` to `garden_map_objects (id, garden_id)`. This prevents a malicious or buggy write from linking a unit to an object in another garden while claiming a different `garden_id`.

Geometry payloads are strict objects:

```json
{
  "x": 1,
  "y": 1,
  "width": 4,
  "height": 3
}
```

For top-level objects, `x` and `y` are 1-based garden-grid coordinates and `width`/`height` are measured in grid cells. For nested units, `x`, `y`, `width`, and `height` are measured in a local coordinate system whose dimensions come from the parent object's `internal_layout_json`.

Style payloads are strict objects:

```json
{
  "color": "#7d9f7a"
}
```

Only safe hex colors are accepted. Unknown keys are rejected.

Internal layout payloads are strict objects:

```json
{
  "rows": 6,
  "cols": 8
}
```

`rows` and `cols` must both be integers from 1 through 100. If `has_internal_layout` is true and the client omits an internal layout, default to `{"rows": 6, "cols": 8}`. If `has_internal_layout` is false, the API may retain the layout values but must reject attempts to create nested units under that object.

## API Contract

- `GET /api/gardens/{garden_id}/map-objects`
  - Returns all objects for the garden, each with nested `units`.
  - Requires membership in the garden.
- `POST /api/gardens/{garden_id}/map-objects`
  - Creates a map object.
  - Requires editor membership.
  - Body includes `object_type`, `name`, `shape_type`, `geometry`, `style`, `z_index`, `has_internal_layout`, and `internal_layout`.
  - Maximum accepted object count per garden after creation: 200.
- `PATCH /api/gardens/{garden_id}/map-objects/{object_public_id}`
  - Updates object metadata, geometry, style, and internal layout flag.
  - Requires editor membership.
- `DELETE /api/gardens/{garden_id}/map-objects/{object_public_id}`
  - Deletes the object and nested units.
  - Requires editor membership.
- `POST /api/gardens/{garden_id}/map-objects/{object_public_id}/units`
  - Creates a nested planting unit inside the object.
  - Requires editor membership and parent object in same garden.
  - Maximum accepted unit count per object after creation: 100.
- `PATCH /api/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}`
  - Updates nested planting unit metadata, geometry, style, and sort order.
  - Requires editor membership.
- `DELETE /api/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}`
  - Deletes one nested planting unit.
  - Requires editor membership.

## Task 1: Backend Failing Tests For Map Object CRUD

**Files:**
- Create: `tests/test_map_objects.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove:

- A garden editor can create a patio map object.
- A viewer can list but cannot create map objects.
- Invalid geometry outside the garden grid is rejected.
- A nested pot can be created inside a patio.
- A nested unit outside the local layout is rejected.
- Deleting a patio deletes its nested units.
- A unit cannot be created under a map object from another garden.
- Creating the 201st map object or the 101st unit under one object is rejected.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_map_objects.py -q
```

Expected: fail because `gardenops.routers.map_objects` and routes do not exist.

## Task 2: Backend Migration, Schema Signature, And Router

**Files:**
- Create: `migrations/0018_map_objects_nested_layouts.sql`
- Create: `gardenops/routers/map_objects.py`
- Modify: `gardenops/main.py`
- Modify: `gardenops/schema_signature.py`
- Modify: `gardenops/admin_edge_policy.py`

- [ ] **Step 1: Add migration**

Create the two new tables, indexes, and constraints listed in the Data Model section.

- [ ] **Step 2: Add router models and validation**

Implement strict request models with:

- Bounded names: 1-120 characters.
- Allowed object/unit/shape types.
- Geometry bounds: `x >= 1`, `y >= 1`, `width >= 1`, `height >= 1`.
- Top-level geometry must fit inside the garden `grid_rows` and `grid_cols`.
- Nested unit geometry must fit inside parent `internal_layout.columns` and `internal_layout.rows`.
- Style color must match safe hex format.
- User-controlled labels must be stored as plain text and emitted as JSON strings only.

- [ ] **Step 3: Add CRUD endpoints**

Implement garden-scoped CRUD endpoints with `_require_membership_editor` for mutations and membership read access for list.

- [ ] **Step 4: Wire router and edge policy**

Include the router in `main.py`. Add `admin_edge_policy.py` generic API exceptions and location rules for `/api/gardens/{garden_id}/map-objects`.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_map_objects.py tests/test_integrity.py::MigrationGuardTests::test_run_migrations_idempotent tests/test_integrity.py::MigrationGuardTests::test_complete_bootstrap_signature_can_be_stamped -q
```

Expected: pass.

## Task 3: Import/Export And Strict Model Tests

**Files:**
- Modify: `gardenops/models.py`
- Modify: `gardenops/main.py`
- Modify: `tests/test_map_objects.py`
- Modify: `tests/test_strict_request_models.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove:

- `GET /api/plots/export` includes `map_objects`.
- `POST /api/plots/import` restores `map_objects` and nested units.
- Snapshot restore restores `map_objects`.
- Map object import rejects extra fields.
- Map object import rejects more than 200 objects or more than 500 units total.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_map_objects.py tests/test_strict_request_models.py -q
```

Expected: import/export tests fail until models and snapshot functions include map objects.

- [ ] **Step 3: Implement import/export**

Extend `snapshot_layout`, `parse_layout_payload`, and `restore_snapshot_data` to round-trip map objects and units. When the import payload includes `map_objects`, clear existing map objects after plot replacement starts and before inserting imported objects; when legacy payloads omit the key, preserve existing map objects. Export should include `public_id`, but import must regenerate an object's or unit's public ID if that exported ID already belongs to another garden. The unit import must map old object IDs to the inserted parent object rows instead of trusting raw numeric IDs from the file.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_map_objects.py tests/test_strict_request_models.py -q
```

Expected: pass.

## Task 4: Frontend Types And API

**Files:**
- Modify: `frontend/src/core/models.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add TypeScript types**

Add `MapObjectShapeType`, `MapObjectType`, `MapObjectGeometry`, `MapObjectStyle`, `MapObjectInternalLayout`, `MapObjectUnit`, and `MapObject`.

- [ ] **Step 2: Add API functions**

Add typed functions for list/create/update/delete object and list/create/update/delete nested unit operations.

- [ ] **Step 3: Run typecheck**

Run:

```bash
cd frontend && npm run typecheck
```

Expected: pass after UI code is added in later tasks; at this stage unused exports are acceptable.

## Task 5: Map Overlay Renderer

**Files:**
- Modify: `frontend/src/components/mapView.ts`
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add failing static/UI contract check**

Add a static contract test under `tests/test_frontend_security_static.py` or a small Node script that confirms `renderMapGrid` accepts `mapObjects` and renders `.map-object-overlay` elements without replacing plot cells. The check must also assert that the new map-object component does not use `innerHTML` for user-controlled names.

- [ ] **Step 2: Implement overlay render**

Render map objects after plot cells and before zoom controls. Use CSS grid placement for the object bounding box and inner SVG/HTML for rectangle or ellipse shape. The overlay container uses `pointer-events: none`; only the object label/action button uses `pointer-events: auto`, so existing plot click and drag behavior remains intact.

- [ ] **Step 3: Verify frontend build**

Run:

```bash
cd frontend && npm run typecheck
cd frontend && npm run build
```

Expected: pass.

## Task 6: Layer Panel And Nested Layout UI

**Files:**
- Create: `frontend/src/components/mapObjects.ts`
- Modify: `frontend/src/components/layout.ts`
- Modify: `frontend/src/app.ts`
- Modify: `frontend/src/core/i18n.ts`
- Modify: `frontend/src/style.css`

- [ ] **Step 1: Add map-object layer controls**

Add a new section in the existing map layers panel with:

- Object visibility toggle.
- Object list.
- Create patio/terrace action visible in edit mode.
- Open nested layout action for objects with internal layouts.

- [ ] **Step 2: Add create flow**

When edit mode is on and plots are selected, the user can create a patio from the selected rectangle. The geometry uses the selected plots' bounding box. If no plots are selected, use a small default geometry near row 1/col 1.

- [ ] **Step 3: Add nested layout panel**

Opening a patio shows a focused panel with a local grid and actions to add a pot or planter. Units render as rectangle or ellipse shapes. The panel uses terse labels such as `Layout only` and does not show plant/task assignment actions, so the UI does not imply unsupported behavior.

- [ ] **Step 4: Persist and reload**

Wire create/delete object and create/delete unit actions through the API, refresh state, and re-render overlays and the nested layout.

- [ ] **Step 5: Verify frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: pass.

## Task 7: Real User E2E Validation

**Files:**
- May create: `scripts/e2e_map_objects_smoke.cjs`

- [ ] **Step 1: Start a local backend/frontend test server**

Use a disposable test database. Start the backend with auth disabled for local smoke validation, then serve the frontend build.

- [ ] **Step 2: Exercise the user flow**

As a real user:

1. Open the app.
2. Go to Map.
3. Enter edit mode.
4. Select several adjacent ground plots.
5. Create a patio map object from the selection.
6. Open the patio nested layout.
7. Add an oval pot and a rectangular planter.
8. Reload the page.
9. Verify the patio and nested units still appear.
10. Delete the patio and verify the units disappear.

- [ ] **Step 3: Capture evidence**

Use Playwright or a deterministic browser smoke script. Record console errors, network failures, and screenshots if the browser tooling is available.

## Simulated User Experience Review

Run this review before implementation:

1. User starts on the Map tab and sees the existing layer panel. The new map-object section must sit below zones/highlight/elevation and must not hide existing controls.
2. User enters edit mode. The section exposes a concise `Create patio` action. If plots are selected, the action uses the selected rectangle. If not, it creates a small default object and immediately selects it.
3. User clicks the patio label on the map or the object row in the panel. The nested layout opens without navigating away from the Map tab.
4. User adds an oval pot and a rectangular planter. They appear in the local grid immediately and persist after reload.
5. User returns to the main map. Plot selection, plot drag, house drag, zone toggles, elevation, and category highlighting still work.
6. User deletes the patio. The object and nested units disappear from the main map and object list.

Review finding to address: the nested layout cannot be hidden behind desktop-only controls. The object list and nested panel must be reachable from the existing mobile map layer sheet as well as desktop.

## Adversarial Review Findings And Plan Revisions

**Critical**

- Nested units can become cross-garden corrupt data unless the database enforces parent-object garden consistency. Revision: add the composite parent FK described in the Data Model section and test cross-garden creation attempts.
- Import/export can collide on globally unique `public_id` values. Revision: preserve public IDs only when safe; regenerate on cross-garden conflict and map imported child units to newly inserted parent rows.

**Important**

- The UI could mislead users into thinking nested units already support plant/task assignment. Revision: avoid "Add plant" or task affordances in the nested panel and use terse `Layout only` status text.
- Map-object overlays can break plot selection and dragging if they capture pointer events. Revision: use pointer-events only on labels/actions, not on the overlay fill.
- User-controlled labels are XSS-sensitive. Revision: render all map-object and unit names with `textContent`, never `innerHTML`, and add a static check.
- Mobile could lose access to the nested layout if it only appears in a desktop side panel. Revision: wire the nested layout to the existing mobile map sheet too.

**Rejected**

- Blocking the whole feature until generic `planting_locations` exists is too conservative. This slice is safe if nested units remain layout objects and the app does not count them in plots, reports, alerts, or tasks.

## Task 8: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Possibly modify: `docs/shademap.md` only if shade semantics are mentioned.

- [ ] **Step 1: Update docs**

Document the new map-object/nested-layout feature briefly and explicitly state that nested units are layout objects in this first slice, not plant/task targets.

- [ ] **Step 2: Run docs impact inventory**

Run:

```bash
python .codex/skills/gardenops-documentation-upkeep/scripts/docs_impact_inventory.py
```

- [ ] **Step 3: Run full verification**

Run:

```bash
uv run pytest tests/test_map_objects.py tests/test_integrity.py tests/test_strict_request_models.py -q
cd frontend && npm run build
```

If time allows and the test database is available:

```bash
uv run pytest tests/ -q
```

## Adversarial Review Checklist

Run this before implementation and after the first implementation pass:

- Backend/data correctness: map objects must be garden-scoped, imported safely, deleted with units, and not mixed into `plots`.
- Security: editor-only mutations, viewer read-only access, strict request models, bounded JSON sizes, safe colors, no HTML injection, no cross-garden IDs.
- Frontend/UI: map object clicks must not break plot clicks, nested layout must be discoverable, mobile layer panel must not overflow, and labels must not hide plot contents.
- Use cases: a user can create a patio from a selected area, add pots inside it, reload, return to the patio, and delete it.
- Non-goals: plant/task assignment to nested units must not be implied as available.
