# GardenOps

GardenOps is a production garden-management app. Work from the existing code,
tests, and documentation; keep changes focused and preserve unrelated user
changes.

Before editing, check `git status --short --branch`. Do not discard or reset
changes you did not make.

Keep the remote clean:

- Never commit `.env` files, credentials, API keys, private keys, database
  dumps, logs, media uploads, build output, or local tool state.
- Keep local research and generated output ignored. Review the complete diff
  before staging and use `git diff --check` before publishing.
- Before commit, push, or pull request creation, inspect staged paths and
  content for secrets and generated/private artifacts.

Keep the project current:

- Update the smallest relevant documentation when behavior, setup, testing,
  deployment, or developer workflow changes.
- Run the focused tests and checks for the changed area; do not claim checks
  passed unless they ran in the current work.
- Deploy only from the current `main` branch after the normal build,
  migration, integrity, health, and log checks pass.

The owner authorized daily dependency maintenance and conditional automatic PR
merges. On 11 November 2026 the owner chose a local-only release gate: GitHub
Actions is disabled. Routine updates run from the fixed
`maintenance/local-dependencies` checkout, change only dependency manifests and
locks, pass the full isolated local checks, deploy sealed artifacts, and then
open/update their PR. This is the authorized exception to the normal main-only
rule. The host's independent local gate verifies the exact live release and
protected PR before merging. Do not bypass that gate or weaken branch
protection. Source, policy, and migration changes need supervised review; do
not automatically introduce new database migrations.
The one-time PR #253 catch-up may deploy a reviewed direct child of live
`594c92446067ea7ddaf3522e11e636c2dfcc2967` before merge only after full
sealed checks with no migration changes; its local status must pass before merge.
Before deploying a dependency update, run the trusted base-branch dependency
source, advisory-delta and release-age checks against the candidate lockfiles.
Keep routine updates outside the seven-day cooldown; only generated evidence
of an actual advisory fix permits the policy's security exception. Do not
weaken the gate to make maintenance pass.
