"""Host lifecycle handoff to layer A; no note, retrieval or distillation policy lives here."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from factory.agent import stream

if TYPE_CHECKING:
    from factory.steps import Context


def collect(ctx: Context, events: Path) -> None:
    """Dispatch nonblocking host distillation of the attempt's stream.

    The session id in the stream's first event is what lets layer A keep one note per
    session. A `--resume` stream holds only the session's new turns (measured), so it is
    handed over as partial evidence and cannot replace the note a full stream wrote.
    """
    resumed = any(
        (invocation.get("metadata") or {}).get("resume") is True
        for invocation in ctx.store.runtime.invocations(ctx.run.id)
        if (invocation.get("metadata") or {}).get("events") == str(events)
    )
    schedule(ctx.project.path, ctx.registry.vault.path, events, resumed=resumed)


def collect_invocation(ctx: Context, invocation: dict) -> None:
    """Some paid probes have no retained conversation; do not break their recovery."""
    events = invocation.get("metadata", {}).get("events")
    if not isinstance(events, str) or not events:
        ctx.log("learning.unavailable", level="warning", reason="invocation-has-no-events")
        return
    collect(ctx, Path(events))


def schedule(project: Path, vault: Path, events: Path, *, resumed: bool = False) -> None:
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
                "resumed" if resumed else "fresh",
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
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Evidence:
    text: str
    sha256: str
    session: str | None
    #: The stream holds its `result` event. Layer A keeps an existing note when it does
    #: not, so a run killed mid-stream cannot overwrite what a finished one wrote.
    ended: bool


def evidence(events: Path) -> Evidence:
    """The complete event lines of the stream. A killed or still-running writer leaves a
    torn last line; it is dropped, not interpreted."""
    lines = []
    for line in events.read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            lines.append(line)
    text = "\n".join(lines) + "\n"
    try:
        ended = any(isinstance(event, stream.Result) for event in stream.events(lines))
    except stream.CorruptStream:
        ended = False
    return Evidence(
        text, hashlib.sha256(text.encode()).hexdigest(), stream.session_id(events), ended
    )
