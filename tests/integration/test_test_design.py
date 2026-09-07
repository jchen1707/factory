"""Separate test design must produce usable scenarios before the builder starts."""

import json

import pytest

from factory.steps import Context, plan
from tests.integration.test_clone import _to_worktree_ready


def test_selected_test_designer_receives_its_contract_and_snapshots_requirements(
    ctx: Context,
) -> None:
    _to_worktree_ready(ctx)
    contracts = ctx.project.path / ".agents/vendor/harness/docs/agents"
    (contracts / "test-design.md").write_text("Define acceptance scenarios and test boundaries.")
    (contracts / "test-design.json").write_text('{"required_artifacts": ["test-plan.md"]}')
    ctx.store.runtime.configure("project", ctx.project.name, {"test_design": True})
    started = plan.start(ctx)
    assert started is not None
    attempt, _, _ = started
    assert (
        "Define acceptance scenarios and test boundaries."
        in attempt.path("plan-prompt.md").read_text()
    )
    request = json.loads(attempt.path("plan-request.json").read_text())
    assert request["role"] == "test_designer"
    assert request["required_artifacts"] == ["test-plan.md"]
    (invocation,) = ctx.store.runtime.invocations(ctx.run.id)
    assert invocation["role"] == "plan"
    assert invocation["metadata"]["semantic_role"] == "test_designer"


def test_test_design_cannot_return_ready_without_its_scenarios(ctx: Context) -> None:
    from factory.machine import Blocked
    from tests.integration.test_clone_plan_collection import prepare

    attempt = prepare(ctx, True)
    attempt.path("plan-request.json").write_text(
        json.dumps(
            {"diagnosis": False, "role": "test_designer", "required_artifacts": ["test-plan.md"]}
        )
    )
    with pytest.raises(Blocked, match="plan-incomplete"):
        plan.collect(ctx, attempt)


def test_test_design_collection_uses_snapshotted_requirements_and_retains_scenarios(
    ctx: Context,
) -> None:
    from tests.integration.test_clone_plan_collection import prepare

    attempt = prepare(ctx, True)
    attempt.path("plan-request.json").write_text(
        json.dumps(
            {"diagnosis": False, "role": "test_designer", "required_artifacts": ["test-plan.md"]}
        )
    )
    # A later settings edit cannot change the invocation that already ran.
    ctx.store.runtime.configure("project", ctx.project.name, {"test_design": False})
    plans = plan.plan_dir(ctx)
    plans.mkdir(parents=True, exist_ok=True)
    content = "# Scenarios\nPublic create/list interface; isolated database; empty and invalid title cases.\n"
    (plans / "test-plan.md").write_text(content)
    plan.collect(ctx, attempt)
    assert attempt.path("planning-output/test-plan.md").read_text() == content
    assert not attempt.path("planning-output/execution-brief.md").exists()


@pytest.mark.parametrize(
    "required", [None, [], "test-plan.md", ["../outside.md"], ["/outside.md"], [{}]]
)
def test_collection_rejects_malformed_or_escaping_artifact_requests(
    ctx: Context, required: object
) -> None:
    from factory.machine import Blocked
    from tests.integration.test_clone_plan_collection import prepare

    attempt = prepare(ctx, True)
    attempt.path("plan-request.json").write_text(
        json.dumps(
            {
                "diagnosis": False,
                "role": "test_designer",
                "required_artifacts": required,
            }
        )
    )
    with pytest.raises(Blocked, match="workflow-contract-invalid"):
        plan.collect(ctx, attempt)
    assert not attempt.path("planning-output").exists()


@pytest.mark.parametrize("change", [{"role": "planner"}, {"required_artifacts": []}, None])
def test_candidate_cannot_weaken_the_host_snapshotted_design_contract(
    ctx: Context, change: dict | None
) -> None:
    from factory.machine import Blocked

    _to_worktree_ready(ctx)
    contracts = ctx.project.path / ".agents/vendor/harness/docs/agents"
    (contracts / "test-design.md").write_text("Define acceptance scenarios and test boundaries.")
    (contracts / "test-design.json").write_text('{"required_artifacts": ["test-plan.md"]}')
    ctx.store.runtime.configure("project", ctx.project.name, {"test_design": True})
    started = plan.start(ctx)
    assert started is not None
    attempt, _, _ = started
    attempt.path("plan-events.jsonl").write_text("")
    attempt.path("plan-stderr.log").write_text("")
    attempt.path("plan-exit").write_text("0")
    attempt.path("plan-last-message.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "classification": "ready",
                "summary": "Ready",
                "acceptance_behavior": "CRUD",
                "reproduction_evidence": "",
            }
        )
    )
    request_path = attempt.path("plan-request.json")
    if change is None:
        request_path.unlink()
    else:
        request_path.write_text(json.dumps(json.loads(request_path.read_text()) | change))
    with pytest.raises(Blocked, match="handoff-request-changed"):
        plan.collect(ctx, attempt)
    assert not attempt.path("planning-output").exists()


def test_null_legacy_request_cannot_skip_readiness_validation(ctx: Context) -> None:
    from factory.machine import Blocked
    from tests.integration.test_clone_plan_collection import prepare

    attempt = prepare(ctx, True)
    attempt.path("plan-request.json").write_text("null")
    with pytest.raises(Blocked, match="handoff-request-invalid"):
        plan.collect(ctx, attempt)
