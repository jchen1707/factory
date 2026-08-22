"""§16.1 — what happened to an attempt nobody was watching.

Every other step in `steps/` performs a state's entry action. This one performs none:
it looks at an attempt that a *previous* process started and answers one question —
is it running, did it finish, or did it die? The tick that asks is never the tick that
started the run, so the answer is read entirely off the filesystem and the database.
Nothing here consults the caller's memory, because on the runs this exists for there
is no caller left to consult.

The three files that carry the answer are the ones §4.2 makes the protocol: `exit`,
`heartbeat` and `sbx-exec.pid`, all inside the attempt directory, all addressed by the
same absolute path on the host and inside the VM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from factory.artifacts import AttemptDir
from factory.machine import AUTOMATIC, State
from factory.sandbox.base import RunHandle, RunStatus
from factory.steps import Context
from factory.steps import implement as implement_step
from factory.steps import plan as plan_step

__all__ = ["AGENT_STATES", "Outcome", "Verdict", "reap"]

#: The two states whose entry action spawns a detached agent, and therefore the only
#: two that can be orphaned. A run sitting anywhere else is between steps: the next
#: tick re-enters it, which is what "idempotent by construction" buys.
AGENT_STATES: tuple[State, ...] = (State.PLANNING, State.IMPLEMENTING)

#: How long after the wrapper is signalled the tick waits for its `exit` file. The
#: wrapper writes `exit` last and atomically, so this is bounded by one `mv`; a run
#: that has not produced one after this long is one whose wrapper is gone too.
KILL_GRACE_SECONDS = 60


class Outcome(StrEnum):
    #: Not in an agent state, or no attempt row — nothing for this module to do.
    NOT_APPLICABLE = "not-applicable"
    #: Heartbeat fresh, holder alive, timeout not reached. Leave it alone.
    RUNNING = "running"
    #: `exit` is present. The step's own `collect` ran, and the state advanced.
    COLLECTED = "collected"
    #: No `exit`, and the holder or the sandbox is gone. Recorded as `resumable`.
    ORPHANED = "orphaned"
    #: Heartbeat fresh but the state's timeout is spent — §16.1's "hung run" row.
    TIMED_OUT = "timed-out"
    #: The attempt finished cleanly and the run is parked waiting for the *next* phase to
    #: start — a completed plan, or gates that failed and sent the work back. Nothing to
    #: reap; the tick's forward dispatch takes it from here.
    NEXT_STEP = "next-step"


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    detail: str = ""


def reap(ctx: Context) -> Verdict:
    """Look at one run's live attempt and act on what the filesystem says.

    Raises whatever the step's `collect` raises — `Blocked` and `Resumable` are the
    step vocabulary and the tick handles them the same way `factory run` does. The
    orphan branch does **not** raise: it records the `resumable` transition itself,
    because there is no live call stack for the exception to unwind.
    """
    state = ctx.run.state
    if state not in AGENT_STATES:
        return Verdict(Outcome.NOT_APPLICABLE, f"{state} spawns no agent")

    row = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, state)
    if row is None or not row["artifact_dir"]:
        return _orphan(ctx, "no-attempt-record", f"no attempt row for {state} #{ctx.run.attempt}")

    # A finished attempt in an agent state means one of two very different things, and
    # the transition log is what tells them apart. Entering `implementing` from
    # `verifying` is the gate report sending the work back, and entering `planning` at
    # all ends with a plan the implement phase has yet to act on — both are the run
    # waiting for its next phase. Anything else is a control plane that died between
    # `finish_attempt` and the transition, and that one is completed below
    # by re-collecting, which is why `collect` re-derives rather than re-records.
    if row["ended_at"] is not None and (
        state is State.PLANNING or _entered_from(ctx) is State.VERIFYING
    ):
        return Verdict(Outcome.NEXT_STEP, f"{state} attempt {ctx.run.attempt} is finished")

    attempt_dir = AttemptDir(Path(str(row["artifact_dir"])))
    handle = RunHandle(
        run_id=ctx.run.id,
        attempt=ctx.run.attempt,
        sandbox=str(row["sandbox"] or ctx.project.build_sandbox),
        workdir=ctx.run.worktree or str(ctx.project.path),
        attempt_dir=attempt_dir.root,
        session_id=row["session_id"],
    )

    status = ctx.sandbox.poll(handle)
    if status is RunStatus.EXITED:
        _collect(ctx, state, attempt_dir)
        return Verdict(Outcome.COLLECTED, f"exit {attempt_dir.exit_code()}")

    if status is RunStatus.ORPHANED:
        return _orphan(ctx, "attempt-orphaned", f"{state} attempt {ctx.run.attempt} is not running")

    overrun = _overrun_seconds(ctx, state)
    if overrun is not None:
        # §16.1's last live row: the heartbeat is fresh, so the wrapper is alive, but
        # the state's own budget is spent. Signal it and *wait for the exit file*, so
        # the attempt ends with a real terminal record rather than a truncated one —
        # the same thing `implement._await_exit` does at the end of a foreground run.
        ctx.log("reap.timeout", level="warning", state=str(state), overrun_seconds=int(overrun))
        kill = getattr(ctx.sandbox, "kill_agent", None)
        if kill is not None:
            kill(handle.sandbox)
        deadline = time.monotonic() + KILL_GRACE_SECONDS
        while time.monotonic() < deadline and not attempt_dir.exit_file.exists():
            time.sleep(5)
        if attempt_dir.exit_file.exists():
            _collect(ctx, state, attempt_dir)
            return Verdict(Outcome.COLLECTED, f"timed out after {int(overrun)}s over budget")
        return _orphan(
            ctx, "state-timeout", f"{state} ran {int(overrun)}s past its timeout and was signalled"
        )

    return Verdict(Outcome.RUNNING, f"{state} attempt {ctx.run.attempt}")


def _collect(ctx: Context, state: State, attempt_dir: AttemptDir) -> None:
    if state is State.IMPLEMENTING:
        implement_step.collect(ctx, attempt_dir, ctx.run.attempt)
    else:
        plan_step.collect(ctx, attempt_dir)


def _entered_from(ctx: Context) -> State | None:
    """The state this run came *from* when it entered the one it is in now."""
    for row in reversed(ctx.store.transitions(ctx.run.id)):
        if str(row["to_state"]) == str(ctx.run.state):
            return State(str(row["from_state"])) if row["from_state"] else None
    return None


def _overrun_seconds(ctx: Context, state: State) -> float | None:
    """How far past the state's timeout this run is, or None if it is inside it.

    Measured from the transition into the state rather than from the attempt's
    `started_at`, because a resumed attempt keeps the state it re-entered and the
    budget being spent is the state's, not the attempt's.
    """
    entered = _entered_state_at(ctx)
    if entered is None:
        return None
    elapsed = time.time() - entered
    timeout = ctx.timeout_for(state)
    return elapsed - timeout if elapsed > timeout else None


def _entered_state_at(ctx: Context, /) -> int | None:
    rows = ctx.store.transitions(ctx.run.id)
    for row in reversed(rows):
        if str(row["to_state"]) == str(ctx.run.state):
            return int(row["at"])
    return None


def _orphan(ctx: Context, reason: str, detail: str) -> Verdict:
    """Record `-> resumable` here rather than raising it.

    `factory run` raises `Resumable` and lets `cmd_run` record it, because there is a
    call stack that knows what it was doing. A tick reaping someone else's attempt has
    no such stack, and a transition that depends on an exception reaching the right
    handler is a transition that will one day not be recorded at all.
    """
    ctx.store.finish_attempt(
        ctx.run.id, ctx.run.attempt, ctx.run.state, exit_code=None, outcome="orphaned"
    )
    ctx.store.record_transition(
        ctx.run.id,
        from_state=ctx.run.state,
        to_state=State.RESUMABLE,
        actor=AUTOMATIC,
        rule=reason,
        detail=detail[:2000],
    )
    ctx.refresh()
    ctx.log("reap.orphaned", level="warning", reason=reason, detail=detail[:500])
    return Verdict(Outcome.ORPHANED, detail)
