# Runtime certification and delegation implementation handoff

Status: implementation in progress; feature is not complete.

Latest checkpoint: root workflow launch wiring is now implemented in this worktree. The
foundation-only statements below describe earlier checkpoints; see **Workflow launch
integration checkpoint** at the end for current behavior and remaining work.

## Objective and approved scope

Implement the approved automatic certification, Astra resolution, and controlled delegation
plan. Synthetic workflows are sufficient. Do not finish/harden CRUD, resume FRO-12, change
historical tickets or require customer work to prove acceptance. Preserve live concurrency four.
The proposed separate agent cap eight, two children per parent and depth one are approved
implementation targets, not activated live settings.

Plans are under `.agents/plans/feat-runtime-certification-and-delegation/` in the implementation
worktree. Both were approved; implementation status is now in progress. Preserve their original
Goal/Approach/Steps and append findings/progress rather than rewriting the approved design.

## Worktrees and live boundary

- Factory: `/Users/james/factory-runtime-certification`, branch
  `feat/runtime-certification-and-delegation`, based on main `a82d91c` (merged #89).
- Shared source: `/Users/james/harness-runtime-certification`, same branch name based on
  `harness@v2` `cc7bf33`. Source files only; no vendored tree was hand-edited.
- Live checkout `/Users/james/factory` was left detached at `a82d91c` so new code/schema
  cannot be picked up by the existing services. Live schema remains 5; no live settings,
  migration, writer restart, template deployment, tracker or forge write occurred.
- The two pre-existing untracked frontend/nemoclaw reports remain untouched.

## Step 1 findings and implemented correction

Astra discovery was reproduced on Codex 0.146.0 and corrected by testing 0.153.4 in the SAME
fresh sandbox, with unchanged authentication. Both experimental initialization settings on
0.146 omitted Astra. The candidate 0.153.4 advertises it and actual high/xhigh turns completed
with schema-valid output. Host CLI 0.153.4 also advertises Astra. Host account classification
is ChatGPT Pro; sandbox account/read reports no local account and requiresOpenaiAuth=false,
consistent with its separate provider context. No credentials were copied or changed.

Candidate installed only under `/tmp/factory-codex-candidate` in experiment VMs. Their default
image/binary remains unchanged; probes supplied the candidate PATH. This is not a production
runtime upgrade or a claim that default `select` on these VMs uses the candidate binary.

Current candidate protocol supports dynamicTools registration. A real Astra parent invoked
a host-serviced request tool, received a pending handle, then retrieved the result through
another registered tool after app-server restart. Native spawning was disabled. This proves
transport only: no actual delegated child was launched by this probe.

The compatibility audit exposed a counter-scope difference: 0.153.4 restarted total counters
on app-server restart, including ordinary completed-thread resume and interrupted recovery.
The first unchanged-worker recovery correctly reported incomplete usage instead of subtracting
an invalid historical baseline. Original failing records are retained.

The worker and AppServerAdapter now accept an explicit compatibility-declared `usage_scope`
(`thread` default or `connection`). Only an explicitly certified connection-local runtime uses
zero as the new process baseline. Unknown scope is refused; regression alone never triggers a
reset inference. Existing thread baseline behavior remains. A red-before-green regression
and the focused worker/model suite passed. New real final-worker build/review checks passed
all six compatibility requirements, including recovery input 9,864 and 9,457 respectively.
The legacy live worker was not edited, so its existing manifests are not invalidated in place.

`agent/runtime_diagnostic.py` is a standalone bounded metadata diagnostic:

    uv run python -m factory.agent.runtime_diagnostic --model gpt-6-astra --effort high

It generates the executing binary's schema before deciding whether account/read is supported,
retains catalogue findings even if account inspection is unsupported, uses refreshToken=false,
and emits only selected account classification fields. No thread/model turn is started.
Review corrections handle unknown classification values and malformed account objects.

## Source foundations (not operational features yet)

- Layer A adds delegation request schema and delegation/certification instructions.
  A schema test refuses caller-supplied approval/authority/parent ownership and invalid modes.
  No consumer has been synced to these unmerged changes.
- Layer D has a schema 5→6 DDL draft for certification jobs, agent leases and delegation
  requests. RuntimeJobs covers idempotent fingerprinted requests, durable reads, fenced job
  ownership, sequential agent admission/draining and child-before-parent finalization.
- These APIs are not wired into application launches, a certification runner, broker,
  console, cancellation/recovery, or writable child integration. `finish_certification`
  trusts its control-plane caller; the future service MUST validate evidence first and
  reconcile detached holders after lease expiry before launching another paid probe.
- A consistent SQLite copy migrated 5→6 with every prior row digest unchanged: 1,892 rows,
  quick_check=ok. Only the three new tables were added. Live schema was NOT migrated.
  The DDL may evolve before service integration; repeat rehearsal after DDL changes.

## Evidence and experiment state

Everything is local under `/Users/james/factory/artifacts/runtime-certification-implementation/`:
`host-standard.json`, `host-experimental.json`, `host-final-diagnostic.json`,
`sandbox-old-False.json`, `sandbox-old-True.json`, `sandbox-candidate.json`, generated
host/candidate protocol, install logs, dynamic-result.json, and Astra attempt handles/specs.
Each role retains original and final canary/interruption/recovery records, usage observations,
identity, isolation result, digest indexes and exact candidate-runtime manifests.
`migration-rehearsal.json` and mode-0600 `schema5-rehearsal.db` retain copy-only migration proof.
Do not publish the private database or raw transcripts.

Run fixture: `f38b67392efd499c`, ticket key COMPAT-EXTENSION in the separate validation store.
Build/review names are `factory-build-crud-live-20260907-f38b67392efd499c` and
`factory-review-crud-live-20260907-f38b67392efd499c`. Both were stopped after measurements.
No other sandbox was stopped or modified. The candidate package remains only in these VMs.
Full raw runtime files are also under the live clone/review per-run protocol mounts, but
there is no corresponding live database run.

The initial factory gate report failed formatting, a new-test Optional typing check, and an
old migration fixture that removed schema-5 tables but left new schema-6 tables before stamping
version 4. Those were corrected; use `factory-gates-final.json` for the final check result.
The fake suite does not prove real compatibility. Mypy covers configured paths; artifact
scripts are not part of its production coverage. Review found an account-schema support gap
and malformed-account handling concern; both were corrected before the final run.

## Exact remaining work

1. Finish/review the runtime diagnostic and source-contract checkpoint; retain candidate
   version/counter-scope evidence. Prepare a pinned template change through the image owner
   before production rollout; do not install the candidate globally or change live routing.
2. Complete and merge shared contracts on harness@v2, then sync exact merged content through
   the normal consumer process. James owns merges. No PR is opened by this implementation
   command unless requested; do not claim consumer freshness before it has been measured.
3. Complete Step 3 launch integration and durable reconciliation. The follow-up below adds
   guarded reservations and real transaction races; these are not yet connected to launch sites.
4. Implement automatic certification service: generation marker, full fingerprint, source-owned
   probe contracts, bounded/paid job scheduling, evidence validation, CLI/daemon resume,
   launch-time revalidation and status. No name-based or unchecked-scope bypass.
5. Implement host-serviced read-only delegation using the proven transport, persistent calls,
   per-child approval/usage, fair agent slots and safe subtree suspension/recovery.
6. Implement isolated-write child environments and serialized dirty-work-preserving integration,
   then run controls, policy snapshots and the remaining synthetic acceptance matrix.
7. Run final source/consumer gates and reviews; update both approved plan checklists accurately.
   Only then prepare migration/deployment/activation requests. Existing approvals do not
   authorize live schema 5→6 application or template deployment.

Do not mark the overall plan implemented from the Step 1 probes or partial persistence tests.

## Final checkpoint verification

Factory canonical final report is PASS: Ruff check, Ruff format --check, mypy and pytest
all exited 0 with empty output tails; none skipped. Two independent review axes checked
this bounded foundation, found the two diagnostic issues above, and confirmed their fixes.
This is not a full-feature review or acceptance claim. Shared-source final checking is
recorded separately in harness-check-final.txt after the committed source snapshot.

## Admission follow-up checkpoint

The implementation worktree now has one `RuntimeJobs.schedule_agent` reservation entry point.
The earlier unchecked `admit_agent` primitive was removed. Within one SQLite transaction it
checks project/run capacity ceilings, delegation enablement, same-run/attempt parent ownership,
depth one and child limits, per-invocation Approval, known run spend and lifetime attempt limits.
Successful admission consumes the exact invocation's approval atomically. A queued/refused
reservation does not consume approval. Existing active reservations drain when limits decrease;
re-reading a reservation is NOT permission to spawn a second process.

`RuntimeState.start_invocation` now rejects replay with a different run, attempt or role even
when metadata is identical. This closed a reproduced ownership weakness. Review also found
child attempt reset and malformed approval-mode fallthrough; both have red-before-green
regressions and were corrected. Both review axes confirmed their findings addressed.

`tests/integration/test_runtime_job_races.py` starts independent Python controllers with
separate SQLite connections. One wins the last agent slot; simultaneous requests join one
certification job and only one claims its lease. These are actual database contention tests,
not real sandbox/model acceptance. The focused admission/race suite passed 39 tests.

No new schema DDL, shared source, live database, sandbox, routing or service changes occurred
in this follow-up. The previous schema-copy rehearsal remains applicable to unchanged DDL.
The new reservation method is NOT called by existing application launch sites yet. Next:
wire invocation-specific admission into accounting/execution and reviewer fan-out, with durable
launch ownership and terminal reconciliation before releasing capacity. Do not attach a simple
`schedule_agent` check to launch without that reconciliation: duplicate reservation success
must never become duplicate paid execution. Then continue the certification service and broker
steps above. Approval keys for new invocations are exact invocation IDs; adapt operator-facing
approval display together with launch wiring, preserving legacy attempt approval behavior.

Factory final gate evidence is `artifacts/runtime-admission/gates-final.json` in the
implementation worktree: PASS, all four gates exit 0, empty output tails, none skipped.
Mypy includes the new production/test paths. The first run failed three test-style lint
checks; those were corrected. Overall plan status remains implementing; this checkpoint
does not complete Step 3 or the full feature.

## Durable launch follow-up checkpoint

`AgentLaunches` now owns a bounded paid-launch operation over `RuntimeJobs` and the existing
sandbox adapter. It atomically reserves capacity, consumes exact invocation approval and writes
an effects-ledger intent before calling the adapter. An immutable contract records the handle,
parent identity, script digest and environment digest (not environment values). Only the winning
controller calls `exec_detached`; an existing intent returns False and must be observed, never
interpreted as permission for a retry. An adapter exception retains intent and capacity because
spawn may already have succeeded. An older bare reservation without an intent is refused as
ambiguous, not adopted as fresh launch permission.

`RuntimeState.transaction` supports nested savepoints so reservation and intent commit together.
`AgentLaunches.start` refuses an enclosing transaction: no adapter call can precede the outer
commit. A storage-failure regression proves approval/capacity/effect rollback together. Existing
launch directories cannot be assigned to another invocation; unrecorded exit/heartbeat/holder/
process-group evidence refuses a new launch without deleting anything.

`observe` recovers the stored handle. `reconcile` collects terminal usage through an idempotent
control-plane callback before freeing a slot; collection failure retains capacity. Running and
orphaned/ambiguous observations do not release it. Active descendants still prevent parent
finalization. This is intentionally not a complete orphan recovery policy: an intent with no
conclusive terminal evidence remains held, and targeted recovery must be implemented before
claiming automatic recovery parity.

Focused tests: 49 pass across agent launches, runtime jobs and real SQLite controller races.
The new process race invokes the launch service from two independent controllers and records
one sandbox-boundary spawn. Lost acknowledgement, store reopen, stale evidence, immutable
contract, reservation replay and atomic rollback have regressions. These use a fake sandbox
boundary, not a real model/VM compatibility measurement.

Two-axis review found a stale-evidence gap and a bare-reservation admission bypass; both were
reproduced and fixed before final verification. Standards review found no hard violations. Its
nonblocking concern remains: directory ownership scans historical launch effects under the
write transaction. Add an indexed canonical ownership representation before broad rollout;
any DDL change requires a fresh copy-only migration rehearsal.

Next work remains application integration, not a repeat of these tests: wire this operation
into builder, execution-brief/diagnosis and reviewer launch sites together with durable queued
requests and exact invocation approval display. Existing `execution.guard` uses legacy
attempt/group keys and advances launch counters before preparation; simply replacing
`exec_detached` would mishandle approval/capacity waits and controller restarts. Preserve those
legacy approvals while making new pending invocations recoverable without incrementing again.
Connect terminal collectors and targeted orphan/subtree recovery before releasing capacity.
Then implement the certification service and child broker/integration from Steps 4–8.

No workflow launch site calls `AgentLaunches` yet. Overall Step 3 and the full feature remain
unfinished. No live service/settings/database, schema DDL, sandbox, shared source or consumer
pins changed in this checkpoint. This turn adds source and isolated test evidence only.

Final canonical gate evidence: `artifacts/runtime-launches/gates-final.json` in this worktree.
PASS: Ruff check, Ruff format --check, mypy and pytest all exit 0, empty output tails, none
skipped. New source/tests are within configured mypy coverage. The pytest caveat applies:
fake sandbox tests establish controller behavior, not real runtime compatibility. Spec reviewer
re-ran the bare-reservation reproduction after the fix and confirmed no launch; both findings
are resolved. No full-feature acceptance claim is made.

## Workflow launch integration checkpoint

Builder, execution-brief/test-design/diagnosis and individual reviewer launches now use
`workflow_launches` and `AgentLaunches`; deterministic verification remains outside paid agent
capacity. The workflow attempt, state transition and prepared request commit together;
invocations are retained before launch (review bookkeeping is staged earlier). SQLite
savepoints cover nested accounting transactions; failed preparation also
refreshes the in-memory Context back to the committed state.

Capacity waits retain one prepared invocation. Reaping resumes the recorded script and handle
without rerunning preparation or advancing attempt/launch counters. Admission still checks
current approval, known spend and limits. Prepared inputs (prompt/schema/worker request),
candidate HEAD, environment digest, authority integrity, policy revision and compatibility
report are checked before launch. No environment values are copied into the prepared record.
The execution timeout starts from the durable launch timestamp, excluding time queued.

New planning phases use `run/<attempt>/planning` to keep their liveness/evidence independent
of the builder. Legacy planning rows continue to use recorded paths and filenames; builder
handoff loading follows that recorded directory. New approval prompts identify the exact
invocation, legacy attempt/group approvals are translated at the workflow guard, and the
console pre-fills the waiting invocation ID without submitting approval automatically.

Reaping and scheduling collect terminal invocation usage before freeing slots. Orphan recovery
signals only a recorded process group belonging to an owned launch, and releases capacity only
after a valid terminal exit and successful accounting collection. Missing/ambiguous evidence
remains held; an orphan observation alone cannot authorize another paid process. This is not
complete real-runtime orphan/subtree recovery acceptance.

Suspend/Cancel can retire never-launched preparations transactionally, fenced against racing
admission. Launched cancellation reconciles costs before archive/removal and refuses cleanup
while any owned lease remains active. Suspended terminal agents release capacity while parked.
`accounting.collect_invocation` permits cancellation collection without model routing or
tracker context. No native or broker child execution has been enabled.

Focused regressions cover queue/reopen/one launch, approval changes while queued, preparation
rollback, independent brief/builder evidence, reviewer capacity, queued Suspend, cancellation
accounting, stale input/candidate refusal and queue-excluded timeouts. Other accounting/admission
and cancellation suites pass. These use isolated SQLite/local Git and fake sandbox adapters,
not real model or sandbox compatibility evidence.

Two-axis review found and resolved queue-timeout, stale queued-input, queued suspension and
cancellation-accounting defects. Final spec rereview found no further hard defects. Standards
found no hard violations; a nonblocking duplication concern remains in owned-active-launch
selection across reconciliation and orphan signaling.

Next: finish Step 3's remaining ambiguous-holder/subtree recovery with the child lifecycle,
then implement Step 4 automatic certification (generation identity, full fingerprint, trusted
probe contracts, paid admission, validated attestation and launch revalidation). Continue
Steps 5–7 broker children, isolated-write integration and controls, then the real synthetic
matrix and cross-repository rollout checks. Do not repeat standalone launch-foundation work
or substitute ticket completion for feature acceptance. The optional timing observer now
starts at the actual builder launch, including queued resume, with a focused regression.

No DDL changed; the prior copy-only schema rehearsal still applies. Live services/settings,
database, sandboxes, shared source and consumer pins remain unchanged. Migration 5→6, template
deployment and activation still need separately reviewed rollout actions.

Final canonical evidence: `artifacts/runtime-workflow-launches/gates-final.json` in this
worktree. PASS: Ruff check, Ruff format --check, mypy and pytest each exited 0; all output
tails empty, none skipped. Mypy covered 134 source/test files including the new paths.
The first full suite had 1,038 passes and two failures expecting old approval-key text;
those assertions now require exact invocation IDs and the final run passes. The sandbox
fake caveat applies: this is not real-runtime acceptance or full-feature completion.
