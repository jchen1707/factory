# Nested runtime accounting assessment

Assessed 2026-09-07 UTC (clock checked at 04:12:37 UTC) from retained evidence only. No sandbox or model was
launched, no credentials/settings were changed, and no source code was edited.

**Factory has no separate child-launch accounting, approval or budget boundary.
Actual child execution and parent/child usage aggregation remain unproven for the
completed CRUD builder.** These are separate conclusions: the source limitation
is established; the retained transcript does not establish underbilling, a child
count, or unauthorized child work. Do not manufacture child usage or disable
collaboration based on those unknowns.

## Observed completed execution

The completed parent transcript's SHA-256 matches its retained audit:
`43e5ec1573c821c84708a4630f7a9ede10c23e6cca5e1ef84dcf74e0d84c2cb8`.
Its 132 events include 12 collaboration events: six wait starts and six wait
completions, with empty receiver IDs and empty agent-state mappings. There is no
retained spawn identity. The final parent `turn.completed` reports 4,274,076 input,
4,179,840 cached input, and 29,554 output tokens. Those are the reported parent
stream totals; their inclusion or exclusion of child work is unknown.

A builder statement that it delegated work is not a child lifecycle record.
Likewise, a completed wait with no receivers is not proof of either a launch or
absence of children. The historical invocation used legacy exec; app-server
capability schemas alone do not fill missing historical exec events.

## Exact runtime protocol capabilities

The retained schemas were generated from **codex-cli 0.146.0**, the CRUD template's
executing runtime. `schema-assessment.json` extracts the relevant definitions.
Schema support is distinct from observed notification delivery.

| Signal | Exact schema supports | What remains unmeasured |
| --- | --- | --- |
| `collabAgentToolCall` | Spawn/send/resume/wait/close tool; sender ID, receiver IDs, target statuses; optional requested model and effort | Actual child identity delivery on this runtime/transport; requested settings may differ from executed settings |
| `subAgentActivity` | Agent thread ID/path and started/interacted/interrupted kind | Whether emitted in this scenario; it contains no usage |
| Thread metadata | `parentThreadId`, shared `sessionId`, spawn source with parent/depth, optional role/nickname, fork source | Availability for runtime-created children, including transient or already-closed threads |
| `thread/tokenUsage/updated` | Thread ID, turn ID, cumulative `total`, current `last`, context window | Subscription delivery for children and whether cumulative counters include descendants or inherited fork history |
| `thread/list` | Explicit subagent source filters, cwd filter, pagination | Historical children may require archived search; default source filters omit them |
| `thread/read` | Read retained turns/items without starting a model | Thread summary schema contains no usage field; it is not itself a token accounting endpoint |
| Server requests | Tool/file/permission approval and dynamic-tool requests | No dedicated child-launch approval request is declared |

Current official documentation describes per-thread usage notifications and stored
thread reads. It also documents newer parent/ancestor list filters, but those are
absent from the retained 0.146 schema and must not be assumed available here.
[OpenAI App Server documentation](https://learn.chatgpt.com/docs/app-server).

Official documentation says children perform their own model/tool work and inherit
sandbox/permission settings. Those Codex permissions concern tool capability; they
do not implement factory's per-invocation approval ledger. The documentation does
not settle parent-total aggregation in the measured binary.
[OpenAI subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Confirmed factory source limitations

These findings come from source snapshots retained with this assessment, not a
new nested runtime execution:

1. `execution.guard` and `accounting.begin` gate and record factory-scheduled
   invocations. A runtime child spawn has no factory callback invoking either.
   `AppServerAdapter` launches one worker for the scheduled parent; the worker
   supplies `approvalPolicy: never` to its parent thread. It fails if an unexpected
   server request arrives, but receives no schema-defined child-launch approval
   request. Nested model work therefore has **no distinct factory approval,
   project admission, or known-spend check** in this code path. Approving
   a parent is not a separately recorded child approval. Under the requirement to
   approve every invocation, approving the parent alone does not satisfy it.
2. `app_server_worker.py` retains received raw events, then ignores normalized
   processing when `params.threadId` differs from the parent. It does not subscribe
   to or discover children. Parent completion ends the worker; child terminal
   lifecycle is not awaited or accounted separately. This does not prove runtime
   shutdown leaves a child alive; lifecycle behavior is another measurement.
3. `accounting.collect` constructs telemetry for one parent ID and reconciles one
   scheduled invocation's cost record. Legacy `parse_events` sums top-level
   `turn.completed` usage and does not normalize collaboration identities.
   Neither path creates child invocation records or reconciles child model/effort.
4. App-server usage/pricing completeness describes the observed parent counters
   and request fragments. No field establishes that all descendants are known.
   A complete parent estimate must not be presented as verified whole-tree cost
   until aggregation is measured. If counters include children with different
   executed models, parent-model pricing may misattribute them; if counters exclude
   children, total spend may omit them. **Both are hypotheses, not established
   failures in the historical run.**

Existing budget checks occur at factory scheduling boundaries, not continuously
before every model request within a parent. Nested work does not acquire a new
factory boundary merely because it uses another thread. This explains the scope
of enforcement without claiming any specific run exceeded its budget.

## Prelaunch veto and a bounded fallback

`item/started`, `item/completed`, and child-activity records are notifications,
not requests awaiting a client response. They supply observability, not a documented
atomic prelaunch veto. Interrupting after such a notification cannot prove that
the child made no request first. The actual 0.146 server-request union has no
collaboration-spawn approval request. Generic permission requests govern capabilities,
not factory invocation admission. A host-serviced dynamic tool is a different
mechanism and would require explicit orchestration integration. Hook event names
alone also do not prove a usable prelaunch factory approval protocol.

Official configuration documents **`features.multi_agent = false`** to turn off
the built-in collaboration tools, and **`agents.enabled = false`** as another
multi-agent-tools switch. A reversible invocation-local override is a candidate
fallback while every child cannot be approved/accounted: for example,
`-c features.multi_agent=false`, applied at launch rather than writing the user's
trust/configuration store. The concurrency limit is not equivalent to an off switch.
[OpenAI configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

These switches are documented current configuration, **not yet an observed
0.146 refusal**. The retained 0.146 app-server config schema permits additional
properties and does not enumerate these switches, so it cannot establish their
effect. Before relying on a fallback, inspect the exact binary's feature metadata,
then measure that the tool is absent/refused on an explicitly requested spawn,
that no child identity appears, and that the primary task still works. Probe exec
and app-server separately; accepted flags were previously insufficient evidence.
If both switches exist in the executing runtime, inspect precedence before choosing
the override. This disables built-in delegation tools only; it is not proof that
an arbitrary shell-launched second model process is prevented.

No fallback was enabled in this assessment. It is a concrete option to honor the
every-invocation approval/budget boundary, not a claim that collaboration must be
removed permanently or that billing was missing. Implement observable child
admission or measure/enforce the fallback before claiming that boundary is complete.

## Concrete next measurement

This is an acceptance experiment proposal, not a re-plan of factory architecture.
Use an isolated scratch home/store, an explicitly authorized bounded parent plus
one child, and the exact runtime/version/config; no product ticket or delivery.
Retain raw events before normalization and preserve original protocol evidence.

1. **Recover identities without model work first.** In the owning sandbox, start
   a metadata-only app-server and use `thread/list` with explicit subagent source
   kinds, exact cwd and pagination; filter returned `parentThreadId` locally.
   Also inspect archived results. Use `thread/read(includeTurns=true)` for the
   known parent and any identified children. Never resume or send turns merely
   to read old history. If a returned local rollout path is required for usage,
   retain a scoped read-only copy and mark its format unstable. Do not scan human
   sessions or treat missing listing entries as proof no child existed.
2. **Controlled active trace.** Approve one scratch parent invocation whose only
   task is to spawn exactly one child, with no grandchildren, no file edits and
   a fixed short response. Record requested and actually observed model/effort
   separately. Keep the parent waiting while the child runs. Capture all thread
   IDs, parent links, lifecycle events, usage events, and request timing until
   both terminate. Discover whether the parent connection receives child events;
   if not, establish the executing version's subscription mechanism first.
   `thread/resume` can load/subscribe rather than simply read, so do not introduce
   it blindly into an active-child measurement.
3. **Establish a token conservation relation.** Retain initial counters for every
   observed thread, especially a forked child's inherited baseline. Attribute
   actual request deltas by thread and response identity when exposed; preserve
   duplicate/out-of-order events rather than summing repeated snapshots. Compare
   parent cumulative change with independently identified parent request usage
   and child request usage after the child and parent both settle. A quiet parent
   interval can show when child work affects parent counters, but cannot exclude
   delayed roll-up at parent completion. If only ambiguous aggregates exist,
   report that limitation and obtain matching runtime implementation or provider
   request evidence; do not infer inclusion from plausible totals or estimate
   token counts from text. Never add parent and child totals until overlap is known.
4. **Replay through factory accounting and approval.** Feed the retained real trace
   through unchanged production parsing/accounting in a new scratch Store. Compare
   records with the measured thread tree and per-request usage. Repeat collection
   to verify idempotence. Record the parent approval and the absence/presence of
   any child guard before launch. A separate bounded fixture can put known spend
   at the configured threshold *after parent approval* and observe whether a child
   launch causes a new check; do not exceed a real spending budget to test it.
5. **Define completion honestly.** Acceptance needs identified successful child
   lifecycle, executed model/effort provenance, baseline-safe usage, measured
   overlap semantics, and an explicit approval/budget scope. A missing signal
   remains unknown; it is not zero usage. Child attribution should remain distinct
   from factory's independent reviewer stage.

No new run is needed to establish the source gaps above. Runtime measurement is
needed before choosing a correct aggregation or child-admission implementation.

## Retained evidence

`artifacts/runtime-nested-accounting/` contains the hash-verified completed parent
audit, exact schema copies, extracted capability assessment, and snapshots of the
five relevant factory modules. `inspect.py` reproduces this read-only inspection;
`evidence-sha256.json` binds the retained files. No model or sandbox execution was
performed during this assessment.
