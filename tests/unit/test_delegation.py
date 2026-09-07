"""Host broker behavior using isolated SQLite and an immutable authority fixture.

Schema fixture is the shared e3fc8ad request contract; no production schema is duplicated.
"""

import hashlib
import json
from pathlib import Path

import pytest

from factory.delegation import DelegationBroker
from factory.runtime_jobs import RuntimeJobs
from factory.store import Store


def setup(tmp_path: Path) -> tuple[Store, Path]:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    source = tmp_path / "source"
    source.mkdir()
    (source / "src").mkdir()
    root = tmp_path / "authority"
    path = root / ".agents/vendor/harness/schema/delegation-request.schema.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(
        (Path(__file__).parents[1] / "fixtures/delegation/request.schema.json").read_bytes()
    )
    payload = {
        "root": str(root),
        "files": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    (root / "snapshot.json").write_text(json.dumps(payload))
    store.runtime.snapshot_policy(run.id, payload)
    store.runtime.start_invocation(
        "parent", run.id, 1, "implement", {"semantic_role": "builder", "policy_revision": 1}
    )
    store.runtime.configure("project", "synthetic", {"delegation_mode": "read-only"})
    assert RuntimeJobs(store).schedule_agent("parent", usd_limit=10, max_attempts=2)
    return store, source


def task() -> dict:
    return {
        "task": "Inspect the parser",
        "mode": "read-only",
        "paths": ["src"],
        "role": "test_designer",
    }


def test_request_replay_survives_restart_and_cannot_change_task(tmp_path: Path) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    first = broker.request("call-1", task())
    assert first["status"] == "pending"
    assert first["child_id"] is None
    store.close()
    reopened = Store(tmp_path / "factory.db")
    try:
        broker = DelegationBroker(reopened, "parent", source)
        assert broker.request("call-1", task()) == first
        assert broker.inspect(first["id"]) == first
        assert broker.requests() == [first]
        with pytest.raises(ValueError, match="immutable"):
            broker.request("call-1", task() | {"task": "Different task"})
        assert len(reopened.runtime.invocations(first["run_id"])) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "change",
    [
        {"parent_id": "forged"},
        {"paths": ["../escape"]},
        {"paths": ["/etc"]},
        {"paths": ["src/../src"]},
        {"paths": ["src", "src"]},
        {"paths": ["link"]},
        {"task": " "},
        {"task": "x" * 65536},
        {"mode": "isolated-write"},
    ],
)
def test_untrusted_arguments_are_refused_before_child_creation(
    tmp_path: Path, change: dict
) -> None:
    store, source = setup(tmp_path)
    (source / "link").symlink_to(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    from factory.agent.base import SchemaInvalid

    with pytest.raises((ValueError, SchemaInvalid)):
        broker.request("call", task() | change)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    assert len(store.runtime.invocations(parent["run_id"])) == 1
    store.close()


def test_owned_pending_request_can_be_cancelled_and_does_not_block_new_work(tmp_path: Path) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    first = broker.request("call-1", task())
    second = broker.request("call-2", task())
    with pytest.raises(ValueError, match="pending child limit"):
        broker.request("call-3", task())
    assert broker.cancel(first["id"])["status"] == "cancelled"
    assert broker.cancel(first["id"])["status"] == "cancelled"
    assert broker.request("call-1", task())["status"] == "cancelled"
    assert broker.request("call-3", task())["status"] == "pending"
    assert broker.inspect(second["id"])["status"] == "pending"
    other = DelegationBroker(store, "unrelated-parent", source)
    with pytest.raises(ValueError, match="unknown owned"):
        other.cancel(second["id"])
    with pytest.raises(ValueError, match="unknown owned"):
        other.inspect(second["id"])
    store.close()


def test_bound_child_needs_separate_approval_and_cancel_fences_admission(tmp_path: Path) -> None:
    from factory.agent_launches import AgentLaunches
    from factory.sandbox.base import RunHandle
    from tests.unit.test_agent_launches import Sandbox

    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("call", task())
    metadata = {"parent_id": "parent", "policy_revision": 1, "semantic_role": "test_designer"}
    store.runtime.start_invocation("child", request["run_id"], 1, "child:test", metadata)
    broker.bind_child(request["id"], "child")
    store.runtime.configure("run", request["run_id"], {"mode": "approval"})
    sandbox = Sandbox()
    handle = RunHandle(
        request["run_id"], 1, "factory-review-child", str(source), tmp_path / "child"
    )
    from factory.execution import AgentApprovalRequired

    with pytest.raises(AgentApprovalRequired):
        AgentLaunches(store, sandbox).start(
            "child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    assert broker.cancel(request["id"])["status"] == "cancelled"
    store.runtime.approve(request["run_id"], "child")
    with pytest.raises(ValueError, match="cancelled"):
        AgentLaunches(store, sandbox).start(
            "child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    store.close()


def test_child_execution_restarts_without_duplicate_and_parent_waits_for_result(
    tmp_path: Path,
) -> None:
    from factory import accounting
    from factory.agent_launches import AgentLaunches
    from factory.sandbox.base import RunHandle
    from tests.unit.test_agent_launches import Sandbox

    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("call", task())
    store.runtime.start_invocation(
        "child",
        request["run_id"],
        1,
        "child:test",
        {
            "parent_id": "parent",
            "policy_revision": 1,
            "semantic_role": "test_designer",
            "adapter": "codex-exec",
            "model": "unpriced-child",
            "cost_step": "child:test",
        },
    )
    broker.bind_child(request["id"], "child")
    handle = RunHandle(
        request["run_id"], 1, "factory-review-child", str(source), tmp_path / "child"
    )
    sandbox = Sandbox()
    assert AgentLaunches(store, sandbox).start(
        "child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2
    )
    store.close()
    store = Store(tmp_path / "factory.db")
    broker = DelegationBroker(store, "parent", source)
    launches = AgentLaunches(store, sandbox)
    assert not launches.start(
        "child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2
    )
    assert sandbox.launches == 1
    with pytest.raises(ValueError, match="terminal"):
        broker.publish_result(request["id"], {"summary": "too early"})
    handle.attempt_dir.mkdir()
    (handle.attempt_dir / "exit").write_text("0")
    events = handle.attempt_dir / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": 3, "cached_input_tokens": 0},
            }
        )
        + "\n"
    )

    def collect() -> None:
        accounting.collect_invocation(store, tmp_path, "child", events)

    assert launches.reconcile("child", collect=collect)
    retained = [dict(row) for row in store.costs(request["run_id"])]
    assert len(retained) == 1
    assert retained[0]["input_tokens"] == 12
    assert retained[0]["usd"] is None
    collect()
    collect()
    assert [dict(row) for row in store.costs(request["run_id"])] == retained
    with pytest.raises(ValueError, match="delegation"):
        RuntimeJobs(store).finish_agent("parent", status="completed")
    result = broker.publish_result(request["id"], {"summary": "parser inspected"})
    assert result["status"] == "completed"
    assert result["result"] == {"summary": "parser inspected"}
    assert broker.publish_result(request["id"], result["result"]) == result
    with pytest.raises(ValueError, match="immutable"):
        broker.publish_result(request["id"], {"summary": "changed"})
    RuntimeJobs(store).finish_agent("parent", status="completed")
    assert RuntimeJobs(store).active_agents("synthetic") == []
    store.close()


@pytest.mark.parametrize("change", ["policy", "disabled", "path"])
def test_queued_child_revalidates_authority_and_scope_before_admission(
    tmp_path: Path, change: str
) -> None:
    from factory.agent_launches import AgentLaunches
    from factory.sandbox.base import RunHandle
    from tests.unit.test_agent_launches import Sandbox

    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("call", task())
    store.runtime.start_invocation(
        "child",
        request["run_id"],
        1,
        "child:test",
        {
            "parent_id": "parent",
            "policy_revision": 1,
            "semantic_role": "test_designer",
        },
    )
    broker.bind_child(request["id"], "child")
    if change == "policy":
        snapshot = store.runtime.policy(request["run_id"])
        assert snapshot is not None
        store.runtime.snapshot_policy(request["run_id"], snapshot, replace=True)
    elif change == "disabled":
        store.runtime.configure("project", "synthetic", {"delegation_mode": "disabled"})
    else:
        (source / "src").rmdir()
        (source / "src").symlink_to(tmp_path)
    sandbox = Sandbox()
    handle = RunHandle(
        request["run_id"], 1, "factory-review-child", str(source), tmp_path / "child"
    )
    with pytest.raises(ValueError, match=r"stale|disabled|symlinks"):
        AgentLaunches(store, sandbox).start(
            "child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    store.close()


def test_running_cancel_keeps_child_and_parent_reserved_until_reconciliation(
    tmp_path: Path,
) -> None:
    from factory.agent_launches import AgentLaunches
    from factory.sandbox.base import RunHandle
    from tests.unit.test_agent_launches import Sandbox

    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("call", task())
    store.runtime.start_invocation(
        "child",
        request["run_id"],
        1,
        "child:test",
        {
            "parent_id": "parent",
            "policy_revision": 1,
            "semantic_role": "test_designer",
        },
    )
    broker.bind_child(request["id"], "child")
    handle = RunHandle(
        request["run_id"], 1, "factory-review-child", str(source), tmp_path / "child"
    )
    sandbox = Sandbox()
    launches = AgentLaunches(store, sandbox)
    launches.start("child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2)
    assert broker.cancel(request["id"])["status"] == "cancelling"
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 2
    assert not launches.start(
        "child", handle, "script", {}, parent_id="parent", usd_limit=10, max_attempts=2
    )
    with pytest.raises(ValueError, match="children"):
        RuntimeJobs(store).finish_agent("parent", status="cancelled")
    handle.attempt_dir.mkdir()
    (handle.attempt_dir / "exit").write_text("143")
    launches.reconcile("child", collect=lambda: None)
    assert broker.publish_result(request["id"], {"summary": "interrupted"})["status"] == "cancelled"
    RuntimeJobs(store).finish_agent("parent", status="cancelled")
    assert RuntimeJobs(store).active_agents("synthetic") == []
    assert sandbox.launches == 1
    store.close()


def _request_contender(directory: str, label: str, call_id: str, barrier: object) -> None:
    from typing import Any, cast

    root = Path(directory)
    store = Store(root / "factory.db")
    try:
        cast(Any, barrier).wait(timeout=15)
        try:
            request = DelegationBroker(store, "parent", root / "source").request(call_id, task())
            result = request["id"]
        except ValueError as exc:
            result = str(exc)
        (root / label).write_text(result)
    finally:
        store.close()


@pytest.mark.parametrize("duplicate", [True, False])
def test_independent_request_controllers_share_identity_and_enforce_queue_limit(
    tmp_path: Path, duplicate: bool
) -> None:
    import multiprocessing

    store, _ = setup(tmp_path)
    store.runtime.configure("project", "synthetic", {"max_children_per_parent": 1})
    store.close()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(
            target=_request_contender,
            args=(str(tmp_path), str(i), "same" if duplicate else str(i), barrier),
        )
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    results = [(tmp_path / str(i)).read_text() for i in range(2)]
    if duplicate:
        assert results[0] == results[1]
        assert len(results[0]) == 32
    else:
        assert sum("pending child limit" in result for result in results) == 1
        assert sum(len(result) == 32 for result in results) == 1


@pytest.mark.parametrize("change", ["parent-ended", "disabled", "scope-deleted", "authority"])
def test_duplicate_request_returns_retained_handle_after_admission_conditions_change(
    tmp_path: Path, change: str
) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    first = broker.request("call", task())
    if change == "parent-ended":
        broker.cancel(first["id"])
        RuntimeJobs(store).finish_agent("parent", status="completed")
    elif change == "disabled":
        store.runtime.configure("project", "synthetic", {"delegation_mode": "disabled"})
    elif change == "scope-deleted":
        (source / "src").rmdir()
    else:
        snapshot = store.runtime.policy(first["run_id"])
        assert snapshot is not None
        store.runtime.snapshot_policy(first["run_id"], snapshot, replace=True)
    retained = broker.inspect(first["id"])
    assert broker.request("call", task()) == retained
    store.close()


def test_tool_registration_precedes_paid_admission_without_authorizing_requests(
    tmp_path: Path,
) -> None:
    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    store.runtime.start_invocation(
        "queued", parent["run_id"], 1, "implement:queued", parent["metadata"]
    )
    broker = DelegationBroker(store, "queued", source)
    assert broker.request_schema()["type"] == "object"
    with pytest.raises(ValueError, match="active root parent"):
        broker.request("call", task())
    assert broker.requests() == []
    store.close()
