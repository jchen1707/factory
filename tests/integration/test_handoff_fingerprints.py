"""Repair decisions use acceptance evidence, independent of rerun presentation."""

import json
from dataclasses import replace

import pytest

from factory import handoffs
from factory.machine import Blocked
from factory.steps import Context


def record(ctx: Context, output: str, attempt: int) -> dict[str, str]:
    ctx.run = replace(ctx.run, attempt=attempt, worktree=str(ctx.project.path))
    path = ctx.factory_dir / f"run/{attempt}/gates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {"gates": [{"name": "acceptance", "status": "fail", "exit": 1, "outputTail": output}]}
    path.write_text(json.dumps(report))
    handoffs.record_failure(ctx, path, report)
    return {
        "classification": "code",
        "status": "repair",
        "summary": "Fix the failing behavior",
        "reproduction_evidence": f"run/{attempt}/gates.json",
    }


def output(attempt: int, *, actual: int = 401, test: str = "test_login") -> str:
    return (
        f"tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-{attempt}/{test}0')\n"
        f"E   AssertionError: expected 200, got {actual}\n"
        f"FAILED tests/test_auth.py::{test} - AssertionError\n"
        f"========== 1 failed, 3 passed in {attempt}.42s ==========\n"
    )


def test_rerun_timing_and_temporary_paths_do_not_authorize_another_repair(ctx: Context) -> None:
    handoffs.authorize_repair(ctx, record(ctx, output(1), 1), 2)
    with pytest.raises(Blocked, match="failure-without-new-evidence"):
        handoffs.authorize_repair(ctx, record(ctx, output(2), 2), 3)


def test_changed_assertion_evidence_allows_second_repair_but_never_a_third(ctx: Context) -> None:
    handoffs.authorize_repair(ctx, record(ctx, output(1), 1), 2)
    handoffs.authorize_repair(ctx, record(ctx, output(2, actual=403), 2), 3)
    with pytest.raises(Blocked, match="repair-limit"):
        handoffs.authorize_repair(ctx, record(ctx, output(3, actual=500), 3), 4)


def test_changed_failed_test_identity_is_new_evidence(ctx: Context) -> None:
    handoffs.authorize_repair(ctx, record(ctx, output(1), 1), 2)
    handoffs.authorize_repair(ctx, record(ctx, output(2, test="test_logout"), 2), 3)


def test_business_durations_are_not_erased(ctx: Context) -> None:
    handoffs.authorize_repair(ctx, record(ctx, "E AssertionError: request took 1.2s", 1), 2)
    handoffs.authorize_repair(ctx, record(ctx, "E AssertionError: request took 2.4s", 2), 3)


@pytest.mark.parametrize(
    ("classification", "next_action"),
    [
        ("environment", "sandbox adapter"),
        ("stale-authority", "replace the paused run's policy"),
        ("requirements", "missing product decision"),
        ("review-dispute", "disposition"),
    ],
)
def test_non_code_diagnosis_retains_actionable_human_handoff(
    ctx: Context, classification: str, next_action: str
) -> None:
    diagnosis = record(ctx, "failure evidence", 1) | {"classification": classification}
    with pytest.raises(Blocked, match=f"diagnosis-{classification}") as failure:
        handoffs.authorize_repair(ctx, diagnosis, 2)
    assert next_action in str(failure.value)
    retained = json.loads((ctx.factory_dir / "diagnosis-2.json").read_text())
    assert retained["diagnosis"] == diagnosis
    assert next_action in retained["next_action"]
    assert ctx.store.checks(ctx.run.id)[-1]["check_name"] == "diagnosis-routing"
    assert not ctx.store.runtime.db.execute("SELECT * FROM failure_episodes").fetchall()


@pytest.mark.parametrize(
    "names", [(), ("execution-brief.md",), ("execution-brief.md", "test-plan.md")]
)
def test_builder_receives_only_collected_handoff_artifacts(
    ctx: Context, names: tuple[str, ...]
) -> None:
    from factory.steps import implement, plan
    from tests.integration.test_clone_plan_collection import prepare

    attempt = prepare(ctx, True)
    handoff = ctx.factory_dir / "handoff.json"
    handoff.write_text("{}")
    contract = ctx.project.path / ".agents/vendor/harness/docs/agents/consume-execution-handoff.md"
    contract.write_text("Read the supplied handoff and implement one failing-test slice at a time.")
    plans = plan.plan_dir(ctx)
    plans.mkdir(parents=True, exist_ok=True)
    for name in names:
        (plans / name).write_text("# Execution context\nApproved behavior.\n")
    plan.collect(ctx, attempt)
    prompt, _ = implement.build_prompt(ctx)
    assert contract.read_text() in prompt
    for name in ("execution-brief.md", "test-plan.md"):
        assert (str(attempt.path("planning-output") / name) in prompt) == (name in names)
        assert str(plans / name) not in prompt
