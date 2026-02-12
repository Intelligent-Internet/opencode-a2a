# OpenCode Session Query Extension

This extension exposes OpenCode session-query capabilities through **A2A JSON-RPC**, without adding custom REST endpoints:

- session list
- message history for a specific session

## Extension URI

`urn:opencode-a2a:opencode-session-query/v1`

## Authentication

Uses the same A2A auth mechanism as the service itself (default: `Authorization: Bearer <A2A_BEARER_TOKEN>`).

## Minimal `params` Contract (Agent Card)

`capabilities.extensions[]` in Agent Card declares:

- `uri`: `urn:opencode-a2a:opencode-session-query/v1`
- `required`: `false`
- `params.methods.list_sessions`: JSON-RPC method name
  (default `opencode.sessions.list`)
- `params.methods.get_session_messages`: JSON-RPC method name
  (default `opencode.sessions.messages.list`)
- `params.pagination`: explicit pagination contract (`page/size` only)
- `params.errors`: business error codes and stable `error.data` fields
- `params.result_envelope`: stable response envelope contract

Notes:

- `directory` is controlled by server config (`OPENCODE_DIRECTORY`); client
  `query.directory` is ignored.
- Do not infer JSON-RPC URL from a base URL string. Read it from Agent Card
  `additional_interfaces[]` where `transport == jsonrpc`.

## Request Format (JSON-RPC)

Use A2A JSON-RPC (default `POST /`) and call extension methods.

### 1) `list_sessions`

method: `opencode.sessions.list`

params (optional):

- `query`: object, forwarded as query params to OpenCode
- `page/size`: forwarded query params (`size` max is exposed via
  Agent Card `params.pagination.max_size`)

Example:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "opencode.sessions.list",
  "params": {
    "page": 1,
    "size": 20,
    "query": {}
  }
}
```

### 2) `get_session_messages`

method: `opencode.sessions.messages.list`

params:

- `session_id`: string, required
- `query`: object, optional
- `page/size`: optional

Example:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "opencode.sessions.messages.list",
  "params": {
    "session_id": "sess-xxx",
    "page": 1,
    "size": 50
  }
}
```

## Response Format (JSON-RPC)

Standard JSON-RPC response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "items": [],
    "pagination": {
      "mode": "page_size",
      "page": 1,
      "size": 20
    }
  }
}
```

Where:

- `result.items` is always an array:
  - `opencode.sessions.list`: **A2A Task** array
    (`task.id == task.contextId == opencode session_id`,
    `status.state == completed`)
  - `opencode.sessions.messages.list`: **A2A Message** array
    (`message.contextId == opencode session_id`)
  - raw OpenCode items are preserved in `metadata.opencode.raw`
  - session title is exposed in `metadata.opencode.title` (fallback:
    `Untitled session`)
- `result.pagination` is a stable pagination envelope
  (null `page/size` means caller did not pass those params)

## Logging and Privacy

When `A2A_LOG_PAYLOADS=true`, if request method matches `opencode.sessions.*`, this service suppresses request/response body logging to reduce chat-history exposure risk.
