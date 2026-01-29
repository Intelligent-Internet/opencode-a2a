# scripts

该目录包含本地启动与 systemd 部署相关脚本。

## 何时使用哪个脚本

- `init_system.sh`：初始化系统环境（依赖与共享目录）。可重复执行，已满足的步骤会自动跳过。用于首次搭建服务器环境。
- `start_services.sh`：本地/临时运行 OpenCode + A2A。无需 sudo，不依赖 systemd，启动后前台常驻，Ctrl+C 会自动停止服务。适合开发、调试、临时验证。
- `deploy.sh`：在服务器上通过 systemd 部署多实例。适合长期运行与运维管理。

保留 `start_services.sh` 的原因：
- 轻量：不需要 systemd 与 sudo 权限。
- 便捷：自动绑定 Tailscale IP 并写入 `A2A_PUBLIC_URL`，适合内网/外网同网段调用。
- 可观测：每次启动自动创建时间戳日志目录，便于定位单次运行问题。

## start_services.sh（本地一键启动）

前置要求：
- `tailscale` 可用且能获取 `tailscale ip -4`
- `opencode` 可执行（系统 PATH 或 `~/.opencode/bin/opencode`）
- `uv` 可执行

使用：

```bash
./scripts/start_services.sh
```

常用环境变量：
- `A2A_PORT`：A2A 端口（默认值见 `docs/guide.md`）
- `OPENCODE_LOG_LEVEL`：OpenCode 日志级别
- `A2A_LOG_LEVEL`：A2A 日志级别（默认值见 `docs/guide.md`）
- `LOG_ROOT`：日志根目录
- `LOG_DIR`：日志目录（覆盖时间戳目录）

## init_system.sh（系统环境初始化）

用于准备 systemd 部署所需的基础环境与共享目录，详见 `docs/deployment.md` 的“系统环境初始化”部分。

## deploy.sh（systemd 多实例部署）

详见 `docs/deployment.md`。

`deploy/` 子目录包含 systemd 单元与实例化部署脚本，由 `deploy.sh` 串联调用。
