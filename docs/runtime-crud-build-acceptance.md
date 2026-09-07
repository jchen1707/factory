# CRUD build runtime acceptance

Measured 2026-09-07 UTC on `factory-build-crud-20260907`, template
`codex-pnpm:v1`, **codex-cli 0.146.0**. The existing private clone and FRO-12
plans were preserved. No run, authority, adapter or operator setting was changed;
no Linear call occurred. The sandbox was stopped and released to the parent
operator after measurement, before any FRO-12 continuation.

The worker was unchanged during this acceptance:
`f0bf1c95b6b98b9de84812f413bf42e6ccdf3ee74bd6ade2c7d1c0d92727c11f`.
The production build spec matched; capability inspection showed only the declared
MCP gateway binding, and `gh auth status` explicitly reported the inherited
acknowledged environment value invalid before any model call.

## Measured effects

- Actual app-server model tool invocation was refused by the project hook when it
  attempted a protected generated-contract edit. The worker completed schema-valid
  `{"ok":true}`, exit 0. Build threads used `danger-full-access` within the VM.
- A temporary marker under the private clone was readable in the VM while the
  same host path was simultaneously absent. It was removed afterward. This proves
  the actual clone's filesystem separation; simultaneous worker resource isolation
  remains a separate acceptance check.
- A detached real worker began `sleep 180`. Its spawning process exited, holder
  PID 90275 was adopted by PID 1, and production `poll` reported running. Targeted
  production `kill_group` produced wrapper exit 143.
- A fresh worker resumed the same thread and returned schema-valid output with
  complete incremental usage: **12,038 input / 15 output tokens**. The prior
  baseline was 11,495 / 491 and the new cumulative total was 23,533 / 506.
- Default-window raw app-server turns, compaction, explicit Sol→Terra change, and
  same-thread resume completed. Cumulative input totals were
  11,464 → 22,955 → 29,753 (compaction) → 41,433 → 56,723 → 72,039.
  Compaction reset current context while cumulative billed usage stayed monotonic.
- The private clone's HEAD, branch, complete tracked diff, and git status matched
  before/after. Existing untracked `.agents/plans/` remained. No model was asked
  to read or change those plans or implement CRUD.

Total measured input across all probes: **118,894 tokens**.

## Evidence and limits

Operator-owned evidence is in `artifacts/crud-runtime-test/build-compatibility/`.
It includes preflight, exact spec argv, actual clone marker observation, before/
after git state, raw protocols and attempt envelopes, same-thread baseline/recovery,
worker snapshots, and stopped inspection. `finalize.py` asserts the recorded
outcomes. `evidence-sha256.json` binds the retained raw copy outside the candidate.

`factory-build-crud-20260907.json` passes production `validate_compatibility` for
all six required checks on this exact runtime/sandbox. This is runtime
compatibility acceptance, not production activation or complete factory workflow
acceptance. Reviewer evidence is separate in
[runtime-crud-reviewer-acceptance.md](runtime-crud-reviewer-acceptance.md).

No automatic model reroute, natural 80% default-window crossing, or worker-triggered
compaction was induced here. Usage semantics were observed through the reusable
raw JSON-RPC probe; hook/schema/interruption/recovery used the exact production
worker and detached envelope. Full `factory resume` orchestration and concurrent
project resource isolation remain separate work. Raw private artifacts must not
be published with the public source/evidence summary.
