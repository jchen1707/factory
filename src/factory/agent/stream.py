from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, NewType

SessionId = NewType("SessionId", str)


class CorruptStream(Exception):
    def __init__(self, line_number: int, text: str) -> None:
        super().__init__(f"line {line_number} is not a valid stream-json event: {text[:200]!r}")
        self.line_number = line_number
        self.text = text


@dataclass(frozen=True)
class Usage:
    input: int
    cache_read: int
    cache_write: int
    output: int
    thinking: int

    @property
    def context_tokens(self) -> int:
        return self.input + self.cache_read + self.cache_write

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input=self.input + other.input,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
            thinking=self.thinking + other.thinking,
        )

    def __sub__(self, other: Usage) -> Usage:
        return Usage(
            input=self.input - other.input,
            cache_read=self.cache_read - other.cache_read,
            cache_write=self.cache_write - other.cache_write,
            output=self.output - other.output,
            thinking=self.thinking - other.thinking,
        )


@dataclass(frozen=True)
class ModelUsage:
    usage: Usage
    notional_usd: float
    context_window: int


@dataclass(frozen=True)
class Plugin:
    name: str
    source: str

    @property
    def builtin(self) -> bool:
        return self.source.endswith("@builtin")


@dataclass(frozen=True)
class Init:
    session: SessionId
    model: str
    tools: frozenset[str]
    plugins: tuple[Plugin, ...]
    mcp_servers: tuple[str, ...]
    permission_mode: str


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: Mapping[str, object]


@dataclass(frozen=True)
class Message:
    id: str
    model: str
    usage: Usage
    text: str
    tool_uses: tuple[ToolUse, ...]
    timestamp: str | None


@dataclass(frozen=True)
class ToolResult:
    tool_use_id: str
    is_error: bool
    text: str
    timestamp: str | None


@dataclass(frozen=True)
class Retry:
    attempt: int
    status: int | None
    error: str


@dataclass(frozen=True)
class Denied:
    tool: str
    tool_use_id: str
    reason: str


@dataclass(frozen=True)
class Window:
    utilization: float
    resets_at: int


@dataclass(frozen=True)
class RateLimit:
    """The subscription's limits as one response reported them.

    `resets_at` belongs to the limit the event names (`rateLimitType`); each window in
    `windows` (keyed `five_hour`, `seven_day` as the wire names them) carries its own.
    """

    status: str
    resets_at: int | None
    windows: Mapping[str, Window]


@dataclass(frozen=True)
class Result:
    is_error: bool
    subtype: str
    terminal_reason: str | None
    api_error_status: int | None
    errors: tuple[str, ...]
    text: str | None
    structured_output: Mapping[str, object] | None
    num_turns: int
    notional_usd: float
    by_model: Mapping[str, ModelUsage]
    denials: tuple[Denied, ...]
    subagents_spawned: int


Event = Init | Message | ToolResult | Retry | Denied | RateLimit | Result


class FailureKind(StrEnum):
    AUTH = "auth"
    MODEL_UNAVAILABLE = "model-unavailable"
    MAX_TURNS = "max-turns"
    BUDGET = "budget"
    RATE_LIMITED = "rate-limited"
    SESSION_LOST = "session-lost"
    SESSION_IN_USE = "session-in-use"
    LAUNCH_REFUSED = "launch-refused"
    EXIT_NONZERO = "exit-nonzero"
    CORRUPT = "corrupt-stream"
    API_ERROR = "api-error"


@dataclass(frozen=True)
class Completed:
    structured_output: Mapping[str, object] | None
    text: str | None
    turns: int
    subagents_spawned: int


@dataclass(frozen=True)
class Failed:
    kind: FailureKind
    detail: str


@dataclass(frozen=True)
class Interrupted:
    exit_code: int | None
    auth_failing: bool


Outcome = Completed | Failed | Interrupted


@dataclass(frozen=True)
class Violation:
    what: str
    expected: str
    observed: str


@dataclass(frozen=True)
class Expected:
    session: SessionId
    model: str
    tools: frozenset[str]
    plugins: frozenset[str]
    permission_mode: str


@dataclass(frozen=True)
class Run:
    session: SessionId | None
    init: Init | None
    outcome: Outcome
    violations: tuple[Violation, ...]
    denials: tuple[Denied, ...]
    rate_limit: RateLimit | None
    context_tokens: int
    notional_usd: float
    by_model: Mapping[str, ModelUsage]
    commands: tuple[str, ...]
    files_touched: tuple[str, ...]


_Wire = dict[str, Any]


def _usage(wire: _Wire) -> Usage:
    # A synthetic error message carries `output_tokens_details: null` (not-logged-in).
    details = wire.get("output_tokens_details") or {}
    return Usage(
        input=wire["input_tokens"],
        cache_read=wire["cache_read_input_tokens"],
        cache_write=wire["cache_creation_input_tokens"],
        output=wire["output_tokens"],
        thinking=details.get("thinking_tokens", 0),
    )


def _model_usage(wire: _Wire) -> ModelUsage:
    return ModelUsage(
        usage=Usage(
            input=wire["inputTokens"],
            cache_read=wire["cacheReadInputTokens"],
            cache_write=wire["cacheCreationInputTokens"],
            output=wire["outputTokens"],
            thinking=wire.get("thinkingTokens", 0),
        ),
        notional_usd=float(wire["costUSD"]),
        context_window=wire["contextWindow"],
    )


def _init(wire: _Wire) -> Init:
    return Init(
        session=SessionId(wire["session_id"]),
        model=wire["model"],
        tools=frozenset(wire["tools"]),
        plugins=tuple(Plugin(name=p["name"], source=p["source"]) for p in wire["plugins"]),
        mcp_servers=tuple(server["name"] for server in wire["mcp_servers"]),
        permission_mode=wire["permissionMode"],
    )


def _message(wire: _Wire) -> Message:
    message = wire["message"]
    blocks = message["content"]
    return Message(
        id=message["id"],
        model=message["model"],
        usage=_usage(message["usage"]),
        text="\n".join(block["text"] for block in blocks if block["type"] == "text"),
        tool_uses=tuple(
            ToolUse(id=block["id"], name=block["name"], input=block["input"])
            for block in blocks
            if block["type"] == "tool_use"
        ),
        timestamp=wire.get("timestamp"),
    )


def _block_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content if block.get("type") == "text")
    return ""


def _tool_results(wire: _Wire) -> Iterator[ToolResult]:
    content = wire["message"]["content"]
    if not isinstance(content, list):
        return
    for block in content:
        if block["type"] == "tool_result":
            yield ToolResult(
                tool_use_id=block["tool_use_id"],
                is_error=bool(block.get("is_error", False)),
                text=_block_text(block.get("content")),
                timestamp=wire.get("timestamp"),
            )


def _retry(wire: _Wire) -> Retry:
    return Retry(attempt=wire["attempt"], status=wire.get("error_status"), error=wire["error"])


def _denied(wire: _Wire) -> Denied:
    return Denied(
        tool=wire["tool_name"],
        tool_use_id=wire["tool_use_id"],
        reason=wire.get("decision_reason", ""),
    )


def _rate_limit(wire: _Wire) -> RateLimit:
    info = wire["rate_limit_info"]
    windows = info.get("unifiedWindows") or {}
    return RateLimit(
        status=info["status"],
        resets_at=info.get("resetsAt"),
        windows=MappingProxyType(
            {
                name: Window(utilization=float(each["utilization"]), resets_at=each["resetsAt"])
                for name, each in windows.items()
                # A window without both numbers is unknown; it must not cost the run its
                # result by corrupting the stream, nor reach the guard half-formed.
                if isinstance(each, dict)
                and isinstance(each.get("utilization"), int | float)
                and isinstance(each.get("resetsAt"), int)
            }
        ),
    )


def _result(wire: _Wire) -> Result:
    # resume-unknown's result has no terminal_reason, api_error_status, result or
    # subagent_stats; the keys every measured result carries are required.
    return Result(
        is_error=bool(wire["is_error"]),
        subtype=wire["subtype"],
        terminal_reason=wire.get("terminal_reason"),
        api_error_status=wire.get("api_error_status"),
        errors=tuple(wire.get("errors") or ()),
        text=wire.get("result"),
        structured_output=wire.get("structured_output"),
        num_turns=wire["num_turns"],
        notional_usd=float(wire["total_cost_usd"]),
        by_model=MappingProxyType(
            {model: _model_usage(usage) for model, usage in wire["modelUsage"].items()}
        ),
        denials=tuple(
            Denied(tool=d["tool_name"], tool_use_id=d["tool_use_id"], reason="")
            for d in wire["permission_denials"]
        ),
        subagents_spawned=(wire.get("subagent_stats") or {}).get("spawned", 0),
    )


def _one(build: Callable[[_Wire], Event]) -> Callable[[_Wire], Iterator[Event]]:
    def emit(wire: _Wire) -> Iterator[Event]:
        yield build(wire)

    return emit


_PARSERS: Mapping[tuple[str, str | None], Callable[[_Wire], Iterator[Event]]] = MappingProxyType(
    {
        ("system", "init"): _one(_init),
        ("system", "api_retry"): _one(_retry),
        ("system", "permission_denied"): _one(_denied),
        ("assistant", None): _one(_message),
        ("user", None): _tool_results,
        ("rate_limit_event", None): _one(_rate_limit),
        ("result", None): _one(_result),
    }
)


_MALFORMED = (ValueError, KeyError, TypeError, AttributeError)


def _parse_line(text: str) -> list[Event]:
    wire = json.loads(text)
    if not isinstance(wire, dict):
        raise TypeError("event is not a JSON object")
    kind: str = wire.get("type", "")
    subtype: str | None = wire.get("subtype") if kind == "system" else None
    parser = _PARSERS.get((kind, subtype))
    return [] if parser is None else list(parser(wire))


def events(lines: Iterable[str]) -> Iterator[Event]:
    """Every known event in stream order.

    A malformed line raises `CorruptStream` unless it is the last non-blank line: a writer that
    was killed, or is still running, leaves a torn tail, so that line is dropped instead.
    """
    pending: tuple[int, str] | None = None
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if pending is not None:
            yield from _strict(*pending)
        pending = (number, line)
    if pending is not None:
        try:
            parsed = _parse_line(pending[1])
        except _MALFORMED:
            return
        yield from parsed


def _strict(number: int, line: str) -> list[Event]:
    try:
        return _parse_line(line)
    except _MALFORMED as error:
        raise CorruptStream(number, line) from error


_SESSION_LOST = "No conversation found with session ID"
#: The `model` of the assistant line the CLI writes itself when the API never answered.
SYNTHETIC = "<synthetic>"


def _is_auth(result: Result) -> bool:
    return (
        result.api_error_status == 401
        or any("authentication_failed" in error for error in result.errors)
        or (result.text or "").startswith("Not logged in")
    )


_ERROR_TABLE: tuple[tuple[Callable[[Result], bool], FailureKind], ...] = (
    (_is_auth, FailureKind.AUTH),
    (lambda r: r.api_error_status == 404, FailureKind.MODEL_UNAVAILABLE),
    (lambda r: r.subtype == "error_max_turns", FailureKind.MAX_TURNS),
    (
        lambda r: r.subtype == "error_max_budget_usd" or r.terminal_reason == "budget_exhausted",
        FailureKind.BUDGET,
    ),
    (lambda r: r.api_error_status == 429, FailureKind.RATE_LIMITED),
    (lambda r: any(_SESSION_LOST in error for error in r.errors), FailureKind.SESSION_LOST),
)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def classify(
    result: Result | None,
    *,
    started: bool,
    exit_code: int | None,
    auth_failing: bool,
    stderr: str,
) -> Outcome:
    stderr_line = _first_line(stderr)
    if result is None:
        if not started and exit_code is not None and "is already in use" in stderr:
            return Failed(FailureKind.SESSION_IN_USE, stderr_line)
        if not started and exit_code not in (None, 0) and stderr_line:
            return Failed(FailureKind.LAUNCH_REFUSED, stderr_line)
        return Interrupted(exit_code=exit_code, auth_failing=auth_failing)

    detail = "; ".join(result.errors) or result.text or stderr_line
    if result.is_error:
        kind = next((k for matches, k in _ERROR_TABLE if matches(result)), FailureKind.API_ERROR)
        return Failed(kind, detail or kind.value)
    if result.subtype != "success":
        return Failed(FailureKind.API_ERROR, detail or result.subtype)
    if exit_code not in (None, 0):
        return Failed(FailureKind.EXIT_NONZERO, stderr_line or f"exit code {exit_code}")
    return Completed(
        structured_output=result.structured_output,
        text=result.text,
        turns=result.num_turns,
        subagents_spawned=result.subagents_spawned,
    )


def attest(init: Init, expected: Expected) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    if init.session != expected.session:
        violations.append(Violation("session", expected.session, init.session))
    if init.model != expected.model:
        violations.append(Violation("model", expected.model, init.model))
    # Subset, not equality: the CLI silently drops a `--tools` name it does not know, so only
    # an extra tool is a signal.
    extra_tools = init.tools - expected.tools
    if extra_tools:
        violations.append(
            Violation("tools", ",".join(sorted(expected.tools)), ",".join(sorted(extra_tools)))
        )
    stray_plugins = [
        p.name for p in init.plugins if not p.builtin and p.name not in expected.plugins
    ]
    if stray_plugins:
        violations.append(
            Violation("plugins", ",".join(sorted(expected.plugins)), ",".join(stray_plugins))
        )
    if init.mcp_servers:
        violations.append(Violation("mcp_servers", "", ",".join(init.mcp_servers)))
    if init.permission_mode != expected.permission_mode:
        violations.append(
            Violation("permission_mode", expected.permission_mode, init.permission_mode)
        )
    return tuple(violations)


_FILE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})


def _str_input(use: ToolUse, key: str) -> str:
    value = use.input.get(key)
    return value if isinstance(value, str) else ""


def fold(
    evs: Iterable[Event],
    *,
    exit_code: int | None = None,
    stderr: str = "",
    expected: Expected | None = None,
) -> Run:
    init: Init | None = None
    result: Result | None = None
    auth_failing = False
    denials: dict[str, Denied] = {}
    rate_limit: RateLimit | None = None
    context_tokens = 0
    tool_uses: dict[str, ToolUse] = {}

    for event in evs:
        match event:
            case Init():
                init = init or event
            case Message():
                if event.model != SYNTHETIC:
                    # The API answered, so whatever a retry said before this is history.
                    auth_failing = False
                context_tokens = event.usage.context_tokens
                for use in event.tool_uses:
                    tool_uses.setdefault(use.id, use)
            case Retry(status=status, error=error):
                auth_failing = auth_failing or error == "authentication_failed" or status == 401
            case Denied():
                denials.setdefault(event.tool_use_id, event)
            case RateLimit():
                rate_limit = event
            case Result():
                result = event
                for denial in event.denials:
                    denials.setdefault(denial.tool_use_id, denial)

    ran = [use for use in tool_uses.values() if use.id not in denials]
    commands = [_str_input(use, "command") for use in ran if use.name == "Bash"]
    files = [_str_input(use, "file_path") for use in ran if use.name in _FILE_TOOLS]
    return Run(
        session=init.session if init else None,
        init=init,
        outcome=classify(
            result,
            started=init is not None,
            exit_code=exit_code,
            auth_failing=auth_failing,
            stderr=stderr,
        ),
        violations=attest(init, expected) if init and expected else (),
        denials=tuple(denials.values()),
        rate_limit=rate_limit,
        context_tokens=context_tokens,
        notional_usd=result.notional_usd if result else 0.0,
        by_model=result.by_model if result else MappingProxyType({}),
        commands=tuple(command for command in commands if command),
        files_touched=tuple(dict.fromkeys(path for path in files if path)),
    )


def _read(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def lines(text: str) -> list[str]:
    """A stream's lines. JSON Lines ends a line only at "\n": `str.splitlines` also ends one at
    U+0085, U+2028 and U+2029, which JSON leaves raw inside strings (Claude Code writes U+0085
    raw in a tool result), and so cuts a valid event in two."""
    return text.split("\n")


def _lines(events_path: Path) -> list[str]:
    return lines(_read(events_path) or "")


def parse(
    events_path: Path,
    *,
    exit_path: Path | None = None,
    stderr_path: Path | None = None,
    expected: Expected | None = None,
) -> Run:
    exit_text = _read(exit_path)
    exit_code = int(exit_text.strip()) if exit_text is not None else None
    try:
        return fold(
            events(_lines(events_path)),
            exit_code=exit_code,
            stderr=_read(stderr_path) or "",
            expected=expected,
        )
    except CorruptStream as corrupt:
        return Run(
            session=None,
            init=None,
            outcome=Failed(FailureKind.CORRUPT, f"line {corrupt.line_number} is not a valid event"),
            violations=(),
            denials=(),
            rate_limit=None,
            context_tokens=0,
            notional_usd=0.0,
            by_model=MappingProxyType({}),
            commands=(),
            files_touched=(),
        )


def has_session(events_path: Path) -> bool:
    try:
        return any(isinstance(event, Init) for event in events(_lines(events_path)))
    except CorruptStream:
        return False


def session_id(events_path: Path) -> SessionId | None:
    for line in _lines(events_path):
        try:
            wire = json.loads(line)
        except ValueError:
            continue
        if isinstance(wire, dict) and isinstance(wire.get("session_id"), str):
            return SessionId(wire["session_id"])
    return None
