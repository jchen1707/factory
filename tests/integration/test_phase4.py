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
from factory.steps import review as review_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
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


def test_start_after_a_verify_fail_loopback_does_not_illegally_re_enter_implementing(
    ctx: Context,
) -> None:
    # FRO-6's resume crashed here. lighthouse failed the gate report, verify looped back to
    # `implementing`, and the tick's drive loop called `implement.start` to begin the next
    # attempt. `start` then recorded `implementing -> implementing`, which is not in the
    # table, and the run blocked on `illegal-transition`. The hop back to `implementing` was
    # already recorded by `verify`; `start` must not record it a second time — it begins a
    # fresh attempt against the same worktree, exactly as from `worktree_ready`.
    _to_worktree(ctx)  # approved -> worktree_ready
    implement_step.start(ctx)  # worktree_ready -> implementing, attempt 1
    assert ctx.state is State.IMPLEMENTING
    assert ctx.run.attempt == 1

    # verify failed and advanced `verifying -> implementing`; the drive loop calls `start`
    # again. Before the fix this raised Blocked("illegal-transition").
    started = implement_step.start(ctx, continuation="previous attempt failed the gate")

    assert started is not None
    assert ctx.run.attempt == 2  # a fresh attempt, not a re-run of attempt 1
    assert ctx.state is State.IMPLEMENTING  # no illegal hop was recorded


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
# verify and review are two-phase — start spawns, a later tick collects
# --------------------------------------------------------------------------------


def _stub_redphase(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake's test gate returns exit 0, which the red-phase classifier reads as a green
    `test-proves-nothing` block. The two-phase verify tests stop at verify, but the tick
    drives straight on into review.start, so the replay is stubbed to "proceed"."""
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "proceed")
    monkeypatch.setattr(review_step.redphase, "weakening_guard", lambda ctx: [])


def test_reap_returns_start_needed_when_verify_has_no_attempt_row(ctx: Context) -> None:
    # Verify is entered by implement.collect, which advances the state but spawns nothing,
    # so the first tick to see `verifying` finds no attempt row and must call `verify.start`.
    # `START_NEEDED` is the outcome that makes the drive do that (mirroring NEXT_STEP); the
    # old behaviour orphaned a no-row implement attempt, which is wrong for verify.
    _to_verifying(ctx)

    verdict = reap_step.reap(ctx)

    assert verdict.outcome is reap_step.Outcome.START_NEEDED


def test_the_drive_starts_verify_on_start_needed(ctx: Context) -> None:
    # Without a START_NEEDED branch the drive would break (state unchanged) and never
    # spawn the gate report — the run would sit at `verifying` forever under the daemon.
    _to_verifying(ctx)

    _tick(ctx)

    run = ctx.store.run_by_id(ctx.run.id)
    assert run is not None
    assert run.state is State.VERIFYING
    assert ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.VERIFYING) is not None


def test_a_second_tick_collects_the_verify_run_the_previous_one_started(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The core two-phase property, for verify: the tick that asks is never the tick that
    # started the run. `start` spawned the gate report last tick; this tick reaps `exit`
    # and collects, advancing to `reviewing` — in a call that never saw `start`'s return.
    _stub_redphase(monkeypatch)
    _start_an_attempt(ctx)
    _tick(ctx)  # implement collected -> verifying started (detached, exit written)
    assert ctx.store.run_by_id(ctx.run.id).state is State.VERIFYING  # type: ignore[union-attr]

    _tick(ctx)  # verify collected -> reviewing

    hops = [(row["from_state"], row["to_state"]) for row in ctx.store.transitions(ctx.run.id)]
    assert (str(State.VERIFYING), str(State.REVIEWING)) in hops


def test_collecting_verify_does_not_increment_the_attempt(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Handoff note 2: verify reads the implement attempt's `last-message.json`, so a
    # verify that incremented would point at a directory that does not exist. The increment
    # belongs to the *next* implement.start, not to verify.collect.
    _stub_redphase(monkeypatch)
    _to_verifying(ctx)
    attempt_before = ctx.run.attempt

    _tick(ctx)  # verify.start (the fake writes exit synchronously)
    _tick(ctx)  # verify collected -> reviewing

    assert ctx.store.run_by_id(ctx.run.id).attempt == attempt_before  # type: ignore[union-attr]


def test_an_orphaned_verify_run_goes_resumable_and_is_rerun(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An orphaned verify attempt (row, no exit, dead holder) goes resumable — uniform with
    # implement — and `resume_run` re-runs it via `verify.start` (not `implement.start`,
    # which would silently re-run a whole implement with an incremented attempt).
    _stub_redphase(monkeypatch)
    _to_verifying(ctx)
    # Spawn verify directly so ctx keeps the fixture's lease (a `_tick` would release it on
    # exit, and `resume_run`'s `advance` needs a holder).
    verify_step.start(ctx)
    assert ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.VERIFYING) is not None

    _fake(ctx).poll_status = RunStatus.ORPHANED  # the holder died; no exit will appear
    verdict = reap_step.reap(ctx)  # ORPHANED -> resumable (no advance, no lease needed)
    assert verdict.outcome is reap_step.Outcome.ORPHANED
    _fake(ctx).poll_status = None
    ctx.refresh()
    assert ctx.run.state is State.RESUMABLE

    # The rerun is gated by §16.4's backoff, so it does not fire in the same tick that
    # orphaned. `factory resume` (and the daemon, after the window) calls `resume_run`;
    # here it is driven directly with the backoff skipped. It re-runs `verify.start`
    # (not `implement.start`, which would silently spend a fresh implement attempt).
    recovery.resume_run(ctx, skip_backoff=True)
    ctx.refresh()

    hops = [(row["from_state"], row["to_state"]) for row in ctx.store.transitions(ctx.run.id)]
    assert (str(State.VERIFYING), str(State.RESUMABLE)) in hops  # the orphan
    assert (str(State.RESUMABLE), str(State.VERIFYING)) in hops  # the rerun via verify.start
    assert ctx.run.state is State.VERIFYING


def test_a_verify_run_that_orphaned_past_the_ceiling_escalates_to_failed(
    ctx: Context,
) -> None:
    # `attempts_in_state` is decorative for verify (start_attempt is INSERT OR REPLACE on a
    # fixed attempt number), so the re-run ceiling counts `verify -> resumable` transitions
    # instead. At `max_attempts` of them, `resume_run` escalates to `failed` rather than
    # re-running a hanging gate report forever — the loop the daemon cannot have.
    _to_verifying(ctx)
    for _ in range(ctx.registry.defaults.max_attempts):
        ctx.store.record_transition(
            ctx.run.id,
            from_state=State.VERIFYING,
            to_state=State.RESUMABLE,
            actor="auto",
            rule="attempt-orphaned",
            detail="orphan",
        )
    ctx.refresh()

    verdict = recovery.resume_run(ctx, skip_backoff=True)

    assert verdict.disposition is Disposition.FAIL
    run = ctx.store.run_by_id(ctx.run.id)
    assert run is not None
    assert run.state is State.FAILED


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


# --------------------------------------------------------------------------------
# §16.3b — suspend and resume
# --------------------------------------------------------------------------------


def _to_verifying(ctx: Context) -> None:
    """Drive a run to `verifying` with an implement attempt directory on disk, the way a
    finished implement step leaves it. `resume` into `verifying` re-runs the gate report
    against that directory, so it has to exist."""
    _start_an_attempt(ctx)
    verdict = reap_step.reap(ctx)
    assert verdict.outcome is reap_step.Outcome.COLLECTED
    assert ctx.state is State.VERIFYING


def _block_at(ctx: Context, state: State, reason: str) -> None:
    """Park a run at `blocked` from `state` the way a blocking step does — the FRO-6 shape
    is `blocked` from `verifying` with a passing gate report."""
    ctx.store.update_run(ctx.run.id, blocked_reason=reason)
    ctx.store.record_transition(
        ctx.run.id,
        from_state=state,
        to_state=State.BLOCKED,
        actor="auto",
        rule=reason,
        detail=reason,
    )
    ctx.refresh()


def test_suspend_parks_a_running_agent_and_keeps_everything(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    ctx.store.set_session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING, "01a0-fake-thread")
    worktree = Path(ctx.run.worktree or "")
    branch = ctx.run.branch
    sandbox = ctx.project.build_sandbox
    assert sandbox in _fake(ctx).running

    origin = recovery.suspend(ctx, reason="checking something")

    assert origin is State.IMPLEMENTING
    assert ctx.state is State.SUSPENDED
    # The transition is a human act, under the reserved rule.
    row = ctx.store.transitions(ctx.run.id)[-1]
    assert (row["from_state"], row["to_state"], row["actor"], row["rule"]) == (
        str(State.IMPLEMENTING),
        str(State.SUSPENDED),
        "human",
        "suspend-is-james",
    )
    # The attempt got a real terminal record, and the worktree/branch/session survived.
    attempt = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING)
    assert attempt is not None
    assert attempt["outcome"] == "suspended"
    assert worktree.exists()
    assert (
        ctx.store.session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING) == "01a0-fake-thread"
    )
    assert branch is not None
    # Stopped, because no other run shares the sandbox.
    assert sandbox not in _fake(ctx).running
    # One suspend comment (the claim step already commented on the way through).
    assert sum("**suspended**" in c for c in ctx.linear.comments) == 1  # type: ignore[attr-defined]


def test_suspend_leaves_the_sandbox_running_when_another_run_shares_it(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    sandbox = ctx.project.build_sandbox
    # A second run of the same project is mid-implement — it shares the build sandbox. A
    # different ticket, because `insert_run` no-ops on a live ticket and would hand back the
    # same run.
    other = ctx.store.insert_run(linear_id="BAC-9", project=ctx.project.name, team="BAC")
    assert other.id != ctx.run.id
    ctx.store.record_transition(
        other.id, from_state=State.APPROVED, to_state=State.IMPLEMENTING, actor="auto"
    )

    recovery.suspend(ctx, reason="checking something")

    # Not stopped: the other run still needs it.
    assert sandbox in _fake(ctx).running


def test_resume_into_verifying_does_not_increment_the_attempt(ctx: Context) -> None:
    # The handoff's note 2. `verify` and `deliver` address `factory_dir / "run" / str(attempt)`,
    # so a resume that incremented would point at a directory that does not exist and the
    # gate report would block on the missing last-message.json.
    _to_verifying(ctx)
    attempt_before = ctx.run.attempt

    recovery.suspend(ctx, reason="park at verify")
    assert ctx.state is State.SUSPENDED

    recovery.resume(ctx)  # no --from: re-enter the recorded state (verifying)
    assert ctx.state is State.VERIFYING
    assert ctx.run.attempt == attempt_before  # the increment that must not happen

    # And the gate report actually runs against the existing attempt directory — it would
    # raise `schema-invalid` (no last-message.json) had the attempt been incremented.
    verify_step.run(ctx)
    assert ctx.state is State.REVIEWING


def test_resume_a_blocked_at_verifying_run_re_enters_verifying(ctx: Context) -> None:
    # FRO-6's shape: blocked *at* verifying with a passing gate report. The cheap repair is
    # `blocked -> verifying`, the edge this phase adds — without it `resume` raises
    # `illegal-transition`.
    _to_verifying(ctx)
    _block_at(ctx, State.VERIFYING, "evidence-mismatch")
    assert ctx.state is State.BLOCKED
    attempt_before = ctx.run.attempt

    recovery.resume(ctx)

    assert ctx.state is State.VERIFYING
    assert ctx.run.attempt == attempt_before  # no agent, no increment
    row = ctx.store.transitions(ctx.run.id)[-1]
    assert (row["from_state"], row["to_state"], row["actor"], row["rule"]) == (
        str(State.BLOCKED),
        str(State.VERIFYING),
        "human",
        "unblock-is-a-judgement",
    )
    # The gate report re-runs against the evidence the implementer already wrote.
    verify_step.run(ctx)
    assert ctx.state is State.REVIEWING


def test_resume_a_suspended_implementing_run_resumes_the_session_by_id(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    ctx.store.set_session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING, "01a0-fake-thread")
    recovery.suspend(ctx, reason="park mid-implement")
    attempt_before = ctx.run.attempt

    recovery.resume(ctx)  # no --from: resume the session the suspended attempt captured

    assert ctx.state is State.IMPLEMENTING
    assert ctx.run.attempt == attempt_before + 1  # an agent starts, so the counter increments
    script = _fake(ctx).detached[-1][1]
    assert "01a0-fake-thread" in script  # resumed by id, never `--last`
    assert "resume --last" not in script


def test_resume_from_implementing_forces_a_fresh_attempt_not_a_session_resume(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    ctx.store.set_session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING, "01a0-fake-thread")
    recovery.suspend(ctx, reason="park mid-implement")

    recovery.resume(ctx, from_state="implementing")  # §16.3b: "starts a new attempt"

    assert ctx.state is State.IMPLEMENTING
    script = _fake(ctx).detached[-1][1]
    assert "01a0-fake-thread" not in script  # fresh attempt, no session resume


def test_resume_from_planning_rewinds(ctx: Context) -> None:
    _start_an_attempt(ctx, finish=False)
    recovery.suspend(ctx, reason="park mid-implement")

    recovery.resume(ctx, from_state="planning")

    assert ctx.state is State.PLANNING
    row = ctx.store.transitions(ctx.run.id)[-1]
    assert (row["from_state"], row["to_state"], row["actor"], row["rule"]) == (
        str(State.SUSPENDED),
        str(State.PLANNING),
        "human",
        "resume-is-james",
    )


def test_resume_a_failed_run_needs_authorise(ctx: Context) -> None:
    _to_verifying(ctx)
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.VERIFYING,
        to_state=State.RESUMABLE,
        actor="auto",
        rule="orphaned",
    )
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.RESUMABLE,
        to_state=State.FAILED,
        actor="auto",
        rule="max-attempts-in-state",
    )
    ctx.refresh()
    assert ctx.state is State.FAILED

    with pytest.raises(Blocked) as caught:
        recovery.resume(ctx)
    assert caught.value.reason == "resume-needs-authorise"


def test_resume_authorise_reauthorises_a_failed_run(ctx: Context) -> None:
    _to_verifying(ctx)
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.VERIFYING,
        to_state=State.RESUMABLE,
        actor="auto",
        rule="orphaned",
    )
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.RESUMABLE,
        to_state=State.FAILED,
        actor="auto",
        rule="max-attempts-in-state",
    )
    ctx.refresh()

    recovery.resume(ctx, authorise=True)

    hops = [
        (row["from_state"], row["to_state"], row["actor"], row["rule"])
        for row in ctx.store.transitions(ctx.run.id)
    ]
    # `failed -> resumable` is James's explicit act, then the ladder takes over.
    assert (str(State.FAILED), str(State.RESUMABLE), "human", "reauthorise-spend") in hops
    assert ctx.state is State.IMPLEMENTING  # restart (no session captured) -> a fresh attempt


def test_suspend_comments_once_and_resume_does_not_repeat(ctx: Context) -> None:
    # F22. Suspension comments once; resuming into the re-entered state does not comment
    # again, so a suspend/resume cycle is one Linear write, not N. (The claim step already
    # commented on the way through, so count the delta, not the absolute total.)
    _to_verifying(ctx)
    before = len(ctx.linear.comments)  # type: ignore[attr-defined]

    recovery.suspend(ctx, reason="park at verify")
    assert len(ctx.linear.comments) == before + 1  # type: ignore[attr-defined]

    recovery.resume(ctx)  # re-enters verifying — no announce
    assert len(ctx.linear.comments) == before + 1  # type: ignore[attr-defined]
