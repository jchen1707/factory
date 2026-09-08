"""Durable invocation evidence and operator controls, on the store's connection."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SCHEMA = (
    "CREATE TABLE invocations (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), "
    "attempt INTEGER NOT NULL, role TEXT NOT NULL, metadata TEXT NOT NULL, telemetry TEXT, "
    "sequence INTEGER NOT NULL DEFAULT -1, started_at REAL NOT NULL, updated_at REAL NOT NULL)",
    "CREATE INDEX invocations_by_run ON invocations(run_id)",
    "CREATE TABLE operator_settings (scope TEXT NOT NULL, owner TEXT NOT NULL, "
    "settings TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(scope, owner))",
    "CREATE TABLE operator_events (id INTEGER PRIMARY KEY, scope TEXT NOT NULL, "
    "owner TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL, at REAL NOT NULL)",
    "CREATE TABLE policy_snapshots (run_id TEXT NOT NULL REFERENCES runs(id), revision INTEGER NOT NULL, "
    "payload TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY(run_id, revision))",
    "CREATE TABLE project_slots (run_id TEXT PRIMARY KEY REFERENCES runs(id), project TEXT NOT NULL)",
    "CREATE TABLE failure_episodes (run_id TEXT NOT NULL REFERENCES runs(id), fingerprint TEXT NOT NULL, "
    "repairs INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, PRIMARY KEY(run_id, fingerprint))",
)


class RuntimeState:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Nested reservations belong to the outer launch-intent transaction. A
        # savepoint may roll back its work, but must never commit the outer intent.
        savepoint = f"runtime_{uuid.uuid4().hex}" if self.db.in_transaction else None
        self.db.execute(f"SAVEPOINT {savepoint}" if savepoint else "BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute(f"ROLLBACK TO {savepoint}" if savepoint else "ROLLBACK")
            if savepoint:
                self.db.execute(f"RELEASE {savepoint}")
            raise
        self.db.execute(f"RELEASE {savepoint}" if savepoint else "COMMIT")

    def settings(self, scope: str, owner: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT settings FROM operator_settings WHERE scope=? AND owner=?", (scope, owner)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def configure(self, scope: str, owner: str, changes: dict[str, Any]) -> None:
        if scope not in {"project", "run"}:
            raise ValueError("settings scope must be project or run")
        with self.transaction():
            settings = self.settings(scope, owner)
            settings.update(changes)
            for field in (
                "delegation_mode",
                "max_active_agents",
                "max_children_per_parent",
                "max_delegation_depth",
            ):
                if field in changes and changes[field] is None:
                    settings.pop(field, None)
            self.db.execute(
                "INSERT INTO operator_settings VALUES (?,?,?,1) "
                "ON CONFLICT(scope,owner) DO UPDATE SET settings=excluded.settings, "
                "revision=operator_settings.revision+1",
                (scope, owner, json.dumps(settings)),
            )
            self.audit(scope, owner, "configure", changes)

    def effective(self, project: str, run: str) -> dict[str, Any]:
        project_settings = self.settings("project", project)
        settings = project_settings | self.settings("run", run)
        # Project reductions drain already admitted work; subsequent admissions use
        # the lower ceiling without rewriting the operator's retained run choices.
        for field, default in (
            ("max_active_agents", 8),
            ("max_children_per_parent", 2),
            ("max_delegation_depth", 1),
        ):
            ceiling = project_settings.get(field, default)
            value = settings.get(field, ceiling)
            if (
                type(ceiling) is int
                and ceiling > 0
                and type(value) is int
                and value > 0
                and field in settings
                and (field != "max_delegation_depth" or value == ceiling == 1)
            ):
                settings[field] = min(value, ceiling)
        modes = {"disabled": 0, "read-only": 1, "isolated-write": 2}
        ceiling = project_settings.get("delegation_mode", "disabled")
        value = settings.get("delegation_mode", ceiling)
        if (
            isinstance(ceiling, str)
            and isinstance(value, str)
            and ceiling in modes
            and value in modes
            and modes[value] > modes[ceiling]
        ):
            settings["delegation_mode"] = ceiling
        return settings

    def audit(self, scope: str, owner: str, action: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO operator_events(scope,owner,action,payload,at) VALUES (?,?,?,?,?)",
            (scope, owner, action, json.dumps(payload), time.time()),
        )

    def invocation(self, invocation_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM invocations WHERE id=?", (invocation_id,)).fetchone()
        if row is None:
            return None
        return dict(row) | {
            "metadata": json.loads(row["metadata"]),
            "telemetry": json.loads(row["telemetry"]) if row["telemetry"] else None,
        }

    def invocations(self, run_id: str) -> list[dict[str, Any]]:
        ids = self.db.execute(
            "SELECT id FROM invocations WHERE run_id=? ORDER BY started_at,id", (run_id,)
        ).fetchall()
        return [item for row in ids if (item := self.invocation(row[0])) is not None]

    def start_invocation(
        self, invocation_id: str, run_id: str, attempt: int, role: str, metadata: dict[str, Any]
    ) -> None:
        old = self.invocation(invocation_id)
        if old:
            if (old["run_id"], old["attempt"], old["role"], old["metadata"]) != (
                run_id,
                attempt,
                role,
                metadata,
            ):
                raise ValueError("an invocation's execution snapshot is immutable")
            return
        now = time.time()
        self.db.execute(
            "INSERT INTO invocations(id,run_id,attempt,role,metadata,started_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (invocation_id, run_id, attempt, role, json.dumps(metadata), now, now),
        )

    def observe(self, invocation_id: str, sequence: int, telemetry: dict[str, Any]) -> bool:
        return (
            self.db.execute(
                "UPDATE invocations SET sequence=?,telemetry=?,updated_at=? "
                "WHERE id=? AND sequence<?",
                (sequence, json.dumps(telemetry), time.time(), invocation_id, sequence),
            ).rowcount
            == 1
        )

    def refresh_pricing(self, invocation_id: str, sequence: int, telemetry: dict[str, Any]) -> None:
        """Reprice identical retained observations without advancing their event sequence."""
        pricing_keys = {"estimate", "parent_estimate"}
        with self.transaction():
            current = self.invocation(invocation_id)
            if current is None or current["sequence"] != sequence or not current["telemetry"]:
                return
            retained = current["telemetry"]
            if {k: v for k, v in retained.items() if k not in pricing_keys} != {
                k: v for k, v in telemetry.items() if k not in pricing_keys
            }:
                return
            if retained != telemetry:
                self.db.execute(
                    "UPDATE invocations SET telemetry=?,updated_at=? WHERE id=? AND sequence=?",
                    (json.dumps(telemetry), time.time(), invocation_id, sequence),
                )

    def policy(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT revision,payload FROM policy_snapshots WHERE run_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return json.loads(row["payload"]) | {"revision": row["revision"]} if row else None

    def snapshot_policy(
        self, run_id: str, payload: dict[str, Any], *, replace: bool = False
    ) -> dict[str, Any]:
        with self.transaction():
            existing = self.policy(run_id)
            if existing and not replace:
                return existing
            revision = existing["revision"] + 1 if existing else 1
            self.db.execute(
                "INSERT INTO policy_snapshots VALUES (?,?,?,?)",
                (run_id, revision, json.dumps(payload), time.time()),
            )
            if replace:
                self.audit("run", run_id, "policy-replaced", {"revision": revision})
        return payload | {"revision": revision}

    def admit(self, run_id: str, project: str, limit: int) -> bool:
        if limit < 1:
            raise ValueError("concurrency must be positive")
        with self.transaction():
            self.db.execute(
                "DELETE FROM project_slots WHERE run_id IN (SELECT id FROM runs "
                "WHERE state IN ('cancelled','completed','blocked','failed','suspended','awaiting_human'))"
            )
            if self.db.execute("SELECT 1 FROM project_slots WHERE run_id=?", (run_id,)).fetchone():
                return True
            # Include pre-upgrade writers and preparation states before the first agent.
            active = self.db.execute(
                "SELECT DISTINCT id FROM runs WHERE project=? AND id!=? AND "
                "(id IN (SELECT run_id FROM project_slots) OR state IN "
                "('claimed','context_loaded','sandbox_creating','sandbox_ready','worktree_ready',"
                "'planning','implementing','verifying','reviewing','pr_ready','resumable'))",
                (project, run_id),
            ).fetchall()
            if len(active) >= limit:
                return False
            self.db.execute("INSERT INTO project_slots VALUES (?,?)", (run_id, project))
            return True

    def approve(self, run_id: str, invocation_key: str) -> None:
        self.configure("run", run_id, {"approved_invocation": invocation_key})
        self.audit("run", run_id, "approve-attempt", {"invocation": invocation_key})

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM operator_events WHERE owner=? ORDER BY id", (run_id,)
            )
        ]
