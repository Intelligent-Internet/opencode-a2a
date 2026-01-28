# 部署指南（systemd 多实例）

本指南用于在一台服务器上按项目隔离部署 OpenCode + A2A（双进程），并复用共享核心包。

## 前置条件

- 具备 `sudo` 权限（写入 systemd unit、创建用户与目录）。
- OpenCode 核心已安装在共享目录（默认 `/opt/.opencode`，可用 `OPENCODE_CORE_DIR` 覆盖）。
- 本仓库已部署在共享目录（默认 `/opt/opencode-a2a/opencode-a2a-serve`，可用 `OPENCODE_A2A_DIR` 覆盖）。
- A2A 的 venv 已准备好（默认 `${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a`）。
- uv Python 池已准备好（默认 `/opt/uv-python`，可用 `UV_PYTHON_DIR` 覆盖）。
- systemd 可用。

> 目录默认值可通过环境变量覆盖，见下文配置说明。

## 目录结构

每个项目实例在 `DATA_ROOT` 下有独立目录（默认 `/data/projects/<project>`）：

- `workspace/`：OpenCode 仅能写入的工作区
- `config/`：root-only 的配置目录，存放 env 文件
- `logs/`：服务日志目录
- `run/`：运行时文件目录（预留）

目录权限默认：
- `DATA_ROOT`：`711`（仅可遍历，不可读取）
- 项目目录/`workspace`/`logs`/`run`：`700`（仅项目用户可访问）
- `config/`：`700` root-only；env 文件权限 `600`

## 快速部署

```bash
./scripts/deploy.sh <project_name> <github_token> <a2a_bearer_token> [a2a_port] [a2a_host]
```

也可以使用 `key:value`（或 `key=value`）形式，避免依赖参数顺序：

```bash
./scripts/deploy.sh project:alpha github_token:ghp_xxx a2a_bearer_token:a2a_xxx a2a_port:8010 a2a_host:127.0.0.1
```

支持的 key（不区分大小写）：`project`/`project_name`、`github_token`/`gh_token`、`a2a_bearer_token`、`a2a_port`、`a2a_host`。

> `github_token` 强烈建议使用 **repo 专属的 Fine-grained personal access token**，并严格限制权限范围（仅授予该项目仓库所需的最小读写权限）。

示例：

```bash
./scripts/deploy.sh alpha ghp_xxx a2a_xxx 8010
```

脚本会：
1) 安装 systemd 模板单元 `opencode@.service` 与 `opencode-a2a@.service`
2) 创建项目用户与目录
3) 写入实例配置 env 文件
4) 启动两套服务

## 配置说明

### 入口脚本环境变量

可在运行 `deploy.sh` 前设置（未设置时使用默认值）：

- `OPENCODE_A2A_DIR`：A2A 仓库路径，默认 `/opt/opencode-a2a/opencode-a2a-serve`
- `OPENCODE_CORE_DIR`：OpenCode 核心路径，默认 `/opt/.opencode`
- `UV_PYTHON_DIR`：uv Python 池路径，默认 `/opt/uv-python`
- `DATA_ROOT`：项目根目录，默认 `/data/projects`

- `OPENCODE_BIND_HOST`：OpenCode 监听地址，默认 `127.0.0.1`
- `OPENCODE_BIND_PORT`：OpenCode 监听端口，默认 `4096`（多实例时需为每个项目分配不同端口；未显式设置时，脚本会尝试用 `A2A_PORT + 1` 自动分配）
- `OPENCODE_LOG_LEVEL`：OpenCode 日志级别，默认 `INFO`
- `OPENCODE_EXTRA_ARGS`：OpenCode 额外启动参数（空格分隔）

- `A2A_HOST`：A2A 监听地址，默认 `127.0.0.1`（也可通过 `deploy.sh` 的第 5 个参数设置）
- `A2A_PORT`：A2A 监听端口，默认 `8000`（多实例时需为每个项目分配不同端口）
- `A2A_PUBLIC_URL`：对外可访问的 A2A URL，默认 `http://<A2A_HOST>:<A2A_PORT>`
- `A2A_LOG_LEVEL`：A2A 日志级别，默认 `info`

### 实例配置文件

每个项目会生成（路径位于 `/data/projects/<project>/config/`，不同项目不会重名）：

- `config/opencode.env`：仅 OpenCode 读取
- `config/a2a.env`：仅 A2A 读取（包含 `GH_TOKEN` 与 `A2A_BEARER_TOKEN`）

如需更新 token 或端口，修改 env 文件后重启服务：

```bash
sudo systemctl restart opencode@<project>.service
sudo systemctl restart opencode-a2a@<project>.service
```

## 服务管理

```bash
sudo systemctl status opencode@<project>.service
sudo systemctl status opencode-a2a@<project>.service
```

停止服务：

```bash
sudo systemctl stop opencode-a2a@<project>.service
sudo systemctl stop opencode@<project>.service
```

## 安全与隔离说明

systemd 单元已启用：

- `ProtectSystem=strict`
- `ReadWritePaths=/data/projects/%i`
- `PrivateTmp=true`
- `NoNewPrivileges=true`

OpenCode 与 A2A 分离运行，敏感变量仅注入 A2A 服务，避免 OpenCode 继承。

如需更强隔离（例如 `RootDirectory`/`BindPaths` 或 `InaccessiblePaths`），可在 systemd 单元中进一步加固。
