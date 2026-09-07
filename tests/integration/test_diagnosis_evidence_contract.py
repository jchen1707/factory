"""The host supplies the exact reproduction identity required by repair admission."""

import hashlib
import json

import pytest

from factory import accounting, handoffs
from factory.machine import Blocked
from factory.steps import Context, plan
from tests.integration.test_phase4 import _to_worktree


def test_diagnosis_start_binds_handoff_request_and_schema_to_recorded_verifier(
    ctx: Context,
) -> None:
    _to_worktree(ctx)
    ctx.store.update_run(ctx.run.id, attempt=1)
    ctx.refresh()
    evidence = ctx.factory_dir / "run/1/gates.json"
    evidence.parent.mkdir(parents=True)
    report = {
        "gates": [
            {"name": "acceptance", "status": "fail", "exit": 1, "outputTail": "expected 4, got 3"}
        ]
    }
    evidence.write_text(json.dumps(report))
    handoffs.record_failure(ctx, evidence, report)

    started = plan.start(ctx)

    assert started is not None
    attempt = started[0]
    handoff = json.loads((ctx.factory_dir / "handoff.json").read_text())
    request = json.loads(attempt.path("plan-request.json").read_text())
    for document in (handoff, request):
        assert document["reproduction_evidence"] == "run/1/gates.json"
        assert document["reproduction_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
    invocation = ctx.store.runtime.invocation(accounting.key(ctx, ctx.run.attempt, "plan"))
    assert invocation is not None
    assert invocation["metadata"]["handoff_contract"] == request
    schema = json.loads(attempt.schema.read_text())
    assert schema["properties"]["reproduction_evidence"]["enum"] == ["run/1/gates.json", ""]
    assert (
        "enum"
        not in json.loads((ctx.home / "schemas/handoff_result.schema.json").read_text())[
            "properties"
        ]["reproduction_evidence"]
    )


def test_diagnosis_without_reproduction_allows_human_routing_but_not_code_repair(
    ctx: Context,
) -> None:
    _to_worktree(ctx)
    ctx.store.update_run(ctx.run.id, attempt=1)
    ctx.refresh()
    started = plan.start(ctx)
    assert started is not None
    attempt = started[0]
    schema = json.loads(attempt.schema.read_text())
    assert schema["properties"]["reproduction_evidence"]["enum"] == [""]
    request = json.loads(attempt.path("plan-request.json").read_text())
    assert request["reproduction_evidence"] == ""
    assert request["reproduction_sha256"] is None
    result = {
        "status": "repair",
        "classification": "code",
        "summary": "Unproven",
        "acceptance_behavior": "doubling",
        "reproduction_evidence": "",
    }
    with pytest.raises(Blocked, match="diagnosis-not-reproduced"):
        handoffs.authorize_repair(ctx, result, ctx.run.attempt)
    result.update(status="needs-human", classification="environment")
    attempt.path("plan-exit").write_text("0")
    attempt.path("plan-events.jsonl").write_text("")
    attempt.path("plan-stderr.log").write_text("")
    attempt.path("plan-last-message.json").write_text(json.dumps(result))
    with pytest.raises(Blocked, match="diagnosis-environment"):
        plan.collect(ctx, attempt)


@pytest.mark.parametrize("test_design", [False, True])
def test_readiness_and_test_design_keep_the_original_schema(
    ctx: Context, test_design: bool
) -> None:
    _to_worktree(ctx)
    if test_design:
        contracts = ctx.project.path / ".agents/vendor/harness/docs/agents"
        (contracts / "test-design.md").write_text("Define scenarios.")
        (contracts / "test-design.json").write_text('{"required_artifacts": ["test-plan.md"]}')
        ctx.store.runtime.configure("run", ctx.run.id, {"test_design": True})
    started = plan.start(ctx)
    assert started is not None
    assert (
        started[0].schema.read_bytes()
        == (ctx.home / "schemas/handoff_result.schema.json").read_bytes()
    )
    request = json.loads(started[0].path("plan-request.json").read_text())
    assert "reproduction_evidence" not in request


def test_diagnosis_cannot_authorize_new_bytes_rerecorded_at_the_original_path(ctx: Context) -> None:
    _to_worktree(ctx)
    ctx.store.update_run(ctx.run.id, attempt=1)
    ctx.refresh()
    evidence = ctx.factory_dir / "run/1/gates.json"
    evidence.parent.mkdir(parents=True)
    report = {
        "gates": [
            {"name": "acceptance", "status": "fail", "exit": 1, "outputTail": "expected 4, got 3"}
        ]
    }
    evidence.write_text(json.dumps(report))
    handoffs.record_failure(ctx, evidence, report)
    started = plan.start(ctx)
    assert started is not None
    attempt = started[0]
    report["gates"][0]["outputTail"] = "expected 4, got 2"
    evidence.write_text(json.dumps(report))
    handoffs.record_failure(ctx, evidence, report)
    plans = plan.plan_dir(ctx)
    plans.mkdir(parents=True)
    for name in ["execution-brief.md", "test-plan.md"]:
        (plans / name).write_text("Preserved first failure diagnosis")
    attempt.path("plan-exit").write_text("0")
    attempt.path("plan-events.jsonl").write_text("")
    attempt.path("plan-stderr.log").write_text("")
    attempt.path("plan-last-message.json").write_text(
        json.dumps(
            {
                "status": "repair",
                "classification": "code",
                "summary": "Original diagnosis",
                "acceptance_behavior": "doubling",
                "reproduction_evidence": "run/1/gates.json",
            }
        )
    )
    with pytest.raises(Blocked, match="diagnosis-reproduction-stale"):
        plan.collect(ctx, attempt)
    assert not ctx.store.runtime.db.execute("SELECT * FROM failure_episodes").fetchall()


@pytest.mark.parametrize("outside", [False, True])
def test_reproduction_identity_uses_resolved_run_containment(ctx: Context, outside: bool) -> None:
    _to_worktree(ctx)
    report = {"gates": [{"name": "acceptance", "status": "fail", "exit": 1}]}
    if outside:
        path = ctx.factory_dir / ".." / "outside.json"
    else:
        alias = ctx.home / "protocol-alias"
        alias.symlink_to(ctx.factory_dir, target_is_directory=True)
        path = alias / "gates.json"
    path.write_text(json.dumps(report))
    handoffs.record_failure(ctx, path, report)
    if outside:
        with pytest.raises(Blocked, match="diagnosis-not-reproduced"):
            handoffs.reproduction_binding(ctx)
    else:
        assert handoffs.reproduction_binding(ctx)["reproduction_evidence"] == "gates.json"
