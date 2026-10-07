"""Factory collection hands retained evidence to layer A without a model in tests."""

import pytest

from factory.steps import Context
from tests.integration.test_pipeline import _to_verifying


def test_completed_attempt_records_learning_capture_outcome(ctx: Context) -> None:
    _to_verifying(ctx)
    outcomes = list(ctx.factory_dir.rglob("*.learning.json"))
    assert outcomes, "completed factory attempt has no observable learning handoff"
    assert "unavailable:script" in outcomes[0].read_text()


def test_orphaned_attempt_schedules_learning_capture(ctx: Context) -> None:
    from factory.sandbox.base import RunStatus
    from factory.steps import reap
    from tests.integration.test_phase4 import _fake, _start_an_attempt

    _start_an_attempt(ctx, finish=False)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    verdict = reap.reap(ctx)
    assert verdict.outcome is reap.Outcome.ORPHANED
    assert list(ctx.factory_dir.rglob("*.learning.json"))


def test_invocation_without_events_does_not_break_collection(ctx: Context) -> None:
    from factory import learning

    learning.collect_invocation(ctx, {"id": "probe", "metadata": {}})


def test_cancellation_hands_off_archived_events(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import cli, driver
    from factory.steps import claim, context, sandbox, worktree
    from tests.integration.test_pipeline import _fake

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    driver.step(ctx)
    monkeypatch.setattr(cli, "SbxAdapter", lambda: _fake(ctx))
    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    assert list((ctx.home / "artifacts").rglob("*.learning.json"))
