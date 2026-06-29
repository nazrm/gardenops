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


def load_github_action_pins_module():
    return load_script_module("check_github_action_pins", "check_github_action_pins.py")


def load_innerhtml_sinks_module():
    return load_script_module("check_innerhtml_sinks", "check_innerhtml_sinks.py")


def load_env_docs_module():
    return load_script_module("check_env_docs", "check_env_docs.py")


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
        (
            f"""
[[package]]
name = "{package_name}"
version = "{version}"
""".lstrip()
            + 'sdist = { url = "https://files.pythonhosted.org/packages/fresh.tar.gz", '
            + 'upload-time = "2999-01-01T00:00:00Z" }\n'
        ),
        encoding="utf-8",
    )


def write_hashed_uv_lock(
    root: Path,
    package_name: str = "hashed-package",
    version: str = "1.0.0",
) -> None:
    (root / "uv.lock").write_text(
        (
            f"""
[[package]]
name = "{package_name}"
version = "{version}"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://files.pythonhosted.org/packages/hashed.tar.gz", hash = "sha256:abc" }}
wheels = [
  {{ url = "https://files.pythonhosted.org/packages/hashed.whl", hash = "sha256:def" }},
]
""".lstrip()
        ),
        encoding="utf-8",
    )


def test_dependency_source_check_rejects_pypi_package_without_artifact_hashes(
    tmp_path,
    monkeypatch,
):
    module = load_dependency_sources_module()
    (tmp_path / "uv.lock").write_text(
        """
[[package]]
name = "metadata-only"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)

    errors = module.check_uv_lock()

    assert "metadata-only has no hashed PyPI artifact metadata" in errors


def test_dependency_source_check_accepts_hashed_pypi_artifacts(tmp_path, monkeypatch):
    module = load_dependency_sources_module()
    write_hashed_uv_lock(tmp_path)
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.check_uv_lock() == []


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
                        "source": "pip-audit base/head diff",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("GARDENOPS_SECURITY_RELEASE_BYPASS", str(evidence_path))
    monkeypatch.setenv("GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE", "true")

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
                        "source": "pip-audit base/head diff",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("GARDENOPS_SECURITY_RELEASE_BYPASS", str(evidence_path))
    monkeypatch.setenv("GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE", "true")

    with pytest.raises(SystemExit):
        module.main()

    error_output = capsys.readouterr().err
    assert "fresh-package==2.0.0" in error_output
    assert "7-day cooldown window" in error_output


def test_python_release_age_rejects_forged_bypass_without_trusted_source(
    tmp_path,
    monkeypatch,
):
    module = load_python_release_age_module()
    write_uv_lock(tmp_path)
    evidence_path = tmp_path / "security-release-bypass.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "python": [
                    {
                        "package": "fresh-package",
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
    monkeypatch.setenv("GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE", "true")

    with pytest.raises(module.SecurityBypassError, match="source"):
        module._load_security_release_bypasses()


def test_python_release_age_rejects_external_bypass_override_without_opt_in(
    tmp_path,
    monkeypatch,
):
    module = load_python_release_age_module()
    evidence_path = tmp_path / "security-release-bypass.json"
    evidence_path.write_text(json.dumps({"schema": 1, "python": []}), encoding="utf-8")
    monkeypatch.setenv("GARDENOPS_SECURITY_RELEASE_BYPASS", str(evidence_path))
    monkeypatch.delenv("GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE", raising=False)

    with pytest.raises(module.SecurityBypassError, match="external bypass file"):
        module._load_security_release_bypasses()


def test_github_action_pin_check_rejects_mutable_uses_ref(tmp_path):
    module = load_github_action_pins_module()
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = workflow_dir / "ci.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    errors = module.check_workflows(tmp_path)

    assert any("actions/checkout@v4" in error for error in errors)


def test_github_action_pin_check_accepts_sha_pinned_uses_ref(tmp_path):
    module = load_github_action_pins_module()
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = workflow_dir / "ci.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n"
        "      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0\n",
        encoding="utf-8",
    )

    assert module.check_workflows(tmp_path) == []


def test_github_action_pin_check_rejects_local_actions(tmp_path):
    module = load_github_action_pins_module()
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = workflow_dir / "ci.yml"
    workflow.write_text(
        "jobs:\n  test:\n    steps:\n      - uses: ./.github/actions/local\n",
        encoding="utf-8",
    )

    errors = module.check_workflows(tmp_path)

    assert any("./.github/actions/local" in error for error in errors)


def test_innerhtml_guard_detects_bracket_assignment_compound_and_helper_alias(tmp_path):
    module = load_innerhtml_sinks_module()
    source = tmp_path / "widget.ts"
    source.write_text(
        """
import { setReviewedDynamicHtml as reviewedHtml } from "./dom";
node["innerHTML"] = payload;
node.outerHTML += payload;
reviewedHtml(node, payload);
""".lstrip(),
        encoding="utf-8",
    )

    sinks = module.find_sinks(tmp_path)
    helpers = module.find_reviewed_dynamic_helpers(tmp_path)

    assert any('node["innerHTML"] = payload;' in sink for sink in sinks)
    assert any("node.outerHTML += payload;" in sink for sink in sinks)
    assert any("reviewedHtml(node, payload);" in helper for helper in helpers)


def test_innerhtml_guard_detects_line_split_assignment(tmp_path):
    module = load_innerhtml_sinks_module()
    source = tmp_path / "widget.ts"
    source.write_text(
        """
node
  ["innerHTML"]
  = payload;
other.innerHTML
  = payload;
""".lstrip(),
        encoding="utf-8",
    )

    sinks = module.find_sinks(tmp_path)

    assert any('["innerHTML"]' in sink for sink in sinks)
    assert any("other.innerHTML" in sink for sink in sinks)


def test_env_docs_scanner_reads_frontend_vite_env(tmp_path, monkeypatch):
    module = load_env_docs_module()
    frontend_src = tmp_path / "frontend" / "src"
    frontend_src.mkdir(parents=True)
    (frontend_src / "app.ts").write_text(
        "\n".join(
            [
                "const name = import.meta.env.VITE_APP_NAME;",
                "const slug = import.meta.env['VITE_APP_SLUG'];",
                "const { VITE_APP_THEME } = import.meta.env;",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "PYTHON_SOURCE_ROOTS", ())
    monkeypatch.setattr(module, "EXTRA_SOURCE_FILES", ())
    monkeypatch.setattr(module, "FRONTEND_SOURCE_ROOTS", (frontend_src,))

    used = module._scan_python_files()
    module._scan_js_files(used)

    assert used == {
        "VITE_APP_NAME": {"frontend/src/app.ts"},
        "VITE_APP_SLUG": {"frontend/src/app.ts"},
        "VITE_APP_THEME": {"frontend/src/app.ts"},
    }


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


def test_security_bypass_generator_audits_hashed_requirements_without_pip(
    tmp_path,
    monkeypatch,
):
    module = load_security_bypass_module()
    observed: dict[str, object] = {}

    def fake_run(
        command,
        *,
        cwd=module.ROOT,
        allow_audit_failure=False,
    ):
        observed["command"] = command
        observed["allow_audit_failure"] = allow_audit_failure
        return subprocess.CompletedProcess(command, 0, '{"dependencies": []}', "")

    monkeypatch.setattr(module, "_run", fake_run)

    audit_data = module._run_pip_audit(tmp_path / "requirements.txt", tmp_path / "cache")

    assert audit_data == {"dependencies": []}
    assert observed["allow_audit_failure"] is True
    assert "--disable-pip" in observed["command"]


def test_codeowners_covers_security_release_bypass_generator():
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")

    assert "/scripts/ @nazrm" in codeowners


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
