#!/usr/bin/env bash
# Wrapper to run opencode-a2a-server from the shared venv.
set -euo pipefail

OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-server}"
A2A_BIN="${A2A_BIN:-${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a-server}"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "opencode-a2a-server entrypoint not found at $A2A_BIN" >&2
  exit 1
fi

if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
  echo "A2A_BEARER_TOKEN is required" >&2
  exit 1
fi

exec "$A2A_BIN"
