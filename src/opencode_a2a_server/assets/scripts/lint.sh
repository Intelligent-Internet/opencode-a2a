#!/usr/bin/env bash
set -euo pipefail

# Run the repo canonical lint pipeline to keep local and CI checks identical.
max_retries="${PRE_COMMIT_MAX_RETRIES:-3}"
retry_delay_seconds="${PRE_COMMIT_RETRY_DELAY_SECONDS:-5}"

attempt=1
while true; do
  if uv run pre-commit run --all-files; then
    break
  fi

  if (( attempt >= max_retries )); then
    echo "ERROR: pre-commit failed after ${attempt} attempts." >&2
    exit 1
  fi

  echo "WARN: pre-commit failed on attempt ${attempt}/${max_retries}, retrying in ${retry_delay_seconds}s..." >&2
  sleep "$retry_delay_seconds"
  attempt=$((attempt + 1))
done
