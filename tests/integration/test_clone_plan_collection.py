"""Planning artifacts live in the sandbox clone; collection retains host evidence."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from factory.artifacts import AttemptDir
from factory.machine import Blocked, State
from factory.sandbox.base import Completed
from factory.steps import Context, plan
from tests.integration.test_clone import _fake, _to_worktree_ready


def prepare(ctx: Context, structured: bool) -> AttemptDir:
    _to_worktree_ready(ctx)
    attempt = AttemptDir.create(ctx.factory_dir, 1)
    ctx.store.start_attempt(
        ctx.run.id,
        1,
        State.PLANNING,
        sandbox=ctx.project.build_sandbox,
        artifact_dir=str(attempt.root),
    )
    ctx.store.update_run(ctx.run.id, attempt=1)
    ctx.refresh()
    attempt.path(plan.PLAN_EXIT_NAME).write_text("0")
    if structured:
        attempt.path("plan-request.json").write_text('{"diagnosis": false}')
        attempt.path("plan-events.jsonl").write_text("")
        attempt.path("plan-stderr.log").write_text("")
        attempt.schema.write_text((ctx.home / "schemas/handoff_result.schema.json").read_text())
        attempt.path("plan-last-message.json").write_text(
            json.dumps(
                {
                    "status": "ready",
                    "classification": "ready",
                    "summary": "Prepared",
                    "acceptance_behavior": "CRUD",
                    "reproduction_evidence": "",
                }
            )
        )
    return attempt


@pytest.mark.parametrize("structured", [False, True])
def test_vm_only_plans_are_collected_and_digest_bound(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch, structured: bool
) -> None:
    attempt = prepare(clone_ctx, structured)
    fake = _fake(clone_ctx)
    vm = fake.clone_dir(clone_ctx.project.build_sandbox)
    assert vm is not None
    plans = plan.plan_dir(clone_ctx)
    vm_plans = vm / plans.relative_to(clone_ctx.project.path)
    vm_plans.mkdir(parents=True)
    names = ("execution-brief.md", "test-plan.md") if structured else plan.PLAN_FILES
    for name in names:
        (vm_plans / name).write_text(f"# {name}\nMeasured behavior and assertions.\n")
    assert not plans.exists()
    real = fake.exec_sync

    def read_vm(name: str, argv: Any, **kwargs: Any) -> Completed:
        if argv[0] == "/bin/cat":
            path = vm / Path(argv[-1]).relative_to(clone_ctx.project.path)
            return Completed(tuple(argv), 0, path.read_text(), "")
        return real(name, argv, **kwargs)

    monkeypatch.setattr(fake, "exec_sync", read_vm)
    plan.collect(clone_ctx, attempt)
    manifest = json.loads(attempt.manifest.read_text())["files"]
    for name in names:
        retained = attempt.path("planning-output") / name
        assert retained.read_bytes() == (vm_plans / name).read_bytes()
        assert (
            manifest[f"planning-output/{name}"]["sha256"]
            == hashlib.sha256(retained.read_bytes()).hexdigest()
        )
    assert not plans.exists()
    check = clone_ctx.store.checks(clone_ctx.run.id)[-1]
    assert check["artifact"] == str(attempt.path("planning-output"))


@pytest.mark.parametrize("content", [None, "", " \n\t"])
def test_missing_or_empty_vm_plans_block(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    attempt = prepare(clone_ctx, False)
    fake = _fake(clone_ctx)
    real = fake.exec_sync

    def read_vm(name: str, argv: Any, **kwargs: Any) -> Completed:
        if argv[0] == "/bin/cat":
            return Completed(tuple(argv), 1 if content is None else 0, content or "", "")
        return real(name, argv, **kwargs)

    monkeypatch.setattr(fake, "exec_sync", read_vm)
    with pytest.raises(Blocked, match="plan-incomplete"):
        plan.collect(clone_ctx, attempt)
    assert not any(
        row["check_name"] == "plan_written" for row in clone_ctx.store.checks(clone_ctx.run.id)
    )


@pytest.mark.parametrize("content", [None, "", " \n", "# Plan\nImplement and assert behavior.\n"])
def test_bind_collection_reads_host_and_rejects_empty_files(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    attempt = prepare(ctx, False)
    plans = plan.plan_dir(ctx)
    plans.mkdir(parents=True)
    if content is not None:
        for name in plan.PLAN_FILES:
            (plans / name).write_text(content)

    def no_sandbox_read(*args: Any, **kwargs: Any) -> Completed:
        raise AssertionError("bind collection must read the host filesystem")

    monkeypatch.setattr(ctx.sandbox, "exec_sync", no_sandbox_read)
    if not content or not content.strip():
        with pytest.raises(Blocked, match="plan-incomplete"):
            plan.collect(ctx, attempt)
    else:
        plan.collect(ctx, attempt)
        assert (attempt.path("planning-output") / "plan.md").read_text() == content


@pytest.mark.parametrize("brief", ["# Brief\nTechnical execution seam.\n", ""])
def test_readiness_accepts_ready_without_diagnosis_artifacts(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch, brief: str
) -> None:
    attempt = prepare(clone_ctx, True)
    fake = _fake(clone_ctx)
    real = fake.exec_sync

    def read_vm(name: str, argv: Any, **kwargs: Any) -> Completed:
        if argv[0] == "/bin/cat":
            content = brief if Path(argv[-1]).name == "execution-brief.md" else ""
            return Completed(tuple(argv), 0 if content else 1, content, "")
        return real(name, argv, **kwargs)

    monkeypatch.setattr(fake, "exec_sync", read_vm)
    plan.collect(clone_ctx, attempt)
    files = json.loads(attempt.manifest.read_text())["files"]
    assert ("planning-output/execution-brief.md" in files) == bool(brief)
    assert "planning-output/test-plan.md" not in files


def test_diagnosis_still_requires_test_plan(
    clone_ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import handoffs

    attempt = prepare(clone_ctx, True)
    attempt.path("plan-request.json").write_text(
        json.dumps(
            {
                "diagnosis": True,
            }
        )
    )
    monkeypatch.setattr(handoffs, "authorize_repair", lambda *args: None)
    fake = _fake(clone_ctx)
    real = fake.exec_sync

    def read_vm(name: str, argv: Any, **kwargs: Any) -> Completed:
        if argv[0] == "/bin/cat":
            content = "# Brief\n" if Path(argv[-1]).name == "execution-brief.md" else ""
            return Completed(tuple(argv), 0 if content else 1, content, "")
        return real(name, argv, **kwargs)

    monkeypatch.setattr(fake, "exec_sync", read_vm)
    with pytest.raises(Blocked, match="plan-incomplete"):
        plan.collect(clone_ctx, attempt)
