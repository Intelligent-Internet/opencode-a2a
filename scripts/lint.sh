#!/usr/bin/env bash
set -euo pipefail

# Run the repo canonical lint pipeline to keep local and CI checks identical.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./health_common.sh
source "${SCRIPT_DIR}/health_common.sh"

cd "$ROOT_DIR"

max_retries="${PRE_COMMIT_MAX_RETRIES:-3}"
retry_delay_seconds="${PRE_COMMIT_RETRY_DELAY_SECONDS:-5}"

attempt=1
while true; do
  state_before="$(repo_state_fingerprint)"

  pre_commit_status=0
  if uv run pre-commit run --all-files; then
    pre_commit_status=0
  else
    pre_commit_status=$?
  fi

  # Auto-fixing hooks (trailing-whitespace, end-of-file-fixer, ruff --fix,
  # ruff-format) exit non-zero when they rewrite files, so a retry would turn a
  # real failure into a green run. Report it as a failure instead of retrying.
  if [[ "$(repo_state_fingerprint)" != "${state_before}" ]]; then
    echo "ERROR: pre-commit rewrote repository files; the lint gate rejects hook-generated edits." >&2
    echo "Review and commit the changes, then rerun." >&2
    exit 1
  fi

  if (( pre_commit_status == 0 )); then
    break
  fi

  if (( attempt >= max_retries )); then
    echo "ERROR: pre-commit failed after ${attempt} attempts." >&2
    exit "${pre_commit_status}"
  fi

  echo "WARN: pre-commit failed on attempt ${attempt}/${max_retries}, retrying in ${retry_delay_seconds}s..." >&2
  sleep "$retry_delay_seconds"
  attempt=$((attempt + 1))
done
