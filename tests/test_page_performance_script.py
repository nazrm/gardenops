import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_page_perf(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(ROOT / "scripts" / "check_page_performance.cjs"), *args],
        cwd=ROOT / "frontend",
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def test_page_performance_script_documents_authenticated_app_scenario() -> None:
    result = _run_page_perf("--help")

    assert result.returncode == 0
    assert "app-unauth, app-auth, or app-auth-large-tabs" in result.stdout
