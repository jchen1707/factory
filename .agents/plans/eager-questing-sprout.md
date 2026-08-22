# Plan: build `suspend` and `resume` (Phase 4, §16.3b)

## Context

Phase 4 is under way on `feat/phase-4-tick-and-recovery`. The tick, reap, recovery, gc, and
the two-phase plan/implement steps are built. The next item the handoff names is
`cli.py: suspend` / `resume`, and it is the unblock for the live subject: **FRO-6** is parked
at `blocked` with a finished, gate-passing implementation that cost 10.5 M input tokens. The
cheap repair is to *re-enter `verifying`* — re-run the gate report (no model call) and the
review — not to pay for that implement a second time. Re-running from scratch is what
exhausted the Codex credit on BAC-4 once already.

Spec: `SOFTWARE-FACTORY-PLAN.md` §16.3b (line 1689), §5.3 (line 428). Working notes and the
three gotchas the spec does not contain: `.agents/plans/phase-4-handoff.md` "Build `resume`
next". This plan implements that section.

## The three gotchas from the handoff (each costs an hour to rediscover)

1. **`BLOCKED` has no edge to `verifying`/`reviewing`.** FRO-6 is blocked *at* `verifying` with
   a passing gate report; the cheap resume re-enters the state that blocked. Add both edges,
   both human-gated under the existing `unblock-is-a-judgement` rule.
2. **The attempt counter must not increment on a non-agent resume.** `verify.py` and
   `deliver.py` address `ctx.factory_dir / "run" / str(ctx.run.attempt)` (verify.py:76,
   deliver.py:150/172/212). Incrementing on a resume into `verifying`/`reviewing` would point
   at a directory that does not exist *and* spend budget for a model call that never happens.
   Increment only when the resume starts an agent.
3. **The state to resume into is already recorded.** `record_transition(from_state=...,
   to_state=SUSPENDED)` stores the origin in the transition row. Generalise
   `recovery.state_that_died` → `state_before(ctx, target)` rather than writing a second
   parser.

## A constraint the handoff does not name (found while reading the code)

`implement_step.start` and `plan_step.start` call `advance(ctx, STATE.IMPLEMENTING|PLANNING)`
with the **default automatic actor**. But `SUSPENDED → IMPLEMENTING` is `resume-is-james` and
`BLOCKED → IMPLEMENTING` is `unblock-is-a-judgement` — both human-gated. So a resume that
calls those `start` functions from a `SUSPENDED`/`BLOCKED` origin must thread `actor="human"`
into the start, or `advance` refuses with `requires-human`. (`RESUMABLE → IMPLEMENTING|PLANNING`
stays automatic, so `resume_run` is unaffected.)

## Design

### 1. `machine.py` — two edges + two rules (handoff note 1)

- `_WORKFLOW[State.BLOCKED]`: add `State.VERIFYING` and `State.REVIEWING`.
- `HUMAN_ONLY`: add `(State.BLOCKED, State.VERIFYING): "unblock-is-a-judgement"` and
  `(State.BLOCKED, State.REVIEWING): "unblock-is-a-judgement"`.

`assert_table_is_sound` and the §21.1 tests still pass: both targets are already reachable,
`BLOCKED` is already reachable, and `test_leaving_blocked_is_still_a_human_decision`
(test_machine.py:159) iterates `TRANSITIONS[BLOCKED] - {CANCELLED}` asserting each is
`unblock-is-a-judgement` — so it *enforces* that the two new edges carry that rule.

### 2. `recovery.py` — `state_before` + the human `resume`

- Rename `state_that_died(ctx)` → `state_before(ctx, target)`: the `from_state` of the most
  recent transition whose `to_state == target` (works for `RESUMABLE`, `BLOCKED`,
  `SUSPENDED`). Update `resume_run` to call `state_before(ctx, State.RESUMABLE)`. Update
  `__all__`. (No other caller: cli uses `recovery.resume_run`/`continuation_prompt` only.)
- Add `resume_run(ctx, *, skip_backoff=False)`: thread `skip_backoff` past the backoff check
  so a human-typed resume of a `RESUMABLE` run (after `--authorise`) does not wait 5 minutes.
- Add `resume(ctx, *, from_state=None, authorise=False) -> State` — the human resume:
  1. `FAILED` + `--authorise` → `advance(ctx, RESUMABLE, actor="human", rule="reauthorise-spend")`; refresh. `FAILED` without `--authorise` → `Blocked("resume-needs-authorise", …)`.
  2. `RESUMABLE` with **no** `--from` → delegate to `resume_run(ctx, skip_backoff=True)` (the existing ladder + ceilings). Return the disposition's target.
  3. Otherwise the run must be `SUSPENDED` or `BLOCKED` (or `RESUMABLE` with a forced `--from`); else `Blocked("not-resumable", …)`.
  4. `target = State(from_state) if from_state else state_before(ctx, ctx.state)`. Must be one of `IMPLEMENTING|PLANNING|VERIFYING|REVIEWING`; else `Blocked("resume-target-invalid", …)`.
  5. `_refuse_over_budget(ctx)` — a resume that cannot afford the next attempt says so before starting.
  6. Dispatch (the increment rule is note 2):
     - `VERIFYING` / `REVIEWING` → `advance(ctx, target, actor="human", rule=<requires_human_rule>)`. **No attempt increment, no agent.** Return.
     - `PLANNING` → `plan_step.start(ctx, actor="human")` (start increments the attempt itself).
     - `IMPLEMENTING` → `implement_step.start(ctx, resume_session=_session_to_resume(ctx, forced=bool(from_state)), continuation=continuation_prompt(ctx), actor="human")`.
  - `_session_to_resume(ctx, *, forced)`: `None` when `forced` (`--from implementing` = fresh
    attempt, per spec); otherwise `store.session_id(run, attempt, IMPLEMENTING)` iff a
    session was captured and the worktree is not mid merge/rebase/cherry-pick
    (`repo.in_progress_operation`), else `None` (fresh). Mirrors `resume_run`'s RESUME/RESTART
    branch.

### 3. `steps/implement.py` + `steps/plan.py` — thread `actor` into `start`

- Add `actor: str = AUTOMATIC` to `implement.start` and `plan.start`, passed to their
  `advance(ctx, …)` calls. Default unchanged everywhere else (tick forward-dispatch,
  `resume_run`, `factory run`).

### 4. `cli.py` — `cmd_suspend`, `cmd_resume`, subparsers

`cmd_suspend <TICKET> [--reason]` (§16.3b 1–5):
- Load run by ticket; refuse if none.
- Acquire the lease (free/expired only — a run under a live lease is being driven; refuse
  with "leased by …, wait for it to finish or use cancel").
- If `ctx.state in reap.AGENT_STATES`: `sandbox.kill_agent`, then **wait for the `exit`
  file** (reuse `reap.KILL_GRACE_SECONDS`), so the attempt ends with a real terminal record.
  `finish_attempt(outcome="suspended", exit_code=<exit file>)`.
- `sbx stop` only when no other live run shares `project.build_sandbox` (scan
  `store.runs_in_states` of the non-terminal states); keep the worktree, branch, attempt
  directory, and session id.
- `advance(ctx, State.SUSPENDED, actor="human", rule="suspend-is-james", detail=args.reason)`.
- One Linear comment through the effects ledger (reuse `block_step`'s `record_effect`
  pattern, key `comment:suspended`, idempotent by marker) — best-effort, never masks the
  transition.
- Release the lease.

`cmd_resume <TICKET> [--from <state>] [--authorise] [--dry-run]`:
- Open store, resolve project, load issue, build `Context` (reuse `_context_for`-shaped
  construction; the run row comes from `store.run_by_ticket`). Acquire lease.
- `target = recovery.resume(ctx, from_state=args.from, authorise=args.authorise)`.
- Then **drive forward synchronously** by reusing the existing tick helper
  `_drive_from_here(ctx)`: for `VERIFYING`/`REVIEWING` targets it runs verify→review→deliver
  in-process (all synchronous) and stops at `awaiting_human`/`pr_ready`/`blocked` or a loop
  back to `implementing`; for `IMPLEMENTING`/`PLANNING` targets it reaps the just-started
  agent, sees `RUNNING`, and returns (the agent finishes under a later tick — same model as
  `resume_run`). This completes FRO-6 in one command and reuses code that already exists.
- Catch `Blocked`/`Resumable` like `cmd_run` (record + announce / record). `finally`
  releases the lease. `--dry-run` works for free (the steps and `advance` are dry-run-aware).
- Subparsers: `suspend` (`ticket`, `--reason`) and `resume` (`ticket`, `--from`,
  `--authorise`, `--dry-run`). `main`'s catch-all already handles `Blocked`.

### 5. Tests

`tests/unit/test_machine.py`
- Explicit assertion: `can(BLOCKED, VERIFYING)`, `can(BLOCKED, REVIEWING)`, both
  `unblock-is-a-judgement` (the FRO-6 edge). The existing
  `test_leaving_blocked_is_still_a_human_decision` already covers the rule; this names the
  case.

`tests/unit/test_recovery.py`
- `state_before` returns the recorded origin for `RESUMABLE`, `SUSPENDED`, `BLOCKED`
  (table-driven against a tiny fake store, or via the integration fixture).
- The non-increment property (note 2) as a pure check where feasible.

`tests/integration/test_phase4.py` (against the fakes, in-process)
- **Suspend a running `implementing` run** (`detach_without_finishing=True`): state →
  `SUSPENDED`, worktree + attempt dir + session id kept, sandbox stopped iff no other run
  shares it, one Linear comment. (Needs `FakeSandbox.kill_agent` to write the `exit` file —
  add a `kill_writes_exit: bool = True` knob modelling the wrapper's graceful-exit-on-signal;
  the real `kill_agent` does not write `exit`, the wrapper does.)
- **Resume a suspended-at-`verifying` run** → re-enters `verifying` **without incrementing**
  (`ctx.run.attempt` unchanged) and drives to `reviewing`. Proves note 2: an increment would
  make `verify.py` read a non-existent attempt dir and block on the missing
  `last-message.json`.
- **Resume a blocked-at-`verifying` run** (the FRO-6 shape) → same, via the new
  `BLOCKED → VERIFYING` edge; without the edge it raises `illegal-transition`.
- **Resume a suspended-at-`implementing` run** → resumes the Codex session **by id**
  (`"01a0-…"` in the detached script, never `resume --last`), attempt increments.
- **`resume --authorise` on a `failed` run** → `failed → resumable`, then `resume_run`'s
  ladder.
- **`resume --from planning`** → rewinds to `planning`.
- **No repeated Linear comment** (F22): suspend comments once; resume does not re-comment.
- Each new test written against the real sequence and mutation-checked to fail without its
  fix (the `prove-tests-fail-without-the-fix` memory).

## Files touched

- `src/factory/machine.py` — two edges, two rules.
- `src/factory/recovery.py` — `state_before`, `resume`, `resume_run(skip_backoff=)`,
  `_session_to_resume`.
- `src/factory/steps/implement.py`, `src/factory/steps/plan.py` — `actor` param on `start`.
- `src/factory/cli.py` — `cmd_suspend`, `cmd_resume`, subparsers.
- `tests/unit/test_machine.py`, `tests/unit/test_recovery.py`,
  `tests/integration/test_phase4.py`, `tests/integration/conftest.py` (FakeSandbox knob).

## Verification

- `uv run pytest tests/unit/test_machine.py tests/unit/test_recovery.py tests/integration/test_phase4.py -x`
- `uv run factory doctor` (asserts `assert_table_is_sound`, so the new edges are checked).
- `uv run mypy src/factory` (gate on the bare command — footgun 3).
- End-to-end target (separate from this slice, needs the sandbox + a cleared `needs-info`):
  `factory resume FRO-6` to drive the parked implementation through verify → review →
  deliver without re-paying for implement. Not run as part of this plan; flagged in the
  handoff as the run that closes Tier 2.