# opencode-a2a

这是一个将 OpenCode 服务封装为 A2A HTTP+JSON 服务的适配层。

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
