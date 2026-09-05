# Garden Experience Implementation Plan

Baseline: remote main `715557d`, 2026-09-05. Branch: `codex/garden-experience`.
Status: all 21 deliverables implemented, reviewed and validated; ready for PR.

## Outcome And Boundaries

A gardener can find a place, inspect a plant, record what actually happened,
recover an interruption, revisit the history, and use it when deciding what
to plant next. Implement all 21 research ideas through existing forms, domain
commands, and map-first navigation. No new dashboard, generalized router,
recursive location model, AI calls, notification generator, or dependency.
Keep plant rows compact and retain direct Edit and existing permission checks.
Research artifacts remain ignored; this plan and behavior documentation are tracked.

## Complete Scope

| ID | Deliverable | Work package |
| --- | --- | --- |
| O1 | Contextual observation composer | Capture |
| O2 | Place search and unplaced-plant destination | Navigation |
| O3 | Manual issue recording with optional diagnosis | Navigation/Capture |
| O4 | Actual occurrence date on task completion | Task truth |
| O5 | Full, persistently scoped plant history | Capture |
| O6 | Explain observed versus catalog bloom timing | Task truth |
| F1 | Preserve quick-task query, scroll and focus | Task UI |
| F2 | Restore one unfinished journal draft per identity/garden | Capture |
| F3 | Recover journal photo submissions | Capture |
| F4 | Selected grouped pruning/fertilizing offline | Task UI |
| N1 | Container links and markers open the same details | Navigation |
| N2 | Explicit selected-area Details entry | Navigation |
| N3 | Read-only plant summary from plant names | Navigation |
| N4 | Session-only remembered subview per main tab | Navigation |
| S1 | Recorded stock at candidate inspection | Planning |
| S2 | Consistent historical harvest dates and units | Harvest |
| S3 | Past outcomes at candidate inspection | Planning |
| T1 | Bloom observation placement selection | Task truth |
| T2 | Observation-language labels and filters | Task UI |
| T3 | Distinguish deferral from season closure | Task truth/UI |
| M1 | Human-readable Matrix save receipts | Assistant |

## Journey 1: Find, Inspect, And Record In The Garden

Start on Map. Search accepts a plant name, ordinary plot ID/name, container
ID/name, or area name. Merge a bounded local place result set with existing
plant search. Each result identifies its kind and parent area where relevant.
Selecting a mapped location selects its area if necessary and opens the same
details as clicking its marker. Standalone/unmapped containers open details
without invented coordinates. Unplaced plants open a plant summary with No
current location and the existing placement action for editors. Search never
silently ends with no meaningful destination.

Plant names in the database, map popover and container/drawer entry points open
a read-only summary. Show identity, current locations, recorded seasonal status,
and existing history preview. Explicit external-reference and Edit actions remain.
Editors can Record observation and Report issue. Viewers can inspect history
but cannot see/use mutation controls; server authorization remains authoritative.
The summary uses the existing dialog shell, not a new page or routing framework.

Record observation opens the existing journal composer with the selected plant
and explicit selected place. A plant with several places must not preselect all
of them as observed. If entered from a plant record alone, show plant context
and leave ambiguous location unselected. Context remains editable. Capture from
a plot alone records plot context without guessing all plants. On save/cancel,
return focus and keep the map position. Guard any asynchronous load against
garden switches and closed dialogs.

Report issue opens the existing manual form with the same explicit context.
Photo diagnosis is a secondary, clearly named action, not a prerequisite.
Save uses existing normal/offline issue handling and leaves a discoverable record.

Selecting an area reveals its markers as today. A selected-area Details action
reveals and focuses the existing area section; it does not automatically obscure
the map. Container list precedes setup actions, with layout editing collapsed.
Changing main tabs restores that tab's last enabled subview for this session.
Explicit action destinations win. Garden/identity changes reset this memory.

## Journey 2: Interrupted Observation And Complete History

One new-entry, unsubmitted text draft per user/garden is retained locally. Store
date, event, notes, selected IDs and a photo-count hint, not photos in this draft.
Restore deliberately in the existing composer, with a Discard draft action.
Validate restored IDs against current choices; report missing links rather than
silently applying them elsewhere. Tell the user to reselect photos if applicable.
Editing an existing entry must never overwrite the new-entry draft. An explicit
new contextual entry must not silently overwrite an unrelated existing draft:
offer Resume or Discard before replacing it. Only explicit Save submits.
Local storage errors must leave current text accessible and display a clear error.
Clear private drafts during existing sign-out cleanup; garden switch isolates them.

For new journal saves, persist the existing entry-plus-media queue operation
before closing, even when online, then trigger existing sync immediately.
Only call it locally saved after IndexedDB commit. Retain the same operation
and attachment identities through partial uploads/retry. Show pending versus
server-saved through existing indicators. If storage is full, retain the form.
Do not automatically enqueue retries of a prior ambiguous direct create.
Existing-entry editing stays on its present path; preserve its form/files on
upload failure so it is not confused with a new queued entry.

After successful submission clear only its matching unsent draft, refresh normal
lists after sync, and keep failure recoverable in the current indicator. Signing
out follows existing queued-work cleanup rules. User/garden switches must never
retarget queued work or reopen another identity's draft.

View all from plant history opens Journal with a visible clearable plant/place
scope, retained through search/type/date changes and pagination. Clear resets
offset and scope; garden switch also resets. Historical places remain as logged,
even after a plant moves. An empty history is an ordinary empty state, and errors
offer retry rather than pretending there are no records. Back/close preserves
the useful origin where the existing navigation permits it.

## Journey 3: Record Real Work And Observations

In the existing bloom/grouped completion sheet, add optional `occurred_on`
(YYYY-MM-DD), defaulting to the browser's local today. Backend validates a real
date, rejects future occurrence dates against the application's established
local date convention, and defaults omitted values compatibly. Audit/completion
timestamps remain actual action time; only journal/observation occurrence uses
the supplied date. Season-negative bloom outcomes apply to the chosen occurrence
year and disclose it. Do not allow backdating to fabricate current-year evidence.

For a single plant with several placements, the bloom dialog asks where it was
seen. Introduce optional `observed_plot_ids`; validate against permitted current
placements of the selected plant and the task garden. Empty explicitly means
plant-level observation without a claimed place; omission must not imply every
linked plot was inspected. One-location UI can preselect its sole valid location.
Plant-level bloom completion may close the task after one location is observed;
unselected locations remain unchanged. No specimen-count or new task status.
Keep journal side effects consistent for REST, batch, offline and assistant paths.

Grouped pruning/fertilizing can queue one explicitly selected subset offline.
Retain revision checks and one unresolved action per task. On sync, record only
selected plants; remaining plants stay actionable. Reject stale/deleted targets
through existing recoverable failure handling, never silently rebase a write.
Propagate occurrence date and observation scope through offline serialization.

Weekly bloom deferral remains fast and does not claim the user inspected it.
Stop appending new observational `not_yet_events` for generic snooze; leave old
history untouched. Season closure is a secondary action with explicit consequence:
no new bloom checks for this plant in that year. Do not claim existing checks
were all cancelled. Use a confirmation within the existing sheet.

Generated bloom details show a short deterministic timing explanation, based on
metadata captured at generation: local observed month(s) versus catalog months.
Do not retrofit historical tasks with guessed provenance. Retain the existing
local-history timing algorithm and no additional notifications.

Use Not seen in YEAR instead of gone/since for negative observation evidence.
Preserve internal status keys and filter predicates, with accurate visible labels.
Do not hide missing records by default or infer removal/death. Keep quick-task
search and scroll/focus through successful rerenders; reset on sheet close or
garden switch. No matches remains distinguishable from no remaining tasks.

## Journey 4: Decide What To Plant And Review A Season

From an existing planner candidate and target plot, open candidate inspection.
Fetch a bounded list of inventory explicitly linked to that plant. Show Recorded
stock with original type/unit and quantity; do not equate seed packets with plants.
An editor can open the existing stock-planting form with item and destination
prefilled, confirm quantity, then use its atomic stock/placement/journal command.
Retain server-side stock validation and stable operation ID. Refresh candidate,
inventory and map after success. Zero stock, insufficient stock, viewer roles and
stale response after garden change have explicit ordinary states.

In the same inspection, optional Recorded here shows a bounded dated set of
existing journal and harvest outcomes matched to plant AND historical plot.
Display original notes, units, and recorded quality without inferred causality or
ranking. Empty means no records found, not failure or success. Independent read
failure must not block the fit result or stock action. No new timeline or scoring.

The Harvest view gets a year choice and synchronized list/summary boundaries.
Default current year; selecting a year sets that year's list dates. If custom
date filters remain, summary uses that exact range including cross-year ranges,
or explicitly suppresses an unsupported summary rather than showing false zero.
Prefer exact-range support using existing API date filters. Aggregate quantities
by unit, never kg plus bunches; show plant/unit rows rather than mixed-unit bars.
Shared-entry quantities must be labeled as shared, not duplicated into a claimed
garden yield. No best-performer claim or zero-yield inference from absent logs.

## Journey 5: Matrix Confirmation

After explicit save succeeds, render a deterministic receipt naming the action,
plant(s), actual placement(s), quantity/unit/date where relevant. Derive it from
validated proposal and confirmed command outcome; do not echo unconfirmed prose.
Partial completion names only selected plants. Retain reference for retries.
Delete says deleted only after success. Replay returns the same receipt; no new
AI call, deep-link subsystem, or relaxation of approval/authentication rules.

## Implementation Ownership And Sequence

1. Complete two independent plan reviews; amend this document once with concrete
   findings. Keep a short review disposition section, not repeated review loops.
2. Parallel bounded packages with disjoint files: task backend and generator;
   journal capture/history module; harvest/planning modules; assistant receipts.
   Main orchestrator owns app.ts, navigation, shared types/API, i18n/CSS, task UI,
   and final integration. Workers report required shared-file hooks, not competing
   edits. Interfaces are integrated before browser validation.
3. Run focused suites as packages finish. Run frontend production build and
   repository static/lint checks after integration. Fix real failures; do not
   weaken checks to conceal a regression.
4. Run a real browser journey with disposable PostgreSQL and seeded synthetic
   data; desktop and narrow mobile viewports. At minimum cover the connected
   find/inspect/record/history loop; additionally cover changed task and offline
   persistence contracts with focused integration/browser checks below.
5. One Astra Ultra implementation review after all packages are integrated.
   Give the reviewer plan, complete diff and validation evidence. Fix findings
   and rerun affected checks. No per-file review cycles or full framework rewrite.
6. Update completion ledger and smallest user documentation, inspect full diff
   and staged paths for private/generated data, `git diff --check`, commit and
   push a PR per standing user preference. No production deployment in this task.

## Validation And Completion Ledger

Use `scripts/run_fast_postgres_tests.py` for isolated tests and command-mode
real-backend browser runs. Read its CLI first. Never point seed/truncate scripts
at local user data. Reuse installed Playwright/Chromium and existing test helpers;
one focused experience journey script is acceptable, no new test framework.

| Journey | Required proof | Status |
| --- | --- | --- |
| Find/inspect | Place search; unplaced plant; container link; read-only viewer; map context and mobile fit | Implemented; search/summary/mobile browser proof and full-suite permission/navigation contracts passed. Container/viewer behavior was reviewed, not exercised in the connected admin browser run. |
| Capture/history | Prefills; manual issue; unsent draft restore/discard; scope survives pagination; photo failure/retry without duplicate entry | Implemented; ten capture-browser and ten queue-runtime scenarios passed; connected mobile saves and photo rendering passed. |
| Task truth | Backdate; invalid/future date; allowed/forbidden plot scope; untouched unobserved plot; grouped partial; stale/offline replay; closure meaning; provenance | Implemented; full backend suite, focused task-browser current-place/group/offline/focus checks and connected mobile partial pruning passed. |
| Planning/harvest | Linked stock with units; prefilled atomic planting; bounded historical context; past/cross-year totals; mixed units and shared entries | Implemented; harvest/backend tests and connected first-use stock prefill/cancel/save passed. |
| Matrix | Readable success/delete/partial receipts; deterministic replay and no extra provider call | Implemented; focused assistant/Matrix tests and final full suite passed; no live Matrix/provider call claimed. |
| Integrated | Frontend build; focused Python tests; lint/static checks; browser screenshot and console inspection; Astra Ultra final review | Final Ultra review completed; findings fixed. Build, all four backend shards, lint/static checks and 23 connected browser checks passed. |

Review findings, exact checks and failures are recorded below as they occur.
An idea is complete only when its visible entry point, dependent API/data path,
success/error handling and relevant validation are connected. No placeholder
buttons, dead callbacks, silently skipped packages, or claims from source-only tests.

## Accepted Review Amendments

Reviewed by Astra Ultra agents `01a07313-d401-78b0-b88b-04398ba216f7`
(journeys) and `01a07313-d2f2-7802-bccf-981dc7f24c62` (data/recovery).
Both required these bounded amendments before implementation. These concrete
contracts supersede looser wording above; all 21 deliverables remain in scope.

- History is a permanent action in summary AND existing Edit, even for zero to
  three entries. Default plant-only history spans historical locations. A local
  Return action restores the origin; close/suspend the origin before navigating.
- All capture task types, including single/final pruning and fertilizing plants,
  use the date-capable existing completion sheet with today preselected. This
  chooses one consistent flow over adding a second alternate-date action everywhere.
- Journal/issue/stock transitions use the existing modal stack or close/suspend
  the origin explicitly. Only the top surface handles Escape. Manual diagnosis
  preserves entered issue fields/context on cancellation; successful saves expose
  the result. Mobile Log journal and plot/container Record observation use the
  same contextual opener and stay on Map.
- A small app-level location dispatcher serves search, plant, Journal, Harvest
  and planner entry points. Multi-location plant search opens a summary with
  explicit choices. Unavailable placement destinations show a reason.
- Unsent drafts include title, selected fields and photo-count hint. Resume,
  Discard and Cancel prevent unintended replacement. Chip removal remains usable
  repeatedly; touched labels are associated with inputs and chips use names.
- Queue persistence resolves only on transaction completion. Capture identity,
  garden and session generation before asynchronous serialization. Reject changed
  context before commit; queue owner identity is checked before replay. Integrate
  private draft cleanup with logout, auth expiry and pre-login fail-closed cleanup.
- Deserialize attachments non-destructively. Track confirmed parent entry in the
  existing queue payload so attachment failure cannot renew/recreate that parent.
  Retain stable operation IDs on ordinary retry. Expose the saved entry and allow
  discarding remaining failed attachments; adding corrected photos can use its
  existing edit form. Deleted-parent recovery requires explicit new submission.
- `observed_plot_ids` is authoritative through shared command and every adapter.
  Explicit empty scope persists a plant-only metadata marker, honored by edit/
  delete reconciliation; it must not infer a sole plot. Omitted new scope defaults
  to no inferred place for new task capture; legacy journal records keep legacy
  reconciliation. Validate actual plant-placement membership, not only garden.
- Optional new task fields are excluded from idempotency fingerprints when absent.
  Explicit dates and empty scope remain fingerprinted. Never resolve today before
  fingerprinting. Add one old-operation replay regression.
- Introduce one small observation clock helper: `GARDENOPS_TIMEZONE`, falling back
  to existing `MATRIX_TIMEZONE`, then `Europe/Oslo`. Invalid zones fail explicitly.
  Use it for task date defaults/validation and seasonal observation classification;
  retain test frozen-clock support. Task responses expose `observation_timezone`
  so the dialog computes today with Intl in that zone, including offline use.
  Matrix uses the same configured fallback. Chosen occurrence dates never change
  on replay; closure year derives from occurrence date, not task due date.
- Assistant ambiguous not-yet phrases cannot silently propose season closure.
  Explicit closure proposals disclose outcome/year/scope before save. Receipts
  are stored from actual command results and immutable confirmed context.
- Candidate inspection is an explicit small dialog using the current modal shell,
  with independently loaded fit/stock and collapsed Recorded here. It can share
  plant summary building blocks but must not create a new general inspector API.
  Stock cancel returns to the candidate/plot. Deduplicate harvest journal mirrors
  using linked_harvest_entry_id; show bounded-result limits honestly.
- Harvest explicit date bounds take precedence over default year (inclusive);
  absent explicit bounds use selected/current year. If monthly sums are returned,
  group by YYYY-MM and unit. Include shared-attribution quantities/counts; garden
  totals count each entry once. Apply identical quality and date filters to list
  and summary. No summing plant-attributed rows into garden yield.
- Season closure shows the year and links to scoped history for correction via
  existing record deletion/edit behavior. Rescheduling alone is not presented as
  undoing the journal-based generation suppression.

Ownership refinement: Capture owns journal/issue modules and new draft helper;
Queue is a separate bounded package owning offlineQueue/offlineFeature and its
tests. Navigation owns app.ts/AppContext and touched map/summary components.
Task backend owns command/capture/observation modules; Assistant owns only its
service and tests and coordinates new command arguments. Main owns shared API
types/models/i18n/CSS, task UI, browser integration and documentation. Workers
must request shared hooks rather than edit another package's files.

Baseline verification: 38 task-generator unit tests passed using the isolated
PostgreSQL runner; frontend typecheck passed. No implementation proof claimed yet.

## Integration Verification

- Final full backend suite: `scripts/run_fast_postgres_tests.py --full-suite --shards 4`
  passed all four shards after review fixes (157.7 seconds, disposable PostgreSQL 17).
- Initial full run found two source-shape assertions for place-picker fallback
  and completion modal options. Replacements assert permission ordering,
  the new unavailable-destination explanation, modal parent and history wiring.
  No runtime check or security boundary was removed to make the suite pass.
- Focused Chromium component tests: `node tests/journal_capture_frontend.cjs`
  passed ten checks; `node tests/offline_queue_runtime.cjs` passed ten checks,
  including transaction abort, owner switching, partial attachment retry and
  retained confirmed parent IDs.
- Repository Ruff lint/format, environment documentation, pinned GitHub Actions,
  backend integrity on a migrated disposable database, and push sanitizer passed.
- Final frontend production build and its included source/security/dist checks passed.
- Integration fixes: preserve quick-task search focus; preserve mobile camera
  position through history return; initialize Inventory lazily before planner
  stock planting; name untitled recorded events; wrap compact popover actions.
- Authentication transport failures must preserve local work behind a retry
  gate. Explicit authentication rejection and sign-out retain fail-closed
  private-work cleanup. Both the lazy entrypoint and loaded app must obey this.

## Final Adversarial Review Disposition

Astra Ultra reviewer `01a0732b-8bc5-70e0-b06a-0528437f4746` reviewed the full
implementation, including new source files and all 21 deliverables. Six findings
were addressed directly without another architecture or testing framework:

1. Ownerless legacy queue rows: show a generic count and explicit confirmed
   discard, never expose or replay content under guessed ownership. Preserve
   these rows through account cleanup. Ten queue-runtime scenarios passed.
2. Matrix deferral mistaken for bloom: require affirmative observation wording,
   distinguish latest clarification, and return unchanged-task guidance for
   deferral. Negated/questioned closure cannot close a season. Full suite passed.
3. Stale task-place links: fetch current plant assignments when opening bloom
   completion; use only confirmed same-session cache offline, otherwise plant-only.
4. Grouped bloom attribution: offer/accept only the intersection of current
   places for every selected plant. Backend rejection and browser subset tests passed.
5. Existing-entry photo retries: retain operation IDs, confirmed assets and
   successful links through upload/link failure. Both retry browser cases passed.
6. Quick-task return focus: restore search after the completion modal releases
   its parent. Actual quickActionsFeature browser regression passed.

Additional integration findings fixed: native image URLs retain authorized garden
scope (including bulk summaries); IndexedDB cleanup exceptions abort the
transaction and use the login retry gate without unhandled browser errors.
The actual lazy auth entrypoint passed transport failure, 401/403 cleanup, and
cleanup-failure retry scenarios. No authorization or release gate was relaxed.

## Connected Browser Outcome

The managed disposable PostgreSQL/FastAPI/Vite run finished with exit 0 in
97.7 seconds. The original task-history checker and all 23 experience checks
passed, with 23 inspected screenshots and real API record assertions. It covered
both desktop and mobile capture/history/draft/issue/unplaced/photo flows, plus
mobile offline backdated partial pruning and desktop first-use stock planting.
Console/API audits passed and all test servers were stopped. Generated reports,
record IDs and screenshots remain ignored under `research/experience-e2e/`.

The connected runner intentionally uses an unauthenticated synthetic admin garden,
not production. Viewer authorization, Matrix/provider traffic, container/area
destinations and broader planner variants were not exercised there. Partial
photo failure, identity isolation, quota failure and current-placement choices
have separate focused browser/backend proof. The stock journey checks existing
unit-consumption semantics, not conversion to specimen count.

One earlier desktop draft-resume attempt failed before Journal readiness. The
final test waits for loaded Journal data and then succeeds with one click, with
the draft unchanged across reload. Its earlier handler-versus-context-guard cause
was not conclusively reproduced; no forced clicks or repeated-click workaround
were used to claim success. The auth-disabled warning banner remained visible.
