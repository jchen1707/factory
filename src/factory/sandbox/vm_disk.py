"""Free bytes and inodes on a sandbox VM's filesystems, read with one `df` exec.

The host floor (`disk_min_free_gb`) never sees inside a VM. On 2026-09-13 the nemoclaw-dev
build VM had 1,310,720 inodes and 3 free, with 8.29 GB of bytes still available, and the
first sign was a failed run (`docs/archive/codex-era-build-runtime-incidents.md`).

`df -P` rather than a Python `statvfs` script: `df` ships with coreutils, and `-P` pins one
line per path in argument order. Real captures are in `tests/fixtures/sbx/`.
"""

from __future__ import annotations

import math
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from factory.sandbox.base import SandboxAdapter

__all__ = [
    "VM_HOME",
    "Free",
    "ProbeError",
    "measure",
    "parse",
    "probe_argv",
    "shortfalls",
    "summary",
]

#: The agent's home in every sbx agent image. Venvs, caches and `/tmp` share its root
#: filesystem, which is the one that ran out.
VM_HOME = "/home/agent"

#: `LC_ALL=C` keeps the headers the parser looks for out of translation.
_SCRIPT = 'LC_ALL=C df -Pk "$@" && LC_ALL=C df -Pi "$@"'


class ProbeError(Exception):
    """The VM could not be measured. Never a pass."""


@dataclass(frozen=True)
class Free:
    path: str
    mount: str
    bytes_available: int
    #: `None` when the filesystem keeps no inode count (`df -Pi` prints 0 inodes).
    inodes_free: int | None


def probe_argv(paths: Sequence[str]) -> list[str]:
    return ["/bin/sh", "-c", _SCRIPT, "sh", *paths]


def measure(sandbox: SandboxAdapter, name: str, paths: Sequence[str]) -> tuple[Free, ...]:
    try:
        done = sandbox.exec_sync(name, probe_argv(paths), timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"df in {name} timed out after {exc.timeout}s") from exc
    if not done.ok:
        first = (done.stderr or done.stdout).strip().splitlines()
        raise ProbeError(f"df in {name} exited {done.returncode}: {first[0] if first else ''}")
    return parse(done.stdout, paths)


def parse(stdout: str, paths: Sequence[str]) -> tuple[Free, ...]:
    # Anything `sbx exec` prints before the first table, such as a start-up line from a
    # stopped sandbox, is not df's.
    lines = stdout.splitlines()
    lines = lines[next((i for i, line in enumerate(lines) if line.startswith("Filesystem")), 0) :]
    starts = [i for i, line in enumerate(lines) if line.startswith("Filesystem")]
    if len(starts) != 2 or "1024-blocks" not in lines[0] or "IFree" not in lines[starts[1]]:
        raise ProbeError(f"df printed no -Pk and -Pi tables: {stdout[:200]!r}")
    blocks = _rows(lines[1 : starts[1]], paths)
    inodes = _rows(lines[starts[1] + 1 :], paths)
    if [row[5] for row in blocks] != [row[5] for row in inodes]:
        raise ProbeError("df's byte and inode tables name different mounts")
    if not all(row[1].isdigit() for row in inodes):
        raise ProbeError("df printed a non-numeric inode total")
    return tuple(
        Free(
            path=path,
            mount=b[5],
            bytes_available=int(b[3]) * 1024,
            inodes_free=int(i[3]) if int(i[1]) else None,
        )
        for path, b, i in zip(paths, blocks, inodes, strict=True)
    )


def _rows(lines: Sequence[str], paths: Sequence[str]) -> list[list[str]]:
    # maxsplit 5: a mount point may contain spaces, and it is the last column.
    rows = [line.split(None, 5) for line in lines if line.strip()]
    if len(rows) != len(paths) or any(len(row) != 6 or not row[3].isdigit() for row in rows):
        raise ProbeError(f"df printed {len(rows)} row(s) for {len(paths)} path(s)")
    return rows


def shortfalls(rows: Sequence[Free], *, min_free_gb: int, min_free_inodes: int) -> list[str]:
    """One line per filesystem under a floor. Empty means every floor holds."""
    found: list[str] = []
    for row in _per_mount(rows):
        if row.inodes_free is not None and row.inodes_free < min_free_inodes:
            found.append(
                f"{row.path} on {row.mount}: {row.inodes_free:,} inodes free, "
                f"floor {min_free_inodes:,}"
            )
        if row.bytes_available < min_free_gb * 1_000_000_000:
            found.append(
                f"{row.path} on {row.mount}: {row.bytes_available / 1e9:.1f} GB free, "
                f"floor {min_free_gb} GB"
            )
    return found


def summary(rows: Sequence[Free]) -> str:
    return "; ".join(
        f"{row.mount}: {row.bytes_available / 1e9:.1f} GB and "
        + ("no inode count" if row.inodes_free is None else f"{row.inodes_free:,} inodes free")
        for row in _per_mount(rows)
    )


def _per_mount(rows: Sequence[Free]) -> list[Free]:
    """A clone workspace and the home share the root mount; report it once, at its lowest."""
    worst: dict[str, Free] = {}
    for row in rows:
        prior = worst.setdefault(row.mount, row)
        if _scarcity(row) < _scarcity(prior):
            worst[row.mount] = row
    return list(worst.values())


def _scarcity(row: Free) -> tuple[float, int]:
    return (math.inf if row.inodes_free is None else row.inodes_free, row.bytes_available)
