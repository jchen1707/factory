"""Prove from inside a sandbox VM that a run's captured authority refuses every write.

The host already refuses a sandbox whose listing lacks the `:ro` authority mount (#133), but
a listing is sbx's word, not the kernel's. One `node` exec attempts a write on every captured
file and in every directory holding one. Only `EROFS` passes.

Measured on 2026-10-07 in a throwaway claude sandbox (`tests/fixtures/sbx/authority-*.json`):

- Every write on a `:ro` workspace failed with `EROFS`, and so did every create after the
  agent, which has passwordless sudo, ran `mount -o remount,bind,rw` on it. sbx enforces the
  share's read-only flag on the host side. `access(W_OK)` and `/proc/self/mountinfo` read only
  the guest's flag and reported that remounted share writable, so they are not evidence; a
  write attempt is.
- A mode-0444 file on that same mount failed with `EACCES`, because the permission check runs
  before the read-only one. `EACCES` therefore says nothing about the mount and does not pass.
- A writable bind mount nested under the share answered `writable` for the paths beneath it.

A file is opened `O_WRONLY | O_NOFOLLOW` without create, truncate or append, and a directory
gets a fresh `mkdtemp` that is removed at once, so a mount that turns out to be writable is
left as it was.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath

from factory.sandbox.base import SandboxAdapter

__all__ = ["READ_ONLY", "ProbeError", "observe", "parse", "probe_argv", "targets", "unprotected"]

#: The only outcome that proves the mount, not the file's mode, refused the write.
READ_ONLY = "EROFS"

#: Reads `{"root", "paths"}` on stdin; a path ending in `/` is a directory. Prints one JSON
#: object mapping each path to `writable`, `not-a-file`, `not-a-directory` or an errno code.
_SCRIPT = r"""
const fs = require("fs");
const { root, paths } = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const rel of paths) {
  const dir = rel.endsWith("/");
  const path = `${root}/${dir ? rel.slice(0, -1) : rel}`;
  try {
    const stat = fs.lstatSync(path);
    if (dir ? !stat.isDirectory() : !stat.isFile()) {
      out[rel] = dir ? "not-a-directory" : "not-a-file";
      continue;
    }
    if (dir) fs.rmdirSync(fs.mkdtempSync(`${path}/.factory-write-probe-`));
    else fs.closeSync(fs.openSync(path, fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW));
    out[rel] = "writable";
  } catch (error) {
    out[rel] = error.code || String(error);
  }
}
process.stdout.write(JSON.stringify(out) + "\n");
"""


class ProbeError(Exception):
    """The VM could not be asked. Never a pass."""


def probe_argv() -> list[str]:
    return ["node", "-e", _SCRIPT]


def targets(files: Iterable[str]) -> list[str]:
    """Every captured file, then every directory holding one as `dir/`, the root as `./`."""
    names = sorted(files)
    dirs = {f"{parent}/" for name in names for parent in PurePosixPath(name).parents}
    return [*names, *sorted(dirs)]


def observe(sandbox: SandboxAdapter, name: str, root: str, paths: Sequence[str]) -> dict[str, str]:
    request = json.dumps({"root": root, "paths": list(paths)})
    try:
        done = sandbox.exec_sync(name, probe_argv(), stdin=request, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"the write probe in {name} timed out after {exc.timeout}s") from exc
    if not done.ok:
        first = (done.stderr or done.stdout).strip().splitlines()
        raise ProbeError(
            f"the write probe in {name} exited {done.returncode}: {first[0] if first else ''}"
        )
    return parse(done.stdout, paths)


def parse(stdout: str, paths: Sequence[str]) -> dict[str, str]:
    # The answer is the last line; a stopped sandbox prints a start-up line before it.
    lines = stdout.strip().splitlines()
    try:
        observed = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        observed = None
    if not isinstance(observed, dict) or not all(isinstance(v, str) for v in observed.values()):
        raise ProbeError(f"the write probe printed no answer: {stdout[:200]!r}")
    if set(observed) != set(paths):
        raise ProbeError(
            f"the write probe answered for {len(observed)} path(s), not the {len(paths)} asked"
        )
    return observed


def unprotected(observed: Mapping[str, str]) -> list[str]:
    """One `path: outcome` line per path the mount did not refuse. Empty means all did."""
    return [f"{path}: {outcome}" for path, outcome in observed.items() if outcome != READ_ONLY]
