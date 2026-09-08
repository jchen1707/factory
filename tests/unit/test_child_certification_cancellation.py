"""Cancelled pre-launch children must drain their already paid certification."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from factory.agent_launches import AgentLaunches
from factory.delegation import DelegationBroker
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import Completed, RunHandle
from factory.workflow_launches import reconcile_run
from tests.unit.test_certification import identity
from tests.unit.test_delegation import setup, task
from tests.unit.test_delegation_cancellation import SignallingSandbox


class ProbeSandbox(SignallingSandbox):
    directory: Path
    current_generation = "host-generation-one"
    process_present = True

    def generation(self, name: str) -> str:
        return self.current_generation

    def exec_sync(self, name: str, argv: list[str]) -> Completed:
        body = str(self.directory / "pgid-body.sh")
        return Completed(
            tuple(argv), 0, f"PID PGID ARGS\n123 123 {body}\n" if self.process_present else "", ""
        )


@pytest.mark.parametrize("parent_ended", [False, True])
@pytest.mark.parametrize("refusal", ["none", "generation", "process", "lost-ack"])
def test_cancelled_request_drains_certifier_without_touching_sibling(
    tmp_path: Path, refusal: str, parent_ended: bool
) -> None:
    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    parent_handle = RunHandle(
        parent["run_id"],
        1,
        "factory-build-parent",
        str(source),
        tmp_path / "parent",
    )
    parent_owner = (parent_handle.run_id, 1, "parent", "agent-launch", "spawn")
    store.intend_effect(*parent_owner)
    store.confirm_effect(
        *parent_owner,
        json.dumps(
            {
                "parent_id": None,
                "handle": asdict(parent_handle) | {"attempt_dir": str(parent_handle.attempt_dir)},
            }
        ),
    )
    store.runtime.db.execute(
        "UPDATE invocations SET metadata=json_set(metadata,'$.events',?) WHERE id='parent'",
        (str(tmp_path / "parent/events"),),
    )
    sandbox = ProbeSandbox()
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("first", task())
    sibling = broker.request("second", task())
    jobs = RuntimeJobs(store)
    name = "factory-review-child-" + request["id"]
    job = jobs.request_certification(request["run_id"], asdict(replace(identity(), sandbox=name)))
    assert jobs.claim_certification(job["id"], now=1, duration=100)
    owner = (request["run_id"], 1, request["id"], "child-execution", "prepare")
    store.intend_effect(*owner)
    store.confirm_effect(*owner, json.dumps({"sandbox": name, "parent_id": "parent"}))
    identifier = "certification:" + job["id"] + ":interrupt"
    directory = tmp_path / "probe"
    sandbox.directory = directory
    store.runtime.start_invocation(
        identifier,
        request["run_id"],
        1,
        "certification",
        {
            "certification_job": job["id"],
            "model": "unpriced",
            "adapter": "codex-exec",
            "events": str(directory / "events"),
            "cost_step": identifier,
        },
    )
    handle = RunHandle(request["run_id"], 1, name, str(source), directory)
    AgentLaunches(store, sandbox).start(
        identifier, handle, "script", {}, usd_limit=10, max_attempts=2
    )
    directory.mkdir()
    (directory / "pgid").write_text("123")
    broker.cancel(request["id"])
    from factory.execution import ProjectQueued

    next_id = "certification:" + job["id"] + ":recovery"
    store.runtime.start_invocation(
        next_id, request["run_id"], 1, "certification", {"certification_job": job["id"]}
    )
    with pytest.raises(ProjectQueued, match="owner is no longer active"):
        AgentLaunches(store, sandbox).start(
            next_id,
            replace(handle, attempt_dir=tmp_path / "next"),
            "script",
            {},
            usd_limit=10,
            max_attempts=2,
        )
    assert sandbox.launches == 1
    if parent_ended:
        broker.cancel(sibling["id"])
        parent_handle.attempt_dir.mkdir()
        (parent_handle.attempt_dir / "exit").write_text("0")
    if refusal == "generation":
        sandbox.current_generation = "replacement"
    if refusal == "process":
        sandbox.process_present = False
    if refusal == "lost-ack":
        sandbox.fail = True
    if refusal == "lost-ack":
        with pytest.raises(ConnectionError):
            reconcile_run(store, tmp_path, sandbox, request["run_id"], "synthetic")
        sandbox.fail = False
    else:
        reconcile_run(store, tmp_path, sandbox, request["run_id"], "synthetic")
    if refusal in {"generation", "process"}:
        assert sandbox.signals == []
        sandbox.current_generation = "host-generation-one"
        sandbox.process_present = True
    reconcile_run(store, tmp_path, sandbox, request["run_id"], "synthetic")
    assert sandbox.signals == [(name, 123)]
    retired = jobs.certification(job["id"])
    assert retired is not None
    assert retired["status"] == "failed"
    assert broker.inspect(sibling["id"])["status"] == ("cancelled" if parent_ended else "pending")
    assert len(jobs.active_agents("synthetic")) == 2
    store.close()
    from factory.store import Store

    store = Store(tmp_path / "factory.db")
    reconcile_run(store, tmp_path, sandbox, request["run_id"], "synthetic")
    assert sandbox.signals == [(name, 123)]
    (directory / "events").write_text(
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 3}})
        + "\n"
    )
    (directory / "exit").write_text("143")
    reconcile_run(store, tmp_path, sandbox, request["run_id"], "synthetic")
    assert {row["invocation_id"] for row in RuntimeJobs(store).active_agents("synthetic")} == (
        set() if parent_ended else {"parent"}
    )
    accounted = store.runtime.invocation(identifier)
    assert accounted is not None
    assert accounted["telemetry"]["usage"]["input_tokens"] == 12
    store.close()
