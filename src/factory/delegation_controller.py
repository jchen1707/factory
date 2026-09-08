"""Invocation-owned mailbox mounts and restart-safe host servicing.

Preparation returns a NEW sandbox specification. The caller must create and certify
that exact specification before freezing configuration into an application launch.
This module does not grant launch permission or start children.
"""

from __future__ import annotations

import fcntl
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
        retained = self.store.find_effect(
            run_id, attempt, parent_id, "delegation-mailbox", "mounts"
        )
        mailbox_owner = (
            json.loads(retained.external_id or "{}").get("mailbox_owner", parent_id)
            if retained
            else parent_id
        )
        name = hashlib.sha256(mailbox_owner.encode()).hexdigest()
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
                "mailbox_owner": mailbox_owner,
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

    def transfer(
        self,
        previous_id: str,
        parent_id: str,
        attempt: int,
        source: Path,
        base: SandboxSpec,
        *,
        resume_session: str | None = None,
    ) -> SandboxSpec:
        """Serialize archive completion and ownership publication across controllers."""
        if self.store.runtime.db.in_transaction:
            raise ValueError("mailbox transfer cannot run inside an outer transaction")
        record = self._record(previous_id)
        if record is None:
            raise ValueError("previous mailbox missing")
        self._validate_paths(record)
        lock = Path(record["configuration"]["inbox"]).parent / "transfer.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                return self._transfer(
                    previous_id, parent_id, attempt, source, base, resume_session=resume_session
                )
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _transfer(
        self,
        previous_id: str,
        parent_id: str,
        attempt: int,
        source: Path,
        base: SandboxSpec,
        *,
        resume_session: str | None = None,
    ) -> SandboxSpec:
        """Transfer a drained mailbox in the same VM through a committed archive intent."""
        if self.store.runtime.db.in_transaction:
            raise ValueError("mailbox transfer cannot run inside an outer transaction")
        if not parent_id or parent_id == previous_id or type(attempt) is not int or attempt < 1:
            raise ValueError("invalid transfer owner")
        previous = self._parent(previous_id)
        run_id = previous["run_id"]
        owner = (run_id, attempt, parent_id, "delegation-mailbox", "mounts")
        transfer = (run_id, attempt, parent_id, "delegation-transfer", "archive")
        with self.store.runtime.transaction():
            existing = self.store.find_effect(*owner)
            if existing is not None:
                record = json.loads(existing.external_id or "{}")
                if (
                    record.get("predecessor") != previous_id
                    or record.get("resume_session") != resume_session
                ):
                    raise ValueError("mailbox transfer ownership changed")
                return self.prepare(run_id, attempt, parent_id, source, base)
            for binding in self.store.effects(run_id):
                if (
                    binding.system == "delegation-transfer"
                    and binding.external_id
                    and binding.step != parent_id
                    and json.loads(binding.external_id)["record"]["predecessor"] == previous_id
                ):
                    raise ValueError("previous mailbox already has a successor")
            lease = self.store.runtime.db.execute(
                "SELECT status FROM agent_leases WHERE invocation_id=?", (previous_id,)
            ).fetchone()
            if (
                lease is None
                or lease["status"] == "active"
                or self.store.runtime.db.execute(
                    "SELECT 1 FROM delegation_requests WHERE parent_id=? "
                    "AND status NOT IN ('completed','failed','cancelled')",
                    (previous_id,),
                ).fetchone()
            ):
                raise ValueError("drain the previous parent and children before transfer")
            record = self._record(previous_id)
            if record is None:
                raise ValueError("previous mailbox missing")
            self._validate_paths(record)
            if self.store.find_effect(
                run_id, previous["attempt"], previous_id, "delegation-mailbox", "failure"
            ):
                raise ValueError("failed mailbox cannot transfer")
            snapshot = self.store.runtime.policy(run_id)
            if snapshot is None or snapshot["revision"] != record["policy_revision"]:
                raise ValueError("mailbox authority changed")
            config = record["configuration"]
            spec = replace(
                base,
                name=record["spec"]["name"],
                workspaces=(
                    *base.workspaces,
                    Workspace(Path(config["inbox"])),
                    Workspace(Path(config["outbox"]), readonly=True),
                ),
            )
            if _spec(spec) != record["spec"] or str(source.resolve()) != record["source"]:
                raise ValueError("mailbox transfer specification changed")
            if self.store.runtime.invocation(parent_id) is not None:
                raise ValueError("transfer must precede invocation accounting")
            record = dict(record) | {
                "predecessor": previous_id,
                "resume_session": resume_session,
                "mailbox_owner": record.get("mailbox_owner", previous_id),
            }
            archive = Path(config["inbox"]).parent / (
                "archive-" + hashlib.sha256(previous_id.encode()).hexdigest()
            )
            contract = json.dumps({"record": record, "archive": str(archive)}, sort_keys=True)
            intended = self.store.find_effect(*transfer)
            if intended is not None:
                if intended.external_id != contract:
                    raise ValueError("mailbox transfer intent is immutable")
            else:
                self.store.intend_effect(*transfer)
                self.store.runtime.db.execute(
                    "UPDATE effects SET external_id=? WHERE run_id=? AND attempt=? AND step=? "
                    "AND system='delegation-transfer' AND key='archive'",
                    (contract, run_id, attempt, parent_id),
                )
        # No database transaction spans filesystem effects. Each move is recoverable
        # from its unique archive destination; ambiguous duplicate files are preserved.
        _mkdir(archive)
        for directory, filename in (("inbox", "request.json"), ("outbox", "response.json")):
            old = Path(config[directory]) / filename
            saved = archive / filename
            if old.exists() or old.is_symlink():
                if saved.exists() or saved.is_symlink():
                    raise ValueError("mailbox archive requires reconciliation")
                old.rename(saved)
        with self.store.runtime.transaction():
            if self.store.find_effect(*owner) is None:
                self.store.intend_effect(*owner)
                self.store.confirm_effect(*owner, json.dumps(record, sort_keys=True))
            self.store.confirm_effect(*transfer, contract)
            return self.prepare(run_id, attempt, parent_id, source, base)

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
                self.cancel_requests(parent["id"])
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
                self.cancel_requests(parent["id"])
        return serviced

    def cancel_requests(self, parent_id: str) -> None:
        """Fence admission when a parent exits or loses its channel; retain paid leases."""
        record = self._record(parent_id)
        if record is None:
            return
        broker = DelegationBroker(self.store, parent_id, Path(record["source"]))
        for request in broker.requests():
            broker.cancel(request["id"])

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
