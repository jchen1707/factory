"""Publish bounded untrusted child assistance only after terminal accounting."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast

from factory import child_workspaces
from factory.agent.app_server_worker import read_mailbox
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.agent_launches import AgentLaunches, DetachedExecution
from factory.delegation import MAX_REQUEST_BYTES, DelegationBroker
from factory.sandbox.child_source import SourceSandbox, capture
from factory.store import Store


def collect(store: Store, run_id: str, sandbox: DetachedExecution | None = None) -> None:
    requests = store.runtime.db.execute(
        "SELECT d.* FROM delegation_requests d JOIN agent_leases a ON a.invocation_id=d.child_id "
        "AND a.parent_id=d.parent_id AND a.run_id=d.run_id "
        "WHERE d.run_id=? AND d.status='prepared' AND a.status IN ('completed','failed')",
        (run_id,),
    ).fetchall()
    for request in requests:
        invocation = store.runtime.invocation(request["child_id"])
        if invocation is None or "child_result_schema" not in invocation["metadata"]:
            continue
        result = None
        failed = False
        try:
            path = Path(invocation["metadata"]["child_result"])
            result = json.loads(read_mailbox(path.parent, path.name, limit=MAX_REQUEST_BYTES))
            validate_against_schema(result, invocation["metadata"]["child_result_schema"])
            if (
                len(json.dumps(result, sort_keys=True, allow_nan=False).encode())
                > MAX_REQUEST_BYTES
            ):
                raise ValueError("canonical result exceeds size limit")
            if invocation["metadata"].get("child_mode") == "isolated-write":
                task = json.loads(request["request"])["task"]
                owner = (
                    run_id,
                    invocation["attempt"],
                    request["id"],
                    "child-workspace",
                    "artifact",
                )
                retained = store.find_effect(*owner)
                if retained and retained.status == "confirmed":
                    encoded = retained.external_id or "{}"
                else:
                    if sandbox is None or not callable(getattr(sandbox, "exec_sync", None)):
                        raise ValueError("writable child requires its recorded sandbox")
                    handle = AgentLaunches(store, sandbox).handle(invocation["id"])
                    workspace = invocation["metadata"]["child_workspace"]
                    if handle.workdir != workspace["path"]:
                        raise ValueError("child source does not match recorded launch")
                    exported = capture(
                        cast(SourceSandbox, sandbox), handle.sandbox, Path(handle.workdir)
                    )
                    artifact = child_workspaces.artifact(workspace, task["paths"], exported)
                    encoded = json.dumps(artifact, sort_keys=True)
                    store.intend_effect(*owner)
                    store.confirm_effect(*owner, encoded)
                result["artifact"] = {
                    "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                    "status": "awaiting-parent-drain",
                }
        except (OSError, ValueError, SchemaInvalid, subprocess.SubprocessError):
            result = None
            failed = True
        DelegationBroker(
            store, request["parent_id"], Path(json.loads(request["request"])["source_root"])
        ).publish_result(request["id"], result, failed=failed)
