"""Child VM retirement uses durable ownership, never name-prefix discovery."""

import json
import time

import pytest

from factory import gc
from factory.steps import Context


def seed(ctx: Context, *, mode: str = "read-only") -> str:
    ctx.store.release_lease(ctx.run.id)
    db = ctx.store.runtime.db
    old = time.time() - 40 * 86400
    for identifier in ("parent", "child"):
        db.execute(
            "INSERT INTO invocations VALUES (?,?,?,?,?,?,?, ?,?)",
            (
                identifier,
                ctx.run.id,
                1,
                "child",
                json.dumps({"cost_step": identifier}),
                "{}",
                0,
                old,
                old,
            ),
        )
    db.execute(
        "INSERT INTO agent_leases VALUES (?,?,?,?,?)",
        ("child", ctx.run.id, ctx.run.project, "completed", "parent"),
    )
    task = {"task": {"mode": mode}}
    db.execute(
        "INSERT INTO delegation_requests VALUES (?,?,?,?,?,?,?,?)",
        ("request", "parent", "call", ctx.run.id, json.dumps(task), "completed", "child", "{}"),
    )
    name = (
        "factory-build-child-" if mode == "isolated-write" else "factory-review-child-"
    ) + "request"
    owner = (ctx.run.id, 1, "request", "child-execution", "prepare")
    ctx.store.intend_effect(*owner)
    ctx.store.confirm_effect(
        *owner, json.dumps({"sandbox": name, "parent_id": "parent", "request": task})
    )
    launch = (ctx.run.id, 1, "child", "agent-launch", "spawn")
    ctx.store.intend_effect(*launch)
    ctx.store.confirm_effect(*launch, json.dumps({"handle": {"sandbox": name}}))
    ctx.store.record_cost(
        ctx.run.id,
        1,
        "child",
        model=None,
        input_tokens=3,
        output_tokens=1,
        cached_tokens=0,
        usd=None,
    )
    db.execute("UPDATE effects SET at=?", (old,))
    db.execute(
        "INSERT INTO runtime_certifications (id,run_id,fingerprint,identity,status,owner,lease_until,evidence,failure) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "cert",
            ctx.run.id,
            "fingerprint",
            json.dumps({"sandbox": name, "generation": "generation-1"}),
            "passed",
            None,
            None,
            None,
            None,
        ),
    )
    return name


def sandbox(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, name: str, generation: str = "generation-1"
) -> list[str]:
    calls: list[str] = []
    present = {name}
    monkeypatch.setattr(ctx.sandbox, "exists", lambda n: n in present)
    monkeypatch.setattr(ctx.sandbox, "generation", lambda n: generation, raising=False)
    monkeypatch.setattr(ctx.sandbox, "stop", lambda n: calls.append("stop:" + n))

    def remove(n: str) -> None:
        calls.append("remove:" + n)
        present.remove(n)

    monkeypatch.setattr(ctx.sandbox, "remove", remove)
    return calls


def test_collects_accounted_terminal_child_once_preserving_host_files(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = seed(ctx)
    calls = sandbox(ctx, monkeypatch, name)
    source = ctx.home / "child-source"
    source.write_text("keep")
    gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=True)
    assert not calls
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert calls == ["stop:" + name, "remove:" + name], actions
    assert any(a.target == name and a.done for a in actions)
    gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert len(calls) == 2
    assert source.read_text() == "keep"


@pytest.mark.parametrize(
    "problem", ["generation", "missing-generation", "active", "accounting", "writable"]
)
def test_refuses_unsafe_child_retirement(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    name = seed(ctx, mode="isolated-write" if problem == "writable" else "read-only")
    db = ctx.store.runtime.db
    if problem == "missing-generation":
        db.execute("DELETE FROM runtime_certifications")
    if problem == "active":
        db.execute("UPDATE agent_leases SET status='running'")
    if problem == "accounting":
        db.execute("DELETE FROM costs")
    calls = sandbox(
        ctx, monkeypatch, name, "replacement" if problem == "generation" else "generation-1"
    )
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert not calls
    assert any(a.target == name and not a.done and "retained" in a.why for a in actions)


def test_live_probe_prevents_child_vm_removal(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = seed(ctx)
    db = ctx.store.runtime.db
    db.execute(
        "INSERT INTO invocations SELECT 'probe',run_id,attempt,role,metadata,telemetry,sequence,started_at,updated_at FROM invocations WHERE id='child'"
    )
    db.execute(
        "INSERT INTO agent_leases VALUES (?,?,?,?,?)",
        ("probe", ctx.run.id, ctx.run.project, "running", None),
    )
    owner = (ctx.run.id, 1, "probe", "agent-launch", "spawn")
    ctx.store.intend_effect(*owner)
    ctx.store.confirm_effect(*owner, json.dumps({"handle": {"sandbox": name}}))
    calls = sandbox(ctx, monkeypatch, name)
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert not calls
    assert any("lease is not terminal" in action.why for action in actions)


@pytest.mark.parametrize("problem", ["unconfirmed", "wrong-name", "young", "controller"])
def test_preparation_and_age_refusals(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    name = seed(ctx)
    db = ctx.store.runtime.db
    if problem == "unconfirmed":
        db.execute("UPDATE effects SET status='intended' WHERE system='child-execution'")
    if problem == "wrong-name":
        db.execute(
            "UPDATE effects SET external_id=? WHERE system='child-execution'",
            (json.dumps({"sandbox": "factory-build-other"}),),
        )
    if problem == "young":
        db.execute("UPDATE invocations SET updated_at=?", (time.time(),))
    if problem == "controller":
        assert ctx.store.acquire_lease(ctx.run.id, ttl_seconds=600, owner="other-controller")
    calls = sandbox(ctx, monkeypatch, name)
    gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert not calls


def test_replacement_between_stop_and_remove_is_preserved(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = seed(ctx)
    calls = sandbox(ctx, monkeypatch, name)

    identity = ["generation-1"]
    monkeypatch.setattr(ctx.sandbox, "generation", lambda n: identity[0])

    def stop(n: str) -> None:
        calls.append("stop:" + n)
        identity[0] = "replacement"

    monkeypatch.setattr(ctx.sandbox, "stop", stop)
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert calls == ["stop:" + name]
    assert any("generation changed" in action.why for action in actions)
    assert ctx.store.find_effect(ctx.run.id, 1, "request", "child-gc", "remove") is None


def test_cleanup_intent_is_durable_before_adapter_failure_and_replayed(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = seed(ctx)
    calls = sandbox(ctx, monkeypatch, name)
    original = ctx.sandbox.remove

    def refuse(n: str) -> None:
        effect = ctx.store.find_effect(ctx.run.id, 1, "request", "child-gc", "remove")
        assert effect is not None
        assert effect.status == "intended"
        raise OSError("temporary removal failure")

    monkeypatch.setattr(ctx.sandbox, "remove", refuse)
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert any("temporary removal failure" in action.why for action in actions)
    monkeypatch.setattr(ctx.sandbox, "remove", original)
    gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert calls[-1] == "remove:" + name
    receipt = ctx.store.find_effect(ctx.run.id, 1, "request", "child-gc", "remove")
    assert receipt is not None
    assert receipt.status == "confirmed"


def test_writable_artifact_receipt_allows_vm_cleanup_without_host_deletion(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    name = seed(ctx, mode="isolated-write")
    calls = sandbox(ctx, monkeypatch, name)
    encoded = json.dumps({"base": "base", "child_head": "head", "changes": []}, sort_keys=True)
    owner = (ctx.run.id, 1, "request", "child-workspace", "artifact")
    ctx.store.intend_effect(*owner)
    ctx.store.confirm_effect(*owner, encoded)
    ctx.store.runtime.db.execute(
        "UPDATE delegation_requests SET result=?",
        (json.dumps({"artifact": {"sha256": hashlib.sha256(encoded.encode()).hexdigest()}}),),
    )
    gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert calls == ["stop:" + name, "remove:" + name]
    receipt = ctx.store.find_effect(*owner)
    assert receipt is not None
    assert receipt.external_id == encoded


def test_unpreserved_writable_child_retains_finished_parent_worktree(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_gc import _finished_run

    _finished_run(ctx)
    name = seed(ctx, mode="isolated-write")
    calls = sandbox(ctx, monkeypatch, name)
    assert ctx.run.worktree
    from pathlib import Path

    source = Path(ctx.run.worktree)
    assert source.exists()
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert source.exists()
    assert not calls
    assert any(action.kind == "worktree-remove" and "artifact" in action.why for action in actions)


def test_pending_probe_retains_finished_parent_source(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from tests.integration.test_gc import _finished_run

    _finished_run(ctx)
    name = seed(ctx)
    ctx.store.runtime.db.execute("UPDATE delegation_requests SET child_id=NULL,status='cancelled'")
    ctx.store.runtime.db.execute("UPDATE agent_leases SET status='running'")
    calls = sandbox(ctx, monkeypatch, name)
    assert ctx.run.worktree
    actions = gc.sweep(ctx.home, ctx.registry, ctx.store, ctx.sandbox, dry_run=False)
    assert Path(ctx.run.worktree).exists()
    assert any(
        action.kind == "worktree-remove" and "unreconciled" in action.why for action in actions
    )
    assert not calls
