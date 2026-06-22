"""Reject Python lockfile packages that are newer than the cooldown window."""

from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COOLDOWN_DAYS = 7
SECURITY_BYPASS_PATH = ROOT / ".gardenops" / "security-release-bypass.json"

# These packages were already locked inside the cooldown window when the
# dependency policy was introduced. The dates are the point where the locked
# artifact has aged out of the cooldown window; remove entries after they expire.
# Security fixes may also use this narrow path when the PR documents the bypass.
TEMPORARY_EXCEPTIONS = {
    "anthropic==0.102.0": datetime(2026, 5, 27, 18, 12, 44, tzinfo=UTC),
    "idna==3.15": datetime(2026, 5, 26, 22, 45, 58, tzinfo=UTC),
    "msgpack==1.2.1": datetime(2026, 6, 25, 16, 13, 53, tzinfo=UTC),
    "openai==2.37.0": datetime(2026, 5, 29, 22, 30, 36, tzinfo=UTC),
    "pip==26.1.2": datetime(2026, 6, 14, 17, 33, 59, tzinfo=UTC),
    "starlette==1.0.1": datetime(2026, 6, 4, 21, 58, 59, tzinfo=UTC),
    "starlette==1.3.1": datetime(2026, 6, 19, 9, 23, 12, tzinfo=UTC),
    "uvicorn==0.47.0": datetime(2026, 5, 28, 18, 16, 55, tzinfo=UTC),
}


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


def _load_security_release_bypasses() -> dict[str, list[str]]:
    path = Path(os.environ.get("GARDENOPS_SECURITY_RELEASE_BYPASS", SECURITY_BYPASS_PATH))
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SecurityBypassError(f"{path} is not valid JSON: {error}") from error

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
        if not isinstance(package, str) or not package:
            raise SecurityBypassError(f"{path} python[{index}].package must be a non-empty string")
        if not isinstance(from_version, str) or not from_version:
            raise SecurityBypassError(f"{path} python[{index}].from must be a non-empty string")
        if not isinstance(to_version, str) or not to_version:
            raise SecurityBypassError(f"{path} python[{index}].to must be a non-empty string")
        if from_version == to_version:
            raise SecurityBypassError(f"{path} python[{index}] must change versions")
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


def main() -> None:
    lock_data = tomllib.loads((ROOT / "uv.lock").read_text())
    cutoff = datetime.now(UTC) - timedelta(days=COOLDOWN_DAYS)
    errors: list[str] = []
    allowed: list[str] = []
    security_bypasses = _load_security_release_bypasses()

    for package_info in lock_data.get("package", []):
        name = _normalize_python_name(str(package_info.get("name", "<unknown>")))
        version = str(package_info.get("version", "<unknown>"))
        key = f"{name}=={version}"
        if name == "gardenops":
            continue

        upload_times = _candidate_upload_times(package_info)
        if not upload_times:
            errors.append(f"{key} has no artifact upload-time metadata in uv.lock")
            continue

        newest_upload = max(upload_times)
        if newest_upload <= cutoff:
            continue

        exception_until = TEMPORARY_EXCEPTIONS.get(key)
        if exception_until and datetime.now(UTC) < exception_until:
            allowed.append(f"{key} until {exception_until.isoformat()}")
            continue

        bypass_advisories = security_bypasses.get(key)
        if bypass_advisories:
            allowed.append(f"{key} fixing {', '.join(bypass_advisories)}")
            continue

        errors.append(
            f"{key} newest artifact {newest_upload.isoformat()} is inside the "
            f"{COOLDOWN_DAYS}-day cooldown window"
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


if __name__ == "__main__":
    main()
