"""`awaiting_human -> completed` — the merge boundary (§5.3, §24.8).

The factory never merges. §13.2 denies `gh pr merge` and §5.3 reserves this hop for James
under `merge-is-james`, so nothing here takes the edge on its own: the caller is a human
typing `factory complete`, and this module supplies the *evidence* that the merge it claims
actually happened, plus the announcement afterwards.

Why the edge needed building at all: `gc.COLLECTABLE` is `TERMINAL | {FAILED}`, and nothing
in `src/factory/` ever reached `COMPLETED`. A successful run therefore sat at
`awaiting_human` for ever and `gc` could never reclaim its worktree, branch or artifacts —
which is how FRO-6's stale worktree came to break `pnpm test` in frontend-harness.
"""

from __future__ import annotations

from factory.intake.linear import LinearError
from factory.steps import Context, effect_marker, record_effect

#: The Linear workflow state a merged ticket lands in. `In Review` is set at
#: `awaiting_human` (`block.IN_REVIEW`); this is the other end of that move.
DONE = "Done"

_STEP = "complete"


def announce_completed(ctx: Context, *, pr_url: str | None) -> None:
    """Move the issue to Done and comment once, after the transition is recorded.

    Best-effort in exactly the sense `block.announce` is: a Linear outage degrades the
    announcement and never masks the transition. The run is already `completed` in the
    store by the time this is called, and that is the record that matters.
    """

    try:
        _move_done(ctx)
        _completed_comment(ctx, pr_url)
    except LinearError as exc:
        ctx.log("completed.announce_failed", level="error", detail=str(exc)[:500])


def _move_done(ctx: Context) -> None:
    if ctx.issue is None:
        ctx.log("completed.move_skipped", reason="no issue loaded")
        return
    team_id = ctx.issue.team_id

    def perform() -> str | None:
        issue_uuid, current = ctx.linear.issue_uuid(ctx.run.linear_id)
        if current == DONE:
            return DONE
        ctx.linear.move_state(issue_uuid, ctx.linear.workflow_state_id(team_id, DONE))
        return DONE

    record_effect(
        ctx,
        step=_STEP,
        system="linear",
        key="state:done",
        reconcile=lambda: DONE if ctx.linear.issue_uuid(ctx.run.linear_id)[1] == DONE else None,
        perform=perform,
    )


def _completed_comment(ctx: Context, pr_url: str | None) -> None:
    marker = effect_marker(ctx, _STEP)
    issue_uuid, _ = ctx.linear.issue_uuid(ctx.run.linear_id)
    link = f"**Merged:** {pr_url}\n\n" if pr_url else ""
    body = (
        f"{marker}\n\n"
        f"{link}"
        f"Run `{ctx.run.id}` is **completed** — the pull request is merged and the run's "
        f"worktree, branch and artifacts are now collectable by `factory gc`.\n\n"
        f"_The merge was James's; the factory only recorded it._"
    )

    def perform() -> str | None:
        return ctx.linear.add_comment(issue_uuid, body)

    record_effect(
        ctx,
        step=_STEP,
        system="linear",
        key="comment:completed",
        reconcile=lambda: (
            "found" if ctx.linear.comment_marker_present(ctx.run.linear_id, marker) else None
        ),
        perform=perform,
    )
