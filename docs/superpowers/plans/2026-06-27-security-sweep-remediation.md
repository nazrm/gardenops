# 2026-06-27 Security Sweep Remediation Plan

## Sweep Scope

Target branch: `codex/security-sweep` from `9f19177` (`main` after PR #72).

Six read-only agents reviewed auth/session/passkeys, authorization and object ownership, file/media/network fetches, frontend/browser safety, supply chain/CI/sanitizer, and AI/provider abuse controls. Local follow-up inspection checked route helpers, existing tests, deployment policy, dependency-audit workflow, frontend offline storage, map import/rendering, and relevant auth/session semantics.

## Confirmed Vulnerabilities

1. TOTP enrollment can refresh destructive-admin step-up from a stale admin session.
   - A stale but valid admin session can start and confirm first-time TOTP enrollment, then receive fresh `reauthenticated_at_ms`.
   - Impact: stolen or unattended stale admin sessions can become eligible for destructive admin controls without current password or pre-existing second factor.

2. Git push sanitizer path blocking misses dot-prefixed sensitive paths.
   - `.env`, `.gardenops/**`, and `.codex/**` are normalized with `lstrip("./")`, so path-level block rules do not match.
   - Impact: ignored secret/state files and dependency release-bypass artifacts can be committed despite policy intent.

3. Git push sanitizer example/suppression logic can suppress hard secret detectors.
   - Lines containing `placeholder`/`dummy` are skipped before hard key regexes run, and commit patch scanning does the same.
   - Impact: real OpenAI/GitHub/database/private-key patterns can be missed when example text is present.

## Hardening Findings To Address In This PR

5. PlantNet HTTP error details can leak query-string API keys into logs if upstream echoes request URLs.

6. ShadeMap remote terrain tiles can preserve/cache unexpected upstream content types when no local tile processing path decodes them.

7. AI identify fallback runs outside the configured `ai-identify` concurrency slot.

8. Task-description AI generation uses provider calls without provider budget/concurrency accounting.

9. Security telemetry minimized-mode hashing falls back to a public static salt.

10. Frontend offline IndexedDB drafts remain after auth expiry.

11. Imported/rendered plot colors are not normalized before assigning CSS background.

12. Source-map guard only blocks `*.map` files, not inline or remote `sourceMappingURL` comments in deploy JS.

13. Security-release bypass generator is not CODEOWNERS-covered.

## Not Promoted

- Public `/api/version` build metadata is intentional diagnostics and does not expose secrets. Leave unchanged unless product policy changes.
- Same-garden viewer mutation routes were initially flagged by static route review, but targeted API tests showed the global mutation guard already returns 403 before the route handler. Keep those regression tests as coverage and do not duplicate route-level gates in this PR.
- Nginx template CSP duplication is not necessary because backend emits CSP and nginx does not hide it; keep validation through existing header tests.
- LiDAR upload CPU budget is bounded to authenticated editors and already has byte/grid limits. Defer explicit point-count CPU caps unless real workload evidence appears.
- Body buffering before some route-specific limiters is bounded by global body limits and edge controls. Defer deeper ASGI middleware ordering work to avoid broad regression in this PR.
- npm install scripts before audit are a supply-chain design question because current lockfile includes packages with install scripts. Do not switch CI to `--ignore-scripts` without a separate compatibility pass.
- Task-description refresh is already writer-only. Treat the finding as provider cost/concurrency hardening, not an authorization bypass.

## Hostile Review Revisions

- Agent capacity blocked spawning additional reviewers after the six sweep agents; the plan was hostile-reviewed locally against route code and existing tests.
- The write-gate tests must explicitly cover plot-plant assignment endpoints (`add`, `quantity update`, `move`, `remove`) in addition to plot CRUD and batch move. These tests passed before route edits because the global API mutation guard already enforces write role.
- TOTP setup should remain usable immediately after a normal fresh login, because `create_session_for_user()` initializes `reauthenticated_at_ms` to `now`. A stale setup session can be required to log in again rather than using `/auth/reauthenticate`, which intentionally rejects destructive-admin reauth before MFA is enabled.
- The task-description AI item must not be framed as missing route authorization; the valid concern is that provider calls should use the existing budget/concurrency framework.
- Frontend offline cleanup has no existing unit-test harness. Validate through TypeScript build/static checks unless a small script can exercise the exported function without creating a brittle browser harness.

## Implementation Plan

1. Add backend authorization regression tests for every suspected viewer mutation route; keep them passing as proof of the global mutation guard.
2. Add failing auth test proving stale-session TOTP setup cannot produce destructive admin step-up.
3. Add failing sanitizer tests for dotfile path rules and hard secret detection despite placeholder/suppression text.
4. Add failing low-level tests for PlantNet error redaction, ShadeMap terrain MIME rejection, AI identify fallback concurrency, task-description provider budget/concurrency, telemetry salt runtime config, and source-map URL detection.
5. Implement minimal fixes:
   - Require recent session context, with MFA setup allowed, before TOTP start/confirm; do not let TOTP confirmation refresh stale `reauthenticated_at_ms`.
   - Preserve leading dot segments during sanitizer normalization; run hard secret regexes before safe-example suppression in full-file and patch scans.
   - Sanitize PlantNet error details before raising/logging.
   - Reject non-image remote terrain content before cache/response.
   - Wrap AI identify fallback and task-description AI generation in existing provider budget/concurrency controls.
   - Require a non-default telemetry privacy salt when telemetry export is enabled in production or internet-exposed mode.
   - Clear offline queue on auth expiry.
   - Restrict rendered plot colors to safe hex CSS colors.
   - Detect inline/remote `sourceMappingURL` comments in deploy JS.
   - Add CODEOWNERS coverage for the release-bypass generator.
6. Run targeted tests red/green, then broader security and build validation:
   - Backend targeted pytest files for auth, authorization, workflows, ShadeMap, AI, PlantNet, sanitizer.
   - `python scripts/check_dependency_sources.py`
   - `node scripts/check_no_sourcemaps.cjs` through frontend build script where possible.
   - Frontend `npm run build`.
   - Push sanitizer pre-add/pre-commit/pre-push before publishing.

## Validation Plan

Each vulnerability gets a regression test that fails before the fix and passes after. Final PR validation must include targeted pytest, frontend build/static checks, dependency source checks, docs-upkeep review, sanitizer pre-publish scan, and GitHub PR creation.
