"""Durable scheduling through the job service, using isolated SQLite stores."""

from pathlib import Path

import pytest

from factory.runtime_jobs import RuntimeJobs
from factory.store import Store


@pytest.mark.parametrize("changed", ["run", "attempt", "role"])
def test_invocation_replay_cannot_change_ownership(tmp_path: Path, changed: str) -> None:
    store = Store(tmp_path / "factory.db")
    first = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    second = store.insert_run(linear_id="SYN-2", project="synthetic", team="SYN")
    store.runtime.start_invocation("first", first.id, 1, "builder", {})
    with pytest.raises(ValueError, match="immutable"):
        store.runtime.start_invocation(
            "first",
            second.id if changed == "run" else first.id,
            2 if changed == "attempt" else 1,
            "reviewer" if changed == "role" else "builder",
            {},
        )
    retained = store.runtime.invocation("first")
    assert retained is not None
    assert (retained["run_id"], retained["attempt"], retained["role"]) == (first.id, 1, "builder")
    store.close()


def test_a_lowered_project_ceiling_drains_a_retained_run_override(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"max_active_agents": 2})
    store.runtime.configure("run", run.id, {"max_active_agents": 3})
    assert store.runtime.effective("synthetic", run.id)["max_active_agents"] == 2
    store.close()


@pytest.mark.parametrize("value", [None, "", "2", 0, -1, True, 1.5])
def test_malformed_agent_limits_refuse_admission(tmp_path: Path, value: object) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("first", run.id, 1, "builder", {})
    store.runtime.configure("project", "synthetic", {"max_active_agents": value})
    if value is None:
        # Public null means inherit; malformed persisted null must still fail closed.
        store.runtime.db.execute(
            "UPDATE operator_settings SET settings=json_set(settings,?,NULL) WHERE scope='project' AND owner='synthetic'",
            ("$.max_active_agents",),
        )
    jobs = RuntimeJobs(store)
    with pytest.raises(ValueError, match="positive integer"):
        jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    assert jobs.active_agents("synthetic") == []
    store.close()


@pytest.mark.parametrize("reason", ["budget", "attempt"])
def test_exhausted_run_cannot_reserve_capacity_or_consume_approval(
    tmp_path: Path, reason: str
) -> None:
    from factory.machine import Blocked

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("first", run.id, 1, "builder", {})
    store.runtime.start_invocation("second", run.id, 3 if reason == "attempt" else 1, "builder", {})
    jobs = RuntimeJobs(store)
    assert jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    store.runtime.configure("project", "synthetic", {"mode": "approval"})
    store.runtime.approve(run.id, "second")
    if reason == "budget":
        store.reconcile_cost(
            run.id,
            1,
            "first",
            model="synthetic",
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            usd=10,
        )
    with pytest.raises(Blocked):
        jobs.schedule_agent("second", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    assert store.runtime.settings("run", run.id)["approved_invocation"] == "second"
    store.close()


@pytest.mark.parametrize("mode", [None, "", "Approval", False])
def test_malformed_approval_mode_does_not_enable_automatic_admission(
    tmp_path: Path, mode: object
) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("first", run.id, 1, "builder", {})
    store.runtime.configure("project", "synthetic", {"mode": mode})
    jobs = RuntimeJobs(store)
    with pytest.raises(ValueError, match="approval mode"):
        jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    assert jobs.active_agents("synthetic") == []
    store.close()


def test_agent_capacity_counts_invocations_in_one_run_and_drains(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    for name in ["first", "second", "reviewer"]:
        store.runtime.start_invocation(name, run.id, 1, name, {})
    jobs = RuntimeJobs(store)
    store.runtime.configure("project", "synthetic", {"max_active_agents": 2})
    assert jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    assert jobs.schedule_agent("second", usd_limit=10, max_attempts=2)
    assert not jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    store.runtime.configure("project", "synthetic", {"max_active_agents": 1})
    assert jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 2
    jobs.finish_agent("second", status="cancelled", evidence="exit")
    assert not jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    jobs.finish_agent("first", status="completed", evidence="exit")
    assert jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    store.close()


def test_a_lowered_ceiling_keeps_an_admitted_agent_without_admitting_it_twice(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"max_active_agents": 2, "mode": "approval"})
    store.runtime.start_invocation("first", run.id, 1, "implement", {"semantic_role": "builder"})
    store.runtime.approve(run.id, "first")
    jobs = RuntimeJobs(store)
    assert jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    assert store.runtime.settings("run", run.id)["approved_invocation"] is None
    store.runtime.configure("run", run.id, {"max_active_agents": 2})
    store.runtime.configure("project", "synthetic", {"max_active_agents": 1})
    assert jobs.schedule_agent("first", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    store.close()
