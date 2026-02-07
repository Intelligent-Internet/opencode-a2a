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
- `A2A_JWT_SECRET`：JWT 验签 key（建议使用非对称算法的 **public key PEM**）
- `A2A_JWT_ALGORITHM`：JWT 签名算法（**仅支持非对称算法**），默认 `RS256`
- `A2A_JWT_ISSUER`：JWT 签发者校验（必填）
- `A2A_JWT_AUDIENCE`：JWT 受众校验（必填）
- `A2A_JWT_SCOPE_MATCH`：当设置了 `A2A_OAUTH_SCOPES` 时的 scopes 匹配规则，可选 `any`/`all`，默认 `any`
- `A2A_STREAMING`：是否启用 SSE streaming（`/v1/message:stream`），默认 `true`
- `A2A_LOG_LEVEL`：A2A 服务日志级别（`DEBUG/INFO/WARNING/ERROR`），默认 `INFO`
- `A2A_LOG_PAYLOADS`：是否记录 A2A 与 OpenCode 的请求/响应正文，默认 `false`
- `A2A_LOG_BODY_LIMIT`：日志正文最大长度，默认 `0`（不截断）
- `A2A_OAUTH_AUTHORIZATION_URL`：OAuth2 授权地址（预留配置）
- `A2A_OAUTH_TOKEN_URL`：OAuth2 token 地址（预留配置）
- `A2A_OAUTH_METADATA_URL`：OAuth2 元数据地址（可选，预留配置）
- `A2A_OAUTH_SCOPES`：OAuth2 scopes，逗号分隔（可选；用于 Agent Card 声明，并可用于 JWT 的 scope/scp 校验）

## 服务行为说明

- 该服务将 A2A 的 `message:send` 请求转发为 OpenCode 的 session/message 调用。
- 任务状态默认返回 `input-required`，便于继续多轮对话。
- Streaming（`/v1/message:stream`）会输出 `TaskArtifactUpdateEvent` 增量（`append=true`），结束时发送 `TaskStatusUpdateEvent(final=true)`；完整内容由 artifact 承载，非 streaming 调用仍返回 `Task`。
- 需在请求中携带 `Authorization: Bearer <token>`，否则返回 401（Agent Card 不受鉴权限制）。
- 支持 JWT 无状态鉴权：会校验签名算法（仅非对称）、签发者（issuer，必填）、受众（audience，必填）、过期时间（exp，必填），并可选校验 Scopes（scope/scp，字符串或数组）。
- OAuth2 URLs（Authorization/Token URL）目前主要用于 Agent Card 声明。

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

## 鉴权示例（JWT 模式）

Token 需为合法的 JWT 且必须包含 `exp`。服务端必须配置 `A2A_JWT_ISSUER` 与 `A2A_JWT_AUDIENCE`。若设置了 `A2A_OAUTH_SCOPES`，Token 的 `scope` 或 `scp` 声明需满足 `A2A_JWT_SCOPE_MATCH` 规则（支持字符串或数组）。

```bash
curl -sS http://127.0.0.1:8000/v1/message:send \
  -H 'Authorization: Bearer <your-jwt-token>' \
  ...
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
    token = os.environ["A2A_JWT_TOKEN"]

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
