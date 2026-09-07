# Runtime implementation handoff

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
