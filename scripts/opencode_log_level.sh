#!/usr/bin/env bash

normalize_opencode_log_level() {
  local raw="${1:-}"
  local upper="${raw^^}"

  case "$upper" in
    DEBUG|INFO|WARN|ERROR)
      printf '%s\n' "$upper"
      ;;
    WARNING)
      printf '%s\n' "WARN"
      ;;
    *)
      echo "Invalid OPENCODE_LOG_LEVEL value: ${raw} (expected DEBUG/INFO/WARN/WARNING/ERROR)" >&2
      return 1
      ;;
  esac
}
