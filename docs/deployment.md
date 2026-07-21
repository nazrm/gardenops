# Deployment

GardenOps can run as a standard FastAPI service behind nginx with PostgreSQL.
Redis is required for production, internet-exposed, or multi-instance rate
limiting.

## Production Baseline

Use this baseline for any internet-exposed deployment:

```bash
APP_ENV=production
INTERNET_EXPOSED=true
AUTH_REQUIRED=true
AUTH_MODE=session
AUTH_SESSION_TTL_HOURS=12
AUTH_SESSION_ABSOLUTE_TTL_HOURS=168
ALLOW_INSECURE_REMOTE=false
CORS_ALLOW_ORIGINS=https://gardenops.example.com
ALLOWED_HOSTS=gardenops.example.com
AUTH_PASSKEY_RP_ID=gardenops.example.com
AUTH_PASSKEY_ORIGINS=https://gardenops.example.com
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0
API_DOCS_ENABLED=false
CSP_REPORT_ONLY=false
AUTH_MFA_SECRET_KEY=
```

The service rejects `API_DOCS_ENABLED=true` in production or internet-exposed
deployments, rejects `CSP_REPORT_ONLY=true` when `INTERNET_EXPOSED=true`, and
requires `RATE_LIMIT_BACKEND=redis` in production, internet-exposed, or
multi-instance modes. Session-auth deployments in production or internet-exposed
mode must also set `AUTH_MFA_SECRET_KEY` to a generated secret with at least 32
characters. Generate one with
`python -c "import secrets; print(secrets.token_urlsafe(32))"` and paste the
output as the value.

The session idle timeout defaults to 12 hours. The independent absolute timeout
defaults to 168 hours and caps renewal even while the user remains active.

If an older deployment copied the previous public placeholder value, rotate it
while the service is private: start a maintenance instance with
`APP_ENV=development` and `INTERNET_EXPOSED=false` using the old value, disable
and re-enroll MFA for affected admins, then set a generated
`AUTH_MFA_SECRET_KEY` and restart in strict mode. Do not expose the app while
using the old placeholder key.

Add provider keys only for integrations you intend to enable.

## Systemd And Nginx

The `deploy/` directory contains example files:

- `gardenops.service.example`
- `gardenops-atomic-release.conf.example`
- `gardenops-atomic-deploy`
- `gardenops-release-preflight`
- `nginx.production.example.conf`

Review and adapt them for your host paths, service user, Python environment,
TLS termination, proxy topology, upload size, log retention, and backup policy.

## Atomic Releases

Production deployments should build an immutable release before changing the
running service. The supplied deployment wrapper keeps the Git checkout as a
control repository, builds commit-addressed releases under
`/srv/gardenops/releases/`, and switches `/srv/gardenops/current` only after the
new release passes its service-user preflight.

Install the host-owned scripts and systemd drop-in:

```bash
sudo install -o root -g root -m 0755 deploy/gardenops-atomic-deploy \
  /usr/local/sbin/gardenops-atomic-deploy
sudo install -o root -g root -m 0755 deploy/gardenops-release-preflight \
  /usr/local/sbin/gardenops-release-preflight
sudo install -d -o root -g root -m 0755 \
  /etc/systemd/system/gardenops.service.d
sudo install -o root -g root -m 0644 \
  deploy/gardenops-atomic-release.conf.example \
  /etc/systemd/system/gardenops.service.d/atomic-release.conf
sudo systemctl daemon-reload
```

Prepare the exact commit from `origin/main` while the current service remains
online, then activate that prepared commit:

```bash
git fetch origin main
COMMIT=$(git rev-parse origin/main)
sudo /usr/local/sbin/gardenops-atomic-deploy prepare "$COMMIT"
sudo /usr/local/sbin/gardenops-atomic-deploy activate "$COMMIT"
```

`prepare` checks out tracked files from the commit, installs locked production
Python dependencies, performs a clean locked frontend install and build, and
normalizes the release to `root:gardenops` with group-readable files. It uses
`umask 0027` independently of the interactive shell. The preflight runs as the
`gardenops` service account and verifies source, migrations, the frontend entry
point and referenced assets, and application importability.

`activate` reruns preflight, applies migrations, runs the read-only backend
integrity audit, atomically updates the `current` symlink, restarts the service,
and waits for local health. The prior release is recorded as
`/srv/gardenops/previous`. Rollback is allowed only when the current and prior
migration trees are identical:

```bash
sudo /usr/local/sbin/gardenops-atomic-deploy rollback
```

This restriction is deliberate: changing application code back does not undo a
database migration. A schema-changing rollback requires an operator-reviewed
database recovery plan and a tested backup.

## First Admin User

Set these values for the initial production bootstrap:

```bash
AUTH_BOOTSTRAP_USERNAME=
AUTH_BOOTSTRAP_PASSWORD=
AUTH_BOOTSTRAP_ROLE=admin
```

After the first admin account exists, remove or rotate the bootstrap values and
restart the service.

## Database

GardenOps requires PostgreSQL 16+. Run migrations before starting a new
deployment or after pulling changes:

```bash
set -a
. /etc/gardenops.env
set +a
/srv/gardenops/current/.venv/bin/python -c \
  "import gardenops.db as db; db.run_migrations()"
```

Test restores before relying on backups. Keep database dumps, media uploads,
and local terrain files out of Git.

## Redis

Use Redis for production, internet-exposed, and multi-instance rate limiting:

```bash
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0
```

Memory rate limiting is suitable for local development only.

## Preflight Checks

Before exposing a deployment:

- Confirm `.env` or the host env file contains no placeholder production
  secrets.
- Confirm `ALLOWED_HOSTS` and `CORS_ALLOW_ORIGINS` match the public origin.
- Confirm `AUTH_PASSKEY_RP_ID` and `AUTH_PASSKEY_ORIGINS` match the public
  HTTPS origin if passkeys are enabled.
- Confirm HTTPS is enforced at the proxy.
- Confirm API docs are disabled and CSP is enforced.
- Run the repository checks from [development.md](development.md).
- Review [SECURITY.md](../SECURITY.md).
