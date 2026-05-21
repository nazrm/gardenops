# GardenOps Documentation

This directory contains public-facing documentation for installing, configuring,
deploying, and contributing to GardenOps.

| Goal | Document |
|---|---|
| Local install | [installation.md](installation.md) |
| Environment and providers | [configuration.md](configuration.md) |
| AI provider plan | [ai-provider-plan.md](ai-provider-plan.md) |
| ShadeMap sun/shade integration | [shademap.md](shademap.md) |
| Production deployment | [deployment.md](deployment.md) |
| Development and PR checks | [development.md](development.md) |
| PR review workflow | [pr-review-runbook.md](pr-review-runbook.md) |

Keep secrets in `.env`, `.env.test.local`, or your deployment secret store. Do
not commit real provider keys, database URLs, backup files, media uploads,
terrain datasets, or generated runtime output.
