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
- `params.pagination`: 明确分页契约（见下方说明），仅支持 `page/size`
- `params.errors`: 业务错误码与 error.data 的稳定字段
- `params.result_envelope`: 返回 envelope 契约（避免客户端绑定 OpenCode 私有 schema）

说明：

- `directory` 参数由服务端配置（`OPENCODE_DIRECTORY`）控制，客户端通过 `query` 传入的 `directory` 会被忽略（不可覆盖）。
- JSON-RPC 调用 URL 不建议由 base_url 推导：应从 Agent Card 的 `additional_interfaces[]` 中选择 `transport==jsonrpc` 的 `url`。

## 请求格式（JSON-RPC）

客户端使用 A2A JSON-RPC（默认 `POST /`），调用 extension 声明的方法。

### 1) list_sessions

method: `opencode.sessions.list`

params（可选）：

- `query`: object，可选；透传 query params 给 OpenCode serve（key/value 建议为字符串）
- `page/size`: 可选；作为 query params 透传（仅支持 page/size；`size` 最大值见 Agent Card `params.pagination.max_size`）

示例：

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

### 2) get_session_messages

method: `opencode.sessions.messages.list`

params：

- `session_id`: string，必填
- `query`: object，可选
- `page/size`: 可选

示例：

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

## 响应格式（JSON-RPC）

服务端返回标准 JSON-RPC response：

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

其中：

- `result.items` 始终为数组：
  - `opencode.sessions.list`：items 为 **A2A Task** 数组（`task.id == task.contextId == opencode session_id`；`status.state` 固定为 `completed`）。
  - `opencode.sessions.messages.list`：items 为 **A2A Message** 数组（`message.contextId == opencode session_id`）。
  - OpenCode 原始 item 不丢弃，放在 `metadata.opencode.raw` 中。
  - 会话标题：`opencode.sessions.list` 会在 `metadata.opencode.title` 提供一个可直接渲染的标题；优先从 OpenCode session payload 提取，若无则为占位值 `Untitled session`。
- `result.pagination` 为稳定的分页 envelope（page/size 为空表示本次请求未显式传入）。

## 日志与隐私

当 `A2A_LOG_PAYLOADS=true` 时，若检测到 `method=opencode.sessions.*` 的 JSON-RPC 请求，本服务不会将请求/响应 body 写入日志，以避免泄露聊天历史内容。
