# Security Scan 97c2af30 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 20 findings from the Codex Security scan `97c2af30b25d_20260627T100201Z` in one PR with focused regressions.

**Architecture:** Group findings by the broken control rather than by report entry. Runtime fixes belong at the central enforcement boundary: route feature gates, public ID validation, strong-auth checks, metric normalization, parser work limits, and object ownership. CI/governance fixes belong in policy scripts and workflows so PR-controlled data is evaluated by trusted policy code.

**Tech Stack:** FastAPI/Pydantic/Psycopg backend, TypeScript/Vite frontend, Python policy scripts, GitHub Actions, pytest/unittest, npm build checks.

---

## Planned Fix Groups

### Task 1: Central Feature-Gate Enforcement

**Findings covered:** LiDAR route gate, onboarding ShadeMap import, plot import ShadeMap import.

**Files:**
- Modify: `gardenops/feature_gates.py`
- Modify: `gardenops/routers/gardens.py`
- Modify: `gardenops/main.py`
- Test: `tests/test_feature_gates.py`
- Test: `tests/test_gardens_endpoints.py`
- Test: `tests/test_export_import.py`

- [ ] Add failing tests showing `/api/gardens/{id}/lidar` maps to `shade_map`, home-tier onboarding rejects ShadeMap payload fields, and plot import rejects ShadeMap payload fields when the active tier lacks `shade_map`.
- [ ] Add `/api/gardens/*/lidar` to the central route map.
- [ ] Add explicit server-side feature checks before onboarding or plot import restores `shademap`, `shademap_calibration`, or `shademap_obstacles`.
- [ ] Re-run targeted feature-gate, garden, and import tests.

### Task 2: Public ID Path-Safety

**Findings covered:** Plant-search add-to-plot dot-segment path injection.

**Files:**
- Create: `gardenops/public_ids.py`
- Modify: `gardenops/routers/plots.py`
- Modify: `gardenops/routers/plants.py`
- Modify: `gardenops/models.py`
- Modify: `frontend/src/services/api.ts`
- Test: `tests/test_plots.py`
- Test: `tests/test_plants.py`
- Add frontend static/security test if an existing harness can cover encoded API paths without a browser.

- [ ] Add failing backend tests that reject `plot_id` and `plt_id` containing `/`, `\`, raw dot segments, encoded dot segments, query/fragment delimiters, or control characters during create/import/rename/bulk assignment.
- [ ] Add frontend path segment encoding around `plotId` and `pltId` in assignment helpers.
- [ ] Re-run targeted plot, plant, and frontend type/build checks.

### Task 3: Auth, Metrics, Parser, and Object-Scope Runtime Controls

**Findings covered:** Bootstrap proxy-local fallback, admin health strong-auth bypass, unbounded metric keys, LAS/LAZ point-count DoS, provider concurrency process-local limit, planner companion cross-garden lookup.

**Files:**
- Modify: `gardenops/security.py`
- Modify: `gardenops/routers/health.py`
- Modify: `gardenops/main.py`
- Modify: `gardenops/security_metrics.py`
- Modify: `gardenops/services/lidar_terrain.py`
- Modify: `gardenops/rate_limit.py`
- Modify: `gardenops/services/planting_planner.py`
- Test: `tests/test_security_unit.py`
- Test: `tests/test_integrity.py`
- Test: `tests/test_security_metrics_unit.py`
- Test: `tests/test_lidar_terrain.py`
- Test: `tests/test_security.py`
- Test: `tests/test_planner.py`

- [ ] Add failing tests for each runtime control at the smallest interface that reproduces the scan evidence.
- [ ] Require internet-exposed loopback bootstrap requests to include `X-Forwarded-For` or `X-Real-IP` before trusting proxy-local client address.
- [ ] Keep admin health bearer token support, but require route-local admin session strong-auth when access falls back to session/API auth.
- [ ] Bound `record_security_event` metric key creation and route mutation metric names.
- [ ] Enforce LAS/LAZ point-count/chunk limits before terrain grid construction.
- [ ] Use shared Redis-backed leases for provider concurrency when the configured rate-limit backend is Redis; keep local leases for memory backend tests/dev.
- [ ] Scope planner candidate plant reads to the active garden.
- [ ] Re-run targeted tests.

### Task 4: Frontend DOM and URL-Sink Controls

**Findings covered:** Leaflet tooltip HTML sink, raw HTML guard bypass, env docs scanner missing Vite env reads.

**Files:**
- Modify: `frontend/src/components/shadePanel.ts`
- Modify: `scripts/check_innerhtml_sinks.py`
- Modify: `scripts/check_env_docs.py`
- Modify: `ENVIRONMENT_VARIABLES.md`
- Test: `tests/test_frontend_security_static.py`
- Test: `tests/test_dependency_policy_scripts.py` or a new focused script test file if cleaner.

- [ ] Add failing tests/fixtures showing bracket notation, compound assignment, and helper aliases are detected by the raw HTML guard.
- [ ] Change ShadeMap tooltip binding to use a text node or escaped reviewed HTML.
- [ ] Expand env-doc scanning to `frontend/src` and document `VITE_APP_NAME` / `VITE_APP_SLUG` as public build-time values.
- [ ] Re-run raw HTML guard, env docs check, and frontend build/type checks.

### Task 5: CI, Dependency Policy, Ownership, and Sanitizer Hardening

**Findings covered:** mutable PR policy scripts, Python lock entries without hashes, forged Python security-release bypass evidence, manual GitHub Actions policy gap, CODEOWNERS omissions, error summary redaction.

**Files:**
- Modify: `.github/workflows/dependency-audits.yml`
- Modify: `.github/workflows/dependency-review.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/CODEOWNERS`
- Modify: `scripts/check_dependency_sources.py`
- Modify: `scripts/check_python_release_age.py`
- Create: `scripts/check_github_action_pins.py`
- Modify: `gardenops/redaction.py`
- Test: `tests/test_dependency_policy_scripts.py`
- Test: `tests/test_summarize_errors.py`
- Docs: `docs/dependency-security-policy.md`, `docs/development.md`

- [ ] Add failing tests for no-artifact Python lock entries, forged bypass JSON, unpinned workflow `uses:` values, colon/JSON secret redaction, and raw workflow policy scripts.
- [ ] Update dependency workflows so PR dependency files are copied into a base-ref policy checkout before policy scripts run.
- [ ] Reject Python registry packages with no hashed artifacts.
- [ ] Require bypass evidence schema/source metadata and exact base `from` version matching the lock diff before release-age bypass is accepted.
- [ ] Add GitHub Actions pin enforcement to CI and document the gate.
- [ ] Expand CODEOWNERS to cover CI-executed scripts, runtime selectors, frontend security allowlists, and workflow policy files.
- [ ] Re-run dependency policy and sanitizer-focused tests.

## Hostile Review Pass

### Important Findings

1. The mutable-script workflow fix must not call a new helper script from the PR branch because that would reproduce the same trust bug. The workflow should perform base checkout and PR data copy inline or call only base-checkout code.
2. Import/onboarding ShadeMap handling must not silently drop user data without telling callers. Rejecting gated ShadeMap fields is safer than stripping because it makes entitlement failures explicit.
3. Public ID validation must cover imports and renames, not just create routes, otherwise stored unsafe IDs remain possible through snapshot or batch paths.
4. Admin health must preserve the bearer-token readiness path. Moving the route out of `public_auth_paths` would break readiness checks.
5. Provider concurrency should not require Redis in local/test memory mode, but production Redis mode should use Redis-backed active leases. Tests should not require a live Redis server.
6. Redaction must be fixed in the shared redaction helper, not only in `scripts/summarize_errors.py`.
7. The raw HTML guard does not need a full TypeScript parser for this PR, but it must detect the bypass forms from the scan and keep allowlist metadata.

### Revisions Applied

- The workflow plan uses inline base-checkout policy execution, not a new PR-controlled runner script.
- The ShadeMap import plan rejects gated payload fields for non-entitled users/admin flows instead of stripping them.
- Public ID validation is planned for model, create, rename, import, and batch entry points.
- Admin health keeps `DEPLOYED_READINESS_ADMIN_BEARER_TOKEN` public-path behavior and adds session strong-auth route-local enforcement.
- Redis-backed concurrency will be implemented behind the existing backend abstraction with memory fallback.
- Shared redaction and scanner tests are included explicitly.

## Validation Plan

- Run targeted pytest for touched backend and script areas.
- Run `uv run python scripts/check_env_docs.py`.
- Run `python scripts/check_dependency_sources.py`.
- Run `python scripts/check_github_action_pins.py`.
- Run `python scripts/check_innerhtml_sinks.py`.
- Run `npm run build` in `frontend`.
- Run docs impact inventory and update docs as needed.
- Run git push sanitizer before staging, commit, push, and PR creation.
