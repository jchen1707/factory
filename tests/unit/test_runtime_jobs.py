"""Durable scheduling through the job service, using isolated SQLite stores."""

from pathlib import Path

from factory.runtime_jobs import RuntimeJobs
from factory.store import Store


def test_certification_request_is_idempotent_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    jobs = RuntimeJobs(store)
    first = jobs.request_certification(
        run.id, {"sandbox": "factory-build-synthetic", "generation": "one"}
    )
    repeated = jobs.request_certification(
        run.id, {"sandbox": "factory-build-synthetic", "generation": "one"}
    )
    assert first["id"] == repeated["id"]
    assert first["status"] == "pending"
    store.close()
    reopened = Store(path)
    try:
        assert RuntimeJobs(reopened).certification(first["id"]) == first
    finally:
        reopened.close()


def test_agent_capacity_counts_invocations_in_one_run_and_drains(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    for name in ["parent", "child", "reviewer"]:
        store.runtime.start_invocation(name, run.id, 1, name, {})
    jobs = RuntimeJobs(store)
    assert jobs.admit_agent("parent", limit=2)
    assert jobs.admit_agent("child", limit=2, parent_id="parent")
    assert not jobs.admit_agent("reviewer", limit=2)
    assert jobs.admit_agent("parent", limit=1)
    assert len(jobs.active_agents("synthetic")) == 2
    jobs.finish_agent("child", status="cancelled")
    assert not jobs.admit_agent("reviewer", limit=1)
    jobs.finish_agent("parent", status="completed")
    assert jobs.admit_agent("reviewer", limit=1)
    store.close()


def test_expired_certifier_cannot_publish_after_another_owner_claims(tmp_path: Path) -> None:
    import pytest

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    jobs = RuntimeJobs(store)
    job = jobs.request_certification(run.id, {"sandbox": "factory-build-test", "generation": "one"})
    first = jobs.claim_certification(job["id"], now=10, duration=5)
    assert first is not None
    assert jobs.claim_certification(job["id"], now=11, duration=5) is None
    second = jobs.claim_certification(job["id"], now=16, duration=5)
    assert second is not None
    assert second != first
    with pytest.raises(ValueError, match="lease"):
        jobs.finish_certification(job["id"], first, now=17, evidence={"manifest": "old"})
    jobs.finish_certification(job["id"], second, now=17, evidence={"manifest": "verified"})
    result = jobs.certification(job["id"])
    assert result is not None
    assert result["status"] == "passed"
    store.close()
