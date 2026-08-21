"""`* -> blocked` — the tracker write §13.1 specifies for a blocked run.

A block that writes only the local database is invisible where it matters. The ticket
keeps whatever state the claim left it in, and the board goes on showing work the factory
has stopped doing; the only record that anything went wrong is a log line on James's
laptop. §13.1's table names the write — *comment with reason and evidence path; add
`needs-info`* — and this module is it.

Two properties matter more than the write itself:

- **It must never mask the block that caused it.** A Linear outage while announcing a
  block would otherwise turn a named, diagnosable stop into a stack trace about HTTP, and
  the reason the run stopped is the single most useful thing the run produced. Every
  failure here is caught and logged.
- **It must be idempotent.** The same block is re-announced whenever the run is
  re-entered, so both halves go through the effects ledger and reconcile by reading
  Linear rather than by retrying.

`needs-info` is also load-bearing rather than decorative: it is in `BLOCKING_LABELS`, so
adding it makes eligibility condition 7 refuse to re-claim the ticket until a human has
taken the label off. The comment says why; the label stops the machine.
"""

from __future__ import annotations

from factory.intake.linear import LinearError
from factory.steps import Context, effect_marker, record_effect

__all__ = ["NEEDS_INFO", "announce"]

STEP = "block"

#: §13.1's blocked row. Already in `linear.BLOCKING_LABELS`, so this both explains the
#: stop to a reader and prevents the next tick from claiming the ticket again.
NEEDS_INFO = "needs-info"


def announce(ctx: Context, reason: str, detail: str) -> None:
    """Comment the reason and the evidence path, then add `needs-info`.

    Called from the one place that handles a block, so a step raising `Blocked` never
    has to remember to do this.
    """
    if ctx.dry_run:
        ctx.would(f"linear: comment on {ctx.run.linear_id} (marker {effect_marker(ctx, STEP)})")
        ctx.would(f"linear: add label {NEEDS_INFO!r} to {ctx.run.linear_id}")
        return

    try:
        _comment(ctx, reason, detail)
        _label(ctx)
    except LinearError as exc:
        # The block itself is already recorded and printed. Losing the announcement is
        # a degraded outcome, not a different one, and raising here would replace a
        # named reason with a transport error.
        ctx.log("block.announce_failed", level="error", reason=reason, detail=str(exc)[:500])


def _comment(ctx: Context, reason: str, detail: str) -> None:
    marker = effect_marker(ctx, STEP)
    issue_uuid, _ = ctx.linear.issue_uuid(ctx.run.linear_id)
    body = (
        f"<!-- {marker} -->\n"
        f"The factory stopped: **{reason}**.\n\n"
        f"{detail}\n\n"
        f"Evidence: `{ctx.state_dir}` — `factory status {ctx.run.linear_id}` reads it back. "
        f"Run `{ctx.run.id}`, attempt {ctx.run.attempt}, state `{ctx.state}`.\n\n"
        f"This ticket now carries `{NEEDS_INFO}`, which stops the factory claiming it "
        "again. Removing that label is the decision to try once more.\n"
    )
    record_effect(
        ctx,
        step=STEP,
        system="linear",
        key=f"comment:blocked:{reason}",
        reconcile=lambda: (
            "found" if ctx.linear.comment_marker_present(ctx.run.linear_id, marker) else None
        ),
        perform=lambda: ctx.linear.add_comment(issue_uuid, body),
    )


def _label(ctx: Context) -> None:
    """Add `needs-info` to whatever labels the issue already carries.

    Linear replaces the whole label set rather than appending, so the current ids are
    read first — and read again on a retry, because a human may have edited the labels
    between the two attempts and clobbering that would be worse than not labelling.
    """
    if ctx.issue is None:
        ctx.log("block.label_skipped", reason="no issue loaded")
        return

    available = ctx.linear.team_labels(ctx.issue.team_id)
    wanted = available.get(NEEDS_INFO)
    if wanted is None:
        # Not fatal, and deliberately not created: the factory adds no label a team has
        # not already defined. The comment carries the whole reason regardless.
        ctx.log("block.label_skipped", reason=f"team defines no {NEEDS_INFO!r} label")
        return

    def perform() -> str | None:
        issue_uuid, current_ids, current_names = ctx.linear.issue_labels(ctx.run.linear_id)
        if NEEDS_INFO in current_names:
            return NEEDS_INFO
        ctx.linear.set_labels(issue_uuid, [*current_ids, wanted])
        return NEEDS_INFO

    record_effect(
        ctx,
        step=STEP,
        system="linear",
        key="label:needs-info",
        reconcile=lambda: (
            NEEDS_INFO if NEEDS_INFO in ctx.linear.issue_labels(ctx.run.linear_id)[2] else None
        ),
        perform=perform,
    )
