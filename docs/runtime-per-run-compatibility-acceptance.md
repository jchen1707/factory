# Per-run sandbox compatibility acceptance

The objective is factory runtime acceptance. Disposable CRUD completion is not required.
This continuation follows merged factory #88 (`f58a339`) and uses a separate validation
store with the live registry, live mount roots, and unmodified production sandbox specs.
No live run, tracker issue, readiness label, routing or project setting was changed.

## Measured identities

Production `isolation.prepare` assigned run `4282c8f9380c458d` these names:

- `factory-build-crud-live-20260907-4282c8f9380c458d`
- `factory-review-crud-live-20260907-4282c8f9380c458d`

The run is a suspended compatibility fixture in
`artifacts/runtime-per-run-compatibility/validation.db`, not a live ticket run.
`build_spec` and `_review_spec` supplied the actual specifications without the earlier
four-worker experiment's CPU/memory overrides. The build uses its private clone and
per-run protocol mount; review uses read-only source and its own per-run scratch mount.
Both report Codex CLI 0.146.0. The worker SHA256 is
`f2b3751989c9810250554ec4b35dfcb9eec07b00d11fc60b0a6f81e9168b93be`.

Credential admission uses James's existing project-only credential acknowledgement.
It does not establish that a future proxy configuration cannot grant capability.
No credential value was recorded or new acknowledgement introduced.

## Results

All six compatibility checks passed on both identities. Production `select` returned
`AppServerAdapter` for both roles in the validation store. A different identity was
refused with `app-server-compatibility-incomplete: runtime or sandbox changed`.

| Check | Actual observation |
| --- | --- |
| Hook enforcement | Both model-driven protected-path edits were refused; raw hook events recorded blocked status. |
| Schema output | Canary and recovery workers exited 0 with schema-valid `{"ok":true}`. |
| Sandbox isolation | Build marker existed only in its private clone; reviewer write failed with read-only filesystem error. Git HEAD, status and diff remained unchanged. |
| Detached durability | Both holders were adopted by PID 1 and remained running for over 63 seconds after launcher exit, with actual sleep processes observed. |
| Recovery | Targeted worker-group interruption returned 143. New workers resumed the same threads, counting only 10,436 build / 10,197 review input tokens after baseline subtraction. |
| Usage semantics | Both raw probes observed monotonic cumulative usage, explicit compaction/reset, Sol-to-Terra model change and same-thread resume. |

Evidence and digest-bound manifests are in
`artifacts/runtime-per-run-compatibility/`. Each role retains its specification,
identity, credential admission, canary/recovery transcripts, raw usage observations,
isolation result and verified summary. `selection.json` retains production selection
and the negative identity check. Manifests were not installed as live project settings.

These bounded probes do not establish natural 80% compaction, automatic rerouting,
full ticket delivery or four concurrent complete workflows. Raw compatibility probes
retain usage evidence but are not a production invocation-ledger/accounting evaluation;
do not add them to live completion or cost-per-accepted-change metrics. Interrupted
worker-group exit does not prove every descendant exited; final sandbox stop bounds
remaining processes. No full-workflow success is inferred from the schema-only response.

## Operational limit and next use

Both new sandboxes were stopped after validation; `stopped.json` verifies their state.
The canonical `gate_report.mjs --force --json` returned PASS: Ruff check, Ruff format,
mypy and pytest each exited 0 with empty output tails. No gates were skipped.
Mypy checks configured paths and does not cover these artifact scripts; fake-sandbox
tests do not establish real compatibility. `factory-gates.json` retains the output.
No production source changed. Console read-back returned HTTP 200.

A compatibility manifest authorizes only the measured sandbox identity, runtime and
worker. This fixture cannot authorize future ticket-run IDs. Production selection must
continue to reject a missing manifest or an identity mismatch. Do not rename/copy a
manifest to grant another identity admission.

Before a real approved workload's first model attempt, retain its assigned run names,
provision their production specifications, and perform these same checks on both names.
Keep the attempt held until exact manifests pass selection. Repeat for each new identity
and after runtime/worker changes. There is no automatic compatibility certification
of newly created per-run VMs in the current factory implementation. This is an ongoing
operational requirement, not a claim that this fixture prevalidates all future runs.

FRO-12 remains parked; FRO-13/14 depend on it and FRO-15 depends on both. Do not change
those dependencies, manufacture readiness or finish CRUD to fill four slots. Capacity
four is configured and measured separately; four full live workflows remain unobserved.
The next useful workload is independently approved work that exercises a missing check
or supplies real delivery metrics. Repeating synthetic compatibility pairs alone will
not measure delivery benefit.
