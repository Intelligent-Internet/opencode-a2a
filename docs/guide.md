# Usage Guide

This guide covers configuration, authentication, API behavior, streaming
re-subscription, and A2A client examples.
It is the canonical document for implementation-level protocol contracts and
JSON-RPC extension details; README stays at overview level.

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
Deployment supervision is intentionally out of scope for this project; use your
own process manager, container runtime, or host orchestration.

Key variables to understand protocol behavior:

- `A2A_BEARER_TOKEN`: required for all authenticated runtime requests.
- `OPENCODE_BASE_URL`: upstream OpenCode HTTP endpoint. Default:
  `http://127.0.0.1:4096`. In two-process deployments, set it explicitly.
- `OPENCODE_WORKSPACE_ROOT`: service-level default workspace root exposed to
  OpenCode when clients do not request a narrower directory override.
- `A2A_ALLOW_DIRECTORY_OVERRIDE`: controls whether clients may pass
  `metadata.opencode.directory`.
- `A2A_ENABLE_SESSION_SHELL`: gates high-risk JSON-RPC method
  `opencode.sessions.shell`.
- `A2A_SANDBOX_MODE` / `A2A_SANDBOX_FILESYSTEM_SCOPE` /
  `A2A_SANDBOX_WRITABLE_ROOTS`: declarative execution-boundary metadata for
  sandbox mode, filesystem scope, and optional writable roots.
- `A2A_NETWORK_ACCESS` / `A2A_NETWORK_ALLOWED_DOMAINS`: declarative
  execution-boundary metadata for network policy and optional allowlist
  disclosure.
- `A2A_APPROVAL_POLICY` / `A2A_APPROVAL_ESCALATION_BEHAVIOR`: declarative
  execution-boundary metadata for approval workflow.
- `A2A_WRITE_ACCESS_SCOPE` / `A2A_WRITE_ACCESS_OUTSIDE_WORKSPACE`: declarative
  execution-boundary metadata for write scope and whether writes may extend
  outside the primary workspace boundary.
- `A2A_HOST` / `A2A_PORT`: runtime bind address. Defaults:
  `127.0.0.1:8000`.
- `A2A_PUBLIC_URL`: public base URL advertised by the Agent Card. Default:
  `http://127.0.0.1:8000`.
- `A2A_LOG_LEVEL`: runtime log level. Default: `WARNING`.
- `A2A_LOG_PAYLOADS` / `A2A_LOG_BODY_LIMIT`: payload logging behavior and
  truncation. When `A2A_LOG_LEVEL=DEBUG`, upstream OpenCode stream events are
  also logged with preview truncation controlled by `A2A_LOG_BODY_LIMIT`.
- `A2A_STREAM_SSE_PING_SECONDS`: explicit SSE keepalive interval for REST
  streaming endpoints (`/v1/message:stream` and `/v1/tasks/{id}:subscribe`).
  Default: `15`.
- `A2A_MAX_REQUEST_BODY_BYTES`: runtime request-body limit. Oversized requests
  return HTTP `413`.
- `A2A_SESSION_CACHE_TTL_SECONDS` / `A2A_SESSION_CACHE_MAXSIZE`: session cache
  behavior for `(identity, contextId) -> session_id`.
- `A2A_INTERRUPT_REQUEST_TTL_SECONDS`: active retention window for the
  in-memory interrupt request binding cache used by `a2a.interrupt.*`
  callback methods. Default: `10800` seconds (`180` minutes).
- `A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS`: retention window for expired
  interrupt tombstones after active TTL has elapsed. During this window,
  repeated replies keep returning `INTERRUPT_REQUEST_EXPIRED` instead of
  falling through to `INTERRUPT_REQUEST_NOT_FOUND`. Default: `600` seconds
  (`10` minutes).
- `A2A_CANCEL_ABORT_TIMEOUT_SECONDS`: best-effort timeout for upstream
  `session.abort` in cancel flow.
- `OPENCODE_TIMEOUT` / `OPENCODE_TIMEOUT_STREAM`: upstream request timeout and
  optional stream timeout override.
- Runtime authentication is bearer-token only via `A2A_BEARER_TOKEN`.

Execution-boundary metadata is intentionally declarative deployment metadata:
it is published through `RuntimeProfile`, Agent Card, OpenAPI, and `/health`,
and should not be interpreted as a live per-request privilege snapshot or a
runtime CLI self-inspection result.

Recommended two-process example:

```bash
opencode serve --hostname 127.0.0.1 --port 4096
```

Configure provider auth and the default model on the OpenCode side before
starting that upstream process:

- Add credentials with `opencode auth login` or `/connect`.
- Check available model IDs with `opencode models` or `opencode models <provider>`.
- Set the default model in `opencode.json`, for example:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "google/gemini-3-pro"
}
```

If your provider uses environment variables for auth, export them before
starting `opencode serve`.

Then start `opencode-a2a-server` against that explicit upstream URL:

```bash
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_BEARER_TOKEN=dev-token \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
opencode-a2a-server serve
```

## Core Behavior

- The service forwards A2A `message:send` to OpenCode session/message calls.
- Main chat requests may override the upstream model for one request through
  `metadata.shared.model`.
- Provider/model catalog discovery is available through
  `opencode.providers.list` and `opencode.models.list`.
- Main chat input supports structured A2A `parts` passthrough:
  - `TextPart` is forwarded as an OpenCode text part.
  - `FilePart(FileWithBytes)` is forwarded as a `file` part with a `data:` URL.
  - `FilePart(FileWithUri)` is forwarded as a `file` part with the original
    URI.
  - `DataPart` is currently rejected explicitly; it is not silently downgraded.
- Task state defaults to `completed` for successful turns.
- The deployment profile is single-tenant and shared-workspace: one server
  instance exposes one OpenCode workspace/environment to all consumers bound to
  that instance.

## Streaming Contract

- Streaming is always enabled in this server profile; `message:stream` is part
  of the stable runtime baseline.
- Streaming (`/v1/message:stream`) emits incremental
  `TaskArtifactUpdateEvent` and then
  `TaskStatusUpdateEvent(final=true)`.
- Stream artifacts carry `artifact.metadata.shared.stream.block_type` with
  values `text` / `reasoning` / `tool_call`.
- All chunks share one stream artifact ID and preserve original timeline via
  `artifact.metadata.shared.stream.event_id`.
- `artifact.metadata.shared.stream.message_id` remains best-effort metadata:
  when upstream omits `message_id`, the service falls back to a stable
  request-scoped message identity.
- `artifact.metadata.shared.stream.sequence` carries the canonical per-request
  stream sequence.
- A final snapshot is emitted only when streaming chunks did not already
  produce the same final text.
- Stream routing is schema-first: the service classifies chunks primarily by
  OpenCode `part.type` and `part_id` state rather than inline text markers.
- `message.part.delta` and `message.part.updated` are merged per `part_id`;
  out-of-order deltas are buffered and replayed when the corresponding
  `part.updated` arrives.
- Structured `tool` parts are emitted as `tool_call` blocks backed by
  `DataPart(data={...})`, while `text` and `reasoning` continue to use
  `TextPart`.
- `tool_call` block payloads are normalized structured objects that may expose
  fields such as `call_id`, `tool`, `status`, `title`, `subtitle`, `input`,
  `output`, and `error`.
- Final status event metadata may include normalized token usage at
  `metadata.shared.usage` with fields such as `input_tokens`,
  `output_tokens`, `total_tokens`, optional `reasoning_tokens`, optional
  `cache_tokens.read_tokens` / `cache_tokens.write_tokens`, and optional
  `cost`.
- Usage is extracted from documented info payloads and supported usage parts
  such as `step-finish`; non-usage parts with similar fields are ignored.
- Interrupt events (`permission.asked` / `question.asked`) are mapped to
  `TaskStatusUpdateEvent(final=false, state=input-required)` with details at
  `metadata.shared.interrupt`, including `request_id`, interrupt `type`,
  `phase=asked`, and a normalized minimal callback payload.
- Resolved interrupt events (`permission.replied` / `question.replied` /
  `question.rejected`) are emitted as
  `TaskStatusUpdateEvent(final=false, state=working)` with
  `metadata.shared.interrupt.phase=resolved` and a normalized
  `metadata.shared.interrupt.resolution`.
- Duplicate or unknown resolved events are suppressed unless the matching
  request is still pending.
- Non-streaming requests return a `Task` directly.
- Non-streaming `message:send` responses may include normalized token usage at
  `Task.metadata.shared.usage` with the same field schema.

## Auth, Limits, and Failure Contract

- Requests require `Authorization: Bearer <token>`; otherwise `401` is
  returned. Agent Card endpoints are public.
- Requests above `A2A_MAX_REQUEST_BODY_BYTES` are rejected with HTTP `413`
  before transport handling.
- For validation failures, missing context (`task_id` / `context_id`), or
  internal errors, the service attempts to return standard A2A failure events
  via `event_queue`.
- Failure events include concrete error details with `failed` state.

## Directory Rules

- Clients can pass `metadata.opencode.directory`, but it must stay inside
  `${OPENCODE_WORKSPACE_ROOT}` or the service runtime root when no workspace
  root is configured.
- `OPENCODE_WORKSPACE_ROOT` is the service-level default workspace root used
  when clients do not request a narrower directory override.
- All paths are normalized with `realpath` to prevent `..` or symlink boundary
  bypass.
- If `A2A_ALLOW_DIRECTORY_OVERRIDE=false`, only the default directory is
  accepted.

## Wire Contract

The service publishes a machine-readable wire contract through Agent Card and
OpenAPI metadata to describe the current runtime method boundary.

Use it to answer:

- which JSON-RPC methods are part of the current A2A core baseline
- which JSON-RPC methods are custom extensions
- which methods are deployment-conditional rather than currently active
- what error shape is returned for unsupported JSON-RPC methods

Current behavior:

- Core JSON-RPC methods are declared under `core.jsonrpc_methods`.
- Core HTTP endpoints are declared under `core.http_endpoints`.
- Extension JSON-RPC methods are declared under `extensions.jsonrpc_methods`.
- Deployment-conditional methods are declared under
  `extensions.conditionally_available_methods`.
- Shared metadata extension URIs such as session binding and streaming are
  listed under `extensions.extension_uris`.
- `all_jsonrpc_methods` is the runtime truth for the current deployment.

When `A2A_ENABLE_SESSION_SHELL=false`, `opencode.sessions.shell` is omitted from
`all_jsonrpc_methods` and exposed only through
`extensions.conditionally_available_methods`.

Unsupported method contract:

- JSON-RPC error code: `-32601`
- Error message: `Unsupported method: <method>`
- Error data fields:
  - `type=METHOD_NOT_SUPPORTED`
  - `method`
  - `supported_methods`
  - `protocol_version`

Consumer guidance:

- Discover custom JSON-RPC methods from Agent Card / OpenAPI before calling
  them.
- Treat `supported_methods` in `error.data` as the runtime truth for the
  current deployment, especially when a deployment-conditional method is
  disabled.

## Compatibility Profile

The service also publishes a machine-readable compatibility profile through
Agent Card and OpenAPI metadata.

Its purpose is to declare:

- the stable A2A core interoperability baseline
- which custom JSON-RPC methods are deployment extensions
- which extension surfaces are required runtime metadata contracts
- which methods are deployment-conditional rather than always available

Current profile shape:

- `profile_id=opencode-a2a-single-tenant-coding-v1`
- Deployment semantics are declared under `deployment`:
  - `id=single_tenant_shared_workspace`
  - `single_tenant=true`
  - `shared_workspace_across_consumers=true`
  - `tenant_isolation=none`
- Runtime features are declared under `runtime_features`:
  - `directory_binding.allow_override=true|false`
  - `directory_binding.scope=workspace_root_or_descendant|workspace_root_only`
  - `session_shell.enabled=true|false`
  - `session_shell.availability=enabled|disabled`
  - `execution_environment.sandbox.mode=unknown|read-only|workspace-write|danger-full-access|custom`
  - `execution_environment.sandbox.filesystem_scope=unknown|workspace_only|workspace_and_declared_roots|unrestricted|custom`
  - `execution_environment.network.access=unknown|disabled|enabled|restricted|custom`
  - `execution_environment.approval.policy=unknown|never|on-request|on-failure|untrusted|custom`
  - `execution_environment.approval.escalation_behavior=unknown|manual|automatic|unsupported|custom`
  - `execution_environment.write_access.scope=unknown|none|workspace_only|workspace_and_declared_roots|unrestricted|custom`
  - `execution_environment.write_access.outside_workspace=unknown|allowed|disallowed|custom`
  - `service_features.streaming.enabled=true`
  - `service_features.health_endpoint.enabled=true`
- Optional disclosure fields are emitted only when explicitly configured:
  - `execution_environment.sandbox.writable_roots`
  - `execution_environment.network.allowed_domains`
- Core methods and endpoints are declared under `core`.
- Extension retention policy is declared under `extension_retention`.
- Per-method retention and availability are declared under `method_retention`.
- Extension params and `/health` expose the same structured `profile` object; there is no
  separate legacy deployment-context shape.
- Execution-environment values are deployment declarations, not a per-turn
  runtime approval or sandbox result.

Retention guidance:

- Treat core A2A methods as the generic client interoperability baseline.
- Treat session binding, request-scoped model selection, and streaming metadata
  contracts as required for the current deployment model.
- Treat `a2a.interrupt.*` methods as shared extensions.
- Treat `opencode.sessions.*`, `opencode.providers.*`, and `opencode.models.*`
  as provider-private OpenCode extensions rather than portable A2A baseline
  capabilities.
- Treat `opencode.sessions.shell` as deployment-conditional and discover it
  from the declared profile and current wire contract before calling it.

## Multipart Input Example

Minimal JSON-RPC example with text + file input:

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-1",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "msg-multipart-1",
        "role": "user",
        "parts": [
          {
            "kind": "text",
            "text": "Please summarize this file."
          },
          {
            "kind": "file",
            "file": {
              "name": "report.pdf",
              "mimeType": "application/pdf",
              "uri": "file:///workspace/report.pdf"
            }
          }
        ]
      }
    }
  }'
```

Current compatibility note:

- `TextPart` and `FilePart` are supported.
- `DataPart` input is not supported and is rejected with an explicit error.

## Extension Capability Overview

The README provides product positioning and quick start guidance. This guide
focuses on how to consume the declared capabilities.

Important distinction:

- Agent Card extension declarations answer "what capability is available?"
- Runtime payload metadata answers "what happened on this request/stream?"
- Clients should not treat runtime metadata alone as a substitute for
  capability discovery when an extension URI is already declared.

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

This extension declares that the main chat path accepts a request-scoped model
override through shared metadata:

- `metadata.shared.model.providerID`
- `metadata.shared.model.modelID`

Declaration versus runtime:

- The URI `urn:a2a:model-selection/v1` is the capability declaration.
- The actual request payload carries the runtime override under
  `metadata.shared.model`.

Behavior:

- The override is optional and scoped to one main chat request.
- Both `providerID` and `modelID` must be present together.
- When both fields are present, the service forwards them to the upstream
  OpenCode request as a model preference.
- When the fields are absent, the upstream OpenCode default behavior applies.

Consumer guidance:

- Use Agent Card discovery to confirm the shared model-selection contract is
  available before sending overrides.
- Treat `metadata.shared.model` as request-scoped preference data rather than
  deployment configuration.
- Provider auth and service-level model defaults belong to `opencode serve`,
  not to `opencode-a2a-server`.

Minimal example:

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-model-1",
      "role": "ROLE_USER",
      "content": [{"text": "Explain the current branch status."}]
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
    `total_tokens`, optional `reasoning_tokens`, optional
    `cache_tokens.read_tokens` / `cache_tokens.write_tokens`, and optional
    `cost`
- `metadata.shared.interrupt`
  - normalized interrupt request or resolution metadata including `request_id`,
    `type`, `phase`, optional `resolution`, and callback-safe details
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
- `text` and `reasoning` blocks use `TextPart`, while `tool_call` uses
  `DataPart`
- `message_id` and `event_id` preserve stable timeline identity where possible
- `sequence` is the per-request canonical stream sequence
- final task/status metadata may repeat normalized usage and interrupt context
  even after the streaming phase ends

## OpenCode Session Query A2A Extension

This service exposes OpenCode session list/message-history queries and session
control methods via A2A JSON-RPC extension methods (default endpoint: `POST /`).
No extra custom REST endpoint is introduced.

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
  - limit pagination defaults to `20`; requests above `100` are rejected
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
        "noReply": true,
        "model": {
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
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
- `request.model` uses the same shape as `metadata.shared.model` and is scoped
  only to the current session-control request.
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
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
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

Response:

- success => `{"item": <A2A Message>}` (JSON-RPC result)
- disabled => JSON-RPC error `METHOD_DISABLED`
- notification (no `id`) => HTTP `204 No Content`

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
- The cache retention windows are controlled by
  `A2A_INTERRUPT_REQUEST_TTL_SECONDS` (default: `10800` seconds / `180`
  minutes) and `A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS` (default: `600`
  seconds / `10` minutes). After the active TTL elapses, the server keeps a
  short-lived tombstone so repeated replies continue to return
  `INTERRUPT_REQUEST_EXPIRED` before eventually aging out to
  `INTERRUPT_REQUEST_NOT_FOUND`.
- These values are deployment/runtime settings and are intentionally not part
  of the shared extension method contract.
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
- The service emits lightweight metric log records (`logger=opencode_a2a_server.execution.executor`):
  - `a2a_stream_requests_total`
  - `a2a_stream_active` (`value=1` when a stream starts, `value=-1` when it closes)
  - `opencode_stream_retries_total`
  - `tool_call_chunks_emitted_total`
  - `interrupt_requests_total`
  - `interrupt_resolved_total`
- The cancel path also emits:
  - `a2a_cancel_requests_total`
  - `a2a_cancel_abort_attempt_total`
  - `a2a_cancel_abort_success_total`
  - `a2a_cancel_abort_timeout_total`
  - `a2a_cancel_abort_error_total`
  - `a2a_cancel_duration_ms` (with `abort_outcome` label)
