"""Standalone Linux observation; stdout contains digests and mount metadata only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("hook input has a symlink component")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("hook input must be a regular file")
        data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("hook input exceeds size limit")
        return hashlib.sha256(data).hexdigest()


def observe(request: dict[str, Any]) -> dict[str, Any]:
    mounts: list[dict[str, Any]] = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields, filesystem = left.split(), right.split()
        unescape = lambda value: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)  # noqa: E731
        mounts.append(
            {
                "root": unescape(fields[3]),
                "path": unescape(fields[4]),
                "options": sorted(fields[5].split(",")),
                "type": filesystem[0],
                "source": unescape(filesystem[1]),
            }
        )
    for index, workspace in enumerate(request["workspaces"]):
        path = Path(workspace["path"])
        if path.resolve() != path or not path.is_dir():
            raise ValueError("workspace is missing or aliased")
        matches = [mount for mount in mounts if mount["path"] == str(path)]
        if request["clone"] and index == 0:
            # A clone lives on the VM's private filesystem, never a host filesystem.
            covering = [mount for mount in mounts if path.is_relative_to(mount["path"])]
            mount = max(covering, key=lambda row: len(row["path"]))
            if mount["type"] in {"virtiofs", "9p", "fuse", "nfs", "nfs4"}:
                raise ValueError("clone workspace is host-mounted")
        elif len(matches) != 1 or matches[0]["type"] != "virtiofs":
            raise ValueError("workspace mount is missing or has an unknown layout")
        else:
            mount = matches[0]
            if mount["root"] != str(path):
                raise ValueError("workspace mount source changed")
        if ("ro" in mount["options"]) != workspace["readonly"]:
            raise ValueError("workspace mount permissions changed")
    if (
        digest({name: os.environ.get(name) for name in request["env_names"]})
        != request["env_sha256"]
    ):
        raise ValueError("requested environment was not applied")
    if os.environ.get("HARNESS_SKIP_VERIFY"):
        raise ValueError("hook enforcement is disabled")
    for name, expected in request["hook_files"].items():
        if file_digest(Path(name)) != expected:
            raise ValueError("trusted hook input changed")
    configs = {}
    roots = [Path.cwd(), *Path.cwd().parents]
    roots.append(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).parent)
    for root in roots:
        for relative in (".codex/hooks.json", ".codex/config.toml"):
            path = root / relative
            if path.exists() or path.is_symlink():
                configs[str(path)] = file_digest(path)
    return {
        "mounts": sorted(mounts, key=lambda row: row["path"]),
        "environment_sha256": digest(dict(os.environ)),
        "configurations": configs,
        "hooks": request["hook_files"],
        "credential_names": sorted(
            name for name in request.get("credential_names", []) if os.environ.get(name)
        ),
    }


if __name__ == "__main__":
    try:
        print(json.dumps(observe(json.loads(sys.argv[1]))))
    except (OSError, ValueError, KeyError, TypeError):
        print("certification sandbox observation refused", file=sys.stderr)
        raise SystemExit(1) from None
