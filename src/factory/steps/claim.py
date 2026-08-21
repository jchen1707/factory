"""`approved -> claimed` — the lease, and the two Linear writes that announce it.

The claim is deliberately two-phase (§7.2): a `UNIQUE` insert nobody can duplicate,
then a conditional lease update whose row count tells the caller whether it won. Two
ticks racing the same ticket end with exactly one claim and one log line.
"""

from __future__ import annotations

from factory.machine import Blocked, State
from factory.steps import Context, advance, effect_marker, record_effect

__all__ = ["run"]

STEP = "claim"

#: Where a claimed ticket goes. `Done` is never written by the factory: Linear's own
#: GitHub integration moves it on merge, which is doctrine in `issue-tracker.md`, and
#: racing it would produce two state changes for one event.
IN_PROGRESS = "In Progress"


def run(ctx: Context) -> None:
    _refuse_second_writer(ctx)

    if ctx.dry_run:
        ctx.would(f"linear: move {ctx.run.linear_id} to {IN_PROGRESS}")
        ctx.would(f"linear: comment on {ctx.run.linear_id} (marker {effect_marker(ctx, STEP)})")
        advance(ctx, State.CLAIMED)
        return

    issue_uuid, current_state = ctx.linear.issue_uuid(ctx.run.linear_id)
    marker = effect_marker(ctx, STEP)

    record_effect(
        ctx,
        step=STEP,
        system="linear",
        key="state:in-progress",
        # Reconciliation for a state change is reading the state, not searching for a
        # marker: a state has no place to carry one, and its current value is the
        # complete answer to "did this already happen".
        reconcile=lambda: IN_PROGRESS if current_state == IN_PROGRESS else None,
        perform=lambda: ctx.linear.move_state(
            issue_uuid, ctx.linear.workflow_state_id(_team_id(ctx), IN_PROGRESS)
        ),
    )

    body = (
        f"<!-- {marker} -->\n"
        f"The factory claimed this ticket. Run `{ctx.run.id}`, attempt {ctx.run.attempt}.\n\n"
        f"Project `{ctx.project.name}`, base `{ctx.project.base_ref}`. "
        "It will stop before merging: that decision is not the factory's.\n"
    )
    record_effect(
        ctx,
        step=STEP,
        system="linear",
        key="comment:claimed",
        reconcile=lambda: (
            "found" if ctx.linear.comment_marker_present(ctx.run.linear_id, marker) else None
        ),
        perform=lambda: ctx.linear.add_comment(issue_uuid, body),
    )

    advance(ctx, State.CLAIMED)


def _team_id(ctx: Context) -> str:
    if ctx.issue is None:
        raise Blocked("no-issue-loaded", "claim needs the issue it is claiming")
    return ctx.issue.team_id


def _refuse_second_writer(ctx: Context) -> None:
    """§11.3 — one writer per project unless the registry says otherwise.

    Structural rather than advisory: two unattended writers in one repository would
    share a `.git`, and the second one's `git worktree add` would race the first one's
    index. Raising the limit is a registry edit, and each ticket still gets its own
    worktree and branch.
    """
    limit = ctx.registry.defaults.concurrency_per_project
    active = [
        run for run in ctx.store.active_runs_for_project(ctx.project.name) if run.id != ctx.run.id
    ]
    if len(active) >= limit:
        raise Blocked(
            "project-busy",
            f"{ctx.project.name} already has {len(active)} run(s) in a writing state "
            f"({[r.linear_id for r in active]}) and concurrency_per_project is {limit}",
        )
