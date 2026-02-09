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
#   ./scripts/uninstall.sh project=<name> [data_root=/data/opencode-a2a] [confirm=UNINSTALL]
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
  echo "Usage: $0 project=<name> [data_root=/data/opencode-a2a] [confirm=UNINSTALL]" >&2
  exit 1
fi

DATA_ROOT="${DATA_ROOT_INPUT:-${DATA_ROOT:-/data/opencode-a2a}}"

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
if [[ "$DATA_ROOT" != /* || "$DATA_ROOT" == "/" ]]; then
  echo "Invalid DATA_ROOT (must be an absolute path, not /): ${DATA_ROOT}" >&2
  exit 1
fi
if [[ "$DATA_ROOT" =~ [[:space:]] ]]; then
  echo "Invalid DATA_ROOT (whitespace not allowed): ${DATA_ROOT}" >&2
  exit 1
fi

UNIT_OPENCODE="opencode@${PROJECT_NAME}.service"
UNIT_A2A="opencode-a2a@${PROJECT_NAME}.service"

APPLY="false"
if [[ "$CONFIRM_INPUT" == "UNINSTALL" ]]; then
  APPLY="true"
fi

# Canonicalize paths for safety and to prevent DATA_ROOT=/path/.. surprises.
#
# In apply mode we refuse dot-segments in DATA_ROOT and require a canonicalizer.
contains_dot_segment() {
  local p="$1"
  [[ "$p" =~ (^|/)\.\.(/|$) || "$p" =~ (^|/)\.(/|$) ]]
}

find_exe() {
  # Find an executable without relying on the caller's PATH (which may omit /usr/sbin).
  local name="$1"
  local p=""
  p="$(command -v "$name" 2>/dev/null || true)"
  if [[ -n "$p" && -x "$p" ]]; then
    echo "$p"
    return 0
  fi
  for dir in /usr/sbin /sbin /usr/bin /bin /usr/local/sbin /usr/local/bin; do
    if [[ -x "${dir}/${name}" ]]; then
      echo "${dir}/${name}"
      return 0
    fi
  done
  return 1
}

print_missing_account_tool_hints() {
  # Print operator-friendly guidance when user/group management tools are missing.
  # Args: user|group
  local kind="${1:-}"
  local os_id=""
  local os_like=""

  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-}"
    os_like="${ID_LIKE:-}"
  fi

  echo "Fallback hints:" >&2
  echo "  - PATH for non-root users may omit /usr/sbin and /sbin." >&2
  echo "    Try: export PATH=\"\$PATH:/usr/sbin:/sbin\" && command -v userdel groupdel" >&2

  if [[ "$kind" == "user" ]]; then
    echo "  - If user deletion tools are missing (userdel/deluser):" >&2
    if [[ "$os_id" == "debian" || "$os_id" == "ubuntu" || "$os_like" == *debian* ]]; then
      echo "    - Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y passwd" >&2
    elif [[ "$os_id" == "rhel" || "$os_id" == "fedora" || "$os_like" == *rhel* || "$os_like" == *fedora* ]]; then
      echo "    - RHEL/Fedora: sudo dnf install -y shadow-utils  (or: sudo yum install -y shadow-utils)" >&2
    else
      echo "    - Install the package providing userdel/deluser (distro-specific)." >&2
    fi
    echo "  - Then delete manually: sudo userdel \"${PROJECT_NAME}\"" >&2
  elif [[ "$kind" == "group" ]]; then
    echo "  - If group deletion tools are missing (groupdel/delgroup):" >&2
    if [[ "$os_id" == "debian" || "$os_id" == "ubuntu" || "$os_like" == *debian* ]]; then
      echo "    - Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y passwd" >&2
    elif [[ "$os_id" == "rhel" || "$os_id" == "fedora" || "$os_like" == *rhel* || "$os_like" == *fedora* ]]; then
      echo "    - RHEL/Fedora: sudo dnf install -y shadow-utils  (or: sudo yum install -y shadow-utils)" >&2
    else
      echo "    - Install the package providing groupdel/delgroup (distro-specific)." >&2
    fi
    echo "  - Then delete manually: sudo groupdel \"${PROJECT_NAME}\"" >&2
  fi
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
  # systemctl reset-failed is best-effort cleanup. If the unit is not loaded/not
  # found, treat it as informational (do not affect exit code).
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

# In apply mode, enforce strict project name constraints to match typical Linux
# username rules (deploy.sh uses PROJECT_NAME as the system user/group name).
if [[ "$APPLY" == "true" ]]; then
  if [[ ! "$PROJECT_NAME" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    echo "Invalid project name for apply mode (expected: ^[a-z_][a-z0-9_-]{0,31}$): ${PROJECT_NAME}" >&2
    exit 1
  fi
fi

# Apply mode requires sudo; avoid hanging in non-interactive environments.
if [[ "$APPLY" == "true" ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo not found; cannot apply uninstall." >&2
    exit 1
  fi
  if [[ -t 0 ]]; then
    # Interactive terminal: refresh credentials (may prompt).
    sudo -v
  else
    # Non-interactive: require non-prompting sudo.
    if ! sudo -n true 2>/dev/null; then
      echo "sudo requires a password or is not permitted (non-interactive). Refusing to apply." >&2
      echo "Run in an interactive shell, or configure NOPASSWD for required commands." >&2
      exit 1
    fi
  fi
fi

# Refuse to delete an unexpected directory layout (defense in depth).
if [[ "$PROJECT_DIR" != "${DATA_ROOT}/"* ]]; then
  echo "Internal error: project dir is not under DATA_ROOT: ${PROJECT_DIR}" >&2
  exit 1
fi

# If the directory exists, require a marker file that deploy.sh creates.
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
  user_deleted="false"
  user_home="$(getent passwd "${PROJECT_NAME}" | cut -d: -f6 || true)"
  if [[ -n "$user_home" && "$user_home" != "${PROJECT_DIR}" ]]; then
    warn "User ${PROJECT_NAME} home mismatch (expected ${PROJECT_DIR}, got ${user_home}); refusing to delete user automatically."
    HAD_NONFATAL_FAILURE="true"
  else
    userdel_bin="$(find_exe userdel || true)"
    deluser_bin="$(find_exe deluser || true)"
    if [[ -n "$userdel_bin" ]]; then
      run_ignore sudo "$userdel_bin" "${PROJECT_NAME}"
    elif [[ -n "$deluser_bin" ]]; then
      run_ignore sudo "$deluser_bin" "${PROJECT_NAME}"
    else
      echo "Neither userdel nor deluser found; cannot remove user ${PROJECT_NAME} automatically." >&2
      HAD_NONFATAL_FAILURE="true"
      print_missing_account_tool_hints user
    fi
  fi

  # Determine whether the user is gone before attempting to delete the group.
  if ! id "${PROJECT_NAME}" &>/dev/null; then
    user_deleted="true"
  fi
else
  echo "User not found; skipping: ${PROJECT_NAME}"
  user_deleted="false"
fi

if getent group "${PROJECT_NAME}" >/dev/null 2>&1; then
  # Safety rules (apply mode):
  # - We only attempt group deletion after the user is gone.
  # - If the group still has members, we refuse to delete it automatically.
  if [[ "$APPLY" == "true" && "$user_deleted" != "true" ]]; then
    warn "Refusing to delete group ${PROJECT_NAME} automatically because user ${PROJECT_NAME} still exists (or was not deleted)."
    HAD_NONFATAL_FAILURE="true"
  else
    if [[ "$APPLY" == "true" ]]; then
      members="$(getent group "${PROJECT_NAME}" | cut -d: -f4 || true)"
      if [[ -n "$members" ]]; then
        warn "Refusing to delete group ${PROJECT_NAME} automatically because it still has members: ${members}"
        HAD_NONFATAL_FAILURE="true"
        # Still print the command for auditability, but do not run it.
        groupdel_bin="$(find_exe groupdel || true)"
        if [[ -n "$groupdel_bin" ]]; then
          echo "+ sudo ${groupdel_bin} ${PROJECT_NAME}"
        else
          echo "+ sudo groupdel ${PROJECT_NAME}"
        fi
      else
        groupdel_bin="$(find_exe groupdel || true)"
        delgroup_bin="$(find_exe delgroup || true)"
        if [[ -n "$groupdel_bin" ]]; then
          run_ignore sudo "$groupdel_bin" "${PROJECT_NAME}"
        elif [[ -n "$delgroup_bin" ]]; then
          run_ignore sudo "$delgroup_bin" "${PROJECT_NAME}"
        else
          echo "Neither groupdel nor delgroup found; cannot remove group ${PROJECT_NAME} automatically." >&2
          HAD_NONFATAL_FAILURE="true"
          print_missing_account_tool_hints group
        fi
      fi
    else
      # Preview mode: print what would be attempted in apply mode.
      groupdel_bin="$(find_exe groupdel || true)"
      delgroup_bin="$(find_exe delgroup || true)"
      if [[ -n "$groupdel_bin" ]]; then
        run_ignore sudo "$groupdel_bin" "${PROJECT_NAME}"
      elif [[ -n "$delgroup_bin" ]]; then
        run_ignore sudo "$delgroup_bin" "${PROJECT_NAME}"
      else
        echo "Neither groupdel nor delgroup found; cannot remove group ${PROJECT_NAME} automatically." >&2
        print_missing_account_tool_hints group
      fi
    fi
  fi
else
  echo "Group not found; skipping: ${PROJECT_NAME}"
fi

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
