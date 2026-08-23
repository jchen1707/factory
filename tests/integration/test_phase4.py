"""§16 — reap, recover, resume, and the tick that does all three.

Everything here runs against the fakes, in-process, and every case is one of §22's
failure-injection rows. The property under test throughout is the one §4.2 states and
Phase 3 could not exercise: *the tick that asks is never the tick that started the run*.
So no test hands a later call anything the earlier call held in memory — the attempt is
found the way a fresh process finds it, through SQLite and the attempt directory.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory import cli, machine, recovery
from factory.intake.linear import LinearError
from factory.machine import Blocked, State
from factory.recovery import Disposition
from factory.routing import RoutingError
from factory.sandbox.base import RunStatus
from factory.steps import Context, advance
from factory.steps import block as block_step
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import reap as reap_step
from factory.steps import review as review_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
from factory.steps import worktree as worktree_step
from factory.store import Effect, Run
from tests.integration.conftest import GOOD_GATE_REPORT, GOOD_RESULT, FakeSandbox

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((_FIXTURES / name).read_text())


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


def test_reaping_a_running_attempt_captures_its_session_id(ctx: Context) -> None:
    """§16.3's "resume by id, never `--last`" needs the id to have been *captured*, and
    the only code that captured it ran in `factory run`'s foreground watch loop. Under
    the daemon nothing does, so the column stayed NULL for the whole of `implementing`
    and every recovery decision for a live run — suspend/resume included — degraded to
    `RESTART (no-session-id)`, throwing away a session that was sitting in
    `events.jsonl` line 1 the entire time.

    Measured on BAC-6 run `3f03240cd3bc4bd0`: suspended from `implementing` with
    `thread_id 01a02c50-a4e2-76d0-8d97-67959c9ec813` on disk and `session_id` NULL in
    the attempts row.
    """
    _start_an_attempt(ctx, finish=False)
    assert ctx.store.session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING) is None

    reap_step.reap(ctx)

    assert (
        ctx.store.session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING) == "01a0-fake-thread"
    )


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


def test_the_continuation_names_a_block_the_human_is_sending_back(ctx: Context) -> None:
    # `blocked -> implementing` is `unblock-is-a-judgement`: a human decided the finding is
    # worth another attempt. Measured on BAC-6, 2026-08-23, the agent that attempt starts
    # was told nothing about it -- `continuation_prompt` matched only transitions into
    # `resumable`, so a run sent back from `blocked` got an empty "how it ended" section
    # and a diff stat. Nothing else in `implement.start` reads the review findings, so the
    # next attempt had to rediscover the reason it was restarted.
    _start_an_attempt(ctx, finish=False)
    advance(ctx, State.BLOCKED, rule="review-finding", detail="- [high] tests/x.py: vacuous")

    continuation = recovery.continuation_prompt(ctx)

    assert "review-finding" in continuation
    assert "vacuous" in continuation


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


def test_the_rewind_hands_codex_a_schema_path_that_exists_where_codex_runs(
    ctx: Context,
) -> None:
    # Measured on FRO-11 attempt 3, 2026-08-23: the rung-3 rewind spawned, and codex
    # died in under a second with
    #   Failed to read output schema file /Users/james/factory/schemas/implement_result.schema.json
    # `--output-schema` was the control plane's *host* path, and the plan runs inside the
    # build sandbox, which has no `/Users/james/factory`. `implement.start` stages the
    # schema into the attempt directory and points at the copy -- the attempt directory
    # resolves identically on both sides, which is the whole point of §14.1's protocol --
    # and `plan.start` made the copy and then handed over the original.
    _start_an_attempt(ctx, finish=False)
    ctx.store.update_run(ctx.run.id, attempt=2)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)
    _fake(ctx).poll_status = None
    _expire_the_backoff(ctx)

    recovery.resume_run(ctx)

    script = _fake(ctx).detached[-1][1]
    flag = script.split("--output-schema", 1)[1].split()[0].strip("'\"")
    assert Path(flag).is_relative_to(ctx.factory_dir), flag
    assert Path(flag).exists()


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
    # No `set_session_id` here on purpose. This test used to write the id itself and then
    # assert it survived, which proves only that the store round-trips a string: the
    # suspend path never captured one, and on the live BAC-6 suspend the column was NULL.
    # "The Codex session is kept" is a claim about the *system*, so the system has to be
    # the thing that records it.
    _start_an_attempt(ctx, finish=False)
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


def test_a_verifying_resume_re_runs_the_gates_rather_than_reading_a_stale_report(
    ctx: Context,
) -> None:
    # The handoff's open question 1. A `--from verifying` resume re-runs the gate report;
    # `collect` never trusts a `gates.json` a prior run left behind. That is the decision
    # that surfaced FRO-6's hidden lighthouse failure, and `env-gate-failed` makes a re-run
    # safe. Here a stale report says `fail` and the fresh reality is `pass`; the run must
    # reach `reviewing` on the fresh verdict, not the stale one.
    _to_verifying(ctx)
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)

    # A prior run's output, sitting on disk where the re-run would write its own.
    stale = dict(GOOD_GATE_REPORT)
    stale["verdict"] = "fail"
    for gate in stale["gates"]:
        gate["status"] = "fail"
    (attempt_dir / "gates.stdout.txt").write_text(json.dumps(stale, indent=2), encoding="utf-8")
    (attempt_dir / "gates.json").write_text(json.dumps(stale, indent=2), encoding="utf-8")

    _block_at(ctx, State.VERIFYING, "env-gate-failed")
    recovery.resume(ctx)  # re-enters verifying (no --from: reads the recorded origin)
    assert ctx.state is State.VERIFYING

    # `fake.gate_report` is still the default pass — the fresh reality.
    detached_before = len(_fake(ctx).detached)
    verify_step.run(ctx)  # start spawns a fresh gate report, collect reads it back

    assert ctx.state is State.REVIEWING  # the fresh pass won, not the stale fail
    assert len(_fake(ctx).detached) > detached_before  # a gate report actually re-ran
    # And the stale report was overwritten by the re-run, not preserved as the verdict.
    assert json.loads((attempt_dir / "gates.json").read_text())["verdict"] == "pass"


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


# --------------------------------------------------------------------------------
# The --all / lighthouse daemon hazard — an environment gate failure blocks
# --------------------------------------------------------------------------------


def test_an_environment_gate_failure_blocks_rather_than_looping_back(ctx: Context) -> None:
    # The lighthouse/`--all` daemon hazard. Under `--all` every opt-in gate runs, so a
    # ticket claiming `playwright` (in scope) also forces `lighthouse` (out of scope),
    # which fails on a missing Chrome and cannot pass even with it (its caveat: a null
    # score is not a pass). Looping back to `implementing` would spend another ~10 M-token
    # attempt "fixing" a missing tool, repeatedly, until the attempt budget exhausted to
    # `failed`. A failure where every failing gate carries a `caveat` is environmental, so
    # the run blocks for a human — install the tool, or `resume --from reviewing` to bypass.
    fake = _fake(ctx)
    fake.result = dict(GOOD_RESULT, gates_run=["ruff check", "mypy", "lighthouse"])
    fake.gate_report = _fixture("gate-report-env-fail.json")
    _to_verifying(ctx)

    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)

    assert caught.value.reason == "env-gate-failed"
    # It did not loop back to a fresh implement — the exit the daemon hazard exploits.
    loopbacks = [
        row
        for row in ctx.store.transitions(ctx.run.id)
        if row["from_state"] == str(State.VERIFYING) and row["to_state"] == str(State.IMPLEMENTING)
    ]
    assert loopbacks == []


def test_a_real_code_gate_failure_still_loops_back(ctx: Context) -> None:
    # The mirror of the rule. `pytest` is a real code gate (`caveat: null`); an honest
    # failure of it is exactly what the loop-back is for, and the env-gate guard must not
    # steal it. `gate-report-fail.json` has pytest failing with no caveat.
    fake = _fake(ctx)
    fake.gate_report = _fixture("gate-report-fail.json")
    _to_verifying(ctx)

    verify_step.run(ctx)

    # verify -> reviewing is gated behind review; the load-bearing claim here is the
    # loop-back did NOT fire (verdict fail on a code gate routes to implementing, not
    # blocked), and neither did an env-gate block.
    assert ctx.state is State.IMPLEMENTING
    assert ctx.store.run_by_id(ctx.run.id).blocked_reason is None  # type: ignore[union-attr]


# --------------------------------------------------------------------------------
# F23 — a third consecutive gate failure climbs the ladder to planning
# --------------------------------------------------------------------------------


def _finish_implement_and_loop_back(ctx: Context, *, attempt: int) -> None:
    """Record a finished implement attempt that failed the gate and looped back, the shape
    `reap` hands to `_drive_from_here`'s NEXT_STEP branch. The two-phase verify dance is
    skipped because what is under test is the ladder dispatch, not the gate report."""
    ctx.store.finish_attempt(
        ctx.run.id, attempt, State.IMPLEMENTING, exit_code=0, outcome="implemented"
    )
    advance(ctx, State.VERIFYING)
    ctx.store.finish_attempt(ctx.run.id, attempt, State.VERIFYING, exit_code=1, outcome="fail")
    advance(ctx, State.IMPLEMENTING)  # the verify-fail loop-back


def test_a_third_gate_failure_rewinds_to_planning_not_a_fourth_implement(ctx: Context) -> None:
    # F23. Two implement attempts have failed the gate; the third rung of §16.3a's ladder
    # rewinds to `planning` rather than running the same prompt a third time. The edge is
    # `verifying -> planning` (in the table), so the rewind is decided in `verify.collect`
    # before the loop-back advances to `implementing`. Without it the daemon would start
    # attempt 3, 4, 5… unbounded — the gate-fail loop it must not run unattended.
    _to_worktree(ctx)
    implement_step.start(ctx)  # attempt 1
    _finish_implement_and_loop_back(ctx, attempt=1)  # -> implementing (loop-back, rung 2)
    cli._start_next_after_gate_fail(ctx)  # rung 2 -> a fresh implement, attempt 2
    ctx.refresh()
    assert ctx.run.attempt == 2

    # Finish attempt 2 and bring it to `verifying`, where the rewind edge lives.
    ctx.store.finish_attempt(ctx.run.id, 2, State.IMPLEMENTING, exit_code=0, outcome="implemented")
    advance(ctx, State.VERIFYING)
    _fake(ctx).gate_report = _fixture("gate-report-fail.json")  # a real code-gate failure

    # Rung 3: verify.collect rewinds to planning rather than looping back to a third implement.
    verify_step.run(ctx)
    ctx.refresh()
    assert ctx.state is State.PLANNING
    assert ctx.run.attempt == 3  # a rewind is one attempt with two phases


def test_a_fourth_gate_failure_parks_at_resumable_not_a_fifth_implement(ctx: Context) -> None:
    # The ladder's exhaustion. Rung 4 is the budget spent, not another attempt. verify.collect
    # diverts only REWIND (rung 3), so a rung-4 failure loops back to `implementing` and
    # `_start_next_after_gate_fail` parks it at `resumable` — no `implementing -> failed` edge
    # exists — for the next tick's `resume_run` to take to `failed` under the same ladder.
    _to_worktree(ctx)
    implement_step.start(ctx)  # attempt 1 -> implementing
    # Place the run at `implementing` for the fourth time (three attempts spent), the shape
    # the NEXT_STEP branch hands to `_start_next_after_gate_fail` after a rung-4 loop-back.
    ctx.store.update_run(ctx.run.id, attempt=3)
    ctx.refresh()

    cli._start_next_after_gate_fail(ctx)
    ctx.refresh()
    assert ctx.state is State.RESUMABLE
    # A park, not a block: `resume_run` takes it the rest of the way to `failed`.
    assert ctx.store.run_by_id(ctx.run.id).blocked_reason is None  # type: ignore[union-attr]


# --------------------------------------------------------------------------------
# F1/F2 — the effects ledger under a real crash between its two halves
# --------------------------------------------------------------------------------


def _comment_effect(ctx: Context) -> Effect:
    return next(e for e in ctx.store.effects(ctx.run.id) if e.key.startswith("comment:blocked"))


def test_f1_a_crash_before_the_linear_write_leaves_the_effect_intended(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F1. The process dies between the `intended` INSERT and the comment POST. The ledger
    # row is committed first, so the next call finds it and reconciles rather than retrying
    # blindly — the property the whole ledger exists for.
    _to_verifying(ctx)
    before = len(ctx.linear.comments)  # type: ignore[attr-defined]  # the claim comment
    monkeypatch.setenv("FACTORY_CRASH_AT", "linear:comment:blocked:evidence-mismatch:pre")

    with pytest.raises(SystemExit):
        block_step.announce(ctx, "evidence-mismatch", "the detail")

    assert len(ctx.linear.comments) == before  # type: ignore[attr-defined]  # no block comment written
    assert _comment_effect(ctx).status == "intended"


def test_f1_resume_performs_the_write_once_and_confirms(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_verifying(ctx)
    before = len(ctx.linear.comments)  # type: ignore[attr-defined]
    monkeypatch.setenv("FACTORY_CRASH_AT", "linear:comment:blocked:evidence-mismatch:pre")
    with pytest.raises(SystemExit):
        block_step.announce(ctx, "evidence-mismatch", "the detail")

    monkeypatch.delenv("FACTORY_CRASH_AT", raising=False)
    block_step.announce(ctx, "evidence-mismatch", "the detail")

    assert len(ctx.linear.comments) == before + 1  # type: ignore[attr-defined]
    assert _comment_effect(ctx).status == "confirmed"


def test_f2_a_crash_after_the_linear_write_leaves_the_comment_but_effect_intended(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F2. The process dies after the comment landed but before `confirmed`. The write
    # happened; the ledger does not know it did. The next call must find the marker and
    # confirm, not comment again.
    _to_verifying(ctx)
    before = len(ctx.linear.comments)  # type: ignore[attr-defined]
    monkeypatch.setenv("FACTORY_CRASH_AT", "linear:comment:blocked:evidence-mismatch:post")

    with pytest.raises(SystemExit):
        block_step.announce(ctx, "evidence-mismatch", "the detail")

    assert len(ctx.linear.comments) == before + 1  # type: ignore[attr-defined]  # written before crash
    assert _comment_effect(ctx).status == "intended"  # not confirmed


def test_f2_resume_confirms_without_repeating_the_write(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_verifying(ctx)
    before = len(ctx.linear.comments)  # type: ignore[attr-defined]
    monkeypatch.setenv("FACTORY_CRASH_AT", "linear:comment:blocked:evidence-mismatch:post")
    with pytest.raises(SystemExit):
        block_step.announce(ctx, "evidence-mismatch", "the detail")

    monkeypatch.delenv("FACTORY_CRASH_AT", raising=False)
    block_step.announce(ctx, "evidence-mismatch", "the detail")

    assert len(ctx.linear.comments) == before + 1  # type: ignore[attr-defined]  # reconcile, no dup
    assert _comment_effect(ctx).status == "confirmed"


# --------------------------------------------------------------------------------
# F14 / F17 / F25 — the daemon's environment-failure rows
# --------------------------------------------------------------------------------


def test_f14_a_run_below_the_disk_floor_blocks(ctx: Context) -> None:
    # F14. `advance` checks free disk before every transition, so a run that hits the floor
    # mid-pipeline blocks with a named reason rather than writing a worktree it cannot
    # complete. The first advance (claim) is enough to see it.
    ctx.registry = replace(
        ctx.registry, defaults=replace(ctx.registry.defaults, disk_min_free_gb=10_000_000)
    )

    _tick(ctx)

    run = ctx.store.run_by_id(ctx.run.id)
    assert run is not None
    assert run.state is State.BLOCKED
    assert run.blocked_reason == "disk-below-floor"


def test_f17_a_linear_outage_leaves_the_run_in_place(ctx: Context) -> None:
    # F17. Linear is unreachable mid-run. The tick catches the adapter error and leaves the
    # run exactly where it was — no state advance on a failed write — so the next tick can
    # try again. A Linear outage is a pause, not a block and not a state change.
    ctx.linear.fail_with = LinearError("Linear unreachable (F17)")  # type: ignore[attr-defined]

    lines = _tick(ctx)

    run = ctx.store.run_by_id(ctx.run.id)
    assert run is not None
    assert run.state is State.APPROVED  # the claim's write never landed
    assert run.blocked_reason is None  # paused, not blocked
    assert any("adapter error" in line for line in lines)


def test_f25_a_bad_routing_table_refuses_the_tick(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F25. An invalid `models.toml` — here, the reviewer sharing the builder's model —
    # refuses the tick and names the rule. It does not fall back to a default, because a
    # factory silently running every role on one model looks exactly like one routing
    # correctly. The daemon (the plist calls `factory tick --once`) inherits this refusal.
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))

    def boom(_path: Path) -> None:
        raise RoutingError(
            "roles.reviewer.model must differ from roles.builder.model (both are 'gpt-5.6-sol')"
        )

    monkeypatch.setattr(cli, "load_routing", boom)

    rc = cli.main(["tick", "--once"])

    assert rc == 2  # refused, not fallen back


def test_suspend_refuses_a_state_the_transition_table_has_no_edge_from(ctx: Context) -> None:
    """§5.4's guards live in `advance`, and `suspend` used to write its transition with a
    bare `record_transition` — around every one of them.

    Measured live on BAC-6: the console's Suspend control was clicked on a run sitting at
    `resumable`, and `resumable -> suspended` went into the audit log even though
    `RESUMABLE`'s edge set does not contain `SUSPENDED`. The run was then unresumable —
    `resume` refused to re-enter `resumable` — so a control that should have been refused
    produced a state only `--from` could get out of.
    """
    _start_an_attempt(ctx, finish=False)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    reap_step.reap(ctx)
    assert ctx.state is State.RESUMABLE
    assert not machine.can(State.RESUMABLE, State.SUSPENDED)

    with pytest.raises(Blocked) as caught:
        recovery.suspend(ctx, reason="park it while I look")

    assert caught.value.reason == "illegal-transition"
    ctx.refresh()
    assert ctx.state is State.RESUMABLE
    assert all(row["to_state"] != str(State.SUSPENDED) for row in ctx.store.transitions(ctx.run.id))
