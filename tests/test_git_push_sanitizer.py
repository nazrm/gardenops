import importlib.util
import sys
import unittest
from pathlib import Path

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
"""

        self.assertEqual(
            self._finding_details(text),
            [
                "SECRET_ASSIGNMENT at line 2",
                "SECRET_ASSIGNMENT at line 3",
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


if __name__ == "__main__":
    unittest.main()
