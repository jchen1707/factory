"""Private child source copies and bounded host-validated edit artifacts."""

from __future__ import annotations

import fcntl
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from factory.delegation_controller import _mkdir
from factory.machine import Blocked
from factory.sandbox import child_source, source_observer
from factory.steps import Context


def prepare(
    ctx: Context,
    parent: dict[str, Any],
    request: dict[str, Any],
    root: Path,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Copy a frozen source once; neither Git objects nor dependencies are shared."""
    _mkdir(root)
    with (root / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare(ctx, parent, request, root, snapshot)


def _prepare(
    ctx: Context,
    parent: dict[str, Any],
    request: dict[str, Any],
    root: Path,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    owner = (ctx.run.id, parent["attempt"], request["id"], "child-workspace", "prepare")
    previous = ctx.store.find_effect(*owner)
    if previous and previous.status == "confirmed":
        result = json.loads(previous.external_id or "{}")
        path = Path(result["path"])
        if path.is_symlink() or not path.is_dir() or list(_identity(path)) != result["identity"]:
            raise Blocked("child-workspace-stale", request["id"])
        return dict(result)
    source = Path(snapshot["path"])
    if child_source.tree_digest(source) != snapshot["tree_digest"]:
        raise Blocked("child-source-stale", request["id"])
    _mkdir(root)
    destination = root / ("writable-" + uuid.uuid4().hex)
    ctx.store.intend_effect(*owner)
    if destination.exists():
        # An interrupted initial copy has no trustworthy completeness evidence.
        raise Blocked("child-workspace-incomplete", str(destination))
    shutil.copytree(source, destination, symlinks=True)
    if child_source.tree_digest(destination) != snapshot["tree_digest"]:
        raise Blocked("child-workspace-incomplete", str(destination))
    result = {
        "path": str(destination),
        "identity": list(_identity(destination)),
        "snapshot": snapshot,
    }
    ctx.store.confirm_effect(*owner, json.dumps(result, sort_keys=True))
    return result


def _identity(path: Path) -> tuple[int, int]:
    item = path.stat()
    return item.st_dev, item.st_ino


def artifact(
    workspace: dict[str, Any], scopes: list[str], exported: dict[str, Any]
) -> dict[str, Any]:
    """Derive edits from actual source bytes, never from a model's reported patch."""
    source = Path(workspace["snapshot"]["path"])
    child = Path(workspace["path"])
    if child.is_symlink() or list(_identity(child)) != workspace["identity"]:
        raise ValueError("child workspace replaced")
    if child_source.tree_digest(source) != workspace["snapshot"]["tree_digest"]:
        raise ValueError("child baseline changed")
    before = source_observer.inventory(source)
    # Candidate Git metadata is interpreted only inside its VM. The host reads
    # verified immutable baseline metadata and this bounded exported packet.
    child_source.validate(exported)
    after = exported["source"]
    baseline = {entry["path"]: entry for entry in before["files"]}
    candidate = {entry["path"]: entry for entry in after["files"]}
    changes = []
    for name in sorted(baseline.keys() | candidate.keys()):
        absent = {"path": name, "mode": None, "data": None}
        old = baseline.get(name, absent)
        new = candidate.get(name, absent)
        if old == new:
            continue
        if old["mode"] == 0o120000 or new["mode"] == 0o120000:
            raise ValueError("child symlink edits require explicit integration support")
        if not any(name == scope or name.startswith(scope + "/") for scope in scopes):
            raise ValueError("child edit outside declared scope: " + name)
        if any(part in {".git", ".factory"} for part in Path(name).parts):
            raise ValueError("child edit targets control metadata")
        changes.append({"before": old, "after": new})
    return {"base": before["head"], "child_head": after["head"], "changes": changes}
