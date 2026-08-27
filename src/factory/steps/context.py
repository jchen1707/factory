"""`claimed -> context_loaded` — the ticket, the spec, the breakdown and the thread.

Written as **files** rather than fetched by the agent through an MCP round-trip. That
is what lets the build sandbox carry no Linear credential at all (§13.1): there is
nothing in the VM to leak and nothing to misuse, and the agent still sees more than a
tool call would have given it.

They are staged under `state/runs/<id>/context/` because `worktree_ready` is three
states away; `worktree.py` copies them into `.factory/` when there is a worktree.
"""

from __future__ import annotations

from factory.harness import cross_check_stack, load_harness_config
from factory.intake.linear import write_context
from factory.machine import Blocked, State
from factory.steps import Context, advance

__all__ = ["run"]


def run(ctx: Context) -> None:
    if ctx.issue is None:
        raise Blocked("no-issue-loaded", ctx.run.linear_id)

    # §10.2 steps 4 to 6, performed here rather than at claim time so a repository that
    # changed under the factory is caught before a sandbox is created for it.
    harness = load_harness_config(ctx.project.path)
    if harness.team != ctx.issue.team_key:
        raise Blocked(
            "team-repo-mismatch",
            f"{ctx.project.path}/harness.config.json declares tracker.team "
            f"{harness.team!r}, the ticket is {ctx.issue.team_key!r}",
        )
    cross_check_stack(harness, ctx.project.stack)
    ctx.harness = harness

    # §9.2 — the vault must resolve before anything runs, or the second brain is
    # silently unmounted and the session-learnings hooks write nowhere.
    if not ctx.registry.vault.path.is_dir():
        raise Blocked("vault-unresolved", str(ctx.registry.vault.path))

    target = ctx.state_dir / "context"

    written = write_context(target, ctx.issue)
    empty = [p.name for p in written if p.stat().st_size == 0]
    if empty:
        raise Blocked("context-empty", f"these context files came out empty: {empty}")

    ctx.log("context.written", files=[p.name for p in written], dir=str(target))
    advance(ctx, State.CONTEXT_LOADED)
