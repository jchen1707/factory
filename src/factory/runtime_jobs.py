"""Durable certification and agent scheduling, on the existing store connection."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.store import Store

SCHEMA = (
    "CREATE TABLE runtime_certifications (id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL REFERENCES runs(id), fingerprint TEXT NOT NULL UNIQUE, "
    "identity TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, lease_until REAL, "
    "evidence TEXT, failure TEXT)",
    "CREATE TABLE agent_leases (invocation_id TEXT PRIMARY KEY REFERENCES invocations(id), "
    "run_id TEXT NOT NULL REFERENCES runs(id), project TEXT NOT NULL, "
    "status TEXT NOT NULL, parent_id TEXT REFERENCES invocations(id))",
    "CREATE TABLE delegation_requests (id TEXT PRIMARY KEY, "
    "parent_id TEXT NOT NULL REFERENCES invocations(id), call_id TEXT NOT NULL, "
    "run_id TEXT NOT NULL REFERENCES runs(id), request TEXT NOT NULL, "
    "status TEXT NOT NULL, child_id TEXT REFERENCES invocations(id), result TEXT, "
    "UNIQUE(parent_id,call_id))",
)


class RuntimeJobs:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.runtime = store.runtime

    def certification(self, job_id: str) -> dict[str, Any] | None:
        row = self.runtime.db.execute(
            "SELECT * FROM runtime_certifications WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row) | {
            "identity": json.loads(row["identity"]),
            "evidence": json.loads(row["evidence"]) if row["evidence"] else None,
        }

    def request_certification(self, run_id: str, identity: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with self.runtime.transaction():
            row = self.runtime.db.execute(
                "SELECT id FROM runtime_certifications WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            job_id = row["id"] if row else uuid.uuid4().hex
            if row is None:
                self.runtime.db.execute(
                    "INSERT INTO runtime_certifications(id,run_id,fingerprint,identity,status) "
                    "VALUES (?,?,?,?, 'pending')",
                    (job_id, run_id, fingerprint, payload),
                )
                self.runtime.audit("run", run_id, "certification-requested", {"job_id": job_id})
        result = self.certification(job_id)
        if result is None:
            raise RuntimeError("certification record disappeared")
        return result

    def active_agents(self, project: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.runtime.db.execute(
                "SELECT * FROM agent_leases WHERE project=? AND status='active' ORDER BY invocation_id",
                (project,),
            )
        ]

    def admit_agent(self, invocation_id: str, *, limit: int, parent_id: str | None = None) -> bool:
        if type(limit) is not int or limit < 1:
            raise ValueError("agent limit must be a positive integer")
        with self.runtime.transaction():
            invocation = self.runtime.invocation(invocation_id)
            if invocation is None:
                raise ValueError("unknown invocation")
            run = self.store.run_by_id(invocation["run_id"])
            if run is None:
                raise ValueError("unknown run")
            old = self.runtime.db.execute(
                "SELECT * FROM agent_leases WHERE invocation_id=?", (invocation_id,)
            ).fetchone()
            if old:
                if old["parent_id"] != parent_id:
                    raise ValueError("agent ownership is immutable")
                return old["status"] == "active"
            if parent_id:
                parent = self.runtime.db.execute(
                    "SELECT * FROM agent_leases WHERE invocation_id=?", (parent_id,)
                ).fetchone()
                if parent is None or parent["run_id"] != run.id or parent["status"] != "active":
                    raise ValueError("child requires an active parent in the same run")
            if len(self.active_agents(run.project)) >= limit:
                return False
            self.runtime.db.execute(
                "INSERT INTO agent_leases VALUES (?,?,?,'active',?)",
                (invocation_id, run.id, run.project, parent_id),
            )
            self.runtime.audit("run", run.id, "agent-admitted", {"invocation": invocation_id})
            return True

    def finish_agent(self, invocation_id: str, *, status: str) -> None:
        if status not in {"completed", "failed", "cancelled", "suspended"}:
            raise ValueError("invalid terminal agent status")
        with self.runtime.transaction():
            if self.runtime.db.execute(
                "SELECT 1 FROM agent_leases WHERE parent_id=? AND status='active'", (invocation_id,)
            ).fetchone():
                raise ValueError("reconcile children before finalizing parent")
            row = self.runtime.db.execute(
                "SELECT * FROM agent_leases WHERE invocation_id=?", (invocation_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown agent lease")
            if row["status"] != "active":
                if row["status"] != status:
                    raise ValueError("terminal agent status is immutable")
                return
            self.runtime.db.execute(
                "UPDATE agent_leases SET status=? WHERE invocation_id=?", (status, invocation_id)
            )
            self.runtime.audit(
                "run",
                row["run_id"],
                "agent-finished",
                {"invocation": invocation_id, "status": status},
            )

    def claim_certification(self, job_id: str, *, now: float, duration: float) -> str | None:
        import math

        if not math.isfinite(now) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid certification lease duration")
        token = uuid.uuid4().hex
        with self.runtime.transaction():
            changed = self.runtime.db.execute(
                "UPDATE runtime_certifications SET status='checking',owner=?,lease_until=? "
                "WHERE id=? AND (status='pending' OR (status='checking' AND lease_until<=?))",
                (token, now + duration, job_id, now),
            ).rowcount
        return token if changed else None

    def finish_certification(
        self,
        job_id: str,
        token: str,
        *,
        now: float,
        evidence: dict[str, Any] | None = None,
        failure: str | None = None,
    ) -> None:
        if bool(evidence) == bool(failure):
            raise ValueError("supply verified evidence or a failure")
        with self.runtime.transaction():
            changed = self.runtime.db.execute(
                "UPDATE runtime_certifications SET status=?,evidence=?,failure=?,owner=NULL,lease_until=NULL "
                "WHERE id=? AND status='checking' AND owner=? AND lease_until>?",
                (
                    "failed" if failure else "passed",
                    json.dumps(evidence) if evidence else None,
                    failure,
                    job_id,
                    token,
                    now,
                ),
            ).rowcount
            if not changed:
                raise ValueError("certification lease is absent, expired or superseded")
