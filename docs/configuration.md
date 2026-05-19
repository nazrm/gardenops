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
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0
AUTH_MFA_SECRET_KEY=change-me
```

For first login, set `AUTH_BOOTSTRAP_USERNAME` and
`AUTH_BOOTSTRAP_PASSWORD`, start the app, create the admin user, then remove or
rotate those bootstrap values.

## Optional Providers

Provider keys are optional. Leave them unset to disable the associated feature.

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
SHADEMAP_TILE_SIGNING_SECRET=change-me
```

Leave ShadeMap keys unset to disable the integration. Do not set only the tile
signing secret and assume the provider integration is active; GardenOps still
needs valid ShadeMap access.

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
