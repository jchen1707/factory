"""Protected provisioning and controller recovery, with real files and an isolated store."""

import json
from pathlib import Path

from factory.delegation_controller import DelegationController
from factory.sandbox.base import SandboxSpec, Workspace
from factory.store import Store
from tests.unit.test_delegation import setup, task


def test_preparation_retains_private_mounts_and_reopens_owned_mailbox(tmp_path: Path) -> None:
    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    store.runtime.start_invocation(
        "queued", parent["run_id"], 1, "implement:queued", parent["metadata"]
    )
    controller = DelegationController(store, tmp_path / "home")
    original = SandboxSpec(
        project="synthetic",
        role="build",
        name="factory-build-original",
        workspaces=(Workspace(source),),
    )
    spec = controller.prepare(parent["run_id"], 1, "queued", source, original)
    config = controller.configuration("queued", spec)
    inbox, outbox = Path(config["inbox"]), Path(config["outbox"])
    assert spec.name != original.name
    assert spec.workspaces == (
        Workspace(source),
        Workspace(inbox),
        Workspace(outbox, readonly=True),
    )
    assert inbox.parent == outbox.parent
    assert not inbox.is_relative_to(source)
    assert not outbox.is_relative_to(source)
    (inbox / "request.json").write_text(
        json.dumps(
            {
                "call_id": "call",
                "tool": "factory_request_child",
                "arguments": task(),
            }
        )
    )
    # Provisioning does not admit the parent or make an unlaunched mailbox executable.
    assert controller.service_run(parent["run_id"]) == 0
    assert not (outbox / "response.json").exists()
    store.close()
    reopened = Store(tmp_path / "factory.db")
    try:
        restored = DelegationController(reopened, tmp_path / "home")
        assert restored.prepare(parent["run_id"], 1, "queued", source, original) == spec
        assert restored.configuration("queued", spec) == config
    finally:
        reopened.close()


def test_workflow_observation_services_recorded_parent_after_restart(tmp_path: Path) -> None:
    from factory.agent_launches import AgentLaunches
    from factory.delegation import DelegationBroker
    from factory.sandbox.base import RunHandle
    from factory.workflow_launches import reconcile_run
    from tests.unit.test_agent_launches import Sandbox

    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    original = SandboxSpec(
        project="synthetic",
        role="build",
        name="factory-build-original",
        workspaces=(Workspace(source),),
    )
    home = tmp_path / "home"
    controller = DelegationController(store, home)
    # Mounts are provisioned before accounting can retain the final certificate.
    spec = controller.prepare(parent["run_id"], 1, "queued", source, original)
    store.runtime.start_invocation(
        "queued",
        parent["run_id"],
        1,
        "implement:queued",
        parent["metadata"] | {"events": str(tmp_path / "events")},
    )
    config = controller.configuration("queued", spec)
    sandbox = Sandbox()
    handle = RunHandle(parent["run_id"], 1, spec.name, str(source), tmp_path / "attempt")
    assert AgentLaunches(store, sandbox).start(
        "queued",
        handle,
        "script",
        {},
        usd_limit=10,
        max_attempts=2,
    )
    request = Path(config["inbox"]) / "request.json"
    request.write_text(
        json.dumps(
            {
                "call_id": "call",
                "tool": "factory_request_child",
                "arguments": task(),
            }
        )
    )
    store.close()
    reopened = Store(tmp_path / "factory.db")
    try:
        reconcile_run(reopened, home, sandbox, parent["run_id"], "synthetic")
        response = Path(config["outbox"]) / "response.json"
        first = response.read_bytes()
        assert json.loads(first)["result"]["success"] is True
        reconcile_run(reopened, home, sandbox, parent["run_id"], "synthetic")
        assert response.read_bytes() == first
        requests = DelegationBroker(reopened, "queued", source).requests()
        assert len(requests) == 1
        assert requests[0]["status"] == "pending"
        assert sandbox.launches == 1
    finally:
        reopened.close()


def test_same_parent_id_cannot_provision_another_runs_mailbox(tmp_path: Path) -> None:
    import pytest

    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    controller = DelegationController(store, tmp_path / "home")
    base = SandboxSpec(
        project="synthetic",
        role="build",
        name="factory-build-original",
        workspaces=(Workspace(source),),
    )
    controller.prepare(parent["run_id"], 1, "queued", source, base)
    other = store.insert_run(linear_id="SYN-2", project="synthetic", team="SYN")
    snapshot = store.runtime.policy(parent["run_id"])
    assert snapshot is not None
    store.runtime.snapshot_policy(other.id, snapshot)
    with pytest.raises(ValueError, match="ownership"):
        controller.prepare(other.id, 1, "queued", source, base)
    store.close()


def test_provisioning_refuses_mounted_controller_ancestors_before_writing(tmp_path: Path) -> None:
    import pytest

    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    home = tmp_path / "home"
    controller = DelegationController(store, home)
    for readonly in (False, True):
        base = SandboxSpec(
            project="synthetic",
            role="build",
            name="factory-build-original",
            workspaces=(Workspace(tmp_path, readonly=readonly),),
        )
        with pytest.raises(ValueError, match="outside all existing mounts"):
            controller.prepare(parent["run_id"], 1, "queued", source, base)
        assert not home.exists()
    store.close()


def test_provisioning_refuses_symlinked_ancestor_without_writing_through_it(tmp_path: Path) -> None:
    import pytest

    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    home, unrelated = tmp_path / "home", tmp_path / "unrelated"
    home.mkdir()
    unrelated.mkdir()
    (home / "state").symlink_to(unrelated, target_is_directory=True)
    controller = DelegationController(store, home)
    base = SandboxSpec(
        project="synthetic",
        role="build",
        name="factory-build-original",
        workspaces=(Workspace(source),),
    )
    with pytest.raises(OSError, match=r"(Too many levels|Not a directory)"):
        controller.prepare(parent["run_id"], 1, "queued", source, base)
    assert list(unrelated.iterdir()) == []
    store.close()


def test_retained_configuration_refuses_changed_mounts_and_directory_identity(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    import pytest

    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    controller = DelegationController(store, tmp_path / "home")
    base = SandboxSpec(
        project="synthetic",
        role="build",
        name="factory-build-original",
        workspaces=(Workspace(source),),
    )
    spec = controller.prepare(parent["run_id"], 1, "queued", source, base)
    store.runtime.start_invocation(
        "queued", parent["run_id"], 1, "implement:queued", parent["metadata"]
    )
    config = controller.configuration("queued", spec)
    outbox = Path(config["outbox"])
    with pytest.raises(ValueError, match="specification changed"):
        controller.configuration(
            "queued", replace(spec, workspaces=(*spec.workspaces[:-1], Workspace(outbox)))
        )
    outbox.rename(outbox.with_name("retained-outbox"))
    outbox.mkdir()
    with pytest.raises(ValueError, match="identity changed"):
        controller.configuration("queued", spec)
    store.close()


def test_poisoned_mailbox_does_not_prevent_terminal_accounting(tmp_path: Path) -> None:
    from factory.agent_launches import AgentLaunches
    from factory.runtime_jobs import RuntimeJobs
    from factory.sandbox.base import RunHandle, RunStatus
    from factory.workflow_launches import reconcile_run
    from tests.unit.test_agent_launches import Sandbox

    store, source = setup(tmp_path)
    parent = store.runtime.invocation("parent")
    assert parent is not None
    home = tmp_path / "home"
    controller = DelegationController(store, home)
    spec = controller.prepare(
        parent["run_id"],
        1,
        "queued",
        source,
        SandboxSpec(
            project="synthetic",
            role="build",
            name="factory-build-original",
            workspaces=(Workspace(source),),
        ),
    )
    events = tmp_path / "events"
    events.write_text(
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
    store.runtime.start_invocation(
        "queued",
        parent["run_id"],
        1,
        "implement:queued",
        parent["metadata"]
        | {
            "events": str(events),
            "adapter": "codex-exec",
            "model": "unknown-model",
            "cost_step": "child-mailbox-test",
        },
    )
    config = controller.configuration("queued", spec)
    sandbox = Sandbox()
    handle = RunHandle(parent["run_id"], 1, spec.name, str(source), tmp_path / "attempt")
    AgentLaunches(store, sandbox).start(
        "queued", handle, "script", {}, usd_limit=10, max_attempts=2
    )
    (Path(config["inbox"]) / "request.json").write_bytes(b"x" * (80 * 1024 + 1))
    handle.attempt_dir.mkdir()
    (handle.attempt_dir / "exit").write_text("0")
    sandbox.status = RunStatus.EXITED
    store.close()
    reopened = Store(tmp_path / "factory.db")
    try:
        reconcile_run(reopened, home, sandbox, parent["run_id"], "synthetic")
        assert [
            row["invocation_id"] for row in RuntimeJobs(reopened).active_agents("synthetic")
        ] == ["parent"]
        invocation = reopened.runtime.invocation("queued")
        assert invocation is not None
        assert invocation["telemetry"]["usage"]["input_tokens"] == 12
        assert invocation["telemetry"]["estimate"]["complete"] is False
        fault = reopened.find_effect(parent["run_id"], 1, "queued", "delegation-mailbox", "failure")
        assert fault is not None
        assert fault.status == "confirmed"
        assert str(tmp_path) not in (fault.external_id or "")
        reconcile_run(reopened, home, sandbox, parent["run_id"], "synthetic")
        repeated = reopened.runtime.invocation("queued")
        assert repeated is not None
        assert repeated["telemetry"] == invocation["telemetry"]
    finally:
        reopened.close()
