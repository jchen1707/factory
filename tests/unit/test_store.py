"""§21.1 — the UNIQUE claim, the lease, and the effects ledger."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from factory.machine import State
from factory.store import Store, marker


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "factory.db")


def test_duplicate_linear_id_cannot_create_a_second_run(store: Store) -> None:
    first = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    second = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    assert first.id == second.id
    assert len(store.all_runs()) == 1


def test_lease_is_exclusive_until_it_expires(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    assert store.acquire_lease(run.id, ttl_seconds=60, owner="host:1")
    assert not store.acquire_lease(run.id, ttl_seconds=60, owner="host:2")
    # The holder renews its own without contest.
    assert store.acquire_lease(run.id, ttl_seconds=60, owner="host:1")


def test_an_expired_lease_can_be_stolen_but_a_fresh_one_cannot(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.acquire_lease(run.id, ttl_seconds=-1, owner="dead:1")
    assert store.acquire_lease(run.id, ttl_seconds=60, owner="alive:2")
    assert not store.acquire_lease(run.id, ttl_seconds=60, owner="thief:3")


def test_holds_lease_is_false_for_another_owner_and_after_release(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.acquire_lease(run.id, ttl_seconds=60, owner="host:1")
    assert store.holds_lease(run.id, owner="host:1")
    assert not store.holds_lease(run.id, owner="host:2")
    store.release_lease(run.id)
    assert not store.holds_lease(run.id, owner="host:1")


def test_effects_are_unique_per_run_attempt_step_system_key(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    first = store.intend_effect(run.id, 0, "claim", "linear", "comment:claimed")
    again = store.intend_effect(run.id, 0, "claim", "linear", "comment:claimed")
    assert first.at == again.at
    assert len(store.effects(run.id)) == 1

    store.confirm_effect(run.id, 0, "claim", "linear", "comment:claimed", "comment-123")
    found = store.find_effect(run.id, 0, "claim", "linear", "comment:claimed")
    assert found is not None
    assert found.status == "confirmed"
    assert found.external_id == "comment-123"


def test_marker_is_stable_and_searchable() -> None:
    assert marker("abc123", 2, "claim") == "factory:abc123:2:claim"


def test_transitions_record_actor_and_rule(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.record_transition(
        run.id, from_state=State.APPROVED, to_state=State.CLAIMED, actor="auto", rule=None
    )
    store.record_transition(
        run.id,
        from_state=State.CLAIMED,
        to_state=State.CANCELLED,
        actor="human",
        rule="abandon-is-james",
    )
    rows = store.transitions(run.id)
    assert [r["to_state"] for r in rows] == ["claimed", "cancelled"]
    assert rows[1]["actor"] == "human"
    assert rows[1]["rule"] == "abandon-is-james"
    assert store.run_by_id(run.id).state is State.CANCELLED  # type: ignore[union-attr]


def test_cost_records_unknown_price_as_null_not_zero(store: Store) -> None:
    # A zero reads as a free run rather than an unpriced one, and the difference
    # decides whether the $20 ceiling means anything.
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.record_cost(
        run.id,
        1,
        "implement",
        model="gpt-5.6-sol",
        input_tokens=1000,
        output_tokens=50,
        cached_tokens=900,
        usd=None,
    )
    tokens_in, tokens_out, usd = store.spend(run.id)
    assert (tokens_in, tokens_out) == (1000, 50)
    assert usd is None


def test_active_runs_only_counts_writing_states(store: Store) -> None:
    busy = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    idle = store.insert_run(linear_id="BAC-5", project="python-harness", team="BAC")
    store.record_transition(busy.id, from_state=None, to_state=State.IMPLEMENTING, actor="auto")
    store.record_transition(idle.id, from_state=None, to_state=State.BLOCKED, actor="auto")
    assert [r.linear_id for r in store.active_runs_for_project("python-harness")] == ["BAC-4"]


def test_update_run_refuses_a_column_it_does_not_own(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    with pytest.raises(ValueError, match="does not write"):
        store.update_run(run.id, state="completed")


def test_integrity_check_passes_on_a_fresh_database(store: Store) -> None:
    ok, detail = store.integrity_ok()
    assert ok, detail


def test_session_id_round_trips(store: Store) -> None:
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.start_attempt(
        run.id,
        1,
        State.IMPLEMENTING,
        sandbox="factory-build-python-harness",
        artifact_dir="artifacts/a",
    )
    assert store.session_id(run.id, 1, State.IMPLEMENTING) is None
    store.set_session_id(run.id, 1, State.IMPLEMENTING, "01a0-thread")
    assert store.session_id(run.id, 1, State.IMPLEMENTING) == "01a0-thread"
    store.finish_attempt(run.id, 1, State.IMPLEMENTING, exit_code=0, outcome="implemented")
    assert store.session_id(run.id, 1, State.IMPLEMENTING) == "01a0-thread"
    assert time.time() > 0
