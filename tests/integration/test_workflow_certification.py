"""Automatic selection consumes a published fresh certificate, never a manual file."""

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from factory import authority
from factory.agent.app_server import AppServerAdapter
from factory.agent.selection import select
from factory.machine import Blocked
from factory.steps import Context, claim, context, sandbox, worktree
from factory.workflow_certification import service
from tests.unit.test_certification import write_report


def test_automatic_selection_requires_published_host_evidence_and_rechecks_it(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
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
    authority.snapshot(ctx)
    probe_root = ctx.home / "probes"
    probe_root.mkdir()
    source = probe_root / "probes.json"
    source.write_text('{"version":1,"prompts":{}}')
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

    def observe_runtime(*args: object, **kwargs: object) -> dict:
        return {
            "generation": "fresh-generation",
            "image_digest": "sha256:" + "a" * 64,
            "runtime_path": "/opt/codex",
            "runtime_version": "fixture",
            "runtime_sha256": "b" * 64,
            "actual": {"environment_sha256": "c" * 64},
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
    report.with_name("evidence.txt").write_text("candidate-authored replacement")
    with pytest.raises(Blocked):
        select(ctx)
    assert ctx.run.attempt == 0
