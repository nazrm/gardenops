#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

die() {
  echo "UI-flow map E2E: $1" >&2
  exit 2
}

unsafe_artifact_dir() {
  echo "Unsafe UI-flow E2E artifact directory: $1" >&2
  return 1
}

validate_artifact_dir() {
  local requested_path="$1"
  local research_dir="$ROOT_DIR/research"
  local resolved_research
  local resolved_artifact
  local segment
  local -a path_segments

  if [[ -z "$requested_path" ]]; then
    unsafe_artifact_dir "path must not be empty"
    return 1
  fi
  IFS='/' read -r -a path_segments <<< "$requested_path"
  for segment in "${path_segments[@]}"; do
    if [[ "$segment" == ".." ]]; then
      unsafe_artifact_dir "path traversal is not allowed"
      return 1
    fi
  done
  if [[ ! -d "$research_dir" || -L "$research_dir" ]]; then
    unsafe_artifact_dir "research/ must be a non-symlink directory"
    return 1
  fi
  if ! git -C "$ROOT_DIR" check-ignore -q -- research; then
    unsafe_artifact_dir "research/ must be gitignored"
    return 1
  fi
  if ! resolved_research="$(realpath -e -- "$research_dir")"; then
    unsafe_artifact_dir "could not resolve research/"
    return 1
  fi
  if ! resolved_artifact="$(realpath -m -- "$requested_path")"; then
    unsafe_artifact_dir "could not resolve the requested path"
    return 1
  fi
  case "$resolved_artifact" in
    "$resolved_research")
      unsafe_artifact_dir "research/ itself is not an artifact directory"
      return 1
      ;;
    "$resolved_research"/*)
      ;;
    *)
      unsafe_artifact_dir "path must resolve beneath research/"
      return 1
      ;;
  esac
  printf '%s\n' "$resolved_artifact"
}

validate_e2e_date() {
  local requested_date="$1"
  local epoch_seconds
  local selected_date

  selected_date="${requested_date:-$(date -u +%F)}"
  if ! epoch_seconds="$(date -u -d "${selected_date}T12:00:00Z" +%s 2>/dev/null)"; then
    die "GARDENOPS_UI_FLOW_E2E_DATE must use YYYY-MM-DD"
  fi
  if [[ "$(date -u -d "@${epoch_seconds}" +%F)" != "$selected_date" ]]; then
    die "GARDENOPS_UI_FLOW_E2E_DATE must be a valid UTC calendar date"
  fi
  printf '%s\n' "$selected_date"
}

validate_viewport() {
  case "$1" in
    all|desktop|mobile) printf '%s\n' "$1" ;;
    *) die "GARDENOPS_UI_FLOW_E2E_VIEWPORT must be all, desktop, or mobile" ;;
  esac
}

if [[ "${1:-}" == "--child" ]]; then
  [[ "$#" -eq 4 ]] || die "usage: scripts/run_ui_flow_map_e2e.sh --child ARTIFACT_DIR E2E_DATE VIEWPORT"
  ARTIFACT_DIR_INPUT="$2"
  E2E_DATE_INPUT="$3"
  VIEWPORT_INPUT="$4"
else
  [[ "$#" -eq 0 ]] || die "usage: scripts/run_ui_flow_map_e2e.sh"
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  ARTIFACT_DIR_INPUT="${GARDENOPS_UI_FLOW_E2E_ARTIFACT_DIR:-$ROOT_DIR/research/optimization-map/runs/ui-flow-$RUN_ID}"
  E2E_DATE_INPUT="${GARDENOPS_UI_FLOW_E2E_DATE:-}"
  VIEWPORT_INPUT="${GARDENOPS_UI_FLOW_E2E_VIEWPORT:-all}"
fi
if ! ARTIFACT_DIR="$(validate_artifact_dir "$ARTIFACT_DIR_INPUT")"; then
  exit 2
fi
E2E_DATE="$(validate_e2e_date "$E2E_DATE_INPUT")"
VIEWPORT="$(validate_viewport "$VIEWPORT_INPUT")"

if [[ "${1:-}" != "--child" ]]; then
  POSTGRES_BIN_DIR="$(pg_config --bindir 2>/dev/null || true)"
  [[ -x "$POSTGRES_BIN_DIR/initdb" ]] || die "PostgreSQL initdb is unavailable"
  exec env PATH="${POSTGRES_BIN_DIR}:${PATH}" "$ROOT_DIR/.venv/bin/python" scripts/run_fast_postgres_tests.py \
    --command --command-database gardenops_test -- \
    bash "$ROOT_DIR/scripts/run_ui_flow_map_e2e.sh" --child "$ARTIFACT_DIR" "$E2E_DATE" "$VIEWPORT"
fi

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

scrub_inherited_environment() {
  local name ignored
  while IFS='=' read -r name ignored; do
    case "$name" in
      *API_KEY|*APIKEY|*TOKEN|*SECRET|*PASSWORD|*PASSWD|*CREDENTIAL*|*PRIVATE_KEY|\
      ANTHROPIC_*|AWS_*|AZURE_*|COHERE_*|DATADOG_*|DD_*|DEPLOYED_*|GCP_*|\
      GEMINI_*|GOOGLE_*|HONEYCOMB_*|NEW_RELIC_*|OPENAI_*|OTEL_*|PLANTNET_*|\
      SENTRY_*|SHADEMAP_*|SSH_*|TAILLIGHT_*|PGPASSWORD|PGPASSFILE|REDIS_*|\
      DATABASE_URL)
        unset "$name"
        ;;
    esac
  done < <(env)
  unset ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY all_proxy http_proxy https_proxy no_proxy
  unset BASH_ENV ENV NODE_OPTIONS NODE_PATH NPM_CONFIG_USERCONFIG PYTHONHOME PYTHONPATH
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
scrub_inherited_environment

readonly E2E_ADMIN_USERNAME="ui_flow_map_e2e_admin"
readonly E2E_ADMIN_PASSWORD="UiFlowMapE2EAdmin!Passphrase2026" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
readonly E2E_EDITOR_USERNAME="ui_flow_map_e2e_editor"
readonly E2E_EDITOR_PASSWORD="UiFlowMapE2EEditor!Passphrase2026" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
readonly E2E_VIEWER_USERNAME="ui_flow_map_e2e_viewer"
readonly E2E_VIEWER_PASSWORD="UiFlowMapE2EViewer!Passphrase2026" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export APP_ENV=test
export AUTH_REQUIRED=true
export AUTH_MODE=session
export AUTH_CSRF_SECRET="ui-flow-map-e2e-csrf-test-only"
export AUTH_ADMIN_MFA_REQUIRED=false
export AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true
export AUTH_PASSWORD_CHECK_HIBP=false
export AUTH_SESSION_COOKIE_SECURE=false
export AUTH_SESSION_COOKIE_SAMESITE=lax
export AUTH_SESSION_COOKIE_DOMAIN=""
export AUTH_API_KEY=""
export AUTH_MFA_SECRET_KEY=""
export AUTH_ADAPTIVE_FRICTION_MODE=off
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
export GARDENOPS_UI_FLOW_MAP_E2E_CHILD=1
export GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE=1
export GARDENOPS_UI_FLOW_E2E_DATE="$E2E_DATE"
E2E_EPOCH_SECONDS="$(date -u -d "${E2E_DATE}T12:00:00Z" +%s)"
export GARDENOPS_ATTENTION_FROZEN_NOW_MS="$((E2E_EPOCH_SECONDS * 1000))"
export GARDENOPS_ATTENTION_FROZEN_DATE="$E2E_DATE"
export DATABASE_URL="$GARDENOPS_DISPOSABLE_POSTGRES_URL"

BACKEND_PORT="$(pick_loopback_port)"
FRONTEND_PORT="$(pick_loopback_port)"
while [[ "$FRONTEND_PORT" == "$BACKEND_PORT" ]]; do
  FRONTEND_PORT="$(pick_loopback_port)"
done
export GARDENOPS_VITE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"
export ALLOWED_HOSTS="localhost,127.0.0.1"
export CORS_ALLOW_ORIGINS="http://127.0.0.1:${FRONTEND_PORT}"

RUNTIME_DIR="$(mktemp -d /tmp/gardenops-ui-flow-map-e2e.XXXXXX)"
LOG_DIR="$RUNTIME_DIR/logs"
mkdir -p "$LOG_DIR" "$RUNTIME_DIR/home" "$RUNTIME_DIR/tmp" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/config"
mkdir -p "$ARTIFACT_DIR/screenshots" "$ARTIFACT_DIR/traces"
chmod 700 "$RUNTIME_DIR" "$LOG_DIR" "$RUNTIME_DIR/home" "$RUNTIME_DIR/tmp" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/config"
chmod 700 "$ARTIFACT_DIR" "$ARTIFACT_DIR/screenshots" "$ARTIFACT_DIR/traces"
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
  "GARDENOPS_UI_FLOW_E2E_VIEWPORT=$VIEWPORT"
  "APP_ENV=$APP_ENV"
  "AUTH_REQUIRED=$AUTH_REQUIRED"
  "AUTH_MODE=$AUTH_MODE"
  "AUTH_CSRF_SECRET=$AUTH_CSRF_SECRET"
  "AUTH_ADMIN_MFA_REQUIRED=$AUTH_ADMIN_MFA_REQUIRED"
  "AUTH_PASSWORD_HASH_FAST_FOR_TESTS=$AUTH_PASSWORD_HASH_FAST_FOR_TESTS"
  "AUTH_PASSWORD_CHECK_HIBP=$AUTH_PASSWORD_CHECK_HIBP"
  "AUTH_SESSION_COOKIE_SECURE=$AUTH_SESSION_COOKIE_SECURE"
  "AUTH_SESSION_COOKIE_SAMESITE=$AUTH_SESSION_COOKIE_SAMESITE"
  "AUTH_SESSION_COOKIE_DOMAIN=$AUTH_SESSION_COOKIE_DOMAIN"
  "AUTH_API_KEY=$AUTH_API_KEY"
  "AUTH_MFA_SECRET_KEY=$AUTH_MFA_SECRET_KEY"
  "AUTH_ADAPTIVE_FRICTION_MODE=$AUTH_ADAPTIVE_FRICTION_MODE"
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
  "GARDENOPS_UI_FLOW_MAP_E2E_CHILD=$GARDENOPS_UI_FLOW_MAP_E2E_CHILD"
  "GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE=$GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE"
  "GARDENOPS_UI_FLOW_E2E_DATE=$GARDENOPS_UI_FLOW_E2E_DATE"
  "GARDENOPS_ATTENTION_FROZEN_NOW_MS=$GARDENOPS_ATTENTION_FROZEN_NOW_MS"
  "GARDENOPS_ATTENTION_FROZEN_DATE=$GARDENOPS_ATTENTION_FROZEN_DATE"
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
  "GARDENOPS_UI_FLOW_E2E_USERNAME=$E2E_ADMIN_USERNAME"
  "GARDENOPS_UI_FLOW_E2E_PASSWORD=$E2E_ADMIN_PASSWORD"
  "GARDENOPS_UI_FLOW_E2E_EDITOR_USERNAME=$E2E_EDITOR_USERNAME"
  "GARDENOPS_UI_FLOW_E2E_EDITOR_PASSWORD=$E2E_EDITOR_PASSWORD"
  "GARDENOPS_UI_FLOW_E2E_VIEWER_USERNAME=$E2E_VIEWER_USERNAME"
  "GARDENOPS_UI_FLOW_E2E_VIEWER_PASSWORD=$E2E_VIEWER_PASSWORD"
  "GARDENOPS_UI_FLOW_E2E_ARTIFACT_DIR=$ARTIFACT_DIR"
)

BACKEND_PID=""
FRONTEND_PID=""
CLEANUP_POLL_ATTEMPTS=40
CLEANUP_POLL_SECONDS=0.25

stop_process_group() {
  local pid="$1"
  local label="$2"
  local attempt

  [[ -n "$pid" ]] || return 0
  if ! kill -0 -- "-$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return 0
  fi
  kill -TERM -- "-$pid" 2>/dev/null || true
  for ((attempt = 0; attempt < CLEANUP_POLL_ATTEMPTS; attempt++)); do
    if ! kill -0 -- "-$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    sleep "$CLEANUP_POLL_SECONDS"
  done
  echo "$label process group did not stop after TERM; sending KILL" >&2
  kill -KILL -- "-$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  stop_process_group "$FRONTEND_PID" "frontend"
  stop_process_group "$BACKEND_PID" "backend"
  if [[ "$status" -ne 0 ]]; then
    echo "UI-flow map E2E failed; private logs: $LOG_DIR" >&2
    echo "UI-flow map E2E artifacts: $ARTIFACT_DIR" >&2
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
  for ((attempt = 0; attempt < 120; attempt++)); do
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
  "$ROOT_DIR/.venv/bin/python" scripts/seed_ui_flow_map_e2e.py

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
  node scripts/check_ui_flow_map_e2e.cjs \
  >"$LOG_DIR/browser.log" 2>&1

env -i "${APP_ENVIRONMENT[@]}" "${FIXTURE_ENVIRONMENT[@]}" \
  "$ROOT_DIR/.venv/bin/python" scripts/seed_ui_flow_map_e2e.py snapshot \
  >"$ARTIFACT_DIR/traces/ui-flow-database-snapshot.json"
chmod 600 "$ARTIFACT_DIR/traces/ui-flow-database-snapshot.json"
echo "UI-flow map E2E passed; private logs: $LOG_DIR"
echo "UI-flow map E2E artifacts: $ARTIFACT_DIR"
