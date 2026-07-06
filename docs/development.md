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
