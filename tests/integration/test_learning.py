"""Factory collection hands retained evidence to layer A without a model in tests."""

from collections.abc import Mapping

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


def test_orphan_retains_native_before_dispatch_without_exit(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from factory.sandbox import native_transcript
    from factory.sandbox.base import RunStatus
    from factory.steps import reap
    from tests.integration.test_phase4 import _fake, _start_an_attempt

    def capture(sandbox: object, name: str, session: str, *, env: Mapping[str, str]) -> str:
        return json.dumps({"type": "session_meta", "payload": {"id": session}}) + "\n"

    monkeypatch.setattr(native_transcript, "capture", capture)
    _start_an_attempt(ctx, finish=False)
    _fake(ctx).poll_status = RunStatus.ORPHANED
    assert reap.reap(ctx).outcome is reap.Outcome.ORPHANED
    retained = list(ctx.factory_dir.rglob("*.native.jsonl"))
    assert retained
    receipt = json.loads(retained[0].with_suffix("").with_suffix(".native.json").read_text())
    assert receipt["outcome"] == "retained"


def test_cancellation_exports_native_before_archiving(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from factory import cli, driver
    from factory.sandbox import native_transcript
    from factory.steps import claim, context, sandbox, worktree
    from tests.integration.test_pipeline import _fake

    def capture(sandbox: object, name: str, session: str, *, env: Mapping[str, str]) -> str:
        return json.dumps({"type": "session_meta", "payload": {"id": session}}) + "\n"

    monkeypatch.setattr(native_transcript, "capture", capture)
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    driver.step(ctx)
    monkeypatch.setattr(cli, "SbxAdapter", lambda: _fake(ctx))
    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    assert list((ctx.home / "artifacts").rglob("*.native.jsonl"))
