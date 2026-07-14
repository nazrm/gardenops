#!/usr/bin/env python3
"""Validate the minimal integrity contract for a Playwright trace archive."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def validate_trace(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("trace must be a regular file")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for required in ("trace.trace", "trace.network"):
            if required not in names:
                raise ValueError(f"trace archive is missing {required}")
            info = archive.getinfo(required)
            if info.file_size <= 0:
                raise ValueError(f"trace archive is missing non-empty {required}")
        corrupt = archive.testzip()
        if corrupt is not None:
            raise ValueError(f"trace archive contains a corrupt member: {corrupt}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_playwright_trace.py TRACE.zip", file=sys.stderr)
        return 2
    try:
        validate_trace(Path(sys.argv[1]))
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        print(f"invalid Playwright trace: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
