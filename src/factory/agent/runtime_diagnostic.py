"""Bounded, metadata-only runtime diagnosis; never logs raw provider/account data."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class DiagnosticError(Exception):
    """A classified failure safe to retain without the provider's error message."""


def _account_supported(command: Sequence[str], timeout: float) -> bool | None:
    with tempfile.TemporaryDirectory(prefix="factory-protocol-") as directory:
        result = subprocess.run(  # noqa: S603
            [*command, "app-server", "generate-json-schema", "--experimental", "--out", directory],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            return None
        try:
            schema = json.loads((Path(directory) / "ClientRequest.json").read_text())
            return any(
                "account/read" in item.get("properties", {}).get("method", {}).get("enum", [])
                for item in schema["oneOf"]
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None


def diagnose(
    command: Sequence[str] = ("codex",),
    *,
    model: str = "gpt-6-astra",
    effort: str = "high",
    experimental: bool = False,
    timeout: float = 30,
) -> dict[str, Any]:
    """Compare exact requested model/effort with runtime metadata, without inference."""
    version = subprocess.run(  # noqa: S603
        [*command, "--version"], capture_output=True, text=True, timeout=timeout, check=True
    ).stdout.strip()
    supports_account = _account_supported(command, timeout)
    process = subprocess.Popen(  # noqa: S603
        [*command, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        process.wait()
        raise DiagnosticError("runtime-pipes-unavailable")
    stdin, stdout = process.stdin, process.stdout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    serial = 0
    buffer = b""
    deadline = time.monotonic() + timeout

    def send(value: dict[str, Any]) -> None:
        stdin.write((json.dumps(value) + "\n").encode())

    def rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal serial, buffer
        serial += 1
        send({"id": serial, "method": method, "params": params})
        while time.monotonic() < deadline:
            while b"\n" not in buffer:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise DiagnosticError("runtime-timeout")
                data = os.read(stdout.fileno(), 262144)
                if not data:
                    raise DiagnosticError("runtime-disconnected")
                buffer += data
                if len(buffer) > 4 * 1024 * 1024:
                    raise DiagnosticError("runtime-response-too-large")
            line, buffer = buffer.split(b"\n", 1)
            event = json.loads(line)
            if not isinstance(event, dict):
                raise DiagnosticError("runtime-malformed-response")
            if "id" in event and "method" in event:
                raise DiagnosticError("runtime-input-required")
            if event.get("id") == serial:
                if "error" in event:
                    raise DiagnosticError("runtime-request-refused")
                result = event.get("result")
                if not isinstance(result, dict):
                    raise DiagnosticError("runtime-malformed-response")
                return result
        raise DiagnosticError("runtime-timeout")

    try:
        rpc(
            "initialize",
            {
                "clientInfo": {"name": "factory-diagnostic", "version": "1"},
                "capabilities": {"experimentalApi": experimental},
            },
        )
        send({"method": "initialized", "params": {}})
        models = []
        cursor = None
        seen = set()
        while True:
            page = rpc("model/list", {"includeHidden": True, "cursor": cursor})
            for item in page["data"]:
                name = item["model"]
                efforts = [entry["reasoningEffort"] for entry in item["supportedReasoningEfforts"]]
                if not isinstance(name, str) or not all(isinstance(e, str) for e in efforts):
                    raise DiagnosticError("runtime-malformed-catalogue")
                models.append({"model": name, "efforts": efforts})
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise DiagnosticError("runtime-pagination-invalid")
            seen.add(cursor)
        account: dict[str, Any] = {}
        account_status = "unsupported" if supports_account is False else "unverified"
        if supports_account:
            try:
                account = rpc("account/read", {"refreshToken": False})
                account_status = "observed"
            except DiagnosticError:
                account_status = "unavailable"
        identity = account.get("account") or {}
        if not isinstance(identity, dict):
            identity = {}
            account_status = "malformed"
        matches = [item for item in models if item["model"] == model]
        status = "model-not-advertised" if not matches else "effort-not-advertised"
        if len(matches) == 1 and effort in matches[0]["efforts"]:
            status = "supported"
        if len(matches) > 1:
            raise DiagnosticError("runtime-duplicate-model")
        return {
            "version": version,
            "experimental": experimental,
            "requested_model": model,
            "requested_effort": effort,
            "status": status,
            "inference_attempted": False,
            "models": models,
            "account_status": account_status,
            "account": {
                "type": identity.get("type")
                if identity.get("type") in {None, "apiKey", "chatgpt", "amazonBedrock"}
                else "unknown",
                "plan": identity.get("planType")
                if identity.get("planType")
                in {None, "free", "plus", "pro", "team", "business", "enterprise", "edu"}
                else "unknown",
                "requires_auth": account.get("requiresOpenaiAuth")
                if type(account.get("requiresOpenaiAuth")) is bool
                else None,
            },
        }
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--experimental", action="store_true")
    args = parser.parse_args()
    try:
        result = diagnose(model=args.model, effort=args.effort, experimental=args.experimental)
    except DiagnosticError as exc:
        result = {"status": str(exc), "inference_attempted": False}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        result = {"status": "runtime-diagnostic-failed", "inference_attempted": False}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "supported" else 1


if __name__ == "__main__":
    raise SystemExit(main())
