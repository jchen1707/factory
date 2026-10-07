"""The subscription hold: a stream's rate-limit report, retained by collect, read by the guard."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from factory import accounting
from factory.execution import rate_limit_hold
from factory.routing import DEFAULT_HOLD_AT
from factory.store import Store
from tests.support import claude_stream

NOW = time.time()
SOON = int(NOW) + 3600
PAST = int(NOW) - 60
LATER = int(NOW) + 5 * 86400


def _collect(store: Store, tmp_path: Path, name: str, *events: claude_stream.Wire) -> None:
    run = store.insert_run(linear_id=f"SYN-{name}", project="synthetic", team="SYN")
    invocation = f"{run.id}:1:implement"
    store.runtime.start_invocation(
        invocation, run.id, 1, "implement", {"model": claude_stream.MODEL, "cost_step": "implement"}
    )
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(f"{line}\n" for line in claude_stream.lines(*events)))
    accounting.collect_invocation(store, invocation, path)


def _held(store: Store) -> str | None:
    return rate_limit_hold(accounting.limits(store, NOW), DEFAULT_HOLD_AT)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "factory.db")


@pytest.mark.parametrize(
    ("five_hour", "seven_day", "resets_at", "seven_day_resets_at", "held"),
    [
        (0.92, 0.10, SOON, LATER, "five_hour window at 92%"),
        (0.90, 0.10, SOON, LATER, "five_hour window at 90%"),
        (0.89, 0.94, SOON, LATER, None),
        (0.92, 0.10, PAST, LATER, None),
        # The five-hour window has already reset; the seven-day one is full and has not.
        (0.20, 0.96, PAST, LATER, "seven_day window at 96%"),
        (0.20, 0.96, PAST, PAST, None),
    ],
)
def test_a_window_at_its_threshold_holds_until_its_own_reset(
    store: Store,
    tmp_path: Path,
    five_hour: float,
    seven_day: float,
    resets_at: int,
    seven_day_resets_at: int,
    held: str | None,
) -> None:
    _collect(
        store,
        tmp_path,
        "one",
        claude_stream.init(),
        claude_stream.rate_limit(
            five_hour=five_hour,
            seven_day=seven_day,
            resets_at=resets_at,
            seven_day_resets_at=seven_day_resets_at,
        ),
        claude_stream.result_success(),
    )

    reason = _held(store)

    if held is None:
        assert reason is None
    else:
        assert held in (reason or "")


def test_a_full_window_that_has_reset_gives_way_to_the_current_one(
    store: Store, tmp_path: Path
) -> None:
    last_window = claude_stream.rate_limit(five_hour=0.99, resets_at=PAST)
    current = claude_stream.rate_limit(five_hour=0.91, resets_at=SOON)
    _collect(store, tmp_path, "last-window", claude_stream.init(), last_window)
    _collect(store, tmp_path, "current", claude_stream.init(), current)

    assert "five_hour window at 91%" in (_held(store) or "")


def test_a_stale_report_collected_late_does_not_hide_a_fuller_one(
    store: Store, tmp_path: Path
) -> None:
    # A long build reports once, at its first response, and is collected for hours after:
    # its 40% must not mask the 95% another run reported since in the same window.
    _collect(
        store,
        tmp_path,
        "full",
        claude_stream.init(),
        claude_stream.rate_limit(five_hour=0.95, resets_at=SOON),
    )
    _collect(
        store,
        tmp_path,
        "long-build",
        claude_stream.init(),
        claude_stream.rate_limit(five_hour=0.4, resets_at=SOON),
    )

    assert "five_hour window at 95%" in (_held(store) or "")


def test_a_429_holds_until_the_reported_reset_whatever_the_utilisation(
    store: Store, tmp_path: Path
) -> None:
    _collect(store, tmp_path, "refused", *_refused(resets_at=SOON))

    reason = _held(store)

    assert "429" in (reason or "")


def test_two_refusals_hold_until_the_later_reset(store: Store, tmp_path: Path) -> None:
    _collect(store, tmp_path, "weekly", *_refused(resets_at=LATER))
    _collect(store, tmp_path, "session", *_refused(resets_at=SOON))

    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(LATER)) in (_held(store) or "")


def test_a_refusal_whose_reset_has_passed_holds_nothing(store: Store, tmp_path: Path) -> None:
    _collect(store, tmp_path, "refused", *_refused(resets_at=PAST))

    assert _held(store) is None


def test_no_report_no_hold(store: Store) -> None:
    assert _held(store) is None


def _refused(*, resets_at: int) -> list[claude_stream.Wire]:
    return [
        claude_stream.init(),
        claude_stream.rate_limit(five_hour=0.3, resets_at=resets_at),
        claude_stream.unmeasured_429_result(),
    ]
