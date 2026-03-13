# opencode-a2a-serve

> Turn OpenCode into a stateful A2A service with a clear security boundary and production-friendly deployment workflow.

## Vision

Provide a practical adapter layer that lets individuals and small teams expose OpenCode through standard A2A interfaces (REST + JSON-RPC) while keeping operations, auth, and session behavior explicit and auditable.

## Core Value

- Protocol bridge: map A2A message/task semantics to OpenCode session/message/event APIs.
- Stateful interaction: support session continuation and reconnection workflows.
- Operational readiness: include systemd multi-instance deployment scripts and guardrails.
- Security baseline: enforce bearer-token auth and document key risk boundaries.

## Design Principle

- Single-tenant runtime boundary: OpenCode may manage multiple projects/directories, but one OpenCode + `opencode-a2a-serve` instance pair is treated as one trust boundary, not a secure multi-tenant runtime.
- Shared-instance usage is best-effort coordination, not tenant isolation: identity/session ownership checks reduce accidental cross-session access, but they do not provide per-tenant OS/process/secret isolation.
- For mutually untrusted tenants, deploy separate instance pairs with isolated Linux users or containers, isolated workspace roots, and isolated credentials.

## Core Capabilities

- A2A HTTP+JSON endpoints (`/v1/message:send`, `/v1/message:stream`, `GET /v1/tasks/{task_id}:subscribe`).
- A2A JSON-RPC endpoint (`POST /`) for standard methods and OpenCode-oriented extensions.
- Streaming with incremental task artifacts and terminal status events.
- Session continuation via `metadata.shared.session.id`.
- Request-scoped model selection via `metadata.shared.model`.
- OpenCode session query/control (`opencode.sessions.*`) and provider/model discovery (`opencode.providers.*`, `opencode.models.*`) extension methods.
- Shared interrupt callback methods.

## Quick Start & Development

1. Start OpenCode:

```bash
opencode serve
```

2. Install dependencies:

```bash
uv sync --all-extras
```

3. Start this service:

```bash
A2A_BEARER_TOKEN=dev-token uv run opencode-a2a-serve
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
  - [`scripts/deploy_light_readme.md`](scripts/deploy_light_readme.md)
  - [`scripts/init_system_readme.md`](scripts/init_system_readme.md)
  - [`scripts/start_services_readme.md`](scripts/start_services_readme.md)
  - [`scripts/uninstall_readme.md`](scripts/uninstall_readme.md)

## Security Boundary

- `A2A_BEARER_TOKEN` is required for startup.
- LLM provider keys are consumed by the OpenCode process. This model is best suited for trusted/internal environments unless stronger credential isolation is introduced.
- Within one service instance, consumers share the same underlying OpenCode workspace/environment. Treat one OpenCode + A2A instance pair as a single-tenant trust boundary; it is not tenant-isolated by default.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
