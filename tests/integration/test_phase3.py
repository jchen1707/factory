"""Phase 3 — the reviewing -> pr_ready -> awaiting_human half (§15.2, §15.3, §13.2).

The state-machine transitions are exercised here against the same `FakeSandbox` /
`FakeLinear` the Phase 1/2 suite uses. The review's codex call and the delivery `gh` call are
host-side model/HTTP spends, so they are monkeypatched: the tests prove the *transitions* and
the *guards*, not the model. The pure judgements (Tier-2 trigger, red-phase classifier, prompt
assembly, PR-body layout) have their own unit tests.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from factory import cli, recovery, repo
from factory.machine import Blocked, State
from factory.steps import Context, advance, redphase
from factory.steps import deliver as deliver_step
from factory.steps import review as review_step
from factory.steps import verify as verify_step
from factory.store import Store
from tests.integration.conftest import (
    HOME,
    FakeLinear,
    FakeSandbox,
    _seed_vendored_review_tree,
    git,
)
from tests.integration.test_pipeline import _fake, _to_verifying

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
    # deliver ran (push + the PR + the awaiting_human transition)
    assert "push" in planned
    assert "gh pr create --base" in planned
    assert "--draft" not in planned
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
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    # The fan-out runs detached; the fake writes the canned findings to each axis. The
    # fixture's diff is small, so the real Tier-2 trigger skips and only the two Tier-1
    # axes run — the summary names the skip rule.
    _fake(ctx).review_findings = {"findings": []}

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
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    finding = [{"severity": "critical", "file": "src/app.py", "line": 9, "summary": "off by one"}]
    _fake(ctx).review_findings = {"findings": finding}
    # Tier 2 skips on the small fixture diff, so the critical finding is from Tier 1.

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


def _accept(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> int:
    """`factory accept <ticket>` against the fixture home, with only Linear faked."""
    target = ctx.home / "config" / "models.toml"
    if not target.exists():
        target.write_text((HOME / "config" / "models.toml").read_text())
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "SbxAdapter", FakeSandbox)
    monkeypatch.setattr(cli, "LinearClient", lambda: ctx.linear)
    return cli.cmd_accept(argparse.Namespace(ticket=ctx.run.linear_id, note=None))


def test_a_cleared_weakening_escalation_lets_the_interrupted_review_run(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard still finds the same hunks; the acceptance is what lets the run past them.

    This is the property, not the command: `weakening_guard` is left returning an offending
    line, so a run that reaches `pr_ready` here can only have got there by reading the
    acceptance. Without the check row it parks at `awaiting_human`, which the assertion
    below the record proves in the same test.
    """
    _to_reviewing(ctx)
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "proceed")
    monkeypatch.setattr(
        review_step.redphase, "weakening_guard", lambda ctx: ["assert result == 42"]
    )
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {"findings": []}

    # Unaccepted: parked.
    review_step.run(ctx)
    assert ctx.state is State.AWAITING_HUMAN

    ctx.store.record_check(
        ctx.run.id,
        ctx.run.attempt,
        redphase.ESCALATION_ACCEPTED,
        "accepted",
        reason=redphase.TEST_WEAKENING,
        detail="the removed assertions asserted a stub this ticket deletes",
    )
    from factory.steps import advance

    advance(ctx, State.REVIEWING, actor="human", rule="escalation-cleared-is-james")

    review_step.run(ctx)

    assert ctx.state is State.PR_READY


def test_a_cleared_redphase_escalation_lets_the_interrupted_review_run(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property for the other §15.3 companion check, and the reason they are
    recorded under separate rules: clearing one must not clear the other."""
    _to_reviewing(ctx)
    monkeypatch.setattr(review_step.redphase, "replay", lambda ctx: "awaiting_human")
    monkeypatch.setattr(review_step.redphase, "weakening_guard", lambda ctx: [])
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {"findings": []}

    # An acceptance of the *other* escalation does not clear this one.
    ctx.store.record_check(
        ctx.run.id,
        ctx.run.attempt,
        redphase.ESCALATION_ACCEPTED,
        "accepted",
        reason=redphase.TEST_WEAKENING,
        detail="wrong escalation",
    )
    review_step.run(ctx)
    assert ctx.state is State.AWAITING_HUMAN

    ctx.store.record_check(
        ctx.run.id,
        ctx.run.attempt,
        redphase.ESCALATION_ACCEPTED,
        "accepted",
        reason=redphase.REDPHASE_INCONCLUSIVE,
        detail="the runner is not installed in this sandbox",
    )
    from factory.steps import advance

    advance(ctx, State.REVIEWING, actor="human", rule="escalation-cleared-is-james")

    review_step.run(ctx)

    assert ctx.state is State.PR_READY


def test_accept_refuses_a_run_that_already_delivered_a_pr(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`awaiting_human` with a PR is the ordinary end of a run, not an escalation. Accepting
    it would re-enter `reviewing` on work that is already delivered."""
    _to_reviewing(ctx)
    ctx.store.update_run(ctx.run.id, pr_url="https://github.com/x/y/pull/1")
    from factory.steps import advance

    advance(ctx, State.AWAITING_HUMAN, actor="auto", rule="delivered")
    code = _accept(ctx, monkeypatch)

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.AWAITING_HUMAN


def test_accept_refuses_an_escalation_it_does_not_clear(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host-execution deny-list also parks at `awaiting_human`, and it is a security
    boundary rather than a judgement about test quality. `accept` must not touch it."""
    _to_reviewing(ctx)
    from factory.steps import advance

    advance(ctx, State.AWAITING_HUMAN, actor="auto", rule="host-execution-deny")
    code = _accept(ctx, monkeypatch)

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.AWAITING_HUMAN


def test_parked_on_reads_the_last_escalation_not_the_first(ctx: Context) -> None:
    """A run can be parked, reopened and parked again; the rule being cleared is the one it
    is sitting on now. The rows are written directly — this is a question about the log, and
    `advance` would refuse the reopen hop from a state this fixture is not in."""
    _to_reviewing(ctx)
    for from_state, to_state, rule in (
        (State.REVIEWING, State.AWAITING_HUMAN, "redphase-inconclusive"),
        (State.AWAITING_HUMAN, State.IMPLEMENTING, "reopen-after-review-is-james"),
        (State.REVIEWING, State.AWAITING_HUMAN, "test-weakening"),
    ):
        ctx.store.record_transition(
            ctx.run.id, from_state=from_state, to_state=to_state, actor="human", rule=rule
        )

    assert cli._parked_on(ctx.store, ctx.run) == "test-weakening"


def test_the_tier2_trigger_reads_the_sensitive_paths_off_the_project(ctx: Context) -> None:
    """The wiring, not the rule. `_decide_tier2`'s table has always been tested with a
    `sensitive` argument passed straight in; nothing asserted where that argument comes
    from, so the trigger could stop consulting the registry and every test would stay
    green. Measured: a mutation replacing `ctx.project.sensitive_paths` with `()` broke no
    test in the suite. That is the same silence the registry move exists to end.

    The change is committed here rather than taken from the fixture so the two assertions
    differ in exactly one thing — what the project declares.
    """
    _to_reviewing(ctx)
    worktree = Path(ctx.run.worktree or "")
    target = worktree / "src" / "app" / "ai" / "retrieval.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(
        worktree,
        "commit",
        "-m",
        "a one-file change inside a directory a project may call sensitive",
    )
    assert ctx.harness is not None

    # Nothing declared: one small file trips no size rule, so Tier 2 is skipped.
    ctx.project = replace(ctx.project, sensitive_paths=())
    assert review_step._tier2_trigger(ctx, ctx.harness, False) == "no-trigger"

    # The same diff, with the directory it touches declared sensitive: Tier 2 runs.
    ctx.project = replace(ctx.project, sensitive_paths=("src/app/ai/**",))
    assert review_step._tier2_trigger(ctx, ctx.harness, False) is None

    # And a glob that names a directory this repository does not have changes nothing —
    # which is precisely why `doctor` has to check the list separately.
    ctx.project = replace(ctx.project, sensitive_paths=("src/**/routes/**",))
    assert review_step._tier2_trigger(ctx, ctx.harness, False) == "no-trigger"


def test_a_human_resume_into_reviewing_re_runs_it_rather_than_re_collecting(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review that already ran and failed leaves a *finished* attempt row, and `reap`
    sends a finished row to `collect` — which re-derives the same failure from the same
    files on every tick for ever. `resume --from reviewing` used to advance and stop, so
    the run could not be moved at all: each attempt to move it recorded an identical block.

    Measured 2026-08-23 on FRO-11, whose reviewer had died on an expired credential. After
    the credential was renewed the resume re-collected byte-identical evidence — same
    `cf-ray`, same five reconnect lines, no new process.

    The property is that the resume leaves a **live** attempt: `ended_at` cleared, because
    `start_attempt` replaced the dead row rather than the tick reading it again.
    """
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {"findings": []}

    # A review that ran and died, exactly as the reaper would have left it.
    ctx.store.start_attempt(
        ctx.run.id,
        ctx.run.attempt,
        State.REVIEWING,
        sandbox=ctx.project.review_sandbox,
        artifact_dir=str(ctx.state_dir / "review"),
    )
    ctx.store.finish_attempt(
        ctx.run.id, ctx.run.attempt, State.REVIEWING, exit_code=1, outcome="failed"
    )
    advance(ctx, State.BLOCKED, actor="auto", rule="review-agent-failed")
    ctx.refresh()
    dead = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.REVIEWING)
    assert dead is not None
    assert dead["ended_at"] is not None

    recovery.resume(ctx, from_state="reviewing")

    ctx.refresh()
    assert ctx.state is State.REVIEWING
    live = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.REVIEWING)
    assert live is not None
    assert live["ended_at"] is None, (
        "the resume re-collected the dead attempt instead of re-running"
    )


# --------------------------------------------------------------------------------
# deliver transitions
# --------------------------------------------------------------------------------


def _to_pr_ready(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {"findings": []}
    review_step.run(ctx)
    assert ctx.state is State.PR_READY


def _capture(sink: list[list[str]], *, stdout: str = "") -> object:
    """Stand in for `subprocess.run` and keep the argv the delivery module built."""

    def run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        sink.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, stdout=stdout, stderr="")

    return run


def test_the_dry_run_preview_names_the_flags_gh_pr_create_actually_carries(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview is worth reading only while it describes the command that runs.

    `--draft` lived in six places and moved out of all six at once; equality here is what
    stops the *next* flag moving in one of them only. Both halves are measured — the argv
    from `create_pr`, the preview from a real dry run — so neither can be restated.
    """
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])

    argv: list[list[str]] = []
    monkeypatch.setattr(deliver_step.github.subprocess, "run", _capture(argv, stdout="url\n"))
    deliver_step.github.create_pr(
        Path(ctx.run.worktree or "."),
        base="v2",
        head="feat/x",
        title="t",
        body_file=Path("body.md"),
    )
    real = {token for token in argv[0] if token.startswith("--")}

    ctx.dry_run = True
    deliver_step.run(ctx)
    start = next(i for i, line in enumerate(ctx.planned) if "gh pr create" in line)
    preview = " ".join(ctx.planned[start : start + 2])
    assert real == set(re.findall(r"--[a-z-]+", preview))


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


def test_deliver_opens_a_ready_for_review_pr_and_announces(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_pr_ready(ctx, monkeypatch)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    monkeypatch.setattr(deliver_step.github, "push", lambda wt, b: None)
    monkeypatch.setattr(deliver_step.github, "find_pr", lambda wt, b: None)
    # `create_pr` is the seam the whole item turns on, so the real argv is captured
    # rather than the wrapper stubbed away — the draft flag is the one token that has to
    # be gone, and a stub that swallows argv could not tell you (§24.8).
    argv: list[list[str]] = []
    monkeypatch.setattr(
        deliver_step.github.subprocess,
        "run",
        _capture(argv, stdout="https://github.com/jchen1707/python-harness/pull/11\n"),
    )

    deliver_step.run(ctx)

    create = next(a for a in argv if a[:3] == ["gh", "pr", "create"])
    assert "--draft" not in create
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


def test_both_tiers_write_where_the_sandbox_can_actually_write(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `-o` a reviewer is given must sit inside the sandbox's writable mount.

    `FakeSandbox` enforces it the way the real one does — a `-o` outside the mounts is
    exit 0 with `Failed to write last message file` and no file — so a tier that names
    the run directory directly fails here exactly as Tier 2 failed on BAC-4's run
    `2efa19065ce6476e`.
    """
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    # Force Tier 2 to run, so both tiers are exercised in one drive.
    monkeypatch.setattr(review_step, "_tier2_trigger", lambda ctx, h, tier1_has_human=False: None)
    _fake(ctx).review_findings = {"findings": []}

    review_step.run(ctx)

    scratch = review_step._review_scratch(ctx)
    # The fan-out ran detached; the per-axis `-o` paths are baked into the script. Every
    # one must sit inside the sandbox's writable scratch mount, the way the real codex
    # `-o` enforces it — a `-o` outside the mounts writes nothing (BAC-4 2efa19065ce6476e).
    scripts = [s for _name, s in _fake(ctx).detached if "review-standards" in s]
    assert len(scripts) == 1
    outputs = [Path(m.group(1)) for m in re.finditer(r"(?:^|\s)-o (\S+)", scripts[0])]
    assert len(outputs) == 3  # standards, spec, full
    # Inside the mount, not necessarily at its root: the *mount* is what §9.1 fixes per
    # project, and a run-id subdirectory under it is free — the same shape the clone mount
    # takes, and what keeps two runs of one ticket from colliding.
    assert all(out.is_relative_to(scratch) for out in outputs), outputs
    # And each landed in the run's own directory afterwards.
    for name in ("review-standards.json", "review-spec.json", "review-full.json"):
        assert (ctx.state_dir / "review" / name).exists(), name
    assert not list(scratch.rglob("review-*.json"))  # moved, not copied


def test_every_path_the_review_script_touches_is_inside_a_review_workspace(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structural check the `-o` test above could not make, derived from the spec.

    The existing test asserts the *findings* paths are writable, and it passed throughout
    the defect. What it could not see is everything else the detached script names: the
    prompt it reads with `<`, the event stream and stderr it writes with `>` and `2>`, and
    the wrapper's own `heartbeat` and `exit`. Those pointed at `state/runs/<run>/review/`
    and `ctx.factory_dir`, and **neither is a workspace of the review sandbox** — its spec
    is the project `:ro` plus the per-project scratch, and nothing else.

    Measured on FRO-7 run `b1aa9785bbe44663`, the first real review the daemon ever drove:
    `cannot create .../.factory/run/1/heartbeat: Directory nonexistent` and
    `cannot open .../review/review-standards.prompt: No such file`, then `reviewing ->
    resumable` on a loop. It survived every test because review only became a *detached*
    step in `147dc88`; before that it ran synchronously, needed no heartbeat, and read its
    prompt from the host.

    So this asserts the property rather than the paths: every absolute path the script
    names must be inside a workspace the spec actually mounts. Written by construction
    from `_review_spec`, so a workspace added or removed changes what this allows without
    anyone editing the test.
    """
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    monkeypatch.setattr(review_step, "_tier2_trigger", lambda ctx, h, tier1_has_human=False: None)
    _fake(ctx).review_findings = {"findings": []}

    review_step.run(ctx)

    spec = review_step._review_spec(ctx, review_step._review_scratch(ctx))
    mounts = [Path(w.path) for w in spec.workspaces]
    writable = [Path(w.path) for w in spec.workspaces if not w.readonly]

    script = next(s for _name, s in _fake(ctx).detached if "review-standards" in s)
    named = {Path(tok.strip("'\"")) for tok in re.findall(r"'?/[^\s'\"<>]+", script)}
    # Only paths the factory owns; the script also names binaries like /bin/sh.
    owned = [p for p in named if p.is_relative_to(ctx.home) or p.is_relative_to(ctx.project.path)]
    assert owned, "the script named no factory-owned path; the regex stopped matching"

    outside = [p for p in owned if not any(p.is_relative_to(m) for m in mounts)]
    assert not outside, f"named but not mounted in the review sandbox: {outside}"

    # The liveness files and the reviewer's outputs are *written*, so a read-only mount is
    # not enough for them — the bind-mounted variant of the same defect, where the worktree
    # is present but `:ro` on purpose (§15.2).
    written = [
        p for p in owned if p.name in {"heartbeat", "exit"} or p.suffix in {".jsonl", ".log"}
    ]
    unwritable = [p for p in written if not any(p.is_relative_to(w) for w in writable)]
    assert not unwritable, f"written by the reviewer but not on a writable mount: {unwritable}"


def test_full_review_runs_tier2_on_a_diff_that_would_have_skipped_it(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override, end to end, with the real trigger function in place.

    The fixture's diff is two files and a few dozen lines, which every §15.2 rule
    declines — `test_a_clean_review_opens_no_pr_and_advances` asserts exactly that. So
    the only thing that can make the fan-out run here is the flag.
    """
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    ctx.store.update_run(ctx.run.id, full_review=True)
    ctx.refresh()

    review_step.run(ctx)

    summary = json.loads((ctx.state_dir / "review" / "review-summary.json").read_text())
    assert summary["tier2"] == review_step.FORCED
    assert (ctx.state_dir / "review" / "review-full.json").exists()


def test_the_override_survives_the_process_that_asked_for_it(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is on the run row, not on the `Context`, and this is why.

    Under `factory tick` the review happens in a later process than the one that took
    the flag — usually several ticks later. A flag carried on the command line's context
    would be an override that worked only while a human was watching, which is the exact
    shape of unprovenness it exists to fix.
    """
    _to_reviewing(ctx)
    ctx.store.update_run(ctx.run.id, full_review=True)

    # A fresh reader of the same database, holding nothing the first one held.
    reread = Store(ctx.store.path).run_by_id(ctx.run.id)

    assert reread is not None
    assert reread.full_review is True


def test_a_forced_tier2_says_so_in_the_pull_request_body(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A forced fan-out reported as a triggered one would misstate what the review rules
    # concluded about this diff, which is the only thing the rules are for.
    review_dir = ctx.state_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "review-summary.json").write_text(
        json.dumps({"tier2": review_step.FORCED, "findings": []})
    )

    line = deliver_step._review_summary(ctx)

    assert "Tier 2 ran" in line
    assert "--full-review" in line
    assert "skipped" not in line
