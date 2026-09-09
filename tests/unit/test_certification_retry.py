"""Explicit retries preserve evidence and never turn polling into paid retries."""

from dataclasses import asdict
from pathlib import Path

import pytest

from factory.certification import Certifications
from factory.machine import State
from factory.runtime_jobs import RuntimeJobs
from factory.store import Store
from tests.unit.test_certification import identity


def test_failed_certificate_can_be_explicitly_retried_without_changing_identity(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN", state=State.BLOCKED)
    jobs = RuntimeJobs(store)
    service = Certifications(store, tmp_path / "certs", writable_roots=())
    first = service.request(run.id, identity())
    token = jobs.claim_certification(first["id"], now=1, duration=30)
    assert token is not None
    jobs.finish_certification(first["id"], token, now=2, failure="observed hook failure")
    failed = jobs.certification(first["id"])
    assert service.request(run.id, identity()) == failed
    retried = jobs.retry_certification(
        run.id, first["id"], asdict(identity()), reason="Operator approved fresh measurement"
    )
    assert retried["id"] != first["id"]
    assert retried["fingerprint"] == first["fingerprint"]
    assert retried["identity"] == first["identity"]
    assert retried["status"] == "pending"
    assert jobs.certification(first["id"]) == failed
    assert service.request(run.id, identity()) == retried
    assert (
        jobs.retry_certification(
            run.id, first["id"], asdict(identity()), reason="Replayed operator request"
        )
        == retried
    )
    assert store.runtime.invocations(run.id) == []
    store.close()


def failed_job(store: Store) -> tuple[str, dict]:
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN", state=State.BLOCKED)
    jobs = RuntimeJobs(store)
    job = jobs.request_certification(run.id, asdict(identity()))
    token = jobs.claim_certification(job["id"], now=1, duration=10)
    assert token
    jobs.finish_certification(job["id"], token, now=2, failure="hook failure")
    return run.id, job


def test_retry_race_and_restart_create_one_successor(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "db"
    store = Store(path)
    run_id, job = failed_job(store)
    store.close()

    def retry(_: int) -> str:
        reopened = Store(path)
        try:
            return RuntimeJobs(reopened).retry_certification(
                run_id, job["id"], asdict(identity()), reason="authorized retry"
            )["id"]
        finally:
            reopened.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(retry, range(8)))
    assert len(set(ids)) == 1
    reopened = Store(path)
    jobs = RuntimeJobs(reopened)
    successor = jobs.certification(ids[0])
    assert successor
    token = jobs.claim_certification(ids[0], now=3, duration=10)
    assert token
    jobs.finish_certification(ids[0], token, now=4, failure="second measured failure")
    # Replaying an old approval does not authorize a third attempt.
    assert retry(0) == ids[0]
    assert jobs.request_certification(run_id, asdict(identity()))["status"] == "failed"
    assert len(list(reopened.runtime.db.execute("SELECT * FROM runtime_certifications"))) == 2
    reopened.close()


def test_retry_refuses_stale_owner_active_and_nonfailure(tmp_path: Path) -> None:
    import pytest

    from factory.machine import Blocked

    store = Store(tmp_path / "db")
    run_id, job = failed_job(store)
    jobs = RuntimeJobs(store)
    with pytest.raises(Blocked, match="certification-retry-owner"):
        jobs.retry_certification("another-run", job["id"], asdict(identity()), reason="retry")
    stale = asdict(identity()) | {"generation": "replacement"}
    with pytest.raises(Blocked, match="certification-stale"):
        jobs.retry_certification(run_id, job["id"], stale, reason="retry")
    store.runtime.db.execute("UPDATE runs SET state='reviewing' WHERE id=?", (run_id,))
    with pytest.raises(Blocked, match="needs-paused-run"):
        jobs.retry_certification(run_id, job["id"], asdict(identity()), reason="retry")
    store.runtime.db.execute("UPDATE runs SET state='blocked' WHERE id=?", (run_id,))
    store.runtime.start_invocation("uncertain", run_id, 1, "certification", {})
    store.runtime.db.execute(
        "INSERT INTO agent_leases VALUES (?,?,'synthetic','active',NULL)", ("uncertain", run_id)
    )
    with pytest.raises(Blocked, match="active-agent"):
        jobs.retry_certification(run_id, job["id"], asdict(identity()), reason="retry")
    assert len(list(store.runtime.db.execute("SELECT * FROM runtime_certifications"))) == 1
    store.close()


def test_schema_six_upgrade_preserves_every_original_column(tmp_path: Path) -> None:
    import sqlite3

    import pytest

    from factory.machine import Blocked
    from factory.runtime_jobs import SCHEMA
    from factory.store import _MIGRATIONS, _SCHEMA

    path = tmp_path / "v6.db"
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    for version in (3, 4, 5):
        for statement in _MIGRATIONS[version]:
            db.execute(statement)
    for statement in SCHEMA:
        db.execute(statement)
    db.execute(
        "INSERT INTO runs(id,linear_id,project,team,state,attempt,created_at,updated_at) VALUES ('r','SYN-1','synthetic','SYN','blocked',2,1,1)"
    )
    for index, status in enumerate(("pending", "checking", "passed", "failed", "cancelled")):
        db.execute(
            "INSERT INTO runtime_certifications VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(index),
                "r",
                str(index),
                "{}",
                status,
                "owner",
                123.0,
                '{"retained":true}',
                "original",
            ),
        )
    original = db.execute("SELECT * FROM runtime_certifications ORDER BY id").fetchall()
    db.execute("PRAGMA user_version=6")
    db.commit()
    db.close()
    with pytest.raises(Blocked, match="schema-approval-required"):
        Store(path)
    store = Store(path, migrate=True)
    retained = list(store.runtime.db.execute("SELECT * FROM runtime_certifications ORDER BY id"))
    assert [tuple(row)[:9] for row in retained] == original
    assert all(row["sequence"] == 0 and row["retry_of"] is None for row in retained)
    assert store.integrity_ok()[0]
    assert list(store.runtime.db.execute("PRAGMA foreign_key_check")) == []
    store.close()


def test_retry_still_requires_resume_and_exact_probe_approval(tmp_path: Path) -> None:
    import pytest

    from factory.execution import AgentApprovalRequired, ProjectQueued
    from tests.unit.test_certification_runner import Driver, Sandbox, runner

    store = Store(tmp_path / "db")
    run_id, old = failed_job(store)
    sandbox, driver = Sandbox(), Driver(tmp_path)
    service = runner(store, tmp_path, sandbox, driver)
    store.runtime.configure("run", run_id, {"mode": "approval"})
    successor = service.certifications.jobs.retry_certification(
        run_id, old["id"], asdict(identity()), reason="authorized retry"
    )
    with pytest.raises(ProjectQueued):
        service.advance(successor["id"], now=3)
    assert sandbox.handles == []
    store.runtime.db.execute("UPDATE runs SET state='reviewing' WHERE id=?", (run_id,))
    assert service.ensure(run_id, automatic=True)["id"] == successor["id"]
    with pytest.raises(AgentApprovalRequired):
        service.advance(successor["id"], now=4)
    assert sandbox.handles == []
    store.runtime.approve(run_id, f"certification:{successor['id']}:first")
    service.advance(successor["id"], now=5)
    assert len(sandbox.handles) == 1
    # Restart/poll the new attempt; the old failed job remains terminal.
    service.advance(successor["id"], now=6)
    assert len(sandbox.handles) == 1
    assert service.status(old["id"])["status"] == "failed"
    store.close()


@pytest.mark.parametrize("lease_loss", [None, "expired", "new-owner"])
def test_operator_command_observes_and_queues_without_launching(
    tmp_path: Path, monkeypatch, lease_loss
) -> None:
    import argparse

    from factory import cli, configuration_cli, registry, routing, workflow_certification
    from factory.intake import linear
    from tests.unit.test_certification_runner import Driver, Sandbox, runner

    store = Store(tmp_path / "state/factory.db")
    run_id, previous = failed_job(store)
    store.close()
    sandbox, driver = Sandbox(), Driver(tmp_path)
    monkeypatch.setattr(cli, "factory_home", lambda: tmp_path)
    monkeypatch.setattr(registry, "load_registry", lambda _: None)
    monkeypatch.setattr(routing, "load_routing", lambda _: None)
    monkeypatch.setattr(linear, "LinearClient", lambda: None)
    monkeypatch.setattr(cli, "_context_for", lambda home, reg, routes, db, client, run: db)
    observations = []

    def service(ctx, **kwargs):
        assert kwargs == {"review": True, "prepare_runtime": False}
        result = runner(ctx, tmp_path, sandbox, driver)

        def observe():
            observations.append(True)
            if lease_loss == "expired":
                ctx.runtime.db.execute("UPDATE runs SET lease_expires_at=1 WHERE id=?", (run_id,))
            elif lease_loss:
                ctx.runtime.db.execute(
                    "UPDATE runs SET lease_owner='new-owner' WHERE id=?", (run_id,)
                )
            return identity()

        result.observe = observe
        return result, None

    monkeypatch.setattr(workflow_certification, "service", service)
    args = argparse.Namespace(ticket="SYN-1", job=previous["id"], role="review", reason="retry")
    if lease_loss:
        from factory.machine import Blocked

        with pytest.raises(Blocked, match="run-lease-lost"):
            configuration_cli.retry_certification(args)
        reopened = Store(tmp_path / "state/factory.db")
        assert len(list(reopened.runtime.db.execute("SELECT * FROM runtime_certifications"))) == 1
        if lease_loss == "new-owner":
            assert reopened.run_by_id(run_id).lease_owner == "new-owner"
        assert sandbox.handles == []
        reopened.close()
        return
    assert configuration_cli.retry_certification(args) == 0
    assert configuration_cli.retry_certification(args) == 0
    reopened = Store(tmp_path / "state/factory.db")
    assert len(observations) == 2
    assert sandbox.handles == []
    assert reopened.runtime.invocations(run_id) == []
    assert len(list(reopened.runtime.db.execute("SELECT * FROM runtime_certifications"))) == 2
    assert reopened.run_by_id(run_id).state == State.BLOCKED
    reopened.close()
