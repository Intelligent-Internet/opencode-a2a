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
  if [[ -t 0 ]]; then
    sudo -v
    return 0
  fi
  if ! sudo -n true 2>/dev/null; then
    echo "sudo requires a password or is not permitted (non-interactive). Refusing to apply." >&2
    echo "Run in an interactive shell, or configure NOPASSWD for required commands." >&2
    exit 1
  fi
}
