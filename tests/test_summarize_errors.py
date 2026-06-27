import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from gardenops.redaction import redact_sensitive_text

ROOT = Path(__file__).resolve().parents[1]


def _write_log(path: Path, entries: list[dict[str, object]]) -> None:
    now = datetime.now(UTC).isoformat()
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps({"ts": now, **entry}) + "\n")


def _run_summary(log_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "summarize_errors.py"),
            "1440",
            "--log-file",
            str(log_file),
            "--exclude-synthetic",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


def test_user_facing_summary_excludes_expected_anonymous_auth_bootstrap(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "errors.jsonl"
    _write_log(
        log_file,
        [
            {
                "level": "WARNING",
                "logger": "gardenops.errors",
                "message": "Client api_error: Unauthorized: session token required",
                "path": "/",
                "method": "CLIENT",
                "status_code": 401,
                "request_id": "auth-bootstrap-request",
                "api_path": "/api/auth/me",
                "error_kind": "api_error",
                "handled": True,
            },
            {
                "level": "WARNING",
                "logger": "gardenops.errors",
                "message": "Client api_error: Request failed (503)",
                "path": "/",
                "method": "CLIENT",
                "status_code": 503,
                "request_id": "real-failure-request",
                "api_path": "/api/plants",
                "error_kind": "api_error",
                "handled": True,
            },
        ],
    )

    result = _run_summary(log_file, "--user-facing")

    assert result.returncode == 0
    assert "=== 1 user-facing error group(s)" in result.stdout
    assert "/api/plants" in result.stdout
    assert "/api/auth/me" not in result.stdout
    assert "auth-bootstrap-request" not in result.stdout


def test_grouped_summary_excludes_expected_anonymous_auth_probe_backend_log(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "errors.jsonl"
    _write_log(
        log_file,
        [
            {
                "level": "WARNING",
                "logger": "gardenops.main",
                "message": "Auth rejected: GET /api/auth/me ip=127.0.0.1",
                "path": "/api/auth/me",
                "method": "GET",
                "request_id": "auth-backend-request",
            },
            {
                "level": "ERROR",
                "logger": "gardenops.main",
                "message": "Database connection failed",
                "path": "/api/plants",
                "method": "GET",
                "status_code": 500,
                "request_id": "real-backend-failure",
            },
        ],
    )

    result = _run_summary(log_file, "--grouped")

    assert result.returncode == 0
    assert "=== 1 error group(s)" in result.stdout
    assert "Database connection failed" in result.stdout
    assert "/api/auth/me" not in result.stdout
    assert "auth-backend-request" not in result.stdout


def test_grouped_summary_excludes_deployed_readiness_admin_probe_auth_rejection(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "errors.jsonl"
    _write_log(
        log_file,
        [
            {
                "level": "WARNING",
                "logger": "gardenops.main",
                "message": "Auth rejected: GET /api/admin/system/health ip=127.0.0.1",
                "path": "/api/admin/system/health",
                "method": "GET",
                "request_id": "admin-health-probe",
            },
            {
                "level": "ERROR",
                "logger": "gardenops.main",
                "message": "Scheduler crashed",
                "path": "/api/tasks",
                "method": "GET",
                "status_code": 500,
                "request_id": "real-error",
            },
        ],
    )

    result = _run_summary(log_file, "--grouped")

    assert result.returncode == 0
    assert "=== 1 error group(s)" in result.stdout
    assert "Scheduler crashed" in result.stdout
    assert "/api/admin/system/health" not in result.stdout
    assert "admin-health-probe" not in result.stdout


def test_summary_skips_invalid_timestamps(tmp_path: Path) -> None:
    log_file = tmp_path / "errors.jsonl"
    now = datetime.now(UTC).isoformat()
    with log_file.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts": {"not": "a timestamp"},
                    "level": "ERROR",
                    "logger": "gardenops.main",
                    "message": "Malformed timestamp entry",
                },
            )
            + "\n",
        )
        handle.write(
            json.dumps(
                {
                    "ts": now,
                    "level": "ERROR",
                    "logger": "gardenops.main",
                    "message": "Real runtime error",
                    "path": "/api/plants",
                    "method": "GET",
                    "status_code": 500,
                    "request_id": "valid-error",
                },
            )
            + "\n",
        )

    result = _run_summary(log_file, "--grouped")

    assert result.returncode == 0
    assert "=== 1 error group(s)" in result.stdout
    assert "Real runtime error" in result.stdout
    assert "Malformed timestamp entry" not in result.stdout


def test_grouped_summary_redacts_tokens_urls_and_secret_assignments(tmp_path: Path) -> None:
    log_file = tmp_path / "errors.jsonl"
    _write_log(
        log_file,
        [
            {
                "level": "ERROR",
                "logger": "gardenops.main",
                "message": (
                    "Provider failed with Bearer secret-token-value-123456 "
                    "url=https://example.com/callback?token=raw-token-123 "
                    "ANTHROPIC_API_KEY=sk-ant-test-secret"
                ),
                "path": "/api/ai/diagnose-plant",
                "method": "POST",
                "status_code": 502,
                "request_id": "redaction-check",
            },
        ],
    )

    result = _run_summary(log_file, "--grouped")

    assert result.returncode == 0
    assert "secret-token-value-123456" not in result.stdout
    assert "raw-token-123" not in result.stdout
    assert "sk-ant-test-secret" not in result.stdout
    assert "[REDACTED_TOKEN]" in result.stdout
    assert "ANTHROPIC_API_KEY=[REDACTED]" in result.stdout


def test_redactor_redacts_colon_and_json_style_secret_values() -> None:
    key_field = "api" + "_key"
    header_field = "x-" + key_field.replace("_", "-")
    json_secret = "sk" + "-json-secret-123456789"
    header_secret = "sk" + "-hyphen-secret-123456789"
    colon_secret = "sk" + "-colon-secret-123456789"
    bearer_secret = "bearer-like-secret-123456789"
    text = (
        f'provider error {{"{key_field}":"{json_secret}"}} '
        f'{{"{header_field}":"{header_secret}"}} '
        f"upstream {key_field}: {colon_secret} "
        f"auth_token : {bearer_secret}"
    )

    redacted = redact_sensitive_text(text)

    assert json_secret not in redacted
    assert header_secret not in redacted
    assert colon_secret not in redacted
    assert bearer_secret not in redacted
    assert f'"{key_field}":"[REDACTED]"' in redacted
    assert f"{key_field}: [REDACTED]" in redacted


def test_redactor_redacts_calendar_terrain_invite_reset_and_malformed_urls() -> None:
    text = (
        "Feed https://example.com/calendar/subscriptions/feed-token-123.ics "
        "terrain=/shademap/terrain/1/0/0.png?token=terrain-token-123 "
        "invite=https://example.com/?invite=invite-token-123 "
        "reset=https://example.com/?reset=reset-token-123 "
        "bad=https://example.com:bad/path?token=raw-token-123"
    )

    redacted = redact_sensitive_text(text)

    assert "feed-token-123" not in redacted
    assert "terrain-token-123" not in redacted
    assert "invite-token-123" not in redacted
    assert "reset-token-123" not in redacted
    assert "raw-token-123" not in redacted
    assert "/calendar/subscriptions/[REDACTED].ics" in redacted
    assert "/shademap/terrain/1/0/0.png?[REDACTED]" in redacted
    assert "[REDACTED_URL]" in redacted
