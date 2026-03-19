# Release Deploy Smoke Test

This document defines a real-host smoke test plan for the release-based systemd
deployment path:

- `opencode-a2a-server init-release-system`
- `opencode-a2a-server deploy-release`

The goal is to validate that a prepared release runtime can be used to deploy,
restart, and remove an instance on a real Linux host without relying on a
source checkout.

## Scope

This smoke test covers:

- prepared release runtime on a real host
- first-time release-based systemd deploy
- service readiness checks
- force-restart path
- uninstall / cleanup path

This smoke test does not replace protocol-level API tests or CI unit tests.

## Recommended Test Environment

- a clean Linux host or temporary VM
- `systemd`, `sudo`, outbound network access
- no reused `/opt/opencode-a2a-release` or `/data/opencode-a2a` state from prior experiments
- a prepared Linux service user/group, for example `svc-alpha:opencode`

## Test Matrix

Run at least these two variants:

1. prepared runtime + secure two-step deploy
2. prepared runtime + explicit secret persistence

## Step 1: Optional Host Bootstrap

If you want to use the built-in admin bootstrap, run:

```bash
opencode-a2a-server init-release-system --release-version 0.1.0
```

Checks:

- `/opt/opencode-a2a-release/tool` exists
- `/opt/opencode-a2a-release/bin/opencode-a2a-server` exists and is executable
- `/opt/opencode-a2a-release/runtime/run_a2a.sh` exists
- `opencode` is available

Expected boundary:

- this path is optional and admin-only
- this path should not require a source checkout as the deployment runtime
- release helper scripts should live under `/opt/opencode-a2a-release/runtime`

## Step 2: First Deploy

Run the first deploy:

```bash
opencode-a2a-server deploy-release --project alpha --service-user svc-alpha --service-group opencode --a2a-port 8010 --a2a-host 127.0.0.1
```

Expected first-run behavior:

- project directories are created
- `*.example` secret templates are created
- services do not start until required secret files exist

Prepare secrets:

```bash
sudo cp /data/opencode-a2a/alpha/config/opencode.auth.env.example /data/opencode-a2a/alpha/config/opencode.auth.env
sudo cp /data/opencode-a2a/alpha/config/a2a.secret.env.example /data/opencode-a2a/alpha/config/a2a.secret.env
sudoedit /data/opencode-a2a/alpha/config/opencode.auth.env
sudoedit /data/opencode-a2a/alpha/config/a2a.secret.env
```

Re-run deploy:

```bash
opencode-a2a-server deploy-release --project alpha --service-user svc-alpha --service-group opencode --a2a-port 8010 --a2a-host 127.0.0.1
```

## Step 3: Service Readiness

Check systemd units:

```bash
sudo systemctl status opencode@alpha.service --no-pager
sudo systemctl status opencode-a2a-server@alpha.service --no-pager
```

Check health:

```bash
curl -fsS -H "Authorization: Bearer <token>" http://127.0.0.1:8010/health
```

Check Agent Card:

```bash
curl -fsS -H "Authorization: Bearer <token>" http://127.0.0.1:8010/.well-known/agent-card.json
```

Inspect generated unit configuration:

```bash
sudo systemctl cat opencode@alpha.service
sudo systemctl cat opencode-a2a-server@alpha.service
```

Inspect logs:

```bash
sudo journalctl -u opencode@alpha.service -n 100 --no-pager
sudo journalctl -u opencode-a2a-server@alpha.service -n 100 --no-pager
```

## Step 4: Restart

Force a restart against the prepared runtime:

```bash
opencode-a2a-server deploy-release --project alpha --service-user svc-alpha --service-group opencode --force-restart
```

Checks:

- services restart cleanly
- no path/import errors appear in `journalctl`
- the runtime still serves `/health`

## Step 5: Uninstall / Cleanup

Preview:

```bash
opencode-a2a-server uninstall-instance --project alpha
```

Apply:

```bash
opencode-a2a-server uninstall-instance --project alpha --confirm UNINSTALL
```

Checks:

- instance services stop cleanly
- instance-specific systemd drop-ins are removed
- shared release runtime under `/opt/opencode-a2a-release` is not removed
- the same project name can be deployed again
- Linux service users/groups are untouched

## Minimum Pass Criteria

The release-based deployment path can be considered smoke-tested when all of
the following succeed on a real host:

- optional `opencode-a2a-server init-release-system`
- first deploy creates templates and stops safely before secrets are provisioned
- second deploy starts both systemd services
- `/health` returns HTTP 200
- `opencode-a2a-server uninstall-instance` removes the instance without breaking the shared release runtime

## Failure Notes

When a smoke test fails, capture:

- exact command
- host OS / distribution
- relevant `systemctl status`
- relevant `journalctl` output
- whether the failure occurred in source-based or release-based mode
