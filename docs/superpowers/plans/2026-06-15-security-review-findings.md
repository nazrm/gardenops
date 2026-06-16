# Security Review Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate the validated security review findings from the passkey/auth review and publish the changes as a draft PR.

**Architecture:** Keep fixes at existing policy boundaries: passkey option generation, route-level admin controls, object ownership helpers, shared-plant mutation guards, startup config validation, and response cache headers. Add focused regression tests beside the affected route tests.

**Tech Stack:** FastAPI, psycopg/Postgres tests, TypeScript frontend auth gate, uv/pytest, npm static auth-flow check.

---

### Task 1: Passkey Login Enumeration

**Files:**
- Modify: `gardenops/passkeys.py`
- Modify: `gardenops/routers/auth.py`
- Modify: `frontend/src/features/authGate.ts`
- Modify: `scripts/check_auth_gate_status_flow.cjs`
- Test: `tests/test_passkeys.py`

- [x] Write tests asserting public passkey login options never include `allowCredentials` for enrolled, missing, inactive, or password-only users.
- [x] Run `tests/test_passkeys.py::TestPasskeyLogin` and verify the new/updated tests fail before implementation.
- [x] Generate discoverable-credential login options by omitting username-scoped descriptors on public login.
- [x] Keep username-bound server challenges so verification still rejects wrong-user credentials.
- [x] Normalize public passkey verify failures so malformed credentials cannot reveal passkey enrollment.
- [x] Update the frontend to auto-start passkey after username resolution without relying on credential descriptors, then fall back to password on any passkey error.
- [x] Update the static auth-flow check to enforce descriptor-independent passkey auto-start.

### Task 2: Destructive Admin Controls

**Files:**
- Modify: `gardenops/routers/gardens.py`
- Modify: `gardenops/routers/media.py`
- Test: `tests/test_security.py`
- Test: `tests/test_media.py`

- [x] Write tests proving garden invitation create/revoke and media cover populate reject stale or non-step-up admin sessions.
- [x] Run the focused tests and verify they fail before implementation.
- [x] Replace role-only checks on destructive mutations with `enforce_destructive_admin_controls`, passing body/header action reasons where available.
- [x] Preserve existing platform-admin read/list behavior where the route is not destructive.
- [x] Wire frontend admin actions through existing step-up/action-reason prompts for affected workflows.

### Task 3: Object Ownership Parity

**Files:**
- Modify: `gardenops/routers/plants.py`
- Modify: `gardenops/routers/plots.py`
- Test: `tests/test_plants.py`
- Test: `tests/test_plots.py`

- [x] Write tests proving same-garden editors cannot batch-update another owner’s plants or batch-move another owner’s plots when single-object mutation would deny them.
- [x] Run the focused tests and verify they fail before implementation.
- [x] Reuse `_require_plant_access` and `_require_plot_access` in batch mutation paths so batch and single-object authorization match.
- [x] Keep admin and owner batch workflows working.

### Task 4: Shared Global Plant Side Effects

**Files:**
- Modify: `gardenops/routers/ai.py`
- Modify: `gardenops/routers/journal.py`
- Modify: `gardenops/services/observation_updates.py`
- Test: `tests/test_ai.py`
- Test: `tests/test_journal.py`

- [x] Write tests proving AI care generation and bloom observation do not mutate global `plants` fields for plants shared with another garden.
- [x] Run the focused tests and verify they fail before implementation.
- [x] Exclude shared plants from AI care global updates.
- [x] Skip global `seen_growing` updates for shared plants while preserving current-garden plot assignment side effects.

### Task 5: Secret and Cache Hardening

**Files:**
- Modify: `gardenops/main.py`
- Modify: `gardenops/routers/shademap.py`
- Test: `tests/test_security.py`
- Test: `tests/test_shademap.py`

- [x] Write tests proving production/internet-exposed startup requires `AUTH_MFA_SECRET_KEY`.
- [x] Write tests proving tokenized ShadeMap terrain responses are not publicly cacheable.
- [x] Run the focused tests and verify they fail before implementation.
- [x] Add startup validation for `AUTH_MFA_SECRET_KEY` in strict deployment modes.
- [x] Require at least 32 characters for `AUTH_MFA_SECRET_KEY` in strict deployment modes.
- [x] Change tokenized terrain cache headers to `private` and cap client cache lifetime to the remaining token lifetime.

### Task 6: Shared Saved Views

**Files:**
- Modify: `gardenops/routers/saved_views.py`
- Test: `tests/test_saved_views.py`

- [x] Write tests proving `user_id IS NULL` shared saved views remain readable but cannot be updated or deleted by ordinary garden writers.
- [x] Run the focused test and verify it fails before implementation.
- [x] Reject update/delete of shared saved views unless a later explicit product policy introduces admin-managed shared presets.
- [x] Reject API-key saved-view writes because API-key contexts have no user principal to own per-user views.

### Task 7: Review, Verify, Publish

**Files:**
- All touched files

- [x] Run focused backend tests for changed files with a dedicated `GARDENOPS_TEST_POSTGRES_URL`.
- [x] Run `npm run check:auth-gate-status` in `frontend/`.
- [x] Run formatting/type/static checks available for touched code.
- [x] Review the full diff hostilely for bypasses, policy inconsistencies, and test blind spots.
- [ ] Fix any review findings, rerun relevant checks, commit, push, and open a draft PR.
