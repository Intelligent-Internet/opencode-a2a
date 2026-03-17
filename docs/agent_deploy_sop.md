# Agent Self-Deploy and Release SOP

Related issue: `#145`

This SOP explains how a consumer-side agent can provision, verify, and release
its own `opencode-a2a-server` instance pair.

## Goal

The operator or calling agent should be able to:

1. choose the correct deployment path
2. start one isolated OpenCode + `opencode-a2a-server` instance
3. verify readiness and basic availability
4. stop or uninstall the instance safely when it is no longer needed

## Scope and Boundaries

- This SOP covers two supported startup paths:
  - `scripts/deploy.sh`: systemd-managed, production-oriented, multi-instance
    deployment
  - `scripts/deploy_light.sh`: lightweight current-user background supervisor
- This SOP does not replace protocol documentation. For API and runtime
  behavior, see [`guide.md`](./guide.md).
- This SOP does not define Docker or Kubernetes flows.

## Choose the Deployment Mode

| Mode | Script | Best for | Trust boundary | Secret handling |
| --- | --- | --- | --- | --- |
| systemd deploy | `scripts/deploy.sh` | long-running, multi-instance, production-oriented setups | isolated project directory under `DATA_ROOT`, systemd units, root-managed config | supports secure default two-step provisioning; `ENABLE_SECRET_PERSISTENCE=true` is optional and explicit |
| lightweight deploy | `scripts/deploy_light.sh` | trusted local/self-host use under the current Linux user | current user and current workspace | secrets come from the current shell environment; `ENABLE_SECRET_PERSISTENCE` does not apply |

Use `deploy.sh` when you need:

- systemd restart behavior
- stable per-project runtime directories
- root-only secret files
- multiple named instances on one host

Use `deploy_light.sh` when you need:

- fast local startup
- no systemd units
- no root-managed instance layout
- one trusted current-user runtime boundary

## Shared Input Contract

### Required Inputs

For `deploy.sh`:

- `project=<name>`
- `GH_TOKEN` and `A2A_BEARER_TOKEN`
  - required immediately when `ENABLE_SECRET_PERSISTENCE=true`
  - otherwise required in root-only secret env files before the second deploy

For `deploy_light.sh`:

- `A2A_BEARER_TOKEN`
- `workdir=/abs/path/to/workspace`

### Common Optional Inputs

- `a2a_host=<host>`
- `a2a_port=<port>`
- `a2a_public_url=<url>`
- `opencode_provider_id=<id>`
- `opencode_model_id=<id>`

### Provider Keys

Provider secrets are environment-only inputs:

- `GOOGLE_GENERATIVE_AI_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `AZURE_OPENAI_API_KEY`
- `OPENROUTER_API_KEY`

Do not pass these values via CLI `key=value`.

## Path A: systemd Deploy (`deploy.sh`)

This is the preferred path for durable and production-oriented deployments.

### Preconditions

Recommended checks:

```bash
command -v systemctl
command -v sudo
```

One-time host bootstrap:

```bash
./scripts/init_system.sh
```

`init_system.sh` defaults to the latest GitHub Release tag of
`opencode-a2a-server`, not `main`. If you need a reproducible rollback target,
edit `OPENCODE_A2A_REF` in [`scripts/init_system.sh`](../scripts/init_system.sh)
to an exact release tag before bootstrap.

### Secret Strategy

`deploy.sh` supports two secret modes.

Default and recommended mode:

- `ENABLE_SECRET_PERSISTENCE=false`
- deploy does not write `GH_TOKEN`, `A2A_BEARER_TOKEN`, or provider keys to disk
- root-only runtime secret files must be provisioned under
  `/data/opencode-a2a/<project>/config/`

Optional legacy-style mode:

- `ENABLE_SECRET_PERSISTENCE=true`
- deploy writes root-only secret env files for the instance
- use only when you explicitly accept secret persistence on disk

### Start Instructions

#### Option A1: secure two-step deploy (`ENABLE_SECRET_PERSISTENCE=false`)

Bootstrap directories and example files:

```bash
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

Populate the generated templates as `root`:

```bash
sudo cp /data/opencode-a2a/alpha/config/opencode.auth.env.example /data/opencode-a2a/alpha/config/opencode.auth.env
sudo cp /data/opencode-a2a/alpha/config/a2a.secret.env.example /data/opencode-a2a/alpha/config/a2a.secret.env
sudoedit /data/opencode-a2a/alpha/config/opencode.auth.env
sudoedit /data/opencode-a2a/alpha/config/a2a.secret.env
```

Re-run deploy to start services:

```bash
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

#### Option A2: explicit secret persistence (`ENABLE_SECRET_PERSISTENCE=true`)

```bash
read -rsp 'GH_TOKEN: ' GH_TOKEN; echo
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

#### Option A3: shell-enabled systemd deploy with stricter isolation

Use this only for trusted operators who explicitly need
`opencode.sessions.shell`.

```bash
./scripts/deploy.sh \
  project=alpha \
  a2a_port=8010 \
  a2a_host=127.0.0.1 \
  a2a_enable_session_shell=true \
  a2a_strict_isolation=true
```

Recommended additions for shell-enabled instances:

- keep the default systemd hardening drop-ins
- consider `a2a_systemd_memory_max=<value>` and `a2a_systemd_cpu_quota=<value>`
- verify audit lines with `journalctl ... | grep session_shell_audit`

Public URL example:

```bash
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
./scripts/deploy.sh project=alpha a2a_port=8010 a2a_public_url=https://a2a.example.com
```

### Update or Restart

```bash
./scripts/deploy.sh project=alpha update_a2a=true force_restart=true
```

### Readiness Checks

Check systemd status:

```bash
sudo systemctl status opencode@alpha.service --no-pager
sudo systemctl status opencode-a2a-server@alpha.service --no-pager
```

Check health:

```bash
curl -fsS http://127.0.0.1:8010/health
```

Optional Agent Card check:

```bash
curl -fsS http://127.0.0.1:8010/.well-known/agent-card.json
```

Success criteria:

- `deploy.sh` exits with code `0`
- `opencode@<project>.service` and `opencode-a2a-server@<project>.service`
  are active/running
- `GET /health` returns HTTP 200 with `{"status":"ok"}`
- requests above `A2A_MAX_REQUEST_BODY_BYTES` are rejected with HTTP `413`

Inspect hardening overrides:

```bash
sudo systemctl cat opencode@alpha.service
sudo systemctl cat opencode-a2a-server@alpha.service
```

### Release / Uninstall

Preview first:

```bash
./scripts/uninstall.sh project=alpha
```

Apply:

```bash
./scripts/uninstall.sh project=alpha confirm=UNINSTALL
```

Notes:

- shared template units are not removed
- preview mode is non-destructive
- uninstall may return exit code `2` when completion includes non-fatal warnings
- uninstall removes instance-specific systemd drop-ins before `daemon-reload`

## Path B: Lightweight Deploy (`deploy_light.sh`)

This path is for trusted local or self-host scenarios under the current Linux
user.

### Key Differences from `deploy.sh`

- no systemd units
- no root-only instance config layout
- no `ENABLE_SECRET_PERSISTENCE`
- provider keys and `A2A_BEARER_TOKEN` are inherited directly from the current
  shell environment

### Start Instructions

Minimum example:

```bash
export A2A_BEARER_TOKEN='<a2a-token>'
./scripts/deploy_light.sh start workdir=/abs/path/to/workspace
```

Example with explicit ports and instance name:

```bash
export A2A_BEARER_TOKEN='<a2a-token>'
./scripts/deploy_light.sh start \
  instance=demo \
  workdir=/srv/workspaces/demo \
  a2a_host=127.0.0.1 \
  a2a_port=8010 \
  a2a_public_url=http://127.0.0.1:8010 \
  opencode_bind_host=127.0.0.1 \
  opencode_bind_port=4106
```

If provider keys are needed, export them in the same shell before startup:

```bash
export OPENAI_API_KEY='<openai-key>'
export A2A_BEARER_TOKEN='<a2a-token>'
./scripts/deploy_light.sh start workdir=/abs/path/to/workspace
```

### Lifecycle Commands

```bash
./scripts/deploy_light.sh status
./scripts/deploy_light.sh stop
./scripts/deploy_light.sh restart workdir=/abs/path/to/workspace
```

### Readiness Checks

`deploy_light.sh start` already waits for both:

1. OpenCode runtime readiness
2. local Agent Card readiness

You can still verify manually:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/.well-known/agent-card.json
```

### Release

```bash
./scripts/deploy_light.sh stop
```

This stops the current-user background processes and preserves local logs/run
metadata under `logs/light/<instance>/` and `run/light/<instance>/`.

## Failure Modes and Recovery Guidance

Common failure classes:

1. missing required secrets
2. `sudo` unavailable or interactive policy not satisfied for systemd deploy
3. invalid `project` or port inputs
4. provider/model configuration without matching provider keys
5. readiness check failure after process start

Recommended response:

1. inspect command stderr
2. inspect systemd or local log files
3. fix missing inputs or secret files
4. re-run the same deploy command

For systemd logs:

```bash
sudo journalctl -u opencode@alpha.service -n 200 --no-pager
sudo journalctl -u opencode-a2a-server@alpha.service -n 200 --no-pager
```

## Security Baseline

- Do not pass secrets through CLI flags or `key=value` arguments.
- `ENABLE_SECRET_PERSISTENCE=true` is an explicit tradeoff, not the secure
  default.
- `deploy_light.sh` assumes the current user is already trusted with provider
  keys and workspace access.
- `A2A_ENABLE_SESSION_SHELL=true` remains a high-risk switch and should be
  limited to trusted internal cases.
- One deployed instance pair is a single-tenant trust boundary, not a secure
  multi-tenant runtime.

## Minimal Execution Templates

### systemd deploy

1. run `init_system.sh` once per host if needed
2. choose secret mode
3. execute `deploy.sh`
4. verify service state and `/health`
5. later run `uninstall.sh` with preview first

### lightweight deploy

1. export `A2A_BEARER_TOKEN` and any needed provider keys
2. execute `deploy_light.sh start`
3. verify `/health` or Agent Card
4. later run `deploy_light.sh stop`
