#!/usr/bin/env bash
# Compatibility wrapper for the packaged deploy-release CLI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSET_SCRIPT="${SCRIPT_DIR}/../src/opencode_a2a_server/assets/scripts/deploy_release.sh"

if command -v opencode-a2a-server >/dev/null 2>&1; then
  exec opencode-a2a-server deploy-release "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run opencode-a2a-server deploy-release "$@"
fi

if [[ -f "$ASSET_SCRIPT" ]]; then
  exec bash "$ASSET_SCRIPT" "$@"
fi

echo "opencode-a2a-server CLI not found and no local packaged deploy asset is available." >&2
echo "Install the released CLI or run from a repository checkout with uv available." >&2
exit 1
