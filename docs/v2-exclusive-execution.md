# Experimental V2 Exclusive Execution

This is a restricted, opt-in execution profile for the pinned upstream version
in [compatibility.md](./compatibility.md#runtime-support), not general V2
shared-session support. V2 is the default upstream API; V1 requires
`OPENCODE_API_VERSION=v1`. Without `OPENCODE_V2_EXECUTION=exclusive`, A2A
execution remains disabled even when V2 is selected by default.

## Required deployment boundary

Set `OPENCODE_V2_EXECUTION=exclusive` **only** for a dedicated instance pair:

- Run exactly one gateway process and one upstream process with fresh private
  upstream state. Do not use an everyday CLI/TUI background service.
- Put both processes in a dedicated network namespace. Bind upstream to
  `127.0.0.1`; publish only the gateway's A2A listener, never upstream's port.
  Loopback on the ordinary shared host is not network isolation.
- Use separate random upstream Basic credentials, available only to this pair.
  Do not share pairing tokens, cookies, authenticated URLs, provider credentials,
  runtime storage, or container administration access with A2A consumers.
- Protect configuration and workspace configuration/plugin discovery paths from
  other writers. No custom plugins, MCP servers, warming, background subagents,
  or external schedulers. The adapter checks effective configuration and plugin
  inventory; these checks are not a replacement for filesystem/network controls.
- Use `A2A_TASK_STORE_BACKEND=database` and a protected SQLite database. Do not
  run multiple gateway workers or share upstream storage between server processes.

The adapter enforces a loopback URL, upstream credentials, explicit workspace,
database-backed ownership, the exact upstream pin, and restrictive session
permissions. It cannot verify network topology or defend against a privileged
host/container administrator. Selecting `exclusive` acknowledges the operator
preconditions above; a Python lock alone never establishes them.

The initial tool policy denies `*`, allows `question`, asks for `read`, and
explicitly denies `external_directory`. Shell, editing, subagents, outbound
`a2a_call`, and arbitrary tools are unavailable. Do not weaken this policy to
enable unreviewed producers. This remains a single-tenant shared-workspace
boundary, not hostile-tenant filesystem isolation.

Pinned upstream applies session rules after agent rules and filters both model
definitions and dispatch entrypoints in
[session context](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/session/context.ts#L127-L139)
and the [tool registry](https://github.com/anomalyco/opencode/blob/v2.0.28/packages/core/src/tool.ts#L225-L295).
The wildcard deny therefore also excludes Code Mode `execute` and its direct
HTTP `fetch`, session movement, and privileged builtin namespace tools even
when their leaf implementations omit a permission assertion. The adapter
rechecks the exact persisted session policy before admission. Fresh upstream
state is necessary: previously saved grants are not trusted by this profile.

## Execution and failure semantics

Only sessions created by this running gateway are executable. Unknown,
preexisting, imported, child, or post-restart session IDs cannot be adopted.
Preferred-session reuse additionally requires an existing owner binding under
the ownership lock; knowing a freshly allocated but not-yet-bound ID cannot
claim it before its creating request commits ownership.
Persistent task history remains queryable after restart, but execution requires
a new context/session. Follow-up in a healthy live session retains context.
Execution uses the configured workspace only. Per-request location changes,
including another directory inside that root, are explicitly rejected rather
than silently ignored when continuing a bound session. Default session-only V2
transport retains its existing location mapping.

Same-session requests serialize; different sessions may run concurrently.
One bounded native `/api/event` connection serves the instance. Subscription
readiness requires `server.connected`. Admission uses a fresh input ID exactly
once. Success requires observed own enqueue/delivery, sequenced assistant steps,
a later terminal, native foreground settlement, empty inbox, and matching
persisted user/assistant/idle projections. Text completion or inactivity alone
cannot complete a task.

Disconnect, overflow, unknown work, ambiguous HTTP admission, missing terminal,
or uncertain cancellation fails without claiming success or rollback. The
session and its uncertain execution capacity remain quarantined; there is no
automatic retry, replay database, or polling-based transcript recovery. A dead
global feed fails the instance closed until it is replaced with a fresh pair.
The experimental session log is deliberately not used.

The HTTP request limit also bounds active V2 execution through a separate
admission budget. The stream limit accounts for the shared connection. Observer
state is bounded; healthy idle sessions can be retired in LRU order at capacity.
Retired contexts must start a new session rather than adopt an old ID.

Cancellation distinguishes local waiting tasks from admitted native work:

- Local queued cancellation submits nothing and does not interrupt its owner.
- Active interrupt is session-wide, bound to the original task/input, and blocks
  successor admission until the interrupt operation settles. Only observed
  interrupted settlement is reported as canceled. Natural completion wins its
  race truthfully; an uncertain interrupt is failed, not canceled.
- Native pending-inbox deletion alone is not a cancellation proof. If it cannot
  be reconciled with a terminal, the session is quarantined.

## Interventions and unsupported operations

Streaming tasks support read approval (`once` or `reject`) and question-generated
forms with validated string/multiselect answers. Callbacks use existing
task/session/principal-bound tracking, expiry, and native 204 replies. Duplicate,
cross-owner, expired, unsupported, and post-observation-loss replies reject
before mutation. Callback eligibility is separate from native resolution events,
so a successful answer can still produce the working transition.

Unsupported model/workspace metadata is rejected by field presence, including
empty, null, and wrong-typed values. Invalid session/directory metadata cannot
silently fall back to a fresh session or default model/location.

Unary text requests await the same proven execution outcome. **Unary
interventions are unsupported**: an unexpected intervention fails and quarantines
the session without approval. Rich/generic form kinds, `always` permission grants,
attachments, request-scoped models, arbitrary session mutations, and `a2a_call`
are not advertised or implemented in this profile. General shared-session
execution and transparent reconnect/restart recovery remain unsupported.

## Reproduce the live evidence

Requires Docker, a local pinned OpenCode binary, `uv`, a real configured OpenAI
credential in the environment, and the stated local image. Credentials are
loaded through private files, never printed or passed as positional arguments.
The harness creates only disposable containers, workspaces, and credentials;
it does not contact or stop the preexisting OpenCode service.

```bash
# Native isolated single-flight/continuation/intervention proof:
bash scripts/live_opencode_v2_exclusive_probe.sh

# Real gateway REST/JSON-RPC, ownership, interventions, cancellation, concurrency:
V2_PROBE_IMAGE=mcr.microsoft.com/playwright:v1.59.1-noble \
V2_PROBE_A2A=1 bash scripts/live_opencode_v2_exclusive_probe.sh

# Independent fresh pair for each negative scenario:
for scenario in gap lost-terminal lost-admission provider-failure intervention-gap expired-intervention; do
  V2_PROBE_IMAGE=mcr.microsoft.com/playwright:v1.59.1-noble \
  V2_PROBE_A2A=1 V2_PROBE_NATIVE=0 V2_PROBE_CASE="$scenario" \
    bash scripts/live_opencode_v2_exclusive_probe.sh
done
```

`V2_PROBE_NATIVE=0` skips only the separate native proof, not the A2A model call.
Provider-failure uses a synthetic invalid credential, not a fake provider.
Fault injection changes transport delivery while upstream execution is real.
The test-only proxy/control endpoint is not a shipped server capability.
Sanitized route counts and pass/fail assertions appear on stdout; model text,
credentials, raw events, workspace paths, and opaque IDs are not evidence output.
