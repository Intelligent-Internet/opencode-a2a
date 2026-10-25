# Compatibility Guide

This document defines the compatibility promises `opencode-a2a` currently upholds for A2A consumers, operators, and maintainers.

## Runtime Support

- Python versions: 3.11, 3.12, 3.13, 3.14
- A2A Python SDK: `a2a-sdk==1.2.3` (SDK package version; not an OpenCode API version)
- Supported A2A protocol line: `1.0`
- OpenCode runtime line: `1.18.x` (verified with `1.18.19`)

OpenCode V2 is an opt-in, experimental session-transport slice selected with
`OPENCODE_API_VERSION=v2`. It implements `/api/info`, `/api/location`, and
session create/get/list transport only; A2A prompt execution and all
OpenCode-specific JSON-RPC extensions remain unavailable. This remains an
explicit safety gate, not a claim that V2 prompt execution works.

The V2 selector is **OpenCode's upstream HTTP API version**, not "A2A SDK V2".
The runtime continues to expose A2A protocol 1.0 interfaces. Its V2 Agent Card
contains no extension declarations and retains only an informational execution
status skill. The A2A Python SDK card contract used here requires populated
default input/output mode fields and a skill entry; those required card fields
are not a claim that V2 accepts A2A messages. `SendMessage` enters the SDK task
lifecycle and returns a failed task with the fail-closed explanation before
session creation or prompt submission. `SendStreamingMessage` is rejected as
unsupported because the card declares `streaming=false`. Neither path makes an
upstream HTTP request. Direct
`opencode.*` JSON-RPC calls return `-32601 Method not found` without an upstream
HTTP request. The same extension hiding applies to shared interrupt callbacks.

| Capability family | OpenCode API V1 (`1.18.x`, verified `1.18.19`) | OpenCode API V2 (`2.0.28` session-transport exercise only) |
| --- | --- | --- |
| A2A protocol interfaces | HTTP+JSON and JSON-RPC, protocol `1.0`; current core behavior | Interfaces remain protocol `1.0`; message send/stream fails before upstream HTTP |
| Shared session/model/stream/interrupt extensions | Declared and implemented as documented | Omitted from cards/OpenAPI; direct calls are unsupported |
| `opencode.sessions.*` (17 catalog methods) | Supported; `opencode.sessions.shell` is deployment-conditional | Omitted and rejected before upstream HTTP |
| Provider/model discovery (2 methods) | Supported | Omitted and rejected before upstream HTTP |
| Project/workspace/worktree (9 methods) | Project discovery stable; workspace/worktree discovery experimental; five mutations deployment-conditional | Omitted and rejected before upstream HTTP |
| Interrupt recovery (2 methods) | Adapter-local and caller-scoped | Omitted and rejected before upstream HTTP |
| `a2a.interrupt.*` callbacks (3 methods) | Supported through the shared interrupt extension | Omitted and rejected before upstream HTTP |
| Internal V2 session transport | Not applicable | `/api/info`, `/api/location`, session create/get/list only; exercised against `2.0.28`, not a model execution test |

V2 support does **not** claim A2A execution, task-scoped cancellation,
permission/form interventions, request-scoped model overrides, structured tool
execution, or outbound `a2a_call` support. `2.0.28` evidence is limited to
session transport; no V2 model-backed acceptance run is claimed.

V1 stays the default, and the adapter does not auto-detect or fall back between
versions. The V2 session transport was exercised against OpenCode `2.0.28`; that
experimental check does not add V2 to the supported runtime line above.
OpenCode V2 allows `external_directory` by default. Before any future release of
V2 prompt execution, operators must configure an explicit restrictive upstream
V2 `permissions` policy. The current session-only slice does not perform model
execution.

This is the single canonical place where the supported upstream OpenCode version line is declared. Keep it updated together with the live integration smoke check (`./scripts/live_opencode_smoke.sh`) and do not duplicate the version line elsewhere.

The SDK-owned core JSON-RPC method set follows the supported SDK line and is locked by repository tests so SDK upgrades trigger an explicit compatibility review.

### SDK and Adapter Review

The adapter's reviewed SDK integration boundaries are:

| Boundary | Decision and regression evidence |
| --- | --- |
| REST success media type | Adopted: successful HTTP+JSON responses use `application/a2a+json`, including adapter-owned `/message:send` and `/tasks` routes. REST error envelopes remain AIP-193 JSON. Transport and OpenAPI tests lock both runtime headers and documented response media types. |
| JSON-RPC and REST request parsing | Adopted: unknown request fields are ignored for forward compatibility, matching SDK parsing behavior. Shared middleware rejects cross-transport envelopes plus legacy `content`, lowercase role, and `kind`/`type`/`file` part shapes before either transport dispatches. |
| Terminal task follow-up | Adopted: SDK now raises `UnsupportedOperationError` with the named terminal `TaskState`; handler regressions lock this behavior for both memory and database stores. |
| Versioned database task lifecycle | Adopted: database mode uses `VersionedDatabaseTaskStore` with SDK optimistic-concurrency control and `DatabaseTaskEventStream` for persisted event replay. The adapter no longer carries `GuardedTaskStore`, `DatabaseTaskStoreCompat`, or a private SQL terminal-write policy. |
| Legacy stored task conversion | Retained only at the SDK migration boundary for SDK-owned persisted rows. This does not restore legacy A2A 0.3 wire support. |
| Request-handler lifecycle | Adopted: `OpencodeRequestHandler` extends SDK `DefaultRequestHandlerV2` and uses the public `RequestContextBuilder` extension point for output-mode persistence. Active-task execution, queueing, cancellation, subscription, direct-Message completion, and shutdown draining stay in the SDK lifecycle. |
| Outbound header forwarding | Uses the SDK `service_parameters` channel. Client transport tests check the final HTTP request, including authentication and tracing headers. |

The missing-import typing overrides for `google.protobuf.*` and `jsonrpc.*` remain limited to those third-party modules. Runtime support is validated by the SDK and transport regressions, not inferred from the typing overrides.

### Intentional SDK Private Seams

Core REST routes, task persistence, and the request-handler lifecycle now use SDK public interfaces. The remaining seams are explicit and regression-bounded:

| Seam | Why it remains |
| --- | --- |
| JSON-RPC dispatcher overrides | `opencode.*` extension methods and repository error redaction require explicit method registration and error adaptation. Core method schemas and response helpers remain SDK-owned. |

The repository still wraps public request-handler streams to enforce byte, duration, and idle budgets, but it no longer subclasses `RestDispatcher` or calls `_handle_streaming`. Shape and transport checks run in middleware before dispatch. Every SDK upgrade must rerun shape/migration tests, handler lifecycle tests, transport media-type tests, and conformance probes.

## Contract Honesty

Machine-readable discovery surfaces must reflect actual runtime behavior:

- public Agent Card
- authenticated extended card
- OpenAPI metadata
- JSON-RPC wire contract
- compatibility profile

If runtime support is not implemented, do not publish it as a supported machine-readable capability.

Consumer guidance:

- Discover the complete SDK-owned v1 core method set from `core.jsonrpc_methods`; do not infer it from a shortened documentation list.
- Treat `urn:opencode-a2a:extension:...` entries in this repository as repository-governed extension identifiers, not as a claim that they are part of the A2A core baseline.
- Treat `opencode.*` methods and `metadata.opencode.*` fields as provider-private OpenCode control and discovery surfaces layered on top of the portable A2A baseline.
- Treat [extension-specifications.md](./extension-specifications.md) as the stable URI/spec index, not as the main usage guide.

## Normative Sources

When docs or reference material disagree, treat these as normative in this order:

- runtime behavior validated by tests
- machine-readable discovery output such as Agent Card, authenticated extended card, and OpenAPI metadata
- repository-owned docs in `README.md`, `docs/`, and `CONTRIBUTING.md`

The repository-owned black-box compatibility probes protect selected runtime invariants but are not a complete conformance suite. Third-party TCK output is investigation input only and does not override the repository's declared contract by itself.

## Compatibility-Sensitive Surface

This repository ships as a beta project. Its documented public contracts are intended to remain stable, while implementation details and explicitly provider-private surfaces may continue to evolve. These declared surfaces should not drift silently:

- core A2A send / stream / task methods
- v1-only request/response payloads and enum values
- v1 protocol-aware JSON-RPC and REST error shaping
- shared session-binding metadata
- shared model-selection metadata
- shared streaming metadata
- declared custom JSON-RPC extension methods
- authenticated extended card and OpenAPI wire-contract metadata

Changes to those surfaces should be treated as compatibility-sensitive and should include corresponding test updates.
- provider-private session query params use one top-level request shape; the repository does not promise parallel nested `query.*` aliases for the same filters.

Service-level behavior layered on top of those core methods should also be declared explicitly when interoperability depends on it. Current examples:

- `SubscribeToTask` rejection with `UnsupportedOperationError` for terminal tasks
- SDK versioned task persistence with compare-and-swap conflict detection
- task-scoped `acceptedOutputModes` negotiation persistence across send / stream / get / subscribe
- request-body rejection behavior for oversized transport payloads

## Deployment Profile

The current service profile is intentionally:

- single-tenant
- shared-workspace
- adapter boundary around one OpenCode deployment

One deployed instance should be treated as a single-tenant trust boundary, not as a secure multi-tenant runtime boundary.

Execution-environment boundary fields published through the runtime profile are declarative deployment metadata. They are not promises that every host-side approval, sandbox escalation, or filesystem change will be reflected live per request.

## Persistence Compatibility

Task durability is deployment-dependent:

- `A2A_TASK_STORE_BACKEND=database` preserves SDK task rows plus adapter-managed session and interrupt state across restarts
- `A2A_TASK_STORE_BACKEND=memory` keeps the service in an ephemeral development profile

Task-store behavior that should remain stable for clients:

- concurrent task mutations are serialized by the SDK versioned-store compare-and-swap contract; stale writers receive `ConcurrentTaskModificationError`
- the v2 request handler rejects follow-up messages to terminal tasks and does not persist mutations after their terminal event
- task-store I/O failures are surfaced as stable service errors instead of leaking backend-specific exceptions
- accepted output-mode negotiation for a task is persisted with the task so later reads keep the same filtered output contract
- adapter-managed migrations only own adapter state tables; SDK-managed task schema remains SDK-owned

The supported persistence profile is one application process using its own local SQLite database. Multiple Uvicorn workers or application replicas must not share that SQLite file. PostgreSQL and other SQLAlchemy dialects are not supported deployment targets; any apparent dialect compatibility is implementation latitude rather than a public promise.

The SDK task-store migration adds `task_versions` and `task_events`. This runtime adopts those stores even in its supported single-process profile so task writes and event replay follow the SDK lifecycle; the migration is additive and does not change existing `tasks` rows.

## Extension Stability

- Shared metadata and extension contracts should stay synchronized across Agent Card, OpenAPI, and runtime behavior.
- Public Agent Card should stay intentionally minimal. Detailed extension params belong in the authenticated extended card, while anonymous OpenAPI should mirror only the shared public disclosure subset.
- Deployment-conditional methods must be declared as conditional rather than silently disappearing.
- `opencode.sessions.prompt_async` input-part passthrough is compatibility-sensitive. Changes to supported part types, passthrough field semantics, or rejection behavior should be treated as wire-level changes.
- `opencode.sessions.shell` is compatibility-sensitive as a deployment-conditional shell snapshot surface. It should not silently widen into a general interactive shell API.
- `opencode.workspaces.*` and `opencode.worktrees.*` are boundary-sensitive and should remain explicitly provider-private, operator-scoped, and deployment-conditional where applicable. Discovery responses are normalized summaries that exclude upstream local paths (`directory`, `canonical`, worktree paths) and raw/internal fields.
- Interrupt callback and recovery methods are compatibility-sensitive because clients may depend on request ID lifecycle, expiry semantics, and identity scoping.
- Agent Card media modes and `acceptedOutputModes` handling are compatibility-sensitive. Changes to declared chat modes, to task-scoped negotiation persistence, or to output filtering of structured tool payloads should be treated as wire-level changes.
- Agent Card and OpenAPI publication of `protocol_compatibility`, `service_behaviors`, and runtime feature toggles is compatibility-sensitive discoverability surface.

## Extension Boundary Governance

When evaluating or evolving `opencode.*` methods, this repository uses the following rules:

- The adapter may document, validate, route, and normalize stable upstream-facing behavior, but it should not grow into a general replacement for upstream private runtime internals or host-level control planes.
- New `opencode.*` methods default to provider-private extension status.
- Read-only discovery, compatibility-preserving projections, and low-risk control methods are preferred over stronger mutating or destructive provider controls.
- A2A core object mappings should be used only for stable, low-ambiguity read projections.
- Subtask/subagent fan-out, task-tool internals, and similar upstream execution mechanisms should stay framed as upstream runtime behavior even when passthrough compatibility exists.

Each new extension proposal should answer:

- what client value exists beyond the current chat/session flow?
- is the upstream behavior stable enough to carry as a maintained contract?
- should the surface be provider-private, deployment-conditional, or excluded?
- are authorization and destructive-side-effect boundaries enforceable?
- can the result shape avoid overfitting OpenCode internals into fake A2A core semantics?

## Extension Taxonomy

This repository distinguishes between three layers:

- core A2A surface
  - standard send / stream / task methods
- shared extensions
  - repo-family conventions such as session binding, model selection, stream hints, and interrupt callbacks
- OpenCode-specific extensions
  - `opencode.*` JSON-RPC methods plus `metadata.opencode.*`

Important note:

- `urn:opencode-a2a:extension:...` extension URIs used here are repository-governed, versioned permanent identifiers.
- They are not a claim that those extensions are part of the A2A core baseline.
- `opencode.*` methods are intentionally product-specific. They improve OpenCode-aware workflows but should not be assumed to transfer unchanged to unrelated A2A agents.

## Non-Goals

This repository does not currently promise:

- hard multi-tenant isolation inside one instance
- generic provider-auth orchestration on behalf of OpenCode
- compatibility with legacy A2A `0.3` method aliases or payload shapes

Those areas may evolve later, but they should not be implied by current machine-readable discovery output.
