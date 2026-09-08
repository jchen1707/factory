"""Host evidence, not model self-report, determines certification outcomes."""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from factory.agent.certification_probes import CertificationProbeDriver
from factory.certification import Certifications
from factory.machine import Blocked
from factory.sandbox.sbx import SbxAdapter
from factory.store import Store


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def driver(tmp_path: Path) -> CertificationProbeDriver:
    source = tmp_path / "source.json"
    write(
        source,
        {
            "version": 1,
            "steps": [
                "canary",
                "interrupt",
                "recovery",
                "usage-initial",
                "usage-second",
                "usage-compact",
                "usage-postcompact",
                "usage-modelchange",
                "usage-resume",
            ],
            "prompts": {},
            "minimum_detached_seconds": 35,
            "maximum_phase_seconds": 300,
        },
    )
    store = Store(tmp_path / "state.db")
    return CertificationProbeDriver(
        Certifications(store, tmp_path / "host", writable_roots=(tmp_path / "protocol",)),
        cast(SbxAdapter, object()),
        protocol_root=tmp_path / "protocol",
        workdir=str(tmp_path / "target"),
        env={},
        source=source,
        model="gpt-5.6-sol",
        effort="low",
        readonly=False,
        canary_path="contract.json",
        alternate_model="gpt-5.6-terra",
    )


def evidence(driver: CertificationProbeDriver, monkeypatch: pytest.MonkeyPatch) -> dict:
    job = {
        "id": "a" * 32,
        "fingerprint": "b" * 64,
        "identity": {
            "sandbox": "factory-build-probe",
            "runtime_version": "codex-cli test",
            "worker_sha256": hashlib.sha256(
                Path(__file__)
                .parents[2]
                .joinpath("src/factory/agent/app_server_worker.py")
                .read_bytes()
            ).hexdigest(),
            "usage_scope": "connection",
        },
    }
    host = driver._host(job)
    write(host / "before.json", {"head": "same"})
    monkeypatch.setattr(driver, "_git", lambda sandbox: {"head": "same"})
    write(
        host / "interruption.json", {"thread_id": "thread", "baseline": {}, "elapsed_seconds": 40}
    )
    for step in driver._contract()["steps"]:
        directory = driver._attempt(job, step)
        directory.mkdir(parents=True)
        events: list[dict[str, Any]] = []
        if step == "canary":
            events = [
                {
                    "type": "factory.runtime",
                    "event": {
                        "method": "hook/completed",
                        "params": {"run": {"eventName": "preToolUse", "status": "blocked"}},
                    },
                }
            ]
        elif step == "recovery":
            events = [{"type": "factory.usage", "thread_id": "thread"}]
        elif step.startswith("usage-"):
            events = [
                {
                    "type": "factory.certification.phase",
                    "thread_id": "usage-thread",
                    "status": "completed",
                    "total": {"inputTokens": 10},
                    "last": {"inputTokens": 10},
                    "compaction_observed": step == "usage-compact",
                }
            ]
            if step == "usage-compact":
                events.append(
                    {
                        "type": "factory.rpc",
                        "direction": "received",
                        "event": {
                            "method": "thread/tokenUsage/updated",
                            "params": {
                                "tokenUsage": {
                                    "last": {"inputTokens": 0},
                                    "total": {"inputTokens": 10},
                                }
                            },
                        },
                    }
                )
        if step in {"canary", "recovery"}:
            events += [
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"ok":true}'},
                },
                {"type": "turn.completed"},
            ]
        driver._events_path(job, step).write_text("\n".join(json.dumps(e) for e in events))
        write(directory / "output.json", {"ok": True})
        (directory / "exit").write_text("143" if step == "interrupt" else "0")
    return job


def test_model_success_without_hook_event_cannot_certify(
    driver: CertificationProbeDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = evidence(driver, monkeypatch)
    driver._events_path(job, "canary").write_text("")
    with pytest.raises(Blocked, match="certification-hook-failed"):
        driver.evaluate(job)
    assert not (driver._host(job) / "compatibility.json").exists()


def test_changed_dirty_file_inventory_refuses_certification(
    driver: CertificationProbeDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = evidence(driver, monkeypatch)
    monkeypatch.setattr(driver, "_git", lambda sandbox: {"head": "different"})
    with pytest.raises(Blocked, match="certification-isolation-failed"):
        driver.evaluate(job)


def test_unobserved_compaction_refuses_certification(
    driver: CertificationProbeDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = evidence(driver, monkeypatch)
    path = driver._events_path(job, "usage-compact")
    path.write_text(
        path.read_text().replace('"compaction_observed": true', '"compaction_observed": false')
    )
    with pytest.raises(Blocked, match="compaction not observed"):
        driver.evaluate(job)


def test_interruption_requires_exact_terminal_signal(driver: CertificationProbeDriver) -> None:
    job = {"id": "a" * 32}
    assert not driver.accept_exit(job, "interrupt", 0)
    assert not driver.accept_exit(job, "interrupt", 143)
    write(driver._host(job) / "interruption.json", {"thread_id": "thread"})
    assert driver.accept_exit(job, "interrupt", 143)
    assert not driver.accept_exit(job, "recovery", 143)


def test_host_capture_is_embedded_in_check_evidence(
    driver: CertificationProbeDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = evidence(driver, monkeypatch)
    driver.evaluate(job)
    report = json.loads((driver._host(job) / "compatibility.json").read_text())
    check = report["checks"]["hook_enforcement"]
    evidence_bytes = (driver._host(job) / check["evidence"]).read_bytes()
    assert hashlib.sha256(evidence_bytes).hexdigest() == check["sha256"]
    assert "hook/completed" in json.loads(evidence_bytes)["captured"]["canary"]
    assert driver._host(job).is_relative_to(driver.certifications.root)


def test_detached_capture_is_host_owned_and_never_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from factory.sandbox import sbx as sbx_module
    from factory.sandbox.base import RunHandle

    protocol = tmp_path / "protocol"
    protocol.mkdir()
    (protocol / "heartbeat").write_text("ready")
    host_capture = tmp_path / "host-events.jsonl"
    spawned = []

    def spawn(argv: list[str], **kwargs: Any) -> Any:
        spawned.append(argv)
        kwargs["stdout"].write('{"type":"trusted-observation"}\n')
        assert kwargs["start_new_session"] is True
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(sbx_module.subprocess, "Popen", spawn)
    handle = RunHandle("run", 1, "factory-build-probe", str(tmp_path), protocol)
    SbxAdapter().exec_detached(handle, "true", {}, stdout_path=host_capture)
    assert json.loads(host_capture.read_text())["type"] == "trusted-observation"
    with pytest.raises(FileExistsError):
        SbxAdapter().exec_detached(handle, "true", {}, stdout_path=host_capture)
    assert len(spawned) == 1


@pytest.mark.parametrize(
    "fence", ["cancel-intent", "interrupt-intent", "timeout-intent", "retired"]
)
def test_timeout_does_not_resignal_after_cancellation_or_uncertain_interrupt(
    driver: CertificationProbeDriver, monkeypatch: pytest.MonkeyPatch, fence: str
) -> None:
    import os
    import time
    from types import SimpleNamespace

    from factory.runtime_jobs import RuntimeJobs
    from factory.sandbox.base import RunHandle, RunStatus
    from tests.unit.test_certification import identity

    store = driver.certifications.store
    run = store.insert_run(linear_id="SYN-SIGNAL", project="synthetic", team="SYN")
    job = driver.certifications.request(run.id, identity())
    jobs = RuntimeJobs(store)
    token = jobs.claim_certification(job["id"], now=time.time(), duration=60)
    assert token is not None
    directory = driver._attempt(job, "canary")
    directory.mkdir(parents=True)
    (directory / "pgid").write_text("123")
    (directory / "sbx-exec.pid").write_text("123")
    expired = time.time() - 400
    os.utime(directory / "sbx-exec.pid", (expired, expired))
    driver._host(job).mkdir(parents=True)
    driver._events_path(job, "canary").write_text("")
    identifier = "certification:" + job["id"] + ":canary"
    if fence == "retired":
        jobs.finish_certification(
            job["id"], token, now=time.time(), failure="child request cancelled"
        )
    else:
        system, key = (
            ("child-certification-cancel", "signal")
            if fence == "cancel-intent"
            else ("certification", fence.removesuffix("-intent"))
        )
        store.intend_effect(run.id, 1, identifier, system, key)
    signals: list[tuple[str, int]] = []
    monkeypatch.setattr(
        driver,
        "sandbox",
        SimpleNamespace(kill_group=lambda name, pgid: signals.append((name, pgid))),
    )
    monkeypatch.setattr(driver, "_owned_group", lambda handle: 123)
    handle = RunHandle(run.id, 1, identity().sandbox, str(directory), directory)
    driver.advance(job, "canary", handle, RunStatus.RUNNING)
    assert signals == []
