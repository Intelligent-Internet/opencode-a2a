# opencode-a2a

> Expose OpenCode through A2A.

`opencode-a2a` adds an A2A runtime layer to `opencode serve`, with
auth, streaming, session continuity, interrupt handling, and a clear
deployment boundary.

## What This Is

- An A2A adapter service built on `opencode serve`, with inbound runtime
  exposure plus outbound peer calling.
- It supports both roles in one process: serving as an A2A Server and hosting an
  embedded A2A Client for `a2a_call`.

## Architecture

```mermaid
flowchart TD
    External["A2A Clients / a2a-client-hub / Gateways"]

    subgraph Adapter["opencode-a2a Runtime"]
        Ingress["Inbound A2A Surface\nREST + JSON-RPC"]
        OpenCode["OpenCode Runtime"]
        Outbound["Embedded A2A Client\na2a_call"]
    end

    subgraph Peers["Peer A2A services"]
        PeerA2A["Peer A2A Agent"]
        PeerRuntime["Peer OpenCode Runtime"]
        PeerA2A --> PeerRuntime
    end

    External -->|message/send,\nmessage:stream| Ingress
    Ingress -->|tool call| OpenCode
    OpenCode -->|model/tool result events| Ingress
    Ingress -->|a2a_call| Outbound
    Outbound -->|message/send,\nmessage:stream| PeerA2A
    PeerA2A -->|tool result| Outbound
    PeerRuntime -->|task session\nexecution| PeerA2A
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
A2A_TASK_STORE_DATABASE_URL=sqlite+aiosqlite:///./opencode-a2a.db \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
opencode-a2a
```

Verify that the service is up:

```bash
curl http://127.0.0.1:8000/.well-known/agent-card.json
```

## Capabilities

- A2A HTTP+JSON endpoints such as `/v1/message:send` and
  `/v1/message:stream`
- A2A JSON-RPC support on `POST /`
- SDK-owned A2A task surfaces such as `GET /v1/tasks`, task push notification
  config routes, and JSON-RPC `agent/getAuthenticatedExtendedCard`
- Peering capabilities: can act as a client via `opencode-a2a call`
- Autonomous tool execution: supports `a2a_call` tool for outbound agent-to-agent communication
- SSE streaming with normalized `text`, `reasoning`, and `tool_call` blocks
- Session continuity through `metadata.shared.session.id`
- Request-scoped model selection through `metadata.shared.model`
- OpenCode-oriented JSON-RPC extensions for session and model/provider queries

## Peering Node / Outbound Access

`opencode-a2a` supports a "Peering Node" architecture where a single process handles both inbound (Server) and outbound (Client) A2A traffic.

### CLI Client
Interact with other A2A agents directly from the command line:

```bash
# Using the target peer agent's Bearer token via environment injection
A2A_CLIENT_BEARER_TOKEN=your-outbound-token \
opencode-a2a call http://other-agent:8000 "How are you?"

# Using the target peer agent's Basic auth via environment injection
# Accepts raw user:pass or its base64-encoded value
A2A_CLIENT_BASIC_AUTH="user:pass" \
opencode-a2a call http://other-agent:8000 "How are you?"
```

### Outbound Agent Calls (Tools)
The server can autonomously execute `a2a_call(url, message)` tool calls emitted by the OpenCode runtime. Results are fetched via A2A and returned to the model as tool results, enabling multi-agent orchestration.

When the target peer agent requires bearer auth, configure
`A2A_CLIENT_BEARER_TOKEN` for server-side outbound calls. When the target peer
agent requires Basic auth, use `A2A_CLIENT_BASIC_AUTH`.
These outbound credentials apply to the peer specified by `opencode-a2a call`
or `a2a_call(url, message)`, not to this service's inbound `A2A_BEARER_TOKEN`.
The CLI intentionally reads outbound credentials from environment variables only,
so secrets do not appear in shell history or process arguments.

Server-side outbound client settings are fully wired through runtime config:
`A2A_CLIENT_TIMEOUT_SECONDS`, `A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS`,
`A2A_CLIENT_USE_CLIENT_PREFERENCE`, `A2A_CLIENT_BEARER_TOKEN`, `A2A_CLIENT_BASIC_AUTH`, and
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
- Use `A2A_CLIENT_BEARER_TOKEN` for server-side outbound peer calls initiated by
  `a2a_call`.
- Deployment supervision is intentionally BYO. Use `systemd`, Docker,
  Kubernetes, or another supervisor if you need long-running operation.
- For mutually untrusted tenants, run separate instance pairs with isolated
  users, containers, workspaces, credentials, and ports.

Read before deployment:

- [SECURITY.md](SECURITY.md)
- [docs/guide.md](docs/guide.md)
- [SUPPORT.md](SUPPORT.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

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
