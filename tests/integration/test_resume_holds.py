"""Operator resume reports scheduling holds without changing the parked run."""

import pytest

from factory import cli, recovery
from factory.machine import State
from factory.steps import Context
from tests.integration.test_phase4 import _fake, _start_an_attempt


def hold(ctx: Context, reason: str) -> None:
    if reason == "approval":
        ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    else:
        ctx.store.runtime.configure("run", ctx.run.id, {"concurrency": 1})
        other = ctx.store.insert_run(linear_id="BAC-99", project=ctx.project.name, team="BAC")
        ctx.store.record_transition(
            other.id, from_state=None, to_state=State.IMPLEMENTING, actor="auto"
        )


@pytest.mark.parametrize("reason", ["approval", "queue"])
def test_cli_resume_reports_hold_without_launch_or_tracker_write(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reason: str,
) -> None:
    _start_an_attempt(ctx, finish=False)
    recovery.suspend(ctx, reason="operator pause")
    hold(ctx, reason)
    before = ctx.store.transitions(ctx.run.id)
    invocations = ctx.store.runtime.invocations(ctx.run.id)
    effects = ctx.store.effects(ctx.run.id)
    launches = len(_fake(ctx).detached)
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "load_routing", lambda path: ctx.routing)
    monkeypatch.setattr(cli, "_open_store", lambda home: ctx.store)
    monkeypatch.setattr(cli, "LinearClient", lambda: ctx.linear)
    monkeypatch.setattr(cli, "_context_for", lambda *args: ctx)

    assert cli.main(["resume", ctx.run.linear_id]) == 0
    output = capsys.readouterr().out
    if reason == "approval":
        assert "waiting for approval" in output
        assert "2:implement:1" in output
    else:
        assert "queued" in output
        assert "waiting for a project slot" in output
    ctx.refresh()
    assert ctx.state is State.SUSPENDED
    assert ctx.run.attempt == 1
    assert len(_fake(ctx).detached) == launches
    assert ctx.store.transitions(ctx.run.id) == before
    assert ctx.store.runtime.invocations(ctx.run.id) == invocations
    assert ctx.store.effects(ctx.run.id) == effects
    assert not ctx.store.holds_lease(ctx.run.id)


@pytest.mark.parametrize("reason", ["approval", "queue"])
@pytest.mark.parametrize("action", ["resume", "resume-planning"])
def test_console_resume_reports_hold_without_launch_or_tracker_write(
    ctx: Context,
    reason: str,
    action: str,
) -> None:
    _start_an_attempt(ctx, finish=False)
    recovery.suspend(ctx, reason="operator pause")
    hold(ctx, reason)
    before = ctx.store.transitions(ctx.run.id)
    effects = ctx.store.effects(ctx.run.id)
    launches = len(_fake(ctx).detached)
    code, message = cli.dispatch_control(
        action,
        ctx.home,
        ctx.registry,
        ctx.routing,
        ctx.store,
        ctx.linear,
        ctx.run,
        context_factory=lambda run: ctx,
    )
    assert code == 0
    assert ("waiting for approval" if reason == "approval" else "queued") in message
    ctx.refresh()
    assert ctx.state is State.SUSPENDED
    assert ctx.run.attempt == 1
    assert len(_fake(ctx).detached) == launches
    assert ctx.store.transitions(ctx.run.id) == before
    assert ctx.store.effects(ctx.run.id) == effects
    assert not ctx.store.holds_lease(ctx.run.id)
