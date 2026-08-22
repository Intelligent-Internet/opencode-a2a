# Security Architecture, Surface, and Residual Risks

This document is the single source of truth for the adapter's security
surface: network listeners and routes, authentication and authorization, input
limits and boundary guards, outbound side effects, the end-to-end mapping from
remote A2A input to OpenCode side effects, and the residual-risk register.

It is a living document. Security-relevant changes must update the mapping and
the risk register in the same change that alters the behavior. It complements,
and does not replace:

- [SECURITY.md](../SECURITY.md) — threat model, trust boundary, and
  vulnerability reporting;
- [guide.md](./guide.md) — operational configuration, defaults, and runbooks;
- [compatibility.md](./compatibility.md) — wire-level compatibility promises.

One-off audit conclusions, review records, and test plans are intentionally
not stored here; they live in the issue/PR history of the repository.

## Network Surface

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

## Security Controls

### Error Text Redaction

Client-visible error text must never expose absolute local filesystem paths.
All error responses that can leave the process pass through a single
deterministic masker (`opencode_a2a.redact.redact_absolute_paths`) before
serialization; masked output uses the fixed placeholder `<redacted-path>`.

Boundaries covered:

- Streaming task error messages — `execution/executor.py:_emit_error`
  (task status messages and streamed error artifacts).
- JSON-RPC error responses — `jsonrpc/error_responses.py:adapt_jsonrpc_error`
  (message, metadata values, and standard JSON-RPC error-code `data`).
- REST/HTTP error bodies — `jsonrpc/error_responses.py:build_http_error_body`.
- Raw-exception fallback — `jsonrpc/application.py:_generate_error_response`
  (SDK base-class wrapping of non-JSON-RPC exceptions).

Masked: POSIX/Windows/UNC absolute paths and `file://` local URIs. Preserved:
remote URLs (`scheme://host/path`), relative paths, ordinary prose, and
slash-prefixed API route tokens such as `/message:send` or `/tasks/{id}:cancel`
(a path-like token immediately followed by a `:` method suffix or `{` route
template is treated as an API route, not a local path). Server-side logs
intentionally retain full exception context for diagnosability; logs that
leave the host must be redacted or access-restricted before export.

Remote peer error text (for example the upstream `detail` field surfaced by
`execution/upstream_error_translator.py`) is remote content, not a local path
leak; it is out of scope for this control and should be assessed separately if
it becomes a trust concern.

### Release Integrity

Every GitHub Release must ship a `SHA256SUMS` checksum manifest alongside the
wheel and sdist artifacts so consumers can verify artifact integrity
independently of the registry. `.github/workflows/publish.yml` regenerates
`dist/SHA256SUMS` at publish time from the built assets (sorted by basename,
`sha256sum -c` compatible) and uploads it as a release asset; existing assets
are skipped idempotently.

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
| R-6 | Push notification config surface exposed-but-unsupported (HTTP 501 / JSON-RPC unsupported) | Accepted | Contract kept explicitly unsupported; capability recovery is intentionally deferred |
| R-7 | SQLite hardening exempts `:memory:` / `file:` URIs and non-POSIX platforms | Accepted | Plain absolute file path recommended for deployments. [guide.md](./guide.md) "SQLite Persistence Hardening" |

## Maintenance Rules

- This document is the canonical security-surface reference. Update the route
  table, mapping, or risk register in the same change that alters listeners,
  routes, authentication/authorization, input limits, outbound policy,
  persistence hardening, error-text redaction boundaries, release integrity,
  or known residual risks.
- Do not turn this document into a report for a specific review cycle: keep it
  focused on facts that remain true for the current code and must stay
  maintainable. One-off conclusions belong in issue/PR history.
