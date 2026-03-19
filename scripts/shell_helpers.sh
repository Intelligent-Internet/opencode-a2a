#!/usr/bin/env bash
# Shared shell helpers for deploy/bootstrap scripts.

is_truthy() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

ensure_sudo_ready() {
  if [[ "${EUID}" -eq 0 ]]; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo not found; run as root or install sudo." >&2
    exit 1
  fi
  # Prefer a non-interactive probe first because some sudoers policies still
  # prompt for `sudo -v` even when NOPASSWD command execution is allowed.
  if sudo -n true 2>/dev/null; then
    return 0
  fi
  if [[ -t 0 ]]; then
    if sudo -v; then
      return 0
    fi
    echo "sudo authentication failed." >&2
    exit 1
  fi
  echo "sudo requires a password or is not permitted (non-interactive). Refusing to apply." >&2
  echo "Run in an interactive shell, or configure NOPASSWD for required commands." >&2
  exit 1
}
