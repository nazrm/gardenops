"""Validate dependency lockfile sources against the approved public registries."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPI_REGISTRY = "https://pypi.org/simple"
PYPI_FILES_PREFIX = "https://files.pythonhosted.org/packages/"
NPM_REGISTRY_PREFIX = "https://registry.npmjs.org/"


def _fail(errors: list[str]) -> None:
    if not errors:
        return
    for error in errors:
        print(f"dependency source check: {error}", file=sys.stderr)
    raise SystemExit(1)


def _check_pypi_file(
    package: str,
    field: str,
    file_info: dict[str, Any],
    errors: list[str],
) -> None:
    url = str(file_info.get("url", ""))
    file_hash = str(file_info.get("hash", ""))
    if not url.startswith(PYPI_FILES_PREFIX):
        errors.append(f"{package} {field} resolves outside PyPI files: {url or '<missing>'}")
    if not file_hash.startswith("sha256:"):
        errors.append(f"{package} {field} is missing a sha256 hash")


def _is_hashed_pypi_file(file_info: dict[str, Any]) -> bool:
    return str(file_info.get("url", "")).startswith(PYPI_FILES_PREFIX) and str(
        file_info.get("hash", "")
    ).startswith("sha256:")


def check_uv_lock(root: Path | None = None) -> list[str]:
    errors: list[str] = []
    effective_root = root or ROOT
    lock_path = effective_root / "uv.lock"
    lock_data = tomllib.loads(lock_path.read_text())
    packages = lock_data.get("package", [])

    for package_info in packages:
        name = package_info.get("name", "<unknown>")
        source = package_info.get("source", {})
        if source == {"editable": "."} and name == "gardenops":
            continue
        if source.get("registry") != PYPI_REGISTRY:
            errors.append(f"{name} uses unsupported Python source: {source}")
            continue

        hashed_artifact_count = 0
        sdist = package_info.get("sdist")
        if isinstance(sdist, dict):
            _check_pypi_file(name, "sdist", sdist, errors)
            if _is_hashed_pypi_file(sdist):
                hashed_artifact_count += 1
        for wheel in package_info.get("wheels", []):
            if isinstance(wheel, dict):
                _check_pypi_file(name, "wheel", wheel, errors)
                if _is_hashed_pypi_file(wheel):
                    hashed_artifact_count += 1
        if hashed_artifact_count == 0:
            errors.append(f"{name} has no hashed PyPI artifact metadata")

    return errors


def check_package_lock(root: Path | None = None) -> list[str]:
    errors: list[str] = []
    effective_root = root or ROOT
    lock_path = effective_root / "frontend" / "package-lock.json"
    lock_data = json.loads(lock_path.read_text())

    lockfile_version = lock_data.get("lockfileVersion")
    packages = lock_data.get("packages")
    if lockfile_version not in (2, 3):
        errors.append(
            "frontend/package-lock.json must use lockfileVersion 2 or 3 "
            f"to expose per-package source metadata; found {lockfile_version or '<missing>'}"
        )
        return errors
    if not isinstance(packages, dict) or not packages:
        errors.append("frontend/package-lock.json is missing npm packages metadata")
        return errors

    dependency_packages = [
        (package_path, package_info)
        for package_path, package_info in packages.items()
        if package_path != ""
    ]
    if not dependency_packages:
        errors.append("frontend/package-lock.json does not contain npm dependency package entries")
        return errors

    for package_path, package_info in dependency_packages:
        if not isinstance(package_info, dict):
            errors.append(f"{package_path} has invalid npm package metadata")
            continue
        resolved = package_info.get("resolved")
        if not isinstance(resolved, str) or not resolved.startswith(NPM_REGISTRY_PREFIX):
            errors.append(
                f"{package_path} resolves outside npm registry: {resolved or '<missing>'}"
            )
        integrity = package_info.get("integrity")
        if not isinstance(integrity, str) or not integrity.startswith(("sha512-", "sha384-")):
            errors.append(f"{package_path} is missing npm integrity metadata")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root containing uv.lock and frontend/package-lock.json",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    errors = [*check_uv_lock(root), *check_package_lock(root)]
    _fail(errors)
    print("Dependency lockfile sources are restricted to approved registries.")


if __name__ == "__main__":
    main()
