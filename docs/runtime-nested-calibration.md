# Nested runtime calibration

## Metadata-only checkpoint — 2026-09-07 UTC

The completed factory builder **did spawn two identifiable child threads**. This
supersedes the historical uncertainty about whether children launched; their token
usage and inclusion in parent totals remain unknown.

The probe started only `factory-build-crud-20260907`, verified capability metadata
and invalid inherited template authentication, then used Codex **0.146.0**
metadata-only app-server calls. No thread start, resume, model turn, configuration
write, tracker action, or product edit occurred. The sandbox was stopped afterward.

Parent: `01a079c0-a16b-7ff3-b80a-82cb727dd3ba`, exact cwd
`/Users/james/factory-crud-verification`.

| Child role/path | Thread ID | Retained own turns |
| --- | --- | --- |
| `/root/standards_review` | `01a079cb-95b5-7111-8537-00878eeb088a` | Two completed turns after child creation |
| `/root/spec_review` | `01a079cb-b0de-7d32-a009-36f94c18fa16` | Two completed turns after child creation |

Both children have the exact parent ID in `parentThreadId` and their spawn source,
with depth 1 and the expected cwd. Parent `thread/read` includes matching
`subAgentActivity` start/interact items absent from the legacy exec JSON audit.
These are runtime identity/lifecycle records, rather than the model's delegation
claim. This is internal builder collaboration, not the factory reviewer stage.

`thread/list` used explicit subagent source kinds, the exact cwd, pagination and
both archived/non-archived filters with `useStateDbOnly=true`. Only children linked
to the known parent were subsequently read. `thread/read(includeTurns=true)`
recovered their history. `thread/loaded/list` was empty before and after, confirming
these reads did not load model threads.

The reads contain **inherited parent history**, including parent turn IDs marked
interrupted in the child copies. The spec-review child's inherited history even
contains the standards child's start item. Therefore embedded activity cannot be
attributed to the containing child without ownership checks. Actual child session
IDs differ from the parent session ID here despite the schema's shared-session
description; `sessionId` alone is not a reliable parent grouping key for this case.

The returned model provenance is only `modelProvider: sandboxd`. There is no
executed model, effort, token usage or request pricing in the captured child reads.
Their returned rollout paths are retained but were not opened during this phase.
No parent/child totals were added or subtracted.

Evidence: `artifacts/runtime-nested-calibration/` contains the read-only RPC script,
preflight, raw request/response stream, identity/turn summary, stopped inspection,
and `evidence-sha256.json`. `summarize.py` checks parent links, excludes inherited
turn IDs, verifies two completed own turns per child, and asserts no start/resume/
turn methods were sent. No human session was inspected.

## Active one-parent/one-child trace — 2026-09-07 UTC

The bounded active trace completed on the same exact runtime, in a new isolated
scratch cwd and with no file edits. One Sol/low parent explicitly requested one
Terra/low child with no inherited conversation. The child ran only `sleep 8` and
returned its fixed message; both turns completed. No additional model turn was
launched during recovery of the evidence.

Parent: `01a07a35-edd5-7812-b730-ed3f21857726`.
Child: `01a07a35-f918-7883-b727-bfecc59f8f3a`.

**Child lifecycle and token usage streamed automatically on the parent's
app-server connection.** No child resume or subscription call was needed. This
establishes a usable observation channel in this configuration. The trace emitted
no server approval requests; child creation did not pass a factory admission guard.
Lifecycle notifications still provide no atomic prelaunch veto.

The immediate `thread/list(useStateDbOnly=true)` returned no children for the exact
cwd, causing the probe's final listing assertion to fail after both turns had
successfully completed. The failure was retained, not replaced with another model
run. A metadata-only recovery read the exact child ID from its observed start item
and the parent ID. Both were recovered with correct parent/cwd links. The reason
for the immediate empty state-DB listing is unestablished; event identities provide
stronger discovery evidence here.

Scoped reads of the exact runtime-returned rollout paths confirmed executed turn
configuration: Sol/low for the parent and Terra/low for the child. They also retained
token-count records. This is stronger than merely assuming a requested spawn model
was honored. No broad session-directory scan occurred.

| Measured stream | Positive usage increments | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| Parent Sol/low | 3 | 35,979 | 23,424 | 141 |
| Child Terra/low | 2 | 23,699 | 11,008 | 112 |

Both fresh streams started with cumulative counters equal to their first `last`
usage, and their scoped rollouts contain only their own turn context. Every later
cumulative difference equals that thread's own `last` breakdown, for all token
categories. The completed parent total equals its three own increments and does
not absorb the two child increments, including at final completion. Thus the
counters are disjoint **for this measured fresh-child trace**. Their measured sum
is 59,678 input / 253 output; this is not a claim about historical forked children,
subscription charges, or other versions/configurations.

### Offline production replay

`active/replay.py` replayed the captured runtime response/notification payloads
through the current production worker and accounting code using a fake pipe and a
separate local SQLite Store. Only JSON-RPC response IDs were remapped for the
worker's request order; no token, model, lifecycle or thread value was synthesized.
This was an offline replay, not a second runtime acceptance launch.

The result confirms the source gap against real measurements: raw child events
survive, but the normalized usage and sole invocation record contain only the
parent's 35,979 input / 141 output. The measured child's **23,699 input / 112 output
are unrepresented in that record**, and no child invocation is created. Repeated
collection is idempotent. This quantifies an omission for this trace without
asserting that historical parent totals had the same semantics. No child USD
estimate was fabricated from absent request-tier/pricing evidence.

The original replay is frozen in `active/baseline-before-collector-fix/`; subsequent
collector corrections must use separate evidence rather than overwrite this
measurement. Its source snapshot and digest index preserve the before-fix scope.

### Remaining boundary and next step

The signals suffice to observe and reconcile this one fresh child after launch;
they do **not** yet provide an atomic factory child approval/budget veto, cover
forked baselines, or prove cleanup of children that outlive a parent. Persisted
rollout model/effort extraction is also an unstable local-format dependency that
needs explicit version handling before production attribution relies on it.

Recommendation: before claiming every-invocation control, measure the documented
invocation-local no-nesting fallback against this exact runtime. Inspect feature
metadata first, then use one short requested spawn whose expected result is tool
unavailability, while its parent task still completes. Confirm no child identity
or usage appears. Current docs name `features.multi_agent=false` and
`agents.enabled=false`; neither switch's actual 0.146 effect was tested here.
No fallback, production config, existing run, or worker source was changed.
Root controls whether and when to run that next bounded experiment.

The build VM was stopped and released after metadata recovery. Evidence is under
`artifacts/runtime-nested-calibration/active/`, including the original failed
listing assertion, successful terminal/usage trace, scoped rollout recovery,
conservation assertions, offline replay, and stopped inspection. The root digest
index binds both metadata and active phases. FRO-12 and its private clone were
preserved.
