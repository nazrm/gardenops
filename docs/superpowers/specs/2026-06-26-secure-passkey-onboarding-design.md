# Secure Passkey Onboarding Design

## Goal

GardenOps should make passkeys easier to adopt without weakening account creation or account recovery. Existing users should be offered a restrained post-login path to add a passkey. Invited users should be able to create an account with only a username and passkey, without creating a password.

## Non-Goals

- Open public self-signup is not part of this change.
- Silent or automatic passkey creation is not part of this change.
- Email or SMS recovery is not introduced by this change.
- Existing password-based invitation acceptance remains supported.

## Current State

- Existing users can manually add passkeys from the settings passkey card.
- The normal password login flow closes the auth gate after success and does not offer passkey enrollment.
- Passkey registration already uses configured RP ID and origins, server-side challenges, resident keys, required user verification, duplicate credential exclusion, and server-side verification.
- Invitation acceptance currently requires a password and creates a password-authenticated user when the invitee account does not already exist.
- `auth_users.password_hash` is currently required, and user creation requires a password.

## Design

### Existing User Passkey Offer

After a successful password login, the frontend fetches `/api/auth/me` and decides whether to show a compact passkey prompt.

The prompt is shown only when all of these are true:

- Auth and session auth are enabled.
- Passkeys are configured for the deployment.
- The browser supports WebAuthn create/get.
- The user is fully authenticated.
- The user is not in a forced password-change flow.
- The user has no registered passkeys.
- The prompt has not been dismissed within the configured cooldown.

The prompt has two actions:

- `Add passkey`: starts the existing passkey registration flow.
- `Not now`: dismisses the prompt for a long cooldown.

The prompt is non-blocking and appears after the auth gate is removed. It must not prevent the app from loading.

### Existing User Enrollment Security

Passkey enrollment remains server-authorized. The frontend offer is only a convenience.

Server requirements:

- A recent authenticated session is required.
- The user must re-enter the current password.
- If the user is an admin and admin MFA is required, a strong session is required before adding another passkey once the user already has MFA or a passkey.
- Registration options must include a server-generated challenge and `excludeCredentials`.
- Verification must require expected RP ID, expected origin, expected challenge, and user verification.
- Credential IDs remain globally unique.
- Audit events are written for prompt dismissal, registration success, and registration failure where failure logging does not include credential material.

### Passwordless Invite Registration

Passwordless registration is invite-gated. An invitee can create an account with their invited username and a passkey, without setting a password.

The flow:

1. User opens an existing invitation link.
2. Frontend peeks the invitation token and pre-fills the invited username.
3. Frontend shows the invitation scope and role in compact copy when the backend can safely derive it from the token.
4. If passkeys are configured and browser-supported, the user can choose `Create passkey`.
5. If passkeys are configured but unsupported in the current browser, the invite card shows one short hint that passkey setup needs a supported browser/device.
6. If the invite username already belongs to any active or inactive `auth_users` row, passwordless registration is generically rejected. Existing users must accept invitations with their current password or use an explicit admin recovery flow.
7. Passwordless invite registration for platform-admin personal invitations is rejected in this version. Admin invitees must use the password path and then complete the existing MFA/passkey setup requirements.
8. Frontend requests passkey registration options for the invite token.
9. Backend validates the invite token, checks it is not expired, revoked, or accepted, and creates a short-lived challenge bound to the invite token hash, invitation scope, invitation ID, invited username, and a random WebAuthn user handle for this registration attempt.
10. Frontend calls `navigator.credentials.create()`.
11. Frontend sends the credential and challenge token to the backend.
12. Backend consumes the challenge once, verifies the WebAuthn response, revalidates and atomically accepts the invitation with an `UPDATE ... WHERE accepted_at_ms IS NULL AND revoked_at_ms IS NULL AND expires_at_ms > now RETURNING ...`, creates the user, stores the passkey, creates a session, and returns the normal authenticated response.

The user account is not created until the WebAuthn response verifies successfully.
Challenge consumption is committed before later soft invitation rejections so a consumed challenge cannot be replayed. The invite/user/passkey transaction is committed before session creation unless session creation is refactored to use the same database connection. If session creation fails after commit, the account remains valid and the user can sign in with the new passkey.

### Passwordless User State

GardenOps adds explicit passwordless state instead of using a hidden fake password.

Schema changes:

- Add `auth_users.password_auth_disabled bigint NOT NULL DEFAULT 0`.
- Add `auth_users.passkey_user_handle text`.
- Add `auth_users.passkey_prompt_dismissed_until_ms bigint NOT NULL DEFAULT 0`.
- Allow `auth_users.password_hash` to be null only when `password_auth_disabled = 1`.
- Add a check constraint requiring either enabled password auth with a non-empty password hash, or disabled password auth with `password_hash IS NULL`.
- Backfill `passkey_user_handle` for all existing users with a random base64url value.
- Make `passkey_user_handle` unique when present.

Migration order:

1. Add new nullable/defaulted columns.
2. Backfill `passkey_user_handle` for every existing user.
3. Add the unique index for handles.
4. Drop the `NOT NULL` constraint from `password_hash`.
5. Add the password-auth consistency check constraint after the table is in a valid state.

Password authentication behavior:

- Existing password users continue to authenticate normally.
- Users with `password_auth_disabled = 1` cannot authenticate with `/api/auth/login`.
- Password reauthentication and password change endpoints reject passwordless users with a generic password-auth unavailable error.
- Password reset tokens get an explicit purpose. Only a reset token with `purpose = 'passwordless_recovery'` may clear `password_auth_disabled` and set a first password for a passwordless user. Passwordless recovery revokes existing passkeys before enabling password authentication.
- Normal password reset tokens for password users continue to set `password_hash`, clear `must_change_password`, and keep `password_auth_disabled = 0`.
- Invitation password acceptance for an existing passwordless user must not silently enable password auth.

### WebAuthn User Handle

New passkey registrations use `auth_users.passkey_user_handle` for `PublicKeyCredentialUserEntity.id`.

Existing passkeys that were registered with numeric user IDs remain valid because login verification is credential-ID based and challenge-bound. No forced passkey migration is required.

### Abuse and Enumeration Controls

Public invitation/passkey endpoints must avoid leaking account or passkey existence.

Abuse and challenge schema changes:

- Extend `auth_passkey_challenges.flow` with `invitation_registration`.
- Add nullable `auth_passkey_challenges.invitation_token_hash text`.
- Add nullable `auth_passkey_challenges.invitation_scope text`.
- Add nullable `auth_passkey_challenges.invitation_id bigint`.
- Add nullable `auth_passkey_challenges.invitee_username text`.
- Add nullable `auth_passkey_challenges.invitation_user_handle text` for the random WebAuthn handle minted with each invitation registration challenge.
- Challenge consume for invitation registration must match all invite-bound fields, not only the opaque challenge token.
- Multiple challenges may be minted for one invitation, but only the first valid verify that atomically accepts the invitation can create the account and session.
- Existing password invitation acceptance also uses the same atomic accept helper so old and new flows share replay/race protection.

Requirements:

- Use the existing invitation accept rate limits and add token-, invitee-, and challenge-scoped limits for passwordless options and verify.
- Add tests for two challenges from one invite, revoke-after-options, and accepted-after-options.
- Failed passwordless verify returns generic invalid invitation/passkey messages.
- Challenge tokens are single-use and expire.
- Invalid or expired invitation attempts increment the existing invalid invitation metric.
- Passkey failures increment existing auth/passkey failure metrics.
- Audit logs, security telemetry outbox/export payloads, client error reporting, and server logs must not include raw invite tokens, challenge tokens, credentials, public keys, signatures, or clientDataJSON.

### Recovery Model

Passwordless account recovery is admin-mediated in this version.

- Admins can revoke or reissue invitations as they can today.
- Admins can enable password recovery for a passwordless user only by issuing a password reset token with `purpose = 'passwordless_recovery'`.
- Passwordless recovery reset tokens clear `password_auth_disabled`, set a password hash, clear `must_change_password`, revoke other sessions, and write an audit event.
- Admin passwordless account creation is blocked in this version. Admins must have password auth plus the existing MFA/passkey setup path.

### Frontend Experience

The auth gate remains low text and consistent with the current design.

Invitation flow:

- Existing password acceptance remains available.
- Passkey-capable users see a compact `Create passkey` option.
- Unsupported browsers see only the password path.
- Passkey-only choice includes one short warning: losing this passkey requires an admin reset.
- Passkey cancellation returns to the invite card and leaves both `Create passkey` and password acceptance available.

Post-login prompt:

- Appears inside the app shell after login.
- Uses short copy and two actions: `Add passkey` and `Not now`.
- `Add passkey` opens a compact enrollment flow that reuses current-password verification and the browser passkey prompt. Nickname defaults to `Passkey` and is editable later in settings, so the prompt does not ask for a nickname.
- `Not now`, user cancellation, and successful browser cancellation dismiss the prompt for 30 days.
- Registration failure that is not a user/browser cancellation dismisses the prompt for the current session only.
- Does not show for passkey sign-in sessions or users that already have a passkey.

`/api/auth/me` adds prompt-driving fields:

- `passkey_count: number`
- `passkey_enrolled: boolean`
- `passkey_prompt_eligible: boolean`
- `passkey_prompt_dismissed_until_ms: number`
- `password_auth_disabled: boolean`

The frontend uses these fields plus local browser support checks. It does not infer prompt eligibility from hidden UI state.

## Files Expected To Change

- `migrations/0016_passwordless_passkey_onboarding.sql`
- `gardenops/schema_signature.py`
- `gardenops/security.py`
- `gardenops/passkeys.py`
- `gardenops/routers/auth.py`
- `gardenops/main.py`
- `frontend/src/features/authGate.ts`
- `frontend/src/features/passkeys.ts`
- `frontend/src/core/authI18n.ts`
- `frontend/src/services/authApi.ts`
- `frontend/src/services/api.ts`
- `frontend/src/core/i18n.ts`
- `tests/test_passkeys.py`
- `tests/test_security.py`
- `tests/test_auth_lifecycle.py`
- `tests/test_integrity.py`
- `docs/configuration.md`
- `docs/development.md`

## Test Plan

Backend tests:

- Existing user login response and `/api/auth/me` expose passkey eligibility without leaking credential data.
- Passwordless users cannot authenticate through password login.
- Passwordless invite options reject missing, expired, revoked, and accepted invites.
- Passwordless invite verify consumes challenges once.
- Passwordless invite verify creates user, stores passkey, accepts invite, creates session, and writes audit data.
- Passwordless invite options/verify reject existing active and inactive usernames without attaching passkeys.
- Passwordless invite options/verify reject admin personal invitations.
- Passwordless invite verify rejects revoked-after-options, accepted-after-options, and second-challenge-after-first-acceptance attempts.
- Cross-token, cross-username, and replay attempts fail.
- Existing password invitation acceptance still works.
- Existing passkey login still works for numeric-handle legacy registrations.
- New passkey registration uses random `passkey_user_handle`.
- Passwordless recovery reset tokens can enable password auth; normal reset tokens cannot enable password auth for passwordless users.
- Admin MFA/passkey step-up requirements still protect passkey registration.

Frontend tests or smoke checks:

- Password field behavior remains unchanged on the normal login path.
- Invite flow can render password and passkey options without excessive text.
- Post-login passkey prompt appears only when eligible and can be dismissed.
- Unsupported browsers do not show unusable passkey actions.

Verification commands:

```bash
uv run pytest tests/test_auth_endpoints.py tests/test_passkeys.py tests/test_auth_lifecycle.py -q
uv run pytest tests/test_security.py tests/test_security_unit.py tests/test_security_mfa_unit.py -q
uv run pytest tests/test_integrity.py -q
uv run pytest tests/test_admin_edge_policy.py tests/test_authorization_negative_sweep.py -q
node scripts/check_auth_gate_status_flow.cjs
cd frontend && npm run build
```

## Hostile Review Notes

The first hostile review blocked the initial spec until the following findings were addressed:

- Passwordless invite registration must reject existing active or inactive usernames to avoid attaching a passkey to an existing account without that user's password.
- Invitation acceptance must be atomically revalidated during passwordless verify because one invitation can mint multiple challenges before acceptance.
- Admin passwordless invites are blocked in this version because admin passkey-only recovery needs stronger operational guarantees.
- Passwordless recovery uses a first-class reset-token purpose instead of reusing generic password reset tokens.
- The frontend must handle passkey-only cancellation and unsupported browsers without dead-ending users in a password field they never created.
- `/api/auth/me` must expose explicit prompt eligibility fields.
- `gardenops/main.py`, `frontend/src/core/authI18n.ts`, and `tests/test_integrity.py` are required implementation surfaces.
- Migration ordering is explicit so nullable password hashes cannot create an invalid intermediate state.
