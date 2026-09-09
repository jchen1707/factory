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


# Remove fingerprint-only uniqueness without rewriting any retained job fields.
RETRY_SCHEMA = (
    "CREATE TABLE runtime_certifications_v7 (id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL REFERENCES runs(id), fingerprint TEXT NOT NULL, "
    "identity TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, lease_until REAL, "
    "evidence TEXT, failure TEXT, sequence INTEGER NOT NULL DEFAULT 0, "
    "retry_of TEXT UNIQUE REFERENCES runtime_certifications_v7(id), "
    "UNIQUE(fingerprint,sequence))",
    "INSERT INTO runtime_certifications_v7 "
    "(id,run_id,fingerprint,identity,status,owner,lease_until,evidence,failure) "
    "SELECT id,run_id,fingerprint,identity,status,owner,lease_until,evidence,failure "
    "FROM runtime_certifications",
    "DROP TABLE runtime_certifications",
    "ALTER TABLE runtime_certifications_v7 RENAME TO runtime_certifications",
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
                "SELECT id FROM runtime_certifications WHERE fingerprint=? ORDER BY sequence DESC LIMIT 1",
                (fingerprint,),
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

    def retry_certification(
        self, run_id: str, job_id: str, identity: dict[str, Any], *, reason: str
    ) -> dict[str, Any]:
        """Explicit operator request, idempotent by failed predecessor; never launches.

        Ordinary request/ensure keeps returning a failure until this operation creates
        its successor. A repeated request for the same predecessor cannot buy another
        attempt, even after that successor fails. Every further retry names that failure.
        """
        from factory.machine import Blocked

        if not reason.strip() or len(reason) > 1000:
            raise ValueError("retry requires a bounded operator reason")
        with self.runtime.transaction():
            previous = self.certification(job_id)
            if previous is None or previous["run_id"] != run_id:
                raise Blocked("certification-retry-owner", job_id)
            if previous["identity"] != identity:
                raise Blocked("certification-stale", job_id)
            if previous["status"] != "failed":
                raise Blocked("certification-retry-not-failed", job_id)
            existing = self.runtime.db.execute(
                "SELECT id FROM runtime_certifications WHERE retry_of=?", (job_id,)
            ).fetchone()
            if existing is not None:
                result = self.certification(existing["id"])
                if result is None:
                    raise RuntimeError("certification retry disappeared")
                return result
            run = self.store.run_by_id(run_id)
            if run is None or run.state not in {"blocked", "suspended", "awaiting_human"}:
                raise Blocked("certification-retry-needs-paused-run", run_id)
            if self.runtime.db.execute(
                "SELECT 1 FROM agent_leases WHERE run_id=? AND status='active'", (run_id,)
            ).fetchone():
                raise Blocked("certification-retry-active-agent", run_id)
            successor = uuid.uuid4().hex
            self.runtime.db.execute(
                "INSERT INTO runtime_certifications "
                "(id,run_id,fingerprint,identity,status,sequence,retry_of) "
                "VALUES (?,?,?,?, 'pending',?,?)",
                (
                    successor,
                    run_id,
                    previous["fingerprint"],
                    json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False),
                    previous["sequence"] + 1,
                    job_id,
                ),
            )
            self.runtime.audit(
                "run",
                run_id,
                "certification-retry-requested",
                {
                    "previous": job_id,
                    "job_id": successor,
                    "reason": reason,
                },
            )
        result = self.certification(successor)
        if result is None:
            raise RuntimeError("certification retry disappeared")
        return result

    def active_agents(self, project: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.runtime.db.execute(
                "SELECT * FROM agent_leases WHERE project=? AND status='active' ORDER BY invocation_id",
                (project,),
            )
        ]

    def finish_agent(self, invocation_id: str, *, status: str) -> None:
        if status not in {"completed", "failed", "cancelled", "suspended"}:
            raise ValueError("invalid terminal agent status")
        with self.runtime.transaction():
            if self.runtime.db.execute(
                "SELECT 1 FROM agent_leases WHERE parent_id=? AND status='active'", (invocation_id,)
            ).fetchone():
                raise ValueError("reconcile children before finalizing parent")
            if self.runtime.db.execute(
                "SELECT 1 FROM delegation_requests WHERE parent_id=? "
                "AND status NOT IN ('completed','failed','cancelled')",
                (invocation_id,),
            ).fetchone():
                raise ValueError("reconcile delegation requests before finalizing parent")
            row = self.runtime.db.execute(
                "SELECT * FROM agent_leases WHERE invocation_id=?", (invocation_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown agent lease")
            from factory.child_certifications import active_for_parent

            if active_for_parent(self.store, row["run_id"], invocation_id):
                raise ValueError("reconcile child certification before finalizing parent")
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

    def schedule_agent(
        self,
        invocation_id: str,
        *,
        usd_limit: float,
        max_attempts: int,
        parent_id: str | None = None,
    ) -> bool:
        """Reserve one invocation atomically; an existing reservation is not a new launch.

        The controller must reconcile a reserved invocation after a crash, never spawn it
        again merely because this operation returns True. External launch stays outside
        the transaction. Limits are supplied by trusted controller configuration.
        """
        from factory.execution import AgentApprovalRequired
        from factory.machine import Blocked

        with self.runtime.transaction():
            invocation = self.runtime.invocation(invocation_id)
            if invocation is None:
                raise ValueError("unknown invocation")
            run = self.store.run_by_id(invocation["run_id"])
            if run is None:
                raise ValueError("unknown run")
            existing = self.runtime.db.execute(
                "SELECT parent_id,status FROM agent_leases WHERE invocation_id=?", (invocation_id,)
            ).fetchone()
            if existing:
                if existing["parent_id"] != parent_id:
                    raise ValueError("agent ownership is immutable")
                return existing["status"] == "active"
            if invocation["role"] == "certification" and run.state in {
                "cancelled",
                "suspended",
                "blocked",
                "failed",
                "completed",
                "awaiting_human",
            }:
                from factory.execution import ProjectQueued

                raise ProjectQueued("certification owner is not active")
            project_settings = self.runtime.settings("project", run.project)
            settings = self.runtime.effective(run.project, run.id)
            limits = {}
            for field, default in (
                ("max_active_agents", 8),
                ("max_children_per_parent", 2),
                ("max_delegation_depth", 1),
            ):
                ceiling = project_settings.get(field, default)
                value = settings.get(field, ceiling)
                if type(ceiling) is not int or ceiling < 1 or type(value) is not int or value < 1:
                    raise ValueError(f"{field} must be a positive integer")
                if value > ceiling:
                    raise ValueError(f"{field} exceeds the project ceiling")
                limits[field] = value
            if (
                limits["max_delegation_depth"] != 1
                or project_settings.get("max_delegation_depth", 1) != 1
            ):
                raise ValueError("only delegation depth one is supported")
            modes = {"disabled": 0, "read-only": 1, "isolated-write": 2}
            project_mode = project_settings.get("delegation_mode", "disabled")
            mode = settings.get("delegation_mode", project_mode)
            if (
                not isinstance(project_mode, str)
                or not isinstance(mode, str)
                or project_mode not in modes
                or mode not in modes
            ):
                raise ValueError("invalid delegation mode")
            if modes[mode] > modes[project_mode]:
                raise ValueError("delegation mode exceeds the project capability")
            if parent_id is not None:
                if mode == "disabled":
                    raise ValueError("delegation is disabled")
                parent = self.runtime.db.execute(
                    "SELECT * FROM agent_leases WHERE invocation_id=?", (parent_id,)
                ).fetchone()
                if parent is None or parent["run_id"] != run.id or parent["status"] != "active":
                    raise ValueError("child requires an active parent in the same run")
                if parent["parent_id"] is not None:
                    raise ValueError("delegation depth exceeded")
                if (
                    self.runtime.db.execute(
                        "SELECT count(*) FROM agent_leases WHERE parent_id=? AND status='active'",
                        (parent_id,),
                    ).fetchone()[0]
                    >= limits["max_children_per_parent"]
                ):
                    return False
            approval_mode = settings.get("mode", "automatic")
            if approval_mode not in ("automatic", "approval"):
                raise ValueError("invalid approval mode")
            approval = approval_mode == "approval"
            if (
                approval
                and self.runtime.settings("run", run.id).get("approved_invocation") != invocation_id
            ):
                raise AgentApprovalRequired(invocation_id)
            if self.store.known_spend(run.id) >= usd_limit:
                raise Blocked(
                    "budget-exceeded", "API-equivalent estimate reached the configured ceiling"
                )
            if invocation["attempt"] > max_attempts:
                raise Blocked("attempts-exhausted", "The lifetime attempt limit is exhausted")
            if parent_id is not None:
                parent_invocation = self.runtime.invocation(parent_id)
                if (
                    parent_invocation is None
                    or invocation["attempt"] != parent_invocation["attempt"]
                ):
                    raise ValueError("child must retain its parent's attempt")
            active = self.active_agents(run.project)
            candidate = {
                "invocation_id": invocation_id,
                "run_id": run.id,
                "parent_id": parent_id,
            }
            if (
                len(active) + 1 + self._progress_reservations(run.project, [*active, candidate])
                > limits["max_active_agents"]
            ):
                return False
            self.runtime.db.execute(
                "INSERT INTO agent_leases VALUES (?,?,?,'active',?)",
                (invocation_id, run.id, run.project, parent_id),
            )
            self.runtime.audit("run", run.id, "agent-admitted", {"invocation": invocation_id})
            if approval:
                self.runtime.db.execute(
                    "UPDATE operator_settings SET settings=json_set(settings,'$.approved_invocation',NULL), "
                    "revision=revision+1 WHERE scope='run' AND owner=?",
                    (run.id,),
                )
                self.runtime.audit(
                    "run", run.id, "approval-consumed", {"invocation": invocation_id}
                )
            return True

    def _progress_reservations(self, project: str, agents: list[dict[str, Any]]) -> int:
        """Keep a first-child lane for every delegating builder, atomically with admission.

        Certification in the same run may occupy that lane while preparing the child.
        Further children and unrelated invocations use spare capacity only. A parent
        waiting on a child therefore cannot have its final progress slot taken by a
        new parent, a reviewer, or another parent's second child. Reservations are
        derived from durable leases; controller restart does not lose them.
        """
        reserved = 0
        for agent in agents:
            if agent["parent_id"] is not None:
                continue
            invocation = self.runtime.invocation(agent["invocation_id"])
            if invocation is None:
                raise ValueError("agent lease has no invocation")
            role = invocation["metadata"].get("semantic_role", invocation["role"])
            settings = self.runtime.effective(project, agent["run_id"])
            if role != "builder" or settings.get("delegation_mode", "disabled") == "disabled":
                continue
            occupied = any(other["parent_id"] == agent["invocation_id"] for other in agents)
            if not occupied:
                for other in agents:
                    if other["run_id"] != agent["run_id"]:
                        continue
                    other_invocation = self.runtime.invocation(other["invocation_id"])
                    if other_invocation is not None and other_invocation["role"] == "certification":
                        occupied = True
                        break
            reserved += not occupied
        return reserved

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
