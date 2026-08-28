import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_sanitizer_module():
    script_path = ROOT / "scripts" / "git_push_sanitizer.py"
    if not script_path.exists():
        raise AssertionError(f"missing sanitizer script: {script_path}")
    spec = importlib.util.spec_from_file_location("git_push_sanitizer", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GitPushSanitizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sanitizer = load_sanitizer_module()

    def _finding_details(self, text: str) -> list[str]:
        findings = self.sanitizer.find_secret_patterns(
            text.encode("utf-8"),
            "example.py",
            "unit",
        )
        return [finding.detail for finding in findings]

    def test_secret_assignment_allows_code_references_and_test_fixtures(self) -> None:
        text = """
const password = passwordInput.value;
token = secrets.token_urlsafe(48)
password=body.current_password,
session_cookie = row["session_cookie"]
token = "invite-passkey-create-token"
"""

        self.assertEqual(self._finding_details(text), [])

    def test_secret_assignment_blocks_high_entropy_literal_values(self) -> None:
        synthetic_value = "ZP6i7Pz4_" + "aN3KqQpt" + "9VxLm2s" + "R0bYfE8c"
        text = f"""
token = "{synthetic_value}"
API_KEY={synthetic_value}
DATABASE_PASSWORD = "{synthetic_value}"
JWT_SECRET = "{synthetic_value}"
AWS_SECRET_ACCESS_KEY = "{synthetic_value}"
STRIPE_API_KEY = "{synthetic_value}"
"""

        self.assertEqual(
            self._finding_details(text),
            [
                "SECRET_ASSIGNMENT at line 2",
                "SECRET_ASSIGNMENT at line 3",
                "SECRET_ASSIGNMENT at line 4",
                "SECRET_ASSIGNMENT at line 5",
                "SECRET_ASSIGNMENT at line 6",
                "SECRET_ASSIGNMENT at line 7",
            ],
        )

    def test_secret_assignment_detects_secret_after_earlier_separator(self) -> None:
        synthetic_value = "ZP6i7Pz4_" + "aN3KqQpt" + "9VxLm2s" + "R0bYfE8c"
        text = f"""
noop = 0; JWT_SECRET = "{synthetic_value}"
config = {{JWT_SECRET: "{synthetic_value}"}}
not_secret = "short"; STRIPE_API_KEY = "{synthetic_value}"
"""

        self.assertEqual(
            self._finding_details(text),
            [
                "SECRET_ASSIGNMENT at line 2",
                "SECRET_ASSIGNMENT at line 3",
                "SECRET_ASSIGNMENT at line 4",
            ],
        )

    def test_secret_assignment_suppression_does_not_disable_hard_detectors(self) -> None:
        synthetic_openai_key = "sk-proj-" + ("A" * 24)
        synthetic_value = "ZP6i7Pz4_" + "aN3KqQpt" + "9VxLm2s" + "R0bYfE8c"
        text = f"""
token = "{synthetic_value}"  # push-sanitizer: allow SECRET_ASSIGNMENT - synthetic fixture
api_key = "{synthetic_openai_key}"  # push-sanitizer: allow SECRET_ASSIGNMENT
"""

        self.assertEqual(
            self._finding_details(text),
            ["OPENAI_KEY at line 3"],
        )

    def test_safe_example_text_does_not_disable_hard_secret_detectors(self) -> None:
        synthetic_openai_key = "sk-proj-" + ("B" * 24)
        text = f'OPENAI_API_KEY="{synthetic_openai_key}"  # placeholder until prod deploy\n'

        self.assertEqual(self._finding_details(text), ["OPENAI_KEY at line 1"])

    def test_dot_prefixed_sensitive_paths_remain_blocked(self) -> None:
        self.assertEqual(self.sanitizer.is_blocked_path(".env"), ".env")
        self.assertEqual(self.sanitizer.is_blocked_path("./.env"), ".env")
        self.assertEqual(
            self.sanitizer.is_blocked_path(".gardenops/security-release-bypass.json"),
            ".gardenops/**",
        )
        self.assertEqual(
            self.sanitizer.is_blocked_path("./.codex/local/settings.json"),
            ".codex/**",
        )

    def test_path_scan_can_skip_unchanged_secret_patterns(self) -> None:
        synthetic_value = "ZP6i7Pz4_" + "aN3KqQpt" + "9VxLm2s" + "R0bYfE8c"
        data = f'token = "{synthetic_value}"\n'.encode()

        full_scan = self.sanitizer.scan_path_and_content(
            "example.py",
            data,
            "unit",
        )
        path_only_scan = self.sanitizer.scan_path_and_content(
            "example.py",
            data,
            "unit",
            scan_secrets=False,
        )
        added_line_scan = self.sanitizer.find_secret_patterns_in_lines(
            [(12, f'token = "{synthetic_value}"')],
            "example.py",
            "unit",
            detail_label="added line",
        )

        self.assertEqual([finding.detail for finding in full_scan], ["SECRET_ASSIGNMENT at line 1"])
        self.assertEqual(path_only_scan, [])
        self.assertEqual(
            [finding.detail for finding in added_line_scan],
            ["SECRET_ASSIGNMENT at added line 12"],
        )

    def test_secret_assignment_rejects_long_non_matching_lines_quickly(self) -> None:
        text = "secret" * 6000

        self.assertEqual(self._finding_details(text), [])

    def test_commit_patch_scan_uses_explicit_git_show_arguments(self) -> None:
        synthetic_value = "ZP6i7Pz4_" + "aN3KqQpt" + "9VxLm2s" + "R0bYfE8c"
        output = (
            "diff --git a/example.py b/example.py\n"
            "+++ b/example.py\n"
            "@@ -0,0 +1 @@\n"
            f'+API_KEY = "{synthetic_value}"\n'
        ).encode()
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr=b"")

        with patch.object(self.sanitizer, "run_git", return_value=completed) as run_git:
            findings = self.sanitizer.scan_commit_patch("a" * 40)

        run_git.assert_called_once_with(
            ["show", "--format=", "--unified=0", "--no-ext-diff", "a" * 40, "--"],
            check=False,
        )
        self.assertTrue(any(finding.path == "example.py" for finding in findings))

    def test_commit_patch_scan_fails_closed_when_git_show_fails(self) -> None:
        completed = subprocess.CompletedProcess([], 128, stdout=b"", stderr=b"bad revision")

        with patch.object(self.sanitizer, "run_git", return_value=completed):
            findings = self.sanitizer.scan_commit_patch("bad")

        self.assertEqual([finding.code for finding in findings], ["PATCH_SCAN_FAILED"])

    def test_commit_patch_scan_does_not_truncate_large_text_patches(self) -> None:
        synthetic_value = "ZP6i7Pz4_" + "aN3KqQpt" + "9VxLm2s" + "R0bYfE8c"
        output = (
            "diff --git a/example.py b/example.py\n"
            "+++ b/example.py\n"
            "@@ -0,0 +1,2 @@\n"
            f"+{'x' * 520_000}\n"
            f'+API_KEY = "{synthetic_value}"\n'
        ).encode()
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr=b"")

        with patch.object(self.sanitizer, "run_git", return_value=completed):
            findings = self.sanitizer.scan_commit_patch("a" * 40)

        self.assertTrue(any(finding.path == "example.py" for finding in findings))


if __name__ == "__main__":
    unittest.main()
