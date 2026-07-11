# Postgres Test-Speed Plan

## Objective

Reduce backend test wall time without weakening production behavior, changing the
live Postgres service, or reducing test isolation.

Current measured baselines on the live host:

- serial backend suite after test-only password-hash optimization: about
  38 minutes 59 seconds
- file-sharded backend suite with the existing test Postgres databases:
  about 25 minutes 51 seconds

The remaining dominant cost is Postgres reset I/O. API-style tests call
`_truncate_all_tables()` from `tests/base.py`, which truncates every public table
except `schema_migrations` before reseeding. The current test DB has 59
non-migration public tables, and Postgres was observed waiting on
`DataFileImmediateSync` during `TRUNCATE TABLE`.

## Current Implementation Status

The first implementation exists at `scripts/run_fast_postgres_tests.py`.

Validated on May 19, 2026:

- `--proof-reset --iterations 20` passed against a generated disposable
  cluster
- reset benchmark result: 59 tables, median 0.0997 seconds, p95 0.1116 seconds,
  mean 0.0989 seconds
- migration setup time in proof mode: 1.226 seconds
- full four-shard backend run passed against a generated disposable cluster
- latest full-suite timing: shard execution 45.94 seconds, total runner time
  48.53 seconds including cluster setup and cleanup
- cleanup smoke modes passed for injected failures after startup, during
  migration, and during pytest
- no `/tmp/gardenops-pgtest-*` directories remained after successful runs
- `gardenops.service` remained active and `/api/health` returned
  `{"status":"ok"}` after the disposable runs

Extended and revalidated on July 10, 2026:

- `--command -- command [args...]` runs a database-backed command against the
  migrated disposable base database
- `--command-database` supports an allowlist of dedicated E2E database names and
  exports matching seeder URLs, free app ports, and isolated logs
- final cleanup verification failure now makes an otherwise successful command
  or suite return nonzero
- all three injected cleanup stages, the dedicated Attention/task-history
  journeys, the broad authenticated UI map, and the full four-shard suite passed
- the expanded four-shard suite completed in 68.90 seconds of shard execution
  and 71.61 seconds total on the same host

## Recommendation

Implement a disposable test-only Postgres runner as an optional local fast path,
not as a replacement for the normal CI/release confidence path.

Use this runner to attack the Postgres sync bottleneck safely. In parallel, keep
reducing unnecessary `BaseApiTest` usage so the suite becomes less dependent on
database reset speed.

## Proposed Implementation

Build a Python supervisor in `scripts/`, for example:

```bash
uv run python scripts/run_fast_postgres_tests.py --shards 4
```

A Python supervisor is preferred over a shell-only implementation because it can
own subprocess groups, parse URLs, handle signals consistently, record timings,
and verify cleanup identity.

The runner must:

- create a unique temporary working directory outside the repository with
  `0700` permissions
- create separate subdirectories for `PGDATA`, Unix sockets, and logs
- run `initdb` for a dedicated cluster
- write explicit `postgresql.conf` and `pg_hba.conf` files for the cluster
- bind TCP only to `127.0.0.1`
- set `unix_socket_directories` to the runner-owned socket directory
- select an unused local TCP port and include it in every generated URL
- create a random test DB role, for example `gardenops_test_runner`
- create one base database named exactly `gardenops_test`
- create shard databases named exactly `gardenops_test_shard0` through
  `gardenops_test_shard{N-1}`, where `N` is the requested shard count
- make the test role own every generated database
- export `APP_ENV=test`
- export `AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true`
- export `GARDENOPS_TEST_POSTGRES_URL` pointing at the generated base test DB
- support a proof mode that runs the reset microbenchmark without the full
  pytest suite
- run `uv run python scripts/run_backend_shards.py --shards N`
- report setup time, migration time, pytest time, cleanup time, and total time
- stop only the Postgres cluster it started
- clean up the temporary data/socket directories on success
- preserve enough logs on failure to debug startup, migration, or cleanup issues

## Test-Only Postgres Settings

Use settings that are acceptable only for a disposable test cluster:

```text
listen_addresses = '127.0.0.1'
unix_socket_directories = '<runner-owned socket dir>'
fsync = off
synchronous_commit = off
full_page_writes = off
autovacuum = off
shared_buffers = 128MB
max_connections = <derived from shard count>
```

Connection budget:

```text
max_connections >= shards * 10 + setup_margin
```

The app pool is currently `max_size=10` per pytest process, so for four shards
the runner should budget at least 40 app connections plus migration/setup
headroom. A practical first value is 80 for four shards. If shard count is
configurable, compute the value from the requested count and reject counts that
exceed the runner's budget.

These settings must never be applied to the live/system Postgres service.

## URL And Auth Guardrails

The runner must generate all connection URLs itself. It must not inherit a
runtime `DATABASE_URL` or derive test URLs from `/etc/gardenops.env`.

Before running pytest, parse generated URLs with a real libpq-aware parser, such
as `psycopg.conninfo`, and enforce:

- explicit host `127.0.0.1`
- explicit selected port
- no `service` parameter
- no multiple hosts
- no non-loopback `hostaddr`
- no inherited query parameters that redirect connection behavior
- no Unix socket path outside the runner-owned socket directory
- database name is exactly `gardenops_test` or
  `gardenops_test_shard{0..N-1}`
- user is the generated test role

Validate every shard URL before spawning pytest by connecting and checking:

```sql
SELECT current_database(), current_user, inet_server_addr(), inet_server_port();
```

Also run a trivial DDL permission check in every generated database before the
parallel test run starts.

## Cluster Lifecycle Guardrails

The runner must fail closed when lifecycle assumptions are not true.

- refuse to reuse an existing `PGDATA`
- record `PGDATA`, socket directory, port, postmaster PID, and system identifier
- start Postgres with `pg_ctl -D "$PGDATA" -w start`
- verify readiness with `pg_isready` against the selected host and port
- verify `postmaster.pid` belongs to the started cluster
- own or track the child process group used for pytest
- handle normal exit, pytest failure, migration/setup failure, `SIGINT`,
  `SIGTERM`, and `SIGHUP`
- stop Postgres with `pg_ctl -D "$PGDATA" -w stop`
- never stop a process unless its PID and `PGDATA` match the recorded cluster
- after cleanup, verify the recorded port is closed and the recorded postmaster
  PID is gone
- on success, remove `PGDATA` and socket directories
- on failure, preserve a timestamped log directory with redacted URLs,
  `postgresql.conf`, `pg_hba.conf`, `pg_ctl` output, Postgres logs, runner
  timings, shard logs, and cleanup status

## Host Preflight

Before starting the disposable cluster, check and print:

- Postgres binary paths and versions for `initdb`, `postgres`, `pg_ctl`,
  `createdb`, and `psql`
- selected filesystem for `PGDATA`
- free bytes and inodes on that filesystem
- available memory
- SELinux mode when available
- chosen port
- shard count and derived `max_connections`
- live `gardenops.service` status when systemd is available

Prefer `/tmp` for the first implementation. Allow `/dev/shm` only behind an
explicit flag after checking tmpfs capacity, because tmpfs can distort benchmark
results and fail under memory pressure.

## Quick Proof Gate

Before benchmarking the full backend suite, prove that the disposable cluster
actually fixes the measured bottleneck. Add a proof mode, for example:

```bash
uv run python scripts/run_fast_postgres_tests.py --proof-reset --iterations 20
```

The proof mode should:

- start the disposable cluster with the same guardrails as the full runner
- create only the base `gardenops_test` database unless shard behavior is being
  tested
- run migrations once
- call the same table-reset path used by tests, or an equivalent SQL reset that
  truncates the same non-migration public tables
- run at least 20 reset iterations after one warm-up reset
- report min, median, p95, max, and mean reset time
- report the number of truncated tables
- report the filesystem used for `PGDATA`
- verify cleanup exactly as the full runner does

The proof should be compared with the current shared test DB reset benchmark.
Earlier measurement on the shared durable test DB was about 5.49 seconds per
reset. Treat the disposable cluster as worth full-suite implementation only if
median reset time drops below 1 second, or at minimum improves by 70 percent.
If the proof mode does not clear that bar, the next bottleneck is probably test
fixture structure rather than Postgres durability, and the work should pivot to
reducing unnecessary `BaseApiTest` usage.

## Benchmark Method

Do not source the production service environment for pytest benchmarks.

Baseline should use `.env.test.local`:

```bash
set -a
. ./.env.test.local
set +a
/usr/bin/time -f 'elapsed=%E' uv run python scripts/run_backend_shards.py --shards 4
```

Candidate:

```bash
/usr/bin/time -f 'elapsed=%E' uv run python scripts/run_fast_postgres_tests.py --shards 4
```

Quick proof:

```bash
uv run python scripts/run_fast_postgres_tests.py --proof-reset --iterations 20
```

Run at least three paired baseline/candidate trials before adoption. Report:

- median wall time
- min and max wall time
- setup time
- migration time
- pytest time
- cleanup time
- filesystem used for `PGDATA`
- host load or other obvious contention

The candidate should beat the current sharded baseline by at least 30 percent
on median wall time before becoming the recommended local full-suite path.

## Acceptance Criteria

The implementation is good enough to adopt as an optional local fast path only
if all criteria pass:

- `uv run python scripts/run_fast_postgres_tests.py --shards 4` completes
  successfully
- all backend tests pass
- every generated database starts empty except system catalogs before migrations
- every generated database has the expected final migration versions
- every generated database has an equivalent schema signature after setup
- generated URLs pass the strict URL/auth guardrails
- the runner proves it connected to the disposable cluster, not live Postgres
- proof mode reports median reset time below 1 second or at least 70 percent
  faster than the shared durable test DB reset benchmark
- median wall time improves by at least 30 percent over the current sharded
  baseline
- `gardenops.service` remains active afterward when systemd is available
- the live health endpoint still returns `{"status":"ok"}` afterward when local
  service validation is available
- no persistent Postgres config or systemd service changes are made
- no temporary Postgres process remains after the runner exits
- no temporary socket remains after the runner exits
- no temporary data directory remains after successful runner exit
- failure logs are preserved with secrets redacted

## Negative Cleanup Tests

Add runner-level tests or smoke modes that intentionally fail after each major
lifecycle phase and verify cleanup:

- after `initdb`
- after Postgres startup
- after database creation
- during migration/setup
- during pytest execution
- on `SIGINT`
- on `SIGTERM`

Each negative test should prove that the recorded postmaster PID is gone, the
recorded port is closed, sockets are gone, and `PGDATA` is handled according to
the success/failure retention policy.

## CI And Release Policy

The disposable fast runner should start as a local/developer optimization.

Keep the existing CI path or another normal-durability Postgres path as a
required confidence gate until the fast runner has proven stable. If the fast
runner is later added to CI, keep at least one scheduled or release-blocking run
that uses normal Postgres durability settings so crash/WAL-adjacent assumptions
are not silently dropped.

## Follow-Up Optimizations

After the disposable runner is validated, reduce fixture debt separately:

- add duration-aware file sharding using a cached timing file
- instrument `_truncate_all_tables()` and per-class setup time
- classify `BaseApiTest` users into no-DB, DB-only, and full-API categories
- move DB-free tests off `BaseApiTest`
- add a lighter DB base for tests that need Postgres but not a full API client,
  seeded media directory, default admin, plots, and plants
- keep node-level sharding available only when file-level balancing becomes a
  problem, because node collection was slower in the current benchmark

## Integrated Hostile Review Findings

The initial plan was reviewed from DB engineer, systems engineer, and test
engineer perspectives. The review changed the implementation requirements in
these ways:

- hard-coded four-shard database creation is not acceptable; shard DBs must be
  generated from the actual requested shard count
- substring checks like "database name contains test" are not safe enough; exact
  generated database names and libpq-aware URL validation are required
- binding to `127.0.0.1` is not enough; Unix sockets and `pg_hba.conf` must also
  be controlled
- cleanup must verify cluster identity using recorded `PGDATA`, PID, port, and
  system identifier rather than merely looking for no remaining process
- benchmark commands must not source `/etc/gardenops.env`
- `/dev/shm` is an explicit experimental option, not the default storage path
- connection limits must be derived from shard count and app pool size
- failure logs must survive cleanup, while temp database state is still
  disposable
- adoption as a default is premature until CI/local parity and normal-durability
  confidence paths are clear
- fixture refactoring remains necessary; the fast cluster should not become an
  excuse to keep every light test on `BaseApiTest`
