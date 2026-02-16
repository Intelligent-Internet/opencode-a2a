# opencode-a2a-serve

> **Turning OpenCode into a production-ready, stateful Agent API with REST/JSON-RPC endpoints, authentication, streaming, and session management.**
>
> **Tech Stack:** Python 3.11+ | FastAPI | A2A SDK | `uv` | `pytest`

`opencode-a2a-serve` is an adapter layer that exposes OpenCode as an A2A service (FastAPI + A2A SDK). It provides:

- A2A HTTP+JSON (REST): `/v1/message:send`, `/v1/message:stream`,
  `GET /v1/tasks/{task_id}:subscribe`, and related endpoints
- A2A JSON-RPC: `POST /` (for standard methods and extensions such as session queries)

In practice, this service is a protocol bridge and security boundary: it maps A2A message/task semantics to OpenCode session/message/event APIs, while adding authentication, observability, and session-continuation contracts.

> Important: `A2A_BEARER_TOKEN` is required for startup.
> See `docs/guide.md`.

## Security Boundary (Read First)

- In the current architecture, the `opencode` process must read LLM provider API
  credentials (for example `GOOGLE_GENERATIVE_AI_API_KEY`).
- Because of that, an `opencode agent` may leak sensitive environment values
  through prompt injection or indirect exfiltration patterns.
- Do not treat this deployment model as a hard guarantee that provider keys are
  inaccessible to agent behavior.
- This project is best suited for trusted/internal environments until a stronger
  token isolation model is implemented (for example tenant isolation, hosted
  proxy credentials, auditing, and rotation/revocation strategy).
- Within one `opencode-a2a-serve` instance, all consumers operate on the same
  underlying OpenCode workspace/environment. It is not tenant-isolated by
  default.

Additional notes:

- The A2A layer enforces bearer-token authentication via `A2A_BEARER_TOKEN`.
- When `A2A_LOG_PAYLOADS=true`, payload logs may include request/response
  bodies. For `opencode.sessions.*` JSON-RPC queries, request/response body
  logging is intentionally suppressed to reduce chat-history exposure risk.
- Deployment-side LLM provider coverage and known gaps are documented in
  `docs/deployment.md` (`Current Provider Coverage and Gaps`).

## Capabilities

- Standard A2A chat: forwards `message:send` / `message:stream` to OpenCode.
- SSE streaming: `/v1/message:stream` emits incremental updates and then
  closes with `TaskStatusUpdateEvent(final=true)`. For detailed streaming
  contract and event semantics, see `docs/guide.md`.
- Token usage passthrough: normalized usage/cost stats are exposed at
  `metadata.opencode.usage` (stream final status and non-streaming task metadata).
- Interrupt callback passthrough: when OpenCode emits `permission.asked` /
  `question.asked`, stream status events include `metadata.opencode.interrupt`
  so downstream can reply via JSON-RPC extension methods.
- Re-subscribe after disconnect: `GET /v1/tasks/{task_id}:subscribe`
  (available while the task is not in a terminal state).
- Session continuation contract: clients can explicitly bind to an existing
  OpenCode session via `metadata.opencode.session_id`.
- OpenCode session query extension (JSON-RPC):
  `opencode.sessions.list` / `opencode.sessions.messages.list`.

## Quick Start

1. Start OpenCode:

```bash
opencode serve
```

2. Install dependencies:

```bash
uv sync --all-extras
```

3. Start A2A service:

```bash
A2A_BEARER_TOKEN=dev-token uv run opencode-a2a-serve
```

Default listen address: `http://127.0.0.1:8000`

A2A Agent Card: `http://127.0.0.1:8000/.well-known/agent-card.json`

Minimal request example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "content": [{"text": "Explain what this repository does."}]
    }
  }'
```

## Key Configuration

For full configuration, see `docs/guide.md`. Most commonly used options:

- `OPENCODE_BASE_URL`: OpenCode base URL (default: `http://127.0.0.1:4096`)
- `OPENCODE_DIRECTORY`: OpenCode `directory` parameter (optional; controlled by
  server and cannot be overridden by clients)
- `A2A_BEARER_TOKEN`: required bearer token for authentication
- `A2A_PUBLIC_URL`: externally reachable URL prefix exposed in Agent Card
- `A2A_PROJECT`: optional project label injected into Agent Card metadata/examples
- `A2A_STREAMING`: enables SSE streaming (default: `true`)
- `A2A_SESSION_CACHE_TTL_SECONDS` / `A2A_SESSION_CACHE_MAXSIZE`:
  in-memory `(identity, contextId) -> session_id` mapping cache settings

## API & Protocol Details

Implementation-level protocol contracts and examples are maintained in
`docs/guide.md`:

- Transport contract and payload shape boundaries
- Session continuation (`metadata.opencode.session_id`)
- JSON-RPC extension methods:
  `opencode.sessions.list`, `opencode.sessions.messages.list`,
  `opencode.permission.reply`, `opencode.question.reply`,
  `opencode.question.reject`
- Interrupt callback request lifecycle and error semantics

## Documentation

- Script entry guide (init/deploy/local/uninstall):
  [`scripts/README.md`](scripts/README.md)
- Usage guide (configuration, auth, streaming, client examples):
  [`docs/guide.md`](docs/guide.md)
- Systemd multi-instance deployment details:
  [`docs/deployment.md`](docs/deployment.md)

## License

This project is licensed under the Apache License 2.0.
See [`LICENSE`](LICENSE).

## Development & Validation

CI (`.github/workflows/ci.yml`) runs the same baseline checks on PRs and `main` pushes.

```bash
uv run pre-commit run --all-files
uv run pytest
```
