# Secure Passkey Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secure passkey enrollment prompts for existing users and invite-gated username plus passkey registration for new users.

**Architecture:** Keep existing password login and invite acceptance intact. Add explicit passwordless account state, invite-bound passkey challenges, and backend-authorized prompt state; the frontend only exposes convenience UI backed by server decisions. Passwordless invite registration creates only brand-new non-admin users and atomically accepts the invite after WebAuthn verification.

**Tech Stack:** FastAPI, PostgreSQL migrations, `webauthn`, existing GardenOps auth/session helpers, vanilla TypeScript frontend, pytest, Vite build.

---

## File Structure

- `migrations/0016_passwordless_passkey_onboarding.sql`: schema changes for passwordless auth, random passkey user handles, prompt cooldown, reset-token purpose, and invite-bound passkey challenges.
- `gardenops/schema_signature.py`: required schema signature updates for new columns/indexes/constraints.
- `gardenops/security.py`: nullable password handling, passwordless user creation, random passkey handles, credential auth guards, session context prompt fields.
- `gardenops/passkeys.py`: invitation registration challenge flow, invite-bound challenge fields, random user handle registration options.
- `gardenops/routers/auth.py`: `/auth/me` prompt fields, prompt dismissal endpoint, existing registration nickname default, passwordless invite options/verify, passwordless recovery reset purpose.
- `gardenops/main.py`: public and CSRF-exempt endpoint allowlists for passwordless invite endpoints.
- `frontend/src/services/authApi.ts`: auth-gate API helpers/types for invite passkey registration and prompt fields.
- `frontend/src/services/api.ts`: full-app API helpers/types stay in sync with auth response fields used by settings and app shell.
- `frontend/src/features/passkeys.ts`: no behavioral rewrite expected; use existing `createPasskey`/`getPasskey`.
- `frontend/src/features/authGate.ts`: invite passwordless UI only; it must continue using auth-only APIs and auth-only i18n.
- `frontend/src/app.ts`: app-shell post-login passkey prompt after authenticated startup.
- `frontend/src/core/authI18n.ts`: auth-gate copy.
- `frontend/src/core/i18n.ts`: app/settings copy if prompt is shared with app shell.
- `tests/test_passkeys.py`: passkey registration, passwordless invite, and WebAuthn user-handle tests.
- `tests/test_security.py`: passwordless auth, recovery reset purpose, and invite existing-user rejection tests.
- `tests/test_auth_lifecycle.py`: lifecycle/admin reset interactions if not covered in security tests.
- `tests/test_integrity.py`: schema signature test updates.
- `docs/configuration.md`, `docs/development.md`: operator and developer notes.

## Task 1: Schema And Security Primitives

**Files:**
- Create: `migrations/0016_passwordless_passkey_onboarding.sql`
- Modify: `gardenops/schema_signature.py`
- Modify: `gardenops/security.py`
- Test: `tests/test_integrity.py`
- Test: `tests/test_security_unit.py`

- [ ] **Step 1: Write failing schema signature tests**

Add assertions in `tests/test_integrity.py::MigrationGuardTests.test_passkey_schema_signature_covers_migration_surface` that require:

```python
{
    "password_auth_disabled",
    "passkey_user_handle",
    "passkey_prompt_dismissed_until_ms",
}.issubset(set(REQUIRED_COLUMNS["auth_users"]))
{
    "invitation_token_hash",
    "invitation_scope",
    "invitation_id",
    "invitee_username",
}.issubset(set(REQUIRED_COLUMNS["auth_passkey_challenges"]))
self.assertIn("purpose", set(REQUIRED_COLUMNS["auth_password_reset_tokens"]))
self.assertIn("ux_auth_users_passkey_user_handle", REQUIRED_INDEXES)
self.assertIn("ck_auth_users_password_auth_state", REQUIRED_CONSTRAINTS)
```

Also add runtime migration assertions that `auth_users.password_hash` is nullable and `pg_get_constraintdef` for `ck_auth_users_password_auth_state` contains `password_hash IS NULL`.

- [ ] **Step 2: Run the failing schema test**

Run:

```bash
uv run pytest tests/test_integrity.py::MigrationGuardTests::test_passkey_schema_signature_covers_migration_surface -q
```

Expected: fails because required columns/index/constraint are absent.

- [ ] **Step 3: Add migration and signature entries**

Create migration with this ordering:

```sql
ALTER TABLE public.auth_users
    ADD COLUMN IF NOT EXISTS password_auth_disabled bigint DEFAULT 0 NOT NULL;
ALTER TABLE public.auth_users
    ADD COLUMN IF NOT EXISTS passkey_user_handle text;
ALTER TABLE public.auth_users
    ADD COLUMN IF NOT EXISTS passkey_prompt_dismissed_until_ms bigint DEFAULT 0 NOT NULL;
ALTER TABLE public.auth_password_reset_tokens
    ADD COLUMN IF NOT EXISTS purpose text DEFAULT 'password_reset' NOT NULL;
ALTER TABLE public.auth_passkey_challenges
    ADD COLUMN IF NOT EXISTS invitation_token_hash text,
    ADD COLUMN IF NOT EXISTS invitation_scope text,
    ADD COLUMN IF NOT EXISTS invitation_id bigint,
    ADD COLUMN IF NOT EXISTS invitee_username text;
UPDATE public.auth_users
SET passkey_user_handle = replace(replace(md5(random()::text || clock_timestamp()::text || id::text), '+', '-'), '/', '_')
WHERE passkey_user_handle IS NULL OR passkey_user_handle = '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_auth_users_passkey_user_handle
    ON public.auth_users (passkey_user_handle)
    WHERE passkey_user_handle IS NOT NULL;
ALTER TABLE public.auth_users
    ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE public.auth_users
    DROP CONSTRAINT IF EXISTS ck_auth_users_password_auth_state;
ALTER TABLE public.auth_users
    ADD CONSTRAINT ck_auth_users_password_auth_state CHECK (
        (password_auth_disabled = 0 AND password_hash IS NOT NULL AND length(password_hash) > 0)
        OR (password_auth_disabled = 1 AND password_hash IS NULL)
    );
```

- [ ] **Step 4: Add passwordless helpers**

Update `gardenops/security.py`:

- Add `password_auth_disabled`, `passkey_user_handle`, and `passkey_prompt_dismissed_until_ms` to user/session selects where relevant.
- Add `generate_passkey_user_handle() -> str`.
- Add optional `password: str | None` and `password_auth_disabled: bool = False` support to `create_user`.
- When `password_auth_disabled=True`, insert `password_hash=NULL`, skip password policy validation, and require that callers are invite passwordless or explicit recovery code paths.
- Make `authenticate_user_credentials` return `None` when `password_auth_disabled=1` or `password_hash` is null.
- Make every `verify_password`, `verify_password_and_upgrade`, reauth, change-password, current-password passkey registration, and existing-user invite-accept caller guard null password hashes before calling.
- Add tests proving passwordless users cannot hit password reauth/change/current-password registration/invite-accept paths without crashes or secret-revealing errors.

- [ ] **Step 5: Run schema/security primitive tests**

Run:

```bash
uv run pytest tests/test_integrity.py tests/test_security_unit.py -q
```

Expected: pass.

## Task 2: Authenticated Passkey Prompt State

**Files:**
- Modify: `gardenops/routers/auth.py`
- Modify: `gardenops/security.py`
- Modify: `frontend/src/services/authApi.ts`
- Modify: `frontend/src/services/api.ts`
- Test: `tests/test_passkeys.py`

- [ ] **Step 1: Write failing prompt state tests**

Add tests that:

- `/api/auth/me` returns `passkey_count`, `passkey_enrolled`, `passkey_prompt_eligible`, `passkey_prompt_dismissed_until_ms`, and `password_auth_disabled`.
- A password-authenticated user with zero passkeys is eligible.
- A passkey-authenticated user or user with one passkey is not eligible.
- Posting dismissal sets `passkey_prompt_dismissed_until_ms` about 30 days in the future and makes eligibility false.

- [ ] **Step 2: Run failing prompt tests**

Run the new focused tests from `tests/test_passkeys.py`.

Expected: fail because fields and endpoint are missing.

- [ ] **Step 3: Implement prompt state**

Update `/api/auth/me` to include the fields. Add:

```python
class PasskeyPromptDismissBody(StrictBaseModel):
    dismissed_days: int = Field(default=30, ge=1, le=365)
```

Add `POST /api/auth/passkeys/prompt-dismiss` requiring session auth and CSRF. It updates only the current user:

```sql
UPDATE auth_users
SET passkey_prompt_dismissed_until_ms = %s
WHERE id = %s
```

Compute eligibility on the server as:

```python
passkeys.passkeys_configured()
and context.auth_type == "session"
and not context.must_change_password
and not context.passkey_enrolled
and current_timestamp_ms() >= passkey_prompt_dismissed_until_ms
and not bool(password_auth_disabled)
```

- [ ] **Step 4: Run prompt tests**

Run:

```bash
uv run pytest tests/test_passkeys.py -q
```

Expected: pass.

## Task 3: Passwordless Recovery Purpose And Password Guards

**Files:**
- Modify: `gardenops/routers/auth.py`
- Modify: `gardenops/security.py`
- Test: `tests/test_security.py`
- Test: `tests/test_auth_lifecycle.py`

- [ ] **Step 1: Write failing recovery tests**

Add tests:

- Normal reset token cannot enable password auth for a passwordless user.
- Admin-issued passwordless recovery token can set first password and clears `password_auth_disabled`.
- Passwordless user cannot call password reauth/change.
- Passwordless user cannot use existing-user invitation password acceptance.
- Passwordless user cannot use current-password passkey registration.
- Existing password reset behavior still works for password users.

- [ ] **Step 2: Run failing recovery tests**

Run focused recovery tests.

Expected: fail until reset purpose and passwordless guards are implemented.

- [ ] **Step 3: Implement reset purpose**

Extend reset token insert metadata/purpose:

- Existing admin reset uses `purpose='password_reset'`.
- New passwordless recovery path uses `purpose='passwordless_recovery'`.

Add either a request body flag `enable_password_auth: bool = False` to `IssueResetTokenBody`, or a dedicated admin endpoint. Use the flag if it keeps API surface smaller:

```python
enable_password_auth: bool = False
```

Only allow `enable_password_auth=True` when target user has `password_auth_disabled=1`.

In reset verify:

- if target is passwordless and token purpose is not `passwordless_recovery`, reject.
- if token purpose is `passwordless_recovery`, update `password_auth_disabled=0`, set `password_hash`, clear `must_change_password`.
- if target is password user, normal purpose works as today.

- [ ] **Step 4: Run recovery tests**

Run:

```bash
uv run pytest tests/test_security.py tests/test_auth_lifecycle.py -q
```

Expected: pass.

## Task 4: Passwordless Invite Backend

**Files:**
- Modify: `gardenops/passkeys.py`
- Modify: `gardenops/routers/auth.py`
- Modify: `gardenops/main.py`
- Test: `tests/test_passkeys.py`
- Test: `tests/test_security.py`

- [ ] **Step 1: Write failing passwordless invite tests**

Add tests covering:

- Options endpoint rejects invalid, expired, revoked, and accepted invites.
- Options endpoint rejects existing active username.
- Options endpoint rejects existing inactive username.
- Options endpoint rejects personal admin invitation.
- Options and verify enforce route and token-scoped rate limits.
- Invalid, expired, revoked, accepted, existing-active-username, and existing-inactive-username cases return the same public status/detail shape.
- Verify creates a passwordless user, stores the passkey, accepts the invite, creates session cookies, and returns `created_user=true`.
- Verify rejects challenge replay.
- Verify rejects revoke-after-options.
- Verify rejects accepted-after-options.
- Two challenges from one invite: first verify succeeds, second verify fails.
- Password invite accept and passwordless passkey verify cannot both accept the same invitation from stale prechecks.
- Existing password invite acceptance still works.
- Audit, telemetry outbox/export, server logs, and client-error paths do not store raw invite tokens, challenge tokens, credential JSON, public keys, signatures, or `clientDataJSON`.

- [ ] **Step 2: Run failing passwordless invite tests**

Run the new focused tests.

Expected: fail because endpoints and challenge binding are missing.

- [ ] **Step 3: Implement invite-bound challenge support**

Extend `PasskeyFlow` with `"invitation_registration"`. Extend `create_challenge` and `consume_challenge` to accept and match:

- `invitation_token_hash`
- `invitation_scope`
- `invitation_id`
- `invitee_username`

Add registration options helper that accepts a pre-created random user handle for pending invite registration and excludes no credentials because existing usernames are rejected.

Extend `ConsumedPasskeyChallenge` to return:

- `invitation_token_hash`
- `invitation_scope`
- `invitation_id`
- `invitee_username`

Verify must use these returned fields so the raw invite token is not resent or logged during registration verification.

- [ ] **Step 4: Implement endpoints**

Add:

- `POST /api/auth/invitations/passkey/register/options`
- `POST /api/auth/invitations/passkey/register/verify`

Both are public and CSRF-exempt in `gardenops/main.py`.

Options behavior:

- enforce route rate limit `AUTH_INVITE_PASSKEY_REGISTER_RATE_LIMIT`.
- enforce token-scoped rate limit `AUTH_INVITE_PASSKEY_REGISTER_TOKEN_RATE_LIMIT`.
- validate token by hash against garden and personal invitation tables.
- return generic invalid token errors for invalid states.
- reject existing `auth_users.username` regardless of active state.
- reject `personal_garden` admin role.
- create challenge bound to invitation fields.
- return `challenge_token`, `publicKey`, `username`, `role`, `garden_id`, and `invitation_scope`.

Verify behavior:

- enforce route rate limit `AUTH_INVITE_PASSKEY_REGISTER_RATE_LIMIT`.
- rely on the options step's token-scoped rate limit and the consumed challenge's stored invitation token hash.
- consume bound challenge.
- verify WebAuthn response.
- use a shared helper to atomically accept invitations with `UPDATE ... WHERE accepted_at_ms IS NULL AND revoked_at_ms IS NULL AND expires_at_ms > %s RETURNING ...`. Refactor the existing password invite accept path to use the same helper.
- re-check no user exists for invited username.
- create user with `password_auth_disabled=True`.
- insert passkey.
- commit the invite/user/passkey transaction.
- create session cookies after commit. If session creation fails, do not roll back the now-valid passwordless user; return a generic error and allow the user to sign in with the passkey.
- write `auth.invitation.passkey.accept`.

- [ ] **Step 5: Run invite backend tests**

Run:

```bash
uv run pytest tests/test_passkeys.py tests/test_security.py -q
```

Expected: pass.

## Task 5: Frontend Auth Gate And Prompt

**Files:**
- Modify: `frontend/src/services/authApi.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/features/authGate.ts`
- Modify: `frontend/src/app.ts`
- Modify: `scripts/check_auth_gate_status_flow.cjs`
- Modify: `frontend/src/core/authI18n.ts`
- Modify: `frontend/src/core/i18n.ts`
- Test: `scripts/check_auth_gate_status_flow.cjs`

- [ ] **Step 1: Add auth API helpers**

In `authApi.ts`, add:

- prompt fields to `AuthUserProfile`.
- `beginInvitePasskeyRegistrationApi(token)`.
- `finishInvitePasskeyRegistrationApi(challengeToken, credential)`.
- auth-gate-only `beginPasskeyRegistrationApi(nickname, currentPassword)` and `finishPasskeyRegistrationApi(...)`.

In app-shell modules, use full `api.ts` or `authApi.ts` consistently without importing full `api.ts` into `authGate.ts`.

Add a shared passkey cancellation classifier in `frontend/src/features/passkeys.ts`:

```ts
export function isPasskeyCancellation(err: unknown): boolean {
  return err instanceof DOMException
    && (err.name === "AbortError" || err.name === "NotAllowedError");
}
```

- [ ] **Step 2: Add invite passkey UI**

In `authGate.ts`, add a `Create passkey` button to invite flow when passkeys are enabled and supported.

Behavior:

- Password path remains first-class.
- Unsupported passkey browsers show one short hint only if passkeys are configured.
- Click `Create passkey` shows one short warning about admin reset, then starts WebAuthn.
- Cancellation returns to invite form.
- Success clears invite token, sets active garden if returned, removes gate, and resolves.
- Auth-gate text keys live in `authI18n.ts`. The invite card must not show unsupported-browser hint, admin-reset warning, and full password checklist simultaneously.

- [ ] **Step 3: Add post-login prompt**

Implement the post-login prompt in `frontend/src/app.ts` or a small app-shell module called from authenticated startup after `refreshGardenContext`. Do not render it from `authGate.ts`.

When the app shell has an auth profile with `passkey_prompt_eligible` and browser support is true, render a compact app-shell prompt.

Behavior:

- `Add passkey` asks only for current password, uses nickname `Passkey`, starts WebAuthn, and dismisses on success/cancel.
- `Not now` calls dismissal endpoint with 30 days.
- Non-cancel failure hides prompt for the current session.
- Prompt never appears after passkey login.
- Add a session-local suppression flag and an in-flight flag so refreshes or repeated profile loads do not show duplicate prompts in the same tab.

- [ ] **Step 4: Run frontend guard/build**

Extend `scripts/check_auth_gate_status_flow.cjs` or add a focused companion script so it checks:

- `authGate.ts` does not import full `services/api` or full `core/i18n`.
- invite passkey helpers are imported from `authApi.ts`.
- invite passkey UI contains `Create passkey` and cancellation handling.
- app-shell prompt code lives outside `authGate.ts`.
- prompt suppression/in-flight state exists.
- auth-gate copy does not include long explanatory paragraphs for the passkey paths.

Run:

```bash
node scripts/check_auth_gate_status_flow.cjs
cd frontend && npm run build
```

Expected: pass.

## Task 6: Documentation And Final Verification

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/development.md`
- Modify: plan/spec if implementation findings require it.

- [ ] **Step 1: Update docs**

Document:

- passkey configuration remains `AUTH_PASSKEY_RP_ID` and `AUTH_PASSKEY_ORIGINS`.
- passwordless invite signup is invite-only and non-admin only.
- passwordless recovery is admin-issued reset with explicit enable-password-auth purpose.
- prompt dismissal policy is 30 days.

- [ ] **Step 2: Run full auth/security verification**

Run:

```bash
uv run pytest tests/test_auth_endpoints.py tests/test_passkeys.py tests/test_auth_lifecycle.py -q
uv run pytest tests/test_security.py tests/test_security_unit.py tests/test_security_mfa_unit.py -q
uv run pytest tests/test_integrity.py -q
uv run pytest tests/test_admin_edge_policy.py tests/test_authorization_negative_sweep.py -q
node scripts/check_auth_gate_status_flow.cjs
cd frontend && npm run build
```

Expected: all commands exit 0.

## Plan Hostile Review Notes

The plan hostile review blocked the first draft until these findings were addressed:

- App-shell post-login prompt must live outside `authGate.ts`; auth gate resolves before the app shell loads.
- Auth gate must use only `authApi.ts` and `authI18n.ts`; full `api.ts`/`i18n.ts` imports remain forbidden.
- Prompt fatigue needs session-local suppression and in-flight dedupe.
- Passkey cancellation needs a shared classifier for create/get flows.
- Frontend guard coverage must expand beyond the current build guard.
- Passwordless invite endpoints need route and token-scoped rate limits plus tests.
- Sensitive token/credential redaction must be testable across audit, telemetry, client errors, and logs.
- Existing password invitation acceptance needs the same atomic accept helper as passwordless invite verification.
- Passwordless DB invariant must require `password_hash IS NULL` when password auth is disabled.
- Public rejection paths need equality tests to avoid enumeration.
- Passwordless verify must commit user/passkey/invite before calling current separate-connection session creation, or session creation must be refactored onto the same connection.
- `ConsumedPasskeyChallenge` must return invitation binding fields so verify does not need the raw invite token.
- Schema signature/tests must include reset-token `purpose`, password nullability, and constraint definition checks.
- Nullable-password guards must land before any code path can create passwordless users.
- Migration handle backfill must avoid unconfigured `pgcrypto`; use repo-compatible SQL or add a deliberate extension migration.
