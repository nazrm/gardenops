# Dependency maintenance, 24 October 2026

This update starts from the operator-deployed commit
`9f3bba19d0c50252da65ce83596b82782f811149`. It retains that release's reduced
dependency graph and does not introduce database migrations or CI changes.

FastAPI advances from 0.143.2 to the age-eligible 0.143.3 patch, and OpenAI
from 3.31.0 to 3.33.0. Optional-provider behavior is checked with the repository's
disposable, deterministic provider browser journey.

The audit-only transitive dependency filelock advances from 4.2.0 to 4.2.4,
fixing CVE-2026-112906, CVE-2026-112907, CVE-2026-112913, CVE-2026-112914,
CVE-2026-112937 and CVE-2026-112940. These affect local file/lease locking;
filelock is not part of the production dependency profile.

Trusted scripts from the default-branch commit generate the exact base/head
advisory evidence for this security update. That evidence permits only the
filelock version change through the existing release-age exception. Source,
advisory-delta, Action and Python/npm release-age checks still apply.

Daily registry checks include direct, transitive and exact-version dependencies.
Newer routine Python releases, PostCSS 8.5.31 and Rolldown 1.2.15 remain inside
the seven-day window. Framework/tool release-line changes require separate
compatibility work; existing intake limits remain intact.
