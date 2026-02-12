# scripts

This directory contains local runtime scripts and systemd deployment scripts.

## Which Script to Use

- `init_system.sh`: prepares host prerequisites and shared directories for
  systemd deployment. Idempotent; completed steps are skipped.
- `start_services.sh`: local/temporary OpenCode + A2A runner. No `sudo`, no
  systemd. Runs in foreground; `Ctrl+C` stops both processes.
- `deploy.sh`: systemd multi-instance deployment for long-running server
  operations.
- `uninstall.sh`: remove one systemd instance by project name. Always prints a
  preview first; destructive actions require explicit
  `confirm=UNINSTALL`.

Why keep `start_services.sh`:

- lightweight: no systemd and no `sudo`
- convenient: auto-detects Tailscale IPv4 and sets `A2A_PUBLIC_URL`
- observable: creates timestamped log directory for each run

## `start_services.sh` (one-command local start)

Prerequisites:

- `tailscale` is installed and `tailscale ip -4` works
- `opencode` is executable (`PATH` or `~/.opencode/bin/opencode`)
- `uv` is executable

Usage:

```bash
./scripts/start_services.sh
```

Common environment variables:

- `A2A_PORT`: A2A port (default in `docs/guide.md`)
- `OPENCODE_LOG_LEVEL`: OpenCode log level
- `A2A_LOG_LEVEL`: A2A log level (default in `docs/guide.md`)
- `LOG_ROOT`: log root directory
- `LOG_DIR`: explicit log directory (overrides timestamp path)

## `init_system.sh` (host initialization)

Prepares base host dependencies and shared directories for systemd deployment.
See `docs/deployment.md` section "Optional System Bootstrap".

## `deploy.sh` (systemd multi-instance deployment)

See `docs/deployment.md`.

The `deploy/` subdirectory contains systemd unit templates and instance setup
scripts orchestrated by `deploy.sh`.
