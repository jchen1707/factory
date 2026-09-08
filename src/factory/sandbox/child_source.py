"""Bounded clone snapshot transfer; never fetch into or reset the parent's checkout."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from factory.sandbox.base import Completed
from factory.sandbox.source_observer import LIMIT, validate_links


class SourceSandbox(Protocol):
    def exec_sync(self, name: str, argv: list[str], *, timeout: int | None = None) -> Completed: ...


def capture(sandbox: SourceSandbox, name: str, source: Path) -> dict[str, Any]:
    result = sandbox.exec_sync(
        name,
        [
            "/usr/bin/python3",
            "-I",
            "-S",
            "-c",
            Path(__file__).with_name("source_observer.py").read_text(),
            str(source),
        ],
        timeout=300,
    )
    if not result.ok:
        raise ValueError("clone source export refused; source may be changing or unsupported")
    if len(result.stdout.encode()) > LIMIT:
        raise ValueError("source export exceeds limit")
    payload = json.loads(result.stdout)
    validate(payload)
    return payload


def validate(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or set(payload) != {"version", "source", "digest", "bundle"}:
        raise ValueError("invalid source export")
    if payload["version"] != 1 or not isinstance(payload["source"], dict):
        raise ValueError("invalid source export version")
    source = payload["source"]
    if set(source) != {"head", "files", "staged"} or not re.fullmatch(
        "(?:[0-9a-f]{40}|[0-9a-f]{64})", source["head"]
    ):
        raise ValueError("invalid source identity")
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(encoded.encode()).hexdigest() != payload["digest"]:
        raise ValueError("source export digest mismatch")
    if len(json.dumps(payload).encode()) > LIMIT:
        raise ValueError("source export exceeds limit")
    if not isinstance(source["files"], list) or len(source["files"]) > 100000:
        raise ValueError("invalid source inventory")
    names: set[str] = set()
    for entry in source["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "mode", "data"}:
            raise ValueError("invalid source entry")
        name = entry["path"]
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise ValueError("unsafe source path")
        path = PurePosixPath(name)
        if path.is_absolute() or any(p in {"", ".", "..", ".git"} for p in name.split("/")):
            raise ValueError("unsafe source path")
        if name in names or any(parent.as_posix() in names for parent in path.parents):
            raise ValueError("overlapping source paths")
        names.add(name)
        if entry["mode"] is None:
            if entry["data"] is not None:
                raise ValueError("invalid deleted source")
        elif type(entry["mode"]) is not int or entry["mode"] not in {0o644, 0o755, 0o120000}:
            raise ValueError("unsupported source mode")
        else:
            base64.b64decode(entry["data"], validate=True)
    if any(parent.as_posix() in names for name in names for parent in PurePosixPath(name).parents):
        raise ValueError("overlapping source paths")
    validate_links(source["files"])
    base64.b64decode(source["staged"], validate=True)
    base64.b64decode(payload["bundle"], validate=True)


def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        return subprocess.run(  # noqa: S603 — only fixed local Git operations in a fresh private root
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
            input=data,
            capture_output=True,
            check=True,
            timeout=120,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1"},
        ).stdout
    except subprocess.SubprocessError as exc:
        raise ValueError("source Git import refused") from exc


def materialize(payload: dict[str, Any], destination: Path) -> None:
    """Create once in an owned fresh directory. Partial failures are retained, never erased."""
    validate(payload)
    destination.mkdir()  # no reuse, symlink following or destructive cleanup
    _git(destination, "init", "--template=")
    bundle = destination / ".git/factory-source.bundle"
    bundle.write_bytes(base64.b64decode(payload["bundle"], validate=True))
    _git(destination, "fetch", "--no-tags", str(bundle), "HEAD")
    head = payload["source"]["head"]
    if _git(destination, "rev-parse", "FETCH_HEAD").decode().strip() != head:
        raise ValueError("source bundle does not match exported commit")
    _git(destination, "update-ref", "HEAD", head)
    _git(destination, "read-tree", head)
    staged = base64.b64decode(payload["source"]["staged"], validate=True)
    if staged:
        _git(destination, "apply", "--cached", "--binary", "-", data=staged)
    # Real files precede links; no later write can traverse a just-created alias.
    for entry in sorted(payload["source"]["files"], key=lambda entry: entry["mode"] == 0o120000):
        if entry["mode"] is None:
            continue
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry["mode"] == 0o120000:
            target.symlink_to(base64.b64decode(entry["data"], validate=True).decode("utf-8"))
            continue
        with target.open("xb") as stream:
            stream.write(base64.b64decode(entry["data"], validate=True))
        target.chmod(entry["mode"])


def tree_digest(root: Path) -> str:
    """Bind regular files and contained relative link bytes without expanding aliases."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("snapshot directory changed")
    digest = hashlib.sha256()
    inventory = []
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            inventory.append(
                {"path": name, "mode": 0o120000, "data": base64.b64encode(target.encode()).decode()}
            )
            entry = [name, "symlink", target]
        elif path.is_dir():
            entry = [name, "directory"]
        elif path.is_file():
            content = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(block)
            inventory.append({"path": name, "mode": 0o644, "data": ""})
            entry = [name, str(path.stat().st_mode & 0o777), content.hexdigest()]
        else:
            raise ValueError("snapshot contains special file")
        digest.update(json.dumps(entry, separators=(",", ":")).encode() + b"\n")
    validate_links(inventory)
    return digest.hexdigest()
