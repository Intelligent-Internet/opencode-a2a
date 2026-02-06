#!/usr/bin/env bash
# Uninstall a single OpenCode + A2A instance created by scripts/deploy.sh.
#
# Safety model (enforced):
# - This script always prints the uninstall actions (preview first).
# - There is NO dry_run=false option.
# - To actually apply destructive actions you must pass confirm=UNINSTALL.
#
# IMPORTANT: This script never removes systemd template units
# (/etc/systemd/system/opencode@.service, opencode-a2a@.service) because they are
# shared globally across all instances.
#
# Usage:
#   ./scripts/uninstall.sh project=<name> [data_root=/data/projects] [confirm=UNINSTALL]
#
# Examples:
#   ./scripts/uninstall.sh project=alpha
#   ./scripts/uninstall.sh project=alpha confirm=UNINSTALL
set -euo pipefail

PROJECT_NAME=""
DATA_ROOT_INPUT=""
CONFIRM_INPUT=""

for arg in "$@"; do
  if [[ "$arg" == *=* ]]; then
    key="${arg%%=*}"
    value="${arg#*=}"
  else
    echo "Unknown argument format: $arg (expected key=value)" >&2
    exit 1
  fi

  case "${key,,}" in
    project|project_name)
      PROJECT_NAME="$value"
      ;;
    data_root)
      DATA_ROOT_INPUT="$value"
      ;;
    confirm)
      CONFIRM_INPUT="$value"
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" ]]; then
  echo "Usage: $0 project=<name> [data_root=/data/projects] [confirm=UNINSTALL]" >&2
  exit 1
fi

DATA_ROOT="${DATA_ROOT_INPUT:-${DATA_ROOT:-/data/projects}}"

# Basic guardrails to prevent path traversal and dangerous deletes.
#
# deploy.sh uses PROJECT_NAME as the Linux system user/group name, so keep this
# aligned with common Linux username constraints.
if [[ "$PROJECT_NAME" == "." || "$PROJECT_NAME" == ".." ]]; then
  echo "Invalid project name: ${PROJECT_NAME}" >&2
  exit 1
fi
if [[ "$PROJECT_NAME" == *"/"* ]]; then
  echo "Invalid project name (must be a single path component): ${PROJECT_NAME}" >&2
  exit 1
fi
if [[ "$PROJECT_NAME" =~ [[:space:]] ]]; then
  echo "Invalid project name (whitespace not allowed): ${PROJECT_NAME}" >&2
  exit 1
fi
if [[ ! "$PROJECT_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "Invalid project name (expected: ^[a-z_][a-z0-9_-]{0,31}$): ${PROJECT_NAME}" >&2
  exit 1
fi
if [[ "$DATA_ROOT" != /* || "$DATA_ROOT" == "/" ]]; then
  echo "Invalid DATA_ROOT (must be an absolute path, not /): ${DATA_ROOT}" >&2
  exit 1
fi
if [[ "$DATA_ROOT" =~ [[:space:]] ]]; then
  echo "Invalid DATA_ROOT (whitespace not allowed): ${DATA_ROOT}" >&2
  exit 1
fi

PROJECT_DIR="${DATA_ROOT}/${PROJECT_NAME}"
UNIT_OPENCODE="opencode@${PROJECT_NAME}.service"
UNIT_A2A="opencode-a2a@${PROJECT_NAME}.service"

APPLY="false"
if [[ "$CONFIRM_INPUT" == "UNINSTALL" ]]; then
  APPLY="true"
fi

run() {
  echo "+ $*"
  if [[ "$APPLY" == "true" ]]; then
    "$@"
  fi
}

warn() {
  echo "WARN: $*" >&2
}

HAD_NONFATAL_FAILURE="false"
run_ignore() {
  echo "+ $*"
  if [[ "$APPLY" == "true" ]]; then
    if ! "$@"; then
      HAD_NONFATAL_FAILURE="true"
      warn "Command failed (ignored): $*"
    fi
  fi
}

echo "Project: ${PROJECT_NAME}"
echo "DATA_ROOT: ${DATA_ROOT}"
echo "Project dir: ${PROJECT_DIR}"
echo "Note: systemd template units will NOT be removed."
echo "Mode: $([[ "$APPLY" == "true" ]] && echo apply || echo preview)"

# Refuse to delete an unexpected directory layout (defense in depth).
if [[ "$PROJECT_DIR" != "${DATA_ROOT}/"* ]]; then
  echo "Internal error: project dir is not under DATA_ROOT: ${PROJECT_DIR}" >&2
  exit 1
fi

# If the directory exists, require a marker file that deploy.sh creates.
if [[ "$APPLY" == "true" && -e "${PROJECT_DIR}" ]]; then
  if command -v sudo >/dev/null 2>&1; then
    if ! sudo test -f "${PROJECT_DIR}/config/a2a.env" && ! sudo test -f "${PROJECT_DIR}/config/opencode.env"; then
      echo "Refusing to delete ${PROJECT_DIR}: missing marker env files under config/." >&2
      echo "Expected one of:" >&2
      echo "  ${PROJECT_DIR}/config/a2a.env" >&2
      echo "  ${PROJECT_DIR}/config/opencode.env" >&2
      exit 1
    fi
  else
    echo "sudo not found; cannot verify marker files safely. Refusing to apply." >&2
    exit 1
  fi
fi

# Stop/disable instance units (idempotent).
if command -v systemctl >/dev/null 2>&1; then
  run_ignore sudo systemctl disable --now "${UNIT_A2A}" "${UNIT_OPENCODE}"
  run_ignore sudo systemctl reset-failed "${UNIT_A2A}" "${UNIT_OPENCODE}"
else
  echo "systemctl not found; skipping systemd unit disable/stop." >&2
fi

# Remove project directory.
if [[ -e "${PROJECT_DIR}" ]]; then
  run sudo rm -rf --one-file-system "${PROJECT_DIR}"
else
  echo "Project dir not found; skipping: ${PROJECT_DIR}"
fi

# Remove project user and group.
if id "${PROJECT_NAME}" &>/dev/null; then
  user_home="$(getent passwd "${PROJECT_NAME}" | cut -d: -f6 || true)"
  if [[ -n "$user_home" && "$user_home" != "${PROJECT_DIR}" ]]; then
    warn "User ${PROJECT_NAME} home mismatch (expected ${PROJECT_DIR}, got ${user_home}); refusing to delete user automatically."
  else
  if command -v userdel >/dev/null 2>&1; then
    run_ignore sudo userdel "${PROJECT_NAME}"
  elif command -v deluser >/dev/null 2>&1; then
    run_ignore sudo deluser "${PROJECT_NAME}"
  else
    echo "Neither userdel nor deluser found; cannot remove user ${PROJECT_NAME} automatically." >&2
  fi
  fi
else
  echo "User not found; skipping: ${PROJECT_NAME}"
fi

if getent group "${PROJECT_NAME}" >/dev/null 2>&1; then
  # Only delete group if it looks like the dedicated group for this user.
  primary_group="$(id -gn "${PROJECT_NAME}" 2>/dev/null || true)"
  if [[ -n "$primary_group" && "$primary_group" != "${PROJECT_NAME}" ]]; then
    warn "User ${PROJECT_NAME} primary group is ${primary_group}; refusing to delete group ${PROJECT_NAME} automatically."
  else
  if command -v groupdel >/dev/null 2>&1; then
    run_ignore sudo groupdel "${PROJECT_NAME}"
  elif command -v delgroup >/dev/null 2>&1; then
    run_ignore sudo delgroup "${PROJECT_NAME}"
  else
    echo "Neither groupdel nor delgroup found; cannot remove group ${PROJECT_NAME} automatically." >&2
  fi
  fi
else
  echo "Group not found; skipping: ${PROJECT_NAME}"
fi

if [[ "$APPLY" == "true" ]]; then
  if [[ "$HAD_NONFATAL_FAILURE" == "true" ]]; then
    warn "Uninstall completed with non-fatal failures. See WARN lines above."
  else
    echo "Uninstall completed."
  fi
else
  echo "Preview completed."
fi
if [[ "$APPLY" != "true" ]]; then
  echo
  echo "Preview only. To apply, re-run with: confirm=UNINSTALL"
fi
