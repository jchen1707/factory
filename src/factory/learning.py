"""Host lifecycle handoff to layer A; no note, retrieval or distillation policy lives here."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from factory.agent import stream

if TYPE_CHECKING:
    from factory.steps import Context


def collect(ctx: Context, events: Path, *, invocation_id: str | None = None) -> None:
    """Dispatch nonblocking host distillation of the attempt's stream.

    The stream is the evidence: every line the CLI wrote, on the host, with the session
    id in its first event, so layer A can keep one note per session. Each changed
    snapshot can be retried by recollection; the worker serializes repeats and leaves a
    receipt.
    """
    schedule(ctx.project.path, ctx.registry.vault.path, events)


def collect_invocation(ctx: Context, invocation: dict) -> None:
    """Some paid probes have no retained conversation; do not break their recovery."""
    events = invocation.get("metadata", {}).get("events")
    if not isinstance(events, str) or not events:
        ctx.log("learning.unavailable", level="warning", reason="invocation-has-no-events")
        return
    collect(ctx, Path(events), invocation_id=invocation["id"])


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
    _write_text(path, json.dumps(payload) + "\n")


def _write_text(path: Path, text: str) -> None:
    """Replace the artifact itself; never follow a candidate-created output symlink."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def evidence(events: Path) -> tuple[str, str, str | None, bool]:
    """The complete event lines, their digest, the session, and whether the run ended.

    A writer that was killed, or is still running, leaves a torn last line; it is dropped
    rather than interpreted. The run "ended" when the stream holds its `result` event,
    which is what tells the distiller whether a partial note may be overwritten.
    """
    lines = []
    for line in events.read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            lines.append(line)
    text = "\n".join(lines) + "\n"
    ended = any(isinstance(event, stream.Result) for event in stream.events(lines))
    return text, hashlib.sha256(text.encode()).hexdigest(), stream.session_id(events), ended
