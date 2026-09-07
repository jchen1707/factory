# Runtime rollout

## Current completion criteria — 2026-09-07

**The objective is factory acceptance, not completion or hardening of disposable
CRUD tickets.** Each test-workload action must serve a named factory requirement;
stop driving that workload once its required evidence is obtained. The reproduced
CORS finding already exercises defect detection and blocked delivery. Fixing it or
finishing FRO-12–15 is necessary only if it enables another missing factory check.
Use targeted isolated experiments for the remaining gaps instead of making the whole
CRUD delivery sequence a prerequisite. James explicitly clarified this scope.

James merged harness #33, Python #77, frontend #55 and factory #83. All consumers
vendor merged shared source `8bc104e33`. [Harness #34](https://github.com/jchen1707/harness/pull/34)
now updates the stack gitlinks to the exact merged commits; local pin/current and
shared contract checks pass. After James merges it, require post-merge Meta currency
and Generate main success. No further consumer refresh is needed for a pin-only change.
Factory #83 has no remote workflows/checks; its retained four-gate result is local
evidence. Exact commits and next steps are in [the handoff](runtime-implementation-handoff.md).

This is the execution-status update for James's approved four-repository improvement
plan. It does not replace or reduce that plan's requirements. All major feature areas
have implementations and the original implementation PRs are merged. **Supported rollout validation now passes at source `c7dacd3`, with the explicit
runtime and historical-data limits below.** All four local factory gates pass in
`artifacts/runtime-final-acceptance/factory-gates.json`. This does not activate a
production adapter or waive shared/consumer merge checks.
Real testing has exposed integration defects; corrective changes are published in factory #83 and harness #32/#33, with dependent
consumer changes in Python #77, frontend #55 and draft CRUD #1.

The final continuation closes the targeted runtime gaps with measured restrictions
and corrections. [Profile reviewers](runtime-policy-model-workflow-acceptance.md)
respect declared requirements/deferrals; Volume model tuples are supported, while
unavailable Astra presets fail closed. [Native nesting is disabled per invocation](runtime-nested-fallback.md)
on the opt-in app-server adapter so child launches cannot bypass factory admission.
Legacy exec behavior and incomplete historical costs remain explicit limitations.
[Final build/reviewer compatibility](runtime-final-app-server-compatibility.md) passes
for worker `f2b37519…`; [worker hash binding](runtime-worker-binding-acceptance.md)
rejects older manifests. [Resume baseline](runtime-resume-baseline-acceptance.md) and
[collector corrections](runtime-accounting-observation-acceptance.md) preserve honest
token/context/cost reporting across failure and compaction. The current final source,
gates and remaining operator sequence are recorded in [the handoff](runtime-implementation-handoff.md).

Required implementation gaps and failed acceptance checks must be resolved and retested
as part of this work, before declaring completion or activating the affected features.
Do not defer them to post-activation work. Testing and corrective implementation can
alternate; no new planning phase is needed.

| Approved area | Accepted evidence and explicit limits |
| --- | --- |
| Delivery policy and authority | Host conflict/deferral/immutable-snapshot/stale-evidence checks, actual CLI replacement, and three actual profile reviews pass. One standards axis per profile establishes the scoped reviewer policy behavior. |
| Model selection and workflow | Volume tuple availability passes; Astra-dependent High-confidence roles are unavailable and refused. Actual diagnosis→repair→verification complements deterministic two-repair, unchanged-evidence, lifetime and budget guards. Approval holds and preserved-work recovery are measured. |
| Separate test design and vertical TDD | Actual app-server test design produces scenarios/boundaries and holds the next approval. Actual base replay observes API/web assertions; classifier regressions prevent ImportError/AttributeError summaries from serving as assertion proof. Historical false-positive rows remain invalid. |
| Runtime and context | Final build/review worker compatibility passes. Compaction, current/cumulative separation, freshness and model changes have exact-runtime evidence; final-worker recovery is measured separately. Native nested tools are disabled on start/resume; no implicit adapter switch. |
| Accounting | Dated pricing, request context bands, duplicate reconciliation, failed-resume baselines, malformed-event lower bounds and budget checks have retained evidence. Historical/legacy usage without request or child evidence stays incomplete; API-equivalent estimates are not account charges. |
| Parallel execution | Actual bind/clone builder/verifier/review paths, isolated resources, admission races, slot queueing, draining, base refresh and targeted control checks are measured. Final cancellation/suspension evidence is linked in the current handoff; model and deterministic-process scopes remain distinct. |
| Composable stacks | Four default presets and selected optional combinations installed and passed 32 host gates; generation checks pass. This does not claim every platform or external-provider integration. |
| Evaluation | Real isolated outcome metrics reconcile, including zero accepted changes and unknown cost per accepted change. Comparative delivery improvement requires longer-term cohorts and is not claimed. |

Completion also requires factory's four Definition-of-Done gates, layer-A contract
checks, consumer generation checks, and the planned real sandbox measurements against
the final revisions. Publish corrective PRs for James to review and merge. A passing
unit suite, scaffold or protocol probe establishes only its measured scope.

Only production activation, raising live concurrency, and longer-term measurement of
delivery improvement wait until after this validation step. Activation requires James's
explicit decision on concrete settings and writer restart. Schema 4→5 is already
applied with all 1,889 rows preserved; do not repeat it.

The original implementation scope used completed Backend tickets only as historical
evidence. James later authorized the separate disposable CRUD verification workload.
That exception covers only the new test project/tickets; existing products and tickets,
especially `nemoclaw-dev`, remain excluded. Production writers remain stopped.

Resume from [the current handoff](runtime-implementation-handoff.md). FRO-12 passed
real readiness recovery, supported suspend/resume, implementation and ten independent
gates, and completed all eight isolated reviews. It is blocked on one high-severity
CORS finding, reproduced and awaiting James's disposition. Corrective source and consumer PRs are merged; explicit authority refresh remains
a prerequisite for changing FRO-12’s pinned declarations; independent scratch experiments may use exact corrective
source revisions without changing that run. Do not start broad intake or claim full
CRUD delivery or complete acceptance.

## Historical rollout baseline

2026-09-06 operator checkpoint: the approved live schema 4→5 migration preserved all
1,889 rows. Follow-up fixes passed scoped real runtime and isolation probes; production
activation has not occurred. The fixes remain local. See the current
[implementation handoff](runtime-implementation-handoff.md) and
[initial host evidence](runtime-validation-2026-09-06.md).
The implementation-era status statements below describe the original rollout baseline.

This branch changes orchestration and shared contracts. It does not apply a production
migration, change tickets, activate app-server, or raise a live project's concurrency.
Real sandbox compatibility and isolation measurements remain required. `sbx` is unavailable
in the implementation environment; protocol fixtures are not substitutes for those measurements.

## Release order

1. Review and merge shared source into `harness@v2`.
2. Regenerate vendors with `scripts/vendor_sync.py sync --harness PATH --target TARGET`.
   Commit each consumer's exact pin. Local feature commits can be synced for testing;
   remote freshness cannot pass until the shared commit is published on `v2`.
3. Review stack catalogs and their generated-project installation reports.
4. Review the factory migration and code, then schedule operator-controlled activation.
   Generated `main` branches are produced through the existing generation workflow.

## Migration

Stop factory writers and retain a database backup before an operator applies the migration.
Preview the DDL without opening or modifying the database:

```sh
factory migrate --database /absolute/path/to/factory.db
```

Schema 5 adds invocation telemetry, settings/audit events, policy snapshots, project slots,
and failure episodes. Existing runs, attempts, and effects are retained. Existing schema 4
stores require explicit migration. New temporary test stores initialize directly.
After James approves the displayed DDL, the operator can run:

```sh
factory migrate --database /absolute/path/to/factory.db --apply
```

The apply command refuses older schema versions whose upgrades were not included in this preview.
No production database has been migrated during implementation.

## Runtime and model selection

Existing runs retain `codex exec`. Project runtime selection affects new runs. An app-server
run retains its selected adapter and compatibility-directory setting; each launch checks the
executing sandbox's `codex --version` and the manifest named `<sandbox>.json` in that directory.
The invocation retains the compatibility report, selected model, effort, preset, and usage.

Each manifest contains `runtime_version` (exact command output, stripped), `sandbox`,
`worker_sha256`, and `checks`. Every check requires `status: "pass"`, a retained evidence filename, and its SHA-256.
The required checks are `hook_enforcement`, `schema_output`, `sandbox_isolation`,
`detached_durability`, `recovery`, and `usage_semantics`. Capture actual runtime effects:
protected-path denial, schema-valid output, reviewer write refusal, detached process survival,
recovery after interruption, and observed usage/compaction semantics. Keep raw events.
Build and review sandboxes need separate manifests. A changed runtime, sandbox or worker
invalidates that evidence. The worker hash is checked during selection and again before
copying the invocation worker. The manifest is operator-owned, outside candidate workspaces.

The opt-in app-server worker disables native nested-agent launches on both thread start
and resume using the measured invocation-local `agents.enabled=false` setting. Factory
schedules each model role through its own approval, resource and budget checks; its
independent reviews remain available. Legacy exec runs retain their adapter behavior.
Historical nested usage remains visibly incomplete; it is not inferred or backfilled.
This restriction is part of selecting the new adapter, not a production configuration
change or an implementation of arbitrary nested-agent admission.

```sh
factory configure --project PROJECT --agent-adapter app-server --app-server-compatibility /absolute/evidence/directory
factory configure --project PROJECT --model-preset volume
```

Presets are `existing`, `volume`, and `high-confidence`, independent of delivery profiles.
App-server validates each chosen model and effort against the executing `model/list` response.
Legacy exec presets use a metadata-only executing-sandbox probe: initialize and model/list,
without creating a thread or invoking a model. The actual attempt retains CodexAdapter.
Existing routing remains usable without a probe. A switch back to `codex-exec` affects new runs only.

Current context appears only from validated current-window observations. It warns at 70%,
requests compaction at a completed-turn boundary at 80%, and becomes stale after 120 seconds.
Compaction and model changes invalidate the prior context measurement. Thread usage is kept
separate from invocation deltas; a resumed thread without a retained baseline stays incomplete.

Spend is **API-equivalent estimated USD**, not Codex account charges. Cached input, cache writes,
output, service tier, pricing date, and long-context treatment affect estimates. Unknown request
attribution and compaction usage remain incomplete. Long-context classification is currently
supported by retained documentation for Astra and Sol; Terra/Luna request bands remain unknown.
Priced lower bounds still count toward the existing budget before the next agent attempt.
Historical reviewer usage is never invented.

## Delivery policy and workflow

Declare `delivery` in the target repository's `harness.config.json`. Shared interpretation lives
in harness. Prototype, Core, and Hardening declare requirements and visible deferrals; each
deferral names its rationale and revisit condition. Generated presets begin with all their
engineering gates required. Projects must explicitly declare any permitted deferral.

```sh
factory configure --project PROJECT --delivery-profile core --workflow diagnosis
factory configure --project PROJECT --mode approval
factory configure --ticket BAC-123 --approve-attempt 2:implement:1
```

Approval mode prevents the next agent launch. Verification and observation may continue.
Use Suspend to stop current work. Approval keys include the attempt, step, and launch ordinal;
an old approval cannot authorize a retry. Project and run controls are available in the console.

The run snapshots source revisions, profile, app policies, reviewer inputs, and file hashes.
Snapshots reject symlinks and escaping paths; later reads check the complete file inventory.
Candidate changes cannot replace their own policy. Explicit replacement requires a paused run:

```sh
factory configure --ticket BAC-123 --replace-policy hardening
```

The console exposes the same explicit action. Replacement increments the authority revision;
verification and review evidence from the old revision cannot authorize delivery.

Approved tickets pass readiness before implementation. Missing product decisions return to a
human; technical gaps receive a short execution brief. Test design remains separately configurable.
Builders follow vertical failing-test/implementation slices. `/plan` remains interactive and optional;
unattended diagnosis uses a structured handoff. Historical planning artifacts remain readable.

Only a code diagnosis citing the latest digest-bound verifier evidence authorizes automatic repair.
An episode allows at most two repairs within lifetime and spend limits. Repeated unchanged failure
blocks further repair. Environment, stale-authority, requirement, and review-dispute diagnoses
pause with distinct reasons for setup correction, authority refresh, or human disposition.
Neither diagnosis nor compaction can silently dismiss a review finding or approve scope changes.

## Parallel execution

Existing explicit concurrency limits are preserved. Raising a limit requires per-run isolation
and a retained isolation manifest. The manifest names `project`, `layout` (`bind` or `clone`),
and checks for `two_runs`, `dependencies`, `temporary_files`, `databases`, `ports`, and
`cancellation`, each with pass status and a digest-bound retained evidence file.

```sh
factory configure --project PROJECT --isolation per-run --isolation-measurement /absolute/isolation.json --concurrency 2
```

The console exposes these settings. New per-run configurations default to two after validation when no explicit project limit exists.
Existing explicit limits are preserved; there is no unmeasured default increase. `--concurrency inherit` uses the existing registry
limit. Lowering the limit drains active work. Per-run sandbox identities persist across restart;
clone protocol and reviewer writable mounts are run-specific. Cleanup uses recorded identities.
The approved sandbox-delivery exception cannot opt into per-run isolation through these controls.

Admission uses SQLite transactions across daemon and CLI processes. Reviewer axes execute one at a time within
the admitted run slot. Each axis returns to the host for accounting, a fresh approval ordinal,
and a budget check before the next launch. Approval waiting does not consume execution timeout. Shared host Git maintenance and clone fetch/mirroring are serialized.
Before verification, an isolated branch must contain the current integration base. Delivery
checks that the base has not changed since verification. A stale branch is preserved and pauses
for integration refresh and renewed verification/review; conflicting integration is not discarded.

Measure both bind and clone layouts with two simultaneous runs before activation. Include targeted
cancellation and recovery. No such real measurement was possible in this environment.

## Composable stacks and evaluation

Stack catalogs live at `scaffolds/components.json` in each consumer. Existing top-level dependency
choices are preserved. `new_project.py compose` takes `--catalog`, `--preset`, optional repeated
`--component`, and `--into`. Python provides minimal/FastAPI; frontend provides minimal TypeScript/
React-Vite. Optional providers, databases, retrieval/agents, and clients are explicit components.
Catalog conflicts prevent selecting server provider SDKs for the browser preset.

Run the repeatable installation checks from the shared checkout:

```sh
python3 scripts/validate_compositions.py /path/to/python-harness/scaffolds/components.json --reports /tmp/python-preset-reports
python3 scripts/validate_compositions.py /path/to/frontend-harness/scaffolds/components.json --reports /tmp/frontend-preset-reports
```

The validator builds fresh temporary projects, runs configured installation, and delegates all
gates to the shared gate runner. Raw reports distinguish installation failures from gate failures.

```sh
factory metrics --database /absolute/path/to/factory.db --project PROJECT
```

Metrics report retained human interventions, repeated failure episodes, completion rate, and
estimated cost per accepted change. Accepted means explicitly recorded completed after merge;
PR creation alone is not acceptance. Cost per accepted change includes unsuccessful runs' costs.
Incomplete historical observations remain labeled. The historical fixture names BAC-53/54/49/22
and cites their source URLs, but contains curated evaluation scenarios, not captured transcripts.
Compare cohorts only after collecting sufficient outcomes under each delivery/model preset.
