# Dependency maintenance, 25 October 2026

Today starts from live commit `8ae1394f5914737dcffa3a8ef90e2be4d472a598`
and incorporates its same-code main merge. OpenAI advances from 3.33.0 to
3.35.1 and the audit-only platformdirs dependency from 4.13.3 to 4.13.4.
Both releases satisfy the seven-day intake window. No migrations, application
features, CI or maintenance-policy changes are included.

Daily registry checks cover direct, transitive and exact-version dependencies.
The full locked Python graph and frontend audit have no known advisories.
Trusted default-branch scripts validate sources, Action pins, the advisory delta
and Python/npm release ages; no security exception is needed. Newer routine
releases inside the cooldown remain deferred, and existing framework/tool
release lines and exact compatibility pins remain intact.

The filelock 4.2.4 security fix is retained. It addresses CVE-2026-112906,
CVE-2026-112907, CVE-2026-112913, CVE-2026-112914, CVE-2026-112937 and
CVE-2026-112940 in local file/lease locking. Filelock and platformdirs belong
to audit tooling, outside the production dependency profile.

The complete disposable PostgreSQL suite, frontend contracts/build/assets,
lint and environment/raw-HTML guards validate the change. Disposable real-backend
authentication/navigation and deterministic-provider browser journeys check
startup, sessions and optional-provider behavior without production writes.
