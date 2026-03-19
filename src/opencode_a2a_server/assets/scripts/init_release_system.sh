#!/usr/bin/env bash
# Release-based host bootstrap that avoids source checkout for opencode-a2a-server.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export INSTALL_A2A_SOURCE="false"

: "${A2A_RELEASE_ROOT:=/opt/opencode-a2a-release}"
: "${A2A_TOOL_DIR:=${A2A_RELEASE_ROOT}/tool}"
: "${A2A_TOOL_BIN_DIR:=${A2A_RELEASE_ROOT}/bin}"
: "${DEPLOY_HELPER_DIR:=${A2A_RELEASE_ROOT}/runtime}"

"${SCRIPT_DIR}/init_system.sh" "$@"
"${SCRIPT_DIR}/deploy/install_release_runtime.sh"
