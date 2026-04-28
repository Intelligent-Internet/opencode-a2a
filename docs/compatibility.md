# Compatibility Guide

This document defines the compatibility promises `opencode-a2a` currently upholds for A2A consumers, operators, and maintainers.

## Runtime Support

- Python versions: 3.11, 3.12, 3.13
- A2A SDK line: `1.x.y`
- Supported A2A protocol line: `1.0`

The repository currently pins one concrete SDK release in `pyproject.toml` within that v1 line. Upgrade the SDK deliberately rather than relying on floating dependency resolution.

## Contract Honesty

Machine-readable discovery surfaces must reflect actual runtime behavior:

- public Agent Card
- authenticated extended card
- OpenAPI metadata
- JSON-RPC wire contract
- compatibility profile

If runtime support is not implemented, do not publish it as a supported machine-readable capability.

Consumer guidance:

- Treat the v1 core A2A methods (`SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `SubscribeToTask`) as the portable baseline.
- Treat `urn:a2a:*` entries in this repository as shared repo-family conventions, not as a claim that they are part of the A2A core baseline.
- Treat `opencode.*` methods and `metadata.opencode.*` fields as provider-private OpenCode control and discovery surfaces layered on top of the portable A2A baseline.
- Treat [extension-specifications.md](./extension-specifications.md) as the stable URI/spec index, not as the main usage guide.

## Normative Sources

When docs or reference material disagree, treat these as normative in this order:

- runtime behavior validated by tests
- machine-readable discovery output such as Agent Card, authenticated extended card, and OpenAPI metadata
- repository-owned docs in `README.md`, `docs/`, and `CONTRIBUTING.md`

External TCK runs and local conformance experiments are investigation inputs. They do not override the repository's declared contract by themselves.

## Compatibility-Sensitive Surface

This repository still ships as an alpha project. Within that alpha line, these declared surfaces should not drift silently:

- core A2A send / stream / task methods
- v1-only request/response payloads and enum values
- v1 protocol-aware JSON-RPC and REST error shaping
- shared session-binding metadata
- shared model-selection metadata
- shared streaming metadata
- declared custom JSON-RPC extension methods
- authenticated extended card and OpenAPI wire-contract metadata

Changes to those surfaces should be treated as compatibility-sensitive and should include corresponding test updates.

Service-level behavior layered on top of those core methods should also be declared explicitly when interoperability depends on it. Current examples:

- `SubscribeToTask` replay-once behavior for terminal updates
- first-terminal-state-wins task persistence policy
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

- once a task reaches a terminal state, later conflicting writes are dropped on a first-terminal-state-wins basis
- task-store I/O failures are surfaced as stable service errors instead of leaking backend-specific exceptions
- accepted output-mode negotiation for a task is persisted with the task so later reads keep the same filtered output contract
- adapter-managed migrations only own adapter state tables; SDK-managed task schema remains SDK-owned

The default SQLite-first profile is intended for local or controlled single-instance deployments. Wider SQLAlchemy dialect compatibility should be treated as implementation latitude rather than a strong public promise unless explicitly documented later.

## Extension Stability

- Shared metadata and extension contracts should stay synchronized across Agent Card, OpenAPI, and runtime behavior.
- Public Agent Card should stay intentionally minimal. Detailed extension params belong in the authenticated extended card and OpenAPI, not back in the anonymous discovery surface.
- Deployment-conditional methods must be declared as conditional rather than silently disappearing.
- `opencode.sessions.prompt_async` input-part passthrough is compatibility-sensitive. Changes to supported part types, passthrough field semantics, or rejection behavior should be treated as wire-level changes.
- `opencode.sessions.shell` is compatibility-sensitive as a deployment-conditional shell snapshot surface. It should not silently widen into a general interactive shell API.
- `opencode.workspaces.*` and `opencode.worktrees.*` are boundary-sensitive and should remain explicitly provider-private, operator-scoped, and deployment-conditional where applicable.
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

- `urn:a2a:*` extension URIs used here should be read as shared conventions in this repository family.
- They are not a claim that those extensions are part of the A2A core baseline.
- `opencode.*` methods are intentionally product-specific. They improve OpenCode-aware workflows but should not be assumed to transfer unchanged to unrelated A2A agents.

## Non-Goals

This repository does not currently promise:

- hard multi-tenant isolation inside one instance
- generic provider-auth orchestration on behalf of OpenCode
- compatibility with legacy A2A `0.3` method aliases or payload shapes

Those areas may evolve later, but they should not be implied by current machine-readable discovery output.
