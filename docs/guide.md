# Usage Guide

This guide covers configuration, authentication, API behavior, streaming re-subscription, and A2A client usage examples.
It is also the canonical document for implementation-level protocol contracts
and JSON-RPC extension details (README stays at overview level).

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
- `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED`: controls A2A SDK tracing instrumentation, default `false` in deploy/start scripts
- `A2A_LOG_PAYLOADS`: log A2A/OpenCode payload bodies, default `false`
- `A2A_LOG_BODY_LIMIT`: payload log body size limit, default `0` (no truncation)
- `A2A_CANCEL_ABORT_TIMEOUT_SECONDS`: best-effort upstream
  `session.abort` timeout in seconds for `tasks/cancel`, default `2.0`
- `A2A_DOCUMENTATION_URL`: optional URL exposed via Agent Card
  `documentationUrl`
- `A2A_OAUTH_AUTHORIZATION_URL`: OAuth2 authorization URL (declarative only)
- `A2A_OAUTH_TOKEN_URL`: OAuth2 token URL (declarative only)
- `A2A_OAUTH_METADATA_URL`: OAuth2 metadata URL (optional)
- `A2A_OAUTH_SCOPES`: comma-separated OAuth2 scopes (declarative only)
- `A2A_SESSION_CACHE_TTL_SECONDS`: in-memory TTL for
  `(identity, contextId) -> OpenCode session_id`, default `3600`
- `A2A_SESSION_CACHE_MAXSIZE`: max cache entries, default `10000`
- `A2A_ENABLE_SESSION_SHELL`: enable `opencode.sessions.shell` JSON-RPC control
  method, default `false`

## Service Behavior

- The service forwards A2A `message:send` to OpenCode session/message calls.
- Task state defaults to `completed` for successful turns.
- Streaming (`/v1/message:stream`) emits incremental
  `TaskArtifactUpdateEvent` and then
  `TaskStatusUpdateEvent(final=true)`. Stream artifacts carry
  `artifact.metadata.opencode.block_type` with values
  `text` / `reasoning` / `tool_call`. All chunks share one stream
  artifact ID and preserve original timeline via
  `artifact.metadata.opencode.event_id`. Events without
  `message_id` are dropped. A final snapshot is only emitted when stream
  chunks did not already produce the same final text.
  Stream routing is schema-first: the service classifies chunks primarily by
  OpenCode `part.type` (plus `part_id` state) rather than inline text markers.
  `message.part.delta` and `message.part.updated` are merged per `part_id`;
  out-of-order deltas are buffered and replayed when the corresponding
  `part.updated` arrives. Structured `tool` parts are emitted as `tool_call`
  blocks with normalized state payload. Final status event metadata may include
  normalized token usage at `metadata.opencode.usage` with fields like
  `input_tokens`, `output_tokens`, `total_tokens`, and optional `cost`.
  Interrupt events (`permission.asked` / `question.asked`) are mapped to
  `TaskStatusUpdateEvent(final=false, state=input-required)` with details at
  `metadata.opencode.interrupt` (including `request_id`, interrupt `type`, and
  minimal callback payload).
  Non-streaming requests return a `Task` directly.
- Non-streaming `message:send` responses may include normalized token usage at
  `Task.metadata.opencode.usage` with the same field schema.
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
  - Clients can pass `metadata.opencode.directory`, but it must stay inside
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

- `metadata.opencode.session_id`: target OpenCode session ID

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
      "opencode": {
        "session_id": "<session_id>"
      }
    }
  }'
```

## OpenCode Session Query (A2A Extension)

This service exposes OpenCode session list/message-history queries and session control methods via A2A JSON-RPC extension methods (default endpoint: `POST /`). No extra custom REST endpoint is introduced.

- Trigger: call extension methods through A2A JSON-RPC
- Auth: same `Authorization: Bearer <token>`
- Privacy guard: when `A2A_LOG_PAYLOADS=true`, request/response bodies are still
  suppressed for `method=opencode.sessions.*`
- Endpoint discovery: prefer `additional_interfaces[]` with
  `transport=jsonrpc` from Agent Card
- Notification behavior: for `opencode.sessions.*`, requests without `id`
  return HTTP `204 No Content`
- Result format (query methods):
  - `result.items` is always an array of A2A standard objects
  - session list => `Task` with `status.state=completed`
  - message history => `Message`
  - `contextId` is an A2A context key derived by the adapter
    (format: `ctx:opencode-session:<session_id>`, not raw OpenCode session ID)
  - OpenCode session identity is exposed explicitly at `metadata.opencode.session_id`
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
    "params": {"limit": 20}
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
      "limit": 50
    }
  }'
```

### Session Prompt Async (`opencode.sessions.prompt_async`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 21,
    "method": "opencode.sessions.prompt_async",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "parts": [{"type": "text", "text": "Continue and summarize next steps."}],
        "noReply": true
      },
      "metadata": {
        "opencode": {
          "directory": "/path/inside/workspace"
        }
      }
    }
  }'
```

Response:

- success => `{"ok": true, "session_id": "<session_id>"}` (JSON-RPC result)
- notification (no `id`) => HTTP `204 No Content`
- error types:
  - `SESSION_NOT_FOUND`
  - `SESSION_FORBIDDEN`
  - `METHOD_DISABLED` (not applicable to prompt_async)
  - `UPSTREAM_UNREACHABLE`
  - `UPSTREAM_HTTP_ERROR`
  - `UPSTREAM_PAYLOAD_ERROR`

Validation notes:

- `metadata.opencode.directory` follows the same normalization and boundary rules
  as message send (`realpath` + workspace boundary check).
- Control methods enforce session owner guard based on request identity.

### Session Command (`opencode.sessions.command`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 22,
    "method": "opencode.sessions.command",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "command": "/review",
        "arguments": "focus on security findings"
      },
      "metadata": {
        "opencode": {
          "directory": "/path/inside/workspace"
        }
      }
    }
  }'
```

Response:

- success => `{"item": <A2A Message>}` (JSON-RPC result)
- notification (no `id`) => HTTP `204 No Content`

### Session Shell (`opencode.sessions.shell`)

`opencode.sessions.shell` is disabled by default. Enable with
`A2A_ENABLE_SESSION_SHELL=true`.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 23,
    "method": "opencode.sessions.shell",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "agent": "code-reviewer",
        "command": "git status --short"
      }
    }
  }'
```

Response:

- success => `{"item": <A2A Message>}` (JSON-RPC result)
- disabled => JSON-RPC error `METHOD_DISABLED`
- notification (no `id`) => HTTP `204 No Content`

## OpenCode Interrupt Callback (A2A Extension)

When stream metadata reports an interrupt request at `metadata.opencode.interrupt`,
clients can reply through JSON-RPC extension methods:

- `opencode.permission.reply`
  - required: `request_id`
  - required: `reply` (`once` / `always` / `reject`)
  - optional: `message`
  - optional: `metadata.opencode.directory`
- `opencode.question.reply`
  - required: `request_id`
  - required: `answers` (`Array<Array<string>>`)
  - optional: `metadata.opencode.directory`
- `opencode.question.reject`
  - required: `request_id`
  - optional: `metadata.opencode.directory`

Notes:

- `request_id` must be a live interrupt request observed from stream metadata
  (`metadata.opencode.interrupt.request_id`).
- The server keeps an in-memory interrupt binding cache; callbacks with unknown
  or expired `request_id` are rejected.
- Callback requests are validated against interrupt type and caller identity.
- Callback context variables use nested metadata namespace:
  `params.metadata.opencode.*` (for example `metadata.opencode.directory`).
- Successful callback responses are minimal: only `ok` and `request_id`.
- Error types:
  - `INTERRUPT_REQUEST_NOT_FOUND`
  - `INTERRUPT_REQUEST_EXPIRED`
  - `INTERRUPT_TYPE_MISMATCH`
  - `UPSTREAM_UNREACHABLE`
  - `UPSTREAM_HTTP_ERROR`

Permission reply example:

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "opencode.permission.reply",
    "params": {
      "request_id": "<request_id>",
      "reply": "once",
      "metadata": {
        "opencode": {
          "directory": "/path/inside/workspace"
        }
      }
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

## Cancellation Semantics (`tasks/cancel`)

- The service first marks the A2A task as `canceled` and keeps cancel requests responsive.
- For running tasks, the service attempts upstream OpenCode `POST /session/{sessionID}/abort` to stop generation.
- Upstream interruption is best-effort: if upstream returns 404, network errors, or other HTTP errors, A2A cancellation still completes with `TaskState.canceled`.
- Idempotency contract: repeated `tasks/cancel` on an already `canceled` task returns the current terminal task state without error.
- Terminal subscribe contract: calling `subscribe` on a terminal task replays one terminal `Task` snapshot and then closes the stream.
- The cancel path emits metric log records (`logger=opencode_a2a_serve.agent`):
  - `a2a_cancel_requests_total`
  - `a2a_cancel_abort_attempt_total`
  - `a2a_cancel_abort_success_total`
  - `a2a_cancel_abort_timeout_total`
- `a2a_cancel_abort_error_total`
- `a2a_cancel_duration_ms` (with `abort_outcome` label)

## Extension Contract Change Checklist

When changing extension methods/errors or extension metadata, validate the
single-source contract and generated surfaces together:

1. Update `src/opencode_a2a_serve/extension_contracts.py` first (SSOT).
2. Run focused contract checks:

```bash
uv run pytest tests/test_extension_contract_consistency.py
```

3. Run baseline quality gates before commit:

```bash
uv run pre-commit run --all-files
uv run mypy src/opencode_a2a_serve
uv run pytest
```

The contract check fails when any of these drift:
- SSOT (`extension_contracts.py`)
- Agent Card extension params
- OpenAPI `POST /` extension metadata (`x-opencode-extension-contracts`) or examples
- Notification behavior (`204 No Content`) for extension methods

## Development Setup

```bash
uv run pre-commit install
uv run mypy src/opencode_a2a_serve
uv run pytest
```

`uv run pytest` includes coverage reporting and enforces `--cov-fail-under=80`.
