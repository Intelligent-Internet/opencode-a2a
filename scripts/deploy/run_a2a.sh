#!/usr/bin/env bash
# Wrapper to run opencode-a2a-serve from the shared venv.
set -euo pipefail

OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-serve}"
A2A_BIN="${A2A_BIN:-${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a-serve}"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "opencode-a2a-serve entrypoint not found at $A2A_BIN" >&2
  exit 1
fi

auth_mode="${A2A_AUTH_MODE:-bearer}"
auth_mode="${auth_mode,,}"

if [[ "$auth_mode" != "bearer" && "$auth_mode" != "jwt" ]]; then
  echo "A2A_AUTH_MODE must be bearer or jwt" >&2
  exit 1
fi

if [[ "$auth_mode" == "bearer" ]]; then
  if [[ -z "${A2A_BEARER_TOKEN:-}" ]]; then
    echo "A2A_BEARER_TOKEN is required when A2A_AUTH_MODE=bearer" >&2
    exit 1
  fi
else
  if [[ -z "${A2A_JWT_SECRET:-}" && -z "${A2A_JWT_SECRET_B64:-}" && -z "${A2A_JWT_SECRET_FILE:-}" ]]; then
    echo "JWT mode requires one of A2A_JWT_SECRET/A2A_JWT_SECRET_B64/A2A_JWT_SECRET_FILE" >&2
    exit 1
  fi
  if [[ -z "${A2A_JWT_ISSUER:-}" || -z "${A2A_JWT_AUDIENCE:-}" ]]; then
    echo "JWT mode requires both A2A_JWT_ISSUER and A2A_JWT_AUDIENCE" >&2
    exit 1
  fi
fi

exec "$A2A_BIN"
