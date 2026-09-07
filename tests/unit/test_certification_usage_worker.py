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
        print(json.dumps({'method':'thread/tokenUsage/updated','params':{'threadId':'thread-1','tokenUsage':{'total':count,'last':count}}}),flush=True)
        print(json.dumps({'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}}),flush=True)
"""


def exercise(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    stage: str = "initial",
    invalid: bool = False,
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
            [sys.executable, "-u", "-c", SERVER, "invalid" if invalid else "valid"],
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


def test_one_admitted_turn_captures_raw_events_and_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events = exercise(monkeypatch, capsys)
    accounting = next(e for e in events if e["type"] == "factory.usage")
    assert accounting["complete"] is True
    assert accounting["usage"]["input_tokens"] == 20
    assert accounting["pricing_complete"] is False
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
