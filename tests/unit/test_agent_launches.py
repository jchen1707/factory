"""Durable paid launch ownership through isolated stores and the sandbox boundary."""

from collections.abc import Mapping
from pathlib import Path

from factory.agent_launches import AgentLaunches
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import RunHandle, RunStatus
from factory.store import Store


class Sandbox:
    def __init__(self) -> None:
        self.launches = 0
        self.status = RunStatus.RUNNING

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
        self.launches += 1

    def poll(self, handle: RunHandle) -> RunStatus:
        return self.status


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


def test_certification_and_application_exclude_each_other_in_same_sandbox(tmp_path: Path) -> None:
    import pytest

    from factory.execution import ProjectQueued

    for first_role, second_role in (
        ("builder", "certification"),
        ("certification", "reviewer"),
        ("certification", "certification"),
    ):
        directory = tmp_path / f"{first_role}-{second_role}"
        directory.mkdir()
        store = Store(directory / "db")
        first_run = store.insert_run(linear_id="SYN-1", project="first", team="SYN")
        second_run = store.insert_run(linear_id="SYN-2", project="second", team="SYN")
        store.runtime.start_invocation("first", first_run.id, 1, first_role, {})
        store.runtime.start_invocation("second", second_run.id, 1, second_role, {})
        sandbox = Sandbox()
        launches = AgentLaunches(store, sandbox)
        first = RunHandle(
            first_run.id, 1, "factory-build-shared", str(directory), directory / "first"
        )
        second = RunHandle(
            second_run.id, 1, "factory-build-shared", str(directory), directory / "second"
        )
        assert launches.start("first", first, "script", {}, usd_limit=10, max_attempts=2)
        with pytest.raises(ProjectQueued, match="sandbox certification"):
            launches.start("second", second, "script", {}, usd_limit=10, max_attempts=2)
        assert not launches.start("first", first, "script", {}, usd_limit=10, max_attempts=2)
        assert sandbox.launches == 1
        distinct = RunHandle(
            second_run.id, 1, "factory-build-distinct", str(directory), directory / "second"
        )
        assert launches.start("second", distinct, "script", {}, usd_limit=10, max_attempts=2)
        assert sandbox.launches == 2
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


def test_independent_controllers_exclude_distinct_jobs_in_same_sandbox(tmp_path: Path) -> None:
    import multiprocessing
    from dataclasses import asdict, replace

    from tests.unit.test_certification import identity

    context = multiprocessing.get_context("spawn")
    for first_role in ("certification", "builder"):
        directory = tmp_path / first_role
        directory.mkdir()
        database = directory / "db"
        store = Store(database)
        run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
        store.runtime.configure("project", "synthetic", {"max_active_agents": 8})
        jobs = [
            RuntimeJobs(store).request_certification(
                run.id,
                asdict(
                    replace(
                        identity(), sandbox="factory-build-racing", authority_sha256=str(index) * 64
                    )
                ),
            )
            for index in (1, 2)
        ]
        assert jobs[0]["fingerprint"] != jobs[1]["fingerprint"]
        invocations = [f"certification:{job['id']}:probe" for job in jobs]
        for invocation_id, role, job in zip(
            invocations, (first_role, "certification"), jobs, strict=True
        ):
            store.runtime.start_invocation(
                invocation_id,
                run.id,
                1,
                role,
                {"certification_job": job["id"]} if role == "certification" else {},
            )
        store.close()
        barrier = context.Barrier(2)
        workers = [
            context.Process(
                target=_exclusive_sandbox_contender,
                args=(str(database), str(directory), invocation_id, barrier),
            )
            for invocation_id in invocations
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)
            assert worker.exitcode == 0
        spawned = (directory / "spawns").read_text().splitlines()
        queued = (directory / "queued").read_text().splitlines()
        assert len(spawned) == len(queued) == 1
        assert set(spawned + queued) == set(invocations)
        reopened = Store(database)
        assert len(RuntimeJobs(reopened).active_agents("synthetic")) == 1
        reopened.close()
