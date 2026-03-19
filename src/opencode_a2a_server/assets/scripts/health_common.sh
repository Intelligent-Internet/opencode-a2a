#!/usr/bin/env bash
# Shared prerequisites for local repo health-check scripts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

run_shared_repo_health_prerequisites() {
  local label="${1:-health}"

  cd "$ROOT_DIR"

  if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found in PATH" >&2
    exit 1
  fi

  echo "[${label}] sync locked environment"
  uv sync --all-extras --frozen

  echo "[${label}] verify dependency compatibility"
  uv pip check
}
