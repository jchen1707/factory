"""§16 — reap, recover, resume, and the tick that does all three.

Everything here runs against the fakes, in-process, and every case is one of §22's
failure-injection rows. The property under test throughout is the one §4.2 states and
Phase 3 could not exercise: *the tick that asks is never the tick that started the run*.
So no test hands a later call anything the earlier call held in memory — the attempt is
found the way a fresh process finds it, through SQLite and the attempt directory.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from factory import cli, recovery
from factory.machine import Blocked, State
from factory.recovery import Disposition
from factory.sandbox.base import RunStatus
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import reap as reap_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from factory.store import Run
from tests.integration.conftest import FakeSandbox


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _expire_the_backoff(ctx: Context) -> None:
    """Back-date the transition the backoff is measured from.

    §16.4's 0/60/300 s schedule is real and is asserted by its own test below; every
    other test here is about what the recovery *decides*, and waiting out a real minute
    to find out would make this suite unrunnable on every commit.
    """
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE transitions SET at = at - 3600 WHERE run_id = ?", (ctx.run.id,))
    ctx.refresh()


def _to_worktree(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)


def _start_an_attempt(ctx: Context, *, finish: bool = True) -> Path:
    """Get a run to `implementing` with a detached attempt, the way a tick leaves one."""
    _to_worktree(ctx)
    _fake(ctx).detach_without_finishing = not finish
    started = implement_step.start(ctx)
    assert started is not None
    ctx.refresh()
    return started[0].root


# --------------------------------------------------------------------------------
# The two-phase agent step
# --------------------------------------------------------------------------------


def test_a_second_process_can_finish_an_attempt_the_first_one_started(ctx: Context) -> None:
    # The whole point of the split. `start` returns while the agent runs; nothing it
    # computed is passed to `collect`, which finds the attempt through the store and
    # the attempt directory exactly as a tick sixty seconds later would.
    _start_an_attempt(ctx)
    assert ctx.state is State.IMPLEMENTING

    verdict = reap_step.reap(ctx)

    assert verdict.outcome is reap_step.Outcome.COLLECTED
    assert ctx.state is State.VERIFYING


def test_the_vault_snapshot_survives_the_process_that_took_it(ctx: Context) -> None:
    # `collect` cannot ask `start` what the vault looked like — they are two processes.
    # If the snapshot were a local variable the §8.5 check would silently not run.
    root = _start_an_attempt(ctx)
    assert (root / implement_step.VAULT_SNAPSHOT).exists()

    (root / implement_step.VAULT_SNAPSHOT).unlink()
    with pytest.raises(Blocked) as caught:
        implement_step.collect(ctx, implement_step.AttemptDir(root), ctx.run.attempt)
    # Missing is a stop, never an empty `before`: an empty one would make every file in
    # the vault look newly added and fire the allowlist check on a run that touched none.
    assert caught.value.reason == "vault-snapshot-missing"


def test_an_attempt_still_running_is_left_alone(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)

    verdict = reap_step.reap(ctx)

    assert verdict.outcome is reap_step.Outcome.RUNNING
    assert ctx.state is State.IMPLEMENTING


# --------------------------------------------------------------------------------
# F3 / F4 — the sandbox stopped, and the host rebooted
# --------------------------------------------------------------------------------


def test_a_stopped_sandbox_under_a_live_run_is_recorded_as_resumable(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    _fake(ctx).poll_status = RunStatus.ORPHANED

    verdict = reap_step.reap(ctx)

    assert verdict.outcome is reap_step.Outcome.ORPHANED
    assert ctx.state is State.RESUMABLE
    # Recorded by the reaper rather than raised: a tick looking at someone else's
    # attempt has no call stack for a `Resumable` to unwind into.
    rows = ctx.store.transitions(ctx.run.id)
    assert rows[-1]["to_state"] == str(State.RESUMABLE)
    assert rows[-1]["rule"] == "attempt-orphaned"


def test_an_orphaned_run_keeps_its_worktree_and_its_commits(ctx: Context) -> None:
    # §16.3's restart branch: the worktree is LEFT AS IS, never reset. Work is not
    # thrown away by a recovery decision.
    _start_an_attempt(ctx, finish=False)
    worktree = Path(ctx.run.worktree or "")
    (worktree / "half-written.py").write_text("x = 1\n")
    _fake(ctx).poll_status = RunStatus.ORPHANED

    reap_step.reap(ctx)

    assert (worktree / "half-written.py").read_text() == "x = 1\n"


def test_a_run_with_no_attempt_record_at_all_is_an_orphan(ctx: Context) -> None:
    # The host rebooted between `start_attempt` and anything else, or the database was
    # restored from a copy. Either way there is nothing to poll, and a run sitting in an
    # agent state with no attempt is not a run that is working.
    _to_worktree(ctx)
    ctx.store.record_transition(
        ctx.run.id, from_state=ctx.state, to_state=State.IMPLEMENTING, actor="auto"
    )
    ctx.refresh()

    verdict = reap_step.reap(ctx)

    assert verdict.outcome is reap_step.Outcome.ORPHANED
    assert ctx.state is State.RESUMABLE


# --------------------------------------------------------------------------------
# §16.3 — resume versus restart, on a real run row
# --------------------------------------------------------------------------------


def test_a_resumable_run_resumes_the_session_it_captured(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    # The session id reaches the store the way the real one does: parsed out of the
    # first line of `events.jsonl` and written before the run counts as started.
    ctx.store.set_session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING, "01a0-fake-thread")
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)
    _fake(ctx).poll_status = None
    _fake(ctx).detached.clear()
    _expire_the_backoff(ctx)

    verdict = recovery.resume_run(ctx)

    assert verdict.disposition is Disposition.RESUME
    assert ctx.state is State.IMPLEMENTING
    script = _fake(ctx).detached[-1][1]
    assert "01a0-fake-thread" in script
    # Never `--last`: on a machine running several tickets that picks a session at random.
    assert "resume --last" not in script


def test_the_continuation_names_the_failure_rather_than_repeating_the_first_prompt(
    ctx: Context,
) -> None:
    _start_an_attempt(ctx, finish=False)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)

    continuation = recovery.continuation_prompt(ctx)

    assert "attempt-orphaned" in continuation


def test_the_third_attempt_rewinds_to_planning_instead_of_running_again(ctx: Context) -> None:
    # §16.3a rung 3, end to end: two attempts are already spent, so the recovery does
    # not start a third implement — it starts a plan.
    _start_an_attempt(ctx, finish=False)
    ctx.store.update_run(ctx.run.id, attempt=2)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)
    _fake(ctx).poll_status = None
    _expire_the_backoff(ctx)

    verdict = recovery.resume_run(ctx)

    assert verdict.disposition is Disposition.REWIND
    assert ctx.state is State.PLANNING


def test_a_run_over_its_budget_blocks_before_the_next_attempt_starts(ctx: Context) -> None:
    # F24. The ceiling is a reason not to begin; a run cut off mid-write is worse than
    # one that stopped an attempt early and said why.
    _start_an_attempt(ctx, finish=False)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)
    _fake(ctx).poll_status = None
    ctx.store.record_cost(
        ctx.run.id,
        ctx.run.attempt,
        "implement",
        model="gpt-5.6-sol",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        usd=ctx.routing.usd_per_run + 1,
    )

    with pytest.raises(Blocked) as caught:
        recovery.resume_run(ctx)

    assert caught.value.reason == "budget-exceeded"


def test_the_backoff_holds_a_run_back_before_it_decides_anything(ctx: Context) -> None:
    # §16.4. A daemon ticking every 60 s must not turn the 0/60/300 s schedule into
    # three immediate retries, so a run that has just become resumable is left alone.
    _start_an_attempt(ctx, finish=False)
    ctx.store.update_run(ctx.run.id, attempt=1)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)
    _fake(ctx).poll_status = None
    _fake(ctx).detached.clear()

    verdict = recovery.resume_run(ctx)

    assert verdict.reason == "backoff"
    assert _fake(ctx).detached == []
    assert ctx.state is State.RESUMABLE


# --------------------------------------------------------------------------------
# The tick itself
# --------------------------------------------------------------------------------


def _tick(ctx: Context, *, claim: bool = False, verbose: bool = False) -> list[str]:
    """One pass, with the fixture's fakes in place of the real adapters.

    The run row is re-read from the store for each pass, exactly as a fresh process
    would: the tick under test is never handed the `Context` a previous one built.
    """

    def build(run: Run) -> Context:
        return replace(ctx, run=run)

    return cli.tick_once(
        ctx.home,
        ctx.registry,
        ctx.routing,
        ctx.store,
        ctx.linear,
        claim=claim,
        verbose=verbose,
        context_factory=build,
    )


def test_one_tick_takes_a_fresh_run_from_approved_to_a_running_agent(ctx: Context) -> None:
    # The forward dispatch does not stop after one step: it advances until the run is
    # waiting on something that is not the factory, which here is the agent's turn.
    _fake(ctx).detach_without_finishing = True

    lines = _tick(ctx)

    assert ctx.store.run_by_id(ctx.run.id).state is State.IMPLEMENTING  # type: ignore[union-attr]
    assert any("approved" in line for line in lines)


def test_the_next_tick_collects_what_the_previous_one_started(ctx: Context) -> None:
    _start_an_attempt(ctx)

    _tick(ctx)

    # `collect` ran in a call that never saw `start`'s return value, and the run moved on.
    hops = [(row["from_state"], row["to_state"]) for row in ctx.store.transitions(ctx.run.id)]
    assert (str(State.IMPLEMENTING), str(State.VERIFYING)) in hops
    # And it kept going in the same pass rather than costing a tick per step. Where it
    # comes to rest depends on what the fixture's review frames do; that it left
    # `implementing` at all is this test's claim.
    assert ctx.store.run_by_id(ctx.run.id).state is not State.IMPLEMENTING  # type: ignore[union-attr]


def test_a_tick_releases_the_lease_it_took(ctx: Context) -> None:
    # Otherwise the next tick — a different process with a different owner token — could
    # not touch the run until the TTL expired, and a poller that locks itself out is a
    # poller that stops.
    _fake(ctx).detach_without_finishing = True
    _tick(ctx)

    assert ctx.store.run_by_id(ctx.run.id).lease_owner is None  # type: ignore[union-attr]


def test_a_run_leased_by_another_process_is_skipped_not_stolen(ctx: Context) -> None:
    # F12/F13. A fresh lease belongs to whoever holds it; a tick that stole one would
    # put two writers in one worktree.
    _fake(ctx).detach_without_finishing = True
    ctx.store.release_lease(ctx.run.id)
    assert ctx.store.acquire_lease(ctx.run.id, ttl_seconds=600, owner="someone-else")

    lines = _tick(ctx, verbose=True)

    assert ctx.store.run_by_id(ctx.run.id).state is State.APPROVED  # type: ignore[union-attr]
    assert any("someone-else" in line for line in lines)


def test_a_blocked_run_is_recorded_and_the_pass_keeps_going(ctx: Context) -> None:
    # One broken run must not end the pass. That is the difference between a poller and
    # a script that happens to loop.
    _fake(ctx).canary_exit = 0  # the protected-path canary cannot refuse: preflight fails

    _tick(ctx)

    run = ctx.store.run_by_id(ctx.run.id)
    assert run is not None
    assert run.state is State.BLOCKED
    assert run.blocked_reason


# --------------------------------------------------------------------------------
# The poller evaluates intake — it does not delegate that to the claim step
# --------------------------------------------------------------------------------


def _ready(ctx: Context, *tickets: str) -> None:
    """Make the fake Linear answer the poller's `ready_issues` query."""
    ctx.linear.ready = list(tickets)  # type: ignore[attr-defined]


def test_the_poller_refuses_a_ticket_that_fails_an_intake_condition(ctx: Context) -> None:
    # `claim_step` performs the claim and judges nothing, so a poller that trusted it
    # would start a run on any ticket carrying the label and learn the rest by spending
    # a model run. This is the ticket FRO-7 was.
    ctx.store.record_transition(
        ctx.run.id, from_state=ctx.state, to_state=State.CANCELLED, actor="human"
    )
    ctx.linear.issue_data = replace(  # type: ignore[attr-defined]
        ctx.linear.issue_data,  # type: ignore[attr-defined]
        blocked_by=(("BAC-3", "started"),),
    )
    _ready(ctx, "BAC-4")

    lines = _tick(ctx, claim=True, verbose=True)

    assert ctx.store.live_run_for_ticket("BAC-4") is None
    assert any("not eligible" in line and "11." in line for line in lines)


def test_a_ticket_whose_blocker_lands_is_picked_up_with_no_intervention(ctx: Context) -> None:
    # The whole reason condition 11 is reasonless. A `needs-info` write here would
    # freeze a ticket that was about to become ready, and a human would have to clear a
    # label the factory had no business applying.
    ctx.store.record_transition(
        ctx.run.id, from_state=ctx.state, to_state=State.CANCELLED, actor="human"
    )
    ctx.linear.issue_data = replace(  # type: ignore[attr-defined]
        ctx.linear.issue_data,  # type: ignore[attr-defined]
        blocked_by=(("BAC-3", "completed"),),
    )
    _fake(ctx).detach_without_finishing = True
    _ready(ctx, "BAC-4")

    _tick(ctx, claim=True)

    run = ctx.store.live_run_for_ticket("BAC-4")
    assert run is not None
    assert run.state is State.IMPLEMENTING
    # And no label was ever written on the way through.
    assert "needs-info" not in (ctx.linear.labels or [])  # type: ignore[attr-defined]
