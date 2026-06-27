# Configuration

GardenOps is configured with environment variables. Start with `.env.example`
for local development and use a secret manager or host-owned env file for
production.

The complete public reference is [../ENVIRONMENT_VARIABLES.md](../ENVIRONMENT_VARIABLES.md).

## Minimum Local Configuration

Runtime `.env`:

```bash
APP_ENV=development
INTERNET_EXPOSED=false
AUTH_REQUIRED=false
AUTH_MODE=session
DATABASE_URL=postgresql://gardenops:change-me@127.0.0.1:5432/gardenops
```

Dedicated `.env.test.local`:

```bash
APP_ENV=test
DATABASE_URL=postgresql://gardenops:change-me@127.0.0.1:5432/gardenops_test
GARDENOPS_TEST_POSTGRES_URL=postgresql://gardenops:change-me@127.0.0.1:5432/gardenops_test
```

## Minimum Production Configuration

```bash
APP_ENV=production
INTERNET_EXPOSED=true
AUTH_REQUIRED=true
AUTH_MODE=session
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
APP_SECRETS_ENCRYPTION_KEY=change-me
```

Production and internet-exposed deployments must keep API docs disabled.
Internet-exposed deployments must also enforce CSP; `CSP_REPORT_ONLY=true`
is rejected at startup. Session-auth deployments in either mode must set
`AUTH_MFA_SECRET_KEY` to a generated secret with at least 32 characters. Generate
one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and
paste the output as the value.
Production, internet-exposed, and multi-instance deployments must use the Redis
rate-limit backend; the in-memory backend is only for local development and
tests.

If security telemetry is enabled with `SECURITY_TELEMETRY_WEBHOOK_URL` and the
privacy mode is `minimized`, production and internet-exposed deployments must
set a deployment-specific `SECURITY_TELEMETRY_PRIVACY_SALT`. Do not reuse the
public default placeholder because it would make hashed telemetry identifiers
linkable across deployments.

If an older deployment copied the previous public placeholder value, rotate it
while the service is private: start a maintenance instance with
`APP_ENV=development` and `INTERNET_EXPOSED=false` using the old value, disable
and re-enroll MFA for affected admins, then set a generated
`AUTH_MFA_SECRET_KEY` and restart in strict mode. Do not expose the app while
using the old placeholder key.

For first login, set `AUTH_BOOTSTRAP_USERNAME` and
`AUTH_BOOTSTRAP_PASSWORD`, start the app, create the admin user, then remove or
rotate those bootstrap values.

## Passkeys And Invitations

Passkeys require `AUTH_PASSKEY_RP_ID` and `AUTH_PASSKEY_ORIGINS` to match the
public browser hostname and origin. When configured, existing password users are
offered a passkey after login and can dismiss that prompt for a quiet period.
Passwordless passkey registration is only available through a valid invitation:
the invitee username must match the invitation, existing usernames are rejected,
and platform-admin invitations still require the password-based invitation path.

Normal password reset tokens do not convert passkey-only accounts back to
password accounts. Admins must issue an explicit `passwordless_recovery` reset
token when a passkey-only user needs password recovery. Using that recovery
token revokes existing passkeys before enabling password authentication so the
account has one active recovery path.

## Optional Providers

Provider keys are optional. Leave them unset to disable the associated feature.
Platform admins can also set OpenAI, Anthropic, PlantNet, and server-side
ShadeMap keys from the admin UI. Admin-managed keys are encrypted in
`app_secrets` and require `APP_SECRETS_ENCRYPTION_KEY`; environment variables
remain fallback values.

| Area | Common variables |
|---|---|
| AI assistant features | `AI_PROVIDER`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Plant identification | `PLANTNET_API_KEY` |
| Weather | `WEATHER_API_KEY` |
| ShadeMap | `SHADEMAP`, `SHADEMAP_PUBLIC_API_KEY`, `SHADEMAP_TILE_SIGNING_SECRET` |

`AI_PROVIDER` accepts `anthropic` or `openai`. Leave it unset to disable
LLM-backed AI features. Plant identification still tries PlantNet first when
`PLANTNET_API_KEY` is configured; the configured AI provider is used only as
fallback for identification, and directly for diagnosis and other AI features.
Garden chat uses the configured OpenAI fast model when OpenAI is active, caps
chat output with `AI_CHAT_MAX_OUTPUT_TOKENS` (default `1024`), and gives the
upstream provider `AI_CHAT_PROVIDER_TIMEOUT_SECONDS` (default `60`) before
returning a timeout response to the browser. Keep that backend timeout below
the frontend garden-chat timeout so users receive the clearer AI timeout
message instead of a generic network abort.
AI-generated task descriptions have separate daily budgets and concurrency
controls: `AI_TASK_DESCRIPTION_DAILY_BUDGET_USER` defaults to `60`,
`AI_TASK_DESCRIPTION_DAILY_BUDGET_GARDEN` defaults to `180`, and
`AI_TASK_DESCRIPTION_CONCURRENCY_LIMIT` defaults to `1`.

Generate `APP_SECRETS_ENCRYPTION_KEY` with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep this value stable and private. Losing it makes encrypted database-managed
provider keys unrecoverable; re-enter the keys from the admin UI after replacing
the storage key.

## ShadeMap Configuration

ShadeMap has a dedicated guide at [shademap.md](shademap.md). It is a paid
third-party service, not something GardenOps includes. Obtain your own API
access from [https://shademap.app/about](https://shademap.app/about), then
configure both the server-side key and browser-safe client key required by your
ShadeMap account.

At minimum for an enabled production ShadeMap panel, configure:

```bash
SHADEMAP=change-me
SHADEMAP_PUBLIC_API_KEY=change-me
SHADEMAP_TILE_SIGNING_SECRET=<generate-a-unique-random-secret>
```

Leave ShadeMap keys unset to disable the integration. Server-side ShadeMap keys
are platform-admin-only and are no longer stored per user. Do not set only the
tile signing secret and assume the provider integration is active; GardenOps
still needs valid ShadeMap access.

Set `SHADEMAP_SHARE_URL`, `SHADEMAP_LAT`, `SHADEMAP_LNG`, and `SHADEMAP_ZOOM`
to your own garden area. Do not publish exact private coordinates unless that is
intentional for your deployment.

## Secret Rules

- Do not commit `.env`.
- Do not paste secrets into issues, PR descriptions, test fixtures, screenshots,
  or docs.
- Use placeholder values such as `change-me`, `example`, `test`, or `disabled`
  in public documentation.
- Rotate secrets if they have ever been committed or logged.
