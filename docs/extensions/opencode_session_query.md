# OpenCode Session Query Extension

本扩展用于在不增加自定义 REST 端点的前提下，通过 **A2A JSON-RPC** 暴露 OpenCode serve 的：

- 会话列表（sessions）
- 指定会话的历史消息（messages）

## Extension URI

`urn:opencode-a2a:opencode-session-query/v1`

## 鉴权

复用 A2A 服务本身的鉴权方式（本仓库默认 `Authorization: Bearer <A2A_BEARER_TOKEN>`）。

## 最小 params 契约（Agent Card）

Agent Card 的 `capabilities.extensions[]` 会声明：

- `uri`: `urn:opencode-a2a:opencode-session-query/v1`
- `required`: `false`
- `params.methods.list_sessions`: JSON-RPC method 名（默认 `opencode.sessions.list`）
- `params.methods.get_session_messages`: JSON-RPC method 名（默认 `opencode.sessions.messages.list`）
- `params.pagination`: 当前实现为透传式（可传 `page/size` 或 `cursor/limit`，服务端会作为 query params 透传给 OpenCode serve）
- `params.result_schema`: 可选；当前为空（客户端按 JSON 透传处理）

## 请求格式（JSON-RPC）

客户端使用 A2A JSON-RPC（默认 `POST /`），调用 extension 声明的方法。

### 1) list_sessions

method: `opencode.sessions.list`

params（可选）：

- `query`: object，可选；透传 query params 给 OpenCode serve（key/value 建议为字符串）
- `page/size` 或 `cursor/limit`: 可选；作为 query params 透传

示例：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "opencode.sessions.list",
  "params": {
    "query": {}
  }
}
```

### 2) get_session_messages

method: `opencode.sessions.messages.list`

params：

- `session_id`: string，必填
- `query`: object，可选
- `page/size` 或 `cursor/limit`: 可选

示例：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "opencode.sessions.messages.list",
  "params": {
    "session_id": "sess-xxx"
  }
}
```

## 响应格式（JSON-RPC）

服务端返回标准 JSON-RPC response：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "...": "OpenCode serve JSON payload (透传)"
  }
}
```

其中 `result` 为 OpenCode serve 的 JSON 响应 **原样透传**（schema 以 OpenCode server 为准）。

## 日志与隐私

当 `A2A_LOG_PAYLOADS=true` 时，若检测到 `method=opencode.sessions.*` 的 JSON-RPC 请求，本服务不会将请求/响应 body 写入日志，以避免泄露聊天历史内容。
