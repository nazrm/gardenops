"""Tests for guards around complete-journey-only local integrations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from gardenops.e2e_fixture import complete_journey_loopback_fixture_enabled


def _fixture_env(artifact_dir: Path) -> dict[str, str]:
    database_url = "postgresql://gardenops-test@127.0.0.1:19452/gardenops_test"
    return {
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": str(artifact_dir),
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD": "a" * 40,
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "123.fixture",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": database_url,
        "GARDENOPS_E2E_LOOPBACK_PROVIDER": "1",
    }


def test_complete_journey_fixture_requires_all_runner_markers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = _fixture_env(Path(tmp))
        with patch.dict(os.environ, env, clear=True):
            assert complete_journey_loopback_fixture_enabled() is True
        with patch.dict(
            os.environ,
            {**env, "GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD": "not-a-commit"},
            clear=True,
        ):
            assert complete_journey_loopback_fixture_enabled() is False


def test_complete_journey_fixture_rejects_a_symlinked_artifact_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "artifact"
        artifact.mkdir()
        link = root / "artifact-link"
        link.symlink_to(artifact, target_is_directory=True)
        with patch.dict(os.environ, _fixture_env(link), clear=True):
            assert complete_journey_loopback_fixture_enabled() is False
