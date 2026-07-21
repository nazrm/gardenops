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
    assert 'install -d -o root -g "$SERVICE_GROUP" -m 0750 "$(dirname "$LOCK_FILE")"' not in script
    assert "umask 0027" in script
    assert 'm 0755 "$RELEASE_ROOT" "$RELEASES_DIR"' in script
    assert 'preflight_release "$release"' in script
    assert 'check_backend_integrity.py" --allow-production' in script
    assert "X-Forwarded-Host: $health_host" in script
    assert "rollback refused because migration contents differ" in script
    assert "mv -Tf" in script
