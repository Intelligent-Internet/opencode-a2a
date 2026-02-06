# opencode-a2a

这是一个将 OpenCode 服务封装为 A2A HTTP+JSON 服务的适配层。

## 安全边界（必须阅读）

- 当前实现与部署方式下，`opencode` 进程需要读取 LLM provider API token（例如 `GOOGLE_GENERATIVE_AI_API_KEY`）。
- 这意味着 `opencode agent` 存在通过套话、拼接等方式泄露敏感环境变量的风险，**不能视为“agent 无法获知 key”**。
- 因此，`opencode-a2a-serve` 当前仅建议用于内部实例：少数可信成员共用 repo 与 LLM key。
- 若要引入到 cgnext 作为通用能力，必须先审视并定义 LLM provider token 的安全方案（如租户隔离、代理托管、审计与轮换策略）。

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
uv run opencode-a2a
```

默认监听：`http://127.0.0.1:8000`

A2A Agent Card：`http://127.0.0.1:8000/.well-known/agent-card.json`

## 文档

- 使用指南（配置/鉴权/Streaming/客户端示例）：`docs/guide.md`
- 部署（systemd 多实例）：`docs/deployment.md`
- 本地/临时脚本：`scripts/README.md`
