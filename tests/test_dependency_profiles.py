"""The production core must not import optional feature/development packages."""

import os
import subprocess
import sys
from pathlib import Path

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
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
