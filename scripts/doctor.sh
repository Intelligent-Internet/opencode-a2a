#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./health_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"

doctor_repo_state_fingerprint() {
  {
    git diff --no-ext-diff --binary --cached -- .
    git diff --no-ext-diff --binary -- .
    while IFS= read -r -d '' path; do
      printf 'untracked %s\n' "$path"
      cat "$path"
      printf '\n'
    done < <(git ls-files --others --exclude-standard -z)
  } | git hash-object --stdin
}

run_doctor_fix_phase() {
  local pre_commit_exit_code=0
  local repo_state_before
  local repo_state_after

  echo "[doctor] run fix phase"
  repo_state_before="$(doctor_repo_state_fingerprint)"

  if uv run pre-commit run --all-files; then
    pre_commit_exit_code=0
  else
    pre_commit_exit_code=$?
  fi

  repo_state_after="$(doctor_repo_state_fingerprint)"
  if [[ "$repo_state_before" != "$repo_state_after" ]]; then
    echo "[doctor] pre-commit modified files; review the changes and rerun doctor" >&2
    exit 1
  fi

  if (( pre_commit_exit_code != 0 )); then
    exit "$pre_commit_exit_code"
  fi
}

run_doctor_verify_phase() {
  echo "[doctor] run verify phase"

  echo "[doctor] run type checks"
  uv run mypy src/opencode_a2a

  echo "[doctor] run tests"
  uv run pytest

  echo "[doctor] enforce coverage policy"
  uv run python ./scripts/check_coverage.py
}

run_doctor_package_phase() {
  echo "[doctor] run package phase"

  echo "[doctor] build release artifacts"
  rm -f dist/opencode_a2a-*.whl dist/opencode_a2a-*.tar.gz
  uv build --no-sources

  echo "[doctor] smoke test built wheel"
  bash ./scripts/smoke_test_built_cli.sh dist/opencode_a2a-*.whl
}

run_shared_repo_health_prerequisites "doctor"
run_doctor_fix_phase
run_doctor_verify_phase
run_doctor_package_phase
