"""Replay a branch's tests against their actual merge-base, not a moving base tip."""

from dataclasses import replace
from pathlib import Path

import pytest

from factory import repo
from factory.sandbox.base import Completed
from factory.steps import Context, redphase
from factory.steps import clone as clone_step
from tests.integration.conftest import git
from tests.integration.test_redphase_gates import _declare_tests, _to_verifying


@pytest.mark.parametrize("in_clone", [False, True])
def test_replay_uses_patch_baseline_when_base_diverges(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, in_clone: bool
) -> None:
    project = ctx.project.path
    tests = project / "tests"
    tests.mkdir(exist_ok=True)
    original = "def test_value():\n    assert 1 == 1\n"
    (tests / "test_value.py").write_text(original)
    git(project, "add", "tests/test_value.py")
    git(project, "commit", "-m", "baseline test")
    base = git(project, "rev-parse", "HEAD")
    base_ref = git(project, "rev-parse", "--symbolic-full-name", ctx.project.base_ref)
    git(project, "update-ref", base_ref, base)
    git(project, "push", "origin", "HEAD:refs/heads/v2")
    _to_verifying(ctx)
    _declare_tests(ctx)
    candidate = original + "\ndef test_new():\n    assert 1 == 2\n"
    (ctx.worktree / "tests/test_value.py").write_text(candidate)
    git(ctx.worktree, "add", "tests/test_value.py")
    git(ctx.worktree, "commit", "-m", "candidate regression test")
    candidate_head = git(ctx.worktree, "rev-parse", "HEAD")
    (tests / "test_value.py").write_text("def test_upstream():\n    assert True\n")
    git(project, "add", "tests/test_value.py")
    git(project, "commit", "-m", "divergent upstream test")
    tip = git(project, "rev-parse", "HEAD")
    git(project, "update-ref", base_ref, tip)
    ctx.project = replace(ctx.project, requires_clone=in_clone)
    if in_clone:
        # Exercise the clone dispatch with real Git in this disposable test repository.
        monkeypatch.setattr(clone_step, "scratch_path", lambda c: project / "scratch-test")
        monkeypatch.setattr(
            clone_step, "scratch_add", lambda c, p, ref: repo.add_detached_worktree(project, p, ref)
        )
        monkeypatch.setattr(
            clone_step, "scratch_apply", lambda c, p, patch: repo.apply_patch(p, patch)
        )
        monkeypatch.setattr(
            clone_step, "scratch_remove", lambda c, p: repo.remove_worktree(project, p, force=True)
        )
    observed: list[Path] = []

    def execute(*args: object, **kwargs: object) -> Completed:
        scratch = Path(str(kwargs["workdir"]))
        observed.append(scratch)
        assert git(scratch, "rev-parse", "HEAD") == base
        assert (scratch / "tests/test_value.py").read_text() == candidate
        return Completed(("test",), 1, "FAILED tests/test_value.py::test_new AssertionError", "")

    monkeypatch.setattr(ctx.sandbox, "exec_sync", execute)
    assert ctx.harness is not None
    assert redphase._replay_scope(ctx, Path("."), ctx.harness) == "proceed"
    assert len(observed) == 1
    assert not observed[0].exists()
    assert git(ctx.worktree, "rev-parse", "HEAD") == candidate_head
    assert git(project, "rev-parse", ctx.project.base_ref) == tip


def test_weakening_guard_reads_a_test_the_base_later_removed(ctx: Context) -> None:
    project = ctx.project.path
    tests = project / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_value.py").write_text("def test_value():\n    assert 1 == 1\n")
    git(project, "add", "tests/test_value.py")
    git(project, "commit", "-m", "baseline test")
    base_ref = git(project, "rev-parse", "--symbolic-full-name", ctx.project.base_ref)
    git(project, "update-ref", base_ref, git(project, "rev-parse", "HEAD"))
    git(project, "push", "origin", "HEAD:refs/heads/v2")
    _to_verifying(ctx)
    _declare_tests(ctx)
    (ctx.worktree / "tests/test_value.py").write_text("def test_value():\n    pass\n")
    git(ctx.worktree, "add", "tests/test_value.py")
    git(ctx.worktree, "commit", "-m", "candidate weakens the test")
    git(project, "rm", "-q", "tests/test_value.py")
    git(project, "commit", "-m", "upstream removes the test")
    git(project, "update-ref", base_ref, git(project, "rev-parse", "HEAD"))

    assert redphase.weakening_guard(ctx) == ["assert 1 == 1"]
