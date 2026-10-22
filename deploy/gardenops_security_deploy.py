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
import tomllib
import urllib.parse
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
    try:
        return subprocess.run(
            argv, check=True, text=True, capture_output=True, timeout=timeout
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        details = []
        for name in ("stdout", "stderr"):
            value = getattr(exc, name, None) or ""
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            if value:
                details.append(f"{name}: {value[-8000:]}")
        raise RuntimeError(f"{exc}\n" + "\n".join(details)) from exc


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


def require_reviewed_merges(revisions, api=github):
    """Only Ole's actual merges authorize unattended deployment, not direct pushes."""
    if not revisions or len(revisions) > 20:
        raise ValueError("Release range needs manual merge review")
    for revision in revisions:
        if not SHA.fullmatch(revision):
            raise ValueError("Invalid release range revision")
        matches = api("commits/" + revision + "/pulls")
        accepted = False
        for summary in matches:
            pull = api("pulls/" + str(int(summary["number"])))
            if (pull.get("merged") is True
                and pull.get("merge_commit_sha") == revision
                and pull.get("merged_by", {}).get("login") == "nazrm"
                and pull.get("base", {}).get("ref") == "main"
                and pull.get("base", {}).get("repo", {}).get("full_name") == "nazrm/gardenops"):
                accepted = True
                break
        if not accepted:
            raise ValueError("Revision lacks Ole's merged-PR authorization: " + revision)


def require_manifest_compatibility(path, before, after):
    """Only dependency constraints may change inside executable manifests."""
    if path.endswith("package.json"):
        old, new = json.loads(before), json.loads(after)
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            old.pop(key, None)
            new.pop(key, None)
    elif path == "pyproject.toml":
        old, new = tomllib.loads(before), tomllib.loads(after)
        for value in (old, new):
            project = value.get("project", {})
            project.pop("dependencies", None)
            project.pop("optional-dependencies", None)
            value.pop("dependency-groups", None)
    else:
        return
    if old != new:
        raise ValueError("Manifest changes executable/source policy; use normal release")


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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Public health redirected; refusing acceptance")


def public_health(url):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("Public health requires HTTPS")
    with urllib.request.build_opener(NoRedirect).open(url, timeout=15) as response:
        if response.status != 200 or json.load(response).get("status") != "ok":
            raise ValueError("Public health is not healthy GardenOps JSON")


def verify(config, sha):
    if installed(config) != sha:
        raise ValueError("Installed revision mismatch")
    pid = command(["systemctl", "show", config["service"], "--property=MainPID", "--value"])
    if not pid.isdigit() or int(pid) <= 1:
        raise ValueError("Service has no live main process")
    cwd = Path("/proc", pid, "cwd").resolve(strict=True)
    if cwd != (Path(config["release_root"]) / "releases" / sha).resolve(strict=True):
        raise ValueError("Running process does not use the installed release")
    public_health(config["health_url"])


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
    require_reviewed_merges(
        execute(["git", "-C", repo, "rev-list", "--first-parent", before + ".." + target]).splitlines(),
        api,
    )
    paths = execute(["git", "-C", repo, "diff", "--name-only", before, target]).splitlines()
    require_dependency_change(paths)
    for path in paths:
        if path in {"pyproject.toml", "frontend/package.json"}:
            require_manifest_compatibility(
                path,
                execute(["git", "-C", repo, "show", before + ":" + path]),
                execute(["git", "-C", repo, "show", target + ":" + path]),
            )
    result = {
        "phase": "ready",
        "previous": before,
        "revision": target,
        "ci_run": ci_run,
        "paths": paths,
    }
    if not apply:
        return result
    binding = execute([config["deployer"], "binding"]).splitlines()
    if binding != [repo, config["release_root"], config["service"]]:
        raise ValueError("Atomic deployer binding differs from poller configuration")
    execute([config["deployer"], "prepare", target], 1800)
    if current(config) != before or target_head(api) != target:
        raise ValueError("Release or main moved during preparation; no activation")
    require_ci(target, api)
    save(receipt, {**result, "phase": "activating"})
    try:
        execute([config["deployer"], "activate-if-current", target, before], 300)
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
