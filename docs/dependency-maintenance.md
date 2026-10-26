# Dependency maintenance, 26 October 2026

Today starts from live commit `646521e34a1d313250be2d25374945f171dd6cd6`
and incorporates its same-code main merge. FastAPI advances from 0.143.3 to
0.143.5, Starlette from 1.8.0 to 1.9.0, httpx2/httpcore2 from 2.13.1 to
2.14.0, audit-only platformdirs from 4.13.4 to 4.13.5, and frontend PostCSS
from 8.5.30 to 8.5.31. All changed releases satisfy the seven-day intake window.

The [FastAPI patch](https://fastapi.tiangolo.com/release-notes/#01435) fixes an
empty byte-file-list parameter case. [Starlette 1.9](https://github.com/encode/starlette/blob/main/docs/release-notes.md)
corrects URL-path encoding and streams TestClient response bodies; closing an
unfinished test stream now cancels the application. The
[HTTP client update](https://github.com/pydantic/httpx2/blob/main/src/httpx2/CHANGELOG.md)
adds CIDR/IPv6 proxy exclusions, while its core fixes proxy targets, concurrent
HTTP/2 reads and closed-TLS writes. Existing application APIs and optional
provider configuration require no changes.

Daily registry checks cover direct, transitive and exact-version dependencies.
The full locked Python graph and frontend audit have no known advisories.
Trusted default-branch scripts validate sources, Action pins, the advisory delta
and Python/npm release ages; no security exception is needed. Newer routine
releases inside the cooldown remain deferred. Existing framework/tool release
lines, unrelated exact pins and accepted newer locks are retained.

The filelock 4.2.4 security fix remains in place. It addresses CVE-2026-112906,
CVE-2026-112907, CVE-2026-112913, CVE-2026-112914, CVE-2026-112937 and
CVE-2026-112940 in local file/lease locking. Filelock and platformdirs belong
to audit tooling, outside the production dependency profile.

Validation uses the complete disposable PostgreSQL suite, frontend
contracts/build/assets, lint and environment/raw-HTML guards. Disposable
real-backend authentication/navigation and deterministic-provider browser
journeys check startup, sessions and optional-provider behavior without
production writes. No migrations, application features, CI or maintenance-policy
changes are included.
