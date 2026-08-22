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

from factory.machine import AUTOMATIC, Blocked, State, requires_human_rule

if TYPE_CHECKING:
    from factory.steps import Context

__all__ = [
    "Disposition",
    "Verdict",
    "backoff_seconds",
    "continuation_prompt",
    "decide",
    "ladder_rung",
    "next_attempt_disposition",
    "resume",
    "resume_run",
    "state_before",
    "suspend",
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


def state_before(ctx: Context, target: State) -> State:
    """The state a run was in before it became `target`.

    `resumable`, `suspended` and `blocked` all record the state they came from in the
    `from_state` of the transition that parked them, and `resume` needs exactly that for
    all three. One lookup rather than three: this file already records one defect that was
    two functions disagreeing about the same input.

    Read from the transition rather than inferred, because `resumable` is reachable from
    four states and the ladder, the session id and the per-state attempt ceiling are all
    keyed by which one it was.
    """
    for row in reversed(ctx.store.transitions(ctx.run.id)):
        if str(row["to_state"]) == str(target) and row["from_state"]:
            return State(str(row["from_state"]))
    return State.IMPLEMENTING


def resume_run(ctx: Context, *, skip_backoff: bool = False) -> Verdict:
    """Take one `resumable` run to its next attempt, or to `failed`.

    Returns without starting anything when the backoff window has not elapsed — a
    verdict of `Disposition.RESTART` with reason `backoff` — so a daemon ticking every
    60 s does not turn §16.4's 0/60/300 s schedule into three immediate retries.
    `skip_backoff` is for `factory resume`: a human who just typed the command does not
    mean "try in five minutes", and the ladder's ceilings still bound the attempt.

    Everything this reads is durable: the transition log says which state died, the
    attempts table says whether a session id was captured, and git says whether the
    worktree is stopped part-way through an operation. None of it came from the process
    that started the attempt, because on the runs this exists for that process is gone.
    """
    from factory.steps import implement as implement_step
    from factory.steps import plan as plan_step

    if ctx.run.state is not State.RESUMABLE:
        raise Blocked("not-resumable", f"{ctx.run.linear_id} is at {ctx.run.state}")

    died_in = state_before(ctx, State.RESUMABLE)
    # Before the backoff, not after it: a run that cannot afford another attempt should
    # say so now rather than wait five minutes to say it. Nothing is spent either way.
    _refuse_over_budget(ctx)

    waited = _seconds_since_last_transition(ctx)
    wait_for = backoff_seconds(ctx.run.attempt)
    if not skip_backoff and waited < wait_for:
        return Verdict(Disposition.RESTART, "backoff", ladder_rung(ctx.run.attempt))

    if died_in in (State.VERIFYING, State.REVIEWING):
        # A verify/review that orphaned or timed out re-runs the step fresh. There is no
        # codex session to resume (verify is a node gate report; review.start re-runs
        # Tier-1/Tier-2 fresh), so `decide()`'s RESUME/RESTART/REWIND dispositions all
        # collapse to "re-run" — and its `attempts_in_state` ceiling is decorative here,
        # because `start_attempt` is `INSERT OR REPLACE` on `(run, attempt, state)` and
        # verify/review re-runs keep the same attempt number (handoff note 2: they read
        # the implement attempt's evidence at `run/<attempt>`). The transitions table
        # counts the re-runs instead: one `died_in -> resumable` row per reap orphan.
        return _rerun_detached(ctx, died_in)

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


def next_attempt_disposition(ctx: Context) -> Verdict:
    """The ladder verdict for the implement attempt that follows a gate-fail loop-back.

    The verify-fail loop-back (`verifying -> implementing`) is a normal transition, not a
    recovery, so it does not pass through `resume_run` — but §16.3a's ladder applies to it
    just the same: a third consecutive failure rewinds to `planning` rather than running
    the same prompt a third time, and a fourth is the budget spent, not another attempt.
    `decide` is the single authority for that ladder, so this is the loop-back's thin way
    to reach it without re-deriving the session and ceiling logic that `resume_run` carries.

    `attempts_spent` is the attempt that just failed (`ctx.run.attempt`), so the rung being
    decided is the next one — `ctx.run.attempt + 1` — matching `resume_run`'s convention.
    """
    return decide(
        attempts_spent=ctx.run.attempt,
        attempts_in_state=ctx.store.attempts_in_state(ctx.run.id, State.IMPLEMENTING),
        session_id=ctx.store.session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING),
        interrupted_by=_interrupted_by(ctx),
        max_attempts=ctx.registry.defaults.max_attempts,
        max_total_attempts=ctx.registry.defaults.max_total_attempts,
    )


def _rerun_detached(ctx: Context, died_in: State) -> Verdict:
    """Re-run an orphaned/timed-out verify or review, bounded by a re-run ceiling.

    `resume_run`'s branch for `died_in in (verifying, reviewing)`. The step's `start`
    advances `resumable -> {verifying,reviewing}` (a valid automatic edge) and spawns
    the detached run; no attempt counter changes, so verify/review keep reading the
    implement attempt's evidence at `run/<attempt>`. The `resumable_reentries` count
    escalates to `failed` at `max_attempts`, which the `attempts_in_state` ceiling
    could not do for a same-attempt re-run.
    """
    from factory.steps import review as review_step
    from factory.steps import verify as verify_step

    rung = ladder_rung(ctx.run.attempt)
    reentries = ctx.store.resumable_reentries(ctx.run.id, died_in)
    if reentries >= ctx.registry.defaults.max_attempts:
        ctx.store.record_transition(
            ctx.run.id,
            from_state=State.RESUMABLE,
            to_state=State.FAILED,
            actor=AUTOMATIC,
            rule=f"max-reruns-{died_in}",
            detail=f"{reentries} {died_in} re-runs against a {ctx.registry.defaults.max_attempts} ceiling",
        )
        ctx.refresh()
        ctx.log(
            "recovery.rerun_failed",
            level="warning",
            state=str(died_in),
            reentries=reentries,
        )
        return Verdict(Disposition.FAIL, f"max-reruns-{died_in}", rung)

    if died_in is State.VERIFYING:
        verify_step.start(ctx)
    else:
        review_step.start(ctx)
    ctx.log("recovery.rerun", state=str(died_in), reentries=reentries)
    return Verdict(Disposition.RESTART, f"rerun-{died_in}", rung)


def resume(ctx: Context, *, from_state: str | None = None, authorise: bool = False) -> State:
    """§16.3b — James resumes a parked run. Re-enters the state it left, or a forced one.

    Handles `suspended`, `blocked` and (after `--authorise`) `failed`/`resumable`. The
    attempt counter increments only when the resume starts an agent (§16.3b, and the
    handoff's note 2): `verify` and `deliver` address `factory_dir / "run" / str(attempt)`,
    so an increment on a non-agent resume would point at a directory that does not exist
    and spend budget for a model call that never happens. The tick's forward dispatch
    drives the re-entered `verifying`/`reviewing` state the rest of the way.

    Every entry the run makes here is a human act, so each `advance` carries
    `actor="human"`: `SUSPENDED -> *` is `resume-is-james` and `BLOCKED -> *` is
    `unblock-is-a-judgement`, and the starts inside `implement`/`plan` thread that actor
    through their own `advance` or it would refuse.
    """
    from factory.steps import advance
    from factory.steps import implement as implement_step
    from factory.steps import plan as plan_step

    state = ctx.run.state
    if state is State.FAILED:
        if not authorise:
            raise Blocked(
                "resume-needs-authorise",
                f"{ctx.run.linear_id} is `failed`; pass --authorise to re-authorise spend "
                "(§16.4: `failed -> resumable` is James's explicit act)",
            )
        advance(ctx, State.RESUMABLE, actor="human", rule="reauthorise-spend")
        ctx.refresh()
        state = ctx.run.state

    # A resumable run with no forced target uses the ladder — the existing machinery, with
    # its ceilings. A human typed this, so the §16.4 backoff does not apply.
    if state is State.RESUMABLE and not from_state:
        resume_run(ctx, skip_backoff=True)
        return state_before(ctx, State.RESUMABLE)

    if state not in (State.RESUMABLE, State.SUSPENDED, State.BLOCKED):
        raise Blocked(
            "not-resumable",
            f"{ctx.run.linear_id} is at {state}; resume works on suspended, blocked or "
            "resumable runs. Use `factory tick` to advance a run that is already going.",
        )

    target = State(from_state) if from_state else state_before(ctx, state)
    if target not in (State.IMPLEMENTING, State.PLANNING, State.VERIFYING, State.REVIEWING):
        raise Blocked(
            "resume-target-invalid",
            f"cannot resume {ctx.run.linear_id} into {target}; --from must be one of "
            "implementing, planning, verifying, reviewing.",
        )

    _refuse_over_budget(ctx)
    rule = requires_human_rule(state, target)

    if target in (State.VERIFYING, State.REVIEWING):
        # No agent, no increment: re-enter and let the forward dispatch re-run the gate
        # report / review against the attempt directory the implementer already wrote.
        advance(ctx, target, actor="human", rule=rule)
        return target

    if target is State.PLANNING:
        plan_step.start(ctx, actor="human")
        return target

    implement_step.start(
        ctx,
        resume_session=_session_to_resume(ctx, forced=bool(from_state)),
        continuation=continuation_prompt(ctx),
        actor="human",
    )
    return target


def _session_to_resume(ctx: Context, *, forced: bool) -> str | None:
    """The Codex session id to resume into `implementing`, or None for a fresh attempt.

    `--from implementing` forces a fresh attempt (§16.3b: "starts a new attempt against
    the existing worktree"), so None. Otherwise the session the prior attempt captured is
    resumed by id — the same decision `resume_run` makes for rung 2 — unless the worktree
    is stopped mid merge/rebase/cherry-pick, which a resumed session must not be handed
    because the model did not leave it that way and cannot be told so in a continuation.
    """
    if forced:
        return None
    session = ctx.store.session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING)
    if session is None:
        return None
    if _interrupted_by(ctx) is not None:
        return None
    return session


# --------------------------------------------------------------------------------
# Suspend — §16.3b's other half
# --------------------------------------------------------------------------------


_SUSPEND_STEP = "suspend"


def _wait_for_exit_file(attempt_dir: Path, grace_seconds: int) -> int | None:
    """Poll the wrapper's atomic `exit` file, the way `reap` does after a kill.

    The wrapper writes `exit` last and atomically, so its appearance is the real terminal
    record; a suspend that parks without it leaves a truncated attempt. Returns the exit
    code if it landed in time, else None.
    """
    exit_path = attempt_dir / "exit"
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if exit_path.exists():
            try:
                return int(exit_path.read_text().strip())
            except (OSError, ValueError):
                return None
        time.sleep(0.05)
    return None


def _stop_sandbox_if_idle(ctx: Context) -> None:
    """§16.3b step 2: stop the build sandbox only when no other run is using it.

    A project's build sandbox is shared across the project's runs, so stopping it under a
    second run would kill that run's agent. `active_runs_for_project` is the set of runs
    that exec into it.
    """
    others = [r for r in ctx.store.active_runs_for_project(ctx.project.name) if r.id != ctx.run.id]
    if others:
        return
    ctx.sandbox.stop(ctx.project.build_sandbox)


def _suspend_announce(ctx: Context, reason: str, origin: State) -> None:
    """One Linear comment, idempotent by marker. Best-effort: a Linear outage degrades the
    announcement but never masks the suspend. No `needs-info` label — suspension is a
    park, not a stop, so the ticket stays In Progress and the poller does not need to be
    told to leave it alone."""
    from factory.intake.linear import LinearError
    from factory.steps import effect_marker, record_effect

    if ctx.dry_run:
        ctx.would(
            f"linear: comment on {ctx.run.linear_id} (suspend; marker {effect_marker(ctx, _SUSPEND_STEP)})"
        )
        return
    marker = effect_marker(ctx, _SUSPEND_STEP)
    issue_uuid, _ = ctx.linear.issue_uuid(ctx.run.linear_id)
    body = (
        f"<!-- {marker} -->\n"
        f"The factory was **suspended** from `{origin}`: {reason}\n\n"
        f"The worktree, branch and Codex session are kept. "
        f"`factory resume {ctx.run.linear_id}` resumes it. "
        f"Run `{ctx.run.id}`, attempt {ctx.run.attempt}.\n"
    )
    try:
        record_effect(
            ctx,
            step=_SUSPEND_STEP,
            system="linear",
            key="comment:suspended",
            reconcile=lambda: (
                "found" if ctx.linear.comment_marker_present(ctx.run.linear_id, marker) else None
            ),
            perform=lambda: ctx.linear.add_comment(issue_uuid, body),
        )
    except LinearError as exc:
        ctx.log("suspend.announce_failed", level="error", detail=str(exc)[:500])


def suspend(ctx: Context, *, reason: str) -> State:
    """§16.3b — park the run in `ctx`. Signal the agent, wait for a real `exit` so the
    attempt has a terminal record, stop the build sandbox if no other run is using it,
    record `-> suspended` keeping the worktree/branch/attempt/session, and announce once.

    The caller (`cmd_suspend`) owns the lease and the surrounding real adapters; the
    transition is `actor="human"` under `suspend-is-james`. Returns the state the run was
    in when parked, which `resume` reads back from the transition's `from_state`.
    """
    from factory.steps import reap as reap_step

    origin = ctx.run.state
    if origin in reap_step.DETACHED_STATES:
        row = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, origin)
        if row and row["artifact_dir"]:
            attempt_dir = Path(str(row["artifact_dir"]))
            kill = getattr(ctx.sandbox, "kill_agent", None)
            if kill is not None:
                kill(str(row["sandbox"] or ctx.project.build_sandbox))
            exit_code = _wait_for_exit_file(attempt_dir, reap_step.KILL_GRACE_SECONDS)
            ctx.store.finish_attempt(
                ctx.run.id, ctx.run.attempt, origin, exit_code=exit_code, outcome="suspended"
            )
        # No attempt row: nothing is running (the run was left mid-state by a crash).
        # Parking it is still the right call; there is no agent to kill.

    _stop_sandbox_if_idle(ctx)

    ctx.store.record_transition(
        ctx.run.id,
        from_state=origin,
        to_state=State.SUSPENDED,
        actor="human",
        rule="suspend-is-james",
        detail=reason,
    )
    ctx.refresh()
    _suspend_announce(ctx, reason, origin)
    return origin


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
