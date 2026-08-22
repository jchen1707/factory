"""§16.3 and §16.3a — resume versus restart, and the escalation ladder.

The decision is made **once**, deterministically, from four facts that are all on disk:
whether a Codex session id was captured, whether the worktree is stopped in the middle
of a git operation, how many attempts the run has already spent, and what the budget
allows. Nothing here calls a model, and nothing here guesses.

The module is deliberately almost pure. `decide` takes values rather than a `Context`
so the whole of §16.3a can be asserted by a table-driven test with no database, no git
and no sandbox — which matters more here than anywhere else in the factory, because
this is the code that runs when everything else has already gone wrong.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from factory.machine import AUTOMATIC, Blocked, State

if TYPE_CHECKING:
    from factory.steps import Context

__all__ = [
    "Disposition",
    "Verdict",
    "backoff_seconds",
    "continuation_prompt",
    "decide",
    "ladder_rung",
    "resume_run",
    "state_that_died",
]


class Disposition(StrEnum):
    """What the next tick does with an orphaned attempt."""

    #: `codex exec resume <session_id>` — the session is intact and the tree is sane.
    RESUME = "resume"
    #: A fresh `codex exec` against the **same worktree**. Work is never thrown away.
    RESTART = "restart"
    #: Rung 3: a fresh context runs `/plan` first. The worktree is not reset.
    REWIND = "rewind"
    #: The budget is spent. `failed` is not terminal — §5.3 gives it one edge, and that
    #: edge is James re-authorising spend.
    FAIL = "fail"


@dataclass(frozen=True)
class Verdict:
    disposition: Disposition
    #: A stable slug, recorded on the transition. "Restarted" with no reason is an
    #: unexplained restart, and §16.3a requires the rung in the audit log.
    reason: str
    #: Which rung of the ladder the *next* attempt is, 1-based.
    rung: int


#: §16.3a, verbatim. Attempt 3 rewinds; attempt 4 is the budget being spent, not a
#: longer wait. Keyed by the attempt about to start, not the one that just died.
_LADDER: dict[int, Disposition] = {
    1: Disposition.RESTART,
    2: Disposition.RESUME,
    3: Disposition.REWIND,
}

#: §16.4. Not exponential beyond the third: a fourth try is a `failed`, not a longer
#: wait, so there is no fourth number to pick.
_BACKOFF: tuple[int, ...] = (0, 60, 300)


def ladder_rung(attempts_spent: int) -> int:
    """The 1-based rung the next attempt occupies."""
    return attempts_spent + 1


def backoff_seconds(attempts_spent: int) -> int:
    """How long to wait before the next attempt starts — 0 s, 60 s, 300 s (§16.4)."""
    index = min(attempts_spent, len(_BACKOFF) - 1)
    return _BACKOFF[index]


def decide(
    *,
    attempts_spent: int,
    attempts_in_state: int,
    session_id: str | None,
    interrupted_by: str | None,
    max_attempts: int,
    max_total_attempts: int,
) -> Verdict:
    """§16.3's three-branch choice, with §16.3a's ladder layered over it.

    `attempts_spent` is how many attempts this run has already recorded, so the rung
    being decided is `attempts_spent + 1`. `attempts_in_state` is the same count
    narrowed to the state that just died, because §16.4's two ceilings are different
    numbers counting different things — `max_attempts` is per state, `max_total_attempts`
    is per run, and collapsing them would let a run that cycled through three states
    spend fifteen attempts. `interrupted_by` is
    `repo.in_progress_operation`'s answer: a half-finished merge or rebase is the one
    tree state a resumed session must not be handed, because the model did not leave
    it that way and cannot be told so in a continuation prompt.

    The ladder is consulted **before** the session id, not after. Two attempts that ran
    the same prompt against the same context produce the same failure, and resuming a
    session for a third time is exactly the repetition the ladder exists to replace.
    """
    rung = ladder_rung(attempts_spent)
    if rung > max_total_attempts:
        return Verdict(Disposition.FAIL, "max-total-attempts", rung)
    if attempts_in_state >= max_attempts:
        return Verdict(Disposition.FAIL, "max-attempts-in-state", rung)

    planned = _LADDER.get(rung, Disposition.FAIL)
    if planned is Disposition.FAIL:
        return Verdict(Disposition.FAIL, "ladder-exhausted", rung)
    if planned is Disposition.REWIND:
        return Verdict(Disposition.REWIND, "rewind-to-planning", rung)

    if planned is Disposition.RESUME:
        if session_id is None:
            return Verdict(Disposition.RESTART, "no-session-id", rung)
        if interrupted_by is not None:
            return Verdict(Disposition.RESTART, f"worktree-mid-{interrupted_by}", rung)
        return Verdict(Disposition.RESUME, "session-intact", rung)

    return Verdict(Disposition.RESTART, "first-retry", rung)


# --------------------------------------------------------------------------------
# Driving the decision — §16.3 applied to a run that is already `resumable`
# --------------------------------------------------------------------------------


def state_that_died(ctx: Context) -> State:
    """Which state the run was in when it became `resumable`.

    Read from the transition that recorded the stop rather than inferred, because
    `resumable` is reachable from four states and the ladder, the session id and the
    per-state attempt ceiling are all keyed by which one it was.
    """
    for row in reversed(ctx.store.transitions(ctx.run.id)):
        if str(row["to_state"]) == str(State.RESUMABLE) and row["from_state"]:
            return State(str(row["from_state"]))
    return State.IMPLEMENTING


def resume_run(ctx: Context) -> Verdict:
    """Take one `resumable` run to its next attempt, or to `failed`.

    Returns without starting anything when the backoff window has not elapsed — a
    verdict of `Disposition.RESTART` with reason `backoff` — so a daemon ticking every
    60 s does not turn §16.4's 0/60/300 s schedule into three immediate retries.

    Everything this reads is durable: the transition log says which state died, the
    attempts table says whether a session id was captured, and git says whether the
    worktree is stopped part-way through an operation. None of it came from the process
    that started the attempt, because on the runs this exists for that process is gone.
    """
    from factory.steps import implement as implement_step
    from factory.steps import plan as plan_step

    if ctx.run.state is not State.RESUMABLE:
        raise Blocked("not-resumable", f"{ctx.run.linear_id} is at {ctx.run.state}")

    died_in = state_that_died(ctx)
    # Before the backoff, not after it: a run that cannot afford another attempt should
    # say so now rather than wait five minutes to say it. Nothing is spent either way.
    _refuse_over_budget(ctx)

    waited = _seconds_since_last_transition(ctx)
    wait_for = backoff_seconds(ctx.run.attempt)
    if waited < wait_for:
        return Verdict(Disposition.RESTART, "backoff", ladder_rung(ctx.run.attempt))

    verdict = decide(
        attempts_spent=ctx.run.attempt,
        attempts_in_state=ctx.store.attempts_in_state(ctx.run.id, died_in),
        session_id=ctx.store.session_id(ctx.run.id, ctx.run.attempt, died_in),
        interrupted_by=_interrupted_by(ctx),
        max_attempts=ctx.registry.defaults.max_attempts,
        max_total_attempts=ctx.registry.defaults.max_total_attempts,
    )
    ctx.log(
        "recovery.decided",
        disposition=str(verdict.disposition),
        reason=verdict.reason,
        rung=verdict.rung,
        died_in=str(died_in),
    )

    if verdict.disposition is Disposition.FAIL:
        ctx.store.record_transition(
            ctx.run.id,
            from_state=State.RESUMABLE,
            to_state=State.FAILED,
            actor=AUTOMATIC,
            rule=verdict.reason,
            detail=f"rung {verdict.rung}; last state {died_in}",
        )
        ctx.refresh()
        return verdict

    if verdict.disposition is Disposition.REWIND:
        plan_step.start(ctx)
        return verdict

    session = (
        ctx.store.session_id(ctx.run.id, ctx.run.attempt, died_in)
        if verdict.disposition is Disposition.RESUME
        else None
    )
    implement_step.start(ctx, resume_session=session, continuation=continuation_prompt(ctx))
    return verdict


def continuation_prompt(ctx: Context) -> str:
    """§16.3a rung 2 — the failure evidence, in the words the next attempt needs.

    Two facts and nothing else: how the previous attempt ended, and what it already
    changed. Both are read back rather than remembered, and both are things the model
    can check for itself in the worktree — which is the point. A continuation that
    asserted more than the evidence supports would be the factory telling the model a
    story about a run it did not watch.
    """
    from factory import repo

    lines = ["### How the previous attempt ended", ""]
    for row in reversed(ctx.store.transitions(ctx.run.id)):
        if str(row["to_state"]) == str(State.RESUMABLE):
            lines += [f"- reason: `{row['rule'] or 'unknown'}`", ""]
            if row["detail"]:
                lines += ["```", str(row["detail"])[:2000], "```", ""]
            break

    if ctx.run.worktree:
        try:
            stat = repo.diff_stat(Path(ctx.run.worktree), ctx.run.base_ref or "HEAD")
        except repo.GitError:
            stat = ""
        if stat:
            lines += ["### What it already changed", "", "```", stat, "```", ""]
    return "\n".join(lines)


def _seconds_since_last_transition(ctx: Context) -> float:
    rows = ctx.store.transitions(ctx.run.id)
    return time.time() - int(rows[-1]["at"]) if rows else 0.0


def _interrupted_by(ctx: Context) -> str | None:
    if not ctx.run.worktree:
        return None
    worktree = Path(ctx.run.worktree)
    if not worktree.is_dir():
        return None
    from factory import repo

    return repo.in_progress_operation(worktree)


def _refuse_over_budget(ctx: Context) -> None:
    """§16.4 — a run whose next attempt would cross the ceiling blocks *before* it starts.

    Checked here rather than mid-attempt on purpose: the ceiling is a reason not to
    begin, and a run cut off part-way through a write is a worse outcome than one that
    stopped one attempt early and said so.
    """
    _, _, usd = ctx.store.spend(ctx.run.id)
    if usd is None:
        return
    ceiling = ctx.routing.usd_per_run
    if usd >= ceiling:
        raise Blocked("budget-exceeded", f"${usd:.2f} spent against a ${ceiling:.2f} ceiling")
