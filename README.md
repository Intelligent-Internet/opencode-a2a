# opencode-a2a-serve

这是一个将 OpenCode 服务封装为 A2A 服务的适配层（FastAPI + A2A SDK），对外提供：

- A2A HTTP+JSON（REST）：`/v1/message:send`、`/v1/message:stream`、`/v1/tasks/{task_id}:resubscribe` 等
- A2A JSON-RPC：`POST /`（用于扩展能力，例如会话查询）

它本质上是一个“协议桥接”与“安全边界收口”：将 A2A 的 message/task 语义转发到 OpenCode 的 session/message/event 接口，并补齐鉴权、可观测与续聊契约。

> 重要：服务启动 **必须** 设置 `A2A_BEARER_TOKEN`（见 `docs/guide.md`）。

## 安全边界（必须阅读）

- 当前实现与部署方式下，`opencode` 进程需要读取 LLM provider API token（例如 `GOOGLE_GENERATIVE_AI_API_KEY`）。
- 这意味着 `opencode agent` 存在通过套话、拼接等方式泄露敏感环境变量的风险，**不能视为“agent 无法获知 key”**。
- 因此，`opencode-a2a-serve` 当前仅建议用于内部实例：少数可信成员共用 repo 与 LLM key。
- 若要引入到 cgnext 作为通用能力，必须先审视并定义 LLM provider token 的安全方案（如租户隔离、代理托管、审计与轮换策略）。

额外说明：
- A2A 服务侧使用 Bearer Token 做最小鉴权收口（`A2A_BEARER_TOKEN`）。
- 开启 `A2A_LOG_PAYLOADS=true` 可能记录请求/响应正文；但对 `opencode.sessions.*` 的 JSON-RPC 会话查询，服务会自动避免写入 body 日志（防止泄露聊天历史）。

## 能力概览

- A2A 标准对话：将 `message:send` / `message:stream` 转发到 OpenCode session/message。
- SSE Streaming：`/v1/message:stream` 输出 `TaskArtifactUpdateEvent` 增量，结束时发送 `TaskStatusUpdateEvent(final=true)`。
- 断线续订：SSE 断线后可 `POST /v1/tasks/{task_id}:resubscribe` 重新订阅事件流（task 未进入终态时）。
- 续聊契约（绑定历史 OpenCode session）：客户端通过 `metadata.opencode_session_id` 显式指定目标 session（见下文与 `docs/guide.md`）。
- OpenCode 会话查询（A2A Extension via JSON-RPC）：`opencode.sessions.list` / `opencode.sessions.messages.list`（见 `docs/guide.md`）。

## 快速启动

1) 先启动 OpenCode：

```bash
opencode serve
```

2) 安装依赖：

```bash
uv sync --all-extras
```

3) 启动 A2A 服务：

```bash
A2A_BEARER_TOKEN=dev-token uv run opencode-a2a-serve
```

默认监听：`http://127.0.0.1:8000`

A2A Agent Card：`http://127.0.0.1:8000/.well-known/agent-card.json`

最小调用示例：

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "content": [{"text": "你好，介绍下这个仓库"}]
    }
  }'
```

## 配置速览

完整配置见 `docs/guide.md`。这里列出最关键的几项：

- `OPENCODE_BASE_URL`：OpenCode 地址（默认 `http://127.0.0.1:4096`）
- `OPENCODE_DIRECTORY`：OpenCode 的 directory 参数（可选；服务端控制，客户端不可覆盖）
- `A2A_BEARER_TOKEN`：必填；用于 Bearer Token 校验
- `A2A_PUBLIC_URL`：对外可访问的 A2A 地址前缀（用于 Agent Card 的 `url`/interfaces；反代/域名场景建议设置）
- `A2A_STREAMING`：是否启用 SSE streaming（默认 `true`）
- `A2A_SESSION_CACHE_TTL_SECONDS` / `A2A_SESSION_CACHE_MAXSIZE`：`(identity, contextId) -> session_id` 内存映射缓存配置（用于未显式绑定 session 的续聊）

## 续聊契约（绑定到历史 OpenCode session）

当下游希望“选择一个历史 OpenCode session 后继续对话”时，应在每次 invoke 的请求 `metadata` 中显式传入：

- `metadata.opencode_session_id`: 目标 OpenCode session id（例如 `ses_xxx`）

服务端行为：

- 若提供 `metadata.opencode_session_id`：优先发送消息到该 session（不新建 session）。
- 若未提供：服务端会创建新 session，并在内存中缓存 `(identity, contextId) -> session_id`（带 TTL 与最大容量限制）。

最小 curl 示例：

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer dev-token' \
  -d '{
    "message": {
      "messageId": "msg-continue-1",
      "role": "ROLE_USER",
      "content": [{"text": "继续刚才的对话：请把上次的结论再总结一下"}]
    },
    "metadata": {
      "opencode_session_id": "<session_id>"
    }
  }'
```

## OpenCode 会话查询（A2A Extension via JSON-RPC）

本服务通过 A2A Extension 的方式暴露“OpenCode 会话列表/历史消息查询”能力，不额外提供自定义 REST 端点。

- 调用方式：通过 A2A JSON-RPC（默认 `POST /`）调用扩展方法
- 鉴权：复用同一个 `Authorization: Bearer <token>`
- 返回：`result.items` 为 A2A 标准对象（会话列表为 Task；消息历史为 Message）；OpenCode 原始 item 放在 `metadata.opencode.raw`

会话列表（`opencode.sessions.list`）：

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

会话消息历史（`opencode.sessions.messages.list`）：

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

## 文档

- 使用指南（配置/鉴权/Streaming/客户端示例）：`docs/guide.md`
- 部署（systemd 多实例）：`docs/deployment.md`
- 本地/临时脚本：`scripts/README.md`

## 开发与回归

```bash
uv run pre-commit run --all-files
uv run pytest
```
