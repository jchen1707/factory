"""§21.1 — the claim's uniqueness, the lease, and the effects ledger."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from factory.machine import TERMINAL, State
from factory.store import _SCHEMA, LIVE_INDEX_SCHEMA_VERSION, SCHEMA_VERSION, Store, marker


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "factory.db")


def test_duplicate_linear_id_cannot_create_a_second_run(store: Store) -> None:
    first = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    second = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    assert first.id == second.id
    assert len(store.all_runs()) == 1


def _move(store: Store, run_id: str, to_state: State) -> None:
    """`update_run` refuses `state` on purpose, so tests move a run the way the factory
    does: by recording the transition."""
    store.record_transition(run_id, from_state=None, to_state=to_state, actor="test")


# --------------------------------------------------------------------------------
# One *live* run per ticket — the narrowing that makes `cancel` + `run` work
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("live", [State.APPROVED, State.IMPLEMENTING, State.BLOCKED])
def test_a_live_run_still_blocks_a_second_one(store: Store, live: State) -> None:
    """§7.2 unchanged: while a run is live, its ticket cannot start another.

    `blocked` is in the list on purpose. It is a stop, not an end, so a blocked run
    keeps its ticket until a human cancels it — which is the whole reason `cmd_run`
    tells you to cancel rather than just re-running.
    """
    first = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    _move(store, first.id, live)
    second = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    assert second.id == first.id
    assert len(store.all_runs()) == 1


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_a_terminal_run_does_not_block_the_next_one(store: Store, terminal: State) -> None:
    """The defect this index exists for: a cancelled row used to own its ticket forever,
    so `factory cancel BAC-4 && factory run BAC-4` — the rollback `cmd_run` advertises
    and the only retry Phase 1 has — refused at `already at cancelled`."""
    first = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    _move(store, first.id, terminal)
    second = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")

    assert second.id != first.id
    assert second.state is State.APPROVED
    # Attempt 0, so the next attempt directory is `run/1` exactly as §19 expects.
    assert second.attempt == 0
    assert len(store.all_runs()) == 2


def test_the_abandoned_run_keeps_its_own_ledger(store: Store) -> None:
    """A successor must not inherit or erase its predecessor's evidence: the effects
    ledger is keyed by run id, and reconciliation would skip a real Linear write if the
    new run could see the old run's confirmed rows."""
    first = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.intend_effect(first.id, 0, "claim", "linear", "state:in-progress")
    store.confirm_effect(first.id, 0, "claim", "linear", "state:in-progress", "In Progress")
    _move(store, first.id, State.CANCELLED)

    second = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")

    assert len(store.effects(first.id)) == 1
    assert store.effects(second.id) == []
    assert store.find_effect(second.id, 0, "claim", "linear", "state:in-progress") is None


def test_run_by_ticket_returns_the_newest_run(store: Store) -> None:
    first = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    _move(store, first.id, State.CANCELLED)
    second = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    # Same wall-clock second as `first`, so only the tiebreak distinguishes them.
    assert store.run_by_ticket("BAC-4").id == second.id  # type: ignore[union-attr]


def test_terminal_states_are_pinned_to_the_live_run_index() -> None:
    """`_LIVE_RUN_INDEX` is derived from `TERMINAL` at import, but a database keeps the
    index it was built with. Changing this set therefore needs a new schema version and a
    migration that rebuilds the index, and this test is the tripwire that says so.

    Pinned to `LIVE_INDEX_SCHEMA_VERSION` rather than to `SCHEMA_VERSION`, so a column
    added for an unrelated reason does not silently retire the tripwire by bumping the
    number this asserts."""
    assert frozenset({State.COMPLETED, State.CANCELLED}) == TERMINAL
    assert LIVE_INDEX_SCHEMA_VERSION == 2


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


# --------------------------------------------------------------------------------
# The 1 -> 2 migration
# --------------------------------------------------------------------------------


#: `runs` as SCHEMA_VERSION 1 wrote it: the column-level UNIQUE this migration removes.
_V1_RUNS = """
CREATE TABLE runs (
  id TEXT PRIMARY KEY, linear_id TEXT NOT NULL UNIQUE,
  project TEXT NOT NULL, team TEXT NOT NULL,
  branch TEXT, base_ref TEXT, worktree TEXT,
  state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_expires_at INTEGER,
  blocked_reason TEXT, pr_url TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


def _v1_database(path: Path) -> str:
    """A version 1 database holding one cancelled run and its ledger — the exact shape
    `state/factory.db` was in when BAC-4's first run stopped."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(_V1_RUNS + _SCHEMA[_SCHEMA.index("CREATE TABLE transitions") :])
    now = int(time.time())
    conn.execute(
        "INSERT INTO runs (id, linear_id, project, team, state, attempt, created_at, updated_at) "
        "VALUES ('03cda9bfebe644d7','BAC-4','python-harness','BAC','cancelled',0,?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO transitions (run_id, from_state, to_state, actor, rule, at) "
        "VALUES ('03cda9bfebe644d7','approved','claimed','auto',NULL,?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO effects (run_id, attempt, step, system, key, status, external_id, at) "
        "VALUES ('03cda9bfebe644d7',0,'claim','linear','state:in-progress','confirmed','x',?)",
        (now,),
    )
    conn.execute("PRAGMA user_version=1")
    conn.close()
    return "03cda9bfebe644d7"


def test_migration_1_to_2_keeps_the_history_it_migrates(tmp_path: Path) -> None:
    """Dropping a table with `foreign_keys=ON` would take the ledger with it. The audit
    history surviving is the property that makes this migration safe to run on the real
    database rather than a reason to start a fresh one."""
    path = tmp_path / "factory.db"
    old_id = _v1_database(path)

    store = Store(path)

    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.run_by_id(old_id) is not None
    assert [r["to_state"] for r in store.transitions(old_id)] == ["claimed"]
    assert len(store.effects(old_id)) == 1
    assert store.integrity_ok()[0]
    assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_database_two_versions_behind_takes_every_step_not_just_the_first(
    tmp_path: Path,
) -> None:
    """The migration walks the whole gap. It used to apply one step and then stamp the
    version as current, which meant a version-1 database would run the 1 -> 2 rebuild and
    then claim to be at 3 with none of 3's columns. Nothing had two versions to cross
    until `full_review` was added, which is the only reason it never fired."""
    path = tmp_path / "factory.db"
    _v1_database(path)

    store = Store(path)

    columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(runs)")}
    assert "full_review" in columns
    run = store.insert_run(linear_id="BAC-9", project="python-harness", team="BAC")
    assert run.full_review is False
    # Foreign keys are restored after the rebuild, not left off.
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_1_to_2_unblocks_the_rerun_that_v1_refused(tmp_path: Path) -> None:
    """The end-to-end point of the migration: against v1 this returned the cancelled row
    and `cmd_run` printed `already at cancelled`."""
    path = tmp_path / "factory.db"
    old_id = _v1_database(path)

    store = Store(path)
    fresh = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")

    assert fresh.id != old_id
    assert fresh.state is State.APPROVED
    assert store.run_by_ticket("BAC-4").id == fresh.id  # type: ignore[union-attr]


def test_migration_is_not_rerun_on_an_already_current_database(tmp_path: Path) -> None:
    path = tmp_path / "factory.db"
    _v1_database(path)
    Store(path).close()
    store = Store(path)  # second open must be a no-op, not a second rebuild
    assert len(store.all_runs()) == 1


# --------------------------------------------------------------------------------
# The re-run ceiling, and what `--authorise` has to do to it
# --------------------------------------------------------------------------------


def _orphan_cycle(store: Store, run_id: str, times: int) -> None:
    """`reviewing -> resumable -> reviewing`, the shape reap writes when it orphans an
    attempt and recovery re-runs it."""
    for _ in range(times):
        store.record_transition(
            run_id,
            from_state=State.REVIEWING,
            to_state=State.RESUMABLE,
            actor="auto",
            rule="attempt-orphaned",
        )
        store.record_transition(
            run_id, from_state=State.RESUMABLE, to_state=State.REVIEWING, actor="auto"
        )


def test_re_entries_are_counted_for_a_run_no_human_has_re_authorised(store: Store) -> None:
    run = store.insert_run(linear_id="FRO-7", project="frontend-harness", team="FRO")
    _orphan_cycle(store, run.id, 3)
    assert store.resumable_reentries(run.id, State.REVIEWING) == 3


def test_re_authorising_restarts_the_re_run_budget(store: Store) -> None:
    """`--authorise` is §16.4's explicit "spend again on this run", and the only way the
    ceiling ever produces `failed` is by being reached — so counting for all time made the
    flag inert in exactly the case it names. FRO-7 run `b1aa9785bbe44663` failed on
    `max-reruns-reviewing`, was re-authorised, and the next tick failed it again on the
    same three historical rows: `failed -> resumable -> failed`, with a good
    implementation stranded behind it.
    """
    run = store.insert_run(linear_id="FRO-7", project="frontend-harness", team="FRO")
    _orphan_cycle(store, run.id, 3)
    store.record_transition(
        run.id,
        from_state=State.RESUMABLE,
        to_state=State.FAILED,
        actor="auto",
        rule="max-reruns-reviewing",
    )

    store.record_transition(
        run.id,
        from_state=State.FAILED,
        to_state=State.RESUMABLE,
        actor="human",
        rule="reauthorise-spend",
    )

    assert store.resumable_reentries(run.id, State.REVIEWING) == 0


def test_re_entries_after_a_re_authorisation_count_against_the_new_budget(store: Store) -> None:
    # The flag restarts the budget; it does not remove the ceiling.
    run = store.insert_run(linear_id="FRO-7", project="frontend-harness", team="FRO")
    _orphan_cycle(store, run.id, 3)
    store.record_transition(
        run.id,
        from_state=State.FAILED,
        to_state=State.RESUMABLE,
        actor="human",
        rule="reauthorise-spend",
    )
    _orphan_cycle(store, run.id, 2)

    assert store.resumable_reentries(run.id, State.REVIEWING) == 2


def test_only_the_most_recent_re_authorisation_bounds_the_count(store: Store) -> None:
    # Two re-authorisations: the earlier one is a spent budget, not this one's.
    run = store.insert_run(linear_id="FRO-7", project="frontend-harness", team="FRO")
    for _ in range(2):
        store.record_transition(
            run.id,
            from_state=State.FAILED,
            to_state=State.RESUMABLE,
            actor="human",
            rule="reauthorise-spend",
        )
        _orphan_cycle(store, run.id, 1)

    assert store.resumable_reentries(run.id, State.REVIEWING) == 1
