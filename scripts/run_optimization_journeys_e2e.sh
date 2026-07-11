#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

if [[ "${GARDENOPS_ALLOW_DESTRUCTIVE_E2E:-}" != "1" ]]; then
  fail "GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1 is required"
fi
if [[ -z "${GARDENOPS_DISPOSABLE_POSTGRES_URL:-}" ]]; then
  fail "Run only via scripts/run_fast_postgres_tests.py --command; runner-issued database URL is required"
fi
if [[ -z "${GARDENOPS_DISPOSABLE_POSTGRES_MARKER:-}" ]]; then
  fail "Run only via scripts/run_fast_postgres_tests.py --command; runner-issued database marker is required"
fi
if [[ -z "${GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER:-}" ]]; then
  fail "Run only via scripts/run_fast_postgres_tests.py --command; runner system identifier is required"
fi
if [[ ! -x "$ROOT_DIR/.venv/bin/python" || ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
  fail "Project virtual environment with Python and uvicorn is required"
fi
if [[ ! -x /usr/bin/chromium ]]; then
  fail "/usr/bin/chromium is required"
fi

# Never allow host credentials or an external integration to reach this disposable run.
unset OPENAI_API_KEY OPENAI_ORG_ID OPENAI_PROJECT_ID OPENAI_BASE_URL
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
unset PLANTNET_API_KEY PLANTNET_BASE_URL
unset SHADEMAP SHADEMAP_API_KEY SHADEMAP_KEY SHADEMAP_PUBLIC_API_KEY
unset SHADEMAP_PUBLIC_KEY SHADEMAP_CLIENT_KEY SHADEMAP_TILE_SIGNING_SECRET
unset SHADEMAP_TERRAIN_URL_TEMPLATE SHADEMAP_OVERPASS_URL SHADEMAP_OVERPASS_URLS
unset SHADEMAP_SHARE_URL SHADEMAP_LOCAL_TERRAIN_PATH
unset TAILLIGHT_URL TAILLIGHT_API_KEY
unset AUTH_API_KEY AUTH_MFA_SECRET_KEY AUTH_CSRF_SECRET AUTH_SESSION_SECRET
unset AUTH_JWT_SECRET AUTH_COOKIE_SECRET DEPLOYED_READINESS_ADMIN_BEARER_TOKEN
unset SECURITY_TELEMETRY_BEARER_TOKEN SECURITY_TELEMETRY_WEBHOOK_URL
unset SECURITY_TELEMETRY_PRIVACY_SALT GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

export APP_ENV=test
export AUTH_REQUIRED=true
export AUTH_MODE=session
export AUTH_ADMIN_MFA_REQUIRED=false
export AUTH_PASSWORD_CHECK_HIBP=false
export AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true
export AUTH_CSRF_SECRET="optimization-journeys-e2e-local-csrf-only" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export AI_PROVIDER=disabled
export GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false
export SECURITY_TELEMETRY_BACKGROUND_EXPORT=false
export INTERNET_EXPOSED=false
export RATE_LIMIT_BACKEND=memory
export TAILLIGHT_URL=""
export TAILLIGHT_API_KEY=""
export NO_PROXY="localhost,127.0.0.1"
export no_proxy="$NO_PROXY"
export GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE=1
export GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_USERNAME="optimization_journeys_e2e_admin"
export GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PASSWORD="OptimizationJourneysE2E!Passphrase2026" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export DATABASE_URL="$GARDENOPS_DISPOSABLE_POSTGRES_URL"

PRIVATE_DIR="$(mktemp -d /tmp/gardenops-optimization-journeys.XXXXXX)"
ARTIFACT_DIR="$PRIVATE_DIR/artifacts"
LOG_DIR="$PRIVATE_DIR/logs"
MEDIA_DIR="$PRIVATE_DIR/media"
mkdir -p "$ARTIFACT_DIR" "$LOG_DIR" "$MEDIA_DIR"
chmod 700 "$PRIVATE_DIR" "$ARTIFACT_DIR" "$LOG_DIR" "$MEDIA_DIR"
export GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ARTIFACT_DIR="$ARTIFACT_DIR"
export GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PYTHON="$ROOT_DIR/.venv/bin/python"
export GARDENOPS_LOGS_DIR="$LOG_DIR"
export MEDIA_STORAGE_DIR="$MEDIA_DIR"
export UV_CACHE_DIR="$PRIVATE_DIR/uv-cache"
mkdir -p "$UV_CACHE_DIR"
chmod 700 "$UV_CACHE_DIR"

pick_loopback_port() {
  "$ROOT_DIR/.venv/bin/python" -c '
import socket
while True:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    if port != 5432:
        print(port)
        break
'
}

validate_local_port() {
  local port="$1"
  local label="$2"
  if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1024 || port > 65535 || port == 5432)); then
    fail "$label must be a non-5432 local TCP port"
  fi
}

BACKEND_PORT="${GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_BACKEND_PORT:-$(pick_loopback_port)}"
FRONTEND_PORT="${GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_FRONTEND_PORT:-$(pick_loopback_port)}"
validate_local_port "$BACKEND_PORT" "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_BACKEND_PORT"
validate_local_port "$FRONTEND_PORT" "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_FRONTEND_PORT"
if [[ "$BACKEND_PORT" == "$FRONTEND_PORT" ]]; then
  fail "Backend and frontend E2E ports must differ"
fi
export GARDENOPS_VITE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"

BACKEND_PID=""
FRONTEND_PID=""
CLEANUP_POLL_ATTEMPTS=40
CLEANUP_POLL_SECONDS=0.25

stop_process_group() {
  local pid="$1"
  local attempt
  if [[ -z "$pid" ]]; then
    return
  fi
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
  kill -KILL -- "-$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  stop_process_group "$FRONTEND_PID"
  stop_process_group "$BACKEND_PID"
  if [[ "$status" -ne 0 ]]; then
    if [[ -f "$ARTIFACT_DIR/optimization-journeys-manifest.json" ]]; then
      cat "$ARTIFACT_DIR/optimization-journeys-manifest.json" >&2
    fi
    printf '%s\n' 'Optimization journey backend log tail:' >&2
    tail -n 80 "$LOG_DIR/backend.log" 2>/dev/null >&2 || true
    printf '%s\n' 'Optimization journey frontend log tail:' >&2
    tail -n 80 "$LOG_DIR/frontend.log" 2>/dev/null >&2 || true
    printf 'Private optimization journey artifacts preserved at %s\n' "$PRIVATE_DIR" >&2
    exit "$status"
  fi
  if [[ "$PRIVATE_DIR" == /tmp/gardenops-optimization-journeys.* ]]; then
    rm -rf -- "$PRIVATE_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_url() {
  local url="$1"
  local pid="$2"
  local label="$3"
  local attempt
  for ((attempt = 0; attempt < 120; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      printf '%s exited before readiness\n' "$label" >&2
      return 1
    fi
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  printf 'Timed out waiting for %s\n' "$label" >&2
  return 1
}

require_url_not_serving() {
  local url="$1"
  local label="$2"
  if curl -fsS "$url" >/dev/null 2>&1; then
    fail "$label is already serving at the requested local E2E port"
  fi
}

require_url_not_serving "http://127.0.0.1:${BACKEND_PORT}/api/health" "FastAPI"
require_url_not_serving "http://127.0.0.1:${FRONTEND_PORT}/" "Vite"

"$ROOT_DIR/.venv/bin/python" scripts/seed_optimization_journeys_e2e.py >/dev/null

setsid "$ROOT_DIR/.venv/bin/uvicorn" gardenops.main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

setsid bash -c \
  'cd "$1" && exec npm run dev -- --host 127.0.0.1 --port "$2" --strictPort' \
  bash "$ROOT_DIR/frontend" "$FRONTEND_PORT" \
  >"$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

wait_for_url "http://127.0.0.1:${BACKEND_PORT}/api/health" "$BACKEND_PID" "FastAPI"
wait_for_url "http://127.0.0.1:${FRONTEND_PORT}/" "$FRONTEND_PID" "Vite"

BASE_URL="http://127.0.0.1:${FRONTEND_PORT}" node scripts/check_optimization_journeys_e2e.cjs
chmod 600 "$ARTIFACT_DIR/optimization-journeys-manifest.json"
