# Passkey Login UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make passkey login work reliably after a user enters their username while preserving a familiar password fallback and avoiding account/passkey enumeration.

**Architecture:** The backend will use the submitted username as a private hint to scope WebAuthn `allowCredentials` when the username resolves to an active passkey user, and it will bind that challenge to the same user so another account's credential cannot satisfy it. The frontend will keep one login form, annotate the username field for passkey autofill, start an abortable conditional WebAuthn request when supported, and keep the explicit passkey button plus password fallback.

**Tech Stack:** FastAPI, PostgreSQL, `webauthn`, Python `unittest`, TypeScript, Vite, browser WebAuthn conditional mediation.

---

## Hostile Review

- Username-scoped options can leak account or passkey existence if response status, error text, timing, or shape differs. The route must return a normal passkey options response for unknown, inactive, or no-passkey users, and the UI must not show "no passkey for this user".
- A challenge scoped to user A must not be satisfiable by user B's passkey. Store the optional `user_id` on `auth_passkey_challenges` and require credential lookup to match it during verification.
- Username-less passkey login must keep working for discoverable credentials. Only bind challenges when a non-empty username resolves to an active user with at least one passkey.
- Reuse the existing `_load_login_candidate` normalization and active-state behavior. Do not introduce a second username interpretation path.
- Keep passkey login as MFA-authenticated for admin/session policy. Do not weaken `mfa_authenticated=True` or session creation.
- Conditional WebAuthn leaves a pending browser request. Explicit passkey click and password form submit must abort any pending conditional request to avoid double-submit or stale credential completion.
- Conditional UI is not universal. The explicit button and password flow remain available.
- Registration already asks for resident keys, but username-scoped `allowCredentials` is still useful for platform quirks and non-ideal authenticator behavior.
- Keep existing passkey login rate limiting. Do not add a clean username probe endpoint.

## Revised Plan

### Task 1: Backend Tests

**Files:**
- Modify: `tests/test_passkeys.py`

- [ ] Add a failing test showing `/api/auth/passkeys/login/options` includes `allowCredentials` for the submitted active username with passkeys.
- [ ] Add a failing test showing unknown usernames, inactive users, and users without passkeys still receive generic options without a differentiated error.
- [ ] Add a failing test showing a user-bound challenge rejects a credential owned by a different user.
- [ ] Run: `env UV_CACHE_DIR=/tmp/uv-cache uv run --no-sync pytest tests/test_passkeys.py -q`
- [ ] Expected before implementation: at least the new username-scoped and wrong-user tests fail.

### Task 2: Frontend Static Regression Check

**Files:**
- Modify: `scripts/check_auth_gate_status_flow.cjs`

- [ ] Add static checks that login username autocomplete is `username webauthn` when passkeys are enabled.
- [ ] Add static checks that conditional passkey login uses `mediation: "conditional"` and an abort signal.
- [ ] Add static checks that explicit passkey login and password submit abort pending conditional login before continuing.
- [ ] Run: `npm run check:auth-gate-status` from `frontend/`.
- [ ] Expected before implementation: new frontend checks fail.

### Task 3: Backend Implementation

**Files:**
- Modify: `gardenops/routers/auth.py`

- [ ] Update `auth_passkey_login_options` to accept `body: PasskeyLoginOptionsBody`.
- [ ] Resolve non-empty `body.username` with `_load_login_candidate`.
- [ ] If the resolved user is active and has passkeys, set `user_id` on the challenge and pass that user id to `passkeys.authentication_options`.
- [ ] Otherwise create an unbound challenge and generic options.
- [ ] Update `auth_passkey_login_verify` to pass `challenge.user_id` into `_verify_and_update_passkey_authentication`.
- [ ] Run: `env UV_CACHE_DIR=/tmp/uv-cache uv run --no-sync pytest tests/test_passkeys.py -q`.

### Task 4: Frontend Implementation

**Files:**
- Modify: `frontend/src/features/passkeys.ts`
- Modify: `frontend/src/features/authGate.ts`

- [ ] Add conditional mediation feature detection.
- [ ] Allow `getPasskey` to accept optional mediation and abort signal options.
- [ ] In the login form, set username autocomplete to `username webauthn` when passkeys are available.
- [ ] Start conditional login after the gate renders when supported.
- [ ] Abort pending conditional login before explicit passkey click and password submit.
- [ ] Keep password fallback visible and working.
- [ ] Run: `npm run check:auth-gate-status`, `npm run typecheck -- --pretty false`, and `npm run build`.

### Task 5: Final Verification And Release

**Files:**
- Review all changed files.

- [ ] Run: `git diff --check`.
- [ ] Run focused backend passkey tests.
- [ ] Run frontend auth-gate check.
- [ ] Run frontend typecheck and build.
- [ ] Commit the implementation.
- [ ] Push `main`.
- [ ] Verify public `/api/version`, `/api/auth/status`, and frontend bundle after deployment.

## Choices Locked By This Plan

- We do not introduce a separate "does this username have a passkey" endpoint.
- We keep password fallback available.
- We support both explicit passkey button and conditional autofill.
- We bind username-scoped challenges to a user id and verify against the same user.
- We preserve username-less discoverable passkey login.
