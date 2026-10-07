from __future__ import annotations

import json
import os
import shlex
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_args

from factory.agent import stream
from factory.agent.stream import SessionId
from factory.sandbox.base import detached_shell_script


class Role(StrEnum):
    PLANNER = "planner"
    TEST_DESIGNER = "test_designer"
    DIAGNOSER = "diagnoser"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    SYNTHESISER = "synthesiser"
    DOCUMENTER = "documenter"


Effort = Literal["low", "medium", "high", "xhigh", "max"]

WRITE_TOOLS = frozenset({"Bash", "Read", "Edit", "Write", "Skill"})
READ_TOOLS = frozenset({"Bash", "Read", "Skill"})

PLAN_WRITERS = frozenset({Role.PLANNER, Role.TEST_DESIGNER, Role.DIAGNOSER})
CODE_WRITERS = frozenset({Role.BUILDER, Role.DOCUMENTER})
TOOLS: Mapping[Role, frozenset[str]] = MappingProxyType(
    {role: WRITE_TOOLS if role in PLAN_WRITERS | CODE_WRITERS else READ_TOOLS for role in Role}
)

# `--json-schema` adds this tool to `init.tools` even under `--tools` (success-schema).
STRUCTURED_OUTPUT = "StructuredOutput"
# Passed as `--permission-mode` because a target repo's `permissions.defaultMode` otherwise
# becomes `init.permissionMode` under `--setting-sources project` and fails attestation.
PERMISSION_MODE = "default"
EMPTY_MCP_CONFIG = '{"mcpServers":{}}'


@dataclass(frozen=True)
class PluginRef:
    name: str
    path: Path


@dataclass(frozen=True)
class AttemptFiles:
    prompt: Path
    events: Path
    stderr: Path
    exit: Path
    heartbeat: Path
    pgid: Path
    last_message: Path


@dataclass(frozen=True)
class Invocation:
    role: Role
    model: str
    effort: Effort | None
    max_turns: int
    max_budget_usd: float
    session: SessionId
    resume: bool
    files: AttemptFiles
    schema: Mapping[str, object]
    env: Mapping[str, str] = MappingProxyType({})
    plugins: tuple[PluginRef, ...] = ()

    def __post_init__(self) -> None:
        forbidden = [key for key in self.env if overrides_cli(key)]
        if forbidden:
            raise ValueError(f"env must not override the Claude CLI: {', '.join(forbidden)}")
        # The CLI warns about an unknown effort and runs at the default instead of refusing.
        if self.effort is not None and self.effort not in get_args(Effort):
            raise ValueError(f"effort {self.effort!r} is not one of {get_args(Effort)}")
        if self.max_turns < 1:
            raise ValueError(f"max_turns must be at least 1, got {self.max_turns}")
        if self.max_budget_usd <= 0:
            raise ValueError(f"max_budget_usd must be positive, got {self.max_budget_usd}")

    @property
    def tools(self) -> frozenset[str]:
        return TOOLS[self.role]

    @property
    def expected(self) -> stream.Expected:
        return stream.Expected(
            session=self.session,
            model=self.model,
            tools=self.tools | {STRUCTURED_OUTPUT},
            plugins=frozenset(plugin.name for plugin in self.plugins),
            permission_mode=PERMISSION_MODE,
        )


class StructuredOutputMissing(Exception):
    pass


def overrides_cli(env_key: str) -> bool:
    """CLAUDE_CODE_EFFORT_LEVEL beats --effort, ANTHROPIC_API_KEY beats the OAuth login
    (auth-retry), and CLAUDE_CONFIG_DIR loses the login (not-logged-in)."""
    return env_key.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) or env_key == "CLAUDE_CONFIG_DIR"


def new_session() -> SessionId:
    return SessionId(str(uuid.uuid4()))


def _plain_decimal(usd: float) -> str:
    return format(Decimal(repr(usd)), "f")


def _schema_json(schema: Mapping[str, object]) -> str:
    # The CLI cannot resolve the draft-2020-12 `$schema` URI and exits before any event
    # (schema-rejected).
    return json.dumps(
        {key: value for key, value in schema.items() if key != "$schema"}, separators=(",", ":")
    )


def argv(inv: Invocation) -> list[str]:
    """The `claude` argv. The prompt arrives on stdin, so nothing here is positional.

    `--tools`, `--allowedTools` and `--mcp-config` are variadic and swallow a following bare
    word, so each takes one value and is followed by a flag or the end.
    """
    tools = ",".join(sorted(inv.tools))
    return [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        inv.model,
        *(["--effort", inv.effort] if inv.effort is not None else []),
        "--max-turns",
        str(inv.max_turns),
        "--max-budget-usd",
        _plain_decimal(inv.max_budget_usd),
        "--setting-sources",
        "project",
        "--resume" if inv.resume else "--session-id",
        inv.session,
        "--permission-prompts",
        "none",
        "--permission-mode",
        PERMISSION_MODE,
        *(arg for plugin in inv.plugins for arg in ("--plugin-dir", str(plugin.path))),
        "--json-schema",
        _schema_json(inv.schema),
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--strict-mcp-config",
        "--mcp-config",
        EMPTY_MCP_CONFIG,
    ]


def script(inv: Invocation) -> str:
    """The detached `/bin/sh` wrapper: prompt on stdin, events and stderr to separate files."""
    files = inv.files
    body = (
        f"{shlex.join(argv(inv))} < {shlex.quote(str(files.prompt))} "
        f"> {shlex.quote(str(files.events))} 2> {shlex.quote(str(files.stderr))}"
    )
    return detached_shell_script(
        heartbeat_path=files.heartbeat, exit_path=files.exit, body=body, pgid_path=files.pgid
    )


def materialize_final(run: stream.Run, path: Path) -> Mapping[str, object]:
    """Write the run's structured answer to `path` atomically and return it."""
    outcome = run.outcome
    if not isinstance(outcome, stream.Completed) or outcome.structured_output is None:
        raise StructuredOutputMissing(f"no structured output in {outcome!r}")
    answer = outcome.structured_output
    # Not `mkstemp`: its 0600 mode would survive the rename onto `path`.
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return answer
