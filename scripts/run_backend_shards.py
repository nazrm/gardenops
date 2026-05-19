#!/usr/bin/env python3
"""Run backend pytest files in parallel against separate disposable databases."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _derive_shard_url(base_url: str, shard_index: int) -> str:
    query = ""
    stem = base_url
    if "?" in base_url:
        stem, query = base_url.split("?", 1)
        query = f"?{query}"
    prefix, sep, database = stem.rpartition("/")
    if not sep or not database:
        raise ValueError("GARDENOPS_TEST_POSTGRES_URL must include a database name")
    return f"{prefix}/{database}_shard{shard_index}{query}"


def _test_files() -> list[str]:
    return [str(path.relative_to(ROOT)) for path in sorted((ROOT / "tests").glob("test_*.py"))]


def _collect_nodeids(env: dict[str, str]) -> list[str]:
    nodeids: list[str] = []
    for path in _test_files():
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", path],
            cwd=ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            print(result.stdout, file=sys.stderr)
            raise RuntimeError(f"pytest collection failed for {path}")
        nodeids.extend(
            line.strip() for line in result.stdout.splitlines() if line.startswith(f"{path}::")
        )
    return nodeids


def _balanced_file_shards(files: Sequence[str], shard_count: int) -> list[list[str]]:
    weighted = sorted(
        ((sum(1 for _ in (ROOT / path).open(encoding="utf-8")), path) for path in files),
        reverse=True,
    )
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    weights = [0 for _ in range(shard_count)]
    for weight, path in weighted:
        index = min(range(shard_count), key=weights.__getitem__)
        shards[index].append(path)
        weights[index] += weight
    return shards


def _balanced_node_shards(nodeids: Sequence[str], shard_count: int) -> list[list[str]]:
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for index, nodeid in enumerate(nodeids):
        shards[index % shard_count].append(nodeid)
    return shards


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, default=4, help="number of parallel pytest processes")
    parser.add_argument(
        "--scope",
        choices=("node", "file"),
        default="file",
        help="split whole test files or individual collected tests",
    )
    parser.add_argument("--logs-dir", default="/tmp/gardenops-pytest-shards")
    args = parser.parse_args()

    if args.shards < 1:
        parser.error("--shards must be at least 1")

    base_url = os.environ.get("GARDENOPS_TEST_POSTGRES_URL", "").strip()
    if not base_url:
        parser.error("GARDENOPS_TEST_POSTGRES_URL must point at a disposable test database")

    base_env = os.environ.copy()
    base_env["APP_ENV"] = base_env.get("APP_ENV", "test")
    base_env["AUTH_PASSWORD_HASH_FAST_FOR_TESTS"] = base_env.get(
        "AUTH_PASSWORD_HASH_FAST_FOR_TESTS",
        "true",
    )
    base_env["GARDENOPS_TEST_POSTGRES_URL"] = base_url
    base_env.pop("DATABASE_URL", None)

    items = _collect_nodeids(base_env) if args.scope == "node" else _test_files()
    if not items:
        parser.error("no tests/test_*.py files found")
    shards = (
        _balanced_node_shards(items, args.shards)
        if args.scope == "node"
        else _balanced_file_shards(items, args.shards)
    )

    logs_dir = Path(args.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    processes: list[tuple[int, Path, subprocess.Popen[str]]] = []
    start = time.monotonic()
    for shard_index, shard_items in enumerate(shards):
        if not shard_items:
            continue
        shard_url = _derive_shard_url(base_url, shard_index)
        env = base_env.copy()
        env["GARDENOPS_TEST_POSTGRES_URL"] = shard_url

        log_path = logs_dir / f"shard-{shard_index}.log"
        command = [
            sys.executable,
            "-m",
            "pytest",
            *shard_items,
            "-q",
            "--tb=short",
        ]
        print(
            f"shard {shard_index}: {len(shard_items)} {args.scope}(s), log={log_path}",
            flush=True,
        )
        log_file = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_file.close()
        processes.append((shard_index, log_path, process))

    failed = False
    for shard_index, log_path, process in processes:
        return_code = process.wait()
        status = "passed" if return_code == 0 else f"failed ({return_code})"
        print(f"shard {shard_index}: {status}", flush=True)
        if return_code != 0:
            failed = True
            print(log_path.read_text(encoding="utf-8"), file=sys.stderr)

    elapsed = time.monotonic() - start
    print(f"elapsed={elapsed:.2f}s", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
