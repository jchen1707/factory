"""A late human stop fences surviving integration after the in-flight write settles."""

from pathlib import Path
from typing import Any

import pytest

from factory import child_integration
from factory.machine import Blocked, State
from factory.sandbox.base import Completed
from factory.store import Store
from tests.unit.test_child_integration import drain, fixture, git, publish


@pytest.mark.parametrize("held", [State.CANCELLED, State.SUSPENDED])
def test_human_stop_during_apply_preserves_receipt_and_fences_next_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: State
) -> None:
    ctx, request, workspace = fixture(tmp_path)
    ctx.store.record_transition(
        ctx.run.id, from_state=None, to_state=State.IMPLEMENTING, actor="fixture"
    )
    ctx.run = ctx.store.run_by_id(ctx.run.id)  # type: ignore[assignment]
    private = Path(workspace["path"])
    (private / "src/a").write_text("child a")
    (private / "src/b").write_text("child b")
    publish(ctx, request, workspace)
    drain(ctx)
    head = git(ctx.worktree, "rev-parse", "HEAD")
    assert ctx.store.acquire_lease(ctx.run.id, ttl_seconds=900, owner="old-controller")
    original = ctx.sandbox.exec_sync
    interrupted = False

    def cancel_after_apply(name: str, argv: list[str], **kwargs: Any) -> Completed:
        nonlocal interrupted
        result = original(name, argv, **kwargs)
        if not interrupted and result.stdout.strip() == "applied":
            interrupted = True
            # A surviving sync operation outlived its controller lease; the next
            # operator can now acquire ownership and persist a real human stop.
            operator = Store(tmp_path / "factory.db")
            try:
                operator.runtime.db.execute(
                    "UPDATE runs SET lease_expires_at=0 WHERE id=?", (ctx.run.id,)
                )
                assert operator.acquire_lease(ctx.run.id, ttl_seconds=900, owner="operator")
                operator.record_transition(
                    ctx.run.id,
                    from_state=State.IMPLEMENTING,
                    to_state=held,
                    actor="human",
                    detail="stop while old integration settles",
                )
                operator.release_lease(ctx.run.id)
            finally:
                operator.close()
        return result

    monkeypatch.setattr(ctx.sandbox, "exec_sync", cancel_after_apply)
    with pytest.raises(Blocked, match="child-integration-held"):
        child_integration.advance(ctx)
    assert interrupted
    assert (ctx.worktree / "src/a").read_text() == "child a"
    assert (ctx.worktree / "src/b").read_text() == "old b"
    assert (ctx.worktree / "notes").read_text() == "untracked parent notes"
    assert git(ctx.worktree, "rev-parse", "HEAD") == head
    receipt = ctx.store.find_effect(ctx.run.id, 1, request["id"], "child-integration", "file:0")
    assert receipt is not None
    assert receipt.status == "confirmed"
    completion = ctx.store.find_effect(
        ctx.run.id, 1, request["id"], "child-integration", "complete"
    )
    assert completion is not None
    assert completion.status == "intended"
    with pytest.raises(Blocked, match="child-integration-held"):
        child_integration.advance(ctx)
    if held is State.SUSPENDED:
        ctx.store.record_transition(
            ctx.run.id,
            from_state=held,
            to_state=State.IMPLEMENTING,
            actor="human",
            detail="explicit integration recovery",
        )
        child_integration.advance(ctx)
        assert (ctx.worktree / "src/b").read_text() == "child b"
        assert git(ctx.worktree, "rev-parse", "HEAD^") == head
        recovered = git(ctx.worktree, "rev-parse", "HEAD")
        child_integration.advance(ctx)
        assert git(ctx.worktree, "rev-parse", "HEAD") == recovered


def test_driver_observes_cancelled_integration_without_overwriting_human_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import driver, workflow_children

    ctx, request, workspace = fixture(tmp_path)
    (Path(workspace["path"]) / "src/a").write_text("child a")
    publish(ctx, request, workspace)
    drain(ctx)
    ctx.store.record_transition(
        ctx.run.id, from_state=State.APPROVED, to_state=State.CANCELLED, actor="human"
    )
    # The surviving controller cached an active entry state before the other
    # controller committed cancellation. It must only observe that new stop.
    ctx.state = State.IMPLEMENTING  # type: ignore[misc]
    monkeypatch.setattr(workflow_children, "advance", lambda context: None)
    monkeypatch.setattr(
        driver.block_step,
        "record",
        lambda *args: pytest.fail("human stop must not announce a new tracker block"),
    )
    result = driver.drive(ctx)
    assert result.outcome is driver.Outcome.STOPPED
    assert result.reason == "child-integration-held"
    current = ctx.store.run_by_id(ctx.run.id)
    assert current is not None
    assert current.state is State.CANCELLED
    assert current.blocked_reason is None
    assert (ctx.worktree / "src/a").read_text() == "old a"
