"""One admitted usage/compaction measurement; staged beside the trusted worker."""

from __future__ import annotations

import importlib.util
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


def helpers() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "certified_app_server_worker", Path(__file__).with_name("app_server_worker.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("trusted worker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


class Client:
    """Bounded JSON-RPC transport retaining every request and observed notification."""

    def __init__(self, process: subprocess.Popen[str], timeout: float):
        self.process = process
        self.deadline = time.monotonic() + timeout
        self.serial = 0
        self.pending: list[dict[str, Any]] = []
        self.buffer = b""
        self.selector = selectors.DefaultSelector()
        if process.stdout is None or process.stdin is None or process.stderr is None:
            raise RuntimeError("app-server pipes unavailable")
        self.selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(process.stderr, selectors.EVENT_READ, "stderr")

    def send(self, method: str, params: dict[str, Any], *, notify: bool = False) -> int:
        self.serial += 1
        event: dict[str, Any] = {"method": method, "params": params}
        if not notify:
            event["id"] = self.serial
        emit({"type": "factory.rpc", "direction": "sent", "event": event})
        if self.process.stdin is None:
            raise RuntimeError("app-server stdin unavailable")
        self.process.stdin.write(json.dumps(event) + "\n")
        self.process.stdin.flush()
        return self.serial

    def receive(self) -> dict[str, Any]:
        while b"\n" not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("certification phase timed out")
            ready = self.selector.select(remaining)
            if not ready:
                raise TimeoutError("app-server notification timed out")
            for key, _ in ready:
                chunk = os.read(key.fd, 262144)
                if key.data == "stderr":
                    if chunk:
                        sys.stderr.write(chunk.decode(errors="replace"))
                        sys.stderr.flush()
                    else:
                        self.selector.unregister(key.fileobj)
                    continue
                if not chunk:
                    raise RuntimeError("app-server disconnected")
                self.buffer += chunk
                if len(self.buffer) > 16 * 1024 * 1024:
                    raise RuntimeError("app-server event exceeds size limit")
        line, self.buffer = self.buffer.split(b"\n", 1)
        event = json.loads(line)
        if not isinstance(event, dict):
            raise RuntimeError("invalid app-server event")
        emit({"type": "factory.rpc", "direction": "received", "event": event})
        if "method" in event and "id" in event:
            raise RuntimeError("unexpected server request during certification")
        return event

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        serial = self.send(method, params)
        while True:
            event = self.receive()
            if event.get("id") == serial:
                if "error" in event:
                    raise RuntimeError("app-server RPC failed: " + json.dumps(event["error"]))
                result = event.get("result", {})
                if not isinstance(result, dict):
                    raise RuntimeError("invalid app-server RPC result")
                return result
            self.pending.append(event)

    def close(self) -> None:
        self.selector.close()
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream:
                stream.close()


def counts(value: Any) -> bool:
    return isinstance(value, dict) and all(
        type(value.get(k)) is int and value[k] >= 0
        for k in ("inputTokens", "cachedInputTokens", "outputTokens", "totalTokens")
    )


def run(request: dict[str, Any]) -> int:
    worker = helpers()
    stage = request.get("stage")
    client = None
    thread = request.get("thread_id")
    turn: str | None = None
    total: dict[str, int] = {}
    last: dict[str, int] = {}
    compacted = False
    status = "failed"
    consistent = True
    scope = request.get("usage_scope", "unknown")
    baseline = request.get("usage_baseline")
    if stage == "initial" or scope == "connection":
        baseline = {}
    try:
        if stage not in {"initial", "second", "compact", "postcompact", "modelchange", "resume"}:
            raise ValueError("invalid certification usage stage")
        if scope not in {"unknown", "thread", "connection"}:
            raise ValueError("invalid usage scope")
        if not request.get("runtime_identity"):
            raise ValueError("certification requires a native runtime identity")
        if stage != "initial" and (not isinstance(thread, str) or not thread):
            raise ValueError("resumed certification phase requires a thread")
        if stage != "compact" and not request.get("prompt"):
            raise ValueError("source-owned certification prompt required")
        timeout = request.get("timeout_seconds", 180)
        if type(timeout) not in {int, float} or not 0 < timeout <= 600:
            raise ValueError("invalid certification timeout")
        workdir = request["workdir"]
        definitions = worker.project_hooks(workdir)
        argv = [
            "codex",
            "--dangerously-bypass-hook-trust",
            "app-server",
            "--stdio",
            "-c",
            "hooks=" + worker.toml_literal(definitions),
        ]
        client = Client(worker.start_server(argv, request), timeout)
        client.rpc(
            "initialize",
            {
                "clientInfo": {"name": "factory-certification-usage", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        client.send("initialized", {}, notify=True)
        hooks = client.rpc("hooks/list", {"cwds": [workdir]})
        worker.project_hook_coverage(
            worker.discovered_hooks(hooks, workdir), definitions, workdir, inline=True
        )
        params = {
            "model": request["model"],
            "cwd": workdir,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "config": {**worker.hook_overrides(hooks, workdir), "agents.enabled": False},
        }
        if thread:
            params["threadId"] = thread
        started = client.rpc("thread/resume" if thread else "thread/start", params)
        observed_thread = started["thread"]["id"]
        if thread and thread != observed_thread:
            raise RuntimeError("runtime resumed a different thread")
        thread = observed_thread
        emit({"type": "thread.started", "thread_id": thread})
        # Pre-turn notifications are evidence, never this invocation's usage.
        client.pending.clear()
        if stage == "compact":
            client.rpc("thread/compact/start", {"threadId": thread})
        else:
            result = client.rpc(
                "turn/start",
                {
                    "threadId": thread,
                    "model": request["model"],
                    "effort": request["effort"],
                    "serviceTierForTurn": "default",
                    "input": [{"type": "text", "text": request["prompt"]}],
                },
            )
            turn = result["turn"]["id"]
        while True:
            event = client.pending.pop(0) if client.pending else client.receive()
            method, params = event.get("method"), event.get("params", {})
            if params.get("threadId") not in {None, thread}:
                raise RuntimeError("unexpected thread notification")
            if method == "thread/tokenUsage/updated":
                usage = params["tokenUsage"]
                observed = usage.get("total")
                if not counts(observed) or not counts(usage.get("last")):
                    consistent = False
                    continue
                if total and worker.usage_delta(observed, total) is None:
                    consistent = False
                total, last = observed, usage["last"]
            elif (
                method == "item/completed"
                and params.get("item", {}).get("type") == "contextCompaction"
            ):
                compacted = True
            elif method == "turn/completed":
                completed = params["turn"]
                if turn is not None and completed["id"] != turn:
                    raise RuntimeError("unexpected completed turn")
                turn, status = completed["id"], completed["status"]
                break
        if status != "completed" or (stage == "compact" and not compacted):
            raise RuntimeError("certification action did not complete")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
        status = "failed"
        emit({"type": "turn.failed", "error": str(exc)})
        return 1
    finally:
        if client:
            try:
                client.close()
            except (OSError, subprocess.TimeoutExpired) as exc:
                consistent = False
                emit({"type": "factory.certification.cleanup_failed", "error": str(exc)})
        delta = (
            worker.usage_delta(total, baseline) if total and isinstance(baseline, dict) else None
        )
        complete = status == "completed" and consistent and delta is not None
        # Compaction request attribution and unknown resumed counter scopes are unproven.
        if stage == "compact" or (stage != "initial" and scope == "unknown"):
            complete = False
        emit(
            {
                "type": "factory.usage",
                "thread_id": thread,
                "model": request.get("model"),
                "observed_at": time.time(),
                "thread_total": total,
                "usage_scope": scope,
                "usage": delta,
                "complete": complete,
                "requests": [],
                "pricing_complete": False,
                "reason": None if complete else "certification usage attribution incomplete",
            }
        )
        emit(
            {
                "type": "factory.certification.phase",
                "stage": stage,
                "thread_id": thread,
                "turn_id": turn,
                "status": status,
                "total": total,
                "last": last,
                "compaction_observed": compacted,
            }
        )


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
