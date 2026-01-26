# opencode-a2a

这是一个将 OpenCode 服务封装为 A2A HTTP+JSON 服务的适配层。

## 运行方式

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
uv run opencode-a2a
```

默认监听：`http://127.0.0.1:8000`

A2A Agent Card：`http://127.0.0.1:8000/.well-known/agent-card.json`

### 使用脚本（自动启动 OpenCode + 绑定 Tailscale IP）

```bash
./scripts/start_services.sh
```

脚本会常驻运行，按 Ctrl+C 退出时会自动停止启动的服务。
每次启动会创建带时间戳的日志目录（默认在 `./logs/<timestamp>`），分别记录 OpenCode 与 A2A 日志。

## 环境变量

- `OPENCODE_BASE_URL`：OpenCode 地址，默认 `http://127.0.0.1:4096`
- `OPENCODE_DIRECTORY`：OpenCode directory 参数（可选）
- `OPENCODE_PROVIDER_ID`：模型 providerID（可选）
- `OPENCODE_MODEL_ID`：模型 modelID（可选）
- `OPENCODE_AGENT`：OpenCode agent 名称（可选）
- `OPENCODE_SYSTEM`：system prompt（可选）
- `OPENCODE_VARIANT`：variant（可选）
- `OPENCODE_TIMEOUT`：请求超时秒数，默认 `120`

- `A2A_PUBLIC_URL`：对外可访问的 A2A 地址前缀，默认 `http://127.0.0.1:8000`
- `A2A_TITLE`：Agent 名称，默认 `OpenCode A2A`
- `A2A_DESCRIPTION`：Agent 描述
- `A2A_VERSION`：Agent 版本号
- `A2A_PROTOCOL_VERSION`：A2A 协议版本，默认 `0.3.0`
- `A2A_HOST`：监听地址，默认 `127.0.0.1`
- `A2A_PORT`：监听端口，默认 `8000`

## 说明

- 该服务将 A2A 的 `message:send` 请求转发为 OpenCode 的 session/message 调用。
- 任务状态默认返回 `input-required`，便于继续多轮对话。

## 代码规范

```bash
uv run pre-commit install
```
