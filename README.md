# opencode-a2a-serve

`opencode-a2a-serve` is an adapter layer that exposes OpenCode as an A2A service (FastAPI + A2A SDK). It provides:

- A2A HTTP+JSON (REST): `/v1/message:send`, `/v1/message:stream`,
  `/v1/tasks/{task_id}:resubscribe`, and related endpoints
- A2A JSON-RPC: `POST /` (used for extensions such as session queries)

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

Additional notes:

- The A2A layer enforces bearer-token authentication via `A2A_BEARER_TOKEN`.
- When `A2A_LOG_PAYLOADS=true`, payload logs may include request/response
  bodies. For `opencode.sessions.*` JSON-RPC queries, request/response body
  logging is intentionally suppressed to reduce chat-history exposure risk.

## Capabilities

- Standard A2A chat: forwards `message:send` / `message:stream` to OpenCode.
- SSE streaming: `/v1/message:stream` emits incremental
  `TaskArtifactUpdateEvent`, then `TaskStatusUpdateEvent(final=true)`.
- Re-subscribe after disconnect: `POST /v1/tasks/{task_id}:resubscribe`
  (available while the task is not in a terminal state).
- Session continuation contract: clients can explicitly bind to an existing
  OpenCode session via `metadata.opencode_session_id`.
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
- `A2A_STREAMING`: enables SSE streaming (default: `true`)
- `A2A_SESSION_CACHE_TTL_SECONDS` / `A2A_SESSION_CACHE_MAXSIZE`:
  in-memory `(identity, contextId) -> session_id` mapping cache settings

## Session Continuation Contract

To continue an existing OpenCode conversation, pass this metadata key on every invoke request:

- `metadata.opencode_session_id`: target OpenCode session ID (for example
  `ses_xxx`)

Server behavior:

- If provided, the server sends the message to the specified session.
- If omitted, the server creates a new session and caches
  `(identity, contextId) -> session_id` with TTL and max-size bounds.

Example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "message": {
      "messageId": "msg-continue-1",
      "role": "ROLE_USER",
      "content": [{"text": "Continue our previous conversation and summarize the last conclusion."}]
    },
    "metadata": {
      "opencode_session_id": "<session_id>"
    }
  }'
```

## OpenCode Session Query (A2A Extension via JSON-RPC)

The service exposes OpenCode session list/history queries through A2A extension methods on the JSON-RPC endpoint (`POST /`), without introducing custom REST endpoints.

- Auth: same `Authorization: Bearer <token>`
- Result: `result.items` always contains A2A standard objects
  (Task for session list, Message for history)
- OpenCode raw records are preserved in `metadata.opencode.raw`

List sessions (`opencode.sessions.list`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "opencode.sessions.list",
    "params": {"page": 1, "size": 20}
  }'
```

List messages in a session (`opencode.sessions.messages.list`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "opencode.sessions.messages.list",
    "params": {
      "session_id": "<session_id>",
      "page": 1,
      "size": 50
    }
  }'
```

## Documentation

- Usage guide (configuration, auth, streaming, client examples):
  `docs/guide.md`
- systemd multi-instance deployment:
  `docs/deployment.md`
- local and operational scripts:
  `scripts/README.md`

## Development & Validation

```bash
uv run pre-commit run --all-files
uv run pytest
```
