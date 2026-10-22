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

The owner authorized daily dependency maintenance on 22 October 2026. Those
updates run in a separate maintenance checkout, pass the documented tests,
deploy from `maintenance/daily-updates`, and open/update their PR after live
verification. This is the authorized exception to the normal main-only rule.
Do not automatically introduce new database migrations. On 22 October 2026 the
owner also authorized automatic daily PR merges after all checks pass. Publish
the tested deployment and let the host's independent protected-CI merge guard
perform that merge; never bypass it or weaken branch protection.
Before deploying a dependency update, run the trusted base-branch dependency
source, advisory-delta and release-age checks against the candidate lockfiles.
Keep routine updates outside the seven-day cooldown; only generated evidence
of an actual advisory fix permits the policy's security exception. Do not
weaken the gate to make maintenance pass.
