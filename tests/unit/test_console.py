"""§18.5 — the console's fold of a Claude launch's stream and its context percentage.

Every stream here is a real capture from `tests/fixtures/claude/` (Claude Code 2.1.292),
cut or edited only where a test needs a state the captures do not hold. The percentage is
either shown from the last message's prompt over the model's `context_window`, or hidden
with the reason stated; it is never estimated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.console.events import context_percentage, lane_of, read_stream
from factory.machine import State
from factory.routing import ModelFacts, Role, Routing

FIXTURES = Path(__file__).parents[1] / "fixtures" / "claude"
MODEL = "claude-opus-5-5"


def _lines(name: str) -> list[str]:
    return (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()


def _write(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _routing(window: int = 100_000) -> Routing:
    return Routing(
        roles={"builder": Role(name="builder", model=MODEL, effort="medium")},
        models={MODEL: ModelFacts(MODEL, window, ("medium",), "medium")},
        usd_per_run=20.0,
        usd_warn_at=12.0,
    )


def test_a_build_stream_folds_to_its_tool_calls_with_measured_durations() -> None:
    view = read_stream(FIXTURES / "build-tools.jsonl")

    assert view is not None
    assert [(c.tool, c.summary, c.outcome) for c in view.tool_calls] == [
        ("Write", "/work/repo/notes.txt", "ok"),
        ("Edit", "/work/repo/notes.txt", "ok"),
        (
            "Bash",
            "touch bash-made.txt && git add -A && git -c commit.gpgsign=false commit -qm probe"
            " && git log --oneline | head -1",
            "ok",
        ),
    ]
    # tool_result timestamp minus the issuing assistant event's: 40.633→40.670,
    # 41.603→41.647, 42.434→43.437.
    durations = [c.duration_s for c in view.tool_calls]
    assert durations == pytest.approx([0.037, 0.044, 1.003], abs=1e-6)
    assert [c.index for c in view.tool_calls] == [0, 1, 2]
    assert view.activity == "done"


def test_the_context_numerator_is_the_last_message_prompt() -> None:
    # The last message reports input 8, cache read 14473, cache write 888; the first
    # (7566 + 6907 + 9) must not be the one that counts.
    view = read_stream(FIXTURES / "build-tools.jsonl")

    assert view is not None
    assert view.context_tokens == 8 + 14473 + 888
    pct, reason = context_percentage(view, State.IMPLEMENTING, MODEL, _routing())
    assert pct == pytest.approx(15369 / 100_000)
    assert reason is None


def test_a_denied_call_is_shown_as_denied() -> None:
    view = read_stream(FIXTURES / "denied.jsonl")

    assert view is not None
    assert [(c.tool, c.outcome) for c in view.tool_calls] == [("Write", "denied")]


def test_a_failed_call_is_an_error(tmp_path: Path) -> None:
    lines = _lines("build-tools")
    bash_result = next(
        i
        for i, text in enumerate(lines)
        if '"tool_use_id":"toolu_01J3SHKytGzN8tNDR8ECZGff"' in text
    )
    wire = json.loads(lines[bash_result])
    wire["message"]["content"][0]["is_error"] = True
    lines[bash_result] = json.dumps(wire)

    view = read_stream(_write(tmp_path, lines))

    assert view is not None
    assert [c.outcome for c in view.tool_calls] == ["ok", "ok", "error"]


def test_a_call_still_running_has_no_duration(tmp_path: Path) -> None:
    lines = _lines("build-tools")
    issued = next(i for i, text in enumerate(lines) if '"name":"Bash"' in text)

    view = read_stream(_write(tmp_path, lines[: issued + 1]))

    assert view is not None
    running = view.tool_calls[-1]
    assert (running.tool, running.outcome, running.duration_s) == ("Bash", "running", None)
    assert view.activity == running.summary


def test_a_launch_that_never_reached_the_api_has_no_context() -> None:
    # The CLI writes a `<synthetic>` message with zero usage when it is not logged in.
    view = read_stream(FIXTURES / "not-logged-in.jsonl")

    assert view is not None
    assert view.context_tokens is None
    assert view.activity == "Not logged in · Please run /login"
    pct, reason = context_percentage(view, State.IMPLEMENTING, MODEL, _routing())
    assert pct is None
    assert reason == "the agent has not answered yet"


def test_a_torn_last_line_and_a_corrupt_middle_line_do_not_raise(tmp_path: Path) -> None:
    lines = _lines("build-tools")
    torn = read_stream(_write(tmp_path, [*lines, lines[3][: len(lines[3]) // 2]]))
    assert torn is not None
    assert len(torn.tool_calls) == 3

    issued = next(i for i, text in enumerate(lines) if '"name":"Bash"' in text)
    corrupt = read_stream(_write(tmp_path, [*lines[:issued], "{not json", *lines[issued:]]))
    assert corrupt is not None
    assert [c.tool for c in corrupt.tool_calls] == ["Write", "Edit"]


def test_a_line_separator_inside_a_tool_result_does_not_cut_the_stream(tmp_path: Path) -> None:
    # Claude Code's JSON leaves U+2028 unescaped inside strings; it is not a line break.
    lines = _lines("build-tools")
    first_result = next(i for i, text in enumerate(lines) if '"type":"tool_result"' in text)
    wire = json.loads(lines[first_result])
    wire["message"]["content"][0]["content"] = "line one\u2028line two"
    lines[first_result] = json.dumps(wire, ensure_ascii=False)

    view = read_stream(_write(tmp_path, lines))

    assert view is not None
    assert [c.outcome for c in view.tool_calls] == ["ok", "ok", "ok"]


def test_a_timestamp_that_is_not_a_string_leaves_the_duration_unknown(tmp_path: Path) -> None:
    lines = _lines("build-tools")
    first_result = next(i for i, text in enumerate(lines) if '"type":"tool_result"' in text)
    wire = json.loads(lines[first_result])
    wire["timestamp"] = 1791346300
    lines[first_result] = json.dumps(wire)

    view = read_stream(_write(tmp_path, lines))

    assert view is not None
    assert view.tool_calls[0].duration_s is None


def test_a_tool_input_that_is_not_an_object_is_summarised_by_name(tmp_path: Path) -> None:
    lines = _lines("build-tools")
    first_use = next(i for i, text in enumerate(lines) if '"name":"Write"' in text)
    wire = json.loads(lines[first_use])
    wire["message"]["content"][0]["input"] = ["not", "an", "object"]
    lines[first_use] = json.dumps(wire)

    view = read_stream(_write(tmp_path, lines))

    assert view is not None
    assert view.tool_calls[0].summary == "Write"


def test_a_missing_stream_is_none(tmp_path: Path) -> None:
    assert read_stream(tmp_path / "absent.jsonl") is None
    assert read_stream(tmp_path) is None


def test_the_percentage_is_hidden_with_its_reason() -> None:
    view = read_stream(FIXTURES / "build-tools.jsonl")
    assert view is not None

    assert context_percentage(view, State.VERIFYING, MODEL, _routing()) == (
        None,
        "no agent running in verifying",
    )
    assert context_percentage("pre-Claude attempt", State.IMPLEMENTING, MODEL, _routing()) == (
        None,
        "pre-Claude attempt",
    )
    assert context_percentage(view, State.IMPLEMENTING, "claude-unknown", _routing()) == (
        None,
        "no context window on file for claude-unknown",
    )


def test_lane_of_maps_each_state_to_its_actor_lane() -> None:
    # The AGENTS.md three-actor model: agents get a named lane, engineer + code are the
    # other two, and the marker/parked states have no lane (they render as the run's exit).
    assert lane_of(State.PLANNING) == "planner"
    assert lane_of(State.IMPLEMENTING) == "builder"
    assert lane_of(State.REVIEWING) == "reviewer"
    assert lane_of(State.APPROVED) == "engineer"
    assert lane_of(State.AWAITING_HUMAN) == "engineer"
    assert lane_of(State.VERIFYING) == "code"
    assert lane_of(State.SANDBOX_CREATING) == "code"
    assert lane_of(State.BLOCKED) is None
    assert lane_of(State.RESUMABLE) is None
    assert lane_of(State.CANCELLED) is None
