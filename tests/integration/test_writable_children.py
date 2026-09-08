"""Writable lifecycle through controller/certification/admission; VM calls are local fakes."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory import accounting, child_integration
from factory.agent_launches import AgentLaunches
from factory.delegation import DelegationBroker
from factory.sandbox.base import Completed, RunHandle
from factory.steps import Context
from factory.workflow_certification import service
from factory.workflow_children import advance
from factory.workflow_delegation import parent_configuration
from factory.workflow_launches import reconcile_run
from tests.integration.test_pipeline import _fake
from tests.integration.test_workflow_children import prepared
from tests.unit.test_certification import write_report
from tests.unit.test_delegation import task


def test_writable_child_isolated_launch_artifact_and_integration(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared(ctx, writable_contract=True)
    ctx.store.runtime.configure("project", ctx.project.name, {"delegation_mode": "isolated-write"})
    ctx.store.runtime.configure(
        "run",
        ctx.run.id,
        {"delegation_generation": "original", "delegation_mode": "isolated-write"},
    )
    monkeypatch.setattr(type(ctx.sandbox), "generation", lambda *args: "original", raising=False)
    original = type(ctx.sandbox).exec_sync

    def execute(self: Any, name: str, argv: list[str], **kwargs: Any) -> Completed:
        if any("prepare_runtime(json.loads" in str(arg) for arg in argv):
            return original(self, name, argv, **kwargs)
        if argv[0] in {"/usr/bin/python3", "/usr/bin/git"}:
            result = subprocess.run(
                argv, capture_output=True, text=True, check=False, input=kwargs.get("stdin")
            )
            return Completed(tuple(argv), result.returncode, result.stdout, result.stderr)
        return original(self, name, argv, **kwargs)

    monkeypatch.setattr(type(ctx.sandbox), "exec_sync", execute)
    _fake(ctx).detach_without_finishing = True
    (ctx.worktree / "child-output.txt").write_text("parent baseline")
    (ctx.worktree / "parent-note.txt").write_text("keep parent dirty note")
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
    request = broker.request(
        "writable-call", task() | {"mode": "isolated-write", "paths": ["child-output.txt"]}
    )
    advance(ctx)
    retained = ctx.store.find_effect(ctx.run.id, 1, request["id"], "child-execution", "prepare")
    assert retained is not None, broker.inspect(request["id"])
    payload = json.loads(retained.external_id or "{}")
    source = Path(payload["source"])
    assert source != ctx.worktree
    spec = _fake(ctx).created[-1]
    assert spec.role == "build"
    assert spec.name.startswith("factory-build-child-")
    assert [(mount.path, mount.readonly) for mount in spec.workspaces][:2] == [
        (Path(payload["scratch"]), False),
        (source, False),
    ]
    assert not any(mount.path == ctx.project.path for mount in spec.workspaces)
    assert (source / "parent-note.txt").read_text() == "keep parent dirty note"

    def observed(*args: object, **kwargs: object) -> dict:
        return {
            "generation": "original",
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

    monkeypatch.setattr(type(ctx.sandbox), "observe_certification", observed, raising=False)
    scratch = Path(payload["scratch"])
    child_ctx = replace(
        ctx,
        run=replace(ctx.run, worktree=str(source)),
        project=replace(
            ctx.project,
            path=source,
            requires_clone=False,
            env={
                **ctx.project.env,
                "TMPDIR": str(scratch / "tmp"),
                "UV_PROJECT_ENVIRONMENT": str(scratch / "venv"),
            },
        ),
    )
    runner, _ = service(child_ctx, review=False, spec=spec, scratch=scratch / "certification")
    current = runner.observe()
    job = runner.certifications.request(ctx.run.id, current)
    write_report(runner.certifications.root, job)
    token = runner.certifications.jobs.claim_certification(job["id"], now=10, duration=30)
    assert token
    runner.certifications.publish(job["id"], token, current, now=11)
    advance(ctx)
    child = broker.inspect(request["id"])["child_id"]
    assert child, broker.inspect(request["id"])
    worker = json.loads((scratch / "execution/child.app-server.json").read_text())
    assert worker["readonly"] is False
    assert worker["resume_session"] is None
    assert "delegation" not in worker
    (source / "child-output.txt").write_text("isolated child edit")
    assert (ctx.worktree / "child-output.txt").read_text() == "parent baseline"
    directory = scratch / "execution"
    (directory / "result.json").write_text(
        json.dumps({"summary": "implemented", "observations": [], "limitations": []})
    )
    (directory / "exit").write_text("0")
    reconcile_run(ctx.store, ctx.home, ctx.sandbox, ctx.run.id, ctx.project.name)
    completed = broker.inspect(request["id"])
    assert completed["status"] == "completed"
    assert completed["result"]["artifact"]["status"] == "awaiting-parent-drain"
    child_integration.advance(ctx)
    assert (ctx.worktree / "child-output.txt").read_text() == "parent baseline"
    ctx.store.runtime.db.execute(
        "UPDATE agent_leases SET status='completed' WHERE invocation_id=?", (parent_id,)
    )
    child_integration.advance(ctx)
    assert (ctx.worktree / "child-output.txt").read_text() == "isolated child edit"
    assert (ctx.worktree / "parent-note.txt").read_text() == "keep parent dirty note"
    child_integration.advance(ctx)
    assert (
        len(
            [
                effect
                for effect in ctx.store.effects(ctx.run.id)
                if effect.system == "child-integration" and effect.key == "complete"
            ]
        )
        == 1
    )
