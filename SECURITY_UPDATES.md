# Dependency updates and deployment

GardenOps uses the host's local release gate. GitHub Actions is disabled, and
the old `gardenops-security-deploy.timer` is disabled because it depends on
GitHub CI. Do not re-enable that timer. Renovate or Dependabot PRs, if present,
are separate review inputs; the host's automatic worker only publishes its own
`maintenance/local-dependencies` branch.

At 06:00 Europe/Oslo, `host-app-local-updates.timer` runs the fixed GardenOps
worker after Lossless. It starts from the exact current live tree, proposes
compatible Python/npm dependency manifest and lock changes, and rejects other
source or migration changes. The builder runs trusted dependency policy,
release-age and advisory checks, the full disposable PostgreSQL suite,
frontend checks, all ten browser journey phases, and package audits as the
isolated `app-build` account. The root-owned publisher verifies every sealed
artifact, takes a host backup, atomically activates the tested release, and
checks active bytes and public health. Only then does the worker push its exact
commit and open/update a pull request.

Both `main` protections require a pull request and `Local Release Gate` status.
The independent gate checks the sealed receipt, live release, public health,
PR head/base, and its own newly posted status before merging. A changed branch,
failed check, stale evidence, or uncertain activation stops the worker and
raises a maintenance alert. An interrupted run resumes its root-only pending
record rather than publishing a second release. An expired receipt for an
already deployed release requires supervised inspection.

Application code, policy, release machinery, and new database migrations are
outside the unattended dependency path. Release those from a reviewed source
change with the appropriate supervised build, migration, integrity, backup,
and rollback checks. The owner authorized deployment before the maintenance PR
for this specific update workflow; ordinary source releases still use `main`.
PR #253 is a one-time supervised catch-up: its reviewed commit must directly
descend from live `594c92446067ea7ddaf3522e11e636c2dfcc2967`, pass full
sealed checks without migration changes, and receive local status before merge.

## Supervised PR #254

On 17 November 2026 the owner authorized a one-time exception to merge and
deploy the developer-feedback changes in PR #254. This exception is limited
to its reviewed branch, exact candidate/base/live revisions and changed paths;
it does not broaden unattended maintenance or remove required checks. The
normal gate requires live sealed-artifact verification before merging, so this
one supervised release may activate before merge through the existing atomic
publisher with backup and rollback, then verify the protected exact-head merge.
Temporary host-side exception copies are disabled after that merge; canonical
gate scripts and branch protections remain unchanged.

The PR also updates only `opentelemetry-api` from 1.45.1 to 1.45.2 to remediate
CVE-2026-126360 / GHSA-mf5v-w3w3-f6cg. The patched release satisfies the normal
release-age window. Trusted base/head advisory evidence and the complete strict
audit are still required: no vulnerability-audit waiver is authorized here.

## Checking an alert

Inspect `systemctl status host-app-local-updates.service` and the root-only
`/var/lib/host-maintenance/local-gardenops.json` report. Use local journal
entries for the failing phase; do not post raw logs, credentials, or database
contents in GitHub issues. Confirm the current release SHA and public health
before retrying. Do not clear a pending receipt or force a merge merely to make
the timer green. The host maintenance source and release contract live in the
private `nazrm/lossless.science` repository under `ops/host-maintenance/`.
