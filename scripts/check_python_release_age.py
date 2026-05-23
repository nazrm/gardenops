"""Reject Python lockfile packages that are newer than the cooldown window."""

from __future__ import annotations

import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COOLDOWN_DAYS = 14

# These packages were already locked inside the cooldown window when the
# dependency policy was introduced. The dates are the point where the locked
# artifact has aged out of the 14-day window; remove entries after they expire.
TEMPORARY_EXCEPTIONS = {
    "anthropic": datetime(2026, 5, 27, 18, 12, 44, tzinfo=UTC),
    "idna": datetime(2026, 5, 26, 22, 45, 58, tzinfo=UTC),
    "openai": datetime(2026, 5, 29, 22, 30, 36, tzinfo=UTC),
    "starlette": datetime(2026, 6, 4, 21, 58, 59, tzinfo=UTC),
    "uvicorn": datetime(2026, 5, 28, 18, 16, 55, tzinfo=UTC),
}


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


def main() -> None:
    lock_data = tomllib.loads((ROOT / "uv.lock").read_text())
    cutoff = datetime.now(UTC) - timedelta(days=COOLDOWN_DAYS)
    errors: list[str] = []
    allowed: list[str] = []

    for package_info in lock_data.get("package", []):
        name = str(package_info.get("name", "<unknown>"))
        if name == "gardenops":
            continue

        upload_times = _candidate_upload_times(package_info)
        if not upload_times:
            errors.append(f"{name} has no artifact upload-time metadata in uv.lock")
            continue

        newest_upload = max(upload_times)
        if newest_upload <= cutoff:
            continue

        exception_until = TEMPORARY_EXCEPTIONS.get(name)
        if exception_until and datetime.now(UTC) < exception_until:
            allowed.append(f"{name} until {exception_until.isoformat()}")
            continue

        errors.append(
            f"{name} newest artifact {newest_upload.isoformat()} is inside the "
            f"{COOLDOWN_DAYS}-day cooldown window"
        )

    if errors:
        for error in errors:
            print(f"python release-age check: {error}", file=sys.stderr)
        raise SystemExit(1)

    if allowed:
        print("Temporary Python release-age exceptions:")
        for item in allowed:
            print(f"- {item}")
    print("Python locked packages satisfy the release-age policy.")


if __name__ == "__main__":
    main()
