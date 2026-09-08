"""Host-owned attestation publication and launch validation, without model calls."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.agent import app_server
from factory.certification import CertificationIdentity, Certifications
from factory.machine import Blocked
from factory.runtime_jobs import RuntimeJobs
from factory.store import Store


def identity() -> CertificationIdentity:
    return CertificationIdentity(
        sandbox="factory-build-synthetic",
        generation="host-generation-one",
        spec_sha256="1" * 64,
        image_digest="sha256:" + "2" * 64,
        runtime_path="/opt/codex/bin/codex",
        runtime_version="synthetic",
        runtime_sha256="3" * 64,
        launcher_sha256="4" * 64,
        code_host_sha256="5" * 64,
        worker_sha256=hashlib.sha256(
            Path(app_server.__file__).with_name("app_server_worker.py").read_bytes()
        ).hexdigest(),
        authority_sha256="4" * 64,
        probe_sha256="5" * 64,
        usage_scope="connection",
    )


def write_report(root: Path, job: dict) -> Path:
    directory = root / job["id"]
    directory.mkdir(parents=True)
    evidence = directory / "evidence.txt"
    evidence.write_text("synthetic observations, not runtime acceptance")
    snapshot = job["identity"]
    report = {
        "sandbox": snapshot["sandbox"],
        "runtime_version": snapshot["runtime_version"],
        "worker_sha256": snapshot["worker_sha256"],
        "usage_scope": snapshot["usage_scope"],
        "certification": {
            "job_id": job["id"],
            "fingerprint": job["fingerprint"],
            "identity": snapshot,
        },
        "checks": {
            name: {
                "status": "pass",
                "evidence": evidence.name,
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            }
            for name in app_server.COMPATIBILITY_CHECKS
        },
    }
    path = directory / "compatibility.json"
    path.write_text(json.dumps(report))
    return path


def test_only_published_exact_identity_survives_controller_restart(tmp_path: Path) -> None:
    db = tmp_path / "store.db"
    root = tmp_path / "certifications"
    store = Store(db)
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    service = Certifications(store, root, writable_roots=(tmp_path / "candidate",))
    current = identity()
    job = service.request(run.id, current)
    with pytest.raises(Blocked, match="certification-pending"):
        service.validate(job["id"], current)
    write_report(root, job)
    # A file alone does not grant launch permission.
    with pytest.raises(Blocked, match="certification-pending"):
        service.validate(job["id"], current)
    token = RuntimeJobs(store).claim_certification(job["id"], now=10, duration=30)
    assert token is not None
    service.publish(job["id"], token, current, now=11)
    store.close()
    store = Store(db)
    service = Certifications(store, root, writable_roots=(tmp_path / "candidate",))
    assert service.request(run.id, current)["id"] == job["id"]
    assert service.validate(job["id"], current)["usage_scope"] == "connection"
    with pytest.raises(Blocked, match="certification-stale"):
        service.validate(job["id"], replace(current, generation="host-generation-two"))
    assert (
        service.request(run.id, replace(current, generation="host-generation-two"))["id"]
        != job["id"]
    )
    store.close()


@pytest.mark.parametrize("change", ["evidence", "report", "identity", "missing-check", "symlink"])
def test_tampered_or_incomplete_evidence_cannot_publish_or_launch(
    tmp_path: Path, change: str
) -> None:
    store = Store(tmp_path / "store.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    root = tmp_path / "certifications"
    service = Certifications(store, root, writable_roots=(tmp_path / "candidate",))
    current = identity()
    job = service.request(run.id, current)
    path = write_report(root, job)
    token = RuntimeJobs(store).claim_certification(job["id"], now=10, duration=30)
    assert token is not None
    service.publish(job["id"], token, current, now=11)
    report = json.loads(path.read_text())
    if change == "evidence":
        path.with_name("evidence.txt").write_text("altered observation")
    elif change == "report":
        report["extra"] = "changed after publication"
        path.write_text(json.dumps(report))
    elif change == "identity":
        report["certification"]["identity"]["generation"] = "forged"
        path.write_text(json.dumps(report))
    elif change == "missing-check":
        del report["checks"]["hook_enforcement"]
        path.write_text(json.dumps(report))
    else:
        target = tmp_path / "candidate-report.json"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(Blocked):
        service.validate(job["id"], current)
    store.close()


@pytest.mark.parametrize(
    "field",
    [
        "generation",
        "spec_sha256",
        "image_digest",
        "runtime_path",
        "runtime_version",
        "runtime_sha256",
        "worker_sha256",
        "authority_sha256",
        "probe_sha256",
        "usage_scope",
    ],
)
def test_every_identity_component_is_required(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValueError, match="missing certification identity"):
        replace(identity(), **{field: ""})


def test_candidate_writable_attestation_root_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(candidate, target_is_directory=True)
    with pytest.raises(ValueError, match="candidate-writable"):
        Certifications(store, alias / "reports", writable_roots=(candidate,))
    store.close()
