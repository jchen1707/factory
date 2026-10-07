"""Every measured stream outcome, driven through the builder step to its verdict.

`tests/unit/test_agent_run.py` pins the table; this proves the steps consult it. Each
scenario is the shape Claude Code 2.1.292 wrote in `tests/fixtures/claude/`, including the
two that end `subtype: success` with `is_error: true`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory import recovery
from factory.machine import Blocked, Resumable, State
from factory.recovery import Disposition
from factory.steps import Context, record_stop
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import plan as plan_step
from factory.steps import reap as reap_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import KILLED, FakeSandbox, advance_state
from tests.integration.test_phase4 import _expire_the_backoff


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _to_worktree(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)


@pytest.mark.parametrize(
    ("outcome", "stop", "reason"),
    [
        ("not_logged_in", Blocked, "agent-auth"),  # subtype success, is_error true
        ("model_unavailable", Blocked, "model-unavailable"),  # subtype success, is_error, 404
        ("budget", Blocked, "budget-exceeded"),
        ("session_in_use", Blocked, "session-in-use"),  # no stream at all, stderr only
        ("max_turns", Resumable, "max-turns"),
        ("session_lost", Resumable, "session-lost"),
    ],
)
def test_the_builder_verdict_follows_the_stream(
    ctx: Context, outcome: str, stop: type[Exception], reason: str
) -> None:
    _fake(ctx).outcome = outcome
    _to_worktree(ctx)

    with pytest.raises(stop) as caught:
        advance_state(ctx, until=State.VERIFYING)

    assert caught.value.reason == reason  # type: ignore[attr-defined]
    row = ctx.store.attempt_row(ctx.run.id, 1, State.IMPLEMENTING)
    assert row is not None
    assert row["outcome"] == reason
    assert ctx.state is State.IMPLEMENTING  # the stop is recorded by the driver, not here


def test_a_lost_session_restarts_with_a_fresh_one(ctx: Context) -> None:
    # `--resume` into a sandbox that no longer holds the transcript ends with one error
    # event and no `init`, so the next attempt must not try the same id again.
    _fake(ctx).outcome = "session_lost"
    _to_worktree(ctx)
    with pytest.raises(Resumable, match="session-lost"):
        advance_state(ctx, until=State.VERIFYING)
    lost = _fake(ctx).launches[-1]
    assert lost is not None
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.IMPLEMENTING,
        to_state=State.RESUMABLE,
        actor="auto",
        rule="session-lost",
    )
    ctx.refresh()
    _fake(ctx).outcome = "success"
    _expire_the_backoff(ctx)

    verdict = recovery.resume_run(ctx)

    assert verdict.disposition is Disposition.RESTART
    assert verdict.reason == "no-session-id"
    fresh = _fake(ctx).launches[-1]
    assert fresh is not None
    assert not fresh.resume
    assert fresh.session != lost.session


def test_a_torn_stream_with_a_recorded_exit_is_resumable(ctx: Context) -> None:
    # The wrapper was killed mid-write: the last line is torn and there is no result
    # event, but the wrapper still landed its exit code.
    _fake(ctx).outcome = "truncated"
    _fake(ctx).exit_code = KILLED
    _to_worktree(ctx)

    with pytest.raises(Resumable) as caught:
        advance_state(ctx, until=State.VERIFYING)

    assert caught.value.reason == "transcript-truncated"
    assert f"exit {KILLED}" in caught.value.detail


def test_a_refused_tool_is_recorded_and_the_run_still_needs_its_answer(ctx: Context) -> None:
    # A denied Write ends `success` with `permission_denials` and no structured answer:
    # the refusal is evidence (a check row), and the missing answer is what blocks.
    _fake(ctx).outcome = "denial"
    _to_worktree(ctx)

    with pytest.raises(Blocked) as caught:
        advance_state(ctx, until=State.VERIFYING)

    assert caught.value.reason == "schema-invalid"
    checks = {row["check_name"]: row for row in ctx.store.checks(ctx.run.id)}
    assert checks["tool_denials"]["status"] == "fail"
    assert "Write" in checks["tool_denials"]["detail"]
    assert checks["isolation_attestation"]["status"] == "pass"


def test_reap_stops_an_agent_whose_credential_keeps_failing(ctx: Context) -> None:
    # The CLI retries a 401 for minutes. The stream shows it (`api_retry` events, no
    # result), so the reaper signals the group instead of waiting out the state timeout,
    # and the collected attempt blocks as the auth failure it is.
    _fake(ctx).outcome = "auth_retrying"
    _to_worktree(ctx)
    advance_state(ctx, until=State.IMPLEMENTING)
    attempt_dir = Path(_fake(ctx).detached_dirs[-1])
    assert not (attempt_dir / "exit").exists()

    with pytest.raises(Blocked) as caught:
        reap_step.reap(ctx)

    assert caught.value.reason == "agent-auth"
    assert (attempt_dir / "exit").read_text() == str(KILLED)  # signalled, not timed out


def test_a_stream_from_another_session_fails_attestation(ctx: Context) -> None:
    # The fake echoes the launch by default; a stream whose `init` names another session
    # (or model, or an extra tool) is not evidence of this run and blocks before its
    # result is read.
    _to_worktree(ctx)
    _fake(ctx).detach_without_finishing = True
    advance_state(ctx, until=State.IMPLEMENTING)
    launch = _fake(ctx).launches[-1]
    assert launch is not None
    from tests.support import claude_stream

    foreign = claude_stream.success(
        session="00000000-0000-4000-8000-000000000000", model=launch.model, tools=launch.tools
    )
    launch.events.write_text("".join(f"{line}\n" for line in foreign.lines))
    (launch.events.parent / "exit").write_text("0")

    with pytest.raises(Blocked) as caught:
        reap_step.reap(ctx)

    assert caught.value.reason == "isolation-attestation-failed"
    assert "session" in caught.value.detail


def test_a_dead_planner_re_runs_the_planner_never_the_builder(ctx: Context) -> None:
    # Before the cutover a planner stream failure blocked; now it goes to the ladder, and
    # the ladder must re-run `plan.start`, not `implement.start` with the planner's
    # session: the builder would open the planner's conversation with no plan collected.
    _fake(ctx).outcome = "max_turns"
    _to_worktree(ctx)
    assert plan_step.start(ctx) is not None
    dead = _fake(ctx).launches[-1]
    assert dead is not None
    with pytest.raises(Resumable) as caught:
        reap_step.reap(ctx)
    assert caught.value.reason == "max-turns"
    record_stop(ctx, State.RESUMABLE, rule=caught.value.reason, detail=caught.value.detail)
    _fake(ctx).outcome = "success"
    _expire_the_backoff(ctx)

    verdict = recovery.resume_run(ctx)

    assert verdict.reason == "rerun-planning"
    assert ctx.state is State.PLANNING
    rerun = _fake(ctx).launches[-1]
    assert rerun is not None
    assert not rerun.resume
    assert rerun.session != dead.session
    assert "classification" in rerun.schema["required"]  # the handoff contract, not the builder's


def test_reap_stops_a_planner_whose_credential_keeps_failing(ctx: Context) -> None:
    # The plan phase writes `plan-exit`; the kill must wait on that file, or a blocked
    # credential turns into a 60-second stall and a resumable orphan.
    _fake(ctx).outcome = "auth_retrying"
    _to_worktree(ctx)
    started = plan_step.start(ctx)
    assert started is not None
    attempt_dir = started[0]

    with pytest.raises(Blocked) as caught:
        reap_step.reap(ctx)

    assert caught.value.reason == "agent-auth"
    assert attempt_dir.path("plan-exit").read_text() == str(KILLED)
    row = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.PLANNING)
    assert row is not None
    assert row["outcome"] == "agent-auth"
