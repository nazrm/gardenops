from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import gardenops.db as db
from gardenops.security import generate_passkey_user_handle, hash_password
from scripts import run_fast_postgres_tests, seed_ui_flow_map_e2e

ROOT = Path(__file__).resolve().parents[1]
DISPOSABLE_URL = "postgresql://gardenops@127.0.0.1:55432/gardenops_test"
DISPOSABLE_SYSTEM_IDENTIFIER = "987654321"
DISPOSABLE_MARKER = f"{DISPOSABLE_SYSTEM_IDENTIFIER}.runner-issued-nonce"


def _set_safe_seed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARDENOPS_UI_FLOW_MAP_E2E_CHILD", "1")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_MODE", "session")
    monkeypatch.setenv("GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE", "1")
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", DISPOSABLE_URL)
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", DISPOSABLE_MARKER)
    monkeypatch.setenv(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        DISPOSABLE_SYSTEM_IDENTIFIER,
    )


def _run_ui_flow_artifact_resolver(
    root: Path,
    artifact_dir: Path | str | None,
) -> subprocess.CompletedProcess[str]:
    script = ROOT / "scripts" / "check_ui_flow_map_e2e.cjs"
    resolver = """
const [scriptPath, rootDir, artifactDir] = process.argv.slice(1);
const { resolveArtifactDirectory } = require(scriptPath);
try {
  process.stdout.write(`${resolveArtifactDirectory(artifactDir, rootDir)}\\n`);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
"""
    command = ["node", "-e", resolver, str(script), str(root)]
    if artifact_dir is not None:
        command.append(str(artifact_dir))
    env = os.environ.copy()
    env.pop("GARDENOPS_UI_FLOW_E2E_ARTIFACT_DIR", None)
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def _is_allowed_browser_request_url(request_url: str) -> bool:
    script = ROOT / "scripts" / "check_ui_flow_map_e2e.cjs"
    resolver = """
const [scriptPath, requestUrl] = process.argv.slice(1);
const { isAllowedBrowserRequestUrl } = require(scriptPath);
process.stdout.write(String(isAllowedBrowserRequestUrl(requestUrl)));
"""
    result = subprocess.run(
        ["node", "-e", resolver, str(script), request_url],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "true"


def _create_artifact_validation_repo(tmp_path: Path) -> Path:
    root = tmp_path / "artifact-validation-repo"
    root.mkdir()
    result = subprocess.run(
        ["git", "init", "--quiet", str(root)],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    (root / ".gitignore").write_text("research/\n")
    (root / "research").mkdir()
    return root


def test_ui_flow_seed_guard_requires_exact_runner_disposable_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)

    seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)

    with pytest.raises(RuntimeError, match="exactly match"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(
            "postgresql://gardenops@127.0.0.1:55433/gardenops_test"
        )
    monkeypatch.setenv(
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "postgresql://gardenops@127.0.0.1:5432/gardenops_test",
    )
    with pytest.raises(RuntimeError, match="port 5432"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(
            "postgresql://gardenops@127.0.0.1:5432/gardenops_test"
        )


def test_ui_flow_seed_guard_rejects_socket_and_missing_or_invalid_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)
    monkeypatch.delenv("GARDENOPS_DISPOSABLE_POSTGRES_URL")
    with pytest.raises(RuntimeError, match="GARDENOPS_DISPOSABLE_POSTGRES_URL"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)

    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", DISPOSABLE_URL)
    socket_url = "postgresql:///gardenops_test"
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", socket_url)
    with pytest.raises(RuntimeError, match="TCP loopback"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(socket_url)

    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", DISPOSABLE_URL)
    monkeypatch.delenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    with pytest.raises(RuntimeError, match="GARDENOPS_DISPOSABLE_POSTGRES_MARKER"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)

    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "wrong-cluster.nonce")
    with pytest.raises(RuntimeError, match="not bound"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://gardenops@127.0.0.1:55432/gardenops_test?application_name=ui-flow",
        "postgresql://gardenops@127.0.0.1:55432/gardenops_test#ui-flow",
        "postgresql://gardenops@127.0.0.1:55432/gardenops_test?hostaddr=127.0.0.2",
        "postgresql://gardenops@127.0.0.1:55432/gardenops_test?service=other-cluster",
    ],
)
def test_ui_flow_seed_guard_rejects_uri_overrides(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    _set_safe_seed_environment(monkeypatch)
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", database_url)

    with pytest.raises(RuntimeError, match="exact TCP URL"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(database_url)


@pytest.mark.parametrize("override_name", ["hostaddr", "service"])
def test_ui_flow_seed_guard_rejects_effective_libpq_overrides(
    monkeypatch: pytest.MonkeyPatch,
    override_name: str,
) -> None:
    _set_safe_seed_environment(monkeypatch)
    conninfo = {
        "dbname": "gardenops_test",
        "host": "127.0.0.1",
        "port": "55432",
        override_name: "unexpected",
    }
    monkeypatch.setattr(seed_ui_flow_map_e2e, "conninfo_to_dict", lambda _url: conninfo)

    with pytest.raises(RuntimeError, match="resolve only to disposable TCP loopback"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)


def test_ui_flow_seed_guard_requires_numeric_runner_system_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", "not-a-number")
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "not-a-number.nonce")

    with pytest.raises(RuntimeError, match="system identifier must be numeric"):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)


def test_ui_flow_seed_marker_must_match_connected_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_safe_seed_environment(monkeypatch)

    class MarkerConnection:
        def __init__(self, marker: str, system_identifier: str) -> None:
            self.marker = marker
            self.system_identifier = system_identifier
            self.calls: list[tuple[str, tuple[str, ...]]] = []

        def execute(
            self,
            statement: str,
            params: tuple[str, ...] = (),
        ) -> SimpleNamespace:
            self.calls.append((statement, params))
            if "current_setting" in statement:
                return SimpleNamespace(fetchone=lambda: {"disposable_marker": self.marker})
            if "pg_control_system" in statement:
                return SimpleNamespace(
                    fetchone=lambda: {"system_identifier": self.system_identifier}
                )
            raise AssertionError(f"unexpected statement: {statement}")

    matching = MarkerConnection(DISPOSABLE_MARKER, DISPOSABLE_SYSTEM_IDENTIFIER)
    seed_ui_flow_map_e2e.verify_ui_flow_map_e2e_database_marker(matching)
    assert matching.calls == [
        (
            "SELECT current_setting(%s, true) AS disposable_marker",
            ("gardenops.disposable_marker",),
        ),
        ("SELECT system_identifier FROM pg_control_system()", ()),
    ]

    with pytest.raises(RuntimeError, match="marker does not match"):
        seed_ui_flow_map_e2e.verify_ui_flow_map_e2e_database_marker(
            MarkerConnection("987654321.different-nonce", DISPOSABLE_SYSTEM_IDENTIFIER)
        )
    with pytest.raises(RuntimeError, match="system identifier does not match"):
        seed_ui_flow_map_e2e.verify_ui_flow_map_e2e_database_marker(
            MarkerConnection(DISPOSABLE_MARKER, "987654322")
        )


def test_ui_flow_seed_verifies_runner_cluster_before_the_truncate_path() -> None:
    source = (ROOT / "scripts" / "seed_ui_flow_map_e2e.py").read_text()
    main_source = source[source.index("def main()") :]

    assert "pg_control_system()" in source
    assert main_source.index("verify_ui_flow_map_e2e_database_marker(conn)") < main_source.index(
        "seed(conn)"
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("GARDENOPS_UI_FLOW_MAP_E2E_CHILD", "0", "runner child"),
        ("APP_ENV", "production", "APP_ENV=test"),
        ("AUTH_REQUIRED", "false", "AUTH_REQUIRED=true"),
        ("AUTH_MODE", "token", "AUTH_MODE=session"),
        ("GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED", "true", "scheduler"),
        ("GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE", "0", "ALLOW_TRUNCATE=1"),
    ],
)
def test_ui_flow_seed_guard_requires_explicit_test_flags(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    _set_safe_seed_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        seed_ui_flow_map_e2e.require_ui_flow_map_e2e_database(DISPOSABLE_URL)


def test_ui_flow_plant_fixture_rows_match_insert_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[tuple[object, ...]]]] = []

    def capture_rows(_conn: object, statement: str, rows: object) -> None:
        calls.append((statement, list(rows)))  # type: ignore[arg-type]

    monkeypatch.setattr(seed_ui_flow_map_e2e, "executemany", capture_rows)

    seed_ui_flow_map_e2e.seed_plots_and_plants(
        object(),
        garden_id=7,
        owner_user_id=11,
    )

    plant_rows = next(rows for statement, rows in calls if "INSERT INTO plants" in statement)
    plot_plant_rows = next(
        rows for statement, rows in calls if "INSERT INTO plot_plants" in statement
    )
    assert len(plant_rows) == 3
    assert all(len(row) == 13 for row in plant_rows)
    assert {int(row[-2]) for row in plant_rows} == {int(str(row[-1])[:4]) for row in plant_rows}
    assert all(str(row[-1]) < seed_ui_flow_map_e2e._fixture_date() for row in plant_rows)
    assert len(plot_plant_rows) == 3
    assert all(len(row) == 5 for row in plot_plant_rows)


def test_ui_flow_snoozed_task_uses_attention_provider_status_and_shared_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_rows: list[tuple[object, ...]] = []

    class TaskConnection:
        def execute(self, statement: str, params: tuple[object, ...]) -> SimpleNamespace:
            if "INSERT INTO garden_tasks" in statement:
                task_rows.append(params)
                return SimpleNamespace(fetchone=lambda: {"id": len(task_rows)})
            raise AssertionError(f"unexpected statement: {statement}")

    monkeypatch.setattr(seed_ui_flow_map_e2e, "set_task_links", lambda *_args: None)
    seed_ui_flow_map_e2e.seed_tasks(TaskConnection(), garden_id=7, owner_user_id=11)

    snoozed = next(row for row in task_rows if row[0] == seed_ui_flow_map_e2e.E2E_TASK_IDS[2])
    assert snoozed[5] == "snoozed"
    assert snoozed[7] == seed_ui_flow_map_e2e._fixture_date(-1)
    assert snoozed[8] == seed_ui_flow_map_e2e._fixture_date()


def test_disposable_postgres_command_mode_exports_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeCluster:
        system_identifier = DISPOSABLE_SYSTEM_IDENTIFIER
        log_dir = tmp_path

        def url_for(self, database: str) -> str:
            return f"postgresql://local/{database}"

    monkeypatch.setattr(run_fast_postgres_tests, "_create_databases", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_validate_url", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_run_migrations", lambda *_args: 1.25)
    monkeypatch.setattr(
        run_fast_postgres_tests,
        "_issue_disposable_marker",
        lambda *_args: DISPOSABLE_MARKER,
    )
    monkeypatch.setattr(run_fast_postgres_tests, "_write_log", lambda *_args: None)
    monkeypatch.setattr(
        run_fast_postgres_tests,
        "_test_env",
        lambda cluster, database: {
            "PATH": os.environ.get("PATH", ""),
            "DATABASE_URL": cluster.url_for(database),
            "GARDENOPS_TEST_POSTGRES_URL": cluster.url_for(database),
        },
    )

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(run_fast_postgres_tests.subprocess, "run", fake_run)

    result = run_fast_postgres_tests._run_command(FakeCluster(), ["example", "--flag"])

    assert result == 7
    assert captured["command"] == ["example", "--flag"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["DATABASE_URL"] == "postgresql://local/gardenops_test"
    assert env["GARDENOPS_TEST_POSTGRES_URL"] == env["DATABASE_URL"]
    assert env["GARDENOPS_DISPOSABLE_POSTGRES_URL"] == env["DATABASE_URL"]
    assert env["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"] == DISPOSABLE_MARKER
    assert env["GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"] == DISPOSABLE_SYSTEM_IDENTIFIER


def test_disposable_runner_uses_a_non_secret_inherited_environment_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile_environment = {
        "ALL_PROXY": "http://proxy.invalid:8080",
        "ANTHROPIC_API_KEY": "must-not-reach-disposable-process",
        "BASH_ENV": "/tmp/untrusted-bash-env",
        "GARDENOPS_UI_FLOW_E2E_DATE": "2099-01-01",
        "NODE_OPTIONS": "--require=/tmp/untrusted-node-hook",
        "OPENAI_API_KEY": "must-not-reach-disposable-process",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://telemetry.invalid",
        "SECURITY_TELEMETRY_BEARER_TOKEN": "must-not-reach-disposable-process",
        "UNLISTED_PROVIDER_PASSWORD": "must-not-reach-disposable-process",
    }
    for name, value in hostile_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LANG", "C.UTF-8")

    class FakeCluster:
        def url_for(self, name: str) -> str:
            return f"postgresql://local/{name}"

    env = run_fast_postgres_tests._test_env(FakeCluster(), "gardenops_test")

    assert env["PATH"] == run_fast_postgres_tests.TEST_SUBPROCESS_PATH
    assert env["LANG"] == "C.UTF-8"
    for name in hostile_environment:
        assert name not in env
    assert set(run_fast_postgres_tests.INHERITED_ENV_ALLOWLIST) == {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
    }


def test_command_env_forwards_only_the_exact_destructive_e2e_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeCluster:
        log_dir = tmp_path

        def url_for(self, name: str) -> str:
            return f"postgresql://local/{name}"

    monkeypatch.setenv("GARDENOPS_ALLOW_DESTRUCTIVE_E2E", "true")
    env = run_fast_postgres_tests._command_env(FakeCluster(), "gardenops_test")
    assert "GARDENOPS_ALLOW_DESTRUCTIVE_E2E" not in env

    monkeypatch.setenv("GARDENOPS_ALLOW_DESTRUCTIVE_E2E", "1")
    env = run_fast_postgres_tests._command_env(FakeCluster(), "gardenops_test")
    assert env["GARDENOPS_ALLOW_DESTRUCTIVE_E2E"] == "1"


def test_disposable_postgres_command_mode_supports_allowlisted_e2e_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = "gardenops_attention_e2e_test"
    created: list[tuple[str, str, bool]] = []
    captured_env: dict[str, str] = {}

    class FakeCluster:
        system_identifier = DISPOSABLE_SYSTEM_IDENTIFIER
        log_dir = tmp_path

        def url_for(self, name: str) -> str:
            return f"postgresql://local/{name}"

    monkeypatch.setattr(run_fast_postgres_tests, "_create_databases", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_validate_url", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_run_migrations", lambda *_args: 0.5)
    monkeypatch.setattr(
        run_fast_postgres_tests,
        "_issue_disposable_marker",
        lambda *_args: DISPOSABLE_MARKER,
    )
    monkeypatch.setattr(run_fast_postgres_tests, "_write_log", lambda *_args: None)
    ports = iter((18001, 15001))
    monkeypatch.setattr(run_fast_postgres_tests, "_pick_port", lambda: next(ports))
    monkeypatch.setattr(
        run_fast_postgres_tests,
        "_psql",
        lambda _cluster, name, sql, *, admin, capture: created.append(
            (name, sql, admin and capture)
        ),
    )
    monkeypatch.setattr(
        run_fast_postgres_tests,
        "_test_env",
        lambda cluster, name: {
            "PATH": os.environ.get("PATH", ""),
            "DATABASE_URL": cluster.url_for(name),
            "GARDENOPS_TEST_POSTGRES_URL": cluster.url_for(name),
        },
    )

    def fake_run(_command: list[str], **kwargs: object) -> SimpleNamespace:
        env = kwargs["env"]
        assert isinstance(env, dict)
        captured_env.update(env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_fast_postgres_tests.subprocess, "run", fake_run)

    result = run_fast_postgres_tests._run_command(
        FakeCluster(),
        ["example"],
        database=database,
    )

    assert result == 0
    assert created == [
        (
            "postgres",
            f"CREATE DATABASE {database} OWNER {run_fast_postgres_tests.TEST_ROLE};",
            True,
        )
    ]
    assert captured_env["DATABASE_URL"] == f"postgresql://local/{database}"
    assert captured_env["GARDENOPS_DISPOSABLE_POSTGRES_URL"] == captured_env["DATABASE_URL"]
    assert captured_env["GARDENOPS_ATTENTION_E2E_TEST_URL"] == captured_env["DATABASE_URL"]
    assert captured_env["GARDENOPS_ATTENTION_E2E_BACKEND_PORT"] == "18001"
    assert captured_env["GARDENOPS_ATTENTION_E2E_FRONTEND_PORT"] == "15001"
    assert captured_env["GARDENOPS_LOGS_DIR"] == str(tmp_path / "command-app")


def test_disposable_postgres_runner_fails_when_final_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cluster = SimpleNamespace(
        pgdata=tmp_path / "pgdata",
        socket_dir=tmp_path / "socket",
        port=55438,
        postgres_os_user=None,
    )
    monkeypatch.setattr(
        run_fast_postgres_tests.sys,
        "argv",
        ["run_fast_postgres_tests.py", "--proof-reset", "--iterations", "1"],
    )
    monkeypatch.setattr(run_fast_postgres_tests.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_create_cluster", lambda: cluster)
    monkeypatch.setattr(run_fast_postgres_tests, "_initdb", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_start_cluster", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_create_databases", lambda *_args: None)
    monkeypatch.setattr(run_fast_postgres_tests, "_proof_reset", lambda *_args: 0)
    monkeypatch.setattr(
        run_fast_postgres_tests,
        "_cleanup_cluster",
        lambda *_args, **_kwargs: False,
    )

    assert run_fast_postgres_tests.main() == 1


def test_disposable_postgres_cleanup_detects_data_removal_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    log_dir = tmp_path / "logs"
    pgdata = work_dir / "pgdata"
    socket_dir = work_dir / "socket"
    pgdata.mkdir(parents=True)
    socket_dir.mkdir()
    log_dir.mkdir()
    cluster = run_fast_postgres_tests.Cluster(
        work_dir=work_dir,
        pgdata=pgdata,
        socket_dir=socket_dir,
        log_dir=log_dir,
        port=55439,
        postgres_os_user=None,
        test_password="test-only",
    )
    monkeypatch.setattr(run_fast_postgres_tests, "_port_is_closed", lambda _port: True)
    monkeypatch.setattr(run_fast_postgres_tests.shutil, "rmtree", lambda _path: None)

    assert not run_fast_postgres_tests._cleanup_cluster(cluster, success=True)
    assert work_dir.exists()
    assert cluster.preserve_logs
    assert "disposable cluster data still exists" in cluster.runner_log.read_text()


def test_ui_flow_browser_script_is_valid_and_blocks_outbound_without_response_mocks() -> None:
    script = ROOT / "scripts" / "check_ui_flow_map_e2e.cjs"
    result = subprocess.run(
        ["node", "--check", str(script)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    source = script.read_text()
    assert "page.route(" not in source
    assert "context.route(" not in source
    assert 'browserContext.route("**/*"' in source
    assert 'route.abort("blockedbyclient")' in source
    assert "browserContext.routeWebSocket(" in source
    assert "route.continue()" in source
    assert ".fulfill(" not in source
    assert "createLoopbackRequestGuard" in source
    assert 'browserContext.on("request"' in source
    assert "isAllowedBrowserRequestUrl" in source
    assert "loopbackRequestGuard.assertClean" in source
    assert "assertLoopbackBaseUrl(BASE_URL)" in source
    assert "GARDENOPS_UI_FLOW_E2E_VIEWPORT" in source
    assert "captureBeyondViewport: false" in source
    assert "transparentPixelRatio" in source
    assert "invalidPixelRatio" in source
    assert 'fetch("/api/auth/users")' in source
    assert "adminUsersAccess === 403" in source
    assert "addPlantEnabled === writeAccess" in source
    assert "importPlantsEnabled === writeAccess" in source
    assert "mobileFabEnabled === writeAccess" in source
    assert "(plantWriteControls > 0) === writeAccess" in source
    assert "(indoorWriteControls > 0) === writeAccess" in source
    assert 'page.locator("#indoor-tab-content").getByText("Genovese Basil"' in source
    assert "if (adminEntryVisible)" in source
    assert 'page.locator("#admin-view").isHidden()' in source
    assert 'viewport: mobile ? "mobile" : "desktop"' in source
    assert "for (const mobile of [false, true])" in source
    assert "const seededAdminSections = [" in source
    assert "Expected exactly one seeded-admin navigation button" in source
    assert "await button.isEnabled()" in source
    assert "adm-nav-btn--active[data-section='${section}']" in source
    assert "if (await button.count() === 0) continue" not in source


@pytest.mark.parametrize(
    ("request_url", "expected"),
    [
        ("http://127.0.0.1:5182/", True),
        ("http://127.0.0.2:5182/api/health", True),
        ("ws://[::1]:5182/", True),
        ("data:text/plain,local", True),
        ("https://example.invalid/collect", False),
        ("file:///tmp/untrusted.html", False),
        ("http://localhost:5182/", False),
        ("http://127.example.invalid/", False),
        ("wss://telemetry.invalid/socket", False),
        ("not a URL", False),
    ],
)
def test_ui_flow_browser_request_guard_rejects_non_loopback_targets(
    request_url: str,
    expected: bool,
) -> None:
    assert _is_allowed_browser_request_url(request_url) is expected


def test_ui_flow_artifact_dir_validation_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    root = _create_artifact_validation_repo(tmp_path)
    research = root / "research"
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("must remain untouched")
    escaped_link = research / "escaped-link"
    escaped_link.symlink_to(outside, target_is_directory=True)

    rejected_cases = [
        (outside, "nested directory under research"),
        (research, "nested directory under research"),
        (escaped_link, "must not traverse symlink"),
        (f"{research}/../outside-artifacts", "must not contain '..'"),
    ]
    for artifact_dir, message in rejected_cases:
        result = _run_ui_flow_artifact_resolver(root, artifact_dir)
        assert result.returncode != 0
        assert message in result.stderr
        assert sentinel.read_text() == "must remain untouched"

    valid_nested_dir = research / "runs" / "review-1"
    result = _run_ui_flow_artifact_resolver(root, valid_nested_dir)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == valid_nested_dir
    assert not valid_nested_dir.exists()
    assert sentinel.read_text() == "must remain untouched"

    default_dir = research / "optimization-map"
    result = _run_ui_flow_artifact_resolver(root, None)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == default_dir
    assert not default_dir.exists()


def test_ui_flow_seed_rolls_back_after_post_truncate_fixture_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = f"ui_flow_seed_rollback_{uuid4().hex}"
    conn = db.get_db()
    try:
        conn.execute(
            """
            INSERT INTO auth_users (
                username, password_hash, password_auth_disabled, passkey_user_handle, role
            )
            VALUES (%s, %s, 0, %s, 'viewer')
            """,
            (
                username,
                hash_password("UiFlowRollback!Passphrase2026"),
                generate_passkey_user_handle(),
            ),
        )
        conn.commit()

        def fail_after_fixture_users(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("injected failure after fixture users")

        monkeypatch.setattr(seed_ui_flow_map_e2e, "ensure_garden", fail_after_fixture_users)
        with pytest.raises(RuntimeError, match="injected failure"):
            seed_ui_flow_map_e2e.seed(conn)
        conn.rollback()

        sentinel = conn.execute(
            "SELECT COUNT(*) AS count FROM auth_users WHERE username = %s",
            (username,),
        ).fetchone()
        fixture_users = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM auth_users
            WHERE username IN (%s, %s, %s)
            """,
            (
                seed_ui_flow_map_e2e.E2E_ADMIN_USERNAME,
                seed_ui_flow_map_e2e.E2E_EDITOR_USERNAME,
                seed_ui_flow_map_e2e.E2E_VIEWER_USERNAME,
            ),
        ).fetchone()
        assert sentinel is not None and int(sentinel["count"]) == 1
        assert fixture_users is not None and int(fixture_users["count"]) == 0
    finally:
        conn.rollback()
        conn.execute("DELETE FROM auth_users WHERE username = %s", (username,))
        conn.commit()
        db.return_db(conn)
        db.close_pool()


def test_ui_flow_runner_uses_authenticated_relative_frozen_fixture() -> None:
    runner = ROOT / "scripts" / "run_ui_flow_map_e2e.sh"
    source = runner.read_text()
    seed_source = (ROOT / "scripts" / "seed_ui_flow_map_e2e.py").read_text()

    assert os.access(runner, os.X_OK)
    assert "export APP_ENV=test" in source
    assert "export AUTH_REQUIRED=true" in source
    assert "export AUTH_MODE=session" in source
    assert '"GARDENOPS_UI_FLOW_E2E_USERNAME=$E2E_ADMIN_USERNAME"' in source
    assert '"GARDENOPS_UI_FLOW_E2E_PASSWORD=$E2E_ADMIN_PASSWORD"' in source  # noqa: E501  # push-sanitizer: allow SECRET_ASSIGNMENT
    assert '"GARDENOPS_UI_FLOW_E2E_EDITOR_USERNAME=$E2E_EDITOR_USERNAME"' in source
    assert '"GARDENOPS_UI_FLOW_E2E_EDITOR_PASSWORD=$E2E_EDITOR_PASSWORD"' in source  # noqa: E501  # push-sanitizer: allow SECRET_ASSIGNMENT
    assert '"GARDENOPS_UI_FLOW_E2E_VIEWER_USERNAME=$E2E_VIEWER_USERNAME"' in source
    assert '"GARDENOPS_UI_FLOW_E2E_VIEWER_PASSWORD=$E2E_VIEWER_PASSWORD"' in source  # noqa: E501  # push-sanitizer: allow SECRET_ASSIGNMENT
    assert "--command --command-database gardenops_test" in source
    assert "require_disposable_parent" in source
    assert source.index("  require_disposable_parent") < source.index(
        'ARTIFACT_DIR="$(validate_artifact_dir'
    )
    assert "GARDENOPS_UI_FLOW_MAP_E2E_CHILD=1" in source
    assert "scrub_inherited_environment" in source
    assert "env -i" in source
    assert "setsid env -i" in source
    assert "mktemp -d /tmp/gardenops-ui-flow-map-e2e." in source
    assert 'export GARDENOPS_LOGS_DIR="$LOG_DIR"' in source
    assert "ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY" in source
    assert "OTEL_*" in source
    assert "OPENAI_*" in source
    assert "curl --noproxy '*'" in source
    assert "validate_artifact_dir()" in source
    assert 'git -C "$ROOT_DIR" check-ignore -q -- research' in source
    assert 'realpath -m -- "$requested_path"' in source
    assert source.index('ARTIFACT_DIR="$(validate_artifact_dir') < source.index(
        'mkdir -p "$ARTIFACT_DIR/screenshots"'
    )
    assert "password=E2E_EDITOR_PASSWORD" in seed_source  # noqa: E501  # push-sanitizer: allow SECRET_ASSIGNMENT
    assert "password=E2E_VIEWER_PASSWORD" in seed_source  # noqa: E501  # push-sanitizer: allow SECRET_ASSIGNMENT
    assert "validate_e2e_date()" in source
    assert "validate_viewport()" in source
    assert "research/optimization-map/runs/ui-flow-$RUN_ID" in source
    assert '"GARDENOPS_UI_FLOW_E2E_VIEWPORT=$VIEWPORT"' in source
    assert "export GARDENOPS_UI_FLOW_E2E_DATE" in source
    assert "export GARDENOPS_ATTENTION_FROZEN_NOW_MS=" in source
    assert "export GARDENOPS_ATTENTION_FROZEN_DATE=" in source
    assert "1783684800000" not in source
    assert "2026-07-10" not in source
    assert "2026-07-10" not in seed_source
    assert "E2E_DATE = _fixture_date_from_environment()" in seed_source
    assert '"snoozed"' in seed_source
    assert "scripts/seed_ui_flow_map_e2e.py snapshot" in source
    assert 'chmod 600 "$ARTIFACT_DIR/traces/ui-flow-database-snapshot.json"' in source
    assert '"rain_days": 2' in seed_source
    assert '"total_mm": 18' in seed_source
    assert (
        'seed_plots_and_plants(conn, garden_id=garden_id, owner_user_id=users["viewer"])'
        in seed_source
    )


def test_ui_flow_runner_rejects_direct_child_execution() -> None:
    script = ROOT / "scripts" / "run_ui_flow_map_e2e.sh"
    result = subprocess.run(
        ["bash", str(script), "--child", str(ROOT / "research" / "optimization-map"), "", "all"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert "must run through run_fast_postgres_tests.py --command" in result.stderr


def test_ui_flow_runner_rejects_unsafe_artifact_paths_before_startup(
    tmp_path: Path,
) -> None:
    script = ROOT / "scripts" / "run_ui_flow_map_e2e.sh"
    (ROOT / "research").mkdir(exist_ok=True)
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("must remain untouched")
    rejected_cases = [
        (outside, "path must resolve beneath research/"),
        (ROOT / "research", "research/ itself is not an artifact directory"),
        (f"{ROOT}/research/../outside-artifacts", "path traversal is not allowed"),
    ]
    for artifact_dir, message in rejected_cases:
        env = os.environ.copy()
        env["GARDENOPS_UI_FLOW_E2E_ARTIFACT_DIR"] = str(artifact_dir)
        result = subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
        assert result.returncode == 2
        assert message in result.stderr
        assert sentinel.read_text() == "must remain untouched"


def test_ui_flow_runner_bounds_process_group_cleanup() -> None:
    script = ROOT / "scripts" / "run_ui_flow_map_e2e.sh"
    result = subprocess.run(
        ["bash", "-n", str(script)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    source = script.read_text()
    assert "stop_process_group()" in source
    assert 'kill -TERM -- "-$pid"' in source
    assert "CLEANUP_POLL_ATTEMPTS=40" in source
    assert "for ((attempt = 0; attempt < CLEANUP_POLL_ATTEMPTS; attempt++)); do" in source
    assert 'kill -KILL -- "-$pid"' in source
    assert 'wait "$pid" 2>/dev/null || true' in source
