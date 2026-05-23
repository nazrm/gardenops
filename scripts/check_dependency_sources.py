"""Validate dependency lockfile sources against the approved public registries."""

from __future__ import annotations

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


def _check_pypi_file(package: str, field: str, file_info: dict[str, Any], errors: list[str]) -> None:
    url = str(file_info.get("url", ""))
    file_hash = str(file_info.get("hash", ""))
    if not url.startswith(PYPI_FILES_PREFIX):
        errors.append(f"{package} {field} resolves outside PyPI files: {url or '<missing>'}")
    if not file_hash.startswith("sha256:"):
        errors.append(f"{package} {field} is missing a sha256 hash")


def check_uv_lock() -> list[str]:
    errors: list[str] = []
    lock_path = ROOT / "uv.lock"
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

        sdist = package_info.get("sdist")
        if isinstance(sdist, dict):
            _check_pypi_file(name, "sdist", sdist, errors)
        for wheel in package_info.get("wheels", []):
            if isinstance(wheel, dict):
                _check_pypi_file(name, "wheel", wheel, errors)

    return errors


def check_package_lock() -> list[str]:
    errors: list[str] = []
    lock_path = ROOT / "frontend" / "package-lock.json"
    lock_data = json.loads(lock_path.read_text())

    for package_path, package_info in lock_data.get("packages", {}).items():
        if package_path == "":
            continue
        resolved = package_info.get("resolved")
        if not isinstance(resolved, str) or not resolved.startswith(NPM_REGISTRY_PREFIX):
            errors.append(f"{package_path} resolves outside npm registry: {resolved or '<missing>'}")
        integrity = package_info.get("integrity")
        if not isinstance(integrity, str) or not integrity.startswith(("sha512-", "sha384-")):
            errors.append(f"{package_path} is missing npm integrity metadata")

    return errors


def main() -> None:
    errors = [*check_uv_lock(), *check_package_lock()]
    _fail(errors)
    print("Dependency lockfile sources are restricted to approved registries.")


if __name__ == "__main__":
    main()
