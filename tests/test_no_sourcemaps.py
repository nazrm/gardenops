import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NoSourceMapsScriptTests(unittest.TestCase):
    def test_inline_source_mapping_url_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dist = Path(tmpdir)
            (dist / "assets").mkdir()
            (dist / "assets" / "app.js").write_text(
                "console.log('built');\n//# sourceMappingURL=data:application/json;base64,e30=\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", "scripts/check_no_sourcemaps.cjs", str(dist)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("sourceMappingURL", result.stderr)


if __name__ == "__main__":
    unittest.main()
