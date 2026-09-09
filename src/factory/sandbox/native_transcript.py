"""Bounded session export through the sandbox adapter; no candidate code runs on the host."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from factory.sandbox.base import Completed

LIMIT = 64 * 1024 * 1024


class TranscriptSandbox(Protocol):
    def exec_sync(
        self, name: str, argv: list[str], *, env: Mapping[str, str], timeout: int
    ) -> Completed: ...


def capture(sandbox: TranscriptSandbox, name: str, session: str, *, env: Mapping[str, str]) -> str:
    if not callable(getattr(sandbox, "exec_sync", None)):
        raise ValueError("sandbox transcript transport unavailable")
    result = sandbox.exec_sync(
        name,
        [
            "/usr/bin/python3",
            "-I",
            "-S",
            "-c",
            Path(__file__).with_name("transcript_observer.py").read_text(),
            session,
        ],
        env=env,
        timeout=60,
    )
    if not result.ok or len(result.stdout.encode()) > LIMIT * 2:
        raise ValueError("native transcript export unavailable")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get("session_id") != session:
        raise ValueError("native transcript export identity mismatch")
    text = payload.get("transcript")
    if not isinstance(text, str) or len(text.encode()) > LIMIT:
        raise ValueError("native transcript export unavailable")
    return text
