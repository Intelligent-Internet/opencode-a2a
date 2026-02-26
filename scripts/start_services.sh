#!/usr/bin/env bash
# Docs: scripts/start_services_readme.md
# Start local foreground OpenCode + A2A processes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=opencode_log_level.sh
source "${SCRIPT_DIR}/opencode_log_level.sh"
cd "$ROOT_DIR"

A2A_PORT="${A2A_PORT:-8000}"
A2A_HOST="${A2A_HOST:-127.0.0.1}"
OPENCODE_LOG_LEVEL="${OPENCODE_LOG_LEVEL:-WARNING}"
A2A_LOG_LEVEL="${A2A_LOG_LEVEL:-WARNING}"
OTEL_INSTRUMENTATION_A2A_SDK_ENABLED="${OTEL_INSTRUMENTATION_A2A_SDK_ENABLED:-false}"
LOG_ROOT="${LOG_ROOT:-${ROOT_DIR}/logs}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="${LOG_DIR:-${LOG_ROOT}/${TIMESTAMP}}"
mkdir -p "$LOG_DIR"
OPENCODE_LOG="${OPENCODE_LOG:-${LOG_DIR}/opencode_serve.log}"
A2A_LOG="${A2A_LOG:-${LOG_DIR}/opencode_a2a.log}"
A2A_PUBLIC_URL="${A2A_PUBLIC_URL:-http://${A2A_HOST}:${A2A_PORT}}"

OPENCODE_LOG_LEVEL="$(normalize_opencode_log_level "${OPENCODE_LOG_LEVEL}")"

kill_existing() {
  local pattern="$1"
  local label="$2"
  local pids=""

  if pids="$(pgrep -f "$pattern" || true)"; then
    if [[ -n "$pids" ]]; then
      echo "Stopping existing ${label} (pids: ${pids})..."
      kill ${pids} >/dev/null 2>&1 || true
      for _ in $(seq 1 30); do
        if ! pgrep -f "$pattern" >/dev/null 2>&1; then
          return 0
        fi
        sleep 0.2
      done
      echo "Force killing ${label} (pids: ${pids})..."
      kill -9 ${pids} >/dev/null 2>&1 || true
    fi
  fi
}

OPENCODE_CMD=""
if command -v opencode >/dev/null 2>&1; then
  OPENCODE_CMD="opencode"
elif [[ -x "$HOME/.opencode/bin/opencode" ]]; then
  OPENCODE_CMD="$HOME/.opencode/bin/opencode"
fi

if [[ -z "$OPENCODE_CMD" ]]; then
  echo "opencode binary not found; install it first" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH" >&2
  exit 1
fi

kill_existing "${OPENCODE_CMD} serve" "opencode serve"
kill_existing "uv run opencode-a2a-serve" "opencode-a2a-serve"

echo "Starting opencode serve..."
"$OPENCODE_CMD" serve --log-level "$OPENCODE_LOG_LEVEL" --print-logs >"$OPENCODE_LOG" 2>&1 &
OPENCODE_PID=$!
echo "opencode serve pid: ${OPENCODE_PID} (log: $OPENCODE_LOG)"

echo "Starting A2A server on ${A2A_HOST}:${A2A_PORT}..."
A2A_HOST="$A2A_HOST" \
A2A_PUBLIC_URL="$A2A_PUBLIC_URL" \
A2A_LOG_LEVEL="$A2A_LOG_LEVEL" \
OTEL_INSTRUMENTATION_A2A_SDK_ENABLED="$OTEL_INSTRUMENTATION_A2A_SDK_ENABLED" \
uv run opencode-a2a-serve >"$A2A_LOG" 2>&1 &
A2A_PID=$!
echo "opencode-a2a-serve pid: ${A2A_PID} (log: $A2A_LOG)"

cleanup() {
  echo "Stopping services..."
  if [[ -n "${A2A_PID:-}" ]]; then
    kill "${A2A_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${OPENCODE_PID:-}" ]]; then
    kill "${OPENCODE_PID}" >/dev/null 2>&1 || true
  fi
  wait "${A2A_PID}" >/dev/null 2>&1 || true
  wait "${OPENCODE_PID}" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM HUP

cat <<INFO

A2A service endpoints:
- Agent Card: ${A2A_PUBLIC_URL}/.well-known/agent-card.json
- REST API:   ${A2A_PUBLIC_URL}/v1/message:send
Log directory: ${LOG_DIR}

INFO

echo "Services are running. Press Ctrl+C to stop."
wait -n "${OPENCODE_PID}" "${A2A_PID}"
echo "One service exited. Shutting down."
