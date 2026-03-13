#!/usr/bin/env bash
# Docs: scripts/deploy_light_readme.md
# Lightweight background supervisor for one local OpenCode + A2A instance.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ACTION="${1:-}"
if [[ -n "$ACTION" ]]; then
  shift
fi

INSTANCE="default"
WORKDIR=""

A2A_HOST="127.0.0.1"
A2A_PORT="8000"
A2A_PUBLIC_URL=""
A2A_PROJECT=""
A2A_LOG_LEVEL="INFO"
A2A_STREAMING="true"
A2A_LOG_PAYLOADS="false"
A2A_LOG_BODY_LIMIT="0"
A2A_ALLOW_DIRECTORY_OVERRIDE="true"
A2A_ENABLE_SESSION_SHELL="false"
A2A_CANCEL_ABORT_TIMEOUT_SECONDS="2.0"

OPENCODE_BIN="opencode"
OPENCODE_BIND_HOST="127.0.0.1"
OPENCODE_BIND_PORT="4096"
OPENCODE_LOG_LEVEL="WARNING"
OPENCODE_PROVIDER_ID=""
OPENCODE_MODEL_ID=""
OPENCODE_LSP="false"
OPENCODE_EXTRA_ARGS=""
OPENCODE_TIMEOUT=""
OPENCODE_TIMEOUT_STREAM=""

LOG_ROOT="${ROOT_DIR}/logs/light"
RUN_ROOT="${ROOT_DIR}/run/light"
START_TIMEOUT_SECONDS="20"

usage() {
  cat <<'USAGE'
Usage:
  A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh start workdir=/abs/path [instance=dev] [a2a_host=127.0.0.1] [a2a_port=8000] [a2a_public_url=http://127.0.0.1:8000] [a2a_project=<name>] [a2a_log_level=INFO] [a2a_streaming=true] [a2a_log_payloads=false] [a2a_log_body_limit=0] [a2a_allow_directory_override=true] [a2a_enable_session_shell=false] [a2a_cancel_abort_timeout_seconds=2.0] [opencode_bin=opencode] [opencode_bind_host=127.0.0.1] [opencode_bind_port=4096] [opencode_log_level=WARNING] [opencode_provider_id=<id>] [opencode_model_id=<id>] [opencode_lsp=false] [opencode_extra_args='...'] [opencode_timeout=120] [opencode_timeout_stream=300] [log_root=./logs/light] [run_root=./run/light] [start_timeout_seconds=20]
  ./scripts/deploy_light.sh stop [instance=dev] [run_root=./run/light]
  ./scripts/deploy_light.sh status [instance=dev] [run_root=./run/light] [log_root=./logs/light]
  A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh restart workdir=/abs/path [instance=dev]
USAGE
}

die() {
  echo "$*" >&2
  exit 1
}

for arg in "$@"; do
  if [[ "$arg" != *=* ]]; then
    die "Unknown argument format: $arg (expected key=value)"
  fi
  key="${arg%%=*}"
  value="${arg#*=}"
  case "${key,,}" in
    instance)
      INSTANCE="$value"
      ;;
    workdir)
      WORKDIR="$value"
      ;;
    a2a_host)
      A2A_HOST="$value"
      ;;
    a2a_port)
      A2A_PORT="$value"
      ;;
    a2a_public_url)
      A2A_PUBLIC_URL="$value"
      ;;
    a2a_project)
      A2A_PROJECT="$value"
      ;;
    a2a_log_level)
      A2A_LOG_LEVEL="$value"
      ;;
    a2a_streaming)
      A2A_STREAMING="$value"
      ;;
    a2a_log_payloads)
      A2A_LOG_PAYLOADS="$value"
      ;;
    a2a_log_body_limit)
      A2A_LOG_BODY_LIMIT="$value"
      ;;
    a2a_allow_directory_override)
      A2A_ALLOW_DIRECTORY_OVERRIDE="$value"
      ;;
    a2a_enable_session_shell)
      A2A_ENABLE_SESSION_SHELL="$value"
      ;;
    a2a_cancel_abort_timeout_seconds)
      A2A_CANCEL_ABORT_TIMEOUT_SECONDS="$value"
      ;;
    opencode_bin)
      OPENCODE_BIN="$value"
      ;;
    opencode_bind_host)
      OPENCODE_BIND_HOST="$value"
      ;;
    opencode_bind_port)
      OPENCODE_BIND_PORT="$value"
      ;;
    opencode_log_level)
      OPENCODE_LOG_LEVEL="$value"
      ;;
    opencode_provider_id)
      OPENCODE_PROVIDER_ID="$value"
      ;;
    opencode_model_id)
      OPENCODE_MODEL_ID="$value"
      ;;
    opencode_lsp)
      OPENCODE_LSP="$value"
      ;;
    opencode_extra_args)
      OPENCODE_EXTRA_ARGS="$value"
      ;;
    opencode_timeout)
      OPENCODE_TIMEOUT="$value"
      ;;
    opencode_timeout_stream)
      OPENCODE_TIMEOUT_STREAM="$value"
      ;;
    log_root)
      LOG_ROOT="$value"
      ;;
    run_root)
      RUN_ROOT="$value"
      ;;
    start_timeout_seconds)
      START_TIMEOUT_SECONDS="$value"
      ;;
    *)
      die "Unknown argument: $arg"
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  usage
  exit 1
fi

if [[ -z "$A2A_PUBLIC_URL" ]]; then
  A2A_PUBLIC_URL="http://${A2A_HOST}:${A2A_PORT}"
fi

INSTANCE_RUN_DIR="${RUN_ROOT}/${INSTANCE}"
INSTANCE_LOG_DIR="${LOG_ROOT}/${INSTANCE}"

OPENCODE_PID_FILE="${INSTANCE_RUN_DIR}/opencode.pid"
A2A_PID_FILE="${INSTANCE_RUN_DIR}/a2a.pid"
METADATA_FILE="${INSTANCE_RUN_DIR}/metadata.env"

OPENCODE_LOG_FILE="${INSTANCE_LOG_DIR}/opencode.log"
A2A_LOG_FILE="${INSTANCE_LOG_DIR}/a2a.log"

normalize_probe_host() {
  case "$1" in
    0.0.0.0|"::"|"[::]"|"")
      echo "127.0.0.1"
      ;;
    *)
      echo "$1"
      ;;
  esac
}

format_http_host() {
  local host="$1"
  if [[ "$host" == *:* && "$host" != \[*\] ]]; then
    printf '[%s]\n' "$host"
  else
    printf '%s\n' "$host"
  fi
}

refresh_probe_urls() {
  OPENCODE_PROBE_HOST="$(normalize_probe_host "$OPENCODE_BIND_HOST")"
  A2A_PROBE_HOST="$(normalize_probe_host "$A2A_HOST")"
  OPENCODE_READY_URL="http://$(format_http_host "$OPENCODE_PROBE_HOST"):${OPENCODE_BIND_PORT}/session"
  A2A_READY_URL="http://$(format_http_host "$A2A_PROBE_HOST"):${A2A_PORT}/.well-known/agent-card.json"
}

refresh_probe_urls

read_pid() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  printf '%s\n' "$pid"
}

pid_matches_tokens() {
  local pid="$1"
  shift
  if [[ -z "$pid" ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    return 1
  fi
  local cmdline
  cmdline="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  if [[ -z "$cmdline" ]]; then
    return 1
  fi
  local token
  for token in "$@"; do
    if [[ "$cmdline" != *"$token"* ]]; then
      return 1
    fi
  done
  return 0
}

opencode_pid() {
  read_pid "$OPENCODE_PID_FILE"
}

a2a_pid() {
  read_pid "$A2A_PID_FILE"
}

is_opencode_running() {
  local pid
  pid="$(opencode_pid 2>/dev/null || true)"
  pid_matches_tokens "$pid" "opencode" "serve"
}

is_a2a_running() {
  local pid
  pid="$(a2a_pid 2>/dev/null || true)"
  pid_matches_tokens "$pid" "opencode-a2a-server"
}

cleanup_stale_pidfiles() {
  if ! is_opencode_running; then
    rm -f "$OPENCODE_PID_FILE"
  fi
  if ! is_a2a_running; then
    rm -f "$A2A_PID_FILE"
  fi
}

validate_port() {
  local label="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || ((value < 1 || value > 65535)); then
    die "${label} must be an integer between 1 and 65535; got: ${value}"
  fi
}

validate_non_negative_int() {
  local label="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    die "${label} must be a non-negative integer; got: ${value}"
  fi
}

validate_bool() {
  local label="$1"
  local value="$2"
  case "${value,,}" in
    1|0|true|false|yes|no|on|off)
      ;;
    *)
      die "${label} must be one of: true/false/1/0/yes/no/on/off; got: ${value}"
      ;;
  esac
}

resolve_opencode_bin() {
  if [[ "$OPENCODE_BIN" == */* ]]; then
    [[ -x "$OPENCODE_BIN" ]] || die "opencode binary not executable: $OPENCODE_BIN"
    printf '%s\n' "$OPENCODE_BIN"
    return 0
  fi
  if command -v "$OPENCODE_BIN" >/dev/null 2>&1; then
    command -v "$OPENCODE_BIN"
    return 0
  fi
  if [[ "$OPENCODE_BIN" == "opencode" && -x "$HOME/.opencode/bin/opencode" ]]; then
    printf '%s\n' "$HOME/.opencode/bin/opencode"
    return 0
  fi
  die "opencode binary not found in PATH: $OPENCODE_BIN"
}

run_python_http_check() {
  local url="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=1.5) as response:
    if response.status != 200:
        raise SystemExit(1)
    body = response.read().decode("utf-8")
    if body:
        json.loads(body)
PY
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    python - "$url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=1.5) as response:
    if response.status != 200:
        raise SystemExit(1)
    body = response.read().decode("utf-8")
    if body:
        json.loads(body)
PY
    return 0
  fi
  uv run python - "$url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=1.5) as response:
    if response.status != 200:
        raise SystemExit(1)
    body = response.read().decode("utf-8")
    if body:
        json.loads(body)
PY
}

wait_for_http_ready() {
  local label="$1"
  local url="$2"
  local timeout="$3"
  local deadline
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    if run_python_http_check "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "Timed out waiting for ${label}: ${url}" >&2
  return 1
}

write_metadata() {
  local tmp_file
  tmp_file="$(mktemp)"
  {
    printf 'INSTANCE=%q\n' "$INSTANCE"
    printf 'WORKDIR=%q\n' "$WORKDIR"
    printf 'A2A_HOST=%q\n' "$A2A_HOST"
    printf 'A2A_PORT=%q\n' "$A2A_PORT"
    printf 'A2A_PUBLIC_URL=%q\n' "$A2A_PUBLIC_URL"
    printf 'A2A_PROJECT=%q\n' "$A2A_PROJECT"
    printf 'OPENCODE_BIND_HOST=%q\n' "$OPENCODE_BIND_HOST"
    printf 'OPENCODE_BIND_PORT=%q\n' "$OPENCODE_BIND_PORT"
    printf 'OPENCODE_BASE_URL=%q\n' "http://$(format_http_host "$OPENCODE_PROBE_HOST"):${OPENCODE_BIND_PORT}"
    printf 'OPENCODE_LOG_FILE=%q\n' "$OPENCODE_LOG_FILE"
    printf 'A2A_LOG_FILE=%q\n' "$A2A_LOG_FILE"
  } >"$tmp_file"
  mv "$tmp_file" "$METADATA_FILE"
}

load_metadata() {
  if [[ -f "$METADATA_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$METADATA_FILE"
  fi
}

terminate_pid() {
  local pid="$1"
  local label="$2"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  kill "$pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 50); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  echo "Force killing ${label} (pid=${pid})..." >&2
  kill -9 "$pid" >/dev/null 2>&1 || true
}

require_start_prerequisites() {
  [[ -n "${A2A_BEARER_TOKEN:-}" ]] || die "A2A_BEARER_TOKEN is required for start/restart."
  [[ -n "$WORKDIR" ]] || die "workdir is required for start/restart."
  [[ -d "$WORKDIR" ]] || die "workdir does not exist: $WORKDIR"
  command -v uv >/dev/null 2>&1 || die "uv not found in PATH."

  validate_port "a2a_port" "$A2A_PORT"
  validate_port "opencode_bind_port" "$OPENCODE_BIND_PORT"
  validate_non_negative_int "a2a_log_body_limit" "$A2A_LOG_BODY_LIMIT"
  validate_non_negative_int "start_timeout_seconds" "$START_TIMEOUT_SECONDS"

  validate_bool "a2a_streaming" "$A2A_STREAMING"
  validate_bool "a2a_log_payloads" "$A2A_LOG_PAYLOADS"
  validate_bool "a2a_allow_directory_override" "$A2A_ALLOW_DIRECTORY_OVERRIDE"
  validate_bool "a2a_enable_session_shell" "$A2A_ENABLE_SESSION_SHELL"
  validate_bool "opencode_lsp" "$OPENCODE_LSP"

  OPENCODE_BIN_RESOLVED="$(resolve_opencode_bin)"
  export OPENCODE_BIN_RESOLVED
}

start_opencode() {
  (
    cd "$WORKDIR"
    export OPENCODE_BIN="$OPENCODE_BIN_RESOLVED"
    export OPENCODE_BIND_HOST
    export OPENCODE_BIND_PORT
    export OPENCODE_LOG_LEVEL
    export OPENCODE_PROVIDER_ID
    export OPENCODE_MODEL_ID
    export OPENCODE_LSP
    export OPENCODE_EXTRA_ARGS
    exec "${ROOT_DIR}/scripts/deploy/run_opencode.sh"
  ) >>"$OPENCODE_LOG_FILE" 2>&1 &
  echo "$!" >"$OPENCODE_PID_FILE"
}

start_a2a() {
  local opencode_base_url="http://$(format_http_host "$OPENCODE_PROBE_HOST"):${OPENCODE_BIND_PORT}"
  (
    cd "$ROOT_DIR"
    export A2A_HOST
    export A2A_PORT
    export A2A_PUBLIC_URL
    export A2A_PROJECT
    export A2A_LOG_LEVEL
    export A2A_STREAMING
    export A2A_LOG_PAYLOADS
    export A2A_LOG_BODY_LIMIT
    export A2A_ALLOW_DIRECTORY_OVERRIDE
    export A2A_ENABLE_SESSION_SHELL
    export A2A_CANCEL_ABORT_TIMEOUT_SECONDS
    export A2A_BEARER_TOKEN
    export OPENCODE_BASE_URL="$opencode_base_url"
    export OPENCODE_DIRECTORY="$WORKDIR"
    if [[ -n "$OPENCODE_PROVIDER_ID" ]]; then
      export OPENCODE_PROVIDER_ID
    fi
    if [[ -n "$OPENCODE_MODEL_ID" ]]; then
      export OPENCODE_MODEL_ID
    fi
    if [[ -n "$OPENCODE_TIMEOUT" ]]; then
      export OPENCODE_TIMEOUT
    fi
    if [[ -n "$OPENCODE_TIMEOUT_STREAM" ]]; then
      export OPENCODE_TIMEOUT_STREAM
    fi
    exec uv run opencode-a2a-server
  ) >>"$A2A_LOG_FILE" 2>&1 &
  echo "$!" >"$A2A_PID_FILE"
}

stop_instance() {
  cleanup_stale_pidfiles

  local running=0
  local a2a_current_pid=""
  local opencode_current_pid=""

  if is_a2a_running; then
    a2a_current_pid="$(a2a_pid)"
    running=1
  fi
  if is_opencode_running; then
    opencode_current_pid="$(opencode_pid)"
    running=1
  fi

  if ((running == 0)); then
    rm -f "$OPENCODE_PID_FILE" "$A2A_PID_FILE"
    echo "Instance '${INSTANCE}' is not running."
    return 0
  fi

  if [[ -n "$a2a_current_pid" ]]; then
    terminate_pid "$a2a_current_pid" "opencode-a2a-server"
  fi
  if [[ -n "$opencode_current_pid" ]]; then
    terminate_pid "$opencode_current_pid" "opencode serve"
  fi

  rm -f "$OPENCODE_PID_FILE" "$A2A_PID_FILE"
  echo "Instance '${INSTANCE}' stopped."
}

status_instance() {
  cleanup_stale_pidfiles
  load_metadata
  refresh_probe_urls

  local opencode_state="stopped"
  local a2a_state="stopped"
  local overall_exit=0

  if is_opencode_running; then
    opencode_state="running (pid=$(opencode_pid))"
  else
    overall_exit=1
  fi

  if is_a2a_running; then
    a2a_state="running (pid=$(a2a_pid))"
  else
    overall_exit=1
  fi

  cat <<INFO
Instance: ${INSTANCE}
Workdir: ${WORKDIR:-unknown}
OpenCode: ${opencode_state}
A2A: ${a2a_state}
OpenCode log: ${OPENCODE_LOG_FILE}
A2A log: ${A2A_LOG_FILE}
OpenCode ready URL: ${OPENCODE_READY_URL}
A2A ready URL: ${A2A_READY_URL}
Public URL: ${A2A_PUBLIC_URL:-unknown}
INFO

  return "$overall_exit"
}

start_instance() {
  require_start_prerequisites
  cleanup_stale_pidfiles

  if is_opencode_running || is_a2a_running; then
    status_instance || true
    die "Instance '${INSTANCE}' is already running or partially running; use stop/restart first."
  fi

  mkdir -p "$INSTANCE_RUN_DIR" "$INSTANCE_LOG_DIR"
  write_metadata

  start_opencode
  if ! wait_for_http_ready "OpenCode" "$OPENCODE_READY_URL" "$START_TIMEOUT_SECONDS"; then
    stop_instance >/dev/null 2>&1 || true
    die "OpenCode failed to become ready. Check log: $OPENCODE_LOG_FILE"
  fi

  start_a2a
  if ! wait_for_http_ready "A2A" "$A2A_READY_URL" "$START_TIMEOUT_SECONDS"; then
    stop_instance >/dev/null 2>&1 || true
    die "A2A service failed to become ready. Check log: $A2A_LOG_FILE"
  fi

  cat <<INFO
Instance '${INSTANCE}' started.
Workdir: ${WORKDIR}
OpenCode pid: $(opencode_pid)
A2A pid: $(a2a_pid)
OpenCode log: ${OPENCODE_LOG_FILE}
A2A log: ${A2A_LOG_FILE}
Agent Card: ${A2A_PUBLIC_URL}/.well-known/agent-card.json
REST endpoint: ${A2A_PUBLIC_URL}/v1/message:send
INFO
}

restart_instance() {
  stop_instance || true
  start_instance
}

case "${ACTION,,}" in
  start)
    start_instance
    ;;
  stop)
    stop_instance
    ;;
  status)
    status_instance
    ;;
  restart)
    restart_instance
    ;;
  *)
    usage
    die "Unknown action: ${ACTION}"
    ;;
esac
