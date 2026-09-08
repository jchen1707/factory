"""Child execution through real broker/store, with only sandbox execution faked."""

import json
import shutil
from pathlib import Path

import pytest

from factory import authority
from factory.steps import Context, claim, context, sandbox, worktree
from tests.unit.test_certification import write_report


def test_child_service_leaves_disabled_projects_unchanged(ctx: Context) -> None:
    from factory.workflow_children import advance

    before = ctx.store.runtime.invocations(ctx.run.id)
    advance(ctx)
    assert ctx.store.runtime.invocations(ctx.run.id) == before
    assert not (ctx.home / "state/children").exists()


def prepared(ctx: Context, *, child_contract: bool = True) -> None:
    source_root = Path(__file__).parents[2]
    for name in ("hooks/delivery_policy.mjs", "docs/agents/delivery-review.md"):
        target = ctx.project.path / ".agents/vendor/harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / ".agents/vendor/harness" / name, target)
    config_path = ctx.project.path / "harness.config.json"
    config = json.loads(config_path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config_path.write_text(json.dumps(config))
    wiring = ctx.project.path / ".codex/hooks.json"
    wiring.parent.mkdir(exist_ok=True)
    wiring.write_text('{"hooks":{"PreToolUse":[]}}')
    schema = ctx.project.path / ".agents/vendor/harness/schema/delegation-request.schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_root / "tests/fixtures/delegation/request.schema.json", schema)
    target = ctx.project.path / ".agents/vendor/harness/schema/delegation-result.schema.json"
    shutil.copyfile(Path(__file__).parents[1] / "fixtures/delegation/result.schema.json", target)
    target = ctx.project.path / ".agents/vendor/harness/docs/agents/delegation-child.md"
    target.write_text(
        "Read only. Return observations and limitations. Child assistance is not review."
    )
    if not child_contract:
        target.unlink()
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    for relative in (".codex", ".agents/vendor/harness"):
        shutil.copytree(ctx.project.path / relative, ctx.worktree / relative, dirs_exist_ok=True)
    shutil.copyfile(config_path, ctx.worktree / "harness.config.json")
    authority.snapshot(ctx)
    probe_root = ctx.home / "probes"
    probe_root.mkdir()
    source = probe_root / "probes.json"
    source.write_text('{"version":1,"prompts":{},"steps":[]}')
    config_file = ctx.home / "certification.json"
    config_file.write_text(
        json.dumps(
            {
                "binary": "/opt/codex",
                "source": str(source),
                "model": "fixture",
                "alternate_model": "other",
                "effort": "low",
                "usage_scope": "thread",
                "canary_path": "protected.txt",
            }
        )
    )
    ctx.store.runtime.configure(
        "run",
        ctx.run.id,
        {
            "agent_adapter": "app-server",
            "certification_mode": "automatic",
            "certification_config": str(config_file),
            "app_server_compatibility": "/absent/manual",
        },
    )
    from factory.workflow_delegation import prepare_parent

    ctx.store.runtime.configure("project", ctx.project.name, {"delegation_mode": "read-only"})
    prepare_parent(ctx, 1)


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "contract",
        "invalid",
        "missing",
        "symlink",
        "oversized",
        "unicode",
        "approval",
        "generation",
        "environment",
    ],
)
def test_child_request_prepares_private_readonly_sandbox_before_paid_certification(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from factory import accounting
    from factory.agent_launches import AgentLaunches
    from factory.delegation import DelegationBroker
    from factory.sandbox.base import RunHandle
    from factory.workflow_children import advance
    from factory.workflow_delegation import parent_configuration
    from tests.unit.test_delegation import task

    prepared(ctx, child_contract=case != "contract")
    from tests.integration.test_pipeline import _fake

    _fake(ctx).detach_without_finishing = True
    parent_id = accounting.key(ctx, 1, "implement")
    events = ctx.state_dir / "parent/events"
    ctx.store.runtime.start_invocation(
        parent_id,
        ctx.run.id,
        1,
        "implement",
        {
            "semantic_role": "builder",
            "policy_revision": 1,
            "events": str(events),
        },
    )
    parent_configuration(ctx, parent_id)
    handle = RunHandle(ctx.run.id, 1, ctx.project.build_sandbox, str(ctx.worktree), events.parent)
    AgentLaunches(ctx.store, ctx.sandbox).start(
        parent_id, handle, "script", {}, usd_limit=10, max_attempts=2
    )
    broker = DelegationBroker(ctx.store, parent_id, ctx.worktree)
    request = broker.request("child-call", task() | {"paths": ["harness.config.json"]})
    advance(ctx)
    retained = ctx.store.find_effect(ctx.run.id, 1, request["id"], "child-execution", "prepare")
    if case == "contract":
        assert retained is None
        assert broker.inspect(request["id"])["waiting"]["reason"] == "child-contract-unavailable"
        assert len(_fake(ctx).created) == 2  # ordinary build and mailbox parent only
        return
    assert retained is not None
    assert retained.status == "confirmed"
    payload = json.loads(retained.external_id or "{}")
    assert payload["sandbox"].startswith("factory-review-child-")
    assert payload["sandbox"] != ctx.project.build_sandbox
    assert Path(payload["scratch"]).is_relative_to(ctx.home / "state/children")
    assert payload["parent_id"] == parent_id
    assert payload["request"] == request["request"]
    assert broker.inspect(request["id"])["child_id"] is None

    from dataclasses import replace

    from factory.store import Store
    from factory.workflow_certification import service
    from tests.integration.test_pipeline import _fake

    generation = "child-generation"

    def observe_runtime(*args: object, **kwargs: object) -> dict:
        return {
            "generation": generation,
            "image_digest": "sha256:" + "a" * 64,
            "runtime_path": "/opt/codex",
            "runtime_version": "fixture",
            "runtime_sha256": "b" * 64,
            "actual": {
                "environment_sha256": "c" * 64,
                "launcher_sha256": "d" * 64,
                "code_host_sha256": "e" * 64,
            },
        }

    monkeypatch.setattr(type(ctx.sandbox), "observe_certification", observe_runtime, raising=False)
    spec = _fake(ctx).created[-1]
    policy = ctx.store.runtime.policy(ctx.run.id)
    assert policy is not None
    assert [(w.path, w.readonly) for w in spec.workspaces] == [
        (Path(payload["scratch"]), False),
        (ctx.project.path, True),
        (Path(policy["root"]), True),
    ]
    child_ctx = replace(
        ctx,
        project=replace(
            ctx.project,
            env={
                **ctx.project.env,
                "TMPDIR": str(Path(payload["scratch"]) / "tmp"),
                "UV_PROJECT_ENVIRONMENT": payload["scratch"] + "/venv",
            },
        ),
    )
    runner, _ = service(
        child_ctx, review=True, spec=spec, scratch=Path(payload["scratch"]) / "certification"
    )
    current = runner.observe()
    job = runner.certifications.request(ctx.run.id, current)
    write_report(runner.certifications.root, job)
    token = runner.certifications.jobs.claim_certification(job["id"], now=10, duration=30)
    assert token is not None
    runner.certifications.publish(job["id"], token, current, now=11)
    if case in {"approval", "generation", "environment"}:
        ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    advance(ctx)
    child = broker.inspect(request["id"])["child_id"]
    assert child is not None
    if case in {"approval", "generation", "environment"}:
        assert child is not None
        assert broker.inspect(request["id"])["waiting"]["reason"] == "AgentApprovalRequired"
        assert ctx.store.find_effect(ctx.run.id, 1, child, "agent-launch", "spawn") is None
        original_project = ctx.project
        if case == "generation":
            generation = "replacement-generation"
        elif case == "environment":
            ctx.project = replace(ctx.project, env={**ctx.project.env, "CHANGED": "yes"})
        ctx.store.runtime.approve(ctx.run.id, child)
        advance(ctx)
        if case != "approval":
            assert ctx.store.find_effect(ctx.run.id, 1, child, "agent-launch", "spawn") is None
            generation = "child-generation"
            ctx.project = original_project
            advance(ctx)
    assert ctx.store.find_effect(ctx.run.id, 1, child, "agent-launch", "spawn") is not None
    invocation = ctx.store.runtime.invocation(child)
    assert invocation is not None
    assert invocation["metadata"]["parent_id"] == parent_id
    assert invocation["metadata"]["semantic_role"] == "test_designer"
    worker = json.loads((Path(payload["scratch"]) / "execution/child.app-server.json").read_text())
    assert worker["readonly"] is True
    assert worker["resume_session"] is None
    assert "delegation" not in worker
    launches = len(_fake(ctx).detached)
    db = ctx.store.path
    ctx.store.close()
    ctx.store = Store(db)
    advance(ctx)
    assert len(_fake(ctx).detached) == launches

    from factory.workflow_launches import reconcile_run

    directory = Path(payload["scratch"]) / "execution"
    result = {
        "summary": "Inspected configuration",
        "observations": ["No framework change needed"],
        "limitations": [],
    }
    (directory / "result.json").write_text(json.dumps(result))
    expected: dict | None = result
    status = "completed"
    if case in {"invalid", "missing", "symlink", "oversized", "unicode"}:
        expected = None
        status = "failed"
        if case == "unicode":
            result["summary"] = "😀" * 8192
            (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False))
        elif case == "missing":
            (directory / "result.json").unlink()
        elif case == "symlink":
            outside = ctx.home / "unrelated-result"
            outside.write_text(json.dumps(result))
            (directory / "result.json").unlink()
            (directory / "result.json").symlink_to(outside)
        else:
            (directory / "result.json").write_text("{}" if case == "invalid" else "x" * 65537)
    (directory / "exit").write_text("0")
    reconcile_run(ctx.store, ctx.home, ctx.sandbox, ctx.run.id, ctx.project.name)
    broker = DelegationBroker(ctx.store, parent_id, ctx.worktree)
    assert broker.inspect(request["id"])["status"] == status
    assert broker.inspect(request["id"])["result"] == expected
    reconcile_run(ctx.store, ctx.home, ctx.sandbox, ctx.run.id, ctx.project.name)
    assert broker.inspect(request["id"])["result"] == expected
