# opencode-a2a-server

> Turn OpenCode into a stateful A2A service with a clear runtime boundary.

`opencode-a2a-server` exposes OpenCode through standard A2A interfaces and adds
the runtime pieces that raw agent runtimes usually do not provide by default:
authentication, session continuity, streaming contracts, interrupt handling,
and explicit security guidance.

## Why This Project Exists

OpenCode is useful as an interactive runtime, but applications and gateways
need a stable service layer around it. This repository provides that layer by:

- bridging A2A transport contracts to OpenCode session/message/event APIs
- making session and interrupt behavior explicit and auditable
- keeping the server/runtime contract explicit while leaving deployment
  supervision to the operator

## What It Already Provides

- A2A HTTP+JSON endpoints (`/v1/message:send`, `/v1/message:stream`,
  `GET /v1/tasks/{task_id}:subscribe`)
- A2A JSON-RPC endpoint (`POST /`) for standard methods and OpenCode-oriented
  extensions
- SSE streaming with normalized `text`, `reasoning`, and `tool_call` blocks
- session continuation via `metadata.shared.session.id`
- OpenCode session query/control extensions
- released CLI install/upgrade flow and a foreground runtime entrypoint

## Extension Capability Overview

The Agent Card declares six extension URIs. Shared contracts are intended for
any compatible consumer; OpenCode-specific contracts stay provider-scoped even
though they are exposed through A2A JSON-RPC.

| Extension URI | Scope | Primary use |
| --- | --- | --- |
| `urn:a2a:session-binding/v1` | Shared | Bind a main chat request to an existing upstream session via `metadata.shared.session.id` |
| `urn:a2a:stream-hints/v1` | Shared | Advertise canonical stream metadata for blocks, usage, interrupts, and session hints |
| `urn:opencode-a2a:session-query/v1` | OpenCode-specific | Query external sessions and invoke OpenCode session control methods |
| `urn:a2a:interactive-interrupt/v1` | Shared | Reply to interrupt callbacks observed from stream metadata |
| `urn:a2a:compatibility-profile/v1` | Shared | Publish stable capability retention and deployment-conditional method guidance |
| `urn:a2a:wire-contract/v1` | Shared | Publish the current runtime method and endpoint boundary |

Detailed consumption guidance:

- Shared session binding: [`docs/guide.md#shared-session-binding-contract`](docs/guide.md#shared-session-binding-contract)
- Shared stream hints: [`docs/guide.md#shared-stream-hints-contract`](docs/guide.md#shared-stream-hints-contract)
- OpenCode session query: [`docs/guide.md#opencode-session-query-a2a-extension`](docs/guide.md#opencode-session-query-a2a-extension)
- Shared interrupt callback: [`docs/guide.md#shared-interrupt-callback-a2a-extension`](docs/guide.md#shared-interrupt-callback-a2a-extension)
- Compatibility profile and retention guidance:
  [`docs/guide.md#compatibility-profile`](docs/guide.md#compatibility-profile)

## Design Principle

One `OpenCode + opencode-a2a-server` instance pair is treated as a
single-tenant trust boundary.

This repository's intended scaling model is parameterized self-deployment:
consumers should launch their own isolated instance pairs instead of sharing
one runtime across mutually untrusted tenants.

- OpenCode may manage multiple projects/directories, but one deployed instance
  is not a secure multi-tenant runtime.
- Shared-instance identity/session checks are best-effort coordination, not
  hard tenant isolation.
- For mutually untrusted tenants, deploy separate instance pairs with isolated
  Linux users or containers, isolated workspace roots, isolated credentials,
  and distinct runtime ports.

## Logical Components

```mermaid
flowchart TD
    Hub["A2A client / a2a-client-hub / app"] --> Api["opencode-a2a-server transport"]
    Api --> Mapping["Task / session / interrupt mapping"]
    Mapping --> Runtime["OpenCode HTTP runtime"]

    Api --> Auth["Bearer auth + request logging controls"]
    Runtime --> Workspace["Shared workspace / environment boundary"]
```

This repository wraps OpenCode in a service layer. It does not change OpenCode
into a hard multi-tenant isolation platform.

## Recommended Client Side

If you need a client-side integration layer to consume this service, prefer
[a2a-client-hub](https://github.com/liujuanjuan1984/a2a-client-hub).

It is a better place for client concerns such as A2A consumption, upstream
adapter normalization, and application-facing integration, while
`opencode-a2a-server` stays focused on the server/runtime boundary around
OpenCode.

## Security Model

This project improves the service boundary around OpenCode, but it is not a
hard multi-tenant isolation layer.

- `A2A_BEARER_TOKEN` protects the A2A surface, but it is not a tenant
  isolation boundary inside one deployed instance.
- LLM provider keys are consumed by the OpenCode process. Prompt injection or
  indirect exfiltration attempts may still expose sensitive values.
- Deployment supervision is intentionally BYO. If you wrap this runtime with
  `systemd`, Docker, Kubernetes, or another supervisor, you own the service
  user, secret storage, restart policy, and hardening choices.

Read before deployment:

- [SECURITY.md](SECURITY.md)
- [docs/guide.md](docs/guide.md)

## User Paths

Released versions are published to PyPI and mapped to Git tags / GitHub
Releases. This is the recommended entry point for users.

### Path 1: Run a Released CLI in an Existing User Environment

Install the latest release:

```bash
uv tool install opencode-a2a-server
```

Upgrade an existing installation:

```bash
uv tool upgrade opencode-a2a-server
```

Install an exact release:

```bash
uv tool install "opencode-a2a-server==<version>"
```

Run it against an existing project/workspace:

```bash
GOOGLE_GENERATIVE_AI_API_KEY=<your-key> \
OPENCODE_PROVIDER_ID=google \
OPENCODE_MODEL_ID=gemini-3.1-pro-preview \
opencode serve --hostname 127.0.0.1 --port 4096

A2A_BEARER_TOKEN=prod-token \
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
opencode-a2a-server serve
```

`OPENCODE_WORKSPACE_ROOT` is the default workspace root that this runtime
exposes to OpenCode.

Default address: `http://127.0.0.1:8000`

Runtime boundary:

- `opencode serve` owns provider auth, model defaults, and upstream lifecycle.
- `opencode-a2a-server serve` connects to that upstream through `OPENCODE_BASE_URL`.

Common runtime variables:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `A2A_BEARER_TOKEN` | Yes | None | Bearer token required for authenticated runtime requests. |
| `OPENCODE_BASE_URL` | Yes in two-process deployments | `http://127.0.0.1:4096` | Upstream OpenCode HTTP endpoint. Set it explicitly when `opencode serve` runs separately. |
| `OPENCODE_WORKSPACE_ROOT` | No | None | Default workspace root exposed to OpenCode. |
| `A2A_HOST` | Yes in explicit deployments | `127.0.0.1` | Bind host for the A2A server. |
| `A2A_PORT` | Yes in explicit deployments | `8000` | Bind port for the A2A server. |
| `A2A_PUBLIC_URL` | No | `http://127.0.0.1:8000` | Public base URL advertised by the Agent Card. |
| `A2A_LOG_LEVEL` | No | `WARNING` | Server log level. |
| `A2A_LOG_PAYLOADS` | No | `false` | Enable request/response payload logging. |
| `A2A_LOG_BODY_LIMIT` | No | `0` | Payload preview size used when payload logging is enabled. |
| `A2A_MAX_REQUEST_BODY_BYTES` | No | `1048576` | Maximum accepted request size. |
| `A2A_ALLOW_DIRECTORY_OVERRIDE` | No | `true` | Allow request-level `metadata.opencode.directory` overrides. |
| `A2A_ENABLE_SESSION_SHELL` | No | `false` | Enable high-risk `opencode.sessions.shell`. |
| `OPENCODE_TIMEOUT` | No | `120` | Upstream OpenCode request timeout in seconds. |
| `OPENCODE_TIMEOUT_STREAM` | No | None | Upstream OpenCode stream timeout override in seconds. |

For provider-specific auth, model IDs, and config details, use the OpenCode
official docs and CLI:

- Providers: <https://opencode.ai/docs/providers/>
- Models: <https://opencode.ai/docs/models/>
- Local checks: `opencode auth list`, `opencode models`, `opencode models <provider>`

This path is for users who already manage their own shell, workspace, and
process lifecycle.

Use any supervisor you prefer for long-running operation:

- `systemd`
- Docker / container runtimes
- Kubernetes
- `supervisord`, `pm2`, or similar process managers

The project no longer ships built-in host bootstrap or process-manager
wrappers. The official product surface is the runtime entrypoint itself.

Minimal self-managed `systemd` example:

1. Create an env file such as `/etc/opencode-a2a/alpha.env`:

```bash
A2A_BEARER_TOKEN=replace-me
OPENCODE_BASE_URL=http://127.0.0.1:4096
A2A_HOST=127.0.0.1
A2A_PORT=8000
A2A_PUBLIC_URL=https://a2a.example.com
OPENCODE_WORKSPACE_ROOT=/srv/my-workspace
```

2. Create a unit file such as `/etc/systemd/system/opencode-a2a-server.service`:

```ini
[Unit]
Description=OpenCode A2A Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/my-workspace
EnvironmentFile=/etc/opencode-a2a/alpha.env
ExecStart=/home/dev/.local/bin/opencode-a2a-server serve
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Replace `ExecStart` with the absolute path returned by `command -v opencode-a2a-server`.

Migration notes:

- `OPENCODE_DIRECTORY` has been removed. Use `OPENCODE_WORKSPACE_ROOT`.
- Built-in `init-release-system`, `deploy-release`, and `uninstall-instance` have been removed.
- Secret storage, service users, restart policy, and supervisor configuration are now operator-managed.

## Contributor Paths

Use the repository checkout directly only for development, local debugging, or
validation against unreleased changes.

Quick source run:

```bash
uv sync --all-extras

GOOGLE_GENERATIVE_AI_API_KEY=<your-key> \
OPENCODE_PROVIDER_ID=google \
OPENCODE_MODEL_ID=gemini-3.1-pro-preview \
opencode serve --hostname 127.0.0.1 --port 4096

A2A_BEARER_TOKEN=dev-token \
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
uv run opencode-a2a-server serve
```

Baseline validation:

```bash
uv run pre-commit run --all-files
uv run pytest
```

## Documentation Map

### User Docs

- [docs/guide.md](docs/guide.md)
  Product behavior, API contracts, and detailed streaming/session/interrupt
  consumption guidance.
- [SECURITY.md](SECURITY.md)
  Threat model, deployment caveats, and vulnerability disclosure guidance.
- [CONTRIBUTING.md](CONTRIBUTING.md)
  Contributor workflow, validation baseline, and documentation expectations.
- [scripts/README.md](scripts/README.md)
  Contributor helper script index.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
