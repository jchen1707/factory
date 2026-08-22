"""`sandbox_ready -> worktree_ready` — the factory makes its own worktree.

`sbx --branch` does not exist in v0.38.0, so the note's shape is unavailable; and
owning the worktree is simpler anyway, because the factory needs the path and the
branch name for the PR. The worktree sits **inside** the mounted workspace, so one
mount covers the main checkout and every worktree and both sides address it by the
same absolute string.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from factory import repo
from factory.machine import Blocked, State
from factory.sandbox.sbx import worktree_inside
from factory.steps import Context, advance
from factory.steps import clone as clone_step

__all__ = ["plan_branch", "run"]


def plan_branch(ctx: Context) -> str:
    if ctx.issue is None:
        raise Blocked("no-issue-loaded", ctx.run.linear_id)
    branch_type = repo.branch_type_for_labels(list(ctx.issue.labels))
    return repo.branch_name(branch_type, ctx.issue.identifier, ctx.issue.title)


def run(ctx: Context) -> None:
    branch = plan_branch(ctx)
    if ctx.project.requires_clone:
        _run_clone(ctx, branch)
        return
    path = ctx.project.worktree_path(ctx.registry.defaults.worktree_subdir, ctx.run.linear_id)

    # The worktree has to sit inside a mounted workspace or the VM simply cannot see
    # it, and the failure would arrive as a puzzling "no such file" from the agent
    # rather than as a configuration error here.
    if not worktree_inside(ctx.project.path, path):
        raise Blocked(
            "worktree-outside-workspace",
            f"{path} is not inside the mounted workspace {ctx.project.path}",
        )

    if ctx.dry_run:
        ctx.shadow_worktree = path
        ctx.shadow_branch = branch
        for argv in repo.WorktreePlan(
            ctx.project.path, path, branch, ctx.project.base_ref
        ).commands():
            ctx.would(" ".join(argv))
        ctx.would(f"copy staged context into {path}/.factory/context/")
        advance(ctx, State.WORKTREE_READY)
        return

    repo.fetch(ctx.project.path, ctx.project.base_branch)

    # The refusal that P0-11 asked for. `origin/feat/BAC-4-application-skeleton` is the
    # live example: a complete, unmerged implementation of the ticket the factory is
    # about to start. Reusing a remote branch would put an unattended writer on top of
    # somebody else's work, and deleting it is a human's decision.
    repo.add_worktree(ctx.project.path, path, branch, ctx.project.base_ref)
    ctx.store.update_run(
        ctx.run.id, branch=branch, base_ref=ctx.project.base_ref, worktree=str(path)
    )
    ctx.refresh()

    _seed(ctx, path)
    ctx.log("worktree.created", branch=branch, path=str(path), base=ctx.project.base_ref)
    advance(ctx, State.WORKTREE_READY)


def _seed(ctx: Context, path: Path) -> None:
    """`.factory/` inside the worktree — §7.3.

    Kept out of every product diff by the **global** gitignore rather than by a commit
    to any repository, which is the same treatment the note prescribes for `.sbx/`.
    """
    factory_dir = path / ".factory"
    (factory_dir / "run").mkdir(parents=True, exist_ok=True)

    staged = ctx.state_dir / "context"
    if staged.is_dir():
        destination = factory_dir / "context"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staged, destination)

    (factory_dir / "run.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "run_id": ctx.run.id,
                "ticket": ctx.run.linear_id,
                "attempt": ctx.run.attempt,
                "branch": ctx.run.branch,
                "base_ref": ctx.run.base_ref,
                "project": ctx.project.name,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_clone(ctx: Context, branch: str) -> None:
    """The same state, reached differently: a `--clone` project has no host worktree.

    The VM runs on a private clone mounted at the project's own path, so the working tree
    the agent gets is that clone and the branch has to be cut *inside* it. `worktree` is
    still recorded, and still the path both sides use — it is simply the same string as
    the project root rather than a directory under it. `steps/clone.py` carries the why.

    The branch comes back to the host once, at the `reviewing` entry (`clone.fetch_back`),
    and everything from there on works against an ordinary host worktree.
    """
    if ctx.dry_run:
        ctx.shadow_worktree = ctx.project.path
        ctx.shadow_branch = branch
        ctx.would(
            f"sbx exec {ctx.project.build_sandbox} git -C {ctx.project.path} "
            f"checkout -b {branch} {ctx.project.base_ref}  # inside the clone"
        )
        ctx.would(f"copy staged context into {ctx.factory_dir}/context/")
        advance(ctx, State.WORKTREE_READY)
        return

    clone_step.create_branch(ctx, branch)
    ctx.store.update_run(
        ctx.run.id,
        branch=branch,
        base_ref=ctx.project.base_ref,
        worktree=str(ctx.project.path),
    )
    ctx.refresh()

    clone_step.seed_context(ctx)
    ctx.log(
        "worktree.created",
        branch=branch,
        path=str(ctx.project.path),
        base=ctx.project.base_ref,
        clone=True,
    )
    advance(ctx, State.WORKTREE_READY)
