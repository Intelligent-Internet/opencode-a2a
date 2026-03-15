#!/usr/bin/env bash
# Docs: scripts/deploy_light_readme.md
# Lightweight foreground launcher for one local OpenCode + A2A instance.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ACTION="${1:-}"
if [[ "$ACTION" == "start" ]]; then
  shift
fi

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

START_TIMEOUT_SECONDS="20"

usage() {
  cat <<'USAGE'
Usage:
  A2A_BEARER_TOKEN=<token> ./scripts/deploy_light.sh [start] workdir=/abs/path [a2a_host=127.0.0.1] [a2a_port=8000] [a2a_public_url=http://127.0.0.1:8000] [a2a_project=<name>] [a2a_log_level=INFO] [a2a_streaming=true] [a2a_log_payloads=false] [a2a_log_body_limit=0] [a2a_allow_directory_override=true] [a2a_enable_session_shell=false] [a2a_cancel_abort_timeout_seconds=2.0] [opencode_bin=opencode] [opencode_bind_host=127.0.0.1] [opencode_bind_port=4096] [opencode_log_level=WARNING] [opencode_provider_id=<id>] [opencode_model_id=<id>] [opencode_lsp=false] [opencode_extra_args='...'] [opencode_timeout=120] [opencode_timeout_stream=300] [start_timeout_seconds=20]

This script runs in the foreground. Use external process managers (nohup, pm2, systemd) for background execution.
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
    start_timeout_seconds)
      START_TIMEOUT_SECONDS="$value"
      ;;
    instance|log_root|run_root)
      # Recognized but ignored for backward compatibility in arguments
      ;;
    *)
      die "Unknown argument: $arg"
      ;;
  esac
done

if [[ -z "$WORKDIR" ]]; then
  usage
  exit 1
fi

if [[ -z "$A2A_PUBLIC_URL" ]]; then
  A2A_PUBLIC_URL="http://${A2A_HOST}:${A2A_PORT}"
fi

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

OPENCODE_PROBE_HOST="$(normalize_probe_host "$OPENCODE_BIND_HOST")"
OPENCODE_READY_URL="http://$(format_http_host "$OPENCODE_PROBE_HOST"):${OPENCODE_BIND_PORT}/session"
A2A_PROBE_HOST="$(normalize_probe_host "$A2A_HOST")"
A2A_READY_URL="http://$(format_http_host "$A2A_PROBE_HOST"):${A2A_PORT}/.well-known/agent-card.json"

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

run_python_http_check_with() {
  local url="$1"
  shift
  "$@" - "$url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=1.5) as response:
        if response.status != 200:
            sys.exit(1)
        body = response.read().decode("utf-8")
        if body:
            json.loads(body)
except Exception:
    sys.exit(1)
PY
}

run_python_http_check() {
  local url="$1"
  if command -v python3 >/dev/null 2>&1; then
    run_python_http_check_with "$url" python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    run_python_http_check_with "$url" python
    return
  fi
  run_python_http_check_with "$url" uv run python
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

require_start_prerequisites() {
  [[ -n "${A2A_BEARER_TOKEN:-}" ]] || die "A2A_BEARER_TOKEN is required."
  [[ -n "$WORKDIR" ]] || die "workdir is required."
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

OPENCODE_PID=""
A2A_PID=""

cleanup() {
  echo "Stopping services..."
  if [[ -n "$A2A_PID" ]]; then
    kill "$A2A_PID" >/dev/null 2>&1 || true
    wait "$A2A_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$OPENCODE_PID" ]]; then
    kill "$OPENCODE_PID" >/dev/null 2>&1 || true
    wait "$OPENCODE_PID" >/dev/null 2>&1 || true
  fi
  exit 0
}

trap cleanup SIGINT SIGTERM EXIT

start_instance() {
  require_start_prerequisites

  # 1. Start opencode
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
  ) &
  OPENCODE_PID=$!

  echo "Starting OpenCode (pid=${OPENCODE_PID})..."
  if ! wait_for_http_ready "OpenCode" "$OPENCODE_READY_URL" "$START_TIMEOUT_SECONDS"; then
    die "OpenCode failed to become ready."
  fi

  # 2. Start A2A
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
    [[ -n "$OPENCODE_PROVIDER_ID" ]] && export OPENCODE_PROVIDER_ID
    [[ -n "$OPENCODE_MODEL_ID" ]] && export OPENCODE_MODEL_ID
    [[ -n "$OPENCODE_TIMEOUT" ]] && export OPENCODE_TIMEOUT
    [[ -n "$OPENCODE_TIMEOUT_STREAM" ]] && export OPENCODE_TIMEOUT_STREAM
    exec uv run opencode-a2a-server
  ) &
  A2A_PID=$!

  echo "Starting A2A (pid=${A2A_PID})..."
  if ! wait_for_http_ready "A2A" "$A2A_READY_URL" "$START_TIMEOUT_SECONDS"; then
    die "A2A service failed to become ready."
  fi

  cat <<INFO
Instance started. logging to stdout/stderr.
Agent Card: ${A2A_PUBLIC_URL}/.well-known/agent-card.json
REST endpoint: ${A2A_PUBLIC_URL}/v1/message:send
INFO

  wait -n "$OPENCODE_PID" "$A2A_PID"
  echo "One service exited. Shutting down."
}

start_instance
