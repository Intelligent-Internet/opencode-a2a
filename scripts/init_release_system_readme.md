# Release Bootstrap Guide (`opencode-a2a-server init-release-system`)

This document explains `opencode-a2a-server init-release-system`.

`opencode-a2a-server init-release-system` prepares the host for release-based
deployments. Unlike [`init_system.sh`](./init_system_readme.md), it does not
clone the `opencode-a2a-server` repository or create a source-tree virtualenv.

`scripts/init_release_system.sh` remains available as a compatibility wrapper.

## Usage

```bash
opencode-a2a-server init-release-system
```

This command is optional admin-only bootstrap. The preferred product boundary is a
prepared runtime plus lightweight `deploy-release`.

Optional exact package version:

```bash
opencode-a2a-server init-release-system --release-version 0.1.0
```

Optional runtime path overrides:

```bash
opencode-a2a-server init-release-system \
  --release-root /opt/opencode-a2a-release \
  --tool-dir /opt/opencode-a2a-release/tool \
  --tool-bin-dir /opt/opencode-a2a-release/bin \
  --deploy-helper-dir /opt/opencode-a2a-release/runtime
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

- admin-managed host bootstrap for production-oriented deployments
- reproducible host bootstrap aligned with published package versions

Use [`init_system.sh`](./init_system_readme.md) instead when you intentionally
need a source checkout for development or debugging.
