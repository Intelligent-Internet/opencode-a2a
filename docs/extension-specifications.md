# Extension Specifications

This document is the stable specification surface referenced by the extension URIs published in the Agent Card. It is intentionally a compact URI/spec index, not the main consumer guide. For runtime behavior, request/response examples, and client integration guidance, see [`guide.md`](./guide.md). For compatibility-sensitive surface and contract-honesty guidance, see [`compatibility.md`](./compatibility.md).

## SDK Compatibility Note

The current A2A prose specification references an extended-card availability flag as `AgentCard.capabilities.extendedAgentCard` in some sections.

The current official JSON schema and SDK types expose the supported field as top-level `supportsAuthenticatedExtendedCard`.

`opencode-a2a` follows the shipped JSON schema and SDK surface, so Agent Card payloads emitted by this project use `supportsAuthenticatedExtendedCard`.

## Shared Session Binding v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#shared-session-binding-v1`

- Scope: shared A2A request metadata for rebinding to an existing upstream session
- Public Agent Card: capability declaration plus minimal routing metadata
- Authenticated extended card: full profile, notes, and detailed contract metadata
- Runtime field: `metadata.shared.session.id`

## Shared Model Selection v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#shared-model-selection-v1`

- Scope: shared request-scoped model override on the main chat path
- Public Agent Card: capability declaration plus required metadata fields
- Authenticated extended card: full profile, notes, and detailed contract metadata
- Runtime field: `metadata.shared.model`

## Shared Stream Hints v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#shared-stream-hints-v1`

- Scope: shared canonical metadata for block, usage, interrupt, and session hints
- Public Agent Card: metadata roots plus the minimum discoverability fields for block identity, progress status, interrupt lifecycle, session identity, and basic token usage
- Authenticated extended card: full shared stream contract including detailed block payload mappings and extended usage metadata
- Runtime fields: `metadata.shared.stream`, `metadata.shared.usage`, `metadata.shared.interrupt`, `metadata.shared.session`

## OpenCode Session Management v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#opencode-session-management-v1`

- Scope: provider-private OpenCode session read, mutation, and control methods
- Public Agent Card: capability declaration only
- Authenticated extended card: full method matrix, read/mutation/control grouping, pagination rules, errors, context semantics, and existing `opencode.sessions.prompt_async` input-part contracts
- Transport: A2A JSON-RPC extension methods
- `opencode.sessions.prompt_async` includes a provider-private `request.parts[]` compatibility surface for upstream OpenCode part types `text`, `file`, `agent`, and `subtask`
- `subtask` support is declared as passthrough-compatible only: subagent selection and task-tool execution remain upstream OpenCode runtime behavior, not a separate `opencode-a2a` orchestration API

## OpenCode Provider Discovery v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#opencode-provider-discovery-v1`

- Scope: provider-private provider and model discovery methods
- Public Agent Card: capability declaration only
- Authenticated extended card: full method contracts, error surface, and routing metadata
- Transport: A2A JSON-RPC extension methods

## Shared Interactive Interrupt v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#shared-interactive-interrupt-v1`

- Scope: shared interrupt callback reply methods
- Public Agent Card: capability declaration, supported interrupt events, and request ID field
- Authenticated extended card: full callback contract, errors, and routing metadata
- Transport: A2A JSON-RPC extension methods

## OpenCode Interrupt Recovery v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#opencode-interrupt-recovery-v1`

- Scope: provider-private recovery methods for pending local interrupt bindings
- Public Agent Card: capability declaration only
- Authenticated extended card: full method contracts, error surface, local-registry notes, and identity-scope semantics
- Transport: A2A JSON-RPC extension methods

## OpenCode Workspace Control v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#opencode-workspace-control-v1`

- Scope: provider-private project discovery plus workspace/worktree surfaces over upstream experimental endpoints, with deployment-conditional operator mutation methods
- Public Agent Card: capability declaration only
- Authenticated extended card: full method contracts, error surface, routing notes, and upstream-stability hints
- Transport: A2A JSON-RPC extension methods

## A2A Compatibility Profile v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#a2a-compatibility-profile-v1`

- Scope: compatibility profile describing core baselines, extension retention, and service behaviors
- Includes machine-readable protocol compatibility summary for the current v1-only runtime boundary
- Public Agent Card: capability declaration only
- Authenticated extended card: full compatibility profile payload
- Transport: Agent Card extension params

## A2A Wire Contract v1

URI: `https://github.com/Intelligent-Internet/opencode-a2a/blob/main/docs/extension-specifications.md#a2a-wire-contract-v1`

- Scope: wire-level contract for supported methods, endpoints, and error semantics
- Includes the same machine-readable protocol compatibility summary published by the compatibility profile
- Public Agent Card: capability declaration only
- Authenticated extended card: full wire contract payload
- Transport: Agent Card extension params
