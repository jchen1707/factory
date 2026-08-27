# ADR-0001 — one execution model

- **Status** — accepted, 2026-08-27
- **Supersedes** — the foreground/detached split described in four step docstrings
- **Ships with** — the `driver` module (PR 2 of the architecture review)

## The decision

There is **one** way a run advances: `driver.step`, which performs the entry action
`machine.ENTRY` gives the run's current state and answers with one of `PROGRESSED`,
`WAITING`, `STOPPED`, `NEEDS_HUMAN`. `driver.drive` is that call in a loop.

`factory run` is `drive(follow=True)`. A `factory tick` pass is `drive()` per run, which
returns as soon as a detached run is live. **The only difference is who waits.**

## What it replaces

Two implementations of one pipeline:

- **Foreground** — `cli._drive` called the steps in a fixed chain, and `plan.run`,
  `implement.run`, `verify.run` and `review.run` were each a ~6-line shell around
  `start` → wait-for-exit → `collect`. Three of them carried their own copy of
  `_await_exit`; `plan` inlined a fourth poll loop. Four declarations of
  `POLL_INTERVAL_SECONDS = 10`, three of a 900 s lease TTL.
- **Detached** — `cli._FORWARD`, a hand-written `dict[State, str]`, dispatched through
  `cli._perform` and driven by `cli._drive_from_here`, with `_start_detached` and
  `_start_next_after_gate_fail` hanging off `reap`'s verdicts.

`machine.py` held the transition table and no entry actions, so **nothing checked that
the two agreed**. Its own comment already names the failure mode — *"Prefer deriving to
enumerating"* — written after `_WORKFLOW` missed `resumable` and then `blocked`.
`_FORWARD` was the same hand-written list one file away, and it had a second, invisible
contract: "which states are detached" was a tuple in `reap.py` that had to be the exact
complement of `_FORWARD`'s *absence* of those states.

## Why it is recorded rather than left to the diff

Four docstrings argued **for** the split, in prose, at the point where someone would next
be tempted to re-add a `run()` wrapper:

> `factory tick` uses `start` and `collect` separately, because a tick that blocked for
> the length of a model run could not reap anything else. The two paths share every line
> that matters.

The premise was right and the conclusion was wrong. The paths did not share every line
that matters, and the divergence was not theoretical:

- **Phase 5 defect 3.** `kill_agent` is `pkill -x`, so signalling the wrong process name
  selects nothing. `verify._await_exit` was fixed to pass `"node"` at the one call site it
  owned. `reap` — the path every *unattended* run times out through — kept signalling
  `codex` at a gate report, so the kill grace always expired into an orphan with no exit
  code: a timeout the factory reported as having signalled, and had not.
- **Orphan detection.** The foreground chain had none. A `factory run` killed at the
  terminal left an agent running in the VM and a run row that looked live. The runbook's
  answer was "use `nohup`". A foreground run now inherits `reap`'s orphan detection and
  `reap.KILL_GRACE_SECONDS` because it *is* the reap path.
- **`--plan`.** The flag was threaded through `cli._drive(ctx, force_plan=...)`, so it
  existed only in the process that typed it. A `factory run --plan` that died before
  `worktree_ready` was resumed by the daemon and implemented with no plan phase, silently.
  It is now `runs.force_plan` (schema 4), read by `steps.start_agent`.

Delete those docstrings without recording why and the split comes back the next time
someone wants a synchronous `verify.run` for a test. It should not: the test helper is
`tests/integration/conftest.advance_state`, and it lives in the tests because production
code that exists only for tests is the smell this whole review is about.

## The soundness property, and why it is derived

`machine.assert_table_is_sound` now asserts:

> every non-terminal state either has an entry action or is human-held

where **human-held is computed**: a state is human-held iff every exit that is not a stop
(`blocked`, `cancelled`) carries a `machine.requires_human_rule`. Today that derives
`{blocked, awaiting_human, suspended, failed}` and nothing else.

Writing those four down would have been a third hand-written list beside a table — which
is the defect this PR exists to remove, not a shortcut past it. `_WORKFLOW` spelled out
the `cancelled` wildcard by hand and missed `resumable`; the `blocked` edge set was
spelled out by hand and missed `sandbox_creating`. Both were found by a run failing, not
by a test.

`ENTRY` holds an `Action` **name**, not a callable, for one reason: `machine` is pure and
cannot import `steps`, and a `dict[State, Callable]` living in `driver` would be the
hand-written list again, one file further from the transitions it has to agree with. The
name is what lets the check live next to the table.

## Two things `driver` deliberately does not know

- **That planning exists.** `ENTRY[WORKTREE_READY]` is one action, `START_AGENT`, and
  `steps.start_agent` makes the plan-versus-implement call.
- **What follows a reaped attempt.** `reap.act` owns its own follow-ons. This is not
  tidiness: the tempting design is one action per `step`, returning after the reap and
  letting the next iteration start the attempt, and **it does not terminate**. `NEXT_STEP`
  is derived from a *finished* attempt row for `run.attempt`, and the only thing that
  increments `runs.attempt` is the follow-on. `factory tick` survived the split only
  because a tick boundary is not a loop iteration.

## Consequences

- `driver.drive` is the only `try` in the pipeline. `Blocked` and `Resumable` stay the
  step vocabulary — `reap.reap`'s contract depends on `collect` raising through it — and
  come to rest in one place, through `block.record` and `steps.record_stop`. `cmd_run`,
  `_work_on`, `cmd_accept`, `cmd_resume` and `dispatch_control` now differ only in how
  they format one `Result`.
- Adapter failures (`GitError`, `SbxError`, `LinearError`) are *not* caught in `driver`,
  because the two callers want opposite answers: a tick reports and leaves the run where
  it was, so a transient Linear outage is a pause rather than a state change (F17), while
  `cli._drive_foreground` turns it into a `Blocked` naming the state that died.
- **A `WAITING` run is leased by whoever is watching it.** `step` renews the lease on
  every `WAITING`, because a foreground loop can now sleep for hours against a 900 s
  lease. Dropping the lease while waiting was rejected: it opens a window for a second
  process to claim a run whose agent is live, and the daemon gets away with that only
  because a tick is short.
