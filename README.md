# opencode-a2a-serve

> Turn OpenCode into a stateful A2A service with a clear security boundary and production-friendly deployment workflow.

## Vision

Provide a practical adapter layer that lets individuals and small teams expose OpenCode through standard A2A interfaces (REST + JSON-RPC) while keeping operations, auth, and session behavior explicit and auditable.

## Core Value

- Protocol bridge: map A2A message/task semantics to OpenCode session/message/event APIs.
- Stateful interaction: support session continuation and reconnection workflows.
- Operational readiness: include systemd multi-instance deployment scripts and guardrails.
- Security baseline: enforce configurable bearer/JWT auth and document key risk boundaries.

## Core Capabilities

- A2A HTTP+JSON endpoints (`/v1/message:send`, `/v1/message:stream`, `GET /v1/tasks/{task_id}:subscribe`).
- A2A JSON-RPC endpoint (`POST /`) for standard methods and OpenCode-oriented extensions.
- Streaming with incremental task artifacts and terminal status events.
- Session continuation via `metadata.opencode.session_id`.
- Session query/control extension methods (`opencode.sessions.*`) and interrupt callback methods.

## Quick Start & Development

1. Start OpenCode:

```bash
opencode serve
```

2. Install dependencies:

```bash
uv sync --all-extras
```

3. Start this service (bearer mode, default):

```bash
A2A_BEARER_TOKEN=dev-token uv run opencode-a2a-serve
```

Or start in JWT mode:

```bash
A2A_AUTH_MODE=jwt \
A2A_JWT_SECRET_FILE=./jwt-public.pem \
A2A_JWT_ALGORITHM=HS256 \
A2A_JWT_ISSUER=dev-issuer \
A2A_JWT_AUDIENCE=dev-audience \
uv run opencode-a2a-serve
```

Default address: `http://127.0.0.1:8000`

Development & validation baseline:

```bash
uv run pre-commit run --all-files
uv run mypy src/opencode_a2a_serve
uv run pytest
```

For deployment and operations scripts, see [`scripts/README.md`](scripts/README.md).

## Documentation Map

- Product/protocol behavior:
  - [`docs/guide.md`](docs/guide.md)
- Script entry and operations:
  - [`scripts/README.md`](scripts/README.md)
  - [`scripts/deploy_readme.md`](scripts/deploy_readme.md)
  - [`scripts/init_system_readme.md`](scripts/init_system_readme.md)
  - [`scripts/start_services_readme.md`](scripts/start_services_readme.md)
  - [`scripts/uninstall_readme.md`](scripts/uninstall_readme.md)

## Security Boundary

- Startup auth config depends on mode:
  - `A2A_AUTH_MODE=bearer` (default): requires `A2A_BEARER_TOKEN`
  - `A2A_AUTH_MODE=jwt`: requires JWT key (`A2A_JWT_SECRET` or
    `A2A_JWT_SECRET_B64` or `A2A_JWT_SECRET_FILE`) plus
    `A2A_JWT_ISSUER` and `A2A_JWT_AUDIENCE`
- LLM provider keys are consumed by the OpenCode process. This model is best suited for trusted/internal environments unless stronger credential isolation is introduced.
- Within one service instance, consumers share the same underlying OpenCode workspace/environment (not tenant-isolated by default).

## License

Apache-2.0. See [`LICENSE`](LICENSE).
