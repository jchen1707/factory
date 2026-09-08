"""Keep a child's certification under its durable request's cancellation boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.agent.app_server_worker import read_mailbox
from factory.agent_launches import AgentLaunches, DetachedExecution
from factory.execution import ProjectQueued
from factory.sandbox.base import RunHandle
from factory.store import Store


def bindings(store: Store, run_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store.runtime.db.execute(
            "SELECT c.id,c.identity,d.id AS request_id,d.parent_id,d.status AS child_status "
            "FROM runtime_certifications c JOIN effects e ON e.run_id=c.run_id "
            "AND e.system='child-execution' AND e.key='prepare' AND e.status='confirmed' "
            "AND json_extract(e.external_id,'$.sandbox')=json_extract(c.identity,'$.sandbox') "
            "JOIN delegation_requests d ON d.id=e.step AND d.run_id=c.run_id "
            "AND d.parent_id=json_extract(e.external_id,'$.parent_id') WHERE c.run_id=?",
            (run_id,),
        )
    ]


def authorize(store: Store, invocation: dict[str, Any], handle: RunHandle) -> None:
    """Called inside paid admission, closing the cancellation versus launch race."""
    job_id = invocation["metadata"].get("certification_job")
    if invocation["role"] != "certification" or job_id is None:
        return
    job = store.runtime.db.execute(
        "SELECT run_id,status,identity FROM runtime_certifications WHERE id=?", (job_id,)
    ).fetchone()
    if (
        job is None
        or job["run_id"] != handle.run_id
        or job["status"] not in {"pending", "checking"}
        or json.loads(job["identity"])["sandbox"] != handle.sandbox
    ):
        raise ProjectQueued("certification is no longer launchable")
    from factory.delegation import DelegationBroker

    for binding in bindings(store, handle.run_id):
        if binding["id"] == job_id:
            request = store.runtime.db.execute(
                "SELECT request FROM delegation_requests WHERE id=?", (binding["request_id"],)
            ).fetchone()
            try:
                DelegationBroker(
                    store,
                    binding["parent_id"],
                    Path(json.loads(request["request"])["source_root"]),
                ).authorize_preparation(binding["request_id"])
            except ValueError as exc:
                raise ProjectQueued("child certification owner is no longer active") from exc


def active_for_parent(store: Store, run_id: str, parent_id: str) -> bool:
    jobs = {b["id"] for b in bindings(store, run_id) if b["parent_id"] == parent_id}
    return any(
        json.loads(row["metadata"]).get("certification_job") in jobs
        for row in store.runtime.db.execute(
            "SELECT i.metadata FROM invocations i JOIN agent_leases a ON a.invocation_id=i.id "
            "WHERE i.run_id=? AND i.role='certification' AND a.status='active'",
            (run_id,),
        )
    )


def reconcile(store: Store, sandbox: DetachedExecution, run_id: str) -> None:
    launches = AgentLaunches(store, sandbox)
    for binding in bindings(store, run_id):
        if binding["child_status"] not in {"cancelled", "cancelling"}:
            continue
        identity = json.loads(binding["identity"])
        with store.runtime.transaction():
            changed = store.runtime.db.execute(
                "UPDATE runtime_certifications SET status='failed',failure=?,owner=NULL,lease_until=NULL "
                "WHERE id=? AND status IN ('pending','checking')",
                ("child request cancelled", binding["id"]),
            ).rowcount
            if changed:
                store.runtime.audit(
                    "run",
                    run_id,
                    "child-certification-cancelled",
                    {"job": binding["id"], "request": binding["request_id"]},
                )
        for row in store.runtime.db.execute(
            "SELECT i.id,i.attempt,i.metadata FROM invocations i "
            "JOIN agent_leases a ON a.invocation_id=i.id "
            "WHERE i.run_id=? AND i.role='certification' AND a.status='active'",
            (run_id,),
        ).fetchall():
            if json.loads(row["metadata"]).get("certification_job") != binding["id"]:
                continue
            handle = launches.handle(row["id"])
            if (
                handle.sandbox != identity["sandbox"]
                or (handle.attempt_dir / handle.exit_name).exists()
            ):
                continue
            owner = (run_id, row["attempt"], row["id"], "child-certification-cancel", "signal")
            if store.find_effect(*owner) is not None:
                continue
            generation = getattr(sandbox, "generation", None)
            execute = getattr(sandbox, "exec_sync", None)
            signal = getattr(sandbox, "kill_group", None)
            if not callable(generation) or not callable(execute) or not callable(signal):
                continue
            if generation(handle.sandbox) != identity["generation"]:
                continue
            try:
                pgid = int(read_mailbox(handle.attempt_dir, handle.pgid_name, limit=32).strip())
            except (OSError, ValueError):
                continue
            processes = execute(handle.sandbox, ["ps", "-eo", "pid,pgid,args"])
            expected = str(handle.attempt_dir / "pgid-body.sh")
            if (
                pgid <= 1
                or not processes.ok
                or not any(
                    len(parts := line.split(None, 2)) == 3
                    and parts[0] == parts[1] == str(pgid)
                    and expected in parts[2]
                    for line in processes.stdout.splitlines()[1:]
                )
            ):
                continue
            if generation(handle.sandbox) != identity["generation"]:
                continue
            contract = json.dumps(
                {"sandbox": handle.sandbox, "generation": identity["generation"], "pgid": pgid}
            )
            with store.runtime.transaction():
                if store.find_effect(*owner) is not None:
                    continue
                store.intend_effect(*owner)
            signal(handle.sandbox, pgid)
            store.confirm_effect(*owner, contract)
