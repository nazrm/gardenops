#!/usr/bin/env python3
"""Fail CI when new raw HTML sinks are introduced in frontend code."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple

PATTERNS = ("innerHTML =", "innerHTML=", "outerHTML =", "outerHTML=", "insertAdjacentHTML(")

PLACEHOLDER_VALUES = {"", "TODO", "TBD", "REQUIRED"}


class AllowlistEntry(NamedTuple):
    sink: str
    review: str
    reason: str


def find_sinks(root: Path) -> list[str]:
    entries: list[str] = []
    for path in sorted(root.rglob("*.ts")):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        rel = path.relative_to(root)
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if any(pattern in raw for pattern in PATTERNS):
                entries.append(f"{rel}:{lineno}|{line}")
    return entries


def parse_allowlist_entry(raw: str, *, lineno: int) -> AllowlistEntry:
    parts = raw.rsplit("|", 2)
    if len(parts) != 3:
        raise ValueError(
            f"line {lineno}: expected '<sink>|review=<class>|reason=<note>' metadata format",
        )

    sink, review_field, reason_field = parts
    if not review_field.startswith("review="):
        raise ValueError(f"line {lineno}: missing review= metadata")
    if not reason_field.startswith("reason="):
        raise ValueError(f"line {lineno}: missing reason= metadata")

    review = review_field.split("=", 1)[1].strip()
    reason = reason_field.split("=", 1)[1].strip()
    if review.upper() in PLACEHOLDER_VALUES:
        raise ValueError(f"line {lineno}: review metadata must be explicit")
    if reason.upper() in PLACEHOLDER_VALUES:
        raise ValueError(f"line {lineno}: reason metadata must be explicit")

    return AllowlistEntry(sink=sink, review=review, reason=reason)


def load_allowlist(path: Path) -> tuple[dict[str, AllowlistEntry], list[str]]:
    if not path.exists():
        return {}, []
    items: dict[str, AllowlistEntry] = {}
    errors: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = parse_allowlist_entry(line, lineno=lineno)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        items[entry.sink] = entry
    return items, errors


def write_allowlist(path: Path, sinks: list[str]) -> None:
    existing, _ = load_allowlist(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "# Reviewed allowlist for raw HTML sinks.\n"
    content += "# Format: <sink>|review=<class>|reason=<note>\n"
    content += "\n".join(
        (
            f"{sink}|review={existing[sink].review}|reason={existing[sink].reason}"
            if sink in existing
            else f"{sink}|review=TODO|reason=TODO"
        )
        for sink in sinks
    )
    content += "\n"
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="frontend/src", help="Frontend source directory")
    parser.add_argument(
        "--allowlist",
        default="frontend/security/innerhtml_allowlist.txt",
        help="Allowlist file path",
    )
    parser.add_argument("--write", action="store_true", help="Write/refresh allowlist")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    allowlist_path = Path(args.allowlist)
    if not allowlist_path.is_absolute():
        allowlist_path = Path.cwd() / allowlist_path

    sinks = find_sinks(root)
    if args.write:
        write_allowlist(allowlist_path, sinks)
        print(f"Wrote allowlist with {len(sinks)} sinks: {allowlist_path}")
        return 0

    allowed, metadata_errors = load_allowlist(allowlist_path)
    current = set(sinks)
    allowlist_sinks = set(allowed)
    new_sinks = sorted(current - allowlist_sinks)
    stale = sorted(allowlist_sinks - current)

    if metadata_errors or new_sinks:
        print("Unsafe HTML sink check failed.")
        if metadata_errors:
            print("\nAllowlist metadata issues:")
            for item in metadata_errors:
                print(f"  ! {item}")
        if new_sinks:
            print("\nNew sinks detected:")
            for item in new_sinks:
                print(f"  + {item}")
        if stale:
            print("\nAllowlist entries no longer present (optional cleanup):")
            for item in stale[:20]:
                print(f"  - {item}")
        return 1

    print(
        "Unsafe HTML sink check passed. "
        f"{len(current)} sinks match the reviewed allowlist inventory."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
