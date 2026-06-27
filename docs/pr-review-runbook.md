# Pull Request Review Runbook

Use this runbook to review GitHub pull requests without disturbing a live
GardenOps checkout or accidentally using production secrets.

## Principles

- Keep the live service checkout stable. Review PRs in a separate worktree.
- Do not source production env files such as `/etc/gardenops.env`.
- Use only disposable local test databases for tests and migrations.
- Treat PR code as untrusted until reviewed. Do not run new scripts from a PR
  before reading them.
- Merge dependency-only PRs before feature PRs when they unblock audits or CI.
- Do not merge until CI is green and local validation matches the change risk.

## Risk Tiers

Classify the PR before running code:

| Tier | Examples | Required handling |
|---|---|---|
| Low | docs, comments, tests that do not change helpers | Diff review, targeted validation, CI green |
| Medium | normal backend/frontend feature work | Full local validation, CI green, test coverage review |
| High | auth, permissions, database migrations, env/config, deployment, backups, restores, nginx, systemd, GitHub Actions, dependency install behavior | Hostile review, full validation, explicit rollback/deploy notes |
| Untrusted | fork PRs, first-time contributors, workflow changes, scripts that run during install/test/build | Static review first, no secrets, no write credentials, no production env, run only in disposable worktree |

Escalate to the highest tier that applies. A one-line docs change inside
`.github/`, `deploy/`, `migrations/`, auth code, backup code, or env handling is
not low risk.

## Intake

List open PRs and their checks:

```bash
gh pr list --limit 20 --json number,title,author,headRefName,baseRefName,isDraft,mergeable,reviewDecision,statusCheckRollup
```

Inspect one PR:

```bash
gh pr view <number> --json title,body,author,baseRefName,headRefName,isDraft,mergeable,reviewDecision,statusCheckRollup,files
gh pr diff <number>
gh pr checks <number>
```

Record the current result:

```text
PR:
Author:
Risk tier:
Changed areas:
CI state:
Local validation planned:
Merge blocker:
```

## Static Review Gate

Before running any code from the PR, scan the diff for:

- workflow changes under `.github/`
- dependency install hooks or lockfile surprises
- shell scripts, Python scripts, or Node scripts that will run in validation
- database migrations
- env/config changes
- generated assets, logs, backups, media, or local runtime files

Use GitHub-hosted diff commands for the first pass:

```bash
gh pr diff <number> --name-only
gh pr diff <number> --patch
```

Do not run `npm install`, `npm ci`, `uv sync`, tests, scripts, or builds until
this static pass is complete. For untrusted PRs, inspect changed workflow files
and package scripts before checking out the branch.

## Worktree Checkout

Keep the current checkout untouched and review in `/tmp`:

```bash
git fetch origin
git worktree add /tmp/gardenops-pr-<number> origin/<head-branch>
cd /tmp/gardenops-pr-<number>
```

For a PR from the same repository, this pattern is usually safer because it
starts from `main` and lets `gh` resolve the PR ref:

```bash
git worktree add /tmp/gardenops-pr-<number> origin/main
cd /tmp/gardenops-pr-<number>
gh pr checkout <number>
```

For fork PRs, do not push to the contributor branch and do not run with elevated
tokens. Keep fixes on a maintainer branch if follow-up commits are needed.

Remove the worktree after review:

```bash
cd /opt/gardenops
git worktree remove /tmp/gardenops-pr-<number>
```

## Local Environment

Install dependencies from lockfiles:

```bash
uv sync --frozen --group test --group lint
cd frontend
npm ci
cd ..
```

Create or update only local disposable env files:

```bash
cp .env.example .env
cp .env.test.example .env.test.local
```

Point both `DATABASE_URL` and `GARDENOPS_TEST_POSTGRES_URL` in
`.env.test.local` at a disposable test database. Do not point tests at the live
runtime database.

Load only the test env before backend checks:

```bash
set -a
. ./.env.test.local
set +a
```

Confirm the loaded database targets before running tests:

```bash
python - <<'PY'
import os
from urllib.parse import urlsplit
for key in ("DATABASE_URL", "GARDENOPS_TEST_POSTGRES_URL"):
    value = os.environ.get(key, "")
    parsed = urlsplit(value)
    print(f"{key}: host={parsed.hostname} db={parsed.path.rsplit('/', 1)[-1]}")
PY
```

Both database names must be disposable test databases. Stop if either points at
the live runtime database.

## Validation

The GitHub CI backend job maps to these local checks:

```bash
uv sync --frozen --group test --group lint
uv run ruff check gardenops tests
uv run ruff format --check gardenops tests
uv run python scripts/check_env_docs.py
python scripts/check_github_action_pins.py
uv run python -c "import gardenops.db as db; db.run_migrations()"
uv run python scripts/check_backend_integrity.py --format text
uv run python -m pytest tests/ -q --tb=short
```

The GitHub CI frontend job maps to these local checks:

```bash
cd frontend
npm ci
python ../scripts/check_innerhtml_sinks.py --root src --allowlist security/innerhtml_allowlist.txt
npm run build
cd ..
node scripts/check_no_sourcemaps.cjs frontend/dist
```

For dependency PRs, also run the relevant audit locally when feasible:

```bash
uv pip freeze | grep -v '^-e ' > /tmp/gardenops-backend-requirements.txt
python -m pip install --upgrade "pip-audit==2.9.0"
pip-audit --strict -r /tmp/gardenops-backend-requirements.txt

cd frontend
npm audit --audit-level=high
cd ..
```

Targeted validation is acceptable for low-risk PRs, but the final merge decision
must say which full checks were skipped and why.

## Agent-Assisted Review

When using a local coding agent, start it inside the PR worktree and give it a
bounded review prompt:

```text
Review this GardenOps pull request as hostile code. Do not edit files yet.
Focus on bugs, security regressions, CI failures, migrations, env changes,
workflow changes, generated files, and missing tests. Do not source production
env files. Report findings with file/line references and then list the exact
validation commands you recommend.
```

For high-risk PRs, use this stricter prompt:

```text
Perform a hostile review of this GardenOps PR. Assume PR code may be malicious
or accidentally unsafe. First classify the risk tier. Then inspect for auth,
authorization, DB migration, env/config, CI workflow, dependency, backup,
restore, deployment, generated-file, and live-host regressions. Do not execute
PR code or edit files. Findings first, with file/line references. Include a
minimal validation plan and explicit rollback/deploy questions.
```

If you want the agent to fix findings, give it a second prompt after reviewing
the findings:

```text
Fix only the confirmed findings from the review. Keep the change scoped. Do not
touch unrelated files. Validate with the standard PR checks and report exactly
what passed or failed.
```

Do not give an agent permission to source production env files, use live
databases, push branches, merge PRs, or alter systemd/nginx/PostgreSQL on the
host unless that is the explicit task.

## Dependabot PRs

For dependency-only PRs:

1. Confirm the diff only changes dependency manifests or lockfiles.
2. Check advisories and CI.
3. Run the relevant local backend/frontend validation.
4. Prefer merging small green dependency PRs before rebasing larger feature PRs.

Merge a clean Dependabot PR:

```bash
gh pr merge <number> --squash --delete-branch
```

If a Dependabot PR conflicts with a newer accepted dependency update, close it
with a short explanation rather than forcing stale lockfile churn.

When several Dependabot PRs are open, process them in this order:

1. GitHub Actions/workflow updates.
2. Backend package updates that unblock backend audit.
3. Frontend package updates that unblock frontend audit.
4. Stale or superseded PRs, closed after `main` already contains the newer
   accepted version.

## Feature PRs

For feature or bug-fix PRs:

1. Read the PR body and diff before running code.
2. Confirm docs and env documentation are updated when behavior/config changes.
3. Require tests for behavior changes.
4. Inspect migrations for reversibility and production impact.
5. Run the standard validation set.
6. Wait for GitHub CI to pass.

If the branch is stale after dependency PRs merge:

```bash
gh pr checkout <number>
git fetch origin
git rebase origin/main
git push --force-with-lease
gh pr checks <number> --watch
```

If the PR changes migrations, deployment examples, env variables, backup/restore
logic, or auth behavior, require a rollout note in the PR description that says:

- what changes for existing deployments
- what must be configured before deploy
- how rollback works
- which data or permissions could be affected

## CI Failure Triage

Inspect check details before changing code:

```bash
gh pr checks <number>
gh run list --branch <head-branch> --limit 10
gh run view <run-id> --log-failed
```

Handle common failures this way:

- Backend lint or format: fix only the reported files, then run backend checks.
- Backend tests: reproduce locally with the same test command before patching.
- Frontend build: run `npm run build` in `frontend/`, then inspect TypeScript
  and security-check output.
- Dependency audit: identify whether the PR itself introduced the advisory or
  whether a separate dependency PR should merge first.
- Workflow failures: inspect workflow diff as high risk before accepting any
  change that increases permissions or runs PR-controlled code with secrets.

## Post-Merge Follow-Up

After merging a PR:

```bash
git fetch origin
git status --short
```

For the live host, deploy from `main` only when the PR is intended for deploy.
Before deploy-impacting changes, take or confirm a fresh backup. After deploy,
check:

```bash
systemctl status gardenops.service gardenops-backup.timer nginx postgresql --no-pager
curl -fsS --resolve gardenops.example.com:443:127.0.0.1 https://gardenops.example.com/api/health
```

For non-deploy PRs, do not touch the live service checkout.

## Merge Gate

Do not merge until all are true:

- The PR is mergeable and not a draft.
- GitHub CI and dependency audits are green.
- Local validation passed or every skipped check is explicitly justified.
- The diff contains no secrets, dumps, logs, media uploads, runtime output, or
  accidental generated files.
- Database, env, systemd, nginx, and GitHub Actions changes have been reviewed
  as high-risk changes.
- Rollback or remediation is clear for deploy-impacting changes.

Merge:

```bash
gh pr merge <number> --squash --delete-branch
```

After merging, update any long-lived feature branches:

```bash
git fetch origin
gh pr checkout <feature-pr-number>
git rebase origin/main
git push --force-with-lease
```

## Emergency Stop

Stop the review and ask for a maintainer decision if any of these appear:

- secrets, private URLs, dumps, logs, media, or generated runtime output in the
  diff
- PR-controlled workflow code requesting broader permissions
- tests or scripts that require production env files
- migrations that rewrite or drop data without a clear rollout plan
- deployment changes that conflict with the current live host
- dependency changes with unresolved advisories
- validation needs credentials or services that are not disposable
