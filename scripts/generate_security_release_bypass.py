"""Generate release-age bypass evidence for dependency security fixes.

The generated file is intentionally narrow: it only lists package version
updates that remove known advisories from the locked dependency graph. Release
age checks consume this file and still reject unrelated fresh packages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".gardenops" / "security-release-bypass.json"


class BypassGenerationError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    allow_audit_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    allowed_codes = {0, 1} if allow_audit_failure else {0}
    if result.returncode not in allowed_codes:
        command_text = " ".join(command)
        raise BypassGenerationError(
            f"{command_text} failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
    return result


def _extract_json(command_name: str, output: str) -> dict[str, Any]:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise BypassGenerationError(f"{command_name} did not emit a JSON object")
    try:
        data = json.loads(output[start : end + 1])
    except json.JSONDecodeError as error:
        raise BypassGenerationError(f"{command_name} emitted invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise BypassGenerationError(f"{command_name} emitted JSON that is not an object")
    return data


def _write_git_file(base_ref: str, repo_path: str, destination: Path, *, required: bool) -> None:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{repo_path}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if required:
            raise BypassGenerationError(
                f"could not read {repo_path} from {base_ref}: "
                f"{result.stderr.decode('utf-8', errors='replace')}"
            )
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result.stdout)


def _materialize_base(base_ref: str, repo_paths: list[str], destination: Path) -> None:
    for repo_path in repo_paths:
        _write_git_file(base_ref, repo_path, destination / repo_path, required=True)
    _write_git_file(base_ref, ".python-version", destination / ".python-version", required=False)


def _normalize_python_name(name: str) -> str:
    return name.replace("_", "-").lower()


def _export_python_requirements(project_dir: Path, output_path: Path) -> None:
    _run(
        [
            "uv",
            "export",
            "--frozen",
            "--all-groups",
            "--format",
            "requirements.txt",
            "--no-emit-project",
            "--output-file",
            str(output_path),
            "--quiet",
        ],
        cwd=project_dir,
    )


def _run_pip_audit(requirements_path: Path, cache_dir: Path) -> dict[str, Any]:
    result = _run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--strict",
            "-r",
            str(requirements_path),
            "--format",
            "json",
            "--progress-spinner",
            "off",
            "--cache-dir",
            str(cache_dir),
        ],
        allow_audit_failure=True,
    )
    return _extract_json("pip-audit", result.stdout + result.stderr)


def _python_dependencies(audit_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    dependencies: dict[str, dict[str, Any]] = {}
    for dependency in audit_data.get("dependencies", []):
        if not isinstance(dependency, dict):
            continue
        name = dependency.get("name")
        version = dependency.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        advisories = {
            vulnerability["id"]
            for vulnerability in dependency.get("vulns", [])
            if isinstance(vulnerability, dict) and isinstance(vulnerability.get("id"), str)
        }
        dependencies[_normalize_python_name(name)] = {
            "version": version,
            "advisories": advisories,
        }
    return dependencies


def build_python_bypasses(
    base_audit: dict[str, Any], head_audit: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    base_dependencies = _python_dependencies(base_audit)
    head_dependencies = _python_dependencies(head_audit)
    grouped: dict[tuple[str, str, str], set[str]] = {}
    warnings: list[str] = []

    for name, base_info in base_dependencies.items():
        base_advisories = base_info["advisories"]
        if not base_advisories:
            continue

        head_info = head_dependencies.get(name)
        if head_info is None:
            continue

        base_version = base_info["version"]
        head_version = head_info["version"]
        if base_version == head_version:
            warnings.append(f"python {name} still has version {head_version}; no bypass emitted")
            continue

        fixed_advisories = base_advisories - head_info["advisories"]
        if not fixed_advisories:
            continue

        grouped.setdefault((name, base_version, head_version), set()).update(fixed_advisories)

    return [
        {
            "package": name,
            "from": from_version,
            "to": to_version,
            "advisories_fixed": sorted(advisories),
            "source": "pip-audit base/head diff",
        }
        for (name, from_version, to_version), advisories in sorted(grouped.items())
    ], warnings


def _package_name_from_lock_path(package_path: str) -> str | None:
    parts = package_path.split("/")
    try:
        node_modules_index = len(parts) - 1 - parts[::-1].index("node_modules")
    except ValueError:
        return None

    if node_modules_index + 1 >= len(parts):
        return None

    first_name_part = parts[node_modules_index + 1]
    if first_name_part.startswith("@"):
        if node_modules_index + 2 >= len(parts):
            return None
        return f"{first_name_part}/{parts[node_modules_index + 2]}"
    return first_name_part


def _load_package_lock(project_dir: Path) -> dict[str, Any]:
    data = json.loads((project_dir / "package-lock.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BypassGenerationError(f"{project_dir / 'package-lock.json'} is not a JSON object")
    return data


def _npm_package_versions(lock_data: dict[str, Any]) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    packages = lock_data.get("packages", {})
    if not isinstance(packages, dict):
        return versions

    for package_path, package_info in packages.items():
        if package_path == "" or not isinstance(package_info, dict):
            continue
        name = _package_name_from_lock_path(package_path)
        version = package_info.get("version")
        if name and isinstance(version, str):
            versions.setdefault(name, set()).add(version)
    return versions


def _advisory_id_from_npm_via(via: object) -> str | None:
    if not isinstance(via, dict):
        return None

    url = via.get("url")
    if isinstance(url, str):
        match = re.search(r"(GHSA-[A-Za-z0-9-]+)", url)
        if match:
            return match.group(1)

    source = via.get("source")
    if isinstance(source, str | int):
        return str(source)
    return None


def _versions_for_npm_vulnerability(
    vulnerability: dict[str, Any],
    lock_data: dict[str, Any],
    fallback_name: str,
) -> set[str]:
    packages = lock_data.get("packages", {})
    versions: set[str] = set()

    for node in vulnerability.get("nodes", []):
        if not isinstance(node, str) or not isinstance(packages, dict):
            continue
        package_info = packages.get(node)
        if isinstance(package_info, dict) and isinstance(package_info.get("version"), str):
            versions.add(package_info["version"])

    if versions:
        return versions

    return _npm_package_versions(lock_data).get(fallback_name, set())


def _npm_audit_findings(
    audit_data: dict[str, Any], lock_data: dict[str, Any]
) -> dict[str, dict[str, set[str]]]:
    findings: dict[str, dict[str, set[str]]] = {}
    vulnerabilities = audit_data.get("vulnerabilities", {})
    if not isinstance(vulnerabilities, dict):
        return findings

    for key, vulnerability in vulnerabilities.items():
        if not isinstance(key, str) or not isinstance(vulnerability, dict):
            continue
        name = vulnerability.get("name")
        package_name = name if isinstance(name, str) else key
        versions = _versions_for_npm_vulnerability(vulnerability, lock_data, package_name)
        advisories = {
            advisory_id
            for advisory_id in (
                _advisory_id_from_npm_via(via) for via in vulnerability.get("via", [])
            )
            if advisory_id
        }
        for advisory in advisories:
            findings.setdefault(package_name, {}).setdefault(advisory, set()).update(versions)

    return findings


def _run_npm_audit(project_dir: Path) -> dict[str, Any]:
    result = _run(
        ["npm", "audit", "--package-lock-only", "--json"],
        cwd=project_dir,
        allow_audit_failure=True,
    )
    return _extract_json("npm audit", result.stdout + result.stderr)


def build_npm_bypasses(
    base_audit: dict[str, Any],
    head_audit: dict[str, Any],
    base_lock: dict[str, Any],
    head_lock: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    base_findings = _npm_audit_findings(base_audit, base_lock)
    head_findings = _npm_audit_findings(head_audit, head_lock)
    head_versions = _npm_package_versions(head_lock)
    grouped: dict[tuple[str, str, str], set[str]] = {}
    warnings: list[str] = []

    for package_name, advisory_versions in base_findings.items():
        for advisory, base_versions in advisory_versions.items():
            if advisory in head_findings.get(package_name, {}):
                continue

            current_versions = head_versions.get(package_name, set())
            if not current_versions:
                continue
            if len(base_versions) != 1 or len(current_versions) != 1:
                warnings.append(
                    f"npm {package_name} advisory {advisory} has ambiguous versions "
                    f"base={sorted(base_versions)} head={sorted(current_versions)}; "
                    "no bypass emitted"
                )
                continue

            base_version = next(iter(base_versions))
            head_version = next(iter(current_versions))
            if base_version == head_version:
                warnings.append(
                    f"npm {package_name} still has version {head_version}; no bypass emitted"
                )
                continue

            grouped.setdefault((package_name, base_version, head_version), set()).add(advisory)

    return [
        {
            "package": package_name,
            "from": from_version,
            "to": to_version,
            "advisories_fixed": sorted(advisories),
            "source": "npm audit base/head diff",
        }
        for (package_name, from_version, to_version), advisories in sorted(grouped.items())
    ], warnings


def generate_python_bypasses(
    base_ref: str, temp_dir: Path, cache_dir: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    base_dir = temp_dir / "base-python"
    _materialize_base(base_ref, ["pyproject.toml", "uv.lock"], base_dir)

    base_requirements = temp_dir / "base-python-requirements.txt"
    head_requirements = temp_dir / "head-python-requirements.txt"
    _export_python_requirements(base_dir, base_requirements)
    _export_python_requirements(ROOT, head_requirements)

    base_audit = _run_pip_audit(base_requirements, cache_dir)
    head_audit = _run_pip_audit(head_requirements, cache_dir)
    return build_python_bypasses(base_audit, head_audit)


def generate_npm_bypasses(base_ref: str, temp_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    base_dir = temp_dir / "base-npm"
    _materialize_base(base_ref, ["frontend/package.json", "frontend/package-lock.json"], base_dir)

    base_frontend = base_dir / "frontend"
    head_frontend = ROOT / "frontend"
    base_lock = _load_package_lock(base_frontend)
    head_lock = _load_package_lock(head_frontend)
    base_audit = _run_npm_audit(base_frontend)
    head_audit = _run_npm_audit(head_frontend)
    return build_npm_bypasses(base_audit, head_audit, base_lock, head_lock)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="Git ref for the pull request base")
    parser.add_argument(
        "--ecosystem",
        choices=("all", "python", "npm"),
        default="all",
        help="Dependency ecosystem to inspect",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path for generated bypass evidence",
    )
    parser.add_argument(
        "--pip-audit-cache-dir",
        type=Path,
        default=Path(os.environ.get("PIP_AUDIT_CACHE_DIR", "/tmp/pip-audit-cache")),
        help="Cache directory passed to pip-audit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    python_entries: list[dict[str, Any]] = []
    npm_entries: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="gardenops-security-bypass-") as temp_path:
        temp_dir = Path(temp_path)
        if args.ecosystem in {"all", "python"}:
            python_entries, python_warnings = generate_python_bypasses(
                args.base_ref, temp_dir, args.pip_audit_cache_dir
            )
            warnings.extend(python_warnings)

        if args.ecosystem in {"all", "npm"}:
            npm_entries, npm_warnings = generate_npm_bypasses(args.base_ref, temp_dir)
            warnings.extend(npm_warnings)

    evidence = {
        "schema": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_ref": args.base_ref,
        "python": python_entries,
        "npm": npm_entries,
        "warnings": warnings,
    }
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Wrote security release-age bypass evidence to {args.output}")
    for ecosystem, entries in (("python", python_entries), ("npm", npm_entries)):
        if entries:
            print(f"{ecosystem} bypasses:")
            for entry in entries:
                advisories = ", ".join(entry["advisories_fixed"])
                print(f"- {entry['package']} {entry['from']} -> {entry['to']} fixing {advisories}")
        else:
            print(f"{ecosystem} bypasses: none")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
