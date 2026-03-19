# Agent Self-Deploy and Release SOP

Related issue: `#145`

This SOP explains how an operator can provision, verify, and release a formal
`opencode-a2a-server` deployment.

## Goal

The operator or calling agent should be able to:

1. bootstrap one host for released package deployment
2. start one isolated OpenCode + `opencode-a2a-server` instance with systemd
3. verify readiness and basic availability
4. stop or uninstall the instance safely when it is no longer needed

## Scope and Boundaries

- This SOP covers the release-based systemd deployment path:
  - `opencode-a2a-server deploy-release`: release-based, systemd-managed,
    recommended for formal multi-instance deployment
- Source-based systemd/bootstrap paths remain available only for
  contributor/internal debugging and are documented under `scripts/`.
- Existing-user self-start is documented in the repository README as direct CLI
  commands, not as a deployment script path.
- This SOP does not replace protocol documentation. For API and runtime
  behavior, see [`guide.md`](./guide.md).
- This SOP does not define Docker or Kubernetes flows.

## Choose the Deployment Mode

| Mode | Command | Best for | Trust boundary | Secret handling |
| --- | --- | --- | --- | --- |
| release systemd deploy | `opencode-a2a-server deploy-release` | long-running, production-oriented deployments pinned to published package versions | isolated project directory under `DATA_ROOT`, systemd units, root-managed config | supports secure default two-step provisioning; `ENABLE_SECRET_PERSISTENCE=true` is optional and explicit |

Use `deploy-release` when you need:

- systemd restart behavior
- stable per-project runtime directories
- root-only secret files
- multiple named instances on one host
- published package versions as the deployment boundary

## Shared Input Contract

### Required Inputs

For `opencode-a2a-server deploy-release`:

- `project=<name>`
- `GH_TOKEN` and `A2A_BEARER_TOKEN`
  - required immediately when `ENABLE_SECRET_PERSISTENCE=true`
  - otherwise required in root-only secret env files before the second deploy

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

## Path A: Release Systemd Deploy (`opencode-a2a-server deploy-release`)

This is the preferred path for durable and production-oriented deployments.

### Preconditions

Recommended checks:

```bash
command -v systemctl
command -v sudo
```

`deploy-release` now performs the effective non-interactive sudo preflight
itself. In agent/non-TTY runs, the command fails fast unless `sudo -n` already
works for the required operations.

One-time host bootstrap:

```bash
opencode-a2a-server init-release-system
```

If you need an exact published package version for bootstrap or rollback,
provide `A2A_RELEASE_VERSION=<version>` to `opencode-a2a-server init-release-system`
and `release_version=<version>` to `opencode-a2a-server deploy-release`.

### Secret Strategy

`deploy-release` supports two secret modes.

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
opencode-a2a-server deploy-release project=alpha a2a_port=8010 a2a_host=127.0.0.1
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
opencode-a2a-server deploy-release project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

#### Option A2: explicit secret persistence (`ENABLE_SECRET_PERSISTENCE=true`)

```bash
read -rsp 'GH_TOKEN: ' GH_TOKEN; echo
read -rsp 'A2A_BEARER_TOKEN: ' A2A_BEARER_TOKEN; echo
GH_TOKEN="${GH_TOKEN}" A2A_BEARER_TOKEN="${A2A_BEARER_TOKEN}" ENABLE_SECRET_PERSISTENCE=true \
opencode-a2a-server deploy-release project=alpha a2a_port=8010 a2a_host=127.0.0.1
```

#### Option A3: shell-enabled systemd deploy with stricter isolation

Use this only for trusted operators who explicitly need
`opencode.sessions.shell`.

```bash
opencode-a2a-server deploy-release \
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
opencode-a2a-server deploy-release project=alpha a2a_port=8010 a2a_public_url=https://a2a.example.com
```

### Update or Restart

```bash
opencode-a2a-server deploy-release project=alpha update_a2a=true force_restart=true
```

### Readiness Checks

Check systemd status:

```bash
sudo systemctl status opencode@alpha.service --no-pager
sudo systemctl status opencode-a2a-server@alpha.service --no-pager
```

Check health:

```bash
curl -fsS -H "Authorization: Bearer <token>" http://127.0.0.1:8010/health
```

Optional Agent Card check:

```bash
curl -fsS http://127.0.0.1:8010/.well-known/agent-card.json
```

Success criteria:

- `opencode-a2a-server deploy-release` exits with code `0`
- the command prints one JSON status line with `{"status":"ok","category":"ready",...}`
- `opencode@<project>.service` and `opencode-a2a-server@<project>.service`
  are active/running
- authenticated `GET /health` returns HTTP 200 with `{"status":"ok"}`
- requests above `A2A_MAX_REQUEST_BODY_BYTES` are rejected with HTTP `413`

Inspect hardening overrides:

```bash
sudo systemctl cat opencode@alpha.service
sudo systemctl cat opencode-a2a-server@alpha.service
```

### Release / Uninstall

Preview first:

```bash
opencode-a2a-server uninstall-instance project=alpha
```

Apply:

```bash
opencode-a2a-server uninstall-instance project=alpha confirm=UNINSTALL
```

Notes:

- shared template units are not removed
- preview mode is non-destructive
- uninstall may return exit code `2` when completion includes non-fatal warnings
- uninstall removes instance-specific systemd drop-ins before `daemon-reload`

## Failure Modes and Recovery Guidance

Common failure classes:

1. missing required secrets
2. `sudo` unavailable or interactive policy not satisfied for systemd deploy
3. invalid `project` or port inputs
4. provider/model configuration without matching provider keys
5. readiness check failure after process start

Recommended response:

1. inspect command stderr
2. if present, parse the final JSON status line for `category`
3. inspect systemd or local log files
4. fix missing inputs or secret files
5. re-run the same deploy command

For systemd logs:

```bash
sudo journalctl -u opencode@alpha.service -n 200 --no-pager
sudo journalctl -u opencode-a2a-server@alpha.service -n 200 --no-pager
```

## Security Baseline

- Do not pass secrets through CLI flags or `key=value` arguments.
- `ENABLE_SECRET_PERSISTENCE=true` is an explicit tradeoff, not the secure
  default.
- `A2A_ENABLE_SESSION_SHELL=true` remains a high-risk switch and should be
  limited to trusted internal cases.
- One deployed instance pair is a single-tenant trust boundary, not a secure
  multi-tenant runtime.

## Minimal Execution Templates

### systemd deploy

1. run `opencode-a2a-server init-release-system` once per host if needed
2. choose secret mode
3. execute `opencode-a2a-server deploy-release` for formal systemd deploys
4. verify service state and `/health`
5. later run `opencode-a2a-server uninstall-instance` with preview first

### contributor/internal source debug

1. use [`../scripts/init_system_readme.md`](../scripts/init_system_readme.md) only when you intentionally need a source checkout on a systemd host
2. use [`../scripts/deploy_readme.md`](../scripts/deploy_readme.md) only for contributor/internal debugging against unreleased source changes
