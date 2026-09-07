# Runtime implementation handoff

## Main objective and next resume — James's scope correction

**Validate the factory workflow and every use case, requirement and function in the
approved four-repository improvement plan. Close factory implementation gaps and
retain acceptance evidence. The disposable CRUD tickets are test workloads, not a
product delivery objective.** James explicitly corrected this after the continuation
below spent unnecessary effort driving the tickets toward completion.

Advance or modify a test workload only when it enables a specific missing factory
acceptance check. State that check first and stop when sufficient evidence is retained.
Do not harden the disposable app, finish every CRUD ticket, merge its implementation,
or fix its CORS issue merely to obtain a completed ticket. The CORS finding already
demonstrated independent review detecting a defect missed by green gates and preventing
delivery. FRO-12 can remain parked. Its product defect is not a blocker for unrelated
factory validation, and no CORS disposition is currently being requested from James.

**Do not merge the pending changes while required CI fails.** The previous merge
recommendation was premature. Latest observed remote checks:

- Harness PR #32: `cross-stack` failed; `generate` and `submodules` passed.
  [Failing run](https://github.com/jchen1707/harness/actions/runs/34077096359).
  Both consumers installed, but their gates were `skipped_unchanged`; the checker
  correctly failed with "layer A changed but no gate ran". Diagnose the changed-path/
  dispatch seam in `scripts/cross_stack.py`, consumer configuration and shared runner,
  reproduce it, then fix the responsible layer. Do not silence the failure, weaken the
  assertion or count skipped gates as passes. The exact root cause is not yet established.
- CRUD PR #1: `freshness` failed because its `d2992c8` pin is ahead of the unmerged
  shared source; `harness@v2` still has `028f0c8`.
  [Failing run](https://github.com/jchen1707/factory-crud-verification/actions/runs/34077142444).
  Keep it draft. After shared CI is green and James merges the source, regenerate
  against the exact merged SHA and require freshness to pass.
- Factory PR #83: GitHub currently returns no check runs. The recorded local four-gate
  pass is valid within its scope, but is not a remote green-CI claim. Verify the required
  repository checks/review conditions before recommending merge.

Full failed logs are retained as `artifacts/crud-runtime-test/harness-pr32-ci-failure.log`
and `crud-pr1-ci-failure.log`. No CI fix was made during this documentation checkpoint.

After James compacts and resumes, continue autonomously with:

1. Resolve the CI failure above and validate final shared/consumer/factory revisions;
   publish corrective updates and report actual remote results before merge advice.
2. Use [the acceptance inventory](runtime-acceptance-inventory.md) to target remaining
   requirements: actual separate test-design execution and vertical TDD/monorepo replay;
   app-server integration through the factory workflow; bounded diagnosis/repair and
   authority refresh; full concurrent-run scheduling, isolation, draining, recovery and
   integration-base handling; and invocation accounting, including unresolved nested
   collaboration attribution. Reconcile the rest of the approved matrix too—this list
   does not reduce the original requirements or declare other rows complete.
3. Choose the smallest isolated experiment for each gap. Reuse captured events and
   existing successful measurements where they establish the required property. Reuse
   or advance CRUD only where necessary; do not impose the FRO-12→13/14→15 delivery
   sequence as a prerequisite for all factory work. Preserve normal ticket dependencies
   if those tickets are used; never fake state, bypass authority, or fabricate evidence.
4. Fix discovered factory/shared-contract defects in their owning repositories, rerun
   relevant acceptance and required gates, and update the inventory and handoff. Stop
   disposable ticket execution once the factory properties under test are established.

This is a handoff for continued work, not a completion claim. Production activation
remains separate; existing products/tickets remain excluded. Both CRUD sandboxes are
stopped and the candidate is preserved. The following continuation is retained evidence;
its former CORS-fix and ticket-completion instructions are superseded by this section.

## Continued validation — 2026-09-07 UTC

Latest operational state: FRO-12 run `47515078d97246e9` is **blocked /
review-finding, attempt 2**, with all eight review axes complete. Seven returned no
findings; standards returned the single high-severity CORS finding documented below.
Run mode has been restored to **Approval**. Both CRUD sandboxes are stopped and their
final inspections are retained. No model or targeted driver remains running.
`artifacts/crud-runtime-test/handoff-state.json` records the final state, every review
result and invocation, and evaluation output (zero accepted changes; costs incomplete).
The candidate is additionally preserved in verified complete-history Git bundle
`fro12-candidate-b635ade.bundle`; do not cancel or delete its branch/worktree.

Current next actions are in the objective/CI section above. There is no requirement
to complete FRO-12 or obtain a CORS disposition before continuing independent factory
acceptance. The finding remains preserved and undisposed; any future review-driven
repair still follows the normal human-decision boundary if that path is needed.

Implementation commit `b635ade17a03b7adf3999b676244be8719bf7d89`
was collected and all ten independent API/web gates ran and passed. Review approval
`2:review:1` was exercised; the run alone was then explicitly switched to Automatic
mode (`run-automatic.json`) to continue its remaining reviews. Project defaults and
production settings are unchanged. Standards reported a high CORS finding, reproduced
by the operator; spec returned no findings. See
[the finding and reproduction](runtime-crud-review-findings.md). James must decide
the finding; the candidate has not been changed to dismiss or fix it.

Security review launch 3 failed with the runtime message "Selected model is at
capacity." Supported `factory resume FRO-12 --from reviewing` launched only the
incomplete security axis as launch 4, preserving standards/spec and the failed
invocation. The retry passed and the remaining axes completed. Retained `review-capacity-resume.txt` and
`review-capacity-accounting.json` show the retry and distinct invocation records.
The run is now parked for James; do not start broad intake or a new run.

Earlier, a real supported suspend/resume acceptance check resumed original
session `01a079c0-a16b-7ff3-b80a-82cb727dd3ba`; exact branch, HEAD,
tracked diff and untracked implementation/test/plan files were preserved.
See [the recovery evidence](runtime-crud-recovery-acceptance.md). The unapproved
resume exposed a CLI reporting exception while correctly preventing launch;
the CLI/console correction now passes all four gates in
`artifacts/crud-runtime-test/factory-gates-resume-holds.json`. Approval `2:implement:1` was then applied
and the existing session resumed successfully. Do not launch another attempt.

Earlier successful real `factory resume FRO-12` recollected readiness and kept
the original attempt and archived its schema/manifest/accounting evidence in
`run/1/recollection-original.json`. A repeated targeted drive then held at
`1:implement:1` without launching a planner or builder. That exact implementation
approval was applied through the CLI, and the detached builder started successfully.
Retained files: `readiness-resume.txt`, `implementation-approval-held.json`,
`approve-implement.json`, and `implementation-launch.json` in the isolated home.
Use the targeted driver to observe this run; do not start another run or broad intake.

Factory source is committed at `0a54196` (following `a8e1dce`) and published in
[factory PR #83](https://github.com/jchen1707/factory/pull/83). All four gates pass
in `artifacts/crud-runtime-test/factory-gates-resume-holds.json` against the final source.
Shared source is in [harness PR #32](https://github.com/jchen1707/harness/pull/32).
The generated CRUD contract/test-path refresh is draft
[CRUD PR #1](https://github.com/jchen1707/factory-crud-verification/pull/1), commit
`fb1392a`, with ten gates and integrity checks passing; remote freshness awaits the
shared-source merge. None of these PRs has been merged by the agent.

The continuation closed additional integration gaps locally: supported deterministic
readiness recollection on operator resume; explicit shared test-design instructions
and host-snapshotted artifact requirements; builder consumption of only collected
artifacts; semantic role accounting; and monorepo red-phase replay using child
configuration from immutable authority, with nested sandbox dependency links.
Independent review caught and corrected candidate-controlled child policy and mutable
test-design request weaknesses. The final integrated gates pass; corrective PR review
and the remaining runtime acceptance are still required.

New retained acceptance:

- [Build compatibility](runtime-crud-build-acceptance.md): all six checks pass for
  the exact CRUD build sandbox/runtime/worker; private clone work was preserved.
  Build and reviewer manifests share operator directory
  `artifacts/crud-runtime-test/compatibility/`. No adapter setting was switched.
- [Installed dependency isolation](runtime-isolation-installed-dependencies-2026-09-07.md):
  real distinct dependency installations, simultaneous temp/SQLite/port resources,
  targeted cancellation and fresh-attempt recovery passed in bind and clone layouts.
  Full controller scheduling/recovery remains separate acceptance.
- [Composable presets](runtime-composition-acceptance-2026-09-07.md): four defaults
  and three optional combinations installed and passed all 32 declared gates on the
  host. Provider/service behavior and Linux-native compatibility are not implied.
- `artifacts/crud-runtime-test/captured-accounting.json`: three production collector
  replays of actual planner events into a database backup preserved one cost record
  and all amounts. USD remains unknown. The evaluated cohort has no accepted changes.
- `artifacts/crud-runtime-test/console-captured-state.json`: read-only ASGI requests
  against actual isolated state rendered board, projects and run settings successfully,
  including the incomplete-cost label. No production server was started.

See [the detailed inventory](runtime-acceptance-inventory.md) for remaining coverage.
The current CRUD authority omits child test path declarations. The new replay code
must still report unavailable for that frozen configuration until generated consumer
changes are merged and an explicit operator authority refresh occurs. Do not invent
test paths or silently alter the current snapshot. James retains all merges.

Earlier gate reports taken while parallel changes were still landing are superseded
by the final reviewed-tree report when present; do not cite a partial/in-flight run
as final verification. The source and retained historical reports below remain valid
only within their named revisions and scope.

## Historical compaction checkpoint and completion rule — 2026-09-07

James requested this earlier documentation checkpoint before compaction. **It is now
superseded by Continued validation above. The following sections are historical;
do not repeat their old next steps or treat them as the current run state.**
The [rollout plan's current completion criteria](runtime-rollout.md#current-completion-criteria--2026-09-07)
now distinguish implemented features from unfinished acceptance, covering all areas of
the approved four-repository plan. Original PRs being merged does not mean every
requirement is complete and working. Close implementation gaps and failed acceptance
checks during this work, before activating affected features. Only production rollout,
raising live concurrency and longer-term outcome comparisons wait until afterward.

Next after compaction:

1. Preserve the local fixes and all retained evidence. Review the final readiness
   collection correction described below and rerun the full factory gates; the last
   four-gate report predates that correction, although its 21 targeted tests passed.
2. Continue the existing isolated FRO-12 run through the supported recovery path,
   preserving its branch and dirty work. It remains blocked; no retry or implementation
   occurred during this documentation update. Never edit the database to fake progress.
3. Complete the rollout acceptance matrix, including the separate test-design role's
   actual behavior. Fix shared contract gaps in harness@v2 and regenerate consumers;
   do not impose an unrequested readiness filename or hand-edit generated vendors.
4. Publish corrective PRs and hand them to James for merge. Finish dependent CRUD
   acceptance after prerequisite merges, then prepare activation settings for James.

This checkpoint changed documentation only. Do not restart production writers, repeat
the migration, touch existing products/tickets, or broaden the isolated test registry.

## Active isolated testing checkpoint — supersedes older status below

Updated 2026-09-07 UTC. James authorized testing the new disposable CRUD workload.
Existing products/tickets, especially `nemoclaw-dev`, remain excluded. Production
writers remain stopped; schema migration is already complete and must not be repeated.

- Isolated home: `/Users/james/factory/artifacts/crud-runtime-test`; registry contains
  only `factory-crud-verification`. Never use broad intake/tick: team routing alone
  could select unrelated FRO tickets. Use `drive_ticket.py FRO-12` in this home.
- Disposable repository main is now `d99be91abc8603192f8af8aa6f24ddee66116441`.
  FRO-16 is the non-executable parent specification. FRO-12 was explicitly made
  ready for this authorized test; FRO-13–15 remain unstarted and depend on earlier
  changes. James retains merges and Done transitions.
- Current run `47515078d97246e9`, FRO-12, attempt 1, is **blocked / plan-incomplete**.
  Planner exited 0 with schema-valid `ready` output and wrote `execution-brief.md`,
  but not `test-plan.md`. Check shared handoff requirements against the collector
  before retrying; do not fabricate missing planner evidence. No implementation or
  delivery has occurred. Prior setup runs `0612cc9e686b428e` and `88015c672b884a40`
  were cancelled after retaining their failures.
- Build sandbox: `factory-build-crud-20260907`; private clone branch
  `chore/FRO-12-create-and-list-sqlite-notes-in-the-api`. Reviewer sandbox
  `factory-review-crud-20260907` was stopped after its probes. Build was restarted
  by a read-only inspection after planner completion; preserve its clone/work.
- Attempt evidence lives under
  `state/clone/factory-crud-verification/FRO-12/47515078d97246e9/.factory/run/1/`
  inside the isolated home. `plan-last-message.json`, `plan-exit`, and raw events
  are retained. Approval key `1:plan:1` was demonstrably held without creating an
  attempt/model usage, then explicitly approved through the CLI. Detached planner
  holder survived launcher exit with PPID 1 (`plan-detached-observation.json`).
- All ten scaffold gates ran and passed inside the actual build VM
  (`sandbox-baseline-gates.json`). This does not prove CRUD functionality. API uses
  a VM-only venv. Web installation created `.pnpm-store/`; it was excluded only in
  the VM's `.git/info/exclude`, without changing tracked source or policy.
- Template-inherited `GH_TOKEN` was measured invalid using `gh auth status` before
  model work. Only that measured name was acknowledged in the isolated registry;
  no usable credential was injected. Private base refresh now uses a host-fetched,
  SHA-checked Git bundle over the existing protocol mount.

Four additional fixes remain uncommitted atop `41f1eef`: private clone refresh;
preserving computed acceptance criteria in readiness facts; collecting clone-only
handoff markdown from the VM and retaining it with attempt artifacts; and treating
absent Codex 0.146 hook `async` metadata as the synchronous default while still
rejecting explicit mismatches. Regression tests demonstrated failures before fixes.
All four factory gates pass in `artifacts/crud-runtime-test/factory-gates-current.json`;
an independent review found no concrete defects in those four diffs. Preserve all
existing changes and reports. No factory fix PR has been opened or merged.

Exact reviewer app-server compatibility evidence passes production manifest validation:
`artifacts/crud-runtime-test/reviewer-compatibility/factory-review-crud-20260907.json`.
See `docs/runtime-crud-reviewer-acceptance.md` for six checks and limitations. It is
specific to that sandbox/runtime/worker; it cannot authorize the build sandbox.
Current FRO-12 remains pinned to legacy `codex exec`.

Remaining: resolve the observed handoff contract mismatch; finish FRO-12 through
implementation, real gates, isolated review and a PR waiting for James; exercise
factory suspend/resume, diagnosis/repair, and concurrency isolation with retained
effects. Full CRUD acceptance needs the dependent tickets after prerequisite merges.
Build app-server compatibility and remaining runtime controls still need their own
evidence. Then publish/review the factory fixes and prepare concrete activation
settings for James. Do not claim all planned features verified from unit tests,
baseline gates, or individual protocol probes.

### Final collection repair at this checkpoint

The observed handoff mismatch is now fixed locally, **without retrying or changing the
blocked run**. Transition 18 names only missing `test-plan.md`; collection successfully
read the VM-only execution brief. The retained planner transcript writes only that brief.
This was a contract mismatch, not a sandbox shutdown or another filesystem visibility bug.

Shared `ticket-readiness.md` permits a schema-valid ready result with no files, or an
optional execution brief for technical gaps. Shared `diagnose-and-hand-off.md` explicitly
requires both `execution-brief.md` and `test-plan.md`. `src/factory/steps/plan.py` now applies
those distinct requirements: readiness retains any nonempty optional markdown; diagnosis
and legacy planning still reject missing or empty required files. Collected markdown stays
under `planning-output/` in the attempt directory and is included in its manifest.
No start, prompt, output-directory, or live invocation paths changed.

Two readiness regressions (brief only, and no markdown) failed before this repair.
All 21 tests in `tests/integration/test_clone_plan_collection.py` and
`tests/integration/test_handoff_fingerprints.py` now pass; targeted Ruff, mypy, and diff
checks pass. The earlier four-gate report predates this final collection adjustment:
rerun the full gates before claiming final verification or publishing. The new patch
remains uncommitted, and the real blocked attempt has **not** been recollected or resumed.

Remaining contract limitation: `test_designer` currently receives the same shared readiness
prompt, which does not mandate a test-plan file. Factory cannot silently enforce that file.
If separate test-design mode needs mandatory file output, define it explicitly in layer A
and update its consumer contract before enforcing it. No vendored or product source was
edited for this repair. Next step is review of this correction, full gates, then the
isolated FRO-12 continuation with retained evidence; do not relaunch broad intake.

## Latest scope and disposable verification workload

Updated 2026-09-07 UTC after James's scope correction. **This section supersedes
the earlier checkpoint below wherever they differ.**

James excluded `nemoclaw-dev` and existing Linear ticket work. Do not resume the
target-project probes described in the older checkpoint. The preceding continuation
only inspected existing target metadata/dependencies; all sandboxes were stopped.
It did not run models or ticket workflows in those projects or change their source,
settings, credentials, or tickets.

James then explicitly authorized creating new simple SQLite + frontend CRUD tickets
and a dedicated verification repository. That authorization covers these new test
items; it does not add ticket-generation behavior to the factory.

- Repository: `https://github.com/jchen1707/factory-crud-verification` (private),
  local `/Users/james/factory-crud-verification`, scaffold commit `54e1b46`.
- Linear project: [Factory CRUD Verification](https://linear.app/development-jchen/project/factory-crud-verification-20a7294f31c7),
  ID `19aa56d2-09d6-4370-8a49-ad04d2552d17`.
- New tickets: FRO-12 create/list; FRO-13 edit and FRO-14 delete each depend on
  FRO-12; FRO-15 persistence/error/browser acceptance depends on FRO-13 and FRO-14.
  All are Backlog with no labels; none has been started or made ready for intake.
- A FastAPI + React monorepo was generated from merged `harness@v2`
  `028f0c8ea82a7c94d9b83eb38f1be70ec3ef5afb` with the agnostic adapter. The CRUD
  work is intentionally left for the tickets. Dependencies and lockfiles are
  installed/generated. All ten declared API/web gates pass; the report is committed
  as `docs/scaffold-verification.json` in that repository. The app gates are required
  in all three declared delivery profiles, with no deferrals.
- Read that repository's `docs/verification-spec.md` and
  `docs/factory-verification.md`. **Use an isolated factory home, database, registry,
  test vault, and fresh sandbox identities.** The production FRO mapping targets
  frontend-harness and must never execute these tickets. Isolated execution setup
  has not yet been activated; James still controls readiness and merges.

Factory fix state: core fixes/earlier evidence are committed locally at `41f1eef`
on `ops/runtime-validation`; no fix PR was opened. A later test-only assertion
and review/target reports remain uncommitted. Preserve them. The new assertion
rejects fresh context after compaction invalidation; all six cases fail with the
guard removed in memory. The unchanged production worker passes all 40 worker
tests, and all four factory gates passed again in
`artifacts/runtime-review-followup/factory-gates.json`.

Seven review axes completed, with one test gap addressed. Repository checklists
are absent; the standards axis could not run under its frame. See
`docs/runtime-fix-review-2026-09-06.md` for the limited review result. The existing
target reports remain historical evidence, not instructions to keep working there.

Next: finish publishing/reviewing the factory fix; prepare isolated execution for
the four new tickets, then measure the planned orchestration features using those
workloads and retained operator actions. Neither the passing scaffold nor the
earlier individual runtime probes proves all factory features end to end. Keep the
live timer/console stopped and production settings unchanged until James's decision.

Updated 2026-09-07 UTC (2026-09-06 Toronto). **Checkpoint requested by James for conversation
compaction.** Continue from here; do not re-plan, re-run the migration, or restart completed
investigations. The approved four-repository plan remains the specification.

## Current outcome

All original implementation PRs and shared pin-update PR #31 are merged. James approved
schema 4→5; the live migration succeeded after stopping writers and retaining a verified
backup. All **1,889 existing rows** remain byte-for-byte equivalent by table row digests.

The real runtime tests found protocol and hook-enforcement defects. They are now fixed
locally and the measured runtime probes pass within the scopes below. This is **not a
completed production activation**: the timer/console remain stopped, no project runtime,
model, delivery, or concurrency setting changed, and no production compatibility/isolation
manifest was created. James asked to stop at this handoff checkpoint.

## Checkout and local changes

- Repository: `/Users/james/factory` on `ops/runtime-validation`, based on merged
  `origin/main` at `4c96bdd` (factory PR #82).
- Shared PR #31 is merged at `028f0c8ea82a7c94d9b83eb38f1be70ec3ef5afb`.
- Disposable Python source: merged `v2`, `624542b4aa8887f1e94a00ef40efd4cde697b723`.
- Follow-up fixes/docs are **uncommitted and unpublished**. Preserve them. No new PR was
  opened and no merge was performed in this continuation.

Changed implementation:

1. `src/factory/agent/app_server_worker.py`: thread/start and thread/resume input sandbox
   values are `read-only` / `danger-full-access`, not rejected camelCase variants.
2. The worker discovers hooks and uses their exact hashes in thread-local `hooks.state`.
   Actual app-server 0.149.1 accepts but does not apply the CLI hook-trust flag; legacy exec
   blocks the same canary. Keep the required flag as well as the effective thread overrides.
3. Secondary read-only project mounts can omit the entire untrusted project hook layer.
   When project hooks are absent, the worker restarts its metadata-only app-server with
   **only the vetted repository hook definitions** as CLI invocation configuration, then
   uses discovered hashes. It does not trust the full project config or write a trust store.
4. Required hook coverage is derived from `.codex/hooks.json`, including handler multiplicity
   and relevant definition fields. Unrelated user hooks cannot mask absent project hooks;
   partial/disabled/mismatched project coverage fails before thread launch. No gate command,
   review prompt, or checklist was copied into factory source.
5. Compaction now waits for its matching `turn/completed`, not the earlier compaction
   `item/completed`. It drains late usage, invalidates current context, and reports failed or
   disconnected compaction honestly. Compaction pricing remains incomplete by design.
6. `tests/unit/test_app_server_worker.py` covers these protocol/enforcement/compaction paths.
   `tests/integration/test_runtime_workflows.py` now reads contracts from this repository's
   pinned vendor tree instead of depending on the operator's sibling harness checkout.

Final worker SHA-256:
`e49ede3640572d8908a82e7f608c45fd80e8b2806303491052c7aad49df2d7d5`.

## Verification and retained evidence

Operator artifacts are ignored by Git and must be retained on this host. They include
private operational history; do not publish their raw database or transcripts to GitHub.

| Evidence directory under `artifacts/` | What it establishes |
| --- | --- |
| `runtime-rollout-20260907T003736Z/` | Schema-4 backup, DDL, migration output, before/after row digests, initial real protocol/hook failures. `factory-schema4.db` is mode 0600. |
| `runtime-validation-followup/` | Repeated failing canary; cwd/feature hypotheses ruled out; legacy exec refusal; corrected app-server refusal; reviewer write refusal; red regression output; real detached interruption and same-thread recovery; final gates. |
| `runtime-usage-followup/` | Digest-bound raw default-config usage, real compaction, Sol→Terra switch, known/missing resume baseline probes in reviewer and build; corrected worker reduced-window compaction and subsequent default-config resume. Read its `README.md`. |
| `runtime-isolation-followup/` | Real simultaneous bind and clone VMs, real SQLite admission race, cancellation/recovery, stopped-sandbox inspection, reusable probe and digest index. |

The final gate command is:

```sh
node .agents/vendor/harness/hooks/gate_report.mjs --force --json
```

Its retained report is `artifacts/runtime-validation-followup/factory-gates-final.json`.
**Final verdict: PASS.** Ruff check, Ruff format --check, mypy, and pytest all returned
exit 0 with empty output tails.
The preceding run passed lint/format/pytest but found a mypy annotation issue in the new
fixture; the protocol fixture list was annotated before the final rerun. Consult the final
report for the completed result, not the earlier `factory-gates.json` failure.
There are 49 passing focused worker/model-probe tests. Full-suite counts are not inferred
from empty output tails. Mypy's configured source/test paths cover this change; fake-sandbox
unit/integration tests alone do not establish runtime behavior.

### Actual runtime observations

- Build `factory-build-runtime-validation-20260906`, reviewer
  `factory-review-runtime-validation-20260907`: Codex **0.149.1**. The earlier existing
  Python reviewer had 0.146.0; do not reuse manifests across names or versions.
- Corrected build canary: actual protected `uv.lock` edit refused by the hook, file unchanged.
  Corrected reviewer: actual write refused by read-only enforcement. Hook definitions were
  discovered/loaded without persistent trust-store writes.
- Actual factory detached envelope survived the spawning host process exiting; its holder
  was adopted by PID 1 while a real model turn continued. Targeted interruption returned
  exit **143**. A new detached worker resumed the same thread, returned schema-valid output,
  and accounted only **17,919 new input / 15 output tokens** against the retained baseline.
  Root attempt snapshots were copied outside the candidate workspace to
  `runtime-validation-followup/attempts/`; see `build-interruption-result.json` and
  `build-recovery-result.json`.
- Compaction keeps cumulative billed counters monotonic and resets the current-context
  observation. The compaction item precedes the compact turn's terminal event. Known resume
  baselines count only new usage; absent baselines remain incomplete. Explicit model changes
  do not necessarily emit `model/rerouted`; this was not mislabeled as an automatic reroute.
- The corrected worker's actual **80% trigger** was measured with a declared test-only
  20,000 context-window override (effective 19,000), using actual runtime observations,
  not fabricated token notifications. Item completion < turn completion < final normalized
  completion. Default-config resume afterward passed. Separate default-window probes in
  both sandboxes measured compaction/usage/model-switch semantics at window 258,400.
- Both bind and clone: two real detached workers held the same internal port and same-name
  dependency/temp/SQLite paths with separate values. Cancelling one returned 143 while the
  survivor progressed; a fresh attempt recovered in the cancelled sandbox. Eight separate
  host admission processes admitted exactly two slots. See
  [isolation measurement](runtime-isolation-followup-2026-09-06.md) for precise scope.

## Machine state and boundaries

- Live store: `/Users/james/factory/state/factory.db`, **schema 5**. Final read-only check
  again matched all 1,889 existing row digests; `operator_settings` remains empty.
- `gui/501/com.jchen.factory` is unloaded. The manual console on port 7717 was stopped
  (TERM/INT did not exit it; it required KILL). The old BAC-49 log follower was stopped too.
  No process held the live database at checkpoint. Do not restart schema-4-only code.
- Both runtime validation sandboxes and all four isolation test sandboxes are stopped.
  Artifact/sandbox identities are retained; none were removed. The usage agent stopped its
  two sandboxes after its final measurements.
- `factory-build-python-harness-2` unexpectedly carries sandbox-scoped
  `FACTORY_GITLAB_TOKEN`, visible after startup but omitted from its first stopped inspect.
  No model was launched there. Do not widen the `nemoclaw-dev` exception or reuse that
  sandbox for clean build validation. The binding was not changed; fresh names were used.
- No `codex-*` sandbox, `~/.codex/config.toml`, generated vendor/main, credential scope,
  live ticket, or production setting was changed. No `~/.factory/` was created.
- The macOS `.venv` was preserved. Sandbox Python environments remain VM-specific.

## Exact next work after compaction

1. Read this handoff and final gate report; inspect `git diff`. The implementation and
   evidence are local and need review/publication as a factory fix PR before deployment.
   Re-run checks only for new changes, failures, or unresolved concerns.
2. Complete **target-project acceptance** before selecting a live adapter or increasing
   concurrency. The current measurements use disposable projects/stock VMs. They do not
   prove a production project's exact template, kits, mounts, dependency installation,
   full tracker/Git delivery lifecycle, or `factory resume` state transitions end to end.
   The bind measurement proves resource separation, not secrecy between worktrees sharing
   a mounted project root. Do not relabel these results as production manifests.
3. Retain complete build/review compatibility manifests and project isolation manifests
   only for the exact runtime/sandbox/project configuration actually measured. The old
   hook failure is resolved locally, but do not claim whole-production rollout success
   from that fact or from the gate suite. Natural 80% crossing at the full default window
   and automatic model rerouting were not induced; compaction pricing and unknown model
   price bands intentionally remain incomplete.
4. Stage the concrete runtime/concurrency selection and writer restart for James's
   activation decision. Schema approval is already spent successfully and need not be
   requested again; it is not blanket approval for live activation or concurrency changes.

[Runtime rollout procedure](runtime-rollout.md) remains the control document.
[Initial host checkpoint](runtime-validation-2026-09-06.md) retains the original failures
and migration details; this handoff supersedes its activation-blocker status.
