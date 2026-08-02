# Dependency Security Policy

GardenOps treats Python packages, npm packages, GitHub Actions, and audit tools
as supply-chain dependencies. Pull requests have one mandatory `Dependency
Policy` check. Backend and frontend CI wait for that check before installing the
pull request's dependency graph.

## Release-Age Tiers

Release age is evaluated only for package versions or Action refs added by the
pull request. Existing locked versions are not re-gated on every change.

| Change | Minimum age |
| --- | ---: |
| `anthropic` or `openai` direct Python SDK update | 1 day |
| Routine patch, minor, or transitive package update | 3 days |
| GitHub Action commit | 7 days |
| New direct dependency | 14 days |
| Direct dependency major update | 14 days |

Age is measured from the package artifact publish time or Action commit time,
not PR creation or lockfile edit time. The AI SDK tier applies only to the exact
direct package names; their transitive dependencies and similarly named
packages use the normal tiers.

Dependabot mirrors these windows where its configuration supports them. The AI
SDKs are excluded from Dependabot's global pip cooldown so Dependabot can open
the PR promptly; the `Dependency Policy` check still enforces their one-day
minimum.

## Pull Request Gate

The gate runs before Backend and Frontend jobs and performs these checks without
installing packages from the pull request:

1. Validate Python and npm lock sources, hashes, and integrity metadata.
2. Require every GitHub Action to use an approved identity pinned to a full
   40-character commit SHA.
3. Apply the seven-day age check to Action refs added by the pull request.
4. Compare base and head vulnerability results.
5. Apply the appropriate release-age tier only to changed locked versions.

Policy scripts execute from a detached base-branch checkout. PR manifests,
lockfiles, and workflow files are copied into that checkout as untrusted data.
Do not run a policy helper from the PR branch or install the PR dependency graph
inside the policy job.

Approved Action identities are intentionally narrow and maintained in
`scripts/check_github_action_pins.py`. Adding an Action requires reviewing its
publisher, purpose, permissions, and pinned commit before adding its identity to
the allowlist.

## Advisory Delta

The PR gate compares base and head audits instead of failing on vulnerabilities
that already exist on the base branch:

- Python fails when the head graph introduces a new advisory.
- npm fails when the head graph introduces a high or critical advisory, or an
  existing advisory worsens to high or critical.
- Existing unchanged findings remain visible to the scheduled full audit but do
  not block unrelated dependency PRs.

The weekly `Dependency Audits` workflow scans the complete current graph, checks
npm registry signatures, and publishes Python and frontend CycloneDX SBOMs. It
is monitoring for baseline risk, not a duplicate pull-request release gate.

## Verified Security Remediation

A package update may bypass its release-age window only when generated base/head
audit evidence proves that the exact version change removes an advisory from
the locked graph. PR titles, labels, bot identity, and reviewer assertions are
not bypass evidence.

Generated evidence must name the package, exact old and new versions, fixed
advisories, and trusted source (`pip-audit base/head diff` or `npm audit
base/head diff`). The normal lock, source, Action, test, and build checks still
apply.

Before merging a security remediation:

1. Confirm the advisory applies to the locked version.
2. Prefer a resolver-compatible dependency update over a manual transitive
   override.
3. Review release notes and maintainer/source identity.
4. Run the relevant focused tests and the complete required CI.
5. Record the advisory and evidence in the PR description.

## Dependency Intake

Every new direct dependency should answer:

- What job does it do, and why is a local or existing implementation unsuitable?
- Who publishes and maintains it, and is its license acceptable?
- Does it add native code, lifecycle scripts, downloads, or network behavior?
- How large is its transitive graph and where does it execute?
- Does it process secrets, private media, location data, or user content?
- Are there advisories, ownership changes, deprecations, or abandonment signals?

`pyproject.toml` and `frontend/package.json` declare direct dependencies.
`uv.lock` and `frontend/package-lock.json` are the authoritative installation
graphs. CI and deployment install with frozen/clean lockfile semantics. Audit
tools must also be declared and locked rather than installed ad hoc.

## Ownership And Recovery

Dependency manifests, lockfiles, Dependabot configuration, workflow files,
policy scripts, and this document require owner review through `CODEOWNERS`.
The protected `main` branch requires `Dependency Policy`, `Backend`, and
`Frontend` status checks.

If an accepted update breaks the app or becomes suspicious, revert it and rerun
the relevant audit. If the revert restores a known advisory, record the accepted
risk and mitigation while preparing a safer fix.
