"""§16.5 — what garbage collection may remove, and what it must refuse to.

The refusals are the interesting half. Every one of them protects somebody's work, and
each is asserted separately here because a sweep that removes one thing it should not is
worse than a sweep that removes nothing at all.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from factory import gc, repo
from factory.machine import State
from factory.sandbox.sbx import SbxError
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import FakeSandbox, git

WEEKS_AGO = time.time() - 30 * gc.DAY_SECONDS


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _finished_run(ctx: Context, state: State = State.CANCELLED) -> None:
    """A run with a real worktree and branch, parked in a collectable state and aged."""
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    ctx.store.record_transition(
        ctx.run.id, from_state=ctx.state, to_state=state, actor="human", rule="test"
    )
    # Both clocks, because §16.5's floor now counts from the transition that parked the
    # run rather than from the row's last write. Ageing `updated_at` alone is what these
    # tests used to do, and it stopped ageing anything the moment the clock moved.
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (int(WEEKS_AGO), ctx.run.id))
        conn.execute(
            "UPDATE transitions SET at = ? WHERE run_id = ? AND to_state = ?",
            (int(WEEKS_AGO), ctx.run.id, str(state)),
        )
    ctx.refresh()


def _sweep(ctx: Context, *, dry_run: bool) -> list[gc.Action]:
    return gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=dry_run)


def _kinds(actions: list[gc.Action], kind: str) -> list[gc.Action]:
    return [a for a in actions if a.kind == kind]


# --------------------------------------------------------------------------------
# --dry-run
# --------------------------------------------------------------------------------


def test_a_dry_run_names_what_it_would_touch_and_touches_none_of_it(ctx: Context) -> None:
    _finished_run(ctx)
    worktree = Path(ctx.run.worktree or "")

    actions = _sweep(ctx, dry_run=True)

    assert [a.target for a in _kinds(actions, "worktree-remove")] == [str(worktree)]
    assert all(not a.done for a in actions)
    assert worktree.is_dir()
    assert repo.local_branch_exists(ctx.project.path, ctx.run.branch or "")


def test_the_dry_run_and_the_real_sweep_are_one_code_path(ctx: Context) -> None:
    # The plan a dry run prints has to be the plan the sweep performs. Two
    # implementations of "what would I do" is how a --dry-run stops being worth reading.
    _finished_run(ctx)
    planned = {(a.kind, a.target) for a in _sweep(ctx, dry_run=True)}

    performed = {(a.kind, a.target) for a in _sweep(ctx, dry_run=False) if a.done}

    assert performed <= planned
    assert ("worktree-remove", ctx.run.worktree) in performed


# --------------------------------------------------------------------------------
# What it collects
# --------------------------------------------------------------------------------


def test_an_old_cancelled_run_loses_its_worktree(ctx: Context) -> None:
    _finished_run(ctx)
    worktree = Path(ctx.run.worktree or "")

    _sweep(ctx, dry_run=False)

    assert not worktree.exists()
    assert not repo.worktree_exists(ctx.project.path, worktree)


def test_the_attempt_evidence_is_archived_before_the_worktree_goes(ctx: Context) -> None:
    # Order matters: for a bind-mounted project the evidence lives *inside* the worktree,
    # so archiving afterwards would archive nothing and report success.
    _finished_run(ctx)
    attempt = Path(ctx.run.worktree or "") / ".factory" / "run" / "1"
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "events.jsonl").write_text('{"type":"thread.started"}\n')

    _sweep(ctx, dry_run=False)

    archived = ctx.home / "artifacts" / ctx.run.linear_id / ctx.run.id / "1" / "events.jsonl"
    assert archived.exists()
    assert not Path(ctx.run.worktree or "").exists()


def test_a_run_still_inside_the_age_floor_is_left_entirely_alone(ctx: Context) -> None:
    _finished_run(ctx)
    # Young by the clock that counts: the hop that parked it happened just now.
    with ctx.store.transaction() as conn:
        conn.execute(
            "UPDATE transitions SET at = ? WHERE run_id = ?", (int(time.time()), ctx.run.id)
        )
    ctx.refresh()

    actions = _sweep(ctx, dry_run=False)

    assert _kinds(actions, "worktree-remove") == []
    assert Path(ctx.run.worktree or "").is_dir()


def test_writing_the_run_row_does_not_grant_it_another_week(ctx: Context) -> None:
    """§16.5's floor counts from when the run stopped moving, not from the row's last write.

    This is the defect, in the shape it actually appeared. `factory complete` writes the
    run row to record a merge, so a run parked a month ago became "0 days old" the instant
    somebody recorded that its PR had landed — and `complete` printed "now collectable"
    while `gc --dry-run` listed nothing. Both behaved as designed and contradicted each
    other in front of the user. Measured 2026-08-23 on BAC-6 and FRO-11.

    `acquire_lease` and its renewal write the same column, so the old reading also meant
    any future lease on a finished run silently extended it. A lease records no transition,
    so counting from the transition log closes that too — asserted below.
    """
    _finished_run(ctx)
    ctx.store.update_run(ctx.run.id, blocked_reason=None)  # any write at all
    ctx.store.acquire_lease(ctx.run.id, ttl_seconds=600)
    ctx.refresh()
    assert ctx.run.updated_at > WEEKS_AGO, "the row really was rewritten just now"

    actions = _sweep(ctx, dry_run=False)

    assert _kinds(actions, "worktree-remove"), "a month-old run was rejuvenated by a row write"


def test_a_run_with_no_transition_into_its_state_falls_back_to_the_row(ctx: Context) -> None:
    """A row that reached its state by a path recording no hop still has to age, and the
    fallback is `updated_at` — the reading this replaced — so such a run is no worse off.

    Asserted in the *young* direction on purpose. "No transitions, therefore collect it"
    passes whether the fallback returns `updated_at` or the epoch, and an accidental epoch
    would quietly make every unlogged run instantly collectable — the one direction where
    a mistake here destroys work rather than merely delaying it.
    """
    _finished_run(ctx)
    with ctx.store.transaction() as conn:
        conn.execute("DELETE FROM transitions WHERE run_id = ?", (ctx.run.id,))
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (int(time.time()), ctx.run.id))
    ctx.refresh()

    actions = _sweep(ctx, dry_run=False)

    assert _kinds(actions, "worktree-remove") == []
    assert Path(ctx.run.worktree or "").is_dir()


def test_a_run_that_re_entered_its_state_ages_from_the_latest_entry(ctx: Context) -> None:
    """A run can be parked, reopened and parked again. The floor asks how long it has been
    sitting still *now*, so the clock starts at the most recent entry — reading the first
    would age it from work that was subsequently resumed, and collect a worktree somebody
    came back to.
    """
    _finished_run(ctx)  # leaves one aged transition into the resting state
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.IMPLEMENTING,
        to_state=ctx.run.state,
        actor="human",
        rule="re-entered just now",
    )
    ctx.refresh()

    actions = _sweep(ctx, dry_run=False)

    assert _kinds(actions, "worktree-remove") == []
    assert Path(ctx.run.worktree or "").is_dir()


def test_a_live_run_is_never_collected(ctx: Context) -> None:
    # Only `completed`, `cancelled` and `failed` are collectable. Age is not enough.
    claim_step.run(ctx)
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (int(WEEKS_AGO), ctx.run.id))
    ctx.refresh()

    actions = _sweep(ctx, dry_run=False)

    assert _kinds(actions, "worktree-remove") == []


# --------------------------------------------------------------------------------
# What it refuses — every one of these is somebody's work
# --------------------------------------------------------------------------------


def test_a_pushed_branch_is_never_deleted(ctx: Context) -> None:
    _finished_run(ctx)
    branch = ctx.run.branch or ""
    git(ctx.project.path, "push", "origin", f"{branch}:{branch}")

    actions = _sweep(ctx, dry_run=False)

    refusal = _kinds(actions, "branch-delete")[0]
    assert not refusal.done
    assert "pushed" in refusal.why
    assert repo.local_branch_exists(ctx.project.path, branch)


def test_a_branch_carrying_commits_beyond_the_base_is_never_deleted(ctx: Context) -> None:
    _finished_run(ctx)
    worktree = Path(ctx.run.worktree or "")
    (worktree / "work.py").write_text("x = 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "the agent's work, unpushed")

    actions = _sweep(ctx, dry_run=False)

    refusal = _kinds(actions, "branch-delete")[0]
    assert not refusal.done
    assert "commit" in refusal.why
    assert repo.local_branch_exists(ctx.project.path, ctx.run.branch or "")


def test_a_refusal_always_says_what_it_refused_and_why(ctx: Context) -> None:
    # Whatever it declines to remove, it names — because the next run fails on precisely
    # the debris nobody was told about.
    _finished_run(ctx)
    git(ctx.project.path, "push", "origin", f"{ctx.run.branch}:{ctx.run.branch}")

    for action in _sweep(ctx, dry_run=False):
        assert action.target
        assert action.why


def test_a_sandbox_in_use_is_not_stopped(ctx: Context) -> None:
    # `active_runs_for_project` is the guard: a sandbox with a run still in it is being
    # written to, and stopping it would kill the run inside.
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    ctx.store.record_transition(
        ctx.run.id, from_state=ctx.state, to_state=State.IMPLEMENTING, actor="auto"
    )
    ctx.refresh()

    actions = _sweep(ctx, dry_run=False)

    stops = _kinds(actions, "sandbox-stop")
    assert stops
    assert all(not a.done for a in stops)
    assert all("still using it" in a.why for a in stops)


def test_the_factory_never_touches_a_sandbox_it_does_not_own(ctx: Context) -> None:
    # A `codex-*` sandbox is James's live `csbx` session. The names come from the
    # registry, never from `sbx ls`, and `policy.assert_factory_sandbox` in the adapter
    # is the second lock on the same door.
    actions = _sweep(ctx, dry_run=False)

    touched = [a.target for a in actions if a.kind.startswith("sandbox-")]
    assert all(t.startswith(("factory-build-", "factory-review-")) for t in touched), touched


def test_artifacts_are_kept_while_the_disk_is_above_the_floor(ctx: Context) -> None:
    # This is the one step that destroys the record of a run, so age alone does not
    # license it: the disk being below the floor is the reason, and the fixture's floor
    # is 0 GB, which is never crossed.
    old = ctx.home / "artifacts" / "BAC-1" / "ancient"
    old.mkdir(parents=True)
    (old / "events.jsonl").write_text("{}\n")

    actions = _sweep(ctx, dry_run=False)

    assert _kinds(actions, "artifact-delete") == []
    assert old.is_dir()


def test_sandbox_cleanup_failure_is_reported_without_claiming_removal(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _finished_run(ctx)

    def refuse(self: FakeSandbox, name: str) -> None:
        raise SbxError(f"sbx remove {name} failed (exit 1)")

    monkeypatch.setattr(FakeSandbox, "remove", refuse)
    removals = _kinds(_sweep(ctx, dry_run=False), "sandbox-remove")
    assert removals
    assert any("failed (exit 1)" in action.why for action in removals)
    assert all(not action.done for action in removals)
