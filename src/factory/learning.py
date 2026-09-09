"""Host lifecycle handoff to layer A; no note, retrieval or distillation policy lives here."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.steps import Context


def collect(ctx: Context, events: Path) -> None:
    """Schedule retained terminal evidence without delaying a workflow transition.

    Each changed snapshot can be retried by recollection. The worker serializes repeats
    and leaves a receipt; the stable runtime session id lets layer A preserve note identity.
    Events are explicitly partial evidence, especially for app-server's reduced stream.
    """
    schedule(ctx.project.path, ctx.registry.vault.path, events)


def collect_invocation(ctx: Context, invocation: dict) -> None:
    """Some paid probes have no retained conversation; do not break their recovery."""
    events = invocation.get("metadata", {}).get("events")
    if not isinstance(events, str) or not events:
        ctx.log("learning.unavailable", level="warning", reason="invocation-has-no-events")
        return
    collect(ctx, Path(events))


def schedule(project: Path, vault: Path, events: Path) -> None:
    """Dispatch from a retained artifact, including cancellation's archived copies."""
    outcome = events.with_suffix(".learning.json")
    script = project / ".agents/vendor/harness/hooks/session_learnings.mjs"
    try:
        if not script.is_file():
            _write(outcome, {"outcome": "unavailable:script"})
            return
        if not events.is_file():
            _write(outcome, {"outcome": "unavailable:transcript"})
            return
        subprocess.Popen(  # noqa: S603 — controller-owned module and registry argv
            [
                sys.executable,
                "-m",
                "factory.learning_worker",
                str(script),
                str(events),
                str(project),
                str(vault),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        with suppress(OSError):
            _write(outcome, {"outcome": f"unavailable:{type(exc).__name__}"})


def _write(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload) + "\n")
    temporary.replace(path)


def evidence(events: Path) -> tuple[str, str, str | None]:
    """Preserve complete event lines after interruption, without interpreting lessons."""
    lines = []
    session = None
    for line in events.read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        lines.append(line)
        if value.get("type") == "thread.started" and isinstance(value.get("thread_id"), str):
            session = value["thread_id"]
    text = "\n".join(lines) + "\n"
    return text, hashlib.sha256(text.encode()).hexdigest(), session
