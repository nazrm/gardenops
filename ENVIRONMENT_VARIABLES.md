# GardenOps Environment Variables

This public reference documents the variables needed for a clean GardenOps
instance. Values shown here are placeholders; do not commit real local env files.

## Required

| Variable | Purpose | Example |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string for the app database. | `postgresql://gardenops:change-me@127.0.0.1:5432/gardenops` |
| `GARDENOPS_TEST_POSTGRES_URL` | PostgreSQL connection string for tests. | `postgresql://gardenops:change-me@127.0.0.1:5432/gardenops_test` |
| `APP_ENV` | Runtime environment: `development`, `test`, or `production`. | `development` |

## Observation Dates

| Variable | Purpose | Default |
|---|---|---|
| `GARDENOPS_TIMEZONE` | IANA timezone for task occurrence defaults, future-date validation, and observation years. Invalid timezone names are rejected. | `Europe/Oslo` |

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

Platform admins can manage OpenAI and PlantNet keys from the admin UI when
`APP_SECRETS_ENCRYPTION_KEY` is configured. The environment variables below
remain supported as deploy-time fallbacks.

Generate a storage key with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

| Variable | Purpose | Example |
|---|---|---|
| `AI_PROVIDER` | Configured LLM provider for AI features. Use `openai` or `disabled`; leave unset to disable LLM-backed AI features. | unset |
| `OPENAI_API_KEY` | OpenAI key used when `AI_PROVIDER=openai`. Leave unset to disable OpenAI-backed AI. | `change-me` |
| `OPENAI_MODEL` | OpenAI model for configured AI features. | `gpt-5.5` |
| `OPENAI_FAST_MODEL` | Optional lower-cost OpenAI model reserved for future fast-path AI tasks. | `gpt-5.4-mini` |
| `AI_TASK_DESCRIPTION_DAILY_BUDGET_USER` | Optional per-user daily budget for AI-generated task descriptions. | `60` |
| `AI_TASK_DESCRIPTION_DAILY_BUDGET_GARDEN` | Optional per-garden daily budget for AI-generated task descriptions. | `180` |
| `AI_TASK_DESCRIPTION_CONCURRENCY_LIMIT` | Optional concurrency limit for AI-generated task descriptions. | `1` |
| `PLANTNET_API_KEY` | Optional plant-identification provider. | `change-me` |
| `WEATHER_API_KEY` | Optional weather provider key. | `change-me` |

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
| `GARDENOPS_E2E_LOOPBACK_PROVIDER` | Test-only Phase 7 provider fixture switch. It is honored only in the runner-issued `APP_ENV=test` disposable child, with the matching database URL, PostgreSQL marker, expected commit, and private artifact directory. Keep it unset outside that runner. | unset |
| `GARDENOPS_E2E_PROVIDER_URL` | Test-only loopback provider base URL. The runner-bound override accepts only `http://127.0.0.1` with a non-PostgreSQL port and the exact `/v1` path. | `http://127.0.0.1:19001/v1` |
| `GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR` | Private ignored artifact directory for the disposable complete-journey runner. It is not a normal runtime media or terrain directory. | unset |
| `GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD` | Runner-issued marker for the disposable complete-journey child process. It must be exactly `1` before a local provider fixture can be used. | unset |
| `GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE` | Runner-issued destructive-test acknowledgement for the disposable complete-journey child. It must be exactly `1`; never set it in a normal app process. | unset |
| `GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD` | Runner-issued 40-character commit identifier binding private complete-journey artifacts and loopback fixtures to the reviewed source revision. | unset |
| `GARDENOPS_DISPOSABLE_POSTGRES_URL` | Runner-issued disposable PostgreSQL URL. The complete-journey child requires it to equal `DATABASE_URL` before local provider fixtures are enabled. | unset |
| `GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER` | Runner-issued numeric PostgreSQL cluster identifier used to bind the disposable database marker. | unset |
| `GARDENOPS_DISPOSABLE_POSTGRES_MARKER` | Runner-issued database marker beginning with the matching disposable PostgreSQL system identifier. | unset |
| `GARDENOPS_NOTIFICATION_SCHEDULER_<SETTING>` | Background notification scheduler enablement, poll interval, and lease duration. | `GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=auto` |
| `GARDENOPS_SMTP_<SETTING>` | SMTP host, port, sender, username, password, and TLS settings for notification email. | `GARDENOPS_SMTP_HOST=smtp.example.com` |
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
