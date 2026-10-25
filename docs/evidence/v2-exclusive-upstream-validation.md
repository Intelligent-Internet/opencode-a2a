# Upstream-stack validation

The [exclusive acceptance record](./v2-exclusive-acceptance.json) describes the
author feature's model-backed live runs. Those model runs were not repeated for
this upstream stack. Its production Python tree and the V1, V2 session, V2
exclusive, and JavaScript probe harness files are byte-identical to author
commit `da51304`. The production digest is the concatenated
`source_tree_sha256` groups recorded in that acceptance record.

The upstream stack retains its foundation commits. Test-only differences mark
synthetic passwords for the secret scanner and check the common default-gate
wording `execution is disabled`; the upstream HTTP side-effect assertion remains
unchanged. The five synthetic-password annotations match `e86042a` exactly.
The public production-source digest uses the author's eight groups of eight hex
characters. The secret scanner baseline remains unchanged from the author;
no scanner gates are disabled.

## Observed regression runs

| Head | Command | Result |
| --- | --- | --- |
| Integration `880299d` | `bash ./scripts/doctor.sh` | 1011 passed, 0 failed; 92.68% line coverage; lint, format, secret scan, typing, coverage policy, package build and wheel smoke passed |
| Acceptance `372b84e` before the evidence-only integration merge | `bash ./scripts/doctor.sh` | 1011 passed, 0 failed; 92.62% line coverage; the same remaining doctor gates passed |
| Acceptance `372b84e` | `bash ./scripts/conformance.sh` | 8 passed, 0 failed |
| Acceptance `372b84e` | `bash ./scripts/live_opencode_v2_session_smoke.sh` | Isolated OpenCode 2.0.28, invalid Basic auth rejected, identity/location/session create/get/list passed; no model prompt submitted |
| Integration `a633808` | `bash ./scripts/doctor.sh` | 1012 passed, 0 failed; 92.65% line coverage; lint, format, secret scan, typing, coverage policy, package build and wheel smoke passed |
| Acceptance `bbddca7` before this QA-note update | `bash ./scripts/doctor.sh` | 1012 passed, 0 failed; 92.67% line coverage; the same remaining doctor gates passed |
| Integration `8111d76` | `bash ./scripts/doctor.sh` | 1021 passed, 0 failed; 92.66% line coverage; lint, format, secret scan, typing, coverage policy, package build and wheel smoke passed |
| Acceptance `b37a849` before this QA-note update | `bash ./scripts/doctor.sh` | 1021 passed, 0 failed; 92.68% line coverage; the same remaining doctor gates passed |

The author acceptance record includes the later 14-admission, single-SSE run
and its session-ownership, external-directory denial, and inside-root workspace
override rejection checks. Exclusive execution uses one configured workspace;
preferred-session reuse requires committed ownership, and malformed reserved
metadata is rejected without fallback. Default V2 session-only location mapping
is unchanged. Those live
results are author evidence, not a model-backed rerun of this upstream stack.

Coverage percentages above describe individual runs; they do not replace the
author record's 1021-test, 92.66% observation for the `792f0c7` source digest.
Final-head reruns and their commit identities are
reported with the integration handoff. No general shared-session V2 support,
automatic recovery, or additional model-backed acceptance is claimed here.

## Bootstrap timeout mitigation

An intermediate doctor run on acceptance `8f4fdb7` hit the existing two-second
bootstrap deadline while awaiting a real SQLite task-store GET, before the
cancellation operation. Its unchanged focused case and full doctor rerun passed.
The underlying host-delay cause is unknown.

Author test-only commit `da51304`, applied to both upstream branches, separates
bootstrap from the cancellation behavior: bootstrap has a ten-second budget and
ten-millisecond polling instead of a two-second budget and one-millisecond
polling. The two-second cancellation and settlement assertion deadlines are
unchanged; there are no added retries, skips, or removed assertions. The author
acceptance JSON records eight consecutive six-case SDK runs (48 checks).
Production source and the six smoke/harness files are unchanged by this commit.
