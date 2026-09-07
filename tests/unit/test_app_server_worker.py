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
    readonly: bool = False,
    hook_report: dict[str, Any] | None = None,
    fallback: bool = False,
    initial_hook_report: dict[str, Any] | None = None,
    launches: list[list[str]] | None = None,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    class Input(io.StringIO):
        def close(self) -> None:
            pass

    definitions = {
        "PreToolUse": [
            {"matcher": "apply_patch", "hooks": [{"type": "command", "command": "policy-check"}]}
        ]
    }
    hook_file = tmp_path / ".codex/hooks.json"
    if not hook_file.exists():
        hook_file.parent.mkdir(exist_ok=True)
        hook_file.write_text(json.dumps({"hooks": definitions}))
    sent = Input()
    protocol: list[dict[str, Any]] = [
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
        {
            "id": 4,
            "result": hook_report
            if hook_report is not None
            else {
                "data": [
                    {
                        "cwd": str(tmp_path),
                        "errors": [],
                        "warnings": [],
                        "hooks": [
                            {
                                "key": "project-hook",
                                "source": "sessionFlags" if fallback else "project",
                                "sourcePath": str(hook_file),
                                "eventName": "preToolUse",
                                "handlerType": "command",
                                "command": "policy-check",
                                "matcher": "apply_patch",
                                "async": False,
                                "enabled": True,
                                "currentHash": "sha256:" + "a" * 64,
                            },
                            {"key": "disabled-hook", "enabled": False},
                        ],
                    }
                ]
            },
        },
        {"id": 5, "result": {"thread": {"id": "thread"}}},
        *events,
    ]
    if fallback:
        discovered = protocol[2]["result"]
        protocol = [
            *protocol[:2],
            {
                "id": 4,
                "result": initial_hook_report or {"data": [{"cwd": str(tmp_path), "hooks": []}]},
            },
            {"id": 5, "result": {}},
            {"id": 7, "result": discovered},
            {"id": 8, "result": {"thread": {"id": "thread"}}},
            *[{**e, "id": 9} if e.get("id") == 6 else e for e in events],
        ]
    process = SimpleNamespace(
        stdin=sent,
        stdout=io.StringIO("\n".join(json.dumps(e) for e in protocol) + "\n"),
        wait=lambda **kwargs: 0,
    )

    def spawn(argv: list[str], **kwargs: Any) -> Any:
        if launches is not None:
            launches.append(list(argv))
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", spawn)
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
            "readonly": readonly,
        }
    )
    return result, emitted, [json.loads(line) for line in sent.getvalue().splitlines()]


def turn_response() -> dict[str, Any]:
    return {"id": 6, "result": {"turn": {"id": "turn"}}}


def turn_end(status: str = "completed") -> dict[str, Any]:
    return notification("turn/completed", turn={"id": "turn", "status": status})


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("readonly", [False, True])
def test_thread_sandbox_uses_executing_runtime_wire_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool, readonly: bool
) -> None:
    # Codex 0.146.0 rejects camelCase variants with JSON-RPC -32600.
    code, _, sent = run_client(
        tmp_path, monkeypatch, [turn_response(), turn_end()], resume=resume, readonly=readonly
    )
    assert code == 0
    thread = next(message for message in sent if message["method"].startswith("thread/"))
    assert thread["method"] == ("thread/resume" if resume else "thread/start")
    assert thread["params"]["sandbox"] == ("read-only" if readonly else "danger-full-access")
    assert thread["params"]["config"] == {
        "hooks.state": {"project-hook": {"trusted_hash": "sha256:" + "a" * 64}}
    }
    assert any(message["method"] == "hooks/list" for message in sent)
    assert not any(message["method"].startswith("config/") for message in sent)


@pytest.mark.parametrize("problem", ["missing", "empty", "errors", "warnings", "hash", "cwd"])
def test_unverified_hook_discovery_never_starts_a_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    entry: dict[str, Any] = {
        "cwd": str(tmp_path),
        "errors": ["cannot read hook"] if problem == "errors" else [],
        "warnings": ["ignored hook"] if problem == "warnings" else [],
        "hooks": []
        if problem == "empty"
        else [
            {
                "key": "hook",
                "source": "project",
                "sourcePath": str(tmp_path / ".codex/hooks.json"),
                "eventName": "preToolUse",
                "handlerType": "command",
                "command": "policy-check",
                "matcher": "apply_patch",
                "async": False,
                "enabled": True,
                "currentHash": "invalid" if problem == "hash" else "sha256:" + "a" * 64,
            }
        ],
    }
    if problem == "cwd":
        entry["cwd"] = "/another-project"
    code, emitted, sent = run_client(
        tmp_path,
        monkeypatch,
        [],
        hook_report={"data": [] if problem == "missing" else [entry]},
        fallback=problem == "empty",
    )
    assert code == 1
    assert any(e["type"] == "turn.failed" and "hook" in e["error"]["message"] for e in emitted)
    assert not any(message["method"] in {"thread/start", "thread/resume"} for message in sent)


def test_untrusted_project_hooks_are_loaded_only_for_the_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tomllib

    definitions = {
        "PreToolUse": [
            {"matcher": "apply_patch", "hooks": [{"type": "command", "command": "policy-check"}]}
        ]
    }
    hook_file = tmp_path / ".codex/hooks.json"
    hook_file.parent.mkdir()
    hook_file.write_text(json.dumps({"hooks": definitions}))
    original = hook_file.read_bytes()
    launches: list[list[str]] = []
    code, _, sent = run_client(
        tmp_path, monkeypatch, [turn_response(), turn_end()], fallback=True, launches=launches
    )
    assert code == 0
    assert len(launches) == 2
    override = next(arg for arg in launches[1] if arg.startswith("hooks="))
    assert tomllib.loads(override) == {"hooks": definitions}
    assert hook_file.read_bytes() == original
    assert not any(e["method"].startswith("config/") for e in sent)


@pytest.mark.parametrize("resume", [False, True])
def test_unrelated_hooks_do_not_conceal_omitted_project_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    unrelated = {
        "key": "user-hook",
        "enabled": True,
        "currentHash": "sha256:" + "b" * 64,
        "source": "user",
        "eventName": "stop",
        "command": "unrelated",
    }
    launches: list[list[str]] = []
    code, _, sent = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), turn_end()],
        resume=resume,
        fallback=True,
        launches=launches,
        initial_hook_report={"data": [{"cwd": str(tmp_path), "hooks": [unrelated]}]},
    )
    assert code == 0
    assert len(launches) == 2
    thread = next(e for e in sent if e["method"] in {"thread/start", "thread/resume"})
    assert "project-hook" in thread["params"]["config"]["hooks.state"]


@pytest.mark.parametrize("problem", ["disabled", "command", "matcher", "event", "missing", "async"])
@pytest.mark.parametrize("fallback", [False, True])
def test_incomplete_project_hook_coverage_never_starts_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str, fallback: bool
) -> None:
    project = {
        "key": "project-hook",
        "enabled": problem != "disabled",
        "currentHash": "sha256:" + "a" * 64,
        "source": "sessionFlags" if fallback else "project",
        "sourcePath": str(tmp_path / ".codex/hooks.json"),
        "eventName": "stop" if problem == "event" else "preToolUse",
        "handlerType": "command",
        "command": "wrong" if problem == "command" else "policy-check",
        "matcher": "Read" if problem == "matcher" else "apply_patch",
        "async": problem == "async",
    }
    # Same-source unrelated hook means path-only validation also fails this test.
    unrelated = {**project, "key": "another-hook", "enabled": True, "command": "unrelated"}
    report = {
        "data": [
            {
                "cwd": str(tmp_path),
                "hooks": [unrelated] if problem == "missing" else [project, unrelated],
            }
        ]
    }
    code, emitted, sent = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), turn_end()],
        hook_report=report,
        fallback=fallback,
    )
    assert code == 1
    assert any(e["type"] == "turn.failed" and "hook" in e["error"]["message"] for e in emitted)
    assert not any(e["method"] in {"thread/start", "thread/resume"} for e in sent)


@pytest.mark.parametrize("declared_async", [None, False, True])
@pytest.mark.parametrize("inline", [False, True])
def test_0146_missing_async_metadata_only_accepts_synchronous_handlers(
    tmp_path: Path, declared_async: bool | None, inline: bool
) -> None:
    handler: dict[str, Any] = {"type": "command", "command": "policy-check"}
    if declared_async is not None:
        handler["async"] = declared_async
    definitions = {"Stop": [{"hooks": [handler]}]}
    discovered = [
        {
            "source": "sessionFlags" if inline else "project",
            "sourcePath": str(tmp_path / ".codex/hooks.json"),
            "enabled": True,
            "eventName": "stop",
            "handlerType": "command",
            "command": "policy-check",
            "matcher": None,
        }
    ]
    if declared_async:
        with pytest.raises(RuntimeError, match="project hook discovery is incomplete"):
            worker.project_hook_coverage(discovered, definitions, str(tmp_path), inline=inline)
    else:
        assert worker.project_hook_coverage(discovered, definitions, str(tmp_path), inline=inline)


@pytest.mark.parametrize("problem", ["duplicate", "timeout"])
def test_declared_handler_count_and_timeout_are_required(tmp_path: Path, problem: str) -> None:
    handler = {"type": "command", "command": "policy-check", "timeout": 30}
    definitions = {"Stop": [{"hooks": [handler, handler] if problem == "duplicate" else [handler]}]}
    discovered = [
        {
            "source": "project",
            "sourcePath": str(tmp_path / ".codex/hooks.json"),
            "enabled": True,
            "eventName": "stop",
            "handlerType": "command",
            "command": "policy-check",
            "matcher": None,
            "async": False,
            "timeoutSec": 60 if problem == "timeout" else 30,
        }
    ]
    with pytest.raises(RuntimeError, match="project hook discovery is incomplete"):
        worker.project_hook_coverage(discovered, definitions, str(tmp_path))


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


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("status", ["completed", "failed", "disconnected"])
def test_compaction_waits_for_its_turn_completion_and_retains_late_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queued: bool,
    status: str,
) -> None:
    compact = [
        notification("turn/started", turn={"id": "compact-turn"}),
        notification(
            "item/completed",
            turnId="compact-turn",
            item={"id": "compact", "type": "contextCompaction"},
        ),
        notification(
            "thread/tokenUsage/updated",
            turnId="compact-turn",
            tokenUsage={"total": counts(910), "last": counts(100), "modelContextWindow": 1000},
        ),
        # An unrelated completion cannot finish the pending compact turn.
        notification("turn/completed", turn={"id": "other-turn", "status": "completed"}),
        *(
            [notification("turn/completed", turn={"id": "compact-turn", "status": status})]
            if status != "disconnected"
            else []
        ),
    ]
    response = {"id": 7, "result": {}}
    code, emitted, sent = run_client(
        tmp_path,
        monkeypatch,
        [
            turn_response(),
            usage_event(counts(810), counts(810)),
            turn_end(),
            *([*compact, response] if queued else [response, *compact]),
        ],
    )
    assert code == (0 if status == "completed" else 1)
    assert sent[-1]["method"] == "thread/compact/start"
    assert sent[-2]["params"]["serviceTierForTurn"] == "default"
    context_events = [e["type"] for e in emitted if e["type"].startswith("factory.context")]
    assert context_events == [
        "factory.context",
        "factory.context.invalidated",
        "factory.context.invalidated",
    ]
    usage = [e for e in emitted if e["type"] == "factory.usage"][-1]
    assert usage["thread_total"]["inputTokens"] == 910
    assert usage["complete"] is (status != "disconnected")
    assert not usage["pricing_complete"]
    assert emitted[-1]["type"] == (
        "turn.completed" if status == "completed" else "factory.failed_usage"
    )


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
