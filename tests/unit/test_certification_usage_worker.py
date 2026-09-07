"""Real pipe transport with a controlled JSON-RPC server at the process boundary."""

import json
import subprocess
import sys
from typing import Any

import pytest

from factory.agent import app_server_worker
from factory.agent import certification_usage_worker as usage

SERVER = r"""
import json, sys
for line in sys.stdin:
    event=json.loads(line)
    method=event['method']
    if 'id' not in event: continue
    result={}
    if method=='hooks/list': result={'data': []}
    if method in ('thread/start','thread/resume'): result={'thread': {'id':'thread-1'}}
    if method=='turn/start': result={'turn': {'id':'turn-1'}}
    print(json.dumps({'id':event['id'],'result':result}),flush=True)
    if method in ('turn/start','thread/compact/start'):
        if method=='thread/compact/start':
            print(json.dumps({'method':'item/completed','params':{'threadId':'thread-1','item':{'type':'contextCompaction'}}}),flush=True)
        count={'inputTokens':20,'cachedInputTokens':5,'outputTokens':3,'totalTokens':23}
        if sys.argv[1]=='invalid': count['inputTokens']=True
        notifications=json.loads(sys.argv[2]) if len(sys.argv)>2 else None
        if notifications is None:
            notifications=[{'method':'thread/tokenUsage/updated','params':{'threadId':'thread-1','turnId':'turn-1','tokenUsage':{'total':count,'last':count}}}]
        for notification in notifications:
            print(json.dumps(notification),flush=True)
        print(json.dumps({'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}}),flush=True)
"""


def exercise(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    stage: str = "initial",
    invalid: bool = False,
    notifications: list[dict[str, Any]] | None = None,
    overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    monkeypatch.setattr(usage, "helpers", lambda: app_server_worker)
    launched = []

    def start(
        argv: list[str], request: dict[str, Any], *, capture_stderr: bool = False
    ) -> subprocess.Popen[str]:
        assert capture_stderr
        launched.append(request)
        assert "hooks=" in argv[-1]
        return subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                SERVER,
                "invalid" if invalid else "valid",
                json.dumps(notifications),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    monkeypatch.setattr(app_server_worker, "start_server", start)
    monkeypatch.setattr(app_server_worker, "project_hooks", lambda _: {"Stop": []})
    monkeypatch.setattr(app_server_worker, "discovered_hooks", lambda *_: [])
    monkeypatch.setattr(app_server_worker, "project_hook_coverage", lambda *_, **__: True)
    monkeypatch.setattr(
        app_server_worker, "hook_overrides", lambda *_: {"hooks.state": {"key": "hash"}}
    )
    request = {
        "runtime_identity": {"runtime_path": "/native", "runtime_sha256": "a" * 64},
        "workdir": "/fixture",
        "stage": stage,
        "model": "model",
        "effort": "low",
        "prompt": "source-owned probe",
        "timeout_seconds": 5,
    }
    if stage != "initial":
        request["thread_id"] = "thread-1"
    request.update(overrides or {})
    assert usage.run(request) == 0
    assert len(launched) == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    starts = [
        e["event"]
        for e in events
        if e["type"] == "factory.rpc"
        and e["direction"] == "sent"
        and e["event"]["method"] in {"thread/start", "thread/resume"}
    ]
    assert starts[0]["params"]["config"]["agents.enabled"] is False
    return events


def notification(total: int, last: int, *, turn: str = "turn-1") -> dict[str, Any]:
    def counters(value: int) -> dict[str, int]:
        return {
            "inputTokens": value,
            "cachedInputTokens": 0,
            "outputTokens": 0,
            "totalTokens": value,
        }

    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": "thread-1",
            "turnId": turn,
            "tokenUsage": {"total": counters(total), "last": counters(last)},
        },
    }


def test_rerouted_probe_does_not_price_usage_as_requested_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(
        monkeypatch,
        capsys,
        notifications=[
            {
                "method": "model/rerouted",
                "params": {"threadId": "thread-1", "toModel": "different"},
            },
            notification(20, 20),
        ],
    )
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert accounting["pricing_complete"] is False
    assert accounting["requests"] == []


def test_prior_turn_notifications_do_not_become_probe_requests(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(
        monkeypatch,
        capsys,
        notifications=[
            notification(100, 100, turn="previous-turn"),
            notification(20, 20),
        ],
    )
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert accounting["complete"] is True
    assert accounting["pricing_complete"] is True
    assert [r["usage"]["input_tokens"] for r in accounting["requests"]] == [20]


def test_observed_request_is_flushed_before_waiting_for_completion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(monkeypatch, capsys)
    completion = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "factory.rpc" and e["event"].get("method") == "turn/completed"
    )
    retained = [e for e in events[:completion] if e["type"] == "factory.usage"]
    assert len(retained) == 1
    assert retained[0]["complete"] is False
    assert retained[0]["pricing_complete"] is False
    assert retained[0]["requests"][0]["usage"]["input_tokens"] == 20


def test_automatic_compaction_does_not_claim_complete_request_attribution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(
        monkeypatch,
        capsys,
        notifications=[
            {
                "method": "item/completed",
                "params": {"threadId": "thread-1", "item": {"type": "contextCompaction"}},
            },
            notification(20, 20),
        ],
    )
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert accounting["pricing_complete"] is False
    assert accounting["requests"] == []


@pytest.mark.parametrize(
    ("observations", "priced", "complete", "pricing_complete"),
    [
        ([(20, 20), (20, 20), (30, 10)], [20, 10], True, True),
        ([(20, 20), (50, 10), (60, 10)], [20, 10], True, False),
        ([(20, 20), (10, 10), (20, 20), (30, 10)], [20, 10], False, False),
    ],
)
def test_duplicate_coalesced_and_regressed_observations_retain_only_proven_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    observations: list[tuple[int, int]],
    priced: list[int],
    complete: bool,
    pricing_complete: bool,
) -> None:
    events = exercise(
        monkeypatch, capsys, notifications=[notification(*values) for values in observations]
    )
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert [r["usage"]["input_tokens"] for r in accounting["requests"]] == priced
    assert accounting["complete"] is complete
    assert accounting["pricing_complete"] is pricing_complete


@pytest.mark.parametrize("scope", ["thread", "connection"])
def test_resumed_probe_prices_only_new_requests_in_verified_scope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scope: str,
) -> None:
    events = exercise(
        monkeypatch,
        capsys,
        stage="resume",
        notifications=[
            notification(120 if scope == "thread" else 20, 20),
        ],
        overrides={
            "usage_scope": scope,
            "usage_baseline": {
                "inputTokens": 100,
                "cachedInputTokens": 0,
                "outputTokens": 0,
                "totalTokens": 100,
            },
            "model": "gpt-5.6-sol",
        },
    )
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert accounting["complete"] is True
    assert accounting["pricing_complete"] is True
    assert accounting["usage"]["input_tokens"] == 20
    assert [r["usage"]["input_tokens"] for r in accounting["requests"]] == [20]
    assert accounting["requests"][0]["model"] == "gpt-5.6-sol"
    assert accounting["requests"][0]["long_context"] is False


def test_resumed_counter_regression_does_not_price_retained_history(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events = exercise(
        monkeypatch,
        capsys,
        stage="resume",
        notifications=[
            notification(90, 90),
            notification(110, 20),
            notification(120, 10),
        ],
        overrides={
            "usage_scope": "thread",
            "usage_baseline": {
                "inputTokens": 100,
                "cachedInputTokens": 0,
                "outputTokens": 0,
                "totalTokens": 100,
            },
        },
    )
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert accounting["complete"] is False
    assert accounting["pricing_complete"] is False
    assert accounting["usage"]["input_tokens"] == 20
    assert [r["usage"]["input_tokens"] for r in accounting["requests"]] == [10]


def test_one_admitted_turn_captures_raw_events_and_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(monkeypatch, capsys)
    accounting = [e for e in events if e["type"] == "factory.usage"][-1]
    assert accounting["complete"] is True
    assert accounting["usage"]["input_tokens"] == 20
    assert accounting["pricing_complete"] is True
    assert len(accounting["requests"]) == 1
    assert accounting["requests"][0]["usage"] == {
        "input_tokens": 20,
        "cached_input_tokens": 5,
        "cache_write_input_tokens": 0,
        "output_tokens": 3,
        "reasoning_output_tokens": 0,
    }
    assert accounting["requests"][0]["service_tier"] == "standard"
    assert events[-1]["thread_id"] == "thread-1"


@pytest.mark.parametrize("stage", ["resume", "compact"])
def test_unproven_attribution_stays_incomplete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str
) -> None:
    events = exercise(monkeypatch, capsys, stage=stage)
    assert next(e for e in events if e["type"] == "factory.usage")["complete"] is False
    assert events[-1]["compaction_observed"] is (stage == "compact")


def test_malformed_counter_cannot_be_complete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(monkeypatch, capsys, invalid=True)
    assert next(e for e in events if e["type"] == "factory.usage")["complete"] is False


def test_missing_native_identity_never_launches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(usage, "helpers", lambda: app_server_worker)

    def forbidden(*args: Any) -> None:
        pytest.fail("uncertified runtime spawned")

    monkeypatch.setattr(app_server_worker, "start_server", forbidden)
    assert usage.run({"stage": "initial"}) == 1
    assert "native runtime identity" in capsys.readouterr().out


def test_usage_transport_accepts_the_shared_launcher_pipes() -> None:
    process = app_server_worker.start_server(
        [sys.executable, "-u", "-c", 'print("ready")'], {}, capture_stderr=True
    )
    client = usage.Client(process, 5)
    try:
        assert process.stderr is not None
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
    finally:
        client.close()
