"""§16.3 and §16.3a — the decision that runs when everything else has already gone wrong.

Table-driven and entirely pure. The point of `recovery.decide` taking values rather than
a `Context` is that this file needs no database, no git and no sandbox: the ladder is
asserted rung by rung, and a change to it fails here before it fails at 3 a.m.
"""

from __future__ import annotations

import pytest

from factory.recovery import Disposition, backoff_seconds, decide, ladder_rung

DEFAULTS = {"max_attempts": 3, "max_total_attempts": 5}


def verdict(attempts_spent: int, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "attempts_spent": attempts_spent,
        "attempts_in_state": attempts_spent,
        "session_id": "01a0-thread",
        "interrupted_by": None,
        **DEFAULTS,
    }
    kwargs.update(overrides)
    return decide(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------
# §16.3a — the ladder, rung by rung
# --------------------------------------------------------------------------------


def test_the_second_attempt_resumes_the_session_rather_than_starting_over() -> None:
    # Rung 2 is "same prompt plus the failure evidence, resumed session where possible".
    result = verdict(1)
    assert result.disposition is Disposition.RESUME  # type: ignore[attr-defined]
    assert result.rung == 2  # type: ignore[attr-defined]


def test_the_third_attempt_rewinds_to_planning_instead_of_repeating() -> None:
    # The whole reason the ladder exists: a third run of the same prompt against the
    # same context produces the same failure, so rung 3 changes the context.
    result = verdict(2)
    assert result.disposition is Disposition.REWIND  # type: ignore[attr-defined]
    assert result.reason == "rewind-to-planning"  # type: ignore[attr-defined]


def test_the_fourth_attempt_is_failed_and_not_a_longer_wait() -> None:
    result = verdict(3)
    assert result.disposition is Disposition.FAIL  # type: ignore[attr-defined]


def test_the_rung_is_one_based_and_counts_the_attempt_about_to_start() -> None:
    assert ladder_rung(0) == 1
    assert ladder_rung(2) == 3


# --------------------------------------------------------------------------------
# §16.3 — resume versus restart
# --------------------------------------------------------------------------------


def test_no_session_id_means_restart_not_resume() -> None:
    # `codex exec resume --last` is never an option: on a machine running several
    # tickets it picks a session at random. No id means a fresh exec against the same
    # worktree, which is the branch that keeps the work.
    result = verdict(1, session_id=None)
    assert result.disposition is Disposition.RESTART  # type: ignore[attr-defined]
    assert result.reason == "no-session-id"  # type: ignore[attr-defined]


@pytest.mark.parametrize("operation", ["merge", "rebase", "cherry-pick"])
def test_a_worktree_stopped_mid_operation_restarts_rather_than_resuming(operation: str) -> None:
    # Resuming a session into a tree with conflict markers in it hands the model a
    # repository it did not leave.
    result = verdict(1, interrupted_by=operation)
    assert result.disposition is Disposition.RESTART  # type: ignore[attr-defined]
    assert operation in result.reason  # type: ignore[attr-defined]


def test_the_worktree_is_never_a_reason_to_fail() -> None:
    # §16.3's restart branch says the worktree is LEFT AS IS. A dirty tree changes how
    # the next attempt starts; it never ends the run.
    assert verdict(1, interrupted_by="rebase", session_id=None).disposition is (  # type: ignore[attr-defined]
        Disposition.RESTART
    )


# --------------------------------------------------------------------------------
# §16.4 — the two ceilings count different things
# --------------------------------------------------------------------------------


def test_the_per_state_ceiling_fails_before_the_per_run_one() -> None:
    result = verdict(1, attempts_in_state=3)
    assert result.disposition is Disposition.FAIL  # type: ignore[attr-defined]
    assert result.reason == "max-attempts-in-state"  # type: ignore[attr-defined]


def test_the_per_run_ceiling_stops_a_run_that_cycled_through_states() -> None:
    # Three states at two attempts each stays under the per-state ceiling every time,
    # and must still stop. Collapsing the two numbers into one would let it run forever.
    result = verdict(5, attempts_in_state=1)
    assert result.disposition is Disposition.FAIL  # type: ignore[attr-defined]
    assert result.reason == "max-total-attempts"  # type: ignore[attr-defined]


def test_the_backoff_is_zero_sixty_three_hundred_and_then_stops_growing() -> None:
    assert [backoff_seconds(n) for n in (0, 1, 2)] == [0, 60, 300]
    # Not exponential beyond that: a fourth try is a `failed`, not a longer wait, so
    # there is no fourth number to pick.
    assert backoff_seconds(9) == 300
