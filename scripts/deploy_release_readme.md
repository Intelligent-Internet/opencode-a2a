# Release Deploy Guide (`opencode-a2a-server deploy-release`)

This document explains the release-based systemd deployment path.

`opencode-a2a-server deploy-release` is the preferred deploy entry point for
operators who want formal deployments to follow published package versions
instead of a source checkout.

`scripts/deploy_release.sh` remains available as a compatibility wrapper.

## What It Uses

- a released `opencode-a2a-server` package installed via `uv tool`
- generated runtime helper scripts under `/opt/opencode-a2a-release/runtime`
- the same per-project config, secret, and systemd hardening flow as
  [`deploy.sh`](./deploy_readme.md)

## Prerequisites

- `systemd` and `sudo`
- OpenCode core path prepared (default `/opt/.opencode`)
- uv/python pool prepared (default `/opt/uv-python`)
- release runtime bootstrap prepared via `opencode-a2a-server init-release-system`
  or by first deploy

## Recommended Usage

Bootstrap the host:

```bash
opencode-a2a-server init-release-system
```

Deploy the latest installed release:

```bash
opencode-a2a-server deploy-release --project alpha --a2a-port 8010 --a2a-host 127.0.0.1
```

Deploy an exact package version:

```bash
opencode-a2a-server deploy-release \
  --project alpha \
  --a2a-port 8010 \
  --a2a-host 127.0.0.1 \
  --release-version 0.1.0
```

Update to the latest published release:

```bash
opencode-a2a-server deploy-release --project alpha --update-a2a --force-restart
```

Update to an exact published release:

```bash
opencode-a2a-server deploy-release \
  --project alpha \
  --release-version 0.1.0 \
  --update-a2a \
  --force-restart
```

## Notes

- `opencode-a2a-server deploy-release` shares the same secret strategy, config
  layout, and systemd hardening model as `deploy.sh`
- `--release-version <version>` pins the installed package version
- if no explicit `--release-version` is provided, first install uses the latest
  published package; later plain deploy reruns reuse the installed runtime
- `scripts/deploy_release.sh` is a compatibility wrapper around the packaged
  CLI entrypoint
- legacy `key=value` arguments remain accepted for compatibility wrappers, but
  the packaged CLI now documents standard flags as the preferred contract
- use [`deploy.sh`](./deploy_readme.md) only when you intentionally want a
  source-based systemd deploy for development or debugging
- for real-host acceptance steps, see [`../docs/release_deploy_smoke_test.md`](../docs/release_deploy_smoke_test.md)
