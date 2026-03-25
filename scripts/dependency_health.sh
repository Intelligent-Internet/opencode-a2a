#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./health_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"

run_shared_repo_health_prerequisites "dependency-health"

echo "[dependency-health] list outdated packages"
uv pip list --outdated

dev_requirements="$(mktemp)"
trap 'rm -f "${dev_requirements}"' EXIT

echo "[dependency-health] export dev extra requirements"
uv export --format requirements.txt --extra dev --no-dev --locked --no-emit-project --output-file "${dev_requirements}" >/dev/null

echo "[dependency-health] run dev dependency vulnerability audit"
uv run pip-audit --requirement "${dev_requirements}"
