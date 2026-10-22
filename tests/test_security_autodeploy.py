import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import gardenops_security_deploy as deploy

OLD = "a" * 40
NEW = "b" * 40


class DeploymentTests(unittest.TestCase):
    def test_failed_command_preserves_diagnostic_output(self):
        with self.assertRaises(RuntimeError) as failure:
            deploy.command(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('build context'); "
                    "print('activation failed', file=sys.stderr); sys.exit(7)",
                ]
            )
        self.assertIn("build context", str(failure.exception))
        self.assertIn("activation failed", str(failure.exception))
        self.assertIn("7", str(failure.exception))

    def test_timed_out_command_preserves_partial_diagnostics(self):
        error = subprocess.TimeoutExpired(
            ["deployer"], 1, output=b"activation started", stderr=b"health pending"
        )
        with patch.object(deploy.subprocess, "run", side_effect=error):
            with self.assertRaises(RuntimeError) as failure:
                deploy.command(["deployer"], timeout=1)
        self.assertIn("activation started", str(failure.exception))
        self.assertIn("health pending", str(failure.exception))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {
            "state_dir": self.tmp.name,
            "control_repo": "/control",
            "deployer": "/deployer",
            "release_root": "/releases",
            "service": "gardenops.service",
        }
        self.commands = []
        self.head = NEW
        self.live = OLD
        self.health_calls = []
        self.paths = "uv.lock\nfrontend/package-lock.json"
        self.ci = "success"

    def api(self, path):
        if path == "commits/main":
            return {"sha": self.head}
        if path.endswith("/pulls"):
            return [{"number": 1}]
        if path == "pulls/1":
            return {
                "merged": True,
                "merge_commit_sha": NEW,
                "merged_by": {"login": "nazrm"},
                "base": {"ref": "main", "repo": {"full_name": "nazrm/gardenops"}},
            }
        return {
            "workflow_runs": [
                {
                    "id": 10,
                    "head_sha": NEW,
                    "head_branch": "main",
                    "event": "push",
                    "status": "completed",
                    "conclusion": self.ci,
                }
            ]
        }

    def command(self, args, timeout=60):
        self.commands.append(args)
        if "binding" in args:
            return "/control\n/releases\ngardenops.service"
        if "rev-parse" in args:
            return self.head
        if "diff" in args:
            return self.paths
        if "rev-list" in args:
            return NEW
        if "activate-if-current" in args:
            self.assertEqual(args[-1], OLD)
            self.live = NEW
        return ""

    def run_deploy(self, apply=True, command=None, health=None):
        return deploy.run(
            self.config,
            apply=apply,
            api=self.api,
            execute=command or self.command,
            current=lambda c: self.live,
            health=health or (lambda c, s: self.health_calls.append(s)),
        )

    def test_success_and_idempotence(self):
        self.assertEqual(self.run_deploy()["phase"], "deployed")
        self.assertEqual(self.run_deploy()["phase"], "current")
        self.assertEqual(sum("activate-if-current" in c for c in self.commands), 1)
        self.assertEqual(
            json.loads((Path(self.tmp.name) / "last.json").read_text())["phase"], "deployed"
        )

    def test_plan_never_deploys(self):
        self.assertEqual(self.run_deploy(False)["phase"], "ready")
        self.assertFalse(any(c[0] == "/deployer" for c in self.commands))

    def test_direct_push_or_unmerged_pr_is_not_authorization(self):
        with self.assertRaisesRegex(ValueError, "merged-PR"):
            deploy.require_reviewed_merges([NEW], lambda path: [])
        for field, value in [
            ("merged", False),
            ("merge_commit_sha", OLD),
            ("merged_by", {"login": "renovate[bot]"}),
            ("base", {"ref": "other"}),
        ]:

            def api(path):
                result = self.api(path)
                if path == "pulls/1":
                    result[field] = value
                return result

            with self.assertRaisesRegex(ValueError, "merged-PR"):
                deploy.require_reviewed_merges([NEW], api)

    def test_entire_release_range_requires_merge_review(self):
        with self.assertRaisesRegex(ValueError, "merged-PR"):
            deploy.require_reviewed_merges([OLD, NEW], self.api)

    def test_failed_ci_blocks(self):
        self.ci = "failure"
        with self.assertRaises(ValueError):
            self.run_deploy()
        self.assertEqual(self.commands, [])

    def test_application_changes_block(self):
        self.paths = "uv.lock\ngardenops/main.py"
        with self.assertRaises(ValueError):
            self.run_deploy()
        self.assertFalse(any(c[0] == "/deployer" for c in self.commands))

    def test_migration_blocks(self):
        self.paths = "uv.lock\nmigrations/101.sql"
        with self.assertRaises(ValueError):
            self.run_deploy()

    def test_moved_head_after_prepare_blocks_activation(self):
        def command(args, timeout=60):
            result = self.command(args, timeout)
            if "prepare" in args:
                self.head = "c" * 40
            return result

        with self.assertRaises(ValueError):
            self.run_deploy(command=command)
        self.assertFalse(any("activate-if-current" in c for c in self.commands))

    def test_unknown_activation_is_not_retried(self):
        def command(args, timeout=60):
            if "activate-if-current" in args:
                raise TimeoutError("uncertain")
            return self.command(args, timeout)

        with self.assertRaises(TimeoutError):
            self.run_deploy(command=command)
        count = len(self.commands)
        with self.assertRaisesRegex(ValueError, "requires inspection"):
            self.run_deploy()
        self.assertEqual(count, len(self.commands))

    def test_crash_receipt_blocks_even_when_target_installed(self):
        deploy.save(Path(self.tmp.name) / "last.json", {"phase": "activating", "revision": NEW})
        self.live = NEW
        with self.assertRaisesRegex(ValueError, "requires inspection"):
            self.run_deploy()

    def test_health_failure_is_not_success(self):
        def fail(c, s):
            raise ValueError("health failed")

        with self.assertRaises(ValueError):
            self.run_deploy(health=fail)
        self.assertEqual(
            json.loads((Path(self.tmp.name) / "last.json").read_text())["phase"], "failed"
        )

    def test_wrong_sha_ci_rejected(self):
        with self.assertRaises(ValueError):
            deploy.require_ci(OLD, self.api)

    def test_manifest_scripts_cannot_change(self):
        with self.assertRaisesRegex(ValueError, "policy"):
            deploy.require_manifest_compatibility(
                "frontend/package.json",
                '{"scripts":{"build":"safe"}}',
                '{"scripts":{"build":"other"}}',
            )
        deploy.require_manifest_compatibility(
            "frontend/package.json", '{"dependencies":{"a":"1"}}', '{"dependencies":{"a":"2"}}'
        )

    def test_python_source_policy_cannot_change(self):
        with self.assertRaisesRegex(ValueError, "policy"):
            deploy.require_manifest_compatibility(
                "pyproject.toml", '[tool.uv.sources]\na="old"', '[tool.uv.sources]\na="new"'
            )

    def test_wrong_deployer_binding_blocks_prepare(self):
        def command(args, timeout=60):
            return "/wrong" if "binding" in args else self.command(args, timeout)

        with self.assertRaisesRegex(ValueError, "binding"):
            self.run_deploy(command=command)
        self.assertFalse(any("prepare" in c for c in self.commands))

    def test_changed_previous_at_activation_is_not_retried(self):
        def command(args, timeout=60):
            if "activate-if-current" in args:
                raise ValueError("current release changed")
            return self.command(args, timeout)

        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_deploy(command=command)
        self.assertEqual(
            json.loads((Path(self.tmp.name) / "last.json").read_text())["phase"], "failed"
        )

    def test_health_redirect_and_plaintext_rejected(self):
        with self.assertRaises(ValueError):
            deploy.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere")
        with self.assertRaises(ValueError):
            deploy.public_health("http://localhost/health")

    def test_health_requires_actual_healthy_json(self):
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(deploy.urllib.request, "build_opener", return_value=opener):
            response.read.return_value = b'{"status":"corrupt"}'
            with self.assertRaises(ValueError):
                deploy.public_health("https://example.invalid/api/health")
            response.read.return_value = b'{"status":"ok"}'
            deploy.public_health("https://example.invalid/api/health")

    def test_real_shell_compare_runs_under_lock_before_activation(self):
        script = (Path(deploy.__file__).parent / "gardenops-atomic-deploy").read_text()
        main = script[script.index("main() {") : script.rindex('main "$@"')]
        root = Path(self.tmp.name)
        releases = root / "releases"
        (releases / OLD).mkdir(parents=True)
        (root / "current").symlink_to(releases / OLD)
        setup = """set -euo pipefail
require_root() { :; }
open_deploy_lock() { locked=yes; }
die() { echo "$*" >&2; exit 1; }
activate_release() { test "$locked" = yes; echo ACTIVATED; }
ENV_FILE=/dev/null
RELEASES_DIR="$1/./releases"
CURRENT_LINK="$1/current"
shift
"""
        for expected, succeeds in [(OLD, True), (NEW, False)]:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    setup + main + '\nmain "$@"',
                    "bash",
                    str(root),
                    "activate-if-current",
                    NEW,
                    expected,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode == 0, succeeds, result.stderr)
            self.assertEqual("ACTIVATED" in result.stdout, succeeds)

    def test_no_downgrade(self):
        def command(args, timeout=60):
            if "merge-base" in args:
                raise ValueError("not an ancestor")
            return self.command(args, timeout)

        with self.assertRaises(ValueError):
            self.run_deploy(command=command)
        self.assertFalse(any(c[0] == "/deployer" for c in self.commands))


if __name__ == "__main__":
    unittest.main()
