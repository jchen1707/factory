# P2-2 — Phase 2's validating run (step 4)

2026-08-21. `factory run BAC-5` drove one approved Linear ticket all the way to
`reviewing`, which is §19 step 4's outcome in full: a real `factory run <TICKET>`
reaching `verifying → reviewing` with a `gates.json` whose gates actually ran. This
note is the evidence and the close of Phase 2.

## The run

```
approved → claimed → context_loaded → sandbox_creating → sandbox_ready
        → worktree_ready → implementing → verifying → reviewing
```

Every hop `auto`, recorded in `transitions`. Ten of ten eligibility conditions passed.

| | |
| --- | --- |
| Run | `b9270221542e47e5`, attempt 1 |
| Ticket | BAC-5 "Extract pages from a PDF and chunk them" (parent BAC-2) |
| Project | `python-harness`, `base_branch=v2`, `stack=python` |
| Base ref | `origin/v2` (`47961a7` — the vendored `--base` gate_report) |
| Sandbox | `factory-build-python-harness-2` |
| Thread | `01a02550-3ffb-7103-96ac-20b107778855` |
| Model | `gpt-5.6-sol` |
| Exit | 0 |
| Tokens | 9,589,052 in (9,403,136 cached), 45,983 out; `usd` NULL per §18.3 |
| Commit | `dd2033a` "feat: extract and chunk PDF pages" |
| Files | `src/app/ai/retrieval/ingestion.py`, `tests/ai/retrieval/test_ingestion.py`, `docs/architecture.md` |

The attempt directory holds `events.jsonl` (242 KB), `stderr.log`, `sbx-exec.stderr`
(0 bytes), `heartbeat`, `exit = 0`, a schema-valid `last-message.json` and `gates.json`,
`prompt.md`, `request.json`, `schema.json` and a `manifest.json` carrying a sha256 for
each.

## The headline: the factory↔layer-A seam defect is fixed, proven in the real pipeline

Step 4 existed to surface one defect, and it did — twice. The seam: the factory commits
the agent's `files_changed` **before** verify runs, so the working tree is clean when
`gate_report.mjs` asks "did this app change?". Layer A answered that question with
`git status --porcelain` (uncommitted turn edits — correct for the Stop hook, wrong for
the factory), so every gate came back `skipped_unchanged` regardless of what changed.
The §15.1 cross-check treats `skipped_unchanged` against a non-empty claim as a
disagreement, so any honest `gates_run` blocked on `evidence-mismatch` and the only path
to `reviewing` was a vacuous `gates_run: []`. Two earlier real runs confirmed this:
BAC-3 (attempt 1) and BAC-5's first background run both blocked on `evidence-mismatch`
with all gates `skipped_unchanged`.

The fix (Option A, "fix the seam at layer A"): `gate_report.mjs --base <ref>` switches
the "did this app change?" check from `git status --porcelain` to
`git diff --name-only <base>..HEAD` — the committed change since the run's base ref. The
factory's `verify.py` passes `--base <run.base_ref>` (`origin/v2`). Harness PR #14
(layer A, plugin 0.6.0 → 0.7.0) + factory PR #7 landed; vendor-sync put the new
`gate_report.mjs` + `gatedChangeSince` into both consumers (python-harness PR #63,
frontend-harness PR #36, pin `7c13a2079`).

**Proven two ways on the same tree:**

*Manual contrast (the same commit, only `--base` differs):*

| gate | `gate_report.mjs --json` (old) | `gate_report.mjs --json --base origin/v2` (fix) |
| --- | --- | --- |
| ruff check | skipped_unchanged | pass |
| ruff format --check | skipped_unchanged | pass |
| mypy | skipped_unchanged | pass |
| pytest | skipped_unchanged | pass |
| pytest -m integration | skipped_unchanged | not_applicable |
| **verdict** | pass (vacuous) | **pass** |

*The real run's `gates.json`* (`.factory/run/1/gates.json`, produced by the factory's own
verify step in the build sandbox):

```
verdict: pass
  ruff check:            pass (exit 0)
  ruff format --check:   pass (exit 0)
  mypy:                  pass (exit 0)
  pytest:                pass (exit 0)
  pytest -m integration: not_applicable
```

Every gate **ran** (`pass`, exit 0) — not `skipped_unchanged`. The seam is closed.

## The evidence cross-check matched a real, naturally-written claim

The companion defect (PR #5): `_evidence_mismatch` matched the agent's `gates_run`
strings exactly against the report gate `name`, but the agent writes full command
strings (`"ruff check: passed"`) and the report uses bare names (`"ruff check"`), so
every claim mismatched regardless of gate status. PR #5 pairs a claim with the report
gate whose `name` it contains, longest-first; the gate's status still comes from the
report.

The agent's claim (from `last-message.json`):

```
gates_run: ["ruff check: passed", "ruff format --check: passed",
            "mypy: passed", "pytest: 171 passed, 3 deselected"]
```

Each claim contains a report gate name; every matched gate is `pass`; the
`pytest -m integration` gate is `not_applicable` and the agent **deliberately omitted**
it — *"the integration command is omitted from gates_run because it did not exercise
changed files"*. That is the PR #5 implement-prompt guidance working: claim only gates
run against files the change exercised; for a gate the change did not touch, the honest
entry is nothing. No mismatch → `verifying → reviewing`, not `blocked`. This is the first
real run to reach `reviewing` through real running gates; the earlier two blocked.

## Checks

| Check | Result |
| --- | --- |
| `preflight:toolchain` | pass (node v22.22.1) |
| `preflight:harness-skip-verify-unset` | pass |
| `preflight:no-secrets-in-vm` | pass (`mcpgateway` source uploaded) |
| `preflight:vendored-tree-intact` | pass — `OK: vendored layer A matches harness@7c13a2079` |
| `preflight:protect-paths-refuses` | pass (`uv.lock` refuse-to-hand-edit fired) |
| `hook_denials` | **fail — and this is the good news** |
| `vault_snapshot` | pass |
| `implement_result_schema` | pass |
| `gate_report_schema` | pass |
| `gate_report` | pass (verdict pass, all gates pass) |

`hook_denials` records layer A refusing an inline interpreter mid-run:

> Command blocked by PreToolUse hook: Refusing tool call - inline interpreters can read
> inherited secrets. Command: `uv run python -c "import inspect, pypdf, fpdf; …"`

Recorded `fail` on purpose: a denial never reaches the JSON stream, so without the check
the evidence would show a clean run for a turn that was blocked. It does not stop the run
— a refused write is enforcement working, not a defect. Same shape as BAC-4 (P1-3).

## What this proved that nothing had proved before

- The factory↔layer-A seam defect is fixed **in the real pipeline**, not a fake: the
  vendored `gate_report.mjs --base origin/v2` ran post-commit in the build sandbox and
  the gates executed (`pass`/`fail`) rather than all-`skipped_unchanged`.
- The PR #5 evidence cross-check matches a real agent's naturally-written `gates_run`
  claim to the report by gate name, and the honest-claim guidance (omit gates the change
  did not exercise) holds: the run reached `reviewing`, not `blocked("evidence-mismatch")`.
- The full `implementing → verifying → reviewing` transition, end to end, against the
  real `python-harness` `origin/v2` with the new vendored hook.
- BAC-5's `pypdf` dependency (delivered via BAC-3 → PR #62, merged to `origin/v2`) was
  present in the worktree; the agent used it without scope-creeping into BAC-3's job
  (`out_of_scope` lists BAC-7 indexing/storage metadata and the cancelled BAC-9 slice).
- `factory run` in the **background** (nohup) survives across turns: the 5400 s
  implementing timeout ran to completion and recorded the transition. The foreground
  orphan pattern that killed two earlier attempts did not recur.

## How the run was produced (the two earlier BAC-5 attempts)

This is the third BAC-5 attempt. The first two are kept here because what they say
about how the factory behaves under a killed driver is still load-bearing.

1. **First background run** (`74720ae90f764dfc`) — blocked `evidence-mismatch`. Real code,
   real `gate_report.mjs`, but **all gates `skipped_unchanged`** (the seam, pre-fix). The
   agent's truthful non-empty claim mismatched → blocked. This is the run that surfaced
   the seam defect.
2. **Orphaned re-run** (`f84f4e098d224473`) — a prior session re-ran against the new
   vendored `--base` hook; codex committed a complete passing re-implementation
   (`e6c195d`) on `origin/v2`, but the driver was killed before `verifying` (the
   foreground/orphan pattern). Left at `implementing`, lease held, no `last-message.json`.
   Its work was preserved (tag `bac-5-impl-e6c195d`, pushed to origin) before the run was
   cancelled, so `factory cancel`'s branch `-D` did not lose it. `factory cancel` of a
   registered worktree fully cleaned the `.factory/` dir this time (the leave-behind
   defect from P1-3 did not recur).
3. **This run** (`b9270221542e47e5`) — launched in the background (nohup), reached
   `reviewing`.

## What is still open

Phase 2 is validated. These are not Phase 2 gaps:

1. **The orphan footgun is operational, not fixed in code.** `factory run` in a
   foreground Bash call is orphan-prone: a tool timeout or session end kills the driver
   after spawn but before the transition, and the detached holder cannot record it. The
   mitigation is nohup, which worked here. A `factory run --detach` that returns after
   spawn and lets `factory status`/a poller advance the run would remove the footgun —
   that is Phase 4's (the poller, §19).
2. **The kept work is unmerged.** `dd2033a` sits on `feat/BAC-5-…` in the worktree,
   unpushed. Phase 2 deliberately stops at `reviewing` — no push, no PR. Phase 3 adds the
   reviewer sandbox and delivery. The orphan's preserved re-impl is on tag
   `bac-5-impl-e6c195d`; this run's `dd2033a` supersedes it on the same base.
3. **BAC-3 is still James's pending action in Linear** — mark it Done. It is In Progress
   with `needs-info`; the factory has no Done code path and does not read Linear
   `blockedBy`. It does not block BAC-5 (which already ran).
4. **`generate-main.yml` regenerating `main`** in python-harness / frontend-harness runs
   in the background and blocks nothing — the factory builds from `origin/v2`.
5. **The protect-paths hook over-blocks new files in `docs/discovery/**`.** Its stated
   `why` — *"a later run that disagrees writes its own file rather than editing the
   record"* — contemplates new files, but `blockReason` (protect_paths.mjs) refuses every
   write matching a protected glob, create or edit. This evidence file itself needed a
   one-off authorization to write. The intent and the implementation disagree; reconciling
   them is a harness-layer-A change, out of scope here.

Phase 3 (reviewer sandbox + delivery) is next.