# Runtime certification and delegation implementation handoff

Status: implementation in progress; feature is not complete.

Latest checkpoint: automatic certification service, complete fingerprint construction and workflow
launch enforcement are implemented in the isolated worktree. Real acceptance is INCOMPLETE. See
**Real compatibility acceptance checkpoint** at the end; earlier sections are historical.

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

## Attestation validation checkpoint

Added `certification.Certifications` and an immutable `CertificationIdentity`. The identity
requires sandbox generation, canonical spec digest (including mounts/layout), image identity,
absolute runtime path/version/binary digest, worker digest, authority/hook digest, probe-suite
digest and explicit measured usage scope. These are inputs from a trusted controller observer,
not measurements performed by this module. A missing identity component is refused.

The service requests the existing fingerprinted durable job, validates a host-authored report
at `<host-root>/<job-id>/compatibility.json`, and publishes the report plus its digest using the
existing fenced SQLite transition. A report file alone never authorizes a launch. Publication
requires all existing compatibility checks and exact job/fingerprint/identity binding. Validation
rereads all evidence, compares the published report and its digest, and refuses changed identity,
pending jobs, altered reports and evidence. Reopening the store preserves the attestation.
The caller must supply a freshly observed identity at publication and launch. Candidate model
output is not a host-authored report, even if it contains matching hashes or says `pass`.

Evidence roots are rejected beneath configured candidate-writable roots, including aliases.
Compatibility evidence reads now use bounded regular-file reads through directory descriptors:
absolute references, traversal and symlink components are refused. This also tightens manual
manifest validation; manual report evidence must reside beneath its report directory. Existing
manual manifests are not promoted to generation-bound certificates. Malformed check lists now
produce a classified refusal instead of an AttributeError. AppServerAdapter revalidates its
manifest/evidence and rejects a changed report before copying the worker/preparing a request.
This closes selection-to-preparation evidence staleness; it does NOT establish fresh runtime
identity at the actual detached launch boundary.

Focused verification: 28 tests passed across attestation publication/reopen, generation change,
evidence tampering, missing identity fields, candidate-writable roots, path escapes, malformed
reports and changed evidence after adapter selection. Path escapes, malformed check lists and
post-selection evidence changes were reproduced failing before their fixes. The new service's
first test failed because the implementation module did not exist. Fixtures use local files
and SQLite only: no runtime/model observations or automatic certification acceptance are claimed.
Both review axes found no blocking issues within this bounded checkpoint.

Next work is still Step 4 orchestration, not another attestation-only implementation:
1. Implement and measure the sandbox generation observer/fallback and exact executing binary
   provenance. Do not fill `CertificationIdentity` from retained job data or a version string.
2. Load source-owned probe contracts, perform deterministic preflight, and run/reconcile the six
   bounded real checks through common paid admission/accounting. Preserve detached probe ownership
   across lease expiry; an expired certification lease never authorizes duplicate execution.
3. Supply fresh identity to this service at publication and immediately before application launch;
   wire ensure/advance/status into build/reviewer/recovery and explicit automatic/manual settings.
4. Continue broker children, subtree recovery, isolated writable integration, remaining controls,
   real synthetic acceptance and rollout preparation. Synthetic feature assertions remain the
   objective; CRUD ticket completion and live workload changes are unnecessary.

No schema DDL, shared source, consumer pins, live services/settings/database, sandbox/template,
tracker or forge writes occurred. The earlier copy-only migration rehearsal still applies.
Overall plan remains implementing; the automatic runner and child features remain unfinished.

Final attestation gate report: `artifacts/runtime-certification-attestations/gates-final.json`.
PASS: Ruff check, Ruff format --check, mypy and pytest all exited 0, no skips, empty output
tails. Pytest ran for 124,949 ms. Configured mypy coverage includes the two new files (136
source/test files). The fake-sandbox caveat applies; no real runtime acceptance was performed.


## Runtime identity observation checkpoint

`SbxAdapter.generation` now reads the creation UUID from `sbx ls --json` on every call.
The previous concern was specific to `inspect`, which does not expose the UUID. A new
owned disposable VM proved that its UUID survives stop/start and changes after same-name
removal/recreation. No fallback nonce is needed for the measured v0.38.0 listing contract;
missing, malformed or ambiguous identity fails closed. No IDs are inferred from names.

`SbxAdapter.observe_runtime` reads generation and image identity, refuses capability
credentials, and executes a standalone metadata observer with isolated Python imports.
The observer requires an explicit absolute native ELF path, hashes the open executable,
queries its version through that same inherited descriptor, and rechecks bytes/path before
returning. It refuses wrappers and detects replacement during observation. Nonblocking opens
refuse FIFO inputs. The adapter rechecks generation, image and secret classification after
the query. No model turn or app-server is started by this observer.

The real template's `codex` is a JavaScript launcher, so hashing it is insufficient. The
measured native path is:
`/usr/local/share/npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl/bin/codex`.
This is evidence for the experiment image, not a new hardcoded production path or template
upgrade. The caller must eventually launch the observed absolute native binary with the same
environment; current application workers still resolve `codex` and are NOT wired to this API.
No claim of certification-to-launch race closure is made.

Real evidence is in this worktree at `artifacts/runtime-certification-identity/`:
- `created.json`, `stopped.json`, `restarted.json`: UUID
  `b9a92ac6-ea4a-41f7-8297-d4e3a8c6593d` remained stable.
- `recreated.json`: same name received `a45373ad-ce24-412b-ae67-c0bb7821146f`.
- `native-observation.json`, `isolated-native-observation.json`: actual 0.146.0 native SHA
  `cb5e8cb8a333a408ce6adbe0d4fad1845c69772c2216af7c1f88c98a11460dc6`;
  image digest `sha256:8ab3deaa75f9c10fb0e95d866a57280bc1494950c1a90b2cc636c8b1391fd574`.
- `same-version-changed-binary.json`: appending bytes to a disposable `/tmp` binary copy
  retained the version while changing the observed SHA. The image binary was not modified.
- `wrapper-refusal.txt`: the npm launcher cannot stand in for native binary evidence.

Experiment name: `factory-build-cert-identity-20260907`, using only the local artifact
workspace, template `codex-pnpm:v1`, one CPU and 2 GiB. It was stopped after final measurements;
its private `/tmp` copy and intentionally hostile fixture `workspace/json.py` remain evidence.
No application work was present. The first noninteractive `sbx rm` refused terminal input;
explicit `sbx rm --force` removed only this newly created experiment before recreating it.
Do not use the existing adapter's unchecked `remove` return as proof of removal.

Review reproduced a forged identity via candidate `json.py`/PYTHONPATH before the fix.
The adapter now uses `/usr/bin/python3 -I -S`; the regression and real VM both prove the
candidate import does not execute. Standards found no hard violations; spec review confirmed
that fix. Reviewers did not independently run tests. Focused tests also cover malformed
UUID listings, namespace refusal, recreation during query, changed bytes at the same version,
concurrent replacement and FIFO refusal. Full-feature acceptance remains unfinished.

Exact next steps:
1. Compose the full fingerprint from these fresh observations plus canonical actual/requested
   spec/mount/layout/environment, trusted authority/hook and source-owned probe inputs. Pin the
   measured native command through worker preparation and recovery, then enforce fresh identity
   immediately before paid application launch. Do not feed retained job identity to `validate`.
2. Implement the source-owned probe runner and deterministic preflight, common paid admission,
   durable ensure/advance/reconcile/status, and automatic/manual configuration. An expired
   certification lease must reconcile its detached launch rather than authorize another probe.
   The six prior checks and attestation validator are inputs, not an automatic runner.
3. Continue child broker/execution/accounting, subtree recovery, isolated writable integration,
   controls and real synthetic acceptance from the approved plan. Do not revisit CRUD completion.
4. Shared merge/consumer sync, template pin, migration and rollout remain pending. James owns
   merges, deployment and live schema 5→6 approval. No live setting/service/database, shared
   source, consumer pin, tracker or forge change occurred here. No DDL changed.

Final canonical gate evidence: `artifacts/runtime-certification-identity/gates-final.json`.
PASS: Ruff check (67 ms), Ruff format (40 ms), mypy (231 ms), pytest (125,071 ms), each
exit 0 with empty output tails; none skipped. Mypy includes the new source/test paths
(138 files). The offline suite's fake-boundary caveat remains; the separate real VM
observations above establish only this identity slice, not automatic certification or
child-workflow acceptance. The two reviews have no remaining blocking findings.


## Certified native launch checkpoint

Continued from f2840bc in the isolated implementation worktree. The next prerequisite now
carries the native path/hash from a report's certification identity into the staged worker
request, including resumed threads. Invalid or inconsistent native metadata refuses validation;
an absent certification section retains explicit legacy manual behavior. This extraction is
NOT attestation publication or full fingerprint validation and does not promote a manual report.

The worker checks certified starts and hook-fallback restarts through one launch boundary.
It requires an absolute regular native ELF, copies and hashes at most 512 MiB into a Linux
memfd, applies write/grow/shrink/seal seals, and executes that descriptor with pass_fds.
Malformed bindings, missing files, symlinks, wrappers, FIFO inputs and changed hashes cannot
fall back to PATH. No binding retains the existing manual native command resolution. Worker
startup now uses `/usr/bin/python3 -I -S` to exclude candidate import paths. Linux memfd/seal
support is required for bound starts; there is no weaker fallback if unavailable.

Review caught a defect in the initial open-file approach: an open descriptor prevents path
replacement but not in-place modification before exec. The sealed snapshot fixes that race.
The snapshot binds executed bytes, not all sandbox/environment/authority state. Full host
fingerprint checks, trusted request/worker staging and orchestration remain necessary.
Both review axes are clear for the bounded change; reviewers did not run real measurements.

Tests: changed-binary and adapter-to-resume binding tests failed before implementation. Focused
suite covers refusal, normal/resumed starts, hook restarts, immutable snapshot contents after
original-file mutation, malformed certificate metadata and retained legacy behavior. Offline
Linux syscalls and subprocesses are boundary fakes; real sealing is separately measured.

Real acceptance used only the existing owned `factory-build-cert-identity-20260907` VM and
its previous image/native identity. It is stopped again. No paid model turn was requested.
Evidence: `artifacts/runtime-certification-native-launch/` (ignored, retained locally):
- `identity.json`: fresh observer equality with the previous exact generation/image/binary.
- `catalogue.jsonl`, `stderr.txt`: production worker run(probe_models=True), executed from a
  sealed native snapshot, returned the actual catalogue on codex-cli 0.146.0.
- `changed-binary-refusal.jsonl`: wrong requested native SHA refused with exit 1.
- `race-experiment.py`, `race-result.json`: instrumented final Popen boundary changed only
  `/tmp/factory-certification-sealed-launch-fixture`, attempted a snapshot write (EPERM), then
  executed the original production start_server result. Observed version codex-cli 0.146.0,
  exit 0, original_mutated=true, snapshot_write_refused=true. Native image binary untouched.
  Re-run by concatenating the current worker source before its __main__ block with this
  experiment and passing identity.json's runtime_path as argv[1] to `/usr/bin/python3 -I -S -c`
  through SbxAdapter.exec_sync on the owned VM. It overwrites only that disposable /tmp fixture.
- The initial catalogue assertion expected the wrong event name (`factory.model_probe`);
  actual successful output uses `factory.models`. Corrected assertion passed. This was an
  experiment assertion error, not a runtime/model failure.

Worker bytes changed, so prior six-check manifests are stale for this implementation build.
The catalogue/sealing measurements do NOT replace six-check compatibility, hook enforcement,
sandbox isolation, detached durability, accounting or recovery acceptance. Live services use
unchanged source; no manifest was rewritten or live adapter selected.

Exact next work:
1. Compose complete fresh fingerprint from observer results, actual/requested canonical
   spec/mount/layout/environment and trusted authority/hooks/probe revision. Wire it to
   certification publication and immediately before root/reviewer/recovery paid application
   launch. Current selection still follows manual manifest lookup; do not treat this binding
   extractor as automatic certification or feed retained job identity to validate().
2. Load source-owned probe contracts, deterministic preflight, common paid admission and
   durable ensure/advance/reconcile/status. Add explicit manual/automatic settings. Expired
   certification leases must reconcile their existing detached launch before any new spend.
3. Run all six checks against the final worker through the service, then remaining child
   broker/execution/accounting/subtree recovery, isolated integration, controls and real
   synthetic matrix. Stop synthetic work once the factory assertions are satisfied.
4. Shared source merge/consumer sync, template pin, schema 5→6 and rollout remain pending.
   James owns merges, deployment and live migration approval. No schema DDL, live service,
   setting, database, project, tracker, forge or consumer pin changed in this checkpoint.

Final canonical gates: `artifacts/runtime-certification-native-launch/gates-final.json`, PASS. Ruff check (114 ms), Ruff format --check (40 ms), mypy (298 ms), pytest (125,917 ms): each exit 0, empty output tails, none skipped. Mypy covers the changed source/tests in its 138-file set. The fake-sandbox caveat applies to the offline suite; the separate Linux catalogue/sealing measurements above prove only this launch slice. Both final bounded review axes have no outstanding findings.


## Automatic certification service checkpoint

Main objective remains factory workflow/runtime feature validation, using synthetic workloads.
Do not finish CRUD tickets, resume FRO-12, touch historical Backend/nemoclaw work, deploy, or change
live settings. User requested a compaction checkpoint during the real acceptance work. This is a
handoff checkpoint, NOT a claim that all four requested tasks or the overall plan are finished.

Implemented since 6f1c8b9:
- Fresh complete fingerprint construction in certification_fingerprint.py and sbx observer:
  actual/requested mounts/layout/environment, generation/image/native bytes, trusted authority and
  hook inventory, source probe contract/parameters and execution-helper source digests. Environment
  values are hashed, never stored in identity. Capability names use central policy. New authority
  snapshots include `.codex/hooks.json` and `.codex/config.toml`; old snapshots are not rewritten.
- Workflow selection now has explicit automatic/manual modes. Automatic selection uses the durable
  service and fresh publication validation; builder/reviewer/recovery prepared launches revalidate.
  Selection happens before root launch-counter advancement, so certification waits do not consume
  application attempts. Existing runs/default manual behavior are retained. CLI configuration fields
  and exact host configuration format are documented in runtime-certification-service.md.
- CertificationRunner ensure/advance/status/collect runs nine separately admitted phases from the
  new layer-A probe contract. Common approval/capacity/budget, invocation accounting and durable
  intent prevent duplicated spend after controller loss. Terminal stale jobs are collected and
  fenced failed before a new fingerprint proceeds; ambiguous holders remain held.
- Real ProbeDriver and usage worker: deterministic preflight, canary/schema, detached wait and exact
  process-group interruption, recovery, compaction/model-change observations, target inventory and
  strict host report evaluation. Model statements cannot publish a certificate. Unknown compaction
  attribution and incomplete usage stay incomplete. Each new phase requires its own admission.
- Raw stdout is captured directly into host-only evidence. Shared certified_command embeds compressed
  worker/request source and sealed prompt/schema memfds. `immutable=True` detached execution uses
  retained argv, not a mutable pgid-body.sh file. Automatic application wrappers use it too.
- Same-VM certification/application exclusion is atomic with paid admission, including cross-project
  names; independent VMs can run together. Paused/cancelled owners cannot admit new certification.

Two-axis review found and fixed two P1 defects: the first frozen bootstrap still flowed through a
writable body file, and preflight-only sandbox exclusion could race admission. Rereview is clear.
Shared bootstrap extraction also removed duplicate sealing logic. Fingerprints include the shared
bootstrap, base envelope and sbx adapter sources, addressing the final reviewer follow-up.

Real evidence in `artifacts/runtime-certification-service/` (ignored, preserve):
- setup.py creates only the two owned disposable fixtures, with target/protocol/readonly trusted
  hook mounts, codex-pnpm:v1, 1 CPU/2 GiB. Targets are tiny local Git fixtures. Declared capability
  environment variables are explicitly empty for these invocations; no credentials were copied.
- build VM `factory-build-cert-service-20260907`, UUID
  `9c559ead-b11d-41c9-b553-d47d6cc7d0c4`; review VM `factory-review-cert-service-20260907`, UUID
  `34b337e3-a962-4929-9d33-b6a5bea34a06`. Actual native CLI is 0.146.0, default image unchanged.
- Both VMs produced stable repeated complete observations. compare-observations.py also confirmed
  the build identity stable across an explicit stop/start. initial-identity.json predates the final
  uptime fix; use fresh observe/ensure results, never load that old identity as current evidence.
- Three build and two review canary invocations completed during integration. Their raw events,
  exact invocations, costs and stale job failures remain in the isolated stores and host evidence.
  These are limited canary/schema observations, NOT full six-check acceptance.
- Real execution exposed an unintended repeat-certification defect: `sbx inspect` adds an `uptime`
  field in running VMs. It was accidentally included as configuration. A red regression reproduced
  changing uptime; the fix excludes it with state/session/daemon uptime. No identity-bearing field
  was waived. After the fix two separate ensure-only controllers reused build job
  `8073b7bcb7ff4964b8886f79ca1cda1f` without another paid probe. It is pending, not passed.
- First fixture setup command was rejected by a local hook for literal protected variable names;
  it did not execute. The fixture instead reads declared variable names from repository config and
  never reads/prints their values. A temporary observer response-shape mismatch was reproduced and
  fixed with a boundary regression. Retain original failures rather than presenting only green runs.

Exact next work after compaction:
1. Read this final section and runtime-certification-service.md. Stay in the implementation
   worktree. Use source refs from git log; do not update/reset live checkout or apply schema DDL.
2. Continue the real complete service matrix. Inputs already exist at
   artifacts/runtime-certification-service/{build,review}/config.json. `advance.py` runs one real
   controller tick and exits; ensure-only.py observes/requests without paid execution. Invocation:

       uv run python artifacts/runtime-certification-service/advance.py artifacts/runtime-certification-service/build/config.json

   Run the analogous review command independently. Poll with new controller processes; after
   the source-declared 35 seconds, the interrupt phase must observe and stop only its owned group,
   then recovery continues. Do not restart or duplicate an ambiguous paid launch. Nine phases have
   NOT completed yet. Declared `usage_scope=thread` is an experiment input for 0.146; the evaluator
   must prove it or fail, never infer a pass. If unsupported, preserve the failure and diagnose.
3. Prove both build/review reports pass all six checks through the service. Exercise simultaneous
   real controllers, crash/reopen after launch, tampered host evidence, changed identity and a
   real automatic application launch/refusal with fresh fingerprints. Test frozen application
   command quoting for paths containing spaces. Offline selection/exclusion tests are not this
   real acceptance. Stop synthetic work when these named factory assertions are satisfied.
4. Add any required real-tier fixes with red regressions, rerun gates/reviews as justified, then
   prepare the remaining shared merge/consumer sync and reviewed rollout. Do not mark Step 4 or
   the full plan complete merely because source is implemented. Child broker/execution/accounting,
   subtree recovery, isolated integration and remaining controls are still unfinished outside
   this four-task checkpoint. Schema 5→6, template deployment and activation remain James's.

No schema DDL, live service/settings/database, template, project, tracker, forge or consumer pin
changed. The new shared files remain authored on the harness feature branch based on v2 and are
not vendored into any consumer. The old copy-only schema rehearsal still applies to unchanged DDL.

Final checkpoint evidence: `artifacts/runtime-certification-service/gates-final.json`, PASS. Ruff
check (87 ms), Ruff format --check (39 ms), mypy (443 ms) and pytest (127,668 ms): all exits 0,
empty output tails, no skips. Mypy covers 152 source/test files. Earlier gate output gates.json
failed a new fake-response dictionary annotation; corrected before the final run. Offline gates
do not establish full runtime compatibility. Shared `python3 scripts/check.py --since origin/v2`
passed (29 repository tests, 149 shared hook tests and generation/consumer composition checks);
no new consumer sync or final six-check shared/runtime acceptance is claimed.

Both owned service VMs are stopped; `stopped-checkpoint.json` confirms zero active agent leases.
Final pending review job is `41282dddde9942af82f964229dd9e9e1`. The first canary jobs became
stale during implementation/uptime correction and remain failed with costs retained. The current
pending build/review jobs have no paid phases yet; do not mark them passed or reuse older canary
results as complete certificates. Approved plan files remain untracked intentionally; preserve them.

Shared source checkpoint is `e3fc8ad` in /Users/james/harness-runtime-certification (unpublished/unmerged). Factory checkpoint is the commit containing this handoff; retrieve it with `git log -1 --oneline`. No PR or push was performed.

## Real compatibility acceptance checkpoint

Continued from factory a733980 / harness e3fc8ad. Objective remains synthetic factory feature
validation, not ticket completion. Live services, settings, templates, schema and consumer pins
remain unchanged. Overall acceptance is NOT complete: the build service passed, but certified
reviewer shell execution has a reproduced native re-execution defect. Do not activate this build.

Implemented correction: the usage client required a stderr pipe while shared start_server inherited
stderr. Added an explicit keyword-only capture_stderr option, requested only by the usage client;
ordinary application/legacy callers preserve inherited stderr. Both native-bound and legacy launch
branches honor it. The shared-launcher/Client regression failed before the fix and passes afterward.
Focused worker/usage tests pass; two-axis bounded review found no issues. No shared source change.

Real findings and evidence (all under ignored artifacts/runtime-certification-service/, preserve):
- The original tiny fixture used hooks.protected strings, while the source hook consumes objects
  with a glob field. The resumed build canary actually modified protected.txt; this was NOT hook
  enforcement. Raw events and failed-fixture-protected.txt preserve those bytes. retire-fixture.py
  stopped the owned fixtures, marked those jobs failed, and corrected/committed the tiny fixture
  configuration. configure.py refreshed the explicit observed hook hashes. Do not rerun the old
  setup.py without correcting its protected-path shape first.
- Stopping the first build interrupt left no terminal file. collect-stopped.py independently
  confirmed the exact VM state stopped, retained partial usage, and released that isolated failed
  reservation as an operator recovery. It fabricated no exit file. operator-stopped-proof.json
  is evidence. Automatic recovery did NOT resolve that orphan; this intervention must stay counted.
- Corrected canaries in BOTH VMs produced actual blocked hook/completed events. Build job
  4f267c5959384779ba567a70d02ad4fe then proved detached interruption (41.695 seconds, exact owned
  group, exit 143) and same-thread recovery before failing usage-initial with
  "app-server pipes unavailable". That failure motivated the committed pipe correction.
- Final build job 8bb83b69220c4fffbb110bbb622447f1 completed all NINE separately admitted phases
  and published all SIX checks through the real service. Its compatibility.json and
  summary-index.json contain exact identity, raw captured events and measurements. Sol→Terra,
  compaction notification/reset and thread-scoped cumulative usage across resumed controller
  connections were measured on native 0.146.0. Compaction attribution remains visibly incomplete.
  This report certifies only its exact build fingerprint, not reviewer or production identities.
- controller-restarts.jsonl records separate short-lived controllers for every tick and independent
  build/review progress. concurrent-passed-requests.json records two simultaneous fresh controllers
  reusing the same passed build job. This does not claim a real same-VM race at first paid admission;
  that case remains covered offline and still needs a real acceptance measurement.
- fresh-refusals.json: fresh original validation passed; altering host summary-index.json refused;
  restoring exact original bytes passed; a freshly observed changed invocation environment refused
  with certification-stale. No certificate was rewritten to accept changed identity/evidence.
- frozen-paths.py exercised the real Linux frozen wrapper with spaces AND single quotes in paths,
  after replacing the writable staged worker/request. The retained source returned the native model
  catalogue. This is command/staging acceptance, not a paid application turn.
- application.py then ran one schema-valid no-tool synthetic application through AppServerAdapter,
  freeze_script, fresh Certifications.validate and AgentLaunches admission. A new controller
  collected its exit 0, {"ok":true} and usage (application-result.json). This exercises the actual
  adapter/admission composition, not full Context/selection→builder/reviewer/recovery wiring.
  The experiment initially omitted required metadata.adapter, causing collection to refuse. The
  original metadata was saved, the isolated correction audited, and collection then completed
  without a second launch. Production snapshots were not changed; this script correction is not
  evidence that normal workflow callers omit metadata.

Remaining blocking runtime finding:
- Review job 22b6cede534b46f6a36122e4fb6b00b4 failed the interrupt phase. Actual shell requests
  (/bin/bash and /bin/sh) returned CreateProcess / No such file or directory. A model returning
  {"ok":false} or a schema-valid final answer cannot satisfy durability.
- native-helper-repro.py reduces this to NO model call: native `codex sandbox -- /bin/true`
  succeeds through the installed path; the same command through production sealed start_server
  fails exit 1 / ENOENT. native-helper-{sealed,path}.json retain the pair. The initial experiment
  mistakenly included a `linux` subcommand; the corrected reproduction removes it.
- Upstream 0.146 arg0 code derives self/helper paths from current_exe and creates helper aliases;
  a memfd executable reports a deleted, non-reopenable filesystem name. Retained upstream source
  is in upstream/. Reference:
  https://github.com/openai/codex/blob/rust-v0.146.0/codex-rs/arg0/src/lib.rs
  This explains why catalogue/schema/build commands can pass while read-only shell execution fails.
  Do NOT fix by disabling the reviewer sandbox, dropping native binding, or executing a mutable
  candidate path. The sealed-byte guarantee and read-only enforcement both remain required.

Exact next work:
1. Resolve stable, immutable native self/helper re-execution, using native-helper-repro.py as the
   fast red signal. Investigate a trusted read-only executable mount/namespace or runtime-supported
   helper binding; neither is an implemented/approved final design here. Bind any new helper or
   launcher dependency into fresh identity. Retain mutation/seal refusal guarantees and validate
   real read-only shell execution before buying more reviewer turns. Do not retry the failed job
   unchanged or synthesize its report.
2. Worker/helper changes invalidate the passed build certificate. Rerun both complete service
   matrices against the final source, then real initial-admission concurrent controllers and full
   automatic workflow selection/launch/recovery refusal paths. Existing real evidence above is
   useful but must not be promoted to those unexecuted claims.
3. Remaining children/execution/accounting/cancellation/subtree recovery, isolated integration,
   controls and rollout work remains as previously recorded. Shared e3fc8ad remains unpublished;
   source merge/sync, template pin, live schema 5→6 and deployment remain James's decisions.

Canonical gate report gates-stderr-fix.json: PASS, no skips. Ruff check exit 0 (98 ms), Ruff format
exit 0 (43 ms), mypy exit 0 (535 ms), pytest exit 0 (142,960 ms); every output tail empty. Mypy's
configured source paths include this change. The fake-sandbox caveat applies to the suite; separate
real measurements above establish the listed runtime behavior and expose the remaining defect.
Approved plans remain intentionally untracked. Both owned service VMs are stopped again, zero active
leases; acceptance-stopped-checkpoint.json records final jobs, accounting completeness and state.
