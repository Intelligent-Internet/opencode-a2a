# Security Policy

## Scope

This repository is an adapter layer that exposes OpenCode through A2A
HTTP+JSON and JSON-RPC interfaces. It adds authentication, task/session
contracts, streaming, interrupt handling, and runtime guidance, but it does
not fully isolate upstream model credentials from OpenCode runtime behavior.

## Security Boundary

- `A2A_BEARER_TOKEN` protects access to the A2A surface, but it is not a
  tenant-isolation boundary inside one deployed instance.
- One `OpenCode + opencode-a2a-server` instance pair is treated as a
  single-tenant trust boundary by design.
- Tenant isolation across consumers is expected to come from parameterized self-deployment of separate instance pairs with distinct Linux users, workspace roots, credentials, and runtime ports.
- Within one instance, consumers share the same underlying OpenCode
  workspace/environment by default.
- LLM provider keys are consumed by the `opencode` process. Prompt injection or
  indirect exfiltration attempts may still expose sensitive values.
- Payload logging is opt-in. When `A2A_LOG_PAYLOADS=true`, operators should
  treat logs as potentially sensitive operational data.
- This project does not ship host bootstrap or process-manager wrappers as an
  official product capability. Operators remain responsible for file
  permissions, secret storage, service users, and supervisor-specific hardening.

## Threat Model

This project is currently best suited for trusted or internal environments.
Important limits:

- no per-tenant workspace isolation inside one instance
- no hard guarantee that upstream provider keys are inaccessible to agent logic
- bearer-token auth only by default; stronger identity propagation is still a
  follow-up hardening area
- operators remain responsible for host hardening, secret rotation, process
  access controls, and reverse-proxy exposure strategy

## Reporting a Vulnerability

Please avoid posting active secrets, bearer tokens, or reproduction payloads
that contain private data in public issues.

Preferred disclosure order:

1. Use GitHub private vulnerability reporting if it is available for this
   repository.
2. If private reporting is unavailable, contact the repository maintainer
   directly through GitHub before opening a public issue.
3. For low-risk hardening ideas that do not expose private data, a normal
   GitHub issue is acceptable.

## Supported Branches

Security fixes are expected to land on the active `main` branch first.
