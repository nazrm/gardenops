# Dependency Security Policy

GardenOps treats Python packages, npm packages, GitHub Actions, and audit tools
as supply-chain dependencies. Routine dependency updates should wait until new
versions have had time to be observed by the ecosystem, while known security
fixes should move through a separate emergency path.

## Cooling-Off Rule

- Routine dependency updates must wait at least 14 days after the package
  artifact was published before they are accepted.
- Major-version updates should wait at least 30 days unless they are part of an
  approved security remediation.
- Release age is measured from package artifact publish time, not from PR
  creation time, merge time, or lockfile edit time.
- The rule applies to direct dependencies, transitive additions, and GitHub
  Action updates.
- Dependabot enforces the routine cooldown window for pip, npm, and GitHub
  Actions updates.

## Emergency Security Updates

Security updates may bypass the routine cooldown when a known advisory affects
the locked dependency graph.

Before merging a cooldown bypass:

1. Confirm the advisory applies to the locked package version.
2. Prefer a resolver-compatible direct dependency update over a manual
   transitive override.
3. Run the relevant dependency audit after the update.
4. Run focused tests for the affected surface, plus broader CI when practical.
5. Review release notes, changelog, and maintainer/source identity for the new
   version.
6. Document the bypass reason in the PR description.

If the only available fix requires an unusual transitive override, document why
the direct dependency cannot be updated first and add focused regression tests
for the affected behavior.

## Manifest And Lockfile Policy

- `pyproject.toml` and `frontend/package.json` declare direct dependencies.
- `uv.lock` and `frontend/package-lock.json` are the authoritative resolved
  dependency graphs for installation and review.
- Development, CI, deployment, and agent workflows must install from lockfiles:
  `uv sync --frozen` for Python and `npm ci` for frontend dependencies.
- PRs that refresh lockfiles without the cooldown guard or without explaining a
  security bypass should be rejected even if audits are green.
- Lockfile changes must be reviewed as code because they can add new transitive
  packages, sources, native extensions, or install scripts.

## New Dependency Intake Checklist

Every new direct dependency should answer:

- What job does this package do, and why is a smaller/local implementation not
  appropriate?
- Who publishes and maintains it?
- Is the license acceptable?
- Does it introduce native code, install scripts, postinstall downloads, or
  network behavior?
- How many transitive dependencies does it add?
- Does it run in production, build/test/lint tooling, or CI only?
- Does it have recent suspicious ownership, release, or package-name changes?
- Are there known advisories, deprecated packages, or abandoned maintainers?
- Can the data flow through the dependency include secrets, private media,
  garden location data, or user-controlled content?

## Review And Ownership

Dependency manifests, lockfiles, Dependabot config, GitHub Actions workflows,
and this policy require owner review through `CODEOWNERS`.

The dependency review workflow must fail PRs that introduce high or critical
known vulnerabilities in runtime or development dependency scopes.

## Rollback And Exceptions

If a dependency update passes audit but breaks the app or introduces suspicious
behavior:

1. Revert the dependency PR or lockfile change.
2. Re-run the relevant audit to confirm the revert state.
3. If the revert reintroduces a known advisory, document the accepted risk and
   mitigation while a safer fix is prepared.

Policy exceptions belong in the PR description and should include the affected
package, version, reason for bypass, validation performed, and follow-up owner.
