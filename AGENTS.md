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
