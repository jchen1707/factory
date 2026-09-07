# Runtime acceptance inventory

**Scope:** this inventory measures factory requirements. Disposable ticket completion,
app hardening and fixing the CORS defect are not acceptance objectives of their own.
Choose only workload actions needed to close a named evidence gap; FRO-12 may remain
parked. Shared cross-stack CI is now green at `5f4e3dd`; dependent consumer freshness
still requires James’s shared-source merge and exact-pin regeneration. Continue the
remaining factory checks. Passing local gates does not establish remote merge readiness.

Final continuation checkpoint: FRO-12 completed implementation and all ten independent
gates, followed by all eight review axes. It is blocked on one reproduced high CORS
finding; seven axes returned no findings. See
[the final review evidence](runtime-crud-review-findings.md) and the current handoff.
One runtime-capacity failure was retried through supported recovery without rerunning
completed axes, with failed and retry invocations retained separately. Both CRUD
sandboxes are stopped and run mode is Approval. This adds real review/recovery evidence
but does not establish an accepted change, complete accounting, or full workflow acceptance.

Read-only evidence audit, 2026-09-07 UTC, against the current completion matrix in
[runtime-rollout.md](runtime-rollout.md). This is an inventory, not an activation
approval or a fresh test report. It covers existing retained artifacts and test
implementations at the current working tree. Other agents are continuing validation;
new reports should supersede the corresponding open items below explicitly.

**The implementation is not yet fully accepted.** Existing evidence is stronger than
“only unit tests”: real runtime, mount, compaction, cancellation, isolation and admission
measurements exist. Their scope does not establish the complete ticket lifecycle.
Existing products and tickets remain excluded; only the disposable CRUD workload may
receive new live verification actions. Production writers stay stopped.

## Subsequent measured progress — 2026-09-07 UTC

This section supersedes corresponding open items in the audit-start table below.

- **Readiness and supported recovery now have real workflow evidence.**
  [The recovery report](runtime-crud-recovery-acceptance.md) records recollection of
  completed readiness without another planner, the implementation approval hold,
  and explicit launch. Actual `factory suspend` and `factory resume` preserved HEAD,
  branch, tracked diff and every untracked file digest, then resumed the original
  Codex session in attempt 2. This proves preserved committed base and dirty work;
  no new builder commit existed at the suspension boundary. Unapproved resume
  prevented launch but exposed a presentation exception; its correction has six
  regressions and full gates, and was not induced a second time live.
- **Exact build compatibility now passes.**
  [The build report](runtime-crud-build-acceptance.md) records all six checks for
  `factory-build-crud-20260907`, independently of the reviewer manifest. Both
  operator-owned manifests are in `artifacts/crud-runtime-test/compatibility/`.
  FRO-12 remains a legacy exec run; this does not claim app-server factory delivery.
- **Installed dependencies are measured in both simultaneous layouts.**
  [The follow-up](runtime-isolation-installed-dependencies-2026-09-07.md) installed
  actual `idna` versions 3.10 and 3.11 in independent real virtualenvs, exercised
  their encoder, and retained distinct versions through targeted cancellation and
  restart. This closes the earlier injected-module limitation. These remain
  adapter-level workers, not two complete factory workflows or the entire CRUD
  dependency graph.
- **Composition acceptance covers all default presets and every declared optional
  component in selected combinations.** [The composition report](runtime-composition-acceptance-2026-09-07.md)
  records seven successful installs and 32 actually passing gates plus two refused
  browser/provider conflicts. It binds exact source/catalog inputs. This is host
  installation and scaffold behavior, not service/provider integration, Linux-native
  compatibility or every possible combination.
- **Actual accounting replay is idempotent within the measured scope.**
  `artifacts/crud-runtime-test/captured-accounting.json` records production
  `accounting.collect` replaying real planner events three times into a SQLite backup:
  one cost row, unchanged 1,415,348 input / 21,012 output / 1,306,496 cached tokens.
  USD correctly remains unknown because request-level tier/context pricing is absent.
  Row timestamps refresh; amounts do not. This is legacy aggregate evidence, not
  app-server pricing or proof of child-agent attribution.
- **Metrics now have retained real-outcome evidence.** The same backup report yields
  three runs, zero accepted changes, three interventions and three runs with incomplete
  cost. Estimated USD per accepted change is null. No delivery-improvement claim is
  supported by this cohort.
- **Read-only console rendering succeeds on real isolated data.** Production
  `create_app` ASGI TestClient returned 200 for `/`, `/projects`, and
  `/settings/runs/FRO-12`; `console-captured-state.json` and `console-*.html` retain
  results. Settings render model and incomplete cost. No fake adapters were injected,
  but this is not browser interaction or live control acceptance.
- **Corrections are published for James.** [Factory PR #83](https://github.com/jchen1707/factory/pull/83)
  contains recovery, test-design, accounting-role and monorepo red-phase fixes;
  `factory-gates-resume-holds.json` records all four gates after the approval-hold
  correction. [Harness PR #32](https://github.com/jchen1707/harness/pull/32) and
  draft [CRUD contract PR #1](https://github.com/jchen1707/factory-crud-verification/pull/1)
  await James; consumer remote freshness depends on shared-source merge. Thus the
  audit-start monorepo limitations below are now corrected in source, with final
  real replay and source/consumer rollout still distinct acceptance obligations.

## Current continuation acceptance and CI

- [Shared CI correction](runtime-cross-stack-ci-acceptance.md): exact failure reproduced,
  real sync/reporter regression red then green, both consumer default suites actually
  ran, and all three remote jobs passed at `5f4e3dd`.
- [Authority acceptance](runtime-authority-acceptance.md): 14 actual host checks passed
  for deferrals, conflicts, candidate weakening, snapshot integrity, explicit replacement
  and stale-evidence refusal. Successful CLI replacement and model reviewer adherence
  remain unmeasured; this is not full delivery acceptance.

- [Controller acceptance](runtime-controller-acceptance.md): real production host
  admission/guard/driver refusal, draining, separate-process state persistence and
  targeted slot reclamation passed. The input isolation manifest is constructed and
  identities are unprovisioned; actual simultaneous workflows, model/process cleanup
  and daemon recovery remain unmeasured by this experiment.

- [Diagnosis admission and handoffs](runtime-diagnosis-acceptance.md): six actual
  failing gate runs fed production failure provenance and repair admission. Two repairs
  were admitted; unchanged evidence, a third repair, tampered evidence and lifetime/spend
  limits were refused. Classifications were supplied by the experiment; actual model
  diagnosis and repair remain open. A reproduced porcelain whitespace defect was fixed
  with host/clone regressions and a successful real-host preservation rerun.
- [Separate test-design role](runtime-test-design-acceptance.md): actual app-server
  `plan.start`/`plan.collect` produced scenarios and boundaries, preserved baseline files,
  recorded one Sol/high invocation and held before implementation. This closes the
  separate-role execution gap and one real factory/app-server integration path, not
  the full workflow. Captured context renders 11% fresh then unavailable/stale under a
  controlled clock. This invocation's eight requests have a complete API-equivalent
  estimate; nested builder attribution and other-role completeness remain separate.

- [Real monorepo replay](runtime-monorepo-replay-acceptance.md): both candidate gates
  passed in the actual build VM; production replay applied each test patch to the base
  and observed API and web assertion failures. Nested clone dependency linking,
  immutable authority and scratch cleanup passed. This closes corrected replay
  integration, not historical FRO-12 pathspecs or full workflow delivery.

The additional handoff correction is committed at `746dfdd`; all four factory gates
passed in `factory-gates-handoff-inventory.json` after independent bounded review.
That final source report supersedes earlier gate reports for the handoff correction.

## Nested builder collaboration: remaining accounting evidence

A read-only inspection of FRO-12 attempt 2 `events.jsonl` observed builder messages
claiming delegation to two reviewers through the code-review skill, followed by
`collab_tool_call` wait events (items 46 and 48). Those retained events have an empty
`receiver_thread_ids` list, empty `agents_states`, and no model, effort or usage fields.
They establish a collaboration-tool interaction and the builder's claim of delegation;
they do not independently establish child launches, child count or completed reviews.
The initial inspection was partial. The completed transcript was subsequently audited:
132 events end in `turn.completed`, and all 12 collaboration events are wait
start/completion items with the same empty identity/usage fields. Retained evidence:
`artifacts/crud-runtime-test/completed-builder-collaboration-audit.json`. Completion
did not resolve child attribution.

`agent/codex.py::parse_events` sums usage from top-level `turn.completed` events and
retains the parent `thread.started` identity. It does not normalize collaboration
items. `accounting.begin` creates a record for each factory-scheduled invocation,
using the scheduled role's model/effort/preset; `accounting.collect` reconciles that
invocation from its transcript. Neither path creates child invocation records from
`collab_tool_call` events. The original wire events remain retained on disk.

Consequently the current evidence does **not** establish whether the parent's usage
includes, excludes or partially includes nested agents. It also cannot attribute
nested usage to actual child model/effort/preset or show a separate approval/budget
check before a nested launch. Do not count the claimed self-review as the factory's
independent reviewer stage or fabricate child usage. Resolve this acceptance gap with
actual supported child-lifecycle/usage evidence and explicit aggregation semantics;
if the executing runtime cannot provide that evidence, record the limitation visibly
and decide the supported orchestration behavior before claiming accounting for every
model invocation. No source fix or live action was made by this audit.

## Audit-start evidence map (superseded above where stated)

| Requirement | Concrete evidence already retained | Precise remaining acceptance |
| --- | --- | --- |
| Schema migration preserves data, backup precedes migration | `runtime-validation-2026-09-06.md` records stopped writers, SQLite backup and integrity checks; handoff records schema 5 with all 1,889 rows preserved. | None requiring another migration. Do not repeat the migration. Final activation still needs an explicit operator decision. |
| Prototype/Core/Hardening, deferrals, authority snapshots | `test_runtime_state.py::test_invocation_metadata_and_policy_snapshots_are_immutable`; `test_runtime_workflows.py` covers snapshot publication recovery, path/symlink refusal, modified/added snapshot files, and refusal to execute a candidate policy interpreter. `test_console.py::test_saving_mode_does_not_replace_the_displayed_run_policy` covers separate controls. | Exercise actual workflow with conflicting parent/child requirements, explicit deferrals, candidate self-weakening and operator replacement of a paused run's authority; prove old verification/review evidence cannot deliver. Pure and fake-adapter tests do not establish runtime reviewer behavior. |
| Model presets independent of profile; availability/effort; retained routing | `test_runtime_model_probe.py` covers supported/unsupported catalog responses, pagination, unchanged existing routing and legacy adapter retention. `test_runtime_workflows.py` covers pinned adapter/version and invocation preset despite future routing changes. CRUD reviewer raw `protocol/models.json` is executing Codex 0.146.0 evidence; real turns used Sol and Terra. | Validate the selected model/effort for every actual role and preserve each invocation's metadata. A model catalog plus two sampled roles does not prove all planned role combinations execute. Do not switch existing pinned runs implicitly. |
| Readiness, optional planning, separate test design, vertical TDD | `test_ticket_readiness.py`, `test_clone_plan_collection.py`, `test_handoff_fingerprints.py`; FRO-12 retained schema-valid ready output and VM-only execution brief. Handoff documents local serialization/collection corrections. | Recovery and test-design agents own readiness retry and separate-role behavior. Retain acceptance scenarios and test boundaries plus real failing-before-implementation slices. Resolve the monorepo replay limitation below before claiming factory red-phase verification. |
| Diagnosis classification, preserved handoffs, two repairs, unchanged evidence refusal | `test_handoff_fingerprints.py` proves timing/path changes do not authorize repairs and changed assertion evidence allows a second repair but never a third. `test_runtime_workflows.py` covers rephrasing within an episode, historical disputes/stale authority and failure counting. | Actual fresh diagnosis on a preserved worktree, reproducible failure, bounded repair, environment/authority/human-dispute routing, and retained committed/dirty work through supported recovery. These tests use controlled evidence and adapters. |
| Approval stops next model attempt; Suspend stops current work safely | `artifacts/crud-runtime-test/approval-held.json`, `approve-plan.json`, `plan-detached-observation.json`: real first planner approval hold and subsequent detached execution. `test_runtime_workflows.py::test_review_retry_requires_new_approval_and_preserves_both_invocations`; `test_phase3.py` covers per-axis approval/budget continuation; `test_phase4.py` covers suspend/resume and shared-sandbox preservation. | Actual retry and between-reviewer approval holds; supported suspend/resume with committed and uncommitted work preserved; no accidental extra launch. Raw process interruption is not full CLI recovery. |
| App-server hooks, schema, sandbox isolation, durability, recovery | `runtime-crud-reviewer-acceptance.md` and `artifacts/crud-runtime-test/reviewer-compatibility/factory-review-crud-20260907.json`: exact reviewer/version six-check manifest, protected-path hook denial, actual read-only refusal, schema output, PID-1 detached survival, interruption 143 and same-thread exact-worker resume. Earlier corrected stock probes are indexed in handoff. | Disposable build sandbox needs its own exact manifest. Full factory workflow/recovery parity remains separate. Production target manifests cannot be inferred from the reviewer or stock template. |
| Current context, intermediate updates, compaction resets, freshness, model changes | `test_telemetry.py` covers intermediate versus cumulative usage, invalidation, thread isolation and out-of-order events. `artifacts/runtime-usage-followup/README.md` records default-window build/reviewer protocol semantics and corrected-worker reduced-window compaction. CRUD reviewer raw protocol retains real compaction, Sol→Terra switch, resume and monotonic totals. | Final-worker build measurements, console intermediate/freshness observation, and reviewer invocation attribution through real orchestration. Natural 80% default-window crossing and automatic reroute were not induced; explicit switch is not automatic reroute. Reduced-window threshold testing is valid only with its override disclosed. |
| Cached/uncached/cache-write/output, long context, tiers, dated estimates | `test_pricing.py` covers cached/write/output pricing, long-context/service-tier treatment and missing historical/request detail. `test_runtime_workflows.py` covers failed usage, unknown fragments, interruption, replay reconciliation and budget lower bounds. CRUD reviewer resume baseline 11,090 input/137 output to 22,435/152 produces invocation delta 11,345/15. | Reconcile actual stored invocations across planner, builder, reviewer, diagnosis, handoff and failed attempts. Keep partially priced requests and compaction visibly incomplete. Do not claim natural long-context or every pricing tier was sampled live. Estimates remain API-equivalent, not Codex account charges. |
| Atomic project slots, safe limits/draining | `runtime-isolation-followup-2026-09-06.md`: eight host processes using separate real SQLite connections raced admission at limit two; exactly two admitted. `test_runtime_state.py` covers atomic connections, drain and targeted release; `test_runtime_workflows.py` covers required measurement manifests and preserved limits. | Actual ticket admission through CLI/daemon plus project UI active/queued/waiting behavior, lowering a live isolated test limit without killing work, and resource accounting across review steps. The separate real transaction race already passed; do not describe it as missing entirely. |
| Bind and clone simultaneous isolation, targeted cancellation/recovery | Same isolation report and `artifacts/runtime-isolation-followup/verified-summary.json`: two real workers in each layout; separate real venvs, same-name temp files/SQLite DBs/ports, protocol separation, targeted cancellation and survivor progress, restart in the original identity. | Two complete factory runs in both layouts, actual product dependency installation, scheduler/reviewer behavior, supported recovery and cleanup. Probe dependencies were tiny fixture modules in separate venvs. Restarted detached workers are not `factory resume`. No project-specific pass manifest was generated. |
| Serialized Git maintenance and integration freshness | `test_runtime_workflows.py::test_isolated_delivery_requires_verification_against_current_base`; clone integration tests cover stale host branches and current host-fetch bundle behavior. Real FRO-12 private clone proceeded after the bundle correction. | Parallel branch base advances followed by preserved work, refreshed integration, renewed gates/review and delivery refusal until current. Scratch VM resource probes did not exercise Git integration. |
| Composable minimal and existing stacks, optional components, generated guidance/gates | `artifacts/crud-runtime-test/sandbox-baseline-gates.json` records all ten FastAPI/React scaffold gates in the real build VM. `runtime-frontend-acceptance-2026-09-06.md` retains merged frontend v2 install and applicable five gates in a disposable VM clone (Playwright not applicable, Lighthouse disabled). Shared `scripts/validate_compositions.py` and catalogs exist. | Retain final-revision minimal Python and minimal TypeScript plus existing FastAPI/React preset installation/gate reports, selected optional component combinations/conflicts, layer-A contract and consumer generation checks. The audit found no complete retained composition report set in the scoped repository artifacts. Existing-stack evidence alone does not validate all combinations. |
| Interventions, repeated episodes, completion rate, cost per accepted change | `test_runtime_workflows.py::test_evaluation_preserves_unknown_cost_and_counts_explicit_approvals` and `test_repeated_failure_metric_counts_failures_even_when_next_repair_is_refused`; `evaluation.py` documents human transition/approval/replacement counts and explicit completed state. | Compare output with retained real isolated outcomes, including setup failures and incomplete usage. FRO-12 has not produced an accepted change. Improvement claims need comparable completed cohorts over time; a feature can pass its arithmetic acceptance before those cohorts exist. |
| Final verification/review/publication | Prior full factory reports: `artifacts/crud-runtime-test/factory-gates-current.json`; corrective changes and evidence are local, with latest readiness correction initially covered by 21 focused tests. | Root owns full four-gate run after final code changes and corrective PR publication. Retain exact source revisions for shared/consumer checks. James owns merges and activation. |

Test filenames above are under `tests/unit/` or `tests/integration/` as appropriate.
Artifact paths are local retained operational evidence, not automatically suitable for
publication; do not publish raw private transcripts.

## Audit-start priorities (source fixes and new evidence noted above)

1. **The disposable monorepo cannot currently obtain factory red-phase replay evidence.**
   `steps/redphase.py:124` returns `unavailable` and proceeds if root configuration
   lacks `tests`; the next branch does the same if no root `kind: test` gate exists.
   The disposable scaffold has both omissions. This follows the existing fallback
   policy, so it is not itself proof of an unauthorized bypass, but it cannot satisfy
   the new vertical-TDD acceptance claim. Even adding root declarations leaves a
   second seam: `steps/clone.py::scratch_add` links only root `node_modules`, while
   the scaffold frontend dependencies live at `apps/web/node_modules`. Address shared
   gate/capability declarations in layer A or target configuration; do not embed a
   second gate list in factory. Preserve actual per-slice builder failure evidence.
2. **Exact build compatibility is absent.** The reviewer manifest is valid for its
   named sandbox and runtime only. The current FRO-12 run remains legacy `codex exec`.
   A successful legacy planner and ten scaffold gates cannot establish the build
   app-server hook/schema/recovery/usage checks.
3. **Remaining workflow acceptance must connect already-passing lower-level pieces.**
   Dual-VM resource isolation and admission transactions are measured; full scheduling,
   review, integration-base refresh and recovery are not established by those probes.
   Avoid spending effort repeating the already-measured scratch effects while leaving
   orchestration seams untested.

No additional proven implementation defect was found by this bounded evidence audit.
The missing compatibility and workflow results are acceptance gaps, not inferred bugs.
