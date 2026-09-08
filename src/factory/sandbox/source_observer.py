"""Isolated source export inside a clone; no imports from candidate Python paths.

Two matching full inventories bracket Git bundle creation. Ignored writable dependencies
are omitted, while tracked files, staged changes and untracked source are preserved.
An unstable/oversized/unsupported tree refuses instead of exporting a partial snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

LIMIT = 64 * 1024 * 1024


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(  # noqa: S603 — fixed Git argv, isolated local repository only
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        check=True,
        timeout=120,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        },
    )
    if len(result.stdout) > LIMIT:
        raise ValueError("source export exceeds limit")
    return result.stdout


def file_entry(root: Path, name: str) -> dict[str, Any]:
    parts = name.split("/")
    if any(p in {"", ".", "..", ".git"} for p in parts):
        raise ValueError("unsafe source path")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            before_link = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(before_link.st_mode):
                target = os.readlink(parts[-1], dir_fd=directory)
                after_link = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                identity = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(before_link, key) != getattr(after_link, key) for key in identity):
                    raise ValueError("source link changed during export")
                return {
                    "path": name,
                    "mode": 0o120000,
                    "data": base64.b64encode(target.encode("utf-8")).decode("ascii"),
                }
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return {"path": name, "mode": None, "data": None}
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
                raise ValueError("unsupported source file")
            data = stream.read(LIMIT + 1)
            after = os.fstat(stream.fileno())
            # Reading may update atime on a fresh Linux clone; it is not a source edit.
            identity = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            if (
                any(getattr(before, key) != getattr(after, key) for key in identity)
                or len(data) > LIMIT
            ):
                raise ValueError("source changed during export")
            return {
                "path": name,
                "mode": 0o755 if before.st_mode & 0o111 else 0o644,
                "data": base64.b64encode(data).decode("ascii"),
            }
    except FileNotFoundError:
        return {"path": name, "mode": None, "data": None}
    finally:
        os.close(directory)


def inventory(root: Path) -> dict[str, Any]:
    head = git(root, "rev-parse", "HEAD").decode().strip()
    index = git(root, "ls-files", "--stage", "-z")
    if any(
        not entry.startswith((b"100644 ", b"100755 ", b"120000 ")) or b" 0\t" not in entry
        for entry in index.split(b"\0")
        if entry
    ):
        raise ValueError("unmerged or submodule source requires explicit preservation")
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    files: list[dict[str, Any]] = []
    size = 0
    for name in sorted(set(names.decode("utf-8").split("\0")) - {""}):
        entry = file_entry(root, name)
        size += len(entry["data"] or "")
        if size > LIMIT or len(files) >= 100000:
            raise ValueError("source export exceeds limit")
        files.append(entry)
    validate_links(files)
    staged = git(
        root, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"
    )
    return {"head": head, "files": files, "staged": base64.b64encode(staged).decode("ascii")}


def validate_links(files: list[dict[str, Any]]) -> None:
    """Resolve relative aliases against the exported inventory, without following the host FS."""
    present = {entry["path"]: entry for entry in files if entry["mode"] is not None}
    directories = {""} | {
        str(parent) if str(parent) != "." else ""
        for name in present
        for parent in PurePosixPath(name).parents
    }
    links: dict[str, str] = {}
    for name, entry in present.items():
        if entry["mode"] != 0o120000:
            continue
        if ".git" in PurePosixPath(name).parts:
            raise ValueError("source link in Git metadata")
        target = base64.b64decode(entry["data"], validate=True).decode("utf-8")
        if (
            not target
            or "\x00" in target
            or "\\" in target
            or target.startswith("/")
            or PurePosixPath(target).as_posix() != target
            or posixpath.normpath(target) != target
        ):
            raise ValueError("source link requires a canonical relative target")
        links[name] = target
    for name, target in links.items():
        stack = list(PurePosixPath(name).parent.parts)
        pending = target.split("/")
        visited = {name}
        while pending:
            part = pending.pop(0)
            if part == ".":
                continue
            if part == "..":
                if not stack:
                    raise ValueError("source link escapes exported root")
                stack.pop()
                continue
            if part == ".git":
                raise ValueError("source link targets Git metadata")
            stack.append(part)
            current = "/".join(stack)
            if current in links:
                if current in visited or len(visited) >= 40:
                    raise ValueError("cyclic source link")
                visited.add(current)
                stack.pop()
                pending = links[current].split("/") + pending
        resolved = "/".join(stack)
        if resolved not in present and resolved not in directories:
            raise ValueError("source link target is not exported")


def main(root: Path) -> None:
    before = inventory(root)
    with tempfile.TemporaryDirectory(prefix="factory-source-") as temporary:
        bundle = Path(temporary) / "source.bundle"
        git(root, "bundle", "create", str(bundle), "HEAD")
        if bundle.stat().st_size > LIMIT:
            raise ValueError("commit history exceeds export limit")
        data = bundle.read_bytes()
    if inventory(root) != before:
        raise ValueError("source changed during export")
    encoded = json.dumps(before, sort_keys=True, separators=(",", ":"))
    result = {
        "version": 1,
        "source": before,
        "digest": hashlib.sha256(encoded.encode()).hexdigest(),
        "bundle": base64.b64encode(data).decode("ascii"),
    }
    output = json.dumps(result, sort_keys=True)
    if len(output.encode()) > LIMIT:
        raise ValueError("source export exceeds limit")
    print(output)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
