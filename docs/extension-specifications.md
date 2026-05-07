# Extension Specifications

This document is the stable specification index for the shared and provider-private extension URIs published by `opencode-a2a`. It is intentionally a compact URI/spec map, not the main consumer guide. For runtime behavior, request examples, and operational setup, see [`guide.md`](./guide.md). For compatibility promises and stability expectations, see [`compatibility.md`](./compatibility.md).

## Discovery Surface Note

`opencode-a2a` splits extension discovery into three layers:

- Public Agent Card: minimal anonymous discovery for core interfaces and low-sensitivity shared extensions
- Authenticated extended Agent Card: the canonical machine-readable source for deployment-specific provider-private contracts
- OpenAPI metadata: minimal anonymous shared-contract disclosure only; provider-private method matrices are intentionally not expanded there

Provider-private contract note:

- `opencode.*` methods in this repository are deployment-specific provider extensions, not portable A2A baseline capabilities.
- Shared `metadata.shared.*` contracts are intended to remain low-risk and transportable.
- Compatibility and wire-contract URIs are descriptive metadata contracts, not activatable runtime capabilities.

## SDK and Discovery Compatibility

- A2A v1.0 Agent Cards expose extended-card availability via `AgentCard.capabilities.extendedAgentCard`.
- `opencode-a2a` emits `capabilities.extendedAgentCard` in both public and authenticated extended cards; it does not emit the removed top-level `supportsAuthenticatedExtendedCard` field.
- The canonical authenticated extended Agent Card HTTP endpoint is `GET /extendedAgentCard`.

## URI Index

| Extension | Scope | Disclosure | URI | Section |
| --- | --- | --- | --- | --- |
| Shared Session Binding v1 | Shared request metadata | Public + extended | `urn:opencode-a2a:extension:shared:session-binding:v1` | [Shared Session Binding v1](#shared-session-binding-v1) |
| Shared Model Selection v1 | Shared request metadata | Public + extended | `urn:opencode-a2a:extension:shared:model-selection:v1` | [Shared Model Selection v1](#shared-model-selection-v1) |
| Shared Stream Hints v1 | Shared response/stream metadata | Public + extended | `urn:opencode-a2a:extension:shared:stream-hints:v1` | [Shared Stream Hints v1](#shared-stream-hints-v1) |
| Shared Interactive Interrupt v1 | Shared JSON-RPC callback methods | Public + extended | `urn:opencode-a2a:extension:shared:interactive-interrupt:v1` | [Shared Interactive Interrupt v1](#shared-interactive-interrupt-v1) |
| OpenCode Session Management v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:session-management:v1` | [OpenCode Session Management v1](#opencode-session-management-v1) |
| OpenCode Provider Discovery v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:provider-discovery:v1` | [OpenCode Provider Discovery v1](#opencode-provider-discovery-v1) |
| OpenCode Workspace Control v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:workspace-control:v1` | [OpenCode Workspace Control v1](#opencode-workspace-control-v1) |
| OpenCode Interrupt Recovery v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:interrupt-recovery:v1` | [OpenCode Interrupt Recovery v1](#opencode-interrupt-recovery-v1) |
| A2A Compatibility Profile v1 | Authenticated discovery metadata | Extended only | `urn:opencode-a2a:extension:private:compatibility-profile:v1` | [A2A Compatibility Profile v1](#a2a-compatibility-profile-v1) |
| A2A Wire Contract v1 | Authenticated discovery metadata | Extended only | `urn:opencode-a2a:extension:private:wire-contract:v1` | [A2A Wire Contract v1](#a2a-wire-contract-v1) |

## Shared Session Binding v1

Extension URI: `urn:opencode-a2a:extension:shared:session-binding:v1`

- Scope: shared A2A request metadata for rebinding to an existing upstream OpenCode session, plus negotiated response/task session metadata
- Disclosure: public Agent Card and authenticated extended Agent Card
- Activation: client requests the URI via `A2A-Extensions` and sends `metadata.shared.session.id`
- Runtime fields: `metadata.shared.session.id`, `metadata.shared.session`, optional provider-private companions `metadata.opencode.directory` and `metadata.opencode.workspace.id`
- Dependencies: none declared by this version
- Security boundary: shared session identity is portable; provider-private routing metadata remains deployment-scoped
- Versioning: breaking changes require a new versioned URI

## Shared Model Selection v1

Extension URI: `urn:opencode-a2a:extension:shared:model-selection:v1`

- Scope: shared request-scoped model override for the main chat path
- Disclosure: public Agent Card and authenticated extended Agent Card
- Activation: client requests the URI via `A2A-Extensions` and sends `metadata.shared.model`
- Runtime fields: `metadata.shared.model.providerID`, `metadata.shared.model.modelID`
- Applies to methods: `SendMessage`, `SendStreamingMessage`
- Dependencies: none declared by this version
- Security boundary: request-scoped model preference is shared; provider defaults and auth remain OpenCode deployment concerns
- Versioning: breaking changes require a new versioned URI

## Shared Stream Hints v1

Extension URI: `urn:opencode-a2a:extension:shared:stream-hints:v1`

- Scope: shared response/task/stream metadata for block identity, progress, and usage hints
- Disclosure: public Agent Card and authenticated extended Agent Card
- Activation: client requests the URI via `A2A-Extensions`; runtime emits shared metadata on streamed events and final task payloads
- Declared field map:
  - `metadata.shared.stream.block_type`
  - `metadata.shared.stream.sequence`
  - `metadata.shared.progress.type`
  - `metadata.shared.progress.status`
  - `metadata.shared.usage.input_tokens`
  - `metadata.shared.usage.output_tokens`
  - `metadata.shared.usage.total_tokens`
- Clients must not treat undeclared metadata under this namespace as part of the shared v1 contract
- Dependencies: none declared by this version
- Security boundary: this extension is observational metadata only; callback methods are defined by the separate shared interactive interrupt extension
- Versioning: breaking changes require a new versioned URI

## Shared Interactive Interrupt v1

Extension URI: `urn:opencode-a2a:extension:shared:interactive-interrupt:v1`

- Scope: shared JSON-RPC callback methods used to answer interactive permission and question interrupts
- Disclosure: public Agent Card and authenticated extended Agent Card
- Activation: client requests the URI via `A2A-Extensions` before calling `a2a.interrupt.*` methods
- Methods: `a2a.interrupt.permission.reply`, `a2a.interrupt.question.reply`, `a2a.interrupt.question.reject`
- Runtime fields: `metadata.shared.interrupt`, including `metadata.shared.interrupt.request_id` as the callback correlation key
- Dependencies: none declared by this version
- Security boundary: the methods are shared, but request IDs remain scoped to authenticated caller identity and local interrupt state
- Versioning: breaking changes require a new versioned URI

## OpenCode Session Management v1

Extension URI: `urn:opencode-a2a:extension:private:session-management:v1`

- Scope: OpenCode session read, mutation, and control methods exposed as A2A JSON-RPC extension methods
- Disclosure: authenticated extended Agent Card only
- Activation: authenticated client requests the URI via `A2A-Extensions` before calling `opencode.sessions.*`
- Methods: `opencode.sessions.status`, `list`, `get`, `children`, `todo`, `diff`, `messages.get`, `messages.list`, `prompt_async`, `command`, `fork`, `share`, `unshare`, `summarize`, `revert`, `unrevert`, and deployment-conditional `shell`
- Dependencies: none declared by this version
- Security boundary: this extension is provider-private and deployment-scoped; consumers must not treat it as portable A2A baseline behavior
- Versioning: breaking changes require a new versioned URI

## OpenCode Provider Discovery v1

Extension URI: `urn:opencode-a2a:extension:private:provider-discovery:v1`

- Scope: OpenCode provider and model discovery methods exposed as A2A JSON-RPC extension methods
- Disclosure: authenticated extended Agent Card only
- Activation: authenticated client requests the URI via `A2A-Extensions` before calling `opencode.providers.list` or `opencode.models.list`
- Methods: `opencode.providers.list`, `opencode.models.list`
- Dependencies: none declared by this version
- Security boundary: provider/model catalogs remain OpenCode-specific operational surfaces
- Versioning: breaking changes require a new versioned URI

## OpenCode Workspace Control v1

Extension URI: `urn:opencode-a2a:extension:private:workspace-control:v1`

- Scope: OpenCode project, workspace, and worktree discovery/control methods exposed as A2A JSON-RPC extension methods
- Disclosure: authenticated extended Agent Card only
- Activation: authenticated client requests the URI via `A2A-Extensions` before calling `opencode.projects.*`, `opencode.workspaces.*`, or `opencode.worktrees.*`
- Methods: stable project discovery plus experimental workspace/worktree discovery and deployment-conditional mutation methods
- Dependencies: none declared by this version
- Security boundary: this extension is provider-private, operator-scoped, and partly deployment-conditional
- Versioning: breaking changes require a new versioned URI

## OpenCode Interrupt Recovery v1

Extension URI: `urn:opencode-a2a:extension:private:interrupt-recovery:v1`

- Scope: local interrupt recovery methods exposed as A2A JSON-RPC extension methods
- Disclosure: authenticated extended Agent Card only
- Activation: authenticated client requests the URI via `A2A-Extensions` before calling `opencode.permissions.list` or `opencode.questions.list`
- Methods: `opencode.permissions.list`, `opencode.questions.list`
- Dependencies: none declared by this version
- Security boundary: recovery state is adapter-local and scoped to the current authenticated caller
- Versioning: breaking changes require a new versioned URI

## A2A Compatibility Profile v1

Extension URI: `urn:opencode-a2a:extension:private:compatibility-profile:v1`

- Scope: authenticated discovery metadata describing protocol support, extension retention, and stable service behaviors
- Disclosure: authenticated extended Agent Card only
- Activation: authenticated extended Agent Card discovery
- Data surface: `protocol_compatibility`, method-retention metadata, deployment profile summary, and declared service behaviors
- Dependencies: none declared by this version
- Security boundary: this is deployment-specific contract metadata, not a portable core A2A guarantee
- Versioning: breaking changes require a new versioned URI

## A2A Wire Contract v1

Extension URI: `urn:opencode-a2a:extension:private:wire-contract:v1`

- Scope: authenticated discovery metadata describing supported methods, HTTP endpoints, extension URIs, and unified error semantics
- Disclosure: authenticated extended Agent Card only
- Activation: authenticated extended Agent Card discovery
- Data surface: core JSON-RPC methods, core HTTP endpoints, extension JSON-RPC methods, conditional availability, and protocol compatibility summary
- Dependencies: none declared by this version
- Security boundary: this is deployment-specific discovery metadata, not an invitation to assume portability of `opencode.*` surfaces
- Versioning: breaking changes require a new versioned URI
