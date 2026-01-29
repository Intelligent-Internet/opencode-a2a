# 系统环境初始化脚本（init_system.sh）

本说明适用于 `scripts/init_system.sh`。该脚本用于准备 systemd 部署所需的系统环境与共享目录，支持重复执行，已满足的步骤会自动跳过。

## 用法

直接执行：

```bash
./scripts/init_system.sh
```

脚本**不接受任何参数**。如需修改路径、开关或版本号，请直接编辑 `scripts/init_system.sh` 顶部的变量。

## 主要行为

- 安装基础工具与 `gh`（添加官方源）。
- 安装 Node.js ≥ 20（含 `npm`/`npx`，使用 NodeSource 官方源或系统包）。
- 安装 `uv`，并预下载 Python `3.10/3.11/3.12/3.13`（仅缺失时安装）。
- 创建共享目录并设置权限（`/opt/uv-python` 先 `777`，预下载后递归 `755`）。
- 克隆 `opencode-a2a-serve` 仓库（默认 SSH 地址，缺少 SSH key 时会提示手动 clone）。
- 创建 A2A venv（`uv sync --all-extras`）。
- 若系统缺少 systemd（`systemctl` 不存在），脚本会直接失败退出。
- 若 OpenCode 安装脚本将程序落在 `/root/.opencode`，会自动迁移到 `OPENCODE_CORE_DIR` 并写入 `/usr/local/bin/opencode`。

## 修改默认配置

请编辑 `scripts/init_system.sh` 顶部的常量区（示例）：

- 路径：`OPENCODE_CORE_DIR`、`SHARED_WRAPPER_DIR`、`UV_PYTHON_DIR`、`DATA_ROOT`
- 权限：`UV_PYTHON_DIR_MODE`、`UV_PYTHON_DIR_FINAL_MODE`、`UV_PYTHON_DIR_GROUP`
- 仓库与分支：`OPENCODE_A2A_REPO`、`OPENCODE_A2A_BRANCH`
- 开关：`INSTALL_PACKAGES`、`INSTALL_UV`、`INSTALL_GH`、`INSTALL_NODE`
- 版本：`NODE_MAJOR`、`UV_PYTHON_VERSIONS`
