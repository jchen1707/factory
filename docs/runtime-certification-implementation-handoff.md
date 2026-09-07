# Runtime certification and delegation implementation handoff

Status: implementation in progress; feature is not complete.

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
