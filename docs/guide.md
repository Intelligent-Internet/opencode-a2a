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

## Runtime Environment Variables

This section keeps only the protocol-relevant variables.
For the full runtime variable catalog and defaults, see
[`../src/opencode_a2a_server/config.py`](../src/opencode_a2a_server/config.py).
For deploy-time inputs and systemd-oriented parameters, see
[`../scripts/deploy_readme.md`](../scripts/deploy_readme.md).

Key variables to understand protocol behavior:

- `A2A_BEARER_TOKEN`: required for all authenticated runtime requests.
- `A2A_STREAMING`: enables/disables SSE endpoint `/v1/message:stream`.
- `A2A_ALLOW_DIRECTORY_OVERRIDE`: controls whether clients may pass
  `metadata.opencode.directory`.
- `A2A_ENABLE_SESSION_SHELL`: gates high-risk JSON-RPC method
  `opencode.sessions.shell`.
- `A2A_LOG_PAYLOADS` / `A2A_LOG_BODY_LIMIT`: payload logging behavior and
  truncation.
- `A2A_SESSION_CACHE_TTL_SECONDS` / `A2A_SESSION_CACHE_MAXSIZE`: session cache
  behavior for `(identity, contextId) -> session_id`.
- `A2A_CANCEL_ABORT_TIMEOUT_SECONDS`: best-effort timeout for upstream
  `session.abort` in cancel flow.
- `A2A_OAUTH_AUTHORIZATION_URL` / `A2A_OAUTH_TOKEN_URL` /
  `A2A_OAUTH_METADATA_URL` / `A2A_OAUTH_SCOPES`: declarative OAuth2 metadata in
  Agent Card only (no runtime OAuth verification in this service).

## Service Behavior

- The service forwards A2A `message:send` to OpenCode session/message calls.
- Main chat requests may override the default upstream model via
  `metadata.shared.model` with `{ providerID, modelID }`.
- Task state defaults to `completed` for successful turns.
- Streaming (`/v1/message:stream`) emits incremental
  `TaskArtifactUpdateEvent` and then
  `TaskStatusUpdateEvent(final=true)`. Stream artifacts carry
  `artifact.metadata.shared.stream.block_type` with values
  `text` / `reasoning` / `tool_call`. All chunks share one stream
  artifact ID and preserve original timeline via
  `artifact.metadata.shared.stream.event_id`.
  `artifact.metadata.shared.stream.message_id` remains best-effort metadata:
  when upstream omits `message_id`, service falls back to a stable
  request-scoped message identity. `artifact.metadata.shared.stream.sequence`
  carries the canonical per-request stream sequence. A final snapshot is
  only emitted when stream
  chunks did not already produce the same final text.
  Stream routing is schema-first: the service classifies chunks primarily by
  OpenCode `part.type` (plus `part_id` state) rather than inline text markers.
  `message.part.delta` and `message.part.updated` are merged per `part_id`;
  out-of-order deltas are buffered and replayed when the corresponding
  `part.updated` arrives. Structured `tool` parts are emitted as `tool_call`
  blocks backed by `DataPart(data={...})`, while `text` and `reasoning`
  continue to use `TextPart`. Final status event metadata may include
  normalized token usage at `metadata.shared.usage` with fields like
  `input_tokens`, `output_tokens`, `total_tokens`, and optional `cost`.
  Usage is extracted from documented info payloads and supported usage parts
  such as `step-finish`; non-usage parts with similar fields are ignored.
  Interrupt events (`permission.asked` / `question.asked`) are mapped to
  `TaskStatusUpdateEvent(final=false, state=input-required)` with details at
  `metadata.shared.interrupt` (including `request_id`, interrupt `type`,
  `phase=asked`, and normalized minimal callback payload). Resolved interrupt
  events (`permission.replied` / `question.replied` / `question.rejected`) are
  emitted as `TaskStatusUpdateEvent(final=false, state=working)` with
  `metadata.shared.interrupt.phase=resolved` and a normalized
  `metadata.shared.interrupt.resolution`. Duplicate or unknown resolved events
  are suppressed unless the matching request is still pending.
  Non-streaming requests return a `Task` directly.
- Non-streaming `message:send` responses may include normalized token usage at
  `Task.metadata.shared.usage` with the same field schema.
- Requests require `Authorization: Bearer <token>`; otherwise `401` is
  returned. Agent Card endpoints are public.
- Within one `opencode-a2a-server` instance, all consumers share the same
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

## Extension Capability Overview

The Agent Card declares six extension URIs. Some are shared contracts that any
consumer may rely on; others stay OpenCode-specific even though they are
transported through A2A JSON-RPC.

Important distinction:

- Agent Card extension declarations answer "what capability is available?"
- Runtime payload metadata answers "what happened on this request/stream?"

Clients should not treat runtime metadata alone as a substitute for capability
discovery when an extension URI is already declared.

| Extension URI | Scope | Trigger / Call Path | Runtime fields or methods | Consumer expectation |
| --- | --- | --- | --- | --- |
| `urn:a2a:session-binding/v1` | Shared | Main chat `message/send`, `message:stream` | `metadata.shared.session.id` | Bind a request to an existing upstream session when present; otherwise let the server create/cache a new one |
| `urn:a2a:model-selection/v1` | Shared | Main chat `message/send`, `message:stream` | `metadata.shared.model.providerID`, `metadata.shared.model.modelID` | Override the default upstream model for one request only |
| `urn:a2a:stream-hints/v1` | Shared | Streaming responses | `metadata.shared.stream`, `metadata.shared.usage`, `metadata.shared.interrupt`, `metadata.shared.session` | Render blocks/usage/interrupts from canonical stream metadata and understand which shared hints may appear |
| `urn:opencode-a2a:session-query/v1` | OpenCode-specific | JSON-RPC `POST /` | `opencode.sessions.*` methods | Discover/query external sessions and call OpenCode session control methods |
| `urn:opencode-a2a:provider-discovery/v1` | OpenCode-specific | JSON-RPC `POST /` | `opencode.providers.*`, `opencode.models.*` methods | Fetch normalized OpenCode provider/model summaries |
| `urn:a2a:interactive-interrupt/v1` | Shared | JSON-RPC `POST /` | `a2a.interrupt.*` methods | Reply to shared interrupt callbacks observed from stream metadata |

## Shared Session Binding Contract

Agent Card capability:

- URI: `urn:a2a:session-binding/v1`

To continue a historical OpenCode session, include this metadata key in each
invoke request:

- `metadata.shared.session.id`: target upstream session ID

Server behavior:

- If provided, the request is sent to that exact OpenCode session.
- If omitted, a new session is created and cached by
  `(identity, contextId) -> session_id`.
- `contextId` remains the A2A conversation context key for task continuity; it
  is not a replacement for the upstream session identifier.
- OpenCode-private context such as `metadata.opencode.directory` may be
  supplied alongside `metadata.shared.session.id`, but it does not change the
  shared session-binding key.

Consumer guidance:

- Use this extension declaration to decide whether the server explicitly
  supports shared session rebinding.
- On the request path, write the upstream session identity to
  `metadata.shared.session.id`.
- On the response/query path, treat `metadata.shared.session` as runtime
  metadata and not as a separate capability declaration.

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
      "shared": {
        "session": {
          "id": "<session_id>"
        }
      }
    }
  }'
```

## Shared Model Selection Contract

Agent Card capability:

- URI: `urn:a2a:model-selection/v1`

To override the default upstream model for one main-chat request, include:

- `metadata.shared.model.providerID`
- `metadata.shared.model.modelID`

Server behavior:

- If both fields are non-empty strings, the request is sent with that upstream
  model override.
- If the object is missing, partial, or invalid, the service falls back to the
  configured default model (`OPENCODE_PROVIDER_ID` / `OPENCODE_MODEL_ID`).
- This contract applies to the main chat path only (`message/send`,
  `message:stream`). OpenCode session-control methods continue to use
  `params.request.model`.

Minimal example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-model-1",
      "role": "ROLE_USER",
      "content": [{"text": "Answer with the faster model."}]
    },
    "metadata": {
      "shared": {
        "model": {
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
        }
      }
    }
  }'
```

## Shared Stream Hints Contract

Agent Card capability:

- URI: `urn:a2a:stream-hints/v1`

This extension declares that streaming and final task payloads use canonical
shared metadata for block, usage, interrupt, and session hints.

Declaration versus runtime:

- The URI `urn:a2a:stream-hints/v1` is the capability declaration.
- The actual request/stream payloads carry the runtime hints under shared
  metadata fields.

Shared runtime fields:

- `metadata.shared.stream`
  - block-level stream metadata such as `block_type`, `source`, `message_id`,
    `event_id`, `sequence`, and `role`
- `metadata.shared.usage`
  - normalized usage data such as `input_tokens`, `output_tokens`,
    `total_tokens`, and optional `cost`
- `metadata.shared.interrupt`
  - normalized interrupt request or resolution metadata including `request_id`,
    `type`, `phase`, and callback-safe details
- `metadata.shared.session`
  - session-level metadata such as the bound upstream session ID and session
    title when available

Consumer guidance:

- Use the extension declaration to know the server emits canonical shared
  stream hints.
- Use runtime metadata to render block timelines, token usage, and interactive
  interruptions.
- Do not infer capability support only from seeing one runtime field on one
  response; rely on Agent Card discovery first when possible.
- Treat `metadata.shared.interrupt` as observation data. Callback operations
  are a separate shared capability declared by
  `urn:a2a:interactive-interrupt/v1`.

Minimal stream semantics summary:

- `text`, `reasoning`, and `tool_call` are emitted as canonical block types
- `message_id` and `event_id` preserve stable timeline identity where possible
- `sequence` is the per-request canonical stream sequence
- final task/status metadata may repeat normalized usage and interrupt context
  even after the streaming phase ends

## OpenCode Session Query & Provider Discovery (A2A Extensions)

This service exposes OpenCode session list/message-history queries, session
control methods, and provider/model discovery via A2A JSON-RPC extension
methods (default endpoint: `POST /`). No extra custom REST endpoint is
introduced.

- Trigger: call extension methods through A2A JSON-RPC
- Auth: same `Authorization: Bearer <token>`
- Privacy guard: when `A2A_LOG_PAYLOADS=true`, request/response bodies are still
  suppressed for `method=opencode.sessions.*`, `opencode.providers.*`, and
  `opencode.models.*`
- Endpoint discovery: prefer `additional_interfaces[]` with
  `transport=jsonrpc` from Agent Card
- Notification behavior: for `opencode.sessions.*`, `opencode.providers.*`, and
  `opencode.models.*`, requests without `id` return HTTP `204 No Content`
- Result format (query methods):
  - `result.items` is always an array of A2A standard objects
  - session list => `Task` with `status.state=completed`
  - message history => `Message`
  - `contextId` is an A2A context key derived by the adapter
    (format: `ctx:opencode-session:<session_id>`, not raw OpenCode session ID)
  - OpenCode session identity is exposed explicitly at `metadata.shared.session.id`
  - session title is available at `metadata.shared.session.title`

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
        "model": {
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
        },
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
- `request.model` uses the same `{ providerID, modelID }` shape as
  `metadata.shared.model`, but is scoped to OpenCode session-control methods.
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
        "arguments": "focus on security findings",
        "model": {
          "providerID": "openai",
          "modelID": "gpt-5"
        }
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

Security warning:

- This is a high-risk method because it can execute shell commands in the
  workspace context.
- Enable only for trusted operators/internal scenarios.
- Keep bearer-token rotation, owner/directory guard checks, and audit log
  monitoring enabled before turning it on.

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

### Provider List (`opencode.providers.list`)

Returns normalized provider summaries from the upstream OpenCode provider
catalog.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 24,
    "method": "opencode.providers.list",
    "params": {}
  }'
```

Response:

- success => `{"items": [...], "default_by_provider": {...}, "connected": [...]}` (JSON-RPC result)

### Model List (`opencode.models.list`)

Returns normalized, flattened model summaries. Supports optional provider filter:

- `params.provider_id`

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 25,
    "method": "opencode.models.list",
    "params": {
      "provider_id": "openai"
    }
  }'
```

Response:

- success => `{"items": [...], "default_by_provider": {...}, "connected": [...]}` (JSON-RPC result)

Response:

- success => `{"item": <A2A Message>}` (JSON-RPC result)
- disabled => JSON-RPC error `METHOD_DISABLED`
- notification (no `id`) => HTTP `204 No Content`

## Shared Interrupt Callback (A2A Extension)

When stream metadata reports an interrupt request at `metadata.shared.interrupt`,
clients can reply through JSON-RPC extension methods:

- `a2a.interrupt.permission.reply`
  - required: `request_id`
  - required: `reply` (`once` / `always` / `reject`)
  - optional: `message`
  - optional: `metadata.opencode.directory`
- `a2a.interrupt.question.reply`
  - required: `request_id`
  - required: `answers` (`Array<Array<string>>`)
  - optional: `metadata.opencode.directory`
- `a2a.interrupt.question.reject`
  - required: `request_id`
  - optional: `metadata.opencode.directory`

Notes:

- `request_id` must be a live interrupt request observed from stream metadata
  (`metadata.shared.interrupt.request_id`).
- The server keeps an in-memory interrupt binding cache; callbacks with unknown
  or expired `request_id` are rejected.
- Callback requests are validated against interrupt type and caller identity.
- Callback context variables use the shared method contract plus
  OpenCode-private metadata when needed
  (`params.metadata.opencode.directory`).
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
    "method": "a2a.interrupt.permission.reply",
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
- The cancel path emits metric log records (`logger=opencode_a2a_server.agent`):
  - `a2a_cancel_requests_total`
  - `a2a_cancel_abort_attempt_total`
  - `a2a_cancel_abort_success_total`
  - `a2a_cancel_abort_timeout_total`
- `a2a_cancel_abort_error_total`
- `a2a_cancel_duration_ms` (with `abort_outcome` label)

## Extension Contract Change Checklist

When changing extension methods/errors or extension metadata, validate the
single-source contract and generated surfaces together:

1. Update `src/opencode_a2a_server/extension_contracts.py` first (SSOT).
2. Run focused contract checks:

```bash
uv run pytest tests/test_extension_contract_consistency.py
```

3. Run baseline quality gates before commit:

```bash
uv run pre-commit run --all-files
uv run mypy src/opencode_a2a_server
uv run pytest
```

The contract check fails when any of these drift:
- SSOT (`extension_contracts.py`)
- Agent Card extension params
- OpenAPI `POST /` extension metadata (`x-a2a-extension-contracts`) or examples
- Notification behavior (`204 No Content`) for extension methods

## Development Setup

```bash
uv run pre-commit install
uv run mypy src/opencode_a2a_server
uv run pytest
```

`uv run pytest` includes coverage reporting and enforces `--cov-fail-under=80`.
