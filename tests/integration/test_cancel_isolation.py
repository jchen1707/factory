"""Cancellation must resolve persisted identities before any sandbox side effect."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from factory import cli
from factory.machine import State
from factory.sandbox.base import Completed
from factory.steps import Context
from tests.integration.conftest import git


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
