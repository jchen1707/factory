"""Paid workflow launch scheduling through the driver and isolated sandbox boundary."""

import time

import pytest

from factory import driver
from factory.machine import State
from factory.runtime_jobs import RuntimeJobs
from factory.steps import Context, claim, context, sandbox, worktree
from factory.store import Store
from tests.integration.test_pipeline import _fake


def test_cancellation_collects_paid_usage_and_releases_capacity_before_cleanup(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import cli

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    driver.step(ctx)
    assert len(RuntimeJobs(ctx.store).active_agents(ctx.project.name)) == 1
    monkeypatch.setattr(cli, "SbxAdapter", lambda: _fake(ctx))
    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    assert RuntimeJobs(ctx.store).active_agents(ctx.project.name) == []
    assert ctx.store.spend(ctx.run.id)[0] > 0
    ctx.refresh()
    assert ctx.state == State.CANCELLED


def test_prepared_builder_requires_its_exact_approval_after_mode_changes(ctx: Context) -> None:
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="SYN-OTHER", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    driver.step(ctx)
    identifier = ctx.store.runtime.invocations(ctx.run.id)[0]["id"]
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    jobs.finish_agent("occupied", status="completed")
    result = driver.step(ctx)
    assert result.outcome == driver.Outcome.NEEDS_HUMAN
    assert identifier in result.detail
    assert ctx.store.runtime.settings("run", ctx.run.id)["waiting_invocation"] == identifier
    assert _fake(ctx).detached == []
    ctx.store.runtime.approve(ctx.run.id, identifier)
    driver.step(ctx)
    assert len(_fake(ctx).detached) == 1
    settings = ctx.store.runtime.settings("run", ctx.run.id)
    assert settings["approved_invocation"] is None
    assert settings["waiting_invocation"] is None
    assert len(ctx.store.runtime.invocations(ctx.run.id)) == 1


def test_suspending_a_queued_builder_needs_no_process_signal(ctx: Context) -> None:
    from factory import recovery

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="SYN-OTHER", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    driver.step(ctx)
    recovery.suspend(ctx, reason="pause queued work")
    assert ctx.state == State.SUSPENDED
    assert _fake(ctx).detached == []
    assert [row["invocation_id"] for row in jobs.active_agents(ctx.project.name)] == ["occupied"]


def test_preparation_storage_failure_rolls_back_attempt_and_state(ctx: Context) -> None:
    import sqlite3

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    ctx.store.runtime.db.execute(
        "CREATE TRIGGER fail_preparation BEFORE INSERT ON effects "
        "WHEN NEW.system='agent-preparation' BEGIN SELECT RAISE(ABORT, 'storage failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
        driver.step(ctx)
    assert ctx.state == State.WORKTREE_READY
    assert ctx.run.attempt == 0
    assert ctx.store.runtime.invocations(ctx.run.id) == []
    assert _fake(ctx).detached == []


def test_queue_wait_does_not_consume_execution_timeout(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="SYN-OTHER", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    driver.step(ctx)
    later = time.time() + ctx.timeout_for(State.IMPLEMENTING) + 10
    monkeypatch.setattr(time, "time", lambda: later)
    jobs.finish_agent("occupied", status="completed")
    _fake(ctx).detach_without_finishing = True
    driver.step(ctx)
    result = driver.step(ctx)
    assert result.outcome == driver.Outcome.WAITING
    assert ctx.state == State.IMPLEMENTING


@pytest.mark.parametrize("changed", ["prompt", "candidate"])
def test_queued_launch_refuses_changed_input(ctx: Context, changed: str) -> None:
    import pytest

    from factory.machine import Blocked

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="SYN-OTHER", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    driver.step(ctx)
    if changed == "prompt":
        (ctx.factory_dir / "run/1/prompt.md").write_text("Changed while queued")
    else:
        from tests.integration.conftest import git

        (ctx.worktree / "changed.txt").write_text("Changed candidate while queued")
        git(ctx.worktree, "add", "changed.txt")
        git(ctx.worktree, "commit", "-m", "Change candidate")
    jobs.finish_agent("occupied", status="completed")
    with pytest.raises(Blocked, match="launch-preparation-stale"):
        driver.step(ctx)
    assert _fake(ctx).detached == []


def test_execution_brief_and_builder_have_independent_launch_evidence(ctx: Context) -> None:
    import json

    from factory.steps import implement, plan

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    started = plan.start(ctx)
    assert started is not None
    assert len(RuntimeJobs(ctx.store).active_agents(ctx.project.name)) == 1
    attempt = started[0]
    attempt.path("plan-exit").write_text("0")
    attempt.path("plan-events.jsonl").write_text("")
    attempt.path("plan-stderr.log").write_text("")
    attempt.path("plan-last-message.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "classification": "ready",
                "summary": "Prepared",
                "acceptance_behavior": "CRUD",
                "reproduction_evidence": "",
            }
        )
    )
    plans = plan.plan_dir(ctx)
    plans.mkdir(parents=True, exist_ok=True)
    for name in ("execution-brief.md", "test-plan.md"):
        (plans / name).write_text("Acceptance scenarios and testing boundaries.\n")
    contracts = ctx.project.path / ".agents/vendor/harness/docs/agents"
    (contracts / "consume-execution-handoff.md").write_text("Consume the verified handoff.")
    driver.step(ctx)
    assert RuntimeJobs(ctx.store).active_agents(ctx.project.name) == []
    built = implement.start(ctx)
    assert built is not None
    assert built[1].attempt_dir != started[1].attempt_dir
    assert (started[1].attempt_dir / started[1].exit_name).exists()


def test_review_axes_share_capacity_and_release_each_axis_after_collection(ctx: Context) -> None:
    from pathlib import Path

    from factory.steps import review
    from tests.integration.conftest import _seed_vendored_review_tree
    from tests.integration.test_phase3 import _to_reviewing

    _to_reviewing(ctx)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    review.start(ctx)
    jobs = RuntimeJobs(ctx.store)
    assert len(jobs.active_agents(ctx.project.name)) == 1
    while ctx.state == State.REVIEWING:
        driver.step(ctx)
    assert ctx.state == State.PR_READY
    assert jobs.active_agents(ctx.project.name) == []


def test_queued_builder_survives_controller_restart_without_a_new_attempt(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace
    from pathlib import Path

    from factory.agent import timings

    observers: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        timings, "spawn", lambda events, exit_path: observers.append((events, exit_path))
    )
    ctx.registry = replace(ctx.registry, defaults=replace(ctx.registry.defaults, timings=True))
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="SYN-CAPACITY", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    assert jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    result = driver.step(ctx)
    assert result.outcome == driver.Outcome.WAITING
    assert "agent slot" in result.detail
    assert _fake(ctx).detached == []
    invocation = ctx.store.runtime.invocations(ctx.run.id)[0]["id"]
    assert ctx.run.attempt == 1
    # The fixture owns an isolated store; reopening it replaces all controller memory.
    database = ctx.store.path
    ctx.store.close()
    ctx.store = Store(database)
    jobs = RuntimeJobs(ctx.store)
    jobs.finish_agent("occupied", status="completed")
    driver.step(ctx)
    assert len(_fake(ctx).detached) == 1
    assert ctx.run.attempt == 1
    assert [row["id"] for row in ctx.store.runtime.invocations(ctx.run.id)] == [invocation]
    driver.step(ctx)
    assert ctx.state == State.VERIFYING
    assert jobs.active_agents(ctx.project.name) == []
    assert ctx.store.spend(ctx.run.id)[0] > 0
    assert len(observers) == 1
