"""Reject changed Python packages that are newer than their cooldown tier."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROUTINE_COOLDOWN_DAYS = 3
MAJOR_OR_NEW_DIRECT_COOLDOWN_DAYS = 14
AI_SDK_COOLDOWN_DAYS = 1
TRUSTED_PYTHON_BYPASS_SOURCE = "pip-audit base/head diff"
REDUCED_COOLDOWN_PACKAGES = {"anthropic", "openai"}
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
VERSION_MAJOR_RE = re.compile(r"^(\d+)")


class SecurityBypassError(ValueError):
    pass


def _parse_upload_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _candidate_upload_times(package_info: dict[str, Any]) -> list[datetime]:
    upload_times: list[datetime] = []
    sdist = package_info.get("sdist")
    if isinstance(sdist, dict) and isinstance(sdist.get("upload-time"), str):
        upload_times.append(_parse_upload_time(sdist["upload-time"]))
    for wheel in package_info.get("wheels", []):
        if isinstance(wheel, dict) and isinstance(wheel.get("upload-time"), str):
            upload_times.append(_parse_upload_time(wheel["upload-time"]))
    return upload_times


def _normalize_python_name(name: str) -> str:
    return name.replace("_", "-").lower()


def _load_security_release_bypasses(root: Path = ROOT) -> dict[str, list[str]]:
    configured_path = os.environ.get("GARDENOPS_SECURITY_RELEASE_BYPASS", "").strip()
    path = (
        Path(configured_path)
        if configured_path
        else root / ".gardenops/security-release-bypass.json"
    )
    if configured_path:
        allow_override = os.environ.get(
            "GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not allow_override:
            raise SecurityBypassError(
                "external bypass file overrides require "
                "GARDENOPS_ALLOW_SECURITY_RELEASE_BYPASS_OVERRIDE=true"
            )
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SecurityBypassError(f"{path} is not valid JSON: {error}") from error

    if data.get("schema") != 1:
        raise SecurityBypassError(f"{path} field 'schema' must be 1")
    entries = data.get("python", [])
    if not isinstance(entries, list):
        raise SecurityBypassError(f"{path} field 'python' must be a list")

    bypasses: dict[str, list[str]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SecurityBypassError(f"{path} python[{index}] must be an object")

        package = entry.get("package")
        from_version = entry.get("from")
        to_version = entry.get("to")
        advisories = entry.get("advisories_fixed")
        source = entry.get("source")
        if not isinstance(package, str) or not package:
            raise SecurityBypassError(f"{path} python[{index}].package must be a non-empty string")
        if not isinstance(from_version, str) or not from_version:
            raise SecurityBypassError(f"{path} python[{index}].from must be a non-empty string")
        if not isinstance(to_version, str) or not to_version:
            raise SecurityBypassError(f"{path} python[{index}].to must be a non-empty string")
        if from_version == to_version:
            raise SecurityBypassError(f"{path} python[{index}] must change versions")
        if source != TRUSTED_PYTHON_BYPASS_SOURCE:
            raise SecurityBypassError(
                f"{path} python[{index}].source must be {TRUSTED_PYTHON_BYPASS_SOURCE!r}"
            )
        if (
            not isinstance(advisories, list)
            or not advisories
            or not all(isinstance(advisory, str) and advisory for advisory in advisories)
        ):
            raise SecurityBypassError(
                f"{path} python[{index}].advisories_fixed must be a non-empty string list"
            )

        key = f"{_normalize_python_name(package)}=={to_version}"
        bypasses[key] = sorted(set(advisories))

    return bypasses


def _lock_packages(root: Path) -> dict[str, dict[str, Any]]:
    lock_data = tomllib.loads((root / "uv.lock").read_text())
    packages: dict[str, dict[str, Any]] = {}
    for package_info in lock_data.get("package", []):
        name = _normalize_python_name(str(package_info.get("name", "<unknown>")))
        version = str(package_info.get("version", "<unknown>"))
        if name != "gardenops":
            packages[f"{name}=={version}"] = package_info
    return packages


def _direct_dependency_names(root: Path) -> set[str]:
    data = tomllib.loads((root / "pyproject.toml").read_text())
    project = data.get("project", {})
    requirements: list[object] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        if isinstance(group, list):
            requirements.extend(group)
    for group in data.get("dependency-groups", {}).values():
        if isinstance(group, list):
            requirements.extend(group)

    names: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, str):
            continue
        match = REQUIREMENT_NAME_RE.match(requirement)
        if match:
            names.add(_normalize_python_name(match.group(1)))
    return names


def _version_major(version: str) -> int | None:
    match = VERSION_MAJOR_RE.match(version)
    return int(match.group(1)) if match else None


def _package_versions(packages: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for key in packages:
        name, version = key.rsplit("==", 1)
        versions.setdefault(name, set()).add(version)
    return versions


def _cooldown_days(
    name: str,
    version: str,
    *,
    base_versions: dict[str, set[str]],
    base_direct: set[str],
    head_direct: set[str],
) -> tuple[int, str]:
    if name in REDUCED_COOLDOWN_PACKAGES and name in head_direct:
        return AI_SDK_COOLDOWN_DAYS, "AI SDK"
    if name in head_direct and name not in base_direct:
        return MAJOR_OR_NEW_DIRECT_COOLDOWN_DAYS, "new direct dependency"
    if name in head_direct:
        head_major = _version_major(version)
        prior_majors = {_version_major(item) for item in base_versions.get(name, set())}
        if head_major is not None and prior_majors and head_major not in prior_majors:
            return MAJOR_OR_NEW_DIRECT_COOLDOWN_DAYS, "major direct update"
    return ROUTINE_COOLDOWN_DAYS, "routine update"


def main(*, base_root: Path | None = None, head_root: Path | None = None) -> None:
    head_root = head_root or ROOT
    head_packages = _lock_packages(head_root)
    base_packages = _lock_packages(base_root) if base_root else {}
    base_direct = _direct_dependency_names(base_root) if base_root else set()
    head_direct = _direct_dependency_names(head_root)
    newly_direct = head_direct - base_direct
    packages_to_check = (
        {
            key: value
            for key, value in head_packages.items()
            if key not in base_packages or key.rsplit("==", 1)[0] in newly_direct
        }
        if base_root
        else head_packages
    )
    base_versions = _package_versions(base_packages)
    now = datetime.now(UTC)
    errors: list[str] = []
    allowed: list[str] = []
    try:
        security_bypasses = _load_security_release_bypasses(ROOT)
    except SecurityBypassError as error:
        print(f"python release-age check: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    for key, package_info in packages_to_check.items():
        name = _normalize_python_name(str(package_info.get("name", "<unknown>")))
        version = str(package_info.get("version", "<unknown>"))

        upload_times = _candidate_upload_times(package_info)
        if not upload_times:
            errors.append(f"{key} has no artifact upload-time metadata in uv.lock")
            continue

        bypass_advisories = security_bypasses.get(key)
        if bypass_advisories:
            allowed.append(f"{key} fixing {', '.join(bypass_advisories)}")
            continue

        cooldown_days, tier = _cooldown_days(
            name,
            version,
            base_versions=base_versions,
            base_direct=base_direct,
            head_direct=head_direct,
        )
        newest_upload = max(upload_times)
        if newest_upload <= now - timedelta(days=cooldown_days):
            continue

        errors.append(
            f"{key} newest artifact {newest_upload.isoformat()} is inside the "
            f"{cooldown_days}-day cooldown window ({tier})"
        )

    if errors:
        for error in errors:
            print(f"python release-age check: {error}", file=sys.stderr)
        raise SystemExit(1)

    if allowed:
        print("Allowed Python release-age exceptions:")
        for item in allowed:
            print(f"- {item}")
    print("Python locked packages satisfy the release-age policy.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", type=Path)
    parser.add_argument("--head-root", type=Path, default=ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(base_root=args.base_root, head_root=args.head_root)
