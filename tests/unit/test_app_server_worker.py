"""Protocol fixtures test the client; they are not real runtime compatibility evidence."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory.agent import app_server_worker as worker


def counts(input_tokens: int, cached: int = 0, output: int = 10) -> dict[str, int]:
    return {
        "inputTokens": input_tokens,
        "cachedInputTokens": cached,
        "cacheWriteInputTokens": 3,
        "outputTokens": output,
        "reasoningOutputTokens": 2,
        "totalTokens": input_tokens + output,
    }


def notification(method: str, **params: Any) -> dict[str, Any]:
    return {"method": method, "params": {"threadId": "thread", **params}}


def usage_event(total: dict[str, int], last: dict[str, int]) -> dict[str, Any]:
    return notification(
        "thread/tokenUsage/updated",
        turnId="turn",
        tokenUsage={"total": total, "last": last, "modelContextWindow": 1000},
    )


def run_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
    *,
    baseline: dict[str, int] | None = None,
    resume: bool = False,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    class Input(io.StringIO):
        def close(self) -> None:
            pass

    sent = Input()
    protocol = [
        {"id": 1, "result": {}},
        {
            "id": 3,
            "result": {
                "data": [
                    {
                        "model": "gpt-5.6-sol",
                        "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                    }
                ]
            },
        },
        {"id": 4, "result": {"thread": {"id": "thread"}}},
        *events,
    ]
    process = SimpleNamespace(
        stdin=sent,
        stdout=io.StringIO("\n".join(json.dumps(e) for e in protocol) + "\n"),
        wait=lambda **kwargs: 0,
    )
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: process)
    emitted: list[dict[str, Any]] = []

    def capture(event: dict[str, Any]) -> None:
        emitted.append(json.loads(json.dumps(event)))

    monkeypatch.setattr(worker, "emit", capture)
    (tmp_path / "prompt").write_text("Implement one slice")
    (tmp_path / "schema").write_text('{"type":"object"}')
    result = worker.run(
        {
            "model": "gpt-5.6-sol",
            "effort": "high",
            "vault": "/vault",
            "workdir": str(tmp_path),
            "prompt": str(tmp_path / "prompt"),
            "schema": str(tmp_path / "schema"),
            "output": str(tmp_path / "output"),
            "context_semantics_verified": True,
            "resume_session": "thread" if resume else None,
            "usage_baseline": baseline,
        }
    )
    return result, emitted, [json.loads(line) for line in sent.getvalue().splitlines()]


def turn_response() -> dict[str, Any]:
    return {"id": 5, "result": {"turn": {"id": "turn"}}}


def turn_end(status: str = "completed") -> dict[str, Any]:
    return notification("turn/completed", turn={"id": "turn", "status": status})


def test_resume_subtracts_every_counter_and_retains_failed_turn_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = counts(500, 100, 50)
    delta = counts(200, 40, 20)
    total = {k: before[k] + v for k, v in delta.items()}
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [usage_event(total, delta), turn_response(), turn_end("failed")],
        baseline=before,
        resume=True,
    )
    assert code == 1
    observed = [event for event in emitted if event["type"] == "factory.usage"][-1]
    assert observed["usage"] == worker.usage_delta(delta, {})
    assert observed["complete"]
    assert observed["pricing_complete"]
    assert len(observed["requests"]) == 1
    assert any(e["type"] == "factory.context" for e in emitted)


def test_resume_without_baseline_does_not_rebill_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), usage_event(counts(700), counts(200)), turn_end()],
        resume=True,
    )
    assert code == 0
    observed = [event for event in emitted if event["type"] == "factory.usage"][-1]
    assert observed["usage"] is None
    assert not observed["complete"]
    assert not observed["pricing_complete"]
    assert observed["thread_total"]["inputTokens"] == 700


def test_usage_before_rpc_response_and_duplicate_notification_are_not_lost_or_double_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = usage_event(counts(500), counts(500))
    code, emitted, _ = run_client(
        tmp_path, monkeypatch, [update, turn_response(), update, turn_end()]
    )
    assert code == 0
    observed = [event for event in emitted if event["type"] == "factory.usage"][-1]
    assert len(observed["requests"]) == 1
    assert observed["usage"]["input_tokens"] == 500


def test_compaction_is_requested_after_turn_completion_and_invalidates_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, emitted, sent = run_client(
        tmp_path,
        monkeypatch,
        [
            turn_response(),
            usage_event(counts(810), counts(810)),
            turn_end(),
            {"id": 6, "result": {}},
            notification("item/completed", item={"id": "compact", "type": "contextCompaction"}),
        ],
    )
    assert code == 0
    assert sent[-1]["method"] == "thread/compact/start"
    assert sent[-2]["params"]["serviceTierForTurn"] == "default"
    assert any(e["type"] == "factory.context.invalidated" for e in emitted)


def test_regressed_usage_is_incomplete() -> None:
    assert worker.usage_delta(counts(100), counts(200)) is None


@pytest.mark.parametrize("recover", [False, True])
def test_regressed_then_repeated_totals_do_not_price_the_same_request_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    one = counts(100, 20, 10)
    two = {key: value * 2 for key, value in one.items()}
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [
            turn_response(),
            usage_event(one, one),
            usage_event(two, one),
            usage_event(one, one),
            *([usage_event(two, one)] if recover else []),
            turn_end(),
        ],
    )
    assert code == 0
    observed = [event for event in emitted if event["type"] == "factory.usage"][-1]
    assert sum(request["usage"]["input_tokens"] for request in observed["requests"]) == 200
    assert observed["thread_total"] == two
    assert observed["usage"] == worker.usage_delta(two, {})
    assert not observed["complete"]
    assert not observed["pricing_complete"]


def test_normalized_usage_survives_a_stream_cut_before_turn_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), usage_event(counts(100), counts(100)), turn_end()],
    )
    assert code == 0
    # A process killed here retains exactly this prefix; its finally block never runs.
    completed_index = next(
        index
        for index, event in enumerate(emitted)
        if event["type"] == "factory.runtime" and event["event"].get("method") == "turn/completed"
    )
    retained = [event for event in emitted[:completed_index] if event["type"] == "factory.usage"]
    assert retained
    observed = retained[-1]
    assert observed["usage"]["input_tokens"] == 100
    assert observed["thread_total"]["inputTokens"] == 100
    assert len(observed["requests"]) == 1
    assert not observed["complete"]
    assert not observed["pricing_complete"]
