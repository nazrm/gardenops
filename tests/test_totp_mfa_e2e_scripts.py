from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import seed_totp_mfa_e2e

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "scripts" / "seed_totp_mfa_e2e.py"
CHECK = ROOT / "scripts" / "check_totp_mfa_e2e.cjs"
RUNNER = ROOT / "scripts" / "run_totp_mfa_e2e.sh"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_totp_mfa_e2e_scripts_have_valid_syntax() -> None:
    compile(_source(SEED), str(SEED), "exec")
    subprocess.run(["node", "--check", str(CHECK)], cwd=ROOT, check=True)
    subprocess.run(["bash", "-n", str(RUNNER)], cwd=ROOT, check=True)


def test_totp_mfa_browser_uses_real_chromium_without_artifacts_or_interception() -> None:
    source = _source(CHECK)

    assert '"frontend",\n  "node_modules",\n  "playwright-core"' in source
    assert 'const CHROMIUM_EXECUTABLE = "/usr/bin/chromium"' in source
    assert 'require("node:crypto")' in source
    assert 'createHmac("sha1"' in source
    assert "30_000" in source
    assert 'padStart(6, "0")' in source
    assert "freshTotpAfter" in source
    assert "page.setViewportSize({ height: 844, width: 390 })" in source
    assert '"mobile MFA transition"' in source
    assert 'page.locator("#mobile-utility-btn").click()' in source
    assert 'page.locator("#mobile-auth-btn").click()' in source
    assert "recoveryDisplaySummary" in source
    assert "invalidTotp" in source
    assert "response.status() === 401" in source
    assert 'page.on("pageerror"' in source
    for forbidden in (
        "page.route(",
        "browserContext.route(",
        "page.screenshot(",
        ".screenshot(",
        ".tracing",
        "recordVideo",
        "video:",
        "har:",
        'page.on("request',
        "page.on('request",
    ):
        assert forbidden not in source
    assert "recoveryOutput.inputValue" not in source
    assert "recoveryOutput.textContent" not in source
    assert "console.log" not in source
    assert 'context.route("**/*"' in source
    assert 'route.abort("blockedbyclient")' in source
    assert "context.routeWebSocket(" in source
    assert "route.continue()" in source
    assert ".fulfill(" not in source


def test_totp_mfa_seed_rejects_connection_override_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql://gardenops@127.0.0.1:55433/gardenops_test?hostaddr=127.0.0.2"
    monkeypatch.setenv("GARDENOPS_TOTP_MFA_E2E_CHILD", "1")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_MODE", "session")
    monkeypatch.setenv("AUTH_ADMIN_MFA_REQUIRED", "true")
    monkeypatch.setenv("GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("GARDENOPS_TOTP_MFA_E2E_ALLOW_TRUNCATE", "1")
    monkeypatch.setenv("AUTH_MFA_SECRET_KEY", "gardenops-totp-mfa-e2e-test-key-only-2026-07-10")
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_URL", database_url)
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "987654321.runner-issued-nonce")
    monkeypatch.setenv("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", "987654321")

    with pytest.raises(RuntimeError, match="exact TCP URL"):
        seed_totp_mfa_e2e.require_totp_mfa_e2e_database(database_url)


def test_sensitive_reauthentication_errors_are_handled_before_click_handlers_return() -> None:
    admin_source = _source(ROOT / "frontend" / "src" / "components" / "adminPanel.ts")
    app_source = _source(ROOT / "frontend" / "src" / "app.ts")

    for source in (admin_source, app_source):
        function_source = source.split("async function ", 1)[1]
        assert "await reauthenticateApi(currentPassword, reauthOptions);" in function_source
        assert 'showToast(getApiErrorMessage(err), "error");' in function_source
        assert "return null;" in function_source


def test_expected_authentication_rejections_do_not_expire_the_session_ui() -> None:
    api_source = _source(ROOT / "frontend" / "src" / "services" / "api.ts")

    assert "suppressAuthExpiry?: boolean;" in api_source
    assert "!options.suppressAuthExpiry && _onAuthExpired" in api_source
    assert api_source.count("{ suppressAuthExpiry: true }") >= 4


def test_totp_mfa_runner_is_disposable_private_and_scrubs_host_environment() -> None:
    source = _source(RUNNER)

    assert "umask 077" in source
    assert "run_fast_postgres_tests.py" in source
    assert "--command --command-database gardenops_test" in source
    assert "require_disposable_parent" in source
    assert "GARDENOPS_DISPOSABLE_POSTGRES_URL" in source
    assert "GARDENOPS_DISPOSABLE_POSTGRES_MARKER" in source
    assert "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER" in source
    assert "scrub_inherited_secrets" in source
    assert "env -i" in source
    assert "AUTH_MFA_SECRET_KEY" in source
    assert "AUTH_ADMIN_MFA_REQUIRED=true" in source
    assert "AUTH_MFA_TOTP_PERIOD_SECONDS=30" in source
    assert "AUTH_MFA_TOTP_DIGITS=6" in source
    assert "GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false" in source
    assert "GARDENOPS_ATTENTION_FROZEN_NOW_MS" in source
    assert "GARDENOPS_ATTENTION_FROZEN_DATE" in source
    assert "mktemp -d /tmp/gardenops-totp-mfa-e2e." in source
    assert 'chmod 700 "$RUNTIME_DIR"' in source
    assert 'chmod 600 "$MANIFEST_PATH"' in source
    assert "setsid env -i" in source
    assert 'kill -TERM -- "-$pid"' in source
    assert 'kill -KILL -- "-$pid"' in source
    assert "scripts/seed_totp_mfa_e2e.py snapshot" in source
    assert "page.screenshot" not in source
    assert "trace" not in source.lower()
    assert "video" not in source.lower()


def test_totp_mfa_seed_requires_all_destructive_safety_guards_and_emits_redacted_manifest() -> None:
    source = _source(SEED)
    snapshot_source = source[source.index("def snapshot(") : source.index("def main()")]

    for required in (
        "APP_ENV",
        "AUTH_REQUIRED",
        "AUTH_MODE",
        "AUTH_ADMIN_MFA_REQUIRED",
        "GARDENOPS_TOTP_MFA_E2E_ALLOW_TRUNCATE",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
        "current_setting(%s, true)",
        "pg_control_system()",
        "127.0.0.1",
        "parsed_port == 5432",
        "TRUNCATE TABLE",
    ):
        assert required in source
    assert "subscription_tier" in source
    assert '"planner" not in features_for_tier("pro")' in source
    assert "auth_passkeys" in source
    assert "mfa_totp_enabled" in source
    assert "auth_mfa_pending_enrollments" in snapshot_source
    assert "auth_mfa_recovery_codes" in snapshot_source
    assert "auth_sessions" in snapshot_source
    assert "audit_events" in snapshot_source
    assert "print(json.dumps(manifest, sort_keys=True))" in source
    assert "password" not in snapshot_source.lower()
    assert "secret_ciphertext" not in snapshot_source
    assert "code_hash" not in snapshot_source
    assert "provisioning_uri" not in snapshot_source
