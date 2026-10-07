from __future__ import annotations

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

    def schedule_agent(
        self,
        invocation_id: str,
        *,
        usd_limit: float,
        max_attempts: int,
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
                "SELECT status FROM agent_leases WHERE invocation_id=?", (invocation_id,)
            ).fetchone()
            if existing:
                return existing["status"] == "active"
            project_settings = self.runtime.settings("project", run.project)
            settings = self.runtime.effective(run.project, run.id)
            ceiling = project_settings.get("max_active_agents", 8)
            limit = settings.get("max_active_agents", ceiling)
            if type(ceiling) is not int or ceiling < 1 or type(limit) is not int or limit < 1:
                raise ValueError("max_active_agents must be a positive integer")
            if limit > ceiling:
                raise ValueError("max_active_agents exceeds the project ceiling")
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
            if len(self.active_agents(run.project)) + 1 > limit:
                return False
            self.runtime.db.execute(
                "INSERT INTO agent_leases VALUES (?,?,?,'active',NULL)",
                (invocation_id, run.id, run.project),
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
