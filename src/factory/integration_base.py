"""Prevent isolated parallel work from delivering against a superseded integration base."""

from __future__ import annotations

from typing import TYPE_CHECKING

from factory import repo
from factory.machine import Blocked

if TYPE_CHECKING:
    from factory.steps import Context


def before_verification(ctx: Context) -> None:
    settings = ctx.store.runtime.settings("run", ctx.run.id)
    if settings.get("isolation") != "per-run":
        return
    with repo.serialized_git(ctx.project.path):
        repo.fetch(ctx.project.path, ctx.project.base_branch)
        current = repo.head_sha(ctx.project.path, ctx.project.base_ref)
    result = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        ["git", "merge-base", "--is-ancestor", current, "HEAD"],
        workdir=str(ctx.worktree),
        env=ctx.env,
        timeout=30,
    )
    if not result.ok:
        raise Blocked(
            "integration-base-refresh-required",
            f"Preserved branch must incorporate integration base {current} before verification",
        )
    ctx.store.runtime.configure("run", ctx.run.id, {"verification_base": current})


def before_delivery(ctx: Context) -> None:
    settings = ctx.store.runtime.settings("run", ctx.run.id)
    if settings.get("isolation") != "per-run":
        return
    with repo.serialized_git(ctx.project.path):
        repo.fetch(ctx.project.path, ctx.project.base_branch)
        current = repo.head_sha(ctx.project.path, ctx.project.base_ref)
    if settings.get("verification_base") != current:
        raise Blocked(
            "integration-evidence-stale",
            "The integration base changed; refresh the preserved branch and repeat verification and review",
        )
