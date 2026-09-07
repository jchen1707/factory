"""Invocation-owned mailbox mounts and restart-safe host servicing.

Preparation returns a NEW sandbox specification. The caller must create and certify
that exact specification before freezing configuration into an application launch.
This module does not grant launch permission or start children.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from factory.delegation import DelegationBroker, request_schema
from factory.delegation_transport import DelegationMailbox, configuration
from factory.sandbox.base import SandboxSpec, Workspace
from factory.store import Store


def _spec(spec: SandboxSpec) -> dict[str, Any]:
    value = asdict(spec)
    value["env"] = hashlib.sha256(json.dumps(dict(spec.env), sort_keys=True).encode()).hexdigest()
    value["workspaces"] = [
        {"path": str(w.path.resolve()), "readonly": w.readonly} for w in spec.workspaces
    ]
    return json.loads(json.dumps(value))


class DelegationController:
    def __init__(self, store: Store, home: Path) -> None:
        self.store = store
        self.root = home.resolve() / "state/delegation-mailboxes"

    def prepare(
        self, run_id: str, attempt: int, parent_id: str, source_root: Path, base: SandboxSpec
    ) -> SandboxSpec:
        """Persist owned mounts before creation; never retrofit a shared running sandbox."""
        if not parent_id or type(attempt) is not int or attempt < 1:
            raise ValueError("invalid mailbox owner")
        snapshot = self.store.runtime.policy(run_id)
        if snapshot is None:
            raise ValueError("mailbox requires trusted authority")
        schema = request_schema(snapshot)
        run = self.store.run_by_id(run_id)
        if run is None or base.project != run.project or base.role != "build":
            raise ValueError("mailbox specification must belong to the builder project")
        if base.allowed_custom_secrets or base.static_mcp:
            raise ValueError("delegation cannot inherit external write capabilities")
        source = source_root.resolve(strict=True)
        if not any(source.is_relative_to(w.path.resolve()) for w in base.workspaces):
            raise ValueError("delegation source must be mounted")
        # Reject even read-only overlapping roots: the controller state tree must not
        # enter a candidate sandbox. Only the two leaf directories are exposed.
        for workspace in base.workspaces:
            path = workspace.path.resolve()
            if self.root.is_relative_to(path) or path.is_relative_to(self.root):
                raise ValueError("mailbox state must be outside all existing mounts")
        name = hashlib.sha256(parent_id.encode()).hexdigest()
        directory = self.root / name
        inbox, outbox = directory / "inbox", directory / "outbox"
        spec = replace(
            base,
            name="factory-build-delegation-" + name[:24],
            workspaces=(*base.workspaces, Workspace(inbox), Workspace(outbox, readonly=True)),
        )
        with self.store.runtime.transaction():
            parent = self.store.runtime.invocation(parent_id)
            if parent is not None and (parent["run_id"], parent["attempt"]) != (run_id, attempt):
                raise ValueError("mailbox invocation ownership changed")
            bindings = self.store.runtime.db.execute(
                "SELECT run_id,attempt FROM effects WHERE step=? AND system='delegation-mailbox'",
                (parent_id,),
            ).fetchall()
            if any((row["run_id"], row["attempt"]) != (run_id, attempt) for row in bindings):
                raise ValueError("mailbox preparation ownership changed")
            owner = (run_id, attempt, parent_id, "delegation-mailbox", "mounts")
            existing = self.store.find_effect(*owner)
            previous = json.loads(existing.external_id or "{}") if existing else None
            if previous is not None:
                if (
                    previous["spec"] != _spec(spec)
                    or previous["source"] != str(source)
                    or previous["policy_revision"] != snapshot["revision"]
                    or existing is None
                    or existing.status != "confirmed"
                ):
                    raise ValueError("mailbox preparation is immutable")
                self._validate_paths(previous)
                return spec
            if self.store.runtime.db.execute(
                "SELECT 1 FROM agent_leases WHERE invocation_id=?", (parent_id,)
            ).fetchone() or self.store.find_effect(
                run_id, attempt, parent_id, "agent-launch", "spawn"
            ):
                raise ValueError("mailbox preparation must precede parent admission")
            for path in (self.root, directory, inbox, outbox):
                _mkdir(path)
            record = {
                "spec": _spec(spec),
                "source": str(source),
                "configuration": configuration(schema, inbox, outbox),
                "policy_revision": snapshot["revision"],
                "directories": {
                    str(path): [path.stat().st_dev, path.stat().st_ino]
                    for path in (self.root, directory, inbox, outbox)
                },
            }
            self.store.intend_effect(*owner)
            self.store.confirm_effect(*owner, json.dumps(record, sort_keys=True))
        return spec

    def configuration(self, parent_id: str, spec: SandboxSpec) -> dict[str, Any]:
        """Read retained registration for the exact spec; not a compatibility attestation."""
        record = self._record(parent_id)
        if record is None or record["spec"] != _spec(spec):
            raise ValueError("mailbox specification changed or missing")
        self._validate_paths(record)
        if self._parent(parent_id)["metadata"].get("policy_revision") != record["policy_revision"]:
            raise ValueError("mailbox authority changed")
        broker = DelegationBroker(self.store, parent_id, Path(record["source"]))
        config = record["configuration"]
        if (
            DelegationMailbox(broker, Path(config["inbox"]), Path(config["outbox"])).configuration()
            != config
        ):
            raise ValueError("mailbox authority changed")
        return config

    def service_run(self, run_id: str) -> int:
        """Recover host bindings from SQLite; never follow worker-supplied paths or IDs."""
        serviced = 0
        for effect in self.store.effects(run_id):
            if (
                effect.system != "delegation-mailbox"
                or effect.key != "mounts"
                or effect.status != "confirmed"
            ):
                continue
            parent = self.store.runtime.invocation(effect.step)
            if parent is None:
                continue
            lease = self.store.runtime.db.execute(
                "SELECT status FROM agent_leases WHERE invocation_id=?", (parent["id"],)
            ).fetchone()
            if lease is None or lease["status"] != "active":
                continue
            launch = self.store.find_effect(
                run_id, parent["attempt"], parent["id"], "agent-launch", "spawn"
            )
            if launch is None:
                continue
            failure = (run_id, parent["attempt"], parent["id"], "delegation-mailbox", "failure")
            if self.store.find_effect(*failure) is not None:
                continue
            try:
                record = self._record(parent["id"])
                if record is None:
                    raise ValueError("mailbox ownership record missing")
                contract = json.loads(launch.external_id or "{}")
                if (
                    contract.get("parent_id") is not None
                    or contract["handle"]["sandbox"] != record["spec"]["name"]
                    or contract["handle"]["workdir"] != record["source"]
                ):
                    raise ValueError("mailbox launch ownership changed")
                self._validate_paths(record)
                config = record["configuration"]
                mailbox = DelegationMailbox(
                    DelegationBroker(self.store, parent["id"], Path(record["source"])),
                    Path(config["inbox"]),
                    Path(config["outbox"]),
                )
                serviced += mailbox.service()
            except (OSError, ValueError, TypeError, KeyError) as exc:
                # Poisoned candidate input must not prevent terminal usage collection.
                # Fence this channel permanently; preserve files for diagnosis. A fresh
                # invocation gets fresh mounts. Never repeat untrusted details in a reply.
                with self.store.runtime.transaction():
                    if self.store.find_effect(*failure) is None:
                        self.store.intend_effect(*failure)
                        self.store.confirm_effect(*failure, type(exc).__name__)
                        self.store.runtime.audit(
                            "run",
                            run_id,
                            "delegation-transport-failed",
                            {"parent": parent["id"], "error": type(exc).__name__},
                        )
        return serviced

    def _parent(self, parent_id: str) -> dict[str, Any]:
        parent = self.store.runtime.invocation(parent_id)
        if parent is None:
            raise ValueError("unknown mailbox parent")
        return parent

    def _record(self, parent_id: str) -> dict[str, Any] | None:
        parent = self._parent(parent_id)
        effect = self.store.find_effect(
            parent["run_id"], parent["attempt"], parent_id, "delegation-mailbox", "mounts"
        )
        if effect is None:
            return None
        if effect.status != "confirmed" or effect.external_id is None:
            raise ValueError("mailbox preparation requires reconciliation")
        return json.loads(effect.external_id)

    def _validate_paths(self, record: dict[str, Any]) -> None:
        if str(self.root) not in record["directories"]:
            raise ValueError("mailbox controller root changed")
        for name, identity in record["directories"].items():
            path = Path(name)
            if path.is_symlink() or not path.is_dir() or path.resolve() != path:
                raise ValueError("mailbox directory changed")
            if [path.stat().st_dev, path.stat().st_ino] != identity:
                raise ValueError("mailbox directory identity changed")


def _mkdir(path: Path) -> None:
    """Create beneath directory descriptors; an existing symlink is never followed."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
