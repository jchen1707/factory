"""Metadata-only identity of an explicitly selected Linux native Codex binary.

Standalone: the sandbox adapter executes this source with the launch environment.
Wrappers are deliberately unsupported. The caller must launch the returned absolute
binary path, not resolve `codex` again through PATH, when consuming this identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO


def _digest(stream: BinaryIO) -> str:
    stream.seek(0)
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise ValueError("runtime must be a regular file")
    if stream.read(4) != b"\x7fELF":
        raise ValueError("runtime must be a native ELF binary, not a launcher")
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        if size > 512 * 1024 * 1024:
            raise ValueError("runtime binary exceeds identity limit")
        digest.update(chunk)
    return digest.hexdigest()


def identify(binary: str) -> dict[str, str]:
    """Hash and query the same open executable; refuse replacement during observation.

    `/proc/self/fd` and pass_fds bind --version to the open Linux executable, even
    if its pathname is concurrently replaced. This starts no model or app-server.
    A later launch must still revalidate; this is an observation, not a file lock.
    """
    if not Path(binary).is_absolute():
        raise ValueError("runtime binary path must be absolute")
    path = Path(binary).resolve(strict=True)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
        digest = _digest(stream)
        result = subprocess.run(  # noqa: S603
            [f"/proc/self/fd/{stream.fileno()}", "--version"],
            pass_fds=(stream.fileno(),),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
            check=True,
        )
        version = result.stdout.strip()
        if not re.fullmatch(r"codex-cli [0-9][A-Za-z0-9.+-]*", version):
            raise ValueError("runtime version response invalid")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as current:
            if _digest(stream) != digest or _digest(current) != digest:
                raise ValueError("runtime binary changed during observation")
        if Path(binary).resolve(strict=True) != path:
            raise ValueError("runtime binary changed during observation")
    return {"runtime_path": str(path), "runtime_version": version, "runtime_sha256": digest}


if __name__ == "__main__":
    try:
        print(json.dumps(identify(sys.argv[1])))
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        print(json.dumps({"error": "runtime-identity-unavailable"}))
        raise SystemExit(1) from None
