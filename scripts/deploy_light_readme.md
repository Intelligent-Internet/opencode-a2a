# Lightweight Local Deploy Guide (`deploy_light.sh`)

This document describes `scripts/deploy_light.sh`, a lightweight foreground launcher for one local OpenCode + A2A instance.

It is intended for trusted local/self-host scenarios where the operator wants to reuse the current Linux user, an existing workspace directory, and the current repository checkout.

This script does **not** replace the systemd deployment flow:

- It keeps the current two-process runtime model:
  - `opencode serve`
  - `opencode-a2a-server`
- It does not create system users, isolated data roots, or systemd units.
- It is best suited for single-user or small-team environments that already trust the current host user and workspace.

For production-oriented multi-instance deployment, prefer [`deploy_release.sh`](./deploy_release_readme.md). Use [`deploy.sh`](./deploy_readme.md) only for source-based debugging workflows.

## Usage

Required environment:

```bash
export A2A_BEARER_TOKEN='<a2a-token>'
```

Start one instance:

```bash
./scripts/deploy_light.sh workdir=/abs/path/to/workspace
```

Example with explicit ports:

```bash
./scripts/deploy_light.sh \
  workdir=/srv/workspaces/demo \
  a2a_host=127.0.0.1 \
  a2a_port=8010 \
  a2a_public_url=http://127.0.0.1:8010 \
  opencode_bind_host=127.0.0.1 \
  opencode_bind_port=4106
```

## Key Inputs

- `workdir`:
  required; becomes the default `OPENCODE_DIRECTORY`
  exposed to the A2A layer
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

On launch, the script:

1. validates required inputs and local commands
2. starts `opencode serve`
3. waits until `GET /session` succeeds on the configured OpenCode bind address
4. starts `opencode-a2a-server`
5. waits until the local Agent Card endpoint responds successfully

If either process fails readiness, the script stops any already-started child process and exits non-zero.

## Security / Scope Notes

- `A2A_BEARER_TOKEN` is still required.
- Provider secrets are inherited from the current shell environment.
- The current Linux user remains the trust boundary.
- All consumers of one lightweight instance still share the same underlying workspace/environment.
- This flow is not tenant-isolated and is not a replacement for stronger deployment isolation.

## Related Docs

- runtime behavior and API contracts: [`../docs/guide.md`](../docs/guide.md)
- script index: [`./README.md`](./README.md)
- systemd deployment flow: [`./deploy_readme.md`](./deploy_readme.md)
