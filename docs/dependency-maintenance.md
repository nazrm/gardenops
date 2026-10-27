# Dependency maintenance, 27 October 2026

This update starts from live commit `c55a251c3ff90de9f4db61f10a32e04c5ae25966`
and incorporates its same-code main merge. The Python pin advances from
3.14.9 to 3.14.10, so production rebuilds use the expedited security release.
[Python 3.14.10](https://www.python.org/downloads/release/python-31410/)
distrusts compromised eMudhra intermediate CA keys (CVE-2026-115557),
rejects destination-replacing links in tarfile filters (CVE-2026-114583),
and rejects negative cookie versions.

httpx2 and httpcore2 advance from 2.14.0 to 2.15.1. The
[HTTP client release notes](https://github.com/pydantic/httpx2/blob/main/src/httpx2/CHANGELOG.md)
describe reuse of the default Linux SSL context, preventing increasing CPU use
across repeated handshakes with older OpenSSL. Python 3.10 support was removed;
GardenOps already requires Python 3.14. Application APIs and optional-provider
configuration require no changes.

Daily registry checks cover direct, transitive and exact-version dependencies.
The locked Python graph and frontend audit have no known advisories. Trusted
current-main scripts validate sources, Action pins, advisory deltas and Python/npm
release ages; the HTTP packages satisfy the seven-day intake window without an
exception. Newer httpx2/httpcore2, OpenAI, Starlette, PostCSS, Rolldown and other
routine releases remain deferred inside the cooldown. Existing exact framework,
compiler and tool compatibility pins remain unchanged. The filelock 4.2.4 security
fix remains in the audit-only profile.

Validation uses locked installs, the complete four-shard disposable PostgreSQL
suite, ruff checks/format, environment/Action/raw-HTML guards, frontend contracts,
types, build and asset checks, and real-backend disposable authentication/provider
browser journeys. Migration digests are unchanged. No application features, CI
or maintenance-policy changes are included.
