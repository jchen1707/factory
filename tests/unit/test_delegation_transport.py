"""Transport uses real files and the durable broker; no model or sandbox is faked as evidence."""

import json
from pathlib import Path

import pytest

from factory.delegation import DelegationBroker
from factory.delegation_transport import DelegationMailbox
from factory.store import Store
from tests.unit.test_delegation import setup, task


def test_mailbox_returns_pending_handle_and_replays_after_controller_restart(
    tmp_path: Path,
) -> None:
    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    call = {"call_id": "call-1", "tool": "factory_request_child", "arguments": task()}
    (inbox / "request.json").write_text(json.dumps(call))
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    assert mailbox.service()
    first = json.loads((outbox / "response.json").read_text())
    result = json.loads(first["result"]["contentItems"][0]["text"])
    assert first["result"]["success"] is True
    assert result["status"] == "pending"
    assert set(result) == {"handle", "status", "result"}
    assert len(mailbox.broker.requests()) == 1
    store.close()
    reopened = Store(tmp_path / "factory.db")
    try:
        restored = DelegationMailbox(DelegationBroker(reopened, "parent", source), inbox, outbox)
        assert restored.service()
        assert json.loads((outbox / "response.json").read_text()) == first
        assert len(restored.broker.requests()) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("resume", [False, True])
def test_worker_transports_owned_request_and_pending_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    from factory.agent import app_server_worker as worker
    from tests.unit.test_app_server_worker import run_client, turn_end, turn_response

    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    monkeypatch.setattr(worker.time, "sleep", lambda _: mailbox.service())
    event = {
        "id": "server-call",
        "method": "item/tool/call",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "call-1",
            "tool": "factory_request_child",
            "arguments": task(),
        },
    }
    code, _, sent = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), event, turn_end()],
        resume=resume,
        delegation=mailbox.configuration(),
    )
    assert code == 0
    start = next(m for m in sent if m.get("method", "").startswith("thread/"))
    assert start["params"]["config"]["agents.enabled"] is False
    # Captured 0.153.4: registration persists on the thread; resume has no dynamicTools field.
    if resume:
        assert "dynamicTools" not in start["params"]
    else:
        assert {t["name"] for t in start["params"]["dynamicTools"]} == {
            "factory_request_child",
            "factory_child_status",
            "factory_cancel_child",
        }
    reply = next(m for m in sent if m.get("id") == "server-call")
    assert reply["result"]["success"] is True
    payload = json.loads(reply["result"]["contentItems"][0]["text"])
    assert payload["status"] == "pending"
    assert mailbox.broker.inspect(payload["handle"])["child_id"] is None
    store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "bind_child"},
        {"parent_id": "other"},
        {"arguments": {"handle": "a" * 32}},
        {"arguments": task() | {"parent_id": "other"}},
    ],
)
def test_mailbox_refuses_untrusted_requests_without_disclosing_host_state(
    tmp_path: Path, change: dict
) -> None:
    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    call = {"call_id": "call-1", "tool": "factory_request_child", "arguments": task()} | change
    (inbox / "request.json").write_text(json.dumps(call))
    assert mailbox.service()
    reply = json.loads((outbox / "response.json").read_text())["result"]
    assert reply["success"] is False
    assert str(tmp_path) not in json.dumps(reply)
    assert mailbox.broker.requests() == []
    store.close()


@pytest.mark.parametrize("kind", ["symlink", "oversized", "fifo"])
def test_mailbox_refuses_unsafe_files_without_blocking_or_modifying_target(
    tmp_path: Path, kind: str
) -> None:
    import os

    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    target = tmp_path / "preserved"
    target.write_text("unchanged")
    path = inbox / "request.json"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "oversized":
        path.write_bytes(b"x" * (80 * 1024 + 1))
    else:
        os.mkfifo(path)
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    with pytest.raises((OSError, ValueError)):
        mailbox.service()
    assert not (outbox / "response.json").exists()
    assert target.read_text() == "unchanged"
    assert mailbox.broker.requests() == []
    store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "spawn_agent"},
        {"namespace": "foreign"},
        {"threadId": "foreign"},
    ],
)
def test_worker_refuses_unregistered_or_foreign_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict
) -> None:
    from tests.unit.test_app_server_worker import run_client, turn_end, turn_response

    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    event = {
        "id": "server-call",
        "method": "item/tool/call",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "call-1",
            "tool": "factory_request_child",
            "arguments": task(),
            **change,
        },
    }
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), event, turn_end()],
        delegation=mailbox.configuration(),
    )
    assert code == 1
    assert any(e["type"] == "factory.failed_usage" for e in emitted)
    assert not (inbox / "request.json").exists()
    assert mailbox.broker.requests() == []
    store.close()


def test_transport_call_identity_cannot_cancel_a_different_child_on_replay(tmp_path: Path) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    first, second = broker.request("a", task()), broker.request("b", task())
    mailbox = DelegationMailbox(broker, tmp_path / "inbox", tmp_path / "outbox")
    call = {
        "call_id": "cancel",
        "tool": "factory_cancel_child",
        "arguments": {"handle": first["id"]},
    }
    reply = mailbox.dispatch(call)
    assert mailbox.dispatch(call) == reply
    with pytest.raises(ValueError, match="immutable"):
        mailbox.dispatch(call | {"arguments": {"handle": second["id"]}})
    assert broker.inspect(second["id"])["status"] == "pending"
    store.close()


def test_result_transport_preserves_maximum_escaped_broker_result(tmp_path: Path) -> None:
    from factory.runtime_jobs import RuntimeJobs

    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("a", task())
    store.runtime.start_invocation(
        "child",
        request["run_id"],
        1,
        "child:test",
        {"parent_id": "parent", "policy_revision": 1, "semantic_role": "test_designer"},
    )
    broker.bind_child(request["id"], "child")
    assert RuntimeJobs(store).schedule_agent(
        "child", parent_id="parent", usd_limit=10, max_attempts=2
    )
    RuntimeJobs(store).finish_agent("child", status="completed")
    result = {"summary": "\\" * 32000}
    broker.publish_result(request["id"], result)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    (inbox / "request.json").write_text(
        json.dumps(
            {
                "call_id": "status",
                "tool": "factory_child_status",
                "arguments": {"handle": request["id"]},
            }
        )
    )
    mailbox = DelegationMailbox(broker, inbox, outbox)
    assert mailbox.service()
    response = json.loads((outbox / "response.json").read_text())
    assert json.loads(response["result"]["contentItems"][0]["text"])["result"] == result
    store.close()


def test_controller_timeout_leaves_request_recoverable_and_usage_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory.agent import app_server_worker as worker
    from tests.unit.test_app_server_worker import run_client, turn_end, turn_response

    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    clock = iter([0, 301])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    event = {
        "id": "server-call",
        "method": "item/tool/call",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "callId": "call-1",
            "tool": "factory_request_child",
            "arguments": task(),
        },
    }
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), event, turn_end()],
        delegation=mailbox.configuration(),
    )
    assert code == 1
    assert any(e["type"] == "factory.usage" and e["complete"] is False for e in emitted)
    assert mailbox.broker.requests() == []
    # A new controller can recover the request even though the worker disconnected.
    assert mailbox.service()
    assert len(mailbox.broker.requests()) == 1
    assert mailbox.broker.requests()[0]["status"] == "pending"
    store.close()


def test_refused_call_stays_refused_when_capacity_changes(tmp_path: Path) -> None:
    store, source = setup(tmp_path)
    broker = DelegationBroker(store, "parent", source)
    first = broker.request("a", task())
    broker.request("b", task())
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    mailbox = DelegationMailbox(broker, inbox, outbox)
    call = {"call_id": "retry", "tool": "factory_request_child", "arguments": task()}
    (inbox / "request.json").write_text(json.dumps(call))
    assert mailbox.service()
    refused = (outbox / "response.json").read_bytes()
    assert json.loads(refused)["result"]["success"] is False
    broker.cancel(first["id"])
    assert mailbox.service()
    assert (outbox / "response.json").read_bytes() == refused
    (inbox / "request.json").write_text(json.dumps(call | {"call_id": "new-call"}))
    assert mailbox.service()
    assert json.loads((outbox / "response.json").read_bytes())["result"]["success"] is True
    store.close()


@pytest.mark.parametrize("poll_delay", [61, 125])
@pytest.mark.parametrize("corrected", [False, True])
def test_back_to_back_requests_survive_normal_controller_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, poll_delay: int, corrected: bool
) -> None:
    """Replay FRO-12: absolute-path refusal, then a call just after a poll."""
    from factory.agent import app_server_worker as worker

    store, source = setup(tmp_path)
    inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
    inbox.mkdir()
    outbox.mkdir()
    mailbox = DelegationMailbox(DelegationBroker(store, "parent", source), inbox, outbox)
    now = 28.0
    next_poll = 60.0

    def sleep(seconds: float) -> None:
        nonlocal now, next_poll
        now += seconds
        if now >= next_poll:
            mailbox.service()
            # Timer plus controller work, optionally missing one tick.
            next_poll = now + poll_delay

    monkeypatch.setattr(worker.time, "monotonic", lambda: now)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    params = {
        "threadId": "thread",
        "callId": "first",
        "tool": "factory_request_child",
        "arguments": task() | {"paths": [str(source)]},
    }
    first = worker.delegation_call(mailbox.configuration(), params, "thread")
    assert first["success"] is False
    assert "repository-relative" in first["contentItems"][0]["text"]
    result = worker.delegation_call(
        mailbox.configuration(),
        params
        | {
            "callId": "second",
            "arguments": task() | {"paths": ["."]} if corrected else params["arguments"],
        },
        "thread",
    )
    assert result["success"] is corrected
    assert len(mailbox.broker.requests()) == int(corrected)
    store.close()
