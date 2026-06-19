import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PerfHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/slow":
            time.sleep(0.03)
        if self.path not in {"/health", "/api/auth/status", "/slow"}:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"path": self.path, "status": "ok"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), PerfHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def _run_perf(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(ROOT / "scripts" / "check_backend_performance.cjs"), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )


def test_backend_performance_script_reports_endpoint_timings(tmp_path: Path) -> None:
    server, base_url = _serve()
    output_path = tmp_path / "backend-perf.json"
    try:
        result = _run_perf(
            "--base-url",
            base_url,
            "--endpoint",
            "health=/health",
            "--endpoint",
            "auth_status=/api/auth/status",
            "--runs",
            "2",
            "--json",
            "--output",
            str(output_path),
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["baseUrl"] == base_url
    assert payload["runs"] == 2
    assert set(payload["summary"]["endpoints"]) == {"health", "auth_status"}
    assert payload["summary"]["endpoints"]["health"]["okRate"] == 1
    assert payload["summary"]["endpoints"]["health"]["medianMs"] >= 0
    assert output_path.exists()


def test_backend_performance_script_fails_endpoint_budget() -> None:
    server, base_url = _serve()
    try:
        result = _run_perf(
            "--base-url",
            base_url,
            "--endpoint",
            "slow=/slow",
            "--runs",
            "2",
            "--endpoint-budget-ms",
            "slow=1",
        )
    finally:
        server.shutdown()

    assert result.returncode == 1
    assert "slow p75" in result.stderr
    assert "exceeds 1ms" in result.stderr
