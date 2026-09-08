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
    store.runtime.start_invocation("parent", first.id, 1, "builder", {})
    with pytest.raises(ValueError, match="immutable"):
        store.runtime.start_invocation(
            "parent",
            second.id if changed == "run" else first.id,
            2 if changed == "attempt" else 1,
            "reviewer" if changed == "role" else "builder",
            {},
        )
    retained = store.runtime.invocation("parent")
    assert retained is not None
    assert (retained["run_id"], retained["attempt"], retained["role"]) == (first.id, 1, "builder")
    store.close()


def test_each_child_needs_its_own_approval_before_reserving_capacity(tmp_path: Path) -> None:
    from factory.execution import AgentApprovalRequired

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    for name in ["parent", "child"]:
        store.runtime.start_invocation(name, run.id, 1, "builder", {})
    store.runtime.configure(
        "project", "synthetic", {"mode": "approval", "delegation_mode": "read-only"}
    )
    jobs = RuntimeJobs(store)
    store.runtime.approve(run.id, "parent")
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    with pytest.raises(AgentApprovalRequired):
        jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    store.runtime.approve(run.id, "child")
    assert jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    assert store.runtime.settings("run", run.id)["approved_invocation"] is None
    assert jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    store.close()


def test_project_ceiling_and_child_limits_cannot_be_bypassed(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    for name in ["parent", "child", "extra", "grandchild"]:
        store.runtime.start_invocation(name, run.id, 1, "builder", {})
    jobs = RuntimeJobs(store)
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    with pytest.raises(ValueError, match="disabled"):
        jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    store.runtime.configure(
        "project",
        "synthetic",
        {
            "delegation_mode": "read-only",
            "max_active_agents": 2,
            "max_children_per_parent": 1,
        },
    )
    store.runtime.configure("run", run.id, {"max_active_agents": 3})
    # A retained run choice cannot prevent a newly lowered project ceiling draining.
    assert store.runtime.effective("synthetic", run.id)["max_active_agents"] == 2
    store.runtime.configure("run", run.id, {"max_active_agents": 2})
    assert jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    assert not jobs.schedule_agent("extra", parent_id="parent", usd_limit=10, max_attempts=2)
    with pytest.raises(ValueError, match="depth"):
        jobs.schedule_agent("grandchild", parent_id="child", usd_limit=10, max_attempts=2)
    jobs.finish_agent("child", status="completed")
    assert jobs.schedule_agent("extra", parent_id="parent", usd_limit=10, max_attempts=2)
    store.close()


@pytest.mark.parametrize(
    "field", ["max_active_agents", "max_children_per_parent", "max_delegation_depth"]
)
@pytest.mark.parametrize("value", [None, "", "2", 0, -1, True, 1.5])
def test_malformed_agent_limits_refuse_admission(tmp_path: Path, field: str, value: object) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("parent", run.id, 1, "builder", {})
    store.runtime.configure("project", "synthetic", {field: value})
    if value is None:
        # Public null means inherit; malformed persisted null must still fail closed.
        store.runtime.db.execute(
            "UPDATE operator_settings SET settings=json_set(settings,?,NULL) WHERE scope='project' AND owner='synthetic'",
            ("$." + field,),
        )
    jobs = RuntimeJobs(store)
    with pytest.raises(ValueError, match="positive integer"):
        jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert jobs.active_agents("synthetic") == []
    store.close()


@pytest.mark.parametrize("reason", ["budget", "attempt"])
def test_exhausted_run_cannot_reserve_child_or_consume_approval(
    tmp_path: Path, reason: str
) -> None:
    from factory.machine import Blocked

    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("parent", run.id, 1, "builder", {})
    store.runtime.start_invocation("child", run.id, 3 if reason == "attempt" else 1, "builder", {})
    jobs = RuntimeJobs(store)
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    store.runtime.configure(
        "project", "synthetic", {"mode": "approval", "delegation_mode": "read-only"}
    )
    store.runtime.approve(run.id, "child")
    if reason == "budget":
        store.reconcile_cost(
            run.id,
            1,
            "parent",
            model="synthetic",
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            usd=10,
        )
    with pytest.raises(Blocked):
        jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    assert store.runtime.settings("run", run.id)["approved_invocation"] == "child"
    store.close()


def test_child_cannot_attach_to_another_runs_parent(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    first = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    second = store.insert_run(linear_id="SYN-2", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"delegation_mode": "read-only"})
    store.runtime.start_invocation("parent", first.id, 1, "builder", {})
    store.runtime.start_invocation("child", second.id, 1, "builder", {})
    jobs = RuntimeJobs(store)
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    with pytest.raises(ValueError, match="same run"):
        jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    store.close()


def test_child_cannot_reset_the_parents_attempt_number(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure("project", "synthetic", {"delegation_mode": "read-only"})
    store.runtime.start_invocation("parent", run.id, 2, "builder", {})
    store.runtime.start_invocation("child", run.id, 1, "builder", {})
    jobs = RuntimeJobs(store)
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    with pytest.raises(ValueError, match="attempt"):
        jobs.schedule_agent("child", parent_id="parent", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    store.close()


@pytest.mark.parametrize("mode", [None, "", "Approval", False])
def test_malformed_approval_mode_does_not_enable_automatic_admission(
    tmp_path: Path, mode: object
) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("parent", run.id, 1, "builder", {})
    store.runtime.configure("project", "synthetic", {"mode": mode})
    jobs = RuntimeJobs(store)
    with pytest.raises(ValueError, match="approval mode"):
        jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert jobs.active_agents("synthetic") == []
    store.close()


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
    store.runtime.configure(
        "project",
        "synthetic",
        {
            "max_active_agents": 2,
            "delegation_mode": "read-only",
        },
    )
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert jobs.schedule_agent("child", usd_limit=10, max_attempts=2, parent_id="parent")
    assert not jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    store.runtime.configure("project", "synthetic", {"max_active_agents": 1})
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 2
    jobs.finish_agent("child", status="cancelled")
    assert not jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    jobs.finish_agent("parent", status="completed")
    assert jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
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


def test_four_parents_keep_progress_capacity_for_children_and_certifiers(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    jobs = RuntimeJobs(store)
    store.runtime.configure(
        "project", "synthetic", {"delegation_mode": "read-only", "max_active_agents": 8}
    )
    runs = [
        store.insert_run(linear_id=f"SYN-{n}", project="synthetic", team="SYN") for n in range(4)
    ]
    for n, run in enumerate(runs):
        for name, role in ((f"parent-{n}", "builder"), (f"child-{n}", "documenter")):
            store.runtime.start_invocation(name, run.id, 1, role, {})
        assert jobs.schedule_agent(f"parent-{n}", usd_limit=10, max_attempts=2)
    store.runtime.start_invocation("reviewer", runs[0].id, 1, "reviewer", {})
    assert not jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    store.runtime.start_invocation("probe", runs[0].id, 1, "certification", {})
    assert jobs.schedule_agent("probe", usd_limit=10, max_attempts=2)
    for n in range(1, 4):
        assert jobs.schedule_agent(
            f"child-{n}", parent_id=f"parent-{n}", usd_limit=10, max_attempts=2
        )
    assert len(jobs.active_agents("synthetic")) == 8
    jobs.finish_agent("probe", status="completed")
    # An eager sibling cannot take the progress lane released by the certifier.
    store.runtime.start_invocation("second-child", runs[1].id, 1, "documenter", {})
    assert not jobs.schedule_agent(
        "second-child", parent_id="parent-1", usd_limit=10, max_attempts=2
    )
    assert jobs.schedule_agent("child-0", parent_id="parent-0", usd_limit=10, max_attempts=2)
    for n in range(4):
        jobs.finish_agent(f"child-{n}", status="completed")
        jobs.finish_agent(f"parent-{n}", status="completed")
    assert jobs.schedule_agent("reviewer", usd_limit=10, max_attempts=2)
    store.close()


def test_delegating_builder_needs_progress_capacity_before_consuming_approval(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.configure(
        "project",
        "synthetic",
        {"max_active_agents": 1, "delegation_mode": "read-only", "mode": "approval"},
    )
    store.runtime.start_invocation("parent", run.id, 1, "implement", {"semantic_role": "builder"})
    store.runtime.approve(run.id, "parent")
    jobs = RuntimeJobs(store)
    assert not jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert jobs.active_agents("synthetic") == []
    assert store.runtime.settings("run", run.id)["approved_invocation"] == "parent"
    store.runtime.configure("project", "synthetic", {"max_active_agents": 2})
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert store.runtime.settings("run", run.id)["approved_invocation"] is None
    # Lowering the ceiling preserves the paid parent. Resuming does not admit it twice.
    store.runtime.configure("run", run.id, {"max_active_agents": 2})
    store.runtime.configure("project", "synthetic", {"max_active_agents": 1})
    assert jobs.schedule_agent("parent", usd_limit=10, max_attempts=2)
    assert len(jobs.active_agents("synthetic")) == 1
    store.close()
