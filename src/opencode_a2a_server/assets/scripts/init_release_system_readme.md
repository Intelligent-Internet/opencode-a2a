# Release Bootstrap Guide (`init_release_system.sh`)

This document explains `scripts/init_release_system.sh`.

`init_release_system.sh` prepares the host for release-based deployments.
Unlike [`init_system.sh`](./init_system_readme.md), it does not clone the
`opencode-a2a-server` repository or create a source-tree virtualenv.

## Usage

```bash
./scripts/init_release_system.sh
```

Optional exact package version:

```bash
A2A_RELEASE_VERSION=0.1.0 ./scripts/init_release_system.sh
```

## What It Does

- runs the shared host/bootstrap steps from `init_system.sh`
- skips source checkout and source `.venv` creation
- installs a released `opencode-a2a-server` CLI into a shared `uv tool` runtime
- installs runtime helper scripts for release-based systemd units

## Default Runtime Paths

- release root: `/opt/opencode-a2a-release`
- tool env: `/opt/opencode-a2a-release/tool`
- tool bin: `/opt/opencode-a2a-release/bin`
- helper scripts: `/opt/opencode-a2a-release/runtime`

## When To Use It

- production-oriented or operator-facing deployments
- reproducible host bootstrap aligned with published package versions

Use [`init_system.sh`](./init_system_readme.md) instead when you intentionally
need a source checkout for development or debugging.
