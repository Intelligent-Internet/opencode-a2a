# System Bootstrap Guide (`init_system.sh`)

This document describes `scripts/init_system.sh`.

The script prepares shared host prerequisites for systemd-based OpenCode + A2A deployment and is designed to be idempotent.

## Usage

```bash
./scripts/init_system.sh
```

The script does not accept runtime arguments. Adjust defaults by editing constants at the top of `scripts/init_system.sh`.

## What It Does

- installs base tooling and `gh`
- installs Node.js >= 20 (`npm`/`npx`)
- installs `uv` and pre-downloads Python `3.10/3.11/3.12/3.13` (if missing)
- creates shared directories and applies permissions
- clones this repository to shared path (HTTPS by default)
- creates A2A virtualenv via `uv sync --all-extras`
- fails fast if `systemctl` is unavailable
- moves OpenCode install output from `/root/.opencode` when needed and wires `/usr/local/bin/opencode`

## Common Constants to Customize

- Paths: `OPENCODE_CORE_DIR`, `SHARED_WRAPPER_DIR`, `UV_PYTHON_DIR`, `DATA_ROOT`
- Repo and branch: `OPENCODE_A2A_REPO`, `OPENCODE_A2A_BRANCH`
- Toggles: `INSTALL_PACKAGES`, `INSTALL_UV`, `INSTALL_GH`, `INSTALL_NODE`
- Versions: `NODE_MAJOR`, `UV_PYTHON_VERSIONS`
- Installer pinning: `OPENCODE_INSTALLER_URL`, `OPENCODE_INSTALLER_VERSION`, `OPENCODE_INSTALLER_SHA256`, `OPENCODE_INSTALL_CMD`

## Recommended Secure Mode

- keep installer pinning/checksum verification enabled
- keep `/opt/uv-python` in controlled group mode (`770` -> `750` hardening flow)
- set `UV_PYTHON_DIR_GROUP` to a controlled group and add runtime users intentionally

## Next Step

After bootstrap, deploy a project instance with [`deploy_readme.md`](./deploy_readme.md).
