# Dependency maintenance, 28 October 2026

This update derives from live commit `97b697c49f80574614ac3f40f2f605a1e75d0580`
and incorporates its identical-code main merge. The exact audit-profile pip pin
advances from 26.2.1 to 26.2.2, an age-eligible stable patch. Runtime Python
remains 3.14.10. No application, authentication, migration or CI changes are made.

The daily registry check covers every Python lock entry and all direct,
transitive and exact npm pins. The complete locked Python audit and frontend
npm audit are clear. Trusted current-main source, Action-pin, advisory-delta and
seven-day release-age checks pass without an exception. Newer HTTP clients,
OpenAI, certificate/parser packages, PostCSS and Rolldown remain inside the
cooldown. Existing framework/compiler/native tool compatibility bounds remain.

Validation includes frozen installs, the complete four-shard disposable
PostgreSQL suite, ruff checks/format, environment and raw-HTML guards, frontend
contracts/types/assets and disposable provider/authentication browser journeys.
The additional complete-journeys phase-0 harness timed out awaiting the password
field after starting WebAuthn on the desktop profile; no authentication setting
or test policy is changed to bypass it. Migration trees remain identical.
