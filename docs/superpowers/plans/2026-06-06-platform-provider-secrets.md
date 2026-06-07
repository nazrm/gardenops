# Platform Provider Secrets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add platform-admin-only management for OpenAI and Anthropic API keys, allow platform admins to choose which AI provider is active, and move ShadeMap keys to the same platform-admin-only model while deleting existing per-user ShadeMap keys.
**Architecture:** Store non-secret provider settings in `app_settings` and encrypted secret values in a new `app_secrets` table. Runtime code resolves encrypted database secrets first and falls back to environment variables for deployment compatibility. Admin-only API endpoints expose secret metadata but never plaintext. The admin UI writes secrets through destructive-admin controls and removes all per-user ShadeMap controls.
**Tech Stack:** FastAPI, Pydantic, PostgreSQL migrations, psycopg, `cryptography.fernet`, TypeScript admin panel, Vite, pytest.
**Implementation Status:** Executed on branch `codex/platform-provider-secrets` in `/tmp/gardenops-platform-provider-secrets`; keep the checklist below as the implementation trace/spec.

---

## Current Context

- AI provider selection and key lookup are currently env-only in `gardenops/services/ai_provider.py`.
- PlantNet lookup in `gardenops/routers/ai.py` reads `PLANTNET_API_KEY` directly from environment.
- ShadeMap lookup in `gardenops/routers/shademap.py` supports env keys, `auth_users.shademap_api_key`, and `app_settings.shademap_api_key`.
- Per-user ShadeMap settings are exposed in `gardenops/routers/auth.py`, `frontend/src/services/api.ts`, and `frontend/src/components/adminPanel.ts`.
- Admin-only destructive controls already exist via `_require_admin_context(request)` and `enforce_destructive_admin_controls` in `gardenops/routers/auth.py`.
- Admin edge policy is centralized in `gardenops/admin_edge_policy.py`.
- Existing migrations end at `migrations/0013_plot_garden_layout_enforcement.sql`; use `0014_platform_provider_secrets.sql`.

## Target Behavior

- Only platform admins can read provider-secret status or write provider-secret settings.
- The platform can store keys for both OpenAI and Anthropic at the same time.
- The active AI provider is one of `disabled`, `openai`, or `anthropic`.
- Secret API responses return only metadata: configured state, source, redacted suffix, update timestamp, and updater identity when available.
- Secret writes require destructive-admin controls and an action reason.
- Runtime provider calls use the selected provider and the matching key.
- Existing env variables remain valid fallbacks:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `PLANTNET_API_KEY`
  - `SHADEMAP_API_KEY`, `SHADEMAP_KEY`, `SHADEMAP`
- New database-managed secret writes require `APP_SECRETS_ENCRYPTION_KEY`.
- Per-user ShadeMap keys are removed from schema, backend payloads, frontend UI, and admin user management.
- Existing per-user ShadeMap key data is deleted by migration `0014`.

## File Structure

```text
migrations/0014_platform_provider_secrets.sql
gardenops/platform_secrets.py
gardenops/provider_settings.py
gardenops/routers/provider_settings.py
gardenops/routers/auth.py
gardenops/routers/ai.py
gardenops/routers/shademap.py
gardenops/services/ai_provider.py
gardenops/admin_edge_policy.py
gardenops/main.py
frontend/src/services/api.ts
frontend/src/components/adminPanel.ts
frontend/src/core/i18n.ts
tests/test_platform_secrets.py
tests/test_provider_settings.py
tests/test_ai_provider.py
tests/test_identify.py
tests/test_shademap.py
tests/test_auth_endpoints.py
ENVIRONMENT_VARIABLES.md
README.md
docs/configuration.md
docs/shademap.md
docs/ai-provider-plan.md
```

## Secret Model

Use a narrow set of managed secret names:

```python
OPENAI_API_KEY = "openai_api_key"
ANTHROPIC_API_KEY = "anthropic_api_key"
PLANTNET_API_KEY = "plantnet_api_key"
SHADEMAP_API_KEY = "shademap_api_key"
```

Use a narrow set of managed setting names:

```python
AI_PROVIDER = "ai_provider"                  # disabled | openai | anthropic
OPENAI_MODEL = "openai_model"
OPENAI_FAST_MODEL = "openai_fast_model"
ANTHROPIC_MODEL = "anthropic_model"
```

Default setting values:

```python
AI_PROVIDER = "disabled"
OPENAI_MODEL = "gpt-5-mini"
OPENAI_FAST_MODEL = "gpt-5-mini"
ANTHROPIC_MODEL = "claude-3-5-haiku-latest"
```

The exact model defaults can mirror current env defaults if the existing code uses different values; keep existing behavior unless a current default is clearly invalid.

---

## Task 1: Add The Encrypted Secret Store Migration

- [ ] Create `migrations/0014_platform_provider_secrets.sql`.
- [ ] Add `public.app_secrets`.
- [ ] Delete legacy plaintext ShadeMap app setting.
- [ ] Delete per-user ShadeMap keys.
- [ ] Drop `auth_users.shademap_api_key` if existing test and production schema checks tolerate it.
- [ ] Update schema-signature tests or schema snapshots that assert `auth_users` columns.

Migration shape:

```sql
CREATE TABLE IF NOT EXISTS public.app_secrets (
    key text PRIMARY KEY,
    encrypted_value bytea NOT NULL,
    encryption_key_id text NOT NULL DEFAULT 'app',
    value_last4 text,
    created_at_ms bigint NOT NULL DEFAULT ((extract(epoch FROM now()) * 1000)::bigint),
    updated_at_ms bigint NOT NULL DEFAULT ((extract(epoch FROM now()) * 1000)::bigint),
    updated_by_user_id uuid REFERENCES public.auth_users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS app_secrets_updated_by_user_id_idx
    ON public.app_secrets(updated_by_user_id);

DELETE FROM public.app_settings
WHERE key = 'shademap_api_key';

UPDATE public.auth_users
SET shademap_api_key = NULL
WHERE shademap_api_key IS NOT NULL;

ALTER TABLE public.auth_users
DROP COLUMN IF EXISTS shademap_api_key;
```

Implementation notes:

- Do not migrate legacy per-user ShadeMap values into platform secrets because the user explicitly asked to delete them.
- Keep `value_last4` as a separate non-secret metadata field for UI confirmation.
- Use `bytea` for encrypted payload bytes.
- If a schema signature fixture includes column lists, regenerate the expected fixture through the repo’s existing schema tooling rather than manually changing only one assertion.

Verification:

```bash
uv run pytest tests/test_schema_signature.py tests/test_migrations.py
```

If either file does not exist in the repo, run the closest migration/schema test discovered with `rg -n "schema_signature|migrations|auth_users" tests`.

---

## Task 2: Add `gardenops/platform_secrets.py`

- [ ] Implement encryption-key loading from `APP_SECRETS_ENCRYPTION_KEY`.
- [ ] Implement secret set, clear, lookup, and metadata helpers.
- [ ] Ensure plaintext is never logged or returned by metadata helpers.
- [ ] Unit test invalid, missing, set, update, clear, and decrypt paths.

Encryption behavior:

- Use `cryptography.fernet.Fernet`.
- `APP_SECRETS_ENCRYPTION_KEY` must be a Fernet key.
- Generate a key for deployments with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Public helper contract:

- `SecretMetadata`: frozen dataclass with `key`, `configured`, `source`, `last4`, `updated_at_ms`, and `updated_by_user_id`.
- `get_database_secret(conn, key)`: returns decrypted database value or `None`.
- `set_database_secret(conn, key, value, updated_by_user_id=None)`: encrypts and upserts a value, then returns metadata.
- `clear_database_secret(conn, key)`: deletes the database secret row.
- `database_secret_metadata(conn, key)`: returns metadata for the database row without decrypting plaintext.
- `secret_metadata_with_env_fallback(conn, key, env_names)`: returns database metadata when present, env metadata when only env is present, otherwise `source="none"`.

Validation rules:

- Reject unknown secret keys before database writes.
- Reject empty values in `set_database_secret`; clearing must use the explicit clear helper.
- Treat missing `APP_SECRETS_ENCRYPTION_KEY` as:
  - read path: database secret decrypt cannot proceed and should raise a configuration error only if an encrypted DB value is needed.
  - write path: return a `503` from the router because the platform cannot safely store secrets.
- Redact with last four non-whitespace characters only.

Tests in `tests/test_platform_secrets.py`:

- `test_set_and_get_database_secret_round_trips_encrypted_value`
- `test_metadata_never_contains_plaintext`
- `test_clear_database_secret_removes_secret`
- `test_write_requires_encryption_key`
- `test_unknown_secret_key_is_rejected`
- `test_env_fallback_metadata_uses_env_without_exposing_value`

Verification:

```bash
uv run pytest tests/test_platform_secrets.py
```

---

## Task 3: Add `gardenops/provider_settings.py`

- [ ] Centralize provider setting names, secret names, env fallback names, and defaults.
- [ ] Implement admin summary building for `GET /api/admin/provider-settings`.
- [ ] Implement runtime config resolution for AI, PlantNet, and ShadeMap.
- [ ] Keep existing environment-only deployments working.

Runtime helper contract:

- `AiRuntimeConfig`: frozen dataclass with `provider`, `openai_api_key`, `anthropic_api_key`, `openai_model`, `openai_fast_model`, and `anthropic_model`.
- `get_ai_runtime_config()`: returns the selected provider, resolved models, and only the runtime key values needed by provider clients.
- `get_plantnet_api_key()`: returns the database-managed PlantNet key with env fallback.
- `get_shademap_api_key()`: returns the database-managed server-side ShadeMap key with env fallback.
- `get_provider_settings_summary(conn)`: returns API-safe provider settings and secret metadata.
- `apply_provider_settings_update(conn, body, actor_user_id)`: validates, writes settings/secrets atomically, and returns the updated summary.

Resolution rules:

- For runtime reads, prefer encrypted DB secret over env fallback.
- For active provider, prefer `app_settings.ai_provider`; if missing, use existing `AI_PROVIDER`; if missing, use `disabled`.
- Validate `ai_provider` before saving and before runtime use.
- For models, prefer `app_settings`; fall back to existing env variables; then use current hard-coded defaults.
- If `ai_provider` is `openai` but no OpenAI key is configured, provider calls should fail with the existing “not configured” behavior rather than silently switching provider.
- If `ai_provider` is `anthropic` but no Anthropic key is configured, provider calls should fail with the existing “not configured” behavior.
- Do not expose decrypted secret values through any public dataclass used by API responses.

Verification:

```bash
uv run pytest tests/test_provider_settings.py tests/test_ai_provider.py tests/test_identify.py tests/test_shademap.py
```

---

## Task 4: Add Platform Admin Provider Settings API

- [ ] Create `gardenops/routers/provider_settings.py`.
- [ ] Register the router in `gardenops/main.py`.
- [ ] Add `/api/admin/provider-settings` entries to `gardenops/admin_edge_policy.py`.
- [ ] Reuse `_require_admin_context(request)` from `gardenops/routers/auth.py`.
- [ ] Reuse `enforce_destructive_admin_controls` for every write request.
- [ ] Emit audit events for every changed setting, set secret, and cleared secret.
- [ ] Increment destructive admin security metrics for write requests.

Endpoints:

```http
GET /api/admin/provider-settings
PUT /api/admin/provider-settings
```

`GET` response:

```json
{
  "ai_provider": "openai",
  "models": {
    "openai_model": "gpt-5-mini",
    "openai_fast_model": "gpt-5-mini",
    "anthropic_model": "claude-3-5-haiku-latest"
  },
  "secrets": {
    "openai_api_key": {
      "configured": true,
      "source": "db",
      "last4": "abcd",
      "updated_at_ms": 1780760000000,
      "updated_by_user_id": "00000000-0000-0000-0000-000000000000",
      "updated_by_username": "admin@example.com"
    },
    "anthropic_api_key": {
      "configured": false,
      "source": "none",
      "last4": null,
      "updated_at_ms": null,
      "updated_by_user_id": null,
      "updated_by_username": null
    },
    "plantnet_api_key": {
      "configured": true,
      "source": "env",
      "last4": "wxyz",
      "updated_at_ms": null,
      "updated_by_user_id": null,
      "updated_by_username": null
    },
    "shademap_api_key": {
      "configured": true,
      "source": "db",
      "last4": "7890",
      "updated_at_ms": 1780760000000,
      "updated_by_user_id": "00000000-0000-0000-0000-000000000000",
      "updated_by_username": "admin@example.com"
    }
  },
  "secrets_encryption_configured": true
}
```

`PUT` request:

```json
{
  "ai_provider": "anthropic",
  "openai_model": "gpt-5-mini",
  "openai_fast_model": "gpt-5-mini",
  "anthropic_model": "claude-3-5-haiku-latest",
  "openai_api_key": "redacted-openai-key-value",
  "anthropic_api_key": "redacted-anthropic-key-value",
  "plantnet_api_key": "plantnet-secret",
  "shademap_api_key": "shademap-secret",
  "clear_openai_api_key": false,
  "clear_anthropic_api_key": false,
  "clear_plantnet_api_key": false,
  "clear_shademap_api_key": false,
  "action_reason": "Rotate platform provider credentials"
}
```

Write semantics:

- Omitted secret fields leave existing secrets unchanged.
- Empty string secret fields are rejected with `422`.
- `clear_*` flags delete the encrypted database secret for that provider.
- Clearing a database secret does not delete env fallback secrets.
- If a clear flag and new value are both sent for the same secret, reject with `422`.
- Settings are committed atomically with secret changes.
- Response body is the same shape as `GET`.

Audit event fields:

```json
{
  "event_type": "provider_settings.updated",
  "actor_user_id": "00000000-0000-0000-0000-000000000000",
  "metadata": {
    "changed_settings": ["ai_provider", "anthropic_model"],
    "set_secrets": ["anthropic_api_key"],
    "cleared_secrets": ["openai_api_key"]
  }
}
```

Do not include plaintext or last-four values in audit metadata unless existing audit conventions require redacted identifiers. If a redacted identifier is needed, use only the secret name.

Tests in `tests/test_provider_settings.py`:

- `test_get_provider_settings_requires_platform_admin`
- `test_put_provider_settings_requires_platform_admin`
- `test_put_provider_settings_requires_destructive_admin_controls`
- `test_put_provider_settings_stores_openai_and_anthropic_keys_without_plaintext_response`
- `test_put_provider_settings_rejects_empty_secret_values`
- `test_put_provider_settings_rejects_clear_and_set_same_secret`
- `test_put_provider_settings_allows_provider_without_key_but_runtime_fails_configured_error`
- `test_provider_settings_edge_policy_classifies_admin_read_and_write`

Verification:

```bash
uv run pytest tests/test_provider_settings.py tests/test_admin_edge_policy.py
```

---

## Task 5: Wire Runtime Consumers To Platform Settings

- [ ] Update `gardenops/services/ai_provider.py` to use `provider_settings.get_ai_runtime_config()`.
- [ ] Update `gardenops/routers/ai.py` PlantNet key lookup to use `provider_settings.get_plantnet_api_key()`.
- [ ] Update `gardenops/routers/shademap.py` server-side key lookup to use `provider_settings.get_shademap_api_key()`.
- [ ] Preserve public-browser ShadeMap key behavior for `SHADEMAP_PUBLIC_API_KEY` or existing equivalent public-key env names.
- [ ] Remove `_read_user_shademap_key` and all `auth_users.shademap_api_key` reads.
- [ ] Remove plaintext `app_settings.shademap_api_key` reads.

AI provider integration:

- Keep public functions in `gardenops/services/ai_provider.py` stable where routers/tests already import them.
- Replace direct `os.getenv("AI_PROVIDER")` and provider-key helpers with the central runtime config.
- Keep existing error status and message class for “not configured” cases when possible.
- Add a test that sets both database keys, switches provider from `openai` to `anthropic`, and verifies the selected provider is the one used.
- Add a test that database key takes precedence over env key without exposing either value in errors.

PlantNet integration:

- Replace direct `os.environ.get("PLANTNET_API_KEY", "")` with central lookup.
- Keep endpoint behavior unchanged when no key exists.
- Add a test that DB PlantNet secret works without env key.

ShadeMap integration:

- Server-side signed/proxy ShadeMap calls use database secret with env fallback.
- Browser-safe public key remains public-key-only and must not use encrypted server secrets.
- Add a regression test that a normal user with no platform-admin role cannot set or read a ShadeMap secret.
- Add a migration or API test showing per-user ShadeMap values are no longer accepted.

Verification:

```bash
uv run pytest tests/test_ai_provider.py tests/test_identify.py tests/test_shademap.py
```

---

## Task 6: Remove Per-User ShadeMap From Auth APIs

- [ ] Update `gardenops/routers/auth.py` Pydantic models to remove `shademap_api_key` and `has_shademap_key`.
- [ ] Remove user settings update code that writes `auth_users.shademap_api_key`.
- [ ] Remove admin user update code that writes `auth_users.shademap_api_key`.
- [ ] Remove user list response fields for `has_shademap_key`.
- [ ] Update tests expecting ShadeMap user fields.

Compatibility decision:

- Prefer removing fields instead of returning false compatibility fields. This makes the platform-admin-only boundary obvious and prevents clients from depending on obsolete user-level controls.
- If any external API contract test proves field removal is too disruptive, return `has_shademap_key: false` for one release but reject writes. Document the temporary compatibility field in the same commit. Do not keep `shademap_api_key` request bodies.

Tests:

- `test_me_settings_does_not_include_shademap_key`
- `test_me_settings_rejects_shademap_key_write`
- `test_admin_user_update_rejects_shademap_key_write`
- `test_admin_user_list_does_not_expose_shademap_key_status`

Verification:

```bash
uv run pytest tests/test_auth_endpoints.py
```

---

## Task 7: Add Admin UI For Provider Settings

- [ ] Add provider-settings API types and functions in `frontend/src/services/api.ts`.
- [ ] Add provider settings state, load, save, clear, and error handling in `frontend/src/components/adminPanel.ts`.
- [ ] Add i18n strings in `frontend/src/core/i18n.ts`.
- [ ] Remove “My ShadeMap key” UI from My Settings.
- [ ] Remove per-user ShadeMap badges/buttons/actions from user administration.
- [ ] Hide all secret input values after successful save.

API types:

```ts
export type ProviderSecretStatus = {
  configured: boolean;
  source: "db" | "env" | "none";
  last4: string | null;
  updated_at_ms: number | null;
  updated_by_user_id: string | null;
  updated_by_username: string | null;
};

export type ProviderSettings = {
  ai_provider: "disabled" | "openai" | "anthropic";
  models: {
    openai_model: string;
    openai_fast_model: string;
    anthropic_model: string;
  };
  secrets: Record<
    "openai_api_key" | "anthropic_api_key" | "plantnet_api_key" | "shademap_api_key",
    ProviderSecretStatus
  >;
  secrets_encryption_configured: boolean;
};

export type ProviderSettingsUpdate = {
  ai_provider?: "disabled" | "openai" | "anthropic";
  openai_model?: string;
  openai_fast_model?: string;
  anthropic_model?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
  plantnet_api_key?: string;
  shademap_api_key?: string;
  clear_openai_api_key?: boolean;
  clear_anthropic_api_key?: boolean;
  clear_plantnet_api_key?: boolean;
  clear_shademap_api_key?: boolean;
  action_reason: string;
};
```

UI behavior:

- Add a platform admin section named “Provider keys” or the existing translated equivalent.
- Provider selector uses a segmented control or select with `Disabled`, `OpenAI`, `Anthropic`.
- Model inputs sit next to the selected provider but both providers’ model settings remain editable.
- Each secret row shows:
  - provider name
  - configured status
  - source `Managed`, `Environment`, or `Not configured`
  - last four characters when available
  - updated timestamp and updater when available
  - password input for replacing the secret
  - clear checkbox or clear icon button when database-managed
- The save button is disabled when destructive-admin controls are incomplete.
- If `secrets_encryption_configured` is false, show a compact warning and disable secret replacement fields while still allowing non-secret setting changes if backend supports that split. If backend uses one write endpoint that validates only touched secrets, disable only secret fields.
- On successful save, clear password input fields and reload server status.

Remove old ShadeMap UI:

- Remove `adm-my-shademap-key`, `adm-my-shademap-save`, `adm-my-shademap-clear`.
- Remove `.adm-act-shademap-key`.
- Remove text and i18n entries for user-level ShadeMap settings.
- Remove `has_shademap_key` from rendered user rows/cards.

Frontend tests and checks:

```bash
npm run typecheck
npm run build
```

If the repo has frontend tests, run the provider-admin-related subset discovered with `rg -n "adminPanel|shademap|provider" frontend tests`.

---

## Task 8: Update Documentation And Operations Notes

- [ ] Update `ENVIRONMENT_VARIABLES.md` with `APP_SECRETS_ENCRYPTION_KEY`.
- [ ] Update `README.md` configuration notes for admin-managed AI provider keys.
- [ ] Update `docs/configuration.md` with storage and fallback behavior.
- [ ] Update `docs/shademap.md` to state ShadeMap keys are platform-admin-only.
- [ ] Update `docs/ai-provider-plan.md` to mark env-only provider setup as superseded by admin-managed provider settings.

Documentation must state:

- Database-managed secrets are encrypted at rest with `APP_SECRETS_ENCRYPTION_KEY`.
- Losing `APP_SECRETS_ENCRYPTION_KEY` makes encrypted database secrets unrecoverable.
- Environment variables remain supported for deploy-time fallback.
- Per-user ShadeMap keys are no longer supported.
- Platform admins can configure both OpenAI and Anthropic keys, but only one active provider is used at runtime.
- API responses never return plaintext secrets.

Verification:

```bash
rg -n "shademap_api_key|has_shademap_key|My ShadeMap|AI_PROVIDER|APP_SECRETS_ENCRYPTION_KEY" README.md ENVIRONMENT_VARIABLES.md docs frontend/src gardenops tests
```

Confirm remaining `shademap_api_key` hits are only platform-secret references or migration cleanup references.

---

## Task 9: Full Verification

- [ ] Run backend focused tests.
- [ ] Run frontend typecheck/build.
- [ ] Run a final text scan for obsolete per-user ShadeMap paths.
- [ ] Check git diff for plaintext secret examples.

Commands:

```bash
uv run pytest tests/test_platform_secrets.py tests/test_provider_settings.py tests/test_ai_provider.py tests/test_identify.py tests/test_shademap.py tests/test_auth_endpoints.py
npm run typecheck
npm run build
rg -n "auth_users\.shademap_api_key|has_shademap_key|adm-my-shademap|adm-act-shademap|app_settings.*shademap_api_key" gardenops frontend/src tests docs migrations
rg -n "sk-[A-Za-z0-9]|sk-ant-|OPENAI_API_KEY=.*[^<]|ANTHROPIC_API_KEY=.*[^<]" .
```

Expected results:

- Backend focused tests pass.
- Frontend typecheck and build pass.
- Obsolete ShadeMap scan has no live code hits.
- Secret-pattern scan has no real plaintext keys in committed files.

---

## Rollout Plan

- [ ] Generate `APP_SECRETS_ENCRYPTION_KEY` on the live server and store it in the same protected environment file as other app secrets.
- [ ] Deploy code and run migrations.
- [ ] Restart the application service.
- [ ] Sign in as a platform admin.
- [ ] Open Admin UI provider settings.
- [ ] Add OpenAI and/or Anthropic API keys.
- [ ] Select the active provider.
- [ ] Add PlantNet and ShadeMap keys if they should be managed from the UI.
- [ ] Confirm normal users cannot access provider settings endpoints.
- [ ] Confirm AI provider, PlantNet, and ShadeMap flows work from live UI.

Operational checks:

```bash
systemctl restart gardenops
systemctl status gardenops --no-pager
journalctl -u gardenops --no-pager -n 100
```

Use the actual service name for this host if it differs.

---

## Risks And Mitigations

- Encryption key missing in production: secret writes return a clear admin-facing error and env fallback keeps existing deploys working.
- Lost encryption key: document that encrypted DB secrets are unrecoverable and must be re-entered.
- Accidental plaintext exposure: keep plaintext only in request bodies and local variables; tests assert API responses and metadata do not include submitted values.
- Provider misconfiguration: active provider can be saved without a key, but runtime returns an explicit configured-error rather than falling back to another provider.
- ShadeMap behavior regression: keep public browser-key behavior separate from server-side secret behavior and test both.
- Migration removes user data: this is intentional for ShadeMap keys per user request; note it in release notes before deploy.
