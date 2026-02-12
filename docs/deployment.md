# Deployment Guide (systemd Multi-Instance)

This guide explains how to deploy OpenCode + A2A as isolated per-project instances (two processes per project) on one host while sharing core runtime artifacts.

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
- clones this repository to shared path (HTTPS URL by default)
- creates A2A virtualenv via `uv sync --all-extras`

Notes:

- `init_system.sh` has no runtime arguments; edit top constants to change
  defaults.

## Directory Layout

Each project instance gets an isolated directory under `DATA_ROOT` (default `/data/opencode-a2a/<project>`):

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
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

HTTPS public URL example:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1 a2a_public_url=https://a2a.example.com
```

Supported CLI keys (case-insensitive): `project`/`project_name`, `data_root`, `a2a_port`, `a2a_host`, `a2a_public_url`, `a2a_streaming`, `a2a_log_level`, `a2a_log_payloads`, `a2a_log_body_limit`, `opencode_provider_id`, `opencode_model_id`, `repo_url`, `repo_branch`, `opencode_timeout`, `opencode_timeout_stream`, `git_identity_name`, `git_identity_email`, `update_a2a`, `force_restart`.

Required secret env vars: `GH_TOKEN`, `A2A_BEARER_TOKEN`

Recommended style: keep only secret values in process env; pass non-secret overrides as CLI keys:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh \
  project=alpha \
  data_root=/data/opencode-a2a \
  a2a_log_payloads=true \
  a2a_log_body_limit=2000
```

Optional provider secret env vars: `GOOGLE_GENERATIVE_AI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `OPENROUTER_API_KEY`

> Use a repository-scoped fine-grained personal access token with minimal
> required permissions.

Minimal example:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha a2a_port=8010
```

Upgrade an existing instance after shared-code update:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha update_a2a=true force_restart=true
```

### Provider Configuration Examples

Gemini (Google):

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' GOOGLE_GENERATIVE_AI_API_KEY='<google-key>' \
./scripts/deploy.sh project=alpha opencode_provider_id=google opencode_model_id=gemini-3-flash-preview
```

OpenAI:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' OPENAI_API_KEY='<openai-key>' \
./scripts/deploy.sh project=alpha opencode_provider_id=openai opencode_model_id='<openai-model-id>'
```

Anthropic:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' ANTHROPIC_API_KEY='<anthropic-key>' \
./scripts/deploy.sh project=alpha opencode_provider_id=anthropic opencode_model_id='<anthropic-model-id>'
```

Azure OpenAI:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' AZURE_OPENAI_API_KEY='<azure-openai-key>' \
./scripts/deploy.sh project=alpha opencode_provider_id=azure opencode_model_id='<azure-deployment-or-model-id>'
```

OpenRouter:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' OPENROUTER_API_KEY='<openrouter-key>' \
./scripts/deploy.sh project=alpha opencode_provider_id=openrouter opencode_model_id='<openrouter-model-id>'
```

Notes:

- Use model IDs that your OpenCode installation/provider mapping supports.
- This deploy layer mainly passes through provider identity/model (`OPENCODE_PROVIDER_ID`/`OPENCODE_MODEL_ID`) and selected provider keys.
- Provider-specific connection settings beyond API key (for example endpoint/base URL, api-version, deployment name) must follow OpenCode's own provider configuration rules.

### Current Provider Coverage and Gaps

This section describes what this repository's deploy scripts currently cover.
It is not a full OpenCode provider capability matrix.

| Provider | Secret key persisted by deploy scripts | Example in this doc | Startup key enforcement in `run_opencode.sh` |
| --- | --- | --- | --- |
| Google / Gemini | `GOOGLE_GENERATIVE_AI_API_KEY` | Yes | Yes (explicitly required for `provider=google` or `model=*gemini*`) |
| OpenAI | `OPENAI_API_KEY` | Yes | No explicit provider-specific check |
| Anthropic | `ANTHROPIC_API_KEY` | Yes | No explicit provider-specific check |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` | Yes | No explicit provider-specific check |
| OpenRouter | `OPENROUTER_API_KEY` | Yes | No explicit provider-specific check |

Known gaps:

- Missing provider-specific validation matrix in scripts (required env vars are only enforced for Google/Gemini).
- Missing compatibility verification checklist per provider/model family.
- Missing explicit documentation that deploy scripts do not replace OpenCode `/connect`-level provider setup.

Script actions:

1. install systemd template units `opencode@.service` and
   `opencode-a2a@.service`
2. create project user and directories
3. write instance config env files
4. start both services (or restart if `force_restart=true`)

## Configuration Details

### `deploy.sh` Environment Variables

For values that support both environment variables and CLI keys, precedence is:
`CLI key=value` > environment variable > built-in default.

#### Secret Variables

| Variable | Required | Default | CLI Support | Notes |
| --- | --- | --- | --- | --- |
| `GH_TOKEN` | Yes | None | No | Used by OpenCode and `gh auth login`. |
| `A2A_BEARER_TOKEN` | Yes | None | No | Written to `a2a.env`. |
| `GOOGLE_GENERATIVE_AI_API_KEY` | Optional | None | No | Persisted into `opencode.secret.env` if provided. |
| `OPENAI_API_KEY` | Optional | None | No | Persisted into `opencode.secret.env` if provided. |
| `ANTHROPIC_API_KEY` | Optional | None | No | Persisted into `opencode.secret.env` if provided. |
| `AZURE_OPENAI_API_KEY` | Optional | None | No | Persisted into `opencode.secret.env` if provided. |
| `OPENROUTER_API_KEY` | Optional | None | No | Persisted into `opencode.secret.env` if provided. |

#### Non-Secret Variables

| Variable | Required | Default | CLI Key | Notes |
| --- | --- | --- | --- | --- |
| `OPENCODE_A2A_DIR` | Optional | `/opt/opencode-a2a/opencode-a2a-serve` | - | Repo path for opencode-a2a-serve. |
| `OPENCODE_CORE_DIR` | Optional | `/opt/.opencode` | - | OpenCode core path. |
| `UV_PYTHON_DIR` | Optional | `/opt/uv-python` | - | uv Python cache path. |
| `DATA_ROOT` | Optional | `/data/opencode-a2a` | `data_root` | Instance root directory. |
| `OPENCODE_BIND_HOST` | Optional | `127.0.0.1` | - | OpenCode bind host. |
| `OPENCODE_BIND_PORT` | Optional | `A2A_PORT + 1` fallback to `4096` | - | Multi-instance should use unique port. |
| `OPENCODE_LOG_LEVEL` | Optional | `DEBUG` | - | OpenCode log level. |
| `OPENCODE_EXTRA_ARGS` | Optional | empty | - | Extra OpenCode startup args. |
| `OPENCODE_PROVIDER_ID` | Optional | None | `opencode_provider_id` | Written to `a2a.env`. |
| `OPENCODE_MODEL_ID` | Optional | None | `opencode_model_id` | Written to `a2a.env`. |
| `OPENCODE_TIMEOUT` | Optional | `300` | `opencode_timeout` | OpenCode request timeout (seconds). |
| `OPENCODE_TIMEOUT_STREAM` | Optional | None | `opencode_timeout_stream` | OpenCode streaming timeout (seconds). |
| `GIT_IDENTITY_NAME` | Optional | `OpenCode-<project>` | `git_identity_name` | Git author/committer name. |
| `GIT_IDENTITY_EMAIL` | Optional | `<project>@example.com` | `git_identity_email` | Git author/committer email. |
| `A2A_HOST` | Optional | `127.0.0.1` | `a2a_host` | A2A bind host. |
| `A2A_PORT` | Optional | `8000` | `a2a_port` | A2A bind port. |
| `A2A_PUBLIC_URL` | Optional | `http://<A2A_HOST>:<A2A_PORT>` | `a2a_public_url` | Public Agent Card URL. |
| `A2A_STREAMING` | Optional | `true` | `a2a_streaming` | SSE streaming switch. |
| `A2A_LOG_LEVEL` | Optional | `DEBUG` | `a2a_log_level` | A2A log level. |
| `A2A_LOG_PAYLOADS` | Optional | `true` | `a2a_log_payloads` | Payload logging switch. |
| `A2A_LOG_BODY_LIMIT` | Optional | `0` | `a2a_log_body_limit` | Payload body max length. |
| `A2A_PROJECT` | Auto-generated | `<project>` | (derived from `project`) | Automatically written into `a2a.env`. |

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
  (`GOOGLE_GENERATIVE_AI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `OPENROUTER_API_KEY`)
- `a2a.env`: A2A-only settings (`A2A_BEARER_TOKEN`, model options, etc.)

If provider keys are supplied during deploy, they are persisted into `opencode.secret.env` (`600`, `root:root`) and loaded by `opencode@.service` via `EnvironmentFile`.

### Token and Key Risk

Because provider keys are injected into the running `opencode` process, `opencode agent` behavior may indirectly exfiltrate sensitive values.

This architecture does not provide hard guarantees that provider keys are inaccessible to agents. Treat it as a trusted-environment setup unless stronger credential-isolation controls are added.

### Recommended Secret Input Pattern

Use single-command environment variable injection to avoid long-lived shell exports:

> Note: if you type secrets directly in a shell command, they may still be recorded by shell history depending on your shell settings and operational practices.

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' GOOGLE_GENERATIVE_AI_API_KEY='<google-key>' \
./scripts/deploy.sh \
  project=alpha \
  a2a_port=8010 \
  a2a_host=127.0.0.1 \
  opencode_provider_id=google \
  opencode_model_id=gemini-3-flash-preview \
  repo_url=https://github.com/org/repo.git \
  repo_branch=main
```

Rotate Gemini key:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' GOOGLE_GENERATIVE_AI_API_KEY='<google-key-new>' \
./scripts/deploy.sh project=alpha force_restart=true
```

If `repo_url` is provided, first deploy can auto-clone into `workspace/` (optional `repo_branch`). Clone is skipped if `workspace/.git` already exists or workspace is non-empty.

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

By default it prints preview commands only. Apply requires explicit confirmation:

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
  `GET /v1/tasks/{task_id}:subscribe`
- service subscribes to OpenCode `/event` stream and forwards filtered
  per-session updates
- stream emits incremental `TaskArtifactUpdateEvent` (`append=true`) and closes
  with `TaskStatusUpdateEvent(final=true)`
