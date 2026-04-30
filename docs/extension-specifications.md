# Extension Specifications

This document is the stable specification surface referenced by the extension URIs published in the Agent Card. It is intentionally a compact URI/spec index, not the main consumer guide. For runtime behavior, request/response examples, and client integration guidance, see [`guide.md`](./guide.md). For compatibility-sensitive surface and contract-honesty guidance, see [`compatibility.md`](./compatibility.md).

Anonymous discovery surfaces intentionally stay minimal:

- Public Agent Card publishes only shared, low-sensitivity extension declarations.
- Anonymous OpenAPI mirrors that minimal shared disclosure policy and does not expand provider-private method matrices.
- Authenticated extended card is the canonical machine-readable source for deployment-specific provider-private contracts.

## SDK Compatibility Note

The current A2A prose specification references an extended-card availability flag as `AgentCard.capabilities.extendedAgentCard` in some sections.

The current official JSON schema and SDK types expose the supported field as top-level `supportsAuthenticatedExtendedCard`.

`opencode-a2a` follows the shipped JSON schema and SDK surface, so Agent Card payloads emitted by this project use `supportsAuthenticatedExtendedCard`.

## Shared Session Binding v1

URI: `urn:a2a:opencode-a2a:shared:session-binding:v1`

- Scope: shared A2A request metadata for rebinding to an existing upstream session
- Activation: request metadata on `SendMessage` / `SendStreamingMessage`
- Public Agent Card: capability declaration plus minimal routing metadata
- Anonymous OpenAPI: minimal shared contract summary only
- Authenticated extended card: full profile, notes, and detailed contract metadata
- Runtime field: `metadata.shared.session.id`

## Shared Model Selection v1

URI: `urn:a2a:opencode-a2a:shared:model-selection:v1`

- Scope: shared request-scoped model override on the main chat path
- Activation: request metadata on `SendMessage` / `SendStreamingMessage`
- Public Agent Card: capability declaration plus required metadata fields
- Anonymous OpenAPI: minimal shared contract summary only
- Authenticated extended card: full profile, notes, and detailed contract metadata
- Runtime field: `metadata.shared.model`

## Shared Stream Hints v1

URI: `urn:a2a:opencode-a2a:shared:stream-hints:v1`

- Scope: shared canonical metadata for block, usage, interrupt, and session hints
- Activation: response/task/stream metadata
- Public Agent Card: metadata roots plus the minimum discoverability fields for block identity, progress status, interrupt lifecycle, session identity, and basic token usage
- Anonymous OpenAPI: minimal shared contract summary only
- Authenticated extended card: full shared stream contract including detailed block payload mappings and extended usage metadata
- Runtime fields: `metadata.shared.stream`, `metadata.shared.usage`, `metadata.shared.interrupt`, `metadata.shared.session`

## OpenCode Session Management v1

URI: `urn:a2a:opencode-a2a:private:session-management:v1`

- Scope: provider-private OpenCode session read, mutation, and control methods
- Activation: A2A JSON-RPC extension methods
- Public Agent Card: not disclosed
- Anonymous OpenAPI: not expanded
- Authenticated extended card: full method matrix, read/mutation/control grouping, pagination rules, errors, context semantics, and existing `opencode.sessions.prompt_async` input-part contracts
- Transport: A2A JSON-RPC extension methods
- `opencode.sessions.prompt_async` includes a provider-private `request.parts[]` compatibility surface for upstream OpenCode part types `text`, `file`, `agent`, and `subtask`
- `subtask` support is declared as passthrough-compatible only: subagent selection and task-tool execution remain upstream OpenCode runtime behavior, not a separate `opencode-a2a` orchestration API

## OpenCode Provider Discovery v1

URI: `urn:a2a:opencode-a2a:private:provider-discovery:v1`

- Scope: provider-private provider and model discovery methods
- Activation: A2A JSON-RPC extension methods
- Public Agent Card: not disclosed
- Anonymous OpenAPI: not expanded
- Authenticated extended card: full method contracts, error surface, and routing metadata
- Transport: A2A JSON-RPC extension methods

## Shared Interactive Interrupt v1

URI: `urn:a2a:opencode-a2a:shared:interactive-interrupt:v1`

- Scope: shared interrupt callback reply methods
- Activation: A2A JSON-RPC extension methods
- Public Agent Card: capability declaration, supported interrupt events, and request ID field
- Anonymous OpenAPI: minimal shared contract summary only
- Authenticated extended card: full callback contract, errors, and routing metadata
- Transport: A2A JSON-RPC extension methods

## OpenCode Interrupt Recovery v1

URI: `urn:a2a:opencode-a2a:private:interrupt-recovery:v1`

- Scope: provider-private recovery methods for pending local interrupt bindings
- Activation: A2A JSON-RPC extension methods
- Public Agent Card: not disclosed
- Anonymous OpenAPI: not expanded
- Authenticated extended card: full method contracts, error surface, local-registry notes, and identity-scope semantics
- Transport: A2A JSON-RPC extension methods

## OpenCode Workspace Control v1

URI: `urn:a2a:opencode-a2a:private:workspace-control:v1`

- Scope: provider-private project discovery plus workspace/worktree surfaces over upstream experimental endpoints, with deployment-conditional operator mutation methods
- Activation: A2A JSON-RPC extension methods
- Public Agent Card: not disclosed
- Anonymous OpenAPI: not expanded
- Authenticated extended card: full method contracts, error surface, routing notes, and upstream-stability hints
- Transport: A2A JSON-RPC extension methods

## A2A Compatibility Profile v1

URI: `urn:a2a:opencode-a2a:private:compatibility-profile:v1`

- Scope: compatibility profile describing core baselines, extension retention, and service behaviors
- Includes machine-readable protocol compatibility summary for the current v1-only runtime boundary
- Activation: authenticated discovery metadata
- Public Agent Card: not disclosed
- Anonymous OpenAPI: not expanded
- Authenticated extended card: full compatibility profile payload
- Transport: Agent Card extension params

## A2A Wire Contract v1

URI: `urn:a2a:opencode-a2a:private:wire-contract:v1`

- Scope: wire-level contract for supported methods, endpoints, and error semantics
- Includes the same machine-readable protocol compatibility summary published by the compatibility profile
- Activation: authenticated discovery metadata
- Public Agent Card: not disclosed
- Anonymous OpenAPI: not expanded
- Authenticated extended card: full wire contract payload
- Transport: Agent Card extension params
