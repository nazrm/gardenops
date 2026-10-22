import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import gardenops_security_deploy as deploy

OLD = "a" * 40
NEW = "b" * 40


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {
            "state_dir": self.tmp.name,
            "control_repo": "/control",
            "deployer": "/deployer",
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
        if "rev-parse" in args:
            return self.head
        if "diff" in args:
            return self.paths
        if "activate" in args:
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
        self.assertEqual(sum("activate" in c for c in self.commands), 1)
        self.assertEqual(
            json.loads((Path(self.tmp.name) / "last.json").read_text())["phase"], "deployed"
        )

    def test_plan_never_deploys(self):
        self.assertEqual(self.run_deploy(False)["phase"], "ready")
        self.assertFalse(any(c[0] == "/deployer" for c in self.commands))

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
        self.assertFalse(any("activate" in c for c in self.commands))

    def test_unknown_activation_is_not_retried(self):
        def command(args, timeout=60):
            if "activate" in args:
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
