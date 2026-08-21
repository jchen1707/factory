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
