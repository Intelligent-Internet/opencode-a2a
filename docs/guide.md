# Usage Guide

This guide covers configuration, authentication, API behavior, streaming re-subscription, and A2A client usage examples.

## Transport Contracts

- The service supports both transports:
  - HTTP+JSON (REST endpoints such as `/v1/message:send`)
  - JSON-RPC (`POST /`)
- Agent Card keeps `preferredTransport=HTTP+JSON` and also exposes JSON-RPC in `additional_interfaces`.
- Payload schema is transport-specific and should not be mixed:
  - REST send payload usually uses `message.content` and role values like `ROLE_USER`
  - JSON-RPC `message/send` payload uses `params.message.parts` and role values `user` / `agent`

## Environment Variables

- `OPENCODE_BASE_URL`: OpenCode base URL, default `http://127.0.0.1:4096`
- `OPENCODE_DIRECTORY`: OpenCode `directory` parameter (optional)
- `OPENCODE_PROVIDER_ID`: model `providerID` (optional)
- `OPENCODE_MODEL_ID`: model `modelID` (optional)
- `OPENCODE_AGENT`: OpenCode agent name (optional)
- `OPENCODE_SYSTEM`: system prompt (optional)
- `OPENCODE_VARIANT`: variant (optional)
- `OPENCODE_TIMEOUT`: request timeout in seconds, default `120`
  (systemd deployment template writes `300` by default)
- `OPENCODE_TIMEOUT_STREAM`: streaming request timeout in seconds (optional;
  unset means no explicit stream timeout)

- `A2A_PUBLIC_URL`: externally reachable A2A URL prefix,
  default `http://127.0.0.1:8000`
- `A2A_PROJECT`: optional project label injected into Agent Card extensions and examples
- `A2A_TITLE`: agent name, default `OpenCode A2A`
- `A2A_DESCRIPTION`: agent description
- `A2A_VERSION`: agent version
- `A2A_PROTOCOL_VERSION`: A2A protocol version, default `0.3.0`
- `A2A_HOST`: bind host, default `127.0.0.1`
- `A2A_PORT`: bind port, default `8000`
- `A2A_BEARER_TOKEN`: required; service fails fast if unset
- `A2A_STREAMING`: enable SSE streaming (`/v1/message:stream`), default `true`
- `A2A_LOG_LEVEL`: `DEBUG/INFO/WARNING/ERROR`, default `INFO`
- `A2A_LOG_PAYLOADS`: log A2A/OpenCode payload bodies, default `false`
- `A2A_LOG_BODY_LIMIT`: payload log body size limit, default `0` (no truncation)
- `A2A_DOCUMENTATION_URL`: optional URL exposed via Agent Card
  `documentationUrl`
- `A2A_OAUTH_AUTHORIZATION_URL`: OAuth2 authorization URL (declarative only)
- `A2A_OAUTH_TOKEN_URL`: OAuth2 token URL (declarative only)
- `A2A_OAUTH_METADATA_URL`: OAuth2 metadata URL (optional)
- `A2A_OAUTH_SCOPES`: comma-separated OAuth2 scopes (declarative only)
- `A2A_SESSION_CACHE_TTL_SECONDS`: in-memory TTL for
  `(identity, contextId) -> OpenCode session_id`, default `3600`
- `A2A_SESSION_CACHE_MAXSIZE`: max cache entries, default `10000`

## Service Behavior

- The service forwards A2A `message:send` to OpenCode session/message calls.
- Task state defaults to `input-required` to support multi-turn interactions.
- Streaming (`/v1/message:stream`) emits incremental
  `TaskArtifactUpdateEvent` and then
  `TaskStatusUpdateEvent(final=true)`. Stream artifacts carry
  `artifact.metadata.opencode.block_type` with values
  `text` / `reasoning` / `tool_call`. All chunks share one stream
  artifact ID and preserve original timeline via
  `artifact.metadata.opencode.sequence`. Events without
  `message_id` are dropped. A final snapshot is only emitted when stream
  chunks did not already produce the same final text.
  The parser strips `<think>...</think>` and `[tool_call: ...]` wrappers and
  emits the inner payload with the corresponding `block_type`.
  If stream ends with an unfinished marker prefix, buffered tail is flushed
  using the parser's current `block_type` (no rollback).
  Non-streaming requests return a `Task` directly.
- Requests require `Authorization: Bearer <token>`; otherwise `401` is
  returned. Agent Card endpoints are public.
- Within one `opencode-a2a-serve` instance, all consumers share the same
  underlying OpenCode workspace/environment. This deployment model is not
  tenant-isolated by default.
- Error handling:
  - For validation failures, missing context (`task_id`/`context_id`), or
    internal errors, the service attempts to return standard A2A failure events
    via `event_queue`.
  - Failure events include concrete error details with `failed` state.
- Directory validation and normalization:
  - Clients can pass `metadata.directory`, but it must stay inside
    `${OPENCODE_DIRECTORY}` (or service runtime root if not configured).
  - All paths are normalized with `realpath` to prevent `..` or symlink
    boundary bypass.
  - If `A2A_ALLOW_DIRECTORY_OVERRIDE=false`, only the default directory is
    accepted.
- OAuth2 settings are currently declarative in Agent Card only; runtime token
  verification for OAuth2 is not implemented yet.
- Agent Card declares OAuth2 only when both
  `A2A_OAUTH_AUTHORIZATION_URL` and `A2A_OAUTH_TOKEN_URL` are set.

## Session Continuation Contract

To continue a historical OpenCode session, include this metadata key in each invoke request:

- `metadata.opencode_session_id`: target OpenCode session ID

Server behavior:

- If provided, the request is sent to that exact OpenCode session.
- If omitted, a new session is created and cached by
  `(identity, contextId) -> session_id`.

Minimal example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-continue-1",
      "role": "ROLE_USER",
      "content": [{"text": "Continue the previous session and restate the key conclusion."}]
    },
    "metadata": {
      "opencode_session_id": "<session_id>"
    }
  }'
```

## OpenCode Session Query (A2A Extension)

This service exposes OpenCode session list and message-history queries via A2A JSON-RPC extension methods (default endpoint: `POST /`). No extra custom REST endpoint is introduced.

- Trigger: call extension methods through A2A JSON-RPC
- Auth: same `Authorization: Bearer <token>`
- Privacy guard: when `A2A_LOG_PAYLOADS=true`, request/response bodies are still
  suppressed for `method=opencode.sessions.*`
- Endpoint discovery: prefer `additional_interfaces[]` with
  `transport=jsonrpc` from Agent Card
- Result format:
  - `result.items` is always an array of A2A standard objects
  - session list => `Task` with `status.state=completed`
  - message history => `Message`
  - raw upstream payload is preserved at `metadata.opencode.raw`
  - session title is available at `metadata.opencode.title`

### Session List (`opencode.sessions.list`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "opencode.sessions.list",
    "params": {"page": 1, "size": 20}
  }'
```

### Session Messages (`opencode.sessions.messages.list`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
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

## Authentication Example (curl)

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "content": [{"text": "Explain what this repository does."}]
    }
  }'
```

## JSON-RPC Send Example (curl)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 101,
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "msg-1",
        "role": "user",
        "parts": [{"kind": "text", "text": "Explain what this repository does."}]
      }
    }
  }'
```

## Streaming Re-Subscription (`subscribe`)

If an SSE connection drops, use `GET /v1/tasks/{task_id}:subscribe` to re-subscribe while the task is still non-terminal.

## Development Setup

```bash
uv run pre-commit install
```
