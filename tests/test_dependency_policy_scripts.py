import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script_module(module_name: str, script_name: str):
    script_path = ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_dependency_sources_module():
    return load_script_module("check_dependency_sources", "check_dependency_sources.py")


def load_python_release_age_module():
    return load_script_module("check_python_release_age", "check_python_release_age.py")


def load_security_bypass_module():
    return load_script_module(
        "generate_security_release_bypass", "generate_security_release_bypass.py"
    )


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


def write_uv_lock(root: Path, package_name: str = "fresh-package", version: str = "2.0.0") -> None:
    (root / "uv.lock").write_text(
        f"""
[[package]]
name = "{package_name}"
version = "{version}"
sdist = {{
    url = "https://files.pythonhosted.org/packages/fresh.tar.gz",
    upload-time = "2999-01-01T00:00:00Z",
}}
""".lstrip(),
        encoding="utf-8",
    )


def test_python_release_age_allows_generated_security_bypass(tmp_path, monkeypatch, capsys):
    module = load_python_release_age_module()
    write_uv_lock(tmp_path)
    evidence_path = tmp_path / "security-release-bypass.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "python": [
                    {
                        "package": "fresh_package",
                        "from": "1.0.0",
                        "to": "2.0.0",
                        "advisories_fixed": ["GHSA-test-advisory"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("GARDENOPS_SECURITY_RELEASE_BYPASS", str(evidence_path))

    module.main()

    output = capsys.readouterr().out
    assert "fresh-package==2.0.0 fixing GHSA-test-advisory" in output
    assert "Python locked packages satisfy the release-age policy." in output


def test_python_release_age_rejects_unrelated_security_bypass(tmp_path, monkeypatch, capsys):
    module = load_python_release_age_module()
    write_uv_lock(tmp_path)
    evidence_path = tmp_path / "security-release-bypass.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "python": [
                    {
                        "package": "other-package",
                        "from": "1.0.0",
                        "to": "2.0.0",
                        "advisories_fixed": ["GHSA-test-advisory"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("GARDENOPS_SECURITY_RELEASE_BYPASS", str(evidence_path))

    with pytest.raises(SystemExit):
        module.main()

    error_output = capsys.readouterr().err
    assert "fresh-package==2.0.0" in error_output
    assert "7-day cooldown window" in error_output


def test_security_bypass_generator_emits_python_advisory_fix():
    module = load_security_bypass_module()
    base_audit = {
        "dependencies": [
            {
                "name": "cryptography",
                "version": "46.0.7",
                "vulns": [{"id": "GHSA-537c-gmf6-5ccf"}],
            }
        ]
    }
    head_audit = {
        "dependencies": [
            {
                "name": "cryptography",
                "version": "48.0.1",
                "vulns": [],
            }
        ]
    }

    entries, warnings = module.build_python_bypasses(base_audit, head_audit)

    assert warnings == []
    assert entries == [
        {
            "package": "cryptography",
            "from": "46.0.7",
            "to": "48.0.1",
            "advisories_fixed": ["GHSA-537c-gmf6-5ccf"],
            "source": "pip-audit base/head diff",
        }
    ]


def test_security_bypass_generator_rejects_still_vulnerable_python_update():
    module = load_security_bypass_module()
    base_audit = {
        "dependencies": [
            {
                "name": "starlette",
                "version": "1.0.1",
                "vulns": [{"id": "GHSA-82w8-qh3p-5jfq"}],
            }
        ]
    }
    head_audit = {
        "dependencies": [
            {
                "name": "starlette",
                "version": "1.1.0",
                "vulns": [{"id": "GHSA-82w8-qh3p-5jfq"}],
            }
        ]
    }

    entries, warnings = module.build_python_bypasses(base_audit, head_audit)

    assert entries == []
    assert warnings == []


def test_security_bypass_generator_emits_npm_advisory_fix():
    module = load_security_bypass_module()
    base_lock = {"packages": {"": {}, "node_modules/vite": {"version": "8.0.15"}}}
    head_lock = {"packages": {"": {}, "node_modules/vite": {"version": "8.0.16"}}}
    base_audit = {
        "vulnerabilities": {
            "vite": {
                "name": "vite",
                "via": [{"url": "https://github.com/advisories/GHSA-fx2h-pf6j-xcff"}],
                "nodes": ["node_modules/vite"],
            }
        }
    }
    head_audit = {"vulnerabilities": {}}

    entries, warnings = module.build_npm_bypasses(base_audit, head_audit, base_lock, head_lock)

    assert warnings == []
    assert entries == [
        {
            "package": "vite",
            "from": "8.0.15",
            "to": "8.0.16",
            "advisories_fixed": ["GHSA-fx2h-pf6j-xcff"],
            "source": "npm audit base/head diff",
        }
    ]
