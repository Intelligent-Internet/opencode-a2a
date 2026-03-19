#!/usr/bin/env bash
# Wrapper to run opencode serve with configured host/port/logging.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/provider_secret_env_keys.sh"

OPENCODE_CORE_DIR="${OPENCODE_CORE_DIR:-/opt/.opencode}"
OPENCODE_BIN="${OPENCODE_BIN:-${OPENCODE_CORE_DIR}/bin/opencode}"
OPENCODE_LOG_LEVEL_RAW="${OPENCODE_LOG_LEVEL:-WARNING}"
OPENCODE_BIND_HOST="${OPENCODE_BIND_HOST:-127.0.0.1}"
OPENCODE_BIND_PORT="${OPENCODE_BIND_PORT:-4096}"
OPENCODE_EXTRA_ARGS="${OPENCODE_EXTRA_ARGS:-}"
OPENCODE_PROVIDER_ID="${OPENCODE_PROVIDER_ID:-}"
OPENCODE_MODEL_ID="${OPENCODE_MODEL_ID:-}"
OPENCODE_LSP="${OPENCODE_LSP:-false}"

case "${OPENCODE_LOG_LEVEL_RAW^^}" in
  DEBUG|INFO|WARN|ERROR)
    OPENCODE_LOG_LEVEL="${OPENCODE_LOG_LEVEL_RAW^^}"
    ;;
  WARNING)
    OPENCODE_LOG_LEVEL="WARN"
    ;;
  *)
    echo "Invalid OPENCODE_LOG_LEVEL value: ${OPENCODE_LOG_LEVEL_RAW} (expected DEBUG/INFO/WARN/WARNING/ERROR)" >&2
    exit 1
    ;;
esac

if [[ ! -x "$OPENCODE_BIN" ]]; then
  echo "opencode binary not found at $OPENCODE_BIN" >&2
  exit 1
fi

provider_lc="${OPENCODE_PROVIDER_ID,,}"
required_secret_key=""
if required_secret_key="$(required_provider_secret_env_key "$provider_lc" 2>/dev/null)"; then
  if [[ -z "${!required_secret_key:-}" ]]; then
    echo "${required_secret_key} is required when OPENCODE_PROVIDER_ID=${OPENCODE_PROVIDER_ID}" >&2
    exit 1
  fi
fi

if [[ -z "${OPENCODE_CONFIG_CONTENT:-}" ]]; then
  case "${OPENCODE_LSP,,}" in
    1|true|yes|on)
      lsp_json=true
      ;;
    0|false|no|off|"")
      lsp_json=false
      ;;
    *)
      echo "Invalid OPENCODE_LSP value: ${OPENCODE_LSP} (expected true/false)" >&2
      exit 1
      ;;
  esac
  printf -v OPENCODE_CONFIG_CONTENT \
    '{"$schema":"https://opencode.ai/config.json","lsp":%s}' \
    "$lsp_json"
  export OPENCODE_CONFIG_CONTENT
fi

cmd=("$OPENCODE_BIN" serve --log-level "$OPENCODE_LOG_LEVEL" --print-logs)

if [[ -n "$OPENCODE_BIND_HOST" ]]; then
  cmd+=(--hostname "$OPENCODE_BIND_HOST")
fi

if [[ -n "$OPENCODE_BIND_PORT" ]]; then
  cmd+=(--port "$OPENCODE_BIND_PORT")
fi

if [[ -n "$OPENCODE_EXTRA_ARGS" ]]; then
  read -r -a extra_args <<<"$OPENCODE_EXTRA_ARGS"
  cmd+=("${extra_args[@]}")
fi

exec "${cmd[@]}"
