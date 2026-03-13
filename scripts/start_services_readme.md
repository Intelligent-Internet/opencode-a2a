# Local Runner Guide (`start_services.sh`)

This document describes `scripts/start_services.sh`, a local foreground runner without systemd.

## Usage

Required env:

```bash
export A2A_BEARER_TOKEN='<a2a-token>'
```

Then run:

```bash
./scripts/start_services.sh
```

The script starts:

- `opencode serve`
- `uv run opencode-a2a-server`

It also stops previous matching local processes before startup.

## Common Environment Variables

- `A2A_BEARER_TOKEN` (required)
- `A2A_HOST` (default `127.0.0.1`)
- `A2A_PORT` (default `8000`)
- `A2A_PUBLIC_URL` (default `http://<A2A_HOST>:<A2A_PORT>`)
- `OPENCODE_LOG_LEVEL` (default `WARNING`, normalized to `WARN` for OpenCode CLI)
- `A2A_LOG_LEVEL` (default `WARNING`)
- `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` (default `false`)
- `LOG_ROOT` / `LOG_DIR` / `OPENCODE_LOG` / `A2A_LOG`

## Notes

- Requires `opencode` and `uv` in PATH.
- Press `Ctrl+C` to stop both processes.
- Runtime protocol behavior is documented in [`../docs/guide.md`](../docs/guide.md).
