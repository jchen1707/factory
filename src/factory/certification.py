"""Host-owned certification attestations; probe execution is a separate operation.

Only the controller may construct identities and write reports in this service's root.
A model's claim of success is never an attestation. Callers must observe identity again
at publication and at launch, rather than replaying the identity stored with the job.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from factory.agent.app_server import read_evidence, validate_compatibility
from factory.machine import Blocked
from factory.policy import assert_factory_sandbox
from factory.runtime_jobs import RuntimeJobs
from factory.store import Store


@dataclass(frozen=True)
class CertificationIdentity:
    sandbox: str
    generation: str
    spec_sha256: str
    image_digest: str
    runtime_path: str
    runtime_version: str
    runtime_sha256: str
    worker_sha256: str
    authority_sha256: str
    probe_sha256: str
    usage_scope: str

    def __post_init__(self) -> None:
        assert_factory_sandbox(self.sandbox)
        for field, value in asdict(self).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"missing certification identity: {field}")
            if field.endswith("_sha256") and (
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"invalid certification digest: {field}")
        if not Path(self.runtime_path).is_absolute():
            raise ValueError("runtime binary path must be absolute")
        if self.usage_scope not in {"thread", "connection"}:
            raise ValueError("unknown usage counter scope")


class Certifications:
    """Publish validated evidence atomically in SQLite, outside candidate mounts.

    This boundary does not execute probes or invent an identity from a sandbox name.
    The orchestration layer supplies fresh adapter observations and host-generated
    reports. Existing manual manifests are never promoted implicitly.
    """

    def __init__(self, store: Store, root: Path, *, writable_roots: tuple[Path, ...]) -> None:
        self.store = store
        self.jobs = RuntimeJobs(store)
        self.root = root.resolve()
        if any(self.root.is_relative_to(path.resolve()) for path in writable_roots):
            raise ValueError("certification evidence must be outside candidate-writable roots")

    def request(self, run_id: str, identity: CertificationIdentity) -> dict[str, Any]:
        return self.jobs.request_certification(run_id, asdict(identity))

    def publish(
        self, job_id: str, token: str, current: CertificationIdentity, *, now: float
    ) -> None:
        job = self._matching(job_id, current)
        report, digest = self._report(job, current)
        # The lease fence and attestation become visible in one database commit. A
        # crash before this point leaves checking; a crash after it leaves passed.
        self.jobs.finish_certification(
            job_id,
            token,
            now=now,
            evidence={"report_sha256": digest, "report": report},
        )

    def validate(self, job_id: str, current: CertificationIdentity) -> dict[str, Any]:
        job = self._matching(job_id, current)
        if job["status"] != "passed":
            raise Blocked("certification-" + job["status"], job.get("failure") or job_id)
        report, digest = self._report(job, current)
        if job["evidence"] != {"report_sha256": digest, "report": report}:
            raise Blocked("certification-evidence-changed", job_id)
        return report

    def _matching(self, job_id: str, current: CertificationIdentity) -> dict[str, Any]:
        job = self.jobs.certification(job_id)
        if job is None:
            raise Blocked("certification-unknown", job_id)
        if job["identity"] != asdict(current):
            raise Blocked("certification-stale", job_id)
        return job

    def _report(
        self, job: dict[str, Any], current: CertificationIdentity
    ) -> tuple[dict[str, Any], str]:
        try:
            # IDs are store-generated UUIDs. Never accept a path supplied by a worker.
            job_id = job["id"]
            if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                raise ValueError("invalid certification job identity")
            directory = self.root / job_id
            if directory.is_symlink():
                raise ValueError("certification directory cannot be a symlink")
            report_bytes = read_evidence(self.root, f"{job_id}/compatibility.json")
            report = validate_compatibility(
                directory / "compatibility.json",
                runtime_version=current.runtime_version,
                sandbox=current.sandbox,
            )
            if report != json.loads(report_bytes):
                raise ValueError("certification report changed during validation")
            if report.get("certification") != {
                "job_id": job_id,
                "fingerprint": job["fingerprint"],
                "identity": asdict(current),
            }:
                raise ValueError("report does not belong to this certification")
            if (
                report.get("worker_sha256") != current.worker_sha256
                or report.get("usage_scope") != current.usage_scope
            ):
                raise ValueError("report worker or usage semantics changed")
            return report, hashlib.sha256(report_bytes).hexdigest()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise Blocked("certification-evidence-invalid", str(exc)) from exc
