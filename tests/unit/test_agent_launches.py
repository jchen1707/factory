"""Durable paid launch ownership through isolated stores and the sandbox boundary."""

import json
from collections.abc import Mapping
from pathlib import Path

from factory.agent_launches import AgentLaunches
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import Gone, RunHandle, RunStatus
from factory.store import Store


class Sandbox:
    def __init__(self) -> None:
        self.launches = 0
        self.status = RunStatus.RUNNING
        self.gone: Gone | None = None

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
        self.launches += 1

    def poll(self, handle: RunHandle) -> RunStatus:
        return self.status

    def confirm_gone(self, handle: RunHandle) -> Gone | None:
        return self.gone


def test_reopened_controller_observes_existing_launch_without_spawning(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    launches = AgentLaunches(store, sandbox)
    assert launches.start("builder", handle, "script", {}, usd_limit=10, max_attempts=2)
    store.close()
    reopened = Store(path)
    try:
        assert not AgentLaunches(reopened, sandbox).start(
            "builder", handle, "script", {}, usd_limit=10, max_attempts=2
        )
        assert sandbox.launches == 1
        assert len(RuntimeJobs(reopened).active_agents("synthetic")) == 1
    finally:
        reopened.close()


def test_lost_launch_acknowledgement_retains_capacity_and_never_retries(tmp_path: Path) -> None:
    import pytest

    class LostAcknowledgement(Sandbox):
        def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
            super().exec_detached(handle, script, env)
            raise ConnectionError("lost after spawn")

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = LostAcknowledgement()
    launches = AgentLaunches(store, sandbox)
    with pytest.raises(ConnectionError, match="lost after spawn"):
        launches.start("builder", handle, "script", {}, usd_limit=10, max_attempts=2)
    assert not launches.start("builder", handle, "script", {}, usd_limit=10, max_attempts=2)
    sandbox.status = RunStatus.ORPHANED
    assert launches.observe("builder") == RunStatus.ORPHANED
    assert sandbox.launches == 1
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    store.close()


def test_terminal_observation_releases_only_after_usage_collection(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    launches = AgentLaunches(store, sandbox)
    launches.start("builder", handle, "script", {}, usd_limit=10, max_attempts=2)
    sandbox.status = RunStatus.EXITED
    handle.attempt_dir.mkdir()
    (handle.attempt_dir / "exit").write_text("0")
    assert launches.observe("builder") == RunStatus.EXITED
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1

    def broken_collection() -> None:
        raise OSError("usage temporarily unreadable")

    with pytest.raises(OSError, match="usage temporarily unreadable"):
        launches.reconcile("builder", collect=broken_collection)
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    collected = []
    launches.reconcile("builder", collect=lambda: collected.append("usage"))
    assert collected == ["usage"]
    assert RuntimeJobs(store).active_agents("synthetic") == []
    assert not launches.start("builder", handle, "script", {}, usd_limit=10, max_attempts=2)
    store.close()


def test_a_sandbox_confirmed_gone_releases_its_lease_as_failed_after_collection(
    tmp_path: Path,
) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    launches = AgentLaunches(store, sandbox)
    launches.start("builder", handle, "script", {}, usd_limit=10, max_attempts=2)
    collected: list[str] = []

    sandbox.status = RunStatus.ORPHANED
    assert not launches.reconcile("builder", collect=lambda: collected.append("usage"))
    assert collected == []
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1

    def broken_collection() -> None:
        raise OSError("usage temporarily unreadable")

    sandbox.gone = "sandbox-absent"
    with pytest.raises(OSError, match="usage temporarily unreadable"):
        launches.reconcile("builder", collect=broken_collection)
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    assert launches.reconcile("builder", collect=lambda: collected.append("usage"))
    assert collected == ["usage"]
    assert RuntimeJobs(store).active_agents("synthetic") == []
    assert launches.reconcile("builder", collect=lambda: None)
    assert [
        json.loads(row["payload"])
        for row in store.runtime.db.execute(
            "SELECT payload FROM operator_events WHERE action='agent-finished'"
        )
    ] == [{"invocation": "builder", "status": "failed", "evidence": "sandbox-absent"}]
    store.close()


def test_replay_cannot_replace_the_execution_contract(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    launches = AgentLaunches(store, sandbox)
    launches.start("builder", handle, "original", {}, usd_limit=10, max_attempts=2)
    with pytest.raises(ValueError, match="immutable"):
        launches.start("builder", handle, "replacement", {}, usd_limit=10, max_attempts=2)
    assert sandbox.launches == 1
    store.close()


def test_another_invocation_cannot_reuse_launch_evidence(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    for invocation in ("first", "second"):
        store.runtime.start_invocation(invocation, run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    launches = AgentLaunches(store, sandbox)
    launches.start("first", handle, "script", {}, usd_limit=10, max_attempts=2)
    with pytest.raises(ValueError, match="evidence directory"):
        launches.start("second", handle, "script", {}, usd_limit=10, max_attempts=2)
    assert sandbox.launches == 1
    store.close()


def test_failed_intent_transaction_restores_approval_and_capacity(tmp_path: Path) -> None:
    import sqlite3

    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    store.runtime.configure("project", "synthetic", {"mode": "approval"})
    store.runtime.approve(run.id, "builder")
    store.runtime.db.execute(
        "CREATE TRIGGER fail_intent BEFORE INSERT ON effects "
        "BEGIN SELECT RAISE(ABORT, 'storage failure'); END"
    )
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
        AgentLaunches(store, sandbox).start(
            "builder", handle, "script", {}, usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    assert RuntimeJobs(store).active_agents("synthetic") == []
    assert store.runtime.settings("run", run.id)["approved_invocation"] == "builder"
    assert store.effects(run.id) == []
    store.close()


def test_paid_launch_refuses_an_enclosing_transaction(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    with store.runtime.transaction(), pytest.raises(ValueError, match="outer transaction"):
        AgentLaunches(store, sandbox).start(
            "builder", handle, "script", {}, usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    store.close()


def test_preexisting_terminal_evidence_cannot_authorize_a_new_launch(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    handle.attempt_dir.mkdir()
    (handle.attempt_dir / "exit").write_text("0")
    sandbox = Sandbox()
    with pytest.raises(ValueError, match="existing execution evidence"):
        AgentLaunches(store, sandbox).start(
            "builder", handle, "script", {}, usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    assert (handle.attempt_dir / "exit").read_text() == "0"
    assert RuntimeJobs(store).active_agents("synthetic") == []
    store.close()


def test_bare_reservation_is_not_permission_to_launch_later(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("builder", run.id, 1, "builder", {})
    assert RuntimeJobs(store).schedule_agent("builder", usd_limit=10, max_attempts=2)
    store.runtime.configure("project", "synthetic", {"mode": "approval"})
    handle = RunHandle(run.id, 1, "factory-build-synthetic", str(tmp_path), tmp_path / "attempt")
    sandbox = Sandbox()
    with pytest.raises(ValueError, match="reservation has no launch intent"):
        AgentLaunches(store, sandbox).start(
            "builder", handle, "script", {}, usd_limit=10, max_attempts=2
        )
    assert sandbox.launches == 0
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    store.close()


def _exclusive_sandbox_contender(
    database: str, directory: str, invocation_id: str, barrier: object
) -> None:
    from typing import Any, cast

    from factory.execution import ProjectQueued

    class RecordedSandbox(Sandbox):
        def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
            with (Path(directory) / "spawns").open("a") as stream:
                stream.write(invocation_id + "\n")

    store = Store(Path(database))
    try:
        invocation = store.runtime.invocation(invocation_id)
        assert invocation is not None
        handle = RunHandle(
            invocation["run_id"],
            1,
            "factory-build-racing",
            directory,
            Path(directory) / invocation_id.replace(":", "-"),
        )
        cast(Any, barrier).wait(timeout=15)
        try:
            AgentLaunches(store, RecordedSandbox()).start(
                invocation_id, handle, "script", {}, usd_limit=10, max_attempts=2
            )
        except ProjectQueued:
            with (Path(directory) / "queued").open("a") as stream:
                stream.write(invocation_id + "\n")
    finally:
        store.close()
