# Release Deploy Guide (`opencode-a2a-server deploy-release`)

This document explains the release-based systemd deployment path.

`opencode-a2a-server deploy-release` is the preferred deploy entry point for
operators who want a lightweight instance-level deploy flow on top of a
pre-provisioned release runtime.

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
- release runtime prepared in advance
- Linux service user/group prepared in advance

## Recommended Usage

Optional admin-managed bootstrap:

```bash
opencode-a2a-server init-release-system
```

Deploy one instance using a prepared release runtime and prepared service account:

```bash
opencode-a2a-server deploy-release \
  --project alpha \
  --service-user svc-alpha \
  --service-group opencode \
  --a2a-port 8010 \
  --a2a-host 127.0.0.1
```

Enable explicit secret persistence when you accept root-only secret files:

```bash
opencode-a2a-server deploy-release \
  --project alpha \
  --service-user svc-alpha \
  --service-group opencode \
  --a2a-port 8010 \
  --a2a-host 127.0.0.1 \
  --enable-secret-persistence
```

## Notes

- `opencode-a2a-server deploy-release` shares the same secret strategy, config
  layout, and systemd hardening model as `deploy.sh`
- the command no longer installs or updates the shared release runtime
- the command no longer creates or deletes Linux users/groups
- `--service-user` is required; `--service-group` defaults to the user's primary
  group when omitted
- `scripts/deploy_release.sh` is a compatibility wrapper around the packaged
  CLI entrypoint
- legacy `key=value` arguments remain accepted for compatibility wrappers, but
  the packaged CLI now documents standard flags as the preferred contract
- use [`deploy.sh`](./deploy_readme.md) only when you intentionally want a
  source-based systemd deploy for development or debugging
- for real-host acceptance steps, see [`../docs/release_deploy_smoke_test.md`](../docs/release_deploy_smoke_test.md)
