from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.agent import stream
from factory.agent.stream import (
    Completed,
    CorruptStream,
    Denied,
    Expected,
    Failed,
    FailureKind,
    Init,
    Interrupted,
    Message,
    Outcome,
    Result,
    SessionId,
    ToolResult,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())["fixtures"]
HAIKU = "claude-haiku-4-5-20251001"
SCHEMA_TOOLS = frozenset({"Bash", "Edit", "Read", "Skill", "StructuredOutput", "Write"})


def lines(name: str) -> list[str]:
    return (FIXTURES / MANIFEST[name]["events"]).read_text().splitlines()


def stderr(name: str) -> str:
    entry = MANIFEST[name]
    return (FIXTURES / entry["stderr"]).read_text() if "stderr" in entry else ""


def parse_fixture(name: str, tmp_path: Path) -> stream.Run:
    entry = MANIFEST[name]
    exit_path = tmp_path / "exit"
    exit_path.write_text(f"{entry['exit']}\n")
    return stream.parse(
        FIXTURES / entry["events"],
        exit_path=exit_path,
        stderr_path=FIXTURES / entry["stderr"] if "stderr" in entry else None,
    )


def wire_subtype(line: str) -> str:
    return str(json.loads(line).get("subtype", ""))


def init_of(name: str) -> Init:
    return next(e for e in stream.events(lines(name)) if isinstance(e, Init))


def kind(outcome: Outcome) -> str:
    return outcome.kind.value if isinstance(outcome, Failed) else type(outcome).__name__


EXPECTED_OUTCOME = {
    "success-schema": "Completed",
    "build-tools": "Completed",
    "denied": "Completed",
    "schema-retry": "Completed",
    "schema-unsatisfiable": "Completed",
    "model-unknown": "model-unavailable",
    "resume-unknown": "session-lost",
    "max-turns": "max-turns",
    "budget": "budget",
    "not-logged-in": "auth",
    "auth-retry": "auth",
    "session-in-use": "session-in-use",
    "schema-rejected": "launch-refused",
}


def test_the_expected_outcome_table_covers_the_manifest() -> None:
    assert set(EXPECTED_OUTCOME) == set(MANIFEST)


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_every_fixture_parses_to_its_classified_outcome(name: str, tmp_path: Path) -> None:
    assert kind(parse_fixture(name, tmp_path).outcome) == EXPECTED_OUTCOME[name]


@pytest.mark.parametrize(
    ("name", "failure"),
    [
        ("not-logged-in", FailureKind.AUTH),
        ("auth-retry", FailureKind.AUTH),
        ("model-unknown", FailureKind.MODEL_UNAVAILABLE),
    ],
)
def test_a_success_subtype_with_is_error_is_a_failure(
    name: str, failure: FailureKind, tmp_path: Path
) -> None:
    result = next(e for e in stream.events(lines(name)) if isinstance(e, Result))
    assert (result.subtype, result.is_error) == ("success", True)

    outcome = parse_fixture(name, tmp_path).outcome

    assert isinstance(outcome, Failed)
    assert outcome.kind is failure
    assert outcome.detail == result.text


def test_launch_refused_names_the_first_stderr_line(tmp_path: Path) -> None:
    outcome = parse_fixture("schema-rejected", tmp_path).outcome

    assert outcome == Failed(FailureKind.LAUNCH_REFUSED, stderr("schema-rejected").strip())


NON_EMPTY = sorted(name for name in MANIFEST if lines(name))


@pytest.mark.parametrize("name", NON_EMPTY)
def test_a_torn_final_line_is_dropped(name: str, tmp_path: Path) -> None:
    whole = lines(name)
    torn = [*whole[:-1], whole[-1][: len(whole[-1]) // 2]]

    assert list(stream.events(torn)) == list(stream.events(whole[:-1]))

    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(torn))
    outcome = stream.parse(events_path)
    auth_failing = name == "auth-retry"
    assert outcome.outcome == Interrupted(exit_code=None, auth_failing=auth_failing)


@pytest.mark.parametrize("name", NON_EMPTY)
def test_a_torn_inner_line_is_corrupt(name: str, tmp_path: Path) -> None:
    whole = lines(name)
    corrupt = [*whole[:-1], '{"type": "assistant", "mess', whole[-1]]
    line_number = len(whole)

    with pytest.raises(CorruptStream) as raised:
        list(stream.events(corrupt))
    assert raised.value.line_number == line_number

    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(corrupt))
    run = stream.parse(events_path)
    assert run.outcome == Failed(FailureKind.CORRUPT, f"line {line_number} is not a valid event")
    assert (run.init, run.denials, run.commands, run.notional_usd) == (None, (), (), 0.0)


def test_a_known_event_missing_a_required_key_is_corrupt_before_the_last_line() -> None:
    whole = lines("success-schema")
    init = json.loads(whole[0])
    del init["session_id"]
    damaged = [json.dumps(init), *whole[1:]]

    with pytest.raises(CorruptStream) as raised:
        list(stream.events(damaged))
    assert raised.value.line_number == 1


def test_unknown_events_and_blank_lines_are_skipped() -> None:
    whole = lines("success-schema")
    noisy = [
        whole[0],
        "",
        '{"type": "system", "subtype": "a_new_subtype", "session_id": "x"}',
        '{"type": "a_new_type"}',
        "   ",
        *whole[1:],
    ]

    assert list(stream.events(noisy)) == list(stream.events(whole))


def test_hook_and_thinking_lines_yield_nothing() -> None:
    hooks = [line for line in lines("max-turns") if wire_subtype(line).startswith("hook_")]
    thinking = [line for line in lines("budget") if wire_subtype(line) == "thinking_tokens"]
    assert len(hooks) == 4
    assert len(thinking) == 2

    assert list(stream.events([*hooks, *thinking])) == []


def test_one_user_line_yields_one_tool_result_per_block() -> None:
    wire = json.loads(lines("max-turns")[8])
    block = wire["message"]["content"][0]
    wire["message"]["content"] = [block, {**block, "tool_use_id": "toolu_2", "is_error": True}]

    results = list(stream.events([json.dumps(wire)]))

    assert [(r.tool_use_id, r.is_error) for r in results if isinstance(r, ToolResult)] == [
        ("toolu_013P56kopGSMKsEEzkVCsdyR", False),
        ("toolu_2", True),
    ]


def test_each_assistant_line_is_a_message_that_keeps_its_api_message_id() -> None:
    messages = [e for e in stream.events(lines("success-schema")) if isinstance(e, Message)]

    assert [m.id for m in messages] == ["msg_011CfnE95TvdGzE28ULfbZ6v"] * 2
    assert [tuple(t.name for t in m.tool_uses) for m in messages] == [(), ("StructuredOutput",)]
    assert messages[1].timestamp == "2026-10-07T04:14:30.323Z"


def test_success_schema_folds_its_accounting(tmp_path: Path) -> None:
    run = parse_fixture("success-schema", tmp_path)

    assert run.session == "3f7667d9-6ce8-4ae7-b063-bbf69881544e"
    assert run.context_tokens == 9 + 0 + 14967
    assert run.notional_usd == pytest.approx(0.031638)
    assert run.by_model[HAIKU].usage == stream.Usage(
        input=9, cache_read=0, cache_write=14967, output=339, thinking=80
    )
    assert run.by_model[HAIKU].context_window == 200000
    assert run.rate_limit == stream.RateLimit(
        status="allowed",
        resets_at=1791358800,
        windows={
            "five_hour": stream.Window(utilization=0.44, resets_at=1791358800),
            "seven_day": stream.Window(utilization=0.11, resets_at=1791403200),
        },
    )
    assert isinstance(run.outcome, Completed)
    assert run.outcome.structured_output is not None
    assert run.outcome.structured_output["status"] == "no_change_needed"
    assert (run.outcome.turns, run.outcome.subagents_spawned) == (2, 0)


def test_a_denial_does_not_fail_the_run_and_is_reported_once(tmp_path: Path) -> None:
    run = parse_fixture("denied", tmp_path)

    assert isinstance(run.outcome, Completed)
    assert [(d.tool, d.tool_use_id) for d in run.denials] == [
        ("Write", "toolu_019GByAShAzja6J8otRdEdC1")
    ]
    assert run.denials[0].reason.startswith("no approval surface")
    assert run.files_touched == ()


def test_build_tools_reports_commands_and_files_touched(tmp_path: Path) -> None:
    run = parse_fixture("build-tools", tmp_path)

    assert run.commands == (
        "touch bash-made.txt && git add -A && git -c commit.gpgsign=false commit -qm probe"
        " && git log --oneline | head -1",
    )
    assert run.files_touched == ("/work/repo/notes.txt",)


def test_auth_retries_without_a_result_are_interrupted_and_auth_failing() -> None:
    without_result = [line for line in lines("auth-retry") if json.loads(line)["type"] != "result"]

    run = stream.fold(stream.events(without_result), exit_code=None)

    assert run.outcome == Interrupted(exit_code=None, auth_failing=True)


def test_max_turns_without_its_result_is_interrupted_with_its_exit_code() -> None:
    run = stream.fold(stream.events(lines("max-turns")[:-1]), exit_code=143)

    assert run.outcome == Interrupted(exit_code=143, auth_failing=False)


def test_missing_files_are_an_empty_unfinished_run(tmp_path: Path) -> None:
    run = stream.parse(tmp_path / "events.jsonl", exit_path=tmp_path / "exit")

    assert run.outcome == Interrupted(exit_code=None, auth_failing=False)
    assert (run.session, run.init, run.context_tokens) == (None, None, 0)


SUCCESS_EXPECTED = Expected(
    session=SessionId("3f7667d9-6ce8-4ae7-b063-bbf69881544e"),
    model=HAIKU,
    tools=SCHEMA_TOOLS,
    plugins=frozenset(),
    permission_mode="default",
)


def test_attest_accepts_the_session_it_pinned() -> None:
    assert stream.attest(init_of("success-schema"), SUCCESS_EXPECTED) == ()


def test_attest_rejects_a_session_other_than_the_pin() -> None:
    pinned = replace(SUCCESS_EXPECTED, session=SessionId("00000000-0000-4000-8000-000000000001"))

    violations = stream.attest(init_of("success-schema"), pinned)

    assert [v.what for v in violations] == ["session"]
    assert violations[0].observed == "3f7667d9-6ce8-4ae7-b063-bbf69881544e"


def test_attest_rejects_a_session_with_mcp_user_plugins_and_spawn_tools() -> None:
    init = init_of("max-turns")
    expected = replace(SUCCESS_EXPECTED, session=init.session)

    violations = {v.what: v for v in stream.attest(init, expected)}

    assert {"mcp_servers", "plugins", "tools"} <= set(violations)
    assert "claude.ai Linear" in violations["mcp_servers"].observed
    assert violations["plugins"].observed.split(",") == ["mattpocock-skills", "pstack", "typesafe"]
    assert {"Task", "EnterWorktree", "Workflow"} <= set(violations["tools"].observed.split(","))


def test_attest_admits_declared_plugins_and_a_tool_subset() -> None:
    init = replace(
        init_of("success-schema"),
        tools=frozenset({"Bash", "Read"}),
        plugins=(*init_of("success-schema").plugins, stream.Plugin("doctrine", "doctrine@local")),
    )
    expected = replace(SUCCESS_EXPECTED, plugins=frozenset({"doctrine"}))

    assert stream.attest(init, expected) == ()


def test_fold_attests_only_when_given_an_expectation(tmp_path: Path) -> None:
    pinned = replace(SUCCESS_EXPECTED, model="claude-opus-5-5")

    attested = stream.fold(stream.events(lines("success-schema")), expected=pinned)
    unattested = stream.fold(stream.events(lines("success-schema")))

    assert [v.what for v in attested.violations] == ["model"]
    assert unattested.violations == ()


def test_has_session_means_an_init_event_was_written(tmp_path: Path) -> None:
    torn = tmp_path / "torn.jsonl"
    torn.write_text("\n".join([*lines("success-schema")[:2], '{"type": "assi']))

    assert stream.has_session(FIXTURES / "success-schema.jsonl")
    assert stream.has_session(torn)
    assert not stream.has_session(FIXTURES / "resume-unknown.jsonl")
    assert not stream.has_session(FIXTURES / "session-in-use.jsonl")
    assert not stream.has_session(tmp_path / "missing.jsonl")


def test_a_null_rate_limit_window_is_an_unknown_window() -> None:
    wire = next(json.loads(x) for x in lines("success-schema") if '"rate_limit_event"' in x)
    wire["rate_limit_info"]["unifiedWindows"]["seven_day"] = None

    parsed = list(stream.events([json.dumps(wire), lines("success-schema")[-1]]))

    assert (
        stream.RateLimit("allowed", 1791358800, {"five_hour": stream.Window(0.44, 1791358800)})
        in parsed
    )


def test_a_wrongly_shaped_inner_event_is_corrupt_not_a_crash() -> None:
    whole = lines("success-schema")
    wire = next(json.loads(x) for x in whole if '"rate_limit_event"' in x)
    wire["rate_limit_info"]["unifiedWindows"] = ["five_hour"]

    with pytest.raises(CorruptStream):
        list(stream.events([json.dumps(wire), whole[-1]]))


def test_session_id_reads_the_first_event_that_carries_one(tmp_path: Path) -> None:
    assert stream.session_id(FIXTURES / "success-schema.jsonl") == SUCCESS_EXPECTED.session
    assert stream.session_id(FIXTURES / "resume-unknown.jsonl") == (
        "00000000-0000-4000-8000-000000000000"
    )
    assert stream.session_id(FIXTURES / "session-in-use.jsonl") is None
    assert stream.session_id(tmp_path / "missing.jsonl") is None


def a_result(
    *,
    is_error: bool = False,
    subtype: str = "success",
    terminal_reason: str | None = "completed",
    api_error_status: int | None = None,
    errors: tuple[str, ...] = (),
    denials: tuple[Denied, ...] = (),
) -> Result:
    return Result(
        is_error=is_error,
        subtype=subtype,
        terminal_reason=terminal_reason,
        api_error_status=api_error_status,
        errors=errors,
        text="done",
        structured_output=None,
        num_turns=1,
        notional_usd=0.0,
        by_model={},
        denials=denials,
        subagents_spawned=0,
    )


@pytest.mark.parametrize(
    ("result", "exit_code", "expected"),
    [
        (a_result(), 0, "Completed"),
        (a_result(), None, "Completed"),
        (a_result(), 1, "exit-nonzero"),
        (a_result(subtype="error_during_execution"), 0, "api-error"),
        (a_result(is_error=True, api_error_status=429), 1, "rate-limited"),
        (
            a_result(is_error=True, api_error_status=500, terminal_reason="api_error"),
            1,
            "api-error",
        ),
        (a_result(is_error=True, errors=("authentication_failed",)), 1, "auth"),
        (a_result(is_error=True, terminal_reason="budget_exhausted"), 1, "budget"),
    ],
)
def test_classify_derived_rows(result: Result, exit_code: int | None, expected: str) -> None:
    outcome = stream.classify(
        result, started=True, exit_code=exit_code, auth_failing=False, stderr=""
    )

    assert kind(outcome) == expected


def test_classify_without_a_result_after_init_is_interrupted_even_with_stderr() -> None:
    outcome = stream.classify(None, started=True, exit_code=1, auth_failing=False, stderr="boom\n")

    assert outcome == Interrupted(exit_code=1, auth_failing=False)


def test_permission_denied_event_and_result_denials_dedupe_in_stream_order() -> None:
    events: list[stream.Event] = [
        Denied("Write", "toolu_a", "async"),
        a_result(denials=(Denied("Write", "toolu_a", ""), Denied("Edit", "toolu_b", ""))),
    ]

    run = stream.fold(events)

    assert [(d.tool_use_id, d.reason) for d in run.denials] == [
        ("toolu_a", "async"),
        ("toolu_b", ""),
    ]


def test_a_real_answer_after_a_401_retry_clears_auth_failing() -> None:
    # One transient 401 followed by work is a run that recovered; the CLI's own synthetic
    # "Not logged in" line is not an answer and keeps the flag (the measured auth-retry).
    from tests.support import claude_stream as fake

    recovered = stream.fold(
        stream.events(
            fake.lines(fake.init(), fake.api_retry(1), fake.assistant_text("working again"))
        )
    )
    assert recovered.outcome == Interrupted(exit_code=None, auth_failing=False)

    failing = stream.fold(
        stream.events(
            fake.lines(
                fake.init(),
                fake.api_retry(1),
                fake.assistant_api_error("Not logged in", "authentication_failed"),
            )
        )
    )
    assert failing.outcome == Interrupted(exit_code=None, auth_failing=True)
