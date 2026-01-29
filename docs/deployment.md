# 部署指南（systemd 多实例）

本指南用于在一台服务器上按项目隔离部署 OpenCode + A2A（双进程），并复用共享核心包。

## 前置条件

- 具备 `sudo` 权限（写入 systemd unit、创建用户与目录）。
- OpenCode 核心已安装在共享目录（默认 `/opt/.opencode`，如需改路径请修改 `scripts/init_system.sh` 顶部变量）。
- 本仓库已部署在共享目录（默认 `/opt/opencode-a2a/opencode-a2a-serve`，如需改路径请修改 `scripts/init_system.sh` 顶部变量）。
- A2A 的 venv 已准备好（默认 `${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a`）。
- uv Python 池已准备好（默认 `/opt/uv-python`，如需改路径请修改 `scripts/init_system.sh` 顶部变量）。
- systemd 可用。

> 共享路径默认值在 `scripts/init_system.sh` 顶部变量；`deploy.sh` 仍支持通过环境变量覆盖（需确保与实际目录一致）。

## 系统环境初始化（可选）

如需一键准备上述基础环境，可先运行：

```bash
./scripts/init_system.sh
```

脚本特点：
- 可重复执行，已满足的步骤会自动跳过。
- 与 `deploy.sh` 解耦，仅负责系统与共享环境准备。

默认行为：
- 安装基础工具（`htop`、`vim`、`curl`、`wget`、`git`、`net-tools`、`lsblk`、`ca-certificates`）与 `gh`（添加官方源）。
- 安装 Node.js ≥ 20（含 `npm`/`npx`，下载 NodeSource 安装脚本、校验后执行，或使用系统包）。
- 安装 `uv`（若未安装，下载脚本校验后执行），并预下载 Python 版本 `3.10/3.11/3.12/3.13`（若缺失才安装）。
- 创建共享目录（`/opt/.opencode`、`/opt/opencode-a2a`、`/opt/uv-python`、`/data/projects`），并为 `/opt/uv-python` 设置权限（默认先 `777`，预下载完成后递归调整为 `755`；可在 `scripts/init_system.sh` 顶部变量中调整）。
- 若系统缺少 systemd（`systemctl` 不存在），脚本将直接失败退出。
- 克隆 `opencode-a2a-serve` 仓库到共享目录（若不存在，默认使用 SSH 地址）。
- 创建 A2A venv（`uv sync --all-extras`）。

常用参数/环境变量：

> 若服务器未配置 SSH key，请先配置 SSH key，或在脚本顶部修改 `OPENCODE_A2A_REPO` 使用 HTTPS 克隆；否则脚本会提示手动 clone。

> 脚本无运行参数，默认行为（含 Node 版本/安装开关）请直接修改 `scripts/init_system.sh` 顶部的变量。

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
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx a2a_port=8010 a2a_host=127.0.0.1 opencode_provider_id=google opencode_model_id=gemini-3-flash-preview
```

支持的 key（不区分大小写）：`project`/`project_name`、`github_token`/`gh_token`、`a2a_bearer_token`、`a2a_port`、`a2a_host`、`opencode_provider_id`、`opencode_model_id`、`repo_url`、`repo_branch`、`opencode_timeout`、`opencode_timeout_stream`、`google_generative_ai_api_key`（可用 `google_api_key` 作为别名）、`update_a2a`、`force_restart`。

> `github_token` **必须使用项目专属的 Fine-grained personal access token**，并严格限制权限范围（**不得跨仓授权**，仅授予该项目仓库所需的最小读写权限）。

示例：

```bash
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx a2a_port=8010
```

已部署实例升级（更新共享代码后）：

```bash
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx update_a2a=true force_restart=true
```

脚本会：
1) 安装 systemd 模板单元 `opencode@.service` 与 `opencode-a2a@.service`
2) 创建项目用户与目录
3) 写入实例配置 env 文件
4) 启动两套服务（如 `force_restart=true`，则会重启已运行的服务）

## 配置说明

### 入口脚本环境变量

可在运行 `deploy.sh` 前设置（未设置时使用默认值）：

- `OPENCODE_BIND_HOST`：OpenCode 监听地址，默认 `127.0.0.1`（映射到 `opencode serve --hostname`）
- `OPENCODE_BIND_PORT`：OpenCode 监听端口，默认 `4096`（多实例时需为每个项目分配不同端口；未显式设置时，脚本会尝试用 `A2A_PORT + 1` 自动分配）
- `OPENCODE_LOG_LEVEL`：OpenCode 日志级别，默认 `DEBUG`（脚本内默认）
- `OPENCODE_EXTRA_ARGS`：OpenCode 额外启动参数（空格分隔）
- `OPENCODE_PROVIDER_ID`：OpenCode 默认 provider（写入 `a2a.env`）
- `OPENCODE_MODEL_ID`：OpenCode 默认 model（写入 `a2a.env`）
- `OPENCODE_TIMEOUT`：请求超时秒数，默认 `300`
- `OPENCODE_TIMEOUT_STREAM`：streaming 请求超时秒数（可选；不设置则不限制）

- `A2A_HOST`：A2A 监听地址，默认 `127.0.0.1`（也可通过 `deploy.sh` 的 `a2a_host=...` 参数设置）
- `A2A_PORT`：A2A 监听端口，默认 `8000`（多实例时需为每个项目分配不同端口）
- `A2A_PUBLIC_URL`：对外可访问的 A2A URL，默认 `http://<A2A_HOST>:<A2A_PORT>`
- `A2A_LOG_LEVEL`：A2A 日志级别，默认 `DEBUG`（脚本内默认）
- `A2A_LOG_PAYLOADS`：是否记录 A2A 与 OpenCode 请求/响应正文，默认 `true`（脚本内默认）
- `A2A_LOG_BODY_LIMIT`：日志正文最大长度，默认 `0`（不截断）

> 共享路径（`OPENCODE_A2A_DIR`/`OPENCODE_CORE_DIR`/`UV_PYTHON_DIR`/`DATA_ROOT`）默认从 `scripts/init_system.sh` 顶部变量读取；`deploy.sh` 仍支持环境变量覆盖（需确保与实际目录一致）。
- `A2A_STREAMING`：是否启用 SSE streaming（`/v1/message:stream`），默认 `true`

### 实例配置文件

每个项目会生成（路径位于 `/data/projects/<project>/config/`，不同项目不会重名）：

- `config/opencode.env`：仅 OpenCode 读取（包含 `GH_TOKEN` 与 Git 身份配置）
- `config/a2a.env`：仅 A2A 读取（包含 `A2A_BEARER_TOKEN`，以及 `OPENCODE_PROVIDER_ID/OPENCODE_MODEL_ID` 等模型配置）

`GOOGLE_GENERATIVE_AI_API_KEY` 不会写入任何配置文件。可在部署时通过环境变量或 `google_generative_ai_api_key` 参数注入，并以 systemd runtime 方式仅作用于 `opencode@` 进程。系统重启后需重新注入。

为保障私有仓库访问，`github_token` 会写入 `config/opencode.env`，并结合 `GIT_ASKPASS` 注入到 OpenCode 进程中使用。该文件权限为 600（root-only）。

部署脚本会为项目用户执行 `gh auth login --with-token`，写入 `/data/projects/<project>/.config/gh/hosts.yml`（权限 600，项目用户私有），确保 OpenCode 调用 `gh` 时可用。

如需使用 `gh` CLI，服务默认将 `PATH` 包含 `/usr/bin`，并显式允许读取 `/usr/bin/gh`。若 `gh` 安装在其他路径，可通过软链接放入 `${OPENCODE_CORE_DIR}/bin`。

若 systemd 不支持 `set-property Environment`，脚本会退化为临时设置 systemd manager 环境变量并立即启动服务，随后清理该环境变量，避免长期暴露。

示例（推荐用环境变量避免写入 shell 历史）：

```bash
GOOGLE_GENERATIVE_AI_API_KEY=AIzxxx ./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx a2a_port=8010 a2a_host=127.0.0.1 opencode_provider_id=google opencode_model_id=gemini-3-flash-preview repo_url=https://github.com/org/repo.git repo_branch=main
```

如需自动初始化仓库，可传 `repo_url`（可选 `repo_branch`），脚本会在首次部署时将仓库克隆到 `workspace/`；如果 `workspace/.git` 已存在或目录非空则跳过。

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

## 日志查看

查看最近日志：

```bash
sudo journalctl -u opencode@<project>.service -n 200 --no-pager
sudo journalctl -u opencode-a2a@<project>.service -n 200 --no-pager
```

实时跟踪：

```bash
sudo journalctl -u opencode@<project>.service -f
sudo journalctl -u opencode-a2a@<project>.service -f
```

只看错误级别：

```bash
sudo journalctl -u opencode@<project>.service -p err --no-pager
```

按时间范围过滤：

```bash
sudo journalctl -u opencode@<project>.service --since "2026-01-28 14:40" --no-pager
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

OpenCode 与 A2A 分离运行：`A2A_BEARER_TOKEN` 仅注入 A2A，`GH_TOKEN`/Git 凭证仅注入 OpenCode，避免跨进程继承。

## Streaming 说明

- A2A 支持 `POST /v1/message:stream`（SSE），需 `A2A_STREAMING=true`。
- 断线可通过 `POST /v1/tasks/{task_id}:resubscribe` 重新订阅（A2A SDK 支持 `client.resubscribe(...)`）。
- A2A 会订阅 OpenCode 的 `/event`（带 `directory` 参数）获取增量事件，并在 A2A 侧按 session 过滤后转发。
- streaming 会输出 `TaskArtifactUpdateEvent` 增量（`append=true`），结束时发送 `TaskStatusUpdateEvent(final=true)`；完整内容由 artifact 负责承载，非 streaming 调用仍返回 `Task`。

如需更强隔离（例如 `RootDirectory`/`BindPaths` 或 `InaccessiblePaths`），可在 systemd 单元中进一步加固。
