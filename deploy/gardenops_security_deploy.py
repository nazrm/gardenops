#!/usr/bin/env python3
"""Deploy tested, merged GardenOps dependency updates through the existing deployer.

Install outside the checkout as root-owned code. No PR code runs in this process.
Only the existing atomic deploy command builds/activates the accepted revision.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
DEPENDENCY_PATHS = {
    "pyproject.toml",
    "uv.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
}
POLICY_PATHS = {".github/dependabot.yml"}


def command(argv, timeout=60):
    return subprocess.run(
        argv, check=True, text=True, capture_output=True, timeout=timeout
    ).stdout.strip()


def github(path):
    request = urllib.request.Request(
        "https://api.github.com/repos/nazrm/gardenops/" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "gardenops-security-deploy",
        },
    )
    # Public repository: no deployment credential is required for metadata reads.
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def target_head(api=github):
    sha = api("commits/main")["sha"]
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid default branch revision")
    return sha


def require_ci(sha, api=github):
    runs = api(
        "actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=" + sha + "&per_page=100"
    )
    matching = [
        r
        for r in runs["workflow_runs"]
        if r.get("head_sha") == sha and r.get("head_branch") == "main" and r.get("event") == "push"
    ]
    latest = max(matching, key=lambda r: (r["id"], r.get("run_attempt", 1)), default=None)
    if not latest or latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("Exact main revision has no successful completed GardenOps CI run")
    return latest["id"]


def require_dependency_change(paths):
    if not paths or not set(paths) <= DEPENDENCY_PATHS | POLICY_PATHS:
        raise ValueError(
            "Change includes application/configuration/schema changes; use normal release"
        )
    if not set(paths) & DEPENDENCY_PATHS:
        raise ValueError("No dependency update to deploy")


def save(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def installed(config):
    root = Path(config["release_root"])
    current = (root / "current").resolve(strict=True)
    releases = (root / "releases").resolve(strict=True)
    if current.parent != releases or not SHA.fullmatch(current.name):
        raise ValueError("Current release is not an immutable commit directory")
    if (current / "RELEASE_COMMIT").read_text().strip() != current.name:
        raise ValueError("Installed release receipt mismatch")
    return current.name


def verify(config, sha):
    if installed(config) != sha:
        raise ValueError("Installed revision mismatch")
    pid = command(["systemctl", "show", config["service"], "--property=MainPID", "--value"])
    if not pid.isdigit() or int(pid) <= 1:
        raise ValueError("Service has no live main process")
    cwd = Path("/proc", pid, "cwd").resolve(strict=True)
    if cwd != (Path(config["release_root"]) / "releases" / sha).resolve(strict=True):
        raise ValueError("Running process does not use the installed release")
    with urllib.request.urlopen(config["health_url"], timeout=15) as response:
        if response.status != 200:
            raise ValueError("Public health check failed")


def run(config, *, apply=False, api=github, execute=command, current=installed, health=verify):
    target = target_head(api)
    before = current(config)
    receipt = Path(config["state_dir"]) / "last.json"
    old = json.loads(receipt.read_text()) if receipt.exists() else {}
    # A restart/crash must never silently retry or certify an unknown activation.
    if old.get("phase") in {"activating", "failed"}:
        raise ValueError("Previous activation requires inspection: " + str(receipt))
    if target == before:
        health(config, target)
        return {"phase": "current", "revision": target}
    ci_run = require_ci(target, api)
    repo = config["control_repo"]
    execute(["git", "-C", repo, "fetch", "origin", "main"], 120)
    fetched = execute(["git", "-C", repo, "rev-parse", "origin/main"])
    if fetched != target:
        raise ValueError("Default branch changed during fetch")
    execute(["git", "-C", repo, "merge-base", "--is-ancestor", before, target])
    paths = execute(["git", "-C", repo, "diff", "--name-only", before, target]).splitlines()
    require_dependency_change(paths)
    result = {
        "phase": "ready",
        "previous": before,
        "revision": target,
        "ci_run": ci_run,
        "paths": paths,
    }
    if not apply:
        return result
    execute([config["deployer"], "prepare", target], 1800)
    if current(config) != before or target_head(api) != target:
        raise ValueError("Release or main moved during preparation; no activation")
    require_ci(target, api)
    save(receipt, {**result, "phase": "activating"})
    try:
        execute([config["deployer"], "activate", target], 300)
        health(config, target)
    except BaseException:
        save(receipt, {**result, "phase": "failed"})
        raise
    result["phase"] = "deployed"
    save(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    state = Path(config["state_dir"])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(run(config, apply=args.apply)))


if __name__ == "__main__":
    main()
