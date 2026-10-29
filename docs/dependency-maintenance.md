# Dependency maintenance, 29 October 2026

This update derives from operator-deployed live commit
`0448f8d7989c638490035c0d8548eb6814c5a021`. The maintenance branch fast-forwards
to that main commit, retaining its request-log redaction, access-log handling,
offline behavior and deployment hardening.

The locked OpenAI SDK advances from 3.35.1 to 3.36.1 and its HTTP clients
httpx2/httpcore2 from 2.15.1 to 2.16.0. Rolldown advances from 1.2.14 to
1.2.15, including its matching platform bindings and required OXC types.
All changed packages satisfy the seven-day release-age policy. The host's
verified same-line runtime update advances the Node pin to 24.22.2; Python
remains 3.14.10. No application API adaptations or new migrations are needed.

The daily registry check covers every Python lock entry and all direct,
transitive and exact npm pins. The complete locked Python audit and frontend
npm audit are clear. Trusted current-main source, Action-pin, advisory-delta and
seven-day release-age checks pass without an exception. Newer OpenAI,
certificate/parser, pytest, Starlette and PostCSS releases remain inside the
cooldown. Existing framework/compiler/native tool compatibility bounds remain.

Validation includes frozen installs and lock consistency, the complete
four-shard disposable PostgreSQL suite, ruff checks/format, environment and
raw-HTML guards, frontend contracts/types/assets and the disposable real-backend
provider/session browser journey. Optional-provider behavior and migration
trees are preserved. CI and maintenance policy are unchanged.
