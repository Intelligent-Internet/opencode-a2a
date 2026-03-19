#!/usr/bin/env bash
# Docs: scripts/uninstall_readme.md
# Preview-first uninstall for one deployed project instance.
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
  echo "Usage: $0 project=<name> [data_root=/data/opencode-a2a] [confirm=UNINSTALL]" >&2
  exit 1
fi

DATA_ROOT="${DATA_ROOT_INPUT:-${DATA_ROOT:-/data/opencode-a2a}}"

# Guardrails for path safety.
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
if [[ "$DATA_ROOT" != /* || "$DATA_ROOT" == "/" ]]; then
  echo "Invalid DATA_ROOT (must be an absolute path, not /): ${DATA_ROOT}" >&2
  exit 1
fi
if [[ "$DATA_ROOT" =~ [[:space:]] ]]; then
  echo "Invalid DATA_ROOT (whitespace not allowed): ${DATA_ROOT}" >&2
  exit 1
fi

UNIT_OPENCODE="opencode@${PROJECT_NAME}.service"
UNIT_A2A="opencode-a2a-server@${PROJECT_NAME}.service"
OPENCODE_OVERRIDE_DIR="/etc/systemd/system/opencode@${PROJECT_NAME}.service.d"
A2A_OVERRIDE_DIR="/etc/systemd/system/opencode-a2a-server@${PROJECT_NAME}.service.d"

APPLY="false"
if [[ "$CONFIRM_INPUT" == "UNINSTALL" ]]; then
  APPLY="true"
fi

# Canonicalize paths and reject dot-segments in apply mode.
contains_dot_segment() {
  local p="$1"
  [[ "$p" =~ (^|/)\.\.(/|$) || "$p" =~ (^|/)\.(/|$) ]]
}

DATA_ROOT_RAW="$DATA_ROOT"
DATA_ROOT_EFFECTIVE="$DATA_ROOT"
PROJECT_DIR_EFFECTIVE=""

if [[ "$APPLY" == "true" ]]; then
  if contains_dot_segment "$DATA_ROOT_RAW"; then
    echo "Invalid DATA_ROOT for apply mode (contains '.' or '..' segments): ${DATA_ROOT_RAW}" >&2
    exit 1
  fi
fi

if command -v realpath >/dev/null 2>&1; then
  DATA_ROOT_EFFECTIVE="$(realpath -m -- "$DATA_ROOT_RAW")"
else
  if [[ "$APPLY" == "true" ]]; then
    echo "realpath not found; cannot safely apply uninstall. Install coreutils or provide realpath." >&2
    exit 1
  fi
fi

PROJECT_DIR_EFFECTIVE="${DATA_ROOT_EFFECTIVE}/${PROJECT_NAME}"
if command -v realpath >/dev/null 2>&1; then
  PROJECT_DIR_EFFECTIVE="$(realpath -m -- "$PROJECT_DIR_EFFECTIVE")"
fi

DATA_ROOT="$DATA_ROOT_EFFECTIVE"
PROJECT_DIR="$PROJECT_DIR_EFFECTIVE"

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

run_reset_failed() {
  # Treat missing/not-loaded units as informational.
  echo "+ $*"
  if [[ "$APPLY" != "true" ]]; then
    return 0
  fi

  local out=""
  if out="$("$@" 2>&1)"; then
    if [[ -n "$out" ]]; then
      echo "$out"
    fi
    return 0
  fi

  local rc=$?
  if [[ -n "$out" ]]; then
    echo "$out" >&2
  fi

  if [[ "$out" == *"not loaded"* || "$out" == *"not found"* ]]; then
    echo "INFO: systemctl reset-failed skipped (unit not loaded/not found)." >&2
    return 0
  fi

  HAD_NONFATAL_FAILURE="true"
  warn "Command failed (ignored): $* (exit=$rc)"
  return 0
}

echo "Project: ${PROJECT_NAME}"
echo "DATA_ROOT: ${DATA_ROOT}"
echo "Project dir: ${PROJECT_DIR}"
echo "Note: systemd template units will NOT be removed."
echo "Mode: $([[ "$APPLY" == "true" ]] && echo apply || echo preview)"

# Enforce strict project-name constraints in apply mode.
if [[ "$APPLY" == "true" ]]; then
  if [[ ! "$PROJECT_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    echo "Invalid project name for apply mode (expected: ^[a-z_][a-z0-9_-]{0,31}$): ${PROJECT_NAME}" >&2
    exit 1
  fi
fi

# Apply mode requires sudo; avoid interactive hangs.
if [[ "$APPLY" == "true" ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo not found; cannot apply uninstall." >&2
    exit 1
  fi
  # Prefer a non-interactive probe first because some sudoers policies still
  # prompt for `sudo -v` even when NOPASSWD command execution is allowed.
  if sudo -n true 2>/dev/null; then
    :
  elif [[ -t 0 ]]; then
    if sudo -v; then
      :
    else
      echo "sudo authentication failed." >&2
      exit 1
    fi
  else
    echo "sudo requires a password or is not permitted (non-interactive). Refusing to apply." >&2
    echo "Run in an interactive shell, or configure NOPASSWD for required commands." >&2
    exit 1
  fi
fi

# Refuse unexpected target layout (defense in depth).
if [[ "$PROJECT_DIR" != "${DATA_ROOT}/"* ]]; then
  echo "Internal error: project dir is not under DATA_ROOT: ${PROJECT_DIR}" >&2
  exit 1
fi

# If the directory exists, require deploy markers.
if [[ "$APPLY" == "true" && -e "${PROJECT_DIR}" ]]; then
  if ! sudo test -f "${PROJECT_DIR}/config/a2a.env" && ! sudo test -f "${PROJECT_DIR}/config/opencode.env"; then
    echo "Refusing to delete ${PROJECT_DIR}: missing marker env files under config/." >&2
    echo "Expected one of:" >&2
    echo "  ${PROJECT_DIR}/config/a2a.env" >&2
    echo "  ${PROJECT_DIR}/config/opencode.env" >&2
    exit 1
  fi
fi

# Stop/disable instance units (idempotent).
if command -v systemctl >/dev/null 2>&1; then
  run_ignore sudo systemctl disable --now "${UNIT_A2A}" "${UNIT_OPENCODE}"
  run_reset_failed sudo systemctl reset-failed "${UNIT_A2A}" "${UNIT_OPENCODE}"
  run_ignore sudo rm -rf -- "${A2A_OVERRIDE_DIR}" "${OPENCODE_OVERRIDE_DIR}"
  run_ignore sudo systemctl daemon-reload
else
  echo "systemctl not found; skipping systemd unit disable/stop." >&2
fi

# Remove project directory.
if [[ -e "${PROJECT_DIR}" ]]; then
  run sudo rm -rf --one-file-system "${PROJECT_DIR}"
else
  echo "Project dir not found; skipping: ${PROJECT_DIR}"
fi

echo "Service user/group lifecycle is not managed by uninstall-instance." >&2

if [[ "$APPLY" == "true" ]]; then
  if [[ "$HAD_NONFATAL_FAILURE" == "true" ]]; then
    warn "Uninstall completed with non-fatal failures. See WARN lines above."
    exit 2
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
