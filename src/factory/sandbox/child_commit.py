"""Commit integrated paths without hooks, filters, checkout, or unrelated index changes."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def commit(root: Path, payload: dict[str, Any]) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": "Factory",
        "GIT_COMMITTER_NAME": "Factory",
        "GIT_AUTHOR_EMAIL": "factory@localhost",
        "GIT_COMMITTER_EMAIL": "factory@localhost",
        "GIT_AUTHOR_DATE": "@" + str(payload["time"]) + " +0000",
        "GIT_COMMITTER_DATE": "@" + str(payload["time"]) + " +0000",
    }

    def git(*args: str, data: bytes | None = None, index: Path | None = None) -> bytes:
        command_env = env | ({"GIT_INDEX_FILE": str(index)} if index else {})
        return subprocess.run(  # noqa: S603 — fixed Git plumbing, no candidate filters/hooks
            [
                "/usr/bin/git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                *args,
            ],
            input=data,
            capture_output=True,
            check=True,
            timeout=60,
            env=command_env,
        ).stdout

    directory = Path(git("rev-parse", "--absolute-git-dir").decode().strip())
    index_path = directory / "index"
    if index_path.is_symlink() or directory.is_symlink():
        raise ValueError("unsafe parent Git index")
    lock = directory / "index.lock"
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    owned_index = directory / ("factory-child-" + token + ".index")
    mutex = directory / ("factory-child-" + token + ".lock")
    fd = os.open(mutex, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        # A controller crash can leave its VM process alive. The VM-local lock
        # serializes that survivor with recovery. Only an inode shared with this
        # exact durable plan's owned index may be reclaimed; other Git locks hold.
        if lock.exists() or lock.is_symlink():
            if (
                lock.is_symlink()
                or owned_index.is_symlink()
                or not owned_index.exists()
                or not os.path.samefile(lock, owned_index)
            ):
                raise ValueError("unowned parent index lock")
            lock.unlink()
        # Take the standard Git lock before reading the real index; a concurrent
        # git add must never be overwritten by an older snapshot of staging.
        if owned_index.exists() or owned_index.is_symlink():
            if owned_index.is_symlink():
                raise ValueError("unsafe owned index")
            owned_index.unlink()
        descriptor = os.open(
            owned_index, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        os.close(descriptor)
        os.link(owned_index, lock)
        with tempfile.TemporaryDirectory(prefix="factory-integration-") as temporary:
            commit_index = Path(temporary) / "commit-index"
            git("read-tree", payload["base"], index=commit_index)
            entries = b""
            for change in payload["changes"]:
                entry = change["after"]
                name = entry["path"]
                if (
                    any(part in {"", ".", "..", ".git", ".factory"} for part in name.split("/"))
                    or "\x00" in name
                ):
                    raise ValueError("unsafe commit path")
                if entry["mode"] is None:
                    record = "0 " + "0" * 40 + "\t" + name
                else:
                    blob = (
                        git(
                            "hash-object",
                            "-w",
                            "--stdin",
                            data=base64.b64decode(entry["data"], validate=True),
                        )
                        .decode()
                        .strip()
                    )
                    record = (
                        ("100755" if entry["mode"] == 0o755 else "100644")
                        + " "
                        + blob
                        + "\t"
                        + name
                    )
                entries += record.encode() + b"\0"
            git("update-index", "-z", "--index-info", data=entries, index=commit_index)
            tree = git("write-tree", index=commit_index).decode().strip()
            created = (
                git("commit-tree", tree, "-p", payload["base"], data=payload["message"].encode())
                .decode()
                .strip()
            )
            head = git("rev-parse", "HEAD").decode().strip()
            if head not in {payload["base"], created}:
                raise ValueError("parent commit changed")
            staged_index = Path(temporary) / "staged-index"
            staged_index.write_bytes(index_path.read_bytes())
            git("update-index", "-z", "--index-info", data=entries, index=staged_index)
            # The desired commit is deterministic, so a lost acknowledgement cannot
            # create another commit. Parent/tree/metadata equality determines its SHA.
            descriptor = os.open(owned_index, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(staged_index.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            if head == payload["base"]:
                git("update-ref", "HEAD", created, payload["base"])
            os.replace(lock, index_path)
            return created


if __name__ == "__main__":
    raw = sys.stdin.read(128 * 1024 * 1024 + 1)
    if len(raw.encode()) > 128 * 1024 * 1024:
        raise ValueError("commit payload exceeds limit")
    print(commit(Path(sys.argv[1]), json.loads(raw)))
