# Complete Journey Verification And Optimization Implementation Plan

> **For agentic workers:** REQUIRED EXECUTION MODE: implement this plan one phase at a time on sequential branches from the latest `origin/main`. Do not combine phases into one pull request. Every phase has a blocking independent GPT-5.6 Sol Ultra validation gate after implementation.

**Goal:** Close every open journey, role, offline, provider, persistence, accessibility, and performance gap in the GardenOps optimization map, while fixing defects found by the new evidence and preserving the map-first product direction.

**Architecture:** Build one hardened, reusable real-backend Playwright journey harness around the existing disposable PostgreSQL runner. Add focused journey modules in dependency order, with deterministic external-provider adapters, explicit database/filesystem postconditions, and a tracked coverage contract. Keep raw screenshots, traces, timings, and reviewer work products under the existing gitignored `research/` tree. Keep durable tests, manifest schemas, commands, and concise validation results in tracked repository files.

**Tech stack:** FastAPI, PostgreSQL 17 through the existing DB wrapper, vanilla TypeScript, Vite, Playwright Core with system Chromium, pytest, Ruff, shell runners, deterministic loopback provider fixtures, and the existing GardenOps production build and CI gates.

---

## How To Execute This Document

Do not begin at Phase 0 without reading the program-wide appendices at the end of this file. They define the non-negotiable outcomes, journey ownership, branch/PR rules, disposable environment, browser/role/network matrix, evidence contract, global validation commands, and exact GPT-5.6 Sol Ultra review protocol.

Execution order:

1. Read Appendices A-H and the Final Program Definition of Done.
2. Implement exactly one numbered phase.
3. Follow the Per-Phase Execution Checklist.
4. Pass local focused and cumulative validation.
5. Pass the independent GPT-5.6 Sol Ultra gate for the final phase SHA.
6. Publish one phase PR and wait for merge before starting the next phase.

The numbered phases contain phase-specific work. The appendices override a phase if an instruction appears ambiguous.

## Phase 0: Evidence Contract And Hardened Journey Harness

**Purpose:** Create the common safety, fixture, browser, database, and evidence infrastructure that every later phase uses. This phase must not change production feature behavior.

**Journey scope:** All IDs for manifest completeness; executable smoke proof for `A1`, `M1`, and `CROSS-01` only.

**Files:**

- Create: `tests/journey_coverage.yaml`
- Create: `scripts/check_journey_coverage.py`
- Create: `tests/test_journey_coverage_manifest.py`
- Create: `scripts/run_complete_journeys_e2e.sh`
- Create: `scripts/seed_complete_journeys_e2e.py`
- Create: `scripts/check_complete_journeys_e2e.cjs`
- Create: `scripts/e2e/completeJourneyBrowser.cjs`
- Create: `scripts/e2e/completeJourneyAssertions.cjs`
- Create: `scripts/e2e/completeJourneyApi.cjs`
- Create: `scripts/e2e/journeys/foundation.cjs`
- Create: `tests/test_complete_journey_e2e_scripts.py`
- Create: `docs/testing/journey-validation-ledger.md`
- Modify: `docs/development.md`
- Modify: `README.md` only to link to the canonical development command if needed

### Task 0.1: Freeze the journey inventory

- [x] Parse `research/optimization-map/journey-matrix.csv` and `test-coverage-matrix.csv` with Python's `csv` module.
- [x] Copy every stable journey ID and its owning phase into `tests/journey_coverage.yaml`; do not copy private paths or raw evidence.
- [x] Add a test with the expected ID set so accidental row deletion fails loudly.
- [x] Add duplicate-ID, unknown-status, missing-reason, and nonexistent-evidence-path test cases.
- [x] Implement `--allow-open` and `--require-closed` modes. Only Phase 9 may use `--require-closed`.

Run RED before implementing the checker:

```bash
.venv/bin/python -m pytest tests/test_journey_coverage_manifest.py -q
```

Expected RED: checker/module or manifest does not exist. Implement, rerun, and require GREEN.

### Task 0.2: Implement the safe runner shell

- [x] Copy safety semantics, not raw code blindly, from `run_ui_flow_map_e2e.sh` and `run_optimization_journeys_e2e.sh`.
- [x] Support `--phase <N>`, `--through-phase <N>`, and internal `--child` modes.
- [x] Reject unknown/duplicate arguments and phase values outside `0..9`.
- [x] In parent mode, allocate a unique ignored artifact directory and invoke `run_fast_postgres_tests.py --command`.
- [x] In child mode, validate disposable URL, marker, system identifier, parent process, exact test environment, and non-production port.
- [x] Scrub inherited secrets, proxy variables, `BASH_ENV`, `ENV`, `NODE_OPTIONS`, `NODE_PATH`, `PYTHONPATH`, cloud credentials, provider keys, and live media/terrain paths.
- [x] Allocate backend, frontend, and deterministic-provider ports on loopback; reject collisions and port 5432.
- [x] Create private `logs`, `media`, `terrain`, `downloads`, and `artifacts` directories with mode `0700`.
- [x] Start backend and Vite in process groups, wait for readiness, and terminate process groups with TERM then bounded KILL fallback.
- [x] Preserve artifacts and log tails on failure; remove the private temporary runner root on success.
- [x] Never run `rm -rf` unless the resolved target matches the runner-created private prefix or validated ignored run directory.

Add static/behavior tests for every rejection and cleanup path. Include child-order tests proving unsafe validation fails before any database, Vite, or browser child is started.

### Task 0.3: Implement shared browser and evidence helpers

- [x] `completeJourneyBrowser.cjs` owns browser launch, named profiles, context creation, non-loopback HTTP/HTTPS/WebSocket aborts, console/page-error collection, trace lifecycle, and authenticated sign-in helpers.
- [x] `completeJourneyAssertions.cjs` owns duplicate-ID, horizontal-overflow, visible-control-name, focus, loading-state, no-browser-error, and eventual-state helpers.
- [x] `completeJourneyApi.cjs` owns CSRF-aware API reads used only for setup verification and post-browser evidence; user actions still go through the UI.
- [x] Do not put feature-specific selectors or business assertions in shared modules.
- [x] Write the evidence manifest atomically: temporary file, fsync/close, then rename.
- [x] Include failed assertions and browser errors even when a journey throws.
- [x] Prevent credentials, cookies, authorization headers, raw provider payloads, media bytes, and database URLs from entering the manifest.

### Task 0.4: Seed and run foundation smoke journeys

Seed:

- one admin, one editor, one viewer;
- two gardens with visibly distinct names, plots, plants, weather, and notifications;
- enough map data to prove Garden A/B isolation;
- no production-derived records.

Foundation browser proof must:

- sign in through the real auth form;
- load Map first and assert no unexpected `/api/plants` startup request;
- switch Garden A -> B -> A and assert headings/map/notifications never show stale cross-garden content;
- run desktop and true mobile contexts;
- assert zero external network attempts, browser errors, duplicate IDs, and horizontal overflow;
- query PostgreSQL after the run to prove the smoke journey made no unintended writes.

Run:

```bash
scripts/run_complete_journeys_e2e.sh --phase 0
.venv/bin/python -m pytest tests/test_complete_journey_e2e_scripts.py tests/test_journey_coverage_manifest.py -q
```

### Task 0.5: Document and validate

- [x] Document the command, safety model, artifact location, phase selection, and failure cleanup in `docs/development.md`.
- [x] Initialize the validation ledger with a schema and Phase 0 row.
- [x] Run all global gates.
- [ ] Run the mandatory GPT-5.6 Sol Ultra gate for Phase 0, focusing on test honesty and destructive-runner safety.

**Phase 0 acceptance:** The manifest checker passes; the foundation journey passes desktop/mobile against a disposable database; all rejection tests pass; production behavior files are unchanged; Sol Ultra disposition is PASS or PASS WITH DOCUMENTED LIMITATIONS.

---

## Phase 1: Garden Context, Map, Plants, And Layout

**Purpose:** Prove the map-first entry experience and the complete garden/layout/plant lifecycle, including true mobile mutation, role behavior, persistence, and cross-garden stale-response safety.

**Journey scope:** `A3`, `M1`, `M2`, `M3`, `M4`, `CROSS-01`.

**Primary files to inspect before editing:**

- `frontend/src/app.ts`
- `frontend/src/components/onboarding.ts`
- `frontend/src/components/plotInteractions.ts`
- `frontend/src/components/mapObjects.ts`
- `frontend/src/components/mapView.ts`
- `frontend/src/features/savedViewsFeature.ts`
- `frontend/src/features/snapshotsFeature.ts`
- `frontend/src/tabs/indoorTab.ts`
- `frontend/src/services/api.ts`
- `gardenops/routers/gardens.py`
- `gardenops/routers/plots.py`
- `gardenops/routers/plants.py`
- `gardenops/routers/map_objects.py`
- `gardenops/routers/exports.py` and current snapshot/import router
- related backend tests named in the journey map

**New phase files:**

- Create: `scripts/e2e/journeys/gardenMapPlants.cjs`
- Extend: `scripts/seed_complete_journeys_e2e.py`
- Extend: `tests/test_complete_journey_e2e_scripts.py`
- Add focused backend/static tests only where new defects or contracts are found

### Task 1.1: Add RED journey skeleton and fixtures

- [ ] Seed an empty first-login account, admin/editor/viewer members, Garden A, Garden B, a large named garden, indoor and outdoor plants, map objects, nested units, saved views, and snapshots.
- [ ] Use stable public IDs returned by seed helpers; do not assume database integer IDs.
- [ ] Add browser steps and initially fail each journey at an explicit `coverage not implemented` assertion.
- [ ] Add database postcondition queries before implementing any behavior fix.

### Task 1.2: Prove onboarding and garden lifecycle (`A3`)

Desktop and mobile must cover:

1. New user enters the actual onboarding UI.
2. Creates a garden with valid location/configuration.
3. Corrects at least one validation error without losing entered data.
4. Reaches Map, reloads, and sees the created garden.
5. Admin/editor updates supported garden settings; viewer sees read-only state and direct write denial.
6. Switches A/B/A during deliberately delayed plants, weather, plot-alert, notification, and lazy-subview responses.
7. Confirms stale responses are discarded and draft state is either scoped or intentionally cleared with visible feedback.

Database assertions: one intended garden, correct owner/membership, no duplicate settings, no Garden A records linked to Garden B.

### Task 1.3: Prove Map, plots, plants, indoor, search, and saved views (`M1`, `M3`)

- [ ] Map startup remains map-first and does not fetch the full plant catalogue before demand.
- [ ] Create, edit, move/assign, and delete or archive a plant using supported UI actions.
- [ ] Create/edit a plot or layout cell and link/unlink a plant.
- [ ] Exercise global plant search and open the correct plant/plot context.
- [ ] Create, apply, rename/update if supported, and delete a saved view; reload between create and apply.
- [ ] Create/edit indoor plant data, verify read-only affordances, and switch gardens while Indoor is loading.
- [ ] Submit one mobile quick action rather than merely opening its sheet.
- [ ] Assert map dimensions do not shift when badges, labels, loading states, or selected objects change.

Database assertions must cover plants, ownership, plots, assignments, indoor fields, saved view ownership, and exact delete/archive semantics.

### Task 1.4: Prove map objects and nested layouts (`M2`)

- [ ] Create each supported map-object category through the real editor.
- [ ] Move/resize or directly manipulate an object as supported.
- [ ] Add/edit/remove a nested planting unit.
- [ ] Reload and verify geometry, units, and active-garden scope.
- [ ] Run the mutation on true mobile, including panel open/close and focus return.
- [ ] Prove viewer controls are absent/disabled and direct API mutation is denied.
- [ ] Delete one object with confirmation and assert nested cascade plus exactly one expected audit event.

Do not expand nested units into task/plant targets unless that product behavior is already supported by the current map-object specification.

### Task 1.5: Prove snapshot/export/import/restore (`M4`)

- [ ] Save a layout snapshot, diverge the layout, restore, and assert one final render rather than intermediate churn.
- [ ] Verify retained assignments survive and absent plots/assignments are removed exactly as documented.
- [ ] Exercise the actual file picker/download path where the browser supports it.
- [ ] Reject malformed, wrong-version, oversized, cross-garden, and structurally incomplete imports without partial writes.
- [ ] Inject a disposable-database failure mid-restore in a backend test and prove transaction rollback.
- [ ] Confirm cancellation leaves the current layout unchanged.

### Task 1.6: Fix only evidence-backed defects

For each failure:

1. Add the narrowest backend or frontend regression test.
2. Identify the ownership boundary: UI state, API validation, transaction, garden scope, or persistence.
3. Fix at that boundary using existing patterns.
4. Rerun the single journey, then all Phase 1 journeys.
5. Record behavior changes in README or feature docs where user-visible.

### Task 1.7: Validate and review

Focused commands:

```bash
scripts/run_complete_journeys_e2e.sh --phase 1
.venv/bin/python -m pytest tests/test_gardens_endpoints.py tests/test_plants.py tests/test_plots.py tests/test_map_objects.py tests/test_export_import.py tests/test_saved_views.py tests/test_indoor_plants.py -q
```

Then run global gates and the mandatory GPT-5.6 Sol Ultra review. Reviewer emphasis: map-first usability, mobile manipulation, stale A/B/A state, destructive restore/delete correctness, and role affordances.

**Phase 1 acceptance:** Every scoped manifest dimension is `proven` or justified `not_applicable`; desktop/mobile writes persist; viewer cannot mutate; malformed restore is atomic; cross-garden delayed responses do not leak; cumulative Phase 0-1 E2E passes.

---

## Phase 2: Attention, Tasks, Calendar, Notifications, And Weather Work

**Purpose:** Prove that daily work is coherent across Today, Tasks, Calendar, plot context, Quick Actions, batch actions, notifications, weather automation, and durable garden history.

**Journey scope:** `D1`, `D2`, `D3`, `D4`, `D5`, `R1`.

**Primary files to inspect:**

- `frontend/src/components/attentionTodayPanel.ts`
- `frontend/src/tabs/tasksTab.ts`
- `frontend/src/tabs/calendarTab.ts`
- `frontend/src/features/quickActionsFeature.ts`
- `frontend/src/components/plotInteractions.ts`
- `frontend/src/features/notificationsFeature.ts`
- `frontend/src/features/weatherFeature.ts`
- `gardenops/routers/attention.py`
- `gardenops/services/attention/*`
- `gardenops/routers/tasks.py`
- `gardenops/routers/calendar.py`
- `gardenops/routers/notifications.py`
- `gardenops/routers/weather.py`
- `gardenops/services/task_completion.py`
- `gardenops/services/task_generator.py`
- `gardenops/services/notification_service.py`
- `gardenops/services/weather_service.py`

**New phase file:** `scripts/e2e/journeys/dailyAttentionWork.cjs`.

### Task 2.1: Build a horticulturally meaningful fixture

Seed frozen dates and explicit plants for:

- single and grouped bloom observation;
- prune and fertilize tasks with care windows;
- watering due with and without sufficient rain;
- stale generated watering work eligible for expiry;
- severe issue follow-up;
- frost/heat/rain alerts;
- manual and generated calendar entries;
- multiple notification preference profiles;
- a second garden with deliberately conflicting plant/task names.

Freeze both date and time through the existing test-only clock guards. Do not use the host's current date inside assertions.

### Task 2.2: Prove Today and task action parity (`D1`, `D2`)

For Tasks, Calendar details, plot drawer, Quick Actions, Today navigation, and batch actions:

- [ ] Complete a single-plant prune/fertilize task and assert the correct journal event and selected plant.
- [ ] Partially complete a grouped task, leaving exactly the unselected plants on the same active task.
- [ ] Record bloom observed and `not seen this season` without marking the plant absent or not growing.
- [ ] Snooze bloom/prune/fertilize with the default +1 week behavior.
- [ ] Choose a manual date before the undo/action toast disappears.
- [ ] Warn when +1 week exceeds a care window and preserve the user's choice.
- [ ] Complete, skip, snooze, and reschedule through offline-supported paths with stable operation IDs.
- [ ] Verify grouped completion cannot bypass plant selection through batch or alternate surfaces.
- [ ] Reload and inspect Journal/Calendar/Today/Notifications/badges for consistent final state.

Use one shared behavior service rather than duplicating task policy in each UI surface. Tests must fail if a surface silently reintroduces `+1 day` or generic completion.

### Task 2.3: Prove calendar actions and export (`D3`)

- [ ] Create/edit/delete a manual event with linked plants/plots.
- [ ] Open and act on projected task/weather entries without mutating read-only projections incorrectly.
- [ ] Exercise month/week/list navigation at desktop and mobile.
- [ ] Download/parse ICS output and assert escaping, dates, garden scope, and no secrets.
- [ ] Create/revoke a subscription where supported and prove the old token no longer works.
- [ ] Verify viewer read and editor/admin write semantics.

### Task 2.4: Prove notification preferences and delivery (`D4`)

- [ ] Configure category, urgency, channel, digest, and quiet-hour preferences through the real UI.
- [ ] Trigger eligible and ineligible events with the scheduler disabled except for an explicit deterministic invocation.
- [ ] Assert Today eligibility, durable inbox event, email/outbox eligibility, and badge count use the intended normalized preference decision.
- [ ] Dismiss/mute through supported semantics and verify the action is user-scoped, not garden-global.
- [ ] Switch A/B/A while inbox requests are delayed; assert stale responses and sheets cannot overwrite active-garden state.
- [ ] Prove focus trap, Escape behavior, focus return, and inert closed sheets on desktop/mobile.
- [ ] Never send real email; inspect the deterministic outbox or service result.

### Task 2.5: Prove weather-generated work and no-action outcomes (`D5`)

- [ ] Run weather checks through deterministic fixture data.
- [ ] Sufficient rain prevents or supersedes watering work according to current policy and creates a compact no-action-needed outcome rather than noisy active work.
- [ ] Stale watering work expires through the maintenance path and is not merely hidden.
- [ ] Frost/heat/dry alerts produce bounded, deduplicated tasks/notifications.
- [ ] Repeated checks and concurrent invocations preserve one logical alert/task identity.
- [ ] Dismissal and regeneration semantics match the current attention specification.
- [ ] Direct browser weather action works on desktop/mobile and refreshes connected surfaces.

Database assertions: task status and links, journal event types, attention state/outcomes, notification events/preferences, calendar records, weather identity/link tables, operation IDs, audit events, and zero duplicates.

### Task 2.6: Validate and review

Focused commands:

```bash
scripts/run_complete_journeys_e2e.sh --phase 2
.venv/bin/python -m pytest tests/test_attention_api.py tests/test_attention_service_unit.py tests/test_tasks.py tests/test_task_generator.py tests/test_notifications.py tests/test_calendar.py tests/test_weather.py tests/test_weather_service_unit.py tests/test_scheduler_automation.py -q
.venv/bin/python scripts/run_fast_postgres_tests.py --command-database gardenops_attention_e2e_test --command -- scripts/run_attention_today_e2e.sh
.venv/bin/python scripts/run_fast_postgres_tests.py --command-database gardenops_task_history_e2e_test --command -- scripts/run_task_completion_history_e2e.sh
```

Then run global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: horticultural meaning, action parity, noise reduction, notification preference wiring, offline task semantics, and accessibility of transient/manual-date controls.

**Phase 2 acceptance:** Every task surface follows one policy; completion creates accurate history; partial work remains actionable; rain and expiry outcomes are semantically correct; calendar and notification preferences persist; no duplicate weather work; cumulative Phase 0-2 passes.

---

## Phase 3: Journal, Issues, Harvest, Media, And Photo-To-Action

**Purpose:** Prove the observation-to-action chain: capture evidence, identify or diagnose when requested, create durable records, schedule follow-up work, preserve linked history, and clean media safely.

**Journey scope:** `P1`, `P2`, `P3`, `P5`, `I2`, `I3`.

**Primary files to inspect:**

- `frontend/src/tabs/journalTab.ts`
- `frontend/src/tabs/issuesTab.ts`
- `frontend/src/tabs/harvestTab.ts`
- `frontend/src/components/mediaGallery.ts`
- `frontend/src/components/identifyPlant.ts`
- `frontend/src/components/diagnosePlant.ts`
- `frontend/src/services/offlineQueue.ts`
- `gardenops/routers/journal.py`
- `gardenops/routers/issues.py`
- `gardenops/routers/harvest.py`
- `gardenops/routers/media.py`
- `gardenops/routers/ai.py`
- `gardenops/services/plantnet.py`
- `gardenops/services/media_store.py`
- `gardenops/offline_idempotency.py`

**New phase file:** `scripts/e2e/journeys/observationToAction.cjs`.

### Task 3.1: Add deterministic media and domain fixtures

- [ ] Add small, license-safe tracked image fixtures with known dimensions/orientation and no personal metadata.
- [ ] Generate malformed, oversized, unsupported-type, and duplicate-byte variants during tests under private `/tmp`; do not commit large binaries.
- [ ] Seed plants/plots for healthy observation, bloom, disease diagnosis, treatment follow-up, and harvest.
- [ ] Use the existing deterministic AI fixture for successful identify/diagnose in this phase; provider breadth belongs to Phase 7.
- [ ] Give each browser run a private media directory and assert it starts empty.

### Task 3.2: Prove journal lifecycle (`P1`)

- [ ] Create a journal entry with text, date, plants, plots, and multiple media assets.
- [ ] Edit links/notes/date, reload, filter, and open the exact entry.
- [ ] Exercise bloom/observation side effects and verify only intended plant fields change.
- [ ] Queue a journal+media create offline, simulate server commit followed by lost response, reload/reconnect twice, and prove one entry/asset/operation.
- [ ] Delete with confirmation and assert database links and committed files are removed after transaction commit.
- [ ] Inject storage cleanup failure in disposable tests and prove database commit semantics plus retry/diagnostic behavior are explicit.

### Task 3.3: Prove issue lifecycle (`P2`, `I3`)

- [ ] Create an issue linked to plants/plots with type, severity, symptoms, suspected cause, treatment, follow-up date, and media.
- [ ] Run diagnosis from a photo, review the advisory result, and explicitly choose to create/update an issue; inference must not silently mutate records.
- [ ] Edit treatment/follow-up and inspect history.
- [ ] Verify exactly one follow-up task and eligible notification after repeated identical updates.
- [ ] Resolve and reopen if supported; verify Today/Tasks/Journal/Notifications consistency.
- [ ] Queue issue+media offline, simulate lost ack and repeated reconnect, and assert one issue, one intended history event, one follow-up, one notification identity, and one asset.
- [ ] Delete with confirmation and prove related links/media cleanup without deleting unrelated plant history.

### Task 3.4: Prove harvest lifecycle (`P3`)

- [ ] Create harvest with crop/plant/plot, quantity, unit, quality/notes, date, and media.
- [ ] Edit and verify rollups and linked journal/history update without duplicate totals.
- [ ] Exercise mobile units and decimal validation.
- [ ] Queue harvest+media offline with lost ack and repeated reconnect.
- [ ] Delete and prove rollups/history/filesystem state return to the documented result.
- [ ] Verify editor/admin writes, viewer denial, garden isolation, and empty/error states.

### Task 3.5: Prove media lifecycle (`P5`, `I2`)

- [ ] Upload, preview, link, relink where supported, select cover, replace cover, download/open, and delete media.
- [ ] Verify EXIF/orientation handling and preview dimensions using pixel/dimension assertions, not only DOM presence.
- [ ] Identify a plant from a photo, then explicitly attach the result to a new or existing plant where the UI supports it.
- [ ] Reject wrong MIME/signature, oversized, truncated, and decompression-risk inputs before unsafe processing.
- [ ] Prove quotas and friendly error recovery without leaving orphan files or database rows.
- [ ] Prove binary fingerprint includes target metadata and bytes so changed replay returns `409`.
- [ ] Prove retained operation targeting a deleted record returns `410` and a new operation ID is required.

### Task 3.6: Postcondition graph

After each browser scenario, query exact counts and links for:

- journal entries and plant/plot links;
- issue rows, history, follow-up tasks, notifications, and attention items;
- harvest rows, journal links, and report rollups;
- media assets, previews, target links, covers, and storage keys;
- offline operation IDs/fingerprints/status;
- audit events.

Walk the private media directory and compare expected files to database storage keys. Fail on orphan files, missing files, path traversal, or files outside the private root.

### Task 3.7: Validate and review

```bash
scripts/run_complete_journeys_e2e.sh --phase 3
.venv/bin/python -m pytest tests/test_journal.py tests/test_issues.py tests/test_harvest.py tests/test_media.py tests/test_media_store_unit.py tests/test_identify.py tests/test_offline_idempotency.py tests/test_offline_idempotency_unit.py -q
```

Then run global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: photo-to-action clarity, no AI-autonomous mutation, linked history correctness, replay idempotency, media security/cleanup, and mobile form usability.

**Phase 3 acceptance:** Journal/issue/harvest/media CRUD and replay pass desktop/mobile; database/file graphs match exactly; diagnosis remains advisory; no duplicate follow-up/history/rollup; viewer and garden isolation are proven; cumulative Phase 0-3 passes.

---

## Phase 4: Planning, Inventory, Procurement, Care, Reports, And Exports

**Purpose:** Prove the less frequently used but operationally important workflows that turn plans and supplies into work, then expose trustworthy history and reports.

**Journey scope:** `P4`, `P6`, `I1`, `L1`, `L2`, `R2`, `R3`.

**Primary files to inspect:**

- `frontend/src/tabs/inventoryTab.ts`
- current planner/workflow/procurement components and tabs
- `frontend/src/tabs/careTab.ts`
- `frontend/src/tabs/indoorTab.ts`
- `frontend/src/tabs/statisticsTab.ts`
- admin export/import surfaces
- `gardenops/routers/inventory.py`
- `gardenops/routers/procurement.py`
- `gardenops/routers/planner.py`
- `gardenops/routers/workflows.py`
- `gardenops/services/gardener_reports.py`
- `gardenops/routers/statistics.py`
- `gardenops/routers/exports.py`
- plant catalogue/search routes

**New phase file:** `scripts/e2e/journeys/planningAndReporting.cjs`.

### Task 4.1: Inventory and procurement (`P4`, `L2`)

- [ ] Create an inventory item linked to a garden/plant where supported.
- [ ] Receive, consume/use, adjust, and reverse or correct stock through UI controls.
- [ ] Verify units, decimals, negative-stock rules, transaction ledger, actor, and timestamps.
- [ ] Submit two concurrent adjustments and prove the documented consistency behavior.
- [ ] Create a procurement item, prioritize/edit it, mark ordered, receive into inventory, and close/cancel it.
- [ ] Prove receiving is idempotent and creates one intended inventory transaction.
- [ ] Exercise filters/empty states and true mobile table/list controls.
- [ ] Prove viewer denial and cross-garden isolation.

### Task 4.2: Planner, goals, suggestions, and workflows (`L1`)

- [ ] Create/edit/complete/archive the supported planning goal lifecycle.
- [ ] Generate or accept a suggestion using deterministic local inputs.
- [ ] Convert an accepted item into tasks or workflow steps exactly once.
- [ ] Progress, skip, and complete workflow steps; verify connected Tasks/Calendar/Today state.
- [ ] Reject stale/concurrent transitions without duplicate tasks.
- [ ] Exercise admin/editor/viewer behavior and mobile interaction.

Do not invent planner capabilities absent from the product. For an unsupported transition, classify it explicitly and test that the UI does not promise it.

### Task 4.3: Care, indoor work, and catalogue (`P6`, `I1`)

- [ ] Exercise supported care generation for representative indoor/outdoor plants.
- [ ] Verify generated work is bounded, deduplicated, and linked to the active garden.
- [ ] Prove empty/degraded catalogue behavior and local search.
- [ ] If an external catalogue provider is configured by the existing product, defer its provider matrix to Phase 7 but prove the ordinary UI contract here.
- [ ] If external catalogue search is intentionally unavailable, record `not_applicable` with the product reason; do not claim external results exist.
- [ ] Verify role, mobile, refresh, and no-result/error states.

### Task 4.4: Reports, statistics, corrective actions, and exports (`R2`, `R3`)

- [ ] Open statistics/reports with seeded known totals and assert displayed values against database calculations.
- [ ] Filter date/garden/plant/plot and verify all cards/tables/charts use the same scope.
- [ ] Follow a corrective-action link back to the exact task/issue/plant context and return without losing filters.
- [ ] Export supported CSV/JSON/ZIP/ICS data through browser downloads.
- [ ] Parse downloads structurally and assert headers/schema, escaping, scope, date format, and absence of secrets/internal paths.
- [ ] Import/restore only where supported and reuse Phase 1 atomicity rules.
- [ ] Verify large export progress/error behavior and viewer permissions.

### Task 4.5: Data-quality assertions

Use fixed seed values to reconcile:

- inventory quantity = sum of ledger transactions;
- procurement receipt = one inventory transaction;
- accepted plan/workflow = expected tasks, no duplicates;
- report totals = source rows after filters;
- harvest/task/issue/care history = linked records shown to the user;
- exports = active garden and authorized scope only.

Any discrepancy is a product defect, not a test tolerance. Fix the source query or contract and add a backend regression test.

### Task 4.6: Validate and review

```bash
scripts/run_complete_journeys_e2e.sh --phase 4
.venv/bin/python -m pytest tests/test_inventory_endpoints.py tests/test_procurement.py tests/test_planner.py tests/test_workflows.py tests/test_gardener_reports.py tests/test_statistics_endpoints.py tests/test_exports.py tests/test_export_import.py tests/test_indoor_plants.py -q
```

Then global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: repeated-work ergonomics, ledger/report correctness, concurrent transitions, trustworthy exports, mobile dense-data presentation, and avoiding unsupported promises.

**Phase 4 acceptance:** Operational lifecycles work desktop/mobile; ledgers and reports reconcile exactly; downloads parse and remain scoped; role checks pass; unsupported catalogue behavior is honestly classified; cumulative Phase 0-4 passes.

---

## Phase 5: Authentication, Invitations, Sessions, Roles, And Settings

**Purpose:** Prove the complete account and membership lifecycle, including passkeys, MFA recovery/disable, session expiry/revocation, invitation acceptance, and browser-level authorization behavior.

**Journey scope:** `A1`, `A2`, `A4`, `C1`, `C3`, `C5`, `CROSS-02`.

**Required skill during implementation/review:** `gardenops-auth-security-glue`.

**Primary files to inspect:**

- frontend auth gate, login, onboarding, passkey, MFA, settings, admin membership, and incident-control modules
- `gardenops/auth/*`
- auth/session/passkey/MFA/invitation routers
- `gardenops/routers/admin.py` and security control routes
- auth, security, authorization, passkey, and MFA tests

**New phase file:** `scripts/e2e/journeys/identityAndRoles.cjs`.

### Task 5.1: Seed isolated identity scenarios

Create distinct fixtures for:

- first admin bootstrap already completed;
- invited editor and viewer;
- expired/revoked/used invitation tokens;
- password user eligible for TOTP and passkey enrollment;
- two independent browser sessions for revocation;
- user requiring step-up;
- emergency read-only/write-block controls.

Store only fixed disposable credentials in the runner environment. Scrub them from traces/manifests and never reuse live values.

### Task 5.2: Invitation and membership lifecycle (`A2`, `C1`)

- [ ] Admin creates an invitation through UI and captures the disposable token without logging it.
- [ ] Invitee accepts, establishes supported credentials, signs in, and sees only the invited garden/role.
- [ ] Used, expired, revoked, malformed, and wrong-user invitations fail without account or membership side effects.
- [ ] Admin changes role/removes membership; the other live session loses capabilities on the next authorized check without stale enabled controls.
- [ ] Viewer/editor cannot invite, promote, or remove beyond policy.
- [ ] Two-user browser contexts prove cross-user and cross-garden isolation.

### Task 5.3: Passkey lifecycle (`A1`, `C3`)

Use Chromium DevTools Protocol virtual WebAuthn authenticators inside disposable browser contexts:

- [ ] Enroll a discoverable credential through the real UI.
- [ ] Sign out and sign in passwordlessly where supported.
- [ ] Exercise user verification required/rejected behavior.
- [ ] Rename/list/revoke a passkey and prove revoked credential cannot authenticate.
- [ ] Cover challenge expiry, replay, wrong RP/origin, and cancellation in backend tests plus browser-visible recovery.
- [ ] Assert credential IDs and public keys are never exposed in logs/manifests beyond safe redacted identifiers.

Do not mock the browser ceremony at the API boundary for the E2E proof.

### Task 5.4: TOTP, recovery, and disable (`C3`)

- [ ] Preserve existing desktop enrollment and mobile login/step-up coverage.
- [ ] Use a recovery code successfully once; prove second use fails.
- [ ] Regenerate recovery codes and invalidate old unused codes.
- [ ] Disable TOTP after required assurance; prove secrets/codes/pending enrollments are removed as designed.
- [ ] Cancel or expire pending enrollment without enabling MFA.
- [ ] Exercise rejected and successful step-up for sensitive actions.
- [ ] Assert expected auth `401` responses do not produce false global auth-expiry behavior.

### Task 5.5: Sessions, logout, settings, and incident controls (`A4`, `C5`)

- [ ] Verify session listing/device metadata only exposes intended information.
- [ ] Revoke another session and prove its next request/UI transition is denied cleanly.
- [ ] Exercise idle/absolute expiry with a test-only frozen clock.
- [ ] Logout clears session UI state and sensitive queued/local data according to policy.
- [ ] Change supported user/security/notification settings, reload, and prove persistence and active-garden scope.
- [ ] Enable emergency or incident write controls in a disposable environment, prove blocked writes and audit evidence, then restore normal mode.
- [ ] Viewer/editor/admin surfaces and direct APIs agree for every sampled sensitive action.

### Task 5.6: Negative authorization sweep

For every write API exercised in Phases 1-5:

- [ ] submit a viewer request;
- [ ] submit a user from another garden;
- [ ] submit an unauthenticated request;
- [ ] submit stale CSRF/session state where applicable;
- [ ] assert denial status and zero database/filesystem side effects.

Extend the existing authorization sweep rather than creating disconnected duplicate policy tables.

### Task 5.7: Validate and review

```bash
scripts/run_complete_journeys_e2e.sh --phase 5
scripts/run_totp_mfa_e2e.sh
.venv/bin/python -m pytest tests/test_auth_endpoints.py tests/test_auth_lifecycle.py tests/test_auth_mfa_stepup.py tests/test_passkeys.py tests/test_security.py tests/test_security_mfa_unit.py tests/test_authorization_negative_sweep.py tests/test_authorization_write_gates.py tests/test_feature_gates.py tests/test_incident_controls_unit.py -q
```

Then global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: credential ceremony authenticity, token/session leakage, cross-user isolation, stale role UI, CSRF, recovery paths, and sensitive-action usability.

**Phase 5 acceptance:** Invitation, passkey, TOTP recovery/disable, session expiry/revocation, logout, role change, settings, and incident controls have real-browser proof; negative sweeps leave zero side effects; no secret appears in artifacts; cumulative Phase 0-5 passes.

---

## Phase 6: Offline Replay, Failure Recovery, And Destructive Integrity

**Purpose:** Prove the system remains correct when responses are lost, users reconnect repeatedly, records disappear, storage fails, transactions abort, and destructive operations cross many tables.

**Journey scope:** `OFF-01`, `C2`, `INT-01`, plus offline/failure variants of `D2`, `P1`, `P2`, `P3`, and `P5`.

**Primary files to inspect:**

- `frontend/src/features/offlineFeature.ts`
- `frontend/src/services/offlineQueue.ts`
- `frontend/src/services/api.ts`
- `gardenops/offline_idempotency.py`
- `gardenops/audit.py`
- `gardenops/schema_signature.py`
- `gardenops/db.py`
- destructive routes for gardens, journal, issues, harvest, media, terrain, and users
- migrations `0021_weather_alert_identity.sql`, `0022_offline_operation_idempotency.sql`, and newer migrations

**New phase file:** `scripts/e2e/journeys/offlineAndFailureRecovery.cjs`.

### Task 6.1: Build a real lost-ack browser controller

- [ ] Use Playwright routing to allow a mutation to reach the real backend, then abort/drop the response to simulate lost acknowledgement.
- [ ] Confirm server commit through a separate postcondition channel; do not fake success in the page.
- [ ] Toggle browser context offline, reload where browser behavior permits, restore network, and trigger repeated reconnect/replay.
- [ ] Record operation ID and endpoint category in redacted evidence.
- [ ] Prove UI moves from queued -> retrying -> resolved or actionable conflict without duplicate optimistic rows.

### Task 6.2: Cover every supported offline operation (`OFF-01`)

For `journal`, `issues`, `harvest`, `task_action`, and `media_upload`:

- [ ] successful first delivery;
- [ ] server commit plus lost response;
- [ ] two or more reconnect/replay attempts;
- [ ] same operation ID and identical payload returns original logical result;
- [ ] same operation ID and changed payload/binary/target returns `409`;
- [ ] retained operation whose target was deleted returns `410`;
- [ ] new operation ID creates a replacement only when user explicitly retries as new;
- [ ] garden+endpoint scope prevents cross-garden or cross-operation collisions;
- [ ] expired operation behavior is deterministic after the 30-day retention boundary;
- [ ] stale queued draft from Garden A cannot replay into Garden B after switching;
- [ ] logout/account change clears or isolates queued data according to policy.

Assert exact row, linked side-effect, notification, journal, rollup, asset, and operation counts after every case.

### Task 6.3: Add test-only failure injection safely

Prefer dependency injection or monkeypatching in backend tests. Where browser-level failure proof requires a runtime hook:

- gate it behind exact `APP_ENV=test` plus a dedicated `GARDENOPS_E2E_FAILURE_INJECTION=true` flag;
- reject startup if the flag is set outside test;
- never expose a generic production HTTP failpoint;
- use an allowlisted finite failure name, not arbitrary SQL/path/code input;
- document and test the production rejection guard.

Required failures:

- media write before DB commit;
- DB failure after staged media write;
- filesystem delete failure after DB commit;
- audit insertion failure during destructive transaction;
- one child-table delete failure during garden deletion;
- snapshot restore failure after partial statements;
- schema migration or signature mismatch before stamp;
- provider response after garden switch (stale response, not external provider failure).

### Task 6.4: Prove destructive atomicity (`C2`)

- [ ] Cancel and confirm destructive actions through UI.
- [ ] Garden delete dynamically enumerates/validates all owned cascade state rather than relying on a stale fixed table list.
- [ ] Audit evidence is atomic with the destructive transaction where policy requires it.
- [ ] Failure before commit leaves the complete graph and files intact.
- [ ] Post-commit filesystem cleanup failure is visible/retryable and does not pretend the file is gone.
- [ ] Successful deletion leaves zero retained scoped rows/files except intentionally retained redacted audit evidence.
- [ ] Sensitive deletes require current assurance/step-up where specified.
- [ ] Mobile confirmation language names the object and consequence without overflow.

### Task 6.5: Prove schema/bootstrap integrity (`INT-01`)

- [ ] Fresh database migrates from zero to current schema.
- [ ] Upgrade fixture from representative prior schema applies every migration once.
- [ ] Existing complete schema can be recognized/stamped only according to current policy.
- [ ] Missing column/index/constraint/nullability/default/critical definition refuses stamping.
- [ ] Partially applied migration fails closed and produces an actionable operator error.
- [ ] Migration rerun is idempotent where intended.
- [ ] Migration and schema checks never run against production in test commands.

### Task 6.6: Validate and review

```bash
scripts/run_complete_journeys_e2e.sh --phase 6
.venv/bin/python -m pytest tests/test_offline_idempotency.py tests/test_offline_idempotency_unit.py tests/test_destructive_audit_atomicity.py tests/test_integrity.py tests/test_export_import.py tests/test_media.py tests/test_gardens_endpoints.py -q
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke after-start
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke during-migration
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke during-pytest
```

Then global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: whether lost-ack is real, replay identity/fingerprint boundaries, transaction/file consistency, production-inert failpoints, destructive audit behavior, and safe disposable cleanup.

**Phase 6 acceptance:** All five offline operation classes pass the complete replay matrix in a browser or justified backend-only boundary; failure injection is impossible in production mode; destructive operations are atomic/observable; schema mismatch fails closed; cumulative Phase 0-6 passes.

---

## Phase 7: Providers, Weather Adapters, ShadeMap, Terrain, And LiDAR

**Purpose:** Prove every external-capability boundary without real provider cost or network access, including degraded behavior and visible map rendering.

**Journey scope:** `M5`, `I2`, `I3`, `I4`, `C4`, `C6`, provider variants of `D5` and `I1`.

**Required skills during implementation/review:** `gardenops-lidar-map-shademap`, `gardenops-auth-security-glue` for provider secrets.

**Primary files to inspect:**

- `gardenops/services/ai_provider.py`
- weather, PlantNet/identify, ShadeMap/Overpass, terrain, and LiDAR adapters/routes
- frontend Shade panel, terrain controls, identify/diagnose, AI chat, weather, and provider settings
- provider usage/budget, secret, and feature-gate code
- existing deterministic provider E2E and LiDAR/ShadeMap backend tests

**New phase files:**

- Create: `scripts/e2e/journeys/providersAndTerrain.cjs`
- Create or extend: deterministic loopback provider fixture modules under `scripts/e2e/providers/`
- Add only small deterministic terrain/image fixtures; generate large grids/LAS variants at runtime

### Task 7.1: Standardize deterministic provider fixtures

Each fixture server must:

- bind only to `127.0.0.1` on a dynamic port;
- support named scenarios: `success`, `timeout`, `malformed`, `quota`, `unauthorized`, and `partial` where meaningful;
- record only method/path, redacted request shape, and count;
- never record secret headers, uploaded media bytes, prompts containing user data, or full coordinates in tracked files;
- expose test-only counters for exact user/garden budget assertions;
- stop with the runner process group and leave no listening socket.

Keep adapters provider-specific. Do not force weather, AI, PlantNet, and terrain into one leaky response abstraction.

### Task 7.2: AI, identify, diagnose, and catalogue (`I1`, `I2`, `I3`, `I4`, `C4`)

- [ ] Disabled provider produces a useful recoverable UI, no blank panel, and no external request.
- [ ] Successful deterministic AI chat/analysis works desktop/mobile and preserves request budgets per user and garden.
- [ ] Identify and diagnose accept a fixture image and render structured advisory results.
- [ ] User explicitly chooses any durable plant/issue/task mutation after inference.
- [ ] Timeout/malformed/quota/auth errors are concise, retryable where safe, and do not duplicate records or consume budgets incorrectly.
- [ ] Provider switching/settings refresh without exposing existing secrets.
- [ ] Admin can set/rotate/delete supported secrets; returned/logged values remain redacted.
- [ ] Editor/viewer cannot read or mutate platform secrets.
- [ ] Catalogue provider behavior, if supported, receives the same disabled/success/failure matrix.

### Task 7.3: Weather provider matrix (`D5`, `C4`)

- [ ] Disabled/no-key, valid forecast, timeout, malformed values, stale cache, and quota behavior.
- [ ] Cache and fallback age are visible enough for user trust without notification noise.
- [ ] Repeated/concurrent checks preserve alert identity and generated work rules from Phase 2.
- [ ] Garden switching discards stale forecast/plot-alert responses.
- [ ] No provider failure erases valid cached state unless policy explicitly requires it.

### Task 7.4: ShadeMap, sun/shade, elevation, and obstacles (`M5`)

- [ ] Open the full-bleed map Shade experience on desktop/mobile, choose modes/date/time, and verify visible pixels change.
- [ ] Use screenshot and canvas/pixel sampling to prove the layer is nonblank, framed correctly, and changes for deterministic sun/time input.
- [ ] Edit calibration, obstacles, elevation overrides, and state through real controls.
- [ ] Reload and verify database/cache state and rendering.
- [ ] Cover provider disabled, token expiry, timeout, cache hit/miss, invalid calibration, and retry.
- [ ] Verify closed mobile sheets are inert, controls do not overlap the map, and map interactions resume after close.
- [ ] Prove editor/admin writes and viewer read-only behavior.

### Task 7.5: Local terrain and LiDAR (`C6`)

- [ ] Generate a small valid LAS/LAZ fixture with known bounds/elevations inside the private runner.
- [ ] Upload through the real admin UI and assert progress, validation, derived grid/cache, and tile availability.
- [ ] Render the terrain on the map and use pixel checks plus known elevation sampling.
- [ ] Reject wrong format, truncated data, unsafe dimensions/bounds, excessive point/grid budgets, path traversal, and garden mismatch.
- [ ] Replace/remove terrain and assert cache invalidation, tile behavior, database rows, and filesystem cleanup.
- [ ] Exercise failure during processing and prove no active half-written terrain state.
- [ ] Test mobile viewing and admin controls without placing the 3D/terrain scene in a decorative card.

### Task 7.6: Validate and review

```bash
scripts/run_complete_journeys_e2e.sh --phase 7
.venv/bin/python scripts/run_fast_postgres_tests.py --command -- bash scripts/run_deterministic_provider_e2e.sh
.venv/bin/python -m pytest tests/test_ai_provider.py tests/test_deterministic_ai_provider.py tests/test_identify.py tests/test_plantnet.py tests/test_weather.py tests/test_weather_service_unit.py tests/test_shademap.py tests/test_lidar_terrain.py tests/test_provider_settings.py tests/test_feature_gates.py -q
```

Then global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: no real network, secret redaction, quota accounting, advisory-vs-mutation clarity, cache/degraded trust, actual terrain pixels, upload validation, and mobile map control ergonomics.

**Phase 7 acceptance:** All provider states are deterministic and externally isolated; durable mutations require user confirmation; secrets remain redacted; weather/cache semantics hold; shade/terrain pixels and state are proven desktop/mobile; LiDAR files/database/cache clean up correctly; cumulative Phase 0-7 passes.

---

## Phase 8: Accessibility And Responsive Interaction Completion

**Purpose:** Apply one coherent WCAG 2.2 AA-oriented interaction pass across representative states of every feature after workflows are behaviorally stable.

**Journey scope:** Cross-cutting states from every journey ID, with critical paths `A1`, `A3`, `M1`, `D1`, `D2`, `P1`, `P2`, `C3`, and `CROSS-01` exercised most deeply.

**Primary files to inspect:** All shared layout, dialog, sheet, toast, form, table/list, navigation, map control, auth, and notification components plus `frontend/src/style.css`.

**New phase files:**

- Create: `scripts/e2e/journeys/accessibilityAndResponsive.cjs`
- Create: `tests/accessibility_expectations.yaml` or an equivalently structured tracked state inventory
- Extend: `tests/test_frontend_accessibility_static.py`
- Modify: `frontend/package.json` and lockfile only if adding a reviewed automated accessibility dependency

### Task 8.1: Freeze the accessibility state inventory

For every feature surface, list at least:

- normal populated state;
- empty state;
- validation error;
- loading/busy state;
- recoverable backend/provider error;
- modal/sheet/menu open state if present;
- read-only/viewer state;
- mobile state.

Map each state to a browser route/navigation sequence, required keyboard action, expected focus target, and expected announcement/semantic name.

### Task 8.2: Add automated WCAG scanning responsibly

- [ ] Prefer a proven maintained accessibility engine such as axe-core and pin it through npm lockfile review.
- [ ] Run it on the state inventory, not only the login page.
- [ ] Treat serious/critical violations as blockers.
- [ ] Do not add broad rule disables. Any narrow exclusion needs element, rule, technical reason, owner, and expiry/revisit phase in the ledger.
- [ ] Keep explicit GardenOps assertions because automated scanning cannot prove workflow usability, focus restoration, or correct live announcements.

### Task 8.3: Keyboard, focus, and semantic proof

For critical journeys on desktop:

- [ ] complete the workflow without a pointer;
- [ ] verify logical Tab/Shift+Tab order and no focus traps outside active modals;
- [ ] verify dialog/sheet initial focus, Escape/cancel, confirm, and focus return;
- [ ] verify menus, tabs, segmented controls, date controls, tables/lists, and map-adjacent controls use expected keyboard semantics;
- [ ] verify visible focus is not obscured;
- [ ] verify loading uses `aria-busy` or equivalent where meaningful;
- [ ] verify validation and async results are associated/announced without stealing focus;
- [ ] verify toast actions pause long enough for focus/hover and remain operable;
- [ ] inspect Chromium's accessibility tree for names, roles, states, relationships, and hidden/inert content.

### Task 8.4: Responsive, zoom, motion, and touch proof

- [ ] Run all inventory states at desktop, Pixel 7 mobile, and tablet.
- [ ] Run critical desktop states at 200 percent zoom and narrow reflow.
- [ ] Run critical states with reduced motion and assert auto-fading/moving UI remains understandable and operable.
- [ ] Assert no horizontal page overflow, clipped longest localized labels, overlapping controls, or content hidden behind fixed navigation/sheets.
- [ ] Measure interactive target boxes for required touch targets, allowing documented inline-text exceptions.
- [ ] Verify orientation/layout changes do not lose entered form state or active garden context.
- [ ] Verify color is not the only status signal and contrast is compliant for text, focus, controls, and meaningful graphics.

Do not scale fonts directly with viewport width. Do not solve overflow by hiding important commands or shrinking touch targets.

### Task 8.5: Screen-reader verification boundary

Automated semantic-tree checks are required but are not called a full screen-reader audit. Before Phase 8 closes:

- [ ] run an operator-assisted screen-reader smoke on sign-in, garden switch, Today/task action, journal create, issue create, and notification preferences;
- [ ] use an available supported browser/screen-reader pair and record versions plus concise outcomes in the validation ledger;
- [ ] verify headings/landmarks, control names, field errors, dialog boundaries, live results, and completion status;
- [ ] keep private recordings/transcripts, if any, under ignored research;
- [ ] if no real screen-reader environment is available, Phase 8 remains blocked rather than being mislabeled complete.

The GPT-5.6 Sol Ultra reviewer validates the recorded evidence and reruns automated proof; it does not replace the real screen-reader smoke.

### Task 8.6: Validate and review

```bash
scripts/run_complete_journeys_e2e.sh --phase 8
.venv/bin/python -m pytest tests/test_frontend_accessibility_static.py tests/test_frontend_read_only_affordances_static.py tests/test_frontend_indoor_read_only_static.py tests/test_frontend_garden_scope_static.py -q
cd frontend && npm run build
```

Then global gates and GPT-5.6 Sol Ultra review. Reviewer emphasis: actual keyboard completion, semantics versus visual appearance, mobile/zoom overflow, reduced motion, transient controls, focus restoration, and honesty of screen-reader evidence.

**Phase 8 acceptance:** State inventory is complete; serious/critical automated violations are zero; critical journeys work keyboard-only; focus/semantics/live feedback pass; desktop/mobile/tablet/zoom/reduced-motion layouts pass; real screen-reader smoke is recorded; cumulative Phase 0-8 passes.

---

## Phase 9: Measured Optimization, Regression Budgets, And Final Closure

**Purpose:** Measure realistic scale, optimize only evidenced bottlenecks, enforce stable budgets, run the complete program, and close every remaining manifest dimension.

**Journey scope:** All journey IDs, with performance focus on `A1`, `A3`, `M1`, `M3`, `D1`, `D2`, `D4`, `D5`, `P1`, `P2`, `P4`, `R2`, `OFF-01`, and `CROSS-01`.

**Primary files to inspect:** Existing page-performance script, optimization E2E, query-scaling tests, frontend lazy boundaries, API list endpoints, DB queries/indexes, and the complete journey harness.

**New/updated files:**

- Create: `tests/performance_budgets.yaml`
- Create: `scripts/check_performance_budgets.py`
- Extend: `scripts/check_page_performance.cjs`
- Extend: `scripts/seed_complete_journeys_e2e.py` with named realistic scale profiles
- Extend: focused query-count/plan tests
- Update: `research/optimization-map/baseline/` private evidence
- Update: `tests/journey_coverage.yaml` to zero open dimensions
- Update: `docs/testing/journey-validation-ledger.md`
- Update public performance/development docs only with reproducible, bounded claims

### Task 9.1: Define realistic scale profiles

Use at least:

- `small`: ordinary smoke fixture;
- `large`: approximately the existing 900 plants / 600 plots fixture, with tasks, journal, issues, media metadata, notifications, and history proportional enough to exercise lists and joins;
- `history-heavy`: multi-season tasks/journal/harvest/weather history;
- `multi-garden`: several gardens with deliberately overlapping names and active queued work.

Generate data deterministically in disposable PostgreSQL. Keep media files small but include realistic metadata counts. Record fixture counts in the manifest.

### Task 9.2: Capture repeatable baselines before optimization

- [ ] Use production frontend build/preview for performance runs, not Vite dev timing.
- [ ] Separate cold startup, warm navigation, API server time, transfer size, script/layout/paint, and Playwright automation overhead.
- [ ] Run enough repetitions to report median and p75; default to at least 7 accepted samples after one warm-up unless variance analysis requires more.
- [ ] Record host/runtime versions and reject samples with browser errors, retries, fixture mismatch, or background external traffic.
- [ ] Measure desktop and true mobile profiles.
- [ ] Capture SQL statement/query counts and `EXPLAIN (ANALYZE, BUFFERS)` only on disposable data.
- [ ] Measure raw/gzip payload sizes and repeated-navigation memory/DOM growth.

Do not make before/after claims until both are measured with the same commit-independent harness and fixture.

### Task 9.3: Create reviewed budgets

Populate `tests/performance_budgets.yaml` only after baseline review. Each budget needs:

- journey/operation ID;
- fixture/profile;
- metric and unit;
- baseline median/p75;
- allowed regression percentage or absolute ceiling;
- sample count and variance rule;
- rationale;
- owner/test command.

Initial budget guidance, subject to measured variance and Sol Ultra review:

- no repeated stable regression greater than 15 percent in app-ready or warm-transition median/p75;
- query counts remain fixed for paths already proven bounded;
- no N+1 slope as plants/tasks/alerts/history scale;
- payload growth remains proportional to requested page size, not total hidden garden data;
- repeated 20-cycle navigation does not accumulate detached views/listeners or unbounded DOM;
- no new source map or unreviewed large eager chunk in production.

The checker must distinguish a noisy inconclusive run from a real pass. It must never rewrite budgets automatically from current results.

### Task 9.4: Optimize one measured bottleneck at a time

For each accepted target:

1. Save baseline evidence.
2. Add a query-count, plan-shape, payload, or browser regression test that fails for the target.
3. Make the smallest ownership-aligned optimization.
4. Rerun the focused journey for correctness.
5. Rerun identical performance samples.
6. Keep only changes with reproducible improvement or necessary budget protection.
7. Revert inconclusive speculative optimizations without disturbing unrelated work.

Likely targets must still be proven before edits: large list virtualization/pagination, hidden-view lifecycle, duplicate refreshes, payload projection, task/history query plans, weather/attention aggregation, media metadata, and repeated tab/map listener cleanup.

### Task 9.5: Close the coverage contract

- [ ] Run `scripts/check_journey_coverage.py --require-closed`.
- [ ] Resolve every remaining `required` dimension with durable evidence or a technically valid `not_applicable` reason reviewed against actual product support.
- [ ] Verify every evidence path exists and every named test is part of a documented command.
- [ ] Reconcile ignored `research/optimization-map/user-journey-map.md`, `test-coverage-matrix.csv`, `findings.md`, and `README.md` with the tracked manifest.
- [ ] Remove stale gap statements only when evidence closes them.
- [ ] Preserve rejected/unmeasured findings rather than rewriting history as a success.

### Task 9.6: Run final cumulative verification

```bash
git status --short
git diff --check origin/main...HEAD
.venv/bin/python scripts/check_journey_coverage.py --require-closed
RUFF_CACHE_DIR=/tmp/gardenops-ruff-cache .venv/bin/ruff check gardenops tests scripts
RUFF_CACHE_DIR=/tmp/gardenops-ruff-cache .venv/bin/ruff format --check gardenops tests scripts
cd frontend && npm ci && npm run build
cd ..
.venv/bin/python scripts/run_fast_postgres_tests.py --full-suite --shards 4
scripts/run_complete_journeys_e2e.sh --through-phase 9
.venv/bin/python scripts/run_fast_postgres_tests.py --command-database gardenops_attention_e2e_test --command -- scripts/run_attention_today_e2e.sh
.venv/bin/python scripts/run_fast_postgres_tests.py --command-database gardenops_task_history_e2e_test --command -- scripts/run_task_completion_history_e2e.sh
.venv/bin/python scripts/run_fast_postgres_tests.py --command -- bash scripts/run_deterministic_provider_e2e.sh
scripts/run_totp_mfa_e2e.sh
scripts/run_ui_flow_map_e2e.sh
GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1 .venv/bin/python scripts/run_fast_postgres_tests.py --command -- scripts/run_optimization_journeys_e2e.sh
.venv/bin/python scripts/check_performance_budgets.py
.venv/bin/python .codex/skills/gardenops-documentation-upkeep/scripts/docs_impact_inventory.py --base origin/main
.venv/bin/python .codex/skills/gardenops-git-push-sanitizer/scripts/git_push_sanitizer.py --pre-push
```

If runtime makes one monolithic browser command impractical, the complete runner may execute isolated phase shards sequentially, but the final result must aggregate every shard and fail if any shard is absent. Do not skip a shard merely because an earlier PR passed it.

### Task 9.7: Final Sol Ultra review

Run the mandatory GPT-5.6 Sol Ultra gate against Phase 9 **and the full program**, not just the performance diff. Supply all phase ledger rows, final closed manifest, cumulative evidence manifest, performance before/after files, and current `origin/main...HEAD` diff.

Additional reviewer questions:

1. Is any journey marked proven by navigation-only evidence?
2. Is any `not_applicable` entry hiding supported or user-visible behavior?
3. Do performance claims separate cold/warm and app/browser/test-runner timing?
4. Did optimization change data semantics, role behavior, accessibility, offline replay, or map-first startup?
5. Are raw artifacts private and durable validation commands tracked?
6. Can a maintainer reproduce every claimed gate from a clean checkout?

Any code/test change after that review requires a new fresh GPT-5.6 Sol Ultra final review.

**Phase 9 acceptance:** Coverage checker has zero open dimensions; all cumulative suites pass; performance budgets pass with reproducible evidence; no unresolved Critical/Important review findings remain; final Sol Ultra disposition is PASS or PASS WITH DOCUMENTED LIMITATIONS; documentation and sanitizer gates pass.

---

## Per-Phase Execution Checklist

Copy this checklist into the working notes for every phase. A less capable implementing model should follow it in order and should not infer that later boxes can compensate for an earlier skipped gate.

### A. Orient

- [ ] Read this plan's global rules and the complete target phase.
- [ ] Read the current tracked journey manifest and prior validation ledger rows.
- [ ] Read the relevant GardenOps repo-local skills before touching auth, attention/tasks, frontend performance, LiDAR/Map, PR review, docs, or publication.
- [ ] Fetch remote state and confirm the prior phase is merged.
- [ ] Create the phase branch from current `origin/main`.
- [ ] Confirm worktree status and preserve unrelated user changes.
- [ ] List the exact journey IDs and files expected to change.

### B. Establish RED

- [ ] Run current focused backend tests and cumulative E2E baseline.
- [ ] Add the new journey skeleton, fixture, and final database/filesystem assertions.
- [ ] Make missing proof fail with an explicit assertion, not a comment or skipped test.
- [ ] Add a narrow regression test before each behavior fix.
- [ ] Record baseline failures and distinguish missing coverage from existing product defects.

### C. Implement

- [ ] Follow existing API, service, DB, frontend, i18n, dialog, and test patterns.
- [ ] Keep transaction boundaries explicit and filesystem work ordered around commit semantics.
- [ ] Scope all state, cache, drafts, requests, and operation IDs by active user/garden where required.
- [ ] Use semantic controls and shared domain policy rather than per-surface copies.
- [ ] Add user-visible loading, empty, error, retry, read-only, and success states.
- [ ] Update English and Norwegian labels together when user-facing text changes.
- [ ] Update only the smallest accurate public/developer documentation set.

### D. Verify GREEN

- [ ] Run the single failing regression test.
- [ ] Run the full phase E2E on desktop and mobile.
- [ ] Run affected backend test modules.
- [ ] Run cumulative E2E through the phase.
- [ ] Run the four-shard backend suite.
- [ ] Run production frontend build.
- [ ] Run coverage manifest check, Ruff, diff check, docs inventory, and push sanitizer.
- [ ] Inspect the private manifest for skipped checks, browser errors, network attempts, and missing postconditions.
- [ ] Update the tracked journey manifest and draft ledger row truthfully.

### E. Independent GPT-5.6 Sol Ultra Gate

- [ ] Capture final pre-review SHA and clean/dirty state.
- [ ] Dispatch a fresh exact GPT-5.6 Sol Ultra agent with the required prompt.
- [ ] Record reported model identity and review artifact location.
- [ ] Triage every finding as accepted, rejected with evidence, or open.
- [ ] Fix every accepted Critical/Important finding and add regression proof.
- [ ] Rerun all affected and cumulative gates.
- [ ] If any file changed, dispatch a new fresh GPT-5.6 Sol Ultra review for the new SHA.
- [ ] Obtain final PASS or PASS WITH DOCUMENTED LIMITATIONS.
- [ ] Finalize the validation ledger row.

### F. Publish

- [ ] Re-run pre-push sanitizer on the exact outbound diff.
- [ ] Commit intentional files only.
- [ ] Push the phase branch.
- [ ] Open a PR with journey scope, problem, solution, behavior changes, evidence, Sol Ultra disposition, risks, and rollback notes.
- [ ] Wait for GitHub checks and address failures on the branch.
- [ ] Review the PR as a whole using `gardenops-pr-review-and-merge`; fix findings rather than only commenting.
- [ ] Merge only after explicit user authorization under the repository's merge rules.
- [ ] Do not deploy merely because a phase merged; production deployment remains a separate explicit user action.

## Pull Request Body Template

```markdown
## Purpose

Phase <N> closes journeys <IDs>. The user problem is <problem>. This phase solves
it by <implementation/evidence approach> while preserving <important boundary>.

## Behavior

- <user-visible behavior or "No production behavior change; evidence only">

## Evidence

- Focused E2E: `<command>` - PASS
- Cumulative E2E through Phase <N>: `<command>` - PASS
- Backend: `<command>` - PASS
- Frontend build: `cd frontend && npm run build` - PASS
- Coverage checker: `<command>` - PASS
- Private manifest: `<ignored relative path>`

## Independent Validation

- Required model: GPT-5.6 Sol Ultra
- Runtime-reported model: `<reported model>`
- Reviewed SHA: `<sha>`
- Disposition: `<PASS or PASS WITH DOCUMENTED LIMITATIONS>`
- Critical findings: 0 unresolved
- Important findings: 0 unresolved
- Accepted limitations: `<none or exact list>`

## Safety And Rollback

- Database/migration impact: `<none or exact migration>`
- Filesystem/provider impact: `<none or exact impact>`
- Rollback boundary: `<code/config/data compatibility>`
```

Do not paste private traces, tokens, test credentials, live data, environment values, or raw reviewer chain-of-thought into the PR.

## Defect Handling Rules

When E2E uncovers a problem:

1. Reproduce it in the smallest disposable fixture.
2. Determine whether the problem is product behavior, test flakiness, fixture error, environment error, or an unsupported expectation.
3. For product behavior, add a failing regression test at the lowest stable layer and preserve a browser assertion for user-visible impact.
4. For test flakiness, remove race assumptions with observable readiness/state; do not add arbitrary sleeps as the primary fix.
5. For fixture errors, fix the fixture and prove it could not accidentally pass an invalid product state.
6. For unsupported expectations, update the manifest reason and user-facing documentation where the UI currently implies support.
7. Never loosen an assertion solely to make the phase green.

Severity definitions for this program:

- **Critical:** production data loss/corruption, auth bypass, cross-user/garden exposure, secret exposure, destructive runner capable of touching production, or unrecoverable migration defect.
- **Important:** broken common journey, duplicate durable side effects, misleading completion/success, inaccessible critical action, stale cross-garden state, unbounded scaling path, or evidence that does not test its claim.
- **Minor:** localized polish/test maintainability issue that does not violate phase acceptance criteria.

## Migration And Rollback Rules

- Prefer additive, backward-compatible migrations.
- Every migration needs fresh install, upgrade, and rerun tests.
- Do not drop/rename data until old code compatibility and rollback implications are explicit.
- Separate filesystem cleanup from database commit and make post-commit failures observable.
- Name a code rollback target before any production rollback.
- Never run Phase E2E, failure injection, schema fixtures, or performance seeds against production.
- A merged phase with migrations is not deployed until the normal live-deploy backup, preflight, migration, integrity, restart, and health gates run under explicit authorization.

## Anti-Patterns That Fail This Plan

- Marking a journey proven because a screenshot exists.
- Using API calls for the user action while claiming browser workflow proof.
- Mocking FastAPI responses in the page for final E2E evidence.
- Reusing one mutable account to simulate role isolation.
- Waiting with fixed long sleeps instead of observing state.
- Running providers over the public internet during tests.
- Treating an empty provider response as success without user-visible degraded state.
- Adding generic failpoint endpoints that could run in production.
- Ignoring files under `research/` without first verifying `git check-ignore` and safe path resolution.
- Committing traces, screenshots, database dumps, uploaded media, generated terrain, or credentials.
- Calling axe/screenshots a complete accessibility audit.
- Updating performance budgets to match a regression.
- Optimizing large files/chunks without runtime evidence.
- Allowing the implementing model or a substitute model to satisfy the mandatory GPT-5.6 Sol Ultra gate.
- Carrying unresolved Important findings into the next phase.

## Final Program Definition Of Done

The final PR may state the program is complete only when:

- [ ] Phases 0-9 are merged in order.
- [ ] Every phase ledger row names a final SHA and GPT-5.6 Sol Ultra PASS disposition.
- [ ] `scripts/check_journey_coverage.py --require-closed` passes.
- [ ] No tracked manifest dimension remains `required`.
- [ ] Every `not_applicable` has a current product/technical reason and reviewer acceptance.
- [ ] Complete real-backend journey suite passes desktop/mobile and Phase 8 variants.
- [ ] Full four-shard backend suite and production frontend build pass.
- [ ] Provider tests show no unexpected outbound network attempt.
- [ ] Database and filesystem postconditions pass for every applicable mutation.
- [ ] Real screen-reader smoke evidence exists for the critical workflows.
- [ ] Performance budgets pass without automatic rebasing.
- [ ] Ignored research maps agree with durable tracked evidence.
- [ ] Docs inventory, environment docs check when applicable, diff check, and push sanitizer pass.
- [ ] Final whole-program GPT-5.6 Sol Ultra review has zero unresolved Critical/Important findings.

At that point, remaining ideas are new product scope rather than unfinished verification of the features covered by this program.

## Appendix A: Non-Negotiable Outcomes

This program is complete only when all of the following are true:

1. Every journey ID currently listed in `research/optimization-map/journey-matrix.csv` has a tracked classification and durable evidence.
2. Every supported user mutation has real-backend desktop and mobile Playwright proof unless the tracked manifest gives a technically valid `not_applicable` reason.
3. Every write surface proves the appropriate admin/editor/viewer behavior in both the browser and backend.
4. Every supported offline mutation proves lost-ack replay, repeated reconnect, conflict, deleted-target, garden-scope, and expiry behavior where applicable.
5. Every cross-domain workflow asserts its final PostgreSQL state. Media and terrain workflows also assert filesystem state.
6. Every provider-backed workflow covers disabled, success, timeout, malformed, and quota/degraded behavior with deterministic local adapters and no real outbound requests.
7. Critical journeys pass keyboard, focus, semantic accessibility-tree, automated WCAG, reduced-motion, zoom, and mobile touch checks.
8. Performance-sensitive journeys have reproducible fixtures, query counts/plans, payload measurements, browser timings, and reviewed regression budgets.
9. The cumulative backend suite, production frontend build, and complete journey suite pass from a clean checkout.
10. Each phase is independently reviewed after implementation by a fresh agent whose selected and reported model is **GPT-5.6 Sol Ultra**. A substitute model does not satisfy the gate unless the user explicitly changes this requirement.

The program must not claim "complete accessibility" from screenshots or axe results alone, must not claim a universal speedup from one timing run, and must not turn unsupported offline/provider behavior into implied product support merely to fill a matrix cell.

## Appendix B: Scope And Journey Inventory

Use the journey IDs already defined in the ignored optimization map. Do not rename them during this program.

| Area | Journey IDs | Owning phase |
|---|---|---:|
| Harness and evidence contract | All IDs, no behavior changes | 0 |
| Garden context, Map, plants, layout | `A3`, `M1`, `M2`, `M3`, `M4`, `CROSS-01` | 1 |
| Attention, tasks, calendar, notifications, weather work | `D1`, `D2`, `D3`, `D4`, `D5`, `R1` | 2 |
| Journal, issues, harvest, media, photo-to-action | `P1`, `P2`, `P3`, `P5`, `I2`, `I3` | 3 |
| Planning, inventory, procurement, care, reports, exports | `P4`, `P6`, `I1`, `L1`, `L2`, `R2`, `R3` | 4 |
| Authentication, invitations, sessions, roles, settings | `A1`, `A2`, `A4`, `C1`, `C3`, `C5`, `CROSS-02` | 5 |
| Offline failure recovery and destructive integrity | `OFF-01`, `C2`, `INT-01`, cross-cutting replay paths | 6 |
| Providers, ShadeMap, terrain, LiDAR | `M5`, `I2`, `I3`, `I4`, `C4`, `C6`, provider side of `D5` | 7 |
| Accessibility and responsive interaction | Cross-cutting representative states from all IDs | 8 |
| Performance, scaling, final closure | All performance-sensitive IDs and the full matrix | 9 |

Overlapping IDs are intentional. Earlier phases prove ordinary user workflows; later phases prove cross-cutting failure, provider, accessibility, or scale variants without duplicating ownership.

## Appendix C: Program Branch And Pull Request Rules

For every phase:

1. Start only after the previous phase is merged.
2. Refresh and branch from the latest remote main:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c codex/journey-program-phase-<N>-<short-name>
```

3. Confirm a clean baseline with `git status --short` before edits.
4. Keep the phase diff limited to its journey IDs, shared harness changes genuinely required by that phase, tests, and documentation.
5. Write failing tests or an explicit failing coverage assertion before behavior fixes.
6. Do not source `/etc/gardenops.env`, do not point tests at production PostgreSQL, and do not let browser tests contact non-loopback providers.
7. Run the focused phase suite, affected backend tests, production frontend build, and cumulative journey smoke before review.
8. Complete the GPT-5.6 Sol Ultra validation gate in this document.
9. Run the GardenOps documentation inventory and git push sanitizer before commit/push.
10. Open one PR for the phase. Merge only after local gates, Sol Ultra validation, and GitHub checks pass.

Suggested PR titles:

```text
Phase 0: Add complete journey evidence harness
Phase 1: Prove garden and map workflows end to end
Phase 2: Prove daily attention and task workflows
Phase 3: Prove observation and issue workflows
Phase 4: Prove planning and reporting workflows
Phase 5: Prove identity and role workflows
Phase 6: Harden offline and failure recovery
Phase 7: Prove provider and terrain workflows
Phase 8: Complete accessibility interaction pass
Phase 9: Enforce performance budgets and close journey map
```

## Appendix D: Required Test Environments

### Database

All backend and E2E mutations must run through the existing disposable PostgreSQL runner:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --command -- <command>
```

The complete journey runner created in Phase 0 must self-wrap through that runner, as `scripts/run_ui_flow_map_e2e.sh` already does. It must reject:

- port `5432`;
- missing disposable URL, marker, or PostgreSQL system identifier;
- a parent process that is not `run_fast_postgres_tests.py --command` in child mode;
- `APP_ENV` other than exact `test`;
- an artifact path outside ignored `research/` or private `/tmp`;
- inherited production secrets, provider credentials, proxy variables, or production media paths.

### Browser Matrix

Use named profiles rather than ad hoc viewport values:

| Profile | Required configuration |
|---|---|
| Desktop | Chromium, `1440x900`, mouse and keyboard |
| Mobile | Chromium emulating Pixel 7 dimensions and touch behavior, portrait |
| Tablet | Chromium, `768x1024`, touch behavior; required beginning in Phase 8 |
| Reduced motion | Desktop and mobile with `prefers-reduced-motion: reduce`; required beginning in Phase 8 |
| Zoom | Desktop at browser zoom equivalent of 200 percent; required beginning in Phase 8 |

Do not label a resized desktop context as mobile. Preserve real mobile context settings, touch, and viewport metadata in the evidence manifest.

### Roles

Seed separate admin, editor, and viewer users. Never mutate one user's role during a journey to simulate three identities. For every write-capable feature:

- admin success is required where the feature is admin-owned;
- editor success is required for ordinary garden writes;
- viewer UI must not present a misleading enabled write control;
- a direct viewer API write must return the expected denial and leave no side effect;
- data created in Garden A must not appear to an unauthorized user or Garden B.

### Network Isolation

Every browser context must abort non-loopback HTTP, HTTPS, and WebSocket traffic before transmission. Deterministic provider servers must bind to `127.0.0.1` on dynamically allocated non-5432 ports. Record blocked request attempts in the private run manifest and fail the run if any external request was attempted unexpectedly.

## Appendix E: Tracked Evidence Contract

Phase 0 creates these tracked artifacts:

- `tests/journey_coverage.yaml`
  - One record for every stable journey ID.
  - Fields: `id`, `phase`, `desktop`, `mobile`, `roles`, `offline`, `provider`, `database`, `filesystem`, `accessibility`, `performance`, `evidence`, `notes`.
  - Dimension values: `required`, `proven`, or `not_applicable`.
  - `not_applicable` requires a non-empty reason and cannot be used for a currently supported behavior.
- `scripts/check_journey_coverage.py`
  - Parses YAML structurally with PyYAML.
  - Rejects duplicate/missing IDs, unknown fields, invalid states, evidence paths that do not exist, and unjustified `not_applicable` entries.
  - In Phase 0, open dimensions may remain `required`. In Phase 9, no `required` values may remain.
- `docs/testing/journey-validation-ledger.md`
  - One concise row per phase with PR, final head SHA, focused test command, cumulative command, reviewer model, reviewer disposition, and accepted residual limitations.
  - Never include tokens, credentials, private screenshots, raw database contents, or live host details.

Private evidence remains under:

```text
research/optimization-map/runs/complete-journeys/<run-id>/
research/optimization-map/reviews/phase-<N>/<review-id>/
```

Each private run directory must include a machine-readable manifest with:

- git SHA and dirty/clean state;
- phase and journey IDs;
- UTC start/end timestamps;
- browser profile and role;
- assertions passed/failed/skipped with reasons;
- browser console/page/request failures;
- blocked outbound network attempts;
- database system identifier hash or non-secret run identity;
- database and filesystem postcondition summary;
- screenshot/trace relative paths;
- timing samples when applicable.

## Appendix F: Global Definition Of A Journey Test

A journey is not proven by opening a screen. A complete mutation journey must:

1. Sign in through the real session UI unless the journey explicitly begins after authentication.
2. Reach the feature through user-visible navigation.
3. Perform the action through visible controls using role/name selectors where practical.
4. Assert loading, success, empty, error, retry, and stale-response behavior relevant to that journey.
5. Navigate away and back or reload where persistence is part of the promise.
6. Inspect connected surfaces such as Today, Tasks, Journal, Notifications, Calendar, reports, or badges.
7. Query the disposable database after browser completion and assert exact durable side effects.
8. Assert filesystem additions/removals for media and terrain.
9. Assert no browser errors, unhandled promise rejections, duplicate IDs, horizontal overflow, or unexpected outbound requests.
10. Clean up only through disposable runner teardown, never by targeting a named live database.

Prefer role/name/label selectors. Add `data-testid` only where a stable semantic selector cannot distinguish repeated domain rows; do not make implementation selectors the primary accessibility contract.

## Appendix G: Global Validation Commands

Use these after every phase, adjusting focused test paths for that phase:

```bash
git diff --check origin/main...HEAD
RUFF_CACHE_DIR=/tmp/gardenops-ruff-cache .venv/bin/ruff check gardenops tests scripts
RUFF_CACHE_DIR=/tmp/gardenops-ruff-cache .venv/bin/ruff format --check gardenops tests scripts
cd frontend && npm run build
cd ..
.venv/bin/python scripts/check_journey_coverage.py --allow-open
.venv/bin/python scripts/run_fast_postgres_tests.py --full-suite --shards 4
scripts/run_complete_journeys_e2e.sh --through-phase <N>
.venv/bin/python .codex/skills/gardenops-documentation-upkeep/scripts/docs_impact_inventory.py --base origin/main
.venv/bin/python .codex/skills/gardenops-git-push-sanitizer/scripts/git_push_sanitizer.py --pre-push
```

If Ruff cannot write its repository cache, keep `RUFF_CACHE_DIR` under `/tmp`. Do not weaken checks or omit the reason when a command is genuinely unavailable.

## Appendix H: Mandatory GPT-5.6 Sol Ultra Phase Gate

This gate applies to **every phase, including Phases 0 and 9**.

### Reviewer identity

- Dispatch a fresh delegated review agent after implementation and local validation.
- Select the exact model named **GPT-5.6 Sol Ultra** in the available agent runtime.
- Record the orchestration-selected model and reasoning tier plus the reviewer's
  visible runtime identity in the validation ledger and PR body. The accepted
  orchestration route is authoritative when the runtime exposes only a generic
  model-family identity to the delegated agent.
- The implementing agent cannot self-certify as the Sol Ultra reviewer.
- If dispatch with `gpt-5.6-sol` and `ultra` is rejected or unavailable, mark the
  phase `BLOCKED: required reviewer unavailable`. Do not silently substitute
  Terra, generic Ultra, Luna, or the main implementation model.

### First review pass

Give the reviewer a fresh context containing only:

- this plan;
- the phase journey IDs and acceptance criteria;
- `git diff --merge-base origin/main HEAD`;
- relevant source/test files;
- the private evidence manifest path, not production data;
- exact commands already run and their exit status;
- permission to run read-only inspection and disposable tests;
- an explicit prohibition on production mutation, merge, push, and secret access.

Use this prompt, replacing placeholders:

```text
You are the independent post-implementation validator for GardenOps Phase <N>.
The required reviewer model is GPT-5.6 Sol Ultra. Confirm your reported model.

Perform a hostile, read-only review of final head <SHA> against
docs/superpowers/plans/2026-07-11-complete-journey-verification-optimization.md.
Review only Phase <N> and its interactions with previously completed phases.
Treat the implementation and its claimed evidence as potentially incomplete.

Required lanes:
1. actual end-to-end user journey and ease of use;
2. frontend/backend/data wiring and stale-state behavior;
3. authorization, isolation, offline/failure behavior where applicable;
4. accessibility on desktop and true mobile;
5. horticultural/domain correctness where applicable;
6. test honesty, disposable-environment safety, and missing postconditions.

Do not edit files, push, merge, deploy, source production environment files, or
mutate live services. You may run tests only through the repository's disposable
test runners. Report Critical, Important, Minor, Rejected, and Open Questions,
with file/line or evidence-manifest references. End with exactly one disposition:
PASS, PASS WITH DOCUMENTED LIMITATIONS, or BLOCK.
```

### Findings and re-review

- Any Critical or Important finding blocks the phase.
- The implementer fixes valid findings and adds a regression test before changing behavior.
- Reject unsupported findings explicitly with repository evidence; do not code around speculation.
- Rerun focused, cumulative, build, and sanitizer gates after fixes.
- If any code or test changes after the first review, dispatch a **new fresh GPT-5.6 Sol Ultra agent** against the new final SHA. The prior disposition is stale.
- Minor findings may be accepted only when the reviewer agrees they do not violate phase acceptance criteria and the ledger names the limitation and owner phase.
- A phase closes only with `PASS` or `PASS WITH DOCUMENTED LIMITATIONS`, zero unresolved Critical/Important findings, and a recorded reviewer identity.
- A ledger-only closure commit may record an already completed review without
  invalidating it; `Final head` names the reviewed implementation SHA. Any code,
  test, plan-contract, or behavioral documentation change still requires review.

---
