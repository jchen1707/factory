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
from factory.delivery import github, gitlab, sandbox_gitlab
from factory.machine import Blocked, State
from factory.registry import SandboxDelivery
from factory.steps import Context, advance, redphase
from factory.steps import deliver as deliver_step
from factory.steps import review as review_step
from factory.store import Store
from tests.integration.conftest import (
    HOME,
    FakeLinear,
    FakeSandbox,
    _seed_vendored_review_tree,
    advance_state,
    git,
)
from tests.integration.test_pipeline import _fake, _to_verifying

# --------------------------------------------------------------------------------
# review transitions
# --------------------------------------------------------------------------------


def _to_reviewing(ctx: Context) -> None:
    _to_verifying(ctx)
    advance_state(ctx)
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

    advance_state(ctx)

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
        advance_state(ctx)
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

    advance_state(ctx)

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

    advance_state(ctx)

    assert ctx.state is State.AWAITING_HUMAN
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    assert "test-weakening" in linear.comments[-1]


def _accept(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    *,
    note: str | None = None,
    review_finding: bool = False,
) -> int:
    """`factory accept <ticket>` against the fixture home, with only Linear faked."""
    target = ctx.home / "config" / "models.toml"
    if not target.exists():
        target.write_text((HOME / "config" / "models.toml").read_text())
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "SbxAdapter", FakeSandbox)
    monkeypatch.setattr(cli, "LinearClient", lambda: ctx.linear)
    return cli.cmd_accept(
        argparse.Namespace(
            ticket=ctx.run.linear_id,
            note=note,
            review_finding=review_finding,
        )
    )


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
    advance_state(ctx)
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

    advance_state(ctx)

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
    advance_state(ctx)
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

    advance_state(ctx)

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


def test_accept_review_finding_records_the_dispute_and_enters_delivery(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {
        "findings": [
            {
                "severity": "high",
                "file": "src/app.py",
                "line": 9,
                "summary": "requires hardening outside this POC",
            }
        ]
    }
    from factory.steps import block as block_step

    with pytest.raises(Blocked) as caught:
        advance_state(ctx)
    block_step.record(ctx, caught.value.reason, caught.value.detail)
    assert ctx.state is State.BLOCKED

    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app.py"])
    monkeypatch.setattr(github, "push", lambda wt, b: None)
    monkeypatch.setattr(github, "find_pr", lambda wt, b: None)
    bodies: list[str] = []

    def create_pr(worktree: Path, **kwargs: object) -> str:
        body_file = kwargs["body_file"]
        assert isinstance(body_file, Path)
        bodies.append(body_file.read_text(encoding="utf-8"))
        return "https://github.com/jchen1707/python-harness/pull/49"

    monkeypatch.setattr(github, "create_pr", create_pr)
    code = _accept(
        ctx,
        monkeypatch,
        note="James accepts this as post-POC hardening.",
        review_finding=True,
    )

    assert code == 0
    ctx.refresh()
    assert ctx.state is State.AWAITING_HUMAN
    assert ctx.run.pr_url == "https://github.com/jchen1707/python-harness/pull/49"
    transition = next(
        row
        for row in ctx.store.transitions(ctx.run.id)
        if row["rule"] == "accept-review-finding-is-james"
    )
    assert (
        transition["from_state"],
        transition["to_state"],
        transition["actor"],
        transition["rule"],
    ) == (
        str(State.BLOCKED),
        str(State.PR_READY),
        "human",
        "accept-review-finding-is-james",
    )
    accepted = [
        row
        for row in ctx.store.checks(ctx.run.id)
        if row["check_name"] == review_step.REVIEW_FINDING_ACCEPTED
    ]
    assert len(accepted) == 1
    assert accepted[0]["reason"] == review_step.REVIEW_FINDING
    assert accepted[0]["detail"] == "James accepts this as post-POC hardening."
    assert len(bodies) == 1
    body = bodies[0]
    assert "Disputed review findings" in body
    assert "requires hardening outside this POC" in body
    assert "James accepts this as post-POC hardening." in body


def test_accept_review_finding_requires_an_explicit_note(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    from factory.steps import block as block_step

    block_step.record(ctx, review_step.REVIEW_FINDING, "one high finding")

    code = _accept(ctx, monkeypatch, review_finding=True)

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_blocked_to_delivery_cannot_advance_automatically(ctx: Context) -> None:
    _to_reviewing(ctx)
    from factory.steps import block as block_step

    block_step.record(ctx, review_step.REVIEW_FINDING, "one high finding")

    with pytest.raises(Blocked) as caught:
        advance(ctx, State.PR_READY)

    assert caught.value.reason == "requires-human"
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_accept_review_finding_requires_recorded_blocking_review_evidence(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    from factory.steps import block as block_step

    ctx.store.start_attempt(
        ctx.run.id,
        ctx.run.attempt,
        State.REVIEWING,
        sandbox=ctx.project.review_sandbox,
        artifact_dir=str(ctx.state_dir / "review"),
    )
    ctx.store.finish_attempt(
        ctx.run.id,
        ctx.run.attempt,
        State.REVIEWING,
        exit_code=0,
        outcome="ran",
    )
    block_step.record(ctx, review_step.REVIEW_FINDING, "claimed without a review summary")

    code = _accept(
        ctx,
        monkeypatch,
        note="This must not be enough on its own.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED
    assert not any(
        row["check_name"] == review_step.REVIEW_FINDING_ACCEPTED
        for row in ctx.store.checks(ctx.run.id)
    )


def test_accept_review_finding_requires_a_passing_gate_verdict(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {
        "findings": [
            {
                "severity": "high",
                "file": "src/app.py",
                "line": 9,
                "summary": "requires hardening outside this POC",
            }
        ]
    }
    from factory.steps import block as block_step

    with pytest.raises(Blocked) as caught:
        advance_state(ctx)
    block_step.record(ctx, caught.value.reason, caught.value.detail)
    gates_path = ctx.factory_dir / "run" / str(ctx.run.attempt) / "gates.json"
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    gates["verdict"] = "fail"
    gates_path.write_text(json.dumps(gates), encoding="utf-8")

    code = _accept(
        ctx,
        monkeypatch,
        note="A dispute cannot waive failed gates.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_accept_review_finding_requires_a_schema_valid_gate_report(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {
        "findings": [
            {
                "severity": "high",
                "file": "src/app.py",
                "line": 9,
                "summary": "requires hardening outside this POC",
            }
        ]
    }
    from factory.steps import block as block_step

    with pytest.raises(Blocked) as caught:
        advance_state(ctx)
    block_step.record(ctx, caught.value.reason, caught.value.detail)
    gates_path = ctx.factory_dir / "run" / str(ctx.run.attempt) / "gates.json"
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    del gates["schemaVersion"]
    gates_path.write_text(json.dumps(gates), encoding="utf-8")

    code = _accept(
        ctx,
        monkeypatch,
        note="A partial gate document cannot authorize delivery.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_accept_review_finding_requires_critical_or_high_review_evidence(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {
        "findings": [
            {
                "severity": "medium",
                "file": "src/app.py",
                "line": 9,
                "summary": "ordinary non-blocking feedback",
            }
        ]
    }
    advance_state(ctx)
    assert ctx.state is State.PR_READY
    from factory.steps import block as block_step

    block_step.record(ctx, review_step.REVIEW_FINDING, "claimed a blocking review")

    code = _accept(
        ctx,
        monkeypatch,
        note="This must not waive non-blocking feedback through the wrong path.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_accept_review_finding_requires_a_canonical_review_summary(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {
        "findings": [
            {
                "severity": "high",
                "file": "src/app.py",
                "line": 9,
                "summary": "requires hardening outside this POC",
            }
        ]
    }
    from factory.steps import block as block_step

    with pytest.raises(Blocked) as caught:
        advance_state(ctx)
    block_step.record(ctx, caught.value.reason, caught.value.detail)
    summary_path = ctx.state_dir / "review" / "review-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["findings"][0]["severity"] = "HIGH"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    code = _accept(
        ctx,
        monkeypatch,
        note="Noncanonical review evidence cannot authorize delivery.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_accept_review_finding_refuses_a_conflicting_retry_note(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _seed_vendored_review_tree(Path(ctx.run.worktree or ""))
    _fake(ctx).review_findings = {
        "findings": [
            {
                "severity": "high",
                "file": "src/app.py",
                "line": 9,
                "summary": "requires hardening outside this POC",
            }
        ]
    }
    from factory.steps import block as block_step

    with pytest.raises(Blocked) as caught:
        advance_state(ctx)
    block_step.record(ctx, caught.value.reason, caught.value.detail)
    ctx.store.record_check(
        ctx.run.id,
        ctx.run.attempt,
        review_step.REVIEW_FINDING_ACCEPTED,
        "accepted",
        reason=review_step.REVIEW_FINDING,
        detail="The originally recorded judgement.",
    )

    code = _accept(
        ctx,
        monkeypatch,
        note="A different judgement on retry.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


def test_accept_review_finding_refuses_a_different_blocker(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    from factory.steps import block as block_step

    block_step.record(ctx, "secret-in-artifact", "a security boundary fired")

    code = _accept(
        ctx,
        monkeypatch,
        note="A review dispute cannot waive the artifact scanner.",
        review_finding=True,
    )

    assert code == 1
    ctx.refresh()
    assert ctx.state is State.BLOCKED


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
    advance_state(ctx)
    assert ctx.state is State.PR_READY


def _capture(sink: list[list[str]], *, stdout: str = "") -> object:
    """Stand in for `subprocess.run` and keep the argv the delivery module built."""

    def run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        sink.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, stdout=stdout, stderr="")

    return run


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
    monkeypatch.setattr(github, "push", lambda wt, b: pushed.append(b))

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
    monkeypatch.setattr(github, "push", lambda wt, b: None)
    monkeypatch.setattr(github, "find_pr", lambda wt, b: None)
    # `create_pr` is the seam the whole item turns on, so the real argv is captured
    # rather than the wrapper stubbed away — the draft flag is the one token that has to
    # be gone, and a stub that swallows argv could not tell you (§24.8).
    argv: list[list[str]] = []
    monkeypatch.setattr(
        github.subprocess,
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
    monkeypatch.setattr(github, "push", lambda wt, b: None)
    monkeypatch.setattr(
        github,
        "find_pr",
        lambda wt, b: "https://github.com/jchen1707/python-harness/pull/12",
    )
    created: list[str] = []
    edited: list[int] = []
    monkeypatch.setattr(github, "create_pr", lambda wt, **kw: created.append(kw["head"]))
    monkeypatch.setattr(github, "edit_pr", lambda wt, n, **kw: edited.append(n))

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
        deliver_step.pr_body,
        "render_pr_body",
        lambda **kw: "Fixes BAC-4\n\nleaked ghp_" + "A" * 36,
    )
    pushed: list[str] = []
    monkeypatch.setattr(github, "push", lambda wt, b: pushed.append(b))

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
        "start",
        lambda _ctx, **_kw: (_ for _ in ()).throw(repo.GitError("git apply failed: corrupt patch")),
    )

    with pytest.raises(Blocked) as caught:
        cli._drive_foreground(ctx, follow=True, poll=0)

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

    advance_state(ctx)

    scratch = review_step._review_scratch(ctx)
    # The fan-out ran detached; the per-axis `-o` paths are baked into the script. Every
    # one must sit inside the sandbox's writable scratch mount, the way the real codex
    # `-o` enforces it — a `-o` outside the mounts writes nothing (BAC-4 2efa19065ce6476e).
    scripts = [s for name, s in _fake(ctx).detached if name == ctx.project.review_sandbox]
    assert len(scripts) == 3  # standards, spec, full, admitted individually
    outputs = [
        Path(m.group(1)) for script in scripts for m in re.finditer(r"(?:^|\s)-o (\S+)", script)
    ]
    assert len(outputs) == 3
    # Inside the mount, not necessarily at its root: the *mount* is what §9.1 fixes per
    # project, and a run-id subdirectory under it is free — the same shape the clone mount
    # takes, and what keeps two runs of one ticket from colliding.
    assert all(out.is_relative_to(scratch) for out in outputs), outputs
    # And each landed in the run's own directory afterwards.
    plan = review_step._read_plan(ctx.state_dir / "review")
    assert all(Path(axis["out"]).exists() for axis in plan["axes"])
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

    advance_state(ctx)

    spec = review_step._review_spec(ctx, review_step._review_scratch(ctx))
    mounts = [Path(w.path) for w in spec.workspaces]
    writable = [Path(w.path) for w in spec.workspaces if not w.readonly]

    scripts = [s for name, s in _fake(ctx).detached if name == ctx.project.review_sandbox]
    named = {
        Path(tok.strip("'\""))
        for script in scripts
        for tok in re.findall(r"'?/[^\s'\"<>]+", script)
    }
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

    advance_state(ctx)

    summary = json.loads((ctx.state_dir / "review" / "review-summary.json").read_text())
    assert summary["tier2"] == review_step.FORCED
    plan = review_step._read_plan(ctx.state_dir / "review")
    full = next(axis for axis in plan["axes"] if axis["label"] == "full")
    assert Path(full["out"]).exists()


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


# --------------------------------------------------------------------------------
# in-VM delivery — the §13.2 reversal, and the lifecycle that bounds it
# --------------------------------------------------------------------------------

PLACEHOLDER_ENV = "FACTORY_GITLAB_TOKEN"
MR_URL = "https://172.18.194.183/nexus-core/ran-ai-agents/nemoclaw-test/-/merge_requests/7"


def _delivering_from_the_sandbox(ctx: Context) -> None:
    """Point the fixture's project at in-VM delivery, and provision the placeholder.

    Both halves, because either alone is a state the machine must refuse: a declaration
    with no secret blocks, and a secret with no declaration is a capability nothing asked
    for and `capability_secrets` still rejects.
    """
    ctx.project = replace(
        ctx.project,
        forge="gitlab",
        sandbox_delivery=SandboxDelivery(
            api_url="https://172.18.194.183",
            project_path="nexus-core/ran-ai-agents/nemoclaw-test",
            placeholder_env=PLACEHOLDER_ENV,
        ),
    )
    _fake(ctx).custom_secrets[PLACEHOLDER_ENV] = "sbx-cs-NOTAREALPLACEHOLDER"


def test_a_declared_project_pushes_and_opens_its_merge_request_from_the_sandbox(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reversal, end to end through the step rather than the adapter.

    The host must run no forge command at all: if `subprocess.run` is reached, delivery
    went out over the host's SSH key and the registry's declaration did nothing.
    """
    _to_pr_ready(ctx, monkeypatch)
    _delivering_from_the_sandbox(ctx)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    for module in (github, gitlab):
        # Both, because the project's forge is `gitlab`: watching only the GitHub module
        # would let a regression that fell back to the *host* GitLab adapter pass, which
        # is precisely the fallback `for_delivery` exists to make impossible.
        monkeypatch.setattr(
            module.subprocess, "run", lambda *a, **k: pytest.fail("delivery ran on the host")
        )
    fake = _fake(ctx)
    fake.api_replies = [(0, json.dumps([])), (0, json.dumps({"web_url": MR_URL}))]

    deliver_step.run(ctx)

    assert ctx.state is State.AWAITING_HUMAN
    assert ctx.store.run_by_id(ctx.run.id).pr_url == MR_URL  # type: ignore[union-attr]
    scripts = " ".join(" ".join(argv) for _, argv in fake.sync_calls)
    assert "git push" in scripts
    assert MR_URL in (ctx.linear.comments[-1])  # type: ignore[attr-defined]


def test_the_delivery_credential_is_removed_even_when_the_push_fails(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.5 keeps the sandbox for hours after the run. A credential file left behind is
    one the *next* ticket's agent inherits, in a sandbox nobody would think to look in —
    and a failed delivery is exactly the path where a `finally` is easy to omit."""
    _to_pr_ready(ctx, monkeypatch)
    _delivering_from_the_sandbox(ctx)
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    monkeypatch.setattr(
        sandbox_gitlab.SandboxGitlabForge,
        "push",
        lambda self, wt, b: (_ for _ in ()).throw(sandbox_gitlab.SandboxGitlabError("no")),
    )

    with pytest.raises(sandbox_gitlab.SandboxGitlabError):
        deliver_step.run(ctx)

    scripts = [" ".join(argv) for _, argv in _fake(ctx).sync_calls]
    assert any(script.startswith("sh -c rm -f") for script in scripts), (
        f"the credential outlived the failed delivery: {scripts[-3:]}"
    )


def test_delivery_blocks_by_name_when_the_placeholder_was_never_provisioned(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the placeholder the push authenticates as nobody. A named block that says
    how to provision it beats a 401 from inside a VM at the end of a paid run."""
    _to_pr_ready(ctx, monkeypatch)
    _delivering_from_the_sandbox(ctx)
    _fake(ctx).custom_secrets.clear()
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])

    with pytest.raises(Blocked) as caught:
        deliver_step.run(ctx)

    assert caught.value.reason == "sandbox-delivery-unprovisioned"
    assert PLACEHOLDER_ENV in str(caught.value)
    assert ctx.state is State.PR_READY


def test_approval_change_between_review_axes_preserves_completed_observation(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import execution

    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    first = review_step.start(ctx)
    assert first is not None
    first_dir, _ = first
    invocations = ctx.store.runtime.invocations(ctx.run.id)
    review_invocations = [i for i in invocations if i["role"].startswith("review:")]
    assert len(review_invocations) == 1
    assert review_invocations[0]["role"] == "review:standards"
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    with pytest.raises(execution.AgentApprovalRequired, match=f"{ctx.run.attempt}:review:2"):
        review_step.collect(ctx, first_dir, ctx.run.attempt)
    plan = review_step._read_plan(ctx.state_dir / "review")
    assert plan["axes"][0]["complete"]
    assert "invocation_id" not in plan["axes"][1]
    assert Path(plan["axes"][0]["out"]).exists()
    ctx.store.runtime.approve(ctx.run.id, f"{ctx.run.attempt}:review:2")
    review_step.collect(ctx, first_dir, ctx.run.attempt)
    plan = review_step._read_plan(ctx.state_dir / "review")
    second_dir = review_step.AttemptDir(Path(plan["axes"][1]["artifact_dir"]))
    review_step.collect(ctx, second_dir, ctx.run.attempt)
    assert ctx.state is State.PR_READY
    assert (
        len(
            [
                i
                for i in ctx.store.runtime.invocations(ctx.run.id)
                if i["role"].startswith("review:")
            ]
        )
        == 2
    )


def test_first_axis_spend_blocks_the_next_actual_model_launch(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    ctx.routing = replace(ctx.routing, usd_per_run=1)
    # Preserve the fake process runner, while supplying a normalized app-server usage record.
    monkeypatch.setattr(ctx.agent, "report", {}, raising=False)
    (ctx.home / "config/prices.toml").write_text((HOME / "config/prices.toml").read_text())
    first = review_step.start(ctx)
    assert first is not None
    plan = review_step._read_plan(ctx.state_dir / "review")
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    Path(plan["axes"][0]["scratch_events"]).write_text(
        json.dumps(
            {
                "type": "factory.usage",
                "usage": usage,
                "complete": True,
                "thread_total": {},
                "pricing_complete": True,
                "requests": [
                    {
                        "model": "gpt-5.6-sol",
                        "usage": usage,
                        "observed_at": 1788652800,
                        "service_tier": "standard",
                        "long_context": False,
                    }
                ],
            }
        )
        + "\n"
    )
    with pytest.raises(Blocked, match="budget-exceeded"):
        review_step.collect(ctx, first[0], ctx.run.attempt)
    assert ctx.store.known_spend(ctx.run.id) == 4
    assert (
        len(
            [
                i
                for i in ctx.store.runtime.invocations(ctx.run.id)
                if i["role"].startswith("review:")
            ]
        )
        == 1
    )
    assert len([1 for name, _ in _fake(ctx).detached if name == ctx.project.review_sandbox]) == 1


def test_review_retry_preserves_completed_axes_and_separate_invocation_evidence(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    first = review_step.start(ctx)
    assert first is not None
    review_step.collect(ctx, first[0], ctx.run.attempt)
    plan = review_step._read_plan(ctx.state_dir / "review")
    completed_path = Path(plan["axes"][0]["out"])
    second = plan["axes"][1]
    second_invocation = second["invocation_id"]
    Path(second["scratch_out"]).unlink()
    with pytest.raises(Blocked, match="review-schema-invalid"):
        review_step.collect(
            ctx, review_step.AttemptDir(Path(second["artifact_dir"])), ctx.run.attempt
        )
    advance(ctx, State.RESUMABLE, rule="review-process-interrupted")
    retry = review_step.start(ctx)
    assert retry is not None
    assert completed_path.exists()
    plan = review_step._read_plan(ctx.state_dir / "review")
    assert plan["axes"][0]["complete"]
    assert plan["axes"][1]["invocation_id"] != second_invocation
    assert plan["axes"][1]["history"][0]["invocation_id"] == second_invocation
    review_step.collect(ctx, retry[0], ctx.run.attempt)
    assert ctx.state is State.PR_READY
    roles = [
        i["role"]
        for i in ctx.store.runtime.invocations(ctx.run.id)
        if i["role"].startswith("review:")
    ]
    assert roles == ["review:standards", "review:spec", "review:spec"]


def test_legacy_whole_suite_plan_remains_collectible(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    first = review_step.start(ctx)
    assert first is not None
    review_step.collect(ctx, first[0], ctx.run.attempt)
    plan = review_step._read_plan(ctx.state_dir / "review")
    plan.pop("schemaVersion")
    review_step._save_plan(ctx, plan)
    second = review_step.AttemptDir(Path(plan["axes"][1]["artifact_dir"]))
    review_step.collect(ctx, second, ctx.run.attempt)
    assert ctx.state is State.PR_READY


def test_approval_wait_does_not_spend_the_next_reviewers_execution_timeout(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import execution
    from factory.steps import reap

    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    first = review_step.start(ctx)
    assert first is not None
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    with pytest.raises(execution.AgentApprovalRequired):
        review_step.collect(ctx, first[0], ctx.run.attempt)
    first_row = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.REVIEWING)
    assert first_row is not None
    old_start = first_row["started_at"]
    later = old_start + ctx.timeout_for(State.REVIEWING) + 60
    monkeypatch.setattr(reap.time, "time", lambda: later)
    ctx.store.runtime.approve(ctx.run.id, f"{ctx.run.attempt}:review:2")
    review_step.collect(ctx, first[0], ctx.run.attempt)
    assert reap._overrun_seconds(ctx, State.REVIEWING) is None
    monkeypatch.setattr(reap.time, "time", lambda: later + ctx.timeout_for(State.REVIEWING) + 1)
    assert reap._overrun_seconds(ctx, State.REVIEWING) == 1


def test_nonzero_reviewer_exit_cannot_authorize_another_axis(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_reviewing(ctx)
    _stub_redphase(monkeypatch)
    _fake(ctx).exit_code = 1
    first = review_step.start(ctx)
    assert first is not None
    # A valid-looking output file cannot erase the process failure.
    with pytest.raises(Blocked, match="review-agent-failed"):
        review_step.collect(ctx, first[0], ctx.run.attempt)
    assert (
        len(
            [
                i
                for i in ctx.store.runtime.invocations(ctx.run.id)
                if i["role"].startswith("review:")
            ]
        )
        == 1
    )
