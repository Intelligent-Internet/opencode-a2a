#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./health_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"

run_shared_repo_health_prerequisites "doctor"

echo "[doctor] run lint"
uv run pre-commit run --all-files

echo "[doctor] run tests"
uv run pytest
