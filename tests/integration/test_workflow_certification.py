"""Automatic selection consumes a published fresh certificate, never a manual file."""

import hashlib
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from factory import authority
from factory.agent.app_server import AppServerAdapter
from factory.agent.selection import select
from factory.machine import Blocked
from factory.steps import Context, claim, context, sandbox, worktree
from factory.workflow_certification import service
from tests.unit.test_certification import write_report


@pytest.mark.parametrize(
    "change", ["evidence", "generation", "environment", "runtime", "mount", "launcher"]
)
def test_prepared_launch_rechecks_its_certificate_without_starting_new_probes(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
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

    generation = "fresh-generation"
    runtime_digest = "b" * 64
    launcher_digest = "d" * 64
    mount = "original-mount"

    def observe_runtime(*args: object, **kwargs: object) -> dict:
        # The real standalone observer compares the candidate's exact bytes with
        # these approved digests. Snapshot serialization must not change them.
        hooks = kwargs["hook_files"]
        assert isinstance(hooks, dict)
        for path, expected in hooks.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
        return {
            "generation": generation,
            "image_digest": "sha256:" + "a" * 64,
            "runtime_path": "/opt/codex",
            "runtime_version": "fixture",
            "runtime_sha256": runtime_digest,
            "actual": {
                "environment_sha256": "c" * 64,
                "launcher_sha256": launcher_digest,
                "mounts": mount,
            },
        }

    monkeypatch.setattr(type(ctx.sandbox), "observe_certification", observe_runtime, raising=False)
    runner, _ = service(ctx, review=False)
    current = runner.observe()
    job = runner.certifications.request(ctx.run.id, current)
    report = write_report(runner.certifications.root, job)
    # Even a valid file is not enough without the fenced host publication.
    with pytest.raises(Blocked, match="certification-pending"):
        runner.certifications.validate(job["id"], current)
    token = runner.certifications.jobs.claim_certification(job["id"], now=10, duration=30)
    assert token is not None
    runner.certifications.publish(job["id"], token, current, now=11)
    select(ctx)
    assert isinstance(ctx.agent, AppServerAdapter)
    assert ctx.agent.report["certification"]["identity"] == asdict(current)
    from factory import workflow_launches
    from factory.execution import ProjectQueued
    from factory.runtime_jobs import RuntimeJobs
    from factory.steps import implement
    from factory.store import Store
    from tests.integration.test_pipeline import _fake

    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="CERT-OCCUPIED", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    with pytest.raises(ProjectQueued):
        implement.start(ctx)
    identifier = ctx.store.runtime.settings("run", ctx.run.id)["waiting_invocation"]
    jobs.finish_agent("occupied", status="completed")
    database = ctx.store.path
    ctx.store.close()
    ctx.store = Store(database)
    original_project = ctx.project
    original_evidence = report.with_name("evidence.txt").read_bytes()
    if change == "runtime":
        runtime_digest = "e" * 64
    elif change == "launcher":
        launcher_digest = "e" * 64
    elif change == "mount":
        mount = "changed-mount"
    elif change == "generation":
        generation = "recreated-generation"
    elif change == "environment":
        ctx.project = replace(ctx.project, env={**ctx.project.env, "FIXTURE_CHANGED": "1"})
    else:
        report.with_name("evidence.txt").write_text("candidate-authored replacement")
    with pytest.raises(Blocked, match=r"compatibility-|certification-|launch-preparation-stale"):
        workflow_launches.resume(ctx)
    assert ctx.run.attempt == 1
    assert len(ctx.store.runtime.invocations(ctx.run.id)) == 1
    assert ctx.store.find_effect(ctx.run.id, 1, identifier, "agent-launch", "spawn") is None
    assert _fake(ctx).detached == []
    assert ctx.store.runtime.settings("run", ctx.run.id)["certification:build"] == job["id"]
    # Exact restoration resumes the same frozen request once; no recertification.
    ctx.project = original_project
    generation = "fresh-generation"
    runtime_digest = "b" * 64
    launcher_digest = "d" * 64
    mount = "original-mount"
    report.with_name("evidence.txt").write_bytes(original_evidence)
    assert workflow_launches.resume(ctx)
    assert not workflow_launches.resume(ctx)
    assert len(_fake(ctx).detached) == 1
    assert len(ctx.store.runtime.invocations(ctx.run.id)) == 1
