"""Guards for complete-journey-only local fixture integrations."""

from __future__ import annotations

import os
import re
from pathlib import Path

_COMPLETE_JOURNEY_HEAD_RE = re.compile(r"[0-9a-f]{40}")


def complete_journey_loopback_fixture_enabled() -> bool:
    """Return whether a provider override is bound to the disposable journey child."""
    if (
        os.environ.get("APP_ENV") != "test"
        or os.environ.get("GARDENOPS_E2E_LOOPBACK_PROVIDER") != "1"
        or os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD") != "1"
        or os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE") != "1"
    ):
        return False

    database_url = os.environ.get("DATABASE_URL", "")
    issued_database_url = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_URL", "")
    system_identifier = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", "")
    marker = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "")
    expected_head = os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD", "")
    artifact_raw = os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR", "")
    if (
        not database_url
        or database_url != issued_database_url
        or not system_identifier.isdecimal()
        or not marker.startswith(f"{system_identifier}.")
        or _COMPLETE_JOURNEY_HEAD_RE.fullmatch(expected_head) is None
        or not artifact_raw
    ):
        return False
    try:
        requested_artifact_dir = Path(artifact_raw)
        if requested_artifact_dir.is_symlink():
            return False
        artifact_dir = requested_artifact_dir.resolve(strict=True)
    except OSError:
        return False
    return artifact_dir.is_dir()
