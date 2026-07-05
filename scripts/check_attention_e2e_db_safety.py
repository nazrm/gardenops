#!/usr/bin/env python3

from __future__ import annotations

import ast
import os
from pathlib import Path

from gardenops.services.attention import require_attention_e2e_database

_TOP_LEVEL_DEFINITION_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import,
    ast.ImportFrom,
)
_DB_TOUCH_FUNCTION_NAMES = {
    "create_user",
    "truncate_public_tables",
}
_DB_TOUCH_METHOD_NAMES = {
    "commit",
    "connect",
    "ensure_default_garden",
    "execute",
    "get_db",
    "run_migrations",
}


class SeedSafetyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.setdefault_lines: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "setdefault"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
        ):
            self.setdefault_lines.append(node.lineno)
        self.generic_visit(node)


def _iter_calls_outside_nested_defs(node: ast.AST):
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child,
            (
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.FunctionDef,
                ast.Lambda,
            ),
        ):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _iter_calls_outside_nested_defs(child)


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_guard_call(call: ast.Call) -> bool:
    return _call_name(call) == "require_attention_e2e_database"


def _is_direct_db_touch(call: ast.Call) -> bool:
    name = _call_name(call)
    if isinstance(call.func, ast.Name):
        return name in _DB_TOUCH_FUNCTION_NAMES
    if isinstance(call.func, ast.Attribute):
        return name in _DB_TOUCH_METHOD_NAMES
    return False


def _function_map(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _nested_function_map(node: ast.FunctionDef) -> dict[str, ast.FunctionDef]:
    return {child.name: child for child in node.body if isinstance(child, ast.FunctionDef)}


def _function_has_db_work(
    node: ast.FunctionDef,
    functions: dict[str, ast.FunctionDef],
    *,
    visiting: set[str] | None = None,
) -> bool:
    visiting = set() if visiting is None else visiting
    local_functions = {**functions, **_nested_function_map(node)}
    for call in _iter_calls_outside_nested_defs(node):
        if _is_direct_db_touch(call):
            return True
        name = _call_name(call)
        if name in local_functions and name not in visiting:
            visiting.add(name)
            if _function_has_db_work(local_functions[name], local_functions, visiting=visiting):
                return True
    return False


def _is_main_entrypoint(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or node.orelse:
        return False
    test = node.test
    if not (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    ):
        return False
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Expr):
        return False
    call = node.body[0].value
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "main"
        and not call.args
        and not call.keywords
    )


def _module_db_work_lines(tree: ast.Module, functions: dict[str, ast.FunctionDef]) -> list[int]:
    lines: list[int] = []
    for node in tree.body:
        if isinstance(node, _TOP_LEVEL_DEFINITION_NODES) or _is_main_entrypoint(node):
            continue
        for call in _iter_calls_outside_nested_defs(node):
            if _is_direct_db_touch(call):
                lines.append(call.lineno)
                continue
            name = _call_name(call)
            if name in functions and _function_has_db_work(functions[name], functions):
                lines.append(call.lineno)
    return lines


def _main_guard_line(main_node: ast.FunctionDef) -> int | None:
    guard_lines = [
        call.lineno for call in _iter_calls_outside_nested_defs(main_node) if _is_guard_call(call)
    ]
    return min(guard_lines) if guard_lines else None


def _main_db_work_before_guard_lines(
    main_node: ast.FunctionDef,
    functions: dict[str, ast.FunctionDef],
    guard_line: int,
) -> list[int]:
    local_functions = {**functions, **_nested_function_map(main_node)}
    lines: list[int] = []
    for call in _iter_calls_outside_nested_defs(main_node):
        if call.lineno >= guard_line:
            continue
        if _is_direct_db_touch(call):
            lines.append(call.lineno)
            continue
        name = _call_name(call)
        if name in local_functions and _function_has_db_work(
            local_functions[name],
            local_functions,
        ):
            lines.append(call.lineno)
    return lines


def validate_seed_source(source: str) -> None:
    tree = ast.parse(source)
    visitor = SeedSafetyVisitor()
    visitor.visit(tree)
    if visitor.setdefault_lines:
        raise SystemExit(
            f"seed script must not set default environment values: {visitor.setdefault_lines}"
        )
    functions = _function_map(tree)
    module_db_lines = _module_db_work_lines(tree, functions)
    if module_db_lines:
        raise SystemExit(f"database work appears before E2E guard: {module_db_lines}")
    main_node = functions.get("main")
    if main_node is None:
        raise SystemExit("seed script must define main")
    guard_line = _main_guard_line(main_node)
    if guard_line is None:
        raise SystemExit("seed script must call require_attention_e2e_database")
    before_guard = _main_db_work_before_guard_lines(main_node, functions, guard_line)
    if before_guard:
        raise SystemExit(f"database work appears before E2E guard: {before_guard}")


def main() -> None:
    seed = Path("scripts/seed_attention_today_e2e.py")
    if not seed.exists():
        raise SystemExit("scripts/seed_attention_today_e2e.py is missing")
    source = seed.read_text(encoding="utf-8")
    validate_seed_source(source)

    original_env = {
        "APP_ENV": os.environ.get("APP_ENV"),
        "AUTH_REQUIRED": os.environ.get("AUTH_REQUIRED"),
        "GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE": os.environ.get(
            "GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE"
        ),
    }
    try:
        os.environ["APP_ENV"] = "test"
        os.environ["AUTH_REQUIRED"] = "false"
        os.environ.pop("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", None)
        try:
            require_attention_e2e_database(
                "postgresql://localhost/gardenops_attention_e2e_test"
            )
        except RuntimeError as exc:
            if "ALLOW_TRUNCATE" not in str(exc):
                raise
        else:
            raise SystemExit("database guard accepted missing allow flag")

        os.environ["GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE"] = "1"
        try:
            require_attention_e2e_database(
                "postgresql://db.example.com/gardenops_attention_e2e_test"
            )
        except RuntimeError as exc:
            if "local" not in str(exc):
                raise
        else:
            raise SystemExit("database guard accepted non-local database host")
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
