"""§22 rows F27, F28 and F30 — the red-phase replay's three blocking outcomes.

`tests/unit/test_redphase.py` covers `_classify`, the pure judgement about *which failure
kind is a real red phase*. That is not the same claim as this file's: what is asserted here
is that `replay` itself **raises** on the two dishonest-test cases and **never rounds a
check that could not run up to a pass**. §23 requires the first two to be unreachable from
any configuration file, so the last test sets every `redphase` knob to its loosest value
and confirms the block still fires.

These are the rows the plan calls "test honesty", and they are the ones a factory that
optimised for green would quietly lose first.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from factory.machine import Blocked, State
from factory.registry import RedPhase
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import redphase as redphase_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import advance_state, git


def _to_verifying(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    advance_state(ctx, until=State.VERIFYING)
    assert ctx.state is State.VERIFYING


def _declare_tests(ctx: Context) -> None:
    assert ctx.harness is not None
    ctx.harness = replace(ctx.harness, tests=("tests",))


def _commit_a_test(ctx: Context) -> None:
    """A test file inside the declared pathspec, so the replay gets past its first guard."""
    tests_dir = ctx.worktree / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_health.py").write_text("def test_health() -> None:\n    assert True\n")
    git(ctx.worktree, "add", "-A")
    git(ctx.worktree, "commit", "-m", "test: a health check")


def _stub_scratch(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scratch worktree is real git and already has its own tests; these rows are about
    the verdict the replay reaches, so the checkout/apply/teardown is stubbed out."""
    monkeypatch.setattr(redphase_step.repo, "add_detached_worktree", lambda r, p, ref: None)
    monkeypatch.setattr(redphase_step.repo, "apply_patch", lambda wt, patch: None)
    monkeypatch.setattr(redphase_step.repo, "remove_worktree", lambda r, p, force=False: None)


# --------------------------------------------------------------------------------
# F27 — a test written only to pass
# --------------------------------------------------------------------------------


def test_f27_a_test_that_passes_at_the_base_ref_blocks_the_run(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The new test passes *without* the implementation, so it asserts something already
    # true and proves nothing about the new behaviour. §22 F27: the run blocks and the PR
    # does not open. This is the headline case the whole replay exists for.
    _to_verifying(ctx)
    _declare_tests(ctx)
    _commit_a_test(ctx)
    _stub_scratch(ctx, monkeypatch)
    monkeypatch.setattr(redphase_step, "_classify", lambda completed, files: ("green", "1 passed"))

    with pytest.raises(Blocked) as caught:
        redphase_step.replay(ctx)

    assert caught.value.reason == "test-proves-nothing"


def test_f27_is_not_reachable_from_any_configuration_file(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # §23: "the two blocking cases are not reachable from any config file. Proven by a test
    # that sets every `redphase` key to its loosest value and confirms `test-proves-nothing`
    # still blocks." The `inconclusive` knob governs the *inconclusive* verdict only; a
    # green replay is not a judgement call and no setting may soften it.
    _to_verifying(ctx)
    _declare_tests(ctx)
    _commit_a_test(ctx)
    _stub_scratch(ctx, monkeypatch)
    monkeypatch.setattr(redphase_step, "_classify", lambda completed, files: ("green", "1 passed"))
    loosest = replace(
        ctx.registry.defaults,
        redphase=RedPhase(inconclusive="report", inconclusive_alarm_pct=100),
    )
    ctx.registry = replace(ctx.registry, defaults=loosest)

    with pytest.raises(Blocked) as caught:
        redphase_step.replay(ctx)

    assert caught.value.reason == "test-proves-nothing"


# --------------------------------------------------------------------------------
# F28 — a behaviour change with no test at all
# --------------------------------------------------------------------------------


def test_f28_a_behaviour_change_touching_no_test_file_blocks(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The agent reported `behaviour_changed: true` and the diff touches no file in the
    # declared `tests` pathspecs. There is no red phase to replay because there is no test,
    # which is the failure — not an excuse to skip the check.
    _to_verifying(ctx)
    _declare_tests(ctx)
    _stub_scratch(ctx, monkeypatch)
    # No test file is committed, so the diff against the pathspec is empty.

    with pytest.raises(Blocked) as caught:
        redphase_step.replay(ctx)

    assert caught.value.reason == "behaviour-change-without-test"


def test_f28_is_not_reachable_from_any_configuration_file(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _to_verifying(ctx)
    _declare_tests(ctx)
    _stub_scratch(ctx, monkeypatch)
    loosest = replace(
        ctx.registry.defaults,
        redphase=RedPhase(inconclusive="report", inconclusive_alarm_pct=100),
    )
    ctx.registry = replace(ctx.registry, defaults=loosest)

    with pytest.raises(Blocked) as caught:
        redphase_step.replay(ctx)

    assert caught.value.reason == "behaviour-change-without-test"


def test_a_change_with_no_behaviour_change_needs_no_test(ctx: Context) -> None:
    # The mirror, and the reason F28 cannot simply be "always require a test": a docs-only
    # or config change has nothing to prove about runtime behaviour, and blocking it would
    # make the guard something to route around rather than something to trust.
    _to_verifying(ctx)
    _declare_tests(ctx)
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    import json

    payload = json.loads((attempt_dir / "last-message.json").read_text())
    payload["behaviour_changed"] = False
    (attempt_dir / "last-message.json").write_text(json.dumps(payload))

    assert redphase_step.replay(ctx) == "proceed"


# --------------------------------------------------------------------------------
# F30 — the check could not be assembled: `unavailable`, never a pass
# --------------------------------------------------------------------------------


def test_f30_an_absent_tests_key_reports_unavailable_and_never_a_pass(ctx: Context) -> None:
    # §22 F30: with no `tests` pathspec the replay cannot be assembled at all. It reports
    # `unavailable` and the run continues — but the *record* must say the check did not
    # run, so the PR body cannot present it as a pass. That distinction is the whole row.
    _to_verifying(ctx)
    assert ctx.harness is not None
    ctx.harness = replace(ctx.harness, tests=())

    assert redphase_step.replay(ctx) == "proceed"

    checks = {row["check_name"]: row for row in ctx.store.checks(ctx.run.id)}
    assert "redphase" in checks
    assert checks["redphase"]["status"] == "unavailable"
    assert checks["redphase"]["status"] != "pass"
    assert "declares no `tests` pathspec" in str(checks["redphase"]["reason"])


def test_f30_a_config_with_no_test_gate_also_reports_unavailable(ctx: Context) -> None:
    # The other half of "could not be assembled": a `tests` pathspec exists but no gate of
    # `kind: test` is declared, so there is no command to replay with.
    _to_verifying(ctx)
    _declare_tests(ctx)
    assert ctx.harness is not None
    ctx.harness = replace(
        ctx.harness, gates=tuple(g for g in ctx.harness.gates if g.kind != "test")
    )

    assert redphase_step.replay(ctx) == "proceed"

    checks = {row["check_name"]: row for row in ctx.store.checks(ctx.run.id)}
    assert checks["redphase"]["status"] == "unavailable"
    assert "no `kind: test` gate" in str(checks["redphase"]["reason"])
