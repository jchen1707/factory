"""Staged stdlib-only stdio client. Runs inside the existing detached sandbox envelope."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


WIRE_COUNTS = {
    "inputTokens": "input_tokens",
    "cachedInputTokens": "cached_input_tokens",
    "cacheWriteInputTokens": "cache_write_input_tokens",
    "outputTokens": "output_tokens",
    "reasoningOutputTokens": "reasoning_output_tokens",
}


def usage_delta(total: dict[str, int], baseline: dict[str, int]) -> dict[str, int] | None:
    result = {}
    for wire, key in WIRE_COUNTS.items():
        count, before = total.get(wire, 0), baseline.get(wire, 0)
        if type(count) is not int or type(before) is not int or count < before or before < 0:
            return None
        result[key] = count - before
    return result


def run(request: dict[str, Any]) -> int:
    probe_models = request.get("probe_models") is True
    argv = ["codex", "app-server", "--stdio"]
    if not probe_models:
        argv.insert(1, "--dangerously-bypass-hook-trust")
        argv += [
            "-c",
            f"shell_environment_policy.set.OBSIDIAN_VAULT_DIRECTORY={json.dumps(request['vault'])}",
        ]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)  # noqa: S603
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("app-server pipes unavailable")
    stdin, stdout = process.stdin, process.stdout
    serial = 0
    thread = ""
    model = request.get("model", "")
    total: dict[str, int] = {}
    baseline: dict[str, int] | None = request.get("usage_baseline")
    if not request.get("resume_session"):
        baseline = {}
    pending: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    previous: dict[str, int] | None = baseline
    context_fraction: float | None = None
    pricing_complete = True
    usage_consistent = True
    turn_finished = False
    outcome = "failed"
    final = ""

    def send(method: str, params: dict[str, Any], *, notify: bool = False) -> int:
        nonlocal serial
        serial += 1
        message: dict[str, Any] = {"method": method, "params": params}
        if not notify:
            message["id"] = serial
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()
        return serial

    def receive() -> dict[str, Any]:
        line = stdout.readline()
        if not line:
            raise RuntimeError("app-server disconnected")
        event: dict[str, Any] = json.loads(line)
        emit({"type": "factory.runtime", "observed_at": time.time(), "event": event})
        # An unattended adapter cannot answer an approval or product decision.
        if "method" in event and "id" in event:
            raise RuntimeError(f"app-server requires operator input: {event['method']}")
        return event

    def rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
        number = send(method, params)
        while True:
            event = receive()
            if event.get("id") == number:
                if "error" in event:
                    raise RuntimeError(str(event["error"]))
                return dict(event.get("result", {}))
            pending.append(event)

    def publish_usage(*, final: bool = False) -> dict[str, int] | None:
        delta = usage_delta(total, baseline) if total and baseline is not None else None
        complete = final and turn_finished and usage_consistent and delta is not None
        reason = (
            "usage counter regressed or was invalid"
            if not usage_consistent
            else "resume baseline or usage unavailable"
            if delta is None
            else "invocation completion unavailable"
            if not complete
            else None
        )
        emit(
            {
                "type": "factory.usage",
                "thread_id": thread,
                "model": model,
                "observed_at": time.time(),
                "thread_total": total,
                "usage": delta,
                "complete": complete,
                "requests": requests,
                "pricing_complete": pricing_complete and complete,
                "reason": reason,
            }
        )
        return delta

    try:
        rpc(
            "initialize",
            {
                "clientInfo": {"name": "factory", "version": "1"},
                "capabilities": {"experimentalApi": False},
            },
        )
        send("initialized", {}, notify=True)
        cursor = None
        models = []
        cursors: set[str] = set()
        while True:
            catalogue = rpc("model/list", {"cursor": cursor, "includeHidden": True})
            models.extend(catalogue["data"])
            cursor = catalogue.get("nextCursor")
            if not cursor:
                break
            if cursor in cursors:
                raise RuntimeError("model catalogue pagination repeated a cursor")
            cursors.add(cursor)
        if probe_models:
            # model/list is metadata. Return before thread creation so probing a
            # legacy exec preset cannot start a model, run tools or load a project.
            emit({"type": "factory.models", "models": models})
            return 0
        facts = next((m for m in models if m["model"] == model), None)
        if facts is None or request["effort"] not in [
            r["reasoningEffort"] for r in facts["supportedReasoningEfforts"]
        ]:
            raise RuntimeError("model or reasoning effort unavailable in executing runtime")
        params = {
            "model": model,
            "cwd": request["workdir"],
            "approvalPolicy": "never",
            "sandbox": "readOnly" if request.get("readonly") else "dangerFullAccess",
        }
        if request.get("resume_session"):
            params["threadId"] = request["resume_session"]
            started = rpc("thread/resume", params)
        else:
            started = rpc("thread/start", params)
        thread = started["thread"]["id"]
        emit({"type": "thread.started", "thread_id": thread})
        # A resume may publish pre-turn totals. If it does not, retain an unknown
        # baseline; treating an absent baseline as zero would bill history twice.
        for event in pending:
            params = event.get("params", {})
            if (
                event.get("method") == "thread/tokenUsage/updated"
                and params.get("threadId") == thread
            ):
                observed = params["tokenUsage"]["total"]
                if usage_delta(observed, baseline or {}) is None:
                    usage_consistent = False
                    pricing_complete = False
                else:
                    baseline = dict(observed)
                    previous = baseline
        pending.clear()
        schema = json.loads(Path(request["schema"]).read_text())
        started_turn = rpc(
            "turn/start",
            {
                "threadId": thread,
                "model": model,
                "effort": request["effort"],
                "serviceTierForTurn": "default",
                "input": [{"type": "text", "text": Path(request["prompt"]).read_text()}],
                "outputSchema": schema,
            },
        )
        turn_id = started_turn["turn"]["id"]
        compacting = False
        while True:
            event = pending.pop(0) if pending else receive()
            params = event.get("params", {})
            method = event.get("method")
            if params.get("threadId") != thread:
                continue
            if method == "thread/tokenUsage/updated":
                usage = params["tokenUsage"]
                if params.get("turnId") != turn_id and not compacting:
                    continue
                observed = usage["total"]
                delta = usage_delta(observed, previous or {})
                if delta is None:
                    # Keep the accepted high-water counters. Lowering them would
                    # price a repeated notification again when the stream catches up.
                    usage_consistent = False
                    pricing_complete = False
                    context_fraction = None
                    emit(
                        {"type": "factory.context.invalidated", "reason": "usage counter regressed"}
                    )
                    publish_usage()
                    continue
                total = dict(observed)
                if previous is not None:
                    # A notification may be repeated, or combine multiple requests.
                    # Price only a delta that the captured latest request substantiates.
                    if any(delta.values()):
                        last = usage_delta(usage["last"], {})
                        if delta != last:
                            pricing_complete = False
                        else:
                            requests.append(
                                {
                                    "model": model,
                                    "usage": delta,
                                    "observed_at": time.time(),
                                    "service_tier": "standard",
                                    "long_context": (delta["input_tokens"] > 272000)
                                    if model in {"gpt-6-astra", "gpt-5.6-sol"}
                                    else None,
                                }
                            )
                else:
                    pricing_complete = False
                previous = dict(total)
                # Flush each observation before reading more runtime events. A
                # killed worker cannot run finally, but its retained lower bound
                # and resume baseline must still survive in the event stream.
                publish_usage()
                window = usage.get("modelContextWindow")
                context_fraction = (
                    usage["last"]["totalTokens"] / window
                    if request["context_semantics_verified"] and window
                    else None
                )
                emit(
                    {
                        "type": "factory.context",
                        "thread_id": thread,
                        "model": model,
                        "tokens": usage["last"]["totalTokens"],
                        "window": usage.get("modelContextWindow"),
                        "observed_at": time.time(),
                        "semantics_verified": request["context_semantics_verified"],
                    }
                )
            elif method == "model/rerouted":
                model = params["toModel"]
                pricing_complete = False
                context_fraction = None
                emit({"type": "factory.context.invalidated", "reason": "model changed"})
            elif method == "item/completed":
                item = params["item"]
                if item["type"] == "contextCompaction":
                    emit({"type": "factory.context.invalidated", "reason": "compaction completed"})
                    context_fraction = None
                    if compacting:
                        break
                elif item["type"] == "agentMessage":
                    if item.get("phase") in (None, "final_answer"):
                        final = item["text"]
                    emit(
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": item["text"]},
                        }
                    )
            elif method == "turn/completed":
                if params["turn"]["id"] != turn_id:
                    continue
                turn_finished = True
                if params["turn"]["status"] != "completed":
                    raise RuntimeError(f"turn ended: {params['turn']['status']}")
                Path(request["output"]).write_text(final)
                outcome = "completed"
                if context_fraction is not None and context_fraction >= 0.8 and not compacting:
                    # A completed turn is the safe boundary. Earlier runtime automatic
                    # compaction invalidates the reading and suppresses this request.
                    compacting = True
                    # Compaction can consume usage not attributed to this turn. Until
                    # runtime evidence covers that attribution, its cost is incomplete.
                    pricing_complete = False
                    rpc("thread/compact/start", {"threadId": thread})
                    continue
                break
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        emit(
            {
                "type": "factory.model_probe.failed" if probe_models else "turn.failed",
                "error": {"message": str(exc)},
            }
        )
        return 1
    finally:
        if not probe_models:
            delta = publish_usage(final=True)
            emit(
                {
                    "type": "turn.completed" if outcome == "completed" else "factory.failed_usage",
                    "usage": delta or {},
                }
            )
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
