# opencode-a2a

> Expose OpenCode through A2A.

`opencode-a2a` adds an A2A runtime layer to `opencode serve`, with
auth, streaming, session continuity, interrupt handling, and a clear
deployment boundary.

## What This Is

- An A2A adapter service for `opencode serve`.
- Use it when you need a stable A2A endpoint for apps, gateways, or A2A
  clients.

```mermaid
flowchart LR
    External["A2A Clients / a2a-client-hub / gateways"]

    subgraph Adapter["opencode-a2a"]
        direction TB
        Ingress["A2A Surface\nREST + JSON-RPC"]
        OpenCode["OpenCode Runtime"]
        Outbound["Embedded A2A Client\n(a2a_call path)"]
        Ingress <--> OpenCode
        Ingress --> Outbound
    end

    subgraph Peers["Peer network"]
        direction TB
        ClientA["Peer A2A Agent"]
        RuntimeB["Peer OpenCode runtime"]
        ClientA --> RuntimeB
    end

    External --> Ingress
    Outbound --> ClientA
    ClientA --> Outbound
```

## Quick Start

Install the released CLI with `uv tool`:

```bash
uv tool install opencode-a2a
```

Upgrade later with:

```bash
uv tool upgrade opencode-a2a
```

Make sure provider credentials and a default model are configured on the
OpenCode side, then start OpenCode:

```bash
opencode auth login
opencode models
opencode serve --hostname 127.0.0.1 --port 4096
```

Treat the deployed OpenCode user's HOME/XDG config directories as part of the
runtime state. If a packaged or service-managed deployment appears to ignore
fresh provider env vars, inspect that user's persisted OpenCode auth/config
files before assuming the A2A adapter layer is overriding credentials.

Then start `opencode-a2a` against that upstream:

```bash
A2A_BEARER_TOKEN=dev-token \
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
opencode-a2a serve
```

Verify that the service is up:

```bash
curl http://127.0.0.1:8000/.well-known/agent-card.json
```

## What You Get

- A2A HTTP+JSON endpoints such as `/v1/message:send` and
  `/v1/message:stream`
- A2A JSON-RPC support on `POST /`
- Peering capabilities: can act as a client via `opencode-a2a call`
- Autonomous tool execution: supports `a2a_call` tool for outbound agent-to-agent communication
- SSE streaming with normalized `text`, `reasoning`, and `tool_call` blocks
- Session continuity through `metadata.shared.session.id`
- Request-scoped model selection through `metadata.shared.model`
- OpenCode-oriented JSON-RPC extensions for session and model/provider queries

## Peering Node

`opencode-a2a` supports a "Peering Node" architecture where a single process handles both inbound (Server) and outbound (Client) A2A traffic.

### CLI Client
Interact with other A2A agents directly from the command line:

```bash
opencode-a2a call http://other-agent:8000 "How are you?" --token your-outbound-token
```

### Outbound Agent Calls (Tools)
The server can autonomously execute `a2a_call(url, message)` tool calls emitted by the OpenCode runtime. Results are fetched via A2A and returned to the model as tool results, enabling multi-agent orchestration.

When the target peer requires bearer auth, configure `A2A_CLIENT_BEARER_TOKEN`
for server-side outbound calls. CLI calls can continue using `--token` or
`A2A_CLIENT_BEARER_TOKEN`.

Server-side outbound client settings are fully wired through runtime config:
`A2A_CLIENT_TIMEOUT_SECONDS`, `A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS`,
`A2A_CLIENT_USE_CLIENT_PREFERENCE`, `A2A_CLIENT_BEARER_TOKEN`, and
`A2A_CLIENT_SUPPORTED_TRANSPORTS`.

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
- Provider auth and default model configuration remain on the OpenCode side; deployment-time
  precedence details and HOME/XDG state impact are documented in
  [docs/guide.md](docs/guide.md#troubleshooting-provider-auth-state).
- `A2A_CLIENT_BEARER_TOKEN` is used for outbound peer calls initiated by the
  server-side `a2a_call` tool.
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
