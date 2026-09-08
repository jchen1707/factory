"""Host-owned child requests. Transport supplies task data, never execution authority."""

from __future__ import annotations

import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from factory import authority
from factory.agent.base import validate_against_schema
from factory.store import Store

MAX_REQUEST_BYTES = 64 * 1024


def request_schema(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Read the shared contract from verified host authority, without paid admission."""
    authority.validate_integrity(snapshot)
    return json.loads(
        (
            Path(snapshot["root"]) / ".agents/vendor/harness/schema/delegation-request.schema.json"
        ).read_text()
    )


class DelegationBroker:
    """Bound by the controller to one invocation and its approved source root.

    These constructor arguments must never come from tool arguments. This service
    persists requests only; accepting a request is not approval or paid admission.
    """

    def __init__(self, store: Store, parent_id: str, source_root: Path) -> None:
        self.store = store
        self.parent_id = parent_id
        self.source_root = source_root.resolve()

    def requests(self) -> list[dict[str, Any]]:
        """Recover owned handles after controller restart, including queued and terminal work."""
        rows = self.store.runtime.db.execute(
            "SELECT id FROM delegation_requests WHERE parent_id=? ORDER BY rowid", (self.parent_id,)
        ).fetchall()
        return [self.inspect(row["id"]) for row in rows]

    def inspect(self, request_id: str) -> dict[str, Any]:
        row = self.store.runtime.db.execute(
            "SELECT * FROM delegation_requests WHERE id=? AND parent_id=?",
            (request_id, self.parent_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown owned delegation request")
        status = self.store.runtime.settings("run", row["run_id"]).get("child-status:" + request_id)
        detail = {"waiting": status} if status and row["status"] in {"pending", "prepared"} else {}
        return (
            dict(row)
            | detail
            | {
                "request": json.loads(row["request"]),
                "result": json.loads(row["result"]) if row["result"] else None,
            }
        )

    def request(self, call_id: str, arguments: object) -> dict[str, Any]:
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 128:
            raise ValueError("invalid delegation call identity")
        encoded = json.dumps(arguments, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > MAX_REQUEST_BYTES:
            raise ValueError("delegation request exceeds size limit")
        with self.store.runtime.transaction():
            old = self.store.runtime.db.execute(
                "SELECT id,request FROM delegation_requests WHERE parent_id=? AND call_id=?",
                (self.parent_id, call_id),
            ).fetchone()
            if old:
                retained = json.loads(old["request"])
                if json.dumps(
                    retained["task"], sort_keys=True, allow_nan=False
                ) != encoded or retained["source_root"] != str(self.source_root):
                    raise ValueError("delegation request is immutable")
                return self.inspect(old["id"])
            parent, snapshot = self._admissible_parent()
            schema = self.request_schema()
            validate_against_schema(arguments, schema)
            if not isinstance(arguments, dict):
                raise ValueError("delegation task must be an object")
            if arguments["mode"] == "isolated-write":
                self._writable_scope(arguments["paths"], parent["run_id"])
            if not arguments["task"].strip():
                raise ValueError("empty delegation task")
            self._paths(arguments["paths"], allow_missing=arguments["mode"] == "isolated-write")
            payload = json.dumps(
                {
                    "task": arguments,
                    "policy_revision": snapshot["revision"],
                    "source_root": str(self.source_root),
                },
                sort_keys=True,
                allow_nan=False,
            )
            run = self.store.run_by_id(parent["run_id"])
            if run is None:
                raise ValueError("delegation owner disappeared")
            project = self.store.runtime.settings("project", run.project)
            settings = self.store.runtime.effective(run.project, run.id)
            ceiling = project.get("max_children_per_parent", 2)
            limit = settings.get("max_children_per_parent", ceiling)
            if type(ceiling) is not int or type(limit) is not int or not 1 <= limit <= ceiling:
                raise ValueError("invalid child limit or project ceiling")
            count = self.store.runtime.db.execute(
                "SELECT count(*) FROM delegation_requests WHERE parent_id=? "
                "AND status NOT IN ('completed','failed','cancelled')",
                (self.parent_id,),
            ).fetchone()[0]
            if count >= limit:
                raise ValueError("pending child limit reached; inspect or cancel existing handles")
            request_id = uuid.uuid4().hex
            self.store.runtime.db.execute(
                "INSERT INTO delegation_requests(id,parent_id,call_id,run_id,request,status) "
                "VALUES (?,?,?,?,?,'pending')",
                (request_id, self.parent_id, call_id, parent["run_id"], payload),
            )
            self.store.runtime.audit(
                "run",
                parent["run_id"],
                "delegation-requested",
                {"request": request_id, "parent": self.parent_id},
            )
        return self.inspect(request_id)

    def request_schema(self) -> dict[str, Any]:
        """Controller registration uses the same immutable contract as request admission."""
        _, snapshot = self._parent_contract()
        return request_schema(snapshot)

    def bind_child(self, request_id: str, child_id: str) -> dict[str, Any]:
        """Attach a host-prepared accounted invocation, never a worker-selected ID."""
        with self.store.runtime.transaction():
            request = self.inspect(request_id)
            parent, snapshot = self._admissible_parent()
            if request["child_id"] is not None:
                if request["child_id"] != child_id:
                    raise ValueError("child binding is immutable")
                return request
            if request["status"] != "pending":
                raise ValueError("delegation request is " + request["status"])
            child = self.store.runtime.invocation(child_id)
            if child is None or (
                child["run_id"],
                child["attempt"],
                child["metadata"].get("parent_id"),
                child["metadata"].get("policy_revision"),
                child["metadata"].get("semantic_role"),
            ) != (
                parent["run_id"],
                parent["attempt"],
                self.parent_id,
                snapshot["revision"],
                request["request"]["task"]["role"],
            ):
                raise ValueError("child invocation does not match delegation ownership")
            if (
                self.store.runtime.db.execute(
                    "SELECT 1 FROM delegation_requests WHERE child_id=?", (child_id,)
                ).fetchone()
                or self.store.runtime.db.execute(
                    "SELECT 1 FROM agent_leases WHERE invocation_id=?", (child_id,)
                ).fetchone()
            ):
                raise ValueError("child invocation already bound or admitted")
            self.store.runtime.db.execute(
                "UPDATE delegation_requests SET child_id=?,status='prepared' WHERE id=?",
                (child_id, request_id),
            )
        return self.inspect(request_id)

    def authorize_preparation(self, request_id: str) -> None:
        """Recheck a queued request before provisioning or spending on certification."""
        request = self.inspect(request_id)
        if request["status"] not in {"pending", "prepared"}:
            raise ValueError("delegation request is " + request["status"])
        _, snapshot = self._admissible_parent()
        if request["request"]["policy_revision"] != snapshot["revision"]:
            raise ValueError("delegation authority is stale")
        if request["request"]["source_root"] != str(self.source_root):
            raise ValueError("delegation source root changed")
        self._requested_mode(request)
        self._paths(
            request["request"]["task"]["paths"],
            allow_missing=request["request"]["task"]["mode"] == "isolated-write",
        )

    def authorize_launch(self, request_id: str, child_id: str) -> None:
        """Called inside the common launch transaction, before slot/approval consumption."""
        request = self.inspect(request_id)
        if request["status"] != "prepared" or request["child_id"] != child_id:
            raise ValueError("delegation request is " + request["status"])
        _, snapshot = self._admissible_parent()
        if request["request"]["policy_revision"] != snapshot["revision"]:
            raise ValueError("delegation authority is stale")
        if request["request"]["source_root"] != str(self.source_root):
            raise ValueError("delegation source root changed")
        self._requested_mode(request)
        self._paths(
            request["request"]["task"]["paths"],
            allow_missing=request["request"]["task"]["mode"] == "isolated-write",
        )

    def publish_result(
        self, request_id: str, result: object, *, failed: bool = False
    ) -> dict[str, Any]:
        """Host collector publishes a bounded result after terminal accounting/reconciliation.

        This is not a worker tool. Child output remains untrusted builder assistance
        and never advances verification or independent review.
        """
        encoded = json.dumps(result, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > MAX_REQUEST_BYTES:
            raise ValueError("delegation result exceeds size limit")
        with self.store.runtime.transaction():
            request = self.inspect(request_id)
            if request["status"] in {"completed", "failed", "cancelled"}:
                if json.dumps(request["result"], sort_keys=True, allow_nan=False) != encoded:
                    raise ValueError("delegation result is immutable")
                return request
            lease = self.store.runtime.db.execute(
                "SELECT status FROM agent_leases WHERE invocation_id=? AND parent_id=? AND run_id=?",
                (request["child_id"], self.parent_id, request["run_id"]),
            ).fetchone()
            if lease is None or lease["status"] == "active":
                raise ValueError("child must be terminal and reconciled before publishing")
            status = (
                "cancelled"
                if request["status"] == "cancelling"
                else "failed"
                if failed
                else lease["status"]
            )
            if status not in {"completed", "failed", "cancelled"}:
                raise ValueError("child requires terminal reconciliation")
            self.store.runtime.db.execute(
                "UPDATE delegation_requests SET status=?,result=? WHERE id=?",
                (status, encoded, request_id),
            )
            self.store.runtime.audit(
                "run",
                request["run_id"],
                "delegation-result",
                {"request": request_id, "status": status},
            )
        return self.inspect(request_id)

    def cancel(self, request_id: str) -> dict[str, Any]:
        """Fence pending admission; executing children remain cancelling until reconciled."""
        with self.store.runtime.transaction():
            request = self.inspect(request_id)
            if request["status"] in {"completed", "failed", "cancelled"}:
                return request
            child = (
                self.store.runtime.invocation(request["child_id"]) if request["child_id"] else None
            )
            launched = (
                child is not None
                and self.store.find_effect(
                    request["run_id"], child["attempt"], child["id"], "agent-launch", "spawn"
                )
                is not None
            )
            status = "cancelling" if launched else "cancelled"
            self.store.runtime.db.execute(
                "UPDATE delegation_requests SET status=? WHERE id=?", (status, request_id)
            )
            if request["status"] != status:
                self.store.runtime.audit(
                    "run", request["run_id"], "delegation-cancel-requested", {"request": request_id}
                )
        return self.inspect(request_id)

    def _admissible_parent(self) -> tuple[dict[str, Any], dict[str, Any]]:
        parent, snapshot = self._parent_contract()
        lease = self.store.runtime.db.execute(
            "SELECT * FROM agent_leases WHERE invocation_id=?", (self.parent_id,)
        ).fetchone()
        if lease is None or lease["status"] != "active" or lease["parent_id"] is not None:
            raise ValueError("delegation requires an active root parent")
        return parent, snapshot

    def _parent_contract(self) -> tuple[dict[str, Any], dict[str, Any]]:
        parent = self.store.runtime.invocation(self.parent_id)
        if (
            parent is None
            or parent["metadata"].get("semantic_role") != "builder"
            or parent["metadata"].get("parent_id") is not None
        ):
            raise ValueError("delegation requires a root builder parent")
        run = self.store.run_by_id(parent["run_id"])
        if run is None or run.state in {
            "cancelled",
            "completed",
            "failed",
            "blocked",
            "suspended",
            "awaiting_human",
        }:
            raise ValueError("delegation owner is not active")
        project = self.store.runtime.settings("project", run.project)
        settings = self.store.runtime.effective(run.project, run.id)
        modes = {"disabled": 0, "read-only": 1, "isolated-write": 2}
        ceiling = project.get("delegation_mode", "disabled")
        mode = settings.get("delegation_mode", ceiling)
        if (
            not isinstance(ceiling, str)
            or not isinstance(mode, str)
            or mode not in modes
            or ceiling not in modes
        ):
            raise ValueError("invalid delegation mode")
        if modes[mode] == 0 or modes[mode] > modes[ceiling]:
            raise ValueError("delegation disabled or exceeds project capability")
        snapshot = self.store.runtime.policy(run.id)
        if snapshot is None or snapshot["revision"] != parent["metadata"].get("policy_revision"):
            raise ValueError("delegation authority is stale")
        authority.validate_integrity(snapshot)
        return parent, snapshot

    def _requested_mode(self, request: dict[str, Any]) -> None:
        run = self.store.run_by_id(request["run_id"])
        if request["request"]["task"]["mode"] == "isolated-write" and (
            run is None
            or self.store.runtime.effective(run.project, run.id).get("delegation_mode", "disabled")
            != "isolated-write"
        ):
            raise ValueError("isolated-write delegation is no longer enabled")

    def _writable_scope(self, paths: list[str], run_id: str) -> None:
        run = self.store.run_by_id(run_id)
        if (
            run is None
            or self.store.runtime.effective(run.project, run.id).get("delegation_mode", "disabled")
            != "isolated-write"
        ):
            raise ValueError("isolated-write delegation is not enabled")
        if not paths or any(
            name == "." or any(part in {".git", ".factory"} for part in PurePosixPath(name).parts)
            for name in paths
        ):
            raise ValueError("writable delegation requires bounded edit scopes")
        snapshot = self.store.runtime.policy(run_id) or {}
        protected = snapshot.get("files", {})
        if any(
            name == item or name.startswith(item + "/") or item.startswith(name + "/")
            for name in paths
            for item in protected
        ):
            raise ValueError("writable scope overlaps trusted authority")
        siblings = self.store.runtime.db.execute(
            "SELECT request FROM delegation_requests WHERE parent_id=? "
            "AND status NOT IN ('failed','cancelled')",
            (self.parent_id,),
        ).fetchall()
        prior = [
            name
            for row in siblings
            if json.loads(row["request"])["task"]["mode"] == "isolated-write"
            for name in json.loads(row["request"])["task"]["paths"]
        ]
        for name in paths:
            if any(
                name == other or name.startswith(other + "/") or other.startswith(name + "/")
                for other in prior
            ):
                raise ValueError("writable child scopes overlap")

    def _paths(self, paths: list[str], *, allow_missing: bool = False) -> None:
        parent = self.store.runtime.invocation(self.parent_id)
        mailbox = (
            self.store.find_effect(
                parent["run_id"], parent["attempt"], self.parent_id, "delegation-mailbox", "mounts"
            )
            if parent
            else None
        )
        cloned = bool(
            mailbox and json.loads(mailbox.external_id or "{}").get("spec", {}).get("clone")
        )
        if len(paths) > 128:
            raise ValueError("delegation scope exceeds path limit")
        for name in paths:
            relative = PurePosixPath(name)
            if (
                not name
                or "\x00" in name
                or "\\" in name
                or relative.is_absolute()
                or any(part in {"..", ".git", ".factory"} for part in relative.parts)
                or relative.as_posix() != name
            ):
                raise ValueError("delegation scope requires canonical relative paths")
            if cloned:
                # Host checkout contents are not the VM's private source. Existence and
                # file types are checked against the exported snapshot before paid work.
                continue
            current = self.source_root
            for component in relative.parts:
                current = current / component
                if current.is_symlink():
                    raise ValueError("delegation scope cannot traverse symlinks")
            if not current.resolve(strict=not allow_missing).is_relative_to(self.source_root):
                raise ValueError("delegation scope escapes source root")
