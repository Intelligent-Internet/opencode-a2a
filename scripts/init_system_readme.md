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
- installs Node.js >= 20 (`npm`/`npx`) from the distro package manager only
- installs pinned `uv` release binaries and pre-downloads Python `3.10/3.11/3.12/3.13` (if missing)
- creates shared directories and applies permissions
- clones this repository to shared path (HTTPS by default) and checks out the latest GitHub Release tag by default
- creates A2A virtualenv via `uv sync --all-extras`
- fails fast if `systemctl` is unavailable
- moves OpenCode install output from `/root/.opencode` when needed and wires `/usr/local/bin/opencode`

## Common Constants to Customize

- Paths: `OPENCODE_CORE_DIR`, `SHARED_WRAPPER_DIR`, `UV_PYTHON_DIR`, `DATA_ROOT`
- Repo and ref: `OPENCODE_A2A_REPO`, `OPENCODE_A2A_REF`
- Toggles: `INSTALL_PACKAGES`, `INSTALL_UV`, `INSTALL_GH`, `INSTALL_NODE`
- Versions: `NODE_MAJOR`, `UV_PYTHON_VERSIONS`
- Installer pinning: `OPENCODE_INSTALLER_URL`, `OPENCODE_INSTALLER_VERSION`, `OPENCODE_INSTALLER_SHA256`, `OPENCODE_INSTALL_CMD`
- uv release pinning: [`init_system_uv_release_manifest.sh`](./init_system_uv_release_manifest.sh)

## Current Trust Model

- `gh`: installed from distro package manager or package-manager-backed vendor repo configuration
- Node.js: installed only from the distro package manager; the script no longer executes NodeSource setup scripts
- `uv`: installed from a pinned upstream GitHub release tarball, with static asset/checksum data sourced from `init_system_uv_release_manifest.sh`
- OpenCode: installer is still a remote script, but it is version-pinned and checksum-verified before execution
- repo bootstrap: default bootstrap resolves the latest GitHub Release tag for the configured repository instead of trusting `main`
- explicit overrides can still point `OPENCODE_A2A_REF` at an exact tag, branch, or commit when needed

## Recommended Secure Mode

- keep installer pinning/checksum verification enabled
- keep `init_system_uv_release_manifest.sh` aligned with the exact `uv` release assets you intend to trust
- prefer distro package mirrors or internal mirrors for Node.js instead of adding ad-hoc third-party setup scripts
- keep `/opt/uv-python` in controlled group mode (`770` -> `750` hardening flow)
- set `UV_PYTHON_DIR_GROUP` to a controlled group and add runtime users intentionally
- keep `OPENCODE_A2A_REF=release` for the default latest-release path, or pin an exact release tag for fully reproducible bootstrap

## Failure Fallback

- If `uv` bootstrap reports an unsupported Linux architecture/libc, install the pinned `uv` release manually and rerun `./scripts/init_system.sh` with `INSTALL_UV=false`.
- If the distro package manager cannot provide `Node.js >= NODE_MAJOR`, install Node.js manually from a trusted distro or internal mirror and rerun with `INSTALL_NODE=false`.
- If OpenCode installer checksum validation fails, stop and refresh the pinned installer metadata before retrying.
- If release resolution fails for a customized repository URL, set `OPENCODE_A2A_REF` to an exact tag, branch, or commit before rerunning bootstrap.

## Next Step

After bootstrap, deploy a project instance with [`deploy_readme.md`](./deploy_readme.md).
