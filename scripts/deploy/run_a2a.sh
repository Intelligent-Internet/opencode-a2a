#!/usr/bin/env bash
# Wrapper to run opencode-a2a from the shared venv.
set -euo pipefail

OPENCODE_A2A_DIR="${OPENCODE_A2A_DIR:-/opt/opencode-a2a/opencode-a2a-serve}"
A2A_BIN="${A2A_BIN:-${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a}"

if [[ ! -x "$A2A_BIN" ]]; then
  echo "opencode-a2a entrypoint not found at $A2A_BIN" >&2
  exit 1
fi

if [[ -z "${A2A_JWT_SECRET:-}" ]]; then
  echo "A2A_JWT_SECRET is required" >&2
  exit 1
fi
if [[ -z "${A2A_JWT_ISSUER:-}" ]]; then
  echo "A2A_JWT_ISSUER is required" >&2
  exit 1
fi
if [[ -z "${A2A_JWT_AUDIENCE:-}" ]]; then
  echo "A2A_JWT_AUDIENCE is required" >&2
  exit 1
fi
if [[ -n "${A2A_JWT_SCOPE_MATCH:-}" ]] && [[ "${A2A_JWT_SCOPE_MATCH}" != "any" && "${A2A_JWT_SCOPE_MATCH}" != "all" ]]; then
  echo "A2A_JWT_SCOPE_MATCH must be 'any' or 'all'" >&2
  exit 1
fi

exec "$A2A_BIN"
