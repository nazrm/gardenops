# Deployment

GardenOps can run as a standard FastAPI service behind nginx with PostgreSQL
and optional Redis.

## Production Baseline

Use this baseline for any internet-exposed deployment:

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
AUTH_MFA_SECRET_KEY=change-me
```

Add provider keys only for integrations you intend to enable.

## Systemd And Nginx

The `deploy/` directory contains example files:

- `gardenops.service.example`
- `nginx.production.example.conf`

Review and adapt them for your host paths, service user, Python environment,
TLS termination, proxy topology, upload size, log retention, and backup policy.

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
/opt/gardenops/.venv/bin/python -c "import gardenops.db as db; db.run_migrations()"
```

Test restores before relying on backups. Keep database dumps, media uploads,
and local terrain files out of Git.

## Redis

Use Redis for production rate limiting:

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
- Confirm HTTPS is enforced at the proxy.
- Confirm API docs are disabled unless you deliberately enabled them.
- Run the repository checks from [development.md](development.md).
- Review [SECURITY.md](../SECURITY.md).
