from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from factory.machine import Blocked, State
from factory.store import Store


def test_project_admission_is_atomic_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    runs = [store.insert_run(linear_id=f"BAC-{n}", project="p", team="BAC") for n in range(8)]

    def admit(run_id: str) -> bool:
        connection = Store(path)
        try:
            return connection.runtime.admit(run_id, "p", 2)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(admit, [r.id for r in runs])) == 2
    assert store.runtime.db.execute("SELECT COUNT(*) FROM project_slots").fetchone()[0] == 2


def test_lowering_concurrency_drains_and_cancellation_releases_only_one_slot(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "factory.db")
    a, b, c = [store.insert_run(linear_id=f"BAC-{n}", project="p", team="BAC") for n in range(3)]
    assert store.runtime.admit(a.id, "p", 2)
    assert store.runtime.admit(b.id, "p", 2)
    assert store.runtime.admit(a.id, "p", 1)
    assert not store.runtime.admit(c.id, "p", 1)
    store.record_transition(
        a.id, from_state=State.APPROVED, to_state=State.CANCELLED, actor="human"
    )
    assert not store.runtime.admit(c.id, "p", 1)
    assert store.runtime.admit(c.id, "p", 2)


def test_invocation_metadata_and_policy_snapshots_are_immutable(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="BAC-1", project="p", team="BAC")
    store.runtime.start_invocation("i", run.id, 1, "builder", {"model": "a", "effort": "high"})
    with pytest.raises(ValueError, match="immutable"):
        store.runtime.start_invocation("i", run.id, 1, "builder", {"model": "b"})
    assert store.runtime.observe("i", 1, {"usage": 100})
    assert not store.runtime.observe("i", 1, {"usage": 200})
    first = store.runtime.snapshot_policy(run.id, {"profile": "core"})
    assert store.runtime.snapshot_policy(run.id, {"profile": "prototype"}) == first
    assert (
        store.runtime.snapshot_policy(run.id, {"profile": "prototype"}, replace=True)["revision"]
        == 2
    )


def test_existing_database_requires_explicit_migration(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    store = Store(path)
    for name in (
        "delegation_requests",
        "agent_leases",
        "runtime_certifications",
        "invocations",
        "operator_settings",
        "operator_events",
        "policy_snapshots",
        "project_slots",
        "failure_episodes",
    ):
        store.runtime.db.execute(f"DROP TABLE {name}")
    store.runtime.db.execute("PRAGMA user_version=4")
    store.close()
    with pytest.raises(Blocked, match="schema-approval-required"):
        Store(path)
    store = Store(path, migrate=True)
    assert store.integrity_ok()[0]


def test_cost_reconciliation_preserves_incomplete_accounting(tmp_path: Path) -> None:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="BAC-1", project="p", team="BAC")
    for _ in range(2):
        store.reconcile_cost(
            run.id,
            1,
            "build",
            model="known",
            input_tokens=100,
            output_tokens=10,
            cached_tokens=20,
            usd=1.25,
        )
    store.reconcile_cost(
        run.id,
        1,
        "review",
        model="unknown",
        input_tokens=10,
        output_tokens=2,
        cached_tokens=0,
        usd=None,
    )
    assert store.spend(run.id) == (110, 12, None)
    assert store.known_spend(run.id) == 1.25
