"""§21.1 — the transition table, asserted as a table."""

from __future__ import annotations

from itertools import pairwise

import pytest

from factory.machine import (
    HUMAN_ONLY,
    TERMINAL,
    TRANSITIONS,
    State,
    assert_table_is_sound,
    can,
    requires_human_rule,
)


def test_table_is_sound() -> None:
    assert_table_is_sound()


def test_every_state_has_a_row() -> None:
    assert set(TRANSITIONS) == set(State)


def test_terminal_states_have_no_exit() -> None:
    for state in TERMINAL:
        assert TRANSITIONS[State(state)] == frozenset()


def test_failed_is_not_terminal_because_james_can_reauthorise() -> None:
    assert State.FAILED not in TERMINAL
    assert can(State.FAILED, State.RESUMABLE)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (State.APPROVED, State.IMPLEMENTING),
        (State.CLAIMED, State.PR_READY),
        (State.WORKTREE_READY, State.VERIFYING),
        (State.COMPLETED, State.IMPLEMENTING),
        (State.PR_READY, State.COMPLETED),
    ],
)
def test_illegal_transitions_are_absent(source: State, target: State) -> None:
    assert not can(source, target)


_HUMAN_ROWS = sorted(HUMAN_ONLY.items(), key=lambda row: (str(row[0][0]), str(row[0][1])))


@pytest.mark.parametrize(("hop", "rule"), _HUMAN_ROWS)
def test_every_human_row_names_its_rule(hop: tuple[State, State], rule: str) -> None:
    source, target = hop
    assert requires_human_rule(source, target) == rule
    # A rule that reserves a hop the table does not contain is a rule that can never
    # fire, which reads as protection while providing none.
    assert can(source, target)


def test_merge_is_never_automatic() -> None:
    assert requires_human_rule(State.AWAITING_HUMAN, State.COMPLETED) == "merge-is-james"


def test_cancel_and_suspend_are_human_from_anywhere() -> None:
    assert requires_human_rule(State.IMPLEMENTING, State.CANCELLED) == "abandon-is-james"
    assert requires_human_rule(State.IMPLEMENTING, State.SUSPENDED) == "suspend-is-james"
    assert requires_human_rule(State.SUSPENDED, State.IMPLEMENTING) == "resume-is-james"


@pytest.mark.parametrize("state", [s for s in State if s not in TERMINAL])
def test_every_non_terminal_state_can_be_cancelled(state: State) -> None:
    """§5.3 writes the edge as `* -> cancelled`, so "anywhere" has to mean anywhere.

    `_WORKFLOW` listed it by hand and missed `resumable`, and the two halves of the
    machine then disagreed in the most misleading possible way: `requires_human_rule`
    answered `abandon-is-james` — governance saying "this is James's call" — while
    `can()` answered False, saying the hop did not exist. `cmd_cancel` guards its
    transition with `can()` and does its cleanup unguarded, so on 2026-08-21 it
    archived the attempt, removed the worktree, deleted the branch, moved Linear back
    to `Todo`, printed success, and left the row at `resumable` for the next
    `factory run` to refuse.
    """
    assert can(state, State.CANCELLED)
    assert requires_human_rule(state, State.CANCELLED) == "abandon-is-james"


def test_the_happy_path_is_automatic() -> None:
    path = [
        State.APPROVED,
        State.CLAIMED,
        State.CONTEXT_LOADED,
        State.SANDBOX_CREATING,
        State.SANDBOX_READY,
        State.WORKTREE_READY,
        State.IMPLEMENTING,
        State.VERIFYING,
        State.REVIEWING,
        State.PR_READY,
        State.AWAITING_HUMAN,
    ]
    for source, target in pairwise(path):
        assert can(source, target), f"{source} -> {target}"
        assert requires_human_rule(source, target) is None, f"{source} -> {target}"


# --------------------------------------------------------------------------------
# Every state a step can raise `Blocked` from must be able to reach `blocked`
# --------------------------------------------------------------------------------

#: A state absent from `TRANSITIONS[s]` cannot be transitioned to at all, so a step
#: raising `Blocked` from a state with no `blocked` edge leaves the run wearing a
#: `blocked_reason` at whatever state it was passing through, and `factory status`
#: reports the journey instead of the stop.
#:
#: Measured 2026-08-21: `factory run BAC-4` blocked in the preflight and came to rest at
#: `sandbox_creating`, with three transitions recorded and no `blocked` among them.
BLOCKING_STATES: list[tuple[State, str]] = [
    # `cmd_run` blocks an ineligible ticket here, and `claim._refuse_second_writer`
    # raises `project-busy` before the hop to `claimed`.
    (State.APPROVED, "eligibility and project-busy"),
    # `sandbox.build_spec` raises `clone-not-implemented` before the hop.
    (State.CONTEXT_LOADED, "clone-not-implemented"),
    # `sandbox.preflight` runs *inside* this state and raises `enforcement-disabled`.
    (State.SANDBOX_CREATING, "enforcement-disabled"),
    # `context.run` raises `vault-unresolved` and `no-issue-loaded`.
    (State.CLAIMED, "vault-unresolved"),
    # `worktree.run` raises on a branch collision.
    (State.SANDBOX_READY, "branch-exists"),
    # `plan.run` raises `no-issue-loaded`.
    (State.WORKTREE_READY, "no-issue-loaded"),
    # `implement.run` raises `schema-invalid` and the vault-allowlist block.
    (State.IMPLEMENTING, "schema-invalid and vault-write-outside-allowlist"),
]


@pytest.mark.parametrize(("state", "why"), BLOCKING_STATES, ids=lambda v: str(v))
def test_a_state_a_step_can_block_from_can_reach_blocked(state: State, why: str) -> None:
    assert can(state, State.BLOCKED), f"{state} raises Blocked ({why}) but cannot record it"


def test_blocked_is_reachable_from_every_non_terminal_state() -> None:
    """The general form of the case above.

    `lease-lost`, `run-vanished` and `no-worktree` are raised by helpers in
    `steps/__init__.py`, which any step calls from whatever state the run is in — so
    enumerating the states that can block is enumerating every state that is still
    running. `blocked` is a stop, not a step, exactly like `cancelled`.
    """
    for state in State:
        if state in TERMINAL or state is State.BLOCKED:
            continue
        assert can(state, State.BLOCKED), f"a run at {state} could not record a block"


def test_leaving_blocked_is_still_a_human_decision() -> None:
    # Widening the way *in* must not widen the way out: an automatic unblock would turn
    # every stop into a retry loop.
    for target in TRANSITIONS[State.BLOCKED] - {State.CANCELLED}:
        assert requires_human_rule(State.BLOCKED, target) == "unblock-is-a-judgement"


def test_a_blocked_run_can_resume_into_the_state_that_blocked_it() -> None:
    # FRO-6 was blocked *at* verifying with a complete, gate-passing implementation. The
    # cheap repair is to re-enter the state that blocked rather than spend another 10
    # M-token implement, and the only way that is expressible is `blocked -> verifying`
    # (and its reviewing twin). Both are the same human judgement as the existing exits.
    assert can(State.BLOCKED, State.VERIFYING)
    assert can(State.BLOCKED, State.REVIEWING)
    assert requires_human_rule(State.BLOCKED, State.VERIFYING) == "unblock-is-a-judgement"
    assert requires_human_rule(State.BLOCKED, State.REVIEWING) == "unblock-is-a-judgement"
