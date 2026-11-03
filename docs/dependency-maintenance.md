# Dependency maintenance

## 31 October 2026

This update derives from live `048f5c359ba76cdf90a1477a92e862b0356e2654`
and its identical-source main merge `51b970a`. The locked pyasn1 dependency
advances from 0.6.4 to 0.6.5, fixing CVE-2026-119194, CVE-2026-119195,
CVE-2026-119196 and CVE-2026-119197. These cover quadratic decoder work for
colliding tags, fragmented strings/ANY, BIT STRINGs and binary REAL mantissas.
Certifi advances from 2026.10.19 to the age-eligible 2026.10.23 CA bundle.

GardenOps reaches pyasn1 through WebAuthn Android-key certificate decoding
during authenticated or invitation-bound passkey registration. The upstream
verifier checks the certificate chain, trusted root and attestation signature
before schema-driven ASN.1 decoding. An application-level exploit through
those checks has not been demonstrated; the library update removes the
affected decoder implementations without changing registration policy.

The unchanged current-main advisory-delta generator identifies all four
advisories removed by exactly pyasn1 0.6.4 to 0.6.5, permitting the existing
security exception to its seven-day cooldown. Certifi passes the ordinary
release-age check. Other fresh Python, PostCSS and Rolldown releases remain
deferred. Source and Action-pin policies, authentication settings, migrations,
optional providers and Node/Python release lines are unchanged.

Local bounded baseline/fixed decoder checks cover all four advisory classes,
decoded-value preservation and ordinary BER/CER/DER controls. Required
validation includes the frontend contracts/types/assets build, complete
four-shard disposable PostgreSQL suite, ruff/format, environment/raw-HTML
guards, locked audits and disposable startup/session/provider browser journey.
The previously documented extra foundation/identity browser-runner limitation
remains; that additional runner was not rerun for this dependency-only change.

## 30 October 2026

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
