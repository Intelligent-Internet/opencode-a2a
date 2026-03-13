#!/usr/bin/env bash
# Enable and start systemd services for a project.
# Usage: ./enable_instance.sh <project_name>
# Requires sudo to manage systemd services.
set -euo pipefail

PROJECT_NAME="${1:-}"

if [[ -z "$PROJECT_NAME" ]]; then
  echo "Usage: $0 <project_name>" >&2
  exit 1
fi

FORCE_RESTART="${FORCE_RESTART:-false}"

sudo systemctl daemon-reload

start_or_restart() {
  local unit="$1"
  if [[ "$FORCE_RESTART" == "true" ]]; then
    if sudo systemctl is-active --quiet "$unit"; then
      sudo systemctl restart "$unit"
    else
      sudo systemctl enable --now "$unit"
    fi
  else
    sudo systemctl enable --now "$unit"
  fi
}

start_or_restart "opencode@${PROJECT_NAME}.service"
start_or_restart "opencode-a2a-server@${PROJECT_NAME}.service"

sudo systemctl status "opencode-a2a-server@${PROJECT_NAME}.service" --no-pager
