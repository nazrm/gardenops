# Canonical Container Plots Implementation Plan

**Goal:** Replace layout-only map units with useful pots and planters that can contain plants, while keeping patios, terraces, greenhouses, and similar areas simple and map-first.

**Architecture:** Keep `garden_map_objects` as non-plantable mapped areas. Make each pot, planter, or raised bed a canonical `plots` row with an optional area parent. Continue using `plot_plants` and the existing plot-linked task, journal, issue, harvest, calendar, attention, and media relationships. Do not add a generic location model, a second assignment table, recursive nesting, stored container layout geometry, or offline move machinery.

**Primary user journey:** Add area -> add container -> place or move plants into the container -> open the container like any other plant location.

**Tech stack:** PostgreSQL migrations, FastAPI/Pydantic, vanilla TypeScript, Vite, pytest, and Playwright against the real backend.

---

## Lean Scope

Implement only the behavior required to make areas and containers useful:

- Areas: `patio`, `terrace`, `greenhouse`, `balcony`, and `other`.
- Containers: `pot`, `planter`, `raised_bed`, and `other`.
- An area contains zero or more containers and displays aggregate container and plant counts.
- A container can exist without an area.
- A plant can be placed in or moved between ordinary plots and containers.
- Moving supports existing multi-home records and quantity greater than one.
- The normal UI uses Area, Pot, Planter, Plants here, Place, and Move.
- Area geometry remains editable with the existing map interaction.
- Containers use deterministic automatic marker placement inside an area. No stored mini-grid.
- Container mutations and plant moves remain online-only.

Explicit non-goals:

- No recursive containers or arbitrary location trees.
- No generic entity, capability, or polymorphic reference framework.
- No second plant-assignment table.
- No direct plant assignment to patios or terraces.
- No shelves, paths, ponds, sheds, decorative units, or per-container soil/capacity model in this slice.
- No plant drag-and-drop requirement. The accessible picker is the canonical move flow.
- No offline move queue, undo event system, automatic task retargeting, or new journal event type.
- No broad renaming of every existing `plot_id` API or database table.

## Product Model

### User vocabulary

- **Area:** Patio, terrace, greenhouse, balcony, or another mapped surface.
- **Container:** Pot, planter, raised bed, or another plantable item.
- **Plants here:** Plants assigned to one container.
- **Where it grows:** The plant's current ordinary plots and containers.

Do not show Object, Unit, Internal layout, Row, Column, Width, Height, or raw generated container IDs in normal workflows.

### Canonical ownership

- `garden_map_objects` owns area identity and map geometry.
- `plots` owns container identity, name, type, parent, environment, and lifecycle.
- `plot_plants` remains the only current plant-placement table.
- A container has one generated immutable `plot_id`; its editable label is `display_name`.
- Historical records keep their existing stable `plot_id` references when plants move or containers are reparented.

### Hierarchy

```text
Garden
|- ordinary plots
|- standalone containers
`- areas
   `- containers
      `- plot_plants assignments
```

The database must prevent cross-garden parentage. There is no third level.

## Data Changes

Create migration `0031_canonical_container_plots.sql` with additive changes only:

- Add `plots.plot_kind`, constrained to `ground`, `indoor`, or `container`.
- Add `plots.display_name`.
- Add `plots.container_type`, constrained to `pot`, `planter`, `raised_bed`, or `other` when `plot_kind='container'`.
- Add nullable `plots.parent_map_object_id`.
- Add `plots.environment`, constrained to `outdoor`, `covered`, or `indoor`.
- Add nullable `plots.archived_at_ms`.
- Add a same-garden composite foreign key from `(parent_map_object_id, garden_id)` to `garden_map_objects(id, garden_id)` with `ON DELETE SET NULL (parent_map_object_id)`, leaving `garden_id` intact.
- Add an index for active containers by garden and parent.
- Backfill existing plots: `indoor` when `zone_code='I'`, otherwise `ground`; set matching environment. Ordinary plots may leave `display_name` null and continue displaying `plot_id`.
- Translate any existing `garden_map_object_units` rows into container plots in the migration, preserving names and parent areas. Production currently has zero rows, but the migration must remain correct for development and imported databases.

Container inserts populate required legacy columns with `zone_code='C'`,
`zone_name='Containers'`, `plot_number=0`, and null grid coordinates. They also
receive a valid `plot_ownership` row used for existing garden scoping. New
columns use old-release-compatible defaults: `plot_kind='ground'` and
`environment='outdoor'`. Containers require a non-empty `display_name`.

Do not drop `garden_map_object_units` in this release. Stop using it at runtime and mark it legacy. This avoids a destructive deployment migration while ensuring there is only one writable source after activation.

Required subtype checks:

- Containers require `container_type`, null `grid_row/grid_col`, and a non-empty display name.
- Ground and indoor plots cannot carry a parent or container type.
- Archived containers cannot receive new assignments.

Update `schema_signature.py` for the new columns, constraints, and index. Retain the legacy unit table signature until a later cleanup migration.

## Backend Contract

### Area endpoints

Keep existing map-object list/create/update endpoints, but limit new UI creation to the scoped area types. Responses include:

- `container_count`
- `plant_count`
- `containers`, loaded from canonical container plots rather than `garden_map_object_units`

Area deletion must unparent its containers and then delete the area. It must never delete a container, plant assignment, or historical record.

### Container endpoints

Add under the existing garden-scoped map router:

- `POST /api/gardens/{garden_id}/containers`
- `PATCH /api/gardens/{garden_id}/containers/{plot_id}`
- `DELETE /api/gardens/{garden_id}/containers/{plot_id}`

Create accepts `name`, `container_type`, and optional `parent_object_public_id`. Defaults environment from the parent: greenhouse -> covered; other supported areas -> outdoor; standalone -> outdoor. It atomically creates the plot and required ownership record.

Patch supports name, type, parent, and environment. Reparenting preserves `plot_id`, assignments, and history.

Delete behavior:

- Return `409` with assignment quantity and affected-plant counts while live plants remain.
- Otherwise archive the container and unparent it. Do not call destructive plot-reference cleanup.
- Archived containers are absent from normal selectors and area summaries but remain resolvable for historical records.

Existing ordinary plot update, batch-position, and destructive delete routes
must reject `plot_kind='container'`. Archived containers are filtered from
`/api/plots`, ordinary selectors, summaries, and all assignment writes. Direct
historical references remain resolvable by stable `plot_id`.

Legacy `/units` mutation routes return `410 Gone` and never write
`garden_map_object_units`. Legacy schema-v1 imports translate units into
container plots at the boundary.

### Permissions

- Any garden member may read a container and its garden-scoped plant list.
- Garden editors/admins may create, rename, and reparent containers.
- Assignment and movement additionally require access to the plant and to any ordinary source or destination plot involved.
- Only garden admins can archive containers in this slice.
- Ordinary plot ownership behavior remains unchanged.
- Responses expose only the minimal per-plant mutation capability needed to hide unauthorized Move and Place controls.

Centralize only the small checks needed to distinguish shared containers from ordinary owner-controlled plots. Do not introduce a general authorization framework.

### Move semantics

Extend the existing plot-to-plot move operation instead of adding a new location service:

- Accept optional integer `quantity`; omitted means the complete source assignment.
- Lock the involved `plots` rows in `plot_id` order before state checks, then lock the source assignment before quantity validation. Destination upsert must handle an initially absent assignment row.
- Reject missing source, invalid quantity, archived destination, cross-garden movement, or unauthorized source/destination.
- For partial moves, decrement the source. For full moves, remove it.
- Merge quantity into an existing destination assignment.
- Preserve destination observation metadata on merge.
- When creating a destination assignment, copy `seen_growing` and `seen_growing_date` from the source.
- Keep `room_label` only when the destination is an indoor ordinary plot; otherwise clear it.
- A same-source/destination request is a no-op.

Archive locks the container plot row before checking assignment counts.
Assignment and move paths lock a container destination row and reject it when
`archived_at_ms` is non-null. This prevents an assignment from racing with
archive.

Do not retarget historical tasks, journals, issues, harvests, or calendar records. Future generated work continues to derive from current `plot_plants` assignments.

### Plot classification

Replace behavior that infers indoor/outdoor solely from null grid coordinates in paths that containers can reach. Use `plot_kind` and `environment` for:

- assignment validation and room labels;
- weather and watering eligibility;
- map focus and labels;
- plot selectors and summaries;
- statistics, planner capacity, and ground-area reports.

Ground-only calculations exclude containers. Operational plant/task queries continue to include them through existing `plot_id` relationships.
Existing ShadeMap coordinate filters should continue to exclude containers;
retain one regression assertion rather than expanding that subsystem.

## Frontend Experience

### Areas panel

Replace the permanent Objects form with a compact **Areas & containers** panel:

- Header with show/hide toggle using `aria-pressed`.
- One **Add area** action.
- Collapsed creation form asks only type and name; footprint comes from selected cells or a sensible default.
- Area rows show type, container count, and plant count.
- Selecting an area opens its containers and primary actions.
- Geometry and appearance are placed under a collapsed **Edit layout** section.

### Containers

- Selected area provides **Add pot or planter**.
- The top-level panel also provides **Add standalone container** for pots that do not belong to an area.
- Creation asks type and name only.
- Container rows show name, type, plant count, and environment label only when it differs from the parent default.
- Containers render as automatically arranged, focusable markers inside the selected area overlay.
- Selecting a marker or row opens the existing plot drawer for that container.
- Standalone containers appear in a small **Standalone containers** section and remain valid move destinations.
- Do not expose mini-grid rows/columns, shape, color, sort order, or shelf conversion.

### Place and Move

- Add a visible **Move** command to plant cards/location rows; do not require dragging.
- The dialog/sheet identifies the selected source home.
- Destinations are searchable and grouped into ordinary plots, each area with containers, and standalone containers.
- If source quantity is one, confirm directly.
- If source quantity is greater than one, show a bounded quantity input defaulting to the full quantity.
- Show the destination merge result before confirmation when that plant already exists there.
- Use **Place** for unassigned plants or adding another home; do not call that Move.
- The plant list/detail surface provides the concrete **Place** entry for unassigned plants, using the same destination picker without a source step.
- On success, refresh source, destination, map counts, and plant details; announce the result through the existing status/live-region mechanism.

### Accessibility and responsive behavior

- Minimum 44px interactive targets in this panel and move dialog.
- Visible focus and non-color selection state.
- Keyboard-operable creation, selection, destination search, quantity, confirmation, and cancellation.
- Dialog focus returns to the invoking Move or Place control.
- Mobile uses the existing bottom-sheet/dialog patterns and never depends on map drag gestures.
- Verify at 390x844, desktop width, and 200% zoom without overlap or clipped labels.

## Import And Export

- Bump the layout export schema to v2.
- Export canonical container plot fields once, with parent area public IDs rather than internal database IDs.
- Import areas first, resolve parent public IDs, then import containers and assignments.
- Accept schema-v1 `map_objects[].units` and translate each unit into a canonical container plot.
- Layout export does not contain plant assignments.
- Validate imported IDs and each container's single optional area reference before writing.
- Upsert imported containers and never pass existing containers through destructive ordinary-plot replacement.
- Leave omitted containers intact; if their imported area is absent, leave them standalone.
- Existing ordinary-plot replacement behavior remains unchanged.
- Translate schema-v1 units at the import boundary, mapping `shelf` to `other`.
- Treat schema-v2 container ID conflicts as transactional failures.
- Keep rollback transactional.

## Implementation Tasks

### Task 1: Migration and schema contract

Files: `migrations/0031_canonical_container_plots.sql`, `gardenops/schema_signature.py`, migration/integrity tests.

- Add columns, checks, FK, index, backfill, and legacy-unit translation.
- Add database tests for subtype constraints, same-garden parents, and preserved row/quantity counts.
- Verify migration from a fixture containing legacy units.

### Task 2: Canonical container backend

Files: `gardenops/routers/map_objects.py`, focused backend tests.

- Serialize containers from `plots` with plant counts.
- Implement create, patch/reparent, archive, permissions, and area-delete unparenting.
- Retire runtime writes to `garden_map_object_units`.
- Add tests for editor collaboration, viewer denial, admin archive, cross-garden rejection, and occupied archive blocking.

### Task 3: Safe quantity-aware moves and plot classification

Files: `gardenops/routers/plots.py`, the smallest affected services, focused tests.

- Add row locking and optional partial quantity.
- Preserve observation metadata and handle destination merges.
- Add container-aware authorization and archived-destination checks.
- Replace relevant grid-null indoor/outdoor assumptions with explicit classification.
- Reject container use through ordinary plot update, batch-position, and destructive delete routes.
- Add regression tests for ordinary plots, indoor plots, containers, multi-home plants, and quantities.

### Task 4: Export/import compatibility

Files: `gardenops/models.py`, `gardenops/main.py`, `gardenops/routers/map_objects.py`, export/import tests.

- Add schema-v2 container payloads and parent public-ID resolution; do not add assignments to layout exports.
- Translate schema-v1 units without maintaining dual runtime state.
- Upsert imported containers, preserve omitted containers, and ensure restore does not cascade-delete container history.

### Task 5: Frontend types, API, and state wiring

Files: `frontend/src/core/models.ts`, `frontend/src/services/api.ts`, `frontend/src/app.ts`.

- Replace runtime MapObjectUnit use with canonical container summaries.
- Add typed container CRUD and quantity-aware move requests.
- Open container markers in the existing plot-detail flow.
- Refresh all affected state after mutations.

### Task 6: Lean responsive UI

Files: `frontend/src/components/mapObjects.ts`, `frontend/src/components/mapView.ts`, relevant plant card/dialog components, `frontend/src/style.css`, `frontend/src/core/i18n.ts`.

- Replace permanent technical forms with progressive Add area and Add container flows.
- Render container counts, lists, and automatic map markers.
- Add accessible Place/Move picker with conditional quantity input.
- Remove runtime mini-grid/editor UI.
- Keep English and Norwegian strings aligned.

### Task 7: End-to-end journey and documentation

Files: focused Playwright journey, static contracts where valuable, `docs/map-objects.md`, and the smallest relevant README/help text.

Real-backend Playwright must prove three focused journeys:

1. Desktop creates an area and container, places an unassigned plant, partially moves and merges quantity, reloads, and verifies names and counts.
2. Mobile at 390x844 creates a standalone pot and moves a plant without dragging.
3. Keyboard at 200% zoom completes Place and Move, restores focus, announces success, and keeps viewer mutation controls absent.

Backend integration tests, rather than separate browser journeys, cover editor
collaboration, archive integrity, area deletion, and v1/v2 import behavior.

Update docs to describe the user model and explicitly remove the old layout-only limitation.

## Validation Gates

Run focused checks after each delegated task, then the integrated set once:

```bash
UV_CACHE_DIR=/tmp/gardenops-uv-cache uv run pytest \
  tests/test_map_objects.py tests/test_plots.py tests/test_export_import.py tests/test_integrity.py -q
cd frontend && npm run typecheck && npm run build
```

Run the focused real-backend Playwright journey at desktop and mobile dimensions.

Run a focused two-connection backend test proving that archive and assignment
cannot race. Rehearse migration against a production-shaped restored database,
then use the normal backup, maintenance activation, integrity, health, and log
checks for release. The migration must remain compatible while the old service
is online before activation; old code sees defaults and the legacy unit table.

Before the final review, verify database invariants against a restored production-shaped snapshot:

- Existing plot count unchanged except intentionally created test containers.
- Existing assignment row count and total quantity unchanged by migration.
- Multi-home assignments remain multi-home.
- Unassigned plant count remains unchanged.
- No active container lacks a valid garden and required ownership.
- No container parent crosses gardens.

The final Sol 5.6 ultra review is one review of the complete implementation, not a review after each file. It must focus on correctness, the actual end-to-end journey, data safety, accessibility, and unnecessary complexity. It must not request speculative abstractions, broad refactors, or unrelated test expansion.

## Completion Criteria

- A user can create an area and container with only intent-level fields.
- A user can place or move a specific quantity of a plant into that container on desktop and mobile.
- Containers participate in existing plot-linked operational features without a parallel assignment model.
- Area removal cannot remove containers or plants.
- Container removal cannot silently remove assignments or history.
- Existing ground and indoor behavior remains correct.
- Import/export round-trips the new model and reads legacy unit snapshots.
- Focused backend, frontend, and Playwright validation passes.
- The final implementation review has no unresolved blocking or high-severity findings.
