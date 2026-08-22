"""§18.5 — the console's event parser and its context percentage.

The load-bearing property is the one the plan states twice: the percentage is either
shown from a confirmed measurement or hidden **with the reason stated**, and it is never
estimated. P0-7 (`docs/discovery/codex-events.md`) refuted the plan's original reading of
the stream — there is no `model_context_window`, no `context_window`, no `tokens_used` and
no `percent` in `codex exec --json` — so the numerator comes from the turn's usage and the
denominator from the model catalogue, and every branch that cannot produce one says which.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from factory.console.events import context_percentage, read_turn_view
from factory.machine import State
from factory.routing import ModelFacts, Role, Routing

#: The exact `turn.completed` shape P0-7 captured off codex-cli 0.147.0 — no `total_tokens`
#: (the factory sums it itself) and no window anywhere in the stream.
TURN_COMPLETED: dict[str, Any] = {
    "type": "turn.completed",
    "usage": {
        "input_tokens": 136000,
        "cached_input_tokens": 11008,
        "cache_write_input_tokens": 0,
        "output_tokens": 900,
        "reasoning_output_tokens": 240,
    },
}

THREAD_STARTED: dict[str, Any] = {
    "type": "thread.started",
    "thread_id": "01a02738-0315-7470-b41e-510699d1",
}


def _events(tmp_path: Path, *events: Mapping[str, Any]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _routing(*, window: int = 272000, percent: int = 95, known: bool = True) -> Routing:
    models: dict[str, ModelFacts] = {}
    if known:
        models["gpt-5.6-sol"] = ModelFacts(
            slug="gpt-5.6-sol",
            display="sol",
            default_effort="high",
            supported_efforts=("high",),
            context_window=window,
            effective_percent=percent,
            hidden=False,
        )
    return Routing(
        provider="codex",
        roles={"builder": Role(name="builder", model="gpt-5.6-sol", effort="high")},
        models=models,
        usd_per_run=20.0,
        usd_warn_at=12.0,
    )


def test_the_latest_turn_supplies_the_numerator(tmp_path: Path) -> None:
    # Two turns: the usage of the *latest* is the context in the window, not the sum. The
    # window holds a conversation, and codex reports the whole conversation each turn — so
    # adding them would double-count and show 200% on a healthy run.
    first: dict[str, Any] = {
        "type": "turn.completed",
        "usage": {**TURN_COMPLETED["usage"], "input_tokens": 50000},
    }
    path = _events(tmp_path, THREAD_STARTED, first, TURN_COMPLETED)

    view = read_turn_view(path)

    assert view is not None
    assert view.has_turn
    assert view.input_tokens == 136000
    assert view.cached_input_tokens == 11008


def test_the_percentage_is_the_turn_over_the_usable_window(tmp_path: Path) -> None:
    # 136000 / (272000 * 95 / 100) = 136000 / 258400 ≈ 0.526.
    view = read_turn_view(_events(tmp_path, THREAD_STARTED, TURN_COMPLETED))

    pct, reason = context_percentage(view, State.IMPLEMENTING, _routing())

    assert reason is None
    assert pct is not None
    assert round(pct, 3) == 0.526


def test_no_window_on_file_hides_the_percentage_and_says_so(tmp_path: Path) -> None:
    # The acceptance row: shown from the confirmed field, or hidden with the reason
    # stated. Never estimated — a console that guessed a window would report context
    # pressure it cannot defend, on the one number an operator uses to decide whether to
    # rewind to planning (§16.3a).
    view = read_turn_view(_events(tmp_path, THREAD_STARTED, TURN_COMPLETED))

    pct, reason = context_percentage(view, State.IMPLEMENTING, _routing(known=False))

    assert pct is None
    assert reason is not None
    assert "gpt-5.6-sol" in reason


def test_a_turn_still_running_hides_the_percentage(tmp_path: Path) -> None:
    # `turn.completed` is the only event carrying usage, so mid-turn there is no numerator.
    view = read_turn_view(_events(tmp_path, THREAD_STARTED))

    pct, reason = context_percentage(view, State.IMPLEMENTING, _routing())

    assert pct is None
    assert reason == "the agent has not completed a turn yet"


def test_a_state_with_no_agent_hides_the_percentage(tmp_path: Path) -> None:
    # `verifying` runs a node gate report and `pr_ready` is a host-side push: neither has a
    # model, so neither has a context window. Showing the builder's percentage there would
    # attribute one state's pressure to another.
    view = read_turn_view(_events(tmp_path, THREAD_STARTED, TURN_COMPLETED))

    pct, reason = context_percentage(view, State.VERIFYING, _routing())

    assert pct is None
    assert reason is not None
    assert "verifying" in reason


def test_a_missing_stream_is_none_rather_than_an_error(tmp_path: Path) -> None:
    assert read_turn_view(tmp_path / "nope.jsonl") is None


def test_a_half_flushed_trailing_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    # The normal state of a file a live agent is writing. `agent.codex.parse_events` raises
    # here — correctly, it advances the state machine on that evidence — but the console
    # only displays, and a board that 500'd because a run was mid-write would be useless
    # exactly when it is being watched.
    path = _events(tmp_path, THREAD_STARTED, TURN_COMPLETED)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"item.comp')

    view = read_turn_view(path)

    assert view is not None
    assert view.input_tokens == 136000


def test_the_activity_column_reads_the_last_completed_item(tmp_path: Path) -> None:
    command: dict[str, Any] = {
        "type": "item.completed",
        "item": {"id": "item_9", "type": "command_execution", "command": "uv run pytest -q"},
    }
    view = read_turn_view(_events(tmp_path, THREAD_STARTED, TURN_COMPLETED, command))

    assert view is not None
    assert view.activity == "uv run pytest -q"
