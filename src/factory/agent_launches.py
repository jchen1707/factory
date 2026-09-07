"""Reserve paid execution and record launch intent before crossing the sandbox boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from factory.execution import ProjectQueued
from factory.policy import assert_factory_sandbox
from factory.runtime_jobs import RuntimeJobs
from factory.sandbox.base import RunHandle, RunStatus
from factory.store import Store


class DetachedExecution(Protocol):
    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None: ...

    def poll(self, handle: RunHandle) -> RunStatus: ...


class AgentLaunches:
    """Host-owned launch intents never expire into permission to launch again.

    A crash before spawn and a crash after spawn but before acknowledgement are
    indistinguishable without external evidence. Both retain capacity. Recovery must
    observe the existing execution; it cannot retry an ambiguous paid effect.
    """

    def __init__(self, store: Store, sandbox: DetachedExecution) -> None:
        self.store = store
        self.sandbox = sandbox
        self.jobs = RuntimeJobs(store)

    def start(
        self,
        invocation_id: str,
        handle: RunHandle,
        script: str,
        env: Mapping[str, str],
        *,
        usd_limit: float,
        max_attempts: int,
        parent_id: str | None = None,
    ) -> bool:
        """True only for the controller that performs the external launch.

        False means an existing intent must be observed, including an uncertain or
        terminal intent. It never means queued work should get a new invocation ID.
        """
        if self.store.runtime.db.in_transaction:
            raise ValueError("paid launch cannot run inside an outer transaction")
        assert_factory_sandbox(handle.sandbox)
        invocation = self.store.runtime.invocation(invocation_id)
        if invocation is None or (invocation["run_id"], invocation["attempt"]) != (
            handle.run_id,
            handle.attempt,
        ):
            raise ValueError("launch handle must belong to the invocation")
        contract = json.dumps(
            {
                "parent_id": parent_id,
                "handle": asdict(handle) | {"attempt_dir": str(handle.attempt_dir)},
                "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
                "env_sha256": hashlib.sha256(
                    json.dumps(dict(env), sort_keys=True).encode()
                ).hexdigest(),
            },
            sort_keys=True,
        )
        with self.store.runtime.transaction():
            preparation = self.store.find_effect(
                handle.run_id, handle.attempt, invocation_id, "agent-preparation", "launch"
            )
            if preparation is not None and preparation.status == "cancelled":
                from factory.machine import Blocked

                raise Blocked("launch-cancelled", invocation_id)
            prior = self.store.find_effect(
                handle.run_id, handle.attempt, invocation_id, "agent-launch", "spawn"
            )
            if prior is not None:
                if prior.external_id != contract:
                    raise ValueError("launch contract is immutable")
                return False
            if self.store.runtime.db.execute(
                "SELECT 1 FROM agent_leases WHERE invocation_id=?", (invocation_id,)
            ).fetchone():
                raise ValueError("reservation has no launch intent; reconcile before launching")
            # Certification mutates its own canary and interrupts a measured worker.
            # Serialize it with every other paid invocation in that VM, across projects.
            # This admission check and the new launch intent share the same transaction.
            for active in self.store.runtime.db.execute(
                "SELECT i.id,i.run_id,i.attempt,i.role FROM agent_leases a "
                "JOIN invocations i ON i.id=a.invocation_id WHERE a.status='active'"
            ):
                if invocation["role"] != "certification" and active["role"] != "certification":
                    continue
                effect = self.store.find_effect(
                    active["run_id"], active["attempt"], active["id"], "agent-launch", "spawn"
                )
                if effect is None or effect.external_id is None:
                    raise ProjectQueued(
                        "waiting for sandbox certification ownership reconciliation"
                    )
                occupied = json.loads(effect.external_id)["handle"]["sandbox"]
                if occupied == handle.sandbox:
                    raise ProjectQueued("waiting for exclusive sandbox certification access")
            for effect in self.store.runtime.db.execute(
                "SELECT external_id FROM effects WHERE system='agent-launch' AND key='spawn'"
            ):
                existing = json.loads(effect["external_id"])["handle"]
                if Path(existing["attempt_dir"]).resolve() == handle.attempt_dir.resolve():
                    raise ValueError("launch evidence directory already belongs to an invocation")
            if any(
                (handle.attempt_dir / name).exists()
                for name in (handle.exit_name, handle.pgid_name, "heartbeat", "sbx-exec.pid")
            ):
                raise ValueError("launch directory contains existing execution evidence")
            if not self.jobs.schedule_agent(
                invocation_id,
                usd_limit=usd_limit,
                max_attempts=max_attempts,
                parent_id=parent_id,
            ):
                raise ProjectQueued("waiting for an agent slot")
            self.store.intend_effect(
                handle.run_id, handle.attempt, invocation_id, "agent-launch", "spawn"
            )
            self.store.runtime.db.execute(
                "UPDATE effects SET external_id=? WHERE run_id=? AND attempt=? "
                "AND step=? AND system='agent-launch' AND key='spawn'",
                (contract, handle.run_id, handle.attempt, invocation_id),
            )
        # No transaction spans an adapter call. An exception leaves the intent intact:
        # the adapter may have spawned successfully before losing its acknowledgement.
        self.sandbox.exec_detached(handle, script, env)
        self.store.confirm_effect(
            handle.run_id, handle.attempt, invocation_id, "agent-launch", "spawn", contract
        )
        return True

    def handle(self, invocation_id: str) -> RunHandle:
        """Recover the controller-recorded handle, never a caller-supplied evidence path."""
        invocation = self.store.runtime.invocation(invocation_id)
        if invocation is None:
            raise ValueError("unknown invocation")
        effect = self.store.find_effect(
            invocation["run_id"], invocation["attempt"], invocation_id, "agent-launch", "spawn"
        )
        if effect is None or effect.external_id is None:
            raise ValueError("invocation has no launch intent")
        payload = json.loads(effect.external_id)["handle"]
        payload["attempt_dir"] = Path(payload["attempt_dir"])
        return RunHandle(**payload)

    def observe(self, invocation_id: str) -> RunStatus:
        """Observation does not retry a spawn or free an ambiguous reservation."""
        return self.sandbox.poll(self.handle(invocation_id))

    def reconcile(self, invocation_id: str, *, collect: Callable[[], None]) -> bool:
        """Retain terminal accounting before releasing a confirmed exited invocation.

        The caller's collector must be idempotent and keep missing usage visibly
        incomplete. Orphaned holders require targeted recovery, not automatic release.
        """
        handle = self.handle(invocation_id)
        if not (handle.attempt_dir / handle.exit_name).exists():
            return False
        code = int((handle.attempt_dir / handle.exit_name).read_text().strip())
        collect()
        self.jobs.finish_agent(invocation_id, status="completed" if code == 0 else "failed")
        return True
