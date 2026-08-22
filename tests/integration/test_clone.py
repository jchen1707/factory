"""The `--clone` path — §19 Phase 7, pulled forward for `frontend-harness`.

`clone_ctx` is `ctx` with `requires_clone = true` and nothing else changed, so every
assertion here is about the clone and not about some other difference between two
fixtures. `FakeSandbox` grows a real `git clone` and a real `sandbox-<name>` remote for
these tests rather than canned answers, because the three things the path is made of —
cutting a branch inside the VM, the host seeing none of it, and the fetch back — are all
git behaviours. Faking them would fake the whole feature.

The constraints being modelled were measured on 2026-08-22 against `sbx` v0.38.0; the
table is in `steps/clone.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.machine import Blocked, State
from factory.sandbox.sbx import create_argv
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import clone as clone_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import review as review_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import FakeSandbox, git


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _to_worktree_ready(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)


def _to_verifying(ctx: Context) -> None:
    _to_worktree_ready(ctx)
    implement_step.run(ctx)


# --------------------------------------------------------------------------------
# the sandbox specification
# --------------------------------------------------------------------------------


def test_a_clone_project_no_longer_blocks_and_asks_for_the_clone(clone_ctx: Context) -> None:
    """The `clone-not-implemented` refusal is gone, and the flag it stood in for is set."""
    spec = sandbox_step.build_spec(clone_ctx)
    assert spec.clone is True
    assert "--clone" in create_argv(spec)


def test_a_bind_mounted_project_asks_for_no_clone(ctx: Context) -> None:
    spec = sandbox_step.build_spec(ctx)
    assert spec.clone is False
    assert "--clone" not in create_argv(spec)


def test_the_clone_spec_carries_a_writable_mount_the_host_can_read(clone_ctx: Context) -> None:
    """The repair for the half of §4.2 that `--clone` breaks.

    The clone is invisible to the host, so `.factory/` cannot live in it. An additional
    `rw` workspace is mounted at its identical path, and that is where the attempt
    directory goes.
    """
    spec = sandbox_step.build_spec(clone_ctx)
    writable = [w.path for w in spec.workspaces if not w.readonly]
    assert clone_ctx.clone_mount in writable
    assert clone_ctx.factory_dir.is_relative_to(clone_ctx.clone_mount)


def test_the_clone_mount_is_project_stable_not_per_run(clone_ctx: Context) -> None:
    """§9.1 fixes the workspace set at creation and the sandbox is named once per project,
    so a mount path carrying a run id or a ticket makes the *second* run of that project
    fail `_assert_spec_matches`. `_review_scratch` records what that already cost once."""
    mount = str(clone_ctx.clone_mount)
    assert clone_ctx.run.id not in mount
    assert clone_ctx.run.linear_id not in mount
    # The per-ticket split lives *inside* the mount, where it is free.
    assert clone_ctx.run.linear_id in str(clone_ctx.factory_dir)


# --------------------------------------------------------------------------------
# worktree_ready — the branch is cut inside the VM
# --------------------------------------------------------------------------------


def test_the_branch_is_cut_inside_the_vm_and_the_host_never_sees_it(clone_ctx: Context) -> None:
    _to_worktree_ready(clone_ctx)
    assert clone_ctx.run.state is State.WORKTREE_READY

    branch = clone_ctx.run.branch
    assert branch
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == branch
    # The host checkout knows nothing about it. That is the isolation, working.
    assert branch not in git(clone_ctx.project.path, "branch", "--list", branch)


def test_the_recorded_worktree_is_the_project_path_itself(clone_ctx: Context) -> None:
    """There is no directory under `.factory/worktrees/` to record: the clone *is* the
    working tree, and it sits at the project's own path on both sides."""
    _to_worktree_ready(clone_ctx)
    assert clone_ctx.run.worktree == str(clone_ctx.project.path)
    assert not (
        clone_ctx.project.path / ".factory" / "worktrees" / clone_ctx.run.linear_id
    ).exists()


def test_the_context_is_seeded_on_the_mount_where_both_sides_can_read_it(
    clone_ctx: Context,
) -> None:
    """The clone carries tracked content only, so `.factory/context/` written into the
    worktree would reach neither the host nor the next run."""
    _to_worktree_ready(clone_ctx)
    context_dir = clone_ctx.factory_dir / "context"
    assert (context_dir / "ticket.md").exists()
    assert (context_dir / "spec.md").exists()
    run_json = json.loads((clone_ctx.factory_dir / "run.json").read_text())
    assert run_json["clone"] is True
    assert run_json["branch"] == clone_ctx.run.branch


def test_cutting_the_branch_twice_attaches_rather_than_resetting(clone_ctx: Context) -> None:
    """The recovery model re-enters steps. A second `create_branch` after the agent has
    committed must not throw the commits away — `-B` would, which is why it is not used."""
    _to_verifying(clone_ctx)
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    head = git(clone, "rev-parse", "HEAD")

    clone_step.create_branch(clone_ctx, clone_ctx.run.branch or "")

    assert git(clone, "rev-parse", "HEAD") == head


# --------------------------------------------------------------------------------
# implementing — the protocol still runs through the filesystem
# --------------------------------------------------------------------------------


def test_the_attempt_directory_lands_on_the_mount_and_the_host_reads_it(
    clone_ctx: Context,
) -> None:
    """§4.2's whole claim: the host learns what happened by looking at files. For a clone
    project those files are on the mount, and the run reaches `verifying` because the host
    could read them there."""
    _to_verifying(clone_ctx)
    assert clone_ctx.run.state is State.VERIFYING
    attempt_dir = clone_ctx.factory_dir / "run" / "1"
    assert (attempt_dir / "last-message.json").exists()
    assert (attempt_dir / "events.jsonl").exists()
    assert attempt_dir.is_relative_to(clone_ctx.clone_mount)


def test_the_prompt_names_the_context_by_a_path_that_exists_in_the_vm(
    clone_ctx: Context,
) -> None:
    """A relative `.factory/context/ticket.md` names nothing inside the clone, and the
    agent would start its turn with no ticket and no way to know."""
    _to_worktree_ready(clone_ctx)
    prompt, _sha = implement_step.build_prompt(clone_ctx)
    context_dir = clone_ctx.factory_dir / "context"
    assert f"{context_dir}/ticket.md" in prompt
    assert (context_dir / "ticket.md").exists()


def test_a_write_under_the_project_path_never_reaches_the_host(clone_ctx: Context) -> None:
    """The constraint the whole path exists for. `node_modules` — and everything else the
    agent writes in its working tree — stays in the VM."""
    _to_verifying(clone_ctx)
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    assert (clone / "src" / "app" / "main.py").exists()
    assert not (clone_ctx.project.path / "src" / "app" / "main.py").exists()


# --------------------------------------------------------------------------------
# verifying -> reviewing — the branch comes home
# --------------------------------------------------------------------------------


def test_fetch_back_lands_the_agents_commits_in_a_host_worktree(clone_ctx: Context) -> None:
    _to_verifying(clone_ctx)
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    vm_head = git(clone, "rev-parse", "HEAD")

    path = clone_step.fetch_back(clone_ctx)

    assert path.is_dir()
    assert git(path, "rev-parse", "HEAD") == vm_head
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == clone_ctx.run.branch
    # Everything downstream reads `ctx.worktree`, and from here it is an ordinary one.
    assert clone_ctx.worktree == path
    assert (path / "src" / "app" / "main.py").exists()


def test_fetch_back_is_re_entrant(clone_ctx: Context) -> None:
    """A tick can die anywhere. The mirror is pure derived state, so the second call
    rebuilds it rather than trying to reconcile it."""
    _to_verifying(clone_ctx)
    first = clone_step.fetch_back(clone_ctx)
    head = git(first, "rev-parse", "HEAD")

    second = clone_step.fetch_back(clone_ctx)

    assert second == first
    assert git(second, "rev-parse", "HEAD") == head


def test_fetch_back_picks_up_a_commit_the_vm_made_after_the_first_fetch(
    clone_ctx: Context,
) -> None:
    _to_verifying(clone_ctx)
    clone_step.fetch_back(clone_ctx)
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    (clone / "later.py").write_text("x = 1\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "a second turn")

    path = clone_step.fetch_back(clone_ctx)

    assert git(path, "rev-parse", "HEAD") == git(clone, "rev-parse", "HEAD")
    assert (path / "later.py").exists()


def test_the_evidence_stays_on_the_mount_after_the_branch_comes_home(
    clone_ctx: Context,
) -> None:
    """`fetch_back` repoints `ctx.worktree`; it must not repoint `ctx.factory_dir`, or the
    verify step's gate report and the attempt's transcript would be looked for in a
    directory that never held them."""
    _to_verifying(clone_ctx)
    before = clone_ctx.factory_dir
    clone_step.fetch_back(clone_ctx)
    assert clone_ctx.factory_dir == before
    assert (before / "run" / "1" / "last-message.json").exists()


def test_the_review_step_fetches_back_before_the_replay(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The placement, not just the mechanism. The replay's host-side diff, the reviewer
    sandbox and the host-execution guard all read a host worktree, so the fetch has to
    have happened before the first of them runs."""
    seen: list[Path] = []

    def _record_worktree(ctx: Context) -> str:
        seen.append(ctx.worktree)
        return "proceed"

    monkeypatch.setattr(review_step.redphase, "replay", _record_worktree)
    monkeypatch.setattr(review_step.redphase, "weakening_guard", lambda ctx: [])
    monkeypatch.setattr(review_step, "_tier1", lambda ctx, h, rd: ([], False))
    monkeypatch.setattr(review_step, "_tier2_trigger", lambda ctx, h, th: "no-trigger")

    _to_verifying(clone_ctx)
    verify_step.run(clone_ctx)
    review_step.run(clone_ctx)

    assert seen == [clone_step.host_worktree_path(clone_ctx)]
    assert clone_ctx.state is State.PR_READY


def test_a_stale_host_branch_blocks_rather_than_reviewing_the_wrong_code(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a local branch of that name survives on the host and cannot be replaced, the
    mirror would be somebody else's code and the review, the PR body and the push would
    all describe work the agent never did. Named and stopped instead."""
    _to_verifying(clone_ctx)
    monkeypatch.setattr(clone_step.repo, "delete_local_branch", lambda repo, branch: None)
    branch = clone_ctx.run.branch or ""
    git(clone_ctx.project.path, "branch", branch, "v2")

    with pytest.raises(Blocked) as caught:
        clone_step.fetch_back(clone_ctx)

    assert caught.value.reason == "clone-mirror-stale"


# --------------------------------------------------------------------------------
# reviewing — the replay's scratch is the one thing that stays in the VM
# --------------------------------------------------------------------------------


def test_the_replay_scratch_stays_inside_the_clone(clone_ctx: Context) -> None:
    """The replay runs the repository's *test* command, and the project that needs
    `--clone` needs it because that command needs the VM-local `node_modules`. So the
    scratch checkout is the one thing that does not follow the branch home."""
    _to_verifying(clone_ctx)
    clone_step.fetch_back(clone_ctx)
    scratch = clone_step.scratch_path(clone_ctx)
    assert scratch.is_relative_to(clone_ctx.project.path)

    clone_step.scratch_add(clone_ctx, scratch, clone_ctx.project.base_ref)

    vm = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert vm is not None
    assert "scratch-" in git(vm, "worktree", "list")
    # In the VM, and nowhere the host can see.
    assert not scratch.exists()

    clone_step.scratch_remove(clone_ctx, scratch)
    assert "scratch-" not in git(vm, "worktree", "list")


def test_the_replay_applies_the_host_patch_inside_the_vm(clone_ctx: Context) -> None:
    """The patch is computed on the host worktree and applied in the VM. The two trees
    agree because one was fetched from the other — this is the assertion that says so."""
    _to_verifying(clone_ctx)
    host = clone_step.fetch_back(clone_ctx)
    patch = clone_step.repo.diff_pathspec(host, clone_ctx.project.base_ref, ["tests"])
    assert patch

    scratch = clone_step.scratch_path(clone_ctx)
    clone_step.scratch_add(clone_ctx, scratch, clone_ctx.project.base_ref)
    try:
        clone_step.scratch_apply(clone_ctx, scratch, patch)
        vm = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
        assert vm is not None
        landed = vm / scratch.relative_to(clone_ctx.project.path) / "tests" / "test_health.py"
        assert landed.exists()
    finally:
        clone_step.scratch_remove(clone_ctx, scratch)


# --------------------------------------------------------------------------------
# the dry run prints the clone chain
# --------------------------------------------------------------------------------


def test_the_dry_run_prints_the_in_vm_checkout_and_the_fetch_back(clone_ctx: Context) -> None:
    clone_ctx.dry_run = True
    claim_step.run(clone_ctx)
    context_step.run(clone_ctx)
    sandbox_step.run(clone_ctx)
    worktree_step.run(clone_ctx)

    planned = "\n".join(clone_ctx.planned)
    assert "--clone" in planned
    assert f"checkout -b {clone_ctx.shadow_branch}" in planned
    assert str(clone_ctx.clone_mount) in planned
