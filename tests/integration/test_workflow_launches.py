"""Paid workflow launch scheduling through the driver and isolated sandbox boundary."""

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from factory import driver, workflow_launches
from factory.machine import Blocked, State
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import RunStatus
from factory.steps import Context, claim, context, reap, sandbox, worktree
from factory.store import Store
from tests.integration.conftest import plan_finished
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


def _prepare_rerun(ctx: Context) -> Context:
    run = ctx.store.insert_run(
        linear_id=ctx.run.linear_id, project=ctx.run.project, team=ctx.run.team
    )
    ctx.store.acquire_lease(run.id, ttl_seconds=600)
    rerun = replace(ctx, run=run)
    for step in (claim, context, sandbox, worktree):
        step.run(rerun)
    return rerun


def _cancel(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from factory import cli

    monkeypatch.setattr(cli, "SbxAdapter", lambda: _fake(ctx))
    return cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")


def test_a_cancelled_ticket_launches_a_fresh_run(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    for step in (claim, context, sandbox, worktree):
        step.run(ctx)
    driver.step(ctx)
    first_events = (ctx.factory_dir / "run/1/events.jsonl").read_bytes()
    _cancel(ctx, monkeypatch)

    rerun = _prepare_rerun(ctx)
    driver.step(rerun)

    assert rerun.state is State.IMPLEMENTING
    assert len(_fake(ctx).detached) == 2
    assert (rerun.factory_dir / "run/1/events.jsonl").is_file()
    archived = ctx.home / "artifacts" / ctx.run.linear_id / ctx.run.id / "1/events.jsonl"
    assert archived.read_bytes() == first_events


def test_a_second_cancel_keeps_the_first_cancels_archive(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    for step in (claim, context, sandbox, worktree):
        step.run(ctx)
    driver.step(ctx)
    first_events = (ctx.factory_dir / "run/1/events.jsonl").read_bytes()
    [first_archive] = [
        Path(line.split(" to ", 1)[1])
        for line in _cancel(ctx, monkeypatch)
        if line.startswith("archived 1 ")
    ]

    rerun = _prepare_rerun(ctx)
    (rerun.factory_dir / "run/1").mkdir(parents=True, exist_ok=True)
    (rerun.factory_dir / "run/1/events.jsonl").write_text('{"type":"second run"}\n')
    _cancel(rerun, monkeypatch)

    assert (first_archive / "events.jsonl").read_bytes() == first_events


def _launch_without_finishing(ctx: Context) -> str:
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    _fake(ctx).detach_without_finishing = True
    driver.step(ctx)
    [lease] = RuntimeJobs(ctx.store).active_agents(ctx.project.name)
    return str(lease["invocation_id"])


def _releases(ctx: Context, invocation: str) -> list[dict[str, object]]:
    return [
        payload
        for row in ctx.store.runtime.db.execute(
            "SELECT payload FROM operator_events WHERE action='agent-finished'"
        )
        if (payload := json.loads(row["payload"]))["invocation"] == invocation
    ]


def _orphan_a_wedged_agent(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> str:
    from factory import cli

    invocation = _launch_without_finishing(ctx)
    fake = _fake(ctx)
    fake.poll_status = RunStatus.ORPHANED
    fake.kill_writes_exit = False
    reap.reap(ctx)
    assert ctx.state is State.RESUMABLE
    assert [
        lease["invocation_id"] for lease in RuntimeJobs(ctx.store).active_agents(ctx.project.name)
    ] == [invocation]
    monkeypatch.setattr(cli, "SbxAdapter", lambda: fake)
    return invocation


def test_cancel_keeps_an_orphaned_lease_while_its_sandbox_runs(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import cli

    invocation = _orphan_a_wedged_agent(ctx, monkeypatch)

    with pytest.raises(Blocked) as refused:
        cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")

    assert refused.value.reason == "cancellation-stop-unverified"
    assert len(RuntimeJobs(ctx.store).active_agents(ctx.project.name)) == 1
    assert _releases(ctx, invocation) == []


def test_cancel_releases_an_orphaned_lease_once_its_sandbox_is_removed(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import cli

    invocation = _orphan_a_wedged_agent(ctx, monkeypatch)
    _fake(ctx).remove(ctx.project.build_sandbox)

    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    workflow_launches.reconcile_run(ctx.store, _fake(ctx), ctx.run.id, ctx.project.name)

    ctx.refresh()
    assert ctx.state is State.CANCELLED
    assert RuntimeJobs(ctx.store).active_agents(ctx.project.name) == []
    assert _releases(ctx, invocation) == [
        {"invocation": invocation, "status": "failed", "evidence": "sandbox-absent"}
    ]


def test_reaping_an_agent_whose_sandbox_was_removed_frees_its_slot(ctx: Context) -> None:
    invocation = _launch_without_finishing(ctx)
    _fake(ctx).remove(ctx.project.build_sandbox)
    _fake(ctx).poll_status = RunStatus.ORPHANED

    reap.reap(ctx)

    assert ctx.state is State.RESUMABLE
    assert RuntimeJobs(ctx.store).active_agents(ctx.project.name) == []
    assert _releases(ctx, invocation) == [
        {"invocation": invocation, "status": "failed", "evidence": "sandbox-absent"}
    ]


def test_cancel_ends_an_attempt_whose_sandbox_stopped_under_it(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import cli

    invocation = _launch_without_finishing(ctx)
    state = ctx.state
    fake = _fake(ctx)
    fake.stop_sandbox(ctx.project.build_sandbox)
    fake.poll_status = RunStatus.ORPHANED
    monkeypatch.setattr(reap, "KILL_GRACE_SECONDS", 0)
    monkeypatch.setattr(cli, "SbxAdapter", lambda: fake)

    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")

    ctx.refresh()
    assert ctx.state is State.CANCELLED
    attempt = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, state)
    assert attempt is not None
    assert (attempt["exit_code"], attempt["outcome"]) == (None, "orphaned")
    assert RuntimeJobs(ctx.store).active_agents(ctx.project.name) == []
    assert _releases(ctx, invocation) == [
        {"invocation": invocation, "status": "failed", "evidence": "sandbox-stopped"}
    ]


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
    jobs.finish_agent("occupied", status="completed", evidence="exit")
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
    jobs.finish_agent("occupied", status="completed", evidence="exit")
    _fake(ctx).detach_without_finishing = True
    driver.step(ctx)
    result = driver.step(ctx)
    assert result.outcome == driver.Outcome.WAITING
    assert ctx.state == State.IMPLEMENTING


@pytest.mark.parametrize("changed", ["prompt", "candidate"])
def test_queued_launch_refuses_changed_input(ctx: Context, changed: str) -> None:
    import pytest

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
    jobs.finish_agent("occupied", status="completed", evidence="exit")
    with pytest.raises(Blocked, match="launch-preparation-stale"):
        driver.step(ctx)
    assert _fake(ctx).detached == []


def test_execution_brief_and_builder_have_independent_launch_evidence(ctx: Context) -> None:

    from factory.steps import implement, plan

    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    started = plan.start(ctx)
    assert started is not None
    assert len(RuntimeJobs(ctx.store).active_agents(ctx.project.name)) == 1
    attempt = started[0]
    plan_finished(
        ctx,
        attempt,
        {
            "status": "ready",
            "classification": "ready",
            "summary": "Prepared",
            "acceptance_behavior": "CRUD",
            "reproduction_evidence": "",
        },
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


def test_queued_builder_survives_controller_restart_without_a_new_attempt(ctx: Context) -> None:
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
    jobs.finish_agent("occupied", status="completed", evidence="exit")
    driver.step(ctx)
    assert len(_fake(ctx).detached) == 1
    assert ctx.run.attempt == 1
    assert [row["id"] for row in ctx.store.runtime.invocations(ctx.run.id)] == [invocation]
    driver.step(ctx)
    assert ctx.state == State.VERIFYING
    assert jobs.active_agents(ctx.project.name) == []
    assert ctx.store.spend(ctx.run.id)[0] > 0
