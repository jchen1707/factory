"""Detached host receipt writer for the target repository's learning hook."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from factory.learning import _write, evidence


def run(script: Path, events: Path, project: Path, vault: Path) -> None:
    receipt = events.with_suffix(".learning.json")
    lock = events.with_suffix(".learning.lock")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            text, digest, session = evidence(events)
            old = json.loads(receipt.read_text()) if receipt.exists() else {}
            if (
                old.get("sha256") == digest
                and old.get("finished") is True
                and old.get("retryable") is False
            ):
                return
            record = {
                "sha256": digest,
                "evidence": "retained-events-partial",
                "session_id": session,
                "finished": False,
                "outcome": "started",
            }
            _write(receipt, record)
            if not session:
                _write(receipt, {**record, "outcome": "unavailable:session-id", "finished": True})
                return
            snapshot = events.with_suffix(".learning.jsonl")
            snapshot.write_text(text)
            payload = {
                "cwd": str(project),
                "runtime": "codex",
                "evidence": "retained-events-partial",
                "session_id": session,
                "transcript_path": str(snapshot),
            }
            proc = subprocess.run(  # noqa: S603 — registered host repository, never worktree
                ["node", str(script), "--json"],  # noqa: S607 — operator-installed runtime
                input=json.dumps(payload),
                cwd=project,
                env={**os.environ, "OBSIDIAN_VAULT_DIRECTORY": str(vault)},
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            result = json.loads(proc.stdout)
            if not isinstance(result, dict) or not isinstance(result.get("outcome"), str):
                raise ValueError("invalid layer-A outcome")
            _write(receipt, {**record, **result, "finished": True, "exit_code": proc.returncode})
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            _write(receipt, {"outcome": f"failed:{type(exc).__name__}", "finished": False})


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
