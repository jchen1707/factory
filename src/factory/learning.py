"""Host lifecycle handoff to layer A; no note, retrieval or distillation policy lives here."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.sandbox.native_transcript import TranscriptSandbox
    from factory.steps import Context


def collect(ctx: Context, events: Path, *, invocation_id: str | None = None) -> None:
    """Retain native evidence before dispatching nonblocking host distillation.

    Each changed snapshot can be retried by recollection. The worker serializes repeats
    and leaves a receipt; the stable runtime session id lets layer A preserve note identity.
    Native export is bounded; unavailable export retains the partial event fallback.
    """
    from factory.agent_launches import AgentLaunches

    invocations = ctx.store.runtime.invocations(ctx.run.id)
    for invocation in invocations:
        if invocation["id"] == invocation_id or (
            invocation_id is None and invocation.get("metadata", {}).get("events") == str(events)
        ):
            try:
                handle = AgentLaunches(ctx.store, ctx.sandbox).handle(invocation["id"])
            except (ValueError, KeyError):
                continue
            retain(
                ctx.sandbox,
                handle.sandbox,
                ctx.env,
                events,
                extra_names=ctx.harness.secret_vars if ctx.harness else (),
            )
            break
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


def evidence(events: Path, *, extra_names: Sequence[str] = ()) -> tuple[str, str, str | None]:
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
    native = events.with_suffix(".native.jsonl")
    if session and native.is_file():
        native_receipt = events.with_suffix(".native.json")
        if native_receipt.is_file():
            status = json.loads(native_receipt.read_text())
            if status.get("outcome") == "quarantined:secret":
                from factory.artifacts import SecretFound

                raise SecretFound(str(status.get("kind", "secret")), str(native))
        retained = native.read_text(errors="replace")
        if native_session(retained) == session:
            from factory.artifacts import scan_for_secrets

            scan_for_secrets(retained, str(native), extra_names=extra_names)
            lines = retained.splitlines()
            if native_prefix(retained):
                lines = lines[:-1]
    text = "\n".join(lines) + "\n"
    return text, hashlib.sha256(text.encode()).hexdigest(), session


def native_session(text: str) -> str | None:
    """Verify native evidence belongs to the event stream, never a neighboring session."""
    try:
        first = json.loads(text.splitlines()[0])
        if first.get("type") == "session_meta":
            session = first.get("payload", {}).get("id")
            if isinstance(session, str):
                return session
    except (ValueError, IndexError, AttributeError):
        pass
    return None


def retain(
    sandbox: TranscriptSandbox,
    name: str,
    env: Mapping[str, str],
    events: Path,
    *,
    extra_names: Sequence[str] = (),
) -> None:
    """Export while the owned sandbox exists, including attempts with no exit marker."""
    from factory.artifacts import SecretFound, scan_for_secrets
    from factory.machine import Blocked
    from factory.sandbox.native_transcript import capture

    receipt = events.with_suffix(".native.json")
    try:
        _, _, session = evidence(events)
        if not session:
            _write(receipt, {"outcome": "unavailable:session-id"})
            return
        text = capture(sandbox, name, session, env=env)
        if native_session(text) != session:
            raise ValueError("native session mismatch")
        target = events.with_suffix(".native.jsonl")
        _write_text(target, text)
        scan_for_secrets(text, str(target), extra_names=extra_names)
        _write(
            receipt,
            {
                "outcome": "retained",
                "session_id": session,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "truncated_final_record": native_prefix(text),
            },
        )
    except SecretFound as exc:
        _write(receipt, {"outcome": "quarantined:secret", "kind": exc.kind})
        raise Blocked("secret-in-artifact", str(exc)) from exc
    except (OSError, ValueError, subprocess.TimeoutExpired):
        _write(receipt, {"outcome": "unavailable:native-transcript"})


def native_prefix(text: str) -> bool:
    """An incomplete final write is retained as evidence, not interpreted as a record."""
    try:
        json.loads(text.splitlines()[-1])
    except (ValueError, IndexError):
        return True
    return False
