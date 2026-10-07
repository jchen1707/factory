from __future__ import annotations

import itertools
import json
import os
import shlex
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory.agent import claude, stream
from factory.agent.claude import (
    STRUCTURED_OUTPUT,
    AttemptFiles,
    Effort,
    Invocation,
    PluginRef,
    Role,
    StructuredOutputMissing,
)
from factory.agent.stream import SessionId

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "claude"
IMPLEMENT_SCHEMA = json.loads((REPO / "schemas" / "implement_result.schema.json").read_text())
COMPACT_SCHEMA = json.dumps(
    {k: v for k, v in IMPLEMENT_SCHEMA.items() if k != "$schema"}, separators=(",", ":")
)
SESSION = SessionId("6f0c1f8e-2d55-4d1e-9a39-6f8f3f7d2c11")
ATTEMPT = Path("/factory/attempts/BAC-1/attempt 1")
FILES = AttemptFiles(
    prompt=ATTEMPT / "prompt.md",
    events=ATTEMPT / "events.jsonl",
    stderr=ATTEMPT / "stderr.log",
    exit=ATTEMPT / "exit",
    heartbeat=ATTEMPT / "heartbeat",
    pgid=ATTEMPT / "pgid",
    last_message=ATTEMPT / "last-message.json",
)
SPAWN_TOOLS = (
    "Task",
    "Workflow",
    "EnterWorktree",
    "CronCreate",
    "ScheduleWakeup",
    "RemoteTrigger",
    "SendMessage",
    "Monitor",
    "ToolSearch",
)
NEVER_EMITTED = (
    "--fallback-model",
    "--bare",
    "--continue",
    "-c",
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--add-dir",
    "--disallowedTools",
)
VALUE_FLAGS = frozenset(
    {
        "--output-format",
        "--model",
        "--effort",
        "--max-turns",
        "--max-budget-usd",
        "--setting-sources",
        "--session-id",
        "--resume",
        "--permission-prompts",
        "--permission-mode",
        "--plugin-dir",
        "--json-schema",
        "--tools",
        "--allowedTools",
        "--mcp-config",
    }
)
VARIADIC_FLAGS = ("--tools", "--allowedTools", "--mcp-config")
TWO_PLUGINS = (
    PluginRef("doctrine", Path("/factory/plugins/doctrine")),
    PluginRef("layer-a", Path("/factory/plugins/layer a")),
)


def invocation(
    *,
    role: Role = Role.BUILDER,
    effort: Effort | None = "medium",
    resume: bool = False,
    plugins: tuple[PluginRef, ...] = (),
    env: dict[str, str] | None = None,
    max_turns: int = 40,
    max_budget_usd: float = 2.5,
) -> Invocation:
    return Invocation(
        role=role,
        model="claude-opus-5-5",
        effort=effort,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        session=SESSION,
        resume=resume,
        files=FILES,
        schema=IMPLEMENT_SCHEMA,
        env=env or {},
        plugins=plugins,
    )


EFFORTS: tuple[Effort | None, ...] = ("high", None)
VARIANTS = [
    invocation(role=role, resume=resume, effort=effort, plugins=plugins)
    for role, resume, effort, plugins in itertools.product(
        Role, (False, True), EFFORTS, ((), TWO_PLUGINS)
    )
]


def variant_id(inv: Invocation) -> str:
    session = "resume" if inv.resume else "fresh"
    return f"{inv.role}-{session}-{inv.effort or 'no-effort'}-{len(inv.plugins)}-plugins"


def value_of(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_each_role_has_its_exact_tool_set() -> None:
    writers = {"Bash", "Read", "Edit", "Write", "Skill"}
    readers = {"Bash", "Read", "Skill"}

    assert {role: set(tools) for role, tools in claude.TOOLS.items()} == {
        Role.PLANNER: writers,
        Role.TEST_DESIGNER: writers,
        Role.DIAGNOSER: writers,
        Role.BUILDER: writers,
        Role.DOCUMENTER: writers,
        Role.REVIEWER: readers,
        Role.SYNTHESISER: readers,
    }


@pytest.mark.parametrize("inv", VARIANTS, ids=variant_id)
def test_no_role_can_reach_a_spawn_tool(inv: Invocation) -> None:
    args = claude.argv(inv)
    granted = (
        set(claude.TOOLS[inv.role])
        | set(value_of(args, "--tools").split(","))
        | set(value_of(args, "--allowedTools").split(","))
    )

    assert granted.isdisjoint(SPAWN_TOOLS)


def test_tools_and_allowed_tools_carry_the_role_set_sorted() -> None:
    args = claude.argv(invocation(role=Role.REVIEWER))

    assert value_of(args, "--tools") == "Bash,Read,Skill"
    assert value_of(args, "--allowedTools") == "Bash,Read,Skill"


def test_argv_fresh_with_effort_is_exact() -> None:
    assert claude.argv(invocation()) == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-opus-5-5",
        "--effort",
        "medium",
        "--max-turns",
        "40",
        "--max-budget-usd",
        "2.5",
        "--setting-sources",
        "project",
        "--session-id",
        SESSION,
        "--permission-prompts",
        "none",
        "--permission-mode",
        "default",
        "--json-schema",
        COMPACT_SCHEMA,
        "--tools",
        "Bash,Edit,Read,Skill,Write",
        "--allowedTools",
        "Bash,Edit,Read,Skill,Write",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]


def test_argv_resume_without_effort_with_plugins_is_exact() -> None:
    args = claude.argv(invocation(resume=True, effort=None, plugins=TWO_PLUGINS))

    assert args[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert args[5:25] == [
        "--model",
        "claude-opus-5-5",
        "--max-turns",
        "40",
        "--max-budget-usd",
        "2.5",
        "--setting-sources",
        "project",
        "--resume",
        SESSION,
        "--permission-prompts",
        "none",
        "--permission-mode",
        "default",
        "--plugin-dir",
        "/factory/plugins/doctrine",
        "--plugin-dir",
        "/factory/plugins/layer a",
        "--json-schema",
        COMPACT_SCHEMA,
    ]
    assert "--session-id" not in args
    assert "--effort" not in args


@pytest.mark.parametrize("inv", VARIANTS, ids=variant_id)
def test_no_bare_word_follows_a_flag_value(inv: Invocation) -> None:
    args = claude.argv(inv)

    for flag in VARIADIC_FLAGS:
        after = args[args.index(flag) + 2 : args.index(flag) + 3]
        assert after == [] or after[0].startswith("-"), f"{flag} would swallow {after}"

    rest = iter(args[1:])
    for arg in rest:
        assert arg.startswith("-"), f"bare word {arg!r} outside any flag value"
        if arg in VALUE_FLAGS:
            next(rest)


@pytest.mark.parametrize("inv", VARIANTS, ids=variant_id)
def test_never_emitted_flags_are_absent(inv: Invocation) -> None:
    assert set(claude.argv(inv)).isdisjoint(NEVER_EMITTED)


@pytest.mark.parametrize(
    "key",
    [
        "CLAUDE_CODE_EFFORT_LEVEL",
        "CLAUDE_CODE_USE_BEDROCK",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
    ],
)
def test_an_env_that_overrides_the_cli_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        invocation(env={key: "x"})


def test_an_ordinary_env_is_accepted() -> None:
    inv = invocation(env={"OBSIDIAN_VAULT_DIRECTORY": "/vault"})

    assert inv.env == {"OBSIDIAN_VAULT_DIRECTORY": "/vault"}


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"effort": "extreme"}, "effort"),
        ({"max_turns": 0}, "max_turns"),
        ({"max_budget_usd": 0.0}, "max_budget_usd"),
        ({"max_budget_usd": -1.0}, "max_budget_usd"),
    ],
)
def test_an_invalid_limit_is_refused(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        replace(invocation(), **changes)


def test_expected_admits_structured_output_and_the_declared_plugins() -> None:
    inv = invocation(role=Role.REVIEWER, plugins=TWO_PLUGINS)

    assert inv.expected == stream.Expected(
        session=SESSION,
        model="claude-opus-5-5",
        tools=frozenset({"Bash", "Read", "Skill", STRUCTURED_OUTPUT}),
        plugins=frozenset({"doctrine", "layer-a"}),
        permission_mode="default",
    )


def test_expected_attests_a_measured_schema_run_clean() -> None:
    init = next(
        e
        for e in stream.events((FIXTURES / "success-schema.jsonl").read_text().splitlines())
        if isinstance(e, stream.Init)
    )
    inv = replace(invocation(), model=init.model, session=init.session)

    assert stream.attest(init, inv.expected) == ()


def test_json_schema_drops_dollar_schema_and_keeps_the_rest() -> None:
    assert "$schema" in IMPLEMENT_SCHEMA
    value = value_of(claude.argv(invocation()), "--json-schema")

    assert "$schema" not in value
    assert json.loads(value) == {k: v for k, v in IMPLEMENT_SCHEMA.items() if k != "$schema"}
    assert value == COMPACT_SCHEMA


@pytest.mark.parametrize("budget", [0.001, 0.0000001, 1e-9, 2.5, 30.0])
def test_a_small_budget_never_renders_as_zero(budget: float) -> None:
    value = value_of(claude.argv(invocation(max_budget_usd=budget)), "--max-budget-usd")

    assert float(value) == budget
    assert "e" not in value.lower()


def test_script_feeds_the_prompt_on_stdin_and_splits_events_from_stderr() -> None:
    inv = invocation()

    text = claude.script(inv)

    assert (
        f"{shlex.join(claude.argv(inv))} < {shlex.quote(str(FILES.prompt))} "
        f"> {shlex.quote(str(FILES.events))} 2> {shlex.quote(str(FILES.stderr))}"
    ) in text
    assert "setsid --wait" in text
    assert f'printf %s "$$" > {shlex.quote(str(FILES.pgid))}.tmp' in text
    assert shlex.quote(str(FILES.heartbeat)) in text
    assert shlex.quote(str(FILES.exit)) in text


def test_script_runs_the_exact_argv_with_the_prompt_on_stdin(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "setsid").write_text('#!/bin/sh\nshift\nexec "$@"\n')
    seen_argv = tmp_path / "argv.bin"
    (bin_dir / "claude").write_text(
        f"#!/bin/sh\nprintf '%s\\0' \"$@\" > {shlex.quote(str(seen_argv))}\n"
        "cat\nprintf 'warn\\n' >&2\nexit 3\n"
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    attempt = tmp_path / "attempt 1"
    attempt.mkdir()
    files = AttemptFiles(
        prompt=attempt / "prompt.md",
        events=attempt / "events.jsonl",
        stderr=attempt / "stderr.log",
        exit=attempt / "exit",
        heartbeat=attempt / "heartbeat",
        pgid=attempt / "pgid",
        last_message=attempt / "last-message.json",
    )
    files.prompt.write_text("Implement BAC-1.\n")
    inv = replace(invocation(plugins=TWO_PLUGINS), files=files)

    subprocess.run(
        ["/bin/sh", "-c", claude.script(inv)],
        check=True,
        env=os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        timeout=10,
    )

    assert seen_argv.read_bytes().split(b"\0")[:-1] == [
        arg.encode() for arg in claude.argv(inv)[1:]
    ]
    assert files.events.read_text() == "Implement BAC-1.\n"
    assert files.stderr.read_text() == "warn\n"
    assert files.exit.read_text() == "3"


def test_new_session_is_a_fresh_uuid4() -> None:
    first, second = claude.new_session(), claude.new_session()

    assert uuid.UUID(first).version == 4
    assert first != second


def parse(name: str) -> stream.Run:
    return stream.parse(FIXTURES / f"{name}.jsonl")


def test_materialize_final_writes_the_structured_output_atomically(tmp_path: Path) -> None:
    run = parse("success-schema")
    target = tmp_path / "last-message.json"

    answer = claude.materialize_final(run, target)
    first = target.read_bytes()
    again = claude.materialize_final(run, target)

    assert isinstance(run.outcome, stream.Completed)
    assert answer == again == run.outcome.structured_output
    assert json.loads(first) == run.outcome.structured_output
    assert first.endswith(b"}\n")
    assert target.read_bytes() == first
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("name", ["schema-unsatisfiable", "max-turns", "session-in-use"])
def test_materialize_final_refuses_a_run_without_structured_output(
    name: str, tmp_path: Path
) -> None:
    target = tmp_path / "last-message.json"

    with pytest.raises(StructuredOutputMissing):
        claude.materialize_final(parse(name), target)

    assert list(tmp_path.iterdir()) == []


def test_materialize_final_leaves_the_mode_a_plain_write_gives(tmp_path: Path) -> None:
    plain = tmp_path / "plain.json"
    plain.write_text("{}\n")
    target = tmp_path / "last-message.json"

    claude.materialize_final(parse("success-schema"), target)

    assert target.stat().st_mode == plain.stat().st_mode
