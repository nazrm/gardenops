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

## PR Checks

Run these before opening a PR:

```bash
set -a
. ./.env.test.local
set +a
cd frontend
npm run build
cd ..
uv run ruff check gardenops tests
uv run ruff format --check gardenops tests
uv run python scripts/check_env_docs.py
uv run python scripts/check_backend_integrity.py --format text
uv run python -m pytest tests/ -q --tb=short
```

## Frontend Security Checks

`npm run build` also checks for:

- unsafe raw HTML sinks
- invite-token storage regressions
- TypeScript errors
- production bundling errors
- sourcemap leakage
- stale generated asset references

If a change intentionally adds a raw HTML sink, document it in
`frontend/security/innerhtml_allowlist.txt` and explain why the sink is safe.

## Test Database

Create `.env.test.local` from `.env.test.example` and use it for test and PR
check commands. `GARDENOPS_TEST_POSTGRES_URL` and `DATABASE_URL` must both point
at the disposable test database because tests can truncate and rewrite data. Do
not source the runtime `.env` or a production service env file for pytest.

## Pull Request Expectations

- Keep changes scoped.
- Include tests for behavior changes.
- Update public docs when behavior, setup, environment variables, or deployment
  expectations change.
- Do not commit `.env`, database dumps, media uploads, local terrain files, or
  generated build output.
