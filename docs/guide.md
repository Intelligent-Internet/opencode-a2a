# 使用指南

本指南集中说明配置、鉴权、接口行为、Streaming 断线续订，以及 A2A 客户端示例。

## 环境变量

- `OPENCODE_BASE_URL`：OpenCode 地址，默认 `http://127.0.0.1:4096`
- `OPENCODE_DIRECTORY`：OpenCode directory 参数（可选）
- `OPENCODE_PROVIDER_ID`：模型 providerID（可选）
- `OPENCODE_MODEL_ID`：模型 modelID（可选）
- `OPENCODE_AGENT`：OpenCode agent 名称（可选）
- `OPENCODE_SYSTEM`：system prompt（可选）
- `OPENCODE_VARIANT`：variant（可选）
- `OPENCODE_TIMEOUT`：请求超时秒数，默认 `120`（systemd 部署模板默认写入 `300`）
- `OPENCODE_TIMEOUT_STREAM`：streaming 请求超时秒数（可选；不设置则不限制）

- `A2A_PUBLIC_URL`：对外可访问的 A2A 地址前缀，默认 `http://127.0.0.1:8000`
- `A2A_TITLE`：Agent 名称，默认 `OpenCode A2A`
- `A2A_DESCRIPTION`：Agent 描述
- `A2A_VERSION`：Agent 版本号
- `A2A_PROTOCOL_VERSION`：A2A 协议版本，默认 `0.3.0`
- `A2A_HOST`：监听地址，默认 `127.0.0.1`
- `A2A_PORT`：监听端口，默认 `8000`
- `A2A_BEARER_TOKEN`：必填；用于 Bearer Token 校验，未设置则服务拒绝启动
- `A2A_STREAMING`：是否启用 SSE streaming（`/v1/message:stream`），默认 `true`
- `A2A_LOG_LEVEL`：A2A 服务日志级别（`DEBUG/INFO/WARNING/ERROR`），默认 `INFO`
- `A2A_LOG_PAYLOADS`：是否记录 A2A 与 OpenCode 的请求/响应正文，默认 `false`
- `A2A_LOG_BODY_LIMIT`：日志正文最大长度，默认 `0`（不截断）
- `A2A_OAUTH_AUTHORIZATION_URL`：OAuth2 授权地址（预留配置）
- `A2A_OAUTH_TOKEN_URL`：OAuth2 token 地址（预留配置）
- `A2A_OAUTH_METADATA_URL`：OAuth2 元数据地址（可选，预留配置）
- `A2A_OAUTH_SCOPES`：OAuth2 scopes，逗号分隔（预留配置）

## 服务行为说明

- 该服务将 A2A 的 `message:send` 请求转发为 OpenCode 的 session/message 调用。
- 任务状态默认返回 `input-required`，便于继续多轮对话。
- Streaming（`/v1/message:stream`）会输出 `TaskArtifactUpdateEvent` 增量（`append=true`），结束时发送 `TaskStatusUpdateEvent(final=true)`；完整内容由 artifact 承载，非 streaming 调用仍返回 `Task`。
- 需在请求中携带 `Authorization: Bearer <token>`，否则返回 401（Agent Card 不受鉴权限制）。
- OAuth2 相关配置目前仅用于 Agent Card 声明，鉴权校验需后续接入。

## OpenCode 会话查询（REST 代理）

除 A2A 协议接口外，本服务还提供只读 REST 路由，用于代理 OpenCode serve 的会话列表与历史消息查询。

- 鉴权：复用同一个 `Authorization: Bearer <token>`。
- 安全：即使开启 `A2A_LOG_PAYLOADS=true`，这些路由也不会将响应 body 写入日志（避免泄露聊天历史）。

### 会话列表

```bash
curl -sS http://127.0.0.1:8000/v1/opencode/sessions \
  -H 'Authorization: Bearer <your-token>'
```

### 会话消息历史

```bash
curl -sS http://127.0.0.1:8000/v1/opencode/sessions/<session_id>/messages \
  -H 'Authorization: Bearer <your-token>'
```

## 鉴权示例（curl）

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "message_id": "msg-1",
      "role": "user",
      "parts": [{"kind": "text", "text": "你好，介绍下这个仓库"}]
    }
  }'
```

## Streaming 断线续订（resubscribe）

当 SSE 连接中断时，可通过 `POST /v1/tasks/{task_id}:resubscribe` 重新订阅事件流（需保持 task 未进入终态）。

## A2A 客户端示例（a2a-sdk + AuthInterceptor）

> 说明：a2a-sdk 0.3.17 的 REST transport 未应用 interceptors，本项目提供了修正封装。

```python
import asyncio
import os

import httpx
from a2a.client.auth.credentials import InMemoryContextCredentialStore
from a2a.client.auth.interceptor import AuthInterceptor
from a2a.client.client_factory import ClientConfig
from a2a.client.middleware import ClientCallContext
from a2a.types import Message, Role, TextPart, TransportProtocol

from opencode_a2a.a2a_client import connect_with_patched_rest


async def main() -> None:
    base_url = "http://127.0.0.1:8000"
    token = os.environ["A2A_BEARER_TOKEN"]

    store = InMemoryContextCredentialStore()
    session_id = "auth-demo"
    await store.set_credentials(session_id, "bearerAuth", token)

    context = ClientCallContext(state={"sessionId": session_id})
    interceptors = [AuthInterceptor(store)]
    config = ClientConfig(
        supported_transports=[TransportProtocol.http_json],
        httpx_client=httpx.AsyncClient(),
        streaming=False,
    )

    client = await connect_with_patched_rest(
        base_url, client_config=config, interceptors=interceptors
    )

    message = Message(
        message_id="msg-1",
        role=Role.user,
        parts=[TextPart(text="hello")],
    )

    async for _ in client.send_message(message, context=context):
        break

    await config.httpx_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

## 代码规范

```bash
uv run pre-commit install
```
