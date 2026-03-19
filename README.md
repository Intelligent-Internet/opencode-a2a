# opencode-a2a-server

> Expose OpenCode as an A2A adapter service.

`opencode-a2a-server` turns `opencode serve` into an A2A adapter service with
auth, streaming, session continuity, interrupt handling, and a clear
deployment boundary.

## What This Is

- An A2A adapter service for OpenCode.
- Use it when you need an A2A endpoint in front of `opencode serve`.
- Not a multi-tenant isolation layer. Treat one
  `OpenCode + opencode-a2a-server` pair as one trust boundary.

## Logical Components

```mermaid
flowchart TD
    Client["a2a-client-hub / any A2A client"]

    subgraph ServerSide["Server-side"]
        Adapter["opencode-a2a-server\nA2A adapter service"]
        Runtime["opencode serve\nOpenCode runtime"]

        Adapter <--> Runtime
    end

    Client <--> Adapter
```

## Quick Start

Install the released CLI with `uv tool`:

```bash
uv tool install opencode-a2a-server
```

Upgrade later with:

```bash
uv tool upgrade opencode-a2a-server
```

Make sure provider credentials and a default model are configured on the
OpenCode side, then start OpenCode:

```bash
opencode auth login
opencode models
opencode serve --hostname 127.0.0.1 --port 4096
```

Then start `opencode-a2a-server` against that upstream:

```bash
A2A_BEARER_TOKEN=dev-token \
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
opencode-a2a-server serve
```

Verify that the service is up:

```bash
curl http://127.0.0.1:8000/.well-known/agent-card.json
```

Default local address: `http://127.0.0.1:8000`

## What You Get

- A2A HTTP+JSON endpoints such as `/v1/message:send` and
  `/v1/message:stream`
- A2A JSON-RPC support on `POST /`
- SSE streaming with normalized `text`, `reasoning`, and `tool_call` blocks
- Session continuity through `metadata.shared.session.id`
- Request-scoped model selection through `metadata.shared.model`
- OpenCode-oriented JSON-RPC extensions for session and model/provider queries

Detailed protocol contracts, examples, and extension docs live in
[`docs/guide.md`](docs/guide.md).

## When To Use It

Use this project when:

- you want to keep OpenCode as the runtime
- you need A2A transports and Agent Card discovery
- you want a thin service boundary instead of building your own adapter

Look elsewhere if:

- you need hard multi-tenant isolation inside one shared runtime
- you want this project to manage your process supervisor or host bootstrap
- you want a general client integration layer rather than a server wrapper

For client-side integration, prefer
[a2a-client-hub](https://github.com/liujuanjuan1984/a2a-client-hub).

## Deployment Boundary

This repository improves the service boundary around OpenCode, but it does not
turn OpenCode into a hardened multi-tenant platform.

- `A2A_BEARER_TOKEN` protects the A2A surface.
- Provider auth and default model configuration remain on the OpenCode side.
- Deployment supervision is intentionally BYO. Use `systemd`, Docker,
  Kubernetes, or another supervisor if you need long-running operation.
- For mutually untrusted tenants, run separate instance pairs with isolated
  users, containers, workspaces, credentials, and ports.

Read before deployment:

- [SECURITY.md](SECURITY.md)
- [docs/guide.md](docs/guide.md)

## Further Reading

- [docs/guide.md](docs/guide.md)
  Usage guide, transport details, streaming behavior, extensions, and examples.
- [SECURITY.md](SECURITY.md)
  Threat model, deployment caveats, and vulnerability disclosure guidance.

## Development

For contributor workflow, local validation, and helper scripts, see
[CONTRIBUTING.md](CONTRIBUTING.md) and [scripts/README.md](scripts/README.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).
