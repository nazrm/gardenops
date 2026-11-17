#!/usr/bin/env python3
"""Fast developer feedback only; never issues release approval or deploys."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def commands(journey: str) -> list[list[str]]:
    python = str(ROOT / ".venv/bin/python")
    chromium = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium")
    runner = [python, "scripts/run_fast_postgres_tests.py"]
    checks = [
        ["uv", "lock", "--check", "--offline", "--no-build"],
        ["uv", "sync", "--locked", "--all-groups", "--check", "--no-config"],
        [
            "npm",
            "ci",
            "--prefix",
            "frontend",
            "--dry-run",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--json",
        ],
        [python, "-m", "ruff", "check", "."],
        [python, "-m", "ruff", "format", "--check", "."],
        [python, "scripts/check_env_docs.py"],
        [*runner, "--full-suite", "--shards", "4"],
        ["npm", "--prefix", "frontend", "run", "build"],
        ["env", f"CHROMIUM_PATH={chromium}", "node", "scripts/test_security_browser.mjs"],
    ]
    database, script = {
        "attention": ("gardenops_attention_e2e_test", "scripts/run_attention_today_e2e.sh"),
        "task-history": (
            "gardenops_task_history_e2e_test",
            "scripts/run_task_completion_history_e2e.sh",
        ),
    }[journey]
    checks.append(
        [
            *runner,
            "--command",
            "--command-database",
            database,
            "--",
            "env",
            "UV_NO_SYNC=1",
            f"CHROMIUM_EXECUTABLE={chromium}",
            "bash",
            script,
        ]
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journey", choices=("attention", "task-history"), default="attention")
    parser.add_argument("--list", action="store_true", help="list commands without executing")
    args = parser.parse_args()
    print("Developer feedback only. Full Local Release Gate is still required.", flush=True)
    for command in commands(args.journey):
        print("+ " + " ".join(command), flush=True)
        if args.list:
            continue
        environment = os.environ.copy()
        if "scripts/run_fast_postgres_tests.py" in command:
            bindir = subprocess.check_output(["pg_config", "--bindir"], text=True).strip()
            environment["PATH"] = bindir + os.pathsep + environment.get("PATH", "")
        started = time.monotonic()
        node_check = "--dry-run" in command
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=node_check,
            text=True,
        )
        print(f"elapsed={time.monotonic() - started:.2f}s exit={result.returncode}", flush=True)
        if result.returncode:
            if node_check:
                print(result.stderr, file=sys.stderr)
            return result.returncode if result.returncode > 0 else 1
        if node_check:
            try:
                state = json.loads(result.stdout)
            except json.JSONDecodeError:
                state = None
            if not isinstance(state, dict) or any(
                state.get(key) != 0 for key in ("added", "changed", "removed")
            ):
                print(
                    "Installed frontend packages differ from the lockfile; "
                    "run npm ci --ignore-scripts after policy approval.",
                    file=sys.stderr,
                )
                return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)
