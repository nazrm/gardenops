from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "deploy" / "gardenops-release-preflight"
DEPLOY = ROOT / "deploy" / "gardenops-atomic-deploy"


def _release_fixture(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    (release / "gardenops").mkdir(parents=True)
    (release / "gardenops" / "__init__.py").write_text("", encoding="utf-8")
    (release / "gardenops" / "main.py").write_text("app = object()\n", encoding="utf-8")
    (release / "nio.py").write_text("# release preflight fixture\n", encoding="utf-8")
    (release / "migrations").mkdir()
    (release / "migrations" / "001.sql").write_text("SELECT 1;\n", encoding="utf-8")
    dist = release / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><script src="/assets/app.js"></script>\n',
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("export {};\n", encoding="utf-8")
    python = release / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    return release


def _run_preflight(release: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(PREFLIGHT), str(release)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )


def test_release_preflight_accepts_readable_complete_release(tmp_path: Path) -> None:
    release = _release_fixture(tmp_path)

    result = _run_preflight(release)

    assert result.returncode == 0, result.stderr
    assert "release preflight: OK" in result.stdout


def test_release_preflight_rejects_index_with_missing_asset(tmp_path: Path) -> None:
    release = _release_fixture(tmp_path)
    (release / "frontend" / "dist" / "assets" / "app.js").unlink()

    result = _run_preflight(release)

    assert result.returncode != 0
    assert "index references missing asset: /assets/app.js" in result.stderr


def test_atomic_deploy_has_required_safety_gates() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    assert "flock -n" in script
    assert "LOCK_FILE=${GARDENOPS_DEPLOY_LOCK:-$DEFAULT_LOCK_DIR/deploy.lock}" in script
    assert 'install -d -o root -g root -m 0750 "$DEFAULT_LOCK_DIR"' in script
    assert 'exec 9>>"$LOCK_FILE"' in script
    assert 'exec 9>"$LOCK_FILE"' not in script
    assert "lock path must not be a symbolic link" in script
    assert "lock directory must be root-owned and not group- or world-writable" in script
    assert "umask 0027" in script
    assert 'm 0755 "$RELEASE_ROOT" "$RELEASES_DIR"' in script
    assert 'preflight_release "$release"' in script
    assert 'check_backend_integrity.py" --allow-production' in script
    assert "X-Forwarded-Host: $health_host" in script
    assert "rollback refused because migration contents differ" in script
    assert "mv -Tf" in script
    assert "--extra matrix" in script
    assert "MATRIX_SERVICE=${GARDENOPS_MATRIX_SERVICE:-gardenops-matrix.service}" in script
    assert 'systemctl restart "$MATRIX_SERVICE"' in script
    assert 'systemctl is-active --quiet "$MATRIX_SERVICE"' in script


def test_atomic_activate_quiesces_writers_before_migration() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    stop = script.index('systemctl stop "$SERVICE"')
    migration = script.index('"import gardenops.db as db; db.run_migrations()"')
    restore_disabled = script.index("restore_service_on_failure=0", migration)
    integrity = script.index('check_backend_integrity.py" --allow-production')
    switch = script.index('atomic_link "$release" "$CURRENT_LINK"')

    assert stop < migration < restore_disabled < integrity < switch
    assert 'systemctl is-active --quiet "$SERVICE"' in script
    assert "trap restore_quiesced_service EXIT" in script
    assert 'systemctl start "$SERVICE"' in script
    assert "restore_service_on_failure=0" in script
