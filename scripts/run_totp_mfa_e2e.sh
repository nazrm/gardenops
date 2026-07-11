#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

die() {
  echo "TOTP MFA E2E: $1" >&2
  exit 2
}

if [[ "${1:-}" != "--child" ]]; then
  [[ "$#" -eq 0 ]] || die "usage: scripts/run_totp_mfa_e2e.sh"
  POSTGRES_BIN_DIR="$(pg_config --bindir 2>/dev/null || true)"
  [[ -x "$POSTGRES_BIN_DIR/initdb" ]] || die "PostgreSQL initdb is unavailable"
  exec env PATH="${POSTGRES_BIN_DIR}:${PATH}" "$ROOT_DIR/.venv/bin/python" scripts/run_fast_postgres_tests.py \
    --command --command-database gardenops_test -- \
    bash "$ROOT_DIR/scripts/run_totp_mfa_e2e.sh" --child
fi
[[ "$#" -eq 1 ]] || die "usage: scripts/run_totp_mfa_e2e.sh --child"

require_disposable_parent() {
  local parent_command=""
  [[ -r "/proc/${PPID}/cmdline" ]] || die "cannot verify disposable command runner"
  parent_command="$(tr '\0' ' ' < "/proc/${PPID}/cmdline")"
  [[ "$parent_command" == *"scripts/run_fast_postgres_tests.py"* ]] \
    || die "must run through run_fast_postgres_tests.py --command"
  [[ "$parent_command" == *"--command"* ]] \
    || die "must run through run_fast_postgres_tests.py --command"
}

require_disposable_runner_environment() {
  [[ "${APP_ENV:-}" == "test" ]] || die "runner must provide APP_ENV=test"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_URL:-}" ]] || die "runner-issued database URL is required"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_MARKER:-}" ]] || die "runner-issued database marker is required"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER:-}" ]] \
    || die "runner-issued database system identifier is required"
}

scrub_inherited_secrets() {
  local name ignored
  while IFS='=' read -r name ignored; do
    case "$name" in
      *API_KEY|*APIKEY|*TOKEN|*SECRET|*PASSWORD|*PASSWD|*CREDENTIAL*|*PRIVATE_KEY|\
      ANTHROPIC_*|AWS_*|AZURE_*|GCP_*|GOOGLE_*|OPENAI_*|PLANTNET_*|SHADEMAP_*|\
      SENTRY_*|TAILLIGHT_*|DEPLOYED_*|SSH_*|PGPASSWORD|PGPASSFILE|REDIS_*|DATABASE_URL)
        unset "$name"
        ;;
    esac
  done < <(env)
  unset ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY BASH_ENV ENV NODE_OPTIONS PYTHONHOME PYTHONPATH
}

pick_loopback_port() {
  "$ROOT_DIR/.venv/bin/python" -c '
import socket
while True:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    if port != 5432:
        print(port)
        break
'
}

require_disposable_parent
require_disposable_runner_environment
scrub_inherited_secrets

readonly TEST_MFA_SECRET_KEY="gardenops-totp-mfa-e2e-test-key-only-2026-07-10" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
readonly E2E_USERNAME="totp_mfa_e2e_admin"
readonly E2E_PASSWORD="TotpMfaE2E!StrongPassword2026" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export APP_ENV=test
export AUTH_REQUIRED=true
export AUTH_MODE=session
export AUTH_ADMIN_MFA_REQUIRED=true
export AUTH_MFA_SECRET_KEY="$TEST_MFA_SECRET_KEY"
export AUTH_CSRF_SECRET="totp-mfa-e2e-csrf-test-only-2026-07-10" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true
export AUTH_PASSWORD_CHECK_HIBP=false
export AUTH_SESSION_COOKIE_SECURE=false
export AUTH_SESSION_COOKIE_SAMESITE=lax
export AUTH_SESSION_COOKIE_DOMAIN=""
export AUTH_API_KEY=""
export AUTH_ADAPTIVE_FRICTION_MODE=off
export AUTH_MFA_TOTP_PERIOD_SECONDS=30
export AUTH_MFA_TOTP_DIGITS=6
export AUTH_MFA_TOTP_WINDOW_STEPS=1
export AUTH_MFA_RECOVERY_CODE_COUNT=10
export AUTH_PASSKEY_RP_ID=""
export AUTH_PASSKEY_ORIGINS=""
export INTERNET_EXPOSED=false
export MULTI_INSTANCE=false
export ALLOW_INSECURE_REMOTE=false
export RATE_LIMIT_BACKEND=memory
export AI_PROVIDER=disabled
export TAILLIGHT_URL=""
export TAILLIGHT_API_KEY=""
export SECURITY_TELEMETRY_ENABLED=false
export SECURITY_TELEMETRY_BEARER_TOKEN=""
export GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false
export GARDENOPS_TOTP_MFA_E2E_CHILD=1
export GARDENOPS_TOTP_MFA_E2E_ALLOW_TRUNCATE=1
export DATABASE_URL="$GARDENOPS_DISPOSABLE_POSTGRES_URL"
E2E_DATE="$(date -u +%F)"
E2E_EPOCH_SECONDS="$(date -u -d "${E2E_DATE}T12:00:00Z" +%s)"
export GARDENOPS_ATTENTION_FROZEN_NOW_MS="$((E2E_EPOCH_SECONDS * 1000))"
export GARDENOPS_ATTENTION_FROZEN_DATE="$E2E_DATE"

BACKEND_PORT="$(pick_loopback_port)"
FRONTEND_PORT="$(pick_loopback_port)"
while [[ "$FRONTEND_PORT" == "$BACKEND_PORT" ]]; do
  FRONTEND_PORT="$(pick_loopback_port)"
done
export GARDENOPS_VITE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"
export ALLOWED_HOSTS="localhost,127.0.0.1"
export CORS_ALLOW_ORIGINS="http://127.0.0.1:${FRONTEND_PORT}"

RUNTIME_DIR="$(mktemp -d /tmp/gardenops-totp-mfa-e2e.XXXXXX)"
LOG_DIR="$RUNTIME_DIR/logs"
MANIFEST_PATH="$RUNTIME_DIR/manifest.json"
mkdir -p "$LOG_DIR" "$RUNTIME_DIR/home" "$RUNTIME_DIR/tmp" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/config"
chmod 700 "$RUNTIME_DIR" "$LOG_DIR" "$RUNTIME_DIR/home" "$RUNTIME_DIR/tmp" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/config"
: > "$MANIFEST_PATH"
chmod 600 "$MANIFEST_PATH"
export GARDENOPS_LOGS_DIR="$LOG_DIR"

SAFE_PATH="/usr/local/bin:/usr/bin:/bin"
APP_ENVIRONMENT=(
  "PATH=$SAFE_PATH"
  "HOME=$RUNTIME_DIR/home"
  "TMPDIR=$RUNTIME_DIR/tmp"
  "XDG_CACHE_HOME=$RUNTIME_DIR/cache"
  "XDG_CONFIG_HOME=$RUNTIME_DIR/config"
  "PYTHONDONTWRITEBYTECODE=1"
  "PYTHONUNBUFFERED=1"
  "APP_ENV=$APP_ENV"
  "AUTH_REQUIRED=$AUTH_REQUIRED"
  "AUTH_MODE=$AUTH_MODE"
  "AUTH_ADMIN_MFA_REQUIRED=$AUTH_ADMIN_MFA_REQUIRED"
  "AUTH_MFA_SECRET_KEY=$AUTH_MFA_SECRET_KEY"
  "AUTH_CSRF_SECRET=$AUTH_CSRF_SECRET"
  "AUTH_PASSWORD_HASH_FAST_FOR_TESTS=$AUTH_PASSWORD_HASH_FAST_FOR_TESTS"
  "AUTH_PASSWORD_CHECK_HIBP=$AUTH_PASSWORD_CHECK_HIBP"
  "AUTH_SESSION_COOKIE_SECURE=$AUTH_SESSION_COOKIE_SECURE"
  "AUTH_SESSION_COOKIE_SAMESITE=$AUTH_SESSION_COOKIE_SAMESITE"
  "AUTH_SESSION_COOKIE_DOMAIN=$AUTH_SESSION_COOKIE_DOMAIN"
  "AUTH_API_KEY=$AUTH_API_KEY"
  "AUTH_ADAPTIVE_FRICTION_MODE=$AUTH_ADAPTIVE_FRICTION_MODE"
  "AUTH_MFA_TOTP_PERIOD_SECONDS=$AUTH_MFA_TOTP_PERIOD_SECONDS"
  "AUTH_MFA_TOTP_DIGITS=$AUTH_MFA_TOTP_DIGITS"
  "AUTH_MFA_TOTP_WINDOW_STEPS=$AUTH_MFA_TOTP_WINDOW_STEPS"
  "AUTH_MFA_RECOVERY_CODE_COUNT=$AUTH_MFA_RECOVERY_CODE_COUNT"
  "AUTH_PASSKEY_RP_ID=$AUTH_PASSKEY_RP_ID"
  "AUTH_PASSKEY_ORIGINS=$AUTH_PASSKEY_ORIGINS"
  "INTERNET_EXPOSED=$INTERNET_EXPOSED"
  "MULTI_INSTANCE=$MULTI_INSTANCE"
  "ALLOW_INSECURE_REMOTE=$ALLOW_INSECURE_REMOTE"
  "RATE_LIMIT_BACKEND=$RATE_LIMIT_BACKEND"
  "AI_PROVIDER=$AI_PROVIDER"
  "TAILLIGHT_URL=$TAILLIGHT_URL"
  "TAILLIGHT_API_KEY=$TAILLIGHT_API_KEY"
  "SECURITY_TELEMETRY_ENABLED=$SECURITY_TELEMETRY_ENABLED"
  "SECURITY_TELEMETRY_BEARER_TOKEN=$SECURITY_TELEMETRY_BEARER_TOKEN"
  "GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=$GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED"
  "GARDENOPS_ATTENTION_FROZEN_NOW_MS=$GARDENOPS_ATTENTION_FROZEN_NOW_MS"
  "GARDENOPS_ATTENTION_FROZEN_DATE=$GARDENOPS_ATTENTION_FROZEN_DATE"
  "GARDENOPS_TOTP_MFA_E2E_CHILD=$GARDENOPS_TOTP_MFA_E2E_CHILD"
  "GARDENOPS_TOTP_MFA_E2E_ALLOW_TRUNCATE=$GARDENOPS_TOTP_MFA_E2E_ALLOW_TRUNCATE"
  "GARDENOPS_DISPOSABLE_POSTGRES_URL=$GARDENOPS_DISPOSABLE_POSTGRES_URL"
  "GARDENOPS_DISPOSABLE_POSTGRES_MARKER=$GARDENOPS_DISPOSABLE_POSTGRES_MARKER"
  "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER=$GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"
  "DATABASE_URL=$DATABASE_URL"
  "GARDENOPS_LOGS_DIR=$GARDENOPS_LOGS_DIR"
  "GARDENOPS_VITE_PROXY_TARGET=$GARDENOPS_VITE_PROXY_TARGET"
  "ALLOWED_HOSTS=$ALLOWED_HOSTS"
  "CORS_ALLOW_ORIGINS=$CORS_ALLOW_ORIGINS"
)
FIXTURE_ENVIRONMENT=(
  "GARDENOPS_TOTP_MFA_E2E_USERNAME=$E2E_USERNAME"
  "GARDENOPS_TOTP_MFA_E2E_PASSWORD=$E2E_PASSWORD"
)

BACKEND_PID=""
FRONTEND_PID=""

stop_process_group() {
  local pid="$1"
  local attempt
  [[ -n "$pid" ]] || return 0
  if ! kill -0 -- "-$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return 0
  fi
  kill -TERM -- "-$pid" 2>/dev/null || true
  for ((attempt = 0; attempt < 40; attempt += 1)); do
    if ! kill -0 -- "-$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    sleep 0.25
  done
  kill -KILL -- "-$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  stop_process_group "$FRONTEND_PID"
  stop_process_group "$BACKEND_PID"
  if [[ "$status" -ne 0 ]]; then
    echo "TOTP MFA E2E failed; private logs and manifest are in $RUNTIME_DIR" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

wait_for_url() {
  local url="$1"
  local pid="$2"
  local label="$3"
  local attempt
  for ((attempt = 0; attempt < 120; attempt += 1)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      die "$label exited before readiness"
    fi
    if curl --noproxy '*' -fsS --max-time 1 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  die "$label did not become ready"
}

port_must_be_unused() {
  local url="$1"
  if curl --noproxy '*' -fsS --max-time 1 "$url" >/dev/null 2>&1; then
    die "selected local port is already serving"
  fi
}

port_must_be_unused "http://127.0.0.1:${BACKEND_PORT}/api/health"
port_must_be_unused "http://127.0.0.1:${FRONTEND_PORT}/"

env -i "${APP_ENVIRONMENT[@]}" "${FIXTURE_ENVIRONMENT[@]}" \
  "$ROOT_DIR/.venv/bin/python" scripts/seed_totp_mfa_e2e.py seed

setsid env -i "${APP_ENVIRONMENT[@]}" \
  "$ROOT_DIR/.venv/bin/uvicorn" gardenops.main:app \
  --host 127.0.0.1 --port "$BACKEND_PORT" \
  >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

setsid env -i "${APP_ENVIRONMENT[@]}" \
  bash -c 'cd "$1" && exec npm run dev -- --host 127.0.0.1 --port "$2" --strictPort' \
  bash "$ROOT_DIR/frontend" "$FRONTEND_PORT" \
  >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

wait_for_url "http://127.0.0.1:${BACKEND_PORT}/api/health" "$BACKEND_PID" "backend"
wait_for_url "http://127.0.0.1:${FRONTEND_PORT}/" "$FRONTEND_PID" "frontend"

env -i "${APP_ENVIRONMENT[@]}" "${FIXTURE_ENVIRONMENT[@]}" \
  "BASE_URL=http://127.0.0.1:${FRONTEND_PORT}/" \
  node scripts/check_totp_mfa_e2e.cjs \
  >"$LOG_DIR/browser.log" 2>&1

env -i "${APP_ENVIRONMENT[@]}" "${FIXTURE_ENVIRONMENT[@]}" \
  "$ROOT_DIR/.venv/bin/python" scripts/seed_totp_mfa_e2e.py snapshot \
  >"$MANIFEST_PATH"
chmod 600 "$MANIFEST_PATH"
echo "TOTP MFA E2E passed; private manifest: $MANIFEST_PATH"
