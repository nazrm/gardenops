#!/usr/bin/env python3
"""Validate GitHub Action identities, immutable refs, and changed-ref age."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)")
PINNED_ACTION_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
ACTION_COOLDOWN_DAYS = 7
APPROVED_ACTIONS = {
    "actions/checkout",
    "actions/setup-node",
    "actions/setup-python",
    "actions/upload-artifact",
    "astral-sh/setup-uv",
}


def _workflow_files(root: Path) -> list[Path]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    return sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")])


def _workflow_actions(root: Path) -> dict[str, list[tuple[Path, int]]]:
    actions: dict[str, list[tuple[Path, int]]] = {}
    for path in _workflow_files(root):
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.match(raw)
            if match:
                actions.setdefault(match.group(1).strip(), []).append((path, lineno))
    return actions


def check_workflows(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for target, locations in _workflow_actions(root).items():
        for path, lineno in locations:
            rel = path.relative_to(root)
            if not PINNED_ACTION_RE.fullmatch(target):
                errors.append(
                    f"{rel}:{lineno} uses mutable action ref {target!r}; pin to a full SHA"
                )
                continue
            action_name = target.rsplit("@", 1)[0]
            if action_name not in APPROVED_ACTIONS:
                errors.append(
                    f"{rel}:{lineno} uses unapproved action {action_name!r}; "
                    "add it to the reviewed allowlist first"
                )
    return errors


def _github_commit_time(action_name: str, sha: str, token: str = "") -> datetime:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{action_name}/commits/{sha}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "gardenops-dependency-policy",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        message = f"could not read commit metadata for {action_name}@{sha}: {error}"
        raise RuntimeError(message) from error

    value = data.get("commit", {}).get("committer", {}).get("date")
    if not isinstance(value, str):
        raise RuntimeError(f"GitHub returned no commit timestamp for {action_name}@{sha}")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def check_changed_action_age(
    base_root: Path,
    head_root: Path,
    *,
    now: datetime | None = None,
    token: str = "",
) -> list[str]:
    now = now or datetime.now(UTC)
    base_targets = set(_workflow_actions(base_root))
    head_targets = set(_workflow_actions(head_root))
    errors: list[str] = []
    for target in sorted(head_targets - base_targets):
        if not PINNED_ACTION_RE.fullmatch(target):
            continue
        action_name, sha = target.rsplit("@", 1)
        if action_name not in APPROVED_ACTIONS:
            continue
        committed_at = _github_commit_time(action_name, sha, token)
        if committed_at > now - timedelta(days=ACTION_COOLDOWN_DAYS):
            errors.append(
                f"{target} commit timestamp {committed_at.isoformat()} is inside the "
                f"{ACTION_COOLDOWN_DAYS}-day cooldown window"
            )
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", type=Path)
    parser.add_argument("--head-root", type=Path, default=ROOT)
    parser.add_argument("--check-age", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = check_workflows(args.head_root)
    if args.check_age:
        if args.base_root is None:
            print("github action pin check: --check-age requires --base-root", file=sys.stderr)
            return 2
        try:
            errors.extend(
                check_changed_action_age(
                    args.base_root,
                    args.head_root,
                    token=os.environ.get("GITHUB_TOKEN", ""),
                )
            )
        except RuntimeError as error:
            errors.append(str(error))
    if errors:
        for error in errors:
            print(f"github action pin check: {error}", file=sys.stderr)
        return 1
    print("GitHub Actions workflow uses: refs are pinned to full commit SHAs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
