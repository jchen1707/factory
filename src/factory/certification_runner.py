"""Durable host orchestration of source-owned compatibility probes.

A certification lease coordinates observation; only AgentLaunches grants paid spawn
ownership. Expiring the former never retries an ambiguous execution of the latter.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from factory import accounting
from factory.agent_launches import AgentLaunches
from factory.certification import CertificationIdentity, Certifications
from factory.execution import ProjectQueued
from factory.machine import Blocked, State
from factory.sandbox.base import RunHandle, RunStatus


@dataclass(frozen=True)
class PreparedProbe:
    handle: RunHandle
    script: str
    events: Path
    metadata: dict[str, Any]
    inputs: tuple[Path, ...] = ()


class ProbeDriver(Protocol):
    """Trusted adapter; preparation is deterministic and must never start execution."""

    def preflight(self, identity: CertificationIdentity) -> None: ...
    def initialize(self, job: dict[str, Any]) -> None: ...

    def steps(self, identity: CertificationIdentity) -> tuple[str, ...]: ...
    def environment(self, job: dict[str, Any], step: str) -> Mapping[str, str]: ...
    def prepare(self, job: dict[str, Any], step: str, invocation_id: str) -> PreparedProbe: ...
    def advance(
        self, job: dict[str, Any], step: str, handle: RunHandle, status: RunStatus
    ) -> None: ...

    def accept_exit(self, job: dict[str, Any], step: str, code: int) -> bool: ...

    def evaluate(self, job: dict[str, Any]) -> None:
        """Observe evidence and write the complete host report; never trust model claims."""
        ...


class CertificationRunner:
    def __init__(
        self,
        certifications: Certifications,
        launches: AgentLaunches,
        probe_driver: ProbeDriver,
        observe: Callable[[], CertificationIdentity],
        *,
        home: Path,
        usd_limit: float,
        max_attempts: int,
    ) -> None:
        self.certifications = certifications
        self.launches = launches
        self.driver = probe_driver
        self.observe = observe
        self.home = home
        self.usd_limit = usd_limit
        self.max_attempts = max_attempts
        self.store = certifications.store

    def status(self, job_id: str) -> dict[str, Any]:
        job = self.certifications.jobs.certification(job_id)
        if job is None:
            raise Blocked("certification-unknown", job_id)
        return job

    def ensure(self, run_id: str, *, automatic: bool = False) -> dict[str, Any]:
        previous = [
            row["id"]
            for row in self.store.runtime.db.execute(
                "SELECT id FROM runtime_certifications WHERE run_id=?", (run_id,)
            )
        ]
        # A new fingerprint cannot strand a terminal old probe's cost or VM slot.
        # Collection needs only durable handles/events, never the obsolete driver inputs.
        for job_id in previous:
            self.collect(job_id)
        identity = self.observe()
        self.driver.preflight(identity)
        for job_id in previous:
            self._retire_stale(job_id, identity)
        job = self.certifications.request(run_id, identity)
        if job["status"] == "passed":
            self.certifications.validate(job["id"], identity)
        elif not automatic:
            raise Blocked("certification-required", job["id"])
        return job

    def advance(self, job_id: str, *, now: float) -> dict[str, Any]:
        started = time.monotonic()
        job = self.status(job_id)
        self.collect(job_id)
        if job["status"] in {"passed", "failed"}:
            if job["status"] == "passed":
                self.certifications.validate(job_id, self.observe())
            return job
        token = self.certifications.jobs.claim_certification(job_id, now=now, duration=60)
        if token is None:
            return self.status(job_id)
        try:
            self._owner_active(job)
            identity = self._current(job)
            self.driver.preflight(identity)
            self.driver.initialize(job)
            steps = self.driver.steps(identity)
            if not steps or len(set(steps)) != len(steps) or any(not step for step in steps):
                raise Blocked("certification-probes-invalid", job_id)
            for step in steps:
                invocation_id, prepared = self._prepare(job, step)
                intent = self.store.find_effect(
                    job["run_id"], prepared.handle.attempt, invocation_id, "agent-launch", "spawn"
                )
                if intent is None:
                    self._owner_active(job)
                    self._current(job)
                    self._fenced(job_id, token, now + time.monotonic() - started)
                    self.launches.start(
                        invocation_id,
                        prepared.handle,
                        prepared.script,
                        self.driver.environment(job, step),
                        usd_limit=self.usd_limit,
                        max_attempts=self.max_attempts,
                    )
                    return self.status(job_id)
                self.driver.advance(
                    job, step, prepared.handle, self.launches.observe(invocation_id)
                )
                if not self.launches.reconcile(
                    invocation_id,
                    collect=partial(
                        accounting.collect_invocation,
                        self.store,
                        self.home,
                        invocation_id,
                        prepared.events,
                    ),
                ):
                    # Unknown/orphaned holders retain ownership and capacity. Operators
                    # can inspect the durable invocation rather than buying a duplicate.
                    return self.status(job_id)
                if not self.driver.accept_exit(
                    job,
                    step,
                    int((prepared.handle.attempt_dir / prepared.handle.exit_name).read_text()),
                ):
                    raise Blocked("certification-probe-failed", invocation_id)
            self.driver.evaluate(job)
            self.certifications.publish(
                job_id, token, self._current(job), now=now + time.monotonic() - started
            )
            return self.status(job_id)
        except Blocked as exc:
            self.certifications.jobs.finish_certification(
                job_id,
                token,
                now=now,
                failure=str(exc),
            )
            raise
        finally:
            # Yield coordination after each advance, retaining all paid launch intents.
            with self.store.runtime.transaction():
                self.store.runtime.db.execute(
                    "UPDATE runtime_certifications SET lease_until=? "
                    "WHERE id=? AND status='checking' AND owner=?",
                    (now, job_id, token),
                )

    def collect(self, job_id: str) -> None:
        """Terminal accounting remains available after suspension or identity failure."""
        job = self.status(job_id)
        for invocation in self.store.runtime.invocations(job["run_id"]):
            if invocation["metadata"].get("certification_job") != job_id:
                continue
            if (
                self.store.find_effect(
                    job["run_id"], invocation["attempt"], invocation["id"], "agent-launch", "spawn"
                )
                is None
            ):
                continue
            self.launches.reconcile(
                invocation["id"],
                collect=partial(
                    accounting.collect_invocation,
                    self.store,
                    self.home,
                    invocation["id"],
                    Path(invocation["metadata"]["events"]),
                ),
            )

    def _retire_stale(self, job_id: str, current: CertificationIdentity) -> None:
        with self.store.runtime.transaction():
            job = self.status(job_id)
            if job["identity"] == asdict(current) or job["status"] not in {"pending", "checking"}:
                return
            if self.store.runtime.db.execute(
                "SELECT 1 FROM agent_leases a JOIN invocations i ON i.id=a.invocation_id "
                "WHERE a.status='active' AND json_extract(i.metadata,'$.certification_job')=?",
                (job_id,),
            ).fetchone():
                return
            now = time.time()
            token = self.certifications.jobs.claim_certification(job_id, now=now, duration=60)
            if token is not None:
                self.certifications.jobs.finish_certification(
                    job_id, token, now=now, failure="certification-stale: observed identity changed"
                )

    def _owner_active(self, job: dict[str, Any]) -> None:
        run = self.store.run_by_id(job["run_id"])
        if run is None or run.state in {
            State.CANCELLED,
            State.SUSPENDED,
            State.BLOCKED,
            State.FAILED,
            State.COMPLETED,
            State.AWAITING_HUMAN,
        }:
            raise ProjectQueued("certification owner is not active")

    def _fenced(self, job_id: str, token: str, now: float) -> None:
        job = self.status(job_id)
        if job["owner"] != token or job["lease_until"] <= now:
            raise ValueError("certification lease expired; reconcile before advancing")

    def _current(self, job: dict[str, Any]) -> CertificationIdentity:
        current = self.observe()
        if asdict(current) != job["identity"]:
            raise Blocked("certification-stale", job["id"])
        return current

    def _prepare(self, job: dict[str, Any], step: str) -> tuple[str, PreparedProbe]:
        invocation_id = f"certification:{job['id']}:{step}"
        environment = self.driver.environment(job, step)
        env_digest = hashlib.sha256(
            json.dumps(dict(environment), sort_keys=True).encode()
        ).hexdigest()
        with self.store.runtime.transaction():
            invocation = self.store.runtime.invocation(invocation_id)
            if invocation is None:
                prepared = self.driver.prepare(job, step, invocation_id)
                if (
                    prepared.handle.run_id != job["run_id"]
                    or prepared.handle.sandbox != job["identity"]["sandbox"]
                ):
                    raise ValueError("probe handle must belong to its certification")
                metadata = prepared.metadata | {
                    "events": str(prepared.events),
                    "cost_step": invocation_id,
                    "certification_job": job["id"],
                    "preparation": {
                        "handle": asdict(prepared.handle)
                        | {"attempt_dir": str(prepared.handle.attempt_dir)},
                        "script": prepared.script,
                        "env_sha256": env_digest,
                        "inputs": {
                            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in prepared.inputs
                        },
                    },
                }
                self.store.runtime.start_invocation(
                    invocation_id, job["run_id"], prepared.handle.attempt, "certification", metadata
                )
                self.store.reconcile_cost(
                    job["run_id"],
                    prepared.handle.attempt,
                    invocation_id,
                    model=metadata["model"],
                    input_tokens=0,
                    output_tokens=0,
                    cached_tokens=0,
                    usd=None,
                )
            else:
                metadata = invocation["metadata"]
                saved = metadata["preparation"]
                if saved["env_sha256"] != env_digest or any(
                    not Path(path).is_file()
                    or hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
                    for path, digest in saved["inputs"].items()
                ):
                    raise Blocked("certification-environment-changed", invocation_id)
                handle = saved["handle"]
                prepared = PreparedProbe(
                    RunHandle(**(handle | {"attempt_dir": Path(handle["attempt_dir"])})),
                    saved["script"],
                    Path(metadata["events"]),
                    metadata,
                )
        return invocation_id, prepared
