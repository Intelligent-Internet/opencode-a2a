# Compatibility Guide

This document defines the compatibility promises `opencode-a2a` currently upholds for A2A consumers, operators, and maintainers.

## Runtime Support

- Python versions: 3.11, 3.12, 3.13, 3.14
- A2A Python SDK: `a2a-sdk==1.2.3` (SDK package version; not an OpenCode API version)
- Supported A2A protocol line: `1.0`
- OpenCode runtime line: `1.18.x` (verified with `1.18.19`)
- Experimental exclusive execution profile: OpenCode `2.0.28` only; see the
  [deployment contract](./v2-exclusive-execution.md).

By default, OpenCode V2 is an opt-in, experimental session-transport slice selected with
`OPENCODE_API_VERSION=v2`. It implements `/api/info`, `/api/location`, and
session create/get/list transport only; A2A prompt execution and all
OpenCode-specific JSON-RPC extensions remain unavailable. This remains an
explicit safety gate, not a claim that V2 prompt execution works.

The additional selector `OPENCODE_V2_EXECUTION=exclusive` enables a separately
verified restricted profile: dedicated private upstream, gateway-created
single-flight sessions, text execution, and streaming read/question
interventions. It does not remove the default gate or solve general shared-session
correlation. The following legacy/default-V2 discussion describes selection
**without** that explicit profile; its earlier general correlation NO-GO remains
valid. See the scoped acceptance matrix below.

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
session transport plus an upstream-only correlation probe; no V2 model-backed
A2A acceptance run is claimed. Parent epic
#1 and the original unrestricted #4/#5 acceptance criteria remain open. The
exclusive profile's narrower evidence does not silently close those criteria.

### Exclusive-profile acceptance matrix

Evidence is recorded in [the sanitized acceptance record](./evidence/v2-exclusive-acceptance.json).
The upstream stack's separate [regression validation record](./evidence/v2-exclusive-upstream-validation.md)
distinguishes its doctor and session-only checks from the author's model-backed runs.
`PASS` applies only to the described tested subset; `NOT VERIFIED` is not a
support claim. V1 remains the stable default. Upstream review/merge and the
epic's final scope decision are separate from these execution results.

| Capability | V1 | V2 exclusive mode | Evidence / limitation |
| --- | --- | --- | --- |
| Session creation | PASS | PASS | Real pinned servers; V2 session-only smoke and model admission |
| A2A streaming | PASS | PASS | Real V1 JSON-RPC; real V2 REST and JSON-RPC, assistant artifacts only |
| A2A non-streaming | PASS | PASS | Real model, terminal state and final artifacts rather than admission |
| Follow-up | PASS | PASS | Remembered nonce; V2 requires a healthy live gateway session |
| Concurrent sessions | NOT VERIFIED | PASS | Separate V2 contexts run without output mixing |
| Shared-session concurrency | NOT VERIFIED | UNSUPPORTED | V1 risk #8 remains; V2 serializes gateway requests and excludes other writers |
| Cancellation | PASS | PASS | V1 durable canceled state; V2 observed active interruption and local queued cancellation |
| Permissions | PASS | PASS | V1 bash allow/reject; V2 read once/reject only, no blanket or persistent grant |
| Forms / questions | PASS | PASS | V1 question reply; V2 question string/multiselect and invalid/stale/cross-owner rejection |
| Request-scoped model selection | NOT VERIFIED | UNSUPPORTED | V2 backend model configured explicitly; overrides rejected before upstream calls |
| `a2a_call` | NOT VERIFIED | UNSUPPORTED | V2 tool policy and discovery exclude it |
| Disconnect recovery | NOT VERIFIED | UNSUPPORTED | V2 gap/terminal loss yields failure and quarantine, not replay or successful recovery |
| Lost-event / ambiguous-admission safety | NOT VERIFIED | PASS | Real upstream plus transport fault injection; one admission, no false success or resend |
| Unary interventions / rich forms | NOT VERIFIED | UNSUPPORTED | Explicit rejection/quarantine; only streaming supported question fields are exposed |
| Reasoning blocks | NOT VERIFIED | NOT VERIFIED | Deterministic native mapping tests pass; accepted live run observed text/tool blocks only |

V1 cancellation remains an A2A terminal-state contract with best-effort upstream
abort; it is not a rollback or exactly-once side-effect guarantee. V2 native
pending-inbox deletion is not independently advertised as successful cancellation:
without terminal proof it fails uncertain and retains quarantine. Restart,
retired-context continuation, and transparent upstream feed reconnection are not
supported by the exclusive profile. See [reproduction commands and detailed
preconditions](./v2-exclusive-execution.md#reproduce-the-live-evidence).

The pinned OpenCode `2.0.28` source confirms one useful correlation link: a
caller-supplied `Session.Message.ID` is reconciled as the inbox ID, and the
`InboxDelivered` projector writes the visible `Session.Message.User` with that
same ID. That identifies admission and delivery, but does not directly bind
later assistant steps or session execution terminals to the input. Reusing the
same ID in the same session/type reconciles to the first admission, including
after delivery; a retry must therefore reuse the exact original payload and ID.

```text
POST prompt(id) -> inbox.enqueued(inboxID=id) -> inbox.delivered(inboxID=id)
                                             -> projected user message(id)  [proven]
                                             -> step.started(assistantMessageID)
                                             -> step.ended(assistantMessageID)
                                             -> execution.terminal(sessionID) [not input-scoped]
```

`Step.Started` / `Step.Ended` carry an assistant message ID, not the input ID;
`session.execution.*` carries only the session ID and represents a process-local
busy period. The runner delivers inputs at step boundaries. At an input
boundary, it prioritizes pending steers, delivers at most one queued input, then
also promotes steers that arrived during delivery. Thus `delivery=queue` and
the gateway's local session lock do not exclude another OpenCode client from
the same step context. A conservative live observer could infer a step belongs
to an input only while that input is the sole newly delivered prompt in the
step's context, then fail uncertain on interleaving. That inference is not
implemented or verified here.

Recovery is a separate blocker. The experimental
`GET /api/experimental/session/{sessionID}/log?after=...&follow=true` uses an
exclusive aggregate sequence and a replay-to-live `log.synced` watermark, but
v2.0.28 stores event rows only when the internal `events.persist` option is
enabled. The stock CLI does not set that option and the bus default is false;
the log therefore cannot replay durable step events in the default server
configuration. Persisted rows have no configured TTL and are deleted with the
session. `GET /api/event` is a separate volatile feed that misses events during
disconnection and has no replay cursor. Message projections can show current
assistant snapshots, but do not expose the durable event sequence needed to
reconstruct a missed input-to-step boundary with certainty.

Cancellation is also not task-scoped: `DELETE .../inbox/{inboxID}` only cancels
an undelivered item and returns no indication whether it was available;
`POST .../interrupt` interrupts active execution for the whole session. The
adapter cannot safely use that interrupt for one A2A task when another client
may have queued work. The adapter therefore rejects before creating a session
or submitting a prompt rather than risking cross-task completion, false
cancellation, or duplicate admission after an uncertain timeout.

Evidence links: pinned [prompt implementation](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/session.ts#L145-L175), [inbox reconciliation and delivery](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/inbox.ts#L154-L201), [delivery ordering](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/inbox.ts#L491-L529), [input projector](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/projector.ts#L618-L648), [step and execution event schemas](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session-event.ts#L203-L370), [assistant and idle message schemas](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/schema/src/session-message.ts#L212-L295), [bus persistence and log replay](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/bus.ts#L174-L204), and [stock CLI server options](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/cli/src/server-process.ts#L94-L134). The generated [v2.0.28 OpenAPI](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/protocol/openapi.json) specifies the endpoint envelopes. A v2.0.30 source spot-check found the same prompt-ID reconciliation, no input ID in assistant/step/terminal schemas, and the same `events.persist=false` bus default.

### Live upstream Phase 1 correlation probe

A sanitized, isolated upstream-only experiment against OpenCode `2.0.28` used
`openai/gpt-6-luna-fast`, a temporary workspace, and restrictive session
permissions. It did **not** use the A2A adapter; the API credential was passed
only to the isolated server through a mode-restricted temporary file and was
neither printed nor included in prompt text. The event subscription returned
HTTP 200 before prompt admission. Two distinct `delivery=queue` inputs were
admitted; the second was submitted after the first `session.step.started`. Both
receipt IDs matched the caller-supplied IDs, both IDs appeared in
`session.inbox.delivered` and the projected user messages, and separate
assistant messages were projected. Both expected sentinel strings appeared
across the assistant history; this probe did not retain raw assistant text or
prove which individual message contained which sentinel. The observed order
was:

```text
inbox.enqueued(A) -> inbox.delivered(A) -> step.started(S1)
-> inbox.enqueued(B) -> step.ended(S1) -> inbox.delivered(B)
-> step.started(S2) -> step.ended(S2)
-> session.execution.succeeded(session only)
```

`S1` and `S2` denote the assistant message IDs carried by step events; the
events do not identify either step as caused by input A or B.
The single terminal event followed both prompts and contained no input ID;
assistant message schemas also had no input-link field. A separate pending
input test confirmed exact same-ID retry returned the original receipt and
deleting that still-undelivered inbox item returned `204`. After closing the
volatile feed, the experimental session log returned HTTP 200 with only a
`log.synced` frame and no replayed step/input event. This demonstrates one
controlled queue ordering, **not** a contract that every input has a distinct
assistant or terminal result, nor a completed A2A execution E2E.
The feed was closed after the observed terminal event; a mid-execution
disconnect/recovery scenario was not exercised.

A property-whitelisted, sanitized event trace and boolean run assertions are
recorded in
[`docs/evidence/opencode-v2.0.28-correlation-probe.json`](./evidence/opencode-v2.0.28-correlation-probe.json).
It omits credentials, session/workspace IDs, prompt and model text, sentinel
strings, and raw event payloads. It documents one observed run, not a general
upstream guarantee.

**Phase 1 decision: FAIL.** Do not enable V2 A2A prompts from this evidence.
The smallest feasible upstream remedy is to propagate a stable input/execution
ID through assistant-step and terminal events, provide replayable or
input-addressable terminal status, and make active cancellation input-scoped
(or document an enforceable exclusive-session contract). Until then, ambiguous
or disconnected outcomes must remain fail-closed.

V1 stays the default, and the adapter does not auto-detect or fall back between
versions. Reproduce the V2 session-transport check with
`bash ./scripts/live_opencode_v2_session_smoke.sh`. It is pinned to
OpenCode `2.0.28`, verifies Basic-auth rejection plus `/api/info`, `/api/location`,
and session create/get/list through the application-selected V2 adapter, and
submits no model prompt. This smoke does not add V2 to the supported runtime
line above. V2
session model selection changes persistent session state, so request-scoped
model overrides are not supported; V2 `a2a_call` execution parity is also not
claimed. OpenCode V2 allows `external_directory` by default. Before any future
release of V2 prompt execution, operators must configure an explicit
restrictive upstream V2 `permissions` policy.

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
