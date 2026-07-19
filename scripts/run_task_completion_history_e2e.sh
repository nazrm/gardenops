#!/usr/bin/env bash
set -euo pipefail

GARDENOPS_TASK_HISTORY_E2E_TEST_URL="${GARDENOPS_TASK_HISTORY_E2E_TEST_URL:-${DATABASE_URL:-}}"
if [[ -z "$GARDENOPS_TASK_HISTORY_E2E_TEST_URL" ]]; then
  echo "GARDENOPS_TASK_HISTORY_E2E_TEST_URL is required" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export APP_ENV=test
export AUTH_REQUIRED=false
export GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE=1
export GARDENOPS_ATTENTION_FROZEN_NOW_MS=1783252800000
export GARDENOPS_ATTENTION_FROZEN_DATE=2026-07-05
export GARDENOPS_LOGS_DIR="${GARDENOPS_LOGS_DIR:-/tmp/gardenops-task-history-e2e-logs}"
export DATABASE_URL="$GARDENOPS_TASK_HISTORY_E2E_TEST_URL"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/gardenops-uv-cache}"

BACKEND_PORT="${GARDENOPS_TASK_HISTORY_E2E_BACKEND_PORT:-8000}"
FRONTEND_PORT="${GARDENOPS_TASK_HISTORY_E2E_FRONTEND_PORT:-5173}"
export GARDENOPS_VITE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"
RUN_DB_AS_POSTGRES="${GARDENOPS_TASK_HISTORY_E2E_RUN_DB_AS_POSTGRES:-0}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  set +e
  if [[ -n "$FRONTEND_PID" ]]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$BACKEND_PID" ]]; then
    pkill -TERM -P "$BACKEND_PID" 2>/dev/null || true
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
    if [[ "${RUN_DB_AS_POSTGRES:-0}" == "1" ]]; then
      sudo -u postgres pkill -TERM -f "uvicorn gardenops.main:app --host 127.0.0.1 --port ${BACKEND_PORT}" 2>/dev/null || true
    fi
  fi
}
trap cleanup EXIT

wait_for_url() {
  local url="$1"
  local label="$2"
  local pid="$3"
  for _ in $(seq 1 120); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$label process exited before readiness" >&2
      exit 1
    fi
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "Timed out waiting for $label at $url" >&2
  exit 1
}

require_url_not_serving() {
  local url="$1"
  local label="$2"
  if curl -fsS "$url" >/dev/null 2>&1; then
    echo "$label is already serving at $url; choose a free E2E port" >&2
    exit 1
  fi
}

run_db_command() {
  if [[ "$RUN_DB_AS_POSTGRES" == "1" ]]; then
    sudo -u postgres env \
      APP_ENV="$APP_ENV" \
      AUTH_REQUIRED="$AUTH_REQUIRED" \
      GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE="$GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE" \
      GARDENOPS_ATTENTION_FROZEN_NOW_MS="$GARDENOPS_ATTENTION_FROZEN_NOW_MS" \
      GARDENOPS_ATTENTION_FROZEN_DATE="$GARDENOPS_ATTENTION_FROZEN_DATE" \
      GARDENOPS_LOGS_DIR="$GARDENOPS_LOGS_DIR" \
      DATABASE_URL="$DATABASE_URL" \
      UV_CACHE_DIR="${GARDENOPS_TASK_HISTORY_E2E_POSTGRES_UV_CACHE_DIR:-/tmp/gardenops-uv-cache-postgres}" \
      "$@"
    return
  fi
  "$@"
}

require_url_not_serving "http://127.0.0.1:${BACKEND_PORT}/api/health" "FastAPI"
require_url_not_serving "http://127.0.0.1:${FRONTEND_PORT}/" "Vite"

run_db_command uv run python scripts/seed_task_completion_history_e2e.py

run_db_command uv run uvicorn gardenops.main:app --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!

(
  cd frontend
  npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort
) &
FRONTEND_PID=$!

wait_for_url "http://127.0.0.1:${BACKEND_PORT}/api/health" "FastAPI health" "$BACKEND_PID"
wait_for_url "http://127.0.0.1:${FRONTEND_PORT}/" "Vite" "$FRONTEND_PID"

BASE_URL="http://127.0.0.1:${FRONTEND_PORT}" node scripts/check_task_completion_history_e2e.cjs
