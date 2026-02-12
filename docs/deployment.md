# Deployment Guide (systemd Multi-Instance)

This guide explains how to deploy OpenCode + A2A as isolated per-project
instances (two processes per project) on one host while sharing core runtime
artifacts.

## Prerequisites

- `sudo` access (required for systemd units, users, and directories).
- OpenCode core installed in shared directory
  (default `/opt/.opencode`; editable in `scripts/init_system.sh`).
- This repository available on host in shared directory
  (default `/opt/opencode-a2a/opencode-a2a-serve`; editable in
  `scripts/init_system.sh`).
- A2A virtualenv prepared
  (default `${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a-serve`).
- `uv` Python pool prepared (default `/opt/uv-python`).
- systemd available.

> Shared path defaults come from top-level constants in
> `scripts/init_system.sh`. `deploy.sh` still supports environment-variable
> overrides; keep them consistent with actual paths.

## Optional System Bootstrap

To prepare host prerequisites in one step:

```bash
./scripts/init_system.sh
```

Script characteristics:

- idempotent: completed steps are skipped
- decoupled from `deploy.sh`: only prepares host/shared environment

Default behavior:

- installs base tools (`htop`, `vim`, `curl`, `wget`, `git`, `net-tools`,
  `lsblk`, `ca-certificates`) and `gh`
- installs Node.js >= 20 (`npm`/`npx`) via NodeSource or distro package
- installs `uv` (if missing), pre-downloads Python `3.10/3.11/3.12/3.13`
- creates shared directories (`/opt/.opencode`, `/opt/opencode-a2a`,
  `/opt/uv-python`, `/data/opencode-a2a`)
- sets `/opt/uv-python` permission from `777` to recursive `755`
- fails if `systemctl` is unavailable
- clones this repository to shared path (SSH URL by default)
- creates A2A virtualenv via `uv sync --all-extras`

Notes:

- If host has no SSH key configured, either set one up or edit
  `OPENCODE_A2A_REPO` to use HTTPS clone URL.
- `init_system.sh` has no runtime arguments; edit top constants to change
  defaults.

## Directory Layout

Each project instance gets an isolated directory under `DATA_ROOT`
(default `/data/opencode-a2a/<project>`):

- `workspace/`: writable OpenCode workspace
- `config/`: root-only config directory for env files
- `logs/`: service logs
- `run/`: runtime files (reserved)

Default permissions:

- `DATA_ROOT`: `711` (traversable, not listable)
- project root + `workspace` + `logs` + `run`: `700`
- `config/`: `700` (root-only), env files `600`

## Quick Deploy

```bash
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx a2a_port=8010 a2a_host=127.0.0.1 opencode_provider_id=google opencode_model_id=gemini-3-flash-preview
```

HTTPS public URL example:

```bash
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx a2a_port=8010 a2a_host=127.0.0.1 a2a_public_url=https://a2a.example.com
```

Supported keys (case-insensitive):
`project`/`project_name`, `github_token`/`gh_token`, `a2a_bearer_token`,
`a2a_port`, `a2a_host`, `a2a_public_url`, `opencode_provider_id`,
`opencode_model_id`, `repo_url`, `repo_branch`, `opencode_timeout`,
`opencode_timeout_stream`, `git_identity_name`, `git_identity_email`,
`google_generative_ai_api_key` (alias `google_api_key`), `update_a2a`,
`force_restart`.

> Use a repository-scoped fine-grained personal access token with minimal
> required permissions.

Minimal example:

```bash
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx a2a_port=8010
```

Upgrade an existing instance after shared-code update:

```bash
./scripts/deploy.sh project=alpha github_token=ghp_xxx a2a_bearer_token=a2a_xxx update_a2a=true force_restart=true
```

Script actions:

1. install systemd template units `opencode@.service` and
   `opencode-a2a@.service`
2. create project user and directories
3. write instance config env files
4. start both services (or restart if `force_restart=true`)

## Configuration Details

### `deploy.sh` Environment Variables

These can be set before running `deploy.sh` (defaults used if unset):

- `OPENCODE_BIND_HOST`: OpenCode bind host, default `127.0.0.1`
- `OPENCODE_BIND_PORT`: OpenCode bind port, default `4096`
  (for multi-instance, each project should use a unique port; if unset,
  script attempts `A2A_PORT + 1`)
- `OPENCODE_LOG_LEVEL`: OpenCode log level, default `DEBUG`
- `OPENCODE_EXTRA_ARGS`: extra OpenCode startup arguments (space-separated)
- `OPENCODE_PROVIDER_ID`: default OpenCode provider (written to `a2a.env`)
- `OPENCODE_MODEL_ID`: default OpenCode model (written to `a2a.env`)
- `OPENCODE_TIMEOUT`: request timeout in seconds, default `300`
- `OPENCODE_TIMEOUT_STREAM`: streaming timeout in seconds (optional)
- `GIT_IDENTITY_NAME`: optional git author/committer name override
  (default `OpenCode-<project>`)
- `GIT_IDENTITY_EMAIL`: optional git author/committer email override
  (default `<project>@internal`)

- `A2A_HOST`: A2A bind host, default `127.0.0.1`
- `A2A_PORT`: A2A bind port, default `8000`
- `A2A_LOG_LEVEL`: A2A log level, default `DEBUG`
- `A2A_LOG_PAYLOADS`: payload logging switch, default `true`
- `A2A_LOG_BODY_LIMIT`: payload body max length, default `0` (unbounded)
- `A2A_PUBLIC_URL`: set by `a2a_public_url=...`; otherwise auto-generated as
  `http://<A2A_HOST>:<A2A_PORT>`
- `A2A_STREAMING`: SSE streaming switch, default `true`

> Shared paths (`OPENCODE_A2A_DIR`, `OPENCODE_CORE_DIR`, `UV_PYTHON_DIR`,
> `DATA_ROOT`) default to `init_system.sh` constants; environment overrides are
> still supported.

> `DATA_ROOT` must be traversable by project users (at least `o+x`). Otherwise
> OpenCode cannot write `$HOME/.cache` / `$HOME/.local` and `/session` may fail
> with `EACCES`.

### Instance Config Files

For each project (`/data/opencode-a2a/<project>/config/`):

- `opencode.env`: OpenCode-only settings (`GH_TOKEN`, git identity, etc.)
- `opencode.secret.env`: optional sensitive OpenCode settings
  (`GOOGLE_GENERATIVE_AI_API_KEY`)
- `a2a.env`: A2A-only settings (`A2A_BEARER_TOKEN`, model options, etc.)

If provided during deploy, `GOOGLE_GENERATIVE_AI_API_KEY` is persisted into
`opencode.secret.env` (`600`, `root:root`) and loaded by
`opencode@.service` via `EnvironmentFile`.

### Token and Key Risk

Because provider keys are injected into the running `opencode` process,
`opencode agent` behavior may indirectly exfiltrate sensitive values.

This architecture does not provide hard guarantees that provider keys are
inaccessible to agents. Treat it as a trusted-environment setup unless stronger
credential-isolation controls are added.

### Recommended Secret Input Pattern

Prefer environment variables over command-line literals to reduce shell-history
exposure:

```bash
export GOOGLE_GENERATIVE_AI_API_KEY='AIza...'
export GH_TOKEN='ghp_...'
export A2A_BEARER_TOKEN='a2a_...'

./scripts/deploy.sh \
  project=alpha \
  github_token="$GH_TOKEN" \
  a2a_bearer_token="$A2A_BEARER_TOKEN" \
  a2a_port=8010 \
  a2a_host=127.0.0.1 \
  opencode_provider_id=google \
  opencode_model_id=gemini-3-flash-preview \
  repo_url=https://github.com/org/repo.git \
  repo_branch=main
```

Rotate Gemini key:

```bash
export GOOGLE_GENERATIVE_AI_API_KEY='AIza_new...'
./scripts/deploy.sh project=alpha github_token="$GH_TOKEN" a2a_bearer_token="$A2A_BEARER_TOKEN" force_restart=true
```

If `repo_url` is provided, first deploy can auto-clone into `workspace/`
(optional `repo_branch`). Clone is skipped if `workspace/.git` already exists or
workspace is non-empty.

If you manually update env files, restart services:

```bash
sudo systemctl restart opencode@<project>.service
sudo systemctl restart opencode-a2a@<project>.service
```

### Gemini Key Acceptance Checklist

- first deploy: `config/opencode.secret.env` exists with `600` and `root:root`
- service restart: Gemini requests still succeed
- host reboot: service auto-recovers and Gemini requests still succeed
- key rotation: new key takes effect after re-running deploy

## Service Management

```bash
sudo systemctl status opencode@<project>.service
sudo systemctl status opencode-a2a@<project>.service
```

## Uninstall One Instance

To remove a single project instance (services, project dirs, user/group):

```bash
./scripts/uninstall.sh project=<project>
```

By default it prints preview commands only.
Apply requires explicit confirmation:

```bash
./scripts/uninstall.sh project=<project> confirm=UNINSTALL
```

Notes:

- `uninstall.sh` never removes shared systemd templates
  (`/etc/systemd/system/opencode@.service`,
  `/etc/systemd/system/opencode-a2a@.service`).
- It only cleans per-project instance units and resources.
- In apply mode, script validates project name, checks marker env files under
  `${DATA_ROOT}/<project>/config/`, canonicalizes `DATA_ROOT`, and rejects
  unsafe paths containing `.` / `..` segments.
- Script uses `sudo` and expects non-interactive `sudo -n` availability in
  automation contexts.

## Logs

Recent logs:

```bash
sudo journalctl -u opencode@<project>.service -n 200 --no-pager
sudo journalctl -u opencode-a2a@<project>.service -n 200 --no-pager
```

Follow logs:

```bash
sudo journalctl -u opencode@<project>.service -f
sudo journalctl -u opencode-a2a@<project>.service -f
```

Errors only:

```bash
sudo journalctl -u opencode@<project>.service -p err --no-pager
```

Filter by time:

```bash
sudo journalctl -u opencode@<project>.service --since "2026-01-28 14:40" --no-pager
```

Stop services:

```bash
sudo systemctl stop opencode-a2a@<project>.service
sudo systemctl stop opencode@<project>.service
```

## Security and Isolation

Enabled in systemd units:

- `ProtectSystem=strict`: root filesystem read-only
- `ReadWritePaths=${DATA_ROOT}/%i`: write access scoped to current instance
- `PrivateTmp=true`: private `/tmp`
- `NoNewPrivileges=true`: no privilege escalation for process tree

Application-level safeguards:

- directory boundary validation with `realpath`
- session ownership checks by identity
- credential separation:
  - `A2A_BEARER_TOKEN` only in A2A process
  - `GH_TOKEN` and git credentials only in OpenCode process

## Streaming Notes

- A2A supports `POST /v1/message:stream` (SSE) when `A2A_STREAMING=true`
- disconnected clients can re-subscribe via
  `POST /v1/tasks/{task_id}:resubscribe`
- service subscribes to OpenCode `/event` stream and forwards filtered
  per-session updates
- stream emits incremental `TaskArtifactUpdateEvent` (`append=true`) and closes
  with `TaskStatusUpdateEvent(final=true)`
