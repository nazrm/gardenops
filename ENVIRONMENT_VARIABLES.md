# GardenOps Environment Variables

This public reference documents the variables needed for a clean GardenOps
instance. Values shown here are placeholders; do not commit real local env files.

## Required

| Variable | Purpose | Example |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string for the app database. | `postgresql://gardenops:change-me@127.0.0.1:5432/gardenops` |
| `GARDENOPS_TEST_POSTGRES_URL` | PostgreSQL connection string for tests. | `postgresql://gardenops:change-me@127.0.0.1:5432/gardenops_test` |
| `APP_ENV` | Runtime environment: `development`, `test`, or `production`. | `development` |

## Authentication

| Variable | Purpose | Example |
|---|---|---|
| `AUTH_REQUIRED` | Require users to sign in. Use `true` outside local demos. | `true` |
| `AUTH_MODE` | Authentication mode. Use `session` for browser deployments. | `session` |
| `AUTH_SESSION_COOKIE_SECURE` | Send session cookies only over HTTPS. | `true` |
| `AUTH_SESSION_TTL_HOURS` | Idle session lifetime in hours, clamped from 1 hour to 30 days. | `12` |
| `AUTH_SESSION_ABSOLUTE_TTL_HOURS` | Absolute session lifetime in hours, clamped from 1 hour to 365 days; activity cannot renew a session beyond it. | `168` |
| `AUTH_MFA_SECRET_KEY` | Secret key for MFA state encryption/signing. Production and internet-exposed session-auth deployments require a generated value with at least 32 characters. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. | _(empty)_ |
| `AUTH_PASSKEY_RP_ID` | Optional WebAuthn relying-party ID for passkey registration and login. Must match the public hostname, without scheme or port. | `gardenops.example.com` |
| `AUTH_PASSKEY_ORIGINS` | Optional comma-separated exact browser origins allowed for passkeys. Use HTTPS outside localhost/test development. | `https://gardenops.example.com` |
| `AUTH_PASSKEY_CHALLENGE_TTL_SECONDS` | Optional passkey ceremony challenge lifetime, clamped between 60 and 900 seconds. | `300` |
| `AUTH_PASSKEY_REGISTER_RATE_LIMIT` | Optional per-client limit for logged-in passkey registration attempts and current-password checks. | `10` |
| `AUTH_PASSKEY_PROMPT_DISMISS_RATE_LIMIT` | Optional per-client limit for dismissing the logged-in passkey enrollment prompt. | `20` |
| `AUTH_INVITE_PASSKEY_REGISTER_RATE_LIMIT` | Optional per-client limit for invite-scoped passwordless passkey registration options and verify requests. | `20` |
| `AUTH_INVITE_PASSKEY_REGISTER_TOKEN_RATE_LIMIT` | Optional per-token limit for invite-scoped passwordless passkey registration options requests. | `6` |
| `AUTH_INVITE_PASSKEY_REGISTER_INVITEE_RATE_LIMIT` | Optional per-invitee limit for invite-scoped passwordless passkey registration options requests. | `6` |
| `AUTH_ADAPTIVE_FRICTION_FLOWS` | Optional comma-separated public auth flows covered by adaptive friction when enabled. Supported defaults include `login`, `reset-password`, `invitation-accept`, and `invitation-passkey-register`. | `login,reset-password,invitation-accept,invitation-passkey-register` |
| `APP_SECRETS_ENCRYPTION_KEY` | Fernet key used to encrypt platform-managed provider secrets stored in the database. Required before admins can save or clear provider keys from the admin UI. | `change-me` |

## HTTP And Proxy

| Variable | Purpose | Example |
|---|---|---|
| `INTERNET_EXPOSED` | Enables internet-exposed safety checks. | `true` |
| `ALLOW_INSECURE_REMOTE` | Allows unsafe remote settings only for deliberate local use. | `false` |
| `CORS_ALLOW_ORIGINS` | Comma-separated allowed browser origins. | `https://gardenops.example.com` |
| `ALLOWED_HOSTS` | Comma-separated accepted Host headers. | `gardenops.example.com` |
| `TRUST_PROXY_HEADERS` | Trust reverse-proxy forwarded headers. | `true` |
| `TRUSTED_PROXY_CIDRS` | CIDRs allowed to set trusted proxy headers. | `127.0.0.1/32,::1/128` |

## Rate Limiting

| Variable | Purpose | Example |
|---|---|---|
| `RATE_LIMIT_BACKEND` | `memory` for local use, `redis` for production. | `redis` |
| `RATE_LIMIT_REDIS_URL` | Redis URL for production rate limiting. | `redis://127.0.0.1:6379/0` |

## Frontend Build-Time

These `VITE_` values are embedded in the browser bundle at build time and are
not secrets.

| Variable | Purpose | Example |
|---|---|---|
| `VITE_APP_NAME` | Public product name shown by the frontend. | `GardenOps` |
| `VITE_APP_SLUG` | Public slug used by the frontend for generated labels and filenames. | `gardenops` |

## Optional Providers

Platform admins can manage OpenAI, Anthropic, PlantNet, and server-side
ShadeMap keys from the admin UI when `APP_SECRETS_ENCRYPTION_KEY` is
configured. The environment variables below remain supported as deploy-time
fallbacks.

Generate a storage key with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| Variable | Purpose | Example |
|---|---|---|
| `AI_PROVIDER` | Configured LLM provider for AI features. Use `anthropic` or `openai`; leave unset to disable LLM-backed AI features. | unset |
| `ANTHROPIC_API_KEY` | Anthropic key used when `AI_PROVIDER=anthropic`. Leave unset to disable Anthropic-backed AI. | `change-me` |
| `ANTHROPIC_MODEL` | Anthropic model for configured AI features. | `claude-sonnet-4-6` |
| `OPENAI_API_KEY` | OpenAI key used when `AI_PROVIDER=openai`. Leave unset to disable OpenAI-backed AI. | `change-me` |
| `OPENAI_MODEL` | OpenAI model for configured AI features. | `gpt-5.5` |
| `OPENAI_FAST_MODEL` | Optional lower-cost OpenAI model reserved for future fast-path AI tasks. | `gpt-5.4-mini` |
| `AI_TASK_DESCRIPTION_DAILY_BUDGET_USER` | Optional per-user daily budget for AI-generated task descriptions. | `60` |
| `AI_TASK_DESCRIPTION_DAILY_BUDGET_GARDEN` | Optional per-garden daily budget for AI-generated task descriptions. | `180` |
| `AI_TASK_DESCRIPTION_CONCURRENCY_LIMIT` | Optional concurrency limit for AI-generated task descriptions. | `1` |
| `PLANTNET_API_KEY` | Optional plant-identification provider. | `change-me` |
| `WEATHER_API_KEY` | Optional weather provider key. | `change-me` |

## ShadeMap Integration

ShadeMap is a paid third-party service. GardenOps does not provide ShadeMap API
access; operators must obtain their own access from
`https://shademap.app/about` and follow the provider terms. See
`docs/shademap.md` for the full integration guide, data-flow notes, local
terrain setup, and production cautions.

| Variable | Purpose | Example |
|---|---|---|
| `SHADEMAP` | Server-side ShadeMap API key, checked first. Required to enable the ShadeMap panel. Leave unset to disable the integration. | `change-me` |
| `SHADEMAP_API_KEY` | Alternative server-side ShadeMap API key. | `change-me` |
| `SHADEMAP_KEY` | Alternative server-side ShadeMap API key. | `change-me` |
| `SHADEMAP_PUBLIC_API_KEY` | Browser-safe ShadeMap key returned to authenticated clients by `/api/shademap/config`. Required to enable the panel. | `change-me` |
| `SHADEMAP_PUBLIC_KEY` | Alternative client/public ShadeMap key. | `change-me` |
| `SHADEMAP_CLIENT_KEY` | Alternative client/public ShadeMap key. | `change-me` |
| `SHADEMAP_TILE_SIGNING_SECRET` | GardenOps HMAC secret for signed same-origin terrain tile URLs. This is not a ShadeMap key. Must be a unique random value in production. | unset |
| `SHADEMAP_TILE_TOKEN_TTL_SECONDS` | Terrain tile token lifetime. Clamped by the app. | `600` |
| `SHADEMAP_TERRAIN_URL_TEMPLATE` | Optional remote Terrarium-compatible PNG terrain tile URL template with `{z}`, `{x}`, and `{y}` placeholders. This is for remote tile fetching, not a local file path. | unset |
| `SHADEMAP_OVERPASS_URL` | Optional single Overpass API URL for building feature data. | unset |
| `SHADEMAP_OVERPASS_URLS` | Optional comma-separated Overpass fallback URLs. | unset |
| `SHADEMAP_SHARE_URL` | Optional public-safe ShadeMap share URL for your own location. | unset |
| `SHADEMAP_LAT` | Optional latitude override for the default map location. | `51.50095` |
| `SHADEMAP_LNG` | Optional longitude override for the default map location. | `-0.12448` |
| `SHADEMAP_ZOOM` | Optional default ShadeMap zoom. | `17` |
| `SHADEMAP_LABEL` | Optional default map marker label. | `Garden` |
| `SHADEMAP_HOUSE_HEIGHT_METERS` | Default house extrusion height for terrain/house shadow calculations. | `9.0` |
| `SHADEMAP_LOCAL_TERRAIN_PATH` | Optional path to a private local LiDAR `.laz` terrain file. This is the local terrain input; do not commit the file. | unset |
| `SHADEMAP_LOCAL_TERRAIN_RESOLUTION_M` | Local terrain grid resolution in meters. | `1.0` |

## Advanced Variable Families

Most instances can keep these unset and use the application defaults. They are
documented so operators and PR checks have complete coverage of every runtime
environment variable read by the public app.

| Variable or family | Purpose | Example |
|---|---|---|
| `APP_NAME` | Runtime product name used in metadata, MFA issuer defaults, calendar exports, and user-agent labels. | `GardenOps` |
| `APP_SLUG` | Runtime slug used for exports and user-agent labels. | `gardenops` |
| `MULTI_INSTANCE` | Enables multi-instance deployment assumptions. | `false` |
| `API_<SETTING>` | API docs, mutation, and request-timeout tuning. Production and internet-exposed deployments reject enabled API docs. | `API_DOCS_ENABLED=false` |
| `MAX_<SETTING>` | Request body limits for standard API, import, and AI photo routes. | `MAX_API_BODY_BYTES=1048576` |
| `CLIENT_ERROR_RATE_LIMIT` | Rate limit applied to repeated client-error responses. | `60` |
| `MUTATION_RATE_LIMIT` | General mutation request rate limit. | `20` |
| `AUTH_<SETTING>` | Authentication, session-cookie, CSRF, password-policy, invitation, MFA, adaptive-friction, bootstrap, and admin step-up settings. | `AUTH_ADMIN_MFA_REQUIRED=true` |
| `CSP_REPORT_<SETTING>` | Content Security Policy report mode, endpoint, rate, and body-size settings. Internet-exposed deployments reject report-only mode. | `CSP_REPORT_ONLY=false` |
| `RATE_LIMIT_<SETTING>` | Rate-limit backend, Redis, bucket, timeout, and global-limit settings. | `RATE_LIMIT_MAX_BUCKETS=50000` |
| `REDIS_URL` | Fallback Redis URL when `RATE_LIMIT_REDIS_URL` is not set. | `redis://127.0.0.1:6379/0` |
| `PROVIDER_CONCURRENCY_LEASE_TTL_SECONDS` | Redis-backed provider concurrency lease lifetime; keep above the expected provider request timeout. | `120` |
| `AI_<SETTING>` | AI feature rate limits, quotas, concurrency limits, care-batch settings, garden-chat provider timeout/output tuning, and rich-context opt-in. | `AI_CHAT_PROVIDER_TIMEOUT_SECONDS=60` |
| `ANTHROPIC_API_<SETTING>` | Anthropic provider timeout and retry settings. | `ANTHROPIC_API_TIMEOUT_SECONDS=25` |
| `OPENAI_API_<SETTING>` | OpenAI provider timeout and retry settings. | `OPENAI_API_TIMEOUT_SECONDS=25` |
| `PLANTNET_<SETTING>` | PlantNet provider timeout and confidence-threshold settings. | `PLANTNET_CONFIDENCE_THRESHOLD=0.40` |
| `PLANT_COVER_IMPORT_<SETTING>` | External plant-cover import timeout, redirect, and page-size limits. | `PLANT_COVER_IMPORT_TIMEOUT_SECONDS=8` |
| `CSV_IMPORT_MAX_ROWS` | Maximum CSV rows accepted by import endpoints. | `5000` |
| `MEDIA_<SETTING>` | Media upload storage directory, byte quotas, asset limits, pixel limits, preview sizing, and upload rate limits. | `MEDIA_MAX_UPLOAD_BYTES=10485760` |
| `GARDEN_<SETTING>` | Garden invitation, membership, settings, onboarding, and zone-creation rate limits. | `GARDEN_SETTINGS_UPDATE_RATE_LIMIT=10` |
| `NOTIFICATION_<SETTING>` | Notification generation, delivery, maintenance, digest, and task-scan limits. | `NOTIFICATION_GENERATE_RATE_LIMIT=10` |
| `GARDENOPS_WEATHER_EXTERNAL_FETCH_ENABLED` | Allows remote weather-provider fetches. Defaults to enabled outside tests and disabled in `APP_ENV=test`; deterministic runners set it to `false` explicitly. | `true` |
| `GARDENOPS_LOGS_DIR` | Directory for runtime JSONL error logs. | `./logs` |
| `GARDENOPS_ATTENTION_<SETTING>` | Attention test clock and guarded E2E settings. Keep unset in normal production; use documented values only for deterministic tests and the disposable Attention Today E2E runner. | `GARDENOPS_ATTENTION_FROZEN_DATE=2026-07-05` |
| `GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER` | Test-only local AI fixture switch. It is honored only when `APP_ENV=test` and the value is exactly `1`; keep it unset in development and production. | unset |
| `GARDENOPS_NOTIFICATION_SCHEDULER_<SETTING>` | Background notification scheduler enablement, poll interval, and lease duration. | `GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=auto` |
| `GARDENOPS_SMTP_<SETTING>` | SMTP host, port, sender, username, password, and TLS settings for notification email. | `GARDENOPS_SMTP_HOST=smtp.example.com` |
| `SHADEMAP_<SETTING>` | ShadeMap keys, location defaults, terrain, Overpass, token, rate-limit, quota, distinct-bound, and local terrain settings. | `SHADEMAP_TILE_TOKEN_TTL_SECONDS=600` |
| `TERRAIN_REQUEST_TIMEOUT_SECONDS` | Timeout for outbound terrain tile fetches. | `20` |
| `SECURITY_TELEMETRY_PRIVACY_SALT` | Deployment-specific salt for hashing identifiers when security telemetry privacy mode is `minimized`. Required in production or internet-exposed mode when security telemetry is enabled. | `change-me-generated-salt` |
| `SECURITY_TELEMETRY_<SETTING>` | Security telemetry webhook, token, delivery format, privacy, batching, polling, and timeout settings. | `SECURITY_TELEMETRY_PRIVACY_MODE=minimized` |
| `SECURITY_METRICS_MAX_KEYS` | Maximum number of distinct in-memory security metric keys retained before new arbitrary metric names are coalesced. | `2048` |
| `TAILLIGHT_<SETTING>` | Optional Taillight-compatible log and telemetry sink settings. | `TAILLIGHT_URL=https://logs.example.com` |
| `ALERT_<SETTING>` | Security alert thresholds shown in admin/security views. | `ALERT_AUTH_FAILURES_PER_MINUTE=30` |
| `DEPLOYED_READINESS_ADMIN_BEARER_TOKEN` | Optional bearer token for protected system health checks. Keep secret and do not use a user session token. | unset |

## Local Development Defaults

For local development, start with `.env.example`, keep provider keys unset, and
set `AUTH_REQUIRED=false` only when you intentionally want an unauthenticated
local demo.

For tests, copy `.env.test.example` to `.env.test.local` and keep both
`DATABASE_URL` and `GARDENOPS_TEST_POSTGRES_URL` pointed at the disposable test
database. Do not source the runtime `.env` or production service env for pytest.
