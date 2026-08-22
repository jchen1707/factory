# Phase 4, item 1 — make `verify` and `review` two-phase

## Context

`factory tick` drives VERIFYING/REVIEWING/PR_READY **synchronously** inside one
process (`_perform` in `cli.py`). The review fan-out alone blocked a real run for
~5 min (four codex axes at high effort), and the verify gate run is the full
lint+types+test+build suite — also minutes. The handoff's next item, in
dependency order, is to make these two-phase so a tick never blocks on a model
run or a gate run. This is the precondition for the daemon: `launchd`
`StartInterval` is non-overlapping, so a tick that blocks stalls reaping of
every other run.

Two scope decisions (settled with James):
- **Split `verify` and `review` only.** `deliver` stays synchronous — it is
  seconds of host-side `git push` + `gh pr create`, and `exec_detached` is
  sandbox-only (`assert_factory_sandbox`, `sbx.py:312`), so splitting it would
  mean a new host-detach mechanism for no safety gain.
- **`redphase.replay` stays synchronous inside `review.start`.** It runs the
  test gate in the *build* sandbox; the codex axes must stay in the read-only
  *review* sandbox (§4.4 — enforcement by mount, not prompt), so the two cannot
  share one detached script. redphase is one focused test run, often a no-op
  (`behaviour_changed=false`). Known residual stall: a behaviour-change ticket
  blocks the tick for one test-run's duration before the fan-out detaches.
  Accepted; flagged below.

Out of scope for this slice (required before the daemon is *loaded*, not before
this lands): the `--all`/lighthouse daemon hazard (handoff item 2). The
two-phase split does not fix it; a daemon resuming such a ticket into
`verifying` would still loop back to a fresh implement on a lighthouse fail.

## Design

Mirror the existing IMPLEMENTING pattern (`implement.py`: `run` = start → await
exit → collect; `reap.py` reaps detached states; `_drive_from_here` dispatches).
A detached state's `start` spawns a process inside a sandbox writing a
heartbeat + atomic `exit`; `collect` reads the filesystem back and advances. The
tick that asks is never the tick that started the run.

### 1. Generalize the reaped set — `steps/reap.py`

- Rename `AGENT_STATES` → `DETACHED_STATES = (PLANNING, IMPLEMENTING, VERIFYING,
  REVIEWING)` and update the 4 other src references (`recovery.py:412`,
  `cli.py:451`, `cli.py:517`, `cli.py:982`). No tests reference the name.
- Add `Outcome.START_NEEDED`: the drive loop calls the state's `start` (mirrors
  the existing `NEXT_STEP` outcome — reap returns a verdict, the drive acts;
  reap stays observation-only). Used ONLY for the no-row case (see below).
- **No attempt row → START_NEEDED for VERIFYING/REVIEWING** (they are entered
  by the previous state's `collect` with no attempt row yet, so the same tick
  must call their `start`). No attempt row → ORPHANED for PLANNING/IMPLEMENTING
  (unchanged — a crash before start there).
- **Orphaned (row, no `exit`, dead holder) and TIMED_OUT (over-budget-won't-die)
  → resumable for ALL detached states, uniformly** (unchanged for
  IMPLEMENTING/PLANNING). The verify/review re-run is bounded in `resume_run`
  (item 3), not in reap. Reverses an earlier "orphan→START_NEEDED" idea: that
  left a deterministically-crashing `verify.start` spinning forever under the
  daemon (orphan→START_NEEDED→crash→orphan, no transition, no ceiling). Routing
  through `resumable` records a transition every re-run, so a ceiling can fire.
  The cost is one §16.4 backoff (~60 s) per crash recovery — acceptable; the
  first verify/review (no-row → START_NEEDED) still starts immediately.
- **Tighten the `NEXT_STEP` guard (`reap.py:88`)** to
  `state is State.PLANNING or (state is State.IMPLEMENTING and _entered_from(ctx) is State.VERIFYING)`.
  Today the `entered_from is VERIFYING` clause is only reached for IMPLEMENTING,
  but once REVIEWING joins the set, a finished REVIEWING attempt entered from
  VERIFYING would be misclassified as NEXT_STEP and lose crash-mid-collect
  recovery. VERIFYING/REVIEWING with `ended_at` set always fall through to
  poll → EXITED → re-collect (idempotent).
- Extend `_collect` (`reap.py:134`) to dispatch VERIFYING→`verify.collect`,
  REVIEWING→`review.collect`.
- **Tighten the `NEXT_STEP` guard (`reap.py:88`)** to
  `state is State.PLANNING or (state is State.IMPLEMENTING and _entered_from(ctx) is State.VERIFYING)`.
  Today the `entered_from is VERIFYING` clause is only reached for IMPLEMENTING,
  but once REVIEWING joins the set, a finished REVIEWING attempt entered from
  VERIFYING would be misclassified as NEXT_STEP and lose crash-mid-collect
  recovery. VERIFYING/REVIEWING with `ended_at` set always fall through to
  poll → EXITED → re-collect (idempotent).
- Extend `_collect` (`reap.py:134`) to dispatch VERIFYING→`verify.collect`,
  REVIEWING→`review.collect`.
- The over-budget-won't-die timeout case still goes resumable for all detached
  states (the one genuinely-stuck case).

### 2. Tick dispatch — `cli.py`

- Remove VERIFYING/REVIEWING from `_FORWARD` (`cli.py:363`); keep
  `PR_READY: "deliver"`.
- `_drive_from_here` reap branch: on `START_NEEDED` call a new
  `_start_detached(ctx, state)` → `verify.start`/`review.start`
  (`plan.start`/`implement.start` if ever needed), then break. `NEXT_STEP`,
  `RUNNING`, `ORPHANED` unchanged; `COLLECTED` continues the loop.
- `_perform` loses the `"verify"`/`"review"` cases (now detached); keeps
  `"deliver"`. The foreground `_drive` (`cli.py:276`) is untouched — it calls
  `verify_step.run`/`review_step.run` directly, never `_FORWARD`.
- `cli.py:982` status wording: "a detached step is running" not "the agent".
- `tick_once` first loop reaps `[*DETACHED_STATES, RESUMABLE]`; verify/review
  runs are reaped there. A run reaped to START_NEEDED→start is not revisited
  this tick (correct — it ends with a detached run spawned).

### 3. `recovery.resume_run` — `recovery.py:159`

Add a branch for `died_in in (VERIFYING, REVIEWING)` that re-runs the step and
is bounded by a real ceiling. Today `resume_run` only does REWIND→`plan.start`
and RESUME/RESTART→`implement.start`; a verify/review resumable (now reachable:
detached verify/review can orphan/timeout; today's sync steps only raise
`Blocked` so the edge was theoretical) would fall through to `implement.start`
— silently re-running a whole implement with an incremented attempt.

The `attempts` table PK is `(run_id, attempt, state)` and `start_attempt` is
`INSERT OR REPLACE` (`store.py:577`), so re-running verify/review at the *same*
attempt number keeps `attempts_in_state` at 1 — the ladder's per-state ceiling
is decorative for verify/review. The branch needs a counter that actually
increments:

- Add `store.resumable_reentries(run_id, from_state)`: count `transitions` rows
  where `to_state=RESUMABLE and from_state=<state>`. Every orphan/timeout re-run
  records one such transition (reap's `_orphan`), so this counts both.
- The branch: after the existing backoff check, if `died_in in (VERIFYING,
  REVIEWING)`, check `resumable_reentries >= registry.defaults.max_attempts` →
  record `resumable → failed` (`rule=f"max-reruns-{died_in}"`), return. Else call
  `verify_step.start(ctx)` / `review_step.start(ctx)` directly (which advances
  `RESUMABLE → {VERIFYING,REVIEWING}` — a valid automatic edge — and spawns the
  re-run), with a distinct reason slug `f"rerun-{died_in}"`. RESUME/RESTART/
  REWIND dispositions collapse to "re-run fresh" (verify has no codex session;
  review.start re-runs Tier-1/Tier-2 fresh), so `decide()`'s disposition is
  ignored — only its ceiling via the reentries count matters.

### 4. Split `verify` into start/collect — `steps/verify.py`

- `start(ctx)`: read `gates_run` from `last-message.json` (host-side), decide
  `--all` (reuse `_claims_opt_in`/`_gate_named_in`), build the detached shell
  script running `node gate_report.mjs --json [--all] [--base ref]` with the
  heartbeat loop + atomic `exit` (mirror `codex.wrapper_script`,
  `agent/codex.py:75`; share a small `detached_wrapper(heartbeat, exit, body)`
  helper to avoid duplicating the heartbeat/exit dance). Record a verify
  attempt row via `store.start_attempt(..., state=VERIFYING, attempt=ctx.run.attempt)`
  (new — verify records none today; keyed by state so no collision with
  implement's row). Guard `if ctx.state is not VERIFYING: advance(...)` (mirror
  `implement.py:178`) — the tick enters start already in VERIFYING. Dry-run:
  would-print, advance to REVIEWING, return None (foreground composite only).
- `collect(ctx, attempt_dir, attempt)`: read `gates.stdout.txt`, validate
  schema, cross-check `gates_run`, advance (REVIEWING | IMPLEMENTING | blocked).
  Idempotent re-derive; guard `record_check` against double-rows on re-collect
  (mirror implement's `_already_recorded` via the attempt row's `ended_at`).
  **Must NOT increment attempt or call `start_attempt`** — only
  `advance(ctx, State.IMPLEMENTING)` on fail (handoff note 2; the increment
  lives in `implement.start`).
- `run(ctx)` (foreground): `start()` → `_await_exit` (reuse the poll loop) →
  `collect`. Blocking; used by `factory run`.

### 5. Split `review` into start/collect — `steps/review.py`

- `start(ctx)`: synchronously run `clone.fetch_back` + `redphase.replay` +
  `weakening_guard` (redphase may raise `Blocked`/return `awaiting_human` —
  handle as today, no detach in those cases). Then compute the Tier-2 trigger
  in Python (`_tier2_trigger`), assemble each axis prompt (`_axis_prompt`),
  ensure the review sandbox, and spawn ONE detached script in the review
  sandbox running the codex Tier-1 axes (+Tier-2 `full` if trigger is None)
  sequentially, each writing findings to the per-project scratch via `-o`,
  with heartbeat + atomic `exit`. Record a review attempt row. Same
  `if ctx.state is not REVIEWING: advance` guard. Dry-run: would-print,
  advance to PR_READY, return None.
- `collect(ctx, attempt_dir, attempt)`: read `exit`; for each axis, land
  findings from scratch → run review dir (`_land`), validate (`_validated_findings`),
  scan for secrets; merge findings; write `review-summary.json`; transition
  (PR_READY | awaiting_human | blocked). Idempotent.
- `run(ctx)` (foreground): `start()` → `_await_exit` → `collect`.

### 6. `deliver` — unchanged

`steps/deliver.py` stays synchronous. `_FORWARD[PR_READY] = "deliver"`,
`_perform("deliver")` calls `deliver_step.run`. Documented rationale in the
module: host-side, seconds, run is `awaiting_human` immediately after.

### 7. `suspend` — `recovery.py:412`

Already correct once VERIFYING/REVIEWING are in `DETACHED_STATES`: suspending
from those states kills the detached gate/review run and waits for `exit`,
giving the attempt a real terminal record (today's sync verify has no agent to
kill). No code change beyond the rename.

## Tests — `tests/integration/test_phase4.py` + `conftest.py`

- `FakeSandbox.exec_detached` (`conftest.py:364`) writes implement-shaped
  files today. Teach it: script contains `gate_report.mjs` → write
  `gates.stdout.txt` (canned report) + `exit` (0/1/3 by verdict); script is the
  review fan-out (codex axes, detect by the `-o`/prompt paths) → write the
  canned `review_findings` to each axis's scratch `-o` path + `exit`.
- Update existing tests whose tick-flow now stops at a detached verify/review
  instead of advancing synchronously (e.g. the "one tick approved→running
  agent" test; the FRO-6 `test_resume_into_verifying_does_not_increment`).
- New, mutation-checked (each fails without its fix):
  - A second tick collects what the previous one started — for **verify**
    (the core two-phase property, mirroring the existing implement test).
  - reap START_NEEDED: a run at `verifying` with no attempt → `verify.start`.
  - orphaned verify/review (row, no `exit`, dead holder) → `resumable`, and
    `resume_run` re-runs it via `verify.start`; after `max_attempts` reentries →
    `failed` (the ceiling that `attempts_in_state` could not provide).
  - verify fail loop-back still advances to `implementing` and `implement.start`
    increments (no double-advance, no `illegal-transition`).
  - Idempotent re-collect: re-running `verify.collect`/`review.collect` after a
    mid-collect crash does not double-record checks or advance twice.
  - `deliver` regression: a run at `pr_ready` is delivered in one synchronous
    tick (unchanged).

## Verification

- `uv run pytest tests/integration/test_phase4.py` green, including new tests.
- `uv run pytest tests/integration/` whole suite green (clone, gc, phase3,
  pipeline).
- `uv run mypy src/factory` (gate on the bare command, not a piped tail —
  footgun 3).
- `uv run pytest tests/unit -k "machine or reap or recovery"` for the table and
  ladder asserts.
- End-to-end against a real ticket is *not* required for this slice (the
  `--all`/lighthouse hazard, item 2, blocks an unattended `--from verifying`
  resume and must be fixed first). The two-phase property is proven by the
  integration tests with fakes, the way the implement split was.

## Order of work

1. reap generalization (rename + START_NEEDED + tighten guard + `_collect`).
2. cli dispatch (`_drive_from_here` START_NEEDED, `_FORWARD`, `_perform`,
   status wording) + `recovery.resume_run` branch.
3. `verify` start/collect + shared `detached_wrapper`.
4. `review` start/collect.
5. fakes + tests; mutation-check each.
6. mypy + full suite.