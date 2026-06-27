#!/usr/bin/env python3
"""Validate that governed runtime env vars are documented."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_DOC_PATH = ROOT / "ENVIRONMENT_VARIABLES.md"
PYTHON_SOURCE_ROOTS = (ROOT / "gardenops",)
FRONTEND_SOURCE_ROOTS = (ROOT / "frontend" / "src",)
EXTRA_SOURCE_FILES = (
    ROOT / "scripts" / "security_ops_smoke.py",
    ROOT / "scripts" / "csp_smoke_check.cjs",
)
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
DOC_ENV_RE = re.compile(r"`([A-Z][A-Z0-9_<>]+)`")
JS_ENV_RE = re.compile(r"(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]*)")
JS_ENV_BRACKET_RE = re.compile(
    r"(?:process\.env|import\.meta\.env)\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]\s*\]"
)
JS_ENV_DESTRUCTURE_RE = re.compile(r"\{(?P<names>[^}]+)\}\s*=\s*(?:process\.env|import\.meta\.env)")
EXACT_ENV_NAMES = {"SHADEMAP"}
IGNORED_ENV_NAMES = {"CURRENT_DATE", "HOSTNAME"}
RATE_LIMIT_BUCKET_PLACEHOLDER = "RATE_LIMIT_GLOBAL_LIMIT_<BUCKET>"


def _python_source_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_SOURCE_ROOTS:
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
    for path in EXTRA_SOURCE_FILES:
        if path.suffix == ".py" and path.exists():
            files.append(path)
    return files


def _js_source_files() -> list[Path]:
    files = [
        path
        for path in EXTRA_SOURCE_FILES
        if path.suffix in {".cjs", ".js", ".mjs", ".ts"} and path.exists()
    ]
    for root in FRONTEND_SOURCE_ROOTS:
        if root.exists():
            files.extend(
                sorted(
                    path
                    for path in root.rglob("*")
                    if path.suffix in {".cjs", ".js", ".mjs", ".ts", ".tsx"}
                    and "node_modules" not in path.parts
                    and "dist" not in path.parts
                )
            )
    return files


def _record(mapping: dict[str, set[str]], name: str, rel_path: str) -> None:
    if name in IGNORED_ENV_NAMES:
        return
    if name in EXACT_ENV_NAMES or ENV_NAME_RE.fullmatch(name):
        mapping.setdefault(name, set()).add(rel_path)


class _EnvStringVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, mapping: dict[str, set[str]]) -> None:
        self._rel_path = rel_path
        self._mapping = mapping

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str):
            _record(self._mapping, node.value.strip(), self._rel_path)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:  # noqa: N802
        literal_parts: list[str] = []
        saw_dynamic = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literal_parts.append(value.value)
            else:
                saw_dynamic = True
        if saw_dynamic and "".join(literal_parts).startswith("RATE_LIMIT_GLOBAL_LIMIT_"):
            _record(self._mapping, RATE_LIMIT_BUCKET_PLACEHOLDER, self._rel_path)
        self.generic_visit(node)


def _scan_python_files() -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for path in _python_source_files():
        rel_path = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        visitor = _EnvStringVisitor(rel_path, mapping)
        visitor.visit(tree)
    return mapping


def _scan_js_files(mapping: dict[str, set[str]]) -> dict[str, set[str]]:
    for path in _js_source_files():
        rel_path = str(path.relative_to(ROOT))
        text = path.read_text(encoding="utf-8")
        for match in JS_ENV_RE.findall(text):
            _record(mapping, match.strip(), rel_path)
        for match in JS_ENV_BRACKET_RE.findall(text):
            _record(mapping, match.strip(), rel_path)
        for match in JS_ENV_DESTRUCTURE_RE.finditer(text):
            for raw_name in match.group("names").split(","):
                name = raw_name.strip().split(":", 1)[0].strip()
                _record(mapping, name, rel_path)
    return mapping


def _doc_tokens() -> tuple[set[str], list[re.Pattern[str]]]:
    text = ENV_DOC_PATH.read_text(encoding="utf-8")
    exact: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    for token in DOC_ENV_RE.findall(text):
        if "<" in token and ">" in token:
            pattern = "^" + re.sub(r"<[^>]+>", r"[A-Z0-9_]+", re.escape(token)) + "$"
            patterns.append(re.compile(pattern))
        else:
            exact.add(token)
    return exact, patterns


def _documented(name: str, exact: set[str], patterns: list[re.Pattern[str]]) -> bool:
    if name in exact:
        return True
    return any(pattern.fullmatch(name) for pattern in patterns)


def main() -> int:
    if not ENV_DOC_PATH.exists():
        print(f"Missing env docs: {ENV_DOC_PATH}", file=sys.stderr)
        return 1

    used = _scan_python_files()
    _scan_js_files(used)
    exact, patterns = _doc_tokens()

    missing = sorted(name for name in used if not _documented(name, exact, patterns))
    if missing:
        for name in missing:
            sources = ", ".join(sorted(used.get(name, ())))
            print(
                f"ENVIRONMENT_VARIABLES.md missing {name} (used in {sources})",
                file=sys.stderr,
            )
        return 1

    print(f"Environment-variable docs cover {len(used)} discovered env vars.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
