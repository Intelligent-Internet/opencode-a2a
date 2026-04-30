# Extension Specifications

This index records the repository-governed extension URIs published by `opencode-a2a`.
Each published URI is a stable extension identifier. The repository-hosted specification
documents in `docs/extensions/**` are the current human-readable publication path for
those identifiers; the extension identity itself is intentionally decoupled from the
GitHub document location.
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

## URI Index

| Extension | Scope | Disclosure | URI | Repository Spec |
| --- | --- | --- | --- | --- |
| Shared Session Binding v1 | Shared request metadata | Public + extended | `urn:opencode-a2a:extension:shared:session-binding:v1` | [docs/extensions/shared/session-binding/v1.md](./extensions/shared/session-binding/v1.md) |
| Shared Model Selection v1 | Shared request metadata | Public + extended | `urn:opencode-a2a:extension:shared:model-selection:v1` | [docs/extensions/shared/model-selection/v1.md](./extensions/shared/model-selection/v1.md) |
| Shared Stream Hints v1 | Shared response/stream metadata | Public + extended | `urn:opencode-a2a:extension:shared:stream-hints:v1` | [docs/extensions/shared/stream-hints/v1.md](./extensions/shared/stream-hints/v1.md) |
| Shared Interactive Interrupt v1 | Shared JSON-RPC callback methods | Public + extended | `urn:opencode-a2a:extension:shared:interactive-interrupt:v1` | [docs/extensions/shared/interactive-interrupt/v1.md](./extensions/shared/interactive-interrupt/v1.md) |
| OpenCode Session Management v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:session-management:v1` | [docs/extensions/private/session-management/v1.md](./extensions/private/session-management/v1.md) |
| OpenCode Provider Discovery v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:provider-discovery:v1` | [docs/extensions/private/provider-discovery/v1.md](./extensions/private/provider-discovery/v1.md) |
| OpenCode Workspace Control v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:workspace-control:v1` | [docs/extensions/private/workspace-control/v1.md](./extensions/private/workspace-control/v1.md) |
| OpenCode Interrupt Recovery v1 | Provider-private JSON-RPC methods | Extended only | `urn:opencode-a2a:extension:private:interrupt-recovery:v1` | [docs/extensions/private/interrupt-recovery/v1.md](./extensions/private/interrupt-recovery/v1.md) |
| A2A Compatibility Profile v1 | Authenticated discovery metadata | Extended only | `urn:opencode-a2a:extension:private:compatibility-profile:v1` | [docs/extensions/private/compatibility-profile/v1.md](./extensions/private/compatibility-profile/v1.md) |
| A2A Wire Contract v1 | Authenticated discovery metadata | Extended only | `urn:opencode-a2a:extension:private:wire-contract:v1` | [docs/extensions/private/wire-contract/v1.md](./extensions/private/wire-contract/v1.md) |
