"""The console's read-only view of one Claude launch's `stream-json` events — §18.5.

`stream.events` parses; this module only folds what the console shows. A display never
raises: a stream that turns corrupt mid-file shows what was read before the bad line.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from factory.agent import stream
from factory.machine import State
from factory.routing import Routing

__all__ = [
    "StreamView",
    "ToolCall",
    "context_fraction",
    "context_percentage",
    "lane_of",
    "read_stream",
]


#: The states an agent runs in. Elsewhere the latest launch's stream is history, and a
#: context percentage against it would describe an agent that is no longer there.
_AGENT_STATES = frozenset({State.PLANNING, State.IMPLEMENTING, State.REVIEWING})


#: The lane each state belongs to on the run timeline — the AGENTS.md three-actor model
#: (engineer / agent / code), with the agent lane split by role. The colour is *identity*,
#: not verdict: it is deliberately separate from `--pass/--fail/--warn` (semantic), so a
#: hatched dead block never reads as a failed gate. States without a lane (`blocked`,
#: `resumable`, `suspended`, `failed`, `cancelled`) are transient or terminal markers and
#: get `None`; the waterfall renders them as the run's exit, not as a lane block.
_LANE: dict[State, str] = {
    State.APPROVED: "engineer",
    State.AWAITING_HUMAN: "engineer",
    State.CLAIMED: "code",
    State.CONTEXT_LOADED: "code",
    State.SANDBOX_CREATING: "code",
    State.SANDBOX_READY: "code",
    State.WORKTREE_READY: "code",
    State.VERIFYING: "code",
    State.PR_READY: "code",
    State.PLANNING: "planner",
    State.IMPLEMENTING: "builder",
    State.REVIEWING: "reviewer",
}


def lane_of(state: State) -> str | None:
    """The lane a state occupies on the run timeline, or `None` for a marker state."""
    return _LANE.get(state)


ToolOutcome = Literal["ok", "error", "denied", "running"]


@dataclass(frozen=True)
class ToolCall:
    """One `tool_use` and its `tool_result`, in the order the agent issued them.

    `duration_s` is the result event's `timestamp` minus the issuing assistant event's,
    and `None` while the call runs or when either event lacks a timestamp."""

    index: int
    tool: str
    summary: str
    outcome: ToolOutcome
    duration_s: float | None


@dataclass(frozen=True)
class StreamView:
    """What the console shows of one launch. `context_tokens` is the prompt the API last
    answered (input + cache read + cache write), `None` until the first real message."""

    activity: str | None
    context_tokens: int | None
    context_at: float | None
    tool_calls: tuple[ToolCall, ...]


def _epoch(timestamp: object) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


def _clip(text: str, width: int = 120) -> str:
    flat = text.strip().replace("\n", " ")
    return flat[:width] + "…" if len(flat) > width else flat


_SUMMARY_KEYS = ("command", "file_path", "notebook_path", "pattern", "skill", "url")


def _summary(use: stream.ToolUse) -> str:
    inputs = use.input if isinstance(use.input, Mapping) else {}
    value = next((inputs[k] for k in _SUMMARY_KEYS if isinstance(inputs.get(k), str)), "")
    return _clip(str(value)) or use.name


def _outcome(denied: bool, answered: bool, is_error: bool) -> ToolOutcome:
    if denied:
        return "denied"
    if not answered:
        return "running"
    return "error" if is_error else "ok"


def _tolerant(lines: Iterable[str]) -> Iterator[stream.Event]:
    try:
        yield from stream.events(lines)
    except stream.CorruptStream:
        return


def read_stream(events_path: Path) -> StreamView | None:
    """Fold a launch's stream for the console, or `None` when the file is absent."""
    try:
        text = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None  # absent, or moved by a review's collect between ticks
    activity: str | None = None
    context_tokens: int | None = None
    context_at: float | None = None
    issued: dict[str, tuple[stream.ToolUse, float | None]] = {}
    results: dict[str, tuple[bool, float | None]] = {}
    denied: set[str] = set()
    for event in _tolerant(stream.lines(text)):
        match event:
            case stream.Message():
                if event.model != stream.SYNTHETIC:
                    # The CLI's own synthetic error line never reached the API.
                    context_tokens = event.usage.context_tokens
                    context_at = _epoch(event.timestamp)
                for use in event.tool_uses:
                    issued.setdefault(use.id, (use, _epoch(event.timestamp)))
                if event.tool_uses:
                    activity = _summary(event.tool_uses[-1])
                elif event.text.strip():
                    activity = _clip(event.text)
            case stream.ToolResult():
                results[event.tool_use_id] = (event.is_error, _epoch(event.timestamp))
            case stream.Denied():
                denied.add(event.tool_use_id)
                activity = f"denied {event.tool}"
            case stream.Retry():
                activity = f"retrying the API (attempt {event.attempt}): {event.error}"
    calls = []
    for index, (use_id, (use, start)) in enumerate(issued.items()):
        is_error, end = results.get(use_id, (False, None))
        duration = end - start if start is not None and end is not None else None
        calls.append(
            ToolCall(
                index=index,
                tool=use.name,
                summary=_summary(use),
                outcome=_outcome(use_id in denied, use_id in results, is_error),
                duration_s=duration if duration is None or duration >= 0 else None,
            )
        )
    return StreamView(activity, context_tokens, context_at, tuple(calls))


def context_fraction(tokens: int | None, model: str, routing: Routing) -> tuple[float | None, str]:
    """`tokens` over the model's `context_window` in `models.toml`, or why there is none."""
    if not tokens:
        return None, "the agent has not answered yet"
    facts = routing.models.get(model)
    if facts is None:
        return None, f"no context window on file for {model}"
    return tokens / facts.context_window, "last message"


def context_percentage(
    launch: StreamView | str, state: State, model: str, routing: Routing
) -> tuple[float | None, str | None]:
    """The run's context percentage, or `None` and the reason it is hidden.

    `launch` is the current launch's stream, or the reason the run has none to read.
    §18.5: shown from the measurement or hidden with the reason, never estimated.
    """
    if state not in _AGENT_STATES:
        return None, f"no agent running in {state.value}"
    if isinstance(launch, str):
        return None, launch
    fraction, reason = context_fraction(launch.context_tokens, model, routing)
    return fraction, None if fraction is not None else reason
