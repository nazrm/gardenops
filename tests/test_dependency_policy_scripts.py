import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dependency_sources_module():
    script_path = ROOT / "scripts" / "check_dependency_sources.py"
    spec = importlib.util.spec_from_file_location("check_dependency_sources", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_package_lock(root: Path, packages: dict[str, object]) -> None:
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages}),
        encoding="utf-8",
    )


def test_dependency_source_check_rejects_root_only_npm_packages(tmp_path, monkeypatch):
    module = load_dependency_sources_module()
    write_package_lock(tmp_path, {"": {"name": "frontend"}})
    monkeypatch.setattr(module, "ROOT", tmp_path)

    errors = module.check_package_lock()

    assert "frontend/package-lock.json does not contain npm dependency package entries" in errors


def test_npm_release_age_check_rejects_root_only_npm_packages(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    temp_script = scripts_dir / "check_npm_release_age.cjs"
    temp_script.write_text(
        (ROOT / "scripts" / "check_npm_release_age.cjs").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    write_package_lock(tmp_path, {"": {"name": "frontend"}})

    result = subprocess.run(
        ["node", str(temp_script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    expected_error = (
        "npm release-age check: frontend/package-lock.json "
        "does not contain npm dependency package entries"
    )
    assert expected_error in result.stderr
