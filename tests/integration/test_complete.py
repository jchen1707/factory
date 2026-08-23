"""§5.3 — `awaiting_human -> completed`, the merge boundary.

The edge and its `merge-is-james` rule existed in `machine.py` from the start; nothing
ever took it. That is why these tests are here rather than in `test_machine.py`: the gap
was never in the table, it was in the absence of a caller, and a table test cannot see
an absent caller.

The property that motivated the work is `test_gc_reclaims_only_once_the_run_is_completed`.
Everything above it protects the one way this command can do damage — recording a merge
that did not happen, which hands a live PR's worktree and branch to `gc`.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pytest

from factory import gc
from factory.machine import Blocked, State
from factory.steps import Context, advance
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import HOME, FakeLinear, FakeSandbox

TICKET_ID = "BAC-4"
PR_URL = "https://github.com/jchen1707/python-harness/pull/66"
WEEKS_AGO = time.time() - 30 * gc.DAY_SECONDS


def _delivered(ctx: Context, *, aged: bool = False) -> None:
    """A run with a real worktree and branch, parked at `awaiting_human` with a PR —
    exactly where the four runs in the live database are sitting."""
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    ctx.store.record_transition(
        ctx.run.id,
        from_state=ctx.state,
        to_state=State.AWAITING_HUMAN,
        actor="auto",
        rule="delivered",
    )
    ctx.store.update_run(ctx.run.id, pr_url=PR_URL)
    if aged:
        with ctx.store.transaction() as conn:
            conn.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ?", (int(WEEKS_AGO), ctx.run.id)
            )
    ctx.refresh()


def _age(ctx: Context) -> None:
    """Make the run look old to `gc`, by both clocks.

    §16.5's floor counts from the transition that parked the run, so ageing `updated_at`
    alone no longer ages anything — which is the point of the change, and the reason this
    helper had to grow a second statement rather than being deleted.
    """
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (int(WEEKS_AGO), ctx.run.id))
        conn.execute("UPDATE transitions SET at = ? WHERE run_id = ?", (int(WEEKS_AGO), ctx.run.id))
    ctx.refresh()


def _complete(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, *, pr_state: str | None, ticket: str = TICKET_ID
) -> tuple[int, FakeLinear]:
    """`factory complete <ticket>` with only `gh` and Linear faked. The registry, the
    store and git are real."""
    from factory import cli

    _seed_models(ctx)
    linear = FakeLinear(ctx.issue, state="In Review")  # type: ignore[arg-type]
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "SbxAdapter", FakeSandbox)
    monkeypatch.setattr(cli, "LinearClient", lambda: linear)
    monkeypatch.setattr(cli.github, "pr_state", lambda cwd, url: pr_state)
    code = cli.cmd_complete(argparse.Namespace(ticket=ticket))
    return code, linear


def _seed_models(ctx: Context) -> None:
    """`cmd_complete` builds a real Context, which loads routing. The fixture home only
    carries the registry, so the real `models.toml` is copied in the way the schemas are."""
    target = ctx.home / "config" / "models.toml"
    if not target.exists():
        target.write_text((HOME / "config" / "models.toml").read_text())


def _transitions(ctx: Context) -> list[tuple[str, str, str]]:
    rows = ctx.store._conn.execute(
        "SELECT from_state, to_state, actor FROM transitions WHERE run_id = ? ORDER BY rowid",
        (ctx.run.id,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# --------------------------------------------------------------------------------
# the factory may never take this edge itself
# --------------------------------------------------------------------------------


def test_the_factory_cannot_complete_a_run_on_its_own(ctx: Context) -> None:
    """`merge-is-james`. The command below is a human typing; this is what stops the
    tick from ever doing the same thing unattended."""
    _delivered(ctx)
    with pytest.raises(Blocked) as exc:
        advance(ctx, State.COMPLETED, rule="delivered")
    assert exc.value.reason == "requires-human"
    assert "merge-is-james" in str(exc.value)
    assert ctx.store.run_by_id(ctx.run.id).state is State.AWAITING_HUMAN  # type: ignore[union-attr]


# --------------------------------------------------------------------------------
# the evidence gate
# --------------------------------------------------------------------------------


def test_an_open_pr_is_refused_and_nothing_moves(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The damage case. Completing an unmerged run makes `gc` delete the worktree and
    branch of a PR that is still open."""
    _delivered(ctx)
    code, linear = _complete(ctx, monkeypatch, pr_state="OPEN")
    assert code == 1
    assert ctx.store.run_by_id(ctx.run.id).state is State.AWAITING_HUMAN  # type: ignore[union-attr]
    assert linear.state_changes == []
    assert linear.comments == []


def test_gh_failing_is_not_the_same_answer_as_not_merged(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pr_state` returns None for an unauthenticated or offline `gh`. Both refuse, but
    only one of them is worth retrying, and the operator has to be able to tell."""
    _delivered(ctx)
    code, linear = _complete(ctx, monkeypatch, pr_state=None)
    assert code == 1
    assert ctx.store.run_by_id(ctx.run.id).state is State.AWAITING_HUMAN  # type: ignore[union-attr]
    assert linear.state_changes == []


def test_a_run_with_no_pr_cannot_be_completed(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review finding parks at `awaiting_human` with `pr_url` unset. There is no merge
    to record, so `cancel` is the command that ends it — not this one."""
    _delivered(ctx)
    ctx.store.update_run(ctx.run.id, pr_url="")
    ctx.refresh()
    code, _ = _complete(ctx, monkeypatch, pr_state="MERGED")
    assert code == 1
    assert ctx.store.run_by_id(ctx.run.id).state is State.AWAITING_HUMAN  # type: ignore[union-attr]


def test_a_run_that_never_delivered_cannot_be_completed(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim_step.run(ctx)
    code, _ = _complete(ctx, monkeypatch, pr_state="MERGED")
    assert code == 1
    assert ctx.store.run_by_id(ctx.run.id).state is not State.COMPLETED  # type: ignore[union-attr]


# --------------------------------------------------------------------------------
# the merge, recorded
# --------------------------------------------------------------------------------


def test_a_merged_pr_takes_the_edge_as_james(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    _delivered(ctx)
    code, _ = _complete(ctx, monkeypatch, pr_state="MERGED")

    assert code == 0
    assert ctx.store.run_by_id(ctx.run.id).state is State.COMPLETED  # type: ignore[union-attr]
    last = _transitions(ctx)[-1]
    assert last == (str(State.AWAITING_HUMAN), str(State.COMPLETED), "human")


def test_the_merge_moves_linear_to_done_and_comments_once(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _delivered(ctx)
    _, linear = _complete(ctx, monkeypatch, pr_state="MERGED")
    assert linear.state == "Done"
    assert len(linear.comments) == 1
    assert PR_URL in linear.comments[0]


def test_completing_twice_is_a_no_op_rather_than_a_second_comment(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.2. `completed` is terminal, so the second call has nothing to do — but it must
    say so and exit 0 rather than erroring, because a human who is not sure whether the
    first one landed will run it again."""
    _delivered(ctx)
    _, linear = _complete(ctx, monkeypatch, pr_state="MERGED")
    assert len(linear.comments) == 1

    from factory import cli

    monkeypatch.setattr(cli, "LinearClient", lambda: linear)
    code = cli.cmd_complete(argparse.Namespace(ticket=TICKET_ID))
    assert code == 0
    assert len(linear.comments) == 1
    assert linear.state_changes == ["state-done"]


def test_a_linear_outage_does_not_mask_the_transition(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transition is the record that matters; the announcement is best-effort. The
    same rule `block.announce` follows."""
    from factory.intake.linear import LinearError

    _delivered(ctx)
    from factory import cli

    _seed_models(ctx)
    linear = FakeLinear(ctx.issue, state="In Review")  # type: ignore[arg-type]
    linear.fail_with = LinearError("503 from Linear")
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "SbxAdapter", FakeSandbox)
    monkeypatch.setattr(cli, "LinearClient", lambda: linear)
    monkeypatch.setattr(cli.github, "pr_state", lambda cwd, url: "MERGED")

    code = cli.cmd_complete(argparse.Namespace(ticket=TICKET_ID))
    assert code == 0
    assert ctx.store.run_by_id(ctx.run.id).state is State.COMPLETED  # type: ignore[union-attr]


# --------------------------------------------------------------------------------
# the property this whole change exists for
# --------------------------------------------------------------------------------


def test_gc_reclaims_only_once_the_run_is_completed(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gc.COLLECTABLE` is `TERMINAL | {FAILED}`, and `awaiting_human` is in neither. So
    before this command existed, a *successful* run was the one kind of run `gc` could
    never reclaim — which is how FRO-6's stale worktree came to break `pnpm test`.
    """
    _delivered(ctx, aged=True)
    worktree = Path(ctx.run.worktree or "")
    assert worktree.exists()

    before = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=True)
    assert [a for a in before if a.target == str(worktree)] == []

    _complete(ctx, monkeypatch, pr_state="MERGED")
    _age(ctx)  # §16.5 collects on age, not on state alone

    after = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=True)
    assert [a for a in after if a.kind == "worktree-remove" and a.target == str(worktree)]
