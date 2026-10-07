"""Factory collection hands retained evidence to layer A without a model in tests."""

from pathlib import Path

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


def test_a_stream_replaced_with_a_link_is_never_handed_to_the_distiller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import learning

    project = tmp_path / "project"
    script = project / ".agents/vendor/harness/hooks/session_learnings.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("")
    host_file = tmp_path / "outside-every-mount"
    host_file.write_text("private\n")
    launch = tmp_path / "scratch" / "1-review-1"
    launch.mkdir(parents=True)
    (launch / "events.jsonl").symlink_to(host_file)
    monkeypatch.setattr(
        learning.subprocess, "Popen", lambda *a, **k: pytest.fail("a host file reached layer A")
    )

    learning.schedule(project, tmp_path / "vault", launch / "events.jsonl")

    assert "unavailable:transcript" in (launch / "events.learning.json").read_text()


def test_a_launch_directory_swapped_for_a_link_gets_nothing_written_through_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import learning

    project = tmp_path / "project"
    script = project / ".agents/vendor/harness/hooks/session_learnings.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("")
    victim = tmp_path / "another-runs-launch"
    victim.mkdir()
    (victim / "events.jsonl").write_text("{}\n")
    launch = tmp_path / "scratch" / "1-review-1"
    launch.parent.mkdir()
    launch.symlink_to(victim)
    monkeypatch.setattr(
        learning.subprocess, "Popen", lambda *a, **k: pytest.fail("distilled through a link")
    )

    learning.schedule(project, tmp_path / "vault", launch / "events.jsonl")

    assert sorted(p.name for p in victim.iterdir()) == ["events.jsonl"]
