"""§21.3 — the one execution model, exercised as one.

`factory run` and `factory tick` used to be two implementations of the same pipeline that
nothing compared. They are now `driver.drive(follow=True)` and `driver.step`, and every
test here is about a property that could not be stated while there were two.

See `docs/adr/0001-one-execution-model.md`.
"""

from __future__ import annotations

import time

import pytest

from factory import driver, machine
from factory.machine import Blocked, State
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import FakeSandbox, advance_state


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _to_worktree(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)


def _loop_back_to_implementing(ctx: Context, *, attempt: int) -> None:
    """The gate-fail shape: a finished implement attempt, a finished verify attempt that
    failed, and the `verifying -> implementing` loop-back that follows."""
    ctx.store.finish_attempt(
        ctx.run.id, attempt, State.IMPLEMENTING, exit_code=0, outcome="implemented"
    )
    machine.assert_table_is_sound()
    from factory.steps import advance

    advance(ctx, State.VERIFYING)
    ctx.store.finish_attempt(ctx.run.id, attempt, State.VERIFYING, exit_code=1, outcome="fail")
    advance(ctx, State.IMPLEMENTING)


# --------------------------------------------------------------------------------
# The trap: one action per step does not terminate
# --------------------------------------------------------------------------------


def test_a_reaped_gate_fail_starts_the_next_attempt_inside_one_step(ctx: Context) -> None:
    """`reap`'s follow-on runs inside the `REAP` action, not on the next loop iteration.

    This is the shape that nearly went in the other way. `NEXT_STEP` is derived from a
    *finished* attempt row for `run.attempt`, and the only thing that increments
    `runs.attempt` is the follow-on itself — so a driver that reaped, returned
    `PROGRESSED`, and left the start to the next iteration would read the same finished
    row forever. `factory tick` survived the split only because a tick boundary is not a
    loop iteration.

    The assertion that matters is the attempt counter moving *within one `step`*.
    """
    _to_worktree(ctx)
    implement_step.start(ctx)
    _loop_back_to_implementing(ctx, attempt=1)
    assert ctx.run.attempt == 1

    result = driver.step(ctx)

    ctx.refresh()
    assert ctx.run.attempt == 2, "the follow-on did not run inside the reap action"
    assert result.outcome is driver.Outcome.WAITING


def test_driving_a_gate_fail_loop_back_terminates(ctx: Context) -> None:
    """The same trap, stated as the thing a user would actually notice.

    `drive` is a loop. If the reap and its follow-on were two iterations, this call would
    not return — the run would sit at `implementing` with one finished attempt and the
    loop would re-derive `NEXT_STEP` from it on every pass. `advance_state` bounds itself
    at 24 looks and raises rather than hanging, so a regression here fails the suite
    instead of wedging it.
    """
    _to_worktree(ctx)
    implement_step.start(ctx)
    _loop_back_to_implementing(ctx, attempt=1)

    advance_state(ctx, until=State.VERIFYING)

    ctx.refresh()
    assert ctx.run.attempt == 2


# --------------------------------------------------------------------------------
# A `WAITING` run is leased by whoever is watching it
# --------------------------------------------------------------------------------


def test_step_renews_the_lease_whenever_it_returns_waiting(ctx: Context) -> None:
    """The invariant a foreground loop needs and the old chain never had.

    `factory run` now sits in a poll loop while an agent runs, which can be hours against
    a 900 s lease. The four deleted `run()` wrappers each renewed inside their own poll
    loop; with the wrappers gone, `step` is the only place left that knows a detached run
    is live — so it renews there, on the outcome that means it.

    Renewing in `drive`'s loop instead would be less precise, and dropping the lease
    while waiting would let a second process claim a run whose agent is live.
    """
    _to_worktree(ctx)
    _fake(ctx).detach_without_finishing = True
    implement_step.start(ctx)
    ctx.refresh()
    assert ctx.state is State.IMPLEMENTING

    # Back-date the lease to just inside its window, the way an hour of polling would.
    with ctx.store.transaction() as conn:
        conn.execute(
            "UPDATE runs SET lease_expires_at = ? WHERE id = ?",
            (int(time.time()) + 30, ctx.run.id),
        )
    ctx.refresh()
    before = ctx.run.lease_expires_at or 0

    result = driver.step(ctx)

    ctx.refresh()
    assert result.outcome is driver.Outcome.WAITING
    assert (ctx.run.lease_expires_at or 0) > before
    assert ctx.store.holds_lease(ctx.run.id)


# --------------------------------------------------------------------------------
# `--plan` outlives the process that typed it
# --------------------------------------------------------------------------------


def test_force_plan_is_read_off_the_run_row_not_the_command_line(ctx: Context) -> None:
    """Schema 4's whole reason.

    `--plan` was threaded through `cli._drive(ctx, force_plan=...)`, so it existed only in
    the process that typed it. A `factory run --plan` that died before `worktree_ready` —
    the sandbox pull is the slow part, and it is before it — was picked up by the daemon
    and implemented with no plan phase at all, silently. The flag is on the row now, and
    `steps.start_agent` is the only thing that reads it.
    """
    ctx.store.update_run(ctx.run.id, force_plan=True)
    ctx.refresh()
    _to_worktree(ctx)
    assert ctx.state is State.WORKTREE_READY

    driver.step(ctx)

    ctx.refresh()
    assert ctx.state is State.PLANNING


def test_without_force_plan_the_same_state_goes_straight_to_implementing(
    ctx: Context,
) -> None:
    """The control for the test above: the registry's `planning.auto` is off by default,
    so nothing but the row's flag makes the difference."""
    _to_worktree(ctx)

    driver.step(ctx)

    ctx.refresh()
    assert ctx.state is State.IMPLEMENTING


# --------------------------------------------------------------------------------
# `drive` is the only handler
# --------------------------------------------------------------------------------


def test_drive_records_a_block_with_its_reason_and_the_tracker_write(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Blocked` comes to rest in one place, and all four writes happen there.

    `cmd_run`, `_work_on`, `cmd_accept`, `cmd_resume` and `dispatch_control` each had
    their own handler; a block recorded without `blocked_reason` is a run the console
    shows as stopped with nothing to show, and one recorded without the Linear comment is
    §13.1's failure. Both now come from `block.record`, which only `drive` calls.
    """
    _to_worktree(ctx)
    monkeypatch.setattr(
        verify_step,
        "start",
        lambda _ctx, **_kw: (_ for _ in ()).throw(Blocked("schema-invalid", "no gates.json")),
    )

    result = driver.drive(ctx, follow=True, poll=0)

    ctx.refresh()
    assert result.outcome is driver.Outcome.STOPPED
    assert result.reason == "schema-invalid"
    assert ctx.state is State.BLOCKED
    assert ctx.run.blocked_reason == "schema-invalid"
    assert any("schema-invalid" in c for c in ctx.linear.comments)  # type: ignore[attr-defined]


def test_drive_stops_at_a_state_held_for_a_human(ctx: Context) -> None:
    """`awaiting_human` has no entry action because every exit from it is James's. The
    loop must say so rather than fall through to a default — `cli._FORWARD.get(state)`
    returning `None` said the same thing by accident, and could not tell "held" apart
    from "not listed yet"."""
    from factory.steps import advance

    _to_worktree(ctx)
    advance_state(ctx, until=State.REVIEWING)
    advance(ctx, State.AWAITING_HUMAN, rule="review-finding")

    result = driver.drive(ctx, follow=True, poll=0)

    assert ctx.state is State.AWAITING_HUMAN
    assert result.outcome is driver.Outcome.NEEDS_HUMAN
