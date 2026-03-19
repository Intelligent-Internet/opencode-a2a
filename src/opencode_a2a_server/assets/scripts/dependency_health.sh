#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./health_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"

run_shared_repo_health_prerequisites "dependency-health"

echo "[dependency-health] list outdated packages"
uv pip list --outdated

echo "[dependency-health] run vulnerability audit"
uv run pip-audit
