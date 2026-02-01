#!/usr/bin/env bash
# Wrapper to run opencode-a2a from the shared venv.
set -euo pipefail

OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-serve}"
A2A_BIN="${A2A_BIN:-${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a}"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "opencode-a2a entrypoint not found at $A2A_BIN" >&2
  exit 1
fi

A2A_AUTH_MODE="${A2A_AUTH_MODE:-bearer}"

if [[ "$A2A_AUTH_MODE" == "bearer" ]]; then
  if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
    echo "A2A_BEARER_TOKEN is required when A2A_AUTH_MODE is bearer" >&2
    exit 1
  fi
elif [[ "$A2A_AUTH_MODE" == "jwt" ]]; then
  if [[ -z "${A2A_JWT_SECRET:-}" ]]; then
    echo "A2A_JWT_SECRET is required when A2A_AUTH_MODE is jwt" >&2
    exit 1
  fi
fi

exec "$A2A_BIN"
