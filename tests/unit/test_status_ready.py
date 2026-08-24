"""`factory status`'s "PR merged — ready to complete" notice.

The `awaiting_human -> completed` edge is `merge-is-james`: the factory never
takes it. Phase 5 produced two runs (BAC-6, FRO-11) that reached `awaiting_human`
with merged PRs and sat there looking like every other parked run, because
nothing told James *when* to take the edge. `_ready_to_complete` surfaces it
without taking it — a notice on the board, not a transition — and
`factory complete <TICKET>` stays the human command.

The property this file keeps honest: a `gh` that cannot answer (`None`) must not
look the same as a PR James has not merged, for the same reason `cmd_complete`
refuses both — only one of them is worth retrying.
"""

from __future__ import annotations

from collections.abc import Callable

from factory.cli import _ready_to_complete
from factory.console.views import RunRow


def _row(ticket: str, project: str, state: str, pr_url: str | None) -> RunRow:
    """Build a RunRow with only the fields `_ready_to_complete` reads set."""
    return RunRow(
        run_id=ticket,
        ticket=ticket,
        project=project,
        branch=None,
        state=state,
        badge=None,
        attempt=0,
        rung="-",
        elapsed_in_state=0.0,
        timeout_seconds=None,
        context_pct=None,
        context_reason=None,
        tokens_in=0,
        tokens_out=0,
        tokens_cached=0,
        spend_usd=None,
        spend_ceiling=20.0,
        activity=None,
        heartbeat_age=None,
        blocked_reason=None,
        pr_url=pr_url,
    )


def _fake(states: dict[tuple[str, str], str | None]) -> Callable[[str, str], str | None]:
    return lambda project, url: states.get((project, url))


def test_an_awaiting_run_with_a_merged_pr_is_ready() -> None:
    rows = [
        _row("BAC-6", "python-harness", "awaiting_human", "https://x/pull/70"),
    ]
    ready = _ready_to_complete(rows, _fake({("python-harness", "https://x/pull/70"): "MERGED"}))
    assert ready == {"BAC-6"}


def test_an_open_pr_is_not_ready() -> None:
    rows = [
        _row("FRO-11", "frontend-harness", "awaiting_human", "https://x/pull/48"),
    ]
    ready = _ready_to_complete(rows, _fake({("frontend-harness", "https://x/pull/48"): "OPEN"}))
    assert ready == set()


def test_gh_failure_is_not_merged() -> None:
    """A transient `gh` failure (None) must not read as "ready" — that would
    nag James about a run whose PR may not be merged, and the edge stays his."""
    rows = [
        _row("BAC-6", "python-harness", "awaiting_human", "https://x/pull/70"),
    ]
    ready = _ready_to_complete(rows, _fake({("python-harness", "https://x/pull/70"): None}))
    assert ready == set()


def test_a_run_not_at_awaiting_human_is_not_ready_even_if_merged() -> None:
    """The notice is about the one edge out of `awaiting_human`. A merged PR on a
    run still in `reviewing` (or anywhere else) is not the factory's to surface."""
    rows = [
        _row("BAC-9", "python-harness", "reviewing", "https://x/pull/99"),
    ]
    ready = _ready_to_complete(rows, _fake({("python-harness", "https://x/pull/99"): "MERGED"}))
    assert ready == set()


def test_an_awaiting_run_without_a_pr_is_not_ready() -> None:
    rows = [
        _row("BAC-10", "python-harness", "awaiting_human", None),
    ]
    ready = _ready_to_complete(rows, _fake({}))
    assert ready == set()
