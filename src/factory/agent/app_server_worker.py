"""Staged stdlib-only stdio client. Runs inside the existing detached sandbox envelope."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


def discovered_hooks(report: dict[str, Any], workdir: str) -> list[dict[str, Any]]:
    entries = report.get("data", [])
    if len(entries) != 1 or Path(entries[0].get("cwd", "")).resolve() != Path(workdir).resolve():
        raise RuntimeError("hook discovery did not return the requested working directory")
    entry = entries[0]
    if entry.get("errors") or entry.get("warnings"):
        raise RuntimeError("hook discovery reported errors or warnings")
    return entry.get("hooks", [])


def project_hook_coverage(
    discovered: list[dict[str, Any]],
    definitions: dict[str, Any],
    workdir: str,
    *,
    inline: bool = False,
) -> bool:
    """Require every repository handler, not merely any hook from its source."""
    source = Path(workdir).resolve() / ".codex/hooks.json"
    candidates = [
        hook
        for hook in discovered
        if (
            hook.get("source") == "sessionFlags"
            if inline
            else hook.get("source") == "project"
            and Path(hook.get("sourcePath") or "").resolve() == source
        )
    ]
    if not candidates and not inline:
        return False
    expected_count = 0
    for event, groups in definitions.items():
        for group in groups:
            for handler in group["hooks"]:
                expected_count += 1
                expected = {
                    "eventName": event[0].lower() + event[1:],
                    "handlerType": handler["type"],
                    "command": handler["command"],
                    "matcher": group.get("matcher"),
                    "async": handler.get("async", False),
                }
                for name, wire in (
                    ("timeout", "timeoutSec"),
                    ("statusMessage", "statusMessage"),
                    ("additionalContextLimit", "additionalContextLimit"),
                ):
                    if name in handler:
                        expected[wire] = handler[name]
                match = next(
                    (
                        i
                        for i, hook in enumerate(candidates)
                        if hook.get("enabled") is True
                        # 0.146's HookMetadata omits async entirely. Only the
                        # synchronous default is compatible with that older shape.
                        and all(
                            hook.get(k, False if k == "async" else None) == v
                            for k, v in expected.items()
                        )
                    ),
                    None,
                )
                if match is None:
                    raise RuntimeError("project hook discovery is incomplete or disabled")
                candidates.pop(match)
    if not expected_count:
        raise RuntimeError("project hook definitions contain no handlers")
    return True


def hook_overrides(report: dict[str, Any], workdir: str) -> dict[str, Any]:
    """Trust discovered enabled hooks only for this thread, never in the user store.

    App-server 0.146/0.149 accepts the CLI hook-trust flag but does not forward it
    to thread configuration. Exact runtime hashes restore that invocation-local
    behavior. Host preflight still owns vetting the project's hook sources.
    """
    states = {}
    for hook in discovered_hooks(report, workdir):
        if hook.get("enabled") is not True:
            continue
        key, digest = hook.get("key"), hook.get("currentHash")
        if (
            not isinstance(key, str)
            or not key
            or key in states
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or any(c not in "0123456789abcdef" for c in digest[7:])
        ):
            raise RuntimeError("hook discovery returned an invalid key or hash")
        states[key] = {"trusted_hash": digest}
    if not states:
        raise RuntimeError("hook discovery found no enabled hooks")
    return {"hooks.state": states}


def toml_literal(value: Any) -> str:
    """Encode JSON hook definitions for one CLI config override."""
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml_literal(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_literal(v) for v in value) + "]"
    if isinstance(value, (str, bool, int)) or (isinstance(value, float) and math.isfinite(value)):
        return json.dumps(value)
    raise RuntimeError("unsupported value in project hook definitions")


def project_hooks(workdir: str) -> dict[str, Any]:
    root = Path(workdir).resolve()
    source = root / ".codex/hooks.json"
    if source.is_symlink() or not source.resolve().is_relative_to(root):
        raise RuntimeError("project hook definitions escape the working directory")
    hooks = json.loads(source.read_text())["hooks"]
    if not isinstance(hooks, dict) or not hooks:
        raise RuntimeError("project hook definitions are empty")
    return hooks


def close_server(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
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


def start_server(
    argv: list[str], request: dict[str, Any], *, capture_stderr: bool = False
) -> subprocess.Popen[str]:
    """Bind certified starts (including hook fallback) to the checked open ELF.

    Absent binding preserves manual legacy execution. Presence never falls back to
    PATH, even when malformed. The host still owns full attestation validation.
    """
    if "runtime_identity" not in request:
        return subprocess.Popen(  # noqa: S603
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if capture_stderr else None,
            text=True,
        )
    identity = request["runtime_identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "runtime_path",
        "runtime_sha256",
        "launcher_sha256",
        "code_host_sha256",
    }:
        raise RuntimeError("invalid certified runtime binding")
    path = identity["runtime_path"]
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise RuntimeError("invalid certified runtime binding")
    for key in ("runtime_sha256", "launcher_sha256", "code_host_sha256"):
        value = identity[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise RuntimeError("invalid certified runtime binding")
    mountpoint = native_mountpoint()
    if mountpoint.is_relative_to(Path(request["workdir"]).resolve()):
        raise RuntimeError("certified runtime mountpoint overlaps working directory")
    with (
        sealed_binary(path, identity["runtime_sha256"]) as descriptor,
        sealed_binary(
            str(Path(path).parent.parent / "codex-resources/bwrap"), identity["launcher_sha256"]
        ) as launcher,
        sealed_binary(
            str(Path(path).parent / "codex-code-mode-host"), identity["code_host_sha256"]
        ) as code_host,
    ):
        executable = str(mountpoint / "codex")
        # Execute the checked launcher snapshot too. No PATH or mutable-binary fallback.
        # A regular file on read-only tmpfs gives current_exe() a stable, read-only helper pathname.
        return subprocess.Popen(  # noqa: S603
            [
                f"/proc/self/fd/{launcher}",
                "--bind",
                "/",
                "/",
                "--dev-bind",
                "/dev",
                "/dev",
                "--tmpfs",
                str(mountpoint),
                "--perms",
                "0555",
                "--file",
                str(descriptor),
                executable,
                "--dir",
                str(mountpoint / "codex-resources"),
                "--perms",
                "0555",
                "--file",
                str(launcher),
                str(mountpoint / "codex-resources/bwrap"),
                "--perms",
                "0555",
                "--file",
                str(code_host),
                str(mountpoint / "codex-code-mode-host"),
                "--remount-ro",
                str(mountpoint),
                "--setenv",
                "PATH",
                str(mountpoint / "codex-resources") + ":" + os.environ.get("PATH", ""),
                "--",
                executable,
                *argv[1:],
            ],
            pass_fds=(descriptor, launcher, code_host),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if capture_stderr else None,
            text=True,
        )


def native_mountpoint() -> Path:
    """Use an empty image-owned anchor that an agent cannot rename or replace."""
    mountpoint = Path("/mnt")
    if os.geteuid() == 0:
        raise RuntimeError("certified runtime requires an unprivileged user")
    for part in (mountpoint, *mountpoint.parents):
        metadata = part.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError("certified runtime mountpoint is not image-owned")
    if any(mountpoint.iterdir()):
        raise RuntimeError("certified runtime mountpoint is not empty")
    return mountpoint


@contextmanager
def sealed_binary(path: str, expected: str) -> Iterator[int]:
    """Freeze checked native bytes before another process can consume them."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(4) != b"\x7fELF":
            raise RuntimeError("certified runtime must be a native ELF")
        stream.seek(0)
        # Linux memfd seals freeze the actual executable bytes, including against
        # in-place writes after validation. A pathname or open original fd cannot.
        # Host type checking runs on macOS; these APIs are required in the Linux VM.
        linux_os: Any = os
        linux_fcntl: Any = fcntl
        descriptor = linux_os.memfd_create("factory-certified-runtime", linux_os.MFD_ALLOW_SEALING)
        with os.fdopen(descriptor, "w+b") as executable:
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise RuntimeError("certified runtime exceeds size limit")
                executable.write(chunk)
                digest.update(chunk)
            executable.flush()
            fcntl.fcntl(
                descriptor,
                linux_fcntl.F_ADD_SEALS,
                linux_fcntl.F_SEAL_WRITE
                | linux_fcntl.F_SEAL_GROW
                | linux_fcntl.F_SEAL_SHRINK
                | linux_fcntl.F_SEAL_SEAL,
            )
            if digest.hexdigest() != expected:
                raise RuntimeError("certified runtime binary changed")
            executable.seek(0)
            yield descriptor


MAILBOX_LIMIT = 80 * 1024
MAILBOX_RESPONSE_LIMIT = 256 * 1024


def read_mailbox(directory: Path, name: str, *, limit: int = MAILBOX_LIMIT) -> bytes:
    """Fixed mailbox filenames under host-selected mounts; never follow candidate links."""
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("mailbox requires a regular file")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("mailbox message exceeds size limit")
            return raw
    finally:
        os.close(root)


def write_mailbox(directory: Path, name: str, raw: bytes, *, limit: int = MAILBOX_LIMIT) -> None:
    if len(raw) > limit:
        raise ValueError("mailbox message exceeds size limit")
    descriptor, temporary = tempfile.mkstemp(prefix=".mailbox-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def delegation_call(config: dict[str, Any], params: dict[str, Any], thread: str) -> dict[str, Any]:
    """Transport only. Host response is not approval to spawn from this worker."""
    if (
        not isinstance(params, dict)
        or not thread
        or params.get("threadId") != thread
        or params.get("namespace") is not None
        or params.get("tool") not in {tool["name"] for tool in config["tools"]}
        or not isinstance(params.get("callId"), str)
        or not params["callId"].strip()
        or len(params["callId"]) > 128
    ):
        raise RuntimeError("unregistered or foreign delegation call")
    raw = json.dumps(
        {"call_id": params["callId"], "tool": params["tool"], "arguments": params["arguments"]},
        allow_nan=False,
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    write_mailbox(Path(config["inbox"]), "request.json", raw)
    # The default controller polls every 60 seconds, in addition to time spent
    # reconciling other runs. Allow several polls, including a missed tick, while
    # retaining a finite failure bound when the controller is unavailable.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            response = json.loads(
                read_mailbox(Path(config["outbox"]), "response.json", limit=MAILBOX_RESPONSE_LIMIT)
            )
        except FileNotFoundError:
            response = {}
        if response.get("request_sha256") == digest:
            result = response["result"]
            if (
                type(result.get("success")) is not bool
                or not isinstance(result.get("contentItems"), list)
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"type", "text"}
                    or item["type"] != "inputText"
                    or not isinstance(item["text"], str)
                    for item in result["contentItems"]
                )
            ):
                raise RuntimeError("invalid delegation response")
            return result
        time.sleep(0.1)
    # The durable request remains available to the controller after disconnection.
    raise RuntimeError("delegation controller response unavailable; request retained")


def run(request: dict[str, Any]) -> int:
    probe_models = request.get("probe_models") is True
    argv = ["codex", "app-server", "--stdio"]
    if not probe_models:
        argv.insert(1, "--dangerously-bypass-hook-trust")
        argv += [
            "-c",
            f"shell_environment_policy.set.OBSIDIAN_VAULT_DIRECTORY={json.dumps(request['vault'])}",
        ]
    try:
        process = start_server(argv, request)
    except (OSError, ValueError, RuntimeError, AttributeError) as exc:
        emit(
            {
                "type": "factory.model_probe.failed" if probe_models else "turn.failed",
                "error": {"message": str(exc)},
            }
        )
        return 1
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("app-server pipes unavailable")
    stdin, stdout = process.stdin, process.stdout
    serial = 0
    thread = ""
    model = request.get("model", "")
    total: dict[str, int] = {}
    baseline: dict[str, int] | None = request.get("usage_baseline")
    usage_scope = request.get("usage_scope", "thread")
    if usage_scope not in {"thread", "connection"}:
        close_server(process)
        raise RuntimeError("unverified usage counter scope")
    if not request.get("resume_session") or usage_scope == "connection":
        # Only a compatibility-verified connection-local runtime may start at zero
        # on resume. Never infer a reset merely because a counter regressed.
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
        if (
            event.get("method") == "item/tool/call"
            and "id" in event
            and request.get("delegation") is not None
        ):
            result = delegation_call(request["delegation"], event["params"], thread)
            stdin.write(json.dumps({"id": event["id"], "result": result}) + "\n")
            stdin.flush()
            return {"method": "factory/tool/handled"}
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
                "usage_scope": usage_scope,
                "usage": delta,
                "complete": complete,
                "requests": requests,
                "pricing_complete": pricing_complete and complete,
                "reason": reason,
            }
        )
        return delta

    def initialize() -> None:
        rpc(
            "initialize",
            {
                "clientInfo": {"name": "factory", "version": "1"},
                "capabilities": {"experimentalApi": not probe_models},
            },
        )
        send("initialized", {}, notify=True)

    try:
        initialize()
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
        hooks = rpc("hooks/list", {"cwds": [request["workdir"]]})
        definitions = project_hooks(request["workdir"])
        if not project_hook_coverage(
            discovered_hooks(hooks, request["workdir"]), definitions, request["workdir"]
        ):
            # A secondary workspace may omit its project layer even while user
            # hooks are present. Load the repository definitions for this process.
            override = "hooks=" + toml_literal(definitions)
            close_server(process)
            argv += ["-c", override]
            process = start_server(argv, request)
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("app-server pipes unavailable")
            stdin, stdout = process.stdin, process.stdout
            pending.clear()
            initialize()
            hooks = rpc("hooks/list", {"cwds": [request["workdir"]]})
            project_hook_coverage(
                discovered_hooks(hooks, request["workdir"]),
                definitions,
                request["workdir"],
                inline=True,
            )
        params = {
            "model": model,
            "cwd": request["workdir"],
            "approvalPolicy": "never",
            "sandbox": "read-only" if request.get("readonly") else "danger-full-access",
            # Child launches have no factory admission callback. Measured on
            # 0.146.0: this thread override disables nesting on start and resume;
            # the CLI features.multi_agent=false override alone does not.
            "config": {
                **hook_overrides(hooks, request["workdir"]),
                "agents.enabled": False,
            },
        }
        if request.get("resume_session"):
            params["threadId"] = request["resume_session"]
            started = rpc("thread/resume", params)
        else:
            if request.get("delegation") is not None:
                params["dynamicTools"] = request["delegation"]["tools"]
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
        compact_turn_id: str | None = None
        while True:
            event = pending.pop(0) if pending else receive()
            params = event.get("params", {})
            method = event.get("method")
            if params.get("threadId") != thread:
                continue
            if method == "turn/started" and compacting:
                compact_turn_id = params["turn"]["id"]
            elif method == "thread/tokenUsage/updated":
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
                                    if model
                                    in {
                                        "gpt-6-astra",
                                        "gpt-5.6-sol",
                                        "gpt-5.6-terra",
                                        "gpt-5.6-luna",
                                    }
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
                if compacting:
                    # Compaction reports both billed requests and a context reset.
                    # Neither is a fresh normal-turn context measurement.
                    continue
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
                    if compacting and compact_turn_id is None:
                        compact_turn_id = params.get("turnId")
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
                expected_turn = compact_turn_id if compacting else turn_id
                if params["turn"]["id"] != expected_turn:
                    continue
                turn_finished = True
                if params["turn"]["status"] != "completed":
                    raise RuntimeError(f"turn ended: {params['turn']['status']}")
                if compacting:
                    # The item completes before the turn becomes available again.
                    # Keep the server alive through the matching terminal event.
                    outcome = "completed"
                    break
                Path(request["output"]).write_text(final)
                outcome = "completed"
                if context_fraction is not None and context_fraction >= 0.8 and not compacting:
                    # A completed turn is the safe boundary. Earlier runtime automatic
                    # compaction invalidates the reading and suppresses this request.
                    compacting = True
                    turn_finished = False
                    outcome = "failed"
                    context_fraction = None
                    emit({"type": "factory.context.invalidated", "reason": "compaction requested"})
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
        close_server(process)


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
