# Upstream-stack validation

The [exclusive acceptance record](./v2-exclusive-acceptance.json) describes the
author feature's model-backed live runs. Those model runs were not repeated for
this upstream stack. Its production Python tree and the V1, V2 session, V2
exclusive, and JavaScript probe harness files are byte-identical to author
commit `e86042a`. The production digest therefore remains the concatenated
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

Coverage percentages above describe individual runs; they do not replace the
author run's 92.67% observation. Final-head reruns and their commit identities are
reported with the integration handoff. No general shared-session V2 support,
automatic recovery, or additional model-backed acceptance is claimed here.
