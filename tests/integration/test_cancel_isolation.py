"""Cancellation must resolve persisted identities before any sandbox side effect."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from factory import cli, recovery
from factory.machine import Blocked, State
from factory.sandbox.base import Completed
from factory.steps import Context
from tests.integration.conftest import advance_state, git


@pytest.mark.parametrize("clone", [False, True], ids=["bind", "clone"])
@pytest.mark.parametrize("per_run", [False, True], ids=["legacy", "per-run"])
def test_cancel_targets_only_the_recorded_build_sandbox(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, clone: bool, per_run: bool
) -> None:
    project = replace(ctx.project, requires_clone=clone)
    registry = replace(ctx.registry, projects={project.name: project})
    sibling = ctx.store.insert_run(
        linear_id="BAC-99", project=project.name, team=ctx.run.team, state=State.IMPLEMENTING
    )
    own_build = project.build_sandbox
    sibling_build = f"{project.build_sandbox}-{sibling.id}"
    if per_run:
        own_build = f"{project.build_sandbox}-{ctx.run.id}"
        ctx.store.runtime.configure(
            "run",
            ctx.run.id,
            {
                "isolation": "per-run",
                "build_sandbox": own_build,
                "review_sandbox": f"{project.review_sandbox}-{ctx.run.id}",
            },
        )
    ctx.store.runtime.configure(
        "run",
        sibling.id,
        {
            "isolation": "per-run",
            "build_sandbox": sibling_build,
            "review_sandbox": f"{project.review_sandbox}-{sibling.id}",
        },
    )
    branch = "feat/BAC-4-cancel-identity"
    git(project.path, "branch", branch, project.base_ref)
    ctx.store.update_run(ctx.run.id, branch=branch)
    ctx.refresh()
    sibling_work = project.worktree_path(registry.defaults.worktree_subdir, "BAC-99")
    sibling_work.mkdir(parents=True)
    dirty = sibling_work / "dirty.txt"
    dirty.write_text("unfinished sibling work")
    stopped: list[str] = []
    queried: list[str] = []

    class SandboxEffects:
        def exec_sync(self, name: str, argv: Sequence[str], **kwargs: Any) -> Completed:
            queried.append(name)
            # A missing clone branch is an ordinary, nonfatal cancellation case.
            return Completed(tuple(argv), 1, "", "branch absent")

        def stop(self, name: str) -> None:
            stopped.append(name)

    monkeypatch.setattr(cli, "SbxAdapter", SandboxEffects)
    lines = cli._cancel_run(ctx.home, registry, ctx.store, ctx.linear, ctx.run, "test")

    assert stopped == [own_build]
    assert queried == ([own_build] if clone else [])
    assert sibling_build not in stopped + queried
    if per_run:
        assert project.build_sandbox not in stopped + queried
    assert dirty.read_text() == "unfinished sibling work"
    retained_sibling = ctx.store.run_by_id(sibling.id)
    assert retained_sibling is not None
    assert retained_sibling.state is State.IMPLEMENTING
    cancelled = ctx.store.run_by_id(ctx.run.id)
    assert cancelled is not None
    assert cancelled.state is State.CANCELLED
    assert any(f"sandbox {own_build} stopped" in line for line in lines)


@pytest.mark.parametrize("state", [State.IMPLEMENTING, State.REVIEWING])
@pytest.mark.parametrize("shared", [False, True], ids=["isolated", "shared"])
def test_cancel_stops_recorded_attempt_before_cleanup_and_preserves_busy_sandbox(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, state: State, shared: bool
) -> None:
    own = ctx.project.review_sandbox if state is State.REVIEWING else ctx.project.build_sandbox
    if not shared:
        own += "-recorded"
    advance_state(ctx, until=state)
    attempt = ctx.home / "attempt"
    attempt.mkdir()
    (attempt / "pgid").write_text("321")
    ctx.store.start_attempt(ctx.run.id, 1, state, sandbox=own, artifact_dir=str(attempt))
    ctx.refresh()
    sibling = ctx.store.insert_run(
        linear_id="BAC-99", project=ctx.project.name, team=ctx.run.team, state=state
    )
    calls: list[tuple[str, object]] = []

    class SandboxEffects:
        def kill_group(self, name: str, pgid: int) -> None:
            calls.append(("signal", (name, pgid)))
            (attempt / "exit").write_text("143")

        def stop(self, name: str) -> None:
            calls.append(("stop", name))

    def cleanup(*args: Any) -> list[str]:
        assert (attempt / "exit").read_text() == "143"
        calls.append(("cleanup", None))
        return []

    monkeypatch.setattr(cli, "SbxAdapter", SandboxEffects)
    monkeypatch.setattr(cli, "_release_local_debris", cleanup)
    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    assert calls[0] == ("signal", (own, 321))
    assert ("stop", own) not in calls if shared else ("stop", own) in calls
    assert ("stop", ctx.project.build_sandbox) not in calls
    row = ctx.store.attempt_row(ctx.run.id, 1, state)
    assert row is not None
    assert row["exit_code"] == 143
    assert row["outcome"] == "cancelled"
    surviving = ctx.store.run_by_id(sibling.id)
    assert surviving is not None
    assert surviving.state is state


@pytest.mark.parametrize("missing_pgid", [False, True], ids=["no-exit", "no-pgid"])
def test_cancel_refuses_cleanup_when_attempt_stop_is_unproven(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, missing_pgid: bool
) -> None:
    state = State.IMPLEMENTING
    advance_state(ctx, until=state)
    ctx.store.insert_run(
        linear_id="BAC-99", project=ctx.project.name, team=ctx.run.team, state=state
    )
    attempt = ctx.home / "attempt"
    attempt.mkdir()
    if not missing_pgid:
        (attempt / "pgid").write_text("321")
    ctx.store.start_attempt(
        ctx.run.id, 1, state, sandbox=ctx.project.build_sandbox, artifact_dir=str(attempt)
    )
    ctx.refresh()

    class SandboxEffects:
        def kill_group(self, name: str, pgid: int) -> None:
            pass  # Signal delivery alone does not prove that the writer stopped.

        def stop(self, name: str) -> None:
            pytest.fail("must not stop the sibling sandbox")

    monkeypatch.setattr(cli, "SbxAdapter", SandboxEffects)
    monkeypatch.setattr(cli, "_release_local_debris", lambda *args: pytest.fail("unsafe cleanup"))
    monkeypatch.setattr(cli.recovery, "_wait_for_exit_file", lambda *args, **kwargs: None)
    with pytest.raises(Blocked, match=r"targeted-signal-unavailable|cancellation-stop-unverified"):
        cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    retained = ctx.store.run_by_id(ctx.run.id)
    assert retained is not None
    assert retained.state is state


def test_cancel_keeps_shared_clone_checkout_while_a_sibling_uses_it(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = replace(ctx.project, requires_clone=True)
    registry = replace(ctx.registry, projects={project.name: project})
    ctx.store.insert_run(
        linear_id="BAC-99", project=project.name, team=ctx.run.team, state=State.IMPLEMENTING
    )
    monkeypatch.setattr(cli, "SbxAdapter", lambda: object())
    monkeypatch.setattr(
        cli.clone_step, "release_branch", lambda *args: pytest.fail("would change sibling checkout")
    )
    lines = cli._cancel_run(ctx.home, registry, ctx.store, ctx.linear, ctx.run, "test")
    assert any("left clone branch" in line for line in lines)
    assert any("kept sandbox" in line for line in lines)


def test_cancel_does_not_signal_an_already_terminal_attempt(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    advance_state(ctx, until=State.REVIEWING)
    attempt = ctx.home / "attempt"
    attempt.mkdir()
    (attempt / "pgid").write_text("321")
    (attempt / "exit").write_text("0")
    ctx.store.start_attempt(
        ctx.run.id,
        1,
        State.REVIEWING,
        sandbox=ctx.project.review_sandbox,
        artifact_dir=str(attempt),
    )
    ctx.refresh()
    stops: list[str] = []

    class SandboxEffects:
        def kill_group(self, *args: Any) -> None:
            pytest.fail("a completed attempt's PGID may have been reused")

        def stop(self, name: str) -> None:
            stops.append(name)

    monkeypatch.setattr(cli, "SbxAdapter", SandboxEffects)
    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "test")
    assert stops == [ctx.project.build_sandbox, ctx.project.review_sandbox]


@pytest.mark.parametrize("plan_finished", [False, True])
def test_cancel_uses_planners_terminal_record(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, plan_finished: bool
) -> None:
    # A previous implementation exit in the same attempt directory is not plan completion.
    run = ctx.store.insert_run(
        linear_id="BAC-98", project=ctx.project.name, team=ctx.run.team, state=State.PLANNING
    )
    attempt = ctx.home / "planner"
    attempt.mkdir()
    (attempt / "exit").write_text("0")
    (attempt / "plan-pgid").write_text("321")
    if plan_finished:
        (attempt / "plan-exit").write_text("143")
    ctx.store.start_attempt(
        run.id, 1, State.PLANNING, sandbox=ctx.project.build_sandbox, artifact_dir=str(attempt)
    )
    refreshed = ctx.store.run_by_id(run.id)
    assert refreshed is not None
    run = refreshed
    signalled = []

    class SandboxEffects:
        def kill_group(self, name: str, pgid: int) -> None:
            signalled.append(pgid)
            (attempt / "plan-exit").write_text("143")

        def stop(self, name: str) -> None:
            pass

    monkeypatch.setattr(cli, "SbxAdapter", SandboxEffects)
    cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, run, "test")
    assert signalled == ([] if plan_finished else [321])
    row = ctx.store.attempt_row(run.id, 1, State.PLANNING)
    assert row is not None
    assert row["exit_code"] == 143


@pytest.mark.parametrize("missing_pgid", [False, True])
def test_cancel_protects_another_project_using_the_same_physical_vm(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, missing_pgid: bool
) -> None:
    other = replace(ctx.project, name="another-project", team="OTHER")
    registry = replace(ctx.registry, projects={ctx.project.name: ctx.project, other.name: other})
    ctx.store.insert_run(
        linear_id="OTHER-1", project=other.name, team=other.team, state=State.IMPLEMENTING
    )
    advance_state(ctx, until=State.IMPLEMENTING)
    attempt = ctx.home / "active"
    attempt.mkdir()
    if not missing_pgid:
        (attempt / "pgid").write_text("321")
    ctx.store.start_attempt(
        ctx.run.id,
        1,
        State.IMPLEMENTING,
        sandbox=ctx.project.build_sandbox,
        artifact_dir=str(attempt),
    )
    ctx.refresh()

    class SandboxEffects:
        def kill_group(self, name: str, pgid: int) -> None:
            (attempt / "exit").write_text("143")

        def kill_agent(self, *args: Any) -> None:
            pytest.fail("unscoped signal could kill the other project's worker")

        def stop(self, name: str) -> None:
            pytest.fail("the other project still uses this VM")

    monkeypatch.setattr(cli, "SbxAdapter", SandboxEffects)
    if missing_pgid:
        with pytest.raises(Blocked, match="targeted-signal-unavailable"):
            cli._cancel_run(ctx.home, registry, ctx.store, ctx.linear, ctx.run, "test")
    else:
        cli._cancel_run(ctx.home, registry, ctx.store, ctx.linear, ctx.run, "test")


@pytest.mark.parametrize("missing_pgid", [False, True])
def test_suspend_planner_uses_plan_exit_and_preserves_other_project_vm(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, missing_pgid: bool
) -> None:
    other = replace(ctx.project, name="other-project", team="OTHER")
    ctx.registry = replace(
        ctx.registry, projects={ctx.project.name: ctx.project, other.name: other}
    )
    ctx.store.insert_run(
        linear_id="OTHER-1", project=other.name, team=other.team, state=State.IMPLEMENTING
    )
    ctx.run = ctx.store.insert_run(
        linear_id="BAC-98", project=ctx.project.name, team=ctx.run.team, state=State.PLANNING
    )
    assert ctx.store.acquire_lease(ctx.run.id, ttl_seconds=300)
    attempt = ctx.home / "planner"
    attempt.mkdir()
    if not missing_pgid:
        (attempt / "plan-pgid").write_text("321")
    (attempt / "exit").write_text("0")
    ctx.store.start_attempt(
        ctx.run.id, 1, State.PLANNING, sandbox=ctx.project.build_sandbox, artifact_dir=str(attempt)
    )
    ctx.refresh()

    def stop_group(name: str, pgid: int) -> None:
        assert pgid == 321
        (attempt / "plan-exit").write_text("143")

    monkeypatch.setattr(ctx.sandbox, "kill_group", stop_group)
    monkeypatch.setattr(ctx.sandbox, "stop", lambda *args: pytest.fail("other project is active"))
    if missing_pgid:
        monkeypatch.setattr(ctx.sandbox, "kill_agent", lambda *args: pytest.fail("unsafe fallback"))
        with pytest.raises(Blocked, match="targeted-signal-unavailable"):
            recovery.suspend(ctx, reason="test")
        assert ctx.state is State.PLANNING
        return
    recovery.suspend(ctx, reason="test")
    row = ctx.store.attempt_row(ctx.run.id, 1, State.PLANNING)
    assert row is not None
    assert row["exit_code"] == 143
    assert ctx.state is State.SUSPENDED


@pytest.mark.parametrize("missing_directory", [False, True])
def test_suspend_refuses_to_park_without_a_terminal_record(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, missing_directory: bool
) -> None:
    advance_state(ctx, until=State.IMPLEMENTING)
    attempt = ctx.home / "unfinished"
    attempt.mkdir()
    (attempt / "pgid").write_text("321")
    ctx.store.start_attempt(
        ctx.run.id,
        1,
        State.IMPLEMENTING,
        sandbox=ctx.project.build_sandbox,
        artifact_dir="" if missing_directory else str(attempt),
    )
    ctx.refresh()
    monkeypatch.setattr(ctx.sandbox, "kill_group", lambda *args: None)
    monkeypatch.setattr(recovery, "_wait_for_exit_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(ctx.sandbox, "stop", lambda *args: pytest.fail("stop is unproven"))
    with pytest.raises(Blocked, match="suspend-stop-unverified"):
        recovery.suspend(ctx, reason="test")
    assert ctx.state is State.IMPLEMENTING
    row = ctx.store.attempt_row(ctx.run.id, 1, State.IMPLEMENTING)
    assert row is not None
    assert row["ended_at"] is None
