# Security Audit #499 — Network Surface Mapping and Residual Risk Register

This document consolidates the audit-mapping deliverables of
[issue #499](https://github.com/Intelligent-Internet/opencode-a2a/issues/499)
(system security audit aligned with codex-a2a #331):

- full network-surface mapping: listeners, routes, authentication and
  authorization, input limits, and outbound side effects;
- end-to-end mapping of remote A2A input to OpenCode session/tool/file side
  effects;
- the residual-risk register, including the risks called out during audit
  close-out.

It is the single source of truth for the audit mapping and risk register.
Operational configuration details live in [guide.md](./guide.md), the threat
model lives in [SECURITY.md](../SECURITY.md), and the one-off security report,
test plan, and supply-chain review record live in issue #499 and its closing PR
(per maintainer decision, audit conclusions are not duplicated into repository
docs to avoid drift).

## Audit Scope and Baseline

- Audit start baseline: `adf2a9b` (recorded in #499).
- Reviewed head: `23f8968` (main).
- Fixes landed via merged PRs:

| Audit item | Issue | Fix PR |
| --- | --- | --- |
| Outbound SSRF / credential exfiltration | #500 | #505 |
| SQLite persistence hardening | #501 | #507 |
| Rate limits and streaming budgets | #502 | #509 |
| Discovery response normalization | #503 | #506 |
| Inbound Origin/Host boundary | #504 | #508 |

## Network Surface Mapping

### Listeners and Bind

- The service is a FastAPI/uvicorn app bound to `A2A_HOST:A2A_PORT`
  (defaults `127.0.0.1:8000`).
- Binding to a non-loopback address without `A2A_ALLOWED_HOSTS` logs a startup
  warning (DNS-rebinding exposure); see residual risk R-3.

### HTTP+JSON (REST) Routes

All REST routes are served at the root path (no `/v1` prefix).

| Method | Path | Purpose | Auth |
| --- | --- | --- | --- |
| GET | `/.well-known/agent-card.json` | Public Agent Card | Anonymous |
| GET | `/extendedAgentCard` | Authenticated extended Agent Card | Credential |
| POST | `/message:send` | Send a task message (unary) | Credential |
| POST | `/message:stream` | Send a task message (SSE stream) | Credential |
| POST | `/tasks/{id}:cancel` | Cancel a task | Credential |
| GET/POST | `/tasks/{id}:subscribe` | Subscribe to a task stream | Credential |
| GET | `/tasks/{id}` | Get a task | Credential |
| GET | `/tasks` | List tasks | Credential |
| GET/POST/DELETE | `/tasks/{id}/pushNotificationConfigs[/{push_id}]` | Push notification config (exposed-but-unsupported) | Credential |
| POST | `/` | JSON-RPC endpoint (core + extensions) | Credential |
| GET | `/health` | Health / runtime profile | Anonymous |
| GET | `/openapi.json` | Sanitized OpenAPI contract | Anonymous |

### JSON-RPC Surface

- Core A2A methods from the SDK dispatcher: `message/send`, `message/stream`,
  `tasks/get`, `tasks/cancel`, `tasks/list`, `tasks/subscribe`,
  `tasks/pushNotificationConfig/*`.
- Provider-private `opencode.*` extensions (authenticated extended card only):
  - `opencode.sessions.*`: `status`, `list`, `messages.list`, `get`, `children`,
    `todo`, `diff`, `message.get`, `prompt_async`, `command`, `fork`, `share`,
    `unshare`, `summarize`, `revert`, `unrevert`, `shell`
    (deployment-conditional);
  - `opencode.providers.*` / `opencode.models.*`: `list_providers`,
    `list_models`;
  - `opencode.projects.*` / `opencode.workspaces.*` / `opencode.worktrees.*`:
    discovery plus gated mutations (`create_workspace`, `remove_workspace`,
    `create_worktree`, `remove_worktree`, `reset_worktree`);
  - `opencode.interrupt.*`: `list_permissions`, `list_questions`,
    `reply_permission`, `reply_question`, `reject_question`.

### Authentication and Authorization

- `A2A_STATIC_AUTH_CREDENTIALS` (Basic/Bearer) is enforced before routing on
  every credential-protected endpoint; the public Agent Card and `/health` are
  anonymous.
- `credential_id` is carried as runtime metadata for audit/logging/rotation; it
  does not participate in principal resolution or authorization.
- Capability gates:
  - `A2A_ENABLE_SESSION_SHELL` → `CAPABILITY_SESSION_SHELL`;
  - `A2A_ENABLE_WORKSPACE_MUTATIONS` → `CAPABILITY_WORKSPACE_MUTATION`;
  - enforced via `request_has_capability` at the handler boundary.
- The runtime is a single-tenant trust boundary by design; see
  [SECURITY.md](../SECURITY.md) for the threat model.

### Input Limits and Boundary Guards

| Guard | Mechanism | Defaults / Notes |
| --- | --- | --- |
| CSRF / Origin | `A2A_ALLOWED_ORIGINS` + `A2A_PUBLIC_URL` origin | `Origin`-bearing requests must match; `Origin: null` rejected |
| DNS rebinding / Host | `A2A_ALLOWED_HOSTS` | Enforced when configured; startup warning otherwise on non-loopback bind |
| Body size | `A2A_MAX_REQUEST_BODY_BYTES` | 1 MiB |
| Rate limit | `A2A_RATE_LIMIT_ENABLED` / `_WINDOW_SECONDS` / `_MAX_REQUESTS` | On by default; 60 s window, 120 requests; 429 + `Retry-After` |
| Stream budgets | `A2A_STREAM_MAX_BYTES` / `_MAX_DURATION_SECONDS` / `_IDLE_TIMEOUT_SECONDS` | 64 MiB / 3600 s / 120 s; `0` disables |
| Payload logging | `A2A_LOG_PAYLOADS` / `A2A_LOG_BODY_LIMIT` | Opt-in; logs treated as sensitive |

### Outbound Side Effects

- `a2a_call` tool execution → `server/client_manager.py` (`borrow_client`) →
  `client/network_policy.py` validation: http/https only, no userinfo,
  `A2A_CLIENT_ALLOWED_HOSTS` allowlist, private/loopback address rejection, and
  credentials attached to allowlisted hosts only.
- CLI `opencode-a2a call` is operator-invoked and shares URL handling, but is
  not allowlist-bound.
- The upstream OpenCode client (`OPENCODE_BASE_URL`, auth, timeouts, concurrency
  caps) is the only other outbound path.

## End-to-End Mapping: Remote A2A Input → OpenCode Side Effects

| A2A input | Adapter path | OpenCode / host side effects |
| --- | --- | --- |
| `message/send` / `message/stream` | REST or JSON-RPC → middleware guards → `execution/executor.py` → `opencode_upstream_client.py` | Upstream OpenCode session prompt/command; stream normalization; task/message persistence |
| Session extension queries/mutations | `jsonrpc/handlers/session_*.py` | Read/shape session state; `fork`/`share`/`revert` mutate upstream session state |
| Workspace/worktree control | `jsonrpc/handlers/workspace_control.py` (mutation gated) | Project/workspace/worktree create/remove/reset → filesystem side effects inside the OpenCode workspace; responses normalized (no local paths) |
| Interrupt recovery/callback | `jsonrpc/handlers/interrupt_*.py` | Request-ID lifecycle, expiry, identity-scoped state |
| Tool execution during a session | `execution/tool_orchestration.py` | `a2a_call` may trigger remote agent work; other tools remain OpenCode-runtime behavior |
| Task/session persistence | `server/task_store.py`, `state_store.py`, `migrations.py` | SQLite task/state rows (hardened file access) |

## Residual Risk Register

| ID | Risk | Status | Mitigation / Reference |
| --- | --- | --- | --- |
| R-1 | Outbound DNS-rebinding TOCTOU: `a2a_call` validation resolves the host, then the connection re-resolves (resolve-then-connect) | Accepted | Allowlist + private-IP rejection reduce exposure; pinned-IP connect is not implemented. [guide.md](./guide.md) "Client Initialization Facade" |
| R-2 | Rate limiter is process-local; multi-process deployments bypass per-credential limits | Accepted | Documented; use a gateway-level limiter or per-instance limits. [guide.md](./guide.md) "Auth, Limits, and Failure Contract" |
| R-3 | Non-loopback bind without `A2A_ALLOWED_HOSTS` only warns at startup | Accepted | Operator contract: trusted network or reverse proxy validates Host. [guide.md](./guide.md) "Inbound Origin and Host Boundary" |
| R-4 | Single-tenant shared-workspace boundary; static credentials only by default | Accepted by design | Threat model and tenant guidance in [SECURITY.md](../SECURITY.md) |
| R-5 | Payload logging can capture sensitive data when enabled | Accepted | Opt-in with `A2A_LOG_BODY_LIMIT` cap; treat logs as sensitive. [SECURITY.md](../SECURITY.md) |
| R-6 | Push notification config surface exposed-but-unsupported (HTTP 501 / JSON-RPC unsupported) | Accepted | Contract kept explicit; capability recovery tracked separately in #451 |
| R-7 | SQLite hardening exempts `:memory:` / `file:` URIs and non-POSIX platforms | Accepted | Plain absolute file path recommended for deployments. [guide.md](./guide.md) "SQLite Persistence Hardening" |
