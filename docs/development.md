# Development

This guide covers the normal development loop and PR checks.

## Local Loop

Run the backend:

```bash
set -a
. ./.env
set +a
.venv/bin/python -m uvicorn gardenops.main:app --host 127.0.0.1 --port 8000
```

Run the frontend dev server in another shell:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`.

## Repo-Local Codex Skills

GardenOps-specific Codex skills live in `.codex/skills/` so the workflow stays
with the repository instead of a single developer's global Codex directory.
When these skills change, keep the repo-local copies authoritative and avoid
adding GardenOps-specific skills under `/root/.codex/skills`.

The git push sanitizer's reviewed implementation is tracked in
`scripts/git_push_sanitizer.py`; the local `.codex` skill entrypoint delegates
to that script. Run it before staging, committing, pushing, or opening/updating
a PR. High-confidence token detectors remain hard blockers, while broad
`SECRET_ASSIGNMENT` matches only allow narrow inline suppressions for reviewed
fixtures.

## PR Checks

Run these before opening a PR:

```bash
python scripts/git_push_sanitizer.py
set -a
. ./.env.test.local
set +a
cd frontend
npm run build
cd ..
uv run ruff check gardenops tests
uv run ruff format --check gardenops tests
uv run python scripts/check_env_docs.py
python scripts/check_github_action_pins.py
python scripts/check_innerhtml_sinks.py
uv run python scripts/check_backend_integrity.py --format text
uv run python -m pytest tests/ -q --tb=short
```

For a faster deploy or local sanity check, run:

```bash
set -a
. ./.env.test.local
set +a
scripts/run_backend_smoke.sh
```

The smoke script covers startup-critical backend behavior and explicitly opts
into the test-only password hash cost with `AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true`.
Run the full backend suite before merging larger backend or security changes.

For the fastest local full backend run, use the disposable Postgres runner:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --full-suite --shards 4
```

The runner creates a temporary local Postgres cluster, generates test-only
credentials and databases, runs the shard suite, and removes the cluster on
success. It does not read `/etc/gardenops.env` or use the live database.

Use `--command --` to run one database-backed command against the same
disposable, migrated database:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --command -- command [args...]
```

The child command receives `DATABASE_URL`, `GARDENOPS_TEST_POSTGRES_URL`, and
`GARDENOPS_DISPOSABLE_POSTGRES_URL`. Cleanup runs after both successful and
failed commands. The runner cleans up its database cluster; an arbitrary child
command remains responsible for terminating any daemonized grandchildren it
starts. The repository E2E runners install their own process-group traps for
FastAPI and Vite.

For focused seeders with a stricter database-name guard, select an allowlisted
dedicated command database before `--command`:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py \
  --command-database gardenops_task_history_e2e_test \
  --command -- scripts/run_task_completion_history_e2e.sh
```

For the dedicated Attention and task-history databases, the runner exports the
matching seeder URL, assigns free backend/frontend ports, and uses an isolated
log directory inside the disposable run artifacts.

To verify cleanup behavior after runner changes:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke after-start
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke during-migration
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke during-pytest
```

As a normal-durability fallback, provision one disposable database per shard,
named by appending `_shard0`, `_shard1`, and so on to
`GARDENOPS_TEST_POSTGRES_URL`'s database name. Then run:

```bash
set -a
. ./.env.test.local
set +a
uv run python scripts/run_backend_shards.py --shards 4
```

For the fallback sharded runner, the default file-level split is the fastest
validated mode on the live host. Use `--scope node` only when whole-file shard
balance becomes a problem.

## Frontend Security Checks

`npm run build` also checks for:

- unsafe raw HTML sinks
- invite-token storage regressions
- auth-gate status flow regressions
- AI chat client contract regressions
- map-object editor contract regressions
- Attention Today panel contract regressions
- TypeScript errors
- production bundling errors
- sourcemap leakage, including inline `sourceMappingURL` references
- stale generated asset references

If a change intentionally adds a raw HTML sink, document it in
`frontend/security/innerhtml_allowlist.txt` and explain why the sink is safe.
The guard detects `innerHTML`/`outerHTML` dot and bracket assignment,
`insertAdjacentHTML`, and reviewed dynamic HTML helper calls including aliases.

## Targeted Frontend E2E Checks

For broad shell, navigation, responsive-layout, or cross-feature changes, run
the authenticated navigation/read map against a disposable database:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py \
  --command -- scripts/run_ui_flow_map_e2e.sh
```

The command seeds a Pro admin plus editor and viewer memberships, signs in
through the real session-auth UI, and exercises the real FastAPI routes in
desktop Chromium and a touch-enabled Pixel mobile context. It proves seeded
content renders across Map and Today, notifications, every Garden, Activity,
and Insights subview, and all seven admin sections. It also checks editor/viewer
identity, write affordances, and admin-section denial. Screenshots, complete
surface captures, traces, the manifest, and the final database snapshot are
written to a unique run directory below the gitignored
`research/optimization-map/runs/` directory, so a later run does not erase
earlier evidence. Trace archives contain disposable test-session data and are
written with owner-only permissions; do not publish them.

This is navigation/read and role-boundary evidence, not mutation coverage for
every feature. Run the focused task-completion and Attention journeys below for
their write-side database assertions, and add a focused real-backend journey
when changing another feature's create/update/delete behavior.

The seed refuses to run unless `APP_ENV=test`, `AUTH_REQUIRED=true`,
`GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE=1`, the database URL exactly matches the
disposable runner URL, the TCP port is not 5432, and a runner-issued marker is
verified from the connected database. The shell script is intentionally usable
only through `run_fast_postgres_tests.py --command`; never point it at a
persistent or shared database. To isolate a responsive failure during
development, set
`GARDENOPS_UI_FLOW_E2E_VIEWPORT=desktop` or `mobile`; omit it for the required
full matrix. Override occupied server ports with
`GARDENOPS_UI_FLOW_E2E_BACKEND_PORT` and
`GARDENOPS_UI_FLOW_E2E_FRONTEND_PORT`.

For the high-risk optimization journeys around Map-first loading, layout
restore, garden switching, offline replay, provider behavior, MFA, and
destructive auditing, use these focused real-backend checks:

```bash
GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1 \
.venv/bin/python scripts/run_fast_postgres_tests.py \
  --command -- scripts/run_optimization_journeys_e2e.sh

.venv/bin/python scripts/run_fast_postgres_tests.py \
  --command -- bash scripts/run_deterministic_provider_e2e.sh

scripts/run_totp_mfa_e2e.sh
```

The combined optimization journey uses session auth on desktop and Pixel 7. It
checks that Map startup does not fetch the full plant catalogue; restores a
genuinely divergent layout while preserving retained plot assignments and
invalidating stale plant UI state; proves one render after three overlapping
snapshot-refresh reads; and verifies rapid garden switching plus garden-scoped
notifications and weather state. It also replays a journal create, its media
upload, and a task action with stable operation IDs, including duplicate delivery; checks the
disabled-provider recovery state; exercises mobile sheet focus/inert behavior;
and deletes only an explicitly named disposable target. The destructive flag
is required because the final database assertions dynamically cover every
garden-owned table, retained offline operation rows, media counts, and the
exactly-once durable delete audit.

The deterministic-provider journey is available only with the runner's exact
`APP_ENV=test` fixture flag. It scrubs inherited provider credentials and proxy
settings, uses the local deterministic adapter, blocks non-loopback browser
traffic before transmission, scans backend logs for vendor credential material,
and checks budget accounting after desktop and mobile chat requests. The TOTP
journey creates its own disposable runner child and exercises desktop enrollment
followed by mobile
MFA login, rejected and successful step-up, recovery-code regeneration, and
redacted database counts. These security journeys do not capture screenshots,
video, traces, TOTP seeds, passwords, or recovery-code values.

The optimization, deterministic-provider, TOTP, and broad UI-flow browser
contexts actively abort non-loopback HTTP(S) and WebSocket requests before they
can leave the test browser. They continue real loopback product routes and
never fulfill or mock API responses.

## Complete Journey Program

The phased complete-journey program extends the broad read map with durable
desktop/mobile mutation, role, offline, provider, database, filesystem,
accessibility, and performance evidence. Use `--phase N` for the focused phase
against a fresh fixture, or `--through-phase N` for the cumulative set from
Phase 0 through Phase N:

```bash
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 0
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 0
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 1
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 1
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 2
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 2
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 3
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 3
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 4
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 4
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 5
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 5
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 6
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 6
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --phase 7
scripts/run_complete_journeys_e2e.sh --expected-head "$(git rev-parse HEAD)" --through-phase 7
```

The runner creates its own disposable PostgreSQL child through
`run_fast_postgres_tests.py --command`, validates the runner-issued marker and
PostgreSQL system identifier, uses dynamic non-5432 loopback ports, scrubs
inherited credentials and proxy settings, and blocks non-loopback browser HTTP,
HTTPS, and WebSocket traffic. It refuses unsafe artifact paths and writes private
manifests/traces under a unique directory below the gitignored
`research/optimization-map/runs/complete-journeys/` tree. Successful runs remove
temporary logs/media/terrain/download state while retaining the ignored evidence
run; failed runs preserve private logs and artifacts for diagnosis. Playwright
first writes each trace to private staging because it can contain request and
response payloads, page state, and identifiers. Run closure sanitizes and validates
that staged trace, deletes the raw staging copy, and retains only the sanitized
private archive. Sanitization is fail-closed: retained traces exclude DOM/page
snapshots, screencast frames, response resources, and unknown archive members,
and redact invitation, challenge, TOTP, recovery-code, credential, session, and
CSRF fields from the remaining event metadata. The public manifest is a sanitized projection; it binds the
fixture, runtime/browser, and lockfiles by hash and size and includes recomputable
canonical projection digests. The
runtime evidence hashes both the Chromium launcher and the resolved ELF browser
payload that Playwright launches; the reported browser version comes from that
running process. The audit digest binds a retained aggregate projection of
method, normalized path, status, and count, rather than raw audit records or a
null placeholder. Initial and final count/digest projections cover every public
domain table, including every table allowed to change. The public manifest does
not contain or replace the retained sanitized trace archive.

The cumulative Phase 1 invocation first runs the Phase 0 desktop and Pixel 7
administrator foundation, then runs independent desktop and Pixel 7 onboarding
accounts plus desktop/mobile administrator, editor, and viewer profiles. The
foundation proves session login, map-first startup, scoped notifications, and
an ordinary A/B/A garden switch before the Phase 1 mutation work begins.

Phase 1 exercises real mobile map-object and plot edits, plant-assignment
lifecycle, indoor data, saved views, snapshots, versioned export/import, and
malformed-import rejection. Its delayed A/B/A race holds real Beta GET requests
for plots, plants, saved views, indoor data, layout, map objects, notifications,
plot alerts, weather, and garden settings; it starts the matching Alpha request,
then releases and observes the held Beta response. Administrators run every
surface on desktop and mobile, while editor/viewer profiles run the core plot
race on both devices. The browser routes only continue those requests; they
never fulfill or mock a response.

The checker verifies exact onboarding grid, location, house configuration,
owner, and membership; the retained mobile snapshot's Alpha garden ownership
and payload; and the complete Alpha/Beta plot, layout, map-object, nested-unit,
plant-ownership, and assignment graphs after restore/import. It also enforces
the exact quick-action records and every unchanged row in mutable Phase 1 domain
tables, the profile/device/role matrix, targeted audit histogram, lifecycle
cleanup, cross-garden absence, backend-error evidence, and empty temporary
filesystem state. The focused backend suite additionally injects a failure
during restore and proves the transaction rolls back.

Phase 2 first runs six fresh-browser-context, read-only administrator, editor,
and viewer probes in a deliberately different desktop/Pixel 7 order. Those
probes establish profile-local session, device, role, and garden-scoped read
behavior only. They reject workflow-domain mutation requests, while login,
session, audit, and read-side media-summary bookkeeping still occurs; they do
not claim isolated database state or mutation-order independence. The six
state-changing administrator, editor, and viewer profiles then run as an
explicitly ordered shared-state choreography: admin desktop, admin mobile,
editor desktop, editor mobile, viewer desktop, and viewer mobile.
Later profiles intentionally validate state produced by earlier profiles. A
focused `--phase 2` run isolates the phase on a fresh fixture, while
`--through-phase 2` proves the same choreography after Phases 0 and 1.

The browser guard permits only the configured disposable frontend, backend, and
optional provider origins. The frontend uses `localhost` so its WebAuthn RP ID
is valid; backend and provider services remain on literal `127.0.0.1`. Every
origin has a dynamic non-5432 port. User mutations use visible controls, while
the few page-origin fetches remain behind that same guard. Expected browser
console diagnostics are admitted only after matching a
specific method, status, path, and probe context; a retained classification
ledger prevents an unexpected diagnostic from being hidden by removing it from
the working array. Pixel 7 evidence enforces its viewport, user-agent contract,
and touch capability, and the persisted private manifest records a hashed trace
for every mutation profile and read-only permutation probe.

The completed Phase 2 browser coverage exercises Today, task actions, mobile
partial grouped completion and manual-date snoozing, Calendar lifecycle and
out-of-range snooze correction, mobile preference mutation and history reload,
selected editor actions, viewer-owned Attention preferences and item state,
viewer Today and Weather affordances, notification preference saving, true
network-offline task replay, nested-modal focus/inert behavior, weather checks,
and delayed A/B/A stale-DOM checks for Tasks, Calendar, and subscriptions. After
the desktop
preference save, the disposable fixture creates one eligible and one ineligible
issue plus notification, invokes the digest-delivery boundary without rerunning
scheduler maintenance, and checks Today, inbox, badge, delivery, and the persisted
rows. The checker also preserves the scoped Phase 1 final state during cumulative
runs, checks maintenance rows and summary totals against an independently
declared fixture specification (including semantic histograms), and follows the
exact maintenance-created task, notification, and alert row identities through
the final snapshot. Whole-table count/digest projections are retained for every
allowed domain table, so scoped assertions do not silently omit unrelated extra
rows. Summary counts, observed-row lengths, and frozen timestamps are not
accepted as independent expectations.

Phase 2 database coverage remains required in `tests/journey_coverage.yaml`:
the harness correlates every `POST`, `PUT`, `PATCH`, and `DELETE` browser
mutation one-to-one with method, path, response status, actor, authentication
type, garden scope, and the response `X-Request-ID` persisted as correlation on
the audit row. The database-generated audit row ID, not client-supplied
`X-Request-ID`, is the unique reservation/finalization identity. Unknown
successful mutation paths fail the evidence check. The audit writer
records wall-clock timestamps, so those timestamps remain an explicit
nondeterministic field and are not used as ordering proof. The same manifest keeps unsupported
editor/viewer role dimensions required. Phase 8 accessibility and Phase 9
performance remain explicitly open; the current structural and focus assertions
are not a substitute for those phase audits.

When Phase 2 follows Phase 1 in a cumulative run, Phase 1's intentional garden
address update invalidates cached forecasts. The guarded Phase 2 preparation
mode restores only the two frozen disposable forecast rows before Phase 2; it
preserves existing row identities, refuses direct use outside the disposable
test contract, and never enables remote weather fetches. Focused and cumulative
Phase 2 runs therefore use the same deterministic weather boundary.

Phase 3 runs fresh desktop and Pixel 7 administrator, editor, and viewer
profiles through the observation-to-action workflows. It covers journal,
issue, harvest, and media lifecycle; explicit photo identification and
diagnosis; issue reopen and repeated resolve; mobile decimal harvest entry;
viewer write denial; and Alpha/Beta garden isolation. Identification and
diagnosis use the deterministic local provider, remain advisory until the user
chooses the corresponding action, and cannot silently create records.

The mobile editor queues journal, issue, harvest, and attachment writes while
Chromium is genuinely offline. The journey first requires the six
application-generated operation UUIDs to be unique and stable across an
IndexedDB reread, then maps them to deterministic oracle identities. It drops
one response after the server commit, reloads, reconnects twice, and verifies
one logical result per operation. Changed payload replay must return `409`;
replay against the deleted journal target must return `410`. Expected offline
network failures are correlated independently by request method and path,
while any unrelated request or console failure remains fatal.

Phase 3 postconditions compare exact journal links, issue history and follow-up
tasks, notifications, harvest-linked journal and rollup data, media links and
storage bytes, cleanup jobs, offline operation fingerprints, and browser/audit
correlation. The checker walks the private media root and requires the database
storage keys, original files, and previews to match without orphans. Journal,
Issues, and Harvest clear their cached rows during a garden switch and reject
late responses from the previous garden before the new content becomes
interactive.

Phase 4 runs fresh administrator, editor, and viewer desktop/mobile profiles
for inventory, procurement, planner/workflows, local catalogue behavior,
reports, and exports. Its independent oracle fixes the
decimal inventory ledger, procurement lifecycle, profile order, supported
surface, and database mutation boundaries. The checker projects inventory
items and transactions, durable procurement receipt provenance, planner goal
preferences, workflow tasks and links, report source rows, audit mutations, and
unchanged Beta-garden rows. Repeating the received transition and workflow
start must not create duplicate transactions or tasks. Inventory deletion is
serialized with ledger writes and cannot erase transaction history; planting
from stock commits its plot assignment, journal event, and stock deduction as
one idempotent command.

Phase 4 parses visible-browser CSV, JSON, and ICS downloads rather than treating
download completion as evidence. It checks garden scope, CSV formula/quote
escaping, maximum-precision decimal quantities without binary-number coercion,
calendar dates, and absence of internal paths or secret-shaped content. Its
primary administrator journey creates inventory,
enters fractional ledger transactions, receives procurement, selects a planner
goal, starts a workflow, and completes the generated task through the visible
UI. It also follows the overdue-task report action to the exact pending overdue
Tasks view; direct requests are reserved for readback, idempotent replay, and
authorization-denial evidence. Pixel 7 evidence covers dense inventory and
procurement controls and delayed Alpha/Beta responses for inventory,
procurement, planner, and reports. External catalogue results are explicitly
`not_applicable` while
the endpoint has no public species catalogue. ZIP export, generic import,
backup restore, ICS import, suggestion acceptance, and workflow-instance
lifecycle remain unsupported and are not Phase 4 claims. Accessibility remains
open for Phase 8 and performance remains open for Phase 9.

Before coordinating a real Phase 4 browser run, validate its static harness and
coverage contracts:

```bash
.venv/bin/pytest -q tests/test_complete_journey_e2e_scripts.py -k phase_four
.venv/bin/python scripts/check_journey_coverage.py --allow-open
node --check scripts/e2e/journeys/planningAndReporting.cjs
node --check scripts/check_complete_journeys_e2e.cjs
```

Phase 5 verifies invitation acceptance, role boundaries, passkey enrollment,
rename, passwordless login, user-verification rejection, revoked-credential
denial, passwordless backup enrollment, and final-factor lockout. It covers TOTP
enrollment cancellation, confirmation, recovery-code regeneration, one-time use,
reuse denial, fallback, and disable. It also proves safe session inventory,
second-browser revocation, idle and absolute expiry, live role refresh, personal
settings persistence, stale-CSRF and cross-garden denials, and emergency
read-only enable/block/disable behavior across the six desktop/Pixel role
profiles. Invitation and authentication secrets are removed before traces
become retained evidence. The disposable runner raises only its local login and
failed-authentication ceilings so deliberate credential-recovery and signed-out
probes remain deterministic; normal deployment limits and their focused backend
tests are unchanged.

Session deployments have two independent limits. `AUTH_SESSION_TTL_HOURS`
defaults to a 12-hour idle timeout; `AUTH_SESSION_ABSOLUTE_TTL_HOURS` defaults to
168 hours and prevents activity from renewing a session forever. Focused auth
tests should cover both boundaries whenever session renewal changes.

Before coordinating a real Phase 5 browser run, validate its static harness and
coverage contracts:

```bash
.venv/bin/pytest -q tests/test_complete_journey_e2e_scripts.py -k phase_five
.venv/bin/python scripts/check_journey_coverage.py --allow-open
node --check scripts/e2e/journeys/identityAndRoles.cjs
node --check scripts/check_complete_journeys_e2e.cjs
```

Phase 6 uses Playwright routing to deliver an offline journal mutation to the
real backend and then drop only its browser-facing response. The journey proves
the retained operation ID, independent committed-state postcondition, repeated
reconnect replay, garden isolation, logout queue clearing, and actionable
conflict/gone recovery across journal, issue, harvest, task-action, and media
draft labels. Failed details are collapsed behind a compact counted control by
default so navigation and sign-out remain available, while newly terminal work
is announced through a live region and recovery rerenders restore keyboard
focus. Logout, session expiry, and confirmed signed-out startup fail closed when
IndexedDB cleanup fails: the next sign-in remains unavailable until the user
retries and completes local queue cleanup. The complete
payload/binary fingerprint, retention-expiry,
side-effect-count, and media-asset matrices remain focused backend boundaries in
`tests/test_offline_idempotency.py` and
`tests/test_offline_idempotency_unit.py`, because their browser transport
mechanics are identical.

The Phase 6 browser manifest names only `OFF-01`. Destructive atomicity (`C2`)
and schema/bootstrap integrity (`INT-01`) are proven by their focused backend
suites and recorded separately in the validation ledger; the browser profile
owns the real lost-ack and recovery interaction. Its five-family terminal-state
UI check uses deliberately inserted IndexedDB fixtures, while backend tests own
the real response-classification and payload/binary fingerprint contracts.
Direct mobile garden-delete wording and all accessibility closure remain Phase
8 work.

Before coordinating a real Phase 6 browser run, validate its static harness and
frontend recovery contracts:

```bash
.venv/bin/pytest -q tests/test_complete_journey_e2e_scripts.py -k phase_six
.venv/bin/python -m unittest tests.test_offline_replay_frontend_static
node --check scripts/e2e/journeys/offlineAndFailureRecovery.cjs
node --check scripts/check_complete_journeys_e2e.cjs
```

Phase 7 proves the provider, weather, ShadeMap, and uploaded-terrain seams as
one shared-state sequence. Administrator, editor, and viewer desktop profiles,
plus the administrator Pixel 7 profile, use visible controls against real local
GardenOps routes. The test-only provider adapter is a separately bound,
loopback-only process, not a browser response mock: it exercises successful
garden chat, a provider quota response, the GardenOps runtime-script proxy,
signed terrain tile rendering, uploaded LiDAR lifecycle, saved Shade state,
calibration, obstacles, and read-only role boundaries. It visibly labels a
stale forecast and verifies that refresh leaves alert generation at zero; the
degraded cache itself is unchanged. The loopback runtime proves GardenOps'
same-origin loading and rendering integration, not vendor simulation accuracy.
The final database audit projection is exact, and temporary LiDAR, generated
terrain, media, and download state are empty at teardown. It retains sanitized
private traces and a manifest only under the ignored complete-journey evidence
directory.

Before coordinating a real Phase 7 browser run, validate its static harness and
provider/terrain contracts:

```bash
.venv/bin/pytest -q tests/test_complete_journey_e2e_scripts.py -k phase_seven
node --check scripts/e2e/providers/deterministicLoopbackProvider.cjs
node --check scripts/e2e/journeys/providersAndTerrain.cjs
node --check scripts/check_complete_journeys_e2e.cjs
```

The tracked coverage contract is `tests/journey_coverage.yaml`. Validate open
phases during implementation and require complete closure only in the final
phase:

```bash
.venv/bin/python scripts/check_journey_coverage.py --allow-open
.venv/bin/python scripts/check_journey_coverage.py --require-closed
```

Never run the complete-journey child mode directly, point it at a persistent
database, source the runtime environment, or publish its ignored artifacts.

For map-object direct manipulation changes, build or start the frontend and run
the mocked browser flow:

```bash
cd frontend
npm run preview -- --host 127.0.0.1 --port 4173
cd ..
BASE_URL=http://127.0.0.1:4173 CHROMIUM_EXECUTABLE=/usr/bin/chromium-browser node scripts/check_map_object_direct_manipulation_e2e.cjs
```

The check runs against local built assets, mocks GardenOps API responses in the
browser, and verifies touch move, touch resize, keyboard move/resize, and
two-finger touch cancellation without touching live data.

For Attention Today panel changes, run the managed real-backend browser journey
against a disposable local database named `gardenops_attention_e2e_test` or
prefixed with `gardenops_attention_e2e_test_`:

```bash
cd frontend
GARDENOPS_ATTENTION_E2E_TEST_URL="postgresql://localhost/gardenops_attention_e2e_test" npm run test:attention-today-e2e
```

The runner refuses to seed unless `APP_ENV=test`, `AUTH_REQUIRED=false`,
`GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE=1`, and the database URL points to an
allowed local E2E database name. It truncates that database, starts FastAPI and
Vite, then runs Playwright against real API routes.

For task completion history and task-specific snooze changes, run the matching
real-backend browser journey against a disposable local database only:

```bash
cd frontend
GARDENOPS_TASK_HISTORY_E2E_TEST_URL="postgresql://localhost/gardenops_task_history_e2e_test" npm run test:task-completion-history-e2e
```

This runner uses a disposable local database only, truncates it after a
task-history-specific safety guard passes, and exercises real API routes
through FastAPI and Vite.
If local TCP PostgreSQL authentication rejects the `localhost` URL, use the
local socket path and run database commands as the `postgres` user instead.
Override the E2E ports when the defaults are already occupied:

```bash
cd frontend
GARDENOPS_TASK_HISTORY_E2E_RUN_DB_AS_POSTGRES=1 \
GARDENOPS_TASK_HISTORY_E2E_BACKEND_PORT=8010 \
GARDENOPS_TASK_HISTORY_E2E_FRONTEND_PORT=5174 \
GARDENOPS_TASK_HISTORY_E2E_TEST_URL="postgresql:///gardenops_task_history_e2e_test?host=/var/run/postgresql" \
npm run test:task-completion-history-e2e
```

## Test Database

Create `.env.test.local` from `.env.test.example` and use it for test and PR
check commands. `GARDENOPS_TEST_POSTGRES_URL` and `DATABASE_URL` must both point
at the disposable test database because tests can truncate and rewrite data. Do
not source the runtime `.env` or a production service env file for pytest.
Fast password hashing is not inferred from `APP_ENV=test` alone. Set
`AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true` only in disposable test-runner
environments with `INTERNET_EXPOSED=false` to lower Argon2 cost so repeated user
seeding does not dominate runtime.

## Pull Request Expectations

- Keep changes scoped.
- Include tests for behavior changes.
- Update public docs when behavior, setup, environment variables, or deployment
  expectations change.
- Do not commit `.env`, database dumps, media uploads, local terrain files, or
  generated build output.

For a repeatable local review process, including worktree checkout, agent-assisted
review prompts, Dependabot handling, and merge gates, see
[pr-review-runbook.md](pr-review-runbook.md).
