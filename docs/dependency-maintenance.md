# Dependency maintenance, 30 October 2026

This update derives from operator-deployed live commit
`5609a1426b2f687fa0d54ca633202def225afb18`. The maintenance branch fast-forwards
to that main commit, retaining its authenticated image/media hardening and all
previous request-log, offline and deployment protections.

The locked OpenAI SDK advances from 3.36.1 to 3.37.0 and the transitive C parser
pycparser from 3.11 to 3.12. Both releases satisfy the seven-day release-age
policy. The verified host runtimes and application pins remain Node 24.22.2
and Python 3.14.10. No application API adaptations or new migrations are needed.

The daily registry check covers every Python lock entry and all direct,
transitive and exact npm pins. The complete locked Python audit and frontend
npm audit are clear. Trusted current-main source, Action-pin, advisory-delta and
seven-day release-age checks pass without an exception. Newer OpenAI,
certificate/validation, pytest, Starlette, Rolldown and PostCSS releases remain
inside the cooldown. Existing framework/compiler/native tool compatibility
bounds remain.

Validation includes frozen installs and lock consistency, the complete
four-shard disposable PostgreSQL suite, ruff checks/format, environment and
raw-HTML guards, frontend contracts/types/assets and the disposable real-backend
provider/session browser journey. Optional-provider behavior and migration
trees are preserved. CI and maintenance policy are unchanged.

The additional complete-journeys foundation/identity runner times out before
login because its Enter-key step leaves the password field hidden. The same
failure reproduces on unchanged live commit `5609a1426b2f687fa0d54ca633202def225afb18`.
The separate deterministic-provider runner signs in through the real session
form successfully and checks authenticated provider behavior. The extra
foundation/identity suite remains a recorded validation limitation; its runner
and assertions are unchanged.
