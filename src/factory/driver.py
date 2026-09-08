"""Layer D's own sentence: *what runs, when, in what order, and what happens when it dies.*

`AGENTS.md` gives that sentence to the control plane, and until this module existed it
had no home. It was spread over six private functions in `cli.py`, written twice, and
the two copies disagreed:

- **foreground** — `cli._drive` called the steps in a fixed chain, and `plan.run`,
  `implement.run`, `verify.run` and `review.run` were each a shell around
  `start` → wait-for-exit → `collect`, three of them with their own copy of the wait;
- **detached** — `cli._FORWARD`, a hand-written `dict[State, str]`, dispatched through
  `cli._perform`, with two special cases hanging off `reap`'s verdicts.

Nothing checked that the two agreed, because `machine.py` held the transitions and no
entry actions. It does now (`machine.ENTRY`), and everything below is a mapping from an
action *name* to the function that performs it, plus one loop.

There is one execution model. `factory run` is that loop in the foreground with
`follow=True`; a tick is one `step` per run per pass. The difference is who waits, and
it is the only difference — which is what makes the foreground path inherit the orphan
detection and the kill-grace that only the daemon used to have. See
`docs/adr/0001-one-execution-model.md`.

`driver` holds **no domain judgement**. It does not know that planning exists
(`steps.start_agent` decides), and it does not know what follows a reaped attempt
(`reap.act` decides). What it owns is: which action a state calls for, what the four
answers mean, when the lease is renewed, and where `Blocked`/`Resumable` come to rest.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from factory import machine, recovery
from factory.execution import AgentApprovalRequired, ProjectQueued
from factory.machine import Action, Blocked, Resumable, State
from factory.steps import Context, record_stop, start_agent
from factory.steps import block as block_step
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import deliver as deliver_step
from factory.steps import reap as reap_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step

__all__ = ["POLL_INTERVAL_SECONDS", "Outcome", "Result", "drive", "step"]

#: How long a foreground `drive(follow=True)` waits between looks. The same number the
#: four `run()` wrappers each declared for themselves, and §4.2's reason is unchanged: a
#: detached run reports through the filesystem, so watching it is a poll, not a wait.
POLL_INTERVAL_SECONDS = 10

#: Renewed by `step` on every `WAITING`. Longer than any poll interval by a wide margin,
#: because the holder renews on every look and a lease that expired under a healthy
#: holder is F13's failure, not a recovery.
LEASE_TTL_SECONDS = 900


class Outcome(StrEnum):
    """What one `step` did, in the four words a caller has to tell apart.

    `PROGRESSED` and `WAITING` are the distinction the old loop did not have. Its
    condition was `if ctx.state is before: break`, which reads "an agent is running" and
    "this run is finished" as the same event — and that is why `step` returns this rather
    than the new `State`: the state alone cannot tell them apart either.
    """

    #: The state changed. Call `step` again.
    PROGRESSED = "progressed"
    #: A detached run is live and this process is watching it. The lease has been renewed.
    WAITING = "waiting"
    #: The run came to rest — blocked, resumable, failed, or terminal.
    STOPPED = "stopped"
    #: Every way out of this state is reserved for James (`machine.is_human_held`).
    NEEDS_HUMAN = "needs-human"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    #: What happened, for the tick's one-line report. `drive` joins the trail.
    detail: str = ""
    #: The stop's reason slug when a step raised, so a caller can name it without
    #: re-parsing `detail`. Empty otherwise.
    reason: str = ""


def step(ctx: Context) -> Result:
    """Perform one state's entry action. The single place a state is turned into work.

    Renews the lease whenever it returns `WAITING`, and that placement is deliberate.
    Under a foreground loop an `implementing` run returns `WAITING` immediately and the
    caller may then sleep for hours against a 900 s lease. Renewing in `drive`'s loop
    instead is less precise — it would renew on outcomes that are about to release the
    lease anyway — and *dropping* the lease while waiting opens a window for a second
    process to claim a run whose agent is live. The daemon gets away with dropping it
    only because a tick is short. The invariant is: **a `WAITING` run is leased by
    whoever is watching it.**

    Raises `Blocked` and `Resumable` through. `drive` is the only handler.
    """
    before = ctx.state
    if before in machine.TERMINAL:
        return Result(Outcome.STOPPED, str(before))

    action = machine.ENTRY.get(before)
    if action is None:
        # Not an oversight and not a fallback: `machine.assert_table_is_sound` proves
        # that a state with no entry action is one `is_human_held` derives as James's.
        return Result(Outcome.NEEDS_HUMAN, f"{before} is held for a human")

    from factory.child_integration import IntegrationHeld

    try:
        from factory.workflow_children import advance as advance_children

        advance_children(ctx)
        from factory.child_integration import advance as integrate_children

        integrate_children(ctx)
        result = _perform(ctx, action, before)
    except IntegrationHeld as exc:
        return Result(Outcome.STOPPED, exc.detail, exc.reason)
    except AgentApprovalRequired as exc:
        return Result(Outcome.NEEDS_HUMAN, f"agent attempt waiting: {exc}", "approval-required")
    except ProjectQueued as exc:
        result = Result(Outcome.WAITING, str(exc), "project-busy")
    if result.outcome is Outcome.WAITING:
        ctx.store.renew_lease(ctx.run.id, ttl_seconds=LEASE_TTL_SECONDS)
    return result


def drive(ctx: Context, *, follow: bool = False, poll: float = POLL_INTERVAL_SECONDS) -> Result:
    """Take one run as far as it will go, and record where it stopped.

    The **only** `try` in the pipeline. `Blocked` and `Resumable` stay the step
    vocabulary — `reap.reap`'s contract depends on `collect` raising through it — and
    this is where they come to rest, through `block.record` and `steps.record_stop`.
    Before this, `cmd_run`, `_work_on`, `cmd_accept`, `cmd_resume` and `dispatch_control`
    each wrote their own handler; they now differ only in how they format one `Result`.

    `follow=False` returns as soon as a detached run is live — a tick's shape, one pass
    per run. `follow=True` keeps looking, which is `factory run`: the same states, the
    same reap, the same orphan detection, and a poll between looks.

    Adapter failures (`GitError`, `SbxError`, `LinearError`) are deliberately *not*
    caught here. A tick reports them and leaves the run exactly where it was, so a
    transient Linear outage is a pause rather than a state change (F17); `factory run`
    turns them into a `Blocked` naming the state they died in. Those are different
    answers to the same exception, and they belong to the callers that hold them.
    """
    trail: list[str] = []

    def note(detail: str) -> None:
        # A `follow` loop looks at the same waiting run every poll; the trail records
        # what happened, not how many times it was looked at.
        if detail and (not trail or trail[-1] != detail):
            trail.append(detail)

    try:
        while True:
            result = step(ctx)
            note(result.detail)
            if result.outcome is Outcome.PROGRESSED:
                continue
            if result.outcome is Outcome.WAITING and follow:
                if poll:
                    time.sleep(poll)
                continue
            return Result(result.outcome, "  ".join(trail), result.reason)
    except Blocked as exc:
        block_step.record(ctx, exc.reason, exc.detail)
        note(f"blocked: {exc.reason}")
        return Result(Outcome.STOPPED, "  ".join(trail), exc.reason)
    except Resumable as exc:
        record_stop(ctx, State.RESUMABLE, rule=exc.reason, detail=exc.detail)
        note(f"resumable: {exc.reason}")
        return Result(Outcome.STOPPED, "  ".join(trail), exc.reason)


# --------------------------------------------------------------------------------
# action name -> the function that performs it
# --------------------------------------------------------------------------------


#: The synchronous actions: they do their work on the host, in this process, and return
#: with the state moved. `claim`, `context`, `sandbox`, `worktree` and `deliver` keep
#: their `run()` for exactly this reason — they are steps, not start/wait/collect
#: wrappers, and there was never a second copy of them to delete.
_SYNCHRONOUS: dict[Action, Callable[[Context], None]] = {
    Action.CLAIM: claim_step.run,
    Action.CONTEXT: context_step.run,
    Action.SANDBOX: sandbox_step.run,
    Action.WORKTREE: worktree_step.run,
    Action.START_AGENT: start_agent,
    Action.DELIVER: deliver_step.run,
}

#: What `reap.act`'s verdict means to the loop. Indexed rather than `.get`: a verdict
#: with no meaning here is a factory bug and should say so on the spot, which is the same
#: discipline `KILL_TARGET` records. `NEXT_STEP` and `START_NEEDED` are absent because
#: `act` resolves both into `STARTED`/`EXHAUSTED` before returning.
_REAPED: dict[reap_step.Outcome, Outcome] = {
    reap_step.Outcome.RUNNING: Outcome.WAITING,
    reap_step.Outcome.STARTED: Outcome.WAITING,
    reap_step.Outcome.COLLECTED: Outcome.PROGRESSED,
    reap_step.Outcome.ORPHANED: Outcome.STOPPED,
    reap_step.Outcome.TIMED_OUT: Outcome.STOPPED,
    reap_step.Outcome.EXHAUSTED: Outcome.STOPPED,
    # Only reachable if the row moved out from under the read; look again rather than
    # loop on it.
    reap_step.Outcome.NOT_APPLICABLE: Outcome.WAITING,
}


def _perform(ctx: Context, action: Action, before: State) -> Result:
    if action is Action.REAP:
        verdict = reap_step.act(ctx)
        return Result(_REAPED[verdict.outcome], f"{before}:{verdict.outcome}")
    if action is Action.RECOVER:
        return _recover(ctx)

    _SYNCHRONOUS[action](ctx)
    if ctx.state is before:
        # A synchronous step that returned without moving. `STOPPED` rather than
        # `WAITING`, because nothing detached is running: there is no event that would
        # make the next look different, and a `follow` loop would spin on it forever.
        # This is the old loop's `if ctx.state is before: break`, kept rather than
        # deleted — it is the only thing standing between a step that quietly does
        # nothing and a process that never returns.
        return Result(Outcome.STOPPED, f"{before}: {action} made no progress")
    return Result(Outcome.PROGRESSED, f"{before}->{ctx.state}")


def _recover(ctx: Context) -> Result:
    """`resumable`'s entry action — §16.4's ladder, which decides for itself.

    Three shapes come back and the state says which: still `resumable` is the backoff
    window (nothing was spent, look again later), `failed` is the ladder exhausted, and
    anything else is a fresh attempt now running detached.
    """
    verdict = recovery.resume_run(ctx)
    detail = f"resumable:{verdict.disposition}({verdict.reason})"
    if ctx.state is State.FAILED:
        return Result(Outcome.STOPPED, detail, verdict.reason)
    return Result(Outcome.WAITING, detail)
