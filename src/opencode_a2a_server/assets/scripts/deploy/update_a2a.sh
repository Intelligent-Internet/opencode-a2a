#!/usr/bin/env bash
# Update the shared opencode-a2a-server environment (no git operations).
# Requires env: OPENCODE_A2A_DIR.
set -euo pipefail

: "${OPENCODE_A2A_DIR:?}"

if [[ ! -d "$OPENCODE_A2A_DIR" ]]; then
  echo "OPENCODE_A2A_DIR not found: $OPENCODE_A2A_DIR" >&2
  exit 1
fi

if [[ ! -f "${OPENCODE_A2A_DIR}/pyproject.toml" ]]; then
  echo "pyproject.toml not found in ${OPENCODE_A2A_DIR}" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH; cannot sync venv" >&2
  exit 1
fi

echo "Syncing opencode-a2a-server venv in ${OPENCODE_A2A_DIR}..."
(
  cd "$OPENCODE_A2A_DIR"
  uv sync --all-extras
)
