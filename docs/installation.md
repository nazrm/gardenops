# Installation

This guide sets up a local GardenOps development instance.

## Requirements

- Git
- Python 3.14.x
- `uv`
- Node.js 24.x and npm
- PostgreSQL 16+

Redis is required for production, internet-exposed, or multi-instance rate
limiting. It is not required for a local development instance.

## Clone And Install Dependencies

```bash
git clone <repository-url> gardenops
cd gardenops
uv python install 3.14
uv venv --python 3.14
uv sync --frozen --group test --group lint
cd frontend
npm ci
cd ..
```

## Create Local Databases

Create one database for the app and one disposable database for tests. Adjust
the role, password, and commands for your operating system and PostgreSQL
policy.

```bash
sudo -u postgres createuser --pwprompt gardenops
sudo -u postgres createdb -O gardenops gardenops
sudo -u postgres createdb -O gardenops gardenops_test
```

## Configure Runtime And Test Env Files

```bash
cp .env.example .env
cp .env.test.example .env.test.local
```

Edit `.env` so runtime values match your local PostgreSQL credentials:

```bash
DATABASE_URL=postgresql://gardenops:change-me@127.0.0.1:5432/gardenops
```

Edit `.env.test.local` so test values point only at the disposable test database:

```bash
DATABASE_URL=postgresql://gardenops:change-me@127.0.0.1:5432/gardenops_test
GARDENOPS_TEST_POSTGRES_URL=postgresql://gardenops:change-me@127.0.0.1:5432/gardenops_test
```

Leave optional provider keys empty until you are ready to enable those
integrations.

## Run Migrations And Build

```bash
set -a
. ./.env
set +a
.venv/bin/python -c "import gardenops.db as db; db.run_migrations()"
cd frontend
npm run build
cd ..
```

## Start The App

```bash
.venv/bin/python -m uvicorn gardenops.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`.

For frontend development, keep the backend running and start Vite in a second
shell:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`.

## Agent/AI Install Prompt

Use this when asking a coding agent to set up the project:

```text
You are in a fresh clone of GardenOps. Read README.md, docs/README.md, and
docs/installation.md first. Set up a complete local development instance
without committing secrets or generated artifacts.

Verify or install Python 3.14, uv, Node.js 24, npm, and PostgreSQL 16+. Install
dependencies, create local `gardenops` and `gardenops_test` databases, create
`.env` from `.env.example`, create `.env.test.local` from `.env.test.example`,
run runtime migrations from `.env`, build the frontend, start the backend on
`127.0.0.1:8000`, and run the commands in the README.md "Development Checks"
section with `.env.test.local`.

Stop and ask before changing firewall rules, nginx, systemd, TLS certificates,
public DNS, production databases, or persistent secrets outside this checkout.
```

## Common Setup Problems

- `DATABASE_URL` must point at PostgreSQL, not SQLite.
- `GARDENOPS_TEST_POSTGRES_URL` must point at a disposable test database.
- Run frontend commands from `frontend/`; there is no root npm package.
- Keep `.env` and `.env.test.local` untracked. They are ignored by Git and
  should never be committed.
