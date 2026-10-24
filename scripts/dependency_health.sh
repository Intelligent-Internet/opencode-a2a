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

run_dependency_audit() {
  local label="$1"
  local requirements_file="$2"
  local output_name="$3"
  local -a audit_args=(
    --requirement "${requirements_file}"
    --disable-pip
    --require-hashes
    --strict
  )

  echo "[dependency-health] run ${label} dependency vulnerability audit"
  if [[ -n "${DEPENDENCY_AUDIT_OUTPUT_DIR:-}" ]]; then
    mkdir -p "${DEPENDENCY_AUDIT_OUTPUT_DIR}"
    uv run pip-audit "${audit_args[@]}" \
      --format json \
      --output "${DEPENDENCY_AUDIT_OUTPUT_DIR}/${output_name}-pip-audit.json"
  else
    uv run pip-audit "${audit_args[@]}"
  fi
}

run_dependency_audit "dev" "${dev_requirements}" "dev"
run_dependency_audit "build" "requirements-build.txt" "build"
