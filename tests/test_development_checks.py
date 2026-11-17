"""Developer feedback cannot publish release approval or touch a live database."""

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "development_checks", Path(__file__).parents[1] / "scripts/check_development.py"
)
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class DevelopmentChecksTest(unittest.TestCase):
    def test_both_profiles_keep_backend_build_and_browser_coverage(self):
        for journey in ("attention", "task-history"):
            commands = checks.commands(journey)
            self.assertIn("--full-suite", commands[4])
            self.assertEqual(commands[5], ["npm", "--prefix", "frontend", "run", "build"])
            self.assertEqual(commands[6][-2:], ["node", "scripts/test_security_browser.mjs"])
            self.assertIn("--command-database", commands[7])
            self.assertIn("UV_NO_SYNC=1", commands[7])
            self.assertTrue(commands[7][-1].endswith("_e2e.sh"))
            self.assertFalse(any("sudo" in command or "gh" in command for command in commands))

    def test_failed_check_stops_without_running_later_commands(self):
        with (
            patch("sys.argv", ["check_development.py"]),
            patch.object(
                checks.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)
            ) as run,
        ):
            self.assertEqual(checks.main(), 7)
            run.assert_called_once()

    def test_list_does_not_execute_commands(self):
        with (
            patch("sys.argv", ["check_development.py", "--list"]),
            patch.object(checks.subprocess, "run") as run,
        ):
            self.assertEqual(checks.main(), 0)
            run.assert_not_called()

    def test_timing_observer(self):
        subprocess.run(
            ["node", "--test", "tests/stage_timings.test.cjs"], cwd=checks.ROOT, check=True
        )


if __name__ == "__main__":
    unittest.main()
