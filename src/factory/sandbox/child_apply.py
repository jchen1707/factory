"""Source-owned, bounded file comparison/application inside the original parent VM."""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from typing import Any

LIMIT = 64 * 1024 * 1024


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def apply(root: Path, change: dict[str, Any], *, check_only: bool = False) -> str:
    before, after = change["before"], change["after"]
    name = before["path"]
    if name != after["path"] or not name or "\\" in name or "\x00" in name:
        raise ValueError("invalid integration path")
    parts = name.split("/")
    if any(part in {"", ".", "..", ".git", ".factory"} for part in parts):
        raise ValueError("unsafe integration path")
    for entry in (before, after):
        if entry["mode"] not in {None, 0o644, 0o755}:
            raise ValueError("invalid integration mode")
        if entry["mode"] is None:
            if entry["data"] is not None:
                raise ValueError("invalid deleted file")
        elif len(base64.b64decode(entry["data"], validate=True)) > LIMIT:
            raise ValueError("integration file exceeds limit")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError:
                if before["mode"] is not None:
                    raise ValueError("parent file disappeared") from None
                if check_only:
                    return "checked"
                os.mkdir(part, dir_fd=descriptor)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            os.close(descriptor)
            descriptor = child
        try:
            item = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
            )
        except FileNotFoundError:
            actual = {"path": name, "mode": None, "data": None}
        else:
            with os.fdopen(item, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > LIMIT:
                    raise ValueError("unsupported parent file")
                data = stream.read(LIMIT + 1)
                if len(data) > LIMIT or _identity(metadata) != _identity(os.fstat(stream.fileno())):
                    raise ValueError("parent changed during integration")
                actual = {
                    "path": name,
                    "mode": 0o755 if metadata.st_mode & 0o111 else 0o644,
                    "data": base64.b64encode(data).decode("ascii"),
                }
        if actual == after:
            return "already-applied"
        if actual != before:
            raise ValueError("parent edit conflicts with child")
        if check_only:
            return "checked"
        if after["mode"] is None:
            os.unlink(parts[-1], dir_fd=descriptor)
        else:
            temporary = ".factory-child-" + uuid.uuid4().hex
            item = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                after["mode"],
                dir_fd=descriptor,
            )
            with os.fdopen(item, "wb") as stream:
                stream.write(base64.b64decode(after["data"], validate=True))
                stream.flush()
                os.fchmod(stream.fileno(), after["mode"])
                os.fsync(stream.fileno())
            os.replace(temporary, parts[-1], src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
        return "applied"
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raw = sys.stdin.read(LIMIT + 1)
    if len(raw.encode()) > LIMIT:
        raise ValueError("integration payload exceeds limit")
    print(apply(Path(sys.argv[1]), json.loads(raw), check_only=len(sys.argv) > 2))
