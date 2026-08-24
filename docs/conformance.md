# Repository-Owned Compatibility Probes

`./scripts/conformance.sh` runs black-box checks maintained and reviewed with this repository. It deliberately does not clone, pin, or execute the official A2A TCK.

## Scope

The probe verifies a small set of high-value A2A 1.0 invariants through public HTTP boundaries:

- Agent Card discovery advertises both HTTP+JSON and JSON-RPC interfaces
- empty `SendMessage` input is rejected before execution
- unsupported push notification configuration uses the protocol-specific error
- subscribing to a terminal task returns `UnsupportedOperationError` on both transports
- `ListTasks` is reachable through both shipped transports

These checks protect this runtime's declared contract. They are not a complete A2A conformance suite and must not be presented as certification.

## Usage

Run against the local dummy-backed runtime:

```bash
bash ./scripts/conformance.sh
```

Run against an existing deployment:

```bash
CONFORMANCE_SUT_URL=http://127.0.0.1:8000 \
CONFORMANCE_AUTH_TOKEN=dev-token \
bash ./scripts/conformance.sh
```

`CONFORMANCE_AUTH_TOKEN` is required for an existing deployment. The default `test-token` is used only for the locally launched test SUT.

Use `CONFORMANCE_OUTPUT_DIR` to select the artifact directory and `CONFORMANCE_SKIP_REPO_SYNC=1` only when the locked environment has already been verified.

## Artifacts

Each run writes:

- `agent-card.json`: the discovered public Agent Card
- `report.json`: versioned check results and repository revision
- `probe.log`: human-readable probe output
- `sut.log`: local test-runtime output, when the script launches it
- `repo-health.log`: repository environment checks, unless explicitly skipped

## External Tools

Maintainers may run third-party TCKs independently to investigate interoperability. Record exact tool revisions and wire payloads when reporting a finding. External output is evidence to triage, not an automatic merge gate, source of runtime truth, or reason to restore obsolete protocol behavior.
