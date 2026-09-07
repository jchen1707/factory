# Nested accounting completeness correction — 2026-09-07

Factory now labels an invocation's estimate incomplete when retained runtime events positively link a child thread to its parent. The priced parent subtotal remains a known lower bound for subsequent-attempt budgets. No child usage is fabricated or added.

The real active calibration established disjoint parent and child counters while the parent's app-server connection received child notifications. Its frozen pre-correction replay therefore demonstrated a concrete display defect: a complete parent-only estimate could appear to cover the invocation despite the retained child evidence. The original calibration trace and replay remain untouched.

`accounting.collect` recognizes exact parent linkage in `subAgentActivity` items (`params.threadId` plus `agentThreadId`) and `thread/started` metadata (`parentThreadId` plus child `id`). Unrelated thread IDs do not establish ancestry. Observed child IDs are deduplicated and sorted. With positive linkage, telemetry records `nested_accounting.complete=false`, preserves the parent pricing result as `parent_estimate`, and changes the overall estimate to `complete=false`, `usd=null`. Request estimates and `known_usd` remain available. Absence of a signal does not create a claim that no descendants exist; missing historical pricing remains unknown.

The focused regression failed against the original source because the parent estimate remained complete. After correction, 24 nested-accounting and existing runtime-workflow tests pass, as do scoped Ruff checks. The initial scoped mypy command checked only `accounting.py`; the root’s full gate subsequently caught overly narrow inferred JSON-event types in the new test. Adding an explicit heterogeneous event-list type fixed that test-only issue, and full `uv run mypy` then passed all 121 source files. The failed full-gate evidence remains retained. Tests cover idempotence, exact ancestry, unrelated activity, duplicate children, incomplete historical evidence, unchanged parent token totals, and budget refusal using the parent lower bound.

A separate offline collector replay reads the frozen active normalized events into a new scratch Store. It discovers child `01a07a35-f918-7883-b727-bfecc59f8f3a`, makes the overall estimate incomplete, and retains **$0.0624096** as the priced parent subtotal. Collecting twice yields identical persisted results, one cost row with unknown whole-invocation USD, and the same known-spend lower bound. The parent's 35,979 input and 141 output tokens remain unchanged; the child's 23,699 input and 112 output tokens are not added. All original baseline replay file hashes remain unchanged.

Evidence: `artifacts/runtime-nested-accounting-correction/` contains `red.txt`, the executable `replay.py`, copied normalized `events.jsonl`, exact pricebook, `first.json`, `second.json`, `result.json`, a new `factory.db`, and `original-hashes.json`. The root's combined final four-gate output is retained separately at `artifacts/runtime-accounting-policy-gates/factory-gates.json`.

The worker source is unchanged by this correction; its SHA-256 remains `4647d194bb59e4b9c1c5d5e2cfd91efc8b3426f2a753eea706e67d0dc2199c1d`. This replay uses existing captured events and production accounting, with no new model invocation, sandbox operation, ticket work, or original-store mutation. Full nested invocation admission, routing, accounting, recovery, and whole-tree completeness remain separate work; this correction makes the current omission visible and preserves conservative budget accounting.

Existing historical invocation rows are not automatically backfilled: the runtime store
accepts only a newer event sequence. Recollecting an already stored transcript at the
same sequence retains its previous telemetry. The separate fresh-store replay above
proves corrected collection, not a migration of earlier complete estimates. Any historical backfill
would need explicit, reviewable reprocessing using retained evidence; none was performed.

Correction commit: `a37c6de`. All four final factory gates pass at combined source
`ec36c41`, with bounded independent review finding no concrete defect.
