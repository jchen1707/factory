"""Runner durability and shared paid admission, with a fake execution boundary."""

from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from factory.agent_launches import AgentLaunches
from factory.certification import CertificationIdentity, Certifications
from factory.certification_runner import CertificationRunner, PreparedProbe
from factory.execution import AgentApprovalRequired, ProjectQueued
from factory.machine import Blocked
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import RunHandle, RunStatus
from factory.store import Store
from tests.unit.test_certification import identity, write_report


class Sandbox:
    def __init__(self) -> None:
        self.handles: list[RunHandle] = []
        self.fail_ack = False

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
        self.handles.append(handle)
        if self.fail_ack:
            raise ConnectionError("lost acknowledgement")

    def poll(self, handle: RunHandle) -> RunStatus:
        return RunStatus.RUNNING


class Driver:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.prepared = 0
        self.env: dict[str, str] = {}

    def preflight(self, current: CertificationIdentity) -> None:
        pass

    def initialize(self, job: dict[str, Any]) -> None:
        pass

    def steps(self, current: CertificationIdentity) -> tuple[str, ...]:
        return ("first", "second")

    def environment(self, job: dict[str, Any], step: str) -> Mapping[str, str]:
        return self.env

    def prepare(self, job: dict[str, Any], step: str, invocation_id: str) -> PreparedProbe:
        self.prepared += 1
        directory = self.root / "execution" / job["id"] / step
        directory.mkdir(parents=True)
        return PreparedProbe(
            RunHandle(job["run_id"], 1, job["identity"]["sandbox"], str(directory), directory),
            "trusted script",
            directory / "events.jsonl",
            {"model": "synthetic", "adapter": "app-server"},
        )

    def advance(self, job: dict[str, Any], step: str, handle: RunHandle, status: RunStatus) -> None:
        pass

    def accept_exit(self, job: dict[str, Any], step: str, code: int) -> bool:
        return code == 0

    def evaluate(self, job: dict[str, Any]) -> None:
        write_report(self.root / "certifications", job)


def runner(store: Store, root: Path, sandbox: Sandbox, driver: Driver) -> CertificationRunner:
    return CertificationRunner(
        Certifications(store, root / "certifications", writable_roots=()),
        AgentLaunches(store, sandbox),
        driver,
        identity,
        home=root,
        usd_limit=10,
        max_attempts=2,
    )


def test_restart_and_expired_lease_never_duplicate_paid_probe(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    sandbox.fail_ack = True
    with pytest.raises(ConnectionError):
        service.advance(job["id"], now=1)
    store.close()
    reopened = Store(tmp_path / "db")
    service = runner(reopened, tmp_path, sandbox, driver)
    assert service.advance(job["id"], now=999)["status"] == "checking"
    assert len(sandbox.handles) == driver.prepared == 1
    assert len(RuntimeJobs(reopened).active_agents("synthetic")) == 1
    reopened.close()


def test_each_probe_requires_its_own_approval_and_releases_accounting_first(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"mode": "approval"})
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    first = f"certification:{job['id']}:first"
    with pytest.raises(AgentApprovalRequired):
        service.advance(job["id"], now=1)
    assert sandbox.handles == []
    store.runtime.approve(run.id, first)
    service.advance(job["id"], now=2)
    (sandbox.handles[0].attempt_dir / "exit").write_text("0")
    with pytest.raises(AgentApprovalRequired):
        service.advance(job["id"], now=3)
    assert len(sandbox.handles) == 1
    assert RuntimeJobs(store).active_agents("synthetic") == []
    second = f"certification:{job['id']}:second"
    store.runtime.approve(run.id, second)
    service.advance(job["id"], now=4)
    (sandbox.handles[1].attempt_dir / "exit").write_text("0")
    assert service.advance(job["id"], now=5)["status"] == "passed"
    assert service.ensure(run.id)["status"] == "passed"
    assert len(sandbox.handles) == driver.prepared == 2
    store.close()


def test_manual_mode_and_changed_identity_cannot_launch(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    with pytest.raises(Blocked, match="certification-required"):
        service.ensure(run.id)
    job = service.ensure(run.id, automatic=True)
    service.observe = lambda: replace(identity(), generation="recreated")
    with pytest.raises(Blocked, match="certification-stale"):
        service.advance(job["id"], now=1)
    assert sandbox.handles == []
    assert service.status(job["id"])["status"] == "failed"
    store.close()


def test_capacity_queue_preserves_preparation_and_changed_environment_refuses(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"max_active_agents": 1})
    store.runtime.start_invocation("busy", run.id, 1, "builder", {})
    RuntimeJobs(store).schedule_agent("busy", usd_limit=10, max_attempts=2)
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    with pytest.raises(ProjectQueued):
        service.advance(job["id"], now=1)
    driver.env = {"PATH": "changed"}
    with pytest.raises(Blocked, match="certification-environment-changed"):
        service.advance(job["id"], now=2)
    assert sandbox.handles == []
    assert driver.prepared == 1
    store.close()


def test_budget_exhaustion_prevents_probe_and_retains_incomplete_cost(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    service.usd_limit = 0
    job = service.ensure(run.id, automatic=True)
    with pytest.raises(Blocked, match="budget-exceeded"):
        service.advance(job["id"], now=1)
    assert sandbox.handles == []
    assert service.status(job["id"])["status"] == "failed"
    store.close()


def test_failed_probe_collects_retained_usage_before_releasing_slot(tmp_path: Path) -> None:
    import json

    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    service.advance(job["id"], now=1)
    directory = sandbox.handles[0].attempt_dir
    (directory / "events.jsonl").write_text(
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 17}})
        + "\n"
    )
    (directory / "exit").write_text("1")
    with pytest.raises(Blocked, match="certification-probe-failed"):
        service.advance(job["id"], now=2)
    invocation = store.runtime.invocation(f"certification:{job['id']}:first")
    assert invocation is not None
    assert invocation["telemetry"]["usage"]["input_tokens"] == 123
    assert RuntimeJobs(store).active_agents("synthetic") == []
    store.close()


def test_published_evidence_tamper_is_refused_after_reopen(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    service.advance(job["id"], now=1)
    (sandbox.handles[0].attempt_dir / "exit").write_text("0")
    service.advance(job["id"], now=2)
    (sandbox.handles[1].attempt_dir / "exit").write_text("0")
    service.advance(job["id"], now=3)
    store.close()
    (tmp_path / "certifications" / job["id"] / "evidence.txt").write_text("tampered")
    reopened = Store(tmp_path / "db")
    with pytest.raises(Blocked, match="unverified evidence"):
        runner(reopened, tmp_path, sandbox, driver).ensure(run.id)
    assert len(sandbox.handles) == 2
    reopened.close()


def _concurrent_runner(database: str, root: str, run_id: str) -> None:
    class RecordedSandbox(Sandbox):
        def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
            with (Path(root) / "spawns").open("a") as stream:
                stream.write("spawn\n")

    store = Store(Path(database))
    try:
        service = runner(store, Path(root), RecordedSandbox(), Driver(Path(root)))
        job = service.ensure(run_id, automatic=True)
        service.advance(job["id"], now=1)
    finally:
        store.close()


def test_independent_controllers_share_one_job_and_paid_launch(tmp_path: Path) -> None:
    import multiprocessing

    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.close()
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=_concurrent_runner, args=(str(tmp_path / "db"), str(tmp_path), run.id)
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert (tmp_path / "spawns").read_text() == "spawn\n"
    reopened = Store(tmp_path / "db")
    assert (
        reopened.runtime.db.execute("SELECT count(*) FROM runtime_certifications").fetchone()[0]
        == 1
    )
    assert len(RuntimeJobs(reopened).active_agents("synthetic")) == 1
    reopened.close()


def test_paused_owner_reconciles_usage_but_does_not_launch_next_probe(tmp_path: Path) -> None:
    from factory.machine import State

    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    service.advance(job["id"], now=1)
    (sandbox.handles[0].attempt_dir / "exit").write_text("0")
    store.runtime.db.execute("UPDATE runs SET state=? WHERE id=?", (State.SUSPENDED, run.id))
    with pytest.raises(ProjectQueued, match="owner is not active"):
        service.advance(job["id"], now=2)
    assert RuntimeJobs(store).active_agents("synthetic") == []
    assert len(sandbox.handles) == 1
    store.close()


def test_cancelled_owner_cannot_admit_prepared_certification(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("probe", run.id, 1, "certification", {})
    store.runtime.db.execute("UPDATE runs SET state='cancelled' WHERE id=?", (run.id,))
    with pytest.raises(ProjectQueued, match="owner is not active"):
        RuntimeJobs(store).schedule_agent("probe", usd_limit=10, max_attempts=2)
    assert RuntimeJobs(store).active_agents("synthetic") == []
    store.close()


def test_approval_cannot_launch_a_modified_prepared_request(tmp_path: Path) -> None:
    class InputDriver(Driver):
        def prepare(self, job: dict[str, Any], step: str, invocation_id: str) -> PreparedProbe:
            prepared = super().prepare(job, step, invocation_id)
            request = prepared.handle.attempt_dir / "request.json"
            request.write_text("approved request")
            return replace(prepared, inputs=(request,))

    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"mode": "approval"})
    sandbox, driver = Sandbox(), InputDriver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    job = service.ensure(run.id, automatic=True)
    with pytest.raises(AgentApprovalRequired):
        service.advance(job["id"], now=1)
    (tmp_path / "execution" / job["id"] / "first" / "request.json").write_text("changed request")
    store.runtime.approve(run.id, f"certification:{job['id']}:first")
    with pytest.raises(Blocked, match="certification-environment-changed"):
        service.advance(job["id"], now=2)
    assert sandbox.handles == []
    store.close()


def test_changed_identity_collects_old_terminal_probe_before_new_job_admission(
    tmp_path: Path,
) -> None:
    import json

    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    old = service.ensure(run.id, automatic=True)
    service.advance(old["id"], now=1)
    directory = sandbox.handles[0].attempt_dir
    (directory / "events.jsonl").write_text(
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 17}})
        + "\n"
    )
    (directory / "exit").write_text("0")
    service.observe = lambda: replace(identity(), generation="new-generation")
    new = service.ensure(run.id, automatic=True)
    assert new["id"] != old["id"]
    service.advance(new["id"], now=2)
    assert len(sandbox.handles) == 2
    invocation = store.runtime.invocation(f"certification:{old['id']}:first")
    assert invocation is not None
    assert invocation["telemetry"]["usage"]["input_tokens"] == 123
    assert service.status(old["id"])["status"] == "failed"
    store.close()


def test_changed_identity_preserves_ambiguous_old_probe_ownership(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    old = service.ensure(run.id, automatic=True)
    service.advance(old["id"], now=1)
    service.observe = lambda: replace(identity(), generation="new-generation")
    new = service.ensure(run.id, automatic=True)
    assert service.status(old["id"])["status"] == "checking"
    with pytest.raises(ProjectQueued, match="sandbox certification"):
        service.advance(new["id"], now=2)
    assert len(sandbox.handles) == 1
    assert len(RuntimeJobs(store).active_agents("synthetic")) == 1
    store.close()


def test_pre_launcher_identity_is_retired_without_launch(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    try:
        run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
        sandbox, driver = Sandbox(), Driver(tmp_path)
        service = runner(store, tmp_path, sandbox, driver)
        old = asdict(identity())
        del old["launcher_sha256"]
        previous = RuntimeJobs(store).request_certification(run.id, old)
        current = service.ensure(run.id, automatic=True)
        assert current["id"] != previous["id"]
        assert service.status(previous["id"])["status"] == "failed"
        assert sandbox.handles == []
    finally:
        store.close()
