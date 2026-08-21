"""Phase 3 — the reviewing -> pr_ready -> awaiting_human half (§15.2, §15.3, §13.2).

The state-machine transitions are exercised here against the same `FakeSandbox` /
`FakeLinear` the Phase 1/2 suite uses. The review's codex call and the delivery `gh` call are
host-side model/HTTP spends, so they are monkeypatched: the tests prove the *transitions* and
the *guards*, not the model. The pure judgements (Tier-2 trigger, red-phase classifier, prompt
assembly, PR-body layout) have their own unit tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory import cli, repo
from factory.machine import Blocked, State
from factory.steps import Context
from factory.steps import deliver as deliver_step
from factory.steps import review as review_step
from factory.steps import verify as verify_step
from tests.integration.conftest import FakeLinear, FakeSandbox, git
from tests.integration.test_pipeline import _to_verifying

# --------------------------------------------------------------------------------
# wiring — the full chain runs review then deliver
# --------------------------------------------------------------------------------


def test_the_drive_calls_review_then_deliver_after_verify(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_drive` walks the whole chain. A dry run records every would-print without spending a
    model call, which is the cheapest proof that the wiring is in place."""
    ctx.dry_run = True
    # The dry-run steps must not touch the real adapters' side effects.
    monkeypatch.setattr(cli, "SbxAdapter", FakeSandbox)
    monkeypatch.setattr(
        cli,
        "LinearClient",
        lambda: FakeLinear(
            __import__("tests.integration.test_pipeline", fromlist=["TICKET"]).TICKET
        ),
    )

    cli._drive(ctx, force_plan=False)

    planned = "\n".join(ctx.planned)
    # review ran (red-phase + Tier 1 would-prints)
    assert "review" in planned.lower() or "tier 1" in planned.lower()
    # deliver ran (push + draft PR + the awaiting_human transition)
    assert "push" in planned
    assert "gh pr create --draft" in planned
    assert f"transition {State.REVIEWING} -> {State.PR_READY}" in planned or "pr_ready" in planned


# --------------------------------------------------------------------------------
# review transitions
# --------------------------------------------------------------------------------


def _to_reviewing(ctx: Context) -> None:
    _to_verifying(ctx)
    verify_step.run(ctx)
    assert ctx.state is State.REVIEWING


def _stub_redphase(monkeypatch: pytest.MonkeyPatch) -> None:
    """The red-phase replay needs a realistic committed worktree to run for real; the transition
    test is about the review's state machine, so the replay is stubbed to "proceed"."""
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "proceed")
    monkeypatch.setattr(review_step.redphase, "weakening_guard", lambda ctx: [])


def test_a_clean_review_advances_to_pr_ready(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    monkeypatch.setattr(review_step, "_tier1", lambda ctx, h, rd: ([], False))
    monkeypatch.setattr(review_step, "_tier2_trigger", lambda ctx, h, th: "no-trigger")

    review_step.run(ctx)

    assert ctx.state is State.PR_READY
    # The review summary is stashed for the PR body.
    summary = (ctx.state_dir / "review" / "review-summary.json").read_text()
    assert "no-trigger" in summary


def test_a_critical_tier1_finding_blocks_and_names_the_finding(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    finding = [{"severity": "critical", "file": "src/app.py", "line": 9, "summary": "off by one"}]
    monkeypatch.setattr(review_step, "_tier1", lambda ctx, h, rd: (finding, True))
    monkeypatch.setattr(review_step, "_tier2_trigger", lambda ctx, h, th: None)
    # Tier 2 returns more findings (none critical here) — the block is from Tier 1.
    monkeypatch.setattr(review_step, "_tier2", lambda ctx, rd: [])

    with pytest.raises(Blocked) as caught:
        review_step.run(ctx)
    assert caught.value.reason == "review-finding"
    assert "critical" in caught.value.detail
    assert "src/app.py" in caught.value.detail
    assert ctx.state is State.REVIEWING  # did not advance


def test_a_redphase_escalate_routes_to_awaiting_human(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "awaiting_human")
    # weakening guard not reached after an escalate, but stub it for safety
    monkeypatch.setattr(review_step.redphase, "weakening_guard", lambda ctx: [])

    review_step.run(ctx)

    assert ctx.state is State.AWAITING_HUMAN
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    assert linear.state == "In Review"
    # one awaiting_human comment, marker-keyed (idempotent on re-entry)
    assert any("awaiting_human" in c for c in linear.comments)


def test_a_test_weakening_guard_routes_to_awaiting_human(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "proceed")
    monkeypatch.setattr(
        review_step.redphase, "weakening_guard", lambda ctx: ["assert result == 42"]
    )

    review_step.run(ctx)

    assert ctx.state is State.AWAITING_HUMAN
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    assert "test-weakening" in linear.comments[-1]


# --------------------------------------------------------------------------------
# deliver transitions
# --------------------------------------------------------------------------------


def _to_pr_ready(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    monkeypatch.setattr(review_step, "_tier1", lambda ctx, h, rd: ([], False))
    monkeypatch.setattr(review_step, "_tier2_trigger", lambda ctx, h, th: "no-trigger")
    review_step.run(ctx)
    assert ctx.state is State.PR_READY


def test_deliver_blocks_on_a_vendored_tree_edit(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(
        deliver_step.repo, "changed_paths", lambda wt, br: [".agents/vendor/harness/hooks/lib.mjs"]
    )

    with pytest.raises(Blocked) as caught:
        deliver_step.run(ctx)
    assert caught.value.reason == "host-execution-blocked"
    assert ctx.state is State.PR_READY  # did not advance


def test_deliver_routes_a_deny_list_path_to_awaiting_human_without_pushing(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: [".husky/pre-commit"])
    pushed: list[str] = []
    monkeypatch.setattr(deliver_step.github, "push", lambda wt, b: pushed.append(b))

    deliver_step.run(ctx)

    assert ctx.state is State.AWAITING_HUMAN
    assert pushed == []  # never pushed
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    assert "Host-execution guard" in linear.comments[-1]
    assert ".husky/pre-commit" in linear.comments[-1]


def test_deliver_opens_a_draft_pr_and_announces(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    monkeypatch.setattr(deliver_step.github, "push", lambda wt, b: None)
    monkeypatch.setattr(deliver_step.github, "find_pr", lambda wt, b: None)
    monkeypatch.setattr(
        deliver_step.github,
        "create_pr",
        lambda wt, **kw: "https://github.com/jchen1707/python-harness/pull/11",
    )

    deliver_step.run(ctx)

    assert ctx.state is State.AWAITING_HUMAN
    assert ctx.run.pr_url == "https://github.com/jchen1707/python-harness/pull/11"
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    assert linear.state == "In Review"
    assert "pull/11" in linear.comments[-1]


def test_a_duplicate_pr_is_edited_not_recreated(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    monkeypatch.setattr(deliver_step.github, "push", lambda wt, b: None)
    monkeypatch.setattr(
        deliver_step.github,
        "find_pr",
        lambda wt, b: "https://github.com/jchen1707/python-harness/pull/12",
    )
    created: list[str] = []
    edited: list[int] = []
    monkeypatch.setattr(
        deliver_step.github, "create_pr", lambda wt, **kw: created.append(kw["head"])
    )
    monkeypatch.setattr(deliver_step.github, "edit_pr", lambda wt, n, **kw: edited.append(n))

    deliver_step.run(ctx)

    assert created == []  # F16: never created a second
    assert edited == [12]
    assert ctx.run.pr_url == "https://github.com/jchen1707/python-harness/pull/12"


def test_a_secret_in_the_pr_body_blocks_before_any_push(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F18: a secret that reached the PR body is compromised. The run fails before any push or
    # PR write, and the block names the kind without naming the value.
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    monkeypatch.setattr(
        deliver_step.github,
        "render_pr_body",
        lambda **kw: "Fixes BAC-4\n\nleaked ghp_" + "A" * 36,
    )
    pushed: list[str] = []
    monkeypatch.setattr(deliver_step.github, "push", lambda wt, b: pushed.append(b))

    with pytest.raises(Blocked) as caught:
        deliver_step.run(ctx)
    assert caught.value.reason == "secret-in-artifact"
    assert pushed == []  # never pushed
    assert ctx.state is State.PR_READY  # did not advance


# --------------------------------------------------------------------------------
# the red-phase replay's patch round trip — against real git, not a fake
# --------------------------------------------------------------------------------


def test_the_test_half_of_a_diff_applies_to_a_scratch_worktree(project_repo: Path) -> None:
    """`diff_pathspec` -> `apply_patch` is the replay's load-bearing seam, and both ends are
    git. Faking either would fake the thing that broke: BAC-4's run `1effc543d83a459a` died
    here with `corrupt patch at line 387`, because the patch reached `git apply` one byte
    short of what git wrote — `_git`'s `strip()` had taken its final newline.

    The assertion is the round trip, not the newline: a patch that applies is the property
    the replay needs, and it holds for any future helper that keeps the document intact.
    """
    tests_dir = project_repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_search.py").write_text("def test_scores_are_sorted() -> None:\n    pass\n")
    git(project_repo, "add", "-A")
    git(project_repo, "commit", "-m", "tests: the existing suite")
    git(project_repo, "push", "origin", "v2")

    git(project_repo, "checkout", "-b", "feat/BAC-9-scores")
    (tests_dir / "test_search.py").write_text(
        "def test_scores_are_sorted() -> None:\n    pass\n\n\n"
        "def test_internal_documents_are_excluded() -> None:\n"
        "    assert exclude_internal(['a', '_internal']) == ['a']\n"
    )
    (project_repo / "engine.py").write_text("def exclude_internal(names):\n    return names\n")
    git(project_repo, "add", "-A")
    git(project_repo, "commit", "-m", "feat: exclude internal documents")

    patch = repo.diff_pathspec(project_repo, "origin/v2", ["tests"])
    assert "engine.py" not in patch  # the test half only

    scratch = project_repo.parent / "scratch-replay"
    repo.add_detached_worktree(project_repo, scratch, "origin/v2")
    repo.apply_patch(scratch, patch)  # raised GitError("corrupt patch at line …") before

    landed = (scratch / "tests" / "test_search.py").read_text()
    assert "test_internal_documents_are_excluded" in landed
    assert not (scratch / "engine.py").exists()  # the implementation half stayed behind


def test_a_step_that_dies_of_a_git_failure_blocks_the_run(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter failure is still a run that stopped, and a stopped run says so.

    The replay's `git apply` is the live case: a `GitError` used to reach `main`'s
    catch-all, so the process exited 2 while the run row sat at `reviewing` with an empty
    `blocked_reason` — indistinguishable, to anything reading state, from a run still in
    flight."""
    monkeypatch.setattr(
        review_step,
        "run",
        lambda _ctx: (_ for _ in ()).throw(repo.GitError("git apply failed: corrupt patch")),
    )

    with pytest.raises(Blocked) as caught:
        cli._drive(ctx, force_plan=False)

    assert caught.value.reason == "reviewing-step-failed"
    assert "corrupt patch" in caught.value.detail
