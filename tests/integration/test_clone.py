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
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory import cli, recovery, repo
from factory.artifacts import AttemptDir
from factory.machine import Blocked, State
from factory.sandbox.base import Completed, SandboxSpec
from factory.sandbox.sbx import create_argv
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import clone as clone_step
from factory.steps import context as context_step
from factory.steps import deliver as deliver_step
from factory.steps import implement as implement_step
from factory.steps import redphase as redphase_step
from factory.steps import review as review_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
from factory.steps import worktree as worktree_step
from factory.store import Run
from tests.integration.conftest import FakeSandbox, advance_state, git


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
    advance_state(ctx, until=State.VERIFYING)


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


def test_the_preflight_proves_the_clone_is_actually_in_effect(clone_ctx: Context) -> None:
    """`ensure` attaches to an existing sandbox by name, and `_assert_spec_matches` compares
    the workspace set — which is *identical* whether or not `--clone` was passed, because
    the clone sits at the project's own path. A sandbox created before this project needed
    a clone would therefore be attached to in silence. This is the assertion that notices."""
    claim_step.run(clone_ctx)
    context_step.run(clone_ctx)
    sandbox_step.run(clone_ctx)

    checks = {c["check_name"]: c["status"] for c in clone_ctx.store.checks(clone_ctx.run.id)}
    assert checks["preflight:clone-isolates-the-workspace"] == "pass"
    # And it left nothing behind in the repository it wrote into.
    assert not list((clone_ctx.project.path / ".factory").glob("clone-canary-*"))


def test_the_isolation_canary_fails_when_the_sandbox_is_bind_mounted(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canary that cannot fail proves nothing — §9.3's whole point. Here the sandbox is
    made without its clone, which is exactly the silent-attach case, and the preflight has
    to refuse the run rather than let a `pnpm install` reach the host's node_modules."""
    claim_step.run(clone_ctx)
    context_step.run(clone_ctx)
    monkeypatch.setattr(
        sandbox_step,
        "build_spec",
        lambda ctx: replace(_clone_spec(ctx), clone=False),
    )

    with pytest.raises(Blocked) as caught:
        sandbox_step.run(clone_ctx)

    assert caught.value.reason == "enforcement-disabled"
    assert "clone-isolates-the-workspace" in caught.value.detail
    assert not list((clone_ctx.project.path / ".factory").glob("clone-canary-*"))


def _clone_spec(ctx: Context) -> SandboxSpec:
    """`build_spec` before the monkeypatch replaces it."""
    return sandbox_step.SandboxSpec(
        project=ctx.project.name,
        role="build",
        name=ctx.project.build_sandbox,
        workspaces=(
            sandbox_step.Workspace(ctx.project.path),
            sandbox_step.Workspace(ctx.clone_mount),
        ),
        template=ctx.project.template or None,
        env=dict(ctx.project.env),
        clone=True,
    )


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


def test_the_branch_is_cut_from_a_freshly_fetched_base(clone_ctx: Context) -> None:
    """The clone outlives every run in it, so its `origin/<base>` is as old as the sandbox.

    This defect did not fail — it silently built the wrong thing. Measured 2026-08-22 on
    frontend-harness: the host's `origin/v2` was `910a2f1` and the clone's `1c4422d`,
    three merges behind, so the next run would have been cut from a base carrying neither
    the previous ticket's work nor the vendored hook it was about to be verified by.

    The commit below lands on the host *after* the clone was made, which is exactly the
    window. Without `refresh_base` the branch does not contain it.
    """
    claim_step.run(clone_ctx)
    context_step.run(clone_ctx)
    sandbox_step.run(clone_ctx)

    host = clone_ctx.project.path
    (host / "moved-on.txt").write_text("a merge that landed after the clone was made\n")
    git(host, "add", "moved-on.txt")
    git(host, "commit", "-m", "chore: advance the base after the clone exists")
    advanced = git(host, "rev-parse", "HEAD")

    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    assert advanced not in git(clone, "log", "--format=%H", "-20", clone_ctx.project.base_ref)

    worktree_step.run(clone_ctx)

    assert advanced in git(clone, "log", "--format=%H", "-20", "HEAD")
    assert (Path(clone) / "moved-on.txt").exists()


def test_a_base_that_cannot_be_fetched_blocks_rather_than_cutting_from_a_stale_one(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continuing would reproduce the very failure `refresh_base` exists to prevent, and
    the staleness would be invisible in every artefact the run goes on to produce."""
    claim_step.run(clone_ctx)
    context_step.run(clone_ctx)
    sandbox_step.run(clone_ctx)

    fake = _fake(clone_ctx)
    real = fake.exec_sync

    def refuse_fetch(name: str, argv: Any, **kwargs: Any) -> Any:
        if "fetch" in tuple(argv):
            return Completed(tuple(argv), 128, "", "fatal: unable to access origin")
        return real(name, argv, **kwargs)

    monkeypatch.setattr(fake, "exec_sync", refuse_fetch)
    with pytest.raises(Blocked) as caught:
        worktree_step.run(clone_ctx)
    assert caught.value.reason == "clone-fetch-failed"


def test_re_entering_the_cut_does_not_refetch_the_base(clone_ctx: Context) -> None:
    """The re-entrant path attaches to the agent's commits. There is nothing to refresh
    into by then, and moving `origin/<base>` under them would change nothing but the diff
    the review reads."""
    _to_verifying(clone_ctx)
    fake = _fake(clone_ctx)
    before = len([call for call in fake.sync_calls if "fetch" in call[1]])

    clone_step.create_branch(clone_ctx, clone_ctx.run.branch or "")

    assert len([call for call in fake.sync_calls if "fetch" in call[1]]) == before


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


def test_fetch_back_starts_a_stopped_sandbox_before_fetching(clone_ctx: Context) -> None:
    """The shape a real run is actually in at the `reviewing` entry, and the one that broke.

    `sbx` registers the `sandbox-<name>` remote when the sandbox starts, gives it a new
    port every time, and removes it when the sandbox stops. The build sandbox's last
    session is the implement step's detached exec, so by `reviewing` it has auto-stopped
    and the remote is gone — a real FRO-6 run failed exactly here, with "does not appear
    to be a git repository" against a remote that `git remote -v` had listed a minute
    earlier. `fetch_back` has to start it first.
    """
    _to_verifying(clone_ctx)
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    vm_head = git(clone, "rev-parse", "HEAD")

    _fake(clone_ctx).stop_sandbox(clone_ctx.project.build_sandbox)
    assert _fake(clone_ctx).git_daemon_url(clone_ctx.project.build_sandbox) is None

    path = clone_step.fetch_back(clone_ctx)

    assert git(path, "rev-parse", "HEAD") == vm_head
    assert clone_ctx.project.build_sandbox in _fake(clone_ctx).running


def test_a_sandbox_that_will_not_start_blocks_with_the_commits_named_as_safe(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure has to be legible: the work is not lost, it is still in the VM, and the
    reason is the remote's lifecycle rather than anything about the branch."""
    _to_verifying(clone_ctx)
    _fake(clone_ctx).stop_sandbox(clone_ctx.project.build_sandbox)
    monkeypatch.setattr(_fake(clone_ctx), "_start", lambda name: None)

    with pytest.raises(Blocked) as caught:
        clone_step.fetch_back(clone_ctx)

    assert caught.value.reason == "clone-remote-unreachable"
    assert "still in the VM" in caught.value.detail


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

    _to_verifying(clone_ctx)
    advance_state(clone_ctx)
    advance_state(clone_ctx)

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


def test_the_pr_body_carries_the_gate_table_for_a_clone_run(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evidence has to survive the worktree moving under the run.

    `fetch_back` repoints `ctx.worktree` at a host checkout, but the attempt's evidence was
    written to `ctx.factory_dir` on the mount and stays there. Anything that rebuilt the
    attempt path from `ctx.worktree` therefore found an empty directory and rendered a PR
    body with **no gate table and a blank verdict** — silently, because a missing file is
    indistinguishable from a gate that did not run. FRO-10's first real PR shipped exactly
    that. §13.2 requires the full table, so this asserts the rows are there.
    """
    _to_verifying(clone_ctx)
    advance_state(clone_ctx)
    _stub_review(monkeypatch)
    advance_state(clone_ctx)
    assert clone_ctx.state is State.PR_READY

    body = deliver_step._render_body(clone_ctx)

    assert "ruff check" in body or "pytest" in body, body
    assert "Verdict: **pass**" in body, body
    assert "Fixes FRO-4" in body or f"Fixes {clone_ctx.run.linear_id}" in body


def _stub_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "proceed")
    monkeypatch.setattr(review_step.redphase, "weakening_guard", lambda ctx: [])


# --------------------------------------------------------------------------------
# verifying — a resume re-establishes the run's branch in the shared clone
# --------------------------------------------------------------------------------


def test_a_clone_verify_resume_re_establishes_the_run_branch_before_running_gates(
    clone_ctx: Context,
) -> None:
    """The handoff's open question 2. A clone build sandbox is shared across a project's
    runs, so a second run checking out its own branch leaves the clone on the wrong tree.
    A `--from verifying` resume would then spawn the gate report against that other
    ticket's code while the evidence points at this one. `verify.start` puts the clone
    back on the run's branch first; `--from reviewing` dodged this (review fetches back
    fresh), verify did not."""
    _to_verifying(clone_ctx)
    branch = clone_ctx.run.branch
    assert branch is not None
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == branch

    # Another run of the same project repoints the shared clone onto its own branch.
    git(clone, "checkout", "-b", "feat/other-ticket")
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "feat/other-ticket"

    # Park at blocked-at-verifying (FRO-6's shape) and resume into verifying.
    clone_ctx.store.update_run(clone_ctx.run.id, blocked_reason="env-gate-failed")
    clone_ctx.store.record_transition(
        clone_ctx.run.id,
        from_state=State.VERIFYING,
        to_state=State.BLOCKED,
        actor="auto",
        rule="env-gate-failed",
        detail="env-gate-failed",
    )
    clone_ctx.refresh()
    recovery.resume(clone_ctx)
    assert clone_ctx.state is State.VERIFYING

    # The tick's START_NEEDED action re-establishes the run's branch before spawning.
    verify_step.start(clone_ctx)

    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == branch


def test_a_clone_verify_resume_blocks_when_the_branch_is_gone(clone_ctx: Context) -> None:
    """A resume that has lost the agent's branch — the sandbox was recreated, or the clone
    reset — blocks rather than silently cutting a fresh empty branch and running the gates
    against a tree with no agent work in it. That is a human decision, not a re-run."""
    _to_verifying(clone_ctx)
    branch = clone_ctx.run.branch
    assert branch is not None
    clone = _fake(clone_ctx).clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None

    # The branch is gone: detach so it is not checked out, then delete it.
    git(clone, "checkout", "--detach", "HEAD")
    git(clone, "branch", "-D", branch)

    with pytest.raises(Blocked) as caught:
        verify_step.start(clone_ctx)
    assert caught.value.reason == "clone-branch-missing"


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


def test_the_replay_chooses_the_in_vm_scratch_for_a_clone_project(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two previous tests prove the in-VM scratch *works*; this one proves `replay`
    reaches for it. Without it, `in_clone` could be hard-coded false and the suite would
    stay green while every real clone run made its scratch on a host tree the VM cannot
    see — a mutation that survived until this test existed.
    """
    _to_verifying(clone_ctx)
    clone_step.fetch_back(clone_ctx)
    _declare_tests(clone_ctx)
    chose: list[str] = []
    monkeypatch.setattr(
        redphase_step.clone_step,
        "scratch_add",
        lambda ctx, scratch, base: chose.append("vm"),
    )
    monkeypatch.setattr(
        redphase_step.repo,
        "add_detached_worktree",
        lambda repo, path, ref: chose.append("host"),
    )
    monkeypatch.setattr(redphase_step.clone_step, "scratch_apply", lambda ctx, s, p: None)
    monkeypatch.setattr(redphase_step.clone_step, "scratch_remove", lambda ctx, s: None)
    monkeypatch.setattr(
        redphase_step, "_classify", lambda completed, files: ("red", "the test failed")
    )

    assert redphase_step.replay(clone_ctx) == "proceed"
    assert chose == ["vm"]


def test_the_replay_chooses_the_host_scratch_for_a_bind_mounted_project(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction, so the clone branch cannot simply swallow both."""
    _to_verifying(ctx)
    _declare_tests(ctx)
    chose: list[str] = []
    monkeypatch.setattr(
        redphase_step.clone_step, "scratch_add", lambda c, scratch, base: chose.append("vm")
    )
    monkeypatch.setattr(
        redphase_step.repo, "add_detached_worktree", lambda r, path, ref: chose.append("host")
    )
    monkeypatch.setattr(redphase_step.repo, "apply_patch", lambda wt, patch: None)
    monkeypatch.setattr(redphase_step.repo, "remove_worktree", lambda r, p, force=False: None)
    monkeypatch.setattr(
        redphase_step, "_classify", lambda completed, files: ("red", "the test failed")
    )
    _commit_a_test(ctx)

    assert redphase_step.replay(ctx) == "proceed"
    assert chose == ["host"]


def _declare_tests(ctx: Context) -> None:
    """Declare a `tests` pathspec on the loaded harness config. Without one the replay
    reports `unavailable` and returns before it picks a scratch at all, which is a
    different (and already tested) branch from the one these two tests are about."""
    assert ctx.harness is not None
    ctx.harness = replace(ctx.harness, tests=("tests",))


def _commit_a_test(ctx: Context) -> None:
    """A bind-mounted run's worktree is empty until something commits into it; the replay
    needs a diff that touches the declared `tests` pathspecs to get past its first guard."""
    worktree = ctx.worktree
    tests_dir = worktree / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_health.py").write_text("def test_health() -> None:\n    assert True\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", "test: a health check")


def _rerun_the_same_ticket(ctx: Context) -> Run:
    """Cancel this run and start a second one for the **same ticket**, as `factory cancel`
    followed by `factory run` does.

    The same ticket is the whole point: `_LIVE_RUN_INDEX` allows a second row only once
    the first reaches a terminal state, and that pair — cancel, then re-run — is exactly
    the sequence that produced the stale-`exit` read on FRO-6.
    """
    ctx.store.record_transition(
        ctx.run.id, from_state=ctx.run.state, to_state=State.CANCELLED, actor="human"
    )
    return ctx.store.insert_run(
        linear_id=ctx.run.linear_id, project=ctx.run.project, team=ctx.run.team
    )


def test_two_runs_of_one_ticket_cannot_share_an_attempt_directory(
    clone_ctx: Context, tmp_path: Path
) -> None:
    """A bind-mounted project gets a fresh evidence tree for free — `cancel` removes the
    worktree it lives in. The clone mount survives every run, so before this the second
    run of a ticket landed on the first one's attempt directory: it found the stale
    `exit`, returned from its wait instantly, and reported the first run's verdict and
    token counts while its own agent was still running in the sandbox.
    """
    first = clone_ctx.factory_dir
    second = replace(clone_ctx, run=_rerun_the_same_ticket(clone_ctx)).factory_dir

    assert first != second
    # Both still inside the one mount `sbx` fixed at creation — the mount is what is
    # fixed, subdirectories under it are free.
    assert clone_ctx.clone_mount in first.parents
    assert clone_ctx.clone_mount in second.parents


def test_a_second_run_does_not_read_the_first_runs_verdict(clone_ctx: Context) -> None:
    # The property that actually failed, stated directly: evidence written by one run is
    # not reachable from another run's attempt directory.
    _to_worktree_ready(clone_ctx)
    first_attempt = AttemptDir.create(clone_ctx.factory_dir, 1)
    first_attempt.exit_file.write_text("0")
    first_attempt.last_message.write_text('{"status": "blocked"}')

    second_ctx = replace(clone_ctx, run=_rerun_the_same_ticket(clone_ctx))
    second_attempt = AttemptDir.create(second_ctx.factory_dir, 1)

    assert not second_attempt.exit_file.exists()
    assert first_attempt.exit_file.exists()  # and the first run's record survives


def test_cancel_releases_the_branch_inside_the_clone(clone_ctx: Context) -> None:
    """The second defect that does not fail loudly.

    `_release_local_debris` deletes the *host* branch; for a `--clone` project the branch
    the agent worked on lives in the VM and survives it. `create_branch` is re-entrant by
    design, so the next run of the same ticket checks that abandoned branch out instead of
    cutting a fresh one from the refreshed base — and builds on a dead run's commits
    without saying so.
    """
    _to_verifying(clone_ctx)
    branch = clone_ctx.run.branch
    assert branch
    fake = _fake(clone_ctx)
    clone = fake.clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None

    lines = clone_step.release_branch(fake, clone_ctx.project, clone_ctx.run)

    assert branch not in git(clone, "branch", "--list", branch)
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD") == clone_ctx.project.base_branch
    assert any(branch in line for line in lines)


def test_the_released_branch_is_unnamed_rather_than_destroyed(clone_ctx: Context) -> None:
    """A rollback that destroys the evidence of why the run needed rolling back is not a
    rollback. The clone's commits were never pushable at all (the VM has no credential),
    so this is the only copy — it keeps its commits under a ref no step checks out."""
    _to_verifying(clone_ctx)
    fake = _fake(clone_ctx)
    clone = fake.clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    head = git(clone, "rev-parse", "HEAD")

    clone_step.release_branch(fake, clone_ctx.project, clone_ctx.run)

    rescue = f"refs/factory/cancelled/{clone_ctx.run.id}/{clone_ctx.run.branch}"
    assert git(clone, "rev-parse", rescue) == head


def test_the_next_run_of_the_ticket_cuts_fresh_after_a_release(clone_ctx: Context) -> None:
    """The property the whole fix is for, stated end to end: cancel, then re-run, and the
    second run's branch does not carry the first run's commits."""
    _to_verifying(clone_ctx)
    fake = _fake(clone_ctx)
    clone = fake.clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    abandoned = git(clone, "rev-parse", "HEAD")
    clone_step.release_branch(fake, clone_ctx.project, clone_ctx.run)

    run = _rerun_the_same_ticket(clone_ctx)
    clone_ctx.store.acquire_lease(run.id, ttl_seconds=600)
    _to_worktree_ready(replace(clone_ctx, run=run))

    assert abandoned not in git(clone, "log", "--format=%H", "-20", "HEAD")


def test_releasing_a_branch_that_is_already_gone_is_not_an_error(clone_ctx: Context) -> None:
    """A rollback must not fail on debris that is already gone — a stopped or recreated
    sandbox has no branch to release, and cancel has to finish either way."""
    _to_verifying(clone_ctx)
    fake = _fake(clone_ctx)
    clone_step.release_branch(fake, clone_ctx.project, clone_ctx.run)

    lines = clone_step.release_branch(fake, clone_ctx.project, clone_ctx.run)

    assert any("no branch" in line for line in lines)


def test_a_bind_mounted_project_has_no_clone_branch_to_release(ctx: Context) -> None:
    # The host rule already covers it; touching the sandbox here would start one for
    # nothing on every cancel of every non-clone project.
    _to_verifying(ctx)
    assert clone_step.release_branch(_fake(ctx), ctx.project, ctx.run) == []


def test_a_branch_that_could_not_be_saved_is_left_in_place(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never delete what could not be saved first. Leaving the branch misdirects the next
    run, which is bad; deleting the only copy of an implementation is worse."""
    _to_verifying(clone_ctx)
    fake = _fake(clone_ctx)
    clone = fake.clone_dir(clone_ctx.project.build_sandbox)
    assert clone is not None
    real = fake.exec_sync

    def refuse_update_ref(name: str, argv: Any, **kwargs: Any) -> Any:
        if "update-ref" in tuple(argv):
            return Completed(tuple(argv), 1, "", "fatal: cannot lock ref")
        return real(name, argv, **kwargs)

    monkeypatch.setattr(fake, "exec_sync", refuse_update_ref)
    lines = clone_step.release_branch(fake, clone_ctx.project, clone_ctx.run)

    branch = clone_ctx.run.branch or ""
    assert branch in git(clone, "branch", "--list", branch)
    assert any("left in place" in line for line in lines)


def test_cancel_never_offers_the_repository_itself_as_a_worktree(clone_ctx: Context) -> None:
    """`factory cancel FRO-6`, 2026-08-22, ran
    `git worktree remove --force /Users/james/frontend-harness`.

    A `--clone` run records the project path as its workdir — the branch is cut inside
    the VM, so the host side *is* the repository. `git worktree list` includes the main
    working tree, so `worktree_exists` said yes and the rollback believed it had found
    debris. Git declined and the whole cancel aborted with it, leaving the tracker
    un-restored and the ticket stuck.
    """
    _to_worktree_ready(clone_ctx)
    clone_ctx.store.update_run(clone_ctx.run.id, worktree=str(clone_ctx.project.path))
    clone_ctx.refresh()

    paths = cli._worktree_paths(
        clone_ctx.project, clone_ctx.registry, clone_ctx.run, clone_ctx.run.linear_id
    )

    assert clone_ctx.project.path not in paths


def test_removing_the_repository_itself_is_refused_by_name(clone_ctx: Context) -> None:
    # The second lock on the same door. Whatever hands it a path, the primitive refuses
    # the repository rather than throwing git's error from the middle of a rollback.
    with pytest.raises(repo.GitError) as caught:
        repo.remove_worktree(clone_ctx.project.path, clone_ctx.project.path, force=True)

    assert "the repository itself" in str(caught.value)
    assert clone_ctx.project.path.is_dir()
