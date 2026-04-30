# Extension Specifications

This index records the repository-governed extension URIs published by `opencode-a2a`.
Each published URI resolves directly to the specification document for that extension.
For runtime behavior, request/response examples, and consumer guidance, see
[`guide.md`](./guide.md). For compatibility-sensitive promises, see
[`compatibility.md`](./compatibility.md).

Anonymous discovery surfaces intentionally stay minimal:

- Public Agent Card publishes only shared, low-sensitivity extension declarations.
- Anonymous OpenAPI mirrors that minimal shared disclosure policy and does not expand
  provider-private method matrices.
- The authenticated extended Agent Card is the canonical machine-readable source for
  deployment-specific provider-private contracts.

## SDK and Discovery Compatibility

- A2A v1.0 Agent Cards expose extended-card availability via
  `AgentCard.capabilities.extendedAgentCard`.
- `opencode-a2a` emits `capabilities.extendedAgentCard` in both public and authenticated
  extended cards; it does not emit the removed top-level
  `supportsAuthenticatedExtendedCard` field.
- The canonical authenticated extended Agent Card HTTP endpoint is `GET /extendedAgentCard`.
- The legacy `GET /agent/authenticatedExtendedCard` path remains available as a
  compatibility alias for older clients.

## URI Index

| Extension | Scope | Disclosure | URI |
| --- | --- | --- | --- |
| Shared Session Binding v1 | Shared request metadata | Public + extended | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/shared/session-binding/v1.md> |
| Shared Model Selection v1 | Shared request metadata | Public + extended | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/shared/model-selection/v1.md> |
| Shared Stream Hints v1 | Shared response/stream metadata | Public + extended | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/shared/stream-hints/v1.md> |
| Shared Interactive Interrupt v1 | Shared JSON-RPC callback methods | Public + extended | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/shared/interactive-interrupt/v1.md> |
| OpenCode Session Management v1 | Provider-private JSON-RPC methods | Extended only | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/private/session-management/v1.md> |
| OpenCode Provider Discovery v1 | Provider-private JSON-RPC methods | Extended only | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/private/provider-discovery/v1.md> |
| OpenCode Workspace Control v1 | Provider-private JSON-RPC methods | Extended only | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/private/workspace-control/v1.md> |
| OpenCode Interrupt Recovery v1 | Provider-private JSON-RPC methods | Extended only | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/private/interrupt-recovery/v1.md> |
| A2A Compatibility Profile v1 | Authenticated discovery metadata | Extended only | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/private/compatibility-profile/v1.md> |
| A2A Wire Contract v1 | Authenticated discovery metadata | Extended only | <https://raw.githubusercontent.com/Intelligent-Internet/opencode-a2a/main/docs/extensions/private/wire-contract/v1.md> |
