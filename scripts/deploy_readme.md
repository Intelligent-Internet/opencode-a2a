# Deploy Script Guide (`deploy.sh`)

This document explains `scripts/deploy.sh` and helper scripts under `scripts/deploy/`.

Scope:

- systemd multi-instance deployment flow
- deploy inputs, precedence, generated runtime files
- deployment operations and script-layer security notes

Out of scope:

- product/API transport contract and JSON-RPC semantics

For product/protocol behavior, see [`../docs/guide.md`](../docs/guide.md).

## Prerequisites

- `systemd` and `sudo` available
- OpenCode core path prepared (default `/opt/.opencode`)
- repo path prepared (default `/opt/opencode-a2a/opencode-a2a-serve`)
- A2A venv prepared (default `${OPENCODE_A2A_DIR}/.venv/bin/opencode-a2a-serve`)
- uv python pool prepared (default `/opt/uv-python`)

For one-time host bootstrap, see [`init_system_readme.md`](./init_system_readme.md).

## Quick Deploy

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

HTTPS public URL example:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_public_url=https://a2a.example.com
```

Upgrade existing instance after shared-code update:

```bash
GH_TOKEN='<gh-token>' A2A_BEARER_TOKEN='<a2a-token>' \
./scripts/deploy.sh project=alpha update_a2a=true force_restart=true
```

## Input Model

### Precedence

For values that support both env and CLI:

`CLI key=value` > process env > built-in default.

### Required Secrets

- `GH_TOKEN`
- `A2A_BEARER_TOKEN`

### CLI Keys

Supported keys (case-insensitive):

- `project` / `project_name`
- `data_root`
- `a2a_port`, `a2a_host`, `a2a_public_url`
- `a2a_streaming`, `a2a_log_level`, `a2a_otel_instrumentation_enabled`
- `a2a_log_payloads`, `a2a_log_body_limit`
- `a2a_cancel_abort_timeout_seconds`, `a2a_enable_session_shell`
- `opencode_provider_id`, `opencode_model_id`, `opencode_lsp`, `opencode_log_level`
- `opencode_timeout`, `opencode_timeout_stream`
- `repo_url`, `repo_branch`
- `git_identity_name`, `git_identity_email`
- `update_a2a`, `force_restart`

Sensitive values are blocked from CLI keys by design.

## Configuration Details

### Secret Variables

| ENV Name | Required | Default | CLI Support | Notes |
| --- | --- | --- | --- | --- |
| `GH_TOKEN` | Yes | None | No | Used by OpenCode and `gh auth login`. |
| `A2A_BEARER_TOKEN` | Yes | None | No | Written to `a2a.env`. |
| `GOOGLE_GENERATIVE_AI_API_KEY` | Optional | None | No | Persisted to `opencode.secret.env` when provided. |
| `OPENAI_API_KEY` | Optional | None | No | Persisted to `opencode.secret.env` when provided. |
| `ANTHROPIC_API_KEY` | Optional | None | No | Persisted to `opencode.secret.env` when provided. |
| `AZURE_OPENAI_API_KEY` | Optional | None | No | Persisted to `opencode.secret.env` when provided. |
| `OPENROUTER_API_KEY` | Optional | None | No | Persisted to `opencode.secret.env` when provided. |

### Non-Secret Input Variables

| ENV Name | CLI Key | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `OPENCODE_A2A_DIR` | - | Optional | `/opt/opencode-a2a/opencode-a2a-serve` | Repo path. |
| `OPENCODE_CORE_DIR` | - | Optional | `/opt/.opencode` | OpenCode core path. |
| `UV_PYTHON_DIR` | - | Optional | `/opt/uv-python` | uv python pool path. |
| `UV_PYTHON_DIR_GROUP` | - | Optional | `opencode` | Optional shared-group access control. |
| `DATA_ROOT` | `data_root` | Optional | `/data/opencode-a2a` | Instance root directory. |
| `OPENCODE_BIND_HOST` | - | Optional | `127.0.0.1` | OpenCode bind host. |
| `OPENCODE_BIND_PORT` | - | Optional | `A2A_PORT + 1` (fallback `4096`) | OpenCode bind port. |
| `OPENCODE_LOG_LEVEL` | `opencode_log_level` | Optional | `WARNING` | OpenCode log level. `WARNING` is normalized to `WARN` before launch. |
| `OPENCODE_EXTRA_ARGS` | - | Optional | empty | Extra OpenCode startup args. |
| `OPENCODE_PROVIDER_ID` | `opencode_provider_id` | Optional | None | Written to `a2a.env`. |
| `OPENCODE_MODEL_ID` | `opencode_model_id` | Optional | None | Written to `a2a.env`. |
| `OPENCODE_LSP` | `opencode_lsp` | Optional | `false` | LSP toggle for deployed instance. |
| `OPENCODE_TIMEOUT` | `opencode_timeout` | Optional | None (`setup_instance.sh` writes `300`) | OpenCode request timeout. |
| `OPENCODE_TIMEOUT_STREAM` | `opencode_timeout_stream` | Optional | None | OpenCode stream timeout. |
| `GIT_IDENTITY_NAME` | `git_identity_name` | Optional | `OpenCode-<project>` | Git name for instance user. |
| `GIT_IDENTITY_EMAIL` | `git_identity_email` | Optional | `<project>@example.com` | Git email for instance user. |
| `REPO_URL` | `repo_url` | Optional | None | Optional repository URL to auto-clone into `workspace/` on first deploy. |
| `REPO_BRANCH` | `repo_branch` | Optional | None | Optional branch used with `REPO_URL` during first clone. |
| `A2A_HOST` | `a2a_host` | Optional | `127.0.0.1` | A2A bind host. |
| `A2A_PORT` | `a2a_port` | Optional | `8000` | A2A bind port. |
| `A2A_PUBLIC_URL` | `a2a_public_url` | Optional | `http://<A2A_HOST>:<A2A_PORT>` | Public URL in Agent Card. |
| `A2A_STREAMING` | `a2a_streaming` | Optional | `true` | SSE streaming toggle. |
| `A2A_LOG_LEVEL` | `a2a_log_level` | Optional | `WARNING` | A2A log level. |
| `A2A_OTEL_INSTRUMENTATION_ENABLED` | `a2a_otel_instrumentation_enabled` | Optional | `false` | Generates `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED` in `a2a.env`. |
| `A2A_LOG_PAYLOADS` | `a2a_log_payloads` | Optional | `false` | Payload logging toggle. |
| `A2A_LOG_BODY_LIMIT` | `a2a_log_body_limit` | Optional | `0` | Payload body max length. |
| `A2A_CANCEL_ABORT_TIMEOUT_SECONDS` | `a2a_cancel_abort_timeout_seconds` | Optional | `2.0` | Best-effort timeout for upstream `session.abort` on `tasks/cancel`. |
| `A2A_ENABLE_SESSION_SHELL` | `a2a_enable_session_shell` | Optional | `false` | Enables high-risk `opencode.sessions.shell`. |

### Auto-Generated Runtime Variables

| Generated Name | Source | Where Written | Notes |
| --- | --- | --- | --- |
| `A2A_PROJECT` | derived from `project=<name>` | `config/a2a.env` | Generated by `setup_instance.sh`; not direct deploy input. |

## Generated Layout and Files

Per project instance (default: `/data/opencode-a2a/<project>`):

- `workspace/`
- `config/`
- `logs/`
- `run/`

Generated config files:

- `config/opencode.env`
- `config/opencode.secret.env`
- `config/a2a.env`

## Provider Coverage (Deploy Script Layer)

| Provider | Secret key persisted by deploy scripts | Startup key enforcement in `run_opencode.sh` |
| --- | --- | --- |
| Google / Gemini | `GOOGLE_GENERATIVE_AI_API_KEY` | Yes (explicit checks for `provider=google` or gemini model pattern) |
| OpenAI | `OPENAI_API_KEY` | No explicit provider-specific check |
| Anthropic | `ANTHROPIC_API_KEY` | No explicit provider-specific check |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` | No explicit provider-specific check |
| OpenRouter | `OPENROUTER_API_KEY` | No explicit provider-specific check |

Known gap: provider/model validation is partial in deploy scripts.

## Service Operations

Status:

```bash
sudo systemctl status opencode@<project>.service
sudo systemctl status opencode-a2a@<project>.service
```

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

Remove one instance:

```bash
./scripts/uninstall.sh project=<project>
./scripts/uninstall.sh project=<project> confirm=UNINSTALL
```

See [`uninstall_readme.md`](./uninstall_readme.md) for safety behavior.

## Security Notes

- `a2a_enable_session_shell=true` enables `opencode.sessions.shell`, a high-risk capability that can execute shell commands in workspace context.
- Enable shell control only for trusted operators/internal use with strong token governance and audit controls.
- This architecture does not provide hard credential isolation from agent behavior.
