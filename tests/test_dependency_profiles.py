"""The production core must not import optional feature/development packages."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gardenops.platform_secrets import ConfigurationError
from gardenops.provider_settings import shademap_enabled

ROOT = Path(__file__).resolve().parents[1]


def test_core_import_does_not_need_optional_packages() -> None:
    code = """
import importlib.abc
import sys
blocked = {'numpy', 'laspy', 'lazrs', 'pyproj', 'dotenv', 'yaml',
           'httptools', 'uvloop', 'watchfiles', 'websockets'}
class NoOptionalPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ImportError('Unexpected optional dependency: ' + fullname)
sys.meta_path.insert(0, NoOptionalPackages())
import gardenops.main
from gardenops.routers.shademap import get_shademap_state
from gardenops.services.lidar_terrain import uploaded_terrain_storage_keys
assert callable(get_shademap_state)
assert callable(uploaded_terrain_storage_keys)
assert not blocked.intersection(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "APP_ENV": "test",
            "AUTH_REQUIRED": "false",
            "RATE_LIMIT_BACKEND": "memory",
            "SHADEMAP_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_explicit_shademap_enable_requires_optional_extra() -> None:
    with (
        patch.dict(os.environ, {"SHADEMAP_ENABLED": "true"}),
        patch("gardenops.provider_settings.find_spec", return_value=None),
        pytest.raises(ConfigurationError, match="--extra terrain"),
    ):
        shademap_enabled()


def test_default_shademap_is_off_without_terrain_extra() -> None:
    with (
        patch.dict(os.environ, {"SHADEMAP_ENABLED": ""}),
        patch("gardenops.provider_settings.find_spec", return_value=None),
    ):
        assert not shademap_enabled()
