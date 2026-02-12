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
- convenient: defaults to local bind (`A2A_HOST=127.0.0.1`) and supports host/public URL override via env
- observable: creates timestamped log directory for each run

## `start_services.sh` (one-command local start)

Prerequisites:

- `opencode` is executable (`PATH` or `~/.opencode/bin/opencode`)
- `uv` is executable

Usage:

```bash
./scripts/start_services.sh
```

Common environment variables:

- `A2A_HOST`: A2A bind host (default `127.0.0.1`)
- `A2A_PORT`: A2A port (default in `docs/guide.md`)
- `A2A_PUBLIC_URL`: public base URL exposed in agent card (default `http://${A2A_HOST}:${A2A_PORT}`)
- `OPENCODE_LOG_LEVEL`: OpenCode log level
- `A2A_LOG_LEVEL`: A2A log level (default in `docs/guide.md`)
- `LOG_ROOT`: log root directory
- `LOG_DIR`: explicit log directory (overrides timestamp path)

## `init_system.sh` (host initialization)

Prepares base host dependencies and shared directories for systemd deployment. See `docs/deployment.md` section "Optional System Bootstrap".

## `deploy.sh` (systemd multi-instance deployment)

See `docs/deployment.md`.

The `deploy/` subdirectory contains systemd unit templates and instance setup scripts orchestrated by `deploy.sh`.
