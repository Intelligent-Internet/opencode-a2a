#!/usr/bin/env bash
# Install systemd template units for OpenCode and A2A.
# Requires env: OPENCODE_A2A_DIR, OPENCODE_CORE_DIR, UV_PYTHON_DIR, DATA_ROOT.
# Requires sudo to write /etc/systemd/system.
set -euo pipefail

: "${OPENCODE_A2A_DIR:?}"
: "${OPENCODE_CORE_DIR:?}"
: "${UV_PYTHON_DIR:?}"
: "${DATA_ROOT:?}"

DEPLOY_HELPER_DIR="${DEPLOY_HELPER_DIR:-${OPENCODE_A2A_DIR}/scripts/deploy}"
A2A_BIN="${A2A_BIN:-}"
A2A_TOOL_DIR="${A2A_TOOL_DIR:-}"
A2A_TOOL_BIN_DIR="${A2A_TOOL_BIN_DIR:-}"
A2A_ENV_BIN_LINE=""
A2A_TOOL_READONLY_LINES=""

if [[ -n "$A2A_BIN" ]]; then
  A2A_ENV_BIN_LINE="Environment=A2A_BIN=${A2A_BIN}"
fi
if [[ -n "$A2A_TOOL_DIR" ]]; then
  A2A_TOOL_READONLY_LINES="${A2A_TOOL_READONLY_LINES}
ReadOnlyPaths=${A2A_TOOL_DIR}"
fi
if [[ -n "$A2A_TOOL_BIN_DIR" ]]; then
  A2A_TOOL_READONLY_LINES="${A2A_TOOL_READONLY_LINES}
ReadOnlyPaths=${A2A_TOOL_BIN_DIR}"
fi

UNIT_DIR="/etc/systemd/system"
OPENCODE_UNIT="${UNIT_DIR}/opencode@.service"
A2A_UNIT="${UNIT_DIR}/opencode-a2a-server@.service"

sudo install -d -m 755 "$UNIT_DIR"

cat <<UNIT | sudo tee "$OPENCODE_UNIT" >/dev/null
[Unit]
Description=OpenCode serve for %i
After=network.target

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=${DATA_ROOT}/%i
Environment=OPENCODE_CORE_DIR=${OPENCODE_CORE_DIR}
Environment=OPENCODE_A2A_DIR=${OPENCODE_A2A_DIR}
Environment=UV_PYTHON_DIR=${UV_PYTHON_DIR}
Environment=PATH=${OPENCODE_CORE_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=${DATA_ROOT}/%i/config/opencode.env
EnvironmentFile=-${DATA_ROOT}/%i/config/opencode.auth.env
EnvironmentFile=-${DATA_ROOT}/%i/config/opencode.secret.env
Environment=HOME=${DATA_ROOT}/%i

ExecStart=${DEPLOY_HELPER_DIR}/run_opencode.sh
Restart=on-failure
RestartSec=2
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_ROOT}/%i
ReadOnlyPaths=${OPENCODE_CORE_DIR}
ReadOnlyPaths=${DEPLOY_HELPER_DIR}
ReadOnlyPaths=${UV_PYTHON_DIR}
ReadOnlyPaths=/usr/bin/gh
${A2A_TOOL_READONLY_LINES}

[Install]
WantedBy=multi-user.target
UNIT

cat <<UNIT | sudo tee "$A2A_UNIT" >/dev/null
[Unit]
Description=OpenCode A2A for %i
After=network.target opencode@%i.service
Requires=opencode@%i.service

[Service]
Type=simple
User=%i
Group=%i
WorkingDirectory=${DATA_ROOT}/%i
Environment=OPENCODE_A2A_DIR=${OPENCODE_A2A_DIR}
Environment=OPENCODE_CORE_DIR=${OPENCODE_CORE_DIR}
Environment=UV_PYTHON_DIR=${UV_PYTHON_DIR}
${A2A_ENV_BIN_LINE}
EnvironmentFile=${DATA_ROOT}/%i/config/a2a.env
EnvironmentFile=-${DATA_ROOT}/%i/config/a2a.secret.env
Environment=HOME=${DATA_ROOT}/%i

ExecStart=${DEPLOY_HELPER_DIR}/run_a2a.sh
Restart=on-failure
RestartSec=2
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${DATA_ROOT}/%i
ReadOnlyPaths=${DEPLOY_HELPER_DIR}
ReadOnlyPaths=${OPENCODE_CORE_DIR}
ReadOnlyPaths=${UV_PYTHON_DIR}
${A2A_TOOL_READONLY_LINES}

[Install]
WantedBy=multi-user.target
UNIT
