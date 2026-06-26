#!/usr/bin/env python3
"""Check GardenOps outbound git changes before add, commit, push, or PR."""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import math
import re
import subprocess
import sys
from pathlib import Path

MAX_TEXT_BYTES = 512_000
MAX_FILE_BYTES = 1_500_000

BLOCKED_GLOBS = (
    ".env",
    ".env.*",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.sqlite",
    "*.sqlite3",
    "*.pgdump",
    "*.dump",
    "*.bak",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "*.log",
    "*.tar",
    "*.tgz",
    "*.zip",
    "*.gz",
    "*.7z",
    "*.laz",
    "*.las",
    "*.tif",
    "*.tiff",
    "*.mbtiles",
    "logs/**",
    "backups/**",
    "media_uploads/**",
    "output/**",
    ".gardenops/**",
    ".codex/**",
    ".venv/**",
    "venv/**",
    "ENV/**",
    "env/**",
    "node_modules/**",
    "frontend/dist/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
)

ALLOWED_GLOBS = (
    ".env.example",
    ".env.test.example",
)

IGNORE_SENTINELS = (
    ".env",
    ".env.local",
    "backup.pgdump",
    "logs/runtime.log",
    "backups/latest-scheduled.pgdump",
    "media_uploads/private.jpg",
    "output/report.txt",
    ".gardenops/state.json",
    ".codex/local/SKILL.md",
    "frontend/dist/assets/app.js",
)

HARD_SECRET_PATTERNS = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PRIVATE )?PRIVATE KEY-----")),
    ("OPENAI_KEY", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b")),
    ("GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("BEARER_TOKEN", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE)),
    (
        "DATABASE_URL",
        re.compile(
            r"\b(?:postgresql|postgres|mysql|redis)://[^/\s:@]+:[^@\s]+@[^/\s]+", re.IGNORECASE
        ),
    ),
)

SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>api[_-]?key|secret|token|password|cookie|session[_-]?cookie|bearer)\b\s*[:=]\s*(?P<value>.+)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_SUPPRESSION_RE = re.compile(
    r"push-sanitizer:\s*allow\s+SECRET_ASSIGNMENT\b",
    re.IGNORECASE,
)
CODE_REFERENCE_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:(?:\.[A-Za-z_$][A-Za-z0-9_$]*)|(?:\[[^\]\n]+\]))*$"
)
FUNCTION_CALL_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.]*\([^)\n]*\)$")

SAFE_EXAMPLE_RE = re.compile(
    r"(change-me|changeme|placeholder|dummy|unset|disabled|<[^>]+>|\.\.\.)",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str
    surface: str


def run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return completed


def git_lines(args: list[str], *, check: bool = True) -> list[str]:
    completed = run_git(args, check=check)
    text = completed.stdout.decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line]


def normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_allowed_path(path: str) -> bool:
    rel = normalize(path)
    return any(fnmatch.fnmatchcase(rel, pattern) for pattern in ALLOWED_GLOBS)


def is_blocked_path(path: str) -> str | None:
    rel = normalize(path)
    if is_allowed_path(rel):
        return None
    for pattern in BLOCKED_GLOBS:
        if fnmatch.fnmatchcase(rel, pattern):
            return pattern
    return None


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def read_worktree_file(root: Path, path: str) -> bytes | None:
    full = root / normalize(path)
    if not full.exists() or not full.is_file():
        return None
    try:
        return full.read_bytes()
    except OSError:
        return None


def read_git_blob(refspec: str) -> bytes | None:
    completed = run_git(["show", refspec], check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout


def assignment_suppressed(line: str) -> bool:
    return bool(SECRET_ASSIGNMENT_SUPPRESSION_RE.search(line))


def extract_assignment_value(raw_value: str) -> tuple[str, bool]:
    value = raw_value.strip()
    if not value:
        return "", False
    if value[0] in {"'", '"'}:
        quote = value[0]
        chars: list[str] = []
        escaped = False
        for char in value[1:]:
            if escaped:
                chars.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                return "".join(chars), True
            chars.append(char)
        return "".join(chars), True
    value = value.split("#", 1)[0].strip().rstrip(",;")
    return value, False


def looks_like_code_reference(value: str) -> bool:
    if FUNCTION_CALL_RE.fullmatch(value):
        return True
    if "." in value or "[" in value:
        return bool(CODE_REFERENCE_RE.fullmatch(value))
    if CODE_REFERENCE_RE.fullmatch(value):
        return not any(char.isdigit() for char in value)
    return False


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum(
        (value.count(char) / len(value)) * math.log2(value.count(char) / len(value))
        for char in set(value)
    )


def character_class_count(value: str) -> int:
    classes = 0
    classes += any(char.islower() for char in value)
    classes += any(char.isupper() for char in value)
    classes += any(char.isdigit() for char in value)
    classes += any(not char.isalnum() for char in value)
    return classes


def looks_like_secret_literal(value: str, *, quoted: bool) -> bool:
    normalized = value.strip()
    if len(normalized) < 20:
        return False
    if not quoted and looks_like_code_reference(normalized):
        return False
    classes = character_class_count(normalized)
    entropy = shannon_entropy(normalized)
    if len(normalized) >= 32 and classes >= 3 and entropy >= 3.8:
        return True
    if len(normalized) >= 24 and classes >= 4 and entropy >= 3.8:
        return True
    return len(normalized) >= 20 and classes >= 3 and entropy >= 4.0


def secret_pattern_details_for_line(line: str) -> list[str]:
    details: list[str] = []
    for name, pattern in HARD_SECRET_PATTERNS:
        if pattern.search(line):
            details.append(name)
            break
    if not assignment_suppressed(line):
        assignment = SECRET_ASSIGNMENT_RE.search(line)
        if assignment:
            value, quoted = extract_assignment_value(assignment.group("value"))
            if looks_like_secret_literal(value, quoted=quoted):
                details.append("SECRET_ASSIGNMENT")
    return details


def find_secret_patterns(data: bytes, path: str, surface: str) -> list[Finding]:
    if not data or is_binary(data):
        return []
    sample = data[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    findings: list[Finding] = []
    for line_no, line in enumerate(sample.splitlines(), start=1):
        if SAFE_EXAMPLE_RE.search(line):
            continue
        for name in secret_pattern_details_for_line(line):
            findings.append(
                Finding(
                    code="SECRET_PATTERN",
                    path=path,
                    detail=f"{name} at line {line_no}",
                    surface=surface,
                )
            )
    return findings


def scan_path_and_content(path: str, data: bytes | None, surface: str) -> list[Finding]:
    rel = normalize(path)
    findings: list[Finding] = []
    blocked = is_blocked_path(rel)
    if blocked:
        findings.append(Finding("BLOCKED_PATH", rel, f"matches {blocked}", surface))
    if data is None:
        return findings
    if len(data) > MAX_FILE_BYTES:
        findings.append(Finding("LARGE_FILE", rel, f"{len(data)} bytes", surface))
    if is_binary(data) and not is_allowed_path(rel):
        findings.append(Finding("BINARY_FILE", rel, "binary file needs explicit review", surface))
    findings.extend(find_secret_patterns(data, rel, surface))
    return findings


def staged_files() -> list[str]:
    return git_lines(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])


def unstaged_files() -> list[str]:
    return git_lines(["diff", "--name-only", "--diff-filter=ACMR"])


def untracked_files() -> list[str]:
    return git_lines(["ls-files", "--others", "--exclude-standard"])


def scan_pre_add(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(set(unstaged_files() + untracked_files())):
        data = read_worktree_file(root, path)
        path_findings = scan_path_and_content(path, data, "pre-add")
        for finding in path_findings:
            if finding.code == "BLOCKED_PATH" and path in untracked_files():
                findings.append(
                    Finding("UNTRACKED_SENSITIVE", finding.path, finding.detail, finding.surface)
                )
            else:
                findings.append(finding)
    return findings


def scan_pre_commit() -> list[Finding]:
    findings: list[Finding] = []
    for path in staged_files():
        data = read_git_blob(f":{path}")
        findings.extend(scan_path_and_content(path, data, "pre-commit"))
    findings.extend(scan_tracked_ignored())
    return findings


def upstream_ref() -> str | None:
    completed = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], check=False
    )
    if completed.returncode == 0:
        value = completed.stdout.decode("utf-8", errors="replace").strip()
        return value or None
    for candidate in ("origin/main", "origin/master"):
        if run_git(["rev-parse", "--verify", candidate], check=False).returncode == 0:
            return candidate
    return None


def commit_paths(commit: str) -> list[str]:
    return git_lines(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "--diff-filter=ACMR", commit]
    )


def scan_commit_patch(commit: str) -> list[Finding]:
    data = read_git_blob(f"{commit} --")
    if not data:
        return []
    text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    findings: list[Finding] = []
    current_path = "<patch>"
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("+++ b/"):
            current_path = normalize(line[6:])
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if SAFE_EXAMPLE_RE.search(line):
            continue
        for name in secret_pattern_details_for_line(line):
            findings.append(
                Finding(
                    "SECRET_PATTERN",
                    current_path,
                    f"{name} in commit {commit[:12]} patch line {line_no}",
                    "pre-push",
                )
            )
    return findings


def scan_pre_push() -> list[Finding]:
    upstream = upstream_ref()
    if not upstream:
        return [
            Finding(
                "NO_UPSTREAM",
                "<branch>",
                "no upstream or origin/main found for pre-push comparison",
                "pre-push",
            )
        ]
    commits = git_lines(["rev-list", f"{upstream}..HEAD"])
    findings: list[Finding] = []
    for commit in commits:
        for path in commit_paths(commit):
            data = read_git_blob(f"{commit}:{path}")
            findings.extend(scan_path_and_content(path, data, f"pre-push:{commit[:12]}"))
        findings.extend(scan_commit_patch(commit))
    return findings


def scan_tracked_ignored() -> list[Finding]:
    findings: list[Finding] = []
    tracked_ignored = git_lines(["ls-files", "-ci", "--exclude-standard"], check=False)
    for path in tracked_ignored:
        if is_blocked_path(path):
            findings.append(
                Finding(
                    "TRACKED_IGNORED_PATH",
                    normalize(path),
                    "tracked file matches ignore policy",
                    "index",
                )
            )
    return findings


def scan_ignore_policy() -> list[Finding]:
    findings: list[Finding] = []
    for path in IGNORE_SENTINELS:
        completed = run_git(["check-ignore", "-q", "--", path], check=False)
        if completed.returncode != 0:
            findings.append(
                Finding("IGNORE_GAP", path, "sensitive sentinel is not ignored", "ignore-policy")
            )
    return findings


def print_findings(findings: list[Finding]) -> None:
    if not findings:
        print("No blocking push-sanitizer findings.")
        return
    print("Blocking push-sanitizer findings:")
    for finding in findings:
        print(f"- BLOCK {finding.code} {finding.path} [{finding.surface}]: {finding.detail}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pre-add", action="store_true", help="scan unstaged and untracked files")
    parser.add_argument("--pre-commit", action="store_true", help="scan staged files")
    parser.add_argument("--pre-push", action="store_true", help="scan commits ahead of upstream")
    parser.add_argument(
        "--skip-ignore-policy", action="store_true", help="skip .gitignore sentinel checks"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path.cwd()
    try:
        run_git(["rev-parse", "--show-toplevel"])
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    selected = args.pre_add or args.pre_commit or args.pre_push
    findings: list[Finding] = []
    try:
        if args.pre_add or not selected:
            findings.extend(scan_pre_add(root))
        if args.pre_commit or not selected:
            findings.extend(scan_pre_commit())
        if args.pre_push or not selected:
            findings.extend(scan_pre_push())
        if not args.skip_ignore_policy:
            findings.extend(scan_ignore_policy())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    unique = list(dict.fromkeys(findings))
    print_findings(unique)
    return 1 if unique else 0


if __name__ == "__main__":
    raise SystemExit(main())
