#!/usr/bin/env -S -u BASH_ENV -u ENV -u PYTHONHOME -u PYTHONPATH -u NODE_OPTIONS -u NODE_PATH -u NPM_CONFIG_USERCONFIG bash
set -euo pipefail
umask 077

if [[ -n "${BASH_ENV:-}${ENV:-}${PYTHONHOME:-}${PYTHONPATH:-}${NODE_OPTIONS:-}${NODE_PATH:-}${NPM_CONFIG_USERCONFIG:-}" ]]; then
  printf 'Complete journey E2E: interpreter startup overrides are not allowed\n' >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"
MAX_IMPLEMENTED_PHASE=9

die() {
  printf 'Complete journey E2E: %s\n' "$1" >&2
  exit 2
}

usage() {
  die "usage: scripts/run_complete_journeys_e2e.sh --expected-head <40hex> (--phase N | --through-phase N)"
}

validate_expected_head() {
  local value="${1:-}"
  [[ "$value" =~ ^[0-9A-Fa-f]{40}$ ]] || die "expected head must be a 40-character hexadecimal commit"
  printf '%s\n' "${value,,}"
}

require_expected_head() {
  local expected="$1" observed
  observed="$(git rev-parse --verify HEAD 2>/dev/null)" \
    || die "could not resolve the review-gated repository HEAD"
  [[ "$observed" == "$expected" ]] \
    || die "review-gated HEAD mismatch: expected $expected, found $observed"
}

validate_phase() {
  local value="$1"
  [[ "$value" =~ ^[0-9]$ ]] || die "phase must be an integer from 0 to 9"
  printf '%s\n' "$value"
}

validate_artifact_dir() {
  local requested="$1"
  local research="$ROOT_DIR/research"
  local resolved_research resolved_requested segment
  local -a segments
  [[ -n "$requested" ]] || die "artifact path must not be empty"
  IFS='/' read -r -a segments <<< "$requested"
  for segment in "${segments[@]}"; do
    [[ "$segment" != ".." ]] || die "artifact path traversal is not allowed"
  done
  if [[ ! -e "$research" && ! -L "$research" ]]; then
    mkdir -- "$research" || true
  fi
  [[ -d "$research" && ! -L "$research" ]] || die "research/ must be a non-symlink directory"
  git check-ignore -q -- research || die "research/ must be gitignored"
  resolved_research="$(realpath -e -- "$research")" || die "could not resolve research/"
  resolved_requested="$(realpath -m -- "$requested")" || die "could not resolve artifact path"
  case "$resolved_requested" in
    "$resolved_research"/*) ;;
    *) die "artifact path must resolve below research/" ;;
  esac
  printf '%s\n' "$resolved_requested"
}

require_disposable_parent() {
  local parent_command=""
  [[ -r "/proc/${PPID}/cmdline" ]] || die "cannot verify disposable command runner"
  parent_command="$(tr '\0' ' ' < "/proc/${PPID}/cmdline")"
  [[ "$parent_command" == *"scripts/run_fast_postgres_tests.py"* ]] \
    || die "child must run through run_fast_postgres_tests.py"
  [[ "$parent_command" == *"--command"* ]] || die "disposable runner must use --command"
}

require_disposable_environment() {
  [[ "${APP_ENV:-}" == "test" ]] || die "disposable runner must provide APP_ENV=test"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_URL:-}" ]] || die "runner-issued database URL is required"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_MARKER:-}" ]] || die "runner-issued marker is required"
  [[ -n "${GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER:-}" ]] \
    || die "runner-issued PostgreSQL system identifier is required"
  [[ "$GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER" =~ ^[0-9]+$ ]] \
    || die "runner system identifier must be numeric"
  [[ "$GARDENOPS_DISPOSABLE_POSTGRES_MARKER" == "$GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER".* ]] \
    || die "runner marker is not bound to the PostgreSQL system identifier"
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
  while IFS='=' read -r name ignored; do
    case "$name" in
      VITE_*) unset "$name" ;;
    esac
  done < <(env)
}

verify_locked_dependencies() {
  local npm_userconfig="$PRIVATE_DIR/npmrc"
  : > "$npm_userconfig"
  chmod 600 "$npm_userconfig"
  command -v uv >/dev/null 2>&1 || die "uv is required to verify the locked Python environment"
  uv sync --locked --all-groups --check --no-config > "$LOG_DIR/uv-lock-verify.log" 2>&1 \
    || die "locked Python dependency verification failed"
  "$ROOT_DIR/.venv/bin/python" -m pip check > "$LOG_DIR/python-pip-check.log" 2>&1 \
    || die "installed Python dependency verification failed"
  (
    cd "$ROOT_DIR/frontend"
    env -i \
      HOME="$PRIVATE_DIR/home" \
      LANG="${LANG:-C.UTF-8}" \
      PATH="$PATH" \
      TMPDIR="$PRIVATE_DIR" \
      npm ci --dry-run --ignore-scripts --no-audit --no-fund --json --userconfig "$npm_userconfig"
  ) > "$LOG_DIR/npm-lock-verify.log" 2> "$LOG_DIR/npm-lock-verify.stderr.log" \
    || die "locked Node dependency verification failed"
  node -e '
    const fs = require("fs");
    const state = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    if (state.added !== 0 || state.changed !== 0 || state.removed !== 0) process.exit(1);
  ' "$LOG_DIR/npm-lock-verify.log" \
    || die "installed Node dependencies diverge from package-lock.json"
  export GARDENOPS_COMPLETE_JOURNEYS_E2E_LOCK_VERIFIED=true
}

write_isolated_vite_config() {
  VITE_CONFIG_PATH="$PRIVATE_DIR/complete-journeys-vite.config.mjs"
  VITE_ENV_DIR="$PRIVATE_DIR/vite-env"
  VITE_DIST_DIR="$PRIVATE_DIR/frontend-dist"
  mkdir -p "$VITE_ENV_DIR"
  chmod 700 "$VITE_ENV_DIR"
  printf '%s\n' \
    "import baseConfig from '$ROOT_DIR/frontend/vite.config.ts';" \
    "const root = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_ROOT;" \
    "const envDir = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_ENV_DIR;" \
    "const outDir = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_DIST_DIR;" \
    "const proxyTarget = process.env.GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_PROXY_TARGET;" \
    "const proxy = { '/api': proxyTarget, '/calendar/subscriptions': proxyTarget, '/shademap': proxyTarget };" \
    "export default async (configEnv) => {" \
    "  const resolved = typeof baseConfig === 'function' ? await baseConfig(configEnv) : baseConfig;" \
    "  return { ...resolved, root, envDir, build: { ...(resolved.build || {}), emptyOutDir: true, outDir }, preview: { ...(resolved.preview || {}), proxy }, server: { ...(resolved.server || {}), proxy } };" \
    "};" \
    > "$VITE_CONFIG_PATH"
  chmod 600 "$VITE_CONFIG_PATH"
}

vite_environment() {
  env -i \
    HOME="$PRIVATE_DIR/home" \
    LANG="${LANG:-C.UTF-8}" \
    PATH="$PATH" \
    TMPDIR="$PRIVATE_DIR" \
    GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_ROOT="$ROOT_DIR/frontend" \
    GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_ENV_DIR="$VITE_ENV_DIR" \
    GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_DIST_DIR="$VITE_DIST_DIR" \
    GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_PROXY_TARGET="$GARDENOPS_VITE_PROXY_TARGET" \
    GARDENOPS_VITE_PROXY_TARGET="$GARDENOPS_VITE_PROXY_TARGET" \
    VITE_SHADEMAP_BASEMAP_URL="$VITE_SHADEMAP_BASEMAP_URL" \
    "$@"
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

validate_port() {
  local port="$1" label="$2"
  [[ "$port" =~ ^[0-9]+$ ]] || die "$label must be numeric"
  ((port >= 1024 && port <= 65535 && port != 5432)) || die "$label must be a non-5432 port"
}

validate_distinct_ports() {
  local backend="$1" frontend="$2" provider="$3"
  validate_port "$backend" "backend port"
  validate_port "$frontend" "frontend port"
  validate_port "$provider" "provider port"
  [[ "$backend" != "$frontend" && "$backend" != "$provider" && "$frontend" != "$provider" ]] \
    || die "backend, frontend, and provider ports must differ"
}

stop_process_group() {
  local pid="$1" attempt
  [[ -n "$pid" ]] || return
  if ! kill -0 -- "-$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return
  fi
  kill -TERM -- "-$pid" 2>/dev/null || true
  for ((attempt = 0; attempt < 40; attempt++)); do
    if ! kill -0 -- "-$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep 0.25
  done
  kill -KILL -- "-$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

finish_private_dir() {
  local status="$1" private_dir="$2"
  if [[ "$status" -ne 0 ]]; then
    printf 'Private complete journey state retained at %s\n' "$private_dir" >&2
    return
  fi
  case "$private_dir" in
    /tmp/gardenops-complete-journeys.*) rm -rf -- "$private_dir" || return 1 ;;
    *) die "refusing unsafe private cleanup: $private_dir" ;;
  esac
}

wait_for_url() {
  local url="$1" pid="$2" label="$3" attempt
  for ((attempt = 0; attempt < 120; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      if [[ -n "${LOG_DIR:-}" && -d "$LOG_DIR" ]]; then
        printf 'label=%s\nurl=%s\nattempt=%s\nevent=process-exited\n' "$label" "$url" "$attempt" \
          > "$LOG_DIR/readiness-failure.log"
        chmod 600 "$LOG_DIR/readiness-failure.log"
      fi
      die "$label exited before readiness"
    fi
    if curl -fsS "$url" >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done
  if [[ -n "${LOG_DIR:-}" && -d "$LOG_DIR" ]]; then
    printf 'label=%s\nurl=%s\nattempts=%s\n' "$label" "$url" "$attempt" \
      > "$LOG_DIR/readiness-failure.log"
    chmod 600 "$LOG_DIR/readiness-failure.log"
  fi
  die "timed out waiting for $label"
}

if [[ "${1:-}" == "--self-test-ports" ]]; then
  [[ "$#" -eq 4 ]] || die "self-test ports require three values"
  validate_distinct_ports "$2" "$3" "$4"
  exit 0
fi
if [[ "${1:-}" == "--self-test-scrub" ]]; then
  [[ "$#" -eq 1 ]] || die "self-test scrub takes no values"
  scrub_inherited_environment
  [[ -z "${OPENAI_API_KEY:-}${ANTHROPIC_API_KEY:-}${DATABASE_URL:-}${BASH_ENV:-}${NODE_OPTIONS:-}${PYTHONPATH:-}" ]] \
    || die "environment scrub self-test failed"
  ! env | cut -d= -f1 | grep -q '^VITE_' \
    || die "Vite environment scrub self-test failed"
  exit 0
fi
if [[ "${1:-}" == "--self-test-process-group" ]]; then
  [[ "$#" -eq 1 ]] || die "self-test process group takes no values"
  "$ROOT_DIR/.venv/bin/python" -c \
    'import os; os.setsid(); os.execlp("sleep", "sleep", "30")' &
  probe_pid=$!
  sleep 0.1
  stop_process_group "$probe_pid"
  ! kill -0 -- "-$probe_pid" 2>/dev/null || die "process-group teardown self-test failed"
  exit 0
fi
if [[ "${1:-}" == "--self-test-cleanup" ]]; then
  [[ "$#" -eq 1 ]] || die "self-test cleanup takes no values"
  success_dir="$(mktemp -d /tmp/gardenops-complete-journeys.XXXXXX)"
  finish_private_dir 0 "$success_dir"
  [[ ! -e "$success_dir" ]] || die "successful cleanup self-test failed"
  failure_dir="$(mktemp -d /tmp/gardenops-complete-journeys.XXXXXX)"
  finish_private_dir 1 "$failure_dir"
  [[ -d "$failure_dir" ]] || die "failure retention self-test failed"
  finish_private_dir 0 "$failure_dir"
  exit 0
fi

PHASE=""
THROUGH_PHASE=""
ARTIFACT_INPUT=""
EXPECTED_HEAD=""
CHILD_MODE=0

if [[ "${1:-}" == "--child" ]]; then
  [[ "$#" -eq 6 && "${5:-}" == "--expected-head" ]] || usage
  CHILD_MODE=1
  PHASE="$(validate_phase "$2")"
  THROUGH_PHASE="$(validate_phase "$3")"
  ((THROUGH_PHASE >= PHASE)) || die "through phase must be greater than or equal to phase"
  ARTIFACT_INPUT="$4"
  EXPECTED_HEAD="$(validate_expected_head "$6")"
else
  [[ "$#" -eq 4 && "${1:-}" == "--expected-head" ]] || usage
  EXPECTED_HEAD="$(validate_expected_head "$2")"
  case "$3" in
    --phase)
      PHASE="$(validate_phase "$4")"
      THROUGH_PHASE="$PHASE"
      ;;
    --through-phase)
      THROUGH_PHASE="$(validate_phase "$4")"
      PHASE=0
      ;;
    *) usage ;;
  esac
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  ARTIFACT_INPUT="${GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR:-$ROOT_DIR/research/optimization-map/runs/complete-journeys/$RUN_ID}"
fi

ARTIFACT_DIR="$(validate_artifact_dir "$ARTIFACT_INPUT")"
((PHASE <= MAX_IMPLEMENTED_PHASE && THROUGH_PHASE <= MAX_IMPLEMENTED_PHASE)) \
  || die "requested phase is not implemented by this harness"
require_expected_head "$EXPECTED_HEAD"

if [[ "$CHILD_MODE" -eq 0 ]]; then
  ARTIFACT_PARENT="$(dirname -- "$ARTIFACT_DIR")"
  mkdir -p -- "$ARTIFACT_PARENT"
  RESOLVED_PARENT="$(realpath -e -- "$ARTIFACT_PARENT")"
  RESOLVED_RESEARCH="$(realpath -e -- "$ROOT_DIR/research")"
  case "$RESOLVED_PARENT" in
    "$RESOLVED_RESEARCH"/*) ;;
    *) die "artifact parent must resolve below research/" ;;
  esac
  mkdir -- "$ARTIFACT_DIR" || die "artifact directory must be newly created and empty"
  chmod 700 "$ARTIFACT_DIR"
  POSTGRES_BIN_DIR="$(pg_config --bindir 2>/dev/null || true)"
  [[ -x "$POSTGRES_BIN_DIR/initdb" ]] || die "PostgreSQL initdb is unavailable"
  exec env -i \
    HOME="${HOME:-/tmp}" \
    LANG="${LANG:-C.UTF-8}" \
    PATH="${POSTGRES_BIN_DIR}:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPYCACHEPREFIX="/tmp/gardenops-complete-journeys-parent-pycache" \
    TMPDIR="${TMPDIR:-/tmp}" \
    USER="${USER:-gardenops-e2e}" \
    GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD="$EXPECTED_HEAD" \
    "$ROOT_DIR/.venv/bin/python" scripts/run_fast_postgres_tests.py \
      --command --command-database gardenops_test -- \
      bash "$ROOT_DIR/scripts/run_complete_journeys_e2e.sh" \
        --child "$PHASE" "$THROUGH_PHASE" "$ARTIFACT_DIR" --expected-head "$EXPECTED_HEAD"
fi

require_expected_head "$EXPECTED_HEAD"
require_disposable_parent
require_disposable_environment
[[ -d "$ARTIFACT_DIR" && ! -L "$ARTIFACT_DIR" ]] \
  || die "child artifact directory must be a non-symlink directory"
[[ -z "$(find "$ARTIFACT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
  || die "child artifact directory must be empty before output creation"
DISPOSABLE_URL="$GARDENOPS_DISPOSABLE_POSTGRES_URL"
scrub_inherited_environment

export APP_ENV=test
export AUTH_REQUIRED=true
export AUTH_MODE=session
export AUTH_ADMIN_MFA_REQUIRED=false
export AUTH_PASSWORD_CHECK_HIBP=false
export AUTH_PASSWORD_HASH_FAST_FOR_TESTS=true
export AUTH_CSRF_SECRET="complete-journeys-e2e-csrf-only" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export AUTH_MFA_SECRET_KEY="complete-journeys-e2e-mfa-key-only" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export AUTH_FAIL_RATE_LIMIT=200
export AUTH_LOGIN_RATE_LIMIT=200
export AUTH_LOGIN_USERNAME_RATE_LIMIT=100
export AUTH_LOGIN_ADMIN_USERNAME_RATE_LIMIT=100
export AUTH_LOGIN_ADMIN_HOST_RATE_LIMIT=200
export AI_PROVIDER=openai
export OPENAI_API_KEY="complete-journeys-loopback-provider-key" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export GARDENOPS_E2E_LOOPBACK_PROVIDER=1
unset GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER
export GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED=false
export GARDENOPS_ATTENTION_FROZEN_NOW_MS=1783857600000
export GARDENOPS_ATTENTION_FROZEN_DATE=2026-07-12
export SECURITY_TELEMETRY_BACKGROUND_EXPORT=false
export INTERNET_EXPOSED=false
export GARDENOPS_WEATHER_EXTERNAL_FETCH_ENABLED=false
export RATE_LIMIT_BACKEND=memory
export DATABASE_URL="$DISPOSABLE_URL"
export NO_PROXY="localhost,127.0.0.1"
export no_proxy="$NO_PROXY"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD=1
export GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE=1
export GARDENOPS_COMPLETE_JOURNEYS_E2E_USERNAME="complete_journeys_e2e_admin"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_PASSWORD="CompleteJourneysE2E!Passphrase2026" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export GARDENOPS_COMPLETE_JOURNEYS_E2E_PHASE="$PHASE"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_THROUGH_PHASE="$THROUGH_PHASE"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR="$ARTIFACT_DIR"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD="$EXPECTED_HEAD"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_FRONTEND_MODE=production-preview
export PYTHON_DOTENV_DISABLED=1
# A data URL keeps the browser journey local while the product default remains OpenStreetMap.
VITE_SHADEMAP_BASEMAP_URL="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="

PRIVATE_DIR="$(mktemp -d /tmp/gardenops-complete-journeys.XXXXXX)"

early_cleanup() {
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    printf 'Complete journey setup failed; private diagnostics preserved at %s\n' \
      "$PRIVATE_DIR" >&2
  fi
  exit "$status"
}
trap early_cleanup EXIT

LOG_DIR="$PRIVATE_DIR/logs"
MEDIA_DIR="$PRIVATE_DIR/media"
TERRAIN_DIR="$PRIVATE_DIR/terrain"
DOWNLOAD_DIR="$PRIVATE_DIR/downloads"
mkdir -p "$PRIVATE_DIR/home" "$LOG_DIR" "$MEDIA_DIR" "$TERRAIN_DIR" "$DOWNLOAD_DIR"
chmod 700 "$PRIVATE_DIR" "$PRIVATE_DIR/home" "$LOG_DIR" "$MEDIA_DIR" "$TERRAIN_DIR" "$DOWNLOAD_DIR"

export HOME="$PRIVATE_DIR/home"
export XDG_CACHE_HOME="$PRIVATE_DIR/xdg-cache"
export XDG_CONFIG_HOME="$PRIVATE_DIR/xdg-config"
export XDG_DATA_HOME="$PRIVATE_DIR/xdg-data"
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
chmod 700 "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"

export GARDENOPS_LOGS_DIR="$LOG_DIR"
export MEDIA_STORAGE_DIR="$MEDIA_DIR"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_DOWNLOAD_DIR="$DOWNLOAD_DIR"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_MEDIA_DIR="$MEDIA_DIR"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_TERRAIN_DIR="$TERRAIN_DIR"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$PRIVATE_DIR/pycache"
export UV_CACHE_DIR="$PRIVATE_DIR/uv-cache"
mkdir -p "$UV_CACHE_DIR"
chmod 700 "$UV_CACHE_DIR"

BACKEND_PORT="${GARDENOPS_COMPLETE_JOURNEYS_E2E_BACKEND_PORT:-$(pick_loopback_port)}"
FRONTEND_PORT="${GARDENOPS_COMPLETE_JOURNEYS_E2E_FRONTEND_PORT:-$(pick_loopback_port)}"
PROVIDER_PORT="${GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_PORT:-$(pick_loopback_port)}"
validate_distinct_ports "$BACKEND_PORT" "$FRONTEND_PORT" "$PROVIDER_PORT"
export GARDENOPS_VITE_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"
export GARDENOPS_E2E_PROVIDER_URL="http://127.0.0.1:${PROVIDER_PORT}/v1"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_PROVIDER_URL="$GARDENOPS_E2E_PROVIDER_URL"
export SHADEMAP="complete-journeys-loopback-shademap-key" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export SHADEMAP_PUBLIC_API_KEY="complete-journeys-loopback-shademap-public-key" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export SHADEMAP_TILE_SIGNING_SECRET="complete-journeys-loopback-terrain-signing-key" # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
export AUTH_PASSKEY_RP_ID="localhost"
export AUTH_PASSKEY_ORIGINS="http://localhost:${FRONTEND_PORT}"

FIXTURE_PATH="$ARTIFACT_DIR/fixture.json"
export GARDENOPS_COMPLETE_JOURNEYS_E2E_FIXTURE_PATH="$FIXTURE_PATH"
BACKEND_PID=""
FRONTEND_PID=""
PROVIDER_PID=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  stop_process_group "$FRONTEND_PID"
  stop_process_group "$BACKEND_PID"
  stop_process_group "$PROVIDER_PID"
  if [[ "$status" -ne 0 ]]; then
    printf 'Private failure artifacts: %s and %s\n' "$ARTIFACT_DIR" "$PRIVATE_DIR" >&2
    finish_private_dir "$status" "$PRIVATE_DIR"
    exit "$status"
  fi
  finish_private_dir 0 "$PRIVATE_DIR" || exit 1
  exit 0
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "${GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD:-}" == "$EXPECTED_HEAD" ]] \
  || die "review-gated expected HEAD was not propagated to the disposable child"
require_expected_head "$EXPECTED_HEAD"
verify_locked_dependencies
write_isolated_vite_config

"$ROOT_DIR/.venv/bin/python" scripts/seed_complete_journeys_e2e.py \
  --output "$FIXTURE_PATH"
chmod 600 "$FIXTURE_PATH"
export GARDENOPS_E2E_SHADEMAP_ESTIMATE_CSV="$ARTIFACT_DIR/phase-seven-sun.csv"

setsid node "$ROOT_DIR/scripts/e2e/providers/deterministicLoopbackProvider.cjs" \
  --port "$PROVIDER_PORT" \
  --scenario success \
  > "$LOG_DIR/provider-fixture.log" 2>&1 &
PROVIDER_PID=$!

BACKEND_APPLICATION="gardenops.main:app"
if ((THROUGH_PHASE >= 9)); then
  BACKEND_APPLICATION="scripts.e2e.performanceFastapiApp:app"
  export GARDENOPS_PERFORMANCE_QUERY_EVIDENCE_PATH="$ARTIFACT_DIR/phase-nine-query-evidence.jsonl"
fi
setsid "$ROOT_DIR/.venv/bin/uvicorn" "$BACKEND_APPLICATION" \
  --host 127.0.0.1 --port "$BACKEND_PORT" > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
vite_environment "$ROOT_DIR/frontend/node_modules/.bin/vite" build \
  --config "$VITE_CONFIG_PATH" > "$LOG_DIR/frontend-build.log" 2>&1 \
  || die "production frontend build failed"
setsid env -i \
  HOME="$PRIVATE_DIR/home" \
  LANG="${LANG:-C.UTF-8}" \
  PATH="$PATH" \
  TMPDIR="$PRIVATE_DIR" \
  GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_ROOT="$ROOT_DIR/frontend" \
  GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_ENV_DIR="$VITE_ENV_DIR" \
  GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_DIST_DIR="$VITE_DIST_DIR" \
  GARDENOPS_COMPLETE_JOURNEYS_E2E_VITE_PROXY_TARGET="$GARDENOPS_VITE_PROXY_TARGET" \
  GARDENOPS_VITE_PROXY_TARGET="$GARDENOPS_VITE_PROXY_TARGET" \
  VITE_SHADEMAP_BASEMAP_URL="$VITE_SHADEMAP_BASEMAP_URL" \
  "$ROOT_DIR/frontend/node_modules/.bin/vite" preview \
    --config "$VITE_CONFIG_PATH" --host localhost --port "$FRONTEND_PORT" --strictPort \
    > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

wait_for_url "http://127.0.0.1:${BACKEND_PORT}/api/health" "$BACKEND_PID" "FastAPI"
wait_for_url "http://127.0.0.1:${PROVIDER_PORT}/healthz" "$PROVIDER_PID" "loopback provider"
wait_for_url "http://localhost:${FRONTEND_PORT}/" "$FRONTEND_PID" "production frontend preview"

BASE_URL="http://localhost:${FRONTEND_PORT}" \
  node scripts/check_complete_journeys_e2e.cjs
chmod 600 "$ARTIFACT_DIR/complete-journeys-manifest.json"
find "$ARTIFACT_DIR" -maxdepth 1 -type f -name '*.zip' -exec chmod 600 {} +
