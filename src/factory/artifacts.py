"""Attempt directories, manifests, the secret scanner and the structured log — §14, §18.

The scanner and the log live together on purpose: §18.1's rule is that the same scan
that guards an artifact guards every log line, and a scanner with two call sites in two
modules is a scanner that will one day guard only one of them.

A hit **fails the run and quarantines the artifact** rather than redacting it. A secret
that reached a transcript is compromised, and rotation is James's decision — redacting
would hide the one fact he needs.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AttemptDir",
    "SecretFound",
    "log_event",
    "scan_for_secrets",
    "write_manifest",
]

#: The patterns every artifact and every log line is checked against. The first two
#: come from each repo's `hooks.secretVars`; the rest are the shapes a leaked value
#: actually takes.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github-token", re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{16,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("linear-key", re.compile(r"\blin_api_[A-Za-z0-9]{16,}")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


class SecretFound(Exception):
    """A secret reached an artifact or a log line. The run fails; nothing is redacted."""

    def __init__(self, kind: str, where: str) -> None:
        super().__init__(
            f"{kind} found in {where}. The artifact is quarantined and the run failed. "
            "The value is compromised and must be rotated — that is James's call, and "
            "this message deliberately names nothing else."
        )
        self.kind = kind
        self.where = where


def scan_for_secrets(text: str, where: str, *, extra_names: Sequence[str] = ()) -> None:
    """Raise on the first hit. `extra_names` are the repo's own `hooks.secretVars`.

    A bare variable *name* is not a secret, so the name check looks for an assignment
    shape — `LINEAR_API_KEY=lin_api_…` — rather than the mention that appears in every
    config file this system ships.
    """
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise SecretFound(kind, where)
    for name in extra_names:
        if re.search(rf"\b{re.escape(name)}\s*[=:]\s*['\"]?\S{{8,}}", text):
            raise SecretFound(f"assignment to {name}", where)


def scan_file(path: Path, *, extra_names: Sequence[str] = ()) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    scan_for_secrets(text, str(path), extra_names=extra_names)


@dataclass(frozen=True)
class AttemptDir:
    """`<factory_dir>/run/<attempt>` — where the sandbox and the host meet.

    Both sides address it by the same absolute string, because `sbx` bind-mounts the
    host path at the identical path inside the VM. That property is what lets the
    control plane die at any moment: the filesystem is the protocol.
    """

    root: Path

    @classmethod
    def create(cls, factory_dir: Path, attempt: int) -> AttemptDir:
        """`<factory_dir>/run/<attempt>`, where `factory_dir` is `Context.factory_dir`.

        The argument is the `.factory` directory and not the worktree, because those are
        the same place only for a bind-mounted project. A `--clone` project's worktree
        lives inside the VM and its untracked content never reaches the host, so its
        `.factory` sits on a separate `rw` mount — and the host still addresses it by the
        identical string the VM does, which is the property the protocol actually needs.
        """
        root = factory_dir / "run" / str(attempt)
        root.mkdir(parents=True, exist_ok=True)
        cls(root)._clear_terminal_markers()
        return cls(root)

    def _clear_terminal_markers(self) -> None:
        """Remove any previous invocation's terminal evidence from this directory.

        The filesystem is the protocol, and every reader here treats `exit` as
        authoritative: `implement._await_exit` returns the moment it appears and
        `sbx.poll` calls it terminal regardless of what the VM is doing. A stale one is
        therefore not clutter — it is a false answer to the only question the protocol
        asks, and the reader has no way to tell.

        Belt to `Context.factory_dir`'s braces. That path now carries the run id so two
        runs cannot land here at all; this makes the directory safe even when they do,
        because a guarantee that depends on a path being constructed correctly somewhere
        else is a guarantee with a seam in it.

        Only the bare names are cleared. `plan.py` writes `plan-exit` and friends into
        this same directory for the planning half of a rewind, and those belong to an
        invocation that already finished.
        """
        for name in ("exit", "last-message.json", "events.jsonl", "stderr.log", "heartbeat"):
            (self.root / name).unlink(missing_ok=True)

    def clear_liveness(self) -> None:
        """Remove only the run-liveness markers (`exit`, `heartbeat`), leaving evidence.

        A verify or review run reuses the implement attempt's directory — it reads the
        implementer's `last-message.json` and the deliver step archives the whole dir —
        so it cannot go through `_clear_terminal_markers`, which would delete that
        evidence. It does need a fresh `exit`/`heartbeat` of its own, because the
        implementer left a (stale, terminal) `exit` there and `poll` treats `exit` as
        authoritative regardless of what produced it. The sbx holder files
        (`sbx-exec.pid`, `sbx-exec.stderr`) are overwritten by `exec_detached`, so they
        need no explicit clear here.
        """
        for name in ("exit", "heartbeat"):
            (self.root / name).unlink(missing_ok=True)

    @property
    def request(self) -> Path:
        return self.root / "request.json"

    @property
    def prompt(self) -> Path:
        return self.root / "prompt.md"

    @property
    def schema(self) -> Path:
        return self.root / "schema.json"

    @property
    def events(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def stderr(self) -> Path:
        """Captured separately from `events.jsonl`, and that is not a detail.

        P0-7 measured two things at once: a hook denial never reaches the JSON stream
        (it is a stderr line from `codex_core::tools::router`), and folding stderr into
        the same file with `2>&1` would put non-JSON lines into a JSONL parser. So the
        streams are split: one is evidence, the other is data.
        """
        return self.root / "stderr.log"

    @property
    def last_message(self) -> Path:
        return self.root / "last-message.json"

    @property
    def heartbeat(self) -> Path:
        return self.root / "heartbeat"

    @property
    def exit_file(self) -> Path:
        return self.root / "exit"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    def path(self, name: str) -> Path:
        return self.root / name

    def exit_code(self) -> int | None:
        """None until the wrapper's atomic `mv` lands the file."""
        try:
            return int(self.exit_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def heartbeat_age(self, *, now: float | None = None) -> float | None:
        try:
            beat = int(self.heartbeat.read_text().strip())
        except (OSError, ValueError):
            return None
        return (now if now is not None else time.time()) - beat


def write_manifest(directory: Path, *, produced_by: str) -> dict[str, object]:
    """sha256, size and producing state for every file. §14.1.

    This is what makes an attempt directory auditable without re-running anything: the
    evidence names itself, so a file that changed after the fact is visible.
    """
    files: dict[str, dict[str, object]] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        data = path.read_bytes()
        files[str(path.relative_to(directory))] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "producedBy": produced_by,
        "writtenAt": int(time.time()),
        "files": files,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def scan_directory(directory: Path, *, extra_names: Sequence[str] = ()) -> None:
    """Every artifact, before it leaves the worktree. F18."""
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            scan_file(path, extra_names=extra_names)


def archive(attempt_dir: Path, destination: Path, *, extra_names: Sequence[str] = ()) -> Path:
    """Copy an attempt directory out to `artifacts/`, scanning first."""
    scan_directory(attempt_dir, extra_names=extra_names)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(attempt_dir, destination)
    return destination


def log_event(
    log_dir: Path,
    *,
    level: str,
    event: str,
    run_id: str | None = None,
    ticket: str | None = None,
    state: str | None = None,
    step: str | None = None,
    attempt: int | None = None,
    detail: Mapping[str, object] | None = None,
    duration_ms: int | None = None,
) -> None:
    """One JSON object per line in `logs/factory-<date>.jsonl` — §18.1.

    Scanned before it is written, and a hit **raises** rather than redacting, for the
    same reason the artifact scan does.
    """
    record: dict[str, object] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "event": event,
    }
    for key, value in (
        ("run_id", run_id),
        ("ticket", ticket),
        ("state", state),
        ("step", step),
        ("attempt", attempt),
        ("duration_ms", duration_ms),
    ):
        if value is not None:
            record[key] = value
    if detail:
        record["detail"] = dict(detail)

    line = json.dumps(record, default=str)
    scan_for_secrets(line, "a control-plane log line")
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / f"factory-{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def tail_lines(path: Path, count: int) -> str:
    """The last `count` lines — what a failure comment carries (§13.1)."""
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:])
    except OSError:
        return ""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: Mapping[str, object] | Iterable[object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
