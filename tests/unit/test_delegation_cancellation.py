"""Cancellation is host-owned, restart-safe, and retains paid child accounting."""

import json
from pathlib import Path

import pytest

from factory.agent_launches import AgentLaunches
from factory.delegation import DelegationBroker
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import RunHandle
from factory.store import Store
from factory.workflow_launches import reconcile_run
from tests.unit.test_agent_launches import Sandbox
from tests.unit.test_delegation import setup, task


class SignallingSandbox(Sandbox):
    def __init__(self) -> None:
        super().__init__()
        self.signals: list[tuple[str, int]] = []
        self.fail = False

    def kill_group(self, name: str, pgid: int) -> None:
        self.signals.append((name, pgid))
        if self.fail:
            raise ConnectionError("lost signal acknowledgement")


@pytest.mark.parametrize("evidence", ["valid", "missing", "zero", "symlink", "lost-ack"])
def test_cancelled_child_is_signalled_once_and_accounted_before_release(
    tmp_path: Path, evidence: str
) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    sandbox = SignallingSandbox()
    requests = []
    for index in range(2):
        request = broker.request(f"call-{index}", task())
        requests.append(request)
        child = f"child-{index}"
        directory = tmp_path / child
        store.runtime.start_invocation(
            child,
            request["run_id"],
            1,
            child,
            {
                "parent_id": "parent",
                "policy_revision": 1,
                "semantic_role": "test_designer",
                "adapter": "codex-exec",
                "model": "unpriced",
                "cost_step": child,
                "events": str(directory / "events"),
            },
        )
        broker.bind_child(request["id"], child)
        handle = RunHandle(request["run_id"], 1, f"factory-review-{child}", str(source), directory)
        AgentLaunches(store, sandbox).start(
            child,
            handle,
            "script",
            {},
            parent_id="parent",
            usd_limit=10,
            max_attempts=2,
        )
        directory.mkdir()
        (directory / handle.pgid_name).write_text(str(100 + index))
    pgid_path = tmp_path / "child-0" / handle.pgid_name
    if evidence == "missing":
        pgid_path.unlink()
    elif evidence == "zero":
        pgid_path.write_text("0")
    elif evidence == "symlink":
        pgid_path.unlink()
        unrelated = tmp_path / "unrelated-pgid"
        unrelated.write_text("999")
        pgid_path.symlink_to(unrelated)
    elif evidence == "lost-ack":
        sandbox.fail = True
    run_id = requests[0]["run_id"]
    broker.cancel(requests[0]["id"])
    store.close()
    store = Store(tmp_path / "factory.db")
    if evidence == "lost-ack":
        with pytest.raises(ConnectionError, match="acknowledgement"):
            reconcile_run(store, tmp_path, sandbox, run_id, "synthetic")
        sandbox.fail = False
    elif evidence in {"missing", "zero", "symlink"}:
        reconcile_run(store, tmp_path, sandbox, run_id, "synthetic")
        assert sandbox.signals == []
        assert len(RuntimeJobs(store).active_agents("synthetic")) == 3
        pgid_path.unlink(missing_ok=True)
        pgid_path.write_text("100")
    reconcile_run(store, tmp_path, sandbox, run_id, "synthetic")
    assert sandbox.signals == [("factory-review-child-0", 100)]
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 3
    assert (
        DelegationBroker(store, "parent", source).inspect(requests[0]["id"])["status"]
        == "cancelling"
    )
    store.close()
    store = Store(tmp_path / "factory.db")
    reconcile_run(store, tmp_path, sandbox, run_id, "synthetic")
    assert sandbox.signals == [("factory-review-child-0", 100)]
    directory = tmp_path / "child-0"
    (directory / "events").write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "cached_input_tokens": 0,
                },
            }
        )
        + "\n"
    )
    (directory / "exit").write_text("143")
    reconcile_run(store, tmp_path, sandbox, run_id, "synthetic")
    broker = DelegationBroker(store, "parent", source)
    assert broker.inspect(requests[0]["id"])["status"] == "cancelled"
    assert broker.inspect(requests[1]["id"])["status"] == "prepared"
    assert {r["invocation_id"] for r in RuntimeJobs(store).active_agents("synthetic")} == {
        "parent",
        "child-1",
    }
    invocation = store.runtime.invocation("child-0")
    assert invocation is not None
    assert invocation["telemetry"]["usage"]["input_tokens"] == 12
    assert invocation["telemetry"]["estimate"]["complete"] is False
    reconcile_run(store, tmp_path, sandbox, run_id, "synthetic")
    assert store.runtime.invocation("child-0") == invocation
    assert sandbox.launches == 2
    store.close()


@pytest.mark.parametrize("state", ["suspended", "cancelled", "failed"])
def test_stopped_run_fences_pending_children_before_parent_cleanup(
    tmp_path: Path, state: str
) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("call", task())
    from factory.machine import State

    store.record_transition(
        request["run_id"], from_state=None, to_state=State(state), actor="human"
    )
    reconcile_run(store, tmp_path, SignallingSandbox(), request["run_id"], "synthetic")
    assert broker.inspect(request["id"])["status"] == "cancelled"
    store.close()


def test_exited_parent_drains_child_before_releasing_its_own_slot(tmp_path: Path) -> None:
    from factory.delegation_controller import DelegationController
    from factory.sandbox.base import SandboxSpec, Workspace

    store, source = setup(tmp_path)
    original = store.runtime.invocation("parent")
    assert original is not None
    run_id = original["run_id"]
    home = tmp_path / "home"
    controller = DelegationController(store, home)
    spec = controller.prepare(
        run_id,
        1,
        "builder",
        source,
        SandboxSpec(
            project="synthetic",
            role="build",
            name="factory-build-original",
            workspaces=(Workspace(source),),
        ),
    )
    metadata = {"policy_revision": 1, "adapter": "codex-exec", "model": "unpriced"}
    store.runtime.start_invocation(
        "builder",
        run_id,
        1,
        "implement:builder",
        metadata
        | {
            "semantic_role": "builder",
            "events": str(tmp_path / "builder/events"),
            "cost_step": "implement:builder",
        },
    )
    sandbox = SignallingSandbox()
    launches = AgentLaunches(store, sandbox)
    parent_handle = RunHandle(run_id, 1, spec.name, str(source), tmp_path / "builder")
    launches.start("builder", parent_handle, "script", {}, usd_limit=10, max_attempts=2)
    broker = DelegationBroker(store, "builder", source)
    request = broker.request("call", task())
    store.runtime.start_invocation(
        "child",
        run_id,
        1,
        "child",
        metadata
        | {
            "parent_id": "builder",
            "semantic_role": "test_designer",
            "events": str(tmp_path / "child/events"),
            "cost_step": "child",
        },
    )
    broker.bind_child(request["id"], "child")
    child_handle = RunHandle(run_id, 1, "factory-review-child", str(source), tmp_path / "child")
    launches.start(
        "child", child_handle, "script", {}, parent_id="builder", usd_limit=10, max_attempts=2
    )
    parent_handle.attempt_dir.mkdir()
    (parent_handle.attempt_dir / parent_handle.exit_name).write_text("1")
    child_handle.attempt_dir.mkdir()
    (child_handle.attempt_dir / child_handle.pgid_name).write_text("123")
    reconcile_run(store, home, sandbox, run_id, "synthetic")
    assert sandbox.signals == [("factory-review-child", 123)]
    assert {r["invocation_id"] for r in RuntimeJobs(store).active_agents("synthetic")} == {
        "parent",
        "builder",
        "child",
    }
    (child_handle.attempt_dir / child_handle.exit_name).write_text("143")
    # Simulate death after terminal child accounting/lease release but before publication.
    launches.reconcile("child", collect=lambda: None)
    store.close()
    store = Store(tmp_path / "factory.db")
    reconcile_run(store, home, sandbox, run_id, "synthetic")
    assert {r["invocation_id"] for r in RuntimeJobs(store).active_agents("synthetic")} == {"parent"}
    assert (
        DelegationBroker(store, "builder", source).inspect(request["id"])["status"] == "cancelled"
    )
    assert sandbox.signals == [("factory-review-child", 123)]
    store.close()
