"""A collector repair can reuse completed readiness without buying another model turn."""

import json
from pathlib import Path

import pytest

from factory import accounting, driver, recovery
from factory.agent.base import SchemaInvalid
from factory.artifacts import AttemptDir
from factory.machine import Blocked, State
from factory.steps import Context, advance, record_stop
from tests.integration.test_clone_plan_collection import prepare
from tests.integration.test_phase4 import _fake


def parked_readiness(ctx: Context) -> AttemptDir:
    attempt = prepare(ctx, True)
    attempt.path("plan-request.json").write_text(
        '{"contract": "noninteractive-handoff", "diagnosis": false}'
    )
    accounting.begin(ctx, 1, ctx.routing.role("planner"), "plan", attempt.path("plan-events.jsonl"))
    advance(ctx, State.PLANNING)
    ctx.store.finish_attempt(ctx.run.id, 1, State.PLANNING, exit_code=0, outcome="plan-incomplete")
    ctx.store.update_run(ctx.run.id, blocked_reason="plan-incomplete")
    record_stop(ctx, State.BLOCKED, rule="plan-incomplete", detail="missing optional markdown")
    ctx.refresh()
    return attempt


@pytest.mark.parametrize("contract", ["noninteractive-handoff", "ticket-readiness", "test-design"])
def test_resume_recollects_ready_evidence_without_another_planner_and_holds_builder(
    ctx: Context,
    contract: str,
) -> None:
    attempt = parked_readiness(ctx)
    request = {"contract": contract, "diagnosis": False}
    if contract == "test-design":
        from factory.steps.plan import plan_dir

        request.update(role="test_designer", required_artifacts=["test-plan.md"])
        plans = plan_dir(ctx)
        plans.mkdir(parents=True)
        (plans / "test-plan.md").write_text("# Acceptance scenarios\nCreate and list notes.\n")
    attempt.path("plan-request.json").write_text(json.dumps(request))
    original = attempt.path("plan-last-message.json").read_bytes()
    original_schema = attempt.schema.read_text()
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})

    assert recovery.resume(ctx) is State.PLANNING
    assert ctx.state is State.PLANNING
    assert ctx.run.attempt == 1
    assert not _fake(ctx).detached
    row = ctx.store.attempt_row(ctx.run.id, 1, State.PLANNING)
    assert row is not None
    assert row["outcome"] == "planned"
    saved = json.loads(attempt.path("recollection-original.json").read_text())
    assert saved["attempt"]["outcome"] == "plan-incomplete"
    assert saved["schema"] == original_schema
    assert attempt.path("plan-last-message.json").read_bytes() == original
    assert len(ctx.store.runtime.invocations(ctx.run.id)) == 1

    driver.drive(ctx, follow=False)
    assert ctx.state is State.PLANNING
    assert ctx.run.attempt == 1
    assert not _fake(ctx).detached
    assert ctx.store.attempt_row(ctx.run.id, 1, State.IMPLEMENTING) is None


@pytest.mark.parametrize("damage", ["exit", "transcript", "schema", "not-ready", "required-file"])
def test_resume_rechecks_failed_evidence_before_unblocking(ctx: Context, damage: str) -> None:
    attempt = parked_readiness(ctx)
    if damage == "exit":
        attempt.path("plan-exit").write_text("1")
    elif damage == "transcript":
        fixture = Path(__file__).parents[1] / "fixtures/codex-exec-turn-failed.jsonl"
        attempt.path("plan-events.jsonl").write_bytes(fixture.read_bytes())
    elif damage == "schema":
        attempt.path("plan-last-message.json").write_text("{}")
    elif damage == "not-ready":
        result = json.loads(attempt.path("plan-last-message.json").read_text())
        result["status"] = "needs-human"
        attempt.path("plan-last-message.json").write_text(json.dumps(result))
    else:
        request = json.loads(attempt.path("plan-request.json").read_text())
        request.update(role="test_designer", required_artifacts=["test-plan.md"])
        attempt.path("plan-request.json").write_text(json.dumps(request))

    with pytest.raises((Blocked, SchemaInvalid)):
        recovery.resume(ctx)
    assert ctx.state is State.BLOCKED
    assert ctx.run.attempt == 1
    assert not _fake(ctx).detached
    assert ctx.store.attempt_row(ctx.run.id, 1, State.IMPLEMENTING) is None


@pytest.mark.parametrize("damage", ["diagnosis", "missing-request", "unfinished", "policy"])
def test_recollection_refuses_attempts_outside_the_original_readiness_contract(
    ctx: Context, damage: str
) -> None:
    attempt = parked_readiness(ctx)
    if damage == "diagnosis":
        attempt.path("plan-request.json").write_text(
            '{"contract": "noninteractive-handoff", "diagnosis": true}'
        )
    elif damage == "missing-request":
        attempt.path("plan-request.json").unlink()
    elif damage == "unfinished":
        ctx.store.start_attempt(
            ctx.run.id,
            1,
            State.PLANNING,
            sandbox=ctx.project.build_sandbox,
            artifact_dir=str(attempt.root),
        )
    else:
        ctx.store.runtime.snapshot_policy(ctx.run.id, {"profile": "changed"}, replace=True)
    with pytest.raises(Blocked, match="readiness-recollection-unavailable"):
        recovery.resume(ctx)
    assert ctx.state is State.BLOCKED
    assert not _fake(ctx).detached
    assert not attempt.path("recollection-original.json").exists()


def test_explicit_planning_restart_still_requires_approval_for_a_new_attempt(ctx: Context) -> None:
    from factory.execution import AgentApprovalRequired

    parked_readiness(ctx)
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    with pytest.raises(AgentApprovalRequired, match="2:plan:1"):
        recovery.resume(ctx, from_state="planning")
    assert ctx.state is State.BLOCKED
    assert ctx.run.attempt == 1


def test_interrupted_recollection_preserves_original_schema_manifest_and_accounting(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory import steps

    attempt = parked_readiness(ctx)
    old_manifest = '{"files": {}, "produced_by": "old-collector"}\n'
    attempt.manifest.write_text(old_manifest)
    with monkeypatch.context() as patch:

        def crash(*args: object, **kwargs: object) -> None:
            raise RuntimeError("died before transition")

        patch.setattr(steps, "advance", crash)
        with pytest.raises(RuntimeError, match="died before transition"):
            recovery.resume(ctx)
    assert ctx.state is State.BLOCKED
    archive = attempt.path("recollection-original.json").read_bytes()
    saved = json.loads(archive)
    assert saved["manifest"] == old_manifest
    assert saved["attempt"]["outcome"] == "plan-incomplete"
    assert saved["invocation"]["role"] == "plan"

    recovery.resume(ctx)
    assert ctx.state is State.PLANNING
    assert attempt.path("recollection-original.json").read_bytes() == archive
    original_schema = attempt.schema.read_text()
    attempt.schema.write_text('{"description": "Next implementation phase schema"}')
    assert json.loads(archive)["schema"] == original_schema
