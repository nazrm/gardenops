<p align="center">
  <img src="docs/assets/gardenops-logo.webp" alt="GardenOps" width="900" height="600">
</p>

# GardenOps

<p align="center">
  <strong>Self-hosted garden operations for mapping the space, managing plants,
  scheduling work, and keeping a durable garden history.</strong>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> |
  <a href="#features">Features</a> |
  <a href="#shademap-integration">ShadeMap</a> |
  <a href="#development-checks">Development</a> |
  <a href="SECURITY.md">Security</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.14-blue" alt="Python 3.14">
  <img src="https://img.shields.io/badge/node-24-green" alt="Node.js 24">
  <img src="https://img.shields.io/badge/postgresql-16%2B-blue" alt="PostgreSQL 16+">
</p>

## Why GardenOps

GardenOps is built for operators who want one private system for the garden map,
plant records, seasonal work, observations, media, and operating history instead
of splitting that information across spreadsheets, notes, calendars, and photo
folders.

It is a full-stack FastAPI/PostgreSQL and Vite/TypeScript application with
schema migrations, backend and frontend test coverage, GitHub Actions, and
systemd/nginx deployment examples. It is designed for self-hosted instances:
your data, provider keys, logs, media uploads, and terrain files stay under your
control.

### Operational Strengths

- Private operations hub: maps, plots, plants, tasks, issues, journal entries,
  media, reports, and garden history live in one system.
- Self-hosted control: location data, photos, logs, provider keys, terrain
  files, and operational records stay under the operator's control.
- Durable garden memory: observations, treatments, harvests, media, and task
  history are structured so a garden can be managed across seasons instead of
  recreated from scattered notes.
- Map-linked workflows: plant records, plot context, issues, journal entries,
  media, and work planning connect back to the physical garden layout.
- Map-first attention overview: a compact Today panel on the map summarizes
  actionable tasks, warnings, follow-ups, and recent no-action-needed outcomes
  with user-controlled delivery settings.
- Built for serious operators: multi-garden memberships, roles, exports,
  calendar subscriptions, notifications, weather context, optional AI, and
  ShadeMap support are available for more complex garden operations.
- Inspectable and maintainable: open source code, migrations, CI checks,
  security controls, deployment examples, and documented configuration make the
  system practical to operate and adapt.

## Features

### Garden Planning

- Editable garden map with plots, zones, plant placement, saved views, and
  draggable/resizable layout-only objects for patios, terraces, greenhouses,
  sheds, ponds, paths, beds, and custom surfaces. The responsive map editor is
  available on desktop and mobile, including optional nested pots/planters.
- Versioned map snapshots and JSON import/export preserve indoor or otherwise
  unplaced plots with null coordinates, retained plot ownership, and persisted
  house dimensions while rejecting unsupported schema versions without partial
  writes.
- Multi-garden support with active garden context, memberships, invitations, and
  garden-specific settings.
- Onboarding flows for defining the garden, location, main structure, map zones,
  and optional integrations.

### Plant And Inventory Management

- Plant catalog and garden plant records with names, categories, care details,
  plot assignments, planting metadata, and custom notes.
- Inventory, procurement, care, issue, harvest, and indoor-plant workflows for
  tracking what exists, what is needed, what happened, and what needs follow-up.
- CSV import/export paths for moving plant and operations data in and out of the
  system.

### Work, Calendar, And Notifications

- Task planning with generated work items, seasonal task windows, manual tasks,
  task-specific snooze defaults, journal-backed completion history, grouped
  plant completion, and overdue/follow-up views.
- Map Today attention panel that keeps the map first while summarizing current
  tasks, issue follow-ups, weather risks, status notices, and rain-covered
  watering outcomes that need no action.
- Calendar views, manual events, preferences, and subscription support for
  garden work and observations.
- Notification and saved-view tooling for keeping recurring work, open issues,
  weather alerts, and high-priority garden states visible.

### Observations, Media, And History

- Journal entries for observations, treatments, batch notes, and plot-linked
  history.
- Offline journal, issue, and harvest creates, task actions, and queued media
  uploads retain their original garden and stable operation ID. Retries within
  30 days return the original result; if its target was deleted, replay returns
  `410 Gone` instead of recreating data.
- Issue tracking for pests, disease, damage, treatments, severity, causes,
  resolution, and follow-up dates.
- Media uploads and links for plants, plots, journal entries, issues, and
  harvest records, including missing-cover reporting.

### Analysis And Decision Support

- Reports and statistics for planting history, bloom windows, area use,
  data-quality gaps, harvest activity, open issues, and upcoming work.
- Weather summaries, checks, alerts, frost/dryness analysis, and plant-aware
  weather risk helpers when configured.
- Optional AI-assisted plant lookup, plant identification, issue diagnosis, and
  garden-aware chat when provider keys are supplied.

### Sun, Shade, And Terrain

- Optional ShadeMap-backed sun and shade panel with saved state, seasonal
  presets, calibration, obstacles, building features, terrain tile signing, and
  monthly estimated sun values.
- ShadeMap is a paid third-party service; GardenOps does not provide access,
  subscriptions, billing, or API keys.
- Local terrain support is available for operators who keep terrain datasets
  outside the repository and expose generated terrain tiles through the app.

### Security And Operations

- Session auth, API-key compatibility mode, roles, MFA support, passkeys,
  invite-gated passwordless registration, password policy, audit logging, rate
  limits, invite-token hashing checks, and security telemetry hooks.
- Production-oriented proxy settings for trusted hosts, CORS, trusted proxy
  CIDRs, internet-exposed mode, Redis rate limiting, and CSP checks.
- Deployment examples for systemd and nginx, plus CI checks for backend tests,
  frontend builds, environment docs, unsafe HTML sinks, invite-token storage,
  and sourcemap leakage.

## Quick Start

```bash
git clone <repository-url> gardenops
cd gardenops
uv python install 3.14
uv venv --python 3.14
uv sync --frozen --group test --group lint
cd frontend && npm ci && cd ..
cp .env.example .env
cp .env.test.example .env.test.local
```

Create PostgreSQL databases named `gardenops` and `gardenops_test`, update
`DATABASE_URL` in `.env`, and point `.env.test.local` at `gardenops_test`.
Then run:

```bash
set -a
. ./.env
set +a
.venv/bin/python -c "import gardenops.db as db; db.run_migrations()"
cd frontend && npm run build && cd ..
.venv/bin/python -m uvicorn gardenops.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`.

Provider keys for OpenAI, Anthropic, PlantNet, and server-side ShadeMap can be
entered later from the platform admin UI. Set `APP_SECRETS_ENCRYPTION_KEY`
before using admin-managed provider keys; environment provider variables remain
valid fallbacks.

For a fuller walkthrough, see [docs/installation.md](docs/installation.md).

## Install With An Agent

Point your coding agent at this README and use this prompt:

```text
You are in a fresh clone of GardenOps. Read README.md, docs/README.md, and
docs/installation.md first. Set up a complete local development instance
without committing secrets or generated artifacts.

Tasks:
1. Verify or install Python 3.14, uv, Node.js 24, npm, and PostgreSQL 16+.
2. Install dependencies with `uv sync --frozen --group test --group lint` and
   `npm ci` in `frontend/`.
3. Create local PostgreSQL databases named `gardenops` and `gardenops_test`
   owned by a local `gardenops` role, or ask me before changing system
   PostgreSQL configuration.
4. Copy `.env.example` to `.env` if needed, fill only local placeholder values,
   and never print secrets.
5. Copy `.env.test.example` to `.env.test.local`, point it only at the
   disposable test database, and never use the runtime `.env` for tests.
6. Load `.env`, run `gardenops.db.run_migrations()`, build the frontend, and
   run the backend on `127.0.0.1:8000`.
7. Run the commands in the README.md "Development Checks" section and report
   exactly what passed, what failed, and what still needs user input.

Stop and ask before changing firewall rules, nginx, systemd, TLS certificates,
public DNS, production databases, or persistent secrets outside this checkout.
```

## Documentation

| Goal | Start here |
|---|---|
| Install locally | [docs/installation.md](docs/installation.md) |
| Configure environment variables | [docs/configuration.md](docs/configuration.md) and [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) |
| Enable sun and shade analysis | [docs/shademap.md](docs/shademap.md) |
| Deploy behind systemd/nginx | [docs/deployment.md](docs/deployment.md) |
| Contribute or open PRs | [docs/development.md](docs/development.md) and [CONTRIBUTING.md](CONTRIBUTING.md) |
| Review the security model | [SECURITY.md](SECURITY.md) |

## ShadeMap Integration

GardenOps includes an optional integration with
[ShadeMap](https://shademap.app/about) for sun and shade analysis. ShadeMap is a
paid external service, and GardenOps does not provide API keys, subscriptions,
provider billing, or permission to use ShadeMap in your deployment.

To enable the panel, configure your own platform-admin-managed or environment
server-side key, browser-safe client key, and GardenOps terrain tile-signing
secret. The browser talks to GardenOps routes for configuration, saved state,
calibration, obstacles, building features, and signed terrain tile URLs.

Read [docs/shademap.md](docs/shademap.md) before enabling the feature. It
explains the required paid access, data flow, key types, calibration, obstacles,
terrain tiles, local terrain files, rate limits, troubleshooting, and production
checks.

## Requirements

- Git
- Python 3.14.x
- `uv`
- Node.js 24.x and npm
- PostgreSQL 16+
- Redis for internet-exposed production rate limiting

## Development Checks

Run these before opening a pull request:

```bash
set -a
. ./.env.test.local
set +a
cd frontend && npm run build && cd ..
uv run ruff check gardenops tests
uv run ruff format --check gardenops tests
uv run python scripts/check_env_docs.py
uv run python scripts/check_backend_integrity.py --format text
uv run python -m pytest tests/ -q --tb=short
```

For a faster backend smoke check during deploy verification or local iteration:

```bash
set -a
. ./.env.test.local
set +a
scripts/run_backend_smoke.sh
```

For the fastest local full backend run, use the disposable Postgres runner:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --full-suite --shards 4
```

The runner creates a temporary local Postgres cluster, generates test-only
credentials and databases, runs the shard suite, and removes the cluster on
success. It does not read `/etc/gardenops.env` or use the live database.

Use the same disposable, migrated database for a database-backed script or
targeted test command with `--command --`:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --command -- command [args...]
```

The command receives `DATABASE_URL`, `GARDENOPS_TEST_POSTGRES_URL`, and
`GARDENOPS_DISPOSABLE_POSTGRES_URL` for the temporary `gardenops_test`
database. Cleanup runs when the command exits, and the runner returns failure if
it cannot verify that its disposable cluster was removed.

Focused E2E seeders that require their dedicated database name can select one
of the runner's allowlisted names:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py \
  --command-database gardenops_attention_e2e_test \
  --command -- scripts/run_attention_today_e2e.sh
```

For the dedicated Attention and task-history databases, the runner also
provides the seeder URL, unique app ports, and an isolated log directory.
Focused real-backend journeys for offline replay, garden switching,
provider-disabled and provider-enabled behavior, destructive audit durability,
and TOTP MFA are listed in
[docs/development.md](docs/development.md#targeted-frontend-e2e-checks).

To verify the cleanup paths after runner changes:

```bash
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke after-start
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke during-migration
.venv/bin/python scripts/run_fast_postgres_tests.py --cleanup-smoke during-pytest
```

As a normal-durability fallback, provision sibling disposable databases named
with `_shard0`, `_shard1`, and so on, then run:

```bash
set -a
. ./.env.test.local
set +a
uv run python scripts/run_backend_shards.py --shards 4
```

For the fallback sharded runner, the default file-level split is the fastest
validated mode on the live host. Use `--scope node` only when whole-file shard
balance becomes a problem.

Create `.env.test.local` from `.env.test.example` and keep it pointed only at
the disposable test database. Do not source the runtime `.env` or any production
service env file for pytest.

The frontend build includes security checks for unsafe HTML sinks, invite-token
storage, auth-gate flow, AI chat client behavior, map-object editor contracts,
TypeScript, production bundling, sourcemap leakage, and stale generated asset
references. If a change adds a new raw HTML sink, it must be reviewed and
documented in `frontend/security/innerhtml_allowlist.txt`.

GitHub Actions runs backend, frontend, and dependency audit checks on pushes and
pull requests.

## Production Notes

For production or any internet-exposed deployment, set at minimum:

```bash
APP_ENV=production
INTERNET_EXPOSED=true
AUTH_REQUIRED=true
AUTH_MODE=session
ALLOW_INSECURE_REMOTE=false
CORS_ALLOW_ORIGINS=https://gardenops.example.com
ALLOWED_HOSTS=gardenops.example.com
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0
API_DOCS_ENABLED=false
CSP_REPORT_ONLY=false
AUTH_MFA_SECRET_KEY=
SHADEMAP_TILE_SIGNING_SECRET=<generate-a-unique-random-secret>
```

The backend refuses to start with API docs enabled in production or in any
internet-exposed deployment, and internet-exposed deployments must enforce CSP
rather than running in report-only mode. Production and internet-exposed
session-auth deployments also require `AUTH_MFA_SECRET_KEY` to be set to a
generated secret with at least 32 characters so MFA seed encryption does not
fall back to database-local state. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the output as the value.

If an older deployment copied the previous public placeholder value, rotate it
while the service is private: start a maintenance instance with
`APP_ENV=development` and `INTERNET_EXPOSED=false` using the old value, disable
and re-enroll MFA for affected admins, then set a generated
`AUTH_MFA_SECRET_KEY` and restart in strict mode. Do not expose the app while
using the old placeholder key.

Set `AUTH_BOOTSTRAP_USERNAME` and `AUTH_BOOTSTRAP_PASSWORD` for the first
production admin account, then remove or rotate those values after bootstrap.

Use the files in `deploy/` as starting templates. Review them for your host,
TLS termination, service user, filesystem ownership, proxy topology, log
retention, backups, restore drills, and provider keys before installing them.

## Project Layout

| Path | Purpose |
|---|---|
| `gardenops/` | FastAPI application, routers, services, security, and database access |
| `frontend/src/` | TypeScript browser app |
| `migrations/` | PostgreSQL schema migrations |
| `tests/` | Backend, integration, and public-runtime tests |
| `scripts/` | Repository checks and operational helpers |
| `deploy/` | Example systemd and nginx deployment files |

## Contributing

Forks and PRs are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), keep
secrets out of commits, run the checks above, and document user-facing behavior
when you add or change features.

## License

GardenOps is released under the MIT License. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
