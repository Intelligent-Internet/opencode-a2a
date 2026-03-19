# Release Deploy Guide (`deploy_release.sh`)

This document explains the release-based systemd deployment path.

`deploy_release.sh` is the preferred deploy entry point for operators who want
formal deployments to follow published package versions instead of a source
checkout.

## What It Uses

- a released `opencode-a2a-server` package installed via `uv tool`
- generated runtime helper scripts under `/opt/opencode-a2a-release/runtime`
- the same per-project config, secret, and systemd hardening flow as
  [`deploy.sh`](./deploy_readme.md)

## Prerequisites

- `systemd` and `sudo`
- OpenCode core path prepared (default `/opt/.opencode`)
- uv/python pool prepared (default `/opt/uv-python`)
- release runtime bootstrap prepared via [`init_release_system.sh`](./init_release_system.sh) or by first deploy

## Recommended Usage

Bootstrap the host:

```bash
./scripts/init_release_system.sh
```

Deploy the latest installed release:

```bash
./scripts/deploy_release.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

Deploy an exact package version:

```bash
./scripts/deploy_release.sh \
  project=alpha \
  a2a_port=8010 \
  a2a_host=127.0.0.1 \
  release_version=0.1.0
```

Update to the latest published release:

```bash
./scripts/deploy_release.sh project=alpha update_a2a=true force_restart=true
```

Update to an exact published release:

```bash
./scripts/deploy_release.sh \
  project=alpha \
  release_version=0.1.0 \
  update_a2a=true \
  force_restart=true
```

## Notes

- `deploy_release.sh` shares the same secret strategy, config layout, and
  systemd hardening model as `deploy.sh`
- `release_version=<version>` pins the installed package version
- if no explicit `release_version` is provided, first install uses the latest
  published package; later plain deploy reruns reuse the installed runtime
- use [`deploy.sh`](./deploy_readme.md) only when you intentionally want a
  source-based systemd deploy for development or debugging
- for real-host acceptance steps, see [`../docs/release_deploy_smoke_test.md`](../docs/release_deploy_smoke_test.md)
