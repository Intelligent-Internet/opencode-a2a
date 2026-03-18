#!/usr/bin/env bash
# Install or refresh a shared released opencode-a2a-server runtime for systemd deploys.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../shell_helpers.sh
source "${SCRIPT_DIR}/../shell_helpers.sh"

: "${A2A_TOOL_DIR:?}"
: "${A2A_TOOL_BIN_DIR:?}"
: "${DEPLOY_HELPER_DIR:?}"

A2A_PACKAGE_NAME="${A2A_PACKAGE_NAME:-opencode-a2a-server}"
A2A_RELEASE_VERSION="${A2A_RELEASE_VERSION:-}"
A2A_PYTHON_VERSION="${A2A_PYTHON_VERSION:-3.13}"
FORCE_A2A_RELEASE_INSTALL="${FORCE_A2A_RELEASE_INSTALL:-false}"
A2A_BIN="${A2A_BIN:-${A2A_TOOL_BIN_DIR}/opencode-a2a-server}"
SUDO=""

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found in PATH; cannot install released runtime" >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo not found; run as root or install sudo." >&2
    exit 1
  fi
  SUDO="sudo"
fi

package_spec="${A2A_PACKAGE_NAME}"
if [[ -n "$A2A_RELEASE_VERSION" ]]; then
  package_spec="${A2A_PACKAGE_NAME}==${A2A_RELEASE_VERSION}"
fi

$SUDO install -d -m 755 "$A2A_TOOL_DIR" "$A2A_TOOL_BIN_DIR" "$DEPLOY_HELPER_DIR"

if [[ ! -x "$A2A_BIN" ]] || is_truthy "$FORCE_A2A_RELEASE_INSTALL" || [[ -n "$A2A_RELEASE_VERSION" ]]; then
  echo "Installing released runtime: ${package_spec}"
  $SUDO env \
    UV_TOOL_DIR="$A2A_TOOL_DIR" \
    UV_TOOL_BIN_DIR="$A2A_TOOL_BIN_DIR" \
    uv tool install --force --python "$A2A_PYTHON_VERSION" "$package_spec"
else
  echo "Released runtime already present at ${A2A_BIN}; skipping reinstall."
fi

$SUDO install -m 755 "${SCRIPT_DIR}/run_opencode.sh" "${DEPLOY_HELPER_DIR}/run_opencode.sh"
$SUDO install -m 755 "${SCRIPT_DIR}/run_a2a.sh" "${DEPLOY_HELPER_DIR}/run_a2a.sh"
$SUDO install -m 644 "${SCRIPT_DIR}/provider_secret_env_keys.sh" "${DEPLOY_HELPER_DIR}/provider_secret_env_keys.sh"
