# Runtime certification and delegation acceptance

This report covers the approved runtime extension: Astra support, automatic certification,
and factory-owned child execution, including writable integration. Synthetic workloads are
sufficient. Ticket delivery and customer application hardening are outside this acceptance.
Live deployment is separate from implementation acceptance.

Status: implementation and synthetic acceptance complete. Final factory gates, shared/consumer
checks and independent reviews pass. Ready for publication and the separately approved rollout;
no live deployment or schema migration has been applied.

## Evidence already retained

| Requirement | Observed behavior | Retained evidence |
| --- | --- | --- |
| Astra discovery and inference | In the same disposable sandbox and unchanged provider context, 0.146.0 omitted Astra; 0.153.4 advertised it and completed schema-valid high/xhigh turns. No alias or credential transfer. | Original `runtime-certification-implementation` artifacts under `/Users/james/factory/artifacts/`; diagnostic and Step 1 sections of the implementation handoff. |
| Automatic compatibility and launch protection | Build/reviewer six-check service, independent launcher race, restart without duplicate paid probe, tampered evidence, environment and replacement-generation launch refusals. | `artifacts/runtime-certification-service/`, `artifacts/runtime-workflow-real/`, and `docs/discovery/runtime-workflow-certification-acceptance.md`. |
| Read-only children and native thread transfer | Two real children, 43.176s overlap, source-write EROFS, results, three accounting replays; parent resumed in the same VM generation/native thread and refused old-owner handles. | `artifacts/runtime-child-real/run-2/{child-result,readonly-all-result,resume-result,cleanup-result}.json`. |
| Clone source isolation | Actual VM commits, index, dirty/untracked source exported; stale host checkout unchanged; private dependencies retained and excluded. Snapshot replay reused exact publication; child source write returned EROFS. Zero model calls. | `artifacts/runtime-child-source/run-4/result.json`; earlier failed runs retained. |
| Real stack aliases | A full clone of frontend consumer `8429ecf` retained `.claude/agents` as a relative alias through VM export, host snapshot replay and read-only child mount. VM commits/staged/dirty/untracked source and private dependencies were preserved; child write returned EROFS. Both VMs removed. | `artifacts/runtime-child-source/run-5/result.json`; actual frontend/Python alias inventories also retained in `shipped-stack-aliases.json`. Zero model calls. |
| Cold-start fingerprint stability | Native `thread/start` was proven to persist VM-private project trust. Durable zero-turn preparation settles that configuration before certification. A fresh clone then passed all six checks on its first fingerprint and launched the builder. | `docs/discovery/runtime-cold-parent-fingerprint.md`, `artifacts/runtime-cold-fingerprint/`, and writable acceptance run 1. |
| Clone restart identity | Four later specification hashes were reproduced solely from the changing loopback host port of sbx's internal Git forwarding. Only this clone transport locator is normalized; exposure, destination, protocol, other ports and generation remain checked. | `artifacts/runtime-cold-fingerprint/writable-port-reconstruction.json`; sandbox regression suite and independent review. Real explicit stop/restart preserved the full identity while the loopback port changed from 49250 to 49251; the retained native parent thread then resumed. |
| Eight-agent capacity and restart | Four parents plus four child native turns overlapped for 44.3169s. Four parents resumed their retained native threads. Induced controller loss caused three parent failures and owned live-child cancellation; a fourth live subtree was suspended. | `artifacts/runtime-capacity-lifecycle/run-1/observed-result.json`. |
| Writable child execution and integration | Sol parent polled two actual Terra medium children to terminal results, with 123.146s native-turn overlap backed by live process observation. Each child proved its own `0 != 1` acceptance failure, full two-test GREEN and native Stop gate completion. Disjoint four-file artifacts integrated once, preserving the private dirty note; intentional conflict remained intact until explicit fixture resolution. Both integrated functions return 1, declared gates pass and three integration/accounting replays are stable. | `artifacts/runtime-writable-acceptance/run-2/integration-result.json`; base `c7e73ef`, integrated commit `052bceb`. |
| Independent integrated-candidate review | A fresh read-only reviewer VM passed all six checks. A distinct Sol thread inspected the integrated diff and independently passed both named tests, returned schema-valid zero findings, and left its snapshot unchanged. Common certified admission/accounting was used; this is not a new full `review.start`/redphase workflow claim. | `artifacts/runtime-writable-acceptance/run-2/independent-review/result.json`. |
| Live approval and draining | Each writable child held across two advances without a lease until its exact approval. With a parent and both children running, lowering cap 4→2 preserved all three leases and live process groups; an approved extra reviewer admission was refused, then cap 4 restored. | `artifacts/runtime-writable-acceptance/run-2/{approval-observations.jsonl,drain-result.json}`. |
| Targeted cancellation and uncertain acknowledgement | Real disposable processes proved targeted child cancellation leaves parent and sibling alive. One uncertain signal intent retained capacity; three replays did not resend, and natural exit preceded lease release. All three processes became terminal. This is zero-model lifecycle evidence, not three more model invocations. | `artifacts/runtime-writable-acceptance/run-2/cancel-processes/result.json`. |
| Owned child cleanup | Public GC refused the wrong generation, left dry-run source unchanged, then stopped/removed the exact owned generation and preserved host artifacts. Zero model calls. | `artifacts/runtime-capacity-lifecycle/gc-real-2/result.json`; 32 GC regressions cover accounting, leases, missing artifacts, age, restart and replacement during cleanup. |
| Schema 5→6 | Read-only online backup of the live database migrated privately. All 1,892 existing rows retained identical per-table digests; SQLite quick check passed and foreign-key violations were zero. | `artifacts/runtime-rollout-readiness/migration-result.json`. Private database is not a publishable artifact. |

The eight-turn interval is derived from native start/events and the measured worker tool
deadline, not a simultaneous process-list sample. That cohort validates admission, overlap,
recovery and targeted cancellation; it is not eight uninterrupted task completions. It also
retains the first cohort's refusal of an overly long sleep instruction. The successful bound
does not erase the failed/limited workload evidence.

Capacity accounting retained 128 invocation records, 32 explicitly incomplete estimates and
a known API-equivalent lower bound of USD 3.71655492. Three reconciliation replays left the
records and estimates unchanged. Missing request detail is not synthesized into exact cost.
All 12 capacity experiment VMs were removed; host evidence and source were preserved.

The first writable acceptance run exposed fixture and coordination failures, which remain
visible: the initial parent ended without polling pending children, and relocated child
canaries failed because a synthetic hook command contained an absolute parent path. Factory
cancelled the unlaunched requests and refused the mutated canaries; no writable child application
launched. The private parent clone and modified canaries were preserved. All five lifetime VMs
were removed, and three accounting replays retained the known API-equivalent USD 1.3116868.
The replacement run uses portable consumer hooks and independent vertical slices from a passing
baseline. Both actual children completed and integration passed. Each also initially ran root
unittest discovery with zero tests, then corrected the command to the real two-test suite without
human candidate repair. Those unsuccessful commands remain retained. Independent review also passed. Its test command succeeded before a trailing informational
macOS `stat` invocation failed on Linux: the overall exit 1 is retained, while named-test output
and execution of the following `&&` commands establish the test exit 0. Final process-lifecycle measurements and generation-checked cleanup passed. All four run-2 VMs
were removed and individually verified absent. The final private clone, integrated history and
unrelated dirty note were preserved; the host fixture remained clean and active leases were zero.
`run-2/cleanup-result.json` retains final inventories and three stable accounting replays.
A subsequent fresh `run-2/final-inventory.json` confirms all nine lifetime VMs across both
writable cohorts are absent.

Run 2 retained 44 invocation records and known API-equivalent USD 1.2639784. Twelve records are
explicitly incomplete: eight interruption/compaction records, one unpaid admission canary and
three zero-model process fixtures. Run 1 retains thirteen incomplete records. Combined known
USD 2.5756652 is below the USD 8 experiment allowance; missing request detail remains incomplete,
so this is a known estimate rather than an exact account bill. No model call was needed to
reconcile the reviewer's successful tests and later informational command failure.

## Factory verification and review

`node .agents/vendor/harness/hooks/gate_report.mjs --force --json` passed on the final
implementation source, including the alias, clone-port, settings-error and integration-cancellation fixes. All four gates ran with
exit 0 and empty output tails: Ruff check (132 ms), Ruff format (73 ms), mypy (544 ms), and
pytest (291,784 ms). Report: `artifacts/runtime-rollout-readiness/factory-gates-complete.json`.
Mypy includes `src` and `tests` (182 files). These tests exercise control-plane behavior with
fake sandbox boundaries; the separate real measurements above establish runtime effects.
Earlier working/completion reports retain caught fixture typing/formatting failures and are
not the final pass.

Independent Standards and Spec/security reviews found no remaining actionable issues after
fixes for integration index locking, VM-only candidate Git inspection, cost lower-bound display,
GC ownership, clone port identity and internal aliases. The final alias review independently
ran 35 source/integration tests. This review does not replace real acceptance.

## Cross-repository verification

Shared `harness` commit `cada9f200` includes the runtime contracts and current `v2` changes
through `f1511d8`. Its `scripts/check.py` passed. Canonical vendoring produced Python commit
`f66c81b` and frontend commit `8429ecf`; factory vendors the same exact source.

Both consumer gate reports pass. Python's PostgreSQL integration gate was not applicable
to generated contract changes. Frontend's browser gate was not applicable; Lighthouse was
already disabled. These are explicit gate states, not claimed test executions.
Shared, Python and frontend `generate_main.py` checks all passed in separate artifact trees.
Vendored integrity passed; remote `v2` freshness remains dependent on James merging the
shared changes and the normal managed sync/publication process. No remote refs were altered
to manufacture a freshness pass.

Reports: `artifacts/runtime-rollout-readiness/{shared-upstream.log,python-gates-final.json,
frontend-gates-final.json,generation-shared.log,generation-python.log,
generation-frontend.log,*-vendor-integrity.log}`.

## Final plan-audit corrections

Settings validation errors now retain submitted values, including malformed numeric text and
unknown selections, with escaped output, a focusable error summary and labeled native controls.
All 32 console tests pass; independent review found no actionable issue. The approved keyboard
acceptance uses DOM inspection, not a claim of a browser automation run.

Cancellation during integration was reproduced using actual file application and a second SQLite
controller recording Cancelled or Suspended. The old integration continued with later writes.
A fresh state fence now stops subsequent mutation and retains the settled in-flight file receipt,
original bytes and child artifacts under a named integration hold. The driver observes that hold
without changing the operator's state or writing to the tracker. Explicit resume can recover a
suspended integration; a cancelled run cannot silently resume. Evidence is retained under
`artifacts/runtime-integration-cancellation/` and described in
`docs/runtime-integration-cancellation-acceptance.md`; 35 focused tests and independent review pass.
This deterministic controller test is separate
from real VM/model cancellation acceptance.

## Operational limits that remain explicit

- Snapshot export preserves canonical relative aliases within the exported tree, including
  the stacks' tracked `.claude` aliases. External, missing, cyclic or noncanonical links,
  submodules, unmerged indexes, changing inventories and oversized exports refuse with work
  preserved. Writable artifacts can retain baseline aliases but cannot add, edit or delete them.
- Uncertain zero-model preparation acknowledgements retain their intent and require operator
  reconciliation. Controller restart cannot blindly repeat the operation.
- Lowering capacity drains existing work. Reducing the cap below already admitted parents
  can require draining/suspending work or restoring capacity before queued children progress.
- Incomplete telemetry remains visible. Known estimates are API-equivalent costs, not Codex
  account charges, and cannot prove an exact account bill.
- Certificates bind actual generations, runtime/helper bytes, mounts, environment, authority
  and probe implementation. A template tag or an earlier VM's pass never authorizes a new VM.
- Existing clones without protected mailboxes retain their old execution path until an
  explicit preservation/drain transition; they are not silently rebuilt or mounted differently.

The rollout document supplies merge order, migration preparation, runtime packaging,
activation and rollback. James owns merges, schema 5→6 approval and live deployment.
