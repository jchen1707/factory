"""Publish bounded untrusted child assistance only after terminal accounting."""

from __future__ import annotations

import json
from pathlib import Path

from factory.agent.app_server_worker import read_mailbox
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.delegation import MAX_REQUEST_BYTES, DelegationBroker
from factory.store import Store


def collect(store: Store, run_id: str) -> None:
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
        except (OSError, ValueError, SchemaInvalid):
            result = None
            failed = True
        DelegationBroker(
            store, request["parent_id"], Path(json.loads(request["request"])["source_root"])
        ).publish_result(request["id"], result, failed=failed)
