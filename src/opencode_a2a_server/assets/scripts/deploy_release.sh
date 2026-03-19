#!/usr/bin/env bash
# Release-based systemd deploy entry point.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export A2A_INSTALL_MODE="release"
exec "${SCRIPT_DIR}/deploy.sh" "$@"
