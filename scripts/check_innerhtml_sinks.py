#!/usr/bin/env python3
"""Fail CI when new raw HTML sinks are introduced in frontend code."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import NamedTuple

SINK_RE = re.compile(
    r"(?i)(?:"
    r"(?:\.\s*(?:innerHTML|outerHTML)|\[\s*['\"](?:innerHTML|outerHTML)['\"]\s*\])"
    r"\s*(?:=|\+=)"
    r"|"
    r"\binsertAdjacentHTML\s*\("
    r")",
)
SINK_SPAN_RE = re.compile(
    r"(?is)(?:"
    r"(?:\.\s*(?:innerHTML|outerHTML)|\[\s*['\"](?:innerHTML|outerHTML)['\"]\s*\])"
    r"\s*(?:=|\+=)"
    r"|"
    r"\binsertAdjacentHTML\s*\("
    r")",
)
REVIEWED_DYNAMIC_HELPER = "setReviewedDynamicHtml"
REVIEWED_DYNAMIC_HELPER_CALL_RE = re.compile(r"\bsetReviewedDynamicHtml\s*\(")
REVIEWED_DYNAMIC_HELPER_ALIAS_RE = re.compile(
    r"\bsetReviewedDynamicHtml\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)",
)

PLACEHOLDER_VALUES = {"", "TODO", "TBD", "REQUIRED"}


class AllowlistEntry(NamedTuple):
    sink: str
    review: str
    reason: str


def find_sinks(root: Path) -> list[str]:
    entries: list[str] = []
    for path in sorted([*root.rglob("*.ts"), *root.rglob("*.tsx")]):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        rel = path.relative_to(root)
        lines = path.read_text(encoding="utf-8").splitlines()
        seen_lines: set[int] = set()
        for lineno, raw in enumerate(lines, start=1):
            line = raw.strip()
            if SINK_RE.search(raw):
                entries.append(f"{rel}:{lineno}|{line}")
                seen_lines.add(lineno)
        text = "\n".join(lines)
        line_starts = [0]
        for match in re.finditer("\n", text):
            line_starts.append(match.end())
        for match in SINK_SPAN_RE.finditer(text):
            lineno = max(
                index + 1 for index, start in enumerate(line_starts) if start <= match.start()
            )
            if lineno in seen_lines:
                continue
            line = lines[lineno - 1].strip()
            entries.append(f"{rel}:{lineno}|{line}")
            seen_lines.add(lineno)
    return entries


def find_reviewed_dynamic_helpers(root: Path) -> list[str]:
    entries: list[str] = []
    for path in sorted([*root.rglob("*.ts"), *root.rglob("*.tsx")]):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        rel = path.relative_to(root)
        lines = path.read_text(encoding="utf-8").splitlines()
        text = "\n".join(lines)
        aliases = set(REVIEWED_DYNAMIC_HELPER_ALIAS_RE.findall(text))
        alias_call_re = (
            re.compile(r"\b(?:" + "|".join(re.escape(alias) for alias in aliases) + r")\s*\(")
            if aliases
            else None
        )
        for lineno, raw in enumerate(lines, start=1):
            line = raw.strip()
            if line.startswith(f"export function {REVIEWED_DYNAMIC_HELPER}"):
                continue
            if REVIEWED_DYNAMIC_HELPER_CALL_RE.search(raw) or (
                alias_call_re is not None and alias_call_re.search(raw)
            ):
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
    parser.add_argument(
        "--helper-allowlist",
        default="",
        help="Reviewed dynamic HTML helper allowlist path",
    )
    parser.add_argument("--write", action="store_true", help="Write/refresh allowlist")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    allowlist_path = Path(args.allowlist)
    if not allowlist_path.is_absolute():
        allowlist_path = Path.cwd() / allowlist_path
    helper_allowlist_path = (
        Path(args.helper_allowlist)
        if args.helper_allowlist
        else allowlist_path.with_name("reviewed_dynamic_html_allowlist.txt")
    )
    if not helper_allowlist_path.is_absolute():
        helper_allowlist_path = Path.cwd() / helper_allowlist_path

    sinks = find_sinks(root)
    reviewed_dynamic_helpers = find_reviewed_dynamic_helpers(root)
    if args.write:
        write_allowlist(allowlist_path, sinks)
        write_allowlist(helper_allowlist_path, reviewed_dynamic_helpers)
        print(f"Wrote allowlist with {len(sinks)} sinks: {allowlist_path}")
        print(
            "Wrote reviewed dynamic HTML helper allowlist with "
            f"{len(reviewed_dynamic_helpers)} calls: {helper_allowlist_path}"
        )
        return 0

    allowed, metadata_errors = load_allowlist(allowlist_path)
    helper_allowed, helper_metadata_errors = load_allowlist(helper_allowlist_path)
    current = set(sinks)
    allowlist_sinks = set(allowed)
    new_sinks = sorted(current - allowlist_sinks)
    stale = sorted(allowlist_sinks - current)
    current_helpers = set(reviewed_dynamic_helpers)
    allowlist_helpers = set(helper_allowed)
    new_helpers = sorted(current_helpers - allowlist_helpers)
    stale_helpers = sorted(allowlist_helpers - current_helpers)

    if metadata_errors or helper_metadata_errors or new_sinks or new_helpers:
        print("Unsafe HTML sink check failed.")
        if metadata_errors:
            print("\nAllowlist metadata issues:")
            for item in metadata_errors:
                print(f"  ! {item}")
        if helper_metadata_errors:
            print("\nReviewed dynamic HTML helper allowlist metadata issues:")
            for item in helper_metadata_errors:
                print(f"  ! {item}")
        if new_sinks:
            print("\nNew sinks detected:")
            for item in new_sinks:
                print(f"  + {item}")
        if new_helpers:
            print("\nNew reviewed dynamic HTML helper calls detected:")
            for item in new_helpers:
                print(f"  + {item}")
        if stale:
            print("\nAllowlist entries no longer present (optional cleanup):")
            for item in stale[:20]:
                print(f"  - {item}")
        if stale_helpers:
            print("\nReviewed dynamic HTML helper entries no longer present (optional cleanup):")
            for item in stale_helpers[:20]:
                print(f"  - {item}")
        return 1

    print(
        "Unsafe HTML sink check passed. "
        f"{len(current)} sinks and {len(current_helpers)} reviewed dynamic HTML "
        "helper calls match the allowlist inventories."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
