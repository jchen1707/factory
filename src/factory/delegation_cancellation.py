"""Drain cancelled children through recorded launch ownership, never tool-supplied PIDs."""

from __future__ import annotations

import json
from pathlib import Path

from factory.agent.app_server_worker import read_mailbox
from factory.agent_launches import AgentLaunches, DetachedExecution
from factory.delegation import DelegationBroker
from factory.machine import State, is_human_held
from factory.store import Store


def reconcile(store: Store, sandbox: DetachedExecution, run_id: str) -> None:
    """Signal once; keep ambiguous effects and active leases for explicit recovery.

    A crash between intent and signal cannot safely become another signal to a
    potentially reused process group. Terminal accounting, not signalling success,
    is the authority for releasing capacity and publishing cancellation.
    """
    run = store.run_by_id(run_id)
    if run is None:
        return
    if is_human_held(run.state) or run.state in {State.COMPLETED, State.CANCELLED}:
        for request in store.runtime.db.execute(
            "SELECT id,parent_id,request FROM delegation_requests WHERE run_id=? "
            "AND status NOT IN ('completed','failed','cancelled')",
            (run_id,),
        ).fetchall():
            DelegationBroker(
                store, request["parent_id"], Path(json.loads(request["request"])["source_root"])
            ).cancel(request["id"])
    from factory import child_certifications

    child_certifications.reconcile(store, sandbox, run_id)
    launches = AgentLaunches(store, sandbox)
    requests = store.runtime.db.execute(
        "SELECT id,parent_id,child_id,request FROM delegation_requests "
        "WHERE run_id=? AND status='cancelling'",
        (run_id,),
    ).fetchall()
    for request in requests:
        child = store.runtime.invocation(request["child_id"])
        if child is None or child["run_id"] != run_id:
            raise ValueError("cancelled child ownership missing")
        lease = store.runtime.db.execute(
            "SELECT status FROM agent_leases WHERE invocation_id=? AND parent_id=? AND run_id=?",
            (child["id"], request["parent_id"], run_id),
        ).fetchone()
        if lease is None:
            # A recorded launch can be ambiguous; never invent terminal evidence.
            continue
        if lease["status"] != "active":
            DelegationBroker(
                store, request["parent_id"], Path(json.loads(request["request"])["source_root"])
            ).publish_result(request["id"], None)
            continue
        handle = launches.handle(child["id"])
        if (handle.attempt_dir / handle.exit_name).exists():
            continue
        try:
            pgid = int(read_mailbox(handle.attempt_dir, handle.pgid_name, limit=32).strip())
        except (OSError, ValueError):
            continue
        if pgid <= 0:
            continue
        signal = getattr(sandbox, "kill_group", None)
        if not callable(signal):
            raise ValueError("sandbox adapter cannot signal cancelled children")
        owner = (run_id, child["attempt"], child["id"], "delegation-cancel", "signal")
        with store.runtime.transaction():
            if store.find_effect(*owner) is not None:
                continue
            store.intend_effect(*owner)
            contract = json.dumps({"sandbox": handle.sandbox, "pgid": pgid}, sort_keys=True)
            store.runtime.db.execute(
                "UPDATE effects SET external_id=? WHERE run_id=? AND attempt=? AND step=? "
                "AND system='delegation-cancel' AND key='signal'",
                (contract, run_id, child["attempt"], child["id"]),
            )
        signal(handle.sandbox, pgid)
        store.confirm_effect(*owner, contract)
