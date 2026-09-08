"""Zero-turn app-server initialization, composed with the trusted sealed launcher."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import select
import stat
import subprocess
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any


def configuration(path: Path) -> tuple[str, dict[str, Any]]:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("runtime configuration is aliased")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return hashlib.sha256(b"").hexdigest(), {}
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("runtime configuration is not a file")
        raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("runtime configuration exceeds preparation limit")
    return hashlib.sha256(raw).hexdigest(), tomllib.loads(raw.decode())


def prepare_runtime(
    request: dict[str, Any], launch: Callable[..., subprocess.Popen[str]]
) -> dict[str, Any]:
    """Only initialize/thread-start. Never send a turn or launch a model tool."""
    home = Path.home().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
    if (
        os.geteuid() == 0
        or not str(home).startswith("/home/")
        or codex_home.resolve() != codex_home
        or not codex_home.is_relative_to(home)
    ):
        raise ValueError("runtime preparation requires a private VM Codex home")
    path = codex_home / "config.toml"
    before, original = configuration(path)
    expected = copy.deepcopy(original)
    project = expected.setdefault("projects", {}).setdefault(request["workdir"], {})
    if project.get("trust_level") not in {None, "trusted"}:
        raise ValueError("runtime preparation cannot override explicit project distrust")
    project["trust_level"] = "trusted"
    process = launch(["codex", "app-server", "--stdio"], request, capture_stderr=True)
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("runtime preparation pipes unavailable")
    buffer = b""
    deadline = time.monotonic() + 45

    def rpc(serial: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal buffer
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("runtime preparation pipes unavailable")
        process.stdin.write(json.dumps({"id": serial, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError("runtime preparation timed out")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                event = json.loads(line)
                if event.get("id") == serial:
                    if "error" in event:
                        raise RuntimeError("runtime preparation request refused")
                    return dict(event["result"])
            readable, _, _ = select.select(
                [process.stdout, process.stderr], [], [], max(0, deadline - time.monotonic())
            )
            for stream in readable:
                chunk = os.read(stream.fileno(), 65536)
                if stream is process.stderr:
                    # Diagnostics can contain account/configuration details; never transport them.
                    continue
                if not chunk:
                    raise RuntimeError("runtime preparation server disconnected")
                buffer += chunk
                if len(buffer) > 1024 * 1024:
                    raise RuntimeError("runtime preparation response exceeds limit")

    try:
        rpc(1, "initialize", {"clientInfo": {"name": "factory-preparation", "version": "1"}})
        process.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        process.stdin.flush()
        response = rpc(
            2,
            "thread/start",
            {
                "model": request["model"],
                "cwd": request["workdir"],
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "config": {"agents.enabled": False},
            },
        )
        thread = response["thread"]["id"]
        if not isinstance(thread, str) or not thread or len(thread) > 128:
            raise RuntimeError("runtime preparation thread identity unavailable")
        after, observed = configuration(path)
        if observed not in (original, expected):
            raise RuntimeError("runtime preparation changed unexpected configuration")
        return {
            "thread_id": thread,
            "configuration_before": before,
            "configuration_after": after,
            "model_turns": 0,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
