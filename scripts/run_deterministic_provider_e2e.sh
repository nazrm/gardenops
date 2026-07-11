#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

die() {
  echo "$1" >&2
  exit 2
}

require_disposable_command_context() {
  local usage
  usage="Run only through: .venv/bin/python scripts/run_fast_postgres_tests.py --command -- bash scripts/run_deterministic_provider_e2e.sh"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_URL:-}" ]] || die "$usage"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_MARKER:-}" ]] || die "$usage"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER:-}" ]] || die "$usage"
  [[ "${APP_ENV:-}" == "test" ]] || die "Disposable command mode must provide APP_ENV=test. $usage"
  [[ "${DATABASE_URL:-}" == "$GARDENOPS_DISPOSABLE_POSTGRES_URL" ]] || die "DATABASE_URL must exactly match the runner-issued URL. $usage"
}

pick_loopback_port() {
  .venv/bin/python -c 'import socket; sock = socket.socket(); sock.bind(("127.0.0.1", 0)); print(sock.getsockname()[1]); sock.close()'
}

validate_local_port() {
  local port="$1"
  local label="$2"
  [[ "$port" =~ ^[0-9]+$ ]] || die "$label must be a numeric local port"
  ((port >= 1024 && port <= 65535 && port != 5432)) || die "$label must be a non-5432 local port"
}

require_disposable_command_context

unset \
  AI_API_BASE \
  AI_API_KEY \
  AI_BASE_URL \
  AI_PROXY \
  AI_PROVIDER_API_KEY \
  AI_PROVIDER_BASE_URL \
  AI_PROVIDER_KEY \
  AI_PROVIDER_PROXY \
  AI_PROVIDER_SECRET \
  AI_PROVIDER_TOKEN \
  AI_PROVIDER_URL \
  ALL_PROXY \
  ANTHROPIC_API_BASE \
  ANTHROPIC_API_KEY \
  ANTHROPIC_API_URL \
  ANTHROPIC_API_TOKEN \
  ANTHROPIC_AUTH_TOKEN \
  ANTHROPIC_BASE_URL \
  ANTHROPIC_HTTP_PROXY \
  ANTHROPIC_HTTPS_PROXY \
  ANTHROPIC_PROXY \
  HTTP_PROXY \
  HTTPS_PROXY \
  OPENAI_API_BASE \
  OPENAI_API_KEY \
  OPENAI_BASE_URL \
  OPENAI_HTTP_PROXY \
  OPENAI_HTTPS_PROXY \
  OPENAI_ORG_ID \
  OPENAI_PROJECT \
  OPENAI_PROXY \
  PLANTNET_API_KEY \
  PLANTNET_API_BASE \
  PLANTNET_BASE_URL \
  PLANTNET_PROXY \
  SHADEMAP \
  SHADEMAP_API_KEY \
  SHADEMAP_CLIENT_KEY \
  SHADEMAP_KEY \
  SHADEMAP_PUBLIC_API_KEY \
  SHADEMAP_PUBLIC_KEY \
  SHADEMAP_TILE_SIGNING_SECRET \
  TAILLIGHT_API_KEY \
  all_proxy \
  http_proxy \
  https_proxy

export APP_ENV=test
export AUTH_REQUIRED=true
export AUTH_MODE=session
export AUTH_CSRF_SECRET="deterministic-provider-e2e-csrf-test-secret"
export AUTH_ADMIN_MFA_REQUIRED=false
export AUTH_PASSWORD_CHECK_HIBP=false
export AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true
export DATABASE_URL="$GARDENOPS_DISPOSABLE_POSTGRES_URL"
export GARDENOPS_DETERMINISTIC_PROVIDER_E2E_ALLOW_TRUNCATE=1
export GARDENOPS_DETERMINISTIC_PROVIDER_E2E_USERNAME="${GARDENOPS_DETERMINISTIC_PROVIDER_E2E_USERNAME:-deterministic_provider_e2e_admin}"
export GARDENOPS_DETERMINISTIC_PROVIDER_E2E_PASSWORD="${GARDENOPS_DETERMINISTIC_PROVIDER_E2E_PASSWORD:-DeterministicProviderE2E!Passphrase2026}" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER=1
export GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false
export GARDENOPS_TEST_POSTGRES_URL="$DATABASE_URL"
export GARDENOPS_VITE_PROXY_TARGET=""
export INTERNET_EXPOSED=false
export NO_PROXY="127.0.0.1,localhost,::1"
export RATE_LIMIT_BACKEND=memory
export SECURITY_TELEMETRY_WEBHOOK_URL=""
export TAILLIGHT_URL=""
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/gardenops-uv-cache}"
export AI_PROVIDER=disabled

BACKEND_PORT="${GARDENOPS_DETERMINISTIC_PROVIDER_E2E_BACKEND_PORT:-$(pick_loopback_port)}"
FRONTEND_PORT="${GARDENOPS_DETERMINISTIC_PROVIDER_E2E_FRONTEND_PORT:-$(pick_loopback_port)}"
while [[ "$FRONTEND_PORT" == "$BACKEND_PORT" ]]; do
  FRONTEND_PORT="$(pick_loopback_port)"
done
validate_local_port "$BACKEND_PORT" "Backend port"
validate_local_port "$FRONTEND_PORT" "Frontend port"
export GARDENOPS_VITE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"

OUTPUT_DIR="$(mktemp -d /tmp/gardenops-deterministic-provider-e2e.XXXXXX)"
LOG_DIR="$OUTPUT_DIR/logs"
MANIFEST_PATH="$OUTPUT_DIR/manifest.json"
mkdir -p "$LOG_DIR"
chmod 700 "$OUTPUT_DIR" "$LOG_DIR"
export GARDENOPS_LOGS_DIR="$LOG_DIR"

BACKEND_PID=""
FRONTEND_PID=""
CLEANUP_POLL_ATTEMPTS=40
CLEANUP_POLL_SECONDS=0.25

stop_process_group() {
  local pid="$1"
  local label="$2"
  local attempt

  [[ -n "$pid" ]] || return
  if ! kill -0 -- "-$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return
  fi
  kill -TERM -- "-$pid" 2>/dev/null || true
  for ((attempt = 0; attempt < CLEANUP_POLL_ATTEMPTS; attempt++)); do
    if ! kill -0 -- "-$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep "$CLEANUP_POLL_SECONDS"
  done
  echo "$label process group did not stop after TERM; sending KILL" >&2
  kill -KILL -- "-$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  stop_process_group "$FRONTEND_PID" "frontend"
  stop_process_group "$BACKEND_PID" "backend"
  if [[ "$status" -ne 0 ]]; then
    echo "Deterministic-provider E2E failed; backend log tail:" >&2
    tail -n 80 "$LOG_DIR/backend.log" 2>/dev/null >&2 || true
    echo "Deterministic-provider E2E failed; frontend log tail:" >&2
    tail -n 80 "$LOG_DIR/frontend.log" 2>/dev/null >&2 || true
    echo "Private E2E output retained at $OUTPUT_DIR" >&2
  else
    cat "$MANIFEST_PATH"
    rm -rf "$OUTPUT_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_url() {
  local url="$1"
  local label="$2"
  local pid="$3"
  local attempt
  for ((attempt = 0; attempt < 120; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$label process exited before readiness" >&2
      return 1
    fi
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "Timed out waiting for $label at $url" >&2
  return 1
}

require_url_not_serving() {
  local url="$1"
  local label="$2"
  if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
    echo "$label is already serving at $url; choose a free E2E port" >&2
    exit 1
  fi
}

assert_backend_log_has_no_vendor_material() {
  local log_path="$1"
  if ! command -v grep >/dev/null 2>&1; then
    echo "grep is required to verify that backend logs contain no vendor material" >&2
    return 1
  fi
  if grep -E -n -i \
    -e 'api\.(openai|anthropic)\.com' \
    -e 'https?://[^[:space:]]*(openai|anthropic)' \
    -e '(openai|anthropic)[^[:space:]]*(api[_-]?key|token|secret)' \
    -e 'sk-(proj-)?[A-Za-z0-9_-]{16,}' \
    -e 'sk-ant-[A-Za-z0-9_-]{16,}' \
    "$log_path"; then
    echo "Backend log contained vendor host or key material" >&2
    return 1
  fi
}

require_url_not_serving "http://127.0.0.1:${BACKEND_PORT}/api/health" "FastAPI"
require_url_not_serving "http://127.0.0.1:${FRONTEND_PORT}/" "Vite"

.venv/bin/python scripts/seed_deterministic_provider_e2e.py >"$LOG_DIR/seed.json"
chmod 600 "$LOG_DIR/seed.json"

setsid .venv/bin/uvicorn gardenops.main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

setsid bash -c \
  'cd "$1" && exec npm run dev -- --host 127.0.0.1 --port "$2" --strictPort' \
  bash "$ROOT_DIR/frontend" "$FRONTEND_PORT" \
  >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

wait_for_url "http://127.0.0.1:${BACKEND_PORT}/api/health" "FastAPI health" "$BACKEND_PID"
wait_for_url "http://127.0.0.1:${FRONTEND_PORT}/" "Vite" "$FRONTEND_PID"

BASE_URL="http://127.0.0.1:${FRONTEND_PORT}" node scripts/check_deterministic_provider_e2e.cjs
.venv/bin/python scripts/seed_deterministic_provider_e2e.py snapshot >"$MANIFEST_PATH"
chmod 600 "$MANIFEST_PATH"
assert_backend_log_has_no_vendor_material "$LOG_DIR/backend.log"
