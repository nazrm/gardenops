#!/usr/bin/env python3
"""Fail CI when GitHub Actions workflow steps use mutable action refs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)")
PINNED_ACTION_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _workflow_files(root: Path) -> list[Path]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    return sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")])


def check_workflows(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path in _workflow_files(root):
        rel = path.relative_to(root)
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.match(raw)
            if not match:
                continue
            target = match.group(1).strip()
            if not PINNED_ACTION_RE.fullmatch(target):
                errors.append(
                    f"{rel}:{lineno} uses mutable action ref {target!r}; pin to a full SHA"
                )
    return errors


def main() -> int:
    errors = check_workflows(ROOT)
    if errors:
        for error in errors:
            print(f"github action pin check: {error}", file=sys.stderr)
        return 1
    print("GitHub Actions workflow uses: refs are pinned to full commit SHAs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
