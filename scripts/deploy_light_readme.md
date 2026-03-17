# Lightweight Local Launcher (`deploy_light.sh`)

This document describes `scripts/deploy_light.sh`, a lightweight entry point for
starting one local OpenCode + A2A instance in the foreground.

## Convergence Notice (#181)

`deploy_light.sh` has converged into a foreground-only launcher. It no longer
manages background process lifecycles, `stop/status/restart`, or per-instance
PID/log files.

Use external process managers like `nohup`, `pm2`, or `systemd` when detached
execution, restart policy, or log capture is required.

Scope:

- stays in foreground with logs emitted to stdout/stderr
- keeps current-user lightweight deployment semantics
- does not create system users or isolated data roots
- keeps `key=value` overrides and environment inheritance
- accepts `instance`, `log_root`, and `run_root` only for backward-compatible parsing

## Usage

Required environment:

```bash
export A2A_BEARER_TOKEN='<a2a-token>'
```

Start one instance (foreground):

```bash
./scripts/deploy_light.sh workdir=/abs/path/to/workspace
```

The script accepts an optional `start` command for backward compatibility:

```bash
./scripts/deploy_light.sh start workdir=/abs/path/to/workspace
```

Complete local example:

```bash
export A2A_BEARER_TOKEN='your-token-test'
./scripts/deploy_light.sh start \
  instance=demo \
  workdir=/home/juanjuan/opencode-a2a-serve \
  a2a_host=127.0.0.1 \
  a2a_port=38010 \
  a2a_public_url=http://127.0.0.1:38010 \
  opencode_bind_host=127.0.0.1 \
  opencode_bind_port=34106
```

## Key Inputs

- `workdir`:
  required; becomes the default `OPENCODE_DIRECTORY` exposed to the A2A layer
- `a2a_host` / `a2a_port` / `a2a_public_url`:
  A2A listen address and published URL
- `opencode_bind_host` / `opencode_bind_port`:
  local OpenCode bind address for the supervised `opencode serve`
- `opencode_provider_id` / `opencode_model_id`:
  default upstream provider/model selection
- `opencode_lsp`:
  whether to enable LSP in generated OpenCode config content
- `start_timeout_seconds`:
  readiness wait budget for both OpenCode and A2A startup

## Readiness Behavior

On start, the script:

1. validates required inputs and local commands
2. starts `opencode serve`
3. waits until `GET /session` succeeds on the configured OpenCode bind address
4. starts `opencode-a2a-server`
5. waits until the local Agent Card endpoint responds successfully

If either process fails readiness or exits, the script stops the other child
process and exits.

## Security / Scope Notes

- `A2A_BEARER_TOKEN` is still required.
- Provider secrets are inherited from the current shell environment.
- The current Linux user remains the trust boundary.
- All consumers of one lightweight instance still share the same underlying workspace/environment.
- This flow is not tenant-isolated and is not a replacement for stronger deployment isolation.

## Related Docs

- runtime behavior and API contracts: [`../docs/guide.md`](../docs/guide.md)
- script index: [`./README.md`](./README.md)
- foreground local runner: [`./start_services_readme.md`](./start_services_readme.md)
- systemd deployment flow: [`./deploy_readme.md`](./deploy_readme.md)
