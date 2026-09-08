"""Parent sandbox ownership through workflow preparation and an isolated store."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory import accounting
from factory.machine import Blocked
from factory.steps import Context, claim, context, sandbox, worktree
from factory.store import Store
from tests.integration.test_pipeline import _fake


def ready(ctx: Context) -> None:
    claim.run(ctx)
    context.run(ctx)
    sandbox.run(ctx)
    worktree.run(ctx)
    root = ctx.home / "state/authority-fixture"
    snapshot: dict[str, Any] = {"root": str(root), "files": {}}
    relative = ".agents/vendor/harness/schema/delegation-request.schema.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (Path(__file__).parents[1] / "fixtures/delegation/request.schema.json").read_bytes()
    )
    snapshot["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "snapshot.json").write_text(json.dumps(snapshot))
    ctx.store.runtime.snapshot_policy(ctx.run.id, snapshot, replace=True)
    ctx.store.runtime.configure("project", ctx.project.name, {"delegation_mode": "read-only"})
    ctx.store.runtime.configure(
        "run", ctx.run.id, {"agent_adapter": "app-server", "certification_mode": "automatic"}
    )


def test_parent_mounts_survive_controller_restart_and_preserve_dirty_source(ctx: Context) -> None:
    from factory.workflow_delegation import prepare_parent

    ready(ctx)
    original = ctx.project.build_sandbox
    note = ctx.worktree / "dirty-note.txt"
    note.write_text("preserve this work")
    prepare_parent(ctx, 1)
    selected = sandbox.build_spec(ctx)
    assert selected.name != original
    assert selected.name == ctx.project.build_sandbox
    assert _fake(ctx).created[-1] == selected
    assert len(selected.workspaces) >= 3
    assert selected.workspaces[-1].readonly
    assert not selected.workspaces[-2].readonly
    assert note.read_text() == "preserve this work"
    assert ctx.store.runtime.invocation(accounting.key(ctx, 1, "implement")) is None
    database = ctx.home / "state/factory.db"
    ctx.store.close()
    ctx.store = Store(database)
    prepare_parent(ctx, 1)
    assert sandbox.build_spec(ctx) == selected
    assert note.read_text() == "preserve this work"


def test_clone_parent_refuses_replacement_before_losing_vm_local_work(ctx: Context) -> None:
    from factory.workflow_delegation import prepare_parent

    ready(ctx)
    original = ctx.project.build_sandbox
    ctx.project = replace(ctx.project, requires_clone=True)
    with pytest.raises(Blocked, match="delegation-clone-transfer-required"):
        prepare_parent(ctx, 1)
    assert ctx.project.build_sandbox == original


def test_builder_entry_prepares_mailbox_before_certification_or_accounting(ctx: Context) -> None:
    from factory.steps.implement import start

    ready(ctx)
    original = ctx.project.build_sandbox
    with pytest.raises(Blocked, match="certification-authority-required"):
        start(ctx)
    assert ctx.project.build_sandbox != original
    assert _fake(ctx).created[-1] == sandbox.build_spec(ctx)
    assert ctx.store.runtime.invocations(ctx.run.id) == []
    assert _fake(ctx).detached == []


def test_thread_recovery_refuses_before_replacing_its_runtime(ctx: Context) -> None:
    from factory.steps.implement import start

    ready(ctx)
    original = ctx.project.build_sandbox
    created = list(_fake(ctx).created)
    with pytest.raises(Blocked, match="delegation-session-transfer-required"):
        start(ctx, resume_session="retained-thread")
    assert ctx.project.build_sandbox == original
    assert _fake(ctx).created == created
    assert ctx.store.runtime.invocations(ctx.run.id) == []


@pytest.mark.parametrize("change", ["none", "generation", "thread", "clone"])
def test_thread_recovery_keeps_exact_vm_and_archives_previous_mailbox(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from factory.runtime_jobs import RuntimeJobs
    from factory.workflow_delegation import prepare_parent

    ready(ctx)
    generation = "retained"
    monkeypatch.setattr(type(ctx.sandbox), "generation", lambda *_: generation, raising=False)
    if change == "clone":
        _fake(ctx).clone_root = ctx.home / "fake-vms"
        (ctx.home / "fake-vms").mkdir()
        ctx.store.update_run(ctx.run.id, worktree=None)
        ctx.refresh()
        ctx.project = replace(ctx.project, requires_clone=True)
    prepare_parent(ctx, 1)
    if change == "clone":
        ctx.store.update_run(ctx.run.id, worktree=str(ctx.project.path))
        ctx.refresh()
    original = ctx.project.build_sandbox
    identifier = accounting.key(ctx, 1, "implement")
    ctx.store.runtime.start_invocation(
        identifier,
        ctx.run.id,
        1,
        "implement",
        {
            "semantic_role": "builder",
            "policy_revision": 1,
            "runtime_compatibility": {
                "certification": {"identity": {"generation": "retained", "sandbox": original}}
            },
        },
    )
    ctx.store.runtime.observe(identifier, 1, {"thread_id": "thread-one"})
    assert RuntimeJobs(ctx.store).schedule_agent(identifier, usd_limit=10, max_attempts=3)
    RuntimeJobs(ctx.store).finish_agent(identifier, status="completed")
    note = ctx.worktree / "dirty-note"
    note.write_text("keep")
    if change == "generation":
        generation = "recreated"
        with pytest.raises(Blocked, match="delegation-runtime-stale"):
            prepare_parent(ctx, 2, resume_session="thread-one")
        generation = "retained"
    elif change == "thread":
        with pytest.raises(Blocked, match="delegation-thread-mismatch"):
            prepare_parent(ctx, 2, resume_session="foreign-thread")
    private = _fake(ctx).clone_dir(original) if change == "clone" else None
    if private:
        (private / "private-dirty").write_text("VM-only work")
    prepare_parent(ctx, 2, resume_session="thread-one")
    if private:
        assert (private / "private-dirty").read_text() == "VM-only work"
    with pytest.raises(Blocked, match="delegation-thread-mismatch"):
        prepare_parent(ctx, 2, resume_session="foreign-thread")
    assert ctx.project.build_sandbox == original
    assert note.read_text() == "keep"
    assert len([spec for spec in _fake(ctx).created if spec.name == original]) == 1
    assert ctx.store.runtime.settings("run", ctx.run.id)["delegation_parent"]["attempt"] == 2
