# Phase 4 finish — resolve the two open questions, then build the §18.5 console

## Context

PR #19 (`feat/phase-4-tick-and-recovery`) is **merged to main** (`21e1ed2`). The Phase 4
handoff (`.agents/plans/phase-4-handoff.md`) leaves exactly three things: two open
questions to resolve, and the §18.5 operator console (the only remaining build). The
daemon-under-the-timer is James's human decision and is **out of scope** for this session —
I surface it, I don't execute it.

The two questions, both already investigated against the code:

1. **Q1 — does `verify` re-run the gates on a `--from verifying` resume, or read the stale
   `gates.json`?** The code already re-runs: `recovery.resume` into `verifying` only
   `advance`s; the next tick's `reap` returns `START_NEEDED` and `_drive_from_here` calls
   `verify.start`, which spawns a fresh gate report; `verify.collect` reads the freshly
   written `gates.stdout.txt` only after the new `exit` appears. `clear_liveness` clears
   only `exit`/`heartbeat`, never the report, but `collect` is gated on the new `exit`, so
   a stale report is never read. Re-running is the honest choice (it surfaced FRO-6's hidden
   lighthouse failure) and is now safe (`env-gate-failed` blocks instead of looping).
   **Decision: keep re-running; pin it with a test. No behaviour change.**

2. **Q2 — the clone worktree on a `--from verifying` resume.** For a `--clone` run,
   `ctx.worktree` is the host project path; the gate report runs *inside the build sandbox*
   against the clone at that path. The clone is **shared across runs of the same project**
   (sandbox named `factory-build-<project>`), and `clone.create_branch` checks out each
   run's branch at `worktree_ready`. So if another run ran between the original run and a
   `--from verifying` resume, the clone is checked out on the *other* run's branch and
   `verify.start` runs the gates against the wrong tree. `--from reviewing` dodges this
   (`review.start` calls `fetch_back` fresh); `verify` does not. **Decision: fix
   `verify.start` to re-establish the run's branch in the clone before spawning the gate
   report.**

## Branching

All new work branches off **`main`** (PR #19 is merged):

- `feat/phase-4-verify-resume-fixes` — Q1 + Q2 (small, stdlib-only). Own PR.
- `feat/phase-4-console` — the §18.5 console (adds web deps). Own PR, as the handoff
  mandates ("its own PR, not folded into this one").

Build Q1+Q2 first, mergeable independently; then the console.

---

## Part A — Q1 + Q2  (branch `feat/phase-4-verify-resume-fixes`)

### A1. Q2 fix — `clone.ensure_on_branch`

New function in `src/factory/steps/clone.py`, beside `create_branch`:

```python
def ensure_on_branch(ctx: Context) -> None:
    """For a clone run, make sure the clone is on the run's branch before a step reads
    its tree. The clone is shared across runs of a project; a second run checks out its
    own branch, so a resume into verifying long after would run the gate report against
    the wrong tree. Idempotent: a no-op when the clone is already on the branch.
    If the branch is gone (sandbox was recreated), block as `clone-branch-missing`
    rather than silently cutting a fresh empty one — a resume that lost the agent's
    commits is a human decision, not a re-run."""
```

- Reuse the existence check pattern from `create_branch`
  (`git -C <project_path> rev-parse --verify --quiet refs/heads/<branch>` via
  `ctx.sandbox.exec_sync`, `.ok`).
- Exists → `git -C <project_path> checkout <branch>` (via `clone._exec`).
- Missing → `raise Blocked("clone-branch-missing", …)`.
- `branch = ctx.branch`; `None` → `Blocked("no-branch", …)` (same as `fetch_back`).

### A2. Q2 wiring — `verify.start`

In `src/factory/steps/verify.py:start`, after `attempt_dir.clear_liveness()` and before
spawning, for clone runs only:

```python
if ctx.project.requires_clone:
    from factory.steps import clone as clone_step

    clone_step.ensure_on_branch(ctx)
```

Covers all three callers uniformly: forward path (no-op, implement just committed on the
branch), orphan/timed-out re-run (`_rerun_detached`), and `--from verifying` resume. The
`exec_sync` checkout starts a stopped sandbox; `exec_detached` then runs the gate report in
it. Comment in `start` explaining *why* (the shared-clone hazard), one sentence.

### A3. Q1 — pin the re-run decision with a test

No code change. Add to `tests/integration/test_phase4.py`, beside
`test_resume_a_blocked_at_verifying_run_re_enters_verifying`:

`test_a_verifying_resume_re_runs_the_gates_rather_than_reading_a_stale_report`:
1. `_to_verifying(ctx)` (attempt dir, no gates.json yet).
2. Write a **stale** `gates.stdout.txt` + `gates.json` with `verdict: fail` into the attempt
   dir (simulating a prior run's output the re-run must not trust).
3. `_block_at(ctx, VERIFYING, "env-gate-failed")`.
4. Set `fake.gate_report` to a **pass** report (the fresh reality).
5. `recovery.resume(ctx)` (re-enters verifying).
6. `verify_step.run(ctx)` (start → await → collect).
7. Assert `ctx.state is State.REVIEWING` (the fresh pass won, not the stale fail), and a
   fresh detached gate-report invocation landed (`fake.detached` grew).

Mutation-check: if `collect` read `gates.json` instead of re-running, the stale `fail`
would block and the assertion fails.

Add a one-line comment at `verify.collect`/`verify.start` noting the decision is deliberate
(re-run, not read-stale; honest + bounded by `env-gate-failed`).

### A4. Q2 test — `tests/integration/test_clone.py`

`test_a_clone_verify_resume_re_establishes_the_run_branch_before_running_gates` (uses
`clone_ctx`):
1. `_to_verifying(clone_ctx)`; record `branch = clone_ctx.run.branch`; assert the clone is
   on it.
2. Simulate another run repointing the clone: `git(clone, "checkout", "-b",
   "feat/other-ticket")`.
3. `_block_at(clone_ctx, VERIFYING, "env-gate-failed")`; `recovery.resume(clone_ctx)`.
4. `verify_step.start(clone_ctx)` (the tick's START_NEEDED action).
5. Assert the clone's `HEAD` branch is back to `branch` (the run's branch), not
   `feat/other-ticket`.
6. Mutation-check: removing the `ensure_on_branch` call leaves the clone on
   `feat/other-ticket` → assertion fails.

Plus `test_a_clone_verify_resume_blocks_when_the_branch_is_gone`:
force the branch missing (`git(clone, "branch", "-D", branch)` or checkout a detached
head and delete), call `verify_step.start`, assert `Blocked("clone-branch-missing")`.

### A5. Verify A

`uv run pytest tests/integration/test_phase4.py tests/integration/test_clone.py -q`,
`uv run mypy`, `uv run ruff check`. Then mutation-pass: revert each fix in turn, re-run the
two new tests, confirm red; restore.

---

## Part B — the §18.5 console  (branch `feat/phase-4-console`)

Spec: `SOFTWARE-FACTORY-PLAN.md` §18.5 (line 1884) + acceptance rows (line 2788). §18.5
**builds the CLI forms first**, then `factory serve`. Reuse: `store.py`'s query API
(`all_runs`, `transitions`, `checks`, `effects`, `spend`, `active_runs_for_project`,
`runs_in_states`, `live_run_for_ticket`, `attempt_row`), `routing.ModelFacts.usable_context`
+ `MODEL_CACHE` (denominator), `artifacts.log_event`/`log_dir`, `agent/codex.parse_events`
(event parser), `policy.assert_factory_sandbox` + `policy.requires_human` (F26), the real
`docs/discovery/codex-events.md` fixture (P0-7).

**Context-% (P0-7 settled it):** the stream carries **neither** the window nor a
percentage; `model_context_window` is absent from `thread.started`. So:
`context_pct = latest_turn.input_tokens / routing.ModelFacts.usable_context`. Numerator
from the latest `turn.completed.usage.input_tokens` (P0-7-confirmed); denominator from
`~/.codex/models_cache.json` via `usable_context` (exists). If the cache is missing the
model → hide the percentage and state the reason ("no context window on file for
`<model>`"). Never estimated.

### B1. Dependencies (`pyproject.toml`)

Add `fastapi`, `uvicorn`, `pydantic`, `structlog` to `[project] dependencies`, and update
the stdlib-only comment (lines 13–17) to say the control plane is stdlib-only **except** the
loopback console, which holds no credential of its own (§18.5). `ruff` per-file `S603/S607`
ignores: none needed (no subprocess in the console beyond reusing existing adapters).
Verify `uv sync` resolves.

### B2. Event + context module — `src/factory/console/events.py`

- `parse_turn_usage(events_path) -> TurnUsage | None`: read `events.jsonl`, return the
  latest `turn.completed` `input_tokens` (and in/out/cached for display), or `None` if no
  turn completed yet. Reuse `agent/codex.parse_events` where it fits; this module is the
  console's read-only view (no `Transcript`/`Usage` mutation).
- `context_pct(usage, model) -> float | None`: `usage.input_tokens /
  routing.usable_context(model)`; `None` when the cache lacks the model. Pin to a fixture
  copied from a real `artifacts/<run>/1/events.jsonl` (P0-7 shape).

### B3. CLI forms (built first) — `src/factory/cli.py`

New subcommands, thin, read-mostly:

- `factory status --all` — runs board (View 1) as a table: ticket, project, state (+ badge),
  attempt + ladder rung, elapsed-in-state, context% or `— (no window)`, tokens in/out/cached,
  spend vs `$20`, latest `item.*` activity, heartbeat age. Reuse `store.all_runs`,
  `transitions`, `spend`, `attempt_row`, `console.events`.
- `factory status <TICKET> --evidence` — run detail (View 3): transition timeline (actor,
  rule), gate report table with pass/fail/unavailable/not_applicable chips + caveats,
  review findings ranked, artifact links. Read `gates.json`/`review-summary.json` from the
  attempt dir.
- `factory logs <TICKET> --follow` — tail `events.jsonl` (the runbook currently points at
  the file directly because this does not exist). `--follow` blocks on the file growing
  (no `tail -f` subprocess; stdlib `time.sleep` + read offset).
- `factory runtimes` — View 2: `sbx ls --json` joined to `active_runs_for_project`; grey
  out non-`^factory-(build|review)-` sandboxes (operator-owned `codex-*`).
- `factory config models` — View 4 read form: render `models.toml` roles/efforts/budget.

Register on the argparse subparser in `cli.py:main` (follow the existing `tick`/`daemon`
shape). F26 lives in the `serve` controls, not here.

### B4. `factory serve` — `src/factory/console/app.py` + templates

- FastAPI app, **loopback-only** (`host="127.0.0.1"`, default port 7717). `factory serve
  --port <p>`.
- One server-rendered page per view (`console/templates/*.html`, Jinja2 if FastAPI ships it;
  else stdlib `string.Template` — prefer the lightest thing that renders a table). No
  frontend build step, no bundler.
- SSE endpoint for the live tail (View 1 + View 3 events stream); reuse
  `console.events.parse_turn_usage` on each tick.
- **View 5 — Controls:** per-run POSTs: Suspend, Resume, Resume-from-planning, Cancel, Retry
  now. Each routes through `policy.requires_human` and writes a `transitions` row with
  `actor="human"` (reuse `recovery.suspend`/`recovery.resume`/`cancel` machinery; the
  ledger records the effect). **No Merge button** — link out to the PR.
- F26: every control aimed at a sandbox routes through `policy.assert_factory_sandbox`
  (the way `sbx.py` does); a control targeting a `codex-*` sandbox is refused.

### B5. Tests

`tests/unit/test_console.py` + `tests/integration/test_console.py` against fakes:

- `factory serve` renders every non-terminal run with state, attempt, ladder rung, tokens,
  spend vs ceiling, liveness; refreshes without reload (SSE wired).
- Context %: shown from P0-7-confirmed `input_tokens`/cache window, **or** hidden with the
  reason stated when the cache lacks the model; never estimated. (Unit test with a fake
  cache + a fixture events.jsonl.)
- Editing a model/effort in the console rejects reviewer == builder model **in the form**
  (reuse `routing`'s validation) before the write.
- Every control writes an `actor="human"` transition; **no Merge button** present in any
  template (assert the string is absent).
- F26: a control aimed at a `codex-*` sandbox is refused by `assert_factory_sandbox`.

### B6. Runbook

`docs/runbook.md`: replace the "the runbook currently points at the log file directly"
clause with `factory logs <TICKET> --follow`; add the console URL + `factory serve` start;
note the daemon-load is still James's decision (do not load it here).

### B7. Verify B

`uv sync`; `uv run pytest tests/unit/test_console.py tests/integration/test_console.py -q`;
`uv run mypy`; `uv run ruff check`. Smoke: `uv run factory serve --port 7717`, `curl
127.0.0.1:7717/` returns the board; confirm `factory logs BAC-4 --follow` tails a real
`events.jsonl`. Then mutation-pass on the context-% and no-Merge-button tests.

---

## What this session does **not** do

- **Load the daemon under the timer.** That is James's decision (§19). The fix from A2 +
  the `env-gate-failed` block + the F23 ladder bound the loops that would burn the attempt
  budget unattended, so loading is safe once James says so — but I do not run
  `launchctl bootstrap`. I will leave the runbook note + a one-line reminder.
- **The two carry-over footguns** in the handoff ("sensitive dirs" dead config;
  `deliver._archive` silent failure). They are recorded defects, not Phase 4 blockers;
  leave them for a follow-up unless you ask otherwise.

## Handoff update

When Part A and Part B land, update `.agents/plans/phase-4-handoff.md` "Next session":
mark Q1 + Q2 resolved (with the decisions above), mark the console built, and leave only
the daemon-load human decision.