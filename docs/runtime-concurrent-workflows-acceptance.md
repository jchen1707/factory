# Concurrent factory workflow acceptance

Status: **concurrent execution/control observations passed; a red-phase classification
defect was found during the final evidence audit.** Both bind workflows reached `pr_ready`;
the clone target was cancelled while its survivor reached `pr_ready`. No delivery occurred.
All seven provisioned fixture sandboxes were inspected stopped. Ten actual top-level model
invocations ran: four builders and six independent reviewer axes.

This closes a different gap from the earlier separate controller and isolated-worker probes:
two real legacy Codex builders are driven through production `implement.start/collect`,
`verify.start/collect`, and `review.start/collect` with shared project admission, separate
sandbox identities, frozen authority, and exact per-invocation approval holds.

The first bind pair uses local synthetic approved contexts and real Git worktrees. Each
builder adds one integer-scaling function and focused tests to its own two allowed files.
Both started at Unix timestamp `1788757078`; their schema outputs landed at
`1788757154.318` and `1788757161.217`. Both committed only their own changes, preserved
operator notes, and were collected into `verifying`. This is actual overlapping model
execution, not two fabricated implemented results.

Both deterministic verification gates subsequently held identical port 38473 and identical
VM temp/SQLite paths simultaneously, with distinct LOCAL-1/LOCAL-2 values. Supported
`recovery.suspend` signaled verifier 0; verifier 1 continued from observed tick 7 to 31 and
67. Supported resume restarted only verifier 0 at the same attempt. Its database retained
the old LOCAL-1 row and gained a second LOCAL-1 row; the survivor database stayed unchanged.
Both gates completed successfully after the operator fixture holds were released. Both
runs advanced to `reviewing`.

The suspended run's tracker announcement was deliberately refused by the fixture's
no-tracker boundary after the production state transition. That exception is retained;
there was no real or fabricated tracker receipt. This establishes process/state recovery,
not successful tracker announcement behavior.

Both reviewer environments passed production preflight. Both review axes per run
launched after distinct approval holds and returned empty findings. Production collection
recorded current review authority and advanced both runs to `pr_ready`. With both project slots occupied, the production
launch guard queued a third synthetic reviewer candidate before any model invocation.
The third candidate is a resource-guard input, not a third implemented workflow.

## Scope and retained inputs

The adapter is the existing supported **legacy `codex exec`** path, explicitly selected for
this concurrency acceptance. This does not validate or activate the revised app-server
worker. Delivery and all tracker/forge calls are outside the fixture.

The two runs start from explicitly constructed `worktree_ready` contexts; no ticket claim,
readiness model, or prior agent attempt is claimed. Their per-run identity settings are
constructed setup inputs resolved by production `project_for_run`; no new isolation pass
manifest was fabricated. Operator configuration/manifest admission remains covered by its
separate controller measurement, not by these initialized settings.

The build specification uses production workspace selection plus a separate per-run fixture
protocol mount for bounded gate progress/hold signals. The target-declared stdlib gate owns
the resource exercise. No command or fixture test policy was added to factory source.
The bind workspace mount is shared at project-root scope, as in the supported production
layout; this is resource separation, not confidentiality between sibling worktrees.

The prepared fixture initially lacked generated discovery stubs. Production preflight
refused it. The official sync command generated the missing stubs from exact merged harness
`f7917ce3a66f916109c5c2d621d4b621bc6fca87`; fixture branches and authority were explicitly
refreshed before model launch. A subsequent driver omission of the controller lease was
also refused before model launch. Those attempt-1 setup rows/failures are retained; the
actual builders ran at attempt 2. Neither setup issue changed factory source.

Evidence lives in `artifacts/runtime-concurrent-workflows-acceptance/bind/`: exact specs,
preflight checks, approval keys, launch requests, SQLite state, authority snapshots, complete
model/gate output, concurrency observations, suspend/resume transitions and reviewer plans.
The approved experiment is bounded to two builders and four Tier-1 reviewer invocations in
the initial bind pair, with no automatic repair solely to finish the fixture.

## Clone cancellation and live draining

Both actual clone builders completed before the resource observation. The supported operator
configuration lowered concurrency from two to one while two real deterministic verifier
processes remained active. Neither slot disappeared; the third guard queued without an
invocation. Both workers continued with distinct data at identical VM paths and ports.
This is live verification-process draining, not a claim about lowering a limit while both
builders were still running.

Before cancellation, both committed candidates were preserved in host-visible Git bundles
and verified by real Git. The target's complete dirty-work inventory contained only
`operator-note.md`; tracked/staged diffs were empty and the exact note bytes were backed up.

Source inspection found `_cancel_run` using the registry's shared sandbox identity instead
of the run's recorded identity. The unsafe call was not executed. The parent coordinated a
minimal correction with failing-before/passing-after targeting regressions and independent
review. This fixture then called the corrected production cancellation path. Its source hash
is retained in `clone/cancel-source.json`.

The target became `cancelled`, its exact per-run VM stopped, and the sibling advanced to
observed tick 184 with unchanged data and port ownership. A brief read-only VM restart
verified that the candidate remained at `refs/factory/cancelled/<run>/test/local-1`, the
active branch was absent, and the operator note remained; the VM was stopped again.
No workflow or model was restarted in the cancelled run. Tracker restoration was explicitly
refused by the no-tracker boundary after local cancellation; no receipt was fabricated.

The survivor completed its independent gate, fetched its private branch back through the
production clone path, and entered review. Reviewer admission reclaimed only the cancelled
slot, leaving the survivor's original identity as the sole admitted run at limit one.
The reviewer sandbox is intentionally a read-only bind layout even for clone builds. The
fixture initially passed the build's clone expectation to the generic preflight; that false
fixture expectation was retained and corrected to the actual reviewer specification before
any reviewer model launched. No production sandbox specification or source rule was relaxed.

This experiment does not measure cancelling an active reviewer, cancelling one run inside a
shared VM, or descendants that outlive a wrapper. The measured cancellation targeted a
verifier in its own per-run build VM. The existing cancellation path still stops the build
VM after local cleanup; no stronger ordering claim is inferred from this result.

## Red-phase evidence defect found in final audit

The raw factory checks report `pass`/`red` for the bind replay rows, but their output contains
`ImportError` and `FAILED (errors=1)`, not a failing assertion. The clone survivor's replay
contains `AttributeError` before the comparison can execute. These are not proof of the
assertion-level red phase. The existing classifier's bare `failed` signal promotes runner
error summaries to an assertion result, including overriding collection-error detection.
The original outputs and erroneous check rows are preserved in the layout `result.json`
files. This defect was reported before any source edit.

The actual overlapping implementations, independent passing gates, reviewer invocations,
resource ownership, draining, suspend/resume, and corrected cancellation observations remain
valid. A green replay classification is explicitly not inferred from this fixture. The
separate monorepo replay acceptance retains genuine assertion failures and is unaffected
by this qualification. `verified-summary.json` distinguishes the bounded successful control
observations from this open guard defect; it is not an activation approval.

Exact wrong-positive records (preserved, not rewritten):

| Evidence file | Run | Factory record | Actual error |
| --- | --- | --- | --- |
| `bind/result.json` | `083307de78cf4b40` | `redphase: pass / red` | ImportError; unittest errors-only summary |
| `bind/result.json` | `0318f9616dfb4af4` | `redphase: pass / red` | ImportError; unittest errors-only summary |
| `clone/result.json` | `90a91a673c6443d8` | `redphase: pass / red` | AttributeError before comparison |

Paths in this table are relative to `artifacts/runtime-concurrent-workflows-acceptance/`.
The complete command outputs, including the clone output beyond the check-row truncation,
are in each layout's `sandbox-commands.jsonl` at scratch-worktree gate executions.
`verify_evidence.py` passed its bounded control/effect assertions; its summary remains
`measured-with-redphase-defect`, deliberately separate from the factory's incorrect rows.
`sha256.json` indexes 817 retained evidence files, excluding Git internals, generated trees,
the separately pinned shared source checkout, and bytecode.
