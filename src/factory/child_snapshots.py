"""Durable host publication of a private clone's point-in-time child source."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from pathlib import Path
from typing import Any

from factory.delegation_controller import _mkdir
from factory.machine import Blocked
from factory.sandbox import child_source
from factory.steps import Context


def prepare(
    ctx: Context, parent: dict[str, Any], request: dict[str, Any], root: Path
) -> dict[str, Any]:
    """Freeze export once, publish once, reconcile publication without replacing source."""
    _mkdir(root)
    owner = (ctx.run.id, parent["attempt"], request["id"], "child-source", "snapshot")
    with (root / "snapshot.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = ctx.store.find_effect(*owner)
        if previous and previous.status == "confirmed":
            retained = json.loads(previous.external_id or "{}")
            validate(ctx, retained)
            return retained
        ctx.store.intend_effect(*owner)
        try:
            generation = _generation(ctx)
            exported = root / "export.json"
            if exported.exists():
                if exported.is_symlink():
                    raise ValueError("source export replaced")
                value = json.loads(exported.read_text())
                if value["generation"] != generation:
                    raise ValueError("source VM generation changed")
            else:
                value = {
                    "generation": generation,
                    "export": child_source.capture(
                        ctx.sandbox, ctx.project.build_sandbox, ctx.worktree
                    ),
                }
                if _generation(ctx) != generation:
                    raise ValueError("source VM generation changed")
                _publish(exported, value)
            child_source.validate(value["export"])
            publication = root / "snapshot.json"
            if publication.exists():
                retained = json.loads(publication.read_text())
            else:
                # A killed importer leaves a named partial directory; a restart imports the
                # same frozen export into a fresh one, never deleting or recapturing parent work.
                destination = root / ("source-" + uuid.uuid4().hex)
                child_source.materialize(value["export"], destination)
                retained = {
                    "path": str(destination),
                    "generation": generation,
                    "head": value["export"]["source"]["head"],
                    "source_digest": value["export"]["digest"],
                    "tree_digest": child_source.tree_digest(destination),
                }
                _publish(publication, retained)
            if retained["source_digest"] != value["export"]["digest"]:
                raise ValueError("snapshot publication changed")
            validate(ctx, retained)
            from factory.steps import _crash_if_asked

            _crash_if_asked("child-source:published")
            ctx.store.confirm_effect(*owner, json.dumps(retained, sort_keys=True))
            return retained
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Blocked("child-source-snapshot-refused", request["id"]) from exc


def validate(ctx: Context, retained: dict[str, Any]) -> None:
    try:
        if _generation(ctx) != retained["generation"]:
            raise ValueError("source VM generation changed")
        if child_source.tree_digest(Path(retained["path"])) != retained["tree_digest"]:
            raise ValueError("source snapshot changed")
        result = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(ctx.worktree),
                "rev-parse",
                "HEAD",
            ],
            timeout=60,
        )
        if not result.ok or result.stdout.strip() != retained["head"]:
            raise ValueError("parent commit changed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Blocked("child-source-stale", "Clone identity, base or snapshot changed") from exc


def _generation(ctx: Context) -> str:
    expected = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_generation")
    reader = getattr(ctx.sandbox, "generation", None)
    if (
        not isinstance(expected, str)
        or not callable(reader)
        or reader(ctx.project.build_sandbox) != expected
    ):
        raise ValueError("retained parent generation is required")
    return expected


def _publish(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
