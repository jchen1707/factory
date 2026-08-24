"""The console's read-only view of a run's `events.jsonl` — §18.5.

P0-7 (`docs/discovery/codex-events.md`) settled the shape: the stream carries per-turn
`usage` (`input_tokens` / `cached_input_tokens` / `cache_write_input_tokens` /
`output_tokens` / `reasoning_output_tokens`) and `item.completed` activity, but **no**
`model_context_window`, `context_window`, `tokens_used` or `percent` — those are TUI-only.
So the console's context percentage is `latest_turn.input_tokens /
routing.ModelFacts.usable_context`, with the denominator from the model cache via
`routing`, and it is *hidden with a reason* when the numerator or denominator is missing
rather than estimated. The plan is explicit (§18.5): never show a number it cannot defend.

This is a display parser, not the state machine's. `agent.codex.parse_events` advances on
evidence and raises on a truncated line; this one only displays, so a half-flushed trailing
line (the normal case for a file a live process is writing) is skipped, not fatal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.machine import State
from factory.routing import Routing

__all__ = [
    "ToolCallView",
    "TurnView",
    "context_percentage",
    "lane_of",
    "read_tool_calls",
    "read_turn_view",
]


#: The agent role each live state runs under — the model that produced the events the
#: console reads. Non-agent states have no model: `verifying` runs a node gate report,
#: `pr_ready`/`deliver` is a host-side push. Their context percentage is hidden with a
#: reason rather than shown against the wrong denominator.
_AGENT_ROLE: dict[State, str] = {
    State.PLANNING: "planner",
    State.IMPLEMENTING: "builder",
    State.REVIEWING: "reviewer",
}


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


@dataclass(frozen=True)
class TurnView:
    """The latest `turn.completed` usage of a run's active attempt, plus the last thing
    the agent did. `has_turn=False` when no turn has completed yet — the caller reads that
    as 'hide the percentage, the agent is mid-turn and a partial turn is not a number to
    defend'."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    activity: str | None
    has_turn: bool


def read_turn_view(events_path: Path) -> TurnView | None:
    """Read an `events.jsonl` for the console's columns.

    `None` when the file is absent — the attempt has not spawned yet, or the run is in a
    state with no events. A `has_turn=False` view when the file exists but no
    `turn.completed` has landed. The last `item.completed` becomes `activity`, the latest
    turn's `usage` becomes the token columns; both are read back rather than remembered,
    because the file is the only thing a fresh console process shares with the run.
    """
    if not events_path.exists():
        return None
    saw_turn = False
    input_tokens = 0
    cached = 0
    output = 0
    reasoning = 0
    activity: str | None = None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # a half-flushed trailing line is normal for a file being written
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn.completed":
            raw = event.get("usage")
            if isinstance(raw, dict):
                input_tokens = int(raw.get("input_tokens", 0) or 0)
                cached = int(raw.get("cached_input_tokens", 0) or 0)
                output = int(raw.get("output_tokens", 0) or 0)
                reasoning = int(raw.get("reasoning_output_tokens", 0) or 0)
            saw_turn = True
        elif kind == "item.completed":
            activity = _activity_from(event.get("item"))
    return TurnView(input_tokens, cached, output, reasoning, activity, saw_turn)


@dataclass(frozen=True)
class ToolCallView:
    """One row in the run-timeline tool-call drill-down — the `events.jsonl` stream folded
    to one entry per `item.completed`, the SSSF visualizer's per-phase call list.

    **No `duration` field in Phase 1.** The event stream carries no timestamp (P0-7,
    `docs/discovery/codex-events.md`): each line has only `type` and `item`/`usage`, never
    `at`. A duration column would be a number the console cannot defend, and the console's
    own rule is that it never shows one (events.py: "never show a number it cannot
    defend"). Phase 2's opt-in `events.timings.jsonl` sidecar is what would defend it; until
    then the column stays absent — not `Optional`, absent.
    """

    index: int
    kind: str  # the item's `type` — command_execution | file_change | agent_message | error
    summary: str
    exit_code: int | None  # only `command_execution` carries one


def read_tool_calls(events_path: Path) -> list[ToolCallView]:
    """Fold an `events.jsonl` into one `ToolCallView` per `item.completed`, in file order.

    A sibling to `read_turn_view`: the same half-flushed-trailing-line skip (a file a live
    agent is writing is the normal case), the same "display only, never raise" stance, and
    the same `_activity_from` summary. Only `item.completed` is folded — `item.started` /
    `item.updated` are the stream's noise, and one row per real call is the SSSF shape.
    Returns `[]` for a missing file (the attempt has not spawned, or the state has no agent).
    """
    if not events_path.exists():
        return []
    rows: list[ToolCallView] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # half-flushed trailing line — see read_turn_view for the reasoning
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("item_type") or item.get("type") or "")
        summary = _activity_from(item) or item_type or "item"
        exit_code: int | None = None
        if item_type == "command_execution":
            raw = item.get("exit_code")
            if isinstance(raw, bool):  # JSON true/false are ints in Python; refuse them
                exit_code = None
            elif isinstance(raw, int):
                exit_code = raw
            elif isinstance(raw, float) and raw.is_integer():
                exit_code = int(raw)
        rows.append(
            ToolCallView(index=len(rows), kind=item_type, summary=summary, exit_code=exit_code)
        )
    return rows


def context_percentage(
    view: TurnView | None, state: State, routing: Routing
) -> tuple[float | None, str | None]:
    """The context-used percentage for a run, or `None` and the reason it is hidden.

    §18.5: the percentage is either shown from the P0-7-confirmed numerator over the
    cache denominator, or hidden with the reason stated. It is never estimated, so every
    branch that returns `None` names exactly what is missing — no model for the state, no
    completed turn yet, or no context window on file for the routed model.
    """
    role = _AGENT_ROLE.get(state)
    if role is None:
        return None, f"no agent running in {state.value}"
    if view is None:
        return None, "no event stream for this attempt yet"
    if not view.has_turn:
        return None, "the agent has not completed a turn yet"
    model = routing.role(role).model
    facts = routing.models.get(model)
    if facts is None:
        return None, f"no context window on file for {model!r}"
    denominator = facts.usable_context
    if denominator <= 0:
        return None, f"context window for {model!r} is {denominator}"
    return view.input_tokens / denominator, None


def _activity_from(item: Any) -> str | None:
    """One short line describing the last `item.completed` — the 'current activity' column.

    The event shapes are P0-7's: `agent_message` (`text`), `command_execution`
    (`command`, `aggregated_output`, `exit_code`), `file_change` (`changes: [{path,
    kind}]`), `error` (`message`). Truncated to a column-friendly width; the full text is
    in the run-detail tail, not the board."""
    if not isinstance(item, dict):
        return None
    item_type = item.get("item_type") or item.get("type")
    if item_type == "agent_message":
        text = str(item.get("text", "")).strip().replace("\n", " ")
        return (text[:120] + "…") if len(text) > 120 else (text or "agent message")
    if item_type == "command_execution":
        cmd = str(item.get("command", "")).strip().replace("\n", " ")
        return (cmd[:120] + "…") if len(cmd) > 120 else (cmd or "command")
    if item_type == "file_change":
        changes = item.get("changes")
        n = len(changes) if isinstance(changes, list) else 0
        return f"changed {n} file{'s' if n != 1 else ''}"
    if item_type == "error":
        return f"error: {str(item.get('message', ''))[:100]}"
    return str(item_type) if item_type else None
