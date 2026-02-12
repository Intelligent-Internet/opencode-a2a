# System Bootstrap Script (`init_system.sh`)

This document describes `scripts/init_system.sh`.
The script prepares shared system prerequisites for systemd-based OpenCode + A2A
deployment. It is idempotent: completed steps are automatically skipped.

## Usage

Run directly:

```bash
./scripts/init_system.sh
```

The script does not accept runtime arguments.
To adjust paths, feature toggles, or versions, edit the constants at the top of
`scripts/init_system.sh`.

## What It Does

- Installs base tooling and `gh` (GitHub CLI) from official sources.
- Installs Node.js >= 20 (`npm`/`npx`) using NodeSource or distro packages.
- Installs `uv` and pre-downloads Python versions
  `3.10/3.11/3.12/3.13` (only if missing).
- Creates shared directories and applies permissions
  (`/opt/uv-python` starts as `777`, then becomes recursively `755` after
  pre-download).
- Clones `opencode-a2a-serve` repository by default (SSH URL by default).
- Creates the A2A virtual environment (`uv sync --all-extras`).
- Fails fast if `systemd` (`systemctl`) is unavailable.
- If OpenCode installer places files in `/root/.opencode`, moves them to
  `OPENCODE_CORE_DIR` and writes `/usr/local/bin/opencode`.

## Customization

Edit the constant block at the top of `scripts/init_system.sh`.
Common values:

- Paths: `OPENCODE_CORE_DIR`, `SHARED_WRAPPER_DIR`, `UV_PYTHON_DIR`,
  `DATA_ROOT`
- Permissions: `UV_PYTHON_DIR_MODE`, `UV_PYTHON_DIR_FINAL_MODE`,
  `UV_PYTHON_DIR_GROUP`
- Repo and branch: `OPENCODE_A2A_REPO`, `OPENCODE_A2A_BRANCH`
- Toggles: `INSTALL_PACKAGES`, `INSTALL_UV`, `INSTALL_GH`, `INSTALL_NODE`
- Versions: `NODE_MAJOR`, `UV_PYTHON_VERSIONS`
