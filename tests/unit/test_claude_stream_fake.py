from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from factory.agent import stream
from factory.agent.stream import (
    Completed,
    Expected,
    Failed,
    FailureKind,
    Interrupted,
    Outcome,
    SessionId,
)
from tests.support import claude_stream as fake

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude"
Wire = dict[str, Any]

REAL_COUNTERPART = {
    "success": "success-schema",
    "denial": "denied",
    "max_turns": "max-turns",
    "budget": "budget",
    "not_logged_in": "not-logged-in",
    "auth_retrying": "auth-retry",
    "session_lost": "resume-unknown",
}


def scenario(name: str) -> fake.Scenario:
    build: Callable[[], fake.Scenario] = getattr(fake, name)
    return build()


def wire(lines: list[str]) -> list[Wire]:
    return [json.loads(line) for line in lines]


def kind(event: Wire) -> tuple[str, str | None]:
    return event["type"], event.get("subtype")


def nested(event: Wire) -> Iterator[tuple[str, Wire]]:
    if kind(event) == ("system", "init"):
        for plugin in event["plugins"]:
            yield "init.plugins[*]", plugin
    if event["type"] in ("assistant", "user"):
        content = event["message"]["content"]
        for block in content if isinstance(content, list) else ():
            yield f"{event['type']}.content[{block['type']}]", block
    if event["type"] == "assistant":
        yield "message.usage", event["message"]["usage"]
    if event["type"] == "result" and event.get("subagent_stats") is not None:
        yield "result.subagent_stats", event["subagent_stats"]
    if event["type"] == "result":
        for usage in event["modelUsage"].values():
            yield "result.modelUsage[*]", usage
        for denial in event["permission_denials"]:
            yield "result.permission_denials[*]", denial
    if event["type"] == "rate_limit_event":
        info = event["rate_limit_info"]
        yield "rate_limit_info", info
        for window in info["unifiedWindows"].values():
            yield "rate_limit_info.unifiedWindows.*", window


def shapes(events: list[Wire]) -> dict[tuple[str, str | None] | str, set[frozenset[str]]]:
    found: dict[tuple[str, str | None] | str, set[frozenset[str]]] = {}
    for event in events:
        found.setdefault(kind(event), set()).add(frozenset(event))
        for path, obj in nested(event):
            found.setdefault(path, set()).add(frozenset(obj))
    return found


@pytest.mark.parametrize(("name", "fixture"), sorted(REAL_COUNTERPART.items()))
def test_the_fake_writes_only_event_kinds_its_real_counterpart_wrote(
    name: str, fixture: str
) -> None:
    real = {kind(e) for e in wire((FIXTURES / f"{fixture}.jsonl").read_text().splitlines())}
    written = {kind(e) for e in wire(scenario(name).lines)}

    assert written <= real


@pytest.mark.parametrize(("name", "fixture"), sorted(REAL_COUNTERPART.items()))
def test_every_shared_shape_has_the_real_key_set(name: str, fixture: str) -> None:
    real = shapes(wire((FIXTURES / f"{fixture}.jsonl").read_text().splitlines()))
    written = shapes(wire(scenario(name).lines))
    shared = written.keys() & real.keys()
    assert shared

    for where in shared:
        for keys in written[where]:
            closest = min(real[where], key=lambda real_keys: len(real_keys ^ keys))
            assert keys in real[where], (
                f"{where}: fake has extra {sorted(keys - closest)}, lacks {sorted(closest - keys)}"
            )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("success", Completed(fake.ANSWER, None, 2, 0)),
        ("denial", "Completed"),
        ("max_turns", Failed(FailureKind.MAX_TURNS, "Reached maximum number of turns (1)")),
        ("budget", Failed(FailureKind.BUDGET, "Reached maximum budget ($0.001)")),
        ("not_logged_in", Failed(FailureKind.AUTH, "Not logged in · Please run /login")),
        ("auth_retrying", Interrupted(exit_code=None, auth_failing=True)),
        ("truncated", Interrupted(exit_code=None, auth_failing=False)),
        (
            "session_lost",
            Failed(
                FailureKind.SESSION_LOST, f"No conversation found with session ID: {fake.SESSION}"
            ),
        ),
        (
            "session_in_use",
            Failed(
                FailureKind.SESSION_IN_USE, f"Error: Session ID {fake.SESSION} is already in use."
            ),
        ),
    ],
)
def test_each_scenario_folds_to_its_intended_outcome(name: str, expected: Outcome | str) -> None:
    lines, exit_code, stderr = scenario(name)

    outcome = stream.fold(stream.events(lines), exit_code=exit_code, stderr=stderr).outcome

    if isinstance(expected, str):
        assert type(outcome).__name__ == expected
    elif isinstance(expected, Completed):
        assert isinstance(outcome, Completed)
        assert (outcome.structured_output, outcome.turns, outcome.subagents_spawned) == (
            expected.structured_output,
            expected.turns,
            expected.subagents_spawned,
        )
    else:
        assert outcome == expected


def test_the_denial_scenario_reports_one_write_denial_and_no_touched_file() -> None:
    lines, exit_code, stderr = fake.denial(path="/work/repo/x.txt")

    run = stream.fold(stream.events(lines), exit_code=exit_code, stderr=stderr)

    assert [d.tool for d in run.denials] == ["Write"]
    assert run.files_touched == ()


def test_the_default_session_attests_clean_against_a_builder_launch() -> None:
    lines, exit_code, _ = fake.success(session="pinned-session", model="claude-opus-5-5")
    expected = Expected(
        session=SessionId("pinned-session"),
        model="claude-opus-5-5",
        tools=frozenset(fake.TOOLS),
        plugins=frozenset(),
        permission_mode="default",
    )

    run = stream.fold(stream.events(lines), exit_code=exit_code, expected=expected)

    assert run.violations == ()
    assert run.session == "pinned-session"


def test_result_error_refuses_a_kind_with_no_measured_shape() -> None:
    with pytest.raises(ValueError, match="rate-limited"):
        fake.result_error(FailureKind.RATE_LIMITED)
