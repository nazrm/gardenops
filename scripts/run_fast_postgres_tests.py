#!/usr/bin/env python3
"""Run GardenOps tests against an isolated disposable Postgres cluster."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parent.parent
RUNUSER = Path("/usr/sbin/runuser")
DEFAULT_POSTGRES_OS_USER = "postgres"
TEST_DB = "gardenops_test"
TEST_ROLE = "gardenops_test_runner"
COMMAND_DATABASES = (
    TEST_DB,
    "gardenops_attention_e2e_test",
    "gardenops_task_history_e2e_test",
)
COMMAND_E2E_ENV = {
    "gardenops_attention_e2e_test": (
        "GARDENOPS_ATTENTION_E2E_TEST_URL",
        "GARDENOPS_ATTENTION_E2E_BACKEND_PORT",
        "GARDENOPS_ATTENTION_E2E_FRONTEND_PORT",
    ),
    "gardenops_task_history_e2e_test": (
        "GARDENOPS_TASK_HISTORY_E2E_TEST_URL",
        "GARDENOPS_TASK_HISTORY_E2E_BACKEND_PORT",
        "GARDENOPS_TASK_HISTORY_E2E_FRONTEND_PORT",
    ),
}
APP_POOL_MAX_SIZE = 10
SETUP_CONNECTION_MARGIN = 40
INHERITED_ENV_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
)
TEST_SUBPROCESS_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass
class Cluster:
    work_dir: Path
    pgdata: Path
    socket_dir: Path
    log_dir: Path
    port: int
    postgres_os_user: str | None
    test_password: str
    postmaster_pid: int | None = None
    system_identifier: str | None = None
    preserve_logs: bool = False
    database_markers: dict[str, str] | None = None

    @property
    def postgres_log(self) -> Path:
        return self.log_dir / "postgres.log"

    @property
    def runner_log(self) -> Path:
        return self.log_dir / "runner.log"

    def url_for(self, database: str) -> str:
        password = quote(self.test_password, safe="")
        return f"postgresql://{TEST_ROLE}:{password}@127.0.0.1:{self.port}/{database}"


class InjectedFailure(RuntimeError):
    """Intentional failure used to exercise cleanup paths."""


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    as_user: str | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    if as_user:
        if not RUNUSER.exists():
            raise RuntimeError(f"runuser not found at {RUNUSER}")
        command = [str(RUNUSER), "-u", as_user, "--", *command]
    return subprocess.run(
        command,
        cwd=cwd or ROOT,
        env=env,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
    )


def _write_log(cluster: Cluster, message: str) -> None:
    cluster.log_dir.mkdir(parents=True, exist_ok=True)
    with cluster.runner_log.open("a", encoding="utf-8") as handle:
        handle.write(f"{message}\n")


def _pick_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port != 5432:
            return port


def _postgres_os_user() -> str | None:
    if os.geteuid() == 0:
        return DEFAULT_POSTGRES_OS_USER
    return None


def _chown_recursive(path: Path, user: str) -> None:
    shutil.chown(path, user=user)
    for child in path.rglob("*"):
        shutil.chown(child, user=user)


def _create_cluster() -> Cluster:
    postgres_os_user = _postgres_os_user()
    work_dir = Path(tempfile.mkdtemp(prefix="gardenops-pgtest-", dir="/tmp"))
    log_dir = Path(tempfile.mkdtemp(prefix="gardenops-run-logs-", dir="/tmp"))
    pgdata = work_dir / "pgdata"
    socket_dir = work_dir / "socket"
    for path in (pgdata, socket_dir):
        path.mkdir(mode=0o700)
    work_dir.chmod(0o700)
    log_dir.chmod(0o700)
    if postgres_os_user:
        _chown_recursive(work_dir, postgres_os_user)
        _chown_recursive(log_dir, postgres_os_user)

    return Cluster(
        work_dir=work_dir,
        pgdata=pgdata,
        socket_dir=socket_dir,
        log_dir=log_dir,
        port=_pick_port(),
        postgres_os_user=postgres_os_user,
        test_password=secrets.token_urlsafe(24),
    )


def _append_postgres_config(cluster: Cluster, max_connections: int) -> None:
    socket_dir = str(cluster.socket_dir).replace("'", "''")
    config = f"""

# GardenOps disposable test cluster settings.
listen_addresses = '127.0.0.1'
port = {cluster.port}
unix_socket_directories = '{socket_dir}'
fsync = off
synchronous_commit = off
full_page_writes = off
autovacuum = off
shared_buffers = 128MB
max_connections = {max_connections}
"""
    with (cluster.pgdata / "postgresql.conf").open("a", encoding="utf-8") as handle:
        handle.write(config)

    hba = """
# GardenOps disposable test cluster access.
local all postgres trust
local all all reject
host all all 127.0.0.1/32 scram-sha-256
host all all ::1/128 reject
"""
    (cluster.pgdata / "pg_hba.conf").write_text(hba, encoding="utf-8")
    if cluster.postgres_os_user:
        _chown_recursive(cluster.pgdata, cluster.postgres_os_user)


def _initdb(cluster: Cluster, max_connections: int) -> None:
    _run(
        [
            "initdb",
            "-D",
            str(cluster.pgdata),
            "--username=postgres",
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
            "--no-locale",
            "--encoding=UTF8",
        ],
        as_user=cluster.postgres_os_user,
        capture=True,
    )
    _append_postgres_config(cluster, max_connections)


def _start_cluster(cluster: Cluster) -> None:
    _run(
        [
            "pg_ctl",
            "-D",
            str(cluster.pgdata),
            "-l",
            str(cluster.postgres_log),
            "-w",
            "start",
        ],
        as_user=cluster.postgres_os_user,
        capture=True,
    )
    pid_file = cluster.pgdata / "postmaster.pid"
    first_line = pid_file.read_text(encoding="utf-8").splitlines()[0]
    cluster.postmaster_pid = int(first_line)
    _run(["pg_isready", "-h", "127.0.0.1", "-p", str(cluster.port)], capture=True)
    cluster.system_identifier = _psql_scalar(
        cluster,
        "postgres",
        "SELECT system_identifier FROM pg_control_system();",
        admin=True,
    )


def _psql(
    cluster: Cluster,
    database: str,
    sql: str,
    *,
    admin: bool = False,
    capture: bool = False,
    tuples_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    command = [
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        str(cluster.socket_dir) if admin else "127.0.0.1",
        "-p",
        str(cluster.port),
        "-U",
        "postgres" if admin else TEST_ROLE,
        "-d",
        database,
        "-c",
        sql,
    ]
    if tuples_only:
        command[2:2] = ["-A", "-t"]
    if not admin:
        env["PGPASSWORD"] = cluster.test_password
    return _run(command, env=env, capture=capture)


def _psql_scalar(cluster: Cluster, database: str, sql: str, *, admin: bool = False) -> str:
    result = _psql(cluster, database, sql, admin=admin, capture=True, tuples_only=True)
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return lines[0] if lines else ""


def _sql_literal(raw: str) -> str:
    return "'" + raw.replace("'", "''") + "'"


def _create_databases(cluster: Cluster, shards: int) -> None:
    _psql(
        cluster,
        "postgres",
        f"CREATE ROLE {TEST_ROLE} LOGIN PASSWORD {_sql_literal(cluster.test_password)};",
        admin=True,
        capture=True,
    )
    for database in [TEST_DB, *(f"{TEST_DB}_shard{index}" for index in range(shards))]:
        _psql(
            cluster,
            "postgres",
            f"CREATE DATABASE {database} OWNER {TEST_ROLE};",
            admin=True,
            capture=True,
        )
        _issue_disposable_marker(cluster, database)


def _validate_url(cluster: Cluster, url: str, database: str) -> None:
    info = conninfo_to_dict(url)
    expected = {
        "host": "127.0.0.1",
        "port": str(cluster.port),
        "dbname": database,
        "user": TEST_ROLE,
    }
    for key, value in expected.items():
        if info.get(key) != value:
            raise RuntimeError(f"generated URL has unexpected {key}: {info.get(key)!r}")
    forbidden = {"service", "hostaddr"}
    present = sorted(key for key in forbidden if info.get(key))
    if present:
        raise RuntimeError(f"generated URL contains forbidden fields: {present}")

    result = _psql(
        cluster,
        database,
        "SELECT current_database(), current_user, inet_server_addr(), inet_server_port();",
        capture=True,
    )
    _write_log(cluster, f"validated {database}: {(result.stdout or '').strip()}")
    _psql(
        cluster,
        database,
        "CREATE TEMP TABLE gardenops_runner_ddl_check (id integer);"
        " DROP TABLE gardenops_runner_ddl_check;",
        capture=True,
    )


def _test_env(cluster: Cluster, database: str) -> dict[str, str]:
    env = {name: value for name in INHERITED_ENV_ALLOWLIST if (value := os.environ.get(name))}
    env["PATH"] = TEST_SUBPROCESS_PATH
    env["APP_ENV"] = "test"
    env["AUTH_PASSWORD_HASH_FAST_FOR_TESTS"] = "true"
    env["AUTH_REQUIRED"] = "false"
    env["RATE_LIMIT_BACKEND"] = "memory"
    env["INTERNET_EXPOSED"] = "false"
    env["ALLOWED_HOSTS"] = "localhost,127.0.0.1,[::1],::1,testserver,testclient"
    env["CORS_ALLOW_ORIGINS"] = "http://localhost:5173"
    env["AUTH_PASSWORD_CHECK_HIBP"] = "false"
    url = cluster.url_for(database)
    env["DATABASE_URL"] = url
    env["GARDENOPS_TEST_POSTGRES_URL"] = url
    markers = getattr(cluster, "database_markers", None) or {}
    marker = markers.get(database)
    if marker and cluster.system_identifier:
        env["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"] = marker
        env["GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"] = cluster.system_identifier
    env["TAILLIGHT_URL"] = ""
    env["TAILLIGHT_API_KEY"] = ""
    return env


def _command_env(cluster: Cluster, database: str) -> dict[str, str]:
    env = _test_env(cluster, database)
    if os.environ.get("GARDENOPS_ALLOW_DESTRUCTIVE_E2E") == "1":
        env["GARDENOPS_ALLOW_DESTRUCTIVE_E2E"] = "1"
    command_log_dir = cluster.log_dir / "command-app"
    command_log_dir.mkdir(parents=True, exist_ok=True)
    env["GARDENOPS_LOGS_DIR"] = str(command_log_dir)

    e2e_vars = COMMAND_E2E_ENV.get(database)
    if e2e_vars:
        url_var, backend_port_var, frontend_port_var = e2e_vars
        backend_port = _pick_port()
        frontend_port = _pick_port()
        while frontend_port == backend_port:
            frontend_port = _pick_port()
        env[url_var] = cluster.url_for(database)
        env[backend_port_var] = str(backend_port)
        env[frontend_port_var] = str(frontend_port)
    return env


def _run_migrations(cluster: Cluster, database: str, *, print_failure: bool = True) -> float:
    start = time.perf_counter()
    try:
        _run(
            [
                sys.executable,
                "-c",
                "import gardenops.db as db; db.run_migrations(); db.close_pool()",
            ],
            env=_test_env(cluster, database),
            capture=True,
        )
    except subprocess.CalledProcessError as exc:
        output = exc.stdout or ""
        _write_log(cluster, f"migration failed for {database}:\n{output}")
        if print_failure:
            print(output, file=sys.stderr)
        raise
    return time.perf_counter() - start


def _issue_disposable_marker(cluster: Cluster, database: str) -> str:
    """Bind destructive test work to this temporary cluster and database."""
    is_shard = (
        database.startswith(f"{TEST_DB}_shard")
        and database.removeprefix(f"{TEST_DB}_shard").isdigit()
    )
    if database not in COMMAND_DATABASES and not is_shard:
        raise RuntimeError(f"unsupported disposable command database: {database}")
    if not cluster.system_identifier:
        raise RuntimeError("disposable cluster system identifier is unavailable")

    existing_markers = cluster.database_markers or {}
    marker = next(
        iter(existing_markers.values()),
        f"{cluster.system_identifier}.{secrets.token_urlsafe(24)}",
    )
    _psql(
        cluster,
        "postgres",
        f"ALTER DATABASE {database} SET gardenops.disposable_marker TO {_sql_literal(marker)};",
        admin=True,
        capture=True,
    )
    observed_marker = _psql_scalar(
        cluster,
        database,
        "SELECT current_setting('gardenops.disposable_marker', true);",
    )
    if observed_marker != marker:
        raise RuntimeError("disposable database marker could not be verified")
    if cluster.database_markers is None:
        cluster.database_markers = {}
    cluster.database_markers[database] = marker
    return marker


def _proof_reset(cluster: Cluster, iterations: int) -> int:
    _validate_url(cluster, cluster.url_for(TEST_DB), TEST_DB)
    migration_time = _run_migrations(cluster, TEST_DB)
    _write_log(cluster, f"migration_time={migration_time:.3f}s")

    os.environ.update(_test_env(cluster, TEST_DB))
    import gardenops.db as app_db
    from gardenops.security import create_user
    from tests.base import _truncate_all_tables, strong_password

    conn = app_db.get_db()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename != 'schema_migrations'
            """,
        ).fetchone()
        table_count = int(row["count"])
    finally:
        app_db.return_db(conn)

    durations: list[float] = []
    for index in range(iterations + 1):
        conn = app_db.get_db()
        try:
            app_db.ensure_default_garden(conn)
            create_user(
                conn,
                username=f"proof_user_{index}",
                password=strong_password(f"proof-password-{index}"),
                role="admin",
            )
            conn.commit()
        finally:
            app_db.return_db(conn)

        started = time.perf_counter()
        _truncate_all_tables()
        elapsed = time.perf_counter() - started
        if index > 0:
            durations.append(elapsed)

    app_db.close_pool()
    durations_sorted = sorted(durations)
    p95_index = min(len(durations_sorted) - 1, int(round(len(durations_sorted) * 0.95)) - 1)
    print(f"tables={table_count}")
    print(f"iterations={iterations}")
    print(f"min={min(durations):.4f}s")
    print(f"median={statistics.median(durations):.4f}s")
    print(f"p95={durations_sorted[p95_index]:.4f}s")
    print(f"max={max(durations):.4f}s")
    print(f"mean={statistics.mean(durations):.4f}s")
    print(f"migration_time={migration_time:.3f}s")
    print(f"pgdata={cluster.pgdata}")
    print(f"logs={cluster.log_dir}")
    return 0


def _run_full_suite(cluster: Cluster, shards: int) -> int:
    for database in [TEST_DB, *(f"{TEST_DB}_shard{index}" for index in range(shards))]:
        _validate_url(cluster, cluster.url_for(database), database)
    env = _test_env(cluster, TEST_DB)
    env.pop("DATABASE_URL", None)
    command = [
        sys.executable,
        "scripts/run_backend_shards.py",
        "--shards",
        str(shards),
        "--logs-dir",
        str(cluster.log_dir / "pytest-shards"),
    ]
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    _print_shard_summaries(cluster)
    return int(result.returncode)


def _run_command(
    cluster: Cluster,
    command: list[str],
    *,
    database: str = TEST_DB,
) -> int:
    if database not in COMMAND_DATABASES:
        raise RuntimeError(f"unsupported disposable command database: {database}")
    _create_databases(cluster, 0)
    if database != TEST_DB:
        _psql(
            cluster,
            "postgres",
            f"CREATE DATABASE {database} OWNER {TEST_ROLE};",
            admin=True,
            capture=True,
        )
    _validate_url(cluster, cluster.url_for(database), database)
    migration_time = _run_migrations(cluster, database)
    _write_log(cluster, f"migration_time={migration_time:.3f}s")
    marker = _issue_disposable_marker(cluster, database)
    env = _command_env(cluster, database)
    env["GARDENOPS_DISPOSABLE_POSTGRES_URL"] = cluster.url_for(database)
    env["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"] = marker
    env["GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"] = str(cluster.system_identifier)
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    print(f"command_exit_code={result.returncode}")
    print(f"migration_time={migration_time:.3f}s")
    return int(result.returncode)


def _cleanup_smoke(cluster: Cluster, stage: str, shards: int) -> None:
    if stage == "after-start":
        raise InjectedFailure("injected failure after cluster startup")

    _create_databases(cluster, shards if stage == "during-pytest" else 0)

    if stage == "during-migration":
        _validate_url(cluster, cluster.url_for(TEST_DB), TEST_DB)
        _psql(
            cluster,
            TEST_DB,
            "CREATE TABLE gardenops_runner_forced_migration_failure (id integer);",
            capture=True,
        )
        try:
            _run_migrations(cluster, TEST_DB, print_failure=False)
        except subprocess.CalledProcessError as exc:
            raise InjectedFailure("injected migration failure") from exc
        raise RuntimeError("expected migration cleanup smoke to fail")

    if stage == "during-pytest":
        env = _test_env(cluster, TEST_DB)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/__forced_missing_test__.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        _write_log(cluster, f"forced pytest failure output:\n{result.stdout or ''}")
        if result.returncode != 0:
            raise InjectedFailure("injected pytest failure")
        raise RuntimeError("expected pytest cleanup smoke to fail")

    raise RuntimeError(f"unknown cleanup smoke stage: {stage}")


def _print_shard_summaries(cluster: Cluster) -> None:
    logs_dir = cluster.log_dir / "pytest-shards"
    if not logs_dir.exists():
        return
    for log_path in sorted(logs_dir.glob("shard-*.log")):
        summary = _pytest_summary_line(log_path)
        if summary:
            print(f"{log_path.name}: {summary}")


def _pytest_summary_line(log_path: Path) -> str:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("=") and (" passed" in stripped or " failed" in stripped):
            return stripped.strip("= ")
    return ""


def _port_is_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _cleanup_cluster(
    cluster: Cluster | None,
    *,
    success: bool,
    discard_logs: bool = False,
) -> bool:
    if cluster is None:
        return True
    cleanup_ok = True
    if cluster.postmaster_pid is not None:
        try:
            _run(
                ["pg_ctl", "-D", str(cluster.pgdata), "-w", "stop"],
                as_user=cluster.postgres_os_user,
                capture=True,
            )
        except Exception as exc:
            cleanup_ok = False
            cluster.preserve_logs = True
            _write_log(cluster, f"failed to stop cluster: {exc}")
    if not _port_is_closed(cluster.port):
        cleanup_ok = False
        cluster.preserve_logs = True
        _write_log(cluster, f"port still open after cleanup: {cluster.port}")
    if cleanup_ok:
        try:
            shutil.rmtree(cluster.work_dir)
        except Exception as exc:
            cleanup_ok = False
            cluster.preserve_logs = True
            _write_log(cluster, f"failed to remove disposable cluster data: {exc}")
        if cluster.work_dir.exists():
            cleanup_ok = False
            cluster.preserve_logs = True
            _write_log(cluster, f"disposable cluster data still exists: {cluster.work_dir}")
    if (success or discard_logs) and cleanup_ok and not cluster.preserve_logs:
        try:
            shutil.rmtree(cluster.log_dir)
        except Exception as exc:
            cleanup_ok = False
            cluster.preserve_logs = True
            _write_log(cluster, f"failed to remove disposable runner logs: {exc}")
        if cluster.log_dir.exists():
            cleanup_ok = False
            cluster.preserve_logs = True
            _write_log(cluster, f"disposable runner logs still exist: {cluster.log_dir}")
    if not cleanup_ok or not (success or discard_logs) or cluster.preserve_logs:
        print(f"preserved logs: {cluster.log_dir}", file=sys.stderr)
    return cleanup_ok


def _max_connections(shards: int) -> int:
    return max(80, shards * APP_POOL_MAX_SIZE + SETUP_CONNECTION_MARGIN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--proof-reset", action="store_true", help="benchmark table reset only")
    mode.add_argument("--full-suite", action="store_true", help="run the sharded backend suite")
    mode.add_argument(
        "--command",
        action="store_true",
        help="run a command against one migrated disposable database",
    )
    mode.add_argument(
        "--cleanup-smoke",
        choices=("after-start", "during-migration", "during-pytest"),
        help="inject a failure and verify cleanup of the disposable cluster",
    )
    parser.add_argument("--iterations", type=int, default=20, help="proof-reset iterations")
    parser.add_argument("--shards", type=int, default=4, help="number of shard databases")
    parser.add_argument(
        "--command-database",
        choices=COMMAND_DATABASES,
        default=TEST_DB,
        help="allowlisted disposable database name for --command",
    )
    parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="command and arguments after --command --",
    )
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.shards < 1:
        parser.error("--shards must be at least 1")
    command_args = list(args.command_args)
    if command_args[:1] == ["--"]:
        command_args = command_args[1:]
    if args.command and not command_args:
        parser.error("--command requires a command after --")
    if not args.command and command_args:
        parser.error("command arguments require --command")
    if not args.command and args.command_database != TEST_DB:
        parser.error("--command-database requires --command")

    max_connections = _max_connections(args.shards)
    cluster: Cluster | None = None
    started = time.perf_counter()
    success = False
    interrupted = False
    cleanup_ok = False
    return_code = 1

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt(f"received signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _handle_signal)

    try:
        cluster = _create_cluster()
        print(f"pgdata={cluster.pgdata}")
        print(f"socket_dir={cluster.socket_dir}")
        print(f"port={cluster.port}")
        print(f"postgres_os_user={cluster.postgres_os_user or getpass.getuser()}")
        print(f"max_connections={max_connections}")
        _initdb(cluster, max_connections)
        _start_cluster(cluster)
        if args.cleanup_smoke:
            _cleanup_smoke(cluster, args.cleanup_smoke, args.shards)
        elif args.proof_reset:
            _create_databases(cluster, 0)
            return_code = _proof_reset(cluster, args.iterations)
        elif args.command:
            return_code = _run_command(
                cluster,
                command_args,
                database=args.command_database,
            )
        else:
            _create_databases(cluster, args.shards)
            return_code = _run_full_suite(cluster, args.shards)
        success = return_code == 0 and not interrupted
        print(f"total_time={time.perf_counter() - started:.3f}s")
    except InjectedFailure as exc:
        if not args.cleanup_smoke:
            raise
        print(str(exc))
        return_code = 0
    finally:
        cleanup_ok = _cleanup_cluster(
            cluster,
            success=success,
            discard_logs=bool(args.cleanup_smoke),
        )
    if args.cleanup_smoke:
        if not cleanup_ok:
            return 1
        if cluster and (cluster.work_dir.exists() or cluster.log_dir.exists()):
            return 1
        if cluster and not _port_is_closed(cluster.port):
            return 1
        print(f"cleanup_smoke={args.cleanup_smoke}: passed")
        print(f"total_time={time.perf_counter() - started:.3f}s")
        return 0
    if not cleanup_ok:
        print("disposable Postgres cleanup failed", file=sys.stderr)
        return return_code if return_code != 0 else 1
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
