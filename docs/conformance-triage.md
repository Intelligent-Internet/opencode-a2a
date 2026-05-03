# External Conformance Triage

This document summarizes the current interpretation rules for external TCK runs after the repository's A2A v1 migration.

## Current Runtime Baseline

- `opencode-a2a` now targets the `a2a-sdk 1.x.y` line
- the runtime defaults to `1.0` and also exposes SDK-backed `0.3` compatibility for core methods
- canonical JSON-RPC core methods are `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, and `SubscribeToTask`
- provider-private `opencode.*` methods remain v1-only and are not part of the `0.3` compatibility promise

## How To Read TCK Failures

When a TCK run fails, classify the result before changing the runtime:

- `Runtime gap`
  - the failure reproduces against the current declared runtime contract and contradicts the repository's machine-readable discovery or docs
- `TCK assumption mismatch`
  - the failure depends on method names, payload shapes, or schema expectations that do not match the current A2A v1 SDK/runtime contract
- `Local experiment artifact`
  - the failure depends on dummy-backed local behavior, environment heuristics, or unrelated tooling/setup issues

## Current Guidance

- Re-run conformance against the current runtime before using any historical triage note.
- Treat Agent Card, authenticated extended card, OpenAPI, and runtime tests as the repository's declared source of truth.
- Do not widen `0.3` compatibility beyond the declared core surface just to satisfy an outdated TCK assumption.
- If a TCK gap is real, document it against the current v1 contract with the exact request/response payloads that failed.

## Historical Note

Earlier repository-local triage notes were written before the v1 migration and described a mixed `0.3` / partial `1.0` state. Those notes are no longer normative and were removed to avoid stale guidance.
