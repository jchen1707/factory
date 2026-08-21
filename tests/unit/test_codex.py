"""§21.1 — the parser is pinned to the P0-7 fixtures, and to nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.agent.base import (
    AgentInvocation,
    SchemaInvalid,
    SchemaUnsupported,
    validate_against_schema,
)
from factory.agent.codex import CodexAdapter, TranscriptError, parse_events

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HOME = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_hello_is_the_minimal_happy_path() -> None:
    transcript = parse_events(_fixture("codex-exec-hello.jsonl"))
    assert transcript.session_id == "01a02119-c6da-76f0-8c71-bbb9d5295a5a"
    assert not transcript.failed
    assert transcript.usage.input_tokens == 14670
    assert transcript.usage.output_tokens == 6


def test_total_tokens_is_summed_because_the_stream_does_not_carry_it() -> None:
    # P0-7 refuted `total_tokens`: it appears in the binary's symbol table and not in
    # the stream.
    usage = parse_events(_fixture("codex-exec-hello.jsonl")).usage
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens


def test_a_command_execution_is_captured() -> None:
    transcript = parse_events(_fixture("codex-exec-command.jsonl"))
    assert len(transcript.commands) == 1


def test_a_file_change_names_its_paths() -> None:
    transcript = parse_events(_fixture("codex-exec-file-change.jsonl"))
    assert transcript.files_touched
    assert all(path.endswith("README.md") for path in transcript.files_touched)


def test_an_error_item_on_a_successful_run_is_not_a_failure() -> None:
    # The two `--dangerously-bypass-hook-trust` warnings arrive as items of
    # `type: "error"` before `turn.started`, on a run that then succeeds.
    transcript = parse_events(_fixture("codex-exec-hook-bypass.jsonl"))
    assert not transcript.failed
    assert len(transcript.error_items) == 2
    assert transcript.usage.input_tokens == 43205


def test_turn_failed_is_a_failure_and_its_message_is_decoded_twice() -> None:
    transcript = parse_events(_fixture("codex-exec-turn-failed.jsonl"))
    assert transcript.failed
    assert transcript.failure is not None
    # The outer value is a JSON document encoded as a string; a parser that reported
    # it verbatim would print an escaped blob where the reason should be.
    assert "invalid_request_error" in transcript.failure
    assert '\\"' not in transcript.failure


def test_a_truncated_transcript_raises_rather_than_returning_a_partial_result() -> None:
    text = _fixture("codex-exec-hello.jsonl") + '{"type":"turn.comp'
    with pytest.raises(TranscriptError, match="truncated"):
        parse_events(text)


def test_a_non_json_line_in_the_middle_raises() -> None:
    text = 'not json\n{"type":"thread.started","thread_id":"x"}\n'
    with pytest.raises(TranscriptError):
        parse_events(text)


def test_hook_denials_are_read_from_stderr(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(_fixture("codex-exec-hook-bypass.jsonl"))
    stderr = tmp_path / "stderr.log"
    stderr.write_text(_fixture("codex-exec-hook-bypass.stderr.txt"))
    transcript = CodexAdapter().read_transcript(events, stderr)
    assert transcript.hook_denials
    assert "uv.lock" in transcript.hook_denials[0]


# -- command construction ---------------------------------------------------------


def _invocation(tmp_path: Path, **overrides: object) -> AgentInvocation:
    defaults: dict[str, object] = {
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "workdir": "/repo/wt",
        "prompt_path": tmp_path / "prompt.md",
        "schema_path": tmp_path / "schema.json",
        "output_path": tmp_path / "last-message.json",
        "events_path": tmp_path / "events.jsonl",
        "stderr_path": tmp_path / "stderr.log",
        "exit_path": tmp_path / "exit",
        "heartbeat_path": tmp_path / "heartbeat",
        "vault_directory": "/Users/james/Documents/Obsidian Vault",
    }
    defaults.update(overrides)
    return AgentInvocation(**defaults)  # type: ignore[arg-type]


def test_the_command_carries_every_flag_the_plan_requires(tmp_path: Path) -> None:
    argv = list(CodexAdapter().command(_invocation(tmp_path)))
    assert argv[:2] == ["codex", "exec"]
    assert "--dangerously-bypass-hook-trust" in argv
    assert "--json" in argv
    assert "--output-schema" in argv
    assert argv[-1] == "-"
    assert "-m" in argv
    assert "gpt-5.6-sol" in argv
    assert "model_reasoning_effort=xhigh" in argv
    assert any(a.startswith("shell_environment_policy.set.OBSIDIAN_VAULT_DIRECTORY=") for a in argv)


def test_the_command_carries_none_of_the_bare_equivalents(tmp_path: Path) -> None:
    argv = list(CodexAdapter().command(_invocation(tmp_path)))
    for forbidden in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--bare"):
        assert forbidden not in argv


def test_resume_uses_the_session_id_and_never_last(tmp_path: Path) -> None:
    argv = list(CodexAdapter().command(_invocation(tmp_path, resume_session="01a0-thread")))
    assert argv[:4] == ["codex", "exec", "resume", "01a0-thread"]
    assert "--last" not in argv


def test_the_vault_setting_is_present_on_a_resume_too(tmp_path: Path) -> None:
    argv = list(CodexAdapter().command(_invocation(tmp_path, resume_session="01a0-thread")))
    assert any("OBSIDIAN_VAULT_DIRECTORY" in a for a in argv)


def test_the_wrapper_redirects_stdin_stdout_and_stderr_separately(tmp_path: Path) -> None:
    script = CodexAdapter().wrapper_script(_invocation(tmp_path))
    assert "2>&1" not in script
    assert f"< {tmp_path / 'prompt.md'}" in script
    assert f"> {tmp_path / 'events.jsonl'}" in script
    assert f"2> {tmp_path / 'stderr.log'}" in script


def test_the_wrapper_writes_the_exit_file_atomically(tmp_path: Path) -> None:
    script = CodexAdapter().wrapper_script(_invocation(tmp_path))
    assert "exit.tmp" in script
    assert "mv " in script
    assert "heartbeat" in script


# -- the result schema ------------------------------------------------------------


def test_the_validator_refuses_a_keyword_it_does_not_implement() -> None:
    # A partial JSON-Schema implementation that skips what it does not know is the
    # "looks green, proves nothing" failure arriving through the check meant to
    # prevent it.
    with pytest.raises(SchemaUnsupported):
        validate_against_schema({"a": 1}, {"type": "object", "patternProperties": {}})


def _implement_schema() -> dict:
    import json

    return json.loads((HOME / "schemas" / "implement_result.schema.json").read_text())


#: A complete `implement_result`, every required key present.
_COMPLETE_RESULT = {
    "status": "implemented",
    "summary": "did the thing",
    "files_changed": ["src/app/main.py"],
    "tests_added": ["tests/test_main.py"],
    "out_of_scope": [],
    "behaviour_changed": True,
    "seam_confirmed": True,
    "tdd_used": True,
    "gates_run": ["ruff check"],
    "blocked_reason": None,
    "docs_updated": [],
}


def test_behaviour_changed_is_required() -> None:
    schema = _implement_schema()
    without = {k: v for k, v in _COMPLETE_RESULT.items() if k != "behaviour_changed"}
    with pytest.raises(SchemaInvalid, match="behaviour_changed"):
        validate_against_schema(without, schema)
    validate_against_schema(_COMPLETE_RESULT, schema)


def test_every_property_is_required_because_openai_rejects_otherwise() -> None:
    """OpenAI structured outputs reject a schema whose `required` omits any key of
    `properties`, and the rejection happens *inside the sandbox*, after the run has
    spent a sandbox, a worktree and a detached exec on it.

    Measured 2026-08-21, the first time a run reached `implementing` — `codex exec`
    exited 1 with:

        Invalid schema for response_format 'codex_output_schema': In context=(),
        'required' is required to be supplied and to be an array including every key
        in properties. Missing 'seam_confirmed'.

    A field with nothing to say uses an empty array or an explicit null instead of
    being absent, so nothing is lost by requiring all of them.
    """
    schema = _implement_schema()
    assert set(schema["required"]) == set(schema["properties"])


def test_a_boolean_is_not_accepted_where_a_number_is_wanted() -> None:
    with pytest.raises(SchemaInvalid, match="boolean"):
        validate_against_schema(True, {"type": "integer"})
