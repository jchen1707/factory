# Active cancellation and suspension preserve shared workers

The remaining cancellation gap was real: the control plane could remove an active
writer's worktree before stopping it, leave an active reviewer running, and stop a
VM still used by another run. The earlier persisted-identity fix alone did not
address these lifecycle operations.

Cancellation now reads the active attempt's recorded sandbox and process group,
requires a valid terminal record before cleanup, and records its terminal outcome.
It signals only the selected group, leaves physically shared VMs running, and stops
an idle recorded reviewer VM as well as the build VM. An occupied shared clone's
branch is left in place with an explicit message, because its cleanup otherwise
checks out the base branch underneath the sibling. Local orphan/branch preservation
rules remain in force.

Independent review found two further defects before real cancellation: planning uses
`plan-exit`, not `exit`; and a physical VM can be shared across registry projects.
Both received failing regressions before correction. Physical occupancy is now
resolved from all active runs' persisted build/review identities and recorded
attempt locations. Name-based signaling is refused when the target VM has another
active owner and no process group is available. A stale ordinary `exit` file cannot
substitute for a planning terminal record.

The same state-aware terminal wait and physical occupancy guard now protect
suspension. A missing or invalid terminal record refuses the suspend transition
instead of claiming the attempt stopped. The existing Context-based signal helper
shares the physical-VM fallback guard, including recovery and reaper callers.

## Deterministic regressions

Retained red evidence covers six original lifecycle failures, four planning and
cross-project cancellation failures, two suspension failures, and the cross-project
suspend fallback failure. A final missing-artifact-directory suspension case also
failed before its refusal guard was added. Twenty focused cases cover legacy/per-run bind/clone
selection; targeted builder/reviewer termination before cleanup; completed attempts;
missing process groups and terminal records; occupied clone checkout preservation;
planning filename separation; cross-project ownership; and suspension refusal.
The combined cancellation plus Phase 4 run passed 75 tests before the final
missing-artifact-directory refusal case; all 20 focused cases passed afterward. The parent
agent retains the separate full four-gate report; this focused result does not
substitute for it.

Logs are under `artifacts/runtime-cancellation-gates/`, including
`active-cancel-red.txt`, `active-review-red.txt`, `suspend-stop-red.txt`,
`suspend-fallback-red.txt`, and `suspend-stop-related-final.txt`, `suspend-missing-directory-red.txt`, and
`final-focused.txt`.

## Real shared-VM cancellation

`artifacts/runtime-active-cancellation/` retains two fresh stock VMs and four
production detached envelopes: two deterministic verifiers in one shared build VM
and two deterministic reviewer-state workers in a shared reviewer VM. Every worker's
synthetic dirty file and SQLite database was backed up before cancellation. No
model was launched. The production cancellation core ran with the real sbx adapter;
its tracker boundary explicitly refused connection, and no tracker effect was made.

Cancelling the first verifier produced exit 143 while its sibling progressed from
tick 244 to 249. Cancelling the first reviewer produced exit 143 while its sibling
progressed from 246 to 250. Both surviving dirty files and databases retained their
original values. The final workers were drained through the same cancellation core;
all four terminal files record 143. Both VMs were stopped and inspected:

- `factory-build-cancel-shared-72733`
- `factory-review-cancel-shared-72733`

The exact executed source before the shared-helper relocation is retained in
`executed-source/` and `executed-source.json`; `observations.jsonl`, wrappers,
controller database, backups and `verified-summary.json` retain the actual effects.
`sha256.json` binds the artifacts. This proves deterministic process lifecycle
through production cancellation, not termination of every possible nested child
of an active model-review invocation.

## Real planning suspension

The separately retained `artifacts/runtime-active-suspension/retry/` experiment
passed on the final source. Two deterministic workers shared
`factory-build-suspend-shared-26500`: a planning attempt and a verifier owned by a
different registry project. The target directory deliberately contained a stale
ordinary `exit` file with value 0. Production `recovery.suspend` signalled the
planner's own process group, observed `plan-exit` 143, and recorded the attempt and
run as suspended. The other project's worker progressed from tick 0 to 4 and still
polled RUNNING; both synthetic dirty files matched their backups. Cleanup stopped
the surviving fixture and the VM; final inspection confirmed stopped.

No model was launched, and the tracker boundary deliberately refused connection.
This verifies the real detached-process/store lifecycle and filename separation,
not a planner model's session continuation. Exact final source hashes, raw wrappers,
controller database, progress records, and `verified-summary.json` are retained.
The encompassing `sha256.json` binds them. The first experiment under the parent
directory timed out waiting for final independent review and safely stopped its VM;
its failure is retained as an operator-wait timeout, not a successful suspension or
a runtime defect. The successful retry began only after that review closed all
concrete findings.

No existing product, FRO-12 run, production registry/database, live ticket, or human
sandbox was used. These scoped measurements do not authorize production activation.
