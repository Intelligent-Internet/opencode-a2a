# Usage Guide

This guide covers configuration, authentication, API behavior, streaming re-subscription, and A2A client examples. It is the canonical document for implementation-level protocol contracts and JSON-RPC extension details; [README](../README.md) stays at overview level, [architecture.md](./architecture.md) explains the service boundary, [maintainer-architecture.md](./maintainer-architecture.md) covers the internal module view for contributors, and [compatibility.md](./compatibility.md) defines the compatibility-sensitive surface.

## Transport Contracts

- The service supports both transports:
  - HTTP+JSON (REST endpoints such as `/message:send`)
  - JSON-RPC (`POST /`)
- Agent Card exposes both HTTP+JSON and JSON-RPC through `supportedInterfaces`.
- HTTP+JSON REST endpoints are served at the root path (e.g. `/message:send`, `/tasks`). A2A 1.0 negotiates protocol version via the `A2A-Version` header, not a URL prefix, so the URL path carries no version segment.
- Breaking change for earlier releases: versions through v1.1.2 served the HTTP+JSON surface under a `/v1` prefix. Deployments upgrading from those releases must repoint clients, reverse proxies, and any `/v1`-prefixed routes to the root paths.
- The public Agent Card is intentionally slimmed to the minimum discovery surface; per-extension disclosure policy is defined in [`extension-specifications.md`](./extension-specifications.md).
- Detailed provider-private contracts are served through the authenticated extended card endpoint `/extendedAgentCard`.
- Agent Card responses emit weak `ETag` and `Cache-Control`; clients should revalidate cached cards instead of repeatedly fetching full payloads.
- Global HTTP gzip compression is enabled for eligible non-streaming HTTP responses larger than `A2A_HTTP_GZIP_MINIMUM_SIZE` bytes when clients send `Accept-Encoding: gzip`; the default threshold is `8192`, so the main benefit currently lands on larger responses such as the authenticated extended card.
- A2A v1.0 Agent Cards expose extended-card availability through `AgentCard.capabilities.extendedAgentCard`. This service emits that field and does not emit the removed top-level `supportsAuthenticatedExtendedCard` field.
- Payload schema is transport-specific and should not be mixed:
  - REST and JSON-RPC both use v1 `message.parts` payloads and enum values such as `ROLE_USER`
  - JSON-RPC uses canonical PascalCase core methods such as `SendMessage` and `SubscribeToTask`
  - legacy `message.content`, lowercase roles, `{kind: ...}` wrappers, and `message/send` aliases are rejected

## Runtime Environment Variables

This section keeps only the protocol-relevant variables. For the full runtime variable catalog and defaults, see [`../src/opencode_a2a/config.py`](../src/opencode_a2a/config.py). Deployment supervision is intentionally out of scope for this project; use your own process manager, container runtime, or host orchestration.

Key variables to understand protocol behavior:

- `A2A_STATIC_AUTH_CREDENTIALS`: required static credential registry in JSON array form. Supports multiple `bearer` / `basic` credentials, bearer-only required `principal`, optional `credential_id`, optional `capabilities`, and optional `enabled=false` for explicit disablement.
- `OPENCODE_BASE_URL`: upstream OpenCode HTTP endpoint. Default: `http://127.0.0.1:4096`. In two-process deployments, set it explicitly.
- `OPENCODE_WORKSPACE_ROOT`: service-level default workspace root exposed to OpenCode when clients do not request a narrower directory override.
- `A2A_ALLOW_DIRECTORY_OVERRIDE`: controls whether clients may pass `metadata.opencode.directory`.
- `A2A_ENABLE_SESSION_SHELL`: gates high-risk JSON-RPC method `opencode.sessions.shell`.
- `A2A_ENABLE_WORKSPACE_MUTATIONS`: gates operator-only workspace/worktree mutation methods such as `opencode.workspaces.create` and `opencode.worktrees.reset`.
- `A2A_EXPOSE_WORKSPACE_ROOT_IN_CARD`: default `false`; controls whether the authenticated extended Agent Card includes the local `OPENCODE_WORKSPACE_ROOT` in the structured profile `runtime_context`. Keep disabled unless trusted callers explicitly need the host path.
- `A2A_SANDBOX_MODE` / `A2A_SANDBOX_FILESYSTEM_SCOPE` / `A2A_SANDBOX_WRITABLE_ROOTS`: declarative execution-boundary metadata for sandbox mode, filesystem scope, and optional writable roots.
- `A2A_NETWORK_ACCESS` / `A2A_NETWORK_ALLOWED_DOMAINS`: declarative execution-boundary metadata for network policy and optional allowlist disclosure.
- `A2A_APPROVAL_POLICY` / `A2A_APPROVAL_ESCALATION_BEHAVIOR`: declarative execution-boundary metadata for approval workflow.
- `A2A_WRITE_ACCESS_SCOPE` / `A2A_WRITE_ACCESS_OUTSIDE_WORKSPACE`: declarative execution-boundary metadata for write scope and whether writes may extend outside the primary workspace boundary.
- `A2A_HOST` / `A2A_PORT`: runtime bind address. Defaults: `127.0.0.1:8000`.
- `A2A_PUBLIC_URL`: public base URL advertised by the Agent Card. Default: `http://127.0.0.1:8000`.
- `A2A_ALLOWED_ORIGINS`: comma-separated extra browser-origin allowlist for inbound requests. Requests carrying an `Origin` header must match the origin of `A2A_PUBLIC_URL` or an entry here; mismatches are rejected with `403` (CSRF guard). Requests without an `Origin` header (CLI/SDK clients) are unaffected.
- `A2A_ALLOWED_HOSTS`: comma-separated `Host` header allowlist (exact names or `*.example.com` wildcards). When configured, every inbound request must present a matching `Host` header. Binding to a non-loopback address without this allowlist logs a startup warning (DNS rebinding risk).
- `A2A_LOG_LEVEL`: runtime log level. Default: `WARNING`.
- `A2A_LOG_PAYLOADS` / `A2A_LOG_BODY_LIMIT`: payload logging behavior and truncation. When `A2A_LOG_LEVEL=DEBUG`, upstream OpenCode stream events are also logged with preview truncation controlled by `A2A_LOG_BODY_LIMIT`.
- The runtime accepts W3C `traceparent` / `tracestate` headers on inbound requests. When `traceparent` is missing or invalid, the runtime generates a fresh valid value and exposes it on the HTTP response header.
- The active `traceparent` / `tracestate` pair is propagated across inbound A2A handling, OpenCode upstream requests, and outbound peer A2A calls triggered through the embedded client or `a2a_call` tool path.
- Logs derive a stable `trace_id` from the active `traceparent` so request-scoped log lines can be correlated without introducing high-cardinality metric labels.
- `A2A_HTTP_GZIP_MINIMUM_SIZE`: minimum eligible response-body size in bytes for global non-streaming HTTP gzip compression. Default: `8192`.
- `A2A_MAX_REQUEST_BODY_BYTES`: runtime request-body limit. Oversized requests return HTTP `413`.
- `A2A_RATE_LIMIT_ENABLED` / `A2A_RATE_LIMIT_WINDOW_SECONDS` / `A2A_RATE_LIMIT_MAX_REQUESTS`: per-credential sliding-window rate limiting (per-peer-IP for the unauthenticated Agent Card surface). Defaults: `true` / `60` seconds / `120` requests. Exceeding the window returns HTTP `429` with a `Retry-After` header; `A2A_RATE_LIMIT_ENABLED=false` disables the limiter.
- `A2A_STREAM_MAX_BYTES` / `A2A_STREAM_MAX_DURATION_SECONDS` / `A2A_STREAM_IDLE_TIMEOUT_SECONDS`: streaming (SSE) response budgets for total bytes, total duration, and idle gap. Defaults: `67108864` bytes / `3600` seconds / `120` seconds; `0` disables the respective budget.
- `A2A_PENDING_SESSION_CLAIM_TTL_SECONDS`: lease duration for pending preferred session claims before they expire and stop blocking other identities.
- `A2A_INTERRUPT_REQUEST_TTL_SECONDS`: active retention window for the interrupt request binding registry used by `a2a.interrupt.*` callback methods. Default: `10800` seconds (`180` minutes).
- `A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS`: retention window for expired interrupt tombstones after active TTL has elapsed. During this window, repeated replies keep returning `INTERRUPT_REQUEST_EXPIRED` instead of falling through to `INTERRUPT_REQUEST_NOT_FOUND`. Default: `600` seconds (`10` minutes).
- `A2A_CANCEL_ABORT_TIMEOUT_SECONDS`: best-effort timeout for upstream `session.abort` in cancel flow.
- `OPENCODE_TIMEOUT` / `OPENCODE_TIMEOUT_STREAM`: upstream request timeout and stream timeout. Defaults: `120` and `900` seconds.
- `OPENCODE_MAX_CONCURRENT_REQUESTS`: fast-fail concurrency limit for unary/control upstream calls. Default: `32`; `0` disables the limit explicitly.
- `OPENCODE_MAX_CONCURRENT_STREAMS`: fast-fail concurrency limit for long-lived upstream `/event` streams. Default: `8`; `0` disables the limit explicitly.
- `OPENCODE_AUTH_USERNAME` / `OPENCODE_AUTH_PASSWORD`: optional HTTP Basic credentials sent on every upstream OpenCode call. Set both when the upstream `opencode serve` is hardened with `OPENCODE_SERVER_PASSWORD`; the username defaults to `opencode` and must match `OPENCODE_SERVER_USERNAME` when that is overridden upstream. When unset, no `Authorization` header is sent.
- `A2A_CLIENT_TIMEOUT_SECONDS`: outbound client timeout. Default: `30` seconds.
- `A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS`: outbound Agent Card fetch timeout. Default: `5` seconds.
- `A2A_CLIENT_USE_CLIENT_PREFERENCE`: whether the outbound client prefers its own transport choices.
- `A2A_CLIENT_BEARER_TOKEN`: optional bearer token attached to outbound peer calls made by the embedded A2A client and `a2a_call` tool path.
- `A2A_CLIENT_BASIC_AUTH`: optional Basic auth credential attached to outbound peer calls made by the embedded A2A client and `a2a_call` tool path.
- `A2A_CLIENT_SUPPORTED_TRANSPORTS`: ordered outbound transport preference list.
- `A2A_CLIENT_ALLOWED_HOSTS`: comma-separated allowlist of outbound target hosts for the embedded A2A client and `a2a_call` tool path (exact names or `*.example.com` wildcards). When configured, outbound calls are restricted to matching hosts, and outbound credentials (`A2A_CLIENT_BEARER_TOKEN` / `A2A_CLIENT_BASIC_AUTH`) are only sent to allowlisted hosts.
- `A2A_CLIENT_ALLOW_PRIVATE_HOSTS`: default `false`. When `false`, outbound `a2a_call(...)` targets that resolve to private, loopback, link-local, reserved, or multicast addresses are rejected (SSRF / DNS-rebinding guard). Set to `true` only when the deployment intentionally targets A2A peers on the local network.
- `A2A_TASK_STORE_BACKEND`: unified lightweight persistence backend for SDK task rows plus adapter-managed session / interrupt state. Supported values: `database`, `memory`. Default: `database`.
- `A2A_TASK_STORE_DATABASE_URL`: database URL used by the unified durable backend when `A2A_TASK_STORE_BACKEND=database`. Default: `sqlite+aiosqlite:///./opencode-a2a.db`.
- On startup, the runtime only auto-migrates adapter-owned state tables; existing SDK-owned task tables must be upgraded explicitly with upstream `a2a-db`.
- Runtime authentication is configured only through the static credential registry declared by `A2A_STATIC_AUTH_CREDENTIALS`.
- The runtime maps authenticated requests to stable principals rather than credential-derived identities.
- With `A2A_STATIC_AUTH_CREDENTIALS`, every bearer credential must declare an explicit `principal`; Basic credentials always derive their runtime principal from `username`.
- `credential_id`, when provided, is carried as optional runtime metadata for audit, logging, diagnostics, credential-rotation workflows, authorization-denied diagnostics, and interrupt tracking; it does not participate in principal resolution or authorization decisions.
- Individual static credentials can be disabled by removing them from the registry or setting `enabled=false`, then restarting/reloading the deployment.
- High-risk methods require explicitly granted operator-level capabilities:
  - `opencode.sessions.shell`
  - `opencode.workspaces.create`
  - `opencode.workspaces.remove`
  - `opencode.worktrees.create`
  - `opencode.worktrees.remove`
  - `opencode.worktrees.reset`
- Runtime authentication also applies to `/health`; the public unauthenticated discovery surface remains `/.well-known/agent-card.json`.
- The authenticated extended card endpoint `/extendedAgentCard` accepts the same configured bearer/basic auth modes as the rest of the authenticated runtime surface.
- The same outbound client flags are also honored by the server-side embedded A2A client used for peer calls and `a2a_call` tool execution:
  - `A2A_CLIENT_TIMEOUT_SECONDS`
  - `A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS`
  - `A2A_CLIENT_USE_CLIENT_PREFERENCE`
  - `A2A_CLIENT_BEARER_TOKEN`
  - `A2A_CLIENT_BASIC_AUTH`
  - `A2A_CLIENT_SUPPORTED_TRANSPORTS`

## Inbound Origin and Host Boundary

Browsers store and automatically attach Basic credentials, and they send an `Origin` header with every request. Without an origin boundary, a malicious web page could cross-site trigger `SendMessage`, `CancelTask`, or task subscription against a Basic-protected service. The runtime therefore enforces:

- every request carrying an `Origin` header must match the origin of `A2A_PUBLIC_URL` (scheme, host, and non-default port) or an entry in `A2A_ALLOWED_ORIGINS`; mismatches return `403` before authentication. `Origin: null` (sandboxed iframes) is rejected unless explicitly allowlisted;
- requests without an `Origin` header — normal CLI/SDK peers — are not subject to origin checks;
- when `A2A_ALLOWED_HOSTS` is configured, every request must present a matching `Host` header (exact names, optional `host:port`, or `*.example.com` wildcards); mismatches return `403`. This also blocks DNS rebinding, where an attacker-controlled hostname resolves to the service address and the browser sends that hostname as `Host`;
- binding to a non-loopback address (`0.0.0.0`, `::`, or a LAN/interface address) without `A2A_ALLOWED_HOSTS` logs a startup warning: the service is then exposed to DNS rebinding and should only run behind a trusted network boundary or a reverse proxy that validates `Host`.

For browser-based clients, prefer the same origin as `A2A_PUBLIC_URL`, or explicitly allow the dashboard origin via `A2A_ALLOWED_ORIGINS` and treat `A2A_ALLOWED_HOSTS` as part of the deployment contract. Basic authentication should not be relied on as the sole browser-facing boundary: combine it with the origin/host checks above, short-lived credentials, and TLS at the public URL.

The security surface mapping and residual-risk register live in
[security-architecture.md](./security-architecture.md).

## Client Initialization Facade (Preview)

`opencode-a2a` now includes a minimal client bootstrap module in `src/opencode_a2a/client/` to support downstream consumer usage while keeping server and client concerns separate.

Boundary separation:

- Server code owns runtime request handling, transport orchestration, stream behavior, and public compatibility profile exposure.
- Client code owns peer card discovery, SDK client construction, operation call helpers, and protocol error normalization.

Current client facade API:

- `A2AClient.get_agent_card()`
- `A2AClient.send()` / `A2AClient.send_message()`
- `A2AClient.get_task()`
- `A2AClient.cancel_task()`
- `A2AClient.subscribe_to_task()`

Server-side outbound peer calls read outbound credentials from environment variables. Configure `A2A_CLIENT_BEARER_TOKEN` or `A2A_CLIENT_BASIC_AUTH` when the remote agent protects its runtime surface. The selected credential and fixed `A2A-Version` header are sent during Agent Card discovery and on subsequent peer operations such as `SendMessage` and `GetTask`. CLI outbound calls follow the same environment-only model.

The embedded `a2a_call(...)` tool lets the upstream model choose the target URL, so the adapter applies a fail-closed network policy before opening any connection:

- only `http`/`https` schemes are accepted, and URLs carrying userinfo credentials are rejected;
- when `A2A_CLIENT_ALLOWED_HOSTS` is set, the target host must match the allowlist (exact or `*.example.com` wildcard);
- unless `A2A_CLIENT_ALLOW_PRIVATE_HOSTS=true`, the resolved addresses must be public; private/loopback/link-local/reserved addresses are rejected even when the hostname matches the allowlist (DNS rebinding defense);
- outbound credentials are only attached to allowlisted hosts. If credentials are configured without an allowlist, they are never sent and a warning is logged;
- the `opencode-a2a call` CLI remains a manual operator action and is not subject to the allowlist, but still rejects non-http(s) schemes through the shared client URL handling.

CLI outbound example:

```bash
A2A_CLIENT_BEARER_TOKEN=peer-token \
opencode-a2a call http://other-agent:8000/.well-known/agent-card.json "How are you?"
```

Service base URLs also work, but this guide prefers Agent Card URLs in CLI examples because they make the A2A discovery target explicit.

`A2AClient.send()` returns the latest response event and keeps the default stream-first behavior. If a peer returns a non-terminal task snapshot and expects follow-up `GetTask` polling, enable the optional facade fallback with:

- `A2A_CLIENT_POLLING_FALLBACK_ENABLED=true`
- `A2A_CLIENT_POLLING_FALLBACK_INITIAL_INTERVAL_SECONDS`
- `A2A_CLIENT_POLLING_FALLBACK_MAX_INTERVAL_SECONDS`
- `A2A_CLIENT_POLLING_FALLBACK_BACKOFF_MULTIPLIER`
- `A2A_CLIENT_POLLING_FALLBACK_TIMEOUT_SECONDS`

The fallback only applies to `send()`, keeps `send_message()` and `subscribe_to_task()` as thin raw `StreamResponse` wrappers, and stops polling once the task reaches a terminal state or a caller-intervention state such as `input-required` or `auth-required`.

Execution-boundary metadata is intentionally declarative deployment metadata: it is published through `RuntimeProfile`, Agent Card, OpenAPI, and `/health`, and should not be interpreted as a live per-request privilege snapshot or a runtime CLI self-inspection result.

Recommended two-process example:

```bash
opencode serve --hostname 127.0.0.1 --port 4096
```

Configure provider auth and the default model on the OpenCode side before starting that upstream process:

- Add credentials with `opencode auth login` or `/connect`.
- Check available model IDs with `opencode models` or `opencode models <provider>`.
- Set the default model in `opencode.json`, for example:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "google/gemini-3-pro"
}
```

If your provider uses environment variables for auth, export them before starting `opencode serve`.

Do not assume startup-script env vars always erase previously persisted OpenCode auth state for the deployed user. When debugging provider-auth surprises, inspect the deployed user's HOME/XDG config directories and the OpenCode files stored there before concluding that `opencode-a2a` changed the credential selection.

Then start `opencode-a2a` against that explicit upstream URL:

```bash
DEMO_BEARER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_STATIC_AUTH_CREDENTIALS='[{"scheme":"bearer","token":"'"${DEMO_BEARER_TOKEN}"'","principal":"automation"}]' \
A2A_HOST=127.0.0.1 \
A2A_PORT=8000 \
A2A_PUBLIC_URL=http://127.0.0.1:8000 \
OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \
opencode-a2a serve
```

By default, the service uses a SQLite-backed durable state store:

```bash
DEMO_BEARER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
OPENCODE_BASE_URL=http://127.0.0.1:4096 \
A2A_STATIC_AUTH_CREDENTIALS='[{"scheme":"bearer","token":"'"${DEMO_BEARER_TOKEN}"'","principal":"automation"}]' \
A2A_TASK_STORE_DATABASE_URL=sqlite+aiosqlite:///./opencode-a2a.db \
opencode-a2a serve
```

With the default `database` backend, the unified lightweight persistence layer persists:

- task records
- session binding / ownership state
- pending preferred-session claims
- interrupt request bindings and tombstones

The supported persistence profile is one `opencode-a2a` application process using its own local SQLite database. Run Uvicorn with one worker and do not share the SQLite file between processes, containers, or replicas. Horizontal application scaling and PostgreSQL deployments are outside the supported deployment matrix. SQLAlchemy remains an internal implementation detail rather than a promise of dialect portability.

The runtime configures local durability-oriented SQLite connection settings (`WAL`, `busy_timeout`, `synchronous=NORMAL`) and creates missing parent directories for file-backed database paths.

### SQLite Persistence Hardening

File-backed SQLite databases are hardened at startup and on every new connection on POSIX systems:

- the database file must be a regular file; symlinks are rejected and fail startup;
- the file must be owned by the user running `opencode-a2a`; a database owned by another user fails startup;
- the database file is forced to mode `0600`, and existing SQLite sidecar files (`-wal`/`-shm`/`-journal`) are converged to the same mode;
- the parent directory is created with mode `0700` when it does not exist; keep the database directory private (no group/world access);
- if the database is replaced by a symlink, a special file, or a foreign-owned file while the service is running, the next connection fails closed.

`A2A_TASK_STORE_DATABASE_URL` values that do not map to a plain file path are exempt: `:memory:` and `file:` URI-style database components (including in-memory shared-cache databases) are not hardened. Prefer a plain absolute file path for deployments that need the startup guarantees. Non-POSIX platforms rely on the user account ACLs of the hosting directory; the same private-directory requirement applies there.

Deployment requirements:

- run `opencode-a2a` as a dedicated user and keep the database directory outside world-readable workspace trees;
- do not place the SQLite file on network-mounted or sync-managed directories;
- when migrating an existing database, fix ownership and mode before startup: `chown <service-user> <db>` and `chmod 600 <db>`, and ensure the parent directory is not group/world accessible.

The runtime automatically applies lightweight schema migrations for its custom state tables and records the applied version in `a2a_schema_version`. Schema-version writes are idempotent across concurrent first-start races, pending preferred-session claims now persist absolute `expires_at` timestamps, legacy rows without `expires_at` are pruned during migration instead of being reconstructed from historical TTL assumptions, and the built-in path currently targets the local SQLite deployment profile without requiring Alembic.

Database-backed task persistence also keeps the existing first-terminal-state-wins contract while tightening the SQLite path with an atomic terminal-write guard instead of relying only on process-local read-before-write checks.

At startup, the runtime logs a concise persistence summary covering the active backend, the redacted database URL when applicable, the shared persistence scope, and whether the SQLite local durability profile is active.

The adapter-owned state tables listed above remain managed by the internal migration runner. The SDK-owned `tasks` table does not use runtime auto-migration here; upgrade existing SDK task schemas explicitly with upstream `a2a-db` before starting the service after an SDK schema change. If `a2a-db` is unavailable in your environment, install the `a2a-sdk[db-cli]` extra first.

In-flight asyncio locks, outbound A2A client caches, and stream-local aggregation buffers remain process-local runtime state.

To opt into an ephemeral development profile, set:

```bash
A2A_TASK_STORE_BACKEND=memory
```

## Troubleshooting Provider Auth State

If one deployment works while another fails against the same upstream provider, check the deployed OpenCode user's local state before assuming the difference comes from the `opencode-a2a` package itself.

- Provider auth and service-level model defaults belong to `opencode serve`.
- The deployed user's HOME/XDG config directories are operational input.
- Existing OpenCode auth/config files may still influence runtime behavior even when you also inject provider env vars from a process manager or shell wrapper.
- Compare the deployed user's OpenCode auth/config files, HOME/XDG values, and effective workspace directory before blaming the A2A adapter layer.
- For OpenCode-specific auth/config troubleshooting, inspect files such as `~/.local/share/opencode/auth.json` and `~/.config/opencode/opencode.json` (or the equivalent XDG-resolved paths for that service user).

## Core Behavior

- The service forwards A2A `SendMessage` / `SendStreamingMessage` traffic to OpenCode session/message calls.
- Main chat requests may override the upstream model for one request through `metadata.shared.model`.
- Provider/model catalog discovery is available through `opencode.providers.list` and `opencode.models.list`.
- Main chat requests that explicitly send `configuration.acceptedOutputModes` must stay compatible with the declared chat output modes.
- Current main chat requests must continue accepting `text/plain`; requests that only accept `application/json` or other incompatible modes are rejected before execution starts.
- `application/json` is additive structured-output support for incremental `tool_call` payloads. It does not guarantee that ordinary assistant prose can always be losslessly represented as JSON, so consumers that expect normal chat text should keep accepting `text/plain`.
- When a client accepts `text/plain` but not `application/json`, structured `tool_call` payloads are downgraded to compact JSON text instead of being silently dropped.
- Accepted output-mode negotiation is persisted as task-scoped metadata so later `GetTask` and `SubscribeToTask` reads keep the same filtered response contract as the original send/stream request.
- Main chat input supports v1 `message.parts[]` passthrough:
  - `{"text": ...}` is forwarded as an OpenCode text part.
  - `{"raw": ..., "mediaType": ..., "filename": ...}` is forwarded as a `file` part with a `data:` URL.
  - `{"url": ..., "mediaType": ..., "filename": ...}` is forwarded as a `file` part with the original URI.
  - structured data-only input parts are rejected explicitly; they are not silently downgraded.
- Task state defaults to `completed` for successful turns.
- The deployment profile is single-tenant and shared-workspace. For detailed isolation principles and security boundaries, see [SECURITY.md](../SECURITY.md).

## Streaming Contract

- Streaming is always enabled in this server profile; `message:stream` is part of the stable runtime baseline.
- Streaming (`/message:stream`) emits an initial working `Task`, then incremental `TaskArtifactUpdateEvent` / `TaskStatusUpdateEvent` updates, and finally a terminal `TaskStatusUpdateEvent(final=true)`.
- Stream artifacts carry `artifact.metadata.shared.stream.block_type` with values `text` / `reasoning` / `tool_call`.
- Stream artifacts are scoped to logical output lanes rather than one shared catch-all artifact:
  - text chunks use a stable text artifact ID
  - reasoning chunks use a stable reasoning artifact ID
  - tool-call updates use a stable per-tool-part artifact ID when the upstream part is identifiable
- The shared stream-hints v1 contract only declares `block_type` and `sequence` under `artifact.metadata.shared.stream`.
- `artifact.metadata.shared.stream.sequence` carries the canonical per-request stream sequence.
- A final complete text snapshot is emitted only when streaming chunks did not already produce the same final text.
- That final complete text snapshot uses `append=false` on the text artifact so clients and the task store can treat it as the canonical replace-on-finish version rather than another fragment.
- Stream routing is schema-first: the service classifies chunks primarily by OpenCode `part.type` and `part_id` state rather than inline text markers.
- `message.part.delta` and `message.part.updated` are merged per `part_id`; out-of-order deltas are buffered and replayed when the corresponding `part.updated` arrives.
- Structured `tool` parts are emitted as `tool_call` blocks using structured v1 part payloads, while `text` and `reasoning` continue to use text parts.
- `tool_call` block payloads are normalized structured objects that may expose fields such as `call_id`, `tool`, `status`, `title`, `subtitle`, `input`, `output`, and `error`.
- If `application/json` is not accepted but `text/plain` is still accepted, those `tool_call` blocks are downgraded to stable compact JSON text so text-only clients retain the same observable state transitions.
- When a request restricts `acceptedOutputModes`, the stream applies the same output filtering before persistence so later task snapshots do not re-expose filtered structured blocks.
- Persistence is canonicalized separately from transport: stream subscribers still receive incremental artifact updates, while task-store persistence rewrites those updates into compact per-artifact snapshots so `GetTask` and terminal replay do not accumulate token-level fragments.
- The shared stream-hints v1 contract declares normalized usage fields `input_tokens`, `output_tokens`, and `total_tokens` at `metadata.shared.usage`.
- Progress metadata at `metadata.shared.progress` is emitted only when the client negotiated `urn:opencode-a2a:extension:stream-hints:v1`; baseline streams do not emit duplicate generic `working` status updates just to carry progress hints.
- Usage is extracted from documented info payloads and supported usage parts such as `step-finish`; non-usage parts with similar fields are ignored.
- Interrupt events (`permission.asked` / `question.asked`) are mapped to `TaskStatusUpdateEvent(final=false, state=input-required)` with details at `metadata.shared.interrupt` when the client negotiated `urn:opencode-a2a:extension:interactive-interrupt:v1`.
- Resolved interrupt events (`permission.replied` / `question.replied` / `question.rejected`) are emitted as `TaskStatusUpdateEvent(final=false, state=working)` with `metadata.shared.interrupt.phase=resolved` only when the same interactive interrupt extension is negotiated.
- Duplicate or unknown resolved events are suppressed unless the matching request is still pending.
- Non-streaming requests return a `Task` directly. When `configuration.returnImmediately=true`, the initial response is a working `Task` snapshot and completion continues in the background for later `GetTask` reads.
- For successful non-streaming `message:send` completions, `Task.artifacts` is the canonical carrier for the assistant result text.
- The terminal `Task.status.message` may carry a short completion status such as `Completed.`, but it does not duplicate the full result text.
- Non-streaming `message:send` responses may include normalized token usage at `Task.metadata.shared.usage` with the same field schema.

## Auth, Limits, and Failure Contract

- Requests require either `Authorization: Bearer <token>` or a configured `Authorization: Basic <base64(username:password)>`; otherwise `401` is returned. Agent Card endpoints are public.
- Requests above `A2A_MAX_REQUEST_BODY_BYTES` are rejected with HTTP `413` before transport handling.
- Inbound requests are rate limited with a sliding window keyed by `credential_id` when configured, otherwise by the authenticated principal; the public Agent Card surface is keyed by the direct peer IP (never `X-Forwarded-For`). Exceeding `A2A_RATE_LIMIT_MAX_REQUESTS` within `A2A_RATE_LIMIT_WINDOW_SECONDS` returns HTTP `429` with a `Retry-After` header. The limiter is process-local; multi-process deployments should place a shared gateway in front or rely on per-instance limits.
- Streaming responses (JSON-RPC `SendStreamingMessage` / `SubscribeToTask` and REST `/message:stream` / `/tasks/{id}:subscribe`) are bounded by `A2A_STREAM_MAX_BYTES`, `A2A_STREAM_MAX_DURATION_SECONDS`, and `A2A_STREAM_IDLE_TIMEOUT_SECONDS`; when a budget is exceeded the server ends the stream with an SSE `event: error` frame. If the very first event already exceeds the budget, REST requests are rejected with HTTP `429` `RESOURCE_EXHAUSTED` before the SSE stream starts.
- For validation failures, missing context (`task_id` / `context_id`), or internal errors, the service attempts to return standard A2A failure events via `event_queue`.
- Failure events include concrete error details with `failed` state.

## Directory Rules

- Clients can pass `metadata.opencode.directory`, but it must stay inside `${OPENCODE_WORKSPACE_ROOT}` or the service runtime root when no workspace root is configured.
- `OPENCODE_WORKSPACE_ROOT` is the service-level default workspace root used when clients do not request a narrower directory override.
- All paths are normalized with `realpath` to prevent `..` or symlink boundary bypass.
- If `A2A_ALLOW_DIRECTORY_OVERRIDE=false`, only the default directory is accepted.

## Wire Contract

The service publishes a machine-readable wire contract through Agent Card and OpenAPI metadata to describe the current runtime method boundary.

Use it to answer:

- which JSON-RPC methods are part of the current A2A core baseline
- which JSON-RPC methods are custom extensions
- which methods are deployment-conditional rather than currently active
- what error shape is returned for unsupported JSON-RPC methods

Current behavior:

- Core JSON-RPC methods are declared under `core.jsonrpc_methods`.
- Core HTTP endpoints are declared under `core.http_endpoints`.
- Extension JSON-RPC methods are declared under `extensions.jsonrpc_methods`.
- Deployment-conditional methods are declared under `extensions.conditionally_available_methods`.
- Shared metadata extension URIs such as session binding and streaming are listed under `extensions.extension_uris`.
- `all_jsonrpc_methods` is the runtime truth for the current deployment.
- The current SDK-owned core JSON-RPC surface includes `GetExtendedAgentCard` and `tasks/pushNotificationConfig/*`.
- The current SDK-owned REST surface also includes `GET /tasks` and the task push notification config routes.
- The SDK-owned core JSON-RPC method set follows the pinned `a2a-sdk` release and is locked by repository tests; review that surface deliberately when upgrading the SDK.
- Push notification config routes/methods are currently exposed only because they are part of the SDK-owned core surface. This runtime does not configure a push config store or push sender, so push notification operations remain unsupported. REST routes currently return HTTP `501`, while JSON-RPC methods surface SDK-owned unsupported error envelopes.

When `A2A_ENABLE_SESSION_SHELL=false`, `opencode.sessions.shell` is omitted from `all_jsonrpc_methods` and exposed only through `extensions.conditionally_available_methods`.

When `A2A_ENABLE_WORKSPACE_MUTATIONS=false`, `opencode.workspaces.create/remove` and `opencode.worktrees.create/remove/reset` are omitted from `all_jsonrpc_methods` and exposed only through `extensions.conditionally_available_methods`.

Unsupported method contract:

- JSON-RPC error code: `-32601`
- Error message: `Unsupported method: <method>`
- Error data fields:
  - `type=METHOD_NOT_SUPPORTED`
  - `method`
  - `supportedMethods`
  - `protocolVersion`

Consumer guidance:

- Discover custom JSON-RPC methods from Agent Card / OpenAPI before calling them.
- Treat `supportedMethods` in `error.data` as the runtime truth for the current deployment, especially when a deployment-conditional method is disabled.

## Protocol Version Negotiation

- The runtime accepts `A2A-Version` from either the HTTP header or the query parameter of A2A transport requests.
- If both are omitted, the runtime uses the fixed v1 protocol version `1.0`.
- Machine-readable discovery still declares `default_protocol_version=1.0` and `supported_protocol_versions=["1.0"]`, but those values are runtime constants rather than operator-configurable settings.
- Unsupported or invalid versions are rejected before request routing:
  - JSON-RPC returns a unified `VERSION_NOT_SUPPORTED` error envelope.
  - REST returns HTTP `400` with the same contract fields.
- Error shaping follows the v1 contract:
  - JSON-RPC keeps standard JSON-RPC error codes for standard failures and uses `google.rpc.ErrorInfo`-style `error.data[]` details for A2A-specific failures.
  - REST uses AIP-193 style `error.details[]`.
- The runtime does not normalize legacy `0.3` method aliases or payload shapes.

Current compatibility matrix:

| Area | `1.0` | Current note |
| --- | --- | --- |
| Version negotiation | Supported | The runtime accepts `A2A-Version` and routes requests before handler dispatch. |
| Agent Card / interface version discovery | Supported | Agent Card publishes v1 `supportedInterfaces` entries for HTTP+JSON and JSON-RPC. |
| Transport payloads and enums | Supported | Request/response payloads, enums, and schema details follow the current SDK-owned v1 baseline. |
| Error model | Supported | JSON-RPC and REST both use the v1 protocol-aware error shapes. |
| Pagination and list semantics | Supported | Cursor/list behavior follows the current SDK baseline. |
| Push notification surfaces | Unsupported | SDK-owned task push-notification routes are still exposed, but this runtime does not enable push sender/config-store support. REST routes return HTTP `501`, while JSON-RPC methods remain unsupported via SDK-owned error envelopes. |
| Signatures and authenticated data | Supported | Security schemes and authenticated extended card discovery follow the shipped SDK schema. |

## Compatibility Profile

The service also publishes a machine-readable compatibility profile through Agent Card and OpenAPI metadata.

Its purpose is to declare:

- the stable A2A core interoperability baseline
- which custom JSON-RPC methods are deployment extensions
- which extension surfaces are required runtime metadata contracts
- which methods are deployment-conditional rather than always available

Current profile shape:

- `profile_id=opencode-a2a-single-tenant-coding-v1`
- `default_protocol_version`
- `supported_protocol_versions`
- `protocol_compatibility`
  - `versions["1.0"].status=supported`
  - `versions[*].supported_features[]`
  - `versions[*].known_gaps[]`
- Deployment semantics are declared under `deployment`:
  - `id=single_tenant_shared_workspace`
  - `single_tenant=true`
  - `shared_workspace_across_consumers=true`
  - `tenant_isolation=none`
- Runtime features are declared under `runtime_features`:
  - `directory_binding.allow_override=true|false`
  - `directory_binding.scope=workspace_root_or_descendant|workspace_root_only`
  - `session_shell.enabled=true|false`
  - `session_shell.availability=enabled|disabled`
  - `execution_environment.sandbox.mode=unknown|read-only|workspace-write|danger-full-access|custom`
  - `execution_environment.sandbox.filesystem_scope=unknown|workspace_only|workspace_and_declared_roots|unrestricted|custom`
  - `execution_environment.network.access=unknown|disabled|enabled|restricted|custom`
  - `execution_environment.approval.policy=unknown|never|on-request|on-failure|untrusted|custom`
  - `execution_environment.approval.escalation_behavior=unknown|manual|automatic|unsupported|custom`
  - `execution_environment.write_access.scope=unknown|none|workspace_only|workspace_and_declared_roots|unrestricted|custom`
  - `execution_environment.write_access.outside_workspace=unknown|allowed|disallowed|custom`
  - `service_features.streaming.enabled=true`
  - `service_features.health_endpoint.enabled=true`
- Optional disclosure fields are emitted only when explicitly configured:
  - `execution_environment.sandbox.writable_roots`
  - `execution_environment.network.allowed_domains`
- Core methods and endpoints are declared under `core`.
- Extension retention policy is declared under `extension_retention`.
- Per-method retention and availability are declared under `method_retention`.
- Extension params and `/health` expose the same structured `profile` object; there is no separate legacy deployment-context shape.
- Execution-environment values are deployment declarations, not a per-turn runtime approval or sandbox result.

Retention guidance:

- Treat core A2A methods as the generic client interoperability baseline.
- Treat session binding, request-scoped model selection, and streaming metadata contracts as required for the current deployment model.
- Treat `a2a.interrupt.*` methods as shared extensions.
- Treat `opencode.sessions.*`, `opencode.providers.*`, and `opencode.models.*` as provider-private OpenCode extensions rather than portable A2A baseline capabilities.
- Treat `opencode.sessions.shell` as deployment-conditional and discover it from the declared profile and current wire contract before calling it.
- Treat `protocol_compatibility` as the runtime truth for which protocol line is fully supported versus only partially adapted.

Extension boundary principles:

- Expose OpenCode-specific capabilities through A2A only when they fit the adapter boundary: the adapter may document, validate, route, and normalize stable upstream-facing behavior, but it should not become a general replacement for upstream private runtime internals or host-level control planes.
- Default new `opencode.*` methods to provider-private extension status. Do not present them as portable A2A baseline capabilities unless they truly align with shared protocol semantics.
- Prefer read-only discovery, stable compatibility surfaces, and low-risk control methods before introducing stronger mutating or destructive operations.
- Map results to A2A core objects only when the upstream payload is a stable, low-ambiguity read projection such as session-to-`Task` or message-to-`Message`. Otherwise prefer provider-private summary/result envelopes.
- Treat upstream internal execution mechanisms, including subtask/subagent fan-out and task-tool internals, as provider-private runtime behavior. The adapter may expose passthrough compatibility and observable output metadata, but should not promote those internals into a first-class A2A orchestration API by default.
- For any new extension proposal, require an explicit answer to all of the following before implementation:
  - What client value is added beyond the existing chat/session flow?
  - Is the upstream behavior stable enough to document as a maintained contract?
  - Should the surface remain provider-private, deployment-conditional, or not be exposed at all?
  - Are authorization, workspace/session ownership, and destructive-side-effect boundaries clear enough to enforce?
  - Can the result shape be expressed without overfitting OpenCode internals into fake A2A core semantics?

## Multipart Input Example

Minimal JSON-RPC example with text + file input:

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-1",
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "msg-multipart-1",
        "role": "ROLE_USER",
        "parts": [
          {
            "text": "Please summarize this file."
          },
          {
            "url": "file:///workspace/report.pdf",
            "filename": "report.pdf",
            "mediaType": "application/pdf"
          }
        ]
      }
    }
  }'
```

Current input note:

- text parts and file/url/raw parts are supported.
- structured data-only input parts are not supported and are rejected with an explicit error.

## Extension Capability Overview

The README provides product positioning and quick start guidance. This guide focuses on how to consume the declared capabilities.

Important distinction:

- Agent Card extension declarations answer "what capability is available?"
- Runtime payload metadata answers "what happened on this request/stream?"
- Clients should not treat runtime metadata alone as a substitute for capability discovery when an extension URI is already declared.
- Treat the extension URI as the stable specification identifier.
- [`extension-specifications.md`](./extension-specifications.md) owns the stable URI catalog plus public-vs-extended disclosure policy.
- This guide owns runtime usage, request/response semantics, and client-facing examples.
- The authenticated extended card is the detailed deployment-specific contract view.
- Anonymous OpenAPI mirrors the public shared-discovery subset only; detailed provider-private extension contracts are intentionally authenticated-only.

## Shared Session Binding Contract

Stable specification URI:

- `urn:opencode-a2a:extension:session-binding:v1`

This section focuses on how clients should use the binding at runtime. For the stable URI record and public-vs-extended disclosure policy, see [`extension-specifications.md`](./extension-specifications.md).

To continue a historical OpenCode session, include this metadata key in each invoke request:

- `metadata.shared.session.id`: target upstream session ID

Server behavior:

- If provided, the request is sent to that exact OpenCode session.
- If omitted, a new session is created and cached by `(identity, contextId) -> session_id`.
- `contextId` remains the A2A conversation context key for task continuity; it is not a replacement for the upstream session identifier.
- OpenCode-private context such as `metadata.opencode.directory` may be supplied alongside `metadata.shared.session.id`, but it does not change the shared session-binding key.

Consumer guidance:

- Use this extension declaration to decide whether the server explicitly supports shared session rebinding.
- On the request path, write the upstream session identity to `metadata.shared.session.id`.
- On the response/query path, treat `metadata.shared.session` as runtime metadata negotiated by the same extension.

Minimal example:

```bash
curl -sS http://127.0.0.1:8000/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-continue-1",
      "role": "ROLE_USER",
      "parts": [{"text": "Continue the previous session and restate the key conclusion."}]
    },
    "metadata": {
      "shared": {
        "session": {
          "id": "<session_id>"
        }
      }
    }
  }'
```

## Shared Model Selection Contract

Stable specification URI:

- `urn:opencode-a2a:extension:model-selection:v1`

This section focuses on request-scoped usage. For the stable URI record and public-vs-extended disclosure policy, see [`extension-specifications.md`](./extension-specifications.md).

This extension declares that the main chat path accepts a request-scoped model override through shared metadata:

- `metadata.shared.model.providerID`
- `metadata.shared.model.modelID`

Runtime payload:

- The actual request carries the override under `metadata.shared.model`.

Behavior:

- The override is optional and scoped to one main chat request.
- Both `providerID` and `modelID` must be present together.
- When both fields are present, the service forwards them to the upstream OpenCode request as a model preference.
- When the fields are absent, the upstream OpenCode default behavior applies.

Consumer guidance:

- Use Agent Card discovery to confirm the shared model-selection contract is available before sending overrides.
- Treat `metadata.shared.model` as request-scoped preference data rather than deployment configuration.
- Provider auth and service-level model defaults belong to `opencode serve`, not to `opencode-a2a`.

Minimal example:

```bash
curl -sS http://127.0.0.1:8000/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-model-1",
      "role": "ROLE_USER",
      "parts": [{"text": "Explain the current branch status."}]
    },
    "metadata": {
      "shared": {
        "model": {
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
        }
      }
    }
  }'
```

## Shared Stream Hints Contract

Stable specification URI:

- `urn:opencode-a2a:extension:stream-hints:v1`

This section focuses on how clients should interpret runtime metadata. For the stable URI record and public-vs-extended disclosure policy, see [`extension-specifications.md`](./extension-specifications.md).

This extension declares that streaming and final task payloads use canonical shared metadata for block, progress, and usage hints.

Runtime payload:

- Request/stream payloads carry the hints under shared metadata fields.

Shared runtime fields:

- `metadata.shared.stream`
  - declared shared v1 fields: `block_type` and `sequence`
- `metadata.shared.usage`
  - declared shared v1 fields: `input_tokens`, `output_tokens`, and `total_tokens`
- clients must ignore undeclared fields when interpreting the shared v1 contract

Consumer guidance:

- Use the extension declaration to know the server emits canonical shared stream hints.
- Use runtime metadata to render block timelines, progress states, and token usage.
- Do not rely on undeclared fields under `metadata.shared.stream` or `metadata.shared.usage` as stable contract surface.
- Do not infer capability support only from seeing one runtime field on one response; rely on Agent Card discovery first when possible.

Minimal stream semantics summary:

- `text`, `reasoning`, and `tool_call` are emitted as canonical block types
- `text` and `reasoning` blocks use text parts, while `tool_call` uses structured v1 part payloads
- only `block_type` and `sequence` are part of the declared shared stream field map
- `sequence` is the per-request canonical stream sequence
- final task/status metadata may repeat declared usage totals

## OpenCode Session Management A2A Extension

This service exposes OpenCode session read, mutation, and control methods via A2A JSON-RPC extension methods (default endpoint: `POST /`). No extra custom REST endpoint is introduced.

Detailed contract discovery for this provider-private surface is intentionally authenticated-only. Public Agent Card and anonymous OpenAPI do not expand this method matrix.

- Trigger: call extension methods through A2A JSON-RPC
- Auth: same runtime auth as the main endpoint (`Bearer` or configured `Basic`)
- Privacy guard: when `A2A_LOG_PAYLOADS=true`, request/response bodies are still suppressed for `method=opencode.sessions.*`
- Endpoint discovery: prefer `supportedInterfaces[]` with `protocolBinding=JSONRPC` from Agent Card
- The runtime still delegates SDK-owned JSON-RPC methods such as `GetExtendedAgentCard` and `tasks/pushNotificationConfig/*` to the base A2A implementation; they are not OpenCode-specific extensions.
- Push notification config methods remain effectively unsupported in the current runtime because no push config store or push sender is configured; REST routes return HTTP `501`, while JSON-RPC methods stay on SDK-owned unsupported error handling.
- Notification behavior: for `opencode.sessions.*`, requests without `id` return HTTP `204 No Content`
- Result format:
  - `opencode.sessions.status` => provider-private status summaries in `result.items`
  - `opencode.sessions.list` / `opencode.sessions.children` => A2A `Task[]`
  - `opencode.sessions.get` => A2A `Task`
  - `opencode.sessions.todo` / `opencode.sessions.diff` => provider-private summaries in `result.items`
  - `opencode.sessions.messages.list` => adapter-normalized A2A `Message` projections
  - `opencode.sessions.messages.get` => adapter-normalized A2A `Message` projection
  - `opencode.sessions.fork` / `opencode.sessions.share` / `opencode.sessions.unshare` => provider-private session summary in `result.item`
  - `opencode.sessions.summarize` => provider-private completion result in `result.ok` plus `result.session_id`
  - `opencode.sessions.revert` / `opencode.sessions.unrevert` => provider-private session summary in `result.item`
  - limit pagination defaults to `20`; requests above `100` are rejected
  - `opencode.sessions.messages.list` also returns `result.next_cursor` when older messages are available
  - `contextId` is an A2A context key derived by the adapter (format: `ctx:opencode-session:<session_id>`, not raw OpenCode session ID)
  - OpenCode session identity is exposed explicitly at `metadata.shared.session.id`
  - session titles remain provider-private summary fields such as `result.item.title` / `result.items[].title`; they are not duplicated under `metadata.shared.session`
- Session list filters:
  - optional `directory`, `roots`, `start`, `search`, `limit`
  - optional `metadata.opencode.workspace.id`
  - nested `query` objects are not supported; pass filters at the top level only
  - `directory` is normalized through the same workspace-boundary rules used by other OpenCode directory overrides before reaching upstream
  - when `metadata.opencode.workspace.id` is present, the adapter routes by workspace and ignores `directory`
- Session message history filters:
  - optional `limit`, `before`
  - optional `metadata.opencode.workspace.id`
  - nested `query` objects are not supported; pass filters at the top level only
  - `before` is an opaque cursor for loading older messages and is only supported on `opencode.sessions.messages.list`
- Mutation methods:
  - `opencode.sessions.fork`
  - `opencode.sessions.share`
  - `opencode.sessions.unshare`
  - `opencode.sessions.summarize`
  - `opencode.sessions.revert`
  - `opencode.sessions.unrevert`
  - these methods reuse the same owner guard as other session control methods

### Session Status (`opencode.sessions.status`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 11,
    "method": "opencode.sessions.status",
    "params": {
      "directory": "services/api"
    }
  }'
```

### Session List (`opencode.sessions.list`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "opencode.sessions.list",
    "params": {
      "directory": "services/api",
      "roots": true,
      "search": "planner",
      "limit": 20
    }
  }'
```

### Session Messages (`opencode.sessions.messages.list`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "opencode.sessions.messages.list",
    "params": {
      "session_id": "<session_id>",
      "before": "<next_cursor_from_previous_page>",
      "limit": 50
    }
  }'
```

Message history responses include:

- `result.items`: adapter-normalized A2A `Message[]`
- `role`: canonical v1 enum values such as `ROLE_USER` / `ROLE_AGENT`
- `parts`: current projection is text-focused; text parts are aggregated into a single `Part(text=...)` rather than preserving arbitrary upstream part structure
- `result.next_cursor`: opaque cursor for the next older page, or `null` when no older page is available

### Session Get / Children / Todo / Diff / Message Get

- `opencode.sessions.get` => read one session and map it to A2A `Task`
- `opencode.sessions.children` => read child sessions and map them to A2A `Task[]`
- `opencode.sessions.todo` => read provider-private todo summaries
- `opencode.sessions.diff` => read provider-private diff summaries; optional `message_id`
- `opencode.sessions.messages.get` => read one message and map it to the same adapter-normalized A2A `Message` projection

Example (`opencode.sessions.messages.get`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 16,
    "method": "opencode.sessions.messages.get",
    "params": {
      "session_id": "<session_id>",
      "message_id": "<message_id>"
    }
  }'
```

### Session Prompt Async (`opencode.sessions.prompt_async`)

Topology note:

- `A2A Task` remains the protocol-level execution object exposed by the adapter.
- `opencode.sessions.prompt_async` is a provider-private extension method, not part of the A2A core baseline.
- `request.parts[].type=subtask` is an upstream-compatible OpenCode input shape carried through that extension method.
- Downstream execution may fan out into upstream OpenCode task-tool / subagent runtime behavior, but that internal orchestration remains provider-private.
- The adapter documents passthrough compatibility and observable `tool_call` output blocks; it does not promote subtask/subagent execution into a first-class A2A orchestration API.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 21,
    "method": "opencode.sessions.prompt_async",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "parts": [{"type": "text", "text": "Continue and summarize next steps."}],
        "noReply": true,
        "model": {
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
        }
      },
      "metadata": {
        "opencode": {
          "directory": "/path/inside/workspace"
        }
      }
    }
  }'
```

Response:

- success => `{"ok": true, "session_id": "<session_id>"}` (JSON-RPC result)
- notification (no `id`) => HTTP `204 No Content`
- error types:
  - `SESSION_NOT_FOUND`
  - `SESSION_FORBIDDEN`
  - `METHOD_DISABLED` (not applicable to prompt_async)
  - `UPSTREAM_UNREACHABLE`
  - `UPSTREAM_HTTP_ERROR`
  - `UPSTREAM_PAYLOAD_ERROR`

Validation notes:

- `metadata.opencode.directory` follows the same normalization and boundary rules as message send (`realpath` + workspace boundary check).
- `metadata.opencode.workspace.id` is a provider-private routing hint. When it is present, the adapter routes the request to that workspace and does not apply directory override resolution for the same call.
- `request.model` uses the same shape as `metadata.shared.model` and is scoped only to the current session-control request.
- `request.parts[]` currently accepts upstream-compatible provider-private part types `text`, `file`, `agent`, and `subtask`.
- `subtask` parts require `prompt`, `description`, and `agent`; they may also include optional `model` and `command`.
- For `subtask` parts, `request.parts[].agent` is the upstream subagent selector. `opencode-a2a` validates and forwards the shape but does not define a separate subagent discovery or orchestration API.
- Control methods enforce session owner guard based on request identity.
- `opencode.sessions.shell` additionally requires the `session_shell` capability, which may be granted to any explicitly configured credential under `A2A_STATIC_AUTH_CREDENTIALS`.

Example (`opencode.sessions.prompt_async` with a provider-private `subtask` part):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 211,
    "method": "opencode.sessions.prompt_async",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "parts": [
          {
            "type": "subtask",
            "prompt": "Inspect the auth middleware and list the highest-risk gaps.",
            "description": "Security-focused pass over request auth flow",
            "agent": "explore",
            "command": "review"
          }
        ]
      }
    }
  }'
```

### Session Command (`opencode.sessions.command`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 22,
    "method": "opencode.sessions.command",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "command": "/review",
        "arguments": "focus on security findings",
        "model": {
          "providerID": "google",
          "modelID": "gemini-2.5-flash"
        }
      },
      "metadata": {
        "opencode": {
          "directory": "/path/inside/workspace"
        }
      }
    }
  }'
```

Response:

- success => `{"item": <A2A Message>}` (JSON-RPC result)
- notification (no `id`) => HTTP `204 No Content`

### Session Fork / Share / Unshare

These methods return provider-private session summaries in `result.item`.

Example (`opencode.sessions.fork`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 221,
    "method": "opencode.sessions.fork",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "messageID": "<message_id>"
      }
    }
  }'
```

### Session Summarize / Revert / Unrevert

- `opencode.sessions.summarize` returns `{"ok": true, "session_id": "<session_id>"}`
- `opencode.sessions.revert` / `opencode.sessions.unrevert` return provider-private session summaries in `result.item`
- `opencode.sessions.revert` requires `request.messageID`; `request.partID` is optional

Example (`opencode.sessions.summarize`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 224,
    "method": "opencode.sessions.summarize",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "providerID": "openai",
        "modelID": "gpt-5",
        "auto": true
      }
    }
  }'
```

Example (`opencode.sessions.revert`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 225,
    "method": "opencode.sessions.revert",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "messageID": "<message_id>",
        "partID": "<part_id>"
      }
    }
  }'
```

Example (`opencode.sessions.share`):

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 222,
    "method": "opencode.sessions.share",
    "params": {
      "session_id": "<session_id>"
    }
  }'
```

### Session Shell (`opencode.sessions.shell`)

`opencode.sessions.shell` is disabled by default. Enable with `A2A_ENABLE_SESSION_SHELL=true`.

Security warning:

- This is a high-risk method because it can execute shell commands in the workspace context.
- Enable only for trusted operators/internal scenarios.
- Keep bearer-token rotation, owner/directory guard checks, and audit log monitoring enabled before turning it on.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 23,
    "method": "opencode.sessions.shell",
    "params": {
      "session_id": "<session_id>",
      "request": {
        "agent": "code-reviewer",
        "command": "git status --short"
      }
    }
  }'
```

Response:

- success => `{"item": <A2A Message>}` (JSON-RPC result)
- disabled => JSON-RPC error `METHOD_DISABLED`
- notification (no `id`) => HTTP `204 No Content`

### Provider List (`opencode.providers.list`)

Returns normalized provider summaries from the upstream OpenCode provider catalog.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 24,
    "method": "opencode.providers.list",
    "params": {}
  }'
```

Response:

- success => `{"items": [...], "default_by_provider": {...}, "connected": [...]}` (JSON-RPC result)
- optional `metadata.opencode.workspace.id` routes discovery against a specific OpenCode workspace; otherwise the adapter falls back to directory routing when `metadata.opencode.directory` is provided

### Model List (`opencode.models.list`)

Returns normalized, flattened model summaries. Supports optional provider filter:

- `params.provider_id`

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 25,
    "method": "opencode.models.list",
    "params": {
      "provider_id": "openai"
    }
  }'
```

Response:

- success => `{"items": [...], "default_by_provider": {...}, "connected": [...]}` (JSON-RPC result)

## Workspace Control (Provider-Private Extension)

The runtime exposes OpenCode project/workspace/worktree discovery through provider-private JSON-RPC methods:

- `opencode.projects.list`
- `opencode.projects.current`
- `opencode.workspaces.list`
- `opencode.worktrees.list`

Deployment-conditional mutation methods remain available for trusted operator scenarios, but they are disabled by default. Enable them with `A2A_ENABLE_WORKSPACE_MUTATIONS=true`:

- `opencode.workspaces.create`
- `opencode.workspaces.remove`
- `opencode.worktrees.create`
- `opencode.worktrees.remove`
- `opencode.worktrees.reset`

Behavior notes:

- These methods target the active OpenCode deployment project. They are not routed through per-request workspace forwarding.
- `metadata.opencode.workspace.id` is declared consistently across the adapter, but current workspace-control methods do not use it to change the target project.
- `opencode.workspaces.*` and `opencode.worktrees.*` currently wrap upstream `/experimental/workspace` and `/experimental/worktree` endpoints; treat them as experimental-upstream surfaces even when declared by the adapter.
- Mutating methods should be treated as operator-only control-plane actions and are disabled by default.
- Discovery responses are normalized provider-private summaries: upstream entries are filtered to stable fields only (`id`/`name`/`vcs` for projects, `id`/`type`/`name`/`branch` for workspaces, `name`/`branch` for worktrees) and never include upstream local paths (`directory`, `canonical`, worktree paths), raw entries, or credential-like fields.

### Project Discovery (`opencode.projects.list`, `opencode.projects.current`)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 31,
    "method": "opencode.projects.current",
    "params": {}
  }'
```

Response:

- `opencode.projects.list` => `{"items": [{"id": "<id>", "name": "<name>", "vcs": "<vcs>"}]}` (normalized summaries; no local paths)
- `opencode.projects.current` => `{"item": {"id": "<id>", "name": "<name>", "vcs": "<vcs>"}}` (normalized summary; no local paths)

### Workspace Discovery

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 32,
    "method": "opencode.workspaces.list",
    "params": {}
  }'
```

Response:

- `opencode.workspaces.list` => `{"items": [{"id": "<id>", "type": "<type>", "name": "<name>", "branch": "<branch>"}]}` (normalized summaries; no local paths)

### Workspace Mutation

`opencode.workspaces.create` and `opencode.workspaces.remove` are disabled by default. Enable with `A2A_ENABLE_WORKSPACE_MUTATIONS=true`.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 32,
    "method": "opencode.workspaces.create",
    "params": {
      "request": {
        "id": "wrk-api",
        "type": "git",
        "branch": "main"
      }
    }
  }'
```

Response:

- `opencode.workspaces.create` => `{"item": {...}}`
- `opencode.workspaces.remove` => `{"item": {...}}`

### Worktree Discovery

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 33,
    "method": "opencode.worktrees.list",
    "params": {}
  }'
```

Response:

- `opencode.worktrees.list` => `{"items": [...]}`

### Worktree Mutation

`opencode.worktrees.create`, `opencode.worktrees.remove`, and `opencode.worktrees.reset` are disabled by default. Enable with `A2A_ENABLE_WORKSPACE_MUTATIONS=true`.

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 33,
    "method": "opencode.worktrees.reset",
    "params": {
      "request": {
        "directory": "/repo/services/api"
      }
    }
  }'
```

Response:

- `opencode.worktrees.create` => `{"item": {...}}`
- `opencode.worktrees.remove` => `{"ok": true|false}`
- `opencode.worktrees.reset` => `{"ok": true|false}`

## Interrupt Recovery (Provider-Private Extension)

The runtime also exposes provider-private recovery queries for pending interactive interrupts:

- `opencode.permissions.list`
- `opencode.questions.list`

These methods return recovery views over the local interrupt binding registry. They do not replace the shared `a2a.interrupt.*` callback methods.

Response shape:

- success => `{"items": [{"request_id", "session_id", "interrupt_type", "task_id", "context_id", "details", "expires_at"}]}` (JSON-RPC result)

Notes:

- Recovery results are scoped to the current authenticated caller identity when the runtime can resolve one.
- If the runtime cannot resolve a caller identity for the current request, recovery queries return an empty item list.
- The runtime stores normalized interrupt `details` alongside request bindings, so recovery results match the shape emitted in `metadata.shared.interrupt.details`.
- The first implementation stage reads from the local interrupt registry rather than proxying upstream global `/permission` or `/question` pending lists.
- Use recovery queries to rediscover pending requests after reconnecting; use `a2a.interrupt.*` methods to resolve them.

## Shared Interrupt Callback (A2A Extension)

When the shared interactive interrupt extension is negotiated, runtime status updates may report an interrupt request at `metadata.shared.interrupt`, and clients can reply through JSON-RPC extension methods:

- `a2a.interrupt.permission.reply`
  - required: `request_id`
  - required: `reply` (`once` / `always` / `reject`)
  - optional: `message`
  - optional: `metadata.opencode.directory`
- `a2a.interrupt.question.reply`
  - required: `request_id`
  - required: `answers` (`Array<Array<string>>`)
  - optional: `metadata.opencode.directory`
- `a2a.interrupt.question.reject`
  - required: `request_id`
  - optional: `metadata.opencode.directory`

Notes:

- `request_id` must be a live interrupt request observed from negotiated runtime metadata (`metadata.shared.interrupt.request_id`) or rediscovered through `opencode.permissions.list` / `opencode.questions.list`.
- The server keeps an interrupt binding registry; callbacks with unknown or expired `request_id` are rejected.
- The cache retention windows are controlled by `A2A_INTERRUPT_REQUEST_TTL_SECONDS` (default: `10800` seconds / `180` minutes) and `A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS` (default: `600` seconds / `10` minutes). After the active TTL elapses, the server keeps a short-lived tombstone so repeated replies continue to return `INTERRUPT_REQUEST_EXPIRED` before eventually aging out to `INTERRUPT_REQUEST_NOT_FOUND`.
- These values are deployment/runtime settings and are intentionally not part of the shared extension method contract.
- Callback requests are validated against interrupt type and caller identity.
- Callback context variables use the shared method contract plus OpenCode-private metadata when needed (`params.metadata.opencode.directory`).
- Successful callback responses are minimal: only `ok` and `request_id`.
- Error types:
  - `INTERRUPT_REQUEST_NOT_FOUND`
  - `INTERRUPT_REQUEST_EXPIRED`
  - `INTERRUPT_TYPE_MISMATCH`
  - `UPSTREAM_UNREACHABLE`
  - `UPSTREAM_HTTP_ERROR`

Permission reply example:

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "a2a.interrupt.permission.reply",
    "params": {
      "request_id": "<request_id>",
      "reply": "once",
      "metadata": {
        "opencode": {
          "directory": "/path/inside/workspace"
        }
      }
    }
  }'
```

## Authentication Example (curl)

```bash
curl -sS http://127.0.0.1:8000/message:send \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "parts": [{"text": "Explain what this repository does."}]
    }
  }'
```

## JSON-RPC Send Example (curl)

```bash
curl -sS http://127.0.0.1:8000/ \
  -H 'content-type: application/json' \
  -H 'Authorization: Bearer <your-token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": 101,
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "msg-1",
        "role": "ROLE_USER",
        "parts": [{"text": "Explain what this repository does."}]
      }
    }
  }'
```

## Streaming Re-Subscription (`SubscribeToTask`)

If an SSE connection drops, use `GET /tasks/{task_id}:subscribe` to re-subscribe while the task is still non-terminal.

## Cancellation Semantics (`CancelTask`)

- The service first marks the A2A task as `canceled` and keeps cancel requests responsive.
- For running tasks, the service attempts upstream OpenCode `POST /session/{sessionID}/abort` to stop generation.
- Upstream interruption is best-effort: if upstream returns 404, network errors, or other HTTP errors, A2A cancellation still completes with `TaskState.TASK_STATE_CANCELED`.
- Idempotency contract: repeated `CancelTask` on an already `canceled` task returns the current terminal task state without error.
- Terminal subscribe contract: calling `SubscribeToTask` or `GET /tasks/{task_id}:subscribe` on a terminal task replays one terminal `Task` snapshot and then closes the stream.
- Terminal persistence contract: once a terminal task snapshot is persisted, this service treats it as immutable. Producers must emit final text and artifact updates before the terminal event, and any final usage or stream metadata must be attached to that terminal event itself. Late terminal-state mutations are rejected by the task-store write policy.
- These two semantics are also declared as machine-readable `service_behaviors` in the compatibility profile and wire contract extensions.
- At `A2A_LOG_LEVEL=DEBUG`, the service emits lightweight metric log records (`logger=opencode_a2a.execution.executor`):
  - `a2a_stream_requests_total`
  - `a2a_stream_active` (`value=1` when a stream starts, `value=-1` when it closes)
  - `opencode_stream_retries_total`
  - `tool_call_chunks_emitted_total`
  - `interrupt_requests_total`
  - `interrupt_resolved_total`
- The cancel path also emits:
  - `a2a_cancel_requests_total`
  - `a2a_cancel_abort_attempt_total`
  - `a2a_cancel_abort_success_total`
  - `a2a_cancel_abort_timeout_total`
  - `a2a_cancel_abort_error_total`
  - `a2a_cancel_duration_ms` (with `abort_outcome` label)
