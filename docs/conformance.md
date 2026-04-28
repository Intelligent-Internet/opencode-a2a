# External Conformance Experiments

This repository keeps internal regression and external interoperability experiments separate on purpose.

## Scope

- `./scripts/doctor.sh` remains the primary internal regression entrypoint.
- `./scripts/conformance.sh` is a local/manual experiment entrypoint for official external tooling.
- External conformance output should be treated as investigation input, not as an automatic merge gate.

## Current Experiment Shape

The default `./scripts/conformance.sh` workflow does the following:

1. Sync the repository environment unless explicitly skipped.
2. Cache or refresh the official `a2aproject/a2a-tck` checkout.
3. Start a local dummy-backed `opencode-a2a` runtime unless `CONFORMANCE_SUT_URL` points to an existing SUT.
4. Run the requested TCK category, defaulting to `mandatory`.
5. Preserve raw logs and machine-readable reports under `run/conformance/<timestamp>/`.

The default local SUT uses the repository test double `DummyChatOpencodeUpstreamClient`. That keeps the experiment reproducible without requiring a live OpenCode upstream.

## Usage

Run the default mandatory experiment:

```bash
bash ./scripts/conformance.sh
```

Run a different TCK category:

```bash
bash ./scripts/conformance.sh capabilities
```

Target an already running runtime instead of the local dummy-backed SUT:

```bash
CONFORMANCE_SUT_URL=http://127.0.0.1:8000 \
A2A_AUTH_TYPE=bearer \
A2A_AUTH_TOKEN=dev-token \
bash ./scripts/conformance.sh mandatory
```

## Artifacts

Each run keeps the following artifacts in the selected output directory:

- `agent-card.json`: fetched public Agent Card
- `health.json`: fetched authenticated health payload when the local SUT is used
- `tck.log`: raw TCK console output
- `pytest-report.json`: pytest-json-report output emitted by the TCK runner
- `failed-tests.json`: compact list of failed/error node IDs for triage
- `metadata.json`: experiment metadata including local repo commit and cached TCK commit

## Interpretation Guidance

When a TCK run fails, inspect the raw report before changing the runtime:

- Some failures may point to real runtime gaps.
- Some failures may come from TCK assumptions that do not match the current `a2a-sdk 1.x.y` contract.
- Some failures may come from local dummy-backed experiment behavior rather than a wire-level runtime defect.

The experiment is useful only if those categories stay separate during triage.

The current first-pass triage is recorded in [`./conformance-triage.md`](./conformance-triage.md).
